"""Bind Prismatic's final typed packets without unused scalar presentation snapshots."""

import re
from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _emit
    from .srd_timed_actor_defenses import _load_runtime
else:
    from srd_actor_spell_modifiers import _emit
    from srd_timed_actor_defenses import _load_runtime

PRESENTATION_FIELDS = frozenset(
    {
        "_hp_after",
        "_hp_lost",
        "_temp_hp_after",
        "_is_alive_after",
        "_conditions_after",
        "_death_failures_after",
        "_stable_after",
        "_stable_recovery_hours_remaining_after",
    }
)
PACKETS = {
    "apply_srd_prismatic_damage": "apply_srd_qualifying_damage",
    "apply_srd_prismatic_composite_damage": "apply_srd_qualifying_composite_damage",
}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _without_presentation_snapshot(original, fields=PRESENTATION_FIELDS):
    """Remove only unused outputs, failing if a retained consumer needs any removed value."""
    tool = deepcopy(original)
    removed = [effect for effect in tool["effects"] if effect.get("set", {}).get("field") in fields]
    if len(removed) != len(fields) or {effect["set"]["field"] for effect in removed} != fields:
        raise SystemExit("Prismatic needs the exact final typed owner's presentation outputs.")
    tool["effects"] = [effect for effect in tool["effects"] if effect not in removed]
    references = {
        field
        for text in _strings(tool)
        for field in fields
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])", text)
    }
    if references:
        raise SystemExit(
            "Prismatic cannot remove presentation values required by a retained consumer: "
            + ", ".join(sorted(references))
        )
    return tool


def _effects(effects):
    for effect in effects:
        yield effect
        for branch in ("on_true", "on_false"):
            yield from _effects(effect.get(branch, []))
        if "for_each" in effect:
            yield from _effects(effect["for_each"]["do"])


def bind_runtime(text):
    """Run after all typed packet consumers and before Root creates reaction continuations."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name, parent in PACKETS.items():
        fields = PRESENTATION_FIELDS
        if parent == "apply_srd_qualifying_composite_damage":
            fields = fields - {"_hp_lost"}
        changed[name] = _without_presentation_snapshot(tools[parent], fields)
    target = deepcopy(tools["resolve_srd_prismatic_target"])
    aliases = {parent: name for name, parent in PACKETS.items()}
    found = set()
    for effect in _effects(target["effects"]):
        name = effect.get("call")
        if name in aliases:
            effect["call"] = aliases[name]
            found.add(effect["call"])
        elif name in PACKETS:
            found.add(name)
    if found != set(PACKETS):
        raise SystemExit("Prismatic requires its actual final scalar and composite packet calls.")
    changed["resolve_srd_prismatic_target"] = target
    # The shared emitter's new-tool anchor uses LF; native Windows archives retain CRLF.
    text = text.replace("procedures: {}\r\n", "procedures: {}\n", 1)
    if any(name not in tools for name in PACKETS) and "procedures: {}\n" not in text:
        raise SystemExit(
            "Prismatic requires the runtime's empty procedures footer for new packets."
        )
    return _emit(text, changed)
