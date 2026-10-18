"""Hand actual Rope Trick drop endpoints to the existing saved falling window."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_mansion_returns import (
        OUTERS,
        _insert_after,
        _normalize,
        _qualifier,
        _receiver_guards,
        _receiving_event,
        _specialize,
        _walk,
    )
    from .srd_mirage_support import _ground_without_landing_requirement
    from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from .srd_spell_travel_dwelling import _lookup
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_mansion_returns import (
        OUTERS,
        _insert_after,
        _normalize,
        _qualifier,
        _receiver_guards,
        _receiving_event,
        _specialize,
        _walk,
    )
    from srd_mirage_support import _ground_without_landing_requirement
    from srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from srd_spell_travel_dwelling import _lookup
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _push, _replace_tools


RETURN = "return_srd_rope_trick_subject"
QUALIFY = "qualify_srd_rope_trick_return"
QUEUE = "queue_srd_rope_trick_drop"
HANDOFF = "handoff_srd_rope_trick_drops"
RECEIPT = "state.rope_trick_return_receipt"
QUEUE_FLAG = "state.has_rope_trick_pending_drops"
FENCE = "Settle the actual queued Rope Trick drop before another ordinary action or time advance."


def _rope(value):
    """Reuse the proven receiving clone while substituting only its source-owned identity."""
    if isinstance(value, str):
        for old, new in (
            ("state.mansion_return_receipt", RECEIPT),
            ("qualify_srd_mansion_return", QUALIFY),
            ("'magnificent_mansion'", "'rope_trick'"),
            ("'mansion_return'", "'rope_trick_return'"),
            ("Mansion", "Rope Trick"),
        ):
            value = value.replace(old, new)
        return value
    if isinstance(value, dict):
        return {key: _rope(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rope(item) for item in value]
    return value


def _qualified_return():
    tool = _rope(_qualifier())
    tool["effects"].append(
        _require(
            "_xd_source.cast_id == _mr_receipt.cast_id",
            "Keep the exact actual Rope Trick cast owning this drop endpoint.",
        )
    )
    return tool


def _source(receipt):
    return [
        *_lookup(receipt + ".source_id"),
        _require(
            "_xd_source.spell == 'rope_trick' and _xd_source.active == false and "
            f"_xd_source.created_tick == {receipt}.created_tick and "
            f"_xd_source.cast_id == {receipt}.cast_id and "
            f"_xd_source.outer_id == {receipt}.outer_id and "
            f"_xd_source.room_id == {receipt}.origin_plane and "
            f"_xd_source.origin_plane == {receipt}.plane",
            "The exact retained Rope Trick source must own the completed drop.",
        ),
    ]


def _queue_tool():
    """Capture actual aftermath once; the completed action, rather than relocation, opens falls."""
    return _tool_scene(
        "Queue the exact completed Rope Trick return after its real receiving callbacks.",
        [],
        [
            _set("_rope_return", RECEIPT),
            _require(
                "_rope_return != null and _rope_return.kind == 'rope_trick_return' and "
                "_rope_return.consumed == true",
                "Only the authentic completed Rope Trick relocation may queue a drop.",
            ),
            *_source("_rope_return"),
            _require(
                "_xd_source.return_pending == true",
                "Queue the actual endpoint before its source return receipt is retired.",
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_rope_return.subject}"},
                POSITION_EXPORTS,
            ),
            _require(
                "_info_scene_entity.active == true and "
                "_info_scene_entity.id == _rope_return.body_id",
                "The returned body must still be the actual admitted source subject.",
            ),
            _when(
                "state.rope_trick_pending_drops == null",
                [_set("state.rope_trick_pending_drops", [])],
            ),
            _set("_rope_drop_queue", "state.rope_trick_pending_drops"),
            _require("len(_rope_drop_queue) < 64", "Complete the existing actual drops first."),
            _loop(
                "_rope_drop_queue",
                "_rope_queued_drop",
                [
                    _require(
                        "_rope_queued_drop.source_id != _rope_return.source_id or "
                        "_rope_queued_drop.body_id != _rope_return.body_id or "
                        "_rope_queued_drop.created_tick != _rope_return.created_tick",
                        "The same authentic return cannot queue a second drop.",
                    )
                ],
            ),
            _set(
                "_rope_drop",
                {
                    "source_id": "_rope_return.source_id",
                    "created_tick": "_rope_return.created_tick",
                    "cast_id": "_rope_return.cast_id",
                    "subject": "_rope_return.subject",
                    "body_id": "_rope_return.body_id",
                    "outer_id": "_rope_return.outer_id",
                    "origin_plane": "_rope_return.origin_plane",
                    "plane": "_rope_return.plane",
                    "return_receipt": "_rope_return",
                    "body": "_info_scene_entity",
                    "captured_tick": "state.lasting_tick",
                },
            ),
            _push("_rope_drop_queue", "_rope_drop"),
            _set("state.rope_trick_pending_drops", "_rope_drop_queue"),
            _set(QUEUE_FLAG, True),
        ],
    )


def _creature_drop_tool():
    """Current flight and existing descent are derived from actual source/body facts."""
    return _tool_scene(
        "Qualify an airborne returned creature without inventing its flight or fall outcome.",
        [_entity("target_state")],
        [
            _require(
                "target_state.is_alive == true",
                "Dropping a dead returned body needs its actual physical fall owner.",
            ),
            _set("_rope_drop_falling", False),
            _call(
                "qualify_srd_mirage_support_flight",
                {"target_state": "{_target_state_id}"},
                ["_support_flight", "_support_scene_descent", "_support_wind_descent"],
            ),
            _when(
                "_support_flight != true and _support_scene_descent != true and "
                "_support_wind_descent != true",
                [
                    _call(
                        "qualify_srd_scene_fall_target",
                        {"target_state": "{_target_state_id}", "fall_cause": "support_lost"},
                        ["_falling_event"],
                    ),
                    _set("_rope_drop_falling", True),
                ],
            ),
        ],
    )


def _handoff_tool():
    """Use the actual endpoint/support before allocating one existing saved falling event."""
    return _tool_scene(
        "Hand completed authentic Rope Trick endpoints to the existing scene-fall opportunity.",
        [],
        [
            _set("_rope_drop_action_id", None),
            _when(
                f"{QUEUE_FLAG} == true and (state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0)",
                [
                    _set("_rope_drop_receipts", "state.rope_trick_pending_drops"),
                    _set("_rope_falling_targets", []),
                    _loop(
                        "_rope_drop_receipts",
                        "_rope_drop_receipt",
                        [
                            *_source("_rope_drop_receipt"),
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_rope_drop_receipt.subject}"},
                                POSITION_EXPORTS,
                            ),
                            _require(
                                "_info_scene_entity.active == true and "
                                "_info_scene_entity.id == _rope_drop_receipt.body_id and "
                                "_info_scene_entity.metadata.plane == "
                                "_rope_drop_receipt.body.metadata.plane and "
                                "_info_scene_entity.footprint.kind == "
                                "_rope_drop_receipt.body.footprint.kind and "
                                "_info_scene_entity.height == _rope_drop_receipt.body.height and "
                                + " and ".join(
                                    f"_info_position[{i}] == _rope_drop_receipt.body.position[{i}]"
                                    for i in range(3)
                                ),
                                "Requalify the actual unchanged returned body before its fall.",
                            ),
                            {
                                "condition": "_info_scene_entity.footprint.kind == 'box'",
                                "on_true": [
                                    _require(
                                        " and ".join(
                                            f"_info_scene_entity.footprint.half_extents[{i}] == "
                                            f"_rope_drop_receipt.body.footprint.half_extents[{i}]"
                                            for i in range(2)
                                        ),
                                        "Keep the actual returned body's dimensions.",
                                    )
                                ],
                                "on_false": [
                                    _require(
                                        "_info_scene_entity.footprint.kind == 'circle' and "
                                        "_info_scene_entity.footprint.radius == "
                                        "_rope_drop_receipt.body.footprint.radius",
                                        "Keep the actual supported returned curved body.",
                                    )
                                ],
                            },
                            *_ground_without_landing_requirement(),
                            _when(
                                "_fall_ground_height == null or "
                                "_info_position[2] > _fall_ground_height + 0.000001",
                                [
                                    _require(
                                        "_info_scene_entity.metadata.participant_ref != null",
                                        "An airborne returned object needs its actual "
                                        "physical drop owner.",
                                    ),
                                    _call(
                                        "qualify_srd_rope_trick_creature_drop",
                                        {
                                            "target_state": "{_info_scene_entity."
                                            "metadata.participant_ref}"
                                        },
                                        ["_rope_drop_falling"],
                                    ),
                                    _when(
                                        "_rope_drop_falling == true",
                                        [
                                            _require(
                                                "_info_scene_entity.metadata.participant_ref "
                                                "not in _rope_falling_targets",
                                                "Choose each authentic falling body once.",
                                            ),
                                            _push(
                                                "_rope_falling_targets",
                                                "_info_scene_entity.metadata.participant_ref",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "len(_rope_falling_targets) > 0",
                        [
                            _call(
                                "begin_srd_scene_fall",
                                {
                                    "target_states": "{_rope_falling_targets}",
                                    "fall_cause": "support_lost",
                                },
                                ["_pending_action_id"],
                            ),
                            _set("_rope_drop_action_id", "_pending_action_id"),
                            _set(
                                "state.pending_srd_actions.{_pending_action_id}.context."
                                "rope_trick_return_receipts",
                                "_rope_drop_receipts",
                            ),
                        ],
                    ),
                    _set("state.rope_trick_pending_drops", []),
                    _set(QUEUE_FLAG, False),
                ],
            ),
        ],
    )


def bind_runtime_consumers(text):
    """Bind after final Travel/receiving/Reaction owners and the Mansion outer-return binder."""
    text = text.replace("\r\n", "\n")
    tools = _load_runtime(text)["tools"]
    required = {
        RETURN,
        "settle_srd_rope_trick_returns",
        "move_srd_travel_subject",
        "qualify_srd_mirage_support_flight",
        "begin_srd_scene_fall",
    }
    if missing := required - tools.keys():
        raise ValueError("Emit the actual Rope/fall owners first: " + ", ".join(sorted(missing)))
    changed = {
        QUALIFY: _qualified_return(),
        QUEUE: _queue_tool(),
        "qualify_srd_rope_trick_creature_drop": _creature_drop_tool(),
        HANDOFF: _handoff_tool(),
    }
    wrapper = _rope(_specialize(tools, tools["move_srd_travel_subject"]))
    calls = [
        e for e in _walk(wrapper["effects"]) if e.get("call") == "move_srd_travel_subject_node"
    ]
    if len(calls) > 1:
        raise ValueError("Rope drop must retain one final relocation node.")
    if calls:
        node = _rope(_specialize(tools, tools["move_srd_travel_subject_node"]))
        calls[0]["call"] = RETURN + "_node"
        for name in ("teleport", "levitate_source_id", "telekinesis_source_id", "sphere_source_id"):
            calls[0]["params"].pop(name, None)
        changed[RETURN + "_node"] = node
        position_owner = node
        _insert_after(
            wrapper["effects"],
            calls[0],
            _rope(_receiving_event(tools, tools["move_srd_travel_subject"])),
        )
    else:
        position_owner = wrapper
    mutations = [e for e in position_owner["effects"] if "spatial_mutation" in e]
    if len(mutations) != 1:
        raise ValueError("Preserve the final owner's one actual position mutation.")
    index = position_owner["effects"].index(mutations[0])
    position_owner["effects"][index:index] = [
        *_rope(_receiver_guards(tools, position_owner)),
        _call(
            QUALIFY, {"subject": "{subject}", "destination": "{destination}", "plane": "{plane}"}
        ),
        _set(RECEIPT + ".consumed", True),
    ]
    if not calls:
        _insert_after(
            position_owner["effects"],
            mutations[0],
            _rope(_receiving_event(tools, tools["move_srd_travel_subject"])),
        )
    wrapper["effects"].append(_call(QUEUE, {}))
    changed[RETURN] = _normalize(wrapper)
    if calls:
        _normalize(changed[RETURN + "_node"])
    for name in (
        *OUTERS,
        "traverse_srd_rope_trick",
        "climb_srd_rope_trick",
        "manipulate_srd_rope_trick_rope",
    ):
        if name not in tools:
            continue
        owner = deepcopy(tools[name])
        if HANDOFF not in str(owner["effects"]):
            owner["effects"].append(_call(HANDOFF, {}, ["_rope_drop_action_id"]))
        changed[name] = _normalize(owner)
    for name in (
        "begin_srd_spell_action",
        "begin_srd_extended_spell_action",
        "begin_srd_character_attack",
        "begin_srd_creature_attack",
        "cast_spell",
        "cast_spell_outside_combat",
        "advance_srd_lasting_time",
        "advance_srd_turn",
        "move_srd_scene_creature",
        "continue_srd_rest",
    ):
        if name not in tools:
            continue
        owner = changed.get(name, deepcopy(tools[name]))
        if not any(effect.get("require", {}).get("error") == FENCE for effect in owner["effects"]):
            owner["effects"].insert(0, _require(f"{QUEUE_FLAG} != true", FENCE))
        changed[name] = _normalize(owner)
    text = _replace_tools(text, {name: tool for name, tool in changed.items() if name in tools})
    added = {name: tool for name, tool in changed.items() if name not in tools}
    if added:
        rendered = yaml.dump(
            added,
            Dumper=getattr(yaml, "CSafeDumper", yaml.SafeDumper),
            sort_keys=False,
            width=105,
        )
        rendered = "".join(
            "  " + line if line.strip() else line for line in rendered.splitlines(True)
        )
        if "\nprocedures:" not in text:
            raise ValueError("Preserve the actual runtime procedures boundary.")
        text = text.replace("\nprocedures:", "\n" + rendered + "procedures:", 1)
    return text
