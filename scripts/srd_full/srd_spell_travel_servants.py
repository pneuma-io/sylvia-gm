"""Mansion staff use source-qualified scene bodies and canonical ordinary object tasks."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _snapshot, _spatial, _tool_scene
    from .srd_spell_object_content import _lookup_object, _metadata_patch
    from .srd_spell_travel_dwelling import _lookup
    from .srd_spell_travel_furnishings import furnishing_intent, furnishing_tools
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _public, _push
else:
    from srd_scene_relations import _array, _snapshot, _spatial, _tool_scene
    from srd_spell_object_content import _lookup_object, _metadata_patch
    from srd_spell_travel_dwelling import _lookup
    from srd_spell_travel_furnishings import furnishing_intent, furnishing_tools
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _public, _push

PUBLIC_TOOLS = {"command_srd_mansion_servant"}


def servant_intent(name="servants"):
    """One staff body receives explicit unprinted geometry and appearance at completion."""
    intent = furnishing_intent(name)
    intent["max_items"] = 1
    item = intent["items"]
    item["properties"].pop("material")
    item["required"].remove("material")
    item["properties"].update(
        index={"type": "integer", "minimum": 1, "maximum": 100},
        attire={"type": "string", "minLength": 1},
    )
    item["required"].extend(["index", "attire"])
    return intent


def _qualification(identity="source_id", body_identity="servant_id"):
    """Authenticate the actual body against one member of the retained hundred-person staff."""
    return [
        *_lookup(identity),
        _require(
            "_xd_source.spell == 'magnificent_mansion' and _xd_source.servant_indices != null",
            "Choose an actual Mansion staff source.",
        ),
        _set("_ms_body", None),
        _set("_ms_bodies", "state.spatial_scene.entities"),
        _loop(
            "_ms_bodies",
            "_ms_candidate",
            [_when(f"_ms_candidate.id == {body_identity}", [_set("_ms_body", "_ms_candidate")])],
        ),
        _require(
            "_ms_body != null and _ms_body.metadata.mansion_servant != null",
            "Choose an actual admitted Mansion servant body.",
        ),
        _set("_ms_marker", "_ms_body.metadata.mansion_servant"),
        _require(
            f"_ms_marker.source_id == {identity} and _ms_marker.cast_id == _xd_source.cast_id and "
            "_ms_marker.created_tick == _xd_source.created_tick and "
            "_ms_marker.room_id == _xd_source.room_id and "
            "_ms_marker.index in _xd_source.servant_indices",
            "Retain the authentic staff member and exact cast identity.",
        ),
        _set("_ms_owned", False),
        _set("_ms_intents", "_xd_source.servants"),
        _loop(
            "_ms_intents",
            "_ms_intent",
            [
                _when(
                    f"_ms_intent.id == {body_identity} and _ms_intent.index == _ms_marker.index",
                    [_set("_ms_owned", True)],
                )
            ],
        ),
        _require(
            "_ms_owned == true and _ms_body.metadata.creature_type == 'spell_force' and "
            "_ms_body.metadata.is_object == false and _ms_body.metadata.participant_ref == null",
            "The actual source must own this staff body without invented creature statistics.",
        ),
        _set("_ms_room", None),
        _set("_ms_rooms", "state.spatial_scene.zones"),
        _loop(
            "_ms_rooms",
            "_ms_zone",
            [_when("_ms_zone.id == _xd_source.room_id", [_set("_ms_room", "_ms_zone")])],
        ),
        _require(
            "_ms_room != null and _ms_room.shape.kind == 'box' and "
            f"_ms_room.metadata.source_id == {identity} and "
            "_ms_body.metadata.plane == _xd_source.room_id",
            "The actual staff body must stay inside its own retained room.",
        ),
    ]


def _living_source():
    return [
        _require(
            "_xd_source.active == true and state.lasting_tick < _xd_source.expires_tick and "
            "_ms_body.active == true and _ms_room.metadata.active == true",
            "The actual Mansion staff has ended.",
        ),
    ]


def _contact():
    """Require touching admitted box faces; no numerical reach or anatomy is invented."""
    return [
        *_lookup_object(),
        _require(
            "_object_scene.metadata.is_object == true and "
            "_object_scene.metadata.plane == _xd_source.room_id and "
            "_ms_body.footprint.kind == 'box' and _object_scene.footprint.kind == 'box'",
            "A servant task needs a real same-room object and supported body contact geometry.",
        ),
        *[
            _set(
                f"_ms_contact_{axis}",
                f"abs(_ms_body.position[{axis}] - _object_scene.position[{axis}]) <= "
                f"_ms_body.footprint.half_extents[{axis}] + "
                f"_object_scene.footprint.half_extents[{axis}]",
            )
            for axis in range(2)
        ],
        _set(
            "_ms_contact_2",
            "_ms_body.position[2] <= _object_scene.position[2] + _object_scene.height and "
            "_object_scene.position[2] <= _ms_body.position[2] + _ms_body.height",
        ),
        _require(
            "_ms_contact_0 == true and _ms_contact_1 == true and _ms_contact_2 == true",
            "The actual servant body must contact the object; ordinary arm reach remains selected.",
        ),
        _set("_ms_face_contact", False),
        *[
            _when(
                f"abs(_ms_body.position[{axis}] - _object_scene.position[{axis}]) == "
                f"_ms_body.footprint.half_extents[{axis}] + "
                f"_object_scene.footprint.half_extents[{axis}]",
                [_set("_ms_face_contact", True)],
            )
            for axis in range(2)
        ],
        _when(
            "_ms_body.position[2] == _object_scene.position[2] + _object_scene.height or "
            "_object_scene.position[2] == _ms_body.position[2] + _ms_body.height",
            [_set("_ms_face_contact", True)],
        ),
        _require(
            "_ms_face_contact == true",
            "Use actual touching surfaces, not overlapping invented servant placement.",
        ),
        *_snapshot(),
        _spatial(
            "exposure",
            {
                "map_id": "{state.spatial_scene.map.id}",
                "observer": {"point": "{_ms_body.position}"},
                "target": {"entity_id": "{object_id}"},
                "samples": {"horizontal": 3, "vertical": 3},
                "include": ["geometry"],
                "metadata_filter": {
                    "key": "plane",
                    "value": "{_xd_source.room_id}",
                    "missing_matches": False,
                },
            },
            "_ms_contact_exposure",
        ),
        _require(
            "_ms_contact_exposure.visible_samples > 0", "A real solid blocks this servant contact."
        ),
    ]


def _access_node(tools):
    """Keep final mundane and magical access rules after source-owned direct contact."""
    fallback = [_require("false", "Servant access needs the actual final object access owner.")]
    if "operate_srd_locked_object_node" in tools:
        node = tools["operate_srd_locked_object_node"]
        index = next(
            i
            for i, row in enumerate(node["effects"])
            if row.get("call") == "resolve_srd_object_content_geometry"
        )
        fallback = deepcopy(node["effects"][index + 1 :])
        password = [
            row for row in fallback if row.get("condition") == "operation == 'speak_password'"
        ]
        if len(password) != 1:
            raise ValueError(
                "Canonical access must retain one actual password/open-close boundary."
            )
        position = fallback.index(password[0])
        fallback[position : position + 1] = password[0]["on_false"]
    return _tool_scene(
        "Perform final ordinary open/close as the actual Mansion staff member, not its caster.",
        [
            _parameter("source_id"),
            _parameter("servant_id"),
            _parameter("object_id"),
            _parameter("operation", "enum", enum_values=["open", "close"]),
        ],
        [
            *_qualification(),
            *_living_source(),
            *_contact(),
            _set("_actor_state_id", "servant_id"),
            *fallback,
        ],
    )


def _pour_node(tools, *, capture=False):
    """Capture canonical pouring only after the final Mage Hand extraction exists."""
    facts = [_require("false", "Servant pouring needs the actual plain-water vial owner.")]
    tail = []
    if capture:
        if not {"qualify_srd_plain_water_vial", "pour_srd_object_vial_source_node"} <= tools.keys():
            raise ValueError("Final servant pouring needs the actual plain-water vial owner.")
        qualifier = tools["qualify_srd_plain_water_vial"]["effects"]
        owner = tools["pour_srd_object_vial_source_node"]["effects"]
        if (
            qualifier[0].get("call") != "resolve_srd_object_content_geometry"
            or owner[0].get("call") != "qualify_srd_plain_water_vial"
        ):
            raise ValueError("Plain-water pouring must retain its genuine qualification boundary.")
        facts = deepcopy(qualifier[1:])
        tail = deepcopy(owner[1:])
        if sum("spatial_mutation" in row for row in tail) != 1:
            raise ValueError("Servant pouring must retain one canonical vial mutation.")
    return _tool_scene(
        "Pour a real touching open plain-water vial through its canonical contents mutation.",
        [_parameter("source_id"), _parameter("servant_id"), _parameter("object_id")],
        [
            *_qualification(),
            *_living_source(),
            *_contact(),
            _require(
                "_object_scene.metadata.carried_by == null and "
                "_object_scene.metadata.worn_by == null",
                "Held or worn vials need their actual holder and servant manipulation owner.",
            ),
            *facts,
            *tail,
        ],
    )


def servant_tools(tools):
    """Create only selected real bodies; the staff remains exactly one hundred source members."""
    if __package__:
        from .srd_spell_travel import ACTIVE, _scene_subject
    else:
        from srd_spell_travel import ACTIVE, _scene_subject

    create = deepcopy(furnishing_tools()["create_srd_mansion_furnishing"])
    create["description"] = (
        "Create one explicit Mansion staff body without inventing its statistics."
    )
    for effect in create["effects"]:
        change = effect.get("set", {})
        if change.get("field") == "_mf_saved_intents":
            change["value"] = "_xd_source.servants"
        if change.get("field") == "_world_created_marker":
            change["value"] = {
                "is_object": False,
                "creature_type": "'spell_force'",
                "participant_ref": None,
            }
        if change.get("field") == "_world_created_metadata" and isinstance(
            change.get("value"), dict
        ):
            metadata = change["value"]
            metadata.pop("mansion_creation")
            metadata.pop("material")
            metadata.update(
                kind="'mansion_servant'",
                near_transparent=True,
                invulnerable=True,
                attire="_mf_selected.attire",
            )
            metadata["mansion_servant"] = {
                "source_id": "source_id",
                "cast_id": "_xd_source.cast_id",
                "created_tick": "_xd_source.created_tick",
                "room_id": "_xd_source.room_id",
                "index": "_mf_selected.index",
            }
        if change.get("field") == "_world_created_entity":
            change["value"]["visibility"] = "'clear'"
        if change.get("field", "").endswith(".has_mansion_furnishings"):
            change["field"] = change["field"].replace(
                ".has_mansion_furnishings", ".has_mansion_servants"
            )

    contact = _tool_scene(
        "Qualify one actual Mansion servant and object contact.",
        [_parameter("source_id"), _parameter("servant_id"), _parameter("object_id")],
        [*_qualification(), *_living_source(), *_contact()],
    )
    command = _public(
        _tool_scene(
            "Command an actual Mansion servant's touching open, close or plain-water pour task.",
            [
                _entity("caster_state"),
                _parameter("source_id"),
                _parameter("servant_id"),
                _parameter("object_id"),
                _parameter("operation", "enum", enum_values=["open", "close", "pour"]),
            ],
            [
                _require(
                    ACTIVE.replace("actor_state", "caster_state"),
                    "A capable living caster gives the command.",
                ),
                _require(
                    "combat.encounter_active != true",
                    "Servant combat task timing remains selected.",
                ),
                _require(
                    "state.pending_srd_action_stack == null or "
                    "len(state.pending_srd_action_stack) == 0",
                    "Complete the actual saved window before this servant task.",
                ),
                *_qualification(),
                *_living_source(),
                _require(
                    "_xd_source.caster == _caster_state_id",
                    "Only this actual Mansion's caster commands its staff.",
                ),
                *_scene_subject("{_caster_state_id}", "_ms_caster"),
                _require(
                    "_ms_caster.metadata.plane == _xd_source.room_id",
                    "Outside-room communication needs its actual command owner.",
                ),
                _call(
                    "check_srd_dwelling_magic",
                    {"subject": "{servant_id}", "slot_level": "{_xd_source.slot_level}"},
                ),
                _set("_ms_closed", None),
                _set("_ms_water_poured", None),
                _when(
                    "operation == 'pour'",
                    [
                        _call(
                            "pour_srd_mansion_servant_vial_node",
                            {
                                "source_id": "{source_id}",
                                "servant_id": "{servant_id}",
                                "object_id": "{object_id}",
                            },
                            ["_force_poured"],
                        ),
                        _set("_ms_water_poured", "_force_poured.water_gallons"),
                    ],
                ),
                _when(
                    "operation != 'pour'",
                    [
                        _call(
                            "operate_srd_mansion_servant_access_node",
                            {
                                "source_id": "{source_id}",
                                "servant_id": "{servant_id}",
                                "object_id": "{object_id}",
                                "operation": "{operation}",
                            },
                            ["_world_access"],
                        ),
                        _set("_ms_closed", "_world_access.closed"),
                    ],
                ),
                _set(
                    "_ms_order",
                    {
                        "source_id": "source_id",
                        "cast_id": "_xd_source.cast_id",
                        "servant_id": "servant_id",
                        "index": "_ms_marker.index",
                        "object_id": "object_id",
                        "operation": "operation",
                        "water_gallons": "_ms_water_poured",
                        "completed_tick": "state.lasting_tick",
                    },
                ),
                _set("_ms_orders", []),
                _when(
                    "_xd_source.servant_orders != null",
                    [_set("_ms_orders", "_xd_source.servant_orders")],
                ),
                _push("_ms_orders", "_ms_order"),
                _set("_ms_patch", {"servant_orders": "_ms_orders"}),
                _set("_xd_source", "merge(_xd_source, _ms_patch)"),
                _call(
                    "save_srd_mansion_servant_orders",
                    {"source_id": "{source_id}", "orders": "{_ms_orders}"},
                ),
            ],
        ),
        "exploration",
        "Mansion servant",
        [
            ("Operation", "_ms_order.operation"),
            ("Closed", "_ms_closed"),
            ("Water poured (gallons)", "_ms_water_poured"),
        ],
    )

    movement = _tool_scene(
        "Keep actual source-created Mansion staff inside their own room.",
        [_parameter("subject"), _array("destination"), _parameter("plane")],
        [
            _set("_ms_candidate_body", None),
            _set("_ms_candidates", "state.spatial_scene.entities"),
            _loop(
                "_ms_candidates",
                "_ms_candidate",
                [
                    _when(
                        "_ms_candidate.id == subject", [_set("_ms_candidate_body", "_ms_candidate")]
                    )
                ],
            ),
            _when(
                "_ms_candidate_body != null and "
                "_ms_candidate_body.metadata.mansion_servant != null",
                [
                    _set(
                        "_ms_selected_source",
                        "_ms_candidate_body.metadata.mansion_servant.source_id",
                    ),
                    *_qualification("_ms_selected_source", "subject"),
                    *_living_source(),
                    _require(
                        "plane == _xd_source.room_id and _ms_body.footprint.kind == 'box'",
                        "The actual Mansion servant cannot leave its dwelling.",
                    ),
                    *[
                        _require(
                            f"destination[{axis}] - _ms_body.footprint.half_extents[{axis}] >= "
                            f"_ms_room.shape.min[{axis}] and "
                            f"destination[{axis}] + _ms_body.footprint.half_extents[{axis}] <= "
                            f"_ms_room.shape.max[{axis}]",
                            "The full servant body must stay inside the actual room.",
                        )
                        for axis in range(2)
                    ],
                    _require(
                        "destination[2] >= _ms_room.shape.min[2] and "
                        "destination[2] + _ms_body.height <= _ms_room.shape.max[2]",
                        "The full servant body must stay inside the actual floor and ceiling.",
                    ),
                ],
            ),
        ],
    )
    retirement_patch = _metadata_patch(
        {"mansion_servant_retired_tick": "state.lasting_tick"},
        active=False,
        movement="passable",
        visibility="clear",
    )
    marker = next(
        row["set"]
        for row in retirement_patch
        if row.get("set", {}).get("field") == "_world_object_marker"
    )
    marker["value"] = {"is_object": False}
    retire_body = _tool_scene(
        "Retire one actual ended staff body through the same canonical metadata mutation.",
        [_parameter("source_id"), _parameter("object_id")],
        [
            *_lookup(),
            *_lookup_object(),
            _require(
                "_object_scene.metadata.mansion_servant.source_id == source_id and "
                "_object_scene.metadata.mansion_servant.cast_id == _xd_source.cast_id and "
                "_object_scene.metadata.mansion_servant.created_tick == _xd_source.created_tick",
                "Retire only the actual ended cast's staff body.",
            ),
            *retirement_patch,
        ],
    )
    retire = _tool_scene(
        "Retire authentic staff bodies when their source ends; never expel staff outside.",
        [_parameter("source_id")],
        [
            *_lookup(),
            _set("_ms_retired_intents", []),
            _when(
                "_xd_source.servants != null", [_set("_ms_retired_intents", "_xd_source.servants")]
            ),
            _loop(
                "_ms_retired_intents",
                "_ms_retired_intent",
                [
                    _call(
                        "retire_srd_mansion_servant_body",
                        {
                            "source_id": "{source_id}",
                            "object_id": "{_ms_retired_intent.id}",
                        },
                    ),
                ],
            ),
        ],
    )
    magic_status = [
        _require(
            "state.has_spell_magic_zones != true",
            "Staff invulnerability needs the actual active magic-zone owner.",
        ),
        _set("_mansion_servant_invulnerable", True),
    ]
    if "check_srd_magic_zone_effect" in tools:
        magic_status = [
            _call(
                "check_srd_magic_zone_effect",
                {
                    "source_subject": "{servant_id}",
                    "target_subject": "{servant_id}",
                    "effect_kind": "spell",
                    "actual_spell_level": "{_xd_source.slot_level}",
                },
                ["_magic_zone_effect_allowed"],
            ),
            _set("_mansion_servant_invulnerable", "_magic_zone_effect_allowed == true"),
        ]
    status = _tool_scene(
        "Derive actual staff invulnerability from source/body identity "
        "and magic-zone qualification.",
        [_parameter("source_id"), _parameter("servant_id")],
        [
            *_qualification(),
            _set("_mansion_servant_invulnerable", False),
            _when(
                "_xd_source.active == true and state.lasting_tick < _xd_source.expires_tick and "
                "_ms_body.active == true",
                [
                    *magic_status,
                ],
            ),
        ],
    )
    if __package__:
        from .srd_spell_travel_dwelling import _save
    else:
        from srd_spell_travel_dwelling import _save
    save = _tool_scene(
        "Retain exact completed staff task receipts in the existing source pool.",
        [_parameter("source_id"), _parameter("orders", "array", items={"type": "object"})],
        [
            *_lookup(),
            _set("_ms_patch", {"servant_orders": "orders"}),
            _set("_xd_source", "merge(_xd_source, _ms_patch)"),
            *_save(),
        ],
    )
    profile_guard = _tool_scene(
        "Keep an actual invulnerable staff body out of the Breaking Objects HP authority.",
        [_parameter("object_id")],
        [
            _set("_ms_profile_bodies", "state.spatial_scene.entities"),
            _loop(
                "_ms_profile_bodies",
                "_ms_profile_body",
                [
                    _when(
                        "_ms_profile_body.id == object_id and "
                        "_ms_profile_body.metadata.mansion_servant != null",
                        [
                            _set(
                                "_ms_selected_source",
                                "_ms_profile_body.metadata.mansion_servant.source_id",
                            ),
                            *_qualification("_ms_selected_source", "object_id"),
                            _require(
                                "false",
                                "An actual invulnerable Mansion servant "
                                "is not a destructible object.",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "create_srd_mansion_servant": create,
        "qualify_srd_mansion_servant_contact": contact,
        "operate_srd_mansion_servant_access_node": _access_node(tools),
        "pour_srd_mansion_servant_vial_node": _pour_node(tools),
        "command_srd_mansion_servant": command,
        "check_srd_mansion_servant_movement": movement,
        "retire_srd_mansion_servants": retire,
        "retire_srd_mansion_servant_body": retire_body,
        "qualify_srd_mansion_servant_invulnerability": status,
        "save_srd_mansion_servant_orders": save,
        "guard_srd_mansion_servant_object_profile": profile_guard,
    }


def bind_servant_consumers(tools, changed):
    """Guard the sole physical relocation and retire staff after exact queue-only source end."""
    name = (
        "move_srd_travel_subject_node"
        if "move_srd_travel_subject_node" in tools
        else "move_srd_travel_subject"
    )
    original = changed.get(name, tools.get(name))
    if original is not None and "check_srd_mansion_servant_movement" not in str(
        original["effects"]
    ):
        move = deepcopy(original)
        move["effects"].insert(
            0,
            _when(
                "state.has_mansion_servants == true",
                [
                    _call(
                        "check_srd_mansion_servant_movement",
                        {
                            "subject": "{subject}",
                            "destination": "{destination}",
                            "plane": "{plane}",
                        },
                    )
                ],
            ),
        )
        changed[name] = move
    original = changed.get(
        "end_srd_extradimensional_source", tools.get("end_srd_extradimensional_source")
    )
    if original is not None and "retire_srd_mansion_servants" not in str(original["effects"]):
        end = deepcopy(original)
        boundary = next(
            row for row in end["effects"] if row.get("condition") == "_xd_source.active == true"
        )
        boundary["on_true"].append(
            _when(
                "_xd_source.spell == 'magnificent_mansion' and _xd_source.servants != null",
                [_call("retire_srd_mansion_servants", {"source_id": "{source_id}"})],
            )
        )
        changed["end_srd_extradimensional_source"] = end
    original = changed.get(
        "prepare_srd_spell_object_node", tools.get("prepare_srd_spell_object_node")
    )
    if original is not None and "guard_srd_mansion_servant_object_profile" not in str(
        original["effects"]
    ):
        prepare = deepcopy(original)
        prepare["effects"].insert(
            0,
            _when(
                "state.has_mansion_servants == true",
                [
                    _call("guard_srd_mansion_servant_object_profile", {"object_id": "{object_id}"}),
                ],
            ),
        )
        changed["prepare_srd_spell_object_node"] = prepare


def emit_late_servant_access(text):
    """Extract only the final access body after actual Door/lock suppression binding."""
    if __package__:
        from .srd_timed_actor_defenses import _load_runtime
        from .srd_timed_senses import _replace_tools
    else:
        from srd_timed_actor_defenses import _load_runtime
        from srd_timed_senses import _replace_tools
    tools = _load_runtime(text)["tools"]
    if "operate_srd_mansion_servant_access_node" not in tools:
        return text
    changed = {"operate_srd_mansion_servant_access_node": _access_node(tools)}
    if "pour_srd_mansion_servant_vial_node" in tools:
        changed["pour_srd_mansion_servant_vial_node"] = _pour_node(tools, capture=True)
    if all(tools[name] == value for name, value in changed.items()):
        return text
    return _replace_tools(text, changed)
