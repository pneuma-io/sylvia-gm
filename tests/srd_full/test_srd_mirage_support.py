"""Checked support transitions stay pure until the actual completed-action owner allocates."""

from copy import deepcopy
from functools import lru_cache

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_courier_body_movement import bind_runtime_consumers as bind_body
from scripts.srd_full.srd_feather_fall import extend_runtime as extend_fall
from scripts.srd_full.srd_mirage_support import extend_runtime
from scripts.srd_full.srd_reaction_spells import _allocate
from scripts.srd_full.srd_scene_relations import _tool_scene
from scripts.srd_full.srd_timed_actor_defenses import _call, _load_runtime, _parameter, _set
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_mirage_arcane as mirage
from tests import test_srd_mirage_geometry as geometry
from tests import test_srd_spell_result_cards as cards
from tests.test_spatial_engine import _geometry
from tests.srd_full.test_srd_action_budget import SRD, _run

_observations = {}


@lru_cache
def _runtime():
    _, content = geometry._runtime()
    text = extend_runtime(bind_body(extend_fall(content.decode())))
    # Existing allocator contract produces a genuine saved prior fall for the duplicate guard.
    tool = _tool_scene(
        "Existing fall allocation contract for the owning test only.",
        [
            _parameter(
                "target_states", "array", items={"type": "string"}, min_items=1, max_items=64
            ),
            _parameter("fall_cause"),
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
    text = _replace_tools(text, {"begin_srd_scene_fall_test_contract": tool})
    return _load_runtime(text), text.encode()


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
    from sylvia_gm.application import package_runtime

    original = package_runtime.execute_tool

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == "observe_srd_mirage_support_change" and result.success:
            # Root consumes these private aggregates before public spatial-context filtering.
            _observations.clear()
            _observations.update(deepcopy(result.context))
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", observed)
    runtime, _ = _runtime()
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])


def _scene(state):
    return state[f"{SRD}.state"]["spatial_scene"]


def _actor(state):
    return state[f"{SRD}.entities"]["hero"]


def _body(state):
    return next(row for row in _scene(state)["entities"] if row["id"] == "hero")


def _platform():
    return {
        "id": "mirage-platform",
        "position": [0, 0, 20],
        "extent": [10, 10, 10],
        "description": "Raised stone platform",
        "tactile_kind": "stone",
        "blocks_movement": True,
        "ground": True,
    }


def _case(*, paid=False):
    state, data, parameters = mirage._case()
    parameters["mirage_structures"] = [_platform()]
    if paid:
        from tests.srd_full.test_srd_extended_casting import _begin

        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
        _run(
            "continue_srd_spell_cast",
            state,
            participant_data=data,
            caster_state="hero",
            elapsed_rounds=100,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
        source = next(reversed(state[f"{SRD}.state"]["mirage_sources"]))
    else:
        source = mirage._cast(state, mirage_structures=[_platform()])
    _body(state)["position"] = [0, 0, 30]
    return state, source


def _capture(state, source):
    before = deepcopy(state)
    result = _run("capture_srd_mirage_support", state, mirage_id=source)
    assert state == before
    return deepcopy(result.result["context"]["_mirage_support_receipts"])


def _observe(state, receipts):
    before = deepcopy(state)
    result = _run("observe_srd_mirage_support_change", state, receipts=receipts)
    assert state == before
    assert result.result["status"] == "committed"
    return deepcopy(_observations)


def _remove(state, source):
    _run("retire_srd_mirage_source", state, mirage_id=source, reason="dispel")


def test_actual_paid_mirage_support_removal_returns_checked_event_without_allocating():
    state, source = _case(paid=True)
    receipts = _capture(state, source)
    assert len(receipts) == 1
    proof = receipts[0]
    assert proof["mirage_id"] == source and proof["body"]["actor_id"] == "hero"
    assert proof["geometry"]["id"] == "mirage-platform"
    assert proof["geometry"]["metadata"]["mirage_source_id"] == source
    assert proof["ground_height"] == 30 and proof["body"]["origin"] == [0, 0, 30]
    paid_slots = deepcopy(_actor(state)["resources"])
    _remove(state, source)
    result = _observe(state, receipts)
    event = result["_falling_events"][0]
    assert event["actor_id"] == "hero" and event["scene_id"] == "hero"
    assert event["distance"] == 30 and event["ground_id"] == "floor"
    assert event["ground_position"] == [0, 0, 0] and event["cause"] == "support_lost"
    assert _actor(state)["resources"] == paid_slots
    assert _actor(state).get("scene_fall") is None
    assert not state[f"{SRD}.state"].get("pending_srd_actions")
    assert _observe(state, receipts)["_falling_events"] == result["_falling_events"]


def test_unchanged_and_independent_same_height_support_do_not_start_falls():
    state, source = _case()
    receipts = _capture(state, source)
    assert _observe(state, receipts)["_mirage_support_results"][0]["status"] == "supported"
    alternate = _geometry("independent-floor", [-5, -5, 25], [5, 5, 30])
    alternate["map_id"] = _scene(state)["map"]["id"]
    alternate["metadata"].update(plane="material", ground=True)
    _scene(state)["geometry"].append(alternate)
    _remove(state, source)
    result = _observe(state, receipts)
    assert not result["_falling_events"]
    assert result["_mirage_support_results"][0]["after"]["geometry"]["id"] == "independent-floor"


def test_projection_overlap_retirement_exposes_real_remaining_support_then_actual_ground():
    state, _, _ = geometry._case()
    hill = geometry._hill(state)
    hill["metadata"]["ground"] = True
    hill["volume"]["max"][2] = 2
    old = mirage._cast(
        state, mirage_terrain_changes=[geometry._spec(movement="blocking", ground=True)]
    )
    new = mirage._cast(
        state, mirage_terrain_changes=[geometry._spec(movement="blocking", ground=True)]
    )
    _body(state)["position"] = [10, 0, 8]
    receipts = _capture(state, new)
    assert len(receipts) == 1
    assert _capture(state, old) == []
    _remove(state, new)
    assert _observe(state, receipts)["_mirage_support_results"][0]["status"] == "supported"
    old_receipts = _capture(state, old)
    _remove(state, old)
    result = _observe(state, old_receipts)
    assert result["_falling_events"][0]["ground_id"] == "hill"
    assert result["_falling_events"][0]["distance"] == 6


@pytest.mark.parametrize("change", ["move", "plane", "body"])
def test_intervening_actual_body_change_does_not_reuse_old_support_receipt(change):
    state, source = _case()
    receipts = _capture(state, source)
    _remove(state, source)
    if change == "move":
        _body(state)["position"][0] += 1
    elif change == "plane":
        _body(state)["metadata"]["plane"] = "ethereal"
    else:
        _body(state)["height"] += 1
    result = _observe(state, receipts)
    assert result["_mirage_support_results"][0]["status"] == "body_changed"
    assert not result["_falling_events"]


def test_absent_landing_ground_is_explicit_and_map_bounds_are_never_ground():
    state, source = _case()
    receipts = _capture(state, source)
    _remove(state, source)
    _scene(state)["geometry"] = []
    result = _observe(state, receipts)
    assert result["_mirage_support_results"][0]["status"] == "missing_landing_ground"
    assert result["_mirage_support_results"][0]["after"]["geometry"] is None
    assert not result["_falling_events"]


def test_existing_saved_scene_descent_is_left_to_its_exact_owner():
    state, source = _case()
    receipts = _capture(state, source)
    _remove(state, source)
    _run(
        "begin_srd_scene_fall_test_contract",
        state,
        target_states=["hero"],
        fall_cause="support_lost",
    )
    result = _observe(state, receipts)
    assert result["_mirage_support_results"][0]["status"] == "existing_scene_descent"
    assert not result["_falling_events"]


@pytest.mark.parametrize("move_first", [False, True])
def test_paid_fly_needs_actual_aloft_movement_not_only_a_granted_speed(move_first):
    from tests.srd_full.test_srd_spell_illusions import _grant_other_spell

    state, source = _case()
    _, data, _ = mirage._case()
    parameters = _grant_other_spell(state, data, "fly")
    parameters.update(target="hero", targets=["hero"])
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    if move_first:
        state[f"{SRD}.combat"].update(
            encounter_active=True,
            turn_active=True,
            active_actor="hero",
            current_actor="hero",
            movement_remaining=60,
            movement_spent=0,
        )
        _run(
            "move_srd_scene_creature", state, actor_state="hero", mode="fly", destination=[1, 0, 30]
        )
        assert _actor(state)["flying_aloft"] is True
    receipts = _capture(state, source)
    _remove(state, source)
    result = _observe(state, receipts)
    status = result["_mirage_support_results"][0]["status"]
    assert status == ("flight_supported" if move_first else "falling")
    assert len(result["_falling_events"]) == (0 if move_first else 1)
    qualifier = result["_mirage_support_results"][0]["qualifiers"]
    assert qualifier["magic_flight_sources"][0]["spell"] == "fly"


@pytest.mark.parametrize("valid_serial", [True, False])
def test_actual_ended_wind_receipt_qualifies_exact_source_target_serial_and_window(valid_serial):
    state, source = _case()
    _run(
        "install_srd_transformation_source",
        state,
        target_state="hero",
        caster_state="hero",
        lasting_state="state",
        spell="wind_walk",
        mode="cloud",
        actual_spell_level=6,
    )
    body_source = _actor(state)["form_current_source_id"]
    _run("end_srd_transformation_source", state, target_state="hero", source_id=body_source)
    receipt = _actor(state)["wind_walk_descent"]
    assert (
        receipt["source_id"] == body_source and receipt["end_tick"] == receipt["started_tick"] + 20
    )
    if not valid_serial:
        receipt["source_serial"] += 1
    receipts = _capture(state, source)
    _remove(state, source)
    result = _observe(state, receipts)
    status = result["_mirage_support_results"][0]["status"]
    assert status == ("existing_wind_descent" if valid_serial else "falling")
    assert len(result["_falling_events"]) == (0 if valid_serial else 1)


def test_circle_body_receipt_and_physically_blocked_descent_are_source_qualified():
    state, source = _case()
    _body(state)["footprint"] = {"kind": "circle", "radius": 1}
    receipts = _capture(state, source)
    _remove(state, source)
    # Centerline reaches floor, but the actual body intersects the independent side wall.
    wall = _geometry("narrow-landing", [0.5, -5, 0], [2, 5, 20])
    wall["map_id"] = _scene(state)["map"]["id"]
    wall["metadata"]["plane"] = "material"
    _scene(state)["geometry"].append(wall)
    result = _observe(state, receipts)
    assert result["_mirage_support_results"][0]["status"] == "unsupported_descent"
    assert result["_mirage_support_results"][0]["clearance"]["clear"] is False
    assert not result["_falling_events"]


def test_actual_magic_flight_source_requires_its_saved_concentration_cast_identity():
    from tests.srd_full.test_srd_spell_illusions import _grant_other_spell

    state, _ = _case()
    _, data, _ = mirage._case()
    parameters = _grant_other_spell(state, data, "fly")
    parameters.update(target="hero", targets=["hero"])
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source = next(row for row in state[f"{SRD}.state"]["turn_effects"] if row["spell"] == "fly")
    assert source["cast_id"]
    before = deepcopy(state)
    for cast_id, expected in [(source["cast_id"], True), ("older-cast", False)]:
        result = _run(
            "qualify_srd_mirage_magic_flight_source",
            state,
            caster_state="hero",
            spell="fly",
            cast_id=cast_id,
        )
        assert result.result["context"]["_support_flight_record_active"] is expected
        assert state == before


def test_source_identity_and_duplicate_receipts_reject_atomically():
    state, source = _case()
    receipts = _capture(state, source)
    _remove(state, source)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="twice"):
        _observe(state, receipts + receipts)
    assert state == before
    state[f"{SRD}.state"]["mirage_sources"][source]["caster"] = "replacement"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="replaced"):
        _observe(state, receipts)
    assert state == before


def test_emitter_repeat_and_no_persistence_or_allocator_hooks():
    runtime, text = _runtime()
    assert _load_runtime(extend_runtime(text.decode())) == runtime
    for name, tool in runtime["tools"].items():
        if name.startswith(
            (
                "capture_srd_mirage_support",
                "observe_srd_mirage_support",
                "query_srd_mirage_body_support",
                "qualify_srd_mirage_support_flight",
            )
        ):
            assert "pending_srd_actions" not in str(tool["effects"])
            assert "spatial_mutation" not in str(tool["effects"])


def test_empty_and_inactive_source_paths_are_pure_and_cheap():
    state, source = _case()
    _remove(state, source)
    assert _capture(state, source) == []
    assert _observe(state, [])["_falling_events"] == []
