"""Normally built party members share real Initiative, attacks and a persisted ending."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.provider._base import ProviderStreamChunk
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider
from tests.qa_attached_request import request_attached
from tests.test_fc_campaign_journey import SRD, _start_campaign, _practice_scene


class _PartyCombatProvider(LibraryProvider):
    """Choose one action per actor using the same trusted decision context as a real model."""

    def __init__(self, player: str, companion: str, *, surge_before_attack: bool) -> None:
        super().__init__()
        self.player = player
        self.companion = companion
        self.prepared = False
        self.started = False
        self.ended = False
        self.recovered = False
        self.moved = False
        self.surge_before_attack = surge_before_attack
        self.surged = False
        self.player_attacks = 0
        self.npc_attacks = 0
        self.npc_moved = False
        self.attacked: set[str] = set()
        self.sent = 0
        self.failure: str | None = None

    async def generate_stream(self, messages, tools=None, **_kwargs):
        names = [tool["function"]["name"] for tool in tools or []]
        actor = self.player
        for message in messages:
            content = message.get("content", "")
            if message.get("role") == "system" and isinstance(content, str) and content.startswith((
                "SYLVIA_GM_DECISION_REQUEST ", "SYLVIA_GM_PLAYER_BOUNDARY_NARRATION ",
            )):
                boundary = json.JSONDecoder().raw_decode(content.split("\n", 1)[1])[0]
                actor = boundary["actor_id"]
        if self.npc_attacks == 1 and actor != "training-guard":
            self.failure = "NPC turn advanced before its declared two-attack Multiattack completed"
            raise AssertionError(self.failure)
        if not self.prepared:
            self.prepared = True
            suffix = "prepare_srd_creature"
            args = {
                "creature_id": "training-guard", "name": "Training Guard",
                "source": "Agreed practice: AC13, HP60, spear +3, 1d6+1. "
                "The veteran has Multiattack: two Practice spear attacks as one action.",
                "armor_class": 13, "hit_points": 60, "speed": 30,
                "initiative_modifier": 1, "size_rank": 2,
                "strength_save": 2, "dexterity_save": 1,
                "multiattack": [{"attack": "Practice spear", "count": 2}],
            }
        elif not self.started:
            self.started = True
            suffix = "start_group_combat_encounter"
            args = {"combatants": [self.player, self.companion, "training-guard"],
                    "combat_scenes": [_practice_scene({self.player: [0, 0, 0], self.companion: [5, 5, 0], "training-guard": [5, 0, 0]})]}
        elif len(self.attacked) == 3 and self.player_attacks == 2 and not self.ended:
            self.ended = True
            suffix, args = "end_combat_encounter", {"end_reason": "fighting_stopped"}
        elif self.ended and not self.recovered:
            self.recovered = True
            suffix, args = "use_srd_second_wind", {"actor": self.player}
        elif self.ended:
            suffix = "update_campaign_file"
            args = {
                "state_change": "unchanged",
                "state_change_summary": "No additional narrative changes.",
                "revised_player_facing_response": [{"kind": "narration", "text":
                    "All three creatures have made their agreed attack. Practice ends; "
                    "the committed damage and Second Wind use remain in effect."}],
            }
        elif actor in self.attacked:
            suffix, args = "end_combat_turn", {}
        elif actor == self.player and not self.moved:
            self.moved = True
            suffix = "move_srd_scene_creature"
            destination = [5, 0, 0] if self.npc_moved else [2.5, 4.330127018922193, 0]
            args = {"actor_state": self.player, "destination": destination, "mode": "walk"}
        elif actor == "training-guard" and self.npc_attacks == 1 and not self.npc_moved:
            self.npc_moved = True
            destination = [7.5, 4.330127018922193, 0] if self.moved else [2.5, 4.330127018922193, 0]
            suffix, args = "move_srd_scene_creature", {"actor_state": "training-guard", "destination": destination, "mode": "walk"}
        elif actor == "training-guard":
            self.npc_attacks += 1
            if self.npc_attacks == 2:
                self.attacked.add(actor)
            suffix = "resolve_adjudicated_creature_attack"
            args = {
                "target": self.player, "attack_name": "Practice spear", "attack_modifier": 3,
                "use_multiattack": True,
                "damage_expression": "d6", "damage_modifier": 1, "damage_type": "piercing",
                "advantage": False, "disadvantage": False,
                "attack_mode": "melee", "attack_distance": 5, "normal_range": 5, "long_range": 5,
                "actor_can_see_target": True, "target_can_see_actor": True,
                "target_is_charmer": False, "fear_source_in_sight": False,
            }
        elif actor == self.player and not self.surged and (
            self.surge_before_attack or self.player_attacks == 1
        ):
            self.surged = True
            suffix, args = "use_srd_action_surge", {"actor": self.player}
        else:
            if actor == self.player:
                self.player_attacks += 1
            if actor != self.player or self.player_attacks == 2:
                self.attacked.add(actor)
            suffix = "resolve_combat_attack"
            args = {
                "actor": actor, "target": "training-guard", "attack_sequence": "primary",
                "use_graze_on_miss": False, "weapon": "spear", "attack_mode": "melee",
                "attack_ability": "strength", "wielding_two_handed": False, "mounted": False,
                "circumstantial_modifier": 0, "advantage": False, "disadvantage": False,
                "actor_can_see_target": True, "target_can_see_actor": True,
                "target_is_charmer": False, "fear_source_in_sight": False, "attack_distance": 5,
            }
            if actor == self.companion and self.surge_before_attack:
                suffix = "resolve_unarmed_strike"
                args = {
                    "actor": actor, "target_state": "training-guard", "effect": "shove_prone",
                    "target_save_ability": "strength", "target_forego_save": False,
                    "hand_free": True, "distance": 5, "advantage": False, "disadvantage": False,
                    "actor_can_see_target": True, "target_can_see_actor": True,
                    "target_is_charmer": False, "fear_source_in_sight": False,
                }
        name = next((name for name in names if name.endswith("_" + suffix)), None)
        if name is None or self.sent >= 18:
            self.failure = f"Required party combat step unavailable or repeated: {actor}, {suffix}"
            raise AssertionError(self.failure)
        self.sent += 1
        yield ProviderStreamChunk(tool_call_delta={
            "index": 0, "id": f"party-{self.sent}",
            "function": {"name": name, "arguments": json.dumps(args)},
        }, finish_reason="tool_calls")


@pytest.mark.parametrize("surge_before_attack", [False, True])
def test_normal_party_combat_retains_all_actors_attacks_recovery_and_ending(
    tmp_path: Path, surge_before_attack: bool,
):
    database = tmp_path / "party.sqlite3"
    campaign, session, turn = _start_campaign(
        database, "the-long-way-round", with_companion=True,
    )
    with AuthorityOwner.open(database, "party-before") as owner:
        before = owner.snapshot(campaign)
        players = {p.data["party_role"]: p.participant_id for p in before.participants}
        player, companion = players["player_character"], players["companion"]
        assert set(before.state[f"{SRD}.entities"]) == {player, companion}
    provider = _PartyCombatProvider(player, companion, surge_before_attack=surge_before_attack)
    harness = _OwnerHarness(database, provider)
    try:
        reply = request_attached(harness, "practice", "submit_session_input", {
            "session_id": session, "attempt_id": "party-practice", "message_id": "party-practice",
            "turn_id": turn, "expected_revision": before.revision,
            "user_text": "Before setting off, we agree to practice with a veteran guard. "
            + ("My companion makes one Unarmed Strike to shove the Medium guard prone; "
               "the guard chooses its Strength +2 save. " if surge_before_attack else
               "My companion makes one spear attack. ")
            + "The guard uses its two-attack Multiattack. "
            "Between its attacks the guard moves five feet around me, remaining within reach. "
            "I use Action Surge for two attacks in my turn, then we stop. "
            "I start in spear reach, move five feet around the guard while staying in reach, attack and end my turn; "
            "my companion starts in reach and acts independently. "
            "Afterward I use Second Wind.",
        }, campaign_id=campaign, attempt_id="party-practice")
        assert reply["interruption"] is None, reply
    finally:
        harness.close()
        assert provider.failure is None, provider.failure
    with AuthorityOwner.open(database, "party-reopen") as owner:
        saved = owner.snapshot(campaign)
        combat = saved.state[f"{SRD}.combat"]
        assert combat["encounter_active"] is False and combat["turn_active"] is False
        assert combat["active_actor"] is None and combat["last_end_reason"] == "fighting_stopped"
        expected = {"start_group_combat_encounter": 1,
                    "resolve_combat_attack": 2 if surge_before_attack else 3,
                    "resolve_adjudicated_creature_attack": 2, "end_combat_encounter": 1,
                    "use_srd_second_wind": 1, "move_srd_scene_creature": 2,
                    "use_srd_action_surge": 1}
        if surge_before_attack:
            expected["resolve_unarmed_strike"] = 1
        for name, count in expected.items():
            actions = [a for a in saved.actions if a.tool_name == name]
            assert len(actions) == count, (name, actions)
            assert all(a.status is ActionStatus.COMMITTED for a in actions), actions
        movement = next(a for a in saved.actions if a.tool_name == "move_srd_scene_creature"
                        and a.parameters["actor_state"] == player)
        attack = next(a for a in saved.actions if a.tool_name == "resolve_combat_attack"
                      and a.parameters["actor"] == player)
        assert movement.resulting_revision < attack.resulting_revision
        npc_attacks = [a for a in saved.actions
                       if a.tool_name == "resolve_adjudicated_creature_attack"]
        npc_movement = next(a for a in saved.actions if a.tool_name == "move_srd_scene_creature"
                            and a.parameters["actor_state"] == "training-guard")
        assert (npc_attacks[0].resulting_revision < npc_movement.resulting_revision
                < npc_attacks[1].resulting_revision)
        surge = next(a for a in saved.actions if a.tool_name == "use_srd_action_surge")
        assert (surge.resulting_revision < attack.resulting_revision) is surge_before_attack
        entities = saved.state[f"{SRD}.entities"]
        assert set(entities) == {player, companion, "training-guard"}
        assert entities[player]["resources"]["second_wind"] == 1
        assert entities[player]["resources"]["action_surge"] == 0
        assert entities[companion]["resources"]["action_surge"] == 1
        assert entities[companion]["resources"]["second_wind"] == 2
        assert entities[companion]["hp"] == 20
        hp = {player: 20, companion: 20, "training-guard": 60}
        for action in saved.actions:
            context = action.result.get("context", {})
            if action.tool_name in {"resolve_combat_attack", "resolve_adjudicated_creature_attack"}:
                card = action.result.get("result_card")
                assert card is not None, "Committed combat attacks need visible, saved result cards"
                assert card["subtitle"] == "Attack roll"
                assert card["equation"]["total"] == context["_attack_adjusted_total"]
                summary = {row["label"]: row["value"] for row in card["summary"]}
                target = action.parameters["target"]
                names = {p.participant_id: p.data["name"] for p in saved.participants}
                names["training-guard"] = "Training Guard"
                assert summary["Target"] == names[target]
                assert isinstance(summary["Damage"], int)
                assert context["_attack_outcome"] in {"hit", "miss", "critical_hit"}
                assert 1 <= context["_attack_roll"]["total"] <= 20
                if context["_attack_outcome"] != "miss":
                    target = ("training-guard"
                              if action.tool_name == "resolve_combat_attack" else player)
                    damage = context["_damage_roll"]["total"] + context["_damage_modifier"]
                    hp[target] -= max(0, damage)
            elif action.tool_name == "use_srd_second_wind":
                roll = context["_second_wind_roll"]
                summary = {
                    row["label"]: row["value"] for row in action.result["result_card"]["summary"]
                }
                restored_hp = min(20 - hp[player], roll["total"] + roll["modifier"])
                assert summary["HP restored"] == restored_hp
                hp[player] = min(20, hp[player] + roll["total"] + roll["modifier"])
            elif action.tool_name == "resolve_unarmed_strike":
                card = action.result["result_card"]
                assert card["subtitle"] == "Saving throw"
                assert card["equation"]["modifier"] == 2
                summary = {row["label"]: row["value"] for row in card["summary"]}
                assert summary["Target"] == summary["Rolled by"] == "Training Guard"
                knocked_prone = "prone" in entities["training-guard"]["conditions"]
                assert knocked_prone is context["_unarmed_success"]
                assert entities[companion]["size_rank"] == next(
                    p.data["size_rank"] for p in saved.participants if p.participant_id == companion
                )
        assert {actor: entities[actor]["hp"] for actor in hp} == hp
        assert provider.attacked == {player, companion, "training-guard"}
        archive = tmp_path / "played-party.zip"
        owner.export_campaign(operation_id="export-party", campaign_id=campaign, target=archive)
    with AuthorityOwner.open(tmp_path / "recovered.sqlite3", "party-recovered") as owner:
        recovered = owner.recover_campaign(recovery_id="recover-party", source=archive)
        assert recovered.campaign_id == campaign
        restored = owner.snapshot(campaign)
        assert restored.state == saved.state
        assert restored.actions == saved.actions
        assert restored.participants == saved.participants
