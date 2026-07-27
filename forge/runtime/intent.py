'''Semantic turn decisions are produced by ``ModelIntentRouter``.

The harness intentionally keeps no keyword or regular-expression fallback for
natural-language intent. Callers that need task routing should provide an
``IntentRouter``; callers without one remain model-driven and create task state
lazily on the first workspace mutation.
'''
