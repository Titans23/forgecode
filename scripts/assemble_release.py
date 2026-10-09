"""Assemble one verified platform group from actual frozen and locked assets."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from uuid import uuid4
from forge.release.runtime import digest,verify_manifest,verify_tool_bundle,tool_inventory
from forge.sandbox.capabilities import windows_supported

ROOT=Path(__file__).resolve().parents[1]
def copy(source,destination):
    source=Path(source);destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    if source.is_symlink():
        link=os.readlink(source)
        if source.resolve().is_relative_to(source.parent) and not Path(link).is_absolute():
            destination.symlink_to(link)
        else:raise ValueError('Runtime link escapes its owned directory')
    else:shutil.copy2(source,destination)

def files(root):
    for current,directories,names in os.walk(root,followlinks=False):
        for name in [*directories,*names]:
            p=Path(current)/name
            if p.is_symlink() and not p.resolve().is_relative_to(root):raise ValueError('Runtime link escaped trusted root')
        for name in names:
            p=Path(current)/name
            if p.is_file():yield p

def asset(root,p):
    a={'path':p.relative_to(root).as_posix(),'sha256':digest(p),'size_bytes':p.stat().st_size}
    if p.is_symlink():a['symlink']=os.readlink(p)
    return a

def copy_tool_bundles(root, stage, lock, target):
    groups = {}
    for bundle in lock.get('tool_bundles', []):
        if bundle['platform'] != target: continue
        verify_tool_bundle(root, bundle)
        name = bundle['name']
        destination = ('runtimes/powershell/' if name == 'powershell' else 'tools/' + name + '/') + bundle['version']
        for entry in tool_inventory(root, bundle):
            source = root / bundle['path'] / entry['path']
            copy(source, stage / destination / entry['path'])
            if source.name.casefold().startswith(('license', 'copying', 'notice', 'unlicense', 'thirdpartynotices')):
                copy(source, stage / 'licenses/native-tools' / name / entry['path'])
        groups[name] = {'root': destination, 'entry_path': destination + '/' + bundle['entry']}
    if target == 'win32-x64' and set(groups) != {'powershell', 'git', 'ripgrep'}:
        raise ValueError('Windows release requires bundled PowerShell, Git and ripgrep')
    return groups

def assemble(build_id,target,output):
    if target!=sys.platform+'-x64':raise ValueError('Native Engine assembly must run on its target platform')
    build_path=ROOT/'.local/engine-build'/build_id
    build=json.loads((build_path/'build.json').read_bytes())
    if build['build_id']!=build_id or build['platform']!=target:raise ValueError('Engine build identity mismatch')
    for p,h in build.get('source_inventory',{}).items():
        if digest(ROOT/p)!=h:raise ValueError('Frozen Engine source changed; rebuild required')
    if not build.get('source_inventory'):raise ValueError('Frozen Engine source inventory is missing; rebuild required')
    lock=json.loads((ROOT/'release-lock.json').read_bytes())
    inventory=json.loads((ROOT/'sandbox_bridge/runtime-manifest.json').read_bytes())
    for name,h in lock['lockfile_hashes'].items():
        if digest(ROOT/name)!=h:raise ValueError('Locked dependency hash differs')
    for a in inventory['files']:
        if digest(ROOT/a['path'])!=a['sha256']:raise ValueError('Actual Bridge dependency differs from inventory')
    output=Path(output).absolute()
    if not output.is_relative_to(ROOT/'.local') or output.resolve()!=output:raise ValueError('Assembly output must be the owned .local resource directory')
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.release-',dir=output.parent))
    bridge=stage/'bridge'/build_id
    engine=stage/'engine'/build_id
    (stage/'tools').mkdir()
    shutil.copytree(build['directory'],engine,symlinks=True)
    try:
        for a in inventory['files']:copy(ROOT/a['path'],bridge/a['path'])
        copy(ROOT/'packaging/verify-installed.mjs',bridge/'packaging/verify-installed.mjs')
        # Materialize the workspace alias with owned package bytes, never a dev symlink.
        shutil.copytree(bridge/'packages/contracts',bridge/'node_modules/@forgecode/contracts')
        (bridge/'entry.mjs').write_text("import './sandbox_bridge/dist/main.js';\n",encoding='utf-8')
        node_asset=next(a for a in lock['assets'] if a['name']=='node' and a['platform']==target)
        if digest(ROOT/node_asset['path'])!=node_asset['sha256']:raise ValueError('Locked Node binary differs')
        node=stage/'runtimes/node'/lock['node']['version']/Path(node_asset['path']).name
        copy(ROOT/node_asset['path'],node)
        tool_groups = copy_tool_bundles(ROOT, stage, lock, target)
        if target == 'linux-x64':
            copy(ROOT/'packaging/linux/forgecode-userns',stage/'linux/forgecode-userns')
        helpers=[]
        for a in lock['assets']:
            if a['name'] in ('srt-win','apply-seccomp','java-proxy-agent') and a['platform'] in (target,'all'):
                if digest(ROOT/a['path'])!=a['sha256']:raise ValueError('Locked native helper differs')
                p=stage/'native-helpers'/target/Path(a['path']).name;copy(ROOT/a['path'],p)
                helpers.append((a,p))
        contract=(ROOT/'forge/application/_generated_contracts.json').read_text(encoding='utf-8').encode()
        contract_path=stage/'contracts/manifest.json';contract_path.parent.mkdir();contract_path.write_bytes(contract)
        contract_hash=sha256(contract).hexdigest()
        if build['identity']['contract_manifest_hash']!=contract_hash:raise ValueError('Frozen Engine contracts differ')
        ui={}
        for key,a in json.loads((ROOT/'apps/desktop/ui-assets.json').read_bytes()).items():
            p=stage/'ui'/key.lstrip('/');copy(ROOT/'apps/desktop'/a['path'],p)
            if digest(p)!=a['sha256']:raise ValueError('UI inventory differs')
            ui[key]={'path':p.relative_to(stage).as_posix(),'sha256':digest(p)}
        (stage/'ui-assets.json').write_text(json.dumps(ui,indent=2)+'\n',encoding='utf-8',newline='\n')
        copy(ROOT/'apps/desktop/.vite/build/main.js',stage/'client/main.js')
        copy(ROOT/'apps/desktop/.vite/build/preload.js',stage/'client/preload.js')
        licenses=stage/'licenses';licenses.mkdir(exist_ok=True)
        project_license=ROOT/'LICENSE'
        if project_license.is_file():copy(project_license,licenses/'ForgeCode-LICENSE')
        else:(licenses/'ForgeCode-NOTICE.txt').write_text('No project LICENSE file was present in the source checkout. This developer preview asserts no project redistribution license; production distribution requires an owner-approved license.\n',encoding='utf-8')
        copy(ROOT/'uv.lock',stage/'sbom/uv.lock');copy(ROOT/'package-lock.json',stage/'sbom/package-lock.json')
        components=list(lock['components'])
        components.extend({key: value for key, value in bundle.items() if key not in ('path', 'inventory', 'strip_prefix', 'entry')}
                          | {'kind': 'bundled-tool', 'scope': 'application-runtime'}
                          for bundle in lock.get('tool_bundles', []) if bundle['platform'] == target)
        for d in build['distributions']:
            item={k:v for k,v in d.items() if k!='license_files'};components.append(item)
            for i,p in enumerate(d['license_files']):
                copy(p,licenses/'python'/d['name']/(str(i)+'-'+Path(p).name))
        packages=json.loads((ROOT/'package-lock.json').read_bytes())['packages']
        from build_bridge import dependency_paths
        for path in sorted(dependency_paths({'packages':packages})):
            info=json.loads((ROOT/path/'package.json').read_bytes())
            components.append({'name':info['name'],'kind':'npm','scope':'bridge-runtime','version':info['version'],
                'source':packages[path].get('resolved'),'integrity':packages[path].get('integrity'),'license':info.get('license','unknown')})
            for p in (ROOT/path).iterdir():
                if p.is_file() and p.name.casefold().startswith(('license','copying','notice')):
                    copy(p,licenses/'npm'/info['name']/p.name)
        node_license=ROOT/'.local/release-runtime'/('node-'+lock['node']['version']+'-LICENSE')
        if not node_license.exists():
            url='https://raw.githubusercontent.com/nodejs/node/v'+lock['node']['version']+'/LICENSE'
            with urllib.request.urlopen(url,timeout=30) as response:node_license.write_bytes(response.read())
        copy(node_license,licenses/'Node-LICENSE')
        # PSF and bundled CPython third-party notices from the actual build runtime.
        for p in Path(sys.base_prefix).glob('*'):
            if p.is_file() and p.name.casefold().startswith(('license','copying','notice')):copy(p,licenses/'python-runtime'/p.name)
        components.append({'name':'CPython','kind':'runtime','version':build['python'],'license':'PSF-2.0','source':lock['python']['source']})
        components.append({'name':'Node.js','kind':'runtime','version':lock['node']['version'],'license':'MIT','source':lock['node']['source']})
        (stage/'sbom/dependencies.json').write_text(json.dumps({'schema_version':'forge.release.sbom.v1','components':components},indent=2)+'\n',encoding='utf-8')
        executable=engine/('forge-engine.exe' if target=='win32-x64' else 'forge-engine')
        all_assets=[asset(stage,p) for p in sorted(files(stage))]
        lookup={a['path']:a for a in all_assets}
        manifest={'schema_version':'forge.release.manifest.v1','build_id':build_id,
            'version':json.loads((ROOT/'apps/desktop/package.json').read_bytes())['version'],'platform':target,
            'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'dirty':bool(subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT)),
            'source_inventory':build['source_inventory'],
            'source_diff_sha256':sha256(subprocess.check_output(['git','diff','HEAD','--binary'],cwd=ROOT)).hexdigest(),
            'build_host':build['host'],'native_build_baseline':'blocked' if target=='win32-x64' and (not windows_supported() or platform.release()!='10') else
                'requires Ubuntu 22.04 native build acceptance' if target=='linux-x64' else 'Windows 10 x64 build 19045',
            'protocol':'forge.engine.v1','contract_manifest_hash':contract_hash,'database_schema':build['identity']['database_schema'],
            'project_license_status':'present' if project_license.is_file() else 'blocked_missing_project_license',
            'signature':{'status':'unsigned','reason':'No production signing certificate configured'},'channel':'developer-preview',
            'trusted_download_source':'https://github.com/Titans23/forgecode/releases',
            'security':lock['security'],'node_version':lock['node']['version'],
            'engine':lookup[executable.relative_to(stage).as_posix()],'bridge':lookup[(bridge/'entry.mjs').relative_to(stage).as_posix()],
            'node':lookup[node.relative_to(stage).as_posix()],
            'tools':{name: {'root': tool['root'], 'entry': lookup[tool['entry_path']]} for name, tool in tool_groups.items()},
            'engine_dependencies':[a for a in all_assets if a['path'].startswith('engine/') and a['path']!=executable.relative_to(stage).as_posix()],
            'bridge_dependencies':[a for a in all_assets if a['path'].startswith('bridge/')],
            'native_helpers':[{**lookup[p.relative_to(stage).as_posix()],'name':a['name']} for a,p in helpers],
            'ui_assets':[a for a in all_assets if a['path'].startswith(('ui/','client/')) or a['path']=='ui-assets.json'],
            'files':all_assets,'components':components,'uninstall':{'preserve_user_data':True,'shared_srt_untouched':True}}
        # Name is component metadata; each helper's identity still equals its full inventory record.
        for a in manifest['native_helpers']:lookup[a['path']].update(name=a['name'])
        (stage/'release-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
        verify_manifest(stage)
        if output.exists():
            verify_manifest(output)
            previous=ROOT/'.local/release-resources-backups';previous.mkdir(exist_ok=True)
            output.rename(previous/(build_id+'-'+uuid4().hex))
        stage.rename(output)
        shutil.copy2(output/'release-manifest.json',ROOT/'release-manifest.json')
        return manifest
    finally:
        if stage.exists() and stage.parent==output.parent and stage.name.startswith('.release-'):shutil.rmtree(stage)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--build-id');parser.add_argument('--check',action='store_true')
    parser.add_argument('--target',choices=('win32-x64','linux-x64'),default=sys.platform+'-x64')
    parser.add_argument('--output',type=Path,default=ROOT/'.local/desktop-resources');args=parser.parse_args(argv)
    try:
        m=verify_manifest(args.output) if args.check else assemble(args.build_id,args.target,args.output)
        print(json.dumps({'status':'pass','build_id':m['build_id'],'files':len(m['files']),'channel':m['channel']}));return 0
    except Exception as error:print(json.dumps({'status':'blocked' if args.target!=sys.platform+'-x64' else 'fail','reason':str(error)}));return 2 if args.target!=sys.platform+'-x64' else 1
if __name__=='__main__':raise SystemExit(main())
