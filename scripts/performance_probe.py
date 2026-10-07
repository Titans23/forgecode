"""Fixed actual Journal/SQLite/artifact/React load; no native or public-model performance claim."""
import argparse
import asyncio
from dataclasses import replace
from hashlib import sha256
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from time import perf_counter
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forge.application.harness_adapter import LocalTrustedBackend
from forge.application.services import ApplicationServices
from forge.engine.methods import EngineMethods
from forge.engine.persistence import Store,new_id
from forge.engine.journal_projection import JournalProjector
from forge.engine.test_profile import scripted_profile
from forge.observability.recorder import JournalRecorder
from forge.sessions.store import SessionJournal
from forge.testing.demo import seed_demo,execute_demo,launch_engine

EVENTS=100000
SPANS=10000
OUTPUT_BYTES=100*1024*1024

def summary(values):
    ordered=sorted(values)
    return {'samples':len(values),'median_seconds':statistics.median(values),
        'p95_seconds':ordered[math.ceil(len(ordered)*.95)-1],'total_seconds':sum(values)}

def peak_memory():
    if sys.platform!='win32':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in
            ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
             'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);api=ctypes.WinDLL('psapi',use_last_error=True)
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    api.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    value=Counters();value.cb=ctypes.sizeof(value)
    if not api.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(value),value.cb):raise ctypes.WinError(ctypes.get_last_error())
    return value.PeakWorkingSetSize

async def latency(directory):
    cold,hot=[],[]
    for i in range(5):
        target=directory/('cold-'+str(i));target.mkdir()
        _,fixture=seed_demo(target)
        with (target/'engine.log').open('wb') as diagnostic:
            start=perf_counter();client=await launch_engine(target,fixture,diagnostic)
            try:
                cold.append(perf_counter()-start)
                for _ in range(20):
                    start=perf_counter();health=await client.call('system.health',{})
                    if health['active_work_items']!=0:raise ValueError('Cold fixture has active work')
                    hot.append(perf_counter()-start)
                await client.call('system.shutdown',{'client_action_id':new_id('act'),'mode':'drain'})
                if await client.drain()!=0:raise ValueError('Engine failed to drain')
            finally:
                if client.process.returncode is None:client.process.kill()
                await client.process.wait()
    return {'cold':summary(cold),'hot':summary(hot),'scope':'source Engine stdio; explicit offline profile'}

def journal(path,project):
    value=SessionJournal(path,session_id='session-'+uuid4().hex[:24],project_root=project)
    value.append('session_started',{'cwd':str(project),'model':'deterministic-load-generator','provider':'scripted','name':None,'project_key':'fixed-load'})
    return value

def load(directory):
    target=directory/'load';target.mkdir()
    params,fixture=seed_demo(target)
    turn,_=asyncio.run(execute_demo(target,fixture,params))
    if turn['outcome']!='completed':raise ValueError('Real offline Harness seed failed')
    with Store(target/'data') as store:
        profile=scripted_profile(json.loads(fixture.read_bytes()))
        service=ApplicationServices(store,profile_id='test-profile',credentials=profile.credentials,
            mode='local-trusted',backend=LocalTrustedBackend())
        methods=EngineMethods(service,profile='test')
        root=store.trace_scope(turn['turn_id'])
        observed=journal(store.data_dir/'harness/fixed-observed.jsonl',target/'project')
        baseline=journal(store.data_dir/'harness/fixed-baseline.jsonl',target/'project')
        recorder=JournalRecorder(observed,scope=root)
        scopes=[replace(root,span_id=f'{i+1:016x}',parent_span_id=None) for i in range(SPANS)]
        raw_times,metadata_times=[],[]
        attributes={'dimension':'tool_calls','model_request_id':None,'amount_decimal':'0','remaining_decimal':None}
        # The baseline has the same observation envelope and fsync contract.
        # Its preconstructed metadata isolates recorder generation/validation cost.
        for i in range(EVENTS):
            scope=scopes[i//10]
            body=store.event_body('budget.consumed',recorder.producer,baseline.sequence+1,attributes,**scope.as_dict())
            start=perf_counter();baseline.append('observation',{'event':body});raw_times.append(perf_counter()-start)
            start=perf_counter();recorder.emit('budget.consumed',attributes,scope=scope);metadata_times.append(perf_counter()-start)
            if (i+1)%10000==0:print(json.dumps({'phase':'journal','events':i+1,'fixed_events':EVENTS}),flush=True)
        session_id=store.connection.execute('SELECT session_id FROM turns WHERE id=?',(turn['turn_id'],)).fetchone()[0]
        start=perf_counter();applied=JournalProjector(store).project(observed.path,session_id,trusted=True);projection_seconds=perf_counter()-start
        if applied!=EVENTS+1:raise ValueError('Fixed Journal projection lost records')
        actual=store.connection.execute("SELECT COUNT(*) FROM events WHERE source_id=?",('journal:'+observed.session_id,)).fetchone()[0]
        span_count=store.connection.execute("SELECT COUNT(*) FROM span_details WHERE trace_id=? AND span_id IN (SELECT printf('%016x',value) FROM json_each(?))",
            (root.trace_id,json.dumps(list(range(1,SPANS+1))))).fetchone()[0]
        items=[];pages=[];cursor=None;span_ids={s.span_id for s in scopes}
        while True:
            query={'scope':{'kind':'turn','id':turn['turn_id']},'limit':100}
            if cursor:query['cursor']=cursor
            start=perf_counter();page=methods.observations.spans(query);pages.append(perf_counter()-start)
            items.extend(x for x in page['items'] if x['span_id'] in span_ids)
            cursor=page['next_cursor']
            if not cursor:break
        if actual!=EVENTS+1 or span_count!=SPANS or len(items)!=SPANS:raise ValueError('Actual query count differs from fixed workload')
        list_input=directory/'actual-spans.json';list_input.write_text(json.dumps(items),encoding='utf-8')
        ui=subprocess.run(['node',str(ROOT/'scripts/ui_list_performance.mjs'),str(list_input)],cwd=ROOT,
            capture_output=True,text=True,encoding='utf-8',timeout=60)
        if ui.returncode:raise ValueError('Actual React performance runner failed: '+ui.stderr[-1000:])
        ui_result=json.loads(ui.stdout)
        content=b'F' * OUTPUT_BYTES
        start=perf_counter();artifact=store.publish_artifact(content,origin='trusted_engine',classification='diagnostic',
            profile_id='test-profile',media_type='application/octet-stream');write_seconds=perf_counter()-start
        start=perf_counter();read=store.read_artifact(artifact['id']);read_seconds=perf_counter()-start
        if read!=content:raise ValueError('Actual 100 MiB output roundtrip differs')
        baseline_summary=summary(raw_times);metadata_summary=summary(metadata_times)
        extra=(metadata_summary['median_seconds']/baseline_summary['median_seconds']-1)*100
        result={'events':EVENTS,'spans':SPANS,'output_bytes':OUTPUT_BYTES,'baseline':baseline_summary,
            'metadata':metadata_summary,'metadata_extra_percent':extra,'metadata_goal_percent':5,
            'comparison_scope':'paired fsync Journal append, preconstructed envelope versus real recorder; not end-to-end model runtime',
            'projection_seconds':projection_seconds,'projection_events_per_second':(EVENTS+1)/projection_seconds,
            'span_pages':summary(pages),'ui':ui_result,'artifact_write_seconds':write_seconds,'artifact_read_seconds':read_seconds,
            'artifact_sha256':sha256(content).hexdigest(),'peak_process_memory_bytes':peak_memory(),
            'disk_bytes':sum(p.stat().st_size for p in target.rglob('*') if p.is_file())}
        (directory/'append-samples.json').write_text(json.dumps({'baseline':raw_times,'metadata':metadata_times}),encoding='utf-8')
        return result

def verify(output):
    output=Path(output).resolve()
    if not output.is_relative_to(ROOT/'.local'):raise ValueError('Performance probe requires owned .local output')
    directory=output.parent/'performance-run';directory.mkdir(parents=True,exist_ok=False)
    report={'status':'blocked','reason':'Supported-platform, packaged startup and graphical UI performance require dedicated native acceptance',
        'eligible_for_native_pass':False,'scope':'development fixed load; real Journal, SQLite, artifact, React SSR and offline Harness',
        'host':platform.platform(),'public_model_calls':0,'checks':[]}
    report['latency']=asyncio.run(latency(directory))
    report['load']=load(directory)
    actual=report['load']
    report['checks']=[{'id':'fixed-workload-preserved','status':'pass'},
        {'id':'real-artifact-hash-roundtrip','status':'pass'},
        {'id':'react-ten-thousand-items-bounded','status':'pass' if actual['ui']['bounded'] else 'fail'},
        {'id':'metadata-median-goal','status':'pass' if actual['metadata_extra_percent']<=5 else 'blocked'},
        {'id':'supported-platform-graphical-performance','status':'blocked'}]
    if any(c['status']=='fail' for c in report['checks']):report.update(status='fail',reason='Actual fixed-load behavior failed')
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:report=verify(args.output)
    except (OSError,ValueError,RuntimeError,KeyError,subprocess.TimeoutExpired) as error:report={'status':'fail','reason':str(error),'checks':[]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False));return 0 if report['status']=='pass' else 2 if report['status']=='blocked' else 1
if __name__=='__main__':raise SystemExit(main())
