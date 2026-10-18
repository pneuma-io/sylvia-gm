"""Levitate uses actual weighted objects, creature saves and the shared vertical geometry owners."""

from copy import deepcopy

if __package__:
    from .srd_feather_fall import _ground, _sweep
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
    from .srd_spell_object_content import _admission_guard, _metadata_patch
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _optional, _public, _push
else:
    from srd_feather_fall import _ground, _sweep
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
    from srd_spell_object_content import _admission_guard, _metadata_patch
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _optional, _public, _push


PUBLIC_TOOLS = {"record_srd_levitate_object_looseness"}


def _body(subject):
    """Bind shared ground and sweep helpers to the actual selected scene subject."""
    return [
        _call("srd_information_scene_entity", {"subject": "{" + subject + "}"}, POSITION_EXPORTS),
        _set("_target_state_id", "_info_scene_entity.id"),
    ]


def _return_geometry():
    """The existing ground owner supplies a real support hit and exact swept descent."""
    return [*_ground(), *_sweep("_fall_ground_position")]


def _source():
    """Select the saved source by its exact owning identity."""
    return [
        _set("_levitate_source", None),
        _when("state.levitate_sources != null", [
            _set("_levitate_records", "state.levitate_sources"),
            _loop("_levitate_records", "_levitate_record", [
                _when("_levitate_record.id == source_id", [
                    _require("_levitate_source == null", "Levitate source identity is ambiguous."),
                    _set("_levitate_source", "_levitate_record"),
                ]),
            ]),
        ]),
        _require("_levitate_source != null", "Select an actual saved Levitate source."),
    ]


def _save_source():
    """Replace one owned source without changing its sibling records."""
    return [
        _set("_levitate_retained", []),
        _set("_levitate_records", "state.levitate_sources"),
        _loop("_levitate_records", "_levitate_record", [
            {
                "condition": "_levitate_record.id == _levitate_source.id",
                "on_true": [_push("_levitate_retained", "_levitate_source")],
                "on_false": [_push("_levitate_retained", "_levitate_record")],
            },
        ]),
        _set("state.levitate_sources", "_levitate_retained"),
    ]


def _patch_body(changes):
    """Save only the owned suspension marker in the canonical admitted scene."""
    return [
        *_snapshot(),
        _set("_levitate_body_patch", changes),
        _set("_levitate_body_metadata", "merge(_info_scene_entity.metadata, _levitate_body_patch)"),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "entity", "id": "{_info_scene_entity.id}"},
                "patch": {"metadata": "{_levitate_body_metadata}"},
            },
            "store_as": "_levitate_marker_mutation",
        },
    ]


def levitate_tools(tools):
    """Preflight actual initial geometry and roll the printed creature save."""
    if __package__:
        from .srd_spell_travel import _placement, _scene_subject
    else:
        from srd_spell_travel import _placement, _scene_subject

    loose = _public(
        _tool_scene(
            "Record whether the admitted object is physically loose.",
            [_parameter("object_id"), _parameter("loose", "boolean")],
            [
                *_admission_guard(),
                _require(
                    "_object_scene.metadata.is_object == true",
                    "Select an actual admitted object rather than a creature or spell force.",
                ),
                _require(
                    "_object_scene.metadata.srd_levitate_loose == null",
                    "This object's existing physical qualification needs its actual change owner.",
                ),
                *_metadata_patch({"srd_levitate_loose": "loose"}),
                _set("_levitate_loose", "loose"),
            ],
        ),
        "exploration",
        "Record loose object",
        [("Loose", "_levitate_loose")],
    )
    creature = _tool_scene(
        "Qualify the actual visible same-plane creature through its existing saved sight owner.",
        [_entity("caster_state"), _entity("target_state")],
        [
            _call(
                "resolve_srd_scene_relation_node",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ["_scene_same_plane", "_scene_can_see", "_scene_distance"],
            ),
            _require(
                "_scene_same_plane == true and _scene_can_see == true and _scene_distance <= 60",
                "Levitate needs an actual creature the caster can see within sixty feet.",
            ),
        ],
    )
    validate = _tool_scene(
        "Qualify a visible creature or loose weighted object and its actual vertical rise.",
        [
            _entity("caster_state"),
            _parameter("subject"),
            _optional("rise", "number", 20, minimum=0, maximum=20),
            _optional("targets", "array", None, items={"type": "string"}),
            _optional("all_targets_willing", "boolean", True),
        ],
        [
            _require(
                "targets == null or len(targets) == 0",
                "Select one actual Levitate scene subject.",
            ),
            *_scene_subject("{_caster_state_id}", "_levitate_caster"),
            *_body("subject"),
            _set("_levitate_body", "_info_scene_entity"),
            _set("_levitate_origin", "_info_position"),
            _require(
                "_levitate_body.metadata.plane != null and "
                "_levitate_body.metadata.plane == _levitate_caster.metadata.plane",
                "Levitate needs the actual target on the caster's admitted plane.",
            ),
            _require(
                "_levitate_body.metadata.levitate_source_id == null",
                "End the actual existing Levitate source first.",
            ),
            _set("_levitate_creature_id", None),
            {
                "condition": "_levitate_body.metadata.is_object == true",
                "on_true": [
                    _call(
                        "resolve_srd_object_utility_facts",
                        {
                            "caster_state": "{_caster_state_id}",
                            "object_id": "{_levitate_body.id}",
                            "range_feet": 60,
                            "requires_sight": True,
                        },
                        ["_object_scene", "_utility_facts"],
                    ),
                    _require(
                        "_utility_facts.weight_pounds <= 500 and "
                        "_object_scene.metadata.srd_levitate_loose == true and "
                        "_object_scene.metadata.carried_by == null and "
                        "_object_scene.metadata.worn_by == null",
                        "Use a loose, unheld object of at most five hundred pounds.",
                    ),
                ],
                "on_false": [
                    _require(
                        "_levitate_body.metadata.creature_type != 'spell_force'",
                        "Levitate affects an actual creature or loose object.",
                    ),
                    _set("_levitate_creature_id", "_levitate_body.id"),
                    _when(
                        "_levitate_body.metadata.participant_ref != null",
                        [_set("_levitate_creature_id", "_levitate_body.metadata.participant_ref")],
                    ),
                    _call(
                        "qualify_srd_levitate_creature",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_levitate_creature_id}",
                        },
                    ),
                    _when("all_targets_willing != true", [
                        _call("qualify_srd_levitate_save", {
                            "target_state": "{_levitate_creature_id}",
                        }),
                    ]),
                ],
            },
            # The borrowed sweep consumes the actual selected body, never caster extents.
            *_body("_levitate_body.id"),
            _set(
                "_levitate_destination",
                ["_info_position[0]", "_info_position[1]", "_info_position[2] + rise"],
            ),
            *_sweep("_levitate_destination"),
            _set("_travel_excluded", []),
            *_placement(
                "{_levitate_body.id}",
                "{_levitate_destination}",
                "{_levitate_body.metadata.plane}",
            ),
            _require(
                "_travel_placement.distance == 0",
                "The actual chosen vertical rise needs an unoccupied destination.",
            ),
            *_ground(),
        ],
    )
    qualify_save = _tool_scene(
        "Require an unwilling creature's actual saved Constitution modifier before payment.",
        [_entity("target_state")],
        [_require("target_state.saving_throw_modifiers.constitution != null",
                  "Record the actual creature's Constitution saving-throw modifier.")],
    )
    save = _tool_scene(
        "Roll the actual unwilling creature's Constitution save without condition immunity gating.",
        [_entity("target_state"), _parameter("save_dc", "integer", minimum=1, maximum=40)],
        [
            _require(
                "target_state.saving_throw_modifiers.constitution != null",
                "Record the actual creature's Constitution saving-throw modifier.",
            ),
            _call(
                "d20_test",
                {
                    "actor_state": "{_target_state_id}",
                    "ability": "constitution",
                    "kind": "saving_throw",
                    "ability_modifier": "{target_state.saving_throw_modifiers.constitution}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "circumstantial_modifier": 0,
                    "target_number": "{save_dc}",
                    "advantage": False,
                    "disadvantage": False,
                    "forego_save": False,
                },
                ["_d20_test", "_outcome"],
            ),
            _set("_levitate_save", "_d20_test"),
            _set("_levitate_save_outcome", "_outcome"),
            _set("target_state.last_levitate_save", "_d20_test"),
            _set("target_state.last_levitate_save_outcome", "_outcome"),
        ],
    )
    installed = _tool_scene(
        "Start the paid Levitate source and rise only after the actual printed save fails.",
        [
            _entity("caster_state"), _parameter("subject"),
            _parameter("slot_level", "integer", minimum=2, maximum=9),
            _parameter("save_dc", "integer", minimum=1, maximum=40),
            _parameter("concentration_modifier", "integer"),
            _parameter("all_targets_willing", "boolean"),
            _optional("rise", "number", 20, minimum=0, maximum=20),
            _optional("spell_cast_id", default="", model_hidden=True),
            _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
            _optional("actual_spell_level", "integer", -1, minimum=-1, maximum=9,
                      model_hidden=True),
        ],
        [
            _call("validate_srd_levitate_cast", {
                "caster_state": "{_caster_state_id}", "subject": "{subject}",
                "rise": "{rise}", "all_targets_willing": "{all_targets_willing}",
            }, ["_levitate_body", "_levitate_origin", "_levitate_destination",
                "_levitate_creature_id"]),
            _set("_levitate_save_outcome", "'failure'"),
            _when("_levitate_creature_id != null and all_targets_willing != true", [
                _call("roll_srd_levitate_initial_save", {
                    "target_state": "{_levitate_creature_id}", "save_dc": "{save_dc}",
                }, ["_levitate_save", "_levitate_save_outcome"]),
            ]),
            _set("_levitate_cast_owner", None),
            _when("spell_cast_owner != null", [
                _set("_levitate_cast_owner", "_spell_cast_owner_id"),
            ]),
            _set("_levitate_focus", ["_caster_state_id"]),
            _call("start_srd_condition_concentration", {
                "caster_state": "{_caster_state_id}", "lasting_state": "state",
                "spell": "levitate", "targets": "{_levitate_focus}", "duration_rounds": 100,
                "save_modifier": "{concentration_modifier}", "spell_cast_id": "{spell_cast_id}",
                "spell_cast_owner": "{_levitate_cast_owner}",
                "actual_spell_level": "{actual_spell_level}",
            }),
            _set("caster_state.concentration_effect_family", "'travel_levitate'"),
            _set("_levitate_installed", False),
            _when("_levitate_save_outcome == 'failure'", [
                _when("state.levitate_sources == null", [_set("state.levitate_sources", [])]),
                _require("len(state.levitate_sources) < 128",
                         "Retire existing Levitate source history before adding more sources."),
                _call("bind_srd_levitate_source_identity", {
                    "source_identity": "levitate:{_caster_state_id}:"
                                       "{caster_state.concentration_sequence}:{_levitate_body.id}",
                }, ["_levitate_source_id"]),
                _set("_levitate_source", {
                    "id": "_levitate_source_id", "spell": "'levitate'",
                    "caster": "_caster_state_id", "subject": "_levitate_body.id",
                    "creature_id": "_levitate_creature_id",
                    "plane": "_levitate_body.metadata.plane",
                    "sequence": "caster_state.concentration_sequence", "slot_level": "slot_level",
                    "created_tick": "state.lasting_tick",
                    "expires_tick": "caster_state.concentration_expires_tick", "active": True,
                    "return_pending": False, "settled": False,
                    "cast_id": "spell_cast_id", "source_caster_id": "_levitate_cast_owner",
                    "ongoing_duration": "spell_cast_id != ''",
                    "movement_destination": "_levitate_destination",
                }),
                _set("_levitate_records", "state.levitate_sources"),
                _push("_levitate_records", "_levitate_source"),
                _set("state.levitate_sources", "_levitate_records"),
                _set("state.has_levitate_sources", True),
                _set("caster_state.levitate_source_id", "_levitate_source_id"),
                *_body("_levitate_body.id"),
                *_patch_body({"levitate_source_id": "_levitate_source_id"}),
                _call("move_srd_travel_subject", {
                    "subject": "{_levitate_body.id}", "destination": "{_levitate_destination}",
                    "plane": "{_levitate_body.metadata.plane}", "teleport": False,
                    "levitate_source_id": "{_levitate_source_id}",
                }),
                _set("_levitate_source_patch", {"movement_destination": None}),
                _set("_levitate_source", "merge(_levitate_source, _levitate_source_patch)"),
                *_save_source(),
                _set("_levitate_installed", True),
            ]),
            _call("settle_srd_levitate_returns", {}),
        ],
    )
    mark = _tool_scene(
        "Queue this exact ended concentration's gentle return without relocating in retirement.",
        [_parameter("source_id"), _entity("caster_state"),
         _parameter("sequence", "integer")],
        [*_source(), _when("_levitate_source.caster == _caster_state_id and "
                           "_levitate_source.sequence == sequence and "
                           "_levitate_source.settled != true", [
            _set("_levitate_source_patch", {"active": False, "return_pending": True,
                                            "movement_destination": None}),
            _set("_levitate_source", "merge(_levitate_source, _levitate_source_patch)"),
            *_save_source(),
            _set("state.has_levitate_pending_returns", True),
        ])],
    )
    settle_one = _tool_scene(
        "Float the actual suspended body to real clear ground after source retirement completes.",
        [_parameter("source_id")],
        [*_source(), _when("_levitate_source.return_pending == true", [
            *_body("_levitate_source.subject"),
            _require("_info_scene_entity.metadata.plane == _levitate_source.plane and "
                     "_info_scene_entity.metadata.levitate_source_id == _levitate_source.id",
                     "Reconcile the actual suspended body before gentle settlement."),
            *_return_geometry(),
            _set("_travel_excluded", []),
            *_placement("{_levitate_source.subject}", "{_fall_ground_position}",
                        "{_levitate_source.plane}"),
            _require("_travel_placement.distance == 0",
                     "The actual gentle landing needs unoccupied supporting ground."),
            _set("_levitate_source_patch", {"movement_destination": "_fall_ground_position"}),
            _set("_levitate_source", "merge(_levitate_source, _levitate_source_patch)"),
            *_save_source(),
            _call("move_srd_travel_subject", {
                "subject": "{_levitate_source.subject}", "destination": "{_fall_ground_position}",
                "plane": "{_levitate_source.plane}", "teleport": False,
                "levitate_source_id": "{_levitate_source.id}",
            }),
            *_body("_levitate_source.subject"),
            *_patch_body({"levitate_source_id": None}),
            _set("_levitate_source_patch", {"return_pending": False, "settled": True,
                                            "movement_destination": None}),
            _set("_levitate_source", "merge(_levitate_source, _levitate_source_patch)"),
            *_save_source(),
        ])],
    )
    settle = _tool_scene(
        "Settle queued actual Levitate returns once outside the retirement call graph.", [],
        [
            _set("state.has_levitate_pending_returns", False),
            _when("state.levitate_sources != null", [
                _set("_levitate_settlement_sources", "state.levitate_sources"),
                _loop("_levitate_settlement_sources", "_levitate_settlement_source", [
                    _when("_levitate_settlement_source.return_pending == true", [
                        _call("settle_srd_levitate_source", {
                            "source_id": "{_levitate_settlement_source.id}",
                        }),
                    ]),
                ]),
            ]),
        ],
    )
    refresh_one = _tool_scene(
        "Refresh the actual Levitate caster's existing concentration and queue its expired source.",
        [_parameter("source_id"), _entity("caster_state")],
        [*_source(), _when("_levitate_source.active == true", [
            _call("refresh_srd_concentration_source", {
                "source_state": "{_caster_state_id}", "target_state": "{_caster_state_id}",
                "lasting_state": "state",
            }),
            _call("check_srd_condition_concentration", {
                "caster_state": "{_caster_state_id}", "sequence": "{_levitate_source.sequence}",
            }, ["_condition_concentration_active"]),
            _when("_condition_concentration_active != true or "
                  "caster_state.concentration_spell_id != 'levitate' or "
                  "state.lasting_tick >= _levitate_source.expires_tick", [
                _call("mark_srd_levitate_return", {
                    "source_id": "{_levitate_source.id}", "caster_state": "{_caster_state_id}",
                    "sequence": "{_levitate_source.sequence}",
                }),
            ]),
        ])],
    )
    refresh = _tool_scene(
        "Refresh real Levitate concentrations and finish their source-defined gentle returns.", [],
        [
            _set("state.has_levitate_sources", False),
            _when("state.levitate_sources != null", [
                _set("_levitate_clock_sources", "state.levitate_sources"),
                _loop("_levitate_clock_sources", "_levitate_clock_source", [
                    _when("_levitate_clock_source.active == true", [
                        _call("refresh_srd_levitate_source", {
                            "source_id": "{_levitate_clock_source.id}",
                            "caster_state": "{_levitate_clock_source.caster}",
                        }),
                    ]),
                ]),
            ]),
            _call("settle_srd_levitate_returns", {}),
            _when("state.levitate_sources != null", [
                _set("_levitate_clock_sources", "state.levitate_sources"),
                _loop("_levitate_clock_sources", "_levitate_clock_source", [
                    _when("_levitate_clock_source.active == true", [
                        _set("state.has_levitate_sources", True),
                    ]),
                ]),
            ]),
        ],
    )
    movement = _tool_scene(
        "Allow only this actual Levitate source's exact initial or gentle-return movement intent.",
        [_parameter("subject"), _array("destination"), _parameter("plane"),
         _optional("levitate_source_id", default="", model_hidden=True),
         _optional("teleport", "boolean", False)],
        [*_body("subject"), _when("_info_scene_entity.metadata.levitate_source_id != null and "
                                  "teleport != true", [
            _set("_levitate_body_marker", "_info_scene_entity.metadata.levitate_source_id"),
            _require("levitate_source_id == _levitate_body_marker",
                     "A suspended Levitate body needs its actual source-owned movement or grip."),
            _call("qualify_srd_levitate_source_movement", {
                "source_id": "{_levitate_body_marker}", "subject": "{_info_scene_entity.id}",
                "destination": "{destination}", "plane": "{plane}",
            }),
        ])],
    )
    source_movement = _tool_scene(
        "Validate the exact source and saved destination before the sole scene mutation.",
        [_parameter("source_id"), _parameter("subject"), _array("destination"),
         _parameter("plane")],
        [*_source(), *_body("subject"),
         _set("_levitate_allowed_destination", ["_levitate_source.movement_destination"]),
         _require("_levitate_source.subject == _info_scene_entity.id and "
                  "_levitate_source.plane == plane and _levitate_source.settled != true and "
                  "destination in _levitate_allowed_destination and "
                  "(_levitate_source.active == true or _levitate_source.return_pending == true)",
                  "Levitate movement must consume the exact saved source-owned destination.")],
    )
    return {
        "record_srd_levitate_object_looseness": loose,
        "qualify_srd_levitate_creature": creature,
        "validate_srd_levitate_cast": validate,
        "roll_srd_levitate_initial_save": save,
        "qualify_srd_levitate_save": qualify_save,
        "install_srd_levitate": installed,
        "mark_srd_levitate_return": mark,
        "settle_srd_levitate_source": settle_one,
        "settle_srd_levitate_returns": settle,
        "refresh_srd_levitate_source": refresh_one,
        "refresh_srd_levitate_sources": refresh,
        "check_srd_levitate_movement": movement,
        "qualify_srd_levitate_source_movement": source_movement,
        "bind_srd_levitate_source_identity": _tool_scene(
            "Bind the actual concentration and admitted body identity to its owned source.",
            [_parameter("source_identity")],
            [_set("_levitate_source_id", "source_identity")],
        ),
    }


def bind_levitate_consumers(tools, changed):
    """Queue retirement and guard the final existing relocation and ordinary movement owners."""
    ending = deepcopy(
        changed.get("end_srd_concentration_node", tools["end_srd_concentration_node"])
    )
    marker = "source_state.concentration_effect_family == 'travel_levitate'"
    ending["effects"] = [e for e in ending["effects"] if e.get("condition") != marker]
    ending["effects"].insert(0, _when(marker, [
        _when("source_state.levitate_source_id != null", [
            _call("mark_srd_levitate_return", {
                "source_id": "{source_state.levitate_source_id}",
                "caster_state": "{_source_state_id}",
                "sequence": "{source_state.concentration_sequence}",
            }),
            _set("source_state.levitate_source_id", None),
        ]),
    ]))
    changed["end_srd_concentration_node"] = ending
    clock = deepcopy(changed.get("advance_srd_lasting_clock", tools["advance_srd_lasting_clock"]))
    clock["effects"] = [e for e in clock["effects"]
                        if "refresh_srd_levitate_sources" not in str(e)]
    clock["effects"].append(_when("state.has_levitate_sources == true or "
                                  "state.has_levitate_pending_returns == true", [
        _call("refresh_srd_levitate_sources", {}),
    ]))
    changed["advance_srd_lasting_clock"] = clock
    for name in ("end_srd_concentration_voluntarily", "end_concentration"):
        if name in tools or name in changed:
            tool = deepcopy(changed.get(name, tools.get(name)))
            tool["effects"] = [e for e in tool["effects"]
                               if e.get("call") != "settle_srd_levitate_returns"]
            tool["effects"].append(_call("settle_srd_levitate_returns", {}))
            changed[name] = tool
    for name in ("move_srd_travel_subject", "move_srd_scene_creature_environment_segment"):
        if name not in tools and name not in changed:
            continue
        tool = deepcopy(changed.get(name, tools.get(name)))
        tool["effects"] = [e for e in tool["effects"]
                           if "check_srd_levitate_movement" not in str(e)]
        params = {"subject": "{_actor_state_id}", "destination": "{destination}",
                  "plane": "{_travel_movement_plane}", "teleport": False}
        if name == "move_srd_travel_subject":
            if not any(p["name"] == "levitate_source_id" for p in tool["parameters"]):
                tool["parameters"].append(_optional("levitate_source_id", default="",
                                                     model_hidden=True))
            params = {"subject": "{_travel_mover.id}", "destination": "{destination}",
                      "plane": "{plane}", "levitate_source_id": "{levitate_source_id}",
                      "teleport": "{teleport}"}
            boundary = next(i for i, e in enumerate(tool["effects"])
                            if e.get("set", {}).get("field") == "_travel_patch")
        else:
            boundary = next(i for i, e in enumerate(tool["effects"])
                            if "spatial_mutation" in e)
        tool["effects"].insert(boundary, _when("state.has_levitate_sources == true or "
                                               "state.has_levitate_pending_returns == true", [
            _call("check_srd_levitate_movement", params),
        ]))
        changed[name] = tool
