"""Qualify Fear and Sleep combat areas before the unchanged control spell owner."""

from copy import deepcopy

from .srd_control_behaviors import _entity, _param, _when
from .srd_lite_attacks import _copy_optional_fact, _effect_names
from .srd_scene_relations import _tool_scene
from .srd_timed_actor_defenses import _call, _require, _set

QUALIFY = "qualify_srd_lite_control_combat_area"
PAID = "cast_srd_control_behavior"
ROOTS = ("cast_spell", "begin_srd_spell_action")
SPELLS = ("fear", "sleep")
FIELDS = ("targets", "targets_max_distance", "all_targets_have_clear_path", "target", "_target_id")
PREFIX_LENGTH = len(FIELDS) + 3
EXPORTS = ("_control_area_targets", "_control_area_origin_distance")
INTENT = (
    "environment_origin",
    "instant_area_apex",
    "instant_area_direction",
    "instant_area_include_origin",
)


def _intent():
    """Reuse the public and pending geometry fields already transported by instant areas."""
    return [
        _param(name, "array", required=False, default=[], items={"type": "number"}, max_items=3)
        for name in INTENT[:3]
    ] + [
        _param("instant_area_include_origin", "boolean", required=False, default=False),
    ]


def _qualifier():
    """Compose admitted Cone/Sphere membership and the existing actual point-cover owner."""
    return _tool_scene(
        "Derive Fear or Sleep recipients from the actual saved combat area before casting.",
        [_entity("caster_state"), _param("spell"), *_intent()],
        [
            _require("spell == 'fear' or spell == 'sleep'", "Use the selected control-area owner."),
            _set("_control_area_profile", "definitions.spells[spell]"),
            _require(
                "_control_area_profile.family == 'timed_control_behavior' and "
                "_control_area_profile.requires_sight == false and "
                "_control_area_profile.save_ability == 'wisdom' and "
                "((spell == 'fear' and _control_area_profile.behavior == 'fear' and "
                "_control_area_profile.range_kind == 'self' and "
                "_control_area_profile.area_feet == 30) or "
                "(spell == 'sleep' and _control_area_profile.behavior == 'sleep' and "
                "_control_area_profile.range_feet == 60 and "
                "_control_area_profile.area_feet == 5))",
                "Use the approved Fear Cone or Sleep Sphere profile.",
            ),
            _when(
                "spell == 'fear'",
                [
                    _call(
                        "collect_srd_cone_creature_targets",
                        {
                            "source_state": "{_caster_state_id}",
                            "length_feet": "{_control_area_profile.area_feet}",
                            "apex": "{instant_area_apex}",
                            "direction": "{instant_area_direction}",
                            "include_origin": "{instant_area_include_origin}",
                        },
                        ("_srd_cone_targets", "_srd_cone_origin"),
                    ),
                    _set("_control_area_candidates", "_srd_cone_targets"),
                    _set("_control_area_origin", "_srd_cone_origin"),
                    _set("_control_area_origin_distance", 0),
                ],
                [
                    _require(
                        "len(environment_origin) == 3",
                        "Choose Sleep's actual three-dimensional Sphere center in combat.",
                    ),
                    _call(
                        "collect_srd_sunburst_creature_targets",
                        {
                            "caster_state": "{_caster_state_id}",
                            "sunburst_origin": "{environment_origin}",
                            "range_feet": "{_control_area_profile.range_feet}",
                            "radius_feet": "{_control_area_profile.area_feet}",
                        },
                        ("_sunburst_sphere_targets", "_sunburst_origin", "_sunburst_range_start"),
                    ),
                    _set("_control_area_candidates", "_sunburst_sphere_targets"),
                    _set("_control_area_origin", "_sunburst_origin"),
                    _set(
                        "_control_area_origin_distance",
                        "ceil(((_sunburst_origin[0] - _sunburst_range_start[0]) ** 2 + "
                        "(_sunburst_origin[1] - _sunburst_range_start[1]) ** 2 + "
                        "(_sunburst_origin[2] - _sunburst_range_start[2]) ** 2) ** 0.5)",
                    ),
                ],
            ),
            _call(
                "collect_srd_sunburst_cover_targets",
                {
                    "source_state": "{_caster_state_id}",
                    "targets": "{_control_area_candidates}",
                    "origin": "{_control_area_origin}",
                },
                ("_srd_area_targets",),
            ),
            _set("_control_area_targets", "_srd_area_targets"),
            _require(
                "len(_control_area_targets) <= _control_area_profile.base_targets",
                "The actual control area exceeds this retained spell's recipient bound.",
            ),
        ],
    )


def _actual(mapping):
    """Replace supplied target facts before staging, while keeping the paid leaf unchanged.

    Its generic distance gate carries the actual source-to-origin distance. The
    qualified shape has already constrained every occupied recipient body; this
    allows Sphere/Cone boundary contact without substituting center-distance lists.
    """
    copied_intent = []
    for name in INTENT[:3]:
        private = "_control_intent_" + name
        copied_intent += [
            _set(private, []),
            _when(name + " != null", [_set(private, name)]),
        ]
    return [
        *copied_intent,
        _set("_control_intent_include_origin", False),
        _when(
            "instant_area_include_origin != null",
            [_set("_control_intent_include_origin", "instant_area_include_origin")],
        ),
        _call(
            QUALIFY,
            {
                "caster_state": "{_caster_state_id}",
                "spell": "{_control_geometry_spell}",
                **{name: "{_control_intent_" + name + "}" for name in INTENT[:3]},
                "instant_area_include_origin": "{_control_intent_include_origin}",
            },
            EXPORTS,
        ),
        _require(
            "len(_control_area_targets) > 0",
            "The retained control owner requires at least one actual affected creature.",
        ),
        _set(mapping["targets"], "_control_area_targets"),
        _set(mapping["targets_max_distance"], "_control_area_origin_distance"),
        _set(mapping["all_targets_have_clear_path"], True),
        _set(mapping["target"], None),
        _set(mapping["_target_id"], None),
    ]


def bind_control_combat_areas(runtime):
    """Bind after scalar, multi, instant-area and Flame facts, before pruning.

    Existing origin/direction staging and completion transport stays unchanged.
    All saves, concentration, conditions, costs and result widgets retain their
    original paid owners. Ordinary exploration retains its existing facts.
    """
    tools = runtime["tools"]
    if QUALIFY in tools or PAID not in tools:
        raise ValueError("Control geometry needs one existing paid control owner.")
    sphere_params = {
        parameter["name"]
        for parameter in tools["collect_srd_sunburst_creature_targets"]["parameters"]
    }
    if not {"range_feet", "radius_feet"} <= sphere_params:
        raise ValueError("Bind the shared sized Sphere owner before control geometry.")
    profiles = deepcopy(runtime["definitions"]["spells"])
    headers = deepcopy(runtime["definitions"]["spell_casting"])
    paid = deepcopy(tools[PAID])
    tools[QUALIFY] = _qualifier()
    for name in ROOTS:
        owner = tools[name]
        names = {parameter["name"] for parameter in owner["parameters"]}
        if not {*FIELDS[:-1], *INTENT, "caster_state", "spell"} <= names:
            raise ValueError("The actual control geometry entry contract changed: " + name)
        mapping = {field: "_control_geometry_" + field for field in FIELDS}
        owner["effects"] = [
            *[_copy_optional_fact(private, public) for public, private in mapping.items()],
            _set("_control_geometry_spell", "spell"),
            _when(
                "spell in definitions.srd_lite_aliases",
                [_set("_control_geometry_spell", "definitions.srd_lite_aliases[spell]")],
            ),
            _when(
                "combat.encounter_active == true and "
                "(_control_geometry_spell == 'fear' or _control_geometry_spell == 'sleep')",
                _actual(mapping),
            ),
            *_effect_names(owner["effects"], mapping),
        ]
        owner["description"] += (
            " In combat, Fear uses the saved body facing or instant_area_direction/apex "
            "for its Cone; Sleep chooses environment_origin for its Sphere. Saved "
            "positions determine actual recipients, range, plane and clear paths."
        )
    if (
        profiles != runtime["definitions"]["spells"]
        or headers != runtime["definitions"]["spell_casting"]
        or paid != tools[PAID]
    ):
        raise ValueError("Control area composition must preserve profiles and paid behavior.")
    return [QUALIFY, *ROOTS]
