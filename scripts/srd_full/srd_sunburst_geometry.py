"""Sunburst derives its chosen-point Sphere from actual admitted creature volumes."""

import re
from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _push
else:
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _push


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if isinstance(effect.get(key), list):
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _intent():
    return [
        _parameter(
            "sunburst_origin",
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            min_items=0,
            max_items=3,
        ),
        _parameter("sunburst_plane", required=False, default=""),
    ]


def _contains(frame, point="origin"):
    return (
        f"abs({point}[0] - {frame}.position[0]) <= {frame}.footprint.half_extents[0] "
        f"and abs({point}[1] - {frame}.position[1]) <= {frame}.footprint.half_extents[1] "
        f"and {point}[2] >= {frame}.position[2] and "
        f"{point}[2] <= {frame}.position[2] + {frame}.height"
    )


def _nearest(frame, point):
    return [
        f"max({frame}.position[0] - {frame}.footprint.half_extents[0], "
        f"min({point}[0], {frame}.position[0] + {frame}.footprint.half_extents[0]))",
        f"max({frame}.position[1] - {frame}.footprint.half_extents[1], "
        f"min({point}[1], {frame}.position[1] + {frame}.footprint.half_extents[1]))",
        f"max({frame}.position[2], min({point}[2], {frame}.position[2] + {frame}.height))",
    ]


def _sphere_tool():
    """Closed Sphere/AABB intersection uses the nearest actual body point, including height."""
    return _tool_scene(
        "Validate Sunburst's actual chosen point and collect every supported living creature.",
        [_entity("caster_state"), *_intent()],
        [
            _require(
                "len(sunburst_origin) == 3", "Choose Sunburst's actual three-dimensional point."
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_sunburst_source", "_info_scene_entity"),
            _require(
                "_sunburst_source.footprint.kind == 'box' and "
                "_sunburst_source.metadata.plane != null",
                "Sunburst currently needs its actual caster box and plane.",
            ),
            _require(
                "sunburst_plane == '' or sunburst_plane == _sunburst_source.metadata.plane",
                "Sunburst's chosen point must be on the caster's actual plane.",
            ),
            _set("_sunburst_plane", "_sunburst_source.metadata.plane"),
            _set("_sunburst_origin", "sunburst_origin"),
            _require(
                "sunburst_origin[0] >= state.spatial_scene.map.bounds.min[0] and "
                "sunburst_origin[0] <= state.spatial_scene.map.bounds.max[0] and "
                "sunburst_origin[1] >= state.spatial_scene.map.bounds.min[1] and "
                "sunburst_origin[1] <= state.spatial_scene.map.bounds.max[1] and "
                "sunburst_origin[2] >= state.spatial_scene.map.bounds.min[2] and "
                "sunburst_origin[2] <= state.spatial_scene.map.bounds.max[2]",
                "Sunburst's point must be inside the admitted scene.",
            ),
            _set("_sunburst_range_start", _nearest("_sunburst_source", "sunburst_origin")),
            _require(
                "(sunburst_origin[0] - _sunburst_range_start[0]) ** 2 + "
                "(sunburst_origin[1] - _sunburst_range_start[1]) ** 2 + "
                "(sunburst_origin[2] - _sunburst_range_start[2]) ** 2 <= 150 ** 2",
                "Sunburst's actual chosen point is beyond150 feet.",
            ),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_sunburst_range_start}",
                    "end": "{sunburst_origin}",
                    "rule": "solid",
                    "plane": "{_sunburst_plane}",
                },
                ("_information_blocked",),
            ),
            _require(
                "_information_blocked != true", "A solid barrier blocks Sunburst's chosen point."
            ),
            *_snapshot(),
            _set("_sunburst_sphere_targets", []),
            _set("_sunburst_seen_actors", []),
            _set("_sunburst_scene_rows", "state.spatial_scene.entities"),
            _loop(
                "_sunburst_scene_rows",
                "_sunburst_target",
                [
                    _when(
                        "_sunburst_target.active == true and "
                        "_sunburst_target.metadata.participant_ref != null and "
                        "_sunburst_target.metadata.is_object != true and "
                        "_sunburst_target.metadata.creature_type != 'spell_force' and "
                        "(_sunburst_target.metadata.plane == _sunburst_plane or "
                        "_sunburst_target.metadata.plane == null)",
                        [
                            _call(
                                "check_srd_cone_creature_actor",
                                {"target_state": "{_sunburst_target.metadata.participant_ref}"},
                                ("_cone_actual_creature",),
                            ),
                            _when(
                                "_cone_actual_creature == true",
                                [
                                    _require(
                                        "_sunburst_target.metadata.plane != null",
                                        "Sunburst needs each actual creature's admitted plane.",
                                    ),
                                    _require(
                                        "_sunburst_target.metadata.participant_ref not in "
                                        "_sunburst_seen_actors",
                                        "Sunburst creature has ambiguous scene proxies.",
                                    ),
                                    _push(
                                        "_sunburst_seen_actors",
                                        "_sunburst_target.metadata.participant_ref",
                                    ),
                                    _require(
                                        "_sunburst_target.footprint.kind == 'box'",
                                        "Sunburst currently supports actual creature box volumes.",
                                    ),
                                    _set(
                                        "_sunburst_near",
                                        _nearest("_sunburst_target", "sunburst_origin"),
                                    ),
                                    _when(
                                        "(_sunburst_near[0] - sunburst_origin[0]) ** 2 + "
                                        "(_sunburst_near[1] - sunburst_origin[1]) ** 2 + "
                                        "(_sunburst_near[2] - sunburst_origin[2]) ** 2 <= 60 ** 2",
                                        [
                                            _require(
                                                "_sunburst_target.metadata.participant_ref not in "
                                                "_sunburst_sphere_targets",
                                                "Sunburst creature has ambiguous scene proxies.",
                                            ),
                                            _require(
                                                "len(_sunburst_sphere_targets) < 64",
                                                "Sunburst exceeds64 supported creatures.",
                                            ),
                                            _push(
                                                "_sunburst_sphere_targets",
                                                "_sunburst_target.metadata.participant_ref",
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


def _point_cover(tools):
    """Reuse physical Cover, excluding the caster body only when it actually contains the origin."""
    collect = deepcopy(tools["collect_srd_spell_area_cover_targets"])
    cover = deepcopy(tools["qualify_srd_spell_area_target_cover"])
    cover["parameters"].append(
        _parameter("source_frame", "object", from_state="state.spatial_scene")
    )
    cover.setdefault("pre_conditions", []).append(
        {
            "condition": "source_frame.id == source_id and source_frame.footprint.kind == 'box'",
            "error": "Sunburst Cover needs this action's actual caster scene frame.",
        }
    )
    inside = _contains("source_frame")
    for effects in _lists(cover["effects"]):
        for effect in effects:
            condition = effect.get("condition", "")
            if "_breath_path_hit.id != source_id" in condition:
                effect["condition"] = condition.replace(
                    "_breath_path_hit.id != source_id",
                    f"(_breath_path_hit.id != source_id or not ({inside}))",
                )
    # This private consumer selects the generic point algorithm; Cone's optimized path is untouched.
    collect["parameters"] = [
        p for p in collect["parameters"] if p["name"] not in {"axis", "source_frames"}
    ]
    collect = _replace_names(
        collect, {"axis": "_sunburst_cover_axis", "source_frames": "_sunburst_cover_frames"}
    )
    collect["effects"][:0] = [
        _set("_sunburst_cover_axis", []),
        _set("_sunburst_cover_frames", []),
    ]
    for effects in list(_lists(collect["effects"])):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if (
                effect.get("set", {}).get("field") == "_breath_source_scene_id"
                and effect["set"]["value"] == "_info_scene_entity.id"
            ):
                effects.insert(index + 1, _set("_sunburst_cover_source", "_info_scene_entity"))
            if effect.get("call") == "qualify_srd_spell_area_target_cover":
                effect["call"] = "qualify_srd_sunburst_target_cover"
                effect["params"]["source_frame"] = "{_sunburst_cover_source}"
            condition = effect.get("condition", "")
            if "_breath_body.id != _breath_source_scene_id" in condition:
                effect["condition"] = condition.replace(
                    "_breath_body.id != _breath_source_scene_id",
                    "(_breath_body.id != _breath_source_scene_id or not ("
                    + _contains("_breath_body")
                    + "))",
                )
    return {
        "collect_srd_sunburst_cover_targets": collect,
        "qualify_srd_sunburst_target_cover": cover,
    }


def _replace_names(value, replacements):
    if isinstance(value, str):
        for old, actual in replacements.items():
            value = re.sub(r"\b" + old + r"\b", actual, value)
        return value
    if isinstance(value, list):
        return [_replace_names(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_names(item, replacements) for key, item in value.items()}
    return value


def compose_sunburst_geometry(tools, changed):
    """Route the real paid producer through point qualification before native area damage."""
    changed["collect_srd_sunburst_creature_targets"] = _sphere_tool()
    changed.update(_point_cover(tools))
    target = deepcopy(tools["resolve_spell_area_save_damage_target"])
    for effects in list(_lists(target["effects"])):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get("call") == "install_srd_timed_condition_source":
                effect.setdefault("export_context", {})["_sunburst_blinded_source_id"] = (
                    "_timed_condition_source_id"
                )
                if not any(
                    "_sunburst_blinded_source_id" in str(row)
                    and row.get("condition") == "spell == 'sunburst'"
                    for row in effects
                ):
                    effects.insert(
                        index + 1,
                        _when(
                            "spell == 'sunburst'",
                            [
                                _set(
                                    "state.timed_condition_sources.{_sunburst_blinded_source_id}.dispel_ongoing",
                                    False,
                                ),
                            ],
                        ),
                    )
    changed["resolve_spell_area_save_damage_target"] = target
    area = deepcopy(tools["cast_spell_instantaneous_area_save_damage"])
    body = area["effects"]
    if len(body) == 1 and body[0].get("condition") == "spell == 'prismatic_spray'":
        body = body[0]["on_false"]
    area["parameters"] = [
        p
        for p in area["parameters"]
        if p["name"]
        not in {
            "targets",
            "half_cover_targets",
            "three_quarters_cover_targets",
            "origin_distance",
        }
    ] + _intent()
    area["pre_conditions"] = [
        p
        for p in area["pre_conditions"]
        if "origin_distance" not in p["condition"] and "spell != 'sunburst'" not in p["condition"]
    ]
    area["pre_conditions"].append(
        {
            "condition": "spell == 'sunburst' and range_feet == 150 and "
            "area_shape == 'sphere' and area_size_feet == 60",
            "error": "This source owner only admits Sunburst's printed Sphere.",
        }
    )
    area["effects"] = [
        _call(
            "collect_srd_sunburst_creature_targets",
            {
                "caster_state": "{_caster_state_id}",
                "sunburst_origin": "{sunburst_origin}",
                "sunburst_plane": "{sunburst_plane}",
            },
            ("_sunburst_sphere_targets", "_sunburst_origin", "_sunburst_plane"),
        ),
        _call(
            "collect_srd_sunburst_cover_targets",
            {
                "source_state": "{_caster_state_id}",
                "targets": "{_sunburst_sphere_targets}",
                "origin": "{_sunburst_origin}",
            },
            ("_srd_area_targets",),
        ),
        _set("_sunburst_half_cover", []),
        _set("_sunburst_three_quarters", []),
        _call(
            "dispel_srd_illumination_darkness_area",
            {"origin": "{_sunburst_origin}", "plane": "{_sunburst_plane}", "radius": 60},
        ),
    ] + _replace_names(
        body,
        {
            "targets": "_srd_area_targets",
            "half_cover_targets": "_sunburst_half_cover",
            "three_quarters_cover_targets": "_sunburst_three_quarters",
        },
    )
    area = _replace_names(
        area,
        {
            "half_cover_targets": "_sunburst_half_cover",
            "three_quarters_cover_targets": "_sunburst_three_quarters",
        },
    )
    changed["cast_srd_sunburst"] = area
    allowed = {p["name"] for p in area["parameters"]}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(changed.get(name, tools[name]))
        for parameter in _intent():
            if not any(p["name"] == parameter["name"] for p in tool["parameters"]):
                tool["parameters"].append(parameter)
        if "cast_srd_sunburst" not in str(tool["effects"]):
            for effects in list(_lists(tool["effects"])):
                for index, effect in enumerate(effects):
                    if effect.get("call") == "cast_spell_instantaneous_area_save_damage":
                        call = deepcopy(effect)
                        call["call"] = "cast_srd_sunburst"
                        call["params"] = {n: v for n, v in call["params"].items() if n in allowed}
                        call["params"].update({p["name"]: "{" + p["name"] + "}" for p in _intent()})
                        effects[index] = {
                            "condition": "spell == 'sunburst'",
                            "on_true": [call],
                            "on_false": [effect],
                        }
        changed[name] = tool
    legacy = deepcopy(
        changed.get(
            "cast_spell_instantaneous_area_save_damage",
            tools["cast_spell_instantaneous_area_save_damage"],
        )
    )
    if not any(p["condition"] == "spell != 'sunburst'" for p in legacy["pre_conditions"]):
        legacy["pre_conditions"].append(
            {
                "condition": "spell != 'sunburst'",
                "error": "Use Sunburst's actual chosen-point source owner.",
            }
        )
    changed["cast_spell_instantaneous_area_save_damage"] = legacy
