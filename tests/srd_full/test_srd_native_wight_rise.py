"""Native Life Drain death, exact 24-hour rise, real corpse cancellation and Zombie consumers."""

import hashlib
import importlib.util
import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.srd_divination_spells import _normalize
from scripts.srd_full.srd_native_mummy_fist import _actual_composite_body
from scripts.srd_full.srd_native_wight_rise import (
    _bind_lethal,
    bind_native_wight_rise_consumers,
    extend_runtime,
)
from scripts.srd_full.srd_parameter_declarations import compact_runtime, restore_runtime
from scripts.srd_full.srd_spell_summoning import _zombie_source_profile
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_summoning import SRD, _information_case, _recorded_corpse_case
from tests.srd_full.test_srd_spell_wave3_information import _entity

ROOT = Path(__file__).resolve().parents[2]
DEPENDENCIES = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-native-wight-rise/input-1")
)


def _factored_binding_fragment():
    """Read only the exact cached packet entries and their pooled parameter declarations."""
    raw = (DEPENDENCIES / "wight-public-parent-runtime.yaml").read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest()
        == "00a7de081d9880701c9bd84f339b10b53ff7eb99a945fc5e39f19b5e325099d5"
    )
    parent = raw.decode()
    blocks = []
    for name in (
        "apply_srd_native_wight_life_drain_packet",
        "apply_srd_qualifying_damage",
        "apply_srd_qualifying_composite_damage",
        "execute_srd_qualified_scalar_packet",
        "execute_srd_qualified_composite_packet",
    ):
        start = re.search(rf"(?m)^  {name}:\r?$", parent)
        assert start is not None
        stop = re.search(r"(?m)^(?:  [a-zA-Z0-9_]+:|[a-zA-Z0-9_]+:)", parent[start.end() :])
        assert stop is not None
        blocks.append(parent[start.start() : start.end() + stop.start()])
    pool = parent[parent.index("\nparameter_declarations:") :]
    return restore_runtime("tools:\n" + "".join(blocks) + "procedures: {}\n" + pool)


def _expanded_binding_parity(tools):
    """Compare the core adapter with the same original owning bodies before factorization."""
    expected = {}
    for name, composite, qualified in (
        ("apply_srd_damage", False, False),
        ("apply_srd_composite_damage", True, False),
        ("apply_srd_qualifying_damage", False, True),
        ("apply_srd_qualifying_composite_damage", True, True),
    ):
        if name not in tools:
            continue
        packet = _actual_composite_body(tools, composite=composite) if qualified else tools[name]
        expected[name] = _bind_lethal(packet, composite, qualified)
    _normalize(expected)
    return expected


@pytest.mark.parametrize("composite", [False, True])
def test_authentic_factored_binding_preserves_delegate_and_rejects_changed_forwarding(composite):
    text = _factored_binding_fragment()
    original = _load_runtime(text)["tools"]
    name = "apply_srd_qualifying_composite_damage" if composite else "apply_srd_qualifying_damage"
    core = original[name]["effects"][0]["call"]
    bound_text = bind_native_wight_rise_consumers(text)
    assert bind_native_wight_rise_consumers(bound_text) == bound_text
    bound = _load_runtime(bound_text)["tools"]
    assert bound[name] == original[name]
    expected = _expanded_binding_parity(original)
    assert _actual_composite_body(bound, composite=composite) == expected[name]
    assert bound[core]["parameters"] == original[core]["parameters"]

    changed = deepcopy(original[name])
    changed["effects"][0]["params"]["render_damage_snapshot"] = False
    rejected = _replace_tools(text, {name: changed})
    with pytest.raises(ValueError, match="exact named delegate"):
        bind_native_wight_rise_consumers(rejected)
    assert _load_runtime(rejected)["tools"][core] == original[core]


@pytest.fixture(scope="module")
def rise_runtime():
    """Compose the exact source-qualified cached public Wight, with current original packets."""
    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    raw = (DEPENDENCIES / "wight-public-parent-runtime.yaml").read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest()
        == "00a7de081d9880701c9bd84f339b10b53ff7eb99a945fc5e39f19b5e325099d5"
    )
    text = restore_runtime(raw.decode())
    tools = _load_runtime(text)["tools"]
    wrappers = {
        name: deepcopy(tools[name])
        for name in ("apply_srd_qualifying_damage", "apply_srd_qualifying_composite_damage")
    }
    text = extend_runtime(text)
    assert extend_runtime(text) == text
    text = bind_native_wight_rise_consumers(text)
    assert bind_native_wight_rise_consumers(text) == text
    factored = _load_runtime(text)["tools"]
    expected = _expanded_binding_parity(tools)
    for name, composite in (
        ("apply_srd_qualifying_damage", False),
        ("apply_srd_qualifying_composite_damage", True),
    ):
        assert factored[name] == wrappers[name]
        core = wrappers[name]["effects"][0]["call"]
        assert core == (
            "execute_srd_qualified_composite_packet"
            if composite
            else "execute_srd_qualified_scalar_packet"
        )
        expanded_core = _actual_composite_body(factored, composite=composite)
        # Binding the shared core is identical to binding its authentic expanded owning entry.
        assert expanded_core == expected[name]
    text = _fresh_native_attack_window(text)
    expanded = _load_runtime(text)
    text = compact_runtime(text)
    assert _load_runtime(restore_runtime(text)) == expanded
    assert not validate_ruleset(text)
    validate_content_shape("package_runtime", _load_runtime(text), file="native-wight-rise")
    return expanded, text.encode()


def _fresh_native_attack_window(text):
    """Derive the actual saved creature attack from its fresh source-qualified owning leaf."""
    path = DEPENDENCIES / "srd_reaction_spells.py"
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == "114e766466d932b1a5dd70c535c6eb8f6c5196986925727f8d9c7a17f3269751"
    )
    spec = importlib.util.spec_from_file_location(
        "scripts._native_wight_rise_reaction_parent", path
    )
    assert spec is not None and spec.loader is not None
    reaction = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reaction)
    tools = _load_runtime(text)["tools"]
    windows = reaction._attack_window(
        tools["resolve_adjudicated_creature_attack"],
        "begin_srd_creature_attack",
        "actor",
        "creature_attack",
    )
    windows["complete_srd_pending_action"] = reaction._complete_window(tools | windows)
    return _replace_tools(text, windows)


@pytest.fixture
def rise_owner(monkeypatch, rise_runtime):
    import tests.srd_full.test_srd_action_budget as budget
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    runtime, encoded = rise_runtime

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


def _controller():
    """Use the genuine printed source Wight from an actual consumed original corpse."""
    state, data = _recorded_corpse_case("revivify")
    state[f"{SRD}.entities"]["guard"].update(inventory=[], equipment=[])
    _run(
        "prepare_srd_source_wight",
        state,
        creature_id="native_wight",
        corpse_state="guard",
        source_caster="hero",
        other_languages=["infernal"],
    )
    _run("update_srd_spell_force_scene", state, force_id="guard", position=[20, 0, 0], active=False)
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
        requires_sight=False,
    )
    return state, data


def _victim(state, identity="rise_victim"):
    """Admit an existing empty-handed Commoner using its exact printed receiving statistics."""
    row = json.loads(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/non-beast-form-index.json").read_text()
    )["forms"]["commoner"]
    assert (
        row["source_stat_block_sha256"]
        == "82a9f8487229bb0b4060a40b3f4bc9502a5fd5c9961cf9f1350f7793a150f328"
    )
    _run(
        "prepare_srd_creature",
        state,
        creature_id=identity,
        name=identity,
        source="SRD5.2.1 Commoner p275; actual empty-handed body, native attacks not used here.",
        creature_type="humanoid",
        armor_class=row["armor_class"],
        hit_points=row["hit_points"],
        speed=row["walk_speed"],
        size_rank=row["size_rank"],
        initiative_modifier=row["initiative_modifier"],
        proficiency_bonus=2,
        **{a + "_score": v for a, v in row["ability_scores"].items()},
        **{a + "_save": v for a, v in row["saving_throw_modifiers"].items()},
    )
    body = state[f"{SRD}.entities"][identity]
    body.update(
        body_available=True,
        vital_parts_present=True,
        soul_willing=True,
        living_creature_type="humanoid",
        languages=["common"],
        inventory=[],
        equipment=[],
    )
    scene = _entity(identity, [10, 0, 0], participant_ref=identity, creature_type="humanoid")
    scene.update(footprint={"kind": "box", "half_extents": [2.5, 2.5]}, height=6)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(scene)
    _run(
        "apply_srd_damage",
        state,
        target=identity,
        amount=2,
        adjustment=0,
        damage_type="bludgeoning",
        critical_hit=False,
    )
    body = state[f"{SRD}.entities"][identity]
    assert body["is_alive"] is True and body["hp"] == 2 and body.get("death_tick") is None
    return body


def _slay(state, identity="rise_victim"):
    _victim(state, identity)
    dice._rng.seed(165)
    result = _run(
        "resolve_srd_native_wight_life_drain",
        state,
        actor_state="native_wight",
        target_state=identity,
    )
    assert result.result["context"]["_wight_life_drain_roll"]["results"] == [1]
    body = state[f"{SRD}.entities"][identity]
    assert body["is_alive"] is False and body["hp"] == 0 and body["max_hp"] == 1
    assert body["death_tick"] == state[f"{SRD}.state"]["lasting_tick"]
    rid = body["native_wight_rise_id"]
    record = state[f"{SRD}.state"]["native_wight_rise_records"][rid]
    assert record["deadline"] == body["death_tick"] + 28800 and record["attack"] == "Life Drain"
    _run(
        "admit_srd_native_wight_rise_language",
        state,
        corpse_state=identity,
        other_language="elvish",
    )
    return rid


def test_real_life_drain_rise_source_body_persistence_fortitude_and_magic_cleanup(rise_owner):
    state, data = _controller()
    rid = _slay(state)
    original = deepcopy(state[f"{SRD}.entities"]["rise_victim"])
    slot_before = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28799)
    assert "wight_zombie_1" not in state[f"{SRD}.entities"]
    state = json.loads(json.dumps(state))
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    actor = state[f"{SRD}.entities"]["wight_zombie_1"]
    corpse = state[f"{SRD}.entities"]["rise_victim"]
    saved = state[f"{SRD}.state"]
    source = _zombie_source_profile()
    assert actor["hp"] == actor["max_hp"] == 15 and actor["armor_class"] == 8
    assert actor["initiative_modifier"] == -2 and actor["speed"] == 20
    assert actor["ability_scores"] == source["ability_scores"]
    assert actor["saving_throw_modifiers"] == source["saving_throw_modifiers"]
    assert actor["condition_immunities"] == ["exhaustion", "poisoned"]
    assert actor["created_spell_id"] is None and actor["created_caster_state_id"] is None
    assert actor["created_origin_kind"] == "wight_life_drain"
    assert actor["native_wight_controller_id"] == "native_wight"
    assert actor["created_body_source_state_id"] == "rise_victim"
    assert actor["created_body_source_id"] == "rise_victim"
    assert actor["created_source_block_sha256"] == source["source_stat_block_sha256"]
    assert (
        actor["created_understood_languages"] == ["common", "elvish"] and actor["languages"] == []
    )
    assert (
        actor.get("animated_control_active") is None and actor.get("animated_control_until") is None
    )
    assert "wight_zombie_1" not in saved["animated_actor_ids"]
    assert saved["has_animated_undead"] is False
    assert corpse["death_tick"] == original["death_tick"] and corpse["body_available"] is False
    assert corpse["body_animated_actor_id"] == "wight_zombie_1"
    scene = next(row for row in saved["spatial_scene"]["entities"] if row["id"] == "wight_zombie_1")
    assert scene["metadata"]["source_caster"] is None
    assert scene["metadata"]["native_controller"] == "native_wight"
    assert saved["native_wight_rise_records"][rid]["status"] == "risen"
    native = saved["created_actor_records"]["wight_zombie_1"]
    assert native["spell"] is None and native["caster"] is None
    assert native["native_origin"] == "wight_life_drain" and native["controller"] == "native_wight"
    assert state[f"{SRD}.entities"]["hero"]["resources"] == slot_before
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    _run("refresh_srd_all_animated_undead_control", state)
    actor = state[f"{SRD}.entities"]["wight_zombie_1"]
    assert actor["created_active"] is True and actor["hp"] == 15
    initiative = _run(
        "classify_srd_summon_initiative_entry", state, creature_state="wight_zombie_1"
    ).result["context"]
    assert initiative["_initiative_is_summon"] is False
    assert actor["summon_active"] is False and actor["summon_caster_state_id"] is None
    dice._rng.seed(5)
    _run(
        "apply_srd_damage",
        state,
        target="wight_zombie_1",
        amount=16,
        adjustment=0,
        damage_type="bludgeoning",
        critical_hit=False,
    )
    actor = state[f"{SRD}.entities"]["wight_zombie_1"]
    assert actor["hp"] == 1 and actor["is_alive"] is True
    assert (
        actor["last_undead_fortitude"]["dc"] == 21
        and actor["last_undead_fortitude"]["success"] is True
    )
    assert actor["death_tick"] is None and actor["body_available"] is True
    _run(
        "apply_srd_qualifying_damage",
        state,
        target="wight_zombie_1",
        amount=1,
        adjustment=0,
        damage_type="bludgeoning",
        source_state_id="native_wight",
        critical_hit=True,
    )
    actor = state[f"{SRD}.entities"]["wight_zombie_1"]
    assert actor["is_alive"] is False and actor["body_available"] is True
    assert actor["created_active"] is True
    scene = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "wight_zombie_1"
    )
    assert scene["active"] is True
    assert actor["last_undead_fortitude"]["critical"] is True
    count = _run(
        "count_srd_native_wight_controlled_zombies", state, controller_state="native_wight"
    )
    assert count.result["context"]["_native_wight_controlled_count"] == 0


@pytest.mark.parametrize("composite", [False, True])
def test_factored_qualified_native_fortitude_then_actual_radiant_bypass(rise_owner, composite):
    state, data = _controller()
    _slay(state)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28800)
    parameters = {
        "target": "wight_zombie_1",
        "source_state_id": "native_wight",
        "critical_hit": False,
    }
    if composite:
        owner = "apply_srd_qualifying_composite_damage"
        parameters.update(
            component_1_amount=8,
            component_1_damage_type="bludgeoning",
            component_2_amount=8,
            component_2_damage_type="necrotic",
        )
    else:
        owner = "apply_srd_qualifying_damage"
        parameters.update(amount=16, adjustment=0, damage_type="bludgeoning")
    dice._rng.seed(5)
    _run(owner, state, **parameters)
    body = state[f"{SRD}.entities"]["wight_zombie_1"]
    assert body["is_alive"] is True and body["hp"] == 1 and body["death_tick"] is None
    assert body["last_undead_fortitude"]["dc"] == 21
    assert body["last_undead_fortitude"]["success"] is True
    if composite:
        parameters.update(
            component_1_amount=0,
            component_2_amount=1,
            component_2_damage_type="radiant",
        )
    else:
        parameters.update(amount=1, damage_type="radiant")
    _run(owner, state, **parameters)
    body = state[f"{SRD}.entities"]["wight_zombie_1"]
    assert body["is_alive"] is False and body["hp"] == 0 and body["death_tick"] is not None
    assert body["last_undead_fortitude"]["radiant"] is True
    assert body["last_undead_fortitude"]["critical"] is False
    assert body["last_undead_fortitude"]["success"] is False


def test_native_risen_zombie_real_independent_turn_saved_slam_and_single_action(rise_owner):
    state, data = _controller()
    _slay(state)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28800)
    _victim(state, "slam_target")
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    target_row = next(row for row in scene if row["id"] == "slam_target")
    target_row["position"] = [20, 0, 0]
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = (
        data["abilities"]["dexterity"] - 10
    ) // 2
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "native_wight", "wight_zombie_1", "slam_target"],
    )
    for _ in range(8):
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=actor)
        if actor == "wight_zombie_1":
            break
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
    else:
        pytest.fail("This actual native Zombie never reached its independent turn.")
    zombie = state[f"{SRD}.entities"]["wight_zombie_1"]
    assert zombie.get("animated_turn_order") is None and zombie.get("dodging") is not True
    relation = _run(
        "qualify_srd_created_actor_attack_geometry",
        state,
        actor_state="wight_zombie_1",
        target_state="slam_target",
    ).result["context"]
    assert relation["_created_attack_distance"] == 5
    dice._rng.seed(5)
    parameters = {
        "actor": "wight_zombie_1",
        "target": "slam_target",
        "attack_name": "Slam",
        "attack_modifier": 3,
        "damage_expression": "d8",
        "damage_modifier": 1,
        "damage_type": "bludgeoning",
        "attack_mode": "melee",
        "normal_range": 5,
        "long_range": 5,
        "attack_distance": relation["_created_attack_distance"],
        "actor_can_see_target": True,
        "target_can_see_actor": True,
        "target_is_charmer": False,
        "fear_source_in_sight": False,
        "advantage": False,
        "disadvantage": False,
        "use_multiattack": False,
        "attack_uses_weapon": False,
    }
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="genuine printed Slam"):
        _run("begin_srd_creature_attack", state, **(parameters | {"damage_modifier": 99}))
    assert state == before
    begun = _run("begin_srd_creature_attack", state, **parameters)
    pending = begun.result["context"]["_pending_action_id"]
    saved_roll = deepcopy(state[f"{SRD}.state"]["pending_srd_actions"][pending]["attack_roll"])
    assert saved_roll["results"] == [20]
    assert state[f"{SRD}.entities"]["slam_target"]["hp"] == 2
    state = json.loads(json.dumps(state))
    completed = _run("complete_srd_pending_action", state, pending_action_id=pending)
    context = completed.result["context"]
    assert context["_attack_roll"] == saved_roll and context["_attack_outcome"] == "critical_hit"
    assert context["_damage_roll"]["count"] == 2
    assert context["_damage_applied"] == context["_damage_roll"]["total"] + 1
    assert state[f"{SRD}.entities"]["slam_target"]["is_alive"] is False
    assert completed.result["result_card"]["title"] == "Attack"
    assert completed.result["result_card"]["details"]
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0


@pytest.mark.parametrize("cancel", ["body_destroyed", "real_revivify"])
def test_actual_original_body_cancellation_preserves_death_and_source_costs(rise_owner, cancel):
    state, data = _controller()
    rid = _slay(state)
    corpse = state[f"{SRD}.entities"]["rise_victim"]
    death = corpse["death_tick"]
    _run(
        "record_srd_deceased_body",
        state,
        target_state="rise_victim",
        death_tick=death,
        old_age=False,
        soul_willing=True,
        body_available=True,
        vital_parts_present=True,
        living_creature_type="humanoid",
        name="Victim",
    )
    if cancel == "body_destroyed":
        _run("record_srd_native_wight_body_destruction", state, corpse_state="rise_victim")
        corpse = state[f"{SRD}.entities"]["rise_victim"]
    if cancel == "real_revivify":
        _, _, params = _information_case("revivify")
        params.update(target="rise_victim", targets=["rise_victim"])
        before = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"]
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
        corpse = state[f"{SRD}.entities"]["rise_victim"]
        assert corpse["is_alive"] is True and corpse["hp"] == 1 and corpse["death_tick"] is None
        assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == before - 1
        restored = state[f"{SRD}.state"]["native_wight_rise_records"][rid]
        assert restored["active"] is False and restored["status"] == "cancelled_restored_to_life"
        assert state[f"{SRD}.state"]["lasting_tick"] == death
        state = json.loads(json.dumps(state))
        _run(
            "apply_srd_damage",
            state,
            target="rise_victim",
            amount=1,
            adjustment=0,
            damage_type="bludgeoning",
            critical_hit=False,
        )
        corpse = state[f"{SRD}.entities"]["rise_victim"]
        assert corpse["is_alive"] is False and corpse["death_tick"] == death
        assert corpse["native_wight_rise_id"] == rid
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28800)
    assert state[f"{SRD}.state"]["native_wight_rise_records"][rid]["status"] == (
        "cancelled_restored_to_life" if cancel == "real_revivify" else "cancelled_body_destroyed"
    )
    assert "wight_zombie_1" not in state[f"{SRD}.entities"]
    if cancel == "body_destroyed":
        assert corpse["death_tick"] == death and corpse["body_available"] is False


def test_unavailable_original_body_is_not_asserted_destroyed(rise_owner):
    state, data = _controller()
    rid = _slay(state)
    corpse = state[f"{SRD}.entities"]["rise_victim"]
    _run(
        "record_srd_deceased_body",
        state,
        target_state="rise_victim",
        death_tick=corpse["death_tick"],
        old_age=False,
        soul_willing=True,
        body_available=False,
        vital_parts_present=True,
        living_creature_type="humanoid",
        name="Victim",
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="unavailability"):
        _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28800)
    assert state == before
    assert state[f"{SRD}.state"]["native_wight_rise_records"][rid]["active"] is True


def test_other_actual_death_is_not_life_drain_and_late_deadline_rejects_atomically(rise_owner):
    state, data = _controller()
    _victim(state, "other_death")
    _run(
        "apply_srd_damage",
        state,
        target="other_death",
        amount=2,
        adjustment=0,
        damage_type="bludgeoning",
        critical_hit=False,
    )
    assert state[f"{SRD}.entities"]["other_death"]["death_tick"] is not None
    assert state[f"{SRD}.entities"]["other_death"].get("native_wight_rise_id") is None
    _slay(state)
    original = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="deadline"):
        _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28801)
    assert state == original


def test_twelve_genuine_living_owned_bodies_bound_and_real_source_receipt_ids(rise_owner):
    state, data = _controller()
    receipts = []
    for ordinal in range(1, 14):
        identity = "corpse_" + str(ordinal)
        rid = _slay(state, identity)
        body = state[f"{SRD}.entities"][identity]
        receipts.append(body["native_undead_max_hp_reduction_receipts"][0]["source"])
        # These are admitted current corpse positions, not a corpse movement action.
        row = next(
            e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == identity
        )
        row["position"] = [20 + 10 * ordinal, 0, 0]
        if ordinal == 13:
            before = deepcopy(state)
            with pytest.raises(RulesetPreconditionRejected, match="twelve"):
                _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28800)
            assert state == before
        else:
            _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28800)
            assert state[f"{SRD}.state"]["native_wight_rise_records"][rid]["status"] == "risen"
    assert len(set(receipts)) == 13
    assert receipts == ["wight_life_drain_" + str(i) for i in range(1, 14)]
    count = _run(
        "count_srd_native_wight_controlled_zombies", state, controller_state="native_wight"
    )
    assert count.result["context"]["_native_wight_controlled_count"] == 12
