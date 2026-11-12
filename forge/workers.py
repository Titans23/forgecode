"""Private worker entry independent of the legacy RPC control plane."""
import sys

ROLES = frozenset({'file-worker', 'process-worker', 'external-worker', 'doctor', 'office-mcp'})


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
    if arguments and arguments[0]=='office-mcp':
        from forge.office.mcp_server import main as office_main
        return office_main()
    raise SystemExit('Expected one private worker role: ' + ', '.join(sorted(ROLES)))


if __name__ == '__main__':
    raise SystemExit(main())
