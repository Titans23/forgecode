'''Run ForgeCode on Terminal-Bench through Harbor.'''

from __future__ import annotations

from typing import Sequence

from benchmark.harbor.run_dataset import main as run_dataset


def main(argv: Sequence[str] | None = None) -> int:
    return run_dataset(
        argv,
        default_dataset='terminal-bench/terminal-bench-2',
    )


if __name__ == '__main__':
    raise SystemExit(main())
