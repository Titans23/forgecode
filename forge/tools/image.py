"""Bounded image input through the existing file authorization boundary."""
import asyncio
import base64
from hashlib import sha256

from pydantic import Field
from forge.tools.base import Tool, ToolInput, ToolResult, ToolExecutionError
from forge.tools.paths import file_access_guard, resolve_repository_path

MAX_IMAGE_BYTES = 512 * 1024  # Base64 plus the helper envelope stays below 1 MiB.


class ViewImageInput(ToolInput):
    path: str = Field(min_length=1)


class ViewImageTool(Tool[ViewImageInput]):
    name = 'view_image'
    description = ('Read a local PNG, JPEG, GIF or WebP image as visual model input. '
                   'Maximum 512 KiB; use run_command to crop or resize larger images first. '
                   'Use this for screenshots, diagrams and photographed text instead of ASCII rendering.')
    input_model = ViewImageInput

    async def execute(self, arguments):
        return await asyncio.to_thread(self._read, arguments.path)

    def _read(self, raw):
        path = resolve_repository_path(self.root, raw)
        access = file_access_guard.get()
        if access:
            data = access.read_bytes(path, MAX_IMAGE_BYTES + 1)
        else:
            with path.open('rb') as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ToolExecutionError('image_too_large', 'Image exceeds 512 KiB; crop or resize it first.')
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            media = 'image/png'
        elif data.startswith(b'\xff\xd8\xff'):
            media = 'image/jpeg'
        elif data[:6] in (b'GIF87a', b'GIF89a'):
            media = 'image/gif'
        elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            media = 'image/webp'
        else:
            raise ToolExecutionError('unsupported_image', 'Expected PNG, JPEG, GIF or WebP image bytes.')
        return ToolResult.ok(f'Image: {raw}', metadata={
            'path': raw, 'bytes': len(data), 'sha256': sha256(data).hexdigest(),
            'image': {'type': 'image', 'source': {'type': 'base64', 'media_type': media,
                      'data': base64.b64encode(data).decode('ascii')}}})
