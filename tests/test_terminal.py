'''Tests for the Rich terminal presentation.'''

from io import StringIO
import asyncio
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.document import Document
from rich.console import Console
import forge.terminal as terminal_module

from forge.permissions.policy import PermissionRequest
from forge.runtime.state import (
    TokenUsage,
    ToolCall,
    TurnResult,
    VerificationEvidence,
)
from forge.terminal import (
    SLASH_COMMAND_COMPLETER,
    SessionOption,
    SlashCommandCompleter,
    TerminalUI,
    streaming_preview,
    token_usage_summary,
)
from forge.tools.base import ToolResult


class FakePromptSession:
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages: list[object] = []

    def prompt(self, message: object = '') -> str:
        self.messages.append(message)
        return self.response


def test_cancelled_stream_preserves_partial_output_without_running_indicators():
    from forge.terminal import StreamingResponseView
    output = StringIO()
    console = Console(file=output, width=100, color_system=None)
    with StreamingResponseView(console) as view:
        view.append_text('Partial response')
        view.start_tool(ToolCall(0, 'pending', 'run_command', {'command': 'long task'}))
        view.cancel()
    rendered = output.getvalue()
    assert 'Partial response' in rendered and 'cancelled' in rendered
    assert rendered.count('Partial response') == 1
    assert '正在运行' not in rendered and 'streaming' not in rendered
    assert '结果需核实' in rendered and 'may be incomplete' in rendered


def terminal_with_output() -> tuple[TerminalUI, StringIO]:
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=False,
        width=100,
    )
    return TerminalUI(console=console), output


def test_pending_tool_reports_received_bytes_without_claiming_execution():
    view = terminal_module.StreamingResponseView(Console(file=StringIO(), width=110))
    view.prepare_tool(0, 'write_file')
    view.append_tool_arguments(0, '中' * 1024)
    assert 'write_file · 3.0 KB · 未执行' in view.phase
    assert not view.timeline
    view.prepare_tool(1, 'git_status')
    view.append_tool_arguments(1, '{}')
    view.append_tool_arguments(0, 'a' * 1024)
    assert 'write_file · 4.0 KB · 未执行' in view.phase
    view.start_tool(ToolCall(0, 'write', 'write_file', {'path': 'output.html', 'content': 'done'}))
    assert '运行中 1' in view.phase and '排队 0' in view.phase
    view.model_started()
    view.prepare_tool(0, 'write_file')
    view.append_tool_arguments(0, 'a' * 1024)
    assert '1.0 KB' in view.phase


def test_model_wait_progress_uses_current_request_and_real_received_bytes(monkeypatch):
    now = 100.0
    monkeypatch.setattr(terminal_module, 'monotonic', lambda: now)
    output = StringIO()
    console = Console(file=output, width=160, color_system=None)
    view = terminal_module.StreamingResponseView(console)
    view.model_started()
    now += 12
    console.print(view._render())
    assert '请求 12.0s' in output.getvalue() and '12s 无新内容' in output.getvalue()
    view.append_reasoning('中' * 1024)
    view.prepare_tool(0, 'write_file')
    view.append_tool_arguments(0, 'a' * 1024)
    output.truncate(0)
    output.seek(0)
    now += 2
    console.print(view._render())
    assert '已接收 4.0 KB' in output.getvalue() and '未执行' in output.getvalue()
    assert '无新内容' not in output.getvalue()
    view.model_started()
    now += 7
    output.truncate(0)
    output.seek(0)
    console.print(view._render())
    assert '请求 7.0s' in output.getvalue() and '本轮 21.0s' in output.getvalue()
    assert '已接收 0.0 KB' in output.getvalue()


def test_partial_inventory_is_a_visible_limitation_not_a_failed_delivery():
    from forge.runtime.delivery import completion_report
    report = completion_report(status='completed', evidence=(), workspace_revision=1, environment_epoch=0,
        reasons=(), has_contract=False, workspace_tracking_complete=False)
    result = TurnResult('Created file.', TokenUsage(1, 1), changed_paths=('output.html',), completion_report=report)
    summary = terminal_module.completion_evidence_summary(result).plain
    assert 'task completed' in summary and 'output.html' in summary
    assert 'unverified' in summary and 'not_configured' in summary
    assert '仅列出已确认的文件变更' in summary and '×' not in summary


def test_modern_approval_has_only_one_call_consent_and_denial(monkeypatch):
    captured = {}
    def choose(**kwargs):
        captured.update(kwargs)
        return 'allow_once'
    monkeypatch.setattr(terminal_module, 'permission_choice', choose)
    terminal = TerminalUI(Console(file=StringIO(), force_terminal=True), prompt_session=FakePromptSession(''))
    result = terminal.select_permission(PermissionRequest('write_file', 'file.write', 'high'), remember=False)
    assert result.choice == 'allow_once'
    assert [key for key, _ in captured['options']] == ['allow_once', 'deny']


def test_permission_picker_uses_shared_presets_and_cancel_keeps_custom_settings(monkeypatch):
    from forge.permissions.presets import PERMISSION_PRESETS
    captured = {}
    def choose(**kwargs):
        captured.update(kwargs)
        return kwargs['default']
    monkeypatch.setattr(terminal_module, 'permission_choice', choose)
    terminal = TerminalUI(Console(file=StringIO(), force_terminal=True), prompt_session=FakePromptSession(''))
    assert terminal.select_permission_mode('workspace-write') == 'workspace-write'
    assert [key for key, _ in captured['options']] == [p.name for p in PERMISSION_PRESETS]
    assert '宿主权限' in captured['options'][-1][1]
    assert terminal.select_permission_mode(None) is None
    assert captured['options'][0][0] is None
    assert not any(item.completion in ('/sandbox ', '/approval ') for item in terminal_module.SLASH_COMMANDS)


def test_saved_history_limits_user_turns_and_summarizes_tools():
    terminal, output = terminal_with_output()
    messages = []
    for i in range(12):
        messages.extend([{'role': 'user', 'content': f'question-{i:02}'},
                         {'role': 'assistant', 'content': f'answer-{i:02}'}])
    messages.extend([
        {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'call', 'name': 'read_file', 'input': {}}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call', 'content': 'x' * 500}]},
    ])
    terminal.show_history('session-test', messages)
    visible = output.getvalue()
    assert 'question-00' not in visible and 'question-02' in visible
    assert '2 earlier turn(s)' in visible and '/history all' in visible
    assert 'read_file' in visible and 'completed' in visible and 'x' * 201 not in visible
    output.seek(0)
    output.truncate()
    terminal.show_history('session-test', messages, limit=None)
    assert 'question-00' in output.getvalue()


def test_delete_permission_prompt_is_concise_and_one_shot(
    monkeypatch,
) -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=100)
    terminal = TerminalUI(
        console=console,
        prompt_session=FakePromptSession(''),
    )
    captured: dict[str, object] = {}

    def fake_choice(**kwargs):
        captured.update(kwargs)
        return 'allow_once'

    monkeypatch.setattr(terminal_module, 'permission_choice', fake_choice)
    response = terminal.select_permission(
        PermissionRequest(
            'apply_patch',
            'file.delete',
            'high',
            ('play/a.txt', 'play/b.txt'),
            'The patch deletes repository files.',
            '{}',
        )
    )

    rendered = output.getvalue()
    assert response.choice == 'allow_once'
    assert '删除文件' in rendered
    assert '目标（2）' in rendered
    assert 'play/a.txt' in rendered
    assert '{}' not in rendered
    assert '如何操作' not in rendered  # Options are displayed only in the selector.
    assert captured['default'] == 'allow_once'
    assert captured['options'] == [
        ('allow_once', '是，仅允许这一次'),
        ('deny', '否，停止本轮操作'),
    ]


def test_pressing_enter_accepts_default_one_shot_delete() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=100)
    terminal = TerminalUI(
        console=console,
        prompt_session=FakePromptSession(''),
    )

    with create_pipe_input() as pipe_input:
        pipe_input.send_text('\r')
        with create_app_session(
            input=pipe_input,
            output=DummyOutput(),
        ):
            response = terminal.select_permission(
                PermissionRequest(
                    'remove_directory',
                    'file.delete',
                    'high',
                    ('play/src',),
                )
            )

    assert response.choice == 'allow_once'


def test_sandbox_widening_shows_host_consequence_and_cannot_be_remembered(monkeypatch):
    output = StringIO()
    terminal = TerminalUI(console=Console(file=output, force_terminal=True, width=100),
                          prompt_session=FakePromptSession(''))
    captured = {}
    def choose(**kwargs):
        captured.update(kwargs)
        return 'allow_once'
    monkeypatch.setattr(terminal_module, 'permission_choice', choose)
    response = terminal.select_permission(PermissionRequest('run_command', 'process.exec', 'high',
        reason='本次命令将使用宿主权限执行，不受工作区沙盒限制。仅本次有效。',
        preview='echo exact', sandbox_mode='full-access'))
    assert response.choice == 'allow_once'
    assert '宿主权限' in output.getvalue() and 'echo exact' in output.getvalue()
    assert [key for key, _ in captured['options']] == ['allow_once', 'deny']


def permission_choice_for_keys(keys: str) -> str:
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=100)
    terminal = TerminalUI(
        console=console,
        prompt_session=FakePromptSession(''),
    )
    with create_pipe_input() as pipe_input:
        pipe_input.send_text(keys)
        with create_app_session(
            input=pipe_input,
            output=DummyOutput(),
        ):
            return terminal.select_permission(
                PermissionRequest(
                    'remove_directory',
                    'file.delete',
                    'high',
                    ('play/src',),
                )
            ).choice


def test_permission_keyboard_shortcuts_are_obvious_and_work() -> None:
    assert permission_choice_for_keys('y') == 'allow_once'
    assert permission_choice_for_keys('1') == 'allow_once'
    assert permission_choice_for_keys('2') == 'allow_session'
    assert permission_choice_for_keys('n') == 'deny'
    assert permission_choice_for_keys('\x03') == 'deny'
    assert permission_choice_for_keys('\x1b[B\r') == 'allow_session'


def test_welcome_card_fits_a_narrow_terminal(monkeypatch):
    from rich.cells import cell_len
    monkeypatch.setenv('TERM', 'xterm-256color')
    output = StringIO()
    terminal = TerminalUI(Console(file=output, force_terminal=False, width=48))
    terminal.show_welcome('deepseek-v4-flash')
    rendered = output.getvalue()
    assert 'ForgeCode v0.1.2' in rendered and 'deepseek-v4-flash' in rendered
    assert '工作区' in rendered and '/resume' in rendered
    assert all(cell_len(line) <= 48 for line in rendered.splitlines())


def test_single_target_delete_can_be_allowed_for_session(
    monkeypatch,
) -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=100)
    terminal = TerminalUI(
        console=console,
        prompt_session=FakePromptSession(''),
    )
    captured: dict[str, object] = {}

    def fake_choice(**kwargs):
        captured.update(kwargs)
        return 'allow_session'

    monkeypatch.setattr(terminal_module, 'permission_choice', fake_choice)
    response = terminal.select_permission(
        PermissionRequest(
            'remove_directory',
            'file.delete',
            'high',
            ('play/src',),
        )
    )

    assert response.choice == 'allow_session'
    assert captured['default'] == 'allow_once'
    assert captured['options'] == [
        ('allow_once', '是，仅允许这一次'),
        ('allow_session', '是，本会话不再询问此目标'),
        ('deny', '否，停止本轮操作'),
    ]


def test_terminal_preserves_multiline_prompt_from_interactive_session() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=100)
    prompt_session = FakePromptSession('first line\nsecond line\nthird line')
    terminal = TerminalUI(
        console=console,
        prompt_session=prompt_session,
    )

    prompt = terminal.read_prompt()

    assert prompt == 'first line\nsecond line\nthird line'
    assert len(prompt_session.messages) == 1


def completions_for(text: str) -> list[object]:
    return list(
        SLASH_COMMAND_COMPLETER.get_completions(
            Document(text=text, cursor_position=len(text)),
            CompleteEvent(completion_requested=True),
        )
    )


def test_slash_opens_command_completion_menu() -> None:
    completions = completions_for('/')

    usages = [completion.display_text for completion in completions]
    descriptions = [completion.display_meta_text for completion in completions]

    assert '/context' in usages
    assert '/skills' in usages
    assert '/skill skill-name' in usages
    assert '/remember name | content' in usages
    assert '/memory consolidate' in usages
    assert '/task' in usages
    assert '/task history' in usages
    assert '/task resume task-id' in usages
    assert '/resume [session]' in usages
    assert '/rewind [checkpoint] [mode]' in usages
    assert '查看当前上下文统计' in descriptions


def test_slash_completion_filters_and_replaces_current_input() -> None:
    completions = completions_for('/memory s')

    assert [completion.text for completion in completions] == [
        '/memory show '
    ]
    assert completions[0].start_position == -len('/memory s')


def test_file_mention_completion_uses_working_directory(tmp_path) -> None:
    source = tmp_path / 'src'
    source.mkdir()
    (source / 'app.py').write_text('print("ready")\n', encoding='utf-8')
    (tmp_path / '.env').write_text('SECRET=value\n', encoding='utf-8')
    completer = SlashCommandCompleter(tmp_path)

    completions = list(
        completer.get_completions(
            Document(
                text='review @src/a',
                cursor_position=len('review @src/a'),
            ),
            CompleteEvent(completion_requested=True),
        )
    )

    assert [item.text for item in completions] == ['src/app.py']
    assert completions[0].display_text == '@src/app.py'
    assert completions[0].start_position == -len('src/a')


def test_normal_prompt_does_not_offer_slash_commands() -> None:
    assert completions_for('fix this bug') == []


def test_permission_is_the_only_permission_mode_slash_command() -> None:
    assert [item.text for item in completions_for('/permission')] == [
        '/permission '
    ]
    assert completions_for('/mode') == []
    assert completions_for('/permissions') == []
    assert [item.text for item in completions_for('/mcp')] == ['/mcp']


def test_resume_completion_filters_dynamic_sessions() -> None:
    completer = SlashCommandCompleter()
    completer.set_session_options(
        (
            SessionOption(
                identifier='session-123',
                label='stable-auth',
                description='stopped · session-123',
            ),
            SessionOption(
                identifier='session-456',
                label='other-task',
                description='active · session-456',
            ),
        )
    )

    completions = list(
        completer.get_completions(
            Document(
                text='/resume stab',
                cursor_position=len('/resume stab'),
            ),
            CompleteEvent(completion_requested=True),
        )
    )

    assert [item.text for item in completions] == ['session-123']
    assert completions[0].display_text == 'stable-auth'
    assert completions[0].start_position == -len('stab')


def test_terminal_session_picker_returns_selected_session(
    monkeypatch,
) -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=100)
    terminal = TerminalUI(
        console=console,
        prompt_session=FakePromptSession(''),
    )

    monkeypatch.setattr(
        'forge.terminal.choice',
        lambda **_kwargs: 'session-123',
    )

    selected = terminal.select_session(
        (
            SessionOption(
                identifier='session-123',
                label='stable-auth',
                description='stopped · session-123',
            ),
        )
    )

    assert selected == 'session-123'
    assert terminal.read_prompt() == ''


def test_terminal_session_picker_ctrl_c_returns_to_prompt(
    monkeypatch,
) -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=100)
    terminal = TerminalUI(
        console=console,
        prompt_session=FakePromptSession('next prompt'),
    )

    def interrupted(**_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr('forge.terminal.choice', interrupted)

    selected = terminal.select_session(
        (
            SessionOption(
                identifier='session-123',
                label='stable-auth',
                description='已停止 · 刚刚 · session-123',
            ),
        )
    )

    assert selected is None
    assert terminal.read_prompt() == 'next prompt'


def test_redirected_terminal_streams_markdown_source_once() -> None:
    terminal, output = terminal_with_output()
    usage = TokenUsage(input_tokens=1200, output_tokens=34)

    terminal.show_welcome('test-model')
    with terminal.stream_response() as response:
        response.update_usage(
            TokenUsage(input_tokens=1200, output_tokens=0)
        )
        response.append_text('**Hello** ')
        assert '**Hello** ' in output.getvalue()  # Before the rest of the response exists.
        response.append_text('from ForgeCode')
        response.update_usage(usage)
        response.complete(
            TurnResult(
                text='**Hello** from ForgeCode',
                usage=usage,
            )
        )
    terminal.show_goodbye()

    rendered = output.getvalue()
    assert 'ForgeCode v0.1.2' in rendered
    assert 'test-model' in rendered
    assert 'Ctrl+C 取消/退出' in rendered
    assert rendered.count('**Hello** from ForgeCode') == 1
    assert 'input 1,200' in rendered
    assert 'output 34' in rendered
    assert 'total 1,234' in rendered
    assert 'Session ended.' in rendered


def test_terminal_renders_cache_token_details() -> None:
    terminal, output = terminal_with_output()

    terminal.console.print(
        token_usage_summary(
            TokenUsage(
                input_tokens=100,
                output_tokens=20,
                cache_creation_input_tokens=30,
                cache_read_input_tokens=40,
            ),
            streaming=False,
        )
    )

    rendered = output.getvalue()
    assert 'tokens' in rendered
    assert 'input 170' in rendered
    assert 'total 190' in rendered
    assert 'cache read 40' in rendered
    assert 'cache write 30' in rendered


def test_terminal_separates_last_request_from_turn_cumulative_usage() -> None:
    terminal, output = terminal_with_output()

    terminal.console.print(
        token_usage_summary(
            TokenUsage(
                input_tokens=313_367,
                output_tokens=10_806,
                cache_read_input_tokens=50_560,
            ),
            streaming=False,
            request_usage=TokenUsage(
                input_tokens=9_000,
                output_tokens=300,
                cache_read_input_tokens=2_000,
            ),
            model_calls=26,
        )
    )

    rendered = output.getvalue()
    assert 'turn cumulative' in rendered
    assert 'input 363,927' in rendered
    assert 'last request  input 11,000' in rendered
    assert 'output 300' in rendered
    assert '26 model calls' in rendered


def test_terminal_labels_zero_stream_usage_as_waiting() -> None:
    terminal, output = terminal_with_output()

    terminal.console.print(
        token_usage_summary(
            TokenUsage(input_tokens=220_371, output_tokens=21_273),
            streaming=True,
            request_usage=TokenUsage(input_tokens=0, output_tokens=0),
            model_calls=15,
        )
    )

    rendered = output.getvalue()
    assert 'waiting for provider usage' in rendered
    assert 'last request  input 0' not in rendered
    assert '15 model calls' in rendered


def test_terminal_renders_tool_arguments_and_result_status() -> None:
    terminal, output = terminal_with_output()
    successful_call = ToolCall(
        index=0,
        id='toolu_read',
        name='read_file',
        arguments={'path': 'README.md'},
    )
    failed_call = ToolCall(
        index=1,
        id='toolu_shell',
        name='run_command',
        arguments={'command': 'pytest'},
    )

    with terminal.stream_response() as response:
        response.start_tool(successful_call)
        response.complete_tool(
            successful_call,
            ToolResult.ok('Read 20 lines.'),
        )
        response.start_tool(failed_call)
        response.complete_tool(
            failed_call,
            ToolResult.fail(
                'command_failed',
                'Command exited with code 1.',
                content='pytest: assertion failed at test_game.py:42',
            ),
        )

    rendered = output.getvalue()
    assert 'read_file {"path": "README.md"}' in rendered
    assert '✓' in rendered
    assert 'Read 20 lines.' in rendered
    assert 'run_command {"command": "pytest"}' in rendered
    assert '×' in rendered
    assert 'Command exited with code 1.' in rendered
    assert 'pytest: assertion failed at test_game.py:42' in rendered


def test_terminal_places_tool_group_between_model_text_blocks() -> None:
    terminal, output = terminal_with_output()
    usage = TokenUsage(input_tokens=40, output_tokens=8)
    first = ToolCall(
        index=0,
        id='toolu_read',
        name='read_file',
        arguments={'path': 'README.md'},
    )
    second = ToolCall(
        index=1,
        id='toolu_grep',
        name='grep',
        arguments={'pattern': 'M1.4'},
    )

    with terminal.stream_response() as response:
        response.append_text('我先检查项目。')
        response.start_tool(first)
        response.complete_tool(first, ToolResult.ok('读取完成。'))
        response.start_tool(second)
        response.complete_tool(second, ToolResult.ok('搜索完成。'))
        response.append_text('检查完成，下面是结论。')
        response.update_usage(usage)
        response.complete(
            TurnResult(
                text='检查完成，下面是结论。',
                usage=usage,
                tool_calls=(first, second),
            )
        )

    rendered = output.getvalue()
    before = rendered.index('我先检查项目。')
    tool_group = rendered.index('已运行 read_file')
    after = rendered.index('检查完成，下面是结论。')
    assert before < tool_group < after
    assert rendered.index('read_file') < rendered.index('grep')


def test_terminal_does_not_repeat_continued_text_around_tools() -> None:
    terminal, output = terminal_with_output()
    usage = TokenUsage(input_tokens=40, output_tokens=8)
    tool_call = ToolCall(
        index=0,
        id='toolu_read',
        name='read_file',
        arguments={'path': 'README.md'},
    )

    with terminal.stream_response() as response:
        response.append_text('First half, ')
        response.start_tool(tool_call)
        response.complete_tool(tool_call, ToolResult.ok('Read file.'))
        response.append_text('second half.')
        response.complete(
            TurnResult(
                text='First half, second half.',
                usage=usage,
                tool_calls=(tool_call,),
            )
        )

    rendered = output.getvalue()
    assert rendered.count('First half') == 1
    assert rendered.count('second half.') == 1


def test_terminal_renders_live_usage_state() -> None:
    terminal, output = terminal_with_output()

    terminal.console.print(
        token_usage_summary(
            TokenUsage(
                input_tokens=100,
                output_tokens=0,
            ),
            streaming=True,
        )
    )

    rendered = output.getvalue()
    assert 'streaming' in rendered
    assert 'input 100' in rendered
    assert 'output 0' in rendered


def test_streaming_preview_keeps_only_a_bounded_tail() -> None:
    preview = streaming_preview(
        'first\nsecond\nthird\nfourth',
        max_lines=2,
        max_characters=100,
    )

    assert preview == '…\nthird\nfourth'
    assert 'first' not in preview
    assert 'second' not in preview


def test_streaming_preview_bounds_long_unbroken_text() -> None:
    preview = streaming_preview(
        'a' * 500,
        max_lines=10,
        max_characters=80,
    )

    assert preview == f'…\n{"a" * 80}'


def test_terminal_renders_error_text_literally() -> None:
    terminal, output = terminal_with_output()

    terminal.show_error(RuntimeError('[provider] unavailable'))

    assert 'Model request failed: [provider] unavailable' in output.getvalue()


def test_terminal_renders_final_change_and_verification_evidence() -> None:
    terminal, output = terminal_with_output()
    usage = TokenUsage(input_tokens=20, output_tokens=4)
    evidence = VerificationEvidence(
        command='pytest -q',
        cwd='.',
        exit_code=0,
        duration_seconds=1.25,
        timed_out=False,
        workspace_revision=1,
    )

    with terminal.stream_response() as response:
        response.complete(
            TurnResult(
                text='Done.',
                usage=usage,
                changed_paths=('forge/app.py',),
                verification=evidence,
            )
        )

    rendered = output.getvalue()
    assert 'task completed' in rendered
    assert 'forge/app.py' in rendered
    assert 'pytest -q' in rendered
    assert 'exit 0' in rendered


def test_interactive_preview_uses_prompt_renderer_without_reprinting_or_losing_input():
    async def run():
        output = StringIO()
        console = Console(file=output, force_terminal=True, legacy_windows=False, width=40, height=16)
        with create_pipe_input() as input_pipe:
            prompt = PromptSession(input=input_pipe, output=DummyOutput())
            prompt.app.min_redraw_interval = 1 / 30
            terminal = TerminalUI(console=console, prompt_session=prompt)
            rendered = asyncio.Event()
            prompt.app.after_render += lambda app: rendered.set()
            with terminal.stream_response() as view:
                reader = asyncio.create_task(terminal.read_prompt_async())
                async def wait_for(predicate):
                    async with asyncio.timeout(3):
                        while not predicate():
                            rendered.clear()
                            await rendered.wait()
                try:
                    await wait_for(lambda: prompt.app.is_running)
                    def screen_text():
                        screen = prompt.app.renderer._last_screen
                        if screen is None:
                            return ''
                        return '\n'.join(''.join(cell.char for _, cell in sorted(row.items()))
                            for _, row in sorted(screen.data_buffer.items()))
                    first = '中文开头：' + '很长的文本' * 50
                    view.append_text(first)
                    await wait_for(lambda: '很长的文本' in screen_text())
                    assert first not in output.getvalue()  # Draft stays in the prompt layout.
                    assert len(view.prompt_fragments()[0][1].splitlines()) <= 10
                    input_pipe.send_text('保留输入')
                    await wait_for(lambda: prompt.default_buffer.text == '保留输入')
                    for _ in range(5):
                        view.update_usage(TokenUsage(10, 1))
                        view.pause_for_prompt()
                        view.resume_after_prompt()
                    view.append_text('\n末尾已经到达')
                    await wait_for(lambda: '末尾已经到达' in screen_text())
                    assert prompt.default_buffer.text == '保留输入'
                    assert '末尾已经到达' not in output.getvalue()
                    view.complete(TurnResult(text=first + '\n末尾已经到达', usage=TokenUsage(10, 2)))
                    assert view.prompt_fragments() == []
                    input_pipe.send_text('\r')
                    assert await asyncio.wait_for(reader, 3) == '保留输入'
                finally:
                    if not reader.done():
                        reader.cancel()
                        await asyncio.gather(reader, return_exceptions=True)
            text = output.getvalue()
            assert text.count('中文开头') == 1 and text.count('末尾已经到达') == 1
    asyncio.run(run())


def test_dumb_terminal_streams_without_cursor_renderer(monkeypatch):
    monkeypatch.setenv('TERM', 'dumb')
    output = StringIO()
    terminal = TerminalUI(Console(file=output, force_terminal=True, width=80))
    assert terminal.prompt_session is None
    with terminal.stream_response() as view:
        view.append_text('Already visible')
        assert 'Already visible' in output.getvalue()
        view.complete(TurnResult(text='Already visible', usage=TokenUsage(10, 2)))
    assert output.getvalue().count('Already visible') == 1


def test_long_tool_history_keeps_wait_animation_visible_with_completion_menu():
    async def run():
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=80, height=24)
        class SizedOutput(DummyOutput):
            def get_size(self):
                from prompt_toolkit.data_structures import Size
                return Size(rows=24, columns=80)
        with create_pipe_input() as pipe:
            # Use the actual CLI's completion reservation, absent from earlier
            # preview tests. It competes with the timeline for terminal rows.
            prompt = PromptSession(input=pipe, output=SizedOutput(),
                completer=SLASH_COMMAND_COMPLETER, reserve_space_for_menu=8)
            terminal = TerminalUI(console=console, prompt_session=prompt)
            rendered = asyncio.Event()
            prompt.app.after_render += lambda app: rendered.set()
            def screen():
                value = prompt.app.renderer._last_screen
                return '' if value is None else '\n'.join(''.join(c.char for _, c in sorted(row.items()))
                    for _, row in sorted(value.data_buffer.items()))
            async def wait_for(predicate):
                async with asyncio.timeout(3):
                    while not predicate():
                        rendered.clear()
                        await rendered.wait()
            with terminal.stream_response() as view:
                for i in range(12):
                    view.append_reasoning('正在检查第 ' + str(i) + ' 步')
                    call = ToolCall(0, str(i), 'run_command', {'command': 'echo ok'})
                    view.start_tool(call)
                    view.complete_tool(call, ToolResult.ok('ok'))
                view.model_started()
                reader = asyncio.create_task(terminal.read_prompt_async())
                try:
                    await wait_for(lambda: '正在等待模型' in screen())
                    previous = screen()
                    await wait_for(lambda: screen() != previous)
                    pipe.send_text('保留草稿')
                    await wait_for(lambda: prompt.default_buffer.text == '保留草稿')
                    pipe.send_text('\x0f')
                    await wait_for(lambda: view.expanded and '正在等待模型' in screen())
                    assert prompt.default_buffer.text == '保留草稿'
                finally:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
    asyncio.run(run())


def test_wait_animation_disclosure_and_draft_survive_turn_completion():
    async def run():
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=100, height=30, color_system=None)
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            prompt = PromptSession(input=pipe, output=DummyOutput())
            terminal = TerminalUI(console=console, prompt_session=prompt)
            rendered = asyncio.Event()
            prompt.app.after_render += lambda app: rendered.set()
            def screen():
                value = prompt.app.renderer._last_screen
                return '' if value is None else '\n'.join(''.join(c.char for _, c in sorted(row.items()))
                    for _, row in sorted(value.data_buffer.items()))
            async def wait_for(predicate):
                async with asyncio.timeout(4):
                    while not predicate():
                        rendered.clear()
                        await rendered.wait()
            with terminal.stream_response() as view:
                reader = asyncio.create_task(terminal.read_prompt_async())
                try:
                    await wait_for(lambda: '正在准备请求' in screen())
                    initial = screen()
                    # The renderer advances with no provider event at all.
                    await wait_for(lambda: screen() != initial)
                    view.append_reasoning('hidden earlier reasoning\nlatest reasoning')
                    await wait_for(lambda: 'latest reasoning' in screen())
                    assert 'hidden earlier reasoning' not in screen()
                    pipe.send_text('\x0f')  # Ctrl+O expands in place, never submits input.
                    await wait_for(lambda: 'hidden earlier reasoning' in screen())
                    call = ToolCall(0, 'read', 'read_file', {'path': 'hello.txt'})
                    view.start_tool(call)
                    view.complete_tool(call, ToolResult.ok('Read.', content='complete tool output'))
                    await wait_for(lambda: 'complete tool output' in screen())
                    pipe.send_text('\x0f')
                    await wait_for(lambda: 'complete tool output' not in screen())
                    assert 'latest reasoning' in screen()
                    pipe.send_text('尚未提交的输入')
                    await wait_for(lambda: prompt.default_buffer.text == '尚未提交的输入')
                    view.append_text('**Answer** arrives immediately.')
                    await wait_for(lambda: 'Answer' in screen())
                    assert 'Answer' not in output.getvalue()
                    view.complete(TurnResult(text='**Answer** arrives immediately.', usage=TokenUsage(20, 5)))
                finally:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
            assert 'hidden earlier reasoning\nlatest reasoning' not in output.getvalue()
            assert 'complete tool output' not in output.getvalue()
            assert output.getvalue().count('Answer') == 1
            assert 'complete tool output' in view.details_text()
            next_reader = asyncio.create_task(terminal.read_prompt_async())
            try:
                await wait_for(lambda: prompt.app.is_running and prompt.default_buffer.text == '尚未提交的输入')
                pipe.send_text('\r')
                assert await next_reader == '尚未提交的输入'
            finally:
                if not next_reader.done():
                    next_reader.cancel()
                    await asyncio.gather(next_reader, return_exceptions=True)
    asyncio.run(run())


def test_completed_details_viewer_closes_without_resubmitting_input(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm-256color')
    async def run():
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            prompt = PromptSession(input=pipe, output=DummyOutput())
            terminal = TerminalUI(Console(file=StringIO(), force_terminal=True), prompt_session=prompt)
            with terminal.stream_response() as view:
                view.append_reasoning('Full reasoning available after the turn.')
                view.complete(TurnResult(text='Answer', usage=TokenUsage(1, 2)))
            task = asyncio.create_task(terminal.show_details_async())
            try:
                await asyncio.sleep(.1)
                assert not task.done()
                pipe.send_text('\x0f')
                await asyncio.wait_for(task, 3)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_switching_session_clears_previous_session_details():
    terminal, _ = terminal_with_output()
    terminal.bind_session('first')
    with terminal.stream_response() as view:
        view.append_reasoning('First session detail')
        view.complete(TurnResult(text='first', usage=TokenUsage(1, 1)))
    terminal.bind_session('first')
    assert terminal._response_view is view
    terminal.bind_session('second')
    assert terminal._response_view is None


def test_completed_detail_search_and_return_preserve_prompt(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm-256color')
    async def run():
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            prompt = PromptSession(input=pipe, output=DummyOutput())
            terminal = TerminalUI(Console(file=StringIO(), force_terminal=True), prompt_session=prompt)
            with terminal.stream_response() as view:
                view.append_reasoning('first\n' * 50 + 'search target')
                view.complete(TurnResult(text='done', usage=TokenUsage(1, 2)))
            # Exercise the actual idle Ctrl+O binding, which suspends the prompt.
            reader = asyncio.create_task(terminal.read_prompt_async())
            async def wait_for(predicate):
                async with asyncio.timeout(3):
                    while not predicate():
                        await asyncio.sleep(.01)
            try:
                await wait_for(lambda: prompt.app.is_running)
                pipe.send_text('draft\x0f')
                await wait_for(lambda: prompt.app._running_in_terminal)
                pipe.send_text('\x06search target\r')
                await asyncio.sleep(.1)
                assert prompt.default_buffer.text == 'draft'
                pipe.send_text('\x0f')
                await wait_for(lambda: not prompt.app._running_in_terminal)
                pipe.send_text('\r')
                assert await asyncio.wait_for(reader, 3) == 'draft'
            finally:
                if not reader.done():
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
    asyncio.run(run())
