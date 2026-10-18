"""Mature-campaign context uses real saved records and recoverable bounded reads."""

import json
import sqlite3
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._stateless_mcp import _safe_error
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.infrastructure import sqlite_continuity
from tests.test_alpha_f4_provider_session_adapter import (
    _OwnerProjection,
    _provider_input,
    _ScriptedProvider,
)


def _saved_memory(filler_count=160, *, completed_id="z-survey-completed"):
    """Include old consequences, a stale plan, later completion and a revised NPC record."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ddl = sqlite_continuity.MIGRATION_9_SQL
    connection.executescript(ddl[ddl.index("CREATE TABLE") :])
    entries = [
        ("a-survey-planned", "fact", "The survey is planned for tomorrow.", "player_known"),
        ("archive-promise", "fact", "We promised Ada a blue cloak on our return.", "player_known"),
        ("captain-route-book", "entity", "The survey has not left.", "player_known"),
        *[
            (f"unrelated-{n:03}", "fact", "Other observation. " * 40, "player_known")
            for n in range(filler_count)
        ],
        (
            completed_id,
            "fact",
            "The survey finished; the captain recorded the safe route.",
            "player_known",
        ),
        (
            "captain-route-book",
            "entity",
            "Survey completed. Upper route tested; lower bank unsafe.",
            "player_known",
        ),
        ("captain-secret", "fact", "The captain privately suspects the archivist.", "gm_private"),
    ]
    for revision, (record_id, kind, text, visibility) in enumerate(entries, 1):
        sqlite_continuity.append_record(
            connection,
            campaign_id="campaign-provider",
            entry_id=f"entry-{revision}",
            record_id=record_id,
            kind=kind,
            status="active",
            payload={
                "text": text,
                "visibility": visibility,
                "known_by": ["player", "captain"] if visibility == "player_known" else ["captain"],
                **(
                    {"entity_kind": "document", "name": "Captain's route book"}
                    if kind == "entity"
                    else {}
                ),
            },
            basis_message_ids=(f"message-{revision}",),
            operation_id=f"operation-{revision}",
            admitted_revision=revision,
        )
    result = sqlite_continuity.read_view(connection, "campaign-provider")
    connection.close()
    return result


def _context(memory, text="We completed the survey. Check the captain's route book.", *, ooc=None):
    """Exercise the same projection the real provider receives, without model inference."""
    adapter = ProviderSessionAdapter(
        _OwnerProjection(continuity=memory),
        _ScriptedProvider([]),
        owner_id="owner-provider",
    )
    return adapter._current_campaign_context_payload(_provider_input(user_text=text, ooc_text=ooc))


def _compact_mature_memory():
    """Reach the record limit without large filler entries masking relevant omissions."""
    memory = _saved_memory(filler_count=300)
    return replace(memory, facts=tuple(
        {**fact, "text": "Other observation."}
        if fact["record_id"].startswith("unrelated") else fact
        for fact in memory.facts
    ))


@pytest.mark.parametrize("question", ["What about the cloak?", "Ada"])
def test_scene_detail_does_not_veto_a_player_memory_cue(question):
    memory = _compact_mature_memory()
    for scene in ({}, {
        "act_id": "act-3", "location_id": "guildhall",
        "summary": "Afternoon after the contract hearing. The party is preparing "
                   "to leave the guildhall for the north road.",
    }):
        context = _context(replace(memory, current_scene=scene), question)
        assert "archive-promise" in {
            fact["record_id"] for fact in context["PLAYER_KNOWN"]["current_facts"]
        }
        assert context["campaign_state"]["memory_selection"]["selected_records"] <= 128


@pytest.mark.parametrize("ic", ["", "I study the sign over the gate."])
def test_ooc_correction_selects_relevant_old_memory_independently_of_ic(ic):
    context = _context(
        _compact_mature_memory(), ic,
        ooc="What did we promise Ada? We should get that blue cloak.",
    )
    assert "archive-promise" in {
        fact["record_id"] for fact in context["PLAYER_KNOWN"]["current_facts"]
    }


@pytest.mark.parametrize("filler_count", [3, 160])
@pytest.mark.parametrize("completed_id", ["z-survey-completed", "a-completed-survey"])
def test_current_outcome_and_entity_do_not_depend_on_alphabetical_position(
    filler_count, completed_id
):
    payload = _context(_saved_memory(filler_count, completed_id=completed_id))
    facts = {item["record_id"]: item for item in payload["PLAYER_KNOWN"]["current_facts"]}
    assert {completed_id, "captain-route-book"} <= facts.keys()
    assert facts["captain-route-book"]["text"].startswith("Survey completed")
    assert facts[completed_id]["admitted_revision"] > 1
    assert facts["captain-route-book"]["record_kind"] == "entity"
    assert "captain-secret" not in facts


def test_older_named_consequence_survives_many_unrelated_recent_records():
    payload = _context(_saved_memory(), "What did we promise Ada? We should get that blue cloak.")
    assert "archive-promise" in {
        item["record_id"] for item in payload["PLAYER_KNOWN"]["current_facts"]
    }


def test_partial_projection_is_explicit_bounded_and_has_working_discovery():
    memory = _saved_memory()
    payload = _context(memory)
    facts = payload["PLAYER_KNOWN"]["current_facts"] + payload["GM_PRIVATE"]["current_facts"]
    selection = payload["campaign_state"]["memory_selection"]
    assert selection["total_records"] == len(memory.facts)
    assert selection["selected_records"] == len(facts) < len(memory.facts)
    assert selection["omitted_records"] == len(memory.facts) - len(facts)
    assert len(facts) <= 128
    assert len(json.dumps(facts, ensure_ascii=False, separators=(",", ":")).encode()) <= 48 * 1024
    assert (
        payload["campaign_state"]["more_state_available_via"]["tool"] == "search_campaign_knowledge"
    )
    assert (
        "campaign_state/captain-route-book"
        in payload["GM_PRIVATE"]["character_knowledge"]["captain"]
    )
    assert "Survey completed. Upper route tested" not in str(
        payload["GM_PRIVATE"]["character_knowledge"]
    )


def test_large_current_read_gives_bounded_exact_read_recovery():
    memory = _saved_memory()
    owner = SimpleNamespace(
        _operation=nullcontext,
        _store=SimpleNamespace(
            snapshot=lambda _: SimpleNamespace(continuity=memory, state={}, revision=200),
            accepted_campaign_plan=lambda _: None,
        ),
    )
    with pytest.raises(CommandRejected) as caught:
        AuthorityOwner.read_campaign_reference(
            owner, "campaign-provider", source="campaign_state", path="current"
        )
    correction = _safe_error(caught.value).structured_content
    assert correction["code"] == "campaign_reference_too_large"
    assert "search_campaign_knowledge" in correction["reason"]
    assert "path" in correction["validation"]["field_paths"]
    found = AuthorityOwner.search_campaign_knowledge(
        owner,
        "campaign-provider",
        query="captain route book",
        sources=["campaign_state"],
        limit=8,
    )
    assert any(item["path"] == "captain-route-book" for item in found["matches"])
    exact = AuthorityOwner.read_campaign_reference(
        owner,
        "campaign-provider",
        source="campaign_state",
        path="captain-route-book",
    )
    data = json.loads(exact["content"])
    assert data["text"].startswith("Survey completed")
    assert data["admitted_revision"] > 3


@pytest.mark.parametrize("limit", [0, 10, 12, True, "12"])
def test_invalid_limit_has_safe_actionable_range(limit):
    with pytest.raises(CommandRejected) as caught:
        AuthorityOwner.search_campaign_knowledge(
            object(), "campaign-provider", query="survey", limit=limit
        )
    correction = _safe_error(caught.value).structured_content
    assert correction["validation"]["field_paths"] == ["limit"]
    assert correction["validation"]["minimum"] == 1
    assert correction["validation"]["maximum"] == 8
    assert correction["state_changed"] is False


def test_unrecognized_error_text_is_not_forwarded_as_lookup_guidance():
    result = _safe_error(CommandRejected("SECRET-CONTENT field 'path'", code="unknown_error"))
    assert "SECRET-CONTENT" not in json.dumps(result.structured_content)
