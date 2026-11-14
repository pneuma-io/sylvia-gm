"""The ward's two Magic Mouth effects use genuine trigger owners and parent lifetime."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _emit
    from .srd_control_behaviors import _each, _push
    from .srd_guards_wards import _active, _exempt, _point, _read
    from .srd_spell_barriers import _effect_lists, _tool
    from .srd_spell_magic_zones import _when
    from .srd_spell_triggers import RECORD_EXPORTS, _word_conditions
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _optional
else:
    from srd_actor_spell_modifiers import _emit
    from srd_control_behaviors import _each, _push
    from srd_guards_wards import _active, _exempt, _point, _read
    from srd_spell_barriers import _effect_lists, _tool
    from srd_spell_magic_zones import _when
    from srd_spell_triggers import RECORD_EXPORTS, _word_conditions
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _optional

MOUTH_SCHEMA = {
    "type": "object",
    "properties": {
        "object_id": {"type": "string", "minLength": 1},
        "words": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string", "minLength": 1},
        "events": {
            "type": "array",
            "items": {"type": "string", "enum": ["visual", "audible"]},
        },
        "delivery_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
        "repeat": {"type": "boolean"},
    },
    "required": ["object_id", "words", "description", "events", "delivery_seconds", "repeat"],
    "additionalProperties": False,
}
GUARDED_EXPORTS = ("_trigger_guarded_mouth", "_guarded_mouth_available")


def _parent():
    return [
        *_read(),
        _require(_active(), "The genuine ward source is inactive."),
        _require(
            "_guards_source.groups.other == true and _guards_source.other == 'magic_mouth'",
            "This actual parent must retain its Magic Mouth Other group.",
        ),
    ]


def _tools():
    """Install source-bound objects without a second child cast, slot or material payment."""
    install = _tool(
        "Install one real object mouth under the exact ward group source.",
        [
            _entity("caster_state"),
            _parameter("source_id"),
            _parameter("mouth", "object"),
        ],
        [
            *_parent(),
            _require(
                "_guards_source.caster_state_id == _caster_state_id",
                "Only this exact parent caster installs its mouths.",
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{mouth.object_id}"},
                ("_info_scene_entity", "_info_position"),
            ),
            _require(
                "_info_scene_entity.metadata.is_object == true and "
                "_info_scene_entity.metadata.plane == _guards_source.plane",
                "Choose an actual same-plane object for the ward mouth.",
            ),
            _require(
                "(_info_scene_entity.metadata.carried_by == null or "
                "_info_scene_entity.metadata.carried_by == '' or "
                "_info_scene_entity.metadata.carried_by == _caster_state_id) and "
                "(_info_scene_entity.metadata.worn_by == null or "
                "_info_scene_entity.metadata.worn_by == '' or "
                "_info_scene_entity.metadata.worn_by == _caster_state_id)",
                "The mouth object cannot be worn or carried by another creature.",
            ),
            *_point("_info_position", "_guards_source.box", "_guarded_mouth_inside"),
            _require(
                "_guarded_mouth_inside == true",
                "Both real mouth locations must be inside the ward.",
            ),
            _call(
                "resolve_srd_illumination_point_sight",
                {
                    "observer_state": "{_caster_state_id}",
                    "point": "{_info_position}",
                    "perception_plane": "{_guards_source.plane}",
                },
                ("_illumination_point_can_see",),
            ),
            _require(
                "_illumination_point_can_see == true",
                "The actual caster must see its mouth object.",
            ),
            _set("_guarded_words", "mouth.words"),
            _require(
                "len(_guarded_words) >= 1 and len(_guarded_words) <= 25",
                "Each actual Magic Mouth message has one to 25 words.",
            ),
            _each("_guarded_words", "_mouth_word", _word_conditions()),
            _require(
                "mouth.delivery_seconds >= 1 and mouth.delivery_seconds <= 600",
                "Each spoken message lasts at most ten minutes.",
            ),
            _set("_guarded_events", "mouth.events"),
            _require(
                "len(_guarded_events) > 0 and len(_guarded_events) <= 2",
                "Choose actual visual or audible trigger conditions.",
            ),
            _require(
                "len(mouth.description) > 0 and len(mouth.description) <= 2000",
                "Describe the actual fictional trigger in at most 2000 characters.",
            ),
            _each(
                "_guarded_events",
                "_guarded_event",
                [
                    _require(
                        "_guarded_event == 'visual' or _guarded_event == 'audible'",
                        "Magic Mouth conditions must be visual or audible.",
                    ),
                ],
            ),
            _when("state.turn_effects == null", [_set("state.turn_effects", [])]),
            _when(
                "state.spell_trigger_sequence == null", [_set("state.spell_trigger_sequence", 0)]
            ),
            _set("state.spell_trigger_sequence", "state.spell_trigger_sequence + 1"),
            _set("_guarded_mouth_id", "state.spell_trigger_sequence"),
            _set(
                "_guarded_new",
                {
                    "kind": "'spell_trigger'",
                    "spell": "'guards_and_wards'",
                    "effect": "'magic_mouth'",
                    "caster": "_caster_state_id",
                    "target": "_caster_state_id",
                    "source_caster_id": "_caster_state_id",
                    "cast_id": "_guards_source.cast_id",
                    "instance": "_guarded_mouth_id",
                    "slot_level": "_guards_source.spell_level",
                    "spell_level": "_guards_source.spell_level",
                    "save_dc": 0,
                    "started_tick": "state.lasting_tick",
                    "expires_tick": None,
                    "concentration": False,
                    "anchor": "mouth.object_id",
                    "source": "mouth.object_id",
                    "anchor_kind": "'object'",
                    "origin": "_info_position",
                    "plane": "_guards_source.plane",
                    "extent": [0, 0, 0],
                    "description": "mouth.description",
                    "events": "mouth.events",
                    "exempt_creatures": [],
                    "creature_types": [],
                    "password": "''",
                    "approach_feet": 0,
                    "words": "mouth.words",
                    "delivery_seconds": "mouth.delivery_seconds",
                    "repeat": "mouth.repeat",
                    "guards_wards_source_id": "source_id",
                    "guards_wards_source_serial": "_guards_source.serial",
                },
            ),
            _set("_guarded_trigger_rows", "state.turn_effects"),
            _push("_guarded_trigger_rows", "_guarded_new"),
            _set("state.turn_effects", "_guarded_trigger_rows"),
            _set("state.has_spell_triggers", True),
            _set("_guarded_parent_mouths", "_guards_source.mouth_trigger_ids"),
            _push("_guarded_parent_mouths", "_guarded_mouth_id"),
            _set(
                "state.spell_guards_wards_sources.{source_id}.mouth_trigger_ids",
                "_guarded_parent_mouths",
            ),
        ],
    )
    # Validate visibility before the parent creates its own corridor fog. The
    # installer then consumes only the exact objects qualified by that cast.
    location_start = next(
        i
        for i, e in enumerate(install["effects"])
        if e.get("call") == "srd_information_scene_entity"
    )
    location_end = next(
        i
        for i, e in enumerate(install["effects"])
        if e.get("condition") == "lasting_state.turn_effects == null"
    )

    def bind_location(value):
        if isinstance(value, str):
            return value.replace("_guards_source.plane", "plane").replace(
                "_guards_source.box", "box"
            )
        if isinstance(value, list):
            return [bind_location(item) for item in value]
        if isinstance(value, dict):
            return {key: bind_location(item) for key, item in value.items()}
        return value

    qualify = _tool(
        "Qualify a real mouth object and message before its parent ward is installed.",
        [
            _entity("caster_state"),
            _parameter("mouth", "object"),
            _parameter("box", "object"),
            _parameter("plane"),
        ],
        bind_location(deepcopy(install["effects"][location_start:location_end]))
        + [_set("_guarded_mouth_qualified_object", "_info_scene_entity.id")],
    )
    sight_index = next(
        i
        for i, e in enumerate(install["effects"])
        if e.get("call") == "resolve_srd_illumination_point_sight"
    )
    del install["effects"][sight_index : sight_index + 2]
    install["effects"].insert(
        location_start,
        _require(
            "mouth.object_id in _guards_source.mouth_qualified_object_ids",
            "Install only an actual object seen and qualified by this parent cast.",
        ),
    )
    group_end = _tool(
        "Retire only the real mouth rows belonging to this exact ward group.",
        [
            _parameter("source_id"),
        ],
        [
            *_read(),
            _set("_guarded_trigger_rows", "state.turn_effects"),
            _each(
                "_guarded_trigger_rows",
                "_guarded_row",
                [
                    _when(
                        "_guarded_row.kind == 'spell_trigger' and "
                        "_guarded_row.guards_wards_source_id == source_id and "
                        "_guarded_row.guards_wards_source_serial == _guards_source.serial",
                        [
                            _call(
                                "retire_srd_spell_trigger",
                                {"trigger_id": "{_guarded_row.instance}"},
                            )
                        ],
                    ),
                ],
            ),
            _set("state.spell_guards_wards_sources.{source_id}.mouth_trigger_ids", []),
        ],
    )
    return {
        "qualify_srd_guarded_magic_mouth_location": qualify,
        "install_srd_guarded_magic_mouth": install,
        "end_srd_guarded_magic_mouth_group": group_end,
    }


def _record_qualification():
    """Parent suppression pauses availability; actual group ending retires the child."""
    return [
        _set("_trigger_guarded_mouth", False),
        _set("_guarded_mouth_available", False),
        _when(
            "record.guards_wards_source_id != null",
            [
                _set(
                    "_guarded_record_parent",
                    "state.spell_guards_wards_sources[record.guards_wards_source_id]",
                ),
                _set("_guarded_record_matches", False),
                _when(
                    "_guarded_record_parent != null and record.kind == 'spell_trigger' and "
                    "record.spell == 'guards_and_wards' and record.effect == 'magic_mouth'",
                    [
                        _set(
                            "_guarded_record_matches",
                            "record.guards_wards_source_serial == "
                            "_guarded_record_parent.serial and record.caster == "
                            "_guarded_record_parent.caster_state_id and record.instance in "
                            "_guarded_record_parent.mouth_trigger_ids",
                        ),
                    ],
                ),
                _set("_trigger_guarded_mouth", "_guarded_record_matches"),
                _when(
                    "_guarded_record_matches == true",
                    [
                        _set(
                            "_trigger_active",
                            "_trigger_active == true and "
                            "_guarded_record_parent.installed == true and "
                            "_guarded_record_parent.groups.other == true and "
                            "_guarded_record_parent.other == 'magic_mouth' and "
                            "(_guarded_record_parent.permanent == true or "
                            "state.lasting_tick < _guarded_record_parent.expires_tick)",
                        ),
                        _set(
                            "_guarded_mouth_available",
                            "_trigger_active == true and _guarded_record_parent.suppressed != true",
                        ),
                        _set("_trigger_retirement", "'ward mouth group ended'"),
                    ],
                    [_set("_trigger_active", False)],
                ),
            ],
        ),
    ]


def bind_runtime(text):
    """Compose actual parent producers and final trigger owners before reaction capture."""
    tools = _load_runtime(text)["tools"]
    changed = _tools()
    parent = deepcopy(tools["cast_srd_guards_wards"])
    choices = [
        _optional(
            "other_effect", "enum", "dancing_lights", enum_values=["dancing_lights", "magic_mouth"]
        ),
        _optional("mouths", "array", [], items=MOUTH_SCHEMA, max_items=2),
    ]
    for parameter in choices:
        if parameter["name"] not in {p["name"] for p in parent["parameters"]}:
            parent["parameters"].append(parameter)
    effects = parent["effects"]
    if not any(e.get("condition") == "other_effect == 'dancing_lights'" for e in effects):
        start = next(
            i
            for i, e in enumerate(effects)
            if e.get("require", {}).get("condition") == "len(lamp_positions) == 4"
        )
        end = next(
            i
            for i, e in enumerate(effects[start:], start)
            if e.get("for_each", {}).get("collection") == "lamp_positions"
        )
        effects[start : end + 1] = [
            _when("other_effect == 'dancing_lights'", effects[start : end + 1])
        ]
    for effect in effects:
        value = effect.get("set", {}).get("value")
        if isinstance(value, dict) and value.get("spell_id") == "'guards_and_wards'":
            value["other"] = "other_effect"
            value["mouth_trigger_ids"] = []
            value["mouth_qualified_object_ids"] = "_guarded_qualified_objects"
    effects[:] = [
        e
        for e in effects
        if e.get("set", {}).get("field") != "_guarded_qualified_objects"
        and "qualify_srd_guarded_magic_mouth_location" not in str(e)
    ]
    qualification_index = next(
        i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_guards_cast_day"
    )
    effects[qualification_index:qualification_index] = [
        _set("_guarded_qualified_objects", []),
        _when(
            "other_effect == 'magic_mouth'",
            [
                _require("len(mouths) == 2", "Choose exactly two actual mouth locations."),
                _each(
                    "mouths",
                    "_guarded_choice",
                    [
                        _require(
                            "_guarded_choice.object_id not in _guarded_qualified_objects",
                            "Choose two different actual mouth objects.",
                        ),
                        _call(
                            "qualify_srd_guarded_magic_mouth_location",
                            {
                                "caster_state": "{_caster_state_id}",
                                "mouth": "{_guarded_choice}",
                                "box": "{_guards_box}",
                                "plane": "{_guards_caster.metadata.plane}",
                            },
                            ("_guarded_mouth_qualified_object",),
                        ),
                        _push("_guarded_qualified_objects", "_guarded_mouth_qualified_object"),
                    ],
                ),
            ],
        ),
    ]
    effects[:] = [e for e in effects if "install_srd_guarded_magic_mouth" not in str(e)]
    index = next(
        i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_spell_summary_rows"
    )
    effects.insert(
        index,
        _when(
            "other_effect == 'magic_mouth'",
            [
                _require(
                    "len(mouths) == 2",
                    "The ward places Magic Mouth in exactly two actual locations.",
                ),
                _set("_guarded_objects", []),
                _each(
                    "mouths",
                    "_guarded_choice",
                    [
                        _require(
                            "_guarded_choice.object_id not in _guarded_objects",
                            "Choose two different actual mouth objects.",
                        ),
                        _push("_guarded_objects", "_guarded_choice.object_id"),
                        _call(
                            "install_srd_guarded_magic_mouth",
                            {
                                "caster_state": "{_caster_state_id}",
                                "source_id": "{_new_guards_wards_source_id}",
                                "mouth": "{_guarded_choice}",
                            },
                        ),
                    ],
                ),
            ],
        ),
    )
    changed["cast_srd_guards_wards"] = parent
    end = deepcopy(tools["end_srd_guards_wards_effect"])
    end["effects"] = [
        e for e in end["effects"] if "end_srd_guarded_magic_mouth_group" not in str(e)
    ]
    end["effects"].append(
        _when(
            "(group == 'other' or group == 'all') and _guards_source.mouth_trigger_ids != null",
            [
                _call("end_srd_guarded_magic_mouth_group", {"source_id": "{source_id}"}),
            ],
        )
    )
    changed["end_srd_guards_wards_effect"] = end
    record = deepcopy(tools["check_srd_spell_trigger_record"])
    # Our tail is replaced as one suffix, retaining all ordinary trigger qualifications.
    starts = [
        i
        for i, e in enumerate(record["effects"])
        if e.get("set", {}).get("field") == "_trigger_guarded_mouth"
    ]
    if starts:
        del record["effects"][starts[0] : starts[0] + 3]
    record["effects"] += _record_qualification()
    changed["check_srd_spell_trigger_record"] = record
    event = deepcopy(tools["resolve_srd_spell_trigger"])
    for group in list(_effect_lists(event["effects"])):
        for effect in group:
            if effect.get("call") == "check_srd_spell_trigger_record":
                effect.pop("exports", None)
                effect.setdefault("export_context", {}).update(
                    {key: key for key in RECORD_EXPORTS + GUARDED_EXPORTS}
                )
            if effect.get("condition") == "_trigger_record.spell == 'magic_mouth'":
                effect["condition"] += " or _trigger_guarded_mouth == true"
            if effect.get("condition") == "_trigger_active == true":
                effect["condition"] += (
                    " and (_trigger_guarded_mouth != true or _guarded_mouth_available == true)"
                )
        group[:] = [e for e in group if e.get("condition") != "_trigger_guarded_mouth == true"]
        for i, effect in enumerate(list(group)):
            if effect.get("set", {}).get("field") == "_trigger_matches" and (
                str(effect["set"].get("value", "")).startswith("fictional_match == true")
            ):
                group.insert(
                    i + 1,
                    _when(
                        "_trigger_guarded_mouth == true",
                        [
                            _set(
                                "_guards_source",
                                "state.spell_guards_wards_sources["
                                "_trigger_record.guards_wards_source_id]",
                            ),
                            _set(
                                "_trigger_matches",
                                "_trigger_matches == true and "
                                "_guarded_mouth_available == true and "
                                + _exempt("_event_actor_id")
                                + " != true",
                            ),
                        ],
                    ),
                )
                break
    changed["resolve_srd_spell_trigger"] = event
    cast = deepcopy(tools["cast_spell"])
    for parameter in choices:
        choice = deepcopy(parameter)
        choice["name"] = "guards_wards_" + choice["name"]
        if choice["name"] not in {p["name"] for p in cast["parameters"]}:
            cast["parameters"].append(choice)
    for group in _effect_lists(cast["effects"]):
        for effect in group:
            if effect.get("call") == "cast_srd_guards_wards":
                effect["params"].update(
                    other_effect="{guards_wards_other_effect}", mouths="{guards_wards_mouths}"
                )
    changed["cast_spell"] = cast
    return _emit(text, changed)
