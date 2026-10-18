"""Derive immediate combat-area recipients before the retained casting owners run."""

from copy import deepcopy

from .srd_control_behaviors import _each, _entity, _param, _push, _when
from .srd_lite_attacks import _effect_names
from .srd_lite_coordinate_lines import bind_rectangular_directional_query
from .srd_lite_profiles import lists
from .srd_scene_relations import _tool_scene
from .srd_sunburst_geometry import _body_nearest_call
from .srd_timed_actor_defenses import _call, _require, _set

AREAS = ("acid_splash", "burning_hands", "circle_of_death", "fireball", "lightning_bolt")
AREA_CONDITION = " or ".join("spell == " + repr(spell) for spell in AREAS)
CONDITION = "combat.encounter_active == true and (" + AREA_CONDITION + ")"
QUALIFY = "qualify_srd_lite_instant_combat_area"
EXPORTS = (
    "_instant_targets",
    "_instant_half_cover",
    "_instant_three_quarters",
    "_instant_objects",
    "_instant_origin_distance",
)


def _intent():
    """Retain actual source/direction choices and explicitly declared Line cross-section facts."""
    return [
        _param(
            name,
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            min_items=0,
            max_items=3,
        )
        for name in ("instant_area_apex", "instant_area_direction", "instant_area_up")
    ] + [
        _param(
            "instant_area_height",
            "number",
            required=False,
            default=0,
            minimum=0,
            maximum=1000000,
            description="In combat, declare the Line vertical extent from the scene. "
            "SRD fixes its length and width but gives no height; 0 is unspecified.",
        ),
        _param(
            "instant_area_include_origin",
            "boolean",
            required=False,
            default=False,
            description="Choose whether the Cone or Line includes its origin.",
        ),
    ]


def _sphere_size(tool):
    """Reuse the qualified Sphere owner with Sunburst's unchanged defaults."""
    tool["parameters"] += [
        _param("range_feet", "integer", required=False, default=150, minimum=1),
        _param("radius_feet", "integer", required=False, default=60, minimum=1),
    ]
    ranged = sized = 0
    for rows in lists(tool["effects"]):
        for row in rows:
            requirement = row.get("require", {})
            if "<= 150 ** 2" in requirement.get("condition", ""):
                requirement["condition"] = requirement["condition"].replace(
                    "<= 150 ** 2", "<= range_feet ** 2"
                )
                requirement["error"] = "The actual area point is beyond this spell's range."
                ranged += 1
            if "<= 60 ** 2" in row.get("condition", ""):
                row["condition"] = row["condition"].replace("<= 60 ** 2", "<= radius_feet ** 2")
                sized += 1
    if (ranged, sized) != (1, 1):
        raise ValueError("The qualified point-Sphere boundary changed.")


def _objects():
    """Collect actual fire-area scenery for the existing ignition rule, without HP damage."""
    return [
        _set("_instant_objects", []),
        _when(
            "spell == 'fireball' or spell == 'burning_hands'",
            [
                _when(
                    "spell == 'fireball'",
                    [
                        {
                            "spatial_query": {
                                "scene": "state.spatial_scene",
                                "operation": "area",
                                "query": {
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "shape": {
                                        "kind": "sphere",
                                        "center": "{_instant_origin}",
                                        "radius": "{_instant_profile.area_size_feet}",
                                    },
                                    "include": ["entities"],
                                    "max_results": 4096,
                                },
                            },
                            "store_as": "_instant_object_query",
                        },
                        _require(
                            "_instant_object_query.truncated != true",
                            "The fire area exceeds the supported scene recipient bound.",
                        ),
                        _set("_instant_hit_ids", []),
                        _set("_instant_hit_rows", "_instant_object_query.results"),
                        _each(
                            "_instant_hit_rows",
                            "_instant_hit",
                            [_push("_instant_hit_ids", "_instant_hit.id")],
                        ),
                    ],
                ),
                _set("_instant_scene_rows", "state.spatial_scene.entities"),
                _each(
                    "_instant_scene_rows",
                    "_instant_object",
                    [
                        _when(
                            "_instant_object.id in _instant_hit_ids "
                            "and _instant_object.active == true "
                            "and _instant_object.metadata.is_object == true "
                            "and _instant_object.metadata.participant_ref == null "
                            "and _instant_object.metadata.plane == _instant_plane "
                            "and _instant_object.metadata.worn_by == null "
                            "and _instant_object.metadata.carried_by == null",
                            [
                                _body_nearest_call(
                                    "_instant_object", "_instant_origin", "_instant_object_near"
                                ),
                                _call(
                                    "srd_information_barriers",
                                    {
                                        "start": "{_instant_origin}",
                                        "end": "{_instant_object_near}",
                                        "rule": "solid",
                                        "plane": "{_instant_plane}",
                                    },
                                    ("_information_blocked",),
                                ),
                                _when(
                                    "_information_blocked != true",
                                    [
                                        _require(
                                            "_instant_object.metadata.flammable != null",
                                        "Record actual combustibility of scenery "
                                        "in the fire area before casting.",
                                        ),
                                        _require(
                                            "_instant_object.metadata.flammable != true or "
                                            "_instant_object.metadata.srd_ordinary_flame.kind "
                                            "== 'ordinary_flame'",
                                        "Record actual ordinary fuel facts "
                                        "for scenery in the fire area before casting.",
                                        ),
                                        _require(
                                            "len(_instant_objects) < 64",
                                            "Resolve at most 64 actual fire-area objects.",
                                        ),
                                        _push("_instant_objects", "_instant_object.id"),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]


def _qualifier():
    """Share actual Sphere, Cone, Line, Cover and ignition primitives without changing profiles."""
    return _tool_scene(
        "Derive combat recipients from the actual approved area before targeting "
        "consequences or costs.",
        [
            _entity("caster_state"),
            _param("spell"),
            _param(
                "environment_origin", "array", items={"type": "number"}, min_items=3, max_items=3
            ),
            *_intent(),
        ],
        [
            _require(AREA_CONDITION, "Use this owner for the selected immediate area spells."),
            _set("_instant_profile", "definitions.spells[spell]"),
            _require(
                "_instant_profile.family == 'instantaneous_area_save_damage'",
                "Use the approved immediate area profile.",
            ),
            _when(
                "spell == 'burning_hands' or spell == 'lightning_bolt'",
                [
                    _set("_instant_rectangle", "spell == 'lightning_bolt'"),
                    _call(
                        "collect_srd_cone_creature_targets",
                        {
                            "source_state": "{_caster_state_id}",
                            "length_feet": "{_instant_profile.area_size_feet}",
                            "width_feet": "{_instant_profile.area_width_feet}",
                            "apex": "{instant_area_apex}",
                            "direction": "{instant_area_direction}",
                            "rectangular": "{_instant_rectangle}",
                            "up": "{instant_area_up}",
                            "height_feet": "{instant_area_height}",
                            "include_origin": "{instant_area_include_origin}",
                        },
                        (
                            "_srd_cone_targets",
                            "_srd_cone_origin",
                            "_srd_cone_source",
                            "_cone_hit_ids",
                        ),
                    ),
                    _set("_instant_candidates", "_srd_cone_targets"),
                    _set("_instant_origin", "_srd_cone_origin"),
                    _set("_instant_plane", "_srd_cone_source.metadata.plane"),
                    _set("_instant_hit_ids", "_cone_hit_ids"),
                    _set("_instant_origin_distance", 0),
                ],
                [
                    _call(
                        "collect_srd_sunburst_creature_targets",
                        {
                            "caster_state": "{_caster_state_id}",
                            "sunburst_origin": "{environment_origin}",
                            "range_feet": "{_instant_profile.range_feet}",
                            "radius_feet": "{_instant_profile.area_size_feet}",
                        },
                        (
                            "_sunburst_sphere_targets",
                            "_sunburst_origin",
                            "_sunburst_plane",
                            "_sunburst_range_start",
                        ),
                    ),
                    _set("_instant_candidates", "_sunburst_sphere_targets"),
                    _set("_instant_origin", "_sunburst_origin"),
                    _set("_instant_plane", "_sunburst_plane"),
                    _set(
                        "_instant_origin_distance",
                        "ceil(((_instant_origin[0] - _sunburst_range_start[0]) ** 2 + "
                        "(_instant_origin[1] - _sunburst_range_start[1]) ** 2 + "
                        "(_instant_origin[2] - _sunburst_range_start[2]) ** 2) ** 0.5)",
                    ),
                ],
            ),
            _call(
                "collect_srd_sunburst_cover_targets",
                {
                    "source_state": "{_caster_state_id}",
                    "targets": "{_instant_candidates}",
                    "origin": "{_instant_origin}",
                },
                ("_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters"),
            ),
            _set("_instant_targets", "_srd_area_targets"),
            _set("_instant_half_cover", []),
            _set("_instant_three_quarters", []),
            _when(
                "_instant_profile.save_ability == 'dexterity'",
                [
                    _set("_instant_half_cover", "_srd_area_half_cover"),
                    _set("_instant_three_quarters", "_srd_area_three_quarters"),
                ],
            ),
            *_objects(),
        ],
    )


def bind_instant_combat_areas(runtime):
    """Qualify genuine public and retained staged casts; leave exploration's fiction path intact."""
    tools = runtime["tools"]
    profiles = deepcopy(runtime["definitions"]["spells"])
    headers = deepcopy(runtime["definitions"]["spell_casting"])
    bind_rectangular_directional_query(runtime)
    _sphere_size(tools["collect_srd_sunburst_creature_targets"])
    tools[QUALIFY] = _qualifier()
    projected = {
        name: "_instant_cast_" + name
        for name in (
            "targets",
            "half_cover_targets",
            "three_quarters_cover_targets",
            "area_object_targets",
            "shatter_object_targets",
            "target",
            "_target_id",
            "origin_distance",
        )
    }
    for name in ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"):
        owner = tools[name]
        owner["parameters"] += _intent()
        qualify = _call(
            QUALIFY,
            {
                "caster_state": "{_caster_state_id}",
                "spell": "{spell}",
                "environment_origin": "{environment_origin}",
                **{p["name"]: "{" + p["name"] + "}" for p in _intent()},
            },
            EXPORTS,
        )
        actual = [
            qualify,
            _set(projected["targets"], "_instant_targets"),
            _set(projected["area_object_targets"], "_instant_objects"),
            _set(projected["shatter_object_targets"], []),
            _set(projected["target"], None),
            _set(projected["_target_id"], None),
            _set(projected["origin_distance"], "_instant_origin_distance"),
        ]
        for original, output in (
            ("half_cover_targets", "_instant_half_cover"),
            ("three_quarters_cover_targets", "_instant_three_quarters"),
        ):
            actual += [
                _set(projected[original], None),
                _when("len(" + output + ") > 0", [_set(projected[original], output)]),
            ]
        owner["effects"] = [
            *[
                effect
                for original, replacement in projected.items()
                for effect in (
                    _set(replacement, None),
                    _when(original + " != null", [_set(replacement, original)]),
                )
            ],
            _when(CONDITION, actual),
            *_effect_names(owner["effects"], projected),
        ]
        for rows in lists(owner["effects"]):
            for effect in rows:
                if effect.get("call") == "cast_spell":
                    effect["params"].update({p["name"]: "{" + p["name"] + "}" for p in _intent()})
                assignment = effect.get("set", {})
                if (
                    name == "begin_srd_spell_action"
                    and assignment.get("field") == "_retained_nullable_capture_rows"
                    and any(row["key"] == "'environment_origin'" for row in assignment["value"])
                ):
                    assignment["value"] += [
                        {
                            "key": repr(p["name"]),
                            "missing": p["name"] + " == null",
                            "value": p["name"],
                        }
                        for p in _intent()
                    ]
    completion_calls = 0
    for rows in lists(tools["complete_srd_pending_action"]["effects"]):
        for effect in rows:
            if effect.get("call") == "cast_spell":
                effect["params"].update(
                    {p["name"]: "{_pending_action.parameters." + p["name"] + "}" for p in _intent()}
                )
                completion_calls += 1
    if completion_calls != 1:
        raise ValueError("The retained pending-cast completion boundary changed.")
    if (
        profiles != runtime["definitions"]["spells"]
        or headers != runtime["definitions"]["spell_casting"]
    ):
        raise ValueError(
            "Combat area binding must preserve every approved profile and casting header."
        )
