"""Final archive proof through ordinary admitted mechanics and SQLite campaign receipts."""

import io
import json
import os
import zipfile
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
from scripts.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_lite_catalogue import SELECTED, VARIANT_ID, build_catalogue

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import (
    ActionStatus,
    MechanicContext,
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
    owned_payload,
    thaw_json_value,
)
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime import _effect_executor
from tests.test_srd_action_budget import _runtime_definition
from tests.test_srd_attack_budget import _combat_case

FULL = "sylvia.srd-lite.bg3"
ARCHIVE = Path(os.environ["SRD_LITE_CANDIDATE"])


@lru_cache
def _event_consumer(name):
    """Compile the actual private event owner from the exact admitted candidate bytes."""
    with zipfile.ZipFile(ARCHIVE) as package:
        record = next(
            row
            for row in json.loads(package.read("manifest.json"))["packages"]
            if row["id"] == VARIANT_ID
        )
        runtime_bytes = package.read("packages/" + VARIANT_ID + "/runtime/ruleset.yaml")
    runtime = _runtime_definition(runtime_bytes)
    schema = _tool_schema(runtime["tools"][name], tool_name=name)
    return materialize_package_mechanic(
        package_id=VARIANT_ID,
        package_version=record["version"],
        package_sha256=record["content_sha256"],
        runtime_bytes=runtime_bytes,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(schema),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


def _replay_event_consumer(case, name, parameters, action_id, monkeypatch):
    """Replay a lower-level consumer with a fresh action identity, bypassing no-op command retry."""
    snapshot = case.snapshot()
    participants = {
        participant.participant_id: {
            "participant_id": participant.participant_id,
            "kind": participant.kind,
            "validation_status": participant.validation_status.value,
            "package_id": participant.package_id,
            "data": participant.data,
        }
        for participant in snapshot.participants
    }
    original_roll = _effect_executor.dice_roll
    rolls = []

    def recorded_roll(expression, *args, **kwargs):
        rolls.append(expression)
        return original_roll(expression, *args, **kwargs)

    with monkeypatch.context() as replay_patch:
        replay_patch.setattr(_effect_executor, "dice_roll", recorded_roll)
        outcome = _event_consumer(name)(
            MechanicContext(
                campaign_id="lite",
                turn_id="turn",
                action_id=action_id,
                state=owned_payload(snapshot.state),
                parameters=owned_payload(parameters),
                participants=owned_payload(participants),
            )
        )
    state = thaw_json_value(snapshot.state)
    assert isinstance(state, dict)
    for mutation in outcome.mutations:
        state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)
    assert outcome.result["status"] == "committed"
    assert state == snapshot.state
    assert not outcome.participant_mutations
    assert rolls == []
    assert "_lite_daggers_roll" not in outcome.result["context"]
    assert case.snapshot() == snapshot


class Case:
    def __init__(self, path, spell, *, guard_ac=1, positions=None, entity_changes=None, participant_changes=None, lighting="bright", casting_source=None, geometry=None, scene_override=None):
        self.path = path
        self.manager = None
        with zipfile.ZipFile(ARCHIVE) as package:
            self.record = next(
                row
                for row in json.loads(package.read("manifest.json"))["packages"]
                if row["id"] == VARIANT_ID
            )
            inventory = json.loads(
                package.read("packages/" + VARIANT_ID + "/content/spell-status-inventory.json")
            )
            header = next(
                row["casting_requirements"]
                for row in inventory["spells"]
                if row["identity"] == spell
            )
        source = casting_source or ("cleric" if spell in {"bless", "cure_wounds", "healing_word", "inflict_wounds", "guiding_bolt"} else "wizard")
        ability = "wisdom" if source in {"cleric", "druid", "ranger"} else ("charisma" if source in {"bard", "sorcerer", "warlock"} else "intelligence")
        state, data = _combat_case([{"id": source, "level": 17}])
        data.update(
            name="Lite caster",
            species="human",
            background="sage",
            size_rank=1,
            saving_throw_proficiencies=["intelligence", "wisdom"],
            origin_feats=["magic_initiate"],
        )
        data["abilities"][ability] = 18
        data["spellcasting_grants"] = [{"source": source, "ability": ability, "spells": [spell]}]
        if source == "wizard":
            data["wizard_spellbook"] = [spell] if header["level"] else []
        data.update(deepcopy(participant_changes or {}))
        entities = state[FULL + ".entities"]
        entities["hero"].update(
            name="Lite caster",
            creature_type="humanoid",
            prepared_spells=[spell],
            spell_slot_cast_this_turn=False,
            resources={f"spell_slot_{level}": 3 for level in range(1, 10)},
            initiative_modifier=20,
            melee_reach=5,
            ammunition={},
            spell_materials=[],
            constitution_modifier=2, dodging=False, stable=False, death_successes=0,
            death_failures=0, stable_recovery_hours_remaining=0,
        )
        for identity in ("guard", "other", "extra"):
            entities[identity] = deepcopy(entities["guard"])
            entities[identity].update(
                name=identity,
                hp=1000,
                max_hp=1000,
                temp_hp=0,
                speed=30,
                dodging=False,
                armor_class=guard_ac,
                creature_type="humanoid",
                melee_reach=5,
                initiative_modifier=0,
                available_reactions=["opportunity_attack"],
                reaction_available=True,
                saving_throw_modifiers=dict.fromkeys(data["abilities"], 0),
                attitudes={"hero": "friendly"},
                stable=False,
                death_successes=0,
                death_failures=0,
                stable_recovery_hours_remaining=0,
            )
        materials = [
            {
                "id": f"material_{index}",
                "inventory_item": f"{spell}_material_{index}",
                "kind": "specific",
                "components": [part["id"]],
                "quantity": part["quantity"] * 3,
                "value_cp": part["minimum_value_cp"],
            }
            for index, part in enumerate(header["material_parts"], 1)
        ]
        if spell == "true_strike":
            for material in materials:
                material.update(inventory_item="dagger", quantity=1, value_cp=200)
        entities["hero"]["spell_materials"] = materials
        entities["hero"]["inventory"] += [item["inventory_item"] for item in materials
                                        if item["inventory_item"] not in entities["hero"]["inventory"]]
        for identity, changes in (entity_changes or {}).items():
            entities[identity].update(changes)
        state[FULL + ".combat"].update(
            encounter_active=False,
            turn_active=False,
            active_actor=None,
            phase="exploration",
            action_available=False,
        )
        positions = positions or {
            "hero": [0, 0, 0],
            "guard": [5, 0, 0],
            "other": [-5, 0, 0],
            "extra": [20, 10, 0],
        }
        scene = {
            "format": 1,
            "map": {
                "id": "lite",
                "revision": 0,
                "bounds": {"min": [-1000, -1000, -100], "max": [1000, 1000, 100]},
                "grid": {"cell_size": 5, "unit": "ft", "origin": [-1000, -1000]},
                "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}],
            },
            "entities": [
                {
                    "id": identity,
                    "map_id": "lite",
                    "position": point,
                    "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
                    "height": 6,
                    "facing": [1, 0],
                    "active": True,
                    "movement": "blocking",
                    "visibility": "opaque",
                    "metadata": {
                        "participant_ref": identity,
                        "plane": "material",
                        "lighting": lighting,
                        "creature_type": entities.get(identity, {}).get("creature_type", "humanoid"),
                    },
                }
                for identity, point in positions.items()
            ],
            "geometry": deepcopy(geometry or []),
            "zones": ([{"id": "ambient", "map_id": "lite",
                "shape": {"kind": "box", "min": [-1000, -1000, -100], "max": [1000, 1000, 100]},
                "metadata": {"active": True, "plane": "material", "lighting": lighting}}]
                if geometry is not None else []),
            "terrain": [],
        }
        if geometry is not None:
            from sylvia_gm.spatial import admit_spatial_scene
            admit_spatial_scene(scene)
        if scene_override is not None:
            from sylvia_gm.spatial import admit_spatial_scene
            scene = deepcopy(scene_override)
            admit_spatial_scene(scene)
        state[FULL + ".state"] = {"lasting_tick": 0, "turn_effects": [], "spatial_scene": scene}
        self.parameters = {
            "spell": spell,
            "caster": "hero",
            "caster_state": "hero",
            "target": "guard",
            "slot_resource": f"spell_slot_{header['level']}" if header["level"] else "cantrip",
            "target_distance": 5,
            "caster_can_see_target": True,
            "caster_can_hear_target": True,
            "caster_can_touch_target": True,
            "target_willing": True,
            "cover": "none",
            "targets_max_distance": 25,
            "caster_can_see_all_targets": True,
            "all_targets_have_clear_path": True,
            "all_targets_willing": True,
            "caster_can_touch_all_targets": True,
            "origin_distance": 0,
            "caster_can_see_origin": True,
            "origin_has_clear_path": True,
            "all_attack_targets_have_clear_path": True,
            "verbal_component_available": True,
            "somatic_component_available": True,
            "material_component_available": True,
            "material_item_ids": [item["id"] for item in materials],
        }
        if spell == "true_strike":
            self.parameters.update(true_strike_weapon="dagger", true_strike_attack_mode="melee",
                                   true_strike_two_handed=False, true_strike_damage_choice="radiant")
        from tests.admitted_library_fixture import prepare_library_database, CANDIDATE_ID
        prepare_library_database(self.path, ARCHIVE.read_bytes())
        self.open()
        installed = self.owner.package_candidate(CANDIDATE_ID)
        exact = next(row for row in installed.package_lock.entries if row.package_id == VARIANT_ID)
        artifact = self.record["participant_artifacts"][0]
        self.owner.create_campaign(
            CampaignCreate(
                campaign_id="lite",
                display_name="SRD Lite foundation proof",
                writer=WriterRole.DM,
                package_lock=installed.package_lock,
                turn_id="turn",
                participants=(
                    Participant(
                        participant_id="hero",
                        kind="player_character",
                        package_id=VARIANT_ID,
                        package_version=exact.version,
                        package_sha256=exact.sha256,
                        schema_id=artifact["schema_id"],
                        schema_sha256=artifact["schema_sha256"],
                        validation_status=ParticipantValidationStatus.VALIDATED,
                        data=data,
                    ),
                ),
                initial_state=tuple(
                    StateValue(VARIANT_ID, key.removeprefix(FULL + "."), value)
                    for key, value in state.items()
                ),
            )
        )

        if spell == "true_strike":
            self.act("record_srd_control_held_items", target_state="hero", held_items=["dagger"], held_attack_names=[])
            self.act("record_srd_scene_hand_occupancy", actor_state="hero", hand_capacity=2, holdings=[{"inventory_item":"dagger", "hand_indices":[0]}])

    def open(self):
        self.manager = AuthorityOwner.open(self.path, "lite-owner")
        self.owner = self.manager.__enter__()

    def close(self):
        if self.manager:
            self.manager.__exit__(None, None, None)
            self.manager = None

    def reopen(self):
        before = self.snapshot()
        self.close()
        self.open()
        assert self.snapshot().state == before.state

    def snapshot(self):
        return self.owner.snapshot("lite")

    def entities(self):
        return self.snapshot().state[VARIANT_ID + ".entities"]

    def lasting(self):
        return self.snapshot().state[VARIANT_ID + ".state"]

    def command(self, tool, parameters, identity=None):
        revision = self.snapshot().revision
        identity = identity or f"{tool}-{revision}"
        return ActionCommand(
            campaign_id="lite",
            action_id=identity,
            idempotency_key=identity,
            owner_id="lite-owner",
            writer=WriterRole.DM,
            expected_revision=revision,
            turn_id="turn",
            package_id=VARIANT_ID,
            tool_name=tool,
            parameters=parameters,
        )

    def execute(self, command):
        if command.tool_name in {
            "cast_spell",
            "cast_spell_outside_combat",
            "begin_srd_spell_action",
            "prepare_srd_spells",
            "prepare_srd_creature",
            "use_srd_sunbeam",
        }:
            return StatelessMcpMechanics(self.owner).execute_action(command)
        return self.owner.execute_action(command)

    def act(self, tool, **parameters):
        command = self.command(tool, parameters)
        receipt = self.execute(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        return receipt, command

    def cast(self, **choices):
        if "origin" in choices:
            choices["environment_origin"] = choices.pop("origin")
        return self.act("cast_spell_outside_combat", **(self.parameters | choices))

    def reject(self, tool, parameters, match=None):
        before = self.snapshot()
        with pytest.raises((MechanicsRejected, CommandRejected), match=match):
            self.execute(self.command(tool, parameters))
        after = self.snapshot()
        assert after.revision == before.revision and after.state == before.state
        assert after.participants == before.participants

    def start(self, second_actor="guard"):
        self.act(
            "start_combat_encounter_entities",
            first_actor="hero",
            second_actor=second_actor,
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        self.act("begin_combat_turn")
        assert self.snapshot().state[VARIANT_ID + ".combat"]["active_actor"] == "hero"

    def admit_scene_encounter(self):
        self.act("start_combat_encounter_entities", first_actor="hero", second_actor="guard",
                 first_surprised=False, second_surprised=False, tie_winner="first")

    def activate_guard_turn_for_movement(self):
        self.act("begin_combat_turn")
        self.act("end_combat_turn")
        self.act("advance_combat_encounter_turn")
        receipt, command = self.act("begin_combat_turn")
        assert self.snapshot().state[VARIANT_ID + ".combat"]["active_actor"] == "guard"
        return receipt, command

    def next_turn(self):
        self.act("end_combat_turn")
        self.act("advance_combat_encounter_turn")
        self.act("begin_combat_turn")

    def attack(self, target="guard"):
        return self.act(
            "resolve_combat_attack",
            actor="hero",
            target=target,
            attack_sequence="primary",
            use_graze_on_miss=False,
            weapon="spear",
            attack_mode="melee",
            attack_ability="strength",
            wielding_two_handed=False,
            mounted=False,
            circumstantial_modifier=0,
            advantage=False,
            disadvantage=False,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
            attack_distance=5,
        )


@pytest.fixture
def cases(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    opened = []

    def make(spell, **changes):
        case = Case(tmp_path / (str(len(opened)) + ".db"), spell, **changes)
        opened.append(case)
        return case

    yield make
    for case in opened:
        from tests.lite_fixture_archive import archive_closed_case
        archive_closed_case(case)


def test_exact_roster_identity_and_pending_states(cases):
    from io import BytesIO
    import hashlib
    from sylvia_gm.application.bundled_packages import bundled_srd_archive

    catalogue = build_catalogue()
    archive_path = Path(__file__).parents[1] / "historical/spell-research/spell-catalogue.json"
    archive_bytes = archive_path.read_bytes()
    historical = json.loads(archive_bytes)
    assert catalogue["count"] == 117
    assert {row["identity"] for row in catalogue["profiles"]} == SELECTED
    assert catalogue["profiles"] == historical["profiles"]
    assert historical["historical_catalogue"]["count"] == 210
    assert sum(row["srd_identity_present"] for row in historical["historical_catalogue"]["profiles"]) == 181
    assert catalogue["historical_research"]["sha256"] == hashlib.sha256(archive_bytes).hexdigest()
    assert all(not row["behavior_accepted"] for row in catalogue["profiles"])
    assert sum(row["profile_state"] == "selected_unverified" for row in catalogue["profiles"]) == 117
    assert len(SELECTED) == 117
    prefix = "packages/" + VARIANT_ID + "/content/"
    with zipfile.ZipFile(BytesIO(bundled_srd_archive())) as package:
        assert json.loads(package.read(prefix + "spell-catalogue.json")) == json.loads(json.dumps(catalogue))
        assert prefix + "normal-roster.txt" not in package.namelist()
        assert prefix + "roster-crosswalk.csv" not in package.namelist()
        assert not any(name.startswith("historical/") for name in package.namelist())
    case = cases("bless")
    legacy_archive = Path(__file__).parents[1] / "tests/fixtures/SRD-Lite-117-QA-Legacy-Contract-Clean.sylvia-gm-package"
    with zipfile.ZipFile(legacy_archive) as package:
        expected_legacy_catalogue = {**historical, "launch_roster_sha256": "960ce421c46a4ba6c31e04a77081e786cf9ca05e9587a4efa8234f44a3b1d224"}
        assert json.loads(package.read(prefix + "spell-catalogue.json")) == expected_legacy_catalogue
        assert package.read(prefix + "normal-roster.txt") == (
            Path(__file__).parents[1] / "historical/spell-research/normal-roster.txt"
        ).read_bytes()
    with zipfile.ZipFile(ARCHIVE) as package:
        exact_input = next(row for row in json.loads(package.read("manifest.json"))["packages"] if row["id"] == VARIANT_ID)
    assert case.record["id"] == VARIANT_ID and case.record["version"] == exact_input["version"]
    exact_lock = next(row for row in case.snapshot().package_lock.entries if row.package_id == VARIANT_ID)
    assert exact_lock.version == exact_input["version"]
    assert exact_lock.sha256 == exact_input["content_sha256"]
    assert "wotc.srd.5.2.1" not in {row.package_id for row in case.snapshot().package_lock.entries}
    assert case.record["participant_artifacts"][0]["schema_id"].startswith(VARIANT_ID + ".")


@pytest.mark.parametrize("spell,slot", [("bless", 1), ("cloud_of_daggers", 2)])
def test_levelled_spells_spend_one_actual_combat_action_and_slot(cases, spell, slot):
    case = cases(spell, positions=CLOUD_POSITIONS if spell == "cloud_of_daggers" else None)
    case.start()
    resources = dict(case.entities()["hero"]["resources"])
    choices = {"targets": ["guard"]} if spell == "bless" else {"environment_origin": [0, 0, 3]}
    case.act("cast_spell", **(case.parameters | choices))
    resources[f"spell_slot_{slot}"] -= 1
    assert case.entities()["hero"]["resources"] == resources
    assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is False
    assert case.entities()["hero"]["concentration_spell_id"] == spell


@pytest.mark.parametrize(
    "tool", ["cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"]
)
@pytest.mark.parametrize("spell,message", [(name, "excluded") for name in
    ("wish", "knock", "barkskin", "resistance", "mass_heal", "protection_from_poison")])
def test_ordinary_out_of_roster_requests_do_not_spend(cases, tool, spell, message):
    case = cases("bless")
    if tool == "cast_spell":
        case.start()
    case.reject(tool, case.parameters | {"spell": spell}, match=message)


def test_excluded_preparation_npc_and_imported_recast_do_not_mutate(cases):
    case = cases("true_strike", entity_changes={"hero": {"concentration_spell_id": "sunbeam"}})
    case.reject(
        "prepare_srd_spells",
        {
            "caster": "hero",
            "caster_state": "hero",
            "source": "wizard",
            "spells": ["wish"],
            "spellbook_available": True,
        },
    )
    case.reject(
        "prepare_srd_creature",
        {
            "creature_id": "mage",
            "name": "NPC mage",
            "source": "Synthetic NPC",
            "armor_class": 12,
            "hit_points": 25,
            "speed": 30,
            "initiative_modifier": 0,
            "size_rank": 2,
            "constitution_save": 0,
            "spellcasting_ability": "wisdom",
            "spellcasting_modifier": 3,
            "spell_attack_bonus": 7,
            "spell_save_dc": 15,
            "spells": [{"spell": "wish", "uses_per_day": 1}],
        },
    )
    case.reject("use_srd_sunbeam", {"caster_state": "hero"}, match="pending or excluded")


def test_bless_upcast_real_attack_and_save_retry_reopen_removal(cases):
    case = cases("bless")
    cast, command = case.cast(
        slot_resource="spell_slot_2", targets=["hero", "guard", "other", "extra"]
    )
    saved = case.snapshot()
    assert case.entities()["hero"]["resources"]["spell_slot_2"] == 2
    assert case.execute(command) == cast and case.snapshot().state == saved.state
    case.reopen()
    assert case.execute(command) == cast
    for kind in ("attack_roll", "saving_throw"):
        receipt, _ = case.act(
            "d20_test",
            actor_state="guard",
            kind=kind,
            ability="dexterity",
            ability_modifier=0,
            proficiency_bonus=0,
            proficiency_factor="none",
            circumstantial_modifier=0,
            target_number=10,
            advantage=False,
            disadvantage=False,
        )
        context = receipt.result["context"]
        assert context["_bless_roll"]["sides"] == 4 and context["_bless_roll"]["count"] == 1
        assert context["_lasting_d20_modifier"] == context["_bless_roll"]["total"]
    case.start()
    attack, _ = case.attack()
    context = attack.result["context"]
    assert context["_weapon_attack_bless_roll"]["sides"] == 4
    assert (
        context["_weapon_attack_lasting_modifier"] == context["_weapon_attack_bless_roll"]["total"]
    )
    assert (
        context["_attack_adjusted_total"]
        == context["_attack_roll"]["total"]
        + context["_attack_roll"]["modifier"]
        + context["_weapon_attack_lasting_modifier"]
    )
    case.act("end_concentration", caster_state="hero")
    assert case.entities()["hero"].get("concentration_spell_id") is None
    assert not case.entities()["guard"].get("concentration_d20_modifier_source_ids")


@pytest.mark.parametrize(
    "changes",
    [
        {"guard": {"attitudes": {"hero": "hostile"}}},
        {"guard": {"is_alive": False}},
        {"guard": {"attitudes": {}}},
    ],
)
def test_bless_invalid_targets_do_not_spend(cases, changes):
    case = cases("bless", entity_changes=changes)
    if changes["guard"].get("is_alive") is False:
        case.reject("cast_spell_outside_combat", case.parameters | {"targets": ["guard"]})
    else:
        # Original SRD Bless does not require a friendly attitude.
        case.cast(targets=["guard"])
        assert case.entities()["hero"]["concentration_spell_id"] == "bless"
        assert case.entities()["hero"]["resources"]["spell_slot_1"] == 2


def test_bless_source_expires_at_twenty_ticks(cases):
    case = cases("bless")
    case.cast(targets=["guard"])
    case.act("advance_srd_lasting_time", elapsed_rounds=9)
    assert case.entities()["hero"]["concentration_spell_id"] == "bless"
    case.act("advance_srd_lasting_time", elapsed_rounds=1)
    assert case.entities()["hero"].get("concentration_spell_id") is None
    assert not case.entities()["guard"].get("concentration_d20_modifier_source_ids")


def test_bless_actual_range_and_base_target_limit_do_not_spend(cases):
    distant = cases(
        "bless",
        positions={
            "hero": [0, 0, 0],
            "guard": [31, 0, 0],
            "other": [-5, 0, 0],
            "extra": [20, 10, 0],
        },
    )
    distant.start()
    distant.reject("cast_spell", distant.parameters | {"targets": ["guard"]})
    overfull = cases("bless")
    overfull.reject(
        "cast_spell_outside_combat",
        overfull.parameters | {"targets": ["hero", "guard", "other", "extra"]},
    )


@pytest.mark.parametrize("armor_class", [1, 1000])
def test_true_strike_native_immediate_attack_hit_or_miss_retry_and_no_future_buff(cases, armor_class):
    case = cases("true_strike", guard_ac=armor_class)
    case.start()
    before = case.entities()["guard"]["hp"]
    resources = dict(case.entities()["hero"]["resources"])
    cast, command = case.act("cast_spell", **case.parameters)
    assert (case.entities()["guard"]["hp"] < before) is (armor_class == 1)
    assert case.entities()["hero"].get("lite_true_strike") is None
    assert case.entities()["hero"].get("concentration_spell_id") is None
    assert case.entities()["hero"]["resources"] == resources
    assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is False
    saved = case.snapshot(); assert case.execute(command) == cast and case.snapshot() == saved
    case.reopen(); assert case.execute(command) == cast and case.snapshot() == saved
    case.next_turn(); case.next_turn()
    ordinary, _ = case.attack()
    assert len(ordinary.result["context"]["_attack_roll"]["results"]) == 1
    assert case.entities()["hero"].get("concentration_spell_id") is None


def test_true_strike_native_target_specific_and_component_rejections(cases):
    case = cases("true_strike")
    guard_hp = case.entities()["guard"]["hp"]
    case.cast(target="other")
    assert case.entities()["guard"]["hp"] == guard_hp
    assert case.entities()["other"]["hp"] < 1000
    invalid = cases("true_strike")
    invalid.reject("cast_spell_outside_combat", invalid.parameters | {"somatic_component_available": False})
    distant = cases("true_strike", positions={"hero":[0,0,0],"guard":[6,0,0],"other":[-5,0,0],"extra":[20,10,0]})
    distant.start()
    distant.reject("cast_spell", distant.parameters)


@pytest.mark.parametrize("changes", [{"is_alive": False}, {"hp": 0}])
def test_true_strike_rejects_actual_dead_targets_without_spend(cases, changes):
    case = cases("true_strike", entity_changes={"guard": changes})
    case.reject("cast_spell_outside_combat", case.parameters)


def test_true_strike_can_damage_a_dying_character_and_replay_exactly(cases):
    case = cases("true_strike", entity_changes={"guard": {
        "creature_kind": "character", "hp": 0, "is_alive": True,
        "conditions": ["unconscious"], "death_failures": 0,
    }})
    receipt, command = case.cast()
    assert receipt.status is ActionStatus.COMMITTED
    after = case.snapshot()
    assert after.state[VARIANT_ID + ".entities"]["guard"]["hp"] == 0
    assert 1 <= after.state[VARIANT_ID + ".entities"]["guard"]["death_failures"] <= 2
    assert case.execute(command) == receipt
    assert case.snapshot() == after
    case.reopen()
    assert case.execute(command) == receipt
    assert case.snapshot().state == after.state


def test_true_strike_native_does_not_grant_ally_attack_advantage(cases):
    case = cases("true_strike")
    case.start(second_actor="other")
    case.act("cast_spell", **case.parameters)
    case.next_turn()
    attack, _ = case.act("resolve_adjudicated_creature_attack", target="guard", attack_name="Claw",
        attack_mode="melee", attack_distance=10, normal_range=10, long_range=10,
        attack_modifier=3, damage_expression="d4", damage_modifier=0, damage_type="slashing",
        advantage=False, disadvantage=False, actor_can_see_target=True, target_can_see_actor=True,
        target_is_charmer=False, fear_source_in_sight=False, attack_uses_weapon=False, use_multiattack=False)
    assert len(attack.result["context"]["_attack_roll"]["results"]) == 1
    assert case.entities()["hero"].get("concentration_spell_id") is None


CLOUD_POSITIONS = {
    "hero": [-30, 0, 0],
    "guard": [0, 0, 0],
    "other": [30, 0, 0],
    "extra": [40, 20, 0],
}


def _assert_daggers_card(receipt, target, before, after, roll, *, kind, expression="4d4"):
    """The ordinary committed receipt must expose the actual roll and resulting vitals."""
    card = receipt.result["result_card"]
    rows = [row for row in card["details"] if row["label"] == target and "equation" not in row]
    equations = [row["equation"] for row in card["details"] if row["label"] == target and "equation" in row]
    assert len(equations) == 1 and equations[0]["rolls"] == list(roll.results)
    assert equations[0]["total"] == roll.total and equations[0]["die_sides"] == roll.sides
    assert len(rows) == 1, card
    detail = rows[0]["value"]
    faces = ", ".join(str(face) for face in roll.results)
    assert f"{expression}: {faces} = {roll.total}" in detail
    assert f"no save; {kind} event" in detail
    assert f"HP {before['hp']} to {after['hp']}" in detail
    assert f"temporary HP {before['temp_hp']} to {after['temp_hp']}" in detail
    applied = before["hp"] + before["temp_hp"] - after["hp"] - after["temp_hp"]
    assert f"{applied} slashing applied" in detail
    return card


def _assert_no_daggers_card(receipt):
    card = receipt.result["result_card"]
    assert all("Cloud of Daggers" not in str(row) for row in card["summary"])
    assert all("slashing applied" not in str(row) for row in card["details"])


@pytest.mark.parametrize("slot,dice_count", [(2, 4), (3, 6)])
def test_daggers_creation_genuine_movement_reentry_and_retry(cases, monkeypatch, slot, dice_count):
    case = cases(
        "cloud_of_daggers", positions=CLOUD_POSITIONS, entity_changes={"guard": {"speed": 100}}
    )
    before = case.entities()["guard"]["hp"]
    original_roll = _effect_executor.dice_roll
    expressions = []
    rolls = []

    def recorded_roll(sides, count=1, modifier=0):
        expressions.append(f"{count}d{sides}")
        result = original_roll(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, "dice_roll", recorded_roll)
    cast, command = case.cast(slot_resource=f"spell_slot_{slot}", origin=[0, 0, 3])
    damage = before - case.entities()["guard"]["hp"]
    assert expressions == [f"{dice_count}d4"]
    assert dice_count <= damage <= dice_count * 4
    card = _assert_daggers_card(
        cast,
        "guard",
        {"hp": before, "temp_hp": 0},
        case.entities()["guard"],
        rolls[-1],
        kind="creation",
        expression=f"{dice_count}d4",
    )
    assert {row["label"]: row["value"] for row in card["summary"]}["guard"] == (
        f"Cloud of Daggers: {damage} slashing"
    )
    assert case.entities()["hero"]["resources"][f"spell_slot_{slot}"] == 2
    assert case.entities()["other"]["hp"] == 1000
    area = case.entities()["hero"]["environment_area_id"]
    source = case.lasting()["persistent_spell_areas"][area]
    assert source["radius"] == 7 and source["origin"] == [0, 0, 3]
    assert len(source["lite_event_receipts"]) == 1
    saved = case.snapshot()
    assert case.execute(command) == cast and case.snapshot().state == saved.state
    case.reopen()
    with monkeypatch.context() as setup:
        setup.setattr(_effect_executor, "dice_roll", original_roll)
        case.admit_scene_encounter()
    start_turn, start_command = case.activate_guard_turn_for_movement()
    movement_before = case.entities()["guard"]["hp"]
    assert movement_before == before - damage - rolls[-1].total
    _assert_daggers_card(start_turn, "guard", {"hp":before-damage, "temp_hp":0},
                         case.entities()["guard"], rolls[-1], kind="start", expression=f"{dice_count}d4")
    assert case.execute(start_command) == start_turn
    quiet, _ = case.act(
        "move_srd_scene_creature", actor_state="guard", destination=[1, 0, 0], mode="walk"
    )
    _assert_no_daggers_card(quiet)
    assert case.entities()["guard"]["hp"] == movement_before
    quiet, _ = case.act(
        "move_srd_scene_creature", actor_state="guard", destination=[20, 0, 0], mode="walk"
    )
    _assert_no_daggers_card(quiet)
    assert case.entities()["guard"]["hp"] == movement_before
    entry, entry_command = case.act(
        "move_srd_scene_creature", actor_state="guard", destination=[0, 0, 0], mode="walk"
    )
    first = case.entities()["guard"]["hp"]
    assert first < movement_before
    card = _assert_daggers_card(
        entry,
        "guard",
        {"hp": movement_before, "temp_hp": 0},
        case.entities()["guard"],
        rolls[-1],
        kind="movement",
        expression=f"{dice_count}d4",
    )
    summary = {row["label"]: row["value"] for row in card["summary"]}
    assert summary["Cloud of Daggers events"] == 1
    assert summary["Cloud of Daggers slashing damage"] == movement_before - first
    saved = case.snapshot()
    assert case.execute(entry_command) == entry and case.snapshot().state == saved.state
    case.reopen()
    assert case.execute(entry_command) == entry and case.snapshot().state == saved.state
    case.act("move_srd_scene_creature", actor_state="guard", destination=[20, 0, 0], mode="walk")
    case.act("move_srd_scene_creature", actor_state="guard", destination=[0, 0, 0], mode="walk")
    assert case.entities()["guard"]["hp"] < first
    assert len(case.lasting()["persistent_spell_areas"][area]["lite_event_receipts"]) == 4
    assert expressions == [f"{dice_count}d4"] * 4
    case.act("end_concentration", caster_state="hero")
    assert case.lasting()["persistent_spell_areas"][area]["active"] is False
    final = case.entities()["guard"]["hp"]
    case.next_turn(); case.next_turn()
    case.act("move_srd_scene_creature", actor_state="guard", destination=[20, 0, 0], mode="walk")
    case.act("move_srd_scene_creature", actor_state="guard", destination=[0, 0, 0], mode="walk")
    assert case.entities()["guard"]["hp"] == final


def test_daggers_start_turn_crossing_no_save_and_expiry(cases, monkeypatch):
    case = cases("cloud_of_daggers", positions=CLOUD_POSITIONS)
    original = _effect_executor.dice_roll
    expressions = []
    rolls = []

    def recorded(sides, count=1, modifier=0):
        expressions.append(f"{count}d{sides}")
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, "dice_roll", recorded)
    case.cast(origin=[0, 0, 3])
    assert expressions and all("d20" not in expression for expression in expressions)
    case.start()
    expressions.clear()
    before = case.entities()["guard"]["hp"]
    case.act("end_combat_turn")
    case.act("advance_combat_encounter_turn")
    turn, command = case.act("begin_combat_turn")
    assert case.entities()["guard"]["hp"] < before
    assert expressions == ["4d4"]
    card = _assert_daggers_card(
        turn,
        "guard",
        {"hp": before, "temp_hp": 0},
        case.entities()["guard"],
        rolls[-1],
        kind="start",
    )
    assert {row["label"]: row["value"] for row in card["summary"]}["Cloud of Daggers events"] == 1
    saved = case.snapshot()
    case.reopen()
    assert case.execute(command) == turn and case.snapshot().state == saved.state
    assert expressions == ["4d4"]
    # A full transit with both endpoints outside still crosses the real Sphere.
    crossing = cases(
        "cloud_of_daggers",
        positions=CLOUD_POSITIONS | {"guard": [-20, 0, 0]},
        entity_changes={"guard": {"speed": 100}},
    )
    crossing.cast(origin=[0, 0, 3])
    assert crossing.entities()["guard"]["hp"] == 1000
    with monkeypatch.context() as setup:
        setup.setattr(_effect_executor, "dice_roll", original)
        crossing.admit_scene_encounter()
    crossing.activate_guard_turn_for_movement()
    crossing.act(
        "move_srd_scene_creature", actor_state="guard", destination=[20, 0, 0], mode="walk"
    )
    assert crossing.entities()["guard"]["hp"] < 1000
    area = crossing.entities()["hero"]["environment_area_id"]
    crossing.act("end_combat_encounter", end_reason="fighting_stopped")
    assert crossing.lasting()["persistent_spell_areas"][area]["active"] is True
    crossing.act("advance_srd_lasting_time", elapsed_rounds=10)
    assert crossing.lasting()["persistent_spell_areas"][area]["active"] is False


def test_daggers_creation_card_keeps_each_target_roll_mitigation_and_temporary_hp(
    cases, monkeypatch
):
    case = cases(
        "cloud_of_daggers",
        positions=CLOUD_POSITIONS | {"other": [3, 0, 0], "extra": [5, 0, 0]},
        entity_changes={
            "guard": {"temp_hp": 5},
            "other": {"damage_resistances": ["slashing"]},
            "extra": {"damage_immunities": ["slashing"]},
        },
    )
    before = deepcopy(case.entities())
    original = _effect_executor.dice_roll
    rolls = []

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, "dice_roll", recorded)
    receipt, command = case.cast(origin=[0, 0, 3])
    assert len(rolls) == 3 and all(roll.sides == 4 and len(roll.results) == 4 for roll in rolls)
    after = case.entities()
    target_rows = [
        row
        for row in receipt.result["result_card"]["details"]
        if row["label"] in {"guard", "other", "extra"} and "equation" not in row
    ]
    assert len(target_rows) == 3 and {row["label"] for row in target_rows} == {
        "guard",
        "other",
        "extra",
    }
    by_target = dict(zip((row["label"] for row in target_rows), rolls, strict=True))
    for target, roll in by_target.items():
        _assert_daggers_card(receipt, target, before[target], after[target], roll, kind="creation")
    assert after["guard"]["hp"] + after["guard"]["temp_hp"] == 1005 - by_target["guard"].total
    assert after["other"]["hp"] == 1000 - by_target["other"].total // 2
    assert after["extra"]["hp"] == 1000
    assert after["hero"]["resources"]["spell_slot_2"] == 2
    saved = case.snapshot()
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert len(rolls) == 3


def test_daggers_duplicate_event_consumers_do_not_roll_or_damage_again(cases, monkeypatch):
    case = cases(
        "cloud_of_daggers", positions=CLOUD_POSITIONS, entity_changes={"guard": {"speed": 100}}
    )
    case.cast(origin=[0, 0, 3])
    case.admit_scene_encounter()
    area = case.entities()["hero"]["environment_area_id"]
    source = case.lasting()["persistent_spell_areas"][area]
    assert len(source["lite_event_receipts"]) == 1
    _replay_event_consumer(
        case,
        "resolve_srd_lite_daggers_event",
        {
            "target_state": "guard",
            "area_id": area,
            "event_kind": "creation",
            "event_sequence": source["sequence"],
        },
        "distinct-creation-consumer-replay",
        monkeypatch,
    )
    case.activate_guard_turn_for_movement()
    case.act("move_srd_scene_creature", actor_state="guard", destination=[20, 0, 0], mode="walk")
    case.act("move_srd_scene_creature", actor_state="guard", destination=[0, 0, 0], mode="walk")
    receipts = case.lasting()["persistent_spell_areas"][area]["lite_event_receipts"]
    movement_receipts = [value for value in receipts if value.startswith("movement:")]
    assert len(movement_receipts) == 1
    event_kind, serial_text, target = movement_receipts[0].split(":")
    serial = int(serial_text)
    assert event_kind == "movement" and target == "guard"
    assert 0 < serial <= case.lasting()["lite_movement_serial"]
    case.reopen()
    _replay_event_consumer(
        case,
        "resolve_srd_environment_hazard_event",
        {
            "target_state": "guard",
            "area_id": area,
            "lite_event_sequence": serial,
        },
        "distinct-movement-consumer-replay-after-reopen",
        monkeypatch,
    )
    case.next_turn(); case.next_turn()
    turn = case.snapshot().state[VARIANT_ID + ".combat"]["turn_sequence"]
    assert (
        f"start:{turn}:guard"
        in case.lasting()["persistent_spell_areas"][area]["lite_event_receipts"]
    )
    case.reopen()
    _replay_event_consumer(
        case,
        "resolve_srd_environment_hazard_start_turn",
        {
            "target_state": "guard",
        },
        "distinct-start-consumer-replay-after-reopen",
        monkeypatch,
    )


def test_daggers_uses_a_three_dimensional_sphere(cases):
    case = cases(
        "cloud_of_daggers", positions=CLOUD_POSITIONS | {"other": [0, 0, 20], "extra": [0, 0, -12]}
    )
    case.cast(origin=[0, 0, 3])
    assert case.entities()["guard"]["hp"] < 1000
    assert case.entities()["other"]["hp"] == 1000 and case.entities()["extra"]["hp"] == 1000


def test_daggers_real_creation_damage_breaks_concentration_and_removes_source(cases, monkeypatch):
    # Individual valid die faces force the genuine concentration save to fail.
    monkeypatch.setattr(dice._rng, "randint", lambda low, high: low)
    case = cases("cloud_of_daggers", positions=CLOUD_POSITIONS | {"hero": [0, 0, 0]})
    hero_before = case.entities()["hero"]["hp"]
    case.cast(origin=[0, 0, 3])
    assert case.entities()["hero"]["hp"] == hero_before - 4
    assert case.entities()["hero"].get("concentration_spell_id") is None
    assert case.entities()["guard"]["hp"] == 996
    areas = case.lasting()["persistent_spell_areas"]
    source = next(value for value in areas.values() if value["spell"] == "cloud_of_daggers")
    assert source["active"] is False
    case.reopen()
    before = case.entities()["guard"]["hp"]
    case.start()
    case.next_turn()
    assert case.entities()["guard"]["hp"] == before


@pytest.mark.parametrize("origin", [[1001, 0, 3], [70, 0, 3], [0, 0]])
def test_daggers_invalid_geometry_does_not_spend(cases, origin):
    case = cases("cloud_of_daggers", positions=CLOUD_POSITIONS)
    case.reject("cast_spell_outside_combat", case.parameters | {"environment_origin": origin})


@pytest.mark.parametrize("field", ["wizard_spellbook", "spellcasting_grants"])
@pytest.mark.parametrize("spell", ["wish", "hellish_rebuke"])
def test_builder_character_import_rejects_excluded_and_pending_spells(cases, field, spell):
    case = cases("true_strike")
    snapshot = case.snapshot()
    participant = snapshot.participants[0]
    data = thaw_json_value(participant.data)
    if field == "wizard_spellbook":
        data[field] = [spell]
    else:
        data[field][0]["spells"] = [spell]
    invalid = replace(participant, data=owned_payload(data))
    with pytest.raises(CommandRejected, match=field):
        case.owner.create_campaign(
            CampaignCreate(
                campaign_id="denied-character-import",
                display_name="Denied Lite spell import",
                writer=WriterRole.DM,
                package_lock=snapshot.package_lock,
                turn_id="denied-import-turn",
                participants=(invalid,),
                initial_state=tuple(
                    StateValue(VARIANT_ID, key.removeprefix(VARIANT_ID + "."), value)
                    for key, value in snapshot.state.items()
                ),
            )
        )
    assert case.snapshot() == snapshot


@pytest.mark.parametrize('mode,distance,reach,accepted', [
    ('melee',5,5,True), ('melee',5.01,5,False),
    ('melee',10,10,True), ('melee',10.01,10,False),
    ('thrown',20,5,True), ('thrown',60,5,True), ('thrown',60.01,5,False),
])
def test_native_weapon_reach_and_throw_boundaries(cases,mode,distance,reach,accepted):
    case=cases('true_strike',guard_ac=1,
        positions={'hero':[0,0,0],'guard':[distance,0,0],'other':[-5,0,0],'extra':[20,10,0]},
        entity_changes={'hero':{'melee_reach':reach}})
    case.start()
    parameters=case.parameters | {'true_strike_attack_mode':mode}
    if not accepted:
        case.reject('cast_spell',parameters)
        return
    before=case.entities()['guard']['hp']
    receipt,command=case.act('cast_spell',**parameters)
    assert case.entities()['guard']['hp'] < before
    assert case.snapshot().state[VARIANT_ID+'.combat']['action_available'] is False
    saved=case.snapshot()
    assert case.execute(command)==receipt and case.snapshot()==saved
    case.reopen()
    assert case.execute(command)==receipt and case.snapshot()==saved
