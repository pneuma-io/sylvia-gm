"""Queue source-qualified completed damage without calling a Reaction from damage."""

from copy import deepcopy

if __package__:
    from .srd_reaction_spells import _allocate
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
    from .srd_timed_senses import _loop, _replace_tools
else:
    from srd_reaction_spells import _allocate
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
    from srd_timed_senses import _loop, _replace_tools


QUEUE = "queue_srd_received_damage_reaction"
RESOLVED = "queue_srd_creature_received_damage_reaction"
KNOWN_RECEIVER = "target.prepared_spells != null and 'hellish_rebuke' in target.prepared_spells"
OWNERS = {
    "apply_srd_damage",
    "apply_srd_composite_damage",
    "apply_srd_qualifying_damage",
    "apply_srd_qualifying_composite_damage",
    "apply_srd_prismatic_damage",
    "apply_srd_prismatic_composite_damage",
    "apply_srd_dream_wake_damage",
    "execute_srd_qualified_scalar_packet",
    "execute_srd_qualified_composite_packet",
}


def _push(field, value):
    return {"list_push": {"list": field, "value": value}}


def _allocate_queued():
    """Use the owning ID; native start damage belongs to the actual scheduled creature."""
    effects = _allocate("received_damage", "_source_state_id", "_target_state_id")
    pushes = [e for e in effects if e.get("list_push", {}).get("list") == "_pending_stack"]
    writes = [
        e for e in effects if e.get("set", {}).get("field") == "state.pending_srd_action_stack"
    ]
    if len(pushes) != 1 or len(writes) != 1:
        raise ValueError("Damage queuing needs the exact owning pending allocator stack boundary.")
    turn_captures = [e for e in effects if e.get("condition") == "combat.encounter_active == true"]
    turn_field = "state.pending_srd_actions.{_pending_action_id}.turn_actor"
    if turn_captures != [
        _when("combat.encounter_active == true", [_set(turn_field, "combat.active_actor")])
    ]:
        raise ValueError("Damage queuing needs the exact owning original-turn capture.")
    position = effects.index(turn_captures[0])
    effects[position] = _when(
        "combat.encounter_active == true",
        [
            _when(
                "combat.turn_active == true and combat.active_actor != null",
                [_set(turn_field, "combat.active_actor")],
            ),
            _when(
                "combat.turn_active != true and combat.current_actor != null",
                [_set(turn_field, "combat.current_actor")],
            ),
        ],
    )
    return [e for e in effects if e not in pushes and e not in writes]


def tools():
    """An internal receiving suffix is the witness; final deltas never redefine damage."""
    parameters = [
        _entity("target_state"),
        _parameter("effective_damage", "number", minimum=0),
        _parameter("hp_before", "number", minimum=0),
        _parameter("temp_hp_before", "number", minimum=0),
    ]
    queue = _tool(
        "Queue only an actual completed receiving transition from a classified creature source.",
        parameters + [_parameter("source_state_id")],
        [
            _set("_received_damage_event_id", None),
            _set("_received_source_classified", False),
            _set("_received_scene_rows", []),
            _when(
                "effective_damage > 0 and state.spatial_scene != null and "
                "state.spatial_scene.entities != null and "
                "target_state.creature_kind != null",
                [_set("_received_scene_rows", "state.spatial_scene.entities")],
            ),
            _loop(
                "_received_scene_rows",
                "_received_scene_row",
                [
                    _when(
                        "(_received_scene_row.id == source_state_id or "
                        "_received_scene_row.metadata.participant_ref == source_state_id) and "
                        "_received_scene_row.active == true and "
                        "_received_scene_row.metadata.is_object != true and "
                        "_received_scene_row.metadata.spell_force != true and "
                        "_received_scene_row.metadata.creature_type != 'object' and "
                        "_received_scene_row.metadata.creature_type != null",
                        [_set("_received_source_classified", True)],
                    ),
                ],
            ),
            _when(
                "_received_source_classified == true",
                [
                    _call(
                        RESOLVED,
                        {
                            "target_state": "{_target_state_id}",
                            "source_state": "{source_state_id}",
                            "effective_damage": "{effective_damage}",
                            "hp_before": "{hp_before}",
                            "temp_hp_before": "{temp_hp_before}",
                        },
                        ["_received_damage_event_id"],
                    ),
                ],
            ),
        ],
    )
    resolved = _tool(
        "Retain the private completed packet witness from actual prepared source and receiver.",
        deepcopy(parameters) + [_entity("source_state")],
        [
            _set("_received_damage_event_id", None),
            _when(
                "effective_damage > 0 and "
                "(source_state.creature_kind == 'character' or "
                "source_state.creature_kind == 'monster') "
                "and (target_state.creature_kind == 'character' or "
                "target_state.creature_kind == 'monster')",
                [
                    _require(
                        "state.lasting_tick != null", "Damage receipts need the actual clock."
                    ),
                    *_allocate_queued(),
                    _set("_received_damage_event_id", "_pending_action_id"),
                    _set("state.pending_srd_actions.{_pending_action_id}.status", "'queued'"),
                    _set("state.pending_srd_actions.{_pending_action_id}.stage", "'queued'"),
                    _set(
                        "state.pending_srd_actions.{_pending_action_id}.owner_kind",
                        "'hellish_received_damage'",
                    ),
                    _set(
                        "state.pending_srd_actions.{_pending_action_id}.received_damage",
                        {
                            "effective_damage": "effective_damage",
                            "hp_before": "hp_before",
                            "temp_hp_before": "temp_hp_before",
                            "hp_after": "target_state.hp",
                            "temp_hp_after": "target_state.temp_hp",
                            "created_tick": "state.lasting_tick",
                        },
                    ),
                    _set(
                        "state.pending_srd_actions.{_pending_action_id}.parent_action_id",
                        None,
                    ),
                    _when(
                        "len(_pending_stack) > 0",
                        [
                            _set(
                                "state.pending_srd_actions.{_pending_action_id}.parent_action_id",
                                "_pending_stack[len(_pending_stack) - 1]",
                            ),
                        ],
                    ),
                    _when(
                        "state.pending_srd_received_damage_ids == null",
                        [_set("state.pending_srd_received_damage_ids", [])],
                    ),
                    _set("_received_damage_queue", "state.pending_srd_received_damage_ids"),
                    _require(
                        "len(_received_damage_queue) < 64", "Settle queued damage opportunities."
                    ),
                    _push("_received_damage_queue", "_pending_action_id"),
                    _set("state.pending_srd_received_damage_ids", "_received_damage_queue"),
                ],
            ),
        ],
    )
    return {QUEUE: queue, RESOLVED: resolved}


def bind_receiving_events(text):
    """Append to actual expanded receiving bodies before any final packet/suffix clones."""
    text = text.replace("\r\n", "\n")
    runtime = _load_runtime(text)
    changed = tools()
    for name in OWNERS & runtime["tools"].keys():
        original = runtime["tools"][name]
        if QUEUE in str(original["effects"]):
            continue
        anchors = [
            i
            for i, e in enumerate(original["effects"])
            if e.get("set", {}).get("field") == "_hp_before"
        ]
        if not anchors:
            # Named packet delegates inherit this hook from their single real implementation.
            continue
        if len(anchors) != 1:
            raise ValueError("Damage Reaction receipts need one actual HP lifecycle.")
        parameters = {p["name"] for p in original["parameters"]}
        if not {"target", "source_state_id"} <= parameters:
            raise ValueError("Damage Reaction receipts need real source/receiving identities.")
        tool = deepcopy(original)
        target_parameter = next(p for p in tool["parameters"] if p["name"] == "target")
        target_id = "{_target_id}" if target_parameter.get("resolve") == "entity" else "{target}"
        tool["effects"].insert(
            anchors[0],
            _when(KNOWN_RECEIVER, [_set("_received_temp_hp_before", "target.temp_hp")]),
        )
        callback = _when(
            "_effective_damage > 0 and source_state_id != null and len(source_state_id) > 0 "
            "and (" + KNOWN_RECEIVER + ")",
            [
                _call(
                    QUEUE,
                    {
                        "target_state": target_id,
                        "source_state_id": "{source_state_id}",
                        "effective_damage": "{_effective_damage}",
                        "hp_before": "{_hp_before}",
                        "temp_hp_before": "{_received_temp_hp_before}",
                    },
                    ["_received_damage_event_id"],
                ),
            ],
        )
        drain = [i for i, e in enumerate(tool["effects"]) if "drain_srd_dream_wakes" in str(e)]
        tool["effects"].insert(drain[-1] if drain else len(tool["effects"]), callback)
        changed[name] = tool
    changed = {n: t for n, t in changed.items() if runtime["tools"].get(n) != t}
    return _replace_tools(text, changed) if changed else text
