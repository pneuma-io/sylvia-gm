"""Queue water-wall exits from the genuine before/after Travel mutation evidence."""

from copy import deepcopy

if __package__:
    from .srd_courier_chronology import _emit
    from .srd_scene_relations import _tool_scene
    from .srd_spell_travel import _scene_subject
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _push
    from .srd_tsunami import _source
else:
    from srd_courier_chronology import _emit
    from srd_scene_relations import _tool_scene
    from srd_spell_travel import _scene_subject
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _push
    from srd_tsunami import _source


WRAPPER = "move_srd_travel_subject"
NODE = "move_srd_travel_subject_node"
CAPTURE = "capture_srd_tsunami_travel"
OBSERVE = "observe_srd_tsunami_travel"
ENTRIES = "_tsunami_travel_entries"
CAPTURE_GUARD = (
    "lasting_state.has_tsunami_sources == true or lasting_state.has_tsunami_pending_exits == true"
)
OBSERVE_GUARD = f"len({ENTRIES}) > 0"


def _private(description, parameters, effects):
    """Keep native receipts internal; they are derived by the sole owning movement call."""
    tool = _tool_scene(description, parameters, effects)
    for parameter in tool["parameters"]:
        parameter["model_hidden"] = True
        if parameter["name"] == "motion":
            parameter["from_state"] = "state.spatial_scene"
    return tool


def _live_tool():
    return _private(
        "Read this exact water focus and clock without retiring a source during movement.",
        [_entity("caster_state"), _parameter("source_id"), _parameter("sequence", "integer")],
        [
            *_source(),
            _set("_tsunami_travel_live", False),
            _when(
                "_tsunami_source.spell == 'tsunami' and _tsunami_source.mode == 'tsunami' and "
                "_tsunami_source.caster == _caster_state_id and _tsunami_source.sequence == "
                "sequence and _tsunami_source.active == true and _tsunami_source.height > 0",
                [
                    _when(
                        "state.lasting_tick >= _tsunami_source.started_tick and "
                        "state.lasting_tick < _tsunami_source.expires_tick and "
                        "caster_state.concentration_spell_id == 'tsunami' and "
                        "caster_state.concentration_effect_family == 'tsunami' and "
                        "caster_state.concentration_sequence == sequence",
                        [_set("_tsunami_travel_live", True)],
                    )
                ],
            ),
        ],
    )


def _require_all(clauses, error):
    """Keep source/body proofs within the existing 500-character expression limit."""
    coordinates = {
        "motion.patch.position == _tsunami_travel_after.position": range(3),
        "motion.patch.metadata.previous_position == _tsunami_travel_entry.origin": range(3),
        "_tsunami_travel_exit.position == _tsunami_travel_entry.origin": range(3),
        "_tsunami_travel_after.footprint.half_extents == _tsunami_travel_entry.extents": range(2),
    }
    result = []
    for clause in clauses.split(" and "):
        if clause in coordinates:
            left, right = clause.split(" == ")
            result.extend(
                _require(f"{left}[{i}] == {right}[{i}]", error) for i in coordinates[clause]
            )
        elif clause == "_tsunami_source.volume == _tsunami_travel_entry.volume":
            # The DSL compares scalar values; validate every actual Box coordinate.
            result.append(
                _require("_tsunami_source.volume.kind == _tsunami_travel_entry.volume.kind", error)
            )
            result.extend(
                _require(
                    f"_tsunami_source.volume.{face}[{i}] == "
                    f"_tsunami_travel_entry.volume.{face}[{i}]",
                    error,
                )
                for face in ("min", "max")
                for i in range(3)
            )
        else:
            result.append(_require(clause, error))
    return result


def _entry_record():
    return {
        "source_id": "_tsunami_travel_source.id",
        "sequence": "_tsunami_travel_source.sequence",
        "cast_id": "_tsunami_travel_source.cast_id",
        "caster": "_tsunami_travel_source.caster",
        "volume": "_tsunami_travel_source.volume",
        "source_plane": "_tsunami_travel_source.plane",
        "body_id": "_tsunami_travel_before.id",
        "subject": "_tsunami_travel_before.metadata.participant_ref",
        "origin": "_tsunami_travel_before.position",
        "plane": "_tsunami_travel_before.metadata.plane",
        "height": "_tsunami_travel_before.height",
        "extents": "_tsunami_travel_before.footprint.half_extents",
        "tick": "state.lasting_tick",
    }


def _capture_source():
    return [
        _when(
            "_tsunami_travel_source.active == true",
            [
                _call(
                    "check_srd_tsunami_travel_source",
                    {
                        "caster_state": "{_tsunami_travel_source.caster}",
                        "source_id": "{_tsunami_travel_source.id}",
                        "sequence": "{_tsunami_travel_source.sequence}",
                    },
                    ["_tsunami_travel_live"],
                ),
                _when(
                    "_tsunami_travel_live == true",
                    [
                        _call(
                            "resolve_srd_tsunami_body_segment",
                            {
                                "source_id": "{_tsunami_travel_source.id}",
                                "moving_subject": "{subject}",
                                "start": "{_tsunami_travel_before.position}",
                                "end": "{_tsunami_travel_before.position}",
                            },
                            ["_body_hit"],
                        ),
                        _when("_body_hit == true", [_push(ENTRIES, _entry_record())]),
                    ],
                ),
            ],
        )
    ]


def _capture_tool():
    return _private(
        "Capture actual contained creatures before the final Travel node and its receiving hooks.",
        [_parameter("subject")],
        [
            _set(ENTRIES, []),
            *_scene_subject("{subject}", "_tsunami_travel_before"),
            _when(
                "_tsunami_travel_before.metadata.participant_ref != null",
                [
                    _set("_tsunami_travel_old_exits", []),
                    _when(
                        "state.tsunami_exits != null",
                        [_set("_tsunami_travel_old_exits", "state.tsunami_exits")],
                    ),
                    _loop(
                        "_tsunami_travel_old_exits",
                        "_tsunami_travel_old_exit",
                        [
                            _require(
                                "_tsunami_travel_old_exit.pending != true or "
                                "_tsunami_travel_old_exit.body_id != _tsunami_travel_before.id",
                                "Settle this creature's already earned water exit before travel.",
                            )
                        ],
                    ),
                    _when(
                        "state.has_tsunami_sources == true and "
                        "(state.tsunami_pending_motion == null or "
                        "state.tsunami_pending_motion.subject != "
                        "_tsunami_travel_before.metadata.participant_ref)",
                        [
                            _set("_tsunami_travel_sources", "state.tsunami_sources"),
                            _loop(
                                "_tsunami_travel_sources",
                                "_tsunami_travel_source",
                                _capture_source(),
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _carry_earned_exit():
    """Relocate only an exit earned during this same node's pre-mutation receiving effects."""
    return [
        _set("_tsunami_travel_exit_rows", []),
        _when(
            "state.tsunami_exits != null",
            [_set("_tsunami_travel_exit_rows", "state.tsunami_exits")],
        ),
        _set("_tsunami_travel_updated_exits", []),
        _set("_tsunami_travel_carried_exit", False),
        _loop(
            "_tsunami_travel_exit_rows",
            "_tsunami_travel_exit",
            [
                _when(
                    "_tsunami_travel_exit.pending == true and "
                    "_tsunami_travel_exit.source_id == _tsunami_travel_entry.source_id and "
                    "_tsunami_travel_exit.body_id == _tsunami_travel_entry.body_id",
                    [
                        *_require_all(
                            "_tsunami_travel_carried_exit != true and "
                            "_tsunami_travel_exit.sequence == _tsunami_travel_entry.sequence and "
                            "_tsunami_travel_exit.subject == _tsunami_travel_entry.subject and "
                            "_tsunami_travel_exit.tick == _tsunami_travel_entry.tick and "
                            "_tsunami_travel_exit.position == _tsunami_travel_entry.origin and "
                            "_tsunami_travel_exit.plane == _tsunami_travel_entry.plane and "
                            "_tsunami_travel_exit.height == _tsunami_travel_entry.height",
                            "Only this newly earned exact water exit may follow "
                            "its committed move.",
                        ),
                        _set(
                            "_tsunami_travel_exit_patch",
                            {
                                "position": "_tsunami_travel_after.position",
                                "plane": "_tsunami_travel_after.metadata.plane",
                                "height": "_tsunami_travel_after.height",
                                "move_revision": "motion.geometry_revision",
                            },
                        ),
                        _set(
                            "_tsunami_travel_exit",
                            "merge(_tsunami_travel_exit, _tsunami_travel_exit_patch)",
                        ),
                        _set("_tsunami_travel_carried_exit", True),
                    ],
                ),
                _push("_tsunami_travel_updated_exits", "_tsunami_travel_exit"),
            ],
        ),
        _when(
            "_tsunami_travel_carried_exit == true",
            [
                _set("state.tsunami_exits", "_tsunami_travel_updated_exits"),
                _set("state.has_tsunami_pending_exits", True),
            ],
        ),
        _when(
            "_tsunami_travel_carried_exit != true",
            [
                _call(
                    "queue_srd_tsunami_exit",
                    {
                        "source_id": "{_tsunami_travel_entry.source_id}",
                        "subject": "{_tsunami_travel_entry.subject}",
                    },
                )
            ],
        ),
    ]


def _observe_tool():
    return _private(
        "Queue authentic source exits at the committed endpoint without opening a nested fall.",
        [
            _parameter("subject"),
            _parameter("entries", "array", items={"type": "object"}),
            _parameter("motion", "object"),
        ],
        [
            _when(
                "len(entries) > 0",
                [
                    *_scene_subject("{subject}", "_tsunami_travel_after"),
                    *_require_all(
                        "motion.kind == 'scene_mutation' and motion.target.kind == 'entity' and "
                        "motion.target.id == _tsunami_travel_after.id and "
                        "motion.map_id == state.spatial_scene.map.id and "
                        "motion.geometry_revision == state.spatial_scene.map.revision and "
                        "motion.after.geometry_revision == motion.before.geometry_revision + 1 "
                        "and motion.geometry_revision == motion.after.geometry_revision and "
                        "motion.patch.position == _tsunami_travel_after.position and "
                        "motion.patch.metadata.plane == _tsunami_travel_after.metadata.plane",
                        "Use the sole Travel node's actual latest committed mutation evidence.",
                    ),
                    _loop(
                        "entries",
                        "_tsunami_travel_entry",
                        [
                            *_source("_tsunami_travel_entry.source_id"),
                            *_require_all(
                                "_tsunami_source.spell == 'tsunami' and "
                                "_tsunami_source.sequence == _tsunami_travel_entry.sequence and "
                                "_tsunami_source.cast_id == _tsunami_travel_entry.cast_id and "
                                "_tsunami_source.caster == _tsunami_travel_entry.caster and "
                                "_tsunami_source.volume == _tsunami_travel_entry.volume and "
                                "_tsunami_source.plane == _tsunami_travel_entry.source_plane and "
                                "_tsunami_travel_after.id == _tsunami_travel_entry.body_id and "
                                "_tsunami_travel_after.metadata.participant_ref == "
                                "_tsunami_travel_entry.subject and "
                                "_tsunami_travel_after.height == _tsunami_travel_entry.height and "
                                "_tsunami_travel_after.footprint.half_extents == "
                                "_tsunami_travel_entry.extents and "
                                "motion.patch.metadata.previous_position == "
                                "_tsunami_travel_entry.origin and "
                                "_tsunami_travel_entry.plane == _tsunami_travel_entry.source_plane "
                                "and state.lasting_tick == _tsunami_travel_entry.tick",
                                "The captured water source, actual body or clock changed "
                                "during travel.",
                            ),
                            _set("_tsunami_travel_still_inside", False),
                            _when(
                                "_tsunami_source.active == true and "
                                "_tsunami_travel_after.metadata.plane == "
                                "_tsunami_travel_entry.source_plane",
                                [
                                    _call(
                                        "resolve_srd_tsunami_body_segment",
                                        {
                                            "source_id": "{_tsunami_travel_entry.source_id}",
                                            "moving_subject": "{subject}",
                                            "start": "{_tsunami_travel_after.position}",
                                            "end": "{_tsunami_travel_after.position}",
                                        },
                                        ["_body_hit"],
                                    ),
                                    _set("_tsunami_travel_still_inside", "_body_hit"),
                                ],
                            ),
                            _when(
                                "_tsunami_travel_still_inside != true",
                                _carry_earned_exit(),
                            ),
                        ],
                    ),
                ],
            )
        ],
    )


def bind_runtime_consumers(text):
    """Bind after final relocation, receiver and Meld joins; before Reaction/completion."""
    text = text.replace("\r\n", "\n")
    tools = _load_runtime(text)["tools"]
    if "queue_srd_tsunami_exit" not in tools:
        return text
    if WRAPPER not in tools or NODE not in tools:
        raise ValueError("Tsunami travel exits require the final sole relocation split.")
    wrapper = deepcopy(tools[WRAPPER])
    wrapper["effects"] = [
        effect
        for effect in wrapper["effects"]
        if effect.get("set", {}).get("field") != ENTRIES
        and effect.get("call") not in {CAPTURE, OBSERVE}
        and not (
            effect.get("condition") in {CAPTURE_GUARD, OBSERVE_GUARD}
            and len(effect.get("on_true", [])) == 1
            and effect["on_true"][0].get("call") in {CAPTURE, OBSERVE}
        )
    ]
    calls = [
        (index, effect)
        for index, effect in enumerate(wrapper["effects"])
        if effect.get("call") == NODE
    ]
    if len(calls) != 1 or any("spatial_mutation" in e for e in wrapper["effects"]):
        raise ValueError("Preserve the exact single Travel node and its post-movement suffix.")
    index, node = calls[0]
    exports = node.get("export_context", {})
    if not isinstance(exports, dict) or exports.get("_travel_scene_move") != "_travel_scene_move":
        raise ValueError("Travel must export its genuine native scene mutation receipt.")
    if not any(p["name"] == "lasting_state" for p in wrapper["parameters"]):
        raise ValueError("Preserve Travel's actual lasting-state binding.")
    wrapper["effects"][index : index + 1] = [
        _set(ENTRIES, []),
        _when(CAPTURE_GUARD, [_call(CAPTURE, {"subject": "{subject}"}, [ENTRIES])]),
        node,
        _when(
            OBSERVE_GUARD,
            [
                _call(
                    OBSERVE,
                    {
                        "subject": "{subject}",
                        "entries": "{" + ENTRIES + "}",
                        "motion": "{_travel_scene_move}",
                    },
                )
            ],
        ),
    ]
    changed = {
        WRAPPER: wrapper,
        "check_srd_tsunami_travel_source": _live_tool(),
        CAPTURE: _capture_tool(),
        OBSERVE: _observe_tool(),
    }
    if all(tools.get(name) == owner for name, owner in changed.items()):
        return text
    return _emit(text, changed)
