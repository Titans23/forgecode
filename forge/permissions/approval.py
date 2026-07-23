'''Approval handlers shared by interactive and non-interactive runtimes.'''

from __future__ import annotations

from dataclasses import dataclass

from forge.permissions.policy import ApprovalChoice, ApprovalResponse, PermissionRequest


@dataclass(slots=True)
class StaticApprovalHandler:
    '''Deterministic approval handler for tests and embedded runtimes.'''

    choice: ApprovalChoice = 'deny'
    reason: str = ''

    async def __call__(self, request: PermissionRequest) -> ApprovalResponse:
        del request
        return ApprovalResponse(self.choice, self.reason)
