'''Immutable, reconstructible model-boundary inputs (credentials excluded).'''

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True, slots=True)
class RequestSnapshot:
    payload_json: str
    sha256: str

    @classmethod
    def capture(cls, *, messages, tools, system, client):
        # Only allowlisted routing metadata: never serialize a client's __dict__,
        # authentication headers, environment, or SDK configuration.
        payload = dict(messages=messages, tools=tools, system=system,
                       model=str(getattr(client, 'model', '')),
                       adapter=type(client).__name__)
        encoded = canonical(payload)
        return cls(encoded, sha256(encoded.encode('utf-8')).hexdigest())

    @property
    def payload(self) -> dict:
        return json.loads(self.payload_json)

    def as_dict(self) -> dict:
        return {'schema_version': 1, 'boundary': 'model_client_input',
                'sha256': self.sha256, 'request': self.payload}

    @classmethod
    def restore(cls, record: dict):
        encoded = canonical(record['request'])
        digest = sha256(encoded.encode('utf-8')).hexdigest()
        if digest != record['sha256']:
            raise ValueError('Request snapshot digest mismatch')
        return cls(encoded, digest)
