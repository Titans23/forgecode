'''Provider-independent chat channel models.'''

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib


@dataclass(frozen=True, slots=True)
class Attachment:
    '''One remote attachment without eagerly downloading its content.'''

    kind: str
    name: str = ''
    key: str = ''
    mime_type: str = ''


@dataclass(frozen=True, slots=True)
class InboundMessage:
    '''Normalized message delivered by one official platform adapter.'''

    platform: str
    tenant_id: str
    message_id: str
    sender_id: str
    chat_id: str
    text: str
    chat_type: str = 'p2p'
    thread_id: str = ''
    sender_name: str = ''
    mentioned_bot: bool = False
    attachments: tuple[Attachment, ...] = ()
    raw: dict[str, object] = field(default_factory=dict, compare=False)

    @property
    def session_key(self) -> str:
        '''Stable, non-secret key for per-chat Conversation isolation.'''
        # Feishu private replies may be rendered as topic messages. Keep one
        # context for the private chat; retain topic isolation for groups.
        thread_scope = '' if self.chat_type == 'p2p' else self.thread_id
        raw = '\x1f'.join(
            (
                self.platform,
                self.tenant_id,
                self.chat_id,
                thread_scope,
            )
        )
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    '''Normalized response sent through a channel adapter.'''

    chat_id: str
    text: str
    format: str = 'markdown'
    reply_to_message_id: str = ''
    thread_id: str = ''


@dataclass(frozen=True, slots=True)
class ApprovalAction:
    '''A platform callback resolving one pending permission request.'''

    approval_id: str
    actor_id: str
    decision: str
    arguments_hash: str
