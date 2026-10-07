"""Read-only discovery of project executables, distinct from private runtimes."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from forge.release.processes import external_argv, external_options

def discover_toolchains():
    tools={}
    private=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else None
    for name,candidates in [('python',('python3','python')),('git',('git',)),('node',('node',))]:
        executable=next((shutil.which(c) for c in candidates if shutil.which(c)),None)
        if not executable:
            tools[name]={'status':'blocked','reason':'Project toolchain is not installed on PATH'};continue
        path=Path(executable).resolve()
        if private and path.is_relative_to(private):
            tools[name]={'status':'blocked','reason':'Private application runtime is not a project toolchain'};continue
        try:
            result=subprocess.run(external_argv([str(path),'--version']),capture_output=True,text=True,encoding='utf-8',
                errors='replace',timeout=8,**external_options())
            tools[name]={'status':'pass' if result.returncode==0 else 'blocked','path':str(path),
                'version':(result.stdout or result.stderr).strip()[:256],'exit_code':result.returncode}
        except (OSError,subprocess.TimeoutExpired) as error:
            tools[name]={'status':'blocked','path':str(path),'reason':type(error).__name__}
    return tools
