"""Model, effort and speed a newly created chat starts from, settled before its first call.

A new Builder or Game Master chat copies the Settings defaults once, when it is created.
The person starting it may instead choose each value for that chat. Either way the chat
keeps its own copy: changing Settings later never changes an existing chat, and changing
a chat never changes Settings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sylvia_gm.config.provider_settings import provider_settings, set_session_speed_settings
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._reasoning import select_reasoning
from sylvia_gm.provider._speed import STANDARD_SPEED, select_speed, supported_speed_ids

from . import _session_reasoning
from .commands import DispatchContext


@dataclass(frozen=True)
class ChatStart:
    """One new chat's settled start: None effort is the mode default, None speed is standard."""

    provider_id: str
    model_id: str
    reasoning_effort: str | None
    speed: str | None


def explicit_effort(model: ModelInfo | None, role: str, value: object) -> str | None:
    """Validate a chosen effort for this model and return the value a chat stores."""
    if not isinstance(value, str):
        raise CommandRejected("Reasoning effort must be a named setting")
    select_reasoning(model, role, value)
    return None if value == "mode_default" else value


def explicit_speed(model: ModelInfo | None, value: object) -> str | None:
    """Validate a chosen speed for this model and return the value a chat stores."""
    if not isinstance(value, str):
        raise CommandRejected("Speed must be a named setting")
    return select_speed(model, value).effective


def resolve_chat_start(
    choices: Sequence[Mapping[str, object]],
    defaults: Mapping[str, object],
    *,
    role: str,
    provider_id: str,
    model_id: str,
    params: Mapping[str, Any],
) -> ChatStart:
    """Settle a new chat's effort and speed, rejecting an unsupported explicit choice.

    A value named in ``params`` is the person's choice for this chat and must be one the
    model supports. An absent value inherits the Settings default; when the chat's model
    does not support that default, the chat falls back to the mode default effort or
    standard speed instead of failing or guessing a nearby level.
    """
    model = _session_reasoning.catalog_model_info(provider_id, model_id, choices)
    effort: str | None = None
    chosen_effort = params.get("reasoning_effort")
    if chosen_effort is not None:
        effort = explicit_effort(model, role, chosen_effort)
    else:
        saved_effort = defaults.get("reasoning_effort")
        supported = (model.supported_reasoning_efforts if model is not None else None) or ()
        if isinstance(saved_effort, str) and (
            saved_effort == "provider_default" or saved_effort in supported
        ):
            effort = saved_effort
    speed: str | None = None
    chosen_speed = params.get("speed")
    if chosen_speed is not None:
        speed = explicit_speed(model, chosen_speed)
    else:
        saved_speed = defaults.get("speed")
        if isinstance(saved_speed, str) and saved_speed in supported_speed_ids(model):
            speed = saved_speed
    return ChatStart(provider_id, model_id, effort, speed)


def chat_start_for(
    ctx: DispatchContext,
    *,
    role: str,
    provider_id: str,
    model_id: str,
    params: Mapping[str, Any],
) -> ChatStart:
    """Resolve a new chat's start against the current Settings defaults and model catalog."""
    defaults = provider_settings(ctx.storage_root / "config.yaml").get("chat_defaults")
    return resolve_chat_start(
        ctx.model_choices(),
        defaults if isinstance(defaults, Mapping) else {},
        role=role,
        provider_id=provider_id,
        model_id=model_id,
        params=params,
    )


def session_exists(ctx: DispatchContext, session_id: str) -> bool:
    """Tell a replayed creation command from a first one, before the chat is created.

    Start settings belong to the moment a chat is created. A command replayed with the
    same identity returns the existing chat and must leave its current settings alone.
    """
    try:
        ctx.application_owner.session_context(session_id)
    except CommandRejected as error:
        if error.code == "session_unavailable":
            return False
        raise
    return True


def apply_chat_start(ctx: DispatchContext, session_id: str, start: ChatStart) -> None:
    """Save a settled start on the new chat. Nothing here calls a model."""
    if start.reasoning_effort is not None:
        ctx.application_owner.update_agent_session_reasoning(
            session_id, effort=start.reasoning_effort, once=None,
        )
    if start.speed is not None:
        set_session_speed_settings(
            ctx.storage_root / "config.yaml", session_id=session_id, speed=start.speed,
        )


def chat_start_projection(start: ChatStart) -> dict[str, object]:
    """Report what a new chat actually started with, in owner-protocol words."""
    return {
        "provider_id": start.provider_id,
        "model_id": start.model_id,
        "reasoning_effort": start.reasoning_effort or "mode_default",
        "speed": start.speed or STANDARD_SPEED,
    }
