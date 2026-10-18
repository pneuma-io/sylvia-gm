"""Keep the actual composite receiving lifecycle's rest interruption equal to scalar damage."""

import re
from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


OWNERS = ("apply_srd_composite_damage", "apply_srd_qualifying_composite_damage")


def _effects(effects):
    for effect in effects:
        yield effect
        for branch in ("on_true", "on_false"):
            yield from _effects(effect.get(branch, []))
        if "for_each" in effect:
            yield from _effects(effect["for_each"]["do"])


FLAG = "_effective_damage_lifecycle_needed"


def _receiving_guard(existing, rest):
    """Preserve OR short-circuit order using two independently bounded predicates."""
    if len(existing) > 500 or len(rest) > 500:
        raise ValueError("Receiving predicate exceeds the unchanged expression limit")
    mark = {"set": {"field": FLAG, "value": True}}
    return [
        {"set": {"field": FLAG, "value": False}},
        {
            "condition": existing,
            "on_true": [deepcopy(mark)],
            "on_false": [{"condition": rest, "on_true": [deepcopy(mark)]}],
        },
    ]


def bind_runtime(text):
    """Run after body-source joins, before Dream, Bond receiver refresh and packet capture."""
    tools = _load_runtime(text)["tools"]
    candidates = [
        effect
        for effect in _effects(tools["apply_srd_qualifying_damage"]["effects"])
        if effect.get("on_true")
        == [
            {
                "call": "interrupt_srd_rest",
                "params": {"target_state": "{target}", "reason": "damage"},
            }
        ]
    ]
    if len(candidates) != 1 or not candidates[0].get("condition", "").startswith(
        "_effective_damage > 0 and (target.spell_preparation_open"
    ):
        raise SystemExit("Composite rest interruption needs the actual scalar receiving owner.")
    rest = candidates[0]
    changed = {}
    for name in OWNERS:
        original = tools[name]
        already_joined = rest in list(_effects(original["effects"]))
        if already_joined and any(
            effect.get("condition") == FLAG + " == true" for effect in original["effects"]
        ):
            continue
        tool = deepcopy(original)
        groups = [
            effect
            for effect in tool["effects"]
            if "end_srd_invisibility_damage_source" in str(effect)
            and "_effective_damage" in effect.get("condition", "")
        ]
        if len(groups) != 1:
            raise SystemExit("Composite rest interruption needs one completed observer boundary.")
        group = groups[0]
        existing = group["condition"]
        if already_joined:
            suffix = (" or (" + rest["condition"] + ")").replace(
                "_effective_damage > 0", "_effective_damage>0"
            )
            if not existing.endswith(suffix):
                raise ValueError("Previously joined receiving predicate has an unknown boundary")
            existing = existing[: -len(suffix)]
        predicates = _receiving_guard(existing, rest["condition"])
        index = tool["effects"].index(group)
        tool["effects"][index:index] = predicates
        group["condition"] = FLAG + " == true"
        if not already_joined:
            group["on_true"].insert(0, deepcopy(rest))
        changed[name] = tool
    if not changed:
        return text
    for name in changed:
        text = re.sub(rf"(?m)^(  {re.escape(name)}:[^\n]*)\r\n", r"\1\n", text, count=1)
    return _replace_tools(text, changed)
