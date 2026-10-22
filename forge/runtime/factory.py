'''Runtime assembly for every ForgeCode entry point.

Keeping construction here prevents benchmark/Feishu/MCP callers from importing
the CLI module (and accidentally inheriting CLI-only initialization behavior).
'''

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from typing import Any, Callable

from forge.config import ForgeConfig, model_environment
from forge.hooks import HookManager
from forge.mcp import MCPClientManager
from forge.runtime.agent_loop import Conversation
from forge.runtime.completion import TaskPolicy
from forge.runtime.dependencies import RuntimeBindings
from forge.runtime.providers import create_model_client
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
    task_relation: str | None = None,
    bindings: RuntimeBindings | None = None,
    defer_new_session: bool = False,
) -> tuple[Conversation, SessionJournal, SessionState | None]:
    '''Assemble new and resumed sessions through the same construction path.'''
    if model_override is not None and not fork_session:
        raise ValueError('model_override requires fork_session=True.')
    config = bindings.config if bindings and bindings.config else ForgeConfig.from_env(model_environment(root / '.env'))
    client_factory = bindings.model_client_factory if bindings and bindings.model_client_factory else create_model_client
    store = SessionStore(root, data_root=bindings.data_root) if bindings else SessionStore(root)
    options = bindings.conversation_options() if bindings else {}
    if task_relation is not None:
        options['task_relation'] = task_relation
    extensions = bindings is None or bindings.trusted_extensions
    if extensions and bindings and bindings.backend and bindings.backend.mode == 'strict':
        from forge.protocol import ContractError
        raise ContractError('Strict execution disables host Hooks and MCP extensions', kind='POLICY_DENIED', code=-32010)

    state = None
    override = model_override.strip() if model_override else ''
    if continue_session or resume_identifier is not None:
        from forge.sessions.store import SessionError
        state, journal = store.open(resume_identifier)
        if defer_new_session and not state.messages and state.active_task is None and not state.pending_inputs:
            raise SessionError('Session has no resumable conversation history.')
        if journal.read_only and not fork_session:
            raise SessionError('Legacy sessions are read-only; use --fork-session with --resume to continue.')
        if state.info.provider and state.info.provider != config.provider and not fork_session:
            from forge.config import ConfigurationError
            raise ConfigurationError('Changing the provider of a stored session requires fork_session=True.')
        if fork_session:
            override = override or os.environ.get('FORGE_MODEL', '') or (
                config.model_id if state.info.provider and state.info.provider != config.provider else '')
            journal = store.fork(state, messages=list(state.messages), task=state.active_task,
                model=override or state.info.model, provider=config.provider)
            state = store.load(journal.session_id)
        if override or state.info.model:
            config = replace(config, model_id=override or state.info.model)

    model = client_factory(config)
    if state is None:
        journal = store.create(model=str(getattr(model, 'model', '')), provider=config.provider,
                               deferred=defer_new_session)
    checkpoint = CheckpointStore.for_session(root, journal.path, journal.session_id)
    registry = create_default_registry(root, execution_profile=execution_profile,
        allow_container_writes=allow_container_writes,
        model_client_factory=lambda: client_factory(replace(config, model_id=model.model)),
        **({'tool_backend': bindings.backend, 'event_recorder': bindings.recorder} if bindings else {}))
    hooks = HookManager.from_root(root) if extensions else None
    mcp = MCPClientManager(root, registry, load_runtime_mcp_servers(root)) if extensions else None
    conversation_type = conversation_factory or Conversation
    conversation = conversation_type(
        client=model, intent_router=(ModelIntentRouter(client_factory(config, max_tokens=600))
            if task_relation is None and (bindings is None or bindings.intent_routing and bindings.task_relation is None) else None),
        registry=registry, initial_messages=list(state.messages) if state else None,
        active_task=state.active_task if state else None, session_journal=journal,
        checkpoint_store=checkpoint, session_store=store, hook_manager=hooks, mcp_manager=mcp,
        task_policy=task_policy, **options)
    if state is not None:
        conversation.verification_history = list(state.verification_history)
        if hasattr(conversation, 'inbox'):
            conversation.inbox.restore(state.pending_inputs)
        if not fork_session:
            journal.record_resumed()
    return conversation, journal, state

def load_runtime_mcp_servers(root: Path) -> dict[str, Any]:
    from forge.integrations import load_runtime_mcp_servers as compose
    return compose(root)
