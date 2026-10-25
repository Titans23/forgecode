"""Fetch and preflight the frozen public, Windows-adapted SWE-bench Lite subset.

This evaluator-only entry point never creates a model client. Original dataset
rows, reference patches and logs stay below the ignored cache directory.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.runner import initialize_workspace_git
from evals.swe_lite_environment import checked, command, task_environment


def digest(data):
    return hashlib.sha256(data).hexdigest()


def row_digest(row):
    return digest(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())


def fetch_dataset(manifest, cache):
    import pyarrow.parquet as parquet

    source = manifest['dataset']
    path = cache / 'test.parquet'
    if not path.exists():
        url = f"https://huggingface.co/datasets/{source['id']}/resolve/{source['revision']}/{source['file']}"
        with urlopen(url, timeout=60) as response:
            data = response.read()
        if digest(data) != source['sha256']:
            raise ValueError('Official dataset content does not match the frozen digest')
        path.write_bytes(data)
    if digest(path.read_bytes()) != source['sha256']:
        raise ValueError('Cached dataset content changed')
    return {row['instance_id']: row for row in parquet.read_table(path).to_pylist()}


async def prepare_case(manifest, spec, row, cache):
    if row_digest(row) != spec['row_sha256']:
        raise ValueError('Task row differs from the frozen official row')
    if row['base_commit'] != spec['base_commit'] or row['repo'] != spec['repo']:
        raise ValueError('Task source identity changed')
    run = cache / 'runs' / (spec['id'] + '-' + uuid4().hex[:12])
    run.mkdir(parents=True)
    result = {'instance_id': spec['id'], 'model_api_calls': 0, 'accepted': False,
              'boundary': 'native host process with owned tree; not strict sandbox acceptance',
              'manifest_sha256': digest(json.dumps(manifest, sort_keys=True).encode())}
    env = task_environment()
    uv = shutil.which('uv')
    git = shutil.which('git')
    if not uv or not git:
        raise RuntimeError('uv and git must be installed')
    try:
        source = cache / 'sources' / (spec['repo'].replace('/', '__') + '.git')
        source.parent.mkdir(exist_ok=True)
        if not source.exists():
            await checked([git, 'init', '--bare', str(source)], cache, run / 'git-init', env)
        present = await command([git, 'cat-file', '-e', spec['base_commit'] + '^{commit}'], source,
                                run / 'git-present', env)
        if present['exit_code']:
            await checked([git, 'fetch', '--depth=1', 'https://github.com/' + spec['repo'] + '.git',
                           spec['base_commit']], source, run / 'git-fetch', env)
        archive = run / 'base.tar'
        await checked([git, 'archive', '--format=tar', '--output=' + str(archive), spec['base_commit']],
                      source, run / 'git-archive', env)
        result['source_archive_sha256'] = digest(archive.read_bytes())
        if result['source_archive_sha256'] != spec['archive_sha256']:
            raise ValueError('Source archive differs from frozen preflight source')
        workspace = run / 'workspace'
        workspace.mkdir()
        with tarfile.open(archive) as extracted:
            extracted.extractall(workspace, filter='data')
        license_path = workspace / spec['license']['path']
        if digest(license_path.read_bytes()) != spec['license']['sha256']:
            raise ValueError('Source license identity changed')
        dependency = manifest['environments'][spec['environment']]
        interpreter_dir = cache / 'python'
        await checked([uv, '--no-config', 'python', 'install', dependency['python'], '--install-dir',
                       str(interpreter_dir), '--no-bin', '--no-registry'], cache, run / 'python-install', env)
        interpreter = interpreter_dir / ('cpython-' + dependency['python'] + '-windows-x86_64-none') / 'python.exe'
        venv = run / 'env'
        await checked([uv, '--no-config', 'venv', str(venv), '--python', str(interpreter)], cache, run / 'venv', env)
        python = venv / 'Scripts' / 'python.exe'
        env = task_environment(python, row['version'])
        requirements = ROOT / dependency['requirements']
        if digest(requirements.read_bytes()) != dependency['sha256']:
            raise ValueError('Task dependency lock changed')
        await checked([uv, '--no-config', 'pip', 'install', '--python', str(python), '--no-deps',
                       '-r', str(requirements)], cache, run / 'dependencies', env)
        await checked([uv, '--no-config', 'pip', 'install', '--python', str(python), '--no-deps',
                       '--no-build-isolation', '-e', str(workspace)], cache, run / 'editable', env)
        await checked([uv, '--no-config', 'pip', 'freeze', '--python', str(python)], cache, run / 'freeze', env)
        package = dependency['package']
        identity_code = (f'import {package},sys,os; p=os.path.realpath({package}.__file__); '
                         'assert os.path.commonpath([p,os.getcwd()])==os.getcwd(),p; '
                         'print(sys.version); print(p)')
        await checked([str(python), '-c', identity_code], workspace, run / 'identity', env)
        initialize_workspace_git(workspace)
        suite_bytes = (ROOT / spec['suite']).read_bytes()
        if digest(suite_bytes) != spec['suite_sha256']:
            raise ValueError('Frozen test mapping changed')
        suite = json.loads(suite_bytes)
        if any(suite[key] != json.loads(row[key]) for key in ('FAIL_TO_PASS', 'PASS_TO_PASS')):
            raise ValueError('Required labels differ from the official task row')
        suite_path = run / 'suite.json'
        suite_path.write_text(json.dumps(suite, indent=2), encoding='utf-8')
        # Controller-side descriptor, not a model payload or a runnable EvalCase.
        # Only task text may enter the model prompt; source paths stay private.
        projection = {'task': row['problem_statement'], 'repo': '.', 'base_commit': row['base_commit'],
                      'base_commit_repository': str(source)}
        (run / 'runner-source.json').write_text(json.dumps(projection, indent=2), encoding='utf-8')
        reports = {}
        for phase, field in (('base', 'test_patch'), ('reference', 'patch')):
            patch = run / (phase + '.patch')
            patch.write_bytes(row[field].encode('utf-8'))
            await checked([git, 'apply', '--check', str(patch)], workspace, run / (phase + '-check'), env)
            await checked([git, 'apply', str(patch)], workspace, run / (phase + '-apply'), env)
            report_path = run / (phase + '.json')
            argv = [str(python), str(ROOT / 'evals' / 'swe_lite_grader.py'), '--suite', str(suite_path),
                    '--report', str(report_path)]
            if phase == 'base':
                argv.append('--expect-base')
            result[phase] = await command(argv, workspace, run / (phase + '-tests'), env)
            reports[phase] = json.loads(report_path.read_text(encoding='utf-8'))
            result[phase]['assessment'] = reports[phase]['assessment']
        result['mapping_unchanged'] = reports['base']['mapping'] == reports['reference']['mapping']
        result['accepted'] = result['mapping_unchanged'] and all(
            reports[phase]['assessment']['accepted'] and result[phase]['exit_code'] == 0
            for phase in ('base', 'reference'))
    except Exception as error:
        result['error'] = str(error)
    finally:
        (run / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'instance_id': spec['id'], 'accepted': result['accepted'], 'run': str(run),
                      'error': result.get('error')}), flush=True)
    return result['accepted']


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('fetch', 'preflight'))
    parser.add_argument('--manifest', type=Path, default=ROOT / 'experiments' / 'native-swe-lite.json')
    parser.add_argument('--cache', type=Path, default=ROOT / '.local' / 'implementation' / 'real-repo-preflight')
    parser.add_argument('--case', action='append', default=[])
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    cache = args.cache.resolve()
    if not cache.is_relative_to(ROOT / '.local'):
        raise ValueError('Evaluator-private cache must be below this checkout .local directory')
    cache.mkdir(parents=True, exist_ok=True)
    rows = fetch_dataset(manifest, cache)
    if args.action == 'fetch':
        print(json.dumps({'dataset_revision': manifest['dataset']['revision'], 'rows': len(rows)}))
        return 0
    if sys.platform != 'win32':
        raise RuntimeError('This manifest freezes Windows native adaptation; Linux has not been preflighted')
    selected = [case for case in manifest['cases'] if not args.case or case['id'] in args.case]
    if args.case and {case['id'] for case in selected} != set(args.case):
        raise ValueError('Unknown case selection')
    results = []
    for spec in selected:
        results.append(await prepare_case(manifest, spec, rows[spec['id']], cache))
    return 0 if results and all(results) else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
