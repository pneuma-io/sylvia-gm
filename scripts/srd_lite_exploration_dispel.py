"""Keep exact native Dispel sources while using current fictional range outside combat."""

from copy import deepcopy

from .srd_control_behaviors import _param, _require, _set, _when
from .srd_lite_exploration_objects import LOAD, _load
from .srd_lite_profiles import lists

RECIPIENT = "qualify_srd65_dispel_recipient"
SELECTED = "dispel_srd65_illumination_selection"
LIGHT_CHECK = "check_srd_lite_exploration_dispel_light"
RECOVERY = "cast_srd_recovery"
COMBAT = "combat.encounter_active == true"
ROOTS = ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action")
ORIGIN_FACTS = ("origin_distance", "origin_has_clear_path")


def _optional(name, kind):
    """Absence remains absence; omitted facts must never become a zero-distance success."""
    return _param(name, kind, required=False)


def _add_parameters(owner, parameters):
    names = {row["name"] for row in owner["parameters"]}
    for row in parameters:
        if row["name"] in names:
            raise ValueError("Dispel exploration fact already owned: " + row["name"])
        owner["parameters"].append(row)


def _actual_roll_receipts(owner):
    """Keep automatic Dispel decisions null instead of leaking an unresolved alias."""
    changed = False
    for group in list(lists(owner["effects"])):
        for index, row in enumerate(group):
            value = row.get("list_push", {}).get("value")
            if not isinstance(value, dict) or value.get("roll") != "_dispel_roll":
                continue
            automatic = deepcopy(row)
            automatic["list_push"]["value"]["roll"] = None
            group[index] = _when("_dispel_roll == null", [automatic], [row])
            changed = True
    return changed


def _light_check(original):
    """Reuse native lifetime/concentration guards without consulting a saved map anchor."""
    result = deepcopy(original)
    anchors = [
        row
        for row in result["effects"]
        if row.get("condition") == "_illumination_active == true and record.source != ''"
    ]
    if len(anchors) != 1:
        raise ValueError("Preserve the actual illumination source-anchor boundary")
    anchors[0]["on_true"] = [
        _require(
            "record.spell == 'light' and record.kind == 'illumination_spell' and "
            "record.mode == 'object' and record.caster == _caster_state_id",
            "Select the actual retained Light source and its caster.",
        ),
        _load("{record.source}", destination="_srd_lite_dispel_object"),
        _require(
            "_srd_lite_dispel_object.metadata.is_object == true",
            "Light must still belong to the actual admitted object.",
        ),
        _set("_illumination_covered", "_srd_lite_dispel_object.metadata.opaque_cover == true"),
    ]
    for row in result["effects"]:
        if row.get("set", {}).get("field") in {"_illumination_origin", "_illumination_plane"}:
            row["set"]["value"] = None
    result["description"] = (
        "Check the exact saved Light source clock/caster and retained object identity; "
        "current fictional range is qualified by the Dispel owner."
    )
    return result


def _adapt_selected(owner):
    """Gate only geometry; exact native selection, source checks and removal stay shared."""
    rows = owner["effects"]
    first_selection = next(
        index
        for index, row in enumerate(rows)
        if row.get("set", {}).get("field") == "_srd65_selected_rows"
    )
    if first_selection != 4 or rows[0].get("call") != "srd_information_scene_entity":
        raise ValueError("Preserve the actual four-effect Dispel caster-origin prefix")
    rows[:first_selection] = [_when(COMBAT, deepcopy(rows[:first_selection]))]
    objects = [row for row in rows if row.get("condition") == "choice == 'illumination_object'"]
    if len(objects) != 1:
        raise ValueError("Preserve the selected-object Dispel join")
    native_object = deepcopy(objects[0]["on_true"])
    objects[0]["on_true"] = [
        _when(
            COMBAT,
            native_object,
            [
                _load("{source_id}", destination="_srd_lite_dispel_object"),
                _require(
                    "_srd_lite_dispel_object.metadata.is_object == true",
                    "Choose an actual retained Dispel object.",
                ),
            ],
        )
    ]
    checks = [
        row
        for group in lists(rows)
        for row in group
        if row.get("call") == "check_srd_illumination_record"
    ]
    if len(checks) != 1:
        raise ValueError("Preserve one native selected illumination record check")
    native_check = deepcopy(checks[0])
    fiction_check = deepcopy(native_check)
    fiction_check["call"] = LIGHT_CHECK
    checks[0].clear()
    checks[0].update(
        _when(
            COMBAT,
            [native_check],
            [
                _require(
                    "_srd65_lamp.spell == 'light' and _srd65_lamp.source != ''",
                    "Outside combat, select an actual retained object Light source.",
                ),
                fiction_check,
            ],
        )
    )
    path_start = next(
        index
        for index, row in enumerate(rows)
        if row.get("require", {}).get("condition") == "_srd65_selected_plane == _srd65_dispel_plane"
    )
    path_end = next(
        index
        for index, row in enumerate(rows)
        if row.get("set", {}).get("field") == "_srd65_dispel_checks"
    )
    if path_end - path_start != 5:
        raise ValueError("Preserve the actual Dispel range/plane/barrier path")
    rows[path_start:path_end] = [
        _when(
            COMBAT,
            deepcopy(rows[path_start:path_end]),
            [
                _require(
                    "origin_distance != null and origin_distance >= 0 and origin_distance <= 120",
                    "Establish the selected Light source's current same-plane range "
                    "within 120 feet.",
                ),
                _require(
                    "origin_has_clear_path == true",
                    "Establish the current clear shared-plane path to the selected Light source.",
                ),
                _require(
                    "len(_srd65_selected_rows) > 0",
                    "The selected object must have an actual active retained Light source.",
                ),
            ],
        )
    ]


def bind_exploration_dispel(runtime):
    """Bind after exploration objects and all recipient projections, before final pruning."""
    tools = runtime["tools"]
    if LIGHT_CHECK in tools:
        raise ValueError("Dispel exploration already bound")
    if LOAD not in tools:
        raise ValueError("Dispel exploration needs the retained object owner")
    recipient, selected, recovery = (tools[name] for name in (RECIPIENT, SELECTED, RECOVERY))
    native_recipient = deepcopy(recipient["effects"])
    _add_parameters(
        recipient,
        [
            _optional("target_distance", "number"),
            _param(
                "cover",
                "enum",
                required=False,
                enum_values=["none", "half", "three_quarters", "total"],
            ),
        ],
    )
    recipient["effects"] = [
        _when(
            COMBAT,
            native_recipient,
            [
                _require(
                    "target_distance != null and target_distance >= 0 and target_distance <= 120",
                    "Establish the ordinary Dispel recipient's current same-plane range "
                    "within 120 feet.",
                ),
                _require(
                    "cover != null and cover != 'total'",
                    "A solid barrier must not block the current ordinary Dispel path.",
                ),
            ],
        )
    ]
    origin_parameters = [
        _optional("origin_distance", "number"),
        _optional("origin_has_clear_path", "boolean"),
    ]
    _add_parameters(selected, deepcopy(origin_parameters))
    _add_parameters(recovery, deepcopy(origin_parameters))
    tools[LIGHT_CHECK] = _light_check(tools["check_srd_illumination_record"])
    _adapt_selected(selected)
    changed = {RECIPIENT, SELECTED, RECOVERY, LIGHT_CHECK}
    for name in (*ROOTS, RECOVERY):
        owner = tools[name]
        calls = [row for group in lists(owner["effects"]) for row in group if "call" in row]
        for row in calls:
            if row["call"] == RECIPIENT:
                # These preflight calls precede projected-local assignment. Combat
                # ignores supplied facts; outside combat qualifies the original facts.
                row["params"].update(target_distance="{target_distance}", cover="{cover}")
                changed.add(name)
            if row["call"] in (SELECTED, RECOVERY):
                row["params"].update({fact: "{" + fact + "}" for fact in ORIGIN_FACTS})
                changed.add(name)
    for name in ROOTS:
        for fact in ORIGIN_FACTS:
            if fact not in {row["name"] for row in tools[name]["parameters"]}:
                raise ValueError("Preserve the existing original Dispel origin fact: " + name)
    for name, owner in tools.items():
        if _actual_roll_receipts(owner):
            changed.add(name)
    return tuple(sorted(changed))
