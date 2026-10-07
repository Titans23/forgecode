"""Render the actual React Trace and exercise its bounded metadata-only state."""
import json
from pathlib import Path
import subprocess


def test_actual_trace_tree_waterfall_bounded_lists_and_private_payload_exclusion():
    script=r'''
import {rolldown} from 'rolldown';
import {createRequire} from 'node:module';
const require=createRequire(process.cwd()+'/package.json');
async function load(path){const b=await rolldown({input:path,platform:'node',external:['react'],transform:{jsx:'react'}});const r=await b.generate({format:'cjs'});const m={exports:{}};new Function('module','exports','require',r.output[0].code)(m,m.exports,require);await b.close();return m.exports;}
const s=await load('packages/ui/src/components/trace/state.ts'),{Trace}=await load('packages/ui/src/components/trace/Trace.tsx');
const React=require('react'),{renderToString}=require('react-dom/server');
const span=(id,parent,start,end)=>({trace_id:'a'.repeat(32),span_id:id,parent_span_id:parent,name:'tool.started',state:'ok',started_at_utc:'2026-10-07T00:00:00Z',ended_at_utc:'2026-10-07T00:00:01Z',attributes:{script:'PRIVATE_SCRIPT',thinking:'HIDDEN_REASONING'},metadata:{start_monotonic_ns:String(start),end_monotonic_ns:String(end),duration_nanoseconds:String(end-start),facts:{script:'PRIVATE_SCRIPT',thinking:'HIDDEN_REASONING'}}});
const root=span('root',null,100n,200n),a=span('a','root',100n,200n),b=span('b','root',100n,200n),missing=span('missing','not-loaded',110n,120n),cycle=span('cycle','cycle',110n,120n);
const rows=s.traceRows([b,root,a,missing,cycle]),parallel=rows.filter(x=>['a','b'].includes(x.span.span_id));
const items=Array.from({length:10000},(_,i)=>span('item-'+i,'root',BigInt(i+100),BigInt(i+101)));
const html=renderToString(React.createElement(Trace,{items,selected:null,select:()=>{}}));
const merged=s.mergeSpans(items,[items[0],span('new',null,1n,2n)]);
console.log(JSON.stringify({parallel:parallel.every(x=>x.depth===1&&x.width===100),missing:rows.find(x=>x.span.span_id==='missing').parentMissing,cycle:rows.find(x=>x.span.span_id==='cycle').cycle,rows:(html.match(/data-virtual-row/g)||[]).length,bounded:merged.items.length===10000&&merged.dropped,unique:s.mergeSpans([root],[root]).items.length===1,safe:!JSON.stringify(s.safeFacts({tool_name:'verify',exit_code:0,script:'PRIVATE_SCRIPT',thinking:'HIDDEN_REASONING'})).includes('PRIVATE_SCRIPT')&&!html.includes('HIDDEN_REASONING')&&!html.includes('PRIVATE_SCRIPT'),unknown:s.milliseconds(null)==='未知'}));
'''
    result=subprocess.run(['node','--input-type=module','-e',script],cwd=Path(__file__).resolve().parents[3],capture_output=True,text=True,timeout=30,check=True)
    report=json.loads(result.stdout)
    assert all(report[k] for k in ('parallel','missing','cycle','bounded','unique','safe','unknown'))
    assert 1<=report['rows']<=20
