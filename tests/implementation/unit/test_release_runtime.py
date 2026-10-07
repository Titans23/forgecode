"""F27 checks real resource files and loader isolation; no native acceptance claim."""
import json
from hashlib import sha256
from pathlib import Path
import sys
import pytest

from forge.release.runtime import verify_manifest
from forge.release.processes import foreign_environment, worker_argv

def release_tree(tmp_path):
    files = {}
    for path, content in {'engine/build/forge-engine': b'engine', 'engine/build/lib.so': b'library',
        'bridge/build/entry.mjs': b'bridge', 'runtimes/node/24/node': b'node',
        'licenses/NOTICE.txt': b'license', 'ui/index.html': b'ui',
        'contracts/manifest.json': b'{}'}.items():
        target = tmp_path/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        files[path] = {'path': path, 'sha256': sha256(content).hexdigest(), 'size_bytes': len(content)}
    manifest = {'schema_version':'forge.release.manifest.v1', 'build_id':'build', 'platform':sys.platform+'-x64',
        'version':'0.1.2', 'source_commit':'a'*40, 'dirty':False, 'contract_manifest_hash':files['contracts/manifest.json']['sha256'],
        'database_schema':15, 'protocol':'forge.engine.v1', 'signature':{'status':'unsigned'}, 'channel':'developer-preview',
        'security':{'status':'blocked'}, 'engine':files['engine/build/forge-engine'],
        'node':files['runtimes/node/24/node'], 'bridge':files['bridge/build/entry.mjs'], 'node_version':'24',
        'engine_dependencies':[files['engine/build/lib.so']], 'bridge_dependencies':[files['bridge/build/entry.mjs']],
        'files':list(files.values()), 'components':[], 'native_helpers':[], 'ui_assets':[files['ui/index.html']]}
    (tmp_path/'release-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest

def test_grouped_manifest_and_tampered_dependency(tmp_path):
    m=release_tree(tmp_path)
    assert verify_manifest(tmp_path)['build_id']=='build'
    (tmp_path/'engine/build/lib.so').write_bytes(b'changed')
    with pytest.raises(ValueError, match='integrity'): verify_manifest(tmp_path)

@pytest.mark.parametrize('field,value',[('protocol','future'),('platform','darwin-x64'),('build_id','other')])
def test_component_version_mismatch(tmp_path,field,value):
    m=release_tree(tmp_path); m[field]=value
    (tmp_path/'release-manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError): verify_manifest(tmp_path)

def test_production_requires_security_and_signature(tmp_path):
    release_tree(tmp_path)
    with pytest.raises(ValueError, match='Production'): verify_manifest(tmp_path, production=True)

def test_resource_path_and_internal_symlink(tmp_path):
    m=release_tree(tmp_path)
    m['files'][0]['path']='../escape'
    (tmp_path/'release-manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError): verify_manifest(tmp_path)
    if sys.platform != 'win32':
        m=release_tree(tmp_path)
        link=tmp_path/'engine/build/alias.so'; link.symlink_to('lib.so')
        asset={'path':'engine/build/alias.so','sha256':m['engine_dependencies'][0]['sha256'],'size_bytes':7,'symlink':'lib.so'}
        m['files'].append(asset)
        (tmp_path/'release-manifest.json').write_text(json.dumps(m))
        assert verify_manifest(tmp_path)
        link.unlink(); link.symlink_to('../../../outside')
        with pytest.raises(ValueError): verify_manifest(tmp_path)

def test_foreign_environment_and_frozen_worker(monkeypatch):
    env={'PATH':'toolchain','LD_LIBRARY_PATH':'private','LD_LIBRARY_PATH_ORIG':'system',
        'PYTHONHOME':'private','NODE_OPTIONS':'--require private','API_KEY':'secret','LD_PRELOAD':'inject'}
    assert foreign_environment(env, platform='linux')=={'PATH':'toolchain','LD_LIBRARY_PATH':'system'}
    assert foreign_environment({'LD_LIBRARY_PATH':'private'},platform='linux')=={}
    assert env['LD_LIBRARY_PATH']=='private'
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    assert worker_argv(['git','--version'])==[sys.executable,'process-worker','["git", "--version"]','0']

def test_preview_metadata_cannot_forge_production_trust(tmp_path):
    m=release_tree(tmp_path)
    m.update(channel='production',signature={'status':'verified'},security={'status':'pass'})
    (tmp_path/'release-manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='Production'):verify_manifest(tmp_path,production=True)

def test_node_and_python_verify_same_actual_manifest(tmp_path):
    import subprocess,shutil
    root=Path(__file__).resolve().parents[3]
    release_tree(tmp_path)
    node=shutil.which('node')
    assert node, 'The locked development Node runtime is required'
    module=(root/'packaging/verify-installed.mjs').as_uri()
    script="import {verifyInstalled} from "+json.dumps(module)+";await verifyInstalled(process.argv[1]);console.log('verified');"
    result=subprocess.run([node,'--input-type=module','-e',script,str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    (tmp_path/'bridge/build/entry.mjs').write_bytes(b'changed')
    failed=subprocess.run([node,'--input-type=module','-e',script,str(tmp_path)],capture_output=True,text=True)
    assert failed.returncode!=0 and 'integrity' in failed.stderr

def test_existing_engine_doctor_json_entry_is_readonly():
    import subprocess
    result=subprocess.run([sys.executable,'-m','forge.engine','doctor','--json'],capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert result.returncode in (0,2),result.stderr
    report=json.loads(result.stdout)
    assert report['read_only'] is True and report['automatic_repair'] is False
    assert report['workspace_tools_executed'] is False
    assert report['status'] in ('pass','blocked')
    assert result.returncode == (0 if report['status']=='pass' else 2)

def test_actual_external_worker_preserves_no_bytecode_verification(tmp_path):
    import os,subprocess
    (tmp_path/'project_module.py').write_text('VALUE=42\n',encoding='utf-8')
    environment=dict(os.environ);environment['PYTHONDONTWRITEBYTECODE']='1'
    from forge.release.processes import worker_argv
    command=[sys.executable,'-c',"import project_module,os,json;print(json.dumps({'value':project_module.VALUE,'flag':os.environ.get('PYTHONDONTWRITEBYTECODE')}))"]
    result=subprocess.run(worker_argv(command,gated=False),cwd=tmp_path,env=environment,
        capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'value':42,'flag':'1'}
    assert not (tmp_path/'__pycache__').exists()
