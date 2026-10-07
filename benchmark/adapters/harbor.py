"""Wrap the pinned official Harbor runner; retain legacy CLI protocols."""
from hashlib import sha256
import asyncio
import json
import os
from pathlib import Path
import sys

from benchmark.catalog import get_benchmark
from benchmark.adapters.diagnostics import doctor
from benchmark.adapters.materialize import resolve_task
from benchmark.adapters.process import run_process
from benchmark.adapters.protocol import (collect_harbor, file_bytes, grader_cache_key, normalize,
    NORMALIZATION_VERSION)
from forge.application.models import ContractError, canonical_hash, strict_loads
from forge.engine.persistence import new_id
from benchmark.core.spec import freeze_spec, validate_resolved_spec
from benchmark.harbor.snapshot import verify_frozen_source


class HarborAdapter:
    def __init__(self,benchmark,*,taskset_root=None,source_root=None):
        self.benchmark=get_benchmark(benchmark)
        if self.benchmark.status!='ready':
            raise ContractError('No existing official runner for this benchmark')
        self.taskset_root=Path(taskset_root) if taskset_root else None
        self.source_root=Path(source_root) if source_root else None
        self.taskset=strict_loads(file_bytes(self.taskset_root/'taskset.json')) if self.taskset_root else None
        self.source=strict_loads(file_bytes(self.source_root.parent/'manifest.json')) if self.source_root else None
        if self.taskset and (self.taskset['benchmark'],self.taskset['dataset'])!=(benchmark,self.benchmark.dataset):
            raise ContractError('Taskset belongs to a different public benchmark')

    def describe(self):
        return {**self.benchmark.to_dict(),'adapter_revision':'forge.harbor.v1',
            'harbor_version':'0.18.0','normalization_version':NORMALIZATION_VERSION,
            'target_platform':'official-environment','task_os':'linux',
            'feedback_protocols':['none'],'legacy_feedback_default':self.benchmark.key=='aider-polyglot',
            'retry_owner':'F19 scheduler; Harbor retries=0 and attempts=1',
            'requires':['materialized exact tasks','frozen source/uv.lock','Linux Docker daemon',
                'explicit API budget authorization','per-request spend enforcement'],
            'result_labels':['official-environment','internal Agent outcome','independent verifier reward']}

    def source_identity(self):
        return {'schema_version':'forge.harbor.source.v1','manifest':self.source}

    def validate(self,spec,values):
        issues=self.configuration_issues(spec,values)
        issues.append({'task_id':None,'kind':'policy_unverified',
            'message':'Official task network policy has not been verified against frozen sandbox policy; live execution remains blocked until capability reconciliation'})
        issues.append({'task_id':None,'kind':'live_authorization_binding_unverified',
            'message':'Official runner has not bound trusted host authorization and per-request accounting to its frozen execution plan'})
        return issues

    def configuration_issues(self,spec,values):
        """Configuration can be materialized without granting permission to execute."""
        validate_resolved_spec(spec,values)
        issues=[]
        def issue(kind,message,task=None):
            issues.append({'task_id':task,'kind':kind,'message':message})
        if spec['dataset']['name']!=self.benchmark.dataset:
            issue('protocol_incompatible','Dataset differs from the existing official runner')
        if spec['execution']['target_platform']!='official-environment':
            issue('protocol_incompatible','Linux Docker benchmark requires official-environment; native Windows/Linux labels are refused')
        if spec['protocol']['feedback']!='none':
            issue('protocol_incompatible','V4 fixed-total-budget external feedback is not yet available; legacy Aider CLI retains its own protocol')
        if spec['model_mode']!='live' or spec['model']['provider'] not in {'anthropic','openai_responses','deepseek'}:
            issue('protocol_incompatible','A scripted fixture cannot produce a public benchmark model score')
        if spec['grader']['adapter_id']!='harbor-official' or spec['grader']['revision']!='0.18.0':
            issue('protocol_incompatible','Official Harbor grader identity must be harbor-official / 0.18.0')
        if self.taskset is None or self.source is None:
            issue('runner_unavailable','Exact taskset and frozen candidate have not been materialized')
        else:
            if spec['dataset']['revision']!=self.taskset['version']:
                issue('protocol_incompatible','Dataset version differs from materialized registry')
            for name in spec['dataset']['task_ids']:
                try:
                    record=self.taskset['tasks'].get(name)
                    if not record or spec['dataset']['task_revisions'][name]!=record['content_sha256']:
                        raise ContractError('Task revision differs from materialized bytes')
                    resolve_task(self.taskset_root,self.taskset,name)
                    if record['os']!='linux':
                        raise ContractError('This adapter only supports declared Linux Docker tasks')
                except (ContractError,OSError,ValueError) as error:
                    issue('environment_setup',str(error),name)
            if values['source']!=self.source_identity() or spec['source']['commit']!=self.source['git_revision']:
                issue('protocol_incompatible','Frozen source identity differs from RunSpec')
            if spec['source']['dirty_diff_sha256']!=self.source.get('dirty_diff_sha256'):
                issue('protocol_incompatible','Frozen dirty diff identity differs from RunSpec')
            try:
                verify_frozen_source(self.source_root,self.source)
                lock=file_bytes(self.source_root/'uv.lock')
                if sha256(lock).hexdigest()!=spec['source']['dependency_lock_sha256']:
                    issue('protocol_incompatible','Dependency lock differs from frozen source')
            except (OSError,ValueError):
                issue('environment_setup','Frozen candidate or dependency lock is unavailable/changed')
        parameters,harness=values['model_parameters'],values['harness']
        if parameters['temperature'] is not None or parameters['top_p'] is not None:
            issue('protocol_incompatible','Current provider API does not apply frozen temperature/top_p; unsupported values are refused')
        if not harness['compaction_enabled'] or not harness['explore_enabled'] or harness['trusted_extensions_enabled'] or harness['max_delivery_repairs']>2:
            issue('protocol_incompatible','Supported Harness: compaction/explore enabled, trusted extensions off, at most two internal repairs')
        if not 1024<=parameters['max_output_tokens']<=32768 or not 4096<=harness['max_context_tokens']<=2000000 or harness['max_context_tokens']<=parameters['max_output_tokens']:
            issue('protocol_incompatible','Frozen model/context limits are outside the actual ForgeConfig bounds')
        network=values['network_cache']
        if network['cache_mode']!='cold':
            issue('protocol_incompatible','V4 adapter currently supports a private cold cache; shared cache must not be silently substituted')
        if values['environment']['backend_version']!='harbor-0.18.0-docker' or values['grader_environment']['backend_version']!='harbor-0.18.0-docker':
            issue('protocol_incompatible','Frozen environment must describe the actual official Harbor Docker backend')
        return issues

    def materialize(self,spec,values,work,directory,*,endpoint):
        """Build an actual JobConfig for exactly one already planned attempt."""
        issues=self.configuration_issues(spec,values)
        if issues:
            raise ContractError('Official configuration cannot be materialized: '+issues[0]['message'])
        if work['task_id'] not in spec['dataset']['task_ids']:
            raise ContractError('Attempt task is not selected in the frozen RunSpec')
        if not 0 < work['duration_seconds'] <= spec['budget']['attempt_wall_seconds']:
            raise ContractError('Attempt deadline exceeds the frozen RunSpec budget')
        from harbor.models.job.config import JobConfig
        directory=Path(directory)
        directory.mkdir(parents=True,exist_ok=False)
        record=self.taskset['tasks'][work['task_id']]
        task_path=resolve_task(self.taskset_root,self.taskset,work['task_id'])
        parameters,harness=values['model_parameters'],values['harness']
        frozen={'schema_version':'forge.harbor.harness.v1','parameters':parameters,'harness':harness,
            'plan':export_runspec(spec,values),
            'scope':{key:work[key] for key in ('trace_id','span_id','run_id','trial_id')},'attempt_id':work['business_id']}
        agent={'import_path':'benchmark.harbor.forgecode_agent:ForgeCodeHarborAgent',
            'model_name':spec['model']['requested_model'],'override_timeout_sec':work['duration_seconds'],
            'kwargs':{'source_dir':str(self.source_root.resolve()),'install_retries':1,'install_retry_delay_seconds':1,
                'max_model_calls':spec['budget']['max_model_requests_per_attempt'],
                'max_tool_calls':spec['budget']['max_tool_calls_per_attempt'],'max_turn_seconds':work['duration_seconds'],
                'frozen_configuration':json.dumps(frozen,separators=(',',':'))},
            'env':{'FORGECODE_API_KEY':'${FORGE_EVAL_API_KEY}','FORGECODE_PROVIDER':spec['model']['provider'],
                'FORGECODE_MODEL':spec['model']['requested_model'],'FORGECODE_BASE_URL':endpoint,
                'FORGECODE_MODEL_MAX_TOKENS':str(parameters['max_output_tokens']),
                'FORGECODE_CONTEXT_WINDOW':str(harness['max_context_tokens']),
                'FORGECODE_REASONING_EFFORT':parameters['reasoning_effort'] or ''}}
        config=JobConfig.model_validate({'job_name':'official-job','jobs_dir':str(directory.resolve()),
            'n_attempts':1,'n_concurrent_trials':1,'retry':{'max_retries':0},'agents':[agent],
            'tasks':[{'path':str(task_path.resolve())}],
            'environment':{'import_path':'benchmark.adapters.docker:EvaluationDockerEnvironment','delete':True,
                'kwargs':{'receipt_path':str((directory/'cleanup.jsonl').resolve())}},
            'verifier':{'disable':False,'override_timeout_sec':values['grader']['timeout_seconds']}})
        (directory/'job.json').write_text(config.model_dump_json(indent=2)+'\n',encoding='utf-8')
        (directory/'runspec.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return {'directory':directory,'job_config':config,'task_checksum':record['harbor_checksum'],
            'argv':[str(Path(sys.executable).with_name('harbor.exe')) if os.name=='nt' else str(Path(sys.executable).with_name('harbor')),
                'run','--config',str((directory/'job.json').resolve()),'--plugin',
                'benchmark.harbor.deadline_plugin:ForgeCodeDeadlinePlugin','-y']}

    async def execute(self,prepared,*,secret,wall_seconds):
        # Called only by a trusted executor after readiness and budget approval, never by Renderer.
        env={key:value for key,value in os.environ.items() if key in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','USERPROFILE','COMSPEC','PATHEXT'}}
        env.update(FORGE_EVAL_API_KEY=secret,PYTHONUTF8='1')
        return await run_process(prepared['argv'],cwd=prepared['directory'],env=env,
            output_dir=prepared['directory']/'process',timeout_seconds=wall_seconds)

    def collect(self,prepared,process,task_id):
        return collect_harbor(prepared['directory']/'official-job',task_name=task_id,
            task_checksum=prepared['task_checksum'],runner_exit=process['exit_code'])

    def grade(self,collected,spec,values,task_id):
        # The official runner already ran its verifier. Normalize its output, do not invent a grader.
        identity=grader_cache_key(grader_hash=spec['grader']['configuration']['sha256'],
            environment_hash=spec['grader']['environment']['sha256'],task_revision=spec['dataset']['task_revisions'][task_id],
            artifact_hash=collected['artifact_hash'])
        return {'cache_key':identity,**normalize(runner_exit=collected['runner_exit'],result=collected['result'],
            agent_status=collected['agent_status'])}


class HarborExecutor:
    """Default composition exposes real compatibility blockers without starting an API."""
    def __init__(self,service,*,adapter=None):
        self.service,self.adapter=service,adapter

    def validate(self,spec,values):
        issues=self.adapter.validate(spec,values) if self.adapter else [{'task_id':None,'kind':'runner_unavailable',
            'message':'Official taskset and frozen candidate must be materialized in trusted Engine configuration'}]
        if spec['execution']['target_platform']=='windows-native':
            issues.append({'task_id':None,'kind':'protocol_incompatible','message':'Linux-only public Docker benchmark cannot run as Windows native; export RunSpec'})
        return issues

    async def execute(self,work,scheduler):
        evaluations=scheduler.evaluations
        spec,values=work['spec'],freeze_spec(evaluations.store,work['spec'])[1]
        from benchmark.core.scheduler import emit
        from benchmark.adapters.docker import cleanup_state
        def finish(**facts):
            evaluations.scheduler.finish(work['id'],owner_epoch=evaluations.store.epoch,
                expected_version=work['version'],**facts)
        issues=self.validate(spec,values)
        if issues:
            finish(execution_state='blocked',cleanup_state='clean',agent_outcome='blocked',
                error_origin='runner_unavailable',reason=json.dumps(issues,ensure_ascii=False))
            return
        readiness=await asyncio.to_thread(doctor)
        if readiness['issues']:
            finish(execution_state='blocked',cleanup_state='clean',agent_outcome='blocked',
                error_origin='environment_setup',reason='; '.join(readiness['issues']))
            return
        secret=self.service.credentials.resolve(spec['model']['connection_id'])
        if not secret:
            finish(execution_state='blocked',cleanup_state='clean',agent_outcome='blocked',
                error_origin='provider_unavailable',reason='Authorized model credential unavailable')
            return
        connection=evaluations.store.connection.execute('SELECT configuration_json FROM connections WHERE id=?',
            (spec['model']['connection_id'],)).fetchone()
        task_id=evaluations.store.connection.execute('SELECT task_id FROM trials WHERE id=?',(work['trial_id'],)).fetchone()[0]
        work={**work,'task_id':task_id}
        directory=evaluations.store.data_dir/'evaluation-runners'/work['business_id']
        from benchmark.harbor.run_dataset import container_base_url
        prepared=self.adapter.materialize(spec,values,work,directory,
            endpoint=container_base_url(json.loads(connection[0])['base_url']))
        cleanup='unknown'
        try:
            process=await self.adapter.execute(prepared,secret=secret,wall_seconds=work['duration_seconds'])
            receipts=directory/'cleanup.jsonl'
            if receipts.exists():
                cleanup=cleanup_state([strict_loads(row) for row in file_bytes(receipts,limit=4194304).splitlines()])
            collected=self.adapter.collect(prepared,process,task_id)
            grade=self.adapter.grade(collected,spec,values,task_id)
            for name,content in collected['files'].items():
                evaluations.store.publish_artifact(content,origin='official-harbor',classification='private-runner',
                    attempt_id=work['business_id'])
            evaluations.store.publish_artifact(json.dumps({'grade':grade,'collection_hash':collected['collection_hash'],
                'artifact_present':collected['artifact_present'],'process':process},ensure_ascii=False).encode(),
                origin='trusted_engine',classification='metadata',attempt_id=work['business_id'])
            grade_id=None
            if grade['grading_state'] in ('graded','grader_error'):
                grade_id=new_id('grade')
                result=grade['grade_result'] if grade['grading_state']=='graded' else None
                with evaluations.store.transaction():
                    evaluations.scheduler._owned(work['id'],evaluations.store.epoch,work['version'])
                    evaluations.store.connection.execute('INSERT INTO grades VALUES(?,?,?,?,?,?,?)',
                        (grade_id,work['business_id'],spec['grader']['configuration']['sha256'],
                            collected['artifact_hash'],grade['grading_state'],grade['raw_reward_decimal'],result))
                    emit(evaluations.store,'grade.finished',{'attempt_id':work['business_id'],
                        'grader_hash':spec['grader']['configuration']['sha256'],'artifact_hash':collected['artifact_hash'],
                        'grade_state':grade['grading_state'],'grade_result':result,'raw_reward_decimal':grade['raw_reward_decimal']},
                        run_id=work['run_id'],trial_id=work['trial_id'],attempt_id=work['business_id'],
                        trace_id=work['trace_id'],span_id=work['span_id'])
            finish(execution_state=grade['execution_state'],cleanup_state=cleanup,agent_outcome=grade['agent_outcome'],
                error_origin=grade['error_origin'],reason=grade['reason'],grade_id=grade_id)
        except asyncio.CancelledError:
            finish(execution_state='cancelled',cleanup_state='unknown',agent_outcome='cancelled',
                reason='Official runner cancelled; Docker cleanup requires host reconciliation')
            raise
        except Exception as error:
            finish(execution_state='error',cleanup_state=cleanup,agent_outcome='indeterminate',
                error_origin='runner_crash',reason=type(error).__name__)


def export_runspec(spec,values):
    """Portable plan with resolved snapshots; no credentials, imported scores or local control authority."""
    validate_resolved_spec(spec,values)
    return {'schema_version':'forge.eval.plan-export.v1','spec':spec,'spec_hash':canonical_hash(spec),
        'resolved_snapshots':values,'execution_label':spec['execution']['target_platform'],
        'read_only_plan':True,'required_environment':('official Harbor Linux Docker environment' if spec['execution']['target_platform']=='official-environment'
            else spec['execution']['target_platform']+' with matching verified sandbox/toolchain snapshots')}
