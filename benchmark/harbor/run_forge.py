'''Run one non-interactive ForgeCode turn inside a Harbor task container.'''

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, replace
import json
from hashlib import sha256
import os
from pathlib import Path
from typing import Any

from forge.runtime.factory import create_runtime
from forge.permissions.policy import ApprovalResponse, PermissionRequest
from forge.runtime.completion import (
    TaskPolicy,
    verification_kind,
    verification_quality,
)
from forge.runtime.profile import ExecutionProfile
from forge.runtime.state import (
    ModelTextDelta,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
    TurnResult,
)


BENCHMARK_TASK_POLICY = TaskPolicy(
    # Terminal-Bench includes services, inspection and environment tasks.
    # Their delivery need not modify a tracked workspace file.
    require_changes=False,
    require_verification=True,
    require_task_verification=True,
    require_positive_verification=True,
    require_verification_coverage=True,
    # Official verifiers own benchmark acceptance. Keep executed behavioral
    # evidence, without requiring a second model-authored req-ID/JSON protocol.
    require_acceptance_reconciliation=False,
    max_delivery_repairs=2,
)
MAX_RESULT_CHANGED_PATHS = 100
_STATUS_PREFIX = 'FORGECODE_BENCHMARK_STATUS='


def result_payload(
    result: TurnResult,
    *,
    resumed: bool,
    recovery: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        'stop_reason': result.stop_reason,
        'statistics': dict(result.statistics),
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
                'quality': verification_quality(evidence.command),
            }
            for evidence in result.verification_history
        ],
        'recovery': recovery,
        'completion_reasons': list(result.completion_reasons),
    }


async def run_turn(
    project: Path,
    message: str,
    *,
    resume: bool,
    frozen_configuration: dict | None = None,
    runtime_bindings=None,
    result_path: Path | None = None,
) -> TurnResult:
    policy=BENCHMARK_TASK_POLICY
    bound_plan=None
    if frozen_configuration:
        from forge.protocol import validate
        from forge.config import ForgeConfig
        from forge.permissions.policy import PermissionManager
        from forge.runtime.dependencies import RuntimeBindings
        parameters=validate('model-parameters',frozen_configuration['parameters'])
        harness=validate('harness-config',frozen_configuration['harness'])
        if 'plan' in frozen_configuration:
            from benchmark.adapters.harbor import export_runspec
            from forge.protocol import canonical_hash
            bound_plan=frozen_configuration['plan']
            checked=export_runspec(bound_plan['spec'],bound_plan['resolved_snapshots'])
            if (bound_plan['spec_hash']!=checked['spec_hash'] or bound_plan.get('read_only_plan') is not True
                    or parameters!=checked['resolved_snapshots']['model_parameters']
                    or harness!=checked['resolved_snapshots']['harness']):
                raise ValueError('Frozen launch configuration differs from RunSpec')
        if resume or harness['trusted_extensions_enabled'] or not harness['compaction_enabled'] or not harness['explore_enabled'] or harness['max_delivery_repairs']>2:
            raise ValueError('Unsupported Harness protocol')
        if parameters['temperature'] is not None or parameters['top_p'] is not None:
            raise ValueError('Unsupported sampling parameter must not be silently ignored')
        policy=replace(policy,max_delivery_repairs=harness['max_delivery_repairs'])
        if runtime_bindings is None:
            config=replace(ForgeConfig.from_env(environ=os.environ),max_tokens=parameters['max_output_tokens'],
                context_window=harness['max_context_tokens'],reasoning_effort=parameters['reasoning_effort'],
                **({key: frozen_configuration['model'][key] for key in ('chat_token_field', 'input_modalities')
                    if key in frozen_configuration.get('model', {})} if frozen_configuration.get('composition') else {}))
            runtime_bindings=RuntimeBindings(config=config,data_root=Path(os.environ['FORGE_DATA_DIR']),trusted_extensions=False,
                permission_manager=PermissionManager(project,load_stored_rules=False))
        if frozen_configuration.get('model'):
            actual = {key: value for key, value in asdict(runtime_bindings.config).items() if key != 'api_key'}
            if actual != frozen_configuration['model']:
                raise ValueError('Actual model configuration differs from frozen experiment')
        if bound_plan:
            config=runtime_bindings.config
            model=bound_plan['spec']['model']
            if config is None or (config.provider,config.model_id)!=(model['provider'],model['requested_model']):
                raise ValueError('Actual model identity differs from frozen RunSpec')
            if (config.max_tokens,config.context_window,config.reasoning_effort)!=(
                    parameters['max_output_tokens'],harness['max_context_tokens'],parameters['reasoning_effort']):
                raise ValueError('Actual model parameters differ from frozen RunSpec')
    composition = (frozen_configuration or {}).get('composition')
    definition = None
    if composition:
        from benchmark.harbor.platform import prepare_runtime
        definition, runtime_bindings = await prepare_runtime(composition, runtime_bindings, project)
    try:
        conversation, journal, _ = create_runtime(
            project, continue_session=resume, task_policy=policy,
            execution_profile=ExecutionProfile.sandbox(), task_relation='active' if resume else 'new',
            **({'bindings':runtime_bindings} if runtime_bindings else {}),
            **({'agent': definition} if definition else {}))
    except BaseException as error:
        if composition:
            error.plugin_cleanup = await runtime_bindings.plugin_host.close()
        raise
    if composition:
        runtime_bindings.plugin_host.model_startup.release()
        from forge.api import SessionOptions, RunProfile
        conversation.session_options = SessionOptions(project, sandbox='full-access', data_root=runtime_bindings.data_root,
            config=runtime_bindings.config, agent=definition, models=composition['models'],
            profile=RunProfile(name='evaluation', intent_routing=False))
        journal.append('evaluation_composition', {'plan_hash': composition['plan_hash'],
            'definition': composition['definition'], 'files': composition['files']})
    if frozen_configuration:
        from forge.observability.events import Scope
        scope=frozen_configuration['scope']
        journal.observation_scope=Scope(trace_id=scope['trace_id'],span_id=scope['span_id'],
            identities={key:scope[key] for key in ('run_id','trial_id')} | {'attempt_id':frozen_configuration['attempt_id']})

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
        if bound_plan:
            identity=bound_plan['spec']['harness']
            if (sha256(conversation.system_prompt.encode('utf-8')).hexdigest()!=identity['prompt_sha256']
                    or canonical_hash(conversation._tool_definitions())!=identity['tool_schema_sha256']):
                raise ValueError('Actual prompt or tool schema differs from frozen RunSpec')
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
        try:
            close=getattr(conversation,'runtime_close',None)
            cleanup = None
            if close is not None:
                cleanup = await close()
        finally:
            journal.record_stopped()

    if final is None:
        raise RuntimeError('ForgeCode ended without a TurnCompleted event.')
    payload = {**result_payload(final, resumed=resume, recovery=None), 'cleanup': cleanup}
    if result_path:
        result_path.write_text(json.dumps(payload,ensure_ascii=False)+'\n',encoding='utf-8')
    print(
        '\nFORGECODE_BENCHMARK_RESULT='
        + json.dumps(payload, ensure_ascii=False),
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
    parser.add_argument('--frozen-configuration',type=Path)
    parser.add_argument('--result-file',type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
            **({'frozen_configuration':json.loads(args.frozen_configuration.read_text(encoding='utf-8'))}
                if args.frozen_configuration else {}),
            **({'result_path':args.result_file} if args.result_file else {}),
        )
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
