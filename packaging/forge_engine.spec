# Product Engine is platform-native onedir with stdin/stdout, never windowed.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root=Path(SPECPATH).parent
a=Analysis([str(root/'packaging/engine_entry.py')],pathex=[str(root)],
    datas=collect_data_files('forge'),
    hiddenimports=collect_submodules('forge')+collect_submodules('benchmark.core')+
        ['benchmark.adapters.harbor','benchmark.catalog'],
    hookspath=[],hooksconfig={},runtime_hooks=[],excludes=['pytest'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='forge-engine',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=True,
    disable_windowed_traceback=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='forge-engine')
