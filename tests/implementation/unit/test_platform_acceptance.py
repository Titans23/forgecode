"""Controlled OS metadata classification is unit evidence, never native acceptance."""
from types import SimpleNamespace
import pytest
from forge.engine.methods import EngineMethods
from forge.sandbox import capabilities
from forge.sandbox.policy import compile_policy
from tests.implementation.unit.test_policy import policy_input


@pytest.mark.parametrize('build,product_type,architecture,expected',[
    (19045,1,'AMD64',True),(19044,1,'AMD64',False),
    (19045,3,'AMD64',False),(19045,2,'AMD64',False),(19045,1,'ARM64',False),
    (26100,3,'AMD64',False),(26100,2,'AMD64',False),
    (26100,1,'ARM64',False),(26100,1,'AMD64',True)])
def test_windows_runtime_host_checks_are_consistent(monkeypatch,build,product_type,architecture,expected):
    monkeypatch.setattr(capabilities.sys,'platform','win32')
    monkeypatch.setattr(capabilities.sys,'getwindowsversion',lambda:SimpleNamespace(major=10,build=build,product_type=product_type),raising=False)
    monkeypatch.setattr(capabilities.host_platform,'machine',lambda:architecture)
    assert capabilities.windows_supported() is expected
    assert (capabilities.unavailable_report().value['platform']=='windows-native') is expected
    report = EngineMethods.capabilities(SimpleNamespace(handlers={}, profile='desktop'), {})
    assert (report['platform'] == 'windows-native') is expected
    assert report['sandbox'] == 'unavailable'


def test_owned_control_child_does_not_silently_change_frozen_policy_hash(tmp_path):
    project,w,p=policy_input(tmp_path)
    data=tmp_path/'private-data';data.mkdir()
    frozen=compile_policy(p,w,control_roots=(data,))
    prepared=compile_policy(frozen.value,w,control_roots=(data/'native'/'turn-owned',))
    assert prepared.sha256==frozen.sha256
    assert str(data/'native'/'turn-owned') not in prepared.value['filesystem']['protected_paths']
