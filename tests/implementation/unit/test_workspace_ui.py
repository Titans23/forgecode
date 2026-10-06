"""Compile actual UI source and render the real React list under Node."""
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]


def test_real_ui_ime_dedup_and_ten_thousand_row_window():
    script = r'''
import {rolldown} from 'rolldown';
import {createRequire} from 'node:module';
const require=createRequire(process.cwd()+'/package.json');
async function load(path) {
  const bundle=await rolldown({input:path,platform:'node',external:['react'],transform:{jsx:'react'}});
  const result=await bundle.generate({format:'cjs'});
  const module={exports:{}};
  new Function('module','exports','require',result.output[0].code)(module,module.exports,require);
  await bundle.close();
  return module.exports;
}
const state=await load('packages/ui/src/state/messages.ts');
const {VirtualList}=await load('packages/ui/src/pages/workspace/VirtualList.tsx');
const React=require('react'), {renderToString}=require('react-dom/server');
const entries=Array.from({length:10000},(_,i)=>({sequence:i+1,kind:'assistant',text:'same text',tool_name:null,status:null}));
const html=renderToString(React.createElement(VirtualList,{items:entries,itemKey:x=>String(x.sequence),render:x=>React.createElement('span',null,x.text),label:'stress'}));
const key={key:'Enter',shiftKey:false};
console.log(JSON.stringify({
  ime:!state.submitsOnEnter(key,true)&&!state.submitsOnEnter({...key,isComposing:true},false)&&!state.submitsOnEnter({...key,keyCode:229},false),
  enter:state.submitsOnEnter(key,false)&&!state.submitsOnEnter({...key,shiftKey:true},false),
  dedup:state.mergeMessages(entries.slice(0,100),entries.slice(50,150)).length===150,
  bounded:state.mergeMessages(entries,[{...entries[0],sequence:10001}]).length===10000,
  rows:(html.match(/data-virtual-row/g)||[]).length,
  end:state.visibleRange(10000,440000-264,264).end
}));
'''
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT,
        capture_output=True, text=True, timeout=30, check=True)
    report = json.loads(result.stdout)
    assert report['ime'] and report['enter'] and report['dedup'] and report['bounded']
    assert 1 <= report['rows'] <= 20 and report['end'] == 10000
