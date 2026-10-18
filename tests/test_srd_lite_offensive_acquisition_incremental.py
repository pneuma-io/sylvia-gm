"""Complete Sorcerer/Acolyte acquisition and seven public SRD cantrip consumers.

The Hero is the exported materializer's unchanged complete output. Bard and
Warlock are explicitly imported casting fixtures: their natural saved sources
prove casting eligibility, not complete creation, advancement or Builder/UI.
One campaign/install supplies real dice, receipts and reopen evidence. The parent
owns native execution; this module changes no runtime or private validator.
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


SORCERER = ["fire_bolt", "ray_of_frost", "shocking_grasp", "poison_spray",
            "magic_missile", "shield"]
INITIATE = ["thaumaturgy", "sacred_flame", "cure_wounds"]


def _artifact():
    with zipfile.ZipFile(ARCHIVE) as archive:
        record = next(row for row in json.loads(archive.read("manifest.json"))["packages"]
                      if row["id"] == VARIANT_ID)
    return record, next(row for row in record["participant_artifacts"]
                        if row["kind"] == "player_character")


def _acolyte():
    """Resolve the printed Dwarf/Acolyte and Sorcerer choices before calculating."""
    participant = {
        "name": "Sorcerer with Acolyte Magic Initiate", "level": 1,
        "classes": [{"id": "sorcerer", "level": 1}],
        "species": "dwarf", "background": "acolyte", "speed": 30, "size_rank": 2,
        # Standard array plus Acolyte +1 Intelligence/Wisdom/Charisma.
        "abilities": dict(strength=8, dexterity=13, constitution=14,
                          intelligence=11, wisdom=13, charisma=16),
        "saving_throw_proficiencies": ["constitution", "charisma"],
        "origin_feats": ["magic_initiate"],
        "skill_proficiencies": ["arcana", "persuasion", "insight", "religion"],
        "tool_proficiencies": ["calligraphers_supplies"],
        "weapon_proficiencies": ["simple_weapons"], "weapon_masteries": [],
        "subclasses": {}, "wizard_spellbook": [],
        "spellcasting_grants": [
            {"source": "sorcerer", "ability": "charisma", "spells": list(SORCERER)},
            {"source": "magic_initiate_cleric", "ability": "wisdom",
             "spells": list(INITIATE)},
        ],
    }
    authored = {
        # Authored maximum: d6 + Constitution 2 + Dwarven Toughness 1.
        "max_hp": 9, "hit_die_size": 6,
        "resources": {"hit_die": 1, "spell_slot_1": 2},
        "resource_maxima": {"hit_die": 1, "spell_slot_1": 2},
        "prepared_spells": [*SORCERER, *INITIATE],
        "inventory": ["dagger", "arcane_focus", "holy_symbol", "calligraphers_supplies"],
        "weapon_sets": {"format": 1, "active_set": None,
                        "melee": {"main_hand": None, "off_hand": None},
                        "ranged": {"main_hand": None, "off_hand": None}},
        "worn_armor": None, "worn_shield": None, "conditions": [],
        "damage_immunities": [], "damage_resistances": ["poison"],
        "damage_vulnerabilities": [], "available_bonus_actions": [],
    }
    return participant, authored


def _contract(artifact):
    return {key: artifact[key] for key in (
        "entity_state_schema", "initial_state_calculations", "initial_state_validation",
    )}


def _imported(class_id, spell):
    """Declare an existing character's narrow natural source, without creating it."""
    state, data = _combat_case([{"id": class_id, "level": 1}])
    data.update(
        name=f"Imported {class_id} casting fixture", species="dwarf", background="soldier",
        size_rank=2, origin_feats=["savage_attacker"], subclasses={}, wizard_spellbook=[],
        saving_throw_proficiencies=(["dexterity", "charisma"] if class_id == "bard"
                                   else ["wisdom", "charisma"]),
        spellcasting_grants=[{"source": class_id, "ability": "charisma", "spells": [spell]}],
    )
    data["abilities"]["charisma"] = 16
    entity = state[FULL + ".entities"]["hero"]
    entity.update(
        name=data["name"], hp=11, max_hp=11, hit_die_size=8, size_rank=2,
        prepared_spells=[spell], spell_slot_cast_this_turn=False,
        resources={"hit_die": 1, "spell_slot_1": 2 if class_id == "bard" else 1},
        resource_maxima={"hit_die": 1, "spell_slot_1": 2 if class_id == "bard" else 1},
        conditions=[], dodging=False, exhaustion_level=0,
        initiative_modifier=2, constitution_modifier=2, melee_reach=5,
        saving_throw_modifiers=dict.fromkeys(data["abilities"], 0),
        stable=False, death_successes=0, death_failures=0, stable_recovery_hours_remaining=0,
    )
    return data, entity


class AcquisitionCase(Case):
    def __init__(self, path, record, artifact, participant, materialized):
        self.path, self.manager, self.record = path, None, record
        state, _ = _combat_case([{"id": "sorcerer", "level": 1}])
        entities = state[FULL + ".entities"]
        entities["hero"] = thaw_json_value(materialized)
        bard, entities["bard"] = _imported("bard", "vicious_mockery")
        warlock, entities["warlock"] = _imported("warlock", "eldritch_blast")
        # Disclosed synthetic target. Real saving dice still run; -20 is its
        # initial modifier, not a dice override. Supply all actual Dodge facts.
        entities["guard"].update(
            name="Synthetic acquisition target", hp=1000, max_hp=1000, temp_hp=0,
            armor_class=1, speed=30, size_rank=1, creature_type="humanoid",
            melee_reach=5, initiative_modifier=0, constitution_modifier=0,
            conditions=[], dodging=False, exhaustion_level=0,
            saving_throw_modifiers=dict.fromkeys(participant["abilities"], -20),
            skill_check_modifiers={"acrobatics": 0},
            attitudes={identity: "hostile" for identity in ("hero", "bard", "warlock")},
            available_reactions=["opportunity_attack"], reaction_available=True,
            stable=False, death_successes=0, death_failures=0, stable_recovery_hours_remaining=0,
        )
        state[FULL + ".combat"].update(
            encounter_active=False, turn_active=False, active_actor=None,
            phase="exploration", action_available=False,
        )
        positions = {"hero": [0, 0, 0], "guard": [5, 0, 0],
                     "bard": [0, -5, 0], "warlock": [-5, 5, 0]}
        state[FULL + ".state"] = {
            "lasting_tick": 0, "turn_effects": [], "spatial_scene": {
                "format": 1,
                "map": {
                    "id": "lite", "revision": 0,
                    "bounds": {"min": [-100, -100, -100], "max": [100, 100, 100]},
                    "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
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
            },
        }
        self.parameters = {
            "spell": "fire_bolt", "caster": "hero", "caster_state": "hero",
            "target": "guard", "slot_resource": "cantrip", "target_distance": 5,
            "caster_can_see_target": True, "caster_can_hear_target": True,
            "caster_can_touch_target": True, "target_willing": False, "cover": "none",
            "verbal_component_available": True, "somatic_component_available": True,
            "material_component_available": True, "material_item_ids": [],
        }
        self.open()
        installed = self.owner.install_package_archive(io.BytesIO(ARCHIVE.read_bytes()),
                                                       install_id="lite")
        exact = next(row for row in installed.package_lock.entries if row.package_id == VARIANT_ID)
        self.owner.create_campaign(CampaignCreate(
            campaign_id="lite", display_name="SRD cantrip source acquisition",
            writer=WriterRole.DM, package_lock=installed.package_lock, turn_id="turn",
            participants=tuple(Participant(
                participant_id=identity, kind="player_character", package_id=VARIANT_ID,
                package_version=exact.version, package_sha256=exact.sha256,
                schema_id=artifact["schema_id"], schema_sha256=artifact["schema_sha256"],
                validation_status=ParticipantValidationStatus.VALIDATED, data=data,
            ) for identity, data in (("hero", participant), ("bard", bard), ("warlock", warlock))),
            initial_state=tuple(StateValue(VARIANT_ID, key.removeprefix(FULL + "."), value)
                                for key, value in state.items()),
        ))


def _retain(case, history, receipt, command):
    if command.tool_name == "cast_spell_outside_combat":
        card = receipt.result["result_card"]
        summary = {row["label"]: row["value"] for row in card["summary"]}
        assert str(summary["Spell slots spent"]) == "0"
    saved = case.snapshot()
    assert case.execute(command) == receipt and case.snapshot() == saved
    history.append((receipt, command))


def _cast_with_actual_grasp_hit(case, choices, history):
    """Keep real misses; make another lawful cast only to obtain the status proof."""
    for _ in range(8 if choices["spell"] == "shocking_grasp" else 1):
        hp = case.entities()["guard"]["hp"]
        receipt, command = case.act("cast_spell_outside_combat", **choices)
        if (choices["spell"] != "shocking_grasp"
                or receipt.result["context"]["_spell_attack_outcome"] == "success"):
            return receipt, command, hp
        assert receipt.result["context"]["_spell_attack_outcome"] == "failure"
        assert receipt.result["context"]["_spell_attack"]["results"]
        assert case.entities()["guard"]["hp"] == hp
        _retain(case, history, receipt, command)
        elapsed, elapsed_command = case.act("advance_srd_lasting_time", elapsed_rounds=1)
        _retain(case, history, elapsed, elapsed_command)
    raise AssertionError("Eight lawful Shocking Grasp casts produced no actual hit.")


def _grasp_other_reaction(case, history):
    """The genuine SRD opportunity block must leave liquid-landing Reaction usable."""
    assert case.entities()["guard"]["opportunity_attacks_blocked"] is True
    assert "guard" in case.lasting()["opportunity_blocked_targets"]
    assert case.entities()["guard"]["reaction_available"] is True
    hp = case.entities()["guard"]["hp"]
    receipt, command = case.act(
        "resolve_srd_fall", target_state="guard", distance=20, landing="liquid",
        reaction="water_acrobatics", advantage=False, disadvantage=False,
        fear_source_in_sight=False,
    )
    context = receipt.result["context"]
    assert receipt.result["result_card"]["title"] == "Falling"
    assert len(context["_fall_roll"]["results"]) == 2
    assert len(context["_fall_check_roll"]["results"]) == 1
    assert context["_fall_check_modifier"] == 0
    assert case.entities()["guard"]["hp"] == hp - context["_fall_hp_lost"]
    assert case.entities()["guard"]["last_reaction"] == "water_acrobatics"
    assert case.entities()["guard"]["reaction_available"] is False
    assert case.entities()["guard"]["opportunity_attacks_blocked"] is True
    _retain(case, history, receipt, command)
    return thaw_json_value(receipt.result["result_card"])


def _single_damage(case, receipt, hp, sides, *, saving_throw=False):
    context = receipt.result["context"]
    if saving_throw:
        assert context["_spell_save"]["results"]
        assert context["_spell_save_outcome"] == "failure"
        count = 1
    else:
        assert context["_spell_attack"]["results"]
        assert context["_spell_attack_outcome"] in ("success", "failure")
        if context["_spell_attack_outcome"] == "failure":
            assert case.entities()["guard"]["hp"] == hp
            return
        count = 2 if context["_spell_attack_kind"] == "critical_hit" else 1
    faces = context["_spell_damage"]["results"]
    assert len(faces) == count and all(1 <= face <= sides for face in faces)
    assert context["_spell_damage_amount"] == sum(faces)
    assert case.entities()["guard"]["hp"] == hp - sum(faces)


def test_complete_sorcerer_acolyte_and_imported_natural_sources_cast_seven_and_reopen(tmp_path):
    record, artifact = _artifact()
    participant, authored = _acolyte()
    original = deepcopy((participant, authored))
    completed = materialize_initial_character_state(participant, authored, **_contract(artifact))
    assert (participant, authored) == original
    assert completed["prepared_spells"] == [*SORCERER, *INITIATE]
    assert completed["hp"] == completed["max_hp"] == 9
    assert completed["armor_class"] == 11 and completed["speed"] == 30
    assert completed["initiative_modifier"] == 1 and completed["constitution_modifier"] == 2
    assert completed["saving_throw_modifiers"]["constitution"] == 4
    assert completed["saving_throw_modifiers"]["charisma"] == 5
    assert verify_initial_character_state(participant, completed, **_contract(artifact)) == completed
    assert (participant, authored) == original

    for missing_source in (0, 1):
        invalid, invalid_state = deepcopy(original)
        removed = invalid["spellcasting_grants"].pop(missing_source)
        invalid_state["prepared_spells"] = [spell for spell in invalid_state["prepared_spells"]
                                           if spell not in removed["spells"]]
        before = deepcopy((invalid, invalid_state))
        with pytest.raises(CommandRejected):
            materialize_initial_character_state(invalid, invalid_state, **_contract(artifact))
        assert (invalid, invalid_state) == before
    invalid, invalid_state = deepcopy(original)
    invalid["spellcasting_grants"][1]["spells"][1] = "eldritch_blast"
    invalid_state["prepared_spells"][7] = "eldritch_blast"
    before = deepcopy((invalid, invalid_state))
    with pytest.raises(CommandRejected, match="not granted by this source"):
        materialize_initial_character_state(invalid, invalid_state, **_contract(artifact))
    assert (invalid, invalid_state) == before

    case = AcquisitionCase.__new__(AcquisitionCase)
    history, observations = [], []
    try:
        case.__init__(tmp_path / "offensive-source-acquisition.db", record, artifact,
                      participant, completed)
        assert case.entities()["hero"] == completed
        participants = case.snapshot().participants
        resources = {identity: deepcopy(case.entities()[identity]["resources"])
                     for identity in ("hero", "bard", "warlock")}
        maxima = {identity: deepcopy(case.entities()[identity]["resource_maxima"])
                  for identity in resources}
        initial = case.snapshot()
        case.reject("cast_spell_outside_combat", case.parameters | {
            "spell": "sacred_flame", "somatic_component_available": False,
        })
        assert case.snapshot() == initial
        cases = (
            ("fire_bolt", "Fire Bolt", "hero", "charisma", 3, 10, "fire"),
            ("ray_of_frost", "Ray of Frost", "hero", "charisma", 3, 8, "cold"),
            ("shocking_grasp", "Shocking Grasp", "hero", "charisma", 3, 8, "lightning"),
            ("poison_spray", "Poison Spray", "hero", "charisma", 3, 12, "poison"),
            ("sacred_flame", "Sacred Flame", "hero", "wisdom", 1, 8, "radiant"),
            ("vicious_mockery", "Vicious Mockery", "bard", "charisma", 3, 6, "psychic"),
            ("eldritch_blast", "Eldritch Blast", "warlock", "charisma", 3, 10, "force"),
        )
        for spell, title, caster, ability, modifier, sides, damage_type in cases:
            choices = case.parameters | {"spell": spell, "caster": caster, "caster_state": caster}
            if spell == "eldritch_blast":
                choices = {key: value for key, value in choices.items()
                           if key not in ("target", "target_distance", "caster_can_see_target")}
                choices.update(attack_targets=["guard"], targets_max_distance=15,
                               all_attack_targets_have_clear_path=True)
            receipt, command, hp = _cast_with_actual_grasp_hit(case, choices, history)
            context = receipt.result["context"]
            profile = context["_casting_profile"]
            assert profile["ability"] == ability and profile["modifier"] == modifier
            assert profile["attack_bonus"] == modifier + 2
            assert profile["save_dc"] == 8 + modifier + 2 and profile["scaling_level"] == 1
            card = receipt.result["result_card"]
            assert card["title"] == title
            if spell == "eldritch_blast":
                assert context["_spell_definition"]["damage_type"] == damage_type
                rows = [row for row in card["details"]
                        if re.search(r"\. D20 \d+(?:, \d+)*; selected ", row["value"])]
                assert len(rows) == 1, card
                damage = re.search(r"Damage (\d+) \(HP (\d+); temporary HP (\d+)\)", rows[0]["value"])
                assert damage, card
                amount, hp_loss, temp_loss = map(int, damage.groups())
                assert amount == hp_loss and temp_loss == 0
                assert case.entities()["guard"]["hp"] == hp - amount
                dice = re.search(r"Damage dice (\d+(?:, \d+)*); rolled (\d+)", rows[0]["value"])
                if rows[0]["value"].startswith("Miss."):
                    assert amount == 0 and dice is None
                else:
                    assert dice, card
                    faces = [int(value.strip()) for value in dice[1].split(",")]
                    assert len(faces) == (2 if rows[0]["value"].startswith("Critical hit.") else 1)
                    assert all(1 <= face <= 10 for face in faces)
                    assert sum(faces) == int(dice[2]) == amount
            elif spell in ("sacred_flame", "vicious_mockery"):
                definition = context["_spell_definition"]
                assert definition["damage_type"] == damage_type
                assert definition["save_ability"] == ("dexterity" if spell == "sacred_flame" else "wisdom")
                _single_damage(case, receipt, hp, sides, saving_throw=True)
            else:
                definition = context["_spell_definition"]
                assert definition["damage_type_mode"] == "fixed"
                assert {key.removeprefix("allows_") for key, value in definition.items()
                        if key.startswith("allows_") and value is True} == {damage_type}
                assert context["_spell_effective_damage_type"] == damage_type
                _single_damage(case, receipt, hp, sides)
            for identity in resources:
                assert case.entities()[identity]["resources"] == resources[identity]
                assert case.entities()[identity]["resource_maxima"] == maxima[identity]
            assert case.snapshot().participants == participants
            _retain(case, history, receipt, command)
            observations.append({"spell": spell, "caster": caster,
                                 "creation": "exported materializer" if caster == "hero" else "imported",
                                 "actual_profile": thaw_json_value(profile),
                                 "actual_card": thaw_json_value(card)})
            if spell == "shocking_grasp":
                observations[-1]["actual_other_reaction_card"] = _grasp_other_reaction(case, history)
        final = case.snapshot()
        case.reopen()
        assert case.snapshot() == final
        for receipt, command in history:
            assert case.execute(command) == receipt and case.snapshot() == final
        assert verify_initial_character_state(participant, completed, **_contract(artifact)) == completed
        (tmp_path / "acquisition-observations.json").write_text(json.dumps({
            "qualification": "Complete Sorcerer/Acolyte materialization component; imported Bard/Warlock consumers; no Builder/UI or progression proof",
            "actual_participant": participant, "actual_materialized_state": thaw_json_value(completed),
            "casts": observations, "exact_retry_and_reopen": True,
        }, indent=2), encoding="utf-8")
    finally:
        if getattr(case, "manager", None):
            archive_closed_case(case)
