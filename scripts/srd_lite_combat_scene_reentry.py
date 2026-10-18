"""Admit later combat rosters without replacing saved spell or object consequences."""

from .srd_control_behaviors import _each, _push
from .srd_lite_combat_scene_setup import GUIDANCE, ROOTS, _setup_effects
from .srd_timed_actor_defenses import _call, _parameter, _require, _set, _tool, _when

ADMIT = "admit_srd_lite_combat_scene"
CAPTURE = "capture_srd_lite_exploration_objects"
RECONCILE = "reconcile_srd_lite_exploration_objects"
RECONCILE_ACTORS = "reconcile_srd_lite_exploration_created_actors"
RECONCILE_MAGE_HAND = "reconcile_srd_lite_exploration_mage_hand"
RECONCILE_STEED = "reconcile_srd_lite_exploration_steed_step"
PREPARE_STEED = "prepare_srd_lite_retired_steed_proxies"
ROWS = ("entities", "geometry", "zones")
VALIDATION_SCENE = "state.lite_combat_scene_admission"
MOVEMENT_ROOTS = ("move_srd_scene_creature", "move_srd_scene_creature_node")
MOVEMENT_ERROR = (
    "Coordinate movement is available during combat. "
    "Outside combat, use the established fictional travel without a map movement budget."
)
REENTRY_GUIDANCE = (
    GUIDANCE.split("An already saved scene", 1)[0]
    +
    "\nCoordinate combat uses the saved spatial scene. Supply one combat_scenes entry "
    "when admitting a later roster or an actual new arena. Omit it to reuse the scene. "
    "For the same map ID, keep the existing bounds, grid and layers: all saved bodies, "
    "objects, blockers, zones and terrain remain authoritative; supplied rows with "
    "existing IDs do not edit or relocate them. New IDs add genuinely present scene "
    "facts. For a new map ID, explicitly place every accompanying saved body/object "
    "by its exact ID. Existing dimensions, activity and mechanic metadata are retained; "
    "only actual destination position, facing, plane and ambient lighting are admitted. "
    "Include active summons, carried/worn objects, and a live Mage Hand with its cargo "
    "and caster. Arena replacement is unavailable while an anchored object, wall panel "
    "including permanent Stone, retained coordinate area/terrain or anchored light "
    "needs its current scene. Those effects are preserved and the operation clearly "
    "rejects before initiative; do not remove them or substitute a different map ID to "
    "bypass this limit. Exploration does not require map movement."
)


def _snapshot(output, scene="state.spatial_scene"):
    return {
        "spatial_query": {"scene": scene, "operation": "snapshot"},
        "store_as": output,
    }


def _same_map_header():
    """Treat a same-map scene as additions, preserving its coordinate frame and revision."""
    return [
        *[
            _require(
                " and ".join(
                    f"_reentry_requested.map.bounds.{edge}[{axis}] == "
                    f"_reentry_saved.map.bounds.{edge}[{axis}]"
                    for axis in range(3)
                ),
                "A same-map roster addition must preserve its bounds.",
            )
            for edge in ("min", "max")
        ],
        _require(
            " and ".join(
                [
                    "_reentry_requested.map.grid.cell_size == _reentry_saved.map.grid.cell_size",
                    "_reentry_requested.map.grid.unit == _reentry_saved.map.grid.unit",
                    *[
                        f"_reentry_requested.map.grid.origin[{axis}] == "
                        f"_reentry_saved.map.grid.origin[{axis}]"
                        for axis in range(2)
                    ],
                ]
            ),
            "A same-map roster addition must preserve its grid.",
        ),
        _require(
            "len(_reentry_requested.map.layers) == len(_reentry_saved.map.layers)",
            "A same-map roster addition must preserve its layers.",
        ),
        _set("_reentry_layer_index", 0),
        _set("_reentry_requested_layers", "_reentry_requested.map.layers"),
        _each(
            "_reentry_requested_layers",
            "_reentry_layer",
            [
                _set("_reentry_saved_layer", "_reentry_saved.map.layers[_reentry_layer_index]"),
                _require(
                    "_reentry_layer.id == _reentry_saved_layer.id and "
                    "_reentry_layer.min_elevation == _reentry_saved_layer.min_elevation and "
                    "_reentry_layer.max_elevation == _reentry_saved_layer.max_elevation",
                    "A same-map roster addition must preserve its layers.",
                ),
                {"increment": {"field": "_reentry_layer_index", "amount": 1}},
            ],
        ),
    ]


def _append_missing(collection):
    """Existing rows stay verbatim; genuinely new identities extend this collection."""
    return [
        _set("_reentry_rows", f"_reentry_saved.{collection}"),
        _set("_reentry_existing_ids", []),
        _each("_reentry_rows", "_reentry_row", [_push("_reentry_existing_ids", "_reentry_row.id")]),
        _set("_reentry_requested_rows", f"_reentry_requested.{collection}"),
        _each(
            "_reentry_requested_rows",
            "_reentry_row",
            [
                _when(
                    "_reentry_row.id not in _reentry_existing_ids",
                    [
                        _push("_reentry_rows", "_reentry_row"),
                        _set("_reentry_changed", True),
                    ],
                )
            ],
        ),
        _set("_reentry_patch", {collection: "_reentry_rows"}),
        _set("_reentry_next", "merge(_reentry_next, _reentry_patch)"),
    ]


def _anchored_sources():
    """Reject cross-map relocation of current sources whose owners read numeric scene origins."""
    return [
        _when(
            "state.persistent_spell_area_ids != null",
            [
                _set("_reentry_area_ids", "state.persistent_spell_area_ids"),
                _each(
                    "_reentry_area_ids",
                    "_reentry_area_id",
                    [
                        _set("_reentry_area", "state.persistent_spell_areas[_reentry_area_id]"),
                        _require(
                            "_reentry_area == null or _reentry_area.active != true or "
                            "(_reentry_area.expires_tick != null and state.lasting_tick >= "
                            "_reentry_area.expires_tick)",
                            "Finish the retained coordinate spell area before replacing its arena.",
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "state.solid_spell_wall_source_ids != null",
            [
                _set("_reentry_wall_ids", "state.solid_spell_wall_source_ids"),
                _each(
                    "_reentry_wall_ids",
                    "_reentry_wall_id",
                    [
                        _set("_reentry_wall", "state.solid_spell_wall_sources[_reentry_wall_id]"),
                        _require(
                            "_reentry_wall == null or _reentry_wall.installed != true",
                            "Arena replacement cannot discard wall panels, "
                            "including permanent Stone.",
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "state.turn_effects != null",
            [
                _set("_reentry_sources", "state.turn_effects"),
                _each(
                    "_reentry_sources",
                    "_reentry_source",
                    [
                        _when(
                            "_reentry_source.active != false and "
                            "(_reentry_source.expires_tick == null or state.lasting_tick < "
                            "_reentry_source.expires_tick)",
                            [
                                _require(
                                    "(_reentry_source.origin == null and "
                                    "_reentry_source.points == null and "
                                    "_reentry_source.ground_intersections == null) or "
                                    "(_reentry_source.kind == 'illumination_spell' and "
                                    "_reentry_source.source != null and "
                                    "_reentry_source.source != '')",
                                    "An anchored light or retained terrain still owns this arena.",
                                )
                            ],
                        )
                    ],
                ),
            ],
        ),
        *[
            effect
            for collection in ("geometry", "zones")
            for effect in (
                _set("_reentry_world_rows", f"_reentry_saved.{collection}"),
                _each(
                    "_reentry_world_rows",
                    "_reentry_world_row",
                    [
                        _require(
                            "_reentry_world_row.metadata.source_environment_id == null and "
                            "_reentry_world_row.metadata.solid_wall_source_id == null and "
                            "_reentry_world_row.metadata.srd_spell_object == null and "
                            "_reentry_world_row.metadata.overlay_area_id == null",
                            "Spell-owned geometry and terrain must keep their authoritative arena.",
                        )
                    ],
                ),
            )
        ],
    ]


def _append_terrain():
    """Terrain is keyed by cell and layer rather than scene object identity."""
    return [
        _set("_reentry_terrain_rows", "_reentry_saved.terrain"),
        _set("_reentry_requested_terrain", "_reentry_requested.terrain"),
        _each(
            "_reentry_requested_terrain",
            "_reentry_terrain_row",
            [
                _set("_reentry_terrain_exists", False),
                _each(
                    "_reentry_terrain_rows",
                    "_reentry_saved_terrain",
                    [
                        _when(
                            "_reentry_saved_terrain.layer_id == _reentry_terrain_row.layer_id and "
                            "_reentry_saved_terrain.cell[0] == _reentry_terrain_row.cell[0] and "
                            "_reentry_saved_terrain.cell[1] == _reentry_terrain_row.cell[1]",
                            [_set("_reentry_terrain_exists", True)],
                        )
                    ],
                ),
                _when(
                    "_reentry_terrain_exists != true",
                    [
                        _push("_reentry_terrain_rows", "_reentry_terrain_row"),
                        _set("_reentry_changed", True),
                    ],
                ),
            ],
        ),
        _set("_reentry_terrain_patch", {"terrain": "_reentry_terrain_rows"}),
        _set("_reentry_next", "merge(_reentry_next, _reentry_terrain_patch)"),
    ]


def _relocate_existing_body():
    """Admit placement only; retain source links, physical dimensions and object consequences."""
    return [
        _require(
            "_reentry_destination.metadata.participant_ref == "
            "_reentry_old_body.metadata.participant_ref",
            "A relocated body must preserve its exact participant binding.",
        ),
        _require(
            "_reentry_destination.metadata.plane != null and "
            "_reentry_destination.metadata.plane != '' and "
            "_reentry_destination.metadata.lighting != null",
            "Admit the relocated body's actual plane and ambient lighting.",
        ),
        _require(
            "_reentry_old_body.metadata.cover_from_points == null or "
            "len(_reentry_old_body.metadata.cover_from_points) == 0",
            "Existing point-specific cover needs its original arena; relocation is unavailable.",
        ),
        _set(
            "_reentry_spatial_metadata",
            {
                "plane": "_reentry_destination.metadata.plane",
                "lighting": "_reentry_destination.metadata.lighting",
            },
        ),
        _set(
            "_reentry_body_metadata",
            "merge(_reentry_old_body.metadata, _reentry_spatial_metadata)",
        ),
        _when(
            "_reentry_old_body.metadata.srd_object_breaks != null",
            [
                _set("_reentry_breaks", []),
                _set("_reentry_old_breaks", "_reentry_old_body.metadata.srd_object_breaks"),
                _each(
                    "_reentry_old_breaks",
                    "_reentry_break",
                    [
                        _push(
                            "_reentry_breaks",
                            {
                                "id": "_reentry_break.id",
                                **{
                                    edge: [
                                        f"_reentry_break.{edge}[{axis}] + "
                                        f"_reentry_destination.position[{axis}] - "
                                        f"_reentry_old_body.position[{axis}]"
                                        for axis in range(3)
                                    ]
                                    for edge in ("min", "max")
                                },
                            },
                        )
                    ],
                ),
                _set("_reentry_break_patch", {"srd_object_breaks": "_reentry_breaks"}),
                _set(
                    "_reentry_body_metadata",
                    "merge(_reentry_body_metadata, _reentry_break_patch)",
                ),
            ],
        ),
        _set(
            "_reentry_body_patch",
            {
                "map_id": "_reentry_requested.map.id",
                "position": "_reentry_destination.position",
                "facing": "_reentry_destination.facing",
                "metadata": "_reentry_body_metadata",
            },
        ),
        _set("_reentry_relocated", "merge(_reentry_old_body, _reentry_body_patch)"),
        _set("_reentry_replaced_rows", []),
        _set("_reentry_new_rows", "_reentry_next.entities"),
        _each(
            "_reentry_new_rows",
            "_reentry_new_body",
            [
                {
                    "condition": "_reentry_new_body.id == _reentry_old_body.id",
                    "on_true": [_push("_reentry_replaced_rows", "_reentry_relocated")],
                    "on_false": [_push("_reentry_replaced_rows", "_reentry_new_body")],
                }
            ],
        ),
        _set("_reentry_body_rows_patch", {"entities": "_reentry_replaced_rows"}),
        _set("_reentry_next", "merge(_reentry_next, _reentry_body_rows_patch)"),
    ]


def _new_arena(exploration_objects):
    """Preserve explicitly accompanying bodies; reject unsupported anchored world relocation."""
    return [
        *_anchored_sources(),
        _set("_reentry_map_changed", True),
        _set("_reentry_revision_patch", {"revision": "_reentry_saved.map.revision + 1"}),
        _set(
            "_reentry_map_patch",
            {"map": "merge(_reentry_requested.map, _reentry_revision_patch)"},
        ),
        _set("_reentry_requested", "merge(_reentry_requested, _reentry_map_patch)"),
        _set("_reentry_next", "_reentry_requested"),
        _set("_reentry_old_rows", "_reentry_saved.entities"),
        _set("_reentry_destination_rows", "_reentry_requested.entities"),
        _each(
            "_reentry_old_rows",
            "_reentry_old_body",
            [
                _set("_reentry_destination", None),
                _set("_reentry_managed_object", False),
                *(
                    [
                        _when(
                            "state.lite_exploration_objects != null and "
                            "_reentry_old_body.id in state.lite_exploration_objects",
                            [_set("_reentry_managed_object", True)],
                        ),
                        _when(
                            "_reentry_managed_object == true",
                            [
                                _set(
                                    "_reentry_old_body",
                                    "state.lite_exploration_objects[_reentry_old_body.id].body",
                                )
                            ],
                        ),
                    ]
                    if exploration_objects
                    else []
                ),
                _set(
                    "_reentry_creature",
                    "_reentry_old_body.metadata.participant_ref != null and "
                    "_reentry_old_body.metadata.participant_ref != '' and "
                    "_reentry_old_body.metadata.is_object != true",
                ),
                _set(
                    "_reentry_portable",
                    "_reentry_creature == true or "
                    "(_reentry_old_body.metadata.carried_by != null and "
                    "_reentry_old_body.metadata.carried_by != '') or "
                    "(_reentry_old_body.metadata.worn_by != null and "
                    "_reentry_old_body.metadata.worn_by != '')",
                ),
                _when(
                    "_reentry_old_body.metadata.srd_object_force.spell == 'mage_hand' or "
                    "_reentry_old_body.metadata.srd_spell_carrier.spell == 'mage_hand'",
                    [_set("_reentry_portable", True)],
                ),
                _when(
                    "(_reentry_old_body.metadata.spell_force == true and "
                    "(_reentry_old_body.metadata.source_ref == 'spiritual_weapon' or "
                    "_reentry_old_body.metadata.source_ref == 'unseen_servant' or "
                    "_reentry_old_body.metadata.source_ref == 'arcane_hand'))",
                    [_set("_reentry_portable", True)],
                ),
                _require(
                    "_reentry_portable == true or _reentry_managed_object == true or "
                    "(_reentry_old_body.active != true and "
                    "_reentry_old_body.metadata.is_object != true and "
                    "_reentry_old_body.metadata.solid_wall_source_id == null and "
                    "_reentry_old_body.metadata.srd_spell_object == null)",
                    "Arena replacement cannot discard an anchored object or wall panel.",
                ),
                _each(
                    "_reentry_destination_rows",
                    "_reentry_new_body",
                    [
                        _require(
                            "_reentry_creature != true or "
                            "_reentry_new_body.metadata.participant_ref != "
                            "_reentry_old_body.metadata.participant_ref or "
                            "_reentry_new_body.id == _reentry_old_body.id",
                            "An accompanying creature must preserve its exact saved body ID.",
                        ),
                        _when(
                            "_reentry_new_body.id == _reentry_old_body.id",
                            [_set("_reentry_destination", "_reentry_new_body")],
                        ),
                    ],
                ),
                _require(
                    "_reentry_old_body.active != true or "
                    "(_reentry_managed_object == true and "
                    "_reentry_old_body.metadata.srd_object_force.spell != 'mage_hand' and "
                    "_reentry_old_body.metadata.srd_spell_carrier.spell != 'mage_hand') or "
                    "(_reentry_creature == true and "
                    "_reentry_old_body.metadata.participant_ref not in actors and "
                    "_reentry_old_body.metadata.source_ref == null) or "
                    "_reentry_destination != null",
                    "Explicitly place each accompanying saved object, summon and selected body.",
                ),
                _when("_reentry_destination != null", _relocate_existing_body()),
            ],
        ),
        _set("state.spatial_scene", "_reentry_next"),
    ]


def _linked_body(subject):
    lookup = _call(
        "srd_information_scene_entity",
        {"subject": "{" + subject + "}", "lasting_state": "{_lasting_state_id}"},
    )
    lookup["export_context"] = {"_reentry_link_body": "_info_scene_entity"}
    return lookup


def _portable_links(exploration_objects):
    """Keep accompanying holders, casters and physical Mage Hand cargo on actual admitted bodies."""
    holder_checks = [
        _when(
            f"_reentry_portable_body.metadata.{field} != null and "
            f"_reentry_portable_body.metadata.{field} != ''",
            [
                _linked_body(f"_reentry_portable_body.metadata.{field}"),
                _require(
                    "_reentry_link_body.metadata.plane == _reentry_portable_body.metadata.plane",
                    "Accompanying objects and created bodies must share their holder/caster plane.",
                ),
            ],
        )
        for field in ("carried_by", "worn_by", "source_caster", "srd_object_force.caster")
    ]
    return [
        _set("_reentry_portable_rows", "state.spatial_scene.entities"),
        _each(
            "_reentry_portable_rows",
            "_reentry_portable_body",
            [
                _when(
                    "_reentry_portable_body.active == true",
                    [
                        *holder_checks,
                        _when(
                            "_reentry_portable_body.metadata.srd_object_force.spell == 'mage_hand'",
                            [
                                _require(
                                    "_reentry_portable_body.metadata.srd_object_force.caster "
                                    "!= null and "
                                    "_reentry_portable_body.metadata.srd_object_force.caster != ''",
                                    "The accompanying Mage Hand needs its actual caster.",
                                ),
                                _linked_body(
                                    "_reentry_portable_body.metadata.srd_object_force.caster"
                                ),
                                _require(
                                    "((_reentry_link_body.position[0] - "
                                    "_reentry_portable_body.position[0]) ** 2 + "
                                    "(_reentry_link_body.position[1] - "
                                    "_reentry_portable_body.position[1]) ** 2 + "
                                    "(_reentry_link_body.position[2] - "
                                    "_reentry_portable_body.position[2]) ** 2) ** 0.5 <= 30",
                                    "The accompanying Mage Hand must remain within "
                                    "30 feet of its caster.",
                                ),
                            ],
                        ),
                        _when(
                            "_reentry_portable_body.metadata.srd_spell_carrier.spell "
                            "== 'mage_hand'",
                            [
                                _linked_body(
                                    "_reentry_portable_body.metadata.srd_spell_carrier.force_id"
                                ),
                                _require(
                                    "_reentry_link_body.metadata.plane == "
                                    "_reentry_portable_body.metadata.plane and "
                                    "_reentry_link_body.metadata.srd_object_force.source_id == "
                                    "_reentry_portable_body.metadata.srd_spell_carrier.source_id",
                                    "Mage Hand cargo must retain its actual force and source link.",
                                ),
                                {
                                    "spatial_query": {
                                        "scene": "state.spatial_scene",
                                        "operation": "body_proximity",
                                        "query": {
                                            "map_id": "{state.spatial_scene.map.id}",
                                            "moving_entity_id": "{_reentry_portable_body.id}",
                                            "fixed_entity_id": "{_reentry_link_body.id}",
                                            "destination": "{_reentry_portable_body.position}",
                                            "radius": 0,
                                        },
                                    },
                                    "store_as": "_reentry_cargo_contact",
                                },
                                _require(
                                    "_reentry_cargo_contact.hit == true",
                                    "The accompanying Mage Hand must actually "
                                    "touch its retained cargo.",
                                ),
                            ],
                        ),
                    ],
                )
            ],
        ),
        _when(
            "state.turn_effects != null",
            [
                _set("_reentry_link_sources", "state.turn_effects"),
                _each(
                    "_reentry_link_sources",
                    "_reentry_link_source",
                    [
                        _when(
                            "_reentry_link_source.kind == 'illumination_spell' and "
                            "_reentry_link_source.source != null and "
                            "_reentry_link_source.source != '' and "
                            "(_reentry_link_source.expires_tick == null or "
                            "state.lasting_tick < _reentry_link_source.expires_tick)",
                            [
                                _set("_reentry_light_body_present", False),
                                _each(
                                    "_reentry_portable_rows",
                                    "_reentry_light_candidate",
                                    [
                                        _when(
                                            "_reentry_light_candidate.active == true and "
                                            "(_reentry_light_candidate.id == "
                                            "_reentry_link_source.source or "
                                            "_reentry_light_candidate.metadata.participant_ref == "
                                            "_reentry_link_source.source)",
                                            [_set("_reentry_light_body_present", True)],
                                        )
                                    ],
                                ),
                                _when(
                                    "_reentry_light_body_present == true or "
                                    "state.lite_exploration_objects["
                                    "_reentry_link_source.source] == null"
                                    if exploration_objects
                                    else "true",
                                    [_linked_body("_reentry_link_source.source")],
                                ),
                            ],
                        )
                    ],
                ),
            ],
        ),
    ]


def _admission_tool(
    exploration_objects, created_actors=False, mage_hand=False, steed=False, retired_steed=False
):
    capture = [_call(CAPTURE, {"lasting_state": "{_lasting_state_id}"})]
    reconcile = [_call(RECONCILE, {"lasting_state": "{_lasting_state_id}"})]
    return _tool(
        "Admit combat scene additions or explicit portable placement "
        "without resetting spell state.",
        [
            _parameter("scenes", "array", items={"type": "object"}, min_items=0, max_items=1),
            _parameter("actors", "array", items={"type": "string"}, max_items=64),
        ],
        [
            _require(
                "combat.encounter_active != true and combat.turn_active != true and "
                "combat.pending_action == null",
                "Finish the active encounter and pending action before admitting a combat scene.",
            ),
            *(
                [_call(PREPARE_STEED, {"lasting_state": "{_lasting_state_id}"})]
                if retired_steed
                else []
            ),
            *(capture if exploration_objects else []),
            _set("_reentry_saved", "state.spatial_scene"),
            _set("_reentry_map_changed", False),
            {
                "condition": "len(scenes) == 0",
                "on_true": [
                    _require(
                        "_reentry_saved != null",
                        "Starting coordinate combat needs one combat_scenes entry "
                        "or a saved scene.",
                    )
                ],
                "on_false": [
                    _set("_reentry_requested", "scenes[0]"),
                    _set(VALIDATION_SCENE, "_reentry_requested"),
                    _snapshot("_reentry_requested_snapshot", VALIDATION_SCENE),
                    _set(VALIDATION_SCENE, None),
                    {
                        "condition": "_reentry_saved == null",
                        "on_true": [
                            _set("state.spatial_scene", "_reentry_requested"),
                            _set("_reentry_map_changed", True),
                        ],
                        "on_false": [
                            {
                                "condition": "_reentry_requested.map.id == _reentry_saved.map.id",
                                "on_true": [
                                    *_same_map_header(),
                                    _set("_reentry_next", "_reentry_saved"),
                                    _set("_reentry_changed", False),
                                    *[effect for kind in ROWS for effect in _append_missing(kind)],
                                    *_append_terrain(),
                                    _when(
                                        "_reentry_changed == true",
                                        [
                                            _set("state.spatial_scene", "_reentry_next"),
                                            {
                                                "increment": {
                                                    "field": "state.spatial_scene.map.revision",
                                                    "amount": 1,
                                                }
                                            },
                                        ],
                                    ),
                                ],
                                "on_false": _new_arena(exploration_objects),
                            },
                        ],
                    },
                ],
            },
            *(
                [
                    _call(
                        RECONCILE_MAGE_HAND,
                        {"lasting_state": "{_lasting_state_id}", "scenes": "{scenes}"},
                    )
                ]
                if mage_hand
                else []
            ),
            *(reconcile if exploration_objects else []),
            *(
                [
                    _call(
                        RECONCILE_STEED,
                        {"lasting_state": "{_lasting_state_id}", "scenes": "{scenes}"},
                    )
                ]
                if steed
                else []
            ),
            *(
                [_call(RECONCILE_ACTORS, {"lasting_state": "{_lasting_state_id}"})]
                if created_actors
                else []
            ),
            _snapshot("_reentry_snapshot"),
            _when("_reentry_map_changed == true", _portable_links(exploration_objects)),
        ],
    )


def bind_combat_only_movement(runtime):
    """Reject exploration movement before the native movement owners read or spend state."""
    tools = runtime["tools"]
    guard = {"condition": "combat.encounter_active == true", "error": MOVEMENT_ERROR}
    for name in MOVEMENT_ROOTS:
        owner = tools[name]
        if guard in owner["pre_conditions"]:
            raise ValueError("Combat-only movement is already bound: " + name)
    for name in MOVEMENT_ROOTS:
        owner = tools[name]
        owner["pre_conditions"].insert(0, dict(guard))
        owner["description"] = "Combat only. " + owner["description"]
    return list(MOVEMENT_ROOTS)


def omit_combat_movement_exploration_budget(runtime, record):
    """Prune the private budget arm only after proving every entry has the combat guard."""
    from .srd_lite_reachable_tools import _strings

    tools = runtime["tools"]
    main, node = MOVEMENT_ROOTS
    celestial = "move_srd_scene_creature_celestial_segment"
    segment = "move_srd_scene_creature_environment_segment"
    parents = {node: {main}, celestial: {node}, segment: {celestial}}
    guard = {"condition": "combat.encounter_active == true", "error": MOVEMENT_ERROR}
    if any(guard not in tools[name]["pre_conditions"] for name in MOVEMENT_ROOTS):
        raise ValueError("Movement budget pruning requires both guarded entry owners")
    for name, expected in parents.items():
        if tools[name].get("metadata", {}).get("category") != "internal":
            raise ValueError("Movement budget pruning requires private segment owners")
        if name in set(_strings(record)) or name in set(_strings({
            key: value for key, value in runtime.items() if key != "tools"
        })):
            raise ValueError("Movement budget segment has a separate declared entry: " + name)
        actual = {
            caller for caller, owner in tools.items() if name in set(_strings(owner))
        }
        if actual != expected:
            raise ValueError("Movement budget segment caller drift: " + name)
    branches = [
        row for row in tools[segment]["effects"]
        if row.get("condition") == guard["condition"]
        and any(child.get("call") == "derive_srd_scene_movement_budget"
                for child in row.get("on_false", []))
    ]
    if len(branches) != 1:
        raise ValueError("Native private exploration movement budget drifted")
    branches[0]["on_false"] = [_require(guard["condition"], MOVEMENT_ERROR)]
    return segment


def bind_combat_scene_reentry(runtime):
    """Replace admission while keeping original body checks, initiative and result widgets."""
    tools = runtime["tools"]
    if ADMIT in tools:
        raise ValueError("Combat scene reentry is already bound")
    object_hooks = CAPTURE in tools and RECONCILE in tools
    if (CAPTURE in tools) != (RECONCILE in tools):
        raise ValueError("Combat admission requires both exploration object hooks")
    tools[ADMIT] = _admission_tool(
        object_hooks,
        RECONCILE_ACTORS in tools,
        RECONCILE_MAGE_HAND in tools,
        RECONCILE_STEED in tools,
        PREPARE_STEED in tools,
    )
    for name in ROOTS:
        owner = tools[name]
        if owner["effects"][0] != _setup_effects(name)[0] or GUIDANCE not in owner["description"]:
            raise ValueError("Combat reentry requires the final initial-scene admission: " + name)
        selected = (
            [_set("_reentry_combatants", "combatants")]
            if name == ROOTS[1]
            else [_set("_reentry_combatants", ["_first_actor_id", "_second_actor_id"])]
        )
        owner["effects"][:1] = [
            *selected,
            _call(
                ADMIT,
                {
                    "scenes": "{combat_scenes}",
                    "actors": "{_reentry_combatants}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
        ]
        owner["description"] = owner["description"].replace(GUIDANCE, REENTRY_GUIDANCE)
    return [*ROOTS, ADMIT]
