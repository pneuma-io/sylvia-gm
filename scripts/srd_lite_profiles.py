"""Variant-only adapters composed into the accepted SRD engine owners."""

from copy import deepcopy

from .srd_control_behaviors import _each, _entity, _lasting, _param, _push
from .srd_timed_actor_defenses import _call, _require, _set, _tool, _when

FULL_ID = "sylvia.srd-lite.bg3"
TRUE_STRIKE = {
    "family": "true_strike",
    "casting_time": "action",
    "base_level": 0,
    "range_kind": "distance",
    "range_feet": 60,
    "concentration": True,
}
CLOUD = {
    "family": "spell_environment",
    "casting_time": "action",
    "base_level": 2,
    "range_kind": "distance",
    "range_feet": 60,
    "radius_feet": 7,
    "radius_per_slot_level": 0,
    "duration_rounds": 10,
    "concentration": True,
    "environment_mode": "lite_daggers",
    "damage_dice": 4,
    "damage_die": "d4",
    "damage_per_slot_level": 2,
    "damage_type": "slashing",
}


def lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if isinstance(effect.get(key), list):
                yield from lists(effect[key])
        if "for_each" in effect:
            yield from lists(effect["for_each"]["do"])


def _end(caster="caster_state"):
    return _call(
        "end_srd_concentration_node",
        {
            "source_state": "{_" + caster + "_id}",
            "target_state": "{" + caster + ".concentration_target_state_id}",
            "lasting_state": "{_lasting_state_id}",
        },
    )


def true_strike_tools():
    """Store one caster-owned target; shared attack and concentration consumers settle it."""
    install = _tool(
        "Concentrate on one target for the caster's next attack against it.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("concentration_save_modifier", "integer"),
        ],
        [
            _require(
                "target_state.is_alive == true and target_state.hp > 0",
                "True Strike needs an actual living target.",
            ),
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ("_scene_distance", "_scene_same_plane", "_scene_line_of_sight"),
            ),
            _require(
                "_scene_distance <= 60 and _scene_same_plane == true and "
                "_scene_line_of_sight == true",
                "True Strike needs a target within sixty feet on the same clear scene plane.",
            ),
            _when("caster_state.concentration_spell_id != null", [_end()]),
            _when("lasting_state.lasting_tick == null", [_set("lasting_state.lasting_tick", 0)]),
            _set("_lite_cast_turn", 0),
            _when(
                "caster_state.turns_started != null",
                [_set("_lite_cast_turn", "caster_state.turns_started")],
            ),
            _set(
                "caster_state.lite_true_strike",
                {
                    "target": "_target_state_id",
                    "expires_after_turn": "_lite_cast_turn + 1",
                    "expires_tick": "lasting_state.lasting_tick + 4",
                },
            ),
            _set("caster_state.concentration_spell_id", "'true_strike'"),
            _set("caster_state.concentration_effect_family", "'lite_true_strike'"),
            _set("caster_state.concentration_target_state_id", "_caster_state_id"),
            _set("caster_state.concentration_save_modifier", "concentration_save_modifier"),
            _set("caster_state.concentration_started_tick", "lasting_state.lasting_tick"),
            _set("caster_state.concentration_expires_tick", "lasting_state.lasting_tick + 4"),
            _when(
                "lasting_state.lite_true_strike_casters == null",
                [_set("lasting_state.lite_true_strike_casters", [])],
            ),
            _set("_lite_casters", "lasting_state.lite_true_strike_casters"),
            _when(
                "_caster_state_id not in _lite_casters",
                [_push("_lite_casters", "_caster_state_id")],
            ),
            _set("lasting_state.lite_true_strike_casters", "_lite_casters"),
            _call(
                "record_srd_spell_area_target_result",
                {
                    "target": "{_target_state_id}",
                    "brief": "Caster gains Advantage on the next attack against this target.",
                    ("detail"): (
                        "The first attack consumes True Strike on hit or miss; the caster n"
                        "ext-turn end expires it."
                    ),
                },
                ("_spell_summary_rows", "_spell_detail_rows"),
            ),
        ],
    )
    consume = _tool(
        "Consume the matching caster/target condition through the actual shared attack owner.",
        [_entity("actor_state"), _entity("target_state"), _lasting()],
        [
            _set("_lite_true_strike_advantage", False),
            _when(
                "actor_state.lite_true_strike != null and "
                "actor_state.concentration_spell_id == 'true_strike' and "
                "actor_state.concentration_effect_family == 'lite_true_strike'",
                [
                    _when(
                        "actor_state.lite_true_strike.target == _target_state_id and "
                        "(combat.encounter_active == true or lasting_state.lasting_tick < "
                        "actor_state.lite_true_strike.expires_tick)",
                        [
                            _set("_lite_true_strike_advantage", True),
                            _end("actor_state"),
                        ],
                    ),
                ],
            ),
        ],
    )
    expire = _tool(
        "Expire True Strike at the end of the caster's next turn, or elapsed free time.",
        [
            _entity("caster_state"),
            _lasting(),
            _param("boundary_actor", required=False, default=""),
            _param("boundary_phase", required=False, default="time"),
            _param("boundary_turn", "integer", required=False, default=0),
        ],
        [
            _set("_lite_boundary_actor", None),
            _when(
                "boundary_phase == 'end'",
                [
                    {
                        "call": "format_srd_information_projection",
                        "params": {"message": "{boundary_actor}"},
                        "export_context": {"_lite_boundary_actor": "_info_projection_message"},
                    }
                ],
            ),
            _when(
                "caster_state.lite_true_strike != null and "
                "caster_state.concentration_effect_family == 'lite_true_strike'",
                [
                    _when(
                        "(_lite_boundary_actor == _caster_state_id and boundary_phase == 'end' and "
                        "boundary_turn >= caster_state.lite_true_strike.expires_after_turn) or "
                        "(combat.encounter_active != true and lasting_state.lasting_tick >= "
                        "caster_state.lite_true_strike.expires_tick)",
                        [_end()],
                    ),
                ],
            ),
        ],
    )
    return {
        "cast_srd_lite_true_strike": install,
        "consume_srd_lite_true_strike": consume,
        "expire_srd_lite_true_strike": expire,
    }


def bless_target_tool():
    """Use admitted attitude and geometry before the retained Bless source is installed."""
    return _tool(
        "Validate one non-hostile Bless target in the actual scene.",
        [_entity("caster_state"), _entity("target_state"), _lasting()],
        [
            _require(
                "target_state.is_alive == true and "
                "(target_state.creature_kind == 'character' or "
                "target_state.creature_kind == 'monster')",
                "Bless needs a living creature.",
            ),
            _require(
                "_target_state_id == _caster_state_id or "
                "(target_state.attitudes != null and "
                "_caster_state_id in target_state.attitudes and "
                "(target_state.attitudes[_caster_state_id] == 'friendly' or "
                "target_state.attitudes[_caster_state_id] == 'indifferent'))",
                "Bless needs the target's saved non-hostile attitude toward the caster.",
            ),
            _call(
                "resolve_srd_scene_geometry_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ("_scene_distance", "_scene_same_plane", "_scene_line_of_sight"),
            ),
            _require(
                "_scene_distance <= 30 and _scene_same_plane == true and "
                "_scene_line_of_sight == true",
                "Bless requires each actual target within thirty feet with a clear path.",
            ),
        ],
    )


def bind_profiles(runtime, *, reuse_original_true_strike=False, reuse_original_bless=False):
    """Change only the task-private variant; retained full-SRD bytes are never an output."""
    tools = runtime["tools"]
    if reuse_original_true_strike:
        tools["cast_srd_true_strike"]["effects"].insert(0, _require(
            "target.is_alive == true and (target.creature_kind == 'character' or target.hp > 0)",
            "True Strike needs a living creature; a zero-HP monster is defeated.",
        ))
    if not reuse_original_true_strike:
        tools.update(true_strike_tools())
    if not reuse_original_bless:
        tools["validate_srd_lite_bless_target"] = bless_target_tool()
    tools["register_srd_lite_concentration"] = _tool(
        "Track the selected retained concentration owners for clock-driven removal.",
        [_entity("caster_state"), _lasting()],
        [
            _when(
                (
                    "caster_state.concentration_spell_id == 'bless' or caster_state.concent"
                    "ration_spell_id == 'cloud_of_daggers'"
                ),
                [
                    _when(
                        "lasting_state.lite_concentration_casters == null",
                        [_set("lasting_state.lite_concentration_casters", [])],
                    ),
                    _set("_lite_concentration_casters", "lasting_state.lite_concentration_casters"),
                    _when(
                        "_caster_state_id not in _lite_concentration_casters",
                        [_push("_lite_concentration_casters", "_caster_state_id")],
                    ),
                    _set("lasting_state.lite_concentration_casters", "_lite_concentration_casters"),
                ],
            )
        ],
    )
    tools["expire_srd_lite_concentration"] = _tool(
        "End expired selected sources through the genuine shared concentration owner.",
        [_entity("caster_state"), _lasting()],
        [
            _when(
                (
                    "(caster_state.concentration_spell_id == 'bless' or caster_state.concen"
                    "tration_spell_id == 'cloud_of_daggers') and lasting_state.lasting_tick"
                    " >= caster_state.concentration_expires_tick"
                ),
                [_end()],
            )
        ],
    )
    if not reuse_original_true_strike:
        spells = runtime["definitions"]["spells"]
        spells["true_strike"] = deepcopy(TRUE_STRIKE)
        schema = runtime["definition_row_schemas"]["definitions.spells"]["variants"]["true_strike"]
        for key, value in TRUE_STRIKE.items():
            if key != "family":
                schema["properties"][key]["enum"] = [value]
        runtime["definitions"]["spell_casting"]["true_strike"].update(
            components=["somatic"],
            material_parts=[],
            material="",
            material_consumed=False,
            material_has_cost=False,
            focus_substitution=False,
            range="60 feet",
            duration="Concentration, up to 2 turns including the casting turn",
        )
    if not reuse_original_bless:
        for name in ("cast_spell", "begin_srd_spell_action"):
            tool = tools[name]
            tool["effects"].insert(
                0,
                _when(
                    "spell == 'bless'",
                    [
                        _require(
                            "targets != null and len(targets) > 0", "Bless needs selected targets."
                        ),
                        _each(
                            "targets",
                            "_lite_bless_target",
                            [
                                _call(
                                    "validate_srd_lite_bless_target",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "target_state": "{_lite_bless_target}",
                                        "lasting_state": "{_lasting_state_id}",
                                    },
                                )
                            ],
                        ),
                    ],
                ),
            )
    if not reuse_original_true_strike:
        branches = [
            e
            for effects in lists(tools["cast_spell"]["effects"])
            for e in effects
            if "_spell_definition.family == 'true_strike'" in e.get("condition", "")
        ]
        if len(branches) != 1:
            raise ValueError("Expected the one accepted True Strike family branch")
        branches[0]["on_true"] = [
            _call(
                "cast_srd_lite_true_strike",
                {
                    "caster_state": "{_caster_state_id}",
                    "target_state": "{_target_id}",
                    "lasting_state": "{_lasting_state_id}",
                    "concentration_save_modifier": "{_casting_profile.concentration_save_modifier}",
                },
                ("_spell_summary_rows", "_spell_detail_rows"),
            )
        ]
    tools["cast_spell"]["effects"].append(
        _call(
            "register_srd_lite_concentration",
            {"caster_state": "{_caster_state_id}", "lasting_state": "{_lasting_state_id}"},
        )
    )
    if not reuse_original_true_strike:
        attack = tools["resolve_srd_attack_conditions"]
        attack["effects"].append(
            _call(
                "consume_srd_lite_true_strike",
                {
                    "actor_state": "{_actor_state_id}",
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_lite_true_strike_advantage",),
            )
        )
        attack["effects"].append(
            _set(
                "_condition_advantage",
                "_condition_advantage == true or _lite_true_strike_advantage == true",
            )
        )
        tools["end_srd_concentration_node"]["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'lite_true_strike'",
                [
                    _set("source_state.lite_true_strike", None),
                    _set("_lite_casters_kept", []),
                    _when(
                        "lasting_state.lite_true_strike_casters != null",
                        [
                            _set("_lite_true_strike_tracked", "lasting_state.lite_true_strike_casters"),
                            _each(
                                "_lite_true_strike_tracked",
                                "_lite_caster_kept",
                                [
                                    _when(
                                        "_lite_caster_kept != _source_state_id",
                                        [_push("_lite_casters_kept", "_lite_caster_kept")],
                                    )
                                ],
                            )
                        ],
                    ),
                    _set("lasting_state.lite_true_strike_casters", "_lite_casters_kept"),
                ],
            ),
        )
    tools["end_srd_concentration_node"]["effects"].insert(
        0 if reuse_original_true_strike else 1,
        _when(
            (
                "source_state.concentration_spell_id == 'bless' or source_state.concentrati"
                "on_spell_id == 'cloud_of_daggers'"
            ),
            [
                _set("_lite_concentration_kept", []),
                _when(
                    "lasting_state.lite_concentration_casters != null",
                    [
                        _set("_lite_concentration_tracked", "lasting_state.lite_concentration_casters"),
                        _each(
                            "_lite_concentration_tracked",
                            "_lite_source_kept",
                            [
                                _when(
                                    "_lite_source_kept != _source_state_id",
                                    [_push("_lite_concentration_kept", "_lite_source_kept")],
                                )
                            ],
                        )
                    ],
                ),
                _set("lasting_state.lite_concentration_casters", "_lite_concentration_kept"),
            ],
        ),
    )
    if not reuse_original_true_strike:
        process = tools["process_srd_turn_effects"]
        process["effects"].insert(
            0,
            _when(
                "lasting_state.lite_true_strike_casters != null",
                [
                    _set("_lite_true_strike_casters", "lasting_state.lite_true_strike_casters"),
                    _each(
                        "_lite_true_strike_casters",
                        "_lite_caster",
                        [
                            _call(
                                "expire_srd_lite_true_strike",
                                {
                                    "caster_state": "{_lite_caster}",
                                    "lasting_state": "{_lasting_state_id}",
                                    "boundary_actor": "{boundary_actor}",
                                    "boundary_phase": "{boundary_phase}",
                                    "boundary_turn": "{boundary_turn}",
                                },
                            )
                        ],
                    ),
                ],
            ),
        )
    process = tools["process_srd_turn_effects"]
    process["effects"].insert(
        0 if reuse_original_true_strike else 1,
        _when(
            "lasting_state.lite_concentration_casters != null",
            [
                _set("_lite_concentration_sources", "lasting_state.lite_concentration_casters"),
                _each(
                    "_lite_concentration_sources",
                    "_lite_concentration_source",
                    [
                        _call(
                            "expire_srd_lite_concentration",
                            {
                                "caster_state": "{_lite_concentration_source}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                        )
                    ],
                ),
            ],
        ),
    )
    return runtime
