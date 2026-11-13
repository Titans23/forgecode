"""Image prefetch preserves the catalog and never builds or starts a task."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from scripts import prefetch_dataset_images as prefetch


def configure_catalog(tmp_path, monkeypatch, names):
    cache = tmp_path / 'home'
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: cache))
    tasks = []
    for name in names:
        digest = name + '-frozen'
        task = cache / '.cache/harbor/tasks/packages/example' / name / digest
        task.mkdir(parents=True)
        (task / 'task.toml').write_text(
            '[environment]\ndocker_image = "ghcr.io/example/' + name + ':pinned"\n', encoding='utf-8')
        tasks.append({'org': 'example', 'name': name, 'ref': 'sha256:' + digest})
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'name': 'fixture', 'version': 'frozen-v1', 'task_ids': tasks}), encoding='utf-8')
    output = tmp_path / 'result'
    monkeypatch.setattr(sys, 'argv', ['prefetch_dataset_images.py', '--catalog', str(catalog),
                                    '--output', str(output), '--concurrency', '1'])
    return catalog, output


def test_prefetch_uses_cached_image_and_only_pulls_missing_official_image(tmp_path, monkeypatch):
    catalog, output = configure_catalog(tmp_path, monkeypatch, ['cached', 'missing'])
    original = catalog.read_bytes()
    available = {'ghcr.io/example/cached:pinned'}
    pulls = []

    def docker(argv, **kwargs):
        if argv[:3] == ['docker', 'image', 'inspect']:
            image = argv[3]
            info = [{'Id': 'sha256:fixture', 'RepoDigests': [image + '@sha256:fixture'],
                     'Size': 123, 'Os': 'linux', 'Architecture': 'amd64'}]
            return SimpleNamespace(returncode=0 if image in available else 1, stdout=json.dumps(info))
        assert argv[:2] == ['docker', 'pull'], 'Prefetch must not build images or run containers'
        pulls.append(argv[2])
        available.add(argv[2])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(prefetch.subprocess, 'run', docker)
    assert prefetch.main() == 0
    report = json.loads((output / 'status.json').read_text())
    assert pulls == ['ghcr.io/example/missing:pinned']
    assert (report['ready'], report['failed'], report['initially_cached']) == (2, 0, 1)
    assert report['dataset'] == 'fixture' and report['version'] == 'frozen-v1'
    assert [row['initially_cached'] for row in report['records']] == [True, False]
    assert catalog.read_bytes() == original


def test_prefetch_reports_exhausted_pull_without_changing_image_or_task(tmp_path, monkeypatch):
    _, output = configure_catalog(tmp_path, monkeypatch, ['unavailable'])
    pulls = []

    def docker(argv, **kwargs):
        if argv[:3] == ['docker', 'image', 'inspect']:
            return SimpleNamespace(returncode=1)
        assert argv[:2] == ['docker', 'pull']
        pulls.append(argv[2])
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(prefetch.subprocess, 'run', docker)
    monkeypatch.setattr(prefetch.time, 'sleep', lambda _: None)
    assert prefetch.main() == 1
    report = json.loads((output / 'status.json').read_text())
    assert pulls == ['ghcr.io/example/unavailable:pinned'] * 3
    assert (report['ready'], report['failed']) == (0, 1)
    assert report['records'][0]['state'] == 'failed'
    assert report['records'][0]['attempts'] == 3 and report['finished_at']
