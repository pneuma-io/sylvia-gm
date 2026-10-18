"""Focused card/source checks; no package load, casts, save data or GUI."""

from copy import deepcopy
from random import Random

import pytest
from scripts import srd_lite_followup_roll_widgets as widgets
from scripts import srd_lite_retained_spell_actions as cards

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget


@pytest.fixture
def runtime():
    """Use the actual retained card generator and the five reviewed effect seams."""
    provider = {
        "effects": [
            {
                "condition": "_attack_outcome != 'miss'",
                "on_true": [
                    {
                        "dice": {"expression": "{damage_expression}"},
                        "store_as": "_damage_roll",
                        "condition": "damage_expression != 'fixed-1'",
                    },
                    {
                        "set": {
                            "field": "_damage_roll",
                            "value": {
                                "results": "_damage_roll.results",
                                "total": "_ray_raw_damage",
                            },
                        }
                    },
                ],
            }
        ]
    }
    tools = {"resolve_adjudicated_creature_attack": provider}
    for name in widgets.FORM_OWNERS:
        owner = {
            "effects": [
                {
                    "call": "resolve_adjudicated_creature_attack",
                    "params": {
                        "damage_expression": "{_form_attack.damage_expression}",
                        "damage_modifier": 3,
                    },
                    "export_context": {
                        "_attack_roll": "_attack_roll",
                        "_attack_total": "_attack_adjusted_total",
                        "_attack_outcome": "_attack_outcome",
                        "_damage_roll": "_damage_roll",
                        "_effective_damage": "_damage_applied",
                        "_hp_lost": "_damage_hp_lost",
                        "_form_extra_damage_roll": "_form_extra_damage_roll",
                    },
                }
            ]
        }
        cards._bind_attack_card(name, owner)
        tools[name] = owner
    tools["resolve_srd_lite_daggers_event"] = {
        "effects": [
            {
                "dice": {"expression": "{_lite_daggers_expression}"},
                "store_as": "_lite_daggers_roll",
            },
            {
                "call": "apply_srd_qualifying_damage",
                "params": {"amount": "{_lite_daggers_roll.total}"},
            },
            {"list_push": {"list": "_lite_daggers_detail_rows", "value": "_spell_target_detail"}},
            {"set": {"field": "_lite_daggers_applied", "value": "_effective_damage"}},
        ]
    }
    tools["end_srd_control_behavior_turn"] = {
        "effects": [
            {
                "condition": "_control_end_save == true",
                "on_true": [
                    {
                        "call": "resolve_srd_control_behavior_save",
                        "params": {"save_dc": "{_control_source.save_dc}"},
                        "export_context": {
                            "_control_save_roll": "_control_save_roll",
                            "_control_save_outcome": "_control_save_outcome",
                        },
                    },
                    {
                        "list_push": {
                            "list": "_control_end_rows",
                            "value": {
                                "label": "_control_source.spell_id",
                                "value": "_control_save_detail",
                            },
                        }
                    },
                    {
                        "call": "retire_srd_timed_condition_source",
                        "condition": "_control_save_outcome == 'success'",
                    },
                ],
            }
        ]
    }
    return {"tools": tools}


@pytest.mark.parametrize("name", widgets.FORM_OWNERS)
def test_form_card_shows_all_attack_faces_and_original_damage(runtime, name):
    widgets.bind_followup_roll_widgets(runtime)
    raw = {"count": 2, "sides": 6, "results": [4, 6], "total": 10, "modifier": 0}
    context = {
        "_actor_state_id": "hero",
        "_target_state_id": "guard",
        "_attack_roll": {"results": [3, 18], "total": 18},
        "_lite_form_attack_modifier": 9,
        "_attack_total": 27,
        "_attack_outcome": "hit",
        "_effective_damage": 3,
        "_hp_lost": 3,
        "_damage_roll": {**raw, "total": 5},
        widgets.FORM_ROLL: raw,
        "_form_extra_damage_roll": {"sides": 8, "results": [2, 7], "total": 9},
    }
    card = project_result_card(
        runtime["tools"][name]["metadata"]["result_card"], parameters={}, public_context=context
    )
    assert card is not None
    assert card["equation"]["rolls"] == [3, 18]
    assert card["equation"]["roll"] == 18 and card["equation"]["total"] == 27
    damage = next(row for row in card["summary"] if row["label"].startswith("Damage dice"))
    assert damage["equation"] == {
        "die_sides": 6,
        "rolls": [4, 6],
        "roll": 10,
        "modifier": 0,
        "total": 10,
    }
    assert next(row for row in card["summary"] if row["label"] == "Damage applied")["value"] == 3
    extra = [row for row in card["summary"] if row["label"].startswith("Additional damage")]
    assert len(extra) == (1 if name == "attack_srd_transformed_form" else 0)
    if extra:
        assert extra[0]["equation"]["rolls"] == [2, 7]


@pytest.mark.parametrize("name", widgets.FORM_OWNERS)
def test_miss_has_no_fabricated_damage_widget(runtime, name):
    widgets.bind_followup_roll_widgets(runtime)
    context = {
        "_actor_state_id": "hero",
        "_target_state_id": "guard",
        "_attack_roll": {"results": [2, 4], "total": 4},
        "_lite_form_attack_modifier": 2,
        "_attack_total": 6,
        "_attack_outcome": "miss",
        "_effective_damage": 0,
        "_damage_roll": None,
        "_hp_lost": 0,
        widgets.FORM_ROLL: None,
        "_form_extra_damage_roll": None,
    }
    card = project_result_card(
        runtime["tools"][name]["metadata"]["result_card"], parameters={}, public_context=context
    )
    assert card is not None and card["equation"]["rolls"] == [2, 4]
    assert not any("dice before" in row.get("label", "") for row in card["summary"])


def test_added_source_feedback_leaves_native_dice_damage_and_cost_calls_unchanged(runtime):
    original = deepcopy(runtime)
    widgets.bind_followup_roll_widgets(runtime)
    for name in runtime["tools"]:
        before = [
            row
            for rows in widgets._lists(original["tools"][name]["effects"])
            for row in rows
            if "dice" in row
            or row.get("call")
            in {
                "apply_srd_qualifying_damage",
                "resolve_adjudicated_creature_attack",
                "resolve_srd_control_behavior_save",
                "retire_srd_timed_condition_source",
            }
        ]
        after = [
            deepcopy(row)
            for rows in widgets._lists(runtime["tools"][name]["effects"])
            for row in rows
            if "dice" in row
            or row.get("call")
            in {
                "apply_srd_qualifying_damage",
                "resolve_adjudicated_creature_attack",
                "resolve_srd_control_behavior_save",
                "retire_srd_timed_condition_source",
            }
        ]
        for row in after:
            exports = row.get("export_context", {})
            exports.pop(widgets.FORM_ROLL, None)
            exports.pop("_visual_order_save_row", None)
        assert after == before
    provider = runtime["tools"]["resolve_adjudicated_creature_attack"]["effects"]
    branch = provider[1]["on_true"]
    assert branch[0].get("store_as") == "_damage_roll"
    assert branch[1]["on_true"][0]["set"]["field"] == widgets.FORM_ROLL
    assert branch[1]["on_true"][0]["set"]["value"] == {
        key: "_damage_roll." + key for key in ("count", "sides", "results", "total", "modifier")
    }
    assert branch[2]["set"]["field"] == "_damage_roll"


def test_cloud_receipt_gains_one_recorded_equation_without_replacing_text(runtime):
    widgets.bind_followup_roll_widgets(runtime)
    rows = runtime["tools"]["resolve_srd_lite_daggers_event"]["effects"]
    assert rows[2] == {
        "list_push": {"list": "_lite_daggers_detail_rows", "value": "_spell_target_detail"}
    }
    equation = rows[3]["list_push"]["value"]["equation"]
    assert (
        equation["rolls"] == "_lite_daggers_roll.results"
        and equation["total"] == "_lite_daggers_roll.total"
    )
    assert rows[4]["set"]["value"] == "_effective_damage"


def test_fear_sleep_repeat_forwards_the_existing_save_row_only(runtime):
    widgets.bind_followup_roll_widgets(runtime)
    branch = runtime["tools"]["end_srd_control_behavior_turn"]["effects"][0]["on_true"]
    assert branch[0]["export_context"]["_visual_order_save_row"] == "_visual_order_save_row"
    assert "_control_source.spell_id == 'fear'" in branch[2]["condition"]
    assert "_control_source.spell_id == 'sleep'" in branch[2]["condition"]
    assert branch[2]["on_true"][0]["list_push"]["value"] == "_visual_order_save_row"
    assert branch[3]["call"] == "retire_srd_timed_condition_source"


def test_drift_fails_without_partial_feedback_mutation(runtime):
    runtime["tools"]["resolve_srd_lite_daggers_event"]["effects"].pop(2)
    original = deepcopy(runtime)
    with pytest.raises(ValueError, match="Cloud recipient receipt"):
        widgets.bind_followup_roll_widgets(runtime)
    assert runtime == original


def test_actual_dsl_keeps_recorded_faces_after_adjusted_total_changes(runtime, monkeypatch):
    """Keep the genuine roll when the native seam replaces it with adjusted damage."""
    monkeypatch.setattr(dice, "_rng", Random(10))
    widgets.bind_followup_roll_widgets(runtime)
    effects = [
        {"set": {"field": "_attack_outcome", "value": "'hit'"}},
        {"set": {"field": "_ray_raw_damage", "value": 0}},
        *runtime["tools"]["resolve_adjudicated_creature_attack"]["effects"],
    ]
    parsed = RulesetRuntime(
        ruleset_dict={
            "tools": {
                "recorded_form_roll_probe": {
                    "description": "Exercise the recorded form damage feedback seam.",
                    "parameters": [
                        {"name": "actor", "type": "string", "resolve": "entity", "required": True},
                        {"name": "damage_expression", "type": "string", "required": True},
                    ],
                    "effects": effects,
                    "pre_conditions": [],
                    "metadata": {
                        "category": "internal",
                        "visible_to_player": False,
                        "costs_action": False,
                    },
                }
            }
        }
    )
    namespace = "test.followup"
    bridge = AuthorityMemoryStateBridge(
        namespace,
        {
            namespace + ".state": {},
            namespace + ".combat": {},
            namespace + ".entities": {
                "hero": {
                    "form_attack_in_progress": True,
                    "altered_natural_attack_in_progress": False,
                }
            },
        },
    )
    result = execute_tool(
        "recorded_form_roll_probe",
        {"actor": "hero", "damage_expression": "2d6"},
        parsed.tools,
        bridge,
        parsed.ruleset_config,
        parsed.definitions,
        combat_state=bridge.combat_state,
        budget=ExecutionBudget(),
    )
    assert result.success, result.error
    original = result.context[widgets.FORM_ROLL]
    adjusted = result.context["_damage_roll"]
    assert original is not adjusted
    assert original["count"] == 2 and original["sides"] == 6
    assert original["total"] == sum(original["results"]) > 0
    assert adjusted["total"] == 0 and original["modifier"] == 0
