"""Keep cantrip scene reads in combat and project actual combat cover and hearing."""

from copy import deepcopy

from .srd_lite_attacks import _copy_optional_fact, _effect_names
from .srd_lite_combat_target_facts import PROJECT
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _call, _require, _when

ROOTS = ("cast_spell", "begin_srd_spell_action")
LEAVES = ("cast_spell_attack_damage", "resolve_spell_multi_attack_target")
COMBAT = "combat.encounter_active == true"


def _recipient_projection(exports, lasting_state="state"):
    result = _call(
        PROJECT,
        {
            "caster_state": "{_caster_state_id}",
            "target_state": "{_target_id}",
            "lasting_state": lasting_state,
            "range_feet": "{_lite_cantrip_range}",
        },
    )
    result["export_context"] = exports
    return result


def _bind_attack_target_sight(owner):
    """Keep the live invisibility observer in combat and retain exploration's sight fact."""
    matches = 0
    for rows in list(lists(owner["effects"])):
        for index in range(len(rows) - 1, -1, -1):
            effect = rows[index]
            if effect.get("call") != "resolve_srd_illumination_target_sight" or effect.get(
                "export_context"
            ) != {"_invisibility_attack_target_can_see": "_illumination_target_can_see"}:
                continue
            rows[index : index + 1] = [
                _copy_optional_fact(
                    "_invisibility_attack_target_can_see", "_cantrip_fact_target_can_see_caster"
                ),
                _when(COMBAT, [effect]),
            ]
            matches += 1
    if matches != 1:
        raise ValueError("The live scalar invisibility observer changed")


def bind_cantrip_combat_scope(runtime):
    """Retain casting/allocation rules; use scene facts only in active combat."""
    tools = runtime["tools"]
    if PROJECT not in tools or "srd_lite_multi_geometry_spells" not in runtime["definitions"]:
        raise ValueError("Cantrip combat scope requires the existing recipient projections")
    for name in ROOTS:
        branches = [row for rows in lists(tools[name]["effects"]) for row in rows]
        single = [
            row
            for row in branches
            if any(
                child.get("call") == "resolve_srd_lite_cantrip_range"
                for child in row.get("on_true", [])
            )
        ]
        beam = [
            row
            for row in branches
            if any(
                child.get("for_each", {}).get("as") == "_lite_cantrip_beam_target"
                for child in row.get("on_true", [])
            )
        ]
        if len(single) != 1 or len(beam) != 1:
            raise ValueError("The cantrip recipient preflight changed: " + name)
        effects = single[0]["on_true"]
        if len(effects) != 9 or effects[3].get("call") != "resolve_srd_lite_cantrip_range":
            raise ValueError("The scalar cantrip scene boundary changed: " + name)
        projection = _recipient_projection(
            {
                "_coord_fact_cover": "_coord_fact_cover",
                "_coord_fact_caster_can_hear_target": "_coord_fact_caster_can_hear_target",
            },
            "{_lasting_state_id}",
        )
        single[0]["on_true"] = [
            *deepcopy(effects[:3]),
            _when(
                COMBAT,
                [
                    *deepcopy(effects[3:8]),
                    projection,
                    _when(
                        "spell == 'vicious_mockery'",
                        [
                            _require(
                                "_cantrip_fact_caster_can_see_target == true or "
                                "_coord_fact_caster_can_hear_target == true",
                                "Vicious Mockery requires actual sight or hearing "
                                "of its combat recipient.",
                            )
                        ],
                    ),
                    deepcopy(effects[8]),
                ],
            ),
        ]
        allocation = beam[0]["on_true"]
        if len(allocation) != 3 or "for_each" not in allocation[2]:
            raise ValueError("The cantrip beam preflight changed: " + name)
        beam[0]["on_true"] = [*deepcopy(allocation[:2]), _when(COMBAT, deepcopy(allocation[2:]))]
    for name in LEAVES:
        owner = tools[name]
        branches = [
            row
            for rows in lists(owner["effects"])
            for row in rows
            if any(
                child.get("call") == "resolve_srd_lite_attack_facts"
                for child in row.get("on_true", [])
            )
        ]
        if len(branches) != 1:
            raise ValueError("The actual cantrip attack projection changed: " + name)
        branch = branches[0]
        branch["condition"] = COMBAT + " and (" + branch["condition"] + ")"
        branch["on_true"] = [
            _call(
                "resolve_srd_lite_cantrip_range",
                {
                    "caster_state": "{_caster_state_id}",
                    "spell": "{spell}",
                },
                ("_lite_cantrip_range",),
            ),
            _recipient_projection({"_cantrip_combat_cover": "_coord_fact_cover"}),
            *branch["on_true"],
        ]
        owner["effects"] = [
            _copy_optional_fact("_cantrip_combat_cover", "cover"),
            *_effect_names(owner["effects"], {"cover": "_cantrip_combat_cover"}),
        ]
    _bind_attack_target_sight(tools["cast_spell_attack_damage"])
    return [*ROOTS, *LEAVES]
