'''Tests for conservative workspace-change intent inference.'''

import pytest

from forge.runtime.intent import (
    infer_change_required,
    infer_explore_delegation_required,
    infer_full_test_suite_required,
    infer_test_changes_required,
    infer_test_execution_required,
    infer_verification_required,
)


@pytest.mark.parametrize(
    'prompt',
    [
        '当前游戏有一个 bug，帮我修复一下',
        '请修改 README.md',
        '优化一下当前的上下文管理',
        '帮我在配置文件中添加一个开关',
        '帮我解决这个 bug',
        '帮我改一下',
        '请检查并修复这个 bug',
        '按刚才的方案执行',
        '把 world.js 改成六面渲染',
        '可以，开始吧',
        'Fix the rendering bug.',
        'Improve Explore failure accounting.',
        'Enhance the session recovery flow.',
        'Refine deterministic denial behavior.',
        'Please resolve the rendering bug.',
        'Inspect and fix the rendering bug.',
        'Could you please update the CLI?',
        'Help me implement streaming output.',
        '\ufeffImplement read-only intent handling.',
        (
            'Implement read-only intent handling for prompts that say '
            '"do not modify files".'
        ),
        '实现“不要修改代码”这类只读提示词的识别逻辑',
    ],
)
def test_explicit_change_requests_require_a_workspace_diff(
    prompt: str,
) -> None:
    assert infer_change_required(prompt) is True


@pytest.mark.parametrize(
    'prompt',
    [
        '为什么会出现这个 bug？',
        '如何修复这个问题？',
        '帮我解释如何修改 README',
        '给出一个修复方案，我再决定是否执行',
        '完成了吗？',
        '优化方案是什么？',
        '修改方案是什么？',
        '优化建议有哪些？',
        '更新一下当前进度',
        '为什么你不能帮我修改文件？',
        '帮我优化这个方案，不要修改代码',
        '继续解释刚才的实现思路',
        '查看 play 目录',
        'Explain how to fix the rendering bug.',
        'Update me on the current progress.',
        'Write a plan for the refactor.',
        'Plan a refactor, but do not change files.',
    ],
)
def test_questions_and_plans_do_not_require_a_workspace_diff(
    prompt: str,
) -> None:
    assert infer_change_required(prompt) is False


@pytest.mark.parametrize(
    'prompt',
    [
        'Run focused tests and then the full test suite.',
        'Execute pytest after the implementation.',
        'Verify the code change.',
        '代码写完之后需要详细测试',
        '运行聚焦测试和完整测试套件',
        '验证修改结果',
    ],
)
def test_explicit_verification_requests_require_verify_tool(
    prompt: str,
) -> None:
    assert infer_verification_required(prompt) is True


@pytest.mark.parametrize(
    'prompt',
    [
        'Run focused tests and then the full test suite.',
        'Execute pytest after the implementation.',
        '代码写完之后需要详细测试',
        '运行聚焦测试和完整测试套件',
    ],
)
def test_explicit_test_requests_require_a_test_runner(
    prompt: str,
) -> None:
    assert infer_test_execution_required(prompt) is True


@pytest.mark.parametrize(
    ('prompt', 'expected'),
    [
        ('Add deterministic tests for denied reads.', True),
        ('Write regression test cases for the CLI.', True),
        ('添加权限拒绝测试用例。', True),
        ('Run focused tests and then the full test suite.', False),
        ('Execute the existing tests.', False),
    ],
)
def test_test_file_change_requirement_is_inferred_separately(
    prompt: str,
    expected: bool,
) -> None:
    assert infer_test_changes_required(prompt) is expected


@pytest.mark.parametrize(
    ('prompt', 'expected'),
    [
        ('Run focused tests and then the full test suite.', True),
        ('运行聚焦测试和完整测试套件', True),
        ('执行全量测试', True),
        ('Run the focused permission tests.', False),
        ('Execute pytest after the implementation.', False),
        ('Do not run the full test suite.', False),
    ],
)
def test_full_suite_requirement_is_inferred_separately(
    prompt: str,
    expected: bool,
) -> None:
    assert infer_full_test_suite_required(prompt) is expected


@pytest.mark.parametrize(
    'prompt',
    [
        'Verify the code change.',
        '验证修改结果',
        'Do not run tests.',
        '不用测试，先修改代码。',
    ],
)
def test_non_test_verification_text_does_not_require_test_runner(
    prompt: str,
) -> None:
    assert infer_test_execution_required(prompt) is False


@pytest.mark.parametrize(
    'prompt',
    [
        'Do not run tests.',
        'Skip verification for now.',
        '不用测试，先修改代码。',
        '先不要验证。',
        'Explain how the tests work.',
    ],
)
def test_negated_or_descriptive_verification_text_is_not_required(
    prompt: str,
) -> None:
    assert infer_verification_required(prompt) is False


def test_large_tested_change_routes_to_explore_agent() -> None:
    prompt = (
        'Implement a production-quality cross-file change. '
        + 'Trace the runtime and permission call paths carefully. ' * 20
        + 'Run focused tests and then the full test suite.'
    )

    assert infer_explore_delegation_required(prompt) is True


def test_improve_style_complex_change_routes_to_explore_agent() -> None:
    prompt = (
        'Improve Explore failure accounting and bounded recovery. '
        + 'Cover exact usage, limit behavior, structured partial reports, '
        'parent isolation, and backward compatibility. ' * 8
        + 'Run focused tests and then the full test suite.'
    )

    assert infer_change_required(prompt) is True
    assert infer_explore_delegation_required(prompt) is True


def test_short_or_read_only_tasks_do_not_force_explore_agent() -> None:
    assert infer_explore_delegation_required(
        'Fix the bug and run tests.'
    ) is False
    assert infer_explore_delegation_required(
        'Analyze the repository and run tests. ' * 30
    ) is False
