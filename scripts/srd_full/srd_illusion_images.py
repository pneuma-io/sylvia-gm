"""Mental, defensive and remote images reuse the exact saved illusion source owner."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_divination_spells import _normalize
    from .srd_illusion_appearance import HIDDEN_SOURCE
    from .srd_illusion_spells import (
        CAST_CHOICES as BASE_CHOICES,
    )
    from .srd_illusion_spells import (
        PENDING,
        RECORD_EXPORTS,
        _action,
        _distance,
        _study_tool,
    )
    from .srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
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
    from srd_illusion_appearance import HIDDEN_SOURCE
    from srd_illusion_spells import (
        CAST_CHOICES as BASE_CHOICES,
    )
    from srd_illusion_spells import (
        PENDING,
        RECORD_EXPORTS,
        _action,
        _distance,
        _study_tool,
    )
    from srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
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
    "mirror_image": (150, "Mirror Image", "Mislead"),
    "phantasmal_force": ([151, 152], "Phantasmal Force", "Phantasmal Killer"),
    "project_image": ([156, 157], "Project Image", "Protection from Energy"),
}
PROFILES = {
    spell: {
        "family": "illusion_image",
        "base_level": level,
        "casting_time": "action",
        "range_feet": distance,
        "effect_rounds": rounds,
        "duration_rounds": rounds,
        "concentration": concentration,
        "cube_feet": cube,
        "illusion_mode": mode,
    }
    for spell, level, distance, rounds, concentration, cube, mode in (
        ("mirror_image", 2, 0, 10, False, 0, "duplicates"),
        ("phantasmal_force", 2, 60, 10, True, 10, "mental"),
        ("project_image", 7, 2640000, 14400, True, 0, "remote"),
    )
}
PUBLIC_TOOLS = {
    "observe_srd_phantasm",
    "study_srd_phantasm",
    "study_srd_phantasm_creature",
    "damage_srd_phantasm",
    "control_srd_project_image",
    "perceive_srd_project_image",
}
CAST_CHOICES = deepcopy(BASE_CHOICES) + [
    _optional("phantasm_dangerous", "boolean", False),
    _optional("phantasm_damage_appearance", default=""),
    _optional("project_image_location", default=""),
]


def validate_profiles(definitions):
    """Bind each fixed field to its complete pinned source, including the 500-mile conversion."""
    for spell, definition in definitions.items():
        if definition.get("family") == "illusion_image" and definition != PROFILES.get(spell):
            raise SystemExit(f"Additional image source profile differs: {spell}")


def _casting_tool():
    """Only the shared paid caster calls this installer; mental targets and locations are real."""
    effects = [
        _lookup("spell", "_image_profile"),
        _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
        _call("srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS),
        _set("_image_caster_entity", "_info_scene_entity"),
        _set("_image_origin", "_info_position"),
        _set("_image_plane", "_info_scene_entity.metadata.plane"),
        _set("_image_position", "_info_position"),
        _set("_image_size", [1, 1, 1]),
        _set("_image_target", "_caster_state_id"),
        _set("_image_install", True),
        _set("_image_save_success", False),
        _when(
            "spell == 'phantasmal_force'",
            [
                _require(
                    "target_state != null", "Phantasmal Force needs its actual creature target."
                ),
                _require("illusion_description != ''", "Describe the target's phantasm."),
                _require(
                    "not phantasm_dangerous or phantasm_damage_appearance != ''",
                    "A dangerous phantasm needs its fictional perceived damage kind.",
                ),
                _require(
                    "illusion_size[0] > 0 and illusion_size[1] > 0 and illusion_size[2] > 0 "
                    "and illusion_size[0] <= 10 and illusion_size[1] <= 10 and illusion_size[2] "
                    "<= 10",
                    "The phantasm must fit a 10-foot Cube.",
                ),
                _call(
                    "resolve_srd_scene_relation",
                    {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                    ["_scene_same_plane", "_scene_distance", "_scene_can_see"],
                ),
                _require(
                    "_scene_same_plane == true and _scene_distance <= 60 and _scene_can_see == "
                    "true",
                    "See the actual target within 60 feet on this plane.",
                ),
                _call(
                    "resolve_srd_sight",
                    {
                        "observer_state": "{_target_state_id}",
                        "distance": 0,
                        "lighting": "bright",
                        "obstruction": "clear",
                        "visual_illusion": True,
                    },
                    ["_sight_illusion_save_success"],
                ),
                {
                    "condition": "_sight_illusion_save_success == true",
                    "on_true": [_set("_image_save_success", True)],
                    "on_false": [
                        _set("_image_target_actor", None),
                        _when(
                            "target_state.creature_kind == 'character'",
                            [_set("_image_target_actor", "_target_state_id")],
                        ),
                        _call(
                            "resolve_srd_divination_save",
                            {
                                "target_state": "{_target_state_id}",
                                "actor": "{_image_target_actor}",
                                "ability": "intelligence",
                                "dc": "{save_dc}",
                            },
                            ["_divination_save_success"],
                        ),
                        _set("_image_save_success", "_divination_save_success"),
                    ],
                },
                _set("_image_install", "_image_save_success != true"),
                _set("_image_target", "_target_state_id"),
                _set("_image_position", "illusion_position"),
                _set("_image_size", "illusion_size"),
            ],
        ),
        _when(
            "spell == 'project_image'",
            [
                _require(
                    "project_image_location != ''", "Select an admitted previously seen location."
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{project_image_location}"},
                    POSITION_EXPORTS,
                ),
                _require(
                    "_info_scene_entity.metadata.seen_by != null and _caster_state_id in "
                    "_info_scene_entity.metadata.seen_by",
                    "The location needs its source-qualified prior sight fact for this caster.",
                ),
                _require(
                    "_info_scene_entity.metadata.plane == _image_plane",
                    "Project Image's distance is calculated on the caster's actual plane.",
                ),
                _distance("{_image_origin}", "{_info_position}", "_image_cast_distance"),
                _require("_image_cast_distance <= 2640000", "The location exceeds 500 miles."),
                _set("_image_position", "_info_position"),
                {
                    "condition": "_image_caster_entity.footprint.kind == 'box'",
                    "on_true": [
                        _set(
                            "_image_size",
                            [
                                "_image_caster_entity.footprint.half_extents[0] * 2",
                                "_image_caster_entity.footprint.half_extents[1] * 2",
                                "_image_caster_entity.height",
                            ],
                        )
                    ],
                    "on_false": [
                        _set(
                            "_image_size",
                            [
                                "_image_caster_entity.footprint.radius * 2",
                                "_image_caster_entity.footprint.radius * 2",
                                "_image_caster_entity.height",
                            ],
                        )
                    ],
                },
            ],
        ),
        _when(
            "_image_profile.concentration == true and caster_state.concentration_spell_id != null",
            [
                _call(
                    "end_srd_concentration_node",
                    {
                        "source_state": "{_caster_state_id}",
                        "target_state": "{caster_state.concentration_target_state_id}",
                    },
                )
            ],
        ),
        _set("_image_summary_rows", []),
        _set("_image_detail_rows", []),
        _when(
            "spell == 'phantasmal_force'",
            [
                _push(
                    "_image_summary_rows",
                    {"label": "'Save succeeded'", "value": "_image_save_success"},
                ),
                _push(
                    "_image_detail_rows",
                    {"label": "'Intelligence save succeeded'", "value": "_image_save_success"},
                ),
            ],
        ),
        _when("_image_install == true", _installation_effects()),
    ]
    return _tool_scene(
        "Install the paid defensive, target-only mental or remote image source.",
        [
            _entity("caster_state"),
            _optional("target_state", default=None, resolve="entity"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer"),
            _parameter("concentration_modifier", "integer"),
            *deepcopy(CAST_CHOICES),
            *deepcopy(HIDDEN_SOURCE),
        ],
        effects,
    )


def _installation_effects():
    """Reuse exact source clocks; defensive and mental images have no scene copy."""
    return [
        _when(
            "state.illusion_spell_sources == null",
            [_set("state.illusion_spell_sources", {}), _set("state.illusion_spell_source_ids", [])],
        ),
        _require(
            "len(state.illusion_spell_source_ids) < 64", "The saved illusion source bound is full."
        ),
        _when("state.illusion_cast_serial == null", [_set("state.illusion_cast_serial", 0)]),
        _set("state.illusion_cast_serial", "state.illusion_cast_serial + 1"),
        _call(
            "record_srd_result_text",
            {"text": "{_caster_state_id}:{spell}:{state.illusion_cast_serial}"},
            ["_recorded_result_text"],
        ),
        _set("_image_id", "_recorded_result_text"),
        _set("_image_proxy_id", "''"),
        _when("spell == 'project_image'", [_set("_image_proxy_id", "_image_id")]),
        _set("_image_source_owner", "_caster_state_id"),
        _when("spell_cast_owner != null", [_set("_image_source_owner", "_spell_cast_owner_id")]),
        _set("_image_level", "slot_level"),
        _when("actual_spell_level >= 0", [_set("_image_level", "actual_spell_level")]),
        _set(
            "_image_record",
            {
                "id": "_image_id",
                "spell": "spell",
                "caster": "_caster_state_id",
                "source_caster_id": "_image_source_owner",
                "cast_id": "spell_cast_id",
                "spell_level": "_image_level",
                "ongoing_duration": True,
                "dispel_ongoing": True,
                "installed": True,
                "started_tick": "state.lasting_tick",
                "expires_tick": "state.lasting_tick + _image_profile.duration_rounds * 2",
                "concentration": "_image_profile.concentration",
                "proxy_id": "_image_proxy_id",
                "save_dc": "save_dc",
                "recognized": [],
                "mode": "_image_profile.illusion_mode",
                "subject_kind": "'creature'",
                "opaque": "spell == 'project_image'",
                "description": "illusion_description",
                "sound": "illusion_sound",
                "sound_volume": "'normal'",
                "smell": "illusion_smell",
                "temperature": "illusion_temperature",
                "trigger": "''",
                "trigger_modality": "'visual'",
                "activated_tick": None,
                "performance_rounds": 0,
                "invisible_active": False,
                "lighting": "illusion_lighting",
                "target": "_image_target",
                "position": "_image_position",
                "plane": "_image_plane",
                "size": "_image_size",
                "dangerous": "spell == 'phantasmal_force' and phantasm_dangerous == true",
                "apparent_damage_kind": "phantasm_damage_appearance",
                "remaining_duplicates": 3,
                "damage_turn_key": "''",
            },
        ),
        _when(
            "spell == 'mirror_image' or spell == 'project_image'",
            [
                _set(
                    "_image_body_patch",
                    {"description": "caster_state.name", "sound": "caster_state.name"},
                ),
                _set("_image_record", "merge(_image_record, _image_body_patch)"),
            ],
        ),
        _set("state.illusion_spell_sources.{_image_id}", "_image_record"),
        _set("_image_ids", "state.illusion_spell_source_ids"),
        _push("_image_ids", "_image_id"),
        _set("state.illusion_spell_source_ids", "_image_ids"),
        _set("state.has_illusion_spells", True),
        _when(
            "_image_profile.concentration == true",
            [
                _set("caster_state.concentration_spell_id", "spell"),
                _set("caster_state.concentration_effect_family", "'illusion_spell'"),
                _set("caster_state.concentration_target_state_id", "_image_target"),
                _set("caster_state.concentration_target_state_ids", ["_image_target"]),
                _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                _set("caster_state.concentration_expires_tick", "_image_record.expires_tick"),
                _set("caster_state.concentration_save_modifier", "concentration_modifier"),
                _set("caster_state.illusion_concentration_id", "_image_id"),
            ],
        ),
        _when(
            "spell == 'project_image'",
            [
                _call(
                    "create_srd_illusion_proxy",
                    {
                        "illusion_id": "{_image_id}",
                        "caster_state": "{_caster_state_id}",
                        "position": "{_image_position}",
                        "size": "{_image_size}",
                        "plane": "{_image_plane}",
                        "spell": "{spell}",
                    },
                )
            ],
        ),
        _push(
            "_image_summary_rows", {"label": "'Effect'", "value": "'Source-owned image installed'"}
        ),
        _push(
            "_image_detail_rows", {"label": "'Effect'", "value": "'Source-owned image installed'"}
        ),
        _push("_image_detail_rows", {"label": "'Illusion source'", "value": "_image_id"}),
    ]


def _mirror_tool():
    """A retained genuine hit rolls once per remaining duplicate without a new attack or action."""
    return _tool_scene(
        "Resolve Mirror Image only after an actual final attack-roll hit, before target damage.",
        [_entity("target_state"), _entity("attacker_state")],
        [
            _set("_mirror_redirected", False),
            _set("_mirror_dice", []),
            _set("_mirror_source_id", "''"),
            _set("_mirror_remaining", 0),
            _when(
                "state.has_illusion_spells == true",
                [
                    _set("_mirror_ids", "state.illusion_spell_source_ids"),
                    _loop(
                        "_mirror_ids",
                        "_mirror_id",
                        [
                            _set("_mirror_candidate", "state.illusion_spell_sources[_mirror_id]"),
                            _when(
                                "_mirror_candidate.spell == 'mirror_image' and "
                                "_mirror_candidate.target == _target_state_id",
                                [
                                    _call(
                                        "collect_srd_illusion_source",
                                        {"illusion_id": "{_mirror_id}"},
                                        RECORD_EXPORTS,
                                    ),
                                    _when(
                                        "_illusion_active == true",
                                        [
                                            _set("_mirror_source_id", "_mirror_id"),
                                            _set("_mirror_record", "_illusion_record"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_mirror_source_id != ''",
                        [
                            _set("_mirror_remaining", "_mirror_record.remaining_duplicates"),
                            _call(
                                "resolve_srd_timed_senses",
                                {"target_state": "{_attacker_state_id}"},
                                ["_sense_truesight"],
                            ),
                            _set(
                                "_mirror_bypass",
                                "'blinded' in attacker_state.conditions or _sense_truesight > 0 "
                                "or (attacker_state.blindsight_feet != null and "
                                "attacker_state.blindsight_feet > 0)",
                            ),
                            _when(
                                "_mirror_bypass != true",
                                [
                                    _set("_mirror_roll_indexes", [1, 2, 3]),
                                    _loop(
                                        "_mirror_roll_indexes",
                                        "_mirror_roll_index",
                                        [
                                            _when(
                                                "_mirror_roll_index <= _mirror_remaining",
                                                [
                                                    {
                                                        "dice": {"expression": "1d6"},
                                                        "store_as": "_mirror_die",
                                                    },
                                                    _push("_mirror_dice", "_mirror_die"),
                                                    _when(
                                                        "_mirror_die.total >= 3",
                                                        [_set("_mirror_redirected", True)],
                                                    ),
                                                ],
                                            )
                                        ],
                                    ),
                                    _when(
                                        "_mirror_redirected == true",
                                        [
                                            _set("_mirror_remaining", "_mirror_remaining - 1"),
                                            _set(
                                                "state.illusion_spell_sources.{_mirror_source_id}.remaining_duplicates",
                                                "_mirror_remaining",
                                            ),
                                            _when(
                                                "_mirror_remaining == 0",
                                                [
                                                    _call(
                                                        "retire_srd_illusion_source",
                                                        {"illusion_id": "{_mirror_source_id}"},
                                                    )
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _phantasm_tools(tools):
    """The target's Study can end its mental source; physical mismatch does not reveal it."""
    perceive = _tool_scene(
        "Deliver the mental illusion only to its actual affected target.",
        [_entity("observer_state"), _parameter("illusion_id")],
        [
            _call("collect_srd_illusion_source", {"illusion_id": "{illusion_id}"}, RECORD_EXPORTS),
            _require(
                "_illusion_active == true and _illusion_record.spell == 'phantasmal_force' "
                "and _illusion_record.target == _observer_state_id",
                "Only the affected target can perceive its active phantasm.",
            ),
            _set("_illusion_can_see", True),
            _set("_illusion_can_hear", "_illusion_record.sound != ''"),
            _set("_phantasm_appearance", "_illusion_record.description"),
            _set("_phantasm_sound", "_illusion_record.sound"),
            _set("_phantasm_temperature", "_illusion_record.temperature"),
            _set("_phantasm_belief", "'Treated as real; physical contradictions are rationalized'"),
        ],
    )
    study = _study_tool(tools)
    study["effects"][0] = _call(
        "resolve_srd_phantasm_perception",
        {"observer_state": "{_observer_state_id}", "illusion_id": "{illusion_id}"},
        ["_illusion_record", "_illusion_can_see", "_illusion_can_hear"],
    )
    success = study["effects"][-1]
    success["on_true"] = [_call("retire_srd_illusion_source", {"illusion_id": "{illusion_id}"})]
    npc = deepcopy(study)
    npc["parameters"] = [
        p
        for p in npc["parameters"]
        if p["name"] not in {"observer", "illusion_skill", "illusion_proficiency_factor"}
    ]
    npc["parameters"][0].pop("model_derive", None)
    npc["effects"].insert(0, _set("observer", None))
    npc["effects"].insert(0, _set("_observer_id", None))
    observe = _public(
        _tool_scene(
            "Receive only this creature's source-owned mental illusion and rationalized stimuli.",
            [_entity("observer_state"), _parameter("illusion_id")],
            [
                _call(
                    "resolve_srd_phantasm_perception",
                    {"observer_state": "{_observer_state_id}", "illusion_id": "{illusion_id}"},
                    [
                        "_phantasm_appearance",
                        "_phantasm_sound",
                        "_phantasm_temperature",
                        "_phantasm_belief",
                    ],
                )
            ],
        ),
        "exploration",
        "Phantasm",
        [
            ("Appearance", "_phantasm_appearance"),
            ("Sound", "_phantasm_sound"),
            ("Temperature", "_phantasm_temperature"),
            ("Belief", "_phantasm_belief"),
        ],
    )
    return {
        "resolve_srd_phantasm_perception": perceive,
        "observe_srd_phantasm": observe,
        "study_srd_phantasm": study,
        "study_srd_phantasm_creature": npc,
        "damage_srd_phantasm": _phantasm_damage_tool(),
    }


def _phantasm_damage_tool():
    """Actual turn identity and calculated distance qualify one optional Psychic packet per turn."""
    return _public(
        _tool_scene(
            "Deal the source's optional damage once on its caster's actual turn when within five "
            "feet.",
            [_entity("caster_state"), _parameter("illusion_id")],
            [
                _require(PENDING, "Resolve the pending action before ordinary spell activity."),
                _call(
                    "collect_srd_illusion_source", {"illusion_id": "{illusion_id}"}, RECORD_EXPORTS
                ),
                _require(
                    "_illusion_active == true and _illusion_record.spell == 'phantasmal_force' "
                    "and _illusion_record.caster == _caster_state_id and "
                    "_illusion_record.dangerous == true",
                    "Select this caster's active dangerous phantasm.",
                ),
                _require(
                    "combat.encounter_active == true and combat.turn_active == true and "
                    "combat.active_actor == _caster_state_id",
                    "The printed damage applies only on the caster's actual turn.",
                ),
                _call(
                    "record_srd_result_text",
                    {"text": "{combat.round}:{combat.turn_index}:{_caster_state_id}"},
                    ["_recorded_result_text"],
                ),
                _set("_phantasm_turn_key", "_recorded_result_text"),
                _require(
                    "_phantasm_turn_key != _illusion_record.damage_turn_key",
                    "This phantasm already dealt damage on this turn.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_illusion_record.target}"},
                    POSITION_EXPORTS,
                ),
                _require(
                    "_info_scene_entity.metadata.plane == _illusion_record.plane",
                    "The target is no longer on the phantasm's plane.",
                ),
                _call(
                    "measure_srd_phantasm_target_distance",
                    {"illusion_id": "{illusion_id}"},
                    ["_phantasm_distance"],
                ),
                _require(
                    "_phantasm_distance <= 5",
                    "The target must be in or within five feet of the actual phantasm volume.",
                ),
                _call(
                    "read_srd_phantasm_target_vitals",
                    {"target_state": "{_illusion_record.target}"},
                    ["_phantasm_vitals"],
                ),
                _set("_phantasm_before", "_phantasm_vitals"),
                {"dice": {"expression": "2d8"}, "store_as": "_phantasm_damage"},
                _set("_phantasm_adjusted_raw", "_phantasm_damage.total"),
                _when(
                    "caster_state.has_actor_spell_modifiers == true",
                    [
                        _call(
                            "refresh_srd_timed_condition_target",
                            {"target_state": "{_caster_state_id}", "lasting_state": "state"},
                        ),
                        _call(
                            "modify_srd_actor_raw_damage_roll",
                            {
                                "actor_state": "{_caster_state_id}",
                                "lasting_state": "state",
                                "amount": "{_phantasm_damage.total}",
                            },
                            ["_ray_raw_damage"],
                        ),
                        _set("_phantasm_adjusted_raw", "_ray_raw_damage"),
                    ],
                ),
                _call(
                    "apply_srd_qualifying_damage",
                    {
                        "target": "{_illusion_record.target}",
                        "amount": "{max(0, _phantasm_adjusted_raw)}",
                        "adjustment": 0,
                        "damage_type": "psychic",
                        "source_state_id": "{_caster_state_id}",
                        "unallocated_roll_penalty": "{max(0, -_phantasm_adjusted_raw)}",
                        "game_damage_roll": True,
                    },
                ),
                _call(
                    "read_srd_phantasm_target_vitals",
                    {"target_state": "{_illusion_record.target}"},
                    ["_phantasm_vitals"],
                ),
                _set("_phantasm_loss", "max(0, _phantasm_before - _phantasm_vitals)"),
                _set(
                    "state.illusion_spell_sources.{illusion_id}.damage_turn_key",
                    "_phantasm_turn_key",
                ),
                _set("_phantasm_damage_kind", "_illusion_record.apparent_damage_kind"),
            ],
        ),
        "combat",
        "Phantasmal damage",
        [
            ("Actual HP and Temp HP lost", "_phantasm_loss"),
            ("Perceived damage kind", "_phantasm_damage_kind"),
        ],
    )


def _project_tools(tools):
    """Actual intangible proxy movement and current caster senses reuse the existing scene owner."""
    perceive = deepcopy(tools["perceive_srd_mislead_proxy"])

    def adapt(value):
        if isinstance(value, str):
            return value.replace("record.spell == 'mislead'", "record.spell == 'project_image'")
        if isinstance(value, list):
            return [adapt(item) for item in value]
        if isinstance(value, dict):
            result = {key: adapt(item) for key, item in value.items()}
            if result.get("call") == "check_srd_illusion_visibility":
                result["params"]["source_illusion_id"] = "{record.proxy_id}"
            return result
        return value

    perceive = adapt(perceive)
    public = adapt(
        deepcopy(
            tools.get("perceive_srd_mislead_double_node", tools["perceive_srd_mislead_double"])
        )
    )
    for effect in public["effects"]:
        if "condition" in effect:
            effect["condition"] = effect["condition"].replace("'mislead'", "'project_image'")
        if effect.get("call") == "perceive_srd_mislead_proxy":
            effect["call"] = "perceive_srd_project_image_proxy"
    public = _public(
        public,
        "exploration",
        "Project Image senses",
        [("Visible subjects", "_info_visible"), ("Heard subjects", "_info_heard")],
    )
    control = _public(
        _tool_scene(
            "Spend the caster's real Magic action to move its copy up to 60 feet and animate it.",
            [
                _entity("caster_state"),
                _parameter("illusion_id"),
                _array("destination"),
                _optional("gesture", default=""),
                _optional("speech", default=""),
            ],
            [
                _call(
                    "collect_srd_illusion_source", {"illusion_id": "{illusion_id}"}, RECORD_EXPORTS
                ),
                _require(
                    "_illusion_active == true and _illusion_record.spell == 'project_image' and "
                    "_illusion_record.caster == _caster_state_id",
                    "Control only this caster's active Project Image.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_illusion_record.proxy_id}"},
                    POSITION_EXPORTS,
                ),
                _distance("{_info_position}", "{destination}", "_project_moved_distance"),
                _require(
                    "_project_moved_distance <= 60",
                    "A Project Image Magic action moves at most 60 feet.",
                ),
                *_action(),
                {
                    "spatial_mutation": {
                        "scene": "state.spatial_scene",
                        "request": {
                            "map_id": "{state.spatial_scene.map.id}",
                            "expected_revision": "{state.spatial_scene.map.revision}",
                            "target": {"kind": "entity", "id": "{_illusion_record.proxy_id}"},
                            "patch": {"position": "{destination}"},
                        },
                    },
                    "store_as": "_project_mutation",
                },
                _set("state.illusion_spell_sources.{illusion_id}.gesture", "gesture"),
                _set("state.illusion_spell_sources.{illusion_id}.speech", "speech"),
                _set("_project_position", "destination"),
                _set("_project_mannerisms", "'Perfectly mimics its caster'"),
            ],
        ),
        "exploration",
        "Project Image controlled",
        [("Position", "_project_position"), ("Mannerisms", "_project_mannerisms")],
    )
    damage = _tool_scene(
        "Retire only an actual Project Image proxy after its owning damage consumer reports "
        "damage.",
        [_parameter("subject_id"), _parameter("effective_damage", "number")],
        [
            _set("_project_damage_ended", False),
            _when(
                "effective_damage > 0 and state.has_illusion_spells == true",
                [
                    _set("_project_damage_ids", "state.illusion_spell_source_ids"),
                    _loop(
                        "_project_damage_ids",
                        "_project_damage_id",
                        [
                            _set(
                                "_project_damage_record",
                                "state.illusion_spell_sources[_project_damage_id]",
                            ),
                            _when(
                                "_project_damage_record.spell == 'project_image' and "
                                "_project_damage_record.proxy_id == subject_id",
                                [
                                    _call(
                                        "collect_srd_illusion_source",
                                        {"illusion_id": "{_project_damage_id}"},
                                        RECORD_EXPORTS,
                                    ),
                                    _when(
                                        "_illusion_active == true",
                                        [
                                            _call(
                                                "retire_srd_illusion_source",
                                                {"illusion_id": "{_project_damage_id}"},
                                            ),
                                            _set("_project_damage_ended", True),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "perceive_srd_project_image_proxy": perceive,
        "perceive_srd_project_image": public,
        "control_srd_project_image": control,
        "end_srd_project_image_damage": damage,
    }


def _phantasm_distance_tool():
    """Measure separation from the phantom volume to the target's admitted body, not its center."""
    return _tool_scene(
        "Calculate the target body's Euclidean distance from the saved phantom Cube.",
        [_parameter("illusion_id")],
        [
            _call("collect_srd_illusion_source", {"illusion_id": "{illusion_id}"}, RECORD_EXPORTS),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_illusion_record.target}"},
                POSITION_EXPORTS,
            ),
            _set(
                "_phantasm_min",
                [
                    "_illusion_record.position[0] - _illusion_record.size[0] * 0.5",
                    "_illusion_record.position[1] - _illusion_record.size[1] * 0.5",
                    "_illusion_record.position[2]",
                ],
            ),
            _set(
                "_phantasm_max",
                [
                    "_illusion_record.position[0] + _illusion_record.size[0] * 0.5",
                    "_illusion_record.position[1] + _illusion_record.size[1] * 0.5",
                    "_illusion_record.position[2] + _illusion_record.size[2]",
                ],
            ),
            _set("_phantasm_z", "_info_position[2]"),
            _set("_phantasm_zmax", "_phantasm_z + _info_scene_entity.height"),
            _set(
                "_phantasm_vertical_gap",
                "max(0, _phantasm_min[2] - _phantasm_zmax, _phantasm_z - _phantasm_max[2])",
            ),
            {
                "condition": "_info_scene_entity.footprint.kind == 'box'",
                "on_true": [
                    _set(
                        "_phantasm_body_gaps",
                        [
                            "max(0, _phantasm_min[0] - _info_position[0] - "
                            "_info_scene_entity.footprint.half_extents[0], _info_position[0] - "
                            "_info_scene_entity.footprint.half_extents[0] - _phantasm_max[0])",
                            "max(0, _phantasm_min[1] - _info_position[1] - "
                            "_info_scene_entity.footprint.half_extents[1], _info_position[1] - "
                            "_info_scene_entity.footprint.half_extents[1] - _phantasm_max[1])",
                        ],
                    ),
                    _set(
                        "_phantasm_horizontal_gap",
                        "(_phantasm_body_gaps[0] ** 2 + _phantasm_body_gaps[1] ** 2) ** 0.5",
                    ),
                ],
                "on_false": [
                    _set(
                        "_phantasm_center_gaps",
                        [
                            "max(0, _phantasm_min[0] - _info_position[0], _info_position[0] - "
                            "_phantasm_max[0])",
                            "max(0, _phantasm_min[1] - _info_position[1], _info_position[1] - "
                            "_phantasm_max[1])",
                        ],
                    ),
                    _set(
                        "_phantasm_center_distance",
                        "(_phantasm_center_gaps[0] ** 2 + _phantasm_center_gaps[1] ** 2) ** 0.5",
                    ),
                    _set(
                        "_phantasm_horizontal_gap",
                        "max(0, _phantasm_center_distance - _info_scene_entity.footprint.radius)",
                    ),
                ],
            },
            _set(
                "_phantasm_distance",
                "(_phantasm_horizontal_gap ** 2 + _phantasm_vertical_gap ** 2) ** 0.5",
            ),
        ],
    )


def _bind_existing_owner(tools, changed):
    """Proxy-free mental/defensive sources do not pretend to be physical scene volumes."""
    retire = deepcopy(tools["retire_srd_illusion_source"])
    for index, effect in enumerate(retire["effects"]):
        if effect.get("call") == "deactivate_srd_information_proxy":
            retire["effects"][index] = _when("_illusion_record.proxy_id != ''", [effect])
    changed["retire_srd_illusion_source"] = retire
    create = deepcopy(tools["create_srd_illusion_proxy"])
    for effect in create["effects"]:
        value = effect.get("set", {}).get("value")
        if (
            isinstance(value, dict)
            and "metadata" in value
            and "illusion_proxy" in value["metadata"]
        ):
            value["metadata"]["invulnerable"] = "spell != 'project_image'"
    changed["create_srd_illusion_proxy"] = create
    volume = deepcopy(tools["resolve_srd_illusion_volume"])
    if "Physical image volume only" not in str(volume["effects"]):
        volume["effects"].insert(
            0,
            _require(
                "state.illusion_spell_sources[illusion_id].proxy_id != ''",
                "Physical image volume only; a mental image or Mirror Image is not a scene object.",
            ),
        )
    rebound = _tool_scene(volume["description"], volume["parameters"], volume["effects"])
    rebound["metadata"] = volume["metadata"]
    rebound["pre_conditions"] = volume["pre_conditions"]
    changed["resolve_srd_illusion_volume"] = rebound
    crossing = deepcopy(tools["interact_srd_illusions_during_movement"])
    perception = deepcopy(tools["resolve_srd_illusion_perception"])

    def adapt(value):
        if isinstance(value, list):
            return [adapt(item) for item in value]
        if isinstance(value, dict):
            output = {key: adapt(item) for key, item in value.items()}
            condition = output.get("condition", "")
            if (
                "_illusion_perceptible == true and _illusion_record.spell !=" in condition
                and "proxy_id" not in condition
            ):
                output["condition"] = "_illusion_record.proxy_id != '' and (" + condition + ")"
            if condition == (
                "_illusion_recognized == true and _illusion_record.spell == 'programmed_illusion'"
            ):
                output["condition"] = (
                    "_illusion_recognized == true and (_illusion_record.spell == "
                    "'programmed_illusion' or _illusion_record.spell == 'project_image')"
                )
            return output
        return value

    changed["interact_srd_illusions_during_movement"] = adapt(crossing)
    changed["resolve_srd_illusion_perception"] = adapt(perception)
    observe_name = (
        "observe_srd_illusion_node"
        if "observe_srd_illusion_node" in tools
        else "observe_srd_illusion"
    )
    changed[observe_name] = adapt(deepcopy(tools[observe_name]))
    sight = deepcopy(tools["check_srd_illusion_visibility"])
    if not any(p["name"] == "source_illusion_id" for p in sight["parameters"]):
        sight["parameters"].append(_optional("source_illusion_id", default=""))

    def exclude_source(value):
        if isinstance(value, list):
            return [exclude_source(item) for item in value]
        if isinstance(value, dict):
            result = {key: exclude_source(item) for key, item in value.items()}
            condition = result.get("condition", "")
            if (
                "_illusion_sight_hit.metadata.illusion_proxy != subject_illusion_id" in condition
                and "source_illusion_id" not in condition
            ):
                result["condition"] = (
                    condition
                    + " and _illusion_sight_hit.metadata.illusion_proxy != source_illusion_id"
                )
            return result
        return value

    changed["check_srd_illusion_visibility"] = exclude_source(sight)


def _bind_cast(tools, changed):
    """Replace only this family's branch in place so repeated generation preserves final guards."""
    cast = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in names]
    args = {p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES}
    args.update(
        caster_state="{_caster_state_id}",
        target_state="{_target_id}",
        spell="{spell}",
        slot_level="{_slot_level}",
        save_dc="{_casting_profile.save_dc}",
        concentration_modifier="{_casting_profile.concentration_save_modifier}",
    )
    branch = _when(
        "_spell_definition.family == 'illusion_image'",
        [
            _call("cast_srd_image_spell", args, ["_image_summary_rows", "_image_detail_rows"]),
            _loop(
                "_image_summary_rows", "_image_row", [_push("_spell_summary_rows", "_image_row")]
            ),
            _loop("_image_detail_rows", "_image_row", [_push("_spell_detail_rows", "_image_row")]),
        ],
    )
    replaced = False

    def replace(effects):
        nonlocal replaced
        output = []
        for effect in effects:
            if "_spell_definition.family == 'illusion_image'" in effect.get("condition", ""):
                new = deepcopy(branch)
                new["condition"] = effect["condition"]
                output.append(new)
                replaced = True
                continue
            for key in ("on_true", "on_false"):
                if key in effect:
                    effect[key] = replace(effect[key])
            output.append(effect)
        return output

    def insert(effects):
        indexes = [
            i
            for i, effect in enumerate(effects)
            if "_spell_definition.family ==" in effect.get("condition", "")
        ]
        if indexes:
            effects.insert(indexes[-1] + 1, deepcopy(branch))
            return True
        return any(
            insert(effect[key])
            for effect in effects
            for key in ("on_true", "on_false")
            if key in effect
        )

    cast["effects"] = replace(cast["effects"])
    if not replaced and not insert(cast["effects"]):
        raise ValueError("The actual spell family dispatcher is absent")
    changed["cast_spell"] = cast


def extend_runtime(text):
    """Emit three image consumers after the shared illusion/appearance owners."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "cast_srd_image_spell": _casting_tool(),
        "resolve_srd_mirror_image_hit": _mirror_tool(),
        "measure_srd_phantasm_target_distance": _phantasm_distance_tool(),
        "read_srd_phantasm_target_vitals": _tool_scene(
            "Read actual remaining HP plus Temp HP after the final damage callbacks.",
            [_entity("target_state")],
            [
                _set("_phantasm_vitals", "target_state.hp"),
                _when(
                    "target_state.temp_hp != null",
                    [_set("_phantasm_vitals", "_phantasm_vitals + target_state.temp_hp")],
                ),
            ],
        ),
        **_phantasm_tools(tools),
        **_project_tools(tools),
    }
    _bind_existing_owner(tools, changed)
    _bind_cast(tools, changed)
    _normalize(changed)
    for tool in changed.values():
        for effect in tool["effects"]:
            mutation = effect.get("spatial_mutation")
            if mutation and "request" in mutation:
                effect["spatial_mutation"] = {"scene": mutation["scene"], **mutation["request"]}
    # The established wrapper walks only its own public set; apply the same direct contract here.
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
                if p.get("resolve") in {"entity", "participant"}
                else p["name"]
            )
            + "}"
            for p in tool["parameters"]
        }
        if any(p["name"] == "lasting_state" for p in node["parameters"]):
            args["lasting_state"] = "state"
        exports = [
            row["value"]["path"].removeprefix("context.")
            for row in tool["metadata"]["result_card"]["summary"]
        ]
        tool["effects"] = [_call(name + "_node", args, exports)]
    text = _replace_tools(text, changed)
    if "      illusion_image:" not in text:
        properties = {
            key: {
                "type": "boolean"
                if type(value) is bool
                else "integer"
                if type(value) is int
                else "string"
            }
            for key, value in PROFILES["mirror_image"].items()
            if key != "family"
        }
        schema = yaml.dump(
            {
                "illusion_image": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
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
