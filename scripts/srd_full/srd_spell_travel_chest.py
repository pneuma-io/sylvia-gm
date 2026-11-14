"""Secret Chest retains its actual components and relocates the same admitted chest."""

from copy import deepcopy

if __package__:
    from .srd_feather_fall import _ground
    from .srd_scene_relations import _array, _snapshot, _tool_scene
    from .srd_spell_object_content import _admission_guard, _metadata_patch
    from .srd_spell_travel_dwelling import _lookup, _save
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _optional, _public, _push
else:
    from srd_feather_fall import _ground
    from srd_scene_relations import _array, _snapshot, _tool_scene
    from srd_spell_object_content import _admission_guard, _metadata_patch
    from srd_spell_travel_dwelling import _lookup, _save
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _optional, _public, _push

PUBLIC_TOOLS = {"record_srd_secret_chest_components", "use_srd_secret_chest"}


def chest_intent():
    """Choose real components and an actual Ethereal destination anchor, never an outcome."""
    return [_parameter("chest_id"), _parameter("replica_id"), _parameter("ethereal_anchor")]


def _lot(identity, component, minimum):
    return [
        _set("_sc_lot", None),
        _set("_sc_lots", []),
        _when(
            "caster_state.spell_materials != null",
            [_set("_sc_lots", "caster_state.spell_materials")],
        ),
        _loop(
            "_sc_lots",
            "_sc_candidate",
            [
                _when(
                    f"_sc_candidate.id == {identity}",
                    [
                        _require("_sc_lot == null", "The actual material lot is ambiguous."),
                        _set("_sc_lot", "_sc_candidate"),
                    ],
                ),
            ],
        ),
        _require(
            f"_sc_lot != null and _sc_lot.quantity >= 1 and _sc_lot.value_cp >= {minimum} and "
            f"_sc_lot.kind == 'specific' and {component!r} in _sc_lot.components and "
            "_sc_lot.inventory_item in caster_state.inventory",
            "Select the actual possessed, correctly priced Secret Chest material component.",
        ),
    ]


def chest_tools(tools):
    """Reuse material lots, canonical container quantities, ground and the sole relocation owner."""
    if __package__:
        from .srd_spell_travel import ACTIVE, _measure, _move, _placement, _scene_subject
    else:
        from srd_spell_travel import ACTIVE, _measure, _move, _placement, _scene_subject

    def touch(identity, local):
        return [
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": identity,
                    "range_feet": 5,
                    "requires_sight": False,
                },
                ["_object_scene", "_world_caster_scene"],
            ),
            _set(local, "_object_scene"),
        ]

    physical = _tool_scene(
        "Qualify actual touched chest/replica dimensions and same admitted rare material mixture.",
        [_entity("caster_state"), _parameter("chest_id"), _parameter("replica_id")],
        [
            _require(
                "chest_id != replica_id", "Touch the actual chest and a different Tiny replica."
            ),
            *touch("{chest_id}", "_sc_chest"),
            *touch("{replica_id}", "_sc_replica"),
            *_scene_subject("{_caster_state_id}", "_sc_caster"),
            _require(
                "_sc_chest.metadata.is_object == true and "
                "_sc_replica.metadata.is_object == true and "
                "_sc_chest.footprint.kind == 'box' and _sc_replica.footprint.kind == 'box'",
                "Use the two actual admitted box objects.",
            ),
            _require(
                "((_sc_chest.footprint.half_extents[0] == 1.5 and "
                "_sc_chest.footprint.half_extents[1] == 1) or "
                "(_sc_chest.footprint.half_extents[0] == 1 and "
                "_sc_chest.footprint.half_extents[1] == 1.5)) and _sc_chest.height == 2",
                "The actual chest must measure three by two by two feet.",
            ),
            _require(
                "_sc_replica.metadata.srd_spell_object.size == 'tiny' and "
                "_sc_replica.metadata.srd_spell_object.destroyed == false and "
                "_sc_chest.metadata.srd_spell_object.destroyed != true",
                "The actual Tiny replica must retain its admitted object profile and be intact.",
            ),
            _require(
                "_sc_chest.metadata.srd_object_material != null and "
                "_sc_replica.metadata.srd_object_material != null",
                "Admit the actual chest and replica materials first.",
            ),
            _set("_sc_materials", "_sc_chest.metadata.srd_object_material.materials"),
            _require(
                "len(_sc_materials) == len(_sc_replica.metadata.srd_object_material.materials)",
                "The actual replica must use the same rare material mixture.",
            ),
            _loop(
                "_sc_materials",
                "_sc_material",
                [
                    _require(
                        "_sc_material in _sc_replica.metadata.srd_object_material.materials",
                        "The actual replica must use the same rare material mixture.",
                    ),
                ],
            ),
            _require(
                "_sc_chest.metadata.carried_by == null and _sc_chest.metadata.worn_by == null and "
                "_sc_replica.metadata.carried_by == null and _sc_replica.metadata.worn_by == null",
                "Held or worn components need their actual physical transfer owner.",
            ),
        ],
    )
    binding = _tool_scene(
        "Bind one actual component through its canonical object metadata owner.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("material_id"),
            _parameter("role", "enum", enum_values=["chest", "replica"]),
        ],
        [
            *_admission_guard(),
            _when("role == 'chest'", _lot("material_id", "secret_chest.1", 500000)),
            _when("role == 'replica'", _lot("material_id", "secret_chest.2", 5000)),
            _require(
                "_object_scene.metadata.srd_secret_chest_component == null",
                "This actual component already has its material identity.",
            ),
            _require(
                "_object_scene.metadata.srd_object_utility.inventory_item "
                "== _sc_lot.inventory_item",
                "The actual object must be the selected inventory component.",
            ),
            *_metadata_patch(
                {
                    "srd_secret_chest_component": {
                        "owner": "_caster_state_id",
                        "material_id": "material_id",
                        "inventory_item": "_sc_lot.inventory_item",
                        "role": "role",
                    }
                }
            ),
        ],
    )
    bind = _public(
        _tool_scene(
            "Bind the actual chest and Tiny replica to their existing priced "
            "specific material lots.",
            [
                _entity("caster_state"),
                _parameter("chest_id"),
                _parameter("replica_id"),
                _parameter("chest_material_id"),
                _parameter("replica_material_id"),
            ],
            [
                _call(
                    "qualify_srd_secret_chest_components",
                    {
                        "caster_state": "{_caster_state_id}",
                        "chest_id": "{chest_id}",
                        "replica_id": "{replica_id}",
                    },
                    ["_sc_chest", "_sc_replica"],
                ),
                _require(
                    "chest_material_id != replica_material_id", "Use two actual material lots."
                ),
                *[
                    _call(
                        "bind_srd_secret_chest_component",
                        {
                            "caster_state": "{_caster_state_id}",
                            "object_id": "{" + role + "_id}",
                            "material_id": "{" + role + "_material_id}",
                            "role": role,
                        },
                    )
                    for role in ("chest", "replica")
                ],
            ],
        ),
        "information",
        "Secret Chest components",
        [("Chest", "_sc_chest.id"), ("Replica", "_sc_replica.id")],
    )
    contents = _tool_scene(
        "Retain canonical nonliving water; reject unsupported nested contents or living occupants.",
        [_parameter("chest_id")],
        [
            *_scene_subject("{chest_id}", "_sc_chest"),
            _require(
                "_sc_chest.metadata.srd_object_container != null",
                "Admit the actual container quantities before hiding the chest.",
            ),
            _require(
                "_sc_chest.metadata.srd_object_container.water_gallons >= 0 and "
                "_sc_chest.metadata.srd_object_container.water_gallons * 231 <= 12 * 1728 and "
                "_sc_chest.metadata.srd_object_container.food_pounds == 0",
                "The initial contents mode supports at most twelve cubic feet of canonical water; "
                "food volume and other nested inventory remain unsupported.",
            ),
            _set("_sc_entities", "state.spatial_scene.entities"),
            _loop(
                "_sc_entities",
                "_sc_inside",
                [
                    _when(
                        "_sc_inside.id != chest_id and _sc_inside.active == true and "
                        "_sc_inside.metadata.plane == _sc_chest.metadata.plane",
                        [
                            _require(
                                "_sc_inside.footprint.kind == 'box'",
                                "Curved possible contents need their actual containment owner.",
                            ),
                            _set("_sc_contained", True),
                            *[
                                _when(
                                    f"_sc_inside.position[{axis}] - "
                                    f"_sc_inside.footprint.half_extents[{axis}] < "
                                    f"_sc_chest.position[{axis}] - "
                                    f"_sc_chest.footprint.half_extents[{axis}] or "
                                    f"_sc_inside.position[{axis}] + "
                                    f"_sc_inside.footprint.half_extents[{axis}] > "
                                    f"_sc_chest.position[{axis}] + "
                                    f"_sc_chest.footprint.half_extents[{axis}]",
                                    [_set("_sc_contained", False)],
                                )
                                for axis in range(2)
                            ],
                            _when(
                                "_sc_inside.position[2] < _sc_chest.position[2] or "
                                "_sc_inside.position[2] + _sc_inside.height > "
                                "_sc_chest.position[2] + _sc_chest.height",
                                [_set("_sc_contained", False)],
                            ),
                            _require(
                                "_sc_contained != true",
                                "Actual nested scene contents need their containment/transfer "
                                "owner.",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    magic_effects = [
        _require(
            "state.has_spell_magic_zones != true", "Load the actual magic-zone consumer first."
        )
    ]
    if "check_srd_magic_zone_effect" in tools:
        magic_effects = [
            _call(
                "check_srd_magic_zone_effect",
                {
                    "source_subject": "{caster_subject}",
                    "target_subject": "{object_subject}",
                    "effect_kind": "spell",
                    "actual_spell_level": "{slot_level}",
                },
                ["_magic_zone_effect_allowed"],
            ),
            _require(
                "_magic_zone_effect_allowed == true",
                "The actual magic zone suppresses this Secret Chest ability.",
            ),
        ]
    magic = _tool_scene(
        "Qualify actual Secret Chest caster and component against active magic zones.",
        [
            _parameter("caster_subject"),
            _parameter("object_subject"),
            _parameter("slot_level", "integer", minimum=4, maximum=9),
        ],
        magic_effects,
    )
    preflight = _tool_scene(
        "Validate actual components, material identity and unoccupied "
        "Ethereal placement before payment.",
        [
            _entity("caster_state"),
            *chest_intent(),
            _parameter("material_item_ids", "array", items={"type": "string"}),
        ],
        [
            _call(
                "qualify_srd_secret_chest_components",
                {
                    "caster_state": "{_caster_state_id}",
                    "chest_id": "{chest_id}",
                    "replica_id": "{replica_id}",
                },
                ["_sc_chest", "_sc_replica", "_sc_caster"],
            ),
            *[
                effect
                for role, minimum in (("chest", 500000), ("replica", 5000))
                for effect in [
                    _set("_sc_binding", f"_sc_{role}.metadata.srd_secret_chest_component"),
                    _require(
                        f"_sc_binding.owner == _caster_state_id and "
                        f"_sc_binding.role == {role!r} and "
                        "_sc_binding.material_id in material_item_ids",
                        "The paid cast must use these actual bound components.",
                    ),
                    *_lot(
                        "_sc_binding.material_id",
                        f"secret_chest.{1 if role == 'chest' else 2}",
                        minimum,
                    ),
                    _require(
                        "_sc_binding.inventory_item == _sc_lot.inventory_item",
                        "The actual component identity changed.",
                    ),
                ]
            ],
            _call("qualify_srd_secret_chest_contents", {"chest_id": "{chest_id}"}),
            *_scene_subject("{ethereal_anchor}", "_sc_anchor"),
            _require(
                "_sc_anchor.metadata.plane == 'ethereal'",
                "Select an actual admitted Ethereal destination anchor.",
            ),
            *_placement("{chest_id}", "{_sc_anchor.position}", "ethereal", []),
            _set("_sc_hidden_position", "_travel_placement.position"),
        ],
    )
    install = _tool_scene(
        "Hide the same paid chest and retain its exact source, replica "
        "and actual Ethereal location.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer", minimum=4, maximum=9),
            *chest_intent(),
            _parameter("material_item_ids", "array", items={"type": "string"}),
            _optional("spell_cast_id", default="", model_hidden=True),
            _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
            _optional(
                "actual_spell_level", "integer", -1, minimum=-1, maximum=9, model_hidden=True
            ),
        ],
        [
            _call(
                "validate_srd_secret_chest_cast",
                {
                    "caster_state": "{_caster_state_id}",
                    **{p["name"]: "{" + p["name"] + "}" for p in chest_intent()},
                    "material_item_ids": "{material_item_ids}",
                },
                ["_sc_chest", "_sc_replica", "_sc_caster", "_sc_hidden_position"],
            ),
            _call(
                "check_srd_secret_chest_magic",
                {
                    "caster_subject": "{_sc_caster.id}",
                    "object_subject": "{chest_id}",
                    "slot_level": "{slot_level}",
                },
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _set("_sc_serial", 1),
            _when(
                "caster_state.secret_chest_serial != null",
                [_set("_sc_serial", "caster_state.secret_chest_serial + 1")],
            ),
            _set("caster_state.secret_chest_serial", "_sc_serial"),
            _set("_sc_sources", []),
            _when(
                "state.extradimensional_sources != null",
                [_set("_sc_sources", "state.extradimensional_sources")],
            ),
            _loop(
                "_sc_sources",
                "_sc_previous",
                [
                    _when(
                        "_sc_previous.spell == 'secret_chest' and _sc_previous.active == true and "
                        "_sc_previous.caster == _caster_state_id",
                        [
                            _call(
                                "end_srd_secret_chest_source", {"source_id": "{_sc_previous.id}"}
                            ),
                        ],
                    ),
                ],
            ),
            _set(
                "_xd_source",
                {
                    "id": "_caster_state_id + ':secret_chest:' + str(_sc_serial)",
                    "spell": "'secret_chest'",
                    "caster": "_caster_state_id",
                    "slot_level": "slot_level",
                    "spell_level": "slot_level",
                    "active": True,
                    "cast_id": "spell_cast_id",
                    "source_caster_id": None,
                    "ongoing_duration": "spell_cast_id != ''",
                    "created_tick": "state.lasting_tick",
                    "expires_tick": None,
                    "chest_id": "chest_id",
                    "replica_id": "replica_id",
                    "ethereal_anchor": "ethereal_anchor",
                    "hidden_position": "_sc_hidden_position",
                    "return_pending": False,
                    "last_risk_day": 60,
                    "risk_rolls": [],
                },
            ),
            _when(
                "spell_cast_id != ''",
                [
                    _require(
                        "spell_cast_owner != null and _spell_cast_owner_id == _caster_state_id and "
                        "caster_state.last_spell_source_cast_id == spell_cast_id and "
                        "caster_state.last_spell_source_spell == 'secret_chest'",
                        "Retain the authentic committed Secret Chest identity.",
                    ),
                    _set("_sc_identity", {"source_caster_id": "_spell_cast_owner_id"}),
                    _set("_xd_source", "merge(_xd_source, _sc_identity)"),
                ],
            ),
            _set("_sc_sources", []),
            _when(
                "state.extradimensional_sources != null",
                [_set("_sc_sources", "state.extradimensional_sources")],
            ),
            _push("_sc_sources", "_xd_source"),
            _set("state.extradimensional_sources", "_sc_sources"),
            _set("state.has_secret_chest_sources", True),
            *_move("{chest_id}", "{_sc_hidden_position}", "ethereal"),
        ],
    )
    end = _tool_scene(
        "End this exact source without relocating a chest left on the Ethereal Plane.",
        [_parameter("source_id")],
        [
            *_lookup(),
            _require("_xd_source.spell == 'secret_chest'", "Select an actual Secret Chest source."),
            _set("_sc_ended", {"active": False}),
            _set("_xd_source", "merge(_xd_source, _sc_ended)"),
            *_save(),
        ],
    )
    refresh = _tool_scene(
        "Resolve actual replica destruction and every due cumulative "
        "daily percentile once, at most twenty.",
        [],
        [
            _set("_sc_sources", []),
            _when(
                "state.extradimensional_sources != null",
                [_set("_sc_sources", "state.extradimensional_sources")],
            ),
            _loop(
                "_sc_sources",
                "_sc_daily",
                [
                    _when(
                        "_sc_daily.spell == 'secret_chest' and _sc_daily.active == true",
                        [
                            *_lookup("_sc_daily.id"),
                            _set("_sc_replica_present", False),
                            _set("_sc_objects", "state.spatial_scene.entities"),
                            _loop(
                                "_sc_objects",
                                "_sc_object",
                                [
                                    _when(
                                        "_sc_object.id == _xd_source.replica_id and "
                                        "_sc_object.active "
                                        "== true and "
                                        "_sc_object.metadata.srd_spell_object.destroyed == false",
                                        [_set("_sc_replica_present", True)],
                                    ),
                                ],
                            ),
                            _when(
                                "_sc_replica_present != true",
                                [
                                    _set("_sc_ended", {"active": False}),
                                    _set("_xd_source", "merge(_xd_source, _sc_ended)"),
                                ],
                            ),
                            _set(
                                "_sc_day",
                                "floor((state.lasting_tick - _xd_source.created_tick) * 28800 "
                                "** -1)",
                            ),
                            _set("_sc_due_days", list(range(61, 81))),
                            _loop(
                                "_sc_due_days",
                                "_sc_due_day",
                                [
                                    _when(
                                        "_xd_source.active == true and _sc_due_day <= _sc_day and "
                                        "_sc_due_day > _xd_source.last_risk_day",
                                        [
                                            {
                                                "dice": {"expression": "1d100"},
                                                "store_as": "_sc_percentile",
                                            },
                                            _set("_sc_chance", "min(100, (_sc_due_day - 60) * 5)"),
                                            _set("_sc_rolls", "_xd_source.risk_rolls"),
                                            _push(
                                                "_sc_rolls",
                                                {
                                                    "day": "_sc_due_day",
                                                    "chance": "_sc_chance",
                                                    "roll": "_sc_percentile.total",
                                                },
                                            ),
                                            _set(
                                                "_sc_daily_update",
                                                {
                                                    "last_risk_day": "_sc_due_day",
                                                    "risk_rolls": "_sc_rolls",
                                                },
                                            ),
                                            _set(
                                                "_xd_source", "merge(_xd_source, _sc_daily_update)"
                                            ),
                                            _when(
                                                "_sc_percentile.total <= _sc_chance",
                                                [
                                                    _set("_sc_ended", {"active": False}),
                                                    _set(
                                                        "_xd_source", "merge(_xd_source, _sc_ended)"
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            *_save(),
                        ],
                    ),
                ],
            ),
        ],
    )
    recall = _tool_scene(
        "Qualify actual unoccupied box-ground recall within five feet "
        "of the caster before Action payment.",
        [_entity("caster_state"), _parameter("chest_id"), _array("destination")],
        [
            *_snapshot(),
            *_scene_subject("{_caster_state_id}", "_sc_caster"),
            *_scene_subject("{chest_id}", "_sc_chest"),
            *_measure("{_sc_caster.position}", "{destination}", "_sc_distance"),
            _require(
                "_sc_distance <= 5", "Recall the chest within five feet of the actual caster."
            ),
            _set("_info_scene_entity", "_sc_caster"),
            _set("_info_position", "destination"),
            *_ground(),
            _require(
                "abs(_fall_ground_height - destination[2]) <= 0.000001",
                "The recall destination must be on actual ground.",
            ),
            _set("_sc_ground", None),
            _set("_sc_ground_rows", "state.spatial_scene.geometry"),
            _loop(
                "_sc_ground_rows",
                "_sc_support",
                [
                    _when("_sc_support.id == _fall_ground_id", [_set("_sc_ground", "_sc_support")]),
                ],
            ),
            _require(
                "_sc_ground.volume.kind == 'box'",
                "Curved ground needs its full-footprint support owner.",
            ),
            *[
                _require(
                    f"destination[{axis}] - _sc_chest.footprint.half_extents[{axis}] "
                    f">= _sc_ground.volume.min[{axis}] and "
                    f"destination[{axis}] + _sc_chest.footprint.half_extents[{axis}] "
                    f"<= _sc_ground.volume.max[{axis}]",
                    "The actual ground must support the entire chest footprint.",
                )
                for axis in range(2)
            ],
            *_placement("{chest_id}", "{destination}", "{_sc_caster.metadata.plane}", []),
            _require(
                "_travel_placement.distance <= 0.000001",
                "Recall needs an actual unoccupied ground destination.",
            ),
        ],
    )
    use = _public(
        _tool_scene(
            "Touch the authentic replica to recall, or both authentic "
            "components to hide, with one Magic Action.",
            [
                _entity("actor_state"),
                _parameter("source_id"),
                _parameter("operation", "enum", enum_values=["recall", "hide"]),
                _array("destination", default=[0, 0, 0]),
            ],
            [
                _require(ACTIVE, "Only an acting living caster can use Secret Chest."),
                _set("_sc_operation", "operation"),
                _require(
                    "combat.pending_action == null", "Finish the actual pending action first."
                ),
                _call("refresh_srd_secret_chest_sources", {}),
                *_lookup(),
                _require(
                    "_xd_source.spell == 'secret_chest' and _xd_source.active == "
                    "true and _xd_source.caster == _actor_state_id",
                    "Use the actual caster's active Secret Chest source.",
                ),
                _call(
                    "resolve_srd_object_content_geometry",
                    {
                        "caster_state": "{_actor_state_id}",
                        "object_id": "{_xd_source.replica_id}",
                        "range_feet": 5,
                        "requires_sight": False,
                    },
                    ["_object_scene", "_world_caster_scene"],
                ),
                _set("_sc_replica", "_object_scene"),
                _set("_sc_caster", "_world_caster_scene"),
                *_scene_subject("{_xd_source.chest_id}", "_sc_chest"),
                _call(
                    "check_srd_secret_chest_magic",
                    {
                        "caster_subject": "{_sc_caster.id}",
                        "object_subject": "{_sc_replica.id}",
                        "slot_level": "{_xd_source.slot_level}",
                    },
                ),
                _call(
                    "check_srd_secret_chest_magic",
                    {
                        "caster_subject": "{_sc_caster.id}",
                        "object_subject": "{_sc_chest.id}",
                        "slot_level": "{_xd_source.slot_level}",
                    },
                ),
                _when(
                    "operation == 'recall'",
                    [
                        _require(
                            "_sc_chest.metadata.plane == 'ethereal'",
                            "Recall the actual chest from the Ethereal Plane.",
                        ),
                        _call(
                            "qualify_srd_secret_chest_recall",
                            {
                                "caster_state": "{_actor_state_id}",
                                "chest_id": "{_xd_source.chest_id}",
                                "destination": "{destination}",
                            },
                        ),
                        _set("_sc_destination", "destination"),
                        _set("_sc_destination_plane", "_sc_caster.metadata.plane"),
                    ],
                ),
                _when(
                    "operation == 'hide'",
                    [
                        _call(
                            "resolve_srd_object_content_geometry",
                            {
                                "caster_state": "{_actor_state_id}",
                                "object_id": "{_xd_source.chest_id}",
                                "range_feet": 5,
                                "requires_sight": False,
                            },
                        ),
                        _call(
                            "qualify_srd_secret_chest_contents",
                            {"chest_id": "{_xd_source.chest_id}"},
                        ),
                        *_placement(
                            "{_xd_source.chest_id}",
                            "{_xd_source.hidden_position}",
                            "ethereal",
                            [],
                        ),
                        _set("_sc_destination", "_travel_placement.position"),
                        _set("_sc_destination_plane", "'ethereal'"),
                    ],
                ),
                _when(
                    "combat.encounter_active == true",
                    [
                        _require(
                            "combat.active_actor == _actor_state_id",
                            "Only the active caster can use this Magic Action.",
                        ),
                        _call("spend_srd_action", {"magic_action": True}),
                    ],
                ),
                *_move("{_xd_source.chest_id}", "{_sc_destination}", "{_sc_destination_plane}"),
            ],
        ),
        "spell",
        "Secret Chest",
        [("Operation", "_sc_operation"), ("Chest", "_xd_source.chest_id")],
    )
    return {
        "qualify_srd_secret_chest_components": physical,
        "bind_srd_secret_chest_component": binding,
        "record_srd_secret_chest_components": bind,
        "qualify_srd_secret_chest_contents": contents,
        "check_srd_secret_chest_magic": magic,
        "validate_srd_secret_chest_cast": preflight,
        "install_srd_secret_chest": install,
        "end_srd_secret_chest_source": end,
        "refresh_srd_secret_chest_sources": refresh,
        "qualify_srd_secret_chest_recall": recall,
        "use_srd_secret_chest": use,
    }


def bind_chest_consumers(tools, changed):
    """The existing lasting clock owns daily risk; the source record owns retry evidence."""
    name = "advance_srd_lasting_clock"
    if name in tools:
        clock = deepcopy(changed.get(name, tools[name]))
        if "refresh_srd_secret_chest_sources" not in str(clock["effects"]):
            clock["effects"].append(
                _when(
                    "state.has_secret_chest_sources == true",
                    [
                        _call("refresh_srd_secret_chest_sources", {}),
                    ],
                )
            )
        changed[name] = clock
