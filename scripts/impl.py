"""Repository-owned implementation commands; never loads model credentials."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib
import urllib.request
from uuid import uuid4
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs' / 'implementation'
SYMBOLS = {
    'cli': ('forge/cli.py', ['main', 'create_session_runtime', 'render_streamed_turn']),
    'factory': ('forge/runtime/factory.py', ['create_runtime', 'load_runtime_mcp_servers']),
    'conversation': ('forge/runtime/agent_loop.py', ['Conversation.__init__', 'Conversation.stream', 'Conversation.runtime_close']),
    'runner': ('forge/runtime/runner.py', ['TurnRunner.__init__', 'TurnRunner.run', 'TurnRunner._prepare_turn']),
    'executor': ('forge/runtime/executor.py', ['ToolExecutor.__init__', 'ToolExecutor.execute', 'ToolExecutor._journal_started', 'ToolExecutor._outcome']),
    'shell': ('forge/tools/shell.py', ['RunCommandTool.__init__', 'RunCommandTool.execute', 'run_process', '_terminate_process_tree']),
    'file_tools': ('forge/tools/base.py', ['resolve_repository_path', 'ToolRegistry.execute']),
    'context': ('forge/context/manager.py', ['ContextManager.prepare', 'ContextManager.compact_history']),
    'model_budget': ('forge/runtime/model_budget.py', ['BudgetedModelClient.stream', 'observe_wire_request']),
    'model': ('forge/runtime/model_client.py', ['AnthropicModelClient.__init__', 'AnthropicModelClient.stream']),
    'providers': ('forge/runtime/providers.py', ['create_model_client', 'NativeModelClient.stream']),
    'journal': ('forge/sessions/store.py', ['SessionJournal.append', 'SessionJournal._append_locked', 'SessionStore.create', 'SessionStore.open', 'SessionStore.load']),
    'benchmark_catalog': ('benchmark/catalog.py', ['BenchmarkSpec']),
    'benchmark_runner': ('benchmark/harbor/run_forge.py', ['run_turn', 'main']),
    'benchmark_results': ('benchmark/harbor/summarize.py', ['summarize_run', 'assess_trial']),
}
SUITES = {'audit': ['tests/implementation/unit/test_impl_audit.py'],
          'unit': ['tests/implementation/unit'], 'portable': ['tests/implementation/portable', 'tests/implementation/integration'],
          'regression': ['tests'], 'packaged': [],
          'sandbox-linux': None, 'sandbox-windows': None, 'desktop': None, 'live-eval': None}
TASK_SUITES = {'F00': ['audit'], 'F01': ['unit', 'packaged'], 'F02': ['unit', 'portable'], 'F03': ['unit', 'portable'], 'F04': ['unit', 'portable']}
CASE_TESTS = {'N04': ['tests/implementation/unit/test_contracts.py',
                      'tests/implementation/portable/test_contracts_parity.py'],
              'D30': ['tests/implementation/integration/test_storage.py'],
              'N02': ['tests/implementation/integration/test_storage.py'],
              'N06': ['tests/implementation/integration/test_storage.py']}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(3, json.dumps({'status': 'invalid_configuration', 'reason': message}) + '\n')


def command(argv: list[str], root: Path = ROOT) -> str:
    result = subprocess.run(argv, cwd=root, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=30)
    if result.returncode:
        raise RuntimeError(f'{argv[0]} exited {result.returncode}: {result.stderr.strip()}')
    return result.stdout.strip()


def symbol_map(root: Path, specs: dict = SYMBOLS) -> dict:
    """Read signatures without importing runtime factories or provider modules."""
    result = {}
    for responsibility, (relative, requested) in specs.items():
        tree = ast.parse((root / relative).read_text(encoding='utf-8-sig'))
        found = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found[node.name] = node
                if isinstance(node, ast.ClassDef):
                    for member in node.body:
                        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            found[f'{node.name}.{member.name}'] = member
        records = {}
        for name in requested:
            if name not in found:
                raise ValueError(f'Audited symbol no longer exists: {relative}:{name}')
            node = found[name]
            if isinstance(node, ast.ClassDef):
                signature = f'class {node.name}'
            else:
                prefix = 'async def' if isinstance(node, ast.AsyncFunctionDef) else 'def'
                signature = f'{prefix} {node.name}({ast.unparse(node.args)})'
                if node.returns:
                    signature += f' -> {ast.unparse(node.returns)}'
            records[name] = {'line': node.lineno, 'signature': signature}
        result[responsibility] = {'path': relative, 'symbols': records}
    return result


def probe_tool(name: str) -> dict:
    executable = shutil.which(name)
    if executable is None:
        return {'status': 'blocked', 'version': None, 'reason': f'{name} not found on PATH'}
    try:
        version = command([executable, '--version'])
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
        return {'status': 'blocked', 'version': None, 'reason': type(error).__name__}
    return {'status': 'pass', 'version': version, 'path': executable}


def probe_network() -> dict:
    """Explicit development-only probe of a fixed public dependency registry."""
    try:
        request = urllib.request.Request('https://registry.npmjs.org/', method='HEAD')
        with urllib.request.urlopen(request, timeout=5) as response:
            status = response.status
        return {'status': 'pass' if status == 200 else 'blocked', 'http_status': status}
    except (OSError, TimeoutError) as error:
        return {'status': 'blocked', 'reason': type(error).__name__}


def doctor(check_network: bool = False) -> dict:
    tools = {name: probe_tool(name) for name in ('git', 'node', 'npm', 'uv')}
    tools['python'] = {'status': 'pass', 'version': platform.python_version(), 'path': sys.executable}
    # uv is optional for the existing installed Python environment.
    required = ('git', 'node', 'npm', 'python')
    report = {'scope': 'development', 'platform': platform.platform(), 'tools': tools,
              'status': 'blocked' if any(tools[name]['status'] == 'blocked' for name in required) else 'pass',
              'network': {'status': 'not_run', 'reason': 'Use --check-network to probe dependency access.'}}
    if check_network:
        report['network'] = probe_network()
        if report['network']['status'] == 'blocked':
            report['status'] = 'blocked'
    return report


def audit() -> dict:
    root = Path(command(['git', 'rev-parse', '--show-toplevel'])).resolve()
    if root != ROOT:
        raise ValueError('Script must belong to the audited repository root.')
    files = command(['git', 'ls-files']).splitlines()
    instructions = []
    for parent in [*reversed(ROOT.parents), ROOT]:
        for name in ('AGENTS.md', 'AGENTS.override.md'):
            path = parent / name
            if path.is_file():
                instructions.append(str(path))
    for directory, children, names in os.walk(ROOT):
        children[:] = sorted(set(children) - {'.git', '.venv', '.local', '.cache', 'node_modules', 'build', 'runs', 'update_implementation_pack'})
        for name in ('AGENTS.md', 'AGENTS.override.md'):
            path = Path(directory) / name
            if name in names and str(path) not in instructions:
                instructions.append(str(path))
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    return {'schema_version': 'forge.repository.audit.v1', 'repository_root': str(root),
            'git_commit': command(['git', 'rev-parse', 'HEAD']),
            'dirty_files': command(['git', 'status', '--short']).splitlines(),
            'tracked_file_count': len(files), 'instructions': instructions,
            'python_requirement': project['requires-python'],
            'entry_points': {**project['scripts'], 'module': 'python -m forge', 'benchmark': 'python -m benchmark'},
            'code_map': symbol_map(root), 'development': doctor()}


def task_status(backlog: dict, progress: dict) -> dict:
    ready, waiting = [], {}
    for task in backlog['tasks']:
        if progress['tasks'][task['id']]['implementation_status'] == 'implemented':
            continue
        missing = [dep for dep in task['depends_on']
                   if progress['tasks'][dep]['implementation_status'] != 'implemented']
        if missing:
            waiting[task['id']] = missing
        else:
            ready.append(task['id'])
    return {'current_task': progress.get('current_task'), 'ready_tasks': ready,
            'waiting_tasks': waiting, 'gates': progress.get('gates', {})}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def pytest_outcome(exit_code: int, report: Path) -> tuple[str, dict]:
    if not report.is_file():
        return 'fail', {'reason': 'pytest did not produce a JUnit report'}
    cases = list(ET.parse(report).iter('testcase'))
    counts = {'collected': len(cases), 'skipped': sum(c.find('skipped') is not None for c in cases),
              'failures': sum(c.find('failure') is not None for c in cases),
              'errors': sum(c.find('error') is not None for c in cases)}
    if not cases:
        return 'fail', {**counts, 'reason': 'No tests collected'}
    if exit_code or counts['failures'] or counts['errors']:
        return 'fail', counts
    if counts['skipped']:
        return 'blocked', {**counts, 'reason': 'Required tests were skipped; inspect JUnit report'}
    return 'pass', counts


def verify(suite: str, task_id: str | None = None) -> dict:
    evidence_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
    output = ROOT / '.local' / 'implementation' / evidence_id
    output.mkdir(parents=True)
    report_path = output / ('build-smoke.json' if suite == 'packaged' else 'junit.xml')
    unavailable = SUITES[suite] is None
    if unavailable:
        argv = []
    elif suite == 'packaged':
        argv = [sys.executable, '-X', 'utf8', str(ROOT / 'scripts' / 'build_smoke.py'), '--output', str(report_path)]
    else:
        argv = [sys.executable, '-X', 'utf8', '-m', 'pytest', *SUITES[suite], '-q', '--tb=short',
                '--basetemp', str(output / 'tmp'), '--junitxml', str(report_path)]
    started = datetime.now(timezone.utc).isoformat()
    preparation_commands = []
    if suite in ('portable', 'regression'):
        node = shutil.which('node')
        if node:
            preparation_commands.append([node, str(ROOT / 'node_modules/typescript/bin/tsc'), '-p', str(ROOT / 'packages/contracts/tsconfig.json')])
    head = command(['git', 'rev-parse', 'HEAD'])
    dirty = command(['git', 'diff', '--binary', 'HEAD'])
    source_hashes = {}
    source_files = command(['git', 'ls-files', '--cached', '--others', '--exclude-standard']).splitlines()
    for relative in sorted(set(source_files)):
        path = Path(relative)
        source_directory = path.parts[0] in {'forge', 'benchmark', 'scripts', 'tests', 'packaging', 'apps', 'packages', 'sandbox_bridge', 'contracts'}
        source_manifest = relative in {'.gitattributes', '.python-version', 'pyproject.toml', 'package.json', 'package-lock.json', 'release-lock.json', 'uv.lock'}
        if (source_directory and path.suffix in {'.py', '.ts', '.js', '.mjs', '.cjs', '.json', '.toml', '.spec', '.sql'}) or source_manifest:
            source_hashes[path.as_posix()] = sha256((ROOT / path).read_bytes()).hexdigest()
    dirty_hash = sha256(json.dumps([dirty, source_hashes], sort_keys=True).encode()).hexdigest()
    with (output / 'stdout.log').open('w', encoding='utf-8') as stdout, (output / 'stderr.log').open('w', encoding='utf-8') as stderr:
        if unavailable:
            exit_code = 1
            stderr.write('Suite verifier has not been implemented; no tests were run.\n')
        else:
            try:
                exit_code = 0
                for preparation in preparation_commands:
                    prepared = subprocess.run(preparation, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=120)
                    if prepared.returncode:
                        exit_code = prepared.returncode
                        break
                if not exit_code:
                    result = subprocess.run(argv, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=900)
                    exit_code = result.returncode
            except subprocess.TimeoutExpired:
                exit_code = 1
                stderr.write('Verification exceeded 900 seconds.\n')
    if unavailable:
        status, counts = 'fail', {'collected': 0, 'reason': 'Suite verifier has not been implemented'}
    elif suite == 'packaged' and report_path.is_file():
        result_report = json.loads(report_path.read_text(encoding='utf-8'))
        status = result_report['status']
        counts = {key: result_report[key] for key in ('development_smoke', 'reason', 'security_status') if key in result_report}
        counts['checks'] = len(result_report.get('checks', []))
        if status == 'pass' and (exit_code != 0 or not counts['checks']):
            status = 'fail'
    else:
        status, counts = pytest_outcome(exit_code, report_path)
    lock_hashes = {name: sha256((ROOT / name).read_bytes()).hexdigest()
                   for name in ('uv.lock', 'package-lock.json', 'release-lock.json') if (ROOT / name).is_file()}
    evidence = {'schema_version': 'forge.implementation.evidence.v1', 'evidence_id': evidence_id,
                'task_id': task_id, 'case_ids': [case for case, refs in CASE_TESTS.items()
                    if suite not in ('packaged',) and SUITES[suite] and any(any(ref.startswith(path) for path in SUITES[suite]) for ref in refs)],
                'suite': suite, 'git_commit': head,
                'dirty_hash': dirty_hash, 'platform': sys.platform, 'os_build': platform.platform(),
                'dependency_lock_hash': lock_hashes, 'command': argv, 'start': started,
                'preparation_commands': preparation_commands,
                'end': datetime.now(timezone.utc).isoformat(), 'exit_code': exit_code,
                'stdout_ref': (output / 'stdout.log').relative_to(ROOT).as_posix(),
                'stderr_ref': (output / 'stderr.log').relative_to(ROOT).as_posix(),
                'report_ref': report_path.relative_to(ROOT).as_posix(),
                'report_hash': sha256(report_path.read_bytes()).hexdigest() if report_path.exists() else None,
                'status': status, 'tests': counts}
    write_json(DOCS / 'evidence' / f'{evidence_id}.json', evidence)
    return evidence


def gate(name: str) -> dict:
    progress = json.loads((DOCS / 'progress.json').read_text(encoding='utf-8'))
    incomplete = [task for task, state in progress['tasks'].items() if state['implementation_status'] != 'implemented']
    missing_evidence = []
    unverified = []
    for task, state in progress['tasks'].items():
        if state['implementation_status'] != 'implemented':
            continue
        if any(status in ('fail', 'not_run') for status in state.get('verification', {}).values()):
            unverified.append(task)
        for evidence_id in state['evidence_ids']:
            path = DOCS / 'evidence' / (evidence_id + '.json')
            if not path.is_file():
                missing_evidence.append(evidence_id)
                continue
            evidence = json.loads(path.read_text(encoding='utf-8'))
            report = (ROOT / evidence['report_ref']).resolve()
            if not report.is_relative_to(ROOT) or not report.is_file() or sha256(report.read_bytes()).hexdigest() != evidence['report_hash']:
                missing_evidence.append(evidence_id)
            if evidence.get('status') == 'fail' or (evidence.get('status') == 'pass' and evidence.get('exit_code') != 0):
                unverified.append(task)
        if not state['evidence_ids']:
            missing_evidence.append(task)
    blockers = {task: state['blocked_reasons'] for task, state in progress['tasks'].items() if state['blocked_reasons']}
    return {'status': 'fail' if incomplete or missing_evidence or unverified else 'blocked' if name == 'release' and blockers else 'pass',
            'gate': name, 'incomplete_tasks': incomplete, 'missing_or_changed_evidence': missing_evidence,
            'failed_or_unverified_tasks': sorted(set(unverified)),
            'blocked_dependencies': blockers if name == 'release' else {}}


def main(argv: list[str] | None = None) -> int:
    parser = Parser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    audit_parser = subs.add_parser('audit', help='Read actual repository symbols and toolchain')
    audit_parser.add_argument('--write', action='store_true', help='Save audit and code-map in docs/implementation')
    doctor_parser = subs.add_parser('doctor', help='Diagnose development dependencies')
    doctor_parser.add_argument('--scope', choices=['development'], default='development')
    doctor_parser.add_argument('--check-network', action='store_true')
    subs.add_parser('status', help='Read task progress and dependency readiness')
    contract_parser = subs.add_parser('contracts', help='Generate shared contracts or check drift')
    contract_parser.add_argument('--check', action='store_true')
    gate_parser = subs.add_parser('gate', help='Check implementation or release evidence without running tasks')
    gate_parser.add_argument('--name', choices=['implementation', 'release'], required=True)
    verify_parser = subs.add_parser('verify', help='Run registered real tests and save evidence')
    selection = verify_parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--suite', choices=sorted(SUITES))
    selection.add_argument('--task', choices=[task['id'] for task in json.loads((DOCS / 'backlog.json').read_text(encoding='utf-8'))['tasks']])
    args = parser.parse_args(argv)
    try:
        if args.command == 'audit':
            report = audit()
            if args.write:
                write_json(DOCS / 'repo-audit.json', report)
                write_json(DOCS / 'code-map.json', report['code_map'])
        elif args.command == 'doctor':
            report = doctor(args.check_network)
        elif args.command == 'status':
            report = task_status(json.loads((DOCS / 'backlog.json').read_text(encoding='utf-8')),
                                 json.loads((DOCS / 'progress.json').read_text(encoding='utf-8')))
        elif args.command == 'contracts':
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/check_contracts.py'), *(['--check'] if args.check else [])], cwd=ROOT)
            return result.returncode
        elif args.command == 'gate':
            report = gate(args.name)
        else:
            if args.task:
                if args.task not in TASK_SUITES:
                    print(json.dumps({'status': 'fail', 'task_id': args.task, 'reason': 'Task test bindings are not implemented'}))
                    return 1
                evidence = [verify(suite, args.task) for suite in TASK_SUITES[args.task]]
                states = [item['status'] for item in evidence]
                report = {'status': 'fail' if 'fail' in states else 'blocked' if 'blocked' in states else 'pass',
                          'task_id': args.task, 'evidence': evidence}
            else:
                report = verify(args.suite)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return {'fail': 1, 'blocked': 2}.get(report.get('status'), 0)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'status': 'fail', 'reason': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
