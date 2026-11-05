"""Lazy providers and credential-free model selections."""
from dataclasses import asdict, dataclass
from copy import deepcopy
import json
from urllib.parse import urlsplit

from forge.config import ForgeConfig, ConfigurationError, model_environment


class ProviderRegistry:
    def __init__(self):
        self._factories = {}

    def register(self, name, factory):
        if name in self.names or not callable(factory):
            raise ValueError(f'Duplicate or invalid provider: {name}')
        self._factories[name] = factory

    @property
    def names(self):
        return ('anthropic', 'openai_responses', 'openai_chat', 'deepseek', 'gemini', *self._factories)

    def create(self, config, *, max_tokens=None, max_retries=2):
        if type(max_retries) is not int or max_retries < 0:
            raise ValueError('max_retries must be a nonnegative integer')
        try:
            if config.provider in self._factories:
                return self._factories[config.provider](config, max_tokens=max_tokens, max_retries=max_retries)
            if config.provider == 'anthropic':
                from forge.runtime.model_client import AnthropicModelClient
                return AnthropicModelClient.from_config(config, max_tokens=max_tokens, max_retries=max_retries)
            if config.provider in {'openai_responses', 'openai_chat', 'deepseek'}:
                from forge.runtime.providers import NativeModelClient
                return NativeModelClient(config, max_tokens=max_tokens, max_attempts=max_retries + 1)
            if config.provider == 'gemini':
                from forge.runtime.gemini import GeminiModelClient
                return GeminiModelClient(config, max_tokens=max_tokens, max_attempts=max_retries + 1)
        except ModuleNotFoundError as error:
            extra = 'anthropic' if config.provider == 'anthropic' else 'gemini' if config.provider == 'gemini' else 'openai'
            raise ConfigurationError(f'Missing provider dependency; install forge-code[{extra}]') from error
        raise ConfigurationError(f'Provider is not registered: {config.provider}')


@dataclass(frozen=True, slots=True)
class ModelSelection:
    provider: str
    model: str
    api_key_env: str | None = None
    base_url: str | None = None
    context_window: int | None = 262144
    max_tokens: int = 65536
    reasoning_effort: str | None = None
    chat_token_field: str = 'max_completion_tokens'
    input_modalities: tuple[str, ...] | None = None

    def __post_init__(self):
        if self.base_url:
            url = urlsplit(self.base_url)
            if url.username or url.password or url.query or url.fragment:
                raise ValueError('Model endpoint must not contain credentials, query or fragment')

    def resolve(self, environment, base=None):
        key = self.api_key_env or {'anthropic': 'ANTHROPIC_API_KEY', 'openai_chat': 'OPENAI_API_KEY',
            'openai_responses': 'OPENAI_API_KEY', 'deepseek': 'DEEPSEEK_API_KEY', 'gemini': 'GEMINI_API_KEY'}.get(self.provider, 'FORGE_API_KEY')
        inherit = self.api_key_env is None and base and base.provider == self.provider and (
            self.base_url is None or self.base_url.rstrip('/') == base.base_url)
        secret = base.api_key if inherit else environment.get(key, '')
        return ForgeConfig(provider=self.provider, model_id=self.model, api_key=secret,
            base_url=self.base_url or (base.base_url if base and base.provider == self.provider else None),
            context_window=self.context_window, max_tokens=self.max_tokens,
            reasoning_effort=self.reasoning_effort, chat_token_field=self.chat_token_field,
            input_modalities=self.input_modalities)


def model_catalog(options):
    from forge.compositions import read_settings
    configured = read_settings().get('models', {})
    if not isinstance(configured, dict) or not isinstance(options.models, dict):
        raise ConfigurationError('Model aliases must be an object')
    result = {**configured, **options.models}
    if 'default' in result:
        raise ConfigurationError('default is reserved for the existing model configuration')
    return result


def initial_config(options):
    environment = model_environment(options.root / '.env' if options.root else None, normalize_aliases=False)
    alias = options.agent.role_models.get('main')
    if alias and alias != 'default':
        values = model_catalog(options)
        if alias not in values:
            raise ConfigurationError(f'Unknown model alias: {alias}')
        return ModelSelection(**values[alias]).resolve(environment, options.config)
    return options.config or ForgeConfig.from_env(model_environment(options.root / '.env' if options.root else None))


def config_for_alias(conversation, alias):
    if alias == 'default':
        return conversation.default_model_config, None
    if alias not in conversation.model_aliases:
        raise ConfigurationError(f'Unknown model alias: {alias}')
    selection = ModelSelection(**conversation.model_aliases[alias])
    return selection.resolve(conversation.model_environment, conversation.default_model_config), selection


def portable_history(messages):
    """Project completed tool history as visible text, retaining all image blocks.

    Foreign provider signatures are not portable. The original structured history
    remains in the Journal; this projection is explicitly recorded on selection.
    """
    result = []
    for message in messages:
        content = message.get('content', '')
        if isinstance(content, str):
            result.append({'role': message['role'], 'content': content})
            continue
        blocks = []
        for block in content:
            if block['type'] in {'text', 'image'}:
                blocks.append(deepcopy(block))
            elif block['type'] == 'tool_use':
                blocks.append({'type': 'text', 'text': '[Earlier tool call] ' + json.dumps(block, ensure_ascii=False)})
            elif block['type'] == 'tool_result':
                value = block.get('content', '')
                blocks.append({'type': 'text', 'text': '[Earlier tool result ' + block['tool_use_id'] + '] '})
                blocks.extend(deepcopy(value) if isinstance(value, list) else [{'type': 'text', 'text': value}])
            else:
                raise ValueError(f'Cannot port historical message block: {block["type"]}')
        result.append({'role': message['role'], 'content': blocks})
    return result


async def select_model(conversation, alias):
    conversation._require_idle()
    if conversation._close_task is not None:
        raise RuntimeError('Session is closing')
    config, selection = config_for_alias(conversation, alias)
    if config == conversation.model_config and alias == conversation.model_alias:
        return {'alias': alias, 'provider': config.provider, 'model': config.model_id, 'changed': False}
    client = conversation.model_factory(config)
    router_client = None
    try:
        if conversation.intent_router is not None:
            router_client = conversation.model_factory(config, max_tokens=600)
        from forge.runtime.capabilities import capabilities_for
        from forge.context.manager import context_stats, request_output_tokens
        history = portable_history(conversation.messages)
        has_images = any(isinstance(m['content'], list) and any(p['type'] == 'image' for p in m['content']) for m in history)
        if has_images and (not capabilities_for(client).accepts_images or not config.input_modalities or
                           'image' not in config.input_modalities):
            raise ValueError('Target must explicitly declare image input support for this history')
        stats = context_stats(history, system_prompt=conversation.system_prompt,
            tools=conversation._tool_definitions(), context_window_tokens=config.context_window,
            reserved_output_tokens=config.max_tokens)
        if config.context_window is not None:
            conversation.context.config.pressure_threshold(config.context_window)
        if request_output_tokens(stats) <= 0:
            raise ValueError('Target context window is too small; compact explicitly or open a new session')
        public = {**(asdict(selection) if selection else {}), 'provider': config.provider, 'model': config.model_id,
            'base_url': config.base_url, 'context_window': config.context_window, 'max_tokens': config.max_tokens,
            'reasoning_effort': config.reasoning_effort, 'chat_token_field': config.chat_token_field,
            'input_modalities': config.input_modalities}
        if conversation.session_journal:
            conversation.session_journal.append('model_selected', {'alias': alias, 'selection': public, 'messages': history})
    except BaseException:
        for candidate in (client, router_client):
            close = getattr(candidate, 'aclose', None)
            if close:
                await close()
        raise
    previous = conversation.client
    conversation.client, conversation.model_config = client, config
    conversation.messages[:] = history
    conversation.model_alias = alias
    conversation.retired_model_clients.append(previous)
    # Retired transports stay owned until session close; no requests use them.
    if conversation.intent_router is not None:
        prior_router = conversation.intent_router.client
        if prior_router is not previous:
            conversation.retired_model_clients.append(prior_router)
        conversation.intent_router.client = router_client
    return {'alias': alias, 'provider': config.provider, 'model': config.model_id, 'changed': True}
