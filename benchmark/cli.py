'''CLI for discovering the ForgeCode benchmark matrix.'''

from __future__ import annotations

import argparse
import importlib
import json
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
        'benchmark', choices=[spec.key for spec in ready_benchmarks()]
    )
    run_parser.add_argument(
        'runner_args', nargs=argparse.REMAINDER,
        help='arguments forwarded to the selected benchmark runner',
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == 'list':
        values = [spec.to_dict() for spec in BENCHMARKS]
        if args.as_json:
            print(json.dumps(values, ensure_ascii=False, indent=2))
            return 0
        print('KEY\tSTATUS\tCAPABILITY\tRUNNER')
        for value in values:
            print(
                f"{value['key']}\t{value['status']}\t"
                f"{value['capability']}\t{value['runner'] or '-'}"
            )
        return 0
    if args.command == 'run':
        spec = get_benchmark(args.benchmark)
        if spec.runner is None:
            raise SystemExit(f'Benchmark {spec.key!r} has no runner.')
        module_name = spec.runner
        runner = importlib.import_module(module_name)
        return int(runner.main(args.runner_args))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
