"""Printed insect hit/save riders reuse paid source creation and real next-turn expiry."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.srd_giant_insect_actions import extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_summoning import SRD, _insect_case, _insect_jab, _insect_turn
from tests.srd_full.test_srd_spell_wave3_information import _case as _information_case

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def insect_actions_runtime():
    text = extend_runtime((ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text())
    assert extend_runtime(text) == text
    return _load_runtime(text), text.encode()


@pytest.fixture
def insect_actions_owner(monkeypatch, insect_actions_runtime):
    import tests.srd_full.test_srd_action_budget as budget
    import tests.srd_full.test_srd_spell_result_cards as cards

    runtime, encoded = insect_actions_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="d" * 64,
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


def _next_turn(state):
    actor = state[f"{SRD}.combat"]["active_actor"]
    _run("end_combat_turn", state, actor=actor)
    _run("advance_group_combat_encounter_turn", state)
    actor = state[f"{SRD}.combat"]["current_actor"]
    _run("begin_combat_turn", state, actor=actor)
    return actor


def _cast_freedom(state, data):
    """Admit and pay the Druid's real Freedom spell before creating its insect."""
    freedom, _, parameters = _information_case("freedom_of_movement")
    caster = state[f"{SRD}.entities"]["hero"]
    materials = deepcopy(freedom[f"{SRD}.entities"]["hero"]["spell_materials"])
    for material in materials:
        material["id"] = "freedom_" + material["id"]
    caster["spell_materials"] += materials
    caster["inventory"] += [row["inventory_item"] for row in materials]
    caster["prepared_spells"].append("freedom_of_movement")
    data["spellcasting_grants"][0]["spells"].append("freedom_of_movement")
    parameters.update(targets=["guard"], material_item_ids=[row["id"] for row in materials])
    before = caster["resources"]["spell_slot_4"]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == before - 1
    assert state[f"{SRD}.entities"]["guard"]["spell_freedom_of_movement"] is True


def test_web_bolt_real_hit_speed_zero_survives_save_until_insect_next_turn(
    monkeypatch,
    insect_actions_owner,
):
    state, data, parameters = _insect_case("spider")
    _cast_freedom(state, data)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _insect_turn(state)
    _run("sync_srd_group_lasting_clock", state, lasting_state="state")
    monkeypatch.setattr(dice, "_rng", Random(5))
    result = _insect_jab(
        state,
        attack_name="Web Bolt",
        attack_mode="ranged",
        damage_expression="d10",
        damage_type="bludgeoning",
        normal_range=60,
        long_range=60,
    ).result["context"]
    assert result["_damage_applied"] > 0
    target = state[f"{SRD}.entities"]["guard"]
    assert target["speed"] > 0 and target["spell_speed_reduction"] == target["speed"]
    assert target["native_insect_speed_zero"] is True
    records = state[f"{SRD}.state"]["turn_effects"]
    native = next((row for row in records if row.get("native_attack") == "Web Bolt"), None)
    assert native is not None, records
    assert native["owner"] == native["caster"] == "insect_one"
    assert native["expires_tick"] is None and native["cast_id"] == ""
    state = json.loads(json.dumps(state))
    assert _next_turn(state) == "guard"
    assert state[f"{SRD}.combat"]["movement_remaining"] == 0
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "spend_combat_movement",
            state,
            actor="guard",
            actor_state="guard",
            distance=5,
            difficult_terrain=False,
            movement_kind="walk",
        )
    assert state == before
    assert _next_turn(state) == "hero"
    assert state[f"{SRD}.entities"]["guard"]["spell_speed_reduction"] > 0
    assert _next_turn(state) == "insect_one"
    assert state[f"{SRD}.entities"]["guard"]["spell_speed_reduction"] == 0
    assert state[f"{SRD}.entities"]["guard"]["native_insect_speed_zero"] is False
    assert _next_turn(state) == "guard"
    _run(
        "spend_combat_movement",
        state,
        actor="guard",
        actor_state="guard",
        distance=5,
        difficult_terrain=True,
        movement_kind="walk",
    )
    assert state[f"{SRD}.combat"]["movement_remaining"] == target["speed"] - 5
    assert state[f"{SRD}.entities"]["guard"]["spell_freedom_of_movement"] is True


def test_venomous_spew_real_bonus_action_save_and_next_insect_turn_expiry(
    monkeypatch,
    insect_actions_owner,
):
    state, data, parameters = _insect_case("centipede")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _insect_turn(state)
    _run("sync_srd_group_lasting_clock", state, lasting_state="state")
    monkeypatch.setattr(dice, "_rng", Random(1))
    result = _run(
        "use_srd_insect_venomous_spew", state, actor_state="insect_one", target_state="guard"
    ).result
    context = result["context"]
    assert context["_insect_spew_outcome"] == "failure"
    insect = state[f"{SRD}.entities"]["insect_one"]
    assert context["_insect_spew_dc"] == insect["summon_save_dc"]
    assert len(context["_insect_spew_save_roll"]["results"]) == 1
    assert insect["bonus_action_available"] is False
    assert state[f"{SRD}.combat"]["action_available"] is True
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    state = json.loads(json.dumps(state))
    assert _next_turn(state) == "guard"
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert _next_turn(state) == "hero"
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert _next_turn(state) == "insect_one"
    assert "poisoned" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_dead_insect_spew_expires_when_real_group_advance_skips_its_next_slot(
    monkeypatch,
    insect_actions_owner,
):
    state, data, parameters = _insect_case("centipede")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _insect_turn(state)
    _run("sync_srd_group_lasting_clock", state, lasting_state="state")
    monkeypatch.setattr(dice, "_rng", Random(1))
    _run("use_srd_insect_venomous_spew", state, actor_state="insect_one", target_state="guard")
    lasting = state[f"{SRD}.state"]
    native = next(
        row for row in lasting["turn_effects"] if row.get("native_attack") == "Venomous Spew"
    )
    assert native["native_order"][native["native_order_index"]]["entity_id"] == "insect_one"
    entities = state[f"{SRD}.entities"]
    _run(
        "apply_srd_damage",
        state,
        target="insect_one",
        amount=entities["insect_one"]["hp"],
        adjustment=0,
        damage_type="force",
    )
    entities = state[f"{SRD}.entities"]
    assert entities["insect_one"]["is_alive"] is False
    assert entities["insect_one"]["created_active"] is False
    assert "poisoned" in entities["guard"]["conditions"]
    state = json.loads(json.dumps(state))
    assert _next_turn(state) == "guard"
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert _next_turn(state) == "hero"
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert _next_turn(state) == "guard"
    assert state[f"{SRD}.entities"]["insect_one"]["turns_started"] == 1
    assert "poisoned" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert not any(
        row.get("native_attack") == "Venomous Spew" for row in state[f"{SRD}.state"]["turn_effects"]
    )


def test_web_rider_carries_real_remaining_slot_deadline_into_exploration(
    monkeypatch,
    insect_actions_owner,
):
    state, data, parameters = _insect_case("spider")
    _cast_freedom(state, data)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _insect_turn(state)
    _run("sync_srd_group_lasting_clock", state, lasting_state="state")
    monkeypatch.setattr(dice, "_rng", Random(5))
    _insect_jab(
        state,
        attack_name="Web Bolt",
        attack_mode="ranged",
        damage_expression="d10",
        damage_type="bludgeoning",
        normal_range=60,
        long_range=60,
    )
    combat = state[f"{SRD}.combat"]
    lasting = state[f"{SRD}.state"]
    native = next(row for row in lasting["turn_effects"] if row.get("native_attack") == "Web Bolt")
    deadline = (
        combat["lasting_epoch_tick"]
        + 2 * (native["native_due_round"] - combat["lasting_epoch_round"])
        + 2 * native["native_order_index"] / len(combat["order"])
    )
    _run("end_combat_encounter", state, end_reason="fighting_stopped")
    native = next(
        row
        for row in state[f"{SRD}.state"]["turn_effects"]
        if row.get("native_attack") == "Web Bolt"
    )
    assert native["native_free_time_deadline"] == pytest.approx(deadline)
    assert native["expires_tick"] is None
    assert state[f"{SRD}.entities"]["guard"]["spell_speed_reduction"] > 0
    state = json.loads(json.dumps(state))
    remaining = _run(
        "derive_srd_scene_movement_budget", state, actor_state="guard", mode="walk"
    ).result["context"]
    assert remaining["_scene_exploration_remaining"] == 0
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.state"]["lasting_tick"] >= deadline
    assert state[f"{SRD}.entities"]["guard"]["spell_speed_reduction"] == 0
    assert state[f"{SRD}.entities"]["guard"]["native_insect_speed_zero"] is False
    remaining = _run(
        "derive_srd_scene_movement_budget", state, actor_state="guard", mode="walk"
    ).result["context"]
    assert remaining["_scene_exploration_remaining"] == state[f"{SRD}.entities"]["guard"]["speed"]
    assert not any(
        row.get("native_attack") == "Web Bolt" for row in state[f"{SRD}.state"]["turn_effects"]
    )
