"""Aura of Life protects genuine native maximum loss without suppressing received damage."""

import hashlib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.srd_aura_of_life_maximum import MARKER, MUMMY, WIGHT, bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_attack_roll_components import _prepared_caster
from tests.srd_full.test_srd_native_wight_actions import _native_wight_case, _next_wight_turn
from tests.srd_full.test_srd_spell_summoning import _information_case
from tests.srd_full.test_srd_spell_wave3_information import _entity

SRD = cards.SRD
PARENT = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-aura-of-life-maximum/wight-public-parent-runtime.yaml")
)


@pytest.fixture(scope="module")
def maximum_runtime():
    raw = PARENT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "00a7de081d9880701c9bd84f339b10b53ff7eb99a945fc5e39f19b5e325099d5"
    )
    text = bind_runtime_consumers(raw.decode())
    return _load_runtime(text), text.encode()


@pytest.fixture
def maximum_owner(monkeypatch, maximum_runtime):
    runtime, encoded = maximum_runtime

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


def _without_maximum_guard(value):
    if isinstance(value, list):
        result = []
        index = 0
        while index < len(value):
            effect = value[index]
            if isinstance(effect, dict) and effect.get("set", {}).get("field") == MARKER:
                result.extend(_without_maximum_guard(value[index + 2]["on_true"]))
                index += 3
            else:
                result.append(_without_maximum_guard(effect))
                index += 1
        return result
    if isinstance(value, dict):
        return {key: _without_maximum_guard(child) for key, child in value.items()}
    return value


def test_only_native_reduction_consequences_change_and_mummy_clock_still_advances(maximum_runtime):
    original = _load_runtime(PARENT.read_text())
    runtime, encoded = maximum_runtime
    assert bind_runtime_consumers(encoded.decode()) == encoded.decode()
    assert {
        name for name in original["tools"] if original["tools"][name] != runtime["tools"][name]
    } == {WIGHT, MUMMY}
    for name in (WIGHT, MUMMY):
        assert _without_maximum_guard(runtime["tools"][name]) == original["tools"][name]
    # Harm's existing Aura protection and all ordinary/rest/form writers stay identical.
    assert runtime["tools"]["cast_srd_recovery"] == original["tools"]["cast_srd_recovery"]
    boundary = runtime["tools"][MUMMY]["effects"]
    due = next(
        e["on_true"]
        for e in boundary
        if e.get("condition", "").startswith(
            "lasting_state.lasting_tick >= _native_mummy_rot_source.native_next_reduction_tick"
        )
    )
    clock = next(
        i
        for i, e in enumerate(due)
        if e.get("set", {}).get("field", "").endswith(".native_next_reduction_tick")
    )
    assert due[clock - 1]["condition"] == MARKER + " != true"
    assert due[clock]["set"]["value"] == (
        "_native_mummy_rot_source.native_next_reduction_tick + 28800"
    )
    assert any(e.get("dice", {}).get("expression") == "3d6" for e in due[: clock - 3])


def test_paid_live_aura_preserves_native_wight_maximum_then_real_end_allows_reduction(
    maximum_owner,
):
    state, data, victim = _native_wight_case("temporary_hp")
    assert victim == "hero"
    budget._run("end_combat_encounter", state, end_reason="fighting_stopped")
    _prepared_caster(state, "warder", "aura_of_life", 4)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("warder", [-5, 0, 0], participant_ref="warder", creature_type="humanoid")
    )
    budget._run(
        "record_srd_creature_relationship",
        state,
        actor_state="warder",
        toward_state="hero",
        attitude="friendly",
        is_ally=True,
    )
    _, _, parameters = _information_case("aura_of_life")
    parameters.pop("caster")
    parameters.pop("slot_resource")
    parameters.update(caster_state="warder", target="warder", targets=["warder"])
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["warder"]["spell_uses_remaining"]["aura_of_life"] == 0
    assert (
        budget._run("resolve_srd_spell_ward_benefits", state, target_state="hero").result[
            "context"
        ]["_ward_life_effective"]
        is True
    )
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    budget._run(
        "command_srd_animated_undead",
        state,
        caster_state="hero",
        actor_ids=["native_wight"],
        order_kind="specific",
        action="attack",
    )
    budget._run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "native_wight", "warder"],
    )
    for _ in range(3):
        actor = state[f"{SRD}.combat"]["current_actor"]
        budget._run("begin_combat_turn", state, actor=actor, participant_data=data)
        if actor == "native_wight":
            break
        budget._run("end_combat_turn", state)
        budget._run("advance_group_combat_encounter_turn", state)
    assert state[f"{SRD}.combat"]["active_actor"] == "native_wight"
    before = deepcopy(state[f"{SRD}.entities"]["hero"])
    dice._rng.seed(31)
    context = budget._run(
        "use_srd_native_wight_life_drain",
        state,
        participant_data=data,
        actor_state="native_wight",
        target_state="hero",
    ).result["context"]
    hero = state[f"{SRD}.entities"]["hero"]
    taken = (context["_wight_life_drain_roll"]["total"] + 2) // 2
    assert context["_wight_life_drain_outcome"] == "failure" and taken > 0
    assert context["_wight_damage_taken"] == taken
    assert hero["temp_hp"] == before["temp_hp"] - taken
    assert hero["max_hp"] == before["max_hp"]
    assert context["_wight_maximum_reduction"] == 0
    assert context["_wight_maximum_receipt_id"] is None
    assert context["_wight_maximum_prevented"] is False
    assert hero.get("recovery_max_hp_reduction", 0) == before.get("recovery_max_hp_reduction", 0)
    assert hero.get("native_undead_max_hp_reduction_receipts") == before.get(
        "native_undead_max_hp_reduction_receipts"
    )
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True

    budget._run("end_concentration", state, caster_state="warder")
    _next_wight_turn(state, data)
    before = deepcopy(state[f"{SRD}.entities"]["hero"])
    dice._rng.seed(31)
    context = budget._run(
        "use_srd_native_wight_life_drain",
        state,
        participant_data=data,
        actor_state="native_wight",
        target_state="hero",
    ).result["context"]
    taken = context["_wight_life_drain_roll"]["total"] + 2
    assert context["_wight_damage_taken"] == context["_wight_maximum_reduction"] == taken
    assert state[f"{SRD}.entities"]["hero"]["max_hp"] == before["max_hp"] - taken
