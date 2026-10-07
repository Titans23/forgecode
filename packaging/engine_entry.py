"""Console product entry; stdlib validation precedes every service/worker import."""
import json
from pathlib import Path
from multiprocessing import freeze_support
import sys

def main():
    freeze_support()
    if getattr(sys,'frozen',False):
        from forge.release.runtime import verify_manifest
        root=Path(sys.executable).resolve().parents[2]
        identity=json.loads((Path(sys._MEIPASS)/'forge/release/_build_identity.json').read_bytes())
        manifest=verify_manifest(root,expected_contract=identity['contract_manifest_hash'])
        if manifest['build_id']!=identity['build_id'] or manifest['database_schema']!=identity['database_schema']:
            raise ValueError('Frozen Engine component version mismatch')
    from forge.engine.__main__ import main as engine_main
    return engine_main()

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status':'blocked','exception_type':type(error).__name__,'reason':str(error)}),file=sys.stderr)
        raise SystemExit(2)
