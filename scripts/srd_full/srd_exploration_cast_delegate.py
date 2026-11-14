"""Delegate final exploration casting to the exact public cast while retaining its outputs."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _emit
    from .srd_timed_actor_defenses import _load_runtime
else:
    from srd_actor_spell_modifiers import _emit
    from srd_timed_actor_defenses import _load_runtime

OWNER = "cast_spell_outside_combat"
CAST = "cast_spell"


def _forward(parameter):
    """Forward admitted IDs rather than their bound entity/participant snapshots."""
    name = parameter["name"]
    if parameter.get("resolve") in {"entity", "participant"}:
        return "{_" + name + "_id}"
    return "{" + name + "}"


def bind_runtime(text):
    """Run after exploration rewrites and all final cast consumers, before admission."""
    tools = _load_runtime(text)["tools"]
    original, cast = tools[OWNER], tools[CAST]
    for field in ("parameters", "pre_conditions"):
        if original.get(field) != cast.get(field):
            raise SystemExit(f"Exploration delegation requires identical final cast {field}.")
    effects = [
        {
            "call": CAST,
            "params": {parameter["name"]: _forward(parameter) for parameter in cast["parameters"]},
            "export_context": "*",
        }
    ]
    if original["effects"] == effects:
        return text
    if original["effects"] != cast["effects"]:
        raise SystemExit("Exploration delegation cannot discard distinct final cast effects.")
    tool = deepcopy(original)
    tool["effects"] = effects
    return _emit(text, {OWNER: tool})
