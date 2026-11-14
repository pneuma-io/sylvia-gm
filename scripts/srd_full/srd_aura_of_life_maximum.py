"""Protect native maximum-HP reductions through Aura of Life's existing live recipient query."""

import re
from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools

MARKER = "_aura_life_native_maximum_protected"
WIGHT = "apply_srd_native_wight_life_drain_packet"
MUMMY = "resolve_srd_native_mummy_rot_boundary"


def _lists(value):
    if isinstance(value, list):
        yield value
        for child in value:
            yield from _lists(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _lists(child)


def _guard(effects, target):
    """Keep the native reduction and its death-prevention receipt as one guarded consequence."""
    return [
        {"set": {"field": MARKER, "value": False}},
        {
            "condition": "lasting_state.has_spell_wards == true",
            "on_true": [
                {
                    "call": "resolve_srd_spell_ward_benefits",
                    "params": {"target_state": target},
                    "export_context": {MARKER: "_ward_life_effective"},
                }
            ],
        },
        {"condition": MARKER + " != true", "on_true": effects},
    ]


def _already_guarded(effects, target):
    if not effects or effects[0].get("set", {}).get("field") != MARKER:
        return False
    if len(effects) != 3 or effects != _guard(effects[-1].get("on_true", []), target):
        raise ValueError("Aura of Life native maximum guard changed unexpectedly.")
    return True


def _bind_wight(tool):
    owners = [
        e
        for effects in _lists(tool["effects"])
        for e in effects
        if isinstance(e, dict) and e.get("condition") == "_wight_damage_taken > 0"
    ]
    if len(owners) != 1 or "target.max_hp" not in str(owners[0]):
        raise ValueError("Aura of Life needs the genuine Wight maximum-reduction owner.")
    if not _already_guarded(owners[0]["on_true"], "{target}"):
        owners[0]["on_true"] = _guard(owners[0]["on_true"], "{target}")


def _bind_mummy(tool):
    owners = [
        effects
        for effects in _lists(tool["effects"])
        if any(
            isinstance(e, dict)
            and e.get("set", {}).get("field")
            == "lasting_state.timed_condition_sources.{source_id}.native_next_reduction_tick"
            for e in effects
        )
    ]
    if len(owners) != 1:
        raise ValueError("Aura of Life needs the genuine Mummy daily maximum-reduction owner.")
    effects = owners[0]
    tick = next(
        i
        for i, e in enumerate(effects)
        if e.get("set", {}).get("field")
        == "lasting_state.timed_condition_sources.{source_id}.native_next_reduction_tick"
    )
    if tick >= 3 and _already_guarded(effects[tick - 3 : tick], "{_target_state_id}"):
        return
    start = next(
        i
        for i, e in enumerate(effects)
        if e.get("condition") == "target_state.max_hp <= _native_mummy_daily_reduction"
    )
    if (
        start + 2 != tick
        or effects[start + 1].get("condition") != "_native_mummy_daily_prevented != true"
    ):
        raise ValueError("Preserve the native Mummy reduction consequences and next-day clock.")
    effects[start : start + 2] = _guard(effects[start : start + 2], "{_target_state_id}")


def _unprotected_reductions(value, protected=False):
    """Reject a newly introduced self-subtracting maximum writer until its owning seam is joined."""
    if isinstance(value, list):
        for child in value:
            yield from _unprotected_reductions(child, protected)
    elif isinstance(value, dict):
        setting = value.get("set", {})
        field = setting.get("field", "")
        expression = setting.get("value")
        if (
            field.endswith(".max_hp")
            and isinstance(expression, str)
            and re.search(re.escape(field) + r"\s*-", expression)
            and not protected
        ):
            yield field
        guarded = value.get("condition") in {
            MARKER + " != true",
            "_aura_maximum_hp_protected != true",
        }
        for key, child in value.items():
            yield from _unprotected_reductions(child, protected or (key == "on_true" and guarded))


def bind_runtime_consumers(text):
    """Run after final native Wight/curse emission and before receiving or reaction copies."""
    tools = _load_runtime(text)["tools"]
    if "resolve_srd_spell_ward_benefits" not in tools or WIGHT not in tools or MUMMY not in tools:
        raise ValueError(
            "Aura of Life maximum protection needs its final ward and native receivers."
        )
    changed = {name: deepcopy(tools[name]) for name in (WIGHT, MUMMY)}
    _bind_wight(changed[WIGHT])
    _bind_mummy(changed[MUMMY])
    for name, original in tools.items():
        if any(_unprotected_reductions(changed.get(name, original).get("effects", []))):
            raise ValueError("Join the actual maximum-HP reduction owner to Aura of Life: " + name)
    changed = {name: tool for name, tool in changed.items() if tool != tools[name]}
    return _replace_tools(text, changed) if changed else text
