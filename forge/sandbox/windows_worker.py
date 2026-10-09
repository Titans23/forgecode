"""One ForgeCode Windows worker across profiles, with durable unresolved-grant protection."""
import ctypes
import json
import os
from pathlib import Path
from uuid import UUID

from forge.application.models import ContractError, validate
from forge.engine.persistence import DirectoryLock, validate_data_directory
from forge.sandbox.path_policy import inspect_path


def windows_local_app_data():
    if os.name != 'nt':
        raise ContractError('Windows worker requires a native host', kind='UNSUPPORTED_PLATFORM', code=-32010)
    # Resolve the current user's known folder through the OS, never project/inherited APPDATA.
    folder_id = (ctypes.c_ubyte * 16).from_buffer_copy(UUID('F1B32785-6FBA-4FCF-9D55-7B8E7F157091').bytes_le)
    pointer = ctypes.c_void_p()
    shell = ctypes.WinDLL('shell32')
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_int32
    if shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(pointer)) != 0:
        raise ContractError('Trusted local application folder unavailable', kind='SANDBOX_UNAVAILABLE', code=-32010)
    try:
        directory = Path(ctypes.wstring_at(pointer))
    finally:
        free = ctypes.WinDLL('ole32').CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free(pointer)
    inspect_path(str(directory), absolute=True).assert_current()
    return directory


def windows_worker_root():
    directory = windows_local_app_data() / 'ForgeCode' / 'sandbox-worker-v1'
    inspect_path(str(directory), absolute=True).assert_current()
    return directory


class WindowsWorkerLease:
    def __init__(self, directory, owner, policy_hash):
        validate('owner-identity', owner)
        if owner['execution_id'] is not None:
            raise ContractError('Worker must be bound to a session')
        self.owner = dict(owner)
        self.directory = validate_data_directory(Path(directory).absolute())
        inspect_path(str(self.directory), absolute=True).assert_current()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = DirectoryLock(self.directory)
        self.marker = self.directory / 'session.json'
        self.content = json.dumps({'owner': self.owner, 'binding_hash': policy_hash}, sort_keys=True)
        try:
            if self.marker.exists():
                raise ContractError('Previous Windows grants require reconciliation; worker remains blocked', kind='CLEANUP_FAILED', code=-32010)
            with self.marker.open('x', encoding='utf-8') as file:
                file.write(self.content)
                file.flush()
                os.fsync(file.fileno())
            self.token = inspect_path(str(self.marker), absolute=True)
        except BaseException:
            self.lock.close()
            raise

    def close(self, cleanup=None):
        if self.lock.file.closed:
            return
        try:
            if cleanup and cleanup.get('owner') == self.owner and cleanup.get('state') == 'clean':
                self.token.assert_current()
                if self.marker.read_text(encoding='utf-8') == self.content:
                    self.marker.unlink()
        except (OSError, ContractError):
            pass  # Retain unresolved marker; the next worker cannot mutate grants.
        finally:
            self.lock.close()
