"""Printed Giant Insect native riders use existing attacks and next-turn effect owners."""

import re
from copy import deepcopy

if __package__:
    from .srd_divination_spells import _normalize
    from .srd_spell_summoning import (
        _choose,
        _created_actor_attack_geometry_tool,
        _giant_insect_source_profile,
        _walk,
    )
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
    from .srd_timed_senses import _public, _replace_tools
else:
    from srd_divination_spells import _normalize
    from srd_spell_summoning import (
        _choose,
        _created_actor_attack_geometry_tool,
        _giant_insect_source_profile,
        _walk,
    )
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
    from srd_timed_senses import _public, _replace_tools

PUBLIC_TOOLS = {"use_srd_insect_venomous_spew"}
NATIVE = "actor.created_spell_id == 'giant_insect' and actor.form_attack_in_progress != true"


def _forward(parameters):
    return {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") in {"entity", "participant"} else p["name"])
        + "}"
        for p in parameters
    }


def _native_effect(tools, source):
    """Keep the existing source/target/turn record with native next-turn expiry."""
    tool = deepcopy(tools["install_srd_turn_effect"])
    keep = {"caster_state", "target_state", "lasting_state", "kind"}
    original = tool["parameters"]
    tool["parameters"] = [p for p in original if p["name"] in keep]
    kind = next(p for p in tool["parameters"] if p["name"] == "kind")
    kind["enum_values"] = ["speed_zero", "poisoned"]
    constants = {
        "spell": "''",
        "effect_label": "'Web Bolt'",
        "expiry_owner": "'caster'",
        "expiry_phase": "'start'",
        "magnitude": 0,
        "dice_count": 0,
        "damage_die": "'d4'",
        "damage_type": "'poison'",
        "spell_cast_id": "''",
        "actual_spell_level": -1,
        "_spell_cast_owner_id": None,
    }
    pushes = [
        e["list_push"]
        for e in tool["effects"]
        if e.get("list_push", {}).get("list") == "_turn_effects"
    ]
    if len(pushes) != 1 or pushes[0]["value"]["expires_tick"] != "lasting_state.lasting_tick + 2":
        raise ValueError("Preserve the actual next-turn record and its ordinary projection owner.")
    pushes[0]["value"].update(
        expires_tick=None,
        source_caster_id=None,
        source_is_magical=False,
        native_body_source="_caster_state_id",
        native_individual="caster_state.created_individual_id",
        native_body_block_sha256=repr(source["source_stat_block_sha256"]),
        native_attack="effect_label",
        native_due_round="combat.round + 1",
        native_order="combat.order",
        native_order_index="combat.turn_index",
        native_free_time_deadline=None,
    )
    local_names = {
        name: name if name.startswith("_") else "_native_insect_" + name for name in constants
    }
    pattern = re.compile(
        r"(?<![.\w])(" + "|".join(sorted(local_names, key=len, reverse=True)) + r")(?!\w)"
    )

    def localize(value):
        if isinstance(value, str):
            return pattern.sub(lambda match: local_names[match.group()], value)
        if isinstance(value, list):
            return [localize(child) for child in value]
        if isinstance(value, dict):
            return {key: localize(child) for key, child in value.items()}
        return value

    tool["effects"] = localize(tool["effects"])
    tool["effects"][:0] = [
        _require(
            "combat.encounter_active == true and combat.group_order == true and "
            "combat.turn_active == true and combat.active_actor == _caster_state_id and "
            "combat.current_actor == _caster_state_id and "
            "combat.order[combat.turn_index].entity_id == _caster_state_id",
            "Retain the native rider's actual complete group Initiative slot.",
        ),
        _require(
            "lasting_state.time_stop_source == null or "
            "lasting_state.time_stop_source.active != true",
            "Native Insect rider chronology during Time Stop remains unsupported.",
        ),
        _require(
            "combat.lasting_epoch_tick != null and combat.lasting_epoch_round != null and "
            "abs(lasting_state.lasting_tick - (combat.lasting_epoch_tick + "
            "2 * (combat.round - combat.lasting_epoch_round) + "
            "2 * combat.turn_index * (len(combat.order) ** -1))) < 0.000000001",
            "Install the native rider only on its actual synchronized group clock.",
        ),
        _require(
            "caster_state.created_spell_id == 'giant_insect' and "
            "caster_state.created_individual_id == _caster_state_id and "
            "caster_state.created_source_block_sha256 == '"
            + source["source_stat_block_sha256"]
            + "' and ((kind == 'speed_zero' and "
            "caster_state.created_insect_form == 'spider') or (kind == 'poisoned' and "
            "caster_state.created_insect_form == 'centipede'))",
            "Use only this actual printed insect body's native rider.",
        ),
        *[_set(local_names[name], value) for name, value in constants.items()],
        _when(
            "kind == 'poisoned'",
            [_set(local_names["effect_label"], "'Venomous Spew'")],
        ),
        _set("lasting_state.has_native_insect_riders", True),
    ]
    tool["description"] = (
        "Install only this source insect's native rider until its next turn starts."
    )
    return tool


def _chronology_hooks(tools, source):
    """Retire only genuine native riders at a skipped slot or its translated free-time deadline."""
    name = "resolve_srd_native_insect_rider_deadline"
    if name in tools:
        return {}
    helper = _tool(
        "Check this installed native rider against its retained body and actual "
        "Initiative chronology.",
        [
            _entity("actor_state"),
            _parameter("record", "object", from_state="state.turn_effects"),
            _parameter("lasting_state", resolve="entity", required=False, default="state"),
            _parameter("leaving_combat", "boolean", required=False, default=False),
        ],
        [
            _require(
                "record.native_due_round != null and record.native_order != null and "
                "record.native_order_index != null",
                "Legacy native riders without saved group chronology remain unsupported.",
            ),
            _require(
                "record in lasting_state.turn_effects and "
                "record.native_body_source == _actor_state_id and "
                "record.native_individual == actor_state.created_individual_id and "
                "record.owner == _actor_state_id and record.caster == _actor_state_id and "
                "actor_state.created_spell_id == 'giant_insect' and "
                "actor_state.created_individual_id == _actor_state_id",
                "Keep the actual installed native Insect source identity, "
                "including after disappearance.",
            ),
            _require(
                "record.native_body_block_sha256 == '"
                + source["source_stat_block_sha256"]
                + "' and record.native_body_block_sha256 == "
                "actor_state.created_source_block_sha256 "
                "and record.spell == '' and record.cast_id == '' and record.phase == 'start'",
                "Use the pinned native body and its actual saved next-turn consequence.",
            ),
            _require(
                "(record.native_attack == 'Web Bolt' and record.kind == 'speed_zero' and "
                "actor_state.created_insect_form == 'spider') or "
                "(record.native_attack == 'Venomous Spew' and record.kind == 'poisoned' and "
                "actor_state.created_insect_form == 'centipede')",
                "Do not infer another native action from this source witness.",
            ),
            _require(
                "lasting_state.time_stop_source == null or "
                "lasting_state.time_stop_source.active != true",
                "Native Insect rider chronology during Time Stop remains unsupported.",
            ),
            _set("_native_insect_rider_due", False),
            _set("_native_insect_rider_deadline", "record.native_free_time_deadline"),
            _choose(
                "record.native_free_time_deadline != null",
                [
                    _require(
                        "combat.encounter_active != true",
                        "Resolve this carried native rider before replacing its encounter order.",
                    ),
                    _set(
                        "_native_insect_rider_due",
                        "lasting_state.lasting_tick + 0.000000001 >= "
                        "record.native_free_time_deadline",
                    ),
                ],
                [
                    _require(
                        "combat.encounter_active == true and combat.group_order == true and "
                        "len(record.native_order) == len(combat.order) and "
                        "record.native_order_index >= 0 and "
                        "record.native_order_index < len(combat.order) and "
                        "combat.order[record.native_order_index].entity_id == _actor_state_id",
                        "Removed, replaced or ambiguous native Insect Initiative "
                        "ordering is unsupported.",
                    ),
                    _set("_native_insect_order_index", 0),
                    {
                        "for_each": {
                            "collection": "record.native_order",
                            "as": "_native_insect_order_entry",
                            "do": [
                                _require(
                                    "_native_insect_order_entry.entity_id == "
                                    "combat.order[_native_insect_order_index].entity_id",
                                    "Keep every actual retained Insect Initiative entry "
                                    "in its original order.",
                                ),
                                _set(
                                    "_native_insect_order_index", "_native_insect_order_index + 1"
                                ),
                            ],
                        }
                    },
                    _when(
                        "actor_state.is_alive != true or actor_state.created_active != true",
                        [
                            _set(
                                "_native_insect_rider_due",
                                "combat.round > record.native_due_round or "
                                "(combat.round == record.native_due_round and "
                                "combat.turn_index > record.native_order_index)",
                            ),
                        ],
                    ),
                    _when(
                        "leaving_combat == true",
                        [
                            _require(
                                "combat.lasting_epoch_tick != null and "
                                "combat.lasting_epoch_round != null",
                                "Translate only the actual group clock's saved next-turn boundary.",
                            ),
                            _set(
                                "_native_insect_rider_deadline",
                                "combat.lasting_epoch_tick + "
                                "2 * (record.native_due_round - combat.lasting_epoch_round) + "
                                "2 * record.native_order_index * (len(combat.order) ** -1)",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    native = (
        "_effect.native_body_source != null and "
        "(_effect.native_attack == 'Web Bolt' or _effect.native_attack == 'Venomous Spew')"
    )
    process = deepcopy(tools["process_srd_turn_effects"])
    loops = [
        e["for_each"]["do"]
        for e in process["effects"]
        if e.get("for_each", {}).get("as") == "_effect"
        and e.get("for_each", {}).get("collection") == "_pending_turn_effects"
    ]
    if len(loops) != 1:
        raise ValueError("Keep the actual turn-effect retirement and projection owner.")
    due = [i for i, e in enumerate(loops[0]) if e.get("set", {}).get("field") == "_effect_due"]
    if len(due) != 1:
        raise ValueError("Keep the actual owner/phase/turn expiry calculation.")
    process["effects"].insert(
        0,
        _when(
            "lasting_state.has_native_insect_riders == true",
            [
                _set("_native_insect_riders_kept", False),
            ],
        ),
    )
    loops[0].insert(
        due[0] + 1,
        _when(
            native,
            [
                _call(
                    name,
                    {"actor_state": "{_effect.native_body_source}", "record": "{_effect}"},
                    ("_native_insect_rider_due",),
                ),
                _set("_effect_due", "_effect_due or _native_insect_rider_due"),
                _when("_effect_due == false", [_set("_native_insect_riders_kept", True)]),
            ],
        ),
    )
    kept = [
        i
        for i, e in enumerate(process["effects"])
        if e.get("set", {}).get("field") == "lasting_state.turn_effects"
    ]
    if len(kept) != 1:
        raise ValueError("Keep the actual retained turn-effect pool.")
    process["effects"].insert(
        kept[0] + 1,
        _when(
            "lasting_state.has_native_insect_riders == true",
            [_set("lasting_state.has_native_insect_riders", "_native_insect_riders_kept")],
        ),
    )
    advance = deepcopy(tools["advance_group_combat_encounter_turn"])
    sync = [
        i
        for i, e in enumerate(advance["effects"])
        if e.get("call") == "sync_srd_group_lasting_clock"
    ]
    if len(sync) != 1 or not any("advance_initiative" in e for e in advance["effects"][: sync[0]]):
        raise ValueError(
            "Observe only the actual group advance followed by its real clock synchronization."
        )
    advance["effects"].insert(
        sync[0] + 1,
        _when(
            "state.has_native_insect_riders == true",
            [_call("process_srd_turn_effects", {"lasting_state": "state"})],
        ),
    )
    end = deepcopy(tools["end_combat_encounter"])
    inactive = [
        i
        for i, e in enumerate(end["effects"])
        if e.get("set", {}).get("field") == "combat.encounter_active" and e["set"]["value"] is False
    ]
    if len(inactive) != 1:
        raise ValueError(
            "Translate at the actual encounter end before its Initiative chronology is closed."
        )
    native_fields = next(
        e["list_push"]["value"]
        for e in _native_effect(tools, source)["effects"]
        if e.get("list_push", {}).get("list") == "_turn_effects"
    )
    translated = {
        field: None if value is None else "_insect_exit_record." + field
        for field, value in native_fields.items()
    }
    translated["native_free_time_deadline"] = "_native_insect_rider_deadline"
    end["effects"].insert(
        inactive[0],
        _when(
            "state.has_native_insect_riders == true and state.turn_effects != null",
            [
                _set("_insect_exit_records", "state.turn_effects"),
                _set("_insect_exit_kept", []),
                {
                    "for_each": {
                        "collection": "_insect_exit_records",
                        "as": "_insect_exit_record",
                        "do": [
                            _set("_insect_exit_replacement", "_insect_exit_record"),
                            _when(
                                native.replace("_effect", "_insect_exit_record"),
                                [
                                    _call(
                                        name,
                                        {
                                            "actor_state": (
                                                "{_insect_exit_record.native_body_source}"
                                            ),
                                            "record": "{_insect_exit_record}",
                                            "leaving_combat": True,
                                        },
                                        ("_native_insect_rider_deadline",),
                                    ),
                                    _set(
                                        "_insect_exit_replacement",
                                        translated,
                                    ),
                                ],
                            ),
                            {
                                "list_push": {
                                    "list": "_insect_exit_kept",
                                    "value": "_insect_exit_replacement",
                                }
                            },
                        ],
                    },
                },
                _set("state.turn_effects", "_insect_exit_kept"),
            ],
        ),
    )
    return {
        name: helper,
        "process_srd_turn_effects": process,
        "advance_group_combat_encounter_turn": advance,
        "end_combat_encounter": end,
    }


def _native_web_movement(tools):
    """Project the authenticated nonmagical Web separately from spell Speed reductions."""
    projection = deepcopy(tools["project_srd_turn_modifiers"])
    if any(
        e.get("set", {}).get("field") == "target_state.native_insect_speed_zero"
        for e in projection["effects"]
    ):
        return {}
    projection["effects"][:0] = [
        _set("_previous_native_insect_speed_zero", "target_state.native_insect_speed_zero == true"),
        _set("target_state.native_insect_speed_zero", False),
    ]
    native = [
        e
        for e in _walk(projection["effects"])
        if e.get("condition")
        == "_speed_effect.kind == 'speed_zero' and _speed_effect.target == _target_state_id"
    ]
    if len(native) != 1:
        raise ValueError("Keep the actual target-matching Speed-zero record projection.")
    native[0]["on_true"].append(
        _when(
            "_speed_effect.native_attack == 'Web Bolt' and "
            "_speed_effect.native_body_source != null",
            [
                _call(
                    "resolve_srd_native_insect_rider_deadline",
                    {
                        "actor_state": "{_speed_effect.native_body_source}",
                        "record": "{_speed_effect}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                    ("_native_insect_rider_due",),
                ),
                _when(
                    "_native_insect_rider_due != true",
                    [_set("target_state.native_insect_speed_zero", True)],
                ),
            ],
        )
    )
    refresh = [
        e
        for e in projection["effects"]
        if "_previous_reduction != _speed_reduction" in e.get("condition", "")
        and any(
            child.get("call") == "refresh_srd_movement_budget" for child in e.get("on_true", [])
        )
    ]
    if len(refresh) != 1:
        raise ValueError("Keep the actual changed-projection movement refresh boundary.")
    refresh[0]["condition"] = refresh[0]["condition"].replace(
        "_previous_reduction != _speed_reduction",
        "_previous_native_insect_speed_zero != target_state.native_insect_speed_zero or "
        "_previous_reduction != _speed_reduction",
    )
    changed = {"project_srd_turn_modifiers": projection}
    for name, remaining in (
        ("refresh_srd_movement_budget", "combat.movement_remaining"),
        ("derive_srd_scene_movement_budget", "_scene_exploration_remaining"),
    ):
        tool = deepcopy(tools[name])
        branches = [tool["effects"]] + [
            value
            for node in _walk(tool["effects"])
            for key, value in node.items()
            if key in {"on_true", "on_false", "do"} and isinstance(value, list)
        ]
        boundaries = [
            (branch, index)
            for branch in branches
            for index, effect in enumerate(branch)
            if effect.get("set", {}).get("field") == remaining
        ]
        if len(boundaries) != 1 or not any(
            e.get("condition") == "actor_state.spell_speed_reduction != null and "
            "actor_state.spell_freedom_of_movement != true"
            for e in _walk(tool["effects"])
        ):
            raise ValueError("Preserve the final actual movement budget and Freedom spell guard.")
        branch, index = boundaries[0]
        branch.insert(
            index,
            _when("actor_state.native_insect_speed_zero == true", [_set("_movement_speed", 0)]),
        )
        changed[name] = tool
    return changed


def _web_receiver(tools):
    """Delegate the real scalar packet and append the printed on-hit Speed consequence."""
    scalar = deepcopy(tools["apply_srd_qualifying_damage"])
    target = next(p for p in scalar["parameters"] if p["name"] == "target")
    target["resolve"] = "entity"
    source = next(p for p in scalar["parameters"] if p["name"] == "source_state_id")
    source["resolve"] = "entity"
    target_id = "_target_id"
    tool = deepcopy(scalar)
    tool["parameters"] += [_entity("actor_state"), _entity("target_state")]
    tool["pre_conditions"] += [
        {
            "condition": "_source_state_id_id == _actor_state_id and "
            + target_id
            + " == _target_state_id and "
            "damage_type == 'bludgeoning' and actual_attack_hit == true and "
            "actor_state.created_insect_form == 'spider'",
            "error": "Use the actual native Web Bolt receiving path.",
        }
    ]
    tool["effects"] = [
        _call(
            "qualify_srd_source_insect",
            {
                "actor_state": "{_actor_state_id}",
                "caster_state": "{actor_state.created_caster_state_id}",
            },
        ),
        {
            "call": "apply_srd_qualifying_damage",
            "params": _forward(scalar["parameters"]),
            "export_context": "*",
        },
        _call(
            "install_srd_native_insect_turn_effect",
            {
                "caster_state": "{_actor_state_id}",
                "target_state": "{_target_state_id}",
                "lasting_state": "{_lasting_state_id}",
                "kind": "speed_zero",
            },
        ),
    ]
    tool.pop("narrative", None)
    tool["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    return tool


def _spew(tools):
    """Use existing Bonus Action, sight and save owners for the actual centipede."""
    original = tools["spend_srd_bonus_action"]
    requirements = []
    for clause in original["pre_conditions"]:
        if clause["condition"] == "actor.validation_status == 'validated'":
            continue
        clause = deepcopy(clause)
        clause["condition"] = clause["condition"].replace("actor.participant_id", "_actor_state_id")
        if "bonus_action in actor_state.available_bonus_actions" in clause["condition"]:
            clause["condition"] = "actor_state.created_insect_form == 'centipede'"
        requirements.append(clause)
    geometry = _created_actor_attack_geometry_tool()
    cutoff = next(
        i
        for i, e in enumerate(geometry["effects"])
        if e.get("set", {}).get("field") == "_created_attack_targets"
    )
    geometry["effects"] = geometry["effects"][:cutoff] + [
        _require(
            "_created_attack_distance <= 10",
            "Venomous Spew needs a creature actually within10 feet.",
        ),
        _call(
            "resolve_srd_scene_relation",
            {"actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
            ("_scene_can_see",),
        ),
        _require("_scene_can_see == true", "The actual centipede must see its target."),
    ]
    action = _tool(
        "Use the printed native Venomous Spew Bonus Action.",
        [_entity("actor_state"), _entity("target_state")],
        [
            _call("require_srd_pending_window_closed", {}),
            _call(
                "qualify_srd_source_insect",
                {
                    "actor_state": "{_actor_state_id}",
                    "caster_state": "{actor_state.created_caster_state_id}",
                },
            ),
            _require(
                "actor_state.summon_command == 'attack' and "
                "actor_state.created_insect_form == 'centipede' and "
                "target_state.is_alive == true and target_state.creature_type != null and "
                "target_state.is_object != true",
                "Use only an actual commanded centipede and living creature target.",
            ),
            _call(
                "qualify_srd_insect_spew_geometry",
                {"actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
            ),
            _call(
                "consume_srd_bonus_action",
                {"actor_state": "{_actor_state_id}", "bonus_action": "Venomous Spew"},
            ),
            _set("_insect_spew_outcome", "'immune'"),
            _set("_insect_spew_save_roll", None),
            _set("_insect_spew_save_total", None),
            _set("_insect_spew_dc", "actor_state.summon_save_dc"),
            _call(
                "check_srd_target_condition_immunity",
                {
                    "target_state": "{_target_state_id}",
                    "condition": "poisoned",
                    "source_state_id": "{_actor_state_id}",
                },
                ("_source_condition_immune",),
            ),
            _when(
                "_source_condition_immune != true",
                [
                    _set("_insect_spew_character", None),
                    _when(
                        "target_state.creature_kind == 'character'",
                        [_set("_insect_spew_character", "_target_state_id")],
                    ),
                    _call(
                        "resolve_srd_divination_save",
                        {
                            "target_state": "{_target_state_id}",
                            "actor": "{_insect_spew_character}",
                            "ability": "constitution",
                            "dc": "{_insect_spew_dc}",
                        },
                        ("_divination_save_success", "_d20_test", "_adjusted_total"),
                    ),
                    _set("_insect_spew_save_roll", "_d20_test"),
                    _set("_insect_spew_save_total", "_adjusted_total"),
                    _choose(
                        "_divination_save_success == true",
                        [_set("_insect_spew_outcome", "'success'")],
                        [
                            _set("_insect_spew_outcome", "'failure'"),
                            _call(
                                "install_srd_native_insect_turn_effect",
                                {
                                    "caster_state": "{_actor_state_id}",
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "state",
                                    "kind": "poisoned",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    action["pre_conditions"] = requirements
    action = _public(
        action,
        "combat",
        "Venomous Spew",
        [
            ("Save", "_insect_spew_outcome"),
            ("Constitution total", "_insect_spew_save_total"),
            ("DC", "_insect_spew_dc"),
        ],
    )
    action["metadata"]["costs_action"] = False
    return {"qualify_srd_insect_spew_geometry": geometry, "use_srd_insect_venomous_spew": action}


def extend_runtime(text):
    """Compose after final insect attack binding and before saved Reaction derivation."""
    source = _giant_insect_source_profile()
    tools = _load_runtime(text)["tools"]
    changed = _spew(tools) | _chronology_hooks(tools, source) | _native_web_movement(tools)
    changed["install_srd_native_insect_turn_effect"] = _native_effect(tools, source)
    changed["apply_srd_insect_web_bolt_damage"] = _web_receiver(tools)
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    fence = "actor.created_spell_id != 'giant_insect' or attack_name != 'Web Bolt'"
    attack["pre_conditions"] = [p for p in attack["pre_conditions"] if p["condition"] != fence]
    rend = [
        clause
        for clause in attack["pre_conditions"]
        if clause.get("error")
        == "A summoned creature must use its saved Rend profile for this attack."
    ]
    if len(rend) != 1:
        raise ValueError("Keep the actual existing saved summon attack qualification.")
    web_profile = (
        "actor.created_spell_id == 'giant_insect' and actor.created_insect_form == 'spider' "
        "and actor.form_attack_in_progress != true and attack_name == 'Web Bolt' and "
        "attack_modifier == actor.summon_attack_bonus and damage_expression == 'd10' and "
        "damage_modifier == 3 + actor.summon_spell_level and damage_type == 'bludgeoning' and "
        "attack_mode == 'ranged' and normal_range == 60 and long_range == 60"
    )
    web_selection = "actor.created_spell_id == 'giant_insect' and attack_name == 'Web Bolt'"
    if web_selection not in rend[0]["condition"]:
        rend[0]["condition"] = "(" + rend[0]["condition"] + ") or (" + web_selection + ")"
    native = [
        e
        for e in attack["effects"]
        if e.get("condition") == NATIVE and "qualify_srd_source_insect" in str(e)
    ]
    if len(native) != 1:
        raise ValueError("Keep this final insect leaf's actual native body qualifier.")
    selected = [
        e["require"]
        for e in native[0]["on_true"]
        if e.get("require", {}).get("condition") == "attack_name == 'Poison Jab'"
    ]
    if selected:
        if len(selected) != 1:
            raise ValueError("Keep one actual native insect attack selection.")
        selected[0].update(
            condition="attack_name == 'Poison Jab' or (attack_name == 'Web Bolt' and "
            "actor.created_insect_form == 'spider')",
            error="Use only this source insect form's printed attack.",
        )
    native[0]["on_true"].append(
        _when(
            "attack_name == 'Web Bolt'",
            [_require(web_profile, "Use Web Bolt's actual saved spell attack and printed damage.")],
        )
    )
    for effect in tuple(_walk(attack["effects"])):
        if effect.get(
            "call"
        ) != "apply_srd_qualifying_damage" or "game_damage_roll" not in effect.get("params", {}):
            continue
        original = deepcopy(effect)
        web = deepcopy(effect)
        web["call"] = "apply_srd_insect_web_bolt_damage"
        web["params"].update(actor_state="{_actor_id}", target_state="{_target_id}")
        effect.clear()
        effect.update(_choose(NATIVE + " and attack_name == 'Web Bolt'", [web], [original]))
    changed["resolve_adjudicated_creature_attack"] = attack
    if "apply_srd_insect_web_bolt_damage" in tools:
        changed.pop("resolve_adjudicated_creature_attack")
    _normalize(changed)
    return _replace_tools(
        text, {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    )
