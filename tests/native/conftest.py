"""Native tests only: grant access to their fresh, owned temporary parent once."""
import sys
import pytest


@pytest.fixture(scope='session', autouse=True)
def owned_native_parent(tmp_path_factory):
    if sys.platform == 'win32':
        from forge.sandbox.windows_worker import grant_controller_access
        grant_controller_access(tmp_path_factory.getbasetemp())
