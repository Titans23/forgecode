'''CLI for discovering the ForgeCode benchmark matrix.'''

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Sequence

from benchmark.catalog import BENCHMARKS, get_benchmark, ready_benchmarks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m benchmark',
        description='Discover supported ForgeCode benchmark adapters.',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)
    list_parser = subparsers.add_parser('list', help='list benchmark capabilities')
    list_parser.add_argument(
        '--json', action='store_true', dest='as_json',
        help='print machine-readable benchmark metadata',
    )
    run_parser = subparsers.add_parser(
        'run', help='run one ready benchmark through its adapter'
    )
    run_parser.add_argument(
        'benchmark', nargs='?', choices=[spec.key for spec in ready_benchmarks()]
    )
    run_parser.add_argument('--plan', type=Path)
    run_parser.add_argument('--execute', action='store_true')
    run_parser.add_argument('--trust-plugins', action='store_true')
    run_parser.add_argument('--output', type=Path)
    run_parser.add_argument(
        'runner_args', nargs=argparse.REMAINDER,
        help='arguments forwarded to the selected benchmark runner',
    )
    for command in ('status', 'cancel', 'report'):
        child = subparsers.add_parser(command, help=f'{command} an evaluation run')
        child.add_argument('run_dir', type=Path)
    export = subparsers.add_parser('export', help='export offline diagnostic trajectories')
    export.add_argument('run_dir', type=Path)
    export.add_argument('--format', choices=['atif', 'openai'], required=True)
    export.add_argument('--output-dir', type=Path)
    compare = subparsers.add_parser('compare', help='compare frozen runs and list uncontrolled differences')
    compare.add_argument('baseline', type=Path)
    compare.add_argument('candidate', type=Path)
    compare.add_argument('--change', choices=['harness', 'model', 'plugin'], required=True)
    plan = subparsers.add_parser('plan', help='freeze tasks, source, composition, models and execution conditions without model calls')
    plan.add_argument('configuration', type=Path)
    plan.add_argument('--output', type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == 'plan':
        from benchmark.harbor.platform import make_plan
        print(json.dumps(make_plan(args.configuration, args.output), ensure_ascii=True, indent=2))
        return 0
    if args.command == 'compare':
        from benchmark.harbor.summarize import compare_runs
        print(json.dumps(compare_runs(args.baseline, args.candidate, change=args.change), ensure_ascii=True, indent=2))
        return 0
    if args.command == 'export':
        from benchmark.harbor.summarize import export_trajectories
        output = args.output_dir or args.run_dir / 'exports' / args.format
        try:
            value = export_trajectories(args.run_dir, output, format=args.format)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(value, ensure_ascii=True, indent=2))
        return 0
    if args.command in {'status', 'cancel', 'report'}:
        from benchmark.harbor.controller import controller_status, cancel_run
        from benchmark.harbor.summarize import evaluation_report
        if not args.run_dir.is_dir():
            raise SystemExit('Run directory does not exist.')
        value = (cancel_run(args.run_dir) if args.command == 'cancel' else
                 controller_status(args.run_dir) if args.command == 'status' else evaluation_report(args.run_dir))
        print(json.dumps(value, ensure_ascii=True, indent=2))
        return 0
    if args.command == 'list':
        values = [spec.to_dict() for spec in BENCHMARKS]
        if args.as_json:
            # Machine-readable JSON must also work in redirected Windows ASCII/codepage streams.
            print(json.dumps(values, ensure_ascii=True, indent=2))
            return 0
        print('KEY\tSTATUS\tCAPABILITY\tRUNNER')
        for value in values:
            print(
                f"{value['key']}\t{value['status']}\t"
                f"{value['capability']}\t{value['runner'] or '-'}"
            )
        return 0
    if args.command == 'run':
        if args.plan:
            if args.benchmark or args.runner_args:
                raise SystemExit('A frozen plan cannot be combined with arbitrary runner overrides')
            from benchmark.harbor.platform import run_plan
            result = run_plan(args.plan, output=args.output, execute=args.execute, trust_plugins=args.trust_plugins)
            print(json.dumps(result, ensure_ascii=True, indent=2))
            return result.get('exit_code', 0)
        if not args.benchmark:
            raise SystemExit('Choose a benchmark or --plan')
        spec = get_benchmark(args.benchmark)
        if spec.runner is None:
            raise SystemExit(f'Benchmark {spec.key!r} has no runner.')
        module_name = spec.runner
        runner = importlib.import_module(module_name)
        return int(runner.main(args.runner_args))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
