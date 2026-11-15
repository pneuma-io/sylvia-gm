"""Each distinct hand option reuses the native casting and receiving owners."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts import srd_arcane_hand as hand
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_wave3_information import _case as scene_case

SOURCE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-arcane-hand-modes/dnd-srd-5.2.1")
)


@lru_cache
def _runtime():
    parent = yaml.load((SOURCE / "runtime/ruleset.yaml").read_text(), Loader=yaml.CSafeLoader)
    parent["definitions"]["spells"]["arcane_hand"] = deepcopy(hand.PROFILE)
    parent["definition_row_schemas"]["definitions.spells"]["variants"]["arcane_hand"] = (
        hand.schema()
    )
    text = hand.bind_runtime_consumers(yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False))
    assert hand.bind_runtime_consumers(text) == text
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "begin_srd_spell_cast_internal",
    ):
        spell = next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")
        if "enum_values" in spell:
            spell["enum_values"] = sorted(set(spell["enum_values"]) | {"arcane_hand"})
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
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


class Rolls:
    def __init__(self, d20=1):
        self.d20 = d20

    def randint(self, _a, sides):
        return self.d20 if sides == 20 else 1


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())
    original = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = original(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _case(option, slot=5):
    state, data, params = scene_case("arcane_hand")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] = [e for e in scene["entities"] if e["id"] in {"hero", "guard"}]
    scene["zones"] = [
        {
            "id": "ambient",
            "map_id": "information",
            "shape": {"kind": "box", "min": [-1000] * 3, "max": [1000] * 3},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    ]
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=100, max_hp=100, temp_hp=0, creature_kind="monster", size_rank=2)
    target["saving_throw_modifiers"].update(strength=0, dexterity=0)
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = {
        "dexterity": (data["abilities"]["dexterity"] - 10) // 2
    }
    params.update(
        hand_id="hand",
        hand_position=[12, 0, 0],
        hand_height=6,
        hand_option=option,
        slot_resource=f"spell_slot_{slot}",
    )
    if option != "interposing_hand":
        params["hand_target"] = "guard"
    _next(state)
    return state, data, params


def _next(state):
    _run("end_combat_turn", state, actor="hero")
    _run("begin_combat_turn", state, actor="hero")


def _cast(state, data, params):
    return _run("cast_spell", state, participant_data=data, **params)


def _position(state, subject):
    return next(
        e["position"]
        for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if e["id"] == subject
    )


def test_forceful_real_strength_save_moves_target_and_hand_with_fixed_limit():
    state, data, params = _case("forceful_hand")
    params["hand_push_feet"] = 10
    _cast(state, data, params)
    record = state[f"{SRD}.state"]["spell_force_records"]["hand"]
    assert record["last_option_save"]["ability"] == "strength"
    assert record["last_option_save"]["outcome"] == "failure"
    assert _position(state, "guard") == [30, 0, 0]
    assert _position(state, "hand") == [22, 0, 0]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
    _next(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="push exceeds"):
        _run(
            "command_srd_arcane_hand",
            state,
            caster_state="hero",
            force_id="hand",
            destination=[22, 0, 0],
            force_target="guard",
            hand_option="forceful_hand",
            hand_push_feet=6 + record["casting_modifier"] * 5,
        )
    assert state == before


def test_grasping_real_save_casting_turn_crush_and_action_escape_preserve_exact_source(monkeypatch):
    state, data, params = _case("grasping_hand", slot=7)
    params.update(hand_target="hero", hand_position=[6, 0, 0])
    state[f"{SRD}.entities"]["hero"]["size_rank"] = 2
    _cast(state, data, params)
    record = state[f"{SRD}.state"]["spell_force_records"]["hand"]
    assert record["last_option_save"]["ability"] == "dexterity"
    target = state[f"{SRD}.entities"]["hero"]
    assert "grappled" in target["conditions"]
    assert target["grapple_escape_dc"] == record["save_dc"]
    hp_before = target["hp"]
    state = json.loads(json.dumps(state))
    monkeypatch.setattr(dice, "_rng", Rolls(20))
    _run(
        "command_srd_arcane_hand",
        state,
        participant_data=data,
        caster_state="hero",
        force_id="hand",
        destination=[6, 0, 0],
        force_target="hero",
        hand_option="grasping_hand",
        hand_crush=True,
    )
    target = state[f"{SRD}.entities"]["hero"]
    assert target["hp"] == hp_before - (8 + record["casting_modifier"])
    assert target["bonus_action_available"] is False
    target["conditions"].append("poisoned")
    _next(state)
    escaped = _run(
        "escape_srd_grapple_action",
        state,
        participant_data={**data, "skill_proficiencies": []},
        actor="hero",
        actor_state="hero",
        ability="strength",
        advantage=False,
        disadvantage=False,
        hand_force_id="hand",
    )
    assert escaped.result["context"]["_escape_success"] is True
    target = state[f"{SRD}.entities"]["hero"]
    assert "grappled" not in target["conditions"] and "poisoned" in target["conditions"]
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.state"]["spell_force_records"]["hand"]["grapple_target"] is None
    _run("end_srd_concentration_node", state, source_state="hero", target_state="hero")
    assert state[f"{SRD}.state"]["spell_force_records"]["hand"]["active"] is False


def test_interposing_actual_cover_and_enemy_terrain_use_current_mode_and_end():
    state, data, params = _case("interposing_hand")
    params["hand_position"] = [10, 0, 0]
    _cast(state, data, params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(e for e in scene["entities"] if e["id"] == "guard")["metadata"]["enemy_of"] = ["hero"]
    covered = _run(
        "resolve_srd_effective_armor_class",
        state,
        target_state="hero",
        base_armor_class=12,
        hand_cover_source="guard",
        lasting_state="state",
    ).result["context"]
    assert covered["_effective_armor_class"] == 14
    dex = _run(
        "resolve_srd_timed_condition_initial_save",
        state,
        target_state="hero",
        save_ability="dexterity",
        save_dc=40,
        selected_condition="blinded",
        source_state_id="guard",
    ).result["context"]
    assert (
        dex["_card_d20_total"]
        == dex["_card_d20_roll"]["total"]
        + state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"]["dexterity"]
        + 2
    )
    args = {"start": [6, 0, 0], "end": [14, 0, 0], "base_difficult": False, "plane": "material"}
    enemy = _run("srd_information_movement_cost", state, actor_subject="guard", **args).result[
        "context"
    ]
    ally = _run("srd_information_movement_cost", state, actor_subject="hero", **args).result[
        "context"
    ]
    assert enemy["_route_cost"] == 16 and ally["_route_cost"] == 8
    _run("end_srd_concentration_node", state, source_state="hero", target_state="hero")
    uncovered = _run(
        "resolve_srd_effective_armor_class",
        state,
        target_state="hero",
        base_armor_class=12,
        hand_cover_source="guard",
        lasting_state="state",
    ).result["context"]
    assert uncovered["_effective_armor_class"] == 12
