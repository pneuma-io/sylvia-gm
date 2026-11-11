"""Providers command handlers moved from the owner protocol router."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sylvia_gm.application.authority import audit_export_save_error
from sylvia_gm.config.provider_settings import (
    detect_local_providers,
    discover_provider_models,
    open_provider_setup,
    provider_settings,
    remove_provider_settings,
    save_provider_settings,
    set_default_model_settings,
    validate_provider_model,
)
from sylvia_gm.domain.errors import CommandRejected

from ..owner_process import (
    _MAX_ADMIN_AUDIT_VIEW_BYTES,
    OwnerProtocolError,
    _json_value,
    _optional_bool,
    _optional_nonnegative_int,
    _optional_text,
    _required_nonnegative_int,
    _required_text,
    _switch_session_model,
)
from . import DispatchContext


def handle_model_choices(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return tuple(ctx.model_choices())


def handle_provider_settings(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return provider_settings(ctx.storage_root / "config.yaml")


def handle_open_provider_setup(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return open_provider_setup(params.get("preset_id"))


def handle_chatgpt_connection(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    from sylvia_gm.config.chatgpt_settings import chatgpt_settings_action
    from sylvia_gm.provider._codex_rpc import CodexConnectionError

    if params.get("action") == "add_model" and ctx.reload_provider_resolver is None:
        raise OwnerProtocolError("Provider settings reload is unavailable")
    try:
        result = chatgpt_settings_action(
            ctx.storage_root, params.get("action"), params.get("model"),
        )
    except CodexConnectionError as error:
        raise OwnerProtocolError(str(error)) from None
    if params.get("action") == "add_model" and ctx.reload_provider_resolver is not None:
        ctx.reload_provider_resolver()
    return result


def handle_claude_connection(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    from sylvia_gm.config.claude_settings import claude_settings_action
    from sylvia_gm.provider._exceptions import ProviderConnectionError

    if params.get("action") == "add_model" and ctx.reload_provider_resolver is None:
        raise OwnerProtocolError("Provider settings reload is unavailable")
    try:
        result = claude_settings_action(
            ctx.storage_root, params.get("action"), params.get("options"),
        )
    except ProviderConnectionError as error:
        raise OwnerProtocolError(str(error)) from None
    if params.get("action") == "add_model" and ctx.reload_provider_resolver is not None:
        ctx.reload_provider_resolver()
    return result


def handle_admin_full_audit_status(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.admin_full_audit_status()


def handle_admin_full_audit_record(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    index = _required_nonnegative_int(params, "index")
    record = ctx.application_owner.admin_full_audit_record(index)
    encoded = json.dumps(
        _json_value(record),
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_ADMIN_AUDIT_VIEW_BYTES:
        return {
            "index": index,
            "trace": None,
            "unavailable_reason": "record_too_large_for_live_view",
            "bytes": len(encoded),
        }
    return record


def handle_export_admin_full_audit(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    selected = Path(_required_text(params, "destination_path"))
    if not selected.is_absolute() or not selected.name:
        raise OwnerProtocolError("Audit export destination must be an absolute file path")
    try:
        parent = selected.parent.resolve(strict=True)
        destination = parent / selected.name
        regular_file = not destination.exists() or destination.is_file()
    except OSError as error:
        raise CommandRejected(audit_export_save_error(error)) from error
    if not regular_file:
        raise OwnerProtocolError(
            "Audit export destination is not a regular file. "
            "Choose a file in a writable folder."
        )
    return ctx.application_owner.export_admin_full_audit(destination)


def handle_set_admin_full_audit(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.set_admin_full_audit(_optional_bool(params, "enabled"))


def handle_detect_local_providers(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return detect_local_providers(ctx.storage_root / "config.yaml")


def handle_discover_provider_models(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return discover_provider_models(
        ctx.storage_root / "config.yaml",
        provider_id=params.get("provider_id"),
        preset_id=params.get("preset_id"),
        base_url=params.get("base_url"),
        credential=params.get("credential"),
        headers=params.get("headers"),
        query=params.get("query"),
        offset=params.get("offset", 0),
        limit=params.get("limit", 100),
    )


def handle_validate_provider_model(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return validate_provider_model(
        ctx.storage_root / "config.yaml",
        provider_id=params.get("provider_id"),
        preset_id=params.get("preset_id"),
        base_url=params.get("base_url"),
        credential=params.get("credential"),
        model_id=params.get("model_id"),
        transport=params.get("transport"),
        headers=params.get("headers"),
    )


def handle_save_provider_settings(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    if ctx.reload_provider_resolver is None:
        raise OwnerProtocolError("Provider settings reload is unavailable")
    saved_provider = save_provider_settings(
        ctx.storage_root / "config.yaml",
        provider_id=params.get("provider_id"),
        preset_id=params.get("preset_id"),
        display_name=params.get("display_name"),
        base_url=params.get("base_url"),
        credential=params.get("credential"),
        model_id=params.get("model_id"),
        model_name=params.get("model_name"),
        context_window=params.get("context_window"),
        max_output_tokens=params.get("max_output_tokens"),
        declared_max_output_tokens=params.get("declared_max_output_tokens"),
        builder_output_tokens=params.get("builder_output_tokens"),
        declared_context_window=params.get("declared_context_window"),
        declared_context_source=params.get("declared_context_source"),
        runtime_context_window=params.get("runtime_context_window"),
        runtime_context_source=params.get("runtime_context_source"),
        configured_context_window=params.get("configured_context_window"),
        context_observed_at=params.get("context_observed_at"),
        declared_context_observed_at=params.get("declared_context_observed_at"),
        runtime_context_observed_at=params.get("runtime_context_observed_at"),
        configured_context_observed_at=params.get("configured_context_observed_at"),
        transport=params.get("transport"),
        headers=params.get("headers"),
        supports_streaming=params.get("supports_streaming"),
        supports_tools=params.get("supports_tools"),
    )
    ctx.reload_provider_resolver()
    return saved_provider


def handle_remove_provider_settings(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    if ctx.reload_provider_resolver is None:
        raise OwnerProtocolError("Provider settings reload is unavailable")
    remove_provider_settings(ctx.storage_root / "config.yaml", params.get("provider_id"))
    ctx.reload_provider_resolver()
    return {"status": "removed"}


def handle_set_default_model(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return set_default_model_settings(
        ctx.storage_root / "config.yaml",
        provider_id=params.get("provider_id"),
        model_id=params.get("model_id"),
    )


def handle_switch_session_model(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    session_id = _required_text(params, "session_id")
    return _switch_session_model(
        application_owner=ctx.application_owner,
        session_loop=ctx.session_loop,
        config_path=ctx.storage_root / "config.yaml",
        session_id=session_id,
        provider_id=params.get("provider_id"),
        model_id=params.get("model_id"),
        mode=_optional_text(params, "mode") or "current",
        choices=ctx.model_choices(),
    )


def handle_inspect_provider_thinking(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return {"text": ctx.application_owner.inspect_provider_thinking(
        _required_text(params, "session_id"), _required_text(params, "attempt_id"),
        _required_text(params, "response_id"),
    )}


def handle_interrupt_provider_stream(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    ctx.bus.interrupt_provider_stream(
        _required_text(params, "campaign_id"),
        _required_text(params, "attempt_id"),
    )
    return {"status": "interrupted"}


def handle_disconnect_provider_stream(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return {
        "disconnect_deadline": ctx.bus.disconnect_provider_stream(
            _required_text(params, "campaign_id"),
            _required_text(params, "attempt_id"),
            expected_generation=_optional_nonnegative_int(params, "attachment_generation"),
        )
    }


def handle_provider_stream_heartbeat(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    attachment_deadline, attachment_generation = ctx.bus.heartbeat_provider_stream(
        _required_text(params, "campaign_id"),
        _required_text(params, "attempt_id"),
        expected_generation=_optional_nonnegative_int(params, "attachment_generation"),
    )
    return {
        "attachment_deadline": attachment_deadline,
        "attachment_generation": attachment_generation,
    }


def handle_provider_stream_status(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    session_id = _required_text(params, "campaign_id")
    attempt_id = _required_text(params, "attempt_id")
    activity = ctx.session_loop.builder_activity(session_id, attempt_id=attempt_id)
    if activity is not None and activity["status"] != "active":
        return activity
    status = ctx.bus.provider_stream_status(session_id, attempt_id)
    # A live admitted run can precede lease registration or outlive its release.
    # Only saved terminal evidence can complete that run.
    if activity is not None and status == "completed":
        status = "active"
    return {
        "status": status,
    }


def handle_reconnect_provider_stream(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return {
        "attachment_generation": ctx.bus.reconnect_provider_stream(
            _required_text(params, "campaign_id"),
            _required_text(params, "attempt_id"),
            expected_generation=_optional_nonnegative_int(params, "attachment_generation"),
        )
    }


def handle_model_guidance(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    guidance: Any = ctx.bus.model_guidance(
        campaign_id=_required_text(params, "campaign_id"),
        model_id=_required_text(params, "model_id"),
        override_accepted=_optional_bool(params, "override_accepted"),
    )
    return {
        **_json_value(guidance),
        "warning_required": guidance.warning_required,
        "may_continue": guidance.may_continue,
    }
