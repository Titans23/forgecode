'''Validated user and project skill discovery with progressive disclosure.'''

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

import yaml


SKILL_NAME_PATTERN = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
EXPLICIT_SKILL_PATTERN = re.compile(
    r'(?<![\w$])\$([a-z0-9]+(?:-[a-z0-9]+)*)\b',
    flags=re.IGNORECASE,
)
FRONTMATTER_PATTERN = re.compile(
    r'\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)(.*)\Z',
    flags=re.DOTALL,
)
RESOURCE_DIRECTORIES = ('scripts', 'references', 'assets')
MAX_SKILLS_PER_ROOT = 100
MAX_SKILL_CHARACTERS = 80_000
MAX_SKILL_BODY_CHARACTERS = 60_000
MAX_DESCRIPTION_CHARACTERS = 1_000
MAX_METADATA_CHARACTERS = 16_000
MAX_RESOURCE_RESULTS = 200
MAX_RESOURCE_CHARACTERS = 30_000


class SkillError(ValueError):
    '''A deterministic skill discovery or loading failure.'''

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class SkillResource:
    '''One safe, repository-independent bundled skill resource.'''

    path: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SkillRecord:
    '''Validated metadata and lazily exposed instructions for one skill.'''

    name: str
    description: str
    body: str
    directory: Path
    source: str
    resources: tuple[SkillResource, ...]

    def render(self) -> str:
        resources = (
            '\n'.join(f'- {item.path}' for item in self.resources)
            if self.resources
            else '- none'
        )
        return (
            f'# Skill: {self.name}\n'
            f'Source: {self.source}\n'
            f'Description: {self.description}\n\n'
            f'{self.body}\n\n'
            '## Bundled resources\n'
            f'{resources}'
        )


@dataclass(frozen=True, slots=True)
class SkillDiagnostic:
    '''A non-fatal discovery problem shown through local skill inspection.'''

    path: str
    code: str
    message: str


class SkillManager:
    '''Discover metadata eagerly and reveal instructions/resources on demand.'''

    def __init__(
        self,
        root: Path,
        *,
        user_skills_root: Path | None = None,
    ) -> None:
        self.root = root.resolve()
        self.user_skills_root = (
            user_skills_root.resolve()
            if user_skills_root is not None
            else (Path.home() / '.forge' / 'skills').resolve()
        )
        self._skills: dict[str, SkillRecord] = {}
        self._diagnostics: list[SkillDiagnostic] = []
        self.refresh()

    @property
    def skills(self) -> tuple[SkillRecord, ...]:
        return tuple(self._skills[name] for name in sorted(self._skills))

    @property
    def diagnostics(self) -> tuple[SkillDiagnostic, ...]:
        return tuple(self._diagnostics)

    def refresh(self) -> None:
        '''Rescan user and project roots with deterministic shadowing.'''
        self._skills.clear()
        self._diagnostics.clear()
        locations = (
            ('user', self.user_skills_root),
            ('project:.forge', self.root / '.forge' / 'skills'),
            ('project:.agents', self.root / '.agents' / 'skills'),
        )
        for source, directory in locations:
            self._discover_root(directory, source)

    def _discover_root(self, directory: Path, source: str) -> None:
        try:
            if not directory.exists():
                return
            if not directory.is_dir() or directory.is_symlink():
                self._diagnostics.append(
                    SkillDiagnostic(
                        path=str(directory),
                        code='skill_root_invalid',
                        message=(
                            'Skill root must be a real directory, not a link.'
                        ),
                    )
                )
                return
            candidates = sorted(
                (
                    path
                    for path in directory.iterdir()
                    if path.is_dir() and not path.is_symlink()
                ),
                key=lambda path: path.name.casefold(),
            )
        except OSError as error:
            self._diagnostics.append(
                SkillDiagnostic(
                    path=str(directory),
                    code='skill_root_unreadable',
                    message=f'Cannot inspect skill root: {error}',
                )
            )
            return
        if len(candidates) > MAX_SKILLS_PER_ROOT:
            self._diagnostics.append(
                SkillDiagnostic(
                    path=str(directory),
                    code='skill_root_truncated',
                    message=(
                        f'Only the first {MAX_SKILLS_PER_ROOT} skill '
                        'directories were inspected.'
                    ),
                )
            )
        for candidate in candidates[:MAX_SKILLS_PER_ROOT]:
            skill_path = candidate / 'SKILL.md'
            try:
                if not skill_path.exists():
                    continue
                record = parse_skill(candidate, source)
            except SkillError as error:
                self._diagnostics.append(
                    SkillDiagnostic(
                        path=str(skill_path),
                        code=error.code,
                        message=str(error),
                    )
                )
                continue
            except OSError as error:
                self._diagnostics.append(
                    SkillDiagnostic(
                        path=str(skill_path),
                        code='skill_unreadable',
                        message=f'Cannot read skill: {error}',
                    )
                )
                continue
            previous = self._skills.get(record.name)
            if previous is not None:
                self._diagnostics.append(
                    SkillDiagnostic(
                        path=str(skill_path),
                        code='skill_shadowed',
                        message=(
                            f'{record.source} skill {record.name!r} overrides '
                            f'{previous.source}.'
                        ),
                    )
                )
            self._skills[record.name] = record

    def get(self, name: str) -> SkillRecord | None:
        return self._skills.get(name.strip().casefold())

    def require(self, name: str) -> SkillRecord:
        normalized = name.strip().casefold()
        record = self.get(normalized)
        if record is None:
            raise SkillError(
                'skill_not_found',
                f'Unknown skill: {name}',
                details={
                    'requested': normalized,
                    'available_skills': sorted(self._skills),
                },
            )
        return record

    def explicit_names(self, prompt: str) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                match.group(1).casefold()
                for match in EXPLICIT_SKILL_PATTERN.finditer(prompt)
            )
        )

    def system_suffix(self, prompt: str) -> str:
        '''Render metadata plus only explicitly requested skill bodies.'''
        if not self._skills and not self.explicit_names(prompt):
            return ''
        lines = [
            '[ForgeCode Skills]',
            'Skills are reusable instructions with progressive disclosure.',
            'Metadata below is always visible. When a skill clearly matches the '
            'request, call load_skill before acting; use read_skill_resource only '
            'for a resource named by the loaded skill. A $skill-name mention '
            'is explicit activation and its instructions are already included.',
            '',
            'Available skills:',
        ]
        total = sum(len(line) + 1 for line in lines)
        for record in self.skills:
            line = f'- {record.name}: {record.description} [{record.source}]'
            if total + len(line) + 1 > MAX_METADATA_CHARACTERS:
                lines.append('- ... additional skill metadata omitted ...')
                break
            lines.append(line)
            total += len(line) + 1

        explicit = self.explicit_names(prompt)
        unknown = [name for name in explicit if name not in self._skills]
        if unknown:
            lines.extend(
                [
                    '',
                    'Unknown explicitly requested skills: ' + ', '.join(unknown),
                    'Do not invent their instructions. Explain the missing skill '
                    'if it is required to complete the request.',
                ]
            )
        for name in explicit:
            record = self._skills.get(name)
            if record is None:
                continue
            lines.extend(
                [
                    '',
                    '<activated_skill>',
                    record.render(),
                    '</activated_skill>',
                ]
            )
        return '\n'.join(lines)

    def describe(self) -> str:
        if not self._skills:
            summary = 'No ForgeCode skills discovered.'
        else:
            summary = '\n'.join(
                f'- {record.name} [{record.source}]: {record.description}'
                for record in self.skills
            )
        if not self._diagnostics:
            return summary
        diagnostics = '\n'.join(
            f'- {item.code}: {item.path} — {item.message}'
            for item in self._diagnostics
        )
        return f'{summary}\n\nDiagnostics:\n{diagnostics}'

    def show(self, name: str) -> str:
        return self.require(name).render()

    def read_resource(self, name: str, raw_path: str) -> tuple[str, SkillResource]:
        record = self.require(name)
        normalized = normalize_resource_path(raw_path)
        known = {item.path: item for item in record.resources}
        resource = known.get(normalized)
        if resource is None:
            raise SkillError(
                'skill_resource_not_found',
                f'Resource {raw_path!r} is not bundled with skill {record.name!r}.',
                details={'available_resources': sorted(known)},
            )
        path = record.directory / PurePosixPath(normalized)
        if path.is_symlink() or not path.is_file():
            raise SkillError(
                'skill_resource_unsafe',
                f'Skill resource is not a safe regular file: {normalized}',
            )
        resolved = path.resolve()
        try:
            resolved.relative_to(record.directory.resolve())
        except ValueError as error:
            raise SkillError(
                'skill_resource_unsafe',
                f'Skill resource escapes its skill directory: {normalized}',
            ) from error
        try:
            content = resolved.read_text(encoding='utf-8')
        except UnicodeDecodeError as error:
            raise SkillError(
                'skill_resource_not_text',
                f'Skill resource is not UTF-8 text: {normalized}',
            ) from error
        except OSError as error:
            raise SkillError(
                'skill_resource_unreadable',
                f'Cannot read skill resource {normalized}: {error}',
            ) from error
        if len(content) > MAX_RESOURCE_CHARACTERS:
            raise SkillError(
                'skill_resource_too_large',
                f'Skill resource exceeds {MAX_RESOURCE_CHARACTERS} characters.',
                details={
                    'path': normalized,
                    'characters': len(content),
                    'maximum': MAX_RESOURCE_CHARACTERS,
                },
            )
        return content, resource


def parse_skill(directory: Path, source: str) -> SkillRecord:
    '''Validate one SKILL.md and catalog safe bundled resources.'''
    skill_path = directory / 'SKILL.md'
    if skill_path.is_symlink() or not skill_path.is_file():
        raise SkillError(
            'skill_file_unsafe',
            'SKILL.md must be a real regular file, not a symbolic link.',
        )
    try:
        text = skill_path.read_text(encoding='utf-8')
    except UnicodeDecodeError as error:
        raise SkillError(
            'skill_not_utf8',
            'SKILL.md must be valid UTF-8 text.',
        ) from error
    if len(text) > MAX_SKILL_CHARACTERS:
        raise SkillError(
            'skill_too_large',
            f'SKILL.md exceeds {MAX_SKILL_CHARACTERS} characters.',
        )
    match = FRONTMATTER_PATTERN.match(text)
    if match is None:
        raise SkillError(
            'skill_frontmatter_missing',
            'SKILL.md must start with YAML frontmatter delimited by three dashes.',
        )
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise SkillError(
            'skill_frontmatter_invalid',
            f'SKILL.md frontmatter is invalid YAML: {error}',
        ) from error
    if not isinstance(metadata, dict):
        raise SkillError(
            'skill_frontmatter_invalid',
            'SKILL.md frontmatter must be a YAML mapping.',
        )
    name = metadata.get('name')
    description = metadata.get('description')
    if not isinstance(name, str) or not name.strip():
        raise SkillError('skill_name_missing', 'Skill name is required.')
    name = name.strip()
    if (
        len(name) > 64
        or name != name.casefold()
        or SKILL_NAME_PATTERN.fullmatch(name) is None
    ):
        raise SkillError(
            'skill_name_invalid',
            'Skill name must use lowercase letters, digits, and single hyphens.',
        )
    if directory.name != name:
        raise SkillError(
            'skill_directory_mismatch',
            f'Skill directory {directory.name!r} must match name {name!r}.',
        )
    if not isinstance(description, str) or not description.strip():
        raise SkillError(
            'skill_description_missing',
            'Skill description is required.',
        )
    description = ' '.join(description.split())
    if len(description) > MAX_DESCRIPTION_CHARACTERS:
        raise SkillError(
            'skill_description_too_large',
            f'Skill description exceeds {MAX_DESCRIPTION_CHARACTERS} characters.',
        )
    body = match.group(2).strip()
    if not body:
        raise SkillError('skill_body_missing', 'SKILL.md body must not be empty.')
    if len(body) > MAX_SKILL_BODY_CHARACTERS:
        raise SkillError(
            'skill_body_too_large',
            f'Skill body exceeds {MAX_SKILL_BODY_CHARACTERS} characters.',
        )
    return SkillRecord(
        name=name,
        description=description,
        body=body,
        directory=directory.resolve(),
        source=source,
        resources=discover_resources(directory),
    )


def discover_resources(directory: Path) -> tuple[SkillResource, ...]:
    resources: list[SkillResource] = []
    for resource_root_name in RESOURCE_DIRECTORIES:
        resource_root = directory / resource_root_name
        if not resource_root.exists() or not resource_root.is_dir():
            continue
        if resource_root.is_symlink():
            continue
        for current, directory_names, file_names in os.walk(
            resource_root,
            followlinks=False,
        ):
            current_path = Path(current)
            directory_names[:] = sorted(
                name
                for name in directory_names
                if not (current_path / name).is_symlink()
            )
            for file_name in sorted(file_names):
                path = current_path / file_name
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(directory).as_posix()
                resources.append(
                    SkillResource(
                        path=relative,
                        size_bytes=path.stat().st_size,
                    )
                )
                if len(resources) >= MAX_RESOURCE_RESULTS:
                    return tuple(resources)
    return tuple(resources)


def normalize_resource_path(raw_path: str) -> str:
    candidate = PurePosixPath(raw_path.replace('\\', '/').strip())
    if (
        not raw_path.strip()
        or candidate.is_absolute()
        or '..' in candidate.parts
        or not candidate.parts
        or candidate.parts[0] not in RESOURCE_DIRECTORIES
    ):
        raise SkillError(
            'skill_resource_path_invalid',
            'Skill resource path must be relative and start with scripts/, '
            'references/, or assets/.',
        )
    return candidate.as_posix()
