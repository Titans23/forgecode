'''Harbor installed-agent adapter for the current ForgeCode checkout.'''

from __future__ import annotations

import math
from pathlib import Path
import shlex
import shutil
from typing import Final, override

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


_AGENT_ROOT: Final = '/opt/forgecode-agent'
_REMOTE_SOURCE_DIR: Final = '/installed-agent/forgecode-src'
_CACHE_DIR: Final = '/opt/forgecode-cache'
_INSTRUCTION_PATH: Final = '/tmp/forgecode-benchmark-instruction.txt'


class ForgeCodeHarborAgent(BaseInstalledAgent):
    '''Install ForgeCode in each task and run one non-interactive turn.'''

    def __init__(
        self,
        *args,
        max_model_calls: int | str = 120,
        max_tool_calls: int | str = 240,
        max_turn_seconds: float | str = 1800,
        install_retries: int | str = 5,
        install_retry_delay_seconds: float | str = 20,
        source_dir: str | Path | None = None,
        package: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._max_model_calls = _positive_int(
            max_model_calls, 'max_model_calls'
        )
        self._max_tool_calls = _positive_int(max_tool_calls, 'max_tool_calls')
        self._max_turn_seconds = _positive_float(
            max_turn_seconds, 'max_turn_seconds'
        )
        self._install_retries = _positive_int(
            install_retries, 'install_retries'
        )
        self._install_retry_delay_seconds = _positive_float(
            install_retry_delay_seconds, 'install_retry_delay_seconds'
        )
        self._source_dir = (
            Path(source_dir).expanduser().resolve()
            if source_dir is not None
            else _default_source_dir()
        )
        self._package = package

    @staticmethod
    @override
    def name() -> str:
        return 'forgecode'

    @override
    def get_version_command(self) -> str | None:
        return (
            f'{shlex.quote(_venv_python())} -c '
            '"from importlib.metadata import version; '
            "print(version('forge-code'))\""
        )

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        install_spec = await self._prepare_install_spec(environment)
        user = shlex.quote(str(environment.default_user or 'root'))
        await self.exec_as_root(
            environment,
            command=(
                'set -euo pipefail; '
                f'mkdir -p {shlex.quote(_AGENT_ROOT)} '
                f'{shlex.quote(_AGENT_ROOT + "/bin")} '
                f'{shlex.quote(_CACHE_DIR)}; '
                f'chown -R {user}:{user} {shlex.quote(_AGENT_ROOT)}; '
                f'chown {user}:{user} {shlex.quote(_CACHE_DIR)}'
            ),
        )
        await self.exec_as_agent(
            environment,
            command=_install_command(
                install_spec,
                retries=self._install_retries,
                retry_delay_seconds=self._install_retry_delay_seconds,
            ),
        )

    @with_prompt_template
    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del context
        resume = instruction.startswith(
            'The tests are correct. Do not modify the tests.'
        )
        instruction_file = self.logs_dir / 'forgecode-benchmark-instruction.txt'
        instruction_file.write_text(instruction, encoding='utf-8')
        await environment.upload_file(instruction_file, _INSTRUCTION_PATH)
        await self.exec_as_agent(
            environment,
            command=self._run_command(
                resume=resume,
                message_file=_INSTRUCTION_PATH,
            ),
            env={'FORGECODE_DISABLE_GLOBAL_SKILLS': '1'},
        )

    async def _prepare_install_spec(self, environment: BaseEnvironment) -> str:
        if self._package is not None:
            return self._package
        staged = self._stage_local_source()
        await self.exec_as_root(
            environment,
            command=(
                'set -euo pipefail; '
                f'rm -rf {shlex.quote(_REMOTE_SOURCE_DIR)}; '
                f'mkdir -p {shlex.quote(_REMOTE_SOURCE_DIR)}'
            ),
        )
        await environment.upload_dir(staged, _REMOTE_SOURCE_DIR)
        user = shlex.quote(str(environment.default_user or 'root'))
        await self.exec_as_root(
            environment,
            command=(
                f'chown -R {user}:{user} '
                f'{shlex.quote(_REMOTE_SOURCE_DIR)}'
            ),
        )
        return _REMOTE_SOURCE_DIR

    def _stage_local_source(self) -> Path:
        source = self._source_dir
        if source is None:
            raise ValueError('No local ForgeCode source directory is available.')
        required = (
            source / 'pyproject.toml',
            source / 'README.md',
            source / 'forge',
            source / 'benchmark',
        )
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise ValueError(
                'ForgeCode source directory is incomplete; missing '
                + ', '.join(missing)
            )

        staged = self.logs_dir / 'forgecode-source'
        if staged.exists():
            shutil.rmtree(staged)
        staged.mkdir(parents=True)
        shutil.copy2(source / 'pyproject.toml', staged / 'pyproject.toml')
        shutil.copy2(source / 'README.md', staged / 'README.md')
        shutil.copytree(
            source / 'forge',
            staged / 'forge',
            ignore=_ignore_source_artifacts,
        )
        staged_benchmark = staged / 'benchmark'
        staged_benchmark.mkdir()
        shutil.copy2(
            source / 'benchmark' / '__init__.py',
            staged_benchmark / '__init__.py',
        )
        shutil.copytree(
            source / 'benchmark' / 'harbor',
            staged_benchmark / 'harbor',
            ignore=_ignore_source_artifacts,
        )
        return staged

    def _run_command(
        self,
        instruction: str | None = None,
        *,
        resume: bool = False,
        message_file: str | None = None,
    ) -> str:
        if (instruction is None) == (message_file is None):
            raise ValueError(
                'Provide exactly one of instruction or message_file.'
            )
        resume_arg = '--resume ' if resume else ''
        log_name = 'forgecode-repair.txt' if resume else 'forgecode.txt'
        timeout = format(self._max_turn_seconds, 'g')
        baseline = '' if resume else _git_baseline_command()
        message_arg = (
            f'--message-file {shlex.quote(message_file)}'
            if message_file is not None
            else f'--message {shlex.quote(instruction or "")}'
        )
        return (
            'set -o pipefail; '
            f'{baseline}'
            'export FORGE_DATA_DIR=/tmp/forgecode-harbor; '
            'export ANTHROPIC_API_KEY="${FORGECODE_API_KEY}"; '
            'export MODEL_ID="${FORGECODE_MODEL}"; '
            'export ANTHROPIC_BASE_URL="${FORGECODE_BASE_URL}"; '
            'export MODEL_MAX_TOKENS="${FORGECODE_MODEL_MAX_TOKENS:-16384}"; '
            'export MODEL_CONTEXT_WINDOW="${FORGECODE_CONTEXT_WINDOW:-128000}"; '
            f'timeout {shlex.quote(timeout)} '
            f'{shlex.quote(_venv_python())} -m benchmark.harbor.run_forge '
            '--project . '
            f'{resume_arg}'
            f'--max-model-calls {self._max_model_calls} '
            f'--max-tool-calls {self._max_tool_calls} '
            f'{message_arg} '
            f'2>&1 | tee /logs/agent/{log_name}; '
            'FORGECODE_EXIT="${PIPESTATUS[0]}"; '
            'if find /tmp/forgecode-harbor -path "*/sessions/*.jsonl" '
            '  -type f -print -quit 2>/dev/null | grep -q .; then '
            '  find /tmp/forgecode-harbor -path "*/sessions/*.jsonl" '
            '    -type f -exec cp {} /logs/agent/ \\;; '
            'fi; '
            'exit "$FORGECODE_EXIT"'
        )


def _git_baseline_command() -> str:
    return (
        "find . -type f \\( -name '*.sh' -o -name gradlew \\) "
        "  -exec sed -i 's/\\r$//' {} \\; 2>/dev/null || true; "
        'if command -v git >/dev/null 2>&1 && '
        '  ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then '
        '  git init --quiet; '
        '  git config user.email forgecode-harbor@example.test; '
        '  git config user.name "ForgeCode Harbor"; '
        '  git config core.whitespace cr-at-eol; '
        "  printf '\\n.forge/\\n.gradle/\\nbuild/\\ndist/\\nnode_modules/"
        "\\ntarget/\\ncoverage/\\n__pycache__/\\n.pytest_cache/\\n' "
        '    >> .git/info/exclude; '
        '  git add -A; '
        '  git commit --quiet -m "Harbor evaluation baseline"; '
        'fi; '
    )


def _install_command(
    install_spec: str,
    *,
    retries: int = 3,
    retry_delay_seconds: float = 5,
) -> str:
    return (
        'set -euo pipefail; '
        f'AGENT_ROOT={shlex.quote(_AGENT_ROOT)}; '
        f'CACHE_DIR={shlex.quote(_CACHE_DIR)}; '
        'UV_BIN="$AGENT_ROOT/bin/uv"; '
        'attempt=1; '
        'while true; do '
        '  if [ ! -x "$UV_BIN" ]; then '
        '    if [ -f "$CACHE_DIR/bin/uv" ]; then '
        '      cp "$CACHE_DIR/bin/uv" "$UV_BIN"; chmod 755 "$UV_BIN"; '
        '    elif command -v curl >/dev/null 2>&1; then '
        '      if curl -LsSf https://astral.sh/uv/install.sh | '
        '        UV_UNMANAGED_INSTALL="$AGENT_ROOT/bin" sh; then :; fi; '
        '    elif command -v wget >/dev/null 2>&1; then '
        '      if wget -qO- https://astral.sh/uv/install.sh | '
        '        UV_UNMANAGED_INSTALL="$AGENT_ROOT/bin" sh; then :; fi; '
        '    else echo "ForgeCode setup requires cached uv, curl, or wget." >&2; exit 64; fi; '
        '  fi; '
        '  if "$UV_BIN" python install 3.12 && '
        '    PYTHON_BIN="$("$UV_BIN" python find 3.12)"; then '
        '    if "$UV_BIN" venv "$AGENT_ROOT/.venv" '
        '      --python "$PYTHON_BIN" --clear && '
        '      "$UV_BIN" pip install '
        '      --python "$AGENT_ROOT/.venv/bin/python" '
        '      --cache-dir "$CACHE_DIR" '
        f'      {shlex.quote(install_spec)}; then break; fi; '
        '  fi; '
        f'  if [ "$attempt" -ge {retries} ]; then exit 1; fi; '
        f'  sleep "$((attempt * {retry_delay_seconds:g}))"; '
        '  attempt="$((attempt + 1))"; '
        'done'
    )


def _default_source_dir() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    return root if (root / 'pyproject.toml').is_file() else None


def _ignore_source_artifacts(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name == '__pycache__' or name.endswith(('.pyc', '.pyo'))
    }


def _venv_python() -> str:
    return f'{_AGENT_ROOT}/.venv/bin/python'


def _positive_int(value: int | str, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} must be a positive integer') from exc
    if parsed <= 0:
        raise ValueError(f'{name} must be a positive integer')
    return parsed


def _positive_float(value: float | str, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} must be a positive number') from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f'{name} must be a positive number')
    return parsed
