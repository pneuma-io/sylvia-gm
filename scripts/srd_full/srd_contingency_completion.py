"""Queue authentic receiving HP loss and drain only outside the release call graph."""

import re
from copy import deepcopy

if __package__:
    from .build_srd_5_2_1_mechanics import TOOL_CAPABILITIES
    from .srd_actor_spell_modifiers import _call, _when
    from .srd_control_behaviors import _walk
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_actor_defenses import _tool as normalized_tool
    from .srd_timed_senses import _replace_tools
else:
    from build_srd_5_2_1_mechanics import TOOL_CAPABILITIES
    from srd_actor_spell_modifiers import _call, _when
    from srd_control_behaviors import _walk
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_actor_defenses import _tool as normalized_tool
    from srd_timed_senses import _replace_tools


OBSERVER = "observe_srd_contingency_damage"
DRAIN = "drain_srd_contingency_releases"
QUEUE = "contingency_release_queue"
DAMAGE_OWNERS = (
    "apply_srd_damage",
    "apply_srd_composite_damage",
    "apply_srd_qualifying_damage",
    "apply_srd_qualifying_composite_damage",
)
SETTLEMENT_ROOTS = {
    DRAIN,
    "settle_srd_travel_exile_returns",
    "settle_srd_levitate_returns",
    "handoff_srd_telekinesis_releases",
    "handoff_srd_mirage_support_losses",
}


def _reachable(graph, roots):
    """Include roots and all actual descendants, including private captured implementations."""
    reached, pending = set(), list(roots)
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        pending.extend(graph.get(name, set()) - reached)
    return reached


def _graph(tools):
    return {
        name: {node["call"] for node in _walk(tool["effects"]) if "call" in node}
        for name, tool in tools.items()
    }


def _emit_changed(text, changed):
    for name in changed:
        text = re.sub(rf"(?m)^(  {re.escape(name)}:[^\n]*)\r\n", r"\1\n", text, count=1)
    return _replace_tools(text, changed) if changed else text


def bind_damage(text):
    """Join real HP loss before Magic Jar copies the body and later receivers capture it."""
    tools = _load_runtime(text)["tools"]
    if OBSERVER not in tools:
        raise ValueError("Contingency receiving needs its actual installed source observer.")
    changed = {}
    for name in DAMAGE_OWNERS:
        owner = deepcopy(tools[name])
        existing = [node for node in _walk(owner["effects"]) if node.get("call") == OBSERVER]
        if existing:
            if len(existing) != 1:
                raise ValueError(f"Contingency receiving has duplicated observations: {name}")
            continue
        groups = [
            (index, effect)
            for index, effect in enumerate(owner["effects"])
            if "end_srd_invisibility_damage_source" in str(effect)
            and "_effective_damage" in effect.get("condition", "")
        ]
        coalesced = [pair for pair in groups if "refresh_srd_spell_wards" in str(pair[1])]
        if len(coalesced) == 1:
            groups = coalesced
        if len(groups) != 1:
            raise ValueError(f"Contingency needs one completed receiving group: {name}")
        index, group = groups[0]
        prefix = list(_walk(owner["effects"][:index]))
        if sum(node.get("set", {}).get("field") == "_hp_before" for node in prefix) != 1:
            raise ValueError(f"Contingency needs the real pre-transition HP witness: {name}")
        transitions = [node["apply_damage"] for node in prefix if "apply_damage" in node]
        if transitions != [{"target": "target", "amount": "_hp_damage"}]:
            raise ValueError(f"Contingency needs the actual completed HP transition: {name}")
        original = group["condition"]
        guard = f"({original}) or (_effective_damage>0 and target.has_contingency == true)"
        if len(guard) > 500:
            raise ValueError(f"Contingency receiving guard exceeds the existing limit: {name}")
        group["condition"] = guard
        group["on_true"] = [
            _when(original, group["on_true"]),
            _when(
                "target.has_contingency == true",
                [
                    _call(
                        OBSERVER,
                        {"target_state": "{target}", "hp_before": "{_hp_before}"},
                    )
                ],
            ),
        ]
        changed[name] = owner
    return _emit_changed(text, changed)


def bind_runtime(text, public_tools):
    """Drain after final outer producers; exclude every release/settlement descendant."""
    tools = _load_runtime(text)["tools"]
    if DRAIN not in tools or OBSERVER not in tools:
        raise ValueError("Contingency completion needs the genuine release and receiving owners.")
    graph = _graph(tools)
    excluded = _reachable(graph, SETTLEMENT_ROOTS & tools.keys())
    candidates = (
        set(public_tools)
        | set(TOOL_CAPABILITIES)
        | {"cast_spell", "cast_spell_outside_combat", "advance_srd_lasting_clock"}
    ) & (tools.keys() - excluded)
    changed = {}
    for name in sorted(candidates):
        if DRAIN in graph[name] or OBSERVER not in _reachable(graph, {name}):
            continue
        owner = deepcopy(tools[name])
        completion = normalized_tool(
            owner["description"],
            owner["parameters"],
            [
                _when(
                    f"state.{QUEUE} != null and len(state.{QUEUE}) > 0",
                    [_call(DRAIN, {})],
                )
            ],
        )
        owner["effects"].extend(completion["effects"])
        for parameter in owner["parameters"]:
            if parameter["name"] == "lasting_state":
                parameter["model_hidden"] = True
        changed[name] = owner
    joined_graph = _graph(tools | changed)
    reachable_from_drain = _reachable(joined_graph, {DRAIN})
    cycles = set(changed) & reachable_from_drain
    if cycles:
        raise ValueError(
            f"Contingency outer drains would recurse through release: {sorted(cycles)}"
        )
    if changed.keys() & excluded:
        raise ValueError("Contingency outer drain entered an actual settlement closure.")
    return _emit_changed(text, changed)
