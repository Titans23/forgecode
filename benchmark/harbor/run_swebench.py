'''Run ForgeCode on SWE-bench Verified through Harbor.'''

from __future__ import annotations

from typing import Sequence

from benchmark.harbor.run_dataset import PROJECT_ROOT, main as run_dataset


def main(argv: Sequence[str] | None = None) -> int:
    return run_dataset(
        argv,
        default_dataset='swe-bench/swe-bench-verified',
        default_output_dir=PROJECT_ROOT / 'benchmark' / 'runs' / 'harbor' / 'swe-bench-verified',
    )


if __name__ == '__main__':
    raise SystemExit(main())
