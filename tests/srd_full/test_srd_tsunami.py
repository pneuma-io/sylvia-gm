"""A real minute cast feeds actual caster turns, water movement and saved exit falls."""

import hashlib
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_tsunami import (
    PROFILES,
    PUBLIC_TOOLS,
    bind_runtime_consumers,
    extend_runtime,
    validate_profiles,
)

from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel import _case as spell_case
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case

BASE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-tsunami/runtime.yaml")
)


@lru_cache
def _runtime():
    encoded = BASE.read_bytes()
    assert (
        hashlib.sha256(encoded).hexdigest()
        == "e3c4858d2b42efd1b4aba54ae570ca55a53cb87f83b6eab9b8b894fe0ad1a813"
    )
    text = bind_runtime_consumers(extend_runtime(encoded.decode().replace("\r\n", "\n")))
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(deepcopy(PROFILES))
    for name in (
        "cast_spell",
        "cast_spell_node",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        "begin_srd_spell_action",
    ):
        for p in runtime["tools"].get(name, {}).get("parameters", []):
            if p["name"] == "spell" and "enum_values" in p:
                p["enum_values"] = sorted(set(p["enum_values"]) | PROFILES.keys())
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@pytest.fixture
def tsunami_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    monkeypatch.setattr(scope, "EXTRA_PUBLIC_TOOLS", frozenset(PUBLIC_TOOLS))
    execute = package_runtime.execute_tool

    def evidence(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print("Actual Tsunami failure:", result.tool_name, result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", evidence)
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _case(_spell="tsunami"):
    state, data = _tk_case("tsunami")
    _, _, params = spell_case("tsunami")
    params.pop("targets", None)
    params.update(
        tsunami_origin=[25, 0, 0], tsunami_length=100, tsunami_height=100, tsunami_thickness=50
    )
    body = next(b for b in state[f"{SRD}.state"]["spatial_scene"]["entities"] if b["id"] == "hero")
    body["position"] = [-20, 0, 0]
    next(b for b in state[f"{SRD}.state"]["spatial_scene"]["entities"] if b["id"] == "orb")[
        "position"
    ] = [8, 40, 0]
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(skill_check_modifiers={"athletics": 0})
    return state, data, params


def _source(state):
    return state[f"{SRD}.state"]["tsunami_sources"][-1]


def _complete(state, data, params):
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
    return _run("cast_spell_outside_combat", state, participant_data=data, **params)


def test_real_paid_wall_carries_gargantuan_but_only_huge_takes_later_damage(
    tsunami_scope, monkeypatch
):
    assert validate_profiles()
    state, data, params = _case()
    giant = deepcopy(state[f"{SRD}.entities"]["guard"])
    giant.update(name="Gargantuan creature", size_rank=5)
    state[f"{SRD}.entities"]["giant"] = giant
    scene = state[f"{SRD}.state"]["spatial_scene"]
    giant_body = deepcopy(next(b for b in scene["entities"] if b["id"] == "guard"))
    giant_body.update(id="giant", position=[5, 35, 0], height=20)
    giant_body["footprint"]["half_extents"] = [10, 10]
    giant_body["metadata"]["participant_ref"] = "giant"
    scene["entities"].append(giant_body)
    next(b for b in scene["entities"] if b["id"] == "orb")["position"] = [300, 80, 0]
    # Initial six dice, initial save, first five dice/save, then four dice/save.
    actual = _TravelDice(
        [(10, 2)] * 6 + [(20, 1), (20, 20)] + [(10, 3)] * 5 + [(20, 1)] + [(10, 4)] * 4 + [(20, 1)]
    )
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    paid = _complete(state, data, params)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 488
    assert _source(state)["height"] == 100 and _source(state)["damage_dice"] == 5
    assert "guard" in str(paid.result["result_card"]["details"])
    assert state[f"{SRD}.entities"]["giant"]["hp"] == 494
    assert actual.consumed == [(10, 2)] * 6 + [(20, 1), (20, 20)]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="authentic paid water"):
        _run(
            "move_srd_tsunami_subject",
            state,
            subject="guard",
            destination=[55, 0, 0],
            plane="material",
        )
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="Athletics"):
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="guard",
            destination=[10, 0, 0],
            mode="walk",
        )
    assert state == before
    state[f"{SRD}.combat"].update(encounter_active=True, round=1, current_actor="hero")
    # The source was cast outside an encounter; first actual caster start advances it.
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    assert _point(state, "guard") == [55, 0, 0]
    assert _point(state, "giant") == [55, 35, 0]
    assert state[f"{SRD}.entities"]["giant"]["hp"] == 494
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 473
    assert _source(state)["event_faces"] == [3] * 5
    _run("end_combat_turn", state, actor="hero")
    assert _source(state)["height"] == 50 and _source(state)["damage_dice"] == 4
    state[f"{SRD}.combat"]["current_actor"] = "hero"
    state[f"{SRD}.combat"]["round"] = 2
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    assert _point(state, "guard") == [105, 0, 0]
    assert _point(state, "giant") == [105, 35, 0]
    assert state[f"{SRD}.entities"]["giant"]["hp"] == 494
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 457
    assert _source(state)["event_faces"] == [4] * 4
    _run("end_combat_turn", state, actor="hero")
    assert not _source(state)["active"] and state[f"{SRD}.state"]["has_tsunami_pending_exits"]


def test_paid_wave_carries_living_zero_hp_character_with_automatic_strength_failure(
    tsunami_scope, monkeypatch
):
    state, data, params = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    hero_body = next(body for body in scene["entities"] if body["id"] == "hero")
    hero_body["position"] = [5, 0, 0]
    mage_body = deepcopy(hero_body)
    mage_body.update(id="mage", position=[-50, 0, 0])
    mage_body["metadata"]["participant_ref"] = "mage"
    scene["entities"] = [hero_body, mage_body]
    _run(
        "prepare_srd_creature",
        state,
        creature_id="mage",
        name="NPC Wave Caster",
        source="Synthetic stat block: 25 HP, Speed 30, Wisdom +3, DC 15, CON save +6.",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=6,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[{"spell": "tsunami", "uses_per_day": 1, "casting_level": 8}],
    )
    hero = state[f"{SRD}.entities"]["hero"]
    _run(
        "apply_srd_damage",
        state,
        target="hero",
        amount=hero["hp"] + 1,
        adjustment=0,
        damage_type="bludgeoning",
    )
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == 0 and hero["is_alive"] is True
    assert "unconscious" in hero["conditions"] and hero.get("death_failures", 0) == 0
    params.pop("caster")
    params.pop("slot_resource")
    params.update(caster_state="mage")
    actual = _TravelDice([(10, 1)] * 6 + [(10, 2)] * 5)
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="mage",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["tsunami"] == 0
    state[f"{SRD}.combat"].update(encounter_active=True, round=1, current_actor="mage")
    _run("begin_combat_turn", state, actor="mage", participant_data=data)
    hero = state[f"{SRD}.entities"]["hero"]
    assert _point(state, "hero") == [55, 0, 0]
    assert hero["hp"] == 0 and hero["is_alive"] is True
    assert "unconscious" in hero["conditions"] and hero["death_failures"] == 2
    events = _source(state)["events"]
    assert [event["outcome"] for event in events] == ["failure", "failure"]
    assert actual.consumed == [(10, 1)] * 6 + [(10, 2)] * 5


def test_real_failed_then_successful_swim_and_actual_elevated_exit_fall(tsunami_scope, monkeypatch):
    state, data, params = _case()
    # This genuine initially supported body loses that support only after leaving the water.
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(b for b in scene["entities"] if b["id"] == "guard")["position"] = [5, 0, 20]
    scene["geometry"].append(
        {
            "id": "guard-platform",
            "map_id": "information",
            "volume": {"kind": "box", "min": [4.4, -1, 19], "max": [5.6, 1, 20]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": True,
            "visibility": "opaque",
            "metadata": {"plane": "material", "ground": True},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    actual = _TravelDice([(10, 1)] * 6 + [(20, 20), (20, 1), (20, 20)] + [(6, 2), (6, 3)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _complete(state, data, params)
    source_id = _source(state)["id"]
    before_point = _point(state, "guard")
    _run(
        "swim_srd_tsunami",
        state,
        actor_state="guard",
        source_id=source_id,
        destination=[-0.4, 0, 20],
    )
    assert _point(state, "guard") == before_point
    assert state[f"{SRD}.entities"]["guard"]["last_tsunami_swim"]["outcome"] == "failure"
    _run(
        "swim_srd_tsunami",
        state,
        actor_state="guard",
        source_id=source_id,
        destination=[-0.4, 0, 20],
    )
    assert _point(state, "guard") == [-0.4, 0, 20]
    assert state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == pytest.approx(10.8)
    assert state[f"{SRD}.state"]["has_tsunami_pending_exits"]
    opened = _run("settle_srd_tsunami_exits", state).result["context"]["_pending_action_id"]
    _run("complete_srd_pending_action", state, pending_action_id=opened)
    assert _point(state, "guard") == [-0.4, 0, 0]
    assert "prone" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 492


def test_actual_public_minute_cast_clock_expiry_and_grounded_exit_reopen_retry(
    tsunami_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", _case)
    actual = _TravelDice([(10, 2)] * 6 + [(20, 20), (20, 20)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "tsunami.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "tsunami", target_overrides={"hp": 500, "max_hp": 500}
        )
        base = ActionCommand(
            campaign_id="casting",
            action_id="begin",
            idempotency_key="begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(params),
        )

        def act(identity, name, selected):
            command = replace(
                base,
                action_id=identity,
                idempotency_key=identity,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=name,
                parameters=selected,
            )
            result = owner.execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            saved = owner.snapshot("casting")
            assert owner.execute_action(command) == result and owner.snapshot("casting") == saved
            return result, saved

        act("begin", "begin_srd_spell_cast", _begin(params))
        begun = owner.snapshot("casting")
        assert "tsunami_sources" not in begun.state[f"{SRD}.state"]
        act(
            "minute",
            "continue_srd_spell_cast",
            {
                "caster_state": "hero",
                "elapsed_rounds": 10,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        _, cast = act("cast", "cast_spell_outside_combat", params)
        assert cast.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
        assert cast.state[f"{SRD}.entities"]["guard"]["hp"] == 494
        assert _source(cast.state)["active"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == cast
        _, swum = act(
            "real-swim",
            "swim_srd_tsunami",
            {
                "actor_state": "guard",
                "source_id": _source(cast.state)["id"],
                "destination": [10, 0, 0],
            },
        )
        assert _point(swum.state, "guard") == [10, 0, 0]
        assert swum.state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == 10
        _, ended = act("expiry", "advance_srd_lasting_time", {"elapsed_rounds": 6})
        assert not _source(ended.state)["active"]
        assert ended.state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
        _, settled = act("grounded-exit", "settle_srd_tsunami_exits", {})
        assert not settled.state[f"{SRD}.state"]["has_tsunami_pending_exits"]
        assert _point(settled.state, "guard") == [10, 0, 0]
        assert actual.consumed == [(10, 2)] * 6 + [(20, 20), (20, 20)]


def test_source_swimming_cost_preserves_native_speed_difficult_terrain_and_one_pool(
    tsunami_scope, monkeypatch
):
    for native, difficult, cost in ((False, False, 10), (False, True, 15), (True, False, 5)):
        state, data, params = _case()
        guard = state[f"{SRD}.entities"]["guard"]
        guard["saving_throw_modifiers"]["strength"] = 5
        guard["skill_check_modifiers"]["athletics"] = 5
        if native:
            guard["swim_speed"] = 30
        if difficult:
            state[f"{SRD}.state"]["spatial_scene"]["zones"].append(
                {
                    "id": "actual-rough-ground",
                    "map_id": "information",
                    "shape": {"kind": "box", "min": [4, -2, -1], "max": [11, 2, 10]},
                    "metadata": {"plane": "material", "difficult": True},
                }
            )
        actual = _TravelDice([(10, 1)] * 6 + [(20, 15), (20, 15)])
        monkeypatch.setattr(dice, "_rng", actual)
        monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
        _complete(state, data, params)
        guard = state[f"{SRD}.entities"]["guard"]
        assert guard["last_tsunami_save"]["raw_total"] == 15
        assert guard["last_tsunami_save"]["modifier"] == 5
        assert guard["last_tsunami_save"]["total"] == 20
        combat_pool = not native and not difficult
        if combat_pool:
            state[f"{SRD}.combat"].update(encounter_active=True, round=1, current_actor="guard")
            _run("begin_combat_turn", state, actor="guard")
        _run(
            "swim_srd_tsunami",
            state,
            actor_state="guard",
            source_id=_source(state)["id"],
            destination=[10, 0, 0],
        )
        guard = state[f"{SRD}.entities"]["guard"]
        assert guard["last_tsunami_swim"]["raw_total"] == 15
        assert guard["last_tsunami_swim"]["modifier"] == 5
        assert guard["last_tsunami_swim"]["total"] == 20
        spent = (
            state[f"{SRD}.combat"]["movement_spent"]
            if combat_pool
            else guard["scene_movement_spent"]
        )
        assert spent == cost
        _run("end_srd_concentration_node", state, source_state="hero", target_state="hero")
        _run("settle_srd_tsunami_exits", state)
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="guard",
            destination=[15, 0, 0],
            mode="walk",
        )
        spent = (
            state[f"{SRD}.combat"]["movement_spent"]
            if combat_pool
            else state[f"{SRD}.entities"]["guard"]["scene_movement_spent"]
        )
        assert spent == cost + (6 if difficult else 5)


def test_genuine_character_save_and_omitted_swim_actor_use_participant_stats(
    tsunami_scope, monkeypatch
):
    state, data, params = _case()
    params["tsunami_origin"] = [0, 0, 0]
    # A level17 Druid with the admitted Athletics proficiency remains the actual
    # source caster and affected character; no NPC save/check projection is added.
    data["saving_throw_proficiencies"] = ["intelligence", "wisdom"]
    data["skill_proficiencies"] = ["athletics"]
    data["proficiency_bonus"] = 6
    hero = state[f"{SRD}.entities"]["hero"]
    hero.pop("saving_throw_modifiers", None)
    hero.pop("skill_check_modifiers", None)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(b for b in scene["entities"] if b["id"] == "guard")["position"] = [100, 0, 0]
    next(b for b in scene["entities"] if b["id"] == "orb")["position"] = [300, 80, 0]
    actual = _TravelDice([(10, 1)] * 6 + [(20, 15), (20, 20), (20, 10)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _complete(state, data, params)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["last_tsunami_save"]["raw_total"] == 15
    assert hero["last_tsunami_save"]["modifier"] == 3
    assert hero["last_tsunami_save"]["total"] == 18
    assert hero["hp"] == 497 and _source(state)["active"]
    _run(
        "swim_srd_tsunami",
        state,
        participant_data=data,
        actor_state="hero",
        source_id=_source(state)["id"],
        destination=[-15, 0, 0],
    )
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["last_tsunami_swim"]["raw_total"] == 10
    assert hero["last_tsunami_swim"]["modifier"] == 9
    assert hero["last_tsunami_swim"]["total"] == 19
    assert _point(state, "hero") == [-15, 0, 0]
    assert hero["scene_movement_spent"] == 10
    assert actual.consumed == [(10, 1)] * 6 + [(20, 15), (20, 20), (20, 10)]
