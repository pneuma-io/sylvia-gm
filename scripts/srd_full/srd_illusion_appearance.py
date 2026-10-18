"""Illusory appearances and writing retain physical subjects and observer-owned knowledge."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_divination_spells import _normalize
    from .srd_illusion_spells import ACTIVE, PENDING, _distance, _study_tool
    from .srd_scene_relations import _array, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _when,
    )
    from .srd_timed_senses import _lookup, _loop, _optional, _public, _push, _replace_tools
else:
    from srd_divination_spells import _normalize
    from srd_illusion_spells import ACTIVE, PENDING, _distance, _study_tool
    from srd_scene_relations import _array, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _when,
    )
    from srd_timed_senses import _lookup, _loop, _optional, _public, _push, _replace_tools


SOURCE = {
    "disguise_self": (124, "Disguise Self", "Disintegrate"),
    "seeming": ([160, 161], "Seeming", "Sending"),
    "hallucinatory_terrain": (139, "Hallucinatory Terrain", "Harm"),
    "illusory_script": (142, "Illusory Script", "Imprisonment"),
}
PROFILES = {
    spell: {
        "family": "illusion_appearance",
        "base_level": level,
        "casting_time": time,
        "range_kind": kind,
        "range_feet": distance,
        "duration_rounds": rounds,
        "concentration": False,
        "appearance_mode": mode,
    }
    for spell, level, time, kind, distance, rounds, mode in (
        ("disguise_self", 1, "action", "self", 0, 600, "disguise"),
        ("seeming", 5, "action", "distance", 30, 4800, "disguise"),
        ("hallucinatory_terrain", 4, "10_minutes", "distance", 300, 14400, "terrain"),
        ("illusory_script", 1, "1_minute", "touch", 5, 144000, "script"),
    )
}
PUBLIC_TOOLS = {
    "observe_srd_appearance",
    "study_srd_appearance",
    "study_srd_appearance_creature",
    "inspect_srd_appearance",
    "read_srd_illusory_script",
    "dismiss_srd_appearance_spell",
}


def validate_profiles(definitions):
    """Bind executable fields to the complete pinned source clauses."""
    for spell, definition in definitions.items():
        if definition.get("family") == "illusion_appearance" and definition != PROFILES.get(spell):
            raise SystemExit(f"Illusory appearance source profile differs: {spell}")


def _list_choice(name, items, default=None, **extra):
    return _optional(name, "array", [] if default is None else default, items=items, **extra)


CAST_CHOICES = [
    _list_choice(
        "appearance_specs",
        {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "height_offset_inches": {"type": "integer", "minimum": -12, "maximum": 12},
                "limb_arrangement": {"type": "string"},
                "willing": {"type": "boolean"},
            },
            "required": [
                "subject",
                "description",
                "height_offset_inches",
                "limb_arrangement",
                "willing",
            ],
            "additionalProperties": False,
        },
        max_items=64,
    ),
    _array("appearance_position", default=[0, 0, 0]),
    _array("appearance_cube_min", default=[0, 0, 0]),
    _optional("appearance_description", default=""),
    _optional("appearance_sound", default=""),
    _optional("appearance_smell", default=""),
    _optional("appearance_terrain_kind", default=""),
    _optional("appearance_tactile_kind", default=""),
    _optional("script_surface", default=""),
    _optional("script_original", default=""),
    _optional("script_apparent", default=""),
    _optional("script_language", default=""),
    _optional("script_handwriting", default=""),
    _list_choice("script_readers", {"type": "string"}, max_items=64),
]
HIDDEN_SOURCE = [
    _optional("spell_cast_id", default=""),
    _optional("spell_cast_owner", default=None, resolve="entity"),
    _optional("actual_spell_level", "integer", -1, minimum=-1, maximum=9),
]


def _source_tools():
    """Non-concentration instances expire independently and retire only their own appearance."""
    collect = _tool_scene(
        "Resolve one exact installed appearance source using the actual lasting clock.",
        [_parameter("appearance_id")],
        [
            _require(
                "state.appearance_spell_sources != null and appearance_id in "
                "state.appearance_spell_sources",
                "Select a saved appearance source.",
            ),
            _set("_appearance_record", "state.appearance_spell_sources[appearance_id]"),
            _set(
                "_appearance_active",
                "_appearance_record.installed == true and "
                "state.lasting_tick < _appearance_record.expires_tick",
            ),
        ],
    )
    recognize = _tool_scene(
        "Save only the actual observer's recognition of this exact appearance.",
        [_parameter("appearance_id"), _entity("observer_state")],
        [
            _set("_appearance_known", "state.appearance_spell_sources[appearance_id].recognized"),
            _when(
                "_observer_state_id not in _appearance_known",
                [_push("_appearance_known", "_observer_state_id")],
            ),
            _set("state.appearance_spell_sources.{appearance_id}.recognized", "_appearance_known"),
        ],
    )
    retire = _tool_scene(
        "Retire exactly one source; Dispel also erases both forms of Illusory Script.",
        [_parameter("appearance_id"), _optional("reason", default="ended")],
        [
            _call(
                "collect_srd_appearance_source",
                {"appearance_id": "{appearance_id}"},
                ["_appearance_record", "_appearance_active"],
            ),
            _when(
                "_appearance_record.installed == true",
                [
                    _set("state.appearance_spell_sources.{appearance_id}.installed", False),
                    _when(
                        "_appearance_record.spell == 'illusory_script' and reason == 'dispel' and "
                        "state.script_written_sources[_appearance_record.subject] == appearance_id",
                        [
                            _set(
                                "state.erased_illusory_script_surfaces.{_appearance_record.subject}",
                                True,
                            ),
                            _set("state.script_written_text.{_appearance_record.subject}", "''"),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "collect_srd_appearance_source": collect,
        "recognize_srd_appearance_source": recognize,
        "retire_srd_appearance_source": retire,
    }


def _install_tool():
    """Save actual source, level, anatomy-bound appearance and finite independent lifetime."""
    return _tool_scene(
        "Install a qualified target's appearance without changing its physical body.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer"),
            _optional("subject_id", default=""),
            _parameter("spec", "object"),
            *deepcopy(CAST_CHOICES[1:]),
            *deepcopy(HIDDEN_SOURCE),
        ],
        [
            _lookup("spell", "_appearance_profile"),
            _set("_appearance_subject_id", "''"),
            _when(
                "_appearance_profile.appearance_mode != 'terrain'",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{subject_id}"},
                        ["_info_scene_entity"],
                    ),
                    _set("_appearance_subject_id", "_info_scene_entity.id"),
                ],
            ),
            _when(
                "state.appearance_spell_sources == null",
                [
                    _set("state.appearance_spell_sources", {}),
                    _set("state.appearance_source_ids", []),
                    _set("state.appearance_subject_sources", {}),
                    _set("state.appearance_area_sources", []),
                    _set("state.erased_illusory_script_surfaces", {}),
                    _set("state.script_written_text", {}),
                    _set("state.script_written_sources", {}),
                    _set("state.appearance_serial", 0),
                ],
            ),
            _require(
                "len(state.appearance_spell_sources) < 256",
                "The saved appearance registry is full.",
            ),
            _set("state.appearance_serial", "state.appearance_serial + 1"),
            _call(
                "record_srd_result_text",
                {"text": "{_caster_state_id}:{spell}:{state.appearance_serial}"},
                ["_recorded_result_text"],
            ),
            _set("_appearance_id", "_recorded_result_text"),
            _set("_appearance_level", "slot_level"),
            _when("actual_spell_level >= 0", [_set("_appearance_level", "actual_spell_level")]),
            _set("_appearance_owner", "_caster_state_id"),
            _when("spell_cast_owner != null", [_set("_appearance_owner", "_spell_cast_owner_id")]),
            _set(
                "_appearance_record",
                {
                    "id": "_appearance_id",
                    "serial": "state.appearance_serial",
                    "spell": "spell",
                    "caster": "_caster_state_id",
                    "source_caster_id": "_appearance_owner",
                    "cast_id": "spell_cast_id",
                    "spell_level": "_appearance_level",
                    "ongoing_duration": True,
                    "dispel_ongoing": True,
                    "installed": True,
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + _appearance_profile.duration_rounds * 2",
                    "subject": "_appearance_subject_id",
                    "mode": "_appearance_profile.appearance_mode",
                    "save_dc": "save_dc",
                    "recognized": [],
                    "spec": "spec",
                    "origin": "appearance_position",
                    "cube_min": "appearance_cube_min",
                    "cube_max": [f"appearance_cube_min[{i}] + 150" for i in range(3)],
                    "description": "appearance_description",
                    "sound": "appearance_sound",
                    "smell": "appearance_smell",
                    "terrain_kind": "appearance_terrain_kind",
                    "tactile_kind": "appearance_tactile_kind",
                    "original": "script_original",
                    "apparent": "script_apparent",
                    "language": "script_language",
                    "handwriting": "script_handwriting",
                    "readers": "script_readers",
                },
            ),
            _set("state.appearance_spell_sources.{_appearance_id}", "_appearance_record"),
            _set("_appearance_source_ids", "state.appearance_source_ids"),
            _push("_appearance_source_ids", "_appearance_id"),
            _set("state.appearance_source_ids", "_appearance_source_ids"),
            _when(
                "_appearance_subject_id != ''",
                [
                    _when(
                        "_appearance_subject_id not in state.appearance_subject_sources",
                        [
                            _set("state.appearance_subject_sources.{_appearance_subject_id}", []),
                        ],
                    ),
                    _set(
                        "_appearance_subject_ids",
                        "state.appearance_subject_sources[_appearance_subject_id]",
                    ),
                    _push("_appearance_subject_ids", "_appearance_id"),
                    _set(
                        "state.appearance_subject_sources.{_appearance_subject_id}",
                        "_appearance_subject_ids",
                    ),
                ],
            ),
            _when(
                "spell == 'hallucinatory_terrain'",
                [
                    _set("_appearance_areas", "state.appearance_area_sources"),
                    _push("_appearance_areas", "_appearance_id"),
                    _set("state.appearance_area_sources", "_appearance_areas"),
                ],
            ),
            _set("state.has_appearance_spells", True),
        ],
    )


def _anatomy_tool():
    """Qualify the fictional limb pattern against the saved current body, then calculate height."""
    return _tool_scene(
        "Validate a disguise against the current admitted subject body without interpreting prose.",
        [_entity("subject_state"), _parameter("spec", "object")],
        [
            _call(
                "srd_information_scene_entity",
                {"subject": "{_subject_state_id}"},
                ["_info_scene_entity", "_info_position"],
            ),
            _require(
                "subject_state.is_alive == true and subject_state.creature_kind != null",
                "A disguise requires an actual living creature body.",
            ),
            _require(
                "_info_scene_entity.metadata.basic_limb_arrangement != null and "
                "spec.limb_arrangement == _info_scene_entity.metadata.basic_limb_arrangement",
                "A disguise requires the saved body's same basic arrangement of limbs.",
            ),
            _when(
                "subject_state.form_id != null and subject_state.form_id != ''",
                [
                    _require(
                        "_info_scene_entity.metadata.anatomy_form_id == subject_state.form_id",
                        "Qualify anatomy against this actual transformed form.",
                    ),
                ],
            ),
            _require(
                "spec.height_offset_inches >= -12 and spec.height_offset_inches <= 12",
                "A disguise can change apparent height by at most one foot.",
            ),
            _require(
                "_info_scene_entity.height + spec.height_offset_inches * 12 ** -1 > 0",
                "The apparent creature height must remain positive.",
            ),
            _set("_appearance_body_height", "_info_scene_entity.height"),
            _set("_appearance_subject_scene", "_info_scene_entity.id"),
        ],
    )


def _casting_tool():
    """Pay through the shared cast owner, then qualify each actual subject and unwilling save."""
    common = {p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES[1:] + HIDDEN_SOURCE}
    common.update(
        caster_state="{_caster_state_id}",
        spell="{spell}",
        slot_level="{slot_level}",
        save_dc="{save_dc}",
    )
    install = _call(
        "install_srd_appearance_source",
        common
        | {
            "subject_id": "{_appearance_subject_scene}",
            "spec": "{_appearance_spec}",
        },
        ["_appearance_id"],
    )
    return _tool_scene(
        "Resolve source-qualified appearances, terrain and written surfaces after shared casting.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer"),
            *deepcopy(CAST_CHOICES),
            *deepcopy(HIDDEN_SOURCE),
        ],
        [
            _lookup("spell", "_appearance_profile"),
            _set("_appearance_summary_rows", []),
            _set("_appearance_detail_rows", []),
            _set("_appearance_installed", []),
            _when(
                "_appearance_profile.appearance_mode == 'disguise'",
                [
                    _require(
                        "len(appearance_specs) >= 1 and len(appearance_specs) <= 64",
                        "Choose one to sixty-four actual appearance subjects.",
                    ),
                    _when(
                        "spell == 'disguise_self'",
                        [
                            _require(
                                "len(appearance_specs) == 1 and "
                                "appearance_specs[0].subject == _caster_state_id",
                                "Disguise Self affects only the actual caster.",
                            ),
                        ],
                    ),
                    _set("_appearance_seen", []),
                    _loop(
                        "appearance_specs",
                        "_appearance_spec",
                        [
                            _require(
                                "_appearance_spec.subject not in _appearance_seen and "
                                "_appearance_spec.description != ''",
                                "Each selected creature needs one distinct described appearance.",
                            ),
                            _push("_appearance_seen", "_appearance_spec.subject"),
                            _call(
                                "qualify_srd_disguise_anatomy",
                                {
                                    "subject_state": "{_appearance_spec.subject}",
                                    "spec": "{_appearance_spec}",
                                },
                                ["_appearance_subject_scene", "_appearance_body_height"],
                            ),
                            _set("_appearance_accepted", True),
                            _when(
                                "spell == 'seeming'",
                                [
                                    _call(
                                        "resolve_srd_illumination_target_sight",
                                        {
                                            "observer_state": "{_caster_state_id}",
                                            "target_state": "{_appearance_spec.subject}",
                                        },
                                        [
                                            "_illumination_target_can_see",
                                            "_illumination_point_distance",
                                        ],
                                    ),
                                    _require(
                                        "_illumination_target_can_see == true and "
                                        "_illumination_point_distance <= 30",
                                        "Seeming needs a creature seen within thirty feet.",
                                    ),
                                    _when(
                                        "_appearance_spec.willing != true",
                                        [
                                            _call(
                                                "resolve_srd_divination_save",
                                                {
                                                    "target_state": "{_appearance_spec.subject}",
                                                    "ability": "charisma",
                                                    "dc": "{save_dc}",
                                                },
                                                ["_divination_save_success"],
                                            ),
                                            _set(
                                                "_appearance_accepted",
                                                "_divination_save_success != true",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            _when(
                                "_appearance_accepted == true",
                                [install, _push("_appearance_installed", "_appearance_id")],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "spell == 'illusory_script'",
                [
                    _require(
                        "script_original != '' and script_surface != ''",
                        "Write a real original message on a selected suitable surface.",
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{script_surface}"},
                        ["_info_scene_entity", "_info_position"],
                    ),
                    _require(
                        "_info_scene_entity.metadata.is_object == true and "
                        "_info_scene_entity.metadata.writable_surface == true",
                        "Select admitted paper, parchment or another suitable written surface.",
                    ),
                    _set("_appearance_subject_scene", "_info_scene_entity.id"),
                    _call(
                        "validate_srd_identify_touch",
                        {
                            "caster_state": "{_caster_state_id}",
                            "subject": "{script_surface}",
                            "touch_confirmed": True,
                        },
                    ),
                    _when(
                        "script_apparent != ''",
                        [
                            _require(
                                "script_language != '' and caster_state.languages != null and "
                                "script_language in caster_state.languages",
                                "The apparent Script language must be one the caster knows.",
                            ),
                        ],
                    ),
                    _set("_appearance_spec", {}),
                    install,
                    _push("_appearance_installed", "_appearance_id"),
                    _set(
                        "state.script_written_text.{_appearance_subject_scene}", "script_original"
                    ),
                    _set(
                        "state.script_written_sources.{_appearance_subject_scene}", "_appearance_id"
                    ),
                    _set(
                        "state.erased_illusory_script_surfaces.{_appearance_subject_scene}", False
                    ),
                ],
            ),
            _when(
                "spell == 'hallucinatory_terrain'",
                [
                    _require(
                        "appearance_description != '' and appearance_terrain_kind != ''",
                        "Describe the source-qualified apparent natural terrain.",
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_caster_state_id}"},
                        ["_info_scene_entity", "_info_position"],
                    ),
                    _set("_appearance_cast_plane", "_info_scene_entity.metadata.plane"),
                    _distance("{_info_position}", "{appearance_position}", "_appearance_range"),
                    _require(
                        "_appearance_range <= 300",
                        "Hallucinatory Terrain's Cube is within 300 feet.",
                    ),
                    _call(
                        "srd_information_barriers",
                        {
                            "start": "{_info_position}",
                            "end": "{appearance_position}",
                            "rule": "solid",
                        },
                        ["_information_blocked"],
                    ),
                    _require(
                        "_information_blocked != true",
                        "A solid barrier blocks the Cube's actual casting origin.",
                    ),
                    *[
                        _require(
                            f"appearance_position[{i}] >= appearance_cube_min[{i}] and "
                            f"appearance_position[{i}] <= appearance_cube_min[{i}] + 150",
                            "The Cube's actual range origin is inside its bounds.",
                        )
                        for i in range(3)
                    ],
                    _require(
                        " or ".join(
                            f"appearance_position[{i}] == appearance_cube_min[{i}] or "
                            f"appearance_position[{i}] == appearance_cube_min[{i}] + 150"
                            for i in range(3)
                        ),
                        "A Cube's actual range origin lies on one of its faces.",
                    ),
                    _set("_appearance_subject_scene", "''"),
                    _set(
                        "_appearance_spec",
                        {
                            "plane": "_appearance_cast_plane",
                        },
                    ),
                    install,
                    _push("_appearance_installed", "_appearance_id"),
                ],
            ),
            _push(
                "_appearance_summary_rows",
                {"label": "'Appearance sources'", "value": "_appearance_installed"},
            ),
        ],
    )


def _perception_tool():
    """Select the latest active subject illusion; terrain affects only admitted natural surfaces."""
    return _tool_scene(
        "Read an actual subject's apparent appearance after the shared sight owner qualifies it.",
        [
            _entity("observer_state"),
            _parameter("subject"),
            _array("point", default=[0, 0, 0]),
            _optional("use_point", "boolean", False),
            _optional("use_observer_senses", "boolean", True),
            _array("sense_origin", default=[0, 0, 0]),
            _optional("use_sense_origin", "boolean", False),
        ],
        [
            _call(
                "srd_information_scene_entity",
                {"subject": "{subject}"},
                ["_info_scene_entity", "_info_position"],
            ),
            _set("_appearance_scene_subject", "_info_scene_entity"),
            _set("_appearance_sample", "_info_position"),
            _when("use_point == true", [_set("_appearance_sample", "point")]),
            _set("_appearance_candidates", []),
            _set("_appearance_record", None),
            _set("_appearance_active", False),
            _set("_appearance_known", False),
            _set("_appearance_description", "''"),
            _set("_appearance_sound", "''"),
            _set("_appearance_smell", "''"),
            _set("_appearance_height", "_info_scene_entity.height"),
            _set("_appearance_original_text", "''"),
            _set("_appearance_real_terrain", "''"),
            _set("_appearance_vague", False),
            _set("_appearance_selected_serial", -1),
            _when(
                "state.script_written_text != null and "
                "_info_scene_entity.id in state.script_written_text",
                [
                    _set(
                        "_appearance_original_text",
                        "state.script_written_text[_info_scene_entity.id]",
                    ),
                ],
            ),
            _when(
                "state.has_appearance_spells == true",
                [
                    _when(
                        "_appearance_scene_subject.id in state.appearance_subject_sources",
                        [
                            _set(
                                "_appearance_candidates",
                                "state.appearance_subject_sources[_appearance_scene_subject.id]",
                            ),
                        ],
                    ),
                    _when(
                        "_appearance_scene_subject.metadata.natural_terrain == true",
                        [
                            _set("_appearance_terrain_ids", "state.appearance_area_sources"),
                            _loop(
                                "_appearance_terrain_ids",
                                "_appearance_candidate_id",
                                [
                                    _set(
                                        "_appearance_candidate",
                                        "state.appearance_spell_sources[_appearance_candidate_id]",
                                    ),
                                    _when(
                                        "_appearance_candidate.spec.plane == "
                                        "_appearance_scene_subject.metadata.plane and "
                                        + " and ".join(
                                            f"_appearance_sample[{i}] >= "
                                            f"_appearance_candidate.cube_min[{i}] and "
                                            f"_appearance_sample[{i}] <= "
                                            f"_appearance_candidate.cube_max[{i}]"
                                            for i in range(3)
                                        ),
                                        [
                                            _push(
                                                "_appearance_candidates", "_appearance_candidate_id"
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _loop(
                        "_appearance_candidates",
                        "_appearance_candidate_id",
                        [
                            _set(
                                "_appearance_candidate",
                                "state.appearance_spell_sources[_appearance_candidate_id]",
                            ),
                            _when(
                                "_appearance_candidate.installed == true and "
                                "state.lasting_tick < _appearance_candidate.expires_tick and "
                                "_appearance_candidate.serial > _appearance_selected_serial",
                                [
                                    _set("_appearance_record", "_appearance_candidate"),
                                    _set(
                                        "_appearance_selected_serial",
                                        "_appearance_candidate.serial",
                                    ),
                                    _set("_appearance_active", True),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "_appearance_active == true",
                [
                    _set(
                        "_appearance_known", "_observer_state_id in _appearance_record.recognized"
                    ),
                    _set("_sense_truesight", 0),
                    _when(
                        "use_observer_senses == true",
                        [
                            _call(
                                "resolve_srd_timed_senses",
                                {"target_state": "{_observer_state_id}"},
                                ["_sense_truesight"],
                            ),
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_observer_state_id}"},
                                ["_info_position"],
                            ),
                            _when(
                                "use_sense_origin == true", [_set("_info_position", "sense_origin")]
                            ),
                            _distance(
                                "{_info_position}", "{_appearance_sample}", "_appearance_distance"
                            ),
                            _when(
                                "_sense_truesight > 0 and _appearance_distance <= _sense_truesight "
                                "and not ('blinded' in observer_state.conditions)",
                                [
                                    _set("_appearance_known", True),
                                    _when(
                                        "_appearance_record.mode != 'script'",
                                        [
                                            _call(
                                                "recognize_srd_appearance_source",
                                                {
                                                    "appearance_id": "{_appearance_record.id}",
                                                    "observer_state": "{_observer_state_id}",
                                                },
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set("_appearance_description", "_appearance_record.description"),
                    _set("_appearance_sound", "_appearance_record.sound"),
                    _set("_appearance_smell", "_appearance_record.smell"),
                    _when(
                        "_appearance_record.mode == 'disguise'",
                        [
                            _set("_appearance_description", "_appearance_record.spec.description"),
                            _set(
                                "_appearance_height",
                                "_appearance_scene_subject.height + "
                                "_appearance_record.spec.height_offset_inches * 12 ** -1",
                            ),
                        ],
                    ),
                    _when(
                        "_appearance_record.mode == 'terrain' and _appearance_known == true",
                        [
                            _set("_appearance_vague", True),
                            _require(
                                "_appearance_scene_subject.metadata.terrain_kind != null",
                                "Admit the real natural terrain seen beneath the vague illusion.",
                            ),
                            _set(
                                "_appearance_real_terrain",
                                "_appearance_scene_subject.metadata.terrain_kind",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _observe_tool():
    """Public observation uses actual target visibility and preserves knowledge per observer."""
    return _public(
        _tool_scene(
            "Observe an appearance on the selected real subject through the shared sight owner.",
            [
                _entity("observer_state"),
                _parameter("subject"),
                _array("point", default=[0, 0, 0]),
                _optional("use_point", "boolean", False),
            ],
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{subject}"},
                    ["_info_scene_entity", "_info_position"],
                ),
                _set("_appearance_observed_subject", "_info_scene_entity"),
                _set("_appearance_observed_point", "_info_position"),
                _when(
                    "_appearance_observed_subject.metadata.is_object == true",
                    [
                        _call(
                            "check_srd_illumination_object_visibility",
                            {
                                "object_id": "{subject}",
                                "observer_state": "{_observer_state_id}",
                            },
                            [
                                "_illumination_object_origin",
                                "_illumination_object_plane",
                                "_illumination_object_invisible",
                                "_illumination_object_outlined",
                            ],
                        ),
                        _set("_appearance_observed_point", "_illumination_object_origin"),
                        _set(
                            "_appearance_observed_subject.metadata.plane",
                            "_illumination_object_plane",
                        ),
                    ],
                ),
                _when(
                    "use_point == true",
                    [
                        _require(
                            "_appearance_observed_subject.metadata.natural_terrain == true and "
                            + " and ".join(
                                "abs(point[" + str(i) + "] - "
                                "_appearance_observed_subject.position[" + str(i) + "]) <= "
                                "_appearance_observed_subject.footprint.half_extents["
                                + str(i)
                                + "]"
                                for i in range(2)
                            )
                            + " and point[2] >= "
                            "_appearance_observed_subject.position[2] and point[2] <= "
                            "_appearance_observed_subject.position[2] + "
                            "_appearance_observed_subject.height",
                            "Sample only a point on the admitted natural terrain surface.",
                        ),
                        _set("_appearance_observed_point", "point"),
                    ],
                ),
                _call(
                    "resolve_srd_illumination_point_sight",
                    {
                        "observer_state": "{_observer_state_id}",
                        "point": "{_appearance_observed_point}",
                        "perception_plane": "{_appearance_observed_subject.metadata.plane}",
                    },
                    [
                        "_illumination_point_can_see",
                        "_illumination_point_sight_distance",
                        "_illumination_point_lighting",
                        "_illumination_point_obstruction",
                    ],
                ),
                _set("_appearance_can_see", "_illumination_point_can_see"),
                _when(
                    "_appearance_observed_subject.metadata.is_object == true",
                    [
                        _set(
                            "_appearance_object_invisible",
                            "_illumination_object_invisible == true and "
                            "_illumination_object_outlined != true",
                        ),
                        _call(
                            "resolve_srd_sight",
                            {
                                "observer_state": "{_observer_state_id}",
                                "distance": "{_illumination_point_sight_distance}",
                                "lighting": "{_illumination_point_lighting}",
                                "obstruction": "{_illumination_point_obstruction}",
                                "subject_invisible": "{_appearance_object_invisible}",
                            },
                            ["_sight_can_see"],
                        ),
                        _set(
                            "_appearance_can_see",
                            "_appearance_can_see == true and _sight_can_see == true",
                        ),
                    ],
                ),
                _when(
                    "_appearance_observed_subject.metadata.participant_ref != null",
                    [
                        _call(
                            "resolve_srd_illumination_target_sight",
                            {
                                "observer_state": "{_observer_state_id}",
                                "target_state": (
                                    "{_appearance_observed_subject.metadata.participant_ref}"
                                ),
                            },
                            ["_illumination_target_can_see"],
                        ),
                        _set("_appearance_can_see", "_illumination_target_can_see"),
                    ],
                ),
                _require("_appearance_can_see == true", "The actual subject must be visible."),
                _set(
                    "_appearance_use_observed_point",
                    "use_point == true or _appearance_observed_subject.metadata.is_object == true",
                ),
                _call(
                    "read_srd_apparent_subject",
                    {
                        "observer_state": "{_observer_state_id}",
                        "subject": "{subject}",
                        "point": "{_appearance_observed_point}",
                        "use_point": "{_appearance_use_observed_point}",
                    },
                    [
                        "_appearance_record",
                        "_appearance_active",
                        "_appearance_known",
                        "_appearance_description",
                        "_appearance_height",
                        "_appearance_sound",
                        "_appearance_smell",
                        "_appearance_real_terrain",
                        "_appearance_vague",
                    ],
                ),
            ],
        ),
        "exploration",
        "Apparent subject",
        [
            ("Active illusion", "_appearance_active"),
            ("Known illusion", "_appearance_known"),
            ("Appearance", "_appearance_description"),
            ("Apparent height", "_appearance_height"),
            ("Sound", "_appearance_sound"),
            ("Smell", "_appearance_smell"),
            ("Vague superimposed image", "_appearance_vague"),
            ("Real terrain", "_appearance_real_terrain"),
        ],
    )


def _script_tool():
    """Written delivery honors designated readers, current Truesight and the shown language."""
    return _public(
        _tool_scene(
            "Read only the message this observer can actually perceive on the written surface.",
            [_entity("observer_state"), _parameter("subject")],
            [
                _call(
                    "observe_srd_appearance_node",
                    {
                        "observer_state": "{_observer_state_id}",
                        "subject": "{subject}",
                    },
                    ["_appearance_record", "_appearance_active", "_appearance_known"],
                ),
                _set("_appearance_message", "''"),
                _set("_appearance_text_readable", False),
                _set("_appearance_handwriting", "''"),
                _when(
                    "_appearance_active == true and _appearance_record.mode == 'script'",
                    [
                        _set("_appearance_handwriting", "'unknown or magical script'"),
                        _when(
                            "_observer_state_id == _appearance_record.caster or "
                            "_observer_state_id in _appearance_record.readers or "
                            "_appearance_known == true",
                            [
                                _set("_appearance_message", "_appearance_record.original"),
                                _set("_appearance_text_readable", True),
                                _set("_appearance_handwriting", "_appearance_record.caster"),
                            ],
                        ),
                        _when(
                            "_appearance_text_readable != true and "
                            "_appearance_record.apparent != '' and "
                            "observer_state.languages != null "
                            "and _appearance_record.language in "
                            "observer_state.languages",
                            [
                                _set("_appearance_message", "_appearance_record.apparent"),
                                _set("_appearance_text_readable", True),
                                _set("_appearance_handwriting", "_appearance_record.handwriting"),
                            ],
                        ),
                    ],
                ),
                _when(
                    "_appearance_active != true and state.script_written_text != null and "
                    "subject in state.script_written_text",
                    [
                        _when(
                            "state.erased_illusory_script_surfaces[subject] != true",
                            [
                                _set("_appearance_message", "state.script_written_text[subject]"),
                                _set("_appearance_text_readable", True),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "exploration",
        "Illusory writing",
        [
            ("Readable", "_appearance_text_readable"),
            ("Message", "_appearance_message"),
            ("Handwriting", "_appearance_handwriting"),
        ],
    )


def _study_tools(tools):
    """Reuse participant/NPC Intelligence Investigation rolls and the saved actual source DC."""

    def adapt(value):
        if isinstance(value, str):
            return (
                value.replace("illusion_proficiency_factor", "appearance_proficiency_factor")
                .replace("illusion_skill", "appearance_skill")
                .replace("_illusion_", "_appearance_")
                .replace("illusion_id", "subject")
            )
        if isinstance(value, list):
            return [adapt(item) for item in value]
        if isinstance(value, dict):
            return {adapt(key): adapt(item) for key, item in value.items()}
        return value

    study = adapt(_study_tool(tools))
    study["description"] = "Study an actual visible disguise or terrain against its saved DC."
    perceive = study["effects"][0]
    perceive["call"] = "observe_srd_appearance_node"
    perceive["export_context"] = {
        "_appearance_record": "_appearance_record",
        "_appearance_can_see": "_appearance_can_see",
        "_appearance_active": "_appearance_active",
    }
    study["effects"][1] = _require(
        "_appearance_active == true and _appearance_record.mode != 'script' and "
        "_appearance_can_see == true",
        "Study requires the actual visible disguise or terrain.",
    )
    last = study["effects"][-1]["on_true"][0]
    last["call"] = "recognize_srd_appearance_source"
    last["params"] = {
        "appearance_id": "{_appearance_record.id}",
        "observer_state": "{_observer_state_id}",
    }
    npc = deepcopy(study)
    npc["parameters"] = [
        p
        for p in npc["parameters"]
        if p["name"]
        not in {
            "observer",
            "appearance_skill",
            "appearance_proficiency_factor",
        }
    ]
    for parameter in npc["parameters"]:
        parameter.pop("model_derive", None)
    npc["effects"] = [_set("observer", None), _set("_observer_id", None), *npc["effects"]]
    return {"study_srd_appearance": study, "study_srd_appearance_creature": npc}


def _inspection_tool():
    """Physical contact uses actual reach and admitted texture, never a supplied numeric verdict."""
    return _public(
        _tool_scene(
            "Inspect the actual subject; apparent clothes and terrain do not alter physical touch.",
            [
                _entity("observer_state"),
                _parameter("subject"),
                _array("point", default=[0, 0, 0]),
                _optional("use_point", "boolean", False),
            ],
            [
                _require(PENDING, "Resolve the pending ordinary action first."),
                _require(
                    ACTIVE.replace("caster_state", "observer_state"),
                    "An Incapacitated creature cannot inspect a subject.",
                ),
                _call(
                    "observe_srd_appearance_node",
                    {
                        "observer_state": "{_observer_state_id}",
                        "subject": "{subject}",
                        "point": "{point}",
                        "use_point": "{use_point}",
                    },
                    [
                        "_appearance_record",
                        "_appearance_active",
                        "_appearance_observed_point",
                        "_appearance_observed_subject",
                    ],
                ),
                _require(
                    "_appearance_active == true and _appearance_record.mode != 'script'",
                    "Select an active physical appearance or terrain illusion.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_observer_state_id}"},
                    ["_info_position"],
                ),
                _distance(
                    "{_info_position}", "{_appearance_observed_point}", "_appearance_reach_distance"
                ),
                _set("_appearance_reach", 5),
                _when(
                    "observer_state.melee_reach != null",
                    [
                        _set("_appearance_reach", "observer_state.melee_reach"),
                    ],
                ),
                _require(
                    "_appearance_reach_distance <= _appearance_reach", "Move within actual reach."
                ),
                _call(
                    "srd_information_barriers",
                    {
                        "start": "{_info_position}",
                        "end": "{_appearance_observed_point}",
                        "rule": "solid",
                    },
                    ["_information_blocked"],
                ),
                _require(
                    "_information_blocked != true", "A solid barrier prevents physical inspection."
                ),
                _set("_appearance_difference", "_appearance_record.mode == 'disguise'"),
                _set("_appearance_physical_height", "_appearance_observed_subject.height"),
                _when(
                    "_appearance_record.mode == 'terrain'",
                    [
                        _require(
                            "_appearance_observed_subject.metadata.tactile_kind != null and "
                            "_appearance_record.tactile_kind != ''",
                            "Admit the actual and apparent tactile terrain facts.",
                        ),
                        _set(
                            "_appearance_difference",
                            "_appearance_record.tactile_kind != "
                            "_appearance_observed_subject.metadata.tactile_kind",
                        ),
                    ],
                ),
                _when(
                    "_appearance_difference == true",
                    [
                        _call(
                            "recognize_srd_appearance_source",
                            {
                                "appearance_id": "{_appearance_record.id}",
                                "observer_state": "{_observer_state_id}",
                            },
                        ),
                    ],
                ),
            ],
        ),
        "exploration",
        "Physical appearance inspection",
        [
            ("Touch differs", "_appearance_difference"),
            ("Physical height", "_appearance_physical_height"),
        ],
    )


def _dismissal_tool():
    return _public(
        _tool_scene(
            "Dismiss this caster's finite source without an Action while able to act.",
            [_entity("caster_state"), _parameter("appearance_id")],
            [
                _require(PENDING, "Resolve the pending ordinary action first."),
                _require(ACTIVE, "An Incapacitated caster cannot dismiss a spell."),
                _call(
                    "collect_srd_appearance_source",
                    {"appearance_id": "{appearance_id}"},
                    ["_appearance_record", "_appearance_active"],
                ),
                _require(
                    "_appearance_active == true and _appearance_record.caster == _caster_state_id",
                    "Dismiss only this caster's active source.",
                ),
                _call("retire_srd_appearance_source", {"appearance_id": "{appearance_id}"}),
                _set("_appearance_dismissed", True),
            ],
        ),
        "exploration",
        "Appearance ended",
        [("Dismissed", "_appearance_dismissed")],
    )


def _refresh_tool():
    """Clock expiry removes only expired appearances and provides a cheap inactive-source guard."""
    return _tool_scene(
        "Refresh exact finite appearance lifetimes without erasing naturally expired writing.",
        [],
        [
            _when(
                "state.has_appearance_spells == true",
                [
                    _set("_appearance_live", False),
                    _set("_appearance_source_ids", "state.appearance_source_ids"),
                    _loop(
                        "_appearance_source_ids",
                        "_appearance_source_id",
                        [
                            _set(
                                "_appearance_source",
                                "state.appearance_spell_sources[_appearance_source_id]",
                            ),
                            _when(
                                "_appearance_source.installed == true",
                                [
                                    {
                                        "condition": (
                                            "state.lasting_tick >= _appearance_source.expires_tick"
                                        ),
                                        "on_true": [
                                            _set(
                                                "state.appearance_spell_sources.{_appearance_source_id}."
                                                "installed",
                                                False,
                                            )
                                        ],
                                        "on_false": [_set("_appearance_live", True)],
                                    },
                                ],
                            ),
                        ],
                    ),
                    _set("state.has_appearance_spells", "_appearance_live"),
                ],
            ),
        ],
    )


def _wrap_public(changed):
    """Public adapters hide internal state binding and retain derived actor/roll fields."""
    for name in PUBLIC_TOOLS:
        tool = changed[name]
        node = deepcopy(tool)
        node["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        changed[name + "_node"] = node
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        args = {
            p["name"]: "{"
            + (
                "_" + p["name"] + "_id"
                if p.get("resolve")
                in {
                    "entity",
                    "participant",
                }
                else p["name"]
            )
            + "}"
            for p in tool["parameters"]
        }
        args["lasting_state"] = "state"
        exports = [
            row["value"]["path"].removeprefix("context.")
            for row in tool["metadata"]["result_card"]["summary"]
        ]
        tool["effects"] = [_call(name + "_node", args, exports)]


def _bind_remote_perception(tools, changed):
    """Actual visible subject delivery includes its form with the owning sensor's senses."""
    for name in ["perceive_srd_information_proxy", "perceive_srd_mislead_proxy"]:
        if name not in tools:
            continue
        proxy = deepcopy(tools[name])
        if "read_srd_apparent_subject" in str(proxy["effects"]):
            continue
        hook = _when(
            "_remote_visible == true and record.modality != 'hearing' and "
            "state.has_appearance_spells == true",
            [
                _set("_appearance_sensor_own_senses", "record.spell != 'arcane_eye'"),
                _call(
                    "read_srd_apparent_subject",
                    {
                        "observer_state": "{_caster_state_id}",
                        "subject": "{_remote_entity.id}",
                        "use_observer_senses": "{_appearance_sensor_own_senses}",
                        "sense_origin": "{_remote_origin}",
                        "use_sense_origin": True,
                    },
                    ["_appearance_active", "_appearance_description", "_appearance_known"],
                ),
                _when(
                    "_appearance_active == true and _appearance_description != ''",
                    [
                        _call(
                            "record_srd_result_text",
                            {
                                "text": "{_remote_entity.id}: apparent "
                                "{_appearance_description}; illusion known={_appearance_known}"
                            },
                            ["_recorded_result_text"],
                        ),
                        _push("_info_results", "_recorded_result_text"),
                    ],
                ),
            ],
        )

        def bind(effects, hook=hook):
            result = []
            for effect in effects:
                for key in ["on_true", "on_false"]:
                    if key in effect:
                        effect[key] = bind(effect[key])
                if "for_each" in effect:
                    effect["for_each"]["do"] = bind(effect["for_each"]["do"])
                result.append(effect)
                if (
                    effect.get("list_push", {}).get("list") == "_info_results"
                    and effect.get("list_push", {}).get("value")
                    in {"_recorded_result_text", "_info_projection_message"}
                ):
                    result.append(deepcopy(hook))
            return result

        proxy["effects"] = bind(proxy["effects"])
        changed[name] = proxy


def _bind_script_reading(tools, changed):
    """Comprehend Languages reads the shown script after the real page clock and physical touch."""
    node = deepcopy(tools["comprehend_srd_language_node"])
    if "_appearance_reading_script" in str(node["effects"]):
        changed["comprehend_srd_language_node"] = node
        return
    header = [
        _set("_appearance_reading_script", False),
        _set("_appearance_comprehended_message", "''"),
        _when(
            "channel == 'written' and reading_phase != 'cancel' and "
            "state.script_written_sources != null and surface_id in state.script_written_sources",
            [
                _call(
                    "observe_srd_appearance_node",
                    {
                        "observer_state": "{_actor_state_id}",
                        "subject": "{surface_id}",
                    },
                    ["_appearance_record", "_appearance_active", "_appearance_known"],
                ),
                _call(
                    "validate_srd_identify_touch",
                    {
                        "caster_state": "{_actor_state_id}",
                        "subject": "{surface_id}",
                        "touch_confirmed": True,
                    },
                ),
                _set("_appearance_reading_script", True),
                _set("_appearance_read_source", "state.script_written_sources[surface_id]"),
                _when(
                    "reading_phase == 'finish'",
                    [
                        _require(
                            "actor_state.language_reader.appearance_source == "
                            "_appearance_read_source",
                            "The written source changed during the page interval; restart reading.",
                        ),
                    ],
                ),
            ],
        ),
    ]
    tail = [
        _when(
            "_appearance_reading_script == true and reading_phase == 'start'",
            [
                _set("actor_state.language_reader.appearance_source", "_appearance_read_source"),
            ],
        ),
        _when(
            "_appearance_reading_script == true and _language_literal_understood == true",
            [
                _call(
                    "observe_srd_appearance_node",
                    {
                        "observer_state": "{_actor_state_id}",
                        "subject": "{surface_id}",
                    },
                    ["_appearance_record", "_appearance_active", "_appearance_known"],
                ),
                _set("_language_literal_understood", False),
                _when(
                    "_appearance_active == true and _appearance_record.mode == 'script'",
                    [
                        {
                            "condition": "_actor_state_id == _appearance_record.caster or "
                            "_actor_state_id in _appearance_record.readers or "
                            "_appearance_known == true",
                            "on_true": [
                                _set(
                                    "_appearance_comprehended_message",
                                    "_appearance_record.original",
                                ),
                                _set("_language_literal_understood", True),
                            ],
                            "on_false": [
                                _when(
                                    "_appearance_record.apparent != ''",
                                    [
                                        _set(
                                            "_appearance_comprehended_message",
                                            "_appearance_record.apparent",
                                        ),
                                        _set("_language_literal_understood", True),
                                    ],
                                ),
                            ],
                        },
                    ],
                ),
                _when(
                    "_appearance_active != true and "
                    "state.erased_illusory_script_surfaces[surface_id] != true",
                    [
                        _set(
                            "_appearance_comprehended_message",
                            "state.script_written_text[surface_id]",
                        ),
                        _set("_language_literal_understood", True),
                    ],
                ),
            ],
        ),
    ]
    node["effects"] = header + node["effects"] + tail
    changed["comprehend_srd_language_node"] = node
    public = deepcopy(tools["comprehend_srd_language"])
    for effect in public["effects"]:
        if effect.get("call") == "comprehend_srd_language_node":
            effect["export_context"]["_appearance_comprehended_message"] = (
                "_appearance_comprehended_message"
            )
    public["metadata"]["result_card"]["summary"].append(
        {
            "label": "Shown written message",
            "value": {"path": "context._appearance_comprehended_message"},
        }
    )
    changed["comprehend_srd_language"] = public


def _bind_cast(tools, changed):
    """Append one owned dispatch after shared costs and preserve final Slow/pending guards."""
    cast = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in names]
    args = {p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES}
    args.update(
        caster_state="{_caster_state_id}",
        spell="{spell}",
        slot_level="{_slot_level}",
        save_dc="{_casting_profile.save_dc}",
    )
    branch = _when(
        "_spell_definition.family == 'illusion_appearance'",
        [
            {
                "call": "cast_srd_appearance_spell",
                "params": args,
                "export_context": {
                    "_appearance_cast_summary": "_appearance_summary_rows",
                    "_appearance_cast_details": "_appearance_detail_rows",
                },
            },
            _loop(
                "_appearance_cast_summary",
                "_appearance_row",
                [_push("_spell_summary_rows", "_appearance_row")],
            ),
            _loop(
                "_appearance_cast_details",
                "_appearance_row",
                [_push("_spell_detail_rows", "_appearance_row")],
            ),
        ],
    )

    existing = False

    def replace(effects):
        nonlocal existing
        result = []
        for effect in effects:
            if "_spell_definition.family == 'illusion_appearance'" in effect.get("condition", ""):
                replacement = deepcopy(branch)
                replacement["condition"] = effect["condition"]
                result.append(replacement)
                existing = True
                continue
            for key in ["on_true", "on_false"]:
                if key in effect:
                    effect[key] = replace(effect[key])
            result.append(effect)
        return result

    def insert(effects):
        indexes = [
            i
            for i, effect in enumerate(effects)
            if "_spell_definition.family ==" in effect.get("condition", "")
        ]
        if indexes:
            effects.insert(indexes[-1] + 1, deepcopy(branch))
            return True
        for effect in effects:
            for key in ["on_true", "on_false"]:
                if key in effect and insert(effect[key]):
                    return True
        return False

    cast["effects"] = replace(cast["effects"])
    if not existing and not insert(cast["effects"]):
        raise ValueError("The actual spell-family dispatcher is absent")
    changed["cast_spell"] = cast


def extend_runtime(text):
    """Emit appearance records after image perception and before common source/removal consumers."""
    tools = _load_runtime(text)["tools"]
    changed = (
        _source_tools()
        | _study_tools(tools)
        | {
            "install_srd_appearance_source": _install_tool(),
            "qualify_srd_disguise_anatomy": _anatomy_tool(),
            "cast_srd_appearance_spell": _casting_tool(),
            "read_srd_apparent_subject": _perception_tool(),
            "observe_srd_appearance": _observe_tool(),
            "read_srd_illusory_script": _script_tool(),
            "inspect_srd_appearance": _inspection_tool(),
            "dismiss_srd_appearance_spell": _dismissal_tool(),
            "refresh_srd_appearance_sources": _refresh_tool(),
        }
    )
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_appearance_sources" not in str(clock["effects"]):
        clock["effects"].append(_call("refresh_srd_appearance_sources", {}))
    changed["advance_srd_lasting_clock"] = clock
    _bind_remote_perception(tools, changed)
    _bind_script_reading(tools, changed)
    _bind_cast(tools, changed)
    _normalize(changed)
    _wrap_public(changed)
    text = _replace_tools(text, changed)
    if "      illusion_appearance:" not in text:
        fields = {
            key: {
                "type": "boolean"
                if type(value) is bool
                else "integer"
                if type(value) is int
                else "string"
            }
            for key, value in PROFILES["disguise_self"].items()
            if key != "family"
        }
        schema = yaml.dump(
            {
                "illusion_appearance": {
                    "type": "object",
                    "properties": fields,
                    "required": list(fields),
                    "additionalProperties": False,
                }
            },
            Dumper=_RuntimeDumper,
            sort_keys=False,
        )
        text = text.replace(
            "      illusion_spell:",
            "".join("      " + line for line in schema.splitlines(True))
            + "\n      illusion_spell:",
            1,
        )
    return text
