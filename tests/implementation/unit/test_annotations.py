"""Rules report correlations and never establish root-cause authority."""
from forge.application.annotations import suggestions


def test_diagnostic_rules_return_only_evidence_backed_suggestions():
    attempt={'id':'attempt-real','execution_state':'error','cleanup_state':'unknown','error_origin':'runner_unavailable'}
    events=[{'event_id':'event-a','event_type':'tool.finished','attributes':{'tool_name':'run','result':'failed','exit_code':1}},
            {'event_id':'event-b','event_type':'tool.finished','attributes':{'tool_name':'run','result':'failed','exit_code':1}},
            {'event_id':'event-c','event_type':'verification.invalidated','attributes':{}}]
    warnings=suggestions(attempt,events)
    assert {w['rule'] for w in warnings}=={'repeated_tool_error','stale_evidence','initialization_unavailable','cleanup_unconfirmed'}
    assert all(w['authority']=='suggestion_only' and w['basis_ids'] for w in warnings)
    assert attempt=={'id':'attempt-real','execution_state':'error','cleanup_state':'unknown','error_origin':'runner_unavailable'}
    assert not suggestions({'id':'attempt-ok','execution_state':'queued','cleanup_state':'pending','error_origin':None},[])
