"""Finite Wall casts retain exact Source identity and their Violet-only Dispel exception."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _emit
    from .srd_control_behaviors import _each, _push, _walk
    from .srd_prismatic_wall import POOL, _read_source, _source_current
    from .srd_scene_relations import _array
    from .srd_spell_barriers import _effect_lists, _tool
    from .srd_spell_magic_zones import _when
    from .srd_spell_removal_adoption import _actual_path, _source_check
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _optional
else:
    from srd_actor_spell_modifiers import _emit
    from srd_control_behaviors import _each, _push, _walk
    from srd_prismatic_wall import POOL, _read_source, _source_current
    from srd_scene_relations import _array
    from srd_spell_barriers import _effect_lists, _tool
    from srd_spell_magic_zones import _when
    from srd_spell_removal_adoption import _actual_path, _source_check
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _optional

SOURCE_SPELL = "prismatic_wall"
SOURCE_INSTALLER = "cast_srd_prismatic_wall"


def _finite_match(record):
    return (
        f"{record}.cast_id == cast_id and cast_id != '' and "
        f"{record}.source_caster_id == _caster_state_id and "
        f"{record}.ongoing_duration == true and " + _source_current(record)
    )


def _tools():
    collector = _tool(
        "Collect only this caster's actual finite Wall casting from its existing source index.",
        [_entity("caster_state"), _parameter("cast_id")],
        [
            _set("_prismatic_wall_dismiss_ids", []),
            _when(
                "state.has_prismatic_walls == true",
                [
                    _set("_owned_wall_ids", "state.spell_prismatic_wall_source_ids"),
                    _each(
                        "_owned_wall_ids",
                        "_owned_wall_id",
                        [
                            _set("_owned_wall", f"state.{POOL}[_owned_wall_id]"),
                            _when(
                                _finite_match("_owned_wall"),
                                [
                                    _push("_prismatic_wall_dismiss_ids", "_owned_wall_id"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    retire = _tool(
        "Retire one finite owned Wall cast without erasing its independent target consequences.",
        [_entity("caster_state"), _parameter("cast_id"), _parameter("source_id")],
        [
            *_read_source(),
            _require(
                _finite_match("_wall_record"), "Select this caster's actual finite Wall cast."
            ),
            _call(
                "end_srd_prismatic_wall_source", {"source_id": "{source_id}", "reason": "dismissed"}
            ),
            _call("refresh_srd_prismatic_walls", {}),
        ],
    )
    dispel = _tool(
        "Apply a genuine Dispel only to the Wall's exposed Violet layer at its actual spell level.",
        [
            _entity("caster_state"),
            _optional("caster", default=None, resolve="participant"),
            _parameter("source_id"),
            _array("point"),
            _parameter("slot_level", "integer"),
            _parameter("casting_modifier", "integer"),
            _parameter("casting_ability"),
        ],
        [
            *_read_source(),
            _require(_source_current("_wall_record"), "Select an actual current Wall."),
            _require(
                " and ".join(
                    f"point[{a}] >= _wall_record.volume.min[{a}] and "
                    f"point[{a}] <= _wall_record.volume.max[{a}]"
                    for a in range(3)
                ),
                "Choose an actual point on this Wall's saved surface.",
            ),
            *_actual_path("point", "_wall_record.plane"),
            _set(
                "_adoption_dispel_check",
                {
                    "spell": "'prismatic_wall'",
                    "source_id": "source_id",
                    "ended": False,
                    "roll": None,
                    "dc": 19,
                    "no_effect": True,
                },
            ),
            _when(
                "_wall_record.outer_layer == 7 and _wall_record.layers_remaining == 1",
                [
                    *_source_check("_wall_record", "spell_id"),
                    _when(
                        "_dispel_succeeded == true",
                        [
                            _call(
                                "retire_srd_prismatic_wall_layer",
                                {
                                    "source_id": "{source_id}",
                                    "expected_layer": 7,
                                    "reason": "actual Dispel Magic",
                                },
                            ),
                            _call("refresh_srd_prismatic_walls", {}),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "collect_srd_prismatic_wall_owned_cast": collector,
        "retire_srd_prismatic_wall_owned_cast": retire,
        "dispel_srd_prismatic_wall_owned_source": dispel,
    }


def _stamp_constructor(tool):
    """Authenticate a real allocated cast owner before any Wall record is created."""
    for parameter in (
        _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
        _optional("actual_spell_level", "integer", -1, minimum=-1, maximum=9, model_hidden=True),
    ):
        if parameter["name"] not in {p["name"] for p in tool["parameters"]}:
            tool["parameters"].append(parameter)
    marker = "spell_cast_id != ''"
    tool["effects"] = [
        e
        for e in tool["effects"]
        if not (e.get("condition") == marker and "actual finite Wall cast owner" in str(e))
    ]
    tool["effects"].insert(
        0,
        _when(
            marker,
            [
                _require(
                    "spell_cast_owner != null and _spell_cast_owner_id == _caster_state_id and "
                    "caster_state.last_spell_source_cast_id == spell_cast_id and "
                    "caster_state.last_spell_source_spell == 'prismatic_wall' and "
                    "actual_spell_level == slot_level",
                    "Use this actual finite Wall cast owner, named spell and paid slot.",
                ),
            ],
        ),
    )
    for effects in list(_effect_lists(tool["effects"])):
        effects[:] = [e for e in effects if e.get("condition") != "_wall_bind_cast_owner == true"]
        for i, effect in enumerate(list(effects)):
            record = effect.get("set", {}).get("value")
            field = effect.get("set", {}).get("field", "")
            if isinstance(record, dict) and record.get("spell_id") == "'prismatic_wall'":
                record.update(
                    source_caster_id=None,
                    ongoing_duration="spell_cast_id != ''",
                    dispel_ongoing=False,
                )
                effects.insert(
                    i + 1,
                    _when(
                        "_wall_bind_cast_owner == true",
                        [
                            _set(field + ".source_caster_id", "_spell_cast_owner_id"),
                        ],
                    ),
                )
    tool["effects"] = [
        e for e in tool["effects"] if e.get("set", {}).get("field") != "_wall_bind_cast_owner"
    ]
    tool["effects"].insert(
        1, _set("_wall_bind_cast_owner", "spell_cast_id != '' and spell_cast_owner != null")
    )
    feedback_field = "_prismatic_wall_cast_feedback"
    tool["effects"] = [
        e
        for e in tool["effects"]
        if e.get("set", {}).get("field") != feedback_field
        and not (
            e.get("condition") == "_prismatic_wall_occupied != true" and feedback_field in str(e)
        )
    ]
    summary = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_spell_summary_rows"
    )
    tool["effects"][summary:summary] = [
        _set(feedback_field, "'Occupied placement; spell ends with no effect.'"),
        _when(
            "_prismatic_wall_occupied != true",
            [
                _set(feedback_field, "_new_prismatic_wall_source_id"),
            ],
        ),
    ]
    for e in tool["effects"]:
        if e.get("set", {}).get("field") == "_spell_summary_rows":
            e["set"]["value"] = [{"label": "'Prismatic Wall'", "value": feedback_field}]
        if e.get("set", {}).get("field") == "_spell_detail_rows":
            e["set"]["value"] = [
                {"label": "'Prismatic Wall result'", "value": feedback_field},
                {"label": "'Occupied-space no effect'", "value": "_prismatic_wall_occupied"},
            ]
    result_field = "caster_state.last_prismatic_wall_cast"
    tool["effects"] = [
        e
        for e in tool["effects"]
        if not (
            e.get("condition") == "_prismatic_wall_occupied != true"
            and result_field + ".source_id" in str(e)
        )
    ]
    index = next(
        i for i, e in enumerate(tool["effects"]) if e.get("set", {}).get("field") == result_field
    )
    tool["effects"][index]["set"]["value"]["source_id"] = None
    tool["effects"].insert(
        index + 1,
        _when(
            "_prismatic_wall_occupied != true",
            [
                _set(result_field + ".source_id", "_new_prismatic_wall_source_id"),
            ],
        ),
    )


def _bind_dismissal(tool):
    """Count exact finite Walls before the existing shared dismissal admission check."""
    selection = "lasting_state.has_prismatic_walls == true"
    tool["effects"] = [
        e
        for e in tool["effects"]
        if not (e.get("condition") == selection and "_prismatic_wall_dismiss_ids" in str(e))
        and e.get("for_each", {}).get("collection") != "_prismatic_wall_dismiss_ids"
        and e.get("set", {}).get("field") != "_prismatic_wall_dismiss_ids"
    ]
    index = next(i for i, e in enumerate(tool["effects"]) if "require" in e)
    tool["effects"][index:index] = [
        _set("_prismatic_wall_dismiss_ids", []),
        _when(
            selection,
            [
                _call(
                    "collect_srd_prismatic_wall_owned_cast",
                    {
                        "caster_state": "{_caster_state_id}",
                        "cast_id": "{cast_id}",
                    },
                    ("_prismatic_wall_dismiss_ids",),
                ),
                _set("_dismiss_count", "_dismiss_count + len(_prismatic_wall_dismiss_ids)"),
            ],
        ),
    ]
    tool["effects"].append(
        _each(
            "_prismatic_wall_dismiss_ids",
            "_prismatic_wall_dismiss_id",
            [
                _call(
                    "retire_srd_prismatic_wall_owned_cast",
                    {
                        "caster_state": "{_caster_state_id}",
                        "cast_id": "{cast_id}",
                        "source_id": "{_prismatic_wall_dismiss_id}",
                    },
                ),
            ],
        )
    )


def _bind_recovery(tool):
    """Add the Wall's source-specific exception to the actual selected-source Recovery owner."""
    fields = {"_recovery_source_intents", "_recovery_source_owners"}
    found = set()
    for e in _walk(tool["effects"]):
        if e.get("set", {}).get("field") in fields:
            found.add(e["set"]["field"])
            choices = e["set"]["value"]
            if repr(SOURCE_SPELL) not in choices:
                choices.append(repr(SOURCE_SPELL))
    if found != fields:
        raise ValueError(
            "Prismatic Wall removal requires the genuine selected-source Recovery owner."
        )
    marker = "spell == 'dispel_magic' and recovery_choice == 'prismatic_wall'"
    tool["effects"] = [e for e in tool["effects"] if e.get("condition") != marker]
    tool["effects"].append(
        _when(
            marker,
            [
                _call(
                    "dispel_srd_prismatic_wall_owned_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "caster": "{_caster_id}",
                        "source_id": "{recovery_source_id}",
                        "point": "{recovery_position}",
                        "slot_level": "{slot_level}",
                        "casting_modifier": "{casting_modifier}",
                        "casting_ability": "{casting_ability}",
                    },
                    ("_adoption_dispel_check",),
                ),
                _set("_dispel_checks", ["_adoption_dispel_check"]),
                _set("_prismatic_dispel_detail", "'Automatic at this slot level'"),
                _when(
                    "_adoption_dispel_check.no_effect == true",
                    [
                        _set(
                            "_prismatic_dispel_detail",
                            "'Only the exposed Violet layer can be dispelled.'",
                        ),
                    ],
                ),
                _when(
                    "_adoption_dispel_check.roll != null",
                    [
                        _call(
                            "format_srd_information_projection",
                            {
                                "message": "d20 {_adoption_dispel_check.roll.results}; "
                                "selected {_adoption_dispel_check.roll.total}; "
                                "modifier {_adoption_dispel_check.roll.modifier}; "
                                "DC {_adoption_dispel_check.dc}; "
                                "effect ended: {_adoption_dispel_check.ended}",
                            },
                            ("_info_projection_message",),
                        ),
                        _set("_prismatic_dispel_detail", "_info_projection_message"),
                    ],
                ),
                _set(
                    "_spell_summary_rows",
                    [
                        {
                            "label": "'Selected Wall layer ended'",
                            "value": "_adoption_dispel_check.ended",
                        }
                    ],
                ),
                _set(
                    "_spell_detail_rows",
                    [
                        {"label": "'Wall source'", "value": "recovery_source_id"},
                        {"label": "'Actual Dispel check'", "value": "_prismatic_dispel_detail"},
                    ],
                ),
            ],
        )
    )


def bind_runtime(text):
    """Stamp the real constructor after Source admission, preserving shared Source ownership."""
    tools = _load_runtime(text)["tools"]
    constructor = deepcopy(tools[SOURCE_INSTALLER])
    _stamp_constructor(constructor)
    changed = {SOURCE_INSTALLER: constructor, **_tools()}
    for name, binder in (
        ("dismiss_srd_spell", _bind_dismissal),
        ("cast_srd_recovery", _bind_recovery),
    ):
        changed[name] = deepcopy(tools[name])
        binder(changed[name])
    return _emit(text, changed)
