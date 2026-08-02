'''Tests for ForgeCode skill discovery and progressive loading.'''

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from forge.runtime.agent_loop import Conversation
from forge.runtime.state import (
    ModelTextDelta,
    ModelToolCallCompleted,
    ModelUsageUpdate,
    TokenUsage,
    ToolCall,
    ToolExecutionCompleted,
)
from forge.skills import SkillManager
from forge.skills.manager import SkillError
from forge.tools import create_default_registry


def write_skill(
    root: Path,
    name: str,
    *,
    description: str = 'Run the release workflow.',
    body: str = 'Always run the release checklist.',
    resource: tuple[str, str] | None = None,
) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text(
        (
            '---\n'
            f'name: {name}\n'
            f'description: {description}\n'
            'metadata:\n'
            '  compatibility: test\n'
            '---\n\n'
            f'{body}\n'
        ),
        encoding='utf-8',
    )
    if resource is not None:
        relative, content = resource
        path = directory / relative
        path.parent.mkdir(parents=True)
        path.write_text(content, encoding='utf-8')
    return directory


def test_metadata_is_eager_but_body_requires_activation(tmp_path: Path) -> None:
    project = tmp_path / 'project'
    project.mkdir()
    write_skill(
        project / '.agents' / 'skills',
        'release-check',
        body='PRIVATE RELEASE INSTRUCTIONS',
    )
    manager = SkillManager(
        project,
        user_skills_root=tmp_path / 'user-skills',
    )

    metadata = manager.system_suffix('Prepare a release')
    activated = manager.system_suffix('Use $release-check for this release')

    assert 'release-check: Run the release workflow.' in metadata
    assert 'PRIVATE RELEASE INSTRUCTIONS' not in metadata
    assert '<activated_skill>' in activated
    assert 'PRIVATE RELEASE INSTRUCTIONS' in activated
    assert manager.explicit_names('$release-check then $release-check') == (
        'release-check',
    )
    assert manager.explicit_names('Use $Release-Check') == ('release-check',)


def test_project_skill_overrides_user_skill_deterministically(
    tmp_path: Path,
) -> None:
    project = tmp_path / 'project'
    project.mkdir()
    user_root = tmp_path / 'user-skills'
    write_skill(user_root, 'review-code', body='USER BODY')
    write_skill(
        project / '.forge' / 'skills',
        'review-code',
        body='FORGE PROJECT BODY',
    )
    write_skill(
        project / '.agents' / 'skills',
        'review-code',
        body='VERSIONED PROJECT BODY',
    )

    manager = SkillManager(project, user_skills_root=user_root)

    skill = manager.require('review-code')
    assert skill.source == 'project:.agents'
    assert skill.body == 'VERSIONED PROJECT BODY'
    assert [item.code for item in manager.diagnostics].count(
        'skill_shadowed'
    ) == 2


def test_invalid_skill_is_reported_without_hiding_valid_skills(
    tmp_path: Path,
) -> None:
    project = tmp_path / 'project'
    project.mkdir()
    root = project / '.agents' / 'skills'
    write_skill(root, 'valid-skill')
    broken = root / 'broken-skill'
    broken.mkdir(parents=True)
    (broken / 'SKILL.md').write_text(
        '---\nname: broken-skill\n---\nbody\n',
        encoding='utf-8',
    )

    manager = SkillManager(
        project,
        user_skills_root=tmp_path / 'user-skills',
    )

    assert [skill.name for skill in manager.skills] == ['valid-skill']
    assert manager.diagnostics[0].code == 'skill_description_missing'
    assert 'Diagnostics:' in manager.describe()


def test_uppercase_skill_name_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / 'project'
    project.mkdir()
    root = project / '.agents' / 'skills'
    write_skill(root, 'Release-Check')
    manager = SkillManager(
        project,
        user_skills_root=tmp_path / 'user-skills',
    )

    assert manager.skills == ()
    assert manager.diagnostics[0].code == 'skill_name_invalid'


def test_skill_resources_are_cataloged_and_path_confined(
    tmp_path: Path,
) -> None:
    project = tmp_path / 'project'
    project.mkdir()
    write_skill(
        project / '.agents' / 'skills',
        'deploy-app',
        resource=('references/cloud.md', 'Use the staging account.'),
    )
    manager = SkillManager(
        project,
        user_skills_root=tmp_path / 'user-skills',
    )

    skill = manager.require('deploy-app')
    content, resource = manager.read_resource(
        'deploy-app',
        'references/cloud.md',
    )

    assert [item.path for item in skill.resources] == [
        'references/cloud.md'
    ]
    assert content == 'Use the staging account.'
    assert resource.path == 'references/cloud.md'
    with pytest.raises(SkillError) as traversal:
        manager.read_resource('deploy-app', '../SKILL.md')
    assert traversal.value.code == 'skill_resource_path_invalid'
    with pytest.raises(SkillError) as unknown:
        manager.read_resource('deploy-app', 'references/missing.md')
    assert unknown.value.code == 'skill_resource_not_found'


def test_skill_tools_return_structured_results(tmp_path: Path) -> None:
    write_skill(
        tmp_path / '.agents' / 'skills',
        'test-project',
        resource=('scripts/check.py', 'print("ok")'),
    )
    registry = create_default_registry(tmp_path)

    loaded = asyncio.run(
        registry.execute('load_skill', {'name': 'test-project'})
    )
    resource = asyncio.run(
        registry.execute(
            'read_skill_resource',
            {'skill': 'test-project', 'path': 'scripts/check.py'},
        )
    )
    missing = asyncio.run(
        registry.execute('load_skill', {'name': 'missing-skill'})
    )

    assert loaded.success is True
    assert 'Always run the release checklist.' in loaded.content
    assert loaded.metadata['resources'] == ['scripts/check.py']
    assert resource.success is True
    assert resource.content == 'print("ok")'
    assert missing.success is False
    assert missing.error is not None
    assert missing.error.code == 'skill_not_found'


class CapturingClient:
    model = 'test-model'
    max_tokens = 100
    context_window = 10_000

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def stream(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[object]:
        self.calls.append(
            {'messages': messages, 'tools': tools, 'system': system}
        )
        yield ModelUsageUpdate(
            usage=TokenUsage(input_tokens=10, output_tokens=0)
        )
        yield ModelTextDelta(text='done')
        yield ModelUsageUpdate(
            usage=TokenUsage(input_tokens=10, output_tokens=1)
        )


def collect(conversation: Conversation, prompt: str) -> None:
    async def run() -> None:
        async for _event in conversation.stream(prompt):
            pass

    asyncio.run(run())


def test_conversation_injects_catalog_and_explicit_body(tmp_path: Path) -> None:
    write_skill(
        tmp_path / '.agents' / 'skills',
        'explain-system',
        description='Explain this repository using its architecture rules.',
        body='Read architecture.md before answering.',
    )
    client = CapturingClient()
    conversation = Conversation(
        client=client,  # type: ignore[arg-type]
        registry=create_default_registry(tmp_path),
    )

    collect(conversation, 'Use $explain-system now')

    system = str(client.calls[0]['system'])
    raw_tools = client.calls[0]['tools']
    assert isinstance(raw_tools, list)
    tool_names = {str(item['name']) for item in raw_tools}
    assert '[ForgeCode Skills]' in system
    assert 'Explain this repository using its architecture rules.' in system
    assert 'Read architecture.md before answering.' in system
    assert {'load_skill', 'read_skill_resource'} <= tool_names


class SkillLoadingClient(CapturingClient):
    def __init__(self) -> None:
        super().__init__()
        self.request_index = 0

    async def stream(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[object]:
        self.calls.append(
            {'messages': messages, 'tools': tools, 'system': system}
        )
        self.request_index += 1
        yield ModelUsageUpdate(
            usage=TokenUsage(input_tokens=10, output_tokens=0)
        )
        if self.request_index == 1:
            yield ModelToolCallCompleted(
                tool_call=ToolCall(
                    index=0,
                    id='load-review-code',
                    name='load_skill',
                    arguments={'name': 'review-code'},
                )
            )
        else:
            yield ModelTextDelta(text='review complete')
        yield ModelUsageUpdate(
            usage=TokenUsage(input_tokens=10, output_tokens=2)
        )


def test_implicit_skill_loading_round_trip_uses_read_only_tool(
    tmp_path: Path,
) -> None:
    write_skill(
        tmp_path / '.agents' / 'skills',
        'review-code',
        description='Review code changes for correctness and regressions.',
        body='Inspect the diff and report concrete findings.',
    )
    client = SkillLoadingClient()
    conversation = Conversation(
        client=client,  # type: ignore[arg-type]
        registry=create_default_registry(tmp_path),
    )

    completed: list[ToolExecutionCompleted] = []

    async def run() -> None:
        async for event in conversation.stream('Review this change'):
            if isinstance(event, ToolExecutionCompleted):
                completed.append(event)

    asyncio.run(run())

    assert len(client.calls) == 2
    first_system = str(client.calls[0]['system'])
    assert 'Review code changes for correctness and regressions.' in first_system
    assert 'Inspect the diff and report concrete findings.' not in first_system
    assert len(completed) == 1
    assert completed[0].result.success is True
    assert 'Inspect the diff and report concrete findings.' in (
        completed[0].result.content
    )
    second_messages = client.calls[1]['messages']
    assert any(
        'Inspect the diff and report concrete findings.' in str(message)
        for message in second_messages
    )
