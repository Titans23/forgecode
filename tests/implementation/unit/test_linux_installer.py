"""Synthetic package metadata tests the release gate, not native Ubuntu setup."""
import io
import tarfile
import subprocess

import pytest

from scripts import make_installer


def package_fixture(monkeypatch, *, omit=None, modified=False, foreign_profile=False):
    dependencies={'bubblewrap','socat','ripgrep','git','bash','apparmor','libgtk-3-0'}-{omit}
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        for name in ('postinst','prerm'):
            data=(make_installer.ROOT/'packaging/linux'/name).read_bytes()
            if modified and name=='postinst': data+=b'echo changed\n'
            member=tarfile.TarInfo('./'+name);member.size=len(data);member.mode=0o755
            archive.addfile(member,io.BytesIO(data))
    outputs={'--field':('Package: forgecode\nDepends: '+', '.join(sorted(dependencies))+'\n').encode(),
        '--ctrl-tarfile':stream.getvalue(),
        '--contents':('drwxr-xr-x root/root 0 time ./usr/lib/forgecode/forgecode\n'
                       '-rw-r--r-- '+('user/user' if foreign_profile else 'root/root')+
                       ' 100 time ./usr/lib/forgecode/resources/linux/forgecode-userns\n').encode()}
    monkeypatch.setattr(make_installer.subprocess,'check_output',lambda argv,**kwargs:outputs[argv[1]])


def test_debian_gate_accepts_complete_core_supply(monkeypatch):
    package_fixture(monkeypatch)
    assert make_installer.verify_debian_package('fixture.deb')['status']=='pass'


@pytest.mark.parametrize('missing',['bubblewrap','socat','ripgrep','git','bash','apparmor'])
def test_debian_gate_rejects_missing_core_dependency(monkeypatch,missing):
    package_fixture(monkeypatch,omit=missing)
    with pytest.raises(ValueError,match='dependencies'):
        make_installer.verify_debian_package('fixture.deb')


def test_debian_gate_rejects_changed_setup_or_unowned_profile(monkeypatch):
    package_fixture(monkeypatch,modified=True)
    with pytest.raises(ValueError,match='differs'):
        make_installer.verify_debian_package('fixture.deb')
    package_fixture(monkeypatch,foreign_profile=True)
    with pytest.raises(ValueError,match='root-owned'):
        make_installer.verify_debian_package('fixture.deb')


def test_native_setup_scripts_invalidate_source_evidence(tmp_path):
    from scripts.evidence_gate import source_fingerprint
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    script=tmp_path/'packaging/linux/postinst'
    script.parent.mkdir(parents=True)
    script.write_bytes(b'#!/bin/sh\nexit 0\n')
    before=source_fingerprint(tmp_path)
    script.write_bytes(b'#!/bin/sh\nexit 1\n')
    assert source_fingerprint(tmp_path)!=before
