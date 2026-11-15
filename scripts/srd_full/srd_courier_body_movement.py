"""Courier intervals read source-owned body speeds without advancing or rewinding time."""

import re
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

BODY_FIELDS = {
    "speed": "_body_walk_speed",
    "flying_speed": "_body_fly_speed",
    "swim_speed": "_body_swim_speed",
    "climb_speed": "_body_climb_speed",
    "can_hover": "_body_can_hover",
}
BODY_EXPORTS = (
    *BODY_FIELDS.values(),
    "_body_cloud",
    "_body_conditions",
    "_body_source_id",
    "_body_source_serial",
    "_body_next_tick",
)


def _source_tool():
    """Mirror actual transformation lifetime clauses, settling Wind mode only in context."""
    return _tool(
        "Qualify an exact saved transformation at a requested tick without changing its source.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("source_id"),
            _parameter("at_tick", "number"),
            _parameter("interval_end", "number"),
        ],
        [
            _set("_body_record", "state.transformation_sources[source_id]"),
            _set("_body_record_next_tick", "interval_end"),
            _set("_body_record_mode", "_body_record.mode"),
            _set(
                "_body_record_active",
                "_body_record.installed == true and "
                "_body_record.target_state_id == _target_state_id and "
                "_body_record.caster_state_id == _caster_state_id",
            ),
            _set(
                "_body_concentration_matches",
                "caster_state.is_alive == true and "
                "caster_state.concentration_spell_id == _body_record.spell_id and "
                "caster_state.concentration_effect_family == 'transformation_spell' and "
                "caster_state.concentration_started_tick == _body_record.started_tick",
            ),
            _set(
                "_body_effectively_permanent",
                "_body_record.permanent == true or "
                "(_body_record.spell_id == 'true_polymorph' and "
                "_body_concentration_matches == true and at_tick >= _body_record.expires_tick)",
            ),
            _when(
                "_body_effectively_permanent != true",
                [
                    _set(
                        "_body_record_active",
                        "_body_record_active == true and "
                        "at_tick + 0.000000001 < _body_record.expires_tick",
                    ),
                ],
            ),
            _when(
                "_body_record.concentration == true and _body_effectively_permanent != true",
                [
                    _set(
                        "_body_record_active",
                        "_body_record_active == true and _body_concentration_matches == true",
                    ),
                ],
            ),
            _when(
                "(_body_record.spell_id == 'polymorph' and target_state.temp_hp == 0) or "
                "(_body_record.spell_id == 'true_polymorph' and target_state.is_alive != true) or "
                "(_body_record.spell_id == 'gaseous_form' and target_state.hp == 0)",
                [_set("_body_record_active", False)],
            ),
            _when(
                "_body_record_active == true",
                [
                    _when(
                        "at_tick + 0.000000001 < _body_record.started_tick",
                        [
                            _set(
                                "_body_record_next_tick",
                                "min(interval_end, _body_record.started_tick)",
                            ),
                            _set("_body_record_active", False),
                        ],
                    ),
                    _when(
                        "_body_record.expires_tick != null and "
                        "at_tick + 0.000000001 < _body_record.expires_tick",
                        [
                            _set(
                                "_body_record_next_tick",
                                "min(_body_record_next_tick, _body_record.expires_tick)",
                            ),
                        ],
                    ),
                    _when(
                        "_body_record.spell_id == 'wind_walk' and "
                        "(_body_record.mode == 'to_normal' or _body_record.mode == 'to_cloud')",
                        [
                            {
                                "condition": "at_tick + 0.000000001 < _body_record.transition_tick",
                                "on_true": [
                                    _set(
                                        "_body_record_next_tick",
                                        "min(_body_record_next_tick, _body_record.transition_tick)",
                                    ),
                                ],
                                "on_false": [
                                    _set("_body_record_mode", "'normal'"),
                                    _when(
                                        "_body_record.mode == 'to_cloud'",
                                        [_set("_body_record_mode", "'cloud'")],
                                    ),
                                ],
                            },
                        ],
                    ),
                ],
            ),
        ],
    )


def _body_tool():
    """Select newest genuine active form and keep native restoration evidence unchanged."""
    native = []
    for field, local in BODY_FIELDS.items():
        native.extend(
            [
                _set(local, False if field == "can_hover" else 0),
                {
                    "condition": "target_state.form_base_statistics != null",
                    "on_true": [
                        _when(
                            f"target_state.form_base_statistics.{field} != null",
                            [_set(local, f"target_state.form_base_statistics.{field}")],
                        ),
                    ],
                    "on_false": [
                        _when(
                            f"target_state.{field} != null", [_set(local, f"target_state.{field}")]
                        ),
                    ],
                },
            ],
        )
    beast = [
        _call(
            "lookup_srd_transformation_form",
            {"form_id": "{_body_winner.form_id}"},
            ["_transformation_form"],
        ),
        _set("_body_walk_speed", "_transformation_form.walk_speed"),
    ]
    for mode, local in (
        ("fly", "_body_fly_speed"),
        ("swim", "_body_swim_speed"),
        ("climb", "_body_climb_speed"),
    ):
        beast.extend(
            [
                _set(local, 0),
                _when(
                    f"'{mode}' in _transformation_form.other_speeds",
                    [_set(local, f"_transformation_form.other_speeds.{mode}")],
                ),
            ],
        )
    return _tool(
        "Read actual native/form body movement and the next saved lifetime boundary, purely.",
        [
            _entity("target_state"),
            _parameter("at_tick", "number"),
            _parameter("interval_end", "number"),
        ],
        [
            _require(
                "at_tick >= 0 and interval_end >= at_tick",
                "Select an ordered nonnegative movement interval.",
            ),
            *native,
            _set("_body_cloud", False),
            _set("_body_wind_transitioning", False),
            _set("_body_source_id", None),
            _set("_body_source_serial", 0),
            _set("_body_winner", None),
            _set("_body_winner_mode", "''"),
            _set("_body_next_tick", "interval_end"),
            _when(
                "target_state.has_transformations == true and "
                "target_state.transformation_source_ids != null and "
                "state.transformation_sources != null",
                [
                    _set("_body_source_ids", "target_state.transformation_source_ids"),
                    _loop(
                        "_body_source_ids",
                        "_body_candidate_id",
                        [
                            _when(
                                "_body_candidate_id in state.transformation_sources",
                                [
                                    _set(
                                        "_body_candidate",
                                        "state.transformation_sources[_body_candidate_id]",
                                    ),
                                    _when(
                                        "_body_candidate.installed == true and "
                                        "_body_candidate.target_state_id == _target_state_id",
                                        [
                                            _call(
                                                "qualify_srd_body_movement_source_at_tick",
                                                {
                                                    "target_state": "{_target_state_id}",
                                                    "caster_state": (
                                                        "{_body_candidate.caster_state_id}"
                                                    ),
                                                    "source_id": "{_body_candidate_id}",
                                                    "at_tick": "{at_tick}",
                                                    "interval_end": "{interval_end}",
                                                },
                                                [
                                                    "_body_record_active",
                                                    "_body_record_mode",
                                                    "_body_record_next_tick",
                                                ],
                                            ),
                                            _set(
                                                "_body_next_tick",
                                                "min(_body_next_tick, _body_record_next_tick)",
                                            ),
                                            _when(
                                                "_body_record_active == true and "
                                                "(_body_winner == null or "
                                                "_body_candidate.serial > _body_source_serial)",
                                                [
                                                    _set("_body_winner", "_body_candidate"),
                                                    _set("_body_source_id", "_body_candidate_id"),
                                                    _set(
                                                        "_body_source_serial",
                                                        "_body_candidate.serial",
                                                    ),
                                                    _set("_body_winner_mode", "_body_record_mode"),
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
            _when(
                "_body_winner != null",
                [
                    _when("_body_winner.profile == 'beast'", beast),
                    _when(
                        "_body_winner.profile == 'mist' or (_body_winner.profile == 'wind' and "
                        "(_body_winner_mode == 'cloud' or _body_winner_mode == 'to_normal'))",
                        [
                            _set("_body_cloud", True),
                            _set("_body_walk_speed", 0),
                            _set("_body_swim_speed", 0),
                            _set("_body_climb_speed", 0),
                            _set("_body_fly_speed", 10),
                            _set("_body_can_hover", True),
                            _when("_body_winner.profile == 'wind'", [_set("_body_fly_speed", 300)]),
                        ],
                    ),
                    _set(
                        "_body_wind_transitioning",
                        "_body_winner.profile == 'wind' and "
                        "(_body_winner_mode == 'to_normal' or _body_winner_mode == 'to_cloud')",
                    ),
                ],
            ),
            _when(
                "target_state.form_base_statistics != null and _body_cloud != true and "
                "target_state.spell_speed_bonus != null",
                [_set("_body_walk_speed", "_body_walk_speed + target_state.spell_speed_bonus")],
            ),
            _when(
                "_body_winner != null and _body_winner.profile == 'alter' and "
                "_body_winner_mode == 'aquatic'",
                [_set("_body_swim_speed", "_body_walk_speed")],
            ),
            _set("_body_conditions", []),
            _set("_body_current_conditions", "target_state.conditions"),
            _loop(
                "_body_current_conditions",
                "_body_condition",
                [
                    _when(
                        "_body_condition != 'stunned' or "
                        "target_state.transformation_stunned != true or "
                        "target_state.timed_condition_unowned_stunned == true or "
                        "target_state.timed_condition_stunned == true or "
                        "target_state.turn_effect_stunned == true",
                        [_push("_body_conditions", "_body_condition")],
                    )
                ],
            ),
            _when(
                "_body_wind_transitioning == true and 'stunned' not in _body_conditions",
                [_push("_body_conditions", "'stunned'")],
            ),
        ],
    )


def _map_body(value):
    if isinstance(value, str):
        for field, local in BODY_FIELDS.items():
            value = value.replace("target_state." + field, local)
        value = re.sub(r"(?<![\w.])mode\b", "_courier_mode", value)
        return value.replace("target_state.form_cloud", "_body_cloud").replace(
            "target_state.conditions", "_body_conditions"
        )
    if isinstance(value, list):
        return [_map_body(child) for child in value]
    if isinstance(value, dict):
        return {key: _map_body(child) for key, child in value.items()}
    return value


def bind_runtime_consumers(text):
    """Bind body projections to existing pace and bound travel by actual saved form events."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "qualify_srd_body_movement_source_at_tick": _source_tool(),
        "project_srd_body_movement_at_tick": _body_tool(),
    }
    pace = deepcopy(tools["project_srd_animal_messenger_pace"])
    pace["effects"] = _map_body(
        [e for e in pace["effects"] if e.get("call") != "project_srd_body_movement_at_tick"]
    )
    for effect in pace["effects"]:
        if effect.get("set", {}).get("field") == "_courier_next_pace_tick":
            effect["set"]["value"] = "_body_next_tick"
    pace["effects"].insert(
        0,
        _call(
            "project_srd_body_movement_at_tick",
            {
                "target_state": "{_target_state_id}",
                "at_tick": "{at_tick}",
                "interval_end": "{interval_end}",
            },
            BODY_EXPORTS,
        ),
    )
    changed["project_srd_animal_messenger_pace"] = pace
    journey = deepcopy(tools["progress_srd_animal_messenger_journey"])
    form_boundaries = _when(
        "target_state.has_transformations == true and "
        "target_state.transformation_source_ids != null and "
        "lasting_state.transformation_sources != null",
        [
            _set("_courier_form_ids", "target_state.transformation_source_ids"),
            _loop(
                "_courier_form_ids",
                "_courier_form_id",
                [
                    _when(
                        "_courier_form_id in lasting_state.transformation_sources",
                        [
                            _set(
                                "_courier_form_record",
                                "lasting_state.transformation_sources[_courier_form_id]",
                            ),
                            _when(
                                "_courier_form_record.installed == true and "
                                "_courier_form_record.target_state_id == _target_state_id",
                                [
                                    *[
                                        _when(
                                            f"_courier_form_record.{field} != null and "
                                            f"_courier_form_record.{field} > _courier_cursor and "
                                            f"_courier_form_record.{field} < interval_end",
                                            [
                                                _push(
                                                    "_courier_segment_records",
                                                    f"_courier_form_record.{field}",
                                                )
                                            ],
                                        )
                                        for field in (
                                            "started_tick",
                                            "expires_tick",
                                            "transition_tick",
                                        )
                                    ]
                                ],
                            ),
                        ],
                    )
                ],
            ),
        ],
    )

    def insert(effects):
        effects[:] = [e for e in effects if e.get("condition") != form_boundaries["condition"]]
        for index, effect in enumerate(effects):
            if effect.get("set", {}).get("field") == "_courier_extra_segment":
                effects.insert(index, form_boundaries)
                return
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    insert(effect[branch])

    insert(journey["effects"])
    changed["progress_srd_animal_messenger_journey"] = journey
    return _replace_tools(text, changed)
