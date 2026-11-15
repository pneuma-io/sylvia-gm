"""An actual spell event reports its typed base exposure separately from extra Curse damage."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.srd_spell_damage_consumers import _effect_lists, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime import _effect_executor
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_spell_damage_consumers import _grant, _npc_attack
from tests.srd_full.test_srd_spell_wave3_afflictions import _cast as _curse_cast

SRD = cards.SRD
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def receipt_runtime():
    text = extend_runtime((ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text())
    runtime = _load_runtime(text)
    event = deepcopy(runtime["tools"]["resolve_srd_fire_shield_hit"])
    for effects in _effect_lists(event["effects"]):
        for effect in effects:
            if effect.get("call") == "apply_srd_qualifying_damage":
                effect["params"]["observe_base_damage"] = True
                effect.setdefault("export_context", {})["_fire_base_receipt"] = (
                    "_source_base_effective_damage"
                )
    text = _replace_tools(text, {"resolve_srd_fire_shield_hit": event})
    assert extend_runtime(text) == text
    runtime = _load_runtime(text)
    assert not validate_ruleset_data(runtime)
    return runtime, text.encode()


@pytest.fixture(autouse=True)
def actual_receipt_owner(monkeypatch, receipt_runtime):
    runtime, encoded = receipt_runtime

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
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


@pytest.mark.parametrize(
    "curse,fire_defense,necrotic_resistance,base,total",
    [
        (False, "none", False, 16, 16),
        (True, "none", False, 16, 24),
        (True, "resistant", False, 8, 16),
        (True, "immune", False, 0, 8),
        (True, "immune", True, 0, 4),
    ],
)
def test_actual_fire_event_receipt_distinguishes_fire_exposure_from_total_temp_hp_packet(
    monkeypatch, curse, fire_defense, necrotic_resistance, base, total
):
    if curse:
        state, data, _, _ = _curse_cast(
            monkeypatch, "bestow_curse", curse_mode="damage", slot_resource="spell_slot_5"
        )
        shield = _grant(state, data, "fire_shield")
    else:
        state, data, shield = cards._spell_case("fire_shield")
        state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    shield.pop("target")
    shield["actor_spell_choice"] = "warm"
    budget._run("cast_spell_outside_combat", state, participant_data=data, **shield)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        temp_hp=1000,
        multiattack=[{"attack": "Claw", "count": 1}],
        damage_immunities=["fire"] if fire_defense == "immune" else [],
        damage_resistances=(
            (["fire"] if fire_defense == "resistant" else [])
            + (["necrotic"] if necrotic_resistance else [])
        ),
    )
    state[f"{SRD}.entities"]["hero"].update(hp=1000, max_hp=1000, temp_hp=0)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        normal_action_spent=False,
    )

    class ActualMaximumFaces:
        def randint(self, low, high):
            assert low == 1
            return high

    monkeypatch.setattr(dice, "_rng", ActualMaximumFaces())
    receipts = []
    actual_call = _effect_executor._EFFECT_HANDLERS["call"]

    def observe(body, effect, context, executor):
        result = actual_call(body, effect, context, executor)
        if body == "apply_srd_qualifying_damage" and context.current_tool == (
            "resolve_srd_fire_shield_hit"
        ):
            receipts.append(context.vars["_fire_base_receipt"])
        return result

    monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "call", observe)
    _npc_attack(state, "Claw", attack_modifier=100, attack_mode="melee", attack_distance=5)
    assert receipts == [base]
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and guard["temp_hp"] == 1000 - total
