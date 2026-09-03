'''Runtime assembly for every ForgeCode entry point.

Keeping construction here prevents benchmark/Feishu/MCP callers from importing
the CLI module (and accidentally inheriting CLI-only initialization behavior).
'''

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
from typing import Any, Callable

from forge.channels import load_channel_settings
from forge.config import ForgeConfig
from forge.hooks import HookManager
from forge.mcp import MCPClientManager, load_mcp_servers
from forge.mcp.config import InternalStdioServerConfig
from forge.runtime.agent_loop import Conversation
from forge.runtime.completion import TaskPolicy
from forge.runtime.model_client import AnthropicModelClient
from forge.runtime.profile import ExecutionProfile
from forge.runtime.router import ModelIntentRouter
from forge.sessions.checkpoint import CheckpointStore
from forge.sessions.store import SessionJournal, SessionState, SessionStore
from forge.tools import create_default_registry


def create_runtime(
    root: Path,
    *,
    continue_session: bool = False,
    resume_identifier: str | None = None,
    fork_session: bool = False,
    model_override: str | None = None,
    task_policy: TaskPolicy | None = None,
    execution_profile: ExecutionProfile | None = None,
    allow_container_writes: bool = False,
    conversation_factory: Callable[..., Conversation] | None = None,
) -> tuple[Conversation, SessionJournal, SessionState | None]:
    '''Create a Conversation and all dependencies for any product surface.'''
    if model_override is not None and not fork_session:
        raise ValueError('model_override requires fork_session=True.')
    resolved_override = model_override.strip() if model_override else ''
    store = SessionStore(root)
    registry = create_default_registry(
        root,
        execution_profile=execution_profile,
        allow_container_writes=allow_container_writes,
    )
    hooks = HookManager.from_root(root)
    mcp = MCPClientManager(root, registry, load_runtime_mcp_servers(root))
    conversation_type = conversation_factory or Conversation

    if continue_session or resume_identifier is not None:
        state, journal = store.open(resume_identifier)
        checkpoint = CheckpointStore.for_session(
            root,
            journal.path,
            journal.session_id,
        )
        if fork_session:
            journal = store.fork(
                state,
                messages=list(state.messages),
                task=state.active_task,
                model=resolved_override or state.info.model,
            )
            checkpoint = CheckpointStore.for_session(
                root,
                journal.path,
                journal.session_id,
            )
            state = store.load(journal.session_id)
        config = ForgeConfig.from_env()
        resumed_model = resolved_override or state.info.model
        if resumed_model:
            config = replace(config, model_id=resumed_model)
        model = AnthropicModelClient.from_config(config)
        conversation = conversation_type(
            client=model,
            intent_router=ModelIntentRouter(
                AnthropicModelClient.from_config(config, max_tokens=600)
            ),
            registry=registry,
            initial_messages=list(state.messages),
            active_task=state.active_task,
            session_journal=journal,
            checkpoint_store=checkpoint,
            session_store=store,
            hook_manager=hooks,
            mcp_manager=mcp,
            task_policy=task_policy,
        )
        if not fork_session:
            journal.record_resumed()
        return conversation, journal, state

    config = ForgeConfig.from_env()
    model = AnthropicModelClient.from_config(config)
    conversation = conversation_type(
        client=model,
        intent_router=ModelIntentRouter(
            AnthropicModelClient.from_config(config, max_tokens=600)
        ),
        registry=registry,
        mcp_manager=mcp,
        task_policy=task_policy,
    )
    journal = store.create(model=str(getattr(model, 'model', '')))
    conversation.session_journal = journal
    conversation.session_store = store
    conversation.checkpoint_store = CheckpointStore.for_session(
        root,
        journal.path,
        journal.session_id,
    )
    conversation.hook_manager = hooks
    permission_manager = getattr(conversation, 'permission_manager', None)
    if permission_manager is not None:
        mcp.bind(permission_manager, journal)
    return conversation, journal, None


def load_runtime_mcp_servers(root: Path) -> dict[str, Any]:
    '''Merge project MCP servers with enabled Feishu office sidecars.'''
    servers: dict[str, Any] = dict(load_mcp_servers(root))
    settings = load_channel_settings(root)
    policies = {
        'feishu_document_read': 'read',
        'feishu_document_create': 'write',
        'feishu_document_update': 'write',
        'feishu_message_send': 'write',
    }
    for name, config in settings.channels.items():
        if not config.enabled or config.platform != 'feishu':
            continue
        ready, _ = config.credential_status()
        if not ready:
            continue
        servers.setdefault(
            f'office-{name}',
            InternalStdioServerConfig(
                command=sys.executable,
                args=('-m', 'forge.office.mcp_server'),
                env={
                    'APP_ID': os.environ[config.app_id_env],
                    'APP_SECRET': os.environ[config.app_secret_env],
                },
                toolPolicies=policies,
            ),
        )
    return servers
