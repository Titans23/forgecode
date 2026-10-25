"""Python-call compatibility only; native ACL acceptance is tested separately."""
import json
import os
import pathlib
from types import SimpleNamespace

from forge.sandbox import windows_python_compat


def test_legacy_pathlib_accessor_keeps_builtin_argument_semantics(tmp_path, monkeypatch):
    scratch = tmp_path / 'scratch'
    calls = []
    def mkdir(path, mode=0o777, *, dir_fd=None):
        calls.append((path, mode, dir_fd))
    fake = SimpleNamespace(name='nt', path=os.path, fsdecode=os.fsdecode, sep=os.sep,
        environ={'FORGE_NATIVE_SCOPE': json.dumps({'temp': str(scratch)})}, mkdir=mkdir)
    monkeypatch.setattr(windows_python_compat, 'os', fake)
    cached = SimpleNamespace(mkdir=mkdir)
    monkeypatch.setattr(pathlib, '_normal_accessor', cached, raising=False)
    windows_python_compat.install()
    original = fake.mkdir
    windows_python_compat.install()
    assert fake.mkdir is original

    class LegacyAccessor:
        mkdir = fake.mkdir  # Python 3.8 pathlib stores the built-in this way.

    LegacyAccessor().mkdir(scratch / 'nested', 0o700)
    fake.mkdir(tmp_path / 'outside', 0o700)
    fake.mkdir(scratch, 0o700)
    fake.mkdir(scratch / 'ordinary', 0o755)
    fake.mkdir(scratch / 'relative-descriptor', 0o700, dir_fd=5)
    cached.mkdir(scratch / 'cached-before-hook', 0o700)
    assert [mode for _, mode, _ in calls] == [0o777, 0o700, 0o700, 0o755, 0o700, 0o777]
    assert calls[-2][2] == 5
