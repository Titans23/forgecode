"""The real source-launcher process path must present an actual Electron window."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def test_source_launcher_presents_real_electron_window(tmp_path):
    entry = tmp_path / 'window.cjs'
    entry.write_text(r"""
const {app,BrowserWindow}=require('electron');
const {writeFileSync}=require('node:fs');
const {join}=require('node:path');
app.setPath('userData',join(__dirname,'profile'));
const deadline=setTimeout(()=>app.exit(2),10000);
app.whenReady().then(async()=>{
  const window=new BrowserWindow({show:false,width:420,height:200,
    webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
  try {
    await window.loadURL('data:text/html,<title>ForgeCode startup regression</title><p>Window visibility check</p>');
    window.show();
    await new Promise(resolve=>setTimeout(resolve,200));
    writeFileSync(join(__dirname,'visibility.json'),JSON.stringify({
      visible:window.isVisible(),minimized:window.isMinimized(),
      loaded:await window.webContents.executeJavaScript('document.body.textContent.includes("Window visibility check")')
    }));
  } finally {window.destroy();clearTimeout(deadline);app.quit();}
}).catch(error=>{console.error(error);app.exit(1)});
""", encoding='utf-8')
    script = (
        'import {launchDesktop} from ' + json.dumps((ROOT / 'scripts/start_desktop.mjs').as_uri()) + ';'
        'await launchDesktop([' + json.dumps(str(entry)) + ']);'
    )
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=tmp_path,
        capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / 'visibility.json').read_text(encoding='utf-8'))
    assert report == {'visible': True, 'minimized': False, 'loaded': True}
