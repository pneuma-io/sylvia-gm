"""Recomposing retained initiative calls must restore the actual Invisible dice benefit."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_illumination import _compose_consumers
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests.srd_full.test_srd_action_budget import SRD
from tests.srd_full.test_srd_spell_illumination import (
    test_faerie_fire_suppresses_native_invisible_group_initiative_benefit as _outlined_group,
)
from tests.srd_full.test_srd_spell_illumination import (
    test_invisible_initiative_advantage_uses_actual_condition_and_cancels_surprise as _invisible,
)


@lru_cache
def _recomposed_runtime():
    text = (
        Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    ).read_text()
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    tools = deepcopy(runtime["tools"])
    encounter = tools["start_combat_encounter_entities"]
    assert (
        sum(e.get("call") == "check_srd_illumination_initiative" for e in encounter["effects"]) == 2
    )
    for actor in ("first", "second"):
        die = next(
            e["dice"]
            for e in encounter["effects"]
            if e.get("dice", {}).get("modifier") == actor + "_actor.initiative_modifier"
        )
        die["advantage"] = actor + "_actor.spell_foresight == true"
        die["disadvantage"] = (
            actor + "_surprised == true and " + actor + "_actor.spell_foresight != true"
        )
    group = tools["roll_srd_actor_group_initiative"]
    next(e["dice"] for e in group["effects"] if "dice" in e)["advantage"] = (
        "actor_state.spell_foresight == true"
    )
    changed = {}
    _compose_consumers(tools, changed)
    repaired = {
        name: changed[name]
        for name in ("start_combat_encounter_entities", "roll_srd_actor_group_initiative")
    }
    assert (
        sum(
            e.get("call") == "check_srd_illumination_initiative"
            for e in repaired["start_combat_encounter_entities"]["effects"]
        )
        == 2
    )
    again = {}
    _compose_consumers(tools | repaired, again)
    assert all(again[name] == tool for name, tool in repaired.items())
    text = _replace_tools(text, repaired)
    return yaml.load(text, Loader=yaml.CSafeLoader), text.encode()


@lru_cache
def _mechanic(name):
    runtime, payload = _recomposed_runtime()
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
def _actual_runtime(monkeypatch):
    runtime, _ = _recomposed_runtime()
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards, "_spell_definitions", lambda: runtime["definitions"]["spells"]
    )


@pytest.mark.parametrize("surprised,rolls", [(False, 2), (True, 1)])
def test_retained_helper_call_still_restores_invisible_initiative_and_surprise(surprised, rolls):
    _invisible(surprised, rolls)


def test_recomposed_group_keeps_actual_outline_suppression():
    _outlined_group()
