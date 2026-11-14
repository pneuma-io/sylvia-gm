"""Incendiary Cloud retains one actual Sphere and moves it on its caster's chosen turn start."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array
    from .srd_spell_environment import (
        PROFILES,
        _call,
        _cloudkill_tools,
        _effect_lists,
        _optional,
        _require,
        _set,
        _when,
    )
else:
    from srd_scene_relations import _array
    from srd_spell_environment import (
        PROFILES,
        _call,
        _cloudkill_tools,
        _effect_lists,
        _optional,
        _require,
        _set,
        _when,
    )

INCENDIARY_PROFILE = PROFILES["incendiary_cloud"]
INCENDIARY_SHA = "f0c5ed44c20899ddf3733e5401ae86d2a7e92a80b91f8131b6d3364ded7b1854"
INCENDIARY_CONTINUATION_SHA = "dba4cdf16b912f655bd62da857b6d2033969bb1abe9aa0e604f3c60e69ec286d"


def tools():
    """Reuse the sole actual Sphere movement owner, changing only source and chosen direction."""
    move = deepcopy(_cloudkill_tools()["move_srd_cloudkill_source"])
    move["description"] = (
        "Move the genuine Incendiary Cloud ten feet away in the actual chosen direction."
    )
    move["parameters"].append(_array("direction"))
    move["parameters"][-1]["items"].update(minimum=-1, maximum=1)
    for effects in _effect_lists(move["effects"]):
        for effect in effects:
            condition = effect.get("require", {}).get("condition")
            if isinstance(condition, str) and "_cloud_source.spell == 'cloudkill'" in condition:
                effect["require"]["condition"] = condition.replace(
                    "'cloudkill'", "'incendiary_cloud'"
                )
                effect["require"]["condition"] += (
                    f" and _cloud_source.source_sha256 == {INCENDIARY_SHA!r}"
                )
                effect["require"]["error"] = "Use this actual caster’s installed Incendiary Cloud."
        first = next(
            (i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_cloud_away"),
            None,
        )
        if first is None:
            continue
        last = next(
            i
            for i, e in enumerate(effects)
            if e.get("set", {}).get("field") == "_cloud_destination"
        )
        effects[first:last] = [
            _set("_cloud_away", "direction"),
            _set(
                "_cloud_away_length",
                "(direction[0] ** 2 + direction[1] ** 2 + direction[2] ** 2) ** 0.5",
            ),
            _require(
                "_cloud_away_length > 0.000000001", "Choose a genuine nonzero cloud direction."
            ),
            _require(
                "(direction[0] * (_cloud_old_origin[0] - _cloud_caster_origin[0]) + "
                "direction[1] * (_cloud_old_origin[1] - _cloud_caster_origin[1]) + "
                "direction[2] * (_cloud_old_origin[2] - _cloud_caster_origin[2])) >= 0",
                "The selected cloud motion must move away from its actual caster.",
            ),
        ]
    for effects in list(_effect_lists(move["effects"])):
        for effect in list(effects):
            if effect.get("set", {}).get("field") == "_cloud_caster_origin":
                index = effects.index(effect)
                effects[index + 1 : index + 1] = [
                    _require(
                        "_info_scene_entity.metadata.plane == _cloud_source.plane or "
                        "(_info_scene_entity.metadata.plane == null and "
                        "_cloud_source.plane == 'material')",
                        "Cloud direction needs the caster and cloud on the same actual plane.",
                    )
                ]
            requirement = effect.get("require", {})
            if "larger actual map" not in requirement.get("error", ""):
                continue
            index = effects.index(effect)
            effects[index:index] = [_set("_inc_bounds", "state.spatial_scene.map.bounds")]
            requirement["condition"] = " and ".join(
                f"_cloud_destination[{axis}] - 20 >= _inc_bounds.min[{axis}] and "
                f"_cloud_destination[{axis}] + 20 <= _inc_bounds.max[{axis}]"
                for axis in range(3)
            )
    return {"move_srd_incendiary_cloud_source": move}


def _bind_shared_appearance(event):
    """The actual initial appearance shares one native roll; later triggers keep their own roll."""
    for effects in list(_effect_lists(event["effects"])):
        first = next(
            (i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_sphere_dice"),
            None,
        )
        if first is None:
            continue
        last = next(
            i
            for i, e in enumerate(effects)
            if e.get("set", {}).get("field") == "_sphere_roll_divisor"
        )
        native = deepcopy(effects[first:last])
        effects[first:last] = [
            _when(
                "_sphere_event_source.spell == 'incendiary_cloud' and "
                "_sphere_event_source.appearance_in_progress == true and "
                "_sphere_event_source.appearance_tick == state.lasting_tick and "
                "_sphere_event_source.appearance_sequence == _sphere_event_source.sequence and "
                "_target_state_id in _sphere_event_source.appearance_targets",
                [
                    _set("_sphere_damage_roll", "_sphere_event_source.initial_damage_roll"),
                    _set("_sphere_amount", "_sphere_event_source.initial_damage_amount"),
                    _set(
                        "_sphere_unallocated_penalty",
                        "_sphere_event_source.initial_unallocated_penalty",
                    ),
                ],
                native,
            )
        ]


def bind_sources(changed):
    """Bind actual installation, initial packet and the mandatory chosen caster-start callback."""
    cast = changed["cast_spell_environment"]
    for effects in _effect_lists(cast["effects"]):
        for i, effect in list(enumerate(effects)):
            if effect.get("call") != "resolve_srd_environment_sphere_scene":
                continue
            effects[i:i] = [
                _when(
                    "_environment_profile.environment_mode == 'incendiary_cloud'",
                    [
                        _set("_inc_origin", "_environment_cast_origin"),
                        _set("_inc_bounds", "state.spatial_scene.map.bounds"),
                        _require(
                            " and ".join(
                                f"_inc_origin[{i}] - 20 >= _inc_bounds.min[{i}] and "
                                f"_inc_origin[{i}] + 20 <= _inc_bounds.max[{i}]"
                                for i in range(3)
                            ),
                            "The whole actual twenty-foot Sphere must fit the admitted map.",
                        ),
                        _set(
                            "state.persistent_spell_areas.{_environment_cast_id}.source_sha256",
                            repr(INCENDIARY_SHA),
                        ),
                        _set(
                            "state.persistent_spell_areas.{_environment_cast_id}.continuation_sha256",
                            repr(INCENDIARY_CONTINUATION_SHA),
                        ),
                        _set(
                            "state.persistent_spell_areas.{_environment_cast_id}.cloud_last_start_turn",
                            0,
                        ),
                        _when(
                            "caster_state.turns_started != null",
                            [
                                _set(
                                    "state.persistent_spell_areas.{_environment_cast_id}.cloud_last_start_turn",
                                    "caster_state.turns_started",
                                ),
                            ],
                        ),
                        {"dice": {"expression": "10d8"}, "store_as": "_incendiary_initial_roll"},
                        _call(
                            "resolve_srd_actor_spell_modifiers",
                            {"target_state": "{_caster_state_id}", "lasting_state": "state"},
                        ),
                        _call(
                            "modify_srd_actor_raw_damage_roll",
                            {
                                "actor_state": "{_caster_state_id}",
                                "lasting_state": "state",
                                "amount": "{_incendiary_initial_roll.total}",
                            },
                            {"_incendiary_initial_amount": "_ray_raw_damage"},
                        ),
                        _set(
                            "state.persistent_spell_areas.{_environment_cast_id}.initial_damage_roll",
                            "_incendiary_initial_roll",
                        ),
                        _set(
                            "state.persistent_spell_areas.{_environment_cast_id}.initial_damage_amount",
                            "max(0, _incendiary_initial_amount)",
                        ),
                        _set(
                            "state.persistent_spell_areas.{_environment_cast_id}.initial_unallocated_penalty",
                            "max(0, -_incendiary_initial_amount)",
                        ),
                    ],
                )
            ]
    _bind_shared_appearance(changed["resolve_srd_environment_sphere_event"])
    start = changed["resolve_srd_environment_hazard_start_turn"]
    start["parameters"] = [p for p in start["parameters"] if p["name"] != "incendiary_direction"]
    start["parameters"].append(
        _optional(
            "incendiary_direction",
            "array",
            None,
            min_items=3,
            max_items=3,
            items={"type": "number", "minimum": -1, "maximum": 1},
        )
    )
    loop = next(e["for_each"] for e in start["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'incendiary_cloud' and _environment_area.caster == "
            "_target_state_id and combat.turn_active == true "
            "and combat.active_actor == _target_state_id "
            "and target_state.turns_started > _environment_area.cloud_last_start_turn",
            [
                _require(
                    "incendiary_direction != null",
                    "Choose this actual cloud's direction at the caster's turn start.",
                ),
                _call(
                    "move_srd_incendiary_cloud_source",
                    {
                        "caster_state": "{_target_state_id}",
                        "area_id": "{_environment_id}",
                        "direction": "{incendiary_direction}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                ),
            ],
        )
    )
    begin = changed["begin_combat_turn"]
    begin["parameters"] = [
        p for p in begin["parameters"] if p["name"] != "environment_incendiary_direction"
    ]
    begin["parameters"].append(
        _optional(
            "environment_incendiary_direction",
            "array",
            None,
            min_items=3,
            max_items=3,
            items={"type": "number", "minimum": -1, "maximum": 1},
        )
    )
    for effects in _effect_lists(begin["effects"]):
        for effect in effects:
            if effect.get("call") == "resolve_srd_environment_hazard_start_turn":
                effect["params"]["incendiary_direction"] = "{environment_incendiary_direction}"
