"""Six actual choices consume the original Storm third-start event exactly once."""

from copy import deepcopy

if __package__:
    from . import srd_storm_later_turns as later
    from . import srd_storm_objects as objects
    from .srd_retained_spell_attacks import _lists
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
    from .srd_timed_senses import _loop, _public, _push, _replace_tools
else:
    import srd_storm_later_turns as later
    import srd_storm_objects as objects
    from srd_retained_spell_attacks import _lists
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
    from srd_timed_senses import _loop, _public, _push, _replace_tools


CHOOSE = "choose_srd_storm_lightning_targets"
TARGET = "resolve_srd_storm_lightning_creature"
PUBLIC_TOOLS = {CHOOSE}


def _creature_target(tools):
    """Preserve the native save/Evasion/card owner and genuine pre-half packet debt."""
    tool = deepcopy(tools["resolve_spell_area_save_damage_target"])
    tool["parameters"].append(_parameter("remaining_penalty", "integer", minimum=0))
    save_calls = []
    damage_calls = []
    for sequence in _lists(tool["effects"]):
        for effect in sequence:
            if effect.get("call") == "resolve_srd_save_damage":
                save_calls.append(effect)
                effect.setdefault("export_context", {})["_storm_evasion"] = "_evasion_available"
            if effect.get("call") == "apply_srd_qualifying_damage":
                damage_calls.append((sequence, effect))
    if len(save_calls) != 1 or len(damage_calls) != 1:
        raise ValueError("Storm Lightning needs the actual scalar area save/packet boundary.")
    sequence, call = damage_calls[0]
    call["params"].update(
        game_damage_roll=True,
        unallocated_roll_penalty="{remaining_penalty}",
        damage_roll_divisor="{_storm_lightning_divisor}",
        damage_roll_base_remainder="{_storm_lightning_remainder}",
    )
    index = sequence.index(call)
    sequence[index : index + 1] = [
        _set("_storm_lightning_divisor", 1),
        _when(
            "(_spell_save_outcome == 'success' and _storm_evasion != true) or "
            "(_spell_save_outcome == 'failure' and _storm_evasion == true)",
            [_set("_storm_lightning_divisor", 2)],
        ),
        _set("_storm_lightning_remainder", "damage_total % _storm_lightning_divisor"),
        _set("_affliction_trigger_damage", 0),
        _when(
            "_spell_save_outcome != 'success' or _storm_evasion != true",
            [call],
        ),
    ]
    tool["effects"].append(_set("_storm_lightning_target_damage", "_affliction_trigger_damage"))
    tool["description"] = (
        "Resolve this earned Storm Lightning recipient's native Dex save and one typed HP packet."
    )
    return tool


def _find_stage():
    return [
        _require(
            "combat.encounter_active == true and combat.turn_active == true and "
            "combat.active_actor == _caster_state_id and "
            "caster_state.is_alive == true and caster_state.hp > 0 and "
            "caster_state.turns_started != null",
            "Choose Storm Lightning during its creating caster's actual current turn.",
        ),
        _set("_storm_choice_record", None),
        _set("_storm_choice_rows", "state.turn_effects"),
        _loop(
            "_storm_choice_rows",
            "_storm_choice_row",
            [
                _when(
                    "_storm_choice_row.kind == 'storm_of_vengeance' and "
                    "_storm_choice_row.caster == _caster_state_id and "
                    "_storm_choice_row.source_id == caster_state.storm_of_vengeance_source_id and "
                    "_storm_choice_row.storm_ordinal == 3 and "
                    "_storm_choice_row.later_effect_pending == true and "
                    "_storm_choice_row.last_caster_start == caster_state.turns_started",
                    [
                        _require(
                            "_storm_choice_record == null",
                            "The original Storm Lightning start must have one actual source row.",
                        ),
                        _set("_storm_choice_record", "_storm_choice_row"),
                    ],
                ),
            ],
        ),
        _require(
            "_storm_choice_record != null",
            "No original pending third-start Storm Lightning event remains.",
        ),
        _call(
            "check_srd_storm_of_vengeance_record",
            {"caster_state": "{_caster_state_id}", "record": "{_storm_choice_record}"},
            ("_storm_live", "_storm_active"),
        ),
        _require(
            "_storm_live == true and _storm_active == true",
            "The actual original Storm source must remain available for its Lightning choice.",
        ),
    ]


def _selection():
    """Validate every real choice before rolling, claiming or damaging any recipient."""
    return [
        _require("len(targets) == 6", "Storm calls six bolts at six different actual targets."),
        _call(
            "collect_srd_storm_creature_targets",
            {"record": "{_storm_choice_record}"},
            ("_storm_targets",),
        ),
        _call(
            objects.COLLECT,
            {"record": "{_storm_choice_record}", "selected_targets": "{targets}"},
            ("_storm_object_candidates",),
        ),
        _set("_storm_chosen_creatures", []),
        _set("_storm_chosen_objects", []),
        _set("_storm_chosen_seen", []),
        _loop(
            "targets",
            "_storm_choice_target",
            [
                _require(
                    "_storm_choice_target not in _storm_chosen_seen",
                    "Each Storm Lightning bolt requires a distinct actual target.",
                ),
                _push("_storm_chosen_seen", "_storm_choice_target"),
                _require(
                    "_storm_choice_target in _storm_targets or "
                    "_storm_choice_target in _storm_object_candidates",
                    "Every chosen Lightning target must actually be beneath this original cloud.",
                ),
                _when(
                    "_storm_choice_target in _storm_targets",
                    [_push("_storm_chosen_creatures", "_storm_choice_target")],
                )
                | {"on_false": [_push("_storm_chosen_objects", "_storm_choice_target")]},
            ],
        ),
        _call(
            "collect_srd_storm_cover_targets",
            {
                "source_state": "{_caster_state_id}",
                "origin": "{_storm_choice_record.origin}",
                "area_plane": "{_storm_choice_record.plane}",
                "targets": "{_storm_chosen_creatures}",
            },
            ("_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters"),
        ),
        _require(
            "len(_srd_area_targets) == len(_storm_chosen_creatures)",
            "A chosen Lightning creature behind actual Total Cover is not a supported target.",
        ),
        {
            "call": objects.COVER,
            "params": {
                "source_state": "{_caster_state_id}",
                "origin": "{_storm_choice_record.origin}",
                "area_plane": "{_storm_choice_record.plane}",
                "targets": "{_storm_chosen_objects}",
            },
            "export_context": {"_storm_lightning_objects": "_srd_area_targets"},
        },
        _require(
            "len(_storm_lightning_objects) == len(_storm_chosen_objects)",
            "A chosen Lightning object behind actual Total Cover is not a supported target.",
        ),
    ]


def _resolve():
    return [
        {"dice": {"expression": "10d6"}, "store_as": "_storm_lightning_roll"},
        _call(
            "modify_srd_actor_damage_roll",
            {
                "actor_state": "{_caster_state_id}",
                "amount": "{_storm_lightning_roll.total}",
                "lasting_state": "{_lasting_state_id}",
            },
            ("_actor_modified_damage", "_actor_unallocated_roll_penalty"),
        ),
        _set(
            "_storm_lightning_receipt",
            {
                "ordinal": 3,
                "caster_start": "caster_state.turns_started",
                "damage_type": "'lightning'",
                "raw_roll": "_storm_lightning_roll",
                "damage_total": "_actor_modified_damage",
                "remaining_penalty": "_actor_unallocated_roll_penalty",
                "targets": "targets",
                "creature_targets": "_storm_chosen_creatures",
                "object_targets": "_storm_chosen_objects",
            },
        ),
        _call(
            later.CLAIM,
            {"record": "{_storm_choice_record}", "stage_result": "{_storm_lightning_receipt}"},
        ),
        _set("_storm_lightning_summary", []),
        _set("_storm_lightning_damage", 0),
        _loop(
            "_storm_chosen_creatures",
            "_storm_lightning_creature",
            [
                _call(
                    TARGET,
                    {
                        "spell": "storm_of_vengeance",
                        "target": "{_storm_lightning_creature}",
                        "save_ability": "dexterity",
                        "spell_save_dc": "{_storm_choice_record.save_dc}",
                        "damage_total": "{_actor_modified_damage}",
                        "damage_type": "lightning",
                        "success_damage": "half",
                        "construct_save_disadvantage": False,
                        "half_cover_targets": "{_srd_area_half_cover}",
                        "three_quarters_cover_targets": "{_srd_area_three_quarters}",
                        "source_state_id": "{_storm_choice_record.caster}",
                        "spell_cast_id": "{_storm_choice_record.cast_id}",
                        "actual_spell_level": "{_storm_choice_record.slot_level}",
                        "spell_cast_owner": "{_storm_choice_record.caster}",
                        "remaining_penalty": "{_actor_unallocated_roll_penalty}",
                    },
                    ("_spell_summary_rows", "_storm_lightning_target_damage"),
                ),
                _loop(
                    "_spell_summary_rows",
                    "_storm_lightning_row",
                    [_push("_storm_lightning_summary", "_storm_lightning_row")],
                ),
                _set(
                    "_storm_lightning_damage",
                    "_storm_lightning_damage + _storm_lightning_target_damage",
                ),
            ],
        ),
        _loop(
            "_storm_chosen_objects",
            "_storm_lightning_object",
            [
                _call(
                    objects.PACKET,
                    {
                        "object_id": "{_storm_lightning_object}",
                        "amount": "{_actor_modified_damage}",
                        "damage_type": "lightning",
                    },
                    ("_storm_object_summary", "_object_damage_applied"),
                ),
                _push("_storm_lightning_summary", "_storm_object_summary"),
                _set(
                    "_storm_lightning_damage",
                    "_storm_lightning_damage + _object_damage_applied",
                ),
            ],
        ),
        _set("_storm_lightning_source_id", "_storm_choice_record.source_id"),
    ]


def _choice_tool():
    tool = _tool(
        "Choose six distinct actual Lightning targets for the original earned third Storm start.",
        [
            _entity("caster_state"),
            _parameter(
                "targets",
                "array",
                items={"type": "string"},
                min_items=6,
                max_items=6,
                unique_items=True,
            ),
        ],
        _find_stage() + _selection() + _resolve(),
    )
    tool = _public(
        tool,
        "combat",
        "Storm of Vengeance: Lightning",
        [
            ("Source", "_storm_lightning_source_id"),
            ("Targets", "_storm_chosen_seen"),
            ("Lightning dice", "_storm_lightning_roll.results"),
            ("Actual damage", "_storm_lightning_damage"),
        ],
    )
    tool["metadata"]["result_card"]["details"] = {"path": "context._storm_lightning_summary"}
    return tool


def bind_runtime_consumers(text):
    """Bind after final area/packet and Storm object owners, before ordinary pending fences."""
    tools = _load_runtime(text)["tools"]
    if objects.PACKET not in tools or later.CLAIM not in tools:
        raise ValueError(
            "Storm Lightning needs its genuine earned-stage and prepared-object owners."
        )
    changed = {TARGET: _creature_target(tools), CHOOSE: _choice_tool()}
    return _replace_tools(
        text, {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    )
