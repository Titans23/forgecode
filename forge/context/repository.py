'''Repository instructions and durable Markdown memory.'''

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re


MEMORY_TYPES = frozenset({'user', 'feedback', 'project', 'reference'})
SECRET_PATTERN = re.compile(
    r'(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*\S+'
    r'|sk-[a-z0-9_-]{8,}|-----BEGIN [A-Z ]+PRIVATE KEY-----'
)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    name: str
    description: str
    memory_type: str
    content: str
    path: Path


@dataclass(frozen=True, slots=True)
class MemorySelection:
    records: tuple[MemoryRecord, ...]
    rendered: str


class MemoryStore:
    '''Store durable project knowledge as inspectable Markdown files.'''

    def __init__(
        self,
        root: Path,
        *,
        max_selected: int = 5,
        max_file_characters: int = 4_096,
        max_total_characters: int = 20_000,
    ) -> None:
        self.root = root.resolve()
        self.directory = self.root / '.forge' / 'memory'
        self.index_path = self.directory / 'MEMORY.md'
        self.max_selected = max_selected
        self.max_file_characters = max_file_characters
        self.max_total_characters = max_total_characters

    def list(self) -> tuple[MemoryRecord, ...]:
        if not self.directory.exists():
            return ()
        records: list[MemoryRecord] = []
        for path in sorted(self.directory.glob('*.md')):
            if path.name == 'MEMORY.md':
                continue
            try:
                records.append(parse_memory(path))
            except ValueError:
                continue
        return tuple(records)

    def remember(
        self,
        name: str,
        content: str,
        *,
        description: str = '',
        memory_type: str = 'project',
    ) -> MemoryRecord:
        clean_name = name.strip()
        clean_content = content.strip()
        if not clean_name:
            raise ValueError('Memory name must not be empty.')
        if not clean_content:
            raise ValueError('Memory content must not be empty.')
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f'Unsupported memory type: {memory_type}')
        if SECRET_PATTERN.search(clean_content):
            raise ValueError('Memory content appears to contain a secret.')
        existing = next(
            (record for record in self.list() if record.name == clean_name),
            None,
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        path = existing.path if existing is not None else (
            self.directory / f'{memory_slug(clean_name)}.md'
        )
        resolved_description = description.strip() or clean_content[:120]
        path.write_text(
            render_memory(
                clean_name,
                resolved_description,
                memory_type,
                clean_content,
            ),
            encoding='utf-8',
        )
        self.rebuild_index()
        if len(self.list()) >= 10:
            self.consolidate()
        return parse_memory(path)

    def get(self, name: str) -> MemoryRecord | None:
        normalized = name.strip().casefold()
        return next(
            (
                record
                for record in self.list()
                if record.name.casefold() == normalized
                or record.path.stem.casefold() == normalized
            ),
            None,
        )

    def forget(self, name: str) -> bool:
        record = self.get(name)
        if record is None:
            return False
        record.path.unlink()
        self.rebuild_index()
        return True

    def select(self, query: str) -> MemorySelection:
        query_terms = search_terms(query)
        if not query_terms:
            return MemorySelection((), '')
        ranked: list[tuple[int, str, MemoryRecord]] = []
        for record in self.list():
            score = (
                5 * len(query_terms & search_terms(record.name))
                + 3 * len(query_terms & search_terms(record.description))
                + len(query_terms & search_terms(record.content))
            )
            if score:
                ranked.append((score, record.name.casefold(), record))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected: list[MemoryRecord] = []
        sections: list[str] = []
        total = 0
        for _, _, record in ranked[:self.max_selected]:
            content = record.content[:self.max_file_characters]
            section = f'### {record.name}\n{content}'
            if total + len(section) > self.max_total_characters:
                break
            selected.append(record)
            sections.append(section)
            total += len(section)
        rendered = '\n\n'.join(sections)
        return MemorySelection(tuple(selected), rendered)

    def rebuild_index(self) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        lines = ['# ForgeCode Repository Memory', '']
        for record in self.list():
            relative = record.path.name
            lines.append(
                f'- [{record.name}]({relative}) — '
                f'{record.description} ({record.memory_type})'
            )
        self.index_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return self.index_path

    def consolidate(self) -> int:
        '''Remove exact duplicate content and rebuild the stable index.'''
        seen: dict[str, MemoryRecord] = {}
        removed = 0
        for record in self.list():
            digest = hashlib.sha256(
                record.content.strip().encode('utf-8')
            ).hexdigest()
            if digest in seen:
                record.path.unlink()
                removed += 1
            else:
                seen[digest] = record
        self.rebuild_index()
        return removed


class RepositoryContext:
    '''Load scoped repository instructions and query-relevant memory.'''

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.memory = MemoryStore(self.root)
        self._scopes: set[Path] = set()

    def observe_paths(self, paths) -> None:
        """Activate only ancestor rules of paths actually used by this session."""
        for raw in paths:
            candidate = (self.root / raw).resolve(strict=False)
            if not candidate.is_relative_to(self.root):
                continue
            directory = candidate if candidate.is_dir() else candidate.parent
            while directory != self.root and directory.is_relative_to(self.root):
                self._scopes.add(directory)
                directory = directory.parent

    def instructions(self) -> str:
        paths = [self.root / 'AGENTS.md', self.root / 'FORGE.md']
        rules = self.root / '.forge' / 'rules'
        if rules.exists():
            paths.extend(sorted(rules.glob('*.md')))
        for directory in sorted(self._scopes, key=lambda path: (len(path.parts), path.as_posix())):
            paths.extend((directory / 'AGENTS.md', directory / 'FORGE.md'))
        sections: list[str] = []
        total = 0
        for path in paths:
            if not path.is_file() or not path.resolve().is_relative_to(self.root):
                continue
            if path.is_symlink() or path.stat().st_nlink > 1:
                raise ValueError(f'Linked repository instructions are not supported: {path.relative_to(self.root)}')
            with path.open(encoding='utf-8') as stream:
                content = stream.read(16_001)
            if len(content) > 16_000:
                raise ValueError(f'Repository instruction file exceeds 16000 characters: {path.relative_to(self.root)}')
            if total + len(content) > 32_000:
                raise ValueError('Applicable repository instructions exceed the 32000-character budget')
            scope = '.' if path.parent == rules else path.parent.relative_to(self.root).as_posix()
            sections.append(f'## {path.relative_to(self.root).as_posix()}\n'
                            f'Applies to {scope}/ and descendants; deeper instructions apply within their own scope.\n{content}')
            total += len(content)
        return '\n\n'.join(sections)

    def system_suffix(self, query: str) -> str:
        sections: list[str] = []
        instructions = self.instructions()
        if instructions:
            sections.append('# Repository instructions\n' + instructions)
        selected = self.memory.select(query)
        if selected.rendered:
            sections.append('# Relevant repository memory\n' + selected.rendered)
        return '\n\n'.join(sections)


def render_memory(
    name: str,
    description: str,
    memory_type: str,
    content: str,
) -> str:
    return (
        '---\n'
        f'name: {json.dumps(name, ensure_ascii=False)}\n'
        f'description: {json.dumps(description, ensure_ascii=False)}\n'
        f'type: {json.dumps(memory_type)}\n'
        '---\n\n'
        f'{content.strip()}\n'
    )


def parse_memory(path: Path) -> MemoryRecord:
    text = path.read_text(encoding='utf-8')
    if not text.startswith('---\n') or '\n---\n' not in text[4:]:
        raise ValueError(f'Invalid memory frontmatter: {path}')
    header, content = text[4:].split('\n---\n', 1)
    metadata: dict[str, str] = {}
    for line in header.splitlines():
        key, separator, value = line.partition(':')
        if not separator:
            continue
        try:
            metadata[key.strip()] = str(json.loads(value.strip()))
        except json.JSONDecodeError:
            metadata[key.strip()] = value.strip()
    name = metadata.get('name', '').strip()
    memory_type = metadata.get('type', '').strip()
    if not name or memory_type not in MEMORY_TYPES:
        raise ValueError(f'Invalid memory metadata: {path}')
    return MemoryRecord(
        name=name,
        description=metadata.get('description', '').strip(),
        memory_type=memory_type,
        content=content.strip(),
        path=path,
    )


def memory_slug(name: str) -> str:
    ascii_parts = re.findall(r'[a-z0-9]+', name.casefold())
    if ascii_parts:
        return '-'.join(ascii_parts)[:64]
    digest = hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]
    return f'memory-{digest}'


def search_terms(text: str) -> set[str]:
    lowered = text.casefold()
    terms = set(re.findall(r'[a-z0-9_]{2,}', lowered))
    for chunk in re.findall(r'[\u4e00-\u9fff]+', lowered):
        if len(chunk) == 1:
            terms.add(chunk)
        else:
            terms.update(chunk[index:index + 2] for index in range(len(chunk) - 1))
    return terms
