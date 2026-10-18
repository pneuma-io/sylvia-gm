"""Qualify scaled stabilization and ordinary Dispel recipients before casting costs."""

import re
from copy import deepcopy

from .srd_lite_combat_target_facts import PROJECT
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _call, _require, _set, _when

MARKER = "_special_recipient_source_intents"
ROOTS = ("cast_spell", "begin_srd_spell_action")


def _private_reference(value):
    """Accept the current consumer's exact private scalar handoff."""
    match = re.fullmatch(r"\{(_[A-Za-z0-9_]+)\}", value)
    if match is None:
        raise ValueError("The special recipient fact handoff changed")
    return match[1]


def _fact_handoffs(owner):
    """Find the retained distance and cover source without replacing either resolver."""
    rows = [row for effects in lists(owner["effects"]) for row in effects]
    stabilization = [
        row for row in rows if row.get("call") == "cast_spell_instantaneous_stabilization"
    ]
    recovery = [
        row
        for row in rows
        if row.get("call") == "cast_srd_recovery" and "target_distance" in row.get("params", {})
    ]
    if len(stabilization) != 1 or len(recovery) != 1:
        raise ValueError("Preserve one stabilization and one ordinary Recovery dispatch")
    distance = _private_reference(stabilization[0]["params"]["target_distance"])
    if _private_reference(recovery[0]["params"]["target_distance"]) != distance:
        raise ValueError("The retained recipient distance handoffs diverged")
    cover = _private_reference(stabilization[0]["params"]["cover"])
    sources = [
        row["set"]["value"]
        for row in rows
        if row.get("set", {}).get("field") == cover
        and re.fullmatch(r"_[A-Za-z0-9_]+", str(row["set"].get("value", "")))
    ]
    if len(sources) != 1:
        raise ValueError("The retained stabilization cover source changed")
    intents = [
        row["set"]["value"]
        for row in rows
        if row.get("set", {}).get("field") == "_recovery_source_intents"
    ]
    if len(intents) != 1 or not isinstance(intents[0], list):
        raise ValueError("The selected-source Recovery dispatch changed")
    source_intents = deepcopy(intents[0])
    # These retained source routes predate the unified selected-source dispatch.
    # They own geometry too; preserve their existing admission and delivery.
    for choice in ("environment", "illusion", "minor_world"):
        if repr(choice) not in source_intents:
            source_intents.append(repr(choice))
    return distance, sources[0], source_intents


def _preflight(distance, cover, source_intents):
    """Use the selected casting profile and saved body for these two combat recipients."""
    project = _call(
        PROJECT,
        {
            "caster_state": "{_caster_state_id}",
            "target_state": "{_target_id}",
            "lasting_state": "{_lasting_state_id}",
            "range_feet": "{_special_recipient_range}",
        },
    )
    project["export_context"] = {
        distance: "_coord_fact_target_distance",
        cover: "_coord_fact_cover",
    }
    return _when(
        "combat.encounter_active == true and (_ms == 'spare_the_dying' or _ms == 'dispel_magic')",
        [
            _set(MARKER, source_intents),
            _when(
                "_ms == 'spare_the_dying' or recovery_choice not in " + MARKER,
                [
                    _require("_target_id != null", "Select the actual combat recipient."),
                    _require(
                        "targets == null or len(targets) == 0 or "
                        "(len(targets) == 1 and targets[0] == _target_id)",
                        "This spell selects one actual recipient.",
                    ),
                    _set("_special_recipient_profile", "definitions.spells[_ms]"),
                    _set("_special_recipient_range", 0),
                    _when(
                        "_ms == 'spare_the_dying'",
                        [
                            _require(
                                "_casting_profile.scaling_level >= 1 and "
                                "_casting_profile.scaling_level <= 20",
                                "Stabilization requires the caster's actual saved level.",
                            ),
                            _set(
                                "_special_recipient_range",
                                "_special_recipient_profile.base_range_feet",
                            ),
                            *[
                                _when(
                                    "_casting_profile.scaling_level >= "
                                    f"_special_recipient_profile.upgrade_level_{index}",
                                    [
                                        _set(
                                            "_special_recipient_range",
                                            f"_special_recipient_profile.upgrade_range_feet_{index}",
                                        ),
                                    ],
                                )
                                for index in (1, 2, 3)
                            ],
                        ],
                    ),
                    _when(
                        "_ms == 'dispel_magic'",
                        [
                            _set(
                                "_special_recipient_range", "_special_recipient_profile.range_feet"
                            ),
                        ],
                    ),
                    project,
                ],
            ),
        ],
    )


def _assert_fact_order(owner, distance, cover, *, pending):
    """Require projection to survive into the later native copy or pending capture."""
    positioned = [
        (index, row)
        for index, node in enumerate(owner["effects"])
        for effects in lists([node])
        for row in effects
    ]
    profiles = [
        index for index, row in positioned if row.get("set", {}).get("field") == "_casting_profile"
    ]
    if len(profiles) != 2:
        raise ValueError("Preserve the two saved casting-profile branches")
    for field in (distance, cover):
        setters = [index for index, row in positioned if row.get("set", {}).get("field") == field]
        if not setters or max(setters) >= min(profiles):
            raise ValueError("A later initializer would overwrite projected recipient facts")
    payments = [
        index
        for index, row in positioned
        if row.get("call") in ("resolve_srd_spell_materials", "spend_spell_casting_budget")
    ]
    if not payments or min(payments) <= max(profiles):
        raise ValueError("Recipient projection must precede material and action payment")
    if pending:
        captures = [
            row["set"]["value"]
            for _, row in positioned
            if row.get("set", {}).get("field") == "_retained_nullable_capture_rows"
        ]
        if len(captures) != 1:
            raise ValueError("Preserve the existing pending-parameter capture")
        for public, private in (("target_distance", distance), ("cover", cover)):
            entries = [row for row in captures[0] if row.get("key") == repr(public)]
            if entries != [
                {"key": repr(public), "missing": private + " == null", "value": private}
            ]:
                raise ValueError("Pending capture must keep the projected " + public)
    else:
        copies = [
            (index, row["set"]["field"])
            for index, row in positioned
            if row.get("set", {}).get("value") == cover
        ]
        calls = [
            index
            for index, row in positioned
            if row.get("call") == "cast_spell_instantaneous_stabilization"
        ]
        if len(copies) != 1 or len(calls) != 1 or not (max(profiles) < copies[0][0] < calls[0]):
            raise ValueError("Stabilization must copy projected cover after profile admission")


def bind_special_recipient_facts(runtime):
    """Bind after existing fact projections, before either profile's payment or pending window."""
    tools = runtime["tools"]
    if PROJECT not in tools:
        raise ValueError("Special recipients require the existing combat projection")
    distance, cover, source_intents = _fact_handoffs(tools["cast_spell"])
    for name in ROOTS:
        owner = tools[name]
        if any(
            row.get("set", {}).get("field") == MARKER
            for effects in lists(owner["effects"])
            for row in effects
        ):
            raise ValueError("Special recipient facts were already bound: " + name)
        _assert_fact_order(owner, distance, cover, pending=name == "begin_srd_spell_action")
        sites = [
            (effects, index)
            for effects in list(lists(owner["effects"]))
            for index, row in enumerate(effects)
            if row.get("set", {}).get("field") == "_casting_profile"
        ]
        if len(sites) != 2:
            raise ValueError("Preserve the actual character and NPC casting profiles: " + name)
        for effects, index in reversed(sites):
            effects.insert(index + 1, _preflight(distance, cover, source_intents))
    return list(ROOTS)
