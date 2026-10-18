"""Project actual Dispel source decisions as readable scalar rows and recorded dice."""

from copy import deepcopy

from .srd_control_behaviors import _call, _each, _push, _set, _when
from .srd_lite_profiles import lists

RESOLVER = "resolve_srd_dispel_source_check"
DESCRIPTION = "_visual_dispel_description"
EQUATION = "_visual_dispel_equation"


def _caption(message):
    return _call(
        "format_srd_information_projection", {"message": message}, ["_info_projection_message"]
    )


def _resolver_feedback(owner):
    """Save the existing check's final adjustment, including shared dynamic modifiers."""
    calls = [
        row for group in lists(owner["effects"]) for row in group if row.get("call") == "d20_test"
    ]
    if len(calls) != 1 or calls[0]["export_context"].get("_d20_test") != "_d20_test":
        raise ValueError("Preserve the sole genuine Dispel casting-ability check")
    calls[0]["export_context"].update(
        _combined_modifier="_combined_modifier", _adjusted_total="_adjusted_total"
    )
    owner["effects"].extend(
        [
            _set("_visual_dispel_outcome", "'retained'"),
            _when("_dispel_succeeded == true", [_set("_visual_dispel_outcome", "'ended'")]),
            _set(EQUATION, None),
            _when(
                "_dispel_roll == null",
                [
                    _caption(
                        "Level {source_level} source · {_visual_dispel_outcome} "
                        "automatically · no roll"
                    ),
                    _set(DESCRIPTION, "_info_projection_message"),
                ],
                [
                    _caption(
                        "Level {source_level} source · DC {_dispel_dc} · {_visual_dispel_outcome}"
                    ),
                    _set(DESCRIPTION, "_info_projection_message"),
                    _set(
                        EQUATION,
                        {
                            "die_sides": "_dispel_roll.sides",
                            "rolls": "_dispel_roll.results",
                            "roll": "_dispel_roll.total",
                            "modifier": "_combined_modifier",
                            "total": "_adjusted_total",
                        },
                    ),
                ],
            ),
        ]
    )


def _receipt_feedback(owner):
    """Annotate actual receipts in their existing scope, preserving literal automatic nulls."""
    calls = [
        row for group in lists(owner["effects"]) for row in group if row.get("call") == RESOLVER
    ]
    if not calls:
        return 0
    receipts = [
        row
        for group in lists(owner["effects"])
        for row in group
        if isinstance(row.get("list_push", row.get("set", {})).get("value"), dict)
        and {"ended", "roll"}.issubset(row.get("list_push", row.get("set", {}))["value"])
    ]
    if not receipts:
        return 0
    for row in calls:
        row["export_context"].update({DESCRIPTION: DESCRIPTION, EQUATION: EQUATION})
    changed = 0
    for group in list(lists(owner["effects"])):
        for index in range(len(group) - 1, -1, -1):
            row = group[index]
            write = row.get("list_push", row.get("set", {}))
            value = write.get("value")
            if not isinstance(value, dict) or "ended" not in value or "roll" not in value:
                continue
            if value["roll"] not in (None, "_dispel_roll"):
                raise ValueError("Preserve actual Dispel receipt roll identity")
            value["feedback"] = DESCRIPTION
            if value["roll"] is None:
                value["equation"] = None
            else:
                value["equation"] = EQUATION
                automatic = deepcopy(row)
                automatic_write = automatic.get("list_push", automatic.get("set", {}))
                automatic_write["value"].update(roll=None, equation=None)
                group[index] = _when("_dispel_roll == null", [automatic], [row])
            changed += 1
    if not changed:
        raise ValueError("Every annotated native Dispel check must have its owning receipt")
    return changed


def _scalar_decisions():
    """Project source identity/outcome; dictionaries stay in the original private receipt."""
    return [
        _each(
            "_dispel_checks",
            "_visual_dispel_decision",
            [
                _set("_visual_dispel_label", "'Dispel source'"),
                _when(
                    "_visual_dispel_decision.spell != null",
                    [_set("_visual_dispel_label", "_visual_dispel_decision.spell")],
                ),
                _when(
                    "_visual_dispel_decision.instance != null",
                    [_set("_visual_dispel_label", "_visual_dispel_decision.instance")],
                ),
                _when(
                    "_visual_dispel_decision.source != null",
                    [_set("_visual_dispel_label", "_visual_dispel_decision.source")],
                ),
                _set("_visual_dispel_value", "'No active source'"),
                _when(
                    "_visual_dispel_decision.feedback != null",
                    [_set("_visual_dispel_value", "_visual_dispel_decision.feedback")],
                ),
                _when(
                    "_visual_dispel_decision.equation == null",
                    [
                        _push(
                            "_spell_detail_rows",
                            {"label": "_visual_dispel_label", "value": "_visual_dispel_value"},
                        ),
                    ],
                    [
                        _push(
                            "_spell_detail_rows",
                            {
                                "label": "_visual_dispel_label",
                                "value": "_visual_dispel_value",
                                "equation": "_visual_dispel_decision.equation",
                            },
                        ),
                    ],
                ),
            ],
        )
    ]


def _suppress_legacy_target_detail(owner):
    """Keep native target summaries; scalar source rows replace only Dispel's raw detail."""
    raw_checks = [
        row for group in lists(owner["effects"]) for row in group
        if row.get("call") == "record_srd_spell_target_result"
        and row.get("params", {}).get("detail") == "Actual source checks: {_dispel_checks}"
    ]
    raw_detail = {"list_push": {"list": "_spell_detail_rows", "value": "_spell_target_detail"}}
    sites = [
        (group, index) for group in lists(owner["effects"])
        for index, row in enumerate(group) if row == raw_detail
    ]
    if len(raw_checks) != 1 or len(sites) != 1:
        raise ValueError("Preserve the sole native Recovery raw source-check detail seam")
    group, index = sites[0]
    group[index] = _when("spell != 'dispel_magic'", [group[index]])


def bind_dispel_decision_widgets(runtime):
    """Bind after exploration Dispel's null repair and before final closure/pruning."""
    tools = runtime["tools"]
    resolver = tools[RESOLVER]
    if any(row.get("set", {}).get("field") == EQUATION for row in resolver["effects"]):
        raise ValueError("Dispel decision widgets already bound")
    recovery = tools["cast_srd_recovery"]
    raw = {
        "list_push": {
            "list": "_spell_detail_rows",
            "value": {"label": "'Dispel source decisions'", "value": "_dispel_checks"},
        }
    }
    sites = [
        (group, index)
        for group in lists(recovery["effects"])
        for index, row in enumerate(group)
        if row == raw
    ]
    if len(sites) != 1:
        raise ValueError("Preserve the sole actual srd65 Dispel decision card seam")
    _resolver_feedback(resolver)
    counts = {name: _receipt_feedback(owner) for name, owner in tools.items() if name != RESOLVER}
    if not counts.get("dispel_srd_target_sources") or not counts.get(
        "dispel_srd65_illumination_selection"
    ):
        raise ValueError("Retain both actual ordinary and selected-illumination receipts")
    group, index = sites[0]
    group[index : index + 1] = _scalar_decisions()
    _suppress_legacy_target_detail(recovery)
    return tuple(
        sorted([RESOLVER, "cast_srd_recovery", *[name for name, count in counts.items() if count]])
    )
