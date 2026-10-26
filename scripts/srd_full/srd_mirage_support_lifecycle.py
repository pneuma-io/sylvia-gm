"""Retain actual Mirage support loss until the completed action can open a fall window."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _tool_scene
    from .srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_scene_relations import _tool_scene
    from srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _push, _replace_tools


QUEUE = "queue_srd_mirage_support_loss"
HANDOFF = "handoff_srd_mirage_support_losses"
MARKER = "state.has_mirage_pending_support_losses == true"
QUEUE_FENCE = (
    "Settle the queued Mirage support loss before another ordinary action or time advance."
)


def _queue_tool():
    """Capture one genuine supported-body receipt per actor before tangible retirement."""
    return _tool_scene(
        "Retain exact supported bodies before retiring this actual Mirage source or piece.",
        [_parameter("mirage_id")],
        [
            _call(
                "capture_srd_mirage_support",
                {"mirage_id": "{mirage_id}"},
                ["_mirage_support_receipts"],
            ),
            _when(
                "len(_mirage_support_receipts) > 0",
                [
                    _when(
                        "state.mirage_pending_support_losses == null",
                        [
                            _set("state.mirage_pending_support_losses", []),
                        ],
                    ),
                    _set("_mirage_loss_queue", "state.mirage_pending_support_losses"),
                    _set("_mirage_loss_seen", []),
                    _loop(
                        "_mirage_loss_queue",
                        "_mirage_loss_old",
                        [
                            _push("_mirage_loss_seen", "_mirage_loss_old.body.actor_id"),
                        ],
                    ),
                    _loop(
                        "_mirage_support_receipts",
                        "_mirage_loss_receipt",
                        [
                            _when(
                                "_mirage_loss_receipt.body.actor_id not in _mirage_loss_seen",
                                [
                                    _require(
                                        "len(_mirage_loss_queue) < 64",
                                        "Complete the existing support loss before "
                                        "retiring more bodies.",
                                    ),
                                    _push("_mirage_loss_queue", "_mirage_loss_receipt"),
                                    _push(
                                        "_mirage_loss_seen", "_mirage_loss_receipt.body.actor_id"
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set("state.mirage_pending_support_losses", "_mirage_loss_queue"),
                    _set("state.has_mirage_pending_support_losses", "len(_mirage_loss_queue) > 0"),
                ],
            ),
        ],
    )


def _handoff_tool():
    """Requalify support after all current action windows, then use the real fall allocator."""
    return _tool_scene(
        "Settle captured Mirage support losses through the existing saved scene-fall owner.",
        [],
        [
            _set("_mirage_loss_action_id", None),
            _when(
                "state.has_mirage_pending_support_losses == true and "
                "state.pending_srd_action == null and "
                "(state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0)",
                [
                    _set("_mirage_loss_receipts", "state.mirage_pending_support_losses"),
                    _call(
                        "observe_srd_mirage_support_change",
                        {"receipts": "{_mirage_loss_receipts}"},
                        ["_mirage_support_results", "_falling_events"],
                    ),
                    _loop(
                        "_mirage_support_results",
                        "_mirage_loss_result",
                        [
                            _require(
                                " or ".join(
                                    "_mirage_loss_result.status == '" + status + "'"
                                    for status in (
                                        "not_alive",
                                        "supported",
                                        "flight_supported",
                                        "existing_scene_descent",
                                        "existing_wind_descent",
                                        "falling",
                                    )
                                ),
                                "The actual changed body, landing ground or descent needs "
                                "supported qualification before this Mirage can be removed.",
                            ),
                        ],
                    ),
                    _set("_mirage_loss_targets", []),
                    _loop(
                        "_falling_events",
                        "_mirage_loss_fall",
                        [
                            _push("_mirage_loss_targets", "_mirage_loss_fall.actor_id"),
                        ],
                    ),
                    _when(
                        "len(_mirage_loss_targets) > 0",
                        [
                            _call(
                                "begin_srd_scene_fall",
                                {
                                    "target_states": "{_mirage_loss_targets}",
                                    "fall_cause": "support_lost",
                                },
                                ["_pending_action_id"],
                            ),
                            _set("_mirage_loss_action_id", "_pending_action_id"),
                            _require(
                                "state.pending_srd_actions[_pending_action_id].kind == "
                                "'scene_fall' and "
                                "state.pending_srd_actions[_pending_action_id].status == 'open'",
                                "Mirage support loss needs the actual saved open scene-fall event.",
                            ),
                            _set(
                                "state.pending_srd_actions.{_pending_action_id}.context."
                                "mirage_support_receipts",
                                "_mirage_loss_receipts",
                            ),
                        ],
                    ),
                    _set("state.mirage_pending_support_losses", []),
                    _set("state.has_mirage_pending_support_losses", False),
                ],
            ),
        ],
    )


def bind_runtime_consumers(text):
    """Wrap only actual source/piece retirement; outer action completion owns handoff."""
    tools = _load_runtime(text)["tools"]
    required = {
        "capture_srd_mirage_support",
        "observe_srd_mirage_support_change",
        "retire_srd_mirage_source",
        "retire_srd_mirage_piece",
        "begin_srd_scene_fall",
    }
    if missing := required - tools.keys():
        raise ValueError(
            "Mirage support lifecycle needs owning tools: " + ", ".join(sorted(missing))
        )
    changed = {QUEUE: _queue_tool(), HANDOFF: _handoff_tool()}
    for name in ("retire_srd_mirage_source", "retire_srd_mirage_piece"):
        owner = deepcopy(tools[name])
        if not any(effect.get("call") == QUEUE for effect in owner["effects"]):
            owner["effects"].insert(0, _call(QUEUE, {"mirage_id": "{mirage_id}"}))
        changed[name] = owner
    # These are existing ordinary-entry guards. Completion and the actual fall allocator
    # stay callable so a saved support loss can reach its owning Reaction window.
    for name in (
        "require_srd_pending_window_closed",
        "begin_srd_spell_action",
        "begin_srd_character_attack",
        "begin_srd_creature_attack",
        "begin_srd_extended_spell_action",
        "continue_srd_extended_spell_action",
        "cast_spell",
        "cast_spell_outside_combat",
        "end_combat_turn",
        "advance_srd_lasting_time",
    ):
        if name not in tools:
            continue
        owner = deepcopy(tools[name])
        if not any(
            effect.get("require", {}).get("error") == QUEUE_FENCE for effect in owner["effects"]
        ):
            owner["effects"].insert(
                0,
                _require(
                    "state.has_mirage_pending_support_losses != true and "
                    "(state.mirage_pending_support_losses == null or "
                    "len(state.mirage_pending_support_losses) == 0)",
                    QUEUE_FENCE,
                ),
            )
        changed[name] = owner
    return _replace_tools(text, changed)
