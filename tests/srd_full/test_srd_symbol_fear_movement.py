"""Symbol Fear derives live glyph geometry and credits actual public walking segments."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_triggers import _tools

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_detection_masks as masks
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_triggers as owner
from tests import test_srd_symbol_communication as communication
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act

FEAR_TOOLS = (
    "qualify_srd_symbol_fear_movement",
    "check_srd_symbol_fear_segment",
    "record_srd_symbol_fear_segment",
    "move_srd_scene_creature_environment_segment",
)


@lru_cache
def _runtime():
    runtime = deepcopy(communication._runtime()[0])
    changed = _tools(runtime)
    runtime["tools"].update({name: changed[name] for name in FEAR_TOOLS})
    return runtime, yaml.safe_dump(runtime, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, payload = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=payload,
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
def _actual_owner(monkeypatch):
    monkeypatch.setattr(owner, "_runtime", _runtime)
    monkeypatch.setattr(owner, "_mechanic", _mechanic)
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards,
        "_spell_definitions",
        lambda: _runtime()[0]["definitions"]["spells"],
    )


def _case(spell="symbol"):
    state, data, params = owner._case(spell, symbol_effect="fear")
    for actor in ("hero", "guard", "other"):
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["wisdom"] = (
            -30 if actor == "hero" else 30
        )
    return state, data, params


def _activated(*, with_data=False):
    state, data, params = _case()
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
    cast = _run("cast_spell", state, participant_data=data, **params)
    owner._event(state, cast.result["context"]["_trigger_id"], event="open")
    assert "frightened" in state[f"{SRD}.entities"]["hero"]["conditions"]
    return (state, data) if with_data else state


def _qualify(state):
    return _run("qualify_srd_symbol_fear_movement", state, actor_state="hero").result["context"]


def _move(state, destination):
    return _run("move_srd_scene_creature", state, actor_state="hero", destination=destination)


def test_actual_paid_fear_walks_thirty_feet_in_split_segments_and_survives_reopen():
    state = _activated()
    before = deepcopy(state)
    source = _qualify(state)["_symbol_fear_movement_sources"][0]
    assert source["glyph_origin"] == [5, 0, 0]
    assert source["glyph_plane"] == "material"
    assert source["moved_feet"] == 0
    assert state == before
    first = _move(state, [-20, 0, 0])
    assert first.result["context"]["_scene_movement_distance"] == 20
    assert _qualify(state)["_symbol_fear_movement_sources"][0]["moved_feet"] == 20
    state = json.loads(json.dumps(state))
    second = _move(state, [-30, 0, 0])
    assert second.result["context"]["_scene_movement_distance"] == 10
    assert _qualify(state)["_symbol_fear_movement_sources"][0]["moved_feet"] == 30
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 30
    assert state[f"{SRD}.entities"]["hero"]["symbol_fear_walk_pending"] is None
    saved = deepcopy(state)
    assert (
        _run("record_srd_symbol_fear_segment", state, actor_state="hero").result["context"][
            "_symbol_fear_segment_credited"
        ]
        is False
    )
    assert state == saved
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _qualify(state)["_symbol_fear_movement_sources"][0]["moved_feet"] == 0


@pytest.mark.parametrize("destination", [[4, 0, 0], [20, 0, 0]])
def test_actual_walk_rejects_a_closer_prefix_even_when_endpoint_is_farther(destination):
    state = _activated()
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="closer"):
        _move(state, destination)
    assert state == saved


def test_real_wall_rejects_one_route_without_fake_inability_and_alternative_route_commits():
    state = _activated()
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [
        {
            "id": "wall",
            "map_id": "information",
            "volume": {"kind": "box", "min": [-12, -1, -5], "max": [-8, 1, 10]},
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": {"plane": "material", "material": "stone"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    ]
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="barrier"):
        _move(state, [-20, 0, 0])
    assert state == saved
    assert _qualify(state)["_symbol_fear_movement_sources"][0]["moved_feet"] == 0
    _move(state, [-10, 10, 0])
    assert _qualify(state)["_symbol_fear_movement_sources"][0]["moved_feet"] == pytest.approx(
        200**0.5
    )


def test_difficult_terrain_spends_real_cost_but_credits_only_actual_distance():
    state = _activated()
    _run(
        "move_srd_scene_creature",
        state,
        actor_state="hero",
        destination=[-15, 0, 0],
        difficult_terrain=True,
    )
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 30
    assert _qualify(state)["_symbol_fear_movement_sources"][0]["moved_feet"] == 15
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="movement"):
        _move(state, [-16, 0, 0])
    assert state == saved


@pytest.mark.parametrize("failure", ["removed", "holder_removed", "plane", "missing_plane"])
def test_unresolved_current_glyph_geometry_retains_independent_fear_and_never_fakes_inability(
    failure,
):
    state = _activated()
    orb = owner._entry(state, "orb")
    if failure == "removed":
        orb["active"] = False
    elif failure == "holder_removed":
        orb["metadata"]["carried_by"] = "missing"
    elif failure == "plane":
        orb["metadata"]["plane"] = "ethereal"
    else:
        del orb["metadata"]["plane"]
    saved = deepcopy(state)
    result = _qualify(state)
    assert result["_symbol_fear_movement_active"] is True
    assert result["_symbol_fear_movement_unresolved"] is True
    assert state == saved
    with pytest.raises(RulesetPreconditionRejected, match="geometry"):
        _move(state, [-10, 0, 0])
    assert state == saved
    assert "frightened" in state[f"{SRD}.entities"]["hero"]["conditions"]


def test_current_carrier_geometry_controls_route_and_self_carried_glyph_earns_no_retreat():
    state = _activated()
    orb = owner._entry(state, "orb")
    orb["metadata"]["carried_by"] = "guard"
    owner._entry(state, "guard")["position"] = [-5, 0, 0]
    saved = deepcopy(state)
    assert _qualify(state)["_symbol_fear_movement_sources"][0]["glyph_origin"] == [-5, 0, 0]
    with pytest.raises(RulesetPreconditionRejected, match="closer"):
        _move(state, [-10, 0, 0])
    assert state == saved
    orb["metadata"]["carried_by"] = "hero"
    result = _qualify(state)
    assert result["_symbol_fear_movement_sources"][0]["follows_actor"] is True
    _move(state, [-10, 0, 0])
    assert _qualify(state)["_symbol_fear_movement_sources"][0]["moved_feet"] == 0


def test_real_combat_turn_uses_actual_budget_and_distinct_source_progress_after_new_turn():
    state = _activated()
    _move(state, [-10, 0, 0])
    clock_turn = _qualify(state)["_symbol_fear_movement_turn"]
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=False,
        current_actor="hero",
        turn_sequence=clock_turn - 1,
    )
    _run("begin_combat_turn", state, actor="hero")
    qualified = _qualify(state)
    assert qualified["_symbol_fear_movement_turn_mode"] == "combat"
    assert qualified["_symbol_fear_movement_turn"] == clock_turn
    assert qualified["_symbol_fear_movement_sources"][0]["moved_feet"] == 0
    _move(state, [-40, 0, 0])
    assert state[f"{SRD}.combat"]["movement_remaining"] == 0
    assert state[f"{SRD}.combat"]["movement_spent"] == 30
    assert _qualify(state)["_symbol_fear_movement_sources"][0]["moved_feet"] == 30
    _run("end_combat_turn", state, actor="hero")
    _run("begin_combat_turn", state, actor="hero")
    sources = _qualify(state)["_symbol_fear_movement_sources"]
    assert sources and all(source["moved_feet"] == 0 for source in sources)
    _move(state, [-50, 0, 0])
    assert all(
        source["moved_feet"] == 10 for source in _qualify(state)["_symbol_fear_movement_sources"]
    )


def test_real_paid_misty_step_does_not_count_as_thirty_feet_of_symbol_retreat():
    state, data = _activated(with_data=True)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    before = state[f"{SRD}.entities"]["hero"].get("scene_movement_spent", 0)
    masks._later_information_cast(state, data, "misty_step", travel_destination=[-30, 0, 0])
    assert owner._entry(state, "hero")["position"] == [-30, 0, 0]
    assert state[f"{SRD}.entities"]["hero"].get("scene_movement_spent", 0) == before
    assert _qualify(state)["_symbol_fear_movement_sources"][0]["moved_feet"] == 0
    saved = deepcopy(state)
    assert (
        _run("record_srd_symbol_fear_segment", state, actor_state="hero").result["context"][
            "_symbol_fear_segment_credited"
        ]
        is False
    )
    assert state == saved


def test_expired_fear_preserves_unowned_frightened_without_a_glyph_movement_requirement():
    state = _activated()
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["frightened"]
    before = deepcopy(state)
    result = _qualify(state)
    assert result["_symbol_fear_movement_active"] is False
    assert result["_symbol_fear_movement_sources"] == []
    assert state == before


def test_final_walking_binding_is_idempotent_and_never_binds_teleport():
    runtime, _ = _runtime()
    again = _tools(runtime)
    for name in FEAR_TOOLS:
        assert again[name] == runtime["tools"][name]
    for name, tool in runtime["tools"].items():
        if "check_srd_symbol_fear_segment" in str(tool["effects"]):
            assert name in ("check_srd_symbol_fear_segment", FEAR_TOOLS[-1])


def test_fear_movement_fragment_admits_actual_parent_archive(monkeypatch):
    communication.clock.test_clock_delta_is_admitted_with_actual_completed_parent_archive(
        monkeypatch
    )


def test_actual_archived_paid_fear_walk_reopen_and_saved_walk_retry(tmp_path, monkeypatch):
    import sylvia_gm.application.bundled_packages as bundled

    monkeypatch.setattr(
        bundled, "_bundled_srd_root", lambda: communication.clock.PARENT / "rulesets/dnd-srd-5.2.1"
    )
    monkeypatch.setattr(public_casting, "bundled_srd_archive", owner._test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", _case)
    owner._test_archive.cache_clear()
    database = tmp_path / "fear.db"
    with AuthorityOwner.open(database, "casting-owner") as authority:
        _, _, params = public_casting._create_spell_campaign(authority, "symbol")
        begun, _ = _act(authority, "begin_srd_spell_cast", **_begin(params))
        assert begun.status is ActionStatus.COMMITTED
        continued, _ = _act(
            authority,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED
        cast, _ = _act(authority, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        activated, _ = _act(
            authority,
            "resolve_srd_spell_trigger",
            trigger_id=cast.result["context"]["_trigger_id"],
            event_actor="guard",
            event_kind="open",
            fictional_match=True,
        )
        assert activated.status is ActionStatus.COMMITTED, activated.result
        moved, command = _act(
            authority, "move_srd_scene_creature", actor_state="hero", destination=[-30, 0, 0]
        )
        assert moved.status is ActionStatus.COMMITTED, moved.result
        assert moved.result["context"]["_scene_movement_distance"] == 30
        saved = authority.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as authority:
        assert authority.snapshot("casting") == saved

        class NoReroll(Random):
            def randint(self, _a, _b):
                pytest.fail("A saved Symbol Fear walk retry rolled again")

        monkeypatch.setattr(dice, "_rng", NoReroll(99))
        assert authority.execute_action(command) == moved
        assert authority.snapshot("casting") == saved
    owner._test_archive.cache_clear()
