'''Unit tests for the M0 Docker result classification.'''

from subprocess import CompletedProcess

from scripts.verify_m0_docker import ServiceCheck, result_matches


def completed(exit_code: int, output: str) -> CompletedProcess[str]:
    return CompletedProcess(args=['docker'], returncode=exit_code, stdout=output)


def test_result_matches_expected_exit_code_and_markers() -> None:
    check = ServiceCheck('fixture', 1, ('failed test', 'assertion'))

    assert result_matches(check, completed(1, 'failed test: assertion'))


def test_result_rejects_dependency_failure_without_test_marker() -> None:
    check = ServiceCheck('fixture', 1, ('failed test',))

    assert not result_matches(check, completed(1, 'command not found'))


def test_result_rejects_unexpected_exit_code() -> None:
    check = ServiceCheck('fixture', 1, ('failed test',))

    assert not result_matches(check, completed(0, 'failed test'))
