"""Append catalog observations to the existing fsync Journal, never tool stdout."""
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import replace
from uuid import UUID
from time import monotonic
from decimal import Decimal

from forge.application.models import validate_event
from forge.observability.events import Scope, active, bind, clock_text, digest, identifier


class JournalRecorder:
    def __init__(self, journal, *, scope=None, scope_sink=None):
        self.journal=journal
        self.root=scope or Scope()
        self.scope_sink=scope_sink
        self.producer=identifier('producer')
        self.inputs={}
        self.requests={}
        self.tools={}
        self.calls={}
        self.last_tools={}
        self.versions={}
        self.environment_epochs={}
        self.verifications={}

    def emit(self, event_type, attributes, *, scope=None, origin='trusted_engine', artifact_refs=()):
        value=active.get()
        scope=scope or (value.scope if value and value.recorder is self else self.root)
        identities={name:None for name in ('workspace_id','session_id','turn_id','run_id','trial_id','attempt_id')}
        body={'schema_version':'forge.events.v1','event_id':identifier('evt'),'event_type':event_type,
            'origin':origin,'producer_id':self.producer,'producer_seq':str(self.journal.sequence+1),
            'occurred_at_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
            'monotonic_ns':clock_text(),'attributes':attributes,'artifact_refs':list(artifact_refs),
            'redaction_version':'metadata-v1',**identities,**scope.as_dict()}
        validate_event({**body,'store_seq':'0'})
        # Journal append/fsync must succeed before any following tool side effect.
        return self.journal.append('observation',{'event':body})

    @contextmanager
    def turn(self, *, nested=False):
        parent=active.get()
        scope=parent.scope.child() if nested and parent else self.root
        with bind(self,scope,role='explore' if nested else 'main'):
            if nested:
                self.emit('context.prepared',self.context_attributes(reason='explore_isolated_context'),scope=scope)
            yield

    def context_attributes(self, *, reason, before=None, after=None, messages=(), estimator='not-estimated-v1'):
        branch=active.get().branch
        version=self.versions.get(branch.span_id,0)+1
        self.versions[branch.span_id]=version
        return {'context_version':version,'reason':reason,'before_tokens':before,'after_tokens':after,
            'retained_message_ids':['message-'+digest({'index':i,'message':message})[:32] for i,message in enumerate(messages[:100])],
            'estimator_version':estimator}

    def record_request(self, kind, attributes):
        value=active.get()
        if value is None or value.recorder is not self:
            return
        invocation=attributes.get('invocation_id')
        if kind=='model_input_snapshot':
            scope=self.last_tools.get(value.branch.span_id,value.scope).child()
            self.inputs[invocation]=scope
            request=attributes['request']
            self.emit('context.prepared',{**self.context_attributes(reason='actual_model_boundary',messages=request['messages']),
                'input_sha256':attributes['sha256'],'retained_message_ids_truncated':len(request['messages'])>100},scope=scope)
        elif kind=='model_request_started':
            context=self.inputs.get(invocation,value.scope)
            scope=context.child()
            request_id='request-'+str(UUID(attributes['request_id']))
            base={'model_request_id':request_id,'invocation_id':invocation,'attempt_no':attributes['attempt_no'],
                'role':'summary' if attributes['stage']=='compaction' else value.role,
                'requested_model':attributes['requested_model'] or 'unreported','returned_model':None,'usage_quality':'unknown'}
            self.requests[attributes['request_id']]={'scope':scope,'base':base,'chunks':0,'bytes':0,'branch':value.branch.span_id}
            self.emit('model.request.started',base,scope=scope)
            remaining=attributes.get('remaining_model_calls')
            self.emit('budget.consumed',{'dimension':'model_calls','model_request_id':request_id,'amount_decimal':'1',
                'remaining_decimal':str(remaining) if remaining is not None else None},scope=scope)
        elif kind in ('model_request_chunk','model_request_finished'):
            request=self.requests.get(attributes['request_id'])
            if request is None:
                return
            if kind=='model_request_chunk':
                request['bytes']+=attributes['size_bytes']
                if request['bytes']<4096:
                    return
                self._flush_chunk(request)
                return
            self._flush_chunk(request)
            for block in attributes.get('tool_blocks',{}).values():
                if block.get('complete') and block.get('id'):
                    self.calls[(request['branch'],block['id'])]=request['scope']
            usage=attributes.get('usage')
            base={**request['base'],'usage_quality':'actual' if usage is not None else 'unknown'}
            normalized={'input_tokens':usage['input_tokens']+usage.get('cache_creation_input_tokens',0)+usage.get('cache_read_input_tokens',0),
                'output_tokens':usage['output_tokens']} if usage is not None else None
            if attributes['outcome']=='completed':
                self.emit('model.request.finished',{**base,'usage':normalized},scope=request['scope'])
            else:
                self.emit('model.request.failed',{**base,'error_kind':attributes.get('reason') or attributes['outcome'],
                    'retryable':attributes['outcome']=='retrying','usage':normalized},scope=request['scope'])
            # Retain identities for tool linkage; release all chunk/input state.
            self.requests.pop(attributes['request_id'],None)

    def _flush_chunk(self, request):
        if request['bytes']:
            self.emit('model.request.chunk',{**request['base'],'chunk_index':request['chunks'],
                'size_bytes':request['bytes']},scope=request['scope'])
            request['chunks']+=1
            request['bytes']=0

    @contextmanager
    def tool(self, call):
        value=active.get()
        parent=self.calls.get((value.branch.span_id,call.id),value.scope)
        scope=parent.child(execution_id=identifier('exec'))
        self.tools[(value.branch.span_id,call.id)]=scope
        if self.scope_sink:
            self.scope_sink(scope)
        with bind(self,scope,branch=value.branch,role=value.role):
            yield scope

    def tool_attributes(self, call):
        return {'execution_id':active.get().scope.execution_id,'tool_name':call.name,'arguments_hash':digest(call.arguments)}

    def tool_intent(self, call):
        self.emit('tool.intent',self.tool_attributes(call))

    def tool_started(self, call, *, workspace_revision=0, environment_epoch=0):
        self.emit('tool.started',self.tool_attributes(call))
        if call.name=='verify':
            self.verifications[active.get().scope.execution_id]=active.get().scope.child()
            self.emit('verification.started',self.verification_attributes(workspace_revision,environment_epoch,'pending'),
                      scope=self.verifications[active.get().scope.execution_id])

    def verification_attributes(self, revision, epoch, result, *, scope=None):
        scope=scope or active.get().scope
        epoch_id=self.environment_epochs.setdefault(epoch,identifier('epoch'))
        return {'workspace_revision':revision,'environment_epoch':epoch_id,
            'evidence_id':scope.execution_id.replace('exec-','evidence-'),'reason':'actual_verify_boundary','result':result}

    def tool_finished(self, call, result, status):
        base=self.tool_attributes(call)
        # Opaque strings never become events, grades or a second budget source.
        streams={k:result.metadata[k] for k in ('stdout','stderr') if isinstance(result.metadata.get(k),str)}
        if not streams:
            streams={'stdout':result.content}
        for stream,text in streams.items():
            size=len(text.encode('utf-8'))
            total=result.metadata.get(stream+'_bytes',size)
            if type(total) is not int or total<size:
                total=size
            self.emit('tool.output',{**base,'stream':stream,'chunk_index':0,'size_bytes':size,
                'discarded_bytes':total-size,'encoding':'utf-8'})
        exit_code=result.metadata.get('exit_code')
        self.emit('tool.finished',{**base,'result':'indeterminate' if status=='indeterminate' else
            'cancelled' if status=='cancelled' else 'success' if result.success else 'failed',
            'exit_code':exit_code if type(exit_code) is int and -(2**31)<=exit_code<2**31 else None})
        self.last_tools[active.get().branch.span_id]=active.get().scope

    def verification(self, evidence, call_id):
        value=active.get()
        scope=self.tools.get((value.branch.span_id,call_id))
        if scope is not None:
            attributes=self.verification_attributes(evidence.workspace_revision,evidence.environment_epoch,
                'passed' if evidence.success else 'failed',scope=scope)
            pending_id=attributes['evidence_id']
            try:
                native_uuid=UUID(evidence.verification_id)
                if native_uuid.version==4:
                    attributes['evidence_id']='evidence-'+str(native_uuid)
            except ValueError:
                pass
            self.emit('verification.finished',{**attributes,'native_evidence_id':evidence.verification_id[:128],'pending_evidence_id':pending_id},
                scope=self.verifications.get(scope.execution_id,scope.child()))

    def invalidate_verification(self, evidence):
        try:
            native_uuid=UUID(evidence.verification_id)
            if native_uuid.version!=4:
                return  # Legacy observations have no v1 evidence identity.
        except ValueError:
            return
        scope=active.get().scope.child()
        self.emit('verification.invalidated',{'workspace_revision':evidence.workspace_revision,
            'environment_epoch':self.environment_epochs.setdefault(evidence.environment_epoch,identifier('epoch')),
            'evidence_id':'evidence-'+str(native_uuid),'reason':'previous_turn_is_not_current_proof','result':'unknown',
            'native_evidence_id':evidence.verification_id},scope=scope)

    def completion(self, *, accepted, reason, revision, evidence_revision, repairs_remaining=0):
        self.emit('completion.accepted' if accepted else 'completion.rejected',{'reason':reason[:1024] or 'caller_acceptance',
            'workspace_revision':revision,'evidence_revision':evidence_revision,'repairs_remaining':repairs_remaining})

    def tool_requested(self, state):
        branch=active.get().branch.span_id
        request=next((item for item in reversed(list(self.requests.values())) if item['branch']==branch),None)
        self.emit('budget.consumed',{'dimension':'tool_calls','model_request_id':request['base']['model_request_id'] if request else None,
            'amount_decimal':'1','remaining_decimal':str(max(0,state.max_tool_calls-state.tool_requests)) if state.max_tool_calls is not None else None})

    def reserve_budget(self, state):
        for dimension,limit in [('model_calls',state.max_model_calls),('tool_calls',state.max_tool_calls),('wall_seconds',state.max_seconds)]:
            if limit is not None:
                amount=format(Decimal(str(limit)), 'f')
                self.emit('budget.reserved',{'dimension':dimension,'model_request_id':None,'amount_decimal':amount,'remaining_decimal':amount})

    def finish_budget(self, state, reason):
        def decimal_text(value):
            return format(Decimal(str(max(0,value))).quantize(Decimal('0.000001')),'f').rstrip('0').rstrip('.') or '0'
        elapsed=monotonic()-state.started_at
        self.emit('budget.consumed',{'dimension':'wall_seconds','model_request_id':None,'amount_decimal':decimal_text(elapsed),
            'remaining_decimal':decimal_text(state.max_seconds-elapsed) if state.max_seconds is not None else None})
        dimension={'time_budget_exhausted':'wall_seconds','model_budget_exhausted':'model_calls',
                   'tool_budget_exhausted':'tool_calls','token_budget_exhausted':'input_tokens'}.get(reason)
        if dimension:
            self.emit('budget.limit_reached',{'dimension':dimension,'model_request_id':None,'amount_decimal':'0','remaining_decimal':'0'})

    async def grade(self, operation, *, attempt_id, grader_bytes, artifact_bytes, scope=None):
        """Observe a trusted grader callback. F20 supplies the actual adapter/protocol."""
        from hashlib import sha256
        base={'attempt_id':attempt_id,'grader_hash':sha256(grader_bytes).hexdigest(),'artifact_hash':sha256(artifact_bytes).hexdigest()}
        parent=scope or active.get().scope
        grade_scope=replace(parent.child(),identities={**parent.identities,'attempt_id':attempt_id})
        try:
            result=await operation()
            attributes={**base,'raw_reward_decimal':result['raw_reward_decimal'],'grade_state':'graded','grade_result':result['grade_result']}
        except BaseException:
            self.emit('grade.finished',{**base,'raw_reward_decimal':None,'grade_state':'grader_error','grade_result':None},
                      scope=grade_scope,origin='grader_adapter')
            raise
        self.emit('grade.finished',attributes,scope=grade_scope,origin='grader_adapter')
        return result
