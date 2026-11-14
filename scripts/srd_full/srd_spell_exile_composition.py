"""Settle exact queued spell returns after their public action owner completes."""

from copy import deepcopy

if __package__:
    from .build_srd_5_2_1_mechanics import TOOL_CAPABILITIES
    from .srd_spell_trigger_composition import _effect_lists
    from .srd_timed_actor_defenses import _call, _load_runtime, _tool, _when
    from .srd_timed_senses import _replace_tools
else:
    from build_srd_5_2_1_mechanics import TOOL_CAPABILITIES
    from srd_spell_trigger_composition import _effect_lists
    from srd_timed_actor_defenses import _call, _load_runtime, _tool, _when
    from srd_timed_senses import _replace_tools


def _reachable(graph, start):
    """Find owning call descendants so settlement cannot call back into itself."""
    found = set(start)
    while True:
        joined = found | set().union(*(graph.get(name, set()) for name in found))
        if joined == found:
            return found
        found = joined


def bind_runtime(text, public_tools):
    """Bind only complete outer actions, preserving the sole relocation leaf."""
    tools = _load_runtime(text)["tools"]
    settlements = {
        "settle_srd_travel_exile_returns":
            "state.travel_exile_targets != null and len(state.travel_exile_targets) > 0",
        "settle_srd_levitate_returns": "state.has_levitate_pending_returns == true",
        "handoff_srd_telekinesis_releases": "state.has_telekinesis_pending_releases == true",
        "handoff_srd_mirage_support_losses": "state.has_mirage_pending_support_losses == true",
    }
    settlements = {name: marker for name, marker in settlements.items() if name in tools}
    if not settlements:
        return text
    graph = {
        name: {
            effect["call"]
            for effects in _effect_lists(tool["effects"])
            for effect in effects
            if "call" in effect
        }
        for name, tool in tools.items()
    }
    within_settlement = _reachable(graph, set(settlements))
    public = (
        set(public_tools)
        | set(TOOL_CAPABILITIES)
        | {
            "cast_spell",
            "cast_spell_outside_combat",
        }
    )
    changed = {}
    markers = set(settlements.values())
    markers |= {marker.replace("state.", "lasting_state.") for marker in markers}
    for name in sorted(public & tools.keys() - within_settlement):
        descendants = _reachable(graph, {name})
        if not descendants & {
            "end_srd_concentration_node",
            "release_srd_telekinesis_grip",
            "retire_srd_mirage_source",
            "retire_srd_mirage_piece",
        }:
            continue
        tool = deepcopy(tools[name])
        tool["effects"] = [
            e
            for e in tool["effects"]
            if e.get("condition") not in markers
            and e.get("call") not in settlements
        ]
        completion = _tool(
            tool["description"], tool["parameters"],
            [_when(marker, [_call(owner, {})]) for owner, marker in settlements.items()],
        )
        for parameter in tool["parameters"]:
            if parameter["name"] == "lasting_state":
                parameter["model_hidden"] = True
        tool["effects"].extend(completion["effects"])
        changed[name] = tool
    return _replace_tools(text, changed)
