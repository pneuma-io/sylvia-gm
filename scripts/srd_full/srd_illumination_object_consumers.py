"""Retained outline Advantage uses actual object sight and source obscurement."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array
    from .srd_timed_actor_defenses import _call, _load_runtime, _parameter, _set, _tool, _when
    from .srd_timed_senses import _replace_tools
else:
    from srd_scene_relations import _array
    from srd_timed_actor_defenses import _call, _load_runtime, _parameter, _set, _tool, _when
    from srd_timed_senses import _replace_tools


def _obscurement_tool(point):
    """Reuse the actual point owner's heavy-zone and Fog rules without replacing body cover."""
    zones = next(
        e for e in point["effects"] if e.get("for_each", {}).get("as") == "_illumination_point_zone"
    )
    zones = deepcopy(zones)
    body = zones["for_each"]["do"][0]["on_true"]
    body[:] = [
        e
        for e in body
        if e.get("condition")
        == "_illumination_point_zone.metadata.obstruction == 'heavy_obscurement'"
    ]
    if len(body) != 1:
        raise ValueError("Object source sight needs the actual point heavy-obscurement owner.")

    def bind(value):
        if isinstance(value, str):
            return (
                value.replace("_illumination_point_origin", "start")
                .replace("_illumination_point_plane", "plane")
                .replace("{point}", "{end}")
                .replace("_illumination_point_obscured", "_illumination_object_obscured")
            )
        if isinstance(value, list):
            return [bind(child) for child in value]
        if isinstance(value, dict):
            return {key: bind(child) for key, child in value.items()}
        return value

    zones = bind(zones)
    fog = next(e for e in point["effects"] if e.get("call") == "resolve_srd_environment_segment")
    fog = bind(deepcopy(fog))
    return _tool(
        "Qualify heavy obscurement on the actual same-plane observer-to-object anchor segment.",
        [_array("start"), _array("end"), _parameter("plane")],
        [
            _set("_illumination_object_obscured", False),
            _set("_illumination_point_zones", "state.spatial_scene.zones"),
            zones,
            _when(
                "state.has_spell_environments == true",
                [
                    fog,
                    _when(
                        "_environment_sight_obscured == true",
                        [_set("_illumination_object_obscured", True)],
                    ),
                ],
            ),
        ],
    )


def _tools(runtime):
    """Bind actual source obscurement after native object senses and before its real D20."""
    tools = runtime["tools"]
    relation = deepcopy(tools["resolve_srd_spell_object_relation"])
    if "resolve_srd_illumination_object_obscurement" in str(relation["effects"]):
        return {}
    if not all(
        name in str(relation["effects"])
        for name in ("_object_caster_scene", "_object_scene", "_object_distance", "_object_can_see")
    ):
        raise ValueError("Object sight needs its actual prepared relation and sense owner.")
    point = tools["resolve_srd_illumination_point_sight"]
    helper = _obscurement_tool(point)
    relation["effects"].append(
        _when(
            "lasting_state.has_spell_environments == true or "
            "len(lasting_state.spatial_scene.zones) > 0",
            [
                _call(
                    "resolve_srd_illumination_object_obscurement",
                    {
                        "start": "{_object_caster_scene.position}",
                        "end": "{_object_scene.position}",
                        "plane": "{_object_scene.metadata.plane}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                    ["_illumination_object_obscured"],
                ),
                _when(
                    "_illumination_object_obscured == true and "
                    "(caster_state.blindsight_feet == null "
                    "or caster_state.blindsight_feet < _object_distance)",
                    [_set("_object_can_see", False)],
                ),
            ],
        )
    )
    return {
        "resolve_srd_spell_object_relation": relation,
        "resolve_srd_illumination_object_obscurement": helper,
    }


def bind_runtime_consumers(text):
    """Run after actual object/Fog/Light owners; their public spell clones share this relation."""
    return _replace_tools(text, _tools(_load_runtime(text)))
