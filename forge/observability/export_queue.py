"""Opt-in metadata exporter: durable bounded queue, independent async HTTP retries."""
import asyncio
from dataclasses import dataclass
from hashlib import sha256
import ipaddress
import json
import re
from time import time
from urllib.parse import urlsplit

import httpx

from forge.observability.usage_ledger import PriceBook


@dataclass(frozen=True)
class ObservationOptions:
    capture_mode: str = 'metadata'
    prices: PriceBook | None = None
    endpoint: str | None = None
    metadata_export_confirmed: bool = False
    queue_items: int = 256
    queue_bytes: int = 4194304
    request_timeout: float = 2
    max_attempts: int = 5

    def __post_init__(self):
        if self.capture_mode not in ('metadata','controlled_debug') or self.prices is not None and not isinstance(self.prices,PriceBook):
            raise ValueError('Invalid observation configuration')
        if not 1<=self.queue_items<=10000 or not 1024<=self.queue_bytes<=67108864 or not 0<self.request_timeout<=30 or not 1<=self.max_attempts<=10:
            raise ValueError('Observation limits are outside supported bounds')
        if self.endpoint:
            parsed=urlsplit(self.endpoint)
            try: loopback=ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError: loopback=parsed.hostname=='localhost'
            if not self.metadata_export_confirmed or parsed.scheme not in ('http','https') or parsed.scheme=='http' and not loopback or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('OTLP requires explicit metadata consent and HTTPS or a loopback collector')
            if parsed.path!='/v1/traces':
                raise ValueError('OTLP endpoint must name /v1/traces')


SENSITIVE=re.compile(r'authorization|api[_-]?key|access[_-]?token|password|secret',re.I)
BEARER=re.compile(r'(?i)\b(?:authorization\s*[:=]\s*)?Bearer\s+[^\s,;]+')
ASSIGNMENT=re.compile(r'(?i)(\b(?:api[_-]?key|password|access[_-]?token|secret)\s*[:=]\s*)[^\s,;]+')


def redact(value,secrets=()):
    if isinstance(value,str):
        for secret in sorted((s for s in secrets if s),key=len,reverse=True):
            value=value.replace(secret,'[REDACTED]')
        return ASSIGNMENT.sub(r'\1[REDACTED]',BEARER.sub('Bearer [REDACTED]',value))
    if isinstance(value,dict):
        return {key:'[REDACTED]' if SENSITIVE.search(key) else redact(item,secrets) for key,item in value.items()}
    if isinstance(value,(list,tuple)):
        return [redact(item,secrets) for item in value]
    return value


def _failure(store,reason,count=1):
    store.connection.execute('INSERT INTO export_failures(reason,count,last_at) VALUES(?,?,?) ON CONFLICT(reason) '
        'DO UPDATE SET count=count+excluded.count,last_at=excluded.last_at',(reason,count,time()))


def enqueue(store,body):
    options=getattr(store,'observation_options',None)
    if options is None or options.endpoint is None or body['origin'] not in ('trusted_engine','trusted_bridge','grader_adapter'):
        return
    from forge.observability.otel_mapping import export_span
    payload=export_span(store,body)
    if payload is None:
        return
    raw=json.dumps(redact(payload,getattr(store,'observation_secrets',())),ensure_ascii=False,separators=(',',':')).encode()
    if len(raw)>min(options.queue_bytes,262144):
        _failure(store,'span_payload_quota')
        return
    destination=sha256(options.endpoint.encode()).hexdigest()
    rows=store.connection.execute('SELECT event_id,size_bytes FROM export_queue ORDER BY rowid').fetchall()
    total=sum(row['size_bytes'] for row in rows)
    while rows and (len(rows)>=options.queue_items or total+len(raw)>options.queue_bytes):
        row=rows.pop(0)
        total-=row['size_bytes']
        store.connection.execute('DELETE FROM export_queue WHERE event_id=?',(row['event_id'],))
        _failure(store,'queue_capacity')
    store.connection.execute('INSERT INTO export_queue VALUES(?,?,?,?,?,?,?)',
        (body['event_id'],destination,raw.decode(),len(raw),0,0,None))


class ExportQueue:
    def __init__(self,store,options):
        self.store=store
        self.options=options
        self.task=None
        self.destination=sha256(options.endpoint.encode()).hexdigest() if options.endpoint else None

    def start(self):
        if self.options.endpoint and (self.task is None or self.task.done()):
            self.task=asyncio.create_task(self.run())

    async def run(self):
        # No environment proxy, redirects, response command interpretation or task-state writes.
        async with httpx.AsyncClient(timeout=self.options.request_timeout,follow_redirects=False,trust_env=False) as client:
            while True:
                row=self.store.connection.execute('SELECT * FROM export_queue WHERE destination=? AND retry_at<=? ORDER BY rowid LIMIT 1',
                    (self.destination,time())).fetchone()
                if row is None:
                    await asyncio.sleep(.1)
                    continue
                reason=None
                retry=False
                try:
                    async with client.stream('POST',self.options.endpoint,content=row['body_json'].encode(),headers={'Content-Type':'application/json'}) as response:
                        data=bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data)>16384:
                                raise ValueError('response_quota')
                        if response.status_code==200:
                            result=json.loads(data or b'{}')
                            partial=result.get('partialSuccess',{})
                            if int(partial.get('rejectedSpans',0)):
                                reason='collector_partial_rejection'
                        else:
                            reason='http_'+str(response.status_code)
                            retry=response.status_code in (429,502,503,504)
                except httpx.HTTPError:
                    reason='transport_error'
                    retry=True
                except (ValueError,TypeError,AttributeError):
                    reason='invalid_collector_response'
                with self.store.transaction():
                    # Queue eviction during await is harmless. Never resurrect an evicted event.
                    if reason:
                        _failure(self.store,reason)
                    if reason and retry and row['attempts']+1<self.options.max_attempts:
                        self.store.connection.execute('UPDATE export_queue SET attempts=attempts+1,retry_at=?,last_error=? WHERE event_id=?',
                            (time()+min(30,.25*2**row['attempts']),reason,row['event_id']))
                    else:
                        self.store.connection.execute('DELETE FROM export_queue WHERE event_id=?',(row['event_id'],))

    async def aclose(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
            self.task=None

    def status(self):
        row=self.store.connection.execute('SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM export_queue').fetchone()
        return {'enabled':bool(self.options.endpoint),'pending_items':row[0],'pending_bytes':row[1],
            'failures':[dict(row) for row in self.store.connection.execute('SELECT * FROM export_failures ORDER BY reason')]}
