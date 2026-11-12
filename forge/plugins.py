"""Explicitly trusted Python plugins with session-owned registrations and effects.

This is lifecycle management, not isolation of arbitrary Python code.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import importlib.metadata
import importlib.util
import inspect
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

from jsonschema import Draft202012Validator

from forge.agents import PluginSpec
from forge.execution import SandboxProvider
from forge.hooks.models import HookOutcome
from forge.permissions.policy import PermissionRequest
from forge.tools.base import ToolResult


class PluginError(ValueError):
    pass


def describe_plugin(spec: PluginSpec):
    """Inspect static metadata before importing any plugin code."""
    if not spec.trusted:
        raise PluginError(f'Plugin is not explicitly trusted: {spec.source}')
    if spec.source.startswith('entrypoint:'):
        name = spec.source.split(':', 1)[1]
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', name):
            raise PluginError('Invalid installed plugin id')
        entries = list(importlib.metadata.entry_points(group='forge.plugins.v1', name=name))
        if len(entries) != 1:
            raise PluginError(f'Expected exactly one installed forge.plugins.v1 entry: {name}')
        return {'id': name, 'api_version': 1, 'requires': list(spec.requires)}, entries[0]
    root = Path(spec.source).resolve(strict=True)
    value = json.loads((root / 'forge-plugin.json').read_text(encoding='utf-8'))
    if value.get('api_version') != 1 or type(value.get('api_version')) is not int:
        raise PluginError('Unsupported plugin API version')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', value.get('id', '')):
        raise PluginError('Invalid plugin id')
    entry = value.get('entry_point', 'plugin.py:activate')
    filename, separator, function = entry.partition(':')
    path = (root / filename).resolve(strict=True)
    if not separator or not function.isidentifier() or not path.is_relative_to(root) or path.suffix != '.py':
        raise PluginError('Plugin entry must be an in-package Python file:function')
    required = value.get('requires', [])
    if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
        raise PluginError('Plugin dependencies must be ids')
    return value, (root, path, function)


class PluginTool:
    """Schema-validated trusted callback implementing the existing registry protocol."""
    def __init__(self, owner, name, description, schema, callback, effect, concurrency_safe=False):
        if effect not in {'read_only', 'workspace_write', 'process'}:
            raise PluginError('Unsupported tool effect')
        if not callable(callback) or not isinstance(schema, dict) or schema.get('type') != 'object':
            raise PluginError('A plugin tool requires a callback and an object JSON schema')
        Draft202012Validator.check_schema(schema)
        self.owner, self.name, self.description = owner, name, description
        self.schema, self.callback, self.effect = deepcopy(schema), callback, effect
        self.validator = Draft202012Validator(self.schema)
        if not isinstance(concurrency_safe, bool) and not callable(concurrency_safe):
            raise PluginError('concurrency_safe must be a boolean or argument classifier')
        self.concurrency_safe = concurrency_safe

    def is_concurrency_safe(self, arguments):
        return self.concurrency_safe(deepcopy(arguments)) if callable(self.concurrency_safe) else self.concurrency_safe

    @property
    def definition(self):
        return {'name': self.name, 'description': self.description, 'input_schema': deepcopy(self.schema)}

    @property
    def provenance(self):
        return {'source': 'trusted_plugin', 'plugin': self.owner, 'sandbox_covered': False}

    def permission_request(self, arguments):
        return PermissionRequest(self.name, 'repository.read' if self.effect == 'read_only' else f'plugin.{self.owner}.write',
                                 'low' if self.effect == 'read_only' else 'medium', reason='Explicitly trusted host plugin')

    def audit_arguments(self, arguments):
        from forge.sessions.trajectory import sanitize
        return sanitize(dict(arguments))

    def validate(self, arguments):
        error = next(self.validator.iter_errors(arguments), None)
        if error is not None:
            return ToolResult.fail('invalid_arguments', f'Invalid plugin tool arguments at {list(error.path)}: {error.validator}')
        return None

    async def run(self, arguments):
        invalid = self.validate(arguments)
        if invalid:
            return invalid
        try:
            result = self.callback(deepcopy(arguments))
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, ToolResult):
                result = ToolResult.ok('Plugin tool completed', content=result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))
            return replace(result, metadata={**result.metadata, 'plugin': self.owner,
                'execution_boundary': 'trusted-plugin-host', 'sandbox_covered': False})
        except Exception as error:
            return ToolResult.fail('plugin_error', f'{self.name}: {type(error).__name__}')


class PluginContext:
    def __init__(self, host, plugin_id):
        self._host, self.plugin_id = host, plugin_id

    def _require_active(self):
        if self._host._closing is not None:
            raise PluginError('Plugin scope is closing or closed')

    @property
    def workspace(self):
        return self._host.workspace

    @property
    def data_dir(self):
        path = self._host.data_root / 'plugins' / self.plugin_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def effect(self, dispose):
        self._require_active()
        if not callable(dispose):
            raise PluginError('Effect disposer must be callable')
        self._host.cleanups.append(dispose)

    def register_tool(self, name, description, schema, callback, *, effect='read_only', concurrency_safe=False):
        self._require_active()
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,79}', name):
            raise PluginError('Invalid tool name')
        if name in self._host.tools:
            raise PluginError(f'Duplicate plugin tool: {name}')
        tool = PluginTool(self.plugin_id, name, description, schema, callback, effect, concurrency_safe)
        self._host.tools[name] = tool
        def remove():
            self._host.tools.pop(name, None)
            registry = self._host.registry
            if registry is not None and registry.implementation(name) is tool:
                registry.unregister(name)
        self.effect(remove)
        return tool

    def register_service(self, name, service, *, close=None):
        self._require_active()
        if name in self._host.services:
            raise PluginError(f'Duplicate service: {name}')
        self._host.services[name] = service
        self.effect(lambda: self._host.services.pop(name, None))
        if close:
            self.effect(close)

    def service(self, name):
        if name not in self._host.services:
            raise PluginError(f'Missing service: {name}')
        return self._host.services[name]

    def register_provider(self, name, factory):
        self._require_active()
        if name in self._host.providers:
            raise PluginError(f'Duplicate provider: {name}')
        self._host.providers[name] = factory
        self.effect(lambda: self._host.providers.pop(name, None))

    def register_sandbox_provider(self, name: str, factory: SandboxProvider):
        """Register a trusted execution factory selected explicitly by an Agent."""
        self._require_active()
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', name):
            raise PluginError('Invalid sandbox provider name')
        if name in {'native', 'auto'} or name in self._host.sandbox_providers:
            raise PluginError(f'Duplicate or reserved sandbox provider: {name}')
        if self._host.conversation is not None or not callable(factory):
            raise PluginError('Sandbox providers require callable factories during activation')
        self._host.sandbox_providers[name] = factory
        self.effect(lambda: self._host.sandbox_providers.pop(name, None))

    def on(self, event, callback):
        self._require_active()
        from typing import get_args
        from forge.hooks.models import HookEventName
        if event not in get_args(HookEventName):
            raise PluginError(f'Unknown lifecycle event: {event}')
        item = (event, callback)
        self._host.handlers.append(item)
        self.effect(lambda: self._host.handlers.remove(item))

    async def run_child(self, prompt, definition, *, role='worker'):
        self._require_active()
        if self._host.conversation is None:
            raise PluginError('Runtime is not bound yet')
        return await self._host.conversation.run_child(prompt, definition, role=role)

    def register_completion_check(self, name, callback):
        self._require_active()
        key = self.plugin_id + '.' + name
        if self._host.conversation is not None or key in self._host.checks or not callable(callback):
            raise PluginError('Completion checks require unique names during activation')
        self._host.checks[key] = callback
        def remove():
            self._host.checks.pop(key, None)
            if self._host.conversation:
                self._host.conversation.completion_checks.pop(key, None)
        self.effect(remove)


class PluginHost:
    def __init__(self, *, workspace=None, data_root):
        self.workspace = workspace
        self.data_root = Path(data_root)
        self.tools, self.services, self.providers = {}, {}, {}
        self.sandbox_providers = {}
        self.handlers, self.cleanups = [], []
        self.checks = {}
        self.registry = self.conversation = self.previous_hooks = None
        self._closing = None
        self.loaded = []

    async def load(self, specifications):
        try:
            entries = {}
            for spec in specifications:
                manifest, entry = describe_plugin(spec)
                name = manifest['id']
                if name in entries:
                    raise PluginError(f'Duplicate plugin: {name}')
                entries[name] = (spec, manifest, entry)
            ordered, visiting = [], set()
            def visit(name):
                if name in visiting:
                    raise PluginError(f'Plugin dependency cycle: {name}')
                if name in ordered:
                    return
                if name not in entries:
                    raise PluginError(f'Missing plugin dependency: {name}')
                visiting.add(name)
                for dep in entries[name][1].get('requires', ()):
                    visit(dep)
                visiting.remove(name)
                ordered.append(name)
            for name in entries:
                visit(name)
            for name in ordered:
                spec, manifest, entry = entries[name]
                if isinstance(entry, tuple):
                    root, path, function = entry
                    module_name = '_forge_plugin_' + uuid4().hex
                    module_spec = importlib.util.spec_from_file_location(module_name, path, submodule_search_locations=[str(root)])
                    module = importlib.util.module_from_spec(module_spec)
                    sys.modules[module_name] = module
                    def unload(prefix=module_name):
                        for key in list(sys.modules):
                            if key == prefix or key.startswith(prefix + '.'):
                                del sys.modules[key]
                    self.cleanups.append(unload)
                    module_spec.loader.exec_module(module)
                    activate = getattr(module, function)
                else:
                    activate = entry.load()
                result = activate(PluginContext(self, name), deepcopy(spec.config))
                if inspect.isawaitable(result):
                    result = await asyncio.wait_for(result, timeout=30)
                if result is not None:
                    if not callable(result):
                        raise PluginError('activate must return a disposer or None')
                    self.cleanups.append(result)
                self.loaded.append(name)
        except BaseException as error:
            error.plugin_cleanup = await self.close()
            raise
        return self

    def bind_registry(self, registry):
        self.registry = registry
        for tool in self.tools.values():
            registry.register(tool)

    def bind(self, conversation):
        self.conversation = conversation
        self.previous_hooks = conversation.hook_manager
        conversation.hook_manager = self
        conversation.plugin_host = self
        conversation.completion_checks.update(self.checks)

    async def emit(self, event):
        outcome = await self.previous_hooks.emit(event) if self.previous_hooks else HookOutcome()
        if not outcome.allowed:
            return outcome
        for name, callback in self.handlers:
            if name != event.name:
                continue
            current = replace(event, arguments=outcome.arguments if outcome.arguments is not None else event.arguments)
            result = callback(current)
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=30)
            if result is None:
                continue
            if not isinstance(result, HookOutcome):
                raise PluginError('Lifecycle handler must return HookOutcome or None')
            outcome = replace(result,
                arguments=result.arguments if result.arguments is not None else outcome.arguments,
                additional_context=(*outcome.additional_context, *result.additional_context),
                executions=(*outcome.executions, *result.executions))
            if not outcome.allowed:
                break
        return outcome

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        return await asyncio.shield(self._closing)

    async def _close(self):
        failures = []
        for dispose in reversed(self.cleanups):
            try:
                result = dispose()
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, timeout=10)
            except (Exception, asyncio.CancelledError) as error:
                failures.append(type(error).__name__)
        self.cleanups.clear()
        if self.conversation:
            self.conversation.hook_manager = self.previous_hooks
        return {'state': 'unknown' if failures else 'clean', 'errors': failures}
