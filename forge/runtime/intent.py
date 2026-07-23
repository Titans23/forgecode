'''Conservative turn-intent boundaries used by the Agent Loop controller.'''

from __future__ import annotations

import re


_CHANGE_VERBS_ZH = (
    '修复|修好|解决|修改|改|实现|实施|执行|落地|处理|新增|添加|'
    '删除|移除|创建|编写|写入|重写|重构|优化|更新|调整|调高|'
    '调低|改进|完成|替换|继续|开始'
)
_DIRECT_CHANGE_ZH = re.compile(
    rf'^\s*(?:(?:请你?|帮我|麻烦你?|你直接|直接)\s*)?'
    rf'(?:{_CHANGE_VERBS_ZH})'
)
_SCOPED_CHANGE_ZH = re.compile(
    rf'(?:帮我|请你|麻烦你|需要你|我希望你|我想让你|你直接)'
    rf'[^，。；！？\n]{{0,40}}(?:{_CHANGE_VERBS_ZH})'
)
_OBJECT_CHANGE_ZH = re.compile(
    rf'(?:把|将)\s*[^，。；！？\n]{{1,60}}(?:{_CHANGE_VERBS_ZH})'
)
_COMBINED_CHANGE_ZH = re.compile(
    rf'(?:检查|排查|分析|定位)'
    rf'[^，。；！？\n]{{0,30}}(?:并|然后|后)'
    rf'[^，。；！？\n]{{0,20}}(?:{_CHANGE_VERBS_ZH})'
)
_EXECUTE_PLAN_ZH = re.compile(
    r'(?:按|按照).{0,40}(?:方案|计划|上述|刚才).{0,20}'
    r'(?:执行|实施|实现|落地)'
)
_NEGATED_CHANGE_ZH = re.compile(
    rf'(?:不要|别|无需|不用|暂时不|先不|禁止)'
    rf'[^，。；！？\n]{{0,30}}(?:{_CHANGE_VERBS_ZH})'
)
_READ_ONLY_ZH = re.compile(
    r'(?:^\s*(?:为什么|为何|如何|怎么|(?:帮我|请你?)?'
    r'(?:解释|说明|介绍)|查看|告诉我|'
    r'列出|总结|回顾|分析)|'
    r'(?:完成|实现|修复|更新|修改|优化|开始|继续)(?:了)?'
    r'(?:吗|没有|了吗|没|呢)|'
    r'(?:方案|计划|建议)(?:是什么|有哪些|怎么样|呢|吗)|'
    r'(?:优化|修改).{0,12}(?:方案|计划|建议)|'
    r'^\s*继续(?:解释|介绍|说明|分析|查看|讨论|回答)|'
    r'(?:更新|介绍|查看|告诉我).{0,12}(?:进度|状态|情况)|'
    r'(?:给出|制定|写|编写).{0,20}(?:方案|计划|建议|plan)|'
    r'我再决定|先进行规划)'
)
_CLAUSE_SPLIT_ZH = re.compile(
    r'[，,。；;！!？?\n]+|(?:然后|接着|随后|并(?:且)?)'
)

_DIRECT_CHANGE_EN = re.compile(
    r'^\s*'
    r'(?:(?:please|kindly)\s+)?'
    r'(?:fix|implement|modify|update|add|remove|delete|create|write|'
    r'refactor|optimize|change|resolve|rewrite|execute|apply|continue|'
    r'start)\b',
    re.IGNORECASE,
)
_REQUESTED_CHANGE_EN = re.compile(
    r'\b(?:'
    r'(?:can|could|would)\s+you\s+(?:please\s+)?|'
    r'help\s+me\s+|'
    r'i\s+need\s+you\s+to\s+'
    r')'
    r'(?:fix|implement|modify|update|add|remove|delete|create|write|'
    r'refactor|optimize|change|resolve|rewrite|execute|apply|continue|'
    r'start)\b',
    re.IGNORECASE,
)
_COMBINED_CHANGE_EN = re.compile(
    r'(?:inspect|review|investigate|analyze|find)'
    r'.{0,40}\b(?:and|then)\b.{0,30}'
    r'(?:fix|modify|change|resolve|implement|rewrite)\b',
    re.IGNORECASE,
)
_NEGATED_CHANGE_EN = re.compile(
    r'(?:do\s+not|don.t|without|no\s+need\s+to|must\s+not)'
    r'.{0,40}'
    r'(?:fix|modify|change|write|implement|update|edit|create|apply)',
    re.IGNORECASE,
)
_READ_ONLY_EN = re.compile(
    r'(?:^\s*(?:why|how|what|explain|describe|tell\s+me|show\s+me|'
    r'list|summarize|review|analyze|inspect)\b|'
    r'\bupdate\s+me\b|'
    r'\b(?:status|progress)\b|'
    r'\b(?:write|create|draft|give|provide)\b.{0,30}'
    r'\b(?:plan|proposal|suggestion|explanation)\b)',
    re.IGNORECASE,
)
_CLAUSE_SPLIT_EN = re.compile(
    r'[\n!?,;]+|\b(?:then|and\s+then|however|but)\b',
    re.IGNORECASE,
)
_TEST_EXECUTION_EN = re.compile(
    r'\b(?:run|execute)\b.{0,30}\b(?:tests?|pytest|test\s+suite)\b',
    re.IGNORECASE,
)
_TEST_EXECUTION_ZH = re.compile(
    r'(?:运行|执行|进行).{0,20}(?:测试|pytest)|'
    r'(?:详细|全面|完整).{0,8}测试'
)
_FULL_TEST_SUITE_EN = re.compile(
    r'\b(?:full|complete|entire|all)\b.{0,20}'
    r'\b(?:tests?|test\s+suite)\b|'
    r'\b(?:tests?|test\s+suite)\b.{0,20}'
    r'\b(?:full|complete|entire|all)\b',
    re.IGNORECASE,
)
_FULL_TEST_SUITE_ZH = re.compile(
    r'(?:完整|全量|全部|全面).{0,8}测试|'
    r'测试.{0,8}(?:完整|全量|全部|全面)'
)
_VERIFICATION_EN = re.compile(
    r'\b(?:run|execute)\b.{0,30}\b(?:tests?|pytest|test\s+suite|checks?)\b|'
    r'\b(?:verify|validate)\b.{0,30}\b(?:change|code|implementation|result)?',
    re.IGNORECASE,
)
_NEGATED_VERIFICATION_EN = re.compile(
    r'\b(?:do\s+not|don.t|skip|without)\b.{0,25}'
    r'\b(?:tests?|pytest|verification|verify|validation)\b',
    re.IGNORECASE,
)
_VERIFICATION_ZH = re.compile(
    r'(?:运行|执行|进行).{0,20}(?:测试|pytest|检查)|'
    r'(?:详细|全面|完整).{0,8}测试|验证.{0,20}(?:代码|修改|实现|结果)'
)
_NEGATED_VERIFICATION_ZH = re.compile(
    r'(?:不要|不用|无需|跳过|先不).{0,20}(?:测试|检查|验证)'
)


def infer_change_required(prompt: str) -> bool:
    '''Return true only for high-confidence requests to change the workspace.

    This is an execution-contract boundary, not a semantic task classifier:
    it decides whether an empty Diff may satisfy the turn, never what code the
    model should write.
    '''
    text = prompt.strip().lstrip('\ufeff')
    if not text:
        return False
    clauses = [
        clause.strip()
        for part in _CLAUSE_SPLIT_ZH.split(text)
        for clause in _CLAUSE_SPLIT_EN.split(part)
        if clause.strip()
    ]
    for clause in clauses:
        if (
            _COMBINED_CHANGE_ZH.search(clause)
            or _COMBINED_CHANGE_EN.search(clause)
            or _EXECUTE_PLAN_ZH.search(clause)
        ):
            return True
        if _READ_ONLY_ZH.search(clause) or _READ_ONLY_EN.search(clause):
            continue
        # A leading imperative defines the turn contract. Requirements often
        # quote phrases such as "do not modify files" while asking ForgeCode to
        # implement handling for them; a later quoted negation must not turn
        # that implementation request into a read-only turn.
        if _DIRECT_CHANGE_ZH.search(clause) or _DIRECT_CHANGE_EN.search(clause):
            return True
        if _NEGATED_CHANGE_ZH.search(clause):
            continue
        if _NEGATED_CHANGE_EN.search(clause):
            continue
        if any(
            pattern.search(clause) is not None
            for pattern in (
                _DIRECT_CHANGE_ZH,
                _SCOPED_CHANGE_ZH,
                _OBJECT_CHANGE_ZH,
                _REQUESTED_CHANGE_EN,
            )
        ):
            return True
    return False


def infer_explore_delegation_required(prompt: str) -> bool:
    '''Return true for large implementation tasks suited to Explore Agent.'''
    text = prompt.strip().lstrip('\ufeff')
    return bool(
        len(text) >= 700
        and infer_change_required(text)
        and infer_test_execution_required(text)
    )


def infer_verification_required(prompt: str) -> bool:
    '''Return true for explicit, non-negated requests to run verification.'''
    text = prompt.strip().lstrip('\ufeff')
    if not text:
        return False
    if _NEGATED_VERIFICATION_ZH.search(text):
        text = _NEGATED_VERIFICATION_ZH.sub('', text)
    if _NEGATED_VERIFICATION_EN.search(text):
        text = _NEGATED_VERIFICATION_EN.sub('', text)
    return bool(
        _VERIFICATION_ZH.search(text)
        or _VERIFICATION_EN.search(text)
    )


def infer_test_execution_required(prompt: str) -> bool:
    '''Return true when the user explicitly requests running tests.'''
    text = prompt.strip().lstrip('\ufeff')
    if not text:
        return False
    if _NEGATED_VERIFICATION_ZH.search(text):
        text = _NEGATED_VERIFICATION_ZH.sub('', text)
    if _NEGATED_VERIFICATION_EN.search(text):
        text = _NEGATED_VERIFICATION_EN.sub('', text)
    return bool(
        _TEST_EXECUTION_ZH.search(text)
        or _TEST_EXECUTION_EN.search(text)
    )


def infer_full_test_suite_required(prompt: str) -> bool:
    '''Return true when the user explicitly requires the complete test suite.'''
    text = prompt.strip().lstrip('\ufeff')
    if not text:
        return False
    if _NEGATED_VERIFICATION_ZH.search(text):
        text = _NEGATED_VERIFICATION_ZH.sub('', text)
    if _NEGATED_VERIFICATION_EN.search(text):
        text = _NEGATED_VERIFICATION_EN.sub('', text)
    return bool(
        _FULL_TEST_SUITE_ZH.search(text)
        or _FULL_TEST_SUITE_EN.search(text)
    )
