'''Run ForgeCode on Terminal-Bench through Harbor.'''

from __future__ import annotations

import sys
from typing import Sequence

from benchmark.harbor.run_dataset import PROJECT_ROOT, main as run_dataset


FIXED_TERMINAL_TASKS: tuple[str, ...] = (
    'break-filter-js-from-html',
    'build-pov-ray',
    'circuit-fibsqrt',
    'compile-compcert',
    'distribution-search',
    'make-mips-interpreter',
    'overfull-hbox',
    'path-tracing',
    'protein-assembly',
    'video-processing',
)


def main(argv: Sequence[str] | None = None) -> int:
    # The product benchmark is a controlled ten-task comparison.  Keep the
    # generic dataset runner reusable, but make the named Terminal-Bench
    # entrypoint reproducible by default. Explicit task/count arguments still
    # opt into a custom run for smoke tests and debugging.
    values = list(sys.argv[1:] if argv is None else argv)
    if '--task' not in values and '--n-tasks' not in values:
        values.extend(
            item
            for task in FIXED_TERMINAL_TASKS
            for item in ('--task', task)
        )
    if '--concurrency' not in values:
        values.extend(('--concurrency', '6'))
    if '--max-retries' not in values:
        values.extend(('--max-retries', '3'))
    if '--model' not in values:
        values.extend(('--model', 'gpt-5.6-luna'))
    return run_dataset(
        values,
        default_dataset='terminal-bench/terminal-bench-2',
        default_output_dir=PROJECT_ROOT / 'benchmark' / 'runs' / 'harbor' / 'terminal-bench',
    )


if __name__ == '__main__':
    raise SystemExit(main())
