"""Provider-native speed tiers and the immutable per-turn request selection.

Speed is separate from reasoning effort, model identity and output limits. Sylvia only
offers a tier where the exact route documents or advertises it, sends the provider's own
value, and never claims the provider served the request at that tier.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.provider._base import ModelInfo, SpeedOption

STANDARD_SPEED = "standard"
"""The owner-protocol word for "send no speed setting"; never a provider value."""

_TIER_ID = re.compile(r"[a-z][a-z0-9_-]{0,31}")

# https://developers.openai.com/api/docs/pricing (Fast and Flex tables, checked 2026-11-12).
# `service_tier` values: https://developers.openai.com/api/docs/guides/fast-mode and
# https://developers.openai.com/api/docs/guides/flex-processing (checked 2026-11-12).
_OPENAI_SOURCE = "https://developers.openai.com/api/docs/pricing"
_OPENAI_FAST = frozenset({
    "gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna", "gpt-6-sol",
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4", "gpt-5.4-mini",
    "gpt-5.2", "gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4o",
    "gpt-4o-mini",
})
_OPENAI_FLEX = frozenset({
    "gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna", "gpt-6-sol",
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.5-pro", "gpt-5.4",
    "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.2", "gpt-5.1", "gpt-5",
    "gpt-5-mini", "gpt-5-nano",
})
# The same aliases the reasoning table already treats as their documented model.
_OPENAI_ALIASES = {"gpt-5.6": "gpt-5.6-sol", "gpt-5.5-2026-04-23": "gpt-5.5"}
_OPENAI_FAST_OPTION = SpeedOption(
    "fast", "Fast",
    "Requests OpenAI Fast mode. Billed at a higher per-token rate; OpenAI may still serve "
    "a request at standard speed and price.",
)
_OPENAI_FLEX_OPTION = SpeedOption(
    "flex", "Flex",
    "Requests OpenAI Flex processing. Billed at a lower rate; replies are slower and can "
    "fail when capacity is unavailable.",
)

# https://platform.claude.com/docs/en/build-with-claude/fast-mode (checked 2026-11-12).
# Only models whose fast mode needs no beta header or separately provisioned access.
_ANTHROPIC_SOURCE = "https://platform.claude.com/docs/en/build-with-claude/fast-mode"
_ANTHROPIC_FAST = frozenset({"claude-opus-5-5", "claude-fable-5-2", "claude-mythos-5-2"})
_ANTHROPIC_FAST_OPTION = SpeedOption(
    "fast", "Fast",
    "Requests Anthropic fast mode. Billed at a higher per-token rate and limited "
    "separately from standard speed.",
)


def speed_options(values: object) -> tuple[SpeedOption, ...] | None:
    """Validate saved runtime-advertised tiers without inventing a universal option list."""
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or len(values) > 8:
        raise ValueError("Speed tiers must be a short list of advertised options")
    options: dict[str, SpeedOption] = {}
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("Speed tiers must be advertised options")
        tier_id, name = value.get("id"), value.get("name")
        description = value.get("description", "")
        if (
            not isinstance(tier_id, str) or not _TIER_ID.fullmatch(tier_id)
            or tier_id == STANDARD_SPEED
            or not isinstance(name, str) or not name.strip() or len(name) > 40
            or not isinstance(description, str) or len(description) > 200
        ):
            raise ValueError("Speed tiers must name a native setting and a short label")
        options.setdefault(tier_id, SpeedOption(tier_id, name.strip(), description.strip()))
    return tuple(options.values())


def configure_speed(model: ModelInfo, base_url: str) -> ModelInfo:
    """Bind documented speed tiers only to their actual endpoint, transport and model."""
    if model.transport == "codex_app_server":
        # model/list is the authority, including tiers unknown to this Sylvia version.
        advertised = model.speed_options
        return replace(
            model,
            speed_options=advertised or None,
            speed_control="codex" if advertised else None,
            speed_source="model/list" if advertised else "unknown",
        )
    endpoint = urlsplit(base_url)
    secure = endpoint.scheme == "https" and endpoint.port in (None, 443)
    wire_id = model.wire_model_id
    if secure and endpoint.hostname == "api.openai.com" and model.transport in {
        "responses",
        "chat_completions",
    }:
        documented_id = _OPENAI_ALIASES.get(wire_id, wire_id)
        options = tuple(
            option
            for option, supported in (
                (_OPENAI_FAST_OPTION, _OPENAI_FAST),
                (_OPENAI_FLEX_OPTION, _OPENAI_FLEX),
            )
            if documented_id in supported
        )
        if options:
            return replace(
                model, speed_options=options, speed_control="openai",
                speed_source=_OPENAI_SOURCE,
            )
    if (
        secure and endpoint.hostname == "api.anthropic.com"
        and model.transport == "anthropic_messages" and wire_id in _ANTHROPIC_FAST
    ):
        return replace(
            model, speed_options=(_ANTHROPIC_FAST_OPTION,), speed_control="anthropic",
            speed_source=_ANTHROPIC_SOURCE,
        )
    return replace(model, speed_options=None, speed_control=None, speed_source="unknown")


def supported_speed_ids(model: ModelInfo | None) -> tuple[str, ...]:
    """Return the native tier ids this model can be asked for; empty when there is no choice."""
    if model is None or not model.speed_options:
        return ()
    return tuple(option.id for option in model.speed_options)


@dataclass(frozen=True)
class SpeedSelection:
    """One reply's speed choice; effective denotes our request, not provider confirmation."""

    requested: str | None
    effective: str | None
    control: str | None
    scope: str

    def options(self) -> dict[str, object]:
        """Return the native request field, or nothing for the provider's own default."""
        if self.effective is None:
            return {}
        if self.control in {"codex", "openai"}:
            return {"service_tier": self.effective}
        if self.control == "anthropic":
            return {"speed": self.effective}
        return {}

    def projection(self) -> dict[str, object]:
        """Keep the requested tier distinct from unreported provider behavior."""
        return {
            "requested": self.requested,
            "effective_request": self.effective,
            "scope": self.scope,
            "native_options": self.options(),
            "provider_reported_effective": None,
        }


def select_speed(
    model: ModelInfo | None,
    requested: object = None,
    *,
    strict: bool = True,
    scope: str = "session",
) -> SpeedSelection:
    """Resolve a chat's speed for one model.

    A strict selection rejects an unsupported tier before anything is saved. A saved tier
    that the reply's model does not offer (for example after a model change) is not sent:
    the reply uses the provider's default rather than failing or guessing an equivalent.
    """
    control = model.speed_control if model else None
    if requested is None or requested == STANDARD_SPEED:
        return SpeedSelection(None, None, control, "provider_default")
    if isinstance(requested, str) and requested in supported_speed_ids(model):
        return SpeedSelection(requested, requested, control, scope)
    if strict:
        raise CommandRejected(
            "This model does not offer the selected speed. Choose an available speed "
            "or Standard before sending."
        )
    return SpeedSelection(
        requested if isinstance(requested, str) else None, None, control,
        "unavailable_for_model",
    )
