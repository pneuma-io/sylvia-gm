"""Builder command handlers moved from the owner protocol router."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import DispatchContext
from ..owner_process import (
    CommandRejected,
    MAX_SOURCE_FILES,
    OwnerProtocolError,
    Path,
    SafePayload,
    SessionMessageKind,
    _builder_workspace,
    _optional_text,
    _required_artifact_id,
    _required_text,
    cast,
    hashlib,
    ingest_builder_sources,
    provider_settings,
    rollback_builder_source_batch,
    start_import_repair_chat,
)


def handle_builder_chat_index(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.builder_chat_index()


def handle_start_import_repair(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    provider_id = _optional_text(params, "provider_id")
    model_id = _optional_text(params, "model_id")
    if provider_id is None or model_id is None:
        default = provider_settings(ctx.storage_root / "config.yaml").get("default_model")
        if not isinstance(default, Mapping):
            raise CommandRejected("Configure a default model before creating a chat")
        provider_id = _required_text(default, "provider_id")
        model_id = _required_text(default, "model_id")
    return start_import_repair_chat(
        ctx.application_owner, ctx.storage_root,
        repair_id=_required_text(params, "repair_id"),
        operation_id=_required_text(params, "operation_id"),
        provider_id=provider_id, model_id=model_id,
    )


def handle_create_builder_chat(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    operation_id = _required_text(params, "operation_id")
    session_id = (
        "session-builder-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    )
    provider_id = _optional_text(params, "provider_id")
    model_id = _optional_text(params, "model_id")
    if provider_id is None or model_id is None:
        default = provider_settings(ctx.storage_root / "config.yaml").get("default_model")
        if not isinstance(default, Mapping):
            raise CommandRejected("Configure a default model before creating a chat")
        provider_id = _required_text(default, "provider_id")
        model_id = _required_text(default, "model_id")
    return ctx.application_owner.create_builder_chat(
        session_id=session_id,
        provider_id=provider_id,
        model_id=model_id,
        display_name=_optional_text(params, "display_name") or "New chat",
    )


def handle_rename_builder_chat(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.rename_builder_chat(
        _required_text(params, "session_id"),
        _required_text(params, "display_name"),
    )


def handle_delete_builder_chat(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.delete_builder_chat(
        _required_text(params, "session_id"),
        confirmation=_required_text(params, "confirmation"),
    )


def handle_archive_builder_chat(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.session_loop.set_builder_chat_archived(
        _required_text(params, "session_id"), archived=ctx.method == "archive_builder_chat"
    )


def handle_ingest_builder_sources(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    session_id = _required_text(params, "session_id")
    ingestion_id = _required_artifact_id(params, "ingestion_id")
    raw_paths = params.get("source_paths")
    if (
        not isinstance(raw_paths, list)
        or not 1 <= len(raw_paths) <= MAX_SOURCE_FILES
        or any(not isinstance(item, str) or not item for item in raw_paths)
    ):
        raise OwnerProtocolError("source_paths must contain one to eight selected paths")
    with ctx.application_owner.builder_session_work(session_id) as builder_context:
        workspace = _builder_workspace(ctx.storage_root, builder_context.session)
        batch = ingest_builder_sources(
            workspace,
            tuple(Path(item) for item in raw_paths),
            batch_id=ingestion_id,
        )
        safe_sources = [source.safe_projection() for source in batch.sources]
        try:
            message = ctx.application_owner.append_agent_message(
                session_id=session_id,
                message_id=(
                    "builder-sources-"
                    + hashlib.sha256(ingestion_id.encode("utf-8")).hexdigest()[:24]
                ),
                kind=SessionMessageKind.EVIDENCE,
                content=cast(
                    SafePayload,
                    {
                        "tool_name": "attached_local_sources",
                        "result": {
                            "status": "ingested",
                            "source_count": len(safe_sources),
                            "sources": safe_sources,
                            "authoritative": False,
                        },
                    },
                ),
            )
        except Exception:
            rollback_builder_source_batch(batch)
            raise
    return {
        "status": "ingested",
        "source_count": len(safe_sources),
        "sources": safe_sources,
        "message_sequence": message.sequence,
    }
