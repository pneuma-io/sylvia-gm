"""Courier pace reads retained movement sources at the actual interval boundary."""

from copy import deepcopy

if __package__:
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
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
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
    from srd_timed_senses import _loop, _push, _replace_tools


PACE_FIELDS = {
    "spell_haste": "_courier_source_haste",
    "spell_slow": "_courier_source_slow",
    "spell_freedom_of_movement": "_courier_source_freedom",
    "spell_speed_reduction": "_courier_source_reduction",
    "turn_speed_halved": "_courier_source_halved",
    "haste_lethargy": "_courier_source_lethargy",
}
PACE_EXPORTS = (*PACE_FIELDS.values(), "_courier_source_bonus", "_courier_source_next_tick")
SPEED_KINDS = ("speed_bonus", "speed_reduction", "speed_zero", "speed_halved")


def _membership(name, values):
    return "(" + " or ".join(f"{name} == '{value}'" for value in values) + ")"


def _qualifier(tools):
    """Reuse exact concentration qualification, replacing only its clock read."""
    tool = deepcopy(tools["check_srd_actor_spell_modifier_active"])
    tool["parameters"].append(_parameter("at_tick", "number"))

    def clock(value):
        if isinstance(value, str):
            return value.replace("lasting_state.lasting_tick", "at_tick")
        if isinstance(value, list):
            return [clock(item) for item in value]
        if isinstance(value, dict):
            return {key: clock(item) for key, item in value.items()}
        return value

    tool["effects"] = clock(tool["effects"])
    tool["effects"].append(
        _set("_actor_modifier_active", "_actor_modifier_active == true and started_tick <= at_tick")
    )
    return tool


def _source_tool():
    """Read only genuine retained records; forecast no missing turn or equipment history."""
    boundary = [
        *[
            _when(
                f"_courier_source_record.{field} != null and "
                f"_courier_source_record.{field} > at_tick",
                [
                    _set(
                        "_courier_source_next_tick",
                        f"min(_courier_source_next_tick, _courier_source_record.{field})",
                    )
                ],
            )
            for field in ("started_tick", "expires_tick")
        ],
    ]
    qualify = _call(
        "qualify_srd_courier_actor_modifier_at_tick",
        {
            "target_state": "{_target_state_id}",
            "source_state": "{_courier_source_record.caster}",
            "lasting_state": "{_lasting_state_id}",
            "spell": "{_courier_source_record.spell}",
            "started_tick": "{_courier_source_record.started_tick}",
            "expires_tick": "{_courier_source_record.expires_tick}",
            "concentration": "{_courier_source_record.concentration}",
            "at_tick": "{at_tick}",
        },
        ["_actor_modifier_active"],
    )
    return _tool(
        "Read actual retained speed sources and their next boundary without changing the clock.",
        [
            _entity("target_state"),
            _parameter("at_tick", "number"),
            _parameter("interval_end", "number"),
        ],
        [
            _require(
                "at_tick >= 0 and interval_end >= at_tick", "Select an ordered pace interval."
            ),
            *[
                _set(name, False)
                for name in PACE_FIELDS.values()
                if name != "_courier_source_reduction"
            ],
            _set("_courier_source_reduction", 0),
            _set("_courier_source_bonus", 0),
            _set("_courier_source_next_tick", "interval_end"),
            _set("_courier_source_records", []),
            _when(
                "state.turn_effects != null",
                [_set("_courier_source_records", "state.turn_effects")],
            ),
            _loop(
                "_courier_source_records",
                "_courier_source_record",
                [
                    _when("_courier_source_record.target == _target_state_id", boundary),
                    _when(
                        "_courier_source_record.target == _target_state_id and "
                        + _membership("_courier_source_record.kind", SPEED_KINDS),
                        [
                            _when(
                                "(_courier_source_record.started_tick == null or "
                                "_courier_source_record.started_tick <= at_tick) and "
                                "(_courier_source_record.expires_tick == null or "
                                "at_tick + 0.000000001 < _courier_source_record.expires_tick)",
                                [
                                    _when(
                                        "_courier_source_record.kind == 'speed_bonus'",
                                        [
                                            _set(
                                                "_courier_source_bonus",
                                                "max(_courier_source_bonus, "
                                                "_courier_source_record.magnitude)",
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_courier_source_record.kind == 'speed_reduction'",
                                        [
                                            _set(
                                                "_courier_source_reduction",
                                                "max("
                                                "_courier_source_reduction, "
                                                "_courier_source_record.magnitude)",
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_courier_source_record.kind == 'speed_zero'",
                                        [
                                            _set("_courier_source_reduction", 1000000),
                                        ],
                                    ),
                                    _when(
                                        "_courier_source_record.kind == 'speed_halved'",
                                        [
                                            _set("_courier_source_halved", True),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_courier_source_record.target == _target_state_id and "
                        "_courier_source_record.kind == 'actor_spell_modifier' and "
                        + _membership("_courier_source_record.mode", ("haste", "slow", "freedom")),
                        [
                            qualify,
                            _when(
                                "_actor_modifier_active == true",
                                [
                                    *[
                                        _when(
                                            f"_courier_source_record.mode == '{mode}'",
                                            [_set(PACE_FIELDS[field], True)],
                                        )
                                        for mode, field in (
                                            ("haste", "spell_haste"),
                                            ("slow", "spell_slow"),
                                            ("freedom", "spell_freedom_of_movement"),
                                        )
                                    ],
                                ],
                            ),
                            _when(
                                "_courier_source_record.mode == 'haste' and "
                                "_courier_source_record.expires_tick > 0 and "
                                "at_tick >= _courier_source_record.expires_tick and "
                                "at_tick < _courier_source_record.expires_tick + 2",
                                [
                                    _set(
                                        "_courier_haste_before_end",
                                        "_courier_source_record.expires_tick - 0.00001",
                                    ),
                                    {
                                        **deepcopy(qualify),
                                        "params": {
                                            **qualify["params"],
                                            "at_tick": "{_courier_haste_before_end}",
                                        },
                                    },
                                    _when(
                                        "_actor_modifier_active == true",
                                        [
                                            _set("_courier_source_lethargy", True),
                                            _set(
                                                "_courier_source_next_tick",
                                                "min("
                                                "_courier_source_next_tick, "
                                                "_courier_source_record.expires_tick + 2)",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "target_state.haste_lethargy == true and "
                "target_state.haste_lethargy_until_tick != null and "
                "at_tick < target_state.haste_lethargy_until_tick",
                [
                    _set("_courier_source_lethargy", True),
                    _set(
                        "_courier_source_next_tick",
                        "min(_courier_source_next_tick, target_state.haste_lethargy_until_tick)",
                    ),
                ],
            ),
        ],
    )


def _bind_fields(value):
    if isinstance(value, str):
        for field, local in PACE_FIELDS.items():
            value = value.replace("target_state." + field, local)
        return value
    if isinstance(value, list):
        return [_bind_fields(item) for item in value]
    if isinstance(value, dict):
        return {key: _bind_fields(item) for key, item in value.items()}
    return value


def bind_runtime_consumers(text):
    """Bind pure modifiers after body projection and retain sufficient boundary iterations."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "qualify_srd_courier_actor_modifier_at_tick": _qualifier(tools),
        "project_srd_courier_modifiers_at_tick": _source_tool(),
    }
    pace = deepcopy(tools["project_srd_animal_messenger_pace"])
    pace["effects"] = _bind_fields(
        [
            effect
            for effect in pace["effects"]
            if effect.get("call") != "project_srd_courier_modifiers_at_tick"
            and effect.get("condition") != "_courier_source_next_tick != null"
        ]
    )
    body_index = next(
        i
        for i, effect in enumerate(pace["effects"])
        if effect.get("call") == "project_srd_body_movement_at_tick"
    )
    pace["effects"][body_index + 1 : body_index + 1] = [
        _call(
            "project_srd_courier_modifiers_at_tick",
            {
                "target_state": "{_target_state_id}",
                "at_tick": "{at_tick}",
                "interval_end": "{interval_end}",
            },
            PACE_EXPORTS,
        ),
        _when(
            "_courier_source_next_tick != null",
            [
                _set("_courier_walk_without_projected_bonus", "_body_walk_speed"),
                _when(
                    "target_state.spell_speed_bonus != null and _body_cloud != true",
                    [
                        _set(
                            "_courier_walk_without_projected_bonus",
                            "max(0, _body_walk_speed - target_state.spell_speed_bonus)",
                        ),
                    ],
                ),
                _when(
                    "_body_cloud != true",
                    [
                        _set(
                            "_body_walk_speed",
                            "_courier_walk_without_projected_bonus + _courier_source_bonus",
                        ),
                    ],
                ),
                _set("_body_next_tick", "min(_body_next_tick, _courier_source_next_tick)"),
            ],
        ),
    ]
    changed["project_srd_animal_messenger_pace"] = pace

    journey = deepcopy(tools["progress_srd_animal_messenger_journey"])
    boundary_condition = "_courier_source_boundary_record.target == _target_state_id"
    boundaries = _loop(
        "_courier_all_records",
        "_courier_source_boundary_record",
        [
            _when(
                boundary_condition,
                [
                    *[
                        _when(
                            f"_courier_source_boundary_record.{field} != null and "
                            f"_courier_source_boundary_record.{field} > _courier_cursor and "
                            f"_courier_source_boundary_record.{field} < interval_end",
                            [
                                _push(
                                    "_courier_segment_records",
                                    f"_courier_source_boundary_record.{field}",
                                ),
                            ],
                        )
                        for field in ("started_tick", "expires_tick")
                    ],
                    _when(
                        "_courier_source_boundary_record.kind == 'actor_spell_modifier' and "
                        "_courier_source_boundary_record.mode == 'haste' and "
                        "_courier_source_boundary_record.expires_tick + 2 > _courier_cursor and "
                        "_courier_source_boundary_record.expires_tick + 2 < interval_end",
                        [
                            _push(
                                "_courier_segment_records",
                                "_courier_source_boundary_record.expires_tick + 2",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    def insert(effects):
        effects[:] = [
            e
            for e in effects
            if e.get("for_each", {}).get("as") != "_courier_source_boundary_record"
        ]
        for index, effect in enumerate(effects):
            if effect.get("set", {}).get("field") == "_courier_extra_segment":
                effects.insert(index, boundaries)
                break
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    insert(effect[branch])

    insert(journey["effects"])
    changed["progress_srd_animal_messenger_journey"] = journey
    return _replace_tools(text, changed)
