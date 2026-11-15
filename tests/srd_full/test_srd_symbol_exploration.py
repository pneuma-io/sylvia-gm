"""Symbol exploration consequences use real clock rounds and actual saved area occupants."""

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_triggers import _tools

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_spell_triggers as owner
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_wave3_information import _wall

PARENT = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-symbol-exploration/input-1"))
CLOCK_TOOLS = (
    "qualify_srd_symbol_turn",
    "apply_srd_symbol_target",
    "resolve_srd_symbol_targets",
    "resolve_srd_symbol_boundary",
    "resolve_srd_symbol_exploration_round",
)


@lru_cache
def _runtime():
    payload = (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == (
        "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
    )
    runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
    changed = _tools(runtime)
    # Preserve final completed sight/damage consumers while overlaying only this clock delta.
    runtime["tools"].update({name: changed[name] for name in CLOCK_TOOLS})
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


def _activated():
    state, _, _, identity = owner._cast("symbol", symbol_effect="death")
    owner._entry(state, "hero")["position"] = [200, 0, 0]
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, turn_sequence=9000)
    for actor, modifier in [("guard", 30), ("other", -30)]:
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["constitution"] = modifier
    owner._event(state, identity, event="open")
    return state, identity


def _round(state):
    return _run("resolve_srd_symbol_exploration_round", state).result["context"]


def test_exploration_ignores_stale_combat_counter_and_uses_actual_saved_six_second_round():
    state, _ = _activated()
    qualified = _run("qualify_srd_symbol_turn", state).result["context"]
    assert qualified["_symbol_turn_mode"] == "clock"
    assert qualified["_symbol_turn"] == state[f"{SRD}.state"]["lasting_tick"] // 2
    saved = deepcopy(state)
    assert _round(state)["_symbol_clock_details"] == []
    assert state == saved
    state = json.loads(json.dumps(state))
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    before = {name: state[f"{SRD}.entities"][name]["hp"] for name in ("guard", "other")}
    rows = _round(state)["_symbol_clock_details"]
    assert len(rows) == 2
    assert state[f"{SRD}.entities"]["guard"]["hp"] < before["guard"]
    assert state[f"{SRD}.entities"]["other"]["hp"] < before["other"]
    saved = deepcopy(state)
    assert _round(state)["_symbol_clock_details"] == []
    assert state == saved


@pytest.mark.parametrize("reason", ["outside", "plane", "cover"])
def test_actual_exploration_occupants_preserve_range_plane_and_total_cover(reason):
    state, _ = _activated()
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    owner._entry(state, "guard")["position"] = [100, 0, 0]
    other = owner._entry(state, "other")
    if reason == "outside":
        other["position"] = [100, 0, 0]
    elif reason == "plane":
        other["metadata"]["plane"] = "ethereal"
    else:
        other["position"] = [20, 0, 0]
        state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(1, material="stone")]
        other["metadata"]["cover_from"] = {owner._source(state)["anchor"]: "total"}
    hp = state[f"{SRD}.entities"]["other"]["hp"]
    assert _round(state)["_symbol_clock_details"] == []
    assert state[f"{SRD}.entities"]["other"]["hp"] == hp


def test_combat_turn_and_clock_round_of_same_number_have_distinct_once_per_turn_identity():
    state, identity = _activated()
    turn = owner._source(state)["symbol_turn"]
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True, turn_sequence=turn)
    before = state[f"{SRD}.entities"]["other"]["hp"]
    result = _run(
        "resolve_srd_symbol_boundary", state, target_state="other", boundary_kind="end"
    ).result["context"]
    assert len(result["_symbol_boundary_details"]) == 1
    assert state[f"{SRD}.entities"]["other"]["hp"] < before
    assert owner._source(state)["symbol_turn_mode"] == "combat"
    saved = deepcopy(state)
    assert (
        _run(
            "resolve_srd_symbol_boundary", state, target_state="other", boundary_kind="end"
        ).result["context"]["_symbol_boundary_details"]
        == []
    )
    assert state == saved
    assert _round(state)["_symbol_clock_details"] == []
    assert owner._source(state)["instance"] == identity


def test_exact_ten_minute_expiry_retires_exploration_source_without_new_roll():
    state, _ = _activated()
    for _ in range(100):
        _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    before = deepcopy(state)
    assert _round(state)["_symbol_clock_details"] == []
    assert state == before
    assert not state[f"{SRD}.state"]["has_active_symbol_auras"]


def test_symbol_round_owner_and_accepted_schema_emission_remain_idempotent():
    runtime, _ = _runtime()
    again = _tools(runtime)
    for name in CLOCK_TOOLS:
        assert again[name] == runtime["tools"][name]


def test_clock_delta_is_admitted_with_actual_completed_parent_archive(monkeypatch):
    import sylvia_gm.application.bundled_packages as bundled

    monkeypatch.setattr(bundled, "_bundled_srd_root", lambda: PARENT / "rulesets/dnd-srd-5.2.1")
    owner._test_archive.cache_clear()
    try:
        assert owner._test_archive()
    finally:
        owner._test_archive.cache_clear()
