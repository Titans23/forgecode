"""Compatibility imports for the released application protocol module."""
from forge.protocol import (ContractError, ErrorKind, MAX_FRAME_BYTES, MAX_DEPTH, MAX_SAFE_INTEGER,
    METHODS, BRIDGE_METHODS, EVENTS, canonical_hash, strict_loads, validate, validate_event, validate_request)

# Kept for callers that inspect the generated schema catalogue.
from forge.protocol import (_BUNDLE, _SCHEMAS, _REGISTRY, _VALIDATORS, _check_tree,
                            _compiled_schema, _absolute_references)
