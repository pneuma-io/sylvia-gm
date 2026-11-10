"""Campaigns command handlers moved from the owner protocol router."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from sylvia_gm.application.builder_references import (
    candidate_for_references,
    capture_launch_references,
    launch_references,
    with_character_examples,
)
from sylvia_gm.application.bundled_campaign_plans import selected_campaign_plan
from sylvia_gm.application.bundled_packages import bundled_rift_authority
from sylvia_gm.application.bundled_starter_characters import available_reusable_participants
from sylvia_gm.application.campaign_library_selection import (
    materialize_library_party,
    plan_with_interview_intent,
    resolve_campaign_library_selection,
)
from sylvia_gm.application.campaign_plan_sources import validate_retained_campaign_plan
from sylvia_gm.application.character_profiles import load_character_profile, profile_from_snapshot
from sylvia_gm.application.plan_bundle import export_plan_bundle
from sylvia_gm.application.plan_snapshot import retained_plan_snapshot, validate_plan_snapshot
from sylvia_gm.application.ready_campaigns import (
    customize_ready_campaign,
    ready_campaign_catalog,
    start_ready_campaign,
)
from sylvia_gm.application.reusable_participants import delete_reusable_participant
from sylvia_gm.application.reusable_plans import (
    delete_reusable_campaign_plan,
    load_reusable_campaign_plan,
    save_reusable_campaign_plan,
    save_reusable_rift_plan,
)
from sylvia_gm.campaign.rift import rift_public_template
from sylvia_gm.domain.authority import (
    PackageLock,
    PackageRole,
    Participant,
    SafePayload,
    StateValue,
)
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure.builder_source_ingestion import (
    finalize_builder_source_purge,
    finalize_existing_builder_source_purge,
    restore_builder_sources_after_failed_purge,
    stage_builder_sources_for_purge,
)

from ..owner_process import (
    _MAX_USER_TEXT_CHARS,
    OwnerProtocolError,
    _campaign_creation_catalog,
    _campaign_plan_payload,
    _campaign_plan_text,
    _candidate_package_lock,
    _guided_start_selection_input,
    _json_value,
    _optional_bool,
    _optional_nonnegative_int,
    _optional_text,
    _preflight_guided_start,
    _required_artifact_id,
    _required_nonnegative_int,
    _required_text,
    _resolve_rift_library_selection,
    _reusable_participant_catalog,
    _rift_reusable_participants,
    _seed_campaign_plan,
    _start_rift_campaign,
)
from . import DispatchContext


def handle_handshake_status(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.handshake_status()


def handle_campaign_index(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.campaign_index()


def handle_campaign_creation_catalog(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return _campaign_creation_catalog(
        ctx.storage_root, ctx.application_owner,
        include_participants=_optional_bool(params, "include_participants", default=True),
    )


def handle_ready_campaign_catalog(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    try:
        return ready_campaign_catalog()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CommandRejected("Prepared campaigns are unavailable or incompatible",
                              code="ready_campaign_invalid") from error


def handle_customize_ready_campaign(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    ready_arguments = {
        "operation_id": _required_text(params, "operation_id"),
        "template_id": _required_text(params, "template_id"),
        "expected_content_sha256": _required_text(params, "expected_content_sha256"),
    }
    if ctx.method == "customize_ready_campaign":
        return customize_ready_campaign(ctx.application_owner, ctx.storage_root, **ready_arguments)
    return start_ready_campaign(
        ctx.application_owner, ctx.storage_root, **ready_arguments,
        display_name=_required_text(params, "display_name"),
        provider_id=_required_text(params, "provider_id"),
        model_id=_required_text(params, "model_id"),
        model_guidance_override=_optional_bool(params, "model_guidance_override"),
    )


def handle_delete_reusable_library_entry(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    kind = _required_text(params, "kind")
    artifact_id = _required_text(params, "artifact_id")
    expected_content_sha256 = _required_text(params, "expected_content_sha256")
    confirmation = _required_text(params, "confirmation")
    try:
        if kind in {"player_character", "companion"}:
            deleted = delete_reusable_participant(
                ctx.storage_root,
                artifact_id,
                expected_content_sha256=expected_content_sha256,
                confirmation=confirmation,
            )
        elif kind == "campaign_plan":
            deleted = delete_reusable_campaign_plan(
                ctx.storage_root,
                artifact_id,
                expected_content_sha256=expected_content_sha256,
                confirmation=confirmation,
            )
        else:
            raise CommandRejected(
                "Only saved local Characters, Companions, and Campaign Plans "
                "can be deleted here",
                code="reusable_library_kind_unsupported",
            )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise CommandRejected(
            str(error) or "Saved library entry could not be deleted",
            code="reusable_library_delete_rejected",
        ) from error
    return {"artifact_id": artifact_id, "deleted": deleted}


def handle_snapshot(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.snapshot(_required_text(params, "campaign_id"))


def handle_display_snapshot(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return (
        ctx.application_owner.desktop_snapshot(
        _required_text(params, "campaign_id"), display_only=True)
    )


def handle_player_projection(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.player_projection(_required_text(params, "campaign_id"))


def handle_player_projection_page(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.player_projection_page(
        _required_text(params, "campaign_id"),
        after_sequence=_optional_nonnegative_int(params, "after_sequence"),
    )


def handle_append_campaign_sequel(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    arguments = {
        "campaign_id": _required_text(params, "campaign_id"),
        "session_id": _required_text(params, "session_id"),
    }
    if ctx.method == "campaign_sequel_context":
        return ctx.application_owner.campaign_sequel_context(**arguments)
    arguments["source_id"] = _required_text(params, "source_id")
    if ctx.method == "preview_campaign_sequel":
        return ctx.application_owner.preview_campaign_sequel(**arguments)
    return ctx.application_owner.append_campaign_sequel(
        **arguments, operation_id=_required_text(params, "operation_id"),
        expected_revision=_required_nonnegative_int(params, "expected_revision"),
        preview_sha256=_required_text(params, "preview_sha256"), review=params.get("review"),
    )


def handle_campaign_rules_update_options(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.campaign_rules_update_options(
        _required_text(params, "campaign_id")
    )


def handle_preview_campaign_rules_update(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.preview_campaign_rules_update(
        _required_text(params, "campaign_id"), _required_text(params, "candidate_id"),
    )


def handle_adopt_campaign_rules_update(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.adopt_campaign_rules_update(
        campaign_id=_required_text(params, "campaign_id"),
        candidate_id=_required_text(params, "candidate_id"),
        activation_id=_required_text(params, "activation_id"),
        expected_revision=_required_nonnegative_int(params, "expected_revision"),
        expected_lock_id=_required_text(params, "expected_lock_id"),
        confirmation=_required_text(params, "confirmation"),
    )


def handle_rift_template(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    try:
        participants = _rift_reusable_participants(ctx.storage_root)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise CommandRejected(
            "Reusable participant library contains an invalid artifact",
            code="reusable_participant_invalid",
        ) from error
    return rift_public_template(reusable_participants=participants)


def handle_campaign_party_options(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    ruleset_selection = _required_text(params, "ruleset_selection")
    setting_id = _required_text(params, "setting_id")
    plan_id = _required_text(params, "plan_id")
    catalog_plan = next((
        item for item in _campaign_creation_catalog(
            ctx.storage_root, ctx.application_owner, include_participants=False,
        )
        if item.get("kind") == "campaign_plan" and item.get("id") == plan_id
    ), None)
    if catalog_plan is not None and catalog_plan.get("setup_id") == "the-rift-awakening":
        candidate, sources, _plan, template = _resolve_rift_library_selection(
            ctx.application_owner, ctx.storage_root, ruleset_selection=ruleset_selection,
            setting_id=setting_id, plan_id=plan_id,
        )
        primary = next(
            entry for entry in candidate.package_lock.entries
            if entry.role is PackageRole.PRIMARY
        )
        declaration = next(
            item for item in ctx.application_owner.exact_participant_declarations(
                primary.package_id, primary.version, primary.sha256,
            ) if item.kind == "player_character"
        )
        return rift_public_template(
            reusable_participants=available_reusable_participants(
                ctx.storage_root, package=primary, declaration=declaration,
                campaign_setting=sources.setting,
                legacy_campaign_setting=bundled_rift_authority().setting,
                setting_character_schema=sources.setting_character_schema,
            ),
            template=template,
        )
    return resolve_campaign_library_selection(
        ctx.application_owner, ctx.storage_root, candidate_id=ruleset_selection,
        setting_id=setting_id, plan_id=plan_id,
    ).party_options()


def handle_retryable_interruption(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.retryable_interruption(
        _required_text(params, "campaign_id"),
        failed_attempt_id=_required_text(params, "failed_attempt_id"),
    )


def handle_start_rift_campaign(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    raw_companion_ids = params.get("companion_ids")
    if (
        not isinstance(raw_companion_ids, list)
        or len(raw_companion_ids) > 5
        or any(not isinstance(item, str) or not item for item in raw_companion_ids)
    ):
        raise OwnerProtocolError("companion_ids must contain zero to five identities")
    customization_notes = params.get("customization_notes", "")
    if not isinstance(customization_notes, str):
        raise OwnerProtocolError("customization_notes must be a string")
    construction_intent = params.get("construction_intent", {})
    if not isinstance(construction_intent, dict):
        raise OwnerProtocolError("construction_intent must be an object")
    return _start_rift_campaign(
        ctx.bus,
        ctx.application_owner,
        ctx.storage_root,
        operation_id=_required_text(params, "operation_id"),
        campaign_id=_required_text(params, "campaign_id"),
        builder_session_id=_required_text(params, "builder_session_id"),
        ruleset_selection=_required_text(params, "ruleset_selection"),
        setting_id=_required_text(params, "setting_id"),
        plan_id=_required_text(params, "plan_id"),
        display_name=_required_text(params, "display_name"),
        lane=_required_text(params, "lane"),
        archetype_id=_required_text(params, "archetype_id"),
        companion_ids=tuple(raw_companion_ids),
        customization_notes=customization_notes,
        provider_id=_required_text(params, "provider_id"),
        model_id=_required_text(params, "model_id"),
        construction_intent=cast(SafePayload, construction_intent),
    )


def handle_start_guided_campaign(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    operation_id = _required_text(params, "operation_id")
    campaign_id = _required_text(params, "campaign_id")
    builder_session_id = _required_text(params, "builder_session_id")
    candidate_id = params.get("candidate_id")
    selection = None
    if params.get("plan_id") is not None:
        selection = resolve_campaign_library_selection(
            ctx.application_owner, ctx.storage_root,
            candidate_id=_required_text(params, "candidate_id"),
            setting_id=_required_text(params, "setting_id"),
            plan_id=_required_text(params, "plan_id"),
        )
        candidate_id = selection.candidate.candidate_id
    if candidate_id is None:
        package_lock = PackageLock(entries=())
    elif isinstance(candidate_id, str) and candidate_id:
        package_lock = _candidate_package_lock(ctx.bus, candidate_id)
    else:
        raise OwnerProtocolError("candidate_id must be a nonempty string or null")
    premise = params.get("premise", "")
    if not isinstance(premise, str) or len(premise) > _MAX_USER_TEXT_CHARS:
        raise OwnerProtocolError("premise must be a bounded string")
    canonical_input: dict[str, Any] = {"premise": premise.strip()} if premise.strip() else {}
    participants: tuple[Participant, ...] = ()
    initial_state: tuple[StateValue, ...] = ()
    selected_plan = None
    plan_id = params.get("plan_id")
    if plan_id is not None:
        if not isinstance(candidate_id, str):
            raise OwnerProtocolError("A saved Plan requires an installed Ruleset")
        raw_companions = params.get("companion_ids", [])
        if (
            not isinstance(raw_companions, list)
            or len(raw_companions) > 64
            or any(not isinstance(item, str) or not item for item in raw_companions)
        ):
            raise OwnerProtocolError("companion_ids must contain bounded character identities")
        intent = params.get("construction_intent") or {}
        if not isinstance(intent, dict):
            raise OwnerProtocolError("construction_intent must be an object")
        assert selection is not None
        participants, initial_state, selected_party = materialize_library_party(
            ctx.application_owner, selection, operation_id=operation_id,
            player_character_id=_required_text(params, "player_character_id"),
            companion_ids=tuple(raw_companions),
        )
        selected_plan = plan_with_interview_intent(selection, cast(SafePayload, intent))
        canonical_input.update(
            {
                "plan_id": selection.plan.artifact_id,
                "plan_content_sha256": selection.plan.content_sha256,
                "setting_id": selection.sources.setting.package_id,
                "player_character": {"archetype_id": params["player_character_id"]},
                "companion_ids": raw_companions,
                "starting_party_policy": selected_plan.payload["starting_party"],
                "selected_party": selected_party,
                "construction_intent": intent,
            }
        )
    elif params.get("player_character_id") or params.get("companion_ids"):
        raise CommandRejected("Selected characters require a concrete Campaign Plan")
    display_name = _required_text(params, "display_name")
    try:
        existing_basis = ctx.application_owner.campaign_construction_basis(campaign_id)
    except CommandRejected as error:
        if str(error) not in {
            "Confirmed campaign construction is unavailable",
            "Campaign construction has not begun",
        }:
            raise
    else:
        if (
            existing_basis.builder_session_id != builder_session_id
            or existing_basis.display_name != display_name
            or existing_basis.package_candidate_id != candidate_id
            or _guided_start_selection_input(existing_basis.canonical_input)
            != _guided_start_selection_input(canonical_input)
        ):
            raise CommandRejected(
                "The interrupted guided Start basis differs from the current selections"
            )
        return {
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "resulting_revision": existing_basis.begin_revision,
        }
    ctx.application_owner.append_agent_message(
        session_id=builder_session_id,
        message_id=(
            "guided-ready-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
        ),
        kind=SessionMessageKind.USER,
        content={
            "text": "Ready to Begin",
            "interview_step": "ready",
            "interview_value": {"action": "ready_to_begin"},
        },
    )
    confirmation = ctx.bus.bind_guided_campaign_basis(
        operation_id=f"{operation_id}-bind",
        campaign_id=campaign_id,
        display_name=display_name,
        package_lock=package_lock,
        turn_id=(
            "turn-construction-"
            f"{hashlib.sha256(operation_id.encode('utf-8')).hexdigest()[:24]}"
        ),
        builder_session_id=builder_session_id,
        package_candidate_id=candidate_id,
        participants=participants,
        initial_state=initial_state,
    )
    if selected_plan is not None:
        _seed_campaign_plan(
            ctx.storage_root, campaign_id,
            selected_plan.readable_markdown, selected_plan.payload,
        )
    elif candidate_id is None:
        _seed_campaign_plan(
            ctx.storage_root, campaign_id,
            f"# {display_name}\n\n"
            "This is an open-ended narrative sandbox with no Ruleset or game mechanics. "
            "Use the player's setup below as the starting brief. When it leaves the "
            "character or opening situation unspecified, ask the player before establishing "
            "those details. Preserve player choices and boundaries.\n\n"
            f"## Player setup\n\n{premise.strip() or 'No starting premise supplied.'}\n",
        )
    begun = ctx.bus.begin_campaign_construction(
        operation_id=f"{operation_id}-begin",
        campaign_id=campaign_id,
        lane="customize" if premise.strip() else "quick_start",
        expected_revision=confirmation.resulting_revision,
        canonical_input=canonical_input,
    )
    return {
        "campaign_id": campaign_id,
        "builder_session_id": builder_session_id,
        "resulting_revision": begun.resulting_revision,
    }


def handle_create_guided_campaign_draft(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    if "builder_session_id" in params:
        raise OwnerProtocolError("builder_session_id is owner-assigned")
    operation_id = _required_text(params, "operation_id")
    builder_session_id = (
        "session-guided-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    )
    draft = ctx.bus.create_guided_campaign_draft(
        operation_id=operation_id,
        display_name=_required_text(params, "display_name"),
        builder_session_id=builder_session_id,
        builder_provider_id=_required_text(params, "provider_id"),
        builder_model_id=_required_text(params, "model_id"),
    )
    return {
        "campaign_id": draft.campaign_id,
        "builder_session_id": draft.builder_session_id,
        "current_step": draft.current_step,
        "construction_revision": draft.construction_revision,
        "resulting_revision": draft.resulting_revision,
        "answer_message_id": draft.answer_message_id,
    }


def handle_navigate_guided_campaign_draft(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    draft = ctx.bus.navigate_guided_campaign_draft(
        operation_id=_required_text(params, "operation_id"),
        campaign_id=_required_text(params, "campaign_id"),
        builder_session_id=_required_text(params, "builder_session_id"),
        expected_construction_revision=_required_nonnegative_int(
            params, "expected_construction_revision"
        ),
        target_step=_required_text(params, "target_step"),
    )
    return {
        "campaign_id": draft.campaign_id,
        "builder_session_id": draft.builder_session_id,
        "current_step": draft.current_step,
        "construction_revision": draft.construction_revision,
        "resulting_revision": draft.resulting_revision,
    }


def handle_advance_guided_campaign_draft(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    answer = params.get("answer")
    if not isinstance(answer, dict):
        raise OwnerProtocolError("answer must be an object")
    draft = ctx.bus.advance_guided_campaign_draft(
        operation_id=_required_text(params, "operation_id"),
        campaign_id=_required_text(params, "campaign_id"),
        builder_session_id=_required_text(params, "builder_session_id"),
        expected_construction_revision=_required_nonnegative_int(
            params, "expected_construction_revision"
        ),
        current_step=_required_text(params, "current_step"),
        visible_answer=_required_text(params, "visible_answer"),
        answer=answer,
    )
    return {
        "campaign_id": draft.campaign_id,
        "builder_session_id": draft.builder_session_id,
        "current_step": draft.current_step,
        "construction_revision": draft.construction_revision,
        "resulting_revision": draft.resulting_revision,
        "answer_message_id": draft.answer_message_id,
    }


def handle_guided_campaign_contribution(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.application_owner.guided_campaign_contribution(
        builder_session_id=_required_text(params, "builder_session_id"),
        contribution_step=_required_text(params, "contribution_step"),
    )


def handle_save_reusable_campaign_plan(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    operation_id = _required_text(params, "operation_id")
    campaign_id = _required_text(params, "campaign_id")
    builder_session_id = _required_text(params, "builder_session_id")
    expected_revision = _required_nonnegative_int(params, "expected_construction_revision")
    context = ctx.application_owner.session_context(builder_session_id)
    if context.session.campaign_id != campaign_id:
        raise CommandRejected("Reusable Campaign Plan source session is unavailable")
    states = tuple(
        message
        for message in context.messages
        if message.kind is SessionMessageKind.EVIDENCE
        and message.content.get("kind") == "campaign_interview_state"
    )
    state = states[-1].content if states else None
    subflows = None if state is None else state.get("subflows")
    derived = isinstance(subflows, Sequence) and any(
        isinstance(subflow, Mapping)
        and subflow.get("used") is True
        and subflow.get("purpose") in {"plan", "campaign_customization"}
        for subflow in subflows
    )
    if (
        state is None
        or state.get("current_step") != "review"
        or state.get("construction_revision") != expected_revision
        or state.get("active_subflow") is not None
        or not derived
    ):
        raise CommandRejected(
            "Only a current reviewed campaign-local Derived Plan can be saved for reuse",
            code="reusable_campaign_plan_source_unavailable",
        )
    payload = _campaign_plan_payload(ctx.storage_root, campaign_id)
    if payload is None:
        raise CommandRejected("Derived structured Campaign Plan is unavailable")
    markdown = _campaign_plan_text(ctx.storage_root, campaign_id)
    try:
        reusable = save_reusable_rift_plan(
            ctx.storage_root,
            operation_id=operation_id,
            title=_required_text(params, "title"),
            source_payload=payload,
            source_markdown=markdown,
            package_snapshot=retained_plan_snapshot(ctx.application_owner, payload),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise CommandRejected(
            "Derived Campaign Plan could not be generalized safely",
            code="reusable_campaign_plan_invalid",
        ) from error
    return reusable.catalog_payload()


def handle_start_guided_creation_subflow(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    operation_id = _required_text(params, "operation_id")
    purpose = _required_text(params, "purpose")
    profile_id = _optional_text(params, "character_profile_id")
    profile_hash = _optional_text(params, "character_profile_sha256")
    character_id = _optional_text(params, "character_artifact_id")
    character_hash = _optional_text(params, "character_artifact_sha256")
    if (character_id is None) != (character_hash is None) or (
        character_id is not None
        and (purpose not in {"player_character", "companion"} or profile_id is not None)
    ):
        raise CommandRejected(
            "Choose one exact Character File to edit a copy", code="character_copy_invalid",
        )
    if (profile_id is None) != (profile_hash is None) or (
        profile_id is not None and purpose != "companion"
    ):
        raise CommandRejected(
            "Choose one exact general profile for a Companion adaptation",
            code="character_profile_invalid",
        )
    child_session_id = (
        "session-subflow-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    )
    launch = (
        ctx.application_owner.start_interview_builder_chat
        if purpose in {"ruleset", "setting", "plan", "player_character", "companion"}
        else ctx.bus.start_guided_creation_subflow
    )
    reference_options = {}
    if purpose in {"ruleset", "setting", "plan", "player_character", "companion"}:
        existing = next(
            (
                chat
                for chat in ctx.application_owner.builder_chat_index()
                if chat.session.session_id == child_session_id
            ),
            None,
        )
        reference_options["references"] = (
            launch_references(ctx.application_owner.session_context(child_session_id))
            if existing is not None
            else capture_launch_references(
                ctx.application_owner,
                ctx.storage_root,
                ctx.application_owner.session_context(_required_text(params, "parent_session_id")),
                _campaign_creation_catalog(ctx.storage_root, ctx.application_owner),
            )
        )
        retained: Mapping[str, Any] = cast(dict[str, Any], reference_options["references"] or {})
        if existing is None and purpose in {"player_character", "companion"}:
            retained = with_character_examples(
                ctx.application_owner, ctx.storage_root, retained, purpose=purpose,
            )
            reference_options["references"] = retained
        pinned = retained.get("character_profile")
        if existing is not None:
            actual = profile_from_snapshot(pinned) if pinned is not None else None
            if (actual is None and profile_id is not None) or (
                actual is not None
                and (actual.artifact_id != profile_id or actual.content_sha256 != profile_hash)
            ):
                raise CommandRejected(
                    "This launch already selected a different source profile",
                    code="stale_character_profile",
                )
        elif profile_id is not None:
            profile = load_character_profile(ctx.storage_root, profile_id, profile_hash)
            selected_candidate = candidate_for_references(ctx.application_owner, retained)
            profile_primary = (
                next(
                    (
                        item
                        for item in selected_candidate.package_lock.entries
                        if item.role is PackageRole.PRIMARY
                    ),
                    None,
                )
                if selected_candidate
                else None
            )
            declarations = (
                ctx.application_owner.exact_participant_declarations(
                    profile_primary.package_id,
                    profile_primary.version,
                    profile_primary.sha256,
                )
                if profile_primary is not None
                else ()
            )
            profile_plan = retained.get("plan")
            profile_policy = (
                profile_plan.get("payload", {}).get("starting_party", {})
                if isinstance(profile_plan, Mapping)
                else {}
            )
            maximum = profile_policy.get("custom_companions", {}).get("maximum", 0)
            profile_parent = ctx.application_owner.session_context(
                _required_text(params, "parent_session_id")
            )
            profile_parent_state = cast(
                dict[str, Any],
                next(
                    (
                        message.content
                        for message in reversed(profile_parent.messages)
                        if message.content.get("kind") == "campaign_interview_state"
                    ),
                    {},
                ),
            )
            answer_id = profile_parent_state.get("answers", {}).get("participants")
            profile_selection = cast(
                dict[str, Any],
                next(
                    (
                        message.content.get("interview_value", {})
                        for message in profile_parent.messages
                        if message.message_id == answer_id
                    ),
                    {},
                ),
            )
            required_ids = {
                item["id"] for item in profile_policy.get("required_companions", [])
            }
            custom_count = len(set(profile_selection.get("companion_ids", [])) - required_ids)
            if (
                not any(item.entity_state_schema is not None for item in declarations)
                or not any(
                    item.get("prefix") == "setting" for item in retained.get("packages", [])
                )
                or not isinstance(maximum, int)
                or maximum <= custom_count
            ):
                raise CommandRejected(
                    "Select a Plan with an available Custom Companion slot "
                    "and character creation rules",
                    code="character_profile_target_unavailable",
                )
            reference_options["references"] = {
                **retained,
                "character_profile": profile.snapshot(),
            }
        pinned_character = retained.get("character_copy")
        if existing is not None:
            if (pinned_character is None and character_id is not None) or (
                pinned_character is not None and (
                    pinned_character.get("artifact_id") != character_id
                    or pinned_character.get("content_sha256") != character_hash
                )
            ):
                raise CommandRejected(
                    "This launch already selected a different Character File",
                    code="stale_character",
                )
        elif character_id is not None:
            source = next((
                item
                for item in _reusable_participant_catalog(ctx.storage_root, ctx.application_owner)
                if item.artifact_id == character_id and item.role == purpose
            ), None)
            if source is None or source.content_sha256 != character_hash:
                raise CommandRejected(
                    "Refresh the selected Character File before editing a copy",
                    code="stale_character",
                )
            reference_options["references"] = {
                **retained,
                "character_copy": {
                    "artifact_id": source.artifact_id,
                    "content_sha256": source.content_sha256,
                    "name": source.name,
                },
            }
    subflow = launch(
        operation_id=operation_id,
        campaign_id=_required_text(params, "campaign_id"),
        parent_session_id=_required_text(params, "parent_session_id"),
        expected_construction_revision=_required_nonnegative_int(
            params, "expected_construction_revision"
        ),
        purpose=purpose,
        visible_answer=_required_text(params, "visible_answer"),
        child_session_id=child_session_id,
        trigger_message_id=_optional_text(params, "trigger_message_id"),
        **reference_options,
    )
    return _json_value(subflow)


def handle_return_guided_creation_subflow(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    subflow = ctx.bus.return_guided_creation_subflow(
        operation_id=_required_text(params, "operation_id"),
        campaign_id=_required_text(params, "campaign_id"),
        parent_session_id=_required_text(params, "parent_session_id"),
        child_session_id=_required_text(params, "child_session_id"),
        expected_construction_revision=_required_nonnegative_int(
            params, "expected_construction_revision"
        ),
        use_result=_optional_bool(params, "use_result"),
    )
    return _json_value(subflow)


def handle_confirm_new_campaign(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    if "builder_session_id" in params:
        raise OwnerProtocolError("builder_session_id is owner-assigned")
    operation_id = _required_text(params, "operation_id")
    builder_session_id = (
        "session-builder-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    )
    candidate_id = params.get("candidate_id")
    if candidate_id is None:
        package_lock = PackageLock(entries=())
    elif isinstance(candidate_id, str) and candidate_id:
        package_lock = _candidate_package_lock(ctx.bus, candidate_id)
    else:
        raise OwnerProtocolError("candidate_id must be a nonempty string or null")
    confirmation = ctx.bus.confirm_new_campaign(
        operation_id=operation_id,
        display_name=_required_text(params, "display_name"),
        package_lock=package_lock,
        turn_id=_required_text(params, "turn_id"),
        package_candidate_id=candidate_id,
        builder_session_id=builder_session_id,
        builder_provider_id=_required_text(params, "provider_id"),
        builder_model_id=_required_text(params, "model_id"),
        builder_selected_skill=(
            _optional_text(params, "builder_skill") or "BUILD CAMPAIGN PLAN"
        ),
    )
    return {
        "campaign_id": confirmation.campaign_id,
        "resulting_revision": confirmation.resulting_revision,
        "builder_session_id": builder_session_id,
    }


def handle_begin_campaign_construction(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    construction_input = params.get("canonical_input")
    if not isinstance(construction_input, dict):
        raise OwnerProtocolError("canonical_input must be an object")
    operation_id = _required_text(params, "operation_id")
    campaign_id = _required_text(params, "campaign_id")
    lane = _required_text(params, "lane")
    begun = ctx.bus.begin_campaign_construction(
        operation_id=operation_id,
        campaign_id=campaign_id,
        lane=lane,
        expected_revision=_required_nonnegative_int(params, "expected_revision"),
        canonical_input=construction_input,
    )
    campaign = next(
        entry
        for entry in ctx.application_owner.campaign_index()
        if entry.campaign_id == campaign_id
    )
    builder_session = next(
        session for session in campaign.sessions if session.role == "builder"
    )
    ctx.application_owner.append_agent_message(
        session_id=builder_session.session_id,
        message_id=(
            "construction-brief-"
            + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
        ),
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "campaign_construction_brief",
            "lane": lane,
            "input": construction_input,
        },
    )
    return begun


def handle_complete_campaign_construction(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    canonical_plan = params.get("canonical_plan")
    if not isinstance(canonical_plan, dict):
        raise OwnerProtocolError("canonical_plan must be an object")
    return ctx.bus.complete_campaign_construction(
        operation_id=_required_text(params, "operation_id"),
        campaign_id=_required_text(params, "campaign_id"),
        expected_revision=_required_nonnegative_int(params, "expected_revision"),
        canonical_plan=canonical_plan,
    )


def handle_finalize_campaign_construction(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    campaign_id = _required_text(params, "campaign_id")
    dm_provider_id = _required_text(params, "dm_provider_id")
    dm_model_id = _required_text(params, "dm_model_id")
    plan_text = _campaign_plan_text(ctx.storage_root, campaign_id)
    structured_plan = _campaign_plan_payload(ctx.storage_root, campaign_id)
    _preflight_guided_start(
        application_owner=ctx.application_owner,
        bus=ctx.bus,
        provider_resolver=ctx.provider_resolver,
        owner_id=ctx.owner_id,
        campaign_id=campaign_id,
        provider_id=dm_provider_id,
        model_id=dm_model_id,
        plan_text=plan_text,
        structured_plan=structured_plan,
        gm_system_prompt=ctx.gm_system_prompt,
    )
    construction, plan, handoff = ctx.bus.finalize_campaign_construction(
        campaign_id=campaign_id,
        dm_provider_id=dm_provider_id,
        dm_model_id=dm_model_id,
        plan_artifact_text=plan_text,
        structured_plan=structured_plan,
        model_guidance_override=_optional_bool(
            params,
            "model_guidance_override",
        ),
    )
    dm_turn_id = ctx.application_owner.current_open_turn(campaign_id)
    if dm_turn_id is None:
        suffix = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]
        dm_turn_id = f"turn-dm-{suffix}"
        ctx.bus.open_turn(campaign_id, dm_turn_id)
    return {
        "construction": construction,
        "plan": plan,
        "handoff": handoff,
        "dm_turn_id": dm_turn_id,
        "dm_attempt_id": (
            "attempt-campaign-start-"
            + hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]
        ),
    }


def handle_catalog(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    catalog: Any = ctx.bus.catalog(_required_text(params, "campaign_id"))
    return [
        {
            "package_id": tool.package_id,
            "package_version": tool.package_version,
            "package_sha256": tool.package_sha256,
            "name": tool.name,
            "capability": tool.capability,
            "input_schema": tool.input_schema,
        }
        for tool in catalog
    ]


def handle_rename_campaign(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.rename_campaign(
        operation_id=_required_text(params, "operation_id"),
        campaign_id=_required_text(params, "campaign_id"),
        display_name=_required_text(params, "display_name"),
        expected_revision=_required_nonnegative_int(params, "expected_revision"),
    )


def handle_archive_campaign(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.archive_campaign(
        operation_id=_required_text(params, "operation_id"),
        campaign_id=_required_text(params, "campaign_id"),
        expected_revision=_required_nonnegative_int(params, "expected_revision"),
        designated_session_id=_optional_text(params, "designated_session_id"),
    )


def handle_restore_archived_campaign(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.restore_archived_campaign(
        operation_id=_required_text(params, "operation_id"),
        campaign_id=_required_text(params, "campaign_id"),
        expected_revision=_required_nonnegative_int(params, "expected_revision"),
        session_id=_optional_text(params, "session_id"),
    )


def handle_trash_campaign(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.trash_campaign(
        operation_id=_required_text(params, "operation_id"),
        campaign_id=_required_text(params, "campaign_id"),
        expected_revision=_required_nonnegative_int(params, "expected_revision"),
        designated_session_id=_optional_text(params, "designated_session_id"),
    )


def handle_restore_trashed_campaign(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.restore_trashed_campaign(
        operation_id=_required_text(params, "operation_id"),
        campaign_id=_required_text(params, "campaign_id"),
        expected_revision=_required_nonnegative_int(params, "expected_revision"),
    )


def handle_purge_campaign(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    campaign_id = _required_text(params, "campaign_id")
    operation_id = _required_text(params, "operation_id")
    workspace = ctx.storage_root / "campaigns" / campaign_id
    purge_stage = None

    def prepare_external_purge() -> None:
        nonlocal purge_stage
        purge_stage = stage_builder_sources_for_purge(
            workspace,
            campaign_id=campaign_id,
            operation_id=operation_id,
        )

    def rollback_external_purge() -> None:
        if purge_stage is not None:
            restore_builder_sources_after_failed_purge(purge_stage)

    purge_result = ctx.application_owner.purge_campaign(
        operation_id=operation_id,
        campaign_id=campaign_id,
        expected_revision=_required_nonnegative_int(params, "expected_revision"),
        confirmation=_required_text(params, "confirmation"),
        prepare_external_purge=prepare_external_purge,
        rollback_external_purge=rollback_external_purge,
    )
    if purge_stage is None:
        finalize_existing_builder_source_purge(
            workspace,
            campaign_id=campaign_id,
            operation_id=operation_id,
        )
    else:
        finalize_builder_source_purge(purge_stage)
    return purge_result


def handle_backup_database(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    backup_id = _required_artifact_id(params, "backup_id")
    target = ctx.storage_root / "backups" / f"{backup_id}.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    ctx.bus.backup_database(target)
    return {"backup_id": backup_id, "status": "created"}


def handle_export_campaign_plan_file(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    export_id = _required_artifact_id(params, "export_id")
    plan_id = _required_text(params, "plan_id")
    expected = _required_text(params, "expected_content_sha256")
    selected = next(
        (item for item in _campaign_creation_catalog(ctx.storage_root, ctx.application_owner)
         if item.get("kind") == "campaign_plan" and item.get("id") == plan_id),
        None,
    )
    if selected is None:
        # A retained built-in saved reference can be exported without adding
        # it to the public new-campaign catalogue. Its exact digest still gates export.
        from sylvia_gm.application.bundled_campaign_plans import bundled_plan
        retained = bundled_plan(plan_id)
        if retained is not None and retained.setup_id is not None:
            selected = dict(retained.catalog_payload())
            selected.pop("reusable", None)
            selected["bundled"] = True
    if selected is None or selected.get("content_sha256") != expected:
        raise CommandRejected("Selected Campaign Plan changed; refresh its library entry")
    target = ctx.storage_root / "exports" / f"{export_id}.sylvia-gm-plan"
    target.parent.mkdir(parents=True, exist_ok=True)
    if selected.get("reusable"):
        saved = load_reusable_campaign_plan(
            ctx.storage_root, plan_id,
            validate_plan=lambda payload, text: validate_retained_campaign_plan(
                ctx.application_owner, payload, text,
            ),
        )
        plan = saved.plan
    else:
        saved = selected_campaign_plan(ctx.storage_root, plan_id)
        plan = saved.plan
    if selected.get("reusable") and saved.package_snapshot is not None:
        with target.open("wb") as stream:
            receipt = export_plan_bundle(
                ctx.storage_root, saved.artifact_id,
                expected_content_sha256=saved.content_sha256, target=stream,
            )
    else:
        # Capture an older Plan's retained sources without changing its library entry.
        snapshot = saved.package_snapshot if saved.package_snapshot is not None else (
            retained_plan_snapshot(ctx.application_owner, plan.payload)
        )
        with tempfile.TemporaryDirectory(prefix=".plan-export-", dir=ctx.storage_root) as temporary:
            export_root = Path(temporary)
            portable = save_reusable_campaign_plan(
                export_root, operation_id=f"portable-{plan_id}",
                source_payload=plan.payload, source_markdown=plan.readable_markdown,
                validate_plan=lambda payload, text: validate_plan_snapshot(
                    snapshot, payload, text,
                ),
                package_snapshot=snapshot,
                setup_id=cast(str | None, selected.get("setup_id")),
                interview_contribution=(
                    saved.interview_contribution if saved is not None
                    else selected.get("interview_contribution")
                ),
            )
            with target.open("wb") as stream:
                receipt = export_plan_bundle(
                    export_root, portable.artifact_id,
                    expected_content_sha256=portable.content_sha256, target=stream,
                )
    return {"export": receipt, "artifact_path": str(target.resolve(strict=True))}


def handle_export_campaign(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    export_id = _required_artifact_id(params, "export_id")
    target = ctx.storage_root / "exports" / f"{export_id}.sylvia-gm-campaign"
    target.parent.mkdir(parents=True, exist_ok=True)
    result = ctx.bus.export_campaign(
        _required_text(params, "operation_id"),
        _required_text(params, "campaign_id"),
        target,
        include_thinking=_optional_bool(params, "include_thinking"),
    )
    if ctx.method == "export_campaign_file":
        return {"export": result, "artifact_path": str(target.resolve(strict=True))}
    return result


def handle_recover_campaign(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    export_id = _required_artifact_id(params, "export_id")
    source = ctx.storage_root / "exports" / f"{export_id}.sylvia-gm-campaign"
    return ctx.bus.recover_campaign(
        recovery_id=_required_text(params, "recovery_id"),
        source=source,
    )


def handle_recover_campaign_file(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    source = Path(_required_text(params, "source_path")).resolve(strict=True)
    if source.suffix != ".sylvia-gm-campaign" or not source.is_file():
        raise OwnerProtocolError("Selected recovery source is not a Sylvia GM campaign archive")
    return ctx.bus.recover_campaign(
        recovery_id=_required_text(params, "recovery_id"),
        source=source,
    )
