"""Show explicitly known, qualified pending spell areas without resolving the spell."""

from copy import deepcopy

from .srd_control_behaviors import _each, _push
from .srd_lite_retained_area_map import TAG as RETAINED_TAG
from .srd_lite_retained_area_map import _zone_changes
from .srd_spell_environment import _environment_tool
from .srd_timed_actor_defenses import _call, _set

REFRESH = "refresh_srd_lite_pending_area_preview"
SHAPES = "_lite_pending_area_shapes"
REVISION = "_lite_pending_area_scene_revision"
MAP = "_lite_pending_area_map_id"
TAG = "srd_lite_pending_area_overlay"
CAPSULE = "area_preview"
LEAVES = {
    "collect_srd_cone_creature_targets": "_cone_area",
    "qualify_srd_retained_ice_storm_geometry": "_retained_ice_area",
    "qualify_srd_lite_fire_storm_cubes": "_fire_cube_area",
}
SPHERE = "collect_srd_sunburst_creature_targets"
HEALING = "qualify_srd_lite_mass_cure_sphere"
FORWARDERS = (
    "qualify_srd_lite_instant_combat_area",
    "qualify_srd_lite_control_combat_area",
    "qualify_srd_lite_flame_strike_combat_cylinder",
)
LABELLED_SPELLS = {
    "acid_splash",
    "burning_hands",
    "circle_of_death",
    "fireball",
    "lightning_bolt",
    "fear",
    "sleep",
    "flame_strike",
    "ice_storm",
    "mass_cure_wounds",
    "fire_storm",
}
GUIDANCE = (
    " In combat, known_area=true also displays a qualified pending area while its "
    "casting reaction window is open. Set it only when the player knows the actual "
    "chosen area, including any relocated origin. The preview does not reveal "
    "creatures, determine affected targets, spend a slot, or resolve the spell. "
    "Cone and Line displays show their exact XY footprint, clipped at map bounds; "
    "height still affects actual recipients."
)


def _lists(rows):
    yield rows
    for row in rows:
        for branch in ("on_true", "on_false"):
            yield from _lists(row.get(branch, []))
        if "for_each" in row:
            yield from _lists(row["for_each"]["do"])


def _when(condition, on_true, on_false=None):
    result = {"condition": condition, "on_true": on_true}
    if on_false is not None:
        result["on_false"] = on_false
    return result


def _value(shape):
    """Copy the query's intent expressions, never its private spatial result rows."""
    result = {}
    for key, value in shape.items():
        if isinstance(value, str):
            result[key] = (
                value[1:-1]
                if value.startswith("{") and value.endswith("}")
                else (repr(value) if key == "kind" else value)
            )
        else:
            result[key] = deepcopy(value)
    return result


def _export(row):
    """Preserve the owner's existing list or renamed dictionary exports."""
    exports = row.setdefault("export_context", {})
    if isinstance(exports, list):
        exports.extend(name for name in (SHAPES, REVISION, MAP) if name not in exports)
    elif isinstance(exports, dict):
        exports.update({name: name for name in (SHAPES, REVISION, MAP)})
    else:
        raise ValueError("Keep the declared private call export contract")


def _capture_query(owner, store):
    """Snapshot each actual queried shape after native admission, including relocated cubes."""
    count = 0
    for rows in list(_lists(owner["effects"])):
        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            query = row.get("spatial_query", {})
            if row.get("store_as") == store and query.get("operation") == "area":
                rows[index + 1 : index + 1] = [
                    _push(SHAPES, _value(query["query"]["shape"])),
                    _set(REVISION, "lasting_state.spatial_scene.map.revision"),
                    _set(MAP, "lasting_state.spatial_scene.map.id"),
                ]
                count += 1
    if count not in (1, 2):
        raise ValueError("Keep the demonstrated actual area-query seam: " + store)
    owner["effects"][0:0] = _empty_capture()


def _empty_capture():
    return [_set(SHAPES, []), _set(REVISION, None), _set(MAP, None)]


def _capture_sphere(owner, center, radius):
    owner["effects"].extend(
        [
            _set(SHAPES, [{"kind": "'sphere'", "center": center, "radius": radius}]),
            _set(REVISION, "lasting_state.spatial_scene.map.revision"),
            _set(MAP, "lasting_state.spatial_scene.map.id"),
        ]
    )


def _zone(shape, suffix, extent=False, exact_geometry=False):
    """Create an inert public display row with no terrain, hazard, or source metadata."""
    label = "{_preview_capsule.label} · pending preview"
    notice = "Map shows the XY footprint; height still affects actual recipients"
    if exact_geometry:
        notice = "Exact XY footprint, clipped at map bounds; height still affects actual recipients"
    elif extent:
        label += " · extent only"
        notice = "Bounding extent only; the engine resolves the actual three-dimensional area"
    player = {
        "label": "_preview_label",
        "kind": "'spell_area_preview'",
        "state": "'Pending; spell has not resolved'",
        "interactive": False,
        "height_label": repr(notice),
    }
    if exact_geometry:
        player["display_geometry"] = "_preview_shape"
    return [
        _call(
            "format_srd_information_projection", {"message": label}, ("_info_projection_message",)
        ),
        _set("_preview_label", "_info_projection_message"),
        _call(
            "format_srd_information_projection",
            {"message": "pending-area:{_preview_action_id}:{_preview_shape_index}:" + suffix},
            ("_info_projection_message",),
        ),
        _push(
            "_map_next_zones",
            {
                "id": "_info_projection_message",
                "map_id": "state.spatial_scene.map.id",
                "shape": shape,
                "metadata": {
                    TAG: True,
                    "overlay_pending_id": "_preview_action_id",
                    "player": player,
                },
            },
        ),
    ]


def _clipped_box(extent=False, exact_geometry=False):
    lower = "state.spatial_scene.map.bounds.min"
    upper = "state.spatial_scene.map.bounds.max"
    return [
        _set("_preview_box_min", [f"max({lower}[{i}], _preview_min[{i}])" for i in range(3)]),
        _set("_preview_box_max", [f"min({upper}[{i}], _preview_max[{i}])" for i in range(3)]),
        *(
            [
                _when(
                    "_preview_box_min[2] >= _preview_box_max[2]",
                    [
                        _set(
                            "_preview_box_min",
                            ["_preview_box_min[0]", "_preview_box_min[1]", f"{lower}[2]"],
                        ),
                        _set(
                            "_preview_box_max",
                            ["_preview_box_max[0]", "_preview_box_max[1]", f"{upper}[2]"],
                        ),
                    ],
                ),
            ]
            if exact_geometry
            else []
        ),
        _when(
            " and ".join(f"_preview_box_min[{i}] < _preview_box_max[{i}]" for i in range(3)),
            _zone(
                {"kind": "'box'", "min": "_preview_box_min", "max": "_preview_box_max"},
                "bounds" if extent else "box",
                extent,
                exact_geometry,
            ),
        ),
    ]


def _round():
    """Render round XY footprints; a clipped round shape becomes labelled extent-only."""
    lower = "state.spatial_scene.map.bounds.min"
    upper = "state.spatial_scene.map.bounds.max"
    return [
        _set(
            "_preview_min",
            [
                "_preview_shape.center[0] - _preview_shape.radius",
                "_preview_shape.center[1] - _preview_shape.radius",
                "_preview_bottom",
            ],
        ),
        _set(
            "_preview_max",
            [
                "_preview_shape.center[0] + _preview_shape.radius",
                "_preview_shape.center[1] + _preview_shape.radius",
                "_preview_top",
            ],
        ),
        _set("_preview_bottom", f"max({lower}[2], _preview_bottom)"),
        _set("_preview_top", f"min({upper}[2], _preview_top)"),
        _when(
            "_preview_top > _preview_bottom",
            [
                _when(
                    " and ".join(
                        f"_preview_min[{i}] >= {lower}[{i}] and _preview_max[{i}] <= {upper}[{i}]"
                        for i in (0, 1)
                    ),
                    _zone(
                        {
                            "kind": "'cylinder'",
                            "center": [
                                "_preview_shape.center[0]",
                                "_preview_shape.center[1]",
                                "_preview_bottom",
                            ],
                            "radius": "_preview_shape.radius",
                            "height": "_preview_top - _preview_bottom",
                        },
                        "round",
                    ),
                    _clipped_box(True),
                ),
            ],
        ),
    ]


def _cone_bounds():
    """Keep an inert admitted envelope alongside the genuine Cone display geometry."""
    return [
        _set(
            "_preview_axis_norm",
            "(" + " + ".join(f"_preview_shape.direction[{i}] ** 2" for i in range(3)) + ") ** 0.5",
        ),
        _set(
            "_preview_axis",
            [f"_preview_shape.direction[{i}] * _preview_axis_norm ** -1" for i in range(3)],
        ),
        _set(
            "_preview_end",
            [
                f"_preview_shape.apex[{i}] + _preview_axis[{i}] * _preview_shape.length"
                for i in range(3)
            ],
        ),
        _set(
            "_preview_extent",
            [
                f"_preview_shape.radius * max(0, 1 - _preview_axis[{i}] ** 2) ** 0.5"
                for i in range(3)
            ],
        ),
        _set(
            "_preview_min",
            [
                f"min(_preview_shape.apex[{i}], _preview_end[{i}] - _preview_extent[{i}])"
                for i in range(3)
            ],
        ),
        _set(
            "_preview_max",
            [
                f"max(_preview_shape.apex[{i}], _preview_end[{i}] + _preview_extent[{i}])"
                for i in range(3)
            ],
        ),
        *_clipped_box(True, True),
    ]


def _prism_bounds():
    """Keep the Line's genuine frame and an inert admitted presentation envelope."""
    cross = ((1, 2), (2, 0), (0, 1))
    return [
        _set(
            "_preview_axis_norm",
            "(" + " + ".join(f"_preview_shape.direction[{i}] ** 2" for i in range(3)) + ") ** 0.5",
        ),
        _set(
            "_preview_axis",
            [f"_preview_shape.direction[{i}] * _preview_axis_norm ** -1" for i in range(3)],
        ),
        _set(
            "_preview_dot",
            " + ".join(f"_preview_shape.up[{i}] * _preview_axis[{i}]" for i in range(3)),
        ),
        _set(
            "_preview_up",
            [f"_preview_shape.up[{i}] - _preview_dot * _preview_axis[{i}]" for i in range(3)],
        ),
        _set(
            "_preview_up_norm",
            "(" + " + ".join(f"_preview_up[{i}] ** 2" for i in range(3)) + ") ** 0.5",
        ),
        _when(
            "_preview_up_norm > 0",
            [
                _set(
                    "_preview_up", [f"_preview_up[{i}] * _preview_up_norm ** -1" for i in range(3)]
                ),
                _set(
                    "_preview_side",
                    [
                        f"_preview_axis[{a}] * _preview_up[{b}] - "
                        f"_preview_axis[{b}] * _preview_up[{a}]"
                        for a, b in cross
                    ],
                ),
                _set(
                    "_preview_end",
                    [
                        f"_preview_shape.origin[{i}] + _preview_axis[{i}] * _preview_shape.length"
                        for i in range(3)
                    ],
                ),
                _set(
                    "_preview_extent",
                    [
                        f"abs(_preview_side[{i}]) * _preview_shape.width * 0.5 + "
                        f"abs(_preview_up[{i}]) * _preview_shape.height * 0.5"
                        for i in range(3)
                    ],
                ),
                _set(
                    "_preview_min",
                    [
                        f"min(_preview_shape.origin[{i}], _preview_end[{i}]) - _preview_extent[{i}]"
                        for i in range(3)
                    ],
                ),
                _set(
                    "_preview_max",
                    [
                        f"max(_preview_shape.origin[{i}], _preview_end[{i}]) + _preview_extent[{i}]"
                        for i in range(3)
                    ],
                ),
                *_clipped_box(True, True),
            ],
        ),
    ]


def _display_shapes():
    return [
        _set("_preview_shapes", "_preview_capsule.shapes"),
        _set("_preview_shape_index", 0),
        _each(
            "_preview_shapes",
            "_preview_shape",
            [
                _when(
                    "_preview_shape.kind == 'sphere'",
                    [
                        _set("_preview_bottom", "_preview_shape.center[2] - _preview_shape.radius"),
                        _set("_preview_top", "_preview_shape.center[2] + _preview_shape.radius"),
                        *_round(),
                    ],
                ),
                _when(
                    "_preview_shape.kind == 'cylinder'",
                    [
                        _set("_preview_bottom", "_preview_shape.center[2]"),
                        _set("_preview_top", "_preview_shape.center[2] + _preview_shape.height"),
                        *_round(),
                    ],
                ),
                _when(
                    "_preview_shape.kind == 'box'",
                    [
                        _set("_preview_min", "_preview_shape.min"),
                        _set("_preview_max", "_preview_shape.max"),
                        *_clipped_box(),
                    ],
                ),
                _when("_preview_shape.kind == 'cone'", _cone_bounds()),
                _when("_preview_shape.kind == 'prism'", _prism_bounds()),
                _set("_preview_shape_index", "_preview_shape_index + 1"),
            ],
        ),
    ]


def _pending_zone_changes():
    """Compare this owner's exact public geometry without widening retained-area comparisons."""
    changes = _zone_changes()
    next_geometry = "_map_next_zone.metadata.player.display_geometry"
    previous_geometry = "_map_previous_zone.metadata.player.display_geometry"
    geometry_changes = [
        _when(
            f"({next_geometry} == null and {previous_geometry} != null) or "
            f"({next_geometry} != null and {previous_geometry} == null)",
            [_set("_map_zones_changed", True)],
        ),
        _when(
            f"_map_zones_changed != true and {next_geometry} != null",
            [
                _set("_preview_next_geometry", next_geometry),
                _set("_preview_previous_geometry", previous_geometry),
                _when(
                    "_preview_next_geometry.kind != _preview_previous_geometry.kind",
                    [_set("_map_zones_changed", True)],
                ),
                *[
                    _when(
                        f"_map_zones_changed != true and _preview_next_geometry.kind == '{kind}'",
                        [
                            _when(
                                f"_preview_next_geometry.{field} != "
                                f"_preview_previous_geometry.{field}",
                                [_set("_map_zones_changed", True)],
                            )
                            for field in fields
                        ],
                    )
                    for kind, fields in (
                        (
                            "cone",
                            [
                                *[
                                    f"{vector}[{axis}]"
                                    for vector in ("apex", "direction")
                                    for axis in range(3)
                                ],
                                "length",
                                "radius",
                            ],
                        ),
                        (
                            "prism",
                            [
                                *[
                                    f"{vector}[{axis}]"
                                    for vector in ("origin", "direction", "up")
                                    for axis in range(3)
                                ],
                                "length",
                                "width",
                                "height",
                            ],
                        ),
                    )
                ],
            ],
        ),
    ]
    expected = "_map_zones_changed != true and _map_next_zone.metadata." + RETAINED_TAG + " == true"
    seams = [row for rows in _lists(changes) for row in rows if row.get("condition") == expected]
    if len(seams) != 1:
        raise ValueError("Keep the genuine owned-zone scalar comparison boundary")
    seams[0]["on_true"][0:0] = geometry_changes
    return changes


def _refresh_tool():
    """Rebuild only this binder's inert zones from still-open, explicitly known pending casts."""
    changes = _pending_zone_changes()

    def replace(value):
        if isinstance(value, str):
            return value.replace(".metadata." + RETAINED_TAG, ".metadata." + TAG)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    return _environment_tool(
        "Refresh explicitly known pending spell-area display without resolving mechanics.",
        [],
        [
            _when(
                "state.spatial_scene != null",
                [
                    _set("_map_next_zones", []),
                    _set("_preview_current_action_ids", []),
                    _set("_preview_old_zones", "state.spatial_scene.zones"),
                    _each(
                        "_preview_old_zones",
                        "_preview_old_zone",
                        [
                            _when(
                                "_preview_old_zone.metadata." + TAG + " != true",
                                [
                                    _push("_map_next_zones", "_preview_old_zone"),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "combat.encounter_active == true and state.pending_srd_actions != null "
                        "and state.pending_srd_action_stack != null",
                        [
                            _set("_preview_action_ids", "state.pending_srd_action_stack"),
                            _each(
                                "_preview_action_ids",
                                "_preview_action_id",
                                [
                                    _when(
                                        "_preview_action_id in state.pending_srd_actions",
                                        [
                                            _set(
                                                "_preview_action",
                                                "state.pending_srd_actions[_preview_action_id]",
                                            ),
                                            _when(
                                                "_preview_action.kind == 'spell_cast' and "
                                                "_preview_action.status == 'open' "
                                                "and _preview_action.area_preview != null",
                                                [
                                                    _set(
                                                        "_preview_capsule",
                                                        "_preview_action.area_preview",
                                                    ),
                                                    _when(
                                                        "_preview_capsule.known_area == true and "
                                                        "_preview_capsule.map_id == "
                                                        "state.spatial_scene.map.id and "
                                                        "_preview_capsule.basis_scene_revision == "
                                                        "state.spatial_scene.map.revision",
                                                        [
                                                            *_display_shapes(),
                                                            _push(
                                                                "_preview_current_action_ids",
                                                                "_preview_action_id",
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
                    *replace(changes),
                    _when(
                        "_map_zones_changed == true",
                        [
                            _set("state.spatial_scene.zones", "_map_next_zones"),
                            {
                                "increment": {
                                    "field": "state.spatial_scene.map.revision",
                                    "amount": 1,
                                }
                            },
                            {
                                "spatial_query": {
                                    "scene": "state.spatial_scene",
                                    "operation": "snapshot",
                                },
                                "store_as": "_preview_scene_snapshot",
                            },
                        ],
                    ),
                    _each(
                        "_preview_current_action_ids",
                        "_preview_current_action_id",
                        [
                            _set(
                                "state.pending_srd_actions.{_preview_current_action_id}.area_preview.basis_scene_revision",
                                "state.spatial_scene.map.revision",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def bind_pending_area_preview(runtime):
    """Bind after actual area qualifiers, retained known-area intent, and before private pruning."""
    tools = runtime["tools"]
    required = {
        *LEAVES,
        SPHERE,
        HEALING,
        "begin_srd_spell_action",
        "complete_srd_pending_action",
        "finish_srd_shared_action_settlements",
    }
    if REFRESH in tools or not required <= tools.keys():
        raise ValueError("Keep the unbound approved actual area and pending-action owners")
    begin = tools["begin_srd_spell_action"]
    if not any(p.get("name") == "known_area" for p in begin["parameters"]):
        raise ValueError("Bind explicit retained-area knowledge before pending previews")
    for name, store in LEAVES.items():
        _capture_query(tools[name], store)
    _capture_sphere(tools[SPHERE], "_sunburst_origin", "radius_feet")
    _capture_sphere(tools[HEALING], "origin", "definitions.spells.mass_cure_wounds.area_size_feet")
    producers = {*LEAVES, SPHERE, HEALING}
    for name in FORWARDERS:
        if name not in tools:
            continue
        owner = tools[name]
        calls = [
            row for rows in _lists(owner["effects"]) for row in rows if row.get("call") in producers
        ]
        if not calls:
            raise ValueError("Keep the actual private area qualification calls: " + name)
        for row in calls:
            _export(row)
        owner["effects"][0:0] = _empty_capture()
        producers.add(name)
    for rows in _lists(begin["effects"]):
        for row in rows:
            if row.get("call") in producers:
                _export(row)
    begin["effects"][0:0] = _empty_capture()
    labels = [
        _when(
            "_preview_saved_action.spell == " + repr(spell),
            [
                _set("_preview_saved_label", repr(spell.replace("_", " ").title())),
            ],
        )
        for spell in sorted(LABELLED_SPELLS & runtime["definitions"]["spells"].keys())
    ]
    begin["effects"].append(
        _when(
            "combat.encounter_active == true and known_area == true and len(" + SHAPES + ") > 0 "
            "and lasting_state.pending_srd_actions != null and "
            "_pending_action_id in lasting_state.pending_srd_actions",
            [
                _set(
                    "_preview_saved_action", "lasting_state.pending_srd_actions[_pending_action_id]"
                ),
                _when(
                    "_preview_saved_action.kind == 'spell_cast' and "
                    "_preview_saved_action.status == 'open'",
                    [
                        _set("_preview_saved_label", "'Spell area'"),
                        *labels,
                        _set(
                            "lasting_state.pending_srd_actions.{_pending_action_id}." + CAPSULE,
                            {
                                "known_area": True,
                                "map_id": MAP,
                                "basis_scene_revision": REVISION,
                                "label": "_preview_saved_label",
                                "shapes": SHAPES,
                            },
                        ),
                    ],
                ),
            ],
        )
    )
    begin["description"] += GUIDANCE
    lifecycle = {
        "begin_srd_spell_action",
        "complete_srd_pending_action",
        "finish_srd_shared_action_settlements",
    }
    lifecycle.update(
        name for name in ("resolve_srd_counterspell", "end_combat_encounter") if name in tools
    )
    for name in lifecycle:
        tools[name]["effects"].append(_call(REFRESH, {}))
    tools[REFRESH] = _refresh_tool()
    return sorted(producers | lifecycle | {REFRESH})
