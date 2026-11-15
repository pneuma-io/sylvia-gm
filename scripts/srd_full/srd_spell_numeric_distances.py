"""Keep source-derived Euclidean distances numeric through the common cast windows."""

from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


OWNERS = (
    "cast_spell",
    "cast_spell_outside_combat",
    "begin_srd_spell_action",
    "cast_srd_reaction_spell",
)


def bind_runtime_consumers(text):
    """Change only the inherited distance type; actual range and cost owners remain exact."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in OWNERS:
        if name not in tools:
            if name == "cast_srd_reaction_spell":
                continue
            raise ValueError("Numeric cast distance needs the actual common owner: " + name)
        original = tools[name]
        distances = [p for p in original["parameters"] if p["name"] == "target_distance"]
        if len(distances) != 1 or distances[0]["type"] not in {"integer", "number"}:
            raise ValueError("Numeric cast distance needs the exact retained formal: " + name)
        if distances[0]["type"] == "number":
            continue
        owner = deepcopy(original)
        next(p for p in owner["parameters"] if p["name"] == "target_distance")["type"] = "number"
        changed[name] = owner
    return _replace_tools(text, changed) if changed else text
