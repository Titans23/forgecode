"""Dedicated stdlib workers isolate foreign loaders without changing the Engine."""
import json
import os
from pathlib import Path
import subprocess
import sys

def foreign_environment(source=None, *, platform=None):
    environment=dict(os.environ if source is None else source)
    platform=platform or sys.platform
    no_bytecode=environment.get('PYTHONDONTWRITEBYTECODE')=='1'
    original=environment.pop('LD_LIBRARY_PATH_ORIG',None)
    environment={key:value for key,value in environment.items()
        if not key.upper().startswith(('PYTHON','NODE_','ELECTRON_','DYLD_'))
        and key.upper() not in ('LD_PRELOAD','LD_AUDIT')
        and not any(marker in key.upper() for marker in ('API_KEY','TOKEN','SECRET','PASSWORD','CREDENTIAL','PRIVATE_KEY'))}
    if platform.startswith('linux'):
        if original: environment['LD_LIBRARY_PATH']=original
        else: environment.pop('LD_LIBRARY_PATH',None)
    if no_bytecode:environment['PYTHONDONTWRITEBYTECODE']='1'
    return environment

def reset_foreign_loader():
    if sys.platform=='win32' and getattr(sys,'frozen',False):
        import ctypes
        function=ctypes.WinDLL('kernel32',use_last_error=True).SetDllDirectoryW
        function.argtypes=[ctypes.c_wchar_p];function.restype=ctypes.c_int
        if not function(None): raise ctypes.WinError(ctypes.get_last_error())
    os.environ.clear()
    os.environ.update(foreign_environment(_worker_environment))

# Capture the bootloader's original loader values before any worker cleanup.
_worker_environment=dict(os.environ)

def worker_argv(command, *, shell=False, cleanup=False, gated=True):
    role='process-worker' if gated or sys.platform.startswith('linux') else 'external-worker'
    base=[sys.executable,role] if getattr(sys,'frozen',False) else [sys.executable,'-I','-B','-m','forge.engine',role]
    return base+[json.dumps(command), '1' if shell else '0']+(['--cleanup-on-exit'] if cleanup else [])

def external_argv(command):
    return worker_argv(command,gated=False) if getattr(sys,'frozen',False) else list(command)

def external_options():
    return {'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}

def main(arguments, *, gated=True):
    if len(arguments) not in (2,3) or arguments[1] not in ('0','1') or len(arguments)==3 and arguments[2]!='--cleanup-on-exit':
        return 125
    command=json.loads(arguments[0]);shell=arguments[1]=='1'
    if (shell and not isinstance(command,str)) or (not shell and
        (not isinstance(command,list) or not command or not all(isinstance(x,str) for x in command))):
        return 125
    reset_foreign_loader()
    if sys.platform.startswith('linux'):
        from forge.tools.linux_process_worker import run
        return run(command,shell,cleanup_normal=len(arguments)==3)
    if sys.platform!='win32': return 125
    if gated and os.read(0,1)!=b'!': return 125
    if not gated:
        from forge.tools.windows_job import WindowsJob
        # The kernel closes this non-inheritable handle when the worker exits,
        # including forced termination. Do not close a job containing ourselves.
        job=WindowsJob()
        job.assign(os.getpid())
    return subprocess.call(command,shell=shell)
