"""Created forces retain their actual sources, positions, action costs and lifetimes."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_spell_summoning import (
    FAMILIAR_PROFILE,
    GUARDIAN_PROFILE,
    HOUND_PROFILE,
    PROFILES,
    SOURCE,
    STEED_PROFILE,
    validate_profiles,
)

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_wave3_information import SRD, _free
from tests.srd_full.test_srd_spell_wave3_information import _case as _information_case

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def _created_undead_runtime():
    import json

    from scripts.srd_full.build_srd_5_2_1_spells import (
        SPELL_DEFINITIONS_END,
        SPELL_DEFINITIONS_START,
        SPELL_SELECTOR_END,
        SPELL_SELECTOR_START,
        _replace_generated_block,
    )
    from scripts.srd_full.srd_reaction_spells import extend_runtime as compose_reactions
    from scripts.srd_full.srd_spell_summoning import (
        CREATED_UNDEAD_PROFILE,
        bind_animated_undead_turn_consumers,
        bind_created_undead_cast_consumers,
        extend_runtime,
    )
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
    from scripts.srd_full.srd_timed_senses import _replace_tools

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    text = extend_runtime((ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text())
    text = bind_animated_undead_turn_consumers(text)
    assert bind_animated_undead_turn_consumers(text) == text
    text = compose_reactions(text)
    before = _load_runtime(text)["tools"]
    text = bind_created_undead_cast_consumers(text)
    assert bind_created_undead_cast_consumers(text) == text
    runtime = _load_runtime(text)
    after = runtime["tools"]
    assert {name for name in before if before[name] != after[name]} <= {
        "begin_srd_spell_cast_internal",
        "begin_srd_spell_cast",
    }
    assert before["cast_spell_outside_combat"] == after["cast_spell_outside_combat"]
    definitions = runtime["definitions"]["spells"]
    previous = set(definitions)
    changed = {}
    for name, tool in runtime["tools"].items():
        for parameter in tool["parameters"]:
            if parameter.get("type") == "enum" and set(parameter["enum_values"]) == previous:
                parameter["enum_values"] = sorted(previous | {"create_undead"})
                changed[name] = tool
    text = _replace_tools(text, changed)
    definitions["create_undead"] = CREATED_UNDEAD_PROFILE
    text = _replace_generated_block(
        text,
        SPELL_DEFINITIONS_START,
        SPELL_DEFINITIONS_END,
        ["    " + spell + ": " + json.dumps(row) for spell, row in sorted(definitions.items())],
    )
    text = _replace_generated_block(
        text,
        SPELL_SELECTOR_START,
        SPELL_SELECTOR_END,
        ["          - " + spell for spell in sorted(definitions)],
    )
    assert not validate_ruleset(text)
    runtime = _load_runtime(text)
    validate_content_shape("package_runtime", runtime, file="created-undead-owning-fragment")
    return runtime, text.encode()


@pytest.fixture
def created_undead_owner(monkeypatch, _created_undead_runtime):
    from functools import lru_cache

    import tests.srd_full.test_srd_action_budget as budget
    import tests.srd_full.test_srd_spell_result_cards as cards
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    runtime, encoded = _created_undead_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="e" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    budget._tool_schema(runtime["tools"][name], tool_name=name)
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    return runtime


def _created_undead_case(count=1, slot=6):
    state, data = _recorded_corpse_case("create_undead")
    _, _, parameters = _information_case("create_undead")
    parameters.pop("target")
    corpse = state[f"{SRD}.entities"]["guard"]
    corpse.update(inventory=[], equipment=[])
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    original = deepcopy(corpse)
    original_scene = deepcopy(scene[1])
    points = [[5, 0, 0], [-5, 0, 0], [0, 5, 0], [0, -5, 0], [5, 0, 6], [-5, 0, 6]]
    choices = []
    for index in range(count):
        identity = "guard" if index == 0 else f"ghoul_corpse_{index}"
        if index:
            state[f"{SRD}.entities"][identity] = deepcopy(original)
            state[f"{SRD}.entities"][identity].update(
                hp=15,
                temp_hp=0,
                is_alive=True,
                death_tick=None,
                conditions=[],
            )
            admitted = deepcopy(original_scene)
            admitted.update(id=identity)
            admitted["metadata"]["participant_ref"] = identity
            scene.append(admitted)
            _run(
                "apply_srd_damage",
                state,
                target=identity,
                amount=15,
                adjustment=0,
                damage_type="radiant",
                critical_hit=False,
            )
        scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
        admitted = next(r for r in scene if r["metadata"].get("participant_ref") == identity)
        admitted["position"] = points[index]
        admitted["metadata"]["creature_type"] = "humanoid"
        choices.append(
            {"corpse_state_id": identity, "individual_id": f"ghoul_{index}", "appearance_height": 6}
        )
    parameters.update(slot_resource=f"spell_slot_{slot}", created_undead_bodies=choices)
    return state, data, parameters


def _finish_created_undead_cast(state, data, parameters, material_target_count=1):
    _run("record_srd_map_time_of_day", state, plane="material", phase="night")
    _run(
        "begin_srd_spell_cast",
        state,
        participant_data=data,
        **{
            k: v
            for k, v in parameters.items()
            if k
            in {
                "spell",
                "caster",
                "caster_state",
                "slot_resource",
                "material_component_available",
                "material_item_ids",
                "verbal_component_available",
                "somatic_component_available",
            }
        },
        material_target_count=material_target_count,
    )
    _run(
        "continue_srd_spell_cast",
        state,
        participant_data=data,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("record_srd_map_time_of_day", state, plane="material", phase="night")
    return _run("cast_spell_outside_combat", state, participant_data=data, **parameters)


def test_created_undead_long_start_keeps_initial_material_intent_and_completion_selection(
    created_undead_owner,
):
    from tests.srd_full.test_srd_extended_casting import _begin

    state, data, parameters = _created_undead_case(3, 6)
    state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] = 1
    _run("record_srd_map_time_of_day", state, plane="material", phase="night")
    materials = deepcopy(state[f"{SRD}.entities"]["hero"]["spell_materials"])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "begin_srd_spell_cast",
            state,
            participant_data=data,
            **_begin(parameters),
            material_target_count=3,
        )
    assert state == before
    _run(
        "begin_srd_spell_cast",
        state,
        participant_data=data,
        **_begin(parameters),
        material_target_count=1,
    )
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["extended_casting"]["spell"] == "create_undead"
    assert hero["resources"]["spell_slot_6"] == 3 and "ghoul_0" not in state[f"{SRD}.entities"]
    _run(
        "continue_srd_spell_cast",
        state,
        participant_data=data,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("record_srd_map_time_of_day", state, plane="material", phase="night")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "cast_spell_outside_combat",
            state,
            participant_data=data,
            **parameters,
            material_target_count=1,
        )
    assert state == before
    state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] = 3
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **parameters,
        material_target_count=1,
    )
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_6"] == 2 and hero["extended_casting"] is None
    assert all(f"ghoul_{index}" in state[f"{SRD}.entities"] for index in range(3))
    assert hero["spell_materials"] == [materials[0] | {"quantity": 3}]


@pytest.mark.parametrize("interruption", ["damage", "dismiss"])
def test_created_undead_interrupted_long_cast_keeps_unconsumed_bodies_and_slot(
    created_undead_owner,
    interruption,
):
    from tests.srd_full.test_srd_extended_casting import _begin

    state, data, parameters = _created_undead_case()
    _run("record_srd_map_time_of_day", state, plane="material", phase="night")
    original_body = deepcopy(state[f"{SRD}.entities"]["guard"])
    materials = deepcopy(state[f"{SRD}.entities"]["hero"]["spell_materials"])
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    if interruption == "damage":
        state[f"{SRD}.entities"]["hero"]["concentration_save_modifier"] = -100
        _run(
            "apply_srd_damage",
            state,
            target="hero",
            amount=1,
            adjustment=0,
            damage_type="force",
            critical_hit=False,
        )
    else:
        _run("end_concentration", state, caster_state="hero")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["extended_casting"] is None and hero["concentration_spell_id"] is None
    assert hero["resources"]["spell_slot_6"] == 3 and hero["spell_materials"] == materials
    assert state[f"{SRD}.entities"]["guard"] == original_body
    assert "ghoul_0" not in state[f"{SRD}.entities"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="no uninterrupted"):
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    assert state == before


@pytest.mark.parametrize("slot,count", [(6, 3), (7, 4), (8, 5), (9, 6)])
def test_created_undead_uses_actual_night_corpses_per_stone_value_and_printed_ghoul_body(
    created_undead_owner,
    slot,
    count,
):
    from scripts.srd_full.srd_spell_summoning import _created_undead_source_profile

    state, data, parameters = _created_undead_case(count, slot)
    materials = state[f"{SRD}.entities"]["hero"]["spell_materials"]
    materials[0]["quantity"] = count
    before_material = deepcopy(materials)
    result = _finish_created_undead_cast(state, data, parameters)
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"] == before_material
    source = _created_undead_source_profile()
    for index, choice in enumerate(parameters["created_undead_bodies"]):
        ghoul = state[f"{SRD}.entities"][f"ghoul_{index}"]
        corpse = state[f"{SRD}.entities"][choice["corpse_state_id"]]
        assert ghoul["hp"] == ghoul["max_hp"] == 22 and ghoul["armor_class"] == 12
        assert ghoul["speed"] == 30 and ghoul["initiative_modifier"] == 2
        assert ghoul["ability_scores"] == source["ability_scores"]
        assert ghoul["saving_throw_modifiers"] == source["saving_throw_modifiers"]
        assert ghoul["condition_immunities"] == ["charmed", "exhaustion", "poisoned"]
        assert ghoul["damage_immunities"] == ["poison"]
        assert ghoul["languages"] == ["common"] and ghoul["darkvision_feet"] == 60
        assert ghoul["created_spell_id"] == "create_undead" and ghoul["created_form_id"] == "ghoul"
        assert ghoul["created_source_block_sha256"] == source["source_stat_block_sha256"]
        assert ghoul["created_body_source_state_id"] == choice["corpse_state_id"]
        assert corpse["body_available"] is False
        assert corpse["body_animated_actor_id"] == f"ghoul_{index}"
        assert ghoul["multiattack"] == [{"attack": "Bite", "count": 2}]
        assert ghoul["animated_control_until"] == 28821
    assert result.result["result_card"]["title"] == "Create Undead"
    assert any(
        r["label"] == "Undead individuals" and r["value"] == count
        for r in result.result["result_card"]["details"]
    )


@pytest.mark.parametrize(
    "form,slot,count,languages,hp,ac,initiative",
    [
        ("ghast", 8, 2, [], 36, 13, 3),
        ("ghast", 9, 3, [], 36, 13, 3),
    ],
)
def test_created_undead_additional_bodies_use_printed_source_and_real_corpse_ownership(
    created_undead_owner,
    form,
    slot,
    count,
    languages,
    hp,
    ac,
    initiative,
):
    from scripts.srd_full.srd_spell_summoning import _created_undead_additional_profiles

    state, data, parameters = _created_undead_case(count, slot)
    parameters["created_undead_form"] = form
    for choice in parameters["created_undead_bodies"]:
        choice["other_languages"] = languages
    state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] = count
    if form == "mummy":
        state[f"{SRD}.entities"]["guard"]["size_rank"] = 1
    result = _finish_created_undead_cast(state, data, parameters)
    source = _created_undead_additional_profiles()[form]
    for index, choice in enumerate(parameters["created_undead_bodies"]):
        actor = state[f"{SRD}.entities"][f"ghoul_{index}"]
        assert actor["created_form_id"] == form
        assert actor["hp"] == actor["max_hp"] == hp and actor["armor_class"] == ac
        assert actor["initiative_modifier"] == initiative
        assert actor["ability_scores"] == source["ability_scores"]
        assert actor["saving_throw_modifiers"] == source["saving_throw_modifiers"]
        assert actor["damage_immunities"] == source["damage_immunities"]
        assert actor["damage_resistances"] == source["damage_resistances"]
        assert actor["damage_vulnerabilities"] == source["damage_vulnerabilities"]
        assert actor["condition_immunities"] == source["condition_immunities"]
        assert actor["languages"] == ["common", *languages]
        assert actor["created_source_attacks"] == source["attacks"]
        assert actor["created_source_block_sha256"] == source["source_stat_block_sha256"]
        assert actor["created_body_source_state_id"] == choice["corpse_state_id"]
        corpse = state[f"{SRD}.entities"][choice["corpse_state_id"]]
        assert corpse["body_available"] is False
        assert corpse["body_animated_actor_id"] == f"ghoul_{index}"
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    assert any(
        r["label"] == "Printed body" and r["value"] == form
        for r in result.result["result_card"]["details"]
    )


def _ghast_case(count=1):
    state, data, parameters = _created_undead_case(count, 8 if count <= 2 else 9)
    parameters["created_undead_form"] = "ghast"
    state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] = count
    _finish_created_undead_cast(state, data, parameters)
    _admit_zombie_slam_target(state)
    return state, data


def _begin_created_undead_target_turn(state, data, identity="hero"):
    entities = state[f"{SRD}.entities"]
    entities["hero"]["initiative_modifier"] = (data["abilities"]["dexterity"] - 10) // 2
    entities["other"]["initiative_modifier"] = 0
    combatants = ["hero", *state[f"{SRD}.state"]["created_ghast_ids"], "other"]
    _run("start_group_combat_encounter", state, participant_data=data, combatants=combatants)
    for _ in range(len(combatants)):
        actor = state[f"{SRD}.combat"]["current_actor"]
        if actor == identity:
            return
        _run("begin_combat_turn", state, actor=actor, participant_data=data)
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
    pytest.fail("The actual target did not reach its independent initiative turn.")


@pytest.mark.parametrize(
    "placement,poisoned",
    [
        ("ordinary", True),
        ("edge", True),
        ("outside", False),
        ("wall", False),
        ("plane", False),
    ],
)
def test_created_ghast_stench_uses_actual_turn_body_emanation_plane_and_physical_path(
    created_undead_owner,
    monkeypatch,
    placement,
    poisoned,
):
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, data = _ghast_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    source = next(r for r in scene["entities"] if r["id"] == "ghoul_0")
    if placement == "edge":
        source["position"] = [7.9, 0, 0]
    elif placement == "outside":
        source["position"] = [8, 0, 0]
    elif placement == "wall":
        wall = _wall(1)
        wall["volume"].update(min=[1, -10, -10], max=[2, 10, 10])
        scene["geometry"] = [wall]
        next(r for r in scene["entities"] if r["id"] == "hero")["metadata"]["cover_from"] = {
            "ghoul_0": "total"
        }
    elif placement == "plane":
        source["metadata"]["plane"] = "ethereal"
    assert state[f"{SRD}.state"]["has_created_ghasts"] is True
    assert state[f"{SRD}.state"]["created_ghast_ids"] == ["ghoul_0"]
    _begin_created_undead_target_turn(state, data)
    geometry = _run(
        "qualify_srd_created_ghast_stench_geometry",
        state,
        actor_state="ghoul_0",
        target_state="hero",
    )
    assert geometry.result["context"]["_ghast_stench_in_area"] is poisoned
    import sylvia_gm.application.package_runtime as package

    actual = []
    execute = package.execute_tool

    def observed(name, parameters, *args, **kwargs):
        result = execute(name, parameters, *args, **kwargs)
        if name == "resolve_srd_created_ghast_stench" and parameters["target_state"] == "hero":
            actual.append(
                {
                    key: result.context.get(key)
                    for key in (
                        "_ghast_stench_outcome",
                        "_source_condition_immune",
                        "_ghast_stench_save_total",
                    )
                }
            )
        return result

    monkeypatch.setattr(package, "execute_tool", observed)
    monkeypatch.setattr(dice, "_rng", Random(1))
    result = _run("begin_combat_turn", state, actor="hero", participant_data=data)
    hero = state[f"{SRD}.entities"]["hero"]
    assert ("poisoned" in hero["conditions"]) is poisoned, actual
    if poisoned:
        details = result.result["result_card"]["details"]
        assert any(r["label"] == "Ghast Stench" and r["value"] == "failure" for r in details)
        assert any(r["label"] == "Stench Constitution dice" and r["value"] == [5] for r in details)
        assert any(r["label"] == "Stench Constitution total" and r["value"] == 7 for r in details)
    if poisoned:
        (source_id,) = hero["ghast_stench_source_ids"]
        effect = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
        assert effect["save_dc"] == 10 and effect["native_attack"] == "Stench"
        assert effect["native_body_source"] == "ghoul_0"
        assert effect["target_start_turn"] == hero["turns_started"] + 1
        assert effect["dispel_ongoing"] is False and effect["source_is_magical"] is False
        assert effect["native_body_source_sequence"] == effect["sequence"]
    else:
        assert not hero.get("ghast_stench_source_ids")


def test_created_ghast_stench_success_is_this_individuals_real_twenty_four_hour_immunity(
    created_undead_owner,
    monkeypatch,
):
    state, data = _ghast_case(2)
    # The second genuine body is nearby but has its own independent printed immunity key.
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    next(r for r in scene if r["id"] == "ghoul_1")["position"] = [0, 5, 0]
    _begin_created_undead_target_turn(state, data)

    class ActualSaves:
        count = 0

        def randint(self, low, high):
            assert (low, high) == (1, 20)
            self.count += 1
            return 18 if self.count == 1 else 2

    monkeypatch.setattr(dice, "_rng", ActualSaves())
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    hero = state[f"{SRD}.entities"]["hero"]
    lasting = state[f"{SRD}.state"]
    assert hero["ghast_stench_immunities"] == {"ghoul_0": lasting["lasting_tick"] + 28800}
    (source_id,) = hero["ghast_stench_source_ids"]
    assert lasting["timed_condition_sources"][source_id]["native_body_source"] == "ghoul_1"
    # Genuine later start retires the failed source while a moved second body cannot reapply it.
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    next(r for r in scene if r["id"] == "ghoul_1")["position"] = [0, 100, 0]
    monkeypatch.setattr(dice, "_rng", Random(0))
    _run("end_combat_turn", state)
    for _ in range(len(state[f"{SRD}.combat"]["order"])):
        _run("advance_group_combat_encounter_turn", state)
        actor = state[f"{SRD}.combat"]["current_actor"]
        if actor == "hero":
            break
        _run("begin_combat_turn", state, actor=actor, participant_data=data)
        _run("end_combat_turn", state)
    monkeypatch.setattr(dice, "_rng", Random(1))
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    hero = state[f"{SRD}.entities"]["hero"]
    assert "poisoned" not in hero["conditions"]
    assert hero["has_ghast_stench_sources"] is False
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is False
    assert hero["ghast_stench_immunities"]["ghoul_0"] > state[f"{SRD}.state"]["lasting_tick"]


@pytest.mark.parametrize(
    "attack,critical,species",
    [
        ("Bite", False, "human"),
        ("Bite", True, "human"),
        ("Claw", False, "elf"),
    ],
)
def test_created_ghast_real_native_bite_and_claw_use_printed_dice_and_one_action(
    created_undead_owner,
    monkeypatch,
    attack,
    critical,
    species,
):
    import sylvia_gm.application.package_runtime as package

    state, data = _ghast_case()
    data["species"] = species
    state[f"{SRD}.entities"]["hero"].update(data=deepcopy(data), creature_type="humanoid")
    _command_ghoul_attack_turn(state, data)
    packets, rolls = [], []
    execute = package.execute_tool

    def observed(name, parameters, *args, **kwargs):
        result = execute(name, parameters, *args, **kwargs)
        if name == "apply_srd_qualifying_composite_damage":
            packets.append(deepcopy(parameters))
        return result

    class ActualDice:
        def randint(self, low, high):
            assert low == 1 and high in (6, 8, 20)
            rolls.append(high)
            if high == 20:
                return (20 if critical else 18) if rolls.count(20) == 1 else 2
            return 3

    monkeypatch.setattr(package, "execute_tool", observed)
    monkeypatch.setattr(dice, "_rng", ActualDice())
    target = "hero" if attack == "Claw" else "other"
    result = _ghoul_bite(
        state,
        participant_data=data,
        target=target,
        attack_name=attack,
        attack_modifier=5,
        damage_expression="2d6" if attack == "Claw" else "d8",
        damage_modifier=3,
        damage_type="slashing" if attack == "Claw" else "piercing",
        use_multiattack=False,
        attack_distance=3 if target == "hero" else 5,
    )
    context = result.result["context"]
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.combat"].get("attacks_remaining", 0) == 0
    if attack == "Bite":
        assert len(packets) == 1 and packets[0]["source_state_id"] == "ghoul_0"
        assert (
            context["_component_1_effective_damage"],
            context["_component_2_effective_damage"],
        ) == ((9, 12) if critical else (6, 6))
        assert rolls == [20, *([8] * 6 if critical else [8] * 3)]
        assert len(context["_ghast_necrotic_damage_roll"]["results"]) == (4 if critical else 2)
    else:
        assert not packets and rolls == [20, 6, 6, 20]
        assert context["_ghast_claw_save_outcome"] == "failure"
        assert "paralyzed" in state[f"{SRD}.entities"]["hero"]["conditions"]
        effect = state[f"{SRD}.state"]["timed_condition_sources"][context["_ghast_claw_source_id"]]
        assert (
            effect["native_body_block_sha256"]
            == state[f"{SRD}.entities"]["ghoul_0"]["created_source_block_sha256"]
        )
        assert effect["native_attack"] == "Claw" and effect["save_dc"] == 10


@pytest.mark.parametrize("native_poisoned", [False, True])
def test_created_ghast_stench_survives_real_clock_and_source_death_then_expires_at_next_start(
    created_undead_owner,
    monkeypatch,
    native_poisoned,
):
    state, data = _ghast_case()
    _begin_created_undead_target_turn(state, data)
    monkeypatch.setattr(dice, "_rng", Random(1))
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    (source_id,) = state[f"{SRD}.entities"]["hero"]["ghast_stench_source_ids"]
    if native_poisoned:
        _run("apply_srd_condition", state, target="hero", condition="poisoned")
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=2)
    assert "poisoned" in state[f"{SRD}.entities"]["hero"]["conditions"]
    _run(
        "apply_srd_damage",
        state,
        target="ghoul_0",
        amount=36,
        adjustment=0,
        damage_type="radiant",
        critical_hit=False,
    )
    assert state[f"{SRD}.entities"]["ghoul_0"]["is_alive"] is False
    _run("end_combat_turn", state)
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is True
    for _ in range(len(state[f"{SRD}.combat"]["order"])):
        _run("advance_group_combat_encounter_turn", state)
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=actor, participant_data=data)
        if actor == "hero":
            break
        _run("end_combat_turn", state)
    else:
        pytest.fail("The real target never reached its next turn start.")
    hero = state[f"{SRD}.entities"]["hero"]
    assert ("poisoned" in hero["conditions"]) is native_poisoned
    assert hero["ghast_stench_source_ids"] == [] and hero["has_ghast_stench_sources"] is False
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is False


@pytest.mark.parametrize("choice", ["low_slot", "too_many", "language", "multiattack", "dice"])
def test_created_ghast_rejects_unprinted_creation_and_native_routines_before_mutation_or_roll(
    created_undead_owner,
    monkeypatch,
    choice,
):
    if choice in ("low_slot", "too_many", "language"):
        state, data, parameters = _created_undead_case(
            3 if choice == "too_many" else 1, 7 if choice == "low_slot" else 8
        )
        parameters["created_undead_form"] = "ghast"
        if choice == "language":
            parameters["created_undead_bodies"][0]["other_languages"] = ["infernal"]
        from tests.srd_full.test_srd_extended_casting import _begin

        _run("record_srd_map_time_of_day", state, plane="material", phase="night")
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
        _run(
            "continue_srd_spell_cast",
            state,
            participant_data=data,
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        _run("record_srd_map_time_of_day", state, plane="material", phase="night")
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected):
            _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
        assert state == before
        return
    state, data = _ghast_case()
    _command_ghoul_attack_turn(state, data)

    class NoRoll:
        def randint(self, low, high):
            pytest.fail("An unprinted routine must reject before any actual roll.")

    monkeypatch.setattr(dice, "_rng", NoRoll())
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _ghoul_bite(
            state,
            attack_modifier=5,
            damage_expression="d6" if choice == "dice" else "d8",
            damage_modifier=3,
            use_multiattack=choice == "multiattack",
        )
    assert state == before


@pytest.mark.parametrize(
    "form,slot,languages",
    [
        ("wight", 8, ["elvish"]),
        ("mummy", 9, ["elvish", "dwarvish"]),
    ],
)
def test_created_undead_incomplete_automatic_bodies_reject_before_consuming_the_actual_corpse(
    created_undead_owner,
    form,
    slot,
    languages,
):
    state, data, parameters = _created_undead_case(1, slot)
    parameters["created_undead_form"] = form
    parameters["created_undead_bodies"][0]["other_languages"] = languages
    _run("record_srd_map_time_of_day", state, plane="material", phase="night")
    from tests.srd_full.test_srd_extended_casting import _begin

    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    _run(
        "continue_srd_spell_cast",
        state,
        participant_data=data,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("record_srd_map_time_of_day", state, plane="material", phase="night")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="full native automatic"):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


@pytest.mark.parametrize(
    "bad",
    [
        "day",
        "stale",
        "low_value",
        "few_stones",
        "duplicate",
        "wight",
        "wrong_type",
        "undead",
        "consumed",
    ],
)
def test_created_undead_rejects_false_night_material_body_and_source_selection_atomically(
    created_undead_owner,
    bad,
):
    state, data, parameters = _created_undead_case(3, 6)
    _run("record_srd_map_time_of_day", state, plane="material", phase="night")
    begin_keys = {
        "spell",
        "caster",
        "caster_state",
        "slot_resource",
        "material_item_ids",
        "material_component_available",
        "verbal_component_available",
        "somatic_component_available",
    }
    begin = {k: v for k, v in parameters.items() if k in begin_keys}
    if bad == "day":
        _run("record_srd_map_time_of_day", state, plane="material", phase="day")
    elif bad == "stale":
        _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    if bad in {"day", "stale"}:
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected):
            _run("begin_srd_spell_cast", state, participant_data=data, **begin)
        assert state == before
        return
    _run("begin_srd_spell_cast", state, participant_data=data, **begin)
    _run(
        "continue_srd_spell_cast",
        state,
        participant_data=data,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("record_srd_map_time_of_day", state, plane="material", phase="night")
    hero = state[f"{SRD}.entities"]["hero"]
    corpse = state[f"{SRD}.entities"]["guard"]
    if bad == "low_value":
        hero["spell_materials"][0]["value_cp"] = 14999
    elif bad == "few_stones":
        hero["spell_materials"][0]["quantity"] = 2
    elif bad == "duplicate":
        parameters["created_undead_bodies"][1]["corpse_state_id"] = "guard"
    elif bad == "wight":
        parameters["created_undead_form"] = "wight"
    elif bad == "wrong_type":
        corpse["death_creature_type"] = "beast"
    elif bad == "undead":
        corpse["death_was_undead"] = True
    elif bad == "consumed":
        corpse["body_animated_actor_id"] = "original_owner"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def _created_ghoul_turn(state, data):
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = (
        data["abilities"]["dexterity"] - 10
    ) // 2
    state[f"{SRD}.entities"]["other"]["initiative_modifier"] = 0
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "ghoul_0", "other"],
    )
    for _ in range(3):
        actor = state[f"{SRD}.combat"]["current_actor"]
        begun = _run("begin_combat_turn", state, actor=actor, participant_data=data)
        if actor == "ghoul_0":
            return begun
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
    pytest.fail("The real Ghoul never reached its independent initiative turn.")


@pytest.mark.parametrize("distance,accepted", [(120, True), (121, False)])
def test_created_undead_command_uses_its_actual_source_range_and_one_bonus_action(
    created_undead_owner,
    distance,
    accepted,
):
    state, data, parameters = _created_undead_case()
    _finish_created_undead_cast(state, data, parameters)
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    next(r for r in scene if r["metadata"].get("participant_ref") == "ghoul_0")["position"] = [
        distance,
        0,
        0,
    ]
    before = deepcopy(state)
    arguments = {
        "caster_state": "hero",
        "actor_ids": ["ghoul_0"],
        "order_kind": "specific",
        "action": "attack",
    }
    if not accepted:
        with pytest.raises(RulesetPreconditionRejected, match="printed command range"):
            _run("command_srd_animated_undead", state, **arguments)
        assert state == before
        return
    _run("command_srd_animated_undead", state, **arguments)
    saved = state[f"{SRD}.entities"]["ghoul_0"]["animated_order"]
    assert saved["caster"] == "hero" and saved["selected_actor_ids"] == ["ghoul_0"]
    assert saved["action"] == "attack" and saved["issued_tick"] == 21
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="one Bonus Action"):
        _run("command_srd_animated_undead", state, **arguments)
    assert state == before
    _admit_zombie_slam_target(state)
    next(
        r
        for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if r["metadata"].get("participant_ref") == "other"
    )["position"] = [distance + 10, 0, 0]
    _created_ghoul_turn(state, data)
    ghoul = state[f"{SRD}.entities"]["ghoul_0"]
    assert ghoul["animated_order"] is None and ghoul["animated_turn_order"] == saved
    assert state[f"{SRD}.combat"]["action_available"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="unknown NPC species"):
        _run(
            "resolve_adjudicated_creature_attack",
            state,
            actor="ghoul_0",
            target="other",
            attack_name="Claw",
            attack_modifier=4,
            damage_expression="d4",
            damage_modifier=2,
            damage_type="slashing",
            attack_distance=5,
            attack_mode="melee",
            normal_range=5,
            long_range=5,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
            advantage=False,
            disadvantage=False,
        )
    assert state == before


@pytest.mark.parametrize("speed", [0, 30])
def test_created_undead_default_dodge_uses_the_existing_real_turn_and_benefit_rules(
    created_undead_owner,
    speed,
):
    state, data, parameters = _created_undead_case()
    _finish_created_undead_cast(state, data, parameters)
    state[f"{SRD}.entities"]["ghoul_0"]["speed"] = speed
    result = _created_ghoul_turn(state, data)
    ghoul = state[f"{SRD}.entities"]["ghoul_0"]
    assert ghoul["dodging"] is (speed > 0)
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.combat"]["action_taken"] == "dodge"
    assert ghoul["animated_turn_order"] is None
    order = next(r for r in state[f"{SRD}.combat"]["order"] if r["entity_id"] == "ghoul_0")
    assert order["modifier"] == 2
    assert any(
        r["label"] == "Create Undead" and "Dodge" in r["value"]
        for r in result.result["result_card"]["details"]
    )


def test_created_undead_renewal_keeps_the_body_and_control_expiry_never_dismisses_it(
    created_undead_owner,
):
    state, data, parameters = _created_undead_case()
    _finish_created_undead_cast(state, data, parameters)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="original body"):
        _run(
            "reassert_srd_animated_undead_control",
            state,
            actor_state="ghoul_0",
            caster_state="hero",
        )
    assert state == before
    state[f"{SRD}.entities"]["ghoul_0"].update(hp=9, memory="grave watch")
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=100)
    renewal = dict(
        parameters,
        created_undead_mode="reassert",
        created_undead_bodies=[],
        created_undead_actor_ids=["ghoul_0"],
    )
    _finish_created_undead_cast(state, data, renewal)
    body = state[f"{SRD}.entities"]["ghoul_0"]
    assert body["hp"] == 9 and body["memory"] == "grave watch"
    assert body["animated_control_until"] == 28941
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28800)
    body = state[f"{SRD}.entities"]["ghoul_0"]
    assert body["animated_control_active"] is False
    assert body["created_active"] is True and body["is_alive"] is True and body["hp"] == 9
    assert body["death_tick"] is None and body["created_body_source_state_id"] == "guard"
    record = state[f"{SRD}.state"]["created_actor_records"]["ghoul_0"]
    assert record["active"] is True and record["body_source"] == "guard"
    assert (
        next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "ghoul_0")[
            "active"
        ]
        is True
    )


def _ghoul_bite_case():
    """Create the actual source body and separately admit its living target's geometry."""
    state, data, parameters = _created_undead_case()
    _finish_created_undead_cast(state, data, parameters)
    _admit_zombie_slam_target(state)
    return state, data


def _ghoul_bite(state, **choices):
    return _run(
        "resolve_adjudicated_creature_attack",
        state,
        **(
            {
                "actor": "ghoul_0",
                "target": "other",
                "attack_name": "Bite",
                "attack_modifier": 4,
                "attack_mode": "melee",
                "damage_expression": "d6",
                "damage_modifier": 2,
                "damage_type": "piercing",
                "normal_range": 5,
                "long_range": 5,
                "attack_distance": 5,
                "actor_can_see_target": True,
                "target_can_see_actor": True,
                "target_is_charmer": False,
                "fear_source_in_sight": False,
                "advantage": False,
                "disadvantage": False,
                "use_multiattack": True,
                "attack_uses_weapon": False,
            }
            | choices
        ),
    )


def _command_ghoul_attack_turn(state, data, **order):
    _run(
        "command_srd_animated_undead",
        state,
        caster_state="hero",
        actor_ids=["ghoul_0"],
        **({"order_kind": "specific", "action": "attack"} | order),
    )
    return _created_ghoul_turn(state, data)


@pytest.mark.parametrize(
    "critical,defenses,expected",
    [
        (False, "ordinary", (5, 4)),
        (True, "ordinary", (8, 8)),
        (False, "typed", (2, 8)),
        (True, "immune", (8, 0)),
    ],
)
def test_created_ghoul_bite_owns_real_critical_components_one_packet_and_native_multiattack(
    created_undead_owner,
    monkeypatch,
    critical,
    defenses,
    expected,
):
    import sylvia_gm.application.package_runtime as package

    state, data = _ghoul_bite_case()
    _command_ghoul_attack_turn(state, data)
    target = state[f"{SRD}.entities"]["other"]
    if defenses == "typed":
        target.update(damage_resistances=["piercing"], damage_vulnerabilities=["necrotic"])
    elif defenses == "immune":
        target["damage_immunities"] = ["necrotic"]
    packets, rolls = [], []
    original_execute = package.execute_tool

    def observed(name, parameters, *args, **kwargs):
        result = original_execute(name, parameters, *args, **kwargs)
        if name == "apply_srd_qualifying_composite_damage":
            packets.append(deepcopy(parameters))
        return result

    class BiteDice:
        def randint(self, low, high):
            assert low == 1 and high in (6, 20)
            rolls.append(high)
            if high == 20:
                return 20 if critical else 18
            count = rolls.count(6)
            return 3 if count <= (2 if critical else 1) else 4

    monkeypatch.setattr(package, "execute_tool", observed)
    monkeypatch.setattr(dice, "_rng", BiteDice())
    result = _ghoul_bite(state)
    context = result.result["context"]
    assert context["_critical_hit"] is critical
    assert (
        context["_component_1_effective_damage"],
        context["_component_2_effective_damage"],
    ) == expected
    assert context["_damage_applied"] == sum(expected)
    assert state[f"{SRD}.entities"]["other"]["hp"] == 100 - sum(expected)
    assert rolls == [20, *([6] * 4 if critical else [6] * 2)]
    assert len(context["_ghoul_necrotic_damage_roll"]["results"]) == (2 if critical else 1)
    assert len(packets) == 1 and packets[0]["source_state_id"] == "ghoul_0"
    assert packets[0]["component_1_damage_type"] == "piercing"
    assert packets[0]["component_2_damage_type"] == "necrotic"
    assert packets[0]["critical_hit"] is critical and packets[0]["actual_attack_hit"] is True
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 1
    assert state[f"{SRD}.combat"]["multiattack_remaining"] == [{"attack": "Bite", "count": 1}]
    assert any(r["label"] == "Bite Necrotic dice" for r in result.result["result_card"]["details"])


def test_created_ghoul_two_bites_consume_one_native_action_and_reject_a_third(
    created_undead_owner,
    monkeypatch,
):
    state, data = _ghoul_bite_case()
    _command_ghoul_attack_turn(state, data)
    monkeypatch.setattr(dice, "_rng", Random(0))
    _ghoul_bite(state)
    assert state[f"{SRD}.combat"]["action_available"] is False
    _ghoul_bite(state, target="hero", attack_distance=3)
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _ghoul_bite(state)
    assert state == before


@pytest.mark.parametrize(
    "problem",
    [
        "claw",
        "die",
        "range",
        "distance",
        "plane",
        "cover",
        "source",
        "body",
        "routine",
        "order",
        "general",
    ],
)
def test_created_ghoul_bite_rejects_unowned_source_profile_geometry_or_task_atomically(
    created_undead_owner,
    monkeypatch,
    problem,
):
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, data = _ghoul_bite_case()
    order = (
        {"order_kind": "general", "task": "Guard this entrance."} if problem == "general" else {}
    )
    if problem == "general":
        order["action"] = "other"
    _command_ghoul_attack_turn(state, data, **order)
    choices = {}
    actor = state[f"{SRD}.entities"]["ghoul_0"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if problem == "claw":
        choices.update(attack_name="Claw", damage_expression="d4", damage_type="slashing")
    elif problem == "die":
        choices["damage_expression"] = "d8"
    elif problem == "range":
        choices["normal_range"] = 10
    elif problem == "distance":
        choices["attack_distance"] = 4
    elif problem == "plane":
        next(r for r in scene["entities"] if r["id"] == "other")["metadata"]["plane"] = "ethereal"
    elif problem == "cover":
        wall = _wall(10)
        wall["volume"].update(min=[9, -10, -10], max=[10, 10, 10])
        scene["geometry"] = [wall]
    elif problem == "source":
        actor["created_source_block_sha256"] = "0" * 64
    elif problem == "body":
        actor["created_body_source_id"] = "unrelated_body"
    elif problem == "routine":
        actor["multiattack"][0]["count"] = 3
    elif problem == "order":
        actor["animated_turn_order"] = None
    before = deepcopy(state)
    monkeypatch.setattr(dice, "_rng", Random(0))
    with pytest.raises(RulesetPreconditionRejected):
        _ghoul_bite(state, **choices)
    assert state == before


def test_created_ghoul_control_expiry_keeps_its_native_body_and_ordinary_attack(
    created_undead_owner,
    monkeypatch,
):
    state, data = _ghoul_bite_case()
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert state[f"{SRD}.entities"]["ghoul_0"]["animated_control_active"] is False
    _created_ghoul_turn(state, data)
    assert state[f"{SRD}.combat"]["action_available"] is True
    monkeypatch.setattr(dice, "_rng", Random(0))
    _ghoul_bite(state, use_multiattack=False)
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0
    assert state[f"{SRD}.entities"]["ghoul_0"]["created_active"] is True


def test_created_ghoul_bite_receives_both_types_before_one_real_concentration_save(
    created_undead_owner,
    monkeypatch,
):
    import sylvia_gm.application.package_runtime as package

    state, data = _ghoul_bite_case()
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    data["spellcasting_grants"][0]["spells"].append("blur")
    data["wizard_spellbook"].append("blur")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("blur")
    _, _, blur = _information_case("blur")
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(blur | {"target": "hero", "target_distance": 0}),
    )
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "blur"
    state[f"{SRD}.entities"]["hero"].update(hp=1000, max_hp=1000, temp_hp=5)
    _command_ghoul_attack_turn(state, data)
    saves, packets = [], []
    original_execute = package.execute_tool

    def observed(name, parameters, *args, **kwargs):
        result = original_execute(name, parameters, *args, **kwargs)
        if (
            name == "d20_test"
            and parameters.get("kind") == "saving_throw"
            and parameters.get("ability") == "constitution"
        ):
            saves.append(deepcopy(parameters))
        if name == "apply_srd_qualifying_composite_damage":
            packets.append(deepcopy(parameters))
        return result

    class BiteDice:
        def randint(self, low, high):
            assert low == 1 and high in (6, 20)
            return 18 if high == 20 else 4

    monkeypatch.setattr(package, "execute_tool", observed)
    monkeypatch.setattr(dice, "_rng", BiteDice())
    result = _ghoul_bite(state, target="hero", attack_distance=3).result["context"]
    assert result["_damage_applied"] == 10 and result["_damage_absorbed"] == 5
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 995
    assert len(packets) == len(saves) == 1
    assert packets[0]["component_1_amount"] == 6 and packets[0]["component_2_amount"] == 4
    assert saves[0]["target_number"] == 10


def _ghoul_claw(state, data, **choices):
    return _run(
        "resolve_adjudicated_creature_attack",
        state,
        participant_data=data,
        **(
            {
                "actor": "ghoul_0",
                "target": "hero",
                "attack_name": "Claw",
                "attack_modifier": 4,
                "attack_mode": "melee",
                "damage_expression": "d4",
                "damage_modifier": 2,
                "damage_type": "slashing",
                "normal_range": 5,
                "long_range": 5,
                "attack_distance": 3,
                "actor_can_see_target": True,
                "target_can_see_actor": True,
                "target_is_charmer": False,
                "fear_source_in_sight": False,
                "advantage": False,
                "disadvantage": False,
                "use_multiattack": False,
                "attack_uses_weapon": False,
            }
            | choices
        ),
    )


@pytest.mark.parametrize(
    "species,save,expected",
    [
        ("human", 1, "failure"),
        ("human", 20, "success"),
        ("elf", 1, "exempt"),
    ],
)
def test_created_ghoul_claw_uses_actual_native_species_and_one_printed_constitution_save(
    created_undead_owner,
    monkeypatch,
    species,
    save,
    expected,
):
    import sylvia_gm.application.package_runtime as package

    state, data = _ghoul_bite_case()
    data["species"] = species
    _command_ghoul_attack_turn(state, data)
    rolls, saves = [], []
    execute = package.execute_tool

    def observed(name, parameters, *args, **kwargs):
        result = execute(name, parameters, *args, **kwargs)
        if name == "d20_test" and parameters.get("kind") == "saving_throw":
            saves.append(deepcopy(parameters))
        return result

    class ClawDice:
        def randint(self, low, high):
            assert low == 1 and high in (4, 20)
            rolls.append(high)
            return 2 if high == 4 else 18 if rolls.count(20) == 1 else save

    monkeypatch.setattr(package, "execute_tool", observed)
    monkeypatch.setattr(dice, "_rng", ClawDice())
    before_hp = state[f"{SRD}.entities"]["hero"]["hp"]
    result = _ghoul_claw(state, data)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == before_hp - 4
    assert result.result["context"]["_ghoul_claw_save_outcome"] == expected
    assert ("paralyzed" in hero["conditions"]) is (expected == "failure")
    assert len(saves) == (0 if species == "elf" else 1)
    if saves:
        assert saves[0]["actor"] == "hero" and saves[0]["ability"] == "constitution"
        assert saves[0]["target_number"] == 10 and saves[0]["kind"] == "saving_throw"
    assert state[f"{SRD}.combat"]["action_available"] is False
    if expected == "failure":
        source_id = result.result["context"]["_ghoul_claw_source_id"]
        source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
        assert source["native_body_source"] == "ghoul_0" and source["native_attack"] == "Claw"
        assert source["native_body_source_sequence"] == source["sequence"]
        assert (
            source["native_body_block_sha256"]
            == state[f"{SRD}.entities"]["ghoul_0"]["created_source_block_sha256"]
        )
        assert source["target_end_turn"] == (hero.get("turns_started") or 0) + 1
        assert source["expires_tick"] is None and source["repeat_save_timing"] == "none"
        assert source["ongoing_duration"] is False and source["source_is_magical"] is False
        assert source["dispel_ongoing"] is False and source["cast_id"] == ""


def test_created_ghoul_claw_exempts_an_actual_second_created_undead_without_a_save(
    created_undead_owner,
    monkeypatch,
):
    state, data, parameters = _created_undead_case(2, 6)
    data["species"] = "human"
    _finish_created_undead_cast(state, data, parameters)
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    next(entry for entry in scene if entry["metadata"].get("participant_ref") == "hero")[
        "position"
    ] = [0, 10, 0]
    _command_ghoul_attack_turn(state, data)
    d20 = []

    class ClawDice:
        def randint(self, low, high):
            assert low == 1 and high in (4, 20)
            if high == 4:
                return 2
            d20.append(high)
            assert len(d20) == 1, "An actual Undead exemption must not roll a Claw save."
            return 18

    monkeypatch.setattr(dice, "_rng", ClawDice())
    result = _ghoul_claw(state, data, target="ghoul_1", attack_distance=5)
    assert result.result["context"]["_ghoul_claw_save_outcome"] == "exempt"
    target = state[f"{SRD}.entities"]["ghoul_1"]
    assert target["hp"] == 18 and target["creature_type"] == "undead"
    assert "paralyzed" not in target["conditions"] and target["created_form_id"] == "ghoul"


@pytest.mark.parametrize(
    "native_paralyzed", [False, True, "sibling", "dead_attacker", "reused_slot"]
)
def test_created_ghoul_claw_expires_at_its_targets_next_real_end_and_keeps_native_paralysis(
    created_undead_owner,
    monkeypatch,
    native_paralyzed,
):
    state, data = _ghoul_bite_case()
    data["species"] = "human"
    _command_ghoul_attack_turn(state, data)
    if native_paralyzed is True:
        _run(
            "apply_srd_condition",
            state,
            participant_data=data,
            target="hero",
            condition="paralyzed",
        )
    d20 = []

    class ClawDice:
        def randint(self, low, high):
            assert low == 1 and high in (4, 20)
            if high == 4:
                return 2
            d20.append(high)
            return 18 if len(d20) <= (2 if native_paralyzed is True else 1) else 1

    monkeypatch.setattr(dice, "_rng", ClawDice())
    result = _ghoul_claw(state, data)
    source_id = result.result["context"]["_ghoul_claw_source_id"]
    assert source_id is not None
    sibling_id = None
    if native_paralyzed in ("sibling", "reused_slot"):
        if native_paralyzed == "reused_slot":
            _run(
                "retire_srd_timed_condition_source",
                state,
                lasting_state="state",
                source_id=source_id,
            )
        sibling_id = _run(
            "install_srd_timed_condition_source",
            state,
            participant_data=data,
            target_state="hero",
            lasting_state="state",
            caster_state="other",
            spell="hold_person",
            condition="paralyzed",
            duration_rounds=10,
            repeat_save_timing="none",
            save_dc=10,
            save_ability="wisdom",
        ).result["context"]["_timed_condition_source_id"]
        if native_paralyzed == "reused_slot":
            assert sibling_id == source_id
            source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
            assert source["native_body_source_sequence"] != source["sequence"]
    if native_paralyzed == "dead_attacker":
        _run(
            "apply_srd_damage",
            state,
            target="ghoul_0",
            amount=22,
            adjustment=0,
            damage_type="radiant",
            critical_hit=False,
        )
        assert state[f"{SRD}.entities"]["ghoul_0"]["is_alive"] is False
    hero = state[f"{SRD}.entities"]["hero"]
    due = (hero.get("turns_started") or 0) + 1
    assert "paralyzed" in hero["conditions"]
    for _ in range(3):
        _run("end_combat_turn", state)
        assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is True
        _run("advance_group_combat_encounter_turn", state)
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=actor, participant_data=data)
        if actor == "hero":
            break
    else:
        pytest.fail("The actual target did not reach its next real turn.")
    assert state[f"{SRD}.entities"]["hero"]["turns_started"] == due
    assert "paralyzed" in state[f"{SRD}.entities"]["hero"]["conditions"]
    _run("end_combat_turn", state)
    hero = state[f"{SRD}.entities"]["hero"]
    assert ("paralyzed" in hero["conditions"]) is (
        native_paralyzed in (True, "sibling", "reused_slot")
    )
    assert hero["has_ghoul_claw_sources"] is False and hero["ghoul_claw_source_ids"] == []
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is (
        native_paralyzed == "reused_slot"
    )
    assert state[f"{SRD}.state"]["timed_condition_free_source_ids"].count(source_id) == (
        0 if native_paralyzed == "reused_slot" else 1
    )
    if sibling_id is not None:
        assert state[f"{SRD}.state"]["timed_condition_sources"][sibling_id]["installed"] is True


@pytest.mark.parametrize(
    "problem", ["unknown", "replacement", "transfer", "missing_species", "routine"]
)
def test_created_ghoul_claw_rejects_unowned_body_species_or_routine_before_rolling(
    created_undead_owner,
    monkeypatch,
    problem,
):
    state, data = _ghoul_bite_case()
    data["species"] = "human"
    _command_ghoul_attack_turn(state, data)
    choices = {}
    if problem == "unknown":
        choices.update(target="other", attack_distance=5)
    elif problem == "replacement":
        state[f"{SRD}.entities"]["hero"]["form_statistics_replaced"] = True
    elif problem == "transfer":
        state[f"{SRD}.entities"]["hero"]["current_body_source_id"] = "actual_clone_body"
    elif problem == "missing_species":
        data.pop("species")
    else:
        choices["use_multiattack"] = True

    class NoRoll:
        def randint(self, _low, _high):
            pytest.fail("Unsupported Claw eligibility reached a real roll.")

    monkeypatch.setattr(dice, "_rng", NoRoll())
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _ghoul_claw(state, data, **choices)
    assert state == before


def test_created_ghoul_claw_miss_spends_the_original_action_without_damage_or_save(
    created_undead_owner,
    monkeypatch,
):
    state, data = _ghoul_bite_case()
    data["species"] = "human"
    _command_ghoul_attack_turn(state, data)
    rolls = []

    class ClawDice:
        def randint(self, low, high):
            assert low == 1 and high == 20
            rolls.append(high)
            assert len(rolls) == 1, "A genuine Claw miss cannot roll damage or its rider save."
            return 1

    monkeypatch.setattr(dice, "_rng", ClawDice())
    before = deepcopy(state[f"{SRD}.entities"]["hero"])
    result = _ghoul_claw(state, data)
    assert result.result["context"]["_attack_outcome"] == "miss"
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == before["hp"] and hero["temp_hp"] == before["temp_hp"]
    assert hero["conditions"] == before["conditions"] and not hero.get("ghoul_claw_source_ids")
    assert state[f"{SRD}.combat"]["action_available"] is False


def test_public_created_ghoul_saved_critical_bite_retains_real_packet_and_retry(
    tmp_path,
    monkeypatch,
):
    import sylvia_gm.application.package_runtime as package
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_extended_casting import _begin

    def case(_spell):
        state, data, parameters = _created_undead_case()
        _admit_zombie_slam_target(state)
        return state, data, parameters

    packets, rolls = [], []
    original_execute = package.execute_tool

    def observed(name, parameters, *args, **kwargs):
        result = original_execute(name, parameters, *args, **kwargs)
        if name == "apply_srd_qualifying_composite_damage":
            packets.append((deepcopy(parameters), deepcopy(result.context)))
        return result

    class BiteDice:
        def randint(self, low, high):
            assert low == 1 and high in (6, 20)
            rolls.append(high)
            return 20 if high == 20 else 3 if rolls.count(6) <= 2 else 4

    monkeypatch.setattr(public, "_spell_case", case)
    monkeypatch.setattr(package, "execute_tool", observed)
    monkeypatch.setattr(dice, "_rng", BiteDice())
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "ghoul-bite.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "create_undead",
            target_overrides={"hp": 0, "max_hp": 20},
        )
        prototype = ActionCommand(
            campaign_id="casting",
            action_id="ghoul-night",
            idempotency_key="ghoul-night",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="record_srd_map_time_of_day",
            parameters={"plane": "material", "phase": "night"},
        )
        mechanics = StatelessMcpMechanics(owner)

        def act(identity, name, **choices):
            action = replace(
                prototype,
                action_id=identity,
                idempotency_key=identity,
                tool_name=name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            receipt = mechanics.execute_action(action)
            assert receipt.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == receipt
            return action, receipt

        act("ghoul-night", "record_srd_map_time_of_day", plane="material", phase="night")
        interrupted_begin, interrupted_begin_receipt = act(
            "ghoul-cast-begin", "begin_srd_spell_cast", **_begin(parameters)
        )
        interrupted_end, interrupted_end_receipt = act(
            "ghoul-cast-dismiss", "end_concentration", caster_state="hero"
        )
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["extended_casting"] is None and hero["resources"]["spell_slot_6"] == 3
        assert "ghoul_0" not in saved.state[f"{SRD}.entities"]
        assert saved.state[f"{SRD}.entities"]["guard"]["body_available"] is True
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(interrupted_begin) == interrupted_begin_receipt
        assert mechanics.execute_action(interrupted_end) == interrupted_end_receipt
        assert owner.snapshot("casting").state == saved.state
        act("ghoul-cast-restart", "begin_srd_spell_cast", **_begin(parameters))
        act(
            "ghoul-cast-progress",
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        act("ghoul-current-night", "record_srd_map_time_of_day", plane="material", phase="night")
        cast, cast_receipt = act("ghoul-cast-complete", "cast_spell_outside_combat", **parameters)
        command, command_receipt = act(
            "ghoul-order",
            "command_srd_animated_undead",
            caster_state="hero",
            actor_ids=["ghoul_0"],
            order_kind="specific",
            action="attack",
        )
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(cast) == cast_receipt
        assert owner.execute_action(command) == command_receipt
        act(
            "ghoul-initiative",
            "start_group_combat_encounter",
            combatants=["hero", "ghoul_0", "other"],
        )
        for index in range(3):
            actor = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            act(f"ghoul-turn-{index}", "begin_combat_turn")
            if actor == "ghoul_0":
                break
            act(f"ghoul-end-{index}", "end_combat_turn")
            act(f"ghoul-next-{index}", "advance_group_combat_encounter_turn")
        else:
            pytest.fail("The actual Ghoul never reached its independent turn.")
        begun, begin_receipt = act(
            "ghoul-bite",
            "begin_srd_creature_attack",
            target="other",
            attack_name="Bite",
            attack_modifier=4,
            attack_mode="melee",
            damage_expression="d6",
            damage_modifier=2,
            damage_type="piercing",
            normal_range=5,
            long_range=5,
            attack_distance=5,
            use_multiattack=True,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
            advantage=False,
            disadvantage=False,
            attack_uses_weapon=False,
        )
        pending = begin_receipt.result["context"]["_pending_action_id"]
        saved = owner.snapshot("casting")
        actual_roll = saved.state[f"{SRD}.state"]["pending_srd_actions"][pending]["attack_roll"]
        d20_count = rolls.count(20)
        assert saved.state[f"{SRD}.entities"]["other"]["hp"] == 1000
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(begun) == begin_receipt
        completed, receipt = act(
            "ghoul-bite-complete", "complete_srd_pending_action", pending_action_id=pending
        )
        context = receipt.result["context"]
        assert context["_attack_roll"] == actual_roll
        assert context["_attack_outcome"] == "critical_hit"
        assert rolls.count(20) == d20_count and rolls.count(6) == 4
        assert len(packets) == 1
        packet, received = packets[0]
        assert packet["source_state_id"] == "ghoul_0" and packet["critical_hit"] is True
        assert packet["component_1_amount"] == packet["component_2_amount"] == 8
        assert packet["component_1_damage_type"] == "piercing"
        assert packet["component_2_damage_type"] == "necrotic"
        assert received["_component_1_effective_damage"] == 8
        assert received["_component_2_effective_damage"] == 8
        assert context["_damage_applied"] == 16
        assert {"Attack roll", "Damage"} <= {
            r["label"] for r in receipt.result["result_card"]["details"]
        }
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["other"]["hp"] == 984
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
        assert saved.state[f"{SRD}.combat"]["attacks_remaining"] == 1
        assert mechanics.execute_action(completed) == receipt and len(packets) == 1
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(completed) == receipt and len(packets) == 1
        assert owner.snapshot("casting").state == saved.state


def test_public_created_ghoul_saved_claw_preserves_actual_save_source_and_next_turn_expiry(
    tmp_path,
    monkeypatch,
):
    import sylvia_gm.application.package_runtime as package
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_extended_casting import _begin

    saves, rolls = [], []
    phase = {"completion": False}
    execute = package.execute_tool

    def observed(name, parameters, *args, **kwargs):
        result = execute(name, parameters, *args, **kwargs)
        if name == "d20_test" and parameters.get("kind") == "saving_throw":
            saves.append((deepcopy(parameters), deepcopy(result.context)))
        return result

    class ClawDice:
        def randint(self, low, high):
            assert low == 1 and high in (4, 20)
            rolls.append(high)
            return 2 if high == 4 else 1 if phase["completion"] else 18

    monkeypatch.setattr(public, "_spell_case", lambda _spell: _created_undead_case())
    monkeypatch.setattr(package, "execute_tool", observed)
    monkeypatch.setattr(dice, "_rng", ClawDice())
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "ghoul-claw.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "create_undead",
            target_overrides={"hp": 0, "max_hp": 20},
        )
        prototype = ActionCommand(
            campaign_id="casting",
            action_id="claw-night",
            idempotency_key="claw-night",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="record_srd_map_time_of_day",
            parameters={"plane": "material", "phase": "night"},
        )
        mechanics = StatelessMcpMechanics(owner)

        def act(identity, name, **choices):
            action = replace(
                prototype,
                action_id=identity,
                idempotency_key=identity,
                tool_name=name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            receipt = mechanics.execute_action(action)
            assert receipt.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == receipt
            return action, receipt

        act("claw-night", "record_srd_map_time_of_day", plane="material", phase="night")
        act("claw-cast-start", "begin_srd_spell_cast", **_begin(parameters))
        act(
            "claw-cast-progress",
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        act("claw-current-night", "record_srd_map_time_of_day", plane="material", phase="night")
        cast, cast_receipt = act("claw-create", "cast_spell_outside_combat", **parameters)
        command, command_receipt = act(
            "claw-command",
            "command_srd_animated_undead",
            caster_state="hero",
            actor_ids=["ghoul_0"],
            order_kind="specific",
            action="attack",
        )
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(cast) == cast_receipt
        assert mechanics.execute_action(command) == command_receipt
        act(
            "claw-initiative",
            "start_group_combat_encounter",
            combatants=["hero", "ghoul_0", "other"],
        )
        for index in range(3):
            actor = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            act(f"claw-turn-{index}", "begin_combat_turn")
            if actor == "ghoul_0":
                break
            act(f"claw-end-{index}", "end_combat_turn")
            act(f"claw-next-{index}", "advance_group_combat_encounter_turn")
        else:
            pytest.fail("The actual Ghoul never reached its independent turn.")
        begun, begin_receipt = act(
            "claw-attack",
            "begin_srd_creature_attack",
            target="hero",
            attack_name="Claw",
            attack_modifier=4,
            attack_mode="melee",
            damage_expression="d4",
            damage_modifier=2,
            damage_type="slashing",
            normal_range=5,
            long_range=5,
            attack_distance=3,
            use_multiattack=False,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
            advantage=False,
            disadvantage=False,
            attack_uses_weapon=False,
        )
        pending = begin_receipt.result["context"]["_pending_action_id"]
        saved = owner.snapshot("casting")
        attack_roll = saved.state[f"{SRD}.state"]["pending_srd_actions"][pending]["attack_roll"]
        hp = saved.state[f"{SRD}.entities"]["hero"]["hp"]
        count = rolls.count(20)
        assert not saves and rolls.count(4) == 0
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(begun) == begin_receipt
        phase["completion"] = True
        completed, receipt = act(
            "claw-complete", "complete_srd_pending_action", pending_action_id=pending
        )
        assert receipt.result["context"]["_attack_roll"] == attack_roll
        assert receipt.result["context"]["_attack_outcome"] == "hit"
        assert rolls.count(20) == count + 1 and rolls.count(4) == 1 and len(saves) == 1
        assert saves[0][0]["actor"] == "hero" and saves[0][0]["target_number"] == 10
        assert saves[0][0]["ability"] == "constitution"
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        (source_id,) = hero["ghoul_claw_source_ids"]
        source = saved.state[f"{SRD}.state"]["timed_condition_sources"][source_id]
        assert hero["hp"] == hp - 4 and "paralyzed" in hero["conditions"]
        assert source["target_end_turn"] == hero["turns_started"] + 1
        assert source["native_body_source"] == "ghoul_0" and source["native_attack"] == "Claw"
        assert source["native_body_source_sequence"] == source["sequence"]
        assert source["ongoing_duration"] is False and source["dispel_ongoing"] is False
        assert hero["resources"]["spell_slot_6"] == 2
        assert {"Attack roll", "Damage"} <= {
            r["label"] for r in receipt.result["result_card"]["details"]
        }
        assert mechanics.execute_action(completed) == receipt and len(saves) == 1
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(completed) == receipt and len(saves) == 1
        for index in range(3):
            act(f"claw-expiry-end-{index}", "end_combat_turn")
            assert (
                owner.snapshot("casting").state[f"{SRD}.state"]["timed_condition_sources"][
                    source_id
                ]["installed"]
                is True
            )
            act(f"claw-expiry-next-{index}", "advance_group_combat_encounter_turn")
            actor = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            act(f"claw-expiry-begin-{index}", "begin_combat_turn")
            if actor == "hero":
                break
        else:
            pytest.fail("The actual target never reached its next turn.")
        assert (
            "paralyzed" in owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["conditions"]
        )
        ended, end_receipt = act("claw-target-end", "end_combat_turn")
        saved = owner.snapshot("casting")
        assert "paralyzed" not in saved.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert (
            saved.state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is False
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(ended) == end_receipt and len(saves) == 1
        assert owner.execute_action(completed) == receipt
        assert owner.snapshot("casting").state == saved.state


def test_public_created_undead_saves_world_night_paid_cast_and_actual_ghoul_command_after_reopen(
    tmp_path,
    monkeypatch,
):
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_extended_casting import _begin

    monkeypatch.setattr(public, "_spell_case", lambda _spell: _created_undead_case())
    database = tmp_path / "created-undead.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "create_undead",
            target_overrides={"hp": 0, "max_hp": 20},
        )
        prototype = ActionCommand(
            campaign_id="casting",
            action_id="world-night",
            idempotency_key="world-night",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="record_srd_map_time_of_day",
            parameters={"plane": "material", "phase": "night"},
        )
        mechanics = StatelessMcpMechanics(owner)

        def act(identity, name, **choices):
            action = replace(
                prototype,
                action_id=identity,
                idempotency_key=identity,
                tool_name=name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            receipt = mechanics.execute_action(action)
            assert receipt.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == receipt
            return action, receipt

        fact, fact_receipt = act(
            "world-night", "record_srd_map_time_of_day", plane="material", phase="night"
        )
        assert fact_receipt.result["result_card"]["title"] == "Map time of day"
        begun, begin_receipt = act("undead-begin", "begin_srd_spell_cast", **_begin(parameters))
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.state"]["map_time_of_day_records"] == [
            {"map_id": "information", "plane": "material", "phase": "night", "tick": 1},
        ]
        assert "ghoul_0" not in saved.state[f"{SRD}.entities"]
        material_before = deepcopy(saved.state[f"{SRD}.entities"]["hero"]["spell_materials"])
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(fact) == fact_receipt
        assert mechanics.execute_action(begun) == begin_receipt
        act(
            "undead-continue",
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        act("world-night-current", "record_srd_map_time_of_day", plane="material", phase="night")
        chosen = parameters | {
            "created_undead_bodies": [
                {
                    "corpse_state_id": "guard",
                    "individual_id": "chosen_ghoul",
                    "appearance_height": 6,
                }
            ]
        }
        completed, cast_receipt = act("undead-complete", "cast_spell_outside_combat", **chosen)
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["chosen_ghoul"]
        assert body["hp"] == 22 and body["armor_class"] == 12 and body["created_form_id"] == "ghoul"
        assert body["created_body_source_state_id"] == body["created_body_source_id"] == "guard"
        assert body["created_caster_state_id"] == "hero"
        assert body["created_spell_id"] == "create_undead"
        assert body["animated_control_until"] == 28821
        assert saved.state[f"{SRD}.entities"]["guard"]["body_animated_actor_id"] == "chosen_ghoul"
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
        assert saved.state[f"{SRD}.entities"]["hero"]["spell_materials"] == material_before
        assert "ghoul_0" not in saved.state[f"{SRD}.entities"]
        assert cast_receipt.result["result_card"]["title"] == "Create Undead"
        commanded, command_receipt = act(
            "undead-command",
            "command_srd_animated_undead",
            caster_state="hero",
            actor_ids=["chosen_ghoul"],
            order_kind="specific",
            action="dodge",
        )
        assert command_receipt.result["result_card"]["title"] == "Create Undead mental command"
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(completed) == cast_receipt
        assert mechanics.execute_action(commanded) == command_receipt
        act(
            "undead-initiative",
            "start_group_combat_encounter",
            combatants=["hero", "chosen_ghoul", "other"],
        )
        for index in range(3):
            actor = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            turn, turn_receipt = act(f"undead-turn-{index}", "begin_combat_turn")
            if actor == "chosen_ghoul":
                break
            act(f"undead-end-{index}", "end_combat_turn")
            act(f"undead-next-{index}", "advance_group_combat_encounter_turn")
        else:
            pytest.fail("The actual Ghoul never reached its independent turn.")
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["chosen_ghoul"]
        assert body["dodging"] is True and body["animated_order"] is None
        assert body["animated_turn_order"]["selected_actor_ids"] == ["chosen_ghoul"]
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert any(
            r["label"] == "Create Undead" and r["value"] == "Commanded Dodge"
            for r in turn_receipt.result["result_card"]["details"]
        )
        order = next(
            r for r in saved.state[f"{SRD}.combat"]["order"] if r["entity_id"] == "chosen_ghoul"
        )
        assert order["modifier"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(turn) == turn_receipt
        assert owner.execute_action(completed) == cast_receipt
        assert owner.snapshot("casting").state == saved.state


@pytest.fixture(scope="module")
def _world_time_runtime():
    from scripts.srd_full.srd_divination_spells import _normalize
    from scripts.srd_full.srd_spell_summoning import _created_world_time_tools
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
    from scripts.srd_full.srd_timed_senses import _replace_tools

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    changed = _created_world_time_tools()
    _normalize(changed)
    source = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    text = _replace_tools(source, changed)
    assert not validate_ruleset(text)
    runtime = _load_runtime(text)
    validate_content_shape("package_runtime", runtime, file="created-world-time-fragment")
    return runtime, text.encode()


@pytest.fixture
def world_time_owner(monkeypatch, _world_time_runtime):
    from functools import lru_cache

    import tests.srd_full.test_srd_action_budget as budget
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    runtime, encoded = _world_time_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="d" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    budget._tool_schema(runtime["tools"][name], tool_name=name)
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    return runtime


def _world_time_case():
    state, data, _ = _information_case("find_familiar")
    _free(state)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    return state, data


def test_created_world_time_retains_actual_map_plane_clock_and_replaces_only_its_fact(
    world_time_owner,
):
    state, _ = _world_time_case()
    result = _run("record_srd_map_time_of_day", state, plane="material", phase="night")
    saved = state[f"{SRD}.state"]
    assert saved["map_time_of_day_records"] == [
        {"map_id": "information", "plane": "material", "phase": "night", "tick": 1},
    ]
    assert result.result["result_card"]["title"] == "Map time of day"
    phase = _run("require_srd_created_undead_night", state, caster_state="hero")
    assert phase.result["context"]["_world_time_tick"] == 1
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["metadata"]["plane"] = "ethereal"
    _run("record_srd_map_time_of_day", state, plane="ethereal", phase="day")
    _run("record_srd_map_time_of_day", state, plane="material", phase="day")
    records = state[f"{SRD}.state"]["map_time_of_day_records"]
    assert len(records) == 2 and all(row["phase"] == "day" for row in records)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="only at night"):
        _run("require_srd_created_undead_night", state, caster_state="hero")
    assert state == before


@pytest.mark.parametrize("changed", ["clock", "plane", "map", "absent", "darkness"])
def test_created_world_time_rejects_stale_or_foreign_facts_and_darkness_inference(
    world_time_owner,
    changed,
):
    state, _ = _world_time_case()
    if changed != "absent":
        _run(
            "record_srd_map_time_of_day",
            state,
            plane="material",
            phase="day" if changed == "darkness" else "night",
        )
    saved = state[f"{SRD}.state"]
    if changed == "clock":
        _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    elif changed == "plane":
        saved["spatial_scene"]["entities"][0]["metadata"]["plane"] = "ethereal"
    elif changed == "map":
        saved["spatial_scene"]["map"]["id"] = "second_map"
        for row in saved["spatial_scene"]["entities"]:
            row["map_id"] = "second_map"
    elif changed == "darkness":
        saved["spatial_scene"]["entities"][0]["metadata"]["lighting"] = "magical_darkness"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("require_srd_created_undead_night", state, caster_state="hero")
    assert state == before


def test_created_world_time_fact_cannot_admit_a_missing_plane_or_caller_timestamp(world_time_owner):
    state, _ = _world_time_case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actually present"):
        _run("record_srd_map_time_of_day", state, plane="other_plane", phase="night")
    assert state == before
    parameters = world_time_owner["tools"]["record_srd_map_time_of_day"]["parameters"]
    assert {p["name"] for p in parameters} == {"plane", "phase", "lasting_state"}
    state[f"{SRD}.state"]["lasting_tick"] = None
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Initialize the actual world clock"):
        _run("record_srd_map_time_of_day", state, plane="material", phase="night")
    assert state == before


@pytest.fixture(scope="module")
def _insect_runtime():
    """Compose the current genuine insect leaf, then let the reaction owner clone that leaf."""
    import json

    from scripts.srd_full.build_srd_5_2_1_spells import (
        SPELL_DEFINITIONS_END,
        SPELL_DEFINITIONS_START,
        SPELL_SELECTOR_END,
        SPELL_SELECTOR_START,
        _replace_generated_block,
    )
    from scripts.srd_full.srd_reaction_spells import extend_runtime as compose_reactions
    from scripts.srd_full.srd_spell_summoning import (
        GIANT_INSECT_PROFILE,
        bind_insect_runtime_consumers,
        extend_runtime,
    )
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
    from scripts.srd_full.srd_timed_senses import _replace_tools

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    text = extend_runtime((ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text())
    text = bind_insect_runtime_consumers(text)
    assert bind_insect_runtime_consumers(text) == text
    text = compose_reactions(text)
    current = _load_runtime(text)
    definitions = current["definitions"]["spells"]
    previous = set(definitions)
    changed = {}
    for name, tool in current["tools"].items():
        for parameter in tool["parameters"]:
            if parameter.get("type") == "enum" and set(parameter["enum_values"]) == previous:
                parameter["enum_values"] = sorted(previous | {"giant_insect"})
                changed[name] = tool
    text = _replace_tools(text, changed)
    definitions["giant_insect"] = GIANT_INSECT_PROFILE
    text = _replace_generated_block(
        text,
        SPELL_DEFINITIONS_START,
        SPELL_DEFINITIONS_END,
        [
            "    " + spell + ": " + json.dumps(row, ensure_ascii=False)
            for spell, row in sorted(definitions.items())
        ],
    )
    text = _replace_generated_block(
        text,
        SPELL_SELECTOR_START,
        SPELL_SELECTOR_END,
        ["          - " + spell for spell in sorted(definitions)],
    )
    assert not validate_ruleset(text)
    runtime = _load_runtime(text)
    validate_content_shape("package_runtime", runtime, file="insect-owning-fragment")
    return runtime, text.encode()


@pytest.fixture
def insect_owner(monkeypatch, _insect_runtime):
    from functools import lru_cache

    import tests.srd_full.test_srd_action_budget as budget
    import tests.srd_full.test_srd_spell_result_cards as cards
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    runtime, encoded = _insect_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="d" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    budget._tool_schema(runtime["tools"][name], tool_name=name)
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    return runtime


def _insect_case(form="centipede", level=4, **choices):
    state, data, parameters = _information_case("giant_insect")
    _free(state)
    parameters.pop("target")
    parameters.update(
        summon_id="insect_one",
        insect_form=form,
        slot_resource=f"spell_slot_{level}",
        created_actor_position=[10, 0, 0],
        created_actor_height=8,
    )
    parameters.update(choices)
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = (
        data["abilities"]["dexterity"] - 10
    ) // 2
    state[f"{SRD}.entities"]["guard"].update(
        hp=1000,
        max_hp=1000,
        temp_hp=0,
        initiative_modifier=0,
    )
    return state, data, parameters


def _insect_turn(state, *, command="attack"):
    if command is not None:
        _run(
            "command_srd_summoned_creature",
            state,
            caster_state="hero",
            summon_state="insect_one",
            command=command,
        )
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        group_order=True,
        turn_active=False,
        current_actor="insect_one",
        active_actor=None,
        phase="awaiting_turn_start",
        order=[{"entity_id": "hero"}, {"entity_id": "insect_one"}, {"entity_id": "guard"}],
        turn_index=1,
        round=1,
        lasting_epoch_round=1,
        lasting_epoch_tick=0,
    )
    return _run("begin_combat_turn", state, actor="insect_one")


def _insect_jab(state, *, multiattack=False, **choices):
    insect = state[f"{SRD}.entities"]["insect_one"]
    return _run(
        "resolve_adjudicated_creature_attack",
        state,
        **(
            {
                "actor": "insect_one",
                "target": "guard",
                "attack_name": "Poison Jab",
                "attack_modifier": insect["summon_attack_bonus"],
                "attack_mode": "melee",
                "damage_expression": "d6",
                "damage_modifier": 3 + insect["summon_spell_level"],
                "damage_type": "piercing",
                "normal_range": 10,
                "long_range": 10,
                "attack_distance": 5,
                "actor_can_see_target": True,
                "target_can_see_actor": True,
                "target_is_charmer": False,
                "fear_source_in_sight": False,
                "advantage": False,
                "disadvantage": False,
                "use_multiattack": multiattack,
                "attack_uses_weapon": False,
            }
            | choices
        ),
    )


@pytest.mark.parametrize("form,level", [("centipede", 4), ("spider", 5), ("wasp", 9)])
def test_insect_creation_retains_exact_source_body_slot_and_concentration(
    insect_owner,
    form,
    level,
):
    from scripts.srd_full.srd_spell_summoning import _giant_insect_source_profile

    state, data, parameters = _insect_case(form, level)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    insect = state[f"{SRD}.entities"]["insect_one"]
    caster = state[f"{SRD}.entities"]["hero"]
    source = _giant_insect_source_profile()
    assert insect["hp"] == insect["max_hp"] == 30 + 10 * (level - 4)
    assert insect["armor_class"] == 11 + level
    assert insect["abilities"] == insect["ability_scores"] == source["ability_scores"]
    assert insect["saving_throw_modifiers"] == source["saving_throw_modifiers"]
    assert insect["challenge_rating_eighths"] is None
    assert insect["creature_type"] == "beast"
    assert insect["speed"] == insect["climbing_speed"] == 40
    assert insect["flying_speed"] == (40 if form == "wasp" else 0)
    assert insect["initiative_modifier"] == 1
    assert insect["created_insect_attacks_per_action"] == level // 2
    assert insect["created_source_block_sha256"] == source["source_stat_block_sha256"]
    assert insect["created_source_pages"] == source["source_pages"]
    assert insect["summon_understood_languages"] == ["common"]
    assert insect["languages"] == []
    assert "summon_breath_name" not in insect and "multiattack" not in insect
    assert caster["concentration_spell_id"] == "giant_insect"
    assert caster["concentration_target_state_id"] == "insect_one"
    assert caster["concentration_expires_tick"] - caster["concentration_started_tick"] == 200
    assert caster["resources"][f"spell_slot_{level}"] == 2


@pytest.mark.parametrize("ending", ["concentration", "clock", "damage"])
def test_insect_ends_actual_actor_and_scene_with_its_source(insect_owner, ending):
    state, data, parameters = _insect_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    if ending == "concentration":
        _run("end_concentration", state, caster_state="hero")
    elif ending == "clock":
        _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    else:
        _run(
            "apply_srd_damage",
            state,
            target="insect_one",
            amount=100,
            adjustment=0,
            damage_type="radiant",
        )
    insect = state[f"{SRD}.entities"]["insect_one"]
    assert insect["summon_active"] is insect["created_active"] is False
    assert insect["created_presence"] == "gone"
    assert insect["hp"] == 0
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.state"]["srd_summon_caster_ids"] == []
    assert (
        next(
            body
            for body in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if body["id"] == "insect_one"
        )["active"]
        is False
    )


@pytest.mark.parametrize("critical", [False, True])
def test_insect_poison_jab_uses_real_critical_typed_damage_and_shared_action(
    monkeypatch,
    insect_owner,
    critical,
):
    state, data, parameters = _insect_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    state[f"{SRD}.entities"]["guard"]["damage_immunities"] = ["poison"]
    _insect_turn(state)
    monkeypatch.setattr(dice, "_rng", Random(5 if critical else 1))
    result = _insect_jab(state, multiattack=True).result["context"]
    assert result["_critical_hit"] is critical
    assert len(result["_insect_poison_damage_roll"]["results"]) == (2 if critical else 1)
    assert result["_component_2_effective_damage"] == 0
    assert result["_damage_applied"] == result["_component_1_effective_damage"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - result["_damage_applied"]
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 1
    _insect_jab(state, multiattack=True)
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _insect_jab(state, multiattack=True)
    assert state == before


@pytest.mark.parametrize("blocked", ["wrong_die", "wrong_range", "far", "web"])
def test_insect_native_attack_rejects_unowned_or_supplied_outcomes_atomically(
    insect_owner,
    blocked,
):
    state, data, parameters = _insect_case("spider")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _insect_turn(state)
    choices = {
        "wrong_die": {"damage_expression": "d12"},
        "wrong_range": {"normal_range": 20, "long_range": 20},
        "far": {"attack_distance": 1},
        "web": {"attack_name": "Web Bolt"},
    }[blocked]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _insect_jab(state, **choices)
    assert state == before


@pytest.mark.parametrize("disabled", ["none", "speed", "incapacitated"])
def test_insect_default_dodge_uses_actual_benefit_and_action_checks(insect_owner, disabled):
    state, data, parameters = _insect_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    insect = state[f"{SRD}.entities"]["insect_one"]
    if disabled == "speed":
        insect["speed"] = 0
    elif disabled == "incapacitated":
        insect["conditions"] = ["incapacitated"]
    result = _insect_turn(state, command=None).result["context"]
    insect = state[f"{SRD}.entities"]["insect_one"]
    assert insect.get("dodging", False) is (disabled == "none")
    assert result["_insect_dodge_taken"] is (disabled != "incapacitated")
    assert result["_insect_dodge_benefit"] is (disabled == "none")


@pytest.mark.parametrize(
    "position,lighting", [([61, 0, 0], "bright"), ([20, 0, 0], "bright"), ([10, 0, 0], "darkness")]
)
def test_insect_placement_rejects_actual_range_occupancy_and_sight_without_cost(
    insect_owner,
    position,
    lighting,
):
    state, data, parameters = _insect_case(
        created_actor_position=position, created_actor_lighting=lighting
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def test_insect_recasting_ends_previous_source_before_same_space_replacement(insect_owner):
    state, data, parameters = _insect_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"summon_id": "insect_two", "insect_form": "wasp"}),
    )
    entities = state[f"{SRD}.entities"]
    assert entities["insect_one"]["created_active"] is False
    assert entities["insect_two"]["created_active"] is True
    assert entities["hero"]["concentration_target_state_id"] == "insect_two"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def test_insect_poison_jab_keeps_both_types_and_one_concentration_save(monkeypatch, insect_owner):
    import sylvia_gm.application.package_runtime as package

    state, data, parameters = _insect_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    caster = state[f"{SRD}.entities"]["hero"]
    caster.update(hp=1000, max_hp=1000, temp_hp=5, damage_resistances=["piercing"])
    _insect_turn(state)
    concentration_saves = []
    original_execute = package.execute_tool

    def observed(name, parameters, *args, **kwargs):
        if (
            name == "d20_test"
            and parameters.get("kind") == "saving_throw"
            and parameters.get("ability") == "constitution"
        ):
            concentration_saves.append(parameters)
        return original_execute(name, parameters, *args, **kwargs)

    monkeypatch.setattr(package, "execute_tool", observed)
    monkeypatch.setattr(dice, "_rng", Random(0))
    result = _insect_jab(state, target="hero").result["context"]
    assert result["_attack_outcome"] != "miss"
    assert result["_component_1_effective_damage"] > 0
    assert result["_component_2_effective_damage"] > 0
    assert (
        result["_damage_applied"]
        == result["_component_1_effective_damage"] + result["_component_2_effective_damage"]
    )
    assert result["_damage_absorbed"] == 5
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 1000 - result["_damage_applied"] + 5
    from scripts.srd_full.srd_spell_summoning import _walk

    helper = insect_owner["tools"]["apply_srd_insect_poison_jab_damage"]
    assert [
        e["call"] for e in _walk(helper["effects"]) if e.get("call", "").startswith("apply_srd_")
    ] == ["apply_srd_qualifying_composite_damage"]
    assert len(result["_insect_poison_damage_roll"]["results"]) == 1
    assert len(concentration_saves) == 1


def test_insect_actual_group_initiative_places_it_immediately_after_its_caster(insect_owner):
    state, data, parameters = _insect_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "insect_one", "guard"],
    )
    order = state[f"{SRD}.combat"]["order"]
    caster_index = next(i for i, row in enumerate(order) if row["entity_id"] == "hero")
    assert order[caster_index + 1]["entity_id"] == "insect_one"
    assert state[f"{SRD}.entities"]["insect_one"]["initiative_modifier"] == 1


def test_public_insect_cast_reopens_retries_and_keeps_the_real_pending_jab(tmp_path, monkeypatch):
    import sylvia_gm.application.package_runtime as package
    from tests import test_srd_public_spell_casting as public

    monkeypatch.setattr(public, "_spell_case", lambda _spell: _insect_case("wasp", 6))
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    packets, completions = [], []
    original_execute = package.execute_tool

    def observed(name, parameters, *args, **kwargs):
        result = original_execute(name, parameters, *args, **kwargs)
        if name == "apply_srd_insect_poison_jab_damage":
            packets.append((deepcopy(parameters), deepcopy(result.context)))
        if name == "complete_srd_creature_attack":
            completions.append(deepcopy(result.context))
        return result

    monkeypatch.setattr(package, "execute_tool", observed)
    database = tmp_path / "insect.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "giant_insect",
            target_overrides={"hp": 1000, "max_hp": 1000},
        )
        prototype = ActionCommand(
            campaign_id="casting",
            action_id="insect-cast",
            idempotency_key="insect-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)

        def act(identity, name, **choices):
            action = replace(
                prototype,
                action_id=identity,
                idempotency_key=identity,
                tool_name=name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            result = mechanics.execute_action(action)
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == result
            return action, result

        cast, cast_receipt = act("insect-cast", "cast_spell_outside_combat", **parameters)
        act(
            "insect-command",
            "command_srd_summoned_creature",
            caster_state="hero",
            summon_state="insect_one",
            command="attack",
        )
        saved = owner.snapshot("casting")
        insect = saved.state[f"{SRD}.entities"]["insect_one"]
        assert insect["hp"] == 50 and insect["armor_class"] == 17
        assert insect["flying_speed"] == 40 and insect["challenge_rating_eighths"] is None
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(cast) == cast_receipt
        act(
            "insect-initiative",
            "start_group_combat_encounter",
            combatants=["hero", "insect_one", "guard"],
        )
        for index in range(4):
            actor = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            act(f"insect-turn-{index}", "begin_combat_turn")
            if actor == "insect_one":
                break
            act(f"insect-end-{index}", "end_combat_turn")
            act(f"insect-next-{index}", "advance_group_combat_encounter_turn")
        else:
            pytest.fail("The actual created insect did not get its caster-linked turn.")
        insect = owner.snapshot("casting").state[f"{SRD}.entities"]["insect_one"]
        begun, begin_receipt = act(
            "insect-jab",
            "begin_srd_creature_attack",
            target="guard",
            attack_name="Poison Jab",
            attack_modifier=insect["summon_attack_bonus"],
            attack_mode="melee",
            damage_expression="d6",
            damage_modifier=9,
            damage_type="piercing",
            normal_range=10,
            long_range=10,
            attack_distance=5,
            use_multiattack=True,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
            advantage=False,
            disadvantage=False,
            attack_uses_weapon=False,
        )
        pending = begin_receipt.result["context"]["_pending_action_id"]
        saved = owner.snapshot("casting")
        event = saved.state[f"{SRD}.state"]["pending_srd_actions"][pending]
        actual_roll = event["attack_roll"]
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(begun) == begin_receipt
        completed, receipt = act(
            "insect-hit", "complete_srd_pending_action", pending_action_id=pending
        )
        context = receipt.result["context"]
        assert context["_attack_roll"] == actual_roll
        assert context["_attack_outcome"] != "miss"
        assert len(packets) == len(completions) == 1
        packet_parameters, packet = packets[0]
        assert packet_parameters["actor_state"] == "insect_one"
        assert packet_parameters["target_state"] == "guard"
        assert packet["_component_1_effective_damage"] > 0
        assert packet["_component_2_effective_damage"] > 0
        assert len(packet["_insect_poison_damage_roll"].results) == 1
        assert (
            completions[0]["_component_1_effective_damage"]
            == packet["_component_1_effective_damage"]
        )
        assert (
            completions[0]["_component_2_effective_damage"]
            == packet["_component_2_effective_damage"]
        )
        assert context["_damage_applied"] == packet["_effective_damage"]
        assert {"Attack roll", "Damage"} <= {
            row["label"] for row in receipt.result["result_card"]["details"]
        }
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - context["_damage_applied"]
        assert mechanics.execute_action(completed) == receipt
        assert len(packets) == len(completions) == 1
        act("insect-source-end", "end_concentration", caster_state="hero")
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["insect_one"]["created_active"] is False
        assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2


@pytest.fixture(scope="module")
def _undead_runtime():
    """Compose the actual current damage/save owners, preserving every existing leaf hook."""
    from scripts.srd_full.srd_divination_spells import _normalize
    from scripts.srd_full.srd_spell_damage_consumers import extend_runtime as compose_damage
    from scripts.srd_full.srd_spell_summoning import (
        _animated_actor_hooks,
        _animated_attack_hooks,
        _animated_command_tools,
        _independent_actor_hooks,
        _undead_fortitude_tools,
        _zombie_actor_tools,
        bind_animated_undead_turn_consumers,
        bind_undead_fortitude_damage_consumers,
    )
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
    from scripts.srd_full.srd_timed_senses import _replace_tools

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    path = ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    text = compose_damage(path.read_text(encoding="utf-8"))
    runtime = _load_runtime(text)
    changed = _zombie_actor_tools(runtime["tools"]) | _undead_fortitude_tools()
    changed["record_srd_actual_death"] = _independent_actor_hooks(runtime["tools"])[
        "record_srd_actual_death"
    ]
    changed.update(_animated_actor_hooks(runtime["tools"] | changed))
    changed.update(_animated_command_tools(runtime["tools"] | changed))
    changed.update(_animated_attack_hooks(runtime["tools"] | changed))
    _normalize(changed)
    text = bind_undead_fortitude_damage_consumers(_replace_tools(text, changed))
    text = bind_animated_undead_turn_consumers(text)
    assert not validate_ruleset(text)
    validate_content_shape("package_runtime", _load_runtime(text), file="undead-owning-fragment")
    assert bind_undead_fortitude_damage_consumers(text) == text
    assert bind_animated_undead_turn_consumers(text) == text
    return _load_runtime(text), text.encode()


@pytest.fixture
def undead_owner(monkeypatch, _undead_runtime):
    from functools import lru_cache

    import tests.srd_full.test_srd_action_budget as budget
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    runtime, encoded = _undead_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="b" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    budget._tool_schema(runtime["tools"][name], tool_name=name)
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    return runtime


def _qualified_runtime_path():
    from os import environ

    return Path(
        environ.get(
            "SYLVIA_SUMMONING_QUALIFIED_RUNTIME",
            str(
                ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml",
            ),
        )
    )


@pytest.fixture(scope="module")
def _qualified_undead_runtime():
    """Use the exact final diagnostic composition, repairing only lethal Fortitude bindings."""
    from hashlib import sha256

    from scripts.srd_full.srd_spell_summoning import bind_undead_fortitude_damage_consumers
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    path = _qualified_runtime_path()
    encoded = path.read_bytes()
    if path == ROOT / ".scratch/qualified-damage-wave22/ruleset.yaml":
        assert sha256(encoded).hexdigest() == (
            "9f9ab63e3a26ee866bdf2e24ff7693e2ae0a8d542aaba276deed611c7c69fd3f"
        )
    source_text = path.read_text(encoding="utf-8")
    before = _load_runtime(source_text)
    text = bind_undead_fortitude_damage_consumers(source_text)
    runtime = _load_runtime(text)
    names = (
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "apply_srd_qualifying_damage",
        "apply_srd_qualifying_composite_damage",
    )
    for name in names:
        old = before["tools"][name]["effects"]
        new = runtime["tools"][name]["effects"]
        lethal = "target.hp == 0 and target.creature_kind == 'monster'"
        assert [e for e in old if e.get("condition") != lethal] == [
            e for e in new if e.get("condition") != lethal
        ]
        branch = next(e for e in new if e.get("condition") == lethal)
        assert "resolve_srd_created_undead_fortitude" in str(branch), (
            name,
            bind_undead_fortitude_damage_consumers.__code__.co_filename,
        )
        assert "record_srd_actual_death" in str(branch)
        assert "target.hp == 0" in str(branch["on_true"][-1])
        original = next(e for e in old if e.get("condition") == lethal)
        if "resolve_srd_created_undead_fortitude" not in str(original):
            assert branch["on_true"][-1]["on_true"] == original["on_true"]
    assert not validate_ruleset(text)
    validate_content_shape("package_runtime", runtime, file="qualified-undead-fragment")
    assert bind_undead_fortitude_damage_consumers(text) == text
    from scripts.srd_full.srd_spell_summoning import _walk
    from scripts.srd_full.srd_timed_actor_defenses import _RuntimeDumper

    selected = set(names) | {
        "prepare_srd_source_zombie",
        "advance_srd_lasting_clock",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "continue_srd_spell_cast",
    }
    pending = list(selected)
    while pending:
        name = pending.pop()
        for effect in _walk(runtime["tools"][name]["effects"]):
            called = effect.get("call")
            if called and called not in selected:
                selected.add(called)
                pending.append(called)
    runtime["tools"] = {name: tool for name, tool in runtime["tools"].items() if name in selected}
    text = yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False, width=105)
    assert not validate_ruleset(text)
    validate_content_shape("package_runtime", runtime, file="qualified-undead-transitive-fragment")
    return runtime, text.encode()


@pytest.fixture
def qualified_undead_owner(monkeypatch, _qualified_undead_runtime):
    from functools import lru_cache

    import tests.srd_full.test_srd_action_budget as budget
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    runtime, encoded = _qualified_undead_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="c" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    budget._tool_schema(runtime["tools"][name], tool_name=name)
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    return runtime


def test_qualified_scalar_inherited_fortitude_repairs_the_normalized_radiant_argument():
    from scripts.srd_full.srd_spell_summoning import _walk, bind_undead_fortitude_damage_consumers
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
    from scripts.srd_full.srd_timed_senses import _replace_tools

    text = _qualified_runtime_path().read_text(encoding="utf-8")
    runtime = _load_runtime(text)
    scalar = deepcopy(runtime["tools"]["apply_srd_qualifying_damage"])
    base = runtime["tools"]["apply_srd_damage"]
    lethal = "target.hp == 0 and target.creature_kind == 'monster'"
    inherited = next(e for e in base["effects"] if e.get("condition") == lethal)
    branch = next(e for e in scalar["effects"] if e.get("condition") == lethal)
    branch["on_true"] = deepcopy(inherited["on_true"])
    text = bind_undead_fortitude_damage_consumers(
        _replace_tools(
            text,
            {"apply_srd_qualifying_damage": scalar},
        )
    )
    repaired = _load_runtime(text)["tools"]["apply_srd_qualifying_damage"]
    branch = next(e for e in repaired["effects"] if e.get("condition") == lethal)
    callbacks = [
        e
        for e in _walk(branch["on_true"])
        if e.get("call") == "resolve_srd_created_undead_fortitude"
    ]
    assert len(callbacks) == 1
    argument = callbacks[0]["params"]["radiant_damage"].strip("{}")
    actual = next(
        e["set"]["value"]
        for e in _walk(branch["on_true"])
        if e.get("set", {}).get("field") == argument
    )
    assert actual == "damage_type == 'radiant' and _undead_base_effective_damage > 0"
    assert branch["on_true"][-1] == inherited["on_true"][-1]
    assert [e for e in repaired["effects"] if e.get("condition") != lethal] == [
        e for e in scalar["effects"] if e.get("condition") != lethal
    ]
    assert bind_undead_fortitude_damage_consumers(text) == text


@pytest.mark.parametrize("composite", [False, True])
@pytest.mark.parametrize(
    "packet",
    ["ordinary", "failed", "radiant", "critical", "immune_radiant_curse", "zero_radiant_curse"],
)
def test_final_qualified_zombie_fortitude_uses_actual_typed_receipt_before_death(
    monkeypatch,
    qualified_undead_owner,
    composite,
    packet,
):
    from tests.srd_full.test_srd_spell_damage_consumers import _grant

    state, data, parameters = _animated_case()
    _finish_familiar_cast(state, data, parameters)
    if packet.endswith("curse"):

        class FailedCurseSave:
            def randint(self, low, high):
                assert (low, high) == (1, 20)
                return 1

        monkeypatch.setattr(dice, "_rng", FailedCurseSave())
        curse = _grant(state, data, "bestow_curse")
        curse.update(
            target="zombie_one",
            targets=["zombie_one"],
            curse_mode="damage",
            slot_resource="spell_slot_5",
        )
        _run("cast_spell_outside_combat", state, participant_data=data, **curse)
        assert state[f"{SRD}.state"]["has_affliction_damage_curses"] is True
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    zombie.update(hp=5, temp_hp=0)
    if packet == "immune_radiant_curse":
        # The damage owner reads an actual saved immunity, never a packet's caller verdict.
        zombie["damage_immunities"].append("radiant")
    rolls = []

    class PacketDice:
        def randint(self, low, high):
            assert low == 1 and high in (8, 20)
            rolls.append(high)
            if high == 20:
                assert packet not in ("radiant", "critical")
                return 1 if packet == "failed" else 20
            return 8

    monkeypatch.setattr(dice, "_rng", PacketDice())
    radiant = packet in ("radiant", "immune_radiant_curse", "zero_radiant_curse")
    amount = 0 if packet == "zero_radiant_curse" else 6
    arguments = {
        "target": "zombie_one",
        "source_state_id": "hero",
        "critical_hit": packet == "critical",
        "game_damage_roll": True,
    }
    if composite:
        arguments.update(
            component_1_amount=amount,
            component_1_damage_type="radiant" if radiant else "fire",
            component_2_amount=0,
            component_2_damage_type="cold",
        )
        name = "apply_srd_qualifying_composite_damage"
    else:
        arguments.update(amount=amount, adjustment=0, damage_type="radiant" if radiant else "fire")
        name = "apply_srd_qualifying_damage"
    result = _run(name, state, **arguments).result["context"]
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    saved = zombie["last_undead_fortitude"]
    effective = 8 if packet.endswith("curse") else 6
    success = packet not in ("failed", "radiant", "critical")
    assert saved["dc"] == 5 + effective and saved["damage_taken"] == effective
    assert saved["radiant"] is (packet == "radiant")
    assert saved["critical"] is (packet == "critical")
    assert saved["success"] is success
    assert zombie["hp"] == (1 if success else 0) and zombie["is_alive"] is success
    assert (zombie["death_tick"] is None) is success
    assert ("dead" in zombie["conditions"]) is not success
    assert rolls.count(20) == (0 if packet in ("radiant", "critical") else 1)
    assert rolls.count(8) == (1 if packet.endswith("curse") else 0)
    if packet.endswith("curse"):
        assert result["_source_base_effective_damage"] == 0
        assert result["_effective_damage"] == 8
        assert zombie["last_curse_damage_roll"]["total"] == 8


def _recorded_corpse_case(spell="find_familiar"):
    state, data, _ = _information_case(spell)
    _free(state)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    corpse = state[f"{SRD}.entities"]["guard"]
    corpse.update(
        creature_kind="monster",
        creature_type="humanoid",
        living_creature_type="humanoid",
        size_rank=2,
        body_available=True,
        vital_parts_present=True,
        soul_willing=True,
    )
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=corpse["hp"] + corpse["temp_hp"],
        adjustment=0,
        damage_type="bludgeoning",
        critical_hit=False,
    )
    assert state[f"{SRD}.entities"]["guard"]["death_tick"] is not None
    return state, data


def _zombie_case():
    state, data = _recorded_corpse_case()
    _run(
        "prepare_srd_source_zombie",
        state,
        participant_data=data,
        creature_id="zombie_one",
        corpse_state="guard",
        source_caster="hero",
        other_language="draconic",
    )
    return state, data


def _animated_case(**choices):
    state, data = _recorded_corpse_case("animate_dead")
    _, _, parameters = _information_case("animate_dead")
    parameters.pop("target")
    corpse = state[f"{SRD}.entities"]["guard"]
    corpse.update(inventory=[], equipment=[])
    body = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]
    body["position"] = [5, 0, 0]
    body["metadata"]["creature_type"] = "humanoid"
    parameters.update(
        animated_mode="create",
        animated_bodies=[
            {
                "corpse_state_id": "guard",
                "individual_id": "zombie_one",
                "other_language": "draconic",
                "appearance_height": 6,
            }
        ],
    )
    parameters.update(choices)
    return state, data, parameters


def _animated_turn(state, data, *, command=None):
    """Reach the actual created NPC's turn through the installed group initiative owner."""
    if state[f"{SRD}.combat"]["encounter_active"] is not True:
        state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = (
            data["abilities"]["dexterity"] - 10
        ) // 2
        state[f"{SRD}.entities"]["other"]["initiative_modifier"] = 0
        _run(
            "start_group_combat_encounter",
            state,
            participant_data=data,
            combatants=["hero", "zombie_one", "other"],
        )
    for _ in range(6):
        actor = state[f"{SRD}.combat"]["current_actor"]
        begun = _run("begin_combat_turn", state, actor=actor)
        if actor == "hero" and command is not None:
            _run(
                "command_srd_animated_undead",
                state,
                caster_state="hero",
                actor_ids=["zombie_one"],
                **command,
            )
            command = None
        elif actor == "zombie_one" and command is None:
            return begun
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
    pytest.fail("The actual Zombie never received its independent initiative turn.")


def _animated_three_case():
    """Supply three separately recorded real deaths for the printed slot-four count."""
    state, data, params = _animated_case(slot_resource="spell_slot_4")
    entities = state[f"{SRD}.entities"]
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    original = deepcopy(entities["guard"])
    original_scene = deepcopy(scene[1])
    scene[1]["position"] = [5, -5, 0]
    for identity, y in (("corpse_two", 0), ("corpse_three", 5)):
        entities[identity] = deepcopy(original)
        entities[identity].update(hp=15, temp_hp=0, is_alive=True, death_tick=None, conditions=[])
        admitted = deepcopy(original_scene)
        admitted.update(id=identity, position=[5, y, 0])
        admitted["metadata"]["participant_ref"] = identity
        scene.append(admitted)
        _run(
            "apply_srd_damage",
            state,
            target=identity,
            amount=15,
            adjustment=0,
            damage_type="radiant",
            critical_hit=False,
        )
        entities = state[f"{SRD}.entities"]
        scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
        params["animated_bodies"].append(
            {
                "corpse_state_id": identity,
                "individual_id": identity + "_zombie",
                "other_language": "draconic",
                "appearance_height": 6,
            }
        )
    return state, data, params


def _admit_zombie_slam_target(state):
    """Place the fixture's actual separate living target in a visible Medium body."""
    from tests.srd_full.test_srd_spell_wave3_information import _entity

    state[f"{SRD}.entities"]["other"].update(
        hp=100,
        max_hp=100,
        temp_hp=0,
        creature_type="humanoid",
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    target = _entity("other", [15, 0, 0], participant_ref="other", creature_type="humanoid")
    target["footprint"]["half_extents"] = [2.5, 2.5]
    scene.append(target)


def _zombie_slam_case():
    state, data, parameters = _animated_case()
    _finish_familiar_cast(state, data, parameters)
    _admit_zombie_slam_target(state)
    return state, data


def _zombie_slam(state, **choices):
    return _run(
        "resolve_adjudicated_creature_attack",
        state,
        **(
            {
                "actor": "zombie_one",
                "target": "other",
                "attack_name": "Slam",
                "attack_modifier": 3,
                "attack_mode": "melee",
                "damage_expression": "d8",
                "damage_modifier": 1,
                "damage_type": "bludgeoning",
                "normal_range": 5,
                "long_range": 5,
                "attack_distance": 5,
                "actor_can_see_target": True,
                "target_can_see_actor": True,
                "target_is_charmer": False,
                "fear_source_in_sight": False,
                "advantage": False,
                "disadvantage": False,
                "use_multiattack": False,
                "attack_uses_weapon": False,
            }
            | choices
        ),
    )


@pytest.mark.parametrize("critical", [False, True])
def test_zombie_slam_uses_actual_source_command_single_action_roll_and_packet(
    undead_owner,
    monkeypatch,
    critical,
):
    state, data = _zombie_slam_case()
    monkeypatch.setattr(dice, "_rng", Random(0))
    _animated_turn(state, data, command={"order_kind": "specific", "action": "attack"})
    body = state[f"{SRD}.entities"]["zombie_one"]
    assert body["animated_turn_order"]["action"] == "attack"
    assert body["animated_order"] is None and body["dodging"] is False
    rolls = []

    class SlamDice:
        def randint(self, low, high):
            assert low == 1 and high in (8, 20)
            rolls.append(high)
            return (20 if critical else 18) if high == 20 else 4

    monkeypatch.setattr(dice, "_rng", SlamDice())
    result = _zombie_slam(state)
    context = result.result["context"]
    assert context["_attack_outcome"] == ("critical_hit" if critical else "hit")
    assert context["_damage_applied"] == (9 if critical else 5)
    assert state[f"{SRD}.entities"]["other"]["hp"] == 100 - context["_damage_applied"]
    assert rolls == [20, *([8, 8] if critical else [8])]
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0
    assert result.result["result_card"]["details"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _zombie_slam(state)
    assert state == before


@pytest.mark.parametrize(
    "problem",
    ["die", "multiattack", "distance", "plane", "cover", "source", "control_order", "general_task"],
)
def test_zombie_slam_rejects_false_profile_geometry_or_unexecuted_intent_atomically(
    undead_owner,
    monkeypatch,
    problem,
):
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, data = _zombie_slam_case()
    monkeypatch.setattr(dice, "_rng", Random(0))
    command = {"order_kind": "specific", "action": "attack"}
    if problem == "general_task":
        command = {"order_kind": "general", "task": "Guard this doorway."}
    _animated_turn(state, data, command=command)
    choices = {}
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if problem == "die":
        choices["damage_expression"] = "d10"
    elif problem == "multiattack":
        choices["use_multiattack"] = True
    elif problem == "distance":
        choices["attack_distance"] = 4
    elif problem == "plane":
        next(row for row in scene["entities"] if row["id"] == "other")["metadata"]["plane"] = (
            "ethereal"
        )
    elif problem == "cover":
        wall = _wall(10)
        wall["volume"].update(min=[9, -10, -10], max=[10, 10, 10])
        scene["geometry"] = [wall]
    elif problem == "source":
        state[f"{SRD}.entities"]["zombie_one"]["created_source_block_sha256"] = "0" * 64
    elif problem == "control_order":
        state[f"{SRD}.entities"]["zombie_one"]["animated_turn_order"] = None
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _zombie_slam(state, **choices)
    assert state == before


def test_zombie_slam_control_expiry_preserves_actual_native_attack_and_body(
    undead_owner,
    monkeypatch,
):
    state, data = _zombie_slam_case()
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    body = state[f"{SRD}.entities"]["zombie_one"]
    assert body["animated_control_active"] is False
    assert body["created_active"] and body["created_body_source_state_id"] == "guard"
    monkeypatch.setattr(dice, "_rng", Random(0))
    _animated_turn(state, data)
    assert state[f"{SRD}.combat"]["action_available"] is True
    assert state[f"{SRD}.entities"]["zombie_one"]["dodging"] is False
    result = _zombie_slam(state)
    assert result.result["context"]["_attack_roll"]
    assert state[f"{SRD}.entities"]["zombie_one"]["created_active"] is True


def test_public_zombie_slam_retains_the_real_command_and_pending_hit_after_reopen(
    tmp_path,
    monkeypatch,
):
    import sylvia_gm.application.package_runtime as package
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_extended_casting import _begin

    def case(_spell):
        state, data, parameters = _animated_case()
        _admit_zombie_slam_target(state)
        return state, data, parameters

    rolls, packets = [], []

    class SourceDice:
        def randint(self, low, high):
            assert low == 1 and high in (8, 20)
            rolls.append(high)
            return 20 if high == 20 else 4

    original_execute = package.execute_tool

    def observed(name, parameters, *args, **kwargs):
        result = original_execute(name, parameters, *args, **kwargs)
        if (
            name == "apply_srd_qualifying_damage"
            and parameters.get("source_state_id") == "zombie_one"
        ):
            packets.append(deepcopy(parameters))
        return result

    monkeypatch.setattr(package, "execute_tool", observed)
    monkeypatch.setattr(public, "_spell_case", case)
    monkeypatch.setattr(dice, "_rng", SourceDice())
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "zombie-slam.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "animate_dead",
            target_overrides={"hp": 0, "max_hp": 15},
        )
        prototype = ActionCommand(
            campaign_id="casting",
            action_id="slam-cast",
            idempotency_key="slam-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )
        mechanics = StatelessMcpMechanics(owner)

        def act(identity, name, **choices):
            action = replace(
                prototype,
                action_id=identity,
                idempotency_key=identity,
                tool_name=name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            result = mechanics.execute_action(action)
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == result
            return action, result

        act("slam-cast", "begin_srd_spell_cast", **_begin(parameters))
        act(
            "slam-cast-progress",
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        act("slam-cast-complete", "cast_spell_outside_combat", **parameters)
        command, command_receipt = act(
            "slam-order",
            "command_srd_animated_undead",
            caster_state="hero",
            actor_ids=["zombie_one"],
            order_kind="specific",
            action="attack",
        )
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command) == command_receipt
        act(
            "slam-initiative",
            "start_group_combat_encounter",
            combatants=["hero", "zombie_one", "other"],
        )
        for index in range(4):
            actor = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            act(f"slam-turn-{index}", "begin_combat_turn")
            if actor == "zombie_one":
                break
            act(f"slam-end-{index}", "end_combat_turn")
            act(f"slam-next-{index}", "advance_group_combat_encounter_turn")
        else:
            pytest.fail("The actual permanent Zombie did not get its independent turn.")
        body = owner.snapshot("casting").state[f"{SRD}.entities"]["zombie_one"]
        assert body["animated_turn_order"]["selected_actor_ids"] == ["zombie_one"]
        assert body["animated_turn_order"]["action"] == "attack" and body["animated_order"] is None
        begun, begin_receipt = act(
            "slam-hit",
            "begin_srd_creature_attack",
            target="other",
            attack_name="Slam",
            attack_modifier=3,
            attack_mode="melee",
            damage_expression="d8",
            damage_modifier=1,
            damage_type="bludgeoning",
            normal_range=5,
            long_range=5,
            attack_distance=5,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
            advantage=False,
            disadvantage=False,
            use_multiattack=False,
            attack_uses_weapon=False,
        )
        pending = begin_receipt.result["context"]["_pending_action_id"]
        saved = owner.snapshot("casting")
        actual_roll = saved.state[f"{SRD}.state"]["pending_srd_actions"][pending]["attack_roll"]
        assert saved.state[f"{SRD}.entities"]["other"]["hp"] == 1000
        d20_count = rolls.count(20)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(begun) == begin_receipt
        completed, receipt = act(
            "slam-complete", "complete_srd_pending_action", pending_action_id=pending
        )
        context = receipt.result["context"]
        assert context["_attack_roll"] == actual_roll
        assert context["_attack_outcome"] == "critical_hit"
        assert rolls.count(20) == d20_count and rolls.count(8) == 2
        assert context["_damage_applied"] == 9
        assert len(packets) == 1 and packets[0]["critical_hit"] is True
        assert packets[0]["damage_type"] == "bludgeoning"
        assert {"Attack roll", "Damage"} <= {
            row["label"] for row in receipt.result["result_card"]["details"]
        }
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["other"]["hp"] == 991
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert saved.state[f"{SRD}.entities"]["zombie_one"]["created_active"] is True
        assert mechanics.execute_action(completed) == receipt and len(packets) == 1
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(completed) == receipt
        assert rolls.count(20) == d20_count and rolls.count(8) == 2


@pytest.mark.parametrize("problem", ["none", "range", "plane", "caster", "form", "inactive"])
def test_animated_command_uses_actual_mental_range_plane_and_source(problem, undead_owner):
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, data, parameters = _animated_case()
    _finish_familiar_cast(state, data, parameters)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    body_scene = next(row for row in scene["entities"] if row["id"] == "zombie_one")
    body_scene["position"] = [60, 0, 0]
    scene["geometry"].append(_wall(30))
    body = state[f"{SRD}.entities"]["zombie_one"]
    body["conditions"] = ["deafened"]
    if problem == "range":
        body_scene["position"] = [60.01, 0, 0]
    elif problem == "plane":
        body_scene["metadata"]["plane"] = "ethereal"
    elif problem == "caster":
        body["created_caster_state_id"] = "guard"
    elif problem == "form":
        body["form_statistics_replaced"] = True
    elif problem == "inactive":
        state[f"{SRD}.state"]["created_actor_records"]["zombie_one"]["active"] = False
    before = deepcopy(state)
    if problem != "none":
        with pytest.raises(RulesetPreconditionRejected):
            _run(
                "command_srd_animated_undead",
                state,
                caster_state="hero",
                actor_ids=["zombie_one"],
                order_kind="specific",
                action="dodge",
            )
        assert state == before
        return
    result = _run(
        "command_srd_animated_undead",
        state,
        caster_state="hero",
        actor_ids=["zombie_one"],
        order_kind="specific",
        action="dodge",
    )
    order = state[f"{SRD}.entities"]["zombie_one"]["animated_order"]
    assert order["selected_actor_ids"] == ["zombie_one"]
    assert order["caster"] == "hero" and order["issued_tick"] == 21
    assert result.result["result_card"]["title"] == "Animate Dead mental command"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="exploration round"):
        _run(
            "command_srd_animated_undead",
            state,
            caster_state="hero",
            actor_ids=["zombie_one"],
            order_kind="specific",
            action="move",
        )
    assert state == before


@pytest.mark.parametrize("condition", ["none", "speed_zero", "incapacitated"])
def test_animated_default_dodge_uses_actual_turn_and_ordinary_no_benefit_rules(
    condition,
    undead_owner,
    monkeypatch,
):
    state, data, parameters = _animated_case()
    _finish_familiar_cast(state, data, parameters)
    body = state[f"{SRD}.entities"]["zombie_one"]
    if condition == "speed_zero":
        body["spell_speed_reduction"] = body["speed"]
    elif condition == "incapacitated":
        body["conditions"] = ["incapacitated"]
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    begun = _animated_turn(state, data)
    body = state[f"{SRD}.entities"]["zombie_one"]
    combat = state[f"{SRD}.combat"]
    assert combat["active_actor"] == "zombie_one"
    assert body["dodging"] is (condition == "none")
    assert combat["action_available"] is False
    assert combat["attack_available"] is False
    if condition != "incapacitated":
        assert combat["action_taken"] == "dodge"
        assert any(
            row["label"] == "Animate Dead" and "Dodge" in row["value"]
            for row in begun.result["result_card"]["details"]
        )
    assert body["animated_order"] is None
    order = next(row for row in combat["order"] if row["entity_id"] == "zombie_one")
    assert order["modifier"] == -2


@pytest.mark.parametrize("invalid_last", [False, True])
def test_animated_command_one_bonus_action_controls_one_actual_selected_batch(
    invalid_last,
    undead_owner,
    monkeypatch,
):
    state, data, parameters = _animated_three_case()
    _finish_familiar_cast(state, data, parameters)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = (
        data["abilities"]["dexterity"] - 10
    ) // 2
    state[f"{SRD}.entities"]["other"]["initiative_modifier"] = 0
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "zombie_one", "other"],
    )
    for _ in range(4):
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=actor)
        if actor == "hero":
            break
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
    else:
        pytest.fail("The actual caster did not receive its turn.")
    selected = [choice["individual_id"] for choice in parameters["animated_bodies"]]
    if invalid_last:
        scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
        next(row for row in scene if row["id"] == selected[-1])["position"] = [61, 0, 0]
    before = deepcopy(state)
    if invalid_last:
        with pytest.raises(RulesetPreconditionRejected, match="60 feet"):
            _run(
                "command_srd_animated_undead",
                state,
                caster_state="hero",
                actor_ids=selected,
                order_kind="specific",
                action="dodge",
            )
        assert state == before
        return
    _run(
        "command_srd_animated_undead",
        state,
        caster_state="hero",
        actor_ids=selected,
        order_kind="specific",
        action="dodge",
    )
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["bonus_action_available"] is False
    assert hero["last_bonus_action"] == "animate_dead_command"
    assert state[f"{SRD}.combat"]["action_available"] is True
    assert state[f"{SRD}.combat"]["bonus_action_used_this_turn"] is True
    for identity in selected:
        saved = state[f"{SRD}.entities"][identity]["animated_order"]
        assert saved["selected_actor_ids"] == selected and saved["caster"] == "hero"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="already been spent"):
        _run(
            "command_srd_animated_undead",
            state,
            caster_state="hero",
            actor_ids=selected,
            order_kind="specific",
            action="move",
        )
    assert state == before


def test_animated_specific_order_applies_once_then_returns_to_default_dodge(
    undead_owner,
    monkeypatch,
):
    state, data, parameters = _animated_case()
    _finish_familiar_cast(state, data, parameters)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run(
        "command_srd_animated_undead",
        state,
        caster_state="hero",
        actor_ids=["zombie_one"],
        order_kind="specific",
        action="move",
    )
    _animated_turn(state, data)
    body = state[f"{SRD}.entities"]["zombie_one"]
    assert body["animated_turn_order"]["action"] == "move"
    assert body["animated_order"] is None and body["dodging"] is False
    assert state[f"{SRD}.combat"]["action_available"] is True
    _run("end_combat_turn", state)
    _run("advance_group_combat_encounter_turn", state)
    _animated_turn(state, data)
    assert state[f"{SRD}.entities"]["zombie_one"]["dodging"] is True
    assert state[f"{SRD}.combat"]["action_available"] is False


def test_animated_general_task_persists_as_intent_without_a_fabricated_outcome(
    undead_owner,
    monkeypatch,
):
    state, data, parameters = _animated_case()
    _finish_familiar_cast(state, data, parameters)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    task = "Guard this passage until I return."
    _run(
        "command_srd_animated_undead",
        state,
        caster_state="hero",
        actor_ids=["zombie_one"],
        order_kind="general",
        task=task,
    )
    for _ in range(2):
        _animated_turn(state, data)
        body = state[f"{SRD}.entities"]["zombie_one"]
        assert body["animated_order"]["task"] == task
        assert body["animated_turn_order"]["task"] == task
        assert body["dodging"] is False
        assert state[f"{SRD}.combat"]["action_available"] is True
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)


def test_animated_expiry_drops_only_active_control_traversal_not_body_registry(undead_owner):
    state, data, parameters = _animated_case()
    _finish_familiar_cast(state, data, parameters)
    _run(
        "command_srd_animated_undead",
        state,
        caster_state="hero",
        actor_ids=["zombie_one"],
        order_kind="general",
        task="Watch the gate.",
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    source = state[f"{SRD}.state"]
    body = state[f"{SRD}.entities"]["zombie_one"]
    assert source["animated_actor_ids"] == [] and source["has_animated_undead"] is False
    assert source["created_actor_records"]["zombie_one"]["active"] is True
    assert body["animated_order"] is None and body["animated_turn_order"] is None
    assert body["created_active"] is True and body["is_alive"] is True and body["hp"] == 15


def test_animate_dead_paid_minute_transforms_the_actual_body_and_keeps_its_source():
    from scripts.srd_full.srd_spell_summoning import ANIMATED_PROFILE

    validate_profiles({"animate_dead": ANIMATED_PROFILE})
    state, data, params = _animated_case()
    result = _finish_familiar_cast(state, data, params)
    entities = state[f"{SRD}.entities"]
    body = entities["zombie_one"]
    assert body["hp"] == body["max_hp"] == 15 and body["armor_class"] == 8
    assert body["animated_control_until"] == 28821 and body["animated_control_active"]
    assert body["created_body_source_state_id"] == "guard"
    assert entities["guard"]["body_animated_actor_id"] == "zombie_one"
    assert entities["guard"]["death_tick"] == 1 and not entities["guard"]["body_available"]
    assert entities["hero"]["resources"]["spell_slot_3"] == 2
    assert entities["hero"]["concentration_spell_id"] is None
    assert entities["hero"]["extended_casting"] is None
    assert state[f"{SRD}.state"]["lasting_tick"] == 21
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    assert not next(row for row in scene if row["id"] == "guard")["active"]
    admitted = next(row for row in scene if row["id"] == "zombie_one")
    assert admitted["active"] and admitted["position"] == [5, 0, 0]
    assert admitted["footprint"] == {"kind": "box", "half_extents": [2.5, 2.5]}
    assert result.result["result_card"]["details"]


@pytest.mark.parametrize("problem", ["range", "wall", "plane", "gear", "living"])
def test_animate_dead_completion_rejects_actual_body_failure_atomically(problem):
    from tests.srd_full.test_srd_extended_casting import _begin
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, data, params = _animated_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if problem == "range":
        scene["entities"][1]["position"] = [10.001, 0, 0]
    elif problem == "wall":
        wall = _wall(1)
        wall["volume"].update(min=[2, -10, -10], max=[3, 10, 10])
        scene["geometry"] = [wall]
    elif problem == "plane":
        scene["entities"][1]["metadata"]["plane"] = "ethereal"
    elif problem == "gear":
        state[f"{SRD}.entities"]["guard"]["inventory"] = ["corpse_shirt"]
    else:
        state[f"{SRD}.entities"]["guard"]["is_alive"] = True
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, MechanicsRejected)):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before and "zombie_one" not in state[f"{SRD}.entities"]


def test_animate_dead_reassertion_at_completion_extends_control_without_replacing_the_body():
    state, data, params = _animated_case()
    _finish_familiar_cast(state, data, params)
    body = state[f"{SRD}.entities"]["zombie_one"]
    body.update(hp=7, individual_memory="original grave")
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=100)
    _finish_familiar_cast(
        state,
        data,
        params
        | {
            "animated_mode": "reassert",
            "animated_bodies": [],
            "animated_actor_ids": ["zombie_one"],
        },
    )
    body = state[f"{SRD}.entities"]["zombie_one"]
    assert body["animated_control_until"] == 28941 and body["hp"] == 7
    assert body["individual_memory"] == "original grave"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 1
    assert state[f"{SRD}.state"]["animated_actor_ids"] == ["zombie_one"]


@pytest.mark.parametrize("ticks,controlled", [(28799, True), (28800, False)])
def test_animate_dead_control_expires_without_dismissing_or_killing_the_actor(ticks, controlled):
    state, data, params = _animated_case()
    _finish_familiar_cast(state, data, params)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=ticks)
    body = state[f"{SRD}.entities"]["zombie_one"]
    assert body["animated_control_active"] is controlled
    assert body["created_active"] and body["is_alive"] and body["hp"] == 15
    assert body["death_tick"] is None
    assert next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] == "zombie_one"
    )["active"]


def test_animate_dead_cannot_reassert_after_the_existing_period_ends_during_casting():
    state, data, params = _animated_case()
    _finish_familiar_cast(state, data, params)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28790)
    before_body = deepcopy(state[f"{SRD}.entities"]["zombie_one"])
    with pytest.raises(RulesetPreconditionRejected, match="before control expires"):
        _finish_familiar_cast(
            state,
            data,
            params
            | {
                "animated_mode": "reassert",
                "animated_bodies": [],
                "animated_actor_ids": ["zombie_one"],
            },
        )
    body = state[f"{SRD}.entities"]["zombie_one"]
    assert body["animated_control_until"] == before_body["animated_control_until"]
    assert body["is_alive"] and body["created_active"]
    assert not body["animated_control_active"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2


@pytest.mark.parametrize(
    "language,understands", [("common", True), ("draconic", True), ("infernal", False)]
)
def test_animate_dead_native_body_understands_only_its_printed_saved_languages(
    language,
    understands,
):
    state, data, params = _animated_case()
    _finish_familiar_cast(state, data, params)
    state[f"{SRD}.entities"]["hero"]["languages"] = [language]
    result = _run(
        "resolve_srd_spell_language",
        state,
        listener_state="zombie_one",
        speaker_state="hero",
        channel="speech",
        language=language,
        perceived=True,
    )
    assert result.result["context"]["_language_understands"] is understands
    assert result.result["context"]["_language_can_reply"] is False
    assert state[f"{SRD}.entities"]["zombie_one"]["languages"] == []


def test_animate_dead_speech_respects_actual_deafness_and_native_non_speaking_body():
    state, data, params = _animated_case()
    _finish_familiar_cast(state, data, params)
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    zombie["conditions"] = ["deafened"]
    heard = _run(
        "resolve_srd_spell_language",
        state,
        listener_state="zombie_one",
        speaker_state="hero",
        channel="speech",
        language="common",
        perceived=True,
    )
    assert heard.result["context"]["_language_understands"] is False
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    zombie["conditions"] = []
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="native Zombie cannot speak"):
        _run(
            "resolve_srd_spell_language",
            state,
            listener_state="hero",
            speaker_state="zombie_one",
            channel="speech",
            language="common",
            perceived=True,
        )
    assert state == before


def test_animate_dead_creation_retains_the_actual_consumed_physical_body_source(undead_owner):
    state, data = _recorded_corpse_case()
    state[f"{SRD}.entities"]["guard"]["current_body_source_id"] = "clone_vessel_7"
    _run(
        "prepare_srd_source_zombie",
        state,
        participant_data=data,
        creature_id="zombie_one",
        corpse_state="guard",
        source_caster="hero",
        other_language="draconic",
    )
    body = state[f"{SRD}.entities"]["zombie_one"]
    record = state[f"{SRD}.state"]["created_actor_records"]["zombie_one"]
    assert body["created_body_source_state_id"] == "guard"
    assert body["created_body_source_id"] == "clone_vessel_7"
    assert record["body_source"] == "guard" and record["body_source_id"] == "clone_vessel_7"


def test_animate_dead_slot_four_creates_three_distinct_actual_corpses():
    state, data, params = _animated_three_case()
    result = _finish_familiar_cast(state, data, params)
    entities = state[f"{SRD}.entities"]
    assert entities["hero"]["resources"]["spell_slot_4"] == 2
    assert len(state[f"{SRD}.state"]["animated_actor_ids"]) == 3
    for choice in params["animated_bodies"]:
        assert (
            entities[choice["individual_id"]]["created_body_source_state_id"]
            == choice["corpse_state_id"]
        )
        assert (
            entities[choice["corpse_state_id"]]["body_animated_actor_id"] == choice["individual_id"]
        )
    assert any(
        row["label"] == "Undead individuals" and row["value"] == 3
        for row in result.result["result_card"]["details"]
    )


def test_animate_dead_duplicate_corpse_cannot_create_two_individuals_atomically():
    from tests.srd_full.test_srd_extended_casting import _begin

    state, data, params = _animated_case(slot_resource="spell_slot_4")
    params["animated_bodies"].append(
        params["animated_bodies"][0]
        | {
            "individual_id": "zombie_two",
        }
    )
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="different corpse"):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def test_public_animate_dead_completes_saved_minute_cast_and_keeps_the_controlled_body(
    tmp_path,
    monkeypatch,
):
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_extended_casting import _begin

    monkeypatch.setattr(public, "_spell_case", lambda _spell: _animated_case())
    database = tmp_path / "animated.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "animate_dead")
        command = ActionCommand(
            campaign_id="casting",
            action_id="animated-begin",
            idempotency_key="animated-begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )
        receipt = StatelessMcpMechanics(owner).execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        saved = owner.snapshot("casting")
        assert "zombie_one" not in saved.state[f"{SRD}.entities"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command) == receipt

        def act(identity, name, **choices):
            action = replace(
                command,
                action_id=identity,
                idempotency_key=identity,
                tool_name=name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            result = mechanics.execute_action(action)
            assert result.status is ActionStatus.COMMITTED
            assert mechanics.execute_action(action) == result
            return action, result

        act(
            "animated-continue",
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        actual_choice = parameters | {
            "animated_bodies": [
                {
                    "corpse_state_id": "guard",
                    "individual_id": "chosen_zombie",
                    "other_language": "infernal",
                    "appearance_height": 6,
                }
            ]
        }
        completed, receipt = act("animated-complete", "cast_spell_outside_combat", **actual_choice)
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["chosen_zombie"]
        assert body["created_understood_languages"] == ["common", "infernal"]
        assert body["created_body_source_state_id"] == "guard" and body["created_active"]
        assert body["animated_control_until"] == 28821
        assert "zombie_one" not in saved.state[f"{SRD}.entities"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        assert receipt.result["result_card"]["title"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(completed) == receipt
        expired = replace(
            command,
            action_id="animated-expired",
            idempotency_key="animated-expired",
            tool_name="advance_srd_lasting_time",
            expected_revision=owner.snapshot("casting").revision,
            parameters={"elapsed_rounds": 14400},
        )
        expired_receipt = mechanics.execute_action(expired)
        assert expired_receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["chosen_zombie"]
        assert not body["animated_control_active"]
        assert body["created_active"] and body["is_alive"] and body["hp"] == 15
        assert saved.state[f"{SRD}.entities"]["guard"]["body_available"] is False
        assert saved.state[f"{SRD}.entities"]["guard"]["body_animated_actor_id"] == "chosen_zombie"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(expired) == expired_receipt
        assert mechanics.execute_action(completed) == receipt
        assert owner.snapshot("casting").state == saved.state


def test_public_animated_command_keeps_the_same_actor_batch_through_reopen_and_native_dodge(
    tmp_path,
    monkeypatch,
):
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_extended_casting import _begin

    monkeypatch.setattr(public, "_spell_case", lambda _spell: _animated_three_case())
    database = tmp_path / "animated-commands.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "animate_dead")
        prototype = ActionCommand(
            campaign_id="casting",
            action_id="created-begin",
            idempotency_key="created-begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )
        mechanics = StatelessMcpMechanics(owner)

        def act(identity, name, **choices):
            action = replace(
                prototype,
                action_id=identity,
                idempotency_key=identity,
                tool_name=name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            result = mechanics.execute_action(action)
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == result
            return action, result

        act("created-begin", "begin_srd_spell_cast", **_begin(parameters))
        act(
            "created-continue",
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        act("created-complete", "cast_spell_outside_combat", **parameters)
        selected = [choice["individual_id"] for choice in parameters["animated_bodies"]]
        commanded, command_receipt = act(
            "created-command",
            "command_srd_animated_undead",
            caster_state="hero",
            actor_ids=selected,
            order_kind="specific",
            action="dodge",
        )
        assert any(
            row["label"] == "Controlled individuals" and row["value"] == 3
            for row in command_receipt.result["result_card"]["summary"]
        )
        saved = owner.snapshot("casting")
        for identity in selected:
            body = saved.state[f"{SRD}.entities"][identity]
            assert body["animated_order"]["selected_actor_ids"] == selected
            assert body["animated_order"]["caster"] == "hero"
            assert body["created_caster_state_id"] == "hero"
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(commanded) == command_receipt
        act("created-initiative", "start_group_combat_encounter", combatants=["hero", *selected])
        for index in range(5):
            actor = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            begun, begun_receipt = act(f"created-turn-{index}", "begin_combat_turn")
            if actor == "zombie_one":
                break
            act(f"created-end-{index}", "end_combat_turn")
            act(f"created-next-{index}", "advance_group_combat_encounter_turn")
        else:
            pytest.fail("The actual created Zombie did not receive its independent turn.")
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["zombie_one"]
        assert body["dodging"] is True and body["animated_order"] is None
        assert body["animated_turn_order"]["selected_actor_ids"] == selected
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert any(
            row["label"] == "Animate Dead" and row["value"] == "Commanded Dodge"
            for row in begun_receipt.result["result_card"]["details"]
        )
        order = next(
            row for row in saved.state[f"{SRD}.combat"]["order"] if row["entity_id"] == "zombie_one"
        )
        assert order["modifier"] == -2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(begun) == begun_receipt
        assert mechanics.execute_action(commanded) == command_receipt
        assert owner.snapshot("casting").state == saved.state


def test_zombie_source_recovers_slam_on_the_actual_following_page(undead_owner):
    from scripts.srd_full.srd_spell_summoning import _zombie_source_profile

    source = _zombie_source_profile()
    assert [page["page"] for page in source["source_pages"]] == [343, 344]
    assert source["attacks"]["Slam"]["damage_expression"] == "d8"
    assert "Ogre Zombie" not in source["source_stat_block"]
    state, _ = _zombie_case()
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    corpse = state[f"{SRD}.entities"]["guard"]
    assert zombie["hp"] == zombie["max_hp"] == 15
    assert zombie["armor_class"] == 8 and zombie["speed"] == 20
    assert zombie["initiative_modifier"] == -2
    assert zombie["ability_scores"] == source["ability_scores"]
    assert zombie["saving_throw_modifiers"] == source["saving_throw_modifiers"]
    assert zombie["damage_immunities"] == ["poison"]
    assert zombie["condition_immunities"] == ["exhaustion", "poisoned"]
    assert zombie["created_source_attack"]["name"] == "Slam"
    assert zombie["created_body_source_state_id"] == "guard"
    assert zombie["created_source_block_sha256"] == source["source_stat_block_sha256"]
    assert zombie["languages"] == [] and zombie["created_understood_languages"] == [
        "common",
        "draconic",
    ]
    assert zombie["resources"]["hit_die"] == 2 and zombie["hit_die_size"] == 8
    assert corpse["body_animated_actor_id"] == "zombie_one"
    assert corpse["body_available"] is False and corpse["death_creature_type"] == "humanoid"
    assert corpse["soul_willing"] is True and corpse["death_tick"] is not None


@pytest.mark.parametrize("length", [0, 81])
def test_zombie_language_length_rejects_before_corpse_or_body_mutation(undead_owner, length):
    from sylvia_gm.application.package_runtime import PackageRuntimeError

    state, data = _recorded_corpse_case()
    before = deepcopy(state)
    rejection = PackageRuntimeError if length == 0 else RulesetPreconditionRejected
    with pytest.raises(rejection):
        _run(
            "prepare_srd_source_zombie",
            state,
            participant_data=data,
            creature_id="zombie_two",
            corpse_state="guard",
            source_caster="hero",
            other_language="x" * length,
        )
    assert state == before and "zombie_two" not in state[f"{SRD}.entities"]


@pytest.mark.parametrize("length", [1, 80])
def test_zombie_language_length_accepts_the_exact_source_choice_bounds(undead_owner, length):
    state, data = _recorded_corpse_case()
    _run(
        "prepare_srd_source_zombie",
        state,
        participant_data=data,
        creature_id="zombie_one",
        corpse_state="guard",
        source_caster="hero",
        other_language="x" * length,
    )
    assert state[f"{SRD}.entities"]["zombie_one"]["created_understood_languages"] == [
        "common",
        "x" * length,
    ]


@pytest.mark.parametrize("composite", [False, True])
@pytest.mark.parametrize("face,success", [(20, True), (1, False)])
def test_zombie_fortitude_uses_actual_zero_hp_save_before_death(
    monkeypatch,
    undead_owner,
    composite,
    face,
    success,
):
    state, _ = _zombie_case()
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    zombie.update(hp=5, temp_hp=0)

    class Face:
        def randint(self, low, high):
            assert (low, high) == (1, 20)
            return face

    monkeypatch.setattr(dice, "_rng", Face())
    if composite:
        result = _run(
            "apply_srd_composite_damage",
            state,
            target="zombie_one",
            component_1_amount=3,
            component_1_damage_type="fire",
            component_2_amount=3,
            component_2_damage_type="cold",
        )
    else:
        result = _run(
            "apply_srd_damage",
            state,
            target="zombie_one",
            amount=6,
            adjustment=0,
            damage_type="fire",
        )
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    assert zombie["last_undead_fortitude"]["dc"] == 11
    assert zombie["last_undead_fortitude"]["success"] is success
    assert zombie["hp"] == (1 if success else 0)
    assert zombie["is_alive"] is success
    assert ("dead" in zombie["conditions"]) is not success
    assert (zombie["death_tick"] is None) is success
    assert zombie["created_active"] is True and zombie["body_available"] is True
    assert result.result["status"] == "committed"


@pytest.mark.parametrize(
    "composite,radiant,critical",
    [
        (False, True, False),
        (False, False, True),
        (True, True, False),
        (True, False, True),
    ],
)
def test_zombie_radiant_and_critical_lethal_packets_bypass_the_save(
    monkeypatch,
    undead_owner,
    composite,
    radiant,
    critical,
):
    state, _ = _zombie_case()
    state[f"{SRD}.entities"]["zombie_one"]["hp"] = 5

    class NoRoll:
        def randint(self, low, high):
            raise AssertionError("Radiant and real critical damage bypass Undead Fortitude.")

    monkeypatch.setattr(dice, "_rng", NoRoll())
    if composite:
        _run(
            "apply_srd_composite_damage",
            state,
            target="zombie_one",
            component_1_amount=3,
            component_1_damage_type="radiant" if radiant else "fire",
            component_2_amount=3,
            component_2_damage_type="cold",
            critical_hit=critical,
        )
    else:
        _run(
            "apply_srd_damage",
            state,
            target="zombie_one",
            amount=6,
            adjustment=0,
            damage_type="radiant" if radiant else "fire",
            critical_hit=critical,
        )
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    assert zombie["hp"] == 0 and zombie["is_alive"] is False
    assert zombie["last_undead_fortitude"]["success"] is False
    assert zombie["last_undead_fortitude"]["critical"] is critical
    assert zombie["last_undead_fortitude"]["radiant"] is radiant


@pytest.mark.parametrize(
    "packet,dc", [("temp_hp", 15), ("immune_poison", 11), ("zero_radiant", 11)]
)
def test_zombie_fortitude_dc_and_bypass_follow_the_actual_typed_packet(
    monkeypatch,
    undead_owner,
    packet,
    dc,
):
    state, _ = _zombie_case()
    state[f"{SRD}.entities"]["zombie_one"].update(hp=5, temp_hp=4 if packet == "temp_hp" else 0)

    class Maximum:
        def randint(self, low, high):
            assert (low, high) == (1, 20)
            return 20

    monkeypatch.setattr(dice, "_rng", Maximum())
    if packet == "temp_hp":
        _run(
            "apply_srd_damage",
            state,
            target="zombie_one",
            amount=10,
            adjustment=0,
            damage_type="fire",
        )
    else:
        _run(
            "apply_srd_composite_damage",
            state,
            target="zombie_one",
            component_1_amount=100 if packet == "immune_poison" else 0,
            component_1_damage_type="poison" if packet == "immune_poison" else "radiant",
            component_2_amount=6,
            component_2_damage_type="fire",
        )
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    assert zombie["last_undead_fortitude"]["dc"] == dc
    assert zombie["last_undead_fortitude"]["radiant"] is False
    assert zombie["hp"] == 1 and zombie["temp_hp"] == 0 and zombie["is_alive"] is True
    assert zombie["death_tick"] is None


@pytest.mark.parametrize("event", ["nonlethal", "immune", "replaced_body"])
def test_zombie_fortitude_skips_nonlethal_immune_and_replaced_body_damage(
    monkeypatch,
    undead_owner,
    event,
):
    state, _ = _zombie_case()
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    if event == "replaced_body":
        zombie.update(form_statistics_replaced=True, hp=5)

    class NoRoll:
        def randint(self, low, high):
            raise AssertionError("No native Undead Fortitude saving throw applies.")

    monkeypatch.setattr(dice, "_rng", NoRoll())
    _run(
        "apply_srd_damage",
        state,
        target="zombie_one",
        amount={"nonlethal": 1, "immune": 100, "replaced_body": 6}[event],
        adjustment=0,
        damage_type="poison" if event == "immune" else "fire",
    )
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    assert zombie.get("last_undead_fortitude") is None
    assert zombie["hp"] == {"nonlethal": 14, "immune": 15, "replaced_body": 0}[event]
    assert zombie["is_alive"] is (event != "replaced_body")


@pytest.mark.parametrize("tamper", ["block", "caster", "corpse"])
def test_zombie_fortitude_rejects_conflicting_saved_source_identity_atomically(
    undead_owner,
    tamper,
):
    state, _ = _zombie_case()
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    zombie["hp"] = 5
    if tamper == "block":
        zombie["created_source_block_sha256"] = "0" * 64
    else:
        record = state[f"{SRD}.state"]["created_actor_records"]["zombie_one"]
        record["caster" if tamper == "caster" else "body_source"] = "other"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "apply_srd_damage",
            state,
            target="zombie_one",
            amount=6,
            adjustment=0,
            damage_type="fire",
        )
    assert state == before


@pytest.mark.parametrize(
    "change",
    [
        {"death_tick": None},
        {"body_available": False},
        {"body_animated_actor_id": "other"},
        {"death_creature_type": "beast"},
        {"size_rank": 3},
        {"is_alive": True},
    ],
)
def test_zombie_creation_rejects_absent_ineligible_or_owned_body_atomically(undead_owner, change):
    state, data = _recorded_corpse_case()
    corpse = state[f"{SRD}.entities"]["guard"]
    corpse.update({"body_available": True, "body_animated_actor_id": None, **change})
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "prepare_srd_source_zombie",
            state,
            participant_data=data,
            creature_id="zombie_two",
            corpse_state="guard",
            source_caster="hero",
            other_language="draconic",
        )
    assert state == before and "zombie_two" not in state[f"{SRD}.entities"]


@pytest.mark.parametrize("spell", ["find_familiar", "find_steed"])
def test_transient_created_bodies_still_disappear_at_zero_after_undead_separation(
    undead_owner,
    spell,
):
    if spell == "find_familiar":
        state, data, params = _familiar_case()
        _finish_familiar_cast(state, data, params)
        individual = "familiar_one"
    else:
        state, data, params = _steed_case()
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
        individual = "steed_one"
    body = state[f"{SRD}.entities"][individual]
    _run(
        "apply_srd_damage",
        state,
        target=individual,
        amount=body["hp"] + body["temp_hp"],
        adjustment=0,
        damage_type="fire",
        critical_hit=False,
    )
    body = state[f"{SRD}.entities"][individual]
    assert body["created_active"] is False and body["body_available"] is False
    record = state[f"{SRD}.state"]["created_actor_records"][individual]
    assert record["active"] is False and record["disappearance"] == "zero_hit_points"
    scene = state[f"{SRD}.state"]["spatial_scene"]
    assert next(row for row in scene["entities"] if row["id"] == individual)["active"] is False


def _familiar_case(form="bat", creature_type="celestial", **choices):
    state, data, params = _information_case("find_familiar")
    _free(state)
    params.pop("target")
    params.update(
        familiar_form_id=form,
        created_individual_id="familiar_one",
        created_creature_type=creature_type,
        created_actor_position=[5, 0, 0],
        created_actor_height=1,
    )
    params.update(choices)
    return state, data, params


def _finish_familiar_cast(state, data, params):
    from tests.srd_full.test_srd_extended_casting import _begin

    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    hero = state[f"{SRD}.entities"]["hero"]
    rounds = hero["extended_casting"]["required_rounds"]
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=rounds,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        spellbook_readable=params.get("spellbook_readable", False),
    )
    return _run("cast_spell_outside_combat", state, participant_data=data, **params)


@pytest.mark.parametrize("form", ["bat", "eagle", "hawk", "jackal", "scorpion", "weasel"])
def test_familiar_uses_exact_source_body_and_real_long_casting_costs(form):
    from scripts.srd_full.build_srd_beast_forms import build_index

    validate_profiles({"find_familiar": FAMILIAR_PROFILE})
    source = build_index()["forms"][form]
    state, data, params = _familiar_case(form)
    result = _finish_familiar_cast(state, data, params)
    body = state[f"{SRD}.entities"]["familiar_one"]
    for field, source_field in (
        ("armor_class", "armor_class"),
        ("hp", "hit_points"),
        ("max_hp", "hit_points"),
        ("speed", "walk_speed"),
        ("size_rank", "size_rank"),
        ("initiative_modifier", "initiative_modifier"),
        ("ability_scores", "ability_scores"),
        ("saving_throw_modifiers", "saving_throw_modifiers"),
        ("passive_perception", "passive_perception"),
    ):
        assert body[field] == source[source_field]
    assert body["creature_type"] == "celestial"
    assert body["created_form_id"] == form
    assert body["created_source_block_sha256"] == source["source_stat_block_sha256"]
    assert body["challenge_rating_eighths"] == 0 and body["proficiency_bonus"] == 2
    assert body["flying_speed"] == source["other_speeds"].get("fly", 0)
    assert body["climb_speed"] == source["other_speeds"].get("climb", 0)
    assert all(body[field] == value for field, value in source["senses"].items())
    assert body["skill_check_modifiers"] == source["skills"]
    from scripts.srd_full.srd_spell_summoning import _familiar_source_forms

    printed = _familiar_source_forms()[form]
    assert body["resources"]["hit_die"] == printed["source_hit_dice"]
    assert body["resource_maxima"]["hit_die"] == printed["source_hit_dice"]
    assert body["hit_die_size"] == printed["source_hit_die_size"]
    assert body["constitution_modifier"] == (source["ability_scores"]["constitution"] - 10) // 2
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_1"] == 2
    assert hero["spell_materials"][0]["quantity"] == 2
    assert hero["concentration_spell_id"] is None and hero["extended_casting"] is None
    assert state[f"{SRD}.state"]["lasting_tick"] == 1200
    assert result.result["result_card"]["details"]


@pytest.mark.parametrize("creature_type", ["fey", "fiend"])
def test_familiar_changes_only_printed_creature_type(creature_type):
    state, data, params = _familiar_case(creature_type=creature_type)
    _finish_familiar_cast(state, data, params)
    body = state[f"{SRD}.entities"]["familiar_one"]
    assert body["creature_type"] == creature_type
    assert body["armor_class"] == 12 and body["hp"] == 1
    assert body["created_caster_state_id"] == "hero"


def test_familiar_ritual_pays_incense_and_actual_seventy_minutes_without_a_slot():
    state, data, params = _familiar_case(
        casting_mode="ritual",
        slot_resource="ritual",
        spellbook_readable=True,
    )
    _finish_familiar_cast(state, data, params)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_1"] == 3
    assert hero["spell_materials"][0]["quantity"] == 2
    assert state[f"{SRD}.state"]["lasting_tick"] == 1400


def test_familiar_zero_hp_disappears_and_cast_returns_the_same_saved_individual():
    state, data, params = _familiar_case()
    _finish_familiar_cast(state, data, params)
    state[f"{SRD}.entities"]["familiar_one"]["logical_soul_reference"] = "saved_spirit"
    _run(
        "apply_srd_damage",
        state,
        target="familiar_one",
        amount=1,
        damage_type="force",
        critical_hit=False,
        adjustment=0,
    )
    body = state[f"{SRD}.entities"]["familiar_one"]
    assert body["created_active"] is False and body["body_available"] is False
    assert body["created_disappearance"] == "zero_hit_points"
    assert state[f"{SRD}.entities"]["hero"]["created_familiar_id"] == "familiar_one"
    params.update(return_created_individual=True, familiar_form_id="weasel")
    _finish_familiar_cast(state, data, params)
    body = state[f"{SRD}.entities"]["familiar_one"]
    assert body["logical_soul_reference"] == "saved_spirit"
    assert body["created_form_id"] == "weasel" and body["hp"] == 1
    assert body["created_active"] and body["created_individual_id"] == "familiar_one"
    assert body["death_tick"] is None
    assert state[f"{SRD}.state"]["created_actor_ids"] == ["familiar_one"]


@pytest.mark.parametrize("problem", ["existing_npc", "unowned_trait", "second_individual"])
def test_familiar_source_and_one_individual_rejection_is_atomic(problem):
    from tests.srd_full.test_srd_extended_casting import _begin

    state, data, params = _familiar_case()
    if problem == "existing_npc":
        params["created_individual_id"] = "guard"
    elif problem == "unowned_trait":
        params["familiar_form_id"] = "owl"
    else:
        _finish_familiar_cast(state, data, params)
        params["created_individual_id"] = "familiar_two"
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=600,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, MechanicsRejected)):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def test_familiar_rolls_own_initiative_moves_on_own_turn_and_cannot_attack(monkeypatch):
    from tests.srd_full.test_srd_attack_budget import _npc_attack

    state, data, params = _familiar_case()
    _finish_familiar_cast(state, data, params)
    entities = state[f"{SRD}.entities"]
    entities["hero"]["initiative_modifier"] = (data["abilities"]["dexterity"] - 10) // 2
    entities["guard"]["initiative_modifier"] = 0
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "familiar_one", "guard"],
    )
    combat = state[f"{SRD}.combat"]
    entries = {row["entity_id"]: row for row in combat["order"]}
    assert entries["familiar_one"]["modifier"] == 2
    assert entries["familiar_one"]["roll"] != entries["hero"]["roll"]
    while state[f"{SRD}.combat"]["current_actor"] != "familiar_one":
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=actor)
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
    _run("begin_combat_turn", state, actor="familiar_one")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Familiar cannot attack"):
        _npc_attack(
            state,
            "Bite",
            actor="familiar_one",
            target="guard",
            attack_modifier=4,
            use_multiattack=False,
        )
    assert state == before
    _run("move_srd_scene_creature", state, actor_state="familiar_one", destination=[8, 0, 0])
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    assert next(row for row in scene if row["id"] == "familiar_one")["position"] == [8, 0, 0]
    assert state[f"{SRD}.combat"]["movement_remaining"] == 2
    entities = state[f"{SRD}.entities"]
    entities["hero"]["is_alive"] = False
    _run("record_srd_actual_death", state, target_state="hero")
    assert state[f"{SRD}.entities"]["familiar_one"]["created_active"] is True


def test_public_familiar_long_cast_reopens_chooses_source_body_and_retries(tmp_path, monkeypatch):
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_extended_casting import _begin

    monkeypatch.setattr(public, "_spell_case", lambda _spell: _familiar_case())
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "familiar.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "find_familiar")
        command = ActionCommand(
            campaign_id="casting",
            action_id="familiar-begin",
            idempotency_key="familiar-begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )
        receipt = StatelessMcpMechanics(owner).execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        saved = owner.snapshot("casting")
        assert "familiar_one" not in saved.state[f"{SRD}.entities"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 3
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command) == receipt

        def act(identity, tool_name, **choices):
            action = replace(
                command,
                action_id=identity,
                idempotency_key=identity,
                tool_name=tool_name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            result = mechanics.execute_action(action)
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == result
            return action, result

        act(
            "familiar-continue",
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert "familiar_one" not in owner.snapshot("casting").state[f"{SRD}.entities"]
        completion, completed = act(
            "familiar-complete",
            "cast_spell_outside_combat",
            **(parameters | {"familiar_form_id": "weasel", "created_creature_type": "fey"}),
        )
        assert mechanics.execute_action(completion) == completed
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["familiar_one"]
        assert body["created_form_id"] == "weasel" and body["creature_type"] == "fey"
        assert body["darkvision_feet"] == 60 and body["climb_speed"] == 30
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["spell_slot_1"] == 2
        assert hero["spell_materials"][0]["quantity"] == 2
        assert hero["concentration_spell_id"] is None
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(completion) == completed

        def act(identity, tool_name, **choices):
            action = replace(
                command,
                action_id=identity,
                idempotency_key=identity,
                tool_name=tool_name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            result = mechanics.execute_action(action)
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == result
            return result

        pocket_command = replace(
            command,
            action_id="familiar-pocket",
            idempotency_key="familiar-pocket",
            tool_name="dismiss_srd_familiar",
            expected_revision=owner.snapshot("casting").revision,
            parameters={"caster_state": "hero", "familiar_state": "familiar_one"},
        )
        pocketed = mechanics.execute_action(pocket_command)
        assert pocketed.status is ActionStatus.COMMITTED
        assert pocketed.result["result_card"]["title"] == "Familiar disappearance"
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["familiar_one"]["created_disappearance"] == "pocket"
        assert saved.state[f"{SRD}.entities"]["familiar_one"]["death_tick"] is None
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(pocket_command) == pocketed
        returned = act(
            "familiar-reappear",
            "reappear_srd_familiar",
            caster_state="hero",
            familiar_state="familiar_one",
            position=[5, 0, 0],
        )
        assert returned.result["result_card"]["title"] == "Familiar reappearance"
        body = owner.snapshot("casting").state[f"{SRD}.entities"]["familiar_one"]
        assert body["created_active"] and body["created_individual_id"] == "familiar_one"
        assert body["created_form_id"] == "weasel" and body["death_tick"] is None
        telepathy = act(
            "familiar-communicate",
            "communicate_srd_familiar",
            caster_state="hero",
            familiar_state="familiar_one",
            communication="The passage is clear.",
            from_familiar=True,
        )
        assert telepathy.result["result_card"]["summary"][0]["value"] is True
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["hero"][
                "received_familiar_communication"
            ]["sender"]
            == "familiar_one"
        )
        # The returned source body retains its ordinary independent initiative and movement.
        act(
            "familiar-start-combat",
            "start_group_combat_encounter",
            combatants=["hero", "familiar_one", "guard"],
        )
        order = owner.snapshot("casting").state[f"{SRD}.combat"]["order"]
        assert next(row for row in order if row["entity_id"] == "familiar_one")["modifier"] == 3
        for index in range(3):
            actor = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            act(f"familiar-turn-{index}", "begin_combat_turn")
            if actor == "familiar_one":
                break
            act(f"familiar-turn-end-{index}", "end_combat_turn")
            act(f"familiar-turn-next-{index}", "advance_group_combat_encounter_turn")
        else:
            pytest.fail("The Familiar did not receive its independent actual turn.")
        act(
            "familiar-move",
            "move_srd_scene_creature",
            actor_state="familiar_one",
            destination=[8, 0, 0],
        )
        saved = owner.snapshot("casting")
        scene = saved.state[f"{SRD}.state"]["spatial_scene"]["entities"]
        assert next(row for row in scene if row["id"] == "familiar_one")["position"] == [8, 0, 0]
        assert saved.state[f"{SRD}.combat"]["movement_remaining"] == 27
        dodge_command = replace(
            command,
            action_id="familiar-dodge",
            idempotency_key="familiar-dodge",
            tool_name="dodge_srd_familiar",
            expected_revision=saved.revision,
            parameters={"actor_state": "familiar_one"},
        )
        dodged = mechanics.execute_action(dodge_command)
        assert dodged.status is ActionStatus.COMMITTED
        assert dodged.result["result_card"]["title"] == "Dodge"
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["familiar_one"]["dodging"]
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert owner.execute_action(dodge_command) == dodged
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(dodge_command) == dodged


def test_familiar_pocket_return_preserves_actual_individual_body_and_leaves_carried_items():
    state, data, params = _familiar_case("eagle")
    _finish_familiar_cast(state, data, params)
    body = state[f"{SRD}.entities"]["familiar_one"]
    body.update(
        hp=3,
        temp_hp=2,
        inventory=["small_token"],
        held_items=["small_token"],
        conditions=["poisoned"],
        logical_soul_reference="saved_spirit",
    )
    original_slots = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"]
    _run("dismiss_srd_familiar", state, caster_state="hero", familiar_state="familiar_one")
    body = state[f"{SRD}.entities"]["familiar_one"]
    assert body["created_disappearance"] == "pocket" and body["created_active"] is False
    assert body["hp"] == 3 and body["temp_hp"] == 2 and body["conditions"] == ["poisoned"]
    assert body["death_tick"] is None and body["logical_soul_reference"] == "saved_spirit"
    assert body["inventory"] == [] and body["held_items"] == []
    lasting = state[f"{SRD}.state"]
    assert lasting["created_actor_dropped_items"] == [
        {
            "item_id": "small_token",
            "position": [5, 0, 0],
            "plane": "material",
            "former_carrier": "familiar_one",
        }
    ]
    assert state[f"{SRD}.entities"]["hero"]["created_familiar_id"] == "familiar_one"
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, MechanicsRejected)):
        _run(
            "reappear_srd_familiar",
            state,
            caster_state="hero",
            familiar_state="familiar_one",
            position=[31, 0, 0],
        )
    assert state == before
    _run(
        "reappear_srd_familiar",
        state,
        caster_state="hero",
        familiar_state="familiar_one",
        position=[30, 0, 0],
    )
    body = state[f"{SRD}.entities"]["familiar_one"]
    assert body["created_active"] and body["is_alive"] and body["body_available"]
    assert body["created_disappearance"] == "" and body["created_individual_id"] == "familiar_one"
    assert body["hp"] == 3 and body["temp_hp"] == 2 and body["conditions"] == ["poisoned"]
    assert body["death_tick"] is None and body["logical_soul_reference"] == "saved_spirit"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == original_slots
    assert len(state[f"{SRD}.state"]["created_actor_dropped_items"]) == 1
    rows = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    assert len([row for row in rows if row["id"] == "familiar_one"]) == 1
    assert next(row for row in rows if row["id"] == "familiar_one")["position"] == [30, 0, 0]


@pytest.mark.parametrize("disappearance", ["zero_hit_points", "forever"])
def test_familiar_no_cost_reappearance_rejects_actual_zero_hp_or_forever(disappearance):
    state, data, params = _familiar_case()
    _finish_familiar_cast(state, data, params)
    if disappearance == "zero_hit_points":
        _run(
            "apply_srd_damage",
            state,
            target="familiar_one",
            amount=1,
            damage_type="force",
            critical_hit=False,
            adjustment=0,
        )
    else:
        _run("dismiss_srd_familiar", state, caster_state="hero", familiar_state="familiar_one")
        _run(
            "dismiss_srd_familiar",
            state,
            caster_state="hero",
            familiar_state="familiar_one",
            forever=True,
        )
        assert state[f"{SRD}.entities"]["hero"]["created_familiar_id"] is None
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, MechanicsRejected)):
        _run(
            "reappear_srd_familiar",
            state,
            caster_state="hero",
            familiar_state="familiar_one",
            position=[5, 0, 0],
        )
    assert state == before
    assert state[f"{SRD}.entities"]["familiar_one"]["created_disappearance"] == disappearance


@pytest.mark.parametrize("problem", ["wrong_caster", "resized", "worn_gear", "occupied"])
def test_familiar_pocket_source_or_return_geometry_rejection_is_atomic(problem):
    state, data, params = _familiar_case()
    _finish_familiar_cast(state, data, params)
    if problem == "resized":
        state[f"{SRD}.entities"]["familiar_one"]["size_rank"] = 2
    elif problem == "worn_gear":
        state[f"{SRD}.entities"]["familiar_one"]["worn_armor"] = "small_armor"
    if problem == "occupied":
        _run("dismiss_srd_familiar", state, caster_state="hero", familiar_state="familiar_one")
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, MechanicsRejected)):
        if problem == "occupied":
            _run(
                "reappear_srd_familiar",
                state,
                caster_state="hero",
                familiar_state="familiar_one",
                position=[20, 0, 0],
            )
        else:
            _run(
                "dismiss_srd_familiar",
                state,
                caster_state="guard" if problem == "wrong_caster" else "hero",
                familiar_state="familiar_one",
            )
    assert state == before


def test_familiar_pocket_actions_use_caster_budget_and_saved_independent_order(monkeypatch):
    state, data, params = _familiar_case()
    _finish_familiar_cast(state, data, params)
    entities = state[f"{SRD}.entities"]
    entities["hero"]["initiative_modifier"] = (data["abilities"]["dexterity"] - 10) // 2
    entities["guard"]["initiative_modifier"] = 0
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "familiar_one", "guard"],
    )
    for _ in range(3):
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=actor)
        if actor == "hero":
            break
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
    else:
        pytest.fail("The actual caster did not receive its own turn.")
    _run("dismiss_srd_familiar", state, caster_state="hero", familiar_state="familiar_one")
    assert state[f"{SRD}.combat"]["action_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "reappear_srd_familiar",
            state,
            caster_state="hero",
            familiar_state="familiar_one",
            position=[5, 0, 0],
        )
    assert state == before
    for _ in range(4):
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
        actor = state[f"{SRD}.combat"]["current_actor"]
        assert actor != "familiar_one"
        _run("begin_combat_turn", state, actor=actor)
        if actor == "hero":
            break
    else:
        pytest.fail("The actual caster did not receive its next turn.")
    _run(
        "reappear_srd_familiar",
        state,
        caster_state="hero",
        familiar_state="familiar_one",
        position=[5, 0, 0],
    )
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert "familiar_one" in {row["entity_id"] for row in state[f"{SRD}.combat"]["order"]}
    assert state[f"{SRD}.entities"]["familiar_one"]["created_active"]


def test_familiar_return_rejects_missing_real_initiative_insertion_atomically():
    state, data, params = _familiar_case()
    _finish_familiar_cast(state, data, params)
    _run("dismiss_srd_familiar", state, caster_state="hero", familiar_state="familiar_one")
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = (
        data["abilities"]["dexterity"] - 10
    ) // 2
    state[f"{SRD}.entities"]["guard"]["initiative_modifier"] = 0
    entities = state[f"{SRD}.entities"]
    entities["guard_two"] = deepcopy(entities["guard"])
    rows = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    other = deepcopy(next(row for row in rows if row["id"] == "guard"))
    other.update(id="guard_two", position=[20, 5, 0])
    other["metadata"]["participant_ref"] = "guard_two"
    rows.append(other)
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "guard", "guard_two"],
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="initiative insertion"):
        _run(
            "reappear_srd_familiar",
            state,
            caster_state="hero",
            familiar_state="familiar_one",
            position=[5, 0, 0],
        )
    assert state == before


def test_familiar_spends_its_printed_hit_die_after_a_real_tracked_short_rest(monkeypatch):
    state, data, params = _familiar_case("eagle")
    _finish_familiar_cast(state, data, params)
    _run(
        "apply_srd_damage",
        state,
        target="familiar_one",
        amount=3,
        damage_type="force",
        critical_hit=False,
        adjustment=0,
    )
    assert state[f"{SRD}.entities"]["familiar_one"]["hp"] == 1
    _run("begin_srd_rest", state, target_state="familiar_one", kind="short", activity="light")
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    _run("complete_srd_rest", state, target_state="familiar_one")
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    result = _run("take_srd_short_rest_hit_die", state, target_state="familiar_one")
    body = state[f"{SRD}.entities"]["familiar_one"]
    assert body["hp"] == 4 and body["resources"]["hit_die"] == 0
    assert result.result["result_card"]["equation"]["die_sides"] == 6
    assert result.result["result_card"]["equation"]["modifier"] == 1
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("take_srd_short_rest_hit_die", state, target_state="familiar_one")
    assert state == before


@pytest.mark.parametrize("problem", ["none", "speed_zero", "incapacitated", "wrong_source"])
def test_familiar_ordinary_dodge_uses_its_actual_turn_rules_and_budget(problem, monkeypatch):
    state, data, params = _familiar_case("eagle")
    _finish_familiar_cast(state, data, params)
    entities = state[f"{SRD}.entities"]
    entities["hero"]["initiative_modifier"] = (data["abilities"]["dexterity"] - 10) // 2
    entities["guard"]["initiative_modifier"] = 0
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "familiar_one", "guard"],
    )
    for _ in range(3):
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=actor)
        if actor == "familiar_one":
            break
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
    else:
        pytest.fail("The genuine Familiar did not receive its own turn.")
    body = state[f"{SRD}.entities"]["familiar_one"]
    if problem == "speed_zero":
        body["spell_speed_reduction"] = body["speed"]
    elif problem == "incapacitated":
        body["conditions"] = ["incapacitated"]
    elif problem == "wrong_source":
        body["created_caster_state_id"] = "guard"
    before = deepcopy(state)
    if problem != "none":
        with pytest.raises(RulesetPreconditionRejected):
            _run("dodge_srd_familiar", state, actor_state="familiar_one")
        assert state == before
        return
    result = _run("dodge_srd_familiar", state, actor_state="familiar_one")
    assert state[f"{SRD}.entities"]["familiar_one"]["dodging"]
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert result.result["result_card"]["title"] == "Dodge"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("dodge_srd_familiar", state, actor_state="familiar_one")
    assert state == before


@pytest.mark.parametrize("from_familiar", [False, True])
def test_familiar_telepathy_uses_real_range_and_recipient_through_an_opaque_wall(from_familiar):
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, data, params = _familiar_case()
    _finish_familiar_cast(state, data, params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(row for row in scene["entities"] if row["id"] == "familiar_one")["position"] = [100, 0, 0]
    scene["geometry"].append(_wall(2))
    result = _run(
        "communicate_srd_familiar",
        state,
        caster_state="hero",
        familiar_state="familiar_one",
        communication="Watch the door.",
        from_familiar=from_familiar,
    )
    recipient = "hero" if from_familiar else "familiar_one"
    sender = "familiar_one" if from_familiar else "hero"
    assert state[f"{SRD}.entities"][recipient]["received_familiar_communication"] == {
        "caster": "hero",
        "sender": sender,
        "communication": "Watch the door.",
        "tick": 1200,
    }
    assert state[f"{SRD}.entities"]["familiar_one"]["languages"] == []
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
    assert result.result["result_card"]["summary"][0]["label"] == "Understood"
    assert result.result["result_card"]["summary"][0]["value"] is True


@pytest.mark.parametrize("problem", ["range", "vertical_range", "plane", "pocket", "unconscious"])
def test_familiar_telepathy_rejects_actual_source_or_distance_failure_atomically(problem):
    state, data, params = _familiar_case()
    _finish_familiar_cast(state, data, params)
    if problem == "pocket":
        _run("dismiss_srd_familiar", state, caster_state="hero", familiar_state="familiar_one")
    else:
        row = next(
            row
            for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if row["id"] == "familiar_one"
        )
        if problem == "range":
            row["position"] = [100.01, 0, 0]
        elif problem == "vertical_range":
            row["position"] = [0, 0, 100.01]
        elif problem == "plane":
            row["metadata"]["plane"] = "fey"
        else:
            state[f"{SRD}.entities"]["familiar_one"]["conditions"] = ["unconscious"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "communicate_srd_familiar",
            state,
            caster_state="hero",
            familiar_state="familiar_one",
            communication="Watch the door.",
        )
    assert state == before


def _steed_case(level=2, creature_type="celestial", individual="steed_one", **choices):
    state, data, params = _information_case("find_steed")
    _free(state)
    params.pop("target")
    params.update(
        slot_level=level,
        slot_resource=f"spell_slot_{level}",
        created_individual_id=individual,
        created_creature_type=creature_type,
        created_actor_position=[10, 0, 0],
        created_actor_height=8,
    )
    state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{level}"] = 4
    params.update(choices)
    return state, data, params


@pytest.mark.parametrize("level", [2, 4, 9])
@pytest.mark.parametrize(
    "creature_type,damage_type",
    [
        ("celestial", "radiant"),
        ("fey", "psychic"),
        ("fiend", "necrotic"),
    ],
)
def test_steed_fixed_profile_uses_actual_source_caster_and_spell_level(
    level,
    creature_type,
    damage_type,
):
    validate_profiles({"find_steed": STEED_PROFILE})
    state, data, params = _steed_case(level, creature_type)
    before_pb = 2 + (data["level"] - 1) // 4
    result = _run("cast_spell_outside_combat", state, participant_data=data, **params)
    body = state[f"{SRD}.entities"]["steed_one"]
    assert body["hp"] == body["max_hp"] == 5 + level * 10
    assert body["armor_class"] == 10 + level
    assert body["speed"] == 60
    assert body["flying_speed"] == (60 if level >= 4 else 0)
    assert body["proficiency_bonus"] == before_pb
    assert body["ability_scores"] == dict(
        zip(
            ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"),
            (18, 12, 14, 6, 12, 8),
            strict=True,
        )
    )
    assert body["saving_throw_modifiers"] == dict(
        zip(
            ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"),
            (4, 1, 2, -2, 1, -1),
            strict=True,
        )
    )
    assert body["created_attack_damage_type"] == damage_type
    assert body["created_active"] and body["summon_active"] is False
    assert body["challenge_rating_eighths"] is None
    assert state[f"{SRD}.entities"]["hero"].get("concentration_spell_id") is None
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{level}"] == 3
    assert result.result["result_card"]["details"]


def test_steed_replacement_and_saved_individual_return_have_distinct_ownership():
    state, data, params = _steed_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    body = state[f"{SRD}.entities"]["steed_one"]
    body["inventory"] = ["saddle"]
    body["logical_soul_reference"] = "first_individual"
    params["created_individual_id"] = "steed_two"
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    records = state[f"{SRD}.state"]["created_actor_records"]
    assert records["steed_one"]["disappearance"] == "replaced"
    assert records["steed_two"]["active"] is True
    assert state[f"{SRD}.state"]["created_actor_dropped_items"] == [
        {
            "item_id": "saddle",
            "position": [10, 0, 0],
            "plane": "material",
            "former_carrier": "steed_one",
        },
    ]
    params.update(
        created_individual_id="steed_one",
        return_created_individual=True,
        created_creature_type="fey",
        created_actor_position=[12, 0, 0],
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    returned = state[f"{SRD}.entities"]["steed_one"]
    assert returned["created_individual_id"] == "steed_one"
    assert returned["logical_soul_reference"] == "first_individual"
    assert returned["created_active"] and returned["is_alive"]
    assert returned["inventory"] == [] and returned["created_attack_damage_type"] == "psychic"
    assert records["steed_two"]["active"] is True  # Prior local snapshots remain immutable.
    assert state[f"{SRD}.state"]["created_actor_records"]["steed_two"]["active"] is False
    proxies = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    assert sum(row["id"] == "steed_one" for row in proxies) == 1
    assert next(row for row in proxies if row["id"] == "steed_one")["position"] == [12, 0, 0]


@pytest.mark.parametrize(
    "problem", ["existing_npc", "foreign_caster", "active_return", "new_return"]
)
def test_steed_identity_rejection_is_atomic_and_cannot_reset_an_existing_npc(problem):
    state, data, params = _steed_case()
    if problem == "existing_npc":
        params["created_individual_id"] = "guard"
    elif problem == "new_return":
        params["return_created_individual"] = True
    else:
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
        params["return_created_individual"] = True
        if problem == "foreign_caster":
            _run(
                "retire_srd_created_actor", state, actor_state="steed_one", reason="zero_hit_points"
            )
            state[f"{SRD}.state"]["created_actor_records"]["steed_one"]["caster"] = "guard"
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, MechanicsRejected)):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def test_steed_disappears_at_actual_zero_hp_and_caster_death_without_a_corpse():
    state, data, params = _steed_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _run(
        "apply_srd_damage",
        state,
        target="steed_one",
        amount=25,
        damage_type="force",
        critical_hit=False,
        adjustment=0,
    )
    body = state[f"{SRD}.entities"]["steed_one"]
    assert not body["created_active"] and body["body_available"] is False
    assert body["created_disappearance"] == "zero_hit_points"
    params["return_created_individual"] = True
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["steed_one"]["hp"] == 25
    assert state[f"{SRD}.entities"]["steed_one"]["death_tick"] is None
    state[f"{SRD}.entities"]["hero"]["is_alive"] = False
    _run("record_srd_actual_death", state, target_state="hero")
    assert state[f"{SRD}.entities"]["steed_one"]["created_disappearance"] == "caster_died"
    assert state[f"{SRD}.entities"]["hero"]["created_steed_id"] is None


def _steed_slam_parameters(body):
    return {
        "actor": "steed_one",
        "target": "guard",
        "attack_name": "Otherworldly Slam",
        "attack_modifier": body["created_attack_bonus"],
        "damage_expression": "d8",
        "damage_modifier": 2,
        "damage_type": body["created_attack_damage_type"],
        "attack_distance": 5,
        "attack_mode": "melee",
        "normal_range": 5,
        "long_range": 5,
        "actor_can_see_target": True,
        "target_can_see_actor": True,
        "target_is_charmer": False,
        "fear_source_in_sight": False,
        "advantage": False,
        "disadvantage": False,
    }


def _steed_turn(creature_type="celestial"):
    state, data, params = _steed_case(creature_type=creature_type)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        group_order=True,
        turn_active=False,
        current_actor="steed_one",
        active_actor=None,
        phase="awaiting_turn_start",
        order=[{"entity_id": "hero"}, {"entity_id": "steed_one"}, {"entity_id": "guard"}],
        turn_index=1,
        round=1,
        lasting_epoch_round=1,
        lasting_epoch_tick=0,
    )
    _run("begin_combat_turn", state, actor="steed_one")
    body = state[f"{SRD}.entities"]["steed_one"]
    return state, data, params, _steed_slam_parameters(body)


@pytest.mark.parametrize("creature_type", ["celestial", "fey", "fiend"])
def test_steed_later_slam_uses_saved_source_and_actual_body_reach(creature_type, monkeypatch):
    state, _, _, attack = _steed_turn(creature_type)
    monkeypatch.setattr(dice, "_rng", Random(5))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    result = _run("resolve_adjudicated_creature_attack", state, **attack)
    body = state[f"{SRD}.entities"]["steed_one"]
    details = {row["label"]: row["value"] for row in result.result["result_card"]["details"]}
    assert int(details["Stat block modifier"]) == body["created_attack_bonus"] == 10
    assert details["Damage type"] == attack["damage_type"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 19
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert body.get("summon_command") is None


@pytest.mark.parametrize("problem", ["modifier", "damage_type", "fake_distance", "actual_range"])
def test_steed_slam_cannot_substitute_stats_or_caller_range_verdicts(problem):
    state, _, _, attack = _steed_turn()
    if problem == "modifier":
        attack["attack_modifier"] += 1
    elif problem == "damage_type":
        attack["damage_type"] = "fire"
    elif problem == "fake_distance":
        attack["attack_distance"] = 1
    else:
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [21, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="source|distance"):
        _run("resolve_adjudicated_creature_attack", state, **attack)
    assert state == before


def test_steed_shares_initiative_and_native_advance_skips_its_disappeared_body():
    state, data, params = _steed_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = (
        data["abilities"]["dexterity"] - 10
    ) // 2
    state[f"{SRD}.entities"]["guard"]["initiative_modifier"] = 0
    _run(
        "start_group_combat_encounter",
        state,
        combatants=["hero", "guard", "steed_one"],
        participant_data=data,
    )
    order = state[f"{SRD}.combat"]["order"]
    caster_index = next(i for i, row in enumerate(order) if row["entity_id"] == "hero")
    assert order[caster_index + 1]["entity_id"] == "steed_one"
    assert order[caster_index + 1]["initiative_result"] == order[caster_index]["initiative_result"]
    state[f"{SRD}.combat"].update(
        turn_active=False,
        current_actor="hero",
        active_actor=None,
        turn_index=caster_index,
    )
    _run(
        "apply_srd_damage",
        state,
        target="steed_one",
        amount=25,
        adjustment=0,
        damage_type="force",
        critical_hit=False,
    )
    _run("advance_group_combat_encounter_turn", state, completed_actor="hero")
    assert state[f"{SRD}.combat"]["current_actor"] == "guard"
    assert next(row for row in state[f"{SRD}.combat"]["order"] if row["entity_id"] == "steed_one")


def test_steed_source_placement_permits_a_far_passable_creature():
    state, data, params = _steed_case()
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["movement"] = "passable"
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["steed_one"]["created_active"]
    assert state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["movement"] == "passable"


def test_steed_saved_return_during_caster_turn_rejoins_the_actual_shared_count():
    state, data, params = _steed_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = 20
    state[f"{SRD}.entities"]["guard"]["initiative_modifier"] = 0
    _run(
        "start_group_combat_encounter",
        state,
        combatants=["hero", "guard", "steed_one"],
        participant_data=data,
    )
    _run(
        "apply_srd_damage",
        state,
        target="steed_one",
        amount=25,
        adjustment=0,
        damage_type="force",
        critical_hit=False,
    )
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    params["return_created_individual"] = True
    _run("cast_spell", state, participant_data=data, **params)
    order = state[f"{SRD}.combat"]["order"]
    assert [row["entity_id"] for row in order] == ["hero", "steed_one", "guard"]
    assert order[0]["initiative_result"] == order[1]["initiative_result"]
    assert state[f"{SRD}.combat"]["active_actor"] == "hero"
    assert state[f"{SRD}.entities"]["steed_one"]["created_active"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2


def test_public_steed_reopens_retries_replaces_returns_and_uses_its_saved_slam(
    tmp_path,
    monkeypatch,
):
    from tests import test_srd_public_spell_casting as public

    monkeypatch.setattr(public, "_spell_case", lambda _spell: _steed_case())
    monkeypatch.setattr(dice, "_rng", Random(5))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "steed.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "find_steed",
            target_overrides={"hp": 100, "max_hp": 100},
        )
        parameters.pop("slot_level")
        command = ActionCommand(
            campaign_id="casting",
            action_id="steed",
            idempotency_key="steed",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = StatelessMcpMechanics(owner).execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(command) == receipt

        def act(identity, tool_name, **choices):
            action = replace(
                command,
                action_id=identity,
                idempotency_key=identity,
                tool_name=tool_name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            result = mechanics.execute_action(action)
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == result
            return action, result

        act(
            "replace-steed",
            "cast_spell_outside_combat",
            **(parameters | {"created_individual_id": "steed_two"}),
        )
        act(
            "return-steed",
            "cast_spell_outside_combat",
            **(
                parameters
                | {
                    "return_created_individual": True,
                    "created_creature_type": "fey",
                }
            ),
        )
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 1
        assert saved.state[f"{SRD}.entities"]["steed_one"]["created_individual_id"] == "steed_one"
        assert saved.state[f"{SRD}.entities"]["steed_two"]["created_active"] is False
        act(
            "start-steed-encounter",
            "start_group_combat_encounter",
            combatants=["hero", "steed_one", "guard"],
        )
        act("caster-begin", "begin_combat_turn")
        act("caster-end", "end_combat_turn")
        act("to-steed", "advance_group_combat_encounter_turn")
        act("steed-begin", "begin_combat_turn")
        body = owner.snapshot("casting").state[f"{SRD}.entities"]["steed_one"]
        attack = _steed_slam_parameters(body)
        attack.pop("actor")
        slam, result = act("steed-slam", "resolve_adjudicated_creature_attack", **attack)
        details = {row["label"]: row["value"] for row in result.result["result_card"]["details"]}
        assert int(details["Stat block modifier"]) == body["created_attack_bonus"]
        assert details["Damage type"] == "psychic"
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["guard"]["hp"] < 100
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert "resolve_adjudicated_creature_attack" not in {
            tool.name for tool in StatelessMcpMechanics(owner).list_tools("casting")
        }
        assert owner.execute_action(slam) == result
        assert owner.snapshot("casting").state == saved.state


def _created_volume_case(kind="box", position=None, **facts):
    state, data, params = _dragon_case()
    row = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]
    row.update(movement="passable", visibility="clear", position=position or [10, 0, 0])
    row["footprint"] = (
        {"kind": "box", "half_extents": [1, 1]}
        if kind == "box"
        else {"kind": "circle", "radius": 1}
    )
    row.update(facts)
    return state, data, params


@pytest.mark.parametrize(
    "kind,position",
    [
        ("box", [10, 0, 0]),
        ("box", [15.9, 0, 0]),
        ("circle", [10, 0, 0]),
        ("circle", [15.9, 0, 0]),
    ],
)
def test_created_source_space_rejects_actual_living_interiors_even_when_passable(kind, position):
    state, data, params = _created_volume_case(kind, position)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="unoccupied.*living creature interiors"):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


@pytest.mark.parametrize(
    "kind,position",
    [
        ("box", [4, 0, 0]),
        ("box", [4, 6, 0]),
        ("box", [10, 0, 8]),
        ("circle", [16, 0, 0]),
        ("circle", [10, 0, 8]),
    ],
)
def test_created_source_space_permits_actual_face_edge_and_curved_tangency(kind, position):
    state, data, params = _created_volume_case(kind, position)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["dragon_one"]["summon_active"]
    assert state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["movement"] == "passable"


@pytest.mark.parametrize(
    "reason",
    ["inactive", "dead", "another_plane", "untyped", "scene_alias", "missing_actor", "force"],
)
def test_created_source_occupancy_qualifies_real_body_identity_life_and_plane(reason):
    state, data, params = _created_volume_case()
    row = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]
    if reason == "inactive":
        row["active"] = False
    elif reason == "dead":
        state[f"{SRD}.entities"]["guard"]["is_alive"] = False
    elif reason == "another_plane":
        row["metadata"]["plane"] = "ethereal"
    elif reason == "untyped":
        state[f"{SRD}.entities"]["guard"]["creature_type"] = None
    elif reason == "missing_actor":
        row["metadata"]["participant_ref"] = None
    elif reason == "force":
        row["metadata"].update(participant_ref=None, creature_type="spell_force")
    else:
        row["id"] = "actual-guard-body"
    before = deepcopy(state)
    if reason in {"untyped", "scene_alias", "missing_actor"}:
        with pytest.raises(
            RulesetPreconditionRejected,
            match="actual type|living creature interiors|bound mechanics",
        ):
            _run("cast_spell_outside_combat", state, participant_data=data, **params)
        assert state == before
    else:
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
        assert state[f"{SRD}.entities"]["dragon_one"]["summon_active"]


def test_created_source_qualifies_64_actual_passable_bodies_without_repeated_scene_scans(
    monkeypatch,
):
    from sylvia_gm.runtime._types import ExecutionBudget
    from tests.srd_full.test_srd_spell_wave3_information import _entity

    budgets = []
    original = ExecutionBudget.consume_operation

    def observe(budget):
        original(budget)
        if not budgets:
            budgets.append(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", observe)
    state, data, params = _created_volume_case("circle", [16, 0, 0])
    actors = state[f"{SRD}.entities"]
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    for index in range(63):
        actor = f"passable-{index}"
        actors[actor] = deepcopy(actors["guard"])
        scene.append(_entity(actor, [16, 0, 0], participant_ref=actor, creature_type="humanoid"))
        scene[-1].update(
            movement="passable", visibility="clear", footprint={"kind": "circle", "radius": 1}
        )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["dragon_one"]["summon_active"]
    assert (
        sum(
            row["movement"] == "passable"
            for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        )
        == 64
    )
    assert budgets[0].max_operations == 10000
    assert budgets[0].operations <= 10000
    print(f"Actual created source with 64 passable bodies: {budgets[0].operations} operations.")


def test_public_created_source_passable_body_tangency_reopens_and_retries(tmp_path, monkeypatch):
    from tests import test_srd_public_spell_casting as public

    monkeypatch.setattr(
        public, "_spell_case", lambda _spell: _created_volume_case("circle", [16, 0, 0])
    )
    database = tmp_path / "living-volume.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "summon_dragon")
        action = ActionCommand(
            campaign_id="casting",
            action_id="volume",
            idempotency_key="volume",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        result = StatelessMcpMechanics(owner).execute_action(action)
        assert result.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["dragon_one"]["summon_active"]
        assert saved.state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["movement"] == "passable"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(owner).execute_action(action) == result
        assert owner.snapshot("casting").state == saved.state


def _case(spell, **choices):
    state, data, params = _information_case(spell)
    _free(state)
    params.pop("target")
    params.update(force_id="force_one", force_position=[20, 0, 0])
    if spell == "unseen_servant":
        params.update(force_position=[5, 0, 0], force_on_ground=True)
    else:
        params["force_target"] = "guard"
    params.update(choices)
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0)
    return state, data, params


def _cast(spell, **choices):
    state, data, params = _case(spell, **choices)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **params)
    return state, data, params, result


def _record(state):
    return state[f"{SRD}.state"]["spell_force_records"]["force_one"]


def _dragon_case(spell="summon_dragon", *, caster_languages=None):
    state, data, params = _information_case(spell)
    _free(state)
    params.pop("target")
    params.update(
        summon_id="dragon_one",
        selected_damage_type="fire",
        summon_distance=5,
        caster_can_see_summon_space=True,
        summon_space_unoccupied=True,
        created_actor_position=[10, 0, 0],
        created_actor_height=8,
    )
    state[f"{SRD}.entities"]["hero"]["languages"] = caster_languages or ["common"]
    state[f"{SRD}.entities"]["guard"]["languages"] = ["common", "draconic", "infernal"]
    for actor, creature_type in (("hero", "humanoid"), ("guard", "fiend"), ("other", "humanoid")):
        state[f"{SRD}.entities"][actor]["creature_type"] = creature_type
    return state, data, params


@pytest.mark.parametrize("known_language", ["common", "elvish"])
def test_dragon_understands_saved_caster_languages_but_speaks_only_draconic(known_language):
    state, data, params = _dragon_case(caster_languages=[known_language])
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    entities = state[f"{SRD}.entities"]
    dragon = entities["dragon_one"]
    assert dragon["languages"] == ["draconic"]
    assert dragon["summon_understood_languages"] == [known_language]
    entities["guard"]["languages"].append(known_language)
    result = _run(
        "communicate_srd_spell",
        state,
        listener_state="dragon_one",
        speaker_state="guard",
        language=known_language,
    ).result["context"]
    assert result["_language_understands"] is True
    assert result["_language_can_reply"] is False
    draconic = _run(
        "communicate_srd_spell",
        state,
        listener_state="dragon_one",
        speaker_state="guard",
        language="draconic",
    ).result["context"]
    assert draconic["_language_understands"] is draconic["_language_can_reply"] is True
    with pytest.raises(RulesetPreconditionRejected, match="cannot understand"):
        _run(
            "communicate_srd_spell",
            state,
            listener_state="dragon_one",
            speaker_state="guard",
            language="infernal",
        )
    entities["dragon_one"]["conditions"].append("deafened")
    with pytest.raises(RulesetPreconditionRejected, match="cannot understand"):
        _run(
            "communicate_srd_spell",
            state,
            listener_state="dragon_one",
            speaker_state="guard",
            language=known_language,
        )


def test_public_dragon_language_reopens_retries_and_ends_with_source(tmp_path, monkeypatch):
    from tests import test_srd_public_spell_casting as public

    monkeypatch.setattr(public, "_spell_case", _dragon_case)
    database = tmp_path / "dragon.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "summon_dragon")
        command = ActionCommand(
            campaign_id="casting",
            action_id="dragon",
            idempotency_key="dragon",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(command) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert saved.state[f"{SRD}.entities"]["dragon_one"]["summon_understood_languages"] == [
            "common"
        ]
        mechanics = StatelessMcpMechanics(owner)
        communication = replace(
            command,
            action_id="language",
            idempotency_key="language",
            expected_revision=saved.revision,
            tool_name="communicate_srd_spell",
            parameters={
                "listener_state": "dragon_one",
                "speaker_state": "guard",
                "language": "common",
            },
        )
        receipt = mechanics.execute_action(communication)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(communication) == receipt
        end = replace(
            command,
            action_id="end",
            idempotency_key="end",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        assert mechanics.execute_action(end).status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["dragon_one"]["summon_active"] is False
        assert (
            next(
                e
                for e in after.state[f"{SRD}.state"]["spatial_scene"]["entities"]
                if e["id"] == "dragon_one"
            )["active"]
            is False
        )
        closed = replace(
            communication,
            action_id="closed",
            idempotency_key="closed",
            expected_revision=after.revision,
        )
        with pytest.raises(MechanicsRejected, match="cannot understand"):
            mechanics.execute_action(closed)
        assert owner.snapshot("casting").state == after.state


@pytest.mark.parametrize(
    "position,lighting,error",
    [
        ([61, 0, 0], "bright", "outside spell range"),
        ([20, 0, 0], "bright", "unoccupied"),
        ([10, 0, 0], "darkness", "must see"),
    ],
)
def test_dragon_placement_rejects_actual_geometry_without_cost(position, lighting, error):
    state, data, params = _dragon_case()
    params.update(created_actor_position=position, created_actor_lighting=lighting)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=error):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def test_dragon_placement_uses_full_source_size_and_actual_sight():
    state, data, params = _dragon_case()
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [15.3, 0, 0]
    with pytest.raises(RulesetPreconditionRejected, match="full printed footprint"):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [20, 0, 0]
    state[f"{SRD}.entities"]["hero"]["darkvision_feet"] = 60
    params.update(
        created_actor_lighting="darkness",
        summon_distance=1000,
        caster_can_see_summon_space=False,
        summon_space_unoccupied=False,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    dragon = state[f"{SRD}.entities"]["dragon_one"]
    assert dragon["initiative_modifier"] == 2
    scene_dragon = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "dragon_one"
    )
    assert scene_dragon["footprint"] == {"kind": "box", "half_extents": [5, 5]}
    assert scene_dragon["height"] == 8
    assert scene_dragon["position"] == [10, 0, 0]


def _guardian_case(spell="guardian_of_faith"):
    state, data, params = _information_case(spell)
    _free(state)
    params.pop("target")
    params.update(
        force_id="guardian_one", created_actor_position=[10, 0, 0], created_actor_height=8
    )
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["enemy_of"] = ["hero"]
    return state, data, params


def _dragon_attack_turn(state):
    _run(
        "command_srd_summoned_creature",
        state,
        caster_state="hero",
        summon_state="dragon_one",
        command="attack",
    )
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        group_order=True,
        turn_active=False,
        current_actor="dragon_one",
        active_actor=None,
        phase="awaiting_turn_start",
        order=[{"entity_id": "hero"}, {"entity_id": "dragon_one"}, {"entity_id": "guard"}],
        turn_index=1,
        round=1,
        lasting_epoch_round=1,
        lasting_epoch_tick=0,
    )
    _run("begin_combat_turn", state, actor="dragon_one")


@pytest.mark.parametrize(
    "position,affected",
    [
        ([30, 7.9, 0], True),
        ([30, 8.2, 0], False),
        ([45.4, 0, 0], True),
        ([45.5, 0, 0], False),
        ([31, 0, 12], True),
        ([31, 0, 12.5], False),
    ],
)
def test_dragon_breath_uses_actual_source_size_and_whole_target_box(position, affected):
    state, data, params = _dragon_case()
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = position
    state[f"{SRD}.state"]["spatial_scene"]["entities"] = [
        body for body in state[f"{SRD}.state"]["spatial_scene"]["entities"] if body["id"] != "orb"
    ]
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _dragon_attack_turn(state)
    result = _run(
        "resolve_srd_summon_breath", state, actor_state="dragon_one", use_multiattack=True
    )
    assert result.result["context"]["_srd_cone_origin"] == [15, 0, 4]
    assert (state[f"{SRD}.entities"]["guard"]["hp"] < 1000) is affected


def test_dragon_breath_collects_all_creatures_and_preserves_saved_multiattack():
    from tests.srd_full.test_srd_spell_wave3_information import _entity

    state, data, params = _dragon_case()
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["dexterity"] = -30
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("other", [22, -2, 0], participant_ref="other", creature_type="humanoid")
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _dragon_attack_turn(state)
    result = _run(
        "resolve_srd_summon_breath", state, actor_state="dragon_one", use_multiattack=True
    )
    context = result.result["context"]
    assert context["_srd_area_targets"] == ["guard", "other"]
    damage = context["_summon_breath_roll"]["total"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - damage
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000 - damage
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 2
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Multiattack"):
        _run("resolve_srd_summon_breath", state, actor_state="dragon_one", use_multiattack=True)
    assert state == before


@pytest.mark.parametrize("visibility", ["opaque", "clear"])
def test_dragon_breath_physical_wall_requires_categorical_cover_before_action(visibility):
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, data, params = _dragon_case()
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    wall = _wall(0.5)
    wall["volume"] = {"kind": "box", "min": [17, -10, -10], "max": [17.5, 10, 10]}
    wall["visibility"] = visibility
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(wall)
    _dragon_attack_turn(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="categorical Cover"):
        _run("resolve_srd_summon_breath", state, actor_state="dragon_one", use_multiattack=True)
    assert state == before
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["cover_from"] = {
        "dragon_one": "total"
    }
    _run("resolve_srd_summon_breath", state, actor_state="dragon_one", use_multiattack=True)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert state[f"{SRD}.combat"]["action_available"] is False


@pytest.mark.parametrize("cover,bonus", [("half", 2), ("three_quarters", 5)])
def test_dragon_breath_partial_cover_uses_existing_save_bonus(cover, bonus):
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, data, params = _dragon_case()
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=1000, max_hp=1000, temp_hp=0)
    target["saving_throw_modifiers"]["dexterity"] = 30
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    wall = _wall(0.5)
    wall["volume"] = {"kind": "box", "min": [17.5, -1, 0], "max": [18, 1, 3]}
    scene["geometry"].append(wall)
    scene["entities"][1]["metadata"]["cover_from"] = {"dragon_one": cover}
    _dragon_attack_turn(state)
    result = _run(
        "resolve_srd_summon_breath", state, actor_state="dragon_one", use_multiattack=True
    )
    assert f"+ ({30 + bonus})" in str(result.result["context"]["_summon_breath_rows"])
    assert (
        state[f"{SRD}.entities"]["guard"]["hp"]
        == 1000 - result.result["context"]["_summon_breath_roll"]["total"] // 2
    )


@pytest.mark.parametrize("kind", ["body", "wall"])
def test_dragon_breath_other_plane_physical_blocker_does_not_supply_cover(kind):
    from tests.srd_full.test_srd_spell_wave3_information import _entity, _wall

    state, data, params = _dragon_case()
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if kind == "body":
        blocker = _entity("other", [17, 0, 0], participant_ref="other", creature_type="humanoid")
        scene["entities"].append(blocker)
    else:
        blocker = _wall(0.5)
        blocker["volume"] = {"kind": "box", "min": [17, -10, -10], "max": [17.5, 10, 10]}
        scene["geometry"].append(blocker)
    blocker["metadata"]["plane"] = "ethereal"
    _dragon_attack_turn(state)
    result = _run(
        "resolve_srd_summon_breath", state, actor_state="dragon_one", use_multiattack=True
    )
    assert result.result["context"]["_srd_area_targets"] == ["guard"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 1000


def _dragon_sixty_four_case():
    from tests.srd_full.test_srd_spell_wave3_information import _entity

    state, data, params = _dragon_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] = [e for e in scene["entities"] if e["id"] in {"hero", "dragon_one"}]
    for index in range(64):
        actor_id = f"cone_{index}"
        target = deepcopy(state[f"{SRD}.entities"]["guard"])
        target.update(id=actor_id, hp=1000, max_hp=1000, temp_hp=0, creature_type="humanoid")
        state[f"{SRD}.entities"][actor_id] = target
        body = _entity(
            actor_id,
            [44, -13.5 + index * 27 / 63, 0],
            participant_ref=actor_id,
            creature_type="humanoid",
        )
        body["footprint"]["half_extents"] = [0.05, 0.05]
        scene["entities"].append(body)
    return state


def test_dragon_breath_actual_sixty_four_target_membership_stays_bounded():
    state = _dragon_sixty_four_case()
    result = _run("qualify_srd_summon_breath_targets", state, actor_state="dragon_one", apex=[])
    assert result.result["context"]["_srd_area_targets"] == [f"cone_{i}" for i in range(64)]


@pytest.mark.parametrize("direction", [[1, 0], [0, 1], [-1, 0], [1, 1]])
def test_area_cover_forward_certificate_uses_the_actual_selected_cone_frame(direction):
    state, data, params = _dragon_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] = [body for body in scene["entities"] if body["id"] != "orb"]
    scene["entities"][0]["position"] = [500, 500, 0]
    scene["entities"][1]["position"] = [10 + direction[0] * 20, direction[1] * 20, 0]
    cone = _run(
        "collect_srd_cone_creature_targets",
        state,
        source_state="dragon_one",
        length_feet=30,
        direction=direction,
    ).result["context"]
    result = _run(
        "collect_srd_spell_area_cover_targets",
        state,
        source_state="dragon_one",
        targets=cone["_srd_cone_targets"],
        origin=cone["_srd_cone_origin"],
        axis=cone["_srd_cone_axis"],
        source_frames=[cone["_srd_cone_source"]],
    ).result["context"]
    assert result["_srd_area_targets"] == ["guard"]
    assert result["_srd_area_half_cover"] == result["_srd_area_three_quarters"] == []


@pytest.mark.parametrize("kind", ["circle_body", "box_geometry"])
def test_area_cover_forward_certificate_preserves_real_physical_blockers(kind):
    from tests.srd_full.test_srd_spell_wave3_information import _entity, _wall

    state, data, params = _dragon_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [30, 0, 0]
    if kind == "circle_body":
        blocker = _entity("obstruction", [17, 0, 0], creature_type="object")
        blocker["metadata"]["is_object"] = True
        blocker["footprint"] = {"kind": "circle", "radius": 0.5}
        scene["entities"].append(blocker)
    else:
        blocker = _wall(0.5)
        blocker["volume"] = {"kind": "box", "min": [17, -1, 0], "max": [18, 1, 10]}
        scene["geometry"].append(blocker)
    cone = _run(
        "collect_srd_cone_creature_targets", state, source_state="dragon_one", length_feet=30
    ).result["context"]
    arguments = {
        "source_state": "dragon_one",
        "targets": cone["_srd_cone_targets"],
        "origin": cone["_srd_cone_origin"],
        "axis": cone["_srd_cone_axis"],
        "source_frames": [cone["_srd_cone_source"]],
    }
    with pytest.raises(RulesetPreconditionRejected, match="categorical Cover"):
        _run("collect_srd_spell_area_cover_targets", state, **arguments)
    scene["entities"][1]["metadata"]["cover_from"] = {"dragon_one": "total"}
    assert (
        _run("collect_srd_spell_area_cover_targets", state, **arguments).result["context"][
            "_srd_area_targets"
        ]
        == []
    )


def test_dragon_breath_actual_sixty_four_saves_and_damage_stay_bounded():
    state = _dragon_sixty_four_case()
    _dragon_attack_turn(state)
    result = _run(
        "resolve_srd_summon_breath", state, actor_state="dragon_one", use_multiattack=True
    )
    assert len(result.result["context"]["_summon_breath_rows"]) == 64
    assert len(result.result["result_card"]["details"]) == 64
    assert all(state[f"{SRD}.entities"][f"cone_{i}"]["hp"] < 1000 for i in range(64))
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 2


@pytest.mark.parametrize(
    "surface,coordinate,accepted",
    [
        ("floor", 0, True),
        ("floor", 0.001, False),
        ("body", 15.4, True),
        ("body", 15.399, False),
        ("ceiling", 8, True),
        ("ceiling", 7.999, False),
        ("map", 15, True),
        ("map", 14.999, False),
    ],
)
def test_created_actor_placement_delegates_true_interior_tangency_and_bounds(
    surface,
    coordinate,
    accepted,
):
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, _, _ = _dragon_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if surface == "body":
        scene["entities"][1]["position"] = [coordinate, 0, 0]
    elif surface == "map":
        scene["entities"] = scene["entities"][:1]
        scene["map"]["bounds"]["max"][0] = coordinate
    else:
        surface_body = _wall(1)
        surface_body["id"] = surface
        lower, upper = (-1, coordinate) if surface == "floor" else (coordinate, 9)
        surface_body["volume"] = {
            "kind": "box",
            "min": [-100, -100, lower],
            "max": [100, 100, upper],
        }
        scene["geometry"].append(surface_body)
    before = deepcopy(state)
    parameters = {
        "caster_state": "hero",
        "summon_id": "placed_actor",
        "spell": "summon_dragon",
        "range_feet": 60,
        "half_extent": 5,
        "creature_type": "dragon",
        "created_actor_position": [10, 0, 0],
        "created_actor_height": 8,
    }
    if not accepted:
        with pytest.raises(RulesetPreconditionRejected, match="unoccupied|clear path"):
            _run("create_srd_source_actor_scene", state, **parameters)
        assert state == before
        return
    _run("create_srd_source_actor_scene", state, **parameters)
    proxy = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "placed_actor"
    )
    assert proxy["position"] == [10, 0, 0]
    assert proxy["footprint"] == {"kind": "box", "half_extents": [5, 5]}
    assert proxy["height"] == 8
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == 1


def test_created_actor_placement_rejects_curved_solids_atomically():
    state, _, _ = _dragon_case()
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["footprint"] = {
        "kind": "circle",
        "radius": 0.4,
    }
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="curved solids"):
        _run(
            "create_srd_source_actor_scene",
            state,
            caster_state="hero",
            summon_id="placed_actor",
            spell="summon_dragon",
            range_feet=60,
            half_extent=5,
            creature_type="dragon",
            created_actor_position=[10, 0, 0],
            created_actor_height=8,
        )
    assert state == before


@pytest.mark.parametrize(
    "spell,requires_sight,accepted",
    [
        ("conjure_elemental", False, True),
        ("conjure_elemental", True, False),
        ("conjure_animals", False, False),
        ("find_steed", False, True),
        ("find_familiar", False, True),
    ],
)
def test_created_actor_sight_exception_is_bound_to_the_printed_source(
    spell,
    requires_sight,
    accepted,
):
    state, _, _ = _dragon_case()
    before = deepcopy(state)
    parameters = {
        "caster_state": "hero",
        "summon_id": "placed_actor",
        "spell": spell,
        "range_feet": 60,
        "half_extent": 5,
        "creature_type": "elemental",
        "created_actor_position": [10, 0, 0],
        "created_actor_height": 8,
        "created_actor_lighting": "darkness",
        "requires_sight": requires_sight,
    }
    if not accepted:
        with pytest.raises(RulesetPreconditionRejected, match="sight|must see"):
            _run("create_srd_source_actor_scene", state, **parameters)
        assert state == before
        return
    _run("create_srd_source_actor_scene", state, **parameters)
    assert state[f"{SRD}.state"]["spatial_scene"]["entities"][-1]["id"] == "placed_actor"


def test_public_created_dragon_on_opaque_floor_reopens_and_retries(tmp_path, monkeypatch):
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    def floor_case(spell):
        state, data, parameters = _dragon_case(spell)
        floor = _wall(1)
        floor["id"] = "floor"
        floor["volume"] = {"kind": "box", "min": [-100, -100, -1], "max": [100, 100, 0]}
        state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(floor)
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", floor_case)
    database = tmp_path / "dragon-floor.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "summon_dragon")
        command = ActionCommand(
            campaign_id="casting",
            action_id="floor-dragon",
            idempotency_key="floor-dragon",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = StatelessMcpMechanics(owner).execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["dragon_one"]["summon_has_scene_proxy"] is True
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state


def test_dragon_breath_native_card_retains_each_save_resistance_and_temporary_hp_result():
    from tests.srd_full.test_srd_spell_wave3_information import _entity

    state, data, params = _dragon_case()
    actors = state[f"{SRD}.entities"]
    actors["guard"].update(hp=1000, max_hp=1000, temp_hp=3, damage_resistances=["fire"])
    actors["guard"]["saving_throw_modifiers"]["dexterity"] = -30
    actors["other"].update(hp=1000, max_hp=1000, temp_hp=1, damage_resistances=[])
    actors["other"]["saving_throw_modifiers"]["dexterity"] = 30
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("other", [22, -2, 0], participant_ref="other", creature_type="humanoid")
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _dragon_attack_turn(state)
    result = _run(
        "resolve_srd_summon_breath", state, actor_state="dragon_one", use_multiattack=True
    )
    actors = state[f"{SRD}.entities"]
    raw = result.result["context"]["_summon_breath_roll"]["total"]
    rows = result.result["context"]["_summon_breath_rows"]
    card = result.result["result_card"]
    assert card["title"] == "Breath Weapon"
    assert len(rows) == len(card["details"]) == 2
    for index, (actor, outcome, temporary) in enumerate(
        [("guard", "failure", 3), ("other", "success", 1)]
    ):
        damage = raw // 2
        temp_lost = min(temporary, damage)
        hp_lost = damage - temp_lost
        assert actors[actor]["hp"] == 1000 - hp_lost
        assert actors[actor]["temp_hp"] == temporary - temp_lost
        assert rows[index]["label"] == actors[actor]["name"]
        assert f": {outcome}. D20" in rows[index]["value"]
        assert f"Damage {damage} (HP {hp_lost}; temporary HP {temp_lost})" in rows[index]["value"]
        assert card["details"][index]["value"] == rows[index]["value"]


@pytest.mark.parametrize("dodging,speed,roll_count", [(True, 30, 2), (True, 0, 1), (False, 30, 1)])
def test_dragon_breath_inlined_inputs_preserve_actual_dodge_and_native_rolls(
    dodging,
    speed,
    roll_count,
):
    state, data, params = _dragon_case()
    target = state[f"{SRD}.entities"]["guard"]
    target.update(
        hp=1000,
        max_hp=1000,
        temp_hp=0,
        damage_resistances=[],
        dodging=dodging,
        speed=speed,
    )
    target["saving_throw_modifiers"]["dexterity"] = -30
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _dragon_attack_turn(state)
    result = _run(
        "resolve_srd_summon_breath",
        state,
        actor_state="dragon_one",
        use_multiattack=True,
    )
    row = result.result["result_card"]["details"][0]["value"]
    raw_rolls = row.split("D20 ", 1)[1].split("; selected", 1)[0]
    rolls = [int(value.strip()) for value in raw_rolls.split(",")]
    assert len(rolls) == roll_count
    assert "+ (-30)" in row
    assert (
        state[f"{SRD}.entities"]["guard"]["hp"]
        == 1000 - result.result["context"]["_summon_breath_roll"]["total"]
    )


def test_dragon_breath_chosen_direction_and_origin_inclusion_remain_actual_choices():
    state, data, params = _dragon_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    direction = _run(
        "qualify_srd_summon_breath_targets",
        state,
        actor_state="dragon_one",
        apex=[],
        direction=[-1, 0],
    ).result["context"]
    assert direction["_srd_cone_origin"] == [5, 0, 4]
    assert direction["_srd_area_targets"] == ["hero"]
    included = _run(
        "qualify_srd_summon_breath_targets",
        state,
        actor_state="dragon_one",
        apex=[],
        include_origin=True,
    ).result["context"]
    assert set(included["_srd_area_targets"]) == {"guard", "dragon_one"}


@pytest.mark.parametrize(
    "choices,error",
    [
        ({"breath_apex": [30, 0, 4]}, "source"),
        ({"breath_direction": [0, 0, 1]}, "horizontal"),
    ],
)
def test_dragon_breath_unsupported_source_choice_rejects_before_spending(choices, error):
    state, data, params = _dragon_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _dragon_attack_turn(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=error):
        _run("resolve_srd_summon_breath", state, actor_state="dragon_one", **choices)
    assert state == before


def test_public_dragon_breath_cast_later_turn_reopen_retry_and_cleanup(tmp_path, monkeypatch):
    from tests import test_srd_public_spell_casting as public

    monkeypatch.setattr(public, "_spell_case", _dragon_case)
    database = tmp_path / "breath.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner,
            "summon_dragon",
            target_overrides={"hp": 1000, "max_hp": 1000},
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="dragon",
            idempotency_key="dragon",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(command).status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)

        def act(name, **parameters):
            action = replace(
                command,
                action_id=name,
                idempotency_key=name,
                tool_name=name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=parameters,
            )
            receipt = mechanics.execute_action(action)
            assert receipt.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == receipt
            return action, receipt

        act("start_group_combat_encounter", combatants=["hero", "dragon_one", "guard"])
        act("begin_combat_turn")
        act(
            "command_srd_summoned_creature",
            caster_state="hero",
            summon_state="dragon_one",
            command="attack",
        )
        act("end_combat_turn")
        act("advance_group_combat_encounter_turn")
        # The actual current actor is bound automatically by the existing public turn owner.
        turn = replace(
            command,
            action_id="dragon-begin",
            idempotency_key="dragon-begin",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="begin_combat_turn",
            parameters={},
        )
        assert mechanics.execute_action(turn).status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "dragon_one"
        breath, receipt = act("resolve_srd_summon_breath", use_multiattack=True)
        assert receipt.result["result_card"]["title"] == "Breath Weapon"
        assert receipt.result["result_card"]["details"]
        assert mechanics.execute_action(breath) == receipt
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["guard"]["hp"] < 1000
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(breath) == receipt
        finish = replace(
            command,
            action_id="end-combat",
            idempotency_key="end-combat",
            expected_revision=saved.revision,
            tool_name="end_combat_encounter",
            parameters={"end_reason": "fighting_stopped"},
        )
        assert mechanics.execute_action(finish).status is ActionStatus.COMMITTED
        end = replace(
            command,
            action_id="end-source",
            idempotency_key="end-source",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        assert mechanics.execute_action(end).status is ActionStatus.COMMITTED
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["dragon_one"]["summon_active"] is False
        assert (
            next(
                body
                for body in final.state[f"{SRD}.state"]["spatial_scene"]["entities"]
                if body["id"] == "dragon_one"
            )["active"]
            is False
        )


def _guardian_begin(state):
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=False,
        current_actor="guard",
        active_actor=None,
        phase="awaiting_turn_start",
    )
    return _run("begin_combat_turn", state, actor="guard")


def _hound_case(spell="faithful_hound", **choices):
    state, data, parameters = _information_case(spell)
    _free(state)
    parameters.pop("target")
    parameters.update(
        force_id="hound_one",
        force_position=[10, 0, 0],
        hound_password="watch",
        hound_lighting="bright",
    )
    parameters.update(choices)
    return state, data, parameters


def _hound_cast(**choices):
    state, data, parameters = _hound_case(**choices)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    return state


def _hound_proxy(state):
    return next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "hound_one"
    )


def test_hound_exact_source_anchor_has_no_invented_creature_stats_and_expires():
    validate_profiles({"faithful_hound": HOUND_PROFILE})
    state = _hound_cast()
    assert "hound_one" not in state[f"{SRD}.entities"]
    record = state[f"{SRD}.state"]["spell_force_records"]["hound_one"]
    assert record["password"] == "watch"
    assert record["expires_tick"] == 9600
    assert _hound_proxy(state)["movement"] == "passable"
    assert _hound_proxy(state)["visibility"] == "clear"
    assert _hound_proxy(state)["metadata"]["truesight_feet"] == 30
    assert _hound_proxy(state)["metadata"]["source_only_visible_to"] == "hero"
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    assert state[f"{SRD}.state"]["spell_force_records"]["hound_one"]["active"] is False
    assert _hound_proxy(state)["active"] is False
    assert state[f"{SRD}.state"]["has_hound_sources"] is False


def _hound_bite_case(*, second=False):
    from tests.srd_full.test_srd_spell_wave3_information import _entity

    state, data, parameters = _hound_case()
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = -30
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [14, 0, 0]
    scene["entities"][1]["metadata"]["enemy_of"] = ["hero"]
    if second:
        state[f"{SRD}.entities"]["other"].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["dexterity"] = -30
        body = _entity("other", [14, 2, 0], participant_ref="other", creature_type="humanoid")
        body["metadata"]["enemy_of"] = ["hero"]
        scene["entities"].append(body)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=False,
        current_actor="hero",
        active_actor=None,
        phase="awaiting_turn_start",
    )
    return state, data


@pytest.mark.parametrize(
    "position,enemy,affected",
    [
        ([15.4, 0, 0], True, True),
        ([15.401, 0, 0], True, False),
        ([14, 0, 3], True, True),
        ([14, 0, 4], True, False),
        ([14, 0, 0], False, False),
    ],
)
def test_hound_bite_uses_exact_anchor_point_actual_body_and_enemy(position, enemy, affected):
    state, _ = _hound_bite_case()
    body = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]
    body["position"] = position
    body["metadata"]["enemy_of"] = ["hero"] if enemy else []
    result = _run("begin_combat_turn", state, actor="hero")
    assert (state[f"{SRD}.entities"]["guard"]["hp"] < 1000) is affected
    if affected:
        assert len(result.result["result_card"]["details"]) == 1
    else:
        assert result.result["result_card"] is None
    assert state[f"{SRD}.combat"]["action_available"] is True
    assert state[f"{SRD}.state"]["spell_force_records"]["hound_one"]["last_bite_turn"] == 1
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="already active"):
        _run("begin_combat_turn", state, actor="hero")
    assert state == before


@pytest.mark.parametrize("problem", ["success", "immunity", "other_plane", "dead", "other_turn"])
def test_hound_bite_requires_actual_target_and_applies_native_save_and_force_packet(problem):
    state, _ = _hound_bite_case()
    target = state[f"{SRD}.entities"]["guard"]
    if problem == "success":
        target["saving_throw_modifiers"]["dexterity"] = 30
    elif problem == "immunity":
        target["damage_immunities"] = ["force"]
    elif problem == "other_plane":
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["plane"] = "other"
    elif problem == "dead":
        target.update(hp=0, is_alive=False)
    else:
        state[f"{SRD}.combat"]["current_actor"] = "guard"
    hp_before = target["hp"]
    result = _run("begin_combat_turn", state, actor="guard" if problem == "other_turn" else "hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp_before
    if problem in {"success", "immunity"}:
        assert len(result.result["result_card"]["details"]) == 1
        assert "Damage 0" in str(result.result["result_card"])


def test_hound_bite_target_intent_cannot_select_an_ally_or_reset_the_turn():
    state, _ = _hound_bite_case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="eligible enemy"):
        _run(
            "begin_combat_turn",
            state,
            actor="hero",
            hound_bite_choices=[{"force_id": "hound_one", "target_id": "hero"}],
        )
    assert state == before


def test_hound_bite_native_choice_is_atomic_and_resolves_only_the_selected_actual_enemy():
    from sylvia_gm.domain.authority import MechanicContext, MechanicsChoiceResolution, owned_payload
    from tests.srd_full.test_srd_action_budget import _mechanic

    state, data = _hound_bite_case(second=True)
    context = MechanicContext(
        campaign_id="budget",
        turn_id="turn",
        action_id="hound-start",
        state=owned_payload(state),
        parameters=owned_payload({"actor": "hero"}),
        participants=owned_payload(
            {
                "hero": {
                    "participant_id": "hero",
                    "kind": "player_character",
                    "validation_status": "validated",
                    "package_id": SRD,
                    "data": data,
                },
            }
        ),
    )
    waiting = _mechanic("begin_combat_turn")(context)
    assert waiting.mutations == ()
    assert waiting.result["status"] == "awaiting_choice"
    assert waiting.choice.choice_id == "srd-hound-bite"
    assert [option.option_id for option in waiting.choice.options] == ["guard", "other"]
    resumed = _mechanic("begin_combat_turn")(
        replace(
            context,
            choice_resolution=MechanicsChoiceResolution(
                waiting.choice.choice_id,
                "other",
                waiting.choice,
            ),
        )
    )
    saved = {f"{m.namespace}.{m.key}": m.value for m in resumed.mutations}
    assert saved[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert saved[f"{SRD}.entities"]["other"]["hp"] < 1000
    assert saved[f"{SRD}.combat"]["turn_active"] is True
    assert len(resumed.result["result_card"]["details"]) == 1


@pytest.mark.parametrize("visibility", ["opaque", "clear"])
def test_hound_bite_physical_wall_needs_actual_categorical_cover(visibility):
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, _ = _hound_bite_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    wall = _wall(0.5)
    wall.update(visibility=visibility)
    wall["volume"] = {"kind": "box", "min": [12, -2, 0], "max": [12.5, 2, 10]}
    scene["geometry"].append(wall)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="categorical Cover"):
        _run("begin_combat_turn", state, actor="hero")
    assert state == before
    scene["entities"][1]["metadata"]["cover_from"] = {"hound_one": "total"}
    result = _run("begin_combat_turn", state, actor="hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert result.result["result_card"] is None


@pytest.mark.parametrize("cover,bonus", [("half", 2), ("three_quarters", 5)])
def test_hound_bite_partial_cover_delegates_the_actual_save_bonus(cover, bonus):
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, _ = _hound_bite_case()
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = 30
    scene = state[f"{SRD}.state"]["spatial_scene"]
    wall = _wall(0.5)
    wall["volume"] = {"kind": "box", "min": [12, -1, 0], "max": [12.5, 1, 3]}
    scene["geometry"].append(wall)
    scene["entities"][1]["metadata"]["cover_from"] = {"hound_one": cover}
    result = _run("begin_combat_turn", state, actor="hero")
    assert f"+ ({30 + bonus})" in str(result.result["result_card"]["details"])
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


@pytest.mark.parametrize("lethal_first", [False, True])
def test_hound_bites_each_source_once_and_skips_an_enemy_killed_by_the_first(lethal_first):
    state, data = _hound_bite_case()
    _free(state)
    _, _, parameters = _hound_case(force_id="hound_two", force_position=[10, 2, 0])
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=False,
        current_actor="hero",
        active_actor=None,
        phase="awaiting_turn_start",
    )
    if lethal_first:
        state[f"{SRD}.entities"]["guard"].update(hp=1, max_hp=1)
    result = _run("begin_combat_turn", state, actor="hero")
    records = state[f"{SRD}.state"]["spell_force_records"]
    assert records["hound_one"]["last_bite_turn"] == records["hound_two"]["last_bite_turn"] == 1
    assert len(result.result["result_card"]["details"]) == (1 if lethal_first else 2)
    assert (state[f"{SRD}.entities"]["guard"]["is_alive"] is False) is lethal_first


def test_hound_duplicate_target_intent_rejects_before_turn_or_source_mutation():
    state, _ = _hound_bite_case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="one eligible enemy"):
        _run(
            "begin_combat_turn",
            state,
            actor="hero",
            hound_bite_choices=[
                {"force_id": "hound_one", "target_id": "guard"},
                {"force_id": "hound_one", "target_id": "guard"},
            ],
        )
    assert state == before


def test_public_hound_bite_choice_reopens_commits_one_native_card_and_retries(
    tmp_path,
    monkeypatch,
):
    from sylvia_gm.application.authority import ResolveMechanicsChoiceCommand
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_spell_wave3_information import _entity

    def spell_case(spell):
        state, data, parameters = _hound_case(spell)
        for actor in ("guard", "other"):
            state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
            state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["dexterity"] = -30
        scene = state[f"{SRD}.state"]["spatial_scene"]
        scene["entities"][1]["position"] = [14, 0, 0]
        scene["entities"][1]["metadata"]["enemy_of"] = ["hero"]
        other = _entity("other", [14, 2, 0], participant_ref="other", creature_type="humanoid")
        other["metadata"]["enemy_of"] = ["hero"]
        scene["entities"].append(other)
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", spell_case)
    database = tmp_path / "hound-bite.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "faithful_hound",
            target_overrides={"hp": 1000, "max_hp": 1000},
        )
        cast = ActionCommand(
            campaign_id="casting",
            action_id="hound",
            idempotency_key="hound",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(cast).status is ActionStatus.COMMITTED

        def act(name, suffix, **params):
            command = replace(
                cast,
                action_id=suffix,
                idempotency_key=suffix,
                tool_name=name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=params,
            )
            receipt = mechanics.execute_action(command)
            return command, receipt

        _, receipt = act(
            "start_group_combat_encounter", "combat", combatants=["hero", "guard", "other"]
        )
        assert receipt.status is ActionStatus.COMMITTED
        for index in range(3):
            if owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"] == "hero":
                break
            assert (
                act("begin_combat_turn", f"npc-begin-{index}")[1].status is ActionStatus.COMMITTED
            )
            assert act("end_combat_turn", f"npc-end-{index}")[1].status is ActionStatus.COMMITTED
            assert (
                act("advance_group_combat_encounter_turn", f"next-{index}")[1].status
                is ActionStatus.COMMITTED
            )
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"] == "hero"
        prior = owner.snapshot("casting")
        begin, waiting = act("begin_combat_turn", "hound-turn")
        assert waiting.status == "awaiting_choice"
        assert [option.option_id for option in waiting.choice.options] == ["guard", "other"]
        assert owner.snapshot("casting").state == prior.state
        assert mechanics.execute_action(begin) == waiting
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == prior.state
        assert owner.execute_action(begin) == waiting
        choice = ResolveMechanicsChoiceCommand(
            campaign_id="casting",
            action_id="hound-turn",
            resolution_id="hound-enemy",
            owner_id="casting-owner",
            expected_revision=prior.revision,
            choice_id=waiting.choice.choice_id,
            option_id="other",
        )
        receipt = owner.resolve_mechanics_choice(choice)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        assert saved.state[f"{SRD}.entities"]["other"]["hp"] < 1000
        assert len(receipt.result["result_card"]["details"]) == 1
        assert saved.state[f"{SRD}.combat"]["turn_active"] is True
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.resolve_mechanics_choice(choice) == receipt
        assert owner.execute_action(begin) == receipt
        assert owner.snapshot("casting").state == saved.state


@pytest.mark.parametrize(
    "choices,reason",
    [
        ({"force_position": [30.01, 0, 0]}, "outside range"),
        ({"force_position": [20, 0, 0]}, "unoccupied"),
        ({"hound_lighting": "darkness"}, "must see"),
        ({"hound_password": None}, "password"),
    ],
)
def test_hound_cast_rejects_actual_origin_or_missing_cast_choice_without_cost(choices, reason):
    state, data, parameters = _hound_case(**choices)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=reason):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def test_hound_depiction_footprint_does_not_change_exact_point_occupancy():
    initial, _, _ = _hound_case()
    guard = initial[f"{SRD}.state"]["spatial_scene"]["entities"][1]
    face = guard["position"][0] - guard["footprint"]["half_extents"][0]
    state = _hound_cast(force_position=[face - 0.001, 0, 0])
    assert _hound_proxy(state)["active"] is True
    state, data, parameters = _hound_case(force_position=[face, 0, 0])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="unoccupied"):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def test_hound_source_only_visibility_respects_actual_caster_sight():
    state = _hound_cast()
    caster = _run("observe_srd_faithful_hound", state, observer_state="hero", force_id="hound_one")
    assert caster.result["context"]["_hound_visible"] is True
    state[f"{SRD}.entities"]["guard"]["truesight_feet"] = 100
    other = _run("observe_srd_faithful_hound", state, observer_state="guard", force_id="hound_one")
    assert other.result["context"]["_hound_visible"] is False
    state[f"{SRD}.entities"]["hero"]["conditions"].append("blinded")
    caster = _run("observe_srd_faithful_hound", state, observer_state="hero", force_id="hound_one")
    assert caster.result["context"]["_hound_visible"] is False


def test_hound_later_magic_movement_is_intangible_and_ends_at_actual_separation():
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state = _hound_cast()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="later turn"):
        _run(
            "move_srd_faithful_hound",
            state,
            caster_state="hero",
            force_id="hound_one",
            destination=[20, 0, 0],
            destination_lighting="bright",
        )
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    wall = _wall(0.5)
    wall["volume"] = {"kind": "box", "min": [14, -2, 0], "max": [15, 2, 10]}
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(wall)
    _run(
        "move_srd_faithful_hound",
        state,
        caster_state="hero",
        force_id="hound_one",
        destination=[20, 0, 0],
        destination_lighting="bright",
    )
    assert _hound_proxy(state)["position"] == [20, 0, 0]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][0]["position"] = [320, 0, 0]
    _run("refresh_srd_hound_separation", state)
    assert _hound_proxy(state)["active"] is True
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][0]["position"] = [320.01, 0, 0]
    _run("refresh_srd_hound_separation", state)
    assert _hound_proxy(state)["active"] is False


def test_hound_later_movement_spends_actual_magic_action_budget():
    state = _hound_cast()
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=False,
        current_actor="hero",
        active_actor=None,
        phase="awaiting_turn_start",
    )
    _run("begin_combat_turn", state, actor="hero")
    _run(
        "move_srd_faithful_hound",
        state,
        caster_state="hero",
        force_id="hound_one",
        destination=[20, 0, 0],
        destination_lighting="bright",
    )
    assert state[f"{SRD}.combat"]["action_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "move_srd_faithful_hound",
            state,
            caster_state="hero",
            force_id="hound_one",
            destination=[30, 0, 0],
            destination_lighting="bright",
        )
    assert state == before


def test_hound_actual_caster_movement_retires_source_beyond300feet():
    state = _hound_cast()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][0]["position"] = [290, 0, 0]
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[320, 0, 0])
    assert _hound_proxy(state)["active"] is False


def test_public_hound_cast_reopen_retry_later_magic_movement_and_expiry(tmp_path, monkeypatch):
    from tests import test_srd_public_spell_casting as public

    monkeypatch.setattr(public, "_spell_case", _hound_case)
    database = tmp_path / "hound.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "faithful_hound")
        prior = owner.snapshot("casting")
        command = ActionCommand(
            campaign_id="casting",
            action_id="hound",
            idempotency_key="hound",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=prior.revision,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(command) == receipt
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == (
            prior.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] - 1
        )
        assert (
            saved.state[f"{SRD}.entities"]["hero"]["spell_materials"]
            == (prior.state[f"{SRD}.entities"]["hero"]["spell_materials"])
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(command) == receipt
        tick = replace(
            command,
            action_id="later",
            idempotency_key="later",
            expected_revision=saved.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 1},
        )
        assert mechanics.execute_action(tick).status is ActionStatus.COMMITTED
        movement = replace(
            command,
            action_id="move",
            idempotency_key="move",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="move_srd_faithful_hound",
            parameters={
                "caster_state": "hero",
                "force_id": "hound_one",
                "destination": [20, 0, 0],
                "destination_lighting": "bright",
            },
        )
        movement_receipt = mechanics.execute_action(movement)
        assert movement_receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(movement) == movement_receipt
        saved = owner.snapshot("casting")
        assert _hound_proxy(saved.state)["position"] == [20, 0, 0]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(movement) == movement_receipt
        expiry = replace(
            command,
            action_id="expire",
            idempotency_key="expire",
            expected_revision=saved.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 4799},
        )
        assert mechanics.execute_action(expiry).status is ActionStatus.COMMITTED
        assert _hound_proxy(owner.snapshot("casting").state)["active"] is False


def test_guardian_source_profile_body_cost_and_clock_cleanup():
    validate_profiles({"guardian_of_faith": GUARDIAN_PROFILE})
    with pytest.raises(SystemExit, match="source profile"):
        validate_profiles({"guardian_of_faith": GUARDIAN_PROFILE | {"damage_amount": 21}})
    state, data, params = _guardian_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    assert "guardian_one" not in state[f"{SRD}.entities"]
    record = state[f"{SRD}.state"]["spell_force_records"]["guardian_one"]
    assert record["expires_tick"] == 9600
    assert record["save_dc"] == 18
    assert state[f"{SRD}.entities"]["hero"].get("concentration_spell_id") is None
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    assert state[f"{SRD}.state"]["spell_force_records"]["guardian_one"]["active"] is False
    assert (
        next(
            e
            for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if e["id"] == "guardian_one"
        )["active"]
        is False
    )


@pytest.mark.parametrize("save_modifier,damage", [(30, 10), (-30, 20)])
def test_guardian_actual_enemy_turn_start_uses_source_save_and_fixed_damage(save_modifier, damage):
    state, data, params = _guardian_case()
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = save_modifier
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _guardian_begin(state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - damage
    assert state[f"{SRD}.state"]["spell_force_records"]["guardian_one"]["damage_dealt"] == damage
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="already active"):
        _run("begin_combat_turn", state, actor="guard")
    assert state == before


@pytest.mark.parametrize(
    "position,enemy,damage",
    [
        ([25.4, 0, 0], True, 10),
        ([25.5, 0, 0], True, 0),
        ([14, 0, 18], True, 10),
        ([14, 0, 19], True, 0),
        ([20, 0, 0], False, 0),
    ],
)
def test_guardian_qualifies_source_size_actual_target_box_height_and_enemy(position, enemy, damage):
    state, data, params = _guardian_case()
    scene_target = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]
    scene_target["position"] = position
    scene_target["metadata"]["enemy_of"] = ["hero"] if enemy else []
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _guardian_begin(state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - damage


def test_guardian_sixty_actual_damage_ends_exact_source_and_resistance_counts():
    state, data, params = _guardian_case()
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = -30
    state[f"{SRD}.entities"]["guard"]["damage_resistances"] = ["radiant"]
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    for index in range(6):
        _guardian_begin(state)
        record = state[f"{SRD}.state"]["spell_force_records"]["guardian_one"]
        assert record["damage_dealt"] == (index + 1) * 10
        assert record["active"] is (index != 5)
        _run("end_combat_turn", state, actor="guard")
    assert state[f"{SRD}.state"]["spell_force_ids"] == []
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 940


def test_guardian_immunity_and_two_sources_do_not_damage_an_already_dead_target():
    state, data, params = _guardian_case()
    target = state[f"{SRD}.entities"]["guard"]
    target["damage_immunities"] = ["radiant"]
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _guardian_begin(state)
    assert state[f"{SRD}.state"]["spell_force_records"]["guardian_one"]["damage_dealt"] == 0
    _run("end_combat_turn", state, actor="guard")
    _free(state)
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(
            params
            | {
                "force_id": "guardian_two",
                "created_actor_position": [10, 15, 0],
            }
        ),
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [20, 7, 0]
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=5, damage_immunities=[])
    target["saving_throw_modifiers"]["dexterity"] = -30
    _guardian_begin(state)
    assert state[f"{SRD}.entities"]["guard"]["is_alive"] is False
    records = state[f"{SRD}.state"]["spell_force_records"]
    assert records["guardian_one"]["damage_dealt"] == 20
    assert records["guardian_two"]["damage_dealt"] == 0


def test_guardian_partial_cover_uses_admitted_relation_and_the_actual_save_owner():
    state, data, params = _guardian_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(
        {
            "id": "low_wall",
            "map_id": scene["map"]["id"],
            "volume": {"kind": "box", "min": [17.5, -1, 0], "max": [18, 1, 3]},
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": {},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="partial Cover"):
        _guardian_begin(state)
    # Only the explicit turn setup above the owner occurs outside the mechanic transaction.
    assert state[f"{SRD}.entities"] == before[f"{SRD}.entities"]
    scene["entities"][1]["metadata"]["cover_from"] = {"guardian_one": "half"}
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = 15
    _guardian_begin(state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 990


def test_guardian_nonconcentration_creation_preserves_weapon_source():
    state, data, params = _case("spiritual_weapon", force_position=[30, 0, 0], force_target=None)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    caster = state[f"{SRD}.entities"]["hero"]
    caster["prepared_spells"].append("guardian_of_faith")
    data["spellcasting_grants"][0]["spells"].append("guardian_of_faith")
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(
            params
            | {
                "spell": "guardian_of_faith",
                "slot_resource": "spell_slot_4",
                "force_id": "guardian_one",
                "created_actor_position": [10, 0, 0],
                "created_actor_height": 8,
            }
        ),
    )
    caster = state[f"{SRD}.entities"]["hero"]
    assert caster["concentration_spell_id"] == "spiritual_weapon"
    assert caster["concentration_spell_force_id"] == "force_one"
    _run("end_concentration", state, caster_state="hero")
    assert _record(state)["active"] is False
    assert state[f"{SRD}.state"]["spell_force_records"]["guardian_one"]["active"] is True


def test_public_guardian_cast_reopen_retry_then_source_expiry(tmp_path, monkeypatch):
    from tests import test_srd_public_spell_casting as public

    def scene_case(spell):
        state, data, params = _guardian_case(spell)
        state[f"{SRD}.combat"]["current_actor"] = "guard"
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", scene_case)
    database = tmp_path / "guardian.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "guardian_of_faith")
        command = ActionCommand(
            campaign_id="casting",
            action_id="guardian",
            idempotency_key="guardian",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(command) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        turn = replace(
            command,
            action_id="expire",
            idempotency_key="expire",
            expected_revision=saved.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 4800},
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(turn)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(turn) == receipt
        assert owner.snapshot("casting").state[f"{SRD}.state"]["spell_force_ids"] == []
        assert owner.snapshot("casting").state[f"{SRD}.state"]["has_guardian_sources"] is False


def _position(state):
    return next(
        x["position"]
        for x in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if x["id"] == "force_one"
    )


@pytest.mark.parametrize("spell", PROFILES)
def test_source_profiles_and_public_creation(spell):
    declaration = yaml.safe_load(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics.yaml").read_text()
    )
    validate_profiles({spell: declaration["spells"][spell]["definition"]})
    changed = deepcopy(PROFILES[spell])
    changed["move_feet"] += 1
    with pytest.raises(SystemExit, match="source profile"):
        validate_profiles({spell: changed})
    assert declaration["spells"][spell]["source"]["page"] == SOURCE[spell][0]
    state, _, _, _ = _cast(spell)
    assert _record(state)["active"] is True
    assert _record(state)["expires_tick"] == PROFILES[spell]["duration_rounds"] * 2
    assert (
        state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{PROFILES[spell]['base_level']}"]
        == 2
    )


def test_source_geometry_and_failed_cast_leave_no_cost_or_force():
    state, data, params = _case("spiritual_weapon", force_position=[61, 0, 0])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="outside range"):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before
    state, data, params = _case("arcane_sword", force_position=[30, 0, 0])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="within 5 feet"):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


@pytest.mark.parametrize(
    "spell,slot,dice_count",
    [
        ("spiritual_weapon", 2, 1),
        ("spiritual_weapon", 4, 3),
        ("arcane_sword", 7, 4),
    ],
)
def test_force_attack_scales_from_saved_slot_and_actual_caster_modifier(
    monkeypatch, spell, slot, dice_count
):
    monkeypatch.setattr(dice, "_rng", Random(2))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, _, _, result = _cast(spell, slot_resource=f"spell_slot_{slot}")
    nested = result.result["context"]
    assert _record(state)["casting_modifier"] == 4
    # The HP delta must include the printed ability modifier, not only the die receipt.
    assert 1000 - state[f"{SRD}.entities"]["guard"]["hp"] >= dice_count + 4
    assert "damage" in str(nested["_spell_summary_rows"]).lower()
    assert _record(state)["slot_level"] == slot


def test_later_force_command_reaches_saved_position_then_concentration_cleanup():
    state, _, _, _ = _cast("spiritual_weapon", force_target=None)
    _run(
        "command_srd_spell_force",
        state,
        caster_state="hero",
        force_id="force_one",
        destination=[25, 0, 0],
        force_target="guard",
    )
    assert _position(state) == [25, 0, 0]
    with pytest.raises(RulesetPreconditionRejected, match="one Bonus Action"):
        _run(
            "command_srd_spell_force",
            state,
            caster_state="hero",
            force_id="force_one",
            destination=[25, 0, 0],
        )
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="cannot move that far"):
        _run(
            "command_srd_spell_force",
            state,
            caster_state="hero",
            force_id="force_one",
            destination=[46, 0, 0],
        )
    assert state == before
    _run("end_concentration", state, caster_state="hero")
    assert _record(state)["active"] is False
    assert (
        next(
            x for x in state[f"{SRD}.state"]["spatial_scene"]["entities"] if x["id"] == "force_one"
        )["active"]
        is False
    )


def test_servant_exact_body_command_radius_and_zero_hp_cleanup():
    state, _, _, _ = _cast("unseen_servant")
    servant = state[f"{SRD}.entities"]["force_one"]
    assert _record(state)["id"] == "force_one"
    assert state[f"{SRD}.state"]["spell_force_ids"] == ["force_one"]
    assert (servant["armor_class"], servant["hp"], servant["abilities"]["strength"]) == (10, 1, 2)
    _run(
        "command_srd_spell_force",
        state,
        caster_state="hero",
        force_id="force_one",
        destination=[20, 0, 0],
        task="polish",
        interaction_subject="orb",
    )
    assert _record(state)["task"] == "polish"
    assert _record(state)["interaction_subject"] == "orb"
    _run("apply_srd_damage", state, target="force_one", amount=1, adjustment=0, damage_type="force")
    assert _record(state)["active"] is False
    assert state[f"{SRD}.entities"]["force_one"]["spell_force_active"] is False
    assert state[f"{SRD}.state"]["spell_force_ids"] == []
    assert state[f"{SRD}.state"]["has_spell_forces"] is False


def test_nonconcentration_servant_preserves_exact_weapon_concentration_source():
    state, data, _, _ = _cast("arcane_sword")
    servant_state, _, servant_params = _case("unseen_servant", force_id="force_two")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("unseen_servant")
    data["spellcasting_grants"][0]["spells"].append("unseen_servant")
    data["wizard_spellbook"].append("unseen_servant")
    supply = deepcopy(servant_state[f"{SRD}.entities"]["hero"]["spell_materials"][0])
    supply["id"] = "servant_material"
    hero = state[f"{SRD}.entities"]["hero"]
    hero["spell_materials"].append(supply)
    hero["inventory"].append(supply["inventory_item"])
    servant_params["material_item_ids"] = [supply["id"]]
    _run("cast_spell_outside_combat", state, participant_data=data, **servant_params)
    _run("end_concentration", state, caster_state="hero")
    sources = state[f"{SRD}.state"]["spell_force_records"]
    assert sources["force_one"]["active"] is False
    assert sources["force_two"]["active"] is True
    assert state[f"{SRD}.entities"]["force_two"]["hp"] == 1
    assert state[f"{SRD}.state"]["spell_force_ids"] == ["force_two"]


def test_expired_weapon_ends_actual_concentration_and_retires_clock_source():
    state, _, _, _ = _cast("spiritual_weapon", force_target=None)
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _record(state)["active"] is False
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_force_id"] is None
    assert state[f"{SRD}.state"]["has_spell_forces"] is False


def test_servant_unoccupied_creation_and_out_of_radius_command_ends_source():
    state, data, params = _case("unseen_servant", force_position=[20, 0, 0])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="unoccupied"):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before
    state, _, _, _ = _cast("unseen_servant", force_position=[55, 0, 0])
    _run(
        "command_srd_spell_force",
        state,
        caster_state="hero",
        force_id="force_one",
        destination=[65, 0, 0],
        task="fetch",
        interaction_subject="orb",
    )
    assert _record(state)["active"] is False


def test_force_later_command_spends_bonus_budget_and_cannot_repeat_cast_turn():
    state, _, _, _ = _cast("arcane_sword")
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(turns_started=0, bonus_action_available=True)
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="hero", current_actor="hero"
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="later turn"):
        _run(
            "command_srd_spell_force",
            state,
            caster_state="hero",
            force_id="force_one",
            destination=[25, 0, 0],
        )
    assert state == before
    state[f"{SRD}.entities"]["hero"]["turns_started"] = 1
    _run(
        "command_srd_spell_force",
        state,
        caster_state="hero",
        force_id="force_one",
        destination=[25, 0, 0],
        force_target="guard",
    )
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "command_srd_spell_force",
            state,
            caster_state="hero",
            force_id="force_one",
            destination=[25, 0, 0],
        )
    assert state == before


def test_scene_enemy_qualification_requires_explicit_narrative_relation():
    state, _, _ = _case("unseen_servant")
    result = _run("qualify_srd_scene_enemy", state, caster_id="hero", target_id="guard")
    assert result.result["context"]["_scene_is_enemy"] is False
    target = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]
    target["metadata"]["enemy_of"] = ["hero"]
    result = _run("qualify_srd_scene_enemy", state, caster_id="hero", target_id="guard")
    assert result.result["context"]["_scene_is_enemy"] is True
    result = _run("qualify_srd_scene_enemy", state, caster_id="other", target_id="guard")
    assert result.result["context"]["_scene_is_enemy"] is False


def test_sword_destination_uses_actual_sight_and_darkvision():
    state, _, _, _ = _cast("arcane_sword")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="can see"):
        _run(
            "command_srd_spell_force",
            state,
            caster_state="hero",
            force_id="force_one",
            destination=[25, 0, 0],
            destination_lighting="darkness",
        )
    assert state == before
    state[f"{SRD}.entities"]["hero"]["darkvision_feet"] = 60
    _run(
        "command_srd_spell_force",
        state,
        caster_state="hero",
        force_id="force_one",
        destination=[25, 0, 0],
        destination_lighting="darkness",
    )
    assert _position(state) == [25, 0, 0]


def test_force_partial_cover_requires_actual_origin_relation():
    state, _, _, _ = _cast("spiritual_weapon", force_target=None, force_position=[15, 0, 0])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(
        {
            "id": "low_wall",
            "map_id": scene["map"]["id"],
            "volume": {"kind": "box", "min": [17.5, -1, 0], "max": [18, 1, 2]},
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": {},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Cover relation"):
        _run(
            "attack_srd_spell_force",
            state,
            caster_state="hero",
            force_id="force_one",
            force_target="guard",
        )
    assert state == before
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["metadata"]["cover_from"] = {"force_one": "half"}
    result = _run(
        "attack_srd_spell_force",
        state,
        caster_state="hero",
        force_id="force_one",
        force_target="guard",
    )
    assert result.result["context"]["_force_cover"] == "half"


def test_public_servant_cast_reopen_retry_command_and_expiry(tmp_path, monkeypatch):
    from tests import test_srd_public_spell_casting as public

    monkeypatch.setattr(public, "_spell_case", _case)
    database = tmp_path / "force.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "unseen_servant")
        command = ActionCommand(
            campaign_id="casting",
            action_id="force",
            idempotency_key="force",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        followup = replace(
            command,
            action_id="command",
            idempotency_key="command",
            expected_revision=saved.revision,
            tool_name="command_srd_spell_force",
            parameters={
                "caster_state": "hero",
                "force_id": "force_one",
                "destination": [20, 0, 0],
                "task": "polish",
                "interaction_subject": "orb",
            },
        )
        assert mechanics.execute_action(followup).status is ActionStatus.COMMITTED
        assert mechanics.execute_action(followup).status is ActionStatus.COMMITTED
        elapsed = replace(
            command,
            action_id="expire",
            idempotency_key="expire",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 600},
        )
        assert mechanics.execute_action(elapsed).status is ActionStatus.COMMITTED
        assert _record(owner.snapshot("casting").state)["active"] is False


def test_public_created_ghast_stench_has_actual_paid_body_turn_source_and_saved_expiry(
    tmp_path,
    monkeypatch,
):
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_extended_casting import _begin

    def case(_spell):
        state, data, parameters = _created_undead_case(1, 8)
        parameters["created_undead_form"] = "ghast"
        parameters["created_undead_bodies"][0]["individual_id"] = "ghast_witness"
        state[f"{SRD}.entities"]["hero"]["creature_type"] = "humanoid"
        _admit_zombie_slam_target(state)
        scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
        next(row for row in scene if row["id"] == "other")["position"] = [100, 0, 0]
        return state, data, parameters

    rolls = []
    phase = {"turn": False}

    class StenchDice:
        def randint(self, low, high):
            assert (low, high) == (1, 20)
            rolls.append(high)
            return 5 if phase["turn"] else 18

    monkeypatch.setattr(public, "_spell_case", case)
    monkeypatch.setattr(dice, "_rng", StenchDice())
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "ghast-stench.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "create_undead",
            target_overrides={"hp": 0, "max_hp": 20},
        )
        prototype = ActionCommand(
            campaign_id="casting",
            action_id="ghast-night",
            idempotency_key="ghast-night",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="record_srd_map_time_of_day",
            parameters={"plane": "material", "phase": "night"},
        )
        mechanics = StatelessMcpMechanics(owner)

        def act(identity, name, **choices):
            action = replace(
                prototype,
                action_id=identity,
                idempotency_key=identity,
                tool_name=name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            receipt = mechanics.execute_action(action)
            assert receipt.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == receipt
            return action, receipt

        act("ghast-night", "record_srd_map_time_of_day", plane="material", phase="night")
        act("ghast-start", "begin_srd_spell_cast", **_begin(parameters))
        act(
            "ghast-progress",
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        act("ghast-current-night", "record_srd_map_time_of_day", plane="material", phase="night")
        created, create_receipt = act("ghast-create", "cast_spell_outside_combat", **parameters)
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["ghast_witness"]
        assert body["created_form_id"] == "ghast" and body["hp"] == body["max_hp"] == 36
        assert body["armor_class"] == 13 and body["languages"] == ["common"]
        assert saved.state[f"{SRD}.entities"]["guard"]["body_animated_actor_id"] == "ghast_witness"
        materials = deepcopy(saved.state[f"{SRD}.entities"]["hero"]["spell_materials"])
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(created) == create_receipt
        act(
            "ghast-initiative",
            "start_group_combat_encounter",
            combatants=["hero", "ghast_witness", "other"],
        )
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"] == "hero"
        phase["turn"] = True
        count = len(rolls)
        begun, begin_receipt = act("ghast-target-begin", "begin_combat_turn")
        assert len(rolls) == count + 1
        rows = begin_receipt.result["result_card"]["details"]
        assert {
            "Ghast Stench source",
            "Ghast Stench",
            "Stench Constitution dice",
            "Stench Constitution total",
        } <= {row["label"] for row in rows}
        assert any(row["label"] == "Ghast Stench" and row["value"] == "failure" for row in rows)
        assert any(
            row["label"] == "Stench Constitution dice" and row["value"] == [5] for row in rows
        )
        assert any(
            row["label"] == "Stench Constitution total" and row["value"] == 7 for row in rows
        )
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        (source_id,) = hero["ghast_stench_source_ids"]
        source = saved.state[f"{SRD}.state"]["timed_condition_sources"][source_id]
        assert "poisoned" in hero["conditions"] and source["save_dc"] == 10
        assert source["target_start_turn"] == hero["turns_started"] + 1
        assert source["native_body_source"] == "ghast_witness"
        assert source["native_attack"] == "Stench"
        assert source["native_body_source_sequence"] == source["sequence"]
        assert source["native_body_block_sha256"] == body["created_source_block_sha256"]
        assert source["dispel_ongoing"] is False and source["source_is_magical"] is False
        assert hero["resources"]["spell_slot_8"] == 2 and hero["spell_materials"] == materials
        assert owner.execute_action(begun) == begin_receipt and len(rolls) == count + 1
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(begun) == begin_receipt and len(rolls) == count + 1
        act(
            "ghast-target-away",
            "move_srd_scene_creature",
            actor_state="hero",
            destination=[-20, 0, 0],
            mode="walk",
        )
        for index in range(3):
            act(f"ghast-end-{index}", "end_combat_turn")
            assert (
                owner.snapshot("casting").state[f"{SRD}.state"]["timed_condition_sources"][
                    source_id
                ]["installed"]
                is True
            )
            act(f"ghast-next-{index}", "advance_group_combat_encounter_turn")
            actor = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            next_begin, next_receipt = act(f"ghast-begin-{index}", "begin_combat_turn")
            if actor == "ghast_witness":
                assert owner.snapshot("casting").state[f"{SRD}.entities"][actor]["dodging"] is True
            if actor == "hero":
                break
        else:
            pytest.fail("The actual target did not reach its next real start.")
        assert len(rolls) == count + 1
        saved = owner.snapshot("casting")
        assert "poisoned" not in saved.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert (
            saved.state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is False
        )
        assert saved.state[f"{SRD}.entities"]["ghast_witness"]["is_alive"] is True
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(next_begin) == next_receipt and len(rolls) == count + 1
        assert owner.execute_action(created) == create_receipt
        assert owner.snapshot("casting").state == saved.state


@pytest.fixture(scope="module")
def _native_mummy_receiver_runtime(_created_undead_runtime):
    from scripts.srd_full.srd_native_undead_curses import (
        bind_native_undead_rest_consumers,
        extend_runtime,
    )
    from scripts.srd_full.srd_spell_summoning import _ghoul_actor_tools
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
    from scripts.srd_full.srd_timed_senses import _replace_tools

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    # The constructor is a private source-owner fixture; public Mummy creation stays blocked.
    parent = _created_undead_runtime[1].decode()
    text = _replace_tools(parent, _ghoul_actor_tools(_load_runtime(parent)["tools"], "mummy"))
    text = extend_runtime(text)
    assert extend_runtime(text) == text
    text = bind_native_undead_rest_consumers(text)
    assert bind_native_undead_rest_consumers(text) == text
    assert not validate_ruleset(text)
    runtime = _load_runtime(text)
    validate_content_shape("package_runtime", runtime, file="native-mummy-curse-receiver")
    return runtime, text.encode()


@pytest.fixture
def native_mummy_receiver_owner(created_undead_owner, monkeypatch, _native_mummy_receiver_runtime):
    from functools import lru_cache

    import tests.srd_full.test_srd_action_budget as budget
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    runtime, encoded = _native_mummy_receiver_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="e" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    budget._tool_schema(
                        runtime["tools"][name],
                        tool_name="" if name == "restore_hit_points" else name,
                    )
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    return runtime


def _native_mummy_receiver_case(caster_spell="create_undead"):
    if caster_spell == "create_undead":
        state, data, _ = _created_undead_case(1, 9)
    else:
        state, data = _recorded_corpse_case(caster_spell)
        state[f"{SRD}.entities"]["guard"].update(inventory=[], equipment=[])
    state[f"{SRD}.entities"]["hero"]["creature_type"] = "humanoid"
    _run(
        "prepare_srd_source_mummy",
        state,
        creature_id="native_mummy",
        corpse_state="guard",
        source_caster="hero",
        other_languages=["elvish", "infernal"],
    )
    _run(
        "finish_srd_mummy_source_actor",
        state,
        actor_state="native_mummy",
        corpse_state="guard",
        caster_state="hero",
        other_languages=["elvish", "infernal"],
    )
    _run(
        "apply_srd_damage",
        state,
        target="hero",
        amount=5,
        adjustment=0,
        damage_type="force",
        critical_hit=False,
    )
    result = _run(
        "install_srd_native_mummy_rot", state, actor_state="native_mummy", target_state="hero"
    )
    return state, data, result.result["context"]["_mummy_rot_source_id"]


def test_native_mummy_curse_receiver_uses_actual_body_pool_and_no_invented_save(
    native_mummy_receiver_owner,
):
    state, data, identity = _native_mummy_receiver_case()
    hero = state[f"{SRD}.entities"]["hero"]
    hp = hero["hp"]
    source = state[f"{SRD}.state"]["timed_condition_sources"][identity]
    assert source["save_dc"] is None and source["repeat_save_timing"] == "none"
    assert source["native_body_source"] == "native_mummy"
    assert source["native_body_source_sequence"] == source["sequence"]
    assert source["native_attack"] == "Rotting Fist" and source["native_profile"] == "mummy_rot"
    assert source["source_is_magical"] is False and source["dispel_ongoing"] is False
    assert hero["hit_point_recovery_blocked"] is True
    _run("restore_hit_points", state, participant_data=data, target="hero", amount=5)
    assert state[f"{SRD}.entities"]["hero"]["hp"] == hp
    repeated = _run(
        "install_srd_native_mummy_rot", state, actor_state="native_mummy", target_state="hero"
    )
    assert repeated.result["context"]["_mummy_rot_source_id"] == identity
    effects = state[f"{SRD}.state"]["turn_effects"]
    assert len([effect for effect in effects if effect.get("native_curse_source") == identity]) == 1
    assert source["native_next_reduction_tick"] == state[f"{SRD}.state"]["lasting_tick"] + 28800


@pytest.mark.parametrize("removal", ["all", "one"])
def test_native_mummy_curse_removal_preserves_the_real_chill_touch_healing_receipt(
    native_mummy_receiver_owner,
    removal,
):
    state, data, identity = _native_mummy_receiver_case()
    _run(
        "install_srd_turn_effect",
        state,
        caster_state="hero",
        target_state="hero",
        lasting_state="state",
        spell="chill_touch",
        effect_label="Chill Touch",
        kind="healing_block",
        expiry_owner="caster",
        expiry_phase="start",
        magnitude=0,
        dice_count=0,
        damage_die="d10",
        damage_type="necrotic",
    )
    chill = deepcopy(
        next(
            effect
            for effect in state[f"{SRD}.state"]["turn_effects"]
            if effect["spell"] == "chill_touch"
        )
    )
    if removal == "all":
        _run("remove_srd_recovery_curses", state, target_state="hero", lasting_state="state")
    else:
        _run(
            "remove_srd_one_recovery_curse",
            state,
            target_state="hero",
            lasting_state="state",
            source_id=identity,
        )
    assert state[f"{SRD}.state"]["timed_condition_sources"][identity]["installed"] is False
    assert chill in state[f"{SRD}.state"]["turn_effects"]
    assert not any(
        effect.get("native_curse_source") == identity
        for effect in state[f"{SRD}.state"]["turn_effects"]
    )
    assert state[f"{SRD}.entities"]["hero"]["hit_point_recovery_blocked"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=2)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hit_point_recovery_blocked"] is False
    hp = hero["hp"]
    _run("restore_hit_points", state, participant_data=data, target="hero", amount=5)
    assert state[f"{SRD}.entities"]["hero"]["hp"] == hp + 5


def test_native_mummy_curse_receipt_survives_actual_attacker_death_and_clock_projection(
    native_mummy_receiver_owner,
):
    state, _, identity = _native_mummy_receiver_case()
    _run(
        "apply_srd_damage",
        state,
        target="native_mummy",
        amount=30,
        adjustment=0,
        damage_type="fire",
        critical_hit=False,
    )
    assert state[f"{SRD}.entities"]["native_mummy"]["is_alive"] is False
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    result = _run("read_srd_native_mummy_rot", state, source_id=identity, lasting_state="state")
    assert result.result["context"]["_native_mummy_rot"] is True
    assert state[f"{SRD}.entities"]["hero"]["hit_point_recovery_blocked"] is True
    _run("remove_srd_recovery_curses", state, target_state="hero", lasting_state="state")
    assert state[f"{SRD}.entities"]["hero"]["hit_point_recovery_blocked"] is False


def test_native_mummy_rest_guard_requires_the_actual_target_and_continuous_source(
    native_mummy_receiver_owner,
):
    state, _, identity = _native_mummy_receiver_case()
    active = _run(
        "qualify_srd_native_mummy_rot_target", state, target_state="hero", lasting_state="state"
    )
    assert active.result["context"]["_native_mummy_rot_active"] is True
    unrelated = _run(
        "qualify_srd_native_mummy_rot_target", state, target_state="guard", lasting_state="state"
    )
    assert unrelated.result["context"]["_native_mummy_rot_active"] is False
    _run(
        "remove_srd_one_recovery_curse",
        state,
        target_state="hero",
        lasting_state="state",
        source_id=identity,
    )
    cured = _run(
        "qualify_srd_native_mummy_rot_target", state, target_state="hero", lasting_state="state"
    )
    assert cured.result["context"]["_native_mummy_rot_active"] is False


def test_native_mummy_real_daily_reduction_blocks_rest_maximum_until_actual_cure(
    native_mummy_receiver_owner,
):
    state, data, identity = _native_mummy_receiver_case()
    original = state[f"{SRD}.entities"]["hero"]["max_hp"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    reduced = _run(
        "resolve_srd_native_mummy_rot_boundary",
        state,
        target_state="hero",
        source_id=identity,
        lasting_state="state",
    )
    amount = reduced.result["context"]["_native_mummy_daily_reduction"]
    assert amount == reduced.result["context"]["_native_mummy_daily_roll"]["total"]
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["max_hp"] == original - amount and hero["recovery_max_hp_reduction"] == amount
    assert hero["native_undead_max_hp_reduction_receipts"][0]["source"] == identity
    hero.update(temp_hp=5, exhaustion_level=2)
    hp = hero["hp"]
    _run(
        "begin_srd_rest",
        state,
        participant_data=data,
        target_state="hero",
        kind="long",
        activity="sleep",
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    _run("complete_srd_rest", state, participant_data=data, target_state="hero")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["max_hp"] == original - amount and hero["hp"] == hp
    assert hero["temp_hp"] == 0 and hero["exhaustion_level"] == 1
    _run("remove_srd_recovery_curses", state, target_state="hero", lasting_state="state")
    _run("advance_srd_lasting_time", state, elapsed_rounds=9600)
    _run(
        "begin_srd_rest",
        state,
        participant_data=data,
        target_state="hero",
        kind="long",
        activity="sleep",
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    _run("complete_srd_rest", state, participant_data=data, target_state="hero")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["max_hp"] == hero["hp"] == original
    assert hero["recovery_max_hp_reduction"] == 0
    assert hero["native_undead_max_hp_reduction_receipts"] == []


def test_native_mummy_greater_restoration_clears_actual_reduction_but_retains_continuous_curse(
    native_mummy_receiver_owner,
):
    state, data, identity = _native_mummy_receiver_case("greater_restoration")
    original = state[f"{SRD}.entities"]["hero"]["max_hp"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    _run(
        "resolve_srd_native_mummy_rot_boundary",
        state,
        target_state="hero",
        source_id=identity,
        lasting_state="state",
    )
    source = state[f"{SRD}.state"]["timed_condition_sources"][identity]
    next_tick = source["native_next_reduction_tick"]
    _, _, params = _information_case("greater_restoration")
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(params | {"target": "hero", "target_distance": 0, "recovery_choice": "maximum_hp"}),
    )
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["max_hp"] == original and hero["recovery_max_hp_reduction"] == 0
    assert hero["native_undead_max_hp_reduction_receipts"] == []
    assert hero["hit_point_recovery_blocked"] is True
    source = state[f"{SRD}.state"]["timed_condition_sources"][identity]
    assert source["installed"] is True and source["native_next_reduction_tick"] == next_tick
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    reduced = _run(
        "resolve_srd_native_mummy_rot_boundary",
        state,
        target_state="hero",
        source_id=identity,
        lasting_state="state",
    )
    amount = reduced.result["context"]["_native_mummy_daily_reduction"]
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["max_hp"] == original - amount and hero["recovery_max_hp_reduction"] == amount
    assert len(hero["native_undead_max_hp_reduction_receipts"]) == 1


@pytest.fixture(scope="module")
def _native_mummy_glare_runtime():
    from scripts.srd_full.srd_native_mummy_actions import bind_mummy_glare_turn_consumers, extend_runtime
    from scripts.srd_full.srd_native_undead_curses import (
        bind_native_undead_rest_consumers,
    )
    from scripts.srd_full.srd_native_undead_curses import (
        extend_runtime as extend_receivers,
    )
    from scripts.srd_full.srd_spell_summoning import _ghoul_actor_tools
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
    from scripts.srd_full.srd_timed_senses import _replace_tools

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    # Reuse the already-generated exact Ghast owner rather than re-emit its unchanged family.
    text = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    if "resolve_srd_created_ghast_stench" not in _load_runtime(text)["tools"]:
        text = _created_undead_runtime.__wrapped__()[1].decode()
    text = _replace_tools(text, _ghoul_actor_tools(_load_runtime(text)["tools"], "mummy"))
    text = bind_native_undead_rest_consumers(extend_receivers(text))
    text = extend_runtime(text)
    assert extend_runtime(text) == text
    text = bind_mummy_glare_turn_consumers(text)
    assert bind_mummy_glare_turn_consumers(text) == text
    assert not validate_ruleset(text)
    runtime = _load_runtime(text)
    validate_content_shape("package_runtime", runtime, file="native-mummy-glare-owner")
    return runtime, text.encode()


@pytest.fixture
def native_mummy_glare_owner(monkeypatch, _native_mummy_glare_runtime):
    from functools import lru_cache

    import tests.srd_full.test_srd_action_budget as budget
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    runtime, encoded = _native_mummy_glare_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="e" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    budget._tool_schema(
                        runtime["tools"][name],
                        tool_name="" if name == "restore_hit_points" else name,
                    )
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    return runtime


def _native_mummy_glare_case(*, death_ward=False):
    """Retain the actual corpse-created Mummy, scene body, command and independent turn."""
    state, data, _ = _native_mummy_receiver_case("death_ward" if death_ward else "create_undead")
    if death_ward:
        _, _, params = _information_case("death_ward")
        slots = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"]
        _run(
            "cast_spell_outside_combat",
            state,
            participant_data=data,
            **(params | {"target": "hero", "target_distance": 0}),
        )
        assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == slots - 1
    _run("update_srd_spell_force_scene", state, force_id="guard", position=[5, 0, 0], active=False)
    _run(
        "create_srd_source_actor_scene",
        state,
        caster_state="hero",
        summon_id="native_mummy",
        spell="create_undead",
        range_feet=10,
        half_extent=2.5,
        creature_type="undead",
        created_actor_position=[5, 0, 0],
        created_actor_height=6,
    )
    _admit_zombie_slam_target(state)
    _run(
        "command_srd_animated_undead",
        state,
        caster_state="hero",
        actor_ids=["native_mummy"],
        order_kind="specific",
        action="attack",
    )
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = (
        data["abilities"]["dexterity"] - 10
    ) // 2
    state[f"{SRD}.entities"]["other"]["initiative_modifier"] = 0
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "native_mummy", "other"],
    )
    for _ in range(3):
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=actor, participant_data=data)
        if actor == "native_mummy":
            return state, data
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
    pytest.fail("The actual Mummy did not reach its own initiative turn.")


def test_native_mummy_glare_real_action_save_reopen_and_next_caster_end_preserves_sibling_fear(
    native_mummy_glare_owner,
):
    import json

    state, data = _native_mummy_glare_case()
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = -30
    result = _run(
        "use_srd_native_mummy_glare", state, actor_state="native_mummy", target_state="other"
    )
    context = result.result["context"]
    assert context["_mummy_glare_outcome"] == "failure"
    assert 1 <= context["_mummy_glare_save_roll"]["total"] <= 20
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    source_id = context["_mummy_glare_source_id"]
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["save_dc"] == 11 and source["save_ability"] == "wisdom"
    assert source["native_body_source_sequence"] == source["sequence"]
    assert source["native_caster_end_turn"] == 2 and source["repeat_save_timing"] == "none"
    assert source["source_is_magical"] is False and source["cast_id"] == ""
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="no action"):
        _run("use_srd_native_mummy_glare", state, actor_state="native_mummy", target_state="other")
    assert state == before
    sibling = _run(
        "install_srd_timed_condition_source",
        state,
        target_state="other",
        caster_state="hero",
        lasting_state="state",
        spell="fear",
        condition="frightened",
        save_dc=10,
        duration_rounds=10,
        repeat_save_timing="none",
        save_ability="wisdom",
    )
    sibling_id = sibling.result["context"]["_timed_condition_source_id"]
    state = json.loads(json.dumps(state))
    _run("end_combat_turn", state)
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is True
    for _ in range(3):
        _run("advance_group_combat_encounter_turn", state)
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=actor, participant_data=data)
        _run("end_combat_turn", state)
        if actor == "native_mummy":
            break
    else:
        pytest.fail("The actual Mummy did not complete its next turn.")
    lasting = state[f"{SRD}.state"]
    assert lasting["timed_condition_sources"][source_id]["installed"] is False
    assert lasting["timed_condition_sources"][sibling_id]["installed"] is True
    assert "frightened" in state[f"{SRD}.entities"]["other"]["conditions"]
    assert state[f"{SRD}.entities"]["native_mummy"]["mummy_glare_source_ids"] == []


def test_native_mummy_glare_success_retains_individual_real_24_hour_immunity_after_reopen(
    native_mummy_glare_owner,
    monkeypatch,
):
    import json

    state, _ = _native_mummy_glare_case()
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = 30
    result = _run(
        "use_srd_native_mummy_glare", state, actor_state="native_mummy", target_state="other"
    )
    assert result.result["context"]["_mummy_glare_outcome"] == "success"
    until = state[f"{SRD}.entities"]["other"]["mummy_glare_immunities"]["native_mummy"]
    assert until == state[f"{SRD}.state"]["lasting_tick"] + 28800
    state = json.loads(json.dumps(state))
    rolls = []

    class ObservedDice(Random):
        def randint(self, low, high):
            rolls.append((low, high))
            return super().randint(low, high)

    monkeypatch.setattr(dice, "_rng", ObservedDice(1))
    result = _run(
        "resolve_srd_native_mummy_glare", state, actor_state="native_mummy", target_state="other"
    )
    assert result.result["context"]["_mummy_glare_outcome"] == "immune" and rolls == []
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28800)
    result = _run(
        "resolve_srd_native_mummy_glare", state, actor_state="native_mummy", target_state="other"
    )
    assert result.result["context"]["_mummy_glare_outcome"] == "success"
    assert rolls == [(1, 20)]


@pytest.mark.parametrize("obstacle", ["range", "wall", "plane", "stunned"])
def test_native_mummy_glare_scene_rejection_rolls_back_actual_action_before_any_save(
    native_mummy_glare_owner,
    monkeypatch,
    obstacle,
):
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, _ = _native_mummy_glare_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    target = next(row for row in scene["entities"] if row["id"] == "other")
    if obstacle == "range":
        target["position"] = [71, 0, 0]
    elif obstacle == "plane":
        target["metadata"]["plane"] = "ethereal"
    elif obstacle == "stunned":
        _run("apply_srd_condition", state, target="native_mummy", condition="stunned")
    else:
        scene["geometry"].append(_wall(1))
    before = deepcopy(state)

    class NoRoll(Random):
        def randint(self, low, high):
            pytest.fail("Scene rejection must precede every actual dice draw.")

    monkeypatch.setattr(dice, "_rng", NoRoll())
    with pytest.raises(
        RulesetPreconditionRejected, match="range60|must see|another plane|incapacitated"
    ):
        _run("use_srd_native_mummy_glare", state, actor_state="native_mummy", target_state="other")
    assert state == before


@pytest.mark.parametrize("victim,ward", [("hero", False), ("hero", True), ("native_mummy", False)])
def test_native_mummy_real_maximum_zero_death_and_paid_ward_negate_one_due_effect(
    native_mummy_receiver_owner,
    victim,
    ward,
):
    import json

    state, data, identity = _native_mummy_receiver_case("death_ward")
    if victim == "native_mummy":
        identity = _run(
            "install_srd_native_mummy_rot", state, actor_state="native_mummy", target_state=victim
        ).result["context"]["_mummy_rot_source_id"]
    # Every setup reduction is a real due3d6 effect on the original printed/prepared body.
    while state[f"{SRD}.entities"][victim]["max_hp"] > 18:
        due = state[f"{SRD}.state"]["timed_condition_sources"][identity][
            "native_next_reduction_tick"
        ]
        _run(
            "advance_srd_lasting_clock",
            state,
            lasting_state="state",
            elapsed_ticks=due - state[f"{SRD}.state"]["lasting_tick"],
        )
        _run(
            "resolve_srd_native_mummy_rot_boundary",
            state,
            target_state=victim,
            source_id=identity,
            lasting_state="state",
        )
    due = state[f"{SRD}.state"]["timed_condition_sources"][identity]["native_next_reduction_tick"]
    if ward:
        _run(
            "advance_srd_lasting_clock",
            state,
            lasting_state="state",
            elapsed_ticks=due - state[f"{SRD}.state"]["lasting_tick"] - 1,
        )
        _, _, params = _information_case("death_ward")
        slots = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"]
        _run(
            "cast_spell_outside_combat",
            state,
            participant_data=data,
            **(params | {"target": victim, "target_distance": 0}),
        )
        assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == slots - 1
    _run(
        "advance_srd_lasting_clock",
        state,
        lasting_state="state",
        elapsed_ticks=max(0, due - state[f"{SRD}.state"]["lasting_tick"]),
    )
    before = deepcopy(state[f"{SRD}.entities"][victim])
    state = json.loads(json.dumps(state))
    # Select an ordinary real RNG seed yielding18; the native dice owner still rolls all3d6.
    seed = 376
    dice._rng.seed(seed)
    result = _run(
        "resolve_srd_native_mummy_rot_boundary",
        state,
        target_state=victim,
        source_id=identity,
        lasting_state="state",
    ).result["context"]
    assert result["_native_mummy_daily_roll"]["results"] == [6, 6, 6]
    assert result["_native_mummy_daily_prevented"] is ward
    source = state[f"{SRD}.state"]["timed_condition_sources"][identity]
    assert source["installed"] is True and source["native_next_reduction_tick"] == due + 28800
    target = state[f"{SRD}.entities"][victim]
    if ward:
        assert target["is_alive"] is True and target["max_hp"] == before["max_hp"]
        assert target["hp"] == before["hp"]
        assert target["recovery_max_hp_reduction"] == before["recovery_max_hp_reduction"]
        assert (
            target["native_undead_max_hp_reduction_receipts"]
            == before["native_undead_max_hp_reduction_receipts"]
        )
        assert result["_native_mummy_daily_applied"] == 0
        assert not any(
            r["kind"] == "death_prevention" for r in state[f"{SRD}.state"]["turn_effects"]
        )
        _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28800)
        dice._rng.seed(seed)
        result = _run(
            "resolve_srd_native_mummy_rot_boundary", state, target_state=victim, source_id=identity
        ).result["context"]
        target = state[f"{SRD}.entities"][victim]
    assert result["_native_mummy_maximum_zero_death"] is True
    assert target["max_hp"] == target["hp"] == 0 and target["is_alive"] is False
    assert "dead" in target["conditions"] and "unconscious" not in target["conditions"]
    assert target["death_tick"] == state[f"{SRD}.state"]["lasting_tick"]
    assert target["recovery_max_hp_reduction"] == (
        before["recovery_max_hp_reduction"] + before["max_hp"]
    )
    assert target.get("body_available") == before.get("body_available")
    assert target.get("vital_parts_present") == before.get("vital_parts_present")
    assert target["native_undead_max_hp_reduction_receipts"][-1]["amount"] == before["max_hp"]


@pytest.fixture(scope="module")
def _native_mummy_fist_runtime(_native_mummy_glare_runtime):
    from scripts.srd_full.srd_native_mummy_fist import (
        _actual_composite_body,
        bind_native_mummy_fist_attack_consumers,
        extend_runtime,
    )
    from scripts.srd_full.srd_prismatic_damage_packets import PRESENTATION_FIELDS
    from scripts.srd_full.srd_qualified_packet_implementations import _canonical, _delegate
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
    from scripts.srd_full.srd_timed_senses import _replace_tools

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    text = _native_mummy_glare_runtime[1].decode()
    tools = _load_runtime(text)["tools"]
    original = {}
    changed = {}
    for composite in (False, True):
        owner = (
            "apply_srd_qualifying_composite_damage" if composite else "apply_srd_qualifying_damage"
        )
        core = (
            "execute_srd_qualified_composite_packet"
            if composite
            else "execute_srd_qualified_scalar_packet"
        )
        original[composite] = deepcopy(tools[owner])
        fields = PRESENTATION_FIELDS - ({"_hp_lost"} if composite else set())
        changed[core] = _canonical(tools[owner], fields)
        changed[owner] = _delegate(tools[owner], core, True, [])
    text = _replace_tools(text, changed)
    tools = _load_runtime(text)["tools"]
    for composite in (False, True):
        assert _actual_composite_body(tools, composite=composite) == original[composite]
    text = extend_runtime(text)
    assert extend_runtime(text) == text
    text = bind_native_mummy_fist_attack_consumers(text)
    assert bind_native_mummy_fist_attack_consumers(text) == text
    assert not validate_ruleset(text)
    runtime = _load_runtime(text)
    validate_content_shape("package_runtime", runtime, file="native-mummy-fist-receiver")
    return runtime, text.encode()


@pytest.fixture
def native_mummy_fist_owner(monkeypatch, _native_mummy_fist_runtime):
    from functools import lru_cache

    import tests.srd_full.test_srd_action_budget as budget
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    runtime, encoded = _native_mummy_fist_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="e" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    budget._tool_schema(
                        runtime["tools"][name],
                        tool_name="" if name == "restore_hit_points" else name,
                    )
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    return runtime


@pytest.mark.parametrize(
    "variant", ["critical_dust", "printed_necrotic_immunity", "death_ward", "miss"]
)
def test_native_mummy_real_fist_d20_typed_rolls_curse_and_true_death_boundary(
    native_mummy_fist_owner,
    monkeypatch,
    variant,
):
    import json

    import sylvia_gm.application.package_runtime as package_runtime

    state, data = _native_mummy_glare_case(death_ward=variant == "death_ward")
    _run("remove_srd_recovery_curses", state, target_state="hero", lasting_state="state")
    victim = "native_mummy" if variant == "printed_necrotic_immunity" else "hero"
    relation = _run(
        "qualify_srd_created_actor_attack_geometry",
        state,
        actor_state="native_mummy",
        target_state=victim,
    ).result["context"]
    before = deepcopy(state[f"{SRD}.entities"][victim])
    state = json.loads(json.dumps(state))
    stamps = []
    execute = package_runtime.execute_tool

    def observe_death(*args, **kwargs):
        result = execute(*args, **kwargs)
        if args[0] == "record_srd_actual_death" and result.success:
            stamps.append(args[0])
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", observe_death)
    dice._rng.seed(31 if variant == "miss" else 5)
    result = _run(
        "resolve_adjudicated_creature_attack",
        state,
        participant_data=data,
        actor="native_mummy",
        target=victim,
        attack_name="Rotting Fist",
        attack_modifier=5,
        damage_expression="d10",
        damage_modifier=3,
        damage_type="bludgeoning",
        attack_mode="melee",
        normal_range=5,
        long_range=5,
        attack_distance=relation["_created_attack_distance"],
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
        advantage=False,
        disadvantage=False,
        use_multiattack=False,
        attack_uses_weapon=False,
    ).result
    context = result["context"]
    target = state[f"{SRD}.entities"][victim]
    if variant == "miss":
        assert context["_attack_roll"]["results"] == [1]
        assert target["hp"] == before["hp"] and target.get("mummy_rot_source_ids") == before.get(
            "mummy_rot_source_ids"
        )
        assert "_mummy_fist_necrotic_roll" not in context
        assert stamps == []
        return
    assert context["_attack_roll"]["results"] == [20]
    assert context["_mummy_fist_necrotic_roll"]["count"] == 6
    identity = context["_mummy_rot_source_id"]
    source = state[f"{SRD}.state"]["timed_condition_sources"][identity]
    assert source["installed"] is True and source["save_dc"] is None
    assert source["native_body_source"] == "native_mummy" and target["hit_point_recovery_blocked"]
    assert "Fist Necrotic dice" in str(result["result_card"])
    if variant == "printed_necrotic_immunity":
        assert "necrotic" in target["damage_immunities"]
        assert context["_component_2_effective_damage"] == 0
        assert target["hp"] == before["hp"] - context["_component_1_effective_damage"]
        assert target["is_alive"] is True and context["_mummy_fist_dust"] is False
        assert stamps == []
    elif variant == "death_ward":
        assert target["hp"] == 1 and target["is_alive"] is True
        assert "dead" not in target["conditions"] and "unconscious" not in target["conditions"]
        assert context["_mummy_fist_dust"] is False and stamps == []
        assert not any(
            r["kind"] == "death_prevention" for r in state[f"{SRD}.state"]["turn_effects"]
        )
        assert state[f"{SRD}.state"].get("native_mummy_dust_bodies") is None
    else:
        assert target["hp"] == 0 and target["is_alive"] is False
        assert "dead" in target["conditions"] and "unconscious" not in target["conditions"]
        assert target["body_available"] is False and target["vital_parts_present"] is False
        assert context["_mummy_fist_dust"] is True
        dust = state[f"{SRD}.state"]["native_mummy_dust_bodies"][victim]
        assert dust["attacker"] == "native_mummy" and dust["native_attack"] == "Rotting Fist"
        assert dust["tick"] == target["death_tick"]
        assert stamps == ["record_srd_actual_death"]
