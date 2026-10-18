"""Qualify ray and dart allocations against saved combat positions before commitment."""

from .srd_control_behaviors import _each, _push
from .srd_lite_attacks import _copy_optional_fact, _effect_names
from .srd_lite_combat_target_facts import PROJECT
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _call, _require, _set, _when

SPELLS = ("magic_missile", "scorching_ray", "eldritch_blast")
FIELDS = (
    "target_distance",
    "caster_can_see_target",
    "targets_max_distance",
    "caster_can_see_all_targets",
    "all_targets_have_clear_path",
    "all_attack_targets_have_clear_path",
    "half_cover_targets",
    "three_quarters_cover_targets",
    "caster_unseen_attack_targets",
    "attack_targets_unable_to_see_caster",
    "attack_targets_within_5_feet",
    "enemy_within_5_feet",
    "close_enemy_can_see_caster",
    "close_enemy_incapacitated",
)


def _fact(field):
    return "_allocation_fact_" + field


def _project():
    call = _call(
        PROJECT,
        {
            "caster_state": "{_caster_state_id}",
            "target_state": "{_allocation_recipient}",
            "lasting_state": "{_lasting_state_id}",
            "range_feet": "{_allocation_profile.range_feet}",
        },
    )
    call["export_context"] = {
        "_allocation_distance": "_coord_fact_target_distance",
        "_allocation_caster_sees": "_coord_fact_caster_can_see_target",
        "_allocation_cover": "_coord_fact_cover",
    }
    return call


def _unique_flag(field, condition):
    private = _fact(field)
    return _when(
        condition + " and _allocation_recipient not in " + private,
        [_push(private, "_allocation_recipient")],
    )


def _preflight():
    """Derive only facts; the existing owners retain target allocation and paid consequences."""
    selected = " or ".join("_allocation_spell == '" + spell + "'" for spell in SPELLS)
    return [
        _set("_allocation_spell", "spell"),
        _when(
            "spell in definitions.srd_lite_aliases",
            [_set("_allocation_spell", "definitions.srd_lite_aliases[spell]")],
        ),
        _when(
            "combat.encounter_active == true and (" + selected + ")",
            [
                _set("_allocation_profile", "definitions.spells[_allocation_spell]"),
                _set("_allocation_recipients", []),
                _when(
                    "_allocation_profile.family == 'automatic_force_projectiles'",
                    [
                        _require(
                            "_target_id != null",
                            "Magic Missile needs its existing primary creature target.",
                        ),
                        _when(
                            "projectile_targets != null",
                            [
                                _each(
                                    "projectile_targets",
                                    "_allocation_input",
                                    [_push("_allocation_recipients", "_allocation_input")],
                                )
                            ],
                        ),
                        _when(
                            "len(_allocation_recipients) == 0",
                            [_push("_allocation_recipients", "_target_id")],
                        ),
                    ],
                ),
                _when(
                    "_allocation_profile.family == 'multi_spell_attack_damage'",
                    [
                        _require(
                            "attack_targets != null and len(attack_targets) > 0",
                            "Allocate the spell's independent creature attacks before casting.",
                        ),
                        _each(
                            "attack_targets",
                            "_allocation_input",
                            [_push("_allocation_recipients", "_allocation_input")],
                        ),
                    ],
                ),
                _set(_fact("targets_max_distance"), 0),
                _set(_fact("caster_can_see_all_targets"), True),
                _set(_fact("all_targets_have_clear_path"), True),
                _set(_fact("all_attack_targets_have_clear_path"), True),
                *[_set(_fact(field), []) for field in FIELDS[6:11]],
                _each(
                    "_allocation_recipients",
                    "_allocation_recipient",
                    [
                        _project(),
                        _require(
                            "_allocation_profile.requires_sight != true or "
                            "_allocation_caster_sees == true",
                            "This allocated combat recipient cannot be seen as the spell requires.",
                        ),
                        _set(
                            _fact("targets_max_distance"),
                            "max(" + _fact("targets_max_distance") + ", _allocation_distance)",
                        ),
                        _set(
                            _fact("caster_can_see_all_targets"),
                            _fact("caster_can_see_all_targets") + " and _allocation_caster_sees",
                        ),
                        _unique_flag("half_cover_targets", "_allocation_cover == 'half'"),
                        _unique_flag(
                            "three_quarters_cover_targets", "_allocation_cover == 'three_quarters'"
                        ),
                        _when(
                            "_allocation_profile.family == 'multi_spell_attack_damage'",
                            [
                                _call(
                                    "resolve_srd_lite_attack_facts",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "target_state": "{_allocation_recipient}",
                                        "lasting_state": "{_lasting_state_id}",
                                    },
                                    ("_lite_attack_target_sees", "_lite_attack_threatened"),
                                ),
                                _unique_flag(
                                    "caster_unseen_attack_targets",
                                    "_allocation_caster_sees != true",
                                ),
                                _unique_flag(
                                    "attack_targets_unable_to_see_caster",
                                    "_lite_attack_target_sees != true",
                                ),
                                _unique_flag(
                                    "attack_targets_within_5_feet", "_allocation_distance <= 5"
                                ),
                                _set(_fact("enemy_within_5_feet"), "_lite_attack_threatened"),
                                _set(_fact("close_enemy_can_see_caster"), True),
                                _set(_fact("close_enemy_incapacitated"), False),
                            ],
                        ),
                    ],
                ),
                _when(
                    "_allocation_profile.family == 'automatic_force_projectiles'",
                    [
                        _set("_allocation_recipient", "_target_id"),
                        _project(),
                        _require(
                            "_allocation_caster_sees == true",
                            "Magic Missile needs actual sight of its primary combat target.",
                        ),
                        _set(_fact("target_distance"), "_allocation_distance"),
                        _set(_fact("caster_can_see_target"), "_allocation_caster_sees"),
                    ],
                ),
            ],
        ),
    ]


def bind_allocated_target_facts(runtime):
    """Feed projected combat facts through the existing public cast and staging handoffs."""
    tools = runtime["tools"]
    if PROJECT not in tools or any(
        spell not in runtime["definitions"]["spells"] for spell in SPELLS
    ):
        raise ValueError(
            "Allocated targets require the selected profiles and existing recipient projection"
        )
    mapping = {field: _fact(field) for field in FIELDS}
    for name in ("cast_spell", "begin_srd_spell_action"):
        owner = tools[name]
        if not set(FIELDS) <= {parameter["name"] for parameter in owner["parameters"]}:
            raise ValueError("The public allocation fact contract changed: " + name)
        owner["effects"] = [
            *[_copy_optional_fact(private, public) for public, private in mapping.items()],
            *_preflight(),
            *_effect_names(owner["effects"], mapping),
        ]
    leaf = tools["resolve_spell_multi_attack_target"]
    matches = [
        row
        for rows in lists(leaf["effects"])
        for row in rows
        if any(
            child.get("call") == "resolve_srd_lite_attack_facts" for child in row.get("on_true", [])
        )
    ]
    if len(matches) != 1 or "spell == 'eldritch_blast'" not in matches[0].get("condition", ""):
        raise ValueError("The existing independent attack combat projection changed")
    matches[0]["condition"] = matches[0]["condition"].replace(
        "spell == 'eldritch_blast'", "(spell == 'eldritch_blast' or spell == 'scorching_ray')"
    )
    return list(SPELLS)
