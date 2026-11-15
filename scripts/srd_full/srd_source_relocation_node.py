"""Separate the exact final Travel position mutation from its post-movement observers."""

from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools

NODE = "move_srd_travel_subject_node"
WRAPPER = "move_srd_travel_subject"


def _exports(value):
    """Retain actual pre-mutation locals needed by unchanged post-movement observers."""
    result = set()
    if isinstance(value, list):
        for item in value:
            result.update(_exports(item))
    elif isinstance(value, dict):
        field = value.get("set", {}).get("field")
        if isinstance(field, str) and field.startswith("_"):
            result.add(field)
        store = value.get("store_as")
        if isinstance(store, str) and store.startswith("_"):
            result.add(store)
        exported = value.get("export_context", [])
        if isinstance(exported, (list, dict)):
            result.update(key for key in exported if key.startswith("_"))
        for item in value.values():
            result.update(_exports(item))
    return result


def bind_runtime_consumers(text):
    """Split only the final owning implementation; repeat composition preserves exact bytes."""
    tools = _load_runtime(text)["tools"]
    wrapper = deepcopy(tools[WRAPPER])
    mutation_indices = [
        i for i, effect in enumerate(wrapper["effects"]) if "spatial_mutation" in effect
    ]
    if not mutation_indices:
        if wrapper["effects"][0].get("call") == NODE and NODE in tools:
            return text
        raise ValueError("Emit the relocation node after the final Travel and Barrier binders.")
    if len(mutation_indices) != 1:
        raise ValueError("Travel relocation must retain its single actual position mutation.")
    index = mutation_indices[0]
    node = deepcopy(wrapper)
    node["description"] = "Commit the exact final owning Travel mutation before movement observers."
    owning_effects = deepcopy(wrapper["effects"][: index + 1])
    exports = _exports(owning_effects)
    guaranteed = set()
    for effect in owning_effects:
        field = effect.get("set", {}).get("field")
        store = effect.get("store_as")
        guaranteed.update(value for value in (field, store) if isinstance(value, str))
        guaranteed.update(effect.get("export_context", {}))
    node["effects"] = [
        {"set": {"field": key, "value": None}} for key in sorted(exports - guaranteed)
    ] + owning_effects
    params = {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
        + "}"
        for p in node["parameters"]
    }
    wrapper["effects"] = [
        {
            "call": NODE,
            "params": params,
            "export_context": {key: key for key in sorted(exports)},
        },
        *wrapper["effects"][index + 1 :],
    ]
    return _replace_tools(text, {NODE: node, WRAPPER: wrapper})
