"""Shared spell-damage clauses compose at actual producers and saved target outcomes."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.srd_actor_spell_modifiers import _emit
from scripts.srd_full.srd_spell_damage_consumers import extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_attack_budget import _npc_attack
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_result_cards import (
    test_area_cast_preserves_all_64_actual_saves_and_damage_within_operation_budget as _area_budget,
)
from tests.srd_full.test_srd_spell_wave3_afflictions import _cast as _curse_cast
from tests.srd_full.test_srd_spell_wave4_divination import _case as _divination_case

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def composed_runtime():
    original = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    corrected = extend_runtime(original)
    assert extend_runtime(corrected) == corrected
    return corrected.encode()


@pytest.fixture(scope="module")
def compiled_consumers(composed_runtime):
    """Compile real public leaves from the final composition without a package rebuild."""
    runtime = _load_runtime(composed_runtime)

    @lru_cache
    def mechanic(name):
        schema = budget._tool_schema(runtime["tools"][name], tool_name=name)
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="a" * 64,
            runtime_bytes=composed_runtime,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.action-economy",
                input_schema=owned_payload(schema),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    return mechanic


@pytest.fixture(autouse=True)
def actual_consumers(monkeypatch, compiled_consumers):
    monkeypatch.setattr(budget, "_mechanic", compiled_consumers)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


def _grant(state, data, spell):
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append(spell)
    data["wizard_spellbook"].append(spell)
    data["spellcasting_grants"][0]["spells"].append(spell)
    supplies, _, parameters = _spell_case(spell)
    for item in supplies[f"{SRD}.entities"]["hero"]["spell_materials"]:
        material = deepcopy(item)
        material["id"] = f"{spell}_{item['id']}"
        hero["spell_materials"].append(material)
        hero["inventory"].append(material["inventory_item"])
    parameters["material_item_ids"] = [
        f"{spell}_{item}" for item in parameters["material_item_ids"]
    ]
    return parameters


def _record_rolls(monkeypatch):
    """Observe the real dice owner while preserving its RNG and returned results."""
    original = _effect_executor.dice_roll
    rolls = []

    def roll(*args, **kwargs):
        result = original(*args, **kwargs)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, "dice_roll", roll)
    return rolls


def _enfeeble(state, data):
    """Use a prepared NPC's real cast so the PC's later concentration does not end the ray."""
    budget._run(
        "prepare_srd_creature",
        state,
        creature_id="mage",
        name="Ray caster",
        source="Synthetic spellcaster.",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=6,
        spellcasting_ability="intelligence",
        spellcasting_modifier=4,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[{"spell": "ray_of_enfeeblement", "uses_per_day": 1}],
    )
    _, _, parameters = _spell_case("ray_of_enfeeblement")
    parameters.pop("caster")
    parameters.pop("slot_resource")
    parameters.update(
        caster_state="mage",
        target="hero",
        targets=["hero"],
        forego_save_targets=["hero"],
    )
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = dict.fromkeys(
        data["abilities"],
        0,
    )
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)


@pytest.mark.parametrize("save_success", [False, True])
def test_mind_spike_penalty_precedes_target_save(monkeypatch, save_success):
    state, data, parameters = _divination_case("mind_spike")
    _enfeeble(state, data)
    guard = state[f"{SRD}.entities"]["guard"]
    guard["saving_throw_modifiers"]["wisdom"] = 30 if save_success else -30
    rolls = _record_rolls(monkeypatch)
    budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **parameters,
    )
    raw, penalty = [r for r in rolls if r.sides == 8]
    assert raw.count == 3 and penalty.count == 1
    expected = max(0, raw.total - penalty.total)
    if save_success:
        expected //= 2
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 500 - expected
    assert (state[f"{SRD}.entities"]["hero"].get("concentration_spell_id") == "mind_spike") is (
        not save_success
    )


def test_mind_spike_ignores_retired_enfeeblement_with_stale_projection(monkeypatch):
    state, data, parameters = _divination_case("mind_spike")
    _enfeeble(state, data)
    budget._run("end_concentration", state, caster_state="mage")
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(has_actor_spell_modifiers=True, spell_ray_of_enfeeblement=True)
    rolls = _record_rolls(monkeypatch)
    budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **parameters,
    )
    (raw,) = [r for r in rolls if r.sides == 8]
    assert raw.count == 3
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 500 - raw.total


@pytest.mark.parametrize("immune", [False, True])
def test_composite_spell_curse_rider_is_once_per_target_after_actual_damage(monkeypatch, immune):
    state, data, _, _ = _curse_cast(
        monkeypatch,
        "bestow_curse",
        curse_mode="damage",
        slot_resource="spell_slot_5",
    )
    parameters = _grant(state, data, "ice_storm")
    parameters["targets"] = ["guard", "other"]
    guard = state[f"{SRD}.entities"]["guard"]
    other = state[f"{SRD}.entities"]["other"]
    guard.update(temp_hp=0, damage_immunities=["bludgeoning", "cold"] if immune else [])
    before = guard["hp"]
    rolls = _record_rolls(monkeypatch)
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    other = state[f"{SRD}.entities"]["other"]
    if immune:
        assert guard["hp"] == before - guard["last_curse_damage_roll"]["total"]
    else:
        assert len(guard["last_curse_damage_roll"]["results"]) == 1
        assert guard["hp"] < before - guard["last_curse_damage_roll"]["total"]
    assert len([r for r in rolls if r.sides == 8 and r.count == 1]) == 1
    assert "last_curse_damage_roll" not in other


@pytest.mark.parametrize(
    "base_immune,necrotic_resisted", [(False, False), (True, False), (True, True)]
)
def test_actual_attack_roll_penalty_allocates_remainder_to_curse_before_typed_defenses(
    monkeypatch,
    base_immune,
    necrotic_resisted,
):
    state, data, _, _ = _curse_cast(
        monkeypatch,
        "bestow_curse",
        curse_mode="damage",
        slot_resource="spell_slot_5",
    )
    parameters = _grant(state, data, "fire_bolt")
    _enfeeble(state, data)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        temp_hp=0,
        damage_immunities=["fire"] if base_immune else [],
        damage_resistances=["necrotic"] if necrotic_resisted else [],
    )

    class ActualRawFaces:
        def randint(self, low, high):
            assert low == 1
            return 8 if high == 8 else 1 if high == 10 else 10

    monkeypatch.setattr(dice, "_rng", ActualRawFaces())
    rolls = _record_rolls(monkeypatch)
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **parameters,
    ).result
    (raw,) = [r for r in rolls if r.sides == 10]
    penalty, extra = [r for r in rolls if r.sides == 8]
    assert raw.results == (1, 1, 1, 1) and raw.total == 4
    assert penalty.total == extra.total == 8
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["last_curse_damage_roll"]["results"] == [8]
    assert guard["last_curse_damage_roll"]["total"] == 8
    assert result["context"]["_spell_damage"]["results"] == [1, 1, 1, 1]
    assert guard["hp"] == 1000 - (2 if necrotic_resisted else 4)


def test_mind_spike_curse_rider_uses_actual_spell_source(monkeypatch):
    state, data, _, _ = _curse_cast(
        monkeypatch,
        "bestow_curse",
        curse_mode="damage",
        slot_resource="spell_slot_5",
    )
    parameters = _grant(state, data, "mind_spike")
    scene, _, _ = _divination_case("mind_spike")
    state[f"{SRD}.state"]["spatial_scene"] = scene[f"{SRD}.state"]["spatial_scene"]
    guard = state[f"{SRD}.entities"]["guard"]
    guard["temp_hp"] = 0
    before = guard["hp"]
    rolls = _record_rolls(monkeypatch)
    budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **parameters,
    )
    guard = state[f"{SRD}.entities"]["guard"]
    extra = guard["last_curse_damage_roll"]
    assert len(extra["results"]) == 1
    raw, rider = [r for r in rolls if r.sides == 8]
    assert raw.count == 3 and rider.count == 1
    assert guard["hp"] == before - raw.total - extra["total"]


def test_fire_shield_curse_rider_uses_later_melee_hit_without_a_reaction(monkeypatch):
    state, data, _, _ = _curse_cast(
        monkeypatch,
        "bestow_curse",
        curse_mode="damage",
        slot_resource="spell_slot_5",
    )
    parameters = _grant(state, data, "fire_shield")
    parameters.pop("target")
    parameters["actor_spell_choice"] = "warm"
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        normal_action_spent=False,
    )
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(multiattack=[{"attack": "Claw", "count": 1}], temp_hp=0)
    before = guard["hp"]
    result = _npc_attack(
        state,
        "Claw",
        attack_modifier=100,
        attack_mode="melee",
        attack_distance=5,
    ).result["context"]
    guard = state[f"{SRD}.entities"]["guard"]
    extra = guard["last_curse_damage_roll"]
    assert len(extra["results"]) == 1
    assert guard["hp"] == before - result["_fire_shield_damage_applied"]
    assert result["_fire_shield_damage_applied"] > extra["total"]
    assert state[f"{SRD}.entities"]["hero"]["reaction_available"] is True


def test_existing_spell_curse_consumer_and_environment_exclusion_are_preserved(monkeypatch):
    state, data, _, _ = _curse_cast(
        monkeypatch,
        "bestow_curse",
        curse_mode="damage",
        slot_resource="spell_slot_5",
    )
    budget._run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=1,
        adjustment=0,
        damage_type="fire",
        source_state_id="hero",
    )
    assert "last_curse_damage_roll" not in state[f"{SRD}.entities"]["guard"]
    parameters = _grant(state, data, "magic_missile")
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert len(state[f"{SRD}.entities"]["guard"]["last_curse_damage_roll"]["results"]) == 1


@pytest.mark.parametrize("composite", [False, True])
def test_curse_component_uses_one_concentration_save_and_one_temp_hp_packet(monkeypatch, composite):
    state, _, _, _ = _curse_cast(
        monkeypatch,
        "bestow_curse",
        curse_mode="damage",
        slot_resource="spell_slot_5",
    )
    budget._run(
        "start_srd_condition_concentration",
        state,
        caster_state="guard",
        lasting_state="state",
        targets=["other"],
        spell="hold_person",
        duration_rounds=10,
        save_modifier=100,
    )
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(temp_hp=100, damage_resistances=["necrotic"])
    rolls = _record_rolls(monkeypatch)
    if composite:
        result = budget._run(
            "apply_srd_qualifying_composite_damage",
            state,
            target="guard",
            source_state_id="hero",
            component_1_amount=24,
            component_1_damage_type="cold",
            component_2_amount=5,
            component_2_damage_type="bludgeoning",
        ).result["context"]
        base = 29
    else:
        result = budget._run(
            "apply_srd_qualifying_damage",
            state,
            target="guard",
            source_state_id="hero",
            amount=24,
            adjustment=0,
            damage_type="cold",
        ).result["context"]
        base = 24
    (rider,) = [r for r in rolls if r.sides == 8]
    assert len([r for r in rolls if r.sides == 20]) == 1
    damage = base + rider.total // 2
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and guard["temp_hp"] == 100 - damage
    assert result["_effective_damage"] == damage
    assert result["_concentration_save_dc"] == damage // 2


def test_curse_component_is_rolled_before_lethal_base_hit(monkeypatch):
    state, data, _, _ = _curse_cast(
        monkeypatch,
        "bestow_curse",
        curse_mode="damage",
        slot_resource="spell_slot_5",
    )
    parameters = _grant(state, data, "ice_storm")
    parameters["targets"] = ["guard"]
    state[f"{SRD}.entities"]["guard"].update(hp=1, temp_hp=0, creature_kind="monster")
    rolls = _record_rolls(monkeypatch)
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["is_alive"] is False and guard["hp"] == 0
    assert len([r for r in rolls if r.sides == 8 and r.count == 1]) == 1
    assert "last_curse_damage_roll" in guard


def test_retired_curse_with_stale_projection_does_not_add_damage(monkeypatch):
    state, _, _, _ = _curse_cast(
        monkeypatch,
        "bestow_curse",
        curse_mode="damage",
        slot_resource="spell_slot_5",
    )
    guard = state[f"{SRD}.entities"]["guard"]
    source_id = guard["control_affliction_source_ids"][0]
    state[f"{SRD}.state"]["timed_condition_sources"][source_id]["expires_tick"] = 0
    rolls = _record_rolls(monkeypatch)
    budget._run(
        "apply_srd_qualifying_damage",
        state,
        target="guard",
        source_state_id="hero",
        amount=1,
        adjustment=0,
        damage_type="fire",
    )
    assert not [r for r in rolls if r.sides == 8]
    assert "last_curse_damage_roll" not in state[f"{SRD}.entities"]["guard"]


@pytest.mark.parametrize("save_modifier,outcome", [(30, "success"), (-30, "failure")])
def test_final_damage_consumers_preserve_64_target_budget(save_modifier, outcome):
    _area_budget(save_modifier, outcome)


def test_public_curse_reopens_then_changes_later_composite_cast_and_retry(tmp_path, monkeypatch):
    database = tmp_path / "damage-consumers.db"
    curse_state, _, _ = _spell_case("bestow_curse")
    storm_state, _, storm = _spell_case("ice_storm")
    curse_hero = curse_state[f"{SRD}.entities"]["hero"]
    storm_materials = deepcopy(storm_state[f"{SRD}.entities"]["hero"]["spell_materials"])
    for material in storm_materials:
        material["id"] = f"storm_{material['id']}"
    storm["material_item_ids"] = [item["id"] for item in storm_materials]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, curse = _create_spell_campaign(
            owner,
            "bestow_curse",
            participant_overrides={
                "wizard_spellbook": ["bestow_curse", "ice_storm"],
                "spellcasting_grants": [
                    {
                        "source": "wizard",
                        "ability": "intelligence",
                        "spells": ["bestow_curse", "ice_storm"],
                    }
                ],
            },
            entity_overrides={
                "prepared_spells": ["bestow_curse", "ice_storm"],
                "spell_materials": curse_hero["spell_materials"] + storm_materials,
                "inventory": curse_hero["inventory"]
                + [item["inventory_item"] for item in storm_materials],
            },
            target_overrides={
                "hp": 1000,
                "max_hp": 1000,
                "temp_hp": 0,
                "saving_throw_modifiers": dict.fromkeys(
                    ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"],
                    -100,
                ),
            },
        )
        receipt, command = _act(
            owner,
            "cast_spell_outside_combat",
            **(
                curse
                | {
                    "targets": ["guard"],
                    "curse_mode": "damage",
                    "slot_resource": "spell_slot_5",
                }
            ),
        )
        assert receipt.status is ActionStatus.COMMITTED
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        rolls = _record_rolls(monkeypatch)
        receipt, command = _act(
            owner,
            "cast_spell_outside_combat",
            **(storm | {"targets": ["guard"]}),
        )
        assert receipt.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        guard = after.state[f"{SRD}.entities"]["guard"]
        (rider,) = [r for r in rolls if r.sides == 8 and r.count == 1]
        damage = sum(r.total for r in rolls if r.sides in {6, 10}) + rider.total
        assert guard["hp"] == 1000 - damage
        assert guard["last_curse_damage_roll"]["total"] == rider.total
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting").state == after.state


def test_actual_befuddlement_regeneration_keeps_its_failed_save_rider_and_curse(monkeypatch):
    from scripts import srd_befuddlement as module

    original = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    tools = _load_runtime(original)["tools"]
    changed = {}
    module.compose_runtime(tools, changed)
    regenerated = _emit(original, changed)
    corrected = extend_runtime(regenerated)
    assert extend_runtime(corrected) == corrected
    runtime = _load_runtime(corrected)
    effects = str(runtime["tools"]["cast_spell_single_target_save_damage"]["effects"])
    assert "install_srd_befuddlement_source" in effects
    assert "apply_srd_qualifying_damage" in effects
    assert "apply_srd_curse_damage_rider" not in effects

    @lru_cache
    def mechanic(name):
        schema = budget._tool_schema(runtime["tools"][name], tool_name=name)
        return materialize_package_mechanic(
            package_id=SRD, package_version="0.3.1", package_sha256="b" * 64,
            runtime_bytes=corrected.encode(),
            declaration=ToolDeclaration(
                name=name, capability="dnd.srd.5.2.1.action-economy",
                input_schema=owned_payload(schema), writers=("dm",), turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    state, data, _, _ = _curse_cast(
        monkeypatch, "bestow_curse", curse_mode="damage", slot_resource="spell_slot_5",
    )
    parameters = _grant(state, data, "befuddlement")
    state[f"{SRD}.entities"]["guard"].update(temp_hp=0)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["intelligence"] = -100
    rolls = _record_rolls(monkeypatch)
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    assert len([r for r in rolls if r.sides == 8 and r.count == 1]) == 1
    assert guard["befuddled"] is True
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    assert any(s["installed"] and s["spell_id"] == "befuddlement" for s in sources.values())


def test_contact_failure_composes_actual_enfeeblement_and_exact_self_curse(monkeypatch):
    state, data, parameters = _divination_case("contact_other_plane")
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=500, max_hp=500, temp_hp=5)
    hero["saving_throw_modifiers"]["wisdom"] = -100
    curse = _grant(state, data, "bestow_curse")
    curse.update(target="hero", targets=["hero"], curse_mode="damage", slot_resource="spell_slot_5")
    budget._run("cast_spell_outside_combat", state, participant_data=data, **curse)
    assert state[f"{SRD}.entities"]["hero"]["control_affliction_source_ids"]
    budget._run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    budget._run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _enfeeble(state, data)
    hero = state[f"{SRD}.entities"]["hero"]
    before = hero["hp"] + hero["temp_hp"]
    monkeypatch.setattr(dice, "_rng", Random(2))
    rolls = _record_rolls(monkeypatch)
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    hero = state[f"{SRD}.entities"]["hero"]
    (raw,) = [r for r in rolls if r.sides == 6]
    penalty, extra = [r for r in rolls if r.sides == 8]
    assert raw.count == 6 and penalty.count == extra.count == 1
    assert hero["hp"] + hero["temp_hp"] == before - max(0, raw.total - penalty.total) - extra.total
    assert hero["last_curse_damage_roll"]["total"] == extra.total
    assert "incapacitated" in hero["conditions"]
