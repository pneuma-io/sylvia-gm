"""Original SRD Spare the Dying through acquisition and admitted public owners.

The caster is the exported materializer's unchanged complete Cleric output.
Recipients are honestly imported synthetic character bodies; actual public
Falling establishes the dying state. This proves package/application mechanics,
not Builder UI, printed-NPC production or a map-triggered fall. Root owns native
execution and stages this new module only after the current packet is sealed.
"""

import io
import json
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
from tests.lite_imported_fixture import ImportedCase
from tests.test_srd_attack_budget import _combat_case
from tests.test_srd_lite_foundation import ARCHIVE, FULL, VARIANT_ID, Case


CANTRIPS = ["spare_the_dying", "thaumaturgy", "sacred_flame"]
PREPARED = ["bless", "cure_wounds", "healing_word", "guiding_bolt"]


def _artifact():
    with zipfile.ZipFile(ARCHIVE) as archive:
        record = next(row for row in json.loads(archive.read("manifest.json"))["packages"]
                      if row["id"] == VARIANT_ID)
    return record, next(row for row in record["participant_artifacts"]
                        if row["kind"] == "player_character")


def _contract(artifact):
    return {key: artifact[key] for key in (
        "entity_state_schema", "initial_state_calculations", "initial_state_validation",
    )}


def _cleric_choices():
    """Complete level-one Cleric and Dwarf/Soldier choices, including Protector."""
    repertoire = [*CANTRIPS, *PREPARED]
    participant = {
        "name": "Cleric of the shared Stable transition", "level": 1,
        "classes": [{"id": "cleric", "level": 1}],
        "species": "dwarf", "background": "soldier", "speed": 30, "size_rank": 2,
        # Standard array plus Soldier's +1 Strength/Dexterity/Constitution.
        "abilities": dict(strength=9, dexterity=14, constitution=15,
                          intelligence=10, wisdom=15, charisma=12),
        "saving_throw_proficiencies": ["wisdom", "charisma"],
        "origin_feats": ["savage_attacker"], "cleric_order": "protector",
        "skill_proficiencies": ["athletics", "intimidation", "insight", "religion"],
        "tool_proficiencies": ["gaming_set_dragonchess"],
        "weapon_proficiencies": ["simple_weapons", "martial_weapons"],
        "weapon_masteries": [], "subclasses": {}, "wizard_spellbook": [],
        "spellcasting_grants": [{"source": "cleric", "ability": "wisdom",
                                "spells": repertoire}],
    }
    authored = {
        # d8 + Constitution 2 + Dwarven Toughness 1.
        "max_hp": 11, "hit_die_size": 8,
        "resources": {"hit_die": 1, "spell_slot_1": 2},
        "resource_maxima": {"hit_die": 1, "spell_slot_1": 2},
        "prepared_spells": repertoire, "inventory": ["dagger", "holy_symbol"],
        "worn_armor": None, "worn_shield": None, "conditions": [],
        "damage_immunities": [], "damage_resistances": ["poison"],
        "damage_vulnerabilities": [], "available_bonus_actions": [],
    }
    return participant, authored


class SpareCase(Case):
    def __init__(self, path, record, artifact, participant, materialized):
        self.path, self.manager, self.record = path, None, record
        state, _ = _combat_case([{"id": "cleric", "level": 1}])
        entities = state[FULL + ".entities"]
        entities["hero"] = thaw_json_value(materialized)
        recipient = {
            "name": "Wounded synthetic ally", "creature_kind": "character",
            "creature_type": "humanoid", "hp": 2, "max_hp": 1000,
            "is_alive": True, "armor_class": 10, "speed": 30, "size_rank": 2,
            "conditions": [], "exhaustion_level": 0, "temp_hp": 0,
            "damage_immunities": [], "damage_resistances": [],
            "damage_vulnerabilities": [], "resources": {}, "resource_maxima": {},
            "initiative_modifier": 0, "constitution_modifier": 0, "melee_reach": 5,
            "saving_throw_modifiers": dict.fromkeys(participant["abilities"], 0),
            "skill_check_modifiers": {"acrobatics": 0}, "dodging": False,
            "reaction_available": True, "available_reactions": ["opportunity_attack"],
            "stable": False, "death_successes": 0, "death_failures": 0,
            "stable_recovery_hours_remaining": 0,
        }
        entities["guard"] = recipient
        entities["dead"] = deepcopy(recipient) | {
            "name": "Imported dead control", "hp": 0, "is_alive": False,
            "conditions": ["dead"], "death_failures": 3,
        }
        entities["far"] = deepcopy(recipient) | {
            "name": "Imported dying range control", "hp": 0,
            "conditions": ["unconscious"],
        }
        state[FULL + ".combat"].update(
            encounter_active=False, turn_active=False, active_actor=None,
            phase="exploration", action_available=False,
        )
        positions = {"hero": [0, 0, 0], "guard": [15, 0, 0], "dead": [10, 0, 0],
                     "far": [16, 0, 0]}
        scene = {
            "format": 1,
            "map": {
                "id": "lite", "revision": 0,
                "bounds": {"min": [-200, -200, -100], "max": [200, 200, 100]},
                "grid": {"cell_size": 5, "unit": "ft", "origin": [-200, -200]},
                "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}],
            },
            "entities": [{
                "id": identity, "map_id": "lite", "position": point,
                "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
                "height": 6, "facing": [1, 0], "active": True,
                "movement": "blocking", "visibility": "opaque",
                "metadata": {"participant_ref": identity, "plane": "material",
                             "lighting": "bright", "creature_type": "humanoid"},
            } for identity, point in positions.items()],
            "geometry": [], "zones": [], "terrain": [],
        }
        state[FULL + ".state"] = {"lasting_tick": 0, "turn_effects": [], "spatial_scene": scene}
        self.parameters = {
            "spell": "spare_the_dying", "caster": "hero", "caster_state": "hero",
            "target": "guard", "slot_resource": "cantrip", "target_distance": 15,
            "caster_can_see_target": True, "caster_can_hear_target": True,
            "caster_can_touch_target": False, "target_willing": True, "cover": "none",
            "verbal_component_available": True, "somatic_component_available": True,
            "material_component_available": False, "material_item_ids": [],
        }
        self.open()
        installed = self.owner.install_package_archive(io.BytesIO(ARCHIVE.read_bytes()),
                                                       install_id="lite")
        exact = next(row for row in installed.package_lock.entries if row.package_id == VARIANT_ID)
        self.owner.create_campaign(CampaignCreate(
            campaign_id="lite", display_name="Original SRD Spare the Dying lifecycle",
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


def _receipt_once(case, receipt, command, *, reopen=False):
    before = case.snapshot()
    assert case.execute(command) == receipt
    assert case.snapshot() == before
    if reopen:
        case.reopen()
        assert case.snapshot().state == before.state
        assert case.snapshot().revision == before.revision
        assert case.snapshot().participants == before.participants
        assert case.snapshot().package_lock == before.package_lock
        assert case.execute(command) == receipt
        assert case.snapshot().state == before.state
        assert case.snapshot().revision == before.revision


def _stable_cast(case, *, changed):
    resources = deepcopy(case.entities()["hero"]["resources"])
    tick = case.lasting()["lasting_tick"]
    receipt, command = case.cast()
    context = receipt.result["context"]
    profile = context["_casting_profile"]
    assert profile["ability"] == "wisdom" and profile["scaling_level"] == 1
    assert context["_spell_stabilization_range"] == 15
    assert context["_spell_stable_changed"] is changed
    card = receipt.result["result_card"]
    assert card["title"] == "Spare the Dying"
    summary = {row["label"]: row["value"] for row in card["summary"]}
    assert summary["Spell slots spent"] == 0
    assert any("stable at 0 HP" in str(row["value"]) for row in card["details"])
    assert case.entities()["hero"]["resources"] == resources
    assert case.lasting()["lasting_tick"] == tick
    ally = case.entities()["guard"]
    assert ally["is_alive"] is True and ally["hp"] == 0 and ally["stable"] is True
    assert "unconscious" in ally["conditions"]
    assert ally["death_successes"] == ally["death_failures"] == 0
    return receipt, command


def test_complete_cleric_acquires_spare_dying_and_real_stability_recovers_once(tmp_path):
    record, artifact = _artifact()
    participant, authored = _cleric_choices()
    original = deepcopy((participant, authored))
    completed = materialize_initial_character_state(participant, authored, **_contract(artifact))
    assert (participant, authored) == original
    assert verify_initial_character_state(participant, completed, **_contract(artifact)) == completed
    assert completed["prepared_spells"] == [*CANTRIPS, *PREPARED]
    assert completed["hp"] == completed["max_hp"] == 11
    assert completed["armor_class"] == 12 and completed["speed"] == 30
    assert completed["saving_throw_modifiers"]["wisdom"] == 4
    for index in (0, 1):
        invalid, invalid_state = deepcopy(original)
        if index == 0:
            invalid["spellcasting_grants"][0]["spells"].remove("spare_the_dying")
        else:
            invalid_state["prepared_spells"].remove("spare_the_dying")
        before = deepcopy((invalid, invalid_state))
        with pytest.raises(CommandRejected):
            materialize_initial_character_state(invalid, invalid_state, **_contract(artifact))
        assert (invalid, invalid_state) == before

    case = SpareCase.__new__(SpareCase)
    try:
        case.__init__(tmp_path / "spare-dying-natural-cleric.db", record, artifact,
                      participant, completed)
        assert case.entities()["hero"] == completed
        initial = case.snapshot()
        case.reject("cast_spell_outside_combat", case.parameters)
        case.reject("cast_spell_outside_combat", case.parameters | {
            "target": "dead", "target_distance": 10,
        })
        assert case.snapshot() == initial
        fall, fall_command = case.act(
            "resolve_srd_fall", target_state="guard", distance=20, landing="ground",
            reaction="none", advantage=False, disadvantage=False, fear_source_in_sight=False,
        )
        assert fall.result["result_card"]["title"] == "Falling"
        assert len(fall.result["context"]["_fall_roll"]["results"]) == 2
        assert case.entities()["guard"]["hp"] == 0
        assert case.entities()["guard"]["is_alive"] is True
        assert case.entities()["guard"]["stable"] is False
        assert "unconscious" in case.entities()["guard"]["conditions"]
        _receipt_once(case, fall, fall_command, reopen=True)
        before = case.snapshot()
        for choices in (
            {"verbal_component_available": False},
            {"somatic_component_available": False},
            {"slot_resource": "spell_slot_1"},
            {"target": "far", "target_distance": 16},
        ):
            case.reject("cast_spell_outside_combat", case.parameters | choices)
        assert case.snapshot() == before

        first, first_command = _stable_cast(case, changed=True)
        roll = first.result["context"]["_spell_stable_recovery_delay"]
        assert len(roll["results"]) == 1 and 1 <= roll["results"][0] <= 4
        assert case.entities()["guard"]["stable_recovery_hours_remaining"] == roll["total"]
        _receipt_once(case, first, first_command, reopen=True)
        repeat, repeat_command = _stable_cast(case, changed=False)
        assert repeat.result["context"]["_spell_stable_recovery_delay"] is None
        assert case.entities()["guard"]["stable_recovery_hours_remaining"] == roll["total"]
        _receipt_once(case, repeat, repeat_command)

        injured, injured_command = case.act(
            "resolve_srd_fall", target_state="guard", distance=10, landing="ground",
            reaction="none", advantage=False, disadvantage=False, fear_source_in_sight=False,
        )
        assert len(injured.result["context"]["_fall_roll"]["results"]) == 1
        ally = case.entities()["guard"]
        assert ally["hp"] == 0 and ally["is_alive"] is True and ally["stable"] is False
        assert ally["death_failures"] == 1 and ally["stable_recovery_hours_remaining"] == 0
        _receipt_once(case, injured, injured_command)
        _receipt_once(case, first, first_command)
        restored, restored_command = _stable_cast(case, changed=True)
        delay = restored.result["context"]["_spell_stable_recovery_delay"]["total"]
        assert 1 <= delay <= 4
        assert case.entities()["guard"]["stable_recovery_hours_remaining"] == delay
        _receipt_once(case, restored, restored_command)
        tick = case.lasting()["lasting_tick"]
        recovered, recovered_command = case.act(
            "advance_stable_recovery", target="guard", elapsed_hours=delay,
        )
        assert recovered.result["context"]["_recovery_complete"] is True
        ally = case.entities()["guard"]
        assert ally["hp"] == 1 and ally["is_alive"] is True and ally["stable"] is False
        assert ally["death_successes"] == ally["death_failures"] == 0
        assert ally["stable_recovery_hours_remaining"] == 0
        assert "unconscious" not in ally["conditions"]
        assert case.lasting()["lasting_tick"] == tick + delay * 1200
        assert case.entities()["hero"]["resources"] == completed["resources"]
        assert case.snapshot().package_lock == initial.package_lock
        _receipt_once(case, recovered, recovered_command, reopen=True)
        _receipt_once(case, first, first_command)
        _receipt_once(case, restored, restored_command)
        case.reject("cast_spell_outside_combat", case.parameters)
    finally:
        archive_closed_case(case)


@pytest.mark.parametrize("total_level,range_feet", [(5, 30), (11, 60), (17, 120)])
def test_spare_dying_natural_imported_source_uses_total_level_range_once(
    tmp_path, total_level, range_feet,
):
    """Imported multiclass sources qualify casting; this is not advancement proof."""
    participant, _ = _cleric_choices()
    participant.update(
        name=f"Imported Cleric1 Fighter{total_level - 1}", level=total_level,
        classes=[{"id": "cleric", "level": 1}, {"id": "fighter", "level": total_level - 1}],
        subclasses={"fighter": "champion"},
    )
    # These bodies are disclosed already-dying recipients, not fake damage rolls.
    dying = {
        "creature_kind": "character", "hp": 0, "max_hp": 20, "is_alive": True,
        "conditions": ["unconscious"], "stable": False,
        "death_successes": 2, "death_failures": 1, "stable_recovery_hours_remaining": 0,
    }
    positions = {
        "hero": [-range_feet / 2, 0, 0], "guard": [range_feet / 2, 0, 0],
        "other": [range_feet / 2 + 1, 0, 0], "extra": [-60, -50, 0],
    }
    case = ImportedCase.__new__(ImportedCase)
    try:
        case.__init__(
            tmp_path / f"spare-dying-total-level-{total_level}.db", "spare_the_dying",
            positions=positions, participant_changes=participant,
            entity_changes={
                "hero": {
                    "prepared_spells": [*CANTRIPS, *PREPARED],
                    "resources": {"spell_slot_1": 0}, "resource_maxima": {"spell_slot_1": 2},
                },
                "guard": dying, "other": deepcopy(dying),
            },
        )
        choices = case.parameters | {
            "target_distance": range_feet, "caster_can_touch_target": False,
            "material_component_available": False,
        }
        initial = case.snapshot()
        case.reject("cast_spell_outside_combat", choices | {
            "target": "other", "target_distance": range_feet + 1,
        }, match="range")
        assert case.snapshot() == initial
        receipt, command = case.act("cast_spell_outside_combat", **choices)
        context = receipt.result["context"]
        assert context["_casting_profile"]["scaling_level"] == total_level
        assert context["_casting_profile"]["ability"] == "wisdom"
        assert context["_spell_stabilization_range"] == range_feet
        assert context["_spell_stable_changed"] is True
        roll = context["_spell_stable_recovery_delay"]
        assert len(roll["results"]) == 1 and 1 <= roll["results"][0] <= 4
        assert roll["total"] == roll["results"][0]
        ally = case.entities()["guard"]
        assert ally["is_alive"] is True and ally["hp"] == 0 and ally["stable"] is True
        assert ally["death_successes"] == ally["death_failures"] == 0
        assert ally["stable_recovery_hours_remaining"] == roll["total"]
        assert "unconscious" in ally["conditions"]
        assert case.entities()["other"] == initial.state[VARIANT_ID + ".entities"]["other"]
        assert case.entities()["hero"]["resources"] == {"spell_slot_1": 0}
        assert case.lasting()["lasting_tick"] == initial.state[VARIANT_ID + ".state"]["lasting_tick"]
        card = receipt.result["result_card"]
        assert card["title"] == "Spare the Dying"
        assert {row["label"]: row["value"] for row in card["summary"]}["Spell slots spent"] == 0
        assert any("stable at 0 HP" in str(row["value"]) for row in card["details"])
        assert case.snapshot().participants == initial.participants
        assert case.snapshot().package_lock == initial.package_lock
        _receipt_once(case, receipt, command, reopen=True)
    finally:
        archive_closed_case(case)
