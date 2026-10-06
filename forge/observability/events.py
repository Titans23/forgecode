"""Fixed provenance and trace identities shared by real execution boundaries."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from time import monotonic_ns
from uuid import uuid4


SEMANTIC_MAPPING_VERSION = 'forge.otel.genai.v1'
SEMANTIC_SOURCE_REVISION = 'cb10b70c15c099ccab144e8316d934c9699da0fd'


def identifier(prefix):
    return prefix + '-' + str(uuid4())


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                             allow_nan=False).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Scope:
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    span_id: str = field(default_factory=lambda: uuid4().hex[:16])
    parent_span_id: str | None = None
    execution_id: str | None = None
    identities: dict = field(default_factory=dict)

    def child(self, *, execution_id=None):
        return replace(self, span_id=uuid4().hex[:16], parent_span_id=self.span_id,
                       execution_id=self.execution_id if execution_id is None else execution_id)

    def as_dict(self):
        return {**self.identities, **{key:getattr(self,key) for key in
            ('trace_id','span_id','parent_span_id','execution_id')}}


@dataclass(frozen=True)
class Active:
    recorder: object
    scope: Scope
    branch: Scope
    role: str = 'main'


active: ContextVar[Active | None] = ContextVar('forge_trusted_observation', default=None)


@contextmanager
def bind(recorder, scope, *, branch=None, role='main'):
    token = active.set(Active(recorder, scope, branch or scope, role))
    try:
        yield
    finally:
        active.reset(token)


def current():
    value=active.get()
    return value.recorder if value else None


def clock_text():
    return str(monotonic_ns())


def span_attributes(body):
    """Pinned GenAI mapping; local schema and state remain independent of OTLP."""
    kind=body['event_type']
    attributes=body['attributes']
    result={'forge.event.type':kind,'forge.schema.version':body['schema_version'],
            'forge.semantic_mapping.version':SEMANTIC_MAPPING_VERSION}
    if kind.startswith('model.request.'):
        result['gen_ai.operation.name']='chat'
        model=attributes.get('requested_model')
        if model and model!='unreported':
            result['gen_ai.request.model']=model
        if attributes.get('returned_model'):
            result['gen_ai.response.model']=attributes['returned_model']
        result['forge.usage.quality']=attributes['usage_quality']
        if attributes['usage_quality']=='actual' and attributes.get('usage'):
            for direction in ('input','output'):
                count=attributes['usage'].get(direction+'_tokens')
                if count is not None:
                    result['gen_ai.usage.'+direction+'_tokens']=count
    elif kind.startswith('tool.'):
        result.update({'gen_ai.operation.name':'execute_tool','gen_ai.tool.name':attributes['tool_name']})
    elif kind.startswith('turn.'):
        result['gen_ai.operation.name']='invoke_agent'
    return result
