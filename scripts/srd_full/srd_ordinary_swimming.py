"""Use the existing Speed pool for authenticated water routes without a Swim Speed."""

import re
from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _load_runtime, _set, _when
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import _load_runtime, _set, _when
    from srd_timed_senses import _replace_tools


WATER = "qualify_srd_water_route"
WET = "_ordinary_water_swim"
MODE = "_ordinary_swim_budget_mode"
SWIMMING_SHA256 = "09b06feeb97ea2800c23025831e4047d34ab8d1bbf9b137c72fb1441f6f2a123"


def _branches(effects):
    """Visit executable effect lists while preserving actual source-record literals."""
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _branches(effect[key])
        if "for_each" in effect:
            yield from _branches(effect["for_each"]["do"])


def _water_projection(original):
    """Expose wetness only after the existing complete body/source route proof succeeds."""
    owner = deepcopy(original)
    owner["effects"] = [
        effect for effect in owner["effects"] if effect.get("set", {}).get("field") != WET
    ]
    owner["effects"].append(
        _set(
            WET,
            "mode == 'swim' and _water_start_wet == true and _water_present == true and "
            "_water_start_body != null and _water_start_body == _water_geometry_id",
        )
    )
    return owner


def bind_runtime_consumers(text):
    """Run after final Water/Tsunami route binding, preserving native swimming and one cost."""
    text = text.replace("\r\n", "\n")
    tools = _load_runtime(text)["tools"]
    name = "move_srd_scene_creature_environment_segment"
    if name not in tools or WATER not in tools:
        return text
    movement = deepcopy(tools[name])
    lists = list(_branches(movement["effects"]))
    water_calls = [effect for effects in lists for effect in effects if effect.get("call") == WATER]
    if not water_calls:
        raise ValueError("Bind the final authentic Water route before ordinary Swimming.")
    for call in water_calls:
        exports = call.setdefault("export_context", {})
        if not isinstance(exports, dict):
            raise ValueError("Preserve named actual Water route exports.")
        exports[WET] = WET
    if not any(effect.get("set", {}).get("field") == WET for effect in movement["effects"]):
        movement["effects"].insert(0, _set(WET, False))

    # Tsunami's existing receipt still proves its Athletics path. Its earlier local
    # extra-cost addition is replaced by the same single ordinary-swimming owner.
    tsunami = any(
        effect.get("call") == "qualify_srd_tsunami_swim_segment"
        for effects in lists
        for effect in effects
    )
    for effects in lists:
        for effect in effects:
            if effect.get("condition") == "_tsunami_ordinary_swim == true":
                effect["on_true"] = [
                    child
                    for child in effect["on_true"]
                    if child.get("set")
                    != {"field": "_route_cost", "value": "_route_cost + _route_distance"}
                ]
    wet = f"({WET} == true" + (" or _tsunami_ordinary_swim == true)" if tsunami else ")")
    condition = (
        "mode == 'swim' and "
        + wet
        + " and (actor_state.swim_speed == null or actor_state.swim_speed <= 0) and "
        "actor_state.spell_freedom_of_movement != true"
    )
    for effects in lists:
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get("call") not in {
                "spend_combat_movement",
                "derive_srd_scene_movement_budget",
            }:
                continue
            key = "movement_kind" if effect["call"] == "spend_combat_movement" else "mode"
            previous = effect["params"][key]
            if previous == "{" + MODE + "}":
                continue
            value = previous[1:-1] if re.fullmatch(r"\{[^{}]+\}", previous) else repr(previous)
            effects[index:index] = [
                _set(MODE, value),
                _when(
                    condition,
                    [_set(MODE, "'walk'"), _set("_route_cost", "_route_cost + _route_distance")],
                ),
            ]
            effect["params"][key] = "{" + MODE + "}"
    return _replace_tools(text, {WATER: _water_projection(tools[WATER]), name: movement})
