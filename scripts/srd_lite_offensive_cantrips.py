"""Join original SRD cantrips to saved-target and allocation validation.

Definitions, ranges, dice, schools and total-level thresholds remain exactly the
original SRD data. This adapter supplies no legacy parity-only mechanics.
"""

from copy import deepcopy

from .srd_control_behaviors import _each, _entity, _param, _tool
from .srd_lite_attacks import _copy_optional_fact, _effect_names
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _call, _require, _set, _when

OFFENSIVE = {
    "fire_bolt",
    "ray_of_frost",
    "shocking_grasp",
    "eldritch_blast",
    "sacred_flame",
    "vicious_mockery",
    "poison_spray",
}
SINGLE = OFFENSIVE - {"eldritch_blast"}

# Retained historical references; the current behavior owner is original SRD.
OFFENSIVE_SOURCE_REVISIONS = {
    "fire_bolt": 373237,
    "ray_of_frost": 422178,
    "shocking_grasp": 328483,
    "eldritch_blast": 431000,
    "sacred_flame": 408634,
    "vicious_mockery": 424652,
    "poison_spray": 404054,
}
OFFENSIVE_ADAPTATIONS = {
    "common": "Reuse original SRD definitions and total-character thresholds 5, 11 and 17 "
    "through level twenty. These Action cantrips spend zero spell slots "
    "and require no concentration. Actual saved grants and ability own casting. "
    "Saved three-dimensional target geometry and clear path precede commitment. "
    "Existing resource, attack/save, typed damage, temporary HP, concentration-check, "
    "pending-action and card owners remain authoritative. Recipient kinds remain "
    "spell-specific; Fire Bolt also has its separate native object receiver.",
    "fire_bolt": "Original 120-foot ranged creature or object attack; one through four "
    "d10 Fire. The object receiver reuses native material AC, durability, mitigation "
    "and hit-only ignition of an unheld flammable object. Combat uses the saved "
    "scene; ordinary exploration objects use current established receiver facts "
    "and durable native metadata. Physical source-ending exploration objects "
    "requiring scene geometry reject before costs.",
    "ray_of_frost": "Original sixty-foot ranged creature attack; one through four d8 Cold. "
    "The source-identified ten-foot reduction expires at caster next Start. "
    "The existing maximum reduction convention remains nonadditive. Dead "
    "expiry owners retain the original finite clock fallback.",
    "shocking_grasp": "Original Touch melee creature attack; one through four d8 Lightning. "
    "Reuse actual saved melee reach and the original Opportunity Attack block "
    "until target next Start. Ordinary Reaction spending remains available. "
    "No metal-armor advantage or paid-Reaction block is added.",
    "eldritch_blast": "Original 120-foot creature allocations; one through four independent "
    "d10 Force beams at saved total levels 1/5/11/17. Allocate every beam "
    "before commitment; repeated targets are allowed. Invocation grants, "
    "actual attack/critical dice, defenses and cards retain their owners.",
    "sacred_flame": "Original sixty-foot visible-creature Dexterity save; failure deals one "
    "through four d8 Radiant, success zero; original partial-cover exception.",
    "vicious_mockery": "Original sixty-foot Wisdom save; failure deals one through four d6 "
    "Psychic and applies the existing next-attack Disadvantage until target "
    "next End; success zero damage and no rider. Retain the original "
    "sight-or-hearing convention and actual attack consumption.",
    "poison_spray": "Original Necromancy, thirty-foot ranged creature attack against AC; "
    "one through four d12 Poison. No Constitution save or Poisoned rider.",
}


def _selected(identities):
    return " or ".join("spell == '" + identity + "'" for identity in sorted(identities))


def bind_offensive_cantrips(runtime):
    """Keep original definitions; validate saved targets and exact beam allocations."""
    tools = runtime["tools"]
    # The public cast and saved three-dimensional scene both carry fractional
    # feet. Clone these three internal declarations rather than mutating their
    # shared original parameter template or rounding authoritative geometry.
    for owner in (
        "finish_srd_retained_cast_tail",
        "cast_spell_attack_damage",
        "cast_spell_single_target_save_damage",
    ):
        parameters = tools[owner]["parameters"]
        matches = [i for i, row in enumerate(parameters) if row.get("name") == "target_distance"]
        if len(matches) != 1:
            raise ValueError("The original scalar range declaration changed: " + owner)
        original = parameters[matches[0]]
        if original.get("type") == "integer":
            parameters[matches[0]] = {**deepcopy(original), "type": "number"}
        elif original.get("type") != "number":
            raise ValueError(
                "The scalar range is neither original integer nor compatible number: " + owner
            )
        # Retain an already compatible Main number declaration without rewriting it.
    tools["resolve_srd_lite_cantrip_range"] = _tool(
        "Read the original spell range and actual saved reach before target qualification.",
        [_entity("caster_state"), _param("spell")],
        [
            _set("_lite_cantrip_range", "definitions.spells[spell].range_feet"),
            _when(
                "spell == 'shocking_grasp'",
                [
                    _set("_lite_cantrip_range", "caster_state.melee_reach"),
                ],
            ),
        ],
    )
    for name in ("cast_spell", "begin_srd_spell_action"):
        tool = tools[name]
        single = [
            _require(
                "object_target_id == ''",
                "This existing creature attack family does not resolve object or environmental effects.",
            ),
            _require(
                "targets == null or len(targets) == 0",
                "Choose one creature target for this cantrip.",
            ),
            _require(
                "attack_targets == null or len(attack_targets) == 0",
                "This single-target cantrip does not accept a beam allocation.",
            ),
            _call(
                "resolve_srd_lite_cantrip_range",
                {
                    "caster_state": "{_caster_state_id}",
                    "spell": "{spell}",
                },
                ("_lite_cantrip_range",),
            ),
            _call(
                "validate_srd_lite_attack_target",
                {
                    "caster_state": "{_caster_state_id}",
                    "target_state": "{_target_id}",
                    "lasting_state": "{_lasting_state_id}",
                    "range_feet": "{_lite_cantrip_range}",
                },
            ),
            _call(
                "resolve_srd_lite_attack_facts",
                {
                    "caster_state": "{_caster_state_id}",
                    "target_state": "{_target_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_lite_attack_distance", "_lite_attack_caster_sees"),
            ),
            _set("target_distance", "_lite_attack_distance"),
            _set("caster_can_see_target", "_lite_attack_caster_sees"),
            _when(
                "spell == 'sacred_flame'",
                [
                    _require(
                        "_lite_attack_caster_sees == true",
                        "Sacred Flame requires actual sight of its target.",
                    ),
                ],
            ),
        ]
        beam = [
            _require(
                "object_target_id == '' and target == null and (targets == null or len(targets) == 0)",
                "Eldritch Blast uses creature beam allocations only.",
            ),
            _require(
                "attack_targets != null and len(attack_targets) > 0",
                "Allocate every Eldritch Blast beam before casting.",
            ),
            _each(
                "attack_targets",
                "_lite_cantrip_beam_target",
                [
                    _call(
                        "validate_srd_lite_attack_target",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_lite_cantrip_beam_target}",
                            "lasting_state": "{_lasting_state_id}",
                            "range_feet": 120,
                        },
                    ),
                ],
            ),
        ]
        tool["effects"][:0] = [
            _when(_selected(SINGLE), single),
            _when("spell == 'eldritch_blast'", beam),
        ]
        bindings = 0
        for effects in lists(tool["effects"]):
            for effect in list(effects):
                if effect.get("set", {}).get("field") == "_casting_profile":
                    allocation = [
                        _set(
                            "_lite_cantrip_beams", "definitions.spells.eldritch_blast.base_attacks"
                        ),
                        _when(
                            "_casting_profile.scaling_level >= definitions.spells.eldritch_blast.upgrade_level_1",
                            [
                                _set(
                                    "_lite_cantrip_beams",
                                    "_lite_cantrip_beams + definitions.spells.eldritch_blast.cantrip_upgrade_attacks",
                                )
                            ],
                        ),
                        _when(
                            "_casting_profile.scaling_level >= definitions.spells.eldritch_blast.upgrade_level_2",
                            [
                                _set(
                                    "_lite_cantrip_beams",
                                    "_lite_cantrip_beams + definitions.spells.eldritch_blast.cantrip_upgrade_attacks",
                                )
                            ],
                        ),
                        _when(
                            "_casting_profile.scaling_level >= definitions.spells.eldritch_blast.upgrade_level_3",
                            [
                                _set(
                                    "_lite_cantrip_beams",
                                    "_lite_cantrip_beams + definitions.spells.eldritch_blast.cantrip_upgrade_attacks",
                                )
                            ],
                        ),
                        _require(
                            "len(attack_targets) == _lite_cantrip_beams",
                            "Allocate exactly the saved caster level's number of beams.",
                        ),
                    ]
                    effects.insert(
                        effects.index(effect) + 1, _when("spell == 'eldritch_blast'", allocation)
                    )
                    bindings += 1
        if not bindings:
            raise ValueError("The actual saved casting-profile binding changed")
    project = [
        _call(
            "resolve_srd_lite_attack_facts",
            {
                "caster_state": "{_caster_state_id}",
                "target_state": "{_target_id}",
                "lasting_state": "state",
            },
            (
                "_lite_attack_distance",
                "_lite_attack_caster_sees",
                "_lite_attack_target_sees",
                "_lite_attack_threatened",
            ),
        ),
        _set("target_distance", "_lite_attack_distance"),
        _set("caster_can_see_target", "_lite_attack_caster_sees"),
        _set("target_can_see_caster", "_lite_attack_target_sees"),
        _set("enemy_within_5_feet", "_lite_attack_threatened"),
        _set("close_enemy_can_see_caster", True),
        _set("close_enemy_incapacitated", False),
    ]
    tools["cast_spell_attack_damage"]["effects"].insert(
        0,
        _when(
            _selected({"fire_bolt", "ray_of_frost", "shocking_grasp", "poison_spray"}),
            deepcopy(project),
        ),
    )
    tools["resolve_spell_multi_attack_target"]["effects"].insert(
        0,
        _when(
            "spell == 'eldritch_blast' and target.is_alive == true",
            deepcopy(project) + [_set("target_within_5_feet", "_lite_attack_distance <= 5")],
        ),
    )
    bind_cantrip_fact_variables(runtime)


def bind_cantrip_fact_variables(runtime):
    """Pass saved scene facts through private variables instead of state writes.

    A DSL set destination without an underscore writes state; it cannot override
    a parameter. Defaults preserve other spells, and the existing projections
    replace those defaults only for the selected cantrips.
    """
    scopes = {
        "cast_spell": ("target_distance", "caster_can_see_target"),
        "begin_srd_spell_action": ("target_distance", "caster_can_see_target"),
        "cast_spell_attack_damage": (
            "target_distance",
            "caster_can_see_target",
            "target_can_see_caster",
            "enemy_within_5_feet",
            "close_enemy_can_see_caster",
            "close_enemy_incapacitated",
        ),
        "resolve_spell_multi_attack_target": (
            "target_distance",
            "caster_can_see_target",
            "target_can_see_caster",
            "enemy_within_5_feet",
            "close_enemy_can_see_caster",
            "close_enemy_incapacitated",
            "target_within_5_feet",
        ),
    }
    for name, fields in scopes.items():
        owner = runtime["tools"][name]
        writes = [
            effect
            for rows in lists(owner["effects"])
            for effect in rows
            if effect.get("set", {}).get("field") in fields
        ]
        if len(writes) != len(fields):
            raise ValueError("The sole saved cantrip projection changed: " + name)
        replacements = {field: "_cantrip_fact_" + field for field in fields}
        declared = {parameter["name"] for parameter in owner["parameters"]}
        owner["effects"] = [
            *[
                _copy_optional_fact(private, public) if public in declared else _set(private, None)
                for public, private in replacements.items()
            ],
            *_effect_names(owner["effects"], replacements),
        ]
    return list(scopes)
