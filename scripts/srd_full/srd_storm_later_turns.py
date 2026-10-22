"""Actual Storm caster starts resolve their no-save creature damage exactly once."""

from copy import deepcopy

if __package__:
    from . import srd_storm_of_vengeance as storm
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
    import srd_storm_of_vengeance as storm
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


START = "resolve_srd_storm_later_caster_start"
TARGET = "resolve_srd_storm_no_save_creature"
CLAIM = "claim_srd_storm_later_stage"
PUBLIC_TOOLS = set()


def _target_tool():
    """Delegate one authentic adjusted game roll to the sole typed HP/TempHP owner."""
    return _tool(
        "Resolve this actual Storm no-save creature recipient without a synthetic D20.",
        [
            _entity("target"),
            _parameter("record", "object", from_state="state.turn_effects"),
            _parameter("amount", "integer", minimum=0),
            _parameter("remaining_penalty", "integer", minimum=0),
            _parameter("damage_type", "enum", enum_values=["acid", "bludgeoning", "cold"]),
        ],
        [
            _set("_storm_target_hp_before", "target.hp"),
            _set("_storm_target_temp_before", "target.temp_hp"),
            _call(
                "apply_srd_qualifying_damage",
                {
                    "target": "{_target_id}",
                    "amount": "{amount}",
                    "adjustment": 0,
                    "damage_type": "{damage_type}",
                    "source_state_id": "{record.caster}",
                    "game_damage_roll": True,
                    "unallocated_roll_penalty": "{remaining_penalty}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_effective_damage",),
            ),
            _set("_storm_target_hp_after", "target.hp"),
            _set("_storm_target_temp_after", "target.temp_hp"),
            _call(
                "record_srd_spell_target_result",
                {
                    "target": "{_target_id}",
                    "brief": "{_effective_damage} {damage_type} damage; no save",
                    "detail": "{_effective_damage} {damage_type} damage; no save. "
                    "HP {_storm_target_hp_before} -> {_storm_target_hp_after}; "
                    "Temp HP {_storm_target_temp_before} -> {_storm_target_temp_after}.",
                },
                ("_spell_target_detail",),
            ),
            _set("_storm_later_target_summary", "_spell_target_detail"),
        ],
    )


def _claim_tool():
    """Claim before damage so source-ending callbacks cannot resurrect an old cloud row."""
    return _tool(
        "Store one genuine Storm stage receipt in its existing source before damage.",
        [
            _parameter("record", "object", from_state="state.turn_effects"),
            _parameter("stage_result", "object", from_state="state.turn_effects"),
        ],
        [
            _set("_storm_claim_rows", "state.turn_effects"),
            _set("_storm_claim_kept", []),
            _set("_storm_claimed", False),
            _loop(
                "_storm_claim_rows",
                "_storm_claim_row",
                [
                    _when(
                        "_storm_claim_row.kind == 'storm_of_vengeance' and "
                        "_storm_claim_row.source_id == record.source_id",
                        [
                            _require(
                                "_storm_claim_row.cast_id == record.cast_id and "
                                "_storm_claim_row.last_caster_start == "
                                "record.last_caster_start and "
                                "_storm_claim_row.storm_ordinal == record.storm_ordinal and "
                                "_storm_claim_row.later_effect_pending == true",
                                "Only the actual current Storm start may claim its stage.",
                            ),
                            _set(
                                "_storm_claim_change",
                                {
                                    "later_effect_pending": False,
                                    "last_resolved_ordinal": "record.storm_ordinal",
                                    "storm_last_stage_result": "stage_result",
                                },
                            ),
                            _set(
                                "_storm_claim_row",
                                "merge(_storm_claim_row, _storm_claim_change)",
                            ),
                            _set("_storm_claimed", True),
                        ],
                    ),
                    _push("_storm_claim_kept", "_storm_claim_row"),
                ],
            ),
            _require("_storm_claimed == true", "The actual Storm stage source must remain saved."),
            _set("state.turn_effects", "_storm_claim_kept"),
        ],
    )


def _stage_effects():
    """The raw roll and its one actor penalty are shared by all current recipients."""
    return [
        _set("_storm_later_dice_count", 1),
        _set("_storm_later_damage_type", "'cold'"),
        _when(
            "_storm_later_row.storm_ordinal == 2",
            [
                _set("_storm_later_dice_count", 4),
                _set("_storm_later_damage_type", "'acid'"),
            ],
        ),
        _when(
            "_storm_later_row.storm_ordinal == 4",
            [
                _set("_storm_later_dice_count", 2),
                _set("_storm_later_damage_type", "'bludgeoning'"),
            ],
        ),
        _set("_storm_later_stage_result", {"ordinal": "_storm_later_row.storm_ordinal"}),
        _when(
            "_storm_active == true",
            [
                _call(
                    "collect_srd_storm_creature_targets",
                    {"record": "{_storm_later_row}"},
                    ("_storm_targets",),
                ),
                _call(
                    "collect_srd_storm_cover_targets",
                    {
                        "source_state": "{_caster_state_id}",
                        "origin": "{_storm_later_row.origin}",
                        "area_plane": "{_storm_later_row.plane}",
                        "targets": "{_storm_targets}",
                    },
                    ("_srd_area_targets",),
                ),
                {
                    "dice": {"expression": "{_storm_later_dice_count}d6"},
                    "store_as": "_storm_later_damage_roll",
                },
                _call(
                    "modify_srd_actor_damage_roll",
                    {
                        "actor_state": "{_caster_state_id}",
                        "amount": "{_storm_later_damage_roll.total}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                    ("_actor_modified_damage", "_actor_unallocated_roll_penalty"),
                ),
                _set(
                    "_storm_later_stage_result",
                    {
                        "ordinal": "_storm_later_row.storm_ordinal",
                        "caster_start": "boundary_turn",
                        "damage_type": "_storm_later_damage_type",
                        "raw_roll": "_storm_later_damage_roll",
                        "damage_total": "_actor_modified_damage",
                        "remaining_penalty": "_actor_unallocated_roll_penalty",
                        "targets": "_srd_area_targets",
                    },
                ),
            ],
        ),
        _call(
            CLAIM,
            {
                "record": "{_storm_later_row}",
                "stage_result": "{_storm_later_stage_result}",
            },
        ),
        _when(
            "_storm_active == true",
            [
                _loop(
                    "_srd_area_targets",
                    "_storm_later_target",
                    [
                        _call(
                            TARGET,
                            {
                                "target": "{_storm_later_target}",
                                "record": "{_storm_later_row}",
                                "amount": "{_actor_modified_damage}",
                                "remaining_penalty": "{_actor_unallocated_roll_penalty}",
                                "damage_type": "{_storm_later_damage_type}",
                            },
                            ("_storm_later_target_summary", "_effective_damage"),
                        ),
                        _push("_storm_later_summary", "_storm_later_target_summary"),
                        _set(
                            "_storm_later_damage_applied",
                            "_storm_later_damage_applied + _effective_damage",
                        ),
                    ],
                ),
            ],
        ),
    ]


def _start_tool():
    """Only the actual start owner creates an earned stage, never an elapsed round."""
    return _tool(
        "Resolve earned Storm stages2/4/5-10 on the actual creating caster's start.",
        [_entity("caster_state"), _parameter("boundary_turn", "integer", minimum=0)],
        [
            _require(
                "caster_state.turns_started != null and "
                "caster_state.turns_started == boundary_turn",
                "Storm damage requires the creating caster's actual started turn.",
            ),
            _set("_storm_later_summary", []),
            _set("_storm_later_damage_applied", 0),
            _set("_storm_later_rows", "state.turn_effects"),
            _loop(
                "_storm_later_rows",
                "_storm_later_row",
                [
                    _when(
                        "_storm_later_row.kind == 'storm_of_vengeance' and "
                        "_storm_later_row.caster == _caster_state_id and "
                        "_storm_later_row.last_caster_start == boundary_turn and "
                        "_storm_later_row.later_effect_pending == true and "
                        "(_storm_later_row.storm_ordinal == 2 or "
                        "_storm_later_row.storm_ordinal == 4 or "
                        "(_storm_later_row.storm_ordinal >= 5 and "
                        "_storm_later_row.storm_ordinal <= 10))",
                        [
                            _call(
                                "check_srd_storm_of_vengeance_record",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "record": "{_storm_later_row}",
                                },
                                ("_storm_live", "_storm_active"),
                            ),
                            _when("_storm_live == true", _stage_effects()),
                        ],
                    ),
                ],
            ),
        ],
    )


def bind_runtime_consumers(text):
    """Append only inside Storm's owning start; ordinary saves pay no new guard."""
    tools = _load_runtime(text)["tools"]
    if storm.START not in tools:
        raise ValueError("Storm later turns need the authentic paid appearance/source owner.")
    penalty = str(tools["modify_srd_actor_damage_roll"]["effects"])
    if "_actor_unallocated_roll_penalty" not in penalty:
        raise ValueError("Storm shared rolls need the genuine clamped raw-penalty remainder.")
    changed = {TARGET: _target_tool(), CLAIM: _claim_tool(), START: _start_tool()}
    start = deepcopy(tools[storm.START])
    if START not in str(start["effects"]):
        start["effects"].append(
            _call(
                START,
                {
                    "caster_state": "{_caster_state_id}",
                    "boundary_turn": "{boundary_turn}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_storm_later_summary", "_storm_later_damage_applied"),
            )
        )
    changed[storm.START] = start
    process = deepcopy(tools["process_srd_turn_effects"])
    branches = [
        e
        for e in process["effects"]
        if e.get("condition", "").startswith("lasting_state.has_storm_sources == true and")
        and storm.START in str(e)
    ]
    if len(branches) != 1:
        raise ValueError("Storm later cards need the actual source-owned caster-start branch.")
    branch = branches[0]
    if "_storm_later_summary" not in str(branch):
        call = next(e for e in branch["on_true"] if e.get("call") == storm.START)
        call["export_context"] = {
            "_storm_later_summary": "_storm_later_summary",
            "_storm_later_damage_applied": "_storm_later_damage_applied",
        }
        branch["on_true"] += [
            _loop(
                "_storm_later_summary",
                "_storm_turn_row",
                [_push("_turn_effect_rows", "_storm_turn_row")],
            ),
            _set(
                "_turn_effect_damage_applied",
                "_turn_effect_damage_applied + _storm_later_damage_applied",
            ),
        ]
        # The existing turn-card accumulators must exist before this source adds its rows.
        process["effects"].remove(branch)
        initialized = [
            i
            for i, e in enumerate(process["effects"])
            if e.get("set", {}).get("field") in {"_turn_effect_rows", "_turn_effect_damage_applied"}
            and e["set"]["value"] in (0, [])
        ]
        if len(initialized) != 2:
            raise ValueError("Storm later cards need the native turn-effect accumulators.")
        process["effects"].insert(max(initialized) + 1, branch)
    changed["process_srd_turn_effects"] = process
    return _replace_tools(text, {n: t for n, t in changed.items() if tools.get(n) != t})
