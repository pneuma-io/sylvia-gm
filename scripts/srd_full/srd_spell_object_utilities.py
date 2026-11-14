"""Source-owned utility forces and marked objects keep one admitted scene identity."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
    from .srd_spell_object_content import (
        GEOMETRY_EXPORTS,
        _admission_guard,
        _held_object_geometry,
        _lookup_object,
        _metadata_patch,
        _now,
    )
    from .srd_spell_supplies import _effect_lists
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _push, _replace_tools, _RuntimeDumper
else:
    from srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
    from srd_spell_object_content import (
        GEOMETRY_EXPORTS,
        _admission_guard,
        _held_object_geometry,
        _lookup_object,
        _metadata_patch,
        _now,
    )
    from srd_spell_supplies import _effect_lists
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _push, _replace_tools, _RuntimeDumper


SOURCE = {
    "floating_disk": (133, "Floating Disk", "Fly"),
    "instant_summons": (143, "Instant Summons", "Irresistible Dance"),
    "mage_hand": (145, "Mage Hand", "Magic Circle"),
}
SOURCE_PAGES = {
    133: "04aec40693154b2a5868994a9c39e10c9c967c27a34fabd5fa0978a3fa26ab2f",
    143: "dba4cdf16b912f655bd62da857b6d2033969bb1abe9aa0e604f3c60e69ec286d",
    145: "20b07bfd30f423de8013c7b04911548623949efdb18428834711f23dcf6ce81c",
}
PROFILES = {
    "floating_disk": {
        "family": "object_utility_spell",
        "base_level": 1,
        "casting_time": "action",
        "range_kind": "distance",
        "range_feet": 30,
        "concentration": False,
        "duration_rounds": 600,
        "diameter_feet": 3,
        "thickness_inches": 1,
        "ground_clearance_feet": 3,
        "capacity_pounds": 500,
        "follow_distance_feet": 20,
        "maximum_distance_feet": 100,
        "maximum_elevation_change_feet": 10,
    },
    "instant_summons": {
        "family": "object_utility_spell",
        "base_level": 6,
        "casting_time": "1_minute",
        "range_kind": "touch",
        "range_feet": 5,
        "concentration": False,
        "maximum_weight_pounds": 10,
        "maximum_dimension_feet": 6,
        "sapphire_minimum_value_cp": 100000,
    },
    "mage_hand": {
        "family": "object_utility_spell",
        "base_level": 0,
        "casting_time": "action",
        "range_kind": "distance",
        "range_feet": 30,
        "concentration": False,
        "duration_rounds": 10,
        "capacity_pounds": 10,
        "movement_per_action_feet": 30,
    },
}
PUBLIC_TOOLS = {
    "record_srd_object_utility_facts",
    "record_srd_object_holder",
    "record_srd_spell_material_object",
    "use_srd_instant_summons",
}
CAST_CHOICES = [
    _optional("utility_object_id", default=""),
    _optional("utility_sapphire_id", default=""),
    _optional("utility_output_id", default=""),
    _optional("utility_form_id", default=""),
    _array("utility_point", default=[0, 0, 0]),
]
FACT_EXPORTS = ("_object_scene", "_utility_facts", "_world_distance", "_world_can_see")


def validate_profiles(definitions):
    for spell, definition in definitions.items():
        if definition.get("family") == "object_utility_spell" and definition != PROFILES.get(spell):
            raise ValueError(f"{spell} does not match its pinned object utility profile")


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


def _fact_guard():
    return [
        _require(
            "_object_scene.metadata.srd_object_utility != null",
            "Admit this actual object's weight and physical classification first.",
        ),
        _set("_utility_facts", "_object_scene.metadata.srd_object_utility"),
        _require("_utility_facts.weight_pounds >= 0", "The actual object needs a valid weight."),
    ]


def _fact_tools():
    mass = _tool_scene(
        "Classify an actual object's weight and manufacture once; no AC, HP or outcome verdicts.",
        [
            _parameter("object_id"),
            _parameter("weight_pounds", "number", minimum=0, maximum=1000000),
            _parameter("manufactured", "boolean"),
            _parameter("metal", "boolean"),
            _parameter(
                "object_kind",
                "enum",
                enum_values=[
                    "object",
                    "weapon",
                    "armor",
                    "shield",
                    "vial",
                    "sapphire",
                    "container",
                ],
            ),
            _optional("inventory_item", default=""),
        ],
        [
            *_admission_guard(),
            _require(
                "_object_scene.metadata.srd_object_utility == null",
                "This object's classification is fixed; re-preparation cannot change eligibility.",
            ),
            *_metadata_patch(
                {
                    "srd_object_utility": {
                        "weight_pounds": "weight_pounds",
                        "manufactured": "manufactured",
                        "metal": "metal",
                        "object_kind": "object_kind",
                        "inventory_item": "inventory_item",
                    }
                }
            ),
        ],
    )
    holder = _tool_scene(
        "Admit the actual creature carrying or wearing this scene object outside combat.",
        [
            _entity("actor_state"),
            _parameter("object_id"),
            _parameter("held_as", "enum", enum_values=["carried", "worn"]),
        ],
        [
            *_admission_guard(),
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_actor_state_id}",
                    "object_id": "{object_id}",
                    "range_feet": 5,
                    "requires_sight": False,
                },
                GEOMETRY_EXPORTS,
            ),
            _require(
                "_object_scene.metadata.carried_by == null and "
                "_object_scene.metadata.worn_by == null",
                "An actual held object needs its existing owner's transfer path.",
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_actor_state_id}"}, POSITION_EXPORTS
            ),
            _set("_utility_holder", "_info_scene_entity"),
            _when(
                "held_as == 'carried'",
                _metadata_patch(
                    {
                        "carried_by": "_actor_state_id",
                        "worn_by": None,
                        "plane": "_utility_holder.metadata.plane",
                        "lighting": "_utility_holder.metadata.lighting",
                    },
                    position="{_utility_holder.position}",
                ),
            ),
            _when(
                "held_as == 'worn'",
                _metadata_patch(
                    {
                        "carried_by": None,
                        "worn_by": "_actor_state_id",
                        "plane": "_utility_holder.metadata.plane",
                        "lighting": "_utility_holder.metadata.lighting",
                    },
                    position="{_utility_holder.position}",
                ),
            ),
        ],
    )
    resolve = _tool_scene(
        "Resolve source-qualified actual object weight, holder geometry, range and sight.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("range_feet", "integer", minimum=0, maximum=100000),
            _parameter("requires_sight", "boolean"),
        ],
        [
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "range_feet": "{range_feet}",
                    "requires_sight": "{requires_sight}",
                },
                GEOMETRY_EXPORTS,
            ),
            *_fact_guard(),
        ],
    )
    link = _tool_scene(
        "Link one actual prepared sapphire material lot to its actual carried scene object.",
        [_entity("caster_state"), _parameter("object_id"), _parameter("material_item_id")],
        [
            *_admission_guard(),
            *_fact_guard(),
            _require(
                "_utility_facts.object_kind == 'sapphire' and "
                "_object_scene.metadata.carried_by == _caster_state_id",
                "Use the caster's actual carried sapphire.",
            ),
            _require(
                "_object_scene.metadata.srd_spell_material_object == null",
                "This actual sapphire already has its inventory binding.",
            ),
            *_material_lookup("material_item_id"),
            _require(
                "_utility_facts.inventory_item == _utility_material.inventory_item",
                "The scene sapphire must be the actual selected inventory item.",
            ),
            *_metadata_patch(
                {
                    "srd_spell_material_object": {
                        "owner": "_caster_state_id",
                        "material_id": "material_item_id",
                        "inventory_item": "_utility_material.inventory_item",
                    }
                }
            ),
        ],
    )
    return {
        "record_srd_object_utility_facts": mass,
        "record_srd_object_holder": holder,
        "resolve_srd_object_utility_facts": resolve,
        "record_srd_spell_material_object": link,
    }


def _material_lookup(identity):
    return [
        _set("_utility_materials", []),
        _when(
            "caster_state.spell_materials != null",
            [_set("_utility_materials", "caster_state.spell_materials")],
        ),
        _set("_utility_material", None),
        _loop(
            "_utility_materials",
            "_utility_lot",
            [
                _when(
                    f"_utility_lot.id == {identity}",
                    [
                        _require("_utility_material == null", "The material lot ID is ambiguous."),
                        _set("_utility_material", "_utility_lot"),
                    ],
                ),
            ],
        ),
        _require(
            "_utility_material != null and _utility_material.quantity >= 1 and "
            "_utility_material.value_cp >= 100000 and 'instant_summons.1' in "
            "_utility_material.components and _utility_material.kind == 'specific' and "
            "_utility_material.inventory_item in caster_state.inventory",
            "Use the actual possessed source sapphire worth at least 1,000 GP.",
        ),
    ]


def _serial():
    return [
        _set("_utility_serial", 1),
        _when(
            "caster_state.srd_object_utility_serial != null",
            [_set("_utility_serial", "caster_state.srd_object_utility_serial + 1")],
        ),
        _set("caster_state.srd_object_utility_serial", "_utility_serial"),
        _call(
            "format_srd_information_projection",
            {"message": "{_caster_state_id}:{spell}:{_utility_serial}"},
            ["_info_projection_message"],
        ),
        _set("_utility_source_id", "_info_projection_message"),
    ]


def _instant_summons():
    return [
        _require(
            "utility_object_id != '' and utility_sapphire_id != '' and "
            "utility_object_id != utility_sapphire_id",
            "Touch one actual object and a different actual source sapphire.",
        ),
        _call(
            "resolve_srd_object_utility_facts",
            {
                "caster_state": "{_caster_state_id}",
                "object_id": "{utility_object_id}",
                "range_feet": 5,
                "requires_sight": False,
            },
            FACT_EXPORTS,
        ),
        _require(
            "_utility_facts.weight_pounds <= 10 and _object_scene.footprint.kind == 'box'",
            "Instant Summons needs an actual object of at most ten pounds and admitted dimensions.",
        ),
        _require(
            "_object_scene.footprint.half_extents[0] * 2 <= 6 and "
            "_object_scene.footprint.half_extents[1] * 2 <= 6 and _object_scene.height <= 6",
            "The actual object's longest dimension exceeds six feet.",
        ),
        _set("_utility_marked_id", "_object_scene.id"),
        _set("_utility_marked_fiction", "_object_scene.metadata.fiction"),
        _call(
            "resolve_srd_object_utility_facts",
            {
                "caster_state": "{_caster_state_id}",
                "object_id": "{utility_sapphire_id}",
                "range_feet": 5,
                "requires_sight": False,
            },
            FACT_EXPORTS,
        ),
        _require(
            "_object_scene.metadata.srd_used_instant_summons == null and "
            "_object_scene.metadata.srd_spell_material_object != null and "
            "_utility_facts.object_kind == 'sapphire'",
            "Each Instant Summons casting requires a different actual sapphire.",
        ),
        _set("_utility_binding", "_object_scene.metadata.srd_spell_material_object"),
        _require(
            "_utility_binding.owner == _caster_state_id and "
            "_utility_binding.material_id in material_item_ids",
            "This must be the actual source sapphire selected by the shared casting owner.",
        ),
        *_material_lookup("_utility_binding.material_id"),
        *_serial(),
        _set(
            "_utility_record",
            {
                "kind": "'object_utility_spell'",
                "spell": "'instant_summons'",
                "spell_level": "slot_level",
                "source_id": "_utility_source_id",
                "source_sha256": repr(SOURCE_PAGES[143]),
                "caster": "_caster_state_id",
                "target": "_caster_state_id",
                "owner": "_caster_state_id",
                "phase": "'never'",
                "turn": 0,
                "object_id": "_utility_marked_id",
                "sapphire_id": "utility_sapphire_id",
                "material_id": "_utility_binding.material_id",
                "inventory_item": "_utility_binding.inventory_item",
                "object_name": "_utility_marked_fiction",
                "started_tick": "_object_now",
                "active": True,
                "expires_tick": None,
                "dispel_ongoing": True,
            },
        ),
        _call(
            "retain_srd_instant_summons_mark",
            {
                "object_id": "{utility_sapphire_id}",
                "source_id": "{_utility_source_id}",
            },
        ),
        _set("_utility_records", []),
        _when("state.turn_effects != null", [_set("_utility_records", "state.turn_effects")]),
        _push("_utility_records", "_utility_record"),
        _set("state.turn_effects", "_utility_records"),
        _push("_utility_summary", {"label": "Marked object", "value": "_utility_marked_fiction"}),
    ]


def _read_source(spell=None):
    spell_guard = f" and _utility_candidate.spell == {spell!r}" if spell else ""
    return [
        *_now(),
        _set("_utility_records", []),
        _when("state.turn_effects != null", [_set("_utility_records", "state.turn_effects")]),
        _set("_utility_record", None),
        _loop(
            "_utility_records",
            "_utility_candidate",
            [
                _when(
                    "_utility_candidate.kind == 'object_utility_spell' and "
                    "_utility_candidate.source_id == source_id and "
                    "_utility_candidate.active == true" + spell_guard,
                    [_set("_utility_record", "_utility_candidate")],
                ),
            ],
        ),
        _require("_utility_record != null", "Select an actual active object utility source."),
        _require("_utility_record.spell_level >= 0", "The source needs its actual cast level."),
        _when(
            "_utility_record.spell == 'instant_summons'",
            [
                _require(
                    f"_utility_record.source_sha256 == {SOURCE_PAGES[143]!r} and "
                    "_utility_record.spell_level >= 6",
                    "Instant Summons requires its exact source and real spell level.",
                ),
            ],
        ),
    ]


def _instant_tools():
    mark = _tool_scene(
        "Remember this sapphire's one actual use without allowing classification to clear it.",
        [_parameter("object_id"), _parameter("source_id")],
        [*_lookup_object(), *_metadata_patch({"srd_used_instant_summons": "source_id"})],
    )
    use = _tool_scene(
        "Spend a Magic action to crush the source sapphire and recall or locate its actual object.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_read_source("instant_summons"),
            _require(
                "_utility_record.caster == _caster_state_id and "
                f"_utility_record.source_sha256 == {SOURCE_PAGES[143]!r}",
                "Only the actual caster may use this pinned Instant Summons source.",
            ),
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{_utility_record.sapphire_id}",
                    "range_feet": 5,
                    "requires_sight": False,
                },
                GEOMETRY_EXPORTS,
            ),
            _require(
                "_object_scene.metadata.srd_used_instant_summons == source_id and "
                "_object_scene.metadata.carried_by == _caster_state_id",
                "The actual source sapphire must still be in the caster's hand.",
            ),
            *_material_lookup("_utility_record.material_id"),
            _require("combat.pending_action == null", "Finish the pending action first."),
            _when(
                "combat.encounter_active == true",
                [
                    _require(
                        "combat.active_actor == _caster_state_id",
                        "Only the active caster may use it.",
                    ),
                    _call("spend_srd_action", {"magic_action": True}),
                ],
            ),
            _call(
                "crush_srd_instant_summons_sapphire",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{_utility_record.sapphire_id}",
                    "material_id": "{_utility_record.material_id}",
                },
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_utility_caster_scene", "_info_scene_entity"),
            _call(
                "resolve_srd_instant_summons_object",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{_utility_record.object_id}",
                },
                [
                    "_object_scene",
                    "_utility_other_holder",
                    "_utility_holder_name",
                    "_utility_holder_position",
                    "_utility_holder_plane",
                ],
            ),
            _when(
                "_utility_other_holder != true",
                [
                    _call(
                        "move_srd_travel_subject",
                        {
                            "subject": "{_utility_record.object_id}",
                            "destination": "{_utility_caster_scene.position}",
                            "plane": "{_utility_caster_scene.metadata.plane}",
                            "teleport": True,
                        },
                    ),
                    _call(
                        "carry_srd_summoned_object",
                        {
                            "caster_state": "{_caster_state_id}",
                            "object_id": "{_utility_record.object_id}",
                        },
                    ),
                ],
            ),
            _call(
                "retire_srd_object_utility_source",
                {
                    "caster_state": "{_caster_state_id}",
                    "source_id": "{source_id}",
                },
            ),
        ],
    )
    use["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "compact",
        "title": "Instant Summons",
        "summary": [
            {
                "label": "Held by another creature",
                "value": {"path": "context._utility_other_holder"},
            }
        ],
        "details": [
            {"label": "Holder", "value": {"path": "context._utility_holder_name"}},
            {"label": "Location", "value": {"path": "context._utility_holder_position"}},
            {"label": "Plane", "value": {"path": "context._utility_holder_plane"}},
        ],
    }
    object_tool = _tool_scene(
        "Read the marked object's actual holder across physical or planar distance.",
        [_entity("caster_state"), _parameter("object_id")],
        [
            *_lookup_object(),
            *_held_object_geometry("{_caster_state_id}"),
            _set("_utility_holder_id", None),
            _when(
                "_object_scene.metadata.carried_by != null",
                [_set("_utility_holder_id", "_object_scene.metadata.carried_by")],
            ),
            _when(
                "_object_scene.metadata.worn_by != null",
                [_set("_utility_holder_id", "_object_scene.metadata.worn_by")],
            ),
            _set("_utility_other_holder", False),
            _set("_utility_holder_name", "''"),
            _set("_utility_holder_position", []),
            _set("_utility_holder_plane", "''"),
            _when(
                "_utility_holder_id != null and _utility_holder_id != _caster_state_id",
                [
                    _set("_utility_other_holder", True),
                    _call(
                        "read_srd_actual_object_holder",
                        {"holder_state": "{_utility_holder_id}"},
                        [
                            "_utility_holder_name",
                            "_utility_holder_position",
                            "_utility_holder_plane",
                        ],
                    ),
                ],
            ),
        ],
    )
    holder_read = _tool_scene(
        "Read the actual holder's name and admitted position when recall cannot take its object.",
        [_entity("holder_state")],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_holder_state_id}"}, POSITION_EXPORTS
            ),
            _set("_utility_holder_name", "holder_state.name"),
            _set("_utility_holder_position", "_info_scene_entity.position"),
            _set("_utility_holder_plane", "_info_scene_entity.metadata.plane"),
        ],
    )
    crush = _tool_scene(
        "Destroy only the actual sapphire and consume only its saved inventory lot once.",
        [_entity("caster_state"), _parameter("object_id"), _parameter("material_id")],
        [
            *_lookup_object(),
            *_metadata_patch({}, active=False),
            *_material_lookup("material_id"),
            _set("_utility_kept_lots", []),
            _loop(
                "_utility_materials",
                "_utility_lot",
                [
                    {
                        "condition": "_utility_lot.id == material_id",
                        "on_true": [
                            _set("_utility_lot_change", {"quantity": "_utility_lot.quantity - 1"}),
                            _push("_utility_kept_lots", "merge(_utility_lot, _utility_lot_change)"),
                        ],
                        "on_false": [_push("_utility_kept_lots", "_utility_lot")],
                    },
                ],
            ),
            _set("caster_state.spell_materials", "_utility_kept_lots"),
            _when(
                "_utility_material.quantity == 1",
                [
                    _set("_utility_inventory", "caster_state.inventory"),
                    _set("_utility_kept_inventory", []),
                    _loop(
                        "_utility_inventory",
                        "_utility_inventory_item",
                        [
                            _when(
                                "_utility_inventory_item != _utility_material.inventory_item",
                                [_push("_utility_kept_inventory", "_utility_inventory_item")],
                            ),
                        ],
                    ),
                    _set("caster_state.inventory", "_utility_kept_inventory"),
                ],
            ),
        ],
    )
    carry = _tool_scene(
        "Put the actually recalled object in the actual caster's hand at its canonical position.",
        [_entity("caster_state"), _parameter("object_id")],
        [*_lookup_object(), *_metadata_patch({"carried_by": "_caster_state_id", "worn_by": None})],
    )
    retire = _tool_scene(
        "Retire only one exact actual object utility source owned by this caster.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_read_source(),
            _require(
                "_utility_record.caster == _caster_state_id",
                "This source belongs to another caster.",
            ),
            _set("_utility_kept_records", []),
            _loop(
                "_utility_records",
                "_utility_candidate",
                [
                    _when(
                        "_utility_candidate.kind != 'object_utility_spell' or "
                        "_utility_candidate.source_id != source_id",
                        [_push("_utility_kept_records", "_utility_candidate")],
                    ),
                ],
            ),
            _set("state.turn_effects", "_utility_kept_records"),
        ],
    )
    return {
        "retain_srd_instant_summons_mark": mark,
        "use_srd_instant_summons": use,
        "resolve_srd_instant_summons_object": object_tool,
        "read_srd_actual_object_holder": holder_read,
        "crush_srd_instant_summons_sapphire": crush,
        "carry_srd_summoned_object": carry,
        "retire_srd_object_utility_source": retire,
    }


def _casting_tool():
    """Apply source effects after the shared cast owns the real components and paid casting time."""
    return _tool_scene(
        "Resolve source-owned object utilities after actual casting payment and duration.",
        [
            _entity("caster_state"),
            _parameter("spell", "enum", enum_values=sorted(PROFILES)),
            _parameter("slot_level", "integer", minimum=0, maximum=9),
            _optional("material_item_ids", "array", items={"type": "string"}, default=[]),
            *deepcopy(CAST_CHOICES),
        ],
        [
            *_now(),
            _set("_utility_summary", []),
            _set("_utility_details", []),
            _require("spell == 'instant_summons'", "Mage Hand and Disk controls remain pending."),
            _when("spell == 'instant_summons'", _instant_summons()),
        ],
    )


def _source_read_tool():
    return _tool_scene(
        "Read the exact ongoing object utility source selected on its actual source object.",
        [_parameter("object_id"), _parameter("source_id")],
        [
            *_read_source(),
            *_lookup_object(),
            _require(
                "_utility_record.dispel_ongoing == true", "Only ongoing sources can be removed."
            ),
            _when(
                "_utility_record.spell == 'instant_summons'",
                [
                    _require(
                        "object_id == _utility_record.sapphire_id",
                        "Dispel Instant Summons on its actual source sapphire.",
                    ),
                ],
            ),
            _set("_object_utility_source", "_utility_record"),
        ],
    )


def extend_runtime(text):
    """Compose one guarded dispatch over the admitted object, source and sole relocation owners."""
    tools = _load_runtime(text)["tools"]
    changed = {
        **_fact_tools(),
        **_instant_tools(),
        "cast_spell_object_utility_spell": _casting_tool(),
        "read_srd_object_utility_source": _source_read_tool(),
    }
    for name in PUBLIC_TOOLS:
        tool = changed[name]
        changed[name + "_node"] = deepcopy(tool)
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        exports = (
            [
                "_utility_other_holder",
                "_utility_holder_name",
                "_utility_holder_position",
                "_utility_holder_plane",
            ]
            if name == "use_srd_instant_summons"
            else []
        )
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
                exports,
            )
        ]
    cast = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in names]
    for effects in list(_effect_lists(cast["effects"])):
        effects[:] = [
            e
            for e in effects
            if "_spell_definition.family == 'object_utility_spell'" not in e.get("condition", "")
        ]
    resolution = next(
        e
        for e in _effect_lists(cast["effects"])
        if any(
            "_spell_definition.family == 'information_spell'" in x.get("condition", "") for x in e
        )
    )
    resolution.append(
        _when(
            "_spell_cast_failed != true and _spell_definition.family == 'object_utility_spell'",
            [
                {
                    "call": "cast_spell_object_utility_spell",
                    "params": {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "slot_level": "{_slot_level}",
                        "material_item_ids": "{material_item_ids}",
                        **{p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES},
                    },
                    "export_context": {
                        "_spell_summary_rows": "_utility_summary",
                        "_spell_detail_rows": "_utility_details",
                    },
                },
            ],
        )
    )
    changed["cast_spell"] = cast
    text = _replace_tools(text, changed)
    if "      object_utility_spell:" not in text:
        schema = yaml.dump(
            {"object_utility_spell": _schema()}, Dumper=_RuntimeDumper, sort_keys=False
        )
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      timed_senses:",
            1,
        )
    return text
