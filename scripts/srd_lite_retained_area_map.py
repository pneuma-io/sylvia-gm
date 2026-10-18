"""Keep explicitly known retained spell footprints on the saved combat scene."""

from copy import deepcopy

from .srd_control_behaviors import _each, _push
from .srd_spell_environment import _environment_tool
from .srd_timed_actor_defenses import _call, _set

REFRESH = "refresh_srd_lite_retained_area_map"
KNOWN = "known_area"
TAG = "srd_lite_retained_overlay"
SPHERE_AREAS = {
    "cloud_of_daggers": ("lite_daggers", "Cloud of Daggers"),
    "insect_plague": ("insects", "Insect Plague"),
    "stinking_cloud": ("stinking_cloud", "Stinking Cloud"),
}
EXTENT_AREAS = {"fog_cloud": ("fog", "Fog Cloud")}
ENVIRONMENT_AREAS = ("web", *SPHERE_AREAS, *EXTENT_AREAS, "sleet_storm")
MEMORY_CAST = "cast_spell_memory_dialogue_spell"
MEMORY_SETTLE = "settle_srd_dialogue_source"
CHOICE_MERGE = "merge(_retained_cast_tail_choices[0], _lite_object_retained_facts)"
GUIDANCE = (
    "\nFor Web, Cloud of Daggers, Insect Plague, Stinking Cloud, Fog Cloud, Sleet Storm "
    "or Zone of Truth, set known_area=true "
    "only when the player knows the "
    "committed spell area from observing the cast or another established fact. Omitted "
    "or false means hidden on the combat map. This fact does not reveal its caster or "
    "any creature, authorize movement, or change spell mechanics."
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


def _parameter():
    return {"name": KNOWN, "type": "boolean", "required": False, "default": False}


def _public_metadata(label, state):
    return {
        TAG: True,
        "overlay_area_id": "_map_area_id",
        "overlay_source_sequence": "_map_source.sequence",
        "player": {
            "label": repr(label),
            "kind": "'spell_area'",
            "state": state,
            "interactive": False,
        },
    }


def _zone(shape, metadata, suffix, identity="retained-spell:{_map_source.sequence}"):
    return [
        _call(
            "format_srd_information_projection",
            {"message": identity + ":" + suffix},
            ("_info_projection_message",),
        ),
        _push(
            "_map_next_zones",
            {
                "id": "_info_projection_message",
                "map_id": "state.spatial_scene.map.id",
                "shape": shape,
                "metadata": metadata,
            },
        ),
    ]


def _round_metadata(label, state, height_label, identity_field):
    return {
        TAG: True,
        "overlay_area_id": "_map_area_id",
        identity_field: "_map_source."
        + ("sequence" if identity_field == "overlay_source_sequence" else "cast_id"),
        "player": {
            "label": label,
            "kind": "'spell_area'",
            "state": state,
            "interactive": False,
            "height_label": height_label,
        },
    }


def _round_zones():
    """Share one box/cylinder display body across explicitly selected native round sources."""
    lower = "state.spatial_scene.map.bounds.min"
    upper = "state.spatial_scene.map.bounds.max"
    clipped_box = {
        "kind": "'box'",
        "min": [
            *[
                f"max({lower}[{axis}], _map_round_origin[{axis}] - _map_round_radius)"
                for axis in (0, 1)
            ],
            "_map_round_lower",
        ],
        "max": [
            *[
                f"min({upper}[{axis}], _map_round_origin[{axis}] + _map_round_radius)"
                for axis in (0, 1)
            ],
            "_map_round_upper",
        ],
    }
    identity = "{_map_round_prefix}:{_map_round_identity}"
    return [
        _when(
            "_map_source.profile == 'environment'",
            [
                _set(
                    "_map_round_metadata",
                    _round_metadata(
                        "_map_round_profile.label",
                        "_map_round_state",
                        "_map_round_notice",
                        "overlay_source_sequence",
                    ),
                ),
                _set(
                    "_map_round_box_metadata",
                    _round_metadata(
                        "_map_round_profile.clipped_label",
                        "'Extent only'",
                        "_map_round_clipped_notice",
                        "overlay_source_sequence",
                    ),
                ),
            ],
            [
                _set(
                    "_map_round_metadata",
                    _round_metadata(
                        "_map_round_profile.label",
                        "_map_round_state",
                        "_map_round_notice",
                        "overlay_source_cast_id",
                    ),
                ),
                _set(
                    "_map_round_box_metadata",
                    _round_metadata(
                        "_map_round_profile.clipped_label",
                        "'Extent only'",
                        "_map_round_clipped_notice",
                        "overlay_source_cast_id",
                    ),
                ),
            ],
        ),
        _set("_map_round_lower", f"max({lower}[2], _map_round_bottom)"),
        _set("_map_round_upper", f"min({upper}[2], _map_round_top)"),
        _when(
            "_map_round_radius > 0 and _map_round_upper > _map_round_lower",
            [
                _when(
                    " and ".join(
                        f"_map_round_origin[{axis}] - _map_round_radius >= {lower}[{axis}] and "
                        f"_map_round_origin[{axis}] + _map_round_radius <= {upper}[{axis}]"
                        for axis in (0, 1)
                    ),
                    _zone(
                        {
                            "kind": "'cylinder'",
                            "center": [
                                "_map_round_origin[0]",
                                "_map_round_origin[1]",
                                "_map_round_lower",
                            ],
                            "radius": "_map_round_radius",
                            "height": "_map_round_upper - _map_round_lower",
                        },
                        "_map_round_metadata",
                        "{_map_round_suffix}",
                        identity,
                    ),
                    [
                        _when(
                            " and ".join(
                                f"_map_round_origin[{axis}] + _map_round_radius > "
                                f"{lower}[{axis}] and "
                                f"_map_round_origin[{axis}] - _map_round_radius < {upper}[{axis}]"
                                for axis in (0, 1)
                            ),
                            _zone(
                                clipped_box, "_map_round_box_metadata", "clipped-extent", identity
                            ),
                        )
                    ],
                )
            ],
        ),
    ]


def _round_profile(mode, label, *, extent=False, cylinder=False):
    """Trusted declarative spell/mode presentation, never labels supplied by saved sources."""
    family = "cylinder" if cylinder else "spherical"
    return {
        "mode": repr(mode),
        "label": repr(label + (" · source extent" if extent else " · " + family + " footprint")),
        "clipped_label": repr(
            label + (" · clipped source extent" if extent else " · clipped area bounds")
        ),
        "extent": extent,
        "cylinder": cylinder,
    }


def _select_round_source():
    """Select only approved sources, with the same saved geometry and live identity as mechanics."""
    return [
        _set("_map_round_selected", False),
        _set("_map_round_state", "'Active'"),
        _set("_map_round_notice", "'Spherical mechanical area; map shows XY footprint'"),
        _set("_map_round_clipped_notice", "'Extent only; sphere continues beyond map'"),
        _when(
            "_map_source.profile == 'environment'",
            [
                _set("_map_round_origin", "_map_source.origin"),
                _set("_map_round_radius", "_map_source.radius"),
                _set("_map_round_bottom", "_map_source.origin[2] - _map_source.radius"),
                _set("_map_round_top", "_map_source.origin[2] + _map_source.radius"),
                _set("_map_round_prefix", "'retained-spell'"),
                _set("_map_round_identity", "_map_source.sequence"),
                _set("_map_round_suffix", "'sphere-footprint'"),
                _set("_map_round_profile", "_map_round_profiles[_map_source.spell]"),
                _when(
                    "_map_round_profile != null and _map_round_profile.mode == _map_source.mode",
                    [
                        _set("_map_round_selected", True),
                        _when(
                            "_map_round_profile.extent == true",
                            [
                                _set("_map_round_state", "'Extent only'"),
                                _set(
                                    "_map_round_notice",
                                    "'Source extent only; cleared subregions are not shown'",
                                ),
                                _set(
                                    "_map_round_clipped_notice",
                                    "'Source extent only; cleared subregions and area beyond map "
                                    "are not shown'",
                                ),
                            ],
                        ),
                        _when(
                            "_map_round_profile.cylinder == true",
                            [
                                _set(
                                    "_map_round_selected", "_map_source.volume.kind == 'cylinder'"
                                ),
                                _when(
                                    "_map_round_selected == true",
                                    [
                                        _set("_map_round_origin", "_map_source.volume.center"),
                                        _set("_map_round_radius", "_map_source.volume.radius"),
                                        _set("_map_round_bottom", "_map_source.volume.center[2]"),
                                        _set(
                                            "_map_round_top",
                                            "_map_source.volume.center[2] + "
                                            "_map_source.volume.height",
                                        ),
                                        _set("_map_round_suffix", "'cylinder-footprint'"),
                                        _set(
                                            "_map_round_notice",
                                            "'Saved cylinder; map shows XY footprint'",
                                        ),
                                        _set(
                                            "_map_round_clipped_notice",
                                            "'Extent only; cylinder continues beyond map'",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                _when("_map_source.spell == 'web' and _map_source.mode == 'web'", _web_zones()),
            ],
            [
                _when(
                    "_map_source.profile == 'truth_zone'",
                    [
                        _set(
                            "_map_truth_source",
                            "state.timed_condition_sources[_map_source.source_id]",
                        ),
                        _when(
                            "_map_truth_source.installed == true and "
                            "_map_truth_source.cast_id == _map_source.cast_id and "
                            "_map_truth_source.dialogue_profile == 'truth_zone' and "
                            "state.lasting_tick < _map_truth_source.expires_tick",
                            [
                                _set("_map_round_origin", "_map_source.origin"),
                                _set("_map_round_radius", 15),
                                _set("_map_round_bottom", "_map_source.origin[2] - 15"),
                                _set("_map_round_top", "_map_source.origin[2] + 15"),
                                _set("_map_round_prefix", "'retained-truth'"),
                                _set("_map_round_identity", "_map_source.cast_id"),
                                _set("_map_round_suffix", "'sphere-footprint'"),
                                _set("_map_round_selected", True),
                                _set(
                                    "_map_round_profile",
                                    _round_profile("truth_zone", "Zone of Truth"),
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _when("_map_round_selected == true", _round_zones()),
    ]


def _web_zones():
    metadata = _public_metadata("Web", "_map_cell.status")
    metadata["player"]["height_label"] = "'Actual five-foot Web cell'"
    return [
        _when(
            "_map_source.web_cell_schema == 1 and _map_source.web_cell_ids != null",
            [
                _set("_map_cell_ids", "_map_source.web_cell_ids"),
                _each(
                    "_map_cell_ids",
                    "_map_cell_id",
                    [
                        _set("_map_cell", "_map_source.web_cells[_map_cell_id]"),
                        _when(
                            "_map_cell.source_sequence == _map_source.sequence and "
                            "(_map_cell.status == 'intact' or (_map_cell.status == 'burning' and "
                            "state.lasting_tick < _map_cell.burn_end_tick))",
                            _zone("_map_cell.volume", metadata, "{_map_cell_id}"),
                        ),
                    ],
                ),
            ],
        )
    ]


def _zone_changes():
    """Compare generated identities and scalar presentation fields using admitted DSL types."""
    changed = "_map_zones_changed"
    fields = ("label", "state", "height_label")
    compare = [
        _set("_map_previous_zone", "state.spatial_scene.zones[_map_zone_index]"),
        _set(
            changed,
            "_map_next_zone.id != _map_previous_zone.id or "
            "_map_next_zone.shape.kind != _map_previous_zone.shape.kind",
        ),
        _when(
            changed + " != true and _map_next_zone.metadata." + TAG + " == true",
            [
                *[
                    _when(
                        f"_map_next_zone.metadata.player.{field} != "
                        f"_map_previous_zone.metadata.player.{field}",
                        [_set(changed, True)],
                    )
                    for field in fields
                ],
                _when(
                    "_map_next_zone.shape.kind == 'box'",
                    [
                        _when(
                            f"_map_next_zone.shape.{bound}[{axis}] != "
                            f"_map_previous_zone.shape.{bound}[{axis}]",
                            [_set(changed, True)],
                        )
                        for bound in ("min", "max")
                        for axis in (0, 1, 2)
                    ],
                    [
                        *[
                            _when(
                                f"_map_next_zone.shape.center[{axis}] != "
                                f"_map_previous_zone.shape.center[{axis}]",
                                [_set(changed, True)],
                            )
                            for axis in (0, 1, 2)
                        ],
                        *[
                            _when(
                                f"_map_next_zone.shape.{field} != _map_previous_zone.shape.{field}",
                                [_set(changed, True)],
                            )
                            for field in ("radius", "height")
                        ],
                    ],
                ),
            ],
        ),
    ]
    return [
        _set(changed, "len(_map_next_zones) != len(state.spatial_scene.zones)"),
        _set("_map_zone_index", 0),
        _each(
            "_map_next_zones",
            "_map_next_zone",
            [
                _when(changed + " != true", compare),
                _set("_map_zone_index", "_map_zone_index + 1"),
            ],
        ),
    ]


def _refresh_tool():
    """Rebuild owned display zones from live records, preserving every unrelated zone."""
    return _environment_tool(
        "Project explicitly known approved retained footprints into display-only "
        "combat scene zones.",
        [],
        [
            _when(
                "state.spatial_scene != null",
                [
                    _set(
                        "_map_round_profiles",
                        {
                            **{
                                spell: _round_profile(mode, label)
                                for spell, (mode, label) in SPHERE_AREAS.items()
                            },
                            **{
                                spell: _round_profile(mode, label, extent=True)
                                for spell, (mode, label) in EXTENT_AREAS.items()
                            },
                            "sleet_storm": _round_profile("sleet", "Sleet Storm", cylinder=True),
                        },
                    ),
                    _set("_map_next_zones", []),
                    _set("_map_old_zones", "state.spatial_scene.zones"),
                    _each(
                        "_map_old_zones",
                        "_map_old_zone",
                        [
                            _when(
                                f"_map_old_zone.metadata.{TAG} != true",
                                [_push("_map_next_zones", "_map_old_zone")],
                            ),
                        ],
                    ),
                    _when(
                        "state.persistent_spell_area_ids != null",
                        [
                            _set("_map_area_ids", "state.persistent_spell_area_ids"),
                            _each(
                                "_map_area_ids",
                                "_map_area_id",
                                [
                                    _set(
                                        "_map_source", "state.persistent_spell_areas[_map_area_id]"
                                    ),
                                    _when(
                                        "_map_source != null and "
                                        "_map_source.active == true and state.lasting_tick < "
                                        "_map_source.expires_tick and "
                                        "_map_source.known_area == true and "
                                        "_map_source.map_id == state.spatial_scene.map.id",
                                        _select_round_source(),
                                    ),
                                ],
                            ),
                        ],
                    ),
                    *_zone_changes(),
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
                                "store_as": "_retained_map_snapshot",
                            },
                        ],
                    ),
                ],
            )
        ],
    )


def _bind_known_intent(tools):
    """Forward the explicit knowledge fact through ordinary, deferred and staged casts."""
    cast = tools["cast_spell_environment"]
    cast["parameters"].append(_parameter())
    cast["description"] += GUIDANCE
    # This runs after the source, its cell geometry and ordinary cast effects commit.
    cast["effects"] += [
        _when(
            " or ".join("spell == " + repr(spell) for spell in ENVIRONMENT_AREAS),
            [
                _set(
                    "lasting_state.persistent_spell_areas.{_environment_cast_id}.known_area", KNOWN
                ),
                _set(
                    "lasting_state.persistent_spell_areas.{_environment_cast_id}.map_id",
                    "lasting_state.spatial_scene.map.id",
                ),
                _call(REFRESH, {"lasting_state": "{_lasting_state_id}"}),
            ],
        )
    ]
    tail = tools["finish_srd_retained_cast_tail"]
    calls = [
        (rows, index)
        for rows in _lists(tail["effects"])
        for index, row in enumerate(rows)
        if row.get("call") == "cast_spell_environment"
    ]
    if len(calls) != 1:
        raise ValueError("Keep the sole retained environment cast dispatch")
    rows, index = calls[0]
    rows[index]["params"][KNOWN] = "{_map_cast_known}"
    rows.insert(index, _set("_map_cast_known", "_retained_cast_tail_choices.known_area == true"))
    if MEMORY_CAST in tools:
        memory = tools[MEMORY_CAST]
        memory["parameters"].append(_parameter())
        memory["description"] += GUIDANCE
        memory["effects"].append(
            _when(
                "spell == 'zone_of_truth' and _dialogue_installed == true",
                [
                    _set("lasting_state.persistent_spell_areas.{_truth_area_id}.known_area", KNOWN),
                    _set(
                        "lasting_state.persistent_spell_areas.{_truth_area_id}.map_id",
                        "lasting_state.spatial_scene.map.id",
                    ),
                    _call(REFRESH, {"lasting_state": "{_lasting_state_id}"}),
                ],
            )
        )
        memory_calls = [
            row
            for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat")
            if name in tools
            for rows in _lists(tools[name]["effects"])
            for row in rows
            if row.get("call") == MEMORY_CAST
        ]
        if not memory_calls:
            raise ValueError("Keep the actual paid memory/dialogue cast dispatch")
        for row in memory_calls:
            row["params"][KNOWN] = "{known_area}"
    choices = tail.get("metadata", {}).get("retained_spell_reactions_v1_choices")
    if not isinstance(choices, list):
        raise ValueError("Keep the retained cast choice capsule")
    choices.append(KNOWN)
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        owner = tools[name]
        owner["parameters"].append(_parameter())
        owner["description"] += GUIDANCE
        delegates = [
            row
            for rows in _lists(owner["effects"])
            for row in rows
            if row.get("call") in ("cast_spell", "cast_spell_node")
        ]
        if delegates:
            for row in delegates:
                row["params"][KNOWN] = "{known_area}"
        captures = [
            row["set"]["value"]
            for rows in _lists(owner["effects"])
            for row in rows
            if (
                row.get("set", {}).get("field") == "_retained_cast_tail_choices"
                or row.get("set", {}).get("field", "").endswith(".retained_cast_choices")
            )
        ]
        for capture in captures:
            # The existing object-facts hook merges into the already captured capsule.
            if capture == [CHOICE_MERGE]:
                continue
            if (
                not isinstance(capture, list)
                or len(capture) != 1
                or not isinstance(capture[0], dict)
            ):
                raise ValueError("Keep each checked retained choice capture")
            capture[0][KNOWN] = KNOWN
        if not delegates and not captures:
            raise ValueError("Keep the existing cast forwarding boundary: " + name)
    begin = tools["begin_srd_spell_action"]
    begin["parameters"].append(_parameter())
    begin["description"] += GUIDANCE
    stamps = [
        (rows, i)
        for rows in _lists(begin["effects"])
        for i, row in enumerate(rows)
        if row.get("set", {}).get("field")
        == "lasting_state.pending_srd_actions.{_pending_action_id}.spell"
    ]
    if len(stamps) != 1:
        raise ValueError("Keep the staged cast parameter allocation")
    rows, index = stamps[0]
    rows.insert(
        index,
        _set("lasting_state.pending_srd_actions.{_pending_action_id}.parameters.known_area", KNOWN),
    )
    complete = tools["complete_srd_pending_action"]
    calls = [
        (rows, index)
        for rows in _lists(complete["effects"])
        for index, row in enumerate(rows)
        if row.get("call") == "cast_spell"
        and row.get("params", {}).get("spell") == "{_pending_action.parameters.spell}"
    ]
    if len(calls) != 1:
        raise ValueError("Keep the staged cast completion dispatcher")
    rows, index = calls[0]
    rows[index]["params"][KNOWN] = "{_map_pending_known}"
    rows.insert(index, _set("_map_pending_known", "_pending_action.parameters.known_area == true"))


def bind_retained_area_map(runtime):
    """Compose public retained footprints after their existing source and cleanup owners."""
    names = {
        "cast_spell_environment",
        "finish_srd_retained_cast_tail",
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "complete_srd_pending_action",
        "refresh_srd_environment_scene",
        "refresh_srd_environment_lifecycle",
        "finish_srd_shared_action_settlements",
        "end_concentration",
        "retire_srd_environment_ground_source",
        "settle_srd_retained_web_burns",
        "expose_srd_retained_web_fire",
    }
    tools = runtime["tools"]
    if REFRESH in tools or not names <= tools.keys():
        raise ValueError("Keep the complete unbound retained map owners")
    if "cast_spell_node" in tools:
        names.add("cast_spell_node")
    if {MEMORY_CAST, MEMORY_SETTLE} & tools.keys():
        if not {MEMORY_CAST, MEMORY_SETTLE} <= tools.keys():
            raise ValueError("Keep both installed truth-area source and cleanup owners")
        names.update((MEMORY_CAST, MEMORY_SETTLE))
    changed = {name: deepcopy(tools[name]) for name in names}
    if any(
        p.get("name") == KNOWN for owner in changed.values() for p in owner.get("parameters", [])
    ):
        raise ValueError("Retained area knowledge is already bound")
    _bind_known_intent(changed)
    for name in names - {
        "cast_spell_environment",
        "finish_srd_retained_cast_tail",
        "cast_spell",
        "cast_spell_node",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "complete_srd_pending_action",
        MEMORY_CAST,
    }:
        owner = changed[name]
        parameters = {p["name"] for p in owner["parameters"]}
        if "lasting_state" not in parameters:
            raise ValueError("Keep the saved state binding for map lifecycle: " + name)
        owner["effects"].append(_call(REFRESH, {"lasting_state": "{_lasting_state_id}"}))
    changed[REFRESH] = _refresh_tool()
    tools.update(changed)
    return sorted(changed)
