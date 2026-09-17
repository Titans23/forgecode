'''Command execution with revision-bound verification evidence, not a second gate.'''

from __future__ import annotations

import os
import json
from hashlib import sha256
from pathlib import Path
import re
from uuid import uuid4

from pydantic import Field, model_validator

from forge.runtime.workspace import WorkspaceTracker
from forge.runtime.verification import verification_quality
from forge.tools.base import Tool, ToolExecutionError, ToolInput, ToolResult, display_path, resolve_repository_path
from forge.tools.shell import has_unquoted_heredoc, process_metadata, render_process_output, run_process
from forge.tools.verification_checks import OutputCheck, dormant_python_tests, evaluate_output_checks


class VerifyInput(ToolInput):
    check_id: str | None = Field(default=None, description='Rerun a stored check definition by ID; command and assertions need not be repeated.')
    inherit_checks_from: list[str] = Field(default_factory=list, max_length=20,
        description='Reuse exact output assertions from these verification IDs when repairing a checker. Omit rewritten old output_checks; add only new checks. Without recorded output assertions, command, stdin and cwd must remain unchanged. Requires revision_reason and unchanged cwd. Automatically adds supersedes.')
    supersedes: list[str] = Field(default_factory=list, max_length=20, description='Prior verification IDs whose checker is repaired by this run; preserve every prior output assertion and cwd.')
    revision_reason: str = Field(default='', max_length=2000, description='Explain checker correction and unchanged expectations when supersedes is used.')
    requirement_ids: list[str] = Field(default_factory=list, max_length=20,
        description='Stable req IDs this check attempts. A label alone does not establish coverage: bind executed output_checks to each ID and give expected_source.')
    output_checks: list[OutputCheck] = Field(
        default_factory=list, max_length=128,
        description='Optional deterministic assertions on the final stdout JSON object. '
                    'Use for measured thresholds, required components or constraint predicates. '
                    'Failed assertions invalidate verification even when the process exits 0.',
    )
    command: str = ''
    cwd: str = Field(default='.', description='Working directory, resolved and authorized like other command paths.')
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    stdin: str | None = Field(default=None, max_length=200_000)
    covers: list[str] = Field(
        default_factory=list, max_length=20,
        description='Concrete user requirements directly exercised, not properties inferred from file existence or exit code alone.',
    )
    limitations: list[str] = Field(
        default_factory=list, max_length=20,
        description='Requested behavior or constraints this command does not establish.',
    )

    @model_validator(mode='after')
    def require_command_or_reference(self):
        if not self.command.strip() and not self.check_id:
            raise ValueError('Provide command or check_id')
        return self


class VerifyTool(Tool[VerifyInput]):
    name = 'verify'
    description = (
        'Run an authorized command and register its actual result as verification '
        'evidence. Uses the same subprocess capability as run_command; accepts '
        'stdin for scripts. State concrete requirement coverage with covers and '
        'remaining limits with limitations. A zero exit code is execution success, '
        'not proof that all user requirements are met. Pure version or directory '
        'queries are inspection-only. Evidence applies to the resulting workspace '
        'revision and environment generation; rerun after relevant changes.'
        ' To repair an earlier checker, use inherit_checks_from and revision_reason '
        'instead of rewriting its output assertions. Independently validate behavioral '
        'accuracy; valid output shape alone does not establish it. Use delta_eq with '
        'reference_key to compare two measured outputs after an input transformation. '
        'For exact components or source identity, compare measured digests with an independent '
        'reference using eq; version labels and file existence are insufficient.'
    )
    input_model = VerifyInput
    effect = 'process'

    def __init__(self, root: Path, tracker: WorkspaceTracker) -> None:
        super().__init__(root)
        self.tracker = tracker

    async def execute(self, arguments: VerifyInput) -> ToolResult:
        if not arguments.command.strip():
            raise ToolExecutionError('unresolved_check', 'Stored checks must be resolved by the task runtime.')
        if arguments.inherit_checks_from:
            raise ToolExecutionError('unresolved_check_reference', 'Recorded checks must be resolved by the turn evidence ledger before execution.')
        cwd = resolve_repository_path(self.root, arguments.cwd)
        if os.name == 'nt' and has_unquoted_heredoc(arguments.command):
            raise ToolExecutionError(
                'unsupported_shell_syntax',
                'Windows cmd.exe does not support POSIX << heredocs. Pass the program in stdin instead.',
            )
        if not cwd.is_dir():
            raise ToolExecutionError('not_a_directory', f'Command cwd is not a directory: {arguments.cwd}')
        result = await run_process(
            arguments.command, cwd=cwd, timeout_seconds=arguments.timeout_seconds,
            input_text=arguments.stdin, shell=True,
            artifact_root=self.root,
        )
        inspection = non_verification_command_reason(arguments.command)
        dormant = dormant_python_tests(arguments.command, cwd, self.root)
        assertion_failures = evaluate_output_checks(result.stdout, arguments.output_checks)
        metadata = {
            'verification_id': uuid4().hex,
            'supersedes': arguments.supersedes,
            'revision_reason': arguments.revision_reason,
            **process_metadata(result),
            'command': arguments.command,
            'cwd': display_path(self.root, cwd),
            'workspace_revision': self.tracker.revision,
            'environment_epoch': getattr(self.tracker, 'environment_epoch', 0),
            'verification_quality': verification_quality(arguments.command),
            'verification_coverage': list(arguments.covers),
            'covers': arguments.covers,
            'limitations': arguments.limitations,
            'requirement_ids': sorted(set(arguments.requirement_ids) | {
                check.requirement_id for check in arguments.output_checks if check.requirement_id
            }),
            'asserted_requirement_ids': sorted({check.requirement_id for check in arguments.output_checks
                if check.requirement_id and check.expected_source.strip()}),
            'verification': inspection is None and dormant is None,
            'inspection_reason': inspection,
            'stdin_characters': len(arguments.stdin or ''),
            'stdin_sha256': sha256(arguments.stdin.encode()).hexdigest() if arguments.stdin is not None else '',
            'evidence_valid': not dormant and not assertion_failures,
            'evidence_issues': ([dormant] if dormant else []) + assertion_failures,
            'output_checks': [check.model_dump() for check in arguments.output_checks],
            'check_signature': sha256(json.dumps(
                [check.model_dump() for check in arguments.output_checks], sort_keys=True,
            ).encode()).hexdigest() if arguments.output_checks else '',
        }
        specification = arguments.model_dump(exclude={'check_id', 'inherit_checks_from', 'supersedes', 'revision_reason'})
        identity = {key: value for key, value in specification.items() if key != 'timeout_seconds'}
        metadata['check_id'] = 'check-' + sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        metadata['check_spec'] = specification
        content = render_process_output(result)
        quality = metadata['verification_quality']
        if quality == 'unknown':
            content += ('\nEvidence classification: unknown command exit semantics. Existing assertions are retained, '
                        'but this run cannot establish positive behavioral coverage. Run the substantive checker '
                        'directly with stdin when needed; do not merely add coverage labels.')
        elif quality in {'structural', 'negative'}:
            content += f'\nEvidence classification: {quality}; this run does not establish positive task behavior.'
        if result.timed_out:
            return ToolResult.fail('verification_timeout', f'Command timed out after {arguments.timeout_seconds:g}s.', content=content, metadata=metadata)
        if result.exit_code != 0:
            return ToolResult.fail('verification_failed', f'Command exited with code {result.exit_code}.', content=content, metadata=metadata)
        if dormant or assertion_failures:
            return ToolResult.fail(
                'verification_not_established', '\n'.join(metadata['evidence_issues']),
                content=content, metadata=metadata,
            )
        return ToolResult.ok(
            ('Inspection completed; no verification evidence registered.' if inspection else
             f'Verification command exited 0 in {result.duration_seconds:.3f}s; coverage is declared, not independently proven.'),
            content=content, metadata=metadata,
        )


def non_verification_command_reason(command: str) -> str | None:
    '''Classify only complete standalone probes; never deny command execution.'''
    # Embedded directory commands must not turn a subsequent real test into a probe.
    if re.fullmatch(r'\s*(?:ls|dir|pwd|where|which)(?:\s+[^;&|\r\n]+)?\s*', command, re.I):
        return 'a standalone inspection command'
    if re.fullmatch(r'\s*\S+\s+(?:--version|-v|version)\s*', command, re.I):
        return 'a runtime or tool version query'
    if re.fullmatch(r'\s*git\s+(?:status|log|show|branch|rev-parse)(?:\s+[^;&|\r\n]+)?\s*', command, re.I):
        return 'a read-only Git inspection command'
    return None
