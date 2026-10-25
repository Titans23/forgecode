"""Native SWE-bench Lite task adapter for the shared HarnessSession evaluator.

Only the problem and a fresh base checkout are Agent inputs. Frozen test patches
are applied to a separate evaluator copy after the Agent session is closed.
"""

from dataclasses import replace
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
from time import monotonic
from uuid import uuid4

from evals.runner import CommandEvidence, EvalCase, PreparedFixture, execute_command, run_case
from evals.swe_lite_environment import checked, install_task_environment, task_environment
from evals.swe_lite_grader import assessment
from forge.api import HarnessSession, RunProfile, SessionOptions
from forge.config import ForgeConfig
from forge.runtime.providers import create_model_client
from forge.sandbox.path_policy import PathPolicy, inspect_path
from forge.sandbox.workspace_transfer import TransferLimits, WorkspaceSnapshot, decode_tree
from forge.sessions.store import SessionStore


ROOT = Path(__file__).resolve().parents[1]


def shell_command(argv):
    return subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)


class TaskSnapshot(WorkspaceSnapshot):
    """Reuse bounded link-safe transfer, excluding task dependency/build caches."""

    def _protected(self, path):
        return super()._protected(path) or any(
            part in {'__pycache__', '.pytest_cache', '.hypothesis'} or part.endswith('.egg-info')
            for part in path.relative_to(self.root).parts)


def source_snapshot(workspace, scope):
    policy = PathPolicy(inspect_path(workspace, absolute=True), 'strict_allowlist_required',
                        (workspace,), (workspace / scope,), ())
    return TaskSnapshot(policy, limits=TransferLimits(entries=8000, bytes=32 * 1024 * 1024))


def candidate_delta(before, after, *, base_commit, scope):
    changed = sorted(name for name in before.files.keys() | after.files.keys()
                     if before.files.get(name) != after.files.get(name))
    allowed = lambda name: name == scope or name.startswith(scope + '/')
    files = {}
    for name in changed:
        if not allowed(name):
            continue
        old, new = before.files.get(name), after.files.get(name)
        files[name] = {'before_sha256': hashlib.sha256(old[0]).hexdigest() if old else None,
                       'after_sha256': hashlib.sha256(new[0]).hexdigest() if new else None,
                       'content': base64.b64encode(new[0]).decode('ascii') if new else None,
                       'executable': new[1] if new else False}
    return {'base_commit': base_commit, 'scope': scope, 'files': files,
            'added_directories': sorted(name for name in after.directories - before.directories if allowed(name)),
            'removed_directories': sorted(name for name in before.directories - after.directories if allowed(name)),
            'invalid_paths': sorted(name for name in set(changed) | (before.directories ^ after.directories)
                                    if not allowed(name))}


def reconstruct_candidate(baseline, artifact, *, base_commit):
    if artifact['base_commit'] != base_commit or artifact['invalid_paths']:
        raise ValueError('Candidate baseline or scope does not match')
    tree = baseline.encode()
    for name, entry in artifact['files'].items():
        old = baseline.files.get(name)
        if (hashlib.sha256(old[0]).hexdigest() if old else None) != entry['before_sha256']:
            raise ValueError('Candidate original-file hash does not match: ' + name)
        if entry['content'] is None:
            del tree['files'][name]
        else:
            tree['files'][name] = {'content': entry['content'], 'executable': entry['executable']}
    tree['directories'] = sorted((set(tree['directories']) | set(artifact['added_directories']))
                                 - set(artifact['removed_directories']))
    decoded, _ = decode_tree(tree, baseline.limits)
    for name, entry in artifact['files'].items():
        new = decoded.get(name)
        if (hashlib.sha256(new[0]).hexdigest() if new else None) != entry['after_sha256']:
            raise ValueError('Candidate content hash does not match: ' + name)
    return tree


class NoModelCalls:
    """Independent verifier session has tools/lifecycle but cannot request a model."""
    provider = 'anthropic'
    model = 'independent-verifier'

    async def aclose(self):
        pass

    async def stream(self, *args, **kwargs):
        raise RuntimeError('Independent verifier cannot call a model')
        yield  # Keep the model-client async-generator protocol.


def no_retry_client(config, *, max_tokens=None):
    return create_model_client(config, max_tokens=max_tokens, max_retries=0)


def leakage_audit(journal, private_roots, instance_id):
    """Observed tool-access audit, not a guarantee against obfuscated host access."""
    findings = []
    markers = [str(path).replace('\\', '/').casefold() for path in private_roots]
    markers += [instance_id.casefold(), 'gold.patch', 'test.patch', 'swe-bench_lite',
                'huggingface.co/datasets', 'git fetch', 'git pull']
    for record in SessionStore._read_records(Path(journal)):
        if record.get('type') != 'tool_started':
            continue
        payload = SessionStore._payload(record, Path(journal))
        content = json.dumps(payload.get('arguments', {}), ensure_ascii=False).replace('\\\\', '/').casefold()
        matches = [marker for marker in markers if marker in content]
        if matches:
            findings.append({'tool_call_id': payload.get('tool_call_id'), 'tool': payload.get('name'),
                             'reason': 'evaluator_artifact_or_future_source_access', 'matches': matches})
    return {'valid': not findings, 'findings': findings,
            'scope': 'visible tool arguments; host reads/network remain partial and obfuscation is not exhaustively detected'}


class NativeTaskAdapter:
    def __init__(self, manifest, spec, row, cache, output, *, sandbox='workspace-write'):
        self.manifest, self.spec, self.row = manifest, spec, row
        self.cache, self.output = Path(cache).resolve(), Path(output).resolve()
        self.sandbox = sandbox
        self.scope = 'sphinx' if spec['repo'] == 'sphinx-doc/sphinx' else 'src'
        self.logs = self.output / ('evaluator-' + uuid4().hex)
        self.logs.mkdir(parents=True)
        self.dependency = manifest['environments'][spec['environment']]
        self.suite = json.loads((ROOT / spec['suite']).read_text(encoding='utf-8'))

    def case(self, ordinal, limits):
        python = str(Path('../environment') / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))
        task = (self.row['problem_statement'] + '\n\nTask Python: ' + shell_command([python])
            + '\nUse this task interpreter for all Python commands and import checks. '
            + 'Run public pytest tests as ' + shell_command([python, '-m', 'pytest']) + '. '
            + 'Bare python and pytest resolve to the Harness environment, whose versions and dependencies differ. '
            + 'Check the task interpreter and import paths before changing dependency-related source code.')
        return EvalCase(id=f'native-swe-{ordinal:03d}', repo='.',
            base_commit_repository=str(self.cache / 'sources' / (self.spec['repo'].replace('/', '__') + '.git')),
            base_commit=self.spec['base_commit'], task=task,
            source_archive_sha256=self.spec.get('archive_sha256'),
            test_command=shell_command([python, '-m', 'compileall', '-q', self.scope]),
            hidden_test_command='independent frozen grader (adapter required)',
            allowed_paths=(self.scope + '/**',), forbidden_paths=('tests/**', 'testing/**', '.git/**', '.venv/**'),
            max_model_calls=limits['max_model_calls'], max_tool_calls=limits['max_tool_calls'],
            max_turn_seconds=limits['max_turn_seconds'], timeout_seconds=limits['verifier_timeout_seconds'],
            require_task_verification=limits.get('require_task_verification', False),
            require_positive_verification=limits.get('require_positive_verification', False),
            intent_routing=limits['intent_routing'])

    async def _environment(self, prepared, phase):
        requirements = ROOT / self.dependency['requirements']
        if hashlib.sha256(requirements.read_bytes()).hexdigest() != self.dependency['sha256']:
            raise ValueError('Frozen task dependencies changed')
        interpreter = self.cache / 'python' / (
            'cpython-' + self.dependency['python'] + '-windows-x86_64-none') / 'python.exe'
        if not interpreter.is_file():
            raise ValueError('Run native preflight to prepare the pinned task interpreter first')
        # Dependency files are outside source tracking and outside the native
        # write grant. Each phase owns a separate copy, with no evaluator data.
        directory = prepared.root / 'environment'
        directory.mkdir(mode=0o700)
        if os.name == 'nt':
            from forge.sandbox.windows_worker import grant_controller_access
            grant_controller_access(directory)
        python = await install_task_environment(prepared.workspace, interpreter, requirements,
            self.logs / phase, version=self.row['version'], environment_root=directory)
        if os.name == 'nt':
            # Reuse the product's scratch-only ACL compatibility hook in this
            # pinned interpreter too; no controller libraries enter its imports.
            site = directory / 'Lib/site-packages'
            (site / 'forge_sandbox_compat.py').write_bytes(
                (ROOT / 'forge/sandbox/windows_python_compat.py').read_bytes())
            (site / 'forge_sandbox_compat.pth').write_text(
                'import forge_sandbox_compat; forge_sandbox_compat.install()\n', encoding='utf-8')
        return python

    async def setup(self, prepared):
        start = monotonic()
        await self._environment(prepared, 'agent-setup')
        self.before = source_snapshot(prepared.workspace, self.scope)
        return CommandEvidence('isolated base-only task environment', 0, monotonic() - start,
                               False, '', '', 'evaluator-owned-process-tree')

    async def capture(self, prepared, outcome):
        audit = leakage_audit(outcome.journal, (self.cache, self.output, ROOT / 'experiments'), self.spec['id'])
        outcome.independent_grading = {'input_audit': audit, 'instance_id': self.spec['id'],
                                       'report_directory': str(self.logs), 'model_api_calls': 0}
        self.before.policy.workspace.assert_current()
        self.after = source_snapshot(prepared.workspace, self.scope)
        self.candidate = candidate_delta(self.before, self.after, base_commit=self.spec['base_commit'], scope=self.scope)
        artifact = self.logs / 'candidate.json'
        artifact.write_text(json.dumps(self.candidate, indent=2), encoding='utf-8')
        outcome.independent_grading['candidate'] = {'path': str(artifact),
            'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(), 'base_commit': self.spec['base_commit']}
        outcome.independent_grading['captured_changed_paths'] = list(self.candidate['files'])
        if self.candidate['invalid_paths']:
            outcome.reasons.append('Independent source snapshot found edits outside allowed scope.')
        if not audit['valid']:
            outcome.reasons.append('Invalid attempt: evaluator artifact or future-source access observed.')

    async def grade(self, case, prepared, result, outcome):
        if outcome.independent_grading is None or not outcome.independent_grading['input_audit']['valid']:
            return
        if self.candidate['invalid_paths']:
            return
        if not self.candidate['files']:
            outcome.reasons.append('Independent source snapshot diff was empty.')
            return
        # A new base + independent dependency environment. Agent test/config and
        # dependency mutations are never trusted as the verifier installation.
        with PreparedFixture(ROOT, case) as verifier:
            session = None
            try:
                python = await self._environment(verifier, 'grader-setup')
                baseline = source_snapshot(verifier.workspace, self.scope)
                baseline.apply(reconstruct_candidate(baseline, self.candidate, base_commit=case.base_commit))
                patch = self.logs / 'test.patch'
                patch.write_bytes(self.row['test_patch'].encode('utf-8'))
                await checked(['git', 'apply', '--check', str(patch)], verifier.workspace,
                              self.logs / 'test-patch-check', task_environment())
                await checked(['git', 'apply', str(patch)], verifier.workspace,
                              self.logs / 'test-patch-apply', task_environment())
                suite = verifier.workspace / 'grading-suite.json'
                suite.write_text(json.dumps(self.suite, indent=2), encoding='utf-8')
                report = verifier.workspace / 'grading-result.json'
                options = SessionOptions(verifier.workspace, sandbox=self.sandbox, approval='auto',
                    data_root=self.logs / 'verifier-session',
                    config=ForgeConfig(api_key='unused-verifier', model_id=NoModelCalls.model),
                    profile=RunProfile(name='independent-verifier', intent_routing=False, wall_seconds=900))
                session = await HarnessSession.open(options, model_client_factory=lambda *a, **kw: NoModelCalls())
                async with session:
                    public_command = shell_command([str(python), '-m', 'compileall', '-q', self.scope])
                    outcome.public_tests = await execute_command(public_command, case.timeout_seconds, session=session)
                    outcome.diff_check = await execute_command('git diff HEAD --check', 30, session=session)
                    command = shell_command([str(python), str(ROOT / 'evals/swe_lite_grader.py'),
                                             '--suite', str(suite), '--report', str(report)])
                    outcome.hidden_tests = await execute_command(command, case.timeout_seconds, session=session)
                outcome.independent_grading['cleanup'] = session.cleanup
                if session.cleanup.get('state') != 'clean':
                    verifier.retain = True
                    outcome.reasons.append('Independent verifier cleanup is unconfirmed.')
                    outcome.independent_grading['retained_workspace'] = str(verifier.workspace)
                    return
                inspect_path(report, absolute=True).assert_current()
                evidence = json.loads(report.read_text(encoding='utf-8'))
                verdict = assessment(self.suite, evidence)
                outcome.independent_grading['assessment'] = verdict
                (self.logs / 'grading-result.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
                if not verdict['accepted']:
                    outcome.reasons.append('Independent frozen test suite rejected the candidate.')
                    outcome.hidden_tests = replace(outcome.hidden_tests, exit_code=1)
            except BaseException as error:
                cleanup = session.cleanup if session is not None else getattr(error, 'cleanup_report', None)
                if cleanup is not None and cleanup.get('state') != 'clean':
                    verifier.retain = True
                    outcome.independent_grading['retained_workspace'] = str(verifier.workspace)
                    outcome.independent_grading['cleanup'] = cleanup
                raise

async def run_native_case(adapter, case, *, config=None, client=None):
    return await run_case(case, project_root=ROOT, output_dir=adapter.output, config=config,
                          client=client, model_client_factory=None if client else no_retry_client,
                          sandbox=adapter.sandbox, fixture_setup=adapter.setup,
                          fixture_capture=adapter.capture, final_grader=adapter.grade)
