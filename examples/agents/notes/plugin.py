"""A personal tool using plugin-owned data, with no repository or workspace."""
from pathlib import Path


def activate(ctx, config):
    path = ctx.data_dir / 'notes.txt'
    def append(args):
        with path.open('a', encoding='utf-8') as stream:
            stream.write(args['text'] + '\n')
        return 'saved'
    ctx.register_tool('save_note', 'Append a local note', {'type': 'object',
        'properties': {'text': {'type': 'string'}}, 'required': ['text'], 'additionalProperties': False},
        append, effect='workspace_write')
    ctx.register_tool('list_notes', 'Read saved notes', {'type': 'object', 'additionalProperties': False},
                      lambda args: path.read_text(encoding='utf-8') if path.exists() else '')
