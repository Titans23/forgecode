'''Abstract chat transport used by the ForgeCode gateway.'''

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from forge.channels.models import ApprovalAction, InboundMessage, OutboundMessage
from forge.permissions.policy import PermissionRequest


MessageHandler = Callable[[InboundMessage], Awaitable[None]]
ApprovalHandler = Callable[[ApprovalAction], Awaitable[None]]


class ChannelAdapter(ABC):
    '''Official platform connection with normalized messages and approvals.'''

    @abstractmethod
    async def start(
        self,
        on_message: MessageHandler,
        on_approval: ApprovalHandler,
    ) -> None:
        '''Connect and dispatch until stopped.'''

    @abstractmethod
    async def stop(self) -> None:
        '''Close network transports and pending work.'''

    @abstractmethod
    async def send(self, message: OutboundMessage) -> None:
        '''Send one normalized message.'''

    @abstractmethod
    async def request_approval(
        self,
        *,
        chat_id: str,
        requester_id: str,
        approval_id: str,
        arguments_hash: str,
        request: PermissionRequest,
    ) -> None:
        '''Render an exact, single-use approval request.'''
