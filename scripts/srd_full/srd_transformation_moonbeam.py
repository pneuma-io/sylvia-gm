"""Bind the actual failed-Moonbeam receipt to later transformation producers."""

from copy import deepcopy

if __package__:
    from . import srd_actor_spell_modifiers as actor
else:
    import srd_actor_spell_modifiers as actor


RECEIPT_GUARD = (
    "target_state.moonbeam_shapechange_sources != null and "
    "len(target_state.moonbeam_shapechange_sources) > 0"
)
CHECK = "check_srd_moonbeam_shapechange"


def _check():
    return actor._call(
        CHECK, {"target_state": "{_target_state_id}"}, ["_moonbeam_shapechange_allowed"]
    )


def _require_change():
    return actor._require(
        "_moonbeam_shapechange_allowed == true",
        "The actual failed Moonbeam save prevents shape-shifting until this creature "
        "leaves its Cylinder.",
    )


def _effect_lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false", "effects"):
            if isinstance(effect.get(key), list):
                yield from _effect_lists(effect[key])
        if "for_each" in effect:
            yield from _effect_lists(effect["for_each"]["do"])


def bind_runtime_consumers(text):
    """Compose after Environment; costs and genuine form retirement stay with their owners."""
    tools = actor.defense._load_runtime(text)["tools"]
    if CHECK not in tools:
        return text
    changed = {}
    target = deepcopy(tools["resolve_srd_transformation_target_cast"])
    if CHECK not in str(target["effects"]):
        effects = target["effects"]
        start = next(
            i
            for i, effect in enumerate(effects)
            if effect.get("set", {}).get("field") == "_transformation_save_outcome"
        )
        effects[start + 1 : start + 1] = [
            actor._when(
                RECEIPT_GUARD,
                [
                    _check(),
                    actor._when(
                        "_moonbeam_shapechange_allowed != true",
                        [actor._set("_transformation_save_outcome", "'no_effect'")],
                    ),
                ],
            )
        ]
        save = effects[start + 2]
        save["condition"] = (
            "_transformation_save_outcome == 'failure' and (" + save["condition"] + ")"
        )
        changed["resolve_srd_transformation_target_cast"] = target

    installer = deepcopy(tools["install_srd_transformation_source"])
    if CHECK not in str(installer["effects"]):
        installer["effects"].insert(0, actor._when(RECEIPT_GUARD, [_check(), _require_change()]))
        changed["install_srd_transformation_source"] = installer

    change = deepcopy(tools["change_srd_transformation_source"])
    if CHECK not in str(change["effects"]):
        # Dismissal and return to the original body do not create another changed body.
        index = next(
            i
            for i, effect in enumerate(change["effects"])
            if effect.get("condition", "").startswith("_changing_form.spell_id == 'animal_shapes'")
        )
        changing_body = (
            "_changing_form.spell_id != 'gaseous_form' and "
            "(_changing_form.spell_id != 'wind_walk' or _changing_form.mode == 'normal')"
        )
        change["effects"].insert(
            index,
            actor._when(
                "(" + RECEIPT_GUARD + ") and (" + changing_body + ")",
                [_check(), _require_change()],
            ),
        )
        changed["change_srd_transformation_source"] = change

    cast = deepcopy(tools["cast_spell_transformation"])
    if "No effect: the actual failed Moonbeam save" not in str(cast["effects"]):
        for effects in list(_effect_lists(cast["effects"])):
            for i, effect in enumerate(effects):
                if effect.get("call") != "record_srd_spell_target_result":
                    continue
                blocked = deepcopy(effect)
                blocked["params"]["detail"] = (
                    "No effect: the actual failed Moonbeam save prevents this creature "
                    "from shape-shifting until it leaves the Cylinder."
                )
                effects[i] = actor._when(
                    "_transformation_save_outcome == 'no_effect'", [blocked], [effect]
                )
                changed["cast_spell_transformation"] = cast
                break
    return actor._emit(text, changed) if changed else text
