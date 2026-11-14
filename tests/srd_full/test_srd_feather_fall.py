"""Scene-fall qualification and real shared-clock descent use actual saved source records."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_feather_fall import PROFILES, bind_runtime_consumers, extend_runtime
from scripts.srd_full.srd_reaction_spells import _allocate
from scripts.srd_full.srd_scene_relations import _tool_scene
from scripts.srd_full.srd_timed_actor_defenses import _call, _load_runtime, _parameter, _RuntimeDumper, _set

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.test_spatial_engine import _geometry
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_scene_relations import _state


@lru_cache
def _runtime():
    source = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    text = bind_runtime_consumers(extend_runtime(source.read_text(encoding="utf-8")))
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(PROFILES)
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "acid_arrow" in parameter.get("enum_values", []):
                parameter["enum_values"] = sorted(runtime["definitions"]["spells"])
    # This exercises Root's exact reserved allocator contract; final public binding is Root-owned.
    runtime["tools"]["begin_srd_scene_fall_test_contract"] = _tool_scene(
        "Open the real existing saved action stack after owning fall qualification.",
        [
            _parameter(
                "target_states", "array", items={"type": "string"}, min_items=1, max_items=64
            ),
            _parameter(
                "fall_cause", "enum", enum_values=["departure", "support_lost", "flight_ended"]
            ),
        ],
        [
            _call(
                "qualify_srd_scene_fall",
                {"target_states": "{target_states}", "fall_cause": "{fall_cause}"},
                ["_falling_events", "_falling_targets", "_fall_first_actor"],
            ),
            *_allocate("scene_fall", "_fall_first_actor", "_fall_first_actor"),
            _set(
                "state.pending_srd_actions.{_pending_action_id}.parameters.falling_events",
                "_falling_events",
            ),
            _call("bind_srd_scene_fall_event", {"pending_action_id": "{_pending_action_id}"}),
        ],
    )
    return runtime, yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, content = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=content,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _authored(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])


def _case(height=120, ground=0, targets=("guard",)):
    from tests.srd_full.test_srd_spell_result_cards import _spell_case

    state, data, parameters = _spell_case("feather_fall")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
    scene["map"]["bounds"]["max"][2] = 1100
    scene["map"]["layers"][0]["max_elevation"] = 1100
    for i, identity in enumerate(targets):
        state[f"{SRD}.entities"][identity] = deepcopy(state[f"{SRD}.entities"]["guard"])
        state[f"{SRD}.entities"][identity].update(hp=1000, max_hp=1000, temp_hp=0, name=identity)
        body = deepcopy(scene["entities"][1])
        body.update(id=identity, position=[5 + i * 3, 0, height])
        body["metadata"]["participant_ref"] = identity
        if i == 0:
            scene["entities"][1] = body
        else:
            scene["entities"].append(body)
    scene["entities"][0]["position"] = [0, 0, height]
    floor = _geometry("fall-ground", [-100, -100, ground - 10], [100, 100, ground])
    floor["map_id"] = scene["map"]["id"]
    floor["metadata"].update(ground=True, plane="material")
    scene["geometry"] = [floor]
    state[f"{SRD}.state"] = {"lasting_tick": 0, "turn_effects": [], "spatial_scene": scene}
    return state, data, parameters


def _actor(state, identity="guard"):
    return state[f"{SRD}.entities"][identity]


def _height(state, identity="guard"):
    return next(
        body["position"][2]
        for body in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if body["id"] == identity
    )


def _begin(state, targets=("guard",), cause="departure"):
    result = _run(
        "begin_srd_scene_fall_test_contract", state, target_states=list(targets), fall_cause=cause
    )
    return result.result["context"]["_pending_action_id"]


def _install(state, trigger, targets=("guard",)):
    return _run(
        "install_srd_feather_fall",
        state,
        caster_state="hero",
        reaction_trigger_id=trigger,
        targets=list(targets),
        actual_spell_level=1,
        spell_cast_id="actual-owner-test-cast",
    )


def _complete(state, trigger):
    result = _run("resolve_srd_scene_fall_event", state, pending_action_id=trigger)
    _run("complete_srd_pending_action", state, pending_action_id=trigger)
    return result


def test_fall_intent_qualifies_actual_airborne_position_and_elevated_landing_geometry():
    state, _, _ = _case(height=180, ground=30)
    trigger = _begin(state)
    saved = state[f"{SRD}.state"]["pending_srd_actions"][trigger]
    row = saved["parameters"]["falling_events"][0]
    assert row["origin"] == [5, 0, 180] and row["ground_position"] == [5, 0, 30]
    assert row["distance"] == 150 and row["ground_id"] == "fall-ground"
    assert _actor(state)["scene_fall"]["event_id"] == trigger
    assert _height(state) == 180 and _actor(state)["hp"] == 1000


@pytest.mark.parametrize(
    "problem", ["absent", "other_plane", "blocked", "grounded", "flight", "duplicate"]
)
def test_unsupported_or_ineligible_fall_rejects_without_saved_event_or_damage(problem):
    state, _, _ = _case(height=120)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    cause, targets = "departure", ["guard"]
    if problem == "absent":
        scene["geometry"] = []
    elif problem == "other_plane":
        scene["geometry"][0]["metadata"]["plane"] = "ethereal"
    elif problem == "blocked":
        wall = _geometry("blocking-shelf", [-100, -100, 70], [100, 100, 80])
        wall["map_id"] = scene["map"]["id"]
        wall["metadata"]["plane"] = "material"
        scene["geometry"].append(wall)
    elif problem == "grounded":
        scene["entities"][1]["position"][2] = 0
    elif problem == "flight":
        cause = "flight_ended"
    else:
        targets = ["guard", "guard"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _begin(state, targets, cause)
    assert state == before


def test_real_feather_source_descends_each_round_and_lands_without_damage_or_new_prone():
    state, _, _ = _case()
    trigger = _begin(state)
    _install(state, trigger)
    _complete(state, trigger)
    assert _height(state) == 120 and _actor(state)["scene_fall"]["status"] == "descending"
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 60 and _actor(state)["hp"] == 1000
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 0 and _actor(state)["hp"] == 1000
    assert "prone" not in _actor(state)["conditions"]
    assert _actor(state)["scene_fall"]["ended_reason"] == "feather_landing"
    assert not state[f"{SRD}.state"]["has_scene_falls"]
    assert all(
        not row["active"]
        for row in state[f"{SRD}.state"]["turn_effects"]
        if row["kind"] == "feather_fall"
    )


def test_unprotected_simultaneous_actor_takes_only_actual_remaining_fall_damage(monkeypatch):
    state, _, _ = _case(height=90, targets=("guard", "other"))
    trigger = _begin(state, ["guard", "other"])
    _install(state, trigger, ["guard"])
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    rng = Random(0)
    amount = sum(rng.randint(1, 6) for _ in range(9))
    _complete(state, trigger)
    assert _height(state, "other") == 0 and _actor(state, "other")["hp"] == 1000 - amount
    assert _height(state) == 90 and _actor(state)["hp"] == 1000
    _run("advance_srd_lasting_time", state, elapsed_rounds=2)
    assert _height(state) == 0 and _actor(state)["hp"] == 1000


def test_expired_source_falls_only_the_remaining_real_height_once(monkeypatch):
    state, _, _ = _case(height=720)
    trigger = _begin(state)
    _install(state, trigger)
    _complete(state, trigger)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    rng = Random(0)
    expected = sum(rng.randint(1, 6) for _ in range(12))
    _run("advance_srd_lasting_time", state, elapsed_rounds=9)
    assert _height(state) == 180 and _actor(state)["hp"] == 1000
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 0 and _actor(state)["hp"] == 1000 - expected
    assert _actor(state)["scene_fall"]["ended_reason"] == "feather_expired"
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _actor(state)["hp"] == 1000 - expected


def test_trigger_requires_actual_sight_but_other_chosen_fallers_do_not():
    state, _, _ = _case(targets=("guard", "other"))
    state[f"{SRD}.state"]["spatial_scene"]["entities"][2]["metadata"]["invisible"] = True
    trigger = _begin(state, ["guard", "other"])
    _install(state, trigger, ["guard", "other"])
    assert len(state[f"{SRD}.state"]["turn_effects"]) == 2
    state2, _, _ = _case()
    _actor(state2, "hero")["conditions"] = ["blinded"]
    trigger2 = _begin(state2)
    before = deepcopy(state2)
    with pytest.raises(RulesetPreconditionRejected, match="See a falling"):
        _install(state2, trigger2)
    assert state2 == before


def test_emitter_repeat_keeps_one_clock_owner_and_inactive_path_is_cheap():
    source = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    once = extend_runtime(source.read_text(encoding="utf-8"))
    twice = extend_runtime(once)
    assert _load_runtime(once) == _load_runtime(twice)
    state, _, _ = _case()
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 120


def test_self_trigger_does_not_require_sight_and_five_real_targets_are_supported():
    names = ("hero", "guard", "faller-2", "faller-3", "faller-4")
    state, _, _ = _case(targets=names[1:])
    _actor(state, "hero")["conditions"] = ["blinded"]
    trigger = _begin(state, names)
    _install(state, trigger, names)
    _complete(state, trigger)
    _run("advance_srd_lasting_time", state, elapsed_rounds=2)
    assert all(_height(state, identity) == 0 for identity in names)
    assert all(
        _actor(state, identity)["scene_fall"]["ended_reason"] == "feather_landing"
        for identity in names
    )


def test_each_target_lands_and_retires_independently():
    state, _, _ = _case(targets=("guard", "other"))
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"][2] = 90
    scene["entities"][2]["position"][2] = 150
    trigger = _begin(state, ["guard", "other"])
    _install(state, trigger, ["guard", "other"])
    _complete(state, trigger)
    _run("advance_srd_lasting_time", state, elapsed_rounds=2)
    assert _height(state) == 0 and _height(state, "other") == 30
    records = state[f"{SRD}.state"]["turn_effects"]
    assert not next(row["active"] for row in records if row["target"] == "guard")
    assert next(row["active"] for row in records if row["target"] == "other")
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state, "other") == 0


def test_overlapping_real_sources_continue_through_only_the_older_expiry():
    state, _, _ = _case(height=660, targets=("guard", "other"))
    trigger = _begin(state)
    _install(state, trigger)
    _complete(state, trigger)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    for identity, x in [("hero", 0), ("other", 8)]:
        _run(
            "move_srd_travel_subject",
            state,
            subject=identity,
            destination=[x, 0, 600],
            plane="material",
            teleport=True,
        )
    next_trigger = _begin(state, ["other"])
    _install(state, next_trigger, ["guard"])
    _complete(state, next_trigger)
    _run("advance_srd_lasting_time", state, elapsed_rounds=9)
    assert _height(state) == 60 and _actor(state)["hp"] == 1000
    assert _actor(state)["scene_fall"]["status"] == "descending"
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 0 and _actor(state)["hp"] == 1000


@pytest.mark.parametrize("wind_first", [True, False])
def test_wind_and_feather_share_one_descent_and_keep_separate_windows(wind_first, monkeypatch):
    state, _, _ = _case(height=720)

    def wind():
        _run(
            "install_srd_transformation_source",
            state,
            target_state="guard",
            caster_state="hero",
            lasting_state="state",
            spell="wind_walk",
            mode="cloud",
            actual_spell_level=6,
        )
        source_id = _actor(state)["form_current_source_id"]
        _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)

    def feather():
        trigger = _begin(state)
        _install(state, trigger)
        _complete(state, trigger)

    (wind if wind_first else feather)()
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 660
    _run(
        "move_srd_travel_subject",
        state,
        subject="hero",
        destination=[0, 0, 660],
        plane="material",
        teleport=True,
    )
    (feather if wind_first else wind)()
    receipt = deepcopy(_actor(state)["wind_walk_descent"])
    _run("advance_srd_lasting_time", state, elapsed_rounds=9)
    assert _height(state) == 120 and _actor(state)["hp"] == 1000
    wind_receipt = _actor(state)["wind_walk_descent"]
    assert wind_receipt["end_tick"] == receipt["end_tick"]
    assert wind_receipt["settled_tick"] == min(20, receipt["end_tick"])
    assert wind_receipt["status"] == ("expired" if wind_first else "pending")
    assert (
        any(
            row["kind"] == "feather_fall" and row["active"]
            for row in state[f"{SRD}.state"]["turn_effects"]
        )
        is wind_first
    )
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    rng = Random(0)
    damage = sum(rng.randint(1, 6) for _ in range(6))
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 0 and _actor(state)["hp"] == 1000 - damage
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _actor(state)["hp"] == 1000 - damage


def test_exact_instance_retirement_preserves_overlap_then_falls_when_last_source_ends():
    state, _, _ = _case(height=720)
    trigger = _begin(state)
    _install(state, trigger)
    _install(state, trigger)
    _complete(state, trigger)
    _run(
        "retire_srd_feather_fall_instance",
        state,
        target_state="guard",
        caster_state="hero",
        instance=1,
        reason="dispel",
    )
    rows = state[f"{SRD}.state"]["turn_effects"]
    assert [row["active"] for row in rows] == [False, True]
    assert _height(state) == 720 and _actor(state)["hp"] == 1000
    _run(
        "retire_srd_feather_fall_instance",
        state,
        target_state="guard",
        caster_state="hero",
        instance=2,
        reason="dispel",
    )
    assert _height(state) == 0 and _actor(state)["hp"] < 1000


def test_actual_relocation_onto_ground_ends_only_that_targets_feather_sources():
    state, _, _ = _case(targets=("guard", "other"))
    trigger = _begin(state, ["guard", "other"])
    _install(state, trigger, ["guard", "other"])
    _complete(state, trigger)
    _run(
        "move_srd_travel_subject",
        state,
        subject="guard",
        destination=[5, 0, 0],
        plane="material",
        teleport=True,
    )
    assert _actor(state)["scene_fall"]["status"] == "landed"
    assert _actor(state)["hp"] == 1000
    assert _actor(state, "other")["scene_fall"]["status"] == "descending"
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state, "other") == 60


def test_actual_landing_settles_exact_wind_receipt_and_no_later_owner_moves_again():
    state, _, _ = _case(height=60)
    _run(
        "install_srd_transformation_source",
        state,
        target_state="guard",
        caster_state="hero",
        lasting_state="state",
        spell="wind_walk",
        mode="cloud",
        actual_spell_level=6,
    )
    source_id = _actor(state)["form_current_source_id"]
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    trigger = _begin(state)
    _install(state, trigger)
    _complete(state, trigger)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 0 and _actor(state)["hp"] == 1000
    receipt = deepcopy(_actor(state)["wind_walk_descent"])
    assert receipt["status"] == "landed" and receipt["settled_tick"] == 2
    assert _actor(state)["scene_fall"]["status"] == "landed"
    _run("advance_srd_lasting_time", state, elapsed_rounds=12)
    assert _height(state) == 0 and _actor(state)["hp"] == 1000
    assert _actor(state)["wind_walk_descent"] == receipt


def test_final_binder_repeats_without_duplicate_wind_guard_or_relocation_observer():
    source = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    once = bind_runtime_consumers(extend_runtime(source.read_text(encoding="utf-8")))
    twice = bind_runtime_consumers(extend_runtime(once))
    assert _load_runtime(once) == _load_runtime(twice)


def _paid_fly(state):
    from tests.srd_full.test_srd_spell_result_cards import _spell_case

    supplies, _, _ = _spell_case("fly")
    materials = deepcopy(supplies[f"{SRD}.entities"]["hero"]["spell_materials"])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    body = deepcopy(scene["entities"][0])
    body["id"] = "mage"
    body["metadata"]["participant_ref"] = "mage"
    scene["entities"].append(body)
    _run(
        "prepare_srd_creature",
        state,
        creature_id="mage",
        name="Flight Mage",
        source="Synthetic NPC: Wisdom spellcasting +3, DC15, Fly twice per day.",
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
        spells=[{"spell": "fly", "uses_per_day": 2}],
        inventory=[item["inventory_item"] for item in materials],
        spell_materials=materials,
    )
    _run(
        "cast_spell_outside_combat",
        state,
        spell="fly",
        caster_state="mage",
        targets=["guard"],
        caster_can_touch_all_targets=True,
        all_targets_willing=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        material_item_ids=[item["id"] for item in materials],
    )
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["fly"] == 1


@pytest.mark.parametrize("finish", ["landing", "expiry"])
def test_paid_fly_actual_budgeted_movement_arrests_fall_and_keeps_feather_until_finish(finish):
    state, _, _ = _case(height=30)
    trigger = _begin(state)
    _install(state, trigger)
    _complete(state, trigger)
    _paid_fly(state)
    _run(
        "move_srd_scene_creature",
        state,
        actor_state="guard",
        destination=[15, 0, 30],
        mode="fly",
        difficult_terrain=False,
    )
    target = _actor(state)
    assert target["scene_fall"]["status"] == "arrested"
    assert target["scene_movement_spent"] == 10
    assert target["flying_aloft"] is True and target["spell_flight_fall_pending"] is False
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 30 and _actor(state)["hp"] == 1000
    assert any(
        row["active"]
        for row in state[f"{SRD}.state"]["turn_effects"]
        if row["kind"] == "feather_fall"
    )
    if finish == "landing":
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="guard",
            destination=[15, 0, 0],
            mode="fly",
            difficult_terrain=False,
        )
        assert _height(state) == 0 and _actor(state)["scene_fall"]["status"] == "landed"
    else:
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
        assert _height(state) == 30 and _actor(state)["scene_fall"]["status"] == "arrested"
    assert _actor(state)["hp"] == 1000
    assert not any(
        row["active"]
        for row in state[f"{SRD}.state"]["turn_effects"]
        if row["kind"] == "feather_fall"
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _actor(state)["hp"] == 1000


def test_declared_fly_mode_without_actual_flight_cannot_arrest_saved_fall():
    state, _, _ = _case(height=30)
    trigger = _begin(state)
    _install(state, trigger)
    _complete(state, trigger)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="guard",
            destination=[15, 0, 30],
            mode="fly",
            difficult_terrain=False,
        )
    assert state == before and _actor(state)["scene_fall"]["status"] == "descending"


def test_actual_flying_arrests_only_matching_wind_receipt_then_landing_settles_it():
    state, _, _ = _case(height=30, targets=("guard", "other"))
    sources = {}
    for identity in ("guard", "other"):
        _run(
            "install_srd_transformation_source",
            state,
            target_state=identity,
            caster_state="hero",
            lasting_state="state",
            spell="wind_walk",
            mode="cloud",
            actual_spell_level=6,
        )
        sources[identity] = _actor(state, identity)["form_current_source_id"]
    assert sources["guard"] != sources["other"]
    for identity, source_id in sources.items():
        _run("end_srd_transformation_source", state, target_state=identity, source_id=source_id)
    unrelated = deepcopy(_actor(state, "other")["wind_walk_descent"])
    trigger = _begin(state)
    _install(state, trigger)
    _complete(state, trigger)
    _paid_fly(state)
    _run(
        "move_srd_scene_creature",
        state,
        actor_state="guard",
        destination=[15, 0, 30],
        mode="fly",
        difficult_terrain=False,
    )
    assert _actor(state)["wind_walk_descent"]["status"] == "arrested"
    assert _actor(state, "other")["wind_walk_descent"] == unrelated
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 30 and _actor(state)["hp"] == 1000
    assert _height(state, "other") == 0 and _actor(state, "other")["hp"] == 1000
    _run(
        "move_srd_scene_creature",
        state,
        actor_state="guard",
        destination=[15, 0, 0],
        mode="fly",
        difficult_terrain=False,
    )
    assert _actor(state)["wind_walk_descent"]["status"] == "landed"
    assert _height(state) == 0 and _actor(state)["hp"] == 1000
    _run("advance_srd_lasting_time", state, elapsed_rounds=12)
    assert _height(state) == 0 and _actor(state)["hp"] == 1000
    assert not any(
        row["active"]
        for row in state[f"{SRD}.state"]["turn_effects"]
        if row["kind"] == "feather_fall"
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _actor(state)["hp"] == 1000
