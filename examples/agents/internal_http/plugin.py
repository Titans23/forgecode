"""Explicit endpoint and credential reference; tests use only a local fixture server."""
import asyncio
import json
import os
import urllib.error
import urllib.request
from forge.tool_types import ToolResult


def activate(ctx, config):
    async def query(args):
        token = os.environ.get(config['credential_env'])
        if not token:
            return ToolResult.fail('authentication_missing', 'Configure the referenced credential')
        def request():
            req = urllib.request.Request(config['endpoint'] + '/' + args['id'], headers={'Authorization': 'Bearer ' + token})
            try:
                with urllib.request.urlopen(req, timeout=config.get('timeout', 2)) as response:
                    return json.loads(response.read(100_000))
            except urllib.error.HTTPError as error:
                error.close()
                return ToolResult.fail('http_error', f'HTTP {error.code}')
            except (TimeoutError, urllib.error.URLError):
                return ToolResult.fail('http_unavailable', 'Endpoint timed out or is unavailable')
        return await asyncio.to_thread(request)
    ctx.register_tool('internal_record', 'Read a record from the configured internal API', {
        'type': 'object', 'properties': {'id': {'type': 'string', 'pattern': '^[a-zA-Z0-9_-]+$'}},
        'required': ['id'], 'additionalProperties': False}, query)
