"""Enforce the dependency boundaries introduced by the Harness refactor."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def runtime_nodes(node):
    """Exclude type-only imports while retaining imports in executable branches."""
    if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == 'TYPE_CHECKING':
        for child in node.orelse:
            yield from runtime_nodes(child)
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from runtime_nodes(child)


def violations(root=ROOT):
    failures = []
    independent = {'messages.py', 'tool_types.py', 'protocol.py'}
    for path in (root / 'forge').rglob('*.py'):
        relative = path.relative_to(root / 'forge')
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        executable = set(runtime_nodes(tree)) if relative.as_posix() == 'runtime/runner.py' else set()
        for node in ast.walk(tree):
            imports = [node.module] if isinstance(node, ast.ImportFrom) and node.module else (
                [name.name for name in node.names] if isinstance(node, ast.Import) else [])
            for module in imports:
                if not module.startswith('forge.'):
                    continue
                forbidden = (relative.as_posix() in independent or
                    relative.parts[0] in {'runtime', 'context', 'tools', 'sandbox', 'sessions'}
                    and module.startswith(('forge.cli', 'forge.application.models')) or
                    relative.parts[0] == 'sandbox' and module == 'forge.runtime.agent_loop' or
                    relative.as_posix() == 'runtime/runner.py' and node in executable
                    and module == 'forge.runtime.agent_loop' or
                    relative.as_posix() == 'application/evaluations.py'
                    and module == 'forge.application.evaluation_client' or
                    relative.as_posix() == 'application/sessions.py'
                    and module == 'forge.engine.event_stream' or
                    relative.as_posix() == 'runtime/state.py'
                    and module in {'forge.runtime.delivery', 'forge.runtime.completion',
                                   'forge.runtime.model_client', 'forge.runtime.providers'} or
                    relative.as_posix() in {'runtime/service_health.py', 'runtime/router.py'}
                    and module == 'forge.runtime.model_client' or
                    relative.as_posix() == 'observability/projection.py'
                    and module == 'forge.engine.persistence' or
                    relative.as_posix() == 'release/toolchains.py'
                    and module == 'forge.sandbox.launcher' or
                    relative.as_posix() in {'runtime/check_contracts.py', 'runtime/delivery.py'}
                    and module == 'forge.runtime.completion')
                if forbidden:
                    failures.append(f'{relative}:{node.lineno}: forbidden dependency {module}')
    return failures


if __name__ == '__main__':
    errors = violations()
    print('\n'.join(errors) if errors else 'Architecture boundaries passed')
    raise SystemExit(bool(errors))
