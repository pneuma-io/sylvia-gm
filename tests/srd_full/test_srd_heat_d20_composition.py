"""Heat's earned grip shares the existing attack/check path without charging area saves."""

from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.srd_d20_normalization import normalize_runtime
from scripts.srd_full.srd_spell_heat_metal import bind_d20_runtime, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_minor_attack_packets import (
    test_actual_ordinary_prismatic64_keeps_packet_budget_after_minor_composition as ordinary64,
)
from tests.srd_full.test_srd_spell_special_damage import (
    test_public_prismatic_complete64_scene_creatures_and_packets as direct64,
)
from tests.srd_full.test_srd_spell_wave3_information import _case, _entity, _scene

ROOT = Path(__file__).resolve().parents[2]
SRD = cards.SRD


@pytest.fixture(scope="module")
def heat_composition_runtime():
    original = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    text = normalize_runtime(bind_d20_runtime(extend_runtime(original)))
    assert normalize_runtime(bind_d20_runtime(extend_runtime(text))) == text
    runtime = _load_runtime(text)
    assert not validate_ruleset_data(runtime)
    return runtime, text.encode()


@pytest.fixture(autouse=True)
def actual_heat_composition(monkeypatch, heat_composition_runtime):
    runtime, encoded = heat_composition_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="a" * 64,
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
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    monkeypatch.setattr(dice, "_rng", Random(0))


@pytest.mark.parametrize("mode", ["ordinary", "direct"])
@pytest.mark.parametrize("success", [True, False])
def test_heat_group_keeps_both_actual_prismatic64_paths(monkeypatch, mode, success):
    actual_case = cards._spell_case

    def admitted_scene(spell):
        state, data, parameters = actual_case(spell)
        state[f"{SRD}.entities"]["hero"]["creature_type"] = "humanoid"
        state[f"{SRD}.state"]["spatial_scene"] = _scene(
            _entity("hero", [0, 0, 0], participant_ref="hero", creature_type="humanoid"),
            _entity("guard", [20, 0, 0], participant_ref="guard", creature_type="humanoid"),
        )
        return state, data, parameters

    monkeypatch.setattr(cards, "_spell_case", admitted_scene)
    operations = 0
    consume = ExecutionBudget.consume_operation

    def counted(execution_budget):
        nonlocal operations
        operations += 1
        consume(execution_budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", counted)
    (ordinary64 if mode == "ordinary" else direct64)(monkeypatch, success)
    assert operations <= 10000
    print(f"Heat grouped {mode} Prismatic64 {success}: {operations} operations")


@pytest.mark.parametrize(
    "kind,expected", [("attack_roll", 2), ("ability_check", 2), ("saving_throw", 1)]
)
def test_actual_heat_damage_earns_attack_and_check_cards_without_penalizing_saves(kind, expected):
    state, _, _ = _case("identify")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=1000, inventory=["metal-item"], held_items=[])
    guard["saving_throw_modifiers"]["constitution"] = -100
    budget._run(
        "record_srd_object_utility_facts",
        state,
        object_id="orb",
        weight_pounds=7,
        manufactured=True,
        metal=True,
        object_kind="armor",
        inventory_item="metal-item",
    )
    budget._run(
        "record_srd_object_holder", state, actor_state="guard", object_id="orb", held_as="worn"
    )
    budget._run(
        "cast_spell_heat_metal_spell",
        state,
        caster_state="hero",
        slot_level=2,
        save_dc=15,
        concentration_modifier=6,
        object_id="orb",
    )
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and guard["temp_hp"] < 1000
    assert len(guard["srd_heat_metal_grips"]) == 1
    result = budget._run(
        "d20_test",
        state,
        actor_state="guard",
        kind=kind,
        ability="strength",
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=15,
    ).result
    assert result["context"]["_effective_disadvantage"] is (expected == 2)
    assert len(result["context"]["_d20_test"]["results"]) == expected
    assert result["result_card"]["title"]
    assert result["result_card"]["details"]


def test_heat_group_preserves_actual_symbol_discord_rolls_and_expiry(
    monkeypatch, heat_composition_runtime
):
    from tests import test_srd_spell_triggers as trigger_owner
    from tests import test_srd_symbol_triggers as symbol_owner

    monkeypatch.setattr(trigger_owner, "_runtime", lambda: heat_composition_runtime)
    monkeypatch.setattr(trigger_owner, "_mechanic", budget._mechanic)
    actual_symbol = symbol_owner._symbol

    def anchored_symbol(effect="death", **choices):
        state, identity = actual_symbol(
            effect,
            trigger_anchor="orb",
            trigger_anchor_kind="object",
            trigger_events=["open"],
            **choices,
        )
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
            row["metadata"]["cover_from"] = {"orb": "half"}
        return state, identity

    monkeypatch.setattr(symbol_owner, "_symbol", anchored_symbol)
    symbol_owner.test_symbol_discord_later_real_roll_disadvantage_and_communication_qualifier()
