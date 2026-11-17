"""Character chat commands: pick a Universal character card and talk to that character."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from sylvia_gm.agent._provider_runtime import _PROVIDER_ASYNC_RUNTIME
from sylvia_gm.application.character_chat import (
    CharacterChat,
    chat_catalog,
    chat_projection,
    collect_reply,
    create_chat,
    load_chat,
    send_message,
    validate_model_choice,
)
from sylvia_gm.application.character_profiles import CharacterProfile
from sylvia_gm.config.provider_settings import provider_settings
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionRecord
from sylvia_gm.provider._output_policy import output_allowance

from ..owner_process import OwnerProtocolError, _optional_text, _required_text
from . import DispatchContext

REPLY_DEADLINE_SECONDS = 600.0


def handle_character_chat(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    """List cards and chats, open a saved chat, or start a new one."""
    action = _required_text(params, "action")
    if action == "catalog":
        return chat_catalog(ctx.storage_root)
    if action == "open":
        return chat_projection(
            ctx.storage_root, load_chat(ctx.storage_root, _required_text(params, "chat_id")),
        )
    if action == "create":
        provider_id = _optional_text(params, "provider_id")
        model_id = _optional_text(params, "model_id")
        if provider_id is None or model_id is None:
            default = provider_settings(ctx.storage_root / "config.yaml").get("default_model")
            if not isinstance(default, Mapping):
                raise CommandRejected(
                    "Configure a default model before starting a character chat",
                    code="character_chat_model_unavailable",
                )
            provider_id = _required_text(default, "provider_id")
            model_id = _required_text(default, "model_id")
        provider_id, model_id = validate_model_choice(ctx.model_choices(), provider_id, model_id)
        chat = create_chat(
            ctx.storage_root,
            operation_id=_required_text(params, "operation_id"),
            artifact_id=_required_text(params, "artifact_id"),
            expected_content_sha256=_required_text(params, "expected_content_sha256"),
            provider_id=provider_id,
            model_id=model_id,
            user_name=_optional_text(params, "user_name"),
        )
        return chat_projection(ctx.storage_root, chat)
    raise OwnerProtocolError("Unknown character chat action")


def _request_options(provider: Any) -> dict[str, object]:
    """Use the same output allowance the Game Master uses for this model."""
    model = getattr(provider, "model_info", None)
    allowance = output_allowance(model)
    if allowance is None:
        return {}
    if getattr(model, "transport", "chat_completions") == "responses":
        return {"max_output_tokens": allowance}
    return {"max_tokens": allowance}


def handle_send_character_chat_message(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    """Send one message and wait for the character's reply on the shared provider loop."""

    def generate(
        chat: CharacterChat, _profile: CharacterProfile, messages: list[dict[str, str]],
    ) -> str:
        # The resolver selects providers by a session-shaped identity. This record is never
        # saved; it names the chat's own provider and model, outside any campaign.
        provider = ctx.provider_resolver(SessionRecord(
            session_id=chat.chat_id, campaign_id=None, role=WriterRole.BUILDER,
            provider_id=chat.provider_id, model_id=chat.model_id,
        ))
        future = asyncio.run_coroutine_threadsafe(
            collect_reply(provider, messages, request_options=_request_options(provider)),
            _PROVIDER_ASYNC_RUNTIME.loop(),
        )
        try:
            return future.result(timeout=REPLY_DEADLINE_SECONDS)
        except TimeoutError as error:
            future.cancel()
            raise CommandRejected(
                "The character took too long to reply. Try sending again",
                code="character_chat_timeout",
            ) from error

    chat = send_message(
        ctx.storage_root,
        chat_id=_required_text(params, "chat_id"),
        operation_id=_required_text(params, "operation_id"),
        text=params.get("text"),
        expected_message_count=params.get("expected_message_count"),
        generate=generate,
    )
    return chat_projection(ctx.storage_root, chat)
