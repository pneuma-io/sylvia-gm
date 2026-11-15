"""Real bead contact saves and source-owned forty-foot throws reuse admitted geometry."""

from copy import deepcopy

if __package__:
    from . import srd_delayed_blast_fireball as bead
    from . import srd_sunburst_geometry as sphere
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _public, _push, _replace_tools
else:
    import srd_delayed_blast_fireball as bead
    import srd_sunburst_geometry as sphere
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _public, _push, _replace_tools


PUBLIC_TOOLS = {"touch_srd_delayed_blast_fireball", "throw_srd_delayed_blast_fireball"}
POSITION_EXPORTS_BEAD = ("_bead_origin", "_bead_plane")
CONTACT_EXPORTS = (*POSITION_EXPORTS_BEAD, "_bead_record", "_bead_actor_body")


def _point(name):
    return _parameter(name, "array", items={"type": "number"}, min_items=3, max_items=3)


def _form(actor, store):
    return [
        _set(store, None),
        _when(
            f"{actor}.form_current_source_id != null",
            [_set(store, f"{actor}.form_current_source_id")],
        ),
    ]


def _holder_tool():
    """A held source point follows actual position, with its body and form facts revalidated."""
    return _tool_scene(
        "Qualify the actual saved bead point against its current admitted holder body.",
        [_entity("holder_state"), _parameter("record", "object", from_state="state.turn_effects")],
        [
            _require("record.held.actor == _holder_state_id", "This is not the saved bead holder."),
            _call(
                "srd_information_scene_entity", {"subject": "{_holder_state_id}"}, POSITION_EXPORTS
            ),
            _set("_bead_holder_body", "_info_scene_entity"),
            *_form("holder_state", "_bead_holder_form"),
            _require(
                "_bead_holder_body.footprint.kind == 'box' and "
                "_bead_holder_body.metadata.plane != null and "
                "record.held.footprint.kind == 'box' and "
                "record.held.height == _bead_holder_body.height and "
                "record.held.footprint.half_extents[0] == "
                "_bead_holder_body.footprint.half_extents[0] and "
                "record.held.footprint.half_extents[1] == "
                "_bead_holder_body.footprint.half_extents[1] and "
                "record.held.form_source_id == _bead_holder_form",
                "The held bead needs its recorded Box body and form; "
                "changed anatomy is unsupported.",
            ),
            _set(
                "_bead_origin",
                [f"_bead_holder_body.position[{i}] + record.held.offset[{i}]" for i in range(3)],
            ),
            _require(
                sphere._contains("_bead_holder_body", "_bead_origin"),
                "The saved bead point is outside its actual holder body.",
            ),
            _require(
                " and ".join(
                    f"_bead_origin[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                    f"_bead_origin[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                    for i in range(3)
                ),
                "The actual held bead point must remain inside the admitted map.",
            ),
            _set("_bead_plane", "_bead_holder_body.metadata.plane"),
        ],
    )


def _position_tool():
    return _tool(
        "Resolve the original point or source-owned current held point, without ending the source.",
        [_parameter("record", "object", from_state="state.turn_effects")],
        [
            _set("_bead_origin", "record.origin"),
            _set("_bead_plane", "record.plane"),
            _when(
                "record.held != null",
                [
                    _call(
                        "qualify_srd_delayed_blast_holder",
                        {"holder_state": "{record.held.actor}", "record": "{record}"},
                        POSITION_EXPORTS_BEAD,
                    )
                ],
            ),
        ],
    )


def _prepare_tool():
    return _tool_scene(
        "Authenticate the still-live paid bead and actual contacting creature.",
        [_entity("actor_state"), _parameter("source_id")],
        [
            _call(bead.REFRESH, {"lasting_state": "{_lasting_state_id}"}),
            _call(
                "check_srd_cone_creature_actor",
                {"target_state": "{_actor_state_id}"},
                ["_cone_actual_creature"],
            ),
            _require(
                "_cone_actual_creature == true",
                "Only an actual creature may touch or throw the bead.",
            ),
            _set("_bead_record", None),
            _set("_bead_rows", []),
            _when("state.turn_effects != null", [_set("_bead_rows", "state.turn_effects")]),
            _loop(
                "_bead_rows",
                "_bead_row",
                [
                    _when(
                        "_bead_row.kind == 'delayed_blast_fireball' and "
                        "_bead_row.source_id == source_id",
                        [
                            _require(
                                "_bead_record == null", "The paid bead has ambiguous source rows."
                            ),
                            _call(
                                "check_srd_delayed_blast_fireball_record",
                                {"caster_state": "{_bead_row.caster}", "record": "{_bead_row}"},
                                ["_dbf_active"],
                            ),
                            _require(
                                "_dbf_active == true and _bead_row.cast_id == source_id",
                                "Only the actual current unsuppressed paid bead is usable.",
                            ),
                            _set("_bead_record", "_bead_row"),
                        ],
                    )
                ],
            ),
            _require("_bead_record != null", "No current paid bead has this source ID."),
            _call(
                "resolve_srd_delayed_blast_position",
                {"record": "{_bead_record}"},
                POSITION_EXPORTS_BEAD,
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_actor_state_id}"}, POSITION_EXPORTS
            ),
            _set("_bead_actor_body", "_info_scene_entity"),
            _require(
                "_bead_actor_body.footprint.kind == 'box' and "
                "_bead_actor_body.metadata.plane == _bead_plane and "
                + sphere._contains("_bead_actor_body", "_bead_origin"),
                "Touch needs the actual bead point inside this same-plane admitted Box body.",
            ),
        ],
    )


def _replace_record():
    return [
        _set("_bead_kept", []),
        _set("_bead_replace_rows", "state.turn_effects"),
        _loop(
            "_bead_replace_rows",
            "_bead_old",
            [
                {
                    "condition": "_bead_old.kind == 'delayed_blast_fireball' and "
                    "_bead_old.source_id == source_id",
                    "on_true": [_push("_bead_kept", "_bead_updated")],
                    "on_false": [_push("_bead_kept", "_bead_old")],
                },
            ],
        ),
        _set("state.turn_effects", "_bead_kept"),
    ]


def _ending():
    return [
        _call(
            "finish_srd_delayed_blast_removal",
            {"caster_state": "{_bead_record.caster}", "source_id": "{source_id}"},
        ),
        {"call": bead.DRAIN, "params": {}, "export_context": "*"},
    ]


def _touch_tool():
    effects = [
        _call(
            "prepare_srd_delayed_blast_contact",
            {"actor_state": "{_actor_state_id}", "source_id": "{source_id}"},
            CONTACT_EXPORTS,
        ),
        _require(
            "actor_state.saving_throw_modifiers != null and "
            "actor_state.saving_throw_modifiers.dexterity != null",
            "The touching creature needs its actual prepared Dexterity save modifier.",
        ),
        {
            "call": "d20_test",
            "params": {
                "actor_state": "{_actor_state_id}",
                "ability": "dexterity",
                "ability_modifier": "{actor_state.saving_throw_modifiers.dexterity}",
                "proficiency_bonus": 0,
                "kind": "saving_throw",
                "proficiency_factor": "none",
                "circumstantial_modifier": 0,
                "target_number": "{_bead_record.save_dc}",
                "advantage": False,
                "disadvantage": False,
                "forego_save": False,
            },
            "export_context": {"_bead_touch_roll": "_d20_test", "_bead_touch_outcome": "_outcome"},
        },
        _set("_bead_touch_faces", "'No roll'"),
        _when("_bead_touch_roll != null", [_set("_bead_touch_faces", "_bead_touch_roll.results")]),
        {
            "condition": "_bead_touch_outcome == 'failure'",
            "on_true": _ending(),
            "on_false": [
                *_form("actor_state", "_bead_touch_form"),
                _set(
                    "_bead_held",
                    {
                        "actor": "_actor_state_id",
                        "offset": [
                            f"_bead_origin[{i}] - _bead_actor_body.position[{i}]" for i in range(3)
                        ],
                        "height": "_bead_actor_body.height",
                        "footprint": "_bead_actor_body.footprint",
                        "form_source_id": None,
                    },
                ),
                _when(
                    "_bead_touch_form != null",
                    [
                        _set("_bead_form_patch", {"form_source_id": "_bead_touch_form"}),
                        _set("_bead_held", "merge(_bead_held, _bead_form_patch)"),
                    ],
                ),
                _set(
                    "_bead_patch",
                    {"held": "_bead_held", "origin": "_bead_origin", "plane": "_bead_plane"},
                ),
                _set("_bead_updated", "merge(_bead_record, _bead_patch)"),
                *_replace_record(),
            ],
        },
    ]
    return _public(
        _tool(
            "Touch the bead: native Dexterity failure detonates; success retains one real throw.",
            [_entity("actor_state"), _parameter("source_id")],
            effects,
        ),
        "exploration",
        "Delayed Blast Fireball contact",
        [
            ("Source", "source_id"),
            ("Dexterity save", "_bead_touch_faces"),
            ("Outcome", "_bead_touch_outcome"),
        ],
    )


def _body_volume(frame):
    return {
        "kind": "box",
        "min": [
            f"{frame}.position[0] - {frame}.footprint.half_extents[0]",
            f"{frame}.position[1] - {frame}.footprint.half_extents[1]",
            f"{frame}.position[2]",
        ],
        "max": [
            f"{frame}.position[0] + {frame}.footprint.half_extents[0]",
            f"{frame}.position[1] + {frame}.footprint.half_extents[1]",
            f"{frame}.position[2] + {frame}.height",
        ],
    }


def _hit(volume):
    return [
        _call(
            "srd_information_segment_interval",
            {"start": "{start}", "end": "{end}", "volume": "{" + volume + "}"},
            ["_segment_hit", "_segment_entry"],
        ),
        _when(
            "_segment_hit == true and _segment_entry <= _bead_impact_fraction",
            [
                _set("_bead_collided", True),
                _set("_bead_impact_fraction", "_segment_entry"),
            ],
        ),
    ]


def _collision_tool():
    """The first real segment entry chooses impact without a caller hit verdict."""
    return _tool_scene(
        "Find the first actual creature-space or solid collision along the forty-foot throw.",
        [_entity("actor_state"), _point("start"), _point("end"), _parameter("plane")],
        [
            *_snapshot(),
            _set("_bead_collided", False),
            _set("_bead_impact_fraction", 1),
            _set("_bead_geometry", "state.spatial_scene.geometry"),
            _loop(
                "_bead_geometry",
                "_bead_barrier",
                [
                    _when(
                        "_bead_barrier.active == true and _bead_barrier.movement == 'blocking' and "
                        "(_bead_barrier.metadata.plane == plane or "
                        "(_bead_barrier.metadata.plane == null and plane == 'material'))",
                        _hit("_bead_barrier.volume"),
                    ),
                ],
            ),
            _set("_bead_scene_rows", "state.spatial_scene.entities"),
            _loop(
                "_bead_scene_rows",
                "_bead_body",
                [
                    _when(
                        "_bead_body.active == true and _bead_body.metadata.plane == plane and "
                        "_bead_body.metadata.participant_ref != _actor_state_id",
                        [
                            _require(
                                "_bead_body.footprint.kind == 'box'",
                                "Bead throwing currently needs actual Box creature/object bodies.",
                            ),
                            _set("_bead_body_volume", _body_volume("_bead_body")),
                            _call(
                                "srd_information_segment_interval",
                                {
                                    "start": "{start}",
                                    "end": "{end}",
                                    "volume": "{_bead_body_volume}",
                                },
                                ["_segment_hit", "_segment_entry"],
                            ),
                            _when(
                                "_segment_hit == true and _segment_entry <= _bead_impact_fraction",
                                [
                                    _set("_bead_physical", False),
                                    {
                                        "condition": "_bead_body.metadata.is_object == true",
                                        "on_true": [
                                            _require(
                                                "_bead_body.metadata.srd_spell_object != null and "
                                                "_bead_body.metadata.srd_spell_object.kind "
                                                "== 'srd_spell_object'",
                                                "A crossed object needs its physical material.",
                                            ),
                                            _require(
                                                "(_bead_body.metadata.carried_by == null or "
                                                "_bead_body.metadata.carried_by == '') and "
                                                "(_bead_body.metadata.worn_by == null or "
                                                "_bead_body.metadata.worn_by == '')",
                                                "Carried objects need current body geometry.",
                                            ),
                                            _set("_bead_physical", True),
                                        ],
                                        "on_false": [
                                            _when(
                                                "_bead_body.metadata.participant_ref != null",
                                                [
                                                    _call(
                                                        "check_srd_cone_creature_actor",
                                                        {
                                                            "target_state": (
                                                                "{_bead_body.metadata."
                                                                "participant_ref}"
                                                            )
                                                        },
                                                        ["_cone_actual_creature"],
                                                    ),
                                                    _set(
                                                        "_bead_physical",
                                                        "_cone_actual_creature == true",
                                                    ),
                                                ],
                                            ),
                                        ],
                                    },
                                    _when(
                                        "_bead_physical == true",
                                        [
                                            _set("_bead_collided", True),
                                            _set("_bead_impact_fraction", "_segment_entry"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set(
                "_bead_impact_point",
                [f"start[{i}] + (end[{i}] - start[{i}]) * _bead_impact_fraction" for i in range(3)],
            ),
        ],
    )


def _throw_tool():
    return _public(
        _tool(
            "Throw the successfully touched bead up to forty feet; real impact detonates.",
            [_entity("actor_state"), _parameter("source_id"), _point("throw_destination")],
            [
                _require(
                    "actor_state.is_alive == true and not "
                    "('incapacitated' in actor_state.conditions or "
                    "'paralyzed' in actor_state.conditions or "
                    "'petrified' in actor_state.conditions or "
                    "'stunned' in actor_state.conditions or "
                    "'unconscious' in actor_state.conditions)",
                    "The throwing creature must be able to act.",
                ),
                _call(
                    "prepare_srd_delayed_blast_contact",
                    {"actor_state": "{_actor_state_id}", "source_id": "{source_id}"},
                    CONTACT_EXPORTS,
                ),
                _require(
                    "_bead_record.held != null and _bead_record.held.actor == _actor_state_id",
                    "Only the actual successful toucher holding this bead may throw it.",
                ),
                _require(
                    " + ".join(
                        f"(throw_destination[{i}] - _bead_origin[{i}]) ** 2" for i in range(3)
                    )
                    + " <= 40 ** 2",
                    "The actual throw endpoint is beyond forty feet.",
                ),
                _require(
                    " and ".join(
                        f"throw_destination[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                        f"throw_destination[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                        for i in range(3)
                    ),
                    "The throw endpoint must be inside the actual admitted map.",
                ),
                _call(
                    "resolve_srd_delayed_blast_throw_collision",
                    {
                        "actor_state": "{_actor_state_id}",
                        "start": "{_bead_origin}",
                        "end": "{throw_destination}",
                        "plane": "{_bead_plane}",
                    },
                    ["_bead_collided", "_bead_impact_point"],
                ),
                _set(
                    "_bead_patch",
                    {"origin": "_bead_impact_point", "plane": "_bead_plane", "held": None},
                ),
                _set("_bead_updated", "merge(_bead_record, _bead_patch)"),
                *_replace_record(),
                _when("_bead_collided == true", _ending()),
            ],
        ),
        "exploration",
        "Delayed Blast Fireball throw",
        [
            ("Source", "source_id"),
            ("Impact or endpoint", "_bead_impact_point"),
            ("Collision", "_bead_collided"),
        ],
    )


def bind_runtime_consumers(text):
    """Append contact leaves and anchor the genuine pre-retirement queue to current occupancy."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "qualify_srd_delayed_blast_holder": _holder_tool(),
        "resolve_srd_delayed_blast_position": _position_tool(),
        "prepare_srd_delayed_blast_contact": _prepare_tool(),
        "touch_srd_delayed_blast_fireball": _touch_tool(),
        "resolve_srd_delayed_blast_throw_collision": _collision_tool(),
        "throw_srd_delayed_blast_fireball": _throw_tool(),
    }
    queue = deepcopy(tools[bead.QUEUE])
    if "resolve_srd_delayed_blast_position" not in str(queue["effects"]):
        found = False
        for effects in bead.beam._lists(queue["effects"]):
            for index, effect in enumerate(effects):
                if (
                    effect.get("set", {}).get("field") == "_dbf_ending_record"
                    and effect["set"]["value"] == "_dbf_row"
                ):
                    effects[index : index + 1] = [
                        _call(
                            "resolve_srd_delayed_blast_position",
                            {"record": "{_dbf_row}"},
                            POSITION_EXPORTS_BEAD,
                        ),
                        _set(
                            "_bead_ending_patch", {"origin": "_bead_origin", "plane": "_bead_plane"}
                        ),
                        _set("_dbf_ending_record", "merge(_dbf_row, _bead_ending_patch)"),
                    ]
                    found = True
                    break
        if not found:
            raise ValueError("The genuine pre-retirement DBF queue source capture is unavailable.")
        changed[bead.QUEUE] = queue
    return _replace_tools(
        text, {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    )
