"""A Mansion banquet retains printed serving quantities on its canonical food object."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _tool_scene
    from .srd_spell_object_content import _lookup_object, _metadata_patch
    from .srd_spell_supplies import _touch
    from .srd_spell_travel_dwelling import _lookup
    from .srd_spell_travel_furnishings import furnishing_intent, furnishing_tools
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _public, _push
else:
    from srd_scene_relations import _tool_scene
    from srd_spell_object_content import _lookup_object, _metadata_patch
    from srd_spell_supplies import _touch
    from srd_spell_travel_dwelling import _lookup
    from srd_spell_travel_furnishings import furnishing_intent, furnishing_tools
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _public, _push

PUBLIC_TOOLS = {"eat_srd_mansion_banquet"}


def banquet_intent(name="banquet"):
    """One optional physical food body represents the source's fixed hundred servings."""
    intent = furnishing_intent(name)
    intent["max_items"] = 1
    return intent


def banquet_tools():
    """Reuse the exact canonical creation body with source-owned food qualification."""
    if __package__:
        from .srd_spell_travel import ACTIVE
    else:
        from srd_spell_travel import ACTIVE

    create = deepcopy(furnishing_tools()["create_srd_mansion_furnishing"])
    create["description"] = (
        "Create the exact paid Mansion's nine-course, hundred-serving food body."
    )
    for effect in create["effects"]:
        change = effect.get("set", {})
        if change.get("field") == "_mf_saved_intents":
            change["value"] = "_xd_source.banquet"
        if change.get("field") == "_world_created_metadata" and isinstance(
            change.get("value"), dict
        ):
            metadata = change["value"]
            metadata["kind"] = "'food'"
            metadata["mansion_creation"]["kind"] = "'banquet'"
            metadata["srd_mansion_banquet"] = {
                "source_id": "source_id",
                "cast_id": "_xd_source.cast_id",
                "created_tick": "_xd_source.created_tick",
                "room_id": "_xd_source.room_id",
                "remaining_servings": 100,
                "courses": 9,
            }

    eat = _public(
        _tool_scene(
            "Consume one actual nine-course Mansion serving; "
            "no healing or daily nutrition is inferred.",
            [_entity("actor_state"), _parameter("object_id")],
            [
                _require(ACTIVE, "A capable living recipient must consume the actual meal."),
                _require(
                    "combat.encounter_active != true",
                    "Combat meal timing remains selected; consume this banquet outside combat.",
                ),
                _require(
                    "state.pending_srd_action_stack == null or "
                    "len(state.pending_srd_action_stack) == 0",
                    "Complete the real saved window before consuming the banquet.",
                ),
                *_lookup_object(),
                _require(
                    "_object_scene.metadata.srd_mansion_banquet != null and "
                    "_object_scene.metadata.mansion_creation != null",
                    "Choose an actual source-created Mansion banquet.",
                ),
                _set("_mb_food", "_object_scene.metadata.srd_mansion_banquet"),
                *_lookup("_mb_food.source_id"),
                _require(
                    "_xd_source.spell == 'magnificent_mansion' and _xd_source.active == true and "
                    "state.lasting_tick < _xd_source.expires_tick",
                    "The actual Mansion food source must still be active.",
                ),
                _require(
                    "_mb_food.cast_id == _xd_source.cast_id and _mb_food.created_tick == "
                    "_xd_source.created_tick and _mb_food.room_id == _xd_source.room_id and "
                    "_object_scene.metadata.plane == _xd_source.room_id",
                    "Keep the exact created food inside its own source room.",
                ),
                _set("_mb_owned", False),
                _set("_mb_intents", "_xd_source.banquet"),
                _loop(
                    "_mb_intents",
                    "_mb_intent",
                    [_when("_mb_intent.id == object_id", [_set("_mb_owned", True)])],
                ),
                _require(
                    "_mb_owned == true and "
                    "_object_scene.metadata.mansion_creation.kind == 'banquet'",
                    "The paid source must own this exact food identity.",
                ),
                _require(
                    "_mb_food.courses == 9 and _mb_food.remaining_servings > 0 and "
                    "_mb_food.remaining_servings <= 100",
                    "The printed banquet has no remaining nine-course serving.",
                ),
                _call(
                    "check_srd_dwelling_magic",
                    {"subject": "{object_id}", "slot_level": "{_xd_source.slot_level}"},
                ),
                *_touch("{_actor_state_id}"),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_actor_state_id}"},
                    ["_info_scene_entity"],
                ),
                _require(
                    "_info_scene_entity.metadata.participant_ref == _actor_state_id and "
                    "_info_scene_entity.metadata.plane == _xd_source.room_id",
                    "The actual recipient must be inside this Mansion with its food.",
                ),
                _set(
                    "_mb_receipt",
                    {
                        "source_id": "_xd_source.id",
                        "cast_id": "_xd_source.cast_id",
                        "object_id": "object_id",
                        "recipient": "_actor_state_id",
                        "serving": "101 - _mb_food.remaining_servings",
                        "courses": 9,
                        "consumed_tick": "state.lasting_tick",
                    },
                ),
                _set("_mb_changed", {"remaining_servings": "_mb_food.remaining_servings - 1"}),
                _set("_mb_food", "merge(_mb_food, _mb_changed)"),
                _set("_mb_active", "_mb_food.remaining_servings > 0"),
                *_metadata_patch({"srd_mansion_banquet": "_mb_food"}, active="{_mb_active}"),
                _set("_mb_meals", []),
                _when(
                    "actor_state.srd_mansion_meals != null",
                    [_set("_mb_meals", "actor_state.srd_mansion_meals")],
                ),
                _push("_mb_meals", "_mb_receipt"),
                _set("actor_state.srd_mansion_meals", "_mb_meals"),
            ],
        ),
        "exploration",
        "Mansion banquet",
        [
            ("Courses", "_mb_receipt.courses"),
            ("Remaining servings", "_mb_food.remaining_servings"),
            ("Recipient", "_mb_receipt.recipient"),
        ],
    )
    return {"create_srd_mansion_banquet": create, "eat_srd_mansion_banquet": eat}
