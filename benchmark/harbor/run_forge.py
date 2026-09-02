'''Run one non-interactive ForgeCode turn inside a Harbor task container.'''

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from forge.cli import create_session_runtime
from forge.permissions.policy import ApprovalResponse, PermissionRequest
from forge.runtime.completion import TaskPolicy, verification_kind
from forge.runtime.profile import ExecutionProfile
from forge.runtime.state import (
    ModelTextDelta,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
    TurnResult,
)


BENCHMARK_TASK_POLICY = TaskPolicy(
    require_changes=True,
    require_verification=True,
    require_task_verification=True,
)
MAX_RESULT_CHANGED_PATHS = 100
_STATUS_PREFIX = 'FORGECODE_BENCHMARK_STATUS='


def result_payload(result: TurnResult, *, resumed: bool) -> dict[str, Any]:
    changed_paths = list(result.changed_paths[:MAX_RESULT_CHANGED_PATHS])
    return {
        'status': result.status,
        'resumed': resumed,
        'changed_paths': changed_paths,
        'changed_path_count': len(result.changed_paths),
        'changed_paths_truncated': (
            len(result.changed_paths) > MAX_RESULT_CHANGED_PATHS
        ),
        'model_calls': result.model_calls,
        'tool_calls': len(result.tool_calls),
        'usage': asdict(result.usage),
        'verification': (
            asdict(result.verification)
            if result.verification is not None
            else None
        ),
        'verification_history': [
            {
                **asdict(evidence),
                'kind': verification_kind(evidence.command),
            }
            for evidence in result.verification_history
        ],
        'completion_reasons': list(result.completion_reasons),
    }


async def run_turn(
    project: Path,
    message: str,
    *,
    resume: bool,
    max_model_calls: int,
    max_tool_calls: int,
) -> TurnResult:
    conversation, journal, _ = create_session_runtime(
        project,
        continue_session=resume,
        task_policy=BENCHMARK_TASK_POLICY,
        execution_profile=ExecutionProfile.sandbox(),
    )
    conversation.max_iterations = max_model_calls
    conversation.max_tool_calls = max_tool_calls

    async def approve_isolated_benchmark_operation(
        request: PermissionRequest,
    ) -> ApprovalResponse:
        return ApprovalResponse(
            choice='allow_once',
            reason=(
                'Authorized inside the disposable Harbor benchmark container: '
                f'{request.capability}.'
            ),
        )

    conversation.permission_manager.approval_handler = (
        approve_isolated_benchmark_operation
    )

    final: TurnResult | None = None
    try:
        async for event in conversation.stream(message):
            if isinstance(event, ModelTextDelta):
                print(event.text, end='', flush=True)
            elif isinstance(event, ToolExecutionStarted):
                print(f'\n[forge tool] {event.tool_call.name}', flush=True)
            elif isinstance(event, ToolExecutionCompleted):
                state = 'ok' if event.result.success else 'failed'
                print(
                    f'[forge tool result] {event.tool_call.name}: {state}',
                    flush=True,
                )
            elif isinstance(event, TurnCompleted):
                final = event.result
    except BaseException as exc:
        print(
            '\n' + _STATUS_PREFIX + json.dumps(
                {
                    'status': 'failed',
                    'exception_type': type(exc).__name__,
                    'message': str(exc)[:2_000],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        raise
    finally:
        journal.record_stopped()

    if final is None:
        raise RuntimeError('ForgeCode ended without a TurnCompleted event.')
    print(
        '\nFORGECODE_BENCHMARK_RESULT='
        + json.dumps(result_payload(final, resumed=resume), ensure_ascii=False),
        flush=True,
    )
    print(
        _STATUS_PREFIX + json.dumps(
            {'status': final.status, 'exception_type': None},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return final


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, default=Path('.'))
    message = parser.add_mutually_exclusive_group(required=True)
    message.add_argument('--message')
    message.add_argument('--message-file', type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-model-calls', type=int, default=120)
    parser.add_argument('--max-tool-calls', type=int, default=240)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_model_calls < 1 or args.max_tool_calls < 1:
        raise SystemExit('Model and tool call limits must be positive.')
    instruction = args.message
    if args.message_file is not None:
        try:
            instruction = args.message_file.read_text(
                encoding='utf-8', errors='replace'
            )
        except OSError as exc:
            raise SystemExit(
                f'Unable to read --message-file {args.message_file}: {exc}'
            ) from exc
    if not instruction:
        raise SystemExit('The benchmark instruction must not be empty.')
    asyncio.run(
        run_turn(
            args.project.resolve(),
            instruction,
            resume=args.resume,
            max_model_calls=args.max_model_calls,
            max_tool_calls=args.max_tool_calls,
        )
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
