"""Mage Hand uses actual scenery, saved object mass and the existing relocation owner."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial, _tool_scene
    from .srd_spell_minor_world import _body_source
    from .srd_spell_object_content import _lookup_object, _metadata_patch, _new_scene_object, _now
    from .srd_spell_object_utilities import (
        SOURCE_PAGES,
        _fact_guard,
        _read_source,
        _serial,
    )
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
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial, _tool_scene
    from srd_spell_minor_world import _body_source
    from srd_spell_object_content import _lookup_object, _metadata_patch, _new_scene_object, _now
    from srd_spell_object_utilities import SOURCE_PAGES, _fact_guard, _read_source, _serial
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


PUBLIC_TOOLS = {"control_srd_mage_hand"}
OPERATIONS = ["move", "hold", "drop", "open", "close", "pour"]
CAST_CHOICES = [
    _optional("utility_operation", "enum", "move", enum_values=OPERATIONS),
]


def _hand_source():
    """Qualify the stored cantrip before any later force action or object mutation."""
    return [
        *_read_source("mage_hand"),
        _require(
            f"_utility_record.source_sha256 == {SOURCE_PAGES[145]!r} and "
            "_utility_record.caster == _caster_state_id and "
            "caster_state.mage_hand_source_id == source_id and "
            "_object_now < _utility_record.expires_tick",
            "Use this caster's actual current Mage Hand source.",
        ),
        _call(
            "srd_information_scene_entity",
            {"subject": "{_utility_record.force_id}"},
            POSITION_EXPORTS,
        ),
        _set("_force_hand_scene", "_info_scene_entity"),
        _require(
            "_force_hand_scene.metadata.srd_object_force.source_id == source_id and "
            "_force_hand_scene.metadata.srd_object_force.spell == 'mage_hand' and "
            "_force_hand_scene.metadata.participant_ref == null",
            "The source needs its actual scene-only spectral hand.",
        ),
    ]


def _save_hand_source():
    return [
        _set("_force_kept_sources", []),
        _loop(
            "_utility_records",
            "_force_candidate",
            [
                _when(
                    "_force_candidate.kind != 'object_utility_spell' or "
                    "_force_candidate.source_id != _utility_record.source_id",
                    [_push("_force_kept_sources", "_force_candidate")],
                )
            ],
        ),
        _push("_force_kept_sources", "_utility_record"),
        _set("state.turn_effects", "_force_kept_sources"),
    ]


def _sweep(subject, destination):
    """Prove actual full box clearance before the sole relocation leaf changes a position."""
    return [
        *_snapshot(),
        _set("_force_material", "_force_hand_scene.metadata.plane == 'material'"),
        _spatial(
            "sweep",
            {
                "map_id": "{state.spatial_scene.map.id}",
                "moving_entity_id": "{" + subject + "}",
                "destination": "{" + destination + "}",
                "include_entities": False,
                "metadata_filter": {
                    "key": "plane",
                    "value": "{_force_hand_scene.metadata.plane}",
                    "missing_matches": "{_force_material}",
                },
            },
            "_force_sweep",
        ),
        _require(
            "_force_sweep.supported == true and _force_sweep.clear == true",
            "The hand and its actual cargo need a supported clear full-body path.",
        ),
    ]


def _move(subject, destination):
    return _call(
        "move_srd_travel_subject",
        {
            "subject": "{" + subject + "}",
            "destination": "{" + destination + "}",
            "plane": "{_force_hand_scene.metadata.plane}",
            "teleport": False,
        },
    )


def _hand_contact():
    """An admitted hand depiction must actually touch the chosen object's current geometry."""
    return [
        *_lookup_object(),
        _require(
            "_object_scene.metadata.plane == _force_hand_scene.metadata.plane and "
            "object_id != _utility_record.force_id",
            "Choose a separate actual object on the hand's plane.",
        ),
        _set(
            "_force_contact_min",
            [
                "_force_hand_scene.position[0] - _force_hand_scene.footprint.half_extents[0]",
                "_force_hand_scene.position[1] - _force_hand_scene.footprint.half_extents[1]",
                "_force_hand_scene.position[2]",
            ],
        ),
        _set(
            "_force_contact_max",
            [
                "_force_hand_scene.position[0] + _force_hand_scene.footprint.half_extents[0]",
                "_force_hand_scene.position[1] + _force_hand_scene.footprint.half_extents[1]",
                "_force_hand_scene.position[2] + _force_hand_scene.height",
            ],
        ),
        *_snapshot(),
        _spatial(
            "area",
            {
                "map_id": "{state.spatial_scene.map.id}",
                "shape": {
                    "kind": "box",
                    "min": "{_force_contact_min}",
                    "max": "{_force_contact_max}",
                },
                "include": ["entities"],
                "max_results": 1000,
            },
            "_force_contacts",
        ),
        _require("_force_contacts.truncated != true", "Actual hand contacts exceed the bound."),
        _set("_force_contact", False),
        _set("_force_contact_rows", "_force_contacts.results"),
        _loop(
            "_force_contact_rows",
            "_force_contact_row",
            [
                _when("_force_contact_row.id == object_id", [_set("_force_contact", True)]),
            ],
        ),
        _require("_force_contact == true", "The actual spectral hand does not touch this object."),
    ]


def _release_tool():
    return _tool_scene(
        "Release only the exact held scenery object; preserve its actual position and identity.",
        [_parameter("object_id"), _parameter("source_id")],
        [
            *_lookup_object(),
            _require(
                "_object_scene.metadata.srd_spell_carrier.source_id == source_id",
                "Release only this exact source's real cargo.",
            ),
            *_metadata_patch({"srd_spell_carrier": None}),
        ],
    )


def _operation_tool():
    hold = [
        *_hand_contact(),
        *_fact_guard(),
        _require(
            "_utility_facts.weight_pounds <= 10 and "
            "_object_scene.metadata.carried_by == null and "
            "_object_scene.metadata.worn_by == null and "
            "_object_scene.metadata.srd_spell_carrier == null and "
            "_object_scene.metadata.srd_object_force == null and "
            "_utility_record.cargo_id == ''",
            "The hand can hold one actual unheld object weighing at most ten pounds.",
        ),
        *_metadata_patch(
            {
                "srd_spell_carrier": {
                    "source_id": "source_id",
                    "force_id": "_utility_record.force_id",
                    "spell": "'mage_hand'",
                    "source_sha256": repr(SOURCE_PAGES[145]),
                }
            }
        ),
        _set("_force_source_change", {"cargo_id": "object_id"}),
        _set("_utility_record", "merge(_utility_record, _force_source_change)"),
        *_save_hand_source(),
    ]
    access = [
        *_hand_contact(),
        _require(
            "_object_scene.metadata.srd_object_access != null",
            "The actual object needs admitted access facts.",
        ),
        _set("_force_access", "_object_scene.metadata.srd_object_access"),
        _require(
            "len(_force_access.locks) == 0 and _force_access.stuck != true and "
            "_force_access.barred != true",
            "An actual mundane lock or restriction blocks the hand.",
        ),
        _set("_force_locks", []),
        _when(
            "_object_scene.metadata.srd_arcane_locks != null",
            [
                _set("_force_locks", "_object_scene.metadata.srd_arcane_locks"),
            ],
        ),
        _loop(
            "_force_locks",
            "_force_lock",
            [
                _require(
                    "_force_lock.suppressed_until_tick != null and "
                    "_force_lock.suppressed_until_tick > _object_now",
                    "An actual Arcane Lock blocks the spectral hand.",
                ),
            ],
        ),
        _set("_force_access_change", {"closed": "operation == 'close'"}),
        _set("_force_access", "merge(_force_access, _force_access_change)"),
        _set("_force_access_metadata", {"srd_object_access": "_force_access"}),
        _when(
            "_object_scene.metadata.srd_object_container != null",
            [
                _set("_force_container_change", {"is_open": "operation == 'open'"}),
                _set(
                    "_force_container",
                    "merge(_object_scene.metadata.srd_object_container, _force_container_change)",
                ),
                _set("_force_container_metadata", {"srd_object_container": "_force_container"}),
                _set(
                    "_force_access_metadata",
                    "merge(_force_access_metadata, _force_container_metadata)",
                ),
            ],
        ),
        _set("_force_movement", "'blocking'"),
        _set("_force_visibility", "'opaque'"),
        _when(
            "operation == 'open'",
            [
                _set("_force_movement", "'passable'"),
                _set("_force_visibility", "'clear'"),
            ],
        ),
        *_metadata_patch(
            "_force_access_metadata", movement="{_force_movement}", visibility="{_force_visibility}"
        ),
    ]
    pour = [
        *_hand_contact(),
        *_fact_guard(),
        _require(
            "_utility_facts.object_kind == 'vial' and "
            "_object_scene.metadata.srd_object_container != null and "
            "_object_scene.metadata.srd_object_container.is_open == true",
            "Pour only the actual contents of an admitted open vial.",
        ),
        _set("_force_vial", "_object_scene.metadata.srd_object_container"),
        _set(
            "_force_poured",
            {
                "water_gallons": "_force_vial.water_gallons",
                "tick": "_object_now",
                "position": "_object_scene.position",
                "plane": "_object_scene.metadata.plane",
                "source_id": "source_id",
            },
        ),
        _set("_force_vial_change", {"water_gallons": 0}),
        _set("_force_vial", "merge(_force_vial, _force_vial_change)"),
        *_metadata_patch(
            {"srd_object_container": "_force_vial", "srd_poured_contents": "_force_poured"}
        ),
    ]
    return _tool_scene(
        "Manipulate an actual touching object without attack or magic-item activation verdicts.",
        [
            _entity("caster_state"),
            _parameter("source_id"),
            _optional("object_id", default=""),
            _parameter("operation", "enum", enum_values=OPERATIONS),
        ],
        [
            *_hand_source(),
            _when("operation == 'hold'", hold),
            _when(
                "operation == 'drop'",
                [
                    _require(
                        "object_id == _utility_record.cargo_id", "Select the hand's actual cargo."
                    ),
                    _call(
                        "release_srd_mage_hand_object",
                        {"object_id": "{object_id}", "source_id": "{source_id}"},
                    ),
                    _set("_force_source_change", {"cargo_id": "''"}),
                    _set("_utility_record", "merge(_utility_record, _force_source_change)"),
                    *_save_hand_source(),
                ],
            ),
            _when("operation == 'open' or operation == 'close'", access),
            _when("operation == 'pour'", pour),
        ],
    )


def _cast_tool():
    return _tool_scene(
        "Create the actual source-bound spectral hand after ordinary casting payment.",
        [
            _entity("caster_state"),
            _parameter("spell", "enum", enum_values=["mage_hand"]),
            _parameter("slot_level", "integer", minimum=0, maximum=9),
            _parameter("output_id"),
            _parameter("form_id"),
            _array("point"),
            _parameter("operation", "enum", enum_values=OPERATIONS),
            _optional("object_id", default=""),
        ],
        [
            *_body_source(),
            *_now(),
            _set("_force_form", None),
            _set("_force_forms", []),
            _when(
                "caster_state.srd_hand_sized_forms != null",
                [
                    _set("_force_forms", "caster_state.srd_hand_sized_forms"),
                ],
            ),
            _loop(
                "_force_forms",
                "_force_candidate",
                [
                    _when(
                        "_force_candidate.id == form_id", [_set("_force_form", "_force_candidate")]
                    ),
                ],
            ),
            _require(
                "_force_form != null and _force_form.caster == _caster_state_id and "
                "_force_form.body_source == _minor_body_source and _force_form.kind == 'image'",
                "Admit this actual caster's fictional spectral-hand depiction first.",
            ),
            _when(
                "caster_state.mage_hand_source_id != null",
                [
                    _call(
                        "retire_srd_mage_hand",
                        {
                            "caster_state": "{_caster_state_id}",
                            "source_id": "{caster_state.mage_hand_source_id}",
                        },
                    ),
                ],
            ),
            _call(
                "resolve_srd_object_content_point",
                {
                    "caster_state": "{_caster_state_id}",
                    "point": "{point}",
                    "range_feet": 30,
                },
                ["_world_origin", "_world_plane", "_world_lighting"],
            ),
            *_serial(),
            _set("_force_extent", "_force_form.extent"),
            *_new_scene_object(
                {
                    "srd_object_force": {
                        "spell": "'mage_hand'",
                        "source_id": "_utility_source_id",
                        "caster": "_caster_state_id",
                        "source_sha256": repr(SOURCE_PAGES[145]),
                    },
                    "fiction": "_force_form.fiction",
                    "plane": "_world_plane",
                    "lighting": "_world_lighting",
                },
                identity="output_id",
                extent="_force_extent",
            ),
            _set("_force_turn", 0),
            _when(
                "caster_state.turns_started != null",
                [_set("_force_turn", "caster_state.turns_started")],
            ),
            _set(
                "_utility_record",
                {
                    "kind": "'object_utility_spell'",
                    "spell": "'mage_hand'",
                    "spell_level": "slot_level",
                    "source_id": "_utility_source_id",
                    "source_sha256": repr(SOURCE_PAGES[145]),
                    "caster": "_caster_state_id",
                    "target": "_caster_state_id",
                    "owner": "_caster_state_id",
                    "phase": "'never'",
                    "turn": 0,
                    "active": True,
                    "started_tick": "_object_now",
                    "expires_tick": "_object_now + 20",
                    "started_turn": "_force_turn",
                    "dispel_ongoing": True,
                    "force_id": "output_id",
                    "cargo_id": "''",
                },
            ),
            _set("_utility_records", []),
            _when("state.turn_effects != null", [_set("_utility_records", "state.turn_effects")]),
            *_save_hand_source(),
            _set("caster_state.mage_hand_source_id", "_utility_source_id"),
            _set("state.has_object_force_sources", True),
            _call(
                "operate_srd_mage_hand",
                {
                    "caster_state": "{_caster_state_id}",
                    "source_id": "{_utility_source_id}",
                    "object_id": "{object_id}",
                    "operation": "{operation}",
                },
            ),
            _set("_utility_summary", [{"label": "'Spectral hand'", "value": "output_id"}]),
            _set("_utility_details", [{"label": "'Duration'", "value": "'One minute'"}]),
        ],
    )


def _control_tool():
    return _tool_scene(
        "Spend the actual later Magic action to move and manipulate the saved spectral hand.",
        [
            _entity("caster_state"),
            _parameter("source_id"),
            _array("destination"),
            _parameter("operation", "enum", enum_values=OPERATIONS),
            _optional("object_id", default=""),
        ],
        [
            *_hand_source(),
            _require(
                "combat.pending_action == null and combat.encounter_active == true and "
                "combat.turn_active == true and combat.active_actor == _caster_state_id and "
                "caster_state.turns_started > _utility_record.started_turn",
                "Control the hand on a later actual caster turn.",
            ),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_force_hand_scene.position}",
                    "end": "{destination}",
                },
                ["_scene_distance"],
            ),
            _require(
                "_scene_distance <= 30", "One real Magic action moves the hand at most thirty feet."
            ),
            *_sweep("_utility_record.force_id", "destination"),
            _when(
                "_utility_record.cargo_id != ''",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_utility_record.cargo_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_force_cargo", "_info_scene_entity"),
                    _require(
                        "_force_cargo.metadata.srd_spell_carrier.source_id == source_id and "
                        "_force_cargo.metadata.plane == _force_hand_scene.metadata.plane",
                        "The exact held cargo must still belong to this hand.",
                    ),
                    _set(
                        "_force_cargo_destination",
                        [
                            f"_force_cargo.position[{i}] + destination[{i}] - "
                            f"_force_hand_scene.position[{i}]"
                            for i in range(3)
                        ],
                    ),
                    *_sweep("_utility_record.cargo_id", "_force_cargo_destination"),
                ],
            ),
            _call("spend_srd_action", {"magic_action": True}),
            _when(
                "_utility_record.cargo_id != ''",
                [_move("_utility_record.cargo_id", "_force_cargo_destination")],
            ),
            _move("_utility_record.force_id", "destination"),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_info_scene_entity.position}",
                    "end": "{destination}",
                },
                ["_scene_distance"],
            ),
            _set(
                "_force_vanished",
                "_scene_distance > 30 or caster_state.mage_hand_source_id != source_id",
            ),
            {
                "condition": "_force_vanished == true",
                "on_true": [
                    _when(
                        "caster_state.mage_hand_source_id == source_id",
                        [
                            _call(
                                "retire_srd_mage_hand",
                                {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                            ),
                        ],
                    ),
                ],
                "on_false": [
                    _call(
                        "operate_srd_mage_hand",
                        {
                            "caster_state": "{_caster_state_id}",
                            "source_id": "{source_id}",
                            "object_id": "{object_id}",
                            "operation": "{operation}",
                        },
                    ),
                ],
            },
        ],
    )


def _lifecycle_tools():
    cleanup = _tool_scene(
        "Vanish only the exact actual spectral hand, releasing its source-owned cargo.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_read_source("mage_hand"),
            _require("_utility_record.caster == _caster_state_id", "Use the actual source owner."),
            _require(
                f"_utility_record.source_sha256 == {SOURCE_PAGES[145]!r}",
                "Retire only the actual pinned Mage Hand source.",
            ),
            _when(
                "_utility_record.cargo_id != ''",
                [
                    _call(
                        "release_srd_mage_hand_object",
                        {
                            "object_id": "{_utility_record.cargo_id}",
                            "source_id": "{source_id}",
                        },
                    ),
                ],
            ),
            _call(
                "deactivate_srd_mage_hand_scene",
                {"object_id": "{_utility_record.force_id}", "source_id": "{source_id}"},
            ),
            _when(
                "caster_state.mage_hand_source_id == source_id",
                [_set("caster_state.mage_hand_source_id", None)],
            ),
        ],
    )
    retire = _tool_scene(
        "Retire this exact Mage Hand through the shared object utility source owner.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _call(
                "retire_srd_object_utility_source",
                {
                    "caster_state": "{_caster_state_id}",
                    "source_id": "{source_id}",
                },
            )
        ],
    )
    deactivate = _tool_scene(
        "Deactivate only the actual source force scenery row without creature effects.",
        [_parameter("object_id"), _parameter("source_id")],
        [
            *_lookup_object(),
            _require(
                "_object_scene.metadata.srd_object_force.source_id == source_id",
                "Vanish only this source's actual spectral hand.",
            ),
            *_metadata_patch({}, active=False),
        ],
    )
    refresh_one = _tool_scene(
        "End the actual hand at its deadline or when caster separation exceeds thirty feet.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_read_source("mage_hand"),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_utility_record.force_id}"},
                POSITION_EXPORTS,
            ),
            _set("_force_scene", "_info_scene_entity"),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_force_scene.position}",
                    "end": "{_info_scene_entity.position}",
                },
                ["_scene_distance"],
            ),
            _when(
                "_object_now >= _utility_record.expires_tick or _scene_distance > 30 or "
                "_force_scene.metadata.plane != _info_scene_entity.metadata.plane",
                [
                    _call(
                        "retire_srd_mage_hand",
                        {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                    )
                ],
            ),
        ],
    )
    refresh = _tool_scene(
        "Refresh actual stored hand sources using the shared turn clock and sole scene positions.",
        [],
        [
            _set("_force_pending", []),
            _when("state.turn_effects != null", [_set("_force_pending", "state.turn_effects")]),
            _loop(
                "_force_pending",
                "_force_pending_source",
                [
                    _when(
                        "_force_pending_source.kind == 'object_utility_spell' and "
                        "_force_pending_source.spell == 'mage_hand'",
                        [
                            _call(
                                "refresh_srd_mage_hand",
                                {
                                    "caster_state": "{_force_pending_source.caster}",
                                    "source_id": "{_force_pending_source.source_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "retire_srd_mage_hand": retire,
        "clear_srd_mage_hand": cleanup,
        "qualify_srd_mage_hand_source": _tool_scene(
            "Qualify the actual current spectral hand for a selected source callback.",
            [_entity("caster_state"), _parameter("source_id")],
            _hand_source(),
        ),
        "deactivate_srd_mage_hand_scene": deactivate,
        "refresh_srd_mage_hand": refresh_one,
        "refresh_srd_object_forces": refresh,
    }


def extend_runtime(text):
    """Bind real hand control after Object Utilities, preserving shared payment and loss guards."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "cast_srd_mage_hand": _cast_tool(),
        "operate_srd_mage_hand": _operation_tool(),
        "release_srd_mage_hand_object": _release_tool(),
        "control_srd_mage_hand": _control_tool(),
        **_lifecycle_tools(),
    }
    changed["control_srd_mage_hand"]["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "compact",
        "title": "Mage Hand",
        "summary": [{"label": "Vanished", "value": {"path": "context._force_vanished"}}],
    }
    for name in ["cast_spell", "cast_spell_outside_combat", "cast_spell_object_utility_spell"]:
        tool = deepcopy(tools[name])
        existing = {p["name"] for p in tool["parameters"]}
        tool["parameters"].extend(p for p in deepcopy(CAST_CHOICES) if p["name"] not in existing)
        if name == "cast_spell_object_utility_spell":
            tool["effects"] = [
                e for e in tool["effects"] if e.get("condition") != "spell == 'mage_hand'"
            ]
            for e in tool["effects"]:
                if e.get("require", {}).get("condition") == "spell == 'instant_summons'":
                    e["require"] = {
                        "condition": "spell == 'instant_summons' or spell == 'mage_hand'",
                        "error": "Floating Disk controls remain pending.",
                    }
            tool["effects"].append(
                {
                    "condition": "spell == 'mage_hand'",
                    "on_true": [
                        {
                            "call": "cast_srd_mage_hand",
                            "params": {
                                "caster_state": "{_caster_state_id}",
                                "spell": "mage_hand",
                                "slot_level": "{slot_level}",
                                "output_id": "{utility_output_id}",
                                "form_id": "{utility_form_id}",
                                "point": "{utility_point}",
                                "operation": "{utility_operation}",
                                "object_id": "{utility_object_id}",
                            },
                            "export_context": {
                                "_utility_summary": "_utility_summary",
                                "_utility_details": "_utility_details",
                            },
                        }
                    ],
                }
            )
        else:

            def bind(effects):
                for e in effects:
                    if e.get("call") in {"cast_spell", "cast_spell_object_utility_spell"}:
                        e["params"]["utility_operation"] = "{utility_operation}"
                    for key in ["on_true", "on_false"]:
                        bind(e.get(key, []))

            bind(tool["effects"])
        changed[name] = tool
    process = deepcopy(tools["process_srd_turn_effects"])
    process["effects"] = [
        e for e in process["effects"] if e.get("call") != "refresh_srd_object_forces"
    ]
    process["effects"].insert(0, _call("refresh_srd_object_forces", {}))
    changed["process_srd_turn_effects"] = process
    retire = deepcopy(tools["retire_srd_object_utility_source"])
    retire["effects"] = [
        e for e in retire["effects"] if e.get("condition") != "_utility_record.spell == 'mage_hand'"
    ]
    index = next(
        i
        for i, e in enumerate(retire["effects"])
        if e.get("set", {}).get("field") == "_utility_kept_records"
    )
    retire["effects"].insert(
        index,
        _when(
            "_utility_record.spell == 'mage_hand'",
            [
                _call(
                    "clear_srd_mage_hand",
                    {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                ),
            ],
        ),
    )
    changed["retire_srd_object_utility_source"] = retire
    read = deepcopy(tools["read_srd_object_utility_source"])
    read["effects"] = [
        e for e in read["effects"] if e.get("condition") != "_utility_record.spell == 'mage_hand'"
    ]
    index = next(
        i
        for i, e in enumerate(read["effects"])
        if e.get("set", {}).get("field") == "_object_utility_source"
    )
    read["effects"].insert(
        index,
        _when(
            "_utility_record.spell == 'mage_hand'",
            [
                _require(
                    "object_id == _utility_record.force_id",
                    "Select this actual spectral hand source.",
                ),
                _call(
                    "qualify_srd_mage_hand_source",
                    {
                        "caster_state": "{_utility_record.caster}",
                        "source_id": "{source_id}",
                    },
                    ["_utility_record"],
                ),
            ],
        ),
    )
    changed["read_srd_object_utility_source"] = read
    return _replace_tools(text, changed)


def bind_runtime_consumers(text):
    """Recheck actual caster separation immediately after the sole relocation mutation."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("move_srd_travel_subject", "move_srd_scene_creature_node"):
        move = deepcopy(tools[name])
        move["effects"] = [
            e
            for e in move["effects"]
            if not any(
                child.get("call") == "refresh_srd_object_forces" for child in e.get("on_true", [])
            )
        ]
        move["effects"].append(
            _when(
                "state.has_object_force_sources == true",
                [_call("refresh_srd_object_forces", {})],
            )
        )
        changed[name] = move
    return _replace_tools(text, changed)
