"""Read-only discovery of project executables, distinct from private runtimes."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from forge.release.processes import external_argv, external_options

def core_tool(name):
    if name not in ('git', 'rg'):
        raise ValueError('Unknown core tool')
    if sys.platform == 'win32':
        from forge.sandbox.launcher import ROOT, unavailable
        from forge.release.runtime import verify_tool_bundle, verify_installed_tool
        tool_name = 'git' if name == 'git' else 'ripgrep'
        try:
            if getattr(sys, 'frozen', False) or (ROOT/'engine').is_dir():
                manifest = json.loads((ROOT/'release-manifest.json').read_bytes())
                return verify_installed_tool(ROOT, manifest, tool_name)
            lock = json.loads((ROOT/'release-lock.json').read_bytes())
            bundles = [bundle for bundle in lock.get('tool_bundles', []) if bundle['name'] == tool_name and bundle['platform'] == 'win32-x64']
            if lock.get('resolution_status') != 'resolved' or len(bundles) != 1:
                raise ValueError('Bundled core tool is unavailable')
            return verify_tool_bundle(ROOT, bundles[0])
        except (OSError, ValueError, KeyError, TypeError):
            unavailable('Bundled core tool is unavailable or invalid')
    path = Path('/usr/bin') / name
    actual = path.resolve(strict=True)
    info = actual.stat()
    if info.st_uid != 0 or info.st_mode & 0o022 or not actual.is_file():
        raise ValueError('Untrusted system core tool')
    return actual

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
