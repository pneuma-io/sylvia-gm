"""Exact F4 Builder/DM session and handoff acceptance probes.

The numbered probe labels in this module map directly to the frozen F4 matrix.
Tests deliberately use only synthetic archives, temporary SQLite databases, and
injected providers.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import io
import json
import os
import socket
import sqlite3
import subprocess
import threading
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    CatalogTool,
)
from sylvia_gm.domain.authority import (
    ActionReceipt,
    MechanicContext,
    MechanicOutcome,
    NarrationStream,
    PackageLock,
    Participant,
    ProseChannel,
    RetryNarrationInput,
    StateMutation,
    StateValue,
    TurnStatus,
    WriterRole,
)
from sylvia_gm.domain.errors import (
    AuthorityError,
    CommandRejected,
    IdempotencyConflict,
    SchemaIdentityError,
    StaleRevision,
    WrongWriter,
)
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record
from sylvia_gm.domain.session import SessionMessageKind, SessionStatus
from sylvia_gm.provider._exceptions import ProviderConnectionError, ProviderTimeoutError

FROZEN_F4_PROBE_MAP = {
    1: "Create/open DM and Builder sessions with stable model/provider metadata.",
    2: "Close/reopen and reconstruct safe history/context exactly.",
    3: (
        "Prove raw/private reasoning is absent from resumable history, canonical events, "
        "SQLite/WAL, ordinary logs, and Builder return evidence."
    ),
    4: "Prove TALK, safe THINK, TOOL CALL, and TOOL RESULT order matches F2.",
    5: (
        "One fake-provider tool call commits exactly once through F1; stale revision and "
        "wrong writer reject."
    ),
    6: "A pending tool card becomes one terminal chronological card.",
    7: (
        "Stop cancels active inference, discards unfinished prose, and preserves committed "
        "mechanics/results."
    ),
    8: (
        "Disconnect grace and expiry follow F2; no automatic retry, fallback, or invisible "
        "continuation."
    ),
    9: "Manual Retry uses stored action result and never reruns mechanics.",
    10: (
        "Steering changes later safe context without mutating campaign state or prior "
        "canonical history."
    ),
    11: (
        "Builder advertises exactly the three selected skills and loads only the requested "
        "task skill."
    ),
    12: (
        "Natural-prompt Builder entry accepts synthetic user-provided/open-source evidence "
        "and records provenance."
    ),
    13: (
        "Unsupported capability or contradictory evidence stops for human review without "
        "approximate-success claims."
    ),
    14: (
        "Parent and child contexts remain isolated; bounded child return reaches parent as "
        "non-authoritative evidence only."
    ),
    15: (
        "Child draft generation produces zero campaign, package-lock, plan, revision, or "
        "writer effect."
    ),
    16: ("Invalid package/provenance/plan admission fails closed and leaves authority unchanged."),
    17: "Valid package candidate crosses F3 without activating a campaign.",
    18: (
        "Valid plan admission records exact content identity, provenance, validation outcome, "
        "and revision once."
    ),
    19: (
        "Handoff rejects wrong writer, stale revision, wrong exact package lock, "
        "invalid/unaccepted plan, active parent/child work, non-quiescent lifecycle, and "
        "incomplete TURN."
    ),
    20: (
        "Failure injection before/during atomic handoff leaves Builder authoritative, no DM "
        "writer/session, no partial chronology, and unchanged revision."
    ),
    21: (
        "Starting DM records exactly one revision/chronology, keeps the Builder session active, "
        "and establishes DM gameplay writer authority without giving Builder DM mechanics access."
    ),
    22: (
        "Duplicate exact handoff resolves idempotently; changed reuse conflicts before "
        "stale/current-state checks."
    ),
    23: (
        "Archive is rejected during incomplete work and admits idle persistent Builder/DM chats "
        "after TURN completion; do not implement F5 lifecycle behavior."
    ),
    24: (
        "Migration 4 opens an exact F3 database and reopens deterministically; migrations 1–3 "
        "and F1/F2/F3 snapshots remain exact."
    ),
    25: (
        "The accepted 93 F1–F3 tests remain green. PROTECTED PARENT-OWNED GATE; the F4 writer "
        "labels but does not execute it."
    ),
    26: (
        "All tests use injected fake providers, synthetic temporary databases/documents, no "
        "network, and no executable plugins."
    ),
}

FROZEN_F4_PROBE_EVIDENCE = {
    1: ("test_probe_01_shared_builder_dm_sessions_persist_exact_model_provider_metadata",),
    2: ("test_probe_01_shared_builder_dm_sessions_persist_exact_model_provider_metadata",),
    3: ("test_probe_01_shared_builder_dm_sessions_persist_exact_model_provider_metadata",),
    4: ("test_probe_04_think_talk_tool_call_result_follow_one_f2_chronology",),
    5: ("test_probe_05_session_tool_commits_once_and_rejects_stale_and_wrong_writer",),
    6: ("test_probe_06_pending_tool_card_becomes_one_terminal_chronological_card",),
    7: ("test_probe_07_stop_cancels_once_discards_prose_preserves_committed_mechanics",),
    8: ("test_probe_08_disconnect_grace_expiry_has_no_automatic_retry_or_fallback",),
    9: ("test_probe_09_manual_retry_narrates_stored_result_without_rerunning_mechanic",),
    10: ("test_probe_10_steering_changes_later_safe_context_without_campaign_mutation",),
    11: ("test_probe_11_builder_advertises_exact_three_skills_and_loads_only_requested",),
    12: ("test_probe_12_natural_prompt_entry_records_user_and_open_source_provenance",),
    13: ("test_probe_13_unsupported_or_contradictory_capability_stops_for_human_review",),
    14: ("test_probe_14_builder_children_are_bounded_isolated_and_return_only_evidence",),
    15: ("test_probe_14_builder_children_are_bounded_isolated_and_return_only_evidence",),
    16: ("test_probe_16_invalid_package_provenance_and_plan_admission_is_fail_closed",),
    17: ("test_probe_17_valid_builder_package_crosses_f3_without_activation_or_repin",),
    18: (
        "test_probe_15_valid_plan_admission_records_identity_provenance_and_one_revision",
        "test_probe_18_exact_plan_identity_replays_after_restart_before_current_checks",
    ),
    19: ("test_probe_19_handoff_rejects_every_nonquiescent_or_inexact_basis",),
    20: ("test_probe_20_injected_handoff_failures_roll_back_every_authority_effect",),
    21: ("test_probe_21_existing_campaign_handoff_is_one_exact_atomic_revision",),
    22: ("test_probe_22_handoff_replay_identity_precedes_all_current_state_checks",),
    23: ("test_probe_23_archive_admission_preserves_idle_open_turns",),
    24: (
        "test_probe_24_exact_f3_first_use_migration_and_deterministic_reopen",
        "test_probe_24_startup_rejects_each_contradictory_f4_authority_field_and_releases_lock",
    ),
    25: ("test_probe_25_durable_map_labels_all_frozen_probes_and_parent_owned_93_gate",),
    26: (
        "test_probe_26_admitted_f4_source_and_behavior_are_strictly_synthetic_only",
        "test_probe_26_readable_builder_to_dm_end_to_end_authority_proof",
        "test_probe_26_static_and_runtime_synthetic_only_exclusion",
    ),
}

F4_PRODUCTION_PATHS = (
    "src/sylvia_gm/domain/authority.py",
    "src/sylvia_gm/application/authority.py",
    "src/sylvia_gm/infrastructure/sqlite_authority.py",
    "src/sylvia_gm/domain/session.py",
    "src/sylvia_gm/application/session.py",
    "src/sylvia_gm/infrastructure/sqlite_session.py",
    "src/sylvia_gm/agent/_session_loop.py",
    "src/sylvia_gm/agent/_skills.py",
)


def _zero_capability_archive() -> bytes:
    payload = b"{}"
    draft: dict[str, object] = {
        "id": "synthetic.zero-capability",
        "version": "1",
        "role": "primary",
        "metadata": {"label": "Synthetic Zero Capability"},
        "provenance": {
            "publisher": "synthetic-f4-tests",
            "source_sha256": hashlib.sha256(payload).hexdigest(),
        },
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": [],
        "tools": [],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [
            {
                "path": "data/zero-capability.json",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    record = {**draft, "content_sha256": content_root_for_record(draft)}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical_json_bytes({"format": 1, "packages": [record]}))
        archive.writestr("packages/synthetic.zero-capability/data/zero-capability.json", payload)
    return stream.getvalue()


def _clockwork_archive() -> bytes:
    payload = b'{"ticks":1}'
    tool = {
        "name": "advance",
        "capability": "clockwork.counter",
        "input_schema": {
            "type": "object",
            "properties": {"steps": {"type": "integer"}},
            "required": ["steps"],
            "additionalProperties": False,
        },
        "state_predicates": [],
        "turn_statuses": ["open"],
        "writers": ["builder", "dm"],
    }
    draft: dict[str, object] = {
        "id": "synthetic.clockwork",
        "version": "1",
        "role": "primary",
        "metadata": {"label": "Synthetic Clockwork"},
        "provenance": {"publisher": "synthetic-f4-tests", "source_sha256": "a" * 64},
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": ["clockwork.counter"],
        "tools": [tool],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [
            {
                "path": "data/clock.json",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    record = {**draft, "content_sha256": content_root_for_record(draft)}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical_json_bytes({"format": 1, "packages": [record]}))
        archive.writestr("packages/synthetic.clockwork/data/clock.json", payload)
    return stream.getvalue()


def _clockwork_campaign(
    owner: AuthorityOwner,
    *,
    campaign_id: str,
    writer: WriterRole,
    turn_id: str,
    calls: list[int],
    mechanic: Callable[[MechanicContext], MechanicOutcome] | None = None,
) -> object:
    candidate = owner.install_package_archive(
        io.BytesIO(_clockwork_archive()), install_id=f"install-{campaign_id}"
    )
    entry = candidate.package_lock.entries[0]

    def advance(context: MechanicContext) -> MechanicOutcome:
        calls.append(int(context.parameters["steps"]))
        before = int(context.state["clock.tick"])
        after = before + int(context.parameters["steps"])
        return MechanicOutcome(
            result={"before": before, "after": after},
            mutations=(StateMutation("clock", "tick", after),),
            consequences=({"kind": "clock_advanced", "after": after},),
        )

    owner.register_tool(
        CatalogTool(
            package_id=entry.package_id,
            package_version=entry.version,
            package_sha256=entry.sha256,
            name="advance",
            capability="clockwork.counter",
            input_schema={
                "type": "object",
                "properties": {"steps": {"type": "integer"}},
                "required": ["steps"],
                "additionalProperties": False,
            },
            execute=mechanic or advance,
        )
    )
    return owner.create_campaign(
        CampaignCreate(
            display_name=f"Synthetic {campaign_id}",
            writer=writer,
            package_lock=candidate.package_lock,
            turn_id=turn_id,
            campaign_id=campaign_id,
            participants=(
                Participant("participant", "observer", entry.package_id, entry.version, {}),
            ),
            initial_state=(StateValue("clock", "tick", 0),),
        )
    )


def _campaign(
    owner: AuthorityOwner,
    *,
    campaign_id: str,
    writer: WriterRole,
    turn_id: str,
) -> object:
    return owner.create_campaign(
        CampaignCreate(
            display_name=f"Synthetic {campaign_id}",
            writer=writer,
            package_lock=PackageLock(entries=()),
            turn_id=turn_id,
            campaign_id=campaign_id,
        )
    )


def test_campaign_file_memory_does_not_require_an_accepted_plan(tmp_path: Path) -> None:
    """Campaign State remains available when a campaign has no optional Plan preparation."""

    with AuthorityOwner.open(tmp_path / "planless-campaign-state.db", "owner-planless") as owner:
        campaign_id = "campaign-planless-state"
        session_id = "session-planless-state"
        turn_id = "turn-planless-state"
        _campaign(
            owner,
            campaign_id=campaign_id,
            writer=WriterRole.DM,
            turn_id=turn_id,
        )
        owner.create_agent_session(
            campaign_id=campaign_id,
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id=session_id,
        )
        basis_message_id = "message-planless-scene"
        owner.append_agent_message(
            session_id=session_id,
            message_id=basis_message_id,
            kind=SessionMessageKind.USER,
            content={"text": "I wait beneath the nearest glass tree."},
        )

        options = owner.campaign_narrative_options(session_id)
        assert options["operations"] == [
            "record_memory", "record_entity", "update_scene", "update_inventory",
        ]
        assert options["scene_act_ids"] == []
        assert options["eligible_secret_ids"] == []

        receipt = owner.update_campaign_file(
            session_id=session_id,
            update_id="campaign-file-planless-opening",
            updates=(
                {
                    "operation": "update_scene",
                    "location_id": "glass-orchard",
                    "summary": "Rain falls in the glass orchard beside an approaching lantern.",
                },
            ),
            basis_message_ids=(basis_message_id,),
            turn_id=turn_id,
        )
        assert receipt["status"] == "campaign_file_updated"
        assert owner.snapshot(campaign_id).continuity.current_scene["location_id"] == (
            "glass-orchard"
        )
        with pytest.raises(CommandRejected, match="update 1 is invalid"):
            owner.update_campaign_file(
                session_id=session_id,
                update_id="campaign-file-planless-phantom-act",
                updates=(
                    {
                        "operation": "update_scene",
                        "act_id": "act-1",
                        "summary": "A Plan-less campaign cannot invent Plan Progress.",
                    },
                ),
                basis_message_ids=(basis_message_id,),
                turn_id=turn_id,
            )
        state = owner.read_campaign_reference(
            campaign_id,
            source="campaign_state",
            path="current",
        )
        assert "glass-orchard" in state["content"]
        with pytest.raises(CommandRejected, match="requires an accepted plan"):
            owner.read_campaign_reference(
                campaign_id,
                source="campaign_plan",
                path="structured",
            )


def _prepare_handoff(
    owner: AuthorityOwner,
    *,
    suffix: str,
    with_package: bool = False,
    plan_content: dict[str, object] | None = None,
) -> dict[str, object]:
    campaign_id = f"campaign-handoff-{suffix}"
    builder_session_id = f"session-builder-{suffix}"
    turn_id = f"turn-handoff-{suffix}"
    if with_package:
        _clockwork_campaign(
            owner,
            campaign_id=campaign_id,
            writer=WriterRole.BUILDER,
            turn_id=turn_id,
            calls=[],
        )
        expected_candidate_id: str | None = f"candidate-install-{campaign_id}"
    else:
        _campaign(owner, campaign_id=campaign_id, writer=WriterRole.BUILDER, turn_id=turn_id)
        expected_candidate_id = None
    owner.create_agent_session(
        campaign_id=campaign_id,
        role=WriterRole.BUILDER,
        provider_id="synthetic-builder-provider",
        model_id="synthetic-builder-model",
        session_id=builder_session_id,
        selected_skill="BUILD CAMPAIGN PLAN",
    )
    evidence_id = f"evidence-handoff-{suffix}"
    owner.record_builder_evidence(
        session_id=builder_session_id,
        evidence_id=evidence_id,
        source_kind="user_provided",
        content_sha256=hashlib.sha256(f"premise-{suffix}".encode()).hexdigest(),
        source_locator=None,
        safe_excerpt="A synthetic handoff premise.",
        claims=("handoff-ready",),
    )
    content = (
        {"title": f"Handoff Plan {suffix}", "facts": ["handoff-ready"]}
        if plan_content is None
        else plan_content
    )
    content_sha256 = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
    lock_id = owner.current_package_lock(campaign_id).lock_id
    plan = owner.admit_campaign_plan(
        plan_id=f"plan-handoff-{suffix}",
        campaign_id=campaign_id,
        builder_session_id=builder_session_id,
        expected_revision=0,
        expected_package_lock_id=lock_id,
        content=content,
        content_sha256=content_sha256,
        provenance_evidence_ids=(evidence_id,),
        validation_outcome="valid",
        acceptance_status="accepted",
    )
    request: dict[str, object] = {
        "handoff_id": f"handoff-{suffix}",
        "campaign_id": campaign_id,
        "builder_session_id": builder_session_id,
        "dm_session_id": f"session-dm-{suffix}",
        "dm_provider_id": "synthetic-dm-provider",
        "dm_model_id": "synthetic-dm-model",
        "expected_writer": WriterRole.BUILDER,
        "expected_revision": 1,
        "expected_package_lock_id": lock_id,
        "expected_package_candidate_id": expected_candidate_id,
        "plan_id": plan.plan_id,
        "expected_plan_revision": plan.plan_revision,
        "expected_plan_content_sha256": plan.content_sha256,
        "completed_turn_id": turn_id,
    }
    if with_package:
        request["model_guidance_override"] = True
    return request


def test_campaign_file_memory_accepts_an_admitted_markdown_only_plan(tmp_path: Path) -> None:
    """Markdown-only preparation cannot disable the GM's durable Campaign File."""

    with AuthorityOwner.open(
        tmp_path / "markdown-plan-campaign-state.db", "owner-markdown"
    ) as owner:
        request = _prepare_handoff(
            owner,
            suffix="markdown-campaign-state",
            plan_content={
                "campaign_plan_markdown": (
                    "# Prepared direction\n\nLet the GM introduce the observatory naturally.\n"
                )
            },
        )
        campaign_id = str(request["campaign_id"])
        owner.complete_turn(campaign_id, str(request["completed_turn_id"]))
        owner.handoff_builder_to_dm(**request)

        options = owner.campaign_narrative_options(str(request["dm_session_id"]))

        assert options == {
            "operations": ["record_memory", "record_entity", "update_scene", "update_inventory"],
            "campaign_revision": 2,
            "scene_act_ids": [],
            "plan_item_ids": [],
            "eligible_secret_ids": [],
        }


def test_probe_01_shared_builder_dm_sessions_persist_exact_model_provider_metadata(
    tmp_path: Path,
) -> None:
    """Probe 1: ordinary Builder/DM roles share one durable session substrate."""

    database = tmp_path / "authority.db"
    with AuthorityOwner.open(database, "owner-f4") as owner:
        _campaign(
            owner,
            campaign_id="campaign-builder",
            writer=WriterRole.BUILDER,
            turn_id="turn-builder",
        )
        _campaign(
            owner,
            campaign_id="campaign-dm",
            writer=WriterRole.DM,
            turn_id="turn-dm",
        )
        create = getattr(owner, "create_agent_session", None)
        assert callable(create), (
            "F4 requires create_agent_session for ordinary Builder/DM roles; "
            "the generic/archive/import model is not the authorized substrate"
        )
        builder = create(
            campaign_id="campaign-builder",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider-a",
            model_id="synthetic-model-a",
            session_id="session-builder",
            selected_skill="BUILD RULESET",
        )
        dm = create(
            campaign_id="campaign-dm",
            role=WriterRole.DM,
            provider_id="synthetic-provider-b",
            model_id="synthetic-model-b",
            session_id="session-dm",
        )
        assert (
            builder.session_id,
            builder.campaign_id,
            builder.role,
            builder.provider_id,
            builder.model_id,
            builder.status.value,
            builder.parent_session_id,
            builder.selected_skill,
        ) == (
            "session-builder",
            "campaign-builder",
            WriterRole.BUILDER,
            "synthetic-provider-a",
            "synthetic-model-a",
            "active",
            None,
            "BUILD RULESET",
        )
        assert (
            dm.session_id,
            dm.campaign_id,
            dm.role,
            dm.provider_id,
            dm.model_id,
            dm.status.value,
            dm.parent_session_id,
            dm.selected_skill,
        ) == (
            "session-dm",
            "campaign-dm",
            WriterRole.DM,
            "synthetic-provider-b",
            "synthetic-model-b",
            "active",
            None,
            None,
        )
        print("F4 PROBE 01", builder, dm)

    with AuthorityOwner.open(database, "owner-f4-reopen") as reopened:
        builder_reopened = reopened.session_context("session-builder").session
        dm_reopened = reopened.session_context("session-dm").session
        assert builder_reopened == builder
        assert dm_reopened == dm


def test_probe_11_builder_advertises_exact_three_skills_and_loads_only_requested() -> None:
    """Probe 11: the Builder role has exactly the selected construction skills."""

    from sylvia_gm.agent import _skills

    advertised = _skills.builder_skills()
    assert advertised == (
        "BUILD RULESET",
        "BUILD CAMPAIGN SETTING",
        "BUILD CAMPAIGN PLAN",
    )
    loaded = _skills.load_builder_skill("BUILD CAMPAIGN PLAN")
    assert loaded.name == "BUILD CAMPAIGN PLAN"
    assert "BUILD CAMPAIGN PLAN" in loaded.text
    assert "BUILD RULESET" not in loaded.text
    assert "BUILD CAMPAIGN SETTING" not in loaded.text
    print("F4 PROBE 11", advertised, loaded.name)


def test_probes_02_03_shared_streaming_loop_persists_only_safe_resumable_history(
    tmp_path: Path,
) -> None:
    """Probes 2–3: shared streaming, safe THINK/TALK, durable safe resume."""

    from sylvia_gm.agent import _session_loop

    event_type = getattr(_session_loop, "ProviderEvent", None)
    request_type = getattr(_session_loop, "SessionRunRequest", None)
    loop_type = getattr(_session_loop, "CampaignSessionLoop", None)
    assert event_type is not None and request_type is not None and loop_type is not None

    database = tmp_path / "streaming.db"
    with AuthorityOwner.open(database, "owner-streaming", disconnect_grace_seconds=2.0) as owner:
        _campaign(
            owner,
            campaign_id="campaign-streaming",
            writer=WriterRole.BUILDER,
            turn_id="turn-streaming",
        )
        owner.create_agent_session(
            campaign_id="campaign-streaming",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-streaming",
            selected_skill="BUILD CAMPAIGN SETTING",
        )

        observed: list[object] = []

        def provider(provider_input: object) -> object:
            observed.append(provider_input)
            yield event_type.private_reasoning("SYNTHETIC-PRIVATE-THOUGHT")
            yield event_type.private_continuation(
                {"reasoning_content": "SYNTHETIC-NATIVE-CONTINUATION"},
                response_id="native-response",
            )
            yield event_type.safe_think("Check the supported source constraints.")
            yield event_type.talk("The setting evidence is ready for review.")

        result = loop_type(owner).run(
            request_type(
                session_id="session-streaming",
                turn_id="turn-streaming",
                attempt_id="attempt-streaming",
                message_id="message-streaming",
                user_text="Build a setting from this synthetic prompt.",
            ),
            provider,
        )
        assert observed and observed[0].session.selected_skill == "BUILD CAMPAIGN SETTING"
        assert observed[0].catalog == ()
        assert owner.catalog("campaign-streaming") == ()
        assert tuple(event.event_type.value for event in result.canonical) == ("think", "talk")
        assert tuple(event.safe_payload["text"] for event in result.canonical) == (
            "Check the supported source constraints.",
            "The setting evidence is ready for review.",
        )
        context = owner.session_context("session-streaming")
        assert "SYNTHETIC-NATIVE-CONTINUATION" not in repr(context)
        assert owner.provider_continuation(
            "session-streaming", "native-response",
            provider_id="synthetic-provider", model_id="synthetic-model",
        ) == {"reasoning_content": "SYNTHETIC-NATIVE-CONTINUATION"}
        assert tuple(message.kind.value for message in context.messages) == (
            "user",
            "canonical_reference",
            "canonical_reference",
        )
        assert context.messages[0].content["text"] == (
            "Build a setting from this synthetic prompt."
        )
        assert tuple(message.canonical_sequence for message in context.messages[1:]) == (1, 2)
        print(
            "F4 PROBES 02-03",
            context.session.provider_id,
            context.session.model_id,
            [event.event_type.value for event in result.canonical],
        )

    database_bytes = database.read_bytes()
    assert b"SYNTHETIC-PRIVATE-THOUGHT" in database_bytes
    assert b"setting evidence is ready" in database_bytes

    with AuthorityOwner.open(database, "owner-streaming-reopen") as reopened:
        resumed = reopened.session_context("session-streaming")
        assert resumed == context
        assert "SYNTHETIC-PRIVATE-THOUGHT" not in repr(resumed)
        assert reopened.inspect_provider_thinking(
            "session-streaming", "attempt-streaming", "attempt-streaming",
        ) == "SYNTHETIC-PRIVATE-THOUGHT"


def test_probe_04_think_talk_tool_call_result_follow_one_f2_chronology(
    tmp_path: Path,
) -> None:
    """Probe 4: shared loop prose and mechanics retain exact F2/F1 order."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    calls: list[int] = []
    with AuthorityOwner.open(
        tmp_path / "chronology.db", "owner-chronology", disconnect_grace_seconds=1.0
    ) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="campaign-chronology",
            writer=WriterRole.BUILDER,
            turn_id="turn-chronology",
            calls=calls,
        )
        owner.create_agent_session(
            campaign_id="campaign-chronology",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-chronology",
            selected_skill="BUILD RULESET",
        )
        entry = owner.current_package_lock("campaign-chronology").package_lock.entries[0]
        command = ActionCommand(
            campaign_id="campaign-chronology",
            action_id="action-chronology",
            idempotency_key="idem-chronology",
            owner_id="owner-chronology",
            writer=WriterRole.BUILDER,
            expected_revision=0,
            turn_id="turn-chronology",
            package_id=entry.package_id,
            tool_name="advance",
            parameters={"steps": 2},
        )

        def provider(_input: object) -> object:
            yield ProviderEvent.safe_think("Check the clock.")
            yield ProviderEvent.action(command)
            yield ProviderEvent.talk("The clock advanced to two.")

        result = CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id="session-chronology",
                turn_id="turn-chronology",
                attempt_id="attempt-chronology",
                message_id="message-chronology",
                user_text="Advance the synthetic clock.",
            ),
            provider,
        )
        event_types = tuple(
            event.event_type.value for event in owner.snapshot("campaign-chronology").events
        )
        assert event_types == (
            "think",
            "action_declared",
            "tool_call",
            "tool_result",
            "state_mutation",
            "consequence",
            "action_committed",
            "talk",
        )
        assert calls == [2]
        assert result.receipts[0].resulting_revision == 1
        print("F4 PROBE 04", event_types, calls, result.receipts[0].resulting_revision)


def test_mixed_ic_ooc_persists_as_one_exact_player_message(tmp_path: Path) -> None:
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, ProviderEvent, SessionRunRequest

    with AuthorityOwner.open(
        tmp_path / "mixed-ooc.db", "owner-mixed-ooc", disconnect_grace_seconds=1.0
    ) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="campaign-mixed-ooc",
            writer=WriterRole.DM,
            turn_id="turn-mixed-ooc",
            calls=[],
        )
        owner.create_agent_session(
            campaign_id="campaign-mixed-ooc",
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-mixed-ooc",
        )

        def provider(_input: object) -> object:
            yield ProviderEvent.talk(
                "The wolf recoils.\n\nChloe: Stay behind me.\n\nOOC — Sylvia: Preference noted.",
                response_blocks=(
                    {"kind": "narration", "text": "The wolf recoils."},
                    {
                        "kind": "npc_dialogue",
                        "speaker_label": "Chloe",
                        "text": "Stay behind me.",
                    },
                    {"kind": "ooc", "text": "Preference noted."},
                ),
            )

        CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id="session-mixed-ooc",
                turn_id="turn-mixed-ooc",
                attempt_id="attempt-mixed-ooc",
                message_id="message-mixed-ooc",
                user_text="I attack the wolf.",
                ooc_text="Please portray Chloe as more affectionate going forward.",
            ),
            provider,
        )

        player_messages = [
            message
            for message in owner.session_context("session-mixed-ooc").messages
            if message.kind is SessionMessageKind.USER
        ]
        assert len(player_messages) == 1
        assert player_messages[0].content == {
            "text": "I attack the wolf.",
            "ooc_text": "Please portray Chloe as more affectionate going forward.",
            "canonical_start_sequence": 0,
            "input_id": "attempt-mixed-ooc",
            "attempt_id": "attempt-mixed-ooc",
            "turn_id": "turn-mixed-ooc",
            "input_kind": "user",
        }
        talk = next(
            item
            for item in owner.player_projection("campaign-mixed-ooc")
            if item.kind.value == "talk"
        )
        assert talk.safe_payload["response_blocks"][1]["speaker_label"] == "Chloe"
        assert len(
            [
                event
                for event in owner.snapshot("campaign-mixed-ooc").events
                if event.event_type.value == "talk"
            ]
        ) == 1


def test_probe_05_session_tool_commits_once_and_rejects_stale_and_wrong_writer(
    tmp_path: Path,
) -> None:
    """Probe 5: all session mechanics cross catalog/execute_action authority."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    calls: list[int] = []
    with AuthorityOwner.open(
        tmp_path / "tool-authority.db",
        "owner-tool-authority",
        disconnect_grace_seconds=1.0,
    ) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="campaign-tool-authority",
            writer=WriterRole.BUILDER,
            turn_id="turn-tool-authority",
            calls=calls,
        )
        owner.create_agent_session(
            campaign_id="campaign-tool-authority",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-tool-authority",
            selected_skill="BUILD RULESET",
        )
        entry = owner.current_package_lock("campaign-tool-authority").package_lock.entries[0]
        loop = CampaignSessionLoop(owner)

        def run_command(command: ActionCommand, suffix: str) -> object:
            def provider(_input: object) -> object:
                yield ProviderEvent.action(command)

            return loop.run(
                SessionRunRequest(
                    session_id="session-tool-authority",
                    turn_id="turn-tool-authority",
                    attempt_id=f"attempt-{suffix}",
                    message_id=f"message-{suffix}",
                    user_text=f"Synthetic action {suffix}",
                ),
                provider,
            )

        committed = ActionCommand(
            campaign_id="campaign-tool-authority",
            action_id="action-exact-once",
            idempotency_key="idem-exact-once",
            owner_id="owner-tool-authority",
            writer=WriterRole.BUILDER,
            expected_revision=0,
            turn_id="turn-tool-authority",
            package_id=entry.package_id,
            tool_name="advance",
            parameters={"steps": 1},
        )
        first = run_command(committed, "first")
        chronology_after_first = owner.snapshot("campaign-tool-authority").events
        duplicate = run_command(committed, "duplicate")
        assert first.receipts == duplicate.receipts
        assert owner.snapshot("campaign-tool-authority").events == chronology_after_first
        assert calls == [1]

        stale = replace(
            committed,
            action_id="action-stale",
            idempotency_key="idem-stale",
        )
        with pytest.raises(StaleRevision, match="stale"):
            run_command(stale, "stale")
        wrong = replace(
            committed,
            action_id="action-wrong-writer",
            idempotency_key="idem-wrong-writer",
            writer=WriterRole.DM,
            expected_revision=1,
        )
        with pytest.raises(WrongWriter, match="Command writer is not authoritative"):
            run_command(wrong, "wrong-writer")
        assert calls == [1]
        assert owner.snapshot("campaign-tool-authority").events == chronology_after_first
        print(
            "F4 PROBE 05",
            first.receipts[0].action_id,
            first.receipts[0].resulting_revision,
            "calls",
            calls,
            "stale=StaleRevision wrong=WrongWriter",
        )


def test_probe_06_pending_tool_card_becomes_one_terminal_chronological_card(
    tmp_path: Path,
) -> None:
    """Probe 6: pending projection is memory-only and resolves once to TOOL RESULT."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []
    errors: list[BaseException] = []

    def blocked(context: MechanicContext) -> MechanicOutcome:
        calls.append(int(context.parameters["steps"]))
        started.set()
        assert release.wait(5)
        return MechanicOutcome(result={"after": 1})

    database = tmp_path / "pending-card.db"
    with AuthorityOwner.open(database, "owner-pending", disconnect_grace_seconds=1.0) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="campaign-pending",
            writer=WriterRole.DM,
            turn_id="turn-pending",
            calls=calls,
            mechanic=blocked,
        )
        owner.create_agent_session(
            campaign_id="campaign-pending",
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-pending",
        )
        entry = owner.current_package_lock("campaign-pending").package_lock.entries[0]
        command = ActionCommand(
            campaign_id="campaign-pending",
            action_id="action-pending",
            idempotency_key="idem-pending",
            owner_id="owner-pending",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="turn-pending",
            package_id=entry.package_id,
            tool_name="advance",
            parameters={"steps": 1},
        )

        def run() -> None:
            try:
                CampaignSessionLoop(owner).run(
                    SessionRunRequest(
                        session_id="session-pending",
                        turn_id="turn-pending",
                        attempt_id="attempt-pending",
                        message_id="message-pending",
                        user_text="Run the pending tool.",
                    ),
                    lambda _input: iter((ProviderEvent.action(command),)),
                )
            except BaseException as error:  # pragma: no cover - thread transfer
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        assert started.wait(5)
        pending = owner.player_projection("campaign-pending")
        assert [(item.kind.value, item.provisional) for item in pending] == [("pending_tool", True)]
        release.set()
        thread.join(5)
        assert not thread.is_alive() and not errors
        terminal = owner.player_projection("campaign-pending")
        cards = [item for item in terminal if item.kind.value == "tool_card"]
        assert len(cards) == 1
        assert cards[0].sequence is not None and not cards[0].provisional
        assert all(item.kind.value != "pending_tool" for item in terminal)
        print(
            "F4 PROBE 06",
            "pending",
            [(item.kind.value, item.provisional) for item in pending],
            "terminal",
            [(item.kind.value, item.sequence) for item in terminal],
        )

    with AuthorityOwner.open(database, "owner-pending", disconnect_grace_seconds=1.0) as reopened:
        reopened_cards = [
            item
            for item in reopened.player_projection("campaign-pending")
            if item.kind.value == "tool_card"
        ]
        assert len(reopened_cards) == 1
        assert reopened_cards[0].sequence == cards[0].sequence
        assert reopened_cards[0].safe_payload == cards[0].safe_payload


def test_probe_07_stop_cancels_once_discards_prose_preserves_committed_mechanics(
    tmp_path: Path,
) -> None:
    """Probe 7: Stop is F2 interruption, not rollback of committed F1 truth."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionProviderInput,
        SessionRunRequest,
        SessionRunResult,
    )

    ready = threading.Event()
    calls: list[int] = []
    errors: list[BaseException] = []
    results: list[SessionRunResult] = []
    with AuthorityOwner.open(
        tmp_path / "stop.db", "owner-stop", disconnect_grace_seconds=1.0
    ) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="campaign-stop",
            writer=WriterRole.BUILDER,
            turn_id="turn-stop",
            calls=calls,
        )
        owner.create_agent_session(
            campaign_id="campaign-stop",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-stop",
            selected_skill="BUILD RULESET",
        )
        entry = owner.current_package_lock("campaign-stop").package_lock.entries[0]
        command = ActionCommand(
            campaign_id="campaign-stop",
            action_id="action-before-stop",
            idempotency_key="idem-before-stop",
            owner_id="owner-stop",
            writer=WriterRole.BUILDER,
            expected_revision=0,
            turn_id="turn-stop",
            package_id=entry.package_id,
            tool_name="advance",
            parameters={"steps": 3},
        )
        loop = CampaignSessionLoop(owner)

        def provider(provider_input: SessionProviderInput) -> object:
            yield ProviderEvent.action(command)
            yield ProviderEvent.safe_think("UNFINISHED-PROVISIONAL-PROSE")
            ready.set()
            assert provider_input.control.wait_cancelled(5)

        def run() -> None:
            try:
                results.append(
                    loop.run(
                        SessionRunRequest(
                            session_id="session-stop",
                            turn_id="turn-stop",
                            attempt_id="attempt-stop",
                            message_id="message-stop",
                            user_text="Run mechanics then stop narration.",
                        ),
                        provider,
                    )
                )
            except BaseException as error:  # pragma: no cover - thread transfer
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        assert ready.wait(5)
        stopped = loop.stop("session-stop")
        thread.join(5)
        assert not thread.is_alive() and not errors and len(results) == 1
        assert stopped.cancellation_count == 1
        assert stopped.interruption.failed_attempt_id == "attempt-stop"
        assert results[0].interruption == stopped.interruption
        snapshot = owner.snapshot("campaign-stop")
        assert snapshot.revision == 1 and snapshot.state["clock.tick"] == 3
        assert calls == [3]
        assert "think" not in tuple(event.event_type.value for event in snapshot.events)
        assert "UNFINISHED-PROVISIONAL-PROSE" not in str(owner.player_projection("campaign-stop"))
        assert owner.session_context("session-stop").session.status.value == "stopped"
        print(
            "F4 PROBE 07",
            stopped.interruption.interruption_id,
            "cancel_count",
            stopped.cancellation_count,
            "revision",
            snapshot.revision,
            "status stopped",
        )


def test_probe_07b_stop_session_fences_pending_provider_admission(
    tmp_path: Path,
) -> None:
    """Stop Session wins after work admission but before attempt/provider start."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        SessionProviderInput,
        SessionRunRequest,
    )

    admitted = threading.Event()
    resume = threading.Event()
    provider_calls: list[str] = []
    errors: list[BaseException] = []
    with AuthorityOwner.open(
        tmp_path / "stop-pending.db",
        "owner-stop-pending",
        disconnect_grace_seconds=1.0,
    ) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="campaign-stop-pending",
            writer=WriterRole.BUILDER,
            turn_id="turn-stop-pending",
            calls=[],
        )
        owner.create_agent_session(
            campaign_id="campaign-stop-pending",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-stop-pending",
            selected_skill="BUILD RULESET",
        )
        loop = CampaignSessionLoop(owner)
        original = loop._run_with_session_work

        def paused_run(request: object, provider: object, context: object) -> object:
            admitted.set()
            assert resume.wait(5)
            return original(request, provider, context)  # type: ignore[arg-type]

        loop._run_with_session_work = paused_run  # type: ignore[method-assign]

        def provider(_provider_input: SessionProviderInput) -> object:
            provider_calls.append("called")
            return ()

        def run() -> None:
            try:
                loop.run(
                    SessionRunRequest(
                        session_id="session-stop-pending",
                        turn_id="turn-stop-pending",
                        attempt_id="attempt-stop-pending",
                        message_id="message-stop-pending",
                        user_text="Do not reach the provider.",
                    ),
                    provider,
                )
            except BaseException as error:  # pragma: no cover - thread transfer
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        assert admitted.wait(5)
        stopped = loop.stop_agent_session("session-stop-pending")
        resume.set()
        thread.join(5)

        assert not thread.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], CommandRejected)
        assert provider_calls == []
        assert stopped.status.value == "stopped"
        assert owner.session_context("session-stop-pending").session.status.value == "stopped"
        print(
            "F4/F6 PROBE 07B",
            "pending admission fenced",
            "provider calls",
            len(provider_calls),
            "status",
            stopped.status.value,
        )


def test_probe_07c_stop_fences_active_run_before_owner_attempt_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop wins after active publication but before owner attempt creation."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionProviderInput,
        SessionRunRequest,
        SessionRunResult,
    )

    entered_attempt_start = threading.Event()
    resume_attempt_start = threading.Event()
    provider_calls: list[str] = []
    results: list[SessionRunResult] = []
    errors: list[BaseException] = []
    with AuthorityOwner.open(
        tmp_path / "stop-pre-attempt.db",
        "owner-stop-pre-attempt",
        disconnect_grace_seconds=1.0,
    ) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="campaign-stop-pre-attempt",
            writer=WriterRole.BUILDER,
            turn_id="turn-stop-pre-attempt",
            calls=[],
        )
        owner.create_agent_session(
            campaign_id="campaign-stop-pre-attempt",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-stop-pre-attempt",
            selected_skill="BUILD RULESET",
        )
        loop = CampaignSessionLoop(owner)
        original_start = owner.start_narration_attempt

        def paused_start(**kwargs: object) -> None:
            entered_attempt_start.set()
            assert resume_attempt_start.wait(5)
            original_start(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(owner, "start_narration_attempt", paused_start)

        def provider(_provider_input: SessionProviderInput) -> object:
            provider_calls.append("called")
            yield ProviderEvent.talk("POST-STOP-PROVIDER-OUTPUT")

        def run() -> None:
            try:
                results.append(
                    loop.run(
                        SessionRunRequest(
                            session_id="session-stop-pre-attempt",
                            turn_id="turn-stop-pre-attempt",
                            attempt_id="attempt-stop-pre-attempt",
                            message_id="message-stop-pre-attempt",
                            user_text="Stop before provider admission.",
                        ),
                        provider,
                    )
                )
            except BaseException as error:  # pragma: no cover - thread transfer
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        assert entered_attempt_start.wait(5)
        stopped = loop.stop_agent_session("session-stop-pre-attempt")
        resume_attempt_start.set()
        thread.join(5)

        assert not thread.is_alive()
        assert errors == [] and len(results) == 1
        assert provider_calls == []
        assert results[0].interruption is not None
        assert results[0].interruption.failed_attempt_id == "attempt-stop-pre-attempt"
        assert stopped.status.value == "stopped"
        assert owner.session_context("session-stop-pre-attempt").session.status.value == "stopped"
        assert "POST-STOP-PROVIDER-OUTPUT" not in str(
            owner.player_projection("campaign-stop-pre-attempt")
        )
        replayed_interruption = owner.interrupt_narration(
            "campaign-stop-pre-attempt",
            "attempt-stop-pre-attempt",
        )
        assert replayed_interruption == results[0].interruption
        print(
            "F4/F6 PROBE 07C",
            "pre-attempt admission fenced",
            "provider calls",
            len(provider_calls),
            "status",
            stopped.status.value,
        )


def test_probe_07d_stop_fences_final_provider_callable_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop after the final loop check must prevent even entering the provider callable."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderControl,
        SessionRunRequest,
    )

    database = tmp_path / "stop-final-admission.db"
    admission_waiting = threading.Event()
    release_admission = threading.Event()
    provider_calls: list[str] = []
    results: list[object] = []
    errors: list[BaseException] = []

    original_admit = ProviderControl.admit

    def paused_admit(
        control: ProviderControl,
        start: Callable[[], object],
    ) -> object:
        admission_waiting.set()
        assert release_admission.wait(5)
        return original_admit(control, start)

    monkeypatch.setattr(ProviderControl, "admit", paused_admit)

    with AuthorityOwner.open(
        database,
        "owner-stop-final-admission",
        disconnect_grace_seconds=1.0,
    ) as owner:
        owner.create_campaign(
            CampaignCreate(
                campaign_id="campaign-stop-final-admission",
                display_name="Stop final provider admission",
                writer=WriterRole.DM,
                package_lock=PackageLock(()),
                turn_id="turn-stop-final-admission",
            )
        )
        owner.create_agent_session(
            campaign_id="campaign-stop-final-admission",
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-stop-final-admission",
        )
        loop = CampaignSessionLoop(owner)

        def provider(_input: object) -> tuple[()]:
            provider_calls.append("called")
            return ()

        def run() -> None:
            try:
                results.append(
                    loop.run(
                        SessionRunRequest(
                            session_id="session-stop-final-admission",
                            turn_id="turn-stop-final-admission",
                            attempt_id="attempt-stop-final-admission",
                            message_id="message-stop-final-admission",
                            user_text="Stop at final provider admission.",
                        ),
                        provider,
                    )
                )
            except BaseException as error:  # pragma: no cover - thread transfer
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        if not admission_waiting.wait(5):
            release_admission.set()
            thread.join(5)
            pytest.fail(f"Provider admission was not reached; worker errors={errors!r}")
        stopped = loop.stop_agent_session("session-stop-final-admission")
        release_admission.set()
        thread.join(5)
        assert not thread.is_alive()
        assert stopped.status.value == "stopped"
        assert provider_calls == []
        assert errors == []
        assert len(results) == 1
        assert (
            owner.session_context("session-stop-final-admission").session.status.value == "stopped"
        )
        projection = owner.player_projection("campaign-stop-final-admission")
        assert len(projection) == 1
        assert projection[0].kind.value == "retry"
        assert "POST-STOP-PROVIDER-OUTPUT" not in str(projection)
        print(
            "F4/F6 PROBE 07D",
            "final callable admission fenced",
            "provider calls",
            len(provider_calls),
            "status",
            stopped.status.value,
        )


def test_probe_08_disconnect_grace_expiry_has_no_automatic_retry_or_fallback(
    tmp_path: Path,
) -> None:
    """Probe 8: shared sessions expose exact F2 disconnect/reconnect/expiry."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionProviderInput,
        SessionRunRequest,
        SessionRunResult,
    )

    ready = threading.Event()
    provider_calls: list[str] = []
    errors: list[BaseException] = []
    results: list[SessionRunResult] = []
    with AuthorityOwner.open(
        tmp_path / "disconnect.db",
        "owner-disconnect",
        disconnect_grace_seconds=2.0,
    ) as owner:
        _campaign(
            owner,
            campaign_id="campaign-disconnect",
            writer=WriterRole.DM,
            turn_id="turn-disconnect",
        )
        owner.create_agent_session(
            campaign_id="campaign-disconnect",
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-disconnect",
        )
        loop = CampaignSessionLoop(owner)

        def provider(provider_input: SessionProviderInput) -> object:
            provider_calls.append("called")
            yield ProviderEvent.safe_think("Unfinished disconnected prose")
            ready.set()
            assert provider_input.control.wait_cancelled(5)

        def run() -> None:
            try:
                results.append(
                    loop.run(
                        SessionRunRequest(
                            session_id="session-disconnect",
                            turn_id="turn-disconnect",
                            attempt_id="attempt-disconnect",
                            message_id="message-disconnect",
                            user_text="Start then disconnect.",
                        ),
                        provider,
                    )
                )
            except BaseException as error:  # pragma: no cover - thread transfer
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        assert ready.wait(5)
        assert loop.disconnect("session-disconnect", now=10.0) == 12.0
        replay = loop.reconnect("session-disconnect", now=11.0, acknowledged_sequence=0)
        assert replay.canonical == () and replay.high_water == 0
        assert loop.disconnect("session-disconnect", now=20.0) == 22.0
        expired = loop.expire("session-disconnect", now=22.0)
        thread.join(5)
        assert not thread.is_alive() and not errors and len(results) == 1
        assert expired.cancellation_count == 1
        assert results[0].interruption == expired.interruption
        assert provider_calls == ["called"]
        assert owner.snapshot("campaign-disconnect").events == ()
        retries = [
            item
            for item in owner.player_projection("campaign-disconnect")
            if item.kind.value == "retry"
        ]
        assert len(retries) == 1
        print(
            "F4 PROBE 08",
            "deadline 12.0/22.0",
            expired.interruption.retryable,
            "provider_calls",
            len(provider_calls),
            "retry_cards",
            len(retries),
        )


def test_retry_first_mechanic_keeps_session_reference_after_replay_and_reopen(
    tmp_path: Path,
) -> None:
    """A mechanic first executed during Retry must survive in saved conversation history."""
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, ProviderEvent, SessionRunRequest

    database = tmp_path / "fresh-retry.db"
    calls: list[int] = []
    with AuthorityOwner.open(database, "owner", disconnect_grace_seconds=1.0) as owner:
        _clockwork_campaign(owner, campaign_id="campaign", writer=WriterRole.DM,
                            turn_id="turn", calls=calls)
        owner.create_agent_session(campaign_id="campaign", role=WriterRole.DM,
                                   provider_id="synthetic", model_id="synthetic", session_id="gm")
        loop = CampaignSessionLoop(owner)

        def fail_before_action(_input):
            raise RuntimeError("Synthetic failure before mechanics")
            yield

        with pytest.raises(RuntimeError, match="before mechanics"):
            loop.run(SessionRunRequest(session_id="gm", turn_id="turn", attempt_id="first",
                                       message_id="user", user_text="Advance the clock."),
                     fail_before_action)
        entry = owner.current_package_lock("campaign").package_lock.entries[0]

        def recovered(_input):
            yield ProviderEvent.action(ActionCommand(
                campaign_id="campaign", action_id="recovered-action", idempotency_key="recovered",
                owner_id="owner", writer=WriterRole.DM, expected_revision=0, turn_id="turn",
                package_id=entry.package_id, tool_name="advance", parameters={"steps": 4},
            ))
            yield ProviderEvent.talk("The clock advanced to four.")

        for _ in range(2):
            loop.retry("gm", retry_id="retry", narrator=lambda *_: None,
                       provider_factory=lambda _: recovered)
        assert calls == [4]
        result = next(event for event in owner.snapshot("campaign").events
                      if event.event_type.value == "tool_result")
        references = [
            message.canonical_sequence for message in owner.session_context("gm").messages
        ]
        assert references.count(result.sequence) == 1
    with AuthorityOwner.open(database, "reopened") as owner:
        references = [
            message.canonical_sequence for message in owner.session_context("gm").messages
        ]
        assert references.count(result.sequence) == 1
        assert calls == [4]


def test_probe_09_manual_retry_narrates_stored_result_without_rerunning_mechanic(
    tmp_path: Path,
) -> None:
    """Probe 9: narration failure preserves action and Retry consumes its receipt."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    calls: list[int] = []
    narrator_inputs: list[RetryNarrationInput] = []
    with AuthorityOwner.open(
        tmp_path / "retry.db", "owner-retry", disconnect_grace_seconds=1.0
    ) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="campaign-retry",
            writer=WriterRole.DM,
            turn_id="turn-retry",
            calls=calls,
        )
        owner.create_agent_session(
            campaign_id="campaign-retry",
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-retry",
        )
        entry = owner.current_package_lock("campaign-retry").package_lock.entries[0]
        command = ActionCommand(
            campaign_id="campaign-retry",
            action_id="action-retry-source",
            idempotency_key="idem-retry-source",
            owner_id="owner-retry",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="turn-retry",
            package_id=entry.package_id,
            tool_name="advance",
            parameters={"steps": 4},
        )
        loop = CampaignSessionLoop(owner)

        def failing_provider(_input: object) -> object:
            yield ProviderEvent.action(command)
            raise RuntimeError("synthetic narration failure")

        with pytest.raises(RuntimeError, match="synthetic narration failure"):
            loop.run(
                SessionRunRequest(
                    session_id="session-retry",
                    turn_id="turn-retry",
                    attempt_id="attempt-retry-source",
                    message_id="message-retry-source",
                    user_text="Commit then fail narration.",
                ),
                failing_provider,
            )
        assert calls == [4] and owner.snapshot("campaign-retry").revision == 1

        def narrator(retry_input: RetryNarrationInput, stream: NarrationStream) -> None:
            narrator_inputs.append(retry_input)
            assert retry_input.action_receipt is not None
            assert retry_input.action_receipt.action_id == "action-retry-source"
            assert retry_input.action_receipt.result["after"] == 4
            stream.emit(ProseChannel.TALK, "Stored result: the clock advanced to four.")

        retried = loop.retry("session-retry", retry_id="retry-explicit", narrator=narrator)
        replay = loop.retry("session-retry", retry_id="retry-explicit", narrator=narrator)
        assert calls == [4]
        assert len(narrator_inputs) == 1
        assert not retried.replayed and replay.replayed
        assert tuple(event.event_type.value for event in retried.canonical) == ("talk",)
        assert retried.canonical == replay.canonical

        def next_provider(provider_input: object) -> object:
            latest = provider_input.history.messages[-1]  # type: ignore[attr-defined]
            assert latest.content["attempt_id"] == "attempt-after-retry"
            yield ProviderEvent.talk("Chloe answers the new question.")

        next_result = loop.run(
            SessionRunRequest(
                session_id="session-retry",
                turn_id="turn-retry",
                attempt_id="attempt-after-retry",
                message_id="message-after-retry",
                user_text="What does Chloe think changed?",
            ),
            next_provider,  # type: ignore[arg-type]
        )
        assert [event.safe_payload.get("text") for event in next_result.canonical] == [
            "Chloe answers the new question."
        ]
        print(
            "F4 PROBE 09",
            command.action_id,
            "mechanic_calls",
            calls,
            "retry",
            retried.input.retry_id,
            "replayed",
            replay.replayed,
        )


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("reopen", [False, True])
def test_stopped_gm_redirects_without_repeating_committed_actions(
    tmp_path: Path, committed: bool, reopen: bool,
) -> None:
    """Stop accepts fresh guidance and a further interrupted continuation keeps that basis."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop, ProviderEvent, SessionProviderInput, SessionRunRequest,
    )

    db = tmp_path / "redirect.db"
    calls: list[int] = []
    owner = AuthorityOwner.open(db, "redirect-owner", disconnect_grace_seconds=1.0)
    try:
        _clockwork_campaign(owner, campaign_id="redirect", writer=WriterRole.DM,
                            turn_id="turn", calls=calls)
        owner.create_agent_session(campaign_id="redirect", role=WriterRole.DM,
                                   provider_id="synthetic", model_id="synthetic", session_id="gm")
        entry = owner.current_package_lock("redirect").package_lock.entries[0]
        action = ActionCommand(
            campaign_id="redirect", action_id="first-action", idempotency_key="first-action",
            owner_id="redirect-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="turn", package_id=entry.package_id, tool_name="advance",
            parameters={"steps": 4},
        )
        loop = CampaignSessionLoop(owner)

        def stopped_provider(provider_input: SessionProviderInput):
            if committed:
                yield ProviderEvent.action(action)
            owner.interrupt_narration("redirect", "original")
            assert provider_input.control.cancelled
            yield ProviderEvent.talk("Unfinished prose must not survive.")

        result = loop.run(SessionRunRequest("gm", "turn", "original", "input", "Advance four"),
                          stopped_provider)
        assert result.interruption is not None
        interruption_id = result.interruption.interruption_id
        if reopen:
            owner.close()
            owner = AuthorityOwner.open(db, "redirect-owner", disconnect_grace_seconds=1.0)
            loop = CampaignSessionLoop(owner)
        projection = next(item for item in owner.player_projection("redirect")
                          if item.kind.value == "retry")
        assert projection.safe_payload["can_redirect"] is True

        def redirected_provider(provider_input: SessionProviderInput):
            assert provider_input.basis_message_id == "replacement-input"
            assert provider_input.history.messages[-1].content["ooc_text"] == "Keep it brief"
            assert (provider_input.retry_action_receipt is not None) is committed
            if committed:
                assert provider_input.retry_action_receipt.result["after"] == 4
            owner.interrupt_narration("redirect", "replacement")
            yield ProviderEvent.talk("Also unfinished.")

        loop.run(SessionRunRequest(
            "gm", "turn", "replacement", "replacement-input", "Talk instead", "Keep it brief",
            redirect_interruption_id=interruption_id,
        ), redirected_provider)
        assert calls == ([4] if committed else [])
        assert owner.snapshot("redirect").revision == (1 if committed else 0)
        owner.close()
        owner = AuthorityOwner.open(db, "redirect-owner", disconnect_grace_seconds=1.0)
        loop = CampaignSessionLoop(owner)

        def retry_provider(provider_input: SessionProviderInput):
            assert provider_input.basis_message_id == "replacement-input"
            assert any(message.content.get("text") == "Talk instead"
                       for message in provider_input.history.messages)
            yield ProviderEvent.talk("The saved outcome is retained; we talk.")

        def no_narrator(*_args):
            pytest.fail("A replacement message must use its updated tool-capable context")

        retried = loop.retry("gm", retry_id="finish", interruption_id=interruption_id,
                             narrator=no_narrator, provider_factory=lambda _turn: retry_provider)
        assert not retried.replayed
        assert calls == ([4] if committed else [])
        assert owner.snapshot("redirect").revision == (1 if committed else 0)
        assert not any(item.kind.value == "retry" for item in owner.player_projection("redirect"))
        with pytest.raises(IdempotencyConflict):
            loop.retry("gm", retry_id="stale-retry", interruption_id=interruption_id,
                       narrator=no_narrator)
    finally:
        owner.close()


@pytest.mark.parametrize("stop_before_provider", [False, True])
def test_redirect_recovery_keeps_new_input_and_later_consequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop_before_provider: bool,
) -> None:
    """Durable acceptance and later committed consequences both survive restart."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop, ProviderEvent, SessionProviderInput, SessionRunRequest,
    )

    db = tmp_path / "redirect-recovery.db"
    calls: list[int] = []
    with AuthorityOwner.open(db, "owner", disconnect_grace_seconds=1.0) as owner:
        _clockwork_campaign(owner, campaign_id="campaign", writer=WriterRole.DM,
                            turn_id="turn", calls=calls)
        owner.create_agent_session(campaign_id="campaign", role=WriterRole.DM,
                                   provider_id="synthetic", model_id="synthetic", session_id="gm")
        entry = owner.current_package_lock("campaign").package_lock.entries[0]
        action = ActionCommand(
            campaign_id="campaign", action_id="first", idempotency_key="first",
            owner_id="owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="turn", package_id=entry.package_id, tool_name="advance",
            parameters={"steps": 4},
        )
        loop = CampaignSessionLoop(owner)

        def original(_input: SessionProviderInput):
            yield ProviderEvent.action(action)
            owner.interrupt_narration("campaign", "first-attempt")
            yield ProviderEvent.talk("Interrupted")

        result = loop.run(SessionRunRequest("gm", "turn", "first-attempt", "first-input", "Act"),
                          original)
        assert result.interruption is not None
        interruption_id = result.interruption.interruption_id

        def continuation(_input: SessionProviderInput):
            yield ProviderEvent.action(replace(action, action_id="consequence", expected_revision=1,
                                               idempotency_key="consequence", parameters={"steps": 2}))
            owner.interrupt_narration("campaign", "redirect-attempt")
            yield ProviderEvent.talk("Interrupted again")

        request = SessionRunRequest(
            "gm", "turn", "redirect-attempt", "replacement", "Explain briefly",
            redirect_interruption_id=interruption_id,
        )
        if stop_before_provider:
            def process_loss(*_args):
                raise RuntimeError("Synthetic process loss after durable acceptance")
            monkeypatch.setattr(loop, "_run_dm_provider", process_loss)
            with pytest.raises(RuntimeError, match="Synthetic process loss"):
                loop.run(request, continuation)
        else:
            loop.run(request, continuation)
    with AuthorityOwner.open(db, "owner", disconnect_grace_seconds=1.0) as owner:
        loop = CampaignSessionLoop(owner)

        def recovered(provider_input: SessionProviderInput):
            assert provider_input.basis_message_id == "replacement"
            assert provider_input.retry_action_receipt is not None
            assert provider_input.retry_action_receipt.result["after"] == (
                4 if stop_before_provider else 6
            )
            yield ProviderEvent.talk("Brief explanation of saved events.")

        def no_narrator(*_args):
            pytest.fail("Recovery must keep the accepted new input")

        loop.retry("gm", retry_id="redirect-attempt" if stop_before_provider else "final",
                   interruption_id=interruption_id, narrator=no_narrator,
                   provider_factory=lambda _turn: recovered)
        assert calls == ([4] if stop_before_provider else [4, 2])
        assert owner.snapshot("campaign").revision == (1 if stop_before_provider else 2)


def test_post_mechanics_retry_reuses_result_and_resettles_campaign_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider failure after mechanics resumes the same GM tool loop before prose."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    calls: list[int] = []
    writes: list[dict[str, object]] = []
    with AuthorityOwner.open(
        tmp_path / "post-mechanics-retry.db",
        "owner-post-mechanics-retry",
        disconnect_grace_seconds=1.0,
    ) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="campaign-post-mechanics-retry",
            writer=WriterRole.DM,
            turn_id="turn-post-mechanics-retry",
            calls=calls,
        )
        owner.create_agent_session(
            campaign_id="campaign-post-mechanics-retry",
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-post-mechanics-retry",
        )
        loop = CampaignSessionLoop(owner)
        entry = owner.current_package_lock(
            "campaign-post-mechanics-retry"
        ).package_lock.entries[0]
        action = ActionCommand(
            campaign_id="campaign-post-mechanics-retry",
            action_id="action-post-mechanics-retry",
            idempotency_key="idem-post-mechanics-retry",
            owner_id="owner-post-mechanics-retry",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="turn-post-mechanics-retry",
            package_id=entry.package_id,
            tool_name="advance",
            parameters={"steps": 4},
        )
        request = SessionRunRequest(
            session_id="session-post-mechanics-retry",
            turn_id="turn-post-mechanics-retry",
            attempt_id="attempt-post-mechanics-retry",
            message_id="message-post-mechanics-retry",
            user_text="Advance the clock by four.",
        )

        def failing_provider(_provider_input: object) -> object:
            yield ProviderEvent.action(action)
            raise ProviderConnectionError("synthetic post-mechanics provider loss")

        with pytest.raises(ProviderConnectionError):
            loop.run(request, failing_provider)  # type: ignore[arg-type]

        interruption = owner.retryable_interruption(
            "campaign-post-mechanics-retry",
            failed_attempt_id=request.attempt_id,
        )
        assert interruption is not None
        terminal = next(
            message
            for message in owner.session_context(request.session_id).messages
            if message.content.get("kind") == "session_attempt_terminal"
        )
        assert terminal.content["recovery_kind"] == "post_mechanics_narration"
        assert calls == [4]

        def capture_narrative_write(
            campaign_id: str,
            **kwargs: object,
        ) -> dict[str, object]:
            writes.append({"campaign_id": campaign_id, **kwargs})
            return {"status": "campaign_file_updated", "resulting_revision": 1}

        monkeypatch.setattr(
            loop._mechanics,  # noqa: SLF001 - exact Retry Campaign File boundary
            "commit_campaign_file",
            capture_narrative_write,
        )
        observed_receipts: list[ActionReceipt | None] = []
        observed_recovery_kinds: list[str | None] = []

        def provider_factory(turn_id: str) -> Callable[[object], object]:
            assert turn_id == request.turn_id

            def provider(provider_input: object) -> object:
                observed_receipts.append(  # type: ignore[attr-defined]
                    provider_input.retry_action_receipt
                )
                observed_recovery_kinds.append(  # type: ignore[attr-defined]
                    provider_input.recovery_kind
                )
                narrative_writer = provider_input.narrative_writer  # type: ignore[attr-defined]
                assert callable(narrative_writer)
                narrative_writer(
                    {
                        "facts": [
                            {
                                "fact_id": "clock-now-four",
                                "category": "world_state",
                                "text": "The clock is now at four.",
                                "visibility": "gm_private",
                                "known_by": ["gm"],
                            }
                        ]
                    }
                )
                yield ProviderEvent.talk("The clock settles on four.")

            return provider

        def narration_only_must_not_run(
            _retry_input: RetryNarrationInput,
            _stream: NarrationStream,
        ) -> None:
            raise AssertionError("post-mechanics Retry used narration-only recovery")

        result = loop.retry(
            request.session_id,
            interruption_id=interruption.interruption_id,
            retry_id="retry-post-mechanics",
            narrator=narration_only_must_not_run,
            provider_factory=provider_factory,  # type: ignore[arg-type]
        )

        assert calls == [4]
        assert observed_recovery_kinds == ["post_mechanics_narration"]
        assert len(observed_receipts) == 1
        assert observed_receipts[0] is not None
        assert observed_receipts[0].action_id == action.action_id
        assert observed_receipts[0].result["after"] == 4
        assert writes == [
            {
                "campaign_id": "campaign-post-mechanics-retry",
                "session_id": request.session_id,
                "basis_message_id": request.message_id,
                "parameters": {
                    "facts": [
                        {
                            "fact_id": "clock-now-four",
                            "category": "world_state",
                            "text": "The clock is now at four.",
                            "visibility": "gm_private",
                            "known_by": ["gm"],
                        }
                    ]
                },
                "turn_id": request.turn_id,
            }
        ]
        assert [event.safe_payload.get("text") for event in result.canonical] == [
            "The clock settles on four."
        ]


def test_probe_09b_stop_fences_pending_retry_provider_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop Session wins after Retry work admission but before provider admission."""

    from sylvia_gm.agent._session_loop import CampaignSessionLoop
    from sylvia_gm.domain.errors import CommandRejected

    entered_retry = threading.Event()
    resume_retry = threading.Event()
    narrator_calls: list[str] = []
    errors: list[BaseException] = []
    with AuthorityOwner.open(
        tmp_path / "pending-retry-stop.db",
        "owner-pending-retry-stop",
        disconnect_grace_seconds=1.0,
    ) as owner:
        _campaign(
            owner,
            campaign_id="campaign-pending-retry-stop",
            writer=WriterRole.DM,
            turn_id="turn-pending-retry-stop",
        )
        session = owner.create_agent_session(
            campaign_id="campaign-pending-retry-stop",
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-pending-retry-stop",
        )
        owner.append_agent_message(
            session_id=session.session_id,
            message_id="message-pending-retry-source",
            kind=SessionMessageKind.EVIDENCE,
            content={
                "kind": "campaign_start",
                "event": "campaign_start",
                "input_id": "attempt-pending-retry-source",
                "attempt_id": "attempt-pending-retry-source",
                "turn_id": "turn-pending-retry-stop",
                "input_kind": "campaign_start",
            },
        )
        owner.start_narration_attempt(
            campaign_id=session.campaign_id,
            attempt_id="attempt-pending-retry-source",
            turn_id="turn-pending-retry-stop",
            cancel=lambda: None,
        )
        source = owner.interrupt_narration(
            session.campaign_id,
            "attempt-pending-retry-source",
        )
        loop = CampaignSessionLoop(owner)
        original_manual_retry = owner.manual_retry

        def paused_manual_retry(**kwargs: object) -> object:
            entered_retry.set()
            assert resume_retry.wait(2.0)
            return original_manual_retry(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(owner, "manual_retry", paused_manual_retry)

        def narrator(_input: RetryNarrationInput, _stream: NarrationStream) -> None:
            narrator_calls.append("called")

        def run_retry() -> None:
            try:
                loop.retry(
                    session.session_id,
                    interruption_id=source.interruption_id,
                    retry_id="retry-pending-stop",
                    narrator=narrator,
                )
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=run_retry, daemon=True)
        worker.start()
        assert entered_retry.wait(2.0)
        stopped = loop.stop_agent_session(session.session_id)
        resume_retry.set()
        worker.join(2.0)

        assert not worker.is_alive()
        assert stopped.status.value == "stopped"
        assert narrator_calls == []
        assert len(errors) == 1
        assert isinstance(errors[0], CommandRejected)
        retry_basis = owner.retryable_interruption(
            session.campaign_id,
            failed_attempt_id="retry-pending-stop",
        )
        assert retry_basis is not None
        retry_terminal = next(
            message
            for message in reversed(owner.session_context(session.session_id).messages)
            if message.content.get("attempt_id") == "retry-pending-stop"
        )
        assert retry_terminal.content["failure_category"] == "user_cancelled"
        assert retry_terminal.content["activity_timing"]["phase"] == "stopped"
        assert retry_terminal.content["activity_timing"]["elapsed_ms"] >= 0
        print(
            "F4 PROBE 09B pending Retry fenced",
            "provider_calls",
            len(narrator_calls),
            "status",
            stopped.status.value,
            "retryable",
            retry_basis.failed_attempt_id,
        )


def test_player_input_identity_and_provider_failure_basis_are_durable(
    tmp_path: Path,
) -> None:
    """One accepted input identity owns its pending projection and recoverable terminal."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        SessionRunRequest,
    )

    with AuthorityOwner.open(
        tmp_path / "input-identity.db",
        "owner-input-identity",
        disconnect_grace_seconds=1.0,
    ) as owner:
        _campaign(
            owner,
            campaign_id="campaign-input-identity",
            writer=WriterRole.DM,
            turn_id="turn-input-identity",
        )
        owner.create_agent_session(
            campaign_id="campaign-input-identity",
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-input-identity",
        )
        loop = CampaignSessionLoop(owner)
        request = SessionRunRequest(
            session_id="session-input-identity",
            turn_id="turn-input-identity",
            attempt_id="input-exact-player-action",
            message_id="message-exact-player-action",
            user_text="Daniel asks Chloe about the Warden stone.",
        )

        def failed_provider(provider_input: object) -> object:
            history = provider_input.history  # type: ignore[attr-defined]
            accepted = history.messages[-1]
            assert accepted.content["input_id"] == request.attempt_id
            assert accepted.content["attempt_id"] == request.attempt_id
            assert accepted.content["turn_id"] == request.turn_id
            raise ProviderConnectionError("synthetic connection loss")
            yield  # pragma: no cover

        with pytest.raises(ProviderConnectionError, match="synthetic connection loss"):
            loop.run(request, failed_provider)  # type: ignore[arg-type]

        messages = owner.session_context(request.session_id).messages
        accepted = next(message for message in messages if message.kind.value == "user")
        terminal = next(
            message
            for message in messages
            if message.content.get("kind") == "session_attempt_terminal"
        )
        assert accepted.content == {
            "text": request.user_text,
            "canonical_start_sequence": 0,
            "input_id": request.attempt_id,
            "attempt_id": request.attempt_id,
            "turn_id": request.turn_id,
            "input_kind": "user",
        }
        assert terminal.content["activity_timing"]["phase"] == "failed"
        assert terminal.content["activity_timing"]["elapsed_ms"] >= 0
        assert terminal.content == {
            "activity_timing": terminal.content["activity_timing"],
            "kind": "session_attempt_terminal",
            "input_id": request.attempt_id,
            "attempt_id": request.attempt_id,
            "message_id": request.message_id,
            "turn_id": request.turn_id,
            "status": "interrupted",
            "failure_category": "provider_unavailable",
            "recovery_kind": "uncommitted_player_action",
            "committed_action_id": None,
            "retryable": True,
        }
        basis = owner.retryable_interruption(
            "campaign-input-identity",
            failed_attempt_id=request.attempt_id,
        )
        assert basis is not None


def test_uncommitted_player_retry_reuses_exact_input_and_may_commit_mechanics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-commit failure retries the same input through the normal tool-capable loop."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    calls: list[int] = []
    with AuthorityOwner.open(
        tmp_path / "uncommitted-retry.db",
        "owner-uncommitted-retry",
        disconnect_grace_seconds=1.0,
    ) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="campaign-uncommitted-retry",
            writer=WriterRole.DM,
            turn_id="turn-uncommitted-retry",
            calls=calls,
        )
        owner.create_agent_session(
            campaign_id="campaign-uncommitted-retry",
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-uncommitted-retry",
        )
        loop = CampaignSessionLoop(owner)
        request = SessionRunRequest(
            session_id="session-uncommitted-retry",
            turn_id="turn-uncommitted-retry",
            attempt_id="input-uncommitted-retry",
            message_id="message-uncommitted-retry",
            user_text="Advance the clock by three.",
        )

        def first_failure(_provider_input: object) -> object:
            raise ProviderConnectionError("synthetic first-attempt loss")
            yield  # pragma: no cover

        with pytest.raises(ProviderConnectionError):
            loop.run(request, first_failure)  # type: ignore[arg-type]
        interruption = owner.retryable_interruption(
            "campaign-uncommitted-retry",
            failed_attempt_id=request.attempt_id,
        )
        assert interruption is not None
        entry = owner.current_package_lock("campaign-uncommitted-retry").package_lock.entries[0]
        action = ActionCommand(
            campaign_id="campaign-uncommitted-retry",
            action_id="action-uncommitted-retry",
            idempotency_key="idem-uncommitted-retry",
            owner_id="owner-uncommitted-retry",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="turn-uncommitted-retry",
            package_id=entry.package_id,
            tool_name="advance",
            parameters={"steps": 3},
        )
        observed_inputs: list[str] = []
        narrative_writes: list[dict[str, object]] = []

        def capture_narrative_write(
            campaign_id: str,
            **kwargs: object,
        ) -> dict[str, object]:
            narrative_writes.append({"campaign_id": campaign_id, **kwargs})
            return {"status": "campaign_file_updated", "resulting_revision": 0}

        monkeypatch.setattr(
            loop._mechanics,  # noqa: SLF001 - exact Retry Campaign State boundary
            "commit_campaign_file",
            capture_narrative_write,
        )

        def provider_factory(turn_id: str) -> Callable[[object], object]:
            assert turn_id == request.turn_id

            def provider(provider_input: object) -> object:
                history = provider_input.history  # type: ignore[attr-defined]
                observed_inputs.append(str(history.messages[-1].content["text"]))
                narrative_writer = provider_input.narrative_writer  # type: ignore[attr-defined]
                assert callable(narrative_writer)
                narrative_writer(
                    {
                        "facts": [
                            {
                                "fact_id": "clock-request-pending",
                                "category": "intent",
                                "text": "The clock request remains pending.",
                                "visibility": "gm_private",
                                "known_by": ["gm"],
                            }
                        ]
                    }
                )
                yield ProviderEvent.action(action)
                yield ProviderEvent.talk("The clock advances to three.")

            return provider

        def narration_only_must_not_run(
            _retry_input: RetryNarrationInput,
            _stream: NarrationStream,
        ) -> None:
            raise AssertionError("uncommitted player retry used narration-only mode")

        result = loop.retry(
            request.session_id,
            interruption_id=interruption.interruption_id,
            retry_id="retry-uncommitted-player-action",
            narrator=narration_only_must_not_run,
            provider_factory=provider_factory,  # type: ignore[arg-type]
        )

        assert observed_inputs == [request.user_text]
        assert narrative_writes == [
            {
                "campaign_id": "campaign-uncommitted-retry",
                "session_id": "session-uncommitted-retry",
                "basis_message_id": "message-uncommitted-retry",
                "parameters": {
                    "facts": [
                        {
                            "fact_id": "clock-request-pending",
                            "category": "intent",
                            "text": "The clock request remains pending.",
                            "visibility": "gm_private",
                            "known_by": ["gm"],
                        }
                    ]
                },
                "turn_id": "turn-uncommitted-retry",
            }
        ]
        assert calls == [3]
        assert owner.snapshot("campaign-uncommitted-retry").revision == 1
        assert [event.safe_payload.get("text") for event in result.canonical] == [
            "The clock advances to three."
        ]


def test_campaign_file_checkpoint_retry_keeps_tools_and_does_not_rerun_mechanics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry resumes the held-prose GM-screen checkpoint, not narration-only recovery."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    calls: list[int] = []
    held_prose = "You reach the far arch while the clock settles on three."
    with AuthorityOwner.open(
        tmp_path / "campaign-file-checkpoint-retry.db",
        "owner-campaign-file-checkpoint-retry",
        disconnect_grace_seconds=1.0,
    ) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="campaign-checkpoint-retry",
            writer=WriterRole.DM,
            turn_id="turn-checkpoint-retry",
            calls=calls,
        )
        owner.create_agent_session(
            campaign_id="campaign-checkpoint-retry",
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-checkpoint-retry",
        )
        loop = CampaignSessionLoop(owner)
        request = SessionRunRequest(
            session_id="session-checkpoint-retry",
            turn_id="turn-checkpoint-retry",
            attempt_id="input-checkpoint-retry",
            message_id="message-checkpoint-retry",
            user_text="Advance the clock and move through the arch.",
        )
        entry = owner.current_package_lock(
            "campaign-checkpoint-retry"
        ).package_lock.entries[0]
        action = ActionCommand(
            campaign_id="campaign-checkpoint-retry",
            action_id="action-checkpoint-retry",
            idempotency_key="idem-checkpoint-retry",
            owner_id="owner-campaign-file-checkpoint-retry",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id=request.turn_id,
            package_id=entry.package_id,
            tool_name="advance",
            parameters={"steps": 3},
        )

        def interrupted_checkpoint(_provider_input: object) -> object:
            yield ProviderEvent.action(action)
            yield ProviderEvent.tool_evidence(
                message_id="held-checkpoint-retry",
                payload={
                    "kind": "provider_held_narration",
                    "turn_id": request.turn_id,
                    "inference_id": "inference-held-checkpoint-retry",
                    "basis_message_id": request.message_id,
                    "assistant": {"role": "assistant", "content": held_prose},
                },
            )
            raise ProviderConnectionError("synthetic Campaign File checkpoint loss")

        with pytest.raises(ProviderConnectionError):
            loop.run(request, interrupted_checkpoint)  # type: ignore[arg-type]

        context = owner.session_context(request.session_id)
        terminal = next(
            message
            for message in context.messages
            if message.content.get("kind") == "session_attempt_terminal"
        )
        assert terminal.content["recovery_kind"] == "campaign_file_checkpoint"
        assert terminal.content["committed_action_id"] == action.action_id
        assert calls == [3]

        narrative_writes: list[dict[str, object]] = []

        def capture_narrative_write(
            campaign_id: str,
            **kwargs: object,
        ) -> dict[str, object]:
            narrative_writes.append({"campaign_id": campaign_id, **kwargs})
            return {"status": "campaign_file_updated", "resulting_revision": 1}

        monkeypatch.setattr(
            loop._mechanics,  # noqa: SLF001 - exact Retry Campaign File boundary
            "commit_campaign_file",
            capture_narrative_write,
        )
        observed_history: list[tuple[str, ...]] = []

        def provider_factory(turn_id: str) -> Callable[[object], object]:
            assert turn_id == request.turn_id

            def provider(provider_input: object) -> object:
                history = provider_input.history  # type: ignore[attr-defined]
                observed_history.append(
                    tuple(
                        str(message.content.get("kind"))
                        for message in history.messages
                    )
                )
                narrative_writer = provider_input.narrative_writer  # type: ignore[attr-defined]
                assert callable(narrative_writer)
                narrative_writer(
                    {
                        "facts": [
                            {
                                "fact_id": "far-arch-reached",
                                "category": "position",
                                "text": "The party has reached the far arch.",
                                "visibility": "gm_private",
                                "known_by": ["gm"],
                            }
                        ]
                    }
                )
                yield ProviderEvent.talk(held_prose)

            return provider

        def narration_only_must_not_run(
            _retry_input: RetryNarrationInput,
            _stream: NarrationStream,
        ) -> None:
            raise AssertionError("Campaign File checkpoint used narration-only Retry")

        interruption = owner.retryable_interruption(
            "campaign-checkpoint-retry",
            failed_attempt_id=request.attempt_id,
        )
        assert interruption is not None
        result = loop.retry(
            request.session_id,
            interruption_id=interruption.interruption_id,
            retry_id="retry-campaign-file-checkpoint",
            narrator=narration_only_must_not_run,
            provider_factory=provider_factory,  # type: ignore[arg-type]
        )

        assert calls == [3]
        assert observed_history
        assert "provider_held_narration" in observed_history[0]
        assert "session_attempt_terminal" in observed_history[0]
        assert narrative_writes == [
            {
                "campaign_id": "campaign-checkpoint-retry",
                "session_id": request.session_id,
                "basis_message_id": request.message_id,
                "parameters": {
                    "facts": [
                        {
                            "fact_id": "far-arch-reached",
                            "category": "position",
                            "text": "The party has reached the far arch.",
                            "visibility": "gm_private",
                            "known_by": ["gm"],
                        }
                    ]
                },
                "turn_id": request.turn_id,
            }
        ]
        assert [event.safe_payload.get("text") for event in result.canonical] == [held_prose]


def test_manual_retry_preserves_the_new_provider_failure_category(
    tmp_path: Path,
) -> None:
    """A failed Retry keeps its exact input basis and safe provider category."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderControl,
        ProviderEvent,
        SessionRunRequest,
    )

    with AuthorityOwner.open(
        tmp_path / "retry-provider-category.db",
        "owner-retry-provider-category",
        disconnect_grace_seconds=1.0,
    ) as owner:
        _campaign(
            owner,
            campaign_id="campaign-retry-provider-category",
            writer=WriterRole.DM,
            turn_id="turn-retry-provider-category",
        )
        owner.create_agent_session(
            campaign_id="campaign-retry-provider-category",
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-retry-provider-category",
        )
        loop = CampaignSessionLoop(owner)
        request = SessionRunRequest(
            session_id="session-retry-provider-category",
            turn_id="turn-retry-provider-category",
            attempt_id="input-retry-provider-category",
            message_id="message-retry-provider-category",
            user_text="Ask the Warden what changed.",
        )

        def initial_failure(_provider_input: object) -> object:
            raise ProviderConnectionError("synthetic initial connection loss")
            yield  # pragma: no cover

        with pytest.raises(ProviderConnectionError):
            loop.run(request, initial_failure)  # type: ignore[arg-type]
        interruption = owner.retryable_interruption(
            "campaign-retry-provider-category",
            failed_attempt_id=request.attempt_id,
        )
        assert interruption is not None

        def provider_factory(_turn_id: str) -> Callable[[object], object]:
            def provider(_provider_input: object) -> object:
                raise ProviderTimeoutError("synthetic Retry timeout")
                yield  # pragma: no cover

            return provider

        control = ProviderControl()
        with pytest.raises(ProviderTimeoutError):
            loop.retry(
                request.session_id,
                interruption_id=interruption.interruption_id,
                retry_id="retry-provider-category",
                narrator=lambda _input, _stream: None,
                provider_factory=provider_factory,  # type: ignore[arg-type]
                cancel=control.cancel,
                control=control,
            )

        terminal = next(
            message
            for message in reversed(owner.session_context(request.session_id).messages)
            if message.content.get("attempt_id") == "retry-provider-category"
        )
        assert terminal.content["input_id"] == request.attempt_id
        assert terminal.content["failure_category"] == "transient"
        assert terminal.content["recovery_kind"] == "uncommitted_player_action"

        chained = owner.retryable_interruption(
            "campaign-retry-provider-category",
            failed_attempt_id="retry-provider-category",
        )
        assert chained is not None
        observed_retry_inputs: list[str] = []

        def successful_provider_factory(_turn_id: str) -> Callable[[object], object]:
            def provider(provider_input: object) -> object:
                history = provider_input.history  # type: ignore[attr-defined]
                observed_retry_inputs.append(str(history.messages[-1].content["text"]))
                yield ProviderEvent.talk("The Warden answers on the second Retry.")

            return provider

        recovered = loop.retry(
            request.session_id,
            interruption_id=chained.interruption_id,
            retry_id="retry-provider-category-recovered",
            narrator=lambda _input, _stream: None,
            provider_factory=successful_provider_factory,  # type: ignore[arg-type]
        )
        assert observed_retry_inputs == [request.user_text]
        assert [event.safe_payload.get("text") for event in recovered.canonical] == [
            "The Warden answers on the second Retry."
        ]


def test_probe_10_steering_changes_later_safe_context_without_campaign_mutation(
    tmp_path: Path,
) -> None:
    """Probe 10: steering is safe session context, never retroactive authority."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionProviderInput,
        SessionRunRequest,
        SessionRunResult,
    )

    ready = threading.Event()
    errors: list[BaseException] = []
    results: list[SessionRunResult] = []
    with AuthorityOwner.open(
        tmp_path / "steering.db", "owner-steering", disconnect_grace_seconds=1.0
    ) as owner:
        _campaign(
            owner,
            campaign_id="campaign-steering",
            writer=WriterRole.BUILDER,
            turn_id="turn-steering",
        )
        owner.create_agent_session(
            campaign_id="campaign-steering",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-steering",
            selected_skill="BUILD CAMPAIGN SETTING",
        )
        loop = CampaignSessionLoop(owner)
        first = loop.run(
            SessionRunRequest(
                session_id="session-steering",
                turn_id="turn-steering",
                attempt_id="attempt-steering-first",
                message_id="message-steering-first",
                user_text="Draft the setting.",
            ),
            lambda _input: iter((ProviderEvent.talk("Initial safe answer."),)),
        )
        before = owner.snapshot("campaign-steering")
        before_context = owner.session_context("session-steering")

        def steered_provider(provider_input: SessionProviderInput) -> object:
            ready.set()
            steering = provider_input.control.wait_for_steering(5)
            assert steering == ("Keep the moon unresolved.",)
            yield ProviderEvent.talk(f"Later context applied: {steering[0]}")

        def run() -> None:
            try:
                results.append(
                    loop.run(
                        SessionRunRequest(
                            session_id="session-steering",
                            turn_id="turn-steering",
                            attempt_id="attempt-steering-second",
                            message_id="message-steering-second",
                            user_text="Continue the setting draft.",
                        ),
                        steered_provider,
                    )
                )
            except BaseException as error:  # pragma: no cover - thread transfer
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        assert ready.wait(5)
        loop.steer(
            "session-steering",
            "Keep the moon unresolved.",
            message_id="steering-explicit",
        )
        during = owner.snapshot("campaign-steering")
        during_context = owner.session_context("session-steering")
        assert (
            during.state,
            during.revision,
            during.package_lock,
            during.writer,
        ) == (
            before.state,
            before.revision,
            before.package_lock,
            before.writer,
        )
        assert during.events[: len(before.events)] == before.events
        assert during_context.messages[: len(before_context.messages)] == before_context.messages
        thread.join(5)
        assert not thread.is_alive() and not errors and len(results) == 1
        after = owner.snapshot("campaign-steering")
        assert after.revision == before.revision == 0
        assert after.state == before.state
        assert after.events[0] == before.events[0] == first.canonical[0]
        assert tuple(event.safe_payload["text"] for event in after.events) == (
            "Initial safe answer.",
            "Later context applied: Keep the moon unresolved.",
        )
        kinds = tuple(
            message.kind.value for message in owner.session_context("session-steering").messages
        )
        assert kinds == (
            "user",
            "canonical_reference",
            "user",
            "steering",
            "canonical_reference",
        )
        print("F4 PROBE 10", kinds, "revision", after.revision, "events", (1, 2))


def test_probe_12_natural_prompt_entry_records_user_and_open_source_provenance(
    tmp_path: Path,
) -> None:
    """Probe 12: Builder evidence is explicit, exact-content, and provenance-carrying."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    with AuthorityOwner.open(
        tmp_path / "evidence.db", "owner-evidence", disconnect_grace_seconds=1.0
    ) as owner:
        _campaign(
            owner,
            campaign_id="campaign-evidence",
            writer=WriterRole.BUILDER,
            turn_id="turn-evidence",
        )
        owner.create_agent_session(
            campaign_id="campaign-evidence",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-evidence",
            selected_skill="BUILD CAMPAIGN SETTING",
        )
        CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id="session-evidence",
                turn_id="turn-evidence",
                attempt_id="attempt-evidence",
                message_id="prompt-evidence",
                user_text="Use my synthetic premise and the cited open source.",
            ),
            lambda _input: iter((ProviderEvent.talk("Evidence requires explicit review."),)),
        )
        user_payload = b"synthetic user premise"
        open_payload = b"synthetic open source excerpt"
        user_evidence = owner.record_builder_evidence(
            session_id="session-evidence",
            evidence_id="evidence-user",
            source_kind="user_provided",
            content_sha256=hashlib.sha256(user_payload).hexdigest(),
            source_locator=None,
            safe_excerpt="A city moves once per century.",
            claims=("moving-city",),
        )
        open_evidence = owner.record_builder_evidence(
            session_id="session-evidence",
            evidence_id="evidence-open",
            source_kind="open_source",
            content_sha256=hashlib.sha256(open_payload).hexdigest(),
            source_locator="https://example.invalid/open-source",
            safe_excerpt="Public-domain lunar calendar reference.",
            claims=("lunar-calendar",),
        )
        assert user_evidence.source_kind.value == "user_provided"
        assert open_evidence.source_kind.value == "open_source"
        assert user_evidence.content_sha256 == hashlib.sha256(user_payload).hexdigest()
        assert open_evidence.source_locator == "https://example.invalid/open-source"
        evidence_messages = [
            item
            for item in owner.session_context("session-evidence").messages
            if item.kind.value == "evidence"
        ]
        assert tuple(item.message_id for item in evidence_messages) == (
            "evidence-user",
            "evidence-open",
        )
        assert owner.snapshot("campaign-evidence").revision == 0
        print(
            "F4 PROBE 12",
            [
                (item.source_kind.value, item.content_sha256)
                for item in (user_evidence, open_evidence)
            ],
        )


def test_probe_13_unsupported_or_contradictory_capability_stops_for_human_review(
    tmp_path: Path,
) -> None:
    """Probe 13: uncertainty is an explicit stop, never approximate success."""

    with AuthorityOwner.open(
        tmp_path / "review.db", "owner-review", disconnect_grace_seconds=1.0
    ) as owner:
        _campaign(
            owner,
            campaign_id="campaign-review",
            writer=WriterRole.BUILDER,
            turn_id="turn-review",
        )
        owner.create_agent_session(
            campaign_id="campaign-review",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-review",
            selected_skill="BUILD RULESET",
        )
        before = owner.snapshot("campaign-review")
        review = owner.require_builder_human_review(
            session_id="session-review",
            review_id="review-unsupported",
            unsupported_capabilities=("quantum-resolution",),
            contradictions=("source-a conflicts with source-b",),
        )
        after = owner.snapshot("campaign-review")
        assert after == before
        assert review.status.value == "review_required"
        context = owner.session_context("session-review")
        assert context.session.status.value == "review_required"
        assert context.messages[-1].kind.value == "human_review"
        assert context.messages[-1].content["outcome"] == "human_review_required"
        assert "success" not in str(context.messages[-1].content).casefold()
        assert owner.player_projection("campaign-review") == ()
        print(
            "F4 PROBE 13",
            review.session_id,
            review.status.value,
            context.messages[-1].content,
            "revision",
            after.revision,
        )


def test_probe_14_builder_children_are_bounded_isolated_and_return_only_evidence(
    tmp_path: Path,
) -> None:
    """Probe 14: direct children are bounded and their drafts never become truth."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )
    from sylvia_gm.domain.errors import CommandRejected

    with AuthorityOwner.open(
        tmp_path / "children.db", "owner-children", disconnect_grace_seconds=1.0
    ) as owner:
        _campaign(
            owner,
            campaign_id="campaign-children",
            writer=WriterRole.BUILDER,
            turn_id="turn-children",
        )
        parent = owner.create_agent_session(
            campaign_id="campaign-children",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-parent",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        child_ids: list[str] = []
        for index in range(3):
            child = owner.create_builder_child_session(
                parent_session_id=parent.session_id,
                provider_id="synthetic-provider",
                model_id=f"synthetic-child-{index}",
                session_id=f"session-child-{index}",
                selected_skill="BUILD CAMPAIGN PLAN",
                scope=(f"chapter-{index}",),
            )
            child_ids.append(child.session_id)
            assert child.parent_session_id == parent.session_id
            assert child.role is WriterRole.BUILDER
            assert child.scope == (f"chapter-{index}",)
            assert owner.session_context(child.session_id).messages == ()
        with pytest.raises(CommandRejected, match="bounded at three"):
            owner.create_builder_child_session(
                parent_session_id=parent.session_id,
                provider_id="synthetic-provider",
                model_id="overflow",
                session_id="session-child-overflow",
                selected_skill="BUILD CAMPAIGN PLAN",
                scope=("overflow",),
            )
        with pytest.raises(CommandRejected, match="cannot create nested children"):
            owner.create_builder_child_session(
                parent_session_id=child_ids[0],
                provider_id="synthetic-provider",
                model_id="nested",
                session_id="session-child-nested",
                selected_skill="BUILD CAMPAIGN PLAN",
                scope=("nested",),
            )
        before = owner.snapshot("campaign-children")
        CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id=child_ids[0],
                turn_id="turn-children",
                attempt_id="attempt-child",
                message_id="message-child",
                user_text="Draft a private candidate chapter.",
            ),
            lambda _input: iter((ProviderEvent.talk("CHILD-DRAFT-NOT-AUTHORITY"),)),
        )
        returned = owner.return_builder_child_evidence(
            child_session_id=child_ids[0],
            evidence_id="child-evidence-one",
            content_sha256=hashlib.sha256(b"bounded child result").hexdigest(),
            safe_summary="One bounded chapter outline is ready for parent review.",
            claims=("chapter-outline",),
        )
        after = owner.snapshot("campaign-children")
        assert (
            after.state,
            after.package_lock,
            after.revision,
            after.writer,
        ) == (
            before.state,
            before.package_lock,
            before.revision,
            before.writer,
        )
        parent_context = owner.session_context(parent.session_id)
        child_context = owner.session_context(child_ids[0])
        assert child_context.session.status.value == "completed"
        assert returned.authoritative is False
        assert parent_context.messages[-1].kind.value == "evidence"
        assert parent_context.messages[-1].content["child_session_id"] == child_ids[0]
        assert "CHILD-DRAFT-NOT-AUTHORITY" not in str(parent_context.messages)
        assert "CHILD-DRAFT-NOT-AUTHORITY" in str(child_context.messages)
        print(
            "F4 PROBE 14",
            child_ids,
            "bound=3",
            returned.evidence_id,
            "authoritative",
            returned.authoritative,
            "revision",
            after.revision,
        )


def test_probe_15_valid_plan_admission_records_identity_provenance_and_one_revision(
    tmp_path: Path,
) -> None:
    """Probe 15: deterministic owner admission creates the only accepted plan truth."""

    database = tmp_path / "plan-valid.db"
    content = {
        "title": "Synthetic Campaign Plan",
        "chapters": ["arrival", "clock tower", "departure"],
    }
    content_sha256 = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
    with AuthorityOwner.open(database, "owner-plan-valid", disconnect_grace_seconds=1.0) as owner:
        _campaign(
            owner,
            campaign_id="campaign-plan-valid",
            writer=WriterRole.BUILDER,
            turn_id="turn-plan-valid",
        )
        owner.create_agent_session(
            campaign_id="campaign-plan-valid",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-plan-valid",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        owner.record_builder_evidence(
            session_id="session-plan-valid",
            evidence_id="evidence-plan-user",
            source_kind="user_provided",
            content_sha256=hashlib.sha256(b"user plan premise").hexdigest(),
            source_locator=None,
            safe_excerpt="The campaign starts at the moving city.",
            claims=("arrival",),
        )
        owner.record_builder_evidence(
            session_id="session-plan-valid",
            evidence_id="evidence-plan-open",
            source_kind="open_source",
            content_sha256=hashlib.sha256(b"open plan source").hexdigest(),
            source_locator="https://example.invalid/plan-source",
            safe_excerpt="A public lunar calendar establishes the clock sequence.",
            claims=("clock-sequence",),
        )
        before = owner.snapshot("campaign-plan-valid")
        exact_lock_id = owner.current_package_lock("campaign-plan-valid").lock_id
        plan = owner.admit_campaign_plan(
            plan_id="plan-valid",
            campaign_id="campaign-plan-valid",
            builder_session_id="session-plan-valid",
            expected_revision=0,
            expected_package_lock_id=exact_lock_id,
            content=content,
            content_sha256=content_sha256,
            provenance_evidence_ids=("evidence-plan-user", "evidence-plan-open"),
            validation_outcome="valid",
            acceptance_status="accepted",
        )
        duplicate = owner.admit_campaign_plan(
            plan_id="plan-valid",
            campaign_id="campaign-plan-valid",
            builder_session_id="session-plan-valid",
            expected_revision=0,
            expected_package_lock_id=exact_lock_id,
            content=content,
            content_sha256=content_sha256,
            provenance_evidence_ids=("evidence-plan-user", "evidence-plan-open"),
            validation_outcome="valid",
            acceptance_status="accepted",
        )
        after = owner.snapshot("campaign-plan-valid")
        assert plan == duplicate == owner.campaign_plan("plan-valid")
        assert plan.content_sha256 == content_sha256
        assert plan.provenance_evidence_ids == (
            "evidence-plan-user",
            "evidence-plan-open",
        )
        assert plan.validation_outcome.value == "valid"
        assert plan.acceptance_status.value == "accepted"
        assert plan.plan_revision == 1
        assert plan.resulting_campaign_revision == 1
        assert (
            after.revision,
            after.state,
            after.package_lock,
            after.writer,
        ) == (1, before.state, before.package_lock, WriterRole.BUILDER)
        print(
            "F4 PROBE 15",
            plan.plan_id,
            plan.content_sha256,
            plan.provenance_evidence_ids,
            plan.validation_outcome.value,
            plan.acceptance_status.value,
            "plan_revision",
            plan.plan_revision,
            "campaign_revision",
            after.revision,
        )


def test_probe_16_invalid_package_provenance_and_plan_admission_is_fail_closed(
    tmp_path: Path,
) -> None:
    """Probe 16: invalid package and plan inputs have zero authority effect."""

    database = tmp_path / "plan-invalid.db"
    content = {"title": "Synthetic Invalid Plan Check", "chapters": ["only"]}
    content_sha256 = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
    with AuthorityOwner.open(database, "owner-plan-invalid") as owner:
        _campaign(
            owner,
            campaign_id="campaign-plan-invalid",
            writer=WriterRole.BUILDER,
            turn_id="turn-plan-invalid",
        )
        owner.create_agent_session(
            campaign_id="campaign-plan-invalid",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-plan-invalid",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        owner.record_builder_evidence(
            session_id="session-plan-invalid",
            evidence_id="evidence-plan-valid",
            source_kind="user_provided",
            content_sha256=hashlib.sha256(b"synthetic premise").hexdigest(),
            source_locator=None,
            safe_excerpt="A bounded synthetic premise.",
            claims=("only",),
        )
        before = owner.snapshot("campaign-plan-invalid")
        exact_lock_id = owner.current_package_lock("campaign-plan-invalid").lock_id
        before_candidates = owner.package_candidates()
        invalid_requests = (
            {
                "plan_id": "plan-wrong-lock",
                "content_sha256": content_sha256,
                "provenance_evidence_ids": ("evidence-plan-valid",),
                "validation_outcome": "valid",
                "acceptance_status": "accepted",
                "expected_revision": 0,
                "expected_package_lock_id": "lock-not-current",
            },
            {
                "plan_id": "plan-bad-content",
                "content_sha256": "0" * 64,
                "provenance_evidence_ids": ("evidence-plan-valid",),
                "validation_outcome": "valid",
                "acceptance_status": "accepted",
                "expected_revision": 0,
            },
            {
                "plan_id": "plan-missing-provenance",
                "content_sha256": content_sha256,
                "provenance_evidence_ids": (),
                "validation_outcome": "valid",
                "acceptance_status": "accepted",
                "expected_revision": 0,
            },
            {
                "plan_id": "plan-unknown-provenance",
                "content_sha256": content_sha256,
                "provenance_evidence_ids": ("evidence-unknown",),
                "validation_outcome": "valid",
                "acceptance_status": "accepted",
                "expected_revision": 0,
            },
            {
                "plan_id": "plan-invalid-validation",
                "content_sha256": content_sha256,
                "provenance_evidence_ids": ("evidence-plan-valid",),
                "validation_outcome": "invalid",
                "acceptance_status": "accepted",
                "expected_revision": 0,
            },
            {
                "plan_id": "plan-unaccepted",
                "content_sha256": content_sha256,
                "provenance_evidence_ids": ("evidence-plan-valid",),
                "validation_outcome": "valid",
                "acceptance_status": "pending",
                "expected_revision": 0,
            },
            {
                "plan_id": "plan-stale",
                "content_sha256": content_sha256,
                "provenance_evidence_ids": ("evidence-plan-valid",),
                "validation_outcome": "valid",
                "acceptance_status": "accepted",
                "expected_revision": 1,
            },
        )
        failures: list[str] = []
        for request in invalid_requests:
            with pytest.raises(AuthorityError) as rejected:
                owner.admit_campaign_plan(
                    campaign_id="campaign-plan-invalid",
                    builder_session_id="session-plan-invalid",
                    content=content,
                    expected_package_lock_id=str(
                        request.get("expected_package_lock_id", exact_lock_id)
                    ),
                    **{
                        key: value
                        for key, value in request.items()
                        if key != "expected_package_lock_id"
                    },
                )
            failures.append(rejected.value.__class__.__name__)
            with pytest.raises(CommandRejected):
                owner.campaign_plan(str(request["plan_id"]))
        with pytest.raises(CommandRejected):
            owner.install_package_archive(
                io.BytesIO(b"not-a-package-archive"),
                install_id="install-invalid-f4",
            )
        after = owner.snapshot("campaign-plan-invalid")
        assert after == before
        assert owner.package_candidates() == before_candidates
        assert owner.session_context("session-plan-invalid").session.status.value == "active"
        print(
            "F4 PROBE 16",
            "rejections",
            tuple(failures),
            "revision",
            after.revision,
            "writer",
            after.writer.value,
            "lock",
            after.package_lock,
        )


def test_probe_17_valid_builder_package_crosses_f3_without_activation_or_repin(
    tmp_path: Path,
) -> None:
    """Probe 17: Builder publication reuses F3 and cannot activate campaign truth."""

    database = tmp_path / "builder-package-candidate.db"
    with AuthorityOwner.open(database, "owner-builder-package") as owner:
        _campaign(
            owner,
            campaign_id="campaign-builder-package",
            writer=WriterRole.BUILDER,
            turn_id="turn-builder-package",
        )
        owner.create_agent_session(
            campaign_id="campaign-builder-package",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-builder-package",
            selected_skill="BUILD RULESET",
        )
        before = owner.snapshot("campaign-builder-package")
        lock_before = owner.current_package_lock("campaign-builder-package")
        candidate = owner.publish_builder_package_candidate(
            builder_session_id="session-builder-package",
            archive=io.BytesIO(_clockwork_archive()),
            install_id="install-builder-package-candidate",
        )
        after = owner.snapshot("campaign-builder-package")
        lock_after = owner.current_package_lock("campaign-builder-package")
        assert candidate == owner.package_candidate(candidate.candidate_id)
        assert candidate.candidate_id != lock_before.lock_id
        assert lock_after == lock_before
        assert after == before
        assert len(owner.package_lock_lineage("campaign-builder-package")) == 1
        print(
            "F4 PROBE 17",
            "candidate",
            candidate.candidate_id,
            "published",
            candidate.status.value,
            "campaign_lock",
            lock_after.lock_id,
            "revision",
            after.revision,
            "writer",
            after.writer.value,
        )


def test_probe_18_exact_plan_identity_replays_after_restart_before_current_checks(
    tmp_path: Path,
) -> None:
    """Probe 18: accepted plan replay is exact, one-revision, and restart durable."""

    database = tmp_path / "plan-restart.db"
    content = {"title": "Restart Plan", "facts": ["stable", "synthetic"]}
    content_sha256 = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
    admission: dict[str, object]
    with AuthorityOwner.open(database, "owner-plan-restart-a") as owner:
        _campaign(
            owner,
            campaign_id="campaign-plan-restart",
            writer=WriterRole.BUILDER,
            turn_id="turn-plan-restart",
        )
        owner.create_agent_session(
            campaign_id="campaign-plan-restart",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-plan-restart",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        owner.record_builder_evidence(
            session_id="session-plan-restart",
            evidence_id="evidence-plan-restart",
            source_kind="user_provided",
            content_sha256=hashlib.sha256(b"restart premise").hexdigest(),
            source_locator=None,
            safe_excerpt="A restart-stable synthetic premise.",
            claims=("stable",),
        )
        lock_id = owner.current_package_lock("campaign-plan-restart").lock_id
        admission = {
            "plan_id": "plan-restart",
            "campaign_id": "campaign-plan-restart",
            "builder_session_id": "session-plan-restart",
            "expected_revision": 0,
            "expected_package_lock_id": lock_id,
            "content": content,
            "content_sha256": content_sha256,
            "provenance_evidence_ids": ("evidence-plan-restart",),
            "validation_outcome": "valid",
            "acceptance_status": "accepted",
        }
        accepted = owner.admit_campaign_plan(**admission)
        assert owner.snapshot("campaign-plan-restart").revision == 1

    with AuthorityOwner.open(database, "owner-plan-restart-b") as reopened:
        stored = reopened.accepted_campaign_plan("campaign-plan-restart")
        replay = reopened.admit_campaign_plan(**admission)
        assert stored == replay == accepted
        assert stored.package_lock_id == admission["expected_package_lock_id"]
        assert reopened.snapshot("campaign-plan-restart").revision == 1
        changed = {"title": "Changed reuse", "facts": ["conflict"]}
        with pytest.raises(IdempotencyConflict):
            reopened.admit_campaign_plan(
                **{
                    **admission,
                    "expected_revision": 999,
                    "content": changed,
                    "content_sha256": hashlib.sha256(canonical_json_bytes(changed)).hexdigest(),
                }
            )
        assert reopened.snapshot("campaign-plan-restart").revision == 1
        print(
            "F4 PROBE 18",
            stored.plan_id,
            "payload",
            stored.payload_sha256,
            "plan_revision",
            stored.plan_revision,
            "campaign_revision",
            stored.resulting_campaign_revision,
            "restart_replay",
            replay == accepted,
        )


def test_probe_19_handoff_rejects_every_nonquiescent_or_inexact_basis(
    tmp_path: Path,
) -> None:
    """Probe 19: all stale, inexact, incomplete, and active-work bases reject."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    database = tmp_path / "handoff-rejections.db"
    with AuthorityOwner.open(
        database, "owner-handoff-rejections", disconnect_grace_seconds=2.0
    ) as owner:
        valid = _prepare_handoff(owner, suffix="reject")
        campaign_id = str(valid["campaign_id"])
        builder_session_id = str(valid["builder_session_id"])
        dm_session_id = str(valid["dm_session_id"])

        def rejected(**changes: object) -> None:
            with pytest.raises(AuthorityError):
                owner.handoff_builder_to_dm(**{**valid, **changes})
            assert owner.snapshot(campaign_id).revision == 1
            assert owner.snapshot(campaign_id).writer is WriterRole.BUILDER
            assert owner.session_context(builder_session_id).session.status.value == "active"
            with pytest.raises(CommandRejected):
                owner.session_context(dm_session_id)

        rejected(expected_writer=WriterRole.DM)
        rejected(expected_revision=0)
        rejected(expected_package_lock_id="lock-not-current")
        rejected(expected_package_candidate_id="candidate-not-current")
        rejected(plan_id="plan-not-admitted")
        rejected(expected_plan_revision=999)
        rejected(expected_plan_content_sha256="f" * 64)
        rejected(completed_turn_id="turn-not-completed")
        rejected()  # the exact TURN exists but is still open

        owner.complete_turn(campaign_id, str(valid["completed_turn_id"]))
        child = owner.create_builder_child_session(
            parent_session_id=builder_session_id,
            provider_id="synthetic-child-provider",
            model_id="synthetic-child-model",
            session_id="session-child-reject",
            selected_skill="BUILD CAMPAIGN PLAN",
            scope=("bounded",),
        )
        rejected()
        owner.stop_agent_session(child.session_id)

        owner._reserve_campaign_transition(campaign_id)
        try:
            rejected()
        finally:
            owner._release_campaign_transition(campaign_id)

        ready = threading.Event()
        release = threading.Event()
        run_errors: list[BaseException] = []

        def provider(_input: object) -> object:
            ready.set()
            assert release.wait(5)
            yield ProviderEvent.talk("Safe completion after handoff rejection.")

        def run_active() -> None:
            try:
                CampaignSessionLoop(owner).run(
                    SessionRunRequest(
                        session_id=builder_session_id,
                        turn_id=str(valid["completed_turn_id"]),
                        attempt_id="attempt-active-handoff-reject",
                        message_id="message-active-handoff-reject",
                        user_text="Hold one synthetic provider call open.",
                    ),
                    provider,
                )
            except BaseException as error:  # pragma: no cover - diagnostic capture
                run_errors.append(error)

        thread = threading.Thread(target=run_active)
        thread.start()
        assert ready.wait(5), run_errors
        rejected()
        release.set()
        thread.join(5)
        assert not thread.is_alive() and not run_errors
        final = owner.snapshot(campaign_id)
        assert final.revision == 1 and final.writer is WriterRole.BUILDER
        print(
            "F4 PROBE 19",
            "campaign",
            campaign_id,
            "plan",
            valid["plan_id"],
            "lock",
            valid["expected_package_lock_id"],
            "revision",
            final.revision,
            "writer",
            final.writer.value,
            "matrix",
            12,
        )


def test_probe_20_injected_handoff_failures_roll_back_every_authority_effect(
    tmp_path: Path,
) -> None:
    """Probe 20: failures before writes and before commit leave Builder solely authoritative."""

    database = tmp_path / "handoff-rollback.db"
    with AuthorityOwner.open(database, "owner-handoff-rollback") as owner:
        request = _prepare_handoff(owner, suffix="rollback")
        campaign_id = str(request["campaign_id"])
        builder_id = str(request["builder_session_id"])
        dm_id = str(request["dm_session_id"])
        owner.complete_turn(campaign_id, str(request["completed_turn_id"]))
        before = owner.snapshot(campaign_id)
        before_builder = owner.session_context(builder_id)
        before_lock_lineage = owner.package_lock_lineage(campaign_id)

        for injected_stage in ("before", "during"):

            def fail_at(stage: str, *, target: str = injected_stage) -> None:
                if stage == target:
                    raise RuntimeError(f"injected-handoff-{target}")

            owner._store._handoff_failure_stage = fail_at  # type: ignore[method-assign]
            with pytest.raises(RuntimeError, match=f"injected-handoff-{injected_stage}"):
                owner.handoff_builder_to_dm(**request)
            assert owner.snapshot(campaign_id) == before
            assert owner.session_context(builder_id) == before_builder
            assert owner.package_lock_lineage(campaign_id) == before_lock_lineage
            with pytest.raises(CommandRejected, match="session is unavailable"):
                owner.session_context(dm_id)
            with pytest.raises(CommandRejected, match="handoff is unavailable"):
                owner.builder_dm_handoff(str(request["handoff_id"]))

        owner._store._handoff_failure_stage = lambda _stage: None  # type: ignore[method-assign]
        final = owner.snapshot(campaign_id)
        assert final.revision == 1 and final.writer is WriterRole.BUILDER
        assert owner.session_context(builder_id).session.status.value == "active"
        print(
            "F4 PROBE 20",
            "campaign",
            campaign_id,
            "failures",
            ("before", "during"),
            "revision",
            final.revision,
            "writer",
            final.writer.value,
            "lock",
            owner.current_package_lock(campaign_id).lock_id,
        )


def test_stopped_dm_handoff_session_reopens_for_resume(tmp_path: Path) -> None:
    """A stopped DM session remains a valid active-campaign handoff projection."""

    database = tmp_path / "stopped-dm-reopen.db"
    with AuthorityOwner.open(database, "owner-stopped-dm") as owner:
        request = _prepare_handoff(owner, suffix="stopped-dm")
        campaign_id = str(request["campaign_id"])
        dm_id = str(request["dm_session_id"])
        owner.complete_turn(campaign_id, str(request["completed_turn_id"]))
        owner.handoff_builder_to_dm(**request)

        stopped = owner.stop_agent_session(dm_id)

        assert stopped.status is SessionStatus.STOPPED
        assert owner.snapshot(campaign_id).writer is WriterRole.DM

    with AuthorityOwner.open(database, "owner-stopped-dm-reopen") as reopened:
        assert reopened.session_context(dm_id).session.status is SessionStatus.STOPPED
        resumed = reopened.resume_agent_session(dm_id)
        assert resumed.status is SessionStatus.ACTIVE


def test_resumed_dm_session_opens_fresh_turn_after_stop_checkpoint(tmp_path: Path) -> None:
    """A resumed DM can keep writing Campaign State after Stop completed its old TURN."""

    database = tmp_path / "stopped-dm-fresh-turn.db"
    with AuthorityOwner.open(
        database,
        "owner-stopped-dm-turn",
        disconnect_grace_seconds=2.0,
    ) as owner:
        request = _prepare_handoff(owner, suffix="stopped-dm-turn")
        campaign_id = str(request["campaign_id"])
        dm_id = str(request["dm_session_id"])
        owner.complete_turn(campaign_id, str(request["completed_turn_id"]))
        owner.handoff_builder_to_dm(**request)
        owner.open_turn(campaign_id, "turn-dm-stopped-checkpoint")
        stopped_turn = owner.current_open_turn(campaign_id)
        assert stopped_turn is not None
        owner.start_narration_attempt(
            campaign_id=campaign_id,
            attempt_id="attempt-before-stop",
            turn_id=stopped_turn,
            cancel=lambda: None,
        )
        interrupted = owner.interrupt_narration(campaign_id, "attempt-before-stop")
        owner.stop_agent_session(dm_id)
        owner.complete_turn(campaign_id, stopped_turn)
        assert owner.current_open_turn(campaign_id) is None

    with AuthorityOwner.open(database, "owner-stopped-dm-turn-reopen") as reopened:
        resumed = reopened.resume_agent_session(dm_id)
        resumed_turn = reopened.current_open_turn(campaign_id)

        assert resumed.status is SessionStatus.ACTIVE
        assert resumed_turn is not None
        assert resumed_turn != stopped_turn
        assert reopened.snapshot(campaign_id).turns[resumed_turn] is TurnStatus.OPEN
        rebound = reopened.narration_interruption(
            campaign_id,
            interrupted.interruption_id,
        )
        assert rebound.turn_id == resumed_turn
        assert rebound.last_action_id is None


def test_probe_21_existing_campaign_handoff_is_one_exact_atomic_revision(
    tmp_path: Path,
) -> None:
    """Probe 21: DM becomes sole gameplay writer while Builder chat persists."""

    database = tmp_path / "handoff-success.db"
    portable = tmp_path / "handoff-success.sylvia-gm"
    with AuthorityOwner.open(
        database, "owner-handoff-success", disconnect_grace_seconds=2.0
    ) as owner:
        request = _prepare_handoff(owner, suffix="success")
        campaign_id = str(request["campaign_id"])
        builder_id = str(request["builder_session_id"])
        dm_id = str(request["dm_session_id"])
        owner.complete_turn(campaign_id, str(request["completed_turn_id"]))
        before = owner.snapshot(campaign_id)
        before_lock = owner.current_package_lock(campaign_id)
        before_lineage = owner.package_lock_lineage(campaign_id)
        builder_messages = owner.session_context(builder_id).messages

        handoff = owner.handoff_builder_to_dm(**request)
        after = owner.snapshot(campaign_id)
        assert handoff == owner.builder_dm_handoff(str(request["handoff_id"]))
        assert handoff.expected_revision == before.revision == 1
        assert handoff.resulting_campaign_revision == after.revision == 2
        assert handoff.package_lock_id == before_lock.lock_id
        assert owner.current_package_lock(campaign_id) == before_lock
        assert owner.package_lock_lineage(campaign_id) == before_lineage
        assert after.writer is WriterRole.DM and after.lifecycle == before.lifecycle
        assert after.events == before.events and after.turns == before.turns
        assert owner.session_context(builder_id).session.status.value == "active"
        assert owner.session_context(builder_id).messages == builder_messages
        dm = owner.session_context(dm_id).session
        assert dm.role is WriterRole.DM and dm.status.value == "active"
        assert dm.provider_id == request["dm_provider_id"]
        assert dm.model_id == request["dm_model_id"]

        connection = sqlite3.connect(database)
        try:
            handoff_rows = connection.execute(
                "SELECT COUNT(*), MIN(chronology_ordinal), MAX(chronology_ordinal) "
                "FROM builder_dm_handoffs WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            writers = connection.execute(
                "SELECT role, status, COUNT(*) FROM agent_sessions WHERE campaign_id = ? "
                "GROUP BY role, status ORDER BY role, status",
                (campaign_id,),
            ).fetchall()
            revision_cause = connection.execute(
                "SELECT handoff_id FROM campaign_revisions WHERE campaign_id = ? AND revision = 2",
                (campaign_id,),
            ).fetchone()
            candidate_row = connection.execute(
                "SELECT package_candidate_id FROM builder_dm_handoffs WHERE handoff_id = ?",
                (handoff.handoff_id,),
            ).fetchone()
        finally:
            connection.close()
        assert handoff_rows == (1, 1, 1)
        assert candidate_row == (None,)
        assert writers == [("builder", "active", 1), ("dm", "active", 1)]
        assert revision_cause == (handoff.handoff_id,)
        index_entry = owner.campaign_index()[0]
        assert tuple(
            (session.session_id, session.role, session.status) for session in index_entry.sessions
        ) == (
            (builder_id, WriterRole.BUILDER.value, "active"),
            (dm_id, WriterRole.DM.value, "active"),
        )

        from sylvia_gm.agent._session_loop import (
            CampaignSessionLoop,
            ProviderEvent,
            SessionRunRequest,
        )

        loop = CampaignSessionLoop(owner)
        result = loop.run(
            SessionRunRequest(
                session_id=dm_id,
                turn_id=str(request["completed_turn_id"]),
                attempt_id="attempt-dm-after-handoff",
                message_id="message-dm-after-handoff",
                user_text="Begin safe DM continuation.",
            ),
            lambda _input: (ProviderEvent.talk("DM authority is active."),),
        )
        assert tuple(event.safe_payload["text"] for event in result.canonical) == (
            "DM authority is active.",
        )
        activity = next(
            message
            for message in owner.session_context(dm_id).messages
            if message.content.get("kind") == "gm_turn_activity"
        )
        timing = activity.content["activity_timing"]
        assert timing["phase"] == "completed"
        assert timing["operation"] is None
        assert 0 <= timing["quiet_ms"] <= timing["elapsed_ms"]
        assert activity.content == {
            "activity_timing": timing,
            "kind": "gm_turn_activity",
            "activity": "turn_completed",
            "attempt_id": "attempt-dm-after-handoff",
            "basis_message_id": "message-dm-after-handoff",
            "turn_id": str(request["completed_turn_id"]),
        }
        owner.campaign_lifecycle(campaign_id)
        owner.export_campaign(
            operation_id="export-handoff-success",
            campaign_id=campaign_id,
            target=portable,
        )
        print(
            "F4 PROBE 21",
            handoff.handoff_id,
            "campaign",
            campaign_id,
            "plan",
            handoff.plan_id,
            "candidate",
            handoff.package_candidate_id,
            "lock",
            handoff.package_lock_id,
            "revision",
            f"{handoff.expected_revision}->{handoff.resulting_campaign_revision}",
            "sessions",
            f"{builder_id}=active,{dm_id}=active",
            "chronology",
            handoff.chronology_ordinal,
        )

    with AuthorityOwner.open(database, "owner-handoff-success-reopen") as reopened:
        persisted = reopened.builder_dm_handoff(str(request["handoff_id"]))
        assert persisted.package_candidate_id is None
        assert reopened.current_package_lock(campaign_id).package_lock == PackageLock(entries=())
        print("F4 PROBE 21 REOPEN", persisted.handoff_id, persisted.package_candidate_id)

    from sylvia_gm.infrastructure import campaign_archive

    campaign_archive.validate_campaign_archive(portable, expected_campaign_id=campaign_id)
    with zipfile.ZipFile(portable) as archive:
        authority = tuple(json.loads(line) for line in archive.read("authority.jsonl").splitlines())
    handoff_rows = tuple(row["row"] for row in authority if row["table"] == "builder_dm_handoffs")
    assert len(handoff_rows) == 1 and handoff_rows[0]["package_candidate_id"] is None

    with AuthorityOwner.open(
        tmp_path / "handoff-recovered.db", "owner-handoff-recovered"
    ) as recovered:
        recovery = recovered.recover_campaign(
            recovery_id="recovery-handoff-success", source=portable
        )
        recovered_handoff = recovered.builder_dm_handoff(str(request["handoff_id"]))
        assert recovery.campaign_id == campaign_id
        assert recovered_handoff.package_candidate_id is None
        assert recovered.current_package_lock(campaign_id).package_lock == PackageLock(entries=())
        print("F4 PROBE 21 PORTABLE", recovery.archive_sha256, recovered_handoff)


def test_nonempty_handoff_requires_exact_candidate_while_empty_handoff_uses_null(
    tmp_path: Path,
) -> None:
    """A nullable candidate is valid only for a genuinely empty campaign closure."""

    with AuthorityOwner.open(
        tmp_path / "handoff-candidate-basis.db", "owner-handoff-basis"
    ) as owner:
        request = _prepare_handoff(owner, suffix="candidate-basis", with_package=True)
        campaign_id = str(request["campaign_id"])
        owner.complete_turn(campaign_id, str(request["completed_turn_id"]))

        with pytest.raises(CommandRejected) as rejected:
            owner.handoff_builder_to_dm(**{**request, "expected_package_candidate_id": None})
        assert rejected.value.code == "handoff_candidate_mismatch"

        handoff = owner.handoff_builder_to_dm(**request)
        assert handoff.package_candidate_id == request["expected_package_candidate_id"]
        assert handoff.package_candidate_id is not None
        print(
            "F4 HANDOFF CANDIDATE BASIS",
            "empty=null",
            "nonempty=exact",
            handoff.package_candidate_id,
        )


def test_probe_21_persistent_builder_stays_chat_active_but_cannot_retry_dm_gameplay(
    tmp_path: Path,
) -> None:
    """Probe 21: Builder chat persists without access to DM gameplay Retry."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    database = tmp_path / "persistent-builder-retry.db"
    with AuthorityOwner.open(
        database, "owner-persistent-builder-retry", disconnect_grace_seconds=2.0
    ) as owner:
        request = _prepare_handoff(owner, suffix="closed-retry")
        campaign_id = str(request["campaign_id"])
        builder_id = str(request["builder_session_id"])
        loop = CampaignSessionLoop(owner)

        def interrupted_provider(_input: object) -> object:
            yield ProviderEvent.talk("Discard this unfinished Builder narration.")
            raise RuntimeError("synthetic Builder narration interruption")

        with pytest.raises(RuntimeError, match="synthetic Builder narration interruption"):
            loop.run(
                SessionRunRequest(
                    session_id=builder_id,
                    turn_id=str(request["completed_turn_id"]),
                    attempt_id="attempt-builder-before-handoff-retry",
                    message_id="message-builder-before-handoff-retry",
                    user_text="Prepare a retryable interruption before handoff.",
                ),
                interrupted_provider,
            )

        owner.complete_turn(campaign_id, str(request["completed_turn_id"]))
        owner.handoff_builder_to_dm(**request)
        before_campaign = owner.snapshot(campaign_id)
        before_builder = owner.session_context(builder_id)
        narrator_calls: list[str] = []

        def forbidden_narrator(_retry_input: RetryNarrationInput, stream: NarrationStream) -> None:
            narrator_calls.append("called")
            stream.emit(
                ProseChannel.TALK,
                "CLOSED-BUILDER-DURABLE-POST-HANDOFF-WRITE",
            )

        with pytest.raises(CommandRejected, match="writer authority"):
            loop.retry(
                builder_id,
                retry_id="retry-builder-after-handoff-forbidden",
                narrator=forbidden_narrator,
            )

        from sylvia_gm.domain.session import SessionMessageKind

        owner.append_agent_message(
            session_id=builder_id,
            message_id="message-builder-after-dm-start",
            kind=SessionMessageKind.USER,
            content={"text": "Continue working in the persistent Builder chat."},
        )

        assert narrator_calls == []
        assert owner.snapshot(campaign_id) == before_campaign
        after_builder = owner.session_context(builder_id)
        assert after_builder.session.status.value == "active"
        assert after_builder.messages[:-1] == before_builder.messages
        assert after_builder.messages[-1].content == {
            "text": "Continue working in the persistent Builder chat."
        }
        assert owner.snapshot(campaign_id).writer is WriterRole.DM
        print(
            "F4 PROBE 21 PERSISTENT BUILDER / DM GAMEPLAY BOUNDARY",
            campaign_id,
            builder_id,
            owner.session_context(builder_id).session.status.value,
            owner.snapshot(campaign_id).writer.value,
        )


def test_probe_19_provider_run_holds_campaign_work_across_session_admission_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Probe 19: handoff cannot land after run validation but before narration admission."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
        SessionRunResult,
    )

    with AuthorityOwner.open(
        tmp_path / "provider-handoff-race.db",
        "owner-provider-handoff-race",
        disconnect_grace_seconds=2.0,
    ) as owner:
        request = _prepare_handoff(owner, suffix="provider-race")
        campaign_id = str(request["campaign_id"])
        builder_id = str(request["builder_session_id"])
        owner.complete_turn(campaign_id, str(request["completed_turn_id"]))
        loop = CampaignSessionLoop(owner)
        narration_admission_reached = threading.Event()
        release_narration_admission = threading.Event()
        provider_calls: list[str] = []
        run_results: list[SessionRunResult] = []
        run_errors: list[BaseException] = []
        original_start = owner.start_narration_attempt

        def paused_start_narration_attempt(**kwargs: object) -> None:
            narration_admission_reached.set()
            assert release_narration_admission.wait(5)
            original_start(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(owner, "start_narration_attempt", paused_start_narration_attempt)

        def provider(_input: object) -> object:
            provider_calls.append("called")
            yield ProviderEvent.talk("RACE-CLOSED-BUILDER-MUST-NOT-CANONICALIZE-RUN")

        def run_builder() -> None:
            try:
                run_results.append(
                    loop.run(
                        SessionRunRequest(
                            session_id=builder_id,
                            turn_id=str(request["completed_turn_id"]),
                            attempt_id="attempt-provider-handoff-race",
                            message_id="message-provider-handoff-race",
                            user_text="Pause after session admission before provider narration.",
                        ),
                        provider,
                    )
                )
            except BaseException as error:  # pragma: no cover - thread transfer
                run_errors.append(error)

        thread = threading.Thread(target=run_builder)
        thread.start()
        assert narration_admission_reached.wait(5), run_errors
        handoff_errors: list[AuthorityError] = []
        try:
            owner.handoff_builder_to_dm(**request)
        except AuthorityError as error:
            handoff_errors.append(error)
        finally:
            release_narration_admission.set()
        thread.join(5)
        assert not thread.is_alive()

        after = owner.snapshot(campaign_id)
        leaked = tuple(
            event.safe_payload.get("text")
            for event in after.events
            if event.safe_payload.get("text") == "RACE-CLOSED-BUILDER-MUST-NOT-CANONICALIZE-RUN"
        )
        print(
            "F4 PROBE 19 PROVIDER CHECK-TO-USE RACE",
            "handoff_error",
            getattr(handoff_errors[0], "code", None) if handoff_errors else None,
            "writer",
            after.writer.value,
            "builder_status",
            owner.session_context(builder_id).session.status.value,
            "provider_calls",
            provider_calls,
            "run_errors",
            [str(error) for error in run_errors],
            "talk",
            leaked,
        )
        assert [getattr(error, "code", None) for error in handoff_errors] == [
            "campaign_work_in_progress"
        ]
        assert not run_errors and len(run_results) == 1
        assert provider_calls == ["called"] and len(leaked) == 1
        assert after.writer is WriterRole.BUILDER
        assert owner.session_context(builder_id).session.status.value == "active"
        with pytest.raises(CommandRejected, match="session is unavailable"):
            owner.session_context(str(request["dm_session_id"]))


def test_probe_21_manual_retry_holds_campaign_work_across_session_admission_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Probe 21: handoff cannot land after Retry validation but before narration work."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    with AuthorityOwner.open(
        tmp_path / "retry-handoff-race.db",
        "owner-retry-handoff-race",
        disconnect_grace_seconds=2.0,
    ) as owner:
        request = _prepare_handoff(owner, suffix="retry-race")
        campaign_id = str(request["campaign_id"])
        builder_id = str(request["builder_session_id"])
        loop = CampaignSessionLoop(owner)

        def interrupted_provider(_input: object) -> object:
            yield ProviderEvent.talk("Discard this retry-race setup prose.")
            raise RuntimeError("synthetic retry-race interruption")

        with pytest.raises(RuntimeError, match="synthetic retry-race interruption"):
            loop.run(
                SessionRunRequest(
                    session_id=builder_id,
                    turn_id=str(request["completed_turn_id"]),
                    attempt_id="attempt-retry-handoff-race-source",
                    message_id="message-retry-handoff-race-source",
                    user_text="Create a retryable Builder interruption.",
                ),
                interrupted_provider,
            )
        owner.complete_turn(campaign_id, str(request["completed_turn_id"]))

        retry_admission_reached = threading.Event()
        release_retry_admission = threading.Event()
        narrator_calls: list[str] = []
        retry_results: list[object] = []
        retry_errors: list[BaseException] = []
        original_retry = owner.manual_retry

        def paused_manual_retry(**kwargs: object) -> object:
            retry_admission_reached.set()
            assert release_retry_admission.wait(5)
            return original_retry(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(owner, "manual_retry", paused_manual_retry)

        def narrator(_retry_input: RetryNarrationInput, stream: NarrationStream) -> None:
            narrator_calls.append("called")
            stream.emit(
                ProseChannel.TALK,
                "RACE-CLOSED-BUILDER-MUST-NOT-CANONICALIZE-RETRY",
            )

        def retry_builder() -> None:
            try:
                retry_results.append(
                    loop.retry(
                        builder_id,
                        retry_id="retry-builder-handoff-race",
                        narrator=narrator,
                    )
                )
            except BaseException as error:  # pragma: no cover - thread transfer
                retry_errors.append(error)

        thread = threading.Thread(target=retry_builder)
        thread.start()
        assert retry_admission_reached.wait(5), retry_errors
        handoff_errors: list[AuthorityError] = []
        try:
            owner.handoff_builder_to_dm(**request)
        except AuthorityError as error:
            handoff_errors.append(error)
        finally:
            release_retry_admission.set()
        thread.join(5)
        assert not thread.is_alive()

        after = owner.snapshot(campaign_id)
        leaked = tuple(
            event.safe_payload.get("text")
            for event in after.events
            if event.safe_payload.get("text") == "RACE-CLOSED-BUILDER-MUST-NOT-CANONICALIZE-RETRY"
        )
        print(
            "F4 PROBE 21 RETRY CHECK-TO-USE RACE",
            "handoff_error",
            getattr(handoff_errors[0], "code", None) if handoff_errors else None,
            "writer",
            after.writer.value,
            "builder_status",
            owner.session_context(builder_id).session.status.value,
            "narrator_calls",
            narrator_calls,
            "retry_errors",
            [str(error) for error in retry_errors],
            "talk",
            leaked,
        )
        assert [getattr(error, "code", None) for error in handoff_errors] == [
            "campaign_work_in_progress"
        ]
        assert not retry_errors and len(retry_results) == 1
        assert narrator_calls == ["called"] and len(leaked) == 1
        assert after.writer is WriterRole.BUILDER
        assert owner.session_context(builder_id).session.status.value == "active"
        with pytest.raises(CommandRejected, match="session is unavailable"):
            owner.session_context(str(request["dm_session_id"]))


def test_probe_22_handoff_replay_identity_precedes_all_current_state_checks(
    tmp_path: Path,
) -> None:
    """Probe 22: exact replay is stored/no-op and changed reuse conflicts first."""

    database = tmp_path / "handoff-replay.db"
    with AuthorityOwner.open(database, "owner-handoff-replay") as owner:
        request = _prepare_handoff(owner, suffix="replay")
        campaign_id = str(request["campaign_id"])
        owner.complete_turn(campaign_id, str(request["completed_turn_id"]))
        accepted = owner.handoff_builder_to_dm(**request)
        before = owner.snapshot(campaign_id)
        before_builder = owner.session_context(str(request["builder_session_id"]))
        before_dm = owner.session_context(str(request["dm_session_id"]))
        before_lineage = owner.package_lock_lineage(campaign_id)

        replay = owner.handoff_builder_to_dm(**request)
        assert replay == accepted
        with pytest.raises(IdempotencyConflict, match="identity payload"):
            owner.handoff_builder_to_dm(
                **{
                    **request,
                    "expected_revision": 999,
                    "dm_model_id": "synthetic-changed-model",
                }
            )
        assert owner.snapshot(campaign_id) == before
        assert owner.session_context(str(request["builder_session_id"])) == before_builder
        assert owner.session_context(str(request["dm_session_id"])) == before_dm
        assert owner.package_lock_lineage(campaign_id) == before_lineage
        connection = sqlite3.connect(database)
        try:
            assert connection.execute("SELECT COUNT(*) FROM builder_dm_handoffs").fetchone() == (1,)
            assert connection.execute(
                "SELECT COUNT(*) FROM campaign_revisions WHERE handoff_id = ?",
                (accepted.handoff_id,),
            ).fetchone() == (1,)
        finally:
            connection.close()
        print(
            "F4 PROBE 22",
            accepted.handoff_id,
            "stored_result",
            replay == accepted,
            "revision",
            before.revision,
            "changed_reuse",
            "conflict-before-current-state",
        )


def test_probe_23_archive_admission_preserves_idle_open_turns(
    tmp_path: Path,
) -> None:
    """Probe 23: admission keeps the idle turn and persistent chat unchanged."""

    database = tmp_path / "archive-admission.db"
    with AuthorityOwner.open(database, "owner-archive-admission") as owner:
        admitted: list[str] = []
        for role in (WriterRole.BUILDER, WriterRole.DM):
            suffix = role.value
            campaign_id = f"campaign-archive-{suffix}"
            session_id = f"session-archive-{suffix}"
            turn_id = f"turn-archive-{suffix}"
            _campaign(owner, campaign_id=campaign_id, writer=role, turn_id=turn_id)
            owner.create_agent_session(
                campaign_id=campaign_id,
                role=role,
                provider_id=f"synthetic-{suffix}-provider",
                model_id=f"synthetic-{suffix}-model",
                session_id=session_id,
                selected_skill="BUILD CAMPAIGN PLAN" if role is WriterRole.BUILDER else None,
            )
            before = owner.snapshot(campaign_id)
            admission = owner.admit_archive(campaign_id=campaign_id, expected_revision=0)
            assert owner.current_open_turn(campaign_id) == turn_id
            after = owner.snapshot(campaign_id)
            assert owner.session_context(session_id).session.status.value == "active"
            assert admission == after
            assert after.lifecycle == before.lifecycle
            assert after.revision == before.revision
            assert after.writer == before.writer
            admitted.append(campaign_id)
        print("F4 PROBE 23", "admitted", tuple(admitted), "lifecycle_effect", "none")


def _copy_sqlite_database(source: Path, destination: Path) -> None:
    """Copy one closed synthetic SQLite authority through SQLite's backup API."""

    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _f3_relational_snapshot(
    database: Path, table_names: tuple[str, ...]
) -> dict[str, tuple[str, ...]]:
    """Capture every pre-F4 row without treating migration-4 columns as predecessor truth."""

    connection = sqlite3.connect(database)
    try:
        snapshot: dict[str, tuple[str, ...]] = {}
        for table_name in table_names:
            if table_name == "schema_migrations":
                query = (
                    "SELECT version, applied_name, sql_sha256 FROM schema_migrations "
                    "WHERE version <= 3"
                )
            elif table_name == "campaign_revisions":
                query = (
                    "SELECT campaign_id, revision, action_id, activation_id FROM campaign_revisions"
                )
            else:
                query = f'SELECT * FROM "{table_name}"'
            snapshot[table_name] = tuple(
                sorted(repr(tuple(row)) for row in connection.execute(query))
            )
        return snapshot
    finally:
        connection.close()


def test_probe_24_exact_f3_first_use_migration_and_deterministic_reopen(
    tmp_path: Path,
) -> None:
    """Probe 24: first F4 use upgrades exact F3 bytes while preserving all predecessor truth."""

    from sylvia_gm.infrastructure.sqlite_authority import (
        _MIGRATION_1_NAME,
        _MIGRATION_1_SHA256,
        _MIGRATION_2_NAME,
        _MIGRATION_2_SHA256,
        _MIGRATION_3_NAME,
        _MIGRATION_3_SHA256,
        _MIGRATION_4_NAME,
        _MIGRATION_4_SHA256,
    )

    database = tmp_path / "exact-f3-first-f4-use.db"
    calls: list[int] = []

    def observe_without_mutation(context: MechanicContext) -> MechanicOutcome:
        steps = int(context.parameters["steps"])
        calls.append(steps)
        before = int(context.state["clock.tick"])
        return MechanicOutcome(
            result={"before": before, "after": before + steps},
            consequences=({"kind": "clock_observed", "after": before + steps},),
        )

    with AuthorityOwner.open(
        database,
        "owner-exact-f3",
        disconnect_grace_seconds=2.0,
    ) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="campaign-exact-f3",
            writer=WriterRole.BUILDER,
            turn_id="turn-exact-f3",
            calls=calls,
            mechanic=observe_without_mutation,
        )
        entry = owner.current_package_lock("campaign-exact-f3").package_lock.entries[0]
        receipt = owner.execute_action(
            ActionCommand(
                campaign_id="campaign-exact-f3",
                action_id="action-exact-f3",
                idempotency_key="idem-exact-f3",
                owner_id="owner-exact-f3",
                writer=WriterRole.BUILDER,
                expected_revision=0,
                turn_id="turn-exact-f3",
                package_id=entry.package_id,
                tool_name="advance",
                parameters={"steps": 2},
            )
        )
        owner.start_narration_attempt(
            campaign_id="campaign-exact-f3",
            attempt_id="attempt-exact-f3",
            turn_id="turn-exact-f3",
            cancel=lambda: None,
        )
        owner.emit_narration_token(
            "campaign-exact-f3",
            "attempt-exact-f3",
            ProseChannel.TALK,
            "Exact F3 narration remains canonical.",
        )
        owner.complete_narration_attempt("campaign-exact-f3", "attempt-exact-f3")
        predecessor_snapshot = owner.snapshot("campaign-exact-f3")
        predecessor_candidates = owner.package_candidates()
        predecessor_lock = owner.current_package_lock("campaign-exact-f3")
        predecessor_lineage = owner.package_lock_lineage("campaign-exact-f3")
        assert receipt.resulting_revision == 1 and calls == [2]

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
        f3_tables = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        migration_1_to_3 = tuple(
            connection.execute(
                "SELECT version, applied_name, sql_sha256 FROM schema_migrations ORDER BY version"
            )
        )
    finally:
        connection.close()
    exact_f3_rows = _f3_relational_snapshot(database, f3_tables)
    assert migration_1_to_3 == (
        (
            1,
            "f1_neutral_authority",
            "6c416e59e5d38a3018845a8cb320b24e3bff74a169377735db048c01d4bff8d1",
        ),
        (
            2,
            "f2_provisional_event_protocol",
            "71e1c58545c1f8ce9f8bca24810987648a35c4779bbcdeecff947c8181b1b193",
        ),
        (
            3,
            "f3_sqlite_package_library",
            "58e3af1675b8232a8ef83211f8fe49161ec464a04bbae065db436a909d70ea3f",
        ),
    )
    assert migration_1_to_3 == (
        (1, _MIGRATION_1_NAME, _MIGRATION_1_SHA256),
        (2, _MIGRATION_2_NAME, _MIGRATION_2_SHA256),
        (3, _MIGRATION_3_NAME, _MIGRATION_3_SHA256),
    )

    with AuthorityOwner.open(database, "owner-first-f4-use") as owner:
        assert owner.snapshot("campaign-exact-f3") == predecessor_snapshot
        assert owner.package_candidates() == predecessor_candidates
        assert owner.current_package_lock("campaign-exact-f3") == predecessor_lock
        assert owner.package_lock_lineage("campaign-exact-f3") == predecessor_lineage
        session = owner.create_agent_session(
            campaign_id="campaign-exact-f3",
            role=WriterRole.BUILDER,
            provider_id="synthetic-migration-provider",
            model_id="synthetic-migration-model",
            session_id="session-exact-f3-migrated",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        assert owner.snapshot("campaign-exact-f3") == predecessor_snapshot

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (4,)
        f4_tables = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        migrations = tuple(
            connection.execute(
                "SELECT version, applied_name, sql_sha256 FROM schema_migrations ORDER BY version"
            )
        )
        assert tuple(connection.execute("PRAGMA integrity_check")) == (("ok",),)
        assert tuple(connection.execute("PRAGMA foreign_key_check")) == ()
    finally:
        connection.close()
    assert set(f4_tables) - set(f3_tables) == {
        "agent_sessions",
        "agent_session_messages",
        "campaign_plans",
        "builder_dm_handoffs",
    }
    assert _f3_relational_snapshot(database, f3_tables) == exact_f3_rows
    assert migrations == (
        *migration_1_to_3,
        (
            4,
            "f4_builder_dm_sessions",
            "e0f2766f3f18d160493f4008ac920b79d0079c6475e6f59a970f05615b73e9fb",
        ),
    )
    assert migrations[-1] == (4, _MIGRATION_4_NAME, _MIGRATION_4_SHA256)

    with AuthorityOwner.open(database, "owner-f4-deterministic-reopen") as reopened:
        assert reopened.snapshot("campaign-exact-f3") == predecessor_snapshot
        assert reopened.package_candidates() == predecessor_candidates
        assert reopened.current_package_lock("campaign-exact-f3") == predecessor_lock
        assert reopened.package_lock_lineage("campaign-exact-f3") == predecessor_lineage
        assert reopened.session_context(session.session_id).session == session
    assert _f3_relational_snapshot(database, f3_tables) == exact_f3_rows
    print(
        "F4 PROBE 24 MIGRATION",
        "version",
        "3->4",
        "migrations",
        migrations,
        "tables_added",
        tuple(sorted(set(f4_tables) - set(f3_tables))),
        "campaign",
        predecessor_snapshot.campaign_id,
        "revision",
        predecessor_snapshot.revision,
        "events",
        tuple(event.event_type.value for event in predecessor_snapshot.events),
        "candidate",
        predecessor_candidates[0].candidate_id,
        "lock",
        predecessor_lock.lock_id,
        "session",
        session.session_id,
        "integrity",
        "ok",
        "foreign_keys",
        0,
    )


def _build_f4_startup_integrity_fixture(database: Path) -> dict[str, str]:
    """Create one fully handed-off synthetic authority graph for startup corruption probes."""

    with AuthorityOwner.open(database, "owner-integrity-fixture") as owner:
        request = _prepare_handoff(owner, suffix="integrity")
        campaign_id = str(request["campaign_id"])
        builder_session_id = str(request["builder_session_id"])
        child = owner.create_builder_child_session(
            parent_session_id=builder_session_id,
            provider_id="synthetic-child-provider-integrity",
            model_id="synthetic-child-model-integrity",
            session_id="session-child-integrity",
            selected_skill="BUILD CAMPAIGN PLAN",
            scope=("synthetic-child-proof",),
        )
        owner.return_builder_child_evidence(
            child_session_id=child.session_id,
            evidence_id="evidence-child-integrity",
            content_sha256=hashlib.sha256(b"synthetic child integrity evidence").hexdigest(),
            safe_summary="Synthetic child evidence is ready for parent validation.",
            claims=("bounded-child-proof",),
        )
        _campaign(
            owner,
            campaign_id="campaign-integrity-other",
            writer=WriterRole.BUILDER,
            turn_id="turn-integrity-other",
        )
        owner.create_agent_session(
            campaign_id="campaign-integrity-other",
            role=WriterRole.BUILDER,
            provider_id="synthetic-other-provider",
            model_id="synthetic-other-model",
            session_id="session-integrity-other",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        owner.complete_turn(campaign_id, str(request["completed_turn_id"]))
        handoff = owner.handoff_builder_to_dm(**request)
        return {
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "child_session_id": child.session_id,
            "dm_session_id": handoff.dm_session_id,
            "plan_id": handoff.plan_id,
            "handoff_id": handoff.handoff_id,
            "other_lock_id": owner.current_package_lock("campaign-integrity-other").lock_id,
            "other_candidate_id": "candidate-install-campaign-integrity-other",
        }


def _assert_startup_fails_twice_and_releases_lock(database: Path, label: str) -> None:
    for attempt in (1, 2):
        unexpected = None
        try:
            unexpected = AuthorityOwner.open(database, f"owner-corrupt-{label}-{attempt}")
        except SchemaIdentityError:
            continue
        finally:
            if unexpected is not None:
                unexpected.close()
        pytest.fail(f"startup admitted F4 corruption {label!r} on attempt {attempt}")


@pytest.mark.parametrize(
    ("label", "mutation"),
    (
        (
            "session-provider",
            "UPDATE agent_sessions SET provider_id = 'tampered-provider' "
            "WHERE session_id = 'session-builder-integrity'",
        ),
        (
            "session-model",
            "UPDATE agent_sessions SET model_id = 'tampered-model' "
            "WHERE session_id = 'session-dm-integrity'",
        ),
        (
            "session-status",
            "UPDATE agent_sessions SET status = 'completed' "
            "WHERE session_id = 'session-builder-integrity'",
        ),
        (
            "session-role",
            "PRAGMA ignore_check_constraints = ON; "
            "UPDATE agent_sessions SET role = 'dm' "
            "WHERE session_id = 'session-builder-integrity'",
        ),
        (
            "session-parent",
            "UPDATE agent_sessions SET parent_session_id = 'session-child-integrity' "
            "WHERE session_id = 'session-child-integrity'",
        ),
        (
            "session-skill",
            "UPDATE agent_sessions SET selected_skill = 'BUILD RULESET' "
            "WHERE session_id = 'session-builder-integrity'",
        ),
        (
            "session-campaign",
            "UPDATE agent_sessions SET campaign_id = 'campaign-integrity-other' "
            "WHERE session_id = 'session-child-integrity'",
        ),
        (
            "plan-content-hash",
            "UPDATE campaign_plans SET content_sha256 = '0' || substr(content_sha256, 2) "
            "WHERE plan_id = 'plan-handoff-integrity'",
        ),
        (
            "plan-provenance",
            "UPDATE campaign_plans SET provenance_json = '[\"evidence-child-integrity\"]' "
            "WHERE plan_id = 'plan-handoff-integrity'",
        ),
        (
            "plan-validation",
            "PRAGMA ignore_check_constraints = ON; "
            "UPDATE campaign_plans SET validation_outcome = 'invalid' "
            "WHERE plan_id = 'plan-handoff-integrity'",
        ),
        (
            "plan-revision",
            "UPDATE campaign_plans SET plan_revision = 2 WHERE plan_id = 'plan-handoff-integrity'",
        ),
        (
            "plan-campaign-revision",
            "UPDATE campaign_plans SET resulting_campaign_revision = 2 "
            "WHERE plan_id = 'plan-handoff-integrity'",
        ),
        (
            "plan-lock-binding",
            "UPDATE campaign_plans SET package_lock_id = "
            "(SELECT current_lock_id FROM campaigns WHERE campaign_id = "
            "'campaign-integrity-other') "
            "WHERE plan_id = 'plan-handoff-integrity'",
        ),
        (
            "handoff-source-session",
            "UPDATE builder_dm_handoffs SET builder_session_id = 'session-child-integrity' "
            "WHERE handoff_id = 'handoff-integrity'",
        ),
        (
            "handoff-plan-result",
            "UPDATE builder_dm_handoffs SET plan_revision = 2 "
            "WHERE handoff_id = 'handoff-integrity'",
        ),
        (
            "handoff-writer",
            "UPDATE campaigns SET writer = 'builder' "
            "WHERE campaign_id = 'campaign-handoff-integrity'",
        ),
        (
            "handoff-revision",
            "PRAGMA ignore_check_constraints = ON; "
            "UPDATE builder_dm_handoffs SET expected_revision = 0 "
            "WHERE handoff_id = 'handoff-integrity'",
        ),
        (
            "handoff-dm-session",
            "UPDATE builder_dm_handoffs SET dm_session_id = 'session-child-integrity' "
            "WHERE handoff_id = 'handoff-integrity'",
        ),
        (
            "handoff-candidate",
            "UPDATE builder_dm_handoffs SET package_candidate_id = "
            "'candidate-install-campaign-integrity-other' "
            "WHERE handoff_id = 'handoff-integrity'",
        ),
        (
            "handoff-payload",
            "UPDATE builder_dm_handoffs SET payload_sha256 = '0' || substr(payload_sha256, 2) "
            "WHERE handoff_id = 'handoff-integrity'",
        ),
    ),
)
def test_probe_24_startup_rejects_each_contradictory_f4_authority_field_and_releases_lock(
    tmp_path: Path,
    label: str,
    mutation: str,
) -> None:
    """Probe 24: every authoritative F4 graph contradiction fails closed on every fresh open."""

    baseline = tmp_path / "f4-integrity-baseline.db"
    _build_f4_startup_integrity_fixture(baseline)
    corrupted = tmp_path / f"f4-integrity-{label}.db"
    _copy_sqlite_database(baseline, corrupted)
    connection = sqlite3.connect(corrupted)
    try:
        connection.executescript(mutation)
        connection.commit()
    finally:
        connection.close()

    _assert_startup_fails_twice_and_releases_lock(corrupted, label)
    print("F4 PROBE 24 STARTUP", label, "SchemaIdentityError", "lock_released_twice")


def test_probe_25_durable_map_labels_all_frozen_probes_and_parent_owned_93_gate() -> None:
    """Probe 25: the durable 1–26 map names exact behavior and the protected parent gate."""

    assert tuple(FROZEN_F4_PROBE_MAP) == tuple(range(1, 27))
    assert tuple(FROZEN_F4_PROBE_EVIDENCE) == tuple(range(1, 27))
    assert FROZEN_F4_PROBE_MAP[25].startswith("The accepted 93 F1–F3 tests remain green.")
    assert "PROTECTED PARENT-OWNED GATE" in FROZEN_F4_PROBE_MAP[25]
    assert all(
        callable(globals().get(test_name))
        for test_names in FROZEN_F4_PROBE_EVIDENCE.values()
        for test_name in test_names
    )
    print(
        "F4 PROBE 25 MAP",
        tuple(
            (number, FROZEN_F4_PROBE_MAP[number], FROZEN_F4_PROBE_EVIDENCE[number])
            for number in FROZEN_F4_PROBE_MAP
        ),
    )


def _ast_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _ast_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def test_probe_26_admitted_f4_source_and_behavior_are_strictly_synthetic_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe 26: no real service, plugin, credential, workflow, or second-owner seam exists."""

    repository = Path(__file__).resolve().parents[1]
    forbidden_import_roots = {
        "aiohttp",
        "dotenv",
        "httpx",
        "keyring",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    forbidden_calls = {
        "importlib.import_module",
        "importlib.metadata.entry_points",
        "os.getenv",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
        "subprocess.run",
        "urllib.request.urlopen",
    }
    forbidden_architecture_names = {
        "budget",
        "checkpoint",
        "cost",
        "fallback",
        "job",
        "outbox",
        "plugin",
        "queue",
        "scheduler",
        "workflow",
    }
    dnd_leakage = ("d&d", "dnd", "armor class", "hit points", "initiative roll")
    sqlite_connectors: set[str] = set()
    audited: list[str] = []

    for relative in F4_PRODUCTION_PATHS:
        source = (repository / relative).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        audited.append(relative)
        imports: set[str] = set()
        calls: set[str] = set()
        names: set[str] = set()
        strings: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module)
            elif isinstance(node, ast.Call):
                calls.add(_ast_name(node.func))
            elif isinstance(node, (ast.Name, ast.Attribute)):
                names.add(_ast_name(node))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.append(node.value.casefold())
        assert not {item.split(".", 1)[0] for item in imports} & forbidden_import_roots
        assert not calls & forbidden_calls
        assert "os.environ" not in names
        assert not {item.casefold() for item in names} & forbidden_architecture_names
        assert not any(".env" in item or "credential" in item for item in strings)
        assert not any(token in item for token in dnd_leakage for item in strings)
        if "sqlite3.connect" in calls:
            sqlite_connectors.add(relative)

    assert sqlite_connectors == {"src/sylvia_gm/infrastructure/sqlite_authority.py"}

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    blocked_calls: list[str] = []

    def blocked(name: str) -> Callable[..., object]:
        def deny(*_args: object, **_kwargs: object) -> object:
            blocked_calls.append(name)
            raise AssertionError(f"forbidden external capability used: {name}")

        return deny

    monkeypatch.setattr(socket, "create_connection", blocked("network"))
    monkeypatch.setattr(subprocess, "Popen", blocked("subprocess"))
    monkeypatch.setattr(subprocess, "run", blocked("subprocess"))
    monkeypatch.setattr(importlib.metadata, "entry_points", blocked("plugin-discovery"))
    monkeypatch.setattr(os, "getenv", blocked("environment"))

    database = tmp_path / "synthetic-boundary.db"
    provider_calls: list[str] = []
    with AuthorityOwner.open(
        database, "owner-synthetic-boundary", disconnect_grace_seconds=2.0
    ) as owner:
        _campaign(
            owner,
            campaign_id="campaign-synthetic-boundary",
            writer=WriterRole.BUILDER,
            turn_id="turn-synthetic-boundary",
        )
        owner.create_agent_session(
            campaign_id="campaign-synthetic-boundary",
            role=WriterRole.BUILDER,
            provider_id="injected-fake-provider",
            model_id="injected-fake-model",
            session_id="session-synthetic-boundary",
            selected_skill="BUILD CAMPAIGN SETTING",
        )

        def fake_provider(_input: object) -> object:
            provider_calls.append("injected")
            yield ProviderEvent.talk("Only injected synthetic evidence was used.")

        CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id="session-synthetic-boundary",
                turn_id="turn-synthetic-boundary",
                attempt_id="attempt-synthetic-boundary",
                message_id="message-synthetic-boundary",
                user_text="Use only this synthetic prompt.",
            ),
            fake_provider,
        )
        before = owner.snapshot("campaign-synthetic-boundary")
        candidate = owner.publish_builder_package_candidate(
            builder_session_id="session-synthetic-boundary",
            archive=io.BytesIO(_zero_capability_archive()),
            install_id="install-synthetic-boundary",
        )
        assert owner.snapshot("campaign-synthetic-boundary") == before
        competing = None
        try:
            with pytest.raises(AuthorityError):
                competing = AuthorityOwner.open(database, "forbidden-second-owner")
        finally:
            if competing is not None:
                competing.close()

    assert provider_calls == ["injected"]
    assert blocked_calls == []
    print(
        "F4 PROBE 26 SYNTHETIC BOUNDARY",
        "audited",
        tuple(audited),
        "provider_calls",
        provider_calls,
        "candidate",
        candidate.candidate_id,
        candidate.status.value,
        "external_calls",
        blocked_calls,
        "sqlite_owner",
        tuple(sqlite_connectors),
    )


def test_probe_26_readable_builder_to_dm_end_to_end_authority_proof(tmp_path: Path) -> None:
    """Probe 26: readable synthetic Builder journey ends with sole live DM authority."""

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    database = tmp_path / "builder-to-dm-readable.db"
    campaign_id = "campaign-readable-e2e"
    turn_id = "turn-readable-e2e"
    review_session_id = "session-readable-review"
    builder_session_id = "session-readable-builder"
    child_session_id = "session-readable-child"
    dm_session_id = "session-readable-dm"
    review_context = None

    with AuthorityOwner.open(
        database, "owner-readable-start", disconnect_grace_seconds=2.0
    ) as owner:
        _campaign(
            owner,
            campaign_id=campaign_id,
            writer=WriterRole.BUILDER,
            turn_id=turn_id,
        )
        owner.create_agent_session(
            campaign_id=campaign_id,
            role=WriterRole.BUILDER,
            provider_id="injected-review-provider",
            model_id="injected-review-model",
            session_id=review_session_id,
            selected_skill="BUILD RULESET",
        )
        CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id=review_session_id,
                turn_id=turn_id,
                attempt_id="attempt-readable-review",
                message_id="message-readable-review",
                user_text="Check this synthetic unsupported capability.",
            ),
            lambda _input: iter(
                (ProviderEvent.talk("The unsupported capability needs human review."),)
            ),
        )
        review_context = owner.session_context(review_session_id)

    with AuthorityOwner.open(
        database, "owner-readable-resume", disconnect_grace_seconds=2.0
    ) as owner:
        assert owner.session_context(review_session_id) == review_context
        review = owner.require_builder_human_review(
            session_id=review_session_id,
            review_id="review-readable-unsupported",
            unsupported_capabilities=("synthetic-unsupported-capability",),
            contradictions=(),
        )
        assert review.status.value == "review_required"
        assert owner.stop_agent_session(review_session_id).status.value == "stopped"

        parent = owner.create_agent_session(
            campaign_id=campaign_id,
            role=WriterRole.BUILDER,
            provider_id="injected-builder-provider",
            model_id="injected-builder-model",
            session_id=builder_session_id,
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        user_evidence = owner.record_builder_evidence(
            session_id=parent.session_id,
            evidence_id="evidence-readable-user",
            source_kind="user_provided",
            content_sha256=hashlib.sha256(b"readable synthetic premise").hexdigest(),
            source_locator=None,
            safe_excerpt="A synthetic city follows a documented clock.",
            claims=("clock-city",),
        )

        before_candidate = owner.snapshot(campaign_id)
        exact_lock = owner.current_package_lock(campaign_id)
        assert exact_lock.package_lock == PackageLock(entries=())
        assert owner.package_candidates() == ()
        assert owner.snapshot(campaign_id) == before_candidate
        assert owner.current_package_lock(campaign_id) == exact_lock
        assert len(owner.package_lock_lineage(campaign_id)) == 1

        child = owner.create_builder_child_session(
            parent_session_id=parent.session_id,
            provider_id="injected-child-provider",
            model_id="injected-child-model",
            session_id=child_session_id,
            selected_skill="BUILD CAMPAIGN PLAN",
            scope=("bounded-chapter-evidence",),
        )
        before_child = owner.snapshot(campaign_id)
        CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id=child.session_id,
                turn_id=turn_id,
                attempt_id="attempt-readable-child",
                message_id="message-readable-child",
                user_text="Return one bounded synthetic chapter outline.",
            ),
            lambda _input: iter((ProviderEvent.talk("CHILD PRIVATE DRAFT"),)),
        )
        child_evidence = owner.return_builder_child_evidence(
            child_session_id=child.session_id,
            evidence_id="evidence-readable-child",
            content_sha256=hashlib.sha256(b"readable child evidence").hexdigest(),
            safe_summary="A bounded chapter outline is ready for parent validation.",
            claims=("bounded-outline",),
        )
        assert child_evidence.authoritative is False
        after_child = owner.snapshot(campaign_id)
        assert (
            after_child.state,
            after_child.package_lock,
            after_child.revision,
            after_child.writer,
        ) == (
            before_child.state,
            before_child.package_lock,
            before_child.revision,
            before_child.writer,
        )
        assert owner.session_context(child.session_id).session.status.value == "completed"
        parent_context = owner.session_context(parent.session_id)
        assert "CHILD PRIVATE DRAFT" not in str(parent_context.messages)
        assert parent_context.messages[-1].content["authoritative"] is False

        content = {
            "title": "Readable Synthetic Campaign Plan",
            "facts": ["clock-city", "bounded-outline"],
        }
        content_sha256 = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
        plan = owner.admit_campaign_plan(
            plan_id="plan-readable-e2e",
            campaign_id=campaign_id,
            builder_session_id=parent.session_id,
            expected_revision=0,
            expected_package_lock_id=exact_lock.lock_id,
            content=content,
            content_sha256=content_sha256,
            provenance_evidence_ids=(user_evidence.evidence_id, child_evidence.evidence_id),
            validation_outcome="valid",
            acceptance_status="accepted",
        )
        assert (
            plan.content_sha256,
            plan.provenance_evidence_ids,
            plan.validation_outcome.value,
            plan.acceptance_status.value,
            plan.plan_revision,
            plan.resulting_campaign_revision,
        ) == (
            content_sha256,
            (user_evidence.evidence_id, child_evidence.evidence_id),
            "valid",
            "accepted",
            1,
            1,
        )

        owner.complete_turn(campaign_id, turn_id)
        builder_history = owner.session_context(parent.session_id).messages
        before_handoff = owner.snapshot(campaign_id)
        handoff = owner.handoff_builder_to_dm(
            handoff_id="handoff-readable-e2e",
            campaign_id=campaign_id,
            builder_session_id=parent.session_id,
            dm_session_id=dm_session_id,
            dm_provider_id="injected-dm-provider",
            dm_model_id="injected-dm-model",
            expected_writer=WriterRole.BUILDER,
            expected_revision=1,
            expected_package_lock_id=exact_lock.lock_id,
            expected_package_candidate_id=None,
            plan_id=plan.plan_id,
            expected_plan_revision=plan.plan_revision,
            expected_plan_content_sha256=plan.content_sha256,
            completed_turn_id=turn_id,
        )
        after_handoff = owner.snapshot(campaign_id)
        assert handoff.expected_revision == before_handoff.revision == 1
        assert handoff.resulting_campaign_revision == after_handoff.revision == 2
        assert after_handoff.writer is WriterRole.DM
        assert after_handoff.package_lock == before_handoff.package_lock
        assert owner.package_lock_lineage(campaign_id) == (exact_lock,)

        persistent_builder = owner.session_context(parent.session_id)
        active_dm = owner.session_context(dm_session_id).session
        assert persistent_builder.session.status.value == "active"
        assert persistent_builder.messages == builder_history
        assert active_dm.role is WriterRole.DM and active_dm.status.value == "active"
        assert set(persistent_builder.session.__dataclass_fields__) == {
            "session_id",
            "campaign_id",
            "role",
            "provider_id",
            "model_id",
            "status",
            "parent_session_id",
            "selected_skill",
            "scope",
            "reasoning_effort",
            "reasoning_effort_once",
        }

        loop = CampaignSessionLoop(owner)
        with pytest.raises(CommandRejected, match="writer authority"):
            loop.run(
                SessionRunRequest(
                    session_id=parent.session_id,
                    turn_id=turn_id,
                    attempt_id="attempt-readable-closed-builder",
                    message_id="message-readable-closed-builder",
                    user_text="Try to use the DM gameplay loop from Builder.",
                ),
                lambda _input: iter((ProviderEvent.talk("MUST NOT RUN"),)),
            )

        dm_result = loop.run(
            SessionRunRequest(
                session_id=dm_session_id,
                turn_id=turn_id,
                attempt_id="attempt-readable-dm",
                message_id="message-readable-dm",
                user_text="Continue under DM gameplay authority.",
            ),
            lambda _input: iter((ProviderEvent.talk("DM gameplay authority is active."),)),
        )
        assert tuple(event.safe_payload["text"] for event in dm_result.canonical) == (
            "DM gameplay authority is active.",
        )

        connection = sqlite3.connect(database)
        try:
            session_statuses = tuple(
                connection.execute(
                    "SELECT session_id, role, status FROM agent_sessions "
                    "WHERE campaign_id = ? ORDER BY session_id",
                    (campaign_id,),
                )
            )
            live_writers = tuple(
                connection.execute(
                    "SELECT role, COUNT(*) FROM agent_sessions WHERE campaign_id = ? "
                    "AND status = 'active' GROUP BY role ORDER BY role",
                    (campaign_id,),
                )
            )
            revision_causes = tuple(
                connection.execute(
                    "SELECT revision, plan_id, handoff_id FROM campaign_revisions "
                    "WHERE campaign_id = ? ORDER BY revision",
                    (campaign_id,),
                )
            )
        finally:
            connection.close()
        assert live_writers == (("builder", 1), ("dm", 1))
        assert revision_causes == (
            (0, None, None),
            (1, plan.plan_id, None),
            (2, None, handoff.handoff_id),
        )
        print(
            "F4 PROBE 26 BUILDER-TO-DM E2E",
            "campaign",
            campaign_id,
            "review",
            f"{review_session_id}=stopped",
            "builder",
            f"{builder_session_id}=active",
            "child",
            f"{child_session_id}=completed/non-authoritative",
            "candidate",
            "none-created-for-no-rules",
            "lock",
            exact_lock.lock_id,
            "plan",
            f"{plan.plan_id}=valid/accepted/{plan.plan_revision}",
            "provenance",
            plan.provenance_evidence_ids,
            "handoff",
            handoff.handoff_id,
            "revision",
            "0->1(plan)->2(handoff)",
            "dm",
            f"{dm_session_id}=active",
            "live_writers",
            live_writers,
            "sessions",
            session_statuses,
            "chronology",
            revision_causes,
        )


def test_probe_26_static_and_runtime_synthetic_only_exclusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe 26: F4 is injected/synthetic only and admits no hidden production machinery."""

    import ast
    import os
    import re
    import socket
    import subprocess
    import urllib.parse
    import urllib.request

    from sylvia_gm.agent import _skills as skill_module

    repository = Path(__file__).resolve().parents[1]
    production_relative_paths = (
        "src/sylvia_gm/domain/authority.py",
        "src/sylvia_gm/application/authority.py",
        "src/sylvia_gm/infrastructure/sqlite_authority.py",
        "src/sylvia_gm/domain/session.py",
        "src/sylvia_gm/application/session.py",
        "src/sylvia_gm/infrastructure/sqlite_session.py",
        "src/sylvia_gm/agent/_session_loop.py",
        "src/sylvia_gm/agent/_skills.py",
    )
    skill_relative_paths = (
        "src/sylvia_gm/agent/skills/build_ruleset/SKILL.md",
        "src/sylvia_gm/agent/skills/build_campaign_setting/SKILL.md",
        "src/sylvia_gm/agent/skills/build_campaign_plan/SKILL.md",
    )
    forbidden_import_roots = {
        "aiohttp",
        "anthropic",
        "boto3",
        "dbm",
        "google",
        "httpx",
        "importlib",
        "openai",
        "requests",
        "shelve",
        "socket",
        "sqlalchemy",
        "sqlmodel",
        "subprocess",
        "tinydb",
        "urllib",
    }
    forbidden_identifier_tokens = {
        "armor",
        "budget",
        "compile",
        "cost",
        "credential",
        "d20",
        "discover",
        "discovery",
        "dnd",
        "dotenv",
        "environ",
        "eval",
        "exec",
        "fallback",
        "getenv",
        "hitpoint",
        "hitpoints",
        "job",
        "outbox",
        "plugin",
        "queue",
        "scheduler",
        "spell",
    }
    sqlite_connect_sites: list[str] = []
    inspected_import_roots: set[str] = set()
    for relative_path in production_relative_paths:
        source = (repository / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative_path)
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    inspected_import_roots.add(root)
                    aliases[alias.asname or root] = alias.name
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                inspected_import_roots.add(root)
                for alias in node.names:
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        assert not (inspected_import_roots & forbidden_import_roots)
        for node in ast.walk(tree):
            identifier = None
            if isinstance(node, ast.Name):
                identifier = node.id
            elif isinstance(node, ast.Attribute):
                identifier = node.attr
            if identifier is not None:
                tokens = {token for token in re.split(r"[^a-z0-9]+", identifier.lower()) if token}
                assert not (tokens & forbidden_identifier_tokens), (
                    relative_path,
                    node.lineno,
                    tokens & forbidden_identifier_tokens,
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
            ):
                qualified = (
                    f"{aliases.get(node.func.value.id, node.func.value.id)}.{node.func.attr}"
                )
                if qualified == "sqlite3.connect":
                    sqlite_connect_sites.append(relative_path)
    assert set(sqlite_connect_sites) == {"src/sylvia_gm/infrastructure/sqlite_authority.py"}

    assert tuple(skill_module._SKILL_DIRECTORIES.values()) == (
        "build_ruleset",
        "build_campaign_setting",
        "build_campaign_plan",
    )
    domain_specific_terms = re.compile(
        r"\b(?:dungeons?\s*(?:&|and)\s*dragons?|dnd|d20|armor class|hit points?|spell slots?)\b",
        re.IGNORECASE,
    )
    for relative_path in skill_relative_paths:
        document = (repository / relative_path).read_text(encoding="utf-8")
        assert not domain_specific_terms.search(document), relative_path

    test_tree = ast.parse(Path(__file__).read_text(encoding="utf-8"), filename=__file__)
    for node in test_tree.body:
        if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) or not node.name.startswith("test_"):
            continue
        arguments = {argument.arg for argument in node.args.args}
        opens_authority = any(
            isinstance(descendant, ast.Call)
            and isinstance(descendant.func, ast.Attribute)
            and descendant.func.attr == "open"
            and isinstance(descendant.func.value, ast.Name)
            and descendant.func.value.id == "AuthorityOwner"
            for descendant in ast.walk(node)
        )
        assert not opens_authority or "tmp_path" in arguments, node.name
    external_provider_roots = {
        "anthropic",
        "boto3",
        "google",
        "httpx",
        "openai",
        "requests",
    }
    test_import_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(test_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(test_tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not (test_import_roots & external_provider_roots)

    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionRunRequest,
    )

    database = tmp_path / "synthetic-only-runtime.db"
    allowed_database = database.resolve()
    allowed_migration_root = (tmp_path / "migration-sources").resolve()
    sqlite_targets: list[Path] = []
    original_connect = sqlite3.connect

    def guarded_connect(target: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        if str(target) == ":memory:":
            return original_connect(target, *args, **kwargs)
        target_text = str(target)
        if kwargs.get("uri") and target_text.startswith("file:"):
            parsed = urllib.parse.urlparse(target_text)
            resolved = Path(urllib.request.url2pathname(parsed.path)).resolve()
        else:
            resolved = Path(target_text).resolve()
        assert resolved == allowed_database or resolved.is_relative_to(allowed_migration_root), (
            f"persistence target escaped authority storage: {resolved}"
        )
        sqlite_targets.append(resolved)
        return original_connect(target, *args, **kwargs)

    def forbidden_runtime(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("forbidden runtime service access")

    benign_runtime_environment_reads: list[str] = []

    class _NoEnvironment(dict[str, str]):
        def __getitem__(self, key: str) -> str:
            raise AssertionError(f"credential/environment access admitted: {key}")

        def get(self, key: str, default: object = None) -> object:
            if key == "PYTHONASYNCIODEBUG":
                benign_runtime_environment_reads.append(key)
                return default
            raise AssertionError(f"credential/environment access admitted: {key}")

        def __contains__(self, key: object) -> bool:
            raise AssertionError(f"credential/environment access admitted: {key}")

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    monkeypatch.setattr(socket, "socket", forbidden_runtime)
    monkeypatch.setattr(socket, "create_connection", forbidden_runtime)
    monkeypatch.setattr(subprocess, "Popen", forbidden_runtime)
    monkeypatch.setattr(subprocess, "run", forbidden_runtime)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_runtime)
    monkeypatch.setattr(os, "getenv", forbidden_runtime)
    monkeypatch.setattr(os, "putenv", forbidden_runtime)
    monkeypatch.setattr(os, "unsetenv", forbidden_runtime)
    monkeypatch.setattr(os, "environ", _NoEnvironment())

    with AuthorityOwner.open(
        database, "owner-synthetic-only", disconnect_grace_seconds=2.0
    ) as owner:
        _campaign(
            owner,
            campaign_id="campaign-synthetic-only",
            writer=WriterRole.BUILDER,
            turn_id="turn-synthetic-only",
        )
        session = owner.create_agent_session(
            campaign_id="campaign-synthetic-only",
            role=WriterRole.BUILDER,
            provider_id="injected-fake-provider",
            model_id="synthetic-model",
            session_id="session-synthetic-only",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        result = CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id=session.session_id,
                turn_id="turn-synthetic-only",
                attempt_id="attempt-synthetic-only",
                message_id="message-synthetic-only",
                user_text="Use only the synthetic package archive and temporary authority.",
            ),
            lambda _provider_input: iter(
                (ProviderEvent.talk("Synthetic injected provider completed locally."),)
            ),
        )
        assert owner.session_context(session.session_id).session == session
        assert tuple(event.safe_payload["text"] for event in result.canonical) == (
            "Synthetic injected provider completed locally.",
        )
        assert owner.snapshot("campaign-synthetic-only").revision == 0

    assert sqlite_targets and allowed_database in sqlite_targets
    assert set(benign_runtime_environment_reads) <= {"PYTHONASYNCIODEBUG"}
    assert all(
        target == allowed_database or target.is_relative_to(allowed_migration_root)
        for target in sqlite_targets
    )
    preserved_source = allowed_migration_root / "synthetic-only-runtime.pre-migration-v3.sqlite3"
    assert preserved_source.is_file()
    print(
        "F4 PROBE 26",
        "production_files",
        len(production_relative_paths),
        "skill_documents",
        tuple(skill_relative_paths),
        "external_provider_imports",
        (),
        "network_subprocess_env_calls",
        0,
        "sqlite_owner_targets",
        {"authority": 1, "migration_source_preserved": True},
        "provider",
        "injected-fake-provider",
        "archive",
        "synthetic-in-memory",
        "authority_effect",
        0,
    )


def test_session_loop_start_rejection_cleans_active_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderControl,
        ProviderEvent,
        SessionRunRequest,
        SessionRunResult,
    )

    with AuthorityOwner.open(
        tmp_path / "start-rejection.db",
        "owner-start-rejection",
        disconnect_grace_seconds=2.0,
    ) as owner:
        campaign_id = "campaign-start-rejection"
        session_id = "session-start-rejection"
        _campaign(
            owner,
            campaign_id=campaign_id,
            writer=WriterRole.DM,
            turn_id="turn-start-rejection",
        )
        owner.create_agent_session(
            campaign_id=campaign_id,
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id=session_id,
        )
        loop = CampaignSessionLoop(owner)
        request = SessionRunRequest(
            session_id=session_id,
            turn_id="turn-start-rejection",
            attempt_id="attempt-start-rejection",
            message_id="message-start-rejection",
            user_text="Reject this exact synthetic request at narration admission.",
        )
        provider_calls: list[str] = []
        rejected_controls: list[ProviderControl] = []
        original_start = owner.start_narration_attempt

        def provider(_input: object) -> object:
            provider_calls.append("called")
            yield ProviderEvent.talk("MUST NOT RUN FOR REJECTED ADMISSION")

        def reject_start(**kwargs: object) -> None:
            cancel = kwargs["cancel"]
            control = getattr(cancel, "__self__", None)
            assert isinstance(control, ProviderControl)
            rejected_controls.append(control)
            raise IdempotencyConflict("Narration attempt already has canonical output")

        monkeypatch.setattr(owner, "start_narration_attempt", reject_start)
        start_rejections: list[AuthorityError] = []
        for _ in range(2):
            try:
                loop.run(request, provider)
            except AuthorityError as error:
                start_rejections.append(error)

        history_after_rejections = owner.session_context(session_id)
        active_operation_results: list[tuple[str, str | None, str | None]] = []
        active_operations = (
            ("stop", lambda: loop.stop(session_id)),
            (
                "steer",
                lambda: loop.steer(
                    session_id,
                    "MUST NOT APPEND AFTER REJECTION",
                    message_id="steer-after-rejection",
                ),
            ),
            ("disconnect", lambda: loop.disconnect(session_id, now=1.0)),
            (
                "reconnect",
                lambda: loop.reconnect(
                    session_id,
                    now=1.0,
                    acknowledged_sequence=0,
                ),
            ),
        )
        for name, invoke in active_operations:
            try:
                invoke()
            except CommandRejected as error:
                active_operation_results.append((name, error.code, str(error)))
            else:
                active_operation_results.append((name, None, None))

        print(
            "F4 START REJECTION",
            session_id,
            request.attempt_id,
            [
                (error.__class__.__name__, getattr(error, "code", None), str(error))
                for error in start_rejections
            ],
            "provider_calls",
            len(provider_calls),
            "cancel_counts",
            [control.cancellation_count for control in rejected_controls],
            "interruptions",
            len(loop._last_interruption),
        )
        print(
            "F4 START REJECTION ACTIVE/HISTORY",
            active_operation_results,
            [message.kind.value for message in history_after_rejections.messages],
        )

        assert len(start_rejections) == 2
        assert all(isinstance(error, IdempotencyConflict) for error in start_rejections)
        assert [error.code for error in start_rejections] == [
            "idempotency_conflict",
            "idempotency_conflict",
        ]
        assert [str(error) for error in start_rejections] == [
            "Narration attempt already has canonical output",
            "Narration attempt already has canonical output",
        ]
        assert provider_calls == []
        assert [control.cancellation_count for control in rejected_controls] == [0, 0]
        assert loop._last_interruption == {}
        assert [message.kind.value for message in history_after_rejections.messages] == ["user"]
        assert history_after_rejections.messages[0].message_id == request.message_id
        assert active_operation_results == [
            ("stop", "command_rejected", "Agent session has no active provider work"),
            ("steer", "command_rejected", "Agent session has no active provider work"),
            ("disconnect", "command_rejected", "Agent session has no active provider work"),
            ("reconnect", "command_rejected", "Agent session has no active provider work"),
        ]

        monkeypatch.setattr(owner, "start_narration_attempt", original_start)
        fresh_provider_calls: list[str] = []

        def fresh_provider(_input: object) -> object:
            fresh_provider_calls.append("called")
            yield ProviderEvent.talk("Fresh provider work succeeds after rejected admission.")

        fresh_result = loop.run(
            SessionRunRequest(
                session_id=session_id,
                turn_id="turn-start-rejection",
                attempt_id="attempt-after-start-rejection",
                message_id="message-after-start-rejection",
                user_text="Run fresh synthetic provider work.",
            ),
            fresh_provider,
        )
        history_after_fresh = owner.session_context(session_id)
        history_kinds_after_fresh = tuple(
            message.kind.value for message in history_after_fresh.messages
        )
        print(
            "F4 START REJECTION FRESH RUN",
            fresh_provider_calls,
            history_kinds_after_fresh,
            fresh_result.canonical,
        )
        assert fresh_provider_calls == ["called"]
        assert history_kinds_after_fresh == (
            "user",
            "user",
            "evidence",
            "canonical_reference",
        )
        assert tuple(message.message_id for message in history_after_fresh.messages[:2]) == (
            request.message_id,
            "message-after-start-rejection",
        )
        assert history_after_fresh.messages[2].content["kind"] == "gm_turn_activity"
        assert history_after_fresh.messages[2].content["activity"] == "turn_completed"
        assert tuple(event.safe_payload["text"] for event in fresh_result.canonical) == (
            "Fresh provider work succeeds after rejected admission.",
        )

        failure_provider_calls: list[str] = []

        def failing_provider(_input: object) -> object:
            failure_provider_calls.append("called")
            raise RuntimeError("synthetic provider failure after successful start")

        with pytest.raises(RuntimeError, match="synthetic provider failure after successful start"):
            loop.run(
                SessionRunRequest(
                    session_id=session_id,
                    turn_id="turn-start-rejection",
                    attempt_id="attempt-provider-failure-after-start",
                    message_id="message-provider-failure-after-start",
                    user_text="Start successfully, then fail the provider.",
                ),
                failing_provider,
            )
        interruption = loop._last_interruption[session_id]
        narrator_inputs: list[RetryNarrationInput] = []

        def retry_narrator(retry_input: RetryNarrationInput, stream: NarrationStream) -> None:
            narrator_inputs.append(retry_input)
            stream.emit(ProseChannel.TALK, "Explicit Retry remains available.")

        retry_result = loop.retry(
            session_id,
            retry_id="retry-after-provider-failure",
            narrator=retry_narrator,
        )
        print(
            "F4 START REJECTION PROVIDER FAILURE/RETRY",
            failure_provider_calls,
            interruption.interruption_id,
            interruption.retryable,
            retry_result.replayed,
        )
        assert failure_provider_calls == ["called"]
        assert interruption.retryable
        assert len(narrator_inputs) == 1
        assert narrator_inputs[0].interruption_id == interruption.interruption_id
        assert not retry_result.replayed
        assert tuple(event.safe_payload["text"] for event in retry_result.canonical) == (
            "Explicit Retry remains available.",
        )
        assert isinstance(fresh_result, SessionRunResult)
