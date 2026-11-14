"""Retire the carried statuette at genuine ordinary inventory and held-drop writes."""

from copy import deepcopy

if __package__:
    from .srd_contingency_completion import _emit_changed
    from .srd_contingency_inventory import CHECK
    from .srd_control_behaviors import _call, _when
    from .srd_timed_actor_defenses import _load_runtime
else:
    from srd_contingency_completion import _emit_changed
    from srd_contingency_inventory import CHECK
    from srd_control_behaviors import _call, _when
    from srd_timed_actor_defenses import _load_runtime


OWNERS = {
    "set_srd_carried_inventory": ("actor_state", "inventory"),
    "drop_srd_control_held_items": ("target_state", "_control_kept_inventory"),
}


def _callback(subject):
    return _when(
        f"{subject}.has_contingency == true",
        [_call(CHECK, {"target_state": "{_" + subject + "_id}"})],
    )


def _bind(tool, name):
    """Observe only the one actual write; failed enclosing transactions still roll back."""
    subject, value = OWNERS[name]
    callback = _callback(subject)
    found = 0

    def bind(sequence):
        nonlocal found
        sequence[:] = [node for node in sequence if node != callback]
        result = []
        for node in sequence:
            for branch in ("on_true", "on_false"):
                if branch in node:
                    bind(node[branch])
            if "for_each" in node:
                bind(node["for_each"]["do"])
            result.append(node)
            write = node.get("set", {})
            if write.get("field") == subject + ".inventory":
                if write.get("value") != value:
                    raise ValueError(f"{name} changed its actual carried-inventory write.")
                found += 1
                result.append(callback)
        sequence[:] = result

    bind(tool["effects"])
    if found != 1:
        raise ValueError(f"{name} requires exactly one actual carried-inventory write.")


def bind_tools(tools):
    """Preserve every inventory permission/material check and existing sibling callback."""
    if CHECK not in tools:
        raise ValueError("Emit Contingency before its actual inventory mutation adapter.")
    changed = {}
    for name in OWNERS:
        if name not in tools:
            raise ValueError(f"Contingency inventory loss requires its genuine owner {name}.")
        tool = deepcopy(tools[name])
        _bind(tool, name)
        changed[name] = tool
    return changed


def bind_runtime(text):
    """Bind beside existing inventory callbacks before transformation/Jar/saved captures."""
    return _emit_changed(text, bind_tools(_load_runtime(text)["tools"]))
