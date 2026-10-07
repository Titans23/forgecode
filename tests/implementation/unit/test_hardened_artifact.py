"""Parser adversarial fixtures are unit evidence, never native/packaged acceptance."""
import pytest
from scripts.hardened_smoke import SENTINEL,read_fuses,hardened_checks


@pytest.mark.parametrize('payload',[b'',b'ordinary executable',SENTINEL+b'\x02\x09'+b'0'*9,
    SENTINEL+b'\x01\x09'+b'0'*8,SENTINEL+b'\x01\x09'+b'x'*9,
    (SENTINEL+b'\x01\x09'+b'0'*9)*2])
def test_invalid_or_ambiguous_binary_wire_cannot_establish_hardening(tmp_path,payload):
    path=tmp_path/'unit-wire';path.write_bytes(payload)
    with pytest.raises(ValueError):read_fuses(path)


def test_enabled_debug_or_environment_fuse_is_a_real_refusal(tmp_path):
    path=tmp_path/'unit-wire';path.write_bytes(b'unit-only'+SENTINEL+b'\x01\x09'+bytes([49,49,49,49,49,49,48,48,49]))
    checks=hardened_checks(read_fuses(path),'win32')
    assert {c['id'] for c in checks if c['status']=='fail'}=={
        'fuse-RunAsNode','fuse-EnableNodeOptionsEnvironmentVariable','fuse-EnableNodeCliInspectArguments'}


def test_linux_does_not_claim_unsupported_embedded_asar_verification(tmp_path):
    path=tmp_path/'unit-wire';path.write_bytes(SENTINEL+b'\x01\x09'+bytes([48,49,48,48,48,49,48,48,49]))
    wire=read_fuses(path)
    assert all(c['status']=='pass' for c in hardened_checks(wire,'linux'))
    assert any(c['status']=='fail' for c in hardened_checks(wire,'win32'))
