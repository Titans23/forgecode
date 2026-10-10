"""Fetch the ForgeCode development branch and continue F31 in a local Codex CLI.

This file also runs standalone from the handoff ZIP using Python 3's standard
library. It never copies credentials, installs tools or starts benchmark jobs.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys


REMOTE = 'https://github.com/Titans23/forgecode.git'
BRANCH = 'main'
PROMPT = '''继续 ForgeCode F31。这是用户选择的另一台 WSL + Docker 评测电脑。
先读取 AGENTS.md、docs/implementation/PLANS.md、progress.json、handoff.md、
docs/implementation/tasks/F31.md 和 docs/experiments/F31-wsl-handoff.md，
若存在 docs/implementation/handoff-win10-wsl-20261009.md，先读取并核对随包工作区增量已恢复。
核对实际 HEAD、工作区和 Linux Docker 服务，再按文档完成剩余代码、回归及正式评测。
用户已授权本次项目工作；具体系统与模型许可见交接中的人类授权记录，保留有限请求、
工具和时间预算。模型连接须在这台机器本地绑定，不能将导入计划当作执行权限。
先补齐官方环境隔离、可信宿主逐请求准入和冻结快照，再实际运行，保留所有尝试与费用。
缺失外部资源记为 blocked，继续独立工作，不伪造评分或把 WSL 结果当 Windows 原生验收。
F28 已随用户迁至同一目标电脑，但须在其 Windows 10 原生环境单独验收。
更新 progress、任务卡、证据和 handoff 后交接。'''


def git(directory, *arguments):
    result = subprocess.run(['git', '-C', str(directory), *arguments], capture_output=True,
                            text=True, encoding='utf-8', timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Git command failed')
    return result.stdout.strip()


def update_checkout(destination):
    destination = destination.expanduser().absolute()
    if destination.is_symlink():
        raise RuntimeError('Choose an explicit checkout directory, not a symlink')
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        git(destination.parent, 'clone', '--single-branch', '--branch', BRANCH, REMOTE, str(destination))
    else:
        if not destination.is_dir() or Path(git(destination, 'rev-parse', '--show-toplevel')).resolve() != destination.resolve():
            raise RuntimeError('Destination must be the existing repository root')
        if git(destination, 'remote', 'get-url', 'origin') != REMOTE:
            raise RuntimeError('Existing checkout has a different origin; no files changed')
        if git(destination, 'branch', '--show-current') != BRANCH:
            raise RuntimeError('Existing checkout is on a different branch; no files changed')
        if git(destination, 'status', '--porcelain', '--untracked-files=all'):
            raise RuntimeError('Existing checkout has uncommitted work; preserve it before updating')
        git(destination, 'fetch', 'origin', BRANCH)
        upstream = git(destination, 'rev-parse', 'FETCH_HEAD')
        ahead = git(destination, 'rev-list', '--count', upstream + '..HEAD')
        if ahead != '0':
            raise RuntimeError('Existing checkout has local commits; reconcile them before updating')
        git(destination, 'merge', '--ff-only', upstream)
    return git(destination, 'rev-parse', 'HEAD')


def start_codex(destination):
    executable = shutil.which('codex')
    if executable is None:
        print('Checkout ready. Codex CLI is missing in this WSL environment. Install/sign in to Codex,')
        print('or open this repository in Codex and continue from docs/experiments/F31-wsl-handoff.md.')
        return 2
    # Inherit the user's model, authentication and permission configuration.
    return subprocess.call([executable, '-C', str(destination), PROMPT], cwd=destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path.home() / 'learn_project' / 'forgecode')
    parser.add_argument('--update-only', action='store_true', help='Fetch code without starting Codex')
    args = parser.parse_args(argv)
    if sys.platform != 'linux' and not args.update_only:
        print('Run this entry inside WSL: python3 resume_f31.py')
        return 2
    try:
        destination = args.workspace.expanduser().absolute()
        revision = update_checkout(destination)
        print('ForgeCode checkout:', destination, flush=True)
        print('GitHub branch:', BRANCH, 'HEAD:', revision, flush=True)
        return 0 if args.update_only else start_codex(destination)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('Handoff blocked:', error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
