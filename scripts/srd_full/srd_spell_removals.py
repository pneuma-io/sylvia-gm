"""Dismiss one actual time-span casting through its existing source owners."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from .srd_spell_removal_adoption import extend_tools as extend_removal_adoption
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from srd_spell_removal_adoption import extend_tools as extend_removal_adoption
    from srd_timed_senses import _replace_tools


PUBLIC_TOOLS = {"dismiss_srd_spell"}


def _tools(tools):
    condition_ids = next(
        p["enum_values"]
        for p in tools["validate_srd_timed_condition_source_id"]["parameters"]
        if p["name"] == "source_id"
    )
    projection = [
        _set("_removed_turn_modifiers", False),
        _set("_removed_hit_point_bonus", False),
        _set("_removed_mobility", False),
        _set("_removed_defense", False),
        _set("_removed_senses", False),
        _each(
            "removed_effects",
            "_removed_effect",
            [
                _when(
                    "_removed_effect.target == _target_state_id",
                    [
                        *[
                            _when(condition, [_set(flag, True)])
                            for flag, condition in (
                                (
                                    "_removed_turn_modifiers",
                                    "_removed_effect.kind == 'speed_bonus' "
                                    "or _removed_effect.kind == 'armor_class_floor'",
                                ),
                                (
                                    "_removed_hit_point_bonus",
                                    "_removed_effect.kind == 'hit_point_bonus'",
                                ),
                                ("_removed_mobility", "_removed_effect.kind == 'actor_mobility'"),
                                (
                                    "_removed_defense",
                                    "_removed_effect.kind == 'timed_actor_defense'",
                                ),
                                ("_removed_senses", "_removed_effect.kind == 'timed_senses'"),
                            )
                        ],
                    ],
                ),
            ],
        ),
    ]
    for name, condition in (
        ("project_srd_turn_modifiers", "_removed_turn_modifiers == true"),
        ("project_srd_timed_hit_point_bonus", "_removed_hit_point_bonus == true"),
        ("project_srd_timed_actor_mobility", "_removed_mobility == true"),
        (
            "refresh_srd_timed_condition_target",
            "condition_removed == true or _removed_defense == true",
        ),
    ):
        if name in tools:
            projection.append(
                _when(
                    condition,
                    [
                        _call(
                            name,
                            {
                                "target_state": "{_target_state_id}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                        )
                    ],
                )
            )
    if "refresh_srd_respiration" in tools:
        projection.append(
            _when(
                "_removed_senses == true and target_state.respiration_medium != null",
                [
                    _call("refresh_srd_respiration", {"target_state": "{_target_state_id}"}),
                ],
            )
        )
    active_condition = (
        "_dismiss_source != null and _dismiss_source.installed == true and "
        "_dismiss_source.cast_id == cast_id and _dismiss_source.source_caster_id == "
        "_caster_state_id and _dismiss_source.ongoing_duration == true and "
        "_dismiss_source.expires_tick != null and lasting_state.lasting_tick < "
        "_dismiss_source.expires_tick"
    )
    active_effect = (
        "_dismiss_effect.cast_id == cast_id and _dismiss_effect.source_caster_id == "
        "_caster_state_id and _dismiss_effect.ongoing_duration == true and "
        "_dismiss_effect.expires_tick != null and lasting_state.lasting_tick < "
        "_dismiss_effect.expires_tick"
    )
    dismiss = _tool(
        "Dismiss one selected actual ongoing time-span cast without spending an action. "
        "Its cast ID comes from the casting result; legacy records without that identity "
        "cannot be grouped by caster, spell, or tick.",
        [_entity("caster_state"), _lasting(), _param("cast_id")],
        [
            _set("_dismiss_cast_id", "cast_id"),
            _set(
                "_dismiss_concentration_owned",
                "cast_id != '' and caster_state.concentration_cast_id == cast_id and "
                "caster_state.concentration_spell_id != null and "
                "caster_state.concentration_expires_tick != null and "
                "lasting_state.lasting_tick < caster_state.concentration_expires_tick",
            ),
            _set("_dismiss_targets", []),
            _set("_dismiss_condition_targets", []),
            _set("_dismiss_sources", []),
            _set("_dismiss_count", 0),
            _set("_dismiss_condition_ids", [f"'{source}'" for source in condition_ids]),
            _when(
                "lasting_state.timed_condition_sources != null",
                [
                    _each(
                        "_dismiss_condition_ids",
                        "_dismiss_source_id",
                        [
                            _set(
                                "_dismiss_source",
                                "lasting_state.timed_condition_sources[_dismiss_source_id]",
                            ),
                            _when(
                                active_condition,
                                [
                                    _push("_dismiss_sources", "_dismiss_source_id"),
                                    _when(
                                        "_dismiss_source.target_state_id not in "
                                        "_dismiss_condition_targets",
                                        [
                                            _push(
                                                "_dismiss_condition_targets",
                                                "_dismiss_source.target_state_id",
                                            )
                                        ],
                                    ),
                                    _set("_dismiss_count", "_dismiss_count + 1"),
                                    _when(
                                        "_dismiss_source.target_state_id not in _dismiss_targets",
                                        [
                                            _push(
                                                "_dismiss_targets",
                                                "_dismiss_source.target_state_id",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("_dismiss_effects", []),
            _set("_dismiss_retained", []),
            _set("_dismiss_removed_effects", []),
            _when(
                "lasting_state.turn_effects != null",
                [
                    _set("_dismiss_effects", "lasting_state.turn_effects"),
                ],
            ),
            _each(
                "_dismiss_effects",
                "_dismiss_effect",
                [
                    _when(
                        active_effect,
                        [
                            _push("_dismiss_removed_effects", "_dismiss_effect"),
                            _set("_dismiss_count", "_dismiss_count + 1"),
                            _when(
                                "_dismiss_effect.target not in _dismiss_targets",
                                [
                                    _push("_dismiss_targets", "_dismiss_effect.target"),
                                ],
                            ),
                        ],
                        [_push("_dismiss_retained", "_dismiss_effect")],
                    ),
                ],
            ),
            _require(
                "_dismiss_count > 0 or _dismiss_concentration_owned == true",
                "Select an active supported cast belonging to this caster; "
                "expired, Instantaneous, indefinite, or legacy ambiguous effects "
                "cannot be dismissed.",
            ),
            _set("lasting_state.turn_effects", "_dismiss_retained"),
            _each(
                "_dismiss_sources",
                "_dismiss_source_id",
                [
                    _call(
                        "retire_srd_timed_condition_source",
                        {
                            "lasting_state": "{_lasting_state_id}",
                            "source_id": "{_dismiss_source_id}",
                        },
                    ),
                ],
            ),
            _each(
                "_dismiss_targets",
                "_dismiss_target",
                [
                    _set(
                        "_dismiss_condition_removed",
                        "_dismiss_target in _dismiss_condition_targets",
                    ),
                    _call(
                        "refresh_srd_removed_spell_target",
                        {
                            "target_state": "{_dismiss_target}",
                            "lasting_state": "{_lasting_state_id}",
                            "removed_effects": "{_dismiss_removed_effects}",
                            "condition_removed": "{_dismiss_condition_removed}",
                        },
                    ),
                ],
            ),
            _when(
                "_dismiss_concentration_owned == true",
                [
                    _call(
                        "end_srd_concentration_voluntarily", {"caster_state": "{_caster_state_id}"}
                    ),
                    _set("caster_state.concentration_cast_id", "''"),
                ],
            ),
        ],
        [
            {
                "condition": "caster_state.is_alive == true",
                "error": "A dead caster cannot dismiss a spell.",
            },
            {
                "condition": "'incapacitated' not in caster_state.conditions and "
                "'unconscious' not in caster_state.conditions and "
                "'stunned' not in caster_state.conditions and "
                "'petrified' not in caster_state.conditions and 'paralyzed' "
                "not in caster_state.conditions",
                "error": "An Incapacitated caster cannot dismiss a spell.",
            },
        ],
        public=True,
    )
    dismiss["narrative"] = {"on_success": "The selected ongoing cast is dismissed."}
    dismiss["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "inline",
        "title": "Spell dismissed",
        "summary": [
            {"label": "Cast", "value": {"path": "context._dismiss_cast_id"}},
            {"label": "Effects ended", "value": {"path": "context._dismiss_count"}},
        ],
    }
    phase_tools = {}
    if "end_srd_etherealness_target" in tools:
        phase_tools["collect_srd_dismiss_ethereal_target"] = _tool(
            "Select one actual recipient of the exact supported Etherealness cast.",
            [_entity("target_state"), _entity("caster_state"), _lasting(), _param("cast_id")],
            [
                _set("_dismiss_ethereal_matches", False),
                _set("_dismiss_ethereal_source", "target_state.travel_ethereal"),
                _when(
                    "_dismiss_ethereal_source != null and "
                    "_dismiss_ethereal_source.spell == 'etherealness' and "
                    "_dismiss_ethereal_source.cast_id == cast_id and "
                    "_dismiss_ethereal_source.source_caster_id == _caster_state_id and "
                    "_dismiss_ethereal_source.ongoing_duration == true and "
                    "_dismiss_ethereal_source.expires_tick != null and "
                    "lasting_state.lasting_tick < _dismiss_ethereal_source.expires_tick",
                    [_set("_dismiss_ethereal_matches", True)],
                ),
            ],
        )
        phase_tools["read_srd_ethereal_dismissal_identity"] = _tool(
            "Read one actual active phase grant's caster and casting identity.",
            [_entity("target_state"), _entity("caster_state"), _lasting()],
            [
                _set("_ethereal_identity_owned", False),
                _set("_ethereal_identity_known", False),
                _set("_ethereal_identity", "''"),
                _set("_ethereal_identity_source", "target_state.travel_ethereal"),
                _when(
                    "_ethereal_identity_source != null and "
                    "_ethereal_identity_source.spell == 'etherealness' and "
                    "_ethereal_identity_source.caster == _caster_state_id and "
                    "_ethereal_identity_source.expires_tick != null and "
                    "lasting_state.lasting_tick < _ethereal_identity_source.expires_tick",
                    [
                        _set("_ethereal_identity_owned", True),
                        _when(
                            "_ethereal_identity_source.cast_id != null and "
                            "_ethereal_identity_source.cast_id != '' and "
                            "_ethereal_identity_source.source_caster_id == _caster_state_id and "
                            "_ethereal_identity_source.ongoing_duration == true",
                            [
                                _set("_ethereal_identity_known", True),
                                _set("_ethereal_identity", "_ethereal_identity_source.cast_id"),
                            ],
                        ),
                    ],
                ),
            ],
        )
        phase_tools["choose_srd_etherealness_dismissal_cast"] = _tool(
            "Choose one known phase casting; caster/spell/tick never group independent casts.",
            [_entity("caster_state"), _lasting(), _param("cast_id", required=False, default="")],
            [
                _set("_ethereal_known_casts", []),
                _set("_ethereal_legacy_cast", False),
                _set("_ethereal_identity_targets", []),
                _when(
                    "lasting_state.travel_phase_targets != null",
                    [_set("_ethereal_identity_targets", "lasting_state.travel_phase_targets")],
                ),
                _each(
                    "_ethereal_identity_targets",
                    "_ethereal_identity_target",
                    [
                        _call(
                            "read_srd_ethereal_dismissal_identity",
                            {
                                "target_state": "{_ethereal_identity_target}",
                                "caster_state": "{_caster_state_id}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                            (
                                "_ethereal_identity_owned",
                                "_ethereal_identity_known",
                                "_ethereal_identity",
                            ),
                        ),
                        _when(
                            "_ethereal_identity_owned == true",
                            [
                                _when(
                                    "_ethereal_identity_known == true",
                                    [
                                        _when(
                                            "_ethereal_identity not in _ethereal_known_casts",
                                            [_push("_ethereal_known_casts", "_ethereal_identity")],
                                        )
                                    ],
                                    [_set("_ethereal_legacy_cast", True)],
                                )
                            ],
                        ),
                    ],
                ),
                _set("_ethereal_dismiss_cast_id", "cast_id"),
                _when(
                    "cast_id == '' and len(_ethereal_known_casts) == 1 and "
                    "_ethereal_legacy_cast != true",
                    [_set("_ethereal_dismiss_cast_id", "_ethereal_known_casts[0]")],
                ),
                _require(
                    "_ethereal_dismiss_cast_id in _ethereal_known_casts",
                    "There is no ongoing unambiguous Etherealness cast to dismiss; "
                    "select one exact known cast reference.",
                ),
            ],
        )
        selection = [
            _set("_dismiss_phase_targets", []),
            _set("_dismiss_scan_phase_targets", []),
            _when(
                "lasting_state.travel_phase_targets != null",
                [_set("_dismiss_scan_phase_targets", "lasting_state.travel_phase_targets")],
            ),
            _each(
                "_dismiss_scan_phase_targets",
                "_dismiss_phase_target",
                [
                    _call(
                        "collect_srd_dismiss_ethereal_target",
                        {
                            "target_state": "{_dismiss_phase_target}",
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "cast_id": "{cast_id}",
                        },
                        ("_dismiss_ethereal_matches",),
                    ),
                    _when(
                        "_dismiss_ethereal_matches == true",
                        [
                            _push("_dismiss_phase_targets", "_dismiss_phase_target"),
                            _set("_dismiss_count", "_dismiss_count + 1"),
                        ],
                    ),
                ],
            ),
        ]
        requirement = next(i for i, e in enumerate(dismiss["effects"]) if "require" in e)
        dismiss["effects"][requirement:requirement] = selection
        # The existing phase owner returns from the actual current position and
        # applies its real nearest-placement shunt consequences.
        dismiss["effects"].insert(
            requirement + len(selection) + 1,
            _each(
                "_dismiss_phase_targets",
                "_dismiss_phase_target",
                [_call("end_srd_etherealness_target", {"target_state": "{_dismiss_phase_target}"})],
            ),
        )
        dismiss["effects"].insert(
            requirement + len(selection) + 2,
            _when(
                "len(_dismiss_phase_targets) > 0",
                [_call("refresh_srd_etherealness_sources", {})],
            ),
        )
    one_condition = _tool(
        "Greater Restoration ends one exact Charmed or Petrified effect, including a "
        "suppressed source, while preserving independent effects of the same condition.",
        [
            _entity("target_state"),
            _lasting(),
            _param(
                "condition",
                "enum",
                enum_values=[
                    "charmed",
                    "petrified",
                ],
            ),
            _param("source_id", required=False, default=""),
        ],
        [
            _call(
                "refresh_srd_timed_condition_target",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
            _set("_one_condition_choices", []),
            _set("_one_condition_ids", []),
            _set("_one_condition_unowned", False),
            *[
                _when(
                    f"condition == '{condition}'",
                    [
                        _set(
                            "_one_condition_unowned",
                            f"target_state.timed_condition_unowned_{condition} == true",
                        ),
                    ],
                )
                for condition in ("charmed", "petrified")
            ],
            _when(
                "_one_condition_unowned == true",
                [
                    _push("_one_condition_choices", "'unowned'"),
                ],
            ),
            _when(
                "target_state.timed_condition_source_ids != null",
                [
                    _set("_one_condition_ids", "target_state.timed_condition_source_ids"),
                ],
            ),
            _each(
                "_one_condition_ids",
                "_one_condition_id",
                [
                    _set(
                        "_one_condition_source",
                        "lasting_state.timed_condition_sources[_one_condition_id]",
                    ),
                    _when(
                        "_one_condition_source != null and _one_condition_source.installed == true "
                        "and _one_condition_source.target_state_id == _target_state_id and "
                        "_one_condition_source.condition == condition",
                        [
                            _push("_one_condition_choices", "_one_condition_id"),
                        ],
                    ),
                ],
            ),
            _set("_one_condition_selected", "source_id"),
            _when(
                "source_id == '' and len(_one_condition_choices) == 1",
                [
                    _set("_one_condition_selected", "_one_condition_choices[0]"),
                ],
            ),
            _require(
                "(len(_one_condition_choices) == 0 and source_id == '') or "
                "_one_condition_selected in _one_condition_choices",
                "Choose the one exact "
                "condition effect Greater Restoration ends; use unowned for the admitted "
                "condition without a tracked source.",
            ),
            _when(
                "len(_one_condition_choices) > 0",
                [
                    _when(
                        "_one_condition_selected == 'unowned'",
                        [
                            *[
                                _when(
                                    f"condition == '{condition}'",
                                    [
                                        _set(
                                            f"target_state.timed_condition_unowned_{condition}",
                                            False,
                                        ),
                                        {
                                            "remove_condition": {
                                                "target": "target_state",
                                                "condition": condition,
                                            }
                                        },
                                    ],
                                )
                                for condition in ("charmed", "petrified")
                            ],
                        ],
                        [
                            _call(
                                "retire_srd_timed_condition_source",
                                {
                                    "lasting_state": "{_lasting_state_id}",
                                    "source_id": "{_one_condition_selected}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            _call(
                "refresh_srd_timed_condition_target",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
        ],
    )
    return phase_tools | {
        "dismiss_srd_spell": dismiss,
        "remove_srd_one_condition_effect": one_condition,
        "refresh_srd_removed_spell_target": _tool(
            "Reproject actual surviving source effects after selected spell removal.",
            [
                _entity("target_state"),
                _lasting(),
                _param("removed_effects", "array", items={"type": "object"}),
                _param("condition_removed", "boolean"),
            ],
            projection,
        ),
    }


def _illusion_removals(tools, changed):
    """Use the image pool and exact proxy/Invisible retire owner already admitted."""
    if "retire_srd_illusion_source" not in tools:
        return
    dismiss = changed["dismiss_srd_spell"]
    selection = [
        _set("_dismiss_illusion_ids", []),
        _set("_dismiss_scan_illusion_ids", []),
        _when(
            "lasting_state.has_illusion_spells == true",
            [
                _set("_dismiss_scan_illusion_ids", "lasting_state.illusion_spell_source_ids"),
            ],
        ),
        _each(
            "_dismiss_scan_illusion_ids",
            "_dismiss_illusion_id",
            [
                _call(
                    "collect_srd_illusion_source",
                    {"illusion_id": "{_dismiss_illusion_id}"},
                    ("_illusion_record", "_illusion_active"),
                ),
                _when(
                    "_illusion_active == true and _illusion_record.cast_id == cast_id and "
                    "_illusion_record.source_caster_id == _caster_state_id and "
                    "_illusion_record.ongoing_duration == true and "
                    "_illusion_record.expires_tick != null",
                    [
                        _push("_dismiss_illusion_ids", "_dismiss_illusion_id"),
                        _set("_dismiss_count", "_dismiss_count + 1"),
                    ],
                ),
            ],
        ),
    ]
    requirement = next(i for i, e in enumerate(dismiss["effects"]) if "require" in e)
    dismiss["effects"][requirement:requirement] = selection
    dismiss["effects"].insert(
        requirement + len(selection) + 1,
        _each(
            "_dismiss_illusion_ids",
            "_dismiss_illusion_id",
            [
                _call(
                    "retire_srd_illusion_source",
                    {
                        "illusion_id": "{_dismiss_illusion_id}",
                        "clear_concentration": False,
                    },
                ),
            ],
        ),
    )
    dismiss["effects"].append(
        _when(
            "len(_dismiss_illusion_ids) > 0",
            [
                _call("refresh_srd_illusion_spells", {}),
            ],
        )
    )

    node_name = (
        "dismiss_srd_illusion_spell_node"
        if "dismiss_srd_illusion_spell_node" in tools
        else "dismiss_srd_illusion_spell"
    )
    old = deepcopy(tools[node_name])
    if not any(p["name"] == "lasting_state" for p in old["parameters"]):
        old["parameters"].append(_lasting() | {"model_hidden": True})
    # Retain the ordinary public pending-action fence before source selection.
    fences = [e for e in old["effects"] if "pending" in str(e).lower() and "require" in e]
    old["effects"] = fences + [
        _call(
            "collect_srd_illusion_source",
            {"illusion_id": "{illusion_id}"},
            ("_illusion_record", "_illusion_active"),
        ),
        _require(
            "_illusion_active == true and _illusion_record.caster == _caster_state_id "
            "and _illusion_record.cast_id != null and _illusion_record.cast_id != '' "
            "and _illusion_record.ongoing_duration == true and "
            "_illusion_record.expires_tick != null",
            "Select this caster's known finite cast; permanent or legacy images "
            "cannot be grouped for dismissal.",
        ),
        _call(
            "dismiss_srd_spell",
            {
                "caster_state": "{_caster_state_id}",
                "lasting_state": "{_lasting_state_id}",
                "cast_id": "{_illusion_record.cast_id}",
            },
        ),
        _set("_illusion_dismissed", True),
    ]
    changed[node_name] = old

    changed["dispel_srd_illusion_source"] = _tool(
        "Qualify an actual saved image or Mislead subject, roll the real source-level "
        "Dispel check, and retire only that exact source.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("illusion_id"),
            _param("select_proxy", "boolean", required=False, default=True),
            _param("caster", resolve="participant", required=False),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
        ],
        [
            _call(
                "collect_srd_illusion_source",
                {"illusion_id": "{illusion_id}"},
                ("_illusion_record", "_illusion_active"),
            ),
            _require(
                "_illusion_active == true and _illusion_record.dispel_ongoing == true",
                "Select an actual active ongoing illusion source.",
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_caster_state_id}"},
                ("_info_scene_entity", "_info_position"),
            ),
            _set("_illusion_dispel_origin", "_info_position"),
            _set("_illusion_dispel_plane", "_info_scene_entity.metadata.plane"),
            _when(
                "select_proxy == true",
                [
                    _call(
                        "resolve_srd_illusion_volume",
                        {"illusion_id": "{illusion_id}"},
                        ("_illusion_volume", "_illusion_plane"),
                    ),
                    _require(
                        "_illusion_plane == _illusion_dispel_plane",
                        "The actual image must be on the Dispel caster's plane.",
                    ),
                    _call(
                        "srd_information_volume_point",
                        {
                            "origin": "{_illusion_dispel_origin}",
                            "volume": "{_illusion_volume}",
                        },
                        ("_volume_point",),
                    ),
                    _set("_illusion_dispel_point", "_volume_point"),
                ],
                [
                    _require(
                        "(_illusion_record.spell == 'mislead' and "
                        "_illusion_record.caster == _target_state_id) or "
                        "((_illusion_record.spell == 'mirror_image' or "
                        "_illusion_record.spell == 'phantasmal_force') and "
                        "_illusion_record.target == _target_state_id)",
                        "Select an illusion that actually affects this creature.",
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        ("_info_scene_entity", "_info_position"),
                    ),
                    _require(
                        "_info_scene_entity.metadata.plane == _illusion_dispel_plane",
                        "The actual Mislead subject must be on the Dispel caster's plane.",
                    ),
                    _set("_illusion_dispel_point", "_info_position"),
                ],
            ),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_illusion_dispel_origin}",
                    "end": "{_illusion_dispel_point}",
                },
                ("_scene_distance",),
            ),
            _require(
                "_scene_distance <= 120", "The actual illusion effect is beyond Dispel range."
            ),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_illusion_dispel_origin}",
                    "end": "{_illusion_dispel_point}",
                    "rule": "solid",
                    "plane": "{_illusion_dispel_plane}",
                },
                ("_information_blocked",),
            ),
            _require(
                "_information_blocked != true", "A solid barrier blocks the actual Dispel path."
            ),
            _set("_illusion_dispel_level", -1),
            _when(
                "_illusion_record.spell_level != null",
                [
                    _set("_illusion_dispel_level", "_illusion_record.spell_level"),
                ],
            ),
            _call(
                "resolve_srd_dispel_source_check",
                {
                    "caster_state": "{_caster_state_id}",
                    "caster": "{_caster_id}",
                    "source_level": "{_illusion_dispel_level}",
                    "slot_level": "{slot_level}",
                    "casting_modifier": "{casting_modifier}",
                    "casting_ability": "{casting_ability}",
                },
                ("_dispel_succeeded", "_dispel_roll"),
            ),
            _set(
                "_illusion_dispel_check",
                {
                    "spell": "_illusion_record.spell",
                    "source_id": "illusion_id",
                    "ended": "_dispel_succeeded",
                    "roll": "_dispel_roll",
                },
            ),
            _when(
                "_dispel_succeeded == true",
                [
                    _call("retire_srd_illusion_source", {"illusion_id": "{illusion_id}"}),
                    _call("refresh_srd_illusion_spells", {}),
                ],
            ),
        ],
    )
    params = {
        "caster_state": "{_caster_state_id}",
        "target_state": "{_target_state_id}",
        "lasting_state": "{_lasting_state_id}",
        "caster": "{_caster_id}",
        "slot_level": "{slot_level}",
        "casting_modifier": "{casting_modifier}",
        "casting_ability": "{casting_ability}",
    }
    recovery = changed["cast_srd_recovery"]

    def qualify(effects, guarded=False):
        for i, effect in enumerate(effects):
            protected = guarded or effect.get("condition") == "recovery_choice != 'illusion'"
            if effect.get("call") == "dispel_srd_target_sources" and not protected:
                effects[i] = _when("recovery_choice != 'illusion'", [effect])
            else:
                for key in ("on_true", "on_false"):
                    if key in effect:
                        qualify(effect[key], protected)

    recovery["effects"] = [
        e
        for e in recovery["effects"]
        if e.get("condition") != "spell == 'dispel_magic' and recovery_choice == 'illusion'"
    ]
    qualify(recovery["effects"])
    recovery["effects"].append(
        _when(
            "spell == 'dispel_magic' and recovery_choice == 'illusion'",
            [
                _call(
                    "dispel_srd_illusion_source",
                    params
                    | {
                        "illusion_id": "{recovery_source_id}",
                        "select_proxy": True,
                    },
                    ("_illusion_dispel_check",),
                ),
                _set("_dispel_checks", ["_illusion_dispel_check"]),
                _set(
                    "_spell_summary_rows",
                    [
                        {
                            "label": "'Selected illusion ended'",
                            "value": "_illusion_dispel_check.ended",
                        }
                    ],
                ),
                _set(
                    "_spell_detail_rows",
                    [
                        {"label": "'Illusion source'", "value": "recovery_source_id"},
                        {"label": "'Actual Dispel check'", "value": "_illusion_dispel_check.roll"},
                    ],
                ),
            ],
        )
    )
    dispel = changed["dispel_srd_target_sources"]
    marker = "lasting_state.has_illusion_spells == true"
    dispel["effects"] = [
        e
        for e in dispel["effects"]
        if not (e.get("condition") == marker and "_mislead_dispel_ids" in str(e))
    ]
    dispel["effects"].append(
        _when(
            marker,
            [
                _set("_mislead_dispel_ids", "lasting_state.illusion_spell_source_ids"),
                _each(
                    "_mislead_dispel_ids",
                    "_mislead_dispel_id",
                    [
                        _call(
                            "collect_srd_illusion_source",
                            {"illusion_id": "{_mislead_dispel_id}"},
                            ("_illusion_record", "_illusion_active"),
                        ),
                        _when(
                            "_illusion_active == true and ((_illusion_record.spell == 'mislead' "
                            "and _illusion_record.caster == _target_state_id) or "
                            "((_illusion_record.spell == 'mirror_image' or "
                            "_illusion_record.spell == 'phantasmal_force') and "
                            "_illusion_record.target == _target_state_id))",
                            [
                                _call(
                                    "dispel_srd_illusion_source",
                                    params
                                    | {
                                        "illusion_id": "{_mislead_dispel_id}",
                                        "select_proxy": False,
                                    },
                                    ("_illusion_dispel_check",),
                                ),
                                _push("_dispel_checks", "_illusion_dispel_check"),
                            ],
                        ),
                    ],
                ),
            ],
        )
    )


def _minor_world_removals(tools, changed):
    """Select the actual printed ongoing clause, preserving Instantaneous consequences."""
    if "read_srd_minor_world_source" not in tools:
        return
    dismiss = changed["dismiss_srd_spell"]
    dismiss["effects"][:0] = [
        _when(
            "lasting_state.turn_effects != null",
            [
                _call("refresh_srd_minor_world_effects", {}),
            ],
        )
    ]
    for node in _walk(dismiss["effects"]):
        if "_dismiss_effect.cast_id == cast_id" in node.get("condition", ""):
            node["condition"] += " and _dismiss_effect.kind != 'minor_world_spell'"
    selection = [
        _set("_dismiss_minor_ids", []),
        _each(
            "_dismiss_effects",
            "_dismiss_minor",
            [
                _when(
                    "_dismiss_minor.kind == 'minor_world_spell' and "
                    "_dismiss_minor.cast_id == cast_id and "
                    "_dismiss_minor.source_caster_id == _caster_state_id and "
                    "_dismiss_minor.ongoing_duration == true and "
                    "_dismiss_minor.dispel_ongoing == true",
                    [
                        _call(
                            "read_srd_minor_world_source",
                            {"source_id": "{_dismiss_minor.source_id}"},
                            ("_minor_world_source",),
                        ),
                        _push("_dismiss_minor_ids", "_dismiss_minor.source_id"),
                        _set("_dismiss_count", "_dismiss_count + 1"),
                    ],
                ),
            ],
        ),
    ]
    requirement = next(i for i, e in enumerate(dismiss["effects"]) if "require" in e)
    dismiss["effects"][requirement:requirement] = selection
    # The owner removes its exact scene output too, including next-turn sources
    # whose lifetime is represented by the real turn rather than a numeric expiry.
    dismiss["effects"].append(
        _each(
            "_dismiss_minor_ids",
            "_dismiss_minor_id",
            [
                _call(
                    "retire_srd_minor_world_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "source_id": "{_dismiss_minor_id}",
                    },
                ),
            ],
        )
    )
    changed["dispel_srd_minor_world_source"] = _tool(
        "Use an actual active minor spell clause and its admitted location for a real "
        "source-level Dispel check and exact output removal.",
        [
            _entity("caster_state"),
            _lasting(),
            _param("source_id"),
            _param("caster", resolve="participant", required=False),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
        ],
        [
            _call(
                "read_srd_minor_world_source",
                {"source_id": "{source_id}"},
                ("_minor_world_source",),
            ),
            _set("_minor_dispel_subject", "''"),
            _when(
                "_minor_world_source.mode == 'altered_eyes' or "
                "_minor_world_source.mode == 'booming_voice'",
                [
                    _set("_minor_dispel_subject", "_minor_world_source.caster"),
                ],
            ),
            _set("_minor_dispel_object", "_minor_world_source.object_id"),
            _when(
                "_minor_world_source.mode == 'minor_creation'",
                [
                    _set("_minor_dispel_object", "_minor_world_source.output_id"),
                ],
            ),
            _when(
                "_minor_dispel_subject != ''",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_minor_dispel_subject}"},
                        ("_info_scene_entity", "_info_position"),
                    ),
                    _set("_minor_dispel_point", "_info_position"),
                    _set("_minor_dispel_plane", "_info_scene_entity.metadata.plane"),
                ],
                [
                    _when(
                        "_minor_dispel_object != ''",
                        [
                            _call(
                                "resolve_srd_object_content_geometry",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "object_id": "{_minor_dispel_object}",
                                    "range_feet": 120,
                                    "requires_sight": False,
                                },
                                ("_object_scene",),
                            ),
                            _set("_minor_dispel_point", "_object_scene.position"),
                            _set("_minor_dispel_plane", "_object_scene.metadata.plane"),
                        ],
                        [
                            _set("_minor_dispel_point", "_minor_world_source.origin"),
                            _set("_minor_dispel_plane", "_minor_world_source.plane"),
                        ],
                    ),
                ],
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_caster_state_id}"},
                ("_info_scene_entity", "_info_position"),
            ),
            _require(
                "_info_scene_entity.metadata.plane == _minor_dispel_plane",
                "The actual minor effect is on another plane.",
            ),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_info_position}",
                    "end": "{_minor_dispel_point}",
                },
                ("_scene_distance",),
            ),
            _require("_scene_distance <= 120", "The actual minor effect is beyond Dispel range."),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_info_position}",
                    "end": "{_minor_dispel_point}",
                    "plane": "{_minor_dispel_plane}",
                    "rule": "solid",
                },
                ("_information_blocked",),
            ),
            _require(
                "_information_blocked != true", "A solid barrier blocks the actual Dispel path."
            ),
            _call(
                "resolve_srd_dispel_source_check",
                {
                    "caster_state": "{_caster_state_id}",
                    "caster": "{_caster_id}",
                    "slot_level": "{slot_level}",
                    "source_level": "{_minor_world_source.spell_level}",
                    "casting_modifier": "{casting_modifier}",
                    "casting_ability": "{casting_ability}",
                },
                ("_dispel_succeeded", "_dispel_roll"),
            ),
            _set(
                "_minor_dispel_check",
                {
                    "spell": "_minor_world_source.spell",
                    "source_id": "source_id",
                    "ended": "_dispel_succeeded",
                    "roll": "_dispel_roll",
                },
            ),
            _when(
                "_dispel_succeeded == true",
                [
                    _call(
                        "retire_srd_minor_world_source",
                        {
                            "caster_state": "{_minor_world_source.caster}",
                            "source_id": "{source_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    dispel = changed["dispel_srd_target_sources"]
    for node in _walk(dispel["effects"]):
        if "_dispel_effect.target == _target_state_id" in node.get("condition", "") and (
            "_dispel_effect.kind != 'minor_world_spell'" not in node["condition"]
        ):
            node["condition"] += " and _dispel_effect.kind != 'minor_world_spell'"
    marker = "lasting_state.turn_effects != null"
    dispel["effects"] = [
        e
        for e in dispel["effects"]
        if not (e.get("condition") == marker and "_minor_creature_dispel_effects" in str(e))
    ]
    dispel["effects"].append(
        _when(
            marker,
            [
                _set("_minor_creature_dispel_effects", "lasting_state.turn_effects"),
                _each(
                    "_minor_creature_dispel_effects",
                    "_minor_creature_dispel_effect",
                    [
                        _when(
                            "_minor_creature_dispel_effect.kind == 'minor_world_spell' and "
                            "_minor_creature_dispel_effect.caster == _target_state_id and "
                            "_minor_creature_dispel_effect.dispel_ongoing == true and "
                            "(_minor_creature_dispel_effect.mode == 'altered_eyes' or "
                            "_minor_creature_dispel_effect.mode == 'booming_voice')",
                            [
                                _call(
                                    "dispel_srd_minor_world_source",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "lasting_state": "{_lasting_state_id}",
                                        "source_id": "{_minor_creature_dispel_effect.source_id}",
                                        "caster": "{_caster_id}",
                                        "slot_level": "{slot_level}",
                                        "casting_modifier": "{casting_modifier}",
                                        "casting_ability": "{casting_ability}",
                                    },
                                    ("_minor_dispel_check",),
                                ),
                                _push("_dispel_checks", "_minor_dispel_check"),
                            ],
                        ),
                    ],
                ),
            ],
        )
    )
    recovery = changed["cast_srd_recovery"]
    marker = "spell == 'dispel_magic' and recovery_choice == 'minor_world'"
    recovery["effects"] = [e for e in recovery["effects"] if e.get("condition") != marker]

    def qualify(effects, guarded=False):
        for i, effect in enumerate(effects):
            protected = guarded or effect.get("condition") == "recovery_choice != 'minor_world'"
            if effect.get("call") == "dispel_srd_target_sources" and not protected:
                effects[i] = _when("recovery_choice != 'minor_world'", [effect])
            else:
                for key in ("on_true", "on_false"):
                    if key in effect:
                        qualify(effect[key], protected)

    qualify(recovery["effects"])
    recovery["effects"].append(
        _when(
            marker,
            [
                _call(
                    "dispel_srd_minor_world_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "source_id": "{recovery_source_id}",
                        "caster": "{_caster_id}",
                        "slot_level": "{slot_level}",
                        "casting_modifier": "{casting_modifier}",
                        "casting_ability": "{casting_ability}",
                    },
                    ("_minor_dispel_check",),
                ),
                _set("_dispel_checks", ["_minor_dispel_check"]),
                _set(
                    "_spell_summary_rows",
                    [
                        {
                            "label": "'Selected minor effect ended'",
                            "value": "_minor_dispel_check.ended",
                        }
                    ],
                ),
                _set(
                    "_spell_detail_rows",
                    [{"label": "'Minor spell source'", "value": "recovery_source_id"}],
                ),
            ],
        )
    )


def _object_utility_removals(tools, changed):
    """Let exact finite force owners release cargo before shared concentration cleanup."""
    if "collect_srd_object_utility_dismiss_sources" not in tools:
        return
    dismiss = changed["dismiss_srd_spell"]
    for node in _walk(dismiss["effects"]):
        if "_dismiss_effect.cast_id == cast_id" in node.get("condition", ""):
            node["condition"] += " and _dismiss_effect.kind != 'object_utility_spell'"
    index = next(i for i, effect in enumerate(dismiss["effects"]) if "require" in effect)
    dismiss["effects"][index:index] = [
        _call(
            "collect_srd_object_utility_dismiss_sources",
            {"caster_state": "{_caster_state_id}", "cast_id": "{cast_id}"},
            ("_utility_dismiss_sources",),
        ),
        _set("_dismiss_count", "_dismiss_count + len(_utility_dismiss_sources)"),
    ]
    index = next(
        i
        for i, effect in enumerate(dismiss["effects"])
        if effect.get("condition", "").startswith("_dismiss_concentration_owned == true")
    )
    dismiss["effects"].insert(
        index,
        _each(
            "_utility_dismiss_sources",
            "_utility_dismiss_source",
            [
                _call(
                    "retire_srd_object_utility_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "source_id": "{_utility_dismiss_source.source_id}",
                    },
                )
            ],
        ),
    )


def extend_runtime(text):
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    changed = _tools(runtime["tools"])
    recovery = deepcopy(runtime["tools"]["cast_srd_recovery"])
    for node in _walk(recovery["effects"]):
        if node.get("call") == "remove_srd_condition" and (
            node.get("params", {}).get("condition") == "{recovery_choice}"
        ):
            node.update(
                _call(
                    "remove_srd_one_condition_effect",
                    {
                        "target_state": "{_target_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "condition": "{recovery_choice}",
                        "source_id": "{recovery_source_id}",
                    },
                )
            )
    changed["cast_srd_recovery"] = recovery
    dispel = deepcopy(runtime["tools"]["dispel_srd_target_sources"])
    for node in _walk(dispel["effects"]):
        condition = node.get("condition", "")
        if "_recovery_source.dispel_ongoing != false" in condition and (
            "definitions.spell_casting" not in condition
        ):
            node["condition"] = condition + (
                " and _recovery_source.spell_id in definitions.spell_casting and "
                "definitions.spell_casting[_recovery_source.spell_id].duration != 'Instantaneous'"
            )
        if "_dispel_effect.target == _target_state_id" in condition and (
            "definitions.spell_casting" not in condition
        ):
            node["condition"] = condition + (
                " and _dispel_effect.dispel_ongoing != false and "
                "_dispel_effect.spell in definitions.spell_casting and "
                "definitions.spell_casting[_dispel_effect.spell].duration != 'Instantaneous'"
            )
    changed["dispel_srd_target_sources"] = dispel
    if "end_srd_etherealness_target" in runtime["tools"]:
        old_public = deepcopy(runtime["tools"]["dismiss_srd_etherealness"])
        if not any(p["name"] == "cast_id" for p in old_public["parameters"]):
            old_public["parameters"].append(_param("cast_id", required=False, default=""))
        if not any(p["name"] == "lasting_state" for p in old_public["parameters"]):
            old_public["parameters"].append(_lasting() | {"model_hidden": True})
        node_name = (
            "dismiss_srd_etherealness_node"
            if "dismiss_srd_etherealness_node" in runtime["tools"]
            else "dismiss_srd_etherealness"
        )
        exact_node = deepcopy(runtime["tools"][node_name])
        if not any(p["name"] == "cast_id" for p in exact_node["parameters"]):
            exact_node["parameters"].append(_param("cast_id", required=False, default=""))
        if not any(p["name"] == "lasting_state" for p in exact_node["parameters"]):
            exact_node["parameters"].append(_lasting() | {"model_hidden": True})
        exact_node["effects"] = [
            _call(
                "choose_srd_etherealness_dismissal_cast",
                {
                    "caster_state": "{_caster_state_id}",
                    "cast_id": "{cast_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_ethereal_dismiss_cast_id",),
            ),
            _call(
                "dismiss_srd_spell",
                {
                    "caster_state": "{_caster_state_id}",
                    "cast_id": "{_ethereal_dismiss_cast_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_dismiss_count",),
            ),
            _set("_travel_dismiss_count", "_dismiss_count"),
        ]
        changed[node_name] = exact_node
        if node_name != "dismiss_srd_etherealness":
            for effect in _walk(old_public["effects"]):
                if effect.get("call") == node_name:
                    effect["params"]["cast_id"] = "{cast_id}"
            changed["dismiss_srd_etherealness"] = old_public
        dispel["effects"] = [
            e
            for e in dispel["effects"]
            if "dispel_srd_etherealness_target" not in str(e)
            and e.get("for_each", {}).get("collection") != "_ethereal_dispel_checks"
        ]
        dispel["effects"].append(
            _call(
                "dispel_srd_etherealness_target",
                {
                    "target_state": "{_target_state_id}",
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                    "caster": "{_caster_id}",
                    "slot_level": "{slot_level}",
                    "casting_modifier": "{casting_modifier}",
                    "casting_ability": "{casting_ability}",
                },
                ("_ethereal_dispel_checks",),
            )
        )
        dispel["effects"].append(
            _each(
                "_ethereal_dispel_checks",
                "_ethereal_dispel_check",
                [_push("_dispel_checks", "_ethereal_dispel_check")],
            )
        )
        changed["dispel_srd_etherealness_target"] = _tool(
            "Dispel one actual ongoing Etherealness grant through its source-level "
            "check and return owner.",
            [
                _entity("target_state"),
                _entity("caster_state"),
                _lasting(),
                _param("caster", resolve="participant", required=False),
                _param("slot_level", "integer"),
                _param("casting_modifier", "integer"),
                _param("casting_ability"),
            ],
            [
                _set("_ethereal_dispel_checks", []),
                _set("_ethereal_dispel_source", "target_state.travel_ethereal"),
                _when(
                    "_ethereal_dispel_source != null and "
                    "_ethereal_dispel_source.spell == 'etherealness' and "
                    "_ethereal_dispel_source.dispel_ongoing != false and "
                    "_ethereal_dispel_source.expires_tick != null and "
                    "lasting_state.lasting_tick < _ethereal_dispel_source.expires_tick",
                    [
                        _call(
                            "resolve_srd_scene_geometry_relation",
                            {
                                "actor_state": "{_caster_state_id}",
                                "target_state": "{_target_state_id}",
                            },
                            (
                                "_scene_same_plane",
                                "_scene_distance",
                                "_scene_actor_position",
                                "_scene_target_position",
                            ),
                        ),
                        _require(
                            "_scene_same_plane == true and _scene_distance <= 120",
                            "Dispel Magic needs this actual Ethereal recipient on the "
                            "caster's plane and within 120 feet.",
                        ),
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_caster_state_id}"},
                            ("_info_scene_entity",),
                        ),
                        _call(
                            "srd_information_barriers",
                            {
                                "start": "{_scene_actor_position}",
                                "end": "{_scene_target_position}",
                                "rule": "solid",
                                "plane": "{_info_scene_entity.metadata.plane}",
                            },
                            ("_information_blocked",),
                        ),
                        _require(
                            "_information_blocked != true",
                            "A solid barrier blocks Dispel Magic's actual path to this recipient.",
                        ),
                        _set("_ethereal_dispel_level", -1),
                        _when(
                            "_ethereal_dispel_source.slot_level != null",
                            [_set("_ethereal_dispel_level", "_ethereal_dispel_source.slot_level")],
                        ),
                        _call(
                            "resolve_srd_dispel_source_check",
                            {
                                "caster_state": "{_caster_state_id}",
                                "caster": "{_caster_id}",
                                "source_level": "{_ethereal_dispel_level}",
                                "slot_level": "{slot_level}",
                                "casting_modifier": "{casting_modifier}",
                                "casting_ability": "{casting_ability}",
                            },
                            ("_dispel_succeeded", "_dispel_roll"),
                        ),
                        _push(
                            "_ethereal_dispel_checks",
                            {
                                "spell": "'etherealness'",
                                "ended": "_dispel_succeeded",
                                "roll": "_dispel_roll",
                            },
                        ),
                        _when(
                            "_dispel_succeeded == true",
                            [
                                _call(
                                    "end_srd_etherealness_target",
                                    {"target_state": "{_target_state_id}"},
                                ),
                                _call("refresh_srd_etherealness_sources", {}),
                            ],
                        ),
                    ],
                ),
            ],
        )
    _illusion_removals(runtime["tools"], changed)
    _minor_world_removals(runtime["tools"], changed)
    _object_utility_removals(runtime["tools"], changed)
    extend_removal_adoption(runtime["tools"], changed)
    # Suppression does not erase source ownership; retirement reprojects rather
    # than clearing a shared effective condition on an actor.
    return _replace_tools(text, deepcopy(changed))
