"""Actions command handlers moved from the owner protocol router."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..owner_process import (
    ActionCommand,
    OwnerProtocolError,
    ResolveMechanicsChoiceCommand,
    WriterRole,
    _optional_nonnegative_int,
    _optional_payload,
    _optional_text,
    _required_nonnegative_int,
    _required_text,
)
from . import DispatchContext


def handle_campaign_panels(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.campaign_panels(
        _required_text(params, "campaign_id"),
        after_revision=_optional_nonnegative_int(params, "after_revision"),
    )


def handle_action_sequence(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.action_sequence(
        session_id=_required_text(params, "session_id"),
        sequence_id=_optional_text(params, "sequence_id"),
    )


def handle_compose_action_sequence(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    arguments = {
        "campaign_id": _required_text(params, "campaign_id"),
        "session_id": _required_text(params, "session_id"),
        "sequence_id": _required_text(params, "sequence_id"),
        "expected_version": _required_nonnegative_int(params, "expected_version"),
    }
    if ctx.method == "compose_action_sequence":
        return ctx.application_owner.compose_action_sequence(
            **arguments,
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
            steps=params.get("steps"),
        )
    return ctx.application_owner.execute_action_sequence(
        **arguments, confirmation=_optional_text(params, "confirmation"),
    )


def handle_player_action_availability(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.player_action_availability(
        campaign_id=_required_text(params, "campaign_id"),
        expected_revision=_required_nonnegative_int(params, "expected_revision"),
        requests=params.get("requests"),
    )


def handle_execute_player_action(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    source = params.get("source")
    selection = params.get("selection")
    if (not isinstance(source, dict)
            or (selection is not None and not isinstance(selection, dict))):
        raise OwnerProtocolError("Player control source and selection must be objects")
    return ctx.application_owner.player_action(
        campaign_id=_required_text(params, "campaign_id"),
        session_id=_required_text(params, "session_id"),
        operation_id=_required_text(params, "operation_id"),
        expected_revision=_required_nonnegative_int(params, "expected_revision"),
        source=source, selection=selection,
        confirmation=_optional_text(params, "confirmation"),
        preview_only=ctx.method == "preview_player_action",
    )


def handle_execute_panel_action(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    action_arguments = {
        "campaign_id": _required_text(params, "campaign_id"),
        "surface_id": _required_text(params, "surface_id"),
        "panel_action_id": _required_text(params, "panel_action_id"),
        "operation_id": _required_text(params, "operation_id"),
        "expected_revision": _required_nonnegative_int(params, "expected_revision"),
        "selection": _optional_payload(params, "selection"),
        "session_id": _optional_text(params, "session_id"),
    }
    if ctx.method == "panel_action_receipt":
        return ctx.application_owner.panel_action_receipt(**action_arguments)
    if ctx.method == "preview_panel_action":
        return ctx.application_owner.preview_panel_action(**action_arguments)
    return ctx.application_owner.execute_panel_action(
        **action_arguments, confirmation=_optional_text(params, "confirmation"),
    )


def handle_execute_action(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    parameters = params.get("parameters", {})
    if not isinstance(parameters, dict):
        raise OwnerProtocolError("parameters must be an object")
    return ctx.bus.execute_action(
        ActionCommand(
            campaign_id=_required_text(params, "campaign_id"),
            action_id=_required_text(params, "action_id"),
            idempotency_key=_required_text(params, "idempotency_key"),
            owner_id=ctx.owner_id,
            writer=WriterRole(_required_text(params, "writer")),
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
            turn_id=_required_text(params, "turn_id"),
            package_id=_required_text(params, "package_id"),
            tool_name=_required_text(params, "tool_name"),
            parameters=parameters,
            initiating_session_id=(
                None
                if params.get("session_id") is None
                else _required_text(params, "session_id")
            ),
        )
    )


def handle_resolve_mechanics_choice(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.resolve_mechanics_choice(
        ResolveMechanicsChoiceCommand(
            campaign_id=_required_text(params, "campaign_id"),
            action_id=_required_text(params, "action_id"),
            resolution_id=_required_text(params, "resolution_id"),
            owner_id=ctx.owner_id,
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
            choice_id=_required_text(params, "choice_id"),
            option_id=_required_text(params, "option_id"),
            choice_sequence=_required_nonnegative_int(
            {"choice_sequence": params.get("choice_sequence", 0)}, "choice_sequence"),
            session_id=(
                None
                if params.get("session_id") is None
                else _required_text(params, "session_id")
            ),
        )
    )


def handle_open_turn(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    ctx.bus.open_turn(
        _required_text(params, "campaign_id"),
        _required_text(params, "turn_id"),
    )
    return {"status": "open"}


def handle_complete_turn(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    ctx.bus.complete_turn(
        _required_text(params, "campaign_id"),
        _required_text(params, "turn_id"),
    )
    return {"status": "completed"}
