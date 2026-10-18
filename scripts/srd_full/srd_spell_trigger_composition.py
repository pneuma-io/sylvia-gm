"""Bind independent wards to actual paid casting, scene mutations and clock boundaries."""

from copy import deepcopy

if __package__:
    from .srd_spell_triggers import CHOICES
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_spell_triggers import CHOICES
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _require, _set, _tool, _when
    from srd_timed_senses import _loop, _push, _replace_tools


def _effect_lists(effects):
    """Visit actual runtime branches without descending into literal effect data."""
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _effect_lists(effect[key])
        if "for_each" in effect:
            yield from _effect_lists(effect["for_each"]["do"])


def bind_runtime(text):
    """Compose once before final reaction clones so retained casts use the same paid leaf."""
    tools = _load_runtime(text)["tools"]
    if "apply_srd_trigger_spell" not in tools:
        raise SystemExit("Trigger composition needs its source owner.")
    changed = {}
    cast = deepcopy(tools["cast_spell"])
    prior = {parameter["name"] for parameter in cast["parameters"]}
    cast["parameters"] += [
        deepcopy(parameter) for parameter in CHOICES if parameter["name"] not in prior
    ]
    for effects in list(_effect_lists(cast["effects"])):
        effects[:] = [
            effect
            for effect in effects
            if "_spell_definition.family == 'trigger_spell'" not in effect.get("condition", "")
        ]
    resolution = next(
        effects
        for effects in _effect_lists(cast["effects"])
        if any(
            "_spell_definition.family == 'information_spell'" in effect.get("condition", "")
            for effect in effects
        )
    )
    install = _call(
        "apply_srd_trigger_spell",
        {
            **{parameter["name"]: "{" + parameter["name"] + "}" for parameter in CHOICES},
            "caster_state": "{_caster_state_id}",
            "spell": "{spell}",
            "slot_level": "{_slot_level}",
            "save_dc": "{_casting_profile.save_dc}",
        },
    )
    install["export_context"] = {
        "_trigger_id": "_trigger_id",
        "_trigger_cast_summary": "_spell_summary_rows",
        "_trigger_cast_details": "_spell_detail_rows",
    }
    resolution.append(
        _when(
            "_spell_cast_failed != true and _spell_definition.family == 'trigger_spell'",
            [
                install,
                _loop(
                    "_trigger_cast_summary",
                    "_trigger_cast_row",
                    [_push("_spell_summary_rows", "_trigger_cast_row")],
                ),
                _loop(
                    "_trigger_cast_details",
                    "_trigger_cast_row",
                    [_push("_spell_detail_rows", "_trigger_cast_row")],
                ),
            ],
        )
    )
    changed["cast_spell"] = cast

    refresh = _when(
        "state.has_spell_triggers == true",
        [
            _call("refresh_srd_spell_triggers", {}),
        ],
    )
    for name, original in tools.items():
        tool = deepcopy(changed.get(name, original))
        edited = False
        for effects in list(_effect_lists(tool["effects"])):
            effects[:] = [effect for effect in effects if effect != refresh]
            # Holder changes and removals can move or destroy the real anchor as well.
            boundaries = [
                index for index, effect in enumerate(effects) if "spatial_mutation" in effect
            ]
            for index in reversed(boundaries):
                effects.insert(index + 1, deepcopy(refresh))
                edited = True
        if name == "process_srd_turn_effects":
            tool["effects"] = [
                effect
                for effect in tool["effects"]
                if not (
                    effect.get("condition")
                    in {
                        "state.has_spell_triggers == true",
                        "lasting_state.has_spell_triggers == true",
                    }
                    and len(effect.get("on_true", [])) == 1
                    and effect["on_true"][0].get("call") == "refresh_srd_spell_triggers"
                )
            ]
            tool["effects"].append(deepcopy(refresh))
            edited = True
        if edited or name in changed:
            changed[name] = tool
    if "resolve_srd_symbol_boundary" in tools:
        _bind_symbol_boundaries(tools | changed, changed)
    if "qualify_srd_symbol_fear_movement" in tools:
        _bind_symbol_fear_turn(tools | changed, changed)
    return _replace_tools(text, changed)


def _bind_symbol_boundaries(tools, changed):
    """Feed the Symbol owner actual pre-move geometry and the saved end-turn actor."""
    move = deepcopy(tools["move_srd_travel_subject"])
    marker = "state.has_active_symbol_auras == true and _travel_guard_needed == true"
    move["effects"] = [e for e in move["effects"] if e.get("condition") != marker]
    before = next(
        i for i, e in enumerate(move["effects"]) if e.get("set", {}).get("field") == "_travel_patch"
    )
    move["effects"].insert(
        before,
        _when(
            marker,
            [
                _set("_symbol_entry_previous_position", "_travel_mover.position"),
                _set("_symbol_entry_previous_plane", "_travel_mover.metadata.plane"),
            ],
        ),
    )
    # Retirement, native relocation receipts and other existing consumers settle first.
    move["effects"].append(
        _when(
            marker,
            [
                _call(
                    "resolve_srd_symbol_boundary",
                    {
                        "target_state": "{_travel_guard_target}",
                        "boundary_kind": "entry",
                        "previous_position": "{_symbol_entry_previous_position}",
                        "previous_plane": "{_symbol_entry_previous_plane}",
                    },
                ),
            ],
        )
    )
    changed["move_srd_travel_subject"] = move
    clock = deepcopy(tools["process_srd_turn_effects"])
    end_marker = (
        "boundary_phase == 'end' and boundary_actor != '' and "
        "state.has_active_symbol_auras == true and combat.turn_sequence != null"
    )
    clock["effects"] = [e for e in clock["effects"] if e.get("condition") != end_marker]
    clock["effects"].append(
        _when(
            end_marker,
            [
                _call(
                    "resolve_srd_symbol_boundary",
                    {
                        "target_state": "{boundary_actor}",
                        "boundary_kind": "end",
                    },
                ),
            ],
        )
    )
    changed["process_srd_turn_effects"] = clock
    if "resolve_srd_symbol_exploration_round" in tools:
        _bind_symbol_exploration(tools | changed, changed)
    # The existing public awakening action owns the cost and source retirement.
    shake = deepcopy(tools["shake_srd_sleeping_creature"])
    contact_marker = "target_state.symbol_affliction_managed == true"
    shake["effects"] = [e for e in shake["effects"] if e.get("condition") != contact_marker]
    contact = deepcopy(tools["shake_srd_symbol_sleeper"]["effects"][:-1])
    shake["effects"].insert(0, _when(contact_marker, contact))
    changed["shake_srd_sleeping_creature"] = shake


def _bind_symbol_exploration(tools, changed):
    """Resolve genuine exploration rounds and entries without skipping an active aura's saves."""
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    message = "Advance one round at a time while an active Symbol aura affects exploration."
    clock["effects"] = [
        effect for effect in clock["effects"] if effect.get("require", {}).get("error") != message
    ]
    clock["effects"].insert(
        0,
        _require(
            "combat.encounter_active == true or lasting_state.has_active_symbol_auras != true "
            "or elapsed_ticks <= 2",
            message,
        ),
    )
    changed["advance_srd_lasting_clock"] = clock
    boundary = deepcopy(tools["process_srd_turn_effects"])
    marker = "state.has_active_symbol_auras == true and combat.encounter_active != true"
    boundary["effects"] = [e for e in boundary["effects"] if e.get("condition") != marker]
    boundary["effects"].append(
        _when(
            marker, [_call("resolve_srd_symbol_exploration_round", {}, ["_symbol_clock_details"])]
        )
    )
    changed["process_srd_turn_effects"] = boundary
    segment_name = "move_srd_scene_creature_environment_segment"
    if segment_name not in tools:
        return
    segment = deepcopy(tools[segment_name])
    entry_marker = "state.has_active_symbol_auras == true"
    segment["effects"] = [
        e
        for e in segment["effects"]
        if not (e.get("condition") == entry_marker and "_symbol_walk_previous" in str(e))
    ]
    index = next(
        i
        for i, e in enumerate(segment["effects"])
        if e.get("set", {}).get("field") == "_move_patch"
    )
    segment["effects"].insert(
        index,
        _when(
            entry_marker,
            [
                _set("_symbol_walk_previous_position", "_move_scene_entity.position"),
                _set("_symbol_walk_previous_plane", "_move_scene_entity.metadata.plane"),
            ],
        ),
    )
    segment["effects"].append(
        _when(
            entry_marker,
            [
                _call(
                    "resolve_srd_symbol_boundary",
                    {
                        "target_state": "{_actor_state_id}",
                        "boundary_kind": "entry",
                        "previous_position": "{_symbol_walk_previous_position}",
                        "previous_plane": "{_symbol_walk_previous_plane}",
                    },
                )
            ],
        ),
    )
    changed[segment_name] = segment


def _bind_symbol_fear_turn(tools, changed):
    """Require actual retreat or exhausted native movement before ending a Fear turn."""
    native_limits = [
        _when("not (" + p["condition"] + ")", [_set("_symbol_retreat_native_blocked", True)])
        for p in tools["spend_combat_movement"]["pre_conditions"]
        if "actor_state." in p["condition"]
    ]
    changed["require_srd_symbol_fear_retreat"] = _tool(
        "Settle Symbol retreat against its exact source and the creature's native movement budget.",
        [_entity("actor_state")],
        [
            _call(
                "qualify_srd_symbol_fear_movement",
                {"actor_state": "{_actor_state_id}"},
                [
                    "_symbol_fear_movement_active",
                    "_symbol_fear_movement_sources",
                ],
            ),
            _when(
                "_symbol_fear_movement_active == true",
                [
                    _call("refresh_srd_movement_budget", {"actor_state": "{_actor_state_id}"}),
                    _set("_symbol_retreat_native_blocked", False),
                    *native_limits,
                    _loop(
                        "_symbol_fear_movement_sources",
                        "_symbol_retreat_source",
                        [
                            _require(
                                "_symbol_retreat_source.moved_feet >= 30 or "
                                "combat.movement_remaining <= 0 "
                                "or _symbol_retreat_native_blocked == true",
                                "Complete the actual Symbol Fear retreat before ending this turn; "
                                "unresolved route inability needs its owning qualification.",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    end = deepcopy(tools["end_combat_turn"])
    marker = "actor.symbol_affliction_managed == true"
    end["effects"] = [
        e
        for e in end["effects"]
        if not (e.get("condition") == marker and "require_srd_symbol_fear_retreat" in str(e))
    ]
    end["effects"].insert(
        0,
        _when(
            marker,
            [
                _call("require_srd_symbol_fear_retreat", {"actor_state": "{_actor_id}"}),
            ],
        ),
    )
    changed["end_combat_turn"] = end
