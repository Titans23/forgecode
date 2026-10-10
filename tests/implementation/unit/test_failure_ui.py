"""Render actual React failure details; injected notes remain escaped text."""
import json
from pathlib import Path
import subprocess


def test_failure_history_provenance_and_rule_authority_render_as_text():
    script=r'''
import {rolldown} from 'rolldown';import{createRequire}from'node:module';
const require=createRequire(process.cwd()+'/package.json');
const build=await rolldown({input:'packages/ui/src/pages/failures/Failures.tsx',platform:'node',external:['react'],transform:{jsx:'react'}});
const output=await build.generate({format:'cjs'}),module={exports:{}};
new Function('module','exports','require',output.output[0].code)(module,module.exports,require);await build.close();
const React=require('react'),{renderToString}=require('react-dom/server');
const value={task_id:'case-fail',task_revision:'v1',origin:'imported_unverified',attempt:{execution_state:'finished',grade_result:'fail',cleanup_state:'clean'},suggestions:[{rule:'repeated_tool_error',category:'tool_usage',authority:'suggestion_only',basis_ids:['evt-a','evt-b']}],history_gap:true,annotations:[{id:'a',category:'tool_usage',author:'alice',created_at:'2026-10-07T00:00:00Z',origin:'imported_unverified',note:'<script>bad()</script>',supersedes:null,evidence_refs:['art-evidence']},{id:'b',category:'unknown',author:'bob',created_at:null,origin:'local_human_overlay',note:'Corrected',supersedes:'a',evidence_refs:[]}],evidence_scope:'run',evidence_count:2};
const html=renderToString(React.createElement(module.exports.FailureDetails,{value}));
console.log(JSON.stringify({history:html.includes('alice')&&html.includes('bob')&&html.includes('Corrected'),provenance:html.includes('imported_unverified')&&html.includes('local_human_overlay'),authority:html.includes('suggestion_only')&&html.includes('不证明根因'),missing:html.includes('缺失产物')&&html.includes('旧版本'),escaped:!html.includes('<script>')&&html.includes('&lt;script&gt;'),unknownTime:html.includes('历史时间未知')}));
'''
    result=subprocess.run(['node','--input-type=module','-e',script],cwd=Path(__file__).resolve().parents[3],capture_output=True,text=True,timeout=30,check=True)
    assert all(json.loads(result.stdout).values())
