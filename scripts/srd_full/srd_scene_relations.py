"""SRD relations read the admitted scene as the sole position authority."""

import re
from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
    from .srd_timed_senses import _loop, _public, _replace_tools
else:
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
    from srd_timed_senses import _loop, _public, _replace_tools

PUBLIC_TOOLS = {"resolve_srd_scene_relation"}
POSITION_EXPORTS = ("_info_scene_entity", "_info_position")
MEASURE_EXPORTS = ("_scene_distance", "_scene_direction")


def _array(name, size=3, default=None):
    parameter = _parameter(name, "array", items={"type": "number"}, min_items=size, max_items=size)
    if default is not None:
        parameter.update(required=False, default=default)
    return parameter


def _snapshot():
    return [
        _require("state.spatial_scene != null", "This operation needs the admitted spatial scene."),
        _require("state.spatial_scene.map.grid.unit == 'ft'", "SRD scene distances use feet."),
        {
            "spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"},
            "store_as": "_info_snapshot",
        },
    ]


def _spatial(operation, query, store):
    return {
        "spatial_query": {
            "scene": "state.spatial_scene",
            "operation": operation,
            "snapshot": "{_info_snapshot}",
            "query": query,
        },
        "store_as": store,
    }


def _tool_scene(description, parameters, effects):
    """Keep the neutral scene effect on its canonical state path."""
    if "spatial_query" in str(effects) and "'operation': 'snapshot'" not in str(effects):
        effects = _snapshot() + effects
    for parameter in parameters:
        if parameter["type"] == "object":
            parameter["from_state"] = (
                "state.spatial_scene"
                if parameter["name"] in {"volume", "candidate"}
                else "state.turn_effects"
            )
    tool = _tool(description, parameters, effects)

    def restore(value):
        if isinstance(value, list):
            return [restore(item) for item in value]
        if isinstance(value, dict):
            return {
                key: "state.spatial_scene" if key == "scene" else restore(item)
                for key, item in value.items()
            }
        return value

    tool["effects"] = restore(tool["effects"])
    return tool


def _scene_tools():
    position = _tool_scene(
        "Resolve a subject from the current admitted scene; no second position authority.",
        [_parameter("subject")],
        _snapshot()
        + [
            _set("_info_scene_entity", None),
            _set("_info_scene_entities", "state.spatial_scene.entities"),
            _loop(
                "_info_scene_entities",
                "_info_scene_item",
                [
                    _when(
                        "_info_scene_item.active == true and (_info_scene_item.id == subject or "
                        "_info_scene_item.metadata.participant_ref == subject)",
                        [
                            _require("_info_scene_entity == null", "Scene subject is ambiguous."),
                            _set("_info_scene_entity", "_info_scene_item"),
                        ],
                    ),
                ],
            ),
            _require("_info_scene_entity != null", "Subject has no active admitted scene entity."),
            _set("_info_position", "_info_scene_entity.position"),
        ],
    )
    measure = _tool(
        "Calculate Euclidean distance and direction from admitted scene positions.",
        [_array("start"), _array("end")],
        [
            _set("_scene_delta", [f"end[{i}] - start[{i}]" for i in range(3)]),
            _set(
                "_scene_distance",
                "(_scene_delta[0] ** 2 + _scene_delta[1] ** 2 + _scene_delta[2] ** 2) ** 0.5",
            ),
            _set("_scene_direction", [0, 0, 0]),
            _when(
                "_scene_distance > 0",
                [
                    _set(
                        "_scene_direction",
                        [f"_scene_delta[{i}] * _scene_distance ** -1" for i in range(3)],
                    ),
                ],
            ),
        ],
    )
    relation = _public(
        _tool_scene(
            "Calculate range, direction, sight and plane from admitted actor/target entries.",
            [_entity("actor_state"), _entity("target_state")],
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_actor_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_scene_actor_position", "_info_position"),
                _set("_scene_actor_entity", "_info_scene_entity"),
                _set("_scene_actor_entity_id", "_info_scene_entity.id"),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_target_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_scene_target_position", "_info_position"),
                _set("_scene_target_entity_id", "_info_scene_entity.id"),
                _require(
                    "_scene_actor_entity.metadata.plane != null and "
                    "_info_scene_entity.metadata.plane != null",
                    "Scene entries need their actual planes.",
                ),
                _set(
                    "_scene_same_plane",
                    "_scene_actor_entity.metadata.plane == _info_scene_entity.metadata.plane",
                ),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_scene_actor_position}", "end": "{_scene_target_position}"},
                    MEASURE_EXPORTS,
                ),
                _spatial(
                    "exposure",
                    {
                        "map_id": "{state.spatial_scene.map.id}",
                        "observer": {"point": "{_scene_actor_position}"},
                        "target": {"entity_id": "{_info_scene_entity.id}"},
                        "samples": {"horizontal": 3, "vertical": 3},
                        "include": ["geometry"],
                    },
                    "_scene_exposure",
                ),
                _set(
                    "_scene_line_of_sight",
                    "_scene_same_plane == true and _scene_exposure.visible_samples > 0",
                ),
                _set("_scene_can_see", False),
                _require(
                    "_info_scene_entity.metadata.lighting != null",
                    "The target needs its actual ambient lighting.",
                ),
                _call(
                    "resolve_srd_sight",
                    {
                        "observer_state": "{_actor_state_id}",
                        "subject_state": "{_target_state_id}",
                        "distance": "{ceil(_scene_distance)}",
                        "lighting": "{_info_scene_entity.metadata.lighting}",
                        "obstruction": "clear",
                        "subject_invisible": "{_info_scene_entity.metadata.invisible == true}",
                        "subject_ethereal": (
                            "{_scene_same_plane != true and "
                            "_info_scene_entity.metadata.plane == 'ethereal'}"
                        ),
                    },
                    ["_sight_can_see"],
                ),
                _set(
                    "_scene_can_see",
                    "_scene_exposure.visible_samples > 0 and _sight_can_see == true and "
                    "(_scene_same_plane == true or "
                    "(_scene_actor_entity.metadata.plane == 'material' "
                    "and _info_scene_entity.metadata.plane == 'ethereal'))",
                ),
                _when(
                    "_scene_line_of_sight == true and actor_state.blindsight_feet != null and "
                    "actor_state.blindsight_feet >= _scene_distance",
                    [_set("_scene_can_see", True)],
                ),
            ],
        ),
        "exploration",
        "Scene relation",
        [
            ("Distance", "_scene_distance"),
            ("Direction", "_scene_direction"),
            ("Line of sight", "_scene_line_of_sight"),
            ("Same plane", "_scene_same_plane"),
            ("Actor position", "_scene_actor_position"),
            ("Target position", "_scene_target_position"),
            ("Actor scene ID", "_scene_actor_entity_id"),
            ("Target scene ID", "_scene_target_entity_id"),
            ("Can see", "_scene_can_see"),
        ],
    )
    geometry = deepcopy(relation)
    sight_start = next(
        index
        for index, effect in enumerate(geometry["effects"])
        if effect.get("set", {}).get("field") == "_scene_can_see"
    )
    geometry["effects"] = geometry["effects"][:sight_start]
    geometry["description"] = (
        "Calculate admitted scene geometry without invoking sight or a D20 test."
    )
    geometry["metadata"] = {
        "category": "internal", "visible_to_player": False, "costs_action": False
    }
    return {
        "srd_information_scene_entity": position,
        "measure_srd_information_distance": measure,
        "resolve_srd_scene_geometry_relation": geometry,
        "resolve_srd_scene_relation": relation,
    }


def _public_wrappers(changed):
    """Keep state bindings private and preserve resolved entity references."""
    for name in PUBLIC_TOOLS:
        tool = changed[name]
        if not any(p["name"] == "lasting_state" for p in tool["parameters"]):
            continue
        node_name = name + "_node"
        node = deepcopy(tool)
        node["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        changed[node_name] = node
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        params = {
            p["name"]: "{"
            + (
                "_" + p["name"] + "_id"
                if p.get("resolve") in {"entity", "participant"}
                else p["name"]
            )
            + "}"
            for p in tool["parameters"]
        }
        params["lasting_state"] = "state"
        exports = [
            row["value"]["path"].removeprefix("context.")
            for row in tool["metadata"]["result_card"]["summary"]
        ]
        optional_references = [
            _when(p["name"] + " == null", [_set("_" + p["name"] + "_id", None)])
            for p in tool["parameters"]
            if p.get("resolve") == "entity" and p.get("required") is False
        ]
        tool["effects"] = optional_references + [_call(node_name, params, exports)]


def extend_runtime(text):
    """Emit the independently admitted range/visibility owner and its public state wrapper."""
    changed = _scene_tools()
    argument_index = 0

    def normalize_calls(effects):
        nonlocal argument_index
        result = []
        for effect in effects:
            for key in ("on_true", "on_false"):
                if key in effect:
                    effect[key] = normalize_calls(effect[key])
            if "for_each" in effect:
                effect["for_each"]["do"] = normalize_calls(effect["for_each"]["do"])
            if "call" in effect:
                for name, value in effect["params"].items():
                    if (
                        isinstance(value, str)
                        and re.fullmatch(r"\{[^{}]+\}", value)
                        and not re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_.]*\}", value)
                    ):
                        argument_index += 1
                        variable = f"_info_argument_{argument_index}"
                        result.append(_set(variable, value[1:-1]))
                        effect["params"][name] = "{" + variable + "}"
            result.append(effect)
        return result

    for tool in changed.values():
        tool["effects"] = normalize_calls(tool["effects"])
    _public_wrappers(changed)
    return _replace_tools(text, changed)
