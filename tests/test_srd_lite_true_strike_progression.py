"""Original SRD True Strike through acquisition, public equipment and real casts.

Hero is the exported materializer's complete, unchanged level-1 output at
campaign creation. The other three PCs are explicitly imported Sorcerer 1 /
Fighter 4, 10 and 16 casting fixtures, qualifying total-level scaling without
claiming complete advancement. No engine consumer, dice or saved grant is
replaced after creation. The parent stages and runs this module on its candidate.
"""

import io
import json
import re
import zipfile
from copy import deepcopy

import pytest

from sylvia_gm.application.authority import CampaignCreate
from sylvia_gm.application.initial_character_state import (
    materialize_initial_character_state,
    verify_initial_character_state,
)
from sylvia_gm.domain.authority import (
    Participant, ParticipantValidationStatus, StateValue, WriterRole, thaw_json_value,
)
from sylvia_gm.domain.errors import CommandRejected
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_attack_budget import _combat_case
from tests.test_srd_lite_foundation import ARCHIVE, FULL, VARIANT_ID, Case
from tests.test_srd_lite_offensive_acquisition_incremental import _acolyte


TIERS = (("tier5", 5, 1, "weapon"), ("tier11", 11, 2, "radiant"),
         ("tier17", 17, 3, "weapon"))


def _material(identity):
    # One canonical dagger, worth 2 GP, owned independently by this caster.
    return {"id": identity + "_dagger", "inventory_item": "dagger", "kind": "specific",
            "components": ["true_strike.1"], "quantity": 1, "value_cp": 200}


def _artifact():
    with zipfile.ZipFile(ARCHIVE) as archive:
        record = next(row for row in json.loads(archive.read("manifest.json"))["packages"]
                      if row["id"] == VARIANT_ID)
    artifact = next(row for row in record["participant_artifacts"]
                    if row["kind"] == "player_character")
    return record, artifact


def _contract(artifact):
    return {key: artifact[key] for key in (
        "entity_state_schema", "initial_state_calculations", "initial_state_validation",
    )}


def _acquired_true_strike():
    participant, authored = _acolyte()
    # Keep all printed Sorcerer and Acolyte choice counts from the accepted
    # complete creation recipe; substitute one naturally granted cantrip.
    index = participant["spellcasting_grants"][0]["spells"].index("poison_spray")
    participant["spellcasting_grants"][0]["spells"][index] = "true_strike"
    authored["prepared_spells"][authored["prepared_spells"].index("poison_spray")] = "true_strike"
    authored["spell_materials"] = [_material("hero")]
    authored["ammunition"] = {}
    # The printed dagger is this fixture's explicitly selected material weapon.
    # Supply the current authored equipment choice before materialization.
    authored["weapon_sets"]["active_set"] = "melee"
    authored["weapon_sets"]["melee"]["main_hand"] = "dagger"
    return participant, authored


class TrueStrikeCase(Case):
    def __init__(self, path, record, artifact, participant, completed):
        self.path, self.manager, self.record = path, None, record
        state, _ = _combat_case([{"id": "sorcerer", "level": 1}])
        entities = state[FULL + ".entities"]
        entities["hero"] = thaw_json_value(completed)
        participants = [("hero", deepcopy(participant))]
        for identity, level, _, _ in TIERS:
            imported = deepcopy(participant)
            imported.update(name=f"Imported total-level {level} True Strike caster", level=level,
                            classes=[{"id": "sorcerer", "level": 1},
                                     {"id": "fighter", "level": level - 1}])
            body = thaw_json_value(completed)
            body.update(name=imported["name"], fighter_level=level - 1,
                        resources={"hit_die": level, "spell_slot_1": 2},
                        resource_maxima={"hit_die": level, "spell_slot_1": 2},
                        spell_materials=[_material(identity)])
            entities[identity] = body
            participants.append((identity, imported))
        entities["guard"].update(
            name="Synthetic True Strike weapon target", hp=1000, max_hp=1000, temp_hp=0,
            armor_class=1, speed=30, size_rank=1, creature_type="humanoid", melee_reach=5,
            initiative_modifier=0, constitution_modifier=0, conditions=[], dodging=False,
            exhaustion_level=0, saving_throw_modifiers=dict.fromkeys(participant["abilities"], 0),
            damage_immunities=[], damage_resistances=[], damage_vulnerabilities=[],
            attitudes={identity: "hostile" for identity, _ in participants},
            available_reactions=["opportunity_attack"], reaction_available=True,
            stable=False, death_successes=0, death_failures=0, stable_recovery_hours_remaining=0,
        )
        state[FULL + ".combat"].update(
            encounter_active=False, turn_active=False, active_actor=None,
            phase="exploration", action_available=False,
        )
        positions = {"hero": [0, 0, 0], "guard": [5, 0, 0], "tier5": [5, -5, 0],
                     "tier11": [5, 5, 0], "tier17": [10, 0, 0]}
        state[FULL + ".state"] = {"lasting_tick": 0, "turn_effects": [], "spatial_scene": {
            "format": 1, "map": {
                "id": "lite", "revision": 0,
                "bounds": {"min": [-100, -100, -100], "max": [100, 100, 100]},
                "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
                "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}],
            },
            "entities": [{
                "id": identity, "map_id": "lite", "position": point,
                "footprint": {"kind": "box", "half_extents": [0.4, 0.4]}, "height": 6,
                "facing": [1, 0], "active": True, "movement": "blocking", "visibility": "opaque",
                "metadata": {"participant_ref": identity, "plane": "material", "lighting": "bright",
                             "creature_type": "humanoid",
                             "enemy_of": [actor for actor, _ in participants] if identity == "guard" else []},
            } for identity, point in positions.items()],
            "geometry": [], "zones": [], "terrain": [],
        }}
        self.parameters = {
            "spell": "true_strike", "caster": "hero", "caster_state": "hero", "target": "guard",
            "slot_resource": "cantrip", "target_distance": 5, "caster_can_see_target": True,
            "caster_can_hear_target": True, "caster_can_touch_target": True,
            "target_willing": False, "cover": "none", "verbal_component_available": False,
            "somatic_component_available": True, "material_component_available": True,
            "material_item_ids": ["hero_dagger"], "true_strike_weapon": "dagger",
            "true_strike_attack_mode": "melee", "true_strike_two_handed": False,
            "true_strike_damage_choice": "radiant",
        }
        self.open()
        installed = self.owner.install_package_archive(io.BytesIO(ARCHIVE.read_bytes()), install_id="lite")
        exact = next(row for row in installed.package_lock.entries if row.package_id == VARIANT_ID)
        self.owner.create_campaign(CampaignCreate(
            campaign_id="lite", display_name="Original SRD True Strike acquisition and scaling",
            writer=WriterRole.DM, package_lock=installed.package_lock, turn_id="turn",
            participants=tuple(Participant(
                participant_id=identity, kind="player_character", package_id=VARIANT_ID,
                package_version=exact.version, package_sha256=exact.sha256,
                schema_id=artifact["schema_id"], schema_sha256=artifact["schema_sha256"],
                validation_status=ParticipantValidationStatus.VALIDATED, data=data,
            ) for identity, data in participants),
            initial_state=tuple(StateValue(VARIANT_ID, key.removeprefix(FULL + "."), value)
                                for key, value in state.items()),
        ))


def _retain(case, history, receipt, command):
    saved = case.snapshot()
    assert case.execute(command) == receipt and case.snapshot() == saved
    history.append((receipt, command))


def _retry_reopened_history(case, history):
    saved = case.snapshot()
    case.reopen()
    assert case.snapshot() == saved
    for receipt, command in history:
        assert case.execute(command) == receipt and case.snapshot() == saved


def _choices(case, identity, damage_choice):
    return case.parameters | {"caster": identity, "caster_state": identity,
                              "material_item_ids": [identity + "_dagger"],
                              "true_strike_damage_choice": damage_choice}


def _card_faces(value):
    return [int(face) for face in re.findall(r"\d+", str(value))]


def _cast_to_actual_hit(case, identity, level, extra_dice, damage_choice, history, *, combat=False):
    choices = _choices(case, identity, damage_choice)
    actors = ("hero", "tier5", "tier11", "tier17")
    resources = {actor: deepcopy(case.entities()[actor]["resources"]) for actor in actors}
    supplies = {actor: deepcopy(case.entities()[actor]["spell_materials"]) for actor in actors}
    inventory = {actor: deepcopy(case.entities()[actor]["inventory"]) for actor in actors}
    for _ in range(8):
        hp = case.entities()["guard"]["hp"]
        receipt, command = case.act("cast_spell" if combat else "cast_spell_outside_combat", **choices)
        context, card = receipt.result["context"], receipt.result["result_card"]
        assert context["_casting_profile"]["ability"] == "charisma"
        assert context["_casting_profile"]["scaling_level"] == level
        assert context["_casting_profile"]["modifier"] == 3
        assert context["_spell_definition"] == {
            "family": "true_strike", "casting_time": "action", "base_level": 0,
            "range_kind": "self", "range_feet": 0, "concentration": False, "lite_profile": "true_strike",
        }
        assert card["title"] == "True Strike"
        summary = {row["label"]: row["value"] for row in card["summary"]}
        details = {row["label"]: row["value"] for row in card["details"]}
        assert str(summary["Spell slots spent"]) == "0"
        assert context["_attack_roll"]["results"]
        assert len(context["_attack_roll"]["results"]) == 1
        assert _card_faces(details["D20 results"]) == list(context["_attack_roll"]["results"])
        assert details["Casting ability"] == "charisma"
        assert int(details["Ability modifier"]) == context["_ability_modifier"] == 3
        assert int(details["Proficiency"]) == context["_weapon_proficiency_modifier"] == 2 + (level - 1) // 4
        assert summary["Weapon"] == "dagger"
        body = case.entities()[identity]
        assert body.get("lite_true_strike") is None and body.get("concentration_spell_id") is None
        assert body["last_spell_source_spell"] == "true_strike"
        assert isinstance(body["last_spell_source_cast_id"], str) and body["last_spell_source_cast_id"]
        assert not any(row.get("spell") == "true_strike" for row in case.lasting().get("turn_effects", []))
        for actor in actors:
            assert case.entities()[actor]["resources"] == resources[actor]
            assert case.entities()[actor]["spell_materials"] == supplies[actor]
            assert case.entities()[actor]["inventory"] == inventory[actor]
        if combat:
            assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is False
            before = case.snapshot()
            case.reject("cast_spell", choices)
            assert case.snapshot() == before
        _retain(case, history, receipt, command)
        if context["_attack_outcome"] == "miss":
            assert case.entities()["guard"]["hp"] == hp
            assert int(summary["Damage"]) == 0
            if combat:
                case.next_turn()
                case.next_turn()
                assert case.snapshot().state[VARIANT_ID + ".combat"]["active_actor"] == "hero"
            else:
                elapsed, elapsed_command = case.act("advance_srd_lasting_time", elapsed_rounds=1)
                _retain(case, history, elapsed, elapsed_command)
            continue
        assert context["_attack_outcome"] in ("hit", "critical_hit")
        critical_factor = 2 if context["_attack_outcome"] == "critical_hit" else 1
        weapon_faces = list(context["_damage_roll"]["results"])
        radiant_faces = list(context["_true_strike_extra_faces"])
        assert len(weapon_faces) == critical_factor and all(1 <= face <= 4 for face in weapon_faces)
        assert len(radiant_faces) == extra_dice * critical_factor
        assert all(1 <= face <= 6 for face in radiant_faces)
        expected = sum(weapon_faces) + 3 + sum(radiant_faces)
        assert case.entities()["guard"]["hp"] == hp - expected
        assert int(summary["Damage"]) == expected
        assert int(details["HP lost"]) == expected and int(details["Temporary HP absorbed"]) == 0
        assert _card_faces(details["Weapon damage dice"]) == weapon_faces
        assert _card_faces(details["Extra Radiant dice"]) == radiant_faces
        assert details["Weapon damage type"] == ("radiant" if damage_choice == "radiant" else "piercing")
        return {"caster": identity, "total_level": level, "extra_dice": extra_dice,
                "damage_choice": damage_choice, "actual_card": thaw_json_value(card),
                "actual_attack": thaw_json_value(context["_attack_roll"]),
                "weapon_faces": weapon_faces, "radiant_faces": radiant_faces}
    raise AssertionError("Eight lawful True Strike casts produced no actual hit.")


def test_true_strike_original_srd_acquisition_weapon_scaling_cost_retry_reopen(tmp_path):
    record, artifact = _artifact()
    participant, authored = _acquired_true_strike()
    original = deepcopy((participant, authored))
    completed = materialize_initial_character_state(participant, authored, **_contract(artifact))
    assert (participant, authored) == original
    assert verify_initial_character_state(participant, completed, **_contract(artifact)) == completed
    assert completed["hp"] == completed["max_hp"] == 9
    assert "true_strike" in completed["prepared_spells"]
    assert "true_strike" in participant["spellcasting_grants"][0]["spells"]
    # Spare the Dying cannot be acquired through Sorcerer merely
    # because the continuation selects its implementation.
    invalid, invalid_authored = deepcopy(original)
    source_spells = invalid["spellcasting_grants"][0]["spells"]
    source_spells[source_spells.index("true_strike")] = "spare_the_dying"
    prepared = invalid_authored["prepared_spells"]
    prepared[prepared.index("true_strike")] = "spare_the_dying"
    unchanged = deepcopy((invalid, invalid_authored))
    with pytest.raises(CommandRejected, match="not granted by this source"):
        materialize_initial_character_state(invalid, invalid_authored, **_contract(artifact))
    assert (invalid, invalid_authored) == unchanged

    case = TrueStrikeCase.__new__(TrueStrikeCase)
    history, observations = [], []
    try:
        case.__init__(tmp_path / "original-true-strike.db", record, artifact, participant, completed)
        assert case.entities()["hero"] == completed
        participants = case.snapshot().participants
        for identity in ("hero", "tier5", "tier11", "tier17"):
            receipt, command = case.act("record_srd_control_held_items", target_state=identity,
                                        held_items=["dagger"], held_attack_names=[])
            assert case.entities()[identity]["held_items"] == ["dagger"]
            _retain(case, history, receipt, command)
            receipt, command = case.act(
                "record_srd_scene_hand_occupancy", actor_state=identity, hand_capacity=2,
                holdings=[{"inventory_item": "dagger", "hand_indices": [0]}],
            )
            _retain(case, history, receipt, command)
        initial = case.snapshot()
        for changes in ({"somatic_component_available": False}, {"material_item_ids": []},
                        {"true_strike_weapon": "quarterstaff"}):
            case.reject("cast_spell_outside_combat", case.parameters | changes)
            assert case.snapshot() == initial
        for identity, level, dice, choice in TIERS:
            observations.append(_cast_to_actual_hit(case, identity, level, dice, choice, history))

        # Public MCP admission uses the current catalogue before receipt lookup.
        # Prove stale exploration receipts after later casts and reopening while
        # the original outside-combat declaration still admits this operation.
        exploration_history = tuple(history)
        _retry_reopened_history(case, exploration_history)

        receipt, command = case.act(
            "start_combat_encounter_entities", first_actor="hero", second_actor="guard",
            first_surprised=False, second_surprised=False, tie_winner="first",
        )
        _retain(case, history, receipt, command)
        receipt, command = case.act("begin_combat_turn")
        _retain(case, history, receipt, command)
        if case.snapshot().state[VARIANT_ID + ".combat"]["active_actor"] == "guard":
            case.next_turn()
        assert case.snapshot().state[VARIANT_ID + ".combat"]["active_actor"] == "hero"
        observations.append(_cast_to_actual_hit(case, "hero", 1, 0, "radiant", history, combat=True))
        assert case.snapshot().participants == participants
        _retry_reopened_history(case, history[len(exploration_history):])
        final = case.snapshot()
        unavailable = []
        for receipt, command in exploration_history:
            if command.tool_name != "cast_spell_outside_combat":
                continue
            # An old exploration intent does not bypass current catalogue
            # permissions merely because its receipt is already committed.
            with pytest.raises(CommandRejected) as rejected:
                case.execute(command)
            assert rejected.value.code == "mcp_tool_unavailable"
            assert case.snapshot() == final
            # The public authority receipt store can recover the identical
            # already-recorded command before fresh-command phase admission.
            # This is separate from MCP/UI catalogue availability.
            assert case.owner.execute_action(command) == receipt
            assert case.snapshot() == final
            unavailable.append({"tool_name": command.tool_name,
                                "action_id": command.action_id,
                                "idempotency_key": command.idempotency_key,
                                "expected_revision": command.expected_revision,
                                "public_receipt_store_recovered": True})
        assert unavailable
        fresh = case.command("cast_spell_outside_combat", _choices(case, "tier5", "weapon"),
                             identity="fresh-outside-intent-during-combat")
        for execute, code in ((case.execute, "mcp_tool_unavailable"),
                              (case.owner.execute_action, "tool_unavailable")):
            with pytest.raises(CommandRejected) as rejected:
                execute(fresh)
            assert rejected.value.code == code
            assert case.snapshot() == final
        (tmp_path / "true-strike-observations.json").write_text(json.dumps({
            "qualification": "Complete Sorcerer/Acolyte materialization; imported multiclass scaling fixtures; no advancement or Builder/UI proof",
            "actual_materialized_state": thaw_json_value(completed), "actual_participant": participant,
            "casts": observations, "exact_retry_and_reopen": True,
            "retry_qualification": "Casting receipts replay through MCP after reopen in their admitted phase. Old outside-cast intents remain unavailable through MCP during an active encounter; identical stored commands recover only through the public authority receipt store. Fresh outside intents reject on both public routes",
            "mcp_cross_phase_receipt_recovery": False,
            "cross_phase_unavailable_intents": unavailable,
        }, indent=2), encoding="utf-8")
    finally:
        if getattr(case, "manager", None):
            archive_closed_case(case)
