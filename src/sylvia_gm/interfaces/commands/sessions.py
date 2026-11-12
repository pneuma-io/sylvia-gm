"""Sessions command handlers moved from the owner protocol router."""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any, BinaryIO, Literal, cast

from sylvia_gm.agent._builder_question_answers import (
    builder_question_answer_content,
    builder_question_details,
)
from sylvia_gm.agent._builder_session import (
    BuilderTurnResult,
    CampaignContentToolAuthority,
    persist_builder_turn,
    stream_builder_turn,
)
from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import (
    ProviderControl,
    ProviderEvent,
    SessionInputKind,
    SessionProviderInput,
    SessionRunRequest,
)
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool
from sylvia_gm.application.builder_references import (
    launch_candidate,
    launch_references,
    reference_reader,
)
from sylvia_gm.application.bundled_packages import bundled_rift_authority
from sylvia_gm.application.campaign_library_selection import (
    participant_catalog_payload,
    require_completed_plan_characters,
)
from sylvia_gm.application.campaign_plan_sources import (
    CampaignPlanToolAuthority,
    campaign_plan_sources,
    validate_retained_campaign_plan,
)
from sylvia_gm.application.character_profiles import (
    load_character_profile,
    profile_from_snapshot,
    save_character_profile,
)
from sylvia_gm.application.compatible_character_copy import (
    authored_character_state,
    copy_compatible_character,
)
from sylvia_gm.application.import_repair import materialize_repair_draft
from sylvia_gm.application.library_sources import LibrarySourceReader
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.application.reusable_participants import save_reusable_participant
from sylvia_gm.application.reusable_plans import save_reusable_campaign_plan
from sylvia_gm.builder._campaign_plan_materializer import (
    CampaignPlanDraft,
    publish_campaign_plan_files,
)
from sylvia_gm.builder._plan_draft_check import check_plan_draft
from sylvia_gm.builder._plan_snapshot_materializer import materialize_plan_snapshot_draft
from sylvia_gm.builder._setting_draft_check import check_setting_draft
from sylvia_gm.builder._setting_materializer import materialize_setting_draft
from sylvia_gm.campaign.rift import RIFT_RULESET_ID
from sylvia_gm.config.provider_settings import (
    clear_session_model_once_settings,
    provider_settings,
    session_model_once_settings,
    session_speed_settings,
)
from sylvia_gm.domain.authority import PackageRole, SafePayload, WriterRole
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    ParticipantArtifactDeclaration,
    campaign_setting_entries,
    canonical_json_bytes,
)
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.interfaces._session_reasoning import reasoning_state, speed_state
from sylvia_gm.provider._admin_audit import AdminAuditedProvider
from sylvia_gm.provider._reasoning import bind_session_reasoning

from ..owner_process import (
    _MAX_USER_TEXT_CHARS,
    OwnerProtocolError,
    _admitted_dm_campaign_plan,
    _builder_workspace,
    _campaign_content_tool_authority,
    _campaign_creation_catalog,
    _campaign_plan_tool_authority,
    _context_fitted_provider,
    _model_slash_command,
    _optional_bool,
    _optional_nonnegative_int,
    _optional_text,
    _publish_builder_package_candidate,
    _publish_builder_ruleset_candidate,
    _required_nonnegative_int,
    _required_text,
    _reusable_participant_catalog,
    _rift_participant_authority,
    _selection_from_reference,
    _switch_session_model,
    _switch_session_reasoning,
    _switch_session_speed,
)
from . import DispatchContext


def handle_switch_session_reasoning(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    if "effort" not in params:
        raise OwnerProtocolError("Reasoning selection requires effort")
    return _switch_session_reasoning(
        application_owner=ctx.application_owner, session_loop=ctx.session_loop,
        config_path=ctx.storage_root / "config.yaml",
        session_id=_required_text(params, "session_id"),
        effort=params["effort"], mode=_optional_text(params, "mode") or "current",
        choices=ctx.model_choices(),
    )


def handle_switch_session_speed(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    if "speed" not in params:
        raise OwnerProtocolError("Speed selection requires speed")
    return _switch_session_speed(
        application_owner=ctx.application_owner, session_loop=ctx.session_loop,
        config_path=ctx.storage_root / "config.yaml",
        session_id=_required_text(params, "session_id"),
        speed=params["speed"], choices=ctx.model_choices(),
    )


def handle_session_context(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    session_id = _required_text(params, "session_id")
    activity = ctx.session_loop.builder_activity(session_id)
    # Read chronology after its activity basis so a completed result includes its final prose.
    page_fields = {}
    if ctx.method == "session_history_page":
        history_page = ctx.application_owner.session_history_page(
            session_id, before_sequence=params.get("before_sequence"),
            limit=params.get("limit", 50),
        )
        page_fields = {
            "next_before_sequence": history_page.next_before_sequence,
            "latest_sequence": history_page.latest_sequence,
            "latest_user_sequence": history_page.latest_user_sequence,
            "launch_messages": history_page.launch_messages,
            "latest_canonical_sequence": history_page.latest_canonical_sequence,
            "act_markers": history_page.act_markers,
            "campaign_ending": history_page.campaign_ending,
            "campaign_endings": history_page.campaign_endings,
            "builder_question_messages": tuple(
                message for message in history_page.builder_question_messages
                if builder_question_details(message.content) is not None
            ),
        }
        display_session = history_page.session
        display_messages = history_page.messages
    else:
        context = ctx.application_owner.session_context(session_id)
        display_session = context.session
        display_messages = context.messages
    settings = provider_settings(ctx.storage_root / "config.yaml")
    pending_model = session_model_once_settings(
        ctx.storage_root / "config.yaml", session_id=session_id,
    )
    return {
        "session": display_session,
        "messages": display_messages,
        "thinking": ctx.application_owner.private_provider_index(session_id),
        **page_fields,
        "provisional": ctx.session_loop.provisional_session_projection(session_id),
        "activity": activity,
        "activity_progress": ctx.session_loop.activity_progress(session_id),
        "context_status_hint": ctx.session_loop.context_status_hint(session_id),
        "reasoning_state": reasoning_state(display_session, ctx.model_choices(), pending_model),
        "speed_state": speed_state(
            display_session, ctx.model_choices(),
            session_speed_settings(ctx.storage_root / "config.yaml", session_id=session_id),
            pending_model,
        ),
        "model_state": {
            "current": {
                "provider_id": display_session.provider_id,
                "model_id": display_session.model_id,
            },
            "once": pending_model,
            "default_model": settings.get("default_model"),
            "chat_defaults": settings.get("chat_defaults"),
        },
    }


def handle_steer_session(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    steering_text = _required_text(params, "text")
    if len(steering_text) > _MAX_USER_TEXT_CHARS:
        raise OwnerProtocolError("text exceeds the session steering limit")
    ctx.session_loop.steer(
        _required_text(params, "session_id"),
        steering_text,
        message_id=_required_text(params, "message_id"),
    )
    return {"status": "accepted"}


def handle_submit_session_input(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    ending_choice = params.get("campaign_ending_choice")
    if ending_choice is not None and (
        not isinstance(ending_choice, Mapping)
        or set(ending_choice) != {"ending_id", "choice"}
        or not isinstance(ending_choice.get("ending_id"), str)
        or ending_choice.get("choice") not in {"continue", "finish"}
    ):
        raise OwnerProtocolError("Campaign ending choice is invalid")
    builder_start = _optional_bool(params, "builder_start")
    player_action_id = _optional_text(params, "player_action_id")
    automatic_response = _optional_bool(params, "automatic_response")
    if automatic_response and player_action_id is None:
        raise OwnerProtocolError("Automatic response requires its saved player action")
    if builder_start and player_action_id is not None:
        raise OwnerProtocolError("Builder start cannot respond to a gameplay action")
    raw_user_text = params.get("user_text", "")
    if not isinstance(raw_user_text, str):
        raise OwnerProtocolError("user_text must be text")
    user_text = raw_user_text
    raw_ooc_text = params.get("ooc_text")
    if raw_ooc_text is not None and not isinstance(raw_ooc_text, str):
        raise OwnerProtocolError("ooc_text must be text")
    ooc_text = raw_ooc_text if isinstance(raw_ooc_text, str) and raw_ooc_text.strip() else None
    if (not user_text.strip() and ooc_text is None
            and not builder_start and not player_action_id and ending_choice is None):
        raise OwnerProtocolError("user_text or ooc_text must be nonempty")
    if player_action_id is not None and (user_text or ooc_text is not None):
        raise OwnerProtocolError("Action responses do not contain player speech")
    if builder_start and (user_text or ooc_text is not None):
        raise OwnerProtocolError("Builder start does not accept invented player input")
    if len(user_text) > _MAX_USER_TEXT_CHARS:
        raise OwnerProtocolError("user_text exceeds the session input limit")
    if ooc_text is not None and len(ooc_text) > _MAX_USER_TEXT_CHARS:
        raise OwnerProtocolError("ooc_text exceeds the session input limit")
    session_id = _required_text(params, "session_id")
    question_answers = params.get("builder_question_answers")
    context = ctx.application_owner.session_context(session_id)
    if question_answers is not None:
        if (builder_start or player_action_id or ending_choice is not None or ooc_text
                or params.get("redirect_interruption_id")):
            raise OwnerProtocolError("Question answers cannot be combined with other input")
        builder_question_answer_content(context, question_answers, user_text)
    model_command = _model_slash_command(user_text) if ooc_text is None else None
    if model_command is not None:
        reference, mode = model_command
        if reference is None:
            ctx.session_loop.assert_idle(session_id)
            return {"status": "model_picker_requested", "mode": mode}
        selection = _selection_from_reference(ctx.model_choices(), reference)
        return _switch_session_model(
            application_owner=ctx.application_owner,
            session_loop=ctx.session_loop,
            config_path=ctx.storage_root / "config.yaml",
            session_id=session_id,
            provider_id=selection["provider_id"],
            model_id=selection["model_id"],
            mode=mode,
            choices=ctx.model_choices(),
        )
    if ending_choice is not None and (
        context.session.role is not WriterRole.DM or builder_start or player_action_id
        or user_text or ooc_text or params.get("redirect_interruption_id")
    ):
        raise OwnerProtocolError("Ending choices require an idle GM ending without other input")
    if builder_start and context.session.role is not WriterRole.BUILDER:
        raise OwnerProtocolError("builder_start is available only to Builder sessions")
    if context.session.role is WriterRole.BUILDER and ooc_text is not None:
        raise OwnerProtocolError("ooc_text is available only to DM sessions")
    turn_id = (
        _required_text(params, "turn_id")
        if context.session.campaign_id is not None
        else (_optional_text(params, "turn_id") or f"builder-chat-{session_id}")
    )
    if player_action_id is not None:
        response_basis = ctx.application_owner.player_action_response_basis(
            session_id, player_action_id, automatic=automatic_response,
        )
        if response_basis["turn_id"] != turn_id:
            raise OwnerProtocolError("The saved player action belongs to another turn")
    pending_once = session_model_once_settings(
        ctx.storage_root / "config.yaml", session_id=session_id
    )

    def consume_pending_once() -> None:
        if pending_once is None:
            return
        clear_session_model_once_settings(
            ctx.storage_root / "config.yaml",
            session_id=session_id,
            expected_provider_id=pending_once["provider_id"],
            expected_model_id=pending_once["model_id"],
        )

    effective_session = (
        context.session
        if pending_once is None
        else dataclasses.replace(
            context.session,
            provider_id=pending_once["provider_id"],
            model_id=pending_once["model_id"],
        )
    )
    try:
        provider = ctx.provider_resolver(effective_session)
    except BaseException:
        consume_pending_once()
        raise
    if ctx.application_owner.admin_full_audit_enabled():
        provider = AdminAuditedProvider(
            provider,
            sink=ctx.application_owner.record_admin_full_audit,
            session_id=session_id,
            role=effective_session.role.value,
            provider_id=effective_session.provider_id,
            model_id=effective_session.model_id,
            selection_mode="once" if pending_once is not None else "session",
        )
    provider = _context_fitted_provider(
        bind_session_reasoning(
            provider, effective_session,
            session_speed_settings(ctx.storage_root / "config.yaml", session_id=session_id),
        ),
        gm_mode=effective_session.role is WriterRole.DM,
    )
    if user_text.strip() == "/compact" and ooc_text is None:
        from sylvia_gm.agent._builder_session import _provider_history
        from sylvia_gm.agent._working_context import compact_saved_messages

        if context.session.role is WriterRole.BUILDER and context.session.campaign_id:
            construction_basis = ctx.application_owner.campaign_construction_basis(
                context.session.campaign_id
            )
            if (
                construction_basis.builder_session_id == session_id
                and construction_basis.completion_revision is None
            ):
                raise OwnerProtocolError(
                    "The campaign Interview keeps its exact transcript. "
                    "Compaction is unavailable."
                )

        def compact_provider(provider_input: SessionProviderInput) -> Iterator[ProviderEvent]:
            if context.session.role is WriterRole.BUILDER:
                messages = _provider_history(provider_input.history)
            else:
                adapter = (
                    ProviderSessionAdapter(ctx.application_owner, provider, owner_id=ctx.owner_id)
                )
                messages = adapter._messages(
                    provider_input, campaign_has_mechanics=False, tools_available=False,
                )
            yield from cast(Callable[..., Iterable[ProviderEvent]], compact_saved_messages)(
                provider, provider_input.history, messages, provider_input.control,
            )

        try:
            ctx.session_loop.run_compaction(
                session_id, _required_text(params, "attempt_id"), compact_provider,
            )
            return {"session_id": session_id, "status": "context_compacted",
                    "messages": (), "interruption": None}
        finally:
            consume_pending_once()
    if context.session.role is WriterRole.BUILDER:
        from sylvia_gm.agent._builder_title import TITLE_LIMIT, opening_title

        fallback_title = None
        request_title = False
        if context.session.campaign_id is None:
            current_chat = next(
                (
                    chat
                    for chat in ctx.application_owner.builder_chat_index()
                    if chat.session.session_id == session_id
                ),
                None,
            )
            if (current_chat is not None and current_chat.display_name == "New chat"
                    and question_answers is None):
                title = opening_title(user_text)
                if title:
                    ctx.application_owner.rename_builder_chat(session_id, title, automatic=True)
                    fallback_title = title
                    request_title = len(" ".join(user_text.split())) > TITLE_LIMIT and not any(
                        message.kind in {SessionMessageKind.USER, SessionMessageKind.TALK}
                        or message.content.get("kind") == "builder_title_preference"
                        for message in context.messages
                    )
        allow_public_web = _optional_bool(params, "allow_public_web")
        request = SessionRunRequest(
            session_id=session_id,
            turn_id=turn_id,
            attempt_id=_required_text(params, "attempt_id"),
            message_id=_required_text(params, "message_id"),
            user_text=user_text,
            input_kind=(
                SessionInputKind.BUILDER_START if builder_start else SessionInputKind.USER
            ),
            builder_question_answers=(
                tuple(question_answers) if question_answers is not None else None
            ),
        )
        campaign_plan_authority = None
        if context.session.campaign_id is not None and context.session.selected_skill in {
            None,
            "BUILD CAMPAIGN PLAN",
        }:
            campaign_plan_authority = _campaign_plan_tool_authority(
                ctx.application_owner,
                context.session.campaign_id,
            )
        participant_purpose = next(
            (
                str(message.content.get("purpose"))
                for message in context.messages
                if message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind") in {
                    "creation_subflow_link", "builder_launch_context"
                }
                and message.content.get("purpose") in {"player_character", "companion"}
            ),
            None,
        )
        participant_ruleset_selection = None
        participant_setting_selection = None
        if participant_purpose is not None:
            link = next(
                message.content
                for message in context.messages
                if message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind") in {
                    "creation_subflow_link", "builder_launch_context"
                }
                and (context.session.campaign_id is not None
                     or message.content.get("kind") == "builder_launch_context")
            )
            selections = link.get("selections")
            if isinstance(selections, Mapping):
                ruleset = selections.get("ruleset", {})
                setting = selections.get("setting", {})
                ruleset_value = ruleset.get("value", {}) if isinstance(ruleset, Mapping) else {}
                setting_value = setting.get("value", {}) if isinstance(setting, Mapping) else {}
                if isinstance(ruleset_value, Mapping):
                    participant_ruleset_selection = ruleset_value.get("candidate_id")
                if isinstance(setting_value, Mapping):
                    participant_setting_selection = setting_value.get("setting_id")
            parent_session_id = link.get("parent_session_id")
            if isinstance(parent_session_id, str):
                parent_context = ctx.application_owner.session_context(parent_session_id)
                parent_states = tuple(
                    message
                    for message in parent_context.messages
                    if message.kind is SessionMessageKind.EVIDENCE
                    and message.content.get("kind") == "campaign_interview_state"
                )
                parent_state = parent_states[-1].content if parent_states else {}
                answers = parent_state.get("answers")
                ruleset_message_id = (
                    answers.get("ruleset") if isinstance(answers, Mapping) else None
                )
                ruleset_message = next(
                    (
                        message
                        for message in parent_context.messages
                        if message.message_id == ruleset_message_id
                    ),
                    None,
                )
                ruleset_value = (
                    None
                    if ruleset_message is None
                    else ruleset_message.content.get("interview_value")
                )
                if isinstance(ruleset_value, Mapping):
                    participant_ruleset_selection = ruleset_value.get("candidate_id")
                setting_message_id = (
                    answers.get("setting") if isinstance(answers, Mapping) else None
                )
                setting_message = next(
                    (
                        message
                        for message in parent_context.messages
                        if message.message_id == setting_message_id
                    ),
                    None,
                )
                setting_value = (
                    None
                    if setting_message is None
                    else setting_message.content.get("interview_value")
                )
                if isinstance(setting_value, Mapping):
                    participant_setting_selection = setting_value.get("setting_id")
        rift_authority = (
            bundled_rift_authority()
            if participant_purpose is not None
            and participant_ruleset_selection == RIFT_RULESET_ID
            else None
        )
        participant_authority = (
            None
            if rift_authority is None
            or participant_setting_selection != rift_authority.setting.package_id
            else _rift_participant_authority()
        )
        participant_campaign_setting = (
            rift_authority.setting
            if participant_authority is not None and rift_authority is not None else None
        )
        participant_package = (
            None if participant_authority is None else participant_authority[0]
        )
        participant_declarations: tuple[ParticipantArtifactDeclaration, ...] = (
            () if participant_authority is None else (participant_authority[1],)
        )
        participant_setting_schema = None
        saved_references = launch_references(context)
        from sylvia_gm.agent._builder_source_tool import (
            builder_setting_source_tool,
            builder_source_tool,
        )
        from sylvia_gm.application.builder_source_selection import BuilderSourceSelection

        source_selection = BuilderSourceSelection(
            ctx.application_owner, ctx.storage_root, context,
            lambda: _campaign_creation_catalog(ctx.storage_root, ctx.application_owner),
        )
        library_sources = None
        if saved_references is not None:
            # Independent chats always retain their original package and Setting choices.
            participant_package = None
            participant_declarations = ()
            participant_campaign_setting = None
            candidate = launch_candidate(ctx.application_owner, context)
            setting_reference = next(
                (
                    item
                    for item in cast(
                        Sequence[Mapping[str, Any]], saved_references.get("packages", [])
                    )
                    if item.get("prefix") == "setting"
                ),
                None,
            )
            if candidate is not None and context.session.campaign_id is None:
                primary = next(
                    entry for entry in candidate.package_lock.entries
                    if entry.role is PackageRole.PRIMARY
                )
                library_sources = LibrarySourceReader(
                    ctx.application_owner, primary.package_id,
                    str(setting_reference["id"]) if setting_reference is not None else None,
                )
            if candidate is not None and setting_reference is not None:
                if context.session.selected_skill in {None, "BUILD CAMPAIGN PLAN"}:
                    campaign_plan_authority = campaign_plan_sources(
                        ctx.application_owner, candidate.package_lock,
                        setting_id=str(setting_reference["id"]),
                    )
                if participant_purpose is not None:
                    participant_package = next(
                        (entry for entry in candidate.package_lock.entries
                         if entry.role is PackageRole.PRIMARY),
                        None,
                    )
                    participant_declarations = (
                        ctx.application_owner.exact_participant_declarations(
                            participant_package.package_id,
                            participant_package.version,
                            participant_package.sha256,
                        ) if participant_package is not None else ()
                    )
                    participant_campaign_setting = PackageBinding(
                        str(setting_reference["id"]), str(setting_reference["version"]),
                        str(setting_reference["content_sha256"]),
                    )
                    participant_sources = campaign_plan_sources(
                        ctx.application_owner, candidate.package_lock,
                        setting_id=participant_campaign_setting.package_id,
                    )
                    participant_setting_schema = (
                        participant_sources.setting_character_schema
                        if participant_sources is not None else None
                    )
        builder_result: BuilderTurnResult | None = None
        repair_character: tuple[str, str] | None = None
        for message in context.messages:
            receipt = message.content.get("result")
            if (
                message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind") == "builder_tool_result"
                and message.content.get("tool_name") == "open_character_draft"
                and isinstance(receipt, Mapping)
                and receipt.get("success") is True
                and isinstance(receipt.get("source_artifact_id"), str)
                and isinstance(receipt.get("source_content_sha256"), str)
            ):
                repair_character = (
                    str(receipt["source_artifact_id"]),
                    str(receipt["source_content_sha256"]),
                )

        def resolve_source_profile(requested: SafePayload | None) -> SafePayload | None:
            """Bind provenance to retained or verified content, never model text."""
            if requested is not None and (
                not isinstance(requested, Mapping)
                or set(requested) != {"artifact_id", "content_sha256"}
                or any(not isinstance(value, str) or not value for value in requested.values())
            ):
                raise CommandRejected(
                    "Supply the source profile's exact identity and hash",
                    code="character_profile_invalid",
                )
            pinned = (saved_references or {}).get("character_profile")
            provenance = None
            if pinned is not None:
                provenance = profile_from_snapshot(pinned).provenance()
            elif repair_character is not None:
                original = next(
                    (
                        item
                        for item in _reusable_participant_catalog(
                            ctx.storage_root,
                            ctx.application_owner,
                        )
                        if item.artifact_id == repair_character[0]
                        and item.content_sha256 == repair_character[1]
                    ),
                    None,
                )
                if original is None:
                    raise CommandRejected(
                        "Reopen the saved character before repairing it", code="stale_character"
                    )
                provenance = original.source_profile
            if requested is None:
                return provenance
            if provenance is not None and all(
                requested[key] == provenance[key] for key in requested
            ):
                return provenance
            if pinned is not None:
                raise CommandRejected(
                    "The profile differs from this chat's original source",
                    code="stale_character_profile",
                )
            return load_character_profile(
                ctx.storage_root,
                str(requested["artifact_id"]),
                str(requested["content_sha256"]),
            ).provenance()

        def publish_profile(data: bytes) -> SafePayload:
            """Save unbound content without selecting or mutating a campaign."""
            from sylvia_gm.application.character_profiles import validate_character_profile

            canonical = canonical_json_bytes(validate_character_profile(data))
            saved = save_character_profile(
                ctx.storage_root,
                operation_id=f"{session_id}:{hashlib.sha256(canonical).hexdigest()}",
                source=data,
            )
            return saved.catalog_payload()

        def import_profile(data: bytes) -> SafePayload:
            from sylvia_gm.application.character_card_import import save_character_card_import
            return save_character_card_import(ctx.storage_root, operation_id=(
                f'{session_id}'
                f':card:'
                f'{hashlib.sha256(data).hexdigest()}'
            ), source=data)

        from sylvia_gm.agent._character_profile_tools import character_profile_tools

        profile_tools = (
            character_profile_tools(
                _builder_workspace(ctx.storage_root, context.session),
                save=publish_profile,
                import_source=import_profile,
                read=lambda identity, digest: load_character_profile(
                    ctx.storage_root, identity, digest
                ),
            )
            if context.session.campaign_id is None
            else ()
        )

        def publish_ruleset_candidate(
            archive: BinaryIO,
            *,
            install_id: str,
            expected_closure_sha256: str,
        ) -> SafePayload:
            return _publish_builder_ruleset_candidate(
                ctx.application_owner,
                session_id,
                archive,
                install_id=install_id,
                expected_closure_sha256=expected_closure_sha256,
            )

        def publish_participant(
            *,
            role: str,
            summary: str,
            authored_profile: str,
            participant: SafePayload,
            entity_state: SafePayload,
            schema_id: str | None = None,
            setting_data: SafePayload | None = None,
            source_profile: SafePayload | None = None,
        ) -> SafePayload:
            if (
                participant_package is None
                or participant_campaign_setting is None
                or role != participant_purpose
            ):
                raise CommandRejected("Participant publication is unavailable")
            declarations = tuple(
                declaration for declaration in participant_declarations
                if schema_id is None or declaration.schema_id == schema_id
            )
            if len(declarations) != 1:
                raise CommandRejected("Choose one of this Ruleset's participant schema IDs")
            declaration = declarations[0]
            source_profile = resolve_source_profile(source_profile)
            content_basis = canonical_json_bytes(
                {
                    "session_id": session_id,
                    "role": role,
                    "summary": summary,
                    "authored_profile": authored_profile,
                    "participant": participant,
                    "entity_state": entity_state,
                    "setting_data": setting_data,
                    "source_profile": source_profile,
                }
            )
            operation_suffix = hashlib.sha256(content_basis).hexdigest()[:24]
            saved = save_reusable_participant(
                ctx.storage_root,
                operation_id=f"builder-participant-{operation_suffix}",
                role=role,
                summary=summary,
                authored_profile=authored_profile,
                participant=participant,
                entity_state=entity_state,
                package=participant_package,
                declaration=declaration,
                campaign_setting=participant_campaign_setting,
                setting_data=setting_data,
                setting_character_schema=participant_setting_schema,
                source_profile=source_profile,
            )
            if context.session.campaign_id is None:
                label = "Character" if role == "player_character" else "Companion"
                ctx.application_owner.rename_builder_chat(
                    session_id, f"{saved.name} · {label}"[:120].strip(), automatic=True
                )
            return participant_catalog_payload(ctx.application_owner, saved)

        def read_participant(
            *, artifact_id: str, expected_content_sha256: str, schema_id: str | None = None,
        ) -> SafePayload:
            """Read an exact library source for explicit adaptation, without publishing it."""
            nonlocal repair_character
            if participant_package is None or participant_campaign_setting is None:
                raise CommandRejected("Character editing needs this chat's saved Setting")
            source = next((
                item
                for item in _reusable_participant_catalog(ctx.storage_root, ctx.application_owner)
                if item.artifact_id == artifact_id and item.role == participant_purpose
            ), None)
            if source is None:
                raise CommandRejected("The source character is unavailable for this role")
            if source.content_sha256 != expected_content_sha256:
                raise CommandRejected(
                    "The source character changed; refresh the catalog", code="stale_character"
                )
            declarations = tuple(
                item for item in participant_declarations
                if schema_id is None or item.schema_id == schema_id
            )
            if len(declarations) != 1:
                raise CommandRejected("Choose one of this Ruleset's participant schema IDs")
            repair_character = (source.artifact_id, source.content_sha256)
            return {
                "schema_id": declarations[0].schema_id,
                "summary": source.summary, "authored_profile": source.authored_profile,
                "participant": dict(source.participant),
                "entity_state": dict(authored_character_state(
                    source.entity_state, declarations[0]
                )),
                "setting_data": dict(source.setting_data),
            }

        def reuse_participant(
            *, artifact_id: str, expected_content_sha256: str, schema_id: str | None = None,
        ) -> SafePayload:
            if participant_package is None or participant_campaign_setting is None:
                raise CommandRejected("Character reuse needs this chat's saved Setting")
            source = next((
                item
                for item in _reusable_participant_catalog(ctx.storage_root, ctx.application_owner)
                if item.artifact_id == artifact_id and item.role == participant_purpose
            ), None)
            if source is None:
                raise CommandRejected("The source character is unavailable for this role")
            declarations = tuple(
                item for item in participant_declarations
                if schema_id is None or item.schema_id == schema_id
            )
            if len(declarations) != 1:
                raise CommandRejected("Choose one of this Ruleset's participant schema IDs")
            copy_basis = canonical_json_bytes({
                "session_id": session_id, "source": artifact_id,
                "content_sha256": expected_content_sha256,
                "setting": participant_campaign_setting.as_payload(),
                "schema_id": declarations[0].schema_id,
            })
            saved = copy_compatible_character(
                ctx.storage_root, source=source, expected_content_sha256=expected_content_sha256,
                operation_id=(
                    "builder-character-copy-" + hashlib.sha256(copy_basis).hexdigest()[:32]
                ),
                package=participant_package, declaration=declarations[0],
                campaign_setting=participant_campaign_setting,
                setting_character_schema=participant_setting_schema,
            )
            if context.session.campaign_id is None:
                label = "Character" if saved.role == "player_character" else "Companion"
                ctx.application_owner.rename_builder_chat(
                    session_id, f"{saved.name} · {label}"[:120].strip(), automatic=True
                )
            return {
                **dict(participant_catalog_payload(ctx.application_owner, saved)),
                "source_artifact_id": source.artifact_id,
            }

        completing_required_characters = any(
            message.kind is SessionMessageKind.EVIDENCE
            and message.content.get("kind") == "builder_launch_context"
            and message.content.get("purpose") == "plan"
            and message.content.get("interview_step") == "participants"
            for message in context.messages
        )

        def selected_plan_authority() -> CampaignPlanToolAuthority | None:
            """Use an explicit plain-chat Setting immediately, retaining contextual bindings."""
            if not source_selection.selectable:
                return campaign_plan_authority
            references = source_selection.references
            setting = next((item for item in cast(
                Sequence[Mapping[str, Any]], (references or {}).get("packages", ())
            )
                            if item.get("prefix") == "setting"), None)
            source = source_selection.candidate()
            if setting is None or source is None:
                return None
            return campaign_plan_sources(
                ctx.application_owner, source.package_lock, setting_id=str(setting["id"]),
            )

        def check_plan() -> SafePayload:
            source = source_selection.candidate()
            selected_authority = selected_plan_authority()
            if source is None or selected_authority is None:
                raise CommandRejected("Plan checking needs this chat's saved Setting")
            return check_plan_draft(
                ctx.application_owner, _builder_workspace(ctx.storage_root, context.session),
                candidate_id=source.candidate_id, expected_authority=selected_authority,
                require_complete_characters=completing_required_characters,
            )

        def check_setting(*, package_id: str, version: str, title: str) -> SafePayload:
            source = source_selection.candidate()
            if source is None or context.session.campaign_id is not None:
                raise CommandRejected("Setting checking needs this chat's saved Ruleset")
            return check_setting_draft(
                ctx.application_owner, _builder_workspace(ctx.storage_root, context.session),
                candidate_id=source.candidate_id,
                package_id=package_id, version=version, title=title,
            )

        def publish_plan() -> tuple[CampaignPlanDraft, SafePayload]:
            selected_authority = selected_plan_authority()
            if selected_authority is None or context.session.campaign_id is not None:
                raise CommandRejected("Independent Campaign Plan saving is unavailable")
            source = source_selection.candidate()
            if source is None:
                raise CommandRejected("Plan publication needs this chat's saved Setting")
            workspace = _builder_workspace(ctx.storage_root, context.session)
            with materialize_plan_snapshot_draft(
                ctx.application_owner, workspace, candidate_id=source.candidate_id,
                expected_authority=selected_authority,
            ) as materialized:
                effective_candidate = source
                if materialized.closure_sha256 != source.closure_sha256:
                    suffix = hashlib.sha256(
                        f"{session_id}:{materialized.closure_sha256}".encode()
                    ).hexdigest()[:32]
                    # Runtime admission precedes saving a selectable Plan.
                    effective_candidate = _publish_builder_package_candidate(
                        ctx.application_owner, session_id, materialized.archive,
                        install_id=f"builder-plan-snapshot-{suffix}",
                        expected_closure_sha256=materialized.closure_sha256,
                    )
                materialized.archive.seek(0)
                plan = materialized.plan
                if completing_required_characters:
                    require_completed_plan_characters(
                        ctx.application_owner, effective_candidate.candidate_id, plan,
                    )
                saved = save_reusable_campaign_plan(
                    ctx.storage_root,
                    operation_id=f"builder-plan-{session_id}-{plan.content_sha256}",
                    source_payload=plan.payload,
                    source_markdown=plan.readable_markdown,
                    validate_plan=lambda payload, text: validate_retained_campaign_plan(
                        ctx.application_owner, payload, text,
                    ),
                    package_snapshot=materialized.archive.read(),
                )
            published = publish_campaign_plan_files(workspace, saved.plan)
            ctx.application_owner.rename_builder_chat(
                session_id, f"{saved.title} · Campaign Plan"[:120].strip(), automatic=True
            )
            return published, saved.catalog_payload()

        def publish_setting(*, package_id: str, version: str, title: str) -> SafePayload:
            source = source_selection.candidate()
            if source is None or context.session.campaign_id is not None:
                raise CommandRejected("Setting publication needs this chat's saved Ruleset")
            with materialize_setting_draft(
                ctx.application_owner, _builder_workspace(ctx.storage_root, context.session),
                candidate_id=source.candidate_id,
                package_id=package_id, version=version, title=title,
            ) as materialized:
                suffix = hashlib.sha256(
                    f"{session_id}:{materialized.closure_sha256}".encode()
                ).hexdigest()[:32]
                installed = _publish_builder_package_candidate(
                    ctx.application_owner, session_id, materialized.archive,
                    install_id=f"builder-setting-{suffix}",
                    expected_closure_sha256=materialized.closure_sha256,
                )
            setting = campaign_setting_entries(installed.package_lock)[0]
            ctx.application_owner.rename_builder_chat(
                session_id, f"{title} · Campaign Setting"[:120].strip(), automatic=True,
            )
            return {
                "kind": "campaign_setting", "id": setting.package_id,
                "version": setting.version, "content_sha256": setting.sha256,
                "title": title, "candidate_id": installed.candidate_id,
            }

        repair_context = next((
            message.content for message in context.messages
            if message.kind is SessionMessageKind.EVIDENCE
            and message.content.get("kind") == "import_repair_context"
        ), None)

        def run_import_repair(*, publishing: bool) -> SafePayload:
            if repair_context is None or context.session.campaign_id is not None:
                raise CommandRejected("This chat has no selected import repair")
            with materialize_repair_draft(
                _builder_workspace(ctx.storage_root, context.session),
                expected_kind=cast(Literal["package", "plan"], repair_context["import_kind"]),
            ) as materialized:
                if not publishing:
                    return {
                        "status": "valid_draft", "published": False, "state_changed": False,
                        "closure_sha256": materialized.closure_sha256,
                    }
                materialized.archive.seek(0)
                suffix = hashlib.sha256(
                    f"{session_id}:{materialized.closure_sha256}".encode()
                ).hexdigest()[:32]
                installed = _publish_builder_package_candidate(
                    ctx.application_owner, session_id, materialized.archive,
                    install_id=f"builder-repair-{suffix}",
                    expected_closure_sha256=materialized.closure_sha256,
                )
                if materialized.plan is not None:
                    materialized.archive.seek(0)
                    snapshot = materialized.archive.read()
                    plan = materialized.plan
                    saved = save_reusable_campaign_plan(
                        ctx.storage_root,
                        operation_id=f"builder-repair-{session_id}-{plan.content_sha256}",
                        source_payload=plan.payload, source_markdown=plan.readable_markdown,
                        validate_plan=lambda payload, text: validate_plan_snapshot(
                            snapshot, payload, text,
                        ),
                        package_snapshot=snapshot,
                        setup_id=materialized.setup_id,
                        interview_contribution=materialized.interview_contribution,
                    )
                    receipt = saved.catalog_payload()
                else:
                    receipt = {
                        "kind": "package", "candidate_id": installed.candidate_id,
                        "closure_sha256": installed.closure_sha256,
                    }
                return {"status": "published", "published": True, **dict(receipt)}

        from sylvia_gm.agent._import_repair_tools import import_repair_tools

        repair_tools = import_repair_tools(
            lambda: run_import_repair(publishing=False),
            lambda: run_import_repair(publishing=True),
        ) if repair_context is not None and context.session.campaign_id is None else ()

        def builder_provider(
            provider_input: SessionProviderInput,
        ) -> Iterator[ProviderEvent]:
            nonlocal builder_result

            def complete(result: BuilderTurnResult) -> None:
                nonlocal builder_result
                builder_result = result

            yield from stream_builder_turn(
                provider,
                context=provider_input.history,
                message_id=request.message_id,
                user_text=request.user_text,
                workspace=_builder_workspace(ctx.storage_root, context.session),
                control=provider_input.control,
                complete=complete,
                request_title=request_title,
                continuation_reader=lambda response_id: ctx.application_owner.provider_continuation(
                    provider_input.session.session_id, response_id,
                    provider_id=provider_input.session.provider_id,
                    model_id=provider_input.session.model_id,
                ),
                publish_ruleset_candidate=publish_ruleset_candidate,
                publish_participant=(
                    publish_participant if participant_declarations else None
                ),
                participant_schemas={
                    declaration.schema_id: declaration.schema
                    for declaration in participant_declarations
                },
                participant_declarations=participant_declarations,
                setting_character_schema=participant_setting_schema,
                reuse_participant=reuse_participant if participant_declarations else None,
                read_participant=read_participant if participant_declarations else None,
                campaign_plan_authority=(selected_plan_authority if source_selection.selectable
                                         else campaign_plan_authority),
                publish_campaign_plan=(
                    publish_plan if context.session.campaign_id is None else None
                ),
                publish_campaign_setting=(
                    publish_setting if (source_selection.selectable or (
                        saved_references is not None
                        and saved_references.get("candidate_id") is not None))
                    and context.session.campaign_id is None else None
                ),
                check_campaign_plan=(
                    check_plan if (campaign_plan_authority is not None
                                   or source_selection.selectable)
                    and context.session.campaign_id is None else None
                ),
                check_campaign_setting=(
                    check_setting if (source_selection.selectable or (
                        saved_references is not None
                        and saved_references.get("candidate_id") is not None))
                    and context.session.campaign_id is None else None
                ),
                repair_tools=repair_tools,
                profile_tools=profile_tools,
                resolve_source_profile=resolve_source_profile,
                campaign_content_authority=CampaignContentToolAuthority(
                    # Discovery refreshes the catalog only when its tool is called.
                    items=(),
                    refresh=lambda: _campaign_content_tool_authority(
                        ctx.application_owner, ctx.storage_root
                    ).items,
                ),
                references=(source_selection.reader if source_selection.selectable
                            else reference_reader(ctx.application_owner, provider_input.history)),
                source_tools=(builder_source_tool(source_selection),
                              builder_setting_source_tool(source_selection))
                if source_selection.selectable else (),
                library_sources=library_sources,
                allow_public_web=allow_public_web,
                application_start=builder_start,
            )

        def persist_builder_result() -> tuple[object, ...]:
            if builder_result is None:
                raise RuntimeError("Builder provider completed without a result")
            return persist_builder_turn(
                ctx.application_owner,
                session_id=session_id,
                result=builder_result,
            )

        try:
            appended = ctx.session_loop.run_builder(
                request,
                builder_provider,
                persist_builder_result,
            )
            if builder_result is None:  # pragma: no cover - generator contract defense
                raise RuntimeError("Builder provider completed without a result")
            if builder_result.suggested_title:
                # Publication-derived names and explicit user renames take precedence.
                current_chat = next(
                    (chat for chat in ctx.application_owner.builder_chat_index()
                     if chat.session.session_id == session_id), None,
                )
                if current_chat is not None and current_chat.display_name == fallback_title:
                    ctx.application_owner.rename_builder_chat(
                        session_id, builder_result.suggested_title, automatic=True,
                    )
            return {
                "session_id": session_id,
                "tool_calls_made": builder_result.tool_calls_made,
                "messages": appended,
                "interruption": None,
            }
        finally:
            consume_pending_once()
    if _optional_bool(params, "allow_public_web"):
        raise OwnerProtocolError("allow_public_web is available only to Builder sessions")
    if context.session.campaign_id is None:
        raise OwnerProtocolError("DM sessions require campaign authority")
    campaign_plan_text, campaign_plan = _admitted_dm_campaign_plan(
        ctx.application_owner,
        campaign_id=context.session.campaign_id,
        session_id=session_id,
    )
    adapter = ProviderSessionAdapter(
        ctx.application_owner,
        provider,
        owner_id=ctx.owner_id,
        gm_system_prompt=ctx.gm_system_prompt,
        campaign_plan_text=campaign_plan_text,
        campaign_plan=campaign_plan,
    )
    try:
        return ctx.session_loop.run(
            SessionRunRequest(
                session_id=session_id,
                turn_id=turn_id,
                attempt_id=_required_text(params, "attempt_id"),
                message_id=_required_text(params, "message_id"),
                user_text=user_text,
                ooc_text=ooc_text,
                campaign_ending_choice=ending_choice,
                input_kind=(
                    SessionInputKind.PLAYER_ACTION_RESPONSE
                    if player_action_id is not None else SessionInputKind.USER
                ),
                player_action_id=player_action_id,
                automatic_response=automatic_response,
                redirect_interruption_id=_optional_text(params, "redirect_interruption_id"),
            ),
            adapter.for_turn(turn_id),
        )
    finally:
        consume_pending_once()


def handle_interrupt_stream(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.interrupt_stream(
        _required_text(params, "campaign_id"),
        _required_text(params, "attempt_id"),
    )


def handle_manual_retry_session(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    session_id = _required_text(params, "session_id")
    context = ctx.application_owner.session_context(session_id)
    pending_once = session_model_once_settings(
        ctx.storage_root / "config.yaml", session_id=session_id
    )
    effective_session = (
        context.session
        if pending_once is None
        else dataclasses.replace(
            context.session,
            provider_id=pending_once["provider_id"],
            model_id=pending_once["model_id"],
        )
    )
    try:
        provider = ctx.provider_resolver(effective_session)
    except BaseException:
        if pending_once is not None:
            clear_session_model_once_settings(
                ctx.storage_root / "config.yaml",
                session_id=session_id,
                expected_provider_id=pending_once["provider_id"],
                expected_model_id=pending_once["model_id"],
            )
        raise
    if ctx.application_owner.admin_full_audit_enabled():
        provider = AdminAuditedProvider(
            provider,
            sink=ctx.application_owner.record_admin_full_audit,
            session_id=session_id,
            role=effective_session.role.value,
            provider_id=effective_session.provider_id,
            model_id=effective_session.model_id,
            selection_mode="once" if pending_once is not None else "session",
        )
    provider = _context_fitted_provider(
        bind_session_reasoning(
            provider, effective_session,
            session_speed_settings(ctx.storage_root / "config.yaml", session_id=session_id),
        ),
        gm_mode=effective_session.role is WriterRole.DM,
    )
    control = ProviderControl()
    retry_plan_text: str | None = None
    retry_plan: Mapping[str, object] | None = None
    campaign_knowledge: SafePayload | None = None
    if effective_session.role is WriterRole.DM and effective_session.campaign_id is not None:
        retry_plan_text, retry_plan = _admitted_dm_campaign_plan(
            ctx.application_owner,
            campaign_id=effective_session.campaign_id,
            session_id=session_id,
        )
        if retry_plan is not None:
            campaign_knowledge = ctx.application_owner.campaign_knowledge_catalog_for_plan(
                effective_session.campaign_id,
                retry_plan,
            )
    adapter = ProviderSessionAdapter(
        ctx.application_owner,
        provider,
        owner_id=ctx.owner_id,
        gm_system_prompt=ctx.gm_system_prompt,
        campaign_plan_text=retry_plan_text,
        campaign_plan=retry_plan,
        campaign_knowledge=campaign_knowledge,
    )
    retry_provider_input = SessionProviderInput(
        session=effective_session,
        history=context,
        catalog=(
            tuple(
                cast(
                    Sequence[ModelMechanicsTool],
                    ctx.bus.catalog(effective_session.campaign_id),
                )
            )
            if effective_session.campaign_id is not None
            else ()
        ),
        control=control,
    )
    try:
        return ctx.session_loop.retry(
            session_id,
            interruption_id=_required_text(params, "interruption_id"),
            retry_id=_required_text(params, "retry_id"),
            narrator=adapter.for_retry(
                effective_session.model_id,
                provider_input=retry_provider_input,
                control=control,
            ),
            provider_factory=adapter.for_turn,
            cancel=control.cancel,
            control=control,
        )
    finally:
        if pending_once is not None:
            clear_session_model_once_settings(
                ctx.storage_root / "config.yaml",
                session_id=session_id,
                expected_provider_id=pending_once["provider_id"],
                expected_model_id=pending_once["model_id"],
            )


def handle_disconnect_stream(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return {
        "disconnect_deadline": ctx.bus.disconnect_stream(
            _required_text(params, "campaign_id"),
            _required_text(params, "attempt_id"),
            expected_generation=_required_nonnegative_int(params, "attachment_generation"),
        )
    }


def handle_stream_heartbeat(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    attachment_deadline, attachment_generation = ctx.bus.heartbeat_stream(
        _required_text(params, "campaign_id"),
        _required_text(params, "attempt_id"),
        expected_generation=_optional_nonnegative_int(params, "attachment_generation"),
    )
    return {
        "attachment_deadline": attachment_deadline,
        "attachment_generation": attachment_generation,
    }


def handle_narration_attempt_status(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return {
        "status": ctx.bus.narration_attempt_status(
            _required_text(params, "campaign_id"),
            _required_text(params, "attempt_id"),
        )
    }


def handle_reconnect_stream(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.reconnect_stream(
        campaign_id=_required_text(params, "campaign_id"),
        attempt_id=_required_text(params, "attempt_id"),
        acknowledged_sequence=_required_nonnegative_int(params, "acknowledged_sequence"),
        expected_generation=_required_nonnegative_int(params, "attachment_generation"),
        listener=lambda _channel, _token: None,
    )


def handle_start_dm_session(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    campaign_id = _required_text(params, "campaign_id")
    session_id = _required_text(params, "session_id")
    turn_id = _required_text(params, "turn_id")
    dm_context = ctx.application_owner.session_context(session_id)
    if (
        dm_context.session.role is not WriterRole.DM
        or dm_context.session.campaign_id != campaign_id
        or ctx.application_owner.current_open_turn(campaign_id) != turn_id
    ):
        raise CommandRejected("Campaign start session authority does not match")
    has_start_event = any(
        message.kind is SessionMessageKind.EVIDENCE
        and message.content.get("kind") == "campaign_start"
        for message in dm_context.messages
    )
    has_opening_narration = any(
        message.kind is SessionMessageKind.CANONICAL_REFERENCE
        and message.content.get("event_type") == "talk"
        for message in dm_context.messages
    )
    if has_start_event and has_opening_narration:
        return {"status": "already_started", "session_id": session_id}
    provider = ctx.provider_resolver(dm_context.session)
    if ctx.application_owner.admin_full_audit_enabled():
        provider = AdminAuditedProvider(
            provider,
            sink=ctx.application_owner.record_admin_full_audit,
            session_id=session_id,
            role=dm_context.session.role.value,
            provider_id=dm_context.session.provider_id,
            model_id=dm_context.session.model_id,
            selection_mode="session",
        )
    provider = _context_fitted_provider(
        bind_session_reasoning(
            provider, dm_context.session,
            session_speed_settings(ctx.storage_root / "config.yaml", session_id=session_id),
        ),
        gm_mode=True,
    )
    opening_plan_text, opening_plan = _admitted_dm_campaign_plan(
        ctx.application_owner,
        campaign_id=campaign_id,
        session_id=session_id,
    )
    adapter = ProviderSessionAdapter(
        ctx.application_owner,
        provider,
        owner_id=ctx.owner_id,
        gm_system_prompt=ctx.gm_system_prompt,
        campaign_plan_text=opening_plan_text,
        campaign_plan=opening_plan,
    )
    suffix = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]
    return ctx.session_loop.run(
        SessionRunRequest(
            session_id=session_id,
            turn_id=turn_id,
            attempt_id=f"attempt-campaign-start-{suffix}",
            message_id=f"message-campaign-start-{suffix}",
            user_text="",
            input_kind=SessionInputKind.CAMPAIGN_START,
        ),
        adapter.for_turn(turn_id),
    )


def handle_stop_agent_session(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    session_id = _required_text(params, "session_id")
    context = ctx.application_owner.session_context(session_id)
    stopped = ctx.session_loop.stop_agent_session(session_id)
    if context.session.role is WriterRole.DM:
        stopped_campaign_id = context.session.campaign_id
        if stopped_campaign_id is None:
            raise OwnerProtocolError("DM sessions require campaign authority")
        open_turn_id = ctx.application_owner.current_open_turn(stopped_campaign_id)
        if open_turn_id is not None:
            ctx.application_owner.complete_turn(stopped_campaign_id, open_turn_id)
    return stopped


def handle_resume_agent_session(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.resume_agent_session(_required_text(params, "session_id"))
