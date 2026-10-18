"""Provider-native reasoning capabilities and immutable per-turn request selection."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, cast
from urllib.parse import urlsplit

from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionRecord
from sylvia_gm.provider._base import (
    ModelInfo,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    closing_provider_stream,
)
from sylvia_gm.provider._model_defaults import documented_model_defaults


def reasoning_values(
    values: object, suggested: object
) -> tuple[tuple[str, ...] | None, str | None]:
    """Validate bounded native names without inventing a universal provider option list."""
    if values is None:
        if suggested is not None:
            raise ValueError("A reasoning default requires advertised reasoning options")
        return None, None
    if (
        not isinstance(values, (list, tuple))
        or len(values) > 16
        or any(
            not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value)
            for value in values
        )
    ):
        raise ValueError("Reasoning options must be a short list of native setting names")
    supported = tuple(dict.fromkeys(values))
    if suggested is not None and (not isinstance(suggested, str) or suggested not in supported):
        raise ValueError("The suggested reasoning default must be an advertised option")
    return supported, suggested


def configure_reasoning(model: ModelInfo, base_url: str) -> ModelInfo:
    """Bind documented controls only to their actual endpoint and supported transport."""
    if model.transport == "codex_app_server":
        # model/list is the authority, including options unknown to this Sylvia version.
        # https://learn.chatgpt.com/docs/app-server#models (checked 2026-09-22).
        return replace(
            model,
            reasoning_control="codex",
            reasoning_source="model/list"
            if model.supported_reasoning_efforts is not None
            else "unknown",
        )
    endpoint = urlsplit(base_url)
    if endpoint.scheme != "https" or endpoint.port not in (None, 443):
        return replace(model, supported_reasoning_efforts=None, default_reasoning_effort=None)
    wire_id = model.wire_model_id
    native_route = {
        ("api.groq.com", "/openai/v1"): ("groq", "https://console.groq.com/docs/reasoning"),
        ("api.deepseek.com", ""): (
            "deepseek", "https://api-docs.deepseek.com/api/create-chat-completion/",
        ),
    }.get((endpoint.hostname, endpoint.path.rstrip("/")))
    if native_route and model.transport == "chat_completions":
        defaults = documented_model_defaults(native_route[0], wire_id)
        if defaults:
            return replace(
                model,
                supported_reasoning_efforts=defaults["supported_reasoning_efforts"],
                default_reasoning_effort=defaults["default_reasoning_effort"],
                reasoning_control="openai",
                reasoning_source=native_route[1],
            )
    if endpoint.hostname == "api.openai.com" and model.transport in {
        "responses",
        "chat_completions",
    }:
        defaults = documented_model_defaults("openai", wire_id)
        if defaults and model.transport == "responses":
            return replace(
                model,
                supported_reasoning_efforts=defaults["supported_reasoning_efforts"],
                default_reasoning_effort=defaults["default_reasoning_effort"],
                reasoning_control="openai",
                reasoning_source=defaults["documented_source"],
            )
        # Exact model policies, not a GPT-name heuristic for arbitrary compatible endpoints.
        # https://developers.openai.com/api/docs/models/gpt-5.2 (checked 2026-09-22).
        supported = {
            "gpt-5.2": ("none", "low", "medium", "high", "xhigh"),
            "gpt-5.5": ("none", "low", "medium", "high", "xhigh"),
            "gpt-5.5-2026-04-23": ("none", "low", "medium", "high", "xhigh"),
            "gpt-5.6-sol": ("none", "low", "medium", "high", "xhigh", "max"),
            "gpt-5.6": ("none", "low", "medium", "high", "xhigh", "max"),
        }.get(wire_id)
        if supported is not None:
            documented_id = (
                "gpt-5.6-sol"
                if wire_id == "gpt-5.6"
                else "gpt-5.5"
                if wire_id == "gpt-5.5-2026-04-23"
                else wire_id
            )
            return replace(
                model,
                supported_reasoning_efforts=supported,
                default_reasoning_effort="none" if wire_id == "gpt-5.2" else "medium",
                reasoning_control="openai",
                reasoning_source=f"https://developers.openai.com/api/docs/models/{documented_id}",
            )
    if endpoint.hostname == "api.anthropic.com" and model.transport == "anthropic_messages":
        # https://platform.claude.com/docs/en/build-with-claude/effort (2026-09-22).
        supported = {
            "claude-opus-4-6": ("low", "medium", "high", "max"),
            "claude-sonnet-4-6": ("low", "medium", "high", "max"),
            "claude-opus-4-7": ("low", "medium", "high", "xhigh", "max"),
            "claude-opus-4-8": ("low", "medium", "high", "xhigh", "max"),
            "claude-opus-5": ("low", "medium", "high", "xhigh", "max"),
            "claude-sonnet-5": ("low", "medium", "high", "xhigh", "max"),
        }.get(wire_id)
        if supported is not None:
            return replace(
                model,
                supported_reasoning_efforts=supported,
                default_reasoning_effort="high",
                reasoning_control="anthropic",
                reasoning_source="https://platform.claude.com/docs/en/build-with-claude/effort",
            )
    minimax_endpoint = endpoint.hostname in {"api.minimax.io", "api.minimaxi.com"} or (
        endpoint.hostname == "opencode.ai"
        and endpoint.path.rstrip("/")
        in {
            "/zen/go/v1",
            "/zen/v1",
        }
    )
    if minimax_endpoint and (
        model.transport == "anthropic_messages" and wire_id.casefold() == "minimax-m3"
    ):
        # M3 exposes on/off adaptive thinking, not graduated effort or a token budget.
        # https://platform.minimax.io/docs/api-reference/text-anthropic-api (2026-09-22).
        # The two existing OpenCode presets already use this same tested M3 Messages control.
        return replace(
            model,
            supported_reasoning_efforts=("disabled", "adaptive"),
            default_reasoning_effort="disabled",
            reasoning_control="adaptive",
            reasoning_source="https://platform.minimax.io/docs/api-reference/text-anthropic-api",
        )
    return replace(
        model,
        supported_reasoning_efforts=None,
        default_reasoning_effort=None,
        reasoning_control=None,
        reasoning_source="unknown",
    )


def mode_reasoning_default(model: ModelInfo | None, role: str) -> str | None:
    """Map GM High / Builder X-High to visible native values without changing budgets."""
    if model is None:
        return None
    supported = model.supported_reasoning_efforts
    if not supported:
        return None
    if model.reasoning_control == "adaptive":
        return "adaptive"
    target = "xhigh" if role == "builder" else "high"
    if target in supported:
        return target
    # Prefer the closest named lower level when the target is absent. In particular High,
    # rather than Max, is our default for Claude 4.6; this does not claim equal intelligence.
    scale = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
    rank = scale.index(target)
    return next((value for value in reversed(scale[:rank]) if value in supported), None) or (
        model.default_reasoning_effort
        if model.default_reasoning_effort is not None
        and model.default_reasoning_effort in supported
        else next((value for value in scale[rank + 1 :] if value in supported), supported[0])
    )


@dataclass(frozen=True)
class ReasoningSelection:
    """One reply's validated choice; effective denotes our request, not provider confirmation."""

    requested: str | None
    effective: str | None
    control: str | None
    transport: str | None
    scope: str

    def options(self) -> dict[str, object]:
        """Return native request fields without touching budgets, tiers, or models."""
        if self.effective is None:
            return {}
        if self.control == "codex":
            return {"reasoning_effort": self.effective}
        if self.control == "openai":
            return (
                {"reasoning": {"effort": self.effective}}
                if self.transport == "responses"
                else {
                    "reasoning_effort": self.effective,
                }
            )
        if self.control == "anthropic":
            return {"thinking": {"type": "adaptive"}, "output_config": {"effort": self.effective}}
        if self.control == "adaptive":
            return {"thinking": {"type": self.effective}}
        return {}

    def projection(self) -> dict[str, object]:
        """Keep requested native options distinct from unreported provider behavior."""
        return {
            "requested": self.requested,
            "effective_request": self.effective,
            "scope": self.scope,
            "native_options": self.options(),
            "provider_reported_effective": None,
        }


def select_reasoning(
    model: ModelInfo | None,
    role: str,
    requested: object = None,
    *,
    scope: str = "mode_default",
) -> ReasoningSelection:
    """Reject unsupported explicit values before inference; absence uses the role default."""
    supported = model.supported_reasoning_efforts if model is not None else None
    if (
        requested is not None
        and requested not in ("provider_default", "mode_default")
        and (not isinstance(requested, str) or not supported or requested not in supported)
    ):
        raise CommandRejected(
            "This model does not support the selected reasoning effort. Choose a supported "
            "value or use the mode default before sending."
        )
    effective = (
        mode_reasoning_default(model, role)
        if requested is None or requested == "mode_default"
        else None
        if requested == "provider_default"
        else requested
    )
    return ReasoningSelection(
        requested,
        effective,
        model.reasoning_control if model else None,
        model.transport if model else None,
        scope,
    )


class TurnReasoningProvider(Provider):
    """Apply one immutable selection to every provider call in a reply, including continuations."""

    def __init__(self, provider: Provider, selection: ReasoningSelection) -> None:
        super().__init__()
        self._provider = provider
        self.selection = selection
        self.model_info = getattr(provider, "model_info", None)
        self.hooks = provider.hooks
        self._base_url = getattr(provider, "_base_url", None)
        self.set_audit_context({})

    def set_audit_context(self, context: Mapping[str, object]) -> None:
        """Keep the turn's requested effort visible alongside ordinary request correlation."""
        setter = getattr(self._provider, "set_audit_context", None)
        if callable(setter):
            setter({**context, "reasoning_selection": self.selection.projection()})

    def record_context_fit(self, context: Mapping[str, object]) -> None:
        """Forward preflight evidence without losing the selected native effort."""
        recorder = getattr(self._provider, "record_context_fit", None)
        if callable(recorder):
            recorder({**context, "reasoning_selection": self.selection.projection()})

    def _options(self, options: Mapping[str, object]) -> dict[str, object]:
        """A snapshotted choice wins over call-local reasoning defaults, without shared mutation."""
        result = deepcopy(dict(options))
        for key in ("reasoning_effort", "thinking"):
            result.pop(key, None)
        for key in ("reasoning", "output_config"):
            value = result.get(key)
            if isinstance(value, dict):
                value.pop("effort", None)
                if not value:
                    result.pop(key)
        for key, value in self.selection.options().items():
            existing = result.get(key)
            if (
                key in {"reasoning", "output_config"}
                and isinstance(existing, dict)
                and isinstance(value, dict)
            ):
                existing.update(value)
            else:
                result[key] = value
        return result

    def provider_request_representation(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        options: Mapping[str, object],
    ) -> dict[str, Any]:
        """Preview exactly the native options used by the request for fit and audit."""
        target: Provider | None = self._provider
        seen = set()
        while target is not None and id(target) not in seen:
            seen.add(id(target))
            preview = getattr(target, "provider_request_representation", None)
            if callable(preview):
                return cast(dict[str, Any], preview(messages, tools, self._options(options)))
            target = getattr(target, "_provider", None)
        return {"messages": messages, "tools": tools or [], "options": self._options(options)}

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        return await self._provider.generate(
            messages, tools=tools, stream=stream, timeout=timeout, **self._options(kwargs)
        )

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        async with closing_provider_stream(self._provider.generate_stream(
            messages, tools=tools, timeout=timeout, **self._options(kwargs)
        )) as stream:
            async for chunk in stream:
                yield chunk

    async def close(self) -> None:
        await self._provider.close()


def bind_session_reasoning(provider: Provider, session: SessionRecord) -> TurnReasoningProvider:
    """Resolve preferences once for all calls in this session's next provider turn."""
    requested = session.reasoning_effort_once or session.reasoning_effort
    scope = ("once" if session.reasoning_effort_once is not None else
             "session" if session.reasoning_effort is not None else "mode_default")
    return TurnReasoningProvider(provider, select_reasoning(
        getattr(provider, "model_info", None), session.role.value, requested, scope=scope,
    ))
