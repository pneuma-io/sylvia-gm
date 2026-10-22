"""Open actual queued Tsunami falls only after an owning public action completes."""

from copy import deepcopy

if __package__:
    from .build_srd_5_2_1_mechanics import TOOL_CAPABILITIES
    from .srd_timed_actor_defenses import _load_runtime, _tool, _when
    from .srd_timed_senses import _replace_tools
else:
    from build_srd_5_2_1_mechanics import TOOL_CAPABILITIES
    from srd_timed_actor_defenses import _load_runtime, _tool, _when
    from srd_timed_senses import _replace_tools


DRAIN = "settle_srd_tsunami_exits_node"
FLAG = "state.has_tsunami_pending_exits == true"
EMPTY_STACK = "(state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0)"
RESULT = "_tsunami_exit_action_id"
TRIGGERS = {"queue_srd_tsunami_exit", "refresh_srd_tsunami_sources"}


def _effects(effects):
    """Walk actual calls without treating literal source records as executable effects."""
    for effect in effects:
        yield effect
        for key in ("on_true", "on_false"):
            yield from _effects(effect.get(key, []))
        yield from _effects(effect.get("for_each", {}).get("do", []))


def _owned_call(effect):
    return effect.get("call") == DRAIN and effect.get("export_context") == {
        RESULT: "_pending_action_id"
    }


def _reachable(graph, roots):
    found = set(roots)
    pending = list(found)
    while pending:
        for child in graph.get(pending.pop(), ()):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def _strip_completion(effects):
    """Replace only this adapter's named completion call on regeneration."""
    result = []
    for effect in effects:
        if _owned_call(effect):
            continue
        for key in ("on_true", "on_false"):
            if key in effect:
                effect[key] = _strip_completion(effect[key])
        if "for_each" in effect:
            effect["for_each"]["do"] = _strip_completion(effect["for_each"]["do"])
        if effect.get("condition") in {
            f"{FLAG} and {EMPTY_STACK}",
            f"{FLAG} and {EMPTY_STACK}".replace("state.", "lasting_state."),
        } and not effect.get("on_true"):
            continue
        result.append(effect)
    return result


def bind_runtime(text, public_tools=()):
    """Run after final public Reaction clones; exclude every settlement/damage/motion closure."""
    tools = _load_runtime(text)["tools"]
    if DRAIN not in tools:
        return text
    graph = {
        name: {
            effect["call"]
            for effect in _effects(tool["effects"])
            if "call" in effect and not _owned_call(effect)
        }
        for name, tool in tools.items()
    }
    receiving = {
        name
        for name in tools
        if name.startswith(("settle_srd_", "handoff_srd_", "drain_srd_", "move_srd_"))
        or (name.startswith("apply_srd_") and "damage" in name)
    }
    receiving |= {DRAIN, "resolve_srd_scene_fall_event", "carry_srd_tsunami_creature"}
    excluded = _reachable(graph, receiving)
    candidates = (
        set(public_tools)
        | set(TOOL_CAPABILITIES)
        | {"cast_spell", "cast_spell_outside_combat", "complete_srd_pending_action"}
    ) & tools.keys() - excluded
    changed = {}
    for name in sorted(candidates):
        if not (_reachable(graph, {name}) & TRIGGERS):
            continue
        owner = deepcopy(tools[name])
        owner["effects"] = _strip_completion(owner["effects"])
        completion = _tool(
            owner["description"],
            owner["parameters"],
            [
                _when(
                    f"{FLAG} and {EMPTY_STACK}",
                    [
                        {
                            "call": DRAIN,
                            "params": {"lasting_state": "state"},
                            "export_context": {RESULT: "_pending_action_id"},
                        }
                    ],
                )
            ],
        )
        owner["effects"].extend(completion["effects"])
        for parameter in owner["parameters"]:
            if parameter["name"] == "lasting_state":
                parameter["model_hidden"] = True
        changed[name] = owner
    return _replace_tools(text, changed) if changed else text
