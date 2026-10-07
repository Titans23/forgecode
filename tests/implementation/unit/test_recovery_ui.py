"""Render the real recovery component with validated contract facts."""
import json
from pathlib import Path
import subprocess


def test_recovery_unknown_cancel_and_history_are_not_rendered_as_success():
    script=r'''
import{rolldown}from'rolldown';import{createRequire}from'node:module';import{readFile}from'node:fs/promises';
const require=createRequire(process.cwd()+'/package.json');
const build=await rolldown({input:'packages/ui/src/pages/workspace/Recovery.tsx',platform:'node',external:['react'],transform:{jsx:'react'}});
const output=await build.generate({format:'cjs'}),module={exports:{}};
new Function('module','exports','require',output.output[0].code)(module,module.exports,require);await build.close();
const React=require('react'),{renderToString}=require('react-dom/server');
const value=JSON.parse(await readFile('contracts/v1/method-fixtures.json','utf8')).cases.find(x=>x.schema==='recovery.inspect.result'&&x.expected==='valid').value;
value.journal.unmatched_intents=['<script>untrusted()</script>'];
const html=renderToString(React.createElement(module.exports.RecoveryDetails,{report:value}));
console.log(JSON.stringify({unknown:html.includes('indeterminate')&&html.includes('unknown'),noReplay:html.includes('禁止自动重放'),ownership:html.includes('历史 PID 不用于终止进程'),legacyScope:html.includes('整个 Journal'),escaped:!html.includes('<script>')&&html.includes('&lt;script&gt;')}));
'''
    result=subprocess.run(['node','--input-type=module','-e',script],cwd=Path(__file__).resolve().parents[3],capture_output=True,text=True,timeout=30,check=True)
    assert all(json.loads(result.stdout).values())
