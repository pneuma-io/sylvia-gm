"""A paid Ice Storm adds hail cost only until the native caster next-turn boundary."""

from copy import deepcopy
from functools import lru_cache

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_ice_storm_terrain import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_spell_result_cards import _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene, _turn


@lru_cache
def _runtime():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    text = bind_runtime_consumers(text)
    assert _load_runtime(bind_runtime_consumers(text)) == _load_runtime(text)
    return text.encode(), _load_runtime(text)


@lru_cache
def _mechanic(name):
    encoded, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=budget.SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


def _case(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    state, data, params = _spell_case("ice_storm")
    _admit_scene(state)
    for actor in ("guard", "other"):
        state[f"{budget.SRD}.entities"][actor].update(hp=1000, max_hp=1000)
    state[f"{budget.SRD}.state"]["spatial_scene"]["zones"] = [
        {
            "id": "hail_ground",
            "map_id": "orders",
            "shape": {"kind": "box", "min": [5, -5, 0], "max": [15, 5, 0.001]},
            "metadata": {"ground": True, "plane": "material", "difficult": False},
        },
        {
            "id": "existing_mud",
            "map_id": "orders",
            "shape": {"kind": "box", "min": [25, -5, 0], "max": [35, 5, 0.001]},
            "metadata": {"ground": True, "plane": "material", "difficult": True},
        },
    ]
    params.update(targets=["guard"], ice_storm_ground_zone_ids=["hail_ground"])
    return state, data, params


def _cost(state, start, end, **extra):
    return budget._run(
        "srd_information_movement_cost",
        state,
        start=start,
        end=end,
        base_difficult=False,
        plane="material",
        **extra,
    ).result["context"]["_route_cost"]


def test_paid_hail_terrain_survives_current_end_and_retires_at_caster_next_end(monkeypatch):
    state, data, params = _case(monkeypatch)
    ordinary = deepcopy(state[f"{budget.SRD}.state"]["spatial_scene"]["zones"])
    result = budget._run("cast_spell", state, participant_data=data, **params)
    assert result.result["status"] == "committed"
    assert state[f"{budget.SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    assert _cost(state, [0, 0, 0], [20, 0, 0]) == 30
    assert _cost(state, [0, 0, 0], [20, 0, 0], ignore_ground_environments=True) == 20
    assert _cost(state, [20, 0, 0], [40, 0, 0]) == 30
    source = next(
        r for r in state[f"{budget.SRD}.state"]["turn_effects"] if r["kind"] == "ice_storm_terrain"
    )
    assert source["turn"] == state[f"{budget.SRD}.entities"]["hero"]["turns_started"] + 1
    budget._run("end_combat_turn", state, actor="hero")
    assert _cost(state, [0, 0, 0], [20, 0, 0]) == 30
    _turn(state, "hero", data)
    assert _cost(state, [0, 0, 0], [20, 0, 0]) == 30
    budget._run("end_combat_turn", state, actor="hero")
    assert _cost(state, [0, 0, 0], [20, 0, 0]) == 20
    assert _cost(state, [20, 0, 0], [40, 0, 0]) == 30
    assert state[f"{budget.SRD}.state"]["spatial_scene"]["zones"] == ordinary


def test_absent_ground_selection_rejects_paid_cast_atomically(monkeypatch):
    state, data, params = _case(monkeypatch)
    params["ice_storm_ground_zone_ids"] = ["absent"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="ground zone is absent"):
        budget._run("cast_spell", state, participant_data=data, **params)
    assert state == before
