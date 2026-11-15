"""Guards and Wards retains its own groups, area and real Arcane Lock child sources."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import _each, _push
    from .srd_scene_relations import _array
    from .srd_spell_barriers import _effect_lists, _scene, _tool
    from .srd_spell_magic_zones import _when
    from .srd_spell_object_content import _arcane_lock, _metadata_patch, _now
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _optional, _replace_tools
else:
    from srd_control_behaviors import _each, _push
    from srd_scene_relations import _array
    from srd_spell_barriers import _effect_lists, _scene, _tool
    from srd_spell_magic_zones import _when
    from srd_spell_object_content import _arcane_lock, _metadata_patch, _now
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _optional, _replace_tools


PROFILES = {
    "guards_and_wards": {
        "family": "guarded_area",
        "base_level": 6,
        "casting_time": "1_hour",
        "range_kind": "touch",
        "range_feet": 0,
        "duration_rounds": 14400,
        "concentration": False,
    }
}
GROUPS = ["corridors", "doors", "stairs", "other"]
PUBLIC_TOOLS = {"speak_srd_guards_wards_password"}


def validate_profiles(definitions):
    """Keep the fixed parent header separate from every referenced child spell's costs."""
    for spell, profile in definitions.items():
        if profile.get("family") == "guarded_area" and profile != PROFILES.get(spell):
            raise SystemExit(f"Guards and Wards profile differs from its pinned source: {spell}")


def _read():
    return [
        _require(
            "state.spell_guards_wards_sources != null and source_id in "
            "state.spell_guards_wards_sources",
            "Select an actual Guards and Wards source.",
        ),
        _set("_guards_source", "state.spell_guards_wards_sources[source_id]"),
    ]


def _active():
    return (
        "(_guards_source.installed == true and _guards_source.suppressed != true and "
        "(_guards_source.permanent == true or state.lasting_tick < _guards_source.expires_tick))"
    )


def _sources(effects):
    return [
        _when(
            "state.has_guards_wards_sources == true",
            [
                _set("_guards_source_ids", "state.spell_guards_wards_source_ids"),
                _each(
                    "_guards_source_ids",
                    "_guards_id",
                    [
                        _set("_guards_source", "state.spell_guards_wards_sources[_guards_id]"),
                        _when(_active(), effects),
                    ],
                ),
            ],
        )
    ]


def _point(point, box, output):
    return [
        _set(
            output,
            " and ".join(
                f"{point}[{a}] >= {box}.min[{a}] and {point}[{a}] <= {box}.max[{a}]"
                for a in range(3)
            ),
        )
    ]


def _exempt(actor_id):
    return (
        f"({actor_id} in _guards_source.designated_creatures or "
        f"{actor_id} in _guards_source.password_speakers)"
    )


def _daily_history():
    """Count real consecutive daily castings from the exact prior source and same warded area."""
    return [
        _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
        _set("_guards_cast_day", "state.lasting_tick // 28800"),
        _set("_guards_daily_count", 1),
        _set("_guards_history_id", None),
        _when(
            "state.guards_wards_daily_history == null",
            [_set("state.guards_wards_daily_history", [])],
        ),
        _set("_guards_histories", "state.guards_wards_daily_history"),
        _set("_guards_axes", [0, 1, 2]),
        _each(
            "_guards_histories",
            "_guards_history",
            [
                _set(
                    "_guards_history_matches",
                    "_guards_history.caster_state_id == _caster_state_id and "
                    "_guards_history.plane == _guards_caster.metadata.plane",
                ),
                _each(
                    "_guards_axes",
                    "_guards_axis",
                    [
                        _when(
                            "_guards_history.box.min[_guards_axis] != "
                            "_guards_box.min[_guards_axis] or "
                            "_guards_history.box.max[_guards_axis] != "
                            "_guards_box.max[_guards_axis]",
                            [_set("_guards_history_matches", False)],
                        ),
                    ],
                ),
                _when(
                    "_guards_history_matches == true",
                    [
                        _require(
                            "state.spell_guards_wards_sources != null and "
                            "_guards_history.source_id in "
                            "state.spell_guards_wards_sources",
                            "Permanence needs the actual prior ward source.",
                        ),
                        _set(
                            "_guards_daily_source",
                            "state.spell_guards_wards_sources[_guards_history.source_id]",
                        ),
                        _require(
                            "_guards_daily_source.spell_id == 'guards_and_wards' and "
                            "_guards_daily_source.caster_state_id == _caster_state_id and "
                            "_guards_daily_source.plane == _guards_caster.metadata.plane and "
                            "_guards_daily_source.daily_count == _guards_history.days and "
                            "_guards_daily_source.daily_day == _guards_history.last_day",
                            "The daily history must match its actual saved ward source.",
                        ),
                        _each(
                            "_guards_axes",
                            "_guards_axis",
                            [
                                _require(
                                    "_guards_daily_source.box.min[_guards_axis] == "
                                    "_guards_box.min[_guards_axis] and "
                                    "_guards_daily_source.box.max[_guards_axis] == "
                                    "_guards_box.max[_guards_axis]",
                                    "The daily area must match its actual prior source.",
                                ),
                            ],
                        ),
                        _set("_guards_history_id", "_guards_history.source_id"),
                        _when(
                            "_guards_daily_source.daily_day == _guards_cast_day",
                            [_set("_guards_daily_count", "_guards_daily_source.daily_count")],
                            [
                                _when(
                                    "_guards_daily_source.daily_day == _guards_cast_day - 1",
                                    [
                                        _set(
                                            "_guards_daily_count",
                                            "_guards_daily_source.daily_count + 1",
                                        )
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]


def _lock_tools():
    # Reuse the real Arcane Lock record/mutation owner, adding only its parent membership.
    install = deepcopy(_arcane_lock())

    def bind_child(value):
        if isinstance(value, str):
            for original, derived in {
                "slot_level": "_guards_child_level",
                "object_designated_creatures": "_guards_child_designations",
                "object_password": "_guards_child_password",
            }.items():
                value = re.sub(r"\b" + original + r"\b", derived, value)
            return value
        if isinstance(value, list):
            return [bind_child(item) for item in value]
        if isinstance(value, dict):
            return {key: bind_child(item) for key, item in value.items()}
        return value

    install = bind_child(install)
    candidates = [e for e in install if e.get("list_push", {}).get("list") == "_world_locks"]
    assert len(candidates) == 1
    push = candidates[0]["list_push"]
    push["value"].update(ward_source_id="source_id", ward_group="'doors'")
    helper = _tool(
        "Install the parent ward's paid door effect through real Arcane Lock authority.",
        [
            _entity("caster_state"),
            _parameter("source_id"),
            _parameter("object_id"),
        ],
        [
            *_read(),
            _require(
                _active() + " and _guards_source.groups.doors == true",
                "The parent door group is not active.",
            ),
            *_scene("object_id", "_object_scene"),
            *_now(),
            _set("_guards_child_level", "_guards_source.spell_level"),
            _set("_guards_child_designations", "_guards_source.designated_creatures"),
            _set("_guards_child_password", "''"),
            _set("_spell_summary_rows", []),
            *install,
        ],
    )
    remove = _tool(
        "Remove only the Arcane Lock children belonging to the exact ward group.",
        [
            _parameter("source_id"),
            _parameter("object_id"),
        ],
        [
            *_scene("object_id", "_object_scene"),
            _set("_world_locks", []),
            _when(
                "_object_scene.metadata.srd_arcane_locks != null",
                [_set("_world_locks", "_object_scene.metadata.srd_arcane_locks")],
            ),
            _set("_guards_kept_locks", []),
            _each(
                "_world_locks",
                "_world_lock",
                [
                    _when(
                        "_world_lock.ward_source_id == null or "
                        "_world_lock.ward_source_id != source_id",
                        [_push("_guards_kept_locks", "_world_lock")],
                    ),
                ],
            ),
            *_metadata_patch({"srd_arcane_locks": "_guards_kept_locks"}),
        ],
    )
    exempt = _tool(
        "Grant only a source-qualified ward speaker immunity to its actual child door locks.",
        [_entity("actor_state"), _parameter("source_id"), _parameter("object_id")],
        [
            *_read(),
            _require(_exempt("_actor_state_id"), "The creature has no actual ward exemption."),
            *_scene("object_id", "_object_scene"),
            _set("_guards_updated_locks", []),
            _set("_world_locks", "_object_scene.metadata.srd_arcane_locks"),
            _each(
                "_world_locks",
                "_world_lock",
                [
                    _set("_guards_updated_lock", "_world_lock"),
                    _when(
                        "_world_lock.ward_source_id != null and "
                        "_world_lock.ward_source_id == source_id",
                        [
                            _set("_guards_lock_exemptions", "_world_lock.designated_creatures"),
                            _when(
                                "_actor_state_id not in _guards_lock_exemptions",
                                [_push("_guards_lock_exemptions", "_actor_state_id")],
                            ),
                            _set(
                                "_guards_exemption_change",
                                {"designated_creatures": "_guards_lock_exemptions"},
                            ),
                            _set(
                                "_guards_updated_lock",
                                "merge(_world_lock, _guards_exemption_change)",
                            ),
                        ],
                    ),
                    _push("_guards_updated_locks", "_guards_updated_lock"),
                ],
            ),
            *_metadata_patch({"srd_arcane_locks": "_guards_updated_locks"}),
        ],
    )
    return {
        "install_srd_guards_wards_door": helper,
        "remove_srd_guards_wards_door": remove,
        "grant_srd_guards_wards_door_exemption": exempt,
    }


def _cast_tools():
    parameters = [
        _entity("caster_state"),
        _parameter("slot_level", "integer"),
        _array("origin"),
        _optional("height", "number", 20),
        _optional("designated_creatures", "array", [], items={"type": "string"}),
        _optional("password", default=""),
        _optional("corridors", "array", [], items={"type": "object"}),
        _optional(
            "lamp_positions",
            "array",
            [],
            items={"type": "array", "items": {"type": "number"}},
            max_items=4,
        ),
        _optional("caster_can_touch_origin", "boolean", False),
        _optional("spell_cast_id", default="", model_hidden=True),
    ]
    install = _tool(
        "Install the real parent ward without second child spell costs or concentration.",
        parameters,
        [
            _require(
                "slot_level >= 6 and slot_level <= 9 and height > 0 and height <= 20",
                "Use the actual parent slot and a height no greater than20feet.",
            ),
            *_scene("_caster_state_id", "_guards_caster"),
            _call(
                "measure_srd_information_distance",
                {"start": "{_guards_caster.position}", "end": "{origin}"},
                ("_scene_distance",),
            ),
            _require(
                "caster_can_touch_origin == true and _scene_distance <= 5",
                "The ward starts at an actual point the caster can touch.",
            ),
            _set(
                "_guards_box",
                {
                    "min": "origin",
                    "max": ["origin[0] + 50", "origin[1] + 50", "origin[2] + height"],
                },
            ),
            *[
                _require(
                    f"_guards_box.min[{a}] >= state.spatial_scene.map.bounds.min[{a}] and "
                    f"_guards_box.max[{a}] <= state.spatial_scene.map.bounds.max[{a}]",
                    "The actual50-foot square must remain inside the admitted map.",
                )
                for a in range(3)
            ],
            _each(
                "designated_creatures",
                "_guards_designee",
                [
                    _call(
                        "validate_srd_object_lock_creature",
                        {"creature_state": "{_guards_designee}"},
                    )
                ],
            ),
            _each(
                "corridors",
                "_guards_corridor",
                [
                    *[
                        _require(
                            f"_guards_corridor.min[{a}] >= _guards_box.min[{a}] and "
                            f"_guards_corridor.max[{a}] <= _guards_box.max[{a}] and "
                            f"_guards_corridor.min[{a}] < _guards_corridor.max[{a}]",
                            "Admit each real corridor Box entirely within the ward.",
                        )
                        for a in range(3)
                    ],
                ],
            ),
            _require(
                "len(lamp_positions) == 4",
                "This supported Other group uses four genuine stationary Dancing Lights.",
            ),
            _require("len(corridors) >= 4", "Place the four lights in four admitted corridors."),
            _set("_guards_lamp_index", 0),
            _each(
                "lamp_positions",
                "_guards_lamp",
                [
                    _require("len(_guards_lamp) == 3", "Each actual lamp needs three coordinates."),
                    _set("_guards_lamp_corridor", "corridors[_guards_lamp_index]"),
                    *_point("_guards_lamp", "_guards_lamp_corridor", "_guards_inside"),
                    _require(
                        "_guards_inside == true", "Each light needs its actual selected corridor."
                    ),
                    _set("_guards_lamp_index", "_guards_lamp_index + 1"),
                ],
            ),
            *_daily_history(),
            _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
            {"increment": {"field": "state.lasting_serial", "amount": 1}},
            _call(
                "format_srd_information_projection",
                {"message": "{_caster_state_id}:guards_and_wards:{state.lasting_serial}"},
                ("_info_projection_message",),
            ),
            _set("_new_guards_wards_source_id", "_info_projection_message"),
            _when(
                "state.spell_guards_wards_sources == null",
                [
                    _set("state.spell_guards_wards_sources", {}),
                    _set("state.spell_guards_wards_source_ids", []),
                ],
            ),
            _set(
                "state.spell_guards_wards_sources.{_new_guards_wards_source_id}",
                {
                    "installed": True,
                    "spell_id": "'guards_and_wards'",
                    "spell_level": "slot_level",
                    "caster_state_id": "_caster_state_id",
                    "source_caster_id": "_caster_state_id",
                    "cast_id": "spell_cast_id",
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 28800",
                    "serial": "state.lasting_serial",
                    "permanent": "_guards_daily_count >= 365",
                    "daily_count": "_guards_daily_count",
                    "daily_day": "_guards_cast_day",
                    "suppressed": False,
                    "box": "_guards_box",
                    "plane": "_guards_caster.metadata.plane",
                    "groups": dict.fromkeys(GROUPS, True),
                    "designated_creatures": "designated_creatures",
                    "password": "password",
                    "password_speakers": [],
                    "corridors": "corridors",
                    "lamps": "lamp_positions",
                    "other": "'dancing_lights'",
                    "door_ids": [],
                },
            ),
            _set("_guards_retained_ids", "state.spell_guards_wards_source_ids"),
            _push("_guards_retained_ids", "_new_guards_wards_source_id"),
            _set("state.spell_guards_wards_source_ids", "_guards_retained_ids"),
            _set("state.has_guards_wards_sources", True),
            _set("_guards_retained_histories", []),
            _each(
                "_guards_histories",
                "_guards_history",
                [
                    _when(
                        "_guards_history.source_id != _guards_history_id",
                        [_push("_guards_retained_histories", "_guards_history")],
                    ),
                ],
            ),
            _push(
                "_guards_retained_histories",
                {
                    "caster_state_id": "_caster_state_id",
                    "plane": "_guards_caster.metadata.plane",
                    "box": "_guards_box",
                    "source_id": "_new_guards_wards_source_id",
                    "days": "_guards_daily_count",
                    "last_day": "_guards_cast_day",
                },
            ),
            _set("state.guards_wards_daily_history", "_guards_retained_histories"),
            _set("_guards_parent_id", "_new_guards_wards_source_id"),
            _set("_guards_scene_entities", "state.spatial_scene.entities"),
            _set("_guards_door_ids", []),
            _each(
                "_guards_scene_entities",
                "_guards_candidate",
                [
                    _when(
                        "_guards_candidate.active != false and "
                        "_guards_candidate.metadata.plane == _guards_caster.metadata.plane and "
                        "_guards_candidate.metadata.srd_object_access != null and "
                        "_guards_candidate.metadata.srd_object_access.kind == 'door'",
                        [
                            *_point("_guards_candidate.position", "_guards_box", "_guards_inside"),
                            _when(
                                "_guards_inside == true",
                                [
                                    _push("_guards_door_ids", "_guards_candidate.id"),
                                    _call(
                                        "install_srd_guards_wards_door",
                                        {
                                            "caster_state": "{_caster_state_id}",
                                            "source_id": "{_guards_parent_id}",
                                            "object_id": "{_guards_candidate.id}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set(
                "state.spell_guards_wards_sources.{_guards_parent_id}.door_ids", "_guards_door_ids"
            ),
            _set("_spell_summary_rows", [{"label": "'Ward'", "value": "'50-foot square'"}]),
            _set(
                "_spell_detail_rows",
                [
                    {"label": "'Door locks'", "value": "len(_guards_door_ids)"},
                    {"label": "'Consecutive days'", "value": "_guards_daily_count"},
                ],
            ),
        ],
    )
    end = _tool(
        "Dispel one actual parent effect; retire the ward only after all four end.",
        [
            _parameter("source_id"),
            _parameter("group", "enum", enum_values=GROUPS + ["all"]),
            _optional("reason", default="dispelled"),
        ],
        [
            *_read(),
            _when(
                "group == 'doors' or group == 'all'",
                [
                    _set("_guards_parent_doors", "_guards_source.door_ids"),
                    _each(
                        "_guards_parent_doors",
                        "_guards_door",
                        [
                            _call(
                                "remove_srd_guards_wards_door",
                                {"source_id": "{source_id}", "object_id": "{_guards_door}"},
                            )
                        ],
                    ),
                ],
            ),
            _when(
                "group == 'all'",
                [
                    *[
                        _set(f"state.spell_guards_wards_sources.{{source_id}}.groups.{k}", False)
                        for k in GROUPS
                    ]
                ],
                [_set("state.spell_guards_wards_sources.{source_id}.groups.{group}", False)],
            ),
            _set("_guards_source", "state.spell_guards_wards_sources[source_id]"),
            _when(
                " and ".join(f"_guards_source.groups.{k} != true" for k in GROUPS),
                [
                    _set("state.spell_guards_wards_sources.{source_id}.installed", False),
                    _set(
                        "state.spell_guards_wards_sources.{source_id}.ended_tick",
                        "state.lasting_tick",
                    ),
                    _set("state.spell_guards_wards_sources.{source_id}.end_reason", "reason"),
                ],
            ),
        ],
    )
    refresh = _tool(
        "Retire exact finite wards and their actual child locks after real clock changes.",
        [],
        [
            _when(
                "state.has_guards_wards_sources == true",
                [
                    _set("_guards_source_ids", "state.spell_guards_wards_source_ids"),
                    _set("_guards_remaining_ids", []),
                    _each(
                        "_guards_source_ids",
                        "_guards_id",
                        [
                            _set("_guards_source", "state.spell_guards_wards_sources[_guards_id]"),
                            _when(
                                "_guards_source.installed == true",
                                [
                                    _when(
                                        "_guards_source.permanent != true and "
                                        "state.lasting_tick >= _guards_source.expires_tick",
                                        [
                                            _call(
                                                "end_srd_guards_wards_effect",
                                                {
                                                    "source_id": "{_guards_id}",
                                                    "group": "all",
                                                    "reason": "expired",
                                                },
                                            )
                                        ],
                                        [_push("_guards_remaining_ids", "_guards_id")],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set("state.spell_guards_wards_source_ids", "_guards_remaining_ids"),
                    _set("state.has_guards_wards_sources", "len(_guards_remaining_ids) > 0"),
                ],
            ),
        ],
    )
    return {
        "cast_srd_guards_wards": install,
        "end_srd_guards_wards_effect": end,
        "refresh_srd_guards_wards": refresh,
    }


def _query_tools():
    password = _tool(
        "Speak the actual ward password within five feet, retaining this speaker's immunity.",
        [
            _entity("actor_state"),
            _parameter("source_id"),
            _parameter("password"),
        ],
        [
            *_read(),
            _require(_active(), "The actual ward is inactive."),
            _require(
                "password != '' and password == _guards_source.password",
                "Speak the actual chosen ward password.",
            ),
            _require(
                "actor_state.is_alive == true and 'silenced' not in actor_state.conditions",
                "The living creature must actually speak aloud.",
            ),
            *_scene("_actor_state_id", "_guards_speaker"),
            _require(
                "_guards_speaker.metadata.plane == _guards_source.plane",
                "Speak near the actual ward on the same plane.",
            ),
            *[
                _set(
                    f"_guards_distance_{a}",
                    f"max(0, _guards_source.box.min[{a}] - "
                    f"_guards_speaker.position[{a}], _guards_speaker.position[{a}] - "
                    f"_guards_source.box.max[{a}])",
                )
                for a in range(3)
            ],
            _require(
                "_guards_distance_0 * _guards_distance_0 + _guards_distance_1 * "
                "_guards_distance_1 + _guards_distance_2 * _guards_distance_2 <= 25",
                "Speak the ward password within five feet of the actual area.",
            ),
            _set("_guards_speakers", "_guards_source.password_speakers"),
            _when(
                "_actor_state_id not in _guards_speakers",
                [_push("_guards_speakers", "_actor_state_id")],
            ),
            _set(
                "state.spell_guards_wards_sources.{source_id}.password_speakers", "_guards_speakers"
            ),
            _set("_guards_parent_doors", "_guards_source.door_ids"),
            _each(
                "_guards_parent_doors",
                "_guards_door",
                [
                    _call(
                        "grant_srd_guards_wards_door_exemption",
                        {
                            "actor_state": "{_actor_state_id}",
                            "source_id": "{source_id}",
                            "object_id": "{_guards_door}",
                        },
                    )
                ],
            ),
        ],
    )
    light = _tool(
        "Resolve real stationary ward lamps from the independent parent group lifetime.",
        [
            _array("point"),
            _parameter("plane"),
        ],
        [
            _set("_guards_wards_dim_light", False),
            _set("_guards_wards_light_level", -1),
            *_sources(
                [
                    _when(
                        "plane == _guards_source.plane and _guards_source.groups.other == true "
                        "and _guards_source.other == 'dancing_lights'",
                        [
                            _set("_guards_parent_lamps", "_guards_source.lamps"),
                            _each(
                                "_guards_parent_lamps",
                                "_guards_lamp",
                                [
                                    *[
                                        _set(
                                            f"_guards_lamp_d{a}", f"point[{a}] - _guards_lamp[{a}]"
                                        )
                                        for a in range(3)
                                    ],
                                    _when(
                                        "_guards_lamp_d0 * _guards_lamp_d0 + "
                                        "_guards_lamp_d1 * _guards_lamp_d1 "
                                        "+ _guards_lamp_d2 * _guards_lamp_d2 <= 100",
                                        [
                                            _set("_guards_wards_dim_light", True),
                                            _set(
                                                "_guards_wards_light_level",
                                                "max(_guards_wards_light_level, "
                                                "_guards_source.spell_level)",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    )
                ]
            ),
        ],
    )
    fog = _tool(
        "Qualify ward corridor obscuration against actual observer exemptions and source boxes.",
        [
            _entity("observer_state"),
            _array("point"),
            _parameter("plane"),
        ],
        [
            *_scene("_observer_state_id", "_guards_observer"),
            _set("_guards_wards_fog", False),
            *_sources(
                [
                    _when(
                        "plane == _guards_source.plane and _guards_source.groups.corridors == true "
                        f"and {_exempt('_observer_state_id')} != true",
                        [
                            _set("_guards_parent_corridors", "_guards_source.corridors"),
                            _each(
                                "_guards_parent_corridors",
                                "_guards_corridor",
                                [
                                    *_point("point", "_guards_corridor", "_guards_target_inside"),
                                    *_point(
                                        "_guards_observer.position",
                                        "_guards_corridor",
                                        "_guards_observer_inside",
                                    ),
                                    _when(
                                        "_guards_target_inside == true or "
                                        "_guards_observer_inside == true",
                                        [_set("_guards_wards_fog", True)],
                                    ),
                                ],
                            ),
                        ],
                    )
                ]
            ),
        ],
    )
    return {
        "speak_srd_guards_wards_password": password,
        "check_srd_guards_wards_light": light,
        "check_srd_guards_wards_fog": fog,
    }


def extend_runtime(text):
    """Emit the parent owners without changing ordinary child spell costs or lifetimes."""
    original = _load_runtime(text)
    tools = original["tools"]
    changed = {**_lock_tools(), **_cast_tools(), **_query_tools()}
    cast = deepcopy(tools["cast_spell"])
    for parameter in [
        _array("guards_wards_origin", default=[0, 0, 0]),
        _optional("guards_wards_height", "number", 20),
        _optional("guards_wards_designations", "array", [], items={"type": "string"}),
        _optional("guards_wards_password", default=""),
        _optional("guards_wards_corridors", "array", [], items={"type": "object"}),
        _optional(
            "guards_wards_lamps",
            "array",
            [],
            items={"type": "array", "items": {"type": "number"}},
            max_items=4,
        ),
    ]:
        if not any(p["name"] == parameter["name"] for p in cast["parameters"]):
            cast["parameters"].append(parameter)
    for effects in _effect_lists(cast["effects"]):
        effects[:] = [
            e
            for e in effects
            if "_spell_definition.family == 'guarded_area'" not in e.get("condition", "")
        ]
    index = max(
        i
        for i, e in enumerate(cast["effects"])
        if "_spell_definition.family ==" in e.get("condition", "")
    )
    cast["effects"].insert(
        index + 1,
        _when(
            "_spell_definition.family == 'guarded_area'",
            [
                _call(
                    "cast_srd_guards_wards",
                    {
                        "caster_state": "{_caster_state_id}",
                        "slot_level": "{_slot_level}",
                        "origin": "{guards_wards_origin}",
                        "height": "{guards_wards_height}",
                        "designated_creatures": "{guards_wards_designations}",
                        "password": "{guards_wards_password}",
                        "corridors": "{guards_wards_corridors}",
                        "lamp_positions": "{guards_wards_lamps}",
                        "caster_can_touch_origin": "{caster_can_touch_target}",
                        "spell_cast_id": "{_spell_source_cast_id}",
                    },
                    ("_spell_summary_rows", "_spell_detail_rows"),
                ),
            ],
        ),
    )
    changed["cast_spell"] = cast
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    clock["effects"] = [e for e in clock["effects"] if "refresh_srd_guards_wards" not in str(e)]
    clock["effects"].append(
        _when("state.has_guards_wards_sources == true", [_call("refresh_srd_guards_wards", {})])
    )
    changed["advance_srd_lasting_clock"] = clock
    text = _replace_tools(text, changed)
    # New tools' empty-procedure separator is normalized once, matching later replacement.
    text = _replace_tools(text, {k: v for k, v in changed.items() if k not in tools})
    props = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in PROFILES["guards_and_wards"].items()
        if k != "family"
    }
    schema = yaml.safe_dump(
        {
            "guarded_area": {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            }
        },
        sort_keys=False,
    )
    schema = "".join("      " + line for line in schema.splitlines(True))
    if "      guarded_area:" in text:
        text = re.sub(
            r"(?ms)^      guarded_area:\n.*?(?=^      [a-z])",
            lambda _: schema + "\n",
            text,
            count=1,
        )
    else:
        first = next(iter(original["definition_row_schemas"]["definitions.spells"]["variants"]))
        anchor = f"      {first}:"
        if text.count(anchor) != 1:
            raise ValueError("Guards and Wards needs the actual unique spell variant anchor.")
        text = text.replace(anchor, schema + "\n" + anchor, 1)
    return text
