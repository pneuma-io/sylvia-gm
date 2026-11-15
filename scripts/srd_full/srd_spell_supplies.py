"""Goodberry and Purify operate on actual supplies, with fixed source quantities and costs."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
    from .srd_spell_minor_world import _body_source
    from .srd_spell_object_content import (
        GEOMETRY_EXPORTS,
        _admission_guard,
        _held_object_geometry,
        _lookup_object,
        _metadata_patch,
        _new_scene_object,
        _now,
    )
    from .srd_spell_object_content import (
        SOURCE_PAGES as OBJECT_SOURCE_PAGES,
    )
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import (
        _loop,
        _optional,
        _push,
        _replace_tools,
        _RuntimeDumper,
    )
else:
    from srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
    from srd_spell_minor_world import _body_source
    from srd_spell_object_content import (
        GEOMETRY_EXPORTS,
        _admission_guard,
        _held_object_geometry,
        _lookup_object,
        _metadata_patch,
        _new_scene_object,
        _now,
    )
    from srd_spell_object_content import SOURCE_PAGES as OBJECT_SOURCE_PAGES
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import (
        _loop,
        _optional,
        _push,
        _replace_tools,
        _RuntimeDumper,
    )


SOURCE = {
    "goodberry": (137, "Goodberry", "Grease"),
    "purify_food_and_drink": (157, "Purify Food and Drink", "Raise Dead"),
}
SOURCE_PAGES = {
    137: "f67bc3cc27f6a0cc392a843e828134585cdb0750dae4134670427eb014bdc84b",
    157: "c9aca3115af09f23e28073ede918084add142e0b73c0cd00d344cc5113c928e4",
}
PROFILES = {
    "goodberry": {
        "family": "supply_spell",
        "base_level": 1,
        "casting_time": "action",
        "range_kind": "self",
        "range_feet": 0,
        "concentration": False,
        "berries": 10,
        "duration_rounds": 14400,
        "healing_per_berry": 1,
        "nourishment_hours": 24,
    },
    "purify_food_and_drink": {
        "family": "supply_spell",
        "base_level": 1,
        "casting_time": "action",
        "range_kind": "distance",
        "range_feet": 10,
        "concentration": False,
        "radius_feet": 5,
    },
}
PUBLIC_TOOLS = {"record_srd_food_and_drink", "eat_srd_goodberry", "transfer_srd_goodberries"}
CAST_CHOICES = [
    _optional("supply_output_id", default=""),
    _optional("supply_hand_form_id", default=""),
    _array("supply_point", default=[0, 0, 0]),
]


def validate_profiles(definitions):
    for spell, definition in definitions.items():
        if definition.get("family") == "supply_spell" and definition != PROFILES.get(spell):
            raise ValueError(f"{spell} does not match its pinned supply profile")


def _schema():
    fields = {}
    for profile in PROFILES.values():
        for name, value in profile.items():
            if name != "family":
                fields[name] = {
                    "type": "boolean"
                    if type(value) is bool
                    else "integer"
                    if type(value) is int
                    else "string"
                }
    return {
        "type": "object",
        "properties": fields,
        "required": sorted(set.intersection(*(set(p) for p in PROFILES.values())) - {"family"}),
        "additionalProperties": False,
    }


def _batch_guard():
    return [
        *_lookup_object(),
        *_now(),
        _require(
            "_object_scene.metadata.srd_goodberry_batch != null", "Choose actual Goodberries."
        ),
        _set("_supply_batch", "_object_scene.metadata.srd_goodberry_batch"),
        _require(
            "_supply_batch.spell == 'goodberry' and _supply_batch.spell_level >= 1 and "
            f"_supply_batch.source_sha256 == {SOURCE_PAGES[137]!r} and "
            "_supply_batch.remaining > 0 and _supply_batch.remaining <= 10 and "
            "_object_now < _supply_batch.expires_tick",
            "The actual Goodberry source has ended or its berries were eaten.",
        ),
    ]


def _touch(actor):
    return [
        _call(
            "resolve_srd_object_content_geometry",
            {
                "caster_state": actor,
                "object_id": "{object_id}",
                "range_feet": 5,
                "requires_sight": False,
            },
            GEOMETRY_EXPORTS,
        )
    ]


def _fact_tool():
    return _tool_scene(
        "Admit actual food, drink, poison and rot once; later classification cannot rewrite them.",
        [
            _parameter("object_id"),
            _parameter("food", "boolean"),
            _parameter("drink", "boolean"),
            _parameter("nonmagical", "boolean"),
            _parameter("poisoned", "boolean"),
            _parameter("rotten", "boolean"),
        ],
        [
            *_admission_guard(),
            _require("food == true or drink == true", "The object must actually be food or drink."),
            _require(
                "_object_scene.metadata.srd_food_and_drink == null",
                "This supply is already classified; re-preparation cannot remove contamination.",
            ),
            _require(
                "nonmagical != true or (_object_scene.metadata.srd_goodberry_batch == null and "
                "_object_scene.metadata.srd_created_object == null and "
                "_object_scene.metadata.magic_item != true)",
                "An active magical source cannot be reclassified as nonmagical food.",
            ),
            *_metadata_patch(
                {
                    "srd_food_and_drink": {
                        "food": "food",
                        "drink": "drink",
                        "nonmagical": "nonmagical",
                        "poisoned": "poisoned",
                        "rotten": "rotten",
                    }
                }
            ),
        ],
    )


def _consume_tools():
    eat = _tool_scene(
        "Eat an unexpired Goodberry as a Bonus Action for one HP and a day's nourishment.",
        [_entity("actor_state"), _parameter("object_id")],
        [
            *_batch_guard(),
            *_touch("{_actor_state_id}"),
            _require(
                "_object_scene.metadata.carried_by == null or "
                "_object_scene.metadata.carried_by == _actor_state_id",
                "Transfer the actual berry batch before another creature eats from it.",
            ),
            _require("actor_state.is_alive == true", "A living creature must eat the berry."),
            _when(
                "combat.encounter_active == true",
                [
                    _require("combat.pending_action == null", "Finish the pending action first."),
                    _call(
                        "consume_srd_bonus_action",
                        {
                            "actor_state": "{_actor_state_id}",
                            "bonus_action": "goodberry",
                        },
                    ),
                ],
            ),
            _call(
                "restore_hit_points",
                {"target": "{_actor_state_id}", "amount": 1},
                ["_hp_before", "_hp_after"],
            ),
            _set("_supply_change", {"remaining": "_supply_batch.remaining - 1"}),
            _set("_supply_batch", "merge(_supply_batch, _supply_change)"),
            _set("_supply_active", "_supply_batch.remaining > 0"),
            *_metadata_patch({"srd_goodberry_batch": "_supply_batch"}, active="{_supply_active}"),
            _set(
                "actor_state.srd_goodberry_nourishment",
                {
                    "source_id": "_supply_batch.source_id",
                    "source_sha256": repr(SOURCE_PAGES[137]),
                    "consumed_tick": "_object_now",
                    "through_tick": "_object_now + 28800",
                },
            ),
        ],
    )
    eat["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "compact",
        "title": "Goodberry",
        "summary": [
            {"label": "Berries remaining", "value": {"path": "context._supply_batch.remaining"}},
            {"label": "HP before", "value": {"path": "context._hp_before"}},
            {"label": "HP after", "value": {"path": "context._hp_after"}},
        ],
        "details": [{"label": "Nourishment", "value": "One day"}],
    }
    transfer = _tool_scene(
        "Give the remaining Goodberry batch to a nearby creature, preserving its actual source.",
        [_entity("actor_state"), _entity("recipient_state"), _parameter("object_id")],
        [
            *_batch_guard(),
            *_touch("{_actor_state_id}"),
            _require(
                "_object_scene.metadata.carried_by == _actor_state_id",
                "The giver must actually carry this berry batch.",
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_actor_state_id}"}, POSITION_EXPORTS
            ),
            _set("_supply_giver", "_info_scene_entity"),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_recipient_state_id}"},
                POSITION_EXPORTS,
            ),
            _set("_supply_recipient", "_info_scene_entity"),
            _require(
                "_supply_recipient.metadata.plane == _supply_giver.metadata.plane",
                "The recipient must share the actual plane.",
            ),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_supply_giver.position}",
                    "end": "{_supply_recipient.position}",
                },
                ["_scene_distance"],
            ),
            _require("_scene_distance <= 5", "The recipient must be within reach."),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_supply_giver.position}",
                    "end": "{_supply_recipient.position}",
                    "rule": "solid",
                },
                ["_information_blocked"],
            ),
            _require(
                "_information_blocked != true",
                "Solid obstruction prevents handing over the berries.",
            ),
            *_metadata_patch(
                {
                    "carried_by": "_recipient_state_id",
                    "worn_by": None,
                    "plane": "_supply_recipient.metadata.plane",
                },
                position="{_supply_recipient.position}",
            ),
        ],
    )
    return {"eat_srd_goodberry": eat, "transfer_srd_goodberries": transfer}


def _goodberry():
    """Use admitted depiction fitting the caster's hand; the source gives no berry dimensions."""
    return [
        _require(
            "supply_output_id != '' and supply_hand_form_id != ''",
            "Choose a unique berry-batch ID and an admitted form fitting this caster's hand.",
        ),
        *_body_source(),
        _set("_supply_forms", []),
        _when(
            "caster_state.srd_hand_sized_forms != null",
            [_set("_supply_forms", "caster_state.srd_hand_sized_forms")],
        ),
        _set("_supply_form", None),
        _loop(
            "_supply_forms",
            "_supply_candidate",
            [
                _when(
                    "_supply_candidate.id == supply_hand_form_id and "
                    "_supply_candidate.caster == _caster_state_id and "
                    "_supply_candidate.body_source == _minor_body_source and "
                    "_supply_candidate.kind == 'trinket'",
                    [_set("_supply_form", "_supply_candidate")],
                ),
            ],
        ),
        _require(
            "_supply_form != null", "The depicted ten berries must fit this actual caster's hand."
        ),
        _set("_supply_serial", 1),
        _when(
            "caster_state.srd_goodberry_serial != null",
            [_set("_supply_serial", "caster_state.srd_goodberry_serial + 1")],
        ),
        _set("caster_state.srd_goodberry_serial", "_supply_serial"),
        _call(
            "format_srd_information_projection",
            {"message": "{_caster_state_id}:goodberry:{_supply_serial}"},
            ["_info_projection_message"],
        ),
        _set("_supply_source_id", "_info_projection_message"),
        _call("srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS),
        _set("_world_origin", "_info_scene_entity.position"),
        _set("_world_plane", "_info_scene_entity.metadata.plane"),
        _set(
            "_supply_batch",
            {
                "spell": "'goodberry'",
                "spell_level": "slot_level",
                "source_id": "_supply_source_id",
                "source_sha256": repr(SOURCE_PAGES[137]),
                "caster": "_caster_state_id",
                "started_tick": "_object_now",
                "expires_tick": "_object_now + 28800",
                "remaining": 10,
                "dispel_ongoing": True,
            },
        ),
        *_new_scene_object(
            {
                "plane": "_world_plane",
                "lighting": "_info_scene_entity.metadata.lighting",
                "carried_by": "_caster_state_id",
                "fiction": "'Ten magical Goodberries'",
                "srd_goodberry_batch": "_supply_batch",
            },
            identity="supply_output_id",
            extent="_supply_form.extent",
        ),
        _push("_supply_summary", {"label": "Goodberries", "value": "'10; 24 hours'"}),
    ]


def _purify_one_tool():
    return _tool_scene(
        "Remove poison and rot from actual nonmagical food/drink within the source Sphere.",
        [_entity("caster_state"), _parameter("object_id"), _array("origin")],
        [
            _set("_supply_purified", False),
            _call("refresh_srd_object_content_one", {"object_id": "{object_id}"}),
            *_lookup_object(),
            *_held_object_geometry("{_caster_state_id}"),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_supply_caster_scene", "_info_scene_entity"),
            _set("_supply_food", None),
            _when(
                "_object_scene.metadata.srd_food_and_drink != null",
                [_set("_supply_food", "_object_scene.metadata.srd_food_and_drink")],
            ),
            _when(
                "_object_scene.metadata.srd_created_supply != null and _supply_food == null",
                [
                    _set("_supply_generated", "_object_scene.metadata.srd_created_supply"),
                    _require(
                        "_supply_generated.spell == 'create_food_and_water' and "
                        f"_supply_generated.source_sha256 == {OBJECT_SOURCE_PAGES[120]!r}",
                        "The created food needs its actual source.",
                    ),
                    _set(
                        "_supply_food",
                        {
                            "nonmagical": True,
                            "poisoned": False,
                            "rotten": "_supply_generated.spoiled_food_pounds > 0",
                        },
                    ),
                ],
            ),
            _when(
                "_supply_food != null",
                [
                    _when(
                        "_supply_food.nonmagical == true and _object_scene.metadata.plane == "
                        "_supply_caster_scene.metadata.plane",
                        [
                            _require(
                                "_object_scene.footprint.kind == 'box'",
                                "Purify currently needs admitted box food sections.",
                            ),
                            *[
                                _set(
                                    f"_supply_near_{axis}",
                                    "max(0, abs("
                                    f"_object_scene.position[{axis}] - origin[{axis}]) - "
                                    f"_object_scene.footprint.half_extents[{axis}])",
                                )
                                for axis in range(2)
                            ],
                            _set(
                                "_supply_near_2",
                                "max(0, _object_scene.position[2] - origin[2], "
                                "origin[2] - _object_scene.position[2] - _object_scene.height)",
                            ),
                            _set(
                                "_supply_near_distance",
                                "(_supply_near_0 ** 2 + _supply_near_1 ** 2 + "
                                "_supply_near_2 ** 2) ** 0.5",
                            ),
                            *[
                                _set(
                                    f"_supply_far_{axis}",
                                    f"abs(_object_scene.position[{axis}] - origin[{axis}]) + "
                                    f"_object_scene.footprint.half_extents[{axis}]",
                                )
                                for axis in range(2)
                            ],
                            _set(
                                "_supply_far_2",
                                "max(abs(_object_scene.position[2] - origin[2]), "
                                "abs(_object_scene.position[2] + "
                                "_object_scene.height - origin[2]))",
                            ),
                            _set(
                                "_supply_far_distance",
                                "(_supply_far_0 ** 2 + _supply_far_1 ** 2 + "
                                "_supply_far_2 ** 2) ** 0.5",
                            ),
                            _when(
                                "_supply_near_distance <= 5",
                                [
                                    _require(
                                        "_supply_far_distance <= 5",
                                        "Split partially affected food into admitted sections.",
                                    ),
                                    _call(
                                        "srd_information_barriers",
                                        {
                                            "start": "{origin}",
                                            "end": "{_object_scene.position}",
                                            "rule": "solid",
                                        },
                                        ["_information_blocked"],
                                    ),
                                    _require(
                                        "_information_blocked != true",
                                        "Total Cover blocks purification.",
                                    ),
                                    _set("_supply_clean", {"poisoned": False, "rotten": False}),
                                    _set("_supply_food", "merge(_supply_food, _supply_clean)"),
                                    *_metadata_patch({"srd_food_and_drink": "_supply_food"}),
                                    _when(
                                        "_object_scene.metadata.srd_created_supply != null",
                                        [
                                            _call(
                                                "purify_srd_created_supply_rot",
                                                {"object_id": "{object_id}"},
                                            ),
                                        ],
                                    ),
                                    _set("_supply_purified", True),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _purify():
    return [
        _call(
            "resolve_srd_object_content_point",
            {
                "caster_state": "{_caster_state_id}",
                "point": "{supply_point}",
                "range_feet": 10,
            },
            ["_world_origin", "_world_plane", "_world_lighting"],
        ),
        _set("_supply_rows", "state.spatial_scene.entities"),
        _set("_supply_purified_ids", []),
        _loop(
            "_supply_rows",
            "_supply_row",
            [
                _when(
                    "_supply_row.active == true and "
                    "_supply_row.metadata.participant_ref == null and "
                    "(_supply_row.metadata.srd_food_and_drink != null or "
                    "_supply_row.metadata.srd_created_supply != null)",
                    [
                        _set("_supply_purified", False),
                        _call(
                            "purify_srd_food_object",
                            {
                                "caster_state": "{_caster_state_id}",
                                "object_id": "{_supply_row.id}",
                                "origin": "{supply_point}",
                            },
                            ["_supply_purified"],
                        ),
                        _when(
                            "_supply_purified == true",
                            [_push("_supply_purified_ids", "_supply_row.id")],
                        ),
                    ],
                ),
            ],
        ),
        _push("_supply_summary", {"label": "Purified supplies", "value": "_supply_purified_ids"}),
    ]


def _casting_tool():
    return _tool_scene(
        "Resolve fixed supply effects only after the source's actual shared casting costs.",
        [
            _entity("caster_state"),
            _parameter("spell", "enum", enum_values=sorted(PROFILES)),
            _parameter("slot_level", "integer", minimum=1, maximum=9),
            *deepcopy(CAST_CHOICES),
        ],
        [
            *_now(),
            _set("_supply_summary", []),
            _set("_supply_details", []),
            _when("spell == 'goodberry'", _goodberry()),
            _when("spell == 'purify_food_and_drink'", _purify()),
        ],
    )


def _lifecycle_tool():
    return _tool_scene(
        "Make only actual uneaten Goodberries disappear at their saved source deadline.",
        [],
        [
            *_now(),
            _set("_supply_rows", "state.spatial_scene.entities"),
            _loop(
                "_supply_rows",
                "_supply_row",
                [
                    _when(
                        "_supply_row.active == true and "
                        "_supply_row.metadata.srd_goodberry_batch != null",
                        [
                            _set("_supply_batch", "_supply_row.metadata.srd_goodberry_batch"),
                            _require(
                                "_supply_batch.spell == 'goodberry' and "
                                f"_supply_batch.source_sha256 == {SOURCE_PAGES[137]!r}",
                                "The berry lifetime needs its exact source.",
                            ),
                            _when(
                                "_object_now >= _supply_batch.expires_tick",
                                [
                                    _call(
                                        "expire_srd_goodberry_object",
                                        {"object_id": "{_supply_row.id}"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _effect_lists(effects):
    """Locate family dispatches under shared loss/reaction guards, preserving one branch."""
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _effect_lists(effect[key])


def _source_tools():
    """Expose only the duration-bearing berry source for the shared exact-source removal owner."""
    read = _tool_scene(
        "Read one actual active Goodberry source; Instantaneous purification is excluded.",
        [_parameter("object_id"), _parameter("source_id")],
        [
            *_batch_guard(),
            _require(
                "_supply_batch.source_id == source_id and _supply_batch.dispel_ongoing == true",
                "Select this object's exact ongoing Goodberry source.",
            ),
            _set("_supply_source", "_supply_batch"),
        ],
    )
    retire = _tool_scene(
        "Retire selected uneaten berries; preserve consumed healing and nourishment.",
        [_parameter("object_id"), _parameter("source_id")],
        [
            _call(
                "read_srd_supply_source",
                {"object_id": "{object_id}", "source_id": "{source_id}"},
                ["_supply_source"],
            ),
            *_lookup_object(),
            *_metadata_patch({}, active=False),
        ],
    )
    return {"read_srd_supply_source": read, "retire_srd_supply_source": retire}


def extend_runtime(text):
    """Compose after Object and Minor; keep shared casting, healing and action owners intact."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "record_srd_food_and_drink": _fact_tool(),
        **_consume_tools(),
        **_source_tools(),
        "cast_spell_supply_spell": _casting_tool(),
        "purify_srd_food_object": _purify_one_tool(),
        "refresh_srd_goodberries": _lifecycle_tool(),
        "purify_srd_created_supply_rot": _tool_scene(
            "Remove only actual rot from the existing fixed created food supply.",
            [_parameter("object_id")],
            [
                *_lookup_object(),
                *_now(),
                _set("_supply_generated", "_object_scene.metadata.srd_created_supply"),
                _require(
                    "_supply_generated != null and _supply_generated.spell == "
                    "'create_food_and_water' and "
                    f"_supply_generated.source_sha256 == {OBJECT_SOURCE_PAGES[120]!r}",
                    "Use the actual created food source.",
                ),
                _set("_supply_spoiled", 0),
                _when(
                    "_supply_generated.spoiled_food_pounds != null",
                    [_set("_supply_spoiled", "_supply_generated.spoiled_food_pounds")],
                ),
                _set(
                    "_supply_food_change",
                    {
                        "food_pounds": "_supply_generated.food_pounds + _supply_spoiled",
                        "spoiled_food_pounds": 0,
                        "food_purified_tick": "_object_now",
                    },
                ),
                _set("_supply_generated", "merge(_supply_generated, _supply_food_change)"),
                *_metadata_patch({"srd_created_supply": "_supply_generated"}),
            ],
        ),
        "expire_srd_goodberry_object": _tool_scene(
            "Expire one exact berry batch.",
            [_parameter("object_id")],
            [*_lookup_object(), *_metadata_patch({}, active=False)],
        ),
    }
    for name in PUBLIC_TOOLS:
        tool = changed[name]
        changed[name + "_node"] = deepcopy(tool)
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        tool["effects"] = [
            _call(
                name + "_node",
                {
                    **{
                        p["name"]: "{"
                        + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
                        + "}"
                        for p in tool["parameters"]
                    },
                    "lasting_state": "state",
                },
                ["_supply_batch", "_hp_before", "_hp_after"] if name == "eat_srd_goodberry" else [],
            )
        ]
    cast = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in names]
    for effects in list(_effect_lists(cast["effects"])):
        effects[:] = [
            e
            for e in effects
            if "_spell_definition.family == 'supply_spell'" not in e.get("condition", "")
        ]
    resolution = next(
        e
        for e in _effect_lists(cast["effects"])
        if any(
            "_spell_definition.family == 'information_spell'" in x.get("condition", "") for x in e
        )
    )
    branch = _when(
        "_spell_cast_failed != true and _spell_definition.family == 'supply_spell'",
        [
            _call(
                "cast_spell_supply_spell",
                {
                    "caster_state": "{_caster_state_id}",
                    "spell": "{spell}",
                    "slot_level": "{_slot_level}",
                    **{p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES},
                },
            ),
        ],
    )
    branch["on_true"][0]["export_context"] = {
        "_spell_summary_rows": "_supply_summary",
        "_spell_detail_rows": "_supply_details",
    }
    resolution.append(branch)
    changed["cast_spell"] = cast
    process = deepcopy(tools["process_srd_turn_effects"])
    process["effects"] = [
        e for e in process["effects"] if e.get("call") != "refresh_srd_goodberries"
    ]
    process["effects"].insert(0, _call("refresh_srd_goodberries", {}))
    changed["process_srd_turn_effects"] = process
    food_refresh = deepcopy(tools["refresh_srd_object_content_one"])
    for effects in _effect_lists(food_refresh["effects"]):
        for effect in effects:
            condition = effect.get("condition", "")
            if (
                "_world_supply.food_spoils_tick" in condition
                and "food_purified_tick" not in condition
            ):
                effect["condition"] = (
                    condition + " and (_world_supply.food_purified_tick == null or "
                    "_world_supply.food_purified_tick < _world_supply.food_spoils_tick)"
                )
    changed["refresh_srd_object_content_one"] = food_refresh
    text = _replace_tools(text, changed)
    if "      supply_spell:" not in text:
        schema = yaml.dump({"supply_spell": _schema()}, Dumper=_RuntimeDumper, sort_keys=False)
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      timed_senses:",
            1,
        )
    return text
