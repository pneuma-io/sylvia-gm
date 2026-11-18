"""Issue 179: an NPC's quoted line is labelled with the speaker's name, not "Unknown speaker".

On 19 November 2026 the opening of A Debt in Iron (Claude, model sonnet) recorded Quarterkeeper
Ysra in the same Campaign File update that quoted her, but gave her line the anonymous speaker.
The chat could only show "Unknown speaker". These scripted turns replay that reply and the two
ways the current contract lets the GM name her. No live model is used.
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest

from sylvia_gm.agent._provider_session import (
    ProviderSessionAdapter,
    _project_response_blocks,
    _response_format_contract_message,
    _validated_raw_response_blocks,
)
from sylvia_gm.agent._session_loop import ProviderEventKind
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import CampaignContinuityView
from sylvia_gm.provider._base import ProviderStreamChunk
from tests.test_alpha_f4_provider_session_adapter import (
    _campaign_file_tool,
    _OwnerProjection,
    _provider_input,
    _ScriptedProvider,
)

_OPENING = (
    "Quarterkeeper Ysra crosses the hall to you, a folded sheet in one hand and a rolled map "
    "case under her arm."
)
_SPOKEN = (
    "The plate hasn't come back to the Lodge. Captain Eda Venn holds this pledge, and she's "
    "lost her way to the plate."
)
_AFTER = "She taps the map case and waits, letting the pledge rest open between you."
_YSRA = {"entity_id": "ysra", "name": "Quarterkeeper Ysra", "attributes": {}}


def _party() -> tuple[object, ...]:
    return (
        SimpleNamespace(
            participant_id="tavin-bell", kind="player_character",
            data={"name": "Tavin Bell", "party_role": "player_character"},
        ),
        SimpleNamespace(
            participant_id="oren-reed", kind="player_character",
            data={"name": "Oren Reed", "party_role": "companion"},
        ),
    )


def _opening_turn(utterance: dict[str, object]) -> tuple[dict[str, object], ...]:
    """Run one scripted opening reply that records Ysra and quotes her."""

    arguments = {
        "entities": [_YSRA],
        "revised_player_facing_response": [
            {"kind": "narration", "text": _OPENING},
            {"kind": "npc_utterance", **utterance, "spoken_words": _SPOKEN},
            {"kind": "narration", "text": _AFTER},
        ],
    }
    provider = _ScriptedProvider([[ProviderStreamChunk(
        tool_call_delta={"index": 0, "id": "opening", "function": {
            "name": "update_campaign_file", "arguments": json.dumps(arguments),
        }},
        finish_reason="tool_calls",
    )]])
    owner = _OwnerProjection(participants=_party())

    def record_ysra(_parameters: object) -> dict[str, object]:
        owner.continuity = CampaignContinuityView(records=(SimpleNamespace(
            record_id="ysra", kind="entity", status="active", admitted_revision=6,
            payload={"name": "Quarterkeeper Ysra", "visibility": "player_known",
                     "known_by": ["player"]},
        ),))
        return {"status": "campaign_file_updated", "campaign_revision": 6}

    provider_input = replace(
        _provider_input(),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        narrative_writer=record_ysra,
    )
    events = tuple(ProviderSessionAdapter(
        cast(AuthorityOwner, owner), provider, owner_id="owner-provider",
    ).for_turn("turn-provider")(provider_input))
    assert len(provider.calls) == 1
    talk = next(event for event in events if event.kind is ProviderEventKind.TALK)
    assert talk.response_blocks is not None
    assert [block["kind"] for block in talk.response_blocks] == [
        "narration", "npc_dialogue", "narration",
    ]
    assert f"{talk.response_blocks[1]['speaker_label']}: {_SPOKEN}" in talk.text
    return tuple(dict(block) for block in talk.response_blocks)


def test_issue_179_saved_reply_had_no_speaker_name_to_show() -> None:
    """The saved 19 November reply named nobody, so only "Unknown speaker" was possible."""

    blocks = _opening_turn({"speaker_id": "anonymous"})
    assert blocks[1] == {
        "kind": "npc_dialogue", "speaker_label": "Unknown speaker", "text": _SPOKEN,
    }


@pytest.mark.parametrize(
    "utterance",
    [
        pytest.param({"speaker_id": "ysra"}, id="entity-recorded-in-same-update"),
        pytest.param(
            {"speaker_id": "anonymous", "speaker_name": "Quarterkeeper Ysra"},
            id="anonymous-with-model-name",
        ),
    ],
)
def test_issue_179_opening_labels_ysra_by_name(utterance: dict[str, object]) -> None:
    blocks = _opening_turn(utterance)
    assert blocks[1] == {
        "kind": "npc_dialogue", "speaker_label": "Quarterkeeper Ysra", "text": _SPOKEN,
    }
    assert "speaker_id" not in blocks[1]
    assert "speaker_name" not in blocks[1]


def _label(
    utterance: dict[str, object], *, records: tuple[object, ...] = (),
) -> str:
    snapshot, _ = _OwnerProjection(
        participants=_party(), continuity=CampaignContinuityView(records=records),
    ).model_context_snapshot("campaign-provider")
    raw = _validated_raw_response_blocks(
        [{"kind": "npc_utterance", **utterance, "spoken_words": "Wait."}]
    )
    _, blocks = _project_response_blocks(raw, snapshot)
    return str(blocks[0]["speaker_label"])


_HIDDEN = SimpleNamespace(
    record_id="eda-venn", kind="entity", status="active", admitted_revision=3,
    payload={"name": "Captain Eda Venn", "visibility": "gm_private", "known_by": ["gm"]},
)
_KNOWN = SimpleNamespace(
    record_id="ysra", kind="entity", status="active", admitted_revision=3,
    payload={"name": "Quarterkeeper Ysra", "visibility": "player_known", "known_by": ["player"]},
)


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ({"speaker_id": "anonymous", "speaker_name": "  The   gate guard "}, "The gate guard"),
        ({"speaker_id": "anonymous"}, "Unknown speaker"),
        # Campaign authority labels a known speaker, whatever name the model adds.
        ({"speaker_id": "ysra", "speaker_name": "Someone else"}, "Quarterkeeper Ysra"),
        ({"speaker_id": "oren-reed", "speaker_name": "Someone else"}, "Oren Reed"),
        # A name the campaign withholds never becomes a label.
        ({"speaker_id": "eda-venn", "speaker_name": "Captain Eda Venn"}, "Unknown speaker"),
        ({"speaker_id": "anonymous", "speaker_name": "captain eda venn"}, "Unknown speaker"),
        ({"speaker_id": "anonymous", "speaker_name": "Eda Venn"}, "Unknown speaker"),
        ({"speaker_id": "anonymous", "speaker_name": "Captain Eda Venn's mate"},
         "Unknown speaker"),
        ({"speaker_id": "anonymous", "speaker_name": "Tavin Bell"}, "Unknown speaker"),
        ({"speaker_id": "tavin-bell", "speaker_name": "Tavin"}, "Unknown speaker"),
        # Whole-word matching only: a longer name that merely starts the same is still shown.
        ({"speaker_id": "anonymous", "speaker_name": "Tavin Bellamy"}, "Tavin Bellamy"),
    ],
)
def test_model_speaker_name_labels_only_an_anonymous_speaker_with_a_safe_name(
    utterance: dict[str, object], expected: str,
) -> None:
    assert _label(utterance, records=(_HIDDEN, _KNOWN)) == expected


@pytest.mark.parametrize(
    "speaker_name",
    ["", "   ", "x" * 81, "Ysra\nsays", "Ysra\tKell", 7, None],
)
def test_speaker_name_must_be_one_short_line(speaker_name: object) -> None:
    with pytest.raises(ValueError, match="speaker_name"):
        _validated_raw_response_blocks([{
            "kind": "npc_utterance", "speaker_id": "anonymous",
            "speaker_name": speaker_name, "spoken_words": "Wait.",
        }])


def test_ooc_and_narration_blocks_still_refuse_a_speaker_name() -> None:
    with pytest.raises(ValueError, match="fields are invalid"):
        _validated_raw_response_blocks([
            {"kind": "narration", "text": "Rain.", "speaker_name": "The guard"},
        ])


def test_gm_request_says_how_to_name_a_speaker() -> None:
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()), _ScriptedProvider([]),
        owner_id="owner-provider",
    )
    tools, _aliases = adapter._provider_tools(  # noqa: SLF001
        (_campaign_file_tool(allow_unchanged=True),)
    )
    response = tools[0]["function"]["parameters"]["properties"]["revised_player_facing_response"]
    assert "an entity_id this same update records" in response["description"]
    dialogue = next(
        item for item in response["items"]["oneOf"]
        if item["properties"]["kind"]["const"] == "npc_utterance"
    )
    speaker_id = dialogue["properties"]["speaker_id"]["description"]
    assert "an entity_id this same update records in entities" in speaker_id
    assert "Record a named NPC as an entity and use its ID." in speaker_id
    assert "Use anonymous only for a speaker without an ID." in speaker_id
    speaker_name = dialogue["properties"]["speaker_name"]
    assert speaker_name["maxLength"] == 80
    assert "speaker_name" not in dialogue["required"]
    assert dialogue["additionalProperties"] is False

    content = str(_response_format_contract_message()["content"])
    assert "Name every speaker" in content
    assert "speaks by that entity_id" in content
    example = json.loads(content.split("Example layout: ", 1)[1])
    utterances = [block for block in example if block["kind"] == "npc_utterance"]
    assert utterances and all(block.get("speaker_name") for block in utterances)
    assert [block["kind"] for block in _validated_raw_response_blocks(example)] == [
        "narration", "npc_dialogue", "narration", "npc_dialogue",
    ]
