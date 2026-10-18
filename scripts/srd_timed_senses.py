"""Source-defined senses, respiration and literal-language consumers for the SRD package."""

from copy import deepcopy

import yaml

try:
    from .srd_runtime_fragments import _ToolFragments
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _tool,
        _when,
    )
except ImportError:
    from srd_runtime_fragments import _ToolFragments
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _tool,
        _when,
    )


PUBLIC_TOOLS = {
    "resolve_srd_sight",
    "comprehend_srd_language",
    "set_srd_respiration",
    "dismiss_srd_senses_spell",
}
PROFILES = {
    "comprehend_languages": (1, "self", 0, False, 600, 1, 0, 0, False, False, False, True),
    "darkvision": (2, "touch", 0, True, 4800, 1, 150, 0, False, False, False, False),
    "see_invisibility": (2, "self", 0, False, 600, 1, 0, 0, True, True, False, False),
    "true_seeing": (6, "touch", 0, True, 600, 1, 0, 120, False, False, False, False),
    "water_breathing": (3, "distance", 30, True, 14400, 10, 0, 0, False, False, True, False),
}
PROFILE_FIELDS = (
    "base_level",
    "range_kind",
    "range_feet",
    "requires_willing",
    "duration_rounds",
    "base_targets",
    "darkvision_feet",
    "truesight_feet",
    "see_invisible",
    "see_ethereal",
    "water_breathing",
    "comprehend_languages",
)
SENSE_EXPORTS = (
    "_sense_darkvision",
    "_sense_truesight",
    "_sense_see_invisible",
    "_sense_see_ethereal",
    "_sense_water_breathing",
    "_sense_comprehension",
    "_sense_comprehension_until",
    "_sense_water_until",
)
SIGHT_EXPORTS = (
    "_sight_can_see",
    "_sight_light",
    "_sight_grayscale",
    "_sight_ghostly",
    "_sight_illusion_transparent",
    "_sight_illusion_save_success",
    "_sight_true_form",
    "_sight_perception_disadvantage",
    "_sight_outcome",
)


def validate_profiles(definitions):
    """Reject declarations whose fixed profile disagrees with the five pinned source entries."""
    for spell, definition in definitions.items():
        if definition.get("family") != "timed_senses":
            continue
        expected = dict(zip(PROFILE_FIELDS, PROFILES.get(spell, ()), strict=False))
        expected.update(family="timed_senses", casting_time="action", concentration=False)
        if definition != expected:
            raise SystemExit(f"Timed-senses source profile differs: {spell}")


def _schema():
    properties = {
        "casting_time": {"type": "string", "enum": ["action"]},
        "base_level": {"type": "integer", "minimum": 1, "maximum": 6},
        "range_kind": {"type": "string", "enum": ["touch", "self", "distance"]},
        "range_feet": {"type": "integer", "minimum": 0, "maximum": 30},
        "requires_willing": {"type": "boolean"},
        "duration_rounds": {"type": "integer", "minimum": 1, "maximum": 14400},
        "base_targets": {"type": "integer", "minimum": 1, "maximum": 10},
        "concentration": {"type": "boolean"},
        "darkvision_feet": {"type": "integer", "minimum": 0, "maximum": 150},
        "truesight_feet": {"type": "integer", "minimum": 0, "maximum": 120},
        **{field: {"type": "boolean"} for field in PROFILE_FIELDS[-4:]},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _push(collection, value):
    return {"list_push": {"list": collection, "value": value}}


def _loop(collection, item, effects):
    return {"for_each": {"collection": collection, "as": item, "do": effects}}


def _lookup(spell, local):
    return {
        "table_lookup": {"table": "definitions.spells", "key": spell, "required": True},
        "store_as": local,
    }


def _optional(name, kind="string", default=None, **extra):
    return {"name": name, "type": kind, "required": False, "default": default, **extra}


def _public(tool, category, title, fields):
    tool["metadata"] = {
        "category": category,
        "costs_action": False,
        "result_card": {
            "version": 1,
            "footprint": "compact",
            "title": title,
            "summary": [
                {"label": label, "value": {"path": "context." + field}} for label, field in fields
            ],
        },
    }
    return tool


def _sense_tools():
    effects = [
        _set("_sense_darkvision", 0),
        _set("_sense_truesight", 0),
        _set("_sense_see_invisible", False),
        _set("_sense_see_ethereal", False),
        _set("_sense_water_breathing", False),
        _set("_sense_comprehension", False),
        _set("_sense_comprehension_until", 0),
        _set("_sense_water_until", 0),
        _when(
            "target_state.darkvision_feet != null",
            [_set("_sense_darkvision", "target_state.darkvision_feet")],
        ),
        _when(
            "target_state.truesight_feet != null",
            [_set("_sense_truesight", "target_state.truesight_feet")],
        ),
        _set("_sense_see_invisible", "target_state.see_invisible == true"),
        _set("_sense_see_ethereal", "target_state.see_ethereal == true"),
        _set("_sense_water_breathing", "target_state.water_breathing == true"),
        _set("_sense_records", []),
        _when("state.turn_effects != null", [_set("_sense_records", "state.turn_effects")]),
        _loop(
            "_sense_records",
            "_sense_record",
            [
                _when(
                    "_sense_record.kind == 'timed_senses' and "
                    "_sense_record.target == _target_state_id and "
                    "state.lasting_tick + 0.000000001 < _sense_record.expires_tick",
                    [
                        _lookup("_sense_record.spell", "_sense_profile"),
                        _set(
                            "_sense_darkvision",
                            "max(_sense_darkvision, _sense_profile.darkvision_feet)",
                        ),
                        _set(
                            "_sense_truesight",
                            "max(_sense_truesight, _sense_profile.truesight_feet)",
                        ),
                        _set(
                            "_sense_see_invisible",
                            "_sense_see_invisible == true or _sense_profile.see_invisible == true",
                        ),
                        _set(
                            "_sense_see_ethereal",
                            "_sense_see_ethereal == true or _sense_profile.see_ethereal == true",
                        ),
                        _set(
                            "_sense_water_breathing",
                            "_sense_water_breathing == true or "
                            "_sense_profile.water_breathing == true",
                        ),
                        _set(
                            "_sense_comprehension",
                            "_sense_comprehension == true or _sense_profile.comprehend_langua"
                            "ges == true",
                        ),
                        _when(
                            "_sense_profile.comprehend_languages == true",
                            [
                                _set(
                                    "_sense_comprehension_until",
                                    "max(_sense_comprehension_until, _sense_record.expires_tick)",
                                )
                            ],
                        ),
                        _when(
                            "_sense_profile.water_breathing == true",
                            [
                                _set(
                                    "_sense_water_until",
                                    "max(_sense_water_until, _sense_record.expires_tick)",
                                )
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]
    return {
        "resolve_srd_timed_senses": _tool(
            "Derive active timed and native senses without overwriting native creature features.",
            [_entity("target_state")],
            effects,
        )
    }


def _casting_tools():
    install = _tool(
        "Persist one source-defined lasting sense effect after full-set validation.",
        [_entity("target_state"), _entity("caster_state"), _parameter("spell")],
        [
            _lookup("spell", "_sense_install_profile"),
            _set("_sense_retained", []),
            _set("_sense_prior", []),
            _when("state.turn_effects != null", [_set("_sense_prior", "state.turn_effects")]),
            _loop(
                "_sense_prior",
                "_sense_old",
                [
                    _when(
                        "not (_sense_old.kind == 'timed_senses' and _sense_old.spell == spell "
                        "and _sense_old.caster == _caster_state_id and _sense_old.target "
                        "== _target_state_id)",
                        [_push("_sense_retained", "_sense_old")],
                    ),
                ],
            ),
            _require(
                "len(_sense_retained) < 128", "Resolve existing lasting effects before adding more."
            ),
            _push(
                "_sense_retained",
                {
                    "spell": "spell",
                    "label": "spell",
                    "caster": "_caster_state_id",
                    "target": "_target_state_id",
                    "kind": "'timed_senses'",
                    "owner": "'__time_only__'",
                    "phase": "'time'",
                    "turn": 1000000000,
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + "
                    "_sense_install_profile.duration_rounds * 2",
                    "concentration": False,
                    "magnitude": 0,
                    "dice_count": 0,
                    "damage_die": "''",
                    "damage_type": "''",
                },
            ),
            _set("state.turn_effects", "_sense_retained"),
            _when(
                "target_state.respiration_medium != null",
                [
                    _call("refresh_srd_respiration", {"target_state": "{_target_state_id}"}),
                ],
            ),
        ],
    )
    cast = _tool(
        "Validate and apply a source-defined senses spell to its complete willing target set.",
        [
            _parameter("spell"),
            _entity("caster_state"),
            _entity("target_state"),
            _optional("targets", "array", [], items={"type": "string"}),
            _parameter("target_willing", "boolean"),
            _parameter("all_targets_willing", "boolean"),
            _parameter("caster_can_touch_target", "boolean"),
            _parameter("caster_can_touch_all_targets", "boolean"),
            _parameter("target_distance", "integer", minimum=0),
            _parameter("targets_max_distance", "integer", minimum=0),
        ],
        [
            _lookup("spell", "_sense_cast_profile"),
            _set("_sense_targets", "targets"),
            _when(
                "_sense_cast_profile.range_kind == 'self'",
                [
                    _set("_sense_targets", ["_caster_state_id"]),
                ],
            ),
            _when(
                "_sense_cast_profile.range_kind != 'self'",
                [
                    _when(
                        "len(_sense_targets) == 0",
                        [
                            _require(
                                "_sense_cast_profile.requires_willing != true or target_willing "
                                "== true",
                                "The target creature must be willing.",
                            ),
                            _require(
                                "_sense_cast_profile.range_kind != 'touch' or "
                                "caster_can_touch_target == true",
                                "The caster must touch the target creature.",
                            ),
                            _require(
                                "_sense_cast_profile.range_kind != 'distance' or target_distance "
                                "<= _sense_cast_profile.range_feet",
                                "The target is outside the spell's range.",
                            ),
                            _set("_sense_targets", ["_target_state_id"]),
                        ],
                    ),
                    _when(
                        "len(targets) > 0",
                        [
                            _require(
                                "all_targets_willing == true",
                                "Every selected creature must be willing.",
                            ),
                            _require(
                                "_sense_cast_profile.range_kind != 'touch' or "
                                "caster_can_touch_all_targets == true",
                                "Every selected creature must be within touch.",
                            ),
                            _require(
                                "_sense_cast_profile.range_kind != 'distance' or "
                                "targets_max_distance <= _sense_cast_profile.range_feet",
                                "Every selected creature must be within the spell's range.",
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "len(_sense_targets) >= 1 and len(_sense_targets) <= "
                "_sense_cast_profile.base_targets",
                "The spell's target limit is exceeded.",
            ),
            _set("_sense_distinct_targets", []),
            _loop(
                "_sense_targets",
                "_sense_target",
                [
                    _require(
                        "_sense_target not in _sense_distinct_targets",
                        "A creature cannot be selected twice.",
                    ),
                    _push("_sense_distinct_targets", "_sense_target"),
                    _call("validate_srd_senses_target", {"target_state": "{_sense_target}"}),
                ],
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _loop(
                "_sense_targets",
                "_sense_target",
                [
                    _call(
                        "install_srd_timed_senses",
                        {
                            "target_state": "{_sense_target}",
                            "caster_state": "{_caster_state_id}",
                            "spell": "{spell}",
                        },
                    ),
                    _call(
                        "record_srd_spell_target_result",
                        {
                            "target": "{_sense_target}",
                            "brief": "{spell} active",
                            "detail": "Lasts {_sense_cast_profile.duration_rounds} rounds. Later "
                            "sensory, language or respiration operations resolve its fixed "
                            "benefits.",
                        },
                        ["_spell_target_summary", "_spell_target_detail"],
                    ),
                    _push("_spell_summary_rows", "_spell_target_summary"),
                    _push("_spell_detail_rows", "_spell_target_detail"),
                ],
            ),
        ],
    )
    validate = _tool(
        "Require an admitted living creature for a senses spell.",
        [_entity("target_state")],
        [
            _require(
                "target_state.creature_kind != null and target_state.conditions "
                "!= null and target_state.is_alive == true",
                "Senses spells require an admitted living creature.",
            )
        ],
    )
    return {
        "install_srd_timed_senses": install,
        "cast_spell_timed_senses": cast,
        "validate_srd_senses_target": validate,
    }


def _sight_tool():
    parameters = [
        _entity("observer_state"),
        _optional("subject_state", resolve="entity"),
        _parameter("distance", "integer", minimum=0, maximum=100000),
        _parameter(
            "lighting", "enum", enum_values=["bright", "dim", "darkness", "magical_darkness"]
        ),
        _parameter("obstruction", "enum", enum_values=["clear", "solid", "heavy_obscurement"]),
        _optional("subject_invisible", "boolean", False),
        _optional("subject_ethereal", "boolean", False),
        _optional("visual_illusion", "boolean", False),
        _optional("magically_transformed", "boolean", False),
        _optional(
            "test", "enum", "none", enum_values=["none", "perception", "visual_illusion_save"]
        ),
        _optional("observer", resolve="participant"),
        _optional(
            "ability",
            "enum",
            "wisdom",
            enum_values=[
                "strength",
                "dexterity",
                "constitution",
                "intelligence",
                "wisdom",
                "charisma",
            ],
        ),
        _optional("npc_modifier", "integer"),
        _optional("target_number", "integer", 10, minimum=0, maximum=1000),
        _optional("circumstantial_modifier", "integer", 0),
        _optional("advantage", "boolean", False),
        _optional("disadvantage", "boolean", False),
    ]
    effects = [
        _call("resolve_srd_timed_senses", {"target_state": "{_observer_state_id}"}, SENSE_EXPORTS),
        _set("_sight_truesight", "_sense_truesight > 0 and distance <= _sense_truesight"),
        _set("_sight_darkvision", "_sense_darkvision > 0 and distance <= _sense_darkvision"),
        _set("_sight_invisible", "subject_invisible"),
        _when(
            "subject_state != null and subject_state.conditions != null and "
            "'invisible' in subject_state.conditions",
            [_set("_sight_invisible", True)],
        ),
        _set("_sight_light", "lighting"),
        _set("_sight_grayscale", False),
        _when(
            "_sight_darkvision == true and lighting == 'dim'", [_set("_sight_light", "'bright'")]
        ),
        _when(
            "_sight_darkvision == true and lighting == 'darkness'",
            [_set("_sight_light", "'dim'"), _set("_sight_grayscale", True)],
        ),
        _when(
            "_sight_truesight == true",
            [_set("_sight_light", "'bright'"), _set("_sight_grayscale", False)],
        ),
        _set(
            "_sight_can_see",
            "obstruction == 'clear' and _sight_light != 'darkness' and "
            "_sight_light != 'magical_darkness' and not ('blinded' in "
            "observer_state.conditions) and "
            "(_sight_invisible != true or _sense_see_invisible == true or "
            "_sight_truesight == true) and "
            "(subject_ethereal != true or _sense_see_ethereal == true or _sight_truesight == true)",
        ),
        _set(
            "_sight_ghostly",
            "_sight_can_see == true and subject_ethereal == true and _sense_see_ethereal == true",
        ),
        _set("_sight_perception_disadvantage", "_sight_can_see == true and _sight_light == 'dim'"),
        _set(
            "_sight_illusion_transparent",
            "_sight_can_see == true and _sight_truesight == true and visual_illusion == true",
        ),
        _set(
            "_sight_illusion_save_success",
            "_sight_truesight == true and visual_illusion == true and "
            "obstruction == 'clear' and not ('blinded' in observer_state.conditions)",
        ),
        _set(
            "_sight_true_form",
            "_sight_can_see == true and _sight_truesight == true and magically_transformed == true",
        ),
        _set("_sight_outcome", "'no_test'"),
        _when(
            "test != 'none'",
            [
                _require(
                    "test != 'visual_illusion_save' or visual_illusion == true",
                    "Identify a visual illusion before resolving its saving throw.",
                ),
                _when(
                    "test == 'perception' and _sight_can_see != true",
                    [_set("_sight_outcome", "'failure'")],
                ),
                _when(
                    "test == 'visual_illusion_save' and _sight_illusion_save_success == true",
                    [_set("_sight_outcome", "'success'")],
                ),
                _when(
                    "_sight_outcome == 'no_test'",
                    [
                        _require(
                            "observer != null or npc_modifier != null",
                            "A sensory test needs a validated character or an NPC modifier.",
                        ),
                        _require(
                            "observer_state.creature_kind != 'character' or observer != null",
                            "A character's sensory test needs its validated Character File.",
                        ),
                        _set("_sight_test_kind", "'ability_check'"),
                        _set("_sight_test_skill", "'perception'"),
                        _set("_sight_test_ability", "'wisdom'"),
                        _when(
                            "test == 'visual_illusion_save'",
                            [
                                _set("_sight_test_kind", "'saving_throw'"),
                                _set("_sight_test_skill", None),
                                _set("_sight_test_ability", "ability"),
                            ],
                        ),
                        _set("_sight_proficiency", "'none'"),
                        _when(
                            "observer != null",
                            [
                                _require(
                                    "_observer_id == _observer_state_id",
                                    "The sensory Character File must match its creature.",
                                ),
                                _when(
                                    "test == 'perception' and observer.data.skill_proficiencies != "
                                    "null and 'perception' in observer.data.skill_proficiencies",
                                    [_set("_sight_proficiency", "'normal'")],
                                ),
                                _when(
                                    "test == 'visual_illusion_save' and "
                                    "observer.data.saving_throw_proficiencies != null "
                                    "and ability in "
                                    "observer.data.saving_throw_proficiencies",
                                    [_set("_sight_proficiency", "'normal'")],
                                ),
                            ],
                        ),
                        _call(
                            "d20_test",
                            {
                                "actor": "{_observer_id}",
                                "actor_state": "{_observer_state_id}",
                                "kind": "{_sight_test_kind}",
                                "ability": "{_sight_test_ability}",
                                "skill": "{_sight_test_skill}",
                                "ability_modifier": "{npc_modifier}",
                                "proficiency_bonus": 0,
                                "proficiency_factor": "{_sight_proficiency}",
                                "target_number": "{target_number}",
                                "circumstantial_modifier": "{circumstantial_modifier}",
                                "advantage": "{advantage}",
                                "disadvantage": "{_sight_test_disadvantage}",
                            },
                            [
                                "_outcome",
                                "_d20_test",
                                "_effective_disadvantage",
                                "_combined_modifier",
                            ],
                        ),
                        _set("_sight_outcome", "_outcome"),
                    ],
                ),
            ],
        ),
    ]
    # The D20 owner derives character modifiers; scene facts contribute lighting.
    effects.insert(
        -1,
        _set(
            "_sight_test_disadvantage",
            "disadvantage == true or "
            "(test == 'perception' and _sight_perception_disadvantage == true)",
        ),
    )
    return _public(
        _tool(
            "Resolve sight from declared scene facts and saved native/spell "
            "senses. For objects omit subject_state. "
            "Use a test only for a meaningful uncertain Perception check or "
            "a visual-illusion saving throw; "
            "the GM supplies fictional appearance and actual illusion content.",
            parameters,
            effects,
        ),
        "exploration",
        "Sight",
        [
            ("Can see", "_sight_can_see"),
            ("Light seen", "_sight_light"),
            ("Colors are gray", "_sight_grayscale"),
            ("Ethereal and ghostly", "_sight_ghostly"),
            ("Illusion transparent", "_sight_illusion_transparent"),
            ("Visual illusion save succeeds", "_sight_illusion_save_success"),
            ("True form discerned", "_sight_true_form"),
            ("Test outcome", "_sight_outcome"),
        ],
    )


def _language_tool():
    return _public(
        _tool(
            "Apply Comprehend Languages to a literal heard, signed or written language. "
            "The GM supplies the literal meaning; this operation never "
            "requests or fabricates a translation. "
            "Written reading needs sight and continuous touch for one minute "
            "per page. Start a saved surface interval, "
            "then finish after actual time passes; cancel it when "
            "interrupted. Outside combat, read composes that interval "
            "with elapsed time. Reading uses no Magic action or concentration.",
            [
                _entity("actor_state"),
                _parameter("channel", "enum", enum_values=["heard", "signed", "written"]),
                _parameter("perceived", "boolean"),
                _optional("touching_surface", "boolean", False),
                _optional("pages", "integer", 1, minimum=1, maximum=60),
                _optional(
                    "reading_phase",
                    "enum",
                    "read",
                    enum_values=["start", "finish", "cancel", "read"],
                ),
                _optional("surface_id", "string", "current_surface"),
                _optional("uninterrupted", "boolean", True),
                _optional("symbols_or_secret_message", "boolean", False),
            ],
            [
                _set("_language_literal_understood", False),
                _set("_language_read_rounds", 0),
                _when(
                    "channel == 'written' and reading_phase == 'cancel'",
                    [
                        _set("actor_state.language_reader", None),
                    ],
                ),
                _when(
                    "channel != 'written' or reading_phase != 'cancel'",
                    [
                        _call(
                            "resolve_srd_timed_senses",
                            {"target_state": "{_actor_state_id}"},
                            SENSE_EXPORTS,
                        ),
                        _require(
                            "_sense_comprehension == true",
                            "No active Comprehend Languages effect applies.",
                        ),
                        _require(
                            "perceived == true", "The language must actually be heard or seen."
                        ),
                        _require(
                            "channel != 'heard' or not ('deafened' in actor_state.conditions)",
                            "A Deafened creature cannot hear this language.",
                        ),
                        _require(
                            "channel == 'heard' or not ('blinded' in actor_state.conditions)",
                            "A Blinded creature cannot see signed or written language.",
                        ),
                        _require(
                            "symbols_or_secret_message != true",
                            "Comprehend Languages does not decode symbols or secret messages.",
                        ),
                        _when("channel != 'written'", [_set("_language_literal_understood", True)]),
                        _when(
                            "channel == 'written'",
                            [
                                _require(
                                    "touching_surface == true",
                                    "The reader must touch the written surface.",
                                ),
                                _require(
                                    "len(surface_id) >= 1 and len(surface_id) <= 200",
                                    "Identify the written surface consistently.",
                                ),
                                _set("_language_read_rounds", "pages * 10"),
                                _when(
                                    "reading_phase == 'start' or reading_phase == 'read'",
                                    [
                                        _require(
                                            "state.lasting_tick + _language_read_rounds * 2 <= "
                                            "_sense_comprehension_until",
                                            "The spell expires before these pages can be read.",
                                        ),
                                        _set(
                                            "actor_state.language_reader",
                                            {
                                                "surface": "surface_id",
                                                "pages": "pages",
                                                "started_tick": "state.lasting_tick",
                                                "due_tick": "state.lasting_tick + "
                                                "_language_read_rounds * 2",
                                            },
                                        ),
                                    ],
                                ),
                                _when(
                                    "reading_phase == 'read'",
                                    [
                                        _require(
                                            "combat.encounter_active != true",
                                            "During combat, start reading then finish "
                                            "after the actual clock "
                                            "advances.",
                                        ),
                                        _call(
                                            "advance_srd_lasting_time",
                                            {"elapsed_rounds": "{_language_read_rounds}"},
                                        ),
                                    ],
                                ),
                                _when(
                                    "reading_phase == 'finish' or reading_phase == 'read'",
                                    [
                                        _require(
                                            "actor_state.language_reader != null",
                                            "No uninterrupted written-reading interval is active.",
                                        ),
                                        _require(
                                            "actor_state.language_reader.surface == surface_id and "
                                            "actor_state.language_reader.pages == pages",
                                            "Finish the same written surface "
                                            "and page count that was started.",
                                        ),
                                        _require(
                                            "uninterrupted == true",
                                            "An interrupted reading interval "
                                            "must be cancelled and restarted.",
                                        ),
                                        _require(
                                            "state.lasting_tick + 0.000000001 >= "
                                            "actor_state.language_reader.due_tick",
                                            "One minute per page has not elapsed.",
                                        ),
                                        _set("_language_literal_understood", True),
                                        _set("actor_state.language_reader", None),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "exploration",
        "Literal language",
        [
            ("Literal meaning understood", "_language_literal_understood"),
            ("Reading rounds", "_language_read_rounds"),
        ],
    )


def _respiration_tools():
    refresh = _tool(
        "Consume elapsed breath and apply source-owned suffocation "
        "Exhaustion at real time or turn boundaries.",
        [
            _entity("target_state"),
            _optional("end_turn", "boolean", False),
            _optional("free_time", "boolean", False),
        ],
        [
            _call(
                "resolve_srd_timed_senses", {"target_state": "{_target_state_id}"}, SENSE_EXPORTS
            ),
            _set("_respiration_now", 0),
            _when("state.lasting_tick != null", [_set("_respiration_now", "state.lasting_tick")]),
            _set(
                "_respiration_elapsed", "max(0, _respiration_now - target_state.respiration_tick)"
            ),
            _set(
                "_respiration_water_until",
                "max(target_state.respiration_water_until, _sense_water_until)",
            ),
            _set(
                "_respiration_can_breathe",
                "target_state.respiration_choking != true and "
                "((target_state.respiration_medium == 'air' and "
                "target_state.respiration_native_air == true) or "
                "(target_state.respiration_medium == 'water' and "
                "(target_state.respiration_native_water == true or "
                "_sense_water_breathing == true)))",
            ),
            _set("_respiration_airless_ticks", "_respiration_elapsed"),
            _when(
                "target_state.respiration_choking != true",
                [
                    _when(
                        "(target_state.respiration_medium == 'air' and "
                        "target_state.respiration_native_air == true) or "
                        "(target_state.respiration_medium == 'water' and "
                        "target_state.respiration_native_water == true)",
                        [_set("_respiration_airless_ticks", 0)],
                    ),
                    _when(
                        "target_state.respiration_medium == 'water' and "
                        "target_state.respiration_native_water != true",
                        [
                            _set(
                                "_respiration_airless_ticks",
                                "max(0, _respiration_elapsed - max(0, "
                                "min(_respiration_now, _respiration_water_until) - "
                                "target_state.respiration_tick))",
                            ),
                            _when(
                                "_respiration_water_until > target_state.respiration_tick",
                                [
                                    _set(
                                        "target_state.respiration_breath_ticks",
                                        "target_state.respiration_capacity_ticks",
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("_respiration_before_breath", "target_state.respiration_breath_ticks"),
            _when(
                "target_state.respiration_choking == true",
                [
                    _set("target_state.respiration_breath_ticks", 0),
                    _set("_respiration_before_breath", 0),
                ],
            ),
            _set(
                "target_state.respiration_breath_ticks",
                "max(0, target_state.respiration_breath_ticks - _respiration_airless_ticks)",
            ),
            _set(
                "_respiration_suffocating_ticks",
                "max(0, _respiration_airless_ticks - _respiration_before_breath)",
            ),
            _set("_respiration_exhaustion_levels", 0),
            _when(
                "free_time == true and _respiration_can_breathe != true",
                [
                    _set(
                        "_respiration_suffocation_total",
                        "target_state.respiration_suffocation_ticks + "
                        "_respiration_suffocating_ticks",
                    ),
                    _set("_respiration_exhaustion_levels", "_respiration_suffocation_total // 2"),
                    _set(
                        "target_state.respiration_suffocation_ticks",
                        "_respiration_suffocation_total % 2",
                    ),
                ],
            ),
            _when(
                "end_turn == true and _respiration_can_breathe != true and "
                "target_state.respiration_breath_ticks <= 0",
                [_set("_respiration_exhaustion_levels", 1)],
            ),
            _when(
                "_respiration_exhaustion_levels > 0 and target_state.is_alive == "
                "true and target_state.exhaustion_level < 6",
                [
                    _call(
                        "gain_srd_exhaustion",
                        {
                            "target": "{_target_state_id}",
                            "levels": "{_respiration_exhaustion_levels}",
                            "cause": "other",
                        },
                        ["_exhaustion_gained"],
                    ),
                    _set(
                        "target_state.respiration_exhaustion",
                        "target_state.respiration_exhaustion + _exhaustion_gained",
                    ),
                ],
            ),
            _when(
                "_respiration_can_breathe == true",
                [
                    _when(
                        "target_state.respiration_exhaustion > 0 and "
                        "target_state.exhaustion_level > 0 and target_state.is_alive == "
                        "true",
                        [
                            _call(
                                "reduce_srd_exhaustion",
                                {
                                    "target": "{_target_state_id}",
                                    "levels": "{target_state.respiration_exhaustion}",
                                },
                            ),
                        ],
                    ),
                    _set("target_state.respiration_exhaustion", 0),
                    _set("target_state.respiration_suffocation_ticks", 0),
                    _set(
                        "target_state.respiration_breath_ticks",
                        "target_state.respiration_capacity_ticks",
                    ),
                ],
            ),
            _set("target_state.respiration_tick", "_respiration_now"),
            _set("target_state.respiration_water_until", "_sense_water_until"),
            _set("target_state.respiration_can_breathe", "_respiration_can_breathe"),
        ],
    )
    set_environment = _public(
        _tool(
            "Declare a creature's actual respiration medium and choking. The "
            "engine derives breath capacity "
            "from its saved Constitution or validated Character File, "
            "retains native air/water modes, "
            "and consumes Water Breathing at later time and turn boundaries.",
            [
                _entity("target_state"),
                _optional("target_character", resolve="participant"),
                _parameter("medium", "enum", enum_values=["air", "water", "no_air"]),
                _optional("choking", "boolean", False),
            ],
            [
                _require(
                    "target_state.is_alive == true and target_state.conditions != "
                    "null and target_state.exhaustion_level != null",
                    "Respiration requires an admitted living creature with Exhaustion state.",
                ),
                _set("_respiration_constitution", "target_state.constitution_score"),
                _require(
                    "target_state.creature_kind != 'character' or target_character != null",
                    "A character's breath capacity needs its validated Character File.",
                ),
                _when(
                    "target_state.equipment_abilities != null and "
                    "target_state.equipment_abilities.constitution != null",
                    [
                        _set(
                            "_respiration_constitution",
                            "target_state.equipment_abilities.constitution",
                        )
                    ],
                ),
                _when(
                    "target_character != null",
                    [
                        _require(
                            "target_character.validation_status == 'validated' and "
                            "target_character.package_id == 'sylvia.srd-lite.bg3' and "
                            "_target_character_id == _target_state_id",
                            "The respiration Character File must belong to the selected creature.",
                        ),
                        _set(
                            "_respiration_constitution",
                            "target_character.data.abilities.constitution",
                        ),
                    ],
                ),
                _require(
                    "_respiration_constitution != null and _respiration_constitution "
                    ">= 1 and _respiration_constitution <= 30",
                    "Respiration needs saved NPC Constitution or the creature's "
                    "validated Character File.",
                ),
                _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
                _when(
                    "target_state.respiration_medium != null",
                    [
                        _call("refresh_srd_respiration", {"target_state": "{_target_state_id}"}),
                    ],
                ),
                _when(
                    "target_state.respiration_medium == null",
                    [
                        _set(
                            "target_state.respiration_capacity_ticks",
                            "max(5, (1 + (_respiration_constitution - 10) // 2) * 10) * 2",
                        ),
                        _set(
                            "target_state.respiration_breath_ticks",
                            "target_state.respiration_capacity_ticks",
                        ),
                        _set("target_state.respiration_exhaustion", 0),
                        _set("target_state.respiration_suffocation_ticks", 0),
                        _set("target_state.respiration_water_until", 0),
                        _set("target_state.respiration_tick", "state.lasting_tick"),
                        _set(
                            "target_state.respiration_native_air",
                            "target_state.breathes_air != false",
                        ),
                        _set(
                            "target_state.respiration_native_water",
                            "target_state.breathes_water == true",
                        ),
                    ],
                ),
                _set("target_state.respiration_medium", "medium"),
                _set("target_state.respiration_choking", "choking"),
                _call(
                    "refresh_srd_respiration",
                    {"target_state": "{_target_state_id}"},
                    ["_respiration_can_breathe"],
                ),
                _set("_respiration_registered_targets", []),
                _when(
                    "state.respiration_targets != null",
                    [_set("_respiration_registered_targets", "state.respiration_targets")],
                ),
                _when(
                    "_target_state_id not in _respiration_registered_targets",
                    [
                        _require(
                            "len(_respiration_registered_targets) < 128",
                            "The active respiration target limit is reached.",
                        ),
                        _push("_respiration_registered_targets", "_target_state_id"),
                    ],
                ),
                _set("state.respiration_targets", "_respiration_registered_targets"),
            ],
        ),
        "exploration",
        "Respiration",
        [
            ("Can breathe", "_respiration_can_breathe"),
            ("Breath ticks left", "target_state.respiration_breath_ticks"),
            ("Suffocation Exhaustion", "target_state.respiration_exhaustion"),
        ],
    )
    refresh_all = _tool(
        "Refresh only tracked respiration creatures at a free-time clock boundary.",
        [],
        [
            _set("_respiration_targets", []),
            _when(
                "state.respiration_targets != null",
                [_set("_respiration_targets", "state.respiration_targets")],
            ),
            _loop(
                "_respiration_targets",
                "_respiration_target",
                [
                    _call(
                        "refresh_srd_respiration",
                        {
                            "target_state": "{_respiration_target}",
                            "free_time": "{_respiration_free_time}",
                        },
                    ),
                ],
            ),
        ],
    )
    refresh_all["effects"].insert(
        0, _set("_respiration_free_time", "combat.encounter_active != true")
    )
    return {
        "refresh_srd_respiration": refresh,
        "set_srd_respiration": set_environment,
        "refresh_all_srd_respiration": refresh_all,
    }


def _replace_tools(text, changed, width=105):
    """Replace only owned tool fragments, preserving generated markers and public casting alias."""
    if not changed:
        return text
    fragments = _ToolFragments(text)
    for name, tool in changed.items():
        rendered = yaml.dump({name: tool}, Dumper=_RuntimeDumper, sort_keys=False, width=width)
        rendered = "".join(
            "  " + line if line.strip() else line for line in rendered.splitlines(True)
        )
        if name == "d20_test" and fragments.has_d20_alias:
            rendered = rendered.replace(
                "      result_card:\n", "      result_card: &d20_result_card\n", 1
            )
        if name == "cast_spell":
            start = rendered.index("    - name: spell\n")
            end = rendered.index("    - name:", start + 1)
            parameter = rendered[start:end]
            a = parameter.index("      enum_values:\n") + len("      enum_values:\n")
            b = parameter.find("      required:", a)
            enums = next(p for p in tool["parameters"] if p["name"] == "spell")["enum_values"]
            parameter = (
                parameter[:a]
                + "        # BEGIN GENERATED SPELL SELECTOR\n"
                + "".join(f"          - {spell}\n" for spell in enums)
                + "        # END GENERATED SPELL SELECTOR\n"
                + parameter[b:]
            )
            rendered = rendered[:start] + parameter + rendered[end:]
            rendered = rendered.replace("  cast_spell:\n", "  cast_spell: &srd_cast_spell\n", 1)
        fragments.replace(name, rendered, insertion_separator="\n")
    return fragments.render()


def _dismissal_tools():
    cleanup = _tool(
        "Refresh a dismissed sense target at the exact removal boundary, "
        "preserving independent grants.",
        [_entity("target_state"), _optional("after_removal", "boolean", False)],
        [
            _when(
                "target_state.respiration_medium != null",
                [
                    _when(
                        "after_removal == true",
                        [
                            _set("target_state.respiration_water_until", "state.lasting_tick"),
                        ],
                    ),
                    _call("refresh_srd_respiration", {"target_state": "{_target_state_id}"}),
                ],
            ),
            _when(
                "after_removal == true and target_state.language_reader != null",
                [
                    _call(
                        "resolve_srd_timed_senses",
                        {"target_state": "{_target_state_id}"},
                        ["_sense_comprehension"],
                    ),
                    _when(
                        "_sense_comprehension != true", [_set("target_state.language_reader", None)]
                    ),
                ],
            ),
        ],
    )
    dismiss = _public(
        _tool(
            "Dismiss only this caster's saved time-span senses spell without spending an action. "
            "An Incapacitated caster cannot dismiss it. "
            "Native senses and other casters' grants remain active.",
            [_entity("caster_state"), _parameter("spell", "enum", enum_values=sorted(PROFILES))],
            [
                _require(
                    "caster_state.is_alive == true and not "
                    "('incapacitated' in caster_state.conditions or "
                    "'paralyzed' in caster_state.conditions or "
                    "'petrified' in caster_state.conditions or "
                    "'stunned' in caster_state.conditions or "
                    "'unconscious' in caster_state.conditions)",
                    "An Incapacitated caster cannot dismiss a spell.",
                ),
                _set("_sense_dismiss_records", []),
                _when(
                    "state.turn_effects != null",
                    [_set("_sense_dismiss_records", "state.turn_effects")],
                ),
                _set("_sense_dismiss_retained", []),
                _set("_sense_dismiss_targets", []),
                _loop(
                    "_sense_dismiss_records",
                    "_sense_dismiss_record",
                    [
                        {
                            "condition": "_sense_dismiss_record.kind == 'timed_senses' and "
                            "_sense_dismiss_record.spell == spell and "
                            "_sense_dismiss_record.caster == _caster_state_id",
                            "on_true": [
                                _when(
                                    "_sense_dismiss_record.target not in _sense_dismiss_targets",
                                    [
                                        _push(
                                            "_sense_dismiss_targets", "_sense_dismiss_record.target"
                                        ),
                                    ],
                                ),
                            ],
                            "on_false": [_push("_sense_dismiss_retained", "_sense_dismiss_record")],
                        },
                    ],
                ),
                _require(
                    "len(_sense_dismiss_targets) > 0",
                    "This caster has no saved effect of the selected spell to dismiss.",
                ),
                _loop(
                    "_sense_dismiss_targets",
                    "_sense_dismiss_target",
                    [
                        _call(
                            "refresh_srd_dismissed_senses_target",
                            {"target_state": "{_sense_dismiss_target}"},
                        ),
                    ],
                ),
                _set("state.turn_effects", "_sense_dismiss_retained"),
                _loop(
                    "_sense_dismiss_targets",
                    "_sense_dismiss_target",
                    [
                        _call(
                            "refresh_srd_dismissed_senses_target",
                            {"target_state": "{_sense_dismiss_target}", "after_removal": True},
                        ),
                    ],
                ),
                _set("_sense_dismiss_count", "len(_sense_dismiss_targets)"),
            ],
        ),
        "exploration",
        "Spell dismissed",
        [("Targets released", "_sense_dismiss_count")],
    )
    return {"dismiss_srd_senses_spell": dismiss, "refresh_srd_dismissed_senses_target": cleanup}


def extend_runtime(text):
    """Compose timed senses and their later public consumers at package generation."""
    tools = _load_runtime(text)["tools"]
    changed = _sense_tools() | _casting_tools() | _respiration_tools() | _dismissal_tools()
    changed["resolve_srd_sight"] = _sight_tool()
    changed["comprehend_srd_language"] = _language_tool()
    for name in PUBLIC_TOOLS:
        tool = changed[name]
        if any(parameter["name"] == "lasting_state" for parameter in tool["parameters"]):
            node_name = name + "_node"
            node = deepcopy(tool)
            node["metadata"] = {
                "category": "internal",
                "visible_to_player": False,
                "costs_action": False,
            }
            changed[node_name] = node
            tool["parameters"] = [
                parameter
                for parameter in tool["parameters"]
                if parameter["name"] != "lasting_state"
            ]
            params = {
                parameter["name"]: "{"
                + (
                    "_" + parameter["name"] + "_id"
                    if parameter.get("resolve") in {"entity", "participant"}
                    else parameter["name"]
                )
                + "}"
                for parameter in tool["parameters"]
            }
            params["lasting_state"] = "state"
            exports = [
                row["value"]["path"].removeprefix("context.")
                for row in tool["metadata"]["result_card"]["summary"]
                if row["value"]["path"].startswith("context._")
            ]
            tool["effects"] = [_call(node_name, params, exports)]
    cast = deepcopy(tools["cast_spell"])
    target_parameter = next(p for p in cast["parameters"] if p["name"] == "targets")
    target_parameter["max_items"] = max(target_parameter["max_items"], 10)
    cast["effects"] = [
        effect
        for effect in cast["effects"]
        if effect.get("condition") != "_spell_definition.family == 'timed_senses'"
    ]
    if "cast_spell_timed_senses" not in str(cast["effects"]):
        branch = _when(
            "_spell_definition.family == 'timed_senses'",
            [
                _set("_sense_dispatch_targets", []),
                _when("targets != null", [_set("_sense_dispatch_targets", "targets")]),
                _call(
                    "cast_spell_timed_senses",
                    {
                        "spell": "{spell}",
                        "caster_state": "{_caster_state_id}",
                        "target_state": "{_scalar_target_id}",
                        "targets": "{_sense_dispatch_targets}",
                        "target_willing": "{target_willing}",
                        "all_targets_willing": "{all_targets_willing}",
                        "caster_can_touch_target": "{_caster_can_touch_target}",
                        "caster_can_touch_all_targets": "{caster_can_touch_all_targets}",
                        "target_distance": "{target_distance}",
                        "targets_max_distance": "{targets_max_distance}",
                    },
                ),
            ],
        )
        branch["on_true"][-1]["export_context"] = {
            "_nested_spell_summary_rows": "_spell_summary_rows",
            "_nested_spell_detail_rows": "_spell_detail_rows",
        }
        for suffix in ("summary", "detail"):
            branch["on_true"].append(
                _loop(
                    f"_nested_spell_{suffix}_rows",
                    "_nested_sense_row",
                    [_push(f"_spell_{suffix}_rows", "_nested_sense_row")],
                )
            )
        last = max(
            i
            for i, effect in enumerate(cast["effects"])
            if "_spell_definition.family ==" in effect.get("condition", "")
        )
        cast["effects"].insert(last + 1, branch)
    changed["cast_spell"] = cast
    end = deepcopy(tools["end_combat_turn"])
    if "refresh_srd_respiration" not in str(end["effects"]):
        end["effects"].insert(
            0,
            _when(
                "actor.respiration_medium != null",
                [
                    _call(
                        "refresh_srd_respiration", {"target_state": "{_actor_id}", "end_turn": True}
                    ),
                ],
            ),
        )
    changed["end_combat_turn"] = end
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_all_srd_respiration" not in str(clock["effects"]):
        clock["effects"].append(
            _call("refresh_all_srd_respiration", {"lasting_state": "{_lasting_state_id}"})
        )
    changed["advance_srd_lasting_clock"] = clock
    reduce = deepcopy(tools["reduce_srd_exhaustion"])
    if "target.respiration_exhaustion" not in str(reduce["effects"]):
        reduce["effects"].append(
            _when(
                "target.respiration_exhaustion != null",
                [
                    _set(
                        "target.respiration_exhaustion",
                        "max(0, target.respiration_exhaustion - levels)",
                    ),
                ],
            )
        )
    changed["reduce_srd_exhaustion"] = reduce
    # Generic movement owns a target parameter; the timed control flag must use that entity.
    movement = deepcopy(tools["spend_srd_movement"])
    for precondition in movement["pre_conditions"]:
        if precondition.get("condition") == "actor_state.timed_control_speed_zero != true":
            precondition["condition"] = "target.timed_control_speed_zero != true"
    changed["spend_srd_movement"] = movement
    # Blur has a real Truesight bypass. Query active spells rather than caching a permanent range.
    attack = deepcopy(tools["resolve_srd_timed_actor_attack_defenses"])
    if "resolve_srd_timed_senses" not in str(attack["effects"]):
        attack["effects"].insert(
            0,
            _call(
                "resolve_srd_timed_senses",
                {"target_state": "{_actor_state_id}"},
                ["_sense_truesight"],
            ),
        )

        def replace_native(value):
            if isinstance(value, str):
                return value.replace(
                    "actor_state.truesight_feet != null and actor_state.truesight_feet",
                    "_sense_truesight > 0 and _sense_truesight",
                )
            if isinstance(value, list):
                return [replace_native(child) for child in value]
            if isinstance(value, dict):
                return {key: replace_native(child) for key, child in value.items()}
            return value

        attack["effects"] = replace_native(attack["effects"])
    changed["resolve_srd_timed_actor_attack_defenses"] = attack
    text = _replace_tools(text, changed)
    if "      timed_senses:" not in text:
        schema = yaml.dump({"timed_senses": _schema()}, Dumper=_RuntimeDumper, sort_keys=False)
        schema = "".join("      " + line for line in schema.splitlines(True))
        text = text.replace(
            "      timed_actor_defense:", schema + "\n      timed_actor_defense:", 1
        )
    return text
