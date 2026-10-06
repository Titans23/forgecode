"""Bounded private JSONL transport. Protocol bytes never use print/Rich."""
import asyncio
from concurrent.futures import CancelledError as FutureCancelled
import json
import os
import queue
import threading
import sys

from forge.application.models import ContractError, ErrorKind, MAX_FRAME_BYTES, METHODS, strict_loads, validate, validate_request
from forge.engine.persistence import encoded, new_id
from forge.engine.scheduler import TurnScheduler


EOF = object()


def rpc_error(request_id, code, message, kind=None):
    value = {'code': code, 'message': message}
    if code == -32010:
        value['data'] = {'kind': str(kind), 'retryable': False, 'correlation_id': new_id('diag')}
    response = {'jsonrpc': '2.0', 'id': request_id, 'error': value}
    validate('rpc-response', response)
    return response


class PipeWriter:
    """One daemon writes bounded bytes. A blocked OS pipe cannot freeze the event loop."""
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.loop = asyncio.get_running_loop()
        self.jobs = queue.Queue(maxsize=1)
        threading.Thread(target=self._run, daemon=True, name='forge-protocol-writer').start()

    def _complete(self, future, error):
        if not future.done():
            future.set_exception(error) if error else future.set_result(None)

    def _run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            data, future = job
            error = None
            try:
                while data:
                    written = os.write(self.descriptor, data)
                    if written <= 0:
                        raise BrokenPipeError('Protocol pipe closed')
                    data = data[written:]
            except OSError as caught:
                error = caught
            try:
                self.loop.call_soon_threadsafe(self._complete, future, error)
            except RuntimeError:
                return

    async def write(self, value):
        raw = encoded(value).encode('utf-8')
        if len(raw) > MAX_FRAME_BYTES:
            raise ContractError('Response exceeds frame limit', kind='ARTIFACT_LIMIT', code=-32010)
        future = self.loop.create_future()
        self.jobs.put_nowait((raw + b'\n', future))
        await asyncio.wait_for(future, 5)

    def close(self):
        try:
            self.jobs.put_nowait(None)
        except queue.Full:
            pass


def start_reader(descriptor, inbound, loop, stopped):
    def publish(frame):
        if stopped.is_set():
            return False
        future = asyncio.run_coroutine_threadsafe(inbound.put(frame), loop)
        try:
            future.result()
            return True
        except (RuntimeError, FutureCancelled):
            return False

    def read():
        buffer = bytearray()
        oversized = False
        try:
            while not stopped.is_set():
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    if buffer or oversized:
                        publish(None if oversized else bytes(buffer))
                    publish(EOF)
                    return
                while chunk:
                    boundary = chunk.find(b'\n')
                    part, chunk = (chunk, b'') if boundary < 0 else (chunk[:boundary], chunk[boundary+1:])
                    if not oversized:
                        if len(buffer) + len(part) > MAX_FRAME_BYTES:
                            buffer.clear()
                            oversized = True
                        else:
                            buffer.extend(part)
                    if boundary >= 0:
                        if not publish(None if oversized else bytes(buffer)):
                            return
                        buffer.clear()
                        oversized = False
        except OSError:
            publish(EOF)

    threading.Thread(target=read, daemon=True, name='forge-protocol-reader').start()


class RpcServer:
    def __init__(self, methods, *, principal='main'):
        self.methods = methods
        self.principal = principal
        self.inbound = asyncio.Queue(maxsize=128)
        self.control = asyncio.Queue(maxsize=64)
        self.data = asyncio.Queue(maxsize=8)
        self.output_ready = asyncio.Event()
        self.work_ready = asyncio.Event()
        self.reader_stopped = threading.Event()
        self.closed = False
        self.scheduler = TurnScheduler(methods, self.work_ready)

    def handle_member(self, member):
        request_id = None
        notification = False
        try:
            try:
                validate('rpc-request', member)
            except ContractError:
                raise ContractError('Invalid JSON-RPC request', code=-32600) from None
            request_id = member.get('id')
            notification = request_id is None
            method = member['method']
            if method == 'system.initialize' and notification:
                return None
            if method == 'system.initialize' and member['params'].get('protocol', {}).get('major') != 1:
                raise ContractError('Protocol major mismatch', kind='INCOMPATIBLE_PROTOCOL', code=-32010)
            validate_request(member, self.principal)
            if method not in self.methods.handlers:
                raise ContractError('Method is not implemented in this build', kind='NOT_FOUND', code=-32601)
            if method != 'system.initialize' and not self.methods.initialized:
                raise ContractError('Initialize the private channel first', kind='INCOMPATIBLE_PROTOCOL', code=-32010)
            if notification and METHODS[method]['mutation']:
                return None
            result = self.methods.handlers[method](member['params'])
            validate(METHODS[method]['result_schema'], result)
            self.work_ready.set()
            return None if notification else {'jsonrpc': '2.0', 'id': request_id, 'result': result}
        except ContractError as error:
            if notification:
                return None
            kind = error.kind if error.kind in ErrorKind else 'INVALID_PARAMS'
            return rpc_error(request_id, error.code, str(error)[:512], kind)
        except Exception as error:
            # Values and provider error bodies are deliberately absent from diagnostics.
            print(json.dumps({'component': 'rpc', 'exception_type': type(error).__name__}), file=sys.stderr)
            return None if notification else rpc_error(request_id, -32603, 'Internal error; inspect diagnostic metadata')

    def handle_frame(self, raw):
        if raw is None:
            return rpc_error(None, -32700, 'Frame exceeds 1 MiB')
        try:
            value = strict_loads(raw)
        except ContractError:
            return rpc_error(None, -32700, 'Invalid UTF-8 JSON frame')
        if isinstance(value, list):
            if not value or len(value) > 32:
                return rpc_error(None, -32600, 'Batch must contain 1 to 32 requests')
            responses = [response for member in value if (response := self.handle_member(member)) is not None]
            if not responses:
                return None
            while len(encoded(responses).encode('utf-8')) > MAX_FRAME_BYTES:
                index = max(range(len(responses)), key=lambda i: len(encoded(responses[i])))
                responses[index] = rpc_error(responses[index]['id'], -32603, 'Batch result exceeds frame limit; query separately')
            return responses
        response = self.handle_member(value)
        if response and len(encoded(response).encode('utf-8')) > MAX_FRAME_BYTES:
            return rpc_error(response['id'], -32010, 'Result exceeds frame limit; use paging', 'ARTIFACT_LIMIT')
        return response

    async def _write(self, writer):
        while not self.closed or not self.control.empty() or not self.data.empty():
            if not self.control.empty():
                value = self.control.get_nowait()
            elif not self.data.empty():
                value = self.data.get_nowait()
            else:
                self.output_ready.clear()
                await self.output_ready.wait()
                continue
            await writer.write(value)

    async def _events(self):
        while not self.closed:
            if not self.data.full():
                for subscription_id in list(self.methods.events.subscriptions):
                    if self.data.full():
                        break
                    try:
                        batch = self.methods.events.next_batch(subscription_id)
                        if batch:
                            self.data.put_nowait(batch)
                            self.output_ready.set()
                    except ContractError as error:
                        print(json.dumps({'component': 'event_stream', 'kind': str(error.kind)}), file=sys.stderr)
                        self.methods.events.subscriptions.pop(subscription_id, None)
            await asyncio.sleep(0.02)

    async def run(self, input_descriptor, output_descriptor):
        writer = PipeWriter(output_descriptor)
        start_reader(input_descriptor, self.inbound, asyncio.get_running_loop(), self.reader_stopped)
        writing = asyncio.create_task(self._write(writer))
        pumping = asyncio.create_task(self._events())
        scheduling = asyncio.create_task(self.scheduler.run())
        try:
            while not self.methods.stopping:
                reading = asyncio.create_task(self.inbound.get())
                done, _ = await asyncio.wait((reading, writing), return_when=asyncio.FIRST_COMPLETED)
                if writing in done:
                    reading.cancel()
                    await asyncio.gather(reading, return_exceptions=True)
                    self.methods.begin_shutdown('cancel', 'Main output pipe lost or blocked')
                    break
                raw = reading.result()
                if raw is EOF:
                    self.methods.begin_shutdown('cancel', 'Main control pipe EOF')
                    self.work_ready.set()
                    break
                response = self.handle_frame(raw)
                if response is not None:
                    try:
                        self.control.put_nowait(response)
                    except asyncio.QueueFull:
                        self.methods.begin_shutdown('cancel', 'Main stopped draining control responses')
                        break
                    self.output_ready.set()
            self.work_ready.set()
            await scheduling
            self.closed = True
            self.output_ready.set()
            try:
                await asyncio.wait_for(writing, 5)
            except (TimeoutError, BrokenPipeError, ConnectionResetError):
                # Work was cancelled/settled independently of stdout draining.
                # A lost consumer cannot turn task output into an exit deadlock.
                pass
            return 0
        finally:
            self.closed = True
            self.reader_stopped.set()
            for task in (pumping, scheduling, writing):
                if not task.done():
                    task.cancel()
            await asyncio.gather(pumping, scheduling, writing, return_exceptions=True)
            writer.close()
