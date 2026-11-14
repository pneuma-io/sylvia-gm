"""Bind copied object mitigation to each packet's actual component type."""

import re
from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


PACKETS = ("apply_srd_area_object_damage", "apply_srd_teleport_object_damage")
LOCAL = "_object_packet_damage_type"


def _lists(effects):
    """Visit the finite emitted effect tree without changing its owning branches."""
    yield effects
    for effect in effects:
        for branch in ("on_true", "on_false"):
            if isinstance(effect.get(branch), list):
                yield from _lists(effect[branch])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _values(value):
    """Retarget only expression values; schema keys and type formals keep their identity."""
    if isinstance(value, str):
        return re.sub(r"\bdamage_type\b", LOCAL, value)
    if isinstance(value, list):
        return [_values(child) for child in value]
    if isinstance(value, dict):
        return {key: _values(child) for key, child in value.items()}
    return value


def bind_runtime_consumers(text):
    """Correct the two copied mitigation segments after final packet/storage emission."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in PACKETS:
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        found = []
        for effects in _lists(tool["effects"]):
            for index, effect in enumerate(effects):
                assignment = effect.get("set", {})
                if assignment.get("field") not in {"damage_type", LOCAL}:
                    continue
                component = assignment.get("value")
                if component not in {"component_1_damage_type", "component_2_damage_type"}:
                    raise ValueError("Retain each object packet's actual component type.")
                number = component.split("_")[1]
                boundary = f"_area_object_component_{number}_applied"
                end = next(
                    (
                        i
                        for i in range(index + 1, len(effects))
                        if effects[i].get("set", {}).get("field") == boundary
                    ),
                    None,
                )
                if end is None or effects[end]["set"]["value"] != "_object_damage_applied":
                    raise ValueError("Retain the existing component mitigation receipt.")
                assignment["field"] = LOCAL
                effects[index + 1 : end] = _values(effects[index + 1 : end])
                found.append(number)
        if sorted(found) != ["1", "2"]:
            raise ValueError("Retain exactly two source-owned object mitigation components.")
        if tool != tools[name]:
            changed[name] = tool
    return _replace_tools(text, changed)
