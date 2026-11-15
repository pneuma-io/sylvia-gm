"""A real retained Wind Wall owns its ground body and printed later barriers."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array
    from .srd_spell_environment import (
        _areas,
        _call,
        _each,
        _effect_lists,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _require,
        _set,
        _wall_of_fire_tools,
        _when,
        _wind_fog_mode,
    )
else:
    from srd_scene_relations import POSITION_EXPORTS, _array
    from srd_spell_environment import (
        _areas,
        _call,
        _each,
        _effect_lists,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _require,
        _set,
        _wall_of_fire_tools,
        _when,
        _wind_fog_mode,
    )

SOURCE_SHA = "6d9300665ef4d85ee1e0999837a0d0e54637da21c9410164bfae28e6eb362a08"
PROFILE = {
    "family": "spell_environment",
    "casting_time": "action",
    "base_level": 3,
    "range_kind": "distance",
    "range_feet": 120,
    "radius_feet": 0,
    "radius_per_slot_level": 0,
    "duration_rounds": 10,
    "concentration": True,
    "environment_mode": "wind_wall",
    "damage_dice": 4,
    "damage_die": "d8",
    "damage_per_slot_level": 0,
    "damage_type": "bludgeoning",
    "save_ability": "strength",
}


def _rename(value):
    if isinstance(value, str):
        return (
            value.replace("wall_of_fire", "wind_wall")
            .replace("Wall of Fire", "Wind Wall")
            .replace("'wall_fire'", "'wind_wall'")
            .replace("_fire_", "_wind_")
        )
    if isinstance(value, dict):
        return {_rename(k): _rename(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rename(v) for v in value]
    return value


def _appearance_tools():
    """Keep the proven whole-Box/shared appearance save packet, with no later damage triggers."""
    original = _wall_of_fire_tools()
    selected = (
        "qualify_srd_wall_of_fire_source",
        "qualify_srd_wall_of_fire_target",
        "resolve_srd_wall_of_fire_body_segment",
        "resolve_srd_wall_of_fire_event",
        "resolve_srd_wall_of_fire_scene",
        "qualify_srd_wall_of_fire_effect",
    )
    result = {_rename(n): _rename(deepcopy(original[n])) for n in selected}
    for tool in result.values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "timing":
                parameter.update(default="initial", enum_values=["initial"])
    source = result["qualify_srd_wind_wall_source"]
    source["effects"].append(
        _set(
            "_sphere_source_current",
            "_sphere_source_current == true and _sphere_source.spell == 'wind_wall' and "
            f"_sphere_source.source_sha256 == '{SOURCE_SHA}'",
        )
    )
    target = result["qualify_srd_wind_wall_target"]
    target["effects"] = [
        e for e in target["effects"] if not e.get("condition", "").startswith("timing == 'end'")
    ]
    source_index = next(
        i
        for i, e in enumerate(target["effects"])
        if e.get("set", {}).get("field") == "_sphere_target_source"
    )
    target["effects"].insert(
        source_index + 1,
        _set(
            "_wind_exposure_origin",
            [
                f"(_sphere_target_source.volume.min[{i}] + "
                f"_sphere_target_source.volume.max[{i}]) * 0.5"
                for i in range(3)
            ],
        ),
    )
    for effects in _effect_lists(target["effects"]):
        for effect in effects:
            if effect.get("spatial_query", {}).get("operation") == "exposure":
                effect["spatial_query"]["query"]["observer"]["point"] = "{_wind_exposure_origin}"
    result["resolve_srd_wind_wall_event"]["description"] = (
        "Settle this exact Wind Wall appearance receipt through the native4d8 STR-half packet."
    )
    return result


def _gas_tool():
    """Reuse Gust's genuine spell-fog retirement rule for a current Wall body."""
    return _environment_tool(
        "Disperse source fog whose own strong-wind clause applies to real clear Wind Wall contact.",
        [_param("area_id")],
        [
            _set("_wind_gas_wall", "state.persistent_spell_areas[area_id]"),
            _call(
                "qualify_srd_wind_wall_source",
                {"caster_state": "{_wind_gas_wall.caster}", "area_id": "{area_id}"},
                ("_sphere_source_current",),
            ),
            _when(
                "_sphere_source_current == true",
                [
                    _set(
                        "_wind_gas_origin",
                        [
                            f"(_wind_gas_wall.volume.min[{i}] + "
                            f"_wind_gas_wall.volume.max[{i}]) * 0.5"
                            for i in range(3)
                        ],
                    ),
                    _set("_wind_gas_ids", "state.persistent_spell_area_ids"),
                    _each(
                        "_wind_gas_ids",
                        "_wind_gas_id",
                        [
                            _set("_wind_gas_fog", "state.persistent_spell_areas[_wind_gas_id]"),
                            _when(
                                "_wind_gas_fog.profile == 'environment' and "
                                "_wind_gas_fog.active == true and "
                                "state.lasting_tick < _wind_gas_fog.expires_tick and "
                                "_wind_gas_fog.plane == _wind_gas_wall.plane and "
                                + _wind_fog_mode("_wind_gas_fog"),
                                [
                                    _set("_wind_fog_caster", "_wind_gas_fog.caster"),
                                    _set(
                                        "_wind_gas_closest",
                                        [
                                            f"max(_wind_gas_wall.volume.min[{i}], "
                                            f"min(_wind_gas_wall.volume.max[{i}], "
                                            f"_wind_gas_fog.origin[{i}]))"
                                            for i in range(3)
                                        ],
                                    ),
                                    _call(
                                        "measure_srd_information_distance",
                                        {
                                            "start": "{_wind_gas_closest}",
                                            "end": "{_wind_gas_fog.origin}",
                                        },
                                        ("_scene_distance",),
                                    ),
                                    _when(
                                        "_scene_distance < _wind_gas_fog.radius",
                                        [
                                            _call(
                                                "srd_information_barriers",
                                                {
                                                    "start": "{_wind_gas_origin}",
                                                    "end": "{_wind_gas_closest}",
                                                    "rule": "solid",
                                                    "plane": "{_wind_gas_wall.plane}",
                                                },
                                                ("_information_blocked",),
                                            ),
                                            _when(
                                                "_information_blocked != true",
                                                [
                                                    _call(
                                                        "qualify_srd_environment_concentration",
                                                        {
                                                            "source_state": "{_wind_fog_caster}",
                                                            "area_id": "{_wind_gas_id}",
                                                        },
                                                    ),
                                                    _call(
                                                        "end_srd_concentration_node",
                                                        {
                                                            "source_state": "{_wind_fog_caster}",
                                                            "target_state": "{_wind_fog_caster}",
                                                            "lasting_state": "state",
                                                        },
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _passage_tool():
    """Movement permission derives actual settled form, size, flight and strict body interior."""
    return _environment_tool(
        "Prevent actual gaseous or Small-or-smaller flying bodies from traversing current wind.",
        [_entity("actor_state"), _array("start"), _array("end"), _param("mode")],
        [
            _call(
                "project_srd_transformation",
                {"target_state": "{_actor_state_id}", "lasting_state": "state"},
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_actor_state_id}"}, POSITION_EXPORTS
            ),
            _set("_wind_actor_plane", "_info_scene_entity.metadata.plane"),
            _set("_wind_actor_restricted", "actor_state.form_cloud == true"),
            _when(
                "mode == 'fly' or _info_scene_entity.metadata.flying == true",
                [
                    _require(
                        "actor_state.size_rank != null",
                        "Admit this flying creature's actual size before wind passage.",
                    ),
                    _set(
                        "_wind_actor_restricted",
                        "_wind_actor_restricted == true or actor_state.size_rank <= 1",
                    ),
                ],
            ),
            _when(
                "_wind_actor_restricted == true",
                _areas(
                    [
                        _when(
                            "_environment_area.mode == 'wind_wall' and "
                            "_environment_area.plane == _wind_actor_plane",
                            [
                                _call(
                                    "qualify_srd_wind_wall_source",
                                    {
                                        "caster_state": "{_environment_area.caster}",
                                        "area_id": "{_environment_id}",
                                    },
                                    ("_sphere_source_current",),
                                ),
                                _when(
                                    "_sphere_source_current == true",
                                    [
                                        _call(
                                            "resolve_srd_wind_wall_body_segment",
                                            {
                                                "moving_subject": "{_actor_state_id}",
                                                "area_id": "{_environment_id}",
                                                "start": "{start}",
                                                "end": "{end}",
                                            },
                                            (
                                                "_body_hit",
                                                "_body_entry",
                                                "_body_exit",
                                                "_body_bounds",
                                            ),
                                        ),
                                        _when(
                                            "_body_hit == true",
                                            [
                                                _set(
                                                    "_wind_passage_mid",
                                                    [
                                                        f"start[{i}] + "
                                                        f"(end[{i}] - start[{i}]) * "
                                                        "((_body_entry + _body_exit) * 0.5)"
                                                        for i in range(3)
                                                    ],
                                                ),
                                                _require(
                                                    " or ".join(
                                                        f"_wind_passage_mid[{i}] <= "
                                                        f"_body_bounds.min[{i}] or "
                                                        f"_wind_passage_mid[{i}] >= "
                                                        f"_body_bounds.max[{i}]"
                                                        for i in range(3)
                                                    ),
                                                    "This actual gaseous or Small flying "
                                                    "body cannot pass through the Wind Wall.",
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ]
                ),
            ),
        ],
    )


def _projectile_tool():
    """An ordinary weapon projectile crosses saved source wind, with no caller miss verdict."""
    return _environment_tool(
        "Resolve an actual weapon projectile's admitted straight scene segment through live wind.",
        [_param("actor_subject"), _param("target_subject")],
        [
            _set("_wind_projectile_deflected", False),
            _call("srd_information_scene_entity", {"subject": "{actor_subject}"}, POSITION_EXPORTS),
            _set("_wind_projectile_plane", "_info_scene_entity.metadata.plane"),
            _call(
                "resolve_srd_scene_sight_origin",
                {"observer_state": "{actor_subject}", "lasting_state": "state"},
                ("_sight_origin",),
            ),
            _set("_wind_projectile_start", "_sight_origin"),
            _call(
                "srd_information_scene_entity", {"subject": "{target_subject}"}, POSITION_EXPORTS
            ),
            _require(
                "_info_scene_entity.metadata.plane == _wind_projectile_plane",
                "The projectile's actual target must share its launch plane.",
            ),
            _call(
                "resolve_srd_scene_sight_origin",
                {"observer_state": "{target_subject}", "lasting_state": "state"},
                ("_sight_origin",),
            ),
            _set("_wind_projectile_end", "_sight_origin"),
            *_areas(
                [
                    _when(
                        "_environment_area.mode == 'wind_wall' and "
                        "_environment_area.plane == _wind_projectile_plane",
                        [
                            _call(
                                "qualify_srd_wind_wall_source",
                                {
                                    "caster_state": "{_environment_area.caster}",
                                    "area_id": "{_environment_id}",
                                },
                                ("_sphere_source_current",),
                            ),
                            _when(
                                "_sphere_source_current == true",
                                [
                                    _call(
                                        "srd_information_segment_interval",
                                        {
                                            "start": "{_wind_projectile_start}",
                                            "end": "{_wind_projectile_end}",
                                            "volume": "{_environment_area.volume}",
                                        },
                                        ("_segment_hit", "_segment_entry", "_segment_exit"),
                                    ),
                                    _when(
                                        "_segment_hit == true and _segment_entry > 0 "
                                        "and _segment_exit < 1",
                                        [
                                            _set(
                                                "_wind_ray",
                                                {
                                                    "volume": "_environment_area.volume",
                                                    "point": [
                                                        f"_wind_projectile_start[{i}] + "
                                                        f"(_wind_projectile_end[{i}] - "
                                                        f"_wind_projectile_start[{i}]) "
                                                        "* ((_segment_entry + _segment_exit) * 0.5)"
                                                        for i in range(3)
                                                    ],
                                                },
                                            ),
                                            _when(
                                                " and ".join(
                                                    f"_wind_ray.point[{i}] > "
                                                    f"_wind_ray.volume.min[{i}] and "
                                                    f"_wind_ray.point[{i}] < "
                                                    f"_wind_ray.volume.max[{i}]"
                                                    for i in range(3)
                                                ),
                                                [_set("_wind_projectile_deflected", True)],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )


def tools():
    result = _appearance_tools()
    result.update(
        {
            "disperse_srd_wind_wall_fog": _gas_tool(),
            "qualify_srd_wind_wall_passage": _passage_tool(),
            "resolve_srd_wind_wall_projectile": _projectile_tool(),
        }
    )
    return result


def bind_sources(changed):
    """Reuse the actual supported Wall placement clauses, without opacity or recurring damage."""
    cast = changed["cast_spell_environment"]
    cast["parameters"] += [
        _optional("wind_wall_length", "number", 50, minimum=0.01, maximum=50),
        _optional("wind_wall_height", "number", 15, minimum=0.01, maximum=15),
        _optional("wind_wall_thickness", "number", 1, minimum=0.01, maximum=1),
    ]
    additions = []
    for index, effect in enumerate(cast["effects"]):
        if effect.get("condition") != "_environment_profile.environment_mode == 'wall_fire'":
            continue
        if "_fire_cast_volume" not in str(effect) and "resolve_srd_wall_of_fire_scene" not in str(
            effect
        ):
            continue
        copy = _rename(deepcopy(effect))
        for effects in _effect_lists(copy["on_true"]):
            for entry in effects:
                value = entry.get("set", {}).get("value")
                if isinstance(value, str):
                    entry["set"]["value"] = value.replace(
                        "fire_wall_length", "wind_wall_length"
                    ).replace("fire_wall_side", "'none'")
                if (
                    entry.get("set", {}).get("field") == "_wind_cast_volume"
                    and value.get("kind") == "'box'"
                ):
                    volume = entry["set"]["value"]
                    volume["max"][2] = "_environment_cast_origin[2] + wind_wall_height"
                    for dim in (0, 1):
                        volume["min"][dim] = (
                            volume["min"][dim]
                            .replace("fire_wall_length", "wind_wall_length")
                            .replace(" - 0.5", " - wind_wall_thickness * 0.5")
                        )
                        volume["max"][dim] = (
                            volume["max"][dim]
                            .replace("fire_wall_length", "wind_wall_length")
                            .replace(" + 0.5", " + wind_wall_thickness * 0.5")
                        )
        if "resolve_srd_wind_wall_scene" in str(copy):
            copy["on_true"].insert(
                0,
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.source_sha256",
                    repr(SOURCE_SHA),
                ),
            )
            copy["on_true"].append(
                _call("disperse_srd_wind_wall_fog", {"area_id": "{_environment_cast_id}"})
            )
        if "_wind_support_volume" in str(copy):
            copy["on_true"].insert(
                0,
                _require(
                    "fire_wall_shape == 'straight'",
                    "Wind Wall currently supports an actual straight continuous ground path.",
                ),
            )
        additions.append((index + 1, copy))
    assert len(additions) == 3, (
        "Preserve the genuine pre-Con geometry, source fields and appearance boundaries."
    )
    for index, effect in reversed(additions):
        cast["effects"].insert(index, effect)
    index = changed["refresh_srd_environment_mode_indices"]
    index["effects"].insert(0, _set("state.has_wind_wall", False))
    loop = next(e["for_each"] for e in index["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when("_environment_area.mode == 'wind_wall'", [_set("state.has_wind_wall", True)])
    )
    for effects in _effect_lists(changed["dispel_srd_environment_source"]["effects"]):
        for effect in effects:
            requirement = effect.get("require", {})
            if "_environment_dispel_zone_found == true" in requirement.get("condition", ""):
                requirement["condition"] += " or _environment_dispel_source.mode == 'wind_wall'"
    public = changed["cast_spell"]
    names = {p["name"] for p in public["parameters"]}
    for suffix, maximum, default in (("length", 50, 50), ("height", 15, 15), ("thickness", 1, 1)):
        name = "environment_wind_wall_" + suffix
        if name not in names:
            public["parameters"].append(
                _optional(name, "number", default, minimum=0.01, maximum=maximum)
            )
    for effects in _effect_lists(public["effects"]):
        for effect in effects:
            if effect.get("call") == "cast_spell_environment":
                for suffix in ("length", "height", "thickness"):
                    effect["params"]["wind_wall_" + suffix] = (
                        "{environment_wind_wall_" + suffix + "}"
                    )
    changed["refresh_srd_environment_wind"]["effects"].append(
        _when(
            "state.has_wind_wall == true",
            _areas(
                [
                    _when(
                        "_environment_area.mode == 'wind_wall'",
                        [
                            _call("disperse_srd_wind_wall_fog", {"area_id": "{_environment_id}"}),
                        ],
                    ),
                ]
            ),
        )
    )


def bind_runtime_consumers(text):
    """Attach source permission and automatic missile miss at final actual shared owners."""
    import yaml

    if __package__:
        from .srd_timed_senses import _replace_tools
    else:
        from srd_timed_senses import _replace_tools

    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    changed = {}
    name = "move_srd_scene_creature_environment_segment"
    if name not in runtime["tools"]:
        name = "move_srd_scene_creature_node"
    movement = deepcopy(runtime["tools"][name])
    if "qualify_srd_wind_wall_passage" not in str(movement["effects"]):
        before = next(
            i
            for i, e in enumerate(movement["effects"])
            if e.get("call") == "srd_information_movement_cost"
        )
        movement["effects"].insert(
            before,
            _when(
                "lasting_state.has_wind_wall == true",
                [
                    _call(
                        "qualify_srd_wind_wall_passage",
                        {
                            "actor_state": "{_actor_state_id}",
                            "start": "{_move_origin}",
                            "end": "{destination}",
                            "mode": "{mode}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    )
                ],
            ),
        )
        changed[name] = movement
    for name in ("resolve_combat_attack", "resolve_combat_attack_followup"):
        if name not in runtime["tools"]:
            continue
        attack = deepcopy(runtime["tools"][name])
        if "resolve_srd_wind_wall_projectile" in str(attack["effects"]):
            continue
        # Ammunition is an actual canonical weapon fact; spell missiles and named
        # Giant/siege adaptations never enter this ordinary-weapon branch.
        before = next(
            i
            for i, e in enumerate(attack["effects"])
            if e.get("set", {}).get("field") == "_critical_hit"
        )
        attack["effects"].insert(
            before,
            _when(
                "lasting_state.has_wind_wall == true and attack_mode == 'ranged' and "
                "weapon_info.ammunition_type != null",
                [
                    _call(
                        "resolve_srd_wind_wall_projectile",
                        {
                            "actor_subject": "{_actor_state_id}",
                            "target_subject": "{_target_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ("_wind_projectile_deflected",),
                    ),
                    _when(
                        "_wind_projectile_deflected == true", [_set("_attack_outcome", "'miss'")]
                    ),
                ],
            ),
        )
        changed[name] = attack
    return _replace_tools(text, changed)
