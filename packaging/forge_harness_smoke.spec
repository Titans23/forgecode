# F01 onedir compatibility probe; the product Engine spec is added by F27.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent
a = Analysis([str(root / 'packaging' / 'harness_smoke.py')],
             pathex=[str(root)],
             datas=collect_data_files('forge'), hiddenimports=[],
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='forge-harness-smoke',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='forge-harness-smoke')
