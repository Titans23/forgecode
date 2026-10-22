"""Enforce the dependency boundaries introduced by the Harness refactor."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def violations(root=ROOT):
    failures = []
    independent = {'messages.py', 'tool_types.py', 'protocol.py'}
    for path in (root / 'forge').rglob('*.py'):
        relative = path.relative_to(root / 'forge')
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            imports = [node.module] if isinstance(node, ast.ImportFrom) and node.module else (
                [name.name for name in node.names] if isinstance(node, ast.Import) else [])
            for module in imports:
                if not module.startswith('forge.'):
                    continue
                forbidden = (relative.as_posix() in independent or
                    relative.parts[0] in {'runtime', 'context', 'tools', 'sandbox', 'sessions'}
                    and module.startswith(('forge.cli', 'forge.application.models')) or
                    relative.parts[0] == 'sandbox' and module == 'forge.runtime.agent_loop')
                if forbidden:
                    failures.append(f'{relative}:{node.lineno}: forbidden dependency {module}')
    return failures


if __name__ == '__main__':
    errors = violations()
    print('\n'.join(errors) if errors else 'Architecture boundaries passed')
    raise SystemExit(bool(errors))
