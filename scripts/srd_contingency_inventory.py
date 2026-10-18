"""Actual physical inventory loss retires the carried Contingency component once."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _call, _when
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import _call, _when
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


# These actual owners remove physical inventory. Melding/wearing/stowing it is
# not inventory loss; released child spells have independent lifetimes.
INVENTORY_OWNERS = {
    "install_srd_transformation_source": [],
    "settle_srd_clone_death": [],
    "settle_srd_clone_transfer": [],
    "drop_srd_heated_object": "_heat_kept_inventory",
}
GUARD = "target_state.has_contingency == true"
CHECK = "check_srd_contingency_source"


def _callback():
    return _when(
        GUARD,
        [_call(CHECK, {"target_state": "{_target_state_id}"})],
    )


def _bind_inventory_loss(tool, name):
    """Place the check in the actual write sequence, preserving its original source guard."""
    expected = INVENTORY_OWNERS[name]
    found = 0

    def bind(sequence):
        nonlocal found
        sequence[:] = [node for node in sequence if node != _callback()]
        result = []
        for node in sequence:
            for branch in ("on_true", "on_false"):
                if branch in node:
                    bind(node[branch])
            if "for_each" in node:
                bind(node["for_each"]["do"])
            result.append(node)
            write = node.get("set", {})
            if write.get("field") == "target_state.inventory":
                if write.get("value") != expected:
                    raise ValueError(f"{name} changed its genuine physical inventory-loss write.")
                found += 1
                result.append(_callback())
        sequence[:] = result

    bind(tool["effects"])
    if found != 1:
        raise ValueError(f"{name} needs exactly one actual physical inventory-loss write.")


def bind_tools(tools):
    """Return only the narrow owned successors; ordinary damage and rolls are untouched."""
    if CHECK not in tools:
        raise ValueError("Emit Contingency before binding its physical component loss.")
    changed = {}
    for name in INVENTORY_OWNERS:
        if name not in tools:
            raise ValueError(f"Contingency component loss requires its actual owner {name}.")
        tool = deepcopy(tools[name])
        _bind_inventory_loss(tool, name)
        changed[name] = tool
    return changed


def bind_runtime(text):
    """Bind after the existing Contingency removal adapter and before body/saved captures."""
    tools = _load_runtime(text)["tools"]
    return _replace_tools(text, bind_tools(tools))
