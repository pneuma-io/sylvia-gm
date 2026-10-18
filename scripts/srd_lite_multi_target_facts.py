"""Project each chosen combat recipient into the existing spell fact contracts."""

from .srd_control_behaviors import _each, _push
from .srd_lite_attacks import _copy_optional_fact, _effect_names
from .srd_lite_combat_target_facts import FIELDS, PROJECT
from .srd_timed_actor_defenses import _call, _require, _set, _when

MULTI_FIELDS = (
    "targets_max_distance",
    "caster_can_see_all_targets",
    "caster_can_touch_all_targets",
    "all_targets_have_clear_path",
    "half_cover_targets",
    "three_quarters_cover_targets",
)
PREFIX_LENGTH = len(FIELDS) + len(MULTI_FIELDS) + 6


FAMILIES = {
    "multi_target_timed_hit_point_bonus",
    "multi_target_concentration_d20_modifier",
    "multi_target_timed_control",
    "timed_control_orders",
    "multi_target_timed_actor_mobility",
    "multi_target_timed_speed_bonus",
    "multi_target_timed_repeating_save_condition",
    "timed_actor_modifier",
}


def bind_multi_target_facts(runtime):
    """Qualify saved combat recipients before staging; retain exploration's supplied facts."""
    tools, definitions = runtime["tools"], runtime["definitions"]
    if PROJECT not in tools or "srd_lite_multi_geometry_spells" in definitions:
        raise ValueError("Multi-recipient projection requires exactly one scalar projection")
    selected = sorted(
        set(definitions["srd_lite_scalar_geometry_spells"])
        | {
            name
            for name, row in definitions["spells"].items()
            if (row["family"] in FAMILIES and row.get("range_kind") != "self")
            or name == "mass_healing_word"
        }
    )
    definitions["srd_lite_multi_geometry_spells"] = selected
    for owner in tools.values():
        owner["parameters"] = [
            {**parameter, "type": "number"}
            if parameter.get("name") == "targets_max_distance"
            and parameter.get("type") == "integer"
            else parameter
            for parameter in owner["parameters"]
        ]
    mapping = {field: "_multi_fact_" + field for field in (*FIELDS, *MULTI_FIELDS)}
    project = _call(
        PROJECT,
        {
            "caster_state": "{_caster_state_id}",
            "target_state": "{_multi_recipient}",
            "lasting_state": "{_lasting_state_id}",
            "range_feet": "{_multi_range}",
        },
    )
    project["export_context"] = {"_multi_item_" + field: "_coord_fact_" + field for field in FIELDS}
    for name in ("cast_spell", "begin_srd_spell_action"):
        owner = tools[name]
        declared = {parameter["name"] for parameter in owner["parameters"]}
        if not set(mapping) <= declared:
            raise ValueError("The multi-recipient fact contract changed: " + name)
        # A scalar handler can still consume target when a separate list is supplied.
        # Qualify that actual scalar too, rather than trusting the extra list's facts.
        scalar = owner["effects"][len(FIELDS) + 2]
        expected = "(targets == null or len(targets) == 0) and "
        if expected not in scalar.get("condition", ""):
            raise ValueError("The scalar preflight composition changed: " + name)
        scalar["condition"] = scalar["condition"].replace(expected, "")
        prefix = [
            *[_copy_optional_fact(private, public) for public, private in mapping.items()],
            _set("_multi_spell", "spell"),
            _when(
                "spell in definitions.srd_lite_aliases",
                [_set("_multi_spell", "definitions.srd_lite_aliases[spell]")],
            ),
            _set("_multi_recipients", []),
            _when(
                "targets != null",
                [_each("targets", "_multi_input", [_push("_multi_recipients", "_multi_input")])],
            ),
            _when(
                "len(_multi_recipients) == 0 and _target_id != null",
                [_push("_multi_recipients", "_target_id")],
            ),
            _when(
                "combat.encounter_active == true and len(_multi_recipients) > 0 and "
                "_multi_spell in definitions.srd_lite_multi_geometry_spells",
                [
                    _set("_multi_profile", "definitions.spells[_multi_spell]"),
                    _set("_multi_range", "_multi_profile.range_feet"),
                    _when("_multi_profile.range_kind == 'touch'", [_set("_multi_range", 5)]),
                    _set("_multi_fact_targets_max_distance", 0),
                    _set("_multi_fact_caster_can_see_all_targets", True),
                    _set("_multi_fact_caster_can_touch_all_targets", True),
                    _set("_multi_fact_all_targets_have_clear_path", True),
                    _set("_multi_fact_half_cover_targets", []),
                    _set("_multi_fact_three_quarters_cover_targets", []),
                    _set("_multi_fact_caster_can_see_target", True),
                    _set("_multi_fact_caster_can_hear_target", True),
                    _set("_multi_fact_caster_can_touch_target", True),
                    _set("_multi_fact_cover", "'none'"),
                    _each(
                        "_multi_recipients",
                        "_multi_recipient",
                        [
                            project,
                            _require(
                                "(_multi_profile.requires_sight != true and "
                                "_multi_profile.targeting_sense != 'sight' "
                                "and _multi_profile.requires_each_target_sight != true and "
                                "_multi_profile.family != 'timed_control_orders') or "
                                "_multi_item_caster_can_see_target == true",
                                "An actual combat recipient cannot be seen as this spell requires.",
                            ),
                            _require(
                                "_multi_profile.targeting_sense != 'sight_or_hearing' or "
                                "_multi_item_caster_can_see_target == true or "
                                "_multi_item_caster_can_hear_target == true",
                                "An actual combat recipient can neither be seen nor heard "
                                "as this spell requires.",
                            ),
                            _set(
                                "_multi_fact_targets_max_distance",
                                "max(_multi_fact_targets_max_distance, "
                                "_multi_item_target_distance)",
                            ),
                            _set(
                                "_multi_fact_caster_can_see_all_targets",
                                "_multi_fact_caster_can_see_all_targets and "
                                "_multi_item_caster_can_see_target",
                            ),
                            _set(
                                "_multi_fact_caster_can_touch_all_targets",
                                "_multi_fact_caster_can_touch_all_targets and "
                                "_multi_item_caster_can_touch_target",
                            ),
                            _set(
                                "_multi_fact_caster_can_see_target",
                                "_multi_fact_caster_can_see_target and "
                                "_multi_item_caster_can_see_target",
                            ),
                            _set(
                                "_multi_fact_caster_can_hear_target",
                                "_multi_fact_caster_can_hear_target and "
                                "_multi_item_caster_can_hear_target",
                            ),
                            _set(
                                "_multi_fact_caster_can_touch_target",
                                "_multi_fact_caster_can_touch_target and "
                                "_multi_item_caster_can_touch_target",
                            ),
                            _when(
                                "_multi_item_cover == 'half'",
                                [_push("_multi_fact_half_cover_targets", "_multi_recipient")],
                            ),
                            _when(
                                "_multi_item_cover == 'three_quarters'",
                                [
                                    _push(
                                        "_multi_fact_three_quarters_cover_targets",
                                        "_multi_recipient",
                                    )
                                ],
                            ),
                            _set("_multi_fact_cover", "_multi_item_cover"),
                        ],
                    ),
                    _set("_multi_fact_target_distance", "_multi_fact_targets_max_distance"),
                ],
            ),
        ]
        owner["effects"] = [*prefix, *_effect_names(owner["effects"], mapping)]
    return selected
