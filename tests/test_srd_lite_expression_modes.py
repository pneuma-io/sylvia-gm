"""Pure-source guards for the executor's expression, template and literal contracts."""
from copy import deepcopy

import pytest

from scripts.srd_lite_attacks import _effect_names, _expression_names
from sylvia_gm.runtime._effect_executor import _resolve_value
from sylvia_gm.runtime._template_resolver import resolve_params
from sylvia_gm.runtime._types import ExecutionContext


ALIASES = {"target": "_actual_target", "targets": "_actual_targets",
           "_target_id": "_actual_target_id", "target_distance": "_actual_distance"}


@pytest.mark.parametrize("message", [
    "Declare every projectile and include its primary target.",
    "Every projectile target needs its actual range, sight and clear path.",
    "Choose one actual cover category per target.",
    "target",
    "{target.name}",
])
def test_staged_requirement_preserves_actual_human_error(message):
    effect = {"require": {"condition": "target != null and _target_id in targets",
                          "error": message}}
    original = deepcopy(effect)
    assert _effect_names([effect], ALIASES) == [{
        "require": {"condition": "_actual_target != null and _actual_target_id in _actual_targets",
                    "error": message},
    }]
    assert effect == original


def test_call_params_rewrite_only_template_references_even_inside_human_quotes():
    effect = {"call": "record_srd_result_text", "params": {
        "target": "{_target_id}",
        "text": "The 'target' is '{target.name}' ({_target_id}); targets {targets}.",
        "literal": "target",
        "record": {"target": "{target.name}"},
        "rows": ["{targets}"],
    }, "export_context": {"_target_id": "_target_id", "_saved": "_target_id"}}
    rewritten = _effect_names([effect], ALIASES)[0]
    assert rewritten["call"] == effect["call"]
    assert rewritten["export_context"] == effect["export_context"]
    assert set(rewritten["params"]) == set(effect["params"])
    assert rewritten["params"]["target"] == "{_actual_target_id}"
    assert rewritten["params"]["text"] == (
        "The 'target' is '{_actual_target.name}' ({_actual_target_id}); targets {_actual_targets}."
    )
    context = ExecutionContext(vars={"_actual_target": {"name": "Guard"},
                                     "_actual_target_id": "guard", "_actual_targets": ["guard"]})
    assert resolve_params(rewritten["params"], context) == {
        "target": "guard",
        "text": "The 'target' is 'Guard' (guard); targets guard.",
        "literal": "target",
        "record": {"target": "{target.name}"},
        "rows": ["{targets}"],
    }


def test_evaluated_payloads_with_effect_named_keys_are_data_and_keep_literal_prose():
    record = {
        "call": "target",
        "params": {"target": "target", "targets": ["targets", "'targets'"]},
        "error": "target_distance",
        "literal_error": "Declare every target before casting.",
        "quoted_key": "'target'",
        "property": "target.target",
    }
    effects = [{"set": {"field": "_record", "value": record}},
               {"list_push": {"list": "_records", "value": record}}]
    rewritten = _effect_names(effects, ALIASES)
    payload = rewritten[0]["set"]["value"]
    assert payload == {
        "call": "_actual_target",
        "params": {"target": "_actual_target", "targets": ["_actual_targets", "'targets'"]},
        "error": "_actual_distance",
        "literal_error": "Declare every target before casting.",
        "quoted_key": "'target'",
        "property": "_actual_target.target",
    }
    assert rewritten[1]["list_push"]["value"] == payload
    context = ExecutionContext(vars={"_actual_target": {"target": "persisted"},
                                     "_actual_targets": ["guard"], "_actual_distance": 12})
    assert _resolve_value(payload, context) == {
        "call": {"target": "persisted"},
        "params": {"target": {"target": "persisted"}, "targets": [["guard"], "targets"]},
        "error": 12,
        "literal_error": "Declare every target before casting.",
        "quoted_key": "target",
        "property": "persisted",
    }


def test_expression_roots_and_indexes_rewrite_without_changing_persisted_members():
    expression = "target.target == targets[_target_id].target and target_distance > 0"
    assert _expression_names(expression, ALIASES) == (
        "_actual_target.target == _actual_targets[_actual_target_id].target and _actual_distance > 0"
    )
    assert _expression_names("target['targets']", ALIASES) == "_actual_target['targets']"
    assert _expression_names("'target targets target_distance'", ALIASES) == "'target targets target_distance'"
    assert _expression_names('"target targets target_distance"', ALIASES) == '"target targets target_distance"'
    assert _expression_names("The target's next turn.", ALIASES) == "The target's next turn."


def test_nested_call_and_conditional_use_their_effect_contracts():
    effect = {"condition": {"condition": "target != null", "on_true": [
        {"call": {"name": "target", "params": {"target": "'{target.name}' target"}},
         "export_context": {"_result": "_target_id"}},
    ]}}
    assert _effect_names([effect], ALIASES) == [{"condition": {
        "condition": "_actual_target != null", "on_true": [
            {"call": {"name": "target", "params": {"target": "'{_actual_target.name}' target"}},
             "export_context": {"_result": "_target_id"}},
        ],
    }}]


def test_destinations_and_nested_effects_keep_saved_paths_and_export_sources():
    effects = [
        {"set": {"field": "_target_id", "value": "target.target"}},
        {"set": {"field": "target.hp", "value": "target.hp - 1"}},
        {"set": {"field": "lasting_state.pending.parameters.target", "value": "target"}},
        {"set": {"field": "lasting_state.pending.{_target_id}.targets", "value": "targets"}},
        {"condition": "target != null", "on_true": [
            {"for_each": {"collection": "targets", "as": "target", "do": [
                {"list_push": {"list": "targets", "value": "target"}, "store_as": "_target_id"},
            ]}},
        ], "on_false": [{"require": {"condition": "target == null", "error": "No target."}}]},
    ]
    assert _effect_names(effects, ALIASES) == [
        {"set": {"field": "_actual_target_id", "value": "_actual_target.target"}},
        {"set": {"field": "_actual_target.hp", "value": "_actual_target.hp - 1"}},
        {"set": {"field": "lasting_state.pending.parameters.target", "value": "_actual_target"}},
        {"set": {"field": "lasting_state.pending.{_actual_target_id}.targets", "value": "_actual_targets"}},
        {"condition": "_actual_target != null", "on_true": [
            {"for_each": {"collection": "_actual_targets", "as": "_actual_target", "do": [
                {"list_push": {"list": "_actual_targets", "value": "_actual_target"},
                 "store_as": "_actual_target_id"},
            ]}},
        ], "on_false": [{"require": {"condition": "_actual_target == null", "error": "No target."}}]},
    ]
