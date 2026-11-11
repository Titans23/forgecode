'''Rich terminal presentation for ForgeCode.'''

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from time import monotonic
from typing import Any, Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import choice
from prompt_toolkit.styles import Style
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from forge import __version__
from forge.permissions.policy import ApprovalResponse, PermissionRequest
from forge.runtime.state import TokenUsage, ToolCall, TurnResult
from forge.context.manager import ContextStats
from forge.context.manager import CompactionReport
from forge.tools.base import ToolResult, is_repository_path_protected


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
    SlashCommandSpec('/history all', '/history all', '显示完整对话'),
    SlashCommandSpec('/details', '/details', '展开最近一轮的思考、工具参数和结果'),
    SlashCommandSpec('/rename ', '/rename name', '重命名当前会话'),
    SlashCommandSpec('/branch ', '/branch [name]', '从当前上下文创建会话分支'),
    SlashCommandSpec('/clear', '/clear', '保存当前会话并开始空白会话'),
    SlashCommandSpec('/skills', '/skills', '列出可用 Skill 与加载诊断'),
    SlashCommandSpec('/skill ', '/skill skill-name', '查看一个 Skill 的完整说明'),
    SlashCommandSpec('/mcp', '/mcp', '查看 MCP Server 连接和工具状态'),
    SlashCommandSpec(
        '/permission ',
        '/permission [workspace-write|danger-full-access|read-only]',
        '查看或切换权限模式',
    ),
    SlashCommandSpec('/sandbox ', '/sandbox [read-only|workspace-write|danger-full-access]', '切换本会话沙盒模式'),
    SlashCommandSpec('/approval ', '/approval [ask|never]', '切换本会话审批策略'),
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
    '''Offer slash commands and working-directory file mentions.'''

    def __init__(self, root: Path | None = None) -> None:
        self.root = root.resolve() if root is not None else None
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
            mention = re.search(r'(?:^|\s)@([^\s]*)$', text)
            if mention is None:
                return
            query = mention.group(1).replace('\\', '/')
            root = self.root or Path.cwd().resolve()
            for candidate in iter_prompt_files(root):
                relative = candidate.relative_to(root).as_posix()
                if not relative.casefold().startswith(query.casefold()):
                    continue
                yield Completion(
                    relative,
                    start_position=-len(mention.group(1)),
                    display='@' + relative,
                    display_meta='文件',
                )
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


def iter_prompt_files(root: Path) -> Iterator[Path]:
    '''Yield mentionable files without following links or exposing secrets.'''
    for directory, directory_names, file_names in os.walk(root):
        current = Path(directory)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not (current / name).is_symlink()
            and not is_repository_path_protected(
                (current / name).relative_to(root)
            )
        )
        for name in sorted(file_names):
            candidate = current / name
            if (
                not candidate.is_symlink()
                and not is_repository_path_protected(candidate.relative_to(root))
            ):
                yield candidate


SLASH_COMMAND_COMPLETER = SlashCommandCompleter()


@dataclass(slots=True)
class _TextTimelineBlock:
    text: str = ''


@dataclass(slots=True)
class _ReasoningTimelineBlock:
    text: str = ''


@dataclass(slots=True)
class _ToolActivity:
    tool_call: ToolCall
    result: ToolResult | None = None


@dataclass(slots=True)
class _ToolTimelineBlock:
    activities: list[_ToolActivity] = field(default_factory=list)


type _TimelineBlock = _TextTimelineBlock | _ReasoningTimelineBlock | _ToolTimelineBlock


class _InteractivePrompt(Protocol):
    def prompt(self, message: Any = '') -> str:
        ...


def permission_choice(*, options, default, key_bindings):
    """An unnumbered approval selector; the reviewed operation stays above it."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.widgets import RadioList
    selection = RadioList(options, default=default, show_numbers=False, select_on_focus=True,
                          open_character='', select_character='›', close_character=' ', show_cursor=False)
    @key_bindings.add('enter', eager=True)
    def accept(event):
        event.app.exit(result=selection.current_value)
    app = Application(layout=Layout(HSplit([
        Window(FormattedTextControl('是否允许本次操作？'), height=1), selection,
        Window(FormattedTextControl('↑/↓ 选择 · Enter 确认 · Y 同意一次 · N/Esc 拒绝'), height=1),
    ]), focused_element=selection), key_bindings=key_bindings, erase_when_done=True,
        style=Style.from_dict({'radio-list': 'bg:default', 'radio': '',
            'radio-selected': 'ansibrightcyan bold', 'radio-checked': 'ansibrightcyan bold'}))
    return app.run()


class TerminalUI:
    '''Render the interactive ForgeCode conversation.'''

    def __init__(
        self,
        console: Console | None = None,
        prompt_session: _InteractivePrompt | None = None,
    ) -> None:
        self.console = console if console is not None else Console()
        self.prompt_session = prompt_session
        self._response_view: StreamingResponseView | None = None
        self._draft = ''
        self._details_session = None
        self.slash_completer = SlashCommandCompleter(Path.cwd())
        if self.prompt_session is None and self.console.is_terminal and not self.console.is_dumb_terminal:
            self.prompt_session = PromptSession(
                completer=self.slash_completer,
                complete_while_typing=True,
                reserve_space_for_menu=8,
            )
            self.prompt_session.app.min_redraw_interval = 1 / 30
        self._bindings = KeyBindings()

        @self._bindings.add('c-o')
        async def details(event):
            view = self._response_view
            if view is not None and not view.completed:
                view.expanded = not view.expanded
                view._refresh()
            else:
                from prompt_toolkit.application import in_terminal
                async with in_terminal():
                    await self.show_details_async()

    def set_resume_options(
        self,
        options: tuple[SessionOption, ...],
    ) -> None:
        '''Refresh dynamic session candidates used by Slash completion.'''
        self.slash_completer.set_session_options(options)

    def bind_session(self, identity) -> None:
        if identity != self._details_session:
            self._response_view = None
            self._details_session = identity

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
        target_lines = list(request.targets[:8]) or ['当前仓库']
        if len(request.targets) > 8:
            target_lines.append(f'… 还有 {len(request.targets) - 8} 个目标')
        risk_labels = {
            'low': '低',
            'medium': '中',
            'high': '高',
            'critical': '严重',
        }
        action_labels = {
            'file.delete': '删除文件',
            'file.write': '修改文件',
            'process.exec': '运行命令',
            'dependency.install': '安装依赖',
                'network.access': '访问网络',
                'mcp.read': '读取外部办公数据',
                'mcp.write': '修改外部办公数据',
        }
        detail = request.preview.strip()
        if detail in {'', '{}'}:
            detail = request.reason or '该操作需要你的明确授权。'
        if request.sandbox_mode:
            detail = request.reason + '\n\n' + detail
        raw_values = [('allow_once', '是，仅允许这一次')]
        if request.sandbox_mode:
            pass  # Wider execution authority is never remembered.
        elif request.capability == 'file.delete':
            if len(request.targets) == 1:
                raw_values.append(
                    ('allow_session', '是，本会话不再询问此目标')
                )
        else:
            raw_values.extend(
                [
                    ('allow_session', '本会话允许相同范围'),
                    ('allow_project', '当前项目记住相同范围'),
                ]
            )
        raw_values.append(('deny', '否，停止本轮操作'))
        values = raw_values
        targets = '\n'.join(f'  • {value}' for value in target_lines)
        self.console.print(
            Panel.fit(
                f'[bold]{escape(action_labels.get(request.capability, request.tool_name))}[/bold]'
                f'  风险：{escape(risk_labels.get(request.risk, request.risk))}\n'
                f'目标（{max(1, len(request.targets))}）：\n{escape(targets)}\n\n'
                f'{escape(detail)}',
                title='需要你的确认',
                border_style='yellow',
                style='default on default',
            )
        )
        bindings = KeyBindings()

        def exit_with(result: str):
            def handler(event: Any) -> None:
                event.app.exit(result=result)

            return handler

        for number, (identifier, _) in enumerate(values, start=1):
            bindings.add(
                str(number),
                eager=True,
            )(exit_with(identifier))
        bindings.add('y', eager=True)(exit_with('allow_once'))
        bindings.add('n', eager=True)(exit_with('deny'))
        bindings.add('escape', eager=True)(exit_with('deny'))
        bindings.add('c-c', eager=True)(exit_with('deny'))

        try:
            selected = permission_choice(
                options=values,
                default='allow_once',
                key_bindings=bindings,
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
        details = Table.grid(padding=(0, 2), expand=True)
        details.add_column(style='dim', no_wrap=True)
        details.add_column(ratio=1)
        details.add_row('模型', Text(model, style='bold bright_white'))
        details.add_row('工作区', Text(str(Path.cwd())))
        self.console.print(Panel(Group(details, Text(''),
            Text('输入问题或描述任务，Enter 开始。', style='bright_cyan')),
            title=title, title_align='left', border_style='bright_cyan', padding=(1, 2),
            width=min(92, self.console.width), subtitle=Text('Ctrl+O 思考与工具详情 · /resume 最近会话', style='dim'),
            subtitle_align='left'))
        self.console.print(Text('  Ctrl+C 取消/退出  ·  Ctrl+D 退出  ·  /details 完整详情', style='dim'))
        self.console.print()

    def show_history(self, session_id, messages, *, notices=(), limit=10):
        """Render saved conversation without replaying model or tool operations."""
        starts = [i for i, message in enumerate(messages) if message.get('role') == 'user'
                  and (isinstance(message.get('content'), str) or any(
                      block.get('type') == 'text' for block in message.get('content', [])
                      if isinstance(block, dict)))]
        omitted = max(0, len(starts) - limit) if limit is not None else 0
        start = starts[omitted] if omitted else 0
        self.console.print(Text(f'History · {session_id}', style='bold cyan'))
        if omitted:
            self.console.print(Text(f'{omitted} earlier turn(s) omitted. Use /history all to view them.', style='dim'))
        for notice in notices:
            self.console.print(Text(notice, style='yellow'))
        tools = {}
        for message in messages[start:]:
            role, content = message.get('role'), message.get('content')
            if role not in {'user', 'assistant'}:
                continue
            blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else content or []
            for block in blocks:
                kind = block.get('type')
                if kind == 'text' and block.get('text'):
                    self.console.print(Text('You' if role == 'user' else 'ForgeCode', style='bold cyan' if role == 'user' else 'bold'))
                    self.console.print(Markdown(block['text']))
                elif kind == 'tool_use':
                    tools[block.get('id')] = block.get('name', 'tool')
                    self.console.print(Text(f"Tool · {block.get('name', 'tool')}", style='dim'))
                elif kind == 'tool_result':
                    result = block.get('content', '')
                    if isinstance(result, list):
                        result = ' '.join(str(item.get('text', '')) for item in result if isinstance(item, dict))
                    brief = ' '.join(str(result).split())[:200]
                    name = tools.get(block.get('tool_use_id'), 'tool')
                    status = 'failed' if block.get('is_error') else 'completed'
                    self.console.print(Text(f'{name} · {status}: {brief}', style='dim'))
        self.console.print(Text('End of saved history', style='dim'))

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
            if not isinstance(self.prompt_session, PromptSession):
                return await prompt_async(self._prompt_message)
            running = self._response_view is not None and not self._response_view.completed
            self.prompt_session.app.erase_when_done = running
            try:
                text = await prompt_async(self._prompt_message, key_bindings=self._bindings,
                    refresh_interval=0.12 if running else 0, default=self._draft)
                self._draft = ''
                if running and text:
                    self.console.print(Text('❯ ' + text, style='bright_cyan'))
                return text
            except asyncio.CancelledError:
                self._draft = self.prompt_session.default_buffer.text
                raise
        return await asyncio.to_thread(self.read_prompt)

    def _prompt_message(self):
        fragments = []
        if self._response_view is not None:
            fragments = self._response_view.prompt_fragments()
        return [*fragments, ('ansibrightcyan bold', '\u276f ')]

    def stream_response(self) -> StreamingResponseView:
        '''Share the input renderer for interactive previews.'''
        prompt = self.prompt_session if isinstance(self.prompt_session, PromptSession) else None
        self._response_view = StreamingResponseView(self.console, prompt=prompt)
        return self._response_view

    async def show_details_async(self) -> None:
        '''Read the last turn's full reasoning and tool results without replaying it.'''
        view = self._response_view
        if view is None:
            self.show_notice('详情', '当前还没有模型响应。')
            return
        content = view.details_text()
        if self.prompt_session is None or not self.console.is_terminal or self.console.is_dumb_terminal:
            self.console.print(Text(content))
            return
        from prompt_toolkit.application import Application
        from prompt_toolkit.layout import Layout, HSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.widgets import TextArea, SearchToolbar
        search = SearchToolbar()
        body = TextArea(text=content, read_only=True, scrollbar=True, search_field=search)
        keys = KeyBindings()
        @keys.add('c-f')
        def find(event):
            from prompt_toolkit.search import start_search
            start_search(body.control)
        @keys.add('escape')
        @keys.add('c-o')
        @keys.add('c-c')
        def close(event):
            event.app.exit()
        app = Application(layout=Layout(HSplit([
            Window(FormattedTextControl(' 思考与工具详情  ·  ↑↓/PgUp/PgDn 滚动  Ctrl+F 搜索  Esc 返回'), height=1),
            body, search,
        ]), focused_element=body), key_bindings=keys, full_screen=True, mouse_support=True)
        await app.run_async()

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
    '''One prompt renderer owns previews; completed transcripts print once.'''

    def __init__(self, console: Console, *, prompt: PromptSession | None = None) -> None:
        self.console = console
        self.prompt = prompt
        self.timeline: list[_TimelineBlock] = []
        self.usage: TokenUsage | None = None
        self.request_usage: TokenUsage | None = None
        self.model_calls = 0
        self.completed = False
        self.cancelled = False
        self._interruption = 'interrupted'
        self.result: TurnResult | None = None
        self._prompt_paused = False
        self._published = False
        self._line_open = False
        self.started = monotonic()
        self.phase = '正在准备请求'
        self.expanded = False
        self.pending_tools: dict[int, tuple[str, int]] = {}

    def __enter__(self) -> StreamingResponseView:
        self.console.print()
        self.console.print(
            Text.assemble(
                ('\u25cf ', 'bold bright_cyan'),
                ('ForgeCode', 'bold bright_white'),
            )
        )
        if self.prompt is None:
            self.console.print(Text('Waiting for model...', style='dim'))
        self._refresh()
        return self

    def __exit__(self, *_: object) -> None:
        if not self.completed:
            self.cancelled = True
            self.completed = True
        self._publish()
        self._prompt_paused = False
        self.console.print()

    def pause_for_prompt(self) -> None:
        '''Hide the preview while an approval prompt owns the input.'''
        self._prompt_paused = True
        self._refresh()

    def resume_after_prompt(self) -> None:
        '''Restore the preview without printing another transcript.'''
        self._prompt_paused = False
        self._refresh()

    def _refresh(self) -> None:
        if self.prompt is not None:
            self.prompt.app.invalidate()

    def prompt_fragments(self):
        if self.prompt is None or self.completed or self._prompt_paused or self.prompt.app.is_done:
            return []
        size = self.prompt.app.output.get_size()
        width = max(1, min(size.columns, self.console.width))
        height = max(1, min(size.rows, self.console.height) - 6)
        # Measure wrapped terminal rows (including wide Chinese characters),
        # not source newlines, so the draft never scrolls into saved output.
        rows = self.console.render_lines(self._render(), self.console.options.update(width=width), pad=False)
        fragments = []
        for row in rows[-height:]:
            for segment in row:
                if segment.control:
                    continue
                style = segment.style
                tokens = []
                if style:
                    if style.color:
                        tokens.append('fg:' + style.color.get_truecolor().hex)
                    if style.bold:
                        tokens.append('bold')
                    if style.italic:
                        tokens.append('italic')
                    if style.dim:
                        tokens.append('fg:#888888')
                fragments.append((' '.join(tokens), segment.text))
            fragments.append(('', '\n'))
        return fragments

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        self._refresh()

    def model_started(self) -> None:
        self.pending_tools.clear()
        self.set_phase('正在等待模型')

    def prepare_tool(self, index: int, name: str) -> None:
        self.pending_tools[index] = (name, 0)
        self.set_phase(f'正在生成参数 · {name} · 未执行')

    def append_tool_arguments(self, index: int, text: str) -> None:
        name, size = self.pending_tools.get(index, ('工具', 0))
        size += len(text.encode('utf-8'))
        self.pending_tools[index] = (name, size)
        self.set_phase(f'正在生成参数 · {name} · {size / 1024:.1f} KB · 未执行')

    def append_reasoning(self, text: str) -> None:
        if not text:
            return
        if self.timeline and isinstance(self.timeline[-1], _ReasoningTimelineBlock):
            self.timeline[-1].text += text
        else:
            self.timeline.append(_ReasoningTimelineBlock(text))
            if self.prompt is None:
                self._end_text()
                self.console.print(Text('思考中…', style='dim'))
        self.set_phase('思考中')

    def details_text(self) -> str:
        sections = []
        for block in self.timeline:
            if isinstance(block, _ReasoningTimelineBlock):
                sections.append('── 思考 ──\n' + block.text)
            elif isinstance(block, _ToolTimelineBlock):
                for activity in block.activities:
                    call, result = activity.tool_call, activity.result
                    status = ('完成' if result.success else '失败') if result else ('已中断，结果未确认' if self.completed else '执行中')
                    sections.append(f'── {call.name} · {status} ──\n'
                        + json.dumps(call.arguments, ensure_ascii=False, indent=2, default=str)
                        + ('\n\n' + result.summary + '\n' + result.content if result else ''))
        if not sections:
            sections.append('本轮没有模型提供的可见思考或工具调用。')
        sections.append(str(token_usage_summary(self.usage, streaming=not self.completed,
            request_usage=self.request_usage, model_calls=self.model_calls)))
        return '\n\n'.join(sections)

    def _end_text(self) -> None:
        if self._line_open:
            self.console.print()
            self._line_open = False

    def _publish(self) -> None:
        if self._published:
            return
        self._published = True
        self._refresh()
        if self.prompt is not None:
            self.console.print(self._render())
        else:
            self._end_text()
            if self.cancelled:
                for block in self.timeline:
                    if isinstance(block, _ToolTimelineBlock):
                        pending = [activity for activity in block.activities if activity.result is None]
                        if pending:
                            self.console.print(self._render_tool_group(_ToolTimelineBlock(pending), limit=None))
                self.console.print(Text(f'↳ {self._interruption} · token usage may be incomplete', style='dim'))
            else:
                if self.result is not None and (self.result.changed_paths or self.result.verification is not None
                                                or self.result.status != 'completed'):
                    self.console.print(completion_evidence_summary(self.result))
                self.console.print(token_usage_summary(self.usage, streaming=False,
                    request_usage=self.request_usage, model_calls=self.model_calls))

    def append_text(self, text: str) -> None:
        '''Append one provider text delta and refresh immediately.'''
        if (
            self.timeline
            and isinstance(self.timeline[-1], _TextTimelineBlock)
        ):
            self.timeline[-1].text += text
        else:
            self.timeline.append(_TextTimelineBlock(text=text))
        if self.prompt is None:
            self.console.print(Text(text), end='', soft_wrap=True)
            if text:
                self._line_open = not text.endswith('\n')
        self.set_phase('正在回答')

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
        self._refresh()

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
        if self.prompt is None:
            self._end_text()
            self.console.print(Text(f'Running {tool_call.name}...', style='dim'))
        self.set_phase('正在运行 ' + tool_call.name)

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
                    if self.prompt is None:
                        self.console.print(self._render_tool_group(_ToolTimelineBlock([activity]), limit=None))
                    self.set_phase('正在处理工具结果')
                    return
        self._refresh()

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
            if self.prompt is None:
                self._end_text()
                self.console.print(Text(result.text))
        self.usage = result.usage
        self.request_usage = result.last_request_usage
        self.model_calls = result.model_calls
        self.result = result
        self.expanded = False
        self.completed = True
        if self.prompt is None:
            self._publish()
        else:
            self._refresh()

    def cancel(self) -> None:
        """Stop progress indicators without inventing a model or tool outcome."""
        self.cancelled = True
        self._interruption = 'cancelled'
        self.expanded = False
        self.completed = True
        if self.prompt is None:
            self._publish()
        else:
            self._refresh()

    def block_completion(self, reasons: tuple[str, ...]) -> None:
        '''Show why a tentative final answer was rejected by the runtime.'''
        details = '\n'.join(f'- {reason}' for reason in reasons)
        self.append_text(f'\nCompletion check: continuing work.\n\n{details}\n')

    def _render(self) -> Group:
        content = self._render_timeline()
        if self.cancelled:
            return Group(content, Text(f'↳ {self._interruption} · token usage may be incomplete', style='dim'))
        renderables: list[object] = [content]
        if self.result is not None and (
            self.result.changed_paths
            or self.result.verification is not None
            or self.result.status != 'completed'
        ):
            renderables.append(completion_evidence_summary(self.result))
        elapsed = monotonic() - self.started
        if not self.completed:
            frames = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
            frame = frames[int(elapsed / .12) % len(frames)]
            renderables.append(Text(f'{frame} {self.phase} · 本轮 {elapsed:.1f}s  '
                + ('Ctrl+O 收起详情' if self.expanded else 'Ctrl+O 展开详情'), style='bright_cyan', no_wrap=True, overflow='ellipsis'))
        else:
            usage = self.usage
            line = f'↳ {elapsed:.1f}s'
            if usage is not None:
                line += f' · ↑ {usage.total_input_tokens:,}  ↓ {usage.output_tokens:,} · 合计 {usage.total_tokens:,} tokens'
            line += f' · {self.model_calls} 次请求 · Ctrl+O 详情'
            renderables.append(Text(line, style='dim'))
        return Group(*renderables)

    def _render_timeline(self) -> Group | Text:
        if not self.timeline:
            if self.cancelled:
                return Group(Text(f'Turn {self._interruption}.', style='yellow'))
            return Text('')
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
                desired_lines = len(self._compact_tools(block).plain.splitlines()) if self.expanded else min(len(block.activities), 6)
            elif isinstance(block, _ReasoningTimelineBlock):
                desired_lines = len(block.text.splitlines()) + 1 if self.expanded else 1
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
        if isinstance(block, _ReasoningTimelineBlock):
            if self.expanded:
                body = block.text if self.completed else streaming_preview(block.text,
                    max_lines=max(4, line_budget or 4),
                    max_characters=max(1000, self.console.width * (line_budget or 4) * 2))
                return Group(Text('▾ 思考', style='dim'), Text(body, style='dim'))
            lines = [line.strip().replace('**', '') for line in block.text.splitlines() if line.strip()]
            summary = (lines[0] if self.completed else lines[-1]) if lines else ''
            if not self.completed:
                summary = streaming_preview(summary, max_lines=1,
                    max_characters=max(20, (self.console.width - 12) // 2)).replace('\n', ' ')
            return Text('▸ 思考 · ' + summary, style='dim', no_wrap=True, overflow='ellipsis')
        if isinstance(block, _TextTimelineBlock):
            if self.completed:
                return Markdown(block.text)
            max_lines = line_budget or 4
            return Markdown(
                streaming_preview(
                    block.text,
                    max_lines=max(20, max_lines * 4),
                    max_characters=max(
                        2000,
                        self.console.width * max_lines * 4,
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
        if self.prompt is not None:
            return self._compact_tools(group)
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
        if pending and self.cancelled:
            rendered.append('？工具已中断，结果需核实', style='yellow')
        elif pending:
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
                rendered.append('? ' if self.cancelled else '● ', style='yellow' if self.cancelled else 'bright_cyan')
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

    def _compact_tools(self, group: _ToolTimelineBlock) -> Text:
        rendered = Text(no_wrap=not self.expanded, overflow='ellipsis' if not self.expanded else 'fold')
        for position, activity in enumerate(group.activities):
            call, result = activity.tool_call, activity.result
            marker = ('✓' if result.success else '×') if result else ('?' if self.cancelled else '●')
            target = next((call.arguments[key] for key in ('path', 'command', 'pattern', 'query')
                           if isinstance(call.arguments.get(key), str)), '')
            if position:
                rendered.append('\n')
            rendered.append('▾ ' if self.expanded else '▸ ', style='dim')
            rendered.append(marker + ' ', style=('green' if result.success else 'red') if result else 'cyan')
            rendered.append(call.name, style='bold')
            if not self.expanded:
                brief = ' '.join(target.split())
                rendered.append('  ' + brief[:max(12, self.console.width - len(call.name) - 12)], style='dim')
                if result is not None and not result.success:
                    rendered.append('\n  ' + result.summary, style='red')
            else:
                body = json.dumps(call.arguments, ensure_ascii=False, indent=2, default=str)
                if result is not None:
                    body += '\n' + result.summary + '\n' + result.content
                if not self.completed:
                    body = streaming_preview(body, max_lines=max(4, self.console.height - 10),
                        max_characters=max(1000, self.console.width * self.console.height * 2))
                rendered.append('\n' + body, style='red' if result is not None and not result.success else 'dim')
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
    if result.completion_report is not None:
        rendered.append('\n  ' + result.completion_report.summary(), style='dim')
    return rendered
