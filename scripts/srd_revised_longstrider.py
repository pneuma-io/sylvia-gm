"""Reuse original SRD Longstrider if an old Lite builder is retained.

Capture its immutable provider rows before old Lite adapters, then bind this
after those adapters. Casting, slots, speed projection and time expiry stay
with the original source owners; no legacy until-rest owner is installed here.
"""

from copy import deepcopy

from .srd_lite_profiles import lists


def capture_srd_longstrider(runtime):
    """Retain the actual immutable provider contract before any variant mutation."""
    profile = runtime["definitions"]["spells"]["longstrider"]
    header = runtime["definitions"]["spell_casting"]["longstrider"]
    assert profile["family"] == "multi_target_timed_speed_bonus"
    assert (profile["base_level"], profile["base_targets"], profile["targets_per_slot_level"],
        profile["duration_rounds"], profile["speed_bonus"], profile["range_kind"]) == (1, 1, 1, 600, 10, "touch")
    assert header["range"] == "Touch" and header["duration"] == "1 hour"
    assert header["components"] == ["verbal", "somatic", "material"] and header["ritual"] is False
    return {"profile": deepcopy(profile), "header": deepcopy(header)}


def bind_revised_srd_longstrider(runtime, captured):
    """Retain other spell adapters while returning only Longstrider to its SRD owners."""
    definitions = runtime["definitions"]
    definitions["spells"]["longstrider"] = deepcopy(captured["profile"])
    definitions["spell_casting"]["longstrider"] = deepcopy(captured["header"])
    if "srd_lite_free_rituals" in definitions:
        definitions["srd_lite_free_rituals"] = [spell for spell in definitions["srd_lite_free_rituals"] if spell != "longstrider"]
    duration_schema = runtime["definition_row_schemas"]["definitions.spells"]["variants"]["multi_target_timed_speed_bonus"]["properties"]["duration_rounds"]
    if "enum" in duration_schema:
        duration_schema["enum"] = list(dict.fromkeys([*duration_schema["enum"], 600]))
    tools = runtime["tools"]
    rewrites = 0
    for name in ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"):
        tool = tools[name]
        for effects in lists(tool["effects"]):
            for effect in effects:
                if effect.get("call") == "cast_srd_lite_longstrider":
                    effect["call"] = "cast_spell_multi_target_timed_speed_bonus"
                    effect["params"].pop("preparation_source", None)
                    effect["params"]["targets"] = "{targets}"
                    rewrites += 1
        for node in _nodes(tool):
            for key in ("condition", "value"):
                expression = node.get(key)
                if isinstance(expression, str):
                    for identity in ("spell", "_ms"):
                        old = "(" + identity + " == 'longstrider' or " + identity + " == 'jump' or " + identity + " == 'enhance_leap')"
                        new = "(" + identity + " == 'jump' or " + identity + " == 'enhance_leap')"
                        expression = expression.replace(old, new)
                    node[key] = expression
    assert "cast_spell_multi_target_timed_speed_bonus" in tools
    return {"longstrider_old_lite_cast_redirects_restored": rewrites,
        "new_mechanic_tools": 0, "geometry_contract": "Original saved adjudicated touch input retained"}


def _nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)
