from __future__ import annotations

import json

import benchmark.cli as benchmark_cli
from benchmark.catalog import get_benchmark, ready_benchmarks
from benchmark.cli import main


def test_catalog_has_three_ready_harbor_benchmarks() -> None:
    assert [spec.key for spec in ready_benchmarks()] == [
        'aider-polyglot',
        'swe-bench-verified',
        'terminal-bench-2',
    ]
    assert get_benchmark('swe-bench-verified').dataset == (
        'swe-bench/swe-bench-verified'
    )


def test_catalog_json_is_machine_readable(capsys) -> None:
    assert main(['list', '--json']) == 0
    values = json.loads(capsys.readouterr().out)
    assert {value['key'] for value in values} >= {
        'aider-polyglot',
        'swe-bench-verified',
        'terminal-bench-2',
    }
    assert all('status' in value for value in values)


def test_run_dispatches_arguments_to_selected_ready_runner(monkeypatch) -> None:
    received: list[str] = []

    class FakeRunner:
        @staticmethod
        def main(argv) -> int:
            received.extend(argv)
            return 7

    monkeypatch.setattr(
        benchmark_cli.importlib,
        'import_module',
        lambda name: FakeRunner() if name == 'benchmark.harbor.run_terminal' else None,
    )

    assert main(['run', 'terminal-bench-2', '--n-tasks', '2']) == 7
    assert received == ['--n-tasks', '2']
