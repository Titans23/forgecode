'''Tests for the ForgeCode CLI.'''

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

import forge.cli as cli_module
from forge.cli import app
from forge.config import ConfigurationError
from forge.runtime.state import (
    ConversationEvent,
    ModelTextDelta,
    ModelUsageUpdate,
    TokenUsage,
    ToolCall,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    TurnCompleted,
    TurnResult,
)
from forge.context.manager import ContextStats
from forge.sessions.store import SessionStore
from forge.tools.base import ToolResult


runner = CliRunner()


class FakeTrajectoryRecorder:
    def __init__(self) -> None:
        self.user_messages: list[str] = []
        self.events: list[ConversationEvent] = []
        self.errors: list[Exception] = []

    def record_user_message(self, content: str) -> None:
        self.user_messages.append(content)

    def record_event(self, event: ConversationEvent) -> None:
        self.events.append(event)

    def record_error(self, error: Exception) -> None:
        self.errors.append(error)


@pytest.fixture(autouse=True)
def avoid_real_runtime_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv('FORGE_DATA_DIR', str(tmp_path / 'forge-data'))
    monkeypatch.setattr(
        cli_module,
        'create_trajectory_recorder',
        lambda _root: FakeTrajectoryRecorder(),
    )


class FakeConversation:
    '''Return scripted responses for interactive CLI tests.'''

    def __init__(
        self,
        *responses: list[ConversationEvent] | Exception,
    ) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.session_actions: list[tuple[str, object]] = []
        self.context_stats = ContextStats(
            2,
            120,
            40,
            system_characters=40,
            repository_characters=20,
            tool_schema_characters=20,
            context_window_tokens=1_000,
            reserved_output_tokens=100,
            stored_message_count=284,
            stored_estimated_characters=537_342,
            stored_tool_result_characters=457_675,
        )

    async def stream(
        self,
        prompt: str,
    ) -> AsyncIterator[ConversationEvent]:
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        for event in response:
            yield event

    def remember(self, name: str, content: str) -> str:
        return f'Remembered {name}: {content}'

    def memory_list(self) -> str:
        return '- testing [project]: test command'

    def memory_show(self, name: str) -> str:
        return f'{name}\nUse pytest.'

    def memory_forget(self, name: str) -> str:
        return f'Forgot {name}.'

    def memory_rebuild(self) -> str:
        return 'Rebuilt memory index.'

    def memory_consolidate(self) -> str:
        return 'Consolidated memory; removed 0 duplicate(s).'

    def task_show(self) -> str:
        return 'id: task-current\nstatus: in_progress'

    def task_history(self) -> str:
        return '- task-saved [blocked]: Finish feature'

    def task_resume(self, task_id: str) -> str:
        return f'Resumed {task_id}: Finish feature'

    def session_status(self) -> str:
        return 'id: session-test\nmessages: 2'

    def session_history(self) -> str:
        return '- 1: session_started'

    def session_rename(self, name: str) -> str:
        self.session_actions.append(('rename', name))
        return f'Renamed session to {name}.'

    def session_resume(self, identifier: str) -> str:
        self.session_actions.append(('resume', identifier))
        return f'Resumed {identifier}.'

    def session_candidates(self) -> str:
        return '- session-old [stopped]'

    def session_branch(self, name: str | None = None) -> str:
        self.session_actions.append(('branch', name))
        return 'Branched session.'

    def session_clear(self) -> str:
        self.session_actions.append(('clear', None))
        return 'Cleared conversation.'

    def checkpoint_history(self) -> str:
        return '- checkpoint-test'

    def checkpoint_rewind(
        self,
        checkpoint_id: str | None = None,
        *,
        mode: str = 'both',
    ) -> str:
        self.session_actions.append(
            ('rewind', (checkpoint_id, mode))
        )
        return f'Rewound {mode}.'


class FakeResponseView:
    '''Record live UI updates without rendering a terminal.'''

    def __init__(self) -> None:
        self.actions: list[tuple[str, object]] = []

    def append_text(self, text: str) -> None:
        self.actions.append(('text', text))

    def update_usage(
        self,
        usage: TokenUsage,
        *,
        request_usage: TokenUsage | None = None,
        model_calls: int = 1,
    ) -> None:
        del request_usage, model_calls
        self.actions.append(('usage', usage))

    def start_tool(self, tool_call: ToolCall) -> None:
        self.actions.append(('tool_started', tool_call))

    def complete_tool(
        self,
        tool_call: ToolCall,
        result: ToolResult,
    ) -> None:
        self.actions.append(('tool_completed', (tool_call, result)))

    def complete(self, result: TurnResult) -> None:
        self.actions.append(('complete', result))


def turn(
    text: str,
    input_tokens: int,
    output_tokens: int,
) -> list[ConversationEvent]:
    usage = TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    result = TurnResult(
        text=text,
        usage=usage,
    )
    return [
        ModelUsageUpdate(
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=0,
            )
        ),
        ModelTextDelta(text=text),
        ModelUsageUpdate(usage=usage),
        TurnCompleted(result=result),
    ]


def test_stream_events_are_forwarded_to_live_view() -> None:
    initial_usage = TokenUsage(input_tokens=10, output_tokens=0)
    final_usage = TokenUsage(input_tokens=10, output_tokens=2)
    result = TurnResult(text='Hello', usage=final_usage)
    tool_call = ToolCall(
        index=0,
        id='toolu_test',
        name='read_file',
        arguments={'path': 'README.md'},
    )
    tool_result = ToolResult.ok('Read file.', content='README')
    streamed_events: list[ConversationEvent] = [
        ModelUsageUpdate(usage=initial_usage),
        ModelTextDelta(text='Hel'),
        ModelTextDelta(text='lo'),
        ToolExecutionStarted(tool_call=tool_call),
        ToolExecutionCompleted(
            tool_call=tool_call,
            result=tool_result,
        ),
        ModelUsageUpdate(usage=final_usage),
        TurnCompleted(result=result),
    ]
    conversation = FakeConversation(streamed_events)
    response_view = FakeResponseView()
    recorder = FakeTrajectoryRecorder()

    asyncio.run(
        cli_module.render_streamed_turn(
            conversation,
            'hello',
            response_view,
            recorder,
        )
    )

    assert recorder.user_messages == ['hello']
    assert recorder.events == streamed_events
    assert recorder.errors == []
    assert response_view.actions == [
        ('usage', initial_usage),
        ('text', 'Hel'),
        ('text', 'lo'),
        ('tool_started', tool_call),
        ('tool_completed', (tool_call, tool_result)),
        ('usage', final_usage),
        ('complete', result),
    ]


def test_cli_starts_an_interactive_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = FakeConversation(
        turn('Hello', input_tokens=10, output_tokens=2),
        turn('I remember', input_tokens=20, output_tokens=3),
    )
    monkeypatch.setattr(
        cli_module,
        'Conversation',
        lambda **_kwargs: conversation,
    )

    result = runner.invoke(app, input='first\nsecond\n')

    assert result.exit_code == 0
    assert 'ForgeCode v0.1.0' in result.output
    assert 'Ctrl+C to exit' in result.output
    assert 'Ask a question or describe a coding task.' in result.output
    assert 'Hello' in result.output
    assert 'I remember' in result.output
    assert 'input 10' in result.output
    assert 'output 2' in result.output
    assert 'total 12' in result.output
    assert 'input 20' in result.output
    assert 'output 3' in result.output
    assert 'total 23' in result.output
    assert 'Session ended.' in result.output
    assert conversation.prompts == ['first', 'second']


def test_context_command_does_not_call_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = FakeConversation()
    monkeypatch.setattr(
        cli_module,
        'Conversation',
        lambda **_kwargs: conversation,
    )

    result = runner.invoke(app, input='/context\n')

    assert result.exit_code == 0
    assert 'stored messages' in result.output
    assert '284' in result.output
    assert 'request messages' in result.output
    assert '2' in result.output
    assert 'estimated input' in result.output
    assert '~50 tokens' in result.output
    assert 'request tool results' in result.output
    assert '40 chars' in result.output
    assert 'projected total' in result.output
    assert '~150 tokens' in result.output
    assert 'remaining' in result.output
    assert '~850 tokens' in result.output
    assert 'projected utilization' in result.output
    assert '15.0%' in result.output
    assert conversation.prompts == []


def test_memory_commands_do_not_call_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = FakeConversation()
    monkeypatch.setattr(
        cli_module,
        'Conversation',
        lambda **_kwargs: conversation,
    )

    result = runner.invoke(
        app,
        input=(
            '/remember testing | Use pytest.\n'
            '/memory list\n'
            '/memory show testing\n'
            '/memory forget testing\n'
            '/memory rebuild\n'
            '/memory consolidate\n'
        ),
    )

    assert result.exit_code == 0
    assert 'Remembered testing: Use pytest.' in result.output
    assert 'Use pytest.' in result.output
    assert 'Forgot testing.' in result.output
    assert 'Rebuilt memory index.' in result.output
    assert 'Consolidated memory' in result.output
    assert conversation.prompts == []


def test_task_commands_do_not_call_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = FakeConversation()
    monkeypatch.setattr(
        cli_module,
        'Conversation',
        lambda **_kwargs: conversation,
    )

    result = runner.invoke(
        app,
        input='/task\n/task history\n/task resume task-saved\n',
    )

    assert result.exit_code == 0
    assert 'task-current' in result.output
    assert 'task-saved [blocked]' in result.output
    assert 'Resumed task-saved' in result.output
    assert conversation.prompts == []


def test_session_commands_do_not_call_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = FakeConversation()
    monkeypatch.setattr(
        cli_module,
        'Conversation',
        lambda **_kwargs: conversation,
    )

    result = runner.invoke(
        app,
        input=(
            '/status\n'
            '/history\n'
            '/rename feature-work\n'
            '/branch experiment\n'
            '/checkpoints\n'
            '/rewind checkpoint-test conversation\n'
            '/clear\n'
            '/resume\n'
            '/resume session-old\n'
        ),
    )

    assert result.exit_code == 0
    assert 'session-test' in result.output
    assert 'session_started' in result.output
    assert 'checkpoint-test' in result.output
    assert 'No other saved ForgeCode sessions' in result.output
    assert conversation.session_actions == [
        ('rename', 'feature-work'),
        ('branch', 'experiment'),
        ('rewind', ('checkpoint-test', 'conversation')),
        ('clear', None),
        ('resume', 'session-old'),
    ]
    assert conversation.prompts == []


def test_resume_picker_switches_context_without_calling_model() -> None:
    conversation = FakeConversation()

    class PickerTerminal:
        supports_session_picker = True

        def __init__(self) -> None:
            self.reads = 0
            self.notices: list[tuple[str, str]] = []

        def show_welcome(self, _model: str) -> None:
            pass

        def set_resume_options(self, _options: object) -> None:
            pass

        def read_prompt(self) -> str:
            self.reads += 1
            if self.reads == 1:
                return '/resume'
            raise EOFError

        def select_session(self, _options: object) -> str:
            return 'session-old'

        def show_notice(self, title: str, content: str) -> None:
            self.notices.append((title, content))

        def show_goodbye(self) -> None:
            pass

    terminal = PickerTerminal()

    cli_module.run_interactive_chat(
        session=conversation,
        terminal=terminal,  # type: ignore[arg-type]
        recorder=FakeTrajectoryRecorder(),  # type: ignore[arg-type]
    )

    assert ('resume', 'session-old') in conversation.session_actions
    assert ('Session', 'Resumed session-old.') in terminal.notices
    assert conversation.prompts == []


def test_interactive_conversation_continues_after_model_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = FakeConversation(
        RuntimeError('provider unavailable'),
        turn('Recovered', input_tokens=12, output_tokens=4),
    )
    monkeypatch.setattr(
        cli_module,
        'Conversation',
        lambda **_kwargs: conversation,
    )

    result = runner.invoke(app, input='first\nsecond\n')

    assert result.exit_code == 0
    assert 'Model request failed: provider unavailable' in result.output
    assert 'Recovered' in result.output
    assert 'total 16' in result.output
    assert conversation.prompts == ['first', 'second']


def test_interactive_conversation_explains_missing_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_conversation(**_kwargs: object) -> None:
        raise ConfigurationError('ANTHROPIC_API_KEY is not set.')

    monkeypatch.setattr(cli_module, 'Conversation', missing_conversation)

    result = runner.invoke(app)

    assert result.exit_code == 1
    assert 'Model configuration is incomplete.' in result.output
    assert 'ANTHROPIC_API_KEY is not set.' in result.output


def test_cli_help() -> None:
    result = runner.invoke(app, ['--help'])

    assert result.exit_code == 0
    assert 'ForgeCode terminal Agent Harness.' in result.stdout
    assert '--version' in result.stdout
    assert '--prompt' not in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ['--version'])

    assert result.exit_code == 0
    assert 'ForgeCode 0.1.0' in result.stdout


def test_cli_rejects_removed_prompt_option() -> None:
    result = runner.invoke(app, ['-p', 'hello'])

    assert result.exit_code == 2
    assert 'No such option' in result.output


def test_cli_continue_and_resume_are_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[bool, str | None, bool]] = []

    def fake_chat(
        *,
        continue_session: bool = False,
        resume_identifier: str | None = None,
        fork_session: bool = False,
    ) -> None:
        calls.append(
            (continue_session, resume_identifier, fork_session)
        )

    monkeypatch.setattr(cli_module, 'run_interactive_chat', fake_chat)

    continued = runner.invoke(app, ['--continue'])
    resumed = runner.invoke(app, ['--resume', 'session-abc'])
    forked = runner.invoke(app, ['--continue', '--fork-session'])

    assert continued.exit_code == 0
    assert resumed.exit_code == 0
    assert forked.exit_code == 0
    assert calls == [
        (True, None, False),
        (False, 'session-abc', False),
        (True, None, True),
    ]


def test_cli_rejects_fork_without_resume() -> None:
    result = runner.invoke(app, ['--fork-session'])

    assert result.exit_code == 2
    assert 'requires --continue or --resume' in result.output


def test_sessions_command_lists_project_sessions() -> None:
    journal = SessionStore(Path.cwd()).create(
        model='test-model',
        name='listed-session',
    )

    result = runner.invoke(app, ['sessions'])

    assert result.exit_code == 0
    assert journal.session_id in result.output
    assert 'listed-session' in result.output


def test_config_command_reports_ready_without_exposing_api_key() -> None:
    result = runner.invoke(
        app,
        ['config'],
        env={
            'ANTHROPIC_API_KEY': 'secret-test-key',
            'MODEL_ID': 'claude-test',
            'ANTHROPIC_BASE_URL': 'https://gateway.example.com/anthropic/',
            'MODEL_CONTEXT_WINDOW': '',
        },
    )

    assert result.exit_code == 0
    assert 'Anthropic configuration is ready.' in result.stdout
    assert 'Model ID: claude-test' in result.stdout
    assert 'https://gateway.example.com/anthropic' in result.stdout
    assert 'API key: configured' in result.stdout
    assert 'Max output tokens: 8,192' in result.stdout
    assert 'Context window: not configured' in result.stdout
    assert 'secret-test-key' not in result.stdout


def test_config_command_explains_missing_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('MODEL_ID', raising=False)
    monkeypatch.delenv('ANTHROPIC_BASE_URL', raising=False)

    result = runner.invoke(app, ['config'])

    assert result.exit_code == 1
    assert 'Model configuration is incomplete.' in result.output
    assert 'ANTHROPIC_API_KEY is not set.' in result.output


def test_config_command_explains_missing_model_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.delenv('MODEL_ID', raising=False)
    monkeypatch.delenv('ANTHROPIC_BASE_URL', raising=False)

    result = runner.invoke(app, ['config'])

    assert result.exit_code == 1
    assert 'MODEL_ID is not set.' in result.output
