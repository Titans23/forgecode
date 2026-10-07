import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {performance} from 'node:perf_hooks';
import {rolldown} from 'rolldown';
const require=createRequire(import.meta.url);
const items=JSON.parse(await readFile(process.argv[2],'utf8'));
if(items.length!==10000||new Set(items.map(x=>x.span_id)).size!==10000)throw Error('Actual fixed span list must contain 10000 distinct items');
const bundle=await rolldown({input:'packages/ui/src/pages/workspace/VirtualList.tsx',platform:'node',
    external:['react'],transform:{jsx:'react'},onLog(level,log,defaultHandler){if(level==='error')defaultHandler(level,log);else console.error(log.message);}});
const built=await bundle.generate({format:'cjs'});await bundle.close();
const module={exports:{}};
new Function('module','exports','require',built.output[0].code)(module,module.exports,require);
const React=require('react'),{renderToString}=require('react-dom/server');
const samples=[];let rows=0;
for(let i=0;i<20;i++){
    const start=performance.now();
    const html=renderToString(React.createElement(module.exports.VirtualList,{items,itemKey:x=>x.span_id,
        render:x=>React.createElement('span',null,x.name),label:'actual fixed span query'}));
    samples.push((performance.now()-start)/1000);
    rows=(html.match(/data-virtual-row/g)||[]).length;
    if(rows<1||rows>20)throw Error('Actual React list exceeded bounded rendering');
}
const ordered=[...samples].sort((a,b)=>a-b);
console.log(JSON.stringify({status:'pass',scope:'actual React component server rendering; graphical scroll/frame latency unverified',
    items:items.length,rendered_rows:rows,bounded:true,samples_seconds:samples,
    median_seconds:(ordered[9]+ordered[10])/2,p95_seconds:ordered[18]}));

