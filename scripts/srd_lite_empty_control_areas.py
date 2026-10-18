"""Permit a paid empty Fear/Sleep area with genuine caster-owned concentration."""

from copy import deepcopy

from .srd_control_behaviors import _each, _push, _when
from .srd_lite_control_area_geometry import PAID, QUALIFY, ROOTS
from .srd_lite_mass_cure_geometry import _effect_lists
from .srd_timed_actor_defenses import _require, _set

START = "start_srd_condition_concentration"
REFRESH = "refresh_srd_concentration_source"
END = "end_srd_concentration_node"
QUERY_PREFIX = "retained_spell_query_"
EMPTY_CAST = (
    "combat.encounter_active == true and (spell == 'fear' or spell == 'sleep') and "
    "definitions.spells[spell].family == 'timed_control_behavior'"
)
EMPTY_SOURCE = (
    "source_state.concentration_effect_family == 'multi_target_timed_repeating_save_condition' "
    "and (source_state.concentration_spell_id == 'fear' or "
    "source_state.concentration_spell_id == 'sleep') and "
    "source_state.concentration_target_state_ids != null and "
    "len(source_state.concentration_target_state_ids) == 0 and "
    "source_state.concentration_target_state_id == _source_state_id and "
    "_target_state_id == _source_state_id"
)
OLD_EMPTY_GUARD = {
    "require": {
        "condition": "len(_control_area_targets) > 0",
        "error": "The retained control owner requires at least one actual affected creature.",
    }
}


def _allow_selected_empty(owner):
    """Relax only this resolved tool declaration, retaining every other target contract."""
    matches = [index for index, row in enumerate(owner["parameters"]) if row["name"] == "targets"]
    if len(matches) != 1:
        raise ValueError("Keep one control targets parameter.")
    index = matches[0]
    parameter = deepcopy(owner["parameters"][index])
    if parameter.get("min_items") != 1 or parameter.get("resolve") != "entity_list":
        raise ValueError("The retained nonempty control targets contract changed.")
    parameter["min_items"] = 0
    owner["parameters"][index] = parameter
    owner["effects"].insert(
        0,
        _require(
            "len(targets) > 0 or (" + EMPTY_CAST + ")",
            "An empty control area is supported only for Fear or Sleep in combat.",
        ),
    )


def _start(owner):
    """Reuse the existing actual-caster anchor without adding an affected recipient.

    Animate Objects and extended casting already use a real caster ownership
    anchor with an empty recipient list. Fear/Sleep use that same representation
    only when their qualified combat area contains no creatures.
    """
    _allow_selected_empty(owner)
    matches = [
        (rows, index)
        for rows in _effect_lists(owner["effects"])
        for index, row in enumerate(rows)
        if row.get("set", {}).get("field") == "caster_state.concentration_target_state_id"
    ]
    if len(matches) != 1:
        raise ValueError("Keep one condition concentration ownership anchor.")
    rows, index = matches[0]
    original = rows[index]
    if original["set"]["value"] != "targets[0]":
        raise ValueError("The existing first-recipient concentration anchor changed.")
    rows[index] = _when(
        "len(targets) > 0",
        [original],
        [_set("caster_state.concentration_target_state_id", "_caster_state_id")],
    )


def _refresh(owner):
    """Recognize the selected empty source's real owner while preserving invalidation rules."""
    matches = [
        (rows, index)
        for rows in _effect_lists(owner["effects"])
        for index, row in enumerate(rows)
        if row.get("set", {}).get("field") == "_source_concentration_target_invalid"
        and row["set"].get("value")
        == "source_state.concentration_target_state_ids == null or "
        "_target_state_id not in source_state.concentration_target_state_ids"
    ]
    if len(matches) != 1:
        raise ValueError("Keep one shared multi-recipient concentration membership guard.")
    rows, index = matches[0]
    # Keep each expression below the runtime's 500-character limit.
    rows.insert(
        index + 1,
        _when(EMPTY_SOURCE, [_set("_source_concentration_target_invalid", False)], []),
    )


def _end(owner):
    """Remove only this empty source's caster index entry after genuine shared ending."""
    owner["effects"].insert(0, _set("_empty_control_ending", EMPTY_SOURCE))
    owner["effects"].append(
        _when(
            "_empty_control_ending == true and lasting_state.condition_caster_ids != null",
            [
                _set("_empty_control_kept_casters", []),
                _set("_empty_control_tracked_casters", "lasting_state.condition_caster_ids"),
                _each(
                    "_empty_control_tracked_casters",
                    "_empty_control_index_caster",
                    [
                        _when(
                            "_empty_control_index_caster != _source_state_id",
                            [_push("_empty_control_kept_casters", "_empty_control_index_caster")],
                        ),
                    ],
                ),
                _set("lasting_state.condition_caster_ids", "_empty_control_kept_casters"),
            ],
        )
    )


def bind_paid_empty_control_areas(runtime):
    """Bind after actual Fear/Sleep geometry and before private-owner pruning.

    No source/actor schema, printed profile, save, cost, action or pending owner
    changes. All nonempty casts retain their original recipient anchor and rolls.
    Empty concentration may persist outside combat after a combat cast.
    """
    tools = runtime["tools"]
    if QUALIFY not in tools:
        raise ValueError("Bind actual Fear/Sleep area admission before paid empty areas.")
    profiles = deepcopy(runtime["definitions"]["spells"])
    headers = deepcopy(runtime["definitions"]["spell_casting"])
    for spell in ("fear", "sleep"):
        if profiles[spell].get("base_targets") != 8:
            raise ValueError("Keep the approved eight-recipient control adaptation.")
    for name in ROOTS:
        removed = 0
        for rows in list(_effect_lists(tools[name]["effects"])):
            for index in range(len(rows) - 1, -1, -1):
                if rows[index] == OLD_EMPTY_GUARD:
                    rows.pop(index)
                    removed += 1
        if removed != 1:
            raise ValueError("Keep one actual control-area nonempty guard before this binding.")
    _allow_selected_empty(tools[PAID])
    _start(tools[START])
    # The retained read-query clones must observe the same live ownership rule.
    changed = [*ROOTS, PAID, START]
    for prefix in ("", QUERY_PREFIX):
        for suffix, binder in ((REFRESH, _refresh), (END, _end)):
            name = prefix + suffix
            if name not in tools:
                raise ValueError("The retained concentration owner is missing: " + name)
            binder(tools[name])
            changed.append(name)
    tools[PAID]["effects"].append(
        _when(
            "len(targets) == 0",
            [
                _push("_spell_summary_rows", {"label": "'Affected creatures'", "value": 0}),
                _push("_spell_summary_rows", {"label": "'Concentration'", "value": "'Active'"}),
                _push(
                    "_spell_detail_rows",
                    {
                        "label": "'Area'",
                        "value": "'No creatures were affected; concentration began.'",
                    },
                ),
            ],
        )
    )
    if (
        profiles != runtime["definitions"]["spells"]
        or headers != runtime["definitions"]["spell_casting"]
    ):
        raise ValueError(
            "Empty control areas must preserve every spell profile and casting header."
        )
    return changed
