"""Explicit adapter capabilities; model capabilities remain unknown unless supplied."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    image_transport: bool | None = None
    model_input_modalities: tuple[str, ...] | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None

    @property
    def accepts_images(self) -> bool:
        return self.image_transport is not False and (
            self.model_input_modalities is None or 'image' in self.model_input_modalities)


def capabilities_for(client) -> ModelCapabilities:
    declared = getattr(client, 'capabilities', None)
    if isinstance(declared, ModelCapabilities):
        return declared
    return ModelCapabilities(image_transport=False if getattr(client, 'provider', '') == 'deepseek' else None,
        context_window=getattr(client, 'context_window', None), max_output_tokens=getattr(client, 'max_tokens', None))
