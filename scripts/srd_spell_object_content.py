"""Pinned object spells change admitted scenery rather than creating creature state."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_information_spells import ACTIVE
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial, _tool_scene
    from .srd_spell_object_targets import _lookup_object
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
    from srd_information_spells import ACTIVE
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial, _tool_scene
    from srd_spell_object_targets import _lookup_object
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
    "arcane_lock": (110, "Arcane Lock", "Arcane Sword"),
    "create_food_and_water": ([120, 121], "Create Food and Water", "Create or Destroy Water"),
    "create_or_destroy_water": (121, "Create or Destroy Water", "Create Undead"),
    "creation": (121, "Creation", "Cure Wounds"),
    "fabricate": (129, "Fabricate", "Faerie Fire"),
    "knock": ([143, 144], "Knock", "Legend Lore"),
    "mending": (148, "Mending", "Message"),
    "stone_shape": (165, "Stone Shape", "Stoneskin"),
}
SOURCE_PAGES = {
    110: "03b22c8bed6c642b84d33dea11fde8bbaaf3a42816ee7804e61f959ed945dec5",
    120: "dcc0f13f68071229e96200384441058b9c07b0858f2230a4d335030f6f7bede1",
    121: "564c41de7bcb0225e84a7af1d5b36062c0f42ee3be59dba253a51a3502d3ad9d",
    129: "aaac1b6b3e96f63f3c42470a2fd9e5e3d042015d530b40262ad0ac535a8b5773",
    143: "dba4cdf16b912f655bd62da857b6d2033969bb1abe9aa0e604f3c60e69ec286d",
    144: "f0edd0263b32157a64c67c0cc0da7d51d9beb03031798b9ac26b3ff0cb1c9626",
    148: "0b69f7c42359b5c879201a3384dad83a2e20deb742010787fad1cc7aa9757c71",
    165: "f354e4148917526a956fc723219b36cedf2ecb617f90d0de9abef47aa9d5d7e9",
}
PROFILES = {}
for _spell, _level, _timing, _range, _feet in (
    ("arcane_lock", 2, "action", "touch", 0),
    ("create_food_and_water", 3, "action", "distance", 30),
    ("create_or_destroy_water", 1, "action", "distance", 30),
    ("creation", 5, "1_minute", "distance", 30),
    ("fabricate", 4, "10_minutes", "distance", 120),
    ("knock", 2, "action", "distance", 60),
    ("mending", 0, "1_minute", "touch", 0),
    ("stone_shape", 4, "action", "touch", 0),
):
    PROFILES[_spell] = {
        "family": "object_content_spell",
        "base_level": _level,
        "casting_time": _timing,
        "range_kind": _range,
        "range_feet": _feet,
        "concentration": False,
        "object_mode": _spell,
    }
PROFILES["arcane_lock"].update(password_rounds=10)
PROFILES["knock"].update(suppression_rounds=100, audible_feet=300)
PROFILES["mending"].update(maximum_break_feet=1)
PROFILES["create_food_and_water"].update(food_pounds=45, water_gallons=30, spoil_rounds=14400)
PROFILES["create_or_destroy_water"].update(
    base_gallons=10, gallons_per_level=10, base_cube_feet=30, cube_feet_per_level=5
)
PROFILES["creation"].update(base_cube_feet=5, cube_feet_per_level=5)
PROFILES["fabricate"].update(
    vegetable_cube_feet=10, connected_five_foot_cubes=8, mineral_cube_feet=5
)
PROFILES["stone_shape"].update(maximum_section_feet=5, maximum_hinges=2, maximum_latches=1)
CREATION_ROUNDS = {
    "vegetable": 14400,
    "stone_or_crystal": 7200,
    "precious_metal": 600,
    "gem": 100,
    "adamantine_or_mithral": 10,
}
PUBLIC_TOOLS = {
    "record_srd_object_access",
    "record_srd_object_break",
    "operate_srd_locked_object",
    "record_srd_object_material",
    "record_srd_object_container",
    "observe_srd_object_pattern",
    "consume_srd_created_supply",
    "record_srd_ordinary_flame",
}
CAST_CHOICES = [
    _optional("object_target_id", default=""),
    _optional("object_break_id", default=""),
    _optional("object_password", default=""),
    _optional("object_designated_creatures", "array", [], items={"type": "string"}, max_items=64),
    _optional(
        "object_mode",
        "enum",
        "create_water",
        enum_values=["create_water", "destroy_water", "rain", "destroy_fog"],
    ),
    _optional("object_form", default=""),
    _optional(
        "object_craft", "enum", "plain", enum_values=["plain", "weapons", "armor", "fine_jewelry"]
    ),
    _array("object_extent", default=[1, 1, 1]),
    _optional(
        "object_materials",
        "array",
        [],
        items={"type": "string", "enum": list(CREATION_ROUNDS)},
        max_items=5,
        unique_items=True,
    ),
    _optional("object_hinges", "integer", 0, minimum=0, maximum=2),
    _optional("object_latches", "integer", 0, minimum=0, maximum=1),
    _optional(
        "object_component_ids",
        "array",
        [],
        items={"type": "string"},
        max_items=16,
        unique_items=True,
    ),
    _optional("object_fabricate_layout", "enum", "box", enum_values=["box", "connected_cubes"]),
    _optional(
        "object_cells",
        "array",
        [],
        items={"type": "array", "items": {"type": "number"}},
        max_items=8,
    ),
    _optional("object_output_id", default=""),
    _array("object_point", default=[0, 0, 0]),
    _optional(
        "object_cube_face",
        "enum",
        "east",
        enum_values=["east", "west", "north", "south", "up", "down"],
    ),
    _optional(
        "object_container_ids",
        "array",
        [],
        items={"type": "string"},
        max_items=16,
        unique_items=True,
    ),
    _optional("object_quantity", "number", 0, minimum=0, maximum=1000000),
    _optional(
        "object_shape", "enum", "solid", enum_values=["solid", "passage", "sealed_doorframe"]
    ),
]
GEOMETRY_EXPORTS = ("_object_scene", "_world_distance", "_world_can_see")


def validate_profiles(definitions):
    """Keep every spell constant equal to its pinned source profile."""
    for spell, definition in definitions.items():
        if definition.get("family") == "object_content_spell" and definition != PROFILES.get(spell):
            raise SystemExit(f"Object content source profile differs: {spell}")


def _profile_schema():
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
    common = set.intersection(*(set(p) for p in PROFILES.values())) - {"family"}
    return {
        "type": "object",
        "properties": fields,
        "required": sorted(common),
        "additionalProperties": False,
    }


def _now():
    return [
        _set("_object_now", 0),
        _when("state.lasting_tick != null", [_set("_object_now", "state.lasting_tick")]),
    ]


def _metadata_patch(changes, **geometry):
    """Change only owned scenery facts and let the neutral mutation validate the scene."""
    return [
        *_snapshot(),
        _set("_world_changes", changes),
        _set("_world_object_marker", {"is_object": True}),
        _set("_world_changes", "merge(_world_changes, _world_object_marker)"),
        _set("_world_metadata", "merge(_object_scene.metadata, _world_changes)"),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "entity", "id": "{object_id}"},
                "patch": {"metadata": "{_world_metadata}", **geometry},
            },
            "store_as": "_world_mutation",
        },
    ]


def _admission_guard():
    return [
        _require("combat.encounter_active != true", "Admit object facts outside combat."),
        _require("combat.pending_action == null", "Finish the pending action first."),
        *_lookup_object(),
    ]


def _fact_tools():
    access = _tool_scene(
        "Admit mundane access facts for actual scenery; this cannot alter a magical lock.",
        [
            _parameter("object_id"),
            _parameter(
                "object_kind",
                "enum",
                enum_values=[
                    "door",
                    "window",
                    "gate",
                    "container",
                    "hatch",
                    "manacles",
                    "padlock",
                    "other",
                ],
            ),
            _parameter("closed", "boolean"),
            _parameter("locks", "array", items={"type": "string"}, max_items=64, unique_items=True),
            _parameter("stuck", "boolean"),
            _parameter("barred", "boolean"),
        ],
        [
            *_admission_guard(),
            _require(
                "_object_scene.metadata.srd_object_access == null",
                "Object access facts are already admitted.",
            ),
            *_metadata_patch(
                {
                    "srd_object_access": {
                        "kind": "object_kind",
                        "closed": "closed",
                        "locks": "locks",
                        "stuck": "stuck",
                        "barred": "barred",
                    }
                }
            ),
        ],
    )
    fracture = _tool_scene(
        (
            "Admit one physical break or tear by its actual bounds; no HP or "
            "magic restoration verdict."
        ),
        [_parameter("object_id"), _parameter("break_id"), _array("break_min"), _array("break_max")],
        [
            *_admission_guard(),
            _require(
                "_object_scene.footprint.kind == 'box'",
                "Admit the object's actual bounds before its break.",
            ),
            *[
                _require(f"break_max[{axis}] >= break_min[{axis}]", "Break bounds must increase.")
                for axis in range(3)
            ],
            *[
                _require(
                    f"break_min[{axis}] >= _object_scene.position[{axis}] - "
                    f"_object_scene.footprint.half_extents[{axis}] and "
                    f"break_max[{axis}] <= _object_scene.position[{axis}] + "
                    f"_object_scene.footprint.half_extents[{axis}]",
                    "The admitted break must lie inside the actual object.",
                )
                for axis in range(2)
            ],
            _require(
                "break_min[2] >= _object_scene.position[2] and "
                "break_max[2] <= _object_scene.position[2] + _object_scene.height",
                "The admitted break must lie inside the actual object.",
            ),
            _set("_world_breaks", []),
            _when(
                "_object_scene.metadata.srd_object_breaks != null",
                [_set("_world_breaks", "_object_scene.metadata.srd_object_breaks")],
            ),
            _loop(
                "_world_breaks",
                "_world_break",
                [
                    _require(
                        "_world_break.id != break_id", "This physical break is already admitted."
                    ),
                ],
            ),
            _push("_world_breaks", {"id": "break_id", "min": "break_min", "max": "break_max"}),
            *_metadata_patch({"srd_object_breaks": "_world_breaks"}),
        ],
    )
    material = _tool_scene(
        (
            "Classify actual object material and raw stock; this cannot "
            "change a prepared damage profile."
        ),
        [
            _parameter("object_id"),
            _parameter("material", "enum", enum_values=list(CREATION_ROUNDS)),
            _parameter("raw_stock", "boolean"),
            _optional("magical", "boolean", False),
            _optional(
                "materials",
                "array",
                [],
                items={"type": "string", "enum": list(CREATION_ROUNDS)},
                max_items=5,
                unique_items=True,
            ),
        ],
        [
            *_admission_guard(),
            _require(
                "_object_scene.metadata.srd_object_material == null",
                "Object material is already admitted.",
            ),
            _require(
                "_object_scene.footprint.kind == 'box'",
                "Material stock needs its actual box bounds.",
            ),
            _set("_world_materials", "materials"),
            _when("len(_world_materials) == 0", [_push("_world_materials", "material")]),
            _require(
                "material in _world_materials",
                "The main material must be part of the admitted actual mixture.",
            ),
            *_metadata_patch(
                {
                    "srd_object_material": {
                        "material": "material",
                        "materials": "_world_materials",
                        "raw_stock": "raw_stock",
                        "magical": "magical",
                    }
                }
            ),
        ],
    )
    container = _tool_scene(
        "Admit an actual vessel's physical capacity and existing water, once.",
        [
            _parameter("object_id"),
            _parameter("capacity_gallons", "number", minimum=0, maximum=1000000),
            _parameter("water_gallons", "number", minimum=0, maximum=1000000),
            _parameter("is_open", "boolean"),
            _optional("food_capacity_pounds", "number", 0, minimum=0, maximum=1000000),
            _optional("food_pounds", "number", 0, minimum=0, maximum=1000000),
        ],
        [
            *_admission_guard(),
            _require(
                "_object_scene.metadata.srd_object_container == null",
                "Container capacity and water are already admitted.",
            ),
            _require(
                "water_gallons <= capacity_gallons",
                "Existing water cannot exceed the actual capacity.",
            ),
            _require("food_pounds <= food_capacity_pounds", "Existing food exceeds capacity."),
            *_metadata_patch(
                {
                    "srd_object_container": {
                        "capacity_gallons": "capacity_gallons",
                        "water_gallons": "water_gallons",
                        "is_open": "is_open",
                        "food_capacity_pounds": "food_capacity_pounds",
                        "food_pounds": "food_pounds",
                    }
                }
            ),
        ],
    )
    flame = _tool_scene(
        "Admit an actual ordinary flame and whether it is exposed to rain, once.",
        [_parameter("object_id"), _parameter("exposed", "boolean")],
        [
            *_admission_guard(),
            _require(
                "_object_scene.metadata.srd_ordinary_flame == null",
                "The ordinary flame is already admitted.",
            ),
            _require(
                "_object_scene.footprint.kind == 'box'", "Admit the flame's actual box bounds."
            ),
            *_metadata_patch(
                {
                    "is_object": True,
                    "srd_ordinary_flame": {
                        "kind": "'ordinary_flame'",
                        "burning": True,
                        "exposed": "exposed",
                    },
                }
            ),
        ],
    )
    observe = _tool_scene(
        "Retain a form/material only after the actual caster sees this admitted object.",
        [_entity("caster_state"), _parameter("object_id")],
        [
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "range_feet": 100000,
                    "requires_sight": True,
                },
                GEOMETRY_EXPORTS,
            ),
            _require(
                "_object_scene.metadata.srd_object_material != null",
                "The pattern needs admitted material facts.",
            ),
            _set("_world_seen_by", []),
            _when(
                "_object_scene.metadata.srd_seen_by != null",
                [_set("_world_seen_by", "_object_scene.metadata.srd_seen_by")],
            ),
            _when(
                "_caster_state_id not in _world_seen_by",
                [_push("_world_seen_by", "_caster_state_id")],
            ),
            _set("_world_seen_patterns", []),
            _when(
                "caster_state.srd_object_patterns != null",
                [_set("_world_seen_patterns", "caster_state.srd_object_patterns")],
            ),
            _set("_world_seen_pattern_found", False),
            _loop(
                "_world_seen_patterns",
                "_world_seen_pattern",
                [
                    _when(
                        "_world_seen_pattern.object_id == object_id",
                        [_set("_world_seen_pattern_found", True)],
                    ),
                ],
            ),
            _when(
                "_world_seen_pattern_found != true",
                [
                    _push(
                        "_world_seen_patterns",
                        {
                            "object_id": "object_id",
                            "fiction": "_object_scene.metadata.fiction",
                            "material": "_object_scene.metadata.srd_object_material",
                            "movement": "_object_scene.movement",
                            "visibility": "_object_scene.visibility",
                        },
                    ),
                    _set("caster_state.srd_object_patterns", "_world_seen_patterns"),
                ],
            ),
            *_metadata_patch({"srd_seen_by": "_world_seen_by"}),
        ],
    )
    return {
        "record_srd_object_access": access,
        "record_srd_object_break": fracture,
        "record_srd_object_material": material,
        "record_srd_object_container": container,
        "observe_srd_object_pattern": observe,
        "record_srd_ordinary_flame": flame,
    }


def _held_object_geometry(observer):
    """Synchronize scenery from the actual holder source before spatial consumption."""
    return [
        _call(
            "check_srd_illumination_object_visibility",
            {
                "object_id": "{object_id}",
                "observer_state": observer,
            },
            [
                "_illumination_object_invisible",
                "_illumination_object_suppressed",
                "_illumination_object_origin",
                "_illumination_object_plane",
                "_illumination_object_lighting",
            ],
        ),
        _require(
            "_illumination_object_origin != null and _illumination_object_plane != null",
            "The actual holder requires admitted geometry and plane.",
        ),
        _when(
            "_object_scene.position[0] != _illumination_object_origin[0] or "
            "_object_scene.position[1] != _illumination_object_origin[1] or "
            "_object_scene.position[2] != _illumination_object_origin[2] or "
            "_object_scene.metadata.plane != _illumination_object_plane or "
            "_object_scene.metadata.lighting != _illumination_object_lighting",
            [
                *_metadata_patch(
                    {
                        "plane": "_illumination_object_plane",
                        "lighting": "_illumination_object_lighting",
                    },
                    position="{_illumination_object_origin}",
                ),
                *_lookup_object(),
            ],
        ),
    ]


def _geometry_tool(*, light=False, object_visibility=False):
    """World spells also address unprepared or destroyed objects without creature statistics."""
    return _tool_scene(
        "Derive real object range, clear path and sight from the admitted scene.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("range_feet", "integer", minimum=0, maximum=100000),
            _parameter("requires_sight", "boolean"),
        ],
        [
            *_lookup_object(),
            _set("_world_invisible", "_object_scene.metadata.invisible == true"),
            *(
                [
                    _require(
                        "_object_scene.metadata.carried_by == null and "
                        "_object_scene.metadata.worn_by == null",
                        "Held objects require their actual source geometry helper.",
                    ),
                ]
                if not object_visibility
                else [
                    *_held_object_geometry("{_caster_state_id}"),
                    _when(
                        "_illumination_object_invisible == true", [_set("_world_invisible", True)]
                    ),
                    _when(
                        "_illumination_object_suppressed == true", [_set("_world_invisible", False)]
                    ),
                ]
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_world_caster_scene", "_info_scene_entity"),
            _require(
                (
                    "_object_scene.metadata.plane != null and "
                    "_world_caster_scene.metadata.plane == "
                    "_object_scene.metadata.plane"
                ),
                "The object must be on the caster's actual plane.",
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_world_caster_scene.position}", "end": "{_object_scene.position}"},
                ["_scene_distance"],
            ),
            _set("_world_distance", "_scene_distance"),
            _require("_world_distance <= range_feet", "The actual object is outside spell range."),
            *_snapshot(),
            _spatial(
                "exposure",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "observer": {"point": "{_world_caster_scene.position}"},
                    "target": {"entity_id": "{object_id}"},
                    "samples": {"horizontal": 3, "vertical": 3},
                    "include": ["geometry"],
                },
                "_world_exposure",
            ),
            _require("_world_exposure.visible_samples > 0", "Total Cover blocks this spell."),
            _set("_world_can_see", False),
            _when(
                "requires_sight == true",
                [
                    _require(
                        "_object_scene.metadata.lighting != null",
                        "The actual object needs ambient lighting.",
                    ),
                    _set("_world_sight_distance", "ceil(_world_distance)"),
                    _set("_world_light", "_object_scene.metadata.lighting"),
                    *(
                        [
                            _call(
                                "resolve_srd_information_light",
                                {
                                    "point": "{_object_scene.position}",
                                    "plane": "{_object_scene.metadata.plane}",
                                    "ambient": "{_world_light}",
                                },
                                ["_information_light"],
                            ),
                            _set("_world_light", "_information_light"),
                        ]
                        if light
                        else []
                    ),
                    _call(
                        "resolve_srd_sight",
                        {
                            "observer_state": "{_caster_state_id}",
                            "distance": "{_world_sight_distance}",
                            "lighting": "{_world_light}",
                            "obstruction": "clear",
                            "subject_invisible": "{_world_invisible}",
                        },
                        ["_sight_can_see"],
                    ),
                    _set("_world_can_see", "_sight_can_see == true"),
                    _when(
                        (
                            "caster_state.blindsight_feet != null and "
                            "caster_state.blindsight_feet >= _world_distance"
                        ),
                        [_set("_world_can_see", True)],
                    ),
                    _require(
                        "_world_can_see == true", "This source requires actually seeing the object."
                    ),
                ],
            ),
        ],
    )


def _mending():
    """Repair precisely one eligible physical break, retaining all existing magic and HP."""
    return [
        _require(
            "object_break_id != '' and _object_scene.metadata.srd_object_breaks != null",
            "Choose an admitted physical break or tear.",
        ),
        _set("_world_breaks", "_object_scene.metadata.srd_object_breaks"),
        _set("_world_retained_breaks", []),
        _set("_world_mended", None),
        _loop(
            "_world_breaks",
            "_world_break",
            [
                {
                    "condition": "_world_break.id == object_break_id",
                    "on_true": [
                        _require("_world_mended == null", "A physical break ID must be unique."),
                        *[
                            _require(
                                f"_world_break.max[{axis}] - _world_break.min[{axis}] <= "
                                "_world_profile.maximum_break_feet",
                                "Mending repairs a break no larger than one foot in any dimension.",
                            )
                            for axis in range(3)
                        ],
                        _set("_world_mended", "_world_break.id"),
                    ],
                    "on_false": [_push("_world_retained_breaks", "_world_break")],
                }
            ],
        ),
        _require("_world_mended != null", "That physical break is not present on this object."),
        *_metadata_patch({"srd_object_breaks": "_world_retained_breaks"}),
        _push(
            "_spell_summary_rows", {"label": "object_id", "value": "'One physical break repaired'"}
        ),
        _push(
            "_spell_detail_rows",
            {"label": "'Mending'", "value": "'Existing magic and numerical HP are unchanged.'"},
        ),
    ]


def _arcane_lock():
    """Store each actual spell source; equal locks use the most recent remaining source."""
    return [
        _require(
            (
                "_object_scene.metadata.srd_object_access != null and "
                "_object_scene.metadata.srd_object_access.closed == true"
            ),
            "Arcane Lock needs an admitted closed door, window, gate, container or hatch.",
        ),
        _set("_world_lock_kinds", ["door", "window", "gate", "container", "hatch"]),
        _require(
            "_object_scene.metadata.srd_object_access.kind in _world_lock_kinds",
            "This object is not an Arcane Lock target.",
        ),
        _set("_world_locks", []),
        _when(
            "_object_scene.metadata.srd_arcane_locks != null",
            [_set("_world_locks", "_object_scene.metadata.srd_arcane_locks")],
        ),
        _loop(
            "object_designated_creatures",
            "_world_designee",
            [
                _call("validate_srd_object_lock_creature", {"creature_state": "{_world_designee}"}),
            ],
        ),
        _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
        {"increment": {"field": "state.lasting_serial", "amount": 1}},
        _set("_world_source_instance", "state.lasting_serial"),
        _call(
            "format_srd_information_projection",
            {"message": "{_caster_state_id}:arcane_lock:{_world_source_instance}"},
            ["_info_projection_message"],
        ),
        _set("_world_source_id", "_info_projection_message"),
        _push(
            "_world_locks",
            {
                "kind": "'object_content_spell'",
                "spell": "'arcane_lock'",
                "source": repr("sylvia.srd-lite.bg3:spells:arcane_lock:" + SOURCE_PAGES[110]),
                "caster": "_caster_state_id",
                "instance": "state.lasting_serial",
                "source_id": "_world_source_id",
                "spell_level": "slot_level",
                "dispel_ongoing": True,
                "started_tick": "_object_now",
                "suppressed_until_tick": 0,
                "password_until_tick": 0,
                "password": "object_password",
                "designated_creatures": "object_designated_creatures",
            },
        ),
        *_metadata_patch({"srd_arcane_locks": "_world_locks"}),
        _push(
            "_spell_summary_rows",
            {"label": "object_id", "value": "'Magically locked until dispelled'"},
        ),
    ]


def _knock():
    """Suppress the actual Arcane Lock source, otherwise unlock one mundane lock."""
    return [
        _require(
            "_object_scene.metadata.srd_object_access != null",
            "Knock needs an admitted object with an access restriction.",
        ),
        _set("_world_access", "_object_scene.metadata.srd_object_access"),
        _set("_world_locks", []),
        _when(
            "_object_scene.metadata.srd_arcane_locks != null",
            [_set("_world_locks", "_object_scene.metadata.srd_arcane_locks")],
        ),
        _set("_world_arcane_index", "len(_world_locks) - 1"),
        {
            "condition": "_world_arcane_index >= 0",
            "on_true": [
                _set("_world_updated_locks", []),
                _set("_world_lock_index", 0),
                _loop(
                    "_world_locks",
                    "_world_lock",
                    [
                        _set("_world_retained_lock", "_world_lock"),
                        _when(
                            "_world_lock_index == _world_arcane_index",
                            [
                                _set(
                                    "_world_suppression",
                                    {
                                        "suppressed_until_tick": (
                                            "_object_now + _world_profile.suppression_rounds * 2"
                                        )
                                    },
                                ),
                                _set(
                                    "_world_retained_lock", "merge(_world_lock, _world_suppression)"
                                ),
                            ],
                        ),
                        _push("_world_updated_locks", "_world_retained_lock"),
                        _set("_world_lock_index", "_world_lock_index + 1"),
                    ],
                ),
                _set("_world_knock_changes", {"srd_arcane_locks": "_world_updated_locks"}),
                _push(
                    "_spell_summary_rows",
                    {"label": "object_id", "value": "'Arcane Lock suppressed for 10 minutes'"},
                ),
            ],
            "on_false": [
                _require(
                    (
                        "len(_world_access.locks) > 0 or _world_access.stuck == true or "
                        "_world_access.barred == true"
                    ),
                    "This object has no supported access restriction.",
                ),
                _set("_world_remaining_locks", []),
                _set("_world_lock_index", 0),
                _set("_world_mundane_locks", "_world_access.locks"),
                _loop(
                    "_world_mundane_locks",
                    "_world_lock",
                    [
                        _when(
                            "_world_lock_index > 0",
                            [_push("_world_remaining_locks", "_world_lock")],
                        ),
                        _set("_world_lock_index", "_world_lock_index + 1"),
                    ],
                ),
                _set(
                    "_world_access_changes",
                    {"locks": "_world_remaining_locks", "stuck": False, "barred": False},
                ),
                _set("_world_access", "merge(_world_access, _world_access_changes)"),
                _set("_world_knock_changes", {"srd_object_access": "_world_access"}),
                _push(
                    "_spell_summary_rows",
                    {
                        "label": "object_id",
                        "value": "'One mundane lock unlocked; unstuck and unbarred'",
                    },
                ),
            ],
        },
        _set(
            "_world_knock_noise",
            {
                "srd_last_knock": {
                    "spell": "'knock'",
                    "caster": "_caster_state_id",
                    "tick": "_object_now",
                    "origin": "_object_scene.position",
                    "plane": "_object_scene.metadata.plane",
                    "audible_feet": "_world_profile.audible_feet",
                }
            },
        ),
        _set("_world_knock_changes", "merge(_world_knock_changes, _world_knock_noise)"),
        *_metadata_patch("_world_knock_changes"),
        _push(
            "_spell_detail_rows",
            {
                "label": "'Loud knock'",
                "value": "'Emanates from the actual target; audible up to 300 feet away.'",
            },
        ),
    ]


def _access_tool():
    """Open actual scenery through saved mundane restrictions and active spell sources."""
    return _tool_scene(
        "Open, close or speak an Arcane Lock password at an actual object within reach.",
        [
            _entity("actor_state"),
            _parameter("object_id"),
            _parameter("operation", "enum", enum_values=["open", "close", "speak_password"]),
            _optional("password", default=""),
        ],
        [
            _require(
                "actor_state.is_alive == true and actor_state.hp > 0", "A living actor is required."
            ),
            _set("_world_reach", 5),
            _when(
                "actor_state.melee_reach != null", [_set("_world_reach", "actor_state.melee_reach")]
            ),
            _when("operation == 'speak_password'", [_set("_world_reach", 5)]),
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_actor_state_id}",
                    "object_id": "{object_id}",
                    "range_feet": "{_world_reach}",
                    "requires_sight": False,
                },
                GEOMETRY_EXPORTS,
            ),
            _require(
                "_object_scene.metadata.srd_object_access != null",
                "This object needs admitted access facts.",
            ),
            *_now(),
            _set("_world_access", "_object_scene.metadata.srd_object_access"),
            _set("_world_locks", []),
            _when(
                "_object_scene.metadata.srd_arcane_locks != null",
                [_set("_world_locks", "_object_scene.metadata.srd_arcane_locks")],
            ),
            _set("_world_effective_lock", None),
            _loop(
                "_world_locks",
                "_world_lock",
                [
                    _require(
                        "_world_lock.kind == 'object_content_spell' and "
                        "_world_lock.spell == 'arcane_lock' and _world_lock.source == "
                        + repr("sylvia.srd-lite.bg3:spells:arcane_lock:" + SOURCE_PAGES[110]),
                        "The magical lock needs its actual pinned source.",
                    ),
                    _set("_world_effective_lock", "_world_lock"),
                ],
            ),
            _set("_world_magically_locked", False),
            _when(
                "_world_effective_lock != null",
                [
                    _set(
                        "_world_magically_locked",
                        (
                            "_object_now >= _world_effective_lock.suppressed_until_tick and "
                            "_object_now >= _world_effective_lock.password_until_tick and "
                            "_actor_state_id != _world_effective_lock.caster and "
                            "_actor_state_id not in _world_effective_lock.designated_creatures"
                        ),
                    ),
                ],
            ),
            {
                "condition": "operation == 'speak_password'",
                "on_true": [
                    _require(
                        (
                            "_world_effective_lock != null and password != '' and password == "
                            "_world_effective_lock.password"
                        ),
                        "Speak the actual spell password.",
                    ),
                    _require(
                        "'silenced' not in actor_state.conditions",
                        "The actor must be able to speak the password.",
                    ),
                    _set("_world_updated_locks", []),
                    _set("_world_lock_index", 0),
                    _loop(
                        "_world_locks",
                        "_world_lock",
                        [
                            _set("_world_retained_lock", "_world_lock"),
                            _when(
                                "_world_lock_index == len(_world_locks) - 1",
                                [
                                    _set(
                                        "_world_password_change",
                                        {"password_until_tick": "_object_now + 20"},
                                    ),
                                    _set(
                                        "_world_retained_lock",
                                        "merge(_world_lock, _world_password_change)",
                                    ),
                                ],
                            ),
                            _push("_world_updated_locks", "_world_retained_lock"),
                            _set("_world_lock_index", "_world_lock_index + 1"),
                        ],
                    ),
                    *_metadata_patch({"srd_arcane_locks": "_world_updated_locks"}),
                ],
                "on_false": [
                    _require(
                        "_world_magically_locked != true",
                        "Arcane Lock prevents this creature from opening or closing the object.",
                    ),
                    _when(
                        "operation == 'open'",
                        [
                            _require(
                                (
                                    "len(_world_access.locks) == 0 and _world_access.stuck != true "
                                    "and _world_access.barred != true"
                                ),
                                "A mundane access restriction still prevents opening.",
                            ),
                        ],
                    ),
                    _set("_world_access_change", {"closed": "operation == 'close'"}),
                    _set("_world_access", "merge(_world_access, _world_access_change)"),
                    _set("_world_movement", "_object_scene.movement"),
                    _set("_world_visibility", "_object_scene.visibility"),
                    _when(
                        (
                            "_world_access.kind == 'door' or _world_access.kind == 'gate' or "
                            "_world_access.kind == 'hatch'"
                        ),
                        [
                            _set("_world_movement", "'blocking'"),
                            _set("_world_visibility", "'opaque'"),
                            _when(
                                "operation == 'open'",
                                [
                                    _set("_world_movement", "'passable'"),
                                    _set("_world_visibility", "'clear'"),
                                ],
                            ),
                        ],
                    ),
                    _set("_world_access_metadata", {"srd_object_access": "_world_access"}),
                    _when(
                        "_object_scene.metadata.srd_object_container != null",
                        [
                            _set("_world_open_change", {"is_open": "operation == 'open'"}),
                            _set(
                                "_world_container",
                                "merge(_object_scene.metadata.srd_object_container, "
                                "_world_open_change)",
                            ),
                            _set(
                                "_world_container_change",
                                {"srd_object_container": "_world_container"},
                            ),
                            _set(
                                "_world_access_metadata",
                                "merge(_world_access_metadata, _world_container_change)",
                            ),
                        ],
                    ),
                    *_metadata_patch(
                        "_world_access_metadata",
                        movement="{_world_movement}",
                        visibility="{_world_visibility}",
                    ),
                ],
            },
        ],
    )


def _point_tool():
    return _tool_scene(
        "Validate a spell's chosen point against the actual caster, plane, map and clear path.",
        [
            _entity("caster_state"),
            _array("point"),
            _parameter("range_feet", "integer", minimum=0, maximum=100000),
        ],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_world_caster_scene", "_info_scene_entity"),
            _require(
                "_world_caster_scene.metadata.plane != null", "The caster needs its actual plane."
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_world_caster_scene.position}", "end": "{point}"},
                ["_scene_distance"],
            ),
            _require("_scene_distance <= range_feet", "The chosen point is outside spell range."),
            *_snapshot(),
            *[
                _require(
                    f"point[{axis}] >= state.spatial_scene.map.bounds.min[{axis}] and "
                    f"point[{axis}] <= state.spatial_scene.map.bounds.max[{axis}]",
                    "The chosen point must lie inside the actual admitted map.",
                )
                for axis in range(3)
            ],
            _call(
                "srd_information_barriers",
                {"start": "{_world_caster_scene.position}", "end": "{point}", "rule": "solid"},
                ["_information_blocked"],
            ),
            _require("_information_blocked != true", "Total Cover blocks the chosen point."),
            _set("_world_origin", "point"),
            _set("_world_plane", "_world_caster_scene.metadata.plane"),
            _set("_world_lighting", "_world_caster_scene.metadata.lighting"),
        ],
    )


def _extent(limit):
    return [
        *[
            _require(
                f"object_extent[{axis}] > 0 and object_extent[{axis}] <= {limit}",
                "The chosen object exceeds the source's actual size limit.",
            )
            for axis in range(3)
        ],
        _set("_world_new_volume", "object_extent[0] * object_extent[1] * object_extent[2]"),
    ]


def _new_scene_object(
    metadata,
    *,
    identity="object_output_id",
    extent="object_extent",
    movement="'passable'",
    visibility="'clear'",
    scene_kind="entity",
):
    """Append a unique scenery row and immediately admit the resulting neutral snapshot."""
    if scene_kind not in {"entity", "geometry"}:
        raise ValueError("Created scenery must be an entity or static geometry.")
    if scene_kind == "geometry":
        return [
            _require(f"{identity} != ''", "Choose a unique ID for the created scenery object."),
            *[
                _require(
                    f"{extent}[{axis}] > 0",
                    "Created geometry needs positive physical dimensions.",
                )
                for axis in range(3)
            ],
            *[
                _require(
                    f"_world_origin[{axis}] - {extent}[{axis}] * 0.5 >= "
                    f"state.spatial_scene.map.bounds.min[{axis}] and "
                    f"_world_origin[{axis}] + {extent}[{axis}] * 0.5 <= "
                    f"state.spatial_scene.map.bounds.max[{axis}]",
                    "Created geometry must fit the actual admitted map bounds.",
                )
                for axis in range(2)
            ],
            _require(
                "_world_origin[2] >= state.spatial_scene.map.bounds.min[2] and "
                f"_world_origin[2] + {extent}[2] <= state.spatial_scene.map.bounds.max[2]",
                "Created geometry must fit the actual admitted map bounds.",
            ),
            *[
                effect
                for collection in ("entities", "geometry", "zones")
                for effect in [
                    _set("_world_existing_rows", f"state.spatial_scene.{collection}"),
                    _loop(
                        "_world_existing_rows",
                        "_world_existing_row",
                        [
                            _require(
                                f"_world_existing_row.id != {identity}",
                                "Created geometry cannot replace an existing scene identity.",
                            ),
                        ],
                    ),
                ]
            ],
            _set("_world_created_metadata", metadata),
            _set("_world_created_marker", {"is_object": True}),
            _set(
                "_world_created_metadata", "merge(_world_created_metadata, _world_created_marker)"
            ),
            _set(
                "_world_created_geometry",
                {
                    "id": identity,
                    "map_id": "state.spatial_scene.map.id",
                    "volume": {
                        "kind": "'box'",
                        "min": [
                            f"_world_origin[0] - {extent}[0] * 0.5",
                            f"_world_origin[1] - {extent}[1] * 0.5",
                            "_world_origin[2]",
                        ],
                        "max": [
                            f"_world_origin[0] + {extent}[0] * 0.5",
                            f"_world_origin[1] + {extent}[1] * 0.5",
                            f"_world_origin[2] + {extent}[2]",
                        ],
                    },
                    "active": True,
                    "movement": movement,
                    "visibility": visibility,
                    "destroyed": {
                        "active": False,
                        "movement": "'passable'",
                        "visibility": "'clear'",
                    },
                    "metadata": "_world_created_metadata",
                },
            ),
            _set("_world_prior_geometry", "state.spatial_scene.geometry"),
            _push("_world_prior_geometry", "_world_created_geometry"),
            _set("state.spatial_scene.geometry", "_world_prior_geometry"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            *_snapshot(),
        ]
    return [
        _require(f"{identity} != ''", "Choose a unique ID for the created scenery object."),
        _set("_world_prior_entities", "state.spatial_scene.entities"),
        _loop(
            "_world_prior_entities",
            "_world_prior_entity",
            [
                _require(
                    f"_world_prior_entity.id != {identity}",
                    "A created object cannot replace an existing scene identity.",
                )
            ],
        ),
        _set("_world_created_metadata", metadata),
        _set("_world_created_marker", {"is_object": True}),
        _set("_world_created_metadata", "merge(_world_created_metadata, _world_created_marker)"),
        _set(
            "_world_created_entity",
            {
                "id": identity,
                "map_id": "state.spatial_scene.map.id",
                "position": "_world_origin",
                "footprint": {
                    "kind": "'box'",
                    "half_extents": [f"{extent}[0] * 0.5", f"{extent}[1] * 0.5"],
                },
                "height": f"{extent}[2]",
                "facing": [1, 0],
                "active": True,
                "movement": movement,
                "visibility": visibility,
                "metadata": "_world_created_metadata",
            },
        ),
        _push("_world_prior_entities", "_world_created_entity"),
        _set("state.spatial_scene.entities", "_world_prior_entities"),
        _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
        *_snapshot(),
    ]


def _creation():
    """Create a previously seen admitted form/material with the shortest source lifetime."""
    return [
        _require("object_id != ''", "Choose a retained previously seen object pattern."),
        _set("_world_pattern", None),
        _set("_world_pattern_fiction", "''"),
        _set("_world_pattern_movement", None),
        _set("_world_pattern_visibility", None),
        _when(
            "caster_state.srd_object_patterns != null",
            [
                _set("_world_seen_patterns", "caster_state.srd_object_patterns"),
                _loop(
                    "_world_seen_patterns",
                    "_world_seen_pattern",
                    [
                        _when(
                            "_world_seen_pattern.object_id == object_id",
                            [
                                _set("_world_pattern", "_world_seen_pattern.material"),
                                _set("_world_pattern_fiction", "_world_seen_pattern.fiction"),
                                _when(
                                    "_world_seen_pattern.movement != null",
                                    [
                                        _set(
                                            "_world_pattern_movement",
                                            "_world_seen_pattern.movement",
                                        )
                                    ],
                                ),
                                _when(
                                    "_world_seen_pattern.visibility != null",
                                    [
                                        _set(
                                            "_world_pattern_visibility",
                                            "_world_seen_pattern.visibility",
                                        )
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _require(
            "_world_pattern != null", "Creation requires a form and material the caster has seen."
        ),
        _require(
            "_world_pattern_movement != null and _world_pattern_visibility != null",
            "The seen pattern needs its actual physical movement and visibility geometry.",
        ),
        _set("_world_creation_materials", "_world_pattern.materials"),
        _when(
            "_world_creation_materials == null",
            [
                _set("_world_creation_materials", []),
                _push("_world_creation_materials", "_world_pattern.material"),
            ],
        ),
        _require(
            "len(object_materials) == 0 or len(object_materials) == len(_world_creation_materials)",
            "Creation uses the admitted pattern's actual material mixture.",
        ),
        _loop(
            "object_materials",
            "_world_chosen_material",
            [
                _require(
                    "_world_chosen_material in _world_creation_materials",
                    "Creation uses the admitted pattern's actual material mixture.",
                ),
            ],
        ),
        _set("_world_creation_material", "_world_pattern.material"),
        _set("_world_creation_durations", CREATION_ROUNDS),
        _set("_world_creation_rounds", "_world_creation_durations[_world_creation_material]"),
        _loop(
            "_world_creation_materials",
            "_world_mixture_material",
            [
                _set(
                    "_world_mixture_duration", "_world_creation_durations[_world_mixture_material]"
                ),
                _set(
                    "_world_creation_rounds", "min(_world_creation_rounds, _world_mixture_duration)"
                ),
            ],
        ),
        _set(
            "_world_max_extent",
            (
                "_world_profile.base_cube_feet + "
                "_world_profile.cube_feet_per_level * (slot_level - "
                "_world_profile.base_level)"
            ),
        ),
        *_extent("_world_max_extent"),
        _call(
            "resolve_srd_object_content_point",
            {
                "caster_state": "{_caster_state_id}",
                "point": "{object_point}",
                "range_feet": "{_world_range}",
            },
            ["_world_origin", "_world_plane", "_world_lighting"],
        ),
        _call(
            "format_srd_information_projection",
            {"message": "{_caster_state_id}:creation:{object_output_id}"},
            ["_info_projection_message"],
        ),
        _set("_world_source_id", "_info_projection_message"),
        *_new_scene_object(
            {
                "plane": "_world_plane",
                "lighting": "_world_lighting",
                "fiction": "_world_pattern_fiction",
                "srd_created_object": {
                    "spell": "'creation'",
                    "source_id": "_world_source_id",
                    "spell_level": "slot_level",
                    "dispel_ongoing": True,
                    "caster": "_caster_state_id",
                    "source_page": 121,
                    "source_sha256": repr(SOURCE_PAGES[121]),
                    "started_tick": "_object_now",
                    "expires_tick": "_object_now + _world_creation_rounds * 2",
                    "material": "_world_creation_material",
                    "materials": "_world_creation_materials",
                    "component_forbidden": True,
                },
            },
            movement="_world_pattern_movement",
            visibility="_world_pattern_visibility",
        ),
        _push(
            "_spell_summary_rows",
            {"label": "object_output_id", "value": "'Created admitted form and material'"},
        ),
        _push(
            "_spell_detail_rows",
            {"label": "'Duration in rounds'", "value": "_world_creation_rounds"},
        ),
    ]


def _supply_record(food, water):
    return {
        "spell": "'create_food_and_water'",
        "caster": "_caster_state_id",
        "source_page": 120,
        "source_sha256": repr(SOURCE_PAGES[120]),
        "started_tick": "_object_now",
        "food_spoils_tick": "_object_now + 28800",
        "food_pounds": food,
        "water_gallons": water,
    }


def _supply_container_tool():
    """Fill actual open containers from the fixed remaining source quantities."""
    return _tool_scene(
        "Place fixed created supply into an actual qualified open container.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("food_remaining", "number", minimum=0, maximum=45),
            _parameter("water_remaining", "number", minimum=0, maximum=30),
        ],
        [
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "range_feet": 30,
                    "requires_sight": False,
                },
                GEOMETRY_EXPORTS,
            ),
            _require(
                "_object_scene.metadata.srd_object_container != null and "
                "_object_scene.metadata.srd_object_container.is_open == true",
                "Created supply requires an admitted open container.",
            ),
            _require(
                "_object_scene.metadata.srd_created_supply == null",
                "Consume or separately place an existing created supply first.",
            ),
            _set("_world_container", "_object_scene.metadata.srd_object_container"),
            _set("_world_food_capacity", 0),
            _set("_world_food_existing", 0),
            _when(
                "_world_container.food_capacity_pounds != null",
                [_set("_world_food_capacity", "_world_container.food_capacity_pounds")],
            ),
            _when(
                "_world_container.food_pounds != null",
                [_set("_world_food_existing", "_world_container.food_pounds")],
            ),
            _set(
                "_world_food_placed",
                "min(food_remaining, max(0, _world_food_capacity - _world_food_existing))",
            ),
            _set(
                "_world_water_placed",
                "min(water_remaining, max(0, "
                "_world_container.capacity_gallons - _world_container.water_gallons))",
            ),
            *_now(),
            *_metadata_patch(
                {"srd_created_supply": _supply_record("_world_food_placed", "_world_water_placed")}
            ),
            _set("_world_food_remaining", "food_remaining - _world_food_placed"),
            _set("_world_water_remaining", "water_remaining - _world_water_placed"),
        ],
    )


def _food():
    """Distribute the printed supply into actual containers and place any remainder on ground."""
    return [
        _set("_world_food_remaining", "_world_profile.food_pounds"),
        _set("_world_water_remaining", "_world_profile.water_gallons"),
        _set("_world_container_ids", "object_container_ids"),
        _when(
            "object_id != ''",
            [
                _require("object_id not in _world_container_ids", "Select each container once."),
                _push("_world_container_ids", "object_id"),
            ],
        ),
        _loop(
            "_world_container_ids",
            "_world_container_id",
            [
                _call(
                    "place_srd_created_supply_container",
                    {
                        "caster_state": "{_caster_state_id}",
                        "object_id": "{_world_container_id}",
                        "food_remaining": "{_world_food_remaining}",
                        "water_remaining": "{_world_water_remaining}",
                    },
                    ["_world_food_remaining", "_world_water_remaining"],
                ),
            ],
        ),
        _when(
            "_world_food_remaining > 0 or _world_water_remaining > 0",
            [
                *_extent("1000000"),
                _call(
                    "resolve_srd_object_content_point",
                    {
                        "caster_state": "{_caster_state_id}",
                        "point": "{object_point}",
                        "range_feet": "{_world_range}",
                    },
                    ["_world_origin", "_world_plane", "_world_lighting"],
                ),
                *_new_scene_object(
                    {
                        "is_object": True,
                        "plane": "_world_plane",
                        "lighting": "_world_lighting",
                        "fiction": "object_form",
                        "srd_created_supply": _supply_record(
                            "_world_food_remaining", "_world_water_remaining"
                        ),
                    }
                ),
            ],
        ),
        _push(
            "_spell_summary_rows",
            {
                "label": "'Created supply'",
                "value": "'45 pounds of nourishing food and 30 gallons of clean water'",
            },
        ),
    ]


def _water_cube_tool():
    """Save a source-sized cube from an actual selected face center and orientation."""
    effects = [
        _call(
            "resolve_srd_object_content_point",
            {
                "caster_state": "{_caster_state_id}",
                "point": "{point}",
                "range_feet": 30,
            },
            ["_world_origin", "_world_plane", "_world_lighting"],
        ),
        _set("_world_cube_side", "30 + 5 * (slot_level - 1)"),
    ]
    for axis in range(3):
        effects += [
            _set(f"_world_cube_min_{axis}", f"point[{axis}] - _world_cube_side * 0.5"),
            _set(f"_world_cube_max_{axis}", f"point[{axis}] + _world_cube_side * 0.5"),
        ]
    for face, axis, sign in [
        ("east", 0, 1),
        ("west", 0, -1),
        ("north", 1, 1),
        ("south", 1, -1),
        ("up", 2, 1),
        ("down", 2, -1),
    ]:
        effects += [
            _when(
                f"face == '{face}'",
                [
                    _set(
                        f"_world_cube_min_{axis}",
                        f"point[{axis}]" if sign > 0 else f"point[{axis}] - _world_cube_side",
                    ),
                    _set(
                        f"_world_cube_max_{axis}",
                        f"point[{axis}] + _world_cube_side" if sign > 0 else f"point[{axis}]",
                    ),
                ],
            )
        ]
    effects += [
        *[
            _require(
                f"_world_cube_min_{axis} >= state.spatial_scene.map.bounds.min[{axis}] and "
                f"_world_cube_max_{axis} <= state.spatial_scene.map.bounds.max[{axis}]",
                "Admit a map containing the actual full source cube.",
            )
            for axis in range(3)
        ],
        *_now(),
        _set(
            "_world_cube",
            {
                "kind": "'object_content_spell'",
                "spell": "'create_or_destroy_water'",
                "mode": "mode",
                "caster": "_caster_state_id",
                "slot_level": "slot_level",
                "dispel_ongoing": False,
                "origin": "point",
                "face": "face",
                "plane": "_world_plane",
                "volume": {
                    "kind": "'box'",
                    "min": [f"_world_cube_min_{a}" for a in range(3)],
                    "max": [f"_world_cube_max_{a}" for a in range(3)],
                },
                "started_tick": "_object_now",
                "source_sha256": repr(SOURCE_PAGES[121]),
            },
        ),
        _set("_world_cube_records", []),
        _when("state.turn_effects != null", [_set("_world_cube_records", "state.turn_effects")]),
        _push("_world_cube_records", "_world_cube"),
        _set("state.turn_effects", "_world_cube_records"),
    ]
    return _tool_scene(
        "Retain the actual printed rain or destroyed-fog cube.",
        [
            _entity("caster_state"),
            _array("point"),
            _parameter("slot_level", "integer", minimum=1, maximum=9),
            _parameter("mode", "enum", enum_values=["rain", "destroy_fog"]),
            _parameter(
                "face", "enum", enum_values=["east", "west", "north", "south", "up", "down"]
            ),
        ],
        effects,
    )


def _rain_tool(*, object_visibility=False):
    """Extinguish actual exposed ordinary flames wholly inside the printed cube."""
    bounds = [
        "_object_scene.position[0] - _object_scene.footprint.half_extents[0]",
        "_object_scene.position[1] - _object_scene.footprint.half_extents[1]",
        "_object_scene.position[2]",
    ]
    maxima = [
        "_object_scene.position[0] + _object_scene.footprint.half_extents[0]",
        "_object_scene.position[1] + _object_scene.footprint.half_extents[1]",
        "_object_scene.position[2] + _object_scene.height",
    ]
    return _tool_scene(
        "Consume one actual ordinary flame against a retained source rain cube.",
        [
            _parameter("object_id"),
            _parameter("record", "object"),
        ],
        [
            *_lookup_object(),
            _require(
                "record.kind == 'object_content_spell' and record.spell == "
                "'create_or_destroy_water' and record.mode == 'rain' and "
                f"record.source_sha256 == {SOURCE_PAGES[121]!r}",
                "Rain must come from the retained exact spell source.",
            ),
            *(
                _held_object_geometry("{record.caster}")
                if object_visibility
                else [
                    _require(
                        "_object_scene.metadata.carried_by == null and "
                        "_object_scene.metadata.worn_by == null",
                        "Held flames require actual holder geometry before rain.",
                    ),
                ]
            ),
            _set("_world_extinguished", False),
            _when(
                "_object_scene.metadata.plane == record.plane and "
                "_object_scene.metadata.srd_ordinary_flame.burning == true and "
                "_object_scene.metadata.srd_ordinary_flame.exposed == true",
                [
                    _set("_world_flame_overlaps", True),
                    _set("_world_flame_inside", True),
                    *[
                        _when(
                            f"{maxima[a]} < record.volume.min[{a}] or "
                            f"{bounds[a]} > record.volume.max[{a}]",
                            [_set("_world_flame_overlaps", False)],
                        )
                        for a in range(3)
                    ],
                    *[
                        _when(
                            f"{bounds[a]} < record.volume.min[{a}] or "
                            f"{maxima[a]} > record.volume.max[{a}]",
                            [_set("_world_flame_inside", False)],
                        )
                        for a in range(3)
                    ],
                    _require(
                        "_world_flame_overlaps != true or _world_flame_inside == true",
                        "Admit separate flame sections when the rain only covers part of a flame.",
                    ),
                    _when(
                        "_world_flame_inside == true",
                        [
                            _call(
                                "srd_information_barriers",
                                {
                                    "start": "{record.origin}",
                                    "end": "{_object_scene.position}",
                                    "rule": "solid",
                                },
                                ["_information_blocked"],
                            ),
                            _when(
                                "_information_blocked != true",
                                [
                                    _set("_world_flame_changes", {"burning": False}),
                                    _set(
                                        "_world_flame",
                                        "merge(_object_scene.metadata.srd_ordinary_flame, "
                                        "_world_flame_changes)",
                                    ),
                                    *_metadata_patch({"srd_ordinary_flame": "_world_flame"}),
                                    _set("_world_extinguished", True),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _water(*, fog=False):
    """Change source gallons in real containers or consume a retained fixed cube."""
    return [
        _when(
            "object_mode == 'create_water' or object_mode == 'destroy_water'",
            [
                _require(
                    (
                        "_object_scene.metadata.srd_object_container != null and "
                        "_object_scene.metadata.srd_object_container.is_open == true"
                    ),
                    "Choose an admitted open container.",
                ),
                _set("_world_container", "_object_scene.metadata.srd_object_container"),
                _set(
                    "_world_gallon_limit",
                    (
                        "_world_profile.base_gallons + _world_profile.gallons_per_level * "
                        "(slot_level - _world_profile.base_level)"
                    ),
                ),
                _set("_world_gallons", "_world_gallon_limit"),
                _when("object_quantity > 0", [_set("_world_gallons", "object_quantity")]),
                _require(
                    "_world_gallons <= _world_gallon_limit",
                    "The source cannot change that many gallons at this slot level.",
                ),
                _set("_world_supply_water", 0),
                _set("_world_water_metadata", {}),
                _when(
                    "_object_scene.metadata.srd_created_supply != null",
                    [
                        _set(
                            "_world_supply_water",
                            "_object_scene.metadata.srd_created_supply.water_gallons",
                        ),
                    ],
                ),
                _set("_world_actual_water", "_world_container.water_gallons + _world_supply_water"),
                _set("_world_after_water", "max(0, _world_actual_water - _world_gallons)"),
                _set("_world_after_supply_water", "max(0, _world_supply_water - _world_gallons)"),
                _when(
                    "object_mode == 'create_water'",
                    [
                        _set(
                            "_world_after_water",
                            "min(_world_container.capacity_gallons, "
                            "_world_actual_water + _world_gallons)",
                        ),
                        _set("_world_after_supply_water", "_world_supply_water"),
                    ],
                ),
                _set(
                    "_world_water_change",
                    {"water_gallons": "_world_after_water - _world_after_supply_water"},
                ),
                _set("_world_container", "merge(_world_container, _world_water_change)"),
                _set("_world_water_metadata", {"srd_object_container": "_world_container"}),
                _when(
                    "_object_scene.metadata.srd_created_supply != null",
                    [
                        _set(
                            "_world_supply_change", {"water_gallons": "_world_after_supply_water"}
                        ),
                        _set(
                            "_world_supply",
                            "merge(_object_scene.metadata.srd_created_supply, "
                            "_world_supply_change)",
                        ),
                        _set("_world_water_extra", {"srd_created_supply": "_world_supply"}),
                        _set(
                            "_world_water_metadata",
                            "merge(_world_water_metadata, _world_water_extra)",
                        ),
                    ],
                ),
                *_metadata_patch("_world_water_metadata"),
                _push(
                    "_spell_summary_rows",
                    {"label": "'Actual water in gallons'", "value": "_world_after_water"},
                ),
            ],
        ),
        _when(
            "object_mode == 'rain' or object_mode == 'destroy_fog'",
            [
                _call(
                    "retain_srd_water_cube",
                    {
                        "caster_state": "{_caster_state_id}",
                        "point": "{object_point}",
                        "slot_level": "{slot_level}",
                        "mode": "{object_mode}",
                        "face": "{object_cube_face}",
                    },
                    ["_world_cube"],
                ),
                _when(
                    "object_mode == 'rain'",
                    [
                        _set("_world_rain_entities", "state.spatial_scene.entities"),
                        _set("_world_flames_extinguished", 0),
                        _loop(
                            "_world_rain_entities",
                            "_world_rain_entity",
                            [
                                _when(
                                    "_world_rain_entity.active == true and "
                                    "_world_rain_entity.metadata.srd_ordinary_flame != null",
                                    [
                                        _call(
                                            "extinguish_srd_rain_flame",
                                            {
                                                "object_id": "{_world_rain_entity.id}",
                                                "record": "{_world_cube}",
                                            },
                                            ["_world_extinguished"],
                                        ),
                                        _when(
                                            "_world_extinguished == true",
                                            [
                                                _set(
                                                    "_world_flames_extinguished",
                                                    "_world_flames_extinguished + 1",
                                                )
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        _push(
                            "_spell_summary_rows",
                            {
                                "label": "'Extinguished exposed ordinary flames'",
                                "value": "_world_flames_extinguished",
                            },
                        ),
                    ],
                ),
                _when(
                    "object_mode == 'destroy_fog'",
                    (
                        [
                            _call(
                                "destroy_srd_environment_fog",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "record": "{_world_cube}",
                                },
                            )
                        ]
                        if fog
                        else [_require("false", "Fog clipping requires its environment consumer.")]
                    ),
                ),
            ],
        ),
    ]


def _material_volume():
    return [
        _require(
            (
                "_object_scene.metadata.srd_object_material != null and "
                "_object_scene.footprint.kind == 'box'"
            ),
            "Admit the actual object's material and box dimensions.",
        ),
        _set("_world_material", "_object_scene.metadata.srd_object_material"),
        _set(
            "_world_existing_volume",
            (
                "_object_scene.footprint.half_extents[0] * 2 * "
                "_object_scene.footprint.half_extents[1] * 2 * "
                "_object_scene.height"
            ),
        ),
    ]


def _fabricate_cells_tool():
    """Qualify at most eight face-connected five-foot source cells in the actual scene."""
    connection = [
        _set("_world_new_connected", False),
        _loop(
            "_world_connected_cells",
            "_world_connected_cell",
            [
                _when(
                    "abs(_world_cell[0] - _world_connected_cell[0]) + "
                    "abs(_world_cell[1] - _world_connected_cell[1]) + "
                    "abs(_world_cell[2] - _world_connected_cell[2]) == 5",
                    [_set("_world_new_connected", True)],
                ),
            ],
        ),
        _set("_world_already_connected", False),
        _loop(
            "_world_connected_cells",
            "_world_connected_cell",
            [
                _when(
                    "_world_cell[0] == _world_connected_cell[0] and "
                    "_world_cell[1] == _world_connected_cell[1] and "
                    "_world_cell[2] == _world_connected_cell[2]",
                    [_set("_world_already_connected", True)],
                ),
            ],
        ),
        _when(
            "_world_new_connected == true and _world_already_connected != true",
            [_push("_world_connected_cells", "_world_cell")],
        ),
    ]
    return _tool_scene(
        "Validate actual source-sized connected Fabricate cells.",
        [
            _entity("caster_state"),
            _parameter(
                "cells",
                "array",
                items={"type": "array", "items": {"type": "number"}},
                min_items=1,
                max_items=8,
            ),
        ],
        [
            _set("_world_cells", "cells"),
            _set("_world_first_cell", "cells[0]"),
            _require("len(_world_first_cell) == 3", "Each actual cell needs three coordinates."),
            _set("_world_checked_cells", []),
            _loop(
                "_world_cells",
                "_world_cell",
                [
                    _require("len(_world_cell) == 3", "Each actual cell needs three coordinates."),
                    *[
                        _require(
                            f"(_world_cell[{a}] - _world_first_cell[{a}]) % 5 == 0",
                            "Connected Fabricate cells share the actual five-foot lattice.",
                        )
                        for a in range(3)
                    ],
                    _loop(
                        "_world_checked_cells",
                        "_world_checked_cell",
                        [
                            _require(
                                "_world_cell[0] != _world_checked_cell[0] or "
                                "_world_cell[1] != _world_checked_cell[1] or "
                                "_world_cell[2] != _world_checked_cell[2]",
                                "Select each actual cell once.",
                            ),
                        ],
                    ),
                    _push("_world_checked_cells", "_world_cell"),
                    _call(
                        "resolve_srd_object_content_point",
                        {
                            "caster_state": "{_caster_state_id}",
                            "point": "{_world_cell}",
                            "range_feet": 120,
                        },
                        ["_world_plane", "_world_lighting"],
                    ),
                ],
            ),
            _set("_world_connected_cells", []),
            _push("_world_connected_cells", "_world_first_cell"),
            *[_loop("_world_cells", "_world_cell", deepcopy(connection)) for _ in range(7)],
            _require(
                "len(_world_connected_cells) == len(_world_cells)",
                "Fabricate requires face-connected actual five-foot cubes.",
            ),
            _set("_world_new_volume", "len(_world_cells) * 125"),
        ],
    )


def _fabricate():
    """Convert actual seen raw stock, preserving material quantity and quality."""
    return [
        *_material_volume(),
        _require(
            (
                "_world_material.raw_stock == true and _world_material.magical != "
                "true and _object_scene.metadata.srd_created_object == null and "
                "_object_scene.metadata.srd_arcane_locks == null"
            ),
            "Fabricate needs raw material and cannot make or convert a magical item.",
        ),
        _set("_world_fabricate_limit", "_world_profile.mineral_cube_feet"),
        _when(
            "_world_material.material == 'vegetable'",
            [_set("_world_fabricate_limit", "_world_profile.vegetable_cube_feet")],
        ),
        _when("object_fabricate_layout == 'box'", [*_extent("_world_fabricate_limit")]),
        _when(
            "object_fabricate_layout == 'connected_cubes'",
            [
                _require(
                    "_world_material.material == 'vegetable'",
                    "Mineral Fabricate uses its printed Medium five-foot cube.",
                ),
                _require("len(object_cells) > 0", "Select the actual connected cells."),
                _call(
                    "validate_srd_fabricate_cells",
                    {
                        "caster_state": "{_caster_state_id}",
                        "cells": "{object_cells}",
                    },
                    ["_world_new_volume", "_world_plane", "_world_lighting"],
                ),
            ],
        ),
        _require(
            "_world_new_volume <= _world_existing_volume",
            "Fabricate requires sufficient actual raw material.",
        ),
        _set("_world_required_artisan", "''"),
        _when(
            "object_craft == 'weapons' or object_craft == 'armor'",
            [_set("_world_required_artisan", "'smiths_tools'")],
        ),
        _when(
            "object_craft == 'fine_jewelry'", [_set("_world_required_artisan", "'jewelers_tools'")]
        ),
        _require(
            "_world_required_artisan == '' or _world_required_artisan in artisan_proficiencies",
            (
                "High-skill Fabricate requires the actual caster's matching "
                "Artisan's Tools proficiency."
            ),
        ),
        _require("object_form != ''", "Describe the chosen physical form."),
        _set("_world_output_group_id", "object_id"),
        _when("object_output_id != ''", [_set("_world_output_group_id", "object_output_id")]),
        _set("_world_fabricated_movement", "_object_scene.movement"),
        _set("_world_fabricated_visibility", "_object_scene.visibility"),
        _set(
            "_world_fabricated_metadata",
            {
                "srd_object_material": "_world_material",
                "fiction": "object_form",
                "srd_fabricated": {
                    "spell": "'fabricate'",
                    "caster": "_caster_state_id",
                    "tick": "_object_now",
                    "output_group_id": "_world_output_group_id",
                    "input_volume": "_world_new_volume",
                    "group_output_volume": "_world_new_volume",
                    "output_volume": "_world_new_volume",
                    "source_sha256": repr(SOURCE_PAGES[129]),
                    "dispel_ongoing": False,
                },
                "plane": "_object_scene.metadata.plane",
                "lighting": "_object_scene.metadata.lighting",
            },
        ),
        _set("_world_material_changes", {"raw_stock": False}),
        _set("_world_fabricated_material", "merge(_world_material, _world_material_changes)"),
        _set(
            "_world_fabricated_material_metadata",
            {"srd_object_material": "_world_fabricated_material"},
        ),
        _set(
            "_world_fabricated_metadata",
            "merge(_world_fabricated_metadata, _world_fabricated_material_metadata)",
        ),
        _when(
            "object_fabricate_layout == 'box' and _world_new_volume == _world_existing_volume",
            [
                _set(
                    "_world_footprint",
                    {
                        "kind": "'box'",
                        "half_extents": ["object_extent[0] * 0.5", "object_extent[1] * 0.5"],
                    },
                ),
                *_metadata_patch(
                    "_world_fabricated_metadata",
                    footprint="{_world_footprint}",
                    height="{_world_height}",
                ),
            ],
        ),
        _when(
            "object_fabricate_layout != 'box' or _world_new_volume < _world_existing_volume",
            [
                _require("object_output_id != ''", "Choose a unique fabricated output scene ID."),
                _set("_world_stock_remaining", "_world_existing_volume - _world_new_volume"),
                _set("_world_stock_inverse", "_world_existing_volume ** -1"),
                _set("_world_stock_fraction", "_world_stock_remaining * _world_stock_inverse"),
                _set(
                    "_world_stock_height",
                    "_object_scene.height * _world_stock_fraction",
                ),
                _set(
                    "_world_stock_position",
                    [
                        "_object_scene.position[0]",
                        "_object_scene.position[1]",
                        "_object_scene.position[2] + _object_scene.height - _world_stock_height",
                    ],
                ),
                _when(
                    "_world_stock_height > 0",
                    [
                        *_metadata_patch(
                            {}, height="{_world_stock_height}", position="{_world_stock_position}"
                        ),
                    ],
                ),
                _when("_world_stock_height <= 0", [*_metadata_patch({}, active=False)]),
                _when(
                    "object_fabricate_layout == 'box'",
                    [
                        _call(
                            "resolve_srd_object_content_point",
                            {
                                "caster_state": "{_caster_state_id}",
                                "point": "{object_point}",
                                "range_feet": 120,
                            },
                            ["_world_origin"],
                        ),
                        *_new_scene_object(
                            "_world_fabricated_metadata",
                            movement="_world_fabricated_movement",
                            visibility="_world_fabricated_visibility",
                        ),
                    ],
                ),
                _when(
                    "object_fabricate_layout == 'connected_cubes'",
                    [
                        _set("_world_output_cells", "object_cells"),
                        _set("_world_cell_index", 0),
                        _set("_world_cell_extent", [5, 5, 5]),
                        _loop(
                            "_world_output_cells",
                            "_world_cell",
                            [
                                _call(
                                    "format_srd_information_projection",
                                    {
                                        "message": "{object_output_id}:section:{_world_cell_index}",
                                    },
                                    ["_info_projection_message"],
                                ),
                                _set("_world_section_id", "_info_projection_message"),
                                _when(
                                    "_world_cell_index == 0",
                                    [_set("_world_section_id", "object_output_id")],
                                ),
                                _set("_world_origin", "_world_cell"),
                                _set(
                                    "_world_section_patch",
                                    {
                                        "section": "_world_cell_index",
                                        "output_volume": 125,
                                    },
                                ),
                                _set(
                                    "_world_section_source",
                                    "merge(_world_fabricated_metadata."
                                    "srd_fabricated, _world_section_patch)",
                                ),
                                _set(
                                    "_world_section_source_patch",
                                    {"srd_fabricated": "_world_section_source"},
                                ),
                                _set(
                                    "_world_section_metadata",
                                    "merge(_world_fabricated_metadata, "
                                    "_world_section_source_patch)",
                                ),
                                *_new_scene_object(
                                    "_world_section_metadata",
                                    identity="_world_section_id",
                                    extent="_world_cell_extent",
                                    movement="_world_fabricated_movement",
                                    visibility="_world_fabricated_visibility",
                                ),
                                _set("_world_cell_index", "_world_cell_index + 1"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _push(
            "_spell_summary_rows",
            {"label": "object_id", "value": "'Raw material fabricated into chosen form'"},
        ),
    ]


def _stone_shape():
    """Reshape one admitted stone object/section without changing a whole large structure."""
    return [
        *_material_volume(),
        _require(
            (
                "_world_material.material == 'stone_or_crystal' and "
                "_object_scene.metadata.material == 'stone'"
            ),
            "Stone Shape requires actual stone, rather than crystal or other mineral.",
        ),
        *[
            _require(
                f"_object_scene.footprint.half_extents[{axis}] * 2 <= "
                "_world_profile.maximum_section_feet",
                "Admit a stone section no larger than five feet in each dimension.",
            )
            for axis in range(2)
        ],
        _require(
            "_object_scene.height <= _world_profile.maximum_section_feet",
            "Admit a stone section no larger than five feet in each dimension.",
        ),
        *_extent("_world_profile.maximum_section_feet"),
        _require(
            "_world_new_volume <= _world_existing_volume",
            "The reshaped stone cannot create additional material.",
        ),
        _require("object_form != ''", "Describe the stone's chosen form."),
        _set(
            "_world_footprint",
            {"kind": "'box'", "half_extents": ["object_extent[0] * 0.5", "object_extent[1] * 0.5"]},
        ),
        _set("_world_movement", "'blocking'"),
        _set("_world_visibility", "'opaque'"),
        _when(
            "object_shape == 'passage'",
            [_set("_world_movement", "'passable'"), _set("_world_visibility", "'clear'")],
        ),
        *_metadata_patch(
            {
                "fiction": "object_form",
                "srd_stone_shape": {
                    "caster": "_caster_state_id",
                    "tick": "_object_now",
                    "hinges": "object_hinges",
                    "latches": "object_latches",
                    "shape": "object_shape",
                },
            },
            footprint="{_world_footprint}",
            height="{_world_height}",
            movement="{_world_movement}",
            visibility="{_world_visibility}",
        ),
        _push(
            "_spell_summary_rows",
            {"label": "object_id", "value": "'Admitted stone object or section reshaped'"},
        ),
    ]


def _casting_tool(*, fog=False):
    effects = [
        _require(ACTIVE, "A capable living caster is required."),
        {
            "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
            "store_as": "_world_profile",
        },
        _set("_world_range", "_world_profile.range_feet"),
        _when("_world_profile.range_kind == 'touch'", [_set("_world_range", 5)]),
        _when(
            "_world_profile.range_kind == 'touch' and caster_state.melee_reach != null",
            [_set("_world_range", "caster_state.melee_reach")],
        ),
        _set("_world_requires_sight", "spell == 'knock' or spell == 'fabricate'"),
        _when(
            "spell != 'creation' and spell != 'create_food_and_water' and "
            "(spell != 'create_or_destroy_water' or object_mode == 'create_water' or "
            "object_mode == 'destroy_water')",
            [
                _require("object_id != ''", "Choose an actual object scene ID."),
                _call(
                    "resolve_srd_object_content_geometry",
                    {
                        "caster_state": "{_caster_state_id}",
                        "object_id": "{object_id}",
                        "range_feet": "{_world_range}",
                        "requires_sight": "{_world_requires_sight}",
                    },
                    GEOMETRY_EXPORTS,
                ),
            ],
        ),
        *_now(),
        _set("_spell_summary_rows", []),
        _set("_spell_detail_rows", []),
        _set("_world_height", "object_extent[2]"),
        _when("spell == 'mending'", _mending()),
        _when("spell == 'arcane_lock'", _arcane_lock()),
        _when("spell == 'knock'", _knock()),
        _when("spell == 'creation'", _creation()),
        _when("spell == 'create_food_and_water'", _food()),
        _when("spell == 'create_or_destroy_water'", _water(fog=fog)),
        _when("spell == 'fabricate'", _fabricate()),
        _when("spell == 'stone_shape'", _stone_shape()),
    ]
    return _tool_scene(
        (
            "Apply source-owned object changes after the shared spell casting "
            "owner pays validated costs."
        ),
        [
            _entity("caster_state"),
            _optional("object_id", default=""),
            _parameter("spell"),
            _parameter("slot_level", "integer", minimum=0, maximum=9),
            _optional("artisan_proficiencies", "array", [], items={"type": "string"}, max_items=64),
            *deepcopy(CAST_CHOICES),
        ],
        effects,
    )


def _lifecycle_tools():
    """Expire source objects and spoil only unconsumed food through the real shared clock."""
    one = _tool_scene(
        "Refresh one active source-owned scenery record.",
        [_parameter("object_id")],
        [
            *_lookup_object(),
            *_now(),
            _set("_world_created", None),
            _when(
                "_object_scene.metadata.srd_created_object != null",
                [_set("_world_created", "_object_scene.metadata.srd_created_object")],
            ),
            _when(
                "_world_created != null",
                [
                    _require(
                        "_world_created.spell == 'creation' and "
                        f"_world_created.source_sha256 == {SOURCE_PAGES[121]!r}",
                        "The created object needs its exact spell source.",
                    ),
                    _when(
                        "_object_now >= _world_created.expires_tick",
                        [
                            {
                                "spatial_mutation": {
                                    "scene": "state.spatial_scene",
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "expected_revision": "{state.spatial_scene.map.revision}",
                                    "target": {"kind": "entity", "id": "{object_id}"},
                                    "patch": {"active": False},
                                },
                                "store_as": "_world_creation_expired",
                            },
                        ],
                    ),
                ],
            ),
            _set("_world_supply", None),
            _when(
                "_object_scene.metadata.srd_created_supply != null",
                [_set("_world_supply", "_object_scene.metadata.srd_created_supply")],
            ),
            _when(
                "_world_supply != null",
                [
                    _require(
                        "_world_supply.spell == 'create_food_and_water' and "
                        f"_world_supply.source_sha256 == {SOURCE_PAGES[120]!r}",
                        "The created supply needs its exact spell source.",
                    ),
                    _when(
                        (
                            "_world_supply.food_pounds > 0 and _object_now >= "
                            "_world_supply.food_spoils_tick"
                        ),
                        [
                            _set(
                                "_world_supply_changes",
                                {
                                    "food_pounds": 0,
                                    "spoiled_food_pounds": "_world_supply.food_pounds",
                                },
                            ),
                            _set("_world_supply", "merge(_world_supply, _world_supply_changes)"),
                            *_metadata_patch({"srd_created_supply": "_world_supply"}),
                        ],
                    ),
                ],
            ),
        ],
    )
    refresh = _tool_scene(
        "Refresh only object spell sources against the shared elapsed clock.",
        [],
        [
            _set("_world_scene_entities", "state.spatial_scene.entities"),
            _loop(
                "_world_scene_entities",
                "_world_scene_entity",
                [
                    _when(
                        (
                            "_world_scene_entity.active == true and "
                            "(_world_scene_entity.metadata.srd_created_object != null or "
                            "_world_scene_entity.metadata.srd_created_supply != null)"
                        ),
                        [
                            _call(
                                "refresh_srd_object_content_one",
                                {"object_id": "{_world_scene_entity.id}"},
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    consume = _tool_scene(
        "Consume admitted nourishing food or clean water; this does not heal creatures.",
        [
            _parameter("object_id"),
            _parameter("food_pounds", "number", minimum=0, maximum=45),
            _parameter("water_gallons", "number", minimum=0, maximum=30),
        ],
        [
            _call("refresh_srd_object_content_one", {"object_id": "{object_id}"}),
            *_lookup_object(),
            *_now(),
            _require(
                "_object_scene.metadata.srd_created_supply != null",
                "Choose an actual created supply.",
            ),
            _set("_world_supply", "_object_scene.metadata.srd_created_supply"),
            _require(
                (
                    "food_pounds <= _world_supply.food_pounds and water_gallons <= "
                    "_world_supply.water_gallons"
                ),
                "The actual unspoiled supply is insufficient.",
            ),
            _set(
                "_world_supply_changes",
                {
                    "food_pounds": "_world_supply.food_pounds - food_pounds",
                    "water_gallons": "_world_supply.water_gallons - water_gallons",
                },
            ),
            _set("_world_supply", "merge(_world_supply, _world_supply_changes)"),
            *_metadata_patch({"srd_created_supply": "_world_supply"}),
        ],
    )
    return {
        "refresh_srd_object_content_one": one,
        "refresh_srd_object_content": refresh,
        "consume_srd_created_supply": consume,
    }


def _component_tool():
    """Report the source's actual component failure without accepting a caller verdict."""
    return _tool_scene(
        "Check actual selected object components against active Creation source records.",
        [
            _parameter(
                "object_ids", "array", items={"type": "string"}, max_items=16, unique_items=True
            )
        ],
        [
            *_now(),
            _set("_object_component_failure", False),
            _loop(
                "object_ids",
                "_world_component_id",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_world_component_id}"},
                        POSITION_EXPORTS,
                    ),
                    _require(
                        (
                            "_info_scene_entity.id == _world_component_id and "
                            "_info_scene_entity.metadata.participant_ref == null"
                        ),
                        "Select actual object components.",
                    ),
                    _when(
                        "_info_scene_entity.metadata.srd_created_object != null",
                        [
                            _set(
                                "_world_component_source",
                                "_info_scene_entity.metadata.srd_created_object",
                            ),
                            _require(
                                "_world_component_source.spell == 'creation' and "
                                f"_world_component_source.source_sha256 == {SOURCE_PAGES[121]!r} "
                                "and _object_now < _world_component_source.expires_tick",
                                "The component needs an active actual Creation source.",
                            ),
                            _set("_object_component_failure", True),
                        ],
                    ),
                ],
            ),
        ],
    )


def _dispel_tools():
    """Retire an exact ongoing spell source, never an Instantaneous physical consequence."""
    read = _tool_scene(
        "Read one exact ongoing Arcane Lock or Creation source on an actual scene object.",
        [_parameter("object_id"), _parameter("source_id")],
        [
            *_lookup_object(),
            *_now(),
            _set("_object_spell_source", None),
            _set("_world_locks", []),
            _when(
                "_object_scene.metadata.srd_arcane_locks != null",
                [_set("_world_locks", "_object_scene.metadata.srd_arcane_locks")],
            ),
            _loop(
                "_world_locks",
                "_world_lock",
                [
                    _when(
                        "_world_lock.source_id == source_id",
                        [
                            _require(
                                "_object_spell_source == null",
                                "The source identity must be unique.",
                            ),
                            _require(
                                (
                                    "_world_lock.spell == 'arcane_lock' and "
                                    "_world_lock.dispel_ongoing == true and "
                                    "_world_lock.spell_level "
                                    ">= 0"
                                ),
                                "The lock needs its actual ongoing source and cast level.",
                            ),
                            _require(
                                "_world_lock.source == "
                                + repr("sylvia.srd-lite.bg3:spells:arcane_lock:" + SOURCE_PAGES[110]),
                                "The lock needs its exact source qualification.",
                            ),
                            _set("_object_spell_source", "_world_lock"),
                        ],
                    ),
                ],
            ),
            _when(
                "_object_scene.metadata.srd_created_object != null",
                [
                    _set("_world_created", "_object_scene.metadata.srd_created_object"),
                    _when(
                        "_world_created.source_id == source_id",
                        [
                            _require(
                                "_object_spell_source == null",
                                "The source identity must be unique.",
                            ),
                            _require(
                                (
                                    "_world_created.spell == 'creation' and "
                                    "_world_created.dispel_ongoing == true and "
                                    "_world_created.spell_level >= 0 and _object_now < "
                                    "_world_created.expires_tick"
                                ),
                                "Creation needs its actual active ongoing source and cast level.",
                            ),
                            _require(
                                "_world_created.source_sha256 == " + repr(SOURCE_PAGES[121]),
                                "Creation needs its exact source qualification.",
                            ),
                            _set("_object_spell_source", "_world_created"),
                        ],
                    ),
                ],
            ),
            _require(
                "_object_spell_source != null",
                "That exact ongoing object spell source is unavailable.",
            ),
        ],
    )
    retire = _tool_scene(
        "Private actual-success callback: retire only one selected ongoing object source.",
        [_parameter("object_id"), _parameter("source_id")],
        [
            _call(
                "read_srd_object_spell_source",
                {"object_id": "{object_id}", "source_id": "{source_id}"},
                ["_object_spell_source"],
            ),
            *_lookup_object(),
            {
                "condition": "_object_spell_source.spell == 'arcane_lock'",
                "on_true": [
                    _set("_world_locks", "_object_scene.metadata.srd_arcane_locks"),
                    _set("_world_retained_locks", []),
                    _loop(
                        "_world_locks",
                        "_world_lock",
                        [
                            _when(
                                "_world_lock.source_id != source_id",
                                [_push("_world_retained_locks", "_world_lock")],
                            )
                        ],
                    ),
                    *_metadata_patch({"srd_arcane_locks": "_world_retained_locks"}),
                ],
                "on_false": [
                    *_snapshot(),
                    {
                        "spatial_mutation": {
                            "scene": "state.spatial_scene",
                            "map_id": "{state.spatial_scene.map.id}",
                            "expected_revision": "{_info_snapshot.geometry_revision}",
                            "target": {"kind": "entity", "id": "{object_id}"},
                            "patch": {"active": False},
                        },
                        "store_as": "_world_creation_dispelled",
                    },
                ],
            },
        ],
    )
    return {"read_srd_object_spell_source": read, "retire_srd_object_spell_source": retire}


def extend_runtime(text):
    """Emit object consumers and bind them to the actual shared public casting path."""
    tools = _load_runtime(text)["tools"]
    changed = _fact_tools()
    changed.update(_lifecycle_tools())
    changed.update(_dispel_tools())
    changed["check_srd_created_object_components"] = _component_tool()
    changed["resolve_srd_object_content_point"] = _point_tool()
    access = _access_tool()
    access["effects"][0:0] = [
        _set("_world_movement", "'blocking'"),
        _set("_world_visibility", "'opaque'"),
        _when(
            "operation == 'open'",
            [_set("_world_movement", "'passable'"), _set("_world_visibility", "'clear'")],
        ),
    ]
    changed["operate_srd_locked_object"] = access
    changed["resolve_srd_object_content_geometry"] = _geometry_tool(
        light="resolve_srd_information_light" in tools,
        object_visibility="check_srd_illumination_object_visibility" in tools,
    )
    changed["place_srd_created_supply_container"] = _supply_container_tool()
    changed["validate_srd_fabricate_cells"] = _fabricate_cells_tool()
    changed["retain_srd_water_cube"] = _water_cube_tool()
    changed["extinguish_srd_rain_flame"] = _rain_tool(
        object_visibility="check_srd_illumination_object_visibility" in tools
    )
    changed["cast_spell_object_content_spell"] = _casting_tool(
        fog="destroy_srd_environment_fog" in tools
    )
    changed["validate_srd_object_lock_creature"] = _tool_scene(
        "Designated creatures must be actual package-owned creature entities.",
        [_entity("creature_state")],
        [
            _require(
                "creature_state.is_alive != null and creature_state.conditions != null",
                "Designate an actual creature entity.",
            )
        ],
    )
    # Preserve resolved IDs and keep the state binding private on ordinary fact admissions.
    for name in PUBLIC_TOOLS & set(changed):
        tool = changed[name]
        node_name = name + "_node"
        changed[node_name] = deepcopy(tool)
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        tool["effects"] = [
            _call(
                node_name,
                {
                    **{
                        p["name"]: "{"
                        + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
                        + "}"
                        for p in tool["parameters"]
                    },
                    "lasting_state": "state",
                },
            )
        ]
    cast = deepcopy(tools["cast_spell"])
    prior = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in prior]
    branch = _when(
        "_spell_cast_failed != true and _spell_definition.family == 'object_content_spell'",
        [
            _set("_world_artisan_proficiencies", []),
            _when(
                "caster != null and caster.data.tool_proficiencies != null",
                [_set("_world_artisan_proficiencies", "caster.data.tool_proficiencies")],
            ),
            _call(
                "cast_spell_object_content_spell",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_target_id}",
                    "spell": "{spell}",
                    "slot_level": "{_slot_level}",
                    "artisan_proficiencies": "{_world_artisan_proficiencies}",
                    **{p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES},
                },
            ),
        ],
    )
    branch["on_true"][2]["export_context"] = {
        "_world_summary": "_spell_summary_rows",
        "_world_details": "_spell_detail_rows",
    }
    branch["on_true"] += [
        _loop("_world_summary", "_world_card", [_push("_spell_summary_rows", "_world_card")]),
        _loop("_world_details", "_world_card", [_push("_spell_detail_rows", "_world_card")]),
    ]

    def effect_lists(effects):
        yield effects
        for effect in effects:
            for key in ("on_true", "on_false"):
                if key in effect:
                    yield from effect_lists(effect[key])

    # Parent composers can nest the paid family branch beneath reaction or loss guards.
    # Remove only this owner's prior branches before selecting the new insertion point.
    for effects in list(effect_lists(cast["effects"])):
        effects[:] = [
            effect
            for effect in effects
            if "_spell_definition.family == 'object_content_spell'"
            not in effect.get("condition", "")
            and not (
                effect.get("condition") == "len(object_component_ids) > 0"
                and any(
                    child.get("call") == "check_srd_created_object_components"
                    for child in effect.get("on_true", [])
                )
            )
        ]

    resolution = next(
        effects
        for effects in effect_lists(cast["effects"])
        if any(
            "_spell_definition.family == 'information_spell'" in e.get("condition", "")
            for e in effects
        )
    )
    # The normal shared owner has already paid validated casting costs here.
    first_resolution = next(
        effects
        for effects in effect_lists(cast["effects"])
        if any(
            "_spell_definition.family == 'reaction_spell'" in e.get("condition", "")
            for e in effects
        )
    )
    first_family_index = next(
        index
        for index, effect in enumerate(first_resolution)
        if "_spell_definition.family == 'reaction_spell'" in effect.get("condition", "")
    )
    first_resolution[first_family_index:first_family_index] = [
        _when(
            "len(object_component_ids) > 0",
            [
                _require(
                    "'material' in _casting_requirements.components",
                    "This spell has no material component.",
                ),
                _call(
                    "check_srd_created_object_components",
                    {"object_ids": "{object_component_ids}"},
                    ["_object_component_failure"],
                ),
                _when(
                    "_object_component_failure == true",
                    [
                        _set("_spell_cast_failed", True),
                        _push(
                            "_spell_detail_rows",
                            {
                                "label": "'Casting failed'",
                                "value": (
                                    "'Creation material used as a spell component; casting costs "
                                    "spent.'"
                                ),
                            },
                        ),
                    ],
                ),
            ],
        )
    ]
    resolution.append(branch)
    changed["cast_spell"] = cast
    # Older generated cast prefixes can carry this completion-only check into a long-cast start.
    # That start has no object choices and must leave all paid failure checks for completion.
    for name in ("begin_srd_spell_cast_internal", "begin_srd_spell_cast"):
        if name not in tools:
            continue
        begin = deepcopy(tools[name])
        for effects in list(effect_lists(begin["effects"])):
            effects[:] = [
                effect
                for effect in effects
                if not (
                    effect.get("condition") == "len(object_component_ids) > 0"
                    and any(
                        child.get("call") == "check_srd_created_object_components"
                        for child in effect.get("on_true", [])
                    )
                )
            ]
        changed[name] = begin
    if "cast_srd_recovery" in tools:
        recovery = deepcopy(tools["cast_srd_recovery"])
        for effects in list(effect_lists(recovery["effects"])):
            effects[:] = [
                effect
                for effect in effects
                if effect.get("condition")
                != "spell == 'dispel_magic' and recovery_choice == 'object_content'"
            ]

        def qualify_dispel(effects, object_guarded=False):
            for index, effect in enumerate(effects):
                guarded = object_guarded or (
                    effect.get("condition") == "recovery_choice != 'object_content'"
                )
                if effect.get("call") == "dispel_srd_target_sources" and not guarded:
                    effects[index] = _when("recovery_choice != 'object_content'", [effect])
                else:
                    for key in ("on_true", "on_false"):
                        if key in effect:
                            qualify_dispel(effect[key], guarded)

        qualify_dispel(recovery["effects"])
        recovery["effects"].append(
            _when(
                "spell == 'dispel_magic' and recovery_choice == 'object_content'",
                [
                    _call(
                        "resolve_srd_object_content_geometry",
                        {
                            "caster_state": "{_caster_state_id}",
                            "object_id": "{recovery_item}",
                            "range_feet": 120,
                            "requires_sight": False,
                        },
                        GEOMETRY_EXPORTS,
                    ),
                    _call(
                        "read_srd_object_spell_source",
                        {"object_id": "{recovery_item}", "source_id": "{recovery_source_id}"},
                        ["_object_spell_source"],
                    ),
                    _call(
                        "resolve_srd_dispel_source_check",
                        {
                            "caster_state": "{_caster_state_id}",
                            "caster": "{_caster_id}",
                            "slot_level": "{slot_level}",
                            "source_level": "{_object_spell_source.spell_level}",
                            "casting_modifier": "{casting_modifier}",
                            "casting_ability": "{casting_ability}",
                        },
                        ["_dispel_succeeded", "_dispel_roll"],
                    ),
                    _set("_world_dispel_outcome", "'Source remains'"),
                    _when(
                        "_dispel_succeeded == true",
                        [
                            _call(
                                "retire_srd_object_spell_source",
                                {
                                    "object_id": "{recovery_item}",
                                    "source_id": "{recovery_source_id}",
                                },
                            ),
                            _set("_world_dispel_outcome", "'Selected ongoing source dispelled'"),
                        ],
                    ),
                    _set(
                        "_spell_summary_rows",
                        [{"label": "recovery_item", "value": "_world_dispel_outcome"}],
                    ),
                    _set(
                        "_spell_detail_rows",
                        [{"label": "recovery_source_id", "value": "_world_dispel_outcome"}],
                    ),
                    _push(
                        "_spell_detail_rows",
                        {
                            "label": "'Source cast level'",
                            "value": "_object_spell_source.spell_level",
                        },
                    ),
                    _when(
                        "_dispel_roll != null",
                        [
                            _push(
                                "_spell_detail_rows",
                                {"label": "'Actual Dispel check'", "value": "_dispel_roll"},
                            )
                        ],
                    ),
                ],
            )
        )
        changed["cast_srd_recovery"] = recovery
    advance = deepcopy(tools["advance_srd_lasting_time"])
    advance["effects"] = [
        effect
        for effect in advance["effects"]
        if not (
            effect.get("condition") == "state.spatial_scene != null"
            and len(effect.get("on_true", [])) == 1
            and effect["on_true"][0].get("call") == "refresh_srd_object_content"
        )
    ]
    advance["effects"] += [
        _when("state.spatial_scene != null", [_call("refresh_srd_object_content", {})])
    ]
    changed["advance_srd_lasting_time"] = advance
    text = _replace_tools(text, changed)
    if "      object_content_spell:" not in text:
        schema = yaml.dump(
            {"object_content_spell": _profile_schema()}, Dumper=_RuntimeDumper, sort_keys=False
        )
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      timed_senses:",
            1,
        )
    return text
