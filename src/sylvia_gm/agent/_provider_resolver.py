"""Resolve durable session identities to configured providers inside the owner.

Credentials never cross the owner protocol. This module loads the ordinary
Sylvia GM configuration with dotenv discovery disabled and constructs only the
provider explicitly named by the durable session record. It deliberately does
not install a fallback chain or guess a substitute model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Literal, cast

from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.config.chatgpt_settings import refresh_saved_subscription_reasoning
from sylvia_gm.config.loader import ProviderDef, load_config, provider_credentials
from sylvia_gm.config.provider_settings import discover_provider_models
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.model_capability import (
    EMPTY_MODEL_CAPABILITY_REGISTRY,
    MODEL_CAPABILITY_REGISTRY_VERSION,
    ModelCapabilityRegistry,
)
from sylvia_gm.domain.session import SessionRecord
from sylvia_gm.provider._base import ModelInfo, Provider
from sylvia_gm.provider._context_capacity import (
    CONTEXT_CAPACITY_SOURCES,
    resolve_context_capacity,
)
from sylvia_gm.provider._exceptions import ModelNotFoundError
from sylvia_gm.provider._models import build_catalog
from sylvia_gm.provider._output_policy import DEFAULT_OUTPUT_ALLOWANCE, output_allowance
from sylvia_gm.provider._reasoning import (
    configure_reasoning,
    mode_reasoning_default,
    reasoning_values,
)
from sylvia_gm.provider._registry import ProviderRegistry
from sylvia_gm.provider._request_metadata import (
    SessionBoundRequestMetadataProvider,
    opencode_request_headers,
    without_opencode_protocol_headers,
)

ProviderResolver = Callable[[SessionRecord], Provider]

_LOCAL_PROVIDER_MODES = frozenset({"local", "ollama"})
_OPENCODE_PRESETS = frozenset({"opencode_go", "opencode_zen"})

_MODEL_FIELDS = frozenset(
    {
        "id",
        "name",
        "context_window",
        "declared_context_window",
        "declared_context_source",
        "runtime_context_window",
        "runtime_context_source",
        "configured_context_window",
        "effective_context_window",
        "effective_context_source",
        "context_window_source",
        "declared_context_observed_at",
        "runtime_context_observed_at",
        "configured_context_observed_at",
        "context_observed_at",
        "configured_runtime_mismatch",
        "configured_declared_mismatch",
        "max_output_tokens",
        "declared_max_output_tokens",
        "configured_output_tokens",
        "output_allowance_source",
        "documented_source",
        "documented_at",
        "builder_output_tokens",
        "transport",
        "supports_streaming",
        "supports_tools",
        "supported_reasoning_efforts",
        "default_reasoning_effort",
    }
)
_MODEL_ID_LIMIT = 256
_MODEL_NAME_LIMIT = 128


def _required_model_text(
    value: object,
    *,
    field: str,
    limit: int,
) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigError(f"Configured model {field} must be non-empty trimmed text")
    if len(value) > limit or any(ord(character) < 32 for character in value):
        raise ConfigError(f"Configured model {field} is invalid")
    return value


def _bounded_model_integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"Configured model {field} must be an integer")
    if value < minimum or value > maximum:
        raise ConfigError(f"Configured model {field} is outside the supported range")
    return value


def _optional_model_integer(
    value: object,
    *,
    field: str,
    minimum: int = 512,
    maximum: int = 10_000_000,
) -> int | None:
    if value is None or value == "":
        return None
    return _bounded_model_integer(value, field=field, minimum=minimum, maximum=maximum)


def _capacity_source(value: object, *, fallback: str = "unknown") -> str:
    return value if isinstance(value, str) and value in CONTEXT_CAPACITY_SOURCES else fallback


def _configured_provider_models(
    provider_key: str,
    definition: ProviderDef,
    known_catalog: tuple[ModelInfo, ...],
) -> tuple[ModelInfo, ...]:
    known = tuple(model for model in known_catalog if model.provider_key == provider_key)
    if not definition.models:
        if not known:
            raise ConfigError(f"Enabled provider '{provider_key}' must declare at least one model")
        return known

    provider_prefix = provider_key.replace("_", "-")
    configured: list[ModelInfo] = []
    identities: set[str] = set()
    for row in definition.models:
        unknown = set(row).difference(_MODEL_FIELDS)
        if unknown:
            raise ConfigError(
                "Configured model contains unsupported fields: " + ", ".join(sorted(unknown))
            )
        declared_id = _required_model_text(
            row.get("id"),
            field="id",
            limit=_MODEL_ID_LIMIT,
        )
        known_model = next(
            (
                model
                for model in known
                if model.id == declared_id or model.wire_model_id == declared_id
            ),
            None,
        )
        canonical_id = (
            known_model.id
            if known_model is not None and known_model.id == declared_id
            else f"{provider_prefix}/{declared_id}"
        )
        upstream_id = (
            known_model.wire_model_id
            if known_model is not None and known_model.id == declared_id
            else declared_id
        )
        if canonical_id in identities:
            raise ConfigError(f"Configured model identity is duplicated: {canonical_id}")
        identities.add(canonical_id)

        name = _required_model_text(
            row.get("name", known_model.name if known_model is not None else declared_id),
            field="name",
            limit=_MODEL_NAME_LIMIT,
        )
        known_declared = None
        if known_model is not None:
            known_declared = known_model.declared_context_window or known_model.context_window
        has_capacity_facts = any(
            key in row
            for key in (
                "declared_context_window",
                "runtime_context_window",
                "configured_context_window",
                "effective_context_window",
            )
        )
        declared_context_window = _optional_model_integer(
            row.get("declared_context_window", known_declared),
            field="declared_context_window",
        )
        runtime_context_window = _optional_model_integer(
            row.get("runtime_context_window"),
            field="runtime_context_window",
        )
        configured_context_window = _optional_model_integer(
            row.get("configured_context_window"),
            field="configured_context_window",
        )
        legacy_context_window = (
            _optional_model_integer(row.get("context_window"), field="legacy_context_window")
            if not has_capacity_facts
            else None
        )
        observed_at = row.get("context_observed_at")
        declared_observed_at = row.get("declared_context_observed_at", observed_at)
        runtime_observed_at = row.get("runtime_context_observed_at", observed_at)
        configured_observed_at = row.get("configured_context_observed_at")
        capacity = resolve_context_capacity(
            declared_context_window=declared_context_window,
            declared_context_source=_capacity_source(
                row.get("declared_context_source"),
                fallback="model_catalog" if known_declared is not None else "unknown",
            ),
            runtime_context_window=runtime_context_window,
            runtime_context_source=_capacity_source(row.get("runtime_context_source")),
            configured_context_window=configured_context_window,
            legacy_context_window=legacy_context_window,
            observed_at=observed_at if isinstance(observed_at, str) else None,
            declared_observed_at=(
                declared_observed_at if isinstance(declared_observed_at, str) else None
            ),
            runtime_observed_at=(
                runtime_observed_at if isinstance(runtime_observed_at, str) else None
            ),
            configured_observed_at=(
                configured_observed_at if isinstance(configured_observed_at, str) else None
            ),
        )
        context_window = capacity.effective_context_window
        selected_output = row.get("max_output_tokens")
        if selected_output is None and row.get("transport") != "codex_app_server":
            selected_output = (
                known_model.max_output_tokens if known_model is not None
                else min(DEFAULT_OUTPUT_ALLOWANCE, context_window or DEFAULT_OUTPUT_ALLOWANCE)
            )
        max_output_tokens = _optional_model_integer(
            selected_output,
            field="max_output_tokens",
            minimum=1,
        )
        if (max_output_tokens is not None and context_window is not None
                and row.get("transport") != "codex_app_server" and max_output_tokens > context_window):
            raise ConfigError("Configured model max_output_tokens is outside the supported range")
        declared_output = _optional_model_integer(
            row.get("declared_max_output_tokens"), field="declared_max_output_tokens", minimum=1,
        )
        _optional_model_integer(
            row.get("configured_output_tokens"), field="configured_output_tokens", minimum=1,
        )
        if declared_output is not None and max_output_tokens is not None:
            max_output_tokens = min(max_output_tokens, declared_output)
        builder_output_tokens = _bounded_model_integer(
            row.get("builder_output_tokens", min(32_000, max_output_tokens or 32_000)),
            field="builder_output_tokens", minimum=1, maximum=max_output_tokens or 10_000_000,
        )
        transport_value = row.get(
            "transport",
            known_model.transport if known_model is not None else "chat_completions",
        )
        if not isinstance(transport_value, str) or transport_value not in {
            "chat_completions",
            "responses",
            "anthropic_messages",
            "codex_app_server",
            "gemini_generate_content",
        }:
            raise ConfigError("Configured model transport is unsupported")
        transport = cast(
            Literal["chat_completions", "responses", "anthropic_messages", "codex_app_server", "gemini_generate_content"],
            transport_value,
        )
        streaming_value = row.get(
            "supports_streaming",
            known_model.supports_streaming if known_model is not None else None,
        )
        tools_value = row.get(
            "supports_tools",
            known_model.supports_tools if known_model is not None else None,
        )
        if streaming_value is not None and not isinstance(streaming_value, bool):
            raise ConfigError("Configured model streaming support must be boolean or null")
        if tools_value is not None and not isinstance(tools_value, bool):
            raise ConfigError("Configured model tool support must be boolean or null")

        if known_model is not None:
            model = replace(
                known_model,
                id=canonical_id,
                name=name,
                context_window=context_window,
                declared_context_window=capacity.declared_context_window,
                declared_context_source=capacity.declared_context_source,
                runtime_context_window=capacity.runtime_context_window,
                runtime_context_source=capacity.runtime_context_source,
                configured_context_window=capacity.configured_context_window,
                context_window_source=capacity.effective_context_source,
                declared_context_observed_at=capacity.declared_observed_at,
                runtime_context_observed_at=capacity.runtime_observed_at,
                configured_context_observed_at=capacity.configured_observed_at,
                context_observed_at=(capacity.runtime_observed_at or capacity.declared_observed_at),
                configured_runtime_mismatch=capacity.configured_runtime_mismatch,
                configured_declared_mismatch=capacity.configured_declared_mismatch,
                max_output_tokens=max_output_tokens,
                builder_output_tokens=builder_output_tokens,
                transport=transport,
                provider_model_id=upstream_id,
                supports_streaming=streaming_value,
                supports_tools=tools_value,
            )
        else:
            model = ModelInfo(
                id=canonical_id,
                name=name,
                provider_name=definition.name or provider_key,
                provider_key=provider_key,
                provider_mode=definition.mode or "byok",
                context_window=context_window,
                declared_context_window=capacity.declared_context_window,
                declared_context_source=capacity.declared_context_source,
                runtime_context_window=capacity.runtime_context_window,
                runtime_context_source=capacity.runtime_context_source,
                configured_context_window=capacity.configured_context_window,
                context_window_source=capacity.effective_context_source,
                declared_context_observed_at=capacity.declared_observed_at,
                runtime_context_observed_at=capacity.runtime_observed_at,
                configured_context_observed_at=capacity.configured_observed_at,
                context_observed_at=(capacity.runtime_observed_at or capacity.declared_observed_at),
                configured_runtime_mismatch=capacity.configured_runtime_mismatch,
                configured_declared_mismatch=capacity.configured_declared_mismatch,
                max_output_tokens=max_output_tokens,
                builder_output_tokens=builder_output_tokens,
                transport=transport,
                provider_model_id=upstream_id,
                supports_streaming=streaming_value,
                supports_tools=tools_value,
            )
        try:
            supported, suggested = reasoning_values(
                row.get("supported_reasoning_efforts"), row.get("default_reasoning_effort"),
            )
        except ValueError as error:
            raise ConfigError(str(error)) from error
        configured.append(replace(model, supported_reasoning_efforts=supported,
                                  default_reasoning_effort=suggested))
    return tuple(configured)


class ConfiguredProviderResolver:
    """Resolve one session through enabled, owner-local provider configuration."""

    def __init__(
        self,
        user_config_path: Path | None = None,
        *,
        model_capability_registry: ModelCapabilityRegistry | None = None,
    ) -> None:
        config = load_config(
            user_config_path,
            load_dotenv_file=False,
            decrypt_credentials=False,
        )
        subscription = config.providers.get("chatgpt_subscription")
        if (
            user_config_path is not None
            and subscription is not None
            and subscription.enabled
            and any(
                row.get("transport") == "codex_app_server"
                and row.get("supported_reasoning_efforts") is None
                for row in subscription.models
            )
            and refresh_saved_subscription_reasoning(Path(user_config_path).resolve().parent)
        ):
            config = load_config(
                user_config_path, load_dotenv_file=False, decrypt_credentials=False
            )
        known_catalog = tuple(build_catalog())
        catalog: list[ModelInfo] = []
        enabled: set[str] = set()
        for provider_key, definition in config.providers.items():
            if not definition.enabled:
                continue
            if not definition.base_url:
                raise ConfigError(f"Enabled provider '{provider_key}' requires base_url")
            catalog.extend(
                configure_reasoning(model, definition.base_url)
                for model in _configured_provider_models(provider_key, definition, known_catalog)
            )
            enabled.add(provider_key)

        registry = ProviderRegistry(catalog=catalog)
        self._registry = registry
        self._configured_providers: set[str] = set()
        self._configuration_lock = RLock()
        self._enabled = frozenset(enabled)
        self._model_ids = frozenset(model.id for model in catalog)
        self._model_providers = {model.id: model.provider_key for model in catalog}
        self._provider_definitions = dict(config.providers)
        self._user_config_path = Path(user_config_path) if user_config_path is not None else None
        self._model_capability_registry = (
            EMPTY_MODEL_CAPABILITY_REGISTRY
            if model_capability_registry is None
            else model_capability_registry
        )

    def _configure_selected_provider(self, provider_key: str) -> None:
        """Unlock a selected connection once; failures do not disable other connections."""
        with self._configuration_lock:
            if provider_key in self._configured_providers:
                return
            definition = self._provider_definitions[provider_key]
            credential, headers = provider_credentials(definition)
            if (definition.preset_id or provider_key) in _OPENCODE_PRESETS:
                headers = without_opencode_protocol_headers(headers)
            self._registry.configure_provider(
                provider_key=provider_key,
                base_url=definition.base_url,
                api_key=credential,
                headers=headers,
                runtime_home=(
                    str(self._user_config_path.resolve().parent / "chatgpt-runtime")
                    if provider_key == "chatgpt_subscription" and self._user_config_path is not None
                    else ""
                ),
            )
            self._configured_providers.add(provider_key)

    def _refresh_local_runtime_capacity(
        self,
        model: ModelInfo,
        definition: ProviderDef,
    ) -> None:
        """Replace a saved local runtime observation with one made for this GM admission."""

        discovered_model: dict[str, object] | None = None
        if self._user_config_path is not None:
            try:
                result = discover_provider_models(
                    self._user_config_path,
                    provider_id=model.provider_key,
                    preset_id=definition.preset_id or model.provider_key,
                    base_url=definition.base_url,
                    credential=None,
                    query=model.wire_model_id,
                    limit=100,
                )
            except ConfigError:
                result = {"models": []}
            rows = result.get("models")
            if isinstance(rows, list):
                discovered_model = next(
                    (
                        row
                        for row in rows
                        if isinstance(row, dict) and row.get("id") == model.wire_model_id
                    ),
                    None,
                )

        def observed_integer(field: str) -> int | None:
            value = discovered_model.get(field) if discovered_model is not None else None
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        def observed_text(field: str, fallback: str | None = None) -> str | None:
            value = discovered_model.get(field) if discovered_model is not None else None
            return value if isinstance(value, str) else fallback

        declared_context = observed_integer("declared_context_window")
        if declared_context is None:
            declared_context = model.declared_context_window
        capacity = resolve_context_capacity(
            declared_context_window=declared_context,
            declared_context_source=observed_text(
                "declared_context_source", model.declared_context_source
            ),
            runtime_context_window=observed_integer("runtime_context_window"),
            runtime_context_source=observed_text("runtime_context_source", "unknown"),
            configured_context_window=model.configured_context_window,
            declared_observed_at=observed_text(
                "declared_context_observed_at", model.declared_context_observed_at
            ),
            runtime_observed_at=observed_text("runtime_context_observed_at"),
            configured_observed_at=model.configured_context_observed_at,
        )
        model.context_window = capacity.effective_context_window
        model.declared_context_window = capacity.declared_context_window
        model.declared_context_source = capacity.declared_context_source
        model.runtime_context_window = capacity.runtime_context_window
        model.runtime_context_source = capacity.runtime_context_source
        model.configured_context_window = capacity.configured_context_window
        model.context_window_source = capacity.effective_context_source
        model.declared_context_observed_at = capacity.declared_observed_at
        model.runtime_context_observed_at = capacity.runtime_observed_at
        model.configured_context_observed_at = capacity.configured_observed_at
        model.context_observed_at = capacity.runtime_observed_at or capacity.declared_observed_at
        model.configured_runtime_mismatch = capacity.configured_runtime_mismatch
        model.configured_declared_mismatch = capacity.configured_declared_mismatch

    def model_choices(self) -> tuple[dict[str, object], ...]:
        """Return configured catalog choices without connection details or credentials."""

        models = (
            model
            for provider_id in sorted(self._enabled)
            for model in self._registry.list_models(provider_id)
        )
        return tuple(
            {
                "provider_id": model.provider_key,
                "provider_name": model.provider_name,
                "model_id": model.id,
                "model_name": model.name,
                "context_window": model.context_window,
                "declared_context_window": model.declared_context_window,
                "declared_context_source": model.declared_context_source,
                "runtime_context_window": model.runtime_context_window,
                "runtime_context_source": model.runtime_context_source,
                "configured_context_window": model.configured_context_window,
                "effective_context_window": model.context_window,
                "effective_context_source": model.context_window_source,
                "declared_context_observed_at": model.declared_context_observed_at,
                "runtime_context_observed_at": model.runtime_context_observed_at,
                "configured_context_observed_at": model.configured_context_observed_at,
                "context_observed_at": model.context_observed_at,
                "configured_runtime_mismatch": model.configured_runtime_mismatch,
                "configured_declared_mismatch": model.configured_declared_mismatch,
                "max_output_tokens": model.max_output_tokens,
                "builder_output_tokens": output_allowance(model, builder=True),
                "output_budget_mode": (
                    "runtime_managed" if model.transport == "codex_app_server" else "enforced"
                ),
                "transport": model.transport,
                "supports_streaming": model.supports_streaming,
                "supports_tools": model.supports_tools,
                "supported_reasoning_efforts": (
                    None if model.supported_reasoning_efforts is None else
                    list(model.supported_reasoning_efforts)
                ),
                "default_reasoning_effort": model.default_reasoning_effort,
                "reasoning_source": model.reasoning_source,
                "reasoning_control": model.reasoning_control,
                "reasoning_mode_defaults": {
                    "dm": mode_reasoning_default(model, "dm"),
                    "builder": mode_reasoning_default(model, "builder"),
                },
                "capability_registry_version": MODEL_CAPABILITY_REGISTRY_VERSION,
                "capability_tier_id": (
                    None
                    if (placement := self._model_capability_registry.placement(model.id)) is None
                    else placement.tier_id
                ),
                "capability_evidence_sha256": (
                    None if placement is None else placement.evidence_sha256
                ),
            }
            for model in sorted(
                models,
                key=lambda item: (
                    item.provider_name.casefold(),
                    item.name.casefold(),
                    item.id,
                ),
            )
        )

    def __call__(self, session: SessionRecord) -> Provider:
        """Return exactly the configured provider/model named by ``session``."""

        if session.provider_id not in self._enabled:
            raise CommandRejected("Agent session provider is not configured")
        if session.model_id not in self._model_ids:
            raise CommandRejected("Agent session model identity is not canonical")
        if self._model_providers[session.model_id] != session.provider_id:
            raise CommandRejected("Agent session provider/model identity mismatch")
        try:
            self._configure_selected_provider(session.provider_id)
        except ConfigError as error:
            raise CommandRejected(str(error), code="provider_credentials_unavailable") from None
        try:
            provider, model = self._registry.resolve(session.model_id)
        except ModelNotFoundError as error:
            raise CommandRejected("Agent session model is not configured") from error
        if model.provider_key != session.provider_id:
            raise CommandRejected("Agent session provider/model identity mismatch")
        if session.role.value == "dm" and model.provider_mode in _LOCAL_PROVIDER_MODES:
            self._refresh_local_runtime_capacity(
                model,
                self._provider_definitions[model.provider_key],
            )
        definition = self._provider_definitions[model.provider_key]
        if (definition.preset_id or model.provider_key) in _OPENCODE_PRESETS:
            return SessionBoundRequestMetadataProvider(
                provider,
                headers=opencode_request_headers(session.session_id),
            )
        return provider
