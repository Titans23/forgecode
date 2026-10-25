"""Keep inherited private ACLs for Python temporaries in owned Windows scratch.

The scratch root is provisioned with a private controller ACL and a unique
execution write SID. CPython's explicit 0700 DACL discards that capability.
Inherit the already-private ACL only below this root; do not alter permissions
elsewhere. This compatibility hook is not the OS security boundary.
"""
import os


def install():
    if os.name != 'nt' or getattr(os.mkdir, '_forge_native_compat', False):
        return
    import json
    raw = os.environ.get('FORGE_NATIVE_SCOPE')
    if not raw:
        return
    temporary = os.path.normcase(os.path.abspath(json.loads(raw)['temp']))
    original = os.mkdir

    def mkdir(path, mode=0o777, *, dir_fd=None):
        target = os.path.normcase(os.path.abspath(os.fsdecode(path)))
        if mode == 0o700 and dir_fd is None and target.startswith(temporary + os.sep):
            # Windows ignores other mode values and inherits the parent's ACL.
            # There is no world-write chmod and no new token capability.
            mode = 0o777
        return original(path, mode, dir_fd=dir_fd)

    mkdir._forge_native_compat = True
    os.mkdir = mkdir
