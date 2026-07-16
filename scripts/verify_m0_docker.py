'''Build and verify the reproducible M0 Docker baseline.'''

from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess
import sys


@dataclass(frozen=True)
class ServiceCheck:
    '''Expected result of running one Docker Compose service.'''

    service: str
    expected_exit_code: int
    required_output: tuple[str, ...]


CHECKS = (
    ServiceCheck('forge-cli', 0, ('ForgeCode terminal Agent Harness.',)),
    ServiceCheck(
        'python-calculator',
        1,
        ('test_divide_by_zero_raises', 'DID NOT RAISE'),
    ),
    ServiceCheck(
        'typescript-todo',
        1,
        ('completes the todo selected by id',),
    ),
    ServiceCheck(
        'java-order-service',
        1,
        ('multipliesUnitPriceByQuantity',),
    ),
)


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    '''Run a command and echo its combined output.'''
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    )
    print(result.stdout, end='')
    return result


def result_matches(check: ServiceCheck, result: subprocess.CompletedProcess[str]) -> bool:
    '''Return whether a service reproduced its expected M0 result.'''
    return (
        result.returncode == check.expected_exit_code
        and all(marker in result.stdout for marker in check.required_output)
    )


def main() -> int:
    '''Build every service and check the CLI and known fixture failures.'''
    if shutil.which('docker') is None:
        print('Docker is not installed or is not available on PATH.', file=sys.stderr)
        return 2

    print('Checking the Docker daemon...')
    daemon = run(['docker', 'version'])
    if daemon.returncode != 0:
        print('Docker is installed, but the daemon is unavailable.', file=sys.stderr)
        return 2

    services = [check.service for check in CHECKS]
    print('\nBuilding M0 images...')
    build = run(['docker', 'compose', '--ansi', 'never', 'build', *services])
    if build.returncode != 0:
        print('M0 image build failed.', file=sys.stderr)
        return 1

    failures: list[str] = []
    for check in CHECKS:
        print(f'\nRunning {check.service}...')
        result = run(
            [
                'docker',
                'compose',
                '--ansi',
                'never',
                'run',
                '--rm',
                '--no-deps',
                check.service,
            ]
        )
        if result_matches(check, result):
            print(f'[PASS] {check.service} produced the expected result.')
        else:
            failures.append(check.service)
            print(
                f'[FAIL] {check.service}: expected exit code '
                f'{check.expected_exit_code} and markers {check.required_output!r}.',
                file=sys.stderr,
            )

    if failures:
        failed_services = ', '.join(failures)
        print(f'\nM0 Docker verification failed: {failed_services}', file=sys.stderr)
        return 1

    print('\nM0 Docker baseline verified successfully.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
