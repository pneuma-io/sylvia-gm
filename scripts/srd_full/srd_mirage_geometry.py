"""Project contained Mirage terrain boxes through the one admitted scene geometry."""

import re
from copy import deepcopy

if __package__:
    from .srd_divination_spells import _normalize
    from .srd_mirage_arcane import _effect_lists, _record
    from .srd_scene_relations import _snapshot, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _push, _replace_tools
else:
    from srd_divination_spells import _normalize
    from srd_mirage_arcane import _effect_lists, _record
    from srd_scene_relations import _snapshot, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _push, _replace_tools

CHANGE = _optional(
    "mirage_terrain_changes",
    "array",
    [],
    max_items=64,
    items={
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "minimum": {"type": "array", "items": {"type": "number"}},
            "maximum": {"type": "array", "items": {"type": "number"}},
            "movement": {"type": "string", "enum": ["passable", "blocking"]},
            "ground": {"type": "boolean"},
            "terrain_kind": {"type": "string"},
            "tactile_kind": {"type": "string"},
        },
        "required": [
            "subject",
            "minimum",
            "maximum",
            "movement",
            "ground",
            "terrain_kind",
            "tactile_kind",
        ],
        "additionalProperties": False,
    },
)


def _actual_geometry():
    return [
        *_snapshot(),
        _set("_mirage_geometry", None),
        _set("_mirage_geometry_rows", "state.spatial_scene.geometry"),
        _loop(
            "_mirage_geometry_rows",
            "_mirage_geometry_row",
            [
                _when(
                    "_mirage_geometry_row.id == subject",
                    [
                        _set("_mirage_geometry", "_mirage_geometry_row"),
                    ],
                ),
            ],
        ),
        _require("_mirage_geometry != null", "Select actual admitted terrain geometry."),
    ]


def _subject_tool(tools):
    """Observe the nearest actual box surface from the authentic perception origin."""
    geometry = []
    if "refresh_srd_etherealness_target" in tools:
        geometry.append(
            _call(
                "refresh_srd_etherealness_target",
                {"target_state": "{_observer_state_id}"},
            )
        )
    geometry += [
        _require(
            "_mirage_found_geometry.volume.kind == 'box'",
            "Mirage geometry observation needs an admitted box surface.",
        ),
        _call(
            "srd_information_scene_entity", {"subject": "{_observer_state_id}"}, ["_info_position"]
        ),
        _set("_mirage_surface_origin", "_info_position"),
    ]
    geometry += [
        _set(
            "_info_position",
            [
                f"max(_mirage_found_geometry.volume.min[{i}], "
                f"min(_mirage_found_geometry.volume.max[{i}], _mirage_surface_origin[{i}]))"
                for i in range(3)
            ],
        ),
        _set(
            "_info_scene_entity",
            {
                "id": "_mirage_found_geometry.id",
                "position": "_info_position",
                "metadata": "_mirage_found_geometry.metadata",
            },
        ),
    ]
    return _tool_scene(
        "Read one actual terrain subject without publishing another position authority.",
        [_parameter("subject"), _entity("observer_state")],
        _snapshot()
        + [
            _set("_mirage_found_geometry", None),
            _set("_mirage_surface_rows", "state.spatial_scene.geometry"),
            _loop(
                "_mirage_surface_rows",
                "_mirage_surface_row",
                [
                    _when(
                        "_mirage_surface_row.id == subject and _mirage_surface_row.active == true",
                        [_set("_mirage_found_geometry", "_mirage_surface_row")],
                    ),
                ],
            ),
            {
                "condition": "_mirage_found_geometry != null",
                "on_true": geometry,
                "on_false": [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{subject}"},
                        ["_info_position", "_info_scene_entity"],
                    )
                ],
            },
        ],
    )


def _project_tool():
    effects = _actual_geometry() + [
        _require(
            "state.mirage_geometry_bases[subject] != null",
            "Geometry has no authentic Mirage restoration source.",
        ),
        _set("_mirage_base", "state.mirage_geometry_bases[subject]"),
        _set("_mirage_projection", None),
        _set("_mirage_projection_id", None),
        _set("_mirage_projection_ids", "_mirage_base.source_ids"),
        _loop(
            "_mirage_projection_ids",
            "_mirage_projection_candidate",
            [
                _call(
                    "check_srd_mirage_source",
                    {"mirage_id": "{_mirage_projection_candidate}"},
                    ["_mirage_active", "_mirage_record"],
                ),
                _when(
                    "_mirage_active == true",
                    [
                        _set("_mirage_projection_id", "_mirage_projection_candidate"),
                        _set("_mirage_projection", "_mirage_record.terrain_changes[subject]"),
                    ],
                ),
            ],
        ),
        _set("_mirage_geometry_volume", "_mirage_base.original.volume"),
        _set("_mirage_geometry_movement", "_mirage_base.original.movement"),
        _set("_mirage_geometry_visibility", "_mirage_base.original.visibility"),
        _set("_mirage_geometry_ground", "_mirage_base.original.metadata.ground"),
        _set("_mirage_geometry_terrain", None),
        _set("_mirage_geometry_tactile", None),
        _when(
            "_mirage_projection != null",
            [
                _set(
                    "_mirage_geometry_volume",
                    {
                        "kind": "'box'",
                        "min": "_mirage_projection.minimum",
                        "max": "_mirage_projection.maximum",
                    },
                ),
                _set("_mirage_geometry_movement", "_mirage_projection.movement"),
                _set("_mirage_geometry_visibility", "'clear'"),
                _set("_mirage_geometry_ground", "_mirage_projection.ground"),
                _set("_mirage_geometry_terrain", "_mirage_projection.terrain_kind"),
                _set("_mirage_geometry_tactile", "_mirage_projection.tactile_kind"),
            ],
        ),
        _set(
            "_mirage_geometry_patch",
            {
                "ground": "_mirage_geometry_ground",
                "mirage_projection_id": "_mirage_projection_id",
                "mirage_projected_terrain": "_mirage_geometry_terrain",
                "mirage_projected_tactile": "_mirage_geometry_tactile",
            },
        ),
        _set(
            "_mirage_geometry_metadata", "merge(_mirage_geometry.metadata, _mirage_geometry_patch)"
        ),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{state.spatial_scene.map.revision}",
                "target": {"kind": "geometry", "id": "{subject}"},
                "patch": {
                    "volume": "{_mirage_geometry_volume}",
                    "movement": "{_mirage_geometry_movement}",
                    "visibility": "{_mirage_geometry_visibility}",
                    "metadata": "{_mirage_geometry_metadata}",
                },
            },
            "store_as": "_mirage_geometry_mutation",
        },
        _when(
            "_mirage_projection == null",
            [
                _set("state.mirage_geometry_bases.{subject}", None),
            ],
        ),
    ]
    return _tool_scene(
        "Reproject exact active terrain sources without rewinding other object state.",
        [_parameter("subject")],
        effects,
    )


def _install_tool():
    effects = _record() + [
        _set("_mirage_installing_record", "_mirage_record"),
        _require("_mirage_record.installed == true", "The actual Mirage source must be active."),
        _set("subject", "spec.subject"),
        *_actual_geometry(),
        _require(
            "_mirage_geometry.active == true and _mirage_geometry.volume.kind == 'box'",
            "This physical consumer supports active admitted box terrain.",
        ),
        _require(
            "_mirage_geometry.metadata.participant_ref == null and "
            "(_mirage_geometry.metadata.natural_terrain == true or "
            "_mirage_geometry.metadata.structure == true)",
            "Mirage replaces terrain or structures, never a creature.",
        ),
        _require(
            "_mirage_geometry.metadata.plane == _mirage_installing_record.plane",
            "The actual terrain and source must occupy the same plane.",
        ),
        _require(
            "len(spec.minimum) == 3 and len(spec.maximum) == 3",
            "Physical replacement needs actual three-dimensional bounds.",
        ),
        _require(
            "spec.terrain_kind != '' and spec.tactile_kind != ''",
            "Select the actual apparent terrain and tangible material.",
        ),
        *[
            _require(
                f"spec.maximum[{i}] > spec.minimum[{i}]",
                "Replacement boxes need positive physical dimensions.",
            )
            for i in range(3)
        ],
        *[
            _require(
                f"_mirage_geometry.volume.min[{i}] >= "
                f"_mirage_installing_record.corner[{i}] and "
                f"_mirage_geometry.volume.max[{i}] <= "
                f"_mirage_installing_record.corner[{i}] + _mirage_installing_record.side and "
                f"spec.minimum[{i}] >= _mirage_installing_record.corner[{i}] and "
                f"spec.maximum[{i}] <= _mirage_installing_record.corner[{i}] + "
                "_mirage_installing_record.side",
                "Contained replacement cannot change terrain outside its actual Square; "
                "partial original-box clipping is not yet supported.",
            )
            for i in range(2)
        ],
        _when("state.mirage_geometry_bases == null", [_set("state.mirage_geometry_bases", {})]),
        _when(
            "subject not in state.mirage_geometry_bases or "
            "state.mirage_geometry_bases[subject] == null",
            [
                _set(
                    "state.mirage_geometry_bases.{subject}",
                    {"original": "_mirage_geometry", "source_ids": []},
                ),
            ],
        ),
        _set("_mirage_geometry_base", "state.mirage_geometry_bases[subject]"),
        _set("_mirage_geometry_source_ids", "_mirage_geometry_base.source_ids"),
        _require(
            "mirage_id not in _mirage_geometry_source_ids",
            "Each source supplies one replacement for an actual terrain identity.",
        ),
        _push("_mirage_geometry_source_ids", "mirage_id"),
        _set("state.mirage_geometry_bases.{subject}.source_ids", "_mirage_geometry_source_ids"),
        _set("state.mirage_sources.{mirage_id}.terrain_changes.{subject}", "spec"),
        _set("_mirage_changed_ids", "state.mirage_sources[mirage_id].terrain_subject_ids"),
        _push("_mirage_changed_ids", "subject"),
        _set("state.mirage_sources.{mirage_id}.terrain_subject_ids", "_mirage_changed_ids"),
        _call("project_srd_mirage_geometry", {"subject": "{subject}"}),
    ]

    def bind_subject(value):
        if isinstance(value, str):
            return re.sub(r"(?<![.\w])subject\b", "_mirage_geometry_subject", value)
        if isinstance(value, list):
            return [bind_subject(item) for item in value]
        if isinstance(value, dict):
            return {key: bind_subject(item) for key, item in value.items()}
        return value

    return _tool_scene(
        "Install one exact contained physical terrain replacement.",
        [_parameter("mirage_id"), _parameter("spec", "object")],
        bind_subject(effects),
    )


def extend_runtime(text):
    """Join contained replacements to the existing source and exact retirement path."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "install_srd_mirage_geometry": _install_tool(),
        "project_srd_mirage_geometry": _project_tool(),
        "read_srd_mirage_terrain_subject": _subject_tool(tools),
    }
    cast = deepcopy(tools["cast_srd_mirage_arcane"])
    if not any(p["name"] == CHANGE["name"] for p in cast["parameters"]):
        cast["parameters"].append(deepcopy(CHANGE))
    cast["effects"] = [
        e
        for e in cast["effects"]
        if e.get("for_each", {}).get("as") != "_mirage_terrain_spec"
        and e.get("set", {}).get("field")
        not in {
            "_mirage_terrain_specs",
            "state.mirage_sources.{_mirage_id}.terrain_changes",
            "state.mirage_sources.{_mirage_id}.terrain_subject_ids",
        }
    ]
    cast["effects"] += [
        _set("state.mirage_sources.{_mirage_id}.terrain_changes", {}),
        _set("state.mirage_sources.{_mirage_id}.terrain_subject_ids", []),
        _set("_mirage_terrain_specs", "mirage_terrain_changes"),
        _loop(
            "_mirage_terrain_specs",
            "_mirage_terrain_spec",
            [
                _call(
                    "install_srd_mirage_geometry",
                    {"mirage_id": "{_mirage_id}", "spec": "{_mirage_terrain_spec}"},
                ),
            ],
        ),
    ]
    changed["cast_srd_mirage_arcane"] = cast
    dispatch = deepcopy(tools["cast_spell"])
    if not any(p["name"] == CHANGE["name"] for p in dispatch["parameters"]):
        dispatch["parameters"].append(deepcopy(CHANGE))
    for effects in _effect_lists(dispatch["effects"]):
        for effect in effects:
            if effect.get("call") == "cast_srd_mirage_arcane":
                effect["params"][CHANGE["name"]] = "{" + CHANGE["name"] + "}"
    changed["cast_spell"] = dispatch
    retire = deepcopy(tools["retire_srd_mirage_source"])
    retire["effects"] = [
        e
        for e in retire["effects"]
        if e.get("condition") != "_mirage_record.terrain_subject_ids != null"
    ]
    retire["effects"].append(
        _when(
            "_mirage_record.terrain_subject_ids != null",
            [
                _set("_mirage_retiring_geometry_ids", "_mirage_record.terrain_subject_ids"),
                _loop(
                    "_mirage_retiring_geometry_ids",
                    "_mirage_retiring_geometry",
                    [
                        _when(
                            "state.mirage_geometry_bases[_mirage_retiring_geometry] != null",
                            [
                                _call(
                                    "project_srd_mirage_geometry",
                                    {"subject": "{_mirage_retiring_geometry}"},
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
    )
    changed["retire_srd_mirage_source"] = retire
    perception = deepcopy(tools["perceive_srd_mirage_terrain"])
    for effects in _effect_lists(perception["effects"]):
        effects[:] = [
            e
            for e in effects
            if e.get("condition") != "_mirage_subject.metadata.mirage_projection_id != null"
        ]
        projected = []
        for effect in effects:
            if (
                effect.get("call")
                in {"srd_information_scene_entity", "read_srd_mirage_terrain_subject"}
                and effect["params"].get("subject") == "{subject}"
            ):
                effect["call"] = "read_srd_mirage_terrain_subject"
                effect["params"]["observer_state"] = "{_observer_state_id}"
            projected.append(effect)
            field = effect.get("set", {}).get("field")
            if field in {"_mirage_apparent_terrain", "_mirage_tactile"} and effect["set"][
                "value"
            ] in {"_mirage_record.terrain_kind", "_mirage_record.tactile_kind"}:
                metadata = (
                    "mirage_projected_terrain"
                    if field == "_mirage_apparent_terrain"
                    else "mirage_projected_tactile"
                )
                projected.append(
                    _when(
                        "_mirage_subject.metadata.mirage_projection_id != null",
                        [_set(field, f"_mirage_subject.metadata.{metadata}")],
                    ),
                )
        effects[:] = projected
    changed["perceive_srd_mirage_terrain"] = perception
    _normalize(changed)
    return _replace_tools(text, changed)
