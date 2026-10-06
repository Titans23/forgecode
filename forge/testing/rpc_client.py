"""Small single-reader private RPC client for offline integration demos."""
import asyncio
import json

from forge.application.models import strict_loads, validate


class RpcClient:
    def __init__(self, process):
        self.process = process
        self.sequence = 0
        self.events = {}

    async def send(self, method, params, *, notification=False):
        self.sequence += 1
        request = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if not notification:
            request['id'] = str(self.sequence)
        self.process.stdin.write((json.dumps(request) + '\n').encode('utf-8'))
        await self.process.stdin.drain()
        return request.get('id')

    async def read(self):
        raw = await asyncio.wait_for(self.process.stdout.readline(), 30)
        if not raw:
            return None
        value = strict_loads(raw)
        if value.get('method') == 'events.batch':
            validate('event-notification', value)
            params = value['params']
            for event in params['events']:
                previous = self.events.get(event['event_id'])
                if previous is not None and previous != event:
                    raise RuntimeError('Conflicting event identity')
                self.events[event['event_id']] = event
            if not self.process.stdin.is_closing():
                await self.send('events.ack', {'subscription_id': params['subscription_id'], 'cursor': params['cursor']}, notification=True)
        else:
            validate('rpc-response', value)
        return value

    async def call(self, method, params):
        request_id = await self.send(method, params)
        async with asyncio.timeout(30):
            while (value := await self.read()) is not None:
                if value.get('id') == request_id:
                    if 'error' in value:
                        raise RuntimeError(f"RPC {method} failed: {value['error']}")
                    return value['result']
        raise RuntimeError('Engine ended before RPC response')

    async def drain(self):
        while await self.read() is not None:
            pass
        return await asyncio.wait_for(self.process.wait(), 30)
