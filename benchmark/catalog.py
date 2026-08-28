'''Capability-oriented catalog of external ForgeCode benchmarks.'''

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


BenchmarkStatus = Literal['ready', 'planned', 'not_applicable']


@dataclass(frozen=True, slots=True)
class BenchmarkSpec:
    '''Describe one benchmark without pretending that scores are comparable.'''

    key: str
    name: str
    capability: str
    protocol: str
    dataset: str | None
    metric: str
    status: BenchmarkStatus
    runner: str | None
    requirements: tuple[str, ...]
    notes: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


# Keep dataset identifiers explicit.  A run should record the exact identifier
# (and, for a moving dataset, the resolved Harbor version) in its output notes.
BENCHMARKS: tuple[BenchmarkSpec, ...] = (
    BenchmarkSpec(
        key='aider-polyglot',
        name='Aider Polyglot',
        capability='多语言仓库代码编辑',
        protocol='Harbor + independent verifier; optional Aider repair turn',
        dataset='aider-polyglot',
        metric='verifier reward; strict pass@1 and repaired final pass rate',
        status='ready',
        runner='benchmark.harbor.run_aider',
        requirements=('Docker', 'Harbor 0.18+', 'model API with quota'),
        notes='当前已有适配；默认允许一次 verifier-feedback repair。',
    ),
    BenchmarkSpec(
        key='swe-bench-verified',
        name='SWE-bench Verified',
        capability='真实 GitHub Issue 的仓库级修复',
        protocol='Harbor task environment + issue-specific tests',
        dataset='swe-bench/swe-bench-verified',
        metric='final verifier reward; single-attempt pass rate',
        status='ready',
        runner='benchmark.harbor.run_swebench',
        requirements=('Docker', 'Harbor 0.18+', 'large image/cache disk'),
        notes='不启用 Aider 专用 repair turn，首轮结果直接作为单次尝试指标。',
    ),
    BenchmarkSpec(
        key='terminal-bench-2',
        name='Terminal-Bench 2',
        capability='通用终端任务、脚本、环境与工具协作',
        protocol='Harbor + task-specific terminal verifier',
        dataset='terminal-bench/terminal-bench-2',
        metric='task verifier reward; pass rate by task category',
        status='ready',
        runner='benchmark.harbor.run_terminal',
        requirements=('Docker', 'Harbor 0.18+', 'larger CPU/RAM or cloud sandbox'),
        notes='Terminal-Bench 是持续更新的数据集；发布结果时必须记录精确版本。',
    ),
    BenchmarkSpec(
        key='bfcl-v4',
        name='Berkeley Function Calling Leaderboard V4',
        capability='函数/工具调用、错误恢复与多步工具状态',
        protocol='BFCL native tool-call protocol',
        dataset=None,
        metric='BFCL category accuracy and execution score',
        status='planned',
        runner=None,
        requirements=('BFCL evaluator', 'external tool schema adapter'),
        notes='需要把 ForgeCode 的内部 ToolRegistry 映射为 BFCL 的外部工具状态机。',
    ),
    BenchmarkSpec(
        key='osworld',
        name='OSWorld',
        capability='桌面 GUI、浏览器和多模态计算机操作',
        protocol='OSWorld desktop environment and multimodal agent API',
        dataset=None,
        metric='execution-based task success rate',
        status='not_applicable',
        runner=None,
        requirements=('Linux VM', 'desktop applications', 'vision + GUI action API'),
        notes='ForgeCode 当前是终端 Agent；在加入 computer-use/vision backend 前不报告该分数。',
    ),
)


_BY_KEY = {spec.key: spec for spec in BENCHMARKS}


def get_benchmark(key: str) -> BenchmarkSpec:
    '''Return a benchmark spec or raise a useful error for CLI callers.'''
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        choices = ', '.join(spec.key for spec in BENCHMARKS)
        raise ValueError(f'Unknown benchmark {key!r}; choose from: {choices}') from exc


def ready_benchmarks() -> tuple[BenchmarkSpec, ...]:
    return tuple(spec for spec in BENCHMARKS if spec.status == 'ready')
