"""Lasting SRD environments consume the admitted scene and persistent-area registry."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import (
        _call as _base_call,
    )
    from .srd_control_behaviors import (
        _each,
        _entity,
        _param,
        _push,
        _require,
        _set,
        _when,
    )
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial
    from .srd_timed_actor_defenses import _tool as _bound_tool
    from .srd_timed_senses import _optional, _public, _replace_tools
else:
    from srd_control_behaviors import (
        _call as _base_call,
    )
    from srd_control_behaviors import (
        _each,
        _entity,
        _param,
        _push,
        _require,
        _set,
        _when,
    )
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial
    from srd_timed_actor_defenses import _tool as _bound_tool
    from srd_timed_senses import _optional, _public, _replace_tools


SOURCE = {
    "black_tentacles": (113, "Black Tentacles", "Blade Barrier"),
    "blade_barrier": (113, "Blade Barrier", "Bless"),
    "call_lightning": (114, "Call Lightning", "Calm Emotions"),
    "cloudkill": (116, "Cloudkill", "Color Spray"),
    "flaming_sphere": ([132, 133], "Flaming Sphere", "Flesh to Stone"),
    "conjure_animals": ([117, 118], "Conjure Animals", "Conjure Celestial"),
    "conjure_elemental": (118, "Conjure Elemental", "Conjure Fey"),
    "conjure_minor_elementals": (118, "Conjure Minor Elementals", "Conjure Woodland Beings"),
    "conjure_woodland_beings": ([118, 119], "Conjure Woodland Beings", "Contact Other Plane"),
    "control_water": ([119, 120], "Control Water", "Control Weather"),
    "control_weather": (120, "Control Weather", "Counterspell"),
    "entangle": (128, "Entangle", "Enthrall"),
    "fog_cloud": (133, "Fog Cloud", "Forbiddance"),
    "grease": (137, "Grease", "Greater Invisibility"),
    "incendiary_cloud": ([142, 143], "Incendiary Cloud", "Insect Plague"),
    "insect_plague": (143, "Insect Plague", "Instant Summons"),
    "moonbeam": ([150, 151], "Moonbeam", "Move Earth"),
    "silence": (162, "Silence", "Silent Image"),
    "sleet_storm": (163, "Sleet Storm", "Slow"),
    "spike_growth": (164, "Spike Growth", "Spirit Guardians"),
    "spirit_guardians": ([164, 165], "Spirit Guardians", "Spiritual Weapon"),
    "stinking_cloud": (165, "Stinking Cloud", "Stone Shape"),
    "wall_of_fire": (172, "Wall of Fire", "Wall of Force"),
    "wall_of_ice": ([172, 173], "Wall of Ice", "Wall of Stone"),
    "web": (174, "Web", "Weird"),
    "wind_wall": ([174, 175], "Wind Wall", "Wish"),
}
PROFILES = {}
for name, level, distance, radius, growth, rounds, mode in (
    ("fog_cloud", 1, 120, 20, 20, 600, "fog"),
    ("silence", 2, 120, 20, 0, 100, "silence"),
):
    PROFILES[name] = {
        "family": "spell_environment",
        "casting_time": "action",
        "base_level": level,
        "range_kind": "distance",
        "range_feet": distance,
        "radius_feet": radius,
        "radius_per_slot_level": growth,
        "duration_rounds": rounds,
        "concentration": True,
        "environment_mode": mode,
    }
PROFILES["grease"] = {
    "family": "spell_environment",
    "casting_time": "action",
    "base_level": 1,
    "range_kind": "distance",
    "range_feet": 60,
    "radius_feet": 0,
    "radius_per_slot_level": 0,
    "duration_rounds": 10,
    "concentration": False,
    "environment_mode": "grease",
    "half_side_feet": 5,
}
PROFILES["entangle"] = {
    **PROFILES["grease"],
    "range_feet": 90,
    "concentration": True,
    "environment_mode": "entangle",
    "half_side_feet": 10,
}
PROFILES["conjure_woodland_beings"] = {
    "family": "spell_environment",
    "casting_time": "action",
    "base_level": 4,
    "range_kind": "self",
    "range_feet": 0,
    "radius_feet": 10,
    "radius_per_slot_level": 0,
    "duration_rounds": 100,
    "concentration": True,
    "environment_mode": "woodland",
    "damage_dice": 5,
    "damage_die": "d8",
    "damage_per_slot_level": 1,
    "damage_type": "force",
    "save_ability": "wisdom",
}
PROFILES["conjure_minor_elementals"] = {
    "family": "spell_environment",
    "casting_time": "action",
    "base_level": 4,
    "range_kind": "self",
    "range_feet": 0,
    "radius_feet": 15,
    "radius_per_slot_level": 0,
    "duration_rounds": 100,
    "concentration": True,
    "environment_mode": "minor_elementals",
    "damage_dice": 2,
    "damage_die": "d8",
    "damage_per_slot_level": 1,
    "damage_type": "choice",
}
PROFILES["conjure_animals"] = {
    **PROFILES["conjure_woodland_beings"],
    "base_level": 3,
    "range_kind": "distance",
    "range_feet": 60,
    "environment_mode": "animals",
    "damage_dice": 3,
    "damage_die": "d10",
    "damage_type": "slashing",
    "save_ability": "dexterity",
    "pack_half_extent": 5,
    "pack_move_feet": 30,
}
PROFILES["conjure_elemental"] = {
    **PROFILES["conjure_animals"],
    "base_level": 5,
    "radius_feet": 0,
    "environment_mode": "elemental",
    "damage_dice": 8,
    "damage_die": "d8",
    "damage_type": "choice",
    "repeat_damage_dice": 4,
    "start_radius_feet": 5,
}
PROFILES["conjure_elemental"].pop("pack_move_feet")
PROFILES["insect_plague"] = {
    **PROFILES["fog_cloud"],
    "base_level": 5,
    "range_feet": 300,
    "radius_per_slot_level": 0,
    "duration_rounds": 100,
    "environment_mode": "insects",
    "damage_dice": 4,
    "damage_die": "d10",
    "damage_per_slot_level": 1,
    "damage_type": "piercing",
    "save_ability": "constitution",
}
PROFILES["cloudkill"] = {
    **PROFILES["insect_plague"],
    "range_feet": 120,
    "environment_mode": "cloudkill",
    "damage_dice": 5,
    "damage_die": "d8",
    "damage_type": "poison",
}
PROFILES["call_lightning"] = {
    **PROFILES["insect_plague"],
    "base_level": 3,
    "range_feet": 120,
    "radius_feet": 60,
    "height_feet": 10,
    "environment_mode": "call_lightning",
    "damage_dice": 3,
    "damage_type": "lightning",
    "save_ability": "dexterity",
}
PROFILES["incendiary_cloud"] = {
    **PROFILES["cloudkill"],
    "base_level": 8,
    "range_feet": 150,
    "duration_rounds": 10,
    "environment_mode": "incendiary_cloud",
    "damage_dice": 10,
    "damage_per_slot_level": 0,
    "damage_type": "fire",
    "save_ability": "dexterity",
}
PROFILES["stinking_cloud"] = {
    **PROFILES["fog_cloud"],
    "base_level": 3,
    "range_feet": 90,
    "radius_per_slot_level": 0,
    "duration_rounds": 10,
    "environment_mode": "stinking_cloud",
    "save_ability": "constitution",
}
PROFILES["moonbeam"] = {
    **PROFILES["insect_plague"],
    "base_level": 2,
    "range_feet": 120,
    "radius_feet": 5,
    "height_feet": 40,
    "duration_rounds": 10,
    "environment_mode": "moonbeam",
    "damage_dice": 2,
    "damage_type": "radiant",
}
PROFILES["spike_growth"] = {
    **PROFILES["fog_cloud"],
    "base_level": 2,
    "range_feet": 150,
    "radius_per_slot_level": 0,
    "duration_rounds": 100,
    "environment_mode": "spikes",
    "damage_dice": 2,
    "damage_die": "d4",
    "damage_per_slot_level": 0,
    "damage_type": "piercing",
}
PROFILES["spirit_guardians"] = {
    **PROFILES["conjure_woodland_beings"],
    "base_level": 3,
    "radius_feet": 15,
    "environment_mode": "guardians",
    "damage_dice": 3,
    "damage_type": "alignment",
}
PROFILES["sleet_storm"] = {
    **PROFILES["stinking_cloud"],
    "range_feet": 150,
    "height_feet": 40,
    "environment_mode": "sleet",
    "save_ability": "dexterity",
}
PROFILES["black_tentacles"] = {
    **PROFILES["entangle"],
    "base_level": 4,
    "environment_mode": "tentacles",
    "damage_dice": 3,
    "damage_die": "d6",
    "damage_per_slot_level": 0,
    "damage_type": "bludgeoning",
    "save_ability": "strength",
}
PROFILES["blade_barrier"] = {
    **PROFILES["black_tentacles"],
    "base_level": 6,
    "duration_rounds": 100,
    "environment_mode": "blade",
    "half_side_feet": 50,
    "height_feet": 20,
    "damage_dice": 6,
    "damage_die": "d10",
    "damage_type": "force",
    "save_ability": "dexterity",
}
PROFILES["wall_of_fire"] = {
    **PROFILES["blade_barrier"],
    "base_level": 4,
    "range_feet": 120,
    "duration_rounds": 10,
    "environment_mode": "wall_fire",
    "half_side_feet": 30,
    "damage_dice": 5,
    "damage_die": "d8",
    "damage_per_slot_level": 1,
    "damage_type": "fire",
}
PROFILES["wall_of_ice"] = {
    "family": "spell_environment",
    "casting_time": "action",
    "base_level": 6,
    "range_kind": "distance",
    "range_feet": 120,
    "radius_feet": 0,
    "radius_per_slot_level": 0,
    "duration_rounds": 100,
    "concentration": True,
    "environment_mode": "wall_ice",
    "height_feet": 10,
    "damage_dice": 10,
    "damage_die": "d6",
    "damage_per_slot_level": 2,
    "damage_type": "cold",
    "save_ability": "dexterity",
    "sheet_damage_dice": 5,
    "sheet_damage_per_slot_level": 1,
}
PROFILES["web"] = {
    **PROFILES["entangle"],
    "base_level": 2,
    "range_feet": 60,
    "duration_rounds": 600,
    "environment_mode": "web",
    "height_feet": 5,
    "save_ability": "dexterity",
}
PROFILES["flaming_sphere"] = {
    "family": "spell_environment", "casting_time": "action", "base_level": 2,
    "range_kind": "distance", "range_feet": 60, "radius_feet": 2.5,
    "radius_per_slot_level": 0, "duration_rounds": 10, "concentration": True,
    "environment_mode": "flaming_sphere", "damage_dice": 2, "damage_die": "d6",
    "damage_per_slot_level": 1, "damage_type": "fire", "save_ability": "dexterity",
}
PROFILES["wind_wall"] = {
    "family": "spell_environment", "casting_time": "action", "base_level": 3,
    "range_kind": "distance", "range_feet": 120, "radius_feet": 0,
    "radius_per_slot_level": 0, "duration_rounds": 10, "concentration": True,
    "environment_mode": "wind_wall", "damage_dice": 4, "damage_die": "d8",
    "damage_per_slot_level": 0, "damage_type": "bludgeoning", "save_ability": "strength",
}
PROFILES["control_weather"] = {
    "family": "spell_environment", "casting_time": "10_minutes", "base_level": 8,
    "range_kind": "self", "range_feet": 0, "radius_feet": 26400,
    "radius_per_slot_level": 0, "duration_rounds": 4800, "concentration": True,
    "environment_mode": "control_weather",
}
PROFILES["control_water"] = {
    "family": "spell_environment", "casting_time": "action", "base_level": 4,
    "range_kind": "distance", "range_feet": 300, "radius_feet": 0,
    "radius_per_slot_level": 0, "duration_rounds": 100, "concentration": True,
    "environment_mode": "control_water",
}
PUBLIC_TOOLS = {
    "disperse_srd_fog",
    "escape_srd_entangle",
    "escape_srd_web",
    "select_srd_environment_targets",
    "disengage_srd_conjure_woodland",
    "move_srd_conjure_animals_pack",
    "move_srd_moonbeam_source",
    "search_srd_spike_growth",
    "escape_srd_black_tentacles",
    "call_srd_lightning_source",
    "roll_srd_flaming_sphere",
    "choose_srd_control_weather",
    "record_srd_water_body",
    "record_srd_water_vehicle",
    "resolve_srd_water_flow",
    "change_srd_control_water",
    "escape_srd_whirlpool",
}
POINT_EXPORTS = (
    "_environment_silence",
    "_environment_fog",
    "_environment_lightly_obscured",
)
WATER_SOURCE_SHA256 = "564c41de7bcb0225e84a7af1d5b36062c0f42ee3be59dba253a51a3502d3ad9d"


def _sphere_damage_mode(source):
    """Select the printed saved area damage families sharing one typed packet."""
    return (
        f"({source}.mode == 'insects' or {source}.mode == 'cloudkill' "
        f"or {source}.mode == 'moonbeam' or {source}.mode == 'incendiary_cloud')"
    )


def _sphere_mode(source):
    """Share saved area qualification while retaining each printed event and shape."""
    return (
        f"({_sphere_damage_mode(source)} or {source}.mode == 'stinking_cloud' "
        f"or {source}.mode == 'sleet')"
    )


def _area_entry_mode(source):
    """Select damaging areas and the printed Sleet first-entry save, without appearance saves."""
    return f"({_sphere_damage_mode(source)} or {source}.mode == 'sleet')"


def _cylinder_mode(source):
    """Select only source-sized saved vertical Cylinders using the existing body interval."""
    return f"({source}.mode == 'moonbeam' or {source}.mode == 'sleet')"


def _heavy_fog_mode(source):
    """Select printed heavy obscuration, preserving ordinary clipped Fog separately."""
    return (
        f"({source}.mode == 'cloudkill' or {source}.mode == 'stinking_cloud' "
        f"or {source}.mode == 'sleet' or {source}.mode == 'incendiary_cloud')"
    )


def _wind_fog_mode(source):
    """Select only saved fog sources whose printed strong-wind clause ends the source."""
    return (
        f"({source}.mode == 'fog' or {source}.mode == 'cloudkill' "
        f"or {source}.mode == 'stinking_cloud' or {source}.mode == 'incendiary_cloud')"
    )


def _call(name, params, exports=()):
    """Retain shared exports while allowing a caller-owned name for a nested result."""
    effect = _base_call(name, params, exports)
    if isinstance(exports, dict):
        effect["export_context"] = exports
    return effect


def _environment_tool(description, parameters, effects, preconditions=(), *, public=False):
    """Bind mutable package state while keeping neutral scene effects on their canonical path."""
    if any(p["name"] == "area_id" for p in parameters) and not any(
        "_environment_area_key" in e.get("export_context", {}) for e in effects
    ):
        # A selected registry key can equal a loaded creature ID. Capture the raw Call
        # parameter before expressions can implicitly resolve that string as a creature.
        def bind_key(value):
            if isinstance(value, str):
                return re.sub(r"\barea_id\b", "_environment_area_key", value)
            if isinstance(value, list):
                return [bind_key(item) for item in value]
            if isinstance(value, dict):
                return {key: bind_key(item) for key, item in value.items()}
            return value

        capture = _call("format_srd_information_projection", {"message": "{area_id}"})
        capture["export_context"] = {"_environment_area_key": "_info_projection_message"}
        effects = [capture, *bind_key(effects)]
    tool = _bound_tool(description, parameters, effects, preconditions)
    for parameter in tool["parameters"]:
        if parameter["name"] == "lasting_state":
            parameter["model_hidden"] = True

    def restore(value):
        if isinstance(value, list):
            return [restore(item) for item in value]
        if isinstance(value, dict):
            return {
                key: "state.spatial_scene" if key == "scene" else restore(item)
                for key, item in value.items()
            }
        return value

    tool["effects"] = restore(tool["effects"])
    if public:
        tool["metadata"].update(category="exploration", visible_to_player=True)
    return tool


def validate_profiles(definitions):
    for spell, definition in definitions.items():
        if definition.get("family") == "spell_environment" and definition != PROFILES.get(spell):
            raise SystemExit(f"Environment source profile differs: {spell}")


def _schema():
    properties = {}
    for profile in PROFILES.values():
        for key, value in profile.items():
            if key != "family":
                properties[key] = {
                    "type": "boolean"
                    if type(value) is bool
                    else "number"
                    if type(value) is float
                    or (type(value) is int and properties.get(key, {}).get("type") == "number")
                    else "integer"
                    if type(value) is int
                    else "string"
                }
    return {
        "type": "object",
        "properties": properties,
        "required": [key for key in properties if all(key in p for p in PROFILES.values())],
        "additionalProperties": False,
    }


def _areas(effects):
    return [
        _set("_environment_ids", []),
        _when(
            "state.has_spell_environments == true",
            [
                _set("_environment_ids", "state.persistent_spell_area_ids"),
            ],
        ),
        _each(
            "_environment_ids",
            "_environment_id",
            [
                _set("_environment_area", "state.persistent_spell_areas[_environment_id]"),
                _when(
                    (
                        "_environment_area.profile == 'environment' and _environment_area.act"
                        "ive == true and state.lasting_tick < _environment_area.expires_tick"
                    ),
                    effects,
                ),
            ],
        ),
    ]


def _origin():
    return [
        _set("_environment_origin", "_environment_area.origin"),
        _set("_environment_enabled", True),
    ]


def _interval(start, end):
    return _call(
        "resolve_srd_persistent_area_segment",
        {
            "start": "{" + start + "}",
            "end": "{" + end + "}",
            "origin": "{_environment_origin}",
            "radius": "{_environment_area.radius}",
        },
        ("_sphere_hit", "_sphere_entry", "_sphere_exit"),
    )


def _fog_clipping_tools():
    """Subtract actual source-qualified Water Cubes from Fog without erasing outside Fog."""
    faces = {
        "east": (0, 1),
        "west": (0, -1),
        "north": (1, 1),
        "south": (1, -1),
        "up": (2, 1),
        "down": (2, -1),
    }
    bounds = []
    for face, (axis, direction) in faces.items():
        minimum, maximum = [], []
        for i in range(3):
            minimum.append(
                f"record.origin[{i}] - _fog_water_side * 0.5"
                if i != axis
                else f"record.origin[{i}] - _fog_water_side"
                if direction < 0
                else f"record.origin[{i}]"
            )
            maximum.append(
                f"record.origin[{i}] + _fog_water_side * 0.5"
                if i != axis
                else f"record.origin[{i}] + _fog_water_side"
                if direction > 0
                else f"record.origin[{i}]"
            )
        bounds.append(
            _when(
                f"record.face == '{face}'",
                [
                    _set("_fog_water_min", minimum),
                    _set("_fog_water_max", maximum),
                ],
            )
        )
    return {
        "destroy_srd_environment_fog": _environment_tool(
            "Clear only Fog actually intersected by the admitted Destroy Water source Cube.",
            [_entity("caster_state"), _param("record", "object", from_state="state.turn_effects")],
            [
                _require(
                    "state.turn_effects != null", "The Water cast needs its saved source record."
                ),
                _set("_fog_water_records", "state.turn_effects"),
                _require(
                    "record in _fog_water_records and record.kind == 'object_content_spell' and "
                    "record.spell == 'create_or_destroy_water' and record.mode == 'destroy_fog' "
                    "and record.caster == _caster_state_id and record.started_tick == "
                    "state.lasting_tick "
                    f"and record.source_sha256 == '{WATER_SOURCE_SHA256}' and "
                    "record.dispel_ongoing == false",
                    "Select the actual current instantaneous Destroy Water cast record.",
                ),
                _require(
                    "record.slot_level >= 1 and record.slot_level <= 9 and len(record.origin) == 3 "
                    "and (record.face == 'east' or record.face == 'west' or record.face == 'north' "
                    "or record.face == 'south' or record.face == 'up' or record.face == 'down') "
                    "and record.volume.kind == 'box'",
                    "The Water record needs its source slot, face origin, orientation and Cube.",
                ),
                _set("_fog_water_side", "30 + 5 * (record.slot_level - 1)"),
                *bounds,
                _require(
                    " and ".join(
                        f"abs(record.volume.min[{i}] - _fog_water_min[{i}]) < 0.000000001 and "
                        f"abs(record.volume.max[{i}] - _fog_water_max[{i}]) < 0.000000001"
                        for i in range(3)
                    ),
                    "The saved Water Cube must equal the source-derived face and slot geometry.",
                ),
                _require(
                    " and ".join(
                        f"_fog_water_min[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                        f"_fog_water_max[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                        for i in range(3)
                    ),
                    "The source Water Cube must stay inside the actual admitted map bounds.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_caster_state_id}"},
                    POSITION_EXPORTS,
                ),
                _require(
                    "record.plane == _info_scene_entity.metadata.plane",
                    "The Water Cube must be on the caster's actual plane.",
                ),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_info_position}", "end": "{record.origin}"},
                    ("_scene_distance",),
                ),
                _require("_scene_distance <= 30", "The Water face origin must be within 30 feet."),
                _call(
                    "srd_information_barriers",
                    {"start": "{_info_position}", "end": "{record.origin}", "rule": "solid"},
                    ("_information_blocked",),
                ),
                _require(
                    "_information_blocked != true", "A solid barrier blocks the Water face origin."
                ),
                _set(
                    "_fog_water_volume",
                    {"kind": "'box'", "min": "_fog_water_min", "max": "_fog_water_max"},
                ),
                _set("_fog_water_changed_ids", []),
                *_areas(
                    [
                        _when(
                            "(_environment_area.mode == 'cloudkill' or "
                            "_environment_area.mode == 'stinking_cloud') and "
                            "_environment_area.plane == record.plane",
                            [
                                _call(
                                    "srd_information_volume_point",
                                    {
                                        "origin": "{_environment_area.origin}",
                                        "volume": "{_fog_water_volume}",
                                    },
                                    ("_volume_point",),
                                ),
                                _call(
                                    "measure_srd_information_distance",
                                    {
                                        "start": "{_environment_area.origin}",
                                        "end": "{_volume_point}",
                                    },
                                    ("_scene_distance",),
                                ),
                                _require(
                                    "_scene_distance >= _environment_area.radius",
                                    "Removing hazardous fog needs its actual remaining "
                                    "creature-space owner.",
                                ),
                            ],
                        ),
                        _when(
                            "_environment_area.mode == 'fog' and "
                            "_environment_area.plane == record.plane",
                            [
                                _call(
                                    "srd_information_volume_point",
                                    {
                                        "origin": "{_environment_area.origin}",
                                        "volume": "{_fog_water_volume}",
                                    },
                                    ("_volume_point",),
                                ),
                                _call(
                                    "measure_srd_information_distance",
                                    {
                                        "start": "{_environment_area.origin}",
                                        "end": "{_volume_point}",
                                    },
                                    ("_scene_distance",),
                                ),
                                _when(
                                    "_scene_distance <= _environment_area.radius",
                                    [
                                        _set("_fog_prior_clearances", []),
                                        _set("_fog_prior_water_sources", []),
                                        _when(
                                            "_environment_area.fog_clearances != null",
                                            [
                                                _set(
                                                    "_fog_prior_clearances",
                                                    "_environment_area.fog_clearances",
                                                ),
                                                _each(
                                                    "_fog_prior_clearances",
                                                    "_fog_prior_clearance",
                                                    [
                                                        _push(
                                                            "_fog_prior_water_sources",
                                                            "_fog_prior_clearance.water_source",
                                                        ),
                                                    ],
                                                ),
                                            ],
                                        ),
                                        _when(
                                            "record not in _fog_prior_water_sources",
                                            [
                                                _require(
                                                    "len(_fog_prior_clearances) < 64",
                                                    "This Fog source exceeds the supported saved "
                                                    "Water clearance limit.",
                                                ),
                                                _push(
                                                    "_fog_prior_clearances",
                                                    {
                                                        "volume": "_fog_water_volume",
                                                        "water_source": "record",
                                                    },
                                                ),
                                                _set(
                                                    "state.persistent_spell_areas.{_environme"
                                                    "nt_id}.fog_clearances",
                                                    "_fog_prior_clearances",
                                                ),
                                                _push("_fog_water_changed_ids", "_environment_id"),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ]
                ),
            ],
        ),
        "resolve_srd_environment_fog_segment": _environment_tool(
            "Resolve remaining Fog by subtracting the exact union of source Water intervals.",
            [_array("start"), _array("end"), _param("area_id")],
            [
                _set("_fog_segment_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_fog_segment_source.profile == 'environment' and "
                    "_fog_segment_source.mode == 'fog' and _fog_segment_source.active == true "
                    "and state.lasting_tick < _fog_segment_source.expires_tick",
                    "Select an actual active Fog source.",
                ),
                _call(
                    "resolve_srd_persistent_area_segment",
                    {
                        "start": "{start}",
                        "end": "{end}",
                        "origin": "{_fog_segment_source.origin}",
                        "radius": "{_fog_segment_source.radius}",
                    },
                    ("_sphere_hit", "_sphere_entry", "_sphere_exit"),
                ),
                _set("_fog_segment_present", "_sphere_hit"),
                _when(
                    "_sphere_hit == true and _fog_segment_source.fog_clearances != null",
                    [
                        _set("_fog_clearances", "_fog_segment_source.fog_clearances"),
                        _set("_fog_clear_intervals", []),
                        _set("_fog_tangent_cleared", False),
                        _each(
                            "_fog_clearances",
                            "_fog_clearance",
                            [
                                _call(
                                    "srd_information_segment_interval",
                                    {
                                        "start": "{start}",
                                        "end": "{end}",
                                        "volume": "{_fog_clearance.volume}",
                                    },
                                    ("_segment_hit", "_segment_entry", "_segment_exit"),
                                ),
                                _when(
                                    "_segment_hit == true and _segment_entry <= _sphere_exit and "
                                    "_segment_exit >= _sphere_entry",
                                    [
                                        _push(
                                            "_fog_clear_intervals",
                                            {
                                                "entry": "max(_sphere_entry, _segment_entry)",
                                                "exit": "min(_sphere_exit, _segment_exit)",
                                            },
                                        ),
                                        _when(
                                            "_segment_entry <= _sphere_entry and "
                                            "_segment_exit >= _sphere_exit",
                                            [_set("_fog_tangent_cleared", True)],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        {
                            "sort": {
                                "collection": "_fog_clear_intervals",
                                "by": "entry",
                                "direction": "asc",
                            }
                        },
                        _set("_fog_clear_cursor", "_sphere_entry"),
                        _set("_fog_segment_present", False),
                        _each(
                            "_fog_clear_intervals",
                            "_fog_clear_interval",
                            [
                                _when(
                                    "_fog_clear_interval.entry - _fog_clear_cursor > "
                                    "0.000000000001",
                                    [
                                        _set("_fog_segment_present", True),
                                    ],
                                ),
                                _set(
                                    "_fog_clear_cursor",
                                    "max(_fog_clear_cursor, _fog_clear_interval.exit)",
                                ),
                            ],
                        ),
                        _when(
                            "_sphere_exit - _fog_clear_cursor > 0.000000000001",
                            [_set("_fog_segment_present", True)],
                        ),
                        _when(
                            "_sphere_entry == _sphere_exit",
                            [
                                _set("_fog_segment_present", "_fog_tangent_cleared != true"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }


def _sphere_geometry_tools():
    """Reuse creature-body Sphere intervals with the actual saved source point as origin."""
    original = _emanation_geometry_tools()["resolve_srd_environment_body_segment"]
    effects = deepcopy(original["effects"])
    # The shared affine and Sphere mathematics stay intact. A saved spell point replaces
    # only the fixed creature's admitted bounds; no scene proxy or position is invented.
    remaining = effects[6:]
    plane_branch = remaining[-1]
    assert plane_branch["condition"] == "_body_moving.metadata.plane == _body_fixed.metadata.plane"
    plane_branch["condition"] = "_body_moving.metadata.plane == _sphere_geometry_source.plane"
    plane_branch["on_true"][0] = _set(
        "_body_bounds",
        {
            "min": [
                f"_sphere_geometry_source.origin[{i}] - _body_moving.footprint.half_extents[{i}]"
                for i in range(2)
            ]
            + ["_sphere_geometry_source.origin[2] - _body_moving.height"],
            "max": [
                f"_sphere_geometry_source.origin[{i}] + _body_moving.footprint.half_extents[{i}]"
                for i in range(2)
            ]
            + ["_sphere_geometry_source.origin[2]"],
        },
    )
    plane_branch["on_true"] = [
        e for e in plane_branch["on_true"] if e.get("condition") != "point_space == true"
    ]

    def bind_radius(value):
        if isinstance(value, str):
            return re.sub(r"\bradius\b", "_sphere_geometry_source.radius", value)
        if isinstance(value, list):
            return [bind_radius(item) for item in value]
        if isinstance(value, dict):
            return {key: bind_radius(item) for key, item in value.items()}
        return value

    tools = {
        "resolve_srd_environment_sphere_body_segment": _environment_tool(
            "Resolve an actual moving box creature against this saved spell Sphere.",
            [_param("moving_subject"), _param("area_id"), _array("start"), _array("end")],
            [
                *effects[:3],
                _set("_sphere_geometry_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_sphere_geometry_source.profile == 'environment' and "
                    + _area_entry_mode("_sphere_geometry_source")
                    + " and _body_moving.footprint.kind == 'box'",
                    "Spell Sphere entry requires its actual source and admitted box creature.",
                ),
                *bind_radius(remaining),
            ],
        ),
        "qualify_srd_environment_sphere_source": _environment_tool(
            "Read the actual current concentration and saved Sphere source.",
            [_entity("caster_state"), _param("area_id")],
            [
                _set("_sphere_source", "state.persistent_spell_areas[area_id]"),
                _set(
                    "_sphere_source_current",
                    "_sphere_source.profile == 'environment' and "
                    + _sphere_mode("_sphere_source")
                    + " and _sphere_source.caster == _caster_state_id and "
                    "_sphere_source.active == true and state.lasting_tick < "
                    "_sphere_source.expires_tick",
                ),
                _set(
                    "_sphere_source_current",
                    "_sphere_source_current == true and caster_state.environment_area_id == "
                    "area_id and caster_state.concentration_spell_id == _sphere_source.spell "
                    "and caster_state.concentration_effect_family == 'spell_environment'",
                ),
            ],
        ),
        "qualify_srd_environment_sphere_target": _environment_tool(
            "Read actual living creature-space Sphere membership and physical exposure.",
            [_entity("target_state"), _param("area_id")],
            [
                _set("_sphere_target_source", "state.persistent_spell_areas[area_id]"),
                _set("_sphere_target_inside", False),
                _call(
                    "qualify_srd_environment_sphere_source",
                    {
                        "caster_state": "{_sphere_target_source.caster}",
                        "area_id": "{area_id}",
                    },
                    ("_sphere_source_current",),
                ),
                _when(
                    "_sphere_source_current == true and target_state.is_alive == true",
                    [
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_target_state_id}"},
                            POSITION_EXPORTS,
                        ),
                        _require(
                            "_info_scene_entity.footprint.kind == 'box'",
                            "Creature-space Sphere membership requires an admitted box footprint.",
                        ),
                        _when(
                            "_info_scene_entity.metadata.plane == _sphere_target_source.plane",
                            [
                                _set(
                                    "_sphere_nearest_body_point",
                                    [
                                        f"min(max(_sphere_target_source.origin[{i}], "
                                        f"_info_position[{i}] - _info_scene_entity.footprint."
                                        f"half_extents[{i}]), _info_position[{i}] + "
                                        f"_info_scene_entity.footprint.half_extents[{i}])"
                                        for i in range(2)
                                    ]
                                    + [
                                        "min(max(_sphere_target_source.origin[2], "
                                        "_info_position[2]), "
                                        "_info_position[2] + _info_scene_entity.height)"
                                    ],
                                ),
                                _call(
                                    "measure_srd_information_distance",
                                    {
                                        "start": "{_sphere_target_source.origin}",
                                        "end": "{_sphere_nearest_body_point}",
                                    },
                                    ("_scene_distance",),
                                ),
                                _when(
                                    "_scene_distance <= _sphere_target_source.radius + 0.000000001",
                                    [
                                        *_snapshot(),
                                        _spatial(
                                            "exposure",
                                            {
                                                "map_id": "{state.spatial_scene.map.id}",
                                                "observer": {
                                                    "point": "{_sphere_target_source.origin}"
                                                },
                                                "target": {"entity_id": "{_info_scene_entity.id}"},
                                                "samples": {"horizontal": 3, "vertical": 3},
                                                "include": ["geometry"],
                                            },
                                            "_sphere_target_exposure",
                                        ),
                                        _set(
                                            "_sphere_target_inside",
                                            "_sphere_target_exposure.visible_samples > 0",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }
    _bind_moonbeam_geometry(tools)
    return tools


def _bind_moonbeam_geometry(tools):
    """Reuse body-circle affine intervals and the existing vertical slab for the Cylinder."""
    if __package__:
        from .srd_information_spells import _interval_effects
    else:
        from srd_information_spells import _interval_effects

    body = deepcopy(tools["resolve_srd_environment_sphere_body_segment"])
    body["description"] = "Resolve an actual moving box creature against the saved source Cylinder."
    source_requirement = next(
        e["require"]
        for e in body["effects"]
        if "_sphere_geometry_source.profile" in e.get("require", {}).get("condition", "")
    )
    source_requirement["condition"] += " and " + _cylinder_mode("_sphere_geometry_source")
    plane = body["effects"][-1]["on_true"]
    plane[0]["set"]["value"]["min"][2] = (
        "_sphere_geometry_source.cylinder_min - _body_moving.height"
    )
    plane[0]["set"]["value"]["max"][2] = "_sphere_geometry_source.cylinder_max"
    for effects in _effect_lists(plane):
        for effect in effects:
            field = effect.get("set", {}).get("field")
            if field in ("_body_affine_start", "_body_affine_end"):
                effect["set"]["value"][2] = 0
            elif field in ("_body_a_2", "_body_b_2"):
                effect["set"]["value"] = 0

    def slab_fields(value):
        if isinstance(value, str):
            return value.replace("volume.", "_body_bounds.").replace("_segment_", "_body_")
        if isinstance(value, list):
            return [slab_fields(item) for item in value]
        if isinstance(value, dict):
            return {key: slab_fields(item) for key, item in value.items()}
        return value

    # Take the vertical slab directly from the proven interval owner. Horizontal body
    # membership above is exact rounded-box distance, not an inflated circular footprint.
    interval = _interval_effects()
    vertical = interval[6]["on_true"]
    vertical = [e for e in vertical if e.get("condition") != "volume.kind == 'cylinder'"]
    plane.extend(
        [
            slab_fields(interval[3]),
            *slab_fields(vertical),
            _set("_body_hit", "_body_hit == true and _body_entry <= _body_exit"),
        ]
    )
    tools["resolve_srd_environment_cylinder_body_segment"] = body
    sphere = tools["resolve_srd_environment_sphere_body_segment"]
    sphere["effects"] = [
        _set("_area_body_source", "state.persistent_spell_areas[area_id]"),
        _when(
            _cylinder_mode("_area_body_source"),
            [
                _call(
                    "resolve_srd_environment_cylinder_body_segment",
                    {
                        "moving_subject": "{moving_subject}",
                        "area_id": "{area_id}",
                        "start": "{start}",
                        "end": "{end}",
                    },
                    ("_body_hit", "_body_entry", "_body_exit", "_body_bounds"),
                )
            ],
            sphere["effects"],
        ),
    ]
    target = tools["qualify_srd_environment_sphere_target"]
    for effects in _effect_lists(target["effects"]):
        for index, effect in list(enumerate(effects)):
            if (
                effect.get("condition")
                == "_scene_distance <= _sphere_target_source.radius + 0.000000001"
            ):
                effects[index:index] = [
                    _set("_sphere_membership", effect["condition"]),
                    _when(
                        _cylinder_mode("_sphere_target_source"),
                        [
                            _call(
                                "resolve_srd_environment_cylinder_body_segment",
                                {
                                    "moving_subject": "{_target_state_id}",
                                    "area_id": "{area_id}",
                                    "start": "{_info_position}",
                                    "end": "{_info_position}",
                                },
                                {"_sphere_membership": "_body_hit"},
                            )
                        ],
                    ),
                ]
                effect["condition"] = "_sphere_membership == true"


def _sphere_hazard_tools():
    """Own initial, entry and ending-turn Sphere saves through the existing typed packet."""
    return {
        **_sphere_geometry_tools(),
        "qualify_srd_environment_sphere_effect": _environment_tool(
            "Use the strongest actual overlapping Sphere effect, then the latest tie.",
            [_entity("target_state"), _param("area_id")],
            [
                _set("_sphere_selected", "state.persistent_spell_areas[area_id]"),
                _set("_sphere_effect_applies", True),
                *_areas(
                    [
                        _when(
                            "_environment_id != area_id and _environment_area.spell == "
                            "_sphere_selected.spell and (_environment_area.spell_level > "
                            "_sphere_selected.spell_level or (_environment_area.spell_level == "
                            "_sphere_selected.spell_level and _environment_area.sequence > "
                            "_sphere_selected.sequence))",
                            [
                                _call(
                                    "qualify_srd_environment_sphere_target",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "area_id": "{_environment_id}",
                                    },
                                    ("_sphere_target_inside",),
                                ),
                                _when(
                                    "_sphere_target_inside == true",
                                    [_set("_sphere_effect_applies", False)],
                                ),
                            ],
                        )
                    ]
                ),
            ],
        ),
        "resolve_srd_environment_sphere_event": _environment_tool(
            "Force one actual Sphere save once per turn and apply its genuine typed roll.",
            [
                _entity("target_state"),
                _param("area_id"),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            [
                _set("_sphere_event_source", "state.persistent_spell_areas[area_id]"),
                _set("_sphere_clock", "'exploration'"),
                _set("_sphere_turn", "state.lasting_tick"),
                _when(
                    "combat.turn_active == true",
                    [
                        _set("_sphere_clock", "'combat'"),
                        _set("_sphere_turn", "combat.turn_sequence"),
                    ],
                ),
                _set("_sphere_prior_targets", []),
                _when(
                    "_sphere_event_source.event_clock == _sphere_clock and "
                    "_sphere_event_source.event_turn == _sphere_turn",
                    [_set("_sphere_prior_targets", "_sphere_event_source.event_target_ids")],
                ),
                _when(
                    "_target_state_id not in _sphere_prior_targets",
                    [
                        _call(
                            "qualify_srd_environment_sphere_target",
                            {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                            ("_sphere_target_inside",),
                        ),
                        _when(
                            "_sphere_event_source.appearance_in_progress == true and "
                            "_sphere_event_source.appearance_tick == state.lasting_tick and "
                            "_sphere_event_source.appearance_sequence == "
                            "_sphere_event_source.sequence "
                            "and _target_state_id in _sphere_event_source.appearance_targets and "
                            "target_state.is_alive == true",
                            [_set("_sphere_target_inside", True)],
                        ),
                        _when(
                            "_sphere_target_inside == true",
                            [
                                _call(
                                    "qualify_srd_environment_sphere_effect",
                                    {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                                    ("_sphere_effect_applies",),
                                ),
                                _when(
                                    "_sphere_effect_applies == true",
                                    [
                                        {
                                            "table_lookup": {
                                                "table": "definitions.spells",
                                                "key": "_sphere_event_source.spell",
                                                "required": True,
                                            },
                                            "store_as": "_sphere_profile",
                                        },
                                        _require(
                                            "target_state.saving_throw_modifiers["
                                            "_sphere_profile.save_ability] != null",
                                            "Use this creature's actual saving throw modifier.",
                                        ),
                                        _set(
                                            "_sphere_save_modifier",
                                            "target_state.saving_throw_modifiers["
                                            "_sphere_profile.save_ability]",
                                        ),
                                        _call(
                                            "d20_test",
                                            {
                                                "actor_state": "{_target_state_id}",
                                                "kind": "saving_throw",
                                                "ability": "{_sphere_profile.save_ability}",
                                                "ability_modifier": "{_sphere_save_modifier}",
                                                "proficiency_bonus": 0,
                                                "proficiency_factor": "none",
                                                "circumstantial_modifier": 0,
                                                "target_number": "{_sphere_event_source.save_dc}",
                                                "advantage": False,
                                                "disadvantage": False,
                                            },
                                            ("_d20_test", "_outcome"),
                                        ),
                                        _set(
                                            "_sphere_dice",
                                            "_sphere_profile.damage_dice + max(0, "
                                            "_sphere_event_source.spell_level - "
                                            "_sphere_profile.base_level) * "
                                            "_sphere_profile.damage_per_slot_level",
                                        ),
                                        _call(
                                            "format_srd_information_projection",
                                            {
                                                ("message"): (
                                                    "{_sphere_dice}{_sphere_profile.damage_die}"
                                                )
                                            },
                                            {"_sphere_expression": "_info_projection_message"},
                                        ),
                                        {
                                            "dice": {"expression": "{_sphere_expression}"},
                                            "store_as": "_sphere_damage_roll",
                                        },
                                        _call(
                                            "resolve_srd_actor_spell_modifiers",
                                            {
                                                "target_state": "{_sphere_event_source.caster}",
                                                "lasting_state": "state",
                                            },
                                        ),
                                        _call(
                                            "modify_srd_actor_raw_damage_roll",
                                            {
                                                "actor_state": "{_sphere_event_source.caster}",
                                                "lasting_state": "state",
                                                "amount": "{_sphere_damage_roll.total}",
                                            },
                                            {"_sphere_amount": "_ray_raw_damage"},
                                        ),
                                        _set(
                                            "_sphere_unallocated_penalty", "max(0, -_sphere_amount)"
                                        ),
                                        _set("_sphere_amount", "max(0, _sphere_amount)"),
                                        _set("_sphere_roll_divisor", 1),
                                        _when(
                                            "_outcome == 'success'",
                                            [_set("_sphere_roll_divisor", 2)],
                                        ),
                                        _set(
                                            "_sphere_roll_remainder",
                                            "_sphere_amount % _sphere_roll_divisor",
                                        ),
                                        _set(
                                            "_sphere_amount",
                                            "_sphere_amount // _sphere_roll_divisor",
                                        ),
                                        _push("_sphere_prior_targets", "_target_state_id"),
                                        _set(
                                            "state.persistent_spell_areas.{area_id}.event_clock",
                                            "_sphere_clock",
                                        ),
                                        _set(
                                            "state.persistent_spell_areas.{area_id}.event_turn",
                                            "_sphere_turn",
                                        ),
                                        _set(
                                            "state.persistent_spell_areas.{area_id}.event_target_id"
                                            "s",
                                            "_sphere_prior_targets",
                                        ),
                                        _call(
                                            "apply_srd_qualifying_damage",
                                            {
                                                "target": "{_target_state_id}",
                                                "amount": "{_sphere_amount}",
                                                "adjustment": 0,
                                                "damage_type": "{_sphere_profile.damage_type}",
                                                "source_state_id": "{_sphere_event_source.caster}",
                                                "witness_state_ids": "{witness_state_ids}",
                                                "unallocated_roll_penalty": (
                                                    "{_sphere_unallocated_penalty}"
                                                ),
                                                "game_damage_roll": True,
                                                "damage_roll_divisor": "{_sphere_roll_divisor}",
                                                "damage_roll_base_remainder": (
                                                    "{_sphere_roll_remainder}"
                                                ),
                                            },
                                            ("_effective_damage",),
                                        ),
                                        _set(
                                            "target_state.last_environment_sphere_save",
                                            {
                                                "area_id": "area_id",
                                                "spell": "_sphere_event_source.spell",
                                                "origin": "_sphere_event_source.origin",
                                                "turn": "_sphere_turn",
                                                "clock": "_sphere_clock",
                                                "roll": "_d20_test",
                                                "outcome": "_outcome",
                                                "raw_damage": "_sphere_damage_roll",
                                                "applied_damage": "_effective_damage",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "resolve_srd_environment_sphere_scene": _environment_tool(
            "Resolve the source's initial save for each actual admitted scene creature.",
            [_param("area_id")],
            [
                _set("_sphere_scene_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_sphere_scene_source.profile == 'environment' and "
                    + _sphere_damage_mode("_sphere_scene_source")
                    + " and _sphere_scene_source.active == true and "
                    "_sphere_scene_source.appearance_completed != true",
                    "The initial Sphere appearance can settle only once for its actual source.",
                ),
                _set("_sphere_initial_targets", []),
                _set("_sphere_scene_entries", "state.spatial_scene.entities"),
                _each(
                    "_sphere_scene_entries",
                    "_sphere_scene_entry",
                    [
                        _when(
                            "_sphere_scene_entry.active == true and "
                            "_sphere_scene_entry.metadata.participant_ref != null",
                            [
                                _call(
                                    "qualify_srd_environment_sphere_target",
                                    {
                                        ("target_state"): (
                                            "{_sphere_scene_entry.metadata.participant_ref}"
                                        ),
                                        "area_id": "{area_id}",
                                    },
                                    ("_sphere_target_inside",),
                                ),
                                _when(
                                    "_sphere_target_inside == true",
                                    [
                                        _push(
                                            "_sphere_initial_targets",
                                            "_sphere_scene_entry.metadata.participant_ref",
                                        )
                                    ],
                                ),
                            ],
                        )
                    ],
                ),
                _set(
                    "state.persistent_spell_areas.{area_id}.appearance_targets",
                    "_sphere_initial_targets",
                ),
                _set(
                    "state.persistent_spell_areas.{area_id}.appearance_tick", "state.lasting_tick"
                ),
                _set(
                    "state.persistent_spell_areas.{area_id}.appearance_sequence",
                    "_sphere_scene_source.sequence",
                ),
                _set("state.persistent_spell_areas.{area_id}.appearance_in_progress", True),
                _each(
                    "_sphere_initial_targets",
                    "_sphere_initial_target",
                    [
                        _call(
                            "resolve_srd_environment_sphere_event",
                            {"target_state": "{_sphere_initial_target}", "area_id": "{area_id}"},
                        )
                    ],
                ),
                _set("state.persistent_spell_areas.{area_id}.appearance_in_progress", False),
                _set("state.persistent_spell_areas.{area_id}.appearance_targets", []),
                _set("state.persistent_spell_areas.{area_id}.appearance_completed", True),
            ],
        ),
        "resolve_srd_environment_sphere_terrain_intervals": _environment_tool(
            "Add actual spell Sphere terrain to the established route cost union.",
            [
                _param("actor_subject"),
                _array("start"),
                _array("end"),
                _optional("ignore_ground_environments", "boolean", False),
            ],
            [
                _set("_sphere_route_intervals", []),
                _when(
                    "ignore_ground_environments != true",
                    [
                        _call(
                            "resolve_srd_minor_terrain_intervals",
                            {
                                "actor_subject": "{actor_subject}",
                                "start": "{start}",
                                "end": "{end}",
                            },
                            {"_sphere_route_intervals": "_minor_route_intervals"},
                        )
                    ],
                ),
                _call(
                    "srd_information_scene_entity", {"subject": "{actor_subject}"}, POSITION_EXPORTS
                ),
                _set("_sphere_route_plane", "_info_scene_entity.metadata.plane"),
                *_areas(
                    [
                        _when(
                            "_environment_area.mode == 'insects' and "
                            "_environment_area.plane == _sphere_route_plane",
                            [
                                _call(
                                    "qualify_srd_environment_sphere_source",
                                    {
                                        "caster_state": "{_environment_area.caster}",
                                        "area_id": "{_environment_id}",
                                    },
                                    ("_sphere_source_current",),
                                ),
                                _when(
                                    "_sphere_source_current == true",
                                    [
                                        *_origin(),
                                        _interval("start", "end"),
                                        _when(
                                            "_sphere_hit == true",
                                            [
                                                _push(
                                                    "_sphere_route_intervals",
                                                    {
                                                        "entry": "_sphere_entry",
                                                        "exit": "_sphere_exit",
                                                        "sphere_source": True,
                                                        "origin": "_environment_area.origin",
                                                        "plane": "_environment_area.plane",
                                                    },
                                                )
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        )
                    ]
                ),
            ],
        ),
    }


def _stinking_cloud_tools():
    """Bind current-turn Poisoned to the existing condition pool and actual target boundary."""
    pointer = "target_state.stinking_cloud_condition_source_id"
    return {
        "project_srd_stinking_cloud_poison": _environment_tool(
            "Project actual timed poison for this managed target without replacing other owners.",
            [_entity("target_state")],
            [
                _set("_stinking_projected_poison", False),
                _set("_stinking_installed_poison", False),
                _call(
                    "check_srd_target_condition_immunity",
                    {
                        "target_state": "{_target_state_id}",
                        "condition": "poisoned",
                        "source_is_magical": True,
                    },
                    {"_stinking_target_immune": "_source_condition_immune"},
                ),
                _when(
                    "target_state.has_progressive_sources == true and "
                    "'poisoned' in target_state.conditions",
                    [_set("_stinking_projected_poison", True)],
                ),
                _set("_stinking_projection_ids", []),
                _when(
                    "target_state.timed_condition_source_ids != null",
                    [_set("_stinking_projection_ids", "target_state.timed_condition_source_ids")],
                ),
                _each(
                    "_stinking_projection_ids",
                    "_stinking_projection_id",
                    [
                        _set(
                            "_stinking_projection_source",
                            "state.timed_condition_sources[_stinking_projection_id]",
                        ),
                        _when(
                            "_stinking_projection_source.installed == true and "
                            "_stinking_projection_source.target_state_id == _target_state_id and "
                            "_stinking_projection_source.condition == 'poisoned'",
                            [
                                _set("_stinking_installed_poison", True),
                                _when(
                                    "_stinking_projection_source.spell_id == 'stinking_cloud'",
                                    [
                                        _set(
                                            "state.timed_condition_sources."
                                            "{_stinking_projection_id}.suppressed",
                                            "_stinking_target_immune",
                                        ),
                                        _set(
                                            "_stinking_projection_source",
                                            "state.timed_condition_sources[_stinking_projection_id]"
                                            "",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        _when(
                            "_stinking_projection_source.installed == true and "
                            "_stinking_projection_source.target_state_id == _target_state_id and "
                            "_stinking_projection_source.condition == 'poisoned' and "
                            "_stinking_projection_source.control_profile != 'contagion' and "
                            "_stinking_projection_source.suppressed != true",
                            [_set("_stinking_projected_poison", True)],
                        ),
                    ],
                ),
                {"remove_condition": {"target": "target_state", "condition": "poisoned"}},
                _when(
                    "_stinking_projected_poison == true or "
                    "target_state.timed_condition_unowned_poisoned == true or "
                    "target_state.turn_effect_poisoned == true",
                    [{"apply_condition": {"target": "target_state", "condition": "poisoned"}}],
                    [
                        _when(
                            "_stinking_installed_poison != true",
                            [_set("target_state.stinking_cloud_managed_poison", False)],
                        )
                    ],
                ),
            ],
        ),
        "qualify_srd_stinking_cloud_poison": _environment_tool(
            "Read only this creature's exact installed current-turn Stinking Cloud poison.",
            [_entity("target_state")],
            [
                _set("_stinking_poison_active", False),
                _when(
                    pointer + " != null",
                    [
                        _set(
                            "_stinking_poison_source", f"state.timed_condition_sources[{pointer}]"
                        ),
                        _when(
                            "_stinking_poison_source.installed == true and "
                            "_stinking_poison_source.spell_id == 'stinking_cloud' and "
                            "_stinking_poison_source.target_state_id == _target_state_id and "
                            "_stinking_poison_source.sequence == "
                            "target_state.stinking_cloud_condition_sequence",
                            [
                                _set(
                                    "_stinking_poison_active",
                                    "_stinking_poison_source.condition == 'poisoned' and "
                                    "_stinking_poison_source.suppressed != true and "
                                    "'poisoned' in target_state.conditions and "
                                    "combat.turn_active == true and "
                                    "combat.active_actor == _target_state_id and "
                                    "_stinking_poison_source.environment_target_turn == "
                                    "target_state.turns_started and "
                                    "_stinking_poison_source.environment_turn_sequence == "
                                    "combat.turn_sequence",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "retire_srd_stinking_cloud_turn": _environment_tool(
            "Retire only the exact poison allocated on this actual ending target turn.",
            [_entity("target_state")],
            [
                _call(
                    "qualify_srd_stinking_cloud_poison",
                    {"target_state": "{_target_state_id}"},
                    ("_stinking_poison_source",),
                ),
                _when(
                    pointer + " != null and combat.turn_active == true and "
                    "combat.active_actor == _target_state_id",
                    [
                        _when(
                            "_stinking_poison_source.installed == true and "
                            "_stinking_poison_source.spell_id == 'stinking_cloud' and "
                            "_stinking_poison_source.target_state_id == _target_state_id and "
                            "_stinking_poison_source.sequence == "
                            "target_state.stinking_cloud_condition_sequence and "
                            "_stinking_poison_source.environment_target_turn == "
                            "target_state.turns_started",
                            [
                                _call(
                                    "retire_srd_timed_condition_source",
                                    {"lasting_state": "state", "source_id": "{" + pointer + "}"},
                                ),
                                _call(
                                    "refresh_srd_timed_condition_target",
                                    {
                                        "lasting_state": "state",
                                        "target_state": "{_target_state_id}",
                                    },
                                ),
                            ],
                        ),
                        _set(pointer, None),
                        _set("target_state.stinking_cloud_condition_sequence", None),
                    ],
                ),
            ],
        ),
        "resolve_srd_stinking_cloud_start_turn": _environment_tool(
            "Force the actual creature's start-turn Constitution save in the current gas Sphere.",
            [_entity("target_state"), _param("area_id")],
            [
                _require(
                    "combat.turn_active == true and combat.active_actor == _target_state_id",
                    "Stinking Cloud saves occur only on this creature's actual start turn.",
                ),
                _set("_stinking_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_stinking_source.spell == 'stinking_cloud'",
                    "Select the actual Stinking Cloud source.",
                ),
                _set("_stinking_prior_targets", []),
                _when(
                    "_stinking_source.event_clock == 'combat' and "
                    "_stinking_source.event_turn == combat.turn_sequence",
                    [_set("_stinking_prior_targets", "_stinking_source.event_target_ids")],
                ),
                _when(
                    "_target_state_id not in _stinking_prior_targets and "
                    "(target_state.last_stinking_cloud_save == null or "
                    "target_state.last_stinking_cloud_save.turn_sequence != "
                    "combat.turn_sequence or "
                    "target_state.last_stinking_cloud_save.target_turn != "
                    "target_state.turns_started)",
                    [
                        _call(
                            "qualify_srd_environment_sphere_target",
                            {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                            ("_sphere_target_inside",),
                        ),
                        _when(
                            "_sphere_target_inside == true",
                            [
                                _call(
                                    "qualify_srd_environment_sphere_effect",
                                    {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                                    ("_sphere_effect_applies",),
                                ),
                                _when(
                                    "_sphere_effect_applies == true",
                                    [
                                        _require(
                                            "target_state.saving_throw_modifiers."
                                            "constitution != null",
                                            "Use this creature's actual Constitution "
                                            "save modifier.",
                                        ),
                                        _call(
                                            "d20_test",
                                            {
                                                "actor_state": "{_target_state_id}",
                                                "kind": "saving_throw",
                                                "ability": "constitution",
                                                "ability_modifier": (
                                                    "{target_state.saving_throw_modifiers."
                                                    "constitution}"
                                                ),
                                                "proficiency_bonus": 0,
                                                "proficiency_factor": "none",
                                                "circumstantial_modifier": 0,
                                                "target_number": "{_stinking_source.save_dc}",
                                                "advantage": False,
                                                "disadvantage": False,
                                            },
                                            ("_d20_test", "_outcome"),
                                        ),
                                        _call(
                                            "check_srd_target_condition_immunity",
                                            {
                                                "target_state": "{_target_state_id}",
                                                "condition": "poisoned",
                                                "source_is_magical": True,
                                            },
                                            {
                                                "_target_condition_immune": (
                                                    "_source_condition_immune"
                                                )
                                            },
                                        ),
                                        _set("_stinking_allocated_source_id", None),
                                        _when(
                                            "_outcome != 'success' and "
                                            "_target_condition_immune != true",
                                            [
                                                _set(
                                                    "target_state.stinking_cloud_managed_poison",
                                                    True,
                                                ),
                                                _call(
                                                    "install_srd_environment_poisoned",
                                                    {
                                                        "target_state": "{_target_state_id}",
                                                        "lasting_state": "state",
                                                        "spell": "stinking_cloud",
                                                        "condition": "poisoned",
                                                        "save_dc": "{_stinking_source.save_dc}",
                                                        "duration_rounds": 0,
                                                        "repeat_save_timing": "none",
                                                        "save_ability": "constitution",
                                                        "actual_spell_level": (
                                                            "{_stinking_source.spell_level}"
                                                        ),
                                                        "spell_cast_owner": (
                                                            "{_stinking_source.caster}"
                                                        ),
                                                    },
                                                    {
                                                        "_stinking_allocated_source_id": (
                                                            "_timed_condition_source_id"
                                                        )
                                                    },
                                                ),
                                                _set(pointer, "_stinking_allocated_source_id"),
                                                _set(
                                                    "target_state.stinking_cloud_condition_sequence"
                                                    "",
                                                    "state.timed_condition_sources[_stinking_alloca"
                                                    "ted_source_id].sequence",
                                                ),
                                                _set(
                                                    "state.timed_condition_sources."
                                                    "{_stinking_allocated_source_id}.environment_target_turn",
                                                    "target_state.turns_started",
                                                ),
                                                _set(
                                                    "state.timed_condition_sources."
                                                    "{_stinking_allocated_source_id}.environment_turn_sequence",
                                                    "combat.turn_sequence",
                                                ),
                                                _set(
                                                    "state.timed_condition_sources."
                                                    "{_stinking_allocated_source_id}.environment_source_id",
                                                    "area_id",
                                                ),
                                                _set(
                                                    "state.timed_condition_sources."
                                                    "{_stinking_allocated_source_id}.environment_source_sequence",
                                                    "_stinking_source.sequence",
                                                ),
                                                _set(
                                                    "state.timed_condition_sources."
                                                    "{_stinking_allocated_source_id}.ongoing_duration",
                                                    True,
                                                ),
                                            ],
                                        ),
                                        _push("_stinking_prior_targets", "_target_state_id"),
                                        _set(
                                            "state.persistent_spell_areas.{area_id}.event_clock",
                                            "'combat'",
                                        ),
                                        _set(
                                            "state.persistent_spell_areas.{area_id}.event_turn",
                                            "combat.turn_sequence",
                                        ),
                                        _set(
                                            "state.persistent_spell_areas.{area_id}.event_target_id"
                                            "s",
                                            "_stinking_prior_targets",
                                        ),
                                        _set(
                                            "target_state.last_stinking_cloud_save",
                                            {
                                                "area_id": "area_id",
                                                "source_sequence": "_stinking_source.sequence",
                                                "roll": "_d20_test",
                                                "outcome": "_outcome",
                                                "immune": "_target_condition_immune",
                                                "condition_source_id": (
                                                    "_stinking_allocated_source_id"
                                                ),
                                                "turn_sequence": "combat.turn_sequence",
                                                "target_turn": "target_state.turns_started",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }


def _bind_stinking_cloud(changed):
    """Settle the exact target boundary even after the gas source or concentration ends."""
    starting = changed["resolve_srd_environment_hazard_start_turn"]
    loop = next(e["for_each"] for e in starting["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'stinking_cloud'",
            [
                _call(
                    "resolve_srd_stinking_cloud_start_turn",
                    {"target_state": "{_target_state_id}", "area_id": "{_environment_id}"},
                )
            ],
        )
    )
    changed["resolve_srd_environment_hazard_end_turn"]["effects"].append(
        _when(
            "target_state.stinking_cloud_condition_source_id != null",
            [_call("retire_srd_stinking_cloud_turn", {"target_state": "{_target_state_id}"})],
        )
    )
    for effect in changed["end_combat_turn"]["effects"]:
        if any(
            e.get("call") == "resolve_srd_environment_hazard_end_turn"
            for e in effect.get("on_true", [])
        ):
            effect["condition"] = (
                "state.has_spell_environments == true or "
                "actor.stinking_cloud_condition_source_id != null"
            )


def _bind_stinking_cloud_budget(tools):
    """Guard real Action and Bonus Action spending from the exact installed poison source."""
    changed = {}
    for name in ("check_srd_active_control_action", "consume_srd_bonus_action"):
        tool = deepcopy(tools[name])
        if "qualify_srd_stinking_cloud_poison" not in str(tool["effects"]):
            tool["effects"].insert(
                0,
                _when(
                    "actor_state.stinking_cloud_condition_source_id != null",
                    [
                        _call(
                            "qualify_srd_stinking_cloud_poison",
                            {"target_state": "{_actor_state_id}"},
                            ("_stinking_poison_active",),
                        ),
                        _require(
                            "_stinking_poison_active != true",
                            "Stinking Cloud poison prevents Actions and Bonus Actions this turn.",
                        ),
                    ],
                ),
            )
        changed[name] = tool
    return changed


def _cloudkill_tools():
    """Move the actual saved Cloudkill Sphere on its caster's genuine later turn."""
    current = _call(
        "qualify_srd_environment_sphere_source",
        {"caster_state": "{_caster_state_id}", "area_id": "{area_id}"},
        {"_cloud_source_current": "_sphere_source_current"},
    )
    candidate = [
        _call(
            "qualify_srd_environment_sphere_motion_creature",
            {"target_state": "{_cloud_candidate.metadata.participant_ref}"},
            ("_sphere_motion_creature",),
        ),
        _when(
            "_sphere_motion_creature == true",
            [
                _set("_cloud_target_start", "_cloud_candidate.position"),
                _set(
                    "_cloud_relative_end",
                    [
                        f"_cloud_target_start[{i}] - (_cloud_destination[{i}] - "
                        f"_cloud_old_origin[{i}])"
                        for i in range(3)
                    ],
                ),
                _call(
                    "resolve_srd_environment_sphere_body_segment",
                    {
                        "moving_subject": "{_cloud_candidate.id}",
                        "area_id": "{area_id}",
                        "start": "{_cloud_target_start}",
                        "end": "{_cloud_relative_end}",
                    },
                    ("_body_hit", "_body_entry", "_body_exit"),
                ),
                _when(
                    "_body_hit == true and _body_exit > 0.000000000001",
                    [
                        _set(
                            "_cloud_contact_t",
                            "max(0, _body_entry) + min(0.0000001, "
                            "(_body_exit - max(0, _body_entry)) * 0.5)",
                        ),
                        _push(
                            "_cloud_contacts",
                            {
                                "target": "_cloud_candidate.metadata.participant_ref",
                                "t": "_cloud_contact_t",
                            },
                        ),
                    ],
                ),
            ],
        ),
    ]
    return {
        "qualify_srd_environment_sphere_motion_creature": _environment_tool(
            "Read the actual living creature before deriving a Sphere movement contact.",
            [_entity("target_state")],
            [
                _set(
                    "_sphere_motion_creature",
                    "target_state.is_alive == true and target_state.creature_type != 'object' "
                    "and target_state.creature_type != 'spell_force'",
                )
            ],
        ),
        "move_srd_cloudkill_source": _environment_tool(
            "Commit the printed ten-foot movement from the actual caster and saved Sphere.",
            [
                _entity("caster_state"),
                _param("area_id"),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            [
                _set("_cloud_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_cloud_source.spell == 'cloudkill' and _cloud_source.active == true and "
                    "_cloud_source.caster == _caster_state_id",
                    "Use this actual caster's active Cloudkill source.",
                ),
                _require(
                    "combat.turn_active == true and combat.active_actor == _caster_state_id "
                    "and caster_state.turns_started > _cloud_source.cloud_last_start_turn",
                    "The cloud moves once at its caster's actual later turn start.",
                ),
                _call("refresh_srd_environment_wind", {}),
                deepcopy(current),
                _when(
                    "_cloud_source_current == true",
                    [
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_caster_state_id}"},
                            POSITION_EXPORTS,
                        ),
                        _set("_cloud_caster_origin", "_info_position"),
                        _set("_cloud_caster_facing", "_info_scene_entity.facing"),
                        _set("_cloud_old_origin", "_cloud_source.origin"),
                        _set(
                            "_cloud_away",
                            [
                                f"_cloud_old_origin[{i}] - _cloud_caster_origin[{i}]"
                                for i in range(3)
                            ],
                        ),
                        _set(
                            "_cloud_away_length",
                            "(_cloud_away[0] ** 2 + _cloud_away[1] ** 2 + "
                            "_cloud_away[2] ** 2) ** 0.5",
                        ),
                        _when(
                            "_cloud_away_length < 0.000000001",
                            [
                                _set(
                                    "_cloud_away",
                                    ["_cloud_caster_facing[0]", "_cloud_caster_facing[1]", 0],
                                ),
                                _set(
                                    "_cloud_away_length",
                                    "(_cloud_away[0] ** 2 + _cloud_away[1] ** 2) ** 0.5",
                                ),
                            ],
                        ),
                        _require(
                            "_cloud_away_length > 0.000000001",
                            "The coincident cloud needs its caster's actual nonzero facing.",
                        ),
                        _set(
                            "_cloud_destination",
                            [
                                f"_cloud_old_origin[{i}] + _cloud_away[{i}] * 10 * "
                                "_cloud_away_length ** -1"
                                for i in range(3)
                            ],
                        ),
                        _require(
                            " and ".join(
                                f"_cloud_destination[{i}] >= "
                                f"state.spatial_scene.map.bounds.min[{i}] and "
                                f"_cloud_destination[{i}] <= "
                                f"state.spatial_scene.map.bounds.max[{i}]"
                                for i in range(3)
                            ),
                            "Admit a larger actual map before the cloud moves beyond its bounds.",
                        ),
                        _set("_cloud_contacts", []),
                        _set("_cloud_candidates", "state.spatial_scene.entities"),
                        _each(
                            "_cloud_candidates",
                            "_cloud_candidate",
                            [
                                _when(
                                    "_cloud_candidate.active == true and "
                                    "_cloud_candidate.metadata.participant_ref != null",
                                    candidate,
                                )
                            ],
                        ),
                        {"sort": {"collection": "_cloud_contacts", "by": "t", "direction": "asc"}},
                        _set(
                            "state.persistent_spell_areas.{area_id}.cloud_last_start_turn",
                            "caster_state.turns_started",
                        ),
                        _set(
                            "state.persistent_spell_areas.{area_id}.cloud_last_move",
                            {
                                "caster": "_caster_state_id",
                                "turn": "caster_state.turns_started",
                                "sequence": "combat.turn_sequence",
                                "from": "_cloud_old_origin",
                                "destination": "_cloud_destination",
                                "to": "_cloud_old_origin",
                            },
                        ),
                        _each(
                            "_cloud_contacts",
                            "_cloud_contact",
                            [
                                deepcopy(current),
                                _when(
                                    "_cloud_source_current == true",
                                    [
                                        _set(
                                            "state.persistent_spell_areas.{area_id}.origin",
                                            [
                                                f"_cloud_old_origin[{i}] + "
                                                f"(_cloud_destination[{i}] - "
                                                f"_cloud_old_origin[{i}]) * _cloud_contact.t"
                                                for i in range(3)
                                            ],
                                        ),
                                        _set(
                                            "state.persistent_spell_areas.{area_id}.cloud_last_move"
                                            ".to",
                                            "state.persistent_spell_areas[area_id].origin",
                                        ),
                                        _call("refresh_srd_environment_wind", {}),
                                        _call(
                                            "resolve_srd_environment_sphere_event",
                                            {
                                                "target_state": "{_cloud_contact.target}",
                                                "area_id": "{area_id}",
                                                "witness_state_ids": "{witness_state_ids}",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        deepcopy(current),
                        _when(
                            "_cloud_source_current == true",
                            [
                                _set(
                                    "state.persistent_spell_areas.{area_id}.origin",
                                    "_cloud_destination",
                                ),
                                _set(
                                    "state.persistent_spell_areas.{area_id}.cloud_last_move.to",
                                    "_cloud_destination",
                                ),
                                _call("refresh_srd_environment_wind", {}),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }


def _blade_barrier_tools():
    """Reuse saved Box bodies and the existing area save packet for straight Blade Barrier."""

    def bind(value):
        if isinstance(value, str):
            return (
                value.replace("black_tentacles", "blade_barrier")
                .replace("_tentacle_body_", "_blade_body_")
                .replace("'tentacles'", "'blade'")
                .replace("Black Tentacles", "Blade Barrier")
                .replace("ground square", "vertical wall")
            )
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {bind(key): bind(item) for key, item in value.items()}
        return value

    tools = bind(_black_tentacles_geometry_tools())
    body = tools["resolve_srd_blade_barrier_body_segment"]
    body["description"] = "Resolve actual box creature space against the saved vertical blade wall."
    for effects in list(_effect_lists(body["effects"])):
        for effect in list(effects):
            if "_info_scene_entity.metadata.flying" in effect.get("condition", ""):
                effect["condition"] = (
                    "_info_scene_entity.metadata.plane == _blade_body_source.plane"
                )
            if effect.get("set", {}).get("field") == "_blade_body_volume":
                effect["set"]["value"]["min"][2] = (
                    "_blade_body_source.volume.min[2] - _info_scene_entity.height"
                )
            if effect.get("condition") == "_body_hit == true":
                effects.remove(effect)
    hazard = _sphere_hazard_tools()
    for old, new in (
        ("qualify_srd_environment_sphere_effect", "qualify_srd_blade_barrier_effect"),
        ("resolve_srd_environment_sphere_event", "resolve_srd_blade_barrier_event"),
        ("resolve_srd_environment_sphere_scene", "resolve_srd_blade_barrier_scene"),
    ):
        tool = deepcopy(hazard[old])
        tool["description"] = (
            "Resolve the actual Blade Barrier source through the saved area owner."
        )
        for effects in list(_effect_lists(tool["effects"])):
            for effect in list(effects):
                if effect.get("call") == "qualify_srd_environment_sphere_target":
                    effect["call"] = "qualify_srd_blade_barrier_target"
                elif effect.get("call") == "qualify_srd_environment_sphere_effect":
                    effect["call"] = "qualify_srd_blade_barrier_effect"
                elif effect.get("call") == "resolve_srd_environment_sphere_event":
                    effect["call"] = "resolve_srd_blade_barrier_event"
                if (
                    effect.get("set", {}).get("field")
                    == "target_state.last_environment_sphere_save"
                ):
                    effect["set"]["field"] = "target_state.last_blade_barrier_save"
                requirement = effect.get("require", {})
                if "_sphere_scene_source.profile" in requirement.get("condition", ""):
                    requirement["condition"] = requirement["condition"].replace(
                        _sphere_damage_mode("_sphere_scene_source"),
                        "_sphere_scene_source.mode == 'blade'",
                    )
        tools[new] = tool
    tools["resolve_srd_blade_barrier_intervals"] = _environment_tool(
        "Contribute actual blade wall space to the existing Difficult Terrain interval union.",
        [_param("actor_subject"), _array("start"), _array("end")],
        [
            _set("_blade_route_intervals", []),
            _call("srd_information_scene_entity", {"subject": "{actor_subject}"}, POSITION_EXPORTS),
            _set("_blade_route_plane", "_info_scene_entity.metadata.plane"),
            *_areas(
                [
                    _when(
                        "_environment_area.mode == 'blade' and _environment_area.plane == "
                        "_blade_route_plane",
                        [
                            _call(
                                "qualify_srd_blade_barrier_source",
                                {
                                    "caster_state": "{_environment_area.caster}",
                                    "area_id": "{_environment_id}",
                                },
                                ("_sphere_source_current",),
                            ),
                            _when(
                                "_sphere_source_current == true",
                                [
                                    _call(
                                        "srd_information_segment_interval",
                                        {
                                            "start": "{start}",
                                            "end": "{end}",
                                            "volume": "{_environment_area.volume}",
                                        },
                                        ("_segment_hit", "_segment_entry", "_segment_exit"),
                                    ),
                                    _when(
                                        "_segment_hit == true",
                                        [
                                            _push(
                                                "_blade_route_intervals",
                                                {
                                                    "entry": "_segment_entry",
                                                    "exit": "_segment_exit",
                                                    "source_spell": "_environment_area.spell",
                                                    "source_environment_id": "_environment_id",
                                                },
                                            )
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )
    tools.update(_blade_body_cover_tools())
    return tools


def _bind_blade_barrier(changed):
    """Bind actual straight source geometry, appearance/entry/end damage and terrain."""
    cast = changed["cast_spell_environment"]
    cast["parameters"] += [
        _optional("wall_length", "number", 100, minimum=0.01, maximum=100),
        _optional("wall_axis", "string", "east_west", enum_values=["east_west", "north_south"]),
    ]
    before_concentration = next(
        i
        for i, e in enumerate(cast["effects"])
        if "_environment_profile.concentration == true" in e.get("condition", "")
    )
    cast["effects"][before_concentration:before_concentration] = [
        _when(
            "_environment_profile.environment_mode == 'blade'",
            [
                _require(
                    "save_dc >= 1 and wall_length >= 0.01 and wall_length <= 100",
                    "Use the actual caster DC and a printed straight-wall length up to100feet.",
                ),
                _set(
                    "_blade_cast_volume",
                    {
                        "kind": "'box'",
                        "min": [
                            "_environment_cast_origin[0] - wall_length * 0.5",
                            "_environment_cast_origin[1] - 2.5",
                            "_environment_cast_origin[2]",
                        ],
                        "max": [
                            "_environment_cast_origin[0] + wall_length * 0.5",
                            "_environment_cast_origin[1] + 2.5",
                            "_environment_cast_origin[2] + 20",
                        ],
                    },
                ),
                _when(
                    "wall_axis == 'north_south'",
                    [
                        _set(
                            "_blade_cast_volume",
                            {
                                "kind": "'box'",
                                "min": [
                                    "_environment_cast_origin[0] - 2.5",
                                    "_environment_cast_origin[1] - wall_length * 0.5",
                                    "_environment_cast_origin[2]",
                                ],
                                "max": [
                                    "_environment_cast_origin[0] + 2.5",
                                    "_environment_cast_origin[1] + wall_length * 0.5",
                                    "_environment_cast_origin[2] + 20",
                                ],
                            },
                        ),
                    ],
                ),
                _require(
                    " and ".join(
                        f"_blade_cast_volume.min[{i}] >= "
                        f"state.spatial_scene.map.bounds.min[{i}] and "
                        f"_blade_cast_volume.max[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                        for i in range(3)
                    ),
                    "The complete actual wall must fit the admitted map bounds.",
                ),
            ],
        ),
    ]
    source_install = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("set", {}).get("field", "").endswith(".has_spell_environments")
    )
    cast["effects"][source_install + 1 : source_install + 1] = [
        _when(
            "_environment_profile.environment_mode == 'blade'",
            [
                *[
                    _set("state.persistent_spell_areas.{_environment_cast_id}." + key, value)
                    for key, value in (
                        ("volume", "_blade_cast_volume"),
                        ("wall_axis", "wall_axis"),
                        ("wall_length", "wall_length"),
                        ("event_turn", -1),
                        ("event_clock", "'none'"),
                        ("event_target_ids", []),
                    )
                ],
            ],
        ),
    ]
    refresh = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "refresh_srd_environment_scene"
    )
    cast["effects"][refresh:refresh] = [
        _when(
            "_environment_profile.environment_mode == 'blade'",
            [
                _call("resolve_srd_blade_barrier_scene", {"area_id": "{_environment_cast_id}"}),
            ],
        )
    ]
    index_tool = changed["refresh_srd_environment_mode_indices"]
    index_tool["effects"].insert(0, _set("state.has_blade_barrier", False))
    loop = next(e["for_each"] for e in index_tool["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when("_environment_area.mode == 'blade'", [_set("state.has_blade_barrier", True)])
    )
    terrain = changed["resolve_srd_environment_sphere_terrain_intervals"]
    terrain["effects"].append(
        _when(
            "ignore_ground_environments != true and state.has_blade_barrier == true",
            [
                _call(
                    "resolve_srd_blade_barrier_intervals",
                    {
                        "actor_subject": "{actor_subject}",
                        "start": "{start}",
                        "end": "{end}",
                    },
                    ("_blade_route_intervals",),
                ),
                _each(
                    "_blade_route_intervals",
                    "_blade_route_piece",
                    [
                        _push("_sphere_route_intervals", "_blade_route_piece"),
                    ],
                ),
            ],
        )
    )
    entry = changed["resolve_srd_environment_hazard_entry"]
    loop = next(e["for_each"] for e in entry["effects"] if "for_each" in e)
    body = loop["do"][-1]["on_true"]
    sphere = next(e for e in body if e.get("condition") == _area_entry_mode("_environment_area"))
    candidate = deepcopy(sphere["on_true"])
    for effects in _effect_lists(candidate):
        for e in effects:
            if e.get("call") == "resolve_srd_environment_sphere_body_segment":
                e["call"] = "resolve_srd_blade_barrier_body_segment"
    body.append(_when("_environment_area.mode == 'blade'", candidate))
    dispatch = changed["resolve_srd_environment_hazard_event"]
    dispatch["effects"][-1:] = [
        _when(
            "_hazard_dispatch_source.mode == 'blade'",
            [
                _call(
                    "resolve_srd_blade_barrier_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{area_id}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                ),
            ],
            dispatch["effects"][-1:],
        )
    ]
    ending = changed["resolve_srd_environment_hazard_end_turn"]
    loop = next(e["for_each"] for e in ending["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'blade' and combat.turn_active == true and "
            "combat.active_actor == _target_state_id",
            [
                _call(
                    "resolve_srd_blade_barrier_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{_environment_id}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                ),
            ],
        )
    )
    for effects in _effect_lists(changed["dispel_srd_environment_source"]["effects"]):
        for effect in effects:
            requirement = effect.get("require", {})
            if "_environment_dispel_zone_found == true" in requirement.get("condition", ""):
                requirement["condition"] += " or _environment_dispel_source.mode == 'blade'"


def _blade_body_cover_tools():
    """Read a source-qualified blade wall through the neutral target's actual body samples."""
    sample_ray = [
        _call(
            "srd_information_segment_interval",
            {
                "start": "{origin}",
                "end": "{_blade_body_sample.point}",
                "volume": "{_environment_area.volume}",
            },
            ("_segment_hit", "_segment_entry", "_segment_exit"),
        ),
        _when(
            "_segment_hit == true and _segment_exit > _segment_entry and "
            "_segment_entry > 0 and _segment_exit < 1",
            [
                _set(
                    "_bcp",
                    [
                        f"origin[{i}] + (_blade_body_sample.point[{i}] - origin[{i}]) * "
                        "(_segment_entry + _segment_exit) * 0.5"
                        for i in range(3)
                    ],
                ),
                _when(
                    " and ".join(
                        f"_bcp[{i}] > _environment_area.volume.min[{i}] and "
                        f"_bcp[{i}] < _environment_area.volume.max[{i}]"
                        for i in range(3)
                    ),
                    [_set("_blade_body_crossed", "_blade_body_crossed + 1")],
                ),
            ],
        ),
    ]
    point = _environment_tool(
        "Qualify printed Blade Cover from a real source origin and actual neutral body samples.",
        [_array("origin"), _param("plane"), _param("target_subject")],
        [
            _set("_environment_blade_cover", "'none'"),
            _set("_environment_blade_cover_supported", True),
            _set("_blade_body_cover_partial", False),
            _when(
                "state.has_blade_barrier == true",
                [
                    *_snapshot(),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{target_subject}"},
                        POSITION_EXPORTS,
                    ),
                    _require(
                        "_info_scene_entity.footprint.kind == 'box'",
                        "Blade body Cover requires an actual admitted Box target.",
                    ),
                    _set("_blade_cover_scene_id", "_info_scene_entity.id"),
                    _set("_blade_cover_target_plane", "_info_scene_entity.metadata.plane"),
                    _when(
                        "_blade_cover_target_plane == plane",
                        [
                            _spatial(
                                "exposure",
                                {
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "observer": {"point": "{origin}"},
                                    "target": {"entity_id": "{_blade_cover_scene_id}"},
                                    "samples": {"horizontal": 3, "vertical": 3},
                                    "include": ["geometry"],
                                },
                                "_blade_body_exposure",
                            ),
                            _set("_blade_body_cover_samples", "_blade_body_exposure.samples"),
                            _require(
                                "len(_blade_body_cover_samples) > 0",
                                "Blade Cover needs the neutral target body's actual samples.",
                            ),
                            *_areas(
                                [
                                    _when(
                                        "_environment_area.mode == 'blade' and "
                                        "_environment_area.plane == plane",
                                        [
                                            _call(
                                                "qualify_srd_blade_barrier_source",
                                                {
                                                    "caster_state": "{_environment_area.caster}",
                                                    "area_id": "{_environment_id}",
                                                },
                                                ("_sphere_source_current",),
                                            ),
                                            _when(
                                                "_sphere_source_current == true",
                                                [
                                                    _set("_blade_body_crossed", 0),
                                                    _each(
                                                        "_blade_body_cover_samples",
                                                        "_blade_body_sample",
                                                        sample_ray,
                                                    ),
                                                    _when(
                                                        "_blade_body_crossed == "
                                                        "len(_blade_body_cover_samples)",
                                                        [
                                                            _set(
                                                                "_environment_blade_cover",
                                                                "'three_quarters'",
                                                            )
                                                        ],
                                                        [
                                                            _when(
                                                                "_blade_body_crossed > 0",
                                                                [
                                                                    _set(
                                                                        "_blade_body_cover_partial",
                                                                        True,
                                                                    )
                                                                ],
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ]
                            ),
                        ],
                    ),
                ],
            ),
            _set(
                "_environment_blade_cover_supported",
                "_environment_blade_cover == 'three_quarters' or _blade_body_cover_partial != true",
            ),
        ],
    )
    observer = _environment_tool(
        "Bind the actual admitted sight origin to the existing source-owned Blade body query.",
        [_param("observer_subject"), _param("target_subject")],
        [
            _set("_environment_blade_cover", "'none'"),
            _set("_environment_blade_cover_supported", True),
            _when(
                "state.has_blade_barrier == true",
                [
                    _call(
                        "resolve_srd_scene_sight_origin",
                        {"observer_state": "{observer_subject}"},
                        ("_sight_origin", "_sight_origin_plane", "_sight_origin_recorded"),
                    ),
                    _call(
                        "resolve_srd_environment_blade_body_cover",
                        {
                            "origin": "{_sight_origin}",
                            "plane": "{_sight_origin_plane}",
                            "target_subject": "{target_subject}",
                        },
                        ("_environment_blade_cover", "_environment_blade_cover_supported"),
                    ),
                ],
            ),
        ],
    )
    return {
        "resolve_srd_environment_blade_body_cover": point,
        "resolve_srd_environment_blade_cover": observer,
    }


def _wall_of_fire_tools():
    """Reuse saved wall bodies; appearance saves and entry/end packets have separate receipts."""

    def bind(value):
        if isinstance(value, str):
            return (
                value.replace("blade_barrier", "wall_of_fire")
                .replace("Blade Barrier", "Wall of Fire")
                .replace("_blade_", "_fire_wall_")
                .replace("'blade'", "'wall_fire'")
            )
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        return value

    original = _blade_barrier_tools()
    tools = {
        name.replace("blade_barrier", "wall_of_fire"): bind(deepcopy(original[name]))
        for name in (
            "qualify_srd_blade_barrier_source",
            "qualify_srd_blade_barrier_target",
            "resolve_srd_blade_barrier_body_segment",
            "resolve_srd_blade_barrier_event",
            "resolve_srd_blade_barrier_scene",
            "qualify_srd_blade_barrier_effect",
        )
    }
    side_axes = []
    for normal, along in ((1, 0), (0, 1)):
        face = [None, None, "_fire_face_z"]
        face[normal], face[along] = "_fire_face_normal", "_fire_face_along"
        body = [None, None, "max(_fire_body_min[2], min(_fire_face_z, _fire_body_max[2]))"]
        body[normal] = (
            f"max(_fire_body_min[{normal}], min(_fire_face_normal, _fire_body_max[{normal}]))"
        )
        body[along] = (
            f"max(_fire_body_min[{along}], min(_fire_face_along, _fire_body_max[{along}]))"
        )
        side_axes.append(
            [
                _set(
                    "_fire_face_along",
                    f"max(_fire_source.volume.min[{along}], min(_fire_body_min[{along}], "
                    f"_fire_source.volume.max[{along}]))",
                ),
                _set("_fire_face_normal", f"_fire_source.volume.max[{normal}]"),
                _set(
                    "_fire_selected_side",
                    f"_fire_body_max[{normal}] >= _fire_source.volume.max[{normal}]",
                ),
                _when(
                    "_fire_source.wall_hot_side == 'negative'",
                    [
                        _set("_fire_face_normal", f"_fire_source.volume.min[{normal}]"),
                        _set(
                            "_fire_selected_side",
                            f"_fire_body_min[{normal}] <= _fire_source.volume.min[{normal}]",
                        ),
                    ],
                ),
                _set("_fire_face_point", face),
                _set("_fire_body_point", body),
            ]
        )
    tools["qualify_srd_wall_of_fire_hot_side"] = _environment_tool(
        "Bind actual creature bounds to the chosen wall face and its printed ten-foot distance.",
        [_entity("target_state"), _param("area_id")],
        [
            _set("_fire_hot_side_inside", False),
            _set("_fire_source", "state.persistent_spell_areas[area_id]"),
            _call(
                "qualify_srd_wall_of_fire_source",
                {"caster_state": "{_fire_source.caster}", "area_id": "{area_id}"},
                ("_sphere_source_current",),
            ),
            _when(
                "_sphere_source_current == true and target_state.is_alive == true",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _require(
                        "_info_scene_entity.footprint.kind == 'box'",
                        "Wall-side distance requires this creature's actual admitted Box body.",
                    ),
                    _when(
                        "_info_scene_entity.metadata.plane == _fire_source.plane",
                        [
                            _set(
                                "_fire_body_min",
                                [
                                    "_info_position[0] - "
                                    "_info_scene_entity.footprint.half_extents[0]",
                                    "_info_position[1] - "
                                    "_info_scene_entity.footprint.half_extents[1]",
                                    "_info_position[2]",
                                ],
                            ),
                            _set(
                                "_fire_body_max",
                                [
                                    "_info_position[0] + "
                                    "_info_scene_entity.footprint.half_extents[0]",
                                    "_info_position[1] + "
                                    "_info_scene_entity.footprint.half_extents[1]",
                                    "_info_position[2] + _info_scene_entity.height",
                                ],
                            ),
                            _set(
                                "_fire_face_z",
                                "max(_fire_source.volume.min[2], min(_fire_body_min[2], "
                                "_fire_source.volume.max[2]))",
                            ),
                            _when(
                                "_fire_source.wall_axis == 'north_south'",
                                side_axes[1],
                                side_axes[0],
                            ),
                            _when(
                                "_fire_selected_side == true",
                                [
                                    _call(
                                        "measure_srd_information_distance",
                                        {
                                            "start": "{_fire_face_point}",
                                            "end": "{_fire_body_point}",
                                        },
                                        ("_scene_distance",),
                                    ),
                                    _set("_fire_hot_side_inside", "_scene_distance <= 10"),
                                    _when(
                                        "_fire_hot_side_inside == true",
                                        [
                                            *_snapshot(),
                                            _spatial(
                                                "exposure",
                                                {
                                                    "map_id": "{state.spatial_scene.map.id}",
                                                    "observer": {"point": "{_fire_face_point}"},
                                                    "target": {
                                                        "entity_id": "{_info_scene_entity.id}"
                                                    },
                                                    "samples": {"horizontal": 3, "vertical": 3},
                                                    "include": ["geometry"],
                                                },
                                                "_fire_side_exposure",
                                            ),
                                            _set(
                                                "_fire_hot_side_inside",
                                                "_fire_side_exposure.visible_samples > 0",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    timing = _optional("timing", "string", "entry", enum_values=["initial", "entry", "end"])
    strongest = tools["qualify_srd_wall_of_fire_effect"]
    strongest["parameters"].append(deepcopy(timing))
    for effects in _effect_lists(strongest["effects"]):
        for effect in effects:
            if effect.get("call") == "qualify_srd_wall_of_fire_target":
                effect["params"]["timing"] = "{timing}"
    target = tools["qualify_srd_wall_of_fire_target"]
    target["parameters"].append(deepcopy(timing))
    target["effects"].append(
        _when(
            "timing == 'end' and _sphere_target_inside != true and "
            "_sphere_source_current == true and target_state.is_alive == true and "
            "_info_scene_entity.metadata.plane == _sphere_target_source.plane",
            [
                _call(
                    "qualify_srd_wall_of_fire_hot_side",
                    {"target_state": "{_target_state_id}", "area_id": "{_environment_area_key}"},
                    ("_fire_hot_side_inside",),
                ),
                _set("_sphere_target_inside", "_fire_hot_side_inside"),
            ],
        )
    )
    event = tools["resolve_srd_wall_of_fire_event"]
    event["description"] = (
        "Save only on actual appearance; settle first wall entry and target-turn-end Fire packets."
    )
    event["parameters"].append(deepcopy(timing))
    for effects in list(_effect_lists(event["effects"])):
        roll_start = next(
            (i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_sphere_dice"),
            None,
        )
        if roll_start is not None:
            roll_end = next(
                i
                for i, e in enumerate(effects)
                if e.get("set", {}).get("field") == "_sphere_roll_divisor"
            )
            ordinary_roll = effects[roll_start:roll_end]
            effects[roll_start:roll_end] = [
                _when(
                    "timing == 'initial'",
                    [
                        _set("_sphere_damage_roll", "_sphere_event_source.initial_damage_roll"),
                        _set("_sphere_amount", "_sphere_event_source.initial_damage_amount"),
                        _set(
                            "_sphere_unallocated_penalty",
                            "_sphere_event_source.initial_unallocated_penalty",
                        ),
                    ],
                    ordinary_roll,
                )
            ]
    # Each printed trigger keeps its own receipt. Appearance must not suppress the
    # target's first later entry or end-turn damage in the same genuine turn.
    original_prior = next(
        e
        for e in event["effects"]
        if e.get("condition", "").startswith("_sphere_event_source.event_clock ==")
    )
    index = event["effects"].index(original_prior)
    event["effects"][index : index + 1] = [
        _when(
            "timing == 'initial'",
            [
                _require(
                    "_sphere_event_source.appearance_in_progress == true and "
                    "_sphere_event_source.appearance_sequence == _sphere_event_source.sequence",
                    "Initial Fire saves require this exact source's actual appearance.",
                ),
                original_prior,
            ],
        ),
        *[
            _when(
                f"timing == '{kind}'",
                [
                    *(
                        [
                            _require(
                                "combat.turn_active == true and "
                                "combat.active_actor == _target_state_id",
                                "Fire end packets require this creature's actual ending turn.",
                            )
                        ]
                        if kind == "end"
                        else []
                    ),
                    _when(
                        f"_sphere_event_source.{kind}_clock == _sphere_clock and "
                        f"_sphere_event_source.{kind}_turn == _sphere_turn",
                        [_set("_sphere_prior_targets", f"_sphere_event_source.{kind}_targets")],
                    ),
                ],
            )
            for kind in ("entry", "end")
        ],
    ]
    for effects in list(_effect_lists(event["effects"])):
        first = next(
            (
                i
                for i, e in enumerate(effects)
                if e.get("require", {})
                .get("condition", "")
                .startswith("target_state.saving_throw_modifiers[")
            ),
            None,
        )
        if first is not None:
            save = effects[first : first + 3]
            assert save[-1].get("call") == "d20_test"
            effects[first : first + 3] = [
                _set("_d20_test", None),
                _set("_outcome", "'no_save'"),
                _when("timing == 'initial'", save),
            ]
        receipt = next(
            (
                i
                for i, e in enumerate(effects)
                if e.get("set", {}).get("field", "").endswith(".event_clock")
            ),
            None,
        )
        if receipt is not None:
            initial_receipt = effects[receipt : receipt + 3]
            effects[receipt : receipt + 3] = [
                _when("timing == 'initial'", initial_receipt),
                *[
                    _when(
                        f"timing == '{kind}'",
                        [
                            _set(
                                f"state.persistent_spell_areas.{{_environment_area_key}}.{kind}_clock",
                                "_sphere_clock",
                            ),
                            _set(
                                f"state.persistent_spell_areas.{{_environment_area_key}}.{kind}_turn",
                                "_sphere_turn",
                            ),
                            _set(
                                f"state.persistent_spell_areas.{{_environment_area_key}}.{kind}_targets",
                                "_sphere_prior_targets",
                            ),
                        ],
                    )
                    for kind in ("entry", "end")
                ],
            ]
        for effect in list(effects):
            if effect.get("call") in (
                "qualify_srd_wall_of_fire_target",
                "qualify_srd_wall_of_fire_effect",
            ):
                effect["params"]["timing"] = "{timing}"
            if effect.get("condition", "").startswith(
                "_sphere_event_source.appearance_in_progress == true"
            ):
                effect["condition"] = "timing == 'initial' and " + effect["condition"]
            if effect.get("condition") == "_outcome == 'success'":
                effect["condition"] = "timing == 'initial' and _outcome == 'success'"
            if effect.get("set", {}).get("field") == "target_state.last_wall_of_fire_save":
                effect["set"]["value"]["timing"] = "timing"
                effect["set"]["value"]["roll"] = None
                effects.insert(
                    effects.index(effect) + 1,
                    _when(
                        "timing == 'initial'",
                        [_set("target_state.last_wall_of_fire_save.roll", "_d20_test")],
                    ),
                )
    scene = tools["resolve_srd_wall_of_fire_scene"]
    for effects in _effect_lists(scene["effects"]):
        for effect in effects:
            if effect.get("call") == "resolve_srd_wall_of_fire_event":
                effect["params"]["timing"] = "initial"
    first_target = next(
        i
        for i, e in enumerate(scene["effects"])
        if e.get("for_each", {}).get("collection") == "_sphere_initial_targets"
    )
    scene["effects"][first_target:first_target] = [
        _when(
            "len(_sphere_initial_targets) > 0",
            [
                {
                    "table_lookup": {
                        "table": "definitions.spells",
                        "key": "_sphere_scene_source.spell",
                        "required": True,
                    },
                    "store_as": "_fire_initial_profile",
                },
                _set(
                    "_fire_initial_dice",
                    "_fire_initial_profile.damage_dice + max(0, "
                    "_sphere_scene_source.spell_level - _fire_initial_profile.base_level) * "
                    "_fire_initial_profile.damage_per_slot_level",
                ),
                _call(
                    "format_srd_information_projection",
                    {"message": "{_fire_initial_dice}{_fire_initial_profile.damage_die}"},
                    {"_fire_initial_expression": "_info_projection_message"},
                ),
                {
                    "dice": {"expression": "{_fire_initial_expression}"},
                    "store_as": "_fire_initial_damage_roll",
                },
                _call(
                    "resolve_srd_actor_spell_modifiers",
                    {"target_state": "{_sphere_scene_source.caster}", "lasting_state": "state"},
                ),
                _call(
                    "modify_srd_actor_raw_damage_roll",
                    {
                        "actor_state": "{_sphere_scene_source.caster}",
                        "lasting_state": "state",
                        "amount": "{_fire_initial_damage_roll.total}",
                    },
                    {"_fire_initial_amount": "_ray_raw_damage"},
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_area_key}.initial_damage_roll",
                    "_fire_initial_damage_roll",
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_area_key}.initial_damage_amount",
                    "max(0, _fire_initial_amount)",
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_area_key}.initial_unallocated_penalty",
                    "max(0, -_fire_initial_amount)",
                ),
            ],
        )
    ]
    return tools


def _wall_of_fire_opacity_tool():
    """Read visual opacity only where an actual live wall intersects the ray interior."""
    return _environment_tool(
        "Resolve current Wall of Fire opacity from its actual saved Box and plane.",
        [_array("start"), _array("end"), _param("plane")],
        [
            _set("_environment_fire_opaque", False),
            _when(
                "state.has_wall_of_fire == true",
                _areas(
                    [
                        _when(
                            "_environment_area.mode == 'wall_fire' and "
                            "_environment_area.plane == plane",
                            [
                                _call(
                                    "qualify_srd_wall_of_fire_source",
                                    {
                                        "caster_state": "{_environment_area.caster}",
                                        "area_id": "{_environment_id}",
                                    },
                                    ("_sphere_source_current",),
                                ),
                                _when(
                                    "_sphere_source_current == true",
                                    [
                                        _call(
                                            "srd_information_segment_interval",
                                            {
                                                "start": "{start}",
                                                "end": "{end}",
                                                "volume": "{_environment_area.volume}",
                                            },
                                            ("_segment_hit", "_segment_entry", "_segment_exit"),
                                        ),
                                        _when(
                                            "_segment_hit == true",
                                            [
                                                _set(
                                                    "_fire_opaque_midpoint",
                                                    [
                                                        f"start[{i}] + (end[{i}] - start[{i}]) * "
                                                        "(_segment_entry + _segment_exit) * 0.5"
                                                        for i in range(3)
                                                    ],
                                                ),
                                                _when(
                                                    " and ".join(
                                                        f"_fire_opaque_midpoint[{i}] > "
                                                        f"_environment_area.volume.min[{i}] and "
                                                        f"_fire_opaque_midpoint[{i}] < "
                                                        f"_environment_area.volume.max[{i}]"
                                                        for i in range(3)
                                                    ),
                                                    [_set("_environment_fire_opaque", True)],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ]
                ),
            ),
        ],
    )


def _bind_wall_of_fire(changed):
    """Install supported straight walls and bind actual entry/end damage and visual opacity."""
    cast = changed["cast_spell_environment"]
    cast["parameters"] += [
        _optional("fire_wall_length", "number", 60, minimum=0.01, maximum=60),
        _optional("fire_wall_side", "string", "positive", enum_values=["positive", "negative"]),
    ]
    before = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("condition", "").startswith("_environment_profile.concentration == true and")
    )
    geometry = []
    for axis, normal, along in (("east_west", 1, 0), ("north_south", 0, 1)):
        minimum = [
            "_environment_cast_origin[0]",
            "_environment_cast_origin[1]",
            "_environment_cast_origin[2]",
        ]
        maximum = [
            "_environment_cast_origin[0]",
            "_environment_cast_origin[1]",
            "_environment_cast_origin[2] + 20",
        ]
        minimum[normal] += " - 0.5"
        maximum[normal] += " + 0.5"
        minimum[along] += " - fire_wall_length * 0.5"
        maximum[along] += " + fire_wall_length * 0.5"
        geometry.append(
            _when(
                f"wall_axis == '{axis}'",
                [
                    _set("_fire_cast_volume", {"kind": "'box'", "min": minimum, "max": maximum}),
                ],
            )
        )
    cast["effects"][before:before] = [
        _when(
            "_environment_profile.environment_mode == 'wall_fire'",
            [
                _require("save_dc >= 1", "Use the actual caster spell save DC for Wall of Fire."),
                _call(
                    "qualify_srd_environment_ground",
                    {
                        "point": "{_environment_cast_origin}",
                        "plane": "{_environment_plane}",
                    },
                    ("_environment_ground_id",),
                ),
                _set("_fire_support_geometry", "state.spatial_scene.geometry"),
                _set("_fire_support_volume", {}),
                _each(
                    "_fire_support_geometry",
                    "_fire_support",
                    [
                        _when(
                            "_fire_support.id == _environment_ground_id",
                            [
                                _set("_fire_support_volume", "_fire_support.volume"),
                            ],
                        ),
                    ],
                ),
                _require(
                    "_fire_support_volume.kind == 'box'",
                    "This straight wall needs actual flat Box support.",
                ),
                *geometry,
                _require(
                    " and ".join(
                        f"_fire_cast_volume.min[{i}] >= _fire_support_volume.min[{i}] and "
                        f"_fire_cast_volume.max[{i}] <= _fire_support_volume.max[{i}]"
                        for i in range(2)
                    )
                    + " and abs(_environment_cast_origin[2] - "
                    "_fire_support_volume.max[2]) < 0.000000001",
                    "The whole wall base must lie on the actual solid supporting surface.",
                ),
                _require(
                    " and ".join(
                        f"_fire_cast_volume.min[{i}] >= "
                        f"state.spatial_scene.map.bounds.min[{i}] and "
                        f"_fire_cast_volume.max[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                        for i in range(3)
                    ),
                    "The whole Wall of Fire must fit the admitted map bounds.",
                ),
            ],
        )
    ]
    install = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("set", {}).get("field", "").endswith(".has_spell_environments")
    )
    fields = [
        ("volume", "_fire_cast_volume"),
        ("ground_id", "_environment_ground_id"),
        ("wall_axis", "wall_axis"),
        ("wall_length", "fire_wall_length"),
        ("wall_hot_side", "fire_wall_side"),
        ("event_turn", -1),
        ("event_clock", "'none'"),
        ("event_target_ids", []),
    ]
    for kind in ("entry", "end"):
        fields += [(kind + "_turn", -1), (kind + "_clock", "'none'"), (kind + "_targets", [])]
    cast["effects"][install + 1 : install + 1] = [
        _when(
            "_environment_profile.environment_mode == 'wall_fire'",
            [
                _set("state.persistent_spell_areas.{_environment_cast_id}." + key, value)
                for key, value in fields
            ],
        )
    ]
    refresh = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "refresh_srd_environment_scene"
    )
    cast["effects"][refresh:refresh] = [
        _when(
            "_environment_profile.environment_mode == 'wall_fire'",
            [
                _call("resolve_srd_wall_of_fire_scene", {"area_id": "{_environment_cast_id}"}),
            ],
        )
    ]
    index = changed["refresh_srd_environment_mode_indices"]
    index["effects"].insert(0, _set("state.has_wall_of_fire", False))
    loop = next(e["for_each"] for e in index["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'wall_fire'",
            [_set("state.has_wall_of_fire", True)],
        )
    )
    entry = changed["resolve_srd_environment_hazard_entry"]
    loop = next(e["for_each"] for e in entry["effects"] if "for_each" in e)
    body = loop["do"][-1]["on_true"]
    candidate = deepcopy(
        next(
            e["on_true"] for e in body if e.get("condition") == "_environment_area.mode == 'blade'"
        )
    )
    for effects in _effect_lists(candidate):
        for effect in effects:
            if effect.get("call") == "resolve_srd_blade_barrier_body_segment":
                effect["call"] = "resolve_srd_wall_of_fire_body_segment"
            for key in ("condition",):
                if key in effect:
                    effect[key] = (
                        effect[key]
                        .replace("_environment_area.event_clock", "_environment_area.entry_clock")
                        .replace("_environment_area.event_turn", "_environment_area.entry_turn")
                    )
            value = effect.get("set", {}).get("value")
            if value == "_environment_area.event_target_ids":
                effect["set"]["value"] = "_environment_area.entry_targets"
    body.append(_when("_environment_area.mode == 'wall_fire'", candidate))
    dispatch = changed["resolve_srd_environment_hazard_event"]
    dispatch["effects"][-1:] = [
        _when(
            "_hazard_dispatch_source.mode == 'wall_fire'",
            [
                _call(
                    "resolve_srd_wall_of_fire_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{area_id}",
                        "witness_state_ids": "{witness_state_ids}",
                        "timing": "entry",
                    },
                ),
            ],
            dispatch["effects"][-1:],
        )
    ]
    ending = changed["resolve_srd_environment_hazard_end_turn"]
    loop = next(e["for_each"] for e in ending["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'wall_fire' and combat.turn_active == true and "
            "combat.active_actor == _target_state_id",
            [
                _call(
                    "resolve_srd_wall_of_fire_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{_environment_id}",
                        "witness_state_ids": "{witness_state_ids}",
                        "timing": "end",
                    },
                ),
            ],
        )
    )
    for name in ("resolve_srd_environment_point", "resolve_srd_environment_segment"):
        params = {"start": "{start}", "end": "{end}", "plane": "{plane}"}
        if name.endswith("point"):
            params.update(start="{point}", end="{point}")
        changed[name]["effects"].append(
            _when(
                "state.has_wall_of_fire == true",
                [
                    _call(
                        "resolve_srd_wall_of_fire_opacity", params, ("_environment_fire_opaque",)
                    ),
                    _when(
                        "_environment_fire_opaque == true",
                        [
                            _set(
                                "_environment_fog"
                                if name.endswith("point")
                                else "_environment_sight_obscured",
                                True,
                            )
                        ],
                    ),
                ],
            )
        )
    for effects in _effect_lists(changed["dispel_srd_environment_source"]["effects"]):
        for effect in effects:
            requirement = effect.get("require", {})
            if "_environment_dispel_zone_found == true" in requirement.get("condition", ""):
                requirement["condition"] += " or _environment_dispel_source.mode == 'wall_fire'"


def _web_tools():
    """Reuse actual Box bodies and exact restraints for supported floor-layered Web."""

    def bind(value):
        if isinstance(value, str):
            return (
                value.replace("black_tentacles", "web")
                .replace("Black Tentacles", "Web")
                .replace("_tentacle_", "_web_")
                .replace("'tentacles'", "'web'")
            )
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {bind(key): bind(item) for key, item in value.items()}
        return value

    originals = _black_tentacles_tools()
    event = bind(deepcopy(originals["resolve_srd_black_tentacles_event"]))
    event["description"] = (
        "Save on actual first entry and turn start; failure restrains without damage."
    )
    event["parameters"].append(
        _optional("timing", "string", "entry", enum_values=["entry", "start"])
    )
    failure = next(
        e
        for effects in _effect_lists(event["effects"])
        for e in effects
        if e.get("condition") == "_outcome == 'failure'"
    )
    restraint = next(
        e for e in failure["on_true"] if "_web_restrained == true" in e.get("condition", "")
    )
    failure["on_true"] = [restraint]
    for effects in list(_effect_lists(event["effects"])):
        for effect in list(effects):
            field = effect.get("set", {}).get("field", "")
            if (
                field in ("_web_damage_roll", "_web_effective_damage")
                or effect.get("condition") == "_web_damage_roll != null"
            ):
                effects.remove(effect)
                continue
            if "target_state.saving_throw_modifiers.strength" in effect.get("require", {}).get(
                "condition", ""
            ):
                effect["require"]["condition"] = (
                    "target_state.saving_throw_modifiers.dexterity != null"
                )
                effect["require"]["error"] = (
                    "Use this creature's actual Dexterity saving throw modifier."
                )
            if effect.get("call") == "d20_test":
                effect["params"]["ability"] = "dexterity"
                effect["params"]["ability_modifier"] = (
                    "{target_state.saving_throw_modifiers.dexterity}"
                )
            if effect.get("call") == "install_srd_environment_restrained":
                effect["params"]["save_ability"] = "dexterity"
            if field == "target_state.last_web_save":
                result = effect["set"]["value"]
                result.pop("raw_damage")
                result.pop("applied_damage")
                result["timing"] = "timing"
    prior = next(
        i
        for i, e in enumerate(event["effects"])
        if e.get("condition") == "_target_state_id not in _web_prior"
    )
    event["effects"][prior:prior] = [
        _when(
            "timing == 'start'",
            [
                _require(
                    "combat.turn_active == true and combat.active_actor == _target_state_id",
                    "Web start saves require this creature's actual active turn.",
                ),
                _set("_web_prior", []),
                _when(
                    "_web_source.start_event_turn == _web_turn",
                    [_set("_web_prior", "_web_source.start_event_targets")],
                ),
            ],
        )
    ]
    for effects in list(_effect_lists(event["effects"])):
        first = next(
            (
                i
                for i, e in enumerate(effects)
                if e.get("set", {}).get("field", "").endswith(".event_clock")
            ),
            None,
        )
        if first is not None:
            receipt = effects[first : first + 3]
            effects[first : first + 3] = [
                _when(
                    "timing == 'start'",
                    [
                        _set(
                            "state.persistent_spell_areas.{area_id}.start_event_turn", "_web_turn"
                        ),
                        _set(
                            "state.persistent_spell_areas.{area_id}.start_event_targets",
                            "_web_prior",
                        ),
                    ],
                    receipt,
                )
            ]
    geometry = _blade_barrier_tools()
    selected = {
        name.replace("blade_barrier", "web"): tool
        for name, tool in geometry.items()
        if name
        in (
            "qualify_srd_blade_barrier_source",
            "qualify_srd_blade_barrier_target",
            "resolve_srd_blade_barrier_body_segment",
            "resolve_srd_blade_barrier_intervals",
        )
    }

    def geometry_bind(value):
        if isinstance(value, str):
            return (
                value.replace("blade_barrier", "web")
                .replace("Blade Barrier", "Web")
                .replace("_blade_", "_web_")
                .replace("'blade'", "'web'")
            )
        if isinstance(value, list):
            return [geometry_bind(item) for item in value]
        if isinstance(value, dict):
            return {geometry_bind(key): geometry_bind(item) for key, item in value.items()}
        return value

    tools = geometry_bind(selected)
    tools.update(
        {
            "resolve_srd_web_event": event,
            "escape_srd_web": bind(deepcopy(originals["escape_srd_black_tentacles"])),
            "qualify_srd_web_effect": bind(
                deepcopy(originals["qualify_srd_black_tentacles_effect"])
            ),
            "refresh_srd_web_restraint_target": _environment_tool(
                "Retire exact Web restraints once the actual creature leaves their current volume.",
                [_entity("target_state")],
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_web_exit_position", "_info_position"),
                    *_areas(
                        [
                            _when(
                                "_environment_area.mode == 'web'",
                                [
                                    _call(
                                        "resolve_srd_web_body_segment",
                                        {
                                            "moving_subject": "{_target_state_id}",
                                            "area_id": "{_environment_id}",
                                            "start": "{_web_exit_position}",
                                            "end": "{_web_exit_position}",
                                        },
                                        ("_body_hit",),
                                    ),
                                    _when(
                                        "_body_hit != true",
                                        [
                                            _set(
                                                "_web_exit_ids",
                                                "_environment_area.condition_source_ids",
                                            ),
                                            _each(
                                                "_web_exit_ids",
                                                "_web_exit_id",
                                                [
                                                    _set(
                                                        "_web_exit_source",
                                                        "state.timed_condition_sources[_web_exit_id]",
                                                    ),
                                                    _when(
                                                        "_web_exit_source.installed == true and "
                                                        "_web_exit_source.spell_id == 'web' and "
                                                        "_web_exit_source.target_state_id == "
                                                        "_target_state_id and "
                                                        "_web_exit_source.environment_area_id == "
                                                        "_environment_id and "
                                                        "_web_exit_source.environment_sequence == "
                                                        "_environment_area.sequence",
                                                        [
                                                            _call(
                                                                "retire_srd_timed_condition_source",
                                                                {
                                                                    "source_id": "{_web_exit_id}",
                                                                    "lasting_state": "state",
                                                                },
                                                            ),
                                                            _call(
                                                                "refresh_srd_timed_condition_target",
                                                                {
                                                                    "target_state": (
                                                                        "{_target_state_id}"
                                                                    ),
                                                                    "lasting_state": "state",
                                                                },
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            )
                        ]
                    ),
                ],
            ),
        }
    )
    return tools


def _bind_web(changed):
    """Compose supported floor volume, independent entry/start receipts and later movement."""
    cast = changed["cast_spell_environment"]
    for effects in _effect_lists(cast["effects"]):
        for effect in effects:
            if effect.get("call") == "srd_information_barriers":
                effect["params"]["plane"] = "{_environment_plane}"
    before = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("condition", "").startswith("_environment_profile.concentration == true and")
    )
    cast["effects"][before:before] = [
        _when(
            "_environment_profile.environment_mode == 'web'",
            [
                _require("save_dc >= 1", "Use the actual caster spell save DC for Web."),
                _call(
                    "qualify_srd_environment_ground",
                    {"point": "{_environment_cast_origin}", "plane": "{_environment_plane}"},
                    ("_environment_ground_id",),
                ),
                _set("_web_floor_geometry", "state.spatial_scene.geometry"),
                _set("_web_floor_volume", {}),
                _each(
                    "_web_floor_geometry",
                    "_web_floor",
                    [
                        _when(
                            "_web_floor.id == _environment_ground_id",
                            [_set("_web_floor_volume", "_web_floor.volume")],
                        ),
                    ],
                ),
                _require(
                    "_web_floor_volume.kind == 'box'",
                    "Floor-layered Web requires actual flat Box ground.",
                ),
                _set(
                    "_web_cast_volume",
                    {
                        "kind": "'box'",
                        "min": [
                            "_environment_cast_origin[0] - 10",
                            "_environment_cast_origin[1] - 10",
                            "_environment_cast_origin[2]",
                        ],
                        "max": [
                            "_environment_cast_origin[0] + 10",
                            "_environment_cast_origin[1] + 10",
                            "_environment_cast_origin[2] + 5",
                        ],
                    },
                ),
                _require(
                    "_web_cast_volume.min[0] >= _web_floor_volume.min[0] and "
                    "_web_cast_volume.max[0] <= _web_floor_volume.max[0] and "
                    "_web_cast_volume.min[1] >= _web_floor_volume.min[1] and "
                    "_web_cast_volume.max[1] <= _web_floor_volume.max[1] and "
                    "abs(_environment_cast_origin[2] - _web_floor_volume.max[2]) < 0.000000001",
                    "The whole20-foot floor layer must lie on the actual flat supporting surface.",
                ),
                _require(
                    " and ".join(
                        f"_web_cast_volume.min[{i}] >= state.spatial_scene.map.bounds.min[{i}] "
                        f"and _web_cast_volume.max[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                        for i in range(3)
                    ),
                    "The whole actual Web must fit the admitted map bounds.",
                ),
            ],
        ),
    ]
    install = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("set", {}).get("field", "").endswith(".has_spell_environments")
    )
    cast["effects"][install + 1 : install + 1] = [
        _when(
            "_environment_profile.environment_mode == 'web'",
            [
                *[
                    _set("state.persistent_spell_areas.{_environment_cast_id}." + key, value)
                    for key, value in (
                        ("volume", "_web_cast_volume"),
                        ("ground_id", "_environment_ground_id"),
                        ("layer", "'floor'"),
                        ("event_clock", "'none'"),
                        ("event_turn", -1),
                        ("event_target_ids", []),
                        ("start_event_turn", -1),
                        ("start_event_targets", []),
                    )
                ],
            ],
        ),
    ]
    index = changed["refresh_srd_environment_mode_indices"]
    index["effects"].insert(0, _set("state.has_web", False))
    loop = next(e["for_each"] for e in index["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when("_environment_area.mode == 'web'", [_set("state.has_web", True)])
    )
    terrain = changed["resolve_srd_environment_sphere_terrain_intervals"]
    terrain["effects"].append(
        _when(
            "ignore_ground_environments != true and state.has_web == true",
            [
                _call(
                    "resolve_srd_web_intervals",
                    {"actor_subject": "{actor_subject}", "start": "{start}", "end": "{end}"},
                    ("_web_route_intervals",),
                ),
                _each(
                    "_web_route_intervals",
                    "_web_route_piece",
                    [_push("_sphere_route_intervals", "_web_route_piece")],
                ),
            ],
        )
    )
    entry = changed["resolve_srd_environment_hazard_entry"]
    loop = next(e["for_each"] for e in entry["effects"] if "for_each" in e)
    body = loop["do"][-1]["on_true"]
    candidate = deepcopy(
        next(
            e["on_true"] for e in body if e.get("condition") == "_environment_area.mode == 'blade'"
        )
    )
    for effects in _effect_lists(candidate):
        for effect in effects:
            if effect.get("call") == "resolve_srd_blade_barrier_body_segment":
                effect["call"] = "resolve_srd_web_body_segment"
    body.append(_when("_environment_area.mode == 'web'", candidate))
    dispatch = changed["resolve_srd_environment_hazard_event"]
    dispatch["effects"][-1:] = [
        _when(
            "_hazard_dispatch_source.mode == 'web'",
            [
                _call(
                    "resolve_srd_web_event",
                    {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                ),
            ],
            dispatch["effects"][-1:],
        )
    ]
    starting = changed["resolve_srd_environment_hazard_start_turn"]
    loop = next(e["for_each"] for e in starting["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'web' and combat.turn_active == true and "
            "combat.active_actor == _target_state_id",
            [
                _call(
                    "resolve_srd_web_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{_environment_id}",
                        "timing": "start",
                    },
                ),
            ],
        )
    )
    for effects in _effect_lists(changed["move_srd_scene_creature_node"]["effects"]):
        for effect in effects:
            if "== 'tentacles' and _hazard_entered_event.target_id" in effect.get("condition", ""):
                effect["condition"] = effect["condition"].replace(
                    "state.persistent_spell_areas[_hazard_entered_event.area_id].mode "
                    "== 'tentacles'",
                    "(state.persistent_spell_areas[_hazard_entered_event.area_id].mode "
                    "== 'tentacles' or "
                    "state.persistent_spell_areas[_hazard_entered_event.area_id].mode == 'web')",
                )
    segment = changed["move_srd_scene_creature_environment_segment"]
    if "refresh_srd_web_restraint_target" not in str(segment["effects"]):
        segment["effects"].append(
            _when(
                "state.has_web == true",
                [
                    _call(
                        "refresh_srd_web_restraint_target", {"target_state": "{_actor_state_id}"}
                    ),
                ],
            )
        )
    for name in ("resolve_srd_environment_point", "resolve_srd_environment_segment"):
        for effects in list(_effect_lists(changed[name]["effects"])):
            for i, effect in list(enumerate(effects)):
                if effect.get("call") == "resolve_srd_persistent_area_segment":
                    effects[i] = _when(
                        "_environment_area.mode == 'web'",
                        [
                            _call(
                                "srd_information_segment_interval",
                                {
                                    "start": effect["params"]["start"],
                                    "end": effect["params"]["end"],
                                    "volume": "{_environment_area.volume}",
                                },
                                {
                                    "_sphere_hit": "_segment_hit",
                                    "_sphere_entry": "_segment_entry",
                                    "_sphere_exit": "_segment_exit",
                                },
                            ),
                        ],
                        [effect],
                    )
                if effect.get("condition") == "_environment_area.mode == 'insects'":
                    effect["condition"] += " or _environment_area.mode == 'web'"
    for effects in _effect_lists(changed["dispel_srd_environment_source"]["effects"]):
        for effect in effects:
            requirement = effect.get("require", {})
            if "_environment_dispel_zone_found == true" in requirement.get("condition", ""):
                requirement["condition"] += " or _environment_dispel_source.mode == 'web'"


def _black_tentacles_geometry_tools():
    """Reuse actual box-body intervals and exposure for a saved flat ground square."""
    geometry = _sphere_geometry_tools()
    source = deepcopy(geometry["qualify_srd_environment_sphere_source"])
    source["description"] = "Read the actual current Black Tentacles source and concentration."
    for effects in _effect_lists(source["effects"]):
        for effect in effects:
            if effect.get("set", {}).get("field") == "_sphere_source_current":
                effect["set"]["value"] = effect["set"]["value"].replace(
                    _sphere_mode("_sphere_source"), "_sphere_source.mode == 'tentacles'"
                )
    target = deepcopy(geometry["qualify_srd_environment_sphere_target"])
    target["description"] = "Read actual creature-space ground-square membership and exposure."
    for effects in list(_effect_lists(target["effects"])):
        for effect in list(effects):
            if effect.get("call") == "qualify_srd_environment_sphere_source":
                effect["call"] = "qualify_srd_black_tentacles_source"
            elif (
                effect.get("set", {}).get("field") == "_sphere_nearest_body_point"
                or effect.get("call") == "measure_srd_information_distance"
            ):
                effects.remove(effect)
            elif effect.get("set", {}).get("field") == "_sphere_membership":
                index = effects.index(effect)
                effects[index : index + 1] = [
                    _call(
                        "resolve_srd_black_tentacles_body_segment",
                        {
                            "moving_subject": "{_target_state_id}",
                            "area_id": "{area_id}",
                            "start": "{_info_position}",
                            "end": "{_info_position}",
                        },
                        {"_sphere_membership": "_body_hit"},
                    )
                ]
            elif effect.get("condition") == _cylinder_mode("_sphere_target_source"):
                effects.remove(effect)
    return {
        "qualify_srd_black_tentacles_source": source,
        "qualify_srd_black_tentacles_target": target,
        "resolve_srd_black_tentacles_body_segment": _environment_tool(
            (
                "Reuse the actual creature bounds and existing box interval on this flat ground"
                " square."
            ),
            [_param("moving_subject"), _param("area_id"), _array("start"), _array("end")],
            [
                _set("_body_hit", False),
                _set("_body_entry", None),
                _set("_body_exit", None),
                _set("_body_bounds", {}),
                _set("_tentacle_body_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    (
                        "_tentacle_body_source.mode == 'tentacles' and _tentacle_body_source.vo"
                        "lume.kind == 'box'"
                    ),
                    "Use the actual saved Black Tentacles ground square.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{moving_subject}"},
                    POSITION_EXPORTS,
                ),
                _require(
                    "_info_scene_entity.footprint.kind == 'box'",
                    "Ground square entry requires an actual box creature.",
                ),
                _when(
                    "_info_scene_entity.metadata.plane == _tentacle_body_source.plane and "
                    "_info_scene_entity.metadata.flying != true",
                    [
                        _set(
                            "_tentacle_body_volume",
                            {
                                "kind": "'box'",
                                "min": [
                                    f"_tentacle_body_source.volume.min[{i}] - "
                                    f"_info_scene_entity.footprint.half_extents[{i}]"
                                    for i in range(2)
                                ]
                                + ["_tentacle_body_source.volume.min[2]"],
                                "max": [
                                    f"_tentacle_body_source.volume.max[{i}] + "
                                    f"_info_scene_entity.footprint.half_extents[{i}]"
                                    for i in range(2)
                                ]
                                + ["_tentacle_body_source.volume.max[2]"],
                            },
                        ),
                        _set("_body_bounds", "_tentacle_body_volume"),
                        _call(
                            "srd_information_segment_interval",
                            {
                                "start": "{start}",
                                "end": "{end}",
                                "volume": "{_tentacle_body_volume}",
                            },
                            {
                                "_body_hit": "_segment_hit",
                                "_body_entry": "_segment_entry",
                                "_body_exit": "_segment_exit",
                            },
                        ),
                        _when(
                            "_body_hit == true",
                            [
                                _require(
                                    "abs(start[2] - _tentacle_body_source.origin[2]) "
                                    "< 0.000000001 and "
                                    "abs(end[2] - _tentacle_body_source.origin[2]) < 0.000000001",
                                    (
                                        "Ground square entry currently requires the actual flat"
                                        " source support."
                                    ),
                                )
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }


def _black_tentacles_tools():
    """Own the actual Strength save, failed-save packet, exact restraint and escape action."""
    originals = _entangle_tools()

    def rename(value):
        if isinstance(value, str):
            return value.replace("entangle", "black_tentacles").replace(
                "Entangle", "Black Tentacles"
            )
        if isinstance(value, list):
            return [rename(item) for item in value]
        if isinstance(value, dict):
            return {rename(key): rename(item) for key, item in value.items()}
        return value

    escape = rename(deepcopy(originals["escape_srd_entangle"]))
    scene = rename(deepcopy(originals["resolve_srd_entangle_scene"]))
    scene["description"] = "Resolve every actual initial occupant, including the caster if present."
    for effects in _effect_lists(scene["effects"]):
        for effect in effects:
            if effect.get("call") == "apply_srd_black_tentacles_save":
                effect["call"] = "resolve_srd_black_tentacles_event"
            if "_black_tentacles_scene_source.caster" in effect.get("condition", ""):
                effect["condition"] = effect["condition"].replace(
                    (
                        " and _black_tentacles_scene_entity.metadata.participant_ref != _black_"
                        "tentacles_scene_source.caster"
                    ),
                    "",
                )
    restraint = deepcopy(originals["apply_srd_entangle_save"])
    install = next(
        e
        for effects in _effect_lists(restraint["effects"])
        for e in effects
        if e.get("condition") == "_entangle_restrained == true"
    )["on_true"]
    install = rename(install)
    # Later failed saves keep the same installed restraint and its original source lifetime.
    allocate = install[:2]
    install[:2] = [
        _set("_tentacle_existing_ids", "_black_tentacles_source.condition_source_ids"),
        _each(
            "_tentacle_existing_ids",
            "_tentacle_existing_id",
            [
                _set(
                    "_tentacle_existing",
                    "state.timed_condition_sources[_tentacle_existing_id]",
                ),
                _when(
                    "_tentacle_existing.installed == true and "
                    "_tentacle_existing.spell_id == 'black_tentacles' and "
                    "_tentacle_existing.condition == 'restrained' and "
                    "_tentacle_existing.target_state_id == _target_state_id and "
                    "_tentacle_existing.caster_state_id == _black_tentacles_source.caster and "
                    "_tentacle_existing.environment_area_id == area_id and "
                    "_tentacle_existing.environment_sequence == _black_tentacles_source.sequence",
                    [_set("_black_tentacles_condition_id", "_tentacle_existing_id")],
                ),
            ],
        ),
        _when("_black_tentacles_condition_id == null", allocate),
    ]
    for effects in list(_effect_lists(install)):
        for i, effect in list(enumerate(effects)):
            if effect.get("list_push", {}).get("value") == "_black_tentacles_condition_id":
                effects[i] = _when(
                    "_black_tentacles_condition_id not in _black_tentacles_conditions", [effect]
                )
    tools = {
        **_black_tentacles_geometry_tools(),
        "resolve_srd_black_tentacles_scene": scene,
        "escape_srd_black_tentacles": escape,
        "resolve_srd_black_tentacles_event": _environment_tool(
            (
                "Roll each actual occupant's Strength save once per turn; only failure damages "
                "and restrains."
            ),
            [
                _entity("target_state"),
                _param("area_id"),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            [
                _set("_black_tentacles_source", "state.persistent_spell_areas[area_id]"),
                _set("_black_tentacles_clock", "'exploration'"),
                _set("_black_tentacles_turn", "state.lasting_tick"),
                _when(
                    "combat.turn_active == true",
                    [
                        _set("_black_tentacles_clock", "'combat'"),
                        _set("_black_tentacles_turn", "combat.turn_sequence"),
                    ],
                ),
                _set("_black_tentacles_prior", []),
                _when(
                    "_black_tentacles_source.event_clock == _black_tentacles_clock and "
                    "_black_tentacles_source.event_turn == _black_tentacles_turn",
                    [_set("_black_tentacles_prior", "_black_tentacles_source.event_target_ids")],
                ),
                _when(
                    "_target_state_id not in _black_tentacles_prior",
                    [
                        _call(
                            "qualify_srd_black_tentacles_target",
                            {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                            ("_sphere_target_inside",),
                        ),
                        _when(
                            "_sphere_target_inside == true",
                            [
                                _require(
                                    "target_state.saving_throw_modifiers.strength != null",
                                    "Use this creature's actual Strength saving throw modifier.",
                                ),
                                _call(
                                    "check_srd_target_condition_immunity",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "condition": "restrained",
                                        "source_state_id": "{_black_tentacles_source.caster}",
                                        "source_is_magical": True,
                                    },
                                ),
                                _call(
                                    "d20_test",
                                    {
                                        "actor_state": "{_target_state_id}",
                                        "kind": "saving_throw",
                                        "ability": "strength",
                                        ("ability_modifier"): (
                                            "{target_state.saving_throw_modifiers.strength}"
                                        ),
                                        "proficiency_bonus": 0,
                                        "proficiency_factor": "none",
                                        "circumstantial_modifier": 0,
                                        "target_number": "{_black_tentacles_source.save_dc}",
                                        "advantage": "{_source_condition_save_advantage}",
                                        "disadvantage": False,
                                        "save_avoids_condition": "restrained",
                                    },
                                    ("_d20_test", "_outcome"),
                                ),
                                _set(
                                    "_black_tentacles_restrained",
                                    "_outcome == 'failure' and _source_condition_immune != true",
                                ),
                                _set("_black_tentacles_condition_id", None),
                                _set("_black_tentacles_damage_roll", None),
                                _set("_black_tentacles_effective_damage", 0),
                                _push("_black_tentacles_prior", "_target_state_id"),
                                *[
                                    _set("state.persistent_spell_areas.{area_id}." + key, value)
                                    for key, value in (
                                        ("event_clock", "_black_tentacles_clock"),
                                        ("event_turn", "_black_tentacles_turn"),
                                        ("event_target_ids", "_black_tentacles_prior"),
                                    )
                                ],
                                _when(
                                    "_outcome == 'failure'",
                                    [
                                        {
                                            "dice": {"expression": "3d6"},
                                            "store_as": "_black_tentacles_damage_roll",
                                        },
                                        _call(
                                            "resolve_srd_actor_spell_modifiers",
                                            {
                                                "target_state": "{_black_tentacles_source.caster}",
                                                "lasting_state": "state",
                                            },
                                        ),
                                        _call(
                                            "modify_srd_actor_raw_damage_roll",
                                            {
                                                "actor_state": "{_black_tentacles_source.caster}",
                                                "lasting_state": "state",
                                                "amount": "{_black_tentacles_damage_roll.total}",
                                            },
                                            {"_black_tentacles_amount": "_ray_raw_damage"},
                                        ),
                                        _set(
                                            "_black_tentacles_penalty",
                                            "max(0, -_black_tentacles_amount)",
                                        ),
                                        _set(
                                            "_black_tentacles_amount",
                                            "max(0, _black_tentacles_amount)",
                                        ),
                                        _call(
                                            "apply_srd_qualifying_damage",
                                            {
                                                "target": "{_target_state_id}",
                                                "amount": "{_black_tentacles_amount}",
                                                "adjustment": 0,
                                                "damage_type": "bludgeoning",
                                                ("source_state_id"): (
                                                    "{_black_tentacles_source.caster}"
                                                ),
                                                "witness_state_ids": "{witness_state_ids}",
                                                ("unallocated_roll_penalty"): (
                                                    "{_black_tentacles_penalty}"
                                                ),
                                                "game_damage_roll": True,
                                            },
                                            {
                                                ("_black_tentacles_effective_damage"): (
                                                    "_effective_damage"
                                                )
                                            },
                                        ),
                                        _when(
                                            "_black_tentacles_restrained == true "
                                            "and target_state.is_alive == true "
                                            "and state.persistent_spell_areas[area_id].active "
                                            "== true",
                                            install,
                                        ),
                                    ],
                                ),
                                _set(
                                    "target_state.last_black_tentacles_save",
                                    {
                                        "area_id": "area_id",
                                        "sequence": "_black_tentacles_source.sequence",
                                        "clock": "_black_tentacles_clock",
                                        "turn": "_black_tentacles_turn",
                                        "roll": "_d20_test",
                                        "outcome": "_outcome",
                                        "raw_damage": None,
                                        "applied_damage": "_black_tentacles_effective_damage",
                                        "condition_source_id": None,
                                    },
                                ),
                                _when(
                                    "_black_tentacles_damage_roll != null",
                                    [
                                        _set(
                                            "target_state.last_black_tentacles_save.raw_damage",
                                            "_black_tentacles_damage_roll",
                                        ),
                                    ],
                                ),
                                _when(
                                    "_black_tentacles_condition_id != null",
                                    [
                                        _set(
                                            "target_state.last_black_tentacles_save.condition_source_id",
                                            "_black_tentacles_condition_id",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }
    strongest = deepcopy(_sphere_hazard_tools()["qualify_srd_environment_sphere_effect"])
    for effects in _effect_lists(strongest["effects"]):
        for effect in effects:
            if effect.get("call") == "qualify_srd_environment_sphere_target":
                effect["call"] = "qualify_srd_black_tentacles_target"
    tools["qualify_srd_black_tentacles_effect"] = strongest
    for effects in list(_effect_lists(tools["resolve_srd_black_tentacles_event"]["effects"])):
        for effect in effects:
            if effect.get("condition") == "_sphere_target_inside == true":
                effect["on_true"] = [
                    _call(
                        "qualify_srd_black_tentacles_effect",
                        {
                            "target_state": "{_target_state_id}",
                            "area_id": "{area_id}",
                        },
                        ("_sphere_effect_applies",),
                    ),
                    _when("_sphere_effect_applies == true", effect["on_true"]),
                ]
    return tools


def _bind_black_tentacles(changed):
    """Compose printed ground placement, initial/entry/end timing and source restraint owners."""
    cast = changed["cast_spell_environment"]
    ground = (
        "_environment_profile.environment_mode == 'grease' or _environment_profile.environment_"
        "mode == 'entangle'"
    )
    for effects in list(_effect_lists(cast["effects"])):
        for i, effect in list(enumerate(effects)):
            if ground in effect.get("condition", ""):
                effect["condition"] = effect["condition"].replace(
                    ground, ground + " or _environment_profile.environment_mode == 'tentacles'"
                )
            if effect.get("call") == "resolve_srd_entangle_scene":
                effects[i] = _when(
                    "_environment_profile.environment_mode == 'tentacles'",
                    [
                        _call(
                            "resolve_srd_black_tentacles_scene",
                            {"area_id": "{_environment_cast_id}"},
                        )
                    ],
                    [effect],
                )
    before_concentration = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("condition", "").startswith("_environment_profile.concentration == true and")
    )
    cast["effects"][before_concentration:before_concentration] = [
        _when(
            "_environment_profile.environment_mode == 'tentacles'",
            [
                _call(
                    "resolve_srd_illumination_point_sight",
                    {
                        "observer_state": "{_caster_state_id}",
                        "point": "{_environment_cast_origin}",
                    },
                    ("_illumination_point_can_see",),
                ),
                _require(
                    "_illumination_point_can_see == true",
                    "The caster must see the actual chosen ground square.",
                ),
            ],
        ),
    ]
    ground_install = next(
        effect
        for effect in cast["effects"]
        if "install_srd_environment_ground_zone" in str(effect.get("on_true", []))
    )["on_true"]
    zone_index = next(
        i
        for i, e in enumerate(ground_install)
        if e.get("call") == "install_srd_environment_ground_zone"
    )
    ground_install[zone_index:zone_index] = [
        _when(
            "_environment_profile.environment_mode == 'tentacles'",
            [
                *[
                    _set("state.persistent_spell_areas.{_environment_cast_id}." + key, value)
                    for key, value in (
                        ("event_turn", -1),
                        ("event_clock", "'none'"),
                        ("event_target_ids", []),
                    )
                ],
            ],
        )
    ]
    entry = changed["resolve_srd_environment_hazard_entry"]
    loop = next(e["for_each"] for e in entry["effects"] if "for_each" in e)
    body = loop["do"][-1]["on_true"]
    sphere = next(e for e in body if e.get("condition") == _area_entry_mode("_environment_area"))
    tentative = deepcopy(sphere["on_true"])
    for effects in _effect_lists(tentative):
        for effect in effects:
            if effect.get("call") == "resolve_srd_environment_sphere_body_segment":
                effect["call"] = "resolve_srd_black_tentacles_body_segment"
    body.append(_when("_environment_area.mode == 'tentacles'", tentative))
    dispatch = changed["resolve_srd_environment_hazard_event"]
    dispatch["effects"][-1:] = [
        _when(
            "_hazard_dispatch_source.mode == 'tentacles'",
            [
                _call(
                    "resolve_srd_black_tentacles_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{area_id}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                ),
            ],
            dispatch["effects"][-1:],
        ),
    ]
    ending = changed["resolve_srd_environment_hazard_end_turn"]
    loop = next(e["for_each"] for e in ending["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            (
                "_environment_area.mode == 'tentacles' and combat.turn_active == true and comba"
                "t.active_actor == _target_state_id"
            ),
            [
                _call(
                    "resolve_srd_black_tentacles_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{_environment_id}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                ),
            ],
        )
    )
    for effects in list(_effect_lists(changed["move_srd_scene_creature_node"]["effects"])):
        for i, effect in list(enumerate(effects)):
            if effect.get("call") == "resolve_srd_environment_hazard_event":
                effects[i + 1 : i + 1] = [
                    _when(
                        "state.persistent_spell_areas[_hazard_entered_event.area_id].mode "
                        "== 'tentacles' and _hazard_entered_event.target_id == _actor_state_id "
                        "and 'restrained' in actor_state.conditions",
                        [_set("_ground_route_continue", False)],
                    )
                ]


def _sleet_tools():
    """Own the printed Cylinder entry/start save and flat-ground costs through saved sources."""
    save = deepcopy(_terrain_tools()["apply_srd_grease_save"])

    def rename(value):
        if isinstance(value, str):
            return (
                value.replace("last_grease_save", "last_sleet_storm_save")
                .replace("_grease_", "_sleet_")
                .replace("'grease'", "'sleet'")
                .replace("Grease", "Sleet Storm")
            )
        if isinstance(value, list):
            return [rename(item) for item in value]
        if isinstance(value, dict):
            return {rename(key): rename(item) for key, item in value.items()}
        return value

    save = rename(save)
    save["description"] = (
        "Roll the actual Dexterity save; failure causes Prone and Concentration loss."
    )
    result = save["effects"][-1]["set"]["value"]
    result["lost_concentration"] = (
        "_outcome == 'failure' and target_state.concentration_spell_id != null"
    )
    # Record the actual failed-save receipt before ending a possible self-owned storm.
    save["effects"].append(
        _when(
            "_outcome == 'failure' and target_state.concentration_spell_id != null",
            [
                _call(
                    "end_srd_concentration_node",
                    {
                        "source_state": "{_target_state_id}",
                        "target_state": "{target_state.concentration_target_state_id}",
                        "lasting_state": "state",
                    },
                ),
            ],
        )
    )
    support = deepcopy(_spike_growth_tools()["qualify_srd_spike_growth_support"])
    support["effects"].pop()
    support["description"] = (
        "Require actual flat same-plane ground within the saved Sleet Cylinder."
    )
    for effects in _effect_lists(support["effects"]):
        for effect in effects:
            if "require" in effect:
                effect["require"]["error"] = effect["require"]["error"].replace(
                    "Spike Growth", "Sleet Storm"
                )
            if effect.get("set", {}).get("field") == "_minor_terrain_bounds":
                effect["set"]["value"]["min"][2] = "_minor_terrain_source.cylinder_min"
                effect["set"]["value"]["max"][2] = "_minor_terrain_source.cylinder_max"
    support["effects"].append(
        _require(
            "abs(start[2] - end[2]) < 0.000000001",
            "Sleet Storm ground costs currently require one actual flat support surface.",
        )
    )
    return {
        **_sleet_flame_tools(),
        "apply_srd_sleet_storm_save": save,
        "qualify_srd_sleet_storm_support": support,
        "resolve_srd_sleet_storm_event": _environment_tool(
            "Retain separate real first-entry and starting-turn save receipts.",
            [
                _entity("target_state"),
                _param("area_id"),
                _optional("timing", "string", "entry", enum_values=["entry", "start"]),
            ],
            [
                _set("_sleet_event_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_sleet_event_source.mode == 'sleet'",
                    "Select the actual saved Sleet Storm source.",
                ),
                _set("_sleet_clock", "'exploration'"),
                _set("_sleet_turn", "state.lasting_tick"),
                _when(
                    "combat.turn_active == true",
                    [
                        _set("_sleet_clock", "'combat'"),
                        _set("_sleet_turn", "combat.turn_sequence"),
                    ],
                ),
                _set("_sleet_prior_targets", []),
                _when(
                    "_sleet_event_source.event_clock == _sleet_clock and "
                    "_sleet_event_source.event_turn == _sleet_turn",
                    [_set("_sleet_prior_targets", "_sleet_event_source.event_target_ids")],
                ),
                _when(
                    "timing == 'start'",
                    [
                        _require(
                            (
                                "combat.turn_active == true and combat.active_actor == _target_"
                                "state_id"
                            ),
                            "Sleet start saves need this creature's actual starting turn.",
                        ),
                        _set("_sleet_prior_targets", []),
                        _when(
                            "_sleet_event_source.start_event_turn == _sleet_turn",
                            [
                                _set(
                                    "_sleet_prior_targets",
                                    "_sleet_event_source.start_event_targets",
                                )
                            ],
                        ),
                    ],
                ),
                _when(
                    "_target_state_id not in _sleet_prior_targets",
                    [
                        _call(
                            "qualify_srd_environment_sphere_target",
                            {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                            ("_sphere_target_inside",),
                        ),
                        _when(
                            "_sphere_target_inside == true",
                            [
                                _call(
                                    "qualify_srd_environment_sphere_effect",
                                    {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                                    ("_sphere_effect_applies",),
                                ),
                                _when(
                                    "_sphere_effect_applies == true",
                                    [
                                        _push("_sleet_prior_targets", "_target_state_id"),
                                        _when(
                                            "timing == 'start'",
                                            [
                                                _set(
                                                    (
                                                        "state.persistent_spell_areas.{area_id}"
                                                        ".start_event_turn"
                                                    ),
                                                    "_sleet_turn",
                                                ),
                                                _set(
                                                    (
                                                        "state.persistent_spell_areas.{area_id}"
                                                        ".start_event_targets"
                                                    ),
                                                    "_sleet_prior_targets",
                                                ),
                                            ],
                                            [
                                                _set(
                                                    (
                                                        "state.persistent_spell_areas.{area_id}"
                                                        ".event_clock"
                                                    ),
                                                    "_sleet_clock",
                                                ),
                                                _set(
                                                    (
                                                        "state.persistent_spell_areas.{area_id}"
                                                        ".event_turn"
                                                    ),
                                                    "_sleet_turn",
                                                ),
                                                _set(
                                                    (
                                                        "state.persistent_spell_areas.{area_id}"
                                                        ".event_target_ids"
                                                    ),
                                                    "_sleet_prior_targets",
                                                ),
                                            ],
                                        ),
                                        _call(
                                            "apply_srd_sleet_storm_save",
                                            {
                                                "target_state": "{_target_state_id}",
                                                "area_id": "{area_id}",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "resolve_srd_sleet_storm_terrain_intervals": _environment_tool(
            "Contribute only supported actual ground crossings in current Sleet Cylinders.",
            [_param("actor_subject"), _array("start"), _array("end")],
            [
                _set("_sleet_route_intervals", []),
                _call(
                    "srd_information_scene_entity", {"subject": "{actor_subject}"}, POSITION_EXPORTS
                ),
                _set("_sleet_route_plane", "_info_scene_entity.metadata.plane"),
                _when(
                    "_info_scene_entity.metadata.flying != true",
                    _areas(
                        [
                            _when(
                                "_environment_area.mode == 'sleet' and "
                                "_environment_area.plane == _sleet_route_plane",
                                [
                                    _call(
                                        "qualify_srd_environment_sphere_source",
                                        {
                                            "caster_state": "{_environment_area.caster}",
                                            "area_id": "{_environment_id}",
                                        },
                                        ("_sphere_source_current",),
                                    ),
                                    _when(
                                        "_sphere_source_current == true",
                                        [
                                            _call(
                                                "srd_information_segment_interval",
                                                {
                                                    "start": "{start}",
                                                    "end": "{end}",
                                                    "volume": "{_environment_area.volume}",
                                                },
                                                ("_segment_hit", "_segment_entry", "_segment_exit"),
                                            ),
                                            _when(
                                                "_segment_hit == true and _segment_exit - "
                                                "_segment_entry > 0.000000001",
                                                [
                                                    *[
                                                        _set(
                                                            name,
                                                            [
                                                                f"start[{i}] + (end[{i}] - "
                                                                f"start[{i}]) * {fraction}"
                                                                for i in range(3)
                                                            ],
                                                        )
                                                        for name, fraction in (
                                                            (
                                                                "_sleet_support_start",
                                                                "_segment_entry",
                                                            ),
                                                            ("_sleet_support_end", "_segment_exit"),
                                                        )
                                                    ],
                                                    _call(
                                                        "qualify_srd_sleet_storm_support",
                                                        {
                                                            "area_id": "{_environment_id}",
                                                            "start": "{_sleet_support_start}",
                                                            "end": "{_sleet_support_end}",
                                                        },
                                                    ),
                                                    _push(
                                                        "_sleet_route_intervals",
                                                        {
                                                            "entry": "_segment_entry",
                                                            "exit": "_segment_exit",
                                                            "area_id": "_environment_id",
                                                        },
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            )
                        ]
                    ),
                ),
            ],
        ),
    }


def _sleet_flame_tools():
    """Douse only actual exposed ordinary flame objects wholly within the saved Cylinder."""
    return {
        "extinguish_srd_sleet_flame": _environment_tool(
            "Consume the existing ordinary flame admission and current holder geometry.",
            [_param("area_id"), _param("object_id")],
            [
                _set("_sleet_flame_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_sleet_flame_source.mode == 'sleet'",
                    "Select the actual saved Sleet Storm source.",
                ),
                _call(
                    "qualify_srd_environment_sphere_source",
                    {"caster_state": "{_sleet_flame_source.caster}", "area_id": "{area_id}"},
                    ("_sphere_source_current",),
                ),
                _when(
                    "_sphere_source_current == true",
                    [
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{object_id}"},
                            POSITION_EXPORTS,
                        ),
                        _set("_sleet_flame_entity", "_info_scene_entity"),
                        _require(
                            "_sleet_flame_entity.metadata.is_object == true and "
                            "_sleet_flame_entity.metadata.srd_ordinary_flame.kind == "
                            "'ordinary_flame'",
                            "Douse only an actual admitted ordinary flame object.",
                        ),
                        _when(
                            "_sleet_flame_entity.metadata.srd_ordinary_flame.burning == true and "
                            "_sleet_flame_entity.metadata.srd_ordinary_flame.exposed == true",
                            [
                                _require(
                                    "_sleet_flame_entity.footprint.kind == 'box'",
                                    "Sleet flame sections need their actual admitted box bounds.",
                                ),
                                _call(
                                    "check_srd_illumination_object_visibility",
                                    {
                                        "object_id": "{object_id}",
                                        "observer_state": "{_sleet_flame_source.caster}",
                                    },
                                    ("_illumination_object_origin", "_illumination_object_plane"),
                                ),
                                _when(
                                    "_illumination_object_plane == _sleet_flame_source.plane",
                                    [
                                        _set(
                                            "_sleet_flame_near",
                                            [
                                                f"max(0, abs(_illumination_object_origin[{i}] - "
                                                f"_sleet_flame_source.origin[{i}]) - "
                                                f"_sleet_flame_entity.footprint.half_extents[{i}])"
                                                for i in range(2)
                                            ],
                                        ),
                                        _set(
                                            "_sleet_flame_far",
                                            [
                                                f"abs(_illumination_object_origin[{i}] - "
                                                f"_sleet_flame_source.origin[{i}]) + "
                                                f"_sleet_flame_entity.footprint.half_extents[{i}]"
                                                for i in range(2)
                                            ],
                                        ),
                                        _set(
                                            "_sleet_flame_overlaps",
                                            "_sleet_flame_near[0] ** 2 + _sleet_flame_near[1] ** 2 "
                                            "<= _sleet_flame_source.radius ** 2 and "
                                            "_illumination_object_origin[2] <= "
                                            "_sleet_flame_source.cylinder_max and "
                                            "_illumination_object_origin[2] + "
                                            "_sleet_flame_entity.height "
                                            ">= _sleet_flame_source.cylinder_min",
                                        ),
                                        _set(
                                            "_sleet_flame_inside",
                                            "_sleet_flame_far[0] ** 2 + _sleet_flame_far[1] ** 2 "
                                            "<= _sleet_flame_source.radius ** 2 and "
                                            "_illumination_object_origin[2] >= "
                                            "_sleet_flame_source.cylinder_min and "
                                            "_illumination_object_origin[2] + "
                                            "_sleet_flame_entity.height "
                                            "<= _sleet_flame_source.cylinder_max",
                                        ),
                                        _require(
                                            (
                                                "_sleet_flame_overlaps != true or _sleet_flame_"
                                                "inside == true"
                                            ),
                                            (
                                                "Admit separate flame sections when sleet cover"
                                                "s only part of a flame."
                                            ),
                                        ),
                                        _when(
                                            "_sleet_flame_inside == true",
                                            [
                                                _call(
                                                    "srd_information_barriers",
                                                    {
                                                        "start": "{_sleet_flame_source.origin}",
                                                        "end": "{_illumination_object_origin}",
                                                        "rule": "solid",
                                                        "plane": "{_sleet_flame_source.plane}",
                                                    },
                                                    ("_information_blocked",),
                                                ),
                                                _when(
                                                    "_information_blocked != true",
                                                    [
                                                        _set(
                                                            "_sleet_flame_change",
                                                            {"burning": False},
                                                        ),
                                                        _set(
                                                            "_sleet_flame_record",
                                                            "merge(_sleet_flame_entity.metadata."
                                                            "srd_ordinary_flame, "
                                                            "_sleet_flame_change)",
                                                        ),
                                                        _set(
                                                            "_sleet_flame_metadata_change",
                                                            {
                                                                ("srd_ordinary_flame"): (
                                                                    "_sleet_flame_record"
                                                                )
                                                            },
                                                        ),
                                                        _set(
                                                            "_sleet_flame_metadata",
                                                            "merge(_sleet_flame_entity.metadata, "
                                                            "_sleet_flame_metadata_change)",
                                                        ),
                                                        *_snapshot(),
                                                        {
                                                            "spatial_mutation": {
                                                                "scene": "state.spatial_scene",
                                                                ("map_id"): (
                                                                    "{state.spatial_scene.map.id}"
                                                                ),
                                                                ("expected_revision"): (
                                                                    "{_info_snapshot.geometry_r"
                                                                    "evision}"
                                                                ),
                                                                "target": {
                                                                    "kind": "entity",
                                                                    "id": "{object_id}",
                                                                },
                                                                "patch": {
                                                                    ("metadata"): (
                                                                        "{_sleet_flame_metadata}"
                                                                    )
                                                                },
                                                            },
                                                            "store_as": "_sleet_flame_mutation",
                                                        },
                                                    ],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "refresh_srd_sleet_flames": _environment_tool(
            "Refresh the current source's ordinary flames after actual installation or movement.",
            [],
            _areas(
                [
                    _when(
                        "_environment_area.mode == 'sleet'",
                        [
                            _set("_sleet_flame_candidates", "state.spatial_scene.entities"),
                            _each(
                                "_sleet_flame_candidates",
                                "_sleet_flame_candidate",
                                [
                                    _when(
                                        "_sleet_flame_candidate.active == true and "
                                        "_sleet_flame_candidate.metadata.is_object == true and "
                                        "_sleet_flame_candidate.metadata."
                                        "srd_ordinary_flame != null",
                                        [
                                            _call(
                                                "extinguish_srd_sleet_flame",
                                                {
                                                    "area_id": "{_environment_id}",
                                                    "object_id": "{_sleet_flame_candidate.id}",
                                                },
                                            )
                                        ],
                                    )
                                ],
                            ),
                        ],
                    )
                ]
            ),
        ),
    }


def _bind_sleet_storm(changed):
    """Bind saved Cylinder geometry and printed timing to existing area/scene consumers."""
    cast = changed["cast_spell_environment"]
    index = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "refresh_srd_environment_scene"
    )
    cast["effects"][index:index] = [
        _when(
            "_environment_profile.environment_mode == 'sleet'",
            [
                _set("_sleet_min", "_environment_cast_origin[2]"),
                _when(
                    "cylinder_face == 'top'",
                    [_set("_sleet_min", "_environment_cast_origin[2] - 40")],
                ),
                *[
                    _set("state.persistent_spell_areas.{_environment_cast_id}." + key, value)
                    for key, value in (
                        ("height", 40),
                        ("start_event_turn", -1),
                        ("start_event_targets", []),
                        ("cylinder_face", "cylinder_face"),
                        ("cylinder_min", "_sleet_min"),
                        ("cylinder_max", "_sleet_min + 40"),
                        (
                            "volume",
                            {
                                "kind": "'cylinder'",
                                "center": [
                                    "_environment_cast_origin[0]",
                                    "_environment_cast_origin[1]",
                                    "_sleet_min",
                                ],
                                "radius": 20,
                                "height": 40,
                            },
                        ),
                    )
                ],
            ],
        )
    ]
    starting = changed["resolve_srd_environment_hazard_start_turn"]
    loop = next(e["for_each"] for e in starting["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'sleet' and combat.turn_active == true "
            "and combat.active_actor == _target_state_id",
            [
                _call(
                    "resolve_srd_sleet_storm_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{_environment_id}",
                        "timing": "start",
                    },
                )
            ],
        )
    )
    dispatch = changed["resolve_srd_environment_hazard_event"]
    dispatch["effects"][-1:] = [
        _when(
            "_hazard_dispatch_source.mode == 'sleet'",
            [
                _call(
                    "resolve_srd_sleet_storm_event",
                    {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                )
            ],
            dispatch["effects"][-1:],
        )
    ]
    for name in ("resolve_srd_environment_point", "resolve_srd_environment_segment"):
        for effects in list(_effect_lists(changed[name]["effects"])):
            for i, effect in list(enumerate(effects)):
                if effect.get("call") == "resolve_srd_persistent_area_segment":
                    effects[i] = _when(
                        "_environment_area.mode == 'sleet'",
                        [
                            _call(
                                "srd_information_segment_interval",
                                {
                                    "start": effect["params"]["start"],
                                    "end": effect["params"]["end"],
                                    "volume": "{_environment_area.volume}",
                                },
                                {
                                    "_sphere_hit": "_segment_hit",
                                    "_sphere_entry": "_segment_entry",
                                    "_sphere_exit": "_segment_exit",
                                },
                            )
                        ],
                        [effect],
                    )
    index_tool = changed["refresh_srd_environment_mode_indices"]
    index_tool["effects"].insert(0, _set("state.has_sleet_storm", False))
    loop = next(e["for_each"] for e in index_tool["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when("_environment_area.mode == 'sleet'", [_set("state.has_sleet_storm", True)])
    )
    terrain = changed["resolve_srd_environment_sphere_terrain_intervals"]
    terrain["effects"].append(
        _when(
            "ignore_ground_environments != true and state.has_sleet_storm == true",
            [
                _call(
                    "resolve_srd_sleet_storm_terrain_intervals",
                    {"actor_subject": "{actor_subject}", "start": "{start}", "end": "{end}"},
                    ("_sleet_route_intervals",),
                ),
                _each(
                    "_sleet_route_intervals",
                    "_sleet_route_interval",
                    [_push("_sphere_route_intervals", "_sleet_route_interval")],
                ),
            ],
        )
    )
    changed["refresh_srd_environment_scene"]["effects"].append(
        _when("state.has_sleet_storm == true", [_call("refresh_srd_sleet_flames", {})])
    )
    segment = changed["move_srd_scene_creature_environment_segment"]
    if "refresh_srd_sleet_flames" not in str(segment["effects"]):
        segment["effects"].append(
            _when("state.has_sleet_storm == true", [_call("refresh_srd_sleet_flames", {})])
        )
    if "dispel_srd_environment_source" in changed:
        for effects in _effect_lists(changed["dispel_srd_environment_source"]["effects"]):
            for effect in effects:
                requirement = effect.get("require", {})
                if requirement.get("condition") == "_environment_dispel_zone_found == true":
                    # The Cylinder is the spell's actual saved volume. Only the original
                    # ground sources additionally need their admitted terrain-zone alias.
                    requirement["condition"] += " or _environment_dispel_source.mode == 'sleet'"


def _moonbeam_tools():
    """Move the source Cylinder with a real later-turn Magic action and project its light."""
    move = deepcopy(_cloudkill_tools()["move_srd_cloudkill_source"])
    current = move["effects"][-1]
    assert current["condition"] == "_cloud_source_current == true"
    motion = current["on_true"]
    first = next(
        i for i, e in enumerate(motion) if e.get("set", {}).get("field") == "_cloud_contacts"
    )
    motion = motion[first:]
    for effects in _effect_lists(motion):
        effects[:] = [e for e in effects if e.get("call") != "refresh_srd_environment_wind"]
    motion[:] = [
        e
        for e in motion
        if not e.get("set", {})
        .get("field", "")
        .endswith("persistent_spell_areas.{_environment_area_key}.cloud_last_start_turn")
    ]

    def bind_motion(value):
        if isinstance(value, str):
            return value.replace("cloud_last_move", "moonbeam_last_move").replace(
                "_cloud_", "_moon_"
            )
        if isinstance(value, list):
            return [bind_motion(item) for item in value]
        if isinstance(value, dict):
            return {bind_motion(key): bind_motion(item) for key, item in value.items()}
        return value

    current_call = _call(
        "qualify_srd_environment_sphere_source",
        {"caster_state": "{_caster_state_id}", "area_id": "{area_id}"},
        {"_moon_source_current": "_sphere_source_current"},
    )
    motion = bind_motion(motion)
    for effects in _effect_lists(motion):
        for i, effect in list(enumerate(effects)):
            if (
                effect.get("set", {})
                .get("field", "")
                .endswith("persistent_spell_areas.{_environment_area_key}.origin")
            ):
                effects[i : i + 1] = [
                    _set("_moon_contact_point", effect["set"]["value"]),
                    _call(
                        "set_srd_moonbeam_origin",
                        {"area_id": "{area_id}", "point": "{_moon_contact_point}"},
                    ),
                    _call("refresh_srd_moonbeam_darkness", {}),
                ]
    return {
        "set_srd_moonbeam_origin": _environment_tool(
            "Keep the Cylinder's exact top/bottom face bounds with its sole saved origin.",
            [_param("area_id"), _array("point")],
            [
                _set("_moon_origin_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_moon_origin_source.mode == 'moonbeam'", "Use the actual Moonbeam source."
                ),
                _set("state.persistent_spell_areas.{area_id}.origin", "point"),
                _set("_moon_min", "point[2]"),
                _when(
                    "_moon_origin_source.cylinder_face == 'top'",
                    [_set("_moon_min", "point[2] - _moon_origin_source.height")],
                ),
                _set("state.persistent_spell_areas.{area_id}.cylinder_min", "_moon_min"),
                _set(
                    "state.persistent_spell_areas.{area_id}.cylinder_max",
                    "_moon_min + _moon_origin_source.height",
                ),
            ],
        ),
        "move_srd_moonbeam_source": _public(
            _environment_tool(
                "Use a later-turn Magic action to move the actual Moonbeam Cylinder up to 60 feet.",
                [
                    _entity("caster_state"),
                    _param("area_id"),
                    _array("destination"),
                    _optional("witness_state_ids", "array", None, items={"type": "string"}),
                ],
                [
                    _set("_moon_source", "state.persistent_spell_areas[area_id]"),
                    _require(
                        "_moon_source.spell == 'moonbeam' and "
                        "_moon_source.caster == _caster_state_id",
                        "Use this actual caster's Moonbeam source.",
                    ),
                    current_call,
                    _require(
                        "_moon_source_current == true",
                        "The actual Moonbeam concentration must be current.",
                    ),
                    _require(
                        "combat.turn_active == true and combat.active_actor == _caster_state_id "
                        "and caster_state.turns_started > _moon_source.cast_caster_turn",
                        "Move the Cylinder on this caster's actual later turn.",
                    ),
                    _set("_moon_old_origin", "_moon_source.origin"),
                    _set("_moon_destination", "destination"),
                    _call(
                        "measure_srd_information_distance",
                        {"start": "{_moon_old_origin}", "end": "{destination}"},
                        ("_scene_distance",),
                    ),
                    _require(
                        "_scene_distance > 0.000000001 and _scene_distance <= 60",
                        "Choose positive Cylinder movement up to its printed 60 feet.",
                    ),
                    _require(
                        " and ".join(
                            f"destination[{i}] >= state.spatial_scene.map.bounds.min[{i}] "
                            f"and destination[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                            for i in range(3)
                        ),
                        "The actual Cylinder origin must remain inside the admitted map.",
                    ),
                    _call("spend_srd_action", {"magic_action": True}),
                    *motion,
                ],
                public=True,
            ),
            "combat",
            "Move Moonbeam",
            [("Caster", "_caster_state_id"), ("Destination", "_moon_destination")],
        ),
        "resolve_srd_environment_light": _environment_tool(
            "Read Moonbeam Dim Light from its actual current Cylinder and concentration.",
            [_array("point"), _param("plane")],
            [
                _call("refresh_srd_moonbeam_darkness", {}),
                _set("_environment_magic_dim", False),
                *_areas(
                    [
                        _when(
                            "_environment_area.mode == 'moonbeam' and "
                            "_environment_area.plane == plane",
                            [
                                _call(
                                    "qualify_srd_environment_sphere_source",
                                    {
                                        "caster_state": "{_environment_area.caster}",
                                        "area_id": "{_environment_id}",
                                    },
                                    ("_sphere_source_current",),
                                ),
                                _when(
                                    "_sphere_source_current == true and point[2] >= "
                                    "_environment_area.cylinder_min and point[2] <= "
                                    "_environment_area.cylinder_max and (point[0] - "
                                    "_environment_area.origin[0]) ** 2 + (point[1] - "
                                    "_environment_area.origin[1]) ** 2 <= "
                                    "_environment_area.radius ** 2",
                                    [_set("_environment_magic_dim", True)],
                                ),
                            ],
                        )
                    ]
                ),
            ],
        ),
        **_moonbeam_darkness_tools(),
        **_moonbeam_form_tools(),
    }


def _moonbeam_darkness_tools():
    """Retire eligible Moonbeam light using actual uncovered Darkness source geometry."""
    return {
        "dispel_srd_moonbeam_darkness_record": _environment_tool(
            "Qualify exact Cylinder/Sphere overlap for one actual active Darkness record.",
            [_param("area_id"), _param("record", "object", from_state="state.turn_effects")],
            [
                _set("_moon_dark_area", "state.persistent_spell_areas[area_id]"),
                _require(
                    "record in state.turn_effects and record.kind == 'illumination_spell' "
                    "and record.spell == 'darkness' and _moon_dark_area.mode == 'moonbeam'",
                    "Use actual admitted Darkness and Moonbeam sources.",
                ),
                _call(
                    "qualify_srd_environment_sphere_source",
                    {"caster_state": "{_moon_dark_area.caster}", "area_id": "{area_id}"},
                    ("_sphere_source_current",),
                ),
                _call(
                    "check_srd_illumination_record",
                    {"record": "{record}", "caster_state": "{record.caster}"},
                    (
                        "_illumination_active",
                        "_illumination_covered",
                        "_illumination_origin",
                        "_illumination_plane",
                    ),
                ),
                _when(
                    "_sphere_source_current == true and _moon_dark_area.spell_level <= 2 "
                    "and _illumination_active == true and _illumination_covered != true "
                    "and _illumination_plane == _moon_dark_area.plane",
                    [
                        _set(
                            "_moon_dark_horizontal",
                            "max(0, ((_illumination_origin[0] - _moon_dark_area.origin[0]) ** 2 "
                            "+ (_illumination_origin[1] - _moon_dark_area.origin[1]) ** 2) ** "
                            "0.5 - _moon_dark_area.radius)",
                        ),
                        _set(
                            "_moon_dark_vertical",
                            "max(0, _moon_dark_area.cylinder_min - _illumination_origin[2], "
                            "_illumination_origin[2] - _moon_dark_area.cylinder_max)",
                        ),
                        _when(
                            "_moon_dark_horizontal ** 2 + _moon_dark_vertical ** 2 <= "
                            "record.dark_radius ** 2",
                            [
                                _call(
                                    "end_srd_concentration_node",
                                    {
                                        "source_state": "{_moon_dark_area.caster}",
                                        "target_state": "{_moon_dark_area.caster}",
                                        "lasting_state": "state",
                                    },
                                )
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "refresh_srd_moonbeam_darkness": _environment_tool(
            "Settle Darkness's exact two-level dispel against current Moonbeam Cylinders.",
            [],
            _areas(
                [
                    _when(
                        "_environment_area.mode == 'moonbeam' and "
                        "_environment_area.spell_level <= 2 and state.turn_effects != null",
                        [
                            _set("_moon_dark_records", "state.turn_effects"),
                            _each(
                                "_moon_dark_records",
                                "_moon_dark_record",
                                [
                                    _when(
                                        "_moon_dark_record.kind == 'illumination_spell' and "
                                        "_moon_dark_record.spell == 'darkness'",
                                        [
                                            _call(
                                                "dispel_srd_moonbeam_darkness_record",
                                                {
                                                    "area_id": "{_environment_id}",
                                                    "record": "{_moon_dark_record}",
                                                },
                                            )
                                        ],
                                    )
                                ],
                            ),
                        ],
                    )
                ]
            ),
        ),
    }


def _moonbeam_form_tools():
    """Retire actual changed body sources through their owner; fence later changes by area."""
    tools = {
        "check_srd_moonbeam_shapechange": _environment_tool(
            "Qualify saved failed-save restrictions against the actual current Cylinder body.",
            [_entity("target_state")],
            [
                _set("_moonbeam_shapechange_allowed", True),
                _when(
                    "target_state.moonbeam_shapechange_sources != null",
                    [
                        _set("_moon_shape_receipts", "target_state.moonbeam_shapechange_sources"),
                        _set("_moon_retained_receipts", []),
                        _each(
                            "_moon_shape_receipts",
                            "_moon_shape_receipt",
                            [
                                _set(
                                    "_moon_shape_area",
                                    "state.persistent_spell_areas[_moon_shape_receipt.area_id]",
                                ),
                                _when(
                                    "_moon_shape_area != null and _moon_shape_area.sequence == "
                                    "_moon_shape_receipt.sequence and "
                                    "_moon_shape_area.mode == 'moonbeam'",
                                    [
                                        _call(
                                            "qualify_srd_environment_sphere_target",
                                            {
                                                "target_state": "{_target_state_id}",
                                                "area_id": "{_moon_shape_receipt.area_id}",
                                            },
                                            ("_sphere_target_inside",),
                                        ),
                                        _when(
                                            "_sphere_target_inside == true",
                                            [
                                                _push(
                                                    "_moon_retained_receipts", "_moon_shape_receipt"
                                                )
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        _set(
                            "target_state.moonbeam_shapechange_sources", "_moon_retained_receipts"
                        ),
                        _set("_moonbeam_shapechange_allowed", "len(_moon_retained_receipts) == 0"),
                    ],
                ),
            ],
        ),
        "revert_srd_moonbeam_form": _environment_tool(
            "Apply the actual failed-save reversion using the existing transformation owner.",
            [_entity("target_state"), _param("area_id")],
            [
                _set("_moon_revert_area", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_moon_revert_area.mode == 'moonbeam'", "Use the actual failed Moonbeam source."
                ),
                _when(
                    "target_state.has_transformations == true and "
                    "target_state.form_current_source_id != null",
                    [
                        _set(
                            "_moon_actual_form",
                            "state.transformation_sources[target_state.form_current_source_id]",
                        ),
                        _when(
                            "_moon_actual_form.installed == true and "
                            "_moon_actual_form.target_state_id == _target_state_id and "
                            "(_moon_actual_form.profile == 'beast' or "
                            "_moon_actual_form.profile == 'alter' or "
                            "_moon_actual_form.profile == 'mist' or "
                            "(_moon_actual_form.profile == 'wind' and "
                            "(_moon_actual_form.mode == 'cloud' or "
                            "_moon_actual_form.mode == 'to_normal')))",
                            [
                                _set("_moon_revert_ids", "target_state.transformation_source_ids"),
                                _each(
                                    "_moon_revert_ids",
                                    "_moon_revert_id",
                                    [
                                        _set(
                                            "_moon_revert_form",
                                            "state.transformation_sources[_moon_revert_id]",
                                        ),
                                        _when(
                                            "_moon_revert_form.installed == true",
                                            [
                                                _call(
                                                    "end_srd_transformation_source",
                                                    {
                                                        "target_state": "{_target_state_id}",
                                                        "source_id": "{_moon_revert_id}",
                                                    },
                                                )
                                            ],
                                        ),
                                    ],
                                ),
                                _call(
                                    "check_srd_moonbeam_shapechange",
                                    {"target_state": "{_target_state_id}"},
                                ),
                                _set(
                                    "_moon_reversion_receipts",
                                    "target_state.moonbeam_shapechange_sources",
                                ),
                                _when(
                                    "_moon_reversion_receipts == null",
                                    [_set("_moon_reversion_receipts", [])],
                                ),
                                _push(
                                    "_moon_reversion_receipts",
                                    {
                                        "area_id": "area_id",
                                        "sequence": "_moon_revert_area.sequence",
                                    },
                                ),
                                _set(
                                    "target_state.moonbeam_shapechange_sources",
                                    "_moon_reversion_receipts",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }
    revert = tools["revert_srd_moonbeam_form"]
    prefix = deepcopy(revert["effects"][:3])
    qualification = deepcopy(revert["effects"][3])
    consequence = qualification["on_true"][1]["on_true"]
    qualification["on_true"][1]["on_true"] = [
        _set(
            "target_state.moonbeam_pending_form_reversion",
            {
                "area_id": "_environment_area_key",
                "sequence": "_moon_revert_area.sequence",
                "form_ids": "target_state.transformation_source_ids",
            },
        )
    ]
    tools["prepare_srd_moonbeam_reversion"] = _environment_tool(
        "Capture the actual changed body before this failed-save damage can deplete its TempHP.",
        [
            _entity("target_state"),
            _param("area_id"),
            _optional("lasting_state", "string", "state", resolve="entity"),
        ],
        [
            *prefix,
            _set("target_state.moonbeam_pending_form_reversion", None),
            qualification,
        ],
    )
    consequence[0] = _set("_moon_revert_ids", "_moon_pending_reversion.form_ids")
    revert["effects"] = [
        *prefix,
        _set("_moon_pending_reversion", "target_state.moonbeam_pending_form_reversion"),
        _when(
            "_moon_pending_reversion != null and _moon_pending_reversion.area_id == "
            "_environment_area_key and _moon_pending_reversion.sequence == "
            "_moon_revert_area.sequence",
            consequence,
        ),
        _set("target_state.moonbeam_pending_form_reversion", None),
    ]
    return tools


def _bind_moonbeam(changed):
    """Bind Cylinder source geometry and failed-save reversion at their actual area owners."""
    cast = changed["cast_spell_environment"]
    source_install = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("set", {}).get("field", "").endswith(".has_spell_environments")
    )
    cast["effects"][source_install + 1 : source_install + 1] = [
        _when(
            "_environment_profile.environment_mode == 'moonbeam'",
            [
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.height",
                    "_environment_profile.height_feet",
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.cylinder_face",
                    "cylinder_face",
                ),
                _call(
                    "set_srd_moonbeam_origin",
                    {"area_id": "{_environment_cast_id}", "point": "{_environment_cast_origin}"},
                ),
                _set("state.persistent_spell_areas.{_environment_cast_id}.cast_caster_turn", 0),
                _when(
                    "caster_state.turns_started != null",
                    [
                        _set(
                            "state.persistent_spell_areas.{_environment_cast_id}.cast_caster_turn",
                            "caster_state.turns_started",
                        )
                    ],
                ),
            ],
        )
    ]
    refresh = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "refresh_srd_environment_scene"
    )
    cast["effects"][refresh:refresh] = [
        _when(
            "_environment_profile.environment_mode == 'moonbeam'",
            [_call("refresh_srd_moonbeam_darkness", {})],
        )
    ]
    event = changed["resolve_srd_environment_sphere_event"]
    for effects in _effect_lists(event["effects"]):
        for i, effect in list(enumerate(effects)):
            if effect.get("call") == "apply_srd_qualifying_damage":
                effects[i:i] = [
                    _when(
                        "_sphere_event_source.mode == 'moonbeam' and _outcome == 'failure'",
                        [
                            _call(
                                "prepare_srd_moonbeam_reversion",
                                {
                                    "target_state": "{_target_state_id}",
                                    "area_id": "{_environment_area_key}",
                                },
                            )
                        ],
                    )
                ]
                effects[i + 2 : i + 2] = [
                    _when(
                        "_sphere_event_source.mode == 'moonbeam' and _outcome == 'failure'",
                        [
                            _call(
                                "revert_srd_moonbeam_form",
                                {
                                    "target_state": "{_target_state_id}",
                                    "area_id": "{_environment_area_key}",
                                },
                            )
                        ],
                    )
                ]


def _bind_sphere_hazards(changed):
    """Compose actual Sphere source consequences inside the established environment owners."""
    cast = changed["cast_spell_environment"]
    refresh = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "refresh_srd_environment_scene"
    )
    cast["effects"][refresh:refresh] = [
        _when(
            _sphere_mode("_environment_profile").replace(".mode", ".environment_mode"),
            [
                _require("save_dc >= 1", "Sphere saves require the actual caster save DC."),
                _require(
                    " and ".join(
                        f"_environment_cast_origin[{i}] >= state.spatial_scene.map.bounds.min[{i}] "
                        f"and _environment_cast_origin[{i}] <= "
                        f"state.spatial_scene.map.bounds.max[{i}]"
                        for i in range(3)
                    ),
                    "The actual Sphere origin must lie inside the admitted map bounds.",
                ),
                _set("state.persistent_spell_areas.{_environment_cast_id}.event_turn", -1),
                _set("state.persistent_spell_areas.{_environment_cast_id}.event_clock", "'none'"),
                _set("state.persistent_spell_areas.{_environment_cast_id}.event_target_ids", []),
                _when(
                    "_environment_profile.environment_mode == 'cloudkill'",
                    [
                        _set(
                            "state.persistent_spell_areas.{_environment_cast_id}.cloud_"
                            "last_start_turn",
                            0,
                        ),
                        _when(
                            "caster_state.turns_started != null",
                            [
                                _set(
                                    "state.persistent_spell_areas.{_environment_cast_id}.cloud_"
                                    "last_start_turn",
                                    "caster_state.turns_started",
                                )
                            ],
                        ),
                    ],
                ),
                _when(
                    _sphere_damage_mode("_environment_profile").replace(
                        ".mode", ".environment_mode"
                    ),
                    [
                        _call(
                            "resolve_srd_environment_sphere_scene",
                            {"area_id": "{_environment_cast_id}"},
                        )
                    ],
                ),
            ],
        )
    ]
    entry = changed["resolve_srd_environment_hazard_entry"]
    area_loop = next(e["for_each"] for e in entry["effects"] if "for_each" in e)
    area_body = area_loop["do"][-1]["on_true"]
    emanation = area_body[0]
    candidates = next(
        effects
        for effects in _effect_lists(emanation["on_true"])
        if any(e.get("call") == "resolve_srd_environment_body_segment" for e in effects)
    )
    candidate = deepcopy(candidates)
    body_call = candidate[0]
    assert body_call.get("call") == "resolve_srd_environment_body_segment"
    body_call["call"] = "resolve_srd_environment_sphere_body_segment"
    body_call["params"] = {
        "moving_subject": "{_actor_state_id}",
        "area_id": "{_environment_id}",
        "start": "{_hazard_start}",
        "end": "{destination}",
    }
    candidate = [e for e in candidate if "_elemental_entry_midpoint" not in str(e)]
    area_body.append(
        _when(
            _area_entry_mode("_environment_area"),
            [
                _set("_hazard_done_ids", []),
                _when(
                    "_environment_area.event_clock == _hazard_clock and "
                    "_environment_area.event_turn == _hazard_turn",
                    [_set("_hazard_done_ids", "_environment_area.event_target_ids")],
                ),
                _when(
                    "_actor_state_id not in _hazard_done_ids",
                    [_set("_hazard_candidate", "_actor_state_id"), *candidate],
                ),
            ],
        )
    )
    dispatch = changed["resolve_srd_environment_hazard_event"]
    old_dispatch = dispatch["effects"][-1]
    dispatch["effects"][-1] = _when(
        _sphere_damage_mode("_hazard_dispatch_source"),
        [
            _call(
                "resolve_srd_environment_sphere_event",
                {
                    "target_state": "{_target_state_id}",
                    "area_id": "{area_id}",
                    "witness_state_ids": "{witness_state_ids}",
                },
            )
        ],
        [old_dispatch],
    )
    ending = changed["resolve_srd_environment_hazard_end_turn"]
    ending_loop = next(e["for_each"] for e in ending["effects"] if "for_each" in e)
    ending_loop["do"][-1]["on_true"].append(
        _when(
            _sphere_damage_mode("_environment_area")
            + " and combat.turn_active == true and combat.active_actor == _target_state_id",
            [
                _call(
                    "resolve_srd_environment_sphere_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{_environment_id}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                )
            ],
        )
    )
    starting_loop = next(
        e["for_each"]
        for e in changed["resolve_srd_environment_hazard_start_turn"]["effects"]
        if "for_each" in e
    )
    starting_loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'cloudkill' and _environment_area.caster == "
            "_target_state_id and combat.turn_active == true and "
            "combat.active_actor == _target_state_id and target_state.turns_started > "
            "_environment_area.cloud_last_start_turn",
            [
                _call(
                    "move_srd_cloudkill_source",
                    {
                        "caster_state": "{_target_state_id}",
                        "area_id": "{_environment_id}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                )
            ],
        )
    )
    cost = changed["srd_information_movement_cost"]
    for effects in _effect_lists(cost["effects"]):
        retained = []
        for effect in effects:
            loop = effect.get("for_each", {})
            if loop.get("collection") == "_route_minor_intervals" and any(
                e.get("for_each", {}).get("collection") == "_route_minor_intervals"
                for e in retained
            ):
                continue
            if effect.get("set", {}).get("field") == "_route_minor_intervals" and any(
                e.get("set", {}).get("field") == "_route_minor_intervals" for e in retained
            ):
                continue
            registration = {e.get("call") for e in effect.get("on_true", []) if e.get("call")}
            registered = {
                e.get("call")
                for prior in retained
                for e in prior.get("on_true", [])
                if e.get("call")
            }
            owners = {
                "resolve_srd_minor_terrain_intervals",
                "resolve_srd_environment_sphere_terrain_intervals",
            }
            if registration & owners and registered & owners:
                continue
            retained.append(effect)
        effects[:] = retained
        for effect in effects:
            if "resolve_srd_minor_terrain_intervals" in str(effect.get("on_true", [])):
                effect["condition"] = (
                    "state.has_spell_environments == true and len(actor_subject) > 0"
                )
            if effect.get("call") == "resolve_srd_minor_terrain_intervals":
                effect["call"] = "resolve_srd_environment_sphere_terrain_intervals"
                effect["params"]["ignore_ground_environments"] = "{ignore_ground_environments}"
                effect["export_context"] = {"_route_minor_intervals": "_sphere_route_intervals"}
            if effect.get("for_each", {}).get("as") == "_route_minor_interval":
                piece = effect["for_each"]["do"][0]
                if not piece.get("condition", "").startswith("_route_mid >="):
                    piece.pop("on_true", None)
                    continue
                difficult = [
                    _set("_route_piece_difficult", True),
                    _set("_route_nonplant_difficult", True),
                ]
                piece["on_true"] = [
                    _when(
                        "_route_minor_interval.sphere_source == true",
                        [
                            _call(
                                "srd_information_barriers",
                                {
                                    "start": "{_route_minor_interval.origin}",
                                    "end": "{_route_point}",
                                    "rule": "solid",
                                    "plane": "{_route_minor_interval.plane}",
                                },
                                ("_information_blocked",),
                            ),
                            _when("_information_blocked != true", difficult),
                        ],
                        deepcopy(difficult),
                    )
                ]
    d20 = changed["d20_test"]
    for effects in _effect_lists(d20["effects"]):
        point_call = next(
            (e for e in effects if e.get("call") == "resolve_srd_environment_point"), None
        )
        if point_call and "_environment_fog" in str(effects):
            point_call["export_context"]["_environment_lightly_obscured"] = (
                "_environment_lightly_obscured"
            )
            if not any("_environment_lightly_obscured == true" in str(e) for e in effects):
                effects.append(
                    _when(
                        "_environment_lightly_obscured == true and ability == 'wisdom' "
                        "and skill == 'perception'",
                        [_set("_effective_disadvantage", True)],
                    )
                )


def _spirit_guardians_tools():
    """Reuse actual Emanation bodies and the established saving-throw damage packet."""

    def bind(value):
        if isinstance(value, str):
            return value.replace(
                "last_environment_sphere_save", "last_spirit_guardians_save"
            ).replace("_sphere_", "_guardian_")
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {bind(key): bind(item) for key, item in value.items()}
        return value

    sphere = _sphere_hazard_tools()["resolve_srd_environment_sphere_event"]
    packet = next(
        e["on_true"]
        for effects in _effect_lists(sphere["effects"])
        for e in effects
        if e.get("condition") == "_sphere_effect_applies == true"
    )
    packet = bind(deepcopy(packet))
    for effects in _effect_lists(packet):
        for effect in effects:
            if effect.get("call") == "apply_srd_qualifying_damage":
                effect["params"]["damage_type"] = "{_guardian_event_source.damage_type}"
    strongest = deepcopy(_conjure_tools()["qualify_srd_woodland_effect"])

    def bind_strongest(value):
        if isinstance(value, str):
            return value.replace("_woodland_", "_guardian_").replace(
                "_target_state_id in _environment_area.force_target_ids",
                "_target_state_id not in _environment_area.unaffected_actor_ids",
            )
        if isinstance(value, list):
            return [bind_strongest(item) for item in value]
        if isinstance(value, dict):
            return {bind_strongest(key): bind_strongest(item) for key, item in value.items()}
        return value

    strongest = bind_strongest(strongest)
    for effects in _effect_lists(strongest["effects"]):
        for effect in effects:
            if effect.get("condition") == "_emanation_inside == true":
                effect["on_true"] = [_set("_guardian_effect_applies", False)]
    selected = deepcopy(_conjure_tools()["select_srd_environment_targets"])
    selected["description"] = (
        "Retain only the caster's actual designated unaffected creatures for this cast."
    )
    selected["metadata"] = {
        "category": "internal",
        "visible_to_player": False,
        "costs_action": False,
    }
    for effects in _effect_lists(selected["effects"]):
        for effect in effects:
            if "require" in effect and "_selected_source.active" in effect["require"]["condition"]:
                effect["require"]["condition"] = (
                    "_selected_source.active == true and _selected_source.mode == 'guardians' and "
                    "caster_state.concentration_spell_id == _selected_source.spell and "
                    "caster_state.concentration_effect_family == 'spell_environment' and "
                    "lasting_state.lasting_tick < _selected_source.expires_tick"
                )
                effect["require"]["error"] = (
                    "Designate unaffected creatures for this actual Spirit Guardians cast."
                )
            if effect.get("set", {}).get("field", "").endswith(".force_target_ids"):
                effect["set"]["field"] = effect["set"]["field"].replace(
                    ".force_target_ids", ".unaffected_actor_ids"
                )
    return {
        "select_srd_spirit_guardians_unaffected": selected,
        "qualify_srd_spirit_guardians_effect": strongest,
        "qualify_srd_spirit_guardians_target": _environment_tool(
            "Read exact current body membership and the actual source's unaffected choices.",
            [_entity("target_state"), _param("area_id")],
            [
                _set("_guardian_source", "state.persistent_spell_areas[area_id]"),
                _set("_guardian_target_inside", False),
                _when(
                    (
                        "_guardian_source.mode == 'guardians' and _target_state_id not in _guar"
                        "dian_source.unaffected_actor_ids"
                    ),
                    [
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_target_state_id}"},
                            POSITION_EXPORTS,
                        ),
                        _when(
                            (
                                "_info_scene_entity.metadata.is_object != true and _info_scene_"
                                "entity.metadata.creature_type != 'spell_force'"
                            ),
                            [
                                _call(
                                    "qualify_srd_environment_emanation_target",
                                    {
                                        "caster_state": "{_guardian_source.caster}",
                                        "target_state": "{_target_state_id}",
                                        "area_id": "{area_id}",
                                    },
                                    {"_guardian_target_inside": "_emanation_inside"},
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "resolve_srd_spirit_guardians_event": _environment_tool(
            "Force this actual affected creature's Wisdom save once in its source's actual turn.",
            [
                _entity("target_state"),
                _param("area_id"),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            [
                _set("_guardian_event_source", "state.persistent_spell_areas[area_id]"),
                _set("_guardian_clock", "'exploration'"),
                _set("_guardian_turn", "state.lasting_tick"),
                _when(
                    "combat.turn_active == true",
                    [
                        _set("_guardian_clock", "'combat'"),
                        _set("_guardian_turn", "combat.turn_sequence"),
                    ],
                ),
                _set("_guardian_prior_targets", []),
                _when(
                    (
                        "_guardian_event_source.event_clock == _guardian_clock and _guardian_ev"
                        "ent_source.event_turn == _guardian_turn"
                    ),
                    [_set("_guardian_prior_targets", "_guardian_event_source.event_target_ids")],
                ),
                _when(
                    "_target_state_id not in _guardian_prior_targets",
                    [
                        _call(
                            "qualify_srd_spirit_guardians_target",
                            {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                            ("_guardian_target_inside",),
                        ),
                        _when(
                            "_guardian_target_inside == true",
                            [
                                _call(
                                    "qualify_srd_spirit_guardians_effect",
                                    {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                                    ("_guardian_effect_applies",),
                                ),
                                _when(
                                    "_guardian_effect_applies == true",
                                    [
                                        _call(
                                            "srd_information_scene_entity",
                                            {"subject": "{_guardian_event_source.caster}"},
                                            POSITION_EXPORTS,
                                        ),
                                        _set(
                                            "_guardian_origin_overlay", {"origin": "_info_position"}
                                        ),
                                        _set(
                                            "_guardian_event_source",
                                            (
                                                "merge(_guardian_event_source, _guardian_origin"
                                                "_overlay)"
                                            ),
                                        ),
                                        *packet,
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "resolve_srd_spirit_guardians_speed": _environment_tool(
            "Derive current magical Speed halving from actual affected body Emanation membership.",
            [_entity("actor_state")],
            [
                _set("_guardian_speed_halved", False),
                _when(
                    "actor_state.spell_freedom_of_movement != true",
                    _areas(
                        [
                            _when(
                                (
                                    "_environment_area.mode == 'guardians' and _guardian_speed_"
                                    "halved != true"
                                ),
                                [
                                    _call(
                                        "qualify_srd_spirit_guardians_target",
                                        {
                                            "target_state": "{_actor_state_id}",
                                            "area_id": "{_environment_id}",
                                        },
                                        ("_guardian_target_inside",),
                                    ),
                                    _when(
                                        "_guardian_target_inside == true",
                                        [_set("_guardian_speed_halved", True)],
                                    ),
                                ],
                            ),
                        ]
                    ),
                ),
            ],
        ),
        "resolve_srd_spirit_guardians_speed_boundary": _environment_tool(
            "Find the next actual body entry or exit before the sole movement owner spends Speed.",
            [_entity("actor_state"), _array("destination")],
            [
                _set("_guardian_speed_boundary_t", 2),
                _set("_guardian_speed_boundary_destination", "destination"),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_actor_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_guardian_route_start", "_info_position"),
                _set("_guardian_route_plane", "_info_scene_entity.metadata.plane"),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_guardian_route_start}", "end": "{destination}"},
                    ("_scene_distance",),
                ),
                _set("_guardian_route_distance", "_scene_distance"),
                _when(
                    (
                        "_guardian_route_distance > 0.000000001 and actor_state.spell_freedom_o"
                        "f_movement != true"
                    ),
                    _areas(
                        [
                            _when(
                                (
                                    "_environment_area.mode == 'guardians' and _actor_state_id "
                                    "not in _environment_area.unaffected_actor_ids and (_actor_"
                                    "state_id != _environment_area.anchor_id or _environment_ar"
                                    "ea.include_origin == true)"
                                ),
                                [
                                    _call(
                                        "qualify_srd_environment_emanation_source",
                                        {
                                            "caster_state": "{_environment_area.caster}",
                                            "area_id": "{_environment_id}",
                                        },
                                        ("_emanation_source_current",),
                                    ),
                                    _when(
                                        "_emanation_source_current == true",
                                        [
                                            _call(
                                                "srd_information_scene_entity",
                                                {"subject": "{_environment_area.anchor_id}"},
                                                POSITION_EXPORTS,
                                            ),
                                            _when(
                                                (
                                                    "_info_scene_entity.metadata.plane == _guar"
                                                    "dian_route_plane"
                                                ),
                                                [
                                                    _call(
                                                        "resolve_srd_environment_body_segment",
                                                        {
                                                            "moving_subject": "{_actor_state_id}",
                                                            ("fixed_subject"): (
                                                                "{_environment_area.anchor_id}"
                                                            ),
                                                            "start": "{_guardian_route_start}",
                                                            "end": "{destination}",
                                                            "radius": "{_environment_area.radius}",
                                                        },
                                                        ("_body_hit", "_body_entry", "_body_exit"),
                                                    ),
                                                    _when(
                                                        "_body_hit == true",
                                                        [
                                                            _set("_guardian_route_cut", 2),
                                                            _when(
                                                                "_body_entry > 0.000000000001",
                                                                [
                                                                    _set(
                                                                        "_guardian_route_cut",
                                                                        "_body_entry",
                                                                    )
                                                                ],
                                                                [
                                                                    _when(
                                                                        "_body_exit < 1",
                                                                        [
                                                                            _set(
                                                                                (
                                                                                    "_guardian_route_"
                                                                                    "cut"
                                                                                ),
                                                                                (
                                                                                    "min(1, _body_"
                                                                                    "exit + 0.0000"
                                                                                    "01 * _"
                                                                                    "guardian_route_d"
                                                                                    "istance ** -1)"
                                                                                ),
                                                                            )
                                                                        ],
                                                                    ),
                                                                ],
                                                            ),
                                                            _when(
                                                                (
                                                                    "_guardian_route_cut < _gua"
                                                                    "rdian_speed_boundary_t"
                                                                ),
                                                                [
                                                                    _set(
                                                                        (
                                                                            "_guardian_speed_bo"
                                                                            "undary_t"
                                                                        ),
                                                                        "_guardian_route_cut",
                                                                    )
                                                                ],
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ]
                    ),
                ),
                _when(
                    "_guardian_speed_boundary_t <= 1",
                    [
                        _set(
                            "_guardian_speed_boundary_destination",
                            [
                                f"_guardian_route_start[{i}] + (destination[{i}] - "
                                f"_guardian_route_start[{i}]) * _guardian_speed_boundary_t"
                                for i in range(3)
                            ],
                        )
                    ],
                ),
            ],
        ),
    }


def _bind_spirit_guardians(changed):
    """Bind actual self placement and retained alignment before source effects are installed."""
    cast = changed["cast_spell_environment"]
    cast["parameters"].extend(
        [
            _optional("unaffected_targets", "array", [], items={"type": "string"}, max_items=64),
            {
                **_optional("spirit_appearance", "string", "angelic"),
                "enum_values": ["angelic", "fey"],
            },
        ]
    )
    for effects in _effect_lists(cast["effects"]):
        for effect in effects:
            if (
                "condition" in effect
                and "_environment_profile.environment_mode == 'woodland'" in effect["condition"]
                and not any(
                    e.get("call") == "select_srd_environment_targets"
                    for e in effect.get("on_true", [])
                )
            ):
                effect["condition"] = (
                    "("
                    + effect["condition"]
                    + ") or _environment_profile.environment_mode == 'guardians'"
                )
    first_scene = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "srd_information_scene_entity"
    )
    cast["effects"][first_scene + 1 : first_scene + 1] = [
        _when(
            "_environment_profile.environment_mode == 'guardians'",
            [
                _require("save_dc >= 1", "Spirit Guardians requires the actual caster save DC."),
                _set("_guardian_cast_ethic", "_info_scene_entity.metadata.alignment_ethic"),
                _require(
                    (
                        "_guardian_cast_ethic == 'good' or _guardian_cast_ethic == 'neutral' or"
                        " _guardian_cast_ethic == 'evil'"
                    ),
                    (
                        "Admit the caster's actual good, neutral or evil alignment ethic before"
                        " casting Spirit Guardians."
                    ),
                ),
            ],
        )
    ]
    source_index = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "refresh_srd_environment_scene"
    )
    cast["effects"][source_index:source_index] = [
        _when(
            "_environment_profile.environment_mode == 'guardians'",
            [
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.alignment_ethic",
                    "_guardian_cast_ethic",
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.damage_type", "'radiant'"
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.spirit_appearance",
                    "spirit_appearance",
                ),
                _when(
                    "_guardian_cast_ethic == 'evil'",
                    [
                        _set(
                            "state.persistent_spell_areas.{_environment_cast_id}.damage_type",
                            "'necrotic'",
                        ),
                        _set(
                            "state.persistent_spell_areas.{_environment_cast_id}.spirit_appearance",
                            "'fiendish'",
                        ),
                    ],
                ),
                _call(
                    "select_srd_spirit_guardians_unaffected",
                    {"caster_state": "{_caster_state_id}", "targets": "{unaffected_targets}"},
                ),
                _call("refresh_srd_environment_mode_indices", {}),
            ],
        )
    ]
    qualifier = changed["qualify_srd_environment_emanation_source"]
    for effects in _effect_lists(qualifier["effects"]):
        for effect in effects:
            if "set" in effect and "_emanation_source.mode == 'elemental')" in str(effect):
                effect["set"]["value"] = effect["set"]["value"].replace(
                    "_emanation_source.mode == 'elemental')",
                    (
                        "_emanation_source.mode == 'elemental' or _emanation_source.mode == 'gu"
                        "ardians')"
                    ),
                )
                if len(effect["set"]["value"]) > 500:
                    value = effect["set"]["value"]
                    split = value.index(
                        " and ", value.index("_emanation_source.mode == 'guardians')")
                    )
                    effect["set"]["value"] = value[:split]
                    effects.insert(
                        effects.index(effect) + 1,
                        _set(
                            "_emanation_source_current", "_emanation_source_current" + value[split:]
                        ),
                    )
    index = changed["refresh_srd_environment_mode_indices"]
    index["effects"].insert(0, _set("state.has_spirit_guardians", False))
    area = next(e["for_each"] for e in index["effects"] if "for_each" in e)
    area["do"][-1]["on_true"].append(
        _when("_environment_area.mode == 'guardians'", [_set("state.has_spirit_guardians", True)])
    )
    end = changed["resolve_srd_environment_hazard_end_turn"]
    loop = next(e["for_each"] for e in end["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'guardians'",
            [
                _call(
                    "resolve_srd_spirit_guardians_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{_environment_id}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                ),
            ],
        )
    )
    dispatch = changed["resolve_srd_environment_hazard_event"]
    previous = dispatch["effects"][2:]
    dispatch["effects"][2:] = [
        _when(
            "_hazard_dispatch_source.mode == 'guardians'",
            [
                _call(
                    "resolve_srd_spirit_guardians_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{_environment_area_key}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                ),
            ],
            previous,
        )
    ]
    entry = changed["resolve_srd_environment_hazard_entry"]
    for effects in _effect_lists(entry["effects"]):
        for effect in effects:
            condition = effect.get("condition", "")
            if condition.startswith("_environment_area.mode == 'woodland' or"):
                effect["condition"] += " or _environment_area.mode == 'guardians'"
            elif condition.startswith("_environment_area.mode == 'woodland' and"):
                effect["condition"] = (
                    "("
                    + condition
                    + (
                        ") or (_environment_area.mode == 'guardians' and _environment_area.cast"
                        "er == _actor_state_id)"
                    )
                )
                effect["on_true"].insert(
                    1,
                    _when(
                        "_environment_area.mode == 'guardians'",
                        [
                            _set("_hazard_candidates", []),
                            _set("_guardian_scene_entries", "lasting_state.spatial_scene.entities"),
                            _each(
                                "_guardian_scene_entries",
                                "_guardian_scene_entry",
                                [
                                    _when(
                                        (
                                            "_guardian_scene_entry.active == true and _guardian"
                                            "_scene_entry.metadata.participant_ref != null and "
                                            "_guardian_scene_entry.metadata.is_object != true a"
                                            "nd _guardian_scene_entry.metadata.creature_type !="
                                            " 'spell_force'"
                                        ),
                                        [
                                            _set(
                                                "_guardian_scene_actor",
                                                "_guardian_scene_entry.metadata.participant_ref",
                                            ),
                                            _when(
                                                (
                                                    "_guardian_scene_actor not in _environment_"
                                                    "area.unaffected_actor_ids"
                                                ),
                                                [
                                                    _push(
                                                        "_hazard_candidates",
                                                        "_guardian_scene_actor",
                                                    )
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                )
            elif condition == "_actor_state_id in _environment_area.force_target_ids":
                effect["condition"] = (
                    "("
                    + condition
                    + (
                        ") or (_environment_area.mode == 'guardians' and _actor_state_id not in"
                        " _environment_area.unaffected_actor_ids)"
                    )
                )
    scene = changed["refresh_srd_environment_scene"]
    scene["effects"].append(
        _when(
            "combat.turn_active == true",
            [_call("refresh_srd_movement_budget", {"actor_state": "{combat.active_actor}"})],
        )
    )
    removal = changed["remove_srd_environment_concentration"]["effects"]
    removal.insert(
        len(removal) - 1,
        _when(
            "state.has_spirit_guardians == true and combat.turn_active == true",
            [_call("refresh_srd_movement_budget", {"actor_state": "{combat.active_actor}"})],
        ),
    )
    movement = changed["move_srd_scene_creature_node"]
    for effects in _effect_lists(movement["effects"]):
        for effect in list(effects):
            if effect.get("list_push", {}).get("value") == "'final_endpoint'":
                effects[effects.index(effect) : effects.index(effect)] = [
                    _when(
                        "state.has_spirit_guardians == true",
                        _areas(
                            [
                                _when(
                                    "_environment_area.mode == 'guardians'",
                                    [
                                        _push("_ground_route_ids", "'guardian_entry'"),
                                        _push("_ground_route_ids", "'guardian_exit'"),
                                        _when(
                                            "_environment_area.caster == _actor_state_id",
                                            [
                                                _set(
                                                    "_guardian_route_entries",
                                                    "lasting_state.spatial_scene.entities",
                                                ),
                                                _each(
                                                    "_guardian_route_entries",
                                                    "_guardian_route_entry",
                                                    [
                                                        _when(
                                                            (
                                                                "_guardian_route_entry.active ="
                                                                "= true and _guardian_route_ent"
                                                                "ry.metadata.participant_ref !="
                                                                " null"
                                                            ),
                                                            [
                                                                _push(
                                                                    "_ground_route_ids",
                                                                    "'guardian_contact'",
                                                                )
                                                            ],
                                                        ),
                                                    ],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ]
                        ),
                    )
                ]
            elif effect.get("call") == "resolve_srd_environment_hazard_entry":
                effects[effects.index(effect) + 1 : effects.index(effect) + 1] = [
                    _when(
                        "state.has_spirit_guardians == true",
                        [
                            _call(
                                "resolve_srd_spirit_guardians_speed_boundary",
                                {
                                    "actor_state": "{_actor_state_id}",
                                    "destination": "{destination}",
                                },
                                (
                                    "_guardian_speed_boundary_t",
                                    "_guardian_speed_boundary_destination",
                                ),
                            ),
                            _when(
                                (
                                    "_guardian_speed_boundary_t < _ground_entry_t - 0.000000001"
                                    " and _guardian_speed_boundary_t < _hazard_entry_t - 0.0000"
                                    "00001"
                                ),
                                [
                                    _set("_ground_entry_t", "_guardian_speed_boundary_t"),
                                    _set("_ground_entry_id", "'guardian_speed'"),
                                    _set("_ground_entry_ids", []),
                                    _set(
                                        "_ground_entry_destination",
                                        "_guardian_speed_boundary_destination",
                                    ),
                                ],
                            ),
                        ],
                    )
                ]
            elif effect.get("call") == "move_srd_scene_creature_environment_segment":
                effects[effects.index(effect) + 1 : effects.index(effect) + 1] = [
                    _when(
                        "state.has_spirit_guardians == true and combat.turn_active == true",
                        [
                            _call(
                                "refresh_srd_movement_budget", {"actor_state": "{_actor_state_id}"}
                            )
                        ],
                    )
                ]


def _spike_growth_tools():
    """Measure actual supported ground travel and settle each printed five-foot packet."""
    source = deepcopy(_sphere_geometry_tools()["qualify_srd_environment_sphere_source"])
    for effect in source["effects"]:
        if effect.get("set", {}).get("field") == "_sphere_source_current":
            value = effect["set"]["value"]
            if "_sphere_source.profile" in value:
                effect["set"]["value"] = (
                    "_sphere_source.profile == 'environment' and _sphere_source.mode == 'spikes' "
                    "and _sphere_source.caster == _caster_state_id and _sphere_source.active == "
                    "true "
                    "and lasting_state.lasting_tick < _sphere_source.expires_tick"
                )
    support = deepcopy(_minor_terrain_tools()["qualify_srd_minor_terrain_support"])
    support["description"] = (
        "Reuse actual support rays and box shadows for this saved ground Sphere."
    )
    for effects in _effect_lists(support["effects"]):
        for effect in effects:
            if "require" in effect:
                effect["require"]["error"] = effect["require"]["error"].replace(
                    "Minor Elementals", "Spike Growth"
                )
    # This source is fixed on the admitted support, rather than centered on its caster.
    scene_index = next(
        i
        for i, e in enumerate(support["effects"])
        if e.get("call") == "srd_information_scene_entity"
    )
    support["effects"][scene_index : scene_index + 1] = []
    for effect in support["effects"]:
        field = effect.get("set", {}).get("field")
        if field == "_minor_terrain_plane":
            effect["set"]["value"] = "_minor_terrain_source.plane"
        elif field == "_minor_terrain_bounds":
            effect["set"]["value"] = {
                "min": [
                    f"_minor_terrain_source.origin[{i}] - _minor_terrain_source.radius"
                    for i in range(3)
                ],
                "max": [
                    f"_minor_terrain_source.origin[{i}] + _minor_terrain_source.radius"
                    for i in range(3)
                ],
            }
    support["effects"].append(
        _require(
            "_minor_terrain_support_id == _minor_terrain_source.ground_id and "
            "abs(start[2] - _minor_terrain_source.origin[2]) < 0.000000001 and "
            "abs(end[2] - _minor_terrain_source.origin[2]) < 0.000000001",
            "Spike Growth currently requires its actual single flat admitted support surface.",
        )
    )
    tools = {
        "qualify_srd_spike_growth_source": source,
        "qualify_srd_spike_growth_support": support,
        "resolve_srd_spike_growth_intervals": _environment_tool(
            "Read supported same-plane ground intervals from actual live Spike Growth sources.",
            [_param("actor_subject"), _array("start"), _array("end"), _param("mode")],
            [
                _set("_spike_intervals", []),
                _call(
                    "srd_information_scene_entity", {"subject": "{actor_subject}"}, POSITION_EXPORTS
                ),
                _set("_spike_plane", "_info_scene_entity.metadata.plane"),
                _when(
                    "mode != 'fly' and _info_scene_entity.metadata.flying != true",
                    _areas(
                        [
                            _when(
                                "_environment_area.mode == 'spikes' and _environment_area.plane == "
                                "_spike_plane",
                                [
                                    _call(
                                        "qualify_srd_spike_growth_source",
                                        {
                                            "caster_state": "{_environment_area.caster}",
                                            "area_id": "{_environment_id}",
                                        },
                                        {"_spike_current": "_sphere_source_current"},
                                    ),
                                    _when(
                                        "_spike_current == true",
                                        [
                                            *_origin(),
                                            _interval("start", "end"),
                                            _when(
                                                "_sphere_hit == true and _sphere_exit - "
                                                "_sphere_entry > 0.000000001",
                                                [
                                                    _set(
                                                        "_spike_support_start",
                                                        [
                                                            f"start[{i}] + (end[{i}] - start[{i}]) "
                                                            f"* _sphere_entry"
                                                            for i in range(3)
                                                        ],
                                                    ),
                                                    _set(
                                                        "_spike_support_end",
                                                        [
                                                            f"start[{i}] + (end[{i}] - start[{i}]) "
                                                            f"* _sphere_exit"
                                                            for i in range(3)
                                                        ],
                                                    ),
                                                    _call(
                                                        "qualify_srd_spike_growth_support",
                                                        {
                                                            "area_id": "{_environment_id}",
                                                            "start": "{_spike_support_start}",
                                                            "end": "{_spike_support_end}",
                                                        },
                                                    ),
                                                    _push(
                                                        "_spike_intervals",
                                                        {
                                                            "area_id": "_environment_id",
                                                            "entry": "_sphere_entry",
                                                            "exit": "_sphere_exit",
                                                            "sequence": (
                                                                "_environment_area.sequence"
                                                            ),
                                                            "caster": "_environment_area.caster",
                                                        },
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ]
                    ),
                ),
                _each(
                    "_spike_intervals",
                    "_spike_first",
                    [
                        _each(
                            "_spike_intervals",
                            "_spike_second",
                            [
                                _require(
                                    "_spike_first.area_id == _spike_second.area_id or "
                                    "min(_spike_first.exit, _spike_second.exit) - "
                                    "max(_spike_first.entry, _spike_second.entry) <= 0.000000001",
                                    "Overlapping Spike Growth damage-distance allocation is "
                                    "currently unsupported.",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "resolve_srd_spike_growth_boundary": _environment_tool(
            "Derive the next five-foot damage point from this creature's saved source travel.",
            [_entity("actor_state"), _array("destination"), _param("mode")],
            [
                _set("_spike_boundary_t", 2),
                _set("_spike_boundary_destination", "destination"),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_actor_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_spike_start", "_info_position"),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_spike_start}", "end": "{destination}"},
                    ("_scene_distance",),
                ),
                _set("_spike_route_length", "_scene_distance"),
                _call(
                    "resolve_srd_spike_growth_intervals",
                    {
                        "actor_subject": "{_actor_state_id}",
                        "start": "{_spike_start}",
                        "end": "{destination}",
                        "mode": "{mode}",
                    },
                    ("_spike_intervals",),
                ),
                _each(
                    "_spike_intervals",
                    "_spike_interval",
                    [
                        _set("_spike_remainder", 0),
                        _set(
                            "_spike_receipt",
                            "actor_state.spike_growth_travel[_spike_interval.area_id]",
                        ),
                        _when(
                            "_spike_receipt != null and _spike_receipt.sequence == "
                            "_spike_interval.sequence",
                            [
                                _set("_spike_remainder", "_spike_receipt.remainder"),
                            ],
                        ),
                        _set(
                            "_spike_inside_distance",
                            "_spike_route_length * (_spike_interval.exit - _spike_interval.entry)",
                        ),
                        _when(
                            "_spike_inside_distance + _spike_remainder >= 5 - 0.000000001",
                            [
                                _set(
                                    "_spike_candidate_t",
                                    "_spike_interval.entry + (5 - _spike_remainder) * "
                                    "_spike_route_length ** -1",
                                ),
                                _when(
                                    "_spike_candidate_t < _spike_boundary_t",
                                    [
                                        _set(
                                            "_spike_boundary_t",
                                            "min(_spike_interval.exit, _spike_candidate_t)",
                                        ),
                                        _set(
                                            "_spike_boundary_destination",
                                            [
                                                f"_spike_start[{i}] + (destination[{i}] - "
                                                f"_spike_start[{i}]) * _spike_boundary_t"
                                                for i in range(3)
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "apply_srd_spike_growth_damage": _environment_tool(
            "Roll the printed 2d4 Piercing damage through the sole typed damage lifecycle.",
            [
                _entity("target_state"),
                _param("area_id"),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            [
                _set("_spike_damage_source", "state.persistent_spell_areas[area_id]"),
                _call(
                    "qualify_srd_spike_growth_source",
                    {"caster_state": "{_spike_damage_source.caster}", "area_id": "{area_id}"},
                    {"_spike_current": "_sphere_source_current"},
                ),
                _require(
                    "_spike_current == true", "The exact Spike Growth source must remain current."
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_target_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_spike_damage_point", "_info_position"),
                {"dice": {"expression": "2d4"}, "store_as": "_spike_damage_roll"},
                _call(
                    "resolve_srd_actor_spell_modifiers",
                    {"target_state": "{_spike_damage_source.caster}", "lasting_state": "state"},
                ),
                _call(
                    "modify_srd_actor_raw_damage_roll",
                    {
                        "actor_state": "{_spike_damage_source.caster}",
                        "lasting_state": "state",
                        "amount": "{_spike_damage_roll.total}",
                    },
                    {"_spike_damage_amount": "_ray_raw_damage"},
                ),
                _set("_spike_damage_debt", "max(0, -_spike_damage_amount)"),
                _set("_spike_damage_amount", "max(0, _spike_damage_amount)"),
                _call(
                    "apply_srd_qualifying_damage",
                    {
                        "target": "{_target_state_id}",
                        "amount": "{_spike_damage_amount}",
                        "adjustment": 0,
                        "damage_type": "piercing",
                        "source_state_id": "{_spike_damage_source.caster}",
                        "witness_state_ids": "{witness_state_ids}",
                        "game_damage_roll": True,
                        "unallocated_roll_penalty": "{_spike_damage_debt}",
                        "damage_roll_divisor": 1,
                        "damage_roll_base_remainder": 0,
                    },
                    ("_effective_damage",),
                ),
                _set(
                    "target_state.last_spike_growth_damage",
                    {
                        "area_id": "area_id",
                        "sequence": "_spike_damage_source.sequence",
                        "raw_damage": "_spike_damage_roll",
                        "damage_type": "'piercing'",
                        "applied_damage": "_effective_damage",
                        "point": "_spike_damage_point",
                    },
                ),
            ],
        ),
        "commit_srd_spike_growth_travel": _environment_tool(
            "Retain only committed actual ground distance and settle its next printed packet.",
            [
                _entity("actor_state"),
                _array("start"),
                _array("end"),
                _param("mode"),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            [
                _call(
                    "measure_srd_information_distance",
                    {"start": "{start}", "end": "{end}"},
                    ("_scene_distance",),
                ),
                _set("_spike_committed_distance", "_scene_distance"),
                _call(
                    "resolve_srd_spike_growth_intervals",
                    {
                        "actor_subject": "{_actor_state_id}",
                        "start": "{start}",
                        "end": "{end}",
                        "mode": "{mode}",
                    },
                    ("_spike_intervals",),
                ),
                _when(
                    "actor_state.spike_growth_travel == null",
                    [_set("actor_state.spike_growth_travel", {})],
                ),
                _each(
                    "_spike_intervals",
                    "_spike_interval",
                    [
                        _set("_spike_remainder", 0),
                        _set(
                            "_spike_receipt",
                            "actor_state.spike_growth_travel[_spike_interval.area_id]",
                        ),
                        _when(
                            "_spike_receipt != null and _spike_receipt.sequence == "
                            "_spike_interval.sequence",
                            [_set("_spike_remainder", "_spike_receipt.remainder")],
                        ),
                        _set(
                            "_spike_remainder",
                            "_spike_remainder + _spike_committed_distance * (_spike_interval.exit "
                            "- _spike_interval.entry)",
                        ),
                        _require(
                            "_spike_remainder < 10 - 0.000000001",
                            "Actual Spike Growth movement must commit at each five-foot boundary.",
                        ),
                        _set("_spike_damage_due", "_spike_remainder >= 5 - 0.000000001"),
                        _when(
                            "_spike_damage_due == true",
                            [_set("_spike_remainder", "max(0, _spike_remainder - 5)")],
                        ),
                        _set(
                            "actor_state.spike_growth_travel.{_spike_interval.area_id}",
                            {
                                "sequence": "_spike_interval.sequence",
                                "remainder": "_spike_remainder",
                            },
                        ),
                        _when(
                            "_spike_damage_due == true and actor_state.is_alive == true and "
                            "actor_state.hp > 0",
                            [
                                _call(
                                    "apply_srd_spike_growth_damage",
                                    {
                                        "target_state": "{_actor_state_id}",
                                        "area_id": "{_spike_interval.area_id}",
                                        "witness_state_ids": "{witness_state_ids}",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "record_srd_spike_growth_visibility": _environment_tool(
            "Remember actual admitted creature sight when this camouflaged ground appears.",
            [_param("area_id")],
            [
                _set("_spike_visibility_source", "state.persistent_spell_areas[area_id]"),
                _set("_spike_recognized", []),
                _set("_spike_visibility_entries", "state.spatial_scene.entities"),
                _each(
                    "_spike_visibility_entries",
                    "_spike_observer",
                    [
                        _when(
                            "_spike_observer.active == true and "
                            "_spike_observer.metadata.participant_ref != null and "
                            "_spike_observer.metadata.plane == _spike_visibility_source.plane",
                            [
                                _call(
                                    "resolve_srd_illumination_point_sight",
                                    {
                                        "observer_state": (
                                            "{_spike_observer.metadata.participant_ref}"
                                        ),
                                        "point": "{_spike_visibility_source.origin}",
                                    },
                                    ("_illumination_point_can_see",),
                                ),
                                _when(
                                    "_illumination_point_can_see == true",
                                    [
                                        _push(
                                            "_spike_recognized",
                                            "_spike_observer.metadata.participant_ref",
                                        )
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                _set(
                    "state.persistent_spell_areas.{area_id}.recognized_actor_ids",
                    "_spike_recognized",
                ),
            ],
        ),
        "refresh_srd_environment_mode_indices": _environment_tool(
            "Derive cheap mode flags only from this owner's currently active source rows.",
            [],
            [
                _set("state.has_spike_growth", False),
                _set("state.has_moonbeam", False),
                *_areas(
                    [
                        _when(
                            "_environment_area.mode == 'spikes'",
                            [_set("state.has_spike_growth", True)],
                        ),
                        _when(
                            "_environment_area.mode == 'moonbeam'",
                            [_set("state.has_moonbeam", True)],
                        ),
                    ]
                ),
            ],
        ),
        "search_srd_spike_growth": _public(
            _environment_tool(
                "Spend a Search action and make the actual source DC Wisdom skill check to "
                "recognize hazardous ground.",
                [
                    _entity("actor_state"),
                    _param("area_id"),
                    {**_param("skill"), "type": "enum", "enum_values": ["perception", "survival"]},
                    _optional("actor", "string", None, resolve="participant"),
                ],
                [
                    _set("_spike_search_source", "state.persistent_spell_areas[area_id]"),
                    _require(
                        "_spike_search_source.profile == 'environment' and "
                        "_spike_search_source.mode == 'spikes'",
                        "Select an actual Spike Growth source for this Search action.",
                    ),
                    _call(
                        "qualify_srd_spike_growth_source",
                        {"caster_state": "{_spike_search_source.caster}", "area_id": "{area_id}"},
                        {"_spike_search_current": "_sphere_source_current"},
                    ),
                    _require(
                        "_spike_search_current == true and actor_state.is_alive == true and "
                        "actor_state.hp > 0 and 'incapacitated' not in actor_state.conditions",
                        "Search an actual current Spike Growth source with a creature able to act.",
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_actor_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _require(
                        "_info_scene_entity.metadata.plane == _spike_search_source.plane",
                        "Search requires this actual same-plane terrain.",
                    ),
                    _set("_spike_search_sight", "skill == 'perception'"),
                    _set("_spike_search_proficiency", "'none'"),
                    _when(
                        "actor != null",
                        [
                            _require(
                                "_actor_id == _actor_state_id",
                                "The character data must belong to the searching creature.",
                            ),
                            _when(
                                "skill in actor.data.skill_proficiencies",
                                [_set("_spike_search_proficiency", "'normal'")],
                            ),
                        ],
                        [
                            _require(
                                "actor_state.skill_check_modifiers[skill] != null",
                                "Admit this creature's actual Wisdom skill check modifier before "
                                "Search.",
                            ),
                            _set(
                                "_spike_search_modifier", "actor_state.skill_check_modifiers[skill]"
                            ),
                        ],
                    ),
                    _when(
                        "combat.encounter_active == true",
                        [
                            _require(
                                "combat.turn_active == true and combat.active_actor == "
                                "_actor_state_id",
                                "Only the active creature can spend this Search action.",
                            ),
                            _call("spend_srd_action", {"magic_action": False}),
                            _set("combat.action_taken", "'search'"),
                        ],
                    ),
                    _when(
                        "actor != null",
                        [
                            _call(
                                "d20_test",
                                {
                                    "actor": "{_actor_id}",
                                    "actor_state": "{_actor_state_id}",
                                    "kind": "ability_check",
                                    "ability": "wisdom",
                                    "skill": "{skill}",
                                    "proficiency_factor": "{_spike_search_proficiency}",
                                    "circumstantial_modifier": 0,
                                    "target_number": "{_spike_search_source.save_dc}",
                                    "requires_sight": "{_spike_search_sight}",
                                },
                                ("_d20_test", "_outcome", "_combined_modifier"),
                            ),
                        ],
                        [
                            _call(
                                "d20_test",
                                {
                                    "actor_state": "{_actor_state_id}",
                                    "kind": "ability_check",
                                    "ability": "wisdom",
                                    "skill": "{skill}",
                                    "ability_modifier": "{_spike_search_modifier}",
                                    "proficiency_bonus": 0,
                                    "proficiency_factor": "none",
                                    "circumstantial_modifier": 0,
                                    "target_number": "{_spike_search_source.save_dc}",
                                    "requires_sight": "{_spike_search_sight}",
                                },
                                ("_d20_test", "_outcome", "_combined_modifier"),
                            ),
                        ],
                    ),
                    _set("_spike_search_recognized", "_outcome == 'success'"),
                    _when(
                        "_spike_search_recognized == true",
                        [
                            _set("_spike_recognized", "_spike_search_source.recognized_actor_ids"),
                            _when(
                                "_actor_state_id not in _spike_recognized",
                                [_push("_spike_recognized", "_actor_state_id")],
                            ),
                            _set(
                                "state.persistent_spell_areas.{area_id}.recognized_actor_ids",
                                "_spike_recognized",
                            ),
                        ],
                    ),
                    _set(
                        "actor_state.last_spike_growth_search",
                        {
                            "area_id": "area_id",
                            "sequence": "_spike_search_source.sequence",
                            "skill": "skill",
                            "roll": "_d20_test",
                            "recognized": "_spike_search_recognized",
                        },
                    ),
                ],
                public=True,
            ),
            "exploration",
            "Spike Growth search",
            [("Recognized", "_spike_search_recognized")],
        ),
    }
    return tools


def _bind_spike_growth(changed):
    """Compose five-foot consequences at the existing actual movement segment boundary."""
    cast = changed["cast_spell_environment"]
    index = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "refresh_srd_environment_scene"
    )
    cast["effects"][index:index] = [
        _when(
            "_environment_profile.environment_mode == 'spikes'",
            [
                _require(
                    "save_dc >= 1", "The camouflaged ground requires the actual caster spell DC."
                ),
                _require(
                    " and ".join(
                        f"_environment_cast_origin[{i}] >= state.spatial_scene.map.bounds.min[{i}] "
                        f"and "
                        f"_environment_cast_origin[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                        for i in range(3)
                    ),
                    "The actual Spike Growth source point must be inside the admitted map bounds.",
                ),
                _call(
                    "qualify_srd_environment_ground",
                    {"point": "{_environment_cast_origin}", "plane": "{_environment_plane}"},
                    ("_environment_ground_id",),
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.ground_id",
                    "_environment_ground_id",
                ),
                _set("state.has_spike_growth", True),
                _call("record_srd_spike_growth_visibility", {"area_id": "{_environment_cast_id}"}),
            ],
        ),
        _call("refresh_srd_environment_mode_indices", {}),
    ]
    for name in ("remove_srd_environment_concentration", "refresh_srd_environment_lifecycle"):
        changed[name]["effects"].append(_call("refresh_srd_environment_mode_indices", {}))
    terrain = changed["resolve_srd_environment_sphere_terrain_intervals"]
    terrain["effects"].append(
        _when(
            "ignore_ground_environments != true and state.has_spike_growth == true",
            [
                _call(
                    "resolve_srd_spike_growth_intervals",
                    {
                        "actor_subject": "{actor_subject}",
                        "start": "{start}",
                        "end": "{end}",
                        "mode": "walk",
                    },
                    ("_spike_intervals",),
                ),
                _each(
                    "_spike_intervals",
                    "_spike_interval",
                    [
                        _push(
                            "_sphere_route_intervals",
                            {"entry": "_spike_interval.entry", "exit": "_spike_interval.exit"},
                        )
                    ],
                ),
            ],
        )
    )
    movement = changed["move_srd_scene_creature_node"]
    active = movement["effects"][0]["on_true"]
    loop = next(
        e["for_each"] for e in active if e.get("for_each", {}).get("as") == "_ground_route_id"
    )
    extra = next(
        i for i, e in enumerate(active) if e.get("list_push", {}).get("value") == "'final_endpoint'"
    )
    active[extra:extra] = [
        _when(
            "state.has_spike_growth == true",
            [
                _set("_spike_steps", list(range(64))),
                _each("_spike_steps", "_spike_step", [_push("_ground_route_ids", "'spike_step'")]),
            ],
        )
    ]
    effects = loop["do"][0]["on_true"]
    segment_index = next(
        i
        for i, e in enumerate(effects)
        if e.get("call") == "move_srd_scene_creature_environment_segment"
    )
    effects[segment_index:segment_index] = [
        _when(
            "state.has_spike_growth == true",
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_actor_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_spike_committed_origin", "_info_position"),
                _call(
                    "resolve_srd_spike_growth_boundary",
                    {
                        "actor_state": "{_actor_state_id}",
                        "destination": "{destination}",
                        "mode": "{mode}",
                    },
                    ("_spike_boundary_t", "_spike_boundary_destination"),
                ),
                _when(
                    "_spike_boundary_t < _ground_entry_t - 0.000000001",
                    [
                        _set("_ground_entry_t", "_spike_boundary_t"),
                        _set("_ground_entry_id", "'spike_step'"),
                        _set("_ground_entry_ids", []),
                        _set("_hazard_entry_events", []),
                        _set("_ground_entry_destination", "_spike_boundary_destination"),
                    ],
                ),
            ],
        )
    ]
    segment_index = next(
        i
        for i, e in enumerate(effects)
        if e.get("call") == "move_srd_scene_creature_environment_segment"
    )
    effects.insert(
        segment_index + 1,
        _when(
            "state.has_spike_growth == true and _scene_movement_distance > 0.000000001",
            [
                _call(
                    "commit_srd_spike_growth_travel",
                    {
                        "actor_state": "{_actor_state_id}",
                        "start": "{_spike_committed_origin}",
                        "end": "{_scene_actor_position}",
                        "mode": "{mode}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                ),
            ],
        ),
    )
    active.append(
        _require(
            "_ground_route_continue != true",
            "Spike Growth movement exceeds its supported bounded segment count.",
        )
    )


def _ground_tools():
    """Qualify a saved ground fact through the neutral engine's actual supporting face hit."""
    return {
        "qualify_srd_environment_ground": _environment_tool(
            "Require the chosen point to lie on actual admitted, same-plane supporting ground.",
            [_array("point"), _param("plane")],
            [
                _set("_environment_ground_id", None),
                _set("_environment_ground_above", ["point[0]", "point[1]", "point[2] + 0.000001"]),
                _set("_environment_ground_below", ["point[0]", "point[1]", "point[2] - 0.000001"]),
                {
                    "spatial_query": {
                        "scene": "state.spatial_scene",
                        "operation": "ray",
                        "query": {
                            "map_id": "{state.spatial_scene.map.id}",
                            "origin": "{_environment_ground_above}",
                            "destination": "{_environment_ground_below}",
                            "include": ["geometry"],
                            "max_intersections": 1000,
                        },
                    },
                    "store_as": "_environment_ground_ray",
                },
                _require(
                    "_environment_ground_ray.truncated != true",
                    "Ground support exceeds the supported ray limit.",
                ),
                _set("_environment_ground_hits", "_environment_ground_ray.intersections"),
                _set("_environment_ground_geometry", "state.spatial_scene.geometry"),
                _each(
                    "_environment_ground_hits",
                    "_environment_ground_hit",
                    [
                        _when(
                            "_environment_ground_hit.normal[2] > 0 and "
                            "abs(_environment_ground_hit.point[2] - point[2]) < 0.000000001 and "
                            "_environment_ground_hit.metadata.ground == true and "
                            "_environment_ground_hit.metadata.plane == plane",
                            [
                                _each(
                                    "_environment_ground_geometry",
                                    "_environment_ground",
                                    [
                                        _when(
                                            "_environment_ground.id == "
                                            "_environment_ground_hit.id and "
                                            "_environment_ground.active == true and "
                                            "_environment_ground.movement == 'blocking'",
                                            [
                                                _set(
                                                    "_environment_ground_id",
                                                    "_environment_ground.id",
                                                )
                                            ],
                                        ),
                                    ],
                                )
                            ],
                        ),
                    ],
                ),
                _require(
                    "_environment_ground_id != null",
                    "Choose a point on actual same-plane supporting ground.",
                ),
            ],
        ),
    }


def _emanation_geometry_tools():
    """Use actual box creature spaces and the existing Sphere interval for body Emanations."""
    cuts = []
    affine = []
    for axis in range(3):
        cuts.append(
            _when(
                f"abs(end[{axis}] - start[{axis}]) > 0.000000000001",
                [
                    *[
                        effect
                        for face in ("min", "max")
                        for effect in [
                            _set(
                                "_body_cut",
                                f"(_body_bounds.{face}[{axis}] - start[{axis}]) * "
                                f"(end[{axis}] - start[{axis}]) ** -1",
                            ),
                            _when(
                                "_body_cut > 0 and _body_cut < 1",
                                [
                                    _push("_body_cuts", {"t": "_body_cut"}),
                                ],
                            ),
                        ]
                    ],
                ],
            )
        )
        affine += [
            _set(f"_body_a_{axis}", 0),
            _set(f"_body_b_{axis}", 0),
            _set(
                "_body_mid_coordinate",
                f"start[{axis}] + (end[{axis}] - start[{axis}]) * "
                "(_body_last_t + _body_next.t) * 0.5",
            ),
            *[
                _when(
                    f"_body_mid_coordinate {comparison} _body_bounds.{face}[{axis}]",
                    [
                        _set(
                            f"_body_a_{axis}",
                            f"start[{axis}] + (end[{axis}] - start[{axis}]) * "
                            f"_body_last_t - _body_bounds.{face}[{axis}]",
                        ),
                        _set(
                            f"_body_b_{axis}",
                            f"start[{axis}] + (end[{axis}] - start[{axis}]) * "
                            f"_body_next.t - _body_bounds.{face}[{axis}]",
                        ),
                    ],
                )
                for face, comparison in (("min", "<"), ("max", ">"))
            ],
        ]
    return {
        "resolve_srd_environment_anchor_geometry": _environment_tool(
            "Query the existing exposure owner from the actual scene-only source anchor.",
            [_param("anchor_subject"), _param("target_subject")],
            [
                *_snapshot(),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{anchor_subject}"},
                    POSITION_EXPORTS,
                ),
                _set("_environment_anchor_position", "_info_position"),
                _set("_environment_anchor_plane", "_info_scene_entity.metadata.plane"),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{target_subject}"},
                    POSITION_EXPORTS,
                ),
                _set(
                    "_environment_anchor_same_plane",
                    "_environment_anchor_plane == _info_scene_entity.metadata.plane",
                ),
                _spatial(
                    "exposure",
                    {
                        "map_id": "{state.spatial_scene.map.id}",
                        "observer": {"point": "{_environment_anchor_position}"},
                        "target": {"entity_id": "{_info_scene_entity.id}"},
                        "samples": {"horizontal": 3, "vertical": 3},
                        "include": ["geometry"],
                    },
                    "_environment_anchor_exposure",
                ),
                _set(
                    "_environment_anchor_clear",
                    "_environment_anchor_same_plane == true and _environment_anchor_exposure."
                    "visible_samples > 0",
                ),
            ],
        ),
        "resolve_srd_environment_body_point": _environment_tool(
            "Resolve a point against the actual box origin of a source Emanation.",
            [_param("anchor_subject"), _array("point"), _param("radius", "number", minimum=0)],
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{anchor_subject}"},
                    POSITION_EXPORTS,
                ),
                _require(
                    "_info_scene_entity.footprint.kind == 'box'",
                    "Point membership currently requires the actual admitted box origin.",
                ),
                _set(
                    "_body_point_nearest",
                    [
                        f"min(max(point[{i}], _info_position[{i}] - "
                        f"_info_scene_entity.footprint.half_extents[{i}]), "
                        f"_info_position[{i}] + _info_scene_entity.footprint.half_extents[{i}])"
                        for i in range(2)
                    ]
                    + [
                        "min(max(point[2], _info_position[2]), "
                        "_info_position[2] + _info_scene_entity.height)"
                    ],
                ),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{point}", "end": "{_body_point_nearest}"},
                    ("_scene_distance",),
                ),
                _set("_body_point_inside", "_scene_distance <= radius"),
            ],
        ),
        "resolve_srd_environment_body_segment": _environment_tool(
            "Resolve an actual moving box creature against a fixed creature-space Emanation.",
            [
                _param("moving_subject"),
                _param("fixed_subject"),
                _array("start"),
                _array("end"),
                _param("radius", "number", minimum=0),
                {**_optional("point_space", "boolean", False), "model_hidden": True},
            ],
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{moving_subject}"},
                    POSITION_EXPORTS,
                ),
                _set("_body_moving", "_info_scene_entity"),
                _require(
                    " and ".join(
                        f"abs(start[{i}] - _body_moving.position[{i}]) < 0.000000001"
                        for i in range(3)
                    ),
                    "The segment must start at the actual moving creature.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{fixed_subject}"},
                    POSITION_EXPORTS,
                ),
                _set("_body_fixed", "_info_scene_entity"),
                _require(
                    "_body_moving.footprint.kind == 'box' and _body_fixed.footprint.kind == 'box'",
                    "Creature-space Emanation entry currently requires admitted box footprints.",
                ),
                _set("_body_hit", False),
                _set("_body_entry", 2),
                _set("_body_exit", -1),
                _set("_body_bounds", {}),
                _when(
                    "_body_moving.metadata.plane == _body_fixed.metadata.plane",
                    [
                        _set(
                            "_body_bounds",
                            {
                                "min": [
                                    f"_body_fixed.position[{i}] - "
                                    f"_body_fixed.footprint.half_extents[{i}] - "
                                    f"_body_moving.footprint.half_extents[{i}]"
                                    for i in range(2)
                                ]
                                + ["_body_fixed.position[2] - _body_moving.height"],
                                "max": [
                                    f"_body_fixed.position[{i}] + "
                                    f"_body_fixed.footprint.half_extents[{i}] + "
                                    f"_body_moving.footprint.half_extents[{i}]"
                                    for i in range(2)
                                ]
                                + ["_body_fixed.position[2] + _body_fixed.height"],
                            },
                        ),
                        _when(
                            "point_space == true",
                            [
                                _set(
                                    "_body_bounds",
                                    {
                                        "min": [
                                            f"_body_fixed.position[{i}] - "
                                            f"_body_fixed.footprint.half_extents[{i}]"
                                            for i in range(2)
                                        ]
                                        + ["_body_fixed.position[2]"],
                                        "max": [
                                            f"_body_fixed.position[{i}] + "
                                            f"_body_fixed.footprint.half_extents[{i}]"
                                            for i in range(2)
                                        ]
                                        + ["_body_fixed.position[2] + _body_fixed.height"],
                                    },
                                ),
                            ],
                        ),
                        _set("_body_cuts", [{"t": 0}, {"t": 1}]),
                        *cuts,
                        {"sort": {"collection": "_body_cuts", "by": "t", "direction": "asc"}},
                        _set("_body_last_t", 0),
                        _each(
                            "_body_cuts",
                            "_body_next",
                            [
                                _when(
                                    "_body_next.t > _body_last_t",
                                    [
                                        *affine,
                                        _set(
                                            "_body_affine_start", [f"_body_a_{i}" for i in range(3)]
                                        ),
                                        _set(
                                            "_body_affine_end", [f"_body_b_{i}" for i in range(3)]
                                        ),
                                        _set("_body_zero", [0, 0, 0]),
                                        _call(
                                            "resolve_srd_persistent_area_segment",
                                            {
                                                "start": "{_body_affine_start}",
                                                "end": "{_body_affine_end}",
                                                "origin": "{_body_zero}",
                                                "radius": "{radius}",
                                            },
                                            ("_sphere_hit", "_sphere_entry", "_sphere_exit"),
                                        ),
                                        _when(
                                            "_sphere_hit != true and "
                                            + " and ".join(
                                                f"abs(end[{i}] - start[{i}]) < 0.000000000001"
                                                for i in range(3)
                                            )
                                            + " and _body_a_0 ** 2 + _body_a_1 ** 2 + "
                                            "_body_a_2 ** 2 <= radius ** 2 + 0.000000001",
                                            [
                                                _set("_sphere_hit", True),
                                                _set("_sphere_entry", 0),
                                                _set("_sphere_exit", 1),
                                            ],
                                        ),
                                        _when(
                                            "_sphere_hit == true",
                                            [
                                                _set("_body_hit", True),
                                                _set(
                                                    "_body_entry",
                                                    "min(_body_entry, _body_last_t + "
                                                    "(_body_next.t - _body_last_t) * "
                                                    "_sphere_entry)",
                                                ),
                                                _set(
                                                    "_body_exit",
                                                    "max(_body_exit, _body_last_t + "
                                                    "(_body_next.t - _body_last_t) * _sphere_exit)",
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                                _set("_body_last_t", "_body_next.t"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "qualify_srd_environment_emanation_source": _environment_tool(
            "Read the actual active caster-owned Emanation and current concentration.",
            [_entity("caster_state"), _param("area_id")],
            [
                _set("_emanation_source", "state.persistent_spell_areas[area_id]"),
                _set(
                    "_emanation_source_current",
                    "_emanation_source.profile == 'environment' and "
                    "(_emanation_source.mode == 'woodland' or "
                    "_emanation_source.mode == 'minor_elementals' or "
                    "_emanation_source.mode == 'animals' or "
                    "_emanation_source.mode == 'elemental') and "
                    "_emanation_source.caster == _caster_state_id and "
                    "_emanation_source.active == true and "
                    "state.lasting_tick < _emanation_source.expires_tick",
                ),
                _set(
                    "_emanation_source_current",
                    "_emanation_source_current == true and "
                    "caster_state.concentration_spell_id == _emanation_source.spell and "
                    "caster_state.concentration_effect_family == 'spell_environment' and "
                    "caster_state.environment_area_id == area_id",
                ),
            ],
        ),
        "qualify_srd_environment_emanation_target": _environment_tool(
            "Qualify actual active caster-space Emanation membership without a position alias.",
            [_entity("caster_state"), _entity("target_state"), _param("area_id")],
            [
                _set("_emanation_source", "state.persistent_spell_areas[area_id]"),
                _set("_emanation_inside", False),
                _call(
                    "qualify_srd_environment_emanation_source",
                    {"caster_state": "{_caster_state_id}", "area_id": "{area_id}"},
                    ("_emanation_source_current",),
                ),
                _when(
                    "_emanation_source_current == true and target_state.is_alive == true "
                    "and (_target_state_id != _emanation_source.anchor_id or "
                    "_emanation_source.include_origin == true)",
                    [
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_target_state_id}"},
                            POSITION_EXPORTS,
                        ),
                        _set("_emanation_target_position", "_info_position"),
                        _call(
                            "resolve_srd_environment_body_segment",
                            {
                                "moving_subject": "{_target_state_id}",
                                "fixed_subject": "{_emanation_source.anchor_id}",
                                "start": "{_emanation_target_position}",
                                "end": "{_emanation_target_position}",
                                "radius": "{_emanation_source.radius}",
                            },
                            ("_body_hit",),
                        ),
                        _when(
                            "_body_hit == true",
                            [
                                _call(
                                    "resolve_srd_environment_anchor_geometry",
                                    {
                                        "anchor_subject": "{_emanation_source.anchor_id}",
                                        "target_subject": "{_target_state_id}",
                                    },
                                    ("_environment_anchor_clear",),
                                ),
                                _set("_emanation_inside", "_environment_anchor_clear == true"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }


def _conjure_tools():
    """Own optional area forcing, per-turn saves and the caster's granted Bonus Action."""
    return {
        "qualify_srd_woodland_effect": _environment_tool(
            "Apply the strongest overlapping selected Woodland source, then the latest tie.",
            [_entity("target_state"), _param("area_id")],
            [
                _set("_woodland_selected_source", "state.persistent_spell_areas[area_id]"),
                _set("_woodland_effect_applies", True),
                *_areas(
                    [
                        _when(
                            "_environment_id != area_id and _environment_area.spell == "
                            "_woodland_selected_source.spell "
                            "and _target_state_id in _environment_area.force_target_ids",
                            [
                                _set(
                                    "_woodland_other_stronger",
                                    "_environment_area.spell_level > "
                                    "_woodland_selected_source.spell_level",
                                ),
                                _when(
                                    "_environment_area.spell_level == "
                                    "_woodland_selected_source.spell_level",
                                    [
                                        _set(
                                            "_woodland_other_stronger",
                                            "_environment_area.save_dc > "
                                            "_woodland_selected_source.save_dc or "
                                            "(_environment_area.save_dc == "
                                            "_woodland_selected_source.save_dc and "
                                            "_environment_area.sequence > "
                                            "_woodland_selected_source.sequence)",
                                        ),
                                    ],
                                ),
                                _when(
                                    "_woodland_other_stronger == true",
                                    [
                                        _call(
                                            "qualify_srd_environment_emanation_target",
                                            {
                                                "caster_state": "{_environment_area.caster}",
                                                "target_state": "{_target_state_id}",
                                                "area_id": "{_environment_id}",
                                            },
                                            ("_emanation_inside",),
                                        ),
                                        _when(
                                            "_emanation_inside == true",
                                            [
                                                _call(
                                                    "resolve_srd_scene_relation",
                                                    {
                                                        "actor_state": "{_environment_area.caster}",
                                                        "target_state": "{_target_state_id}",
                                                    },
                                                    ("_scene_can_see",),
                                                ),
                                                _when(
                                                    "_scene_can_see == true",
                                                    [_set("_woodland_effect_applies", False)],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "select_srd_environment_targets": _public(
            _environment_tool(
                "Select actual creatures to force at subsequent eligible Conjure events.",
                [
                    _entity("caster_state"),
                    _param("targets", "array", items={"type": "string"}, max_items=64),
                ],
                [
                    _set("_selected_source_id", "caster_state.environment_area_id"),
                    _set("_selected_source", "state.persistent_spell_areas[_selected_source_id]"),
                    _require(
                        "_selected_source.active == true and (_selected_source.mode == 'woodland' "
                        "or _selected_source.mode == 'animals' or "
                        "_selected_source.mode == 'elemental') "
                        "and caster_state.concentration_spell_id == _selected_source.spell "
                        "and caster_state.concentration_effect_family == 'spell_environment' "
                        "and state.lasting_tick < _selected_source.expires_tick",
                        "Select an actual active caster-owned optional Conjure area source.",
                    ),
                    _set("_selected_targets", []),
                    _each(
                        "targets",
                        "_selected_target",
                        [
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_selected_target}"},
                                POSITION_EXPORTS,
                            ),
                            _require(
                                "_info_scene_entity.metadata.participant_ref != null",
                                "Select actual admitted creatures for optional forcing.",
                            ),
                            _set(
                                "_selected_creature_id",
                                "_info_scene_entity.metadata.participant_ref",
                            ),
                            _when(
                                "_selected_creature_id not in _selected_targets",
                                [
                                    _push("_selected_targets", "_selected_creature_id"),
                                ],
                            ),
                        ],
                    ),
                    _set(
                        "state.persistent_spell_areas.{_selected_source_id}.force_target_ids",
                        "_selected_targets",
                    ),
                ],
                public=True,
            ),
            "exploration",
            "Conjure target choices",
            [("Creatures", "_selected_targets")],
        ),
        "resolve_srd_woodland_event": _environment_tool(
            "Resolve one actual visible selected creature's eligible Woodland entry or turn end.",
            [
                _entity("target_state"),
                _param("area_id"),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            [
                _set("_woodland_source", "state.persistent_spell_areas[area_id]"),
                {
                    "table_lookup": {
                        "table": "definitions.spells",
                        "key": "_woodland_source.spell",
                        "required": True,
                    },
                    "store_as": "_conjure_profile",
                },
                _set("_woodland_triggered", False),
                _set("_woodland_clock", "'exploration'"),
                _set("_woodland_turn", "state.lasting_tick"),
                _when(
                    "combat.turn_active == true",
                    [
                        _set("_woodland_clock", "'combat'"),
                        _set("_woodland_turn", "combat.turn_sequence"),
                    ],
                ),
                _set("_woodland_prior_targets", []),
                _when(
                    "_woodland_source.event_clock == _woodland_clock and "
                    "_woodland_source.event_turn == _woodland_turn",
                    [
                        _set("_woodland_prior_targets", "_woodland_source.event_target_ids"),
                    ],
                ),
                _when(
                    "(_woodland_source.mode == 'woodland' or _woodland_source.mode == 'animal"
                    "s') and "
                    "_target_state_id in _woodland_source.force_target_ids and "
                    "_target_state_id not in _woodland_prior_targets",
                    [
                        _call(
                            "qualify_srd_environment_emanation_target",
                            {
                                "caster_state": "{_woodland_source.caster}",
                                "target_state": "{_target_state_id}",
                                "area_id": "{area_id}",
                            },
                            ("_emanation_inside",),
                        ),
                        _when(
                            "_emanation_inside == true",
                            [
                                _call(
                                    "resolve_srd_scene_relation",
                                    {
                                        "actor_state": "{_woodland_source.caster}",
                                        "target_state": "{_target_state_id}",
                                    },
                                    ("_scene_can_see",),
                                ),
                                _set("_woodland_source_can_see", "_scene_can_see"),
                                _call(
                                    "qualify_srd_woodland_effect",
                                    {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                                    ("_woodland_effect_applies",),
                                ),
                                _when(
                                    "_woodland_source_can_see == true and "
                                    "_woodland_effect_applies == true",
                                    [
                                        _require(
                                            "target_state.saving_throw_modifiers["
                                            "_conjure_profile.save_ability] != null",
                                            "Use the creature's actual printed save modifier.",
                                        ),
                                        _set(
                                            "_conjure_save_modifier",
                                            "target_state.saving_throw_modifiers["
                                            "_conjure_profile.save_ability]",
                                        ),
                                        _call(
                                            "d20_test",
                                            {
                                                "actor_state": "{_target_state_id}",
                                                "kind": "saving_throw",
                                                "ability": "{_conjure_profile.save_ability}",
                                                "ability_modifier": "{_conjure_save_modifier}",
                                                "proficiency_bonus": 0,
                                                "proficiency_factor": "none",
                                                "circumstantial_modifier": 0,
                                                "target_number": "{_woodland_source.save_dc}",
                                                "advantage": False,
                                                "disadvantage": False,
                                            },
                                            ("_d20_test", "_outcome"),
                                        ),
                                        _set(
                                            "_woodland_dice",
                                            "_conjure_profile.damage_dice + max(0, "
                                            "_woodland_source.spell_level - "
                                            "_conjure_profile.base_level)",
                                        ),
                                        _call(
                                            "format_srd_information_projection",
                                            {
                                                "message": (
                                                    "{_woodland_dice}{_conjure_profile.damage_die}"
                                                )
                                            },
                                            {"_woodland_expression": "_info_projection_message"},
                                        ),
                                        {
                                            "dice": {"expression": "{_woodland_expression}"},
                                            "store_as": "_woodland_damage_roll",
                                        },
                                        _set("_woodland_amount", "_woodland_damage_roll.total"),
                                        _call(
                                            "resolve_srd_actor_spell_modifiers",
                                            {
                                                "target_state": "{_woodland_source.caster}",
                                                "lasting_state": "state",
                                            },
                                        ),
                                        _call(
                                            "modify_srd_actor_raw_damage_roll",
                                            {
                                                "actor_state": "{_woodland_source.caster}",
                                                "lasting_state": "state",
                                                "amount": "{_woodland_amount}",
                                            },
                                            {"_woodland_amount": "_ray_raw_damage"},
                                        ),
                                        _set(
                                            "_woodland_unallocated_penalty",
                                            "max(0, -_woodland_amount)",
                                        ),
                                        _set("_woodland_amount", "max(0, _woodland_amount)"),
                                        _set("_woodland_roll_divisor", 1),
                                        _when(
                                            "_outcome == 'success'",
                                            [
                                                _set("_woodland_roll_divisor", 2),
                                            ],
                                        ),
                                        _set(
                                            "_woodland_roll_remainder",
                                            "_woodland_amount % _woodland_roll_divisor",
                                        ),
                                        _set(
                                            "_woodland_amount",
                                            "_woodland_amount // _woodland_roll_divisor",
                                        ),
                                        _push("_woodland_prior_targets", "_target_state_id"),
                                        _set(
                                            "state.persistent_spell_areas.{area_id}.event_clock",
                                            "_woodland_clock",
                                        ),
                                        _set(
                                            "state.persistent_spell_areas.{area_id}.event_turn",
                                            "_woodland_turn",
                                        ),
                                        _set(
                                            "state.persistent_spell_areas.{area_id}.event_tar"
                                            "get_ids",
                                            "_woodland_prior_targets",
                                        ),
                                        _call(
                                            "apply_srd_qualifying_damage",
                                            {
                                                "target": "{_target_state_id}",
                                                "amount": "{_woodland_amount}",
                                                "adjustment": 0,
                                                "damage_type": "{_conjure_profile.damage_type}",
                                                "source_state_id": "{_woodland_source.caster}",
                                                "witness_state_ids": "{witness_state_ids}",
                                                "unallocated_roll_penalty": (
                                                    "{_woodland_unallocated_penalty}"
                                                ),
                                                "game_damage_roll": True,
                                                "damage_roll_divisor": "{_woodland_roll_divisor}",
                                                "damage_roll_base_remainder": (
                                                    "{_woodland_roll_remainder}"
                                                ),
                                            },
                                            ("_effective_damage",),
                                        ),
                                        _set("_woodland_triggered", True),
                                        _set(
                                            "target_state.last_woodland_save",
                                            {
                                                "area_id": "area_id",
                                                "turn": "_woodland_turn",
                                                "clock": "_woodland_clock",
                                                "roll": "_d20_test",
                                                "outcome": "_outcome",
                                                "raw_damage": "_woodland_damage_roll",
                                                "applied_damage": "_effective_damage",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "disengage_srd_conjure_woodland": _public(
            _environment_tool(
                "Take the actual Woodland source's Disengage option using its Bonus Action budget.",
                [_entity("actor_state")],
                [
                    _set(
                        "_woodland_bonus_source",
                        "state.persistent_spell_areas[actor_state.environment_area_id]",
                    ),
                    _require(
                        "_woodland_bonus_source.active == true and "
                        "_woodland_bonus_source.mode == 'woodland' "
                        "and state.lasting_tick < _woodland_bonus_source.expires_tick and "
                        "actor_state.concentration_spell_id == 'conjure_woodland_beings' and "
                        "actor_state.concentration_effect_family == 'spell_environment' and "
                        "actor_state.is_alive == true and "
                        "actor_state.hp > 0 and actor_state.control_confusion_active != true and "
                        "actor_state.control_order_blocks_bonus != true",
                        "Use an actual active Woodland source and a creature able to act.",
                    ),
                    _require(
                        "'incapacitated' not in actor_state.conditions and "
                        "'unconscious' not in actor_state.conditions and "
                        "'paralyzed' not in actor_state.conditions and "
                        "'petrified' not in actor_state.conditions and "
                        "'stunned' not in actor_state.conditions",
                        "The creature must be able to take the granted Bonus Action.",
                    ),
                    _call(
                        "consume_srd_bonus_action",
                        {
                            "actor_state": "{_actor_state_id}",
                            "bonus_action": "conjure_woodland_disengage",
                        },
                    ),
                    _set("combat.disengaged_actor", "_actor_state_id"),
                ],
                public=True,
            ),
            "combat",
            "Woodland Disengage",
            [("Creature", "_actor_state_id")],
        ),
        "roll_srd_conjure_minor_damage_component": _environment_tool(
            "Roll one source-qualified typed Minor Elementals component for an actual hit owner.",
            [
                _entity("caster_state"),
                _entity("target_state"),
                _optional("critical_hit", "boolean", False),
                _optional(
                    "damage_type", "enum", "fire", enum_values=["acid", "cold", "fire", "lightning"]
                ),
            ],
            [
                _set("_conjure_minor_qualifies", False),
                _set("_conjure_minor_amount", 0),
                _set("_conjure_minor_type", "damage_type"),
                _set("_conjure_minor_roll", None),
                _when(
                    "caster_state.environment_area_id != null",
                    [
                        _set("_minor_area_id", "caster_state.environment_area_id"),
                        _set("_minor_source", "state.persistent_spell_areas[_minor_area_id]"),
                        _when(
                            "_minor_source.mode == 'minor_elementals' and "
                            "_minor_source.spell == 'conjure_minor_elementals'",
                            [
                                _call(
                                    "qualify_srd_environment_emanation_target",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "target_state": "{_target_state_id}",
                                        "area_id": "{_minor_area_id}",
                                    },
                                    ("_emanation_inside",),
                                ),
                                _when(
                                    "_emanation_inside == true",
                                    [
                                        _require(
                                            "_minor_source.spell_level >= 4 and "
                                            "_minor_source.spell_level <= 9",
                                            "Minor Elementals needs its actual valid cast slot.",
                                        ),
                                        _set(
                                            "_conjure_minor_count",
                                            "2 + _minor_source.spell_level - 4",
                                        ),
                                        _call(
                                            "format_srd_information_projection",
                                            {"message": "{_conjure_minor_count}d8"},
                                            {
                                                "_conjure_minor_expression": (
                                                    "_info_projection_message"
                                                )
                                            },
                                        ),
                                        {
                                            "dice": {
                                                "expression": "{_conjure_minor_expression}",
                                                "critical": "critical_hit",
                                            },
                                            "store_as": "_conjure_minor_roll",
                                        },
                                        _set("_conjure_minor_amount", "_conjure_minor_roll.total"),
                                        _set("_conjure_minor_qualifies", True),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }


def _minor_terrain_tools():
    """Contribute enemy ground intervals to the existing movement-cost union."""
    return {
        "qualify_srd_minor_terrain_support": _environment_tool(
            "Require one actual box support surface and reject unresolved ground shadows.",
            [_param("area_id"), _array("start"), _array("end")],
            [
                _set("_minor_terrain_source", "state.persistent_spell_areas[area_id]"),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_minor_terrain_source.caster}"},
                    POSITION_EXPORTS,
                ),
                _set("_minor_terrain_plane", "_info_scene_entity.metadata.plane"),
                _set(
                    "_minor_terrain_bounds",
                    {
                        "min": [
                            f"_info_position[{i}] - _info_scene_entity.footprint.half_extents[{i}] "
                            "- _minor_terrain_source.radius"
                            for i in range(2)
                        ]
                        + ["_info_position[2] - _minor_terrain_source.radius"],
                        "max": [
                            f"_info_position[{i}] + _info_scene_entity.footprint.half_extents[{i}] "
                            "+ _minor_terrain_source.radius"
                            for i in range(2)
                        ]
                        + [
                            "_info_position[2] + _info_scene_entity.height + "
                            "_minor_terrain_source.radius"
                        ],
                    },
                ),
                _call(
                    "qualify_srd_environment_ground",
                    {"point": "{start}", "plane": "{_minor_terrain_plane}"},
                    {"_minor_terrain_support_id": "_environment_ground_id"},
                ),
                _call(
                    "qualify_srd_environment_ground",
                    {"point": "{end}", "plane": "{_minor_terrain_plane}"},
                    ("_environment_ground_id",),
                ),
                _require(
                    "_minor_terrain_support_id == _environment_ground_id",
                    "Minor Elementals ground costs currently require one admitted support surface.",
                ),
                _set("_minor_terrain_geometry", "state.spatial_scene.geometry"),
                _each(
                    "_minor_terrain_geometry",
                    "_minor_terrain_solid",
                    [
                        _when(
                            "_minor_terrain_solid.active == true and "
                            "_minor_terrain_solid.movement == 'blocking' and "
                            "(_minor_terrain_solid.metadata.plane == _minor_terrain_plane or "
                            "(_minor_terrain_solid.metadata.plane == null and "
                            "_minor_terrain_plane == 'material'))",
                            [
                                _require(
                                    "_minor_terrain_solid.volume.kind == 'box'",
                                    "Minor Elementals ground support requires admitted box solids.",
                                ),
                                _when(
                                    "_minor_terrain_solid.id != _minor_terrain_support_id and "
                                    + " and ".join(
                                        f"_minor_terrain_solid.volume.min[{i}] < "
                                        f"_minor_terrain_bounds.max[{i}] and "
                                        f"_minor_terrain_solid.volume.max[{i}] > "
                                        f"_minor_terrain_bounds.min[{i}]"
                                        for i in range(3)
                                    ),
                                    [
                                        _require(
                                            "false",
                                            "Exact Minor Elementals ground shadows from solids "
                                            "are currently unsupported.",
                                        )
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "resolve_srd_minor_terrain_intervals": _environment_tool(
            "Derive enemy-only difficult ground from active actual Minor Emanations.",
            [_param("actor_subject"), _array("start"), _array("end")],
            [
                _set("_minor_route_intervals", []),
                *_areas(
                    [
                        _when(
                            "_environment_area.mode == 'minor_elementals' and "
                            "("
                            + " or ".join(
                                f"abs(end[{i}] - start[{i}]) > 0.000000001" for i in range(3)
                            )
                            + ")",
                            [
                                _call(
                                    "qualify_srd_environment_emanation_source",
                                    {
                                        "caster_state": "{_environment_area.caster}",
                                        "area_id": "{_environment_id}",
                                    },
                                    ("_emanation_source_current",),
                                ),
                                _call(
                                    "qualify_srd_scene_enemy",
                                    {
                                        "caster_id": "{_environment_area.caster}",
                                        "target_id": "{actor_subject}",
                                    },
                                    ("_scene_is_enemy",),
                                ),
                                _when(
                                    "_emanation_source_current == true and _scene_is_enemy == true",
                                    [
                                        _call(
                                            "resolve_srd_environment_body_segment",
                                            {
                                                "moving_subject": "{actor_subject}",
                                                "fixed_subject": "{_environment_area.caster}",
                                                "start": "{start}",
                                                "end": "{end}",
                                                "radius": "{_environment_area.radius}",
                                                "point_space": True,
                                            },
                                            ("_body_hit", "_body_entry", "_body_exit"),
                                        ),
                                        _when(
                                            "_body_hit == true and _body_exit > _body_entry",
                                            [
                                                _call(
                                                    "qualify_srd_minor_terrain_support",
                                                    {
                                                        "area_id": "{_environment_id}",
                                                        "start": "{start}",
                                                        "end": "{end}",
                                                    },
                                                ),
                                                _push(
                                                    "_minor_route_intervals",
                                                    {
                                                        "entry": "_body_entry",
                                                        "exit": "_body_exit",
                                                        "area_id": "_environment_id",
                                                    },
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ]
                ),
            ],
        ),
    }


def _animals_tools():
    """Read the actual pack for its nearby save grant and exact scene retirement."""
    return {
        **_animals_movement_tools(),
        "qualify_srd_conjure_animals_strength": _environment_tool(
            "Derive the caster's Strength save advantage from its active actual pack space.",
            [_entity("actor_state")],
            [
                _set("_conjure_animals_strength_advantage", False),
                _when(
                    "actor_state.environment_area_id != null",
                    [
                        _set("_animals_source_id", "actor_state.environment_area_id"),
                        _set(
                            "_animals_source",
                            "state.persistent_spell_areas[_animals_source_id]",
                        ),
                        _when(
                            "_animals_source.mode == 'animals'",
                            [
                                _call(
                                    "qualify_srd_environment_emanation_source",
                                    {
                                        "caster_state": "{_actor_state_id}",
                                        "area_id": "{_animals_source_id}",
                                    },
                                    ("_emanation_source_current",),
                                ),
                                _when(
                                    "_emanation_source_current == true",
                                    [
                                        _call(
                                            "srd_information_scene_entity",
                                            {"subject": "{_actor_state_id}"},
                                            POSITION_EXPORTS,
                                        ),
                                        _call(
                                            "resolve_srd_environment_body_segment",
                                            {
                                                "moving_subject": "{_actor_state_id}",
                                                "fixed_subject": "{_animals_source.anchor_id}",
                                                "start": "{_info_position}",
                                                "end": "{_info_position}",
                                                "radius": 5,
                                            },
                                            ("_body_hit",),
                                        ),
                                        _set("_conjure_animals_strength_advantage", "_body_hit"),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "retire_srd_environment_pack_scene": _environment_tool(
            "Retire only this environment source's exact actual intangible pack proxy.",
            [_param("area_id")],
            [
                _set("_pack_ending_source", "state.persistent_spell_areas[area_id]"),
                _when(
                    "_pack_ending_source.mode == 'animals' or "
                    "_pack_ending_source.mode == 'elemental'",
                    [
                        _set("_pack_ending_entries", "state.spatial_scene.entities"),
                        _each(
                            "_pack_ending_entries",
                            "_pack_ending_entry",
                            [
                                _when(
                                    "_pack_ending_entry.id == _pack_ending_source.anchor_id and "
                                    "_pack_ending_entry.active == true and "
                                    "_pack_ending_entry.metadata.source_environment_id == "
                                    "area_id and "
                                    "_pack_ending_entry.metadata.source_caster == "
                                    "_pack_ending_source.caster and "
                                    "_pack_ending_entry.metadata.source_ref == "
                                    "_pack_ending_source.spell",
                                    [
                                        _set(
                                            "_pack_scene_revision",
                                            "state.spatial_scene.map.revision",
                                        ),
                                        {
                                            "spatial_mutation": {
                                                "scene": "state.spatial_scene",
                                                "map_id": "{state.spatial_scene.map.id}",
                                                "expected_revision": "{_pack_scene_revision}",
                                                "target": {
                                                    "kind": "entity",
                                                    "id": "{_pack_ending_entry.id}",
                                                },
                                                "patch": {"active": False},
                                            },
                                            "store_as": "_pack_ending_mutation",
                                        },
                                        *_snapshot(),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }


def _animals_movement_tools():
    """An actual caster movement grants one immediate optional intangible pack movement."""
    clock = [
        _set("_pack_clock", "'exploration'"),
        _set("_pack_turn", "state.lasting_tick"),
        _when(
            "combat.encounter_active == true",
            [
                _require(
                    "combat.turn_active == true and combat.active_actor == _actor_state_id",
                    "The pack can move only after its caster moves on the caster's actual turn.",
                ),
                _set("_pack_clock", "'combat'"),
                _set("_pack_turn", "combat.turn_sequence"),
            ],
        ),
    ]
    source = [
        _set("_pack_source_id", "actor_state.environment_area_id"),
        _set("_pack_source", "state.persistent_spell_areas[_pack_source_id]"),
        _require("_pack_source.mode == 'animals'", "Use the caster's actual Animals source."),
        _call(
            "qualify_srd_environment_emanation_source",
            {"caster_state": "{_actor_state_id}", "area_id": "{_pack_source_id}"},
            ("_emanation_source_current",),
        ),
        _require("_emanation_source_current == true", "The actual pack source must be current."),
    ]
    choose_entry = [
        _when(
            "_pack_candidate not in visited_targets",
            [
                _call(
                    "resolve_srd_scene_relation",
                    {"actor_state": "{_actor_state_id}", "target_state": "{_pack_candidate}"},
                    ("_scene_can_see",),
                ),
                _when(
                    "_scene_can_see == true",
                    [
                        _call(
                            "resolve_srd_environment_body_segment",
                            {
                                "moving_subject": "{_pack_source.anchor_id}",
                                "fixed_subject": "{_pack_candidate}",
                                "start": "{_pack_start}",
                                "end": "{destination}",
                                "radius": 10,
                            },
                            ("_body_hit", "_body_entry", "_body_exit"),
                        ),
                        _when(
                            "_body_hit == true and _body_exit > 0.000000000001",
                            [
                                _set("_pack_candidate_t", "max(0, _body_entry)"),
                                _when(
                                    "_pack_candidate_t <= 0.000000000001",
                                    [
                                        _set(
                                            "_pack_candidate_t",
                                            "min(_body_exit * 0.5, "
                                            "0.000001 * _pack_route_distance ** -1)",
                                        ),
                                    ],
                                ),
                                _when(
                                    "_pack_candidate_t < _pack_entry_t - 0.000000000001",
                                    [
                                        _set("_pack_entry_t", "_pack_candidate_t"),
                                        _set("_pack_entry_targets", []),
                                    ],
                                ),
                                _when(
                                    "abs(_pack_candidate_t - _pack_entry_t) < 0.000000000001",
                                    [_push("_pack_entry_targets", "_pack_candidate")],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]
    return {
        "record_srd_environment_caster_movement": _environment_tool(
            "Record the active pack caster's actual committed ordinary movement continuation.",
            [_entity("actor_state")],
            [
                _when(
                    "actor_state.environment_area_id != null and "
                    "actor_state.concentration_spell_id == 'conjure_animals' and "
                    "(combat.encounter_active != true or (combat.turn_active == true and "
                    "combat.active_actor == _actor_state_id))",
                    [
                        *deepcopy(source),
                        *deepcopy(clock),
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_actor_state_id}"},
                            POSITION_EXPORTS,
                        ),
                        _set("_pack_receipt_serial", "_pack_source.pack_movement_serial + 1"),
                        _set(
                            "_pack_receipt",
                            {
                                "source_id": "_pack_source_id",
                                "caster": "_actor_state_id",
                                "serial": "_pack_receipt_serial",
                                "clock": "_pack_clock",
                                "turn": "_pack_turn",
                                "map_id": "state.spatial_scene.map.id",
                                "revision": "state.spatial_scene.map.revision",
                                "position": "_info_position",
                            },
                        ),
                        _set(
                            "state.persistent_spell_areas.{_pack_source_id}.pack_movement_serial",
                            "_pack_receipt_serial",
                        ),
                        _set(
                            "state.persistent_spell_areas.{_pack_source_id}.pack_movement_receipt",
                            "_pack_receipt",
                        ),
                    ],
                ),
            ],
        ),
        "resolve_srd_animals_pack_entry": _environment_tool(
            "Derive visible selected creature events from the actual moving pack body.",
            [
                _entity("actor_state"),
                _array("destination"),
                _param("visited_targets", "array", items={"type": "string"}),
            ],
            [
                *deepcopy(source),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_pack_source.anchor_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_pack_start", "_info_position"),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_pack_start}", "end": "{destination}"},
                    {"_pack_route_distance": "_scene_distance"},
                ),
                _set("_pack_entry_t", 2),
                _set("_pack_entry_targets", []),
                _set("_pack_entry_destination", "destination"),
                _when(
                    "_pack_route_distance > 0.000000001",
                    [
                        _set("_pack_candidates", "_pack_source.force_target_ids"),
                        _each("_pack_candidates", "_pack_candidate", choose_entry),
                    ],
                ),
                _when(
                    "len(_pack_entry_targets) > 0",
                    [
                        _set(
                            "_pack_entry_destination",
                            [
                                f"_pack_start[{i}] + (destination[{i}] - _pack_start[{i}]) * "
                                "_pack_entry_t"
                                for i in range(3)
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "move_srd_conjure_animals_pack": _public(
            _environment_tool(
                "Choose the pack's printed movement after the caster's actual movement.",
                [
                    _entity("actor_state"),
                    _array("destination"),
                    _optional("witness_state_ids", "array", None, items={"type": "string"}),
                ],
                [
                    *deepcopy(source),
                    *deepcopy(clock),
                    _require(
                        "actor_state.is_alive == true and actor_state.hp > 0 and "
                        "'incapacitated' not in actor_state.conditions and "
                        "'unconscious' not in actor_state.conditions and "
                        "'paralyzed' not in actor_state.conditions and "
                        "'petrified' not in actor_state.conditions and "
                        "'stunned' not in actor_state.conditions",
                        "The actual caster must be able to choose the optional movement.",
                    ),
                    _set("_pack_receipt", "_pack_source.pack_movement_receipt"),
                    _require(
                        "_pack_receipt != null and _pack_receipt.source_id == _pack_source_id "
                        "and _pack_receipt.caster == _actor_state_id and "
                        "_pack_receipt.serial == _pack_source.pack_movement_serial and "
                        "_pack_receipt.clock == _pack_clock and _pack_receipt.turn == _pack_turn",
                        "One current movement by this exact source's caster is required.",
                    ),
                    _require(
                        "_pack_receipt.map_id == state.spatial_scene.map.id and "
                        "_pack_receipt.revision == state.spatial_scene.map.revision",
                        "Choose the pack continuation before another scene movement.",
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_pack_source.anchor_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_pack_original_position", "_info_position"),
                    _set("_pack_plane", "_info_scene_entity.metadata.plane"),
                    _call(
                        "measure_srd_information_distance",
                        {"start": "{_pack_original_position}", "end": "{destination}"},
                        {"_pack_move_distance": "_scene_distance"},
                    ),
                    _require(
                        "_pack_move_distance > 0.000000001 and _pack_move_distance <= 30",
                        "The optional pack movement is positive and at most its printed 30 feet.",
                    ),
                    _call(
                        "resolve_srd_illumination_point_sight",
                        {"observer_state": "{_actor_state_id}", "point": "{destination}"},
                        ("_illumination_point_can_see",),
                    ),
                    _require(
                        "_illumination_point_can_see == true",
                        "The caster must currently see the pack's destination space.",
                    ),
                    _set("_pack_excluded", ["_pack_source.anchor_id"]),
                    _call(
                        "resolve_srd_travel_placement",
                        {
                            "subject": "{_pack_source.anchor_id}",
                            "destination": "{destination}",
                            "plane": "{_pack_plane}",
                            "excluded": "{_pack_excluded}",
                        },
                        ("_travel_placement",),
                    ),
                    _require(
                        "abs(_travel_placement.position[0] - destination[0]) < 0.000000001 and "
                        "abs(_travel_placement.position[1] - destination[1]) < 0.000000001 and "
                        "abs(_travel_placement.position[2] - destination[2]) < 0.000000001",
                        "The selected destination must actually be unoccupied.",
                    ),
                    _set(
                        "state.persistent_spell_areas.{_pack_source_id}.pack_movement_receipt",
                        None,
                    ),
                    _set("_pack_visited_targets", []),
                    _set("_pack_move_continue", True),
                    _set("_pack_route_steps", "_pack_source.force_target_ids"),
                    _push("_pack_route_steps", "'final_endpoint'"),
                    _each(
                        "_pack_route_steps",
                        "_pack_route_step",
                        [
                            _when(
                                "_pack_move_continue == true",
                                [
                                    _call(
                                        "resolve_srd_animals_pack_entry",
                                        {
                                            "actor_state": "{_actor_state_id}",
                                            "destination": "{destination}",
                                            "visited_targets": "{_pack_visited_targets}",
                                        },
                                        ("_pack_entry_targets", "_pack_entry_destination"),
                                    ),
                                    _call(
                                        "move_srd_travel_subject",
                                        {
                                            "subject": "{_pack_source.anchor_id}",
                                            "destination": "{_pack_entry_destination}",
                                            "plane": "{_pack_plane}",
                                            "teleport": False,
                                        },
                                    ),
                                    _each(
                                        "_pack_entry_targets",
                                        "_pack_entered_target",
                                        [
                                            _push("_pack_visited_targets", "_pack_entered_target"),
                                            _call(
                                                "resolve_srd_woodland_event",
                                                {
                                                    "target_state": "{_pack_entered_target}",
                                                    "area_id": "{_pack_source_id}",
                                                    "witness_state_ids": "{witness_state_ids}",
                                                },
                                            ),
                                        ],
                                    ),
                                    _call(
                                        "qualify_srd_environment_emanation_source",
                                        {
                                            "caster_state": "{_actor_state_id}",
                                            "area_id": "{_pack_source_id}",
                                        },
                                        ("_emanation_source_current",),
                                    ),
                                    _set("_pack_move_continue", "_emanation_source_current"),
                                    _when(
                                        "len(_pack_entry_targets) == 0",
                                        [
                                            _when(
                                                "_emanation_source_current == true",
                                                [
                                                    _call(
                                                        "qualify_srd_created_actor_unoccupied",
                                                        {"summon_id": "{_pack_source.anchor_id}"},
                                                    )
                                                ],
                                            ),
                                            _set("_pack_move_continue", False),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
                public=True,
            ),
            "exploration",
            "Conjured pack moved",
            [("Pack", "_pack_source.anchor_id"), ("Chosen distance", "_pack_move_distance")],
        ),
    }


def _bind_animals_strength_d20(test):
    """Reuse the physical-save branch so ordinary Dexterity saves gain no inactive step."""
    test["effects"] = [
        effect
        for effect in test["effects"]
        if not (
            "qualify_srd_conjure_animals_strength" in str(effect)
            and effect.get("condition", "").startswith(
                ("state.has_spell_environments", "lasting_state.has_spell_environments")
            )
        )
    ]
    if "qualify_srd_conjure_animals_strength" in str(test["effects"]):
        return
    for effects in _effect_lists(test["effects"]):
        for effect in effects:
            condition = effect.get("condition", "")
            if (
                condition.startswith("kind == 'saving_throw'")
                and "ability == 'strength'" in condition
                and "'paralyzed' in actor_state.conditions" in condition
            ):
                original = effect["on_true"]
                animals = (
                    "kind == 'saving_throw' and ability == 'strength' and "
                    "state.has_spell_environments == true"
                )
                effect["condition"] = f"({condition}) or ({animals})"
                effect["on_true"] = [
                    _when(condition, original),
                    _when(
                        animals,
                        [
                            _call(
                                "qualify_srd_conjure_animals_strength",
                                {
                                    "actor_state": "{_actor_state_id}",
                                },
                                ("_conjure_animals_strength_advantage",),
                            ),
                            _set(
                                "_condition_advantage",
                                "_condition_advantage == true or "
                                "_conjure_animals_strength_advantage == true",
                            ),
                        ],
                    ),
                ]
                return
    raise ValueError("The actual physical-save condition branch is missing.")


def _bind_environment_hearing_guard(cast):
    """Qualify a hearing path only when the cast has a genuine scalar recipient."""
    for effects in _effect_lists(cast["effects"]):
        for effect in effects:
            if any(
                child.get("call") == "qualify_srd_environment_hearing"
                for child in effect.get("on_true", [])
            ):
                effect["condition"] = (
                    "state.has_spell_environments == true and _scalar_target_supplied == true"
                )


def _bind_blade_cover_consumers(tools):
    """Compose source Cover at existing consumers; physical degrees remain their authority."""
    changed = {}
    if "resolve_srd_scene_sight_origin" not in tools:
        return changed
    attack = deepcopy(tools["cast_spell_attack_damage"])
    if "resolve_srd_environment_blade_cover" not in str(attack["effects"]):
        index = next(
            i
            for i, e in enumerate(attack["effects"])
            if e.get("set", {}).get("field") == "_spell_target_armor_class"
        )
        # The final saved-hit owner reuses the armor slice beginning at this set.
        attack["effects"][index + 1 : index + 1] = [
            _set("_spell_blade_cover", "'none'"),
            _when(
                "state.has_blade_barrier == true",
                [
                    _require(
                        "force_source_id == ''",
                        "A created force attack needs its actual source-frame Blade Cover adapter.",
                    ),
                    _call(
                        "resolve_srd_environment_blade_cover",
                        {
                            "observer_subject": "{_caster_state_id}",
                            "target_subject": "{_target_id}",
                        },
                        ("_environment_blade_cover", "_environment_blade_cover_supported"),
                    ),
                    _require(
                        "_environment_blade_cover_supported == true",
                        "Partly covered body rays need continuous Blade Cover qualification.",
                    ),
                    _set("_spell_blade_cover", "_environment_blade_cover"),
                ],
            ),
        ]
        for effect in attack["effects"]:
            if effect.get("condition") == "cover == 'half'":
                effect["condition"] += " and _spell_blade_cover != 'three_quarters'"
            elif effect.get("condition") == "cover == 'three_quarters'":
                effect["condition"] += " or _spell_blade_cover == 'three_quarters'"
        changed["cast_spell_attack_damage"] = attack
    if "collect_srd_spell_area_cover_targets" in tools:
        collect = deepcopy(tools["collect_srd_spell_area_cover_targets"])
        if "resolve_srd_environment_blade_body_cover" not in str(collect["effects"]):
            collect["effects"].append(
                _when(
                    "state.has_blade_barrier == true",
                    [
                        _each(
                            "_srd_area_targets",
                            "_blade_area_target",
                            [
                                _when(
                                    "_blade_area_target not in _srd_area_three_quarters",
                                    [
                                        _call(
                                            "resolve_srd_environment_blade_body_cover",
                                            {
                                                "origin": "{origin}",
                                                "plane": "{_breath_source_plane}",
                                                "target_subject": "{_blade_area_target}",
                                            },
                                            (
                                                "_environment_blade_cover",
                                                "_environment_blade_cover_supported",
                                            ),
                                        ),
                                        _require(
                                            "_environment_blade_cover_supported == true",
                                            "Partial area body rays need continuous Blade Cover.",
                                        ),
                                        _when(
                                            "_environment_blade_cover == 'three_quarters'",
                                            [
                                                _push(
                                                    "_srd_area_three_quarters", "_blade_area_target"
                                                )
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        _set("_blade_area_prior_half", "_srd_area_half_cover"),
                        _set("_srd_area_half_cover", []),
                        _each(
                            "_blade_area_prior_half",
                            "_blade_area_half_target",
                            [
                                _when(
                                    "_blade_area_half_target not in _srd_area_three_quarters",
                                    [_push("_srd_area_half_cover", "_blade_area_half_target")],
                                ),
                            ],
                        ),
                    ],
                )
            )
            changed["collect_srd_spell_area_cover_targets"] = collect
    for tool in changed.values():
        bound = _environment_tool(
            tool["description"], tool["parameters"], tool["effects"], tool.get("pre_conditions", [])
        )
        tool["parameters"], tool["effects"] = bound["parameters"], bound["effects"]
    return changed


def bind_runtime_consumers(text):
    """Bind the movement receipt after the final shared ordinary movement consumer."""
    tools = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))["tools"]
    movement = deepcopy(tools["move_srd_scene_creature_node"])
    if "record_srd_environment_caster_movement" not in str(movement["effects"]):
        movement["effects"].append(
            _when(
                "state.has_spell_environments == true and _scene_movement_distance > 0.000000001",
                [
                    _call(
                        "record_srd_environment_caster_movement",
                        {"actor_state": "{_actor_state_id}"},
                    )
                ],
            )
        )
    if "check_srd_moonbeam_shapechange" not in str(movement["effects"]):
        movement["effects"].append(
            _when(
                "actor_state.moonbeam_shapechange_sources != null and "
                "len(actor_state.moonbeam_shapechange_sources) > 0",
                [_call("check_srd_moonbeam_shapechange", {"target_state": "{_actor_state_id}"})],
            )
        )
    changed = {
        "move_srd_scene_creature_node": movement,
        **_bind_stinking_cloud_budget(tools),
        **_bind_blade_cover_consumers(tools),
    }
    if __package__:
        from .srd_environment_wall_ice import bind_movement, bind_objects
    else:
        from srd_environment_wall_ice import bind_movement, bind_objects
    changed.update(bind_objects(tools))
    changed.update(bind_movement(tools))
    for name in ("refresh_srd_movement_budget", "derive_srd_scene_movement_budget"):
        budget = deepcopy(tools[name])
        if "resolve_srd_spirit_guardians_speed" not in str(budget["effects"]):
            for effects in _effect_lists(budget["effects"]):
                remaining = next(
                    (
                        e
                        for e in effects
                        if e.get("set", {}).get("field")
                        in ("combat.movement_remaining", "_scene_exploration_remaining")
                    ),
                    None,
                )
                if remaining is not None:
                    effects[effects.index(remaining) : effects.index(remaining)] = [
                        _when(
                            "state.has_spirit_guardians == true",
                            [
                                _call(
                                    "resolve_srd_spirit_guardians_speed",
                                    {"actor_state": "{_actor_state_id}"},
                                    ("_guardian_speed_halved",),
                                ),
                                _when(
                                    "_guardian_speed_halved == true",
                                    [_set("_movement_speed", "_movement_speed // 2")],
                                ),
                            ],
                        )
                    ]
        bound = _environment_tool(
            budget["description"],
            budget["parameters"],
            budget["effects"],
            budget.get("pre_conditions", []),
        )
        budget["parameters"] = bound["parameters"]
        budget["effects"] = bound["effects"]
        changed[name] = budget
    return _replace_tools(text, changed)


def _bind_animals_events(changed):
    """Preserve the shared packet while a successful Animals save rolls no damage."""
    event = changed["resolve_srd_woodland_event"]
    event["description"] = (
        "Resolve one visible selected creature's actual optional Conjure entry or end turn."
    )
    damage = next(
        effects
        for effects in _effect_lists(event["effects"])
        if any(e.get("set", {}).get("field") == "_woodland_dice" for e in effects)
    )
    start = next(
        i for i, e in enumerate(damage) if e.get("set", {}).get("field") == "_woodland_dice"
    )
    end = next(
        i for i, e in enumerate(damage) if e.get("set", {}).get("field") == "_woodland_triggered"
    )
    block = damage[start:end]
    marks, packet = [], []
    for effect in block:
        field = effect.get("set", {}).get("field", "")
        if (
            any(key in field for key in (".event_clock", ".event_turn", ".event_target_ids"))
            or effect.get("list_push", {}).get("list") == "_woodland_prior_targets"
        ):
            marks.append(effect)
        else:
            packet.append(effect)
    damage[start:end] = [
        *marks,
        _set("_woodland_damage_roll", None),
        _set("_effective_damage", 0),
        _when("_woodland_source.mode == 'woodland' or _outcome != 'success'", packet),
    ]
    saved = next(
        e for e in damage if e.get("set", {}).get("field", "").endswith(".last_woodland_save")
    )
    field = saved["set"]["field"]
    saved["set"]["field"] = "_conjure_environment_event"
    # A successful Animals save has no damage roll. Store that literal absence;
    # the expression resolver preserves unresolved/null identifiers as text.
    no_damage = deepcopy(saved)
    no_damage["set"]["value"]["raw_damage"] = None
    damage[damage.index(saved)] = _when(
        "_woodland_source.mode == 'animals' and _outcome == 'success'",
        [no_damage],
        [saved],
    )
    damage.append(
        _when(
            "_woodland_source.mode == 'woodland'",
            [_set(field, "_conjure_environment_event")],
            [
                _set(
                    field.replace("last_woodland_save", "last_conjure_animals_save"),
                    "_conjure_environment_event",
                )
            ],
        )
    )
    retire = changed["retire_srd_environment_ground_source"]
    capture = next(
        i
        for i, effect in enumerate(retire["effects"])
        if "_environment_area_key" in effect.get("export_context", {})
    )
    retire["effects"].insert(
        capture + 1,
        _call("retire_srd_environment_pack_scene", {"area_id": "{_environment_area_key}"}),
    )


def _elemental_overlap_tools():
    """Qualify nonstacking effects from actual source bindings and eligible spaces."""
    return {
        "qualify_srd_elemental_other_effect": _environment_tool(
            "Read whether a stronger Elemental source actually affects this creature.",
            [_entity("target_state"), _param("area_id"), _param("timing")],
            [
                _set("_elemental_other", "state.persistent_spell_areas[area_id]"),
                _set("_elemental_other_effect", False),
                _call(
                    "qualify_srd_environment_emanation_source",
                    {
                        "caster_state": "{_elemental_other.caster}",
                        "area_id": "{area_id}",
                    },
                    ("_emanation_source_current",),
                ),
                _when(
                    "_emanation_source_current == true",
                    [
                        _call(
                            "qualify_srd_conjure_elemental_binding",
                            {"area_id": "{area_id}"},
                            ("_elemental_binding_busy", "_elemental_binding"),
                        ),
                        _when(
                            "_elemental_binding_busy == true and "
                            "_elemental_binding.target_state_id == _target_state_id",
                            [
                                _set("_elemental_other_effect", True),
                            ],
                        ),
                        _when(
                            "_elemental_binding_busy != true and "
                            "_target_state_id in _elemental_other.force_target_ids",
                            [
                                _call(
                                    "srd_information_scene_entity",
                                    {"subject": "{_target_state_id}"},
                                    POSITION_EXPORTS,
                                ),
                                _set("_elemental_other_position", "_info_position"),
                                _set("_elemental_other_radius", 0),
                                _when("timing == 'start'", [_set("_elemental_other_radius", 5)]),
                                _call(
                                    "resolve_srd_environment_body_segment",
                                    {
                                        "moving_subject": "{_target_state_id}",
                                        "fixed_subject": "{_elemental_other.anchor_id}",
                                        "start": "{_elemental_other_position}",
                                        "end": "{_elemental_other_position}",
                                        "radius": "{_elemental_other_radius}",
                                    },
                                    ("_body_hit", "_body_bounds"),
                                ),
                                _when(
                                    "timing != 'start'",
                                    [
                                        _set(
                                            "_body_hit",
                                            "_body_hit == true and "
                                            + " and ".join(
                                                f"_elemental_other_position[{i}] > "
                                                f"_body_bounds.min[{i}] and "
                                                f"_elemental_other_position[{i}] < "
                                                f"_body_bounds.max[{i}]"
                                                for i in range(3)
                                            ),
                                        ),
                                    ],
                                ),
                                _when(
                                    "_body_hit == true",
                                    [
                                        _call(
                                            "resolve_srd_environment_anchor_geometry",
                                            {
                                                "anchor_subject": "{_elemental_other.anchor_id}",
                                                "target_subject": "{_target_state_id}",
                                            },
                                            ("_environment_anchor_clear",),
                                        ),
                                        _call(
                                            "resolve_srd_scene_relation",
                                            {
                                                "actor_state": "{_elemental_other.caster}",
                                                "target_state": "{_target_state_id}",
                                            },
                                            ("_scene_can_see",),
                                        ),
                                        _set(
                                            "_elemental_other_effect",
                                            "_environment_anchor_clear == true "
                                            "and _scene_can_see == true",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "qualify_srd_elemental_effect": _environment_tool(
            "Apply the strongest actual overlapping Elemental effect, then the latest tie.",
            [_entity("target_state"), _param("area_id"), _param("timing")],
            [
                _set("_elemental_selection", "state.persistent_spell_areas[area_id]"),
                _set("_elemental_effect_applies", True),
                *_areas(
                    [
                        _when(
                            "_environment_id != area_id and _environment_area.mode == 'elemental'",
                            [
                                _set(
                                    "_elemental_other_stronger",
                                    "_environment_area.spell_level > "
                                    "_elemental_selection.spell_level",
                                ),
                                _when(
                                    "_environment_area.spell_level == "
                                    "_elemental_selection.spell_level",
                                    [
                                        _set(
                                            "_elemental_other_stronger",
                                            "_environment_area.save_dc > "
                                            "_elemental_selection.save_dc or "
                                            "(_environment_area.save_dc == "
                                            "_elemental_selection.save_dc and "
                                            "_environment_area.sequence > "
                                            "_elemental_selection.sequence)",
                                        ),
                                    ],
                                ),
                                _when(
                                    "_elemental_other_stronger == true",
                                    [
                                        _call(
                                            "qualify_srd_elemental_other_effect",
                                            {
                                                "target_state": "{_target_state_id}",
                                                "area_id": "{_environment_id}",
                                                "timing": "{timing}",
                                            },
                                            ("_elemental_other_effect",),
                                        ),
                                        _when(
                                            "_elemental_other_effect == true",
                                            [
                                                _set("_elemental_effect_applies", False),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        )
                    ]
                ),
            ],
        ),
    }


def _elemental_tools():
    """Use one actual timed restraint source for the stationary Elemental spirit."""
    save = [
        _require(
            "target_state.saving_throw_modifiers.dexterity != null",
            "Use the target's actual Dexterity saving throw modifier.",
        ),
        _call(
            "check_srd_target_condition_immunity",
            {
                "target_state": "{_target_state_id}",
                "condition": "restrained",
                "source_state_id": "{_elemental_source.caster}",
                "source_is_magical": True,
            },
            ("_source_condition_immune", "_source_condition_save_advantage"),
        ),
        _call(
            "d20_test",
            {
                "actor_state": "{_target_state_id}",
                "kind": "saving_throw",
                "ability": "dexterity",
                "ability_modifier": "{target_state.saving_throw_modifiers.dexterity}",
                "proficiency_bonus": 0,
                "proficiency_factor": "none",
                "circumstantial_modifier": 0,
                "target_number": "{_elemental_source.save_dc}",
                "advantage": "{_source_condition_save_advantage}",
                "disadvantage": False,
                "save_avoids_condition": "restrained",
            },
            ("_outcome", "_d20_test"),
        ),
        _set("_elemental_save_failed", "_outcome != 'success'"),
        _set("_elemental_save_roll", "_d20_test"),
    ]
    packet = [
        _call(
            "apply_srd_conjure_elemental_damage",
            {
                "target_state": "{_target_state_id}",
                "area_id": "{area_id}",
                "repeat": "{_elemental_repeat}",
                "witness_state_ids": "{witness_state_ids}",
            },
            ("_elemental_raw_damage", "_elemental_applied_damage"),
        ),
    ]
    result = {
        "area_id": "area_id",
        "timing": "timing",
        "repeat": "_elemental_repeat",
        "failed": "_elemental_save_failed",
        "roll": "_elemental_save_roll",
        "raw_damage": "_elemental_raw_damage",
        "applied_damage": "_elemental_applied_damage",
        "tick": "state.lasting_tick",
    }
    return {
        **_elemental_overlap_tools(),
        "qualify_srd_elemental_bound_creature": _environment_tool(
            "Read the actual living creature and projected restraint behind a source binding.",
            [_entity("target_state")],
            [
                _set(
                    "_elemental_bound_creature",
                    "target_state.is_alive == true and 'restrained' in target_state.conditions",
                )
            ],
        ),
        "qualify_srd_conjure_elemental_binding": _environment_tool(
            "Read the spirit's exact installed restraint and current suppression projection.",
            [_param("area_id")],
            [
                _set("_elemental_binding_area", "state.persistent_spell_areas[area_id]"),
                _set("_elemental_binding_id", "_elemental_binding_area.restrained_source_id"),
                _set("_elemental_binding_present", False),
                _set("_elemental_binding_busy", False),
                _when(
                    "_elemental_binding_id != null",
                    [
                        _set(
                            "_elemental_binding",
                            "state.timed_condition_sources[_elemental_binding_id]",
                        ),
                        _when(
                            "_elemental_binding.installed == true and "
                            "_elemental_binding.spell_id == 'conjure_elemental' and "
                            "_elemental_binding.condition == 'restrained' and "
                            "_elemental_binding.environment_area_id == area_id and "
                            "_elemental_binding.environment_sequence == "
                            "_elemental_binding_area.sequence and "
                            "_elemental_binding.caster_state_id == _elemental_binding_area.caster",
                            [
                                _call(
                                    "refresh_srd_timed_condition_target",
                                    {
                                        "target_state": "{_elemental_binding.target_state_id}",
                                        "lasting_state": "state",
                                    },
                                ),
                                _set(
                                    "_elemental_binding",
                                    "state.timed_condition_sources[_elemental_binding_id]",
                                ),
                                _set(
                                    "_elemental_binding_present",
                                    "_elemental_binding.installed == true",
                                ),
                                _call(
                                    "qualify_srd_elemental_bound_creature",
                                    {
                                        "target_state": "{_elemental_binding.target_state_id}",
                                    },
                                    ("_elemental_bound_creature",),
                                ),
                                _set(
                                    "_elemental_binding_busy",
                                    "_elemental_binding_present == true and "
                                    "_elemental_binding.suppressed != true and "
                                    "_elemental_bound_creature == true",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "retire_srd_conjure_elemental_binding": _environment_tool(
            "Release only this spirit's exact restraint through the central source owner.",
            [_param("area_id")],
            [
                _call(
                    "qualify_srd_conjure_elemental_binding",
                    {"area_id": "{area_id}"},
                    ("_elemental_binding_present", "_elemental_binding", "_elemental_binding_id"),
                ),
                _when(
                    "_elemental_binding_present == true",
                    [
                        _call(
                            "retire_srd_timed_condition_source",
                            {"source_id": "{_elemental_binding_id}", "lasting_state": "state"},
                        ),
                        _call(
                            "refresh_srd_timed_condition_target",
                            {
                                "target_state": "{_elemental_binding.target_state_id}",
                                "lasting_state": "state",
                            },
                        ),
                    ],
                ),
                _set("state.persistent_spell_areas.{area_id}.restrained_source_id", None),
            ],
        ),
        "apply_srd_conjure_elemental_damage": _environment_tool(
            "Roll the actual source's initial or repeat typed damage in the shared damage packet.",
            [
                _entity("target_state"),
                _param("area_id"),
                _param("repeat", "boolean"),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            [
                _set("_elemental_damage_source", "state.persistent_spell_areas[area_id]"),
                _set(
                    "_elemental_damage_dice", "8 + max(0, _elemental_damage_source.spell_level - 5)"
                ),
                _when(
                    "repeat == true",
                    [
                        _set(
                            "_elemental_damage_dice",
                            "4 + max(0, _elemental_damage_source.spell_level - 5)",
                        )
                    ],
                ),
                _call(
                    "format_srd_information_projection",
                    {"message": "{_elemental_damage_dice}d8"},
                    {"_elemental_damage_expression": "_info_projection_message"},
                ),
                {
                    "dice": {"expression": "{_elemental_damage_expression}"},
                    "store_as": "_elemental_raw_damage",
                },
                _call(
                    "resolve_srd_actor_spell_modifiers",
                    {"target_state": "{_elemental_damage_source.caster}", "lasting_state": "state"},
                ),
                _call(
                    "modify_srd_actor_raw_damage_roll",
                    {
                        "actor_state": "{_elemental_damage_source.caster}",
                        "lasting_state": "state",
                        "amount": "{_elemental_raw_damage.total}",
                    },
                    {"_elemental_damage_amount": "_ray_raw_damage"},
                ),
                _set("_elemental_roll_penalty", "max(0, -_elemental_damage_amount)"),
                _set("_elemental_damage_amount", "max(0, _elemental_damage_amount)"),
                _call(
                    "apply_srd_qualifying_damage",
                    {
                        "target": "{_target_state_id}",
                        "amount": "{_elemental_damage_amount}",
                        "adjustment": 0,
                        "damage_type": "{_elemental_damage_source.damage_type}",
                        "source_state_id": "{_elemental_damage_source.caster}",
                        "witness_state_ids": "{witness_state_ids}",
                        "game_damage_roll": True,
                        "damage_roll_divisor": 1,
                        "damage_roll_base_remainder": 0,
                        "unallocated_roll_penalty": "{_elemental_roll_penalty}",
                    },
                    {"_elemental_applied_damage": "_effective_damage"},
                ),
            ],
        ),
        "resolve_srd_conjure_elemental_event": _environment_tool(
            "Resolve optional entry or start proximity and mandatory restrained turn saves.",
            [
                _entity("target_state"),
                _param("area_id"),
                _optional("timing", "string", "entry", enum_values=["entry", "start"]),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            [
                _set("_elemental_source", "state.persistent_spell_areas[area_id]"),
                _set("_elemental_triggered", False),
                _set("_elemental_repeat", False),
                _set("_elemental_eligible", False),
                _set("_elemental_raw_damage", None),
                _set("_elemental_applied_damage", 0),
                _call(
                    "qualify_srd_environment_emanation_source",
                    {"caster_state": "{_elemental_source.caster}", "area_id": "{area_id}"},
                    ("_emanation_source_current",),
                ),
                _when(
                    "_emanation_source_current == true and "
                    "_elemental_source.mode == 'elemental' and target_state.is_alive == true",
                    [
                        _when(
                            "timing == 'start'",
                            [
                                _require(
                                    "combat.turn_active == true and "
                                    "combat.active_actor == _target_state_id",
                                    "Elemental start saves require the creature's actual turn.",
                                ),
                            ],
                        ),
                        _call(
                            "qualify_srd_conjure_elemental_binding",
                            {"area_id": "{area_id}"},
                            (
                                "_elemental_binding_present",
                                "_elemental_binding_busy",
                                "_elemental_binding",
                                "_elemental_binding_id",
                            ),
                        ),
                        _when(
                            "timing == 'start' and _elemental_binding_busy == true and "
                            "_elemental_binding.target_state_id == _target_state_id",
                            [
                                _set("_elemental_repeat", True),
                                _set(
                                    "_elemental_eligible",
                                    "_elemental_binding.environment_repeat_turn != "
                                    "combat.turn_sequence",
                                ),
                                _when(
                                    "_elemental_eligible == true",
                                    [
                                        _set(
                                            "state.timed_condition_sources.{_elemental_binding_id}."
                                            "environment_repeat_turn",
                                            "combat.turn_sequence",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        _when(
                            "_elemental_repeat != true and _elemental_binding_busy != true "
                            "and _target_state_id in _elemental_source.force_target_ids",
                            [
                                _call(
                                    "srd_information_scene_entity",
                                    {"subject": "{_target_state_id}"},
                                    POSITION_EXPORTS,
                                ),
                                _set("_elemental_target_position", "_info_position"),
                                _set("_elemental_event_radius", 0),
                                _when("timing == 'start'", [_set("_elemental_event_radius", 5)]),
                                _call(
                                    "resolve_srd_environment_body_segment",
                                    {
                                        "moving_subject": "{_target_state_id}",
                                        "fixed_subject": "{_elemental_source.anchor_id}",
                                        "start": "{_elemental_target_position}",
                                        "end": "{_elemental_target_position}",
                                        "radius": "{_elemental_event_radius}",
                                    },
                                    ("_body_hit",),
                                ),
                                _when(
                                    "_body_hit == true",
                                    [
                                        _call(
                                            "resolve_srd_environment_anchor_geometry",
                                            {
                                                "anchor_subject": "{_elemental_source.anchor_id}",
                                                "target_subject": "{_target_state_id}",
                                            },
                                            ("_environment_anchor_clear",),
                                        ),
                                        _call(
                                            "resolve_srd_scene_relation",
                                            {
                                                "actor_state": "{_elemental_source.caster}",
                                                "target_state": "{_target_state_id}",
                                            },
                                            ("_scene_can_see",),
                                        ),
                                        _set(
                                            "_elemental_eligible",
                                            "_environment_anchor_clear == true and "
                                            "_scene_can_see == true",
                                        ),
                                        _when(
                                            "timing == 'start'",
                                            [
                                                _when(
                                                    "_elemental_source.initial_start_turn == "
                                                    "combat.turn_sequence and "
                                                    "_elemental_source.initial_start_target == "
                                                    "_target_state_id",
                                                    [_set("_elemental_eligible", False)],
                                                ),
                                                _when(
                                                    "_elemental_eligible == true",
                                                    [
                                                        _set(
                                                            "state.persistent_spell_areas.{area_id}"
                                                            ".initial_start_turn",
                                                            "combat.turn_sequence",
                                                        ),
                                                        _set(
                                                            "state.persistent_spell_areas.{area_id}"
                                                            ".initial_start_target",
                                                            "_target_state_id",
                                                        ),
                                                    ],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        _when(
                            "_elemental_eligible == true",
                            [
                                _call(
                                    "qualify_srd_elemental_effect",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "area_id": "{area_id}",
                                        "timing": "{timing}",
                                    },
                                    ("_elemental_effect_applies",),
                                ),
                                _set("_elemental_eligible", "_elemental_effect_applies"),
                            ],
                        ),
                        _when(
                            "_elemental_eligible == true",
                            [
                                *save,
                                _when(
                                    "_elemental_save_failed == true",
                                    packet,
                                    [
                                        _when(
                                            "_elemental_repeat == true",
                                            [
                                                _call(
                                                    "retire_srd_conjure_elemental_binding",
                                                    {"area_id": "{area_id}"},
                                                )
                                            ],
                                        ),
                                    ],
                                ),
                                _when(
                                    "_elemental_repeat != true and _elemental_save_failed == "
                                    "true and _source_condition_immune != true and "
                                    "target_state.is_alive == true",
                                    [
                                        _call(
                                            "qualify_srd_environment_emanation_source",
                                            {
                                                "caster_state": "{_elemental_source.caster}",
                                                "area_id": "{area_id}",
                                            },
                                            ("_emanation_source_current",),
                                        ),
                                        _when(
                                            "_emanation_source_current == true",
                                            [
                                                _call(
                                                    "retire_srd_conjure_elemental_binding",
                                                    {"area_id": "{area_id}"},
                                                ),
                                                _call(
                                                    "install_srd_environment_restrained",
                                                    {
                                                        "target_state": "{_target_state_id}",
                                                        "lasting_state": "state",
                                                        "spell": "conjure_elemental",
                                                        "condition": "restrained",
                                                        "save_dc": "{_elemental_source.save_dc}",
                                                        "duration_rounds": 100,
                                                        "repeat_save_timing": "none",
                                                        "save_ability": "dexterity",
                                                        "caster_state": (
                                                            "{_elemental_source.caster}"
                                                        ),
                                                        "actual_spell_level": (
                                                            "{_elemental_source.spell_level}"
                                                        ),
                                                    },
                                                    (
                                                        "_timed_condition_source_id",
                                                        "_timed_condition_source_active",
                                                    ),
                                                ),
                                                _when(
                                                    "_timed_condition_source_active == true",
                                                    [
                                                        _set(
                                                            "state.timed_condition_sources."
                                                            "{_timed_condition_source_id}.expires_tick",
                                                            "_elemental_source.expires_tick",
                                                        ),
                                                        _set(
                                                            "state.timed_condition_sources."
                                                            "{_timed_condition_source_id}.environment_area_id",
                                                            "area_id",
                                                        ),
                                                        _set(
                                                            "state.timed_condition_sources."
                                                            "{_timed_condition_source_id}.environment_sequence",
                                                            "_elemental_source.sequence",
                                                        ),
                                                        _set(
                                                            "state.persistent_spell_areas.{area_id}"
                                                            ".restrained_source_id",
                                                            "_timed_condition_source_id",
                                                        ),
                                                        _set(
                                                            "_elemental_restraint_ids",
                                                            "state.persistent_spell_areas[area_id]."
                                                            "condition_source_ids",
                                                        ),
                                                        _when(
                                                            "_timed_condition_source_id "
                                                            "not in _elemental_restraint_ids",
                                                            [
                                                                _push(
                                                                    "_elemental_restraint_ids",
                                                                    "_timed_condition_source_id",
                                                                )
                                                            ],
                                                        ),
                                                        _set(
                                                            "state.persistent_spell_areas.{area_id}"
                                                            ".condition_source_ids",
                                                            "_elemental_restraint_ids",
                                                        ),
                                                        _when(
                                                            "timing == 'start'",
                                                            [
                                                                _set(
                                                                    "state.timed_condition_sources."
                                                                    "{_timed_condition_source_id}.environment_repeat_turn",
                                                                    "combat.turn_sequence",
                                                                ),
                                                            ],
                                                        ),
                                                    ],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                                _set("_elemental_triggered", True),
                                _set("target_state.last_conjure_elemental_save", result),
                                _when(
                                    "_elemental_save_failed != true",
                                    [
                                        _set(
                                            "target_state.last_conjure_elemental_save",
                                            {**result, "raw_damage": None},
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "resolve_srd_environment_hazard_start_turn": _environment_tool(
            "Consume the actual starting creature's Elemental proximity or restraint event.",
            [
                _entity("target_state"),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            _areas(
                [
                    _when(
                        "_environment_area.mode == 'elemental'",
                        [
                            _call(
                                "resolve_srd_conjure_elemental_event",
                                {
                                    "target_state": "{_target_state_id}",
                                    "area_id": "{_environment_id}",
                                    "timing": "start",
                                    "witness_state_ids": "{witness_state_ids}",
                                },
                            ),
                        ],
                    )
                ]
            ),
        ),
        "resolve_srd_environment_hazard_event": _environment_tool(
            "Dispatch the committed entry to its exact environmental source owner.",
            [
                _entity("target_state"),
                _param("area_id"),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            [
                _set("_hazard_dispatch_source", "state.persistent_spell_areas[area_id]"),
                _when(
                    "_hazard_dispatch_source.mode == 'elemental'",
                    [
                        _call(
                            "resolve_srd_conjure_elemental_event",
                            {
                                "target_state": "{_target_state_id}",
                                "area_id": "{area_id}",
                                "witness_state_ids": "{witness_state_ids}",
                            },
                        ),
                    ],
                    [
                        _call(
                            "resolve_srd_woodland_event",
                            {
                                "target_state": "{_target_state_id}",
                                "area_id": "{area_id}",
                                "witness_state_ids": "{witness_state_ids}",
                            },
                        )
                    ],
                ),
            ],
        ),
    }


def _conjure_entry_tools():
    """Find actual entry before movement, then force only after its shared segment commits."""
    candidate = [
        _when(
            "_hazard_candidate not in _hazard_done_ids",
            [
                _call(
                    "resolve_srd_environment_body_segment",
                    {
                        "moving_subject": "{_actor_state_id}",
                        "fixed_subject": "{_hazard_fixed_subject}",
                        "start": "{_hazard_start}",
                        "end": "{destination}",
                        "radius": "{_environment_area.radius}",
                    },
                    ("_body_hit", "_body_entry", "_body_exit", "_body_bounds"),
                ),
                _when(
                    "_environment_area.mode == 'elemental' and _body_hit == true",
                    [
                        _set(
                            "_elemental_entry_midpoint",
                            [
                                f"_hazard_start[{i}] + (destination[{i}] - "
                                f"_hazard_start[{i}]) * (_body_entry + _body_exit) * 0.5"
                                for i in range(3)
                            ],
                        ),
                        _set(
                            "_body_hit",
                            "_body_exit > _body_entry and "
                            + " and ".join(
                                f"_elemental_entry_midpoint[{i}] > _body_bounds.min[{i}] and "
                                f"_elemental_entry_midpoint[{i}] < _body_bounds.max[{i}]"
                                for i in range(3)
                            ),
                        ),
                        _when(
                            "_body_hit == true and _body_entry > 0.000000000001",
                            [
                                _call(
                                    "measure_srd_information_distance",
                                    {"start": "{_hazard_start}", "end": "{destination}"},
                                    ("_scene_distance",),
                                ),
                                _set(
                                    "_body_entry",
                                    "_body_entry + min((_body_exit - _body_entry) * 0.5, "
                                    "0.000001 * _scene_distance ** -1)",
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    "_body_hit == true and _body_entry > 0.000000000001 and _body_entry <= 1",
                    [
                        _when(
                            "_body_entry < _hazard_entry_t - 0.000000000001",
                            [
                                _set("_hazard_entry_events", []),
                                _set("_hazard_entry_t", "_body_entry"),
                            ],
                        ),
                        _when(
                            "abs(_body_entry - _hazard_entry_t) < 0.000000000001",
                            [
                                _push(
                                    "_hazard_entry_events",
                                    {
                                        "area_id": "_environment_id",
                                        "target_id": "_hazard_candidate",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]
    return {
        "resolve_srd_environment_hazard_entry": _environment_tool(
            "Derive the next selected Emanation entry on the actual movement segment.",
            [_entity("actor_state"), _array("destination")],
            [
                _set("_hazard_entry_t", 2),
                _set("_hazard_entry_events", []),
                _set("_hazard_entry_destination", "destination"),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_actor_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_hazard_start", "_info_position"),
                _set("_hazard_clock", "'exploration'"),
                _set("_hazard_turn", "state.lasting_tick"),
                _when(
                    "combat.turn_active == true",
                    [
                        _set("_hazard_clock", "'combat'"),
                        _set("_hazard_turn", "combat.turn_sequence"),
                    ],
                ),
                *_areas(
                    [
                        _when(
                            "_environment_area.mode == 'woodland' or _environment_area.mode ="
                            "= 'animals' or _environment_area.mode == 'elemental'",
                            [
                                _set("_hazard_done_ids", []),
                                _when(
                                    "_environment_area.event_clock == _hazard_clock and "
                                    "_environment_area.event_turn == _hazard_turn",
                                    [
                                        _set(
                                            "_hazard_done_ids", "_environment_area.event_target_ids"
                                        ),
                                    ],
                                ),
                                _when(
                                    "_environment_area.mode == 'elemental'",
                                    [
                                        _call(
                                            "qualify_srd_conjure_elemental_binding",
                                            {"area_id": "{_environment_id}"},
                                            ("_elemental_binding_busy",),
                                        ),
                                        _when(
                                            "_elemental_binding_busy == true",
                                            [_push("_hazard_done_ids", "_actor_state_id")],
                                        ),
                                    ],
                                ),
                                _when(
                                    "_environment_area.mode == 'woodland' and _environment_ar"
                                    "ea.caster == _actor_state_id",
                                    [
                                        _set(
                                            "_hazard_candidates",
                                            "_environment_area.force_target_ids",
                                        ),
                                        _each(
                                            "_hazard_candidates",
                                            "_hazard_candidate",
                                            [
                                                _when(
                                                    "_hazard_candidate != _actor_state_id",
                                                    [
                                                        _set(
                                                            "_hazard_fixed_subject",
                                                            "_hazard_candidate",
                                                        ),
                                                        *candidate,
                                                    ],
                                                ),
                                            ],
                                        ),
                                    ],
                                    [
                                        _when(
                                            "_actor_state_id in _environment_area.force_target_ids",
                                            [
                                                _set("_hazard_candidate", "_actor_state_id"),
                                                _set(
                                                    "_hazard_fixed_subject",
                                                    "_environment_area.anchor_id",
                                                ),
                                                *candidate,
                                            ],
                                        )
                                    ],
                                ),
                            ],
                        )
                    ]
                ),
                _when(
                    "len(_hazard_entry_events) > 0",
                    [
                        _set(
                            "_hazard_entry_destination",
                            [
                                f"_hazard_start[{i}] + (destination[{i}] - _hazard_start[{i}]) * "
                                "_hazard_entry_t"
                                for i in range(3)
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "resolve_srd_environment_hazard_end_turn": _environment_tool(
            "Force eligible selected Woodland occupants once on the actual ending turn.",
            [
                _entity("target_state"),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            _areas(
                [
                    _when(
                        "_environment_area.mode == 'woodland' or _environment_area.mode == 'a"
                        "nimals'",
                        [
                            _call(
                                "resolve_srd_woodland_event",
                                {
                                    "target_state": "{_target_state_id}",
                                    "area_id": "{_environment_id}",
                                    "witness_state_ids": "{witness_state_ids}",
                                },
                            ),
                        ],
                    )
                ]
            ),
        ),
    }


def _entangle_tools():
    """Entangle restrains initial occupants; its later escape is an actual Athletics Action."""
    save_result = {
        "area_id": "area_id",
        "roll": "_d20_test",
        "outcome": "_outcome",
        "restrained": "_entangle_restrained",
        "condition_source_id": None,
        "tick": "state.lasting_tick",
    }
    tools = {
        "apply_srd_entangle_save": _environment_tool(
            "Resolve the initial Strength save against the actual Entangle source DC.",
            [_entity("target_state"), _param("area_id")],
            [
                _set("_entangle_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_entangle_source.active == true and _entangle_source.mode == 'entangle' "
                    "and state.lasting_tick < _entangle_source.expires_tick and "
                    "_entangle_source.caster != _target_state_id",
                    "Select an initial occupant other than the active Entangle caster.",
                ),
                _require(
                    "target_state.saving_throw_modifiers.strength != null",
                    "Entangle needs the creature's actual Strength saving throw modifier.",
                ),
                _call(
                    "check_srd_target_condition_immunity",
                    {
                        "target_state": "{_target_state_id}",
                        "condition": "restrained",
                        "source_state_id": "{_entangle_source.caster}",
                        "source_is_magical": True,
                    },
                ),
                _call(
                    "d20_test",
                    {
                        "actor_state": "{_target_state_id}",
                        "kind": "saving_throw",
                        "ability": "strength",
                        "ability_modifier": "{target_state.saving_throw_modifiers.strength}",
                        "proficiency_bonus": 0,
                        "proficiency_factor": "none",
                        "circumstantial_modifier": 0,
                        "target_number": "{_entangle_source.save_dc}",
                        "advantage": "{_source_condition_save_advantage}",
                        "disadvantage": False,
                        "save_avoids_condition": "restrained",
                    },
                ),
                _set(
                    "_entangle_restrained",
                    "_outcome == 'failure' and _source_condition_immune != true",
                ),
                _set("_entangle_condition_id", None),
                _when(
                    "_entangle_restrained == true",
                    [
                        _call(
                            "install_srd_environment_restrained",
                            {
                                "target_state": "{_target_state_id}",
                                "lasting_state": "state",
                                "spell": "entangle",
                                "condition": "restrained",
                                "save_dc": "{_entangle_source.save_dc}",
                                "duration_rounds": 10,
                                "repeat_save_timing": "none",
                                "save_ability": "strength",
                                "caster_state": "{_entangle_source.caster}",
                                "actual_spell_level": "{_entangle_source.spell_level}",
                            },
                            ("_timed_condition_source_id",),
                        ),
                        _set("_entangle_condition_id", "_timed_condition_source_id"),
                        _set(
                            "state.timed_condition_sources.{_entangle_condition_id}.expires_tick",
                            "_entangle_source.expires_tick",
                        ),
                        _set(
                            "state.timed_condition_sources.{_entangle_condition_id}.environme"
                            "nt_area_id",
                            "area_id",
                        ),
                        _set(
                            "state.timed_condition_sources.{_entangle_condition_id}.environme"
                            "nt_sequence",
                            "_entangle_source.sequence",
                        ),
                        _set(
                            "_entangle_conditions",
                            "state.persistent_spell_areas[area_id].condition_source_ids",
                        ),
                        _push("_entangle_conditions", "_entangle_condition_id"),
                        _set(
                            "state.persistent_spell_areas.{area_id}.condition_source_ids",
                            "_entangle_conditions",
                        ),
                    ],
                ),
                _set("target_state.last_entangle_save", save_result),
                _when(
                    "_entangle_condition_id != null",
                    [
                        _set(
                            "target_state.last_entangle_save",
                            {**save_result, "condition_source_id": "_entangle_condition_id"},
                        )
                    ],
                ),
            ],
        ),
        "resolve_srd_entangle_scene": _environment_tool(
            "Resolve Entangle once for actual initial ground occupants other than its caster.",
            [_param("area_id")],
            [
                _set("_entangle_scene_source", "state.persistent_spell_areas[area_id]"),
                {
                    "spatial_query": {
                        "scene": "state.spatial_scene",
                        "operation": "area",
                        "query": {
                            "map_id": "{state.spatial_scene.map.id}",
                            "shape": {
                                "kind": "box",
                                "min": "{_entangle_scene_source.volume.min}",
                                "max": "{_entangle_scene_source.volume.max}",
                            },
                            "include": ["entities"],
                            "max_results": 4096,
                        },
                    },
                    "store_as": "_entangle_initial_area",
                },
                _require(
                    "_entangle_initial_area.truncated != true",
                    "Entangle initial occupants exceed the supported area-query limit.",
                ),
                _set("_entangle_scene_entities", "_entangle_initial_area.results"),
                _each(
                    "_entangle_scene_entities",
                    "_entangle_scene_entity",
                    [
                        _when(
                            "_entangle_scene_entity.metadata.participant_ref != null and "
                            "_entangle_scene_entity.metadata.plane == "
                            "_entangle_scene_source.plane and "
                            "_entangle_scene_entity.metadata.participant_ref != "
                            "_entangle_scene_source.caster",
                            [
                                _call(
                                    "apply_srd_entangle_save",
                                    {
                                        "target_state": (
                                            "{_entangle_scene_entity.metadata.participant_ref}"
                                        ),
                                        "area_id": "{area_id}",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "retire_srd_environment_restraints": _environment_tool(
            "Retire only still-installed condition sources belonging to this exact area instance.",
            [_param("area_id")],
            [
                _set("_restraint_area", "state.persistent_spell_areas[area_id]"),
                _set("_restraint_ids", []),
                _when(
                    "_restraint_area.condition_source_ids != null",
                    [_set("_restraint_ids", "_restraint_area.condition_source_ids")],
                ),
                _each(
                    "_restraint_ids",
                    "_restraint_id",
                    [
                        _set("_restraint_source", "state.timed_condition_sources[_restraint_id]"),
                        _when(
                            "_restraint_source.installed == true and "
                            "_restraint_source.spell_id == _restraint_area.spell and "
                            "_restraint_source.caster_state_id == _restraint_area.caster and "
                            "_restraint_source.condition == 'restrained' and "
                            "_restraint_source.environment_area_id == area_id and "
                            "_restraint_source.environment_sequence == _restraint_area.sequence",
                            [
                                _call(
                                    "retire_srd_timed_condition_source",
                                    {"source_id": "{_restraint_id}", "lasting_state": "state"},
                                ),
                                _call(
                                    "refresh_srd_timed_condition_target",
                                    {
                                        "target_state": "{_restraint_source.target_state_id}",
                                        "lasting_state": "state",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "escape_srd_entangle": _public(
            _environment_tool(
                "Spend an Action on the source DC Strength (Athletics) check to escape Entangle.",
                [
                    _entity("actor_state"),
                    _param("source_id"),
                    _optional("actor", "string", None, resolve="participant"),
                ],
                [
                    _require(
                        "actor_state.is_alive == true and "
                        "'incapacitated' not in actor_state.conditions and "
                        "'restrained' in actor_state.conditions",
                        "The creature must be able to act while Restrained by Entangle.",
                    ),
                    _set("_escape_source", "state.timed_condition_sources[source_id]"),
                    _set(
                        "_escape_area",
                        "state.persistent_spell_areas[_escape_source.environment_area_id]",
                    ),
                    _require(
                        "_escape_source.installed == true and "
                        "_escape_source.spell_id == 'entangle' "
                        "and _escape_source.target_state_id == _actor_state_id and "
                        "_escape_source.condition == 'restrained' and _escape_area.active == true "
                        "and _escape_source.environment_sequence == _escape_area.sequence and "
                        "state.lasting_tick < _escape_area.expires_tick",
                        "Select this creature's still-active Entangle restraint source.",
                    ),
                    _set("_escape_dc", "_escape_source.save_dc"),
                    _set("_escape_proficiency", "'none'"),
                    _when(
                        "actor != null",
                        [
                            _require(
                                "_actor_id == _actor_state_id",
                                "The character data must belong to the escaping creature.",
                            ),
                            _when(
                                "'athletics' in actor.data.skill_proficiencies",
                                [_set("_escape_proficiency", "'normal'")],
                            ),
                        ],
                        [
                            _require(
                                "actor_state.skill_check_modifiers.athletics != null",
                                "Record the creature's actual Athletics modifier before "
                                "this check.",
                            ),
                        ],
                    ),
                    _when(
                        "combat.encounter_active == true",
                        [
                            _require(
                                "combat.turn_active == true and "
                                "combat.active_actor == _actor_state_id",
                                "Only the active creature can spend this Action.",
                            ),
                            _call("spend_srd_action", {"magic_action": False}),
                            _set("combat.action_taken", "'escape_entangle'"),
                        ],
                    ),
                    _when(
                        "actor != null",
                        [
                            _call(
                                "d20_test",
                                {
                                    "actor": "{_actor_id}",
                                    "actor_state": "{_actor_state_id}",
                                    "ability": "strength",
                                    "skill": "athletics",
                                    "kind": "ability_check",
                                    "proficiency_factor": "{_escape_proficiency}",
                                    "circumstantial_modifier": 0,
                                    "target_number": "{_escape_dc}",
                                },
                                ("_outcome", "_d20_test", "_adjusted_total", "_combined_modifier"),
                            ),
                        ],
                        [
                            _call(
                                "d20_test",
                                {
                                    "actor_state": "{_actor_state_id}",
                                    "ability": "strength",
                                    "skill": "athletics",
                                    "kind": "ability_check",
                                    "ability_modifier": (
                                        "{actor_state.skill_check_modifiers.athletics}"
                                    ),
                                    "proficiency_bonus": 0,
                                    "proficiency_factor": "none",
                                    "circumstantial_modifier": 0,
                                    "target_number": "{_escape_dc}",
                                },
                                ("_outcome", "_d20_test", "_adjusted_total", "_combined_modifier"),
                            ),
                        ],
                    ),
                    _set("_escape_success", "_outcome == 'success'"),
                    _when(
                        "_escape_success == true",
                        [
                            _call(
                                "retire_srd_timed_condition_source",
                                {"source_id": "{source_id}", "lasting_state": "state"},
                            ),
                            _call(
                                "refresh_srd_timed_condition_target",
                                {"target_state": "{_actor_state_id}", "lasting_state": "state"},
                            ),
                        ],
                    ),
                    _set(
                        "actor_state.last_entangle_escape",
                        {
                            "source_id": "source_id",
                            "roll": "_d20_test",
                            "total": "_adjusted_total",
                            "dc": "_escape_dc",
                            "success": "_escape_success",
                            "tick": "state.lasting_tick",
                        },
                    ),
                    _when(
                        "combat.encounter_active != true",
                        [_call("advance_srd_lasting_time", {"elapsed_rounds": 1})],
                    ),
                ],
                public=True,
            ),
            "combat",
            "Escape Entangle",
            [("Escaped", "_escape_success"), ("Total", "_adjusted_total"), ("DC", "_escape_dc")],
        ),
    }
    save = tools["apply_srd_entangle_save"]["effects"]
    tools["apply_srd_entangle_save"]["effects"] = [
        *save[:2],
        _when("target_state.is_alive == true", save[2:]),
    ]
    return tools


def _terrain_tools():
    """Persist source terrain and derive saves from actual ground membership and crossings."""
    zone = {
        "id": "_info_projection_message",
        "map_id": "state.spatial_scene.map.id",
        "shape": "_terrain_source.volume",
        "metadata": {
            "source_environment_id": "area_id",
            "source_spell": "_terrain_source.spell",
            "plane": "_terrain_source.plane",
            "difficult": True,
            "ground_environment": True,
            "magical_effect": True,
            "plants": "_terrain_source.mode == 'entangle'",
            "plant_growth": "_terrain_source.mode == 'entangle'",
        },
    }
    snapshot = {
        "spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"},
        "store_as": "_environment_ground_snapshot",
    }
    return {
        "install_srd_environment_ground_zone": _environment_tool(
            "Admit the source's fixed ground square as Difficult Terrain in the actual scene.",
            [_param("area_id")],
            [
                _set("_terrain_source", "state.persistent_spell_areas[area_id]"),
                _call(
                    "format_srd_information_projection",
                    {
                        "message": "environment:{area_id}",
                    },
                    ("_info_projection_message",),
                ),
                _set("_terrain_zone", zone),
                _when(
                    "_terrain_source.mode == 'grease'",
                    [
                        _set(
                            "_terrain_zone",
                            {**zone, "metadata": {**zone["metadata"], "flammable": False}},
                        )
                    ],
                ),
                _set("_terrain_zones", "state.spatial_scene.zones"),
                _push("_terrain_zones", "_terrain_zone"),
                _set("state.spatial_scene.zones", "_terrain_zones"),
                {"increment": {"field": "state.spatial_scene.map.revision", "amount": 1}},
                snapshot,
            ],
        ),
        "retire_srd_environment_ground_source": _environment_tool(
            "Retire only the selected source's terrain without ending another concentration.",
            [_param("area_id")],
            [
                _call("retire_srd_environment_restraints", {"area_id": "{area_id}"}),
                _set("state.persistent_spell_areas.{area_id}.active", False),
                _set("_terrain_prior", "state.spatial_scene.zones"),
                _set("_terrain_retained", []),
                _each(
                    "_terrain_prior",
                    "_terrain_zone",
                    [
                        _when(
                            "_terrain_zone.metadata.source_environment_id != area_id",
                            [
                                _push("_terrain_retained", "_terrain_zone"),
                            ],
                        ),
                    ],
                ),
                _when(
                    "len(_terrain_retained) != len(_terrain_prior)",
                    [
                        _set("state.spatial_scene.zones", "_terrain_retained"),
                        {"increment": {"field": "state.spatial_scene.map.revision", "amount": 1}},
                        snapshot,
                    ],
                ),
            ],
        ),
        "qualify_srd_environment_ground_actor": _environment_tool(
            "Determine whether the actual creature stands on the source ground square.",
            [
                _entity("target_state"),
                _param("area_id"),
                _optional("standing_only", "boolean", False),
            ],
            [
                _set("_ground_actor_inside", False),
                _set("_ground_actor_source", "state.persistent_spell_areas[area_id]"),
                _call(
                    "srd_information_scene_entity",
                    {
                        "subject": "{_target_state_id}",
                    },
                    POSITION_EXPORTS,
                ),
                _when(
                    "target_state.is_alive == true and "
                    "(standing_only != true or 'prone' not in target_state.conditions) and "
                    "_ground_actor_source.active == true and "
                    "state.lasting_tick < _ground_actor_source.expires_tick and "
                    "_ground_actor_source.plane == _info_scene_entity.metadata.plane and "
                    "_info_scene_entity.metadata.flying != true",
                    [
                        _call(
                            "srd_information_segment_interval",
                            {
                                "start": "{_info_position}",
                                "end": "{_info_position}",
                                "volume": "{_ground_actor_source.volume}",
                            },
                            ("_segment_hit",),
                        ),
                        _set("_ground_actor_inside", "_segment_hit"),
                    ],
                ),
            ],
        ),
        "apply_srd_grease_save": _environment_tool(
            "Roll the creature's Dexterity save against the saved caster DC and apply Prone.",
            [_entity("target_state"), _param("area_id")],
            [
                _set("_grease_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_grease_source.active == true and _grease_source.mode == 'grease' "
                    "and state.lasting_tick < _grease_source.expires_tick",
                    "The Grease source must still be active.",
                ),
                _require(
                    "target_state.saving_throw_modifiers.dexterity != null",
                    "This save needs the creature's actual Dexterity saving throw modifier.",
                ),
                _call(
                    "check_srd_target_condition_immunity",
                    {
                        "target_state": "{_target_state_id}",
                        "condition": "prone",
                        "source_state_id": "{_grease_source.caster}",
                        "source_is_magical": True,
                    },
                ),
                _call(
                    "d20_test",
                    {
                        "actor_state": "{_target_state_id}",
                        "kind": "saving_throw",
                        "ability": "dexterity",
                        "ability_modifier": "{target_state.saving_throw_modifiers.dexterity}",
                        "proficiency_bonus": 0,
                        "proficiency_factor": "none",
                        "circumstantial_modifier": 0,
                        "target_number": "{_grease_source.save_dc}",
                        "advantage": "{_source_condition_save_advantage}",
                        "disadvantage": False,
                        "save_avoids_condition": "prone",
                    },
                ),
                _set("_grease_fell", "_outcome == 'failure' and _source_condition_immune != true"),
                _when(
                    "_grease_fell == true",
                    [
                        _call(
                            "apply_srd_condition",
                            {
                                "target": "{_target_state_id}",
                                "condition": "prone",
                                "source_state_id": "{_grease_source.caster}",
                                "source_is_magical": True,
                            },
                        ),
                    ],
                ),
                _set(
                    "target_state.last_grease_save",
                    {
                        "area_id": "area_id",
                        "roll": "_d20_test",
                        "outcome": "_outcome",
                        "fell": "_grease_fell",
                        "tick": "state.lasting_tick",
                    },
                ),
            ],
        ),
        "resolve_srd_grease_scene": _environment_tool(
            "Resolve the initial Grease save for each actual creature standing in the source area.",
            [_param("area_id")],
            [
                _set("_grease_scene_entities", "state.spatial_scene.entities"),
                _each(
                    "_grease_scene_entities",
                    "_grease_scene_entity",
                    [
                        _when(
                            "_grease_scene_entity.active == true and "
                            "_grease_scene_entity.metadata.participant_ref != null",
                            [
                                _call(
                                    "qualify_srd_environment_ground_actor",
                                    {
                                        "target_state": (
                                            "{_grease_scene_entity.metadata.participant_ref}"
                                        ),
                                        "area_id": "{area_id}",
                                        "standing_only": True,
                                    },
                                    ("_ground_actor_inside",),
                                ),
                                _when(
                                    "_ground_actor_inside == true",
                                    [
                                        _call(
                                            "apply_srd_grease_save",
                                            {
                                                "target_state": (
                                                    "{_grease_scene_entity.metadata.participa"
                                                    "nt_ref}"
                                                ),
                                                "area_id": "{area_id}",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "resolve_srd_grease_end_turn": _environment_tool(
            "Resolve each still-active Grease source containing the ending creature.",
            [_entity("target_state")],
            _areas(
                [
                    _when(
                        "_environment_area.mode == 'grease'",
                        [
                            _call(
                                "qualify_srd_environment_ground_actor",
                                {
                                    "target_state": "{_target_state_id}",
                                    "area_id": "{_environment_id}",
                                },
                                ("_ground_actor_inside",),
                            ),
                            _when(
                                "_ground_actor_inside == true",
                                [
                                    _call(
                                        "apply_srd_grease_save",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "area_id": "{_environment_id}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ),
        "resolve_srd_environment_ground_entry": _environment_tool(
            "Derive the next actual entry boundary along the chosen movement segment.",
            [_entity("actor_state"), _array("destination"), _param("mode")],
            [
                _set("_ground_entry_id", None),
                _set("_ground_entry_ids", []),
                _set("_ground_entry_t", 2),
                _set("_ground_entry_destination", "destination"),
                _call(
                    "srd_information_scene_entity",
                    {
                        "subject": "{_actor_state_id}",
                    },
                    POSITION_EXPORTS,
                ),
                _set("_ground_entry_origin", "_info_position"),
                _set("_ground_entry_plane", "_info_scene_entity.metadata.plane"),
                _when(
                    "mode != 'fly' and _info_scene_entity.metadata.flying != true",
                    _areas(
                        [
                            _when(
                                "_environment_area.mode == 'grease' and "
                                "_environment_area.plane == _ground_entry_plane",
                                [
                                    _call(
                                        "srd_information_segment_interval",
                                        {
                                            "start": "{_ground_entry_origin}",
                                            "end": "{destination}",
                                            "volume": "{_environment_area.volume}",
                                        },
                                        ("_segment_hit", "_segment_entry", "_segment_exit"),
                                    ),
                                    _when(
                                        "_segment_hit == true and _segment_entry > 0.000000001 and "
                                        "_segment_entry <= _ground_entry_t",
                                        [
                                            _when(
                                                "_segment_entry < _ground_entry_t",
                                                [
                                                    _set("_ground_entry_ids", []),
                                                    _set("_ground_entry_t", "_segment_entry"),
                                                    _set("_ground_entry_id", "_environment_id"),
                                                ],
                                            ),
                                            _push("_ground_entry_ids", "_environment_id"),
                                        ],
                                    ),
                                ],
                            ),
                        ]
                    ),
                ),
                _when(
                    "_ground_entry_id != null",
                    [
                        _set(
                            "_ground_entry_destination",
                            [
                                f"_ground_entry_origin[{i}] + (destination[{i}] - "
                                f"_ground_entry_origin[{i}]) * _ground_entry_t"
                                for i in range(3)
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }


def _tools():
    cast = _environment_tool(
        "Install a source-sized environment at a chosen, range-qualified admitted scene point.",
        [
            _entity("caster_state"),
            _param("spell"),
            _param("slot_level", "integer"),
            _param("concentration_save_modifier", "integer"),
            _optional("save_dc", "integer", 0, minimum=0),
            _array("origin"),
            _optional("force_targets", "array", [], items={"type": "string"}, max_items=64),
            _optional("include_origin", "boolean", False),
            _optional("pack_id", "string", ""),
            _optional("pack_height", "number", None, minimum=0.01, maximum=1000),
            _optional("cylinder_face", "string", "bottom", enum_values=["bottom", "top"]),
            _optional("element", "string", "air", enum_values=["air", "earth", "fire", "water"]),
        ],
        [
            {
                "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
                "store_as": "_environment_profile",
            },
            _require(
                "_environment_profile.family == 'spell_environment' and "
                "slot_level >= _environment_profile.base_level and slot_level <= 9",
                "Use this environment family's actual valid source slot.",
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_environment_plane", "_info_scene_entity.metadata.plane"),
            _set("_environment_cast_origin", "origin"),
            _when(
                "_environment_profile.environment_mode == 'woodland' or "
                "_environment_profile.environment_mode == 'minor_elementals'",
                [
                    _set("_environment_cast_origin", "_info_position"),
                    _when(
                        "_environment_profile.environment_mode == 'woodland'",
                        [_require("save_dc >= 1", "Use the actual caster DC for Woodland saves.")],
                    ),
                ],
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_info_position}", "end": "{_environment_cast_origin}"},
                ("_scene_distance",),
            ),
            _require(
                "_scene_distance <= _environment_profile.range_feet",
                "The chosen environment origin is outside the spell's range.",
            ),
            _call(
                "srd_information_barriers",
                {"start": "{_info_position}", "end": "{_environment_cast_origin}", "rule": "solid"},
                ("_information_blocked",),
            ),
            _require(
                "_information_blocked != true", "A solid barrier prevents placing this environment."
            ),
            _when(
                "(_environment_profile.environment_mode == 'grease' or "
                "_environment_profile.environment_mode == 'entangle')",
                [
                    _require(
                        "save_dc >= 1", "Ground spell saves need the actual caster spell save DC."
                    ),
                    _call(
                        "qualify_srd_environment_ground",
                        {
                            "point": "{_environment_cast_origin}",
                            "plane": "{_environment_plane}",
                        },
                        ("_environment_ground_id",),
                    ),
                ],
            ),
            _when(
                "_environment_profile.environment_mode == 'animals' or "
                "_environment_profile.environment_mode == 'elemental'",
                [
                    _require("save_dc >= 1", "Use the actual caster DC for Conjure saves."),
                    _require(
                        "pack_height != null", "Admit the pack's actual physical appearance height."
                    ),
                    _require(
                        " and ".join(
                            [
                                f"_environment_cast_origin[{i}] - "
                                "_environment_profile.pack_half_extent >= "
                                f"state.spatial_scene.map.bounds.min[{i}] and "
                                f"_environment_cast_origin[{i}] + "
                                "_environment_profile.pack_half_extent <= "
                                f"state.spatial_scene.map.bounds.max[{i}]"
                                for i in range(2)
                            ]
                        ),
                        "The actual Large pack must fit within the admitted map bounds.",
                    ),
                    _require(
                        "_environment_cast_origin[2] >= state.spatial_scene.map.bounds.min[2] and "
                        "_environment_cast_origin[2] + pack_height <= state.spatial_scene.map"
                        ".bounds.max[2]",
                        "The pack's actual height must fit within the admitted map bounds.",
                    ),
                    _call(
                        "resolve_srd_illumination_point_sight",
                        {
                            "observer_state": "{_caster_state_id}",
                            "point": "{_environment_cast_origin}",
                        },
                        ("_illumination_point_can_see", "_illumination_point_lighting"),
                    ),
                    _when(
                        "_environment_profile.environment_mode == 'animals'",
                        [
                            _require(
                                "_illumination_point_can_see == true",
                                "The caster must see the actual pack space.",
                            )
                        ],
                    ),
                    _set("_environment_pack_lighting", "_illumination_point_lighting"),
                ],
            ),
            _when(
                "_environment_profile.concentration == true and "
                "caster_state.concentration_spell_id != null",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{caster_state.concentration_target_state_id}",
                            "lasting_state": "state",
                        },
                    ),
                ],
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
            {"increment": {"field": "state.lasting_serial", "amount": 1}},
            _set("_environment_cast_id", "_caster_state_id"),
            _when(
                "_environment_profile.concentration != true",
                [
                    _call(
                        "format_srd_information_projection",
                        {
                            "message": "{_caster_state_id}:{spell}:{state.lasting_serial}",
                        },
                        ("_info_projection_message",),
                    ),
                    _set("_environment_cast_id", "_info_projection_message"),
                ],
            ),
            _when(
                "state.persistent_spell_area_ids == null",
                [
                    _set("state.persistent_spell_area_ids", []),
                    _set("state.persistent_spell_areas", {}),
                ],
            ),
            _set(
                "state.persistent_spell_areas.{_environment_cast_id}",
                {
                    "profile": "'environment'",
                    "spell": "spell",
                    "caster": "_caster_state_id",
                    "origin": "_environment_cast_origin",
                    "plane": "_environment_plane",
                    ("radius"): (
                        "_environment_profile.radius_feet + max(0, slot_level - _environment_"
                        "profile.base_level) * _environment_profile.radius_per_slot_level"
                    ),
                    "expires_tick": "state.lasting_tick + _environment_profile.duration_rounds * 2",
                    "active": True,
                    "mode": "_environment_profile.environment_mode",
                    "concentration": "_environment_profile.concentration",
                    "save_dc": "save_dc",
                    "spell_level": "slot_level",
                    "dispel_ongoing": True,
                    "sequence": "state.lasting_serial",
                    "condition_source_ids": [],
                },
            ),
            _set("_environment_registered", "state.persistent_spell_area_ids"),
            _when(
                "_environment_cast_id not in _environment_registered",
                [_push("_environment_registered", "_environment_cast_id")],
            ),
            _set("state.persistent_spell_area_ids", "_environment_registered"),
            _set("state.has_persistent_spell_areas", True),
            _set("state.has_spell_environments", True),
            _when(
                "_environment_profile.concentration == true",
                [
                    _set("caster_state.concentration_spell_id", "spell"),
                    _set("caster_state.concentration_effect_family", "'spell_environment'"),
                    _set("caster_state.concentration_target_state_id", "_caster_state_id"),
                    _set("caster_state.concentration_save_modifier", "concentration_save_modifier"),
                    _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                    _set(
                        "caster_state.concentration_expires_tick",
                        "state.lasting_tick + _environment_profile.duration_rounds * 2",
                    ),
                    _set("caster_state.environment_area_id", "_environment_cast_id"),
                ],
            ),
            _when(
                "_environment_profile.environment_mode == 'woodland' or "
                "_environment_profile.environment_mode == 'minor_elementals' or "
                "_environment_profile.environment_mode == 'animals' or "
                "_environment_profile.environment_mode == 'elemental'",
                [
                    _set(
                        "state.persistent_spell_areas.{_environment_cast_id}.anchor_id",
                        "_caster_state_id",
                    ),
                    _when(
                        "_environment_profile.environment_mode == 'woodland' or "
                        "_environment_profile.environment_mode == 'minor_elementals'",
                        [
                            _set(
                                "state.persistent_spell_areas.{_environment_cast_id}.subject_"
                                "entity_id",
                                "_caster_state_id",
                            )
                        ],
                    ),
                    _set(
                        "state.persistent_spell_areas.{_environment_cast_id}.include_origin",
                        "include_origin",
                    ),
                    _set("state.persistent_spell_areas.{_environment_cast_id}.event_turn", -1),
                    _set(
                        "state.persistent_spell_areas.{_environment_cast_id}.event_clock", "'none'"
                    ),
                    _set(
                        "state.persistent_spell_areas.{_environment_cast_id}.event_target_ids", []
                    ),
                    _when(
                        "_environment_profile.environment_mode == 'animals' or "
                        "_environment_profile.environment_mode == 'elemental'",
                        [
                            _set("_environment_pack_id", "pack_id"),
                            _when(
                                "len(pack_id) == 0",
                                [
                                    _call(
                                        "format_srd_information_projection",
                                        {
                                            "message": (
                                                "{_caster_state_id}:{spell}:{state.lasting_serial}"
                                            )
                                        },
                                        {"_environment_pack_id": "_info_projection_message"},
                                    ),
                                ],
                            ),
                            _set(
                                "_environment_source_requires_sight",
                                "_environment_profile.environment_mode != 'elemental'",
                            ),
                            _when(
                                "_environment_profile.environment_mode == 'elemental'",
                                [
                                    _set("_environment_element_type", "'lightning'"),
                                    *[
                                        _when(
                                            f"element == '{choice}'",
                                            [_set("_environment_element_type", f"'{damage_type}'")],
                                        )
                                        for choice, damage_type in (
                                            ("earth", "thunder"),
                                            ("fire", "fire"),
                                            ("water", "cold"),
                                        )
                                    ],
                                    _set(
                                        "state.persistent_spell_areas.{_environment_cast_id}.elemen"
                                        "t",
                                        "element",
                                    ),
                                    _set(
                                        "state.persistent_spell_areas.{_environment_cast_id}.damage"
                                        "_type",
                                        "_environment_element_type",
                                    ),
                                ],
                            ),
                            _call(
                                "create_srd_source_actor_scene",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "summon_id": "{_environment_pack_id}",
                                    "spell": "{spell}",
                                    "range_feet": "{_environment_profile.range_feet}",
                                    "half_extent": "{_environment_profile.pack_half_extent}",
                                    "creature_type": "spell_force",
                                    "created_actor_position": "{_environment_cast_origin}",
                                    "created_actor_height": "{pack_height}",
                                    "created_actor_lighting": "{_environment_pack_lighting}",
                                    "requires_sight": "{_environment_source_requires_sight}",
                                },
                            ),
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_environment_pack_id}"},
                                POSITION_EXPORTS,
                            ),
                            _set(
                                "_environment_pack_source",
                                {
                                    "source_environment_id": "_environment_cast_id",
                                    "participant_ref": None,
                                },
                            ),
                            _set(
                                "_environment_pack_metadata",
                                "merge(_info_scene_entity.metadata, _environment_pack_source)",
                            ),
                            {
                                "spatial_mutation": {
                                    "scene": "state.spatial_scene",
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "expected_revision": "{state.spatial_scene.map.revision}",
                                    "target": {"kind": "entity", "id": "{_environment_pack_id}"},
                                    "patch": {
                                        "movement": "passable",
                                        "visibility": "clear",
                                        "metadata": "{_environment_pack_metadata}",
                                    },
                                },
                                "store_as": "_environment_pack_proxy",
                            },
                            _set(
                                "state.persistent_spell_areas.{_environment_cast_id}.anchor_id",
                                "_environment_pack_id",
                            ),
                            _set(
                                "state.persistent_spell_areas.{_environment_cast_id}.pack_mov"
                                "ement_serial",
                                0,
                            ),
                        ],
                    ),
                    _when(
                        "_environment_profile.environment_mode == 'woodland' or "
                        "_environment_profile.environment_mode == 'animals' or "
                        "_environment_profile.environment_mode == 'elemental'",
                        [
                            _call(
                                "select_srd_environment_targets",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "targets": "{force_targets}",
                                },
                            )
                        ],
                    ),
                ],
            ),
            _when(
                "(_environment_profile.environment_mode == 'grease' or "
                "_environment_profile.environment_mode == 'entangle')",
                [
                    _set(
                        "_environment_ground_volume",
                        {
                            "kind": "'box'",
                            "min": [
                                "origin[0] - _environment_profile.half_side_feet",
                                "origin[1] - _environment_profile.half_side_feet",
                                "origin[2]",
                            ],
                            "max": [
                                "origin[0] + _environment_profile.half_side_feet",
                                "origin[1] + _environment_profile.half_side_feet",
                                "origin[2] + 0.000001",
                            ],
                        },
                    ),
                    _set(
                        "state.persistent_spell_areas.{_environment_cast_id}.volume",
                        "_environment_ground_volume",
                    ),
                    _set(
                        "state.persistent_spell_areas.{_environment_cast_id}.ground_id",
                        "_environment_ground_id",
                    ),
                    _call(
                        "install_srd_environment_ground_zone", {"area_id": "{_environment_cast_id}"}
                    ),
                    _when(
                        "_environment_profile.environment_mode == 'grease'",
                        [_call("resolve_srd_grease_scene", {"area_id": "{_environment_cast_id}"})],
                        [
                            _call(
                                "resolve_srd_entangle_scene", {"area_id": "{_environment_cast_id}"}
                            )
                        ],
                    ),
                ],
            ),
            _call("refresh_srd_environment_scene", {}),
            _call("refresh_srd_environment_wind", {}),
            _set("_environment_summary", "'environment active'"),
            _when(
                "state.persistent_spell_areas[_environment_cast_id].active != true",
                [_set("_environment_summary", "'source ended during the cast'")],
            ),
            _when(
                "_environment_profile.environment_mode == 'fog' and "
                "caster_state.concentration_spell_id == null",
                [_set("_environment_summary", "'fog dispersed by strong wind'")],
            ),
            _call(
                "record_srd_spell_target_result",
                {
                    "target": "{_caster_state_id}",
                    "brief": "{spell}: {_environment_summary}",
                    ("detail"): (
                        "Source-sized area saved in the admitted scene's persistent registry."
                    ),
                },
                ("_spell_target_summary", "_spell_target_detail"),
            ),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _push("_spell_summary_rows", "_spell_target_summary"),
            _push("_spell_detail_rows", "_spell_target_detail"),
        ],
    )
    point = _environment_tool(
        "Read exact source-radius environment membership at an actual point.",
        [_array("point"), _param("plane")],
        [
            _call("refresh_srd_environment_wind", {}),
            _set("_environment_silence", False),
            _set("_environment_fog", False),
            _set("_environment_lightly_obscured", False),
            *_areas(
                _origin()
                + [
                    _interval("point", "point"),
                    _when(
                        (
                            "_environment_enabled == true and _environment_area.plane == plan"
                            "e and _sphere_hit == true"
                        ),
                        [
                            _when(
                                "_environment_area.mode == 'silence'",
                                [_set("_environment_silence", True)],
                            ),
                            _when(
                                "_environment_area.mode == 'insects'",
                                [_set("_environment_lightly_obscured", True)],
                            ),
                            _when(
                                _heavy_fog_mode("_environment_area"),
                                [_set("_environment_fog", True)],
                            ),
                            _when(
                                "_environment_area.mode == 'fog'",
                                [
                                    _call(
                                        "resolve_srd_environment_fog_segment",
                                        {
                                            "start": "{point}",
                                            "end": "{point}",
                                            "area_id": "{_environment_id}",
                                        },
                                        ("_fog_segment_present",),
                                    ),
                                    _when(
                                        "_fog_segment_present == true",
                                        [
                                            _set("_environment_fog", True),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )
    segment = _environment_tool(
        "Read source-owned fog and sound crossings on the exact scene segment.",
        [_array("start"), _array("end"), _param("plane")],
        [
            _call("refresh_srd_environment_wind", {}),
            _set("_environment_sound_blocked", False),
            _set("_environment_sight_obscured", False),
            _set("_environment_lightly_obscured", False),
            *_areas(
                _origin()
                + [
                    _interval("start", "end"),
                    _when(
                        (
                            "_environment_enabled == true and _environment_area.plane == plan"
                            "e and _sphere_hit == true"
                        ),
                        [
                            _when(
                                "_environment_area.mode == 'silence'",
                                [_set("_environment_sound_blocked", True)],
                            ),
                            _when(
                                "_environment_area.mode == 'insects'",
                                [_set("_environment_lightly_obscured", True)],
                            ),
                            _when(
                                _heavy_fog_mode("_environment_area"),
                                [_set("_environment_sight_obscured", True)],
                            ),
                            _when(
                                "_environment_area.mode == 'fog'",
                                [
                                    _call(
                                        "resolve_srd_environment_fog_segment",
                                        {
                                            "start": "{start}",
                                            "end": "{end}",
                                            "area_id": "{_environment_id}",
                                        },
                                        ("_fog_segment_present",),
                                    ),
                                    _when(
                                        "_fog_segment_present == true",
                                        [
                                            _set("_environment_sight_obscured", True),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )
    actor = _environment_tool(
        "Derive Silence's entire-creature boundary from the admitted footprint and height.",
        [_entity("target_state")],
        [
            _set("_environment_entirely_silenced", False),
            _set("_environment_silence_expires", 0),
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _set("_environment_subject", "_info_scene_entity"),
            _set("_environment_subject_position", "_info_position"),
            *_areas(
                _origin()
                + [
                    _when(
                        (
                            "_environment_area.mode == 'silence' and _environment_area.plane "
                            "== _environment_subject.metadata.plane"
                        ),
                        [
                            _set("_environment_extent_x", "_environment_subject.footprint.radius"),
                            _set("_environment_extent_y", "_environment_subject.footprint.radius"),
                            _when(
                                "_environment_subject.footprint.kind == 'box'",
                                [
                                    _set(
                                        "_environment_extent_x",
                                        "_environment_subject.footprint.half_extents[0]",
                                    ),
                                    _set(
                                        "_environment_extent_y",
                                        "_environment_subject.footprint.half_extents[1]",
                                    ),
                                ],
                            ),
                            _set(
                                "_environment_far_x",
                                (
                                    "abs(_environment_subject_position[0] - _environment_"
                                    "origin[0]) + _environment_extent_x"
                                ),
                            ),
                            _set(
                                "_environment_far_y",
                                (
                                    "abs(_environment_subject_position[1] - _environment_"
                                    "origin[1]) + _environment_extent_y"
                                ),
                            ),
                            _set(
                                "_environment_far_z",
                                (
                                    "max(abs(_environment_subject_position[2] - _environm"
                                    "ent_origin[2]), abs(_environment_subject_position[2]"
                                    " + _environment_subject.height - _environment_origin"
                                    "[2]))"
                                ),
                            ),
                            _set(
                                "_environment_horizontal_squared",
                                "_environment_far_x ** 2 + _environment_far_y ** 2",
                            ),
                            _when(
                                "_environment_subject.footprint.kind == 'circle'",
                                [
                                    _set(
                                        "_environment_horizontal_squared",
                                        (
                                            "(((_environment_subject_position[0] - _envir"
                                            "onment_origin[0]) ** 2 + (_environment_subje"
                                            "ct_position[1] - _environment_origin[1]) ** "
                                            "2) ** 0.5 + _environment_subject.footprint.r"
                                            "adius) ** 2"
                                        ),
                                    ),
                                ],
                            ),
                            _when(
                                (
                                    "_environment_horizontal_squared + _environment_far_z ** "
                                    "2 <= _environment_area.radius ** 2"
                                ),
                                [
                                    _set("_environment_entirely_silenced", True),
                                    _set(
                                        "_environment_silence_expires",
                                        (
                                            "max(_environment_silence_expires, _environme"
                                            "nt_area.expires_tick)"
                                        ),
                                    ),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )
    remove = _environment_tool(
        "Retire only the exact environment source belonging to this concentration instance.",
        [_entity("caster_state")],
        [
            _when(
                "caster_state.environment_area_id != null",
                [
                    _set("_environment_remove_id", "caster_state.environment_area_id"),
                    _call(
                        "retire_srd_environment_ground_source",
                        {"area_id": "{_environment_remove_id}"},
                    ),
                    _set("caster_state.environment_area_id", None),
                    _call("retire_srd_environment_silence_scene", {}),
                    _call("prune_srd_affinity_anchors", {"lasting_state": "state"}),
                    _set("_environment_remaining_ids", "state.persistent_spell_area_ids"),
                    _set("_environment_still_active", False),
                    _each(
                        "_environment_remaining_ids",
                        "_environment_remaining_id",
                        [
                            _when(
                                (
                                    "state.persistent_spell_areas[_environment_remaining_"
                                    "id].profile == 'environment'"
                                ),
                                [_set("_environment_still_active", True)],
                            ),
                        ],
                    ),
                    _set("state.has_spell_environments", "_environment_still_active"),
                ],
            ),
        ],
    )
    disperse = _public(
        _environment_tool(
            "Disperse an actual fog source using a strong-wind zone admitted to the scene.",
            [_param("area_id"), _param("wind_zone_id")],
            [
                _set("_environment_area", "state.persistent_spell_areas[area_id]"),
                _require(
                    (
                        "_environment_area.profile == 'environment' and "
                        + _wind_fog_mode("_environment_area")
                        + " and _environment_area.active == true"
                    ),
                    "Select an active fog spell source.",
                ),
                _set("_environment_wind_found", False),
                _set("_environment_wind_zones", "state.spatial_scene.zones"),
                _each(
                    "_environment_wind_zones",
                    "_environment_wind_zone",
                    [
                        _when(
                            (
                                "_environment_wind_zone.id == wind_zone_id and _environment_w"
                                "ind_zone.metadata.strong_wind == true and _environment_wind_"
                                "zone.metadata.plane == _environment_area.plane"
                            ),
                            [
                                _when(
                                    "_environment_wind_zone.shape.kind == 'box'",
                                    [
                                        _set(
                                            "_environment_wind_nearest",
                                            [
                                                f"max(_environment_wind_zone.shape.min[{i}], "
                                                f"min(_environment_area.origin[{i}], "
                                                f"_environment_wind_zone.shape.max[{i}]))"
                                                for i in range(3)
                                            ],
                                        ),
                                        _call(
                                            "measure_srd_information_distance",
                                            {
                                                "start": "{_environment_area.origin}",
                                                "end": "{_environment_wind_nearest}",
                                            },
                                            ("_scene_distance",),
                                        ),
                                    ],
                                    [
                                        _require(
                                            "_environment_wind_zone.shape.kind == 'cylinder'",
                                            "Use the actual admitted strong-wind zone extent.",
                                        ),
                                        _set(
                                            "_environment_wind_horizontal",
                                            (
                                                "max(0, ((_environment_area.origin[0] - _"
                                                "environment_wind_zone.shape.center[0]) *"
                                                "* 2 + (_environment_area.origin[1] - _en"
                                                "vironment_wind_zone.shape.center[1]) ** "
                                                "2) ** 0.5 - _environment_wind_zone.shape"
                                                ".radius)"
                                            ),
                                        ),
                                        _set(
                                            "_environment_wind_vertical",
                                            (
                                                "max(0, _environment_wind_zone.shape.cent"
                                                "er[2] - _environment_area.origin[2], _en"
                                                "vironment_area.origin[2] - _environment_"
                                                "wind_zone.shape.center[2] - _environment"
                                                "_wind_zone.shape.height)"
                                            ),
                                        ),
                                        _set(
                                            "_scene_distance",
                                            (
                                                "(_environment_wind_horizontal ** 2 + _en"
                                                "vironment_wind_vertical ** 2) ** 0.5"
                                            ),
                                        ),
                                    ],
                                ),
                                _when(
                                    "_scene_distance <= _environment_area.radius",
                                    [_set("_environment_wind_found", True)],
                                ),
                            ],
                        ),
                    ],
                ),
                _require(
                    "_environment_wind_found == true",
                    "An actual strong wind must overlap this fog.",
                ),
                _call(
                    "end_srd_concentration_node",
                    {
                        "source_state": "{_environment_area.caster}",
                        "target_state": "{_environment_area.caster}",
                        "lasting_state": "state",
                    },
                ),
            ],
            public=True,
        ),
        "exploration",
        "Fog dispersed",
        [("Source", "area_id")],
    )
    wind = deepcopy(disperse)
    wind["description"] = "Qualify actual strong-wind overlap against one active Fog Cloud source."
    wind["parameters"] = [
        _param("area_id"),
        _optional("wind_zone_id", default=""),
        *[deepcopy(p) for p in disperse["parameters"] if p["name"] == "lasting_state"],
    ]
    wind["effects"] = wind["effects"][:-2]
    wind.pop("result_card", None)
    wind["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    for effects in _effect_lists(wind["effects"]):
        for effect in effects:
            if "_environment_wind_zone.id == wind_zone_id and" in effect.get("condition", ""):
                effect["condition"] = effect["condition"].replace(
                    "_environment_wind_zone.id == wind_zone_id and",
                    "(wind_zone_id == '' or _environment_wind_zone.id == wind_zone_id) and",
                )
    return {
        **_fog_clipping_tools(),
        **_sphere_hazard_tools(),
        **_spike_growth_tools(),
        **_spirit_guardians_tools(),
        **_cloudkill_tools(),
        **_moonbeam_tools(),
        **_stinking_cloud_tools(),
        **_sleet_tools(),
        **_black_tentacles_tools(),
        **_blade_barrier_tools(),
        **_wall_of_fire_tools(),
        "resolve_srd_wall_of_fire_opacity": _wall_of_fire_opacity_tool(),
        **_web_tools(),
        **_emanation_geometry_tools(),
        **_conjure_tools(),
        **_minor_terrain_tools(),
        **_animals_tools(),
        **_elemental_tools(),
        **_conjure_entry_tools(),
        **_ground_tools(),
        **_entangle_tools(),
        **_terrain_tools(),
        "cast_spell_environment": cast,
        "resolve_srd_environment_point": point,
        "resolve_srd_environment_segment": segment,
        "resolve_srd_environment_actor": actor,
        "remove_srd_environment_concentration": remove,
        "disperse_srd_fog": disperse,
        "qualify_srd_environment_wind": wind,
        "refresh_srd_environment_wind": _environment_tool(
            "Disperse Fog Cloud when actual admitted strong wind overlaps its source extent.",
            [],
            _areas(
                [
                    _when(
                        _wind_fog_mode("_environment_area"),
                        [
                            _set("_environment_wind_caster", "_environment_area.caster"),
                            _call(
                                "qualify_srd_environment_wind",
                                {"area_id": "{_environment_id}"},
                                ("_environment_wind_found",),
                            ),
                            _when(
                                "_environment_wind_found == true",
                                [
                                    _call(
                                        "end_srd_concentration_node",
                                        {
                                            "source_state": "{_environment_wind_caster}",
                                            "target_state": "{_environment_wind_caster}",
                                            "lasting_state": "state",
                                        },
                                    )
                                ],
                            ),
                        ],
                    )
                ]
            ),
        ),
        "refresh_srd_environment_actor": _environment_tool(
            "Project source-owned Deafened while the actual creature is entirely in Silence.",
            [_entity("target_state")],
            [
                _call(
                    "resolve_srd_environment_actor",
                    {"target_state": "{_target_state_id}"},
                    ("_environment_entirely_silenced", "_environment_silence_expires"),
                ),
                _when(
                    "target_state.environment_silence_condition_id != null",
                    [
                        _set(
                            "_environment_condition",
                            (
                                "state.timed_condition_sources[target_state.environment_silen"
                                "ce_condition_id]"
                            ),
                        ),
                        _when(
                            "_environment_entirely_silenced == true and "
                            "_environment_condition.installed == true",
                            [
                                _set(
                                    "state.timed_condition_sources.{target_state.environment_"
                                    "silence_condition_id}.expires_tick",
                                    "_environment_silence_expires",
                                )
                            ],
                        ),
                        _when(
                            (
                                "_environment_entirely_silenced != true or _environment_condi"
                                "tion.installed != true"
                            ),
                            [
                                _call(
                                    "retire_srd_timed_condition_source",
                                    {
                                        ("source_id"): (
                                            "{target_state.environment_silence_condition_id}"
                                        ),
                                        "lasting_state": "state",
                                    },
                                ),
                                _set("target_state.environment_silence_condition_id", None),
                            ],
                        ),
                    ],
                ),
                _when(
                    (
                        "_environment_entirely_silenced == true and target_state.environment_"
                        "silence_condition_id == null"
                    ),
                    [
                        _set(
                            "_environment_condition_duration",
                            "ceil((_environment_silence_expires - state.lasting_tick) * 0.5)",
                        ),
                        _call(
                            "install_srd_environment_deafened",
                            {
                                "target_state": "{_target_state_id}",
                                "lasting_state": "state",
                                "spell": "silence",
                                "condition": "deafened",
                                "save_dc": 1,
                                "duration_rounds": "{_environment_condition_duration}",
                                "repeat_save_timing": "none",
                            },
                            ("_timed_condition_source_id",),
                        ),
                        _set(
                            "target_state.environment_silence_condition_id",
                            "_timed_condition_source_id",
                        ),
                    ],
                ),
                _call(
                    "refresh_srd_timed_condition_target",
                    {"target_state": "{_target_state_id}", "lasting_state": "state"},
                ),
            ],
        ),
        "refresh_srd_environment_scene": _environment_tool(
            "Reproject the actual scene's creature conditions after an environment source changes.",
            [],
            [
                _set("_environment_scene_entities", "state.spatial_scene.entities"),
                _each(
                    "_environment_scene_entities",
                    "_environment_scene_entity",
                    [
                        _when(
                            (
                                "_environment_scene_entity.active == true and _environment_sc"
                                "ene_entity.metadata.participant_ref != null"
                            ),
                            [
                                _call(
                                    "refresh_srd_environment_actor",
                                    {
                                        ("target_state"): (
                                            "{_environment_scene_entity.metadata.participant_ref}"
                                        )
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "retire_srd_environment_silence_actor": _environment_tool(
            "Remove an exited or ended Silence condition without allocating another source.",
            [_entity("target_state")],
            [
                _when(
                    "target_state.environment_silence_condition_id != null",
                    [
                        _call(
                            "resolve_srd_environment_actor",
                            {"target_state": "{_target_state_id}"},
                            ("_environment_entirely_silenced", "_environment_silence_expires"),
                        ),
                        _when(
                            "_environment_entirely_silenced == true",
                            [
                                _set(
                                    "state.timed_condition_sources.{target_state.environment_"
                                    "silence_condition_id}.expires_tick",
                                    "_environment_silence_expires",
                                )
                            ],
                        ),
                        _when(
                            "_environment_entirely_silenced != true",
                            [
                                _call(
                                    "retire_srd_timed_condition_source",
                                    {
                                        ("source_id"): (
                                            "{target_state.environment_silence_condition_id}"
                                        ),
                                        "lasting_state": "state",
                                    },
                                ),
                                _set("target_state.environment_silence_condition_id", None),
                                _call(
                                    "refresh_srd_timed_condition_target",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "lasting_state": "state",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "retire_srd_environment_silence_scene": _environment_tool(
            (
                "Retire affected scene conditions after source removal without installing or "
                "ending concentration recursively."
            ),
            [],
            [
                _set("_environment_retire_entities", "state.spatial_scene.entities"),
                _each(
                    "_environment_retire_entities",
                    "_environment_retire_entity",
                    [
                        _when(
                            (
                                "_environment_retire_entity.active == true and _environment_r"
                                "etire_entity.metadata.participant_ref != null"
                            ),
                            [
                                _call(
                                    "retire_srd_environment_silence_actor",
                                    {
                                        ("target_state"): (
                                            "{_environment_retire_entity.metadata.participant_ref}"
                                        )
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "refresh_srd_environment_lifecycle": _environment_tool(
            "Expire the exact environment concentration before persistent-area pruning.",
            [],
            [
                _when(
                    "state.has_spell_environments == true",
                    [
                        _set("_environment_lifecycle_ids", "state.persistent_spell_area_ids"),
                        _set("_environment_live", False),
                        _each(
                            "_environment_lifecycle_ids",
                            "_environment_lifecycle_id",
                            [
                                _set(
                                    "_environment_lifecycle",
                                    "state.persistent_spell_areas[_environment_lifecycle_id]",
                                ),
                                _when(
                                    (
                                        "_environment_lifecycle.profile == 'environment' and "
                                        "_environment_lifecycle.active == true"
                                    ),
                                    [
                                        _when(
                                            (
                                                "state.lasting_tick >= _environment_lifec"
                                                "ycle.expires_tick"
                                            ),
                                            [
                                                _when(
                                                    "_environment_lifecycle.concentration == false",
                                                    [
                                                        _call(
                                                            "retire_srd_environment_ground_source",
                                                            {
                                                                "area_id": (
                                                                    "{_environment_lifecycle_id}"
                                                                ),
                                                            },
                                                        )
                                                    ],
                                                    [
                                                        _call(
                                                            "end_srd_concentration_node",
                                                            {
                                                                ("source_state"): (
                                                                    "{_environment_lifecycle."
                                                                    "caster}"
                                                                ),
                                                                ("target_state"): (
                                                                    "{_environment_lifecycle."
                                                                    "caster}"
                                                                ),
                                                                "lasting_state": "state",
                                                            },
                                                        ),
                                                    ],
                                                ),
                                            ],
                                            [_set("_environment_live", True)],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        _set("state.has_spell_environments", "_environment_live"),
                    ],
                ),
            ],
        ),
    }


def _dispel_tools(tools):
    """Compose selected effect removal with the recovery owner's real source-level check."""
    if "resolve_srd_dispel_source_check" not in tools:
        return {}
    check_row = {"source": "area_id", "roll": None, "ended": "_dispel_succeeded"}
    return {
        "qualify_srd_environment_concentration": _environment_tool(
            "Require the concentrated source to match its actual caster's current area.",
            [_entity("source_state"), _param("area_id")],
            [
                _require(
                    "source_state.environment_area_id == area_id and "
                    "source_state.concentration_effect_family == 'spell_environment'",
                    "The selected environment is no longer this caster's concentration.",
                )
            ],
        ),
        "dispel_srd_environment_source": _environment_tool(
            "Dispel one actual selected environment through its saved level and source geometry.",
            [
                _entity("caster_state"),
                _param("area_id"),
                _param("slot_level", "integer"),
                _param("casting_modifier", "integer"),
                _param("casting_ability"),
                _param("caster", resolve="participant", required=False),
                _optional("point", "array", [], items={"type": "number"}, max_items=3),
            ],
            [
                _set("_environment_dispel_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_environment_dispel_source.profile == 'environment' and "
                    "_environment_dispel_source.active == true and "
                    "_environment_dispel_source.dispel_ongoing == true and "
                    "state.lasting_tick < _environment_dispel_source.expires_tick",
                    "Choose an actual active ongoing environment source.",
                ),
                _set("_environment_dispel_point", "_environment_dispel_source.origin"),
                _set("_environment_dispel_plane", "_environment_dispel_source.plane"),
                _set("_environment_dispel_emanation", False),
                _when(
                    "_environment_dispel_source.mode == 'woodland' or "
                    "_environment_dispel_source.mode == 'minor_elementals' or "
                    "_environment_dispel_source.mode == 'animals' or "
                    "_environment_dispel_source.mode == 'elemental'",
                    [
                        _set("_environment_dispel_emanation", True),
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_environment_dispel_source.anchor_id}"},
                            POSITION_EXPORTS,
                        ),
                        _set("_environment_dispel_point", "_info_position"),
                        _set("_environment_dispel_plane", "_info_scene_entity.metadata.plane"),
                    ],
                ),
                _when(
                    "len(point) > 0",
                    [
                        _require(
                            "len(point) == 3", "Choose an actual three-dimensional effect point."
                        ),
                        _set("_environment_dispel_point", "point"),
                    ],
                ),
                _when(
                    "_environment_dispel_source.volume != null",
                    [
                        _call(
                            "srd_information_segment_interval",
                            {
                                "start": "{_environment_dispel_point}",
                                "end": "{_environment_dispel_point}",
                                "volume": "{_environment_dispel_source.volume}",
                            },
                            ("_segment_hit",),
                        ),
                        _require(
                            "_segment_hit == true", "The chosen point is outside that effect."
                        ),
                        _set("_environment_dispel_zone_found", False),
                        _set("_environment_dispel_zones", "state.spatial_scene.zones"),
                        _each(
                            "_environment_dispel_zones",
                            "_environment_dispel_zone",
                            [
                                _when(
                                    "_environment_dispel_zone.metadata.source_environment_id "
                                    "== area_id "
                                    "and _environment_dispel_zone.metadata.plane == "
                                    "_environment_dispel_source.plane",
                                    [
                                        _call(
                                            "srd_information_segment_interval",
                                            {
                                                "start": "{_environment_dispel_point}",
                                                "end": "{_environment_dispel_point}",
                                                "volume": "{_environment_dispel_zone.shape}",
                                            },
                                            ("_segment_hit",),
                                        ),
                                        _when(
                                            "_segment_hit == true",
                                            [_set("_environment_dispel_zone_found", True)],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        _require(
                            "_environment_dispel_zone_found == true",
                            "The selected source needs its actual admitted terrain zone.",
                        ),
                    ],
                    [
                        _when(
                            "_environment_dispel_emanation == true",
                            [
                                _call(
                                    "resolve_srd_environment_body_point",
                                    {
                                        "anchor_subject": "{_environment_dispel_source.anchor_id}",
                                        "point": "{_environment_dispel_point}",
                                        "radius": "{_environment_dispel_source.radius}",
                                    },
                                    {"_sphere_hit": "_body_point_inside"},
                                ),
                            ],
                            [
                                _call(
                                    "resolve_srd_persistent_area_segment",
                                    {
                                        "start": "{_environment_dispel_point}",
                                        "end": "{_environment_dispel_point}",
                                        "origin": "{_environment_dispel_source.origin}",
                                        "radius": "{_environment_dispel_source.radius}",
                                    },
                                    ("_sphere_hit",),
                                ),
                            ],
                        ),
                        _require("_sphere_hit == true", "The chosen point is outside that effect."),
                        _when(
                            "_environment_dispel_source.mode == 'fog'",
                            [
                                _call(
                                    "resolve_srd_environment_fog_segment",
                                    {
                                        "start": "{_environment_dispel_point}",
                                        "end": "{_environment_dispel_point}",
                                        "area_id": "{area_id}",
                                    },
                                    ("_fog_segment_present",),
                                ),
                                _require(
                                    "_fog_segment_present == true",
                                    "The selected point needs actual remaining Fog.",
                                ),
                            ],
                        ),
                    ],
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_caster_state_id}"},
                    POSITION_EXPORTS,
                ),
                _require(
                    "_info_scene_entity.metadata.plane == _environment_dispel_plane",
                    "The selected effect must be on the caster's actual plane.",
                ),
                _call(
                    "measure_srd_information_distance",
                    {
                        "start": "{_info_position}",
                        "end": "{_environment_dispel_point}",
                    },
                    ("_scene_distance",),
                ),
                _require(
                    "_scene_distance <= 120", "The selected effect is beyond Dispel Magic's range."
                ),
                _call(
                    "srd_information_barriers",
                    {
                        "start": "{_info_position}",
                        "end": "{_environment_dispel_point}",
                        "rule": "solid",
                    },
                    ("_information_blocked",),
                ),
                _require(
                    "_information_blocked != true", "A solid barrier blocks the selected effect."
                ),
                _when(
                    "_environment_dispel_source.concentration == true",
                    [
                        _call(
                            "qualify_srd_environment_concentration",
                            {
                                "source_state": "{_environment_dispel_source.caster}",
                                "area_id": "{area_id}",
                            },
                        ),
                    ],
                ),
                _call(
                    "resolve_srd_dispel_source_check",
                    {
                        "caster_state": "{_caster_state_id}",
                        "caster": "{_caster_id}",
                        "slot_level": "{slot_level}",
                        "source_level": "{_environment_dispel_source.spell_level}",
                        "casting_modifier": "{casting_modifier}",
                        "casting_ability": "{casting_ability}",
                    },
                    ("_dispel_succeeded", "_dispel_roll"),
                ),
                _when(
                    "_dispel_succeeded == true",
                    [
                        _when(
                            "_environment_dispel_source.concentration == true",
                            [
                                _call(
                                    "end_srd_concentration_node",
                                    {
                                        "source_state": "{_environment_dispel_source.caster}",
                                        "target_state": "{_environment_dispel_source.caster}",
                                        "lasting_state": "state",
                                    },
                                ),
                            ],
                            [
                                _call(
                                    "retire_srd_environment_ground_source", {"area_id": "{area_id}"}
                                ),
                                _call("prune_srd_affinity_anchors", {"lasting_state": "state"}),
                                _call("refresh_srd_environment_lifecycle", {}),
                            ],
                        ),
                    ],
                ),
                _set("_environment_dispel_row", check_row),
                _when(
                    "_dispel_roll != null",
                    [_set("_environment_dispel_row", {**check_row, "roll": "_dispel_roll"})],
                ),
                _set("_environment_dispel_checks", []),
                _push("_environment_dispel_checks", "_environment_dispel_row"),
            ],
        ),
    }


def extend_runtime(text):
    """Compose environment consequences at existing casting, sight, sound and damage owners."""
    tools = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))["tools"]
    changed = {**_tools(), **_dispel_tools(tools)}
    _bind_animals_events(changed)
    d20 = deepcopy(tools["d20_test"])
    _bind_animals_strength_d20(d20)
    changed["d20_test"] = d20
    barriers = deepcopy(tools["srd_information_barriers"])
    for effects in _effect_lists(barriers["effects"]):
        for effect in effects:
            if effect.get("call") == "srd_information_segment_interval":
                effect["export_context"].update(
                    _segment_entry="_segment_entry", _segment_exit="_segment_exit"
                )
            barrier_condition = "_segment_hit == true and _segment_thickness > 0.000000000001"
            if effect.get("condition") in (
                barrier_condition,
                barrier_condition + " and _segment_entry != null",
            ):
                original = effect["on_true"]
                if any(
                    e.get("set", {}).get("field") in ("_environment_barrier_midpoint", "_p")
                    for e in original
                ):
                    # Recompose only this owner's previously emitted interior wrapper.
                    # Its final true branch is the existing material/rule barrier owner.
                    original = original[-1]["on_true"]
                effect["on_true"] = [
                    _require(
                        "_barrier.volume.kind == 'box' or _barrier.volume.kind == 'cylinder'",
                        "Solid barrier tangency needs supported admitted box or cylinder geometry.",
                    ),
                    _set(
                        "_p",
                        [
                            f"start[{i}] + (end[{i}] - start[{i}]) * "
                            "(_segment_entry + _segment_exit) * 0.5"
                            for i in range(3)
                        ],
                    ),
                    _when(
                        # p is the actual interval midpoint. A single conditional keeps
                        # the established box path's operation cost unchanged.
                        "(_barrier.volume.kind == 'box' and min("
                        + ", ".join(
                            [f"_p[{i}] - _barrier.volume.min[{i}]" for i in range(3)]
                            + [f"_barrier.volume.max[{i}] - _p[{i}]" for i in range(3)]
                        )
                        + ") > 0) or (_barrier.volume.kind == 'cylinder' and "
                        "(_p[0] - _barrier.volume.center[0]) ** 2 + "
                        "(_p[1] - _barrier.volume.center[1]) ** 2 < _barrier.volume.radius ** 2 "
                        "and _p[2] > _barrier.volume.center[2] and "
                        "_p[2] < _barrier.volume.center[2] + _barrier.volume.height)",
                        original,
                    ),
                ]
                effect["condition"] = barrier_condition + " and _segment_entry != null"
    changed["srd_information_barriers"] = barriers
    if any(p["name"] == "plane" for p in barriers["parameters"]):
        for effects in _effect_lists(changed["cast_spell_environment"]["effects"]):
            for effect in effects:
                if effect.get("call") == "srd_information_barriers":
                    effect["params"]["plane"] = "{_environment_plane}"
        for effects in _effect_lists(changed["destroy_srd_environment_fog"]["effects"]):
            for effect in effects:
                if effect.get("call") == "srd_information_barriers":
                    effect["params"]["plane"] = "{record.plane}"
        if "dispel_srd_environment_source" in changed:
            for effects in _effect_lists(changed["dispel_srd_environment_source"]["effects"]):
                for effect in effects:
                    if effect.get("call") == "srd_information_barriers":
                        effect["params"]["plane"] = "{_environment_dispel_plane}"
    if "cast_srd_recovery" in tools:
        recovery = deepcopy(tools["cast_srd_recovery"])
        for effects in _effect_lists(recovery["effects"]):
            for effect in effects:
                if (
                    effect.get("condition") == "spell == 'dispel_magic'"
                    and "dispel_srd_target_sources" in str(effect)
                    and "dispel_srd_environment_source" not in str(effect)
                ):
                    effect["on_true"] = [
                        _when(
                            "recovery_choice != 'environment'",
                            effect["on_true"],
                            [
                                _call(
                                    "dispel_srd_environment_source",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "caster": "{_caster_id}",
                                        "area_id": "{recovery_source_id}",
                                        "point": "{recovery_position}",
                                        "slot_level": "{slot_level}",
                                        "casting_modifier": "{casting_modifier}",
                                        "casting_ability": "{casting_ability}",
                                    },
                                    ("_environment_dispel_checks",),
                                ),
                                _set("_dispel_checks", "_environment_dispel_checks"),
                            ],
                        )
                    ]
        changed["cast_srd_recovery"] = recovery
    # Deafened cannot incapacitate. Reuse the condition owner's allocation/projection while
    # excluding its irrelevant concentration-ending branch from the static call graph.
    install = deepcopy(tools["install_srd_timed_condition_source"])
    install["description"] = (
        "Install Deafened through the established condition pool; Deafened never ends con"
        "centration."
    )
    install["effects"] = [
        e for e in install["effects"] if "end_srd_concentration_node" not in str(e)
    ]
    next(p for p in install["parameters"] if p["name"] == "condition")["enum_values"] = ["deafened"]
    changed["install_srd_environment_deafened"] = install
    poison_install = deepcopy(install)
    poison_install["description"] = (
        "Install Poisoned through the existing condition pool without incapacitation."
    )
    next(p for p in poison_install["parameters"] if p["name"] == "condition")["enum_values"] = [
        "poisoned"
    ]
    changed["install_srd_environment_poisoned"] = poison_install
    restraint_install = deepcopy(install)
    restraint_install["description"] = (
        "Install Restrained through the existing condition pool without incapacitation."
    )
    next(p for p in restraint_install["parameters"] if p["name"] == "condition")["enum_values"] = [
        "restrained"
    ]
    restraint_install["effects"].insert(
        0,
        _when(
            "target_state.timed_condition_unowned_restrained == null",
            [
                _set(
                    "target_state.timed_condition_unowned_restrained",
                    "'restrained' in target_state.conditions",
                )
            ],
        ),
    )
    changed["install_srd_environment_restrained"] = restraint_install
    projection = deepcopy(tools["refresh_srd_timed_condition_target"])
    if "_timed_restrained" not in str(projection["effects"]):
        projection["effects"].insert(0, _set("_timed_restrained", False))
        for effects in _effect_lists(projection["effects"]):
            for effect in effects:
                if effect.get("condition") == "_timed_condition_source.condition == 'paralyzed'":
                    effects.append(
                        _when(
                            "_timed_condition_source.condition == 'restrained'",
                            [_set("_timed_restrained", True)],
                        )
                    )
        i = next(
            i
            for i, e in enumerate(projection["effects"])
            if e.get("call") == "refresh_srd_movement_budget"
        )
        projection["effects"][i:i] = [
            _when(
                "target_state.timed_condition_unowned_restrained == null",
                [
                    _set(
                        "target_state.timed_condition_unowned_restrained",
                        "'restrained' in target_state.conditions",
                    )
                ],
            ),
            {"remove_condition": {"target": "target_state", "condition": "restrained"}},
            _when(
                "target_state.timed_condition_unowned_restrained == true or "
                "_timed_restrained == true",
                [{"apply_condition": {"target": "target_state", "condition": "restrained"}}],
            ),
            _set("target_state.timed_condition_restrained", "_timed_restrained"),
        ]
    if "_timed_deafened_other" not in str(projection["effects"]):
        projection["effects"].insert(0, _set("_timed_deafened_other", False))
        for effects in _effect_lists(projection["effects"]):
            for effect in effects:
                if (
                    effect.get("condition") == "_timed_condition_source.condition == 'blinded' and "
                    "_timed_condition_source.spell_id != 'blindness_deafness'"
                ):
                    effects.append(
                        _when(
                            "_timed_condition_source.condition == 'deafened' and "
                            "_timed_condition_source.spell_id != 'blindness_deafness'",
                            [_set("_timed_deafened_other", True)],
                        )
                    )
                if "target_state.timed_condition_unowned_deafened == true or" in effect.get(
                    "condition", ""
                ):
                    effect["condition"] += " or _timed_deafened_other == true"
    projection["effects"] = [
        e
        for e in projection["effects"]
        if e.get("condition") != "target_state.stinking_cloud_managed_poison == true"
    ]
    progressive = next(
        e
        for e in projection["effects"]
        if "resolve_srd_progressive_projection" in str(e.get("on_true", []))
    )
    if "project_srd_stinking_cloud_poison" not in str(progressive):
        progressive["condition"] = (
            "target_state.has_progressive_sources == true or "
            "target_state.stinking_cloud_managed_poison == true"
        )
        progressive["on_true"] = [
            _when("target_state.has_progressive_sources == true", progressive["on_true"]),
            _when(
                "target_state.stinking_cloud_managed_poison == true",
                [
                    _call(
                        "project_srd_stinking_cloud_poison", {"target_state": "{_target_state_id}"}
                    )
                ],
            ),
        ]
    changed["refresh_srd_timed_condition_target"] = projection
    cast = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in cast["parameters"]}
    choices = [
        _optional(
            "environment_origin",
            "array",
            [0, 0, 0],
            items={"type": "number"},
            min_items=3,
            max_items=3,
        ),
        _optional("environment_force_targets", "array", [], items={"type": "string"}, max_items=64),
        _optional("environment_include_origin", "boolean", False),
        _optional(
            "environment_unaffected_targets", "array", [], items={"type": "string"}, max_items=64
        ),
        _optional(
            "environment_spirit_appearance", "string", "angelic", enum_values=["angelic", "fey"]
        ),
        _optional("environment_pack_id", "string", ""),
        _optional("environment_pack_height", "number", None, minimum=0.01, maximum=1000),
        _optional("environment_cylinder_face", "string", "bottom", enum_values=["bottom", "top"]),
        _optional("environment_wall_length", "number", 100, minimum=0.01, maximum=100),
        _optional("environment_fire_wall_length", "number", 60, minimum=0.01, maximum=60),
        _optional(
            "environment_fire_wall_side", "string", "positive", enum_values=["positive", "negative"]
        ),
        _optional(
            "environment_wall_axis",
            "string",
            "east_west",
            enum_values=["east_west", "north_south"],
        ),
        _optional(
            "environment_element", "string", "air", enum_values=["air", "earth", "fire", "water"]
        ),
    ]
    cast["parameters"] += [p for p in choices if p["name"] not in names]
    for effects in _effect_lists(cast["effects"]):
        effects[:] = [
            e
            for e in effects
            if "_spell_definition.family == 'spell_environment'" not in e.get("condition", "")
        ]
    branch = _when(
        "_spell_definition.family == 'spell_environment'",
        [
            _call(
                "cast_spell_environment",
                {
                    "spell": "{spell}",
                    "caster_state": "{_caster_state_id}",
                    "slot_level": "{_slot_level}",
                    "concentration_save_modifier": "{_casting_profile.concentration_save_modifier}",
                    "save_dc": "{_casting_profile.save_dc}",
                    "origin": "{environment_origin}",
                    "force_targets": "{environment_force_targets}",
                    "include_origin": "{environment_include_origin}",
                    "unaffected_targets": "{environment_unaffected_targets}",
                    "spirit_appearance": "{environment_spirit_appearance}",
                    "pack_id": "{environment_pack_id}",
                    "pack_height": "{environment_pack_height}",
                    "cylinder_face": "{environment_cylinder_face}",
                    "wall_length": "{environment_wall_length}",
                    "fire_wall_length": "{environment_fire_wall_length}",
                    "fire_wall_side": "{environment_fire_wall_side}",
                    "wall_axis": "{environment_wall_axis}",
                    "element": "{environment_element}",
                },
                ("_spell_summary_rows", "_spell_detail_rows"),
            ),
        ],
    )
    last = max(
        i
        for i, e in enumerate(cast["effects"])
        if "_spell_definition.family ==" in e.get("condition", "")
    )
    cast["effects"].insert(last + 1, branch)
    changed["cast_spell"] = cast
    if "qualify_srd_environment_hearing" not in str(cast["effects"]):
        hearing_positions = [
            i
            for i, e in enumerate(cast["effects"])
            if e.get("set", {}).get("field") == "_caster_can_hear_target"
            or any(
                x.get("set", {}).get("field") == "_caster_can_hear_target"
                for x in e.get("on_true", [])
            )
        ]
        index = max(hearing_positions) + 1
        cast["effects"].insert(
            index,
            _when(
                "state.has_spell_environments == true and _scalar_target_supplied == true",
                [
                    _call(
                        "qualify_srd_environment_hearing",
                        {
                            "listener_state": "{_caster_state_id}",
                            "speaker_state": "{_scalar_target_id}",
                        },
                        ("_environment_can_hear",),
                    ),
                    _set(
                        "_caster_can_hear_target",
                        "_caster_can_hear_target == true and _environment_can_hear == true",
                    ),
                ],
            ),
        )
    _bind_environment_hearing_guard(cast)
    changed["qualify_srd_environment_hearing"] = _environment_tool(
        "Read whether sound can travel between two actual scene creatures.",
        [_entity("listener_state"), _entity("speaker_state")],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_speaker_state_id}"}, POSITION_EXPORTS
            ),
            _set("_environment_hearing_origin", "_info_position"),
            _set("_environment_hearing_plane", "_info_scene_entity.metadata.plane"),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_listener_state_id}"},
                POSITION_EXPORTS,
            ),
            _set("_environment_listener_plane", "_info_scene_entity.metadata.plane"),
            _call(
                "resolve_srd_environment_segment",
                {
                    "start": "{_environment_hearing_origin}",
                    "end": "{_info_position}",
                    "plane": "{_environment_hearing_plane}",
                },
                ("_environment_sound_blocked",),
            ),
            _set(
                "_environment_can_hear",
                (
                    "_environment_sound_blocked != true and _environment_hearing_plane == _en"
                    "vironment_listener_plane and 'deafened' not in listener_state.conditions"
                ),
            ),
        ],
    )
    end = deepcopy(tools["end_srd_concentration_node"])
    if "remove_srd_environment_concentration" not in str(end["effects"]):
        end["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'spell_environment'",
                [
                    _call(
                        "remove_srd_environment_concentration",
                        {"caster_state": "{_source_state_id}"},
                    )
                ],
            ),
        )
    changed["end_srd_concentration_node"] = end
    sight = deepcopy(tools["resolve_srd_scene_relation_node"])
    if "resolve_srd_environment_segment" not in str(sight["effects"]):
        sight["effects"].append(
            _set(
                "_scene_can_hear",
                "_scene_same_plane == true and 'deafened' not in actor_state.conditions",
            )
        )
        sight["effects"].append(
            _when(
                "state.has_spell_environments == true",
                [
                    _call(
                        "resolve_srd_environment_segment",
                        {
                            "start": "{_scene_actor_position}",
                            "end": "{_scene_target_position}",
                            "plane": "{_scene_actor_entity.metadata.plane}",
                        },
                        ("_environment_sight_obscured", "_environment_sound_blocked"),
                    ),
                    _when(
                        (
                            "_environment_sight_obscured == true and (actor_state.blindsight_"
                            "feet == null or actor_state.blindsight_feet < _scene_distance)"
                        ),
                        [_set("_scene_can_see", False)],
                    ),
                    _set(
                        "_scene_can_hear",
                        (
                            "_environment_sound_blocked != true and _scene_same_plane == true"
                            " and 'deafened' not in actor_state.conditions"
                        ),
                    ),
                ],
            )
        )
    changed["resolve_srd_scene_relation_node"] = sight
    relation = deepcopy(tools["resolve_srd_scene_relation"])
    relation["effects"] = [
        e
        for e in relation["effects"]
        if "resolve_srd_environment_segment" not in str(e)
        and e.get("set", {}).get("field") != "_scene_can_hear"
    ]
    for effect in relation["effects"]:
        if effect.get("call") == "resolve_srd_scene_relation_node":
            effect["export_context"]["_scene_can_hear"] = "_scene_can_hear"
    card = relation["metadata"]["result_card"]["summary"]
    if not any(row["label"] == "Can hear" for row in card):
        card.append({"label": "Can hear", "value": {"path": "context._scene_can_hear"}})
    changed["resolve_srd_scene_relation"] = relation
    # Components are checked in public casts and the longer-casting owners, including retries.
    for name, original in tools.items():
        tool = deepcopy(changed.get(name, original))
        mutated = False
        for effects in _effect_lists(tool.get("effects", [])):
            for index, effect in list(enumerate(effects)):
                condition = effect.get("require", {}).get("condition", "")
                if (
                    isinstance(condition, str)
                    and "verbal_component_available == true" in condition
                    and "_casting_requirements.components" in condition
                ):
                    if index > 0 and "Silence makes a Verbal component impossible here." in str(
                        effects[index - 1]
                    ):
                        continue
                    effects[index:index] = [
                        _when(
                            "state.has_spell_environments == true",
                            [
                                _call(
                                    "srd_information_scene_entity",
                                    {"subject": "{_caster_state_id}"},
                                    POSITION_EXPORTS,
                                ),
                                _call(
                                    "resolve_srd_environment_point",
                                    {
                                        "point": "{_info_position}",
                                        "plane": "{_info_scene_entity.metadata.plane}",
                                    },
                                    POINT_EXPORTS,
                                ),
                                _require(
                                    (
                                        "_environment_silence != true or 'verbal' not in _cas"
                                        "ting_requirements.components or 'verbal' in _compone"
                                        "nt_exemptions"
                                    ),
                                    "Silence makes a Verbal component impossible here.",
                                ),
                            ],
                        )
                    ]
                    mutated = True
        if mutated:
            changed[name] = tool
    damage = deepcopy(tools["apply_srd_damage"])
    if "resolve_srd_environment_actor" not in str(damage["effects"]):
        index = (
            next(
                i
                for i, e in enumerate(damage["effects"])
                if e.get("set", {}).get("field") == "_effective_damage"
            )
            + 1
        )
        damage["effects"].insert(
            index,
            _when(
                "state.has_spell_environments == true and damage_type == 'thunder'",
                [
                    _call(
                        "resolve_srd_environment_actor",
                        {"target_state": "{target}"},
                        ("_environment_entirely_silenced",),
                    ),
                    _when("_environment_entirely_silenced == true", [_set("_effective_damage", 0)]),
                ],
            ),
        )
    changed["apply_srd_damage"] = damage
    for name, original in tools.items():
        if not any(
            p["name"] == "component_1_damage_type" for p in original.get("parameters", [])
        ) or not any(
            e.get("set", {}).get("field") == "_component_1_effective_damage"
            for e in original.get("effects", [])
        ):
            continue
        components = deepcopy(original)
        if "resolve_srd_environment_actor" not in str(components["effects"]):
            for component in (2, 1):
                field = f"_component_{component}_effective_damage"
                index = (
                    next(
                        i
                        for i, e in enumerate(components["effects"])
                        if e.get("set", {}).get("field") == field
                    )
                    + 1
                )
                components["effects"].insert(
                    index,
                    _when(
                        "state.has_spell_environments == true and "
                        f"component_{component}_damage_type == 'thunder'",
                        [
                            _call(
                                "resolve_srd_environment_actor",
                                {"target_state": "{target}"},
                                ("_environment_entirely_silenced",),
                            ),
                            _when("_environment_entirely_silenced == true", [_set(field, 0)]),
                        ],
                    ),
                )
        changed[name] = components
    d20 = deepcopy(changed["d20_test"])
    if "refresh_srd_environment_actor" not in str(d20["effects"]):
        d20["effects"].insert(
            0,
            _when(
                (
                    "state.has_spell_environments == true and actor_state != null and require"
                    "s_hearing == true"
                ),
                [
                    _call("refresh_srd_environment_actor", {"target_state": "{_actor_state_id}"}),
                ],
            ),
        )
    changed["d20_test"] = d20
    if "_environment_fog" not in str(d20["effects"]):
        index = next(
            i
            for i, effect in enumerate(d20["effects"])
            if effect.get("condition", "").startswith("forego_save == true or")
        )
        d20["effects"].insert(
            index,
            _when(
                "state.has_spell_environments == true and actor_state != null and "
                "requires_sight == true and kind == 'ability_check' and "
                "(actor_state.blindsight_feet == null or actor_state.blindsight_feet == 0)",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_actor_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _call(
                        "resolve_srd_environment_point",
                        {
                            "point": "{_info_position}",
                            "plane": "{_info_scene_entity.metadata.plane}",
                        },
                        POINT_EXPORTS,
                    ),
                    _when(
                        "_environment_fog == true",
                        [_set("_automatic_failure_reason", "'blinded_sight'")],
                    ),
                ],
            ),
        )
    # The row schema is part of package admission, not a second runtime definition table.
    segment_name = "move_srd_scene_creature_environment_segment"
    cost = deepcopy(tools["srd_information_movement_cost"])
    if not any(p["name"] == "ignore_ground_environments" for p in cost["parameters"]):
        cost["parameters"].append(
            {
                **_optional("ignore_ground_environments", "boolean", False),
                "model_hidden": True,
            }
        )
        for effects in _effect_lists(cost["effects"]):
            for effect in effects:
                loop = effect.get("for_each", {})
                if loop.get("as") == "_route_zone":
                    loop["do"] = [
                        _when(
                            "_route_zone.metadata.ground_environment != true or "
                            "ignore_ground_environments != true",
                            loop["do"],
                        )
                    ]
    if not any(p["name"] == "actor_subject" for p in cost["parameters"]):
        cost["parameters"].append(
            {**_optional("actor_subject", "string", ""), "model_hidden": True}
        )
    if "resolve_srd_minor_terrain_intervals" not in str(cost["effects"]) and (
        "resolve_srd_environment_sphere_terrain_intervals" not in str(cost["effects"])
    ):
        cut_index = next(
            i
            for i, effect in enumerate(cost["effects"])
            if effect.get("sort", {}).get("collection") == "_route_cuts"
        )
        cost["effects"][cut_index:cut_index] = [
            _set("_route_minor_intervals", []),
            _when(
                "state.has_spell_environments == true and ignore_ground_environments != true "
                "and len(actor_subject) > 0",
                [
                    _call(
                        "resolve_srd_minor_terrain_intervals",
                        {"actor_subject": "{actor_subject}", "start": "{start}", "end": "{end}"},
                        {"_route_minor_intervals": "_minor_route_intervals"},
                    ),
                    _each(
                        "_route_minor_intervals",
                        "_route_minor_interval",
                        [
                            _push("_route_cuts", {"t": "_route_minor_interval.entry"}),
                            _push("_route_cuts", {"t": "_route_minor_interval.exit"}),
                        ],
                    ),
                ],
            ),
        ]
        for effects in _effect_lists(cost["effects"]):
            plant_index = next(
                (
                    i
                    for i, effect in enumerate(effects)
                    if effect.get("for_each", {}).get("as") == "_route_plant"
                ),
                None,
            )
            if plant_index is not None:
                effects[plant_index:plant_index] = [
                    _each(
                        "_route_minor_intervals",
                        "_route_minor_interval",
                        [
                            _when(
                                "_route_mid >= _route_minor_interval.entry and "
                                "_route_mid <= _route_minor_interval.exit",
                                [
                                    _set("_route_piece_difficult", True),
                                    _set("_route_nonplant_difficult", True),
                                ],
                            ),
                        ],
                    ),
                ]
    changed["srd_information_movement_cost"] = cost
    movement = deepcopy(tools.get(segment_name, tools["move_srd_scene_creature_node"]))
    if not any(p["name"] == "witness_state_ids" for p in movement["parameters"]):
        movement["parameters"].append(
            _optional("witness_state_ids", "array", None, items={"type": "string"})
        )
    public_movement = deepcopy(tools["move_srd_scene_creature"])
    if not any(p["name"] == "witness_state_ids" for p in public_movement["parameters"]):
        public_movement["parameters"].append(
            _optional("witness_state_ids", "array", None, items={"type": "string"})
        )
    for effects in _effect_lists(public_movement["effects"]):
        for effect in effects:
            if effect.get("call") == "move_srd_scene_creature_node":
                effect["params"]["witness_state_ids"] = "{witness_state_ids}"
    changed["move_srd_scene_creature"] = public_movement
    for effects in _effect_lists(movement["effects"]):
        for index, effect in list(enumerate(effects)):
            if effect.get("call") == "srd_information_movement_cost":
                effect["params"]["actor_subject"] = "{_actor_state_id}"
                if "ignore_ground_environments" not in effect["params"]:
                    effects.insert(
                        index,
                        _set(
                            "_move_ignore_ground_environments",
                            "mode == 'fly' or _move_scene_entity.metadata.flying == true",
                        ),
                    )
                effect["params"]["ignore_ground_environments"] = (
                    "{_move_ignore_ground_environments}"
                )
    if "refresh_srd_environment_actor" not in str(movement["effects"]):
        movement["effects"].append(
            _when(
                "state.has_spell_environments == true",
                [_call("refresh_srd_environment_actor", {"target_state": "{_actor_state_id}"})],
            )
        )
    if "'prone' in actor_state.conditions" not in str(movement["effects"]):
        index = next(
            i
            for i, effect in enumerate(movement["effects"])
            if effect.get("require", {}).get("condition") == "_route_distance > 0"
        )
        movement["effects"].insert(
            index,
            _when(
                "'prone' in actor_state.conditions",
                [
                    _set("_route_cost", "_route_cost + _route_distance"),
                ],
            ),
        )
    segment = deepcopy(movement)
    segment["description"] = (
        "Commit one actual environment movement segment through the shared movement owner."
    )
    segment["metadata"].update(category="internal", visible_to_player=False)
    changed[segment_name] = segment
    movement["effects"] = [
        _set("_ground_route_distance", 0),
        _set("_ground_route_cost", 0),
        _set("_ground_route_continue", True),
        _set("_ground_route_ids", []),
        _when(
            "state.has_spell_environments == true",
            [
                _set("_ground_route_ids", "state.persistent_spell_area_ids"),
            ],
        ),
        _push("_ground_route_ids", "'final_endpoint'"),
        _each(
            "_ground_route_ids",
            "_ground_route_id",
            [
                _when(
                    "_ground_route_continue == true",
                    [
                        _call(
                            "resolve_srd_environment_ground_entry",
                            {
                                "actor_state": "{_actor_state_id}",
                                "destination": "{destination}",
                                "mode": "{mode}",
                            },
                            ("_ground_entry_id", "_ground_entry_ids", "_ground_entry_destination"),
                        ),
                        _call(
                            segment_name,
                            {
                                "actor_state": "{_actor_state_id}",
                                "destination": "{_ground_entry_destination}",
                                "mode": "{mode}",
                                "difficult_terrain": "{difficult_terrain}",
                            },
                            (
                                "_scene_movement_distance",
                                "_scene_movement_cost",
                                "_scene_actor_position",
                                "_scene_actor_entity_id",
                            ),
                        ),
                        _set(
                            "_ground_route_distance",
                            "_ground_route_distance + _scene_movement_distance",
                        ),
                        _set("_ground_route_cost", "_ground_route_cost + _scene_movement_cost"),
                        _set("_ground_route_continue", "_ground_entry_id != null"),
                        _each(
                            "_ground_entry_ids",
                            "_ground_entered_id",
                            [
                                _call(
                                    "apply_srd_grease_save",
                                    {
                                        "target_state": "{_actor_state_id}",
                                        "area_id": "{_ground_entered_id}",
                                    },
                                    ("_grease_fell",),
                                ),
                                _when(
                                    "_grease_fell == true", [_set("_ground_route_continue", False)]
                                ),
                            ],
                        ),
                        _when(
                            "abs(_scene_actor_position[0] - destination[0]) < 0.000000001 and "
                            "abs(_scene_actor_position[1] - destination[1]) < 0.000000001 and "
                            "abs(_scene_actor_position[2] - destination[2]) < 0.000000001",
                            [_set("_ground_route_continue", False)],
                        ),
                    ],
                ),
            ],
        ),
        _set("_scene_movement_distance", "_ground_route_distance"),
        _set("_scene_movement_cost", "_ground_route_cost"),
    ]
    # The same committing segment owns terrain spending and every source entry.
    route_effects = movement["effects"]
    final_index = next(
        i
        for i, e in enumerate(route_effects)
        if e.get("list_push", {}).get("value") == "'final_endpoint'"
    )
    route_effects[final_index:final_index] = _areas(
        [
            _when(
                "_environment_area.mode == 'woodland' and "
                "_environment_area.caster == _actor_state_id",
                [
                    _set("_route_hazard_targets", "_environment_area.force_target_ids"),
                    _each(
                        "_route_hazard_targets",
                        "_route_hazard_target",
                        [
                            _push("_ground_route_ids", "_route_hazard_target"),
                        ],
                    ),
                ],
            ),
        ]
    )
    for effects in _effect_lists(route_effects):
        entry_index = next(
            (
                i
                for i, e in enumerate(effects)
                if e.get("call") == "resolve_srd_environment_ground_entry"
            ),
            None,
        )
        if entry_index is None:
            continue
        effects[entry_index]["export_context"]["_ground_entry_t"] = "_ground_entry_t"
        effects[entry_index + 1 : entry_index + 1] = [
            _call(
                "resolve_srd_environment_hazard_entry",
                {
                    "actor_state": "{_actor_state_id}",
                    "destination": "{destination}",
                },
                ("_hazard_entry_t", "_hazard_entry_events", "_hazard_entry_destination"),
            ),
            _when(
                "len(_hazard_entry_events) > 0",
                [
                    _when(
                        "_hazard_entry_t < _ground_entry_t - 0.000000001",
                        [
                            _set("_ground_entry_id", "'hazard_entry'"),
                            _set("_ground_entry_ids", []),
                            _set("_ground_entry_destination", "_hazard_entry_destination"),
                        ],
                    ),
                    _when(
                        "_hazard_entry_t > _ground_entry_t + 0.000000001",
                        [
                            _set("_hazard_entry_events", []),
                        ],
                    ),
                ],
            ),
        ]
        continued_index = next(
            i
            for i, e in enumerate(effects)
            if e.get("set", {}).get("field") == "_ground_route_continue"
        )
        effects[continued_index + 1 : continued_index + 1] = [
            _each(
                "_hazard_entry_events",
                "_hazard_entered_event",
                [
                    _call(
                        "resolve_srd_environment_hazard_event",
                        {
                            "target_state": "{_hazard_entered_event.target_id}",
                            "area_id": "{_hazard_entered_event.area_id}",
                            "witness_state_ids": "{witness_state_ids}",
                        },
                    ),
                ],
            ),
            _when(
                "actor_state.is_alive != true or actor_state.hp <= 0 or "
                "'restrained' in actor_state.conditions or "
                "'incapacitated' in actor_state.conditions or "
                "'unconscious' in actor_state.conditions or "
                "'paralyzed' in actor_state.conditions or "
                "'petrified' in actor_state.conditions or 'stunned' in actor_state.conditions",
                [_set("_ground_route_continue", False)],
            ),
        ]
    active_ground = movement["effects"]
    movement["effects"] = [
        _when(
            "state.has_spell_environments == true",
            active_ground,
            [
                _call(
                    segment_name,
                    {
                        "actor_state": "{_actor_state_id}",
                        "destination": "{destination}",
                        "mode": "{mode}",
                        "difficult_terrain": "{difficult_terrain}",
                    },
                    (
                        "_scene_movement_distance",
                        "_scene_movement_cost",
                        "_scene_actor_position",
                        "_scene_actor_entity_id",
                    ),
                ),
            ],
        )
    ]
    changed["move_srd_scene_creature_node"] = movement
    end_turn = deepcopy(tools["end_combat_turn"])
    if "resolve_srd_grease_end_turn" not in str(end_turn["effects"]):
        index = next(
            i
            for i, effect in enumerate(end_turn["effects"])
            if effect.get("set", {}).get("field") == "combat.action_available"
        )
        end_turn["effects"].insert(
            index,
            _when(
                "state.has_spell_environments == true",
                [
                    _call("resolve_srd_grease_end_turn", {"target_state": "{_actor_id}"}),
                ],
            ),
        )
    changed["end_combat_turn"] = end_turn
    beginning = deepcopy(tools["begin_combat_turn"])
    if "resolve_srd_environment_hazard_start_turn" not in str(beginning["effects"]):
        beginning["effects"].append(
            _when(
                "state.has_spell_environments == true",
                [
                    _call(
                        "resolve_srd_environment_hazard_start_turn", {"target_state": "{_actor_id}"}
                    ),
                ],
            )
        )
    changed["begin_combat_turn"] = beginning
    if "resolve_srd_environment_hazard_end_turn" not in str(end_turn["effects"]):
        index = next(
            i
            for i, effect in enumerate(end_turn["effects"])
            if effect.get("set", {}).get("field") == "combat.action_available"
        )
        end_turn["effects"].insert(
            index,
            _when(
                "state.has_spell_environments == true",
                [
                    _call(
                        "resolve_srd_environment_hazard_end_turn",
                        {
                            "target_state": "{_actor_id}",
                            "witness_state_ids": "{witness_state_ids}",
                        },
                    ),
                ],
            ),
        )
    language = deepcopy(tools["resolve_srd_spell_language"])
    if "resolve_srd_environment_segment" not in str(language["effects"]):
        index = (
            next(
                i
                for i, e in enumerate(language["effects"])
                if e.get("set", {}).get("field") == "_language_perceived"
            )
            + 1
        )
        language["effects"].insert(
            index,
            _when(
                "state.has_spell_environments == true and channel == 'speech'",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_speaker_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_environment_speaker_origin", "_info_position"),
                    _set("_environment_speaker_plane", "_info_scene_entity.metadata.plane"),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_listener_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _call(
                        "resolve_srd_environment_segment",
                        {
                            "start": "{_environment_speaker_origin}",
                            "end": "{_info_position}",
                            "plane": "{_environment_speaker_plane}",
                        },
                        ("_environment_sound_blocked",),
                    ),
                    _set(
                        "_language_perceived",
                        "_language_perceived == true and _environment_sound_blocked != true",
                    ),
                ],
            ),
        )
    changed["resolve_srd_spell_language"] = language
    proxy = deepcopy(tools["perceive_srd_information_proxy"])
    if "resolve_srd_environment_segment" not in str(proxy["effects"]):
        for effects in _effect_lists(proxy["effects"]):
            for index, effect in list(enumerate(effects)):
                if (
                    effect.get("condition")
                    == "_remote_visible == true and _remote_entity.metadata.participant_ref != null"
                ):
                    effects[index:index] = [
                        _when(
                            "state.has_spell_environments == true",
                            [
                                _call(
                                    "resolve_srd_environment_segment",
                                    {
                                        "start": "{_remote_origin}",
                                        "end": "{_remote_entity.position}",
                                        "plane": "{_remote_plane}",
                                    },
                                    ("_environment_sight_obscured", "_environment_sound_blocked"),
                                ),
                                _when(
                                    "record.modality == 'hearing'",
                                    [
                                        _set(
                                            "_remote_visible",
                                            (
                                                "_remote_visible == true and _environment"
                                                "_sound_blocked != true"
                                            ),
                                        )
                                    ],
                                    [
                                        _set(
                                            "_remote_visible",
                                            (
                                                "_remote_visible == true and _environment"
                                                "_sight_obscured != true"
                                            ),
                                        )
                                    ],
                                ),
                            ],
                        )
                    ]
    changed["perceive_srd_information_proxy"] = proxy
    observation = deepcopy(tools["observe_srd_information_proxy_node"])
    if "resolve_srd_environment_segment" not in str(observation["effects"]):
        observation["effects"].append(
            _when(
                "state.has_spell_environments == true",
                [
                    _call(
                        "resolve_srd_environment_segment",
                        {
                            "start": "{_info_position}",
                            "end": "{_observed_proxy.position}",
                            "plane": "{_proxy_observer_plane}",
                        },
                        ("_environment_sight_obscured",),
                    ),
                    _when(
                        "_environment_sight_obscured == true and "
                        "(observer_state.blindsight_feet == null or "
                        "observer_state.blindsight_feet < _proxy_observer_distance)",
                        [_set("_proxy_observed", False), _set("_info_orb", False)],
                    ),
                ],
            )
        )
    changed["observe_srd_information_proxy_node"] = observation
    candidate = deepcopy(tools["qualify_srd_information_candidate"])
    candidate["effects"] = [
        e for e in candidate["effects"] if "resolve_srd_environment_segment" not in str(e)
    ]
    if "resolve_srd_environment_segment" not in str(candidate["effects"]):
        candidate["effects"].append(
            _when(
                "state.has_spell_environments == true and _candidate_eligible == true and "
                "(spell == 'find_traps' or (spell == 'detect_magic' and mode == 'aura'))",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_caster_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _call(
                        "resolve_srd_environment_segment",
                        {
                            "start": "{record.origin}",
                            "end": "{candidate.position}",
                            "plane": "{_info_scene_entity.metadata.plane}",
                        },
                        ("_environment_sight_obscured",),
                    ),
                    _call(
                        "measure_srd_information_distance",
                        {
                            "start": "{record.origin}",
                            "end": "{candidate.position}",
                        },
                        ("_scene_distance",),
                    ),
                    _when(
                        "_environment_sight_obscured == true and "
                        "(caster_state.blindsight_feet == null or "
                        "caster_state.blindsight_feet < _scene_distance)",
                        [_set("_candidate_eligible", False)],
                    ),
                ],
            )
        )
    changed["qualify_srd_information_candidate"] = candidate
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_environment_lifecycle" not in str(clock["effects"]):
        index = (
            next(
                i
                for i, e in enumerate(clock["effects"])
                if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
            )
            + 1
        )
        clock["effects"].insert(index, _call("refresh_srd_environment_lifecycle", {}))
    if "refresh_srd_environment_wind" not in str(clock["effects"]):
        index = (
            next(
                i
                for i, e in enumerate(clock["effects"])
                if e.get("call") == "refresh_srd_environment_lifecycle"
            )
            + 1
        )
        clock["effects"].insert(index, _call("refresh_srd_environment_wind", {}))
    changed["advance_srd_lasting_clock"] = clock
    _bind_sphere_hazards(changed)
    _bind_moonbeam(changed)
    _bind_spike_growth(changed)
    _bind_spirit_guardians(changed)
    _bind_stinking_cloud(changed)
    _bind_sleet_storm(changed)
    _bind_black_tentacles(changed)
    _bind_blade_barrier(changed)
    _bind_wall_of_fire(changed)
    _bind_web(changed)
    if __package__:
        from .srd_environment_wall_ice import bind_sources
        from .srd_environment_wall_ice import tools as ice_tools
    else:
        from srd_environment_wall_ice import bind_sources
        from srd_environment_wall_ice import tools as ice_tools
    changed.update(ice_tools())
    bind_sources(changed)
    if __package__:
        from .srd_environment_wall_ice_formation import bind_sources as bind_ice_formation
        from .srd_environment_wall_ice_formation import tools as ice_formation_tools
    else:
        from srd_environment_wall_ice_formation import bind_sources as bind_ice_formation
        from srd_environment_wall_ice_formation import tools as ice_formation_tools
    changed.update(ice_formation_tools(tools))
    bind_ice_formation(changed)
    if __package__:
        from .srd_environment_wall_fire_ring import bind_sources as bind_fire_ring
        from .srd_environment_wall_fire_ring import tools as fire_ring_tools
    else:
        from srd_environment_wall_fire_ring import bind_sources as bind_fire_ring
        from srd_environment_wall_fire_ring import tools as fire_ring_tools
    changed.update(fire_ring_tools(changed))
    bind_fire_ring(changed)
    if __package__:
        from .srd_environment_retained_damage import bind_sources as bind_retained_damage
        from .srd_environment_retained_damage import tools as retained_damage_tools
    else:
        from srd_environment_retained_damage import bind_sources as bind_retained_damage
        from srd_environment_retained_damage import tools as retained_damage_tools
    changed.update(retained_damage_tools(changed))
    bind_retained_damage(changed)
    if __package__:
        from .srd_environment_cloud_fire import bind_sources as bind_incendiary_cloud
        from .srd_environment_cloud_fire import tools as incendiary_cloud_tools
    else:
        from srd_environment_cloud_fire import bind_sources as bind_incendiary_cloud
        from srd_environment_cloud_fire import tools as incendiary_cloud_tools
    changed.update(incendiary_cloud_tools())
    bind_incendiary_cloud(changed)
    if __package__:
        from .srd_environment_flaming_sphere import bind_sources as bind_flaming_sphere
        from .srd_environment_flaming_sphere import tools as flaming_sphere_tools
    else:
        from srd_environment_flaming_sphere import bind_sources as bind_flaming_sphere
        from srd_environment_flaming_sphere import tools as flaming_sphere_tools
    changed.update(flaming_sphere_tools(changed))
    bind_flaming_sphere(changed)
    if __package__:
        from .srd_environment_wind_wall import bind_sources as bind_wind_wall
        from .srd_environment_wind_wall import tools as wind_wall_tools
    else:
        from srd_environment_wind_wall import bind_sources as bind_wind_wall
        from srd_environment_wind_wall import tools as wind_wall_tools
    changed.update(wind_wall_tools())
    bind_wind_wall(changed)
    if __package__:
        from .srd_environment_weather import bind_sources as bind_weather
        from .srd_environment_weather import tools as weather_tools
    else:
        from srd_environment_weather import bind_sources as bind_weather
        from srd_environment_weather import tools as weather_tools
    changed.update(weather_tools())
    bind_weather(changed)
    if __package__:
        from .srd_environment_water import bind_sources as bind_water
        from .srd_environment_water import tools as water_tools
    else:
        from srd_environment_water import bind_sources as bind_water
        from srd_environment_water import tools as water_tools
    changed.update(water_tools())
    bind_water(changed)
    if __package__:
        from .srd_environment_wall_ice import bind_barriers
    else:
        from srd_environment_wall_ice import bind_barriers
    bind_barriers(changed["srd_information_barriers"])
    for tool in changed.values():
        if re.search(r"\bstate\.", str(tool["effects"])):
            bound = _environment_tool(
                tool["description"],
                tool["parameters"],
                tool["effects"],
                tool.get("pre_conditions", ()),
            )
            tool["parameters"] = bound["parameters"]
            tool["effects"] = bound["effects"]
    text = _replace_tools(text, changed)
    if "&d20_result_card" not in text:
        text = re.sub(
            r"(?ms)(^  d20_test:\n.*?    result_card):\n", r"\1: &d20_result_card\n", text, count=1
        )
    schema = yaml.safe_dump({"spell_environment": _schema()}, sort_keys=False)
    rendered_schema = "".join("      " + line for line in schema.splitlines(True))
    if "      spell_environment:" in text:
        text = re.sub(
            r"(?ms)^      spell_environment:\n.*?(?=^      [a-z])",
            lambda _: rendered_schema + "\n",
            text,
            count=1,
        )
    else:
        text = text.replace(
            "      divination_spell:",
            rendered_schema + "\n      divination_spell:",
            1,
        )
    return text


def _effect_lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _effect_lists(effect[key])
        if "for_each" in effect:
            yield from _effect_lists(effect["for_each"]["do"])
