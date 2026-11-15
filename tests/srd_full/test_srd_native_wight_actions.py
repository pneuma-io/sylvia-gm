"""Native Wight Life Drain uses the actual save, typed receiving packet and maximum-loss owner."""

from copy import deepcopy

import pytest

from sylvia_gm import dice
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_summoning import (
    SRD,
    _admit_zombie_slam_target,
    _information_case,
    _recorded_corpse_case,
)
from tests.srd_full.test_srd_spell_summoning import (
    _native_mummy_fist_runtime as _native_mummy_fist_runtime,
)
from tests.srd_full.test_srd_spell_summoning import (
    _native_mummy_glare_runtime as _native_mummy_glare_runtime,
)


@pytest.fixture(scope="module")
def _native_wight_runtime(_native_mummy_fist_runtime):
    from scripts.srd_full.srd_damage_type_receipts import bind_runtime
    from scripts.srd_full.srd_native_mummy_fist import _actual_composite_body
    from scripts.srd_full.srd_native_wight_actions import (
        bind_native_wight_life_drain_consumers,
        extend_runtime,
    )
    from scripts.srd_full.srd_prismatic_damage_packets import PRESENTATION_FIELDS
    from scripts.srd_full.srd_qualified_packet_implementations import _canonical, _delegate
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
    from scripts.srd_full.srd_timed_senses import _replace_tools

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    text = _native_mummy_fist_runtime[1].decode()
    tools = _load_runtime(text)["tools"]
    text = _replace_tools(
        text,
        {
            "apply_srd_qualifying_damage": _actual_composite_body(tools, composite=False),
            "apply_srd_qualifying_composite_damage": _actual_composite_body(tools, composite=True),
        },
    )
    text = bind_runtime(text)
    assert bind_runtime(text) == text
    tools = _load_runtime(text)["tools"]
    expected = {}
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
        expected[composite] = deepcopy(tools[owner])
        fields = PRESENTATION_FIELDS - ({"_hp_lost"} if composite else set())
        changed[core] = _canonical(tools[owner], fields)
        changed[owner] = _delegate(tools[owner], core, True, [])
    text = _replace_tools(text, changed)
    tools = _load_runtime(text)["tools"]
    for composite in (False, True):
        assert _actual_composite_body(tools, composite=composite) == expected[composite]
    text = extend_runtime(text)
    assert extend_runtime(text) == text
    assert bind_native_wight_life_drain_consumers(text) == text
    assert not validate_ruleset(text)
    runtime = _load_runtime(text)
    validate_content_shape("package_runtime", runtime, file="native-wight-life-drain")
    return runtime, text.encode()


@pytest.fixture
def native_wight_owner(monkeypatch, _native_wight_runtime):
    from functools import lru_cache

    import tests.srd_full.test_srd_action_budget as budget
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    runtime, encoded = _native_wight_runtime

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


def _native_wight_case(variant):
    """Prepare authentic native bodies, source Temporary HP and one commanded real Wight turn."""
    maximum_case = variant in {"maximum_zero", "maximum_zero_ward"}
    spell = "false_life" if variant == "temporary_hp" or maximum_case else "create_undead"
    state, data = _recorded_corpse_case(spell)
    state[f"{SRD}.entities"]["guard"].update(inventory=[], equipment=[])
    state[f"{SRD}.entities"]["hero"]["creature_type"] = "humanoid"
    _run(
        "prepare_srd_source_wight",
        state,
        creature_id="native_wight",
        corpse_state="guard",
        source_caster="hero",
        other_languages=["infernal"],
    )
    _run(
        "finish_srd_wight_source_actor",
        state,
        actor_state="native_wight",
        corpse_state="guard",
        caster_state="hero",
        other_languages=["infernal"],
    )
    _run("update_srd_spell_force_scene", state, force_id="guard", position=[5, 0, 0], active=False)
    _run(
        "create_srd_source_actor_scene",
        state,
        caster_state="hero",
        summon_id="native_wight",
        spell="create_undead",
        range_feet=10,
        half_extent=2.5,
        creature_type="undead",
        created_actor_position=[5, 0, 0],
        created_actor_height=6,
    )
    _admit_zombie_slam_target(state)
    victim = "native_wight" if variant == "resistance" else "hero"
    if variant == "immunity":
        corpse = state[f"{SRD}.entities"]["other"]
        corpse.update(
            creature_kind="monster",
            creature_type="humanoid",
            living_creature_type="humanoid",
            size_rank=2,
            body_available=True,
            vital_parts_present=True,
            soul_willing=True,
            inventory=[],
            equipment=[],
        )
        _run(
            "apply_srd_damage",
            state,
            target="other",
            amount=corpse["hp"] + corpse["temp_hp"],
            adjustment=0,
            damage_type="bludgeoning",
            critical_hit=False,
        )
        _run(
            "prepare_srd_source_mummy",
            state,
            creature_id="immune_mummy",
            corpse_state="other",
            source_caster="hero",
            other_languages=["elvish", "infernal"],
        )
        _run(
            "finish_srd_mummy_source_actor",
            state,
            actor_state="immune_mummy",
            corpse_state="other",
            caster_state="hero",
            other_languages=["elvish", "infernal"],
        )
        _run(
            "update_srd_spell_force_scene",
            state,
            force_id="other",
            position=[10, 0, 0],
            active=False,
        )
        _run(
            "create_srd_source_actor_scene",
            state,
            caster_state="hero",
            summon_id="immune_mummy",
            spell="create_undead",
            range_feet=10,
            half_extent=2.5,
            creature_type="undead",
            created_actor_position=[10, 0, 0],
            created_actor_height=6,
        )
        victim = "immune_mummy"
    if maximum_case:
        from tests.srd_full.test_srd_attack_roll_components import _prepared_caster
        from tests.srd_full.test_srd_spell_wave3_information import _entity

        _prepared_caster(state, "warder", "death_ward", 4)
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
            _entity("warder", [-5, 0, 0], participant_ref="warder", creature_type="humanoid")
        )
    if variant == "temporary_hp" or maximum_case:
        _, _, parameters = _information_case("false_life")
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
        assert state[f"{SRD}.entities"]["hero"]["temp_hp"] >= 6
    _run(
        "command_srd_animated_undead",
        state,
        caster_state="hero",
        actor_ids=["native_wight"],
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
        combatants=["hero", "native_wight", "warder"]
        if maximum_case
        else ["hero", "native_wight", "immune_mummy"]
        if variant == "immunity"
        else ["hero", "native_wight", "other"],
    )
    for _ in range(3):
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=actor, participant_data=data)
        if actor == "native_wight":
            return state, data, victim
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
    pytest.fail("The actual Wight did not reach its independent turn.")


@pytest.mark.parametrize(
    "variant", ["ordinary", "resistance", "immunity", "temporary_hp", "save_success"]
)
def test_native_wight_actual_life_drain_save_typed_damage_and_maximum_receipt(
    native_wight_owner, variant
):
    import json

    state, data, victim = _native_wight_case(variant)
    body = state[f"{SRD}.entities"]["native_wight"]
    assert body["armor_class"] == 14 and body["inventory"] == ["Studded Leather Armor"]
    assert body["worn_armor"]["inventory_item"] == "Studded Leather Armor"
    assert body.get("equipment_profile") is None
    before = deepcopy(state[f"{SRD}.entities"][victim])
    state = json.loads(json.dumps(state))
    dice._rng.seed(5 if variant == "save_success" else 31)
    result = _run(
        "use_srd_native_wight_life_drain",
        state,
        participant_data=data,
        actor_state="native_wight",
        target_state=victim,
    ).result
    context = result["context"]
    assert "Maximum HP lost" in str(result["result_card"])
    target = state[f"{SRD}.entities"][victim]
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    if variant == "save_success":
        assert context["_wight_life_drain_outcome"] == "success"
        assert context["_save_total"] >= 13 and context["_wight_life_drain_roll"] is None
        assert target["hp"] == before["hp"] and target["max_hp"] == before["max_hp"]
        assert context["_wight_maximum_reduction"] == 0
        return
    assert context["_wight_life_drain_outcome"] == "failure" and context["_save_total"] < 13
    rolled = context["_wight_life_drain_roll"]["total"] + 2
    assert 3 <= rolled <= 10
    taken = 0 if variant == "immunity" else rolled // 2 if variant == "resistance" else rolled
    assert context["_wight_damage_taken"] == context["_wight_maximum_reduction"] == taken
    assert target["max_hp"] == before["max_hp"] - taken
    if taken:
        assert (
            target["recovery_max_hp_reduction"]
            == before.get("recovery_max_hp_reduction", 0) + taken
        )
        receipt = target["native_undead_max_hp_reduction_receipts"][-1]
        assert receipt["source"] == context["_wight_maximum_receipt_id"]
        assert receipt["target"] == victim and receipt["native_body_source"] == "native_wight"
        assert receipt["native_attack"] == "Life Drain" and receipt["damage_taken"] == taken
    else:
        assert target.get("native_undead_max_hp_reduction_receipts") is None
    if variant == "temporary_hp":
        assert context["_wight_temporary_absorbed"] == min(before["temp_hp"], taken)
        assert context["_wight_packet_hp_damage"] == max(0, taken - before["temp_hp"])
        assert taken > context["_wight_packet_hp_damage"]
    assert target["is_alive"] is True and not target.get("has_native_mummy_rot", False)


def _next_wight_turn(state, data, *, refill=False, ward=False):
    """Use genuine turn boundaries and optional real PC/NPC casting on their own turns."""
    visited = set()
    for _ in range(3):
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=actor, participant_data=data)
        if actor == "native_wight":
            assert not refill or "hero" in visited
            assert not ward or "warder" in visited
            return
        visited.add(actor)
        if actor == "hero":
            _run(
                "command_srd_animated_undead",
                state,
                participant_data=data,
                caster_state="hero",
                actor_ids=["native_wight"],
                order_kind="specific",
                action="attack",
            )
        if actor == "hero" and refill:
            _, _, parameters = _information_case("false_life")
            before = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"]
            _run("cast_spell", state, participant_data=data, **parameters)
            assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == before - 1
            assert state[f"{SRD}.entities"]["hero"]["temp_hp"] >= 6
        if actor == "warder" and ward:
            _, _, parameters = _information_case("death_ward")
            parameters.pop("caster")
            parameters.pop("slot_resource")
            parameters.update(caster_state="warder", target="hero", targets=["hero"])
            _run("cast_spell", state, participant_data=data, **parameters)
    pytest.fail("The actual Wight did not return after the three admitted turns.")


@pytest.mark.parametrize("ward", [False, True])
def test_native_wight_maximum_zero_death_and_actual_ward_with_temporary_hp(
    native_wight_owner,
    monkeypatch,
    ward,
):
    import json

    import sylvia_gm.application.package_runtime as package_runtime

    state, data, _ = _native_wight_case("maximum_zero_ward" if ward else "maximum_zero")
    original_maximum = state[f"{SRD}.entities"]["hero"]["max_hp"]
    while state[f"{SRD}.entities"]["hero"]["max_hp"] > 3:
        dice._rng.seed(165)
        context = _run(
            "use_srd_native_wight_life_drain",
            state,
            participant_data=data,
            actor_state="native_wight",
            target_state="hero",
        ).result["context"]
        assert context["_save_roll"]["results"] == [1]
        assert context["_wight_life_drain_roll"]["results"] == [1]
        assert context["_wight_damage_taken"] == 3
        assert state[f"{SRD}.entities"]["hero"]["is_alive"] is True
        _next_wight_turn(state, data)
    # The final packet is truly absorbed by paid False Life, isolating maximum-zero death.
    _next_wight_turn(state, data, refill=True, ward=ward)
    before = deepcopy(state[f"{SRD}.entities"]["hero"])
    assert 0 < before["max_hp"] <= 3 and before["temp_hp"] >= 3
    state = json.loads(json.dumps(state))
    stamped = []
    execute = package_runtime.execute_tool

    def observe(*args, **kwargs):
        result = execute(*args, **kwargs)
        if args[0] == "record_srd_actual_death" and result.success:
            stamped.append(args[0])
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", observe)
    dice._rng.seed(165)
    context = _run(
        "use_srd_native_wight_life_drain",
        state,
        participant_data=data,
        actor_state="native_wight",
        target_state="hero",
    ).result["context"]
    hero = state[f"{SRD}.entities"]["hero"]
    assert context["_wight_damage_taken"] == context["_wight_temporary_absorbed"] == 3
    assert context["_wight_packet_hp_damage"] == 0
    assert context["_wight_maximum_prevented"] is ward
    if ward:
        assert hero["is_alive"] is True and hero["hp"] == before["hp"]
        assert hero["max_hp"] == before["max_hp"]
        assert (
            hero["native_undead_max_hp_reduction_receipts"]
            == before["native_undead_max_hp_reduction_receipts"]
        )
        assert context["_wight_maximum_reduction"] == 0 and stamped == []
        assert not any(
            e["kind"] == "death_prevention" for e in state[f"{SRD}.state"]["turn_effects"]
        )
        _next_wight_turn(state, data)
        dice._rng.seed(165)
        context = _run(
            "use_srd_native_wight_life_drain",
            state,
            participant_data=data,
            actor_state="native_wight",
            target_state="hero",
        ).result["context"]
        hero = state[f"{SRD}.entities"]["hero"]
    assert context["_wight_maximum_zero_death"] is True
    assert hero["max_hp"] == hero["hp"] == 0 and hero["is_alive"] is False
    assert "dead" in hero["conditions"] and "unconscious" not in hero["conditions"]
    assert hero["death_tick"] == state[f"{SRD}.state"]["lasting_tick"]
    assert hero["recovery_max_hp_reduction"] == original_maximum
    assert hero["native_undead_max_hp_reduction_receipts"][-1]["amount"] == before["max_hp"]
    assert not hero.get("has_native_mummy_rot", False)
    assert stamped == ["record_srd_actual_death"]


def test_actual_wight_maximum_receipt_restores_at_real_long_rest(native_wight_owner):
    import json

    state, data, _ = _native_wight_case("ordinary")
    original = state[f"{SRD}.entities"]["hero"]["max_hp"]
    dice._rng.seed(165)
    context = _run(
        "use_srd_native_wight_life_drain",
        state,
        participant_data=data,
        actor_state="native_wight",
        target_state="hero",
    ).result["context"]
    assert context["_wight_maximum_reduction"] == 3
    _run("end_combat_encounter", state, participant_data=data, end_reason="fighting_stopped")
    state = json.loads(json.dumps(state))
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
