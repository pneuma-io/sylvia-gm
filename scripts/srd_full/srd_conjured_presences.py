"""The 2024 Celestial cylinder and Fey spirit reuse actual scene, roll and source owners."""

import re
from copy import deepcopy

from .srd_scene_relations import _array, _snapshot, _spatial, _tool_scene
from .srd_spell_object_content import _new_scene_object
from .srd_spell_travel import _normalize
from .srd_timed_actor_defenses import (
    _call as _base_call,
)
from .srd_timed_actor_defenses import (
    _entity,
    _load_runtime,
    _parameter,
    _require,
    _set,
    _tool,
    _when,
)
from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools

PROFILES = {
    "conjure_celestial": {
        "family": "conjured_presence",
        "mode": "celestial",
        "casting_time": "action",
        "base_level": 7,
        "range_feet": 90,
        "duration_rounds": 100,
        "range_kind": "distance",
        "concentration": True,
        "radius": 10,
        "height": 40,
        "healing_dice": 4,
        "damage_dice": 6,
        "damage_die": "d12",
        "damage_type": "radiant",
        "save_ability": "dexterity",
        "success_damage": "half",
    },
    "conjure_fey": {
        "family": "conjured_presence",
        "mode": "fey",
        "casting_time": "action",
        "base_level": 6,
        "range_feet": 60,
        "duration_rounds": 100,
        "range_kind": "distance",
        "concentration": True,
        "size": "medium",
        "teleport_range": 30,
        "attack_range": 5,
        "damage_dice": 3,
        "damage_die": "d12",
        "damage_type": "psychic",
        "condition": "frightened",
    },
}
PUBLIC_TOOLS = {"use_srd_conjured_fey"}
POSITION = ("_info_scene_entity", "_info_position")


def _call(name, params, exports=()):
    result = _base_call(name, params, exports)
    if isinstance(exports, dict):
        result["export_context"] = exports
    return result


def schema():
    properties = {}
    required = set.intersection(*(set(p) for p in PROFILES.values())) - {"family"}
    for profile in PROFILES.values():
        for key, value in profile.items():
            if key == "family":
                continue
            if key not in properties:
                properties[key] = {
                    "type": "boolean"
                    if isinstance(value, bool)
                    else "integer"
                    if isinstance(value, int)
                    else "string",
                    "enum": [],
                }
            if value not in properties[key]["enum"]:
                properties[key]["enum"].append(value)
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(required),
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    for spell, definition in definitions.items():
        if definition.get("family") == "conjured_presence" and definition != PROFILES.get(spell):
            raise ValueError("Conjured presence differs from the pinned 2024 source.")


def intents():
    return [
        _optional("conjured_source_id", default=""),
        _parameter(
            "conjured_point",
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            max_items=3,
        ),
        *[
            _parameter(
                name,
                "entity_id_list",
                required=False,
                default=[],
                resolve="entity_list",
                unique_items=True,
                max_items=64,
            )
            for name in ("celestial_healing_targets", "celestial_searing_targets")
        ],
        _optional("celestial_origin_face", "enum", "bottom", enum_values=["bottom", "top"]),
        _optional("fey_appearance", default=""),
        _optional("fey_height", "number", 0, minimum=0),
        _optional("fey_attack_target", default=""),
    ]


def _subject(subject, prefix):
    return [
        _call("srd_information_scene_entity", {"subject": subject}, POSITION),
        _set(prefix, "_info_scene_entity"),
        _require(f"{prefix}.metadata.plane != null", "Use the subject's actual admitted plane."),
    ]


def _bounds(point, below=0, above=0, radius=0):
    return [
        *[
            _require(
                f"{point}[{i}] - {radius} >= state.spatial_scene.map.bounds.min[{i}] and "
                f"{point}[{i}] + {radius} <= state.spatial_scene.map.bounds.max[{i}]",
                "The complete actual spell shape must fit the admitted map.",
            )
            for i in (0, 1)
        ],
        _require(
            f"{point}[2] - {below} >= state.spatial_scene.map.bounds.min[2] and "
            f"{point}[2] + {above} <= state.spatial_scene.map.bounds.max[2]",
            "The complete actual spell shape must fit the admitted map.",
        ),
    ]


def _distance(start, end, local):
    return [
        _call(
            "measure_srd_information_distance",
            {"start": start, "end": end},
            {local: "_scene_distance"},
        )
    ]


def _cylinder_faces(point, face="celestial_origin_face"):
    """The printed Cylinder origin is on its circular top or bottom, never mid-height."""
    return [
        _set("_presence_cylinder_min", f"{point}[2]"),
        _set("_presence_cylinder_max", f"{point}[2] + 40"),
        _when(
            f"{face} == 'top'",
            [
                _set("_presence_cylinder_min", f"{point}[2] - 40"),
                _set("_presence_cylinder_max", f"{point}[2]"),
            ],
        ),
    ]


def _lookup(live=True):
    effects = [
        _require("state.persistent_spell_areas != null", "Select an actual retained spell area."),
        _set("_presence", "state.persistent_spell_areas[source_id]"),
        _require(
            "_presence != null and _presence.profile == 'conjured_presence'",
            "Select an actual retained Celestial or Fey source.",
        ),
    ]
    if live:
        effects += [
            _require(
                "_presence.active == true and state.lasting_tick < _presence.expires_tick",
                "The actual conjured source has ended.",
            ),
            _call(
                "qualify_srd_conjured_presence_focus",
                {"source_id": "{source_id}"},
                ["_presence_focus_live"],
            ),
            _require(
                "_presence_focus_live == true", "This exact conjured source lost Concentration."
            ),
        ]
    return effects


def _magic(caster, target, level):
    return [
        _set("_presence_magic_allowed", True),
        _when(
            "state.has_spell_magic_zones == true",
            [
                _call(
                    "check_srd_magic_zone_effect",
                    {
                        "source_subject": caster,
                        "target_subject": target,
                        "effect_kind": "spell",
                        "actual_spell_level": level,
                    },
                    {"_presence_magic_allowed": "_magic_zone_effect_allowed"},
                ),
            ],
        ),
    ]


def _geometry(tools):
    """Change only the source guard on the existing exact Cylinder/actual Box intersection."""
    cylinder = deepcopy(tools["resolve_srd_environment_cylinder_body_segment"])
    # The Wall of Fire owner keeps ordinary Cylinder qualification in its fallback.
    # Celestial must retain that branch, without inheriting the annulus projection.
    adapters = [
        (index, effect)
        for index, effect in enumerate(cylinder["effects"])
        if effect.get("condition") == "_sphere_geometry_source.mode == 'wall_fire' and "
        "_sphere_geometry_source.volume.kind == 'annulus'"
    ]
    if len(adapters) > 1:
        raise ValueError("The ordinary Cylinder source adapter is ambiguous.")
    if adapters:
        index, adapter = adapters[0]
        fallback = adapter.get("on_false", [])
        if len(fallback) != 1 or "_sphere_geometry_source.profile" not in fallback[0].get(
            "require", {}
        ).get("condition", ""):
            raise ValueError("Retain the actual ordinary Cylinder qualification fallback.")
        cylinder["effects"][index] = deepcopy(fallback[0])
    guards = [
        e
        for e in cylinder["effects"]
        if "require" in e and "_sphere_geometry_source.profile" in e["require"]["condition"]
    ]
    if len(guards) != 1:
        raise ValueError("Retain the one existing cylinder source qualification.")
    guards[0]["require"] = {
        "condition": "_sphere_geometry_source.profile == 'conjured_presence' and "
        "_sphere_geometry_source.mode == 'celestial' and "
        "_body_moving.footprint.kind == 'box'",
        "error": "Celestial membership requires this actual Cylinder and an admitted Box body.",
    }
    return cylinder


def _intent_geometry(tools):
    """Prepayment uses the same Cylinder math on creation intent, without installing a source."""
    tool = _geometry(tools)
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "area_id"]
    tool["parameters"].append(_array("point"))
    tool["parameters"].append(
        _optional("celestial_origin_face", "enum", "bottom", enum_values=["bottom", "top"])
    )
    tool["effects"] = [
        e for e in tool["effects"] if e.get("call") != "format_srd_information_projection"
    ]
    owner = next(
        e for e in tool["effects"] if e.get("set", {}).get("field") == "_sphere_geometry_source"
    )
    owner["set"]["value"] = {
        "profile": "'conjured_presence'",
        "mode": "'celestial'",
        "origin": "point",
        "plane": "_body_moving.metadata.plane",
        "radius": 10,
        "cylinder_min": "_presence_cylinder_min",
        "cylinder_max": "_presence_cylinder_max",
    }
    index = tool["effects"].index(owner)
    tool["effects"][index:index] = _cylinder_faces("point")
    tool["parameters"].append(_parameter("plane"))
    owner["set"]["value"]["plane"] = "plane"
    return tool


def _intent_targets():
    tool = _visible_targets()
    tool["parameters"] = [
        _entity("caster_state"),
        _array("point"),
        _parameter("plane"),
        _optional("celestial_origin_face", "enum", "bottom", enum_values=["bottom", "top"]),
    ] + [p for p in tool["parameters"] if p["name"] == "lasting_state"]
    first = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_presence_visible"
    )
    tool["effects"] = tool["effects"][first:]
    for sequence in _lists(tool["effects"]):
        for e in sequence:
            if e.get("call") == "resolve_srd_celestial_body_segment":
                e["call"] = "resolve_srd_celestial_intent_body_segment"
                e["params"].pop("area_id")
                e["params"].update(
                    point="{point}",
                    plane="{plane}",
                    celestial_origin_face="{celestial_origin_face}",
                )
    return tool


def _choices(healing, searing):
    return [
        _require(
            f"len({healing}) + len({searing}) == len(_presence_visible)",
            "Choose one light for every actual visible creature in the Cylinder.",
        ),
        _loop(
            healing,
            "_presence_chosen",
            [
                _require(
                    f"_presence_chosen in _presence_visible and _presence_chosen not in {searing}",
                    "Healing intent must select a visible creature only once.",
                ),
            ],
        ),
        _loop(
            searing,
            "_presence_chosen",
            [
                _require(
                    "_presence_chosen in _presence_visible",
                    "Searing intent must select a visible creature in the Cylinder.",
                ),
            ],
        ),
    ]


def _sight(tools):
    """Pure actual point sight avoids lifecycle damage inside D20's fear consumer."""
    tool = deepcopy(tools["resolve_srd_illumination_point_sight"])
    for sequence in _lists(tool["effects"]):
        for e in sequence:
            if e.get("call") == "resolve_srd_travel_perception":
                e["call"] = "qualify_srd_travel_perception"
            if e.get("call") == "resolve_srd_sight":
                if "resolve_srd_symbol_glyph_sight" not in tools:
                    raise ValueError(
                        "Conjured perception needs the native sensory-only Sight clone."
                    )
                e["call"] = "resolve_srd_symbol_glyph_sight"
    return tool


def _subject_sight(tools):
    """Preserve final point sight and derive creature invisibility from the actual subject."""
    tool = _sight(tools)
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "point"]
    tool["parameters"].append(_parameter("subject"))
    tool["effects"][:0] = [
        *_subject("{subject}", "_presence_sight_body"),
        _set("point", "_presence_sight_body.position"),
        _set("_presence_sight_subject_state", None),
        _when(
            "_presence_sight_body.metadata.participant_ref != null",
            [
                _set(
                    "_presence_sight_subject_state", "_presence_sight_body.metadata.participant_ref"
                ),
            ],
        ),
    ]
    for sequence in _lists(tool["effects"]):
        for e in sequence:
            if e.get("call") == "resolve_srd_symbol_glyph_sight":
                e["params"]["subject_state"] = "{_presence_sight_subject_state}"
                e["params"]["subject_invisible"] = (
                    "{_presence_sight_body.metadata.invisible == true}"
                )

    def point_values(value):
        if isinstance(value, str):
            return re.sub(r"\bpoint\b", "_presence_sight_point", value)
        if isinstance(value, list):
            return [point_values(child) for child in value]
        if isinstance(value, dict):
            return {key: point_values(child) for key, child in value.items()}
        return value

    tool["effects"] = point_values(tool["effects"])
    return tool


def _lists(value):
    if isinstance(value, list):
        if all(isinstance(child, dict) for child in value):
            yield value
        for child in value:
            yield from _lists(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _lists(child)


def _fey_space(point="point"):
    return [
        *_bounds(point, above="height", radius=2.5),
        _set(
            "_presence_probe",
            {
                "kind": "box",
                "min": [f"{point}[0] - 2.5", f"{point}[1] - 2.5", f"{point}[2]"],
                "max": [f"{point}[0] + 2.5", f"{point}[1] + 2.5", f"{point}[2] + height"],
            },
        ),
        *_snapshot(),
        _spatial(
            "area",
            {
                "map_id": "{state.spatial_scene.map.id}",
                "shape": {
                    "kind": "box",
                    "min": "{_presence_probe.min}",
                    "max": "{_presence_probe.max}",
                },
                "include": ["geometry", "entities"],
                "max_results": 512,
            },
            "_presence_overlaps",
        ),
        _require("_presence_overlaps.truncated != true", "Occupancy selection must be complete."),
        _set("_presence_overlap_hits", "_presence_overlaps.results"),
        _set("_presence_scene_bodies", "state.spatial_scene.entities"),
        _set("_presence_scene_solids", "state.spatial_scene.geometry"),
        _loop(
            "_presence_overlap_hits",
            "_presence_overlap",
            [
                _set("_presence_occupant", None),
                _loop(
                    "_presence_scene_bodies",
                    "_presence_body",
                    [
                        _when(
                            "_presence_body.id == _presence_overlap.id",
                            [
                                _set("_presence_occupant", "_presence_body"),
                            ],
                        ),
                    ],
                ),
                _loop(
                    "_presence_scene_solids",
                    "_presence_solid",
                    [
                        _when(
                            "_presence_solid.id == _presence_overlap.id",
                            [
                                _set("_presence_occupant", "_presence_solid"),
                            ],
                        ),
                    ],
                ),
                _require("_presence_occupant != null", "Resolve each actual occupancy hit."),
                _require(
                    "_presence_occupant.metadata.plane != null",
                    "Occupancy needs actual admitted plane facts.",
                ),
                _require(
                    "_presence_occupant.id == excluded_id or "
                    "_presence_occupant.metadata.plane != plane or "
                    "(_presence_occupant.metadata.participant_ref == null and "
                    "_presence_occupant.movement != 'blocking')",
                    "The whole Medium spirit needs an actual unoccupied space.",
                ),
            ],
        ),
    ]


def _visible_targets():
    return _tool_scene(
        "Collect actual visible creatures intersecting this source's exact Cylinder.",
        [_entity("caster_state"), _parameter("source_id")],
        _lookup()
        + [
            _set("_presence_visible", []),
            _set("_presence_scene_targets", "state.spatial_scene.entities"),
            _loop(
                "_presence_scene_targets",
                "_presence_target",
                [
                    _when(
                        "_presence_target.active == true and "
                        "_presence_target.metadata.participant_ref != null and "
                        "_presence_target.metadata.is_object != true",
                        [
                            _call(
                                "resolve_srd_celestial_body_segment",
                                {
                                    "moving_subject": "{_presence_target.id}",
                                    "area_id": "{source_id}",
                                    "start": "{_presence_target.position}",
                                    "end": "{_presence_target.position}",
                                },
                                ["_body_hit"],
                            ),
                            _when(
                                "_body_hit == true",
                                [
                                    _call(
                                        "resolve_srd_conjured_subject_sight",
                                        {
                                            "observer_state": "{_caster_state_id}",
                                            "subject": "{_presence_target.id}",
                                        },
                                        [
                                            "_illumination_point_can_see",
                                            "_illumination_point_plane",
                                        ],
                                    ),
                                    _when(
                                        "_illumination_point_can_see == true and "
                                        "_illumination_point_plane == "
                                        "_presence_target.metadata.plane",
                                        [
                                            _push(
                                                "_presence_visible",
                                                "_presence_target.metadata.participant_ref",
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


def _validate():
    return _tool_scene(
        "Validate actual source identity, range, shape, visible intent and unoccupied Fey "
        "body before payment.",
        [_entity("caster_state"), _parameter("spell"), *intents()],
        [
            _require(
                "conjured_source_id != '' and len(conjured_point) == 3",
                "Choose the source identity and actual spell origin.",
            ),
            _require(
                "state.persistent_spell_areas == null or "
                "state.persistent_spell_areas[conjured_source_id] == null",
                "A retained source identity cannot be reused.",
            ),
            *_subject("{_caster_state_id}", "_presence_caster"),
            *_distance("{_presence_caster.position}", "{conjured_point}", "_presence_range"),
            _when(
                "spell == 'conjure_celestial'",
                [
                    _require(
                        "_presence_range <= 90", "Celestial's actual origin must be within90 feet."
                    ),
                    *_bounds("conjured_point", radius=10),
                    *_cylinder_faces("conjured_point"),
                    _require(
                        "_presence_cylinder_min >= state.spatial_scene.map.bounds.min[2] and "
                        "_presence_cylinder_max <= state.spatial_scene.map.bounds.max[2]",
                        "The complete forty-foot Cylinder must fit the admitted map.",
                    ),
                    _require("fey_attack_target == ''", "Celestial has no Fey attack intent."),
                    _require(
                        "len(celestial_healing_targets) + len(celestial_searing_targets) <= 64",
                        "This initial batch supports64 actual creatures.",
                    ),
                    _loop(
                        "celestial_healing_targets",
                        "_presence_choice",
                        [
                            _require(
                                "_presence_choice not in celestial_searing_targets",
                                "Choose only one initial light for each actual creature.",
                            ),
                        ],
                    ),
                    _call(
                        "collect_srd_celestial_intent_creatures",
                        {
                            "caster_state": "{_caster_state_id}",
                            "point": "{conjured_point}",
                            "plane": "{_presence_caster.metadata.plane}",
                            "celestial_origin_face": "{celestial_origin_face}",
                        },
                        ["_presence_visible"],
                    ),
                    *_choices("celestial_healing_targets", "celestial_searing_targets"),
                ],
            ),
            _when(
                "spell == 'conjure_fey'",
                [
                    _require(
                        "_presence_range <= 60 and fey_appearance != '' and fey_height > 0",
                        "Choose a Medium Fey appearance, actual height and origin within60 feet.",
                    ),
                    _require(
                        "len(celestial_healing_targets) == 0 and "
                        "len(celestial_searing_targets) == 0",
                        "Fey does not bathe creatures in Celestial light.",
                    ),
                    _call(
                        "resolve_srd_conjured_point_sight",
                        {
                            "observer_state": "{_caster_state_id}",
                            "point": "{conjured_point}",
                        },
                        ["_illumination_point_can_see"],
                    ),
                    _require(
                        "_illumination_point_can_see == true",
                        "The caster must actually see Fey's space.",
                    ),
                    _call(
                        "validate_srd_fey_space",
                        {
                            "point": "{conjured_point}",
                            "plane": "{_presence_caster.metadata.plane}",
                            "height": "{fey_height}",
                            "excluded_id": "",
                        },
                    ),
                    _when(
                        "fey_attack_target != ''",
                        [
                            _call(
                                "validate_srd_fey_attack_target",
                                {
                                    "target_state": "{fey_attack_target}",
                                    "point": "{conjured_point}",
                                    "plane": "{_presence_caster.metadata.plane}",
                                    "height": "{fey_height}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_presence_caster.position}",
                    "end": "{conjured_point}",
                    "plane": "{_presence_caster.metadata.plane}",
                    "rule": "solid",
                },
                ["_information_blocked"],
            ),
            _require("_information_blocked != true", "A solid blocks the spell's actual origin."),
        ],
    )


def _fey_target():
    return _tool_scene(
        "Qualify the actual target body against the Medium spirit's real attack origin.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _array("point"),
            _parameter("plane"),
            _parameter("height", "number", minimum=0.000001),
        ],
        [
            *_subject("{_target_state_id}", "_presence_attack_body"),
            _require(
                "_presence_attack_body.metadata.plane == plane and "
                "_presence_attack_body.footprint.kind == 'box' and "
                "_presence_attack_body.metadata.is_object != true",
                "Fey attacks an actual same-plane creature with supported Box geometry.",
            ),
            _set("_presence_target_conditions", "target_state.conditions"),
            _loop(
                "_presence_target_conditions",
                "_presence_target_condition",
                [
                    _require(
                        "_presence_target_condition == 'frightened'",
                        "Fey target-condition attack modifiers remain "
                        "unsupported in this bounded path.",
                    ),
                ],
            ),
            _require(
                "target_state.dodging != true and "
                "_presence_attack_body.metadata.cover_from == null",
                "Fey Dodge and categorical Cover attack modifiers remain unsupported here.",
            ),
            _call(
                "resolve_srd_conjured_subject_sight",
                {
                    "observer_state": "{_caster_state_id}",
                    "subject": "{_presence_attack_body.id}",
                },
                ["_illumination_point_can_see"],
            ),
            _require(
                "_illumination_point_can_see == true",
                "This bounded Fey attack needs actual caster sight; "
                "unseen attack modifiers remain selected.",
            ),
            _call(
                "resolve_srd_conjured_point_sight",
                {
                    "observer_state": "{_target_state_id}",
                    "point": "{point}",
                },
                ["_illumination_point_can_see"],
            ),
            _require(
                "_illumination_point_can_see == true",
                "This bounded Fey attack needs actual target sight of its visible spirit.",
            ),
            _set(
                "_presence_nearest",
                [
                    "max(_presence_attack_body.position[0] - "
                    "_presence_attack_body.footprint.half_extents[0] - point[0] - 2.5, "
                    "point[0] - 2.5 - _presence_attack_body.position[0] - "
                    "_presence_attack_body.footprint.half_extents[0], 0)",
                    "max(_presence_attack_body.position[1] - "
                    "_presence_attack_body.footprint.half_extents[1] - point[1] - 2.5, "
                    "point[1] - 2.5 - _presence_attack_body.position[1] - "
                    "_presence_attack_body.footprint.half_extents[1], 0)",
                    "max(_presence_attack_body.position[2] - point[2] - height, point[2] - "
                    "_presence_attack_body.position[2] - _presence_attack_body.height, 0)",
                ],
            ),
            _require(
                "_presence_nearest[0] ** 2 + _presence_nearest[1] ** 2 + "
                "_presence_nearest[2] ** 2 <= 25",
                "Fey's target must actually be within5 feet.",
            ),
            _call(
                "srd_information_barriers",
                {
                    "start": "{point}",
                    "end": "{_presence_attack_body.position}",
                    "plane": "{plane}",
                    "rule": "solid",
                },
                ["_information_blocked"],
            ),
            _require(
                "_information_blocked != true", "A real solid blocks Fey's melee spell attack."
            ),
        ],
    )


def _create_fey():
    metadata = {
        "is_object": False,
        "creature_type": "'spell_force'",
        "participant_ref": None,
        "plane": "plane",
        "conjured_presence": {
            "source_id": "source_id",
            "cast_id": "cast_id",
            "created_tick": "state.lasting_tick",
        },
        "appearance": "appearance",
        "size": "'medium'",
    }
    effects = [_set("_world_origin", "point"), _set("_world_extent", [5, 5, "height"])]
    effects += _new_scene_object(
        metadata, identity="subject_id", extent="_world_extent", movement="'blocking'"
    )
    for sequence in _lists(effects):
        for e in sequence:
            if e.get("set", {}).get("field") == "_world_created_marker":
                e["set"]["value"] = {"is_object": False}
    return _tool_scene(
        "Create one actual Medium source-qualified Fey anchor without invented creature "
        "statistics.",
        [
            _parameter("source_id"),
            _parameter("subject_id"),
            _array("point"),
            _parameter("plane"),
            _parameter("height", "number", minimum=0.000001),
            _parameter("appearance"),
            _optional("cast_id", default="", model_hidden=True),
        ],
        effects,
    )


def _install():
    return _tool_scene(
        "Install the one paid spell and retain its actual casting profile, identity and duration.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            *intents(),
            _parameter("slot_level", "integer", minimum=6, maximum=9),
            _parameter("casting_ability"),
            _parameter("casting_modifier", "integer"),
            _parameter("attack_bonus", "integer"),
            _parameter("save_dc", "integer"),
            _optional("concentration_modifier", "integer", 0),
            _optional("spell_cast_id", default="", model_hidden=True),
            _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
            _optional("actual_spell_level", "integer", -1, model_hidden=True),
        ],
        [
            _call(
                "validate_srd_conjured_presence_cast",
                {
                    "caster_state": "{_caster_state_id}",
                    "spell": "{spell}",
                    **{p["name"]: "{" + p["name"] + "}" for p in intents()},
                },
                ["_presence_caster"],
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            *_cylinder_faces("conjured_point"),
            _set("_presence_focus", ["_caster_state_id"]),
            _call(
                "start_srd_condition_concentration",
                {
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "state",
                    "spell": "{spell}",
                    "targets": "{_presence_focus}",
                    "duration_rounds": 100,
                    "save_modifier": "{concentration_modifier}",
                    "spell_cast_id": "{spell_cast_id}",
                    "spell_cast_owner": "{_caster_state_id}",
                    "actual_spell_level": "{actual_spell_level}",
                },
            ),
            _set("caster_state.concentration_effect_family", "'conjured_presence'"),
            _set("caster_state.conjured_presence_source_id", "conjured_source_id"),
            _set("_presence_mode", "'celestial'"),
            _set("_presence_base_level", 7),
            _when(
                "spell == 'conjure_fey'",
                [
                    _set("_presence_mode", "'fey'"),
                    _set("_presence_base_level", 6),
                ],
            ),
            _require(
                "slot_level >= _presence_base_level", "Use an admitted spell slot for this source."
            ),
            _set(
                "_presence",
                {
                    "id": "conjured_source_id",
                    "spell": "spell",
                    "profile": "'conjured_presence'",
                    "mode": "_presence_mode",
                    "active": True,
                    "installed": True,
                    "caster": "_caster_state_id",
                    "source_caster_id": None,
                    "cast_id": "spell_cast_id",
                    "ongoing_duration": "spell_cast_id != ''",
                    "sequence": "caster_state.concentration_sequence",
                    "created_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 200",
                    "cast_caster_turn": 0,
                    "slot_level": "slot_level",
                    "spell_level": "slot_level",
                    "casting_ability": "casting_ability",
                    "casting_modifier": "casting_modifier",
                    "attack_bonus": "attack_bonus",
                    "save_dc": "save_dc",
                    "upcast": "slot_level - _presence_base_level",
                    "origin": "conjured_point",
                    "plane": "_presence_caster.metadata.plane",
                    "radius": 10,
                    "cylinder_min": "conjured_point[2] - 20",
                    "cylinder_max": "conjured_point[2] + 20",
                    "height": "fey_height",
                    "subject_id": None,
                    "event_clock": "'none'",
                    "event_turn": -1,
                    "event_target_ids": [],
                },
            ),
            _when(
                "spell == 'conjure_celestial'",
                [
                    _set(
                        "_presence_patch",
                        {
                            "cylinder_origin_face": "celestial_origin_face",
                            "cylinder_min": "_presence_cylinder_min",
                            "cylinder_max": "_presence_cylinder_max",
                        },
                    ),
                    _set("_presence", "merge(_presence, _presence_patch)"),
                ],
            ),
            _when(
                "caster_state.turns_started != null",
                [
                    _set("_presence_patch", {"cast_caster_turn": "caster_state.turns_started"}),
                    _set("_presence", "merge(_presence, _presence_patch)"),
                ],
            ),
            _when(
                "spell_cast_id != ''",
                [
                    _require(
                        "spell_cast_owner != null and _spell_cast_owner_id == _caster_state_id and "
                        "caster_state.last_spell_source_cast_id == spell_cast_id and "
                        "caster_state.last_spell_source_spell == spell",
                        "Retain the authentic committed source owner.",
                    ),
                    _set("_presence_patch", {"source_caster_id": "_spell_cast_owner_id"}),
                    _set("_presence", "merge(_presence, _presence_patch)"),
                ],
            ),
            _when(
                "state.persistent_spell_areas == null", [_set("state.persistent_spell_areas", {})]
            ),
            _when(
                "spell == 'conjure_fey'",
                [
                    _call(
                        "format_srd_information_projection",
                        {"message": "{conjured_source_id}:spirit"},
                        {"_presence_subject_id": "_info_projection_message"},
                    ),
                    _call(
                        "create_srd_conjured_fey_anchor",
                        {
                            "source_id": "{conjured_source_id}",
                            "subject_id": "{_presence_subject_id}",
                            "point": "{conjured_point}",
                            "plane": "{_presence_caster.metadata.plane}",
                            "height": "{fey_height}",
                            "appearance": "{fey_appearance}",
                            "cast_id": "{spell_cast_id}",
                        },
                    ),
                    _set("_presence_patch", {"subject_id": "_presence_subject_id"}),
                    _set("_presence", "merge(_presence, _presence_patch)"),
                ],
            ),
            _set("state.persistent_spell_areas.{conjured_source_id}", "_presence"),
            _when(
                "state.persistent_spell_area_ids == null",
                [_set("state.persistent_spell_area_ids", [])],
            ),
            _set("_presence_registered", "state.persistent_spell_area_ids"),
            _push("_presence_registered", "conjured_source_id"),
            _set("state.persistent_spell_area_ids", "_presence_registered"),
            _set("state.has_conjured_presences", True),
            _set("_presence_source_id", "conjured_source_id"),
            _set("_presence_results", []),
            _set("_presence_attack_kind", "'none'"),
            _set("_presence_damage", 0),
            _when(
                "spell == 'conjure_celestial'",
                [
                    _call(
                        "resolve_srd_celestial_initial_lights",
                        {
                            "caster_state": "{_caster_state_id}",
                            "source_id": "{conjured_source_id}",
                            "healing_targets": "{celestial_healing_targets}",
                            "searing_targets": "{celestial_searing_targets}",
                        },
                        ["_presence_results"],
                    ),
                ],
            ),
            _when(
                "spell == 'conjure_fey' and fey_attack_target != ''",
                [
                    _call(
                        "attack_srd_conjured_fey",
                        {
                            "caster_state": "{_caster_state_id}",
                            "source_id": "{conjured_source_id}",
                            "target_state": "{fey_attack_target}",
                        },
                        ["_presence_attack_kind", "_presence_damage"],
                    ),
                ],
            ),
        ],
    )


def _packet(amount, target, damage_type, divisor="1"):
    return [
        _call(
            "resolve_srd_actor_spell_modifiers",
            {
                "target_state": "{_presence.caster}",
                "lasting_state": "state",
            },
        ),
        _call(
            "modify_srd_actor_raw_damage_roll",
            {
                "actor_state": "{_presence.caster}",
                "lasting_state": "state",
                "amount": amount,
            },
            ["_ray_raw_damage"],
        ),
        _call(
            "apply_srd_qualifying_damage",
            {
                "target": target,
                "source_state_id": "{_presence.caster}",
                "amount": "{max(0, _ray_raw_damage) // " + divisor + "}",
                "damage_type": damage_type,
                "adjustment": 0,
                "game_damage_roll": True,
                "unallocated_roll_penalty": "{max(0, -_ray_raw_damage)}",
                "damage_roll_divisor": "{" + divisor + "}",
                "damage_roll_base_remainder": "{max(0, _ray_raw_damage) % " + divisor + "}",
            },
            ["_effective_damage"],
        ),
        _set("_presence_damage", "_effective_damage"),
    ]


def _celestial():
    tool = _tool_scene(
        "Resolve chosen initial lights for every actual visible creature; simultaneous "
        "saves share one real roll.",
        [
            _entity("caster_state"),
            _parameter("source_id"),
            *[
                _parameter(
                    name, "entity_id_list", resolve="entity_list", unique_items=True, max_items=64
                )
                for name in ("healing_targets", "searing_targets")
            ],
        ],
        _lookup()
        + [
            _require(
                "_presence.mode == 'celestial' and _presence.caster == _caster_state_id",
                "Use this caster's actual Celestial Cylinder.",
            ),
            _call(
                "collect_srd_celestial_visible_creatures",
                {
                    "caster_state": "{_caster_state_id}",
                    "source_id": "{source_id}",
                },
                ["_presence_visible"],
            ),
            _require(
                "len(healing_targets) + len(searing_targets) == len(_presence_visible)",
                "Choose one light for every actual visible creature in the Cylinder.",
            ),
            _loop(
                "healing_targets",
                "_presence_chosen",
                [
                    _require(
                        "_presence_chosen in _presence_visible and _presence_chosen not in "
                        "searing_targets",
                        "Healing intent must select a visible creature only once.",
                    ),
                ],
            ),
            _loop(
                "searing_targets",
                "_presence_chosen",
                [
                    _require(
                        "_presence_chosen in _presence_visible",
                        "Searing intent must select a visible creature in the Cylinder.",
                    ),
                ],
            ),
            _set("_presence_results", []),
            _set("_presence_searing_roll", 0),
            _set("_presence_searing_dice", "6 + _presence.upcast"),
            _set("_presence_healing_dice", "4 + _presence.upcast"),
            _when(
                "len(searing_targets) > 0",
                [
                    {
                        "dice": {"expression": "{_presence_searing_dice}d12"},
                        "store_as": "_presence_searing_roll",
                    }
                ],
            ),
            _loop(
                "healing_targets",
                "_presence_recipient",
                [
                    *_magic(
                        "{_presence.caster}", "{_presence_recipient}", "{_presence.spell_level}"
                    ),
                    _when(
                        "_presence_magic_allowed == true",
                        [
                            {
                                "dice": {"expression": "{_presence_healing_dice}d12"},
                                "store_as": "_presence_healing_roll",
                            },
                            _call(
                                "restore_shared_spell_healing_target",
                                {
                                    "target": "{_presence_recipient}",
                                    "amount": "{max(0, _presence_healing_roll.total + "
                                    "_presence.casting_modifier)}",
                                    "maximum_amount": "{max(0, (4 + _presence.upcast) * 12 + "
                                    "_presence.casting_modifier)}",
                                },
                            ),
                            _push(
                                "_presence_results",
                                {"target": "_presence_recipient", "light": "'healing'"},
                            ),
                        ],
                    ),
                ],
            ),
            _loop(
                "searing_targets",
                "_presence_recipient",
                [
                    *_magic(
                        "{_presence.caster}", "{_presence_recipient}", "{_presence.spell_level}"
                    ),
                    _when(
                        "_presence_magic_allowed == true",
                        [
                            _call(
                                "resolve_srd_celestial_save",
                                {
                                    "target_state": "{_presence_recipient}",
                                    "save_dc": "{_presence.save_dc}",
                                },
                                {"_presence_save": "_outcome", "_presence_save_roll": "_d20_test"},
                            ),
                            _set("_presence_divisor", 1),
                            _when("_presence_save == 'success'", [_set("_presence_divisor", 2)]),
                            *_packet(
                                "{_presence_searing_roll.total}",
                                "{_presence_recipient}",
                                "radiant",
                                "_presence_divisor",
                            ),
                            _push(
                                "_presence_results",
                                {
                                    "target": "_presence_recipient",
                                    "light": "'searing'",
                                    "save": "_presence_save",
                                    "roll": "_presence_save_roll",
                                    "damage": "_presence_damage",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            _set("_presence_event_clock", "'exploration'"),
            _set("_presence_event_turn", "state.lasting_tick"),
            _when(
                "combat.encounter_active == true",
                [
                    _set("_presence_event_clock", "'combat'"),
                    _set("_presence_event_turn", "combat.turn_sequence"),
                ],
            ),
            _set(
                "_presence_event_patch",
                {
                    "event_clock": "_presence_event_clock",
                    "event_turn": "_presence_event_turn",
                    "event_target_ids": "_presence_visible",
                },
            ),
            _set("_presence", "merge(_presence, _presence_event_patch)"),
            _set("state.persistent_spell_areas.{source_id}", "_presence"),
        ],
    )

    receipt = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_presence_event_clock"
    )
    retained = tool["effects"][receipt:]
    del tool["effects"][receipt:]
    before_roll = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_presence_results"
    )
    tool["effects"][before_roll:before_roll] = retained
    return tool


def _fey_attack():
    tool = _tool_scene(
        "The actual caster rolls a melee spell attack from its authenticated Fey body, "
        "then applies Psychic and exact fear.",
        [_entity("caster_state"), _entity("target_state"), _parameter("source_id")],
        _lookup()
        + [
            _require(
                "_presence.mode == 'fey' and _presence.caster == _caster_state_id",
                "Use this caster's actual Fey spirit.",
            ),
            *_subject("{_presence.subject_id}", "_presence_spirit"),
            _require(
                "_presence_spirit.metadata.conjured_presence.source_id == source_id and "
                "_presence_spirit.metadata.conjured_presence.cast_id == _presence.cast_id and "
                "_presence_spirit.metadata.conjured_presence.created_tick == "
                "_presence.created_tick",
                "The attack origin must be this exact source's actual spirit.",
            ),
            _call(
                "validate_srd_fey_attack_target",
                {
                    "target_state": "{_target_state_id}",
                    "point": "{_presence_spirit.position}",
                    "plane": "{_presence_spirit.metadata.plane}",
                    "height": "{_presence_spirit.height}",
                },
            ),
            *_magic("{_presence.caster}", "{_target_state_id}", "{_presence.spell_level}"),
            _set("_presence_attack_kind", "'suppressed'"),
            _set("_presence_damage", 0),
            _when(
                "_presence_magic_allowed == true",
                [
                    _set("_presence_ac", "target_state.armor_class"),
                    _when(
                        "target_state.data != null",
                        [_set("_presence_ac", "target_state.data.armor_class")],
                    ),
                    _require(
                        "_presence_ac != null", "The actual creature needs its native Armor Class."
                    ),
                    _call(
                        "resolve_srd_effective_armor_class",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "state",
                            "base_armor_class": "{_presence_ac}",
                        },
                        {"_presence_ac": "_effective_armor_class"},
                    ),
                    _call(
                        "d20_test",
                        {
                            "actor_state": "{_caster_state_id}",
                            "ability": "{_presence.casting_ability}",
                            "kind": "attack_roll",
                            "ability_modifier": "{_presence.casting_modifier}",
                            "proficiency_bonus": 0,
                            "proficiency_factor": "normal",
                            "circumstantial_modifier": (
                                "{_presence.attack_bonus - _presence.casting_modifier}"
                            ),
                            "target_number": "{_presence_ac}",
                        },
                        {
                            "_presence_attack_kind": "_attack_outcome",
                            "_presence_attack_outcome": "_outcome",
                            "_presence_attack_roll": "_d20_test",
                        },
                    ),
                    _when(
                        "_presence_attack_outcome == 'success'",
                        [
                            _set("_presence_dice", "3 + _presence.upcast"),
                            _when(
                                "_presence_attack_kind == 'critical_hit'",
                                [_set("_presence_dice", "_presence_dice * 2")],
                            ),
                            {
                                "dice": {"expression": "{_presence_dice}d12"},
                                "store_as": "_presence_attack_damage",
                            },
                            *_packet(
                                "{max(0, _presence_attack_damage.total + "
                                "_presence.casting_modifier)}",
                                "{_target_state_id}",
                                "psychic",
                            ),
                            _call(
                                "install_srd_conjured_fey_fear",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "target_state": "{_target_state_id}",
                                    "source_id": "{source_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    for sequence in _lists(tool["effects"]):
        for effect in sequence:
            if effect.get("condition") == "_presence_attack_outcome == 'success'":
                packet = effect["on_true"]
                effect["on_true"] = [
                    _call(
                        "resolve_srd_mirror_image_hit",
                        {
                            "target_state": "{_target_state_id}",
                            "attacker_state": "{_caster_state_id}",
                        },
                        ["_mirror_redirected"],
                    ),
                    _when("_mirror_redirected != true", packet),
                ]
    return tool


def _fey_fear():
    tool = _tool(
        "Install exact Frightened provenance with both actual fear origins and the "
        "caster's next-turn boundary.",
        [_entity("caster_state"), _entity("target_state"), _parameter("source_id")],
        _lookup()
        + [
            _call(
                "install_srd_timed_condition_source",
                {
                    "caster_state": "{_caster_state_id}",
                    "target_state": "{_target_state_id}",
                    "lasting_state": "state",
                    "spell": "conjure_fey",
                    "condition": "frightened",
                    "save_dc": "{_presence.save_dc}",
                    "duration_rounds": 0,
                    "repeat_save_timing": "none",
                    "actual_spell_level": "{_presence.spell_level}",
                },
                ["_timed_condition_source_id"],
            ),
            _set("_presence_fear", "state.timed_condition_sources[_timed_condition_source_id]"),
            _set("_presence_fear_turn", 1),
            _when(
                "caster_state.turns_started != null",
                [
                    _set("_presence_fear_turn", "caster_state.turns_started + 1"),
                ],
            ),
            _set(
                "_presence_fear_patch",
                {
                    "fey_source_id": "source_id",
                    "fey_spirit_id": "_presence.subject_id",
                    "fey_caster_next_start": "_presence_fear_turn",
                },
            ),
            _set("_presence_fear", "merge(_presence_fear, _presence_fear_patch)"),
            _set("state.timed_condition_sources.{_timed_condition_source_id}", "_presence_fear"),
            _when(
                "state.conjured_fey_condition_source_ids == null",
                [
                    _set("state.conjured_fey_condition_source_ids", []),
                ],
            ),
            _set("_presence_fear_registered", "state.conjured_fey_condition_source_ids"),
            _when(
                "_timed_condition_source_id not in _presence_fear_registered",
                [
                    _push("_presence_fear_registered", "_timed_condition_source_id"),
                ],
            ),
            _set("state.conjured_fey_condition_source_ids", "_presence_fear_registered"),
            _set("state.has_conjured_fey_fear", True),
            _call(
                "refresh_srd_timed_condition_target",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "state",
                },
            ),
        ],
    )

    prefix = len(_lookup())
    installation = tool["effects"][prefix:]
    tool["effects"][prefix:] = [
        _call(
            "check_srd_target_condition_immunity",
            {
                "target_state": "{_target_state_id}",
                "condition": "frightened",
                "source_state_id": "{_caster_state_id}",
                "source_is_magical": True,
            },
            ["_source_condition_immune"],
        ),
        _when("_source_condition_immune != true", installation),
    ]
    return tool


def _use_fey():
    return _public(
        _tool_scene(
            "On a later actual turn, pay one Bonus Action to teleport the live Fey to "
            "visible unoccupied space and attack.",
            [
                _entity("caster_state"),
                _parameter("source_id"),
                _array("point"),
                _entity("target_state"),
            ],
            _lookup()
            + [
                _require(
                    "_presence.mode == 'fey' and _presence.caster == _caster_state_id",
                    "Use this caster's own retained Fey.",
                ),
                _require(
                    "combat.turn_active == true and combat.active_actor == _caster_state_id and "
                    "caster_state.turns_started > _presence.cast_caster_turn",
                    "Fey's Bonus Action is available on this caster's later turns.",
                ),
                *_subject("{_presence.subject_id}", "_presence_spirit"),
                *_subject("{_caster_state_id}", "_presence_caster"),
                _require(
                    "_presence_spirit.metadata.plane == _presence_caster.metadata.plane",
                    "Fey teleport stays on the actual spirit/caster plane.",
                ),
                *_distance("{_presence_spirit.position}", "{point}", "_presence_range"),
                _require(
                    "_presence_range <= 30",
                    "Fey teleports at most30 feet from the actual space left.",
                ),
                _call(
                    "resolve_srd_conjured_point_sight",
                    {
                        "observer_state": "{_caster_state_id}",
                        "point": "{point}",
                    },
                    ["_illumination_point_can_see"],
                ),
                _require(
                    "_illumination_point_can_see == true", "The caster must see Fey's destination."
                ),
                _call(
                    "validate_srd_fey_space",
                    {
                        "point": "{point}",
                        "plane": "{_presence_spirit.metadata.plane}",
                        "height": "{_presence_spirit.height}",
                        "excluded_id": "{_presence_spirit.id}",
                    },
                ),
                _call(
                    "validate_srd_fey_attack_target",
                    {
                        "target_state": "{_target_state_id}",
                        "point": "{point}",
                        "plane": "{_presence_spirit.metadata.plane}",
                        "height": "{_presence_spirit.height}",
                    },
                ),
                *_magic("{_presence.caster}", "{_presence.subject_id}", "{_presence.spell_level}"),
                _require(
                    "_presence_magic_allowed == true",
                    "Antimagic suppresses this actual Fey exertion.",
                ),
                _call(
                    "consume_srd_bonus_action",
                    {"actor_state": "{_caster_state_id}", "bonus_action": "conjure_fey"},
                ),
                _call(
                    "move_srd_travel_subject",
                    {
                        "subject": "{_presence.subject_id}",
                        "destination": "{point}",
                        "plane": "{_presence_spirit.metadata.plane}",
                        "teleport": True,
                    },
                ),
                _call(
                    "attack_srd_conjured_fey",
                    {
                        "caster_state": "{_caster_state_id}",
                        "target_state": "{_target_state_id}",
                        "source_id": "{source_id}",
                    },
                    ["_presence_attack_kind", "_presence_damage"],
                ),
            ],
        ),
        "magic",
        "Conjured Fey",
        [("Attack", "_presence_attack_kind"), ("Psychic damage", "_presence_damage")],
    )


def _focus():
    return _tool(
        "Read this source's exact current caster Concentration without retiring or "
        "changing positions.",
        [_parameter("source_id")],
        _lookup(False)
        + [
            _call(
                "qualify_srd_conjured_presence_caster",
                {
                    "caster_state": "{_presence.caster}",
                    "source_id": "{source_id}",
                },
                ["_presence_focus_live"],
            ),
        ],
    )


def _caster_focus():
    return _tool(
        "Qualify actual source/caster/sequence and current Concentration.",
        [_entity("caster_state"), _parameter("source_id")],
        _lookup(False)
        + [
            _set(
                "_presence_focus_live",
                "_presence.active == true and "
                "state.lasting_tick < _presence.expires_tick and "
                "caster_state.concentration_spell_id == _presence.spell and "
                "caster_state.concentration_sequence == _presence.sequence and "
                "caster_state.conjured_presence_source_id == source_id",
            ),
        ],
    )


def _end():
    return _tool_scene(
        "Retire this exact source and its actual spirit; independent next-turn fear "
        "remains separate.",
        [_parameter("source_id")],
        _lookup(False)
        + [
            _set("_presence_was_active", "_presence.active == true"),
            _set("_presence_patch", {"active": False, "installed": False}),
            _set("_presence", "merge(_presence, _presence_patch)"),
            _set("state.persistent_spell_areas.{source_id}", "_presence"),
            _when(
                "_presence.mode == 'fey' and _presence_was_active == true",
                [
                    *_snapshot(),
                    {
                        "spatial_mutation": {
                            "scene": "state.spatial_scene",
                            "map_id": "{state.spatial_scene.map.id}",
                            "expected_revision": "{_info_snapshot.geometry_revision}",
                            "target": {"kind": "entity", "id": "{_presence.subject_id}"},
                            "patch": {
                                "active": False,
                                "movement": "passable",
                                "visibility": "clear",
                            },
                        },
                        "store_as": "_presence_retired_anchor",
                    },
                ],
            ),
        ],
    )


def _refresh():
    return _tool(
        "Refresh actual conjured sources on the existing clock and Concentration boundaries.",
        [],
        [
            _when(
                "state.has_conjured_presences == true",
                [
                    _set("_presence_keys", "state.persistent_spell_area_ids"),
                    _loop(
                        "_presence_keys",
                        "_presence_refresh_id",
                        [
                            _set(
                                "_presence_refresh",
                                "state.persistent_spell_areas[_presence_refresh_id]",
                            ),
                            _when(
                                "_presence_refresh.profile == 'conjured_presence' and "
                                "_presence_refresh.active == true",
                                [
                                    _call(
                                        "qualify_srd_conjured_presence_focus",
                                        {"source_id": "{_presence_refresh_id}"},
                                        ["_presence_focus_live"],
                                    ),
                                    _when(
                                        "_presence_focus_live != true",
                                        [
                                            _call(
                                                "end_srd_conjured_presence_source",
                                                {"source_id": "{_presence_refresh_id}"},
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


def _fear_turn():
    return _tool(
        "Retire only this caster's exact Fey hit conditions at its actual next turn start.",
        [_entity("caster_state")],
        [
            _when(
                "state.has_conjured_fey_fear == true",
                [
                    _set("_presence_fear_ids", "state.conjured_fey_condition_source_ids"),
                    _loop(
                        "_presence_fear_ids",
                        "_presence_fear_id",
                        [
                            _set(
                                "_presence_fear_record",
                                "state.timed_condition_sources[_presence_fear_id]",
                            ),
                            _when(
                                "_presence_fear_record.installed == true and "
                                "_presence_fear_record.spell_id == 'conjure_fey' and "
                                "_presence_fear_record.fey_source_id != null and "
                                "_presence_fear_record.caster_state_id == _caster_state_id and "
                                "caster_state.turns_started >= "
                                "_presence_fear_record.fey_caster_next_start",
                                [
                                    _call(
                                        "retire_srd_timed_condition_source",
                                        {
                                            "source_id": "{_presence_fear_id}",
                                            "lasting_state": "state",
                                        },
                                    ),
                                    _call(
                                        "refresh_srd_timed_condition_target",
                                        {
                                            "target_state": (
                                                "{_presence_fear_record.target_state_id}"
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


def _light():
    return _tool(
        "Derive Bright Light from actual live Celestial Cylinders, not an ambient-zone verdict.",
        [_array("point"), _parameter("plane")],
        [
            _set("_presence_magic_bright", False),
            _when(
                "state.has_conjured_presences == true",
                [
                    _set("_presence_light_keys", "state.persistent_spell_area_ids"),
                    _loop(
                        "_presence_light_keys",
                        "_presence_light_id",
                        [
                            _set(
                                "_presence_light",
                                "state.persistent_spell_areas[_presence_light_id]",
                            ),
                            _when(
                                "_presence_light.profile == 'conjured_presence' and "
                                "_presence_light.mode == 'celestial' and "
                                "_presence_light.active == true and "
                                "state.lasting_tick < _presence_light.expires_tick and "
                                "_presence_light.plane == plane and "
                                "point[2] >= _presence_light.cylinder_min and point[2] <= "
                                "_presence_light.cylinder_max and "
                                "(point[0] - _presence_light.origin[0]) ** 2 + "
                                "(point[1] - _presence_light.origin[1]) ** 2 <= 100",
                                [
                                    _call(
                                        "qualify_srd_conjured_presence_focus",
                                        {"source_id": "{_presence_light_id}"},
                                        ["_presence_focus_live"],
                                    ),
                                    _when(
                                        "_presence_focus_live == true",
                                        [_set("_presence_magic_bright", True)],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _fear_live():
    return _tool(
        "Qualify the exact Fey hit condition against its real caster's next start.",
        [_entity("caster_state"), _parameter("condition_source_id")],
        [
            _set("_presence_fear_record", "state.timed_condition_sources[condition_source_id]"),
            _set(
                "_presence_fear_parent",
                "state.persistent_spell_areas[_presence_fear_record.fey_source_id]",
            ),
            _require(
                "_presence_fear_record.spell_id == 'conjure_fey' and "
                "_presence_fear_record.caster_state_id == _caster_state_id and "
                "_presence_fear_parent.profile == 'conjured_presence' and "
                "_presence_fear_parent.mode == 'fey' and _presence_fear_parent.caster == "
                "_caster_state_id and "
                "_presence_fear_parent.subject_id == _presence_fear_record.fey_spirit_id",
                "Authenticate both origins from the exact actual Fey hit and parent source.",
            ),
            _set(
                "_presence_fear_live",
                "_presence_fear_record.installed == true and "
                "_presence_fear_record.spell_id == 'conjure_fey' and "
                "(caster_state.turns_started == null or "
                "caster_state.turns_started < _presence_fear_record.fey_caster_next_start)",
            ),
        ],
    )


def _fear_projection():
    return _tool(
        "Qualify Fey hit conditions for the existing Frightened projection without another "
        "condition pool.",
        [_entity("target_state")],
        [
            _set("_presence_frightened", False),
            _when(
                "state.has_conjured_fey_fear == true and "
                "target_state.timed_condition_source_ids != null",
                [
                    _set("_presence_fear_keys", "target_state.timed_condition_source_ids"),
                    _loop(
                        "_presence_fear_keys",
                        "_presence_fear_key",
                        [
                            _set(
                                "_presence_fear_candidate",
                                "state.timed_condition_sources[_presence_fear_key]",
                            ),
                            _when(
                                "_presence_fear_candidate.installed == true and "
                                "_presence_fear_candidate.spell_id == 'conjure_fey' and "
                                "_presence_fear_candidate.fey_source_id != null and "
                                "_presence_fear_candidate.target_state_id == _target_state_id",
                                [
                                    _call(
                                        "qualify_srd_conjured_fey_fear",
                                        {
                                            "caster_state": (
                                                "{_presence_fear_candidate.caster_state_id}"
                                            ),
                                            "condition_source_id": "{_presence_fear_key}",
                                        },
                                        ["_presence_fear_live"],
                                    ),
                                    _when(
                                        "_presence_fear_live == true",
                                        [
                                            _call(
                                                "check_srd_target_condition_immunity",
                                                {
                                                    "target_state": "{_target_state_id}",
                                                    "condition": "frightened",
                                                    "source_state_id": (
                                                        "{_presence_fear_candidate.caster_state_id}"
                                                    ),
                                                    "source_is_magical": True,
                                                },
                                                ["_source_condition_immune"],
                                            ),
                                            _when(
                                                "_source_condition_immune != true",
                                                [
                                                    _set("_presence_frightened", True),
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
    )


def _fear_visible():
    see_origin = _when(
        "_presence_fear_origin.active == true and "
        "(_presence_fear_origin.metadata.participant_ref == "
        "_presence_fear_candidate.caster_state_id or "
        "_presence_fear_origin.id == _presence_fear_candidate.fey_spirit_id)",
        [
            _call(
                "resolve_srd_conjured_subject_sight",
                {
                    "observer_state": "{_actor_state_id}",
                    "subject": "{_presence_fear_origin.id}",
                },
                ["_illumination_point_can_see", "_illumination_point_plane"],
            ),
            _when(
                "_illumination_point_can_see == true and "
                "_illumination_point_plane == _presence_fear_origin.metadata.plane",
                [
                    _set("_presence_fear_visible", True),
                ],
            ),
        ],
    )
    candidate = [
        _set("_presence_fear_candidate", "state.timed_condition_sources[_presence_fear_key]"),
        _when(
            "_presence_fear_candidate.installed == true and "
            "_presence_fear_candidate.spell_id == 'conjure_fey' and "
            "_presence_fear_candidate.fey_source_id != null and "
            "_presence_fear_candidate.target_state_id == _actor_state_id",
            [
                _call(
                    "qualify_srd_conjured_fey_fear",
                    {
                        "caster_state": ("{_presence_fear_candidate.caster_state_id}"),
                        "condition_source_id": "{_presence_fear_key}",
                    },
                    ["_presence_fear_live"],
                ),
                _when(
                    "_presence_fear_live == true",
                    [
                        _loop("_presence_fear_bodies", "_presence_fear_origin", [see_origin]),
                    ],
                ),
            ],
        ),
    ]
    return _tool_scene(
        "Read actual visibility of both authentic Fey fear origins without lifecycle mutation.",
        [_entity("actor_state")],
        [
            _set("_presence_fear_visible", False),
            _when(
                "state.has_conjured_fey_fear == true and 'frightened' in actor_state.conditions",
                [
                    _set("_presence_fear_keys", "actor_state.timed_condition_source_ids"),
                    _set("_presence_fear_bodies", "state.spatial_scene.entities"),
                    _loop("_presence_fear_keys", "_presence_fear_key", candidate),
                ],
            ),
        ],
    )


def _fear_movement():
    "A straight translation cannot reduce distance to either actual Box fear origin at any"
    "point."
    actor = "_presence_moving"
    origin = "_presence_fear_origin"
    gaps = [
        f"max({actor}.position[{axis}] - {actor}.footprint.half_extents[{axis}] - "
        f"{origin}.position[{axis}] - {origin}.footprint.half_extents[{axis}], 0) - "
        f"max({origin}.position[{axis}] - {origin}.footprint.half_extents[{axis}] - "
        f"{actor}.position[{axis}] - {actor}.footprint.half_extents[{axis}], 0)"
        for axis in (0, 1)
    ] + [
        f"max({actor}.position[2] - {origin}.position[2] - {origin}.height, 0) - "
        f"max({origin}.position[2] - {actor}.position[2] - {actor}.height, 0)"
    ]
    derivative = " + ".join(
        f"_presence_fear_gap[{axis}] * (destination[{axis}] - {actor}.position[{axis}])"
        for axis in (0, 1, 2)
    )
    origin_effect = _when(
        f"{origin}.active == true and {origin}.metadata.plane == {actor}.metadata.plane and "
        f"({origin}.metadata.participant_ref == _presence_fear_candidate.caster_state_id or "
        f"{origin}.id == _presence_fear_candidate.fey_spirit_id)",
        [
            _require(f"{origin}.footprint.kind == 'box'", "Fey fear needs an actual Box origin."),
            _set("_presence_fear_gap", gaps),
            _require(
                derivative + " >= 0", "Frightened movement cannot come closer to caster or Fey."
            ),
        ],
    )
    candidate = [
        _set("_presence_fear_candidate", "state.timed_condition_sources[_presence_fear_key]"),
        _when(
            "_presence_fear_candidate.installed == true and "
            "_presence_fear_candidate.spell_id == 'conjure_fey' and "
            "_presence_fear_candidate.suppressed != true and "
            "_presence_fear_candidate.fey_source_id != null and "
            "_presence_fear_candidate.target_state_id == _actor_state_id",
            [
                _call(
                    "qualify_srd_conjured_fey_fear",
                    {
                        "caster_state": ("{_presence_fear_candidate.caster_state_id}"),
                        "condition_source_id": "{_presence_fear_key}",
                    },
                    ["_presence_fear_live"],
                ),
                _when(
                    "_presence_fear_live == true",
                    [
                        _loop("_presence_fear_bodies", origin, [origin_effect]),
                    ],
                ),
            ],
        ),
    ]
    return _tool_scene(
        "Prevent genuine voluntary ordinary movement closer to either authentic Fey fear origin.",
        [_entity("actor_state"), _array("destination")],
        [
            _when(
                "state.has_conjured_fey_fear == true and 'frightened' in "
                "actor_state.conditions and "
                "actor_state.timed_condition_source_ids != null",
                [
                    *_subject("{_actor_state_id}", actor),
                    _require(
                        f"{actor}.footprint.kind == 'box'",
                        "Fey fear movement needs an actual Box mover.",
                    ),
                    _set("_presence_fear_keys", "actor_state.timed_condition_source_ids"),
                    _set("_presence_fear_bodies", "state.spatial_scene.entities"),
                    _loop("_presence_fear_keys", "_presence_fear_key", candidate),
                ],
            ),
        ],
    )


def _celestial_save():
    return _tool(
        "Roll actual Dexterity through native D20 and admitted creature facts.",
        [_entity("target_state"), _parameter("save_dc", "integer")],
        [
            _require(
                "target_state.saving_throw_modifiers.dexterity != null",
                "Celestial needs the creature's actual Dexterity saving-throw modifier.",
            ),
            _call(
                "d20_test",
                {
                    "actor_state": "{_target_state_id}",
                    "ability": "dexterity",
                    "kind": "saving_throw",
                    "ability_modifier": "{target_state.saving_throw_modifiers.dexterity}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "circumstantial_modifier": 0,
                    "target_number": "{save_dc}",
                },
                ["_outcome", "_d20_test"],
            ),
        ],
    )


def tools(runtime):
    return {
        "resolve_srd_conjured_point_sight": _sight(runtime["tools"]),
        "resolve_srd_conjured_subject_sight": _subject_sight(runtime["tools"]),
        "resolve_srd_celestial_body_segment": _geometry(runtime["tools"]),
        "resolve_srd_celestial_save": _celestial_save(),
        "qualify_srd_conjured_fey_fear": _fear_live(),
        "project_srd_conjured_fey_frightened": _fear_projection(),
        "resolve_srd_conjured_fey_fear_visibility": _fear_visible(),
        "check_srd_conjured_fey_movement": _fear_movement(),
        "resolve_srd_celestial_intent_body_segment": _intent_geometry(runtime["tools"]),
        "collect_srd_celestial_intent_creatures": _intent_targets(),
        "validate_srd_fey_space": _tool_scene(
            "Prove actual full Medium body occupancy.",
            [
                _array("point"),
                _parameter("plane"),
                _parameter("height", "number", minimum=0.000001),
                _optional("excluded_id", default=""),
            ],
            _fey_space(),
        ),
        "validate_srd_fey_attack_target": _fey_target(),
        "validate_srd_conjured_presence_cast": _validate(),
        "create_srd_conjured_fey_anchor": _create_fey(),
        "install_srd_conjured_presence": _install(),
        "collect_srd_celestial_visible_creatures": _visible_targets(),
        "resolve_srd_celestial_initial_lights": _celestial(),
        "attack_srd_conjured_fey": _fey_attack(),
        "install_srd_conjured_fey_fear": _fey_fear(),
        "use_srd_conjured_fey": _use_fey(),
        "qualify_srd_conjured_presence_focus": _focus(),
        "qualify_srd_conjured_presence_caster": _caster_focus(),
        "end_srd_conjured_presence_source": _end(),
        "refresh_srd_conjured_presences": _refresh(),
        "start_srd_conjured_fey_fear_turn": _fear_turn(),
        "resolve_srd_conjured_presence_light": _light(),
    }


def bind_runtime_consumers(text):
    "Bind the existing paid casting/clock/light owners; final Source and reaction joins"
    "stay external."
    runtime = _load_runtime(text)
    emitted = tools(runtime)
    if "install_srd_conjured_presence" in runtime["tools"]:
        intent_frames = {
            name
            for name, tool in runtime["tools"].items()
            if "conjured_point" in {p["name"] for p in tool["parameters"]}
        }
        for name in sorted(intent_frames):
            tool = emitted.get(name, deepcopy(runtime["tools"][name]))
            existing = {p["name"] for p in tool["parameters"]}
            tool["parameters"] += [p for p in intents() if p["name"] not in existing]
            for sequence in _lists(tool["effects"]):
                for effect in sequence:
                    if effect.get("call") in intent_frames:
                        effect["params"].update(
                            {p["name"]: "{" + p["name"] + "}" for p in intents()}
                        )
            emitted[name] = tool
        for tool in emitted.values():
            for sequence in _lists(tool["effects"]):
                for effect in sequence:
                    if effect.get("call") == "validate_srd_fey_attack_target":
                        effect["params"]["caster_state"] = "{_caster_state_id}"
        _normalize(emitted)
        return _replace_tools(text, emitted)
    for name in ("cast_spell", "cast_spell_outside_combat"):
        if name not in runtime["tools"]:
            continue
        tool = deepcopy(runtime["tools"][name])
        existing = {p["name"] for p in tool["parameters"]}
        tool["parameters"] += [p for p in intents() if p["name"] not in existing]
        sequences = list(_lists(tool["effects"]))
        if any(e.get("call") == "cast_spell" for s in sequences for e in s):
            for sequence in sequences:
                for e in sequence:
                    if e.get("call") == "cast_spell":
                        e["params"].update({p["name"]: "{" + p["name"] + "}" for p in intents()})
        else:
            qualifier = _when(
                "_spell_definition.family == 'conjured_presence'",
                [
                    _require(
                        "targets == null or len(targets) == 0",
                        "Use the source's actual presence intents.",
                    ),
                    _call(
                        "validate_srd_conjured_presence_cast",
                        {
                            "caster_state": "{_caster_state_id}",
                            "spell": "{spell}",
                            **{p["name"]: "{" + p["name"] + "}" for p in intents()},
                        },
                    ),
                ],
            )
            payment = [
                s
                for s in sequences
                if any(e.get("call") == "resolve_srd_spell_materials" for e in s)
            ]
            if len(payment) != 1:
                raise ValueError("Retain exactly one actual prepayment material boundary.")
            index = next(
                i
                for i, e in enumerate(payment[0])
                if e.get("call") == "resolve_srd_spell_materials"
            )
            payment[0].insert(index, qualifier)
            dispatch = _when(
                "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
                "_spell_definition.family == 'conjured_presence'",
                [
                    {
                        "call": "install_srd_conjured_presence",
                        "export_context": {
                            name: name
                            for name in (
                                "_presence_source_id",
                                "_presence_results",
                                "_presence_attack_kind",
                                "_presence_damage",
                            )
                        },
                        "params": {
                            "caster_state": "{_caster_state_id}",
                            "spell": "{spell}",
                            "slot_level": "{_slot_level}",
                            "casting_ability": "{_casting_profile.ability}",
                            "casting_modifier": "{_casting_profile.modifier}",
                            "attack_bonus": "{_casting_profile.attack_bonus}",
                            "save_dc": "{_casting_profile.save_dc}",
                            "concentration_modifier": (
                                "{_casting_profile.concentration_save_modifier}"
                            ),
                            "spell_cast_id": "{_spell_source_cast_id}",
                            "spell_cast_owner": "{_caster_state_id}",
                            "actual_spell_level": "{_slot_level}",
                            **{p["name"]: "{" + p["name"] + "}" for p in intents()},
                        },
                    },
                ],
            )
            boundaries = [
                s
                for s in sequences
                if any(
                    e.get("set", {}).get("field") == "_card_slots_after"
                    and e["set"].get("value") == 0
                    for e in s
                )
            ]
            if len(boundaries) != 1:
                raise ValueError("Retain exactly one final actual paid spell dispatch boundary.")
            index = next(
                i
                for i, e in enumerate(boundaries[0])
                if e.get("set", {}).get("field") == "_card_slots_after"
                and e["set"].get("value") == 0
            )
            boundaries[0].insert(index, dispatch)
        emitted[name] = tool
    for name in ("advance_srd_lasting_clock", "processTurnEffects"):
        if name in runtime["tools"]:
            tool = deepcopy(runtime["tools"][name])
            tool["effects"].append(
                _when(
                    "state.has_conjured_presences == true",
                    [
                        _call("refresh_srd_conjured_presences", {}),
                    ],
                )
            )
            emitted[name] = tool
    if "begin_combat_turn" in runtime["tools"]:
        turn = deepcopy(runtime["tools"]["begin_combat_turn"])
        boundary = next(
            i
            for i, e in enumerate(turn["effects"])
            if e.get("set", {}).get("field") == "actor.turns_started"
            and e["set"].get("value") == "actor.turns_started + 1"
        )
        turn["effects"].insert(
            boundary + 1,
            _when(
                "state.has_conjured_fey_fear == true",
                [
                    _call(
                        "start_srd_conjured_fey_fear_turn",
                        {"caster_state": "{_presence_turn_actor_id}"},
                    ),
                ],
            ),
        )
        turn["effects"].insert(0, _set("_presence_turn_actor_id", "_actor_id"))
        emitted["begin_combat_turn"] = turn
    movement_name = (
        "move_srd_scene_creature_environment_segment"
        if "move_srd_scene_creature_environment_segment" in runtime["tools"]
        else "move_srd_scene_creature_node"
    )
    movement = deepcopy(runtime["tools"][movement_name])
    movement["effects"].insert(
        0,
        _when(
            "state.has_conjured_fey_fear == true",
            [
                _call(
                    "check_srd_conjured_fey_movement",
                    {
                        "actor_state": "{_actor_state_id}",
                        "destination": "{destination}",
                    },
                ),
            ],
        ),
    )
    emitted[movement_name] = movement
    d20 = deepcopy(runtime["tools"]["d20_test"])
    boundary = next(
        i
        for i, e in enumerate(d20["effects"])
        if e.get("set", {}).get("field") == "_effective_disadvantage"
    )
    d20["effects"][boundary:boundary] = [
        _when(
            "actor_state != null and state.has_conjured_fey_fear == true and "
            "'frightened' in "
            "actor_state.conditions and "
            "(kind == 'ability_check' or kind == 'attack_roll')",
            [
                _call(
                    "resolve_srd_conjured_fey_fear_visibility",
                    {
                        "actor_state": "{_actor_state_id}",
                    },
                    ["_presence_fear_visible"],
                ),
                _when("_presence_fear_visible == true", [_set("_condition_disadvantage", True)]),
            ],
        ),
    ]
    emitted["d20_test"] = d20
    control = deepcopy(runtime["tools"]["resolve_srd_control_behavior_conditions"])
    boundary = next(
        i
        for i, e in enumerate(control["effects"])
        if e.get("condition") == "target_state.timed_condition_unowned_frightened != null"
    )
    control["effects"][boundary:boundary] = [
        _when(
            "state.has_conjured_fey_fear == true",
            [
                _call(
                    "project_srd_conjured_fey_frightened",
                    {
                        "target_state": "{_target_state_id}",
                    },
                    ["_presence_frightened"],
                ),
                _when("_presence_frightened == true", [_set("_control_frightened", True)]),
            ],
        ),
    ]
    emitted["resolve_srd_control_behavior_conditions"] = control
    end = deepcopy(runtime["tools"]["end_srd_concentration_node"])
    end["effects"].insert(
        0,
        _when(
            "source_state.concentration_effect_family == 'conjured_presence' and "
            "source_state.conjured_presence_source_id != null",
            [
                _call(
                    "end_srd_conjured_presence_source",
                    {"source_id": "{source_state.conjured_presence_source_id}"},
                ),
            ],
        ),
    )
    emitted["end_srd_concentration_node"] = end
    light_name = (
        "resolve_srd_information_light_node"
        if "resolve_srd_information_light_node" in runtime["tools"]
        else "resolve_srd_information_light"
    )
    light = deepcopy(runtime["tools"][light_name])
    final = [
        s
        for s in _lists(light["effects"])
        if any(e.get("condition") == "_light_magic_dark == true" for e in s)
    ]
    if len(final) != 1:
        raise ValueError("Preserve the one existing final magical-light decision.")
    index = next(
        i for i, e in enumerate(final[0]) if e.get("condition") == "_light_magic_dark == true"
    )
    final[0][index:index] = [
        _when(
            "state.has_conjured_presences == true",
            [
                _call(
                    "resolve_srd_conjured_presence_light",
                    {"point": "{point}", "plane": "{plane}"},
                    ["_presence_magic_bright"],
                ),
                _when("_presence_magic_bright == true", [_set("_light_magic_bright", True)]),
            ],
        )
    ]
    emitted[light_name] = light
    for tool in emitted.values():
        for sequence in _lists(tool["effects"]):
            for effect in sequence:
                if effect.get("call") == "validate_srd_fey_attack_target":
                    effect["params"]["caster_state"] = "{_caster_state_id}"
    _normalize(emitted)
    return _replace_tools(text, emitted)
