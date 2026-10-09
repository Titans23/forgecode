"""Private stdio Engine entry point; strict sandbox is the desktop default."""
import argparse
import asyncio
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == 'file-worker':
        from forge.sandbox.file_worker import main as worker_main
        return worker_main(arguments[1:])
    if arguments and arguments[0] in ('process-worker','external-worker'):
        from forge.release.processes import main as process_main
        return process_main(arguments[1:],gated=arguments[0]=='process-worker')
    if arguments and arguments[0]=='doctor':
        from forge.sandbox.doctor import main as doctor_main
        return doctor_main(arguments[1:])
    if arguments and arguments[0]=='upgrade':
        from forge.release.__main__ import main as upgrade_main
        return upgrade_main(arguments[1:])
    if arguments and arguments[0]=='cli':
        from forge.cli import app
        app(args=arguments[1:])
        return 0
    if arguments and arguments[0]=='office-mcp':
        from forge.office.mcp_server import main as office_main
        return office_main()
    parser = argparse.ArgumentParser(description='ForgeCode private JSONL Engine')
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--profile', choices=('desktop', 'cli', 'test', 'evaluation'), default='desktop')
    parser.add_argument('--profile-id')
    parser.add_argument('--principal', choices=('main', 'renderer'), default='main')
    parser.add_argument('--main-owner-pid',type=int,help='Trusted Main parent identity for private desktop lifecycle')
    parser.add_argument('--execution-mode', choices=('strict', 'local-trusted', 'workspace-write'), default='strict')
    parser.add_argument('--scripted-fixture', type=Path)
    parser.add_argument('--interactive-approvals', action='store_true')
    parser.add_argument('--pricing-file',type=Path)
    parser.add_argument('--capture-mode',choices=('metadata','controlled_debug'),default='metadata')
    parser.add_argument('--otlp-endpoint')
    parser.add_argument('--export-metadata',action='store_true',help='Confirm outbound OTLP metadata export to the supplied endpoint')
    parser.add_argument('--evaluation-benchmark',choices=('aider-polyglot','swe-bench-verified','terminal-bench-2'))
    parser.add_argument('--evaluation-taskset',type=Path)
    parser.add_argument('--evaluation-source',type=Path)
    args = parser.parse_args(argv)
    if any((args.evaluation_benchmark,args.evaluation_taskset,args.evaluation_source)) and not all((args.evaluation_benchmark,args.evaluation_taskset,args.evaluation_source)):
        print(json.dumps({'status':'invalid_configuration','reason':'Official evaluation needs benchmark, exact taskset and frozen source together'}),file=sys.stderr)
        return 3
    if args.interactive_approvals and (args.profile != 'test' or args.principal != 'main'):
        print(json.dumps({'status': 'invalid_configuration', 'reason': 'interactive test approvals require a private Main test profile'}), file=sys.stderr)
        return 3
    if args.scripted_fixture and args.profile != 'test':
        print(json.dumps({'status': 'invalid_configuration', 'reason': 'scripted fixture requires test profile'}), file=sys.stderr)
        return 3
    if args.execution_mode != 'strict' and (args.profile == 'evaluation' or
            args.profile == 'desktop' and (args.principal != 'main' or args.main_owner_pid is None)):
        print(json.dumps({'status': 'invalid_configuration', 'reason': 'Local desktop execution requires its owning Main; evaluation requires strict sandbox'}), file=sys.stderr)
        return 3
    input_descriptor, output_descriptor = sys.stdin.fileno(), sys.stdout.fileno()
    if sys.platform == 'win32':
        import msvcrt
        import os
        msvcrt.setmode(input_descriptor, os.O_BINARY)
        msvcrt.setmode(output_descriptor, os.O_BINARY)
    parent_lease=None
    try:
        if args.main_owner_pid is not None:
            from forge.engine.parent_watch import ParentLease
            parent_lease=ParentLease.open(args.main_owner_pid)
        # A dependency accidentally printing cannot corrupt the protocol pipe.
        with redirect_stdout(sys.stderr):
            from forge.engine.bootstrap import create_application
            from forge.engine.methods import EngineMethods
            from forge.engine.persistence import Store
            from forge.engine.rpc import RpcServer
            from forge.engine.test_profile import load_scripted_profile
            from forge.observability.export_queue import ObservationOptions
            from forge.observability.usage_ledger import PriceBook
            from forge.application.models import strict_loads
            prices=PriceBook(strict_loads(args.pricing_file.read_bytes())) if args.pricing_file else None
            observations=ObservationOptions(capture_mode=args.capture_mode,prices=prices,endpoint=args.otlp_endpoint,metadata_export_confirmed=args.export_metadata)
            scripted = load_scripted_profile(args.scripted_fixture) if args.scripted_fixture else None
            adapter = None
            if args.evaluation_benchmark:
                from benchmark.adapters.harbor import HarborAdapter
                adapter = HarborAdapter(args.evaluation_benchmark, taskset_root=args.evaluation_taskset,
                    source_root=args.evaluation_source)
            with Store(args.data_dir) as store:
                service = create_application(store, profile=args.profile, mode=args.execution_mode,
                    profile_id=args.profile_id, native_execution=True, scripted=scripted,
                    interactive_approvals=args.interactive_approvals, observation_options=observations,
                    evaluation_adapter=adapter)
                return asyncio.run(RpcServer(EngineMethods(service, profile=args.profile, interactive_approvals=args.interactive_approvals), principal=args.principal,parent_lease=parent_lease).run(input_descriptor, output_descriptor))
    except Exception as error:
        print(json.dumps({'status': 'blocked', 'exception_type': type(error).__name__,
                          'kind': str(getattr(error, 'kind', 'INDETERMINATE'))}), file=sys.stderr)
        return 2
    finally:
        if parent_lease is not None:
            parent_lease.close()


if __name__ == '__main__':
    raise SystemExit(main())
