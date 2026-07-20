'''Tests for conservative workspace-change intent inference.'''

import pytest

from forge.runtime.intent import infer_change_required


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
        'Please resolve the rendering bug.',
        'Inspect and fix the rendering bug.',
        'Could you please update the CLI?',
        'Help me implement streaming output.',
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
