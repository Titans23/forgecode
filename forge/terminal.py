'''Rich terminal presentation for ForgeCode.'''

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from forge import __version__
from forge.runtime.state import TokenUsage, ToolCall, TurnResult
from forge.context.manager import ContextStats
from forge.context.manager import CompactionReport
from forge.tools.base import ToolResult


@dataclass(frozen=True, slots=True)
class SlashCommandSpec:
    '''One discoverable local command shown by the interactive prompt.'''

    completion: str
    usage: str
    description: str


SLASH_COMMANDS = (
    SlashCommandSpec('/context', '/context', '查看当前上下文统计'),
    SlashCommandSpec('/compact', '/compact', '立即压缩当前会话'),
    SlashCommandSpec(
        '/remember ',
        '/remember name | content',
        '保存一条仓库记忆',
    ),
    SlashCommandSpec('/memory list', '/memory list', '列出仓库记忆'),
    SlashCommandSpec(
        '/memory show ',
        '/memory show name',
        '查看一条仓库记忆',
    ),
    SlashCommandSpec(
        '/memory forget ',
        '/memory forget name',
        '删除一条仓库记忆',
    ),
    SlashCommandSpec(
        '/memory rebuild',
        '/memory rebuild',
        '重建记忆索引',
    ),
    SlashCommandSpec(
        '/memory consolidate',
        '/memory consolidate',
        '整理重复记忆',
    ),
)


class SlashCommandCompleter(Completer):
    '''Offer local commands only while the input starts with a slash.'''

    def get_completions(
        self,
        document: Document,
        complete_event: object,
    ):
        del complete_event
        text = document.text_before_cursor
        if not text.startswith('/'):
            return
        normalized = text.casefold()
        for command in SLASH_COMMANDS:
            if not command.completion.casefold().startswith(normalized):
                continue
            yield Completion(
                command.completion,
                start_position=-len(text),
                display=command.usage,
                display_meta=command.description,
            )


SLASH_COMMAND_COMPLETER = SlashCommandCompleter()


@dataclass(slots=True)
class _TextTimelineBlock:
    text: str = ''


@dataclass(slots=True)
class _ToolActivity:
    tool_call: ToolCall
    result: ToolResult | None = None


@dataclass(slots=True)
class _ToolTimelineBlock:
    activities: list[_ToolActivity] = field(default_factory=list)


type _TimelineBlock = _TextTimelineBlock | _ToolTimelineBlock


class _InteractivePrompt(Protocol):
    def prompt(self, message: Any = '') -> str:
        ...


class TerminalUI:
    '''Render the interactive ForgeCode conversation.'''

    def __init__(
        self,
        console: Console | None = None,
        prompt_session: _InteractivePrompt | None = None,
    ) -> None:
        self.console = console if console is not None else Console()
        self.prompt_session = prompt_session
        if self.prompt_session is None and self.console.is_terminal:
            self.prompt_session = PromptSession(
                completer=SLASH_COMMAND_COMPLETER,
                complete_while_typing=True,
                reserve_space_for_menu=8,
            )

    def show_welcome(self, model: str) -> None:
        '''Show a compact session header inspired by modern coding agents.'''
        title = Text.assemble(
            ('\u25c6 ', 'bold bright_cyan'),
            ('ForgeCode', 'bold white'),
            (f' v{__version__}', 'dim'),
        )
        details = Table.grid(padding=(0, 2))
        details.add_column(style='dim', no_wrap=True)
        details.add_column()
        details.add_row('model', Text(model, style='bright_white'))
        details.add_row('cwd', Text(str(Path.cwd()), style='bright_white'))

        self.console.print(
            Panel.fit(
                details,
                title=title,
                subtitle=Text('Ctrl+C to exit', style='dim'),
                border_style='bright_cyan',
                padding=(1, 2),
            )
        )
        self.console.print(
            '[dim]Ask a question or describe a coding task.[/]'
        )
        self.console.print()

    def read_prompt(self) -> str:
        '''Read one message, preserving bracketed multi-line terminal paste.'''
        if self.prompt_session is not None:
            return self.prompt_session.prompt(
                [('ansibrightcyan bold', '\u276f ')]
            )
        return self.console.input('[bold bright_cyan]\u276f[/] ')

    def stream_response(self) -> StreamingResponseView:
        '''Create a live view for one streaming model response.'''
        return StreamingResponseView(self.console)

    def show_error(self, error: Exception) -> None:
        '''Render a recoverable request error without interpreting its markup.'''
        self.console.print(
            f'[bold red]Error[/] [dim]Model request failed:[/] '
            f'{escape(str(error))}'
        )

    def show_goodbye(self) -> None:
        '''Render the session exit message.'''
        self.console.print()
        self.console.print('[dim]Session ended.[/]')

    def show_context(self, stats: ContextStats) -> None:
        '''Render estimated input categories and remaining context capacity.'''
        table = Table.grid(padding=(0, 2))
        table.add_column(style='dim', no_wrap=True)
        table.add_column(style='bright_white', justify='right')
        table.add_row('messages', f'{stats.message_count:,}')
        table.add_row('system', f'~{stats.system_tokens:,} tokens')
        table.add_row('repository', f'~{stats.repository_tokens:,} tokens')
        table.add_row('tools', f'~{stats.tool_schema_tokens:,} tokens')
        table.add_row(
            'history',
            f'~{stats.history_tokens:,} tokens '
            f'({stats.estimated_characters:,} characters)',
        )
        table.add_row(
            'tool results',
            f'{stats.tool_result_characters:,} chars',
        )
        table.add_row('estimated input', f'~{stats.estimated_tokens:,} tokens')
        table.add_row(
            'reserved output',
            f'{stats.reserved_output_tokens:,} tokens',
        )
        if stats.context_window_tokens is None:
            table.add_row('context window', 'not configured')
            table.add_row('remaining', 'unavailable')
        else:
            table.add_row(
                'context window',
                f'{stats.context_window_tokens:,} tokens',
            )
            table.add_row(
                'remaining',
                f'~{stats.remaining_tokens or 0:,} tokens',
            )
            table.add_row(
                'input utilization',
                f'{(stats.utilization or 0) * 100:.1f}%',
            )
        self.console.print('[bold bright_cyan]Context[/]')
        self.console.print(table)
        self.console.print(
            '[dim]Estimated from the latest request snapshot; the next user '
            'prompt is not included.[/]'
        )

    def show_compaction(self, report: CompactionReport) -> None:
        '''Render the result of an explicit /compact request.'''
        if report.success:
            self.console.print(
                '[bold green]Context compacted[/]  '
                f'{report.before_characters:,} → '
                f'{report.after_characters:,} characters'
            )
            if report.transcript_path:
                self.console.print(
                    f'[dim]Full transcript: {report.transcript_path}[/]'
                )
            elif report.reason:
                self.console.print(f'[dim]{report.reason}[/]')
            return
        self.console.print(
            f'[bold red]Compaction failed[/] [dim]{escape(report.reason)}[/]'
        )

    def show_notice(self, title: str, content: str) -> None:
        '''Render a local command result without starting a model turn.'''
        self.console.print(f'[bold bright_cyan]{escape(title)}[/]')
        self.console.print(escape(content))


class StreamingResponseView:
    '''Update streamed Markdown and exact usage in place.'''

    def __init__(self, console: Console) -> None:
        self.console = console
        self.timeline: list[_TimelineBlock] = []
        self.usage: TokenUsage | None = None
        self.completed = False
        self.result: TurnResult | None = None
        self.live = Live(
            self._render(),
            console=console,
            refresh_per_second=16,
            vertical_overflow='ellipsis',
            transient=console.is_terminal,
        )

    def __enter__(self) -> StreamingResponseView:
        self.console.print()
        self.console.print(
            Text.assemble(
                ('\u25cf ', 'bold bright_cyan'),
                ('ForgeCode', 'bold bright_white'),
            )
        )
        self.live.start(refresh=True)
        return self

    def __exit__(self, *_: object) -> None:
        self.live.stop()
        if self.completed and self.console.is_terminal:
            self.console.print(self._render())
        self.console.print()

    def append_text(self, text: str) -> None:
        '''Append one provider text delta and refresh immediately.'''
        if (
            self.timeline
            and isinstance(self.timeline[-1], _TextTimelineBlock)
        ):
            self.timeline[-1].text += text
        else:
            self.timeline.append(_TextTimelineBlock(text=text))
        self.live.update(self._render(), refresh=True)

    def update_usage(self, usage: TokenUsage) -> None:
        '''Refresh the exact usage snapshot reported by the provider.'''
        self.usage = usage
        self.live.update(self._render(), refresh=True)

    def start_tool(self, tool_call: ToolCall) -> None:
        '''Show a model-requested tool while it is executing.'''
        if (
            self.timeline
            and isinstance(self.timeline[-1], _ToolTimelineBlock)
        ):
            group = self.timeline[-1]
        else:
            group = _ToolTimelineBlock()
            self.timeline.append(group)
        group.activities.append(_ToolActivity(tool_call=tool_call))
        self.live.update(self._render(), refresh=True)

    def complete_tool(
        self,
        tool_call: ToolCall,
        result: ToolResult,
    ) -> None:
        '''Replace one pending tool activity with its result summary.'''
        for block in reversed(self.timeline):
            if not isinstance(block, _ToolTimelineBlock):
                continue
            for activity in reversed(block.activities):
                if activity.tool_call.id == tool_call.id:
                    activity.result = result
                    self.live.update(self._render(), refresh=True)
                    return
        self.live.update(self._render(), refresh=True)

    def complete(self, result: TurnResult) -> None:
        '''Finalize the view with validated text and exact final usage.'''
        final_text_is_present = (
            bool(self.timeline)
            and isinstance(self.timeline[-1], _TextTimelineBlock)
            and self.timeline[-1].text.strip() == result.text
        )
        if result.text and not final_text_is_present:
            self.timeline.append(_TextTimelineBlock(text=result.text))
        self.usage = result.usage
        self.result = result
        self.completed = True
        self.live.update(self._render(), refresh=True)

    def block_completion(self, reasons: tuple[str, ...]) -> None:
        '''Show why a tentative final answer was rejected by the runtime.'''
        details = '\n'.join(f'- {reason}' for reason in reasons)
        self.timeline.append(
            _TextTimelineBlock(
                text=f'Completion check: continuing work.\n\n{details}'
            )
        )
        self.live.update(self._render(), refresh=True)

    def _render(self) -> Group:
        content = self._render_timeline()
        renderables: list[object] = [content]
        if self.result is not None and (
            self.result.changed_paths
            or self.result.verification is not None
            or self.result.status != 'completed'
        ):
            renderables.append(completion_evidence_summary(self.result))
        renderables.append(
            token_usage_summary(self.usage, streaming=not self.completed)
        )
        return Group(*renderables)

    def _render_timeline(self) -> Group | Spinner:
        if not self.timeline:
            return Spinner(
                'dots',
                Text('Waiting for model...', style='bright_cyan'),
            )
        if self.completed:
            renderables = [
                self._render_timeline_block(block, line_budget=None)
                for block in self.timeline
            ]
        else:
            renderables = self._render_streaming_timeline()
        separated: list[object] = []
        for position, renderable in enumerate(renderables):
            if position:
                separated.append(Text(''))
            separated.append(renderable)
        return Group(*separated)

    def _render_streaming_timeline(self) -> list[object]:
        remaining_lines = max(4, self.console.height - 4)
        selected: list[tuple[_TimelineBlock, int]] = []
        for block in reversed(self.timeline):
            if isinstance(block, _ToolTimelineBlock):
                desired_lines = min(len(block.activities), 6) + 1
            else:
                desired_lines = max(1, len(block.text.splitlines()))
            line_budget = min(remaining_lines, desired_lines)
            selected.append((block, max(1, line_budget)))
            remaining_lines -= line_budget
            if remaining_lines <= 0:
                break
        selected.reverse()

        renderables: list[object] = []
        if len(selected) < len(self.timeline):
            renderables.append(Text('… earlier activity', style='dim'))
        renderables.extend(
            self._render_timeline_block(block, line_budget=line_budget)
            for block, line_budget in selected
        )
        return renderables

    def _render_timeline_block(
        self,
        block: _TimelineBlock,
        *,
        line_budget: int | None,
    ) -> object:
        if isinstance(block, _TextTimelineBlock):
            if self.completed:
                return Markdown(block.text)
            max_lines = line_budget or 4
            return Text(
                streaming_preview(
                    block.text,
                    max_lines=max_lines,
                    max_characters=max(
                        200,
                        self.console.width * max_lines // 2,
                    ),
                )
            )
        activity_limit = (
            None if line_budget is None else max(1, line_budget - 1)
        )
        return self._render_tool_group(block, limit=activity_limit)

    def _render_tool_group(
        self,
        group: _ToolTimelineBlock,
        *,
        limit: int | None,
    ) -> Text:
        rendered = Text()
        activities = group.activities
        hidden_count = 0
        if limit is not None and len(activities) > limit:
            hidden_count = len(activities) - limit
            activities = activities[-limit:]

        pending = any(activity.result is None for activity in group.activities)
        failed = any(
            activity.result is not None and not activity.result.success
            for activity in group.activities
        )
        count = len(group.activities)
        if pending:
            rendered.append('● ', style='bold bright_cyan')
            rendered.append(
                '正在运行工具' if count == 1 else f'正在运行 {count} 个工具',
                style='bold',
            )
        elif failed:
            rendered.append('× ', style='bold red')
            rendered.append(
                '工具执行完成，存在失败',
                style='bold',
            )
        else:
            rendered.append('✓ ', style='bold green')
            rendered.append(
                (
                    f'已运行 {group.activities[0].tool_call.name}'
                    if count == 1
                    else f'已运行 {count} 个工具'
                ),
                style='dim',
            )
        rendered.append('\n')

        if hidden_count:
            rendered.append(
                f'  … {hidden_count} 个更早的工具调用\n',
                style='dim',
            )
        for position, activity in enumerate(activities):
            is_last = position == len(activities) - 1
            rendered.append('  └─ ' if is_last else '  ├─ ', style='dim')
            result = activity.result
            if result is None:
                rendered.append('● ', style='bright_cyan')
            elif result.success:
                rendered.append('✓ ', style='green')
            else:
                rendered.append('× ', style='red')
            rendered.append(activity.tool_call.name, style='bold')
            arguments = json.dumps(
                activity.tool_call.arguments,
                ensure_ascii=False,
                default=str,
            )
            if len(arguments) > 120:
                arguments = f'{arguments[:117]}...'
            rendered.append(f' {arguments}', style='dim')
            if result is not None:
                rendered.append(f' — {result.summary}', style='dim')
            if not is_last:
                rendered.append('\n')
        return rendered


def streaming_preview(
    text: str,
    *,
    max_lines: int,
    max_characters: int,
) -> str:
    '''Return a bounded tail so a live frame stays inside the terminal.'''
    lines = text.splitlines()
    preview = '\n'.join(lines[-max_lines:])
    truncated = len(lines) > max_lines
    if len(preview) > max_characters:
        preview = preview[-max_characters:]
        truncated = True
    if truncated:
        preview = f'…\n{preview.lstrip()}'
    return preview


def token_usage_summary(
    usage: TokenUsage | None,
    *,
    streaming: bool,
) -> Text:
    '''Build the live or final token usage line.'''
    prefix = '\u21b3 streaming' if streaming else '\u21b3 tokens'
    if usage is None:
        return Text.assemble(
            (prefix, 'dim'),
            ('  input ...  output ...  total ...', 'dim'),
        )

    summary = Text.assemble(
        (prefix, 'dim'),
        ('  input ', 'dim'),
        (f'{usage.total_input_tokens:,}', 'bright_cyan'),
        ('  output ', 'dim'),
        (f'{usage.output_tokens:,}', 'bright_cyan'),
        ('  total ', 'dim'),
        (f'{usage.total_tokens:,}', 'bold bright_cyan'),
    )
    if usage.cache_read_input_tokens:
        summary.append('  cache read ', style='dim')
        summary.append(
            f'{usage.cache_read_input_tokens:,}',
            style='bright_cyan',
        )
    if usage.cache_creation_input_tokens:
        summary.append('  cache write ', style='dim')
        summary.append(
            f'{usage.cache_creation_input_tokens:,}',
            style='bright_cyan',
        )
    return summary


def completion_evidence_summary(result: TurnResult) -> Text:
    '''Render the objective completion state below the final answer.'''
    rendered = Text()
    status_style = 'green' if result.status == 'completed' else 'red'
    rendered.append('↳ task ', style='dim')
    rendered.append(result.status, style=f'bold {status_style}')
    if result.changed_paths:
        rendered.append('  changed ', style='dim')
        rendered.append(', '.join(result.changed_paths), style='bright_white')
    if result.verification is not None:
        evidence = result.verification
        rendered.append('\n  verify ', style='dim')
        rendered.append(evidence.command, style='bright_white')
        rendered.append(
            f'  exit {evidence.exit_code}  {evidence.duration_seconds:.3f}s',
            style='dim',
        )
    for reason in result.completion_reasons:
        rendered.append(f'\n  × {reason}', style='red')
    return rendered
