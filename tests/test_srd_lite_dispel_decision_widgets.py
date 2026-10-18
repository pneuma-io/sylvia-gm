"""Selected actual-owner Dispel feedback, with no paid cast or whole spell matrix."""

import importlib.util
import os
import re
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_lite_profiles import lists
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.test_srd_lite_exploration_dispel import fiction, light
from tests.test_srd_lite_exploration_objects import invoke
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls

from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._expression_parser import parse_expression

try:
    from scripts import srd_lite_dispel_decision_widgets as widgets
except ImportError:
    spec = importlib.util.spec_from_file_location(
        "scripts.srd_lite_dispel_decision_widgets",
        Path(__file__).with_name("srd_lite_dispel_decision_widgets.py"),
    )
    widgets = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = widgets
    spec.loader.exec_module(widgets)


@pytest.fixture(scope="module")
def owners():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    assert "check_srd_lite_exploration_dispel_light" in data["tools"]
    already = any(
        row.get("set", {}).get("field") == widgets.EQUATION
        for row in data["tools"][widgets.RESOLVER]["effects"]
    )
    if not already:
        widgets.bind_dispel_decision_widgets(data)
    closure, pending = (
        {},
        [
            "cast_srd_recovery",
            "cast_spell_illumination_spell",
            "record_srd_lite_exploration_object",
        ],
    )
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = deepcopy(data["tools"][name])
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset), data


def _recovery(runtime, value, **changes):
    return invoke(
        runtime,
        value,
        "cast_srd_recovery",
        {
            "caster_state": "hero",
            "target_state": "guard",
            "spell": "dispel_magic",
            "slot_level": 3,
            "casting_modifier": 3,
            "casting_ability": "wisdom",
            "save_dc": 13,
            "concentration_save_modifier": 0,
            "can_touch": False,
            "target_distance": 20,
            **changes,
        },
        allow_rng=True,
    )


def _project(context):
    declaration = {
        "version": 1,
        "title": "Dispel decisions",
        "summary": [],
        "details": {"path": "context._spell_detail_rows"},
    }
    card = project_result_card(declaration, parameters={}, public_context=context)
    assert card is not None, "Real scalar decision rows must satisfy the actual card projector"
    return card


def test_selected_light_automatic_receipt_projects_without_a_fake_roll(owners):
    runtime, _ = owners
    value = fiction()
    source = light(runtime, value)
    result = _recovery(
        runtime,
        value,
        recovery_choice="illumination_object",
        recovery_source_id="cup",
        origin_distance=20,
        origin_has_clear_path=True,
    )
    assert result.success, result.error
    decision = result.context["_dispel_checks"][0]
    assert decision["roll"] is None and decision["equation"] is None and decision["ended"] is True
    assert "automatically" in decision["feedback"] and "no roll" in decision["feedback"]
    card = _project(result.context)
    assert any(
        row["label"] == "cup" and row["value"] == decision["feedback"] for row in card["details"]
    )
    assert not any("equation" in row for row in card["details"])
    assert all(
        row.get("instance") != source["instance"]
        for row in value[NAMESPACE + ".state"]["turn_effects"]
    )


def test_real_higher_source_check_projects_actual_dice_and_dynamic_penalty(owners):
    runtime, _ = owners
    value = fiction()
    value[NAMESPACE + ".entities"]["hero"].update(exhaustion_level=1)
    value[NAMESPACE + ".state"]["turn_effects"] = [
        {
            "spell": "darkvision",
            "label": "darkvision",
            "caster": "hero",
            "target": "guard",
            "kind": "timed_senses",
            "owner": "__time_only__",
            "phase": "time",
            "turn": 1000000000,
            "started_tick": 0,
            "expires_tick": 9600,
            "concentration": False,
            "magnitude": 0,
            "dice_count": 0,
            "damage_die": "",
            "damage_type": "",
            "spell_level": 4,
        }
    ]
    result = _recovery(runtime, value)
    assert result.success, result.error
    decision = result.context["_dispel_checks"][0]
    roll, equation = decision["roll"], decision["equation"]
    assert roll.sides == 20 and len(roll.results) == 1
    assert equation["rolls"] == list(roll.results) or equation["rolls"] == roll.results
    assert equation["roll"] == roll.total and equation["modifier"] == roll.modifier - 2
    assert equation["total"] == roll.total + equation["modifier"]
    assert decision["ended"] is (equation["total"] >= 14)
    card = _project(result.context)
    projected = [row for row in card["details"] if "equation" in row]
    assert len(projected) == 1 and projected[0]["equation"]["total"] == equation["total"]
    assert "DC 14" in projected[0]["value"]
    assert len(value[NAMESPACE + ".state"]["turn_effects"]) == int(not decision["ended"])


def test_compose_once_and_actual_raw_row_drift_guard(owners):
    _, data = owners
    with pytest.raises(ValueError, match="already bound"):
        widgets.bind_dispel_decision_widgets(deepcopy(data))
    assert not any(
        row.get("list_push", {}).get("value")
        == {"label": "'Dispel source decisions'", "value": "_dispel_checks"}
        for group in lists(data["tools"]["cast_srd_recovery"]["effects"])
        for row in group
    )


def test_new_expressions_and_loop_collections_fit_native_limits(owners):
    _, data = owners
    selected = [
        row
        for name, owner in data["tools"].items()
        if name == widgets.RESOLVER
        or name == "cast_srd_recovery"
        or any(widgets.EQUATION in str(row) for row in owner["effects"])
        for row in owner["effects"]
    ]
    for group in lists(selected):
        for row in group:
            if "for_each" in row and row["for_each"]["as"].startswith("_visual_dispel"):
                assert re.fullmatch(r"_[A-Za-z0-9_]+", row["for_each"]["collection"])
            condition = row.get("condition")
            if condition and "_visual_dispel" in condition:
                assert len(condition) <= 500
                parse_expression(condition)
