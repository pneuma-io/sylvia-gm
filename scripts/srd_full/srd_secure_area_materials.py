"""Forbiddance's actual thirtieth cast consumes its existing reviewed material supply."""

from copy import deepcopy

if __package__:
    from .srd_spell_barriers import _effect_lists
    from .srd_spell_magic_zones import _when
    from .srd_timed_actor_defenses import _call, _load_runtime, _set
    from .srd_timed_senses import _optional, _replace_tools
else:
    from srd_spell_barriers import _effect_lists
    from srd_spell_magic_zones import _when
    from srd_timed_actor_defenses import _call, _load_runtime, _set
    from srd_timed_senses import _optional, _replace_tools


PREPARATION = "spell == 'forbiddance' and _spell_definition.family == 'spell_secure_area'"

FINAL_CONSUMPTION = (
    "(_part.consumed or (spell == 'forbiddance' and secure_area_final_consumption == true))"
)


def extend_runtime(text):
    """Add the private quantity/card/commit predicate before extended-casting validation capture."""
    material = deepcopy(_load_runtime(text)["tools"]["resolve_srd_spell_materials"])
    if not any(p["name"] == "secure_area_final_consumption" for p in material["parameters"]):
        material["parameters"].append(
            _optional(
                "secure_area_final_consumption",
                "boolean",
                False,
                model_hidden=True,
            )
        )
    found = 0
    for effects in _effect_lists(material["effects"]):
        for effect in effects:
            original = effect.get("condition")
            if original in ("_part.consumed", FINAL_CONSUMPTION):
                effect["condition"] = FINAL_CONSUMPTION
                found += 1
            elif original in (
                "_part.consumed and validate_only != true",
                FINAL_CONSUMPTION + " and validate_only != true",
            ):
                effect["condition"] = FINAL_CONSUMPTION + " and validate_only != true"
                found += 1
    if found != 2:
        raise ValueError("Forbiddance requires both actual material quantity and commit owners.")
    return _replace_tools(text, {"resolve_srd_spell_materials": material})


def _actual_slot_effects(cast):
    """Reuse the owner's real resource/NPC/feature/Ritual level qualification before materials."""
    result = [_set("_secure_material_slot", 0)]
    for effect in cast["effects"]:
        if (
            effect.get("condition")
            and effect.get("on_true")
            and all(e.get("set", {}).get("field") == "_slot_level" for e in effect["on_true"])
        ):
            copied = deepcopy(effect)
            for child in copied["on_true"]:
                child["set"]["field"] = "_secure_material_slot"
            result.append(copied)
    if len(result) < 10:
        raise ValueError("Use the actual final cast's selected slot qualification.")
    return result


def bind_runtime(text):
    """Derive final consumption after Source/extended owners and before reaction prefix cloning."""
    tools = _load_runtime(text)["tools"]
    cast = deepcopy(tools["cast_spell"])
    cast["effects"] = [
        e
        for e in cast["effects"]
        if not (
            e.get("set", {}).get("field") == "_secure_materials_consumed"
            or "prepare_srd_secure_area_cast" in str(e)
            and e.get("condition") == PREPARATION
        )
    ]
    index = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "resolve_srd_spell_materials"
    )
    cast["effects"][index]["params"]["secure_area_final_consumption"] = (
        "{_secure_materials_consumed}"
    )
    cast["effects"][index:index] = [
        _set("_secure_materials_consumed", False),
        _when(
            PREPARATION,
            [
                *_actual_slot_effects(cast),
                _call(
                    "prepare_srd_secure_area_cast",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "slot_level": "{_secure_material_slot}",
                        "save_dc": "{_casting_profile.save_dc}",
                        "origin": "{secure_area_origin}",
                        "chosen_types": "{secure_area_types}",
                        "security": "{secure_area_security}",
                        "reverse": "{secure_area_reverse}",
                        "side": "{secure_area_side}",
                        "floor_width": "{secure_area_floor_width}",
                        "floor_length": "{secure_area_floor_length}",
                        "password": "{secure_area_password}",
                        "damage_type": "{selected_damage_type}",
                        "caster_can_see_origin": "{caster_can_see_origin}",
                        "caster_can_touch_origin": "{caster_can_touch_target}",
                    },
                    ("_secure_materials_consumed",),
                ),
            ],
        ),
    ]
    return _replace_tools(text, {"cast_spell": cast})
