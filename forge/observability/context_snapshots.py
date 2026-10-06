"""Exact-message metadata and tool pairing, without asserting summary semantics."""
from forge.observability.events import digest
from forge.context.manager import context_stats


def describe_context(messages,*,system='',tools=None):
    fingerprints=[]
    constraints=[]
    calls={}
    results={}
    for message in messages:
        fingerprint='message-'+digest(message)[:32]
        fingerprints.append(fingerprint)
        content=message.get('content',[])
        if message.get('role')=='user' and (isinstance(content,str) or any(b.get('type')=='text' for b in content if isinstance(b,dict))):
            constraints.append(fingerprint)
        for block in content if isinstance(content,list) else []:
            if not isinstance(block,dict):
                continue
            if block.get('type')=='tool_use':
                key=digest(block.get('id'))[:32]
                calls.setdefault(key,[]).append(fingerprint)
            elif block.get('type')=='tool_result':
                key=digest(block.get('tool_use_id'))[:32]
                results.setdefault(key,[]).append(fingerprint)
    pairs=[{'call_fingerprint':key,'use_message_ids':calls.get(key,[]),'result_message_ids':results.get(key,[]),
        'state':'paired' if len(calls.get(key,[]))==len(results.get(key,[]))==1 else 'invalid'} for key in sorted(calls.keys()|results.keys())]
    return {'message_ids':fingerprints[:64],'message_count':len(messages),'constraint_message_ids':constraints[:64],
        'tool_pairs':pairs[:32],'pairing_complete':all(p['state']=='paired' for p in pairs),
        'truncated':len(messages)>64 or len(pairs)>32,'estimated_tokens':context_stats(messages,system_prompt=system,tools=tools).estimated_tokens,
        'estimator_version':'forge.context.characters-div4.v1','messages_sha256':digest(messages)}


def compare_context(before,after):
    retained=[key for key in before['constraint_message_ids'] if key in after['message_ids']]
    return {'before':before,'after':after,'retained_constraint_message_ids':retained,
        'retention_check_complete':not before['truncated'] and not after['truncated'],
        'check_kind':'exact_original_message_presence'}
