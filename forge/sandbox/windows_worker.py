"""Windows known-folder resolution and private directory ACL preparation."""
import ctypes
import os
from pathlib import Path
from uuid import UUID

from forge.application.models import ContractError
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


def grant_controller_access(directory):
    """Grant the current user explicit access to a fresh, owned private directory.

    Python's Windows mode 0700 uses OWNER RIGHTS, which restricted tokens cannot
    use reliably. Keep the same user boundary with an explicit inheritable SID.
    """
    if os.name != 'nt':
        raise OSError('Private directory ACL preparation requires Windows')
    import win32api
    import win32con
    import win32security
    from ntsecuritycon import FILE_ALL_ACCESS
    from forge.sandbox.path_policy import inspect_path
    token = inspect_path(str(directory), absolute=True)
    if any(directory.iterdir()):
        raise ValueError('Private directory must be fresh and empty')
    process_token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(process_token, win32security.TokenUser)[0]
        default_owner = win32security.GetTokenInformation(process_token, win32security.TokenOwner)
    finally:
        process_token.Close()
    descriptor = win32security.GetFileSecurity(str(directory), win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
    # Elevated tokens may create directories owned by their default owner group.
    if descriptor.GetSecurityDescriptorOwner() not in (sid, default_owner):
        raise ValueError('Private directory belongs to another controller')
    acl = descriptor.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE, FILE_ALL_ACCESS, sid)
    token.assert_current()
    win32security.SetNamedSecurityInfo(str(directory), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION, None, None, acl, None)
