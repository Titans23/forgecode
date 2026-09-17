'''Delivery and observations are independent of an agent's success claim.'''

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.runtime.state import VerificationEvidence


@dataclass(frozen=True, slots=True)
class CompletionReport:
    run_status: str
    agent_assessment: str
    acceptance_status: str
    verification_status: str
    passed_checks: tuple[str, ...] = ()
    failed_checks: tuple[str, ...] = ()
    historical_checks: tuple[str, ...] = ()
    unmet_requirements: tuple[str, ...] = ()
    usage_complete: bool = True

    def summary(self) -> str:
        return (f'Verification: {self.verification_status}; '
                f'{len(self.passed_checks)} passed, {len(self.failed_checks)} failed, '
                f'{len(self.historical_checks)} historical/expired. '
                f'Caller acceptance: {self.acceptance_status}.'
                + ('' if self.usage_complete else ' Token usage is incomplete.'))


def completion_report(*, status: str, evidence: tuple['VerificationEvidence', ...],
                      workspace_revision: int, environment_epoch: int,
                      reasons: tuple[str, ...], has_contract: bool,
                      agent_assessment: str | None = None, usage_complete: bool = True) -> CompletionReport:
    # Repeated runs of the same check remain in the durable ledger; presentation
    # uses the latest observation, without erasing distinct failed checks.
    from forge.runtime.completion import unresolved_verification_failures
    unresolved = {id(item) for item in unresolved_verification_failures(evidence)}
    latest = {}
    for item in evidence:
        key = (item.command, item.cwd, item.stdin_sha256, item.check_signature)
        latest[key] = item
    passed, failed, historical = [], [], []
    for item in latest.values():
        identity = item.verification_id or item.command
        current = (item.freshness == 'current' and item.workspace_revision == workspace_revision
                   and item.environment_epoch == environment_epoch)
        resolved = not item.success and id(item) not in unresolved
        target = historical if not current or resolved else passed if item.success else failed
        target.append(identity)
    verification = 'failed_checks' if failed else 'passed_checks' if passed else 'unverified'
    # 运行结束、模型自评和调用方验收分别记录；有产物不等于满足全部要求。
    acceptance = ('unmet' if reasons else 'met') if has_contract else 'not_configured'
    # Failure/cancellation must never imply that unassessed caller checks passed.
    if has_contract and status != 'completed' and not reasons:
        acceptance = 'unknown'
    return CompletionReport(
        run_status='completed' if status in {'completed', 'partial', 'blocked'} else 'failed',
        agent_assessment=agent_assessment or status, acceptance_status=acceptance,
        verification_status=verification, passed_checks=tuple(passed),
        failed_checks=tuple(failed), historical_checks=tuple(historical),
        unmet_requirements=reasons,
        usage_complete=usage_complete,
    )
