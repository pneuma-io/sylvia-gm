"""Preflight the retained SRD Misty Step before its shared casting budget.

The original SRD travel owner remains responsible for paid casting and saved
teleportation. This binder repeats its actual 30-foot Euclidean distance, exact
placement and point-sight checks before a pending begin can spend a Bonus Action.
"""

import re


def _set(field, value):
    return {"set": {"field": field, "value": value}}


def _require(condition, error):
    return {"require": {"condition": condition, "error": error}}


def _call(name, params, exports=()):
    result = {"call": name, "params": params}
    if exports:
        result["export_context"] = {key: key for key in exports}
    return result


def misty_step_preflight_tool():
    """Only scalar identity and exact numeric coordinates cross this helper."""
    return {
        "description": "Check the original SRD exact visible 30-foot destination before expenditure.",
        "parameters": [
            {"name": "caster_state", "type": "string", "resolve": "entity", "required": True},
            {"name": "lasting_state", "type": "string", "resolve": "entity", "required": False, "default": "state"},
            {"name": "destination", "type": "array", "items": {"type": "number"}, "min_items": 3, "max_items": 3, "required": True},
        ],
        "pre_conditions": [],
        "effects": [
            _call("srd_information_scene_entity", {"subject": "{_caster_state_id}"}, ("_info_scene_entity",)),
            _set("_revised_misty_caster", "_info_scene_entity"),
            _require("_revised_misty_caster.metadata.plane != null", "Record the caster's actual plane before teleporting."),
            _call("measure_srd_information_distance", {
                "start": "{_revised_misty_caster.position}", "end": "{destination}",
            }, ("_scene_distance",)),
            _require("_scene_distance <= 30", "The chosen destination exceeds the spell's actual teleport range."),
            _call("resolve_srd_travel_placement", {
                "subject": "{_caster_state_id}", "destination": "{destination}",
                "plane": "{_revised_misty_caster.metadata.plane}", "excluded": [],
                "lasting_state": "{_lasting_state_id}",
            }, ("_travel_placement",)),
            _require("_travel_placement.distance == 0", "Misty Step needs an actual unoccupied destination."),
            _call("check_srd_travel_destination_sight", {
                "caster_state": "{_caster_state_id}", "point": "{destination}",
            }),
        ],
        "metadata": {"category": "internal", "visible_to_player": False, "costs_action": False},
    }


def bind_revised_misty_step(runtime):
    """Bind only pre-cost checks, preserving original profiles and paid travel."""
    tools = runtime["tools"]
    required = {
        "cast_spell", "begin_srd_spell_action", "cast_spell_travel_spell",
        "srd_information_scene_entity", "measure_srd_information_distance",
        "resolve_srd_travel_placement", "check_srd_travel_destination_sight",
    }
    missing = required - tools.keys()
    if missing:
        raise ValueError("Retain the real SRD travel consumers before binding Misty Step: " + ", ".join(sorted(missing)))
    helper = "validate_revised_srd_misty_step"
    if helper in tools:
        raise ValueError("The revised Misty Step preflight was already composed.")
    tools[helper] = misty_step_preflight_tool()
    identities = {}
    for name in ("cast_spell", "begin_srd_spell_action"):
        effects = tools[name]["effects"]
        joins = [i for i, effect in enumerate(effects)
                 if effect.get("set", {}).get("field") == "_spell_definition"
                 or effect.get("store_as") == "_spell_definition"]
        if len(joins) != 1:
            raise ValueError("Review the actual SRD pre-cost spell-definition join for " + name)
        lookup = effects[joins[0]].get("table_lookup", {})
        identity = lookup.get("key")
        if (lookup.get("table") != "definitions.spells" or lookup.get("required") is not True
                or not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identity)):
            raise ValueError("Review the actual required SRD spell-definition selector for " + name)
        identities[name] = identity
        effects[joins[0] + 1:joins[0] + 1] = [{
            "condition": identity + " == 'misty_step'",
            "on_true": [
                _require("targets == null or len(targets) == 0", "Misty Step transports only its caster."),
                _call(helper, {
                    "caster_state": "{_caster_state_id}", "lasting_state": "{_lasting_state_id}",
                    "destination": "{travel_destination}",
                }),
            ],
        }]
    return {"profile": "misty_step", "pre_cost_paths": ["cast_spell", "begin_srd_spell_action"],
            "identity_from_actual_definition_lookup": identities,
            "retained_owner": "cast_spell_travel_spell", "teleport_distance_feet": 30,
            "profile_or_paid_owner_replaced": False}
