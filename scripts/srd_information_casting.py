"""Self information effects derive their actual caster before required target binding."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _emit
    from .srd_timed_actor_defenses import _load_runtime, _set, _when
else:
    from srd_actor_spell_modifiers import _emit
    from srd_timed_actor_defenses import _load_runtime, _set, _when


def bind_runtime_consumers(text):
    """Preserve final public guards and derived targets for every non-Self information cast."""
    tools = _load_runtime(text)["tools"]
    changed = {}

    def bind(effects):
        result = []
        for original in effects:
            if original.get("set", {}).get("field") == "_information_self_target_id":
                continue
            if original.get("condition") == "_spell_definition.range_kind == 'self'" and (
                "_information_self_target_id" in str(original)
            ):
                continue
            effect = deepcopy(original)
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    effect[branch] = bind(effect[branch])
            if "for_each" in effect:
                effect["for_each"]["do"] = bind(effect["for_each"]["do"])
            if effect.get("call") == "cast_spell_information_spell" and effect["params"].get(
                "target_state"
            ) in {"{_scalar_target_id}", "{_information_self_target_id}"}:
                result.extend(
                    [
                        _set("_information_self_target_id", "_scalar_target_id"),
                        _when(
                            "_spell_definition.range_kind == 'self'",
                            [_set("_information_self_target_id", "_caster_state_id")],
                        ),
                    ]
                )
                effect["params"]["target_state"] = "{_information_self_target_id}"
            result.append(effect)
        return result

    for name, original in tools.items():
        if "cast_spell_information_spell" not in str(original["effects"]):
            continue
        tool = deepcopy(original)
        tool["effects"] = bind(tool["effects"])
        if tool != original:
            changed[name] = tool
    return _emit(text, changed)
