"""Opt-in loopback HTTP/SSE transport for the existing Engine services."""
import argparse
import asyncio
from hashlib import sha256
import hmac
import json
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs

from aiohttp import web

from forge.application.models import ContractError, MAX_FRAME_BYTES, METHODS, strict_loads, validate
from forge.engine.methods import manifest_hash
from forge.engine.persistence import encoded
from forge.engine.rpc import RpcServer, rpc_error


LOGIN_PAGE=b'<!doctype html><html lang="zh"><meta charset="utf-8"><title>ForgeCode login</title><h1>ForgeCode</h1><p>Enter the one-time credential printed by the trusted CLI.</p><form method="post" action="/api/v1/login"><input name="credential" type="password" autocomplete="off" required maxlength="128"><button>Connect</button></form></html>'
CSP="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
COOKIE='forge_web_session'
SESSION=web.RequestKey('forge.session',dict)


class HttpAdapter:
    def __init__(self, methods, *, asset_root=None, asset_manifest=None):
        self.methods=methods
        # Like named Desktop IPC, the trusted host forwards only explicitly public business DTOs.
        # Stdio audiences stay unchanged; HTTP never exposes other Main-only methods.
        self.allowed={name for name,catalog in METHODS.items() if catalog['audience']=='renderer' and name in methods.handlers}
        self.allowed.update(('session.create_default','session.submit'))
        self.rpc=RpcServer(methods,principal='main')
        self.credential=secrets.token_urlsafe(32)
        self.login_expires=time.monotonic()+300
        self.sessions={}
        self.pending={}
        self.streaming=set()
        self.asset_root=Path(asset_root).resolve(strict=True) if asset_root else None
        self.assets=json.loads(Path(asset_manifest).read_text(encoding='utf-8')) if asset_manifest else {}
        self.origin=None
        self.scheduling=None
        self.runner=None
        self.app=web.Application(client_max_size=MAX_FRAME_BYTES,middlewares=[self.boundary])
        self.app.router.add_post('/api/v1/login',self.login)
        self.app.router.add_get('/api/v1/session',self.session_metadata)
        self.app.router.add_post('/api/v1/rpc',self.dispatch)
        self.app.router.add_get('/api/v1/events',self.events)
        self.app.router.add_get('/api/v1/artifacts/{id}',self.artifact)
        self.app.router.add_get('/{asset:.*}',self.static)

    @web.middleware
    async def boundary(self,request,handler):
        try:
            if request.remote!='127.0.0.1' or request.headers.getall('Host',[])!=[self.origin.removeprefix('http://')]:
                raise web.HTTPForbidden(text='Loopback Host required')
            origins=request.headers.getall('Origin',[])
            if (request.method=='POST' and origins!=[self.origin]) or (origins and origins!=[self.origin]):
                raise web.HTTPForbidden(text='Exact same Origin required')
            if request.headers.get('Sec-Fetch-Site') not in (None,'same-origin','none'):
                raise web.HTTPForbidden(text='Cross-site request denied')
            if request.headers.get('Content-Encoding') not in (None,'identity'):
                raise web.HTTPUnsupportedMediaType(text='Encoded request bodies are unsupported')
            if request.path.startswith('/api/') and request.path!='/api/v1/login':
                if request.path in ('/api/v1/rpc','/api/v1/session') and request.query:
                    raise web.HTTPBadRequest(text='Credentials and RPC parameters do not belong in URLs')
                session=self.sessions.get(request.cookies.get(COOKIE,''))
                if session is None or time.monotonic()>=session['expires']:
                    raise web.HTTPUnauthorized(text='Login required')
                request[SESSION]=session
                if request.method=='POST':
                    csrf=request.headers.getall('X-Forge-CSRF',[])
                    if len(csrf)!=1 or not hmac.compare_digest(csrf[0].encode(),session['csrf'].encode()):
                        raise web.HTTPForbidden(text='CSRF validation failed')
                    nonces=request.headers.getall('X-Forge-Nonce',[])
                    stamps=request.headers.getall('X-Forge-Time',[])
                    if len(nonces)!=1 or len(stamps)!=1:raise web.HTTPForbidden(text='Single request nonce/time required')
                    nonce=nonces[0]
                    stamp=stamps[0]
                    now=int(time.time())
                    if len(nonce)!=36 or len(stamp)!=10 or not stamp.isdigit() or abs(now-int(stamp))>60:
                        raise web.HTTPForbidden(text='Request nonce/time required')
                    session['nonces']={key:value for key,value in session['nonces'].items() if value>=now}
                    if nonce in session['nonces']:raise web.HTTPConflict(text='Request nonce was already consumed')
                    if len(session['nonces'])>=2048:raise web.HTTPTooManyRequests(text='Replay window quota reached')
                    session['nonces'][nonce]=max(now,int(stamp))+60
            response=await handler(request)
        except web.HTTPException as error:
            response=web.Response(status=error.status,body=error.body,headers=error.headers)
        response.headers.update({'Cache-Control':'no-store','Content-Security-Policy':CSP,
            'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin'})
        return response

    async def login(self,request):
        if request.query:raise web.HTTPBadRequest(text='Login credentials are accepted only in the form body')
        raw=await request.read()
        if len(raw)>1024:raise web.HTTPRequestEntityTooLarge(max_size=1024,actual_size=len(raw))
        if request.content_type!='application/x-www-form-urlencoded':raise web.HTTPUnsupportedMediaType()
        try:fields=parse_qs(raw.decode('utf-8'),strict_parsing=True,max_num_fields=1)
        except (ValueError,UnicodeError):raise web.HTTPBadRequest() from None
        value=fields.get('credential',[''])[0]
        if self.credential is None or time.monotonic()>=self.login_expires or not hmac.compare_digest(value.encode(),self.credential.encode()):
            raise web.HTTPUnauthorized(text='One-time credential expired or consumed')
        self.credential=None
        cookie=secrets.token_urlsafe(32)
        self.sessions[cookie]={'csrf':secrets.token_urlsafe(32),'expires':time.monotonic()+3600,'nonces':{},'subscriptions':set()}
        response=web.Response(status=303,headers={'Location':'/'})
        response.set_cookie(COOKIE,cookie,httponly=True,samesite='Strict',max_age=3600,path='/')
        return response

    async def session_metadata(self,request):
        health=self.methods.health({})
        validate('system.health.result',health)
        return web.json_response({'csrf':request[SESSION]['csrf'],'health':health,'mode':self.methods.service.mode,
            'profile':self.methods.profile,'credential_storage':{'mode':'trusted-cli-memory','backend':'cli-process',
                'available':False,'state':'desktop_or_cli_required','stored_in_memory':len(getattr(self.methods.service.credentials,'values',{}))}})

    async def dispatch(self,request):
        if request.content_type!='application/json':raise web.HTTPUnsupportedMediaType()
        try:
            member=strict_loads(await request.read())
            validate('rpc-request',member)
            method=member['method']
            catalog=METHODS.get(method)
            if method in ('system.initialize','system.shutdown') or (catalog and method not in self.allowed):
                response=rpc_error(member.get('id'),-32010,'This operation requires trusted Desktop or CLI approval','DESKTOP_OR_CLI_APPROVAL_REQUIRED')
            elif method in ('events.ack','events.unsubscribe') and member['params'].get('subscription_id') not in request[SESSION]['subscriptions']:
                response=rpc_error(member.get('id'),-32010,'Subscription belongs to another browser session','UNAUTHORIZED')
            else:
                response=await self.rpc.handle_member(member)
                if response and 'result' in response:
                    if method=='events.subscribe':
                        request[SESSION]['subscriptions'].add(response['result']['subscription_id'])
                    if method in ('events.ack','events.unsubscribe'):
                        key=member['params']['subscription_id'];self.pending.pop(key,None)
                        if method=='events.unsubscribe':request[SESSION]['subscriptions'].discard(key)
            if response is None:raise ContractError('HTTP RPC requires a response ID',code=-32600)
            if len(encoded(response).encode())>MAX_FRAME_BYTES:
                response=rpc_error(member.get('id'),-32010,'Result exceeds frame limit; use paging','ARTIFACT_LIMIT')
        except ContractError as error:
            response=rpc_error(None,error.code,str(error),error.kind)
        return web.json_response(response,dumps=encoded)

    async def events(self,request):
        if set(request.query)!={'subscription_id'}:raise web.HTTPBadRequest(text='Only a scoped subscription identity is accepted')
        key=request.query['subscription_id']
        if key not in request[SESSION]['subscriptions']:raise web.HTTPForbidden(text='Unowned subscription')
        if key in self.streaming:raise web.HTTPConflict(text='Subscription already has a stream')
        response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-store',
            'X-Content-Type-Options':'nosniff','Content-Security-Policy':CSP,'Referrer-Policy':'same-origin'})
        await response.prepare(request)
        self.streaming.add(key)
        emitted=None;heartbeat=time.monotonic()
        try:
            while not self.methods.stopping and time.monotonic()<request[SESSION]['expires'] and key in request[SESSION]['subscriptions']:
                batch=self.pending.get(key)
                if batch is None:
                    batch=self.methods.events.next_batch(key)
                    if batch:self.pending[key]=batch
                if batch and batch['params']['cursor']!=emitted:
                    await asyncio.wait_for(response.write(('data: '+encoded(batch)+'\n\n').encode()),5)
                    emitted=batch['params']['cursor']
                elif time.monotonic()-heartbeat>=15:
                    await asyncio.wait_for(response.write(b': heartbeat\n\n'),5);heartbeat=time.monotonic()
                await asyncio.sleep(0.05)
        except (ConnectionError,TimeoutError,ContractError):
            pass
        finally:self.streaming.discard(key)
        return response

    async def artifact(self,request):
        try:
            if set(request.query)-{'offset','length'}:raise ValueError()
            params={'artifact_id':request.match_info['id'],'offset':int(request.query.get('offset','0')),
                    'length':int(request.query.get('length','262144'))}
            validate('artifact.read_chunk.request',params)
            result=self.methods.artifacts.read_chunk(params)
            validate('artifact.read_chunk.result',result)
            return web.json_response(result,dumps=encoded)
        except (ValueError,ContractError):
            raise web.HTTPNotFound(text='Controlled artifact unavailable') from None

    async def static(self,request):
        if request.query:raise web.HTTPBadRequest(text='Static resources do not accept query credentials')
        cookie=self.sessions.get(request.cookies.get(COOKIE,''))
        if request.path=='/' and (cookie is None or time.monotonic()>=cookie['expires']):
            return web.Response(body=LOGIN_PAGE,content_type='text/html')
        if cookie is None or time.monotonic()>=cookie['expires']:raise web.HTTPUnauthorized()
        key='/index.html' if request.path=='/' else request.path
        item=self.assets.get(key)
        if not item or self.asset_root is None:raise web.HTTPNotFound(text='Build the fixed ForgeCode UI assets first')
        path=self.asset_root/item['path']
        if path.resolve(strict=True)!=path or not path.is_relative_to(self.asset_root):raise web.HTTPNotFound()
        data=path.read_bytes()
        if len(data)>10485760 or sha256(data).hexdigest()!=item['sha256']:raise web.HTTPNotFound(text='UI asset integrity mismatch')
        mime={'html':'text/html','js':'application/javascript','css':'text/css'}.get(path.suffix[1:])
        if mime is None:raise web.HTTPNotFound()
        return web.Response(body=data,content_type=mime)

    async def start(self):
        params={'protocol':{'major':1,'minor':0},'expected_manifest_hash':manifest_hash(),'profile':self.methods.profile,'client_build':'forge-web-development'}
        validate('system.initialize.request',params)
        self.methods.initialize(params)
        self.runner=web.AppRunner(self.app,access_log=None,handler_cancellation=True)
        await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0)
        await site.start()
        port=self.runner.addresses[0][1]
        self.origin=f'http://127.0.0.1:{port}'
        self.scheduling=asyncio.create_task(self.rpc.scheduler.run())
        self.methods.service.exporter.start()
        self.rpc.work_ready.set()
        return self.origin

    async def close(self):
        self.methods.begin_shutdown('cancel','Trusted CLI Web service closed')
        self.rpc.work_ready.set()
        if self.scheduling:
            await self.scheduling
        if self.runner:await self.runner.cleanup()
        await self.methods.service.exporter.aclose()


def main(argv=None):
    parser=argparse.ArgumentParser(description='Explicit opt-in ForgeCode loopback Web service. No remote bind.')
    parser.add_argument('--data-dir',required=True,type=Path)
    parser.add_argument('--execution-mode',choices=('strict','local-trusted'),default='strict')
    parser.add_argument('--profile',choices=('cli','test'),default='cli')
    parser.add_argument('--profile-id')
    parser.add_argument('--scripted-fixture',type=Path,help='Offline test profile only')
    parser.add_argument('--project',type=Path,help='Register a project from this trusted CLI before opening Web')
    parser.add_argument('--authorize-project',action='store_true',help='Explicitly authorize this CLI-selected project')
    parser.add_argument('--from-cli-config',action='store_true',help='Load provider configuration into trusted process memory')
    args=parser.parse_args(argv)
    if args.scripted_fixture and args.profile!='test':parser.error('scripted fixture requires test profile')
    if args.authorize_project and not args.project:parser.error('authorization requires a CLI-selected project')
    from forge.application.harness_adapter import LocalTrustedBackend
    from forge.application.services import ApplicationServices
    from forge.engine.methods import EngineMethods
    from forge.engine.persistence import Store
    from forge.engine.test_profile import MemoryCredentials,load_scripted_profile
    fixture=load_scripted_profile(args.scripted_fixture) if args.scripted_fixture else None
    credentials=fixture.credentials if fixture else MemoryCredentials()
    with Store(args.data_dir) as store:
        service=ApplicationServices(store,profile_id=args.profile_id or args.profile+'-profile',credentials=credentials,
            mode=args.execution_mode,backend=LocalTrustedBackend() if args.execution_mode=='local-trusted' else None,
            model_client_factory=fixture.model_client_factory if fixture else None,
            approval_handler=fixture.approval_handler if fixture else None,task_relation='new' if fixture else None)
        if args.project:
            workspace=service.open_workspace(args.project)
            if args.authorize_project and workspace['trust']!='execution_allowed':
                service.authorize_workspace(workspace['id'],expected_revision=workspace['revision'],allow=True)
        if args.from_cli_config:
            from forge.config import ForgeConfig
            config=ForgeConfig.from_env()
            identifier=service.put_connection(config);credentials.values[identifier]=config.api_key
        if args.profile!='test':
            from benchmark.adapters.harbor import HarborExecutor
            service.evaluation_executor=HarborExecutor(service)
        root=Path(__file__).resolve().parents[2]/'apps/desktop'
        adapter=HttpAdapter(EngineMethods(service,profile=args.profile),asset_root=root,asset_manifest=root/'ui-assets.json')
        async def run():
            try:
                origin=await adapter.start()
                print('ForgeCode loopback URL: '+origin,flush=True)
                print('One-time login credential: '+adapter.credential,flush=True)
                await asyncio.Event().wait()
            finally:await adapter.close()
        try:asyncio.run(run())
        except KeyboardInterrupt:return 0
    return 0


if __name__=='__main__':
    raise SystemExit(main())
