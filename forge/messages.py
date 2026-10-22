"""Bounded model input, independent of SDKs, tools, storage and interfaces."""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from typing import Literal, TypedDict

MAX_IMAGE_BYTES = 512 * 1024
MAX_TEXT_BYTES = 256 * 1024
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_IMAGES = 8


class TextBlock(TypedDict):
    type: Literal['text']
    text: str


class ImageSource(TypedDict):
    type: Literal['base64']
    media_type: str
    data: str


class ImageBlock(TypedDict):
    type: Literal['image']
    source: ImageSource


UserInput = str | list[TextBlock | ImageBlock]


def image_block(data: bytes, media_type: str | None = None) -> ImageBlock:
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('Image exceeds 512 KiB; crop or resize it first.')
    detected = ('image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else
                'image/jpeg' if data.startswith(b'\xff\xd8\xff') else
                'image/gif' if data[:6] in (b'GIF87a', b'GIF89a') else
                'image/webp' if data[:4] == b'RIFF' and data[8:12] == b'WEBP' else None)
    if detected is None or media_type is not None and media_type != detected:
        raise ValueError('Expected matching PNG, JPEG, GIF or WebP image bytes.')
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': detected,
                                      'data': base64.b64encode(data).decode('ascii')}}


def normalize_input(value: UserInput) -> UserInput:
    """Validate and detach inputs before persisting or starting a model request."""
    if isinstance(value, str):
        if not value.strip():
            raise ValueError('prompt must not be empty; provide text or an image')
        if len(value.encode('utf-8')) > MAX_TEXT_BYTES:
            raise ValueError('Text input exceeds 256 KiB')
        return value
    if not isinstance(value, list) or not value or len(value) > 64:
        raise ValueError('Input must be text or 1–64 text/image blocks')
    size = text_size = image_count = 0
    meaningful = False
    for block in value:
        if not isinstance(block, dict) or 'type' not in block:
            raise ValueError('Invalid input block')
        if block['type'] == 'text' and set(block) == {'type', 'text'} and isinstance(block['text'], str):
            text_size += len(block['text'].encode('utf-8'))
            meaningful |= bool(block['text'].strip())
        elif block['type'] == 'image' and set(block) == {'type', 'source'}:
            source = block['source']
            if not isinstance(source, dict) or set(source) != {'type', 'media_type', 'data'} or source['type'] != 'base64':
                raise ValueError('Invalid image source')
            encoded = source['data']
            if not isinstance(encoded, str) or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
                raise ValueError('Image exceeds 512 KiB')
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError('Invalid image base64') from error
            image_block(data, source['media_type'])
            size += len(data)
            image_count += 1
            meaningful = True
        else:
            raise ValueError('Only text and base64 image input is supported')
    if text_size > MAX_TEXT_BYTES or size + text_size > MAX_INPUT_BYTES or image_count > MAX_IMAGES:
        raise ValueError('Input exceeds text, image count or total attachment budget')
    if not meaningful:
        raise ValueError('prompt must not be empty; provide text or an image')
    return deepcopy(value)


def input_text(value: UserInput) -> str:
    if isinstance(value, str):
        return value
    text = '\n'.join(block['text'] for block in value if block['type'] == 'text')
    return text if text.strip() else '[User supplied image input]'
