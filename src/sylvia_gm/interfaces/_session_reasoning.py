"""Saved-chat reasoning selection using the configured catalog's safe capability projection."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionRecord
from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._reasoning import reasoning_values, select_reasoning


def selected_model_info(
    session: SessionRecord,
    choices: Sequence[Mapping[str, object]],
) -> ModelInfo | None:
    """Read only catalog capability facts; a display model name never grants support."""
    choice = next(
        (
            item
            for item in choices
            if item.get("provider_id") == session.provider_id
            and item.get("model_id") == session.model_id
        ),
        None,
    )
    if choice is None:
        return None
    supported, suggested = reasoning_values(
        choice.get("supported_reasoning_efforts"),
        choice.get("default_reasoning_effort"),
    )
    transport = choice.get("transport", "chat_completions")
    if transport not in (
        "chat_completions", "responses", "anthropic_messages", "codex_app_server",
        "gemini_generate_content",
    ):
        raise ValueError("Configured model transport is unsupported")
    control = choice.get("reasoning_control")
    return ModelInfo(
        id=session.model_id,
        name=str(choice.get("model_name", session.model_id)),
        provider_key=session.provider_id,
        provider_name=str(choice.get("provider_name", "")),
        provider_mode="unknown",
        supported_reasoning_efforts=supported,
        default_reasoning_effort=suggested,
        reasoning_control=control if isinstance(control, str) else None,
        reasoning_source=str(choice.get("reasoning_source", "unknown")),
        transport=transport,
    )


def validate_model_preferences(
    session: SessionRecord,
    choices: Sequence[Mapping[str, object]],
    *,
    once: bool,
) -> None:
    """Require valid explicit settings before accepting a destination model."""
    model = selected_model_info(session, choices)
    values = (
        (session.reasoning_effort_once or session.reasoning_effort,)
        if once
        else (session.reasoning_effort, session.reasoning_effort_once)
    )
    for value in values:
        if value is not None:
            try:
                select_reasoning(model, session.role.value, value)
            except CommandRejected as error:
                raise CommandRejected(
                    f"The destination model does not support reasoning effort '{value}'. "
                    "Clear the next-reply override or select Mode default for this chat "
                    "before switching models."
                ) from error


def reasoning_state(
    session: SessionRecord,
    choices: Sequence[Mapping[str, object]],
    once_model: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Describe the next request without claiming the provider confirmed its actual effort."""
    effective_session = (
        session
        if once_model is None
        else replace(
            session,
            provider_id=once_model["provider_id"],
            model_id=once_model["model_id"],
        )
    )
    model = selected_model_info(effective_session, choices)
    requested = session.reasoning_effort_once or session.reasoning_effort
    scope = (
        "once"
        if session.reasoning_effort_once is not None
        else "session"
        if session.reasoning_effort is not None
        else "mode_default"
    )
    result: dict[str, object] = {
        "current": session.reasoning_effort,
        "once": session.reasoning_effort_once,
        "provider_id": effective_session.provider_id,
        "model_id": effective_session.model_id,
        "supported": list(model.supported_reasoning_efforts)
        if model is not None and model.supported_reasoning_efforts is not None
        else None,
        "capability_source": model.reasoning_source if model else "unknown",
    }
    try:
        selection = select_reasoning(model, session.role.value, requested, scope=scope)
    except CommandRejected as error:
        result.update(
            {
                "valid": False,
                "error": str(error),
                "requested": requested,
                "scope": scope,
                "effective_request": None,
            }
        )
    else:
        result.update({"valid": True, **selection.projection()})
    return result
