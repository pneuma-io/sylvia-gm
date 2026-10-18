"""Original SRD Thaumaturgy/Mending through complete acquisition and public owners.

The parent admits this new module after its active source freeze. No runtime is
generated or patched here; the exact SRD_LITE_CANDIDATE supplies every mechanic.
The Hero is the exported materializer's unchanged output, not an imported grant.
Cosmetic flame/sound events are not claims about illumination radius or audio UI.
"""

import io
import json
from copy import deepcopy

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import CampaignCreate
from sylvia_gm.application.initial_character_state import (
    materialize_initial_character_state, verify_initial_character_state,
)
from sylvia_gm.domain.authority import (
    Participant, ParticipantValidationStatus, StateValue, WriterRole, thaw_json_value,
)
from sylvia_gm.domain.errors import CommandRejected
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_attack_budget import _combat_case
from tests.test_srd_lite_foundation import ARCHIVE, FULL, VARIANT_ID, Case
from tests.test_srd_lite_offensive_acquisition_incremental import _acolyte, _artifact, _contract


THAUMATURGY_SOURCE = "49b2000fd8566ea098e4052af87257f659dc929358cae97ddd4eb3740ca77e84"
INITIATE = ["thaumaturgy", "mending", "cure_wounds"]
TRANSITION_PHASES = {
    "start_combat_encounter_entities": "awaiting_turn_start",
    "begin_combat_turn": "turn_active",
    "end_combat_turn": "awaiting_advance",
    "advance_combat_encounter_turn": "awaiting_turn_start",
}


def _choices():
    participant, authored = _acolyte()
    participant["spellcasting_grants"][1]["spells"] = list(INITIATE)
    authored["prepared_spells"] = [*participant["spellcasting_grants"][0]["spells"], *INITIATE]
    # One material unit is the printed pair, with its original component identity.
    # The feat grants no class focus substitution; these are actual carried supplies.
    authored["inventory"].append("two_lodestones")
    authored["spell_materials"] = [{
        "id": "lodestones", "inventory_item": "two_lodestones", "kind": "specific",
        "components": ["mending.1"], "quantity": 1, "value_cp": 0,
    }]
    return participant, authored


def _scene_entity(identity, position, *, creature=False, metadata=None):
    return {
        "id": identity, "map_id": "lite", "position": position,
        "footprint": {"kind": "box", "half_extents": [0.4, 0.4] if creature else [0.5, 0.5]},
        "height": 6 if creature else 3, "facing": [1, 0], "active": True,
        "movement": "blocking", "visibility": "opaque",
        "metadata": {"plane": "material", "lighting": "bright", **(metadata or {})},
    }


class WorldCase(Case):
    def __init__(self, path, record, artifact, participant, materialized):
        self.path, self.manager, self.record = path, None, record
        state, _ = _combat_case([{"id": "sorcerer", "level": 1}])
        state[FULL + ".entities"]["hero"] = thaw_json_value(materialized)
        state[FULL + ".entities"]["guard"].update(
            hp=30, max_hp=30, temp_hp=0, armor_class=10, speed=30, size_rank=1,
            creature_type="humanoid", melee_reach=5, initiative_modifier=0,
            conditions=[], dodging=False, exhaustion_level=0, constitution_modifier=0,
            saving_throw_modifiers=dict.fromkeys(participant["abilities"], 0),
            available_reactions=["opportunity_attack"], reaction_available=True,
            stable=False, death_successes=0, death_failures=0, stable_recovery_hours_remaining=0,
        )
        state[FULL + ".combat"].update(
            encounter_active=False, turn_active=False, active_actor=None,
            phase="exploration", action_available=False,
        )
        state[FULL + ".state"] = {
            "lasting_tick": 0, "turn_effects": [], "spatial_scene": {
                "format": 1,
                "map": {
                    "id": "lite", "revision": 0,
                    "bounds": {"min": [-100, -100, -100], "max": [100, 100, 100]},
                    "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
                    "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}],
                },
                "entities": [
                    _scene_entity("hero", [0, 0, 0], creature=True, metadata={"participant_ref": "hero"}),
                    _scene_entity("guard", [10, 10, 0], creature=True, metadata={"participant_ref": "guard"}),
                    _scene_entity("relic", [3, 0, 0], metadata={
                        "fiction": "Synthetic broken enchanted wooden object",
                        "magic": "Synthetic broken enchantment",
                        "srd_spell_object": {"hp": 0, "destroyed": True},
                    }),
                    _scene_entity("far_relic", [40, 0, 0], metadata={"fiction": "Distant broken object"}),
                    _scene_entity("flame", [5, 0, 0], metadata={"fiction": "Burning ordinary candle"}),
                    _scene_entity("door", [15, 0, 0], metadata={"fiction": "Unlocked ordinary door"}),
                ],
                "geometry": [], "zones": [], "terrain": [],
            },
        }
        self.open()
        installed = self.owner.install_package_archive(io.BytesIO(ARCHIVE.read_bytes()), install_id="lite")
        exact = next(row for row in installed.package_lock.entries if row.package_id == VARIANT_ID)
        self.owner.create_campaign(CampaignCreate(
            campaign_id="lite", display_name="Original SRD Thaumaturgy and Mending",
            writer=WriterRole.DM, package_lock=installed.package_lock, turn_id="turn",
            participants=(Participant(
                participant_id="hero", kind="player_character", package_id=VARIANT_ID,
                package_version=exact.version, package_sha256=exact.sha256,
                schema_id=artifact["schema_id"], schema_sha256=artifact["schema_sha256"],
                validation_status=ParticipantValidationStatus.VALIDATED, data=participant,
            ),),
            initial_state=tuple(StateValue(VARIANT_ID, key.removeprefix(FULL + "."), value)
                                for key, value in state.items()),
        ))

    def object(self, identity):
        return next(row for row in self.lasting()["spatial_scene"]["entities"] if row["id"] == identity)


def _cast_parameters(spell, **choices):
    return {
        "caster": "hero", "caster_state": "hero", "spell": spell,
        "spell_grant": "magic_initiate_cleric", "slot_resource": "cantrip",
        "verbal_component_available": True, "somatic_component_available": True,
        "material_component_available": True,
        "material_item_ids": ["lodestones"] if spell == "mending" else [],
        **choices,
    }


def _sources(case):
    return [row for row in case.lasting()["turn_effects"]
            if row["kind"] == "minor_world_spell" and row["spell"] == "thaumaturgy"]


def _unavailable_recorded_receipt(case, receipt, command):
    """Current MCP admission rejects; public storage recovers only this exact command."""
    saved = case.snapshot()
    with pytest.raises(CommandRejected) as rejection:
        StatelessMcpMechanics(case.owner).execute_action(command)
    assert rejection.value.code == "mcp_tool_unavailable"
    assert case.snapshot() == saved
    # Do not rewrite parameters, expected revision, owner, turn or command identity.
    assert case.owner.execute_action(command) == receipt
    assert case.snapshot() == saved


def _remember(case, history, receipt, command):
    if command.tool_name in TRANSITION_PHASES:
        # Each successful transition immediately disables its own catalogue entry.
        assert case.snapshot().state[VARIANT_ID + ".combat"]["phase"] == TRANSITION_PHASES[command.tool_name]
        _unavailable_recorded_receipt(case, receipt, command)
    else:
        saved = case.snapshot()
        assert case.execute(command) == receipt and case.snapshot() == saved
    history.append((receipt, command))


def _replay_exploration(case, history):
    saved = case.snapshot()
    assert saved.state[VARIANT_ID + ".combat"]["encounter_active"] is False
    for receipt, command in history:
        assert case.execute(command) == receipt and case.snapshot() == saved


def _replay_active_combat(case, history):
    saved = case.snapshot()
    assert saved.state[VARIANT_ID + ".combat"]["phase"] == "turn_active"
    unavailable = {
        "cast_spell_outside_combat", "start_combat_encounter_entities",
        "begin_combat_turn", "advance_combat_encounter_turn",
    }
    for receipt, command in history:
        if command.tool_name in unavailable:
            _unavailable_recorded_receipt(case, receipt, command)
        elif command.tool_name == "end_combat_turn":
            # The historical guard-end command is available in the final active turn.
            assert StatelessMcpMechanics(case.owner).execute_action(command) == receipt
        else:
            assert case.execute(command) == receipt
        assert case.snapshot() == saved


def _reject_fresh_exploration_cast(case, history):
    saved = case.snapshot()
    command = case.command(
        "cast_spell_outside_combat", _cast_parameters("thaumaturgy", minor_mode="booming_voice"),
        identity=f"fresh-unavailable-exploration-{saved.revision}",
    )
    assert command.expected_revision == saved.revision
    assert all(command.action_id != old.action_id and command.idempotency_key != old.idempotency_key
               for _, old in history)
    with pytest.raises(CommandRejected) as rejection:
        StatelessMcpMechanics(case.owner).execute_action(command)
    assert rejection.value.code == "mcp_tool_unavailable"
    assert case.snapshot() == saved
    with pytest.raises(CommandRejected) as rejection:
        case.owner.execute_action(command)
    assert rejection.value.code == "tool_unavailable"
    assert case.snapshot() == saved


def _act(case, history, tool, **choices):
    receipt, command = case.act(tool, **choices)
    _remember(case, history, receipt, command)
    return receipt


def _cast(case, history, spell, *, tool="cast_spell_outside_combat", **choices):
    receipt = _act(case, history, tool, **_cast_parameters(spell, **choices))
    card = receipt.result["result_card"]
    assert card["title"] == ("Mending" if spell == "mending" else "Thaumaturgy")
    summary = {row["label"]: row["value"] for row in card["summary"]}
    assert str(summary["Spell slots spent"]) == "0"
    profile = receipt.result["context"]["_casting_profile"]
    assert profile["ability"] == "wisdom" and profile["modifier"] == 1
    assert profile["scaling_level"] == 1
    return receipt


def _check(case, history, *, advantage, skill="intimidation"):
    receipt = _act(
        case, history, "d20_test", actor="hero", actor_state="hero",
        kind="ability_check", ability="charisma", skill=skill,
        proficiency_factor="normal" if skill == "persuasion" else "none",
        circumstantial_modifier=0, target_number=15, advantage=False, disadvantage=False,
        requires_sight=False, requires_hearing=False, fear_source_in_sight=False,
    )
    context = receipt.result["context"]
    assert context["_effective_advantage"] is advantage
    assert context["_effective_disadvantage"] is False
    faces = context["_d20_test"]["results"]
    assert len(faces) == (2 if advantage else 1) and all(1 <= face <= 20 for face in faces)
    assert context["_ability_modifier"] == 3
    assert receipt.result["result_card"]
    return receipt


def test_complete_acolyte_thaumaturgy_mending_world_consumers_cost_expiry_and_reopen(tmp_path):
    record, artifact = _artifact()
    participant, authored = _choices()
    original = deepcopy((participant, authored))
    completed = materialize_initial_character_state(participant, authored, **_contract(artifact))
    assert (participant, authored) == original
    assert completed["hp"] == completed["max_hp"] == 9 and completed["armor_class"] == 11
    assert completed["spell_materials"] == authored["spell_materials"]
    assert completed["prepared_spells"] == authored["prepared_spells"]
    assert verify_initial_character_state(participant, completed, **_contract(artifact)) == completed
    invalid, invalid_state = deepcopy(original)
    invalid["spellcasting_grants"][1]["spells"].remove("mending")
    invalid_state["prepared_spells"].remove("mending")
    with pytest.raises(CommandRejected):
        materialize_initial_character_state(invalid, invalid_state, **_contract(artifact))

    case = WorldCase.__new__(WorldCase)
    history, cards = [], []
    try:
        case.__init__(tmp_path / "progression-cantrips-c.db", record, artifact, participant, completed)
        assert case.entities()["hero"] == completed
        participants = case.snapshot().participants
        resources = deepcopy(case.entities()["hero"]["resources"])
        maxima = deepcopy(case.entities()["hero"]["resource_maxima"])
        materials = deepcopy(case.entities()["hero"]["spell_materials"])
        preserved_magic = deepcopy(case.object("relic")["metadata"]["magic"])
        preserved_damage = deepcopy(case.object("relic")["metadata"]["srd_spell_object"])
        for break_id, height in (("tear", 1), ("other_tear", 0.5), ("oversized", 2)):
            _act(case, history, "record_srd_object_break", object_id="relic", break_id=break_id,
                 break_min=[3, 0, 0], break_max=[3, 0, height])
        _act(case, history, "record_srd_object_break", object_id="far_relic", break_id="tear",
             break_min=[40, 0, 0], break_max=[40, 0, 1])
        _act(case, history, "record_srd_minor_world_subject", object_id="flame", kind="candle",
             nonliving=True, lit=True, exposed=True)
        _act(case, history, "record_srd_minor_world_subject", object_id="door", kind="door",
             nonliving=True, held_open=False)
        _act(case, history, "record_srd_object_access", object_id="door", object_kind="door",
             closed=True, locks=[], stuck=False, barred=False)

        mending = _cast_parameters("mending", object_target_id="relic", object_break_id="tear")
        case.reject("cast_spell_outside_combat", mending)
        _act(case, history, "begin_srd_spell_cast", **_cast_parameters("mending"))
        assert case.entities()["hero"]["extended_casting"]["required_rounds"] == 10
        assert case.entities()["hero"]["extended_casting"]["progress_rounds"] == 0
        assert case.entities()["hero"]["concentration_spell_id"] == "mending"
        continuation = dict(caster_state="hero", uninterrupted=True,
                            verbal_component_available=True, somatic_component_available=True,
                            material_component_available=True)
        _act(case, history, "continue_srd_spell_cast", elapsed_rounds=9, **continuation)
        assert case.entities()["hero"]["extended_casting"]["progress_rounds"] == 9
        case.reject("cast_spell_outside_combat", mending)
        _act(case, history, "continue_srd_spell_cast", elapsed_rounds=1, **continuation)
        assert case.entities()["hero"]["extended_casting"]["progress_rounds"] == 10
        case.reject("cast_spell_outside_combat", mending | {"object_break_id": "oversized"}, match="one foot")
        case.reject("cast_spell_outside_combat", mending | {"object_target_id": "far_relic"}, match="range")
        case.reject("cast_spell_outside_combat", mending | {"somatic_component_available": False})
        repair = _cast(case, history, "mending", object_target_id="relic", object_break_id="tear")
        cards.append(thaw_json_value(repair.result["result_card"]))
        assert [row["id"] for row in case.object("relic")["metadata"]["srd_object_breaks"]] == ["other_tear", "oversized"]
        assert case.object("relic")["metadata"]["magic"] == preserved_magic
        assert case.object("relic")["metadata"]["srd_spell_object"] == preserved_damage
        assert case.entities()["hero"].get("extended_casting") is None
        assert case.entities()["hero"].get("concentration_spell_id") is None
        assert not any(row.get("spell") == "mending" for row in case.lasting()["turn_effects"])

        _check(case, history, advantage=False)
        case.reject("cast_spell_outside_combat", _cast_parameters(
            "thaumaturgy", minor_mode="booming_voice", verbal_component_available=False))
        boom = _cast(case, history, "thaumaturgy", minor_mode="booming_voice",
                     minor_description="A booming appeal to the uncertain synthetic guard")
        cards.append(thaw_json_value(boom.result["result_card"]))
        voice = deepcopy(_sources(case)[0])
        assert voice["source_sha256"] == THAUMATURGY_SOURCE and voice["caster"] == "hero"
        assert voice["mode"] == "booming_voice" and voice["expires_tick"] - voice["started_tick"] == 20
        assert voice["dispel_ongoing"] is True
        _check(case, history, advantage=True)
        _check(case, history, advantage=False, skill="persuasion")
        flame_before = deepcopy(case.object("flame")["metadata"]["srd_ordinary_flame"])
        fire = _cast(case, history, "thaumaturgy", minor_mode="fire_play", minor_object_id="flame",
                     minor_operation="color", minor_color="violet", minor_description="Violet candle flame")
        cards.append(thaw_json_value(fire.result["result_card"]))
        fire_source = next(row for row in _sources(case) if row["mode"] == "fire_play")
        assert fire_source["object_id"] == "flame" and fire_source["color"] == "violet"
        assert case.object("flame")["metadata"]["srd_ordinary_flame"] == flame_before
        _cast(case, history, "thaumaturgy", minor_mode="altered_eyes", minor_description="Silver eyes")
        eyes = next(row for row in _sources(case) if row["mode"] == "altered_eyes")
        assert len(_sources(case)) == 3
        case.reject("cast_spell_outside_combat", _cast_parameters(
            "thaumaturgy", minor_mode="tremors", minor_point=[10, -10, 0]), match="three")

        sound = _cast(case, history, "thaumaturgy", minor_mode="phantom_sound",
                      minor_point=[20, 5, 0], minor_description="A distant raven call")
        cards.append(thaw_json_value(sound.result["result_card"]))
        event = case.lasting()["srd_last_minor_world_event"]
        assert event["spell"] == "thaumaturgy" and event["mode"] == "phantom_sound"
        assert event["origin"] == [20, 5, 0] and event["description"] == "A distant raven call"
        assert event["deals_damage"] is False and len(_sources(case)) == 3
        _cast(case, history, "thaumaturgy", minor_mode="invisible_hand", minor_object_id="door",
              minor_operation="open")
        assert case.object("door")["metadata"]["srd_object_access"]["closed"] is False
        assert case.object("door")["movement"] == "passable" and case.object("door")["visibility"] == "clear"
        assert len(_sources(case)) == 3
        _cast(case, history, "thaumaturgy", minor_mode="tremors", minor_point=[10, -10, 0],
              minor_retire_source_id=eyes["source_id"], minor_description="Harmless ground tremors")
        assert len(_sources(case)) == 3 and eyes["source_id"] not in {row["source_id"] for row in _sources(case)}
        assert voice["source_id"] in {row["source_id"] for row in _sources(case)}
        _act(case, history, "advance_srd_lasting_time", elapsed_rounds=9)
        assert len(_sources(case)) == 3
        _check(case, history, advantage=True)
        _act(case, history, "advance_srd_lasting_time", elapsed_rounds=1)
        assert _sources(case) == []
        _check(case, history, advantage=False)
        # Instantaneous door movement and repaired physical material survive expiry.
        assert case.object("door")["metadata"]["srd_object_access"]["closed"] is False
        assert [row["id"] for row in case.object("relic")["metadata"]["srd_object_breaks"]] == ["other_tear", "oversized"]
        assert case.object("relic")["metadata"]["magic"] == preserved_magic
        assert case.object("relic")["metadata"]["srd_spell_object"] == preserved_damage

        # Prove actual outside-cast MCP retries and reopen while exploration is available.
        exploration = case.snapshot()
        _replay_exploration(case, history)
        case.reopen()
        assert case.snapshot() == exploration
        _replay_exploration(case, history)

        initiative = _act(
            case, history, "start_combat_encounter_entities", first_actor="hero", second_actor="guard",
            first_surprised=False, second_surprised=False, tie_winner="first",
        )
        assert initiative.result["context"]["_initiative_first_roll"]["modifier"] == 1
        assert initiative.result["context"]["_initiative_second_roll"]["modifier"] == 0
        _act(case, history, "begin_combat_turn")
        if case.snapshot().state[VARIANT_ID + ".combat"]["active_actor"] == "guard":
            # Preserve real Initiative; the synthetic guard takes an ordinary empty turn.
            _act(case, history, "end_combat_turn")
            _act(case, history, "advance_combat_encounter_turn")
            _act(case, history, "begin_combat_turn")
        assert case.snapshot().state[VARIANT_ID + ".combat"]["active_actor"] == "hero"
        assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is True
        combat_cast = _cast(case, history, "thaumaturgy", tool="cast_spell", minor_mode="booming_voice")
        cards.append(thaw_json_value(combat_cast.result["result_card"]))
        assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is False
        assert case.entities()["hero"].get("concentration_spell_id") is None
        assert case.entities()["hero"]["resources"] == resources
        assert case.entities()["hero"]["resource_maxima"] == maxima
        assert case.entities()["hero"]["spell_materials"] == materials
        assert case.snapshot().participants == participants
        final = case.snapshot()
        _replay_active_combat(case, history)
        _reject_fresh_exploration_cast(case, history)
        case.reopen()
        assert case.snapshot() == final
        _replay_active_combat(case, history)
        _reject_fresh_exploration_cast(case, history)
        (tmp_path / "progression-cantrips-c-observations.json").write_text(json.dumps({
            "qualification": "Complete exported materialization; original public cast and consumers; no Builder/UI or full progression claim",
            "participant": participant, "materialized": thaw_json_value(completed),
            "cards": cards, "exact_retry_and_reopen": True,
            "same_phase_mcp_cast_retry_and_reopen": True,
            "cross_phase_receipts": "Unchanged recorded ActionCommands via public AuthorityOwner; current MCP admission rejects unavailable intents",
            "fresh_cross_phase_command": "MCP mcp_tool_unavailable and AuthorityOwner tool_unavailable; full snapshot unchanged",
            "mcp_ui_cross_phase_recovery": "Unproved",
        }, indent=2), encoding="utf-8")
    finally:
        if getattr(case, "manager", None):
            archive_closed_case(case)
