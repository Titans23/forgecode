"""Build the actual native frozen console Engine; never cross-compile a Python binary."""
import argparse
import ast
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
import platform
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-id',required=True)
    parser.add_argument('--target',choices=('win32-x64','linux-x64'),default=sys.platform+'-x64')
    args=parser.parse_args(argv)
    if args.target!=sys.platform+'-x64':
        print(json.dumps({'status':'blocked','reason':'PyInstaller must build on the target OS','checks':[]}));return 2
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}',args.build_id): raise ValueError('Invalid build ID')
    if args.target=='linux-x64':
        release=platform.freedesktop_os_release()
        if release.get('ID')!='ubuntu' or release.get('VERSION_ID')!='22.04':
            print(json.dumps({'status':'blocked','reason':'Linux builds require the oldest supported Ubuntu 22.04 baseline','checks':[]}));return 2
    sources=[p for base in ('forge','benchmark/core','benchmark/adapters','benchmark/harbor') for p in (ROOT/base).rglob('*')
        if p.is_file() and p.suffix in ('.py','.json','.sql','.md') and '__pycache__' not in p.parts]
    sources += [ROOT/'benchmark/__init__.py',ROOT/'benchmark/catalog.py',ROOT/'packaging/engine_entry.py',ROOT/'packaging/forge_engine.spec',ROOT/'uv.lock']
    before={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sources}
    output=ROOT/'.local/engine-build'/args.build_id
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--distpath',str(output/'dist'),
        '--workpath',str(output/'work'),str(ROOT/'packaging/forge_engine.spec')]
    subprocess.run(command,cwd=ROOT,check=True)
    if any(sha256((ROOT/p).read_bytes()).hexdigest()!=h for p,h in before.items()):raise ValueError('Engine source changed during build; rebuild before assembly')
    directory=output/'dist/forge-engine'
    contract=(ROOT/'forge/application/_generated_contracts.json').read_text(encoding='utf-8').replace('\r\n','\n').encode()
    schema=max(int(p.name.split('_')[0]) for p in (ROOT/'forge/engine/migrations').glob('*.sql'))
    identity={'build_id':args.build_id,'contract_manifest_hash':sha256(contract).hexdigest(),'database_schema':schema}
    target=directory/'_internal/forge/release/_build_identity.json';target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(identity)+'\n',encoding='utf-8',newline='\n')
    analysis=ast.literal_eval((output/'work/forge_engine/Analysis-00.toc').read_text(encoding='utf-8'))
    modules={str(entry[0]).split('.')[0] for group in analysis if isinstance(group,list) for entry in group
        if isinstance(entry,tuple) and len(entry)>2 and entry[-1]=='PYMODULE'}
    packages=metadata.packages_distributions()
    names=sorted({name for module in modules for name in packages.get(module,[])})
    distributions=[]
    for name in names:
        d=metadata.distribution(name)
        distributions.append({'name':d.metadata['Name'],'version':d.version,'kind':'python','scope':'frozen-analysis',
            'license':d.metadata.get('License-Expression') or d.metadata.get('License','unknown'),
            'license_files':[str(d.locate_file(p)) for p in (d.files or []) if any(x.lower() in ('licenses','license','copying','notice') or x.lower().startswith(('license.','notice.','copying.')) for x in p.parts)]})
    report={'status':'pass','build_id':args.build_id,'platform':args.target,'python':platform.python_version(),
        'host':platform.platform(),'console':True,'directory':str(directory),'command':command,'identity':identity,
        'distributions':distributions,'source_inventory':before,'eligible_for_native_pass':False}
    (output/'build.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'pass','directory':str(directory),'distributions':len(distributions)}));return 0
if __name__=='__main__':raise SystemExit(main())
