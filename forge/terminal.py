'''Rich terminal presentation for ForgeCode.'''

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import choice
from prompt_toolkit.styles import Style
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from forge import __version__
from forge.permissions.policy import ApprovalResponse, PermissionRequest
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


@dataclass(frozen=True, slots=True)
class SessionOption:
    '''One resumable session shown in completion and picker UIs.'''

    identifier: str
    label: str
    description: str


SLASH_COMMANDS = (
    SlashCommandSpec('/resume ', '/resume [session]', '选择并切换会话上下文'),
    SlashCommandSpec('/status', '/status', '查看当前会话状态'),
    SlashCommandSpec('/history', '/history', '查看当前会话历史'),
    SlashCommandSpec('/rename ', '/rename name', '重命名当前会话'),
    SlashCommandSpec('/branch ', '/branch [name]', '从当前上下文创建会话分支'),
    SlashCommandSpec('/clear', '/clear', '保存当前会话并开始空白会话'),
    SlashCommandSpec('/mcp', '/mcp', '查看 MCP Server 连接和工具状态'),
    SlashCommandSpec(
        '/permission ',
        '/permission [plan|supervised|auto]',
        '查看或切换权限模式',
    ),
    SlashCommandSpec('/undo', '/undo', '撤销最近一次 Agent 代码修改'),
    SlashCommandSpec('/checkpoints', '/checkpoints', '列出文件 Checkpoint'),
    SlashCommandSpec(
        '/rewind ',
        '/rewind [checkpoint] [mode]',
        '恢复代码、对话或两者',
    ),
    SlashCommandSpec('/context', '/context', '查看当前上下文统计'),
    SlashCommandSpec('/compact', '/compact', '立即压缩当前会话'),
    SlashCommandSpec('/task', '/task', '查看当前任务与计划'),
    SlashCommandSpec('/task history', '/task history', '列出已保存的复杂任务'),
    SlashCommandSpec(
        '/task resume ',
        '/task resume task-id',
        '恢复一个已保存的复杂任务',
    ),
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

    def __init__(self) -> None:
        self.session_options: tuple[SessionOption, ...] = ()

    def set_session_options(
        self,
        options: tuple[SessionOption, ...],
    ) -> None:
        self.session_options = options

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
        resume_prefix = '/resume '
        if normalized.startswith(resume_prefix):
            query = text[len(resume_prefix):]
            normalized_query = query.casefold()
            for option in self.session_options:
                searchable = (
                    option.identifier,
                    option.label,
                    option.description,
                )
                if normalized_query and not any(
                    normalized_query in value.casefold()
                    for value in searchable
                ):
                    continue
                yield Completion(
                    option.identifier,
                    start_position=-len(query),
                    display=option.label,
                    display_meta=option.description,
                )
            return
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
        self.slash_completer = SlashCommandCompleter()
        if self.prompt_session is None and self.console.is_terminal:
            self.prompt_session = PromptSession(
                completer=self.slash_completer,
                complete_while_typing=True,
                reserve_space_for_menu=8,
            )

    def set_resume_options(
        self,
        options: tuple[SessionOption, ...],
    ) -> None:
        '''Refresh dynamic session candidates used by Slash completion.'''
        self.slash_completer.set_session_options(options)

    @property
    def supports_session_picker(self) -> bool:
        return self.console.is_terminal and self.prompt_session is not None

    def select_session(
        self,
        options: tuple[SessionOption, ...],
    ) -> str | None:
        '''Open a compact inline picker that always restores the prompt.'''
        self.set_resume_options(options)
        if not options or not self.supports_session_picker:
            return None
        values = [
            (
                option.identifier,
                [
                    ('class:session-name', option.label),
                    ('class:session-meta', '  ' + option.description),
                ],
            )
            for option in options
        ]
        bindings = KeyBindings()

        @bindings.add('escape')
        def cancel(event: Any) -> None:
            event.app.exit(result=None)

        style = Style.from_dict(
            {
                'input-selection': 'bg:default',
                'option': '#d0d0d0',
                'selected-option': '#5fd7ff bold',
                'number': '#666666',
                'session-name': '#f5f5f5 bold',
                'session-meta': '#777777',
                'picker-mark': '#5fd7ff bold',
                'picker-title': '#f5f5f5 bold',
                'bottom-toolbar': 'bg:default #666666',
                'bottom-toolbar.text': 'bg:default #888888',
            }
        )
        try:
            return choice(
                message=[
                    ('class:picker-mark', '◇ '),
                    ('class:picker-title', '切换会话'),
                ],
                options=values,
                default=options[0].identifier,
                symbol='●',
                show_frame=False,
                style=style,
                key_bindings=bindings,
                bottom_toolbar='↑/↓ 选择  Enter 确认  Esc 取消',
            )
        except KeyboardInterrupt:
            return None

    def select_permission(
        self,
        request: PermissionRequest,
    ) -> ApprovalResponse:
        '''Ask for one explicit, scoped permission decision.'''
        if not self.supports_session_picker:
            return ApprovalResponse(
                'deny',
                'Interactive approval is unavailable.',
            )
        target = ', '.join(request.targets) or 'repository'
        self.console.print(
            Panel.fit(
                f'[bold]{escape(request.tool_name)}[/bold]\n'
                f'capability: {escape(request.capability)}\n'
                f'target: {escape(target)}\n'
                f'risk: {escape(request.risk)}\n'
                f'{escape(request.preview)}',
                title='Permission required',
                border_style='yellow',
            )
        )
        values = [
            ('allow_once', '允许一次'),
            ('allow_session', '本会话允许'),
            ('allow_project', '当前项目允许'),
            ('deny', '拒绝'),
        ]
        bindings = KeyBindings()

        @bindings.add('escape')
        def cancel(event: Any) -> None:
            event.app.exit(result='deny')

        try:
            selected = choice(
                message='选择权限操作',
                options=values,
                default='deny',
                show_frame=False,
                key_bindings=bindings,
                bottom_toolbar='↑/↓ 选择  Enter 确认  Esc 拒绝',
            )
        except KeyboardInterrupt:
            selected = 'deny'
        return ApprovalResponse(selected or 'deny')

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

    async def read_prompt_async(self) -> str:
        '''Read input without blocking MCP background notification tasks.'''
        prompt_async = getattr(self.prompt_session, 'prompt_async', None)
        if prompt_async is not None:
            return await prompt_async([('ansibrightcyan bold', '\u276f ')])
        return await asyncio.to_thread(self.read_prompt)

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
        table.add_row('stored messages', f'{stats.stored_messages:,}')
        table.add_row(
            'stored history',
            f'~{stats.stored_tokens:,} tokens '
            f'({stats.stored_characters:,} characters)',
        )
        table.add_row(
            'stored tool results',
            f'{stats.stored_tool_characters:,} chars',
        )
        table.add_row('request messages', f'{stats.message_count:,}')
        table.add_row('system', f'~{stats.system_tokens:,} tokens')
        table.add_row('repository', f'~{stats.repository_tokens:,} tokens')
        table.add_row('tools', f'~{stats.tool_schema_tokens:,} tokens')
        table.add_row(
            'request history',
            f'~{stats.history_tokens:,} tokens '
            f'({stats.estimated_characters:,} characters)',
        )
        table.add_row(
            'request tool results',
            f'{stats.tool_result_characters:,} chars',
        )
        table.add_row('estimated input', f'~{stats.estimated_tokens:,} tokens')
        table.add_row(
            'reserved output',
            f'{stats.reserved_output_tokens:,} tokens',
        )
        table.add_row(
            'projected total',
            f'~{stats.projected_tokens:,} tokens',
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
                'projected utilization',
                f'{(stats.utilization or 0) * 100:.1f}%',
            )
        self.console.print('[bold bright_cyan]Context[/]')
        self.console.print(table)
        self.console.print(
            '[dim]Request values include cheap compaction and match the '
            'automatic compaction threshold. Stored history remains available '
            'locally. The next user prompt is not included.[/]'
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
        self.request_usage: TokenUsage | None = None
        self.model_calls = 0
        self.completed = False
        self.result: TurnResult | None = None
        self.live = Live(
            self._render(),
            console=console,
            refresh_per_second=16,
            vertical_overflow='ellipsis',
            transient=False,
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

    def update_usage(
        self,
        usage: TokenUsage,
        *,
        request_usage: TokenUsage | None = None,
        model_calls: int = 1,
    ) -> None:
        '''Refresh the exact usage snapshot reported by the provider.'''
        self.usage = usage
        self.request_usage = request_usage
        self.model_calls = model_calls
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
        visible_text = ''.join(
            block.text
            for block in self.timeline
            if isinstance(block, _TextTimelineBlock)
        ).strip()
        final_text_is_present = (
            visible_text == result.text
            or (
                bool(self.timeline)
                and isinstance(self.timeline[-1], _TextTimelineBlock)
                and self.timeline[-1].text.strip() == result.text
            )
        )
        if result.text and not final_text_is_present:
            self.timeline.append(_TextTimelineBlock(text=result.text))
        self.usage = result.usage
        self.request_usage = result.last_request_usage
        self.model_calls = result.model_calls
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
            token_usage_summary(
                self.usage,
                streaming=not self.completed,
                request_usage=self.request_usage,
                model_calls=self.model_calls,
            )
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
                diagnostic = result.content.strip()
                if not result.success and diagnostic:
                    if len(diagnostic) > 800:
                        diagnostic = (
                            diagnostic[:800]
                            + '\n...[diagnostic shortened]...'
                        )
                    diagnostic = diagnostic.replace('\n', '\n       ')
                    rendered.append(
                        f'\n       {diagnostic}',
                        style='dim red',
                    )
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
    request_usage: TokenUsage | None = None,
    model_calls: int = 1,
) -> Text:
    '''Build the live or final token usage line.'''
    if request_usage is not None:
        prefix = (
            '\u21b3 turn cumulative (streaming)'
            if streaming
            else '\u21b3 turn cumulative'
        )
    else:
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
    if request_usage is not None:
        if streaming and request_usage.total_tokens == 0:
            summary.append(
                '\n  last request  waiting for provider usage ...',
                style='dim yellow',
            )
            summary.append(f'  {model_calls} model calls', style='dim')
            return summary
        summary.append('\n  last request  input ', style='dim')
        summary.append(
            f'{request_usage.total_input_tokens:,}',
            style='bright_cyan',
        )
        summary.append('  output ', style='dim')
        summary.append(
            f'{request_usage.output_tokens:,}',
            style='bright_cyan',
        )
        summary.append('  turn total above', style='dim')
        summary.append(
            f'  {model_calls} model calls',
            style='dim',
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
