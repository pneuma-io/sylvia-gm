"""Keep Light and Mending object consequences outside combat without a map."""

from copy import deepcopy

from .build_srd_5_2_1_spells import _tool_schema
from .srd_control_behaviors import _each, _lasting, _push
from .srd_lite_attacks import _copy_optional_fact
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import (
    _call,
    _entity,
    _parameter,
    _require,
    _set,
    _tool,
    _when,
)
from .srd_timed_senses import _optional, _public

POOL = "lite_exploration_objects"
INDEX = "lite_exploration_object_ids"
ADMIT = "record_srd_lite_exploration_object"
LOAD = "read_srd_lite_exploration_object"
QUALIFY = "qualify_srd_lite_exploration_object_spell"
CAPTURE = "capture_srd_lite_exploration_objects"
RECONCILE = "reconcile_srd_lite_exploration_objects"
LIGHT_INSTALL = "install_srd_lite_exploration_light"
LIGHT = "cast_spell_illumination_spell"
MENDING = "cast_spell_object_content_spell"
ROOTS = ("cast_spell", "begin_srd_spell_action", "cast_spell_outside_combat")
FACTS = (
    "record_srd_object_break",
    "record_srd_object_material",
    "record_srd_object_container",
    "set_srd_illumination_cover",
)
FACT_NODES = {name + "_node": "lite_exploration_" + name + "_node" for name in FACTS}
SCENE = "lasting_state.spatial_scene"
RECORD = "lasting_state." + POOL + "[object_id]"
SCOPE = "combat.encounter_active != true"
DISTANCE, TOUCH, COVER = (
    "_lite_object_fiction_distance",
    "_lite_object_fiction_touch",
    "_lite_object_fiction_cover",
)


def _init_pool():
    return [
        _when(
            "lasting_state." + POOL + " == null",
            [
                _set("lasting_state." + POOL, {}),
                _set("lasting_state." + INDEX, []),
            ],
        )
    ]


def _save_body(body, *, pending=True, changes=None, frame_changes=None):
    """Save the actual object frame and metadata; position is only a break-bounds reference."""
    return [
        *_init_pool(),
        _require(
            "object_id in lasting_state." + POOL + " or len(lasting_state." + INDEX + ") < 128",
            "Exploration supports at most 128 retained objects.",
        ),
        _set("_lite_saved_pending_patch", {}),
        _set("_lite_saved_pending_frame_patch", {}),
        *(
            [
                _when(
                    "object_id in lasting_state."
                    + POOL
                    + " and "
                    + RECORD
                    + ".pending_patch != null",
                    [_set("_lite_saved_pending_patch", RECORD + ".pending_patch")],
                ),
                *(
                    [
                        _set(
                            "_lite_saved_pending_patch",
                            "merge(_lite_saved_pending_patch, " + changes + ")",
                        )
                    ]
                    if changes is not None
                    else []
                ),
            ]
            if pending
            else []
        ),
        *(
            [
                _when(
                    "object_id in lasting_state."
                    + POOL
                    + " and "
                    + RECORD
                    + ".pending_frame_patch != null",
                    [
                        _set(
                            "_lite_saved_pending_frame_patch",
                            RECORD + ".pending_frame_patch",
                        )
                    ],
                ),
                *(
                    [
                        _set(
                            "_lite_saved_pending_frame_patch",
                            "merge(_lite_saved_pending_frame_patch, " + frame_changes + ")",
                        )
                    ]
                    if frame_changes is not None
                    else []
                ),
            ]
            if pending
            else []
        ),
        _set(
            "lasting_state." + POOL + ".{object_id}",
            {
                "id": "object_id",
                "body": body,
                "pending_scene_reconciliation": pending,
                "pending_patch": "_lite_saved_pending_patch",
                "pending_frame_patch": "_lite_saved_pending_frame_patch",
            },
        ),
        _set("_lite_object_ids", "lasting_state." + INDEX),
        _when(
            "object_id not in _lite_object_ids",
            [_push("_lite_object_ids", "object_id")],
        ),
        _set("lasting_state." + INDEX, "_lite_object_ids"),
    ]


def _pending_body(record, body):
    """Overlay only these spells' pending metadata on the newest saved object facts."""
    return [
        _set("_lite_current_patch", {}),
        _when(
            record + ".pending_patch != null",
            [_set("_lite_current_patch", record + ".pending_patch")],
        ),
        _when(
            "_lite_current_patch.srd_object_breaks != null",
            [
                _set("_lite_current_breaks", []),
                _set("_lite_current_break_rows", "_lite_current_patch.srd_object_breaks"),
                _each(
                    "_lite_current_break_rows",
                    "_lite_current_break",
                    [
                        _push(
                            "_lite_current_breaks",
                            {
                                "id": "_lite_current_break.id",
                                **{
                                    edge: [
                                        f"_lite_current_break.{edge}[{a}] + {body}.position[{a}] - "
                                        f"{record}.body.position[{a}]"
                                        for a in range(3)
                                    ]
                                    for edge in ("min", "max")
                                },
                            },
                        ),
                    ],
                ),
                _set(
                    "_lite_current_break_patch",
                    {"srd_object_breaks": "_lite_current_breaks"},
                ),
                _set(
                    "_lite_current_patch",
                    "merge(_lite_current_patch, _lite_current_break_patch)",
                ),
            ],
        ),
        _set(
            "_lite_current_metadata",
            {"metadata": f"merge({body}.metadata, _lite_current_patch)"},
        ),
        _set("_lite_current_body", f"merge({body}, _lite_current_metadata)"),
        _when(
            record + ".pending_frame_patch != null",
            [
                _set(
                    "_lite_current_body",
                    "merge(_lite_current_body, " + record + ".pending_frame_patch)",
                )
            ],
        ),
    ]


def _scene_candidate():
    """Read optional saved scenery by exact ID, without requiring or using map movement."""
    return [
        _set("_lite_object_scene_body", None),
        _when(
            SCENE + " != null",
            [
                _set("_lite_object_scene_rows", SCENE + ".entities"),
                _each(
                    "_lite_object_scene_rows",
                    "_lite_object_scene_row",
                    [
                        _when(
                            "_lite_object_scene_row.id == object_id",
                            [
                                _require(
                                    "_lite_object_scene_body == null",
                                    "The retained object's scene ID is ambiguous.",
                                ),
                                _require(
                                    "_lite_object_scene_row.metadata.participant_ref == null",
                                    "An exploration object cannot replace a creature body.",
                                ),
                                _set("_lite_object_scene_body", "_lite_object_scene_row"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]


def _load_tool():
    """Pending exploration changes win over stale scenery; otherwise the current scene wins."""
    return _tool(
        "Read one retained object's actual facts; no exploration distance comes from a map.",
        [_parameter("object_id"), _lasting()],
        [
            _set("_lite_object_record", None),
            _set("_lite_current_patch", {}),
            _when(
                "lasting_state." + POOL + " != null and object_id in lasting_state." + POOL,
                [_set("_lite_object_record", RECORD)],
            ),
            *_scene_candidate(),
            _set("_lite_object_body", "_lite_object_scene_body"),
            _when(
                "_lite_object_record != null and _lite_object_scene_body == null",
                [
                    _set("_lite_object_body", "_lite_object_record.body"),
                    _when(
                        "_lite_object_record.pending_patch != null",
                        [
                            _set(
                                "_lite_current_patch",
                                "_lite_object_record.pending_patch",
                            )
                        ],
                    ),
                ],
            ),
            _when(
                "_lite_object_record != null and _lite_object_scene_body != null and "
                "_lite_object_record.pending_scene_reconciliation == true",
                [
                    *_pending_body("_lite_object_record", "_lite_object_scene_body"),
                    _set("_lite_object_body", "_lite_current_body"),
                ],
            ),
            _require(
                "_lite_object_body != null and _lite_object_body.id == object_id and "
                "_lite_object_body.active == true and "
                "_lite_object_body.metadata.participant_ref == null",
                "Admit this actual exploration object before using its spell facts.",
            ),
        ],
    )


def _load(object_id="{object_id}", *, destination="_object_scene"):
    row = _call(LOAD, {"object_id": object_id, "lasting_state": "{_lasting_state_id}"})
    row["export_context"] = {
        destination: "_lite_object_body",
        "_lite_current_patch": "_lite_current_patch",
        "_lite_object_scene_body": "_lite_object_scene_body",
    }
    return row


def _admit_tool():
    """The new API admits identity and local dimensions, never replacement HP or magic."""
    dimensions = _parameter("extent", "array", items={"type": "number"}, min_items=3, max_items=3)
    tool = _tool(
        "Admit a new fictional object for Light/Mending outside combat, without map coordinates. "
        "Use existing record_srd_object_break/material/container tools for its physical facts. "
        "Break bounds are relative to the object: x/y centred on zero, z from zero to height. "
        "Existing scene or retained objects cannot be replaced. When included in a combat arena, "
        "use the same object ID and dimensions; reconciliation preserves its spell consequences.",
        [
            _parameter("object_id"),
            _parameter("fiction"),
            dimensions,
            _parameter("size_rank", "integer", minimum=0, maximum=5),
            _optional("carried_by", default=""),
            _optional("worn_by", default=""),
            _optional("movement", "enum", "blocking", enum_values=["blocking", "passable"]),
            _optional("visibility", "enum", "opaque", enum_values=["opaque", "clear"]),
            _lasting(),
        ],
        [
            _require(
                SCOPE + " and combat.pending_action == null",
                "Admit exploration object facts outside combat and pending actions.",
            ),
            _require(
                "object_id != '' and fiction != ''",
                "Choose a stable object ID and fiction.",
            ),
            *_scene_candidate(),
            *_init_pool(),
            _require(
                "_lite_object_scene_body == null and object_id not in lasting_state." + POOL,
                "Preserve the existing object; its facts cannot be replaced.",
            ),
            *[
                _require(
                    f"extent[{axis}] > 0 and extent[{axis}] <= 100000",
                    "Actual object dimensions must be positive and bounded.",
                )
                for axis in range(3)
            ],
            _require("carried_by == '' or worn_by == ''", "Use one actual object holder."),
            _set(
                "_lite_new_object_body",
                {
                    "id": "object_id",
                    "position": [0, 0, 0],
                    "footprint": {
                        "kind": "'box'",
                        "half_extents": ["extent[0] * 0.5", "extent[1] * 0.5"],
                    },
                    "height": "extent[2]",
                    "active": True,
                    "movement": "movement",
                    "visibility": "visibility",
                    "metadata": {
                        "is_object": True,
                        "fiction": "fiction",
                        "size_rank": "size_rank",
                        "carried_by": "carried_by",
                        "worn_by": "worn_by",
                        "opaque_cover": False,
                    },
                },
            ),
            *_save_body("_lite_new_object_body"),
            _set("_lite_object_admitted", "object_id"),
        ],
    )
    return _public(
        tool,
        "exploration",
        "Object facts retained",
        [("Object", "_lite_object_admitted")],
    )


def _metadata_save(metadata, changes):
    return [
        _set("_lite_object_metadata_patch", {"metadata": metadata}),
        _set(
            "_lite_object_patched_body",
            "merge(_object_scene, _lite_object_metadata_patch)",
        ),
        _set("_lite_object_owned_changes", "merge(_lite_current_patch, " + changes + ")"),
        *_save_body("_lite_object_patched_body", changes="_lite_object_owned_changes"),
        _set("_object_scene", "_lite_object_patched_body"),
    ]


def _without_scene(effects, *, fact_nodes=None):
    """Reuse only the selected public fact/repair metadata operations, retaining their guards."""
    output = []
    for original in effects:
        row = deepcopy(original)
        if fact_nodes and row.get("call") in fact_nodes:
            row["call"] = fact_nodes[row["call"]]
            output.append(row)
            continue
        condition = row.get("require", {}).get("condition")
        if condition in (SCENE + " != null", SCENE + ".map.grid.unit == 'ft'"):
            continue
        if row.get("spatial_query", {}).get("operation") == "snapshot":
            continue
        if "spatial_query" in row:
            raise ValueError("Do not bypass another object's actual geometry operation")
        if row.get("call") == "srd_information_scene_entity":
            if row["params"].get("subject") != "{object_id}":
                raise ValueError("Keep other scene subject owners intact")
            output += [
                _load(destination="_info_scene_entity"),
                _set("_info_position", "_info_scene_entity.position"),
            ]
            continue
        if row.get("call") == "resolve_srd_illumination_overlaps":
            continue
        if "spatial_mutation" in row:
            patch = row["spatial_mutation"]["patch"]
            if set(patch) != {"metadata"}:
                raise ValueError("Exploration facts must not replace body geometry or statistics")
            metadata = patch["metadata"]
            if not (
                isinstance(metadata, str) and metadata.startswith("{") and metadata.endswith("}")
            ):
                raise ValueError("Preserve the existing metadata handoff")
            # Cover's original owner uses _info_scene_entity rather than _object_scene.
            if metadata == "{_cover_metadata}":
                output.append(_set("_object_scene", "_info_scene_entity"))
            output += _metadata_save(
                metadata[1:-1],
                "_cover_patch" if metadata == "{_cover_metadata}" else "_world_changes",
            )
            continue
        for branch in ("on_true", "on_false"):
            if branch in row:
                row[branch] = _without_scene(row[branch], fact_nodes=fact_nodes)
        if "for_each" in row:
            if row["for_each"]["collection"] == "lasting_state.turn_effects":
                if row["for_each"]["as"] != "_lite_cover_record":
                    raise ValueError("Review the changed illumination record loop")
                output.append(_set("_lite_cover_records", "lasting_state.turn_effects"))
                row["for_each"]["collection"] = "_lite_cover_records"
            row["for_each"]["do"] = _without_scene(row["for_each"]["do"], fact_nodes=fact_nodes)
        output.append(row)
    return output


def _qualification_tool(runtime):
    object_light = next(
        row["on_true"]
        for row in runtime["tools"][LIGHT]["effects"]
        if row.get("condition", "").startswith("_light_profile.illumination_mode == 'object'")
    )
    light_restrictions = next(
        row["on_true"] for row in object_light if row.get("condition") == "spell == 'light'"
    )
    mending = next(
        row["on_true"]
        for row in runtime["tools"][MENDING]["effects"]
        if row.get("condition") == "spell == 'mending'"
    )
    # Native eligibility stops before the exact native metadata patch. It is used
    # before staging/payment as well as by the actual repair below.
    stop = next(
        index
        for index, row in enumerate(mending)
        if row.get("require", {}).get("condition") == SCENE + " != null"
    )
    return _tool(
        "Validate supplied exploration contact and the actual retained object before spell costs.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("spell"),
            _optional("object_break_id", default=""),
            _parameter("object_distance", "number", minimum=0),
            _parameter("object_touch", "boolean"),
            _parameter("object_cover"),
            _lasting(),
        ],
        [
            _require(
                SCOPE + " and (spell == 'light' or spell == 'mending')",
                "Use this qualifier only for Light or Mending exploration.",
            ),
            _require(
                "caster_state.is_alive == true and not ('incapacitated' in caster_state.conditions "
                "or 'paralyzed' in caster_state.conditions or 'petrified' in "
                "caster_state.conditions or 'stunned' in caster_state.conditions "
                "or 'unconscious' in caster_state.conditions)",
                "A capable living caster is required.",
            ),
            _set("_lite_object_reach", 5),
            _when(
                "caster_state.melee_reach != null",
                [_set("_lite_object_reach", "caster_state.melee_reach")],
            ),
            _require(
                "object_touch == true and object_distance <= _lite_object_reach and "
                "object_cover != 'total'",
                "Admit actual exploration contact within reach and a clear spell path.",
            ),
            _load(),
            _require(
                "_object_scene.metadata.is_object == true",
                "Use an admitted actual object.",
            ),
            _when(
                "spell == 'light'",
                [
                    _set("_info_scene_entity", "_object_scene"),
                    *deepcopy(light_restrictions),
                ],
            ),
            _when(
                "spell == 'mending'",
                [
                    {
                        "table_lookup": {
                            "table": "definitions.spells",
                            "key": "spell",
                            "required": True,
                        },
                        "store_as": "_world_profile",
                    },
                    *deepcopy(mending[:stop]),
                ],
            ),
        ],
    )


def _qualify_call(object_id):
    return _call(
        QUALIFY,
        {
            "caster_state": "{_caster_state_id}",
            "object_id": object_id,
            "spell": "{spell}",
            "object_break_id": "" if object_id == "{illumination_object}" else "{object_break_id}",
            "object_distance": "{object_distance}",
            "object_touch": "{object_touch}",
            "object_cover": "{object_cover}",
            "lasting_state": "{_lasting_state_id}",
        },
    )


def _light_install_tool(runtime):
    tool = deepcopy(runtime["tools"]["install_srd_illumination_source"])
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] not in ("origin", "plane")]
    record = next(
        row["set"]["value"]
        for row in tool["effects"]
        if row.get("set", {}).get("field") == "_light_install_record"
    )
    record.update(origin=None, plane=None)
    tool["effects"].insert(
        0,
        _require(
            SCOPE + " and spell == 'light'",
            "Scene-free illumination preserves only the actual Light spell.",
        ),
    )
    return tool


def _light_effects(runtime):
    native = runtime["tools"][LIGHT]["effects"]
    stop = next(
        index
        for index, row in enumerate(native)
        if row.get("call") == "srd_information_scene_entity"
    )
    recast = next(
        row
        for row in native
        if row.get("condition") == "spell == 'light' or spell == 'produce_flame'"
    )
    return [
        _qualify_call("{illumination_object}"),
        *deepcopy(native[:stop]),
        deepcopy(recast),
        _load("{illumination_object}"),
        _set("_lite_light_source", ["illumination_object"]),
        _each(
            "_lite_light_source",
            "object_id",
            _save_body("_object_scene", changes="_lite_current_patch"),
        ),
        _call(
            LIGHT_INSTALL,
            {
                "caster_state": "{_caster_state_id}",
                "spell": "{spell}",
                "source": "{_lite_light_source}",
                "slot_level": "{slot_level}",
                "instance": "{_light_cast_instance}",
                "color": "{illumination_color}",
                "lasting_state": "{_lasting_state_id}",
            },
        ),
        *deepcopy(native[-2:]),
    ]


def _mending_effects(runtime):
    native = runtime["tools"][MENDING]["effects"]
    repair = next(row["on_true"] for row in native if row.get("condition") == "spell == 'mending'")
    return [
        _qualify_call("{object_id}"),
        deepcopy(native[0]),
        deepcopy(native[1]),
        _load(),
        _when(
            "lasting_state.lasting_tick == null",
            [_set("lasting_state.lasting_tick", 0)],
        ),
        _set("_object_now", "lasting_state.lasting_tick"),
        _set("_spell_summary_rows", []),
        _set("_spell_detail_rows", []),
        *_without_scene(repair),
    ]


def _capture_tool():
    return _tool(
        "Capture managed objects from the old authoritative scene before replacing it; "
        "preserve pending exploration changes.",
        [_lasting()],
        [
            _require(SCOPE, "Capture objects before starting coordinate combat."),
            _when(
                SCENE + " != null and lasting_state.turn_effects != null",
                [
                    _set("_lite_capture_lights", "lasting_state.turn_effects"),
                    _each(
                        "_lite_capture_lights",
                        "_lite_capture_light",
                        [
                            _when(
                                "_lite_capture_light.kind == 'illumination_spell' and "
                                "_lite_capture_light.spell == 'light' and "
                                "_lite_capture_light.source != '' and "
                                "(_lite_capture_light.expires_tick == null or "
                                "lasting_state.lasting_tick < _lite_capture_light.expires_tick)",
                                [
                                    _set(
                                        "_lite_capture_light_ids",
                                        ["_lite_capture_light.source"],
                                    ),
                                    _each(
                                        "_lite_capture_light_ids",
                                        "object_id",
                                        [
                                            *_scene_candidate(),
                                            _when(
                                                "_lite_object_scene_body != null and "
                                                "(lasting_state." + POOL + " == null or "
                                                "object_id not in lasting_state." + POOL + ")",
                                                _save_body("_lite_object_scene_body"),
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "lasting_state." + INDEX + " != null and " + SCENE + " != null",
                [
                    _set("_lite_capture_ids", "lasting_state." + INDEX),
                    _each(
                        "_lite_capture_ids",
                        "object_id",
                        [
                            _set("_lite_capture_record", RECORD),
                            *_scene_candidate(),
                            _when(
                                "_lite_object_scene_body != null",
                                [
                                    *_pending_body(
                                        "_lite_capture_record",
                                        "_lite_object_scene_body",
                                    ),
                                    *_save_body(
                                        "_lite_current_body",
                                        changes="_lite_current_patch",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _reconcile_tool():
    """The admitted scene supplies positions; the retained object supplies consequences."""
    effects = [
        _require(SCOPE, "Reconcile objects before starting coordinate combat."),
        _when(
            "lasting_state." + INDEX + " != null",
            [
                _require(
                    SCENE + " != null",
                    "Coordinate combat must admit the retained exploration objects.",
                ),
                _set("_rc_rows", SCENE + ".entities"),
                _set("_rc_ids", "lasting_state." + INDEX),
                _each(
                    "_rc_ids",
                    "object_id",
                    [
                        _set("_rc_record", RECORD),
                        _set("_rc_body", None),
                        _set("_rc_holder", "''"),
                        _set("_rc_holder_selected", False),
                        _when(
                            "_rc_record.body.metadata.carried_by != null",
                            [_set("_rc_holder", "_rc_record.body.metadata.carried_by")],
                        ),
                        _when(
                            "_rc_holder == '' and _rc_record.body.metadata.worn_by != null",
                            [_set("_rc_holder", "_rc_record.body.metadata.worn_by")],
                        ),
                        _each(
                            "_rc_rows",
                            "_rc_row",
                            [
                                _when(
                                    "_rc_row.id == object_id",
                                    [
                                        _require(
                                            "_rc_body == null",
                                            "The retained object ID must be unique.",
                                        ),
                                        _set("_rc_body", "_rc_row"),
                                    ],
                                ),
                                _when(
                                    "_rc_holder != '' and _rc_row.active == true and "
                                    "(_rc_row.id == _rc_holder or "
                                    "_rc_row.metadata.participant_ref == _rc_holder)",
                                    [_set("_rc_holder_selected", True)],
                                ),
                            ],
                        ),
                        _require(
                            "_rc_record.body.active != true or "
                            "_rc_holder_selected != true or _rc_body != null",
                            "Include the actual retained object carried or worn "
                            "by this arena's holder.",
                        ),
                        _when(
                            "_rc_body != null",
                            [
                                _require(
                                    "_rc_body.metadata.participant_ref == null and "
                                    "_rc_body.height == _rc_record.body.height and "
                                    "_rc_body.footprint.kind == _rc_record.body.footprint.kind",
                                    "Preserve the object ID, dimensions and creature boundary.",
                                ),
                                _when(
                                    "_rc_body.footprint.kind == 'box'",
                                    [
                                        _require(
                                            "_rc_body.footprint.half_extents[0] == "
                                            "_rc_record.body.footprint.half_extents[0] and "
                                            "_rc_body.footprint.half_extents[1] == "
                                            "_rc_record.body.footprint.half_extents[1]",
                                            "Preserve the object's actual box dimensions.",
                                        )
                                    ],
                                ),
                                _when(
                                    "_rc_body.footprint.kind == 'circle'",
                                    [
                                        _require(
                                            "_rc_body.footprint.radius == "
                                            "_rc_record.body.footprint.radius",
                                            "Preserve the object's actual circular dimensions.",
                                        )
                                    ],
                                ),
                                _require(
                                    "_rc_body.metadata.plane != null and "
                                    "_rc_body.metadata.lighting != null",
                                    "Admit the object's actual plane and ambient light.",
                                ),
                                _set(
                                    "_rc_spatial",
                                    {
                                        "plane": "_rc_body.metadata.plane",
                                        "lighting": "_rc_body.metadata.lighting",
                                    },
                                ),
                                _set(
                                    "_rc_metadata",
                                    "merge(_rc_record.body.metadata, _rc_spatial)",
                                ),
                                _set("_rc_breaks", []),
                                _when(
                                    "_rc_record.body.metadata.srd_object_breaks != null",
                                    [
                                        _set(
                                            "_rc_source_breaks",
                                            "_rc_record.body.metadata.srd_object_breaks",
                                        ),
                                        _each(
                                            "_rc_source_breaks",
                                            "_rc_break",
                                            [
                                                _push(
                                                    "_rc_breaks",
                                                    {
                                                        "id": "_rc_break.id",
                                                        **{
                                                            edge: [
                                                                f"_rc_break.{edge}[{a}] + "
                                                                f"_rc_body.position[{a}] - "
                                                                f"_rc_record.body.position[{a}]"
                                                                for a in range(3)
                                                            ]
                                                            for edge in ("min", "max")
                                                        },
                                                    },
                                                ),
                                            ],
                                        ),
                                        _set(
                                            "_rc_break_patch",
                                            {"srd_object_breaks": "_rc_breaks"},
                                        ),
                                        _set(
                                            "_rc_metadata",
                                            "merge(_rc_metadata, _rc_break_patch)",
                                        ),
                                    ],
                                ),
                                _set(
                                    "_rc_patch",
                                    {
                                        "metadata": "merge(_rc_body.metadata, _rc_metadata)",
                                        "active": "_rc_record.body.active",
                                        "movement": "_rc_record.body.movement",
                                        "visibility": "_rc_record.body.visibility",
                                    },
                                ),
                                _set(
                                    "_rc_body",
                                    "merge(_rc_body, _rc_patch)",
                                ),
                                _set("_rc_next", []),
                                _each(
                                    "_rc_rows",
                                    "_rc_row",
                                    [
                                        {
                                            "condition": "_rc_row.id == object_id",
                                            "on_true": [_push("_rc_next", "_rc_body")],
                                            "on_false": [_push("_rc_next", "_rc_row")],
                                        },
                                    ],
                                ),
                                _set("_rc_rows", "_rc_next"),
                                *_save_body("_rc_body", pending=False),
                            ],
                        ),
                    ],
                ),
                _set(SCENE + ".entities", "_rc_rows"),
                _set(SCENE + ".map.revision", SCENE + ".map.revision + 1"),
                {
                    "spatial_query": {
                        "scene": "state.spatial_scene",
                        "operation": "snapshot",
                    },
                    "store_as": "_lite_reconciled_snapshot",
                },
            ],
        ),
    ]
    return _tool(
        "Reconcile selected retained Light/Mending objects before initiative. "
        "Omitted world objects retain their global consequences; a selected holder "
        "must bring its actual carried objects.",
        [_lasting()],
        effects,
    )


def _bind_light_record(runtime):
    owner = runtime["tools"]["check_srd_illumination_record"]
    row = next(
        row
        for row in owner["effects"]
        if row.get("condition") == "_illumination_active == true and record.source != ''"
    )
    native = deepcopy(row["on_true"])
    row["on_true"] = [
        {
            "condition": "("
            + SCOPE
            + " or record.spell == 'light') and lasting_state."
            + POOL
            + " != null and record.source in lasting_state."
            + POOL,
            "on_true": [
                _set("_lite_record_source_ids", ["record.source"]),
                _each(
                    "_lite_record_source_ids",
                    "object_id",
                    [
                        _set(
                            "_object_scene",
                            "lasting_state." + POOL + "[object_id].body",
                        ),
                        *_scene_candidate(),
                        {
                            "condition": "_lite_object_scene_body != null and "
                            "_lite_object_scene_body.active == true",
                            "on_true": [_load("{record.source}"), *deepcopy(native)],
                            "on_false": [
                                _require(
                                    "record.spell == 'light'",
                                    "Only Light supports a retained source without a scene.",
                                ),
                                _set("_illumination_origin", None),
                                _set("_illumination_plane", None),
                            ],
                        },
                        _set(
                            "_illumination_covered",
                            "_object_scene.metadata.opaque_cover == true",
                        ),
                    ],
                ),
            ],
            "on_false": native,
        }
    ]


def _bind_roots(runtime):
    for name in ROOTS:
        owner = runtime["tools"][name]
        if name == "cast_spell_outside_combat":
            delegates = [
                row
                for group in lists(owner["effects"])
                for row in group
                if row.get("call") == "cast_spell"
            ]
            profiles = [
                row
                for group in lists(owner["effects"])
                for row in group
                if row.get("set", {}).get("field") == "_casting_profile"
                or row.get("store_as") == "_casting_profile"
            ]
            if len(delegates) == 1 and not profiles:
                params = delegates[0]["params"]
                for fact in ("target_distance", "caster_can_touch_target", "cover"):
                    if params.get(fact) != "{" + fact + "}":
                        raise ValueError("Keep the exploration object's actual fact handoff")
                # The public cast owns profile admission and payment; preserve the
                # exploration delegate's guards and its exact fact handoff.
                continue
        rows = [row for group in lists(owner["effects"]) for row in group]
        identity = (
            "_ms" if any(row.get("set", {}).get("field") == "_ms" for row in rows) else "spell"
        )
        scoped = SCOPE + f" and ({identity} == 'light' or {identity} == 'mending')"
        owner["effects"][:0] = [
            _copy_optional_fact(DISTANCE, "target_distance"),
            _copy_optional_fact(TOUCH, "caster_can_touch_target"),
            _copy_optional_fact(COVER, "cover"),
        ]
        validation = _when(
            scoped,
            [
                _require(
                    DISTANCE + " != null and " + TOUCH + " != null and " + COVER + " != null",
                    "Supply exploration object distance, contact and cover facts.",
                ),
                _set("_lite_object_spell_id", "object_target_id"),
                _when(
                    identity + " == 'light'",
                    [_set("_lite_object_spell_id", "illumination_object")],
                ),
                _call(
                    QUALIFY,
                    {
                        "caster_state": "{_caster_state_id}",
                        "object_id": "{_lite_object_spell_id}",
                        "spell": "{" + identity + "}",
                        "object_break_id": "{object_break_id}",
                        "object_distance": "{" + DISTANCE + "}",
                        "object_touch": "{" + TOUCH + "}",
                        "object_cover": "{" + COVER + "}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                ),
            ],
        )
        sites = [
            (group, i)
            for group in lists(owner["effects"])
            for i, row in enumerate(group)
            if row.get("set", {}).get("field") == "_casting_profile"
            or row.get("store_as") == "_casting_profile"
        ]
        if not sites:
            raise ValueError("Preserve the actual casting profile admission: " + name)
        for group, i in reversed(sites):
            group.insert(i + 1, deepcopy(validation))
        choices = [
            (group, i)
            for group in lists(owner["effects"])
            for i, row in enumerate(group)
            if row.get("set", {}).get("field") == "_retained_cast_tail_choices"
            and isinstance(row["set"]["value"], list)
        ]
        for group, i in reversed(choices):
            group.insert(
                i + 1,
                _when(
                    scoped,
                    [
                        _set(
                            "_lite_object_retained_facts",
                            {
                                "exploration_object_distance": DISTANCE,
                                "exploration_object_touch": TOUCH,
                                "exploration_object_cover": COVER,
                            },
                        ),
                        _set(
                            "_retained_cast_tail_choices",
                            ["merge(_retained_cast_tail_choices[0], _lite_object_retained_facts)"],
                        ),
                    ],
                ),
            )


def bind_exploration_objects(runtime, record):
    """Bind the package object vertical; setup calls capture and reconciliation explicitly."""
    tools = runtime["tools"]
    if ADMIT in tools:
        raise ValueError("Exploration object support was already bound")
    tools.update(
        {
            ADMIT: _admit_tool(),
            LOAD: _load_tool(),
            QUALIFY: _qualification_tool(runtime),
            CAPTURE: _capture_tool(),
            RECONCILE: _reconcile_tool(),
            LIGHT_INSTALL: _light_install_tool(runtime),
        }
    )
    # Public facts delegate to private native nodes. Adapt only these selected
    # copies, retaining every physical-fact guard and the original map owners.
    for native_name, exploration_name in FACT_NODES.items():
        if exploration_name in tools:
            raise ValueError("Exploration fact node was already bound: " + exploration_name)
        node = deepcopy(tools[native_name])
        node["effects"] = _without_scene(node["effects"])
        if not any(row.get("require", {}).get("condition") == SCOPE for row in node["effects"]):
            node["effects"].insert(
                0, _require(SCOPE, "Retained object facts are only used outside combat.")
            )
        tools[exploration_name] = node
    effects = {LIGHT: _light_effects(runtime), MENDING: _mending_effects(runtime)}
    for name, branch in effects.items():
        owner = tools[name]
        for p in (
            _optional("object_distance", "number", None, minimum=0),
            _optional("object_touch", "boolean", False),
            _optional("object_cover", default="total"),
        ):
            if any(existing["name"] == p["name"] for existing in owner["parameters"]):
                raise ValueError("Preserve the original object spell facts")
            owner["parameters"].append(p)
        owner["effects"] = [
            {
                "condition": SCOPE
                + " and spell == '"
                + ("light" if name == LIGHT else "mending")
                + "'",
                "on_true": branch,
                "on_false": deepcopy(owner["effects"]),
            }
        ]
    for name in FACTS:
        owner = tools[name]
        original = deepcopy(owner["effects"])
        owner["effects"] = [
            {
                "condition": SCOPE,
                "on_true": _without_scene(original, fact_nodes=FACT_NODES),
                "on_false": original,
            }
        ]
        owner["description"] += (
            " Outside combat, retained exploration objects use their local physical facts; "
            "no map setup or movement is required."
        )
    _bind_light_record(runtime)
    _bind_roots(runtime)
    dispatches = 0
    for owner in tools.values():
        for group in list(lists(owner["effects"])):
            for i in range(len(group) - 1, -1, -1):
                row = group[i]
                if row.get("call") in (LIGHT, MENDING):
                    original = deepcopy(row)
                    if "_retained_cast_tail_choices" not in row["params"].get("spell", ""):
                        raise ValueError("Keep the real retained object spell dispatch handoff")
                    amended = deepcopy(row)
                    amended["params"].update(
                        {
                            "object_distance": (
                                "{_retained_cast_tail_choices.exploration_object_distance}"
                            ),
                            "object_touch": (
                                "{_retained_cast_tail_choices.exploration_object_touch}"
                            ),
                            "object_cover": (
                                "{_retained_cast_tail_choices.exploration_object_cover}"
                            ),
                        }
                    )
                    spell = "light" if row["call"] == LIGHT else "mending"
                    group[i] = {
                        "condition": SCOPE
                        + " and _retained_cast_tail_choices.spell == '"
                        + spell
                        + "'",
                        "on_true": [amended],
                        "on_false": [original],
                    }
                    dispatches += 1
    if dispatches != 2:
        raise ValueError("Preserve the two original retained object spell deliveries")
    record["tools"].append(
        {
            "name": ADMIT,
            "capability": "dnd.srd.5.2.1.spells",
            "writers": ["dm"],
            "turn_statuses": ["open"],
            "state_predicates": [],
            "input_schema": _tool_schema(tools[ADMIT]),
        }
    )
    return [
        ADMIT,
        LOAD,
        QUALIFY,
        CAPTURE,
        RECONCILE,
        LIGHT_INSTALL,
        *FACT_NODES.values(),
        LIGHT,
        MENDING,
        *FACTS,
        *ROOTS,
    ]
