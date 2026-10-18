"""Dream keeps actual trance, sleeping dialogue and delayed nightmare consequences separate."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_divination_spells import _normalize
    from .srd_illusion_appearance import HIDDEN_SOURCE
    from .srd_illusion_spells import PENDING
    from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
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
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
else:
    from srd_divination_spells import _normalize
    from srd_illusion_appearance import HIDDEN_SOURCE
    from srd_illusion_spells import PENDING
    from srd_scene_relations import POSITION_EXPORTS, _tool_scene
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
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools

SOURCE = {"dream": (126, "Dream", "Druidcraft")}
PROFILES = {
    "dream": {
        "family": "dream_spell",
        "base_level": 5,
        "casting_time": "1_minute",
        "range_kind": "special",
        "duration_rounds": 4800,
        "concentration": False,
    }
}
PUBLIC_TOOLS = {"converse_srd_dream", "end_srd_dream_trance"}
CAST_CHOICES = [
    _optional("dream_messenger", default=None, resolve="entity"),
    _optional("dream_messenger_willing", "boolean", False),
    _optional("dream_terrifying", "boolean", False),
    _optional("dream_message_words", "array", [], items={"type": "string"}, max_items=10),
    _optional("dream_environment", default=""),
]


def validate_profiles(definitions):
    """Require the exact printed level, casting time, range and independent eight-hour lifetime."""
    for spell, definition in definitions.items():
        if definition.get("family") == "dream_spell" and definition != PROFILES.get(spell):
            raise SystemExit(f"Dream profile differs from pinned source: {spell}")


def _source(id_expression="dream_id"):
    return [
        _require(
            "state.dream_spell_sources != null and "
            + id_expression
            + " in state.dream_spell_sources",
            "Select an actual saved Dream source.",
        ),
        _set("_dream_record", "state.dream_spell_sources[" + id_expression + "]"),
    ]


def _sleep_tool():
    """Unconscious alone is insufficient: use actual rest sleep or qualified spell sleep sources."""
    return _tool_scene(
        "Derive actual sleep from the existing rest or active sleeping condition owners.",
        [_entity("target_state")],
        [
            _set(
                "_dream_asleep",
                "target_state.rest_added_unconscious == true and "
                "target_state.rest_phase == 'active' and target_state.rest_activity == 'sleep' "
                "and target_state.hp > 0",
            ),
            _when(
                "target_state.timed_condition_source_ids != null",
                [
                    _call(
                        "refresh_srd_timed_condition_target",
                        {"target_state": "{_target_state_id}", "lasting_state": "state"},
                    ),
                    _set("_dream_sleep_sources", "target_state.timed_condition_source_ids"),
                    _loop(
                        "_dream_sleep_sources",
                        "_dream_sleep_source_id",
                        [
                            _set(
                                "_dream_sleep_source",
                                "state.timed_condition_sources[_dream_sleep_source_id]",
                            ),
                            _when(
                                "_dream_sleep_source.installed == true and "
                                "_dream_sleep_source.target_state_id == _target_state_id and "
                                "_dream_sleep_source.condition == 'unconscious' and "
                                "(_dream_sleep_source.control_profile == 'sleep' or "
                                "_dream_sleep_source.control_profile == 'eyebite_sleep') and "
                                "_dream_sleep_source.stage == 'sleeping' and "
                                "state.lasting_tick < _dream_sleep_source.expires_tick",
                                [_set("_dream_asleep", True)],
                            ),
                        ],
                    ),
                ],
            ),
            _set(
                "_dream_asleep",
                "_dream_asleep == true and target_state.is_alive == true and target_state.hp > 0",
            ),
        ],
    )


def _trance_tools():
    """Retire exact Dream conditions and derive Speed 0 from live messenger sources."""
    project = _tool_scene(
        "Derive the messenger's effective Dream Speed 0 without overwriting native speeds.",
        [_entity("messenger_state")],
        [
            _set("messenger_state.dream_trance_speed_zero", False),
            _set("_dream_remaining_messenger_ids", []),
            _when(
                "messenger_state.dream_messenger_source_ids != null",
                [
                    _set("_dream_messenger_ids", "messenger_state.dream_messenger_source_ids"),
                    _loop(
                        "_dream_messenger_ids",
                        "_dream_messenger_id",
                        [
                            _set(
                                "_dream_messenger_source",
                                "state.dream_spell_sources[_dream_messenger_id]",
                            ),
                            _when(
                                "_dream_messenger_source.active == true and "
                                "_dream_messenger_source.messenger == _messenger_state_id and "
                                "state.lasting_tick < _dream_messenger_source.expires_tick",
                                [
                                    _set("messenger_state.dream_trance_speed_zero", True),
                                    _push("_dream_remaining_messenger_ids", "_dream_messenger_id"),
                                ],
                            ),
                        ],
                    ),
                    _set(
                        "messenger_state.dream_messenger_source_ids",
                        "_dream_remaining_messenger_ids",
                    ),
                ],
            ),
        ],
    )
    end = _tool_scene(
        "End this exact messenger trance while retaining any pending target wake consequence.",
        [_parameter("dream_id"), _parameter("reason")],
        _source()
        + [
            _set("state.dream_spell_sources.{dream_id}.active", False),
            _set("state.dream_spell_sources.{dream_id}.ended_reason", "reason"),
            _when(
                "_dream_record.condition_source_id in state.timed_condition_sources and "
                "state.timed_condition_sources[_dream_record.condition_source_id].spell_id == "
                "'dream' and "
                "state.timed_condition_sources[_dream_record.condition_source_id].target_state_"
                "id == "
                "_dream_record.messenger and "
                "state.timed_condition_sources[_dream_record.condition_source_id].dream_source_"
                "id == "
                "dream_id",
                [
                    _call(
                        "retire_srd_timed_condition_source",
                        {
                            "lasting_state": "state",
                            "source_id": "{_dream_record.condition_source_id}",
                        },
                    ),
                    _call(
                        "refresh_srd_timed_condition_target",
                        {"target_state": "{_dream_record.messenger}", "lasting_state": "state"},
                    ),
                ],
            ),
            _call("project_srd_dream_trance", {"messenger_state": "{_dream_record.messenger}"}),
            _call("refresh_srd_dream_registry", {}),
        ],
    )
    emerge = _public(
        _tool_scene(
            "Emerge at any time from the actor's actual trance; no action is required by Dream.",
            [_entity("messenger_state"), _parameter("dream_id")],
            [
                _require(PENDING, "Resolve the pending action before ending the trance."),
                _require(
                    "messenger_state.is_alive == true and messenger_state.hp > 0 and "
                    "'unconscious' not in messenger_state.conditions",
                    "The living conscious messenger chooses to emerge.",
                ),
                *_source(),
                _require(
                    "_dream_record.active == true and _dream_record.messenger == "
                    "_messenger_state_id",
                    "Only this actual messenger can emerge from its trance.",
                ),
                _call("retire_srd_dream_source", {"dream_id": "{dream_id}", "reason": "emerged"}),
                _set("_dream_ended", True),
            ],
        ),
        "exploration",
        "Dream trance ended",
        [("Ended", "_dream_ended")],
    )
    return {
        "project_srd_dream_trance": project,
        "retire_srd_dream_source": end,
        "end_srd_dream_trance": emerge,
    }


def _save_tool():
    """A terrifying message causes one actual Wisdom save only after entering genuine sleep."""
    return _tool_scene(
        "Deliver the saved terrifying message and resolve the target's actual Wisdom save.",
        [_entity("target_state"), _parameter("dream_id")],
        _source()
        + [
            _require(
                "_dream_record.target == _target_state_id",
                "Dream's save belongs to its actual target.",
            ),
            _set("_dream_save_actor", None),
            _when(
                "target_state.creature_kind == 'character'",
                [_set("_dream_save_actor", "_target_state_id")],
            ),
            _call(
                "resolve_srd_divination_save",
                {
                    "target_state": "{_target_state_id}",
                    "actor": "{_dream_save_actor}",
                    "ability": "wisdom",
                    "dc": "{_dream_record.save_dc}",
                },
                ["_divination_save_success"],
            ),
            _set("state.dream_spell_sources.{dream_id}.terror_save_used", True),
            _set("state.dream_spell_sources.{dream_id}.save_success", "_divination_save_success"),
            _set(
                "state.dream_spell_sources.{dream_id}.wake_damage_pending",
                "not _divination_save_success",
            ),
            _when(
                "_divination_save_success != true and target_state.rest_phase == 'active'",
                [
                    _set("target_state.dream_denied_rest_serial", "target_state.dream_rest_serial"),
                    _set(
                        "state.dream_spell_sources.{dream_id}.denied_rest_serial",
                        "target_state.dream_rest_serial",
                    ),
                ],
            ),
        ],
    )


def _observe_tools():
    """Queue actual sleep transitions before the final damage owner settles wake consequences."""
    observe = _tool_scene(
        "Observe genuine target sleep, wake and exact eight-hour Dream expiry.",
        [_entity("target_state")],
        [
            _call(
                "read_srd_dream_sleep", {"target_state": "{_target_state_id}"}, ["_dream_asleep"]
            ),
            _when(
                "target_state.dream_target_source_ids != null",
                [
                    _set("_observed_dream_ids", "target_state.dream_target_source_ids"),
                    _loop(
                        "_observed_dream_ids",
                        "_observed_dream_id",
                        [
                            *_source("_observed_dream_id"),
                            _when(
                                "_dream_record.active == true and state.lasting_tick >= "
                                "_dream_record.expires_tick",
                                [
                                    _call(
                                        "retire_srd_dream_source",
                                        {"dream_id": "{_observed_dream_id}", "reason": "expiry"},
                                    ),
                                    _set(
                                        "_dream_record",
                                        "state.dream_spell_sources[_observed_dream_id]",
                                    ),
                                ],
                            ),
                            _when(
                                "_dream_record.active == true and _dream_asleep == true and "
                                "_dream_record.sleep_entered != true",
                                [
                                    _set(
                                        "state.dream_spell_sources.{_observed_dream_id}.sleep_e"
                                        "ntered",
                                        True,
                                    ),
                                    _when(
                                        "_dream_record.terrifying == true and "
                                        "_dream_record.terror_save_used != true",
                                        [
                                            _call(
                                                "resolve_srd_dream_terror",
                                                {
                                                    "target_state": "{_target_state_id}",
                                                    "dream_id": "{_observed_dream_id}",
                                                },
                                            ),
                                        ],
                                    ),
                                    _set(
                                        "_dream_record",
                                        "state.dream_spell_sources[_observed_dream_id]",
                                    ),
                                ],
                            ),
                            _when(
                                "_dream_record.wake_damage_pending == true and _dream_asleep "
                                "== true and "
                                "target_state.rest_phase == 'active' and "
                                "_dream_record.denied_rest_serial == null",
                                [
                                    _set(
                                        "target_state.dream_denied_rest_serial",
                                        "target_state.dream_rest_serial",
                                    ),
                                    _set(
                                        "state.dream_spell_sources.{_observed_dream_id}.denied_"
                                        "rest_serial",
                                        "target_state.dream_rest_serial",
                                    ),
                                ],
                            ),
                            _when(
                                "_dream_record.sleep_entered == true and _dream_record.woke != "
                                "true and "
                                "_dream_asleep != true and target_state.is_alive == true and "
                                "target_state.hp > 0 and 'unconscious' not in "
                                "target_state.conditions",
                                [
                                    _set(
                                        "state.dream_spell_sources.{_observed_dream_id}.woke", True
                                    ),
                                    _when(
                                        "_dream_record.wake_damage_pending == true",
                                        [
                                            _call(
                                                "queue_srd_dream_wake",
                                                {"target_state": "{_target_state_id}"},
                                            ),
                                        ],
                                    ),
                                    _set(
                                        "target_state.dream_recall.{_observed_dream_id}",
                                        {
                                            "messages": "_dream_record.messages",
                                            "environment": "_dream_record.environment",
                                            "environment_history": (
                                                "_dream_record.environment_history"
                                            ),
                                            "terrifying_message": "_dream_record.message_words",
                                            "perfect_recall": True,
                                        },
                                    ),
                                    _when(
                                        "_dream_record.active == true",
                                        [
                                            _call(
                                                "retire_srd_dream_source",
                                                {
                                                    "dream_id": "{_observed_dream_id}",
                                                    "reason": "target_woke",
                                                },
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
    settle = _tool_scene(
        "Settle each actual failed nightmare once on waking using the final typed damage owner.",
        [_entity("target_state")],
        [
            _call("observe_srd_dream_sleep", {"target_state": "{_target_state_id}"}),
            _set("_dream_wake_effective_damage", 0),
            _when(
                "target_state.dream_target_source_ids != null",
                [
                    _set("_wake_dream_ids", "target_state.dream_target_source_ids"),
                    _loop(
                        "_wake_dream_ids",
                        "_wake_dream_id",
                        [
                            *_source("_wake_dream_id"),
                            _when(
                                "_dream_record.woke == true and _dream_record.wake_damage_pendi"
                                "ng == true",
                                [
                                    _set(
                                        "state.dream_spell_sources.{_wake_dream_id}.wake_damage"
                                        "_pending",
                                        False,
                                    ),
                                    _set(
                                        "state.dream_spell_sources.{_wake_dream_id}.wake_damage"
                                        "_settled",
                                        True,
                                    ),
                                    {"dice": {"expression": "3d6"}, "store_as": "_dream_damage"},
                                    _call(
                                        "read_srd_dream_vitals",
                                        {"target_state": "{_target_state_id}"},
                                        ["_dream_vitals"],
                                    ),
                                    _set("_dream_vitals_before", "_dream_vitals"),
                                    _call(
                                        "adjust_srd_dream_damage_roll",
                                        {
                                            "caster_state": "{_dream_record.caster}",
                                            "raw_total": "{_dream_damage.total}",
                                        },
                                        ["_dream_raw_damage"],
                                    ),
                                    _call(
                                        "apply_srd_dream_wake_damage",
                                        {
                                            "target": "{_target_state_id}",
                                            "amount": "{max(0, _dream_raw_damage)}",
                                            "adjustment": 0,
                                            "damage_type": "psychic",
                                            "source_state_id": "{_dream_record.caster}",
                                            "unallocated_roll_penalty": (
                                                "{max(0, -_dream_raw_damage)}"
                                            ),
                                            "game_damage_roll": True,
                                        },
                                        ["_effective_damage"],
                                    ),
                                    _set(
                                        "state.dream_spell_sources.{_wake_dream_id}.wake_effect"
                                        "ive_damage",
                                        "_effective_damage",
                                    ),
                                    _call(
                                        "read_srd_dream_vitals",
                                        {"target_state": "{_target_state_id}"},
                                        ["_dream_vitals"],
                                    ),
                                    _set(
                                        "state.dream_spell_sources.{_wake_dream_id}.wake_hp_temp_loss",
                                        "max(0, _dream_vitals_before - _dream_vitals)",
                                    ),
                                    _set(
                                        "state.dream_spell_sources.{_wake_dream_id}.wake_damage"
                                        "_roll",
                                        "_dream_damage",
                                    ),
                                    _set(
                                        "_dream_wake_effective_damage",
                                        "_dream_wake_effective_damage + "
                                        "max(0, _dream_vitals_before - _dream_vitals)",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    adjust = _tool_scene(
        "Apply the real caster-owned raw damage modifier once while preserving the 3d6 roll.",
        [_entity("caster_state"), _parameter("raw_total", "integer")],
        [
            _set("_dream_raw_damage", "raw_total"),
            _when(
                "caster_state.has_actor_spell_modifiers == true",
                [
                    _call(
                        "modify_srd_actor_raw_damage_roll",
                        {"actor_state": "{_caster_state_id}", "amount": "{raw_total}"},
                        ["_ray_raw_damage"],
                    ),
                    _set("_dream_raw_damage", "_ray_raw_damage"),
                ],
            ),
        ],
    )
    return {
        "observe_srd_dream_sleep": observe,
        "settle_srd_dream_wake": settle,
        "adjust_srd_dream_damage_roll": adjust,
        "read_srd_dream_vitals": _tool_scene(
            "Read actual HP and Temp HP after the final typed damage observers.",
            [_entity("target_state")],
            [
                _set("_dream_vitals", "target_state.hp"),
                _when(
                    "target_state.temp_hp != null",
                    [_set("_dream_vitals", "_dream_vitals + target_state.temp_hp")],
                ),
            ],
        ),
    }


def _casting_tool():
    """Install after the paid one-minute cast using admitted knowledge and actual touch."""
    return _tool_scene(
        "Install Dream only on a known same-plane creature and a willing actual touched messenger.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _parameter("save_dc", "integer"),
            _parameter("slot_level", "integer"),
            *deepcopy(CAST_CHOICES),
            *deepcopy(HIDDEN_SOURCE),
        ],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_dream_cast_plane", "_info_scene_entity.metadata.plane"),
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _require(
                "_info_scene_entity.metadata.known_by != null and _caster_state_id in "
                "_info_scene_entity.metadata.known_by",
                "The target needs its admitted known-by-caster fact.",
            ),
            _require(
                "_info_scene_entity.metadata.plane == _dream_cast_plane",
                "Dream targets the same plane.",
            ),
            _set("_dream_chosen_messenger", "_caster_state_id"),
            _when(
                "dream_messenger != null", [_set("_dream_chosen_messenger", "_dream_messenger_id")]
            ),
            _when(
                "_dream_chosen_messenger != _caster_state_id",
                [
                    _require(
                        "dream_messenger_willing == true",
                        "The touched messenger must willingly enter trance.",
                    ),
                    _call(
                        "resolve_srd_scene_geometry_relation",
                        {
                            "actor_state": "{_caster_state_id}",
                            "target_state": "{_dream_chosen_messenger}",
                        },
                        ["_scene_distance", "_scene_same_plane", "_scene_line_of_sight"],
                    ),
                    _require(
                        "_scene_same_plane == true and _scene_distance <= 5 and "
                        "_scene_line_of_sight == true",
                        "Touch the actual willing messenger within reach.",
                    ),
                ],
            ),
            _when(
                "dream_terrifying == true",
                [
                    _require(
                        "len(dream_message_words) >= 1 and len(dream_message_words) <= 10",
                        "A terrifying messenger delivers no more than ten words.",
                    ),
                    _call("validate_srd_spell_words", {"words": "{dream_message_words}"}),
                ],
            ),
            _when(
                "state.dream_spell_sources == null",
                [_set("state.dream_spell_sources", {}), _set("state.dream_spell_target_ids", [])],
            ),
            _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
            {"increment": {"field": "state.lasting_serial", "amount": 1}},
            _call(
                "format_srd_information_projection",
                {"message": "dream-{state.lasting_serial}"},
                ["_info_projection_message"],
            ),
            _set("_dream_id", "_info_projection_message"),
            _call(
                "install_srd_timed_condition_source",
                {
                    "target_state": "{_dream_chosen_messenger}",
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "state",
                    "spell": "dream",
                    "condition": "incapacitated",
                    "save_dc": "{save_dc}",
                    "duration_rounds": 4800,
                    "repeat_save_timing": "none",
                    "control_profile": "none",
                    "spell_cast_id": "{spell_cast_id}",
                    "spell_cast_owner": "{_spell_cast_owner_id}",
                    "actual_spell_level": "{slot_level}",
                },
                ["_timed_condition_source_id"],
            ),
            _set(
                "state.timed_condition_sources.{_timed_condition_source_id}.dream_source_id",
                "_dream_id",
            ),
            _set(
                "state.dream_spell_sources.{_dream_id}",
                {
                    "id": "_dream_id",
                    "spell": "'dream'",
                    "active": True,
                    "caster": "_caster_state_id",
                    "target": "_target_state_id",
                    "messenger": "_dream_chosen_messenger",
                    "save_dc": "save_dc",
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 9600",
                    "concentration": False,
                    "condition_source_id": "_timed_condition_source_id",
                    "terrifying": "dream_terrifying",
                    "message_words": "dream_message_words",
                    "messages": [],
                    "environment": "dream_environment",
                    "environment_history": ["dream_environment"],
                    "sleep_entered": False,
                    "woke": False,
                    "terror_save_used": False,
                    "wake_damage_pending": False,
                    "denied_rest_serial": None,
                    "cast_id": "spell_cast_id",
                    "source_caster_id": "_caster_state_id",
                    "spell_level": "slot_level",
                    "ongoing_duration": True,
                    "dispel_ongoing": True,
                },
            ),
            _call(
                "register_srd_dream_subjects",
                {
                    "target_state": "{_target_state_id}",
                    "messenger_state": "{_dream_chosen_messenger}",
                    "dream_id": "{_dream_id}",
                },
            ),
            _call("observe_srd_dream_sleep", {"target_state": "{_target_state_id}"}),
            _call(
                "read_srd_dream_sleep", {"target_state": "{_target_state_id}"}, ["_dream_asleep"]
            ),
            _set("_dream_summary_rows", [{"label": "'Dream'", "value": "_dream_id"}]),
            _set(
                "_dream_detail_rows",
                [
                    {"label": "'Messenger'", "value": "_dream_chosen_messenger"},
                    {"label": "'Target asleep'", "value": "_dream_asleep"},
                ],
            ),
        ],
    )


def _registration_tool():
    return _tool_scene(
        "Index this exact target/messenger source and immediately project its trance.",
        [_entity("target_state"), _entity("messenger_state"), _parameter("dream_id")],
        [
            _when(
                "target_state.dream_target_source_ids == null",
                [_set("target_state.dream_target_source_ids", [])],
            ),
            _set("_dream_target_ids", "target_state.dream_target_source_ids"),
            _push("_dream_target_ids", "dream_id"),
            _set("target_state.dream_target_source_ids", "_dream_target_ids"),
            _when(
                "messenger_state.dream_messenger_source_ids == null",
                [_set("messenger_state.dream_messenger_source_ids", [])],
            ),
            _set("_dream_messenger_ids", "messenger_state.dream_messenger_source_ids"),
            _push("_dream_messenger_ids", "dream_id"),
            _set("messenger_state.dream_messenger_source_ids", "_dream_messenger_ids"),
            _set("_dream_all_targets", "state.dream_spell_target_ids"),
            _when(
                "_target_state_id not in _dream_all_targets",
                [_push("_dream_all_targets", "_target_state_id")],
            ),
            _set("state.dream_spell_target_ids", "_dream_all_targets"),
            _set("state.has_dream_spells", True),
            _call(
                "refresh_srd_timed_condition_target",
                {"target_state": "{_messenger_state_id}", "lasting_state": "state"},
            ),
            _call("project_srd_dream_trance", {"messenger_state": "{_messenger_state_id}"}),
        ],
    )


def _registry_tools():
    """Keep cheap consumers active only for a live trance or an authentic pending wake/recall."""
    target = _tool_scene(
        "Prune completed target indexes while preserving all source records and recall.",
        [_entity("target_state")],
        [
            _set("_dream_target_pending", False),
            _set("_dream_pending_ids", []),
            _when(
                "target_state.dream_target_source_ids != null",
                [
                    _set("_dream_old_ids", "target_state.dream_target_source_ids"),
                    _loop(
                        "_dream_old_ids",
                        "_dream_old_id",
                        [
                            _set("_dream_old", "state.dream_spell_sources[_dream_old_id]"),
                            _when(
                                "_dream_old.active == true or _dream_old.wake_damage_pending == "
                                "true or (_dream_old.sleep_entered == true and "
                                "_dream_old.woke != true)",
                                [
                                    _push("_dream_pending_ids", "_dream_old_id"),
                                    _set("_dream_target_pending", True),
                                ],
                            ),
                        ],
                    ),
                    _set("target_state.dream_target_source_ids", "_dream_pending_ids"),
                ],
            ),
        ],
    )
    registry = _tool_scene(
        "Retain only actual targets with live Dream or pending wake consequences.",
        [],
        [
            _set("_dream_old_targets", "state.dream_spell_target_ids"),
            _set("_dream_remaining_targets", []),
            _loop(
                "_dream_old_targets",
                "_dream_old_target",
                [
                    _call(
                        "prune_srd_dream_target",
                        {"target_state": "{_dream_old_target}"},
                        ["_dream_target_pending"],
                    ),
                    _when(
                        "_dream_target_pending == true",
                        [_push("_dream_remaining_targets", "_dream_old_target")],
                    ),
                ],
            ),
            _set("state.dream_spell_target_ids", "_dream_remaining_targets"),
            _set("state.has_dream_spells", "len(_dream_remaining_targets) > 0"),
        ],
    )
    queue = _tool_scene(
        "Queue an authentic failed-nightmare wake observed by the owning source transition.",
        [_entity("target_state")],
        [
            _when("state.dream_wake_target_ids == null", [_set("state.dream_wake_target_ids", [])]),
            _set("_dream_wake_queue", "state.dream_wake_target_ids"),
            _when(
                "_target_state_id not in _dream_wake_queue",
                [_push("_dream_wake_queue", "_target_state_id")],
            ),
            _set("state.dream_wake_target_ids", "_dream_wake_queue"),
        ],
    )
    passes = _tool_scene(
        "Bound wake draining by the actual remaining failed-nightmare source count.",
        [_entity("target_state")],
        [
            _set("_dream_pending_pass_ids", []),
            _when(
                "target_state.dream_target_source_ids != null",
                [
                    _set("_dream_pass_ids", "target_state.dream_target_source_ids"),
                    _loop(
                        "_dream_pass_ids",
                        "_dream_pass_id",
                        [
                            _set("_dream_pass_source", "state.dream_spell_sources[_dream_pass_id]"),
                            _when(
                                "_dream_pass_source.wake_damage_pending == true",
                                [_push("_dream_pending_pass_ids", "_dream_pass_id")],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    drain = _tool_scene(
        "Drain real wake consequences without recursively entering the final damage owner.",
        [],
        [
            _when(
                "state.dream_wake_target_ids != null and len(state.dream_wake_target_ids) > 0",
                [
                    _set("_dream_drain_targets", "state.dream_spell_target_ids"),
                    _set("_dream_drain_passes", []),
                    _loop(
                        "_dream_drain_targets",
                        "_dream_drain_target",
                        [
                            _call(
                                "read_srd_dream_pending_wakes",
                                {"target_state": "{_dream_drain_target}"},
                                ["_dream_pending_pass_ids"],
                            ),
                            _loop(
                                "_dream_pending_pass_ids",
                                "_dream_drain_source",
                                [_push("_dream_drain_passes", "_dream_drain_source")],
                            ),
                        ],
                    ),
                    _push("_dream_drain_passes", "'final'"),
                    _loop(
                        "_dream_drain_passes",
                        "_dream_drain_pass",
                        [
                            _set("_dream_current_wakes", "state.dream_wake_target_ids"),
                            _set("state.dream_wake_target_ids", []),
                            _loop(
                                "_dream_current_wakes",
                                "_dream_current_target",
                                [
                                    _call(
                                        "settle_srd_dream_wake",
                                        {"target_state": "{_dream_current_target}"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _require(
                        "len(state.dream_wake_target_ids) == 0",
                        "Dream wake effects exceeded their authentic pending source bound.",
                    ),
                    _call("refresh_srd_dream_registry", {}),
                ],
            ),
        ],
    )
    return {
        "prune_srd_dream_target": target,
        "refresh_srd_dream_registry": registry,
        "queue_srd_dream_wake": queue,
        "read_srd_dream_pending_wakes": passes,
        "drain_srd_dream_wakes": drain,
        "observe_srd_dream_targets": _tool_scene(
            "Observe each actual saved Dream target after a completed spell consequence.",
            [],
            [
                _set("_dream_observer_targets", "state.dream_spell_target_ids"),
                _loop(
                    "_dream_observer_targets",
                    "_dream_observer_target",
                    [
                        _call(
                            "observe_srd_dream_sleep", {"target_state": "{_dream_observer_target}"}
                        )
                    ],
                ),
            ],
        ),
    }


def _conversation_tool():
    return _public(
        _tool_scene(
            "Converse and shape the dream only while this actual target sleeps and messenger "
            "remains in trance.",
            [
                _entity("messenger_state"),
                _parameter("dream_id"),
                _parameter("message"),
                _optional("environment", default=""),
            ],
            [
                _require(PENDING, "Resolve the pending action before dream dialogue."),
                _require(
                    "messenger_state.is_alive == true and messenger_state.hp > 0 and "
                    "'unconscious' not in messenger_state.conditions",
                    "A living conscious messenger shapes and speaks in the dream.",
                ),
                *_source(),
                _require(
                    "_dream_record.active == true and _dream_record.messenger == "
                    "_messenger_state_id and "
                    "state.lasting_tick < _dream_record.expires_tick",
                    "This actor must be the live Dream messenger.",
                ),
                _call("observe_srd_dream_sleep", {"target_state": "{_dream_record.target}"}),
                _call(
                    "read_srd_dream_sleep",
                    {"target_state": "{_dream_record.target}"},
                    ["_dream_asleep"],
                ),
                _require(
                    "_dream_asleep == true",
                    "The actual target is awake; the messenger may wait or emerge.",
                ),
                _require(
                    "_dream_record.terrifying != true",
                    "The terrifying messenger has its single saved ten-word message.",
                ),
                _set("_dream_messages", "_dream_record.messages"),
                _push("_dream_messages", "message"),
                _set("state.dream_spell_sources.{dream_id}.messages", "_dream_messages"),
                _when(
                    "environment != ''",
                    [
                        _set("state.dream_spell_sources.{dream_id}.environment", "environment"),
                        _set("_dream_environments", "_dream_record.environment_history"),
                        _push("_dream_environments", "environment"),
                        _set(
                            "state.dream_spell_sources.{dream_id}.environment_history",
                            "_dream_environments",
                        ),
                    ],
                ),
                _set("_dream_message_delivered", "message"),
            ],
        ),
        "exploration",
        "Dream conversation",
        [("Delivered", "_dream_message_delivered")],
    )


def _rest_tools():
    return {
        "check_srd_dream_rest_benefit": _tool_scene(
            "Qualify nightmare denial only against this actual rest episode.",
            [_entity("target_state")],
            [
                _set(
                    "_dream_rest_denied",
                    "target_state.dream_denied_rest_serial != null and "
                    "target_state.dream_rest_serial != null and "
                    "target_state.dream_denied_rest_serial == "
                    "target_state.dream_rest_serial",
                )
            ],
        )
    }


def _bind_cast(tools, changed):
    """Replace one owned family branch in place, preserving final Slow and pending-loss guards."""
    tool = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in tool["parameters"]}
    tool["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in names]
    args = {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
        + "}"
        for p in CAST_CHOICES
    }
    args.update(
        caster_state="{_caster_state_id}",
        target_state="{_target_id}",
        save_dc="{_casting_profile.save_dc}",
        slot_level="{_slot_level}",
    )
    branch = _when(
        "_spell_definition.family == 'dream_spell'",
        [
            _call("cast_srd_dream_spell", args, ["_dream_summary_rows", "_dream_detail_rows"]),
            _loop(
                "_dream_summary_rows",
                "_dream_cast_row",
                [_push("_spell_summary_rows", "_dream_cast_row")],
            ),
            _loop(
                "_dream_detail_rows",
                "_dream_cast_row",
                [_push("_spell_detail_rows", "_dream_cast_row")],
            ),
        ],
    )
    found = False

    def replace(effects):
        nonlocal found
        out = []
        for e in effects:
            if "_spell_definition.family == 'dream_spell'" in e.get("condition", ""):
                b = deepcopy(branch)
                b["condition"] = e["condition"]
                out.append(b)
                found = True
                continue
            for k in ("on_true", "on_false"):
                if k in e:
                    e[k] = replace(e[k])
            out.append(e)
        return out

    def insert(effects):
        indices = [
            i
            for i, e in enumerate(effects)
            if "_spell_definition.family ==" in e.get("condition", "")
        ]
        if indices:
            effects.insert(indices[-1] + 1, deepcopy(branch))
            return True
        return any(insert(e[k]) for e in effects for k in ("on_true", "on_false") if k in e)

    tool["effects"] = replace(tool["effects"])
    if not found and not insert(tool["effects"]):
        raise ValueError("Paid family dispatch is absent")
    changed["cast_spell"] = tool


def bind_runtime_consumers(text):
    """Clone final damage/rest owners while omitting only Dream's direct wake drain."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    damage = deepcopy(tools["apply_srd_qualifying_damage"])
    damage["effects"] = [
        e
        for e in damage["effects"]
        if e.get("condition") != "lasting_state.has_dream_spells == true"
    ]
    damage["effects"].append(
        _when(
            "lasting_state.has_dream_spells == true",
            [
                _call("observe_srd_dream_sleep", {"target_state": "{target}"}),
            ],
        )
    )
    changed["apply_srd_dream_wake_damage"] = deepcopy(damage)
    damage["effects"].append(
        _when("lasting_state.has_dream_spells == true", [_call("drain_srd_dream_wakes", {})])
    )
    changed["apply_srd_qualifying_damage"] = damage
    if "apply_srd_composite_damage" in tools:
        composite = deepcopy(tools["apply_srd_composite_damage"])
        composite["effects"] = [
            e
            for e in composite["effects"]
            if e.get("condition") != "lasting_state.has_dream_spells == true"
        ]
        composite["effects"].append(
            _when(
                "lasting_state.has_dream_spells == true",
                [
                    _call("observe_srd_dream_sleep", {"target_state": "{target}"}),
                    _call("drain_srd_dream_wakes", {}),
                ],
            )
        )
        changed["apply_srd_composite_damage"] = composite
    for name in (
        "wake_srd_rest_sleeper",
        "set_srd_rest_activity",
        "shake_srd_sleeping_creature",
        "wake_srd_alarm_sleep",
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        tool["effects"] = [
            e
            for e in tool["effects"]
            if e.get("condition") != "lasting_state.has_dream_spells == true"
        ]
        tool["effects"].append(
            _when(
                "lasting_state.has_dream_spells == true",
                [
                    _call("observe_srd_dream_sleep", {"target_state": "{_target_state_id}"}),
                    *(
                        [_call("drain_srd_dream_wakes", {})]
                        if name in {"set_srd_rest_activity", "shake_srd_sleeping_creature"}
                        else []
                    ),
                ],
            )
        )
        changed[name] = tool
    begin = deepcopy(tools["begin_srd_rest"])
    if "dream_rest_serial" not in str(begin["effects"]):
        begin["effects"] = [
            _when(
                "target_state.dream_rest_serial == null",
                [_set("target_state.dream_rest_serial", 0)],
            ),
            {"increment": {"field": "target_state.dream_rest_serial", "amount": 1}},
        ] + begin["effects"]
    changed["begin_srd_rest"] = begin
    if "observe_srd_dream_sleep" not in str(begin["effects"]):
        begin["effects"].append(
            _when(
                "lasting_state.has_dream_spells == true",
                [_call("observe_srd_dream_sleep", {"target_state": "{_target_state_id}"})],
            )
        )
    recover = deepcopy(tools["recover_srd_rest_resources"])
    if "_dream_rest_denied" not in str(recover["effects"]):
        recover["effects"] = [
            _call(
                "check_srd_dream_rest_benefit",
                {"target_state": "{_target_state_id}"},
                ["_dream_rest_denied"],
            ),
            {
                "condition": "_dream_rest_denied != true",
                "on_true": recover["effects"],
                "on_false": [_set("_rest_resource_rows", [])],
            },
        ]
    changed["recover_srd_rest_resources"] = recover
    complete = deepcopy(tools["complete_srd_rest"])
    if "_dream_rest_denied" not in str(complete["effects"]):
        cutoff = next(
            i
            for i, e in enumerate(complete["effects"])
            if e.get("set", {}).get("field") == "_rest_hp_before"
        )
        complete["effects"] = complete["effects"][:cutoff] + [
            _call(
                "check_srd_dream_rest_benefit",
                {"target_state": "{_target_state_id}"},
                ["_dream_rest_denied"],
            ),
            {
                "condition": "_dream_rest_denied != true",
                "on_true": complete["effects"][cutoff:],
                "on_false": [
                    _set("_rest_resource_rows", []),
                    _set("_rest_hp_restored", 0),
                    _set("_rest_exhaustion_removed", 0),
                    _set("_rest_minutes", "_rest_total_ticks // 20"),
                    _call("wake_srd_rest_sleeper", {"target_state": "{_target_state_id}"}),
                    _set("target_state.rest_phase", "'complete'"),
                ],
            },
        ]
    if "drain_srd_dream_wakes" not in str(complete["effects"]):
        complete["effects"].append(
            _when(
                "lasting_state.has_dream_spells == true",
                [
                    _call("observe_srd_dream_sleep", {"target_state": "{_target_state_id}"}),
                    _call("drain_srd_dream_wakes", {}),
                ],
            )
        )
    changed["complete_srd_rest"] = complete
    for name in ("cast_spell", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        cast["effects"] = [
            effect
            for effect in cast["effects"]
            if effect.get("condition") != "lasting_state.has_dream_spells == true"
        ]
        cast["effects"].append(
            _when(
                "lasting_state.has_dream_spells == true",
                [
                    _call("observe_srd_dream_targets", {}),
                    _call("drain_srd_dream_wakes", {}),
                ],
            )
        )
        changed[name] = cast
    for name in ("derive_srd_scene_movement_budget", "spend_srd_movement", "spend_combat_movement"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        actor = "target" if name == "spend_srd_movement" else "actor_state"
        if "dream_trance_speed_zero" not in str(tool["effects"]):
            tool["effects"].insert(
                0,
                _require(
                    actor + ".dream_trance_speed_zero != true", "A Dream messenger has Speed 0."
                ),
            )
        changed[name] = tool
    for tool in changed.values():
        if "lasting_state." in str(tool["effects"]) and not any(
            parameter["name"] == "lasting_state" for parameter in tool["parameters"]
        ):
            tool["parameters"].append(
                _optional("lasting_state", default="state", resolve="entity", model_hidden=True)
            )
    _normalize(changed)
    return _replace_tools(text, changed)


def extend_runtime(text):
    """Emit Dream leaves after illusion/divination/condition source owners, before final cloning."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "read_srd_dream_sleep": _sleep_tool(),
        "resolve_srd_dream_terror": _save_tool(),
        "cast_srd_dream_spell": _casting_tool(),
        "register_srd_dream_subjects": _registration_tool(),
        "converse_srd_dream": _conversation_tool(),
        **_trance_tools(),
        **_observe_tools(),
        **_rest_tools(),
        **_registry_tools(),
    }
    _bind_cast(tools, changed)
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    clock["effects"] = [
        e
        for e in clock["effects"]
        if e.get("condition") != "lasting_state.has_dream_spells == true"
    ]
    clock["effects"].append(
        _when(
            "lasting_state.has_dream_spells == true",
            [
                _set("_dream_clock_targets", "lasting_state.dream_spell_target_ids"),
                _loop(
                    "_dream_clock_targets",
                    "_dream_clock_target",
                    [
                        _call("observe_srd_dream_sleep", {"target_state": "{_dream_clock_target}"}),
                    ],
                ),
                _call("drain_srd_dream_wakes", {}),
                _call("refresh_srd_dream_registry", {}),
            ],
        )
    )
    changed["advance_srd_lasting_clock"] = clock
    _normalize(changed)
    text = _replace_tools(text, changed)
    if "      dream_spell:" not in text:
        p = {
            k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
            for k, v in PROFILES["dream"].items()
            if k != "family"
        }
        schema = yaml.dump(
            {
                "dream_spell": {
                    "type": "object",
                    "properties": p,
                    "required": list(p),
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
