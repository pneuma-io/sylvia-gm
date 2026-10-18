"""Expose a fresh matching-type receipt from the actual final qualified damage packet."""

import re
from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _parameter, _set, _when
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_actor_spell_modifiers import _parameter, _set, _when
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


OWNERS = ("apply_srd_qualifying_damage", "apply_srd_qualifying_composite_damage")
CHOICE = "observe_damage_type"
RECEIPT = "_source_observed_type_damage"
SUFFIX = " or observe_damage_type != 'none'"


def _replace_owned(text, changed):
    """Normalize only owned native CRLF headers for the established fragment replacer."""
    for name in changed:
        text = re.sub(rf"(?m)^(  {re.escape(name)}:[^\n]*)\r\n", r"\1\n", text, count=1)
    return _replace_tools(text, changed)


def _parameter_receipt(tool):
    kinds = next(
        parameter["enum_values"]
        for parameter in tool["parameters"]
        if parameter["name"] in {"damage_type", "component_1_damage_type"}
    )
    return _parameter(CHOICE, "enum", required=False, default="none", enum_values=["none", *kinds])


def _remove_receipt(effects):
    """Undo only this binder's exact fresh Set and conditional type sums before regeneration."""
    result = []
    for original in effects:
        if original == _set(RECEIPT, 0):
            continue
        children = original.get("on_true", [])
        if len(children) == 1 and children[0].get("set", {}).get("field") == RECEIPT:
            if not original.get("condition", "").endswith(" == " + CHOICE):
                raise SystemExit("Typed receipt cannot discard a later changed receipt consumer.")
            continue
        effect = deepcopy(original)
        if effect.get("condition", "").endswith(SUFFIX):
            effect["condition"] = effect["condition"][: -len(SUFFIX)]
        for key in ("on_true", "on_false"):
            if key in effect:
                effect[key] = _remove_receipt(effect[key])
        if "for_each" in effect:
            effect["for_each"]["do"] = _remove_receipt(effect["for_each"]["do"])
        result.append(effect)
    return result


def _sum(kind, amount):
    return _when(kind + " == " + CHOICE, [_set(RECEIPT, RECEIPT + " + " + amount)])


def _rider_sums(effects, found):
    """Read each optional rider only inside its actual successful mitigation producer."""
    for index in range(len(effects) - 1, -1, -1):
        effect = effects[index]
        expression = effect.get("set", {}).get("value")
        if effect.get("set", {}).get("field") == "_effective_damage":
            for amount, kind in (
                ("_minor_effective_damage", "_conjure_minor_type"),
                ("_curse_effective_damage", "'necrotic'"),
            ):
                if expression == "_effective_damage + " + amount:
                    if amount in found:
                        raise SystemExit("Typed receipt needs one actual producer per rider.")
                    found.add(amount)
                    effects.insert(index + 1, _sum(kind, amount))
        for key in ("on_true", "on_false"):
            if key in effect:
                _rider_sums(effect[key], found)
        if "for_each" in effect:
            _rider_sums(effect["for_each"]["do"], found)


def restore_runtime(text):
    """Restore qualified source owners before families or final packet clones are regenerated."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in OWNERS:
        tool = deepcopy(tools[name])
        parameters = [p for p in tool["parameters"] if p["name"] == CHOICE]
        if not parameters:
            continue
        if parameters != [_parameter_receipt(tool)]:
            raise SystemExit("Typed receipt's optional selector was changed by another owner.")
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != CHOICE]
        tool["effects"] = _remove_receipt(tool["effects"])
        changed[name] = tool
    return _replace_owned(text, changed) if changed else text


def bind_runtime(text):
    """Capture final typed producers before Dream/Bond/Prism and shared packet cloning."""
    source = restore_runtime(text)
    tools = _load_runtime(source)["tools"]
    changed = {}
    for name in OWNERS:
        tool = deepcopy(tools[name])
        accepted_superior_prefix = {
            "call": "resolve_srd_superior_defense",
            "params": {"target_state": "{target}"},
            "export_context": {"_superior_defense_active": "_superior_defense_active"},
        }
        effects = tool["effects"]
        if effects[:1] == [accepted_superior_prefix]:
            effects = effects[1:]
        if any(
            not isinstance(effect, dict)
            or any(key in effect for key in ("call", "params", "export_context"))
            for effect in effects[:1]
        ):
            raise SystemExit("Typed receipt must bind the expanded final qualified owner.")
        if any(p["name"] == "component_3_damage_type" for p in tool["parameters"]):
            raise SystemExit("Typed receipt needs the actual third-component producer first.")
        tool["parameters"].append(_parameter_receipt(tool))
        branch = next(
            effect
            for effect in tool["effects"]
            if "observe_base_damage == true" in effect.get("condition", "")
        )
        if branch["on_true"][0] != _set("_source_base_effective_damage", "_effective_damage"):
            raise SystemExit("Typed receipt lost its actual pre-rider base receipt boundary.")
        branch["condition"] += SUFFIX
        if len(branch["condition"]) > 500:
            raise SystemExit("Typed receipt cannot exceed the unchanged expression bound.")
        if "composite" in name:
            sums = [
                _sum("component_1_damage_type", "_component_1_effective_damage"),
                _sum("component_2_damage_type", "_component_2_effective_damage"),
            ]
        else:
            sums = [_sum("damage_type", "_source_base_effective_damage")]
        found = set()
        _rider_sums(branch["on_true"], found)
        if found != {"_minor_effective_damage", "_curse_effective_damage"}:
            raise SystemExit("Typed receipt requires the current actual Minor and Curse producers.")
        branch["on_true"][1:1] = sums
        # A child call can inherit earlier private aliases. Every invocation starts at real zero.
        tool["effects"].insert(0, _set(RECEIPT, 0))
        changed[name] = tool
    return _replace_owned(source, changed)
