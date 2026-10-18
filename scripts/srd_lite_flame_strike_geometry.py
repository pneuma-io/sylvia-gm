"""Qualify combat Flame Strike through the retained neutral Cylinder owner."""

from copy import deepcopy

from .srd_lite_attacks import _copy_optional_fact, _effect_names
from .srd_retained_ice_storm_geometry_v3 import (
    AREA,
    _array,
    _call,
    _entity,
    _qualifier,
    _require,
    _set,
    _tool_scene,
    _when,
)
from .srd_retained_ice_storm_geometry_v3 import (
    QUALIFY as CYLINDER,
)

QUALIFY = "qualify_srd_lite_flame_strike_combat_cylinder"
ROOTS = ("cast_spell", "begin_srd_spell_action")
FACTS = ("targets", "half_cover_targets", "three_quarters_cover_targets", "origin_distance")
ROOT_FIELDS = (*FACTS, "target", "_target_id")
PREFIX_LENGTH = len(ROOT_FIELDS) + 3
CONDITION = "combat.encounter_active == true and spell == 'flame_strike'"
EXPORTS = ("_flame_targets", "_flame_half_cover", "_flame_three_quarters", "_flame_distance")


def _flame_qualifier():
    """Use the immutable selected profile; creature projection never retains hail terrain."""
    return _tool_scene(
        "Derive Flame Strike's actual Cylinder recipients, cover and origin range in combat.",
        [_entity("caster_state"), _array("origin")],
        [
            _set("_flame_profile", "definitions.spells.flame_strike"),
            _require(
                "_flame_profile.family == 'instantaneous_area_save_composite_damage' and "
                "_flame_profile.area_shape == 'cylinder' and _flame_profile.range_feet == 60 "
                "and _flame_profile.area_radius_feet == 10 and "
                "_flame_profile.area_height_feet == 40",
                "Use the approved Flame Strike Cylinder profile.",
            ),
            _call(
                CYLINDER,
                {
                    "caster_state": "{_caster_state_id}",
                    "origin": "{origin}",
                    "range_feet": "{_flame_profile.range_feet}",
                    "radius_feet": "{_flame_profile.area_radius_feet}",
                    "height_feet": "{_flame_profile.area_height_feet}",
                    "retain_ground": False,
                },
                dict(
                    zip(
                        EXPORTS,
                        (
                            "_retained_ice_targets",
                            "_retained_ice_half_cover",
                            "_retained_ice_three_quarters_cover",
                            "_retained_ice_distance",
                        ),
                        strict=True,
                    )
                ),
            ),
        ],
    )


def _actual(mapping):
    """Derive facts before the existing cast or pending-action owner spends anything."""
    effects = [
        _require(
            "environment_origin != null and len(environment_origin) == 3",
            "Choose Flame Strike's actual three-dimensional Cylinder origin in combat.",
        ),
        _call(
            QUALIFY,
            {"caster_state": "{_caster_state_id}", "origin": "{environment_origin}"},
            EXPORTS,
        ),
    ]
    for field, output in zip(FACTS[:3], EXPORTS[:3], strict=True):
        effects.append(_set(mapping[field], output))
    effects.append(_set(mapping["origin_distance"], "ceil(_flame_distance)"))
    for field in ("target", "_target_id"):
        if field in mapping:
            effects.append(_set(mapping[field], None))
    return effects


def bind_flame_strike_combat_cylinder(runtime):
    """Bind after the existing fact projections, before private-owner pruning.

    Exploration keeps its existing supplied-fact contract. Combat admission and
    pending completion use the same saved-scene qualifier; existing component
    rolls, saves, payment, slot scaling and feedback remain in their paid owner.
    """
    tools = runtime["tools"]
    if QUALIFY in tools or CYLINDER not in tools or AREA not in tools:
        raise ValueError("Flame Strike needs one retained Cylinder composition boundary.")
    profiles = deepcopy(runtime["definitions"]["spells"])
    headers = deepcopy(runtime["definitions"]["spell_casting"])
    profile = profiles["flame_strike"]
    expected = {
        "family": "instantaneous_area_save_composite_damage",
        "area_shape": "cylinder",
        "range_feet": 60,
        "area_radius_feet": 10,
        "area_height_feet": 40,
        "component_1_base_dice": 5,
        "component_1_damage_die": "d6",
        "component_1_damage_type": "fire",
        "component_2_base_dice": 5,
        "component_2_damage_die": "d6",
        "component_2_damage_type": "radiant",
    }
    if any(profile.get(key) != value for key, value in expected.items()):
        raise ValueError("The immutable Flame Strike profile changed.")
    # The same generated helper is used by fresh package composition and by a
    # source check against the previous admitted candidate's resolved tools.
    tools[CYLINDER] = _qualifier()
    tools[QUALIFY] = _flame_qualifier()
    for name in ROOTS:
        owner = tools[name]
        names = {parameter["name"] for parameter in owner["parameters"]}
        if not {*FACTS, "target", "environment_origin", "caster_state", "spell"} <= names:
            raise ValueError("The actual Flame Strike entry contract changed: " + name)
        mapping = {field: "_flame_dispatch_" + field for field in ROOT_FIELDS}
        owner["effects"] = [
            *[_copy_optional_fact(private, public) for public, private in mapping.items()],
            _set("_flame_spell", "spell"),
            _when(
                "spell in definitions.srd_lite_aliases",
                [_set("_flame_spell", "definitions.srd_lite_aliases[spell]")],
            ),
            _when(
                "combat.encounter_active == true and _flame_spell == 'flame_strike'",
                _actual(mapping),
            ),
            *_effect_names(owner["effects"], mapping),
        ]
        owner["description"] += (
            " In combat, Flame Strike requires environment_origin as its chosen "
            "Cylinder point; saved positions determine recipients, range and cover."
        )
    area = tools[AREA]
    mapping = {field: "_flame_area_" + field for field in FACTS}
    changed_origin = 0
    for requirement in area.get("pre_conditions", []):
        if "origin_kind == 'point' and origin_distance <= range_feet" in requirement["condition"]:
            requirement["condition"] = "(" + CONDITION + ") or (" + requirement["condition"] + ")"
            changed_origin += 1
    if changed_origin != 1:
        raise ValueError("The retained composite-area origin precondition changed.")
    area["effects"] = [
        *[_copy_optional_fact(private, public) for public, private in mapping.items()],
        _when(
            CONDITION,
            [
                _require(
                    "origin_kind == 'point' and area_shape == 'cylinder' and range_feet == 60 "
                    "and area_radius_feet == 10 and area_height_feet == 40 "
                    "and area_point_count == 1",
                    "Use Flame Strike's actual approved Cylinder at its chosen point.",
                ),
                *_actual(mapping),
            ],
        ),
        *_effect_names(area["effects"], mapping),
    ]
    if (
        profiles != runtime["definitions"]["spells"]
        or headers != runtime["definitions"]["spell_casting"]
    ):
        raise ValueError("Cylinder composition must preserve every profile and casting header.")
    return [CYLINDER, QUALIFY, *ROOTS, AREA]
