"""Release journeys start through the same Interview/Builder commands as the desktop.

Provider replies are scripted; package admission, tools, persistence and mechanics are real.
No helper inserts opponents or edits campaign state to make combat reachable.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.provider._base import ProviderResponse, ProviderStreamChunk, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider
from tests.qa_attached_request import request_attached

ROOT = Path(__file__).resolve().parents[1]
SRD = "sylvia.srd-lite.bg3"


def _character_reply(name: str = "Journey Hero", *, level: int = 2) -> ProviderResponse:
    """Publish the selected synthetic Fighter level through the ordinary Character Builder."""
    with zipfile.ZipFile(ROOT / "campaign-plans/a-debt-in-iron/setting.sylvia-gm-package") as archive:
        source = json.loads(archive.read("packages/pneuma.severed-lands/characters/tavin-bell.json"))
    participant = source["participant"]
    participant.update(name=name, level=level, classes=[{"id": "fighter", "level": level}])
    state = {
        "max_hp": 12 if level == 1 else 20, "hit_die_size": 10,
        "worn_armor": source["entity_state"]["worn_armor"],
        "worn_shield": source["entity_state"]["worn_shield"],
        "conditions": [], "prepared_spells": [],
        "inventory": source["entity_state"]["inventory"],
        # This scripted Builder explicitly chooses the actual practice spear.
        "weapon_sets": {"format": 1, "active_set": "melee",
                        "melee": {"main_hand": "spear", "off_hand": source["entity_state"].get("worn_shield")},
                        "ranged": {"main_hand": None, "off_hand": None}},
        "available_bonus_actions": ["second_wind"],
        "damage_immunities": [], "damage_resistances": [], "damage_vulnerabilities": [],
        "resources": {"hit_die": level},
        "resource_maxima": {"hit_die": level},
    }
    assert "spear" in state["inventory"]
    if "worn_armor" not in source["entity_state"]:
        state.pop("worn_armor")
        state.pop("worn_shield")
        state["armor_class"] = source["entity_state"]["armor_class"]
    return ProviderResponse(tool_calls=[ToolCall(
        id="save-journey-character",
        name="publish_participant_artifact",
        arguments=json.dumps({
            "summary": f"A level-{level} Human Fighter for the selected adventure.",
            "authored_profile": f"# {name}\n\nA fighter seeking honest sparring work.",
            "participant": participant,
            "entity_state": json.dumps(state),
            "setting_data": "{}",
        }),
    )])


def _start_campaign(
    database: Path, plan_id: str = "the-sparring-debt", *, with_companion: bool = False,
    character_level: int = 2,
) -> tuple[str, str, str]:
    """Use New Campaign, New Character, selection and Ready to Begin without state injection."""
    provider = LibraryProvider(responses=[
        _character_reply(level=character_level), ProviderResponse(content="The character is saved."),
    ])
    harness = _OwnerHarness(database, provider)
    try:
        draft = harness.request("new", "create_guided_campaign_draft", {
            "operation_id": "journey-new", "display_name": "Combat journey",
            "provider_id": "test.transport", "model_id": "test.transport/model0",
        })
        campaign_id = draft["campaign_id"]
        interview_id = draft["builder_session_id"]

        def answer(revision: int, step: str, value: dict) -> None:
            harness.request(step, "advance_guided_campaign_draft", {
                "operation_id": f"journey-{step}",
                "campaign_id": campaign_id, "builder_session_id": interview_id,
                "expected_construction_revision": revision,
                "current_step": step, "visible_answer": step, "answer": value,
            })

        for revision, (step, value) in enumerate((
            ("ruleset", {"candidate_id": SRD}),
            ("setting", {"setting_id": "pneuma.severed-lands"}),
            ("plan", {"plan_id": plan_id}),
            ("name", {"display_name": "Combat journey"}),
        )):
            answer(revision, step, value)
        party = harness.request("available-party", "campaign_party_options", {
            "ruleset_selection": SRD, "setting_id": "pneuma.severed-lands", "plan_id": plan_id,
        })
        # Legacy compatible-party projections predate the explicit policy field.
        # Final ordinary materialization still validates the selected Plan policy.
        if party.get("starting_party", {}).get("player_character", {}).get("mode") == "fixed":
            saved = next(row for row in party["player_archetypes"] if row.get("source_ref"))
            assert saved["campaign_ready"] is True, saved
        else:
            launch = harness.request("character", "start_guided_creation_subflow", {
                "operation_id": "journey-character", "campaign_id": campaign_id,
                "parent_session_id": interview_id, "expected_construction_revision": 4,
                "purpose": "player_character", "visible_answer": "New Character",
            })
            built = harness.request("build", "submit_session_input", {
                "session_id": launch["child_session_id"], "attempt_id": "journey-build",
                "message_id": "journey-build", "user_text": f"Save the level-{character_level} Fighter.",
            })
            assert built["interruption"] is None, built
            saved = next(
                json.loads(message["content"])
                for message in provider.calls[-1]["messages"]
                if message.get("tool_call_id") == "save-journey-character"
            )
            assert saved["success"] is True and saved["campaign_ready"] is True, saved
        companion_ids = []
        if with_companion:
            harness.close()
            provider = LibraryProvider(responses=[
                _character_reply("Journey Companion", level=character_level), ProviderResponse(content="Companion saved."),
            ])
            harness = _OwnerHarness(database, provider)
            companion = harness.request("companion", "start_guided_creation_subflow", {
                "operation_id": "journey-companion", "campaign_id": campaign_id,
                "parent_session_id": interview_id, "expected_construction_revision": 4,
                "purpose": "companion", "visible_answer": "New Companion",
            })
            built = harness.request("build-companion", "submit_session_input", {
                "session_id": companion["child_session_id"], "attempt_id": "build-companion",
                "message_id": "build-companion", "user_text": f"Save the level-{character_level} companion.",
            })
            assert built["interruption"] is None, built
            companion_saved = next(
                json.loads(message["content"])
                for message in provider.calls[-1]["messages"]
                if message.get("tool_call_id") == "save-journey-character"
            )
            assert companion_saved["success"] and companion_saved["campaign_ready"], companion_saved
            companion_ids.append(companion_saved["id"])
        answer(4, "participants", {"archetype_id": saved["id"], "companion_ids": companion_ids})
        answer(5, "boundaries_preferences", {"boundaries": "", "preferences": ""})
        answer(6, "specific_requests", {"no_additional_requests": True, "requests": ""})
        harness.request("ready", "start_guided_campaign", {
            "operation_id": "journey-ready", "campaign_id": campaign_id,
            "builder_session_id": interview_id, "display_name": "Combat journey",
            "candidate_id": SRD, "setting_id": "pneuma.severed-lands",
            "plan_id": plan_id, "player_character_id": saved["id"],
            "companion_ids": companion_ids, "premise": "",
            "construction_intent": {
                "boundaries": "", "preferences": "", "specific_requests": "",
                "contributions": {},
            },
        })
        finalized = harness.request("begin", "finalize_campaign_construction", {
            "campaign_id": campaign_id,
            "dm_provider_id": "test.transport", "dm_model_id": "test.transport/model0",
            "model_guidance_override": True,
        })
        return campaign_id, finalized["handoff"]["dm_session_id"], finalized["dm_turn_id"]
    finally:
        harness.close()


def _practice_scene(positions):
    """Declare the agreed synthetic combat geometry through the public encounter input."""
    return {
        "format": 1,
        "map": {"id": "practice", "revision": 1,
                "bounds": {"min": [-20, -20, 0], "max": [30, 30, 20]},
                "grid": {"cell_size": 5, "unit": "ft", "origin": [-20, -20]},
                "layers": [{"id": "ground", "min_elevation": 0, "max_elevation": 20}]},
        "entities": [{"id": identity, "map_id": "practice", "position": point,
                      "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
                      "height": 6, "facing": [1, 0], "active": True,
                      "movement": "blocking", "visibility": "opaque",
                      "metadata": {"participant_ref": identity, "plane": "material",
                                   "lighting": "bright", "creature_type": "humanoid",
                                   "player": {"label": identity, "kind": "creature"}}}
                     for identity, point in positions.items()],
        "geometry": [], "zones": [], "terrain": [],
    }


class _CombatProvider(LibraryProvider):
    """Choose exposed mechanics, then settle the GM reply without inventing an outcome."""

    def __init__(
        self, player_id: str, *, borrowed_inventory: list[str] | None = None,
        stop_before_action: bool = False,
        stop_after_dodge: bool = False,
    ) -> None:
        super().__init__()
        self.player_id = player_id
        self.prepared = False
        self.started = False
        self.dodged = False
        self.call_count = 0
        self.borrowed_inventory = borrowed_inventory
        self.unavailable_tool = None
        self.stop_before_action = stop_before_action
        self.stop_after_dodge = stop_after_dodge
        self.ended = False

    async def generate_stream(self, messages, tools=None, **_kwargs):
        last_tool = next((m for m in reversed(messages) if m.get("role") == "tool"), None)
        if last_tool:
            result = json.loads(last_tool["content"])
            if result.get("code") == "tool_unavailable":
                self.unavailable_tool = last_tool
            assert result.get("code") != "tool_unavailable", (
                "FC-13: an operation offered by the current GM tool surface was rejected "
                "as unavailable by execution", last_tool,
            )
        names = [item["function"]["name"] for item in tools or []]
        preparation = next((name for name in names if name.endswith("_prepare_srd_creature")), None)
        attack = next(
            (name for name in names if name.endswith("_resolve_adjudicated_creature_attack")), None,
        )
        dodge = next((name for name in names if name.endswith("_take_dodge_action")), None)
        end_turn = next((name for name in names if name.endswith("_end_combat_turn")), None)
        settle = next((name for name in names if name.endswith("_update_campaign_file")), None)
        assert self.call_count < 12, "Scripted combat journey exceeded its expected action sequence"
        if preparation and not self.prepared:
            self.prepared = True
            name = preparation
            arguments = {
                "creature_id": "dera-flint", "name": "Dera Flint",
                "source": "The Sparring Debt: Dera Flint's authored sparring statistics.",
                "armor_class": 13, "hit_points": 10, "speed": 30,
                "initiative_modifier": 1, "size_rank": 2,
            }
        elif not self.started:
            self.started = True
            name = next(name for name in names if name.endswith("_start_combat_encounter_entities"))
            arguments = {
                "first_actor": self.player_id, "second_actor": "dera-flint",
                "first_surprised": False, "second_surprised": False, "tie_winner": "first",
                "combat_scenes": [_practice_scene({self.player_id: [0, 0, 0], "dera-flint": [5, 0, 0]})],
            }
        elif self.stop_before_action and not self.ended:
            name = next((name for name in names if name.endswith("_end_combat_encounter")), None)
            if name is None:
                self.unavailable_tool = "end_combat_encounter after initiative"
                raise AssertionError(
                    "An agreed stop must not require an unwanted player/NPC action",
                )
            self.ended = True
            arguments = {"end_reason": "fighting_stopped"}
        elif self.stop_after_dodge and self.dodged and not self.ended:
            self.ended = True
            name = next(name for name in names if name.endswith("_end_combat_encounter"))
            arguments = {"end_reason": "fighting_stopped"}
        elif attack:
            name = attack
            arguments = {
                "target": self.player_id, "attack_name": "Practice staff", "attack_modifier": 3,
                "damage_expression": "d4", "damage_modifier": 1, "damage_type": "bludgeoning",
                "advantage": False, "disadvantage": False,
                "attack_mode": "melee", "attack_distance": 5, "normal_range": 5, "long_range": 5,
                "actor_can_see_target": True, "target_can_see_actor": True,
                "target_is_charmer": False, "fear_source_in_sight": False,
            }
        elif dodge and not self.dodged and not self.ended:
            self.dodged = True
            name = dodge
            arguments = {"actor": self.player_id, "actor_state": self.player_id}
        elif end_turn and not self.dodged and not self.ended:
            name = end_turn
            arguments = {}
        else:
            name = settle
            if name is None:
                self.unavailable_tool = {"expected": "update_campaign_file", "available": names,
                                         "last_receipt": last_tool}
                raise AssertionError("The completed exchange cannot save its campaign disposition")
            arguments = {
                "state_change": "unchanged", "state_change_summary": "No narrative state changes.",
                "revised_player_facing_response": [{
                    "kind": "narration", "text": "The encounter-start attempt is complete.",
                }],
            }
            if self.borrowed_inventory is not None:
                revision = next(
                    value["campaign_revision"]
                    for message in reversed(messages) if message.get("role") == "tool"
                    for value in [json.loads(message["content"])]
                    if isinstance(value, dict) and "campaign_revision" in value
                )
                arguments.update(
                    state_change="changed",
                    state_change_summary="The player borrowed the agreed practice club.",
                    character_updates=[{
                        "participant_id": self.player_id, "expected_revision": revision,
                        "fields": {"inventory": self.borrowed_inventory},
                    }],
                )
        self.call_count += 1
        yield ProviderStreamChunk(tool_call_delta={
            "index": 0, "id": f"journey-{self.call_count}",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }, finish_reason="tool_calls")


@pytest.mark.parametrize("borrow_club,stop_before_action,stop_after_dodge", [
    (False, False, False), (True, False, False), (True, True, False), (False, False, True),
], ids=["dodge", "dodge-and-inventory", "agreed-stop-with-pending-action", "end-clears-dodge"])
def test_normal_sparring_debt_can_admit_its_opponent_and_start_combat(
    tmp_path: Path, borrow_club: bool, stop_before_action: bool, stop_after_dodge: bool,
) -> None:
    database = tmp_path / "journey.sqlite3"
    campaign_id, session_id, turn_id = _start_campaign(database)
    with AuthorityOwner.open(database, "journey-inspect") as owner:
        initial = owner.snapshot(campaign_id)
        assert initial.writer is WriterRole.DM
        assert owner.session_context(session_id).session.campaign_id == campaign_id
        (player,) = initial.participants
        assert player.data["level"] == 2
        assert set(initial.state[f"{SRD}.entities"]) == {player.participant_id}
        inventory = [*initial.state[f"{SRD}.entities"][player.participant_id]["inventory"],
                     "practice_club"]

    provider = _CombatProvider(
        player.participant_id, borrowed_inventory=inventory if borrow_club else None,
        stop_before_action=stop_before_action,
        stop_after_dodge=stop_after_dodge,
    )
    harness = _OwnerHarness(database, provider)
    try:
        reply = request_attached(harness, "combat", "submit_session_input", {
            "session_id": session_id,
            "attempt_id": "journey-combat", "message_id": "journey-combat",
            "turn_id": turn_id, "expected_revision": initial.revision,
            "user_text": ("I borrow the agreed practice club. " if borrow_club else "") + (
                "I accept Dera's friendly bout. Start combat and roll initiative. "
                "On my first turn Dodge, then stop for my next decision."
                + (
                    " We agree to stop the bout immediately after initiative, before either "
                    "creature acts; the declared Dodge no longer applies."
                    if stop_before_action else ""
                )
                + (" After I Dodge we both agree to stop fighting and end the encounter."
                   if stop_after_dodge else "")
            ),
        }, campaign_id=campaign_id, attempt_id="journey-combat")
        assert reply["interruption"] is None, reply
    finally:
        harness.close()
        assert provider.unavailable_tool is None, (
            "FC-13: a required combat journey operation is unavailable through the real "
            "GM tool surface; no ChatGPT transport was involved.", provider.unavailable_tool,
        )

    with AuthorityOwner.open(database, "journey-reopen") as owner:
        saved = owner.snapshot(campaign_id)
        starts = [
            action for action in saved.actions
            if action.tool_name == "start_combat_encounter_entities"
        ]
        assert starts and starts[-1].status is ActionStatus.COMMITTED, (
            "FC-13: normal Interview and Character Builder succeeded, but Dera's encounter did "
            "not start through the real GM/MCP path. Opponent preparation must work without "
            "a test helper inserting a creature into campaign state.",
            [(action.status.value, action.result) for action in starts],
        )
        combat = saved.state[f"{SRD}.combat"]
        ended = stop_before_action or stop_after_dodge
        assert combat["encounter_active"] is not ended
        assert {combat["order_first"], combat["order_second"]} == {
            player.participant_id, "dera-flint",
        }
        assert combat["turn_active"] is not ended
        dodges = [action for action in saved.actions if action.tool_name == "take_dodge_action"]
        if stop_before_action:
            assert not dodges
            assert combat["action_available"] is False
            assert combat["active_actor"] is None and combat["current_actor"] is None
            assert combat["last_end_reason"] == "fighting_stopped"
            assert not any(action.tool_name == "resolve_adjudicated_creature_attack"
                           for action in saved.actions)
        else:
            assert len(dodges) == 1 and dodges[0].status is ActionStatus.COMMITTED
            assert saved.state[f"{SRD}.entities"][player.participant_id]["dodging"] is not ended
        if borrow_club:
            carried = saved.state[f"{SRD}.entities"][player.participant_id]["inventory"]
            assert list(carried) == inventory
        print("QA_CORE_SPARring", {"combat_assertions": "PASS", "borrow_club": borrow_club,
            "stop_before_action": stop_before_action, "stop_after_dodge": stop_after_dodge})
        routine = [projection for projection in owner.player_projection(campaign_id)
                   if projection.safe_payload.get("result", {}).get("activity_label")]
        assert routine
        activity = {message.content["action_id"]: message.content["activity"]
                    for message in owner.session_context(session_id).messages
                    if message.content.get("kind") == "mechanics_activity"}
        assert all(activity[projection.action_id]["attempt_id"] == "journey-combat"
                   for projection in routine)
        by_sequence = {projection.sequence: projection for projection in routine}
        full_context = owner.session_context(session_id)
        for message in full_context.messages:
            if message.canonical_sequence not in by_sequence:
                continue
            page = owner.session_history_page(
                session_id, before_sequence=message.sequence + 1, limit=1,
            )
            assert len(page.messages) == 1
            projected = page.messages[0]
            assert projected.message_id == message.message_id
            assert projected.content.get("activity") == activity[
                by_sequence[message.canonical_sequence].action_id
            ], "A result page must retain its saved activity link without older evidence rows"
        assert owner.session_context(session_id) == full_context



