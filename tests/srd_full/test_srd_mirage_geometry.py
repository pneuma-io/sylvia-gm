"""Contained terrain replacements retain true form and exact overlapping physical sources."""

from copy import deepcopy
from functools import lru_cache

import pytest
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_extended_casting import extend_runtime as extend_casting
from scripts.srd_full.srd_mirage_geometry import extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_mirage_arcane as mirage
from tests import test_srd_spell_result_cards as cards
from tests.test_spatial_engine import _geometry
from tests.srd_full.test_srd_action_budget import SRD, _run

_base_runtime = mirage._runtime
_base_case = mirage._case


@lru_cache
def _runtime():
    _, content = _base_runtime()
    text = refresh_exploration_runtime(extend_runtime(content.decode()))
    runtime = _load_runtime(text)
    text = extend_casting(text, spell_ids=sorted(runtime["definitions"]["spells"]))
    return _load_runtime(text), text.encode()


@pytest.fixture(autouse=True)
def _authored(monkeypatch):
    monkeypatch.setattr(mirage, "_runtime", _runtime)
    mirage._mechanic.cache_clear()
    monkeypatch.setattr(budget, "_mechanic", mirage._mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])


def _case():
    state, data, parameters = _base_case()
    hill = _geometry("hill", [8, -5, 0], [12, 5, 8])
    hill["map_id"] = "relation"
    hill["metadata"].update(
        plane="material", natural_terrain=True, terrain_kind="rocky hill", ground=False
    )
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(hill)
    return state, data, parameters


def _spec(**changes):
    return {
        "subject": "hill",
        "minimum": [8, -5, 0],
        "maximum": [12, 5, 8],
        "movement": "passable",
        "ground": False,
        "terrain_kind": "clear meadow",
        "tactile_kind": "soft grass",
        **changes,
    }


def _hill(state):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"] if row["id"] == "hill"
    )


def test_contained_physical_terrain_replacement_allows_actual_movement():
    state, _, _ = _case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="blocked|obstruct|movement"):
        _run("move_srd_scene_creature", state, actor_state="hero", destination=[15, 0, 0])
    assert state == before
    mirage._cast(state, mirage_terrain_changes=[_spec()])
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[15, 0, 0])
    assert _hill(state)["movement"] == "passable"
    actor = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "hero"
    )
    assert actor["position"] == [15, 0, 0]


def test_reached_truesight_retains_tangible_replacement_and_true_terrain():
    state, _, _ = _case()
    mirage._cast(state, mirage_terrain_changes=[_spec()])
    state[f"{SRD}.entities"]["hero"]["truesight_feet"] = 120
    actor = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "hero"
    )
    actor["position"] = [5, 0, 4]
    result = _run("perceive_srd_mirage_terrain", state, observer_state="hero", subject="hill")
    rows = mirage._summary(result)
    assert rows["Terrain"] == "rocky hill" and rows["True form visible"]
    assert rows["Physical feel"] == "soft grass"
    assert _hill(state)["movement"] == "passable"


@pytest.mark.parametrize("blinded", [False, True])
def test_actual_surface_range_and_blindness_preserve_physical_feel(blinded):
    state, _, _ = _case()
    mirage._cast(state, mirage_terrain_changes=[_spec()])
    observer = state[f"{SRD}.entities"]["hero"]
    observer["truesight_feet"] = 120 if blinded else 2
    if blinded:
        observer["conditions"].append("blinded")
    actor = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "hero"
    )
    actor["position"] = [5, 0, 4]
    rows = mirage._summary(
        _run("perceive_srd_mirage_terrain", state, observer_state="hero", subject="hill")
    )
    assert rows["Terrain"] == ("" if blinded else "clear meadow")
    assert rows["True form visible"] is False
    assert rows["Physical feel"] == "soft grass"


def test_exact_overlap_retirement_restores_previous_then_original_geometry():
    state, _, _ = _case()
    original = deepcopy(_hill(state))
    old = mirage._cast(state, mirage_terrain_changes=[_spec()])
    new = mirage._cast(state, mirage_terrain_changes=[_spec(movement="blocking")])
    assert _hill(state)["metadata"]["mirage_projection_id"] == new
    _run("dismiss_srd_mirage_arcane", state, caster_state="hero", mirage_id=new)
    assert _hill(state)["movement"] == "passable"
    assert _hill(state)["metadata"]["mirage_projection_id"] == old
    _hill(state)["metadata"]["independent_note"] = "preserved"
    _run("dismiss_srd_mirage_arcane", state, caster_state="hero", mirage_id=old)
    assert _hill(state)["volume"] == original["volume"]
    assert _hill(state)["movement"] == original["movement"]
    assert _hill(state)["visibility"] == original["visibility"]
    assert _hill(state)["metadata"]["independent_note"] == "preserved"


def test_unsupported_partial_original_box_is_rejected_atomically():
    state, _, _ = _case()
    _hill(state)["volume"]["min"][0] = -30
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="partial original-box"):
        mirage._cast(state, mirage_terrain_changes=[_spec()])
    assert state == before


def test_identical_expiry_of_independent_sources_restores_original_once():
    state, _, _ = _case()
    mirage._cast(state, mirage_terrain_changes=[_spec()])
    mirage._cast(state, mirage_terrain_changes=[_spec(movement="blocking")])
    _run("advance_srd_lasting_time", state, elapsed_rounds=100000)
    _run("advance_srd_lasting_time", state, elapsed_rounds=44000)
    assert _hill(state)["movement"] == "blocking"
    assert state[f"{SRD}.state"]["mirage_geometry_bases"]["hill"] is None


def test_geometry_emitter_semantic_repeat():
    runtime, content = _runtime()
    assert _load_runtime(extend_runtime(content.decode())) == runtime


def test_strict_paid_terrain_replacement_later_crossing_and_reopen(tmp_path, monkeypatch):
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests.srd_full.test_srd_npc_spell_casting import _act

    def actual_case():
        state, data, parameters = _case()
        parameters["mirage_terrain_changes"] = [_spec()]
        return state, data, parameters

    wall = mirage._wall()
    wall.update(position=[10, 10, 0], extent=[2, 2, 8])
    monkeypatch.setattr(mirage, "_case", actual_case)
    monkeypatch.setattr(mirage, "_wall", lambda: wall)
    mirage.test_strict_paid_ten_minute_cast_reopen_retry_and_actual_later_movement(
        tmp_path, monkeypatch
    )
    with AuthorityOwner.open(tmp_path / "mirage.db", "casting-owner") as owner:
        before = owner.snapshot("casting")
        assert _hill(before.state)["movement"] == "passable"
        moved, command = _act(
            owner, "move_srd_scene_creature", actor_state="hero", destination=[15, 0, 0]
        )
        assert moved.status is ActionStatus.COMMITTED
        assert mirage._summary(moved)["Movement spent"] == 20
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(tmp_path / "mirage.db", "casting-owner") as owner:
        assert owner.execute_action(command) == moved
        assert owner.snapshot("casting").state == saved.state
        assert saved.state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 30
