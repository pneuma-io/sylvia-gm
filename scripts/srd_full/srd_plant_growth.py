"""Plant Growth changes admitted natural plants and preserves its printed area consequence."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_body_soul_spells import _clock
    from .srd_control_behaviors import _when
    from .srd_information_spells import _sphere_interval_tool
    from .srd_magic_jar_consumers import _walk
    from .srd_scene_relations import (
        MEASURE_EXPORTS,
        POSITION_EXPORTS,
        _array,
        _snapshot,
        _tool_scene,
    )
    from .srd_spell_object_content import _new_scene_object
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
else:
    from srd_body_soul_spells import _clock
    from srd_control_behaviors import _when
    from srd_information_spells import _sphere_interval_tool
    from srd_magic_jar_consumers import _walk
    from srd_scene_relations import (
        MEASURE_EXPORTS,
        POSITION_EXPORTS,
        _array,
        _snapshot,
        _tool_scene,
    )
    from srd_spell_object_content import _new_scene_object
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools

SOURCE_PAGE = 153
SOURCE_SHA = "4be44b219bc1a77387068b3d6df4a72b2efee65e289d1298d572e91f7930ba7d"
YEAR_TICKS = 365 * 28800
PROFILE = {
    "family": "plant_growth_spell",
    "base_level": 3,
    "casting_time": "action",
    "range_kind": "point",
    "range_feet": 150,
    "concentration": False,
    "duration_rounds": 0,
}
CAST_CHOICES = [
    _optional("plant_growth_mode", "enum", "overgrowth", enum_values=["overgrowth", "enrichment"]),
    _array("plant_growth_point", default=[0, 0, 0]),
    _optional("plant_growth_exclusions", "array", [], items={"type": "object"}, max_items=16),
]
PUBLIC_TOOLS = {"harvest_srd_enriched_crop"}


def validate_profiles(definitions):
    """The printed modes share one level, range and Instantaneous source."""
    for spell, definition in definitions.items():
        if definition.get("family") == PROFILE["family"] and (
            spell != "plant_growth" or definition != PROFILE
        ):
            raise ValueError("Plant Growth differs from its pinned profile")


def _patch_zone(zone, metadata):
    return {
        "spatial_mutation": {
            "scene": "state.spatial_scene",
            "map_id": "{state.spatial_scene.map.id}",
            "expected_revision": "{state.spatial_scene.map.revision}",
            "target": {"kind": "zone", "id": "{" + zone + ".id}"},
            "patch": {"metadata": "{" + metadata + "}"},
        },
        "store_as": "_growth_plant_mutation",
    }


def _patch_plant(plant, metadata):
    return {
        "spatial_mutation": {
            "scene": "state.spatial_scene",
            "map_id": "{state.spatial_scene.map.id}",
            "expected_revision": "{state.spatial_scene.map.revision}",
            "target": {"kind": "entity", "id": "{" + plant + ".id}"},
            "patch": {"metadata": "{" + metadata + "}"},
        },
        "store_as": "_growth_plant_mutation",
    }


def _interval_tool():
    """Reuse the proven actual-segment Sphere calculation with the printed chosen radius."""
    tool = deepcopy(_sphere_interval_tool())
    tool["description"] = "Calculate the actual segment interval in a retained Plant Growth Sphere."
    tool["parameters"].append(_parameter("radius", "number", minimum=0))

    def radius(node):
        if node.get("set", {}).get("field") == "_sphere_c":
            node["set"]["value"] = node["set"]["value"].replace("- 900", "- radius ** 2")

    _walk(tool["effects"], radius)
    return tool


def _validate():
    return _tool_scene(
        "Qualify the real point, clear path and selected excluded Boxes before committing growth.",
        [
            _entity("caster_state"),
            _array("point"),
            _parameter("exclusions", "array", items={"type": "object"}),
        ],
        _snapshot()
        + [
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_growth_caster", "_info_scene_entity"),
            _set("_growth_plane", "_growth_caster.metadata.plane"),
            _require("_growth_plane != null", "Plant Growth needs the actual scene plane."),
            _call(
                "measure_srd_information_distance",
                {"start": "{_info_position}", "end": "{point}"},
                MEASURE_EXPORTS,
            ),
            _require("_scene_distance <= 150", "The chosen growth point is beyond 150 feet."),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_info_position}",
                    "end": "{point}",
                    "rule": "solid",
                    "plane": "{_growth_plane}",
                },
                ["_information_blocked"],
            ),
            _require("_information_blocked != true", "The growth point has no clear spell path."),
            *[
                _require(
                    f"point[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                    f"point[{i}] <= state.spatial_scene.map.bounds.max[{i}]",
                    "The chosen point must be in the admitted scene.",
                )
                for i in range(3)
            ],
            _set("_growth_exclusions", "exclusions"),
            _loop(
                "_growth_exclusions",
                "_growth_exclusion",
                [
                    _require(
                        "_growth_exclusion.kind == 'box'",
                        "Only excluded Boxes are admitted; other geometry is unsupported.",
                    ),
                    *[
                        _require(
                            f"_growth_exclusion.min[{i}] < _growth_exclusion.max[{i}]",
                            "An excluded Box needs its real increasing bounds.",
                        )
                        for i in range(3)
                    ],
                    _set(
                        "_growth_farthest",
                        [
                            f"max(abs(_growth_exclusion.min[{i}] - point[{i}]), "
                            f"abs(_growth_exclusion.max[{i}] - point[{i}]))"
                            for i in range(3)
                        ],
                    ),
                    _require(
                        "_growth_farthest[0] ** 2 + _growth_farthest[1] ** 2 + "
                        "_growth_farthest[2] ** 2 <= 10000",
                        "An excluded area must lie within the printed 100-foot Sphere.",
                    ),
                ],
            ),
        ],
    )


def _point_in_growth():
    """Admit unambiguous whole plant Boxes; boundary-spanning plant bodies fail clearly."""
    bounds = []
    for axis in range(3):
        low = (
            f"_growth_plant.position[{axis}] - _growth_plant.footprint.half_extents[{axis}]"
            if axis < 2
            else "_growth_plant.position[2]"
        )
        high = (
            f"_growth_plant.position[{axis}] + _growth_plant.footprint.half_extents[{axis}]"
            if axis < 2
            else "_growth_plant.position[2] + _growth_plant.height"
        )
        bounds.extend(
            [_set(f"_growth_plant_min{axis}", low), _set(f"_growth_plant_max{axis}", high)]
        )
    nearest = " + ".join(
        f"max(_growth_plant_min{i} - point[{i}], point[{i}] - _growth_plant_max{i}, 0) ** 2"
        for i in range(3)
    )
    farthest = " + ".join(
        f"max(abs(_growth_plant_min{i} - point[{i}]), abs(_growth_plant_max{i} - point[{i}])) ** 2"
        for i in range(3)
    )
    overlap = " and ".join(
        f"_growth_plant_min{i} < _growth_exclusion.max[{i}] and "
        f"_growth_plant_max{i} > _growth_exclusion.min[{i}]"
        for i in range(3)
    )
    contained = " and ".join(
        f"_growth_plant_min{i} >= _growth_exclusion.min[{i}] and "
        f"_growth_plant_max{i} <= _growth_exclusion.max[{i}]"
        for i in range(3)
    )
    return [
        _require(
            "_growth_plant.footprint.kind == 'box'",
            "Natural plants currently need admitted Box bodies.",
        ),
        *bounds,
        _set("_growth_plant_inside", nearest + " <= _growth_radius ** 2"),
        _when(
            "_growth_plant_inside == true",
            [
                _require(
                    farthest + " <= _growth_radius ** 2",
                    "A boundary-spanning plant body needs unsupported area qualification.",
                ),
                _when(
                    "mode == 'overgrowth'",
                    [
                        _loop(
                            "_growth_exclusions",
                            "_growth_exclusion",
                            [
                                _when(
                                    overlap,
                                    [
                                        _require(
                                            contained,
                                            "Plant/exclusion boundary overlap is unsupported.",
                                        ),
                                        _set("_growth_plant_inside", False),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]


def _cast():
    return _tool_scene(
        "Apply actual normal-plant growth or one-year enrichment after the genuine paid casting.",
        [
            _entity("caster_state"),
            _array("point"),
            _parameter("mode", "enum", enum_values=["overgrowth", "enrichment"]),
            _parameter("slot_level", "integer", minimum=3, maximum=9),
            _parameter("exclusions", "array", items={"type": "object"}),
        ],
        [
            _call(
                "validate_srd_plant_growth_point",
                {
                    "caster_state": "{_caster_state_id}",
                    "point": "{point}",
                    "exclusions": "{exclusions}",
                },
                ["_growth_plane", "_growth_exclusions"],
            ),
            _clock(),
            _set("_growth_radius", 100),
            _when("mode == 'enrichment'", [_set("_growth_radius", 2640)]),
            _set("_growth_plants_changed", []),
            _set("_growth_plants", "state.spatial_scene.entities"),
            _loop(
                "_growth_plants",
                "_growth_plant",
                [
                    _when(
                        "_growth_plant.active == true and "
                        "_growth_plant.metadata.plane == _growth_plane and "
                        "_growth_plant.metadata.nonmagical_plant == true",
                        [
                            _set("_growth_plant_position", "_growth_plant.position"),
                            *_point_in_growth(),
                            _when(
                                "_growth_plant_inside == true",
                                [
                                    _set("_growth_patch", {"plant_growth_overgrown": True}),
                                    _set("_growth_apply", True),
                                    _when(
                                        "mode == 'enrichment'",
                                        [
                                            _set(
                                                "_growth_apply",
                                                "_growth_plant.metadata."
                                                "plant_growth_enriched_until == null "
                                                "or state.lasting_tick >= "
                                                "_growth_plant.metadata.plant_growth_enriched_until",
                                            ),
                                            _set(
                                                "_growth_patch",
                                                {
                                                    "plant_growth_enriched_from": (
                                                        "state.lasting_tick"
                                                    ),
                                                    "plant_growth_enriched_until": (
                                                        f"state.lasting_tick + {YEAR_TICKS}"
                                                    ),
                                                    "plant_growth_normal_yield_factor": 2,
                                                },
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_growth_apply == true",
                                        [
                                            _set(
                                                "_growth_plant_metadata",
                                                "merge(_growth_plant.metadata, _growth_patch)",
                                            ),
                                            _patch_plant("_growth_plant", "_growth_plant_metadata"),
                                            _push("_growth_plants_changed", "_growth_plant.id"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "mode == 'overgrowth'",
                [
                    {"increment": {"field": "state.lasting_serial", "amount": 1}},
                    _call(
                        "format_srd_information_projection",
                        {"message": "plant-growth:{_caster_state_id}:{state.lasting_serial}"},
                        ["_info_projection_message"],
                    ),
                    _set("_growth_area_id", "_info_projection_message"),
                    _set(
                        "_growth_zone",
                        {
                            "id": "_growth_area_id",
                            "map_id": "state.spatial_scene.map.id",
                            "shape": {
                                "kind": "'box'",
                                "min": [
                                    f"max(point[{i}] - 100, "
                                    f"state.spatial_scene.map.bounds.min[{i}])"
                                    for i in range(3)
                                ],
                                "max": [
                                    f"min(point[{i}] + 100, "
                                    f"state.spatial_scene.map.bounds.max[{i}])"
                                    for i in range(3)
                                ],
                            },
                            "metadata": {
                                "source_spell": "'plant_growth'",
                                "source_sha256": repr(SOURCE_SHA),
                                "origin": "point",
                                "radius": 100,
                                "exclusions": "exclusions",
                                "plane": "_growth_plane",
                                "caster": "_caster_state_id",
                                "spell_level": "slot_level",
                                "started_tick": "state.lasting_tick",
                                "dispel_ongoing": False,
                                "ongoing_duration": False,
                            },
                        },
                    ),
                    _set("_growth_zones", "state.spatial_scene.zones"),
                    _push("_growth_zones", "_growth_zone"),
                    _set("state.spatial_scene.zones", "_growth_zones"),
                    _set("state.has_plant_growth_terrain", True),
                    {"increment": {"field": "state.spatial_scene.map.revision", "amount": 1}},
                    {
                        "spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"},
                        "store_as": "_growth_admitted",
                    },
                ],
            ),
            _set("_spell_summary_rows", [{"label": "'Plant Growth'", "value": "mode"}]),
            _set(
                "_spell_detail_rows",
                [{"label": "'Actually affected plants'", "value": "_growth_plants_changed"}],
            ),
        ],
    )


def _harvest():
    """Consume a saved ready crop's normal food yield, with its actual enrichment year."""
    tool = _tool_scene(
        "Harvest an admitted ready crop. Crop readiness and normal food yield are saved GM "
        "scene facts; this action does not simulate crop growth or convert nutrition.",
        [_entity("actor_state"), _parameter("crop_id"), _parameter("output_id")],
        [
            _require(
                "actor_state.is_alive == true and not ('incapacitated' in "
                "actor_state.conditions or 'unconscious' in actor_state.conditions or "
                "'petrified' in actor_state.conditions or 'paralyzed' in "
                "actor_state.conditions or 'stunned' in actor_state.conditions)",
                "An incapacitated actor cannot harvest a crop.",
            ),
            _clock(),
            _call(
                "srd_information_scene_entity", {"subject": "{_actor_state_id}"}, POSITION_EXPORTS
            ),
            _set("_crop_actor", "_info_scene_entity"),
            _call("srd_information_scene_entity", {"subject": "{crop_id}"}, POSITION_EXPORTS),
            _set("_crop", "_info_scene_entity"),
            _require(
                "_crop.metadata.nonmagical_plant == true and "
                "_crop.metadata.crop_ready == true and "
                "_crop.metadata.crop_normal_food_pounds > 0",
                "Harvest needs an admitted ready natural crop and its normal food yield.",
            ),
            _require(
                "_crop_actor.metadata.plane == _crop.metadata.plane",
                "The crop is on another plane.",
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_crop_actor.position}", "end": "{_crop.position}"},
                MEASURE_EXPORTS,
            ),
            _require("_scene_distance <= 5", "Reach the actual crop to harvest it."),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_crop_actor.position}",
                    "end": "{_crop.position}",
                    "rule": "solid",
                    "plane": "{_crop.metadata.plane}",
                },
                ["_information_blocked"],
            ),
            _require("_information_blocked != true", "The crop has no clear physical access."),
            _set("_crop_food_pounds", "_crop.metadata.crop_normal_food_pounds"),
            _when(
                "_crop.metadata.plant_growth_enriched_from != null and "
                "_crop.metadata.plant_growth_enriched_until != null and "
                "state.lasting_tick >= _crop.metadata.plant_growth_enriched_from and "
                "state.lasting_tick < _crop.metadata.plant_growth_enriched_until",
                [_set("_crop_food_pounds", "_crop_food_pounds * 2")],
            ),
            _when(
                "combat.encounter_active == true",
                [
                    _require(
                        "combat.active_actor == _actor_state_id",
                        "Only the active actor may harvest on its turn.",
                    ),
                    _call("spend_srd_action", {"magic_action": False}),
                ],
            ),
            _set("_crop_harvest_patch", {"crop_ready": False}),
            _set("_growth_plant_metadata", "merge(_crop.metadata, _crop_harvest_patch)"),
            _set("_world_origin", "_crop.position"),
            _call(
                "format_srd_information_projection",
                {"message": "{output_id}"},
                ["_info_projection_message"],
            ),
            _set("_crop_output_id", "_info_projection_message"),
            _require(
                "_crop.metadata.crop_harvest_extent != null and "
                "len(_crop.metadata.crop_harvest_extent) == 3",
                "Admit the harvested crop's actual food-scene dimensions.",
            ),
            _set("_crop_extent", "_crop.metadata.crop_harvest_extent"),
            *[
                _require(
                    f"_crop_extent[{i}] > 0",
                    "Harvested food needs positive actual physical dimensions.",
                )
                for i in range(3)
            ],
            *_new_scene_object(
                {
                    "plane": "_crop.metadata.plane",
                    "nonmagical_food": True,
                    "food_pounds": "_crop_food_pounds",
                    "crop_source_id": "_crop.id",
                },
                identity="_crop_output_id",
                extent="_crop_extent",
            ),
            _patch_plant("_crop", "_growth_plant_metadata"),
        ],
    )
    _public(tool, "spells", "Harvest crop", [("Food pounds", "_crop_food_pounds")])
    tool["metadata"]["costs_action"] = True
    return tool


def extend_runtime(text):
    """Install one existing-scene family; Instantaneous consequences get no ongoing identity."""
    tools = _load_runtime(text)["tools"]
    cast = deepcopy(tools["cast_spell"])
    existing = {p["name"] for p in cast["parameters"]}
    cast["parameters"].extend(deepcopy(p) for p in CAST_CHOICES if p["name"] not in existing)
    marker = "_spell_cast_failed != true and _spell_definition.family == 'plant_growth_spell'"
    cast["effects"] = [e for e in cast["effects"] if e.get("condition") != marker]
    cast["effects"].append(
        _when(
            marker,
            [
                _call(
                    "cast_srd_plant_growth",
                    {
                        "caster_state": "{_caster_state_id}",
                        "point": "{plant_growth_point}",
                        "mode": "{plant_growth_mode}",
                        "slot_level": "{_slot_level}",
                        "exclusions": "{plant_growth_exclusions}",
                    },
                    ["_spell_summary_rows", "_spell_detail_rows"],
                )
            ],
        )
    )
    text = _replace_tools(
        text,
        {
            "cast_spell": cast,
            "cast_srd_plant_growth": _cast(),
            "validate_srd_plant_growth_point": _validate(),
            "srd_plant_growth_sphere_interval": _interval_tool(),
            "harvest_srd_enriched_crop": _harvest(),
        },
    )
    if "      plant_growth_spell:" not in text:
        schema = {
            "plant_growth_spell": {
                "type": "object",
                "properties": {
                    k: {
                        "type": "boolean"
                        if type(v) is bool
                        else "integer"
                        if type(v) is int
                        else "string"
                    }
                    for k, v in PROFILE.items()
                    if k != "family"
                },
                "required": [k for k in PROFILE if k != "family"],
                "additionalProperties": False,
            }
        }
        rendered = yaml.safe_dump(schema, sort_keys=False)
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in rendered.splitlines(True))
            + "\n      timed_senses:",
            1,
        )
    return text
