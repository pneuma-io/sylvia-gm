"""Retain an actual HP trigger before Clone replaces the damaged physical body."""

from copy import deepcopy

if __package__:
    from .srd_contingency_completion import DAMAGE_OWNERS, OBSERVER, _emit_changed
    from .srd_control_behaviors import _call, _when
    from .srd_qualified_packet_implementations import COHORTS, _delegate, _drain
    from .srd_timed_actor_defenses import _load_runtime
else:
    from srd_contingency_completion import DAMAGE_OWNERS, OBSERVER, _emit_changed
    from srd_control_behaviors import _call, _when
    from srd_qualified_packet_implementations import COHORTS, _delegate, _drain
    from srd_timed_actor_defenses import _load_runtime


CLONE = "settle_srd_clone_death"
CORES = {"execute_srd_qualified_scalar_packet", "execute_srd_qualified_composite_packet"}
DELEGATES = {owner: core for core, owner, *_ in COHORTS}


def _observation():
    return _when(
        "target.has_contingency == true",
        [_call(OBSERVER, {"target_state": "{target}", "hp_before": "{_hp_before}"})],
    )


def _bind_owner(tool, name):
    """Bind only a real HP/death path with the original witness still in scope."""
    found = 0

    def bind(sequence, before=False, transitioned=False, dead=False):
        nonlocal found
        own = _observation()
        sequence[:] = [
            node
            for index, node in enumerate(sequence)
            if not (
                node == own
                and index + 1 < len(sequence)
                and sequence[index + 1].get("call") == CLONE
            )
        ]
        result = []
        for node in sequence:
            write = node.get("set", {})
            if write.get("field") == "_hp_before":
                if write.get("value") != "target.hp" or transitioned:
                    raise ValueError(f"{name} changed its actual pre-transition HP witness.")
                before = True
            if "apply_damage" in node:
                if node["apply_damage"] != {"target": "target", "amount": "_hp_damage"}:
                    raise ValueError(f"{name} changed its actual HP transition.")
                if not before or transitioned:
                    raise ValueError(f"{name} requires one witnessed physical HP transition.")
                transitioned = True
            condition = node.get("apply_condition", {})
            if condition.get("condition") == "dead":
                if condition.get("target") != "target":
                    raise ValueError(f"{name} changed its actual damaged death target.")
                dead = True
            if node.get("call") == CLONE:
                if not (before and transitioned and dead):
                    raise ValueError(f"{name} Clone call is not in a witnessed HP death path.")
                if node.get("params") != {"target_state": "{target}"}:
                    raise ValueError(f"{name} changed its actual Clone death binding.")
                result.append(own)
                found += 1
            for branch in ("on_true", "on_false"):
                if branch in node:
                    bind(node[branch], before, transitioned, dead)
            if "for_each" in node:
                bind(node["for_each"]["do"], before, transitioned, dead)
            result.append(node)
        sequence[:] = result

    bind(tool["effects"])
    if not found:
        raise ValueError(f"{name} lacks its actual source-owned Clone HP death callback.")


def bind_tools(tools):
    """Return narrow HP-owner changes, preserving unrelated death and outer drain owners."""
    if OBSERVER not in tools or CLONE not in tools:
        raise ValueError("Emit genuine Contingency and Clone before their ordering adapter.")
    changed = {}
    for entry in DAMAGE_OWNERS:
        name = entry
        first = tools[name]["effects"][0]
        if first.get("call") in CORES:
            name = first["call"]
            owner = tools[entry]
            expected = _delegate(owner, name, True, _drain(owner)[1])["effects"]
            if DELEGATES.get(entry) != name or owner["effects"] != expected:
                raise ValueError(f"{entry} requires its exact qualified packet delegate.")
        if name in changed:
            continue
        tool = deepcopy(tools[name])
        _bind_owner(tool, name)
        changed[name] = tool
    return changed


def bind_runtime(text):
    """Bind after actual BodySoul HP hooks and Contingency observation, before Jar/capture."""
    return _emit_changed(text, bind_tools(_load_runtime(text)["tools"]))
