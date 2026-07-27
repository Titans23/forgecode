'''The runtime intentionally has no keyword-based intent classifier.'''

import forge.runtime.intent as intent


def test_semantic_intent_is_owned_by_the_model_router() -> None:
    public_callables = {
        name
        for name, value in vars(intent).items()
        if not name.startswith('_') and callable(value)
    }

    assert public_callables == set()
