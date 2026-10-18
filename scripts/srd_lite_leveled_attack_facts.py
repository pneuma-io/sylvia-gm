"""Use saved combat facts in retained leveled spell attack consumers."""

from copy import deepcopy

from .srd_timed_actor_defenses import _call, _require, _set, _when


def bind_leveled_attack_facts(runtime):
    """Retain original spell bodies, headers and rules; repair the missing fact projection."""
    exports = (
        "_lite_attack_distance",
        "_lite_attack_caster_sees",
        "_lite_attack_target_sees",
        "_lite_attack_threatened",
        "_lite_attack_same_plane",
        "_lite_attack_line_of_sight",
    )
    project = [
        _call(
            "resolve_srd_lite_attack_facts",
            {
                "caster_state": "{_caster_state_id}",
                "target_state": "{_target_id}",
                "lasting_state": "state",
            },
            exports,
        )
    ]
    project += [
        _set("_cantrip_fact_" + field, value)
        for field, value in (
            ("target_distance", "_lite_attack_distance"),
            ("caster_can_see_target", "_lite_attack_caster_sees"),
            ("target_can_see_caster", "_lite_attack_target_sees"),
            ("enemy_within_5_feet", "_lite_attack_threatened"),
            ("close_enemy_can_see_caster", True),
            ("close_enemy_incapacitated", False),
        )
    ]
    for name, selected in (("cast_spell_attack_damage", "true"),):
        effects = runtime["tools"][name]["effects"]
        anchors = [
            i
            for i, row in enumerate(effects)
            if row.get("set", {}).get("field") == "_retained_caster_sees"
        ]
        if len(anchors) != 1:
            raise ValueError("Retained spell attack fact boundary changed: " + name)
        body = deepcopy(project)
        body.insert(1, _require("_lite_attack_same_plane == true and _lite_attack_line_of_sight == true and ((attack_kind == 'melee' and range_kind == 'touch' and _lite_attack_distance <= caster_state.melee_reach) or (attack_kind == 'ranged' and range_kind == 'distance' and _lite_attack_distance <= range_feet))", 'The actual spell-attack target is beyond range or lacks a clear path on this plane.'))
        if name == "resolve_spell_multi_attack_target":
            body.append(_set("_cantrip_fact_target_within_5_feet", "_lite_attack_distance <= 5"))
        effects.insert(
            anchors[0], _when("combat.encounter_active == true and (" + selected + ")", body)
        )
    target = runtime["tools"]["resolve_spell_multi_attack_target"]
    dead = next(
        row for row in target["effects"] if row.get("condition") == "target.is_alive == true"
    )
    if dead.get("on_false"):
        raise ValueError("Ray dead-target handling already has an owner")
    dead["on_false"] = [
        _set(field, value)
        for field, value in (
            ("_spell_target_armor_class", None),
            ("_spell_attack", None),
            ("_spell_attack_outcome", "'skipped'"),
            ("_spell_attack_kind", "'skipped'"),
            ("_spell_damage", None),
            ("_spell_damage_amount", 0),
            ("_spell_concentration_save_dc", None),
            ("_spell_concentration_save_outcome", None),
            ("_spell_concentration_save_roll", None),
            ("_spell_summary_rows", []),
            ("_spell_detail_rows", []),
        )
    ]
    return ["cast_spell_attack_damage", "resolve_spell_multi_attack_target"]
