"""Actual authenticated loopback server, SSE and ApplicationServices; no simulated API."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import aiohttp
import pytest

from forge.application.models import validate
from forge.engine.http_adapter import HttpAdapter
from forge.engine.methods import EngineMethods
from test_application import setup

ROOT=Path(__file__).resolve().parents[3]


async def opened(tmp_path):
    service,store,_,clients,params,turn=setup(tmp_path)
    server=HttpAdapter(EngineMethods(service,profile='test'))
    origin=await server.start()
    client=aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    credential=server.credential
    response=await client.post(origin+'/api/v1/login',data={'credential':credential},headers={'Origin':origin},allow_redirects=False)
    assert response.status==303 and server.credential is None
    cookie=response.headers['Set-Cookie']
    assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
    response=await client.get(origin+'/api/v1/session')
    metadata=await response.json()
    return server,client,store,clients,params,turn,metadata,credential


def headers(server,metadata,*,nonce=None):
    return {'Origin':server.origin,'X-Forge-CSRF':metadata['csrf'],'X-Forge-Nonce':nonce or str(uuid4()),
        'X-Forge-Time':str(int(time.time())),'Content-Type':'application/json'}


async def rpc(server,client,metadata,method,params,*,nonce=None):
    response=await client.post(server.origin+'/api/v1/rpc',json={'jsonrpc':'2.0','id':'http-test','method':method,'params':params},
        headers=headers(server,metadata,nonce=nonce))
    assert response.status==200
    value=await response.json();validate('rpc-response',value)
    return value


def test_actual_http_turn_sse_reconnect_ack_artifact_and_no_false_grades(tmp_path):
    async def run():
        server,client,store,clients,params,turn,metadata,_=await opened(tmp_path)
        stream=None
        try:
            subscribed=(await rpc(server,client,metadata,'events.subscribe',{'scope':{'kind':'session','id':turn['session_id']}}))['result']
            stream=await client.get(server.origin+'/api/v1/events',params={'subscription_id':subscribed['subscription_id']})
            assert stream.status==200 and stream.headers['Cache-Control']=='no-store'
            accepted=(await rpc(server,client,metadata,'session.start_turn',turn))['result']
            line=await asyncio.wait_for(stream.content.readline(),10)
            assert line.startswith(b'data: ')
            batch=json.loads(line[6:]);validate('event-notification',batch)
            assert batch['params']['events'][0]['turn_id']==accepted['turn_id']
            stream.close();await asyncio.sleep(0.1)
            stream=await client.get(server.origin+'/api/v1/events',params={'subscription_id':subscribed['subscription_id']})
            repeated=json.loads((await asyncio.wait_for(stream.content.readline(),10))[6:])
            assert repeated==batch  # Unacknowledged delivery is replayed on the real socket.
            ack=await rpc(server,client,metadata,'events.ack',{'subscription_id':subscribed['subscription_id'],'cursor':batch['params']['cursor']})
            assert ack['result']['acknowledged']
            async with asyncio.timeout(15):
                while True:
                    result=(await rpc(server,client,metadata,'session.snapshot',{'session_id':turn['session_id']}))['result']
                    if result['turns'][0]['state']=='finished':break
                    await asyncio.sleep(0.02)
            assert result['turns'][0]['outcome']=='completed' and len(clients[0].calls)==2
            assert store.connection.execute('SELECT COUNT(*) FROM grades').fetchone()[0]==0
            artifact=store.publish_artifact(b'controlled bytes',origin='trusted_engine',classification='metadata',profile_id=server.methods.service.profile_id)
            response=await client.get(server.origin+'/api/v1/artifacts/'+artifact['id'],params={'offset':0,'length':8})
            assert response.status==200
            chunk=await response.json();validate('artifact.read_chunk.result',chunk)
            import base64
            assert base64.b64decode(chunk['data_base64'])==b'controll'
            denied=await client.get(server.origin+'/api/v1/artifacts/../../project/value.txt')
            assert denied.status in (400,404)
        finally:
            if stream:stream.close()
            await client.close();await server.close();store.close()
    asyncio.run(run())


def test_actual_browser_login_csp_and_shared_ui_execute_the_scripted_task(tmp_path):
    service,store,_,clients,_,_=setup(tmp_path)
    async def run():
        server=HttpAdapter(EngineMethods(service,profile='test'),asset_root=ROOT/'apps/desktop',asset_manifest=ROOT/'apps/desktop/ui-assets.json')
        origin=await server.start()
        try:
            executable=ROOT/'node_modules/electron/dist'/('electron.exe' if sys.platform=='win32' else 'electron')
            child=await asyncio.create_subprocess_exec(str(executable),str(ROOT/'tests/implementation/node/http-browser-case.cjs'),
                cwd=tmp_path,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            payload=json.dumps({'origin':origin,'credential':server.credential,'directory':str(tmp_path)})
            try:stdout,stderr=await asyncio.wait_for(child.communicate(payload.encode()),65)
            finally:
                if child.returncode is None:child.kill();await child.wait()
            report_path=tmp_path/'http-browser-report.json'
            assert report_path.is_file(), (f'Electron browser produced no report (exit={child.returncode}): '
                +stderr.decode('utf-8',errors='replace')[-8000:])
            report=json.loads(report_path.read_text())
            assert child.returncode==0 and report['status']=='pass',json.dumps(report)+stderr.decode()
            assert report['rendered_result'] and report['csp_eval_violations']==0
            assert len(clients[0].calls)==2 and (tmp_path/'http-browser.png').is_file()
        finally:await server.close();store.close()
    asyncio.run(run())


@pytest.mark.parametrize('fault',['host','origin','null_origin','csrf','unicode_csrf','nonce','duplicate_nonce','time','duplicate_time','query','compressed','unowned_subscription'])
def test_actual_http_boundary_rejects_forged_requests_before_model_or_tools(tmp_path,fault):
    async def run():
        server,client,store,clients,_,turn,metadata,_=await opened(tmp_path)
        try:
            values=headers(server,metadata);url=server.origin+'/api/v1/rpc'
            if fault=='host':values['Host']='rebind.invalid'
            if fault=='origin':values['Origin']='https://attacker.invalid'
            if fault=='null_origin':values['Origin']='null'
            if fault=='csrf':values['X-Forge-CSRF']='forged'
            if fault=='unicode_csrf':values['X-Forge-CSRF']='é'
            if fault=='nonce':values.pop('X-Forge-Nonce')
            if fault=='time':values['X-Forge-Time']='0000000000'
            if fault=='query':url+='?credential=should-never-be-here'
            if fault=='compressed':values['Content-Encoding']='gzip'
            if fault in ('duplicate_nonce','duplicate_time'):
                values=list(values.items())+[(('X-Forge-Nonce' if fault=='duplicate_nonce' else 'X-Forge-Time'),'forged')]
            if fault=='unowned_subscription':
                response=await client.get(server.origin+'/api/v1/events?subscription_id=sub-'+str(uuid4()))
            else:
                response=await client.post(url,json={'jsonrpc':'2.0','id':'bad','method':'session.start_turn','params':turn},headers=values)
            assert response.status in (400,403,415)
            assert clients==[] and store.connection.execute('SELECT COUNT(*) FROM turns').fetchone()[0]==0
        finally:await client.close();await server.close();store.close()
    asyncio.run(run())


def test_one_time_login_nonce_replay_and_privileged_methods_cannot_lower_authority(tmp_path):
    async def run():
        server,client,store,clients,_,_,metadata,credential=await opened(tmp_path)
        try:
            repeated=await client.post(server.origin+'/api/v1/login',data={'credential':credential},headers={'Origin':server.origin},allow_redirects=False)
            assert repeated.status==401
            nonce=str(uuid4())
            assert 'result' in await rpc(server,client,metadata,'system.health',{},nonce=nonce)
            replay=await client.post(server.origin+'/api/v1/rpc',json={'jsonrpc':'2.0','id':'replayed','method':'system.health','params':{}},
                headers=headers(server,metadata,nonce=nonce))
            assert replay.status==409
            for method in ('credentials.inject','workspace.authorize','approval.decide','connection.set','system.shutdown'):
                denied=await rpc(server,client,metadata,method,{'role':'main'})
                assert denied['error']['data']['kind']=='DESKTOP_OR_CLI_APPROVAL_REQUIRED'
            assert clients==[] and not server.methods.stopping
            async with aiohttp.ClientSession() as outsider:
                assert (await outsider.get(server.origin+'/api/v1/session')).status==401
        finally:await client.close();await server.close();store.close()
    asyncio.run(run())


def test_existing_cli_import_does_not_load_optional_http_stack_or_listen():
    result=subprocess.run([sys.executable,'-c',
        'import sys;import forge.cli;print("aiohttp" in sys.modules)'],cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert result.returncode==0 and result.stdout.strip()=='False'


@pytest.mark.parametrize('payload',['duplicate','depth','oversize'])
def test_http_wire_limits_reject_payload_without_business_side_effects(tmp_path,payload):
    async def run():
        server,client,store,clients,_,_,metadata,_=await opened(tmp_path)
        try:
            if payload=='duplicate':body='{"jsonrpc":"2.0","id":"x","id":"y","method":"system.health","params":{}}'
            elif payload=='depth':body='['*65+'0'+']'*65
            else:body=' '*1048577
            response=await client.post(server.origin+'/api/v1/rpc',data=body,headers=headers(server,metadata))
            if payload=='oversize':assert response.status==413
            else:
                assert response.status==200 and 'error' in await response.json()
            assert clients==[] and store.connection.execute('SELECT COUNT(*) FROM turns').fetchone()[0]==0
        finally:await client.close();await server.close();store.close()
    asyncio.run(run())


@pytest.mark.parametrize('exercise_expiry',[False,True],ids=['normal','expired-ack'])
def test_actual_desktop_and_http_transports_share_task_dtos_reducer_and_durable_reconciliation(tmp_path,exercise_expiry):
    from test_rpc import seed
    desktop=tmp_path/'desktop';desktop.mkdir()
    http_root=tmp_path/'http';http_root.mkdir()
    fixture,_,desktop_turn=seed(desktop)
    service,store,_,clients,params,http_turn=setup(http_root)
    async def run():
        server=HttpAdapter(EngineMethods(service,profile='test'))
        if exercise_expiry:server.methods.events.cursor_ttl_seconds=4
        origin=await server.start()
        try:
            child=await asyncio.create_subprocess_exec('node',str(ROOT/'tests/implementation/node/http-transport-case.mjs'),
                cwd=tmp_path,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            payload=json.dumps({'directory':str(tmp_path),'desktop':str(desktop),'fixture':str(fixture),'origin':origin,
                'credential':server.credential,'desktopTurn':desktop_turn,'httpTurn':http_turn,'workspaceId':params['workspace_id'],
                'exerciseExpiry':exercise_expiry})
            try:stdout,stderr=await asyncio.wait_for(child.communicate(payload.encode()),45)
            finally:
                if child.returncode is None:child.kill();await child.wait()
            assert child.returncode==0,stdout.decode()+stderr.decode()
            report=json.loads(stdout)
            assert report['same_dtos'] and report['same_reducer'] and report['lost_mutation_reply_reconciled']
            active=[client for client in clients if client.calls]
            assert len(active)==(2 if exercise_expiry else 1) and all(len(client.calls)==2 for client in active)
            assert store.connection.execute('SELECT COUNT(*) FROM turns').fetchone()[0]==(2 if exercise_expiry else 1)
            if exercise_expiry:assert report['expired_ack_recovered'] and len(server.methods.events.subscriptions)==0
        finally:await server.close();store.close()
    asyncio.run(run())
