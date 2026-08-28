'''Run ForgeCode on Terminal-Bench through Harbor.'''

from __future__ import annotations

from typing import Sequence

from benchmark.harbor.run_dataset import PROJECT_ROOT, main as run_dataset


def main(argv: Sequence[str] | None = None) -> int:
    return run_dataset(
        argv,
        default_dataset='terminal-bench/terminal-bench-2',
        default_output_dir=PROJECT_ROOT / 'benchmark' / 'runs' / 'harbor' / 'terminal-bench',
    )


if __name__ == '__main__':
    raise SystemExit(main())
