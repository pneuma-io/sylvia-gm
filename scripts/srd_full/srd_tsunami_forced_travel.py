"""Observe Tsunami exits around authentic source-caused relocation receivers."""

from copy import deepcopy

if __package__:
    from .srd_courier_chronology import _emit
    from .srd_reverse_gravity import _source
    from .srd_scene_relations import _tool_scene
    from .srd_spell_travel import _scene_subject
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop
    from .srd_tsunami_travel import (
        CAPTURE,
        CAPTURE_GUARD,
        ENTRIES,
        OBSERVE,
        OBSERVE_GUARD,
    )
else:
    from srd_courier_chronology import _emit
    from srd_reverse_gravity import _source
    from srd_scene_relations import _tool_scene
    from srd_spell_travel import _scene_subject
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop
    from srd_tsunami_travel import CAPTURE, CAPTURE_GUARD, ENTRIES, OBSERVE, OBSERVE_GUARD


RECEIVERS = {
    "move_srd_reverse_gravity_subject": (
        "move_srd_reverse_gravity_subject_node",
        "qualify_srd_reverse_gravity_motion",
        "reverse_gravity_pending_motion",
    ),
    "move_srd_whirlpool_subject": (
        "move_srd_whirlpool_subject_node",
        "qualify_srd_whirlpool_motion",
        "whirlpool_pending_motion",
    ),
}
HELD = "check_srd_tsunami_gravity_aloft"
LIVE = "check_srd_tsunami_gravity_source"
HELD_EXPORT = "_tsunami_exit_gravity_held"


def _private(parameters, effects, description):
    tool = _tool_scene(description, parameters, effects)
    for parameter in tool["parameters"]:
        parameter["model_hidden"] = True
    return tool


def _live_tool():
    return _private(
        [_entity("caster_state"), _parameter("source_id"), _parameter("sequence", "integer")],
        [
            *_source(),
            _set("_tsunami_gravity_live", False),
            _when(
                "_gravity_source.active == true and _gravity_source.spell == 'reverse_gravity' "
                "and _gravity_source.caster == _caster_state_id and "
                "_gravity_source.sequence == sequence and "
                "state.lasting_tick >= _gravity_source.started_tick and "
                "state.lasting_tick < _gravity_source.expires_tick",
                [
                    _when(
                        "caster_state.concentration_spell_id == 'reverse_gravity' and "
                        "caster_state.concentration_effect_family == 'reverse_gravity' and "
                        "caster_state.concentration_sequence == sequence",
                        [_set("_tsunami_gravity_live", True)],
                    )
                ],
            ),
        ],
        "Qualify this exact current Gravity source without retiring or settling it.",
    )


def _held_tool():
    return _private(
        [_parameter("subject")],
        [
            _set(HELD_EXPORT, False),
            _when(
                "state.has_reverse_gravity_sources == true",
                [
                    *_scene_subject("{subject}", "_tsunami_gravity_body"),
                    _set("_tsunami_gravity_sources", "state.reverse_gravity_sources"),
                    _loop(
                        "_tsunami_gravity_sources",
                        "_tsunami_gravity_source",
                        [
                            _when(
                                "_tsunami_gravity_source.active == true",
                                [
                                    _call(
                                        LIVE,
                                        {
                                            "caster_state": "{_tsunami_gravity_source.caster}",
                                            "source_id": "{_tsunami_gravity_source.id}",
                                            "sequence": "{_tsunami_gravity_source.sequence}",
                                        },
                                        ["_tsunami_gravity_live"],
                                    ),
                                    _when(
                                        "_tsunami_gravity_live == true",
                                        [
                                            _set(
                                                "_tsunami_gravity_subjects",
                                                "_tsunami_gravity_source.subjects",
                                            ),
                                            _loop(
                                                "_tsunami_gravity_subjects",
                                                "_tsunami_gravity_subject",
                                                [
                                                    _when(
                                                        "_tsunami_gravity_subject.phase == "
                                                        "'hovering' and "
                                                        "_tsunami_gravity_subject.body_id == "
                                                        "_tsunami_gravity_body.id and "
                                                        "_tsunami_gravity_subject.creature == "
                                                        "_tsunami_gravity_body.metadata."
                                                        "participant_ref",
                                                        [
                                                            _require(
                                                                "_tsunami_gravity_body.metadata."
                                                                "plane == _tsunami_gravity_source."
                                                                "plane and "
                                                                "_tsunami_gravity_body.position[2] "
                                                                "== _tsunami_gravity_source."
                                                                "origin[2] + 100",
                                                                "Reconcile the real hovering "
                                                                "Gravity body before a water fall.",
                                                            ),
                                                            *[
                                                                _require(
                                                                    "_tsunami_gravity_body."
                                                                    f"position[{i}] == "
                                                                    "_tsunami_gravity_subject."
                                                                    f"origin[{i}]",
                                                                    "Moved Gravity hover needs "
                                                                    "its actual source witness.",
                                                                )
                                                                for i in range(2)
                                                            ],
                                                            _set(HELD_EXPORT, True),
                                                        ],
                                                    )
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            )
                        ],
                    ),
                ],
            ),
        ],
        "Read the actual active Gravity hover witness; other aloft sources are not classified.",
    )


def _settlement(owner):
    """Keep a real exit while a different actual source owns its continued hover/future fall."""
    owner = deepcopy(owner)
    found = 0
    for effects in _lists(owner["effects"]):
        effects[:] = [effect for effect in effects if effect.get("call") != HELD]
        for index, effect in enumerate(effects):
            if effect.get("condition") not in {
                "_info_position[2] > _fall_ground_height",
                f"_info_position[2] > _fall_ground_height and {HELD_EXPORT} != true",
            } or not any(
                e.get("list_push", {}).get("list") == "_tsunami_fall_targets"
                for e in _effects(effect.get("on_true", []))
            ):
                continue
            effect["condition"] = (
                f"_info_position[2] > _fall_ground_height and {HELD_EXPORT} != true"
            )
            effects.insert(
                index,
                _call(HELD, {"subject": "{_tsunami_exit.subject}"}, [HELD_EXPORT]),
            )
            found += 1
            break
    if found != 1:
        raise ValueError("Preserve the exact water-exit target selection before native fall.")
    return owner


def _lists(effects):
    yield effects
    for effect in list(effects):
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"].get("do", []))


def _effects(effects):
    for effect in effects:
        yield effect
        for key in ("on_true", "on_false", "effects"):
            if key in effect:
                yield from _effects(effect[key])
        if "for_each" in effect:
            yield from _effects(effect["for_each"].get("do", []))


def _strip_observation(effects):
    """Remove only this adapter's exact shared capture/observe insertion on repeat."""
    return [
        effect
        for effect in effects
        if effect.get("set", {}).get("field") != ENTRIES
        and effect.get("call") not in {CAPTURE, OBSERVE}
        and not (
            effect.get("condition") in {CAPTURE_GUARD, OBSERVE_GUARD}
            and len(effect.get("on_true", [])) == 1
            and effect["on_true"][0].get("call") in {CAPTURE, OBSERVE}
        )
    ]


def _receiver(wrapper, node, node_name, qualifier, receipt):
    """Keep real qualification/consumption and every native receiving effect intact."""
    result = deepcopy(wrapper)
    result["effects"] = _strip_observation(result["effects"])
    calls = [
        (index, effect)
        for index, effect in enumerate(result["effects"])
        if effect.get("call") == node_name
    ]
    if len(calls) != 1 or any("spatial_mutation" in e for e in result["effects"]):
        raise ValueError("Preserve the source receiver's single final relocation-node call.")
    index, call = calls[0]
    exports = call.get("export_context", {})
    if not isinstance(exports, dict) or exports.get("_travel_scene_move") != "_travel_scene_move":
        raise ValueError("The source receiver must export its authentic native move receipt.")
    parameter_names = {p["name"] for p in result["parameters"]}
    if not {"subject", "destination", "plane", "lasting_state"} <= parameter_names:
        raise ValueError("Keep the actual source receiver's body, plane and state bindings.")
    if {"teleport", "levitate_source_id", "telekinesis_source_id", "sphere_source_id"} & (
        parameter_names
    ):
        raise ValueError("Source-caused movement cannot expose caller bypass choices.")
    if not any(e.get("call") == qualifier for e in _effects(result["effects"][:index])):
        raise ValueError("Authenticate the paid source before capturing water membership.")
    native = [i for i, e in enumerate(node["effects"]) if "spatial_mutation" in e]
    if len(native) != 1 or node["effects"][native[0]].get("store_as") != "_travel_scene_move":
        raise ValueError("Use the actual source node's sole named native mutation.")
    before = list(_effects(node["effects"][: native[0]]))
    if not any(e.get("call") == qualifier for e in before) or not any(
        e.get("set", {}).get("field")
        in (
            f"state.{receipt}.consumed",
            f"lasting_state.{receipt}.consumed",
        )
        and e["set"].get("value") is True
        for e in before
    ):
        raise ValueError("Preserve exact source requalification and consumption before movement.")
    result["effects"][index : index + 1] = [
        _set(ENTRIES, []),
        _when(CAPTURE_GUARD, [_call(CAPTURE, {"subject": "{subject}"}, [ENTRIES])]),
        call,
        _when(
            OBSERVE_GUARD,
            [
                _call(
                    OBSERVE,
                    {
                        "subject": "{subject}",
                        "entries": "{" + ENTRIES + "}",
                        "motion": "{_travel_scene_move}",
                    },
                )
            ],
        ),
    ]
    return result


def bind_runtime_consumers(text):
    """Bind after final source receiver captures/Travel1 and before retained Reaction clones."""
    text = text.replace("\r\n", "\n")
    tools = _load_runtime(text)["tools"]
    present = [name for name in RECEIVERS if name in tools]
    if not present or "queue_srd_tsunami_exit" not in tools:
        return text
    if CAPTURE not in tools or OBSERVE not in tools:
        raise ValueError("Bind the authentic Tsunami Travel capture/observer owner first.")
    changed = {LIVE: _live_tool(), HELD: _held_tool()}
    for name in present:
        node_name, qualifier, receipt = RECEIVERS[name]
        if node_name not in tools or qualifier not in tools:
            raise ValueError("Use the final source receiver and its actual saved-motion qualifier.")
        owner = _receiver(tools[name], tools[node_name], node_name, qualifier, receipt)
        if owner != tools[name]:
            changed[name] = owner
    settlement = "settle_srd_tsunami_exits_node"
    if settlement not in tools:
        raise ValueError("Use the authentic outer water-exit settlement owner.")
    changed[settlement] = _settlement(tools[settlement])
    changed = {name: owner for name, owner in changed.items() if tools.get(name) != owner}
    return _emit(text, changed) if changed else text
