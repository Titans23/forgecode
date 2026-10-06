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
    parser = argparse.ArgumentParser(description='ForgeCode private JSONL Engine')
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--profile', choices=('desktop', 'cli', 'test', 'evaluation'), default='desktop')
    parser.add_argument('--profile-id')
    parser.add_argument('--principal', choices=('main', 'renderer'), default='main')
    parser.add_argument('--execution-mode', choices=('strict', 'local-trusted'), default='strict')
    parser.add_argument('--scripted-fixture', type=Path)
    args = parser.parse_args(argv)
    if args.scripted_fixture and args.profile != 'test':
        print(json.dumps({'status': 'invalid_configuration', 'reason': 'scripted fixture requires test profile'}), file=sys.stderr)
        return 3
    if args.execution_mode == 'local-trusted' and args.profile not in ('test', 'cli'):
        print(json.dumps({'status': 'invalid_configuration', 'reason': 'desktop/evaluation require strict sandbox'}), file=sys.stderr)
        return 3
    input_descriptor, output_descriptor = sys.stdin.fileno(), sys.stdout.fileno()
    if sys.platform == 'win32':
        import msvcrt
        import os
        msvcrt.setmode(input_descriptor, os.O_BINARY)
        msvcrt.setmode(output_descriptor, os.O_BINARY)
    try:
        # A dependency accidentally printing cannot corrupt the protocol pipe.
        with redirect_stdout(sys.stderr):
            from forge.application.harness_adapter import LocalTrustedBackend
            from forge.application.services import ApplicationServices
            from forge.engine.methods import EngineMethods
            from forge.engine.persistence import Store
            from forge.engine.rpc import RpcServer
            from forge.engine.test_profile import MemoryCredentials, load_scripted_profile
            scripted = load_scripted_profile(args.scripted_fixture) if args.scripted_fixture else None
            credentials, factory = (scripted.credentials, scripted.model_client_factory) if scripted else (MemoryCredentials(), None)
            with Store(args.data_dir) as store:
                service = ApplicationServices(store, profile_id=args.profile_id or args.profile + '-profile', credentials=credentials,
                    mode=args.execution_mode, backend=LocalTrustedBackend() if args.execution_mode == 'local-trusted' else None,
                    model_client_factory=factory, task_relation='new' if factory else None,
                    approval_handler=scripted.approval_handler if scripted else None)
                return asyncio.run(RpcServer(EngineMethods(service, profile=args.profile), principal=args.principal).run(input_descriptor, output_descriptor))
    except Exception as error:
        print(json.dumps({'status': 'blocked', 'exception_type': type(error).__name__,
                          'kind': str(getattr(error, 'kind', 'INDETERMINATE'))}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
