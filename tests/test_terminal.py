'''Tests for the Rich terminal presentation.'''

from io import StringIO

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from rich.console import Console

from forge.runtime.state import (
    TokenUsage,
    ToolCall,
    TurnResult,
    VerificationEvidence,
)
from forge.terminal import (
    SLASH_COMMAND_COMPLETER,
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


def terminal_with_output() -> tuple[TerminalUI, StringIO]:
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=False,
        width=100,
    )
    return TerminalUI(console=console), output


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
    assert '/remember name | content' in usages
    assert '/memory consolidate' in usages
    assert '/task' in usages
    assert '/task history' in usages
    assert '/task resume task-id' in usages
    assert '查看当前上下文统计' in descriptions


def test_slash_completion_filters_and_replaces_current_input() -> None:
    completions = completions_for('/memory s')

    assert [completion.text for completion in completions] == [
        '/memory show '
    ]
    assert completions[0].start_position == -len('/memory s')


def test_normal_prompt_does_not_offer_slash_commands() -> None:
    assert completions_for('fix this bug') == []


def test_terminal_renders_session_header_and_markdown_response() -> None:
    terminal, output = terminal_with_output()
    usage = TokenUsage(input_tokens=1200, output_tokens=34)

    terminal.show_welcome('test-model')
    with terminal.stream_response() as response:
        response.update_usage(
            TokenUsage(input_tokens=1200, output_tokens=0)
        )
        response.append_text('**Hello** ')
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
    assert 'ForgeCode v0.1.0' in rendered
    assert 'test-model' in rendered
    assert 'Ctrl+C to exit' in rendered
    assert 'Hello from ForgeCode' in rendered
    assert rendered.count('Hello from ForgeCode') == 1
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
            ),
        )

    rendered = output.getvalue()
    assert 'read_file {"path": "README.md"}' in rendered
    assert '✓' in rendered
    assert 'Read 20 lines.' in rendered
    assert 'run_command {"command": "pytest"}' in rendered
    assert '×' in rendered
    assert 'Command exited with code 1.' in rendered


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
    tool_group = rendered.index('已运行 2 个工具')
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
