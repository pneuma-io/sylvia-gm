"""Project saved combat geometry into the retained scalar spell consumers."""

from .srd_control_behaviors import _entity, _lasting, _param
from .srd_lite_attacks import _copy_optional_fact, _effect_names
from .srd_timed_actor_defenses import _call, _require, _set, _tool, _when

PROJECT = "project_srd_lite_combat_recipient"
FIELDS = (
    "target_distance",
    "caster_can_see_target",
    "caster_can_hear_target",
    "caster_can_touch_target",
    "cover",
)
FAMILIES = {
    "spell_attack_damage",
    "single_target_save_damage",
    "spell_healing",
    "instantaneous_restoration",
    "timed_actor_defense",
    "timed_senses",
    "single_target_timed_death_prevention",
    "single_target_concentration_damage_resistance",
    "single_target_concentration_armor_class_bonus",
    "instantaneous_threshold_death_damage",
    "threshold_timed_condition",
    "lasting_reactive_concentration",
    "dragon_breath",
    "transformation_spell",
}
ALREADY_QUALIFIED = {
    "fire_bolt",
    "ray_of_frost",
    "shocking_grasp",
    "poison_spray",
    "sacred_flame",
    "vicious_mockery",
}
ORDINARY_RECOVERY_SPELLS = {"greater_restoration", "revivify"}


def bind_combat_target_facts(runtime):
    """Keep exploration inputs; derive scalar combat facts before staging or payment."""
    definitions = runtime["definitions"]["spells"]
    selected = sorted(
        name
        for name, row in definitions.items()
        if (row["family"] in FAMILIES or name in ORDINARY_RECOVERY_SPELLS)
        and row.get("range_kind") != "self"
        and name not in ALREADY_QUALIFIED
    )
    tools = runtime["tools"]
    if PROJECT in tools:
        raise ValueError("Combat recipient projection was already installed")
    # Distances from unsnapped positions stay fractional through internal calls.
    # Clone declarations so shared parameter templates retain their other uses.
    for owner in tools.values():
        owner["parameters"] = [
            {**parameter, "type": "number"}
            if parameter.get("name") == "target_distance" and parameter.get("type") == "integer"
            else parameter
            for parameter in owner["parameters"]
        ]
    tools[PROJECT] = _tool(
        "Read actual combat range, sight, hearing, touch and cover from the saved scene.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("range_feet", "number"),
        ],
        [
            _call(
                "resolve_srd_scene_relation_node",
                {
                    "actor_state": "{_caster_state_id}",
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                (
                    "_scene_distance",
                    "_scene_same_plane",
                    "_scene_line_of_sight",
                    "_scene_can_see",
                    "_scene_can_hear",
                    "_scene_exposure",
                ),
            ),
            _require(
                "_scene_same_plane == true and _scene_line_of_sight == true "
                "and _scene_distance <= range_feet",
                "The actual combat recipient is beyond range or lacks a clear path on this plane.",
            ),
            _set("_coord_fact_target_distance", "_scene_distance"),
            _set("_coord_fact_caster_can_see_target", "_scene_can_see"),
            _set("_coord_fact_caster_can_hear_target", "_scene_can_hear"),
            _set("_coord_fact_caster_can_touch_target", "_scene_distance <= 5"),
            _set("_coord_fact_cover", "'none'"),
            _when("_scene_exposure.exposure <= 0.5", [_set("_coord_fact_cover", "'half'")]),
            _when(
                "_scene_exposure.exposure <= 0.25", [_set("_coord_fact_cover", "'three_quarters'")]
            ),
        ],
    )
    runtime["definitions"]["srd_lite_scalar_geometry_spells"] = selected
    mapping = {field: "_coord_fact_" + field for field in FIELDS}
    for name in ("cast_spell", "begin_srd_spell_action"):
        owner = tools[name]
        declared = {parameter["name"] for parameter in owner["parameters"]}
        if not set(FIELDS) <= declared:
            raise ValueError("The scalar spell fact contract changed: " + name)
        prefix = [
            *[_copy_optional_fact(private, public) for public, private in mapping.items()],
            _set("_coord_spell", "spell"),
            _when(
                "spell in definitions.srd_lite_aliases",
                [
                    _set("_coord_spell", "definitions.srd_lite_aliases[spell]"),
                ],
            ),
            _when(
                "combat.encounter_active == true and _target_id != null and "
                "(targets == null or len(targets) == 0) and "
                "_coord_spell in definitions.srd_lite_scalar_geometry_spells",
                [
                    _set("_coord_range", "definitions.spells[_coord_spell].range_feet"),
                    _when(
                        "definitions.spells[_coord_spell].range_kind == 'touch'",
                        [_set("_coord_range", 5)],
                    ),
                    _call(
                        PROJECT,
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_target_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "range_feet": "{_coord_range}",
                        },
                        tuple(mapping.values()),
                    ),
                    _require(
                        "(definitions.spells[_coord_spell].requires_sight != true and "
                        "definitions.spells[_coord_spell].targeting_sense != 'sight') or "
                        "_coord_fact_caster_can_see_target == true",
                        "The actual combat recipient cannot be seen as this spell requires.",
                    ),
                    _require(
                        "definitions.spells[_coord_spell].targeting_sense != 'sight_or_hearing' or "
                        "_coord_fact_caster_can_see_target == true or "
                        "_coord_fact_caster_can_hear_target == true",
                        "The actual combat recipient can neither be seen nor heard "
                        "as this spell requires.",
                    ),
                ],
            ),
        ]
        owner["effects"] = [*prefix, *_effect_names(owner["effects"], mapping)]
    return selected
