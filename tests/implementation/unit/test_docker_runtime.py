import os

import pytest

from forge.protocol import ContractError
from forge.sandbox.docker_process import docker_binary


def executable(directory):
    directory.mkdir()
    path = directory / ('docker.exe' if os.name == 'nt' else 'docker')
    path.write_bytes(b'synthetic path selection fixture; never executed')
    path.chmod(0o755)
    return path.resolve()


def test_docker_resolution_ignores_current_directory_and_relative_path(tmp_path, monkeypatch):
    project, installed = tmp_path / 'project', tmp_path / 'installed'
    executable(project)
    expected = executable(installed)
    monkeypatch.chdir(project)
    monkeypatch.setenv('PATH', os.pathsep.join(['.', str(project), str(installed)]))
    assert docker_binary() == str(expected)


def test_docker_resolution_rejects_explicit_workspace_subdirectory(tmp_path, monkeypatch):
    project, installed = tmp_path / 'project', tmp_path / 'installed'
    project.mkdir()
    executable(project / 'bin')
    expected = executable(installed)
    monkeypatch.chdir(project)
    monkeypatch.setenv('PATH', os.pathsep.join([str(project / 'bin'), str(installed)]))
    assert docker_binary((project,)) == str(expected)
    monkeypatch.setenv('PATH', str(project / 'bin'))
    with pytest.raises(ContractError, match='outside the workspace'):
        docker_binary((project,))
