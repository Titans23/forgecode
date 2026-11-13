'''Bounded provider diagnostics shared by HTTP adapters; never dump request bodies.'''
import json
import re

from forge.runtime.state import ModelCallError
from forge.sessions.trajectory import redact_text


def provider_failure(error, reason, message, *, retryable, api_key=None):
    response = getattr(error, 'response', None)
    status = getattr(error, 'status_code', None) or getattr(response, 'status_code', None)
    headers = getattr(response, 'headers', {})
    try:
        request = getattr(error, 'request', None)
    except RuntimeError:
        # Raw HTTPX transport errors may precede a bound request. Diagnostics
        # must not replace the original connection failure with this accessor error.
        request = None
    secrets = [api_key] if isinstance(api_key, str) and api_key else []
    for key in ('authorization', 'x-api-key', 'x-goog-api-key', 'api-key'):
        value = getattr(request, 'headers', {}).get(key)
        if value:
            secrets.extend((value, value.removeprefix('Bearer ')))
    def safe(value, maximum=1024):
        text = str(value)
        for secret in secrets:
            text = text.replace(secret, '[redacted]')
        text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', redact_text(text))
        return ''.join(char if char.isprintable() else ' ' for char in text)[:maximum]
    detail = {'http_status': status} if status is not None else {}
    request_id = getattr(error, 'request_id', None) or headers.get('request-id') or headers.get('x-request-id')
    if request_id:
        detail['request_id'] = safe(request_id, 200)
    body = getattr(error, 'body', None)
    if isinstance(body, str) and len(body) <= 16384:
        try:
            body = json.loads(body)
        except ValueError:
            body = None
    if isinstance(body, dict):
        body = body.get('error', body)
        if isinstance(body, dict):
            for key in ('type', 'code', 'param', 'message'):
                value = body.get(key)
                if isinstance(value, (str, int)):
                    detail[key] = safe(value, 1024 if key == 'message' else 200)
    suffix = ' ' + json.dumps(detail, ensure_ascii=False) if detail else ''
    return ModelCallError(reason, message + suffix, retryable=retryable, diagnostics=detail)


async def read_error_body(response):
    """Gemini's streamed HTTP errors have not been read by an SDK."""
    data = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=4096):
        data.extend(chunk[:16384-len(data)])
        if len(data) >= 16384:
            break
    try:
        return json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
