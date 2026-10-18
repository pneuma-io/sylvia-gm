"""Owner-only provider configuration for the desktop Models surface.

This module deliberately keeps provider credentials and custom header values on
the owner side.  Renderer projections contain only safe connection metadata,
configured model metadata, and booleans describing whether secrets exist.
"""

from __future__ import annotations

import hashlib
import json
import re
import webbrowser
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Literal, ParamSpec, TypeVar, cast
from urllib.parse import urlsplit

import httpx

from sylvia_gm.config._credential_protection import UNAVAILABLE_MESSAGE, unprotect_credentials
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.config._user_settings import CONFIG_LOCK, read_user_settings, write_user_settings
from sylvia_gm.config.loader import load_config, provider_credentials
from sylvia_gm.provider._context_capacity import (
    CONTEXT_CAPACITY_SOURCES,
    ResolvedContextCapacity,
    resolve_context_capacity,
)
from sylvia_gm.provider._model_defaults import documented_model_defaults, documented_model_ids
from sylvia_gm.provider._protocol_routes import model_protocol

ProviderTransport = Literal[
    "chat_completions", "responses", "anthropic_messages", "codex_app_server", "gemini_generate_content"
]

_DISCOVERY_RESPONSE_LIMIT = 32 * 1024 * 1024
_DISCOVERY_MODEL_LIMIT = 100_000
_DISCOVERY_PAGE_LIMIT = 200
_HEADER_COUNT_LIMIT = 16
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,80}$")
_PROFILE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TRANSPORTS: frozenset[str] = frozenset(
    {"chat_completions", "responses", "anthropic_messages", "codex_app_server", "gemini_generate_content"}
)
_CONFIG_LOCK = CONFIG_LOCK
_P = ParamSpec("_P")
_R = TypeVar("_R")

_PROVIDER_PRESETS: dict[str, dict[str, object]] = {
    "chatgpt_subscription": {
        "name": "ChatGPT subscription",
        "mode": "subscription",
        "base_url": "https://chatgpt.com",
        "credential_required": False,
        "endpoint_editable": False,
        "default_transport": "codex_app_server",
        "setup_hint": "Sign in from Accounts using the installed Codex runtime.",
        "setup_url": "https://developers.openai.com/codex/auth",
    },
    "lm_studio": {
        "name": "LM Studio",
        "mode": "local",
        "base_url": "http://127.0.0.1:1234/v1",
        "credential_required": False,
        "endpoint_editable": True,
        "default_transport": "chat_completions",
        "launch_hint": "Start the LM Studio local server, then detect or find models.",
        "setup_hint": "Load a model in LM Studio, then start its server from the Developer tab.",
        "setup_url": "https://lmstudio.ai/docs/developer/core/server",
    },
    "ollama": {
        "name": "Ollama",
        "mode": "local",
        "base_url": "http://127.0.0.1:11434/v1",
        "credential_required": False,
        "endpoint_editable": True,
        "default_transport": "chat_completions",
        "launch_hint": "Start Ollama before connecting. Sylvia GM does not launch it.",
        "setup_hint": (
            "Start Ollama with a downloaded model. Use your server's address and context "
            "setting."
        ),
        "setup_url": "https://docs.ollama.com/api/openai-compatibility",
    },
    "llama_cpp": {
        "name": "llama.cpp server",
        "mode": "local",
        "base_url": "http://127.0.0.1:9931/v1",
        "credential_required": False,
        "endpoint_editable": True,
        "default_transport": "chat_completions",
        "launch_hint": "Start llama-server with an appropriate chat template and tool support.",
        "setup_hint": (
            "Start llama-server with your model and a tool-capable chat template. Match its "
            "port below."
        ),
        "setup_url": "https://github.com/ggml-org/llama.cpp/tree/master/tools/server",
    },
    "vllm": {
        "name": "vLLM",
        "mode": "local",
        "base_url": "http://127.0.0.1:8000/v1",
        "credential_required": False,
        "endpoint_editable": True,
        "default_transport": "chat_completions",
        "launch_hint": "Start the vLLM OpenAI-compatible server before connecting.",
        "setup_hint": (
            "Start vLLM with a chat model and tool-call parser. Enter its key only if "
            "authentication is enabled."
        ),
        "setup_url": "https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/",
    },
    "localai": {
        "name": "LocalAI",
        "mode": "local",
        "base_url": "http://127.0.0.1:8080/v1",
        "credential_required": False,
        "endpoint_editable": True,
        "default_transport": "chat_completions",
        "launch_hint": "Start LocalAI before connecting. The port is editable.",
        "setup_hint": (
            "Start LocalAI with a chat model. Match its address and enter a key if your "
            "server requires one."
        ),
        "setup_url": "https://localai.io/docs/getting-started/",
    },
    "opencode_go": {
        "name": "OpenCode Go",
        "mode": "byok",
        "base_url": "https://opencode.ai/inference/go/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Use the key from your OpenCode Go workspace. Model access depends on your Go "
            "plan."
        ),
        "setup_url": "https://opencode.ai/docs/go/",
    },
    "opencode_zen": {
        "name": "OpenCode Zen",
        "mode": "byok",
        "base_url": "https://opencode.ai/inference/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "chat_completions",
        "setup_hint": "Copy your OpenCode Zen API key from its workspace, then find models.",
        "setup_url": "https://opencode.ai/docs/zen/",
    },
    "openrouter": {
        "name": "OpenRouter",
        "mode": "byok",
        "base_url": "https://openrouter.ai/api/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Use an OpenRouter API key. Model limits come from OpenRouter's own catalogue."
        ),
        "setup_url": "https://openrouter.ai/settings/keys",
    },
    "featherless": {
        "name": "Featherless",
        "mode": "byok",
        "base_url": "https://api.featherless.ai/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Copy a key from your Featherless dashboard. Your plan may allow less context "
            "than the model maximum."
        ),
        "setup_url": "https://featherless.ai/docs/quickstart-guide",
    },
    "openai": {
        "name": "OpenAI API",
        "mode": "byok",
        "base_url": "https://api.openai.com/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "responses",
        "setup_hint": (
            "Create an OpenAI API key in your project. ChatGPT sign-in is available "
            "separately under Accounts."
        ),
        "setup_url": "https://platform.openai.com/api-keys",
    },
    "groq": {
        "name": "Groq",
        "mode": "byok",
        "base_url": "https://api.groq.com/openai/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Create a GroqCloud API key, then choose a chat model available to your "
            "project."
        ),
        "setup_url": "https://console.groq.com/keys",
    },
    "together": {
        "name": "Together AI",
        "mode": "byok",
        "base_url": "https://api.together.ai/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Create an API key in your Together project. Select the full provider/model "
            "identifier."
        ),
        "setup_url": "https://api.together.ai/settings/projects/~current/api-keys",
    },
    "deepseek": {
        "name": "DeepSeek API",
        "mode": "byok",
        "base_url": "https://api.deepseek.com",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Use a DeepSeek platform API key. Your DeepSeek chat sign-in is not an API key."
        ),
        "setup_url": "https://api-docs.deepseek.com/quick_start",
    },
    "anthropic": {
        "name": "Anthropic",
        "mode": "byok",
        "base_url": "https://api.anthropic.com/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "anthropic_messages",
        "setup_hint": (
            "Use a Claude API key from Claude Console. Claude subscription sign-in is separate."
        ),
        "setup_url": "https://platform.claude.com/docs/en/api/overview",
    },
    "gemini": {
        "name": "Google Gemini",
        "mode": "byok",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Use a Gemini API key from Google AI Studio. Select a model that supports tools."
        ),
        "setup_url": "https://ai.google.dev/gemini-api/docs/openai",
    },
    "xai": {
        "name": "xAI",
        "mode": "byok",
        "base_url": "https://api.x.ai/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "responses",
        "setup_hint": (
            "Use an xAI API key from your team. Grok app subscriptions are separate."
        ),
        "setup_url": "https://docs.x.ai/developers/quickstart",
    },
    "mistral": {
        "name": "Mistral",
        "mode": "byok",
        "base_url": "https://api.mistral.ai/v1",
        "credential_required": True,
        "endpoint_editable": True,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Use a Mistral API key and the endpoint assigned to your workspace."
        ),
        "setup_url": "https://docs.mistral.ai/getting-started/quickstart",
    },
    "minimax": {
        "name": "MiniMax",
        "mode": "byok",
        "base_url": "https://api.minimax.io/anthropic/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "anthropic_messages",
        "setup_hint": (
            "Use a MiniMax platform API key. Enter the documented model ID if discovery "
            "is unavailable."
        ),
        "setup_url": "https://platform.minimax.io/docs/api-reference/text-anthropic-api",
    },
    "moonshot": {
        "name": "Kimi / Moonshot",
        "mode": "byok",
        "base_url": "https://api.moonshot.ai/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Use a Moonshot platform API key. This uses the ordinary API, not Kimi Code."
        ),
        "setup_url": "https://platform.kimi.ai/docs/guide/start-using-kimi-api",
    },
    "zai": {
        "name": "Z.AI",
        "mode": "byok",
        "base_url": "https://api.z.ai/api/paas/v4",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Use a Z.AI API key for the standard API. Coding Plan keys use a separate service."
        ),
        "setup_url": "https://docs.z.ai/guides/overview/quick-start",
    },
    "alibaba": {
        "name": "Alibaba Model Studio",
        "mode": "byok",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "credential_required": True,
        "endpoint_editable": True,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Match the API endpoint to your key's region and workspace. Singapore is shown "
            "by default."
        ),
        "setup_url": "https://www.alibabacloud.com/help/en/model-studio/base-url",
    },
    "fireworks": {
        "name": "Fireworks AI",
        "mode": "byok",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Use a Fireworks API key and the full model or deployment identifier."
        ),
        "setup_url": "https://docs.fireworks.ai/tools-sdks/openai-compatibility",
    },
    "cerebras": {
        "name": "Cerebras",
        "mode": "byok",
        "base_url": "https://api.cerebras.ai/v1",
        "credential_required": True,
        "endpoint_editable": False,
        "default_transport": "chat_completions",
        "setup_hint": (
            "Use a Cerebras Inference API key and select an available tool-capable model."
        ),
        "setup_url": "https://inference-docs.cerebras.ai/resources/openai",
    },
    "custom": {
        "name": "Custom OpenAI-Compatible",
        "mode": "custom",
        "base_url": "",
        "credential_required": False,
        "endpoint_editable": True,
        "default_transport": "chat_completions",
        "launch_hint": "Enter the exact API base URL for an already-running endpoint.",
        "setup_hint": (
            "Enter your service's API base URL and any required key. Unknown model limits "
            "can be entered under Advanced."
        ),
    },
}


def _serialized_settings_write(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """Serialize owner-local read/modify/write operations on provider settings."""

    @wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with _CONFIG_LOCK:
            return function(*args, **kwargs)

    return wrapped


def _preset(preset_id: object) -> tuple[str, Mapping[str, object]]:
    if not isinstance(preset_id, str) or preset_id not in _PROVIDER_PRESETS:
        raise ConfigError("Provider preset is unsupported")
    return preset_id, _PROVIDER_PRESETS[preset_id]


def open_provider_setup(preset_id: object) -> dict[str, object]:
    """Open only a shipped service's setup page, never a renderer-supplied URL."""
    _, preset = _preset(preset_id)
    url = preset.get("setup_url")
    if not isinstance(url, str):
        raise ConfigError("Ask your custom service for its connection instructions")
    try:
        opened = webbrowser.open(url, new=2)
    except (OSError, webbrowser.Error):
        opened = False
    return {"opened": bool(opened), "url": url}


def _provider_profile_id(
    provider_id: object,
    *,
    preset_id: str,
    display_name: str,
    base_url: str,
    document: Mapping[str, object],
) -> str:
    if (
        isinstance(provider_id, str)
        and _PROFILE_ID.fullmatch(provider_id)
        and provider_id != "custom"
    ):
        return provider_id
    if preset_id != "custom":
        return preset_id
    digest = hashlib.sha256(f"{display_name.casefold()}\n{base_url}".encode()).hexdigest()[:16]
    identity = f"custom_{digest}"
    providers = document.get("providers")
    if isinstance(providers, Mapping):
        existing = providers.get(identity)
        if isinstance(existing, Mapping):
            existing_url = existing.get("base_url")
            if isinstance(existing_url, str) and existing_url.rstrip("/") != base_url:
                raise ConfigError("Custom provider identity collision")
    return identity


def _base_url(preset_id: str, preset: Mapping[str, object], value: object) -> str:
    expected = str(preset["base_url"])
    candidate = value.strip().rstrip("/") if isinstance(value, str) else ""
    if not candidate:
        candidate = expected
    historical_endpoints = {
        "opencode_go": "https://opencode.ai/zen/go/v1",
        "opencode_zen": "https://opencode.ai/zen/v1",
    }
    if (not bool(preset["endpoint_editable"])
            and candidate not in {expected, historical_endpoints.get(preset_id)}):
        raise ConfigError(f"{preset['name']} uses its official API endpoint")
    if candidate == historical_endpoints.get(preset_id):
        candidate = expected
    parsed = urlsplit(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigError("Provider endpoint must be a plain HTTP(S) API base URL")
    return candidate


def _display_name(value: object, fallback: str) -> str:
    candidate = value.strip() if isinstance(value, str) else ""
    if not candidate:
        candidate = fallback
    if len(candidate) > 120 or any(ord(character) < 32 for character in candidate):
        raise ConfigError("Provider display name is invalid")
    return candidate


def _model_text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Provider model {field} is required")
    result = value.strip()
    if len(result) > limit or any(ord(character) < 32 for character in result):
        raise ConfigError(f"Provider model {field} is invalid")
    return result


def _model_integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"Provider model {field} must be an integer")
    if value < minimum or value > maximum:
        raise ConfigError(f"Provider model {field} is outside the supported range")
    return value


def _optional_model_integer(
    value: object, field: str, minimum: int = 512, maximum: int = 10_000_000
) -> int | None:
    """Validate an optional capacity value without inventing a default."""

    if value is None or value == "":
        return None
    return _model_integer(value, field, minimum, maximum)


def _capacity_source(value: object, *, fallback: str = "unknown") -> str:
    if isinstance(value, str) and value in CONTEXT_CAPACITY_SOURCES:
        return value
    return fallback


def _capacity_timestamp(value: object) -> str | None:
    """Accept one owner-recorded ISO timestamp without inventing one while reading."""

    return value if isinstance(value, str) and value else None


def _capacity_from_model_row(
    row: Mapping[str, object],
    *,
    declared_fallback: int | None = None,
    declared_fallback_source: str = "model_catalog",
) -> ResolvedContextCapacity:
    """Read new capacity facts or preserve an old single value as legacy-unverified."""

    has_capacity_facts = any(
        key in row
        for key in (
            "declared_context_window",
            "runtime_context_window",
            "configured_context_window",
            "effective_context_window",
        )
    )
    declared = _optional_model_integer(
        row.get("declared_context_window", declared_fallback),
        "declared context window",
    )
    runtime = _optional_model_integer(
        row.get("runtime_context_window"),
        "runtime context window",
    )
    configured = _optional_model_integer(
        row.get("configured_context_window"),
        "configured context window",
    )
    legacy = None
    if not has_capacity_facts:
        legacy = _optional_model_integer(row.get("context_window"), "legacy context window")
    observed_at = _capacity_timestamp(row.get("context_observed_at"))
    return resolve_context_capacity(
        declared_context_window=declared,
        declared_context_source=_capacity_source(
            row.get("declared_context_source"),
            fallback=declared_fallback_source if declared is not None else "unknown",
        ),
        runtime_context_window=runtime,
        runtime_context_source=_capacity_source(row.get("runtime_context_source")),
        configured_context_window=configured,
        legacy_context_window=legacy,
        observed_at=observed_at,
        declared_observed_at=_capacity_timestamp(row.get("declared_context_observed_at")),
        runtime_observed_at=_capacity_timestamp(row.get("runtime_context_observed_at")),
        configured_observed_at=_capacity_timestamp(row.get("configured_context_observed_at")),
    )


def _transport(value: object, *, fallback: str = "chat_completions") -> ProviderTransport:
    candidate = value if isinstance(value, str) and value else fallback
    if candidate not in _TRANSPORTS:
        raise ConfigError("Provider model transport is unsupported")
    return cast(ProviderTransport, candidate)


def _private_headers(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > _HEADER_COUNT_LIMIT:
        raise ConfigError("Provider headers must be a bounded mapping")
    result: dict[str, str] = {}
    folded: set[str] = set()
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not _HEADER_NAME.fullmatch(raw_name):
            raise ConfigError("Provider header name is invalid")
        if not isinstance(raw_value, str) or not raw_value or len(raw_value) > 1024:
            raise ConfigError("Each provider header value must contain 1–1024 characters")
        if any(ord(character) < 32 and character != "\t" for character in raw_value):
            raise ConfigError("Provider header value is invalid")
        folded_name = raw_name.casefold()
        if folded_name in folded:
            raise ConfigError("Provider header names must be unique")
        folded.add(folded_name)
        result[raw_name] = raw_value
    return result


def _read_user_config(path: Path) -> dict[str, Any]:
    return read_user_settings(path)


def _write_user_config(path: Path, data: Mapping[str, object]) -> None:
    write_user_settings(path, data)


def _provider_preset_id(provider_id: str, raw: Mapping[str, object]) -> str:
    declared = raw.get("preset_id")
    if isinstance(declared, str) and declared in _PROVIDER_PRESETS:
        return declared
    return provider_id if provider_id in _PROVIDER_PRESETS else "custom"


def _canonical_model_id(provider_id: str, upstream_model_id: str) -> str:
    return f"{provider_id.replace('_', '-')}/{upstream_model_id}"


def _configured_model_exists(
    document: Mapping[str, object], provider_id: str, canonical_model_id: str
) -> bool:
    providers = document.get("providers")
    provider = providers.get(provider_id) if isinstance(providers, Mapping) else None
    models = provider.get("models") if isinstance(provider, Mapping) else None
    if not isinstance(models, list):
        return False
    for model in models:
        if not isinstance(model, Mapping):
            continue
        upstream = model.get("id")
        if (
            isinstance(upstream, str)
            and _canonical_model_id(provider_id, upstream) == canonical_model_id
        ):
            return True
    return False


def require_configured_model(
    config_path: Path, *, provider_id: object, model_id: object
) -> dict[str, str]:
    """Return one exact safe configured selection or fail closed."""

    if not isinstance(provider_id, str) or not _PROFILE_ID.fullmatch(provider_id):
        raise ConfigError("Provider identity is invalid")
    if (
        not isinstance(model_id, str)
        or not model_id
        or len(model_id) > 512
        or model_id != model_id.strip()
    ):
        raise ConfigError("Model identity is invalid")
    document = _read_user_config(config_path)
    if not _configured_model_exists(document, provider_id, model_id):
        raise ConfigError("The selected model is not configured")
    return {"provider_id": provider_id, "model_id": model_id}


def _project_configured_model(
    provider_id: str,
    row: Mapping[str, object],
    *,
    default_transport: object,
) -> dict[str, object]:
    """Project one saved model with resolved capacity provenance and no secrets."""

    capacity = _capacity_from_model_row(row)
    upstream_id = row.get("id")
    return {
        "id": upstream_id,
        "canonical_id": (
            _canonical_model_id(provider_id, str(upstream_id)) if upstream_id else None
        ),
        "name": row.get("name", upstream_id),
        **capacity.projection(),
        "max_output_tokens": row.get("max_output_tokens"),
        "declared_max_output_tokens": row.get("declared_max_output_tokens"),
        "configured_output_tokens": row.get(
            "configured_output_tokens", row.get("max_output_tokens"),
        ),
        "documented_source": row.get("documented_source"),
        "output_allowance_source": row.get("output_allowance_source"),
        "documented_at": row.get("documented_at"),
        "builder_output_tokens": row.get("builder_output_tokens"),
        "output_budget_mode": (
            "runtime_managed" if row.get("transport", default_transport) == "codex_app_server"
            else "enforced"
        ),
        "transport": row.get("transport", default_transport),
        "supports_streaming": row.get("supports_streaming"),
        "supports_tools": row.get("supports_tools"),
        "supported_reasoning_efforts": row.get("supported_reasoning_efforts"),
        "default_reasoning_effort": row.get("default_reasoning_effort"),
    }


def provider_settings(config_path: Path) -> dict[str, object]:
    """Return safe provider metadata without credentials or header values."""

    config = load_config(config_path, load_dotenv_file=False, decrypt_credentials=False)
    raw_document = _read_user_config(config_path)
    raw_providers = raw_document.get("providers")
    raw_mapping = raw_providers if isinstance(raw_providers, Mapping) else {}
    configured: list[dict[str, object]] = []
    for provider_id, definition in sorted(config.providers.items()):
        if not definition.enabled:
            continue
        raw = raw_mapping.get(provider_id)
        raw = raw if isinstance(raw, Mapping) else {}
        preset_id = _provider_preset_id(provider_id, raw)
        preset = _PROVIDER_PRESETS[preset_id]
        try:
            credential, headers = provider_credentials(definition)
            credential_configured, header_count = bool(credential), len(headers)
            credential_error = None
        except ConfigError:
            credential_configured, header_count = True, 0
            credential_error = UNAVAILABLE_MESSAGE
        configured.append(
            {
                "provider_id": provider_id,
                "preset_id": preset_id,
                "name": definition.name or preset["name"],
                "mode": definition.mode or preset["mode"],
                "base_url": definition.base_url,
                "credential_configured": credential_configured,
                "header_count": header_count,
                "credential_error": credential_error,
                "models": [
                    _project_configured_model(
                        provider_id,
                        row,
                        default_transport=preset["default_transport"],
                    )
                    for row in definition.models
                ],
            }
        )
    default_model = raw_document.get("default_model")
    safe_default: dict[str, str] | None = None
    if isinstance(default_model, Mapping):
        default_provider_id = default_model.get("provider_id")
        default_model_id = default_model.get("model_id")
        if (
            isinstance(default_provider_id, str)
            and isinstance(default_model_id, str)
            and _configured_model_exists(raw_document, default_provider_id, default_model_id)
        ):
            safe_default = {
                "provider_id": default_provider_id,
                "model_id": default_model_id,
            }
    return {
        "presets": [
            {"provider_id": provider_id, **preset}
            for provider_id, preset in _PROVIDER_PRESETS.items()
        ],
        "configured": configured,
        "default_model": safe_default,
        "model_defaults": [
            {"preset_id": preset_id,
             **(_discovery_model({"id": model_id}, preset_id=preset_id) or {})}
            for preset_id, model_id in documented_model_ids()
        ],
    }


def _existing_connection(
    config_path: Path, provider_id: str, *, replacing_secrets: bool = False
) -> tuple[str, str, dict[str, str], str] | None:
    definition = load_config(
        config_path, load_dotenv_file=False, decrypt_credentials=False
    ).providers.get(provider_id)
    if definition is None or not definition.enabled:
        return None
    document = _read_user_config(config_path)
    providers = document.get("providers")
    raw = providers.get(provider_id) if isinstance(providers, Mapping) else None
    preset_id = _provider_preset_id(provider_id, raw if isinstance(raw, Mapping) else {})
    try:
        credential, headers = provider_credentials(definition)
    except ConfigError:
        if not replacing_secrets:
            raise
        credential, headers = "", {}
    return (
        definition.base_url,
        credential,
        headers,
        preset_id,
    )


def _model_transport_for_preset(
    preset_id: str, model_id: str, declared: object | None = None
) -> ProviderTransport:
    return cast(ProviderTransport, model_protocol(
        preset_id, model_id, declared, fallback=str(_PROVIDER_PRESETS[preset_id]["default_transport"]),
    ))


def _discovery_model(row: Mapping[str, object], *, preset_id: str) -> dict[str, object] | None:
    """Read service facts without inferring account access from a model's identity."""
    if preset_id == "together" and row.get("type") not in (None, "chat"):
        return None
    if preset_id == "featherless" and row.get("available_on_current_plan") is False:
        return None
    capabilities = row.get("capabilities")
    if (preset_id == "mistral" and isinstance(capabilities, Mapping)
            and capabilities.get("completion_chat") is False):
        return None
    value = row.get("id")
    if not isinstance(value, str):
        return None
    model_id = value.strip()
    if not model_id or len(model_id) > 256 or any(ord(character) < 32 for character in model_id):
        return None
    if preset_id in {"opencode_go", "opencode_zen"} and model_id.casefold().startswith("jev-"):
        # OpenCode documents Jev as typed decisions through /systemone, not a GM conversation.
        return None
    defaults = documented_model_defaults(preset_id, model_id)
    raw_name = row.get("name", row.get("display_name", defaults.get("name")))
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else model_id
    context = row.get("context_length", row.get("context_window"))
    if context is None and preset_id == "anthropic":
        context = row.get("max_input_tokens")
    if context is None and preset_id == "mistral":
        context = row.get("max_context_length")
    if not isinstance(context, int) or isinstance(context, bool) or context < 512:
        top_provider = row.get("top_provider")
        context = top_provider.get("context_length") if isinstance(top_provider, Mapping) else None
    if not isinstance(context, int) or isinstance(context, bool) or context < 512:
        context = None
    context_source = "provider_metadata" if context is not None else "unknown"
    if context is None and defaults.get("context_window"):
        context = defaults["context_window"]
        context_source = "model_catalog"
    maximum = row.get("max_completion_tokens", row.get("max_output_tokens"))
    if maximum is None and preset_id == "anthropic":
        maximum = row.get("max_tokens")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
        top_provider = row.get("top_provider")
        maximum = (
            top_provider.get("max_completion_tokens") if isinstance(top_provider, Mapping) else None
        )
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
        maximum = defaults.get("max_output_tokens")
    supported = row.get("supported_parameters")
    supports_tools: bool | None = defaults.get("supports_tools")
    supports_streaming: bool | None = defaults.get("supports_streaming")
    if (preset_id == "mistral" and isinstance(capabilities, Mapping)
            and isinstance(capabilities.get("function_calling"), bool)):
        supports_tools = capabilities["function_calling"]
    if isinstance(supported, list):
        values = {item for item in supported if isinstance(item, str)}
        supports_tools = bool({"tools", "tool_choice"}.intersection(values))
        if "stream" in values:
            supports_streaming = True
    # Featherless has a separate plan ceiling; /models alone is not the usable allowance.
    hosted = str(_PROVIDER_PRESETS[preset_id]["mode"]) == "byok" and preset_id != "featherless"
    observed_at = (
        defaults.get("documented_at") if context_source == "model_catalog"
        else datetime.now(UTC).isoformat() if context is not None and hosted else None
    )
    capacity = resolve_context_capacity(
        declared_context_window=context,
        declared_context_source=context_source,
        # A hosted provider's own catalog describes its served limit. For local and custom
        # OpenAI-compatible endpoints, a generic model maximum is not proof of the loaded KV cache.
        runtime_context_window=context if hosted else None,
        runtime_context_source=(
            context_source if context is not None and hosted else "unknown"
        ),
        observed_at=observed_at,
    )
    return {
        "id": model_id,
        "name": name,
        **capacity.projection(),
        "max_output_tokens": maximum,
        "declared_max_output_tokens": maximum,
        "configured_output_tokens": None,
        "transport": _model_transport_for_preset(preset_id, model_id, row.get("transport")),
        "supports_tools": supports_tools,
        "supports_streaming": supports_streaming,
        "documented_source": defaults.get("documented_source"),
        "documented_at": defaults.get("documented_at"),
    }


def _native_endpoint(endpoint: str, suffix: str) -> str:
    """Resolve a provider-native route beside an OpenAI-compatible `/v1` base."""

    base = endpoint[:-3] if endpoint.endswith("/v1") else endpoint
    return base.rstrip("/") + suffix


def _response_document(response: httpx.Response) -> Mapping[str, object] | None:
    if response.status_code != 200 or len(response.content) > _DISCOVERY_RESPONSE_LIMIT:
        return None
    try:
        value = response.json()
    except ValueError:
        return None
    return value if isinstance(value, Mapping) else None


def _merge_featherless_capacity(
    client: httpx.Client,
    *,
    endpoint: str,
    request_headers: Mapping[str, str],
    models: list[dict[str, object]],
) -> str | None:
    """Apply the account plan ceiling; a failed lookup leaves usable context unknown."""
    # https://featherless.ai/docs/api-reference-plan (checked 2026-09-23).
    try:
        plan = _response_document(client.get(endpoint + "/plan", headers=request_headers))
    except httpx.HTTPError:
        plan = None
    limit = plan.get("max_context_length") if plan else None
    if (plan is None or "max_context_length" not in plan
            or (limit is not None and (
                not isinstance(limit, int) or isinstance(limit, bool) or limit < 512
            ))):
        return (
            "Featherless plan limits were unavailable. Refresh, or enter your plan's "
            "context allowance under Advanced."
        )
    observed = datetime.now(UTC).isoformat()
    for model in models:
        declared = model.get("declared_context_window")
        maximum = declared if isinstance(declared, int) else None
        runtime = min(maximum, limit) if maximum and isinstance(limit, int) else maximum or limit
        model.update(resolve_context_capacity(
            declared_context_window=maximum,
            declared_context_source=model.get("declared_context_source"),
            runtime_context_window=runtime,
            runtime_context_source="provider_metadata",
            declared_observed_at=cast(str | None, model.get("declared_context_observed_at")),
            runtime_observed_at=observed,
        ).projection())
    return None


def _lm_studio_capacity_models(
    client: httpx.Client,
    *,
    endpoint: str,
    request_headers: Mapping[str, str],
) -> list[dict[str, object]]:
    """Read LM Studio's loaded instance context and declared model maximum."""

    try:
        response = client.get(
            _native_endpoint(endpoint, "/api/v1/models"), headers=dict(request_headers)
        )
    except httpx.HTTPError:
        return []
    document = _response_document(response)
    rows = document.get("models") if document is not None else None
    if not isinstance(rows, list) or len(rows) > _DISCOVERY_MODEL_LIMIT:
        return []

    models: list[dict[str, object]] = []
    for raw in rows:
        if not isinstance(raw, Mapping) or raw.get("type") != "llm":
            continue
        key = raw.get("key")
        if not isinstance(key, str) or not key or len(key) > 256:
            continue
        declared = raw.get("max_context_length")
        if not isinstance(declared, int) or isinstance(declared, bool) or declared < 512:
            declared = None
        loaded_instances: list[dict[str, object]] = []
        raw_instances = raw.get("loaded_instances")
        if isinstance(raw_instances, list):
            for instance in raw_instances:
                if not isinstance(instance, Mapping):
                    continue
                instance_id = instance.get("id")
                config = instance.get("config")
                context = config.get("context_length") if isinstance(config, Mapping) else None
                if (
                    isinstance(instance_id, str)
                    and instance_id
                    and isinstance(context, int)
                    and not isinstance(context, bool)
                    and context >= 512
                ):
                    loaded_instances.append({"id": instance_id, "context_window": context})
        capabilities = raw.get("capabilities")
        trained_for_tools = (
            capabilities.get("trained_for_tool_use") if isinstance(capabilities, Mapping) else None
        )
        models.append(
            {
                "id": key,
                "name": (
                    raw.get("display_name") if isinstance(raw.get("display_name"), str) else key
                ),
                "declared_context_window": declared,
                "loaded_instances": loaded_instances,
                "supports_tools": (
                    trained_for_tools if isinstance(trained_for_tools, bool) else None
                ),
            }
        )
    return models


def _merge_lm_studio_capacity(
    discovered: list[dict[str, object]],
    native_models: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Merge native LM Studio facts into the generic model catalog without guessing an instance."""

    by_key = {str(row["id"]): row for row in native_models}
    by_instance: dict[str, dict[str, object]] = {}
    for row in native_models:
        instances = row.get("loaded_instances", [])
        for instance in instances if isinstance(instances, list) else []:
            if isinstance(instance, Mapping) and isinstance(instance.get("id"), str):
                by_instance[str(instance["id"])] = row

    merged: list[dict[str, object]] = []
    seen: set[str] = set()
    for generic in discovered:
        model_id = str(generic["id"])
        native = by_key.get(model_id) or by_instance.get(model_id)
        if native is None:
            merged.append(generic)
            seen.add(model_id)
            continue
        instances = native.get("loaded_instances")
        instance_rows = (
            [item for item in instances if isinstance(item, Mapping)]
            if isinstance(instances, list)
            else []
        )
        exact = next((item for item in instance_rows if item.get("id") == model_id), None)
        selected = exact or (instance_rows[0] if len(instance_rows) == 1 else None)
        runtime = selected.get("context_window") if isinstance(selected, Mapping) else None
        declared = native.get("declared_context_window")
        if not isinstance(declared, int):
            declared = generic.get("declared_context_window")
        declared_source = (
            "provider_metadata"
            if isinstance(native.get("declared_context_window"), int)
            else generic.get("declared_context_source", "unknown")
        )
        capacity = resolve_context_capacity(
            declared_context_window=declared if isinstance(declared, int) else None,
            declared_context_source=declared_source,
            runtime_context_window=runtime if isinstance(runtime, int) else None,
            runtime_context_source="runtime_reported",
            observed_at=(
                datetime.now(UTC).isoformat()
                if isinstance(declared, int) or isinstance(runtime, int)
                else None
            ),
        )
        merged.append(
            {
                **generic,
                **capacity.projection(),
                "name": native.get("name") or generic.get("name") or model_id,
                "supports_tools": (
                    native.get("supports_tools")
                    if native.get("supports_tools") is not None
                    else generic.get("supports_tools")
                ),
            }
        )
        seen.add(model_id)

    for native in native_models:
        model_id = str(native["id"])
        if model_id in seen:
            continue
        instances = native.get("loaded_instances")
        instance_rows = (
            [item for item in instances if isinstance(item, Mapping)]
            if isinstance(instances, list)
            else []
        )
        selected = instance_rows[0] if len(instance_rows) == 1 else None
        runtime = selected.get("context_window") if isinstance(selected, Mapping) else None
        declared = native.get("declared_context_window")
        capacity = resolve_context_capacity(
            declared_context_window=declared if isinstance(declared, int) else None,
            declared_context_source="provider_metadata",
            runtime_context_window=runtime if isinstance(runtime, int) else None,
            runtime_context_source="runtime_reported",
            observed_at=datetime.now(UTC).isoformat(),
        )
        merged.append(
            {
                "id": model_id,
                "name": native.get("name") or model_id,
                **capacity.projection(),
                "max_output_tokens": None,
                "transport": "chat_completions",
                "supports_tools": native.get("supports_tools"),
                "supports_streaming": None,
            }
        )
    return merged


def _merge_ollama_capacity(
    client: httpx.Client,
    *,
    endpoint: str,
    request_headers: Mapping[str, str],
    discovered: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Merge Ollama's actual running context and loaded model maximum when available."""

    try:
        response = client.get(_native_endpoint(endpoint, "/api/ps"), headers=dict(request_headers))
    except httpx.HTTPError:
        document = None
    else:
        document = _response_document(response)
    raw_running = document.get("models") if document is not None else None
    if not isinstance(raw_running, list):
        raw_running = []
    running = {
        str(row.get("model", row.get("name"))): row
        for row in raw_running
        if isinstance(row, Mapping) and isinstance(row.get("model", row.get("name")), str)
    }
    result: list[dict[str, object]] = []
    for model in discovered:
        model_id = str(model["id"])
        active = running.get(model_id, {})
        runtime = active.get("context_length")
        runtime = (
            runtime
            if isinstance(runtime, int) and not isinstance(runtime, bool) and runtime >= 512
            else None
        )
        declared = model.get("declared_context_window")
        declared = declared if isinstance(declared, int) else None
        declared_source = model.get("declared_context_source", "unknown")
        try:
            show = client.post(
                _native_endpoint(endpoint, "/api/show"),
                headers=dict(request_headers),
                json={"model": model_id},
            )
            show_document = _response_document(show)
            model_info = show_document.get("model_info") if show_document is not None else None
            if isinstance(model_info, Mapping):
                candidates = [
                    value
                    for key, value in model_info.items()
                    if isinstance(key, str)
                    and key.endswith(".context_length")
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 512
                ]
                if candidates:
                    declared = max(candidates)
                    declared_source = "provider_metadata"
        except httpx.HTTPError:
            pass
        capacity = resolve_context_capacity(
            declared_context_window=declared,
            declared_context_source=declared_source,
            runtime_context_window=runtime,
            runtime_context_source="runtime_reported",
            observed_at=(
                datetime.now(UTC).isoformat()
                if runtime is not None or declared is not None
                else None
            ),
        )
        result.append({**model, **capacity.projection()})
    return result


def _model_catalog(
    client: httpx.Client,
    *,
    endpoint: str,
    preset_id: str,
    name: str,
    headers: Mapping[str, str],
) -> list[object]:
    """Read a bounded catalog, following Anthropic's native pagination when present."""
    rows: list[object] = []
    cursors: set[str] = set()
    params: dict[str, str | int] = {"limit": 1000} if preset_id == "anthropic" else {}
    total_bytes = 0
    for _ in range(100):
        response = client.get(endpoint + "/models", headers=headers, params=params)
        if response.status_code in {401, 403}:
            raise ConfigError(f"{name} rejected the API credential")
        if response.status_code in {404, 405}:
            raise ConfigError(
                f"{name} does not expose model discovery here. Enter its documented model "
                "identifier and limits under Advanced."
            )
        if response.status_code != 200:
            raise ConfigError(
                f"{name} model discovery failed with HTTP {response.status_code}"
            )
        total_bytes += len(response.content)
        if total_bytes > _DISCOVERY_RESPONSE_LIMIT:
            raise ConfigError("Provider model discovery response is too large")
        document = response.json()
        if preset_id in {"together", "mistral"} and isinstance(document, list):
            page = document
        else:
            page = document.get("data") if isinstance(document, dict) else None
        if not isinstance(page, list):
            raise ConfigError("Provider returned an invalid model catalog")
        rows.extend(page)
        if len(rows) > _DISCOVERY_MODEL_LIMIT:
            raise ConfigError("Provider model catalog exceeds the supported bound")
        if preset_id != "anthropic" or not document.get("has_more"):
            return rows
        cursor = document.get("last_id")
        if not page or not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ConfigError("Provider returned invalid model catalog pagination")
        cursors.add(cursor)
        params["after_id"] = cursor
    raise ConfigError("Provider model catalog exceeds the supported page bound")


def discover_provider_models(
    config_path: Path,
    *,
    provider_id: object,
    base_url: object,
    credential: object,
    preset_id: object | None = None,
    query: object = "",
    offset: object = 0,
    limit: object = 100,
    headers: object = None,
) -> dict[str, object]:
    """Query and page one explicitly selected provider's model catalog."""

    requested_id = provider_id if isinstance(provider_id, str) else ""
    existing = _existing_connection(
        config_path, requested_id,
        replacing_secrets=bool(isinstance(credential, str) and credential.strip()) or bool(headers),
    ) if requested_id else None
    selected_preset_id = (
        preset_id
        if isinstance(preset_id, str) and preset_id
        else (existing[3] if existing is not None else requested_id)
    )
    preset_key, preset = _preset(selected_preset_id)
    if preset_key == "chatgpt_subscription":
        raise ConfigError("Use the ChatGPT subscription controls to discover models")
    endpoint = _base_url(
        preset_key,
        preset,
        base_url if base_url else (existing[0] if existing is not None else None),
    )
    supplied = credential.strip() if isinstance(credential, str) else ""
    if not supplied and existing is not None:
        supplied = existing[1]
    if bool(preset["credential_required"]) and not supplied:
        raise ConfigError(f"{preset['name']} requires an API key")
    supplied_headers = _private_headers(headers)
    if not supplied_headers and existing is not None:
        supplied_headers = existing[2]

    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ConfigError("Model discovery offset is invalid")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= _DISCOVERY_PAGE_LIMIT
    ):
        raise ConfigError("Model discovery page size is invalid")
    search = query.strip().casefold() if isinstance(query, str) else ""
    if len(search) > 256:
        raise ConfigError("Model discovery search is too long")

    request_headers = dict(supplied_headers)
    if preset["default_transport"] == "anthropic_messages":
        request_headers.setdefault("x-api-key", supplied)
        request_headers.setdefault("anthropic-version", "2023-06-01")
    elif supplied:
        request_headers.setdefault("Authorization", f"Bearer {supplied}")
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            rows = _model_catalog(
                client, endpoint=endpoint, preset_id=preset_key,
                name=str(preset["name"]), headers=request_headers,
            )
    except ConfigError:
        raise
    except (httpx.HTTPError, ValueError) as error:
        raise ConfigError(f"Could not connect to {preset['name']}") from error

    discovered: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_row in rows:
        if not isinstance(raw_row, Mapping):
            continue
        model = _discovery_model(raw_row, preset_id=preset_key)
        if model is None:
            continue
        model_id = cast(str, model["id"])
        if model_id in seen:
            continue
        seen.add(model_id)
        discovered.append(model)

    notice = None
    if preset_key == "featherless":
        with httpx.Client(timeout=20.0, follow_redirects=False) as native_client:
            notice = _merge_featherless_capacity(
                native_client, endpoint=endpoint,
                request_headers=request_headers, models=discovered,
            )

    # OpenAI-compatible `/models` commonly omits the loaded runtime context. Known local presets
    # receive one bounded native probe; a native failure never invalidates otherwise valid generic
    # discovery.
    if preset_key in {"lm_studio", "ollama"}:
        with httpx.Client(timeout=20.0, follow_redirects=False) as native_client:
            if preset_key == "lm_studio":
                discovered = _merge_lm_studio_capacity(
                    discovered,
                    _lm_studio_capacity_models(
                        native_client,
                        endpoint=endpoint,
                        request_headers=request_headers,
                    ),
                )
            else:
                discovered = _merge_ollama_capacity(
                    native_client,
                    endpoint=endpoint,
                    request_headers=request_headers,
                    discovered=discovered,
                )

    unique: dict[str, dict[str, object]] = {}
    for model in discovered:
        model_id = str(model["id"])
        if (
            search
            and search not in model_id.casefold()
            and search not in str(model["name"]).casefold()
        ):
            continue
        unique.setdefault(model_id, model)
    discovered = list(unique.values())
    discovered.sort(key=lambda item: (str(item["name"]).casefold(), str(item["id"])))
    total = len(discovered)
    page = discovered[offset : offset + limit]
    return {
        "models": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total,
        "notice": notice,
    }


def detect_local_providers(config_path: Path) -> tuple[dict[str, object], ...]:
    """Probe only fixed loopback preset endpoints; never scan the LAN."""

    del config_path  # Reserved for future owner-local endpoint overrides.
    results: list[dict[str, object]] = []
    with httpx.Client(timeout=1.5, follow_redirects=False) as client:
        for provider_id, preset in _PROVIDER_PRESETS.items():
            if preset["mode"] != "local":
                continue
            endpoint = str(preset["base_url"])
            parsed = urlsplit(endpoint)
            if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                continue
            reachable = False
            authentication_required = False
            model_count = 0
            message = str(preset.get("launch_hint", "Start the local provider first."))
            try:
                response = client.get(endpoint + "/models")
                reachable = response.status_code < 500
                authentication_required = response.status_code in {401, 403}
                if response.status_code == 200:
                    document = response.json()
                    data = document.get("data") if isinstance(document, Mapping) else None
                    if isinstance(data, list):
                        model_count = len(data)
                        message = f"Detected {model_count} model{'s' if model_count != 1 else ''}."
            except (httpx.HTTPError, ValueError):
                pass
            results.append(
                {
                    "provider_id": provider_id,
                    "name": preset["name"],
                    "base_url": endpoint,
                    "reachable": reachable,
                    "authentication_required": authentication_required,
                    "model_count": model_count,
                    "message": message,
                }
            )
    return tuple(results)


def validate_provider_model(
    config_path: Path,
    *,
    provider_id: object,
    base_url: object,
    credential: object,
    model_id: object,
    transport: object,
    preset_id: object | None = None,
    headers: object = None,
) -> dict[str, object]:
    """Run one explicit bounded agent-compatibility probe.

    This call may perform a tiny inference request.  It is never run during
    ordinary save/startup and therefore cannot create invisible provider spend.
    Every returned field is a safe status, never a raw provider response or
    exception string.
    """

    requested_id = provider_id if isinstance(provider_id, str) else ""
    existing = _existing_connection(
        config_path, requested_id,
        replacing_secrets=bool(isinstance(credential, str) and credential.strip()) or bool(headers),
    ) if requested_id else None
    selected_preset_id = (
        preset_id
        if isinstance(preset_id, str) and preset_id
        else (existing[3] if existing is not None else requested_id)
    )
    preset_key, preset = _preset(selected_preset_id)
    if preset_key == "chatgpt_subscription":
        raise ConfigError("Use the ChatGPT subscription connection for model verification")
    endpoint = _base_url(
        preset_key,
        preset,
        base_url if base_url else (existing[0] if existing is not None else None),
    )
    upstream_model_id = _model_text(model_id, "ID", 256)
    selected_transport = _transport(transport, fallback=str(preset["default_transport"]))
    supplied = credential.strip() if isinstance(credential, str) else ""
    if not supplied and existing is not None:
        supplied = existing[1]
    supplied_headers = _private_headers(headers)
    if not supplied_headers and existing is not None:
        supplied_headers = existing[2]
    request_headers = dict(supplied_headers)
    if supplied and selected_transport not in {"anthropic_messages", "gemini_generate_content"}:
        request_headers.setdefault("Authorization", f"Bearer {supplied}")
    if selected_transport == "gemini_generate_content" and supplied:
        request_headers.setdefault("x-goog-api-key", supplied)
    if selected_transport == "anthropic_messages":
        if supplied:
            request_headers.setdefault("x-api-key", supplied)
        request_headers.setdefault("anthropic-version", "2023-06-01")
    if preset_key in {"opencode_go", "opencode_zen"}:
        from sylvia_gm.provider._request_metadata import opencode_request_headers

        request_headers.update(opencode_request_headers("provider-compatibility-probe"))

    report: dict[str, object] = {
        "reachability": "failed",
        "authentication": "unverified" if supplied else "not_required",
        "model_discovery": "unverified",
        "transport": "unverified",
        "streaming": "unverified",
        "tool_calling": "unverified",
        "agent_ready": False,
        "message": "The provider is unreachable. Start it or check the endpoint.",
    }
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            catalog_response = client.get(endpoint + "/models", headers=request_headers)
            if catalog_response.status_code in {401, 403}:
                report["reachability"] = "passed"
                report["authentication"] = "failed"
                report["message"] = "The provider rejected the API credential."
                return report
            report["reachability"] = "passed" if catalog_response.status_code < 500 else "failed"
            if report["reachability"] == "failed":
                return report
            if supplied and catalog_response.status_code == 200:
                report["authentication"] = "passed"
            if catalog_response.status_code == 200:
                try:
                    catalog = catalog_response.json()
                    rows = catalog.get("data") if isinstance(catalog, Mapping) else None
                    if isinstance(rows, list):
                        report["model_discovery"] = (
                            "passed"
                            if any(
                                isinstance(row, Mapping) and row.get("id") == upstream_model_id
                                for row in rows
                            )
                            else "model_not_listed"
                        )
                except ValueError:
                    report["model_discovery"] = "invalid_response"

            tool_name = "sylvia_compatibility_probe"
            if selected_transport == "responses":
                route = "/responses"
                payload: dict[str, object] = {
                    "model": upstream_model_id,
                    "input": "Call sylvia_compatibility_probe with an empty object.",
                    "stream": True,
                    "max_output_tokens": 128,
                    "tools": [
                        {
                            "type": "function",
                            "name": tool_name,
                            "description": "Confirm function-calling compatibility.",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                        }
                    ],
                    "tool_choice": "required",
                }
            elif selected_transport == "gemini_generate_content":
                from urllib.parse import quote

                route = "/models/" + quote(upstream_model_id.removeprefix("models/"), safe="") + ":streamGenerateContent?alt=sse"
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": "Call sylvia_compatibility_probe with an empty object."}]}],
                    "generationConfig": {"maxOutputTokens": 128},
                    "tools": [{"functionDeclarations": [{"name": tool_name,
                        "parametersJsonSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]}],
                    "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [tool_name]}},
                }
            elif selected_transport == "anthropic_messages":
                route = "/messages"
                payload = {
                    "model": upstream_model_id,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Call sylvia_compatibility_probe with an empty object.",
                        }
                    ],
                    "stream": True,
                    "max_tokens": 128,
                    "tools": [
                        {
                            "name": tool_name,
                            "description": "Confirm function-calling compatibility.",
                            "input_schema": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                        }
                    ],
                    "tool_choice": {"type": "tool", "name": tool_name},
                }
            else:
                route = "/chat/completions"
                payload = {
                    "model": upstream_model_id,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Call sylvia_compatibility_probe with an empty object.",
                        }
                    ],
                    "stream": True,
                    "max_tokens": 128,
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "description": "Confirm function-calling compatibility.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {},
                                    "additionalProperties": False,
                                },
                            },
                        }
                    ],
                    # Some otherwise-compatible local chat templates (including
                    # Qwen through LM Studio) mis-parse the OpenAI ``required``
                    # mode even though ordinary automatic tool calls work.
                    "tool_choice": "auto",
                }

            saw_stream_event = False
            saw_tool_call = False
            byte_count = 0
            with client.stream(
                "POST",
                endpoint + route,
                headers=request_headers,
                json=payload,
            ) as response:
                if response.status_code in {401, 403}:
                    report["authentication"] = "failed"
                    report["message"] = "The provider rejected the API credential."
                    return report
                if response.status_code != 200:
                    report["transport"] = "failed"
                    report["message"] = (
                        "The endpoint is reachable, but the selected transport was rejected."
                    )
                    return report
                report["transport"] = "passed"
                if supplied:
                    report["authentication"] = "passed"
                for line in response.iter_lines():
                    byte_count += len(line.encode("utf-8"))
                    if byte_count > 1024 * 1024:
                        break
                    if not line or line.startswith(":"):
                        continue
                    data = line[5:].strip() if line.startswith("data:") else line.strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, Mapping):
                        continue
                    saw_stream_event = True
                    if event.get("type") in {"error", "response.failed", "response.incomplete"}:
                        report["transport"] = "failed"
                        report["message"] = "The provider could not complete the test response."
                        return report
                    if selected_transport == "gemini_generate_content":
                        candidates = event.get("candidates", [])
                        for candidate in candidates if isinstance(candidates, list) else []:
                            for part in candidate.get("content", {}).get("parts", []):
                                saw_tool_call |= part.get("functionCall", {}).get("name") == tool_name
                    elif selected_transport == "anthropic_messages":
                        block = event.get("content_block")
                        saw_tool_call |= bool(
                            event.get("type") == "content_block_start"
                            and isinstance(block, Mapping)
                            and block.get("type") == "tool_use" and block.get("name") == tool_name
                        )
                    elif selected_transport == "responses":
                        item = event.get("item")
                        saw_tool_call |= bool(
                            isinstance(item, Mapping) and item.get("type") == "function_call"
                            and item.get("name") == tool_name
                        )
                    else:
                        for choice in event.get("choices", []):
                            delta = choice.get("delta", {}) if isinstance(choice, Mapping) else {}
                            if not isinstance(delta, Mapping):
                                continue
                            for call in delta.get("tool_calls", []) or []:
                                function = (
                                    call.get("function") if isinstance(call, Mapping) else None
                                )
                                saw_tool_call |= bool(
                                    isinstance(function, Mapping)
                                    and function.get("name") == tool_name
                                )
            report["streaming"] = "passed" if saw_stream_event else "failed"
            report["tool_calling"] = "passed" if saw_tool_call else "failed"
    except (httpx.HTTPError, ValueError):
        return report

    report["agent_ready"] = (
        report["reachability"] == "passed"
        and report["authentication"] != "failed"
        and report["transport"] == "passed"
        and report["streaming"] == "passed"
        and report["tool_calling"] == "passed"
    )
    report["message"] = (
        "Provider and model passed the agent compatibility probe."
        if report["agent_ready"]
        else "The provider connected, but this model did not pass every agent capability check."
    )
    return report


@_serialized_settings_write
def refresh_subscription_reasoning(
    config_path: Path, models: list[dict[str, object]],
) -> None:
    """Refresh advertised effort metadata on saved subscription models without other edits."""
    from sylvia_gm.provider._reasoning import reasoning_values

    document = _read_user_config(config_path)
    providers = document.get("providers")
    configured = providers.get("chatgpt_subscription") if isinstance(providers, dict) else None
    rows = configured.get("models") if isinstance(configured, dict) else None
    if not isinstance(rows, list):
        return
    by_id = {item.get("id"): item for item in models if isinstance(item.get("id"), str)}
    changed = False
    for row in rows:
        if not isinstance(row, dict) or row.get("transport") != "codex_app_server":
            continue
        advertised = by_id.get(row.get("id"))
        if advertised is None:
            continue
        supported, suggested = reasoning_values(
            advertised.get("supported_reasoning_efforts"),
            advertised.get("default_reasoning_effort"),
        )
        for key, value in {
            "supported_reasoning_efforts": None if supported is None else list(supported),
            "default_reasoning_effort": suggested,
        }.items():
            if row.get(key) != value:
                row[key] = value
                changed = True
    if changed:
        _write_user_config(config_path, document)


@_serialized_settings_write
def save_provider_settings(
    config_path: Path,
    *,
    provider_id: object,
    base_url: object,
    credential: object,
    model_id: object,
    model_name: object,
    context_window: object,
    max_output_tokens: object,
    declared_max_output_tokens: object = None,
    builder_output_tokens: object = None,
    preset_id: object | None = None,
    display_name: object = None,
    transport: object = None,
    headers: object = None,
    supports_streaming: object = None,
    supports_tools: object = None,
    supported_reasoning_efforts: object = None,
    default_reasoning_effort: object = None,
    declared_context_window: object = None,
    declared_context_source: object = None,
    runtime_context_window: object = None,
    runtime_context_source: object = None,
    configured_context_window: object = None,
    context_observed_at: object = None,
    declared_context_observed_at: object = None,
    runtime_context_observed_at: object = None,
    configured_context_observed_at: object = None,
) -> dict[str, object]:
    """Atomically add or update one model without replacing its provider catalog."""

    requested_id = provider_id if isinstance(provider_id, str) else ""
    document = _read_user_config(config_path)
    providers = document.setdefault("providers", {})
    if not isinstance(providers, dict):
        raise ConfigError("The local providers setting must be a mapping")
    existing_raw = providers.get(requested_id)
    existing_raw = existing_raw if isinstance(existing_raw, Mapping) else {}
    selected_preset = (
        preset_id
        if isinstance(preset_id, str) and preset_id
        else _provider_preset_id(requested_id, existing_raw)
    )
    preset_key, preset = _preset(selected_preset)
    endpoint = _base_url(preset_key, preset, base_url)
    name = _display_name(display_name, str(preset["name"]))
    profile_id = _provider_profile_id(
        provider_id,
        preset_id=preset_key,
        display_name=name,
        base_url=endpoint,
        document=document,
    )
    upstream_model_id = _model_text(model_id, "ID", 256)
    model_display_name = _model_text(model_name or upstream_model_id, "name", 128)
    defaults = documented_model_defaults(preset_key, upstream_model_id)
    declared_output = _optional_model_integer(
        declared_max_output_tokens, "model maximum output", minimum=1,
    )
    if declared_output is None:
        declared_output = defaults.get("max_output_tokens")
    configured_output = _optional_model_integer(max_output_tokens, "output limit", minimum=1)
    output = configured_output or declared_output
    if declared_output is not None and output is not None:
        output = min(output, declared_output)
    model_transport = _transport(
        transport, fallback=_model_transport_for_preset(preset_key, upstream_model_id),
    )
    if output is None and model_transport != "codex_app_server":
        from sylvia_gm.provider._output_policy import DEFAULT_OUTPUT_ALLOWANCE

        output = DEFAULT_OUTPUT_ALLOWANCE
    if preset_key == "openai" and defaults and model_transport != defaults["transport"]:
        raise ConfigError("Choose Responses for this model to use Sylvia's tools and reasoning")
    if (model_transport == "codex_app_server") != (preset_key == "chatgpt_subscription"):
        raise ConfigError("ChatGPT subscription requires its managed runtime connection")
    if preset_key == "chatgpt_subscription" and (credential or headers):
        raise ConfigError(
            "Use managed ChatGPT sign-in; API keys and private headers are not accepted"
        )
    supplied = credential.strip() if isinstance(credential, str) else ""

    existing = providers.get(profile_id, {})
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        raise ConfigError("The existing provider setting is invalid")
    try:
        old_key, old_headers = (
            unprotect_credentials(existing["protected_credentials"], resolve=False)
            if "protected_credentials" in existing else ("", {})
        )
    except ConfigError:
        if not supplied and not headers:
            raise
        # Explicit recovery replaces the unavailable secret material as a whole.
        old_key, old_headers = "", {}
    stored_credential = supplied or old_key
    if bool(preset["credential_required"]) and not stored_credential:
        raise ConfigError(f"{preset['name']} requires an API key")
    submitted_headers = _private_headers(headers)
    stored_headers = submitted_headers or old_headers
    if not isinstance(stored_headers, Mapping):
        raise ConfigError("The existing provider headers are invalid")

    models = existing.get("models", [])
    if not isinstance(models, list):
        raise ConfigError("The existing provider model catalog is invalid")
    prior = next(
        (row for row in models if isinstance(row, Mapping) and row.get("id") == upstream_model_id),
        None,
    )
    has_new_capacity = any(
        value is not None and value != ""
        for value in (
            declared_context_window,
            runtime_context_window,
            configured_context_window,
        )
    )
    legacy_context = None
    if not has_new_capacity:
        legacy_context = _optional_model_integer(context_window, "legacy context window")
        if legacy_context is None and isinstance(prior, Mapping):
            legacy_capacity = _capacity_from_model_row(prior)
            if legacy_capacity.effective_context_source == "legacy_unverified":
                legacy_context = legacy_capacity.effective_context_window
    declared = _optional_model_integer(declared_context_window, "declared context window")
    runtime = _optional_model_integer(runtime_context_window, "runtime context window")
    configured = _optional_model_integer(configured_context_window, "configured context window")
    observed = _capacity_timestamp(context_observed_at)
    prior_capacity = _capacity_from_model_row(prior) if isinstance(prior, Mapping) else None
    declared_observed = _capacity_timestamp(declared_context_observed_at) or observed
    runtime_observed = _capacity_timestamp(runtime_context_observed_at) or observed
    configured_observed = _capacity_timestamp(configured_context_observed_at)
    if defaults:
        if declared is None:
            declared = defaults["context_window"]
            declared_context_source = "model_catalog"
            declared_observed = defaults["documented_at"]
        if runtime is None:
            runtime = defaults["context_window"]
            runtime_context_source = "model_catalog"
            runtime_observed = defaults["documented_at"]
        # Old single-value caps still represent the user's selected allowance.
        if configured is None and legacy_context is not None:
            configured = legacy_context
    if configured is not None and configured_observed is None:
        configured_observed = (
            prior_capacity.configured_observed_at
            if prior_capacity is not None and prior_capacity.configured_context_window == configured
            else datetime.now(UTC).isoformat()
        )
    capacity = resolve_context_capacity(
        declared_context_window=declared,
        declared_context_source=_capacity_source(declared_context_source),
        runtime_context_window=runtime,
        runtime_context_source=_capacity_source(runtime_context_source),
        configured_context_window=configured,
        legacy_context_window=legacy_context,
        observed_at=observed,
        declared_observed_at=declared_observed,
        runtime_observed_at=runtime_observed,
        configured_observed_at=configured_observed,
    )
    if (configured_output is None and declared_output is None and output is not None
            and capacity.effective_context_window is not None):
        output = min(output, max(1, capacity.effective_context_window // 4))
    if (capacity.effective_context_window is not None and output is not None
            and model_transport != "codex_app_server" and output > capacity.effective_context_window):
        raise ConfigError("Provider model output limit exceeds its effective context capacity")
    previous_builder_budget = prior.get("builder_output_tokens") if prior is not None else None
    if previous_builder_budget is not None:
        previous_builder_budget = _model_integer(
            previous_builder_budget, "Saved Builder output limit", 1, 2_000_000,
        )
    builder_budget = (
        _model_integer(builder_output_tokens, "Builder output limit", 1, output or 10_000_000)
        if builder_output_tokens is not None else min(previous_builder_budget or 32_000, output or 32_000)
    )
    replacement = {
        "id": upstream_model_id,
        "name": model_display_name,
        **capacity.projection(),
        "max_output_tokens": output,
        "declared_max_output_tokens": declared_output,
        "configured_output_tokens": configured_output,
        "output_allowance_source": (
            "user_configured" if configured_output is not None else
            "provider_metadata" if declared_output is not None else
            "runtime_managed" if model_transport == "codex_app_server" else "application_default"
        ),
        "documented_source": defaults.get("documented_source"),
        "documented_at": defaults.get("documented_at"),
        "builder_output_tokens": builder_budget,
        "transport": model_transport,
        "supports_streaming": (
            supports_streaming if isinstance(supports_streaming, bool)
            else defaults.get("supports_streaming")
        ),
        "supports_tools": (
            supports_tools if isinstance(supports_tools, bool) else defaults.get("supports_tools")
        ),
    }
    if model_transport == "codex_app_server":
        from sylvia_gm.provider._reasoning import reasoning_values

        efforts = supported_reasoning_efforts
        suggested = default_reasoning_effort
        if efforts is None and prior is not None:
            efforts = prior.get("supported_reasoning_efforts")
            suggested = prior.get("default_reasoning_effort")
        try:
            supported, suggested = reasoning_values(efforts, suggested)
        except ValueError as error:
            raise ConfigError(str(error)) from error
        replacement["supported_reasoning_efforts"] = None if supported is None else list(supported)
        replacement["default_reasoning_effort"] = suggested
    updated_models: list[dict[str, object]] = []
    replaced = False
    for row in models:
        if isinstance(row, Mapping) and row.get("id") == upstream_model_id:
            updated_models.append(replacement)
            replaced = True
        elif isinstance(row, Mapping):
            updated_models.append(dict(row))
    if not replaced:
        updated_models.append(replacement)

    providers[profile_id] = {
        "enabled": True,
        "preset_id": preset_key,
        "mode": preset["mode"],
        "name": name,
        "base_url": endpoint,
        "api_key": stored_credential,
        "headers": dict(stored_headers),
        "models": updated_models,
    }
    _write_user_config(config_path, document)
    return {
        "provider_id": profile_id,
        "preset_id": preset_key,
        "name": name,
        "model_id": upstream_model_id,
        "canonical_model_id": _canonical_model_id(profile_id, upstream_model_id),
        "credential_configured": bool(stored_credential),
    }


@_serialized_settings_write
def set_default_model_settings(
    config_path: Path, *, provider_id: object, model_id: object
) -> dict[str, str]:
    """Persist the exact configured model used for newly created sessions."""

    if not isinstance(provider_id, str) or not _PROFILE_ID.fullmatch(provider_id):
        raise ConfigError("Default provider identity is invalid")
    if not isinstance(model_id, str) or not model_id:
        raise ConfigError("Default model identity is invalid")
    document = _read_user_config(config_path)
    if not _configured_model_exists(document, provider_id, model_id):
        raise ConfigError("Default model must be exactly configured")
    default = {"provider_id": provider_id, "model_id": model_id}
    document["default_model"] = default
    _write_user_config(config_path, document)
    return default


@_serialized_settings_write
def set_session_model_once_settings(
    config_path: Path,
    *,
    session_id: object,
    provider_id: object,
    model_id: object,
) -> dict[str, str]:
    """Durably stage one model selection for only the session's next provider turn."""

    if (
        not isinstance(session_id, str)
        or not session_id
        or session_id != session_id.strip()
        or len(session_id) > 512
    ):
        raise ConfigError("Session identity is invalid")
    selection = require_configured_model(
        config_path,
        provider_id=provider_id,
        model_id=model_id,
    )
    document = _read_user_config(config_path)
    pending = document.setdefault("session_model_once", {})
    if not isinstance(pending, dict):
        raise ConfigError("Pending session model settings are invalid")
    pending[session_id] = selection
    _write_user_config(config_path, document)
    return selection


def session_model_once_settings(config_path: Path, *, session_id: object) -> dict[str, str] | None:
    """Read one pending safe model override without consuming it."""

    if not isinstance(session_id, str) or not session_id:
        return None
    document = _read_user_config(config_path)
    pending = document.get("session_model_once")
    raw = pending.get(session_id) if isinstance(pending, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    provider_id = raw.get("provider_id")
    model_id = raw.get("model_id")
    try:
        return require_configured_model(
            config_path,
            provider_id=provider_id,
            model_id=model_id,
        )
    except ConfigError:
        return None


@_serialized_settings_write
def clear_session_model_once_settings(
    config_path: Path,
    *,
    session_id: object,
    expected_provider_id: object,
    expected_model_id: object,
) -> bool:
    """Consume only the pending one-turn selection that actually ran."""

    if not isinstance(session_id, str) or not session_id:
        return False
    document = _read_user_config(config_path)
    pending = document.get("session_model_once")
    raw = pending.get(session_id) if isinstance(pending, dict) else None
    if not isinstance(raw, Mapping) or raw != {
        "provider_id": expected_provider_id,
        "model_id": expected_model_id,
    }:
        return False
    pending_map = cast(dict[str, Any], pending)
    pending_map.pop(session_id, None)
    if not pending_map:
        document.pop("session_model_once", None)
    _write_user_config(config_path, document)
    return True


@_serialized_settings_write
def remove_provider_settings(config_path: Path, provider_id: object) -> None:
    """Remove one owner-local provider entry, including stored private values."""

    if not isinstance(provider_id, str) or not _PROFILE_ID.fullmatch(provider_id):
        raise ConfigError("Provider identity is invalid")
    document = _read_user_config(config_path)
    providers = document.get("providers")
    if isinstance(providers, dict):
        providers.pop(provider_id, None)
    default = document.get("default_model")
    if isinstance(default, Mapping) and default.get("provider_id") == provider_id:
        document.pop("default_model", None)
    pending = document.get("session_model_once")
    if isinstance(pending, dict):
        for session_id, selection in tuple(pending.items()):
            if isinstance(selection, Mapping) and selection.get("provider_id") == provider_id:
                pending.pop(session_id, None)
        if not pending:
            document.pop("session_model_once", None)
    _write_user_config(config_path, document)
