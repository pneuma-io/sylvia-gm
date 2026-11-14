"""Record bounded anatomical sight origins on the current admitted creature body."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _replace_tools


PUBLIC_TOOLS = {"record_srd_scene_sight_origin"}
EXPORTS = ["_sight_origin", "_sight_origin_recorded", "_sight_origin_record", "_sight_origin_plane"]


def _branch(condition, on_true, on_false):
    return {"condition": condition, "on_true": on_true, "on_false": on_false}


def _tools():
    """Keep anatomy factual; stamp current shape/form and revalidate before every use."""
    resolve = _tool_scene(
        "Read the actual bounded sight origin; absent anatomy retains the scene-anchor contract.",
        [
            _entity("observer_state"),
            _parameter(
                "offset",
                "array",
                required=False,
                default=None,
                items={"type": "number"},
                min_items=3,
                max_items=3,
                model_hidden=True,
            ),
        ],
        [
            _call(
                "srd_information_scene_entity",
                {"subject": "{_observer_state_id}"},
                POSITION_EXPORTS,
            ),
            _set("_sight_body", "_info_scene_entity"),
            _require(
                "_sight_body.metadata.is_object != true and _sight_body.metadata.plane != null",
                "Sight anatomy needs an actual admitted creature body and plane.",
            ),
            _set("_sight_origin", "_info_position"),
            _set("_sight_origin_plane", "_sight_body.metadata.plane"),
            _set("_sight_origin_recorded", False),
            _set("_sight_origin_record", None),
            _set("_sight_form", None),
            _when(
                "observer_state.form_current_source_id != null",
                [_set("_sight_form", "observer_state.form_current_source_id")],
            ),
            _branch(
                "offset != null",
                [
                    _set(
                        "_sight_origin_record",
                        {
                            "offset": "offset",
                            "footprint": "_sight_body.footprint",
                            "height": "_sight_body.height",
                            "form_source_id": None,
                        },
                    ),
                    _when(
                        "_sight_form != null",
                        [
                            _set("_sight_form_patch", {"form_source_id": "_sight_form"}),
                            _set(
                                "_sight_origin_record",
                                "merge(_sight_origin_record, _sight_form_patch)",
                            ),
                        ],
                    ),
                ],
                [
                    _when(
                        "_sight_body.metadata.srd_sight_origin != null",
                        [
                            _set("_sight_origin_record", "_sight_body.metadata.srd_sight_origin"),
                        ],
                    )
                ],
            ),
            _when(
                "_sight_origin_record != null",
                [
                    _require(
                        "len(_sight_origin_record) == 4 and "
                        "len(_sight_origin_record.offset) == 3 and "
                        "_sight_origin_record.height == _sight_body.height and "
                        "_sight_origin_record.footprint.kind == _sight_body.footprint.kind and "
                        "_sight_origin_record.form_source_id == _sight_form",
                        "Recorded sight anatomy belongs to a different body or form; "
                        "record it again.",
                    ),
                    _set("_sight_offset", "_sight_origin_record.offset"),
                    _require(
                        "_sight_offset[2] >= 0 and _sight_offset[2] <= _sight_body.height",
                        "The anatomical sight origin must lie within the actual body height.",
                    ),
                    _branch(
                        "_sight_body.footprint.kind == 'box'",
                        [
                            _require(
                                " and ".join(
                                    f"_sight_origin_record.footprint.half_extents[{i}] == "
                                    f"_sight_body.footprint.half_extents[{i}] and "
                                    f"_sight_offset[{i}] >= "
                                    f"-_sight_body.footprint.half_extents[{i}] and "
                                    f"_sight_offset[{i}] <= _sight_body.footprint.half_extents[{i}]"
                                    for i in range(2)
                                ),
                                "Recorded sight origin needs the current exact Box footprint.",
                            ),
                        ],
                        [
                            _require(
                                "_sight_body.footprint.kind == 'circle' and "
                                "_sight_origin_record.footprint.radius == "
                                "_sight_body.footprint.radius "
                                "and _sight_offset[0] ** 2 + _sight_offset[1] ** 2 <= "
                                "_sight_body.footprint.radius ** 2",
                                "Recorded sight origin needs the current exact circular footprint.",
                            ),
                        ],
                    ),
                    _set(
                        "_sight_origin",
                        [f"_sight_body.position[{i}] + _sight_offset[{i}]" for i in range(3)],
                    ),
                    _require(
                        " and ".join(
                            f"_sight_origin[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                            f"_sight_origin[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                            for i in range(3)
                        ),
                        "The current sight origin must remain inside admitted map bounds.",
                    ),
                    _set("_sight_origin_recorded", True),
                ],
            ),
        ],
    )
    record = _tool_scene(
        "Record a factual anatomical offset within the current creature's admitted body.",
        [_entity("observer_state"), _array("offset")],
        [
            _call(
                "resolve_srd_scene_sight_origin",
                {
                    "observer_state": "{_observer_state_id}",
                    "offset": "{offset}",
                },
                EXPORTS,
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_observer_state_id}"},
                POSITION_EXPORTS,
            ),
            _set("_sight_metadata_patch", {"srd_sight_origin": "_sight_origin_record"}),
            _set("_sight_metadata", "merge(_info_scene_entity.metadata, _sight_metadata_patch)"),
            *_snapshot(),
            {
                "spatial_mutation": {
                    "scene": "state.spatial_scene",
                    "map_id": "{state.spatial_scene.map.id}",
                    "expected_revision": "{_info_snapshot.geometry_revision}",
                    "target": {"kind": "entity", "id": "{_info_scene_entity.id}"},
                    "patch": {"metadata": "{_sight_metadata}"},
                },
                "store_as": "_sight_recording",
            },
        ],
    )
    record["metadata"] = {
        "category": "exploration",
        "visible_to_player": False,
        "costs_action": False,
    }
    return {"resolve_srd_scene_sight_origin": resolve, "record_srd_scene_sight_origin": record}


def _consumers(runtime):
    """Keep source range at its actor anchor and sample actual Cover from the saved eye."""
    changed = {}
    for name in ("resolve_srd_illumination_point_sight", "resolve_srd_illumination_target_sight"):
        if name not in runtime["tools"]:
            continue
        point = deepcopy(runtime["tools"][name])
        if "resolve_srd_scene_sight_origin" in str(point["effects"]):
            continue
        index = next(
            i
            for i, e in enumerate(point["effects"])
            if e.get("set", {}).get("field") == "_illumination_point_ambient"
        )
        point["effects"][index:index] = [
            _call(
                "resolve_srd_scene_sight_origin",
                {"observer_state": "{_observer_state_id}"},
                EXPORTS,
            ),
            _set("_illumination_point_origin", "_sight_origin"),
        ]
        changed[name] = point
    name = "resolve_srd_spell_object_relation"
    if name in runtime["tools"]:
        relation = deepcopy(runtime["tools"][name])
        if "resolve_srd_scene_sight_origin" not in str(relation["effects"]):
            matches = [
                (i, e)
                for i, e in enumerate(relation["effects"])
                if e.get("store_as") == "_object_exposure" and "spatial_query" in e
            ]
            if len(matches) != 1:
                raise ValueError("Sight origins need the actual object exposure owner.")
            index, exposure = matches[0]
            exposure["spatial_query"]["query"]["observer"]["point"] = "{_sight_origin}"
            relation["effects"].insert(
                index,
                _call(
                    "resolve_srd_scene_sight_origin",
                    {"observer_state": "{_caster_state_id}"},
                    EXPORTS,
                ),
            )
            changed[name] = relation
    return changed


def extend_runtime(text):
    """Emit exact sight primitives before early callers; bind final consumers separately."""
    runtime = _load_runtime(text)
    changed = {name: tool for name, tool in _tools().items() if runtime["tools"].get(name) != tool}
    return _replace_tools(text, changed) if changed else text


def bind_runtime_consumers(text):
    """Bind after source point and object owners; Root fences the factual recording tool."""
    runtime = _load_runtime(text)
    changed = _tools() | _consumers(runtime)
    changed = {name: tool for name, tool in changed.items() if runtime["tools"].get(name) != tool}
    return _replace_tools(text, changed) if changed else text
