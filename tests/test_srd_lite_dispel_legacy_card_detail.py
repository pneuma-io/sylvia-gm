"""Reuse saved public actions; execute only the final native card row, never paid casts."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import srd_lite_dispel_decision_widgets as widgets
from scripts.srd_control_behaviors import _param, _set, _tool
from scripts.srd_lite_profiles import lists
from tests.test_srd_lite_exploration_objects import invoke

from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.runtime import RulesetRuntime

WORK = Path("D:/Codex/2026-10-09/sylvia-main-01a11b3b/integration-01a120e9")
RECORDED = WORK / "parallel-thaumaturgy-dispel-visual-draft-owner991/native-run"
RAW_DETAIL = {"list_push": {"list": "_spell_detail_rows", "value": "_spell_target_detail"}}


@pytest.fixture(scope="module")
def native_recovery():
    path = Path(os.environ.get(
        "SRD_LITE_DISPEL_WIDGET_OWNERS", str(WORK / "selected-exploration-dispel-owners-1081.json")
    ))
    return json.loads(path.read_text())["tools"]["cast_srd_recovery"]


@pytest.fixture(scope="module")
def feedback_only(native_recovery):
    owner = deepcopy(native_recovery)
    widgets._suppress_legacy_target_detail(owner)
    seam = [
        row for group in lists(owner["effects"]) for row in group
        if row.get("condition") == "spell != 'dispel_magic'" and row.get("on_true") == [RAW_DETAIL]
    ]
    assert len(seam) == 1
    tool = _tool(
        "Project the actual final native target-detail row using already-recorded scalar feedback.",
        [_param("spell"), _param("recorded_scalar_rows", "array", items={"type": "object"}),
         _param("_spell_target_detail", "object", required=False, default={})],
        [_set("_spell_detail_rows", "recorded_scalar_rows"), deepcopy(seam[0])],
    )
    assert not any("call" in row for group in lists(tool["effects"]) for row in group)
    return RulesetRuntime(ruleset_dict={"tools": {"feedback_only": tool}})


def test_exact_card_row_change_preserves_all_original_native_consumers(native_recovery):
    owner = deepcopy(native_recovery)
    widgets._suppress_legacy_target_detail(owner)
    sites = [
        (group, index) for group in lists(owner["effects"])
        for index, row in enumerate(group)
        if row.get("condition") == "spell != 'dispel_magic'" and row.get("on_true") == [RAW_DETAIL]
    ]
    assert len(sites) == 1
    group, index = sites[0]
    group[index] = deepcopy(RAW_DETAIL)
    assert owner == native_recovery


def test_legacy_producer_drift_fails_clearly(native_recovery):
    owner = deepcopy(native_recovery)
    producer = next(
        row for group in lists(owner["effects"]) for row in group
        if row.get("params", {}).get("detail") == "Actual source checks: {_dispel_checks}"
    )
    producer["params"]["detail"] = "Changed producer"
    with pytest.raises(ValueError, match="raw source-check detail seam"):
        widgets._suppress_legacy_target_detail(owner)


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _all_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_strings(child)


@pytest.mark.parametrize(("case", "source"), [
    ("light-automatic", "visual-torch"), ("aid-real-check", "aid"),
])
def test_saved_native_automatic_and_d20_cards_have_scalar_details_only(feedback_only, case, source):
    path = RECORDED / ("thaumaturgy-dispel-owner991-dispel-" + case + "-1005.json")
    raw_checkpoint = path.read_bytes()
    checkpoint = json.loads(raw_checkpoint)
    result = checkpoint["receipt"]["result"]
    context, original_card = result["context"], result["result_card"]
    scalar_rows = [row for row in context["_spell_detail_rows"] if row["label"] == source]
    assert len(scalar_rows) == 1
    legacy = [row for row in context["_spell_detail_rows"] if row["label"] == "guard"]
    if case == "aid-real-check":
        assert len(legacy) == 1 and "RollResult(" in legacy[0]["value"]
    else:
        assert not legacy
    callback = invoke(feedback_only, {}, "feedback_only", {
        "spell": "dispel_magic", "recorded_scalar_rows": deepcopy(scalar_rows),
        "_spell_target_detail": deepcopy(legacy[0]) if legacy else {},
    })
    assert callback.success, callback.error
    declaration = deepcopy(original_card)
    declaration["details"] = {"path": "context._spell_detail_rows"}
    projected = project_result_card(
        declaration, parameters=checkpoint["parameters"], public_context=callback.context,
    )
    assert projected is not None
    expected_rows = [{**row, "emphasis": row.get("emphasis", False)} for row in scalar_rows]
    assert projected["details"] == expected_rows
    assert {key: value for key, value in projected.items() if key != "details"} == {
        key: value for key, value in original_card.items() if key != "details"
    }
    assert all(isinstance(row["label"], str) and isinstance(row["value"], str)
               for row in projected["details"])
    for text in _all_strings(projected):
        assert "Actual source checks:" not in text
        assert "RollResult(" not in text
        assert "{'spell':" not in text
    if case == "light-automatic":
        assert "automatically" in scalar_rows[0]["value"] and "no roll" in scalar_rows[0]["value"]
        assert "equation" not in scalar_rows[0]
    else:
        equation = scalar_rows[0]["equation"]
        assert "Level 4" in scalar_rows[0]["value"] and "DC 14" in scalar_rows[0]["value"]
        assert equation == {"die_sides": 20, "rolls": [2], "roll": 2, "modifier": 4, "total": 6}
    assert path.read_bytes() == raw_checkpoint


def test_other_recovery_target_details_remain_native(feedback_only):
    native_detail = {"label": "guard", "value": "Recovered; selection none; Hit Points 12/20"}
    callback = invoke(feedback_only, {}, "feedback_only", {
        "spell": "greater_restoration", "recorded_scalar_rows": [],
        "_spell_target_detail": native_detail,
    })
    assert callback.success, callback.error
    assert callback.context["_spell_detail_rows"] == [native_detail]
