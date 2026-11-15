"""Source-owned Smite, Hex and Hunter's Mark components share the actual attack packet."""

from copy import deepcopy

if __package__:
    from .srd_ensnaring_strike import PROFILE as ENSNARING_PROFILE
    from .srd_ensnaring_strike import PUBLIC_TOOLS as ENSNARING_PUBLIC_TOOLS
    from .srd_ensnaring_strike import bind_condition_projection
    from .srd_ensnaring_strike import tools as ensnaring_tools
    from .srd_scene_relations import _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _replace_tools
else:
    from srd_ensnaring_strike import PROFILE as ENSNARING_PROFILE
    from srd_ensnaring_strike import PUBLIC_TOOLS as ENSNARING_PUBLIC_TOOLS
    from srd_ensnaring_strike import bind_condition_projection
    from srd_ensnaring_strike import tools as ensnaring_tools
    from srd_scene_relations import _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _replace_tools

SMITES = ("divine_smite", "searing_smite", "shining_smite")
AFTER_HIT_SPELLS = SMITES + ("ensnaring_strike",)
MARKS = ("hex", "hunters_mark")
PUBLIC_TOOLS = {"transfer_srd_attack_mark"} | ENSNARING_PUBLIC_TOOLS
PROFILES = {
    "ensnaring_strike": deepcopy(ENSNARING_PROFILE),
    "divine_smite": {
        "base_level": 1,
        "range_kind": "self",
        "range_feet": 0,
        "concentration": False,
        "duration_rounds": 0,
        "rider_die": "d8",
        "rider_type": "radiant",
        "rider_base_dice": 2,
    },
    "searing_smite": {
        "base_level": 1,
        "range_kind": "self",
        "range_feet": 0,
        "concentration": False,
        "duration_rounds": 10,
        "rider_die": "d6",
        "rider_type": "fire",
        "rider_base_dice": 1,
    },
    "shining_smite": {
        "base_level": 2,
        "range_kind": "self",
        "range_feet": 0,
        "concentration": True,
        "duration_rounds": 10,
        "rider_die": "d6",
        "rider_type": "radiant",
        "rider_base_dice": 2,
    },
    "hex": {
        "base_level": 1,
        "range_kind": "feet",
        "range_feet": 90,
        "concentration": True,
        "duration_rounds": 600,
        "rider_die": "d6",
        "rider_type": "necrotic",
        "rider_base_dice": 1,
    },
    "hunters_mark": {
        "base_level": 1,
        "range_kind": "feet",
        "range_feet": 90,
        "concentration": True,
        "duration_rounds": 600,
        "rider_die": "d6",
        "rider_type": "force",
        "rider_base_dice": 1,
    },
}
for _profile in PROFILES.values():
    _profile.update(family="attack_rider_spell", casting_time="bonus_action")

CAST_CHOICES = [
    _optional(
        "rider_ability",
        "enum",
        "strength",
        enum_values=["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"],
    ),
    _optional("rider_pending_action_id", default=""),
]
SOURCE_EXPORTS = ("_rider_source_id", "_rider_source", "_rider_live")
COMPONENT_EXPORTS = ("_rider_components", "_rider_damage_rolls")


def schema():
    properties = {}
    for profile in PROFILES.values():
        for key, value in profile.items():
            if key != "family":
                properties[key] = {
                    "type": "boolean"
                    if type(value) is bool
                    else "integer"
                    if type(value) is int
                    else "string"
                }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    for spell, profile in definitions.items():
        if profile.get("family") == "attack_rider_spell" and profile != PROFILES.get(spell):
            raise ValueError("Attack rider differs from its pinned source: " + spell)


def _push(name, value):
    return {"list_push": {"list": name, "value": value}}


def _append(field, value):
    """ListPush owns locals; commit an authority collection through its real Set owner."""
    return [
        _set("_rider_append", field),
        _push("_rider_append", value),
        _set(field, "_rider_append"),
    ]


def _source(field, source_id="source_id"):
    return "state.attack_rider_sources.{" + source_id + "}." + field


def _initialize():
    return [
        _when("state.attack_rider_sources == null", [_set("state.attack_rider_sources", {})]),
        _when("state.attack_rider_source_ids == null", [_set("state.attack_rider_source_ids", [])]),
        _when("state.turn_effects == null", [_set("state.turn_effects", [])]),
    ]


def _hit_tool():
    """Accept only the original final hit staged by the actual post-defense attack owner."""
    return _tool(
        "Read one saved hit after AC, Shield and Mirror settlement and before its original damage.",
        [
            _entity("caster_state"),
            _parameter("pending_action_id"),
            _parameter("spell", "enum", enum_values=list(AFTER_HIT_SPELLS)),
        ],
        [
            _require(
                (
                    "state.pending_srd_actions != null and pending_action_id in "
                    "state.pending_srd_actions"
                ),
                "Select the actual pending attack hit.",
            ),
            _set("_smite_hit", "state.pending_srd_actions[pending_action_id]"),
            _require(
                (
                    "_smite_hit.status == 'open' and _smite_hit.stage == "
                    "'attack_hit_riders' and _smite_hit.actor_state_id == "
                    "_caster_state_id and (_smite_hit.attack_outcome == 'hit' or "
                    "_smite_hit.attack_outcome == 'critical_hit')"
                ),
                (
                    "A Smite requires this caster's final accepted hit before damage; "
                    "the defensive window is not sufficient."
                ),
            ),
            _require(
                (
                    "_smite_hit.attack_roll != null and _smite_hit.attack_roll.total >= "
                    "1 and _smite_hit.attack_roll.total <= 20"
                ),
                "Preserve the original real attack roll.",
            ),
            _require(
                (
                    "combat.turn_active == true and combat.active_actor == "
                    "_caster_state_id and _smite_hit.turn_actor == _caster_state_id"
                ),
                "Take this Bonus Action immediately on the original attack turn.",
            ),
            _set("_smite_weapon", None),
            _set(
                "_smite_unarmed",
                (
                    "_smite_hit.owner_kind == 'unarmed_strike' and "
                    "_smite_hit.parameters.effect == 'damage'"
                ),
            ),
            _when(
                "_smite_unarmed != true and _smite_hit.parameters.weapon != null",
                [
                    {
                        "table_lookup": {
                            "table": "definitions.weapons",
                            "key": "_smite_hit.parameters.weapon",
                            "required": True,
                        },
                        "store_as": "_smite_weapon",
                    },
                ],
            ),
            _require(
                (
                    "(spell == 'ensnaring_strike' and _smite_weapon != null) or "
                    "(spell != 'ensnaring_strike' and (_smite_unarmed == true or "
                    "(_smite_weapon != null and _smite_weapon.kind == 'melee')))"
                ),
                (
                    "Ensnaring Strike requires an actual weapon hit; Smites require "
                    "a Melee weapon or successful Unarmed Strike."
                ),
            ),
            _set("_smite_target_id", "_smite_hit.target_state_id"),
            _set("_smite_critical", "_smite_hit.attack_outcome == 'critical_hit'"),
            _require(
                "_smite_hit.smite_cast_id == null",
                "Only the original unused after-hit Bonus Action may attach a Smite.",
            ),
        ],
    )


def _lookup_tool():
    """Require both the saved source and its still-retained canonical spell effect."""
    return _tool(
        "Read the exact current rider source; removed effects cannot survive in an index.",
        [_parameter("source_id")],
        [
            _set("_rider_source", None),
            _set("_rider_live", False),
            _set("_rider_current", False),
            _when(
                "state.attack_rider_sources != null and source_id in state.attack_rider_sources",
                [_set("_rider_source", "state.attack_rider_sources[source_id]")],
            ),
            _when(
                "_rider_source != null and _rider_source.installed == true",
                [
                    _call(
                        "check_srd_attack_rider_record",
                        {"source_id": "{source_id}", "caster_state": "{_rider_source.caster}"},
                        ("_rider_live", "_rider_current"),
                    ),
                ],
            ),
            _set("_rider_source_id", "source_id"),
        ],
    )


def _record_tool():
    return _tool(
        "Qualify an installed rider against its exact caster, clock, effect and Concentration.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_rider_live", False),
            _set("_rider_current", False),
            _set("_rider_record", None),
            _when(
                "state.attack_rider_sources != null and source_id in state.attack_rider_sources",
                [_set("_rider_record", "state.attack_rider_sources[source_id]")],
            ),
            _set("_rider_retained", False),
            _set("_rider_suppressed", False),
            _when(
                "state.turn_effects != null",
                [
                    _loop(
                        "state.turn_effects",
                        "_rider_meta",
                        [
                            _when(
                                (
                                    "_rider_meta.kind == 'attack_rider_spell' and "
                                    "_rider_meta.source_id "
                                    "== source_id and _rider_meta.source_caster_id == "
                                    "_caster_state_id"
                                ),
                                [
                                    _set("_rider_retained", True),
                                    _when(
                                        "_rider_meta.suppressed == true",
                                        [_set("_rider_suppressed", True)],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                (
                    "_rider_record != null and _rider_record.installed == true and "
                    "_rider_retained == true and _rider_record.caster == "
                    "_caster_state_id and state.lasting_tick < _rider_record.expires_tick"
                ),
                [
                    _set("_rider_current", True),
                    _when(
                        "_rider_record.concentration == true",
                        [
                            _set(
                                "_rider_current",
                                (
                                    "caster_state.is_alive == true and "
                                    "caster_state.concentration_cast_id == source_id and "
                                    "caster_state.concentration_spell_id == "
                                    "_rider_record.spell and "
                                    "caster_state.concentration_effect_family == "
                                    "'attack_rider_spell' "
                                    "and not ('incapacitated' in caster_state.conditions or "
                                    "'paralyzed' "
                                    "in caster_state.conditions or 'petrified' in "
                                    "caster_state.conditions or 'stunned' in "
                                    "caster_state.conditions or "
                                    "'unconscious' in caster_state.conditions)"
                                ),
                            ),
                        ],
                    ),
                    _set(
                        "_rider_live",
                        (
                            "_rider_current == true and _rider_record.suppressed != true and "
                            "_rider_suppressed != true"
                        ),
                    ),
                ],
            ),
        ],
    )


def _link_tool():
    return _tool(
        "Index one actual source on its actual target without duplicating a prior instance.",
        [_entity("target_state"), _parameter("source_id")],
        [
            _when(
                "target_state.attack_rider_source_ids == null",
                [_set("target_state.attack_rider_source_ids", [])],
            ),
            _when(
                "source_id not in target_state.attack_rider_source_ids",
                _append("target_state.attack_rider_source_ids", "source_id"),
            ),
            _set("target_state.has_attack_rider_sources", True),
        ],
    )


def _install_tool():
    """Install only the source facts of a committed paid cast; never pay twice."""
    parameters = [
        _entity("caster_state"),
        _entity("target_state"),
        _parameter(
            "spell",
            "enum",
            enum_values=list(MARKS) + ["searing_smite", "shining_smite", "ensnaring_strike"],
        ),
        _parameter("slot_level", "integer", minimum=1, maximum=9),
        _parameter("spell_cast_id"),
        _parameter("save_dc", "integer"),
        _parameter("concentration_save_modifier", "integer"),
        deepcopy(CAST_CHOICES[0]),
    ]
    effects = [
        *_initialize(),
        _require(
            (
                "caster_state.is_alive == true and spell_cast_id != '' and "
                "caster_state.last_spell_source_cast_id == spell_cast_id and "
                "caster_state.last_spell_source_spell == spell"
            ),
            "Install only the exact committed paid spell source.",
        ),
        _require(
            "spell_cast_id not in state.attack_rider_sources",
            "The actual paid cast already installed this source.",
        ),
        {
            "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
            "store_as": "_rider_profile",
        },
        _require(
            (
                "_rider_profile.family == 'attack_rider_spell' and slot_level >= "
                "_rider_profile.base_level"
            ),
            "Use the actual rider spell and its paid slot.",
        ),
        _set("_rider_duration", "_rider_profile.duration_rounds"),
        _when("spell == 'hex' and slot_level == 2", [_set("_rider_duration", 2400)]),
        _when(
            "(spell == 'hex' or spell == 'hunters_mark') and (slot_level == 3 or slot_level == 4)",
            [_set("_rider_duration", 4800)],
        ),
        _when(
            "(spell == 'hex' or spell == 'hunters_mark') and slot_level >= 5",
            [_set("_rider_duration", 14400)],
        ),
        _when(
            "_rider_profile.concentration == true",
            [
                _when(
                    "caster_state.concentration_spell_id != null",
                    [
                        _call(
                            "end_srd_concentration_node",
                            {
                                "source_state": "{_caster_state_id}",
                                "target_state": "{caster_state.concentration_target_state_id}",
                            },
                        )
                    ],
                ),
                _set("caster_state.concentration_spell_id", "spell"),
                _set("caster_state.concentration_cast_id", "spell_cast_id"),
                _set("caster_state.concentration_effect_family", "'attack_rider_spell'"),
                _set("caster_state.concentration_target_state_id", "_target_state_id"),
                _set("caster_state.concentration_target_state_ids", ["_target_state_id"]),
                _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                _set(
                    "caster_state.concentration_expires_tick",
                    "state.lasting_tick + 2 * _rider_duration",
                ),
                _set("caster_state.concentration_save_modifier", "concentration_save_modifier"),
            ],
        ),
        _set("_rider_last_target_turn", -1),
        _when(
            "target_state.turns_started != null",
            [_set("_rider_last_target_turn", "target_state.turns_started")],
        ),
        _set(
            "state.attack_rider_sources.{spell_cast_id}",
            {
                "source_id": "spell_cast_id",
                "cast_id": "spell_cast_id",
                "caster": "_caster_state_id",
                "target": "_target_state_id",
                "spell": "spell",
                "slot_level": "slot_level",
                "spell_level": "slot_level",
                "save_dc": "save_dc",
                "ability": "rider_ability",
                "installed": True,
                "suppressed": False,
                "concentration": "_rider_profile.concentration",
                "started_tick": "state.lasting_tick",
                "expires_tick": "state.lasting_tick + 2 * _rider_duration",
                "dropped_to_zero": False,
                "dropped_caster_turn": -1,
                "dropped_tick": -1,
                "last_target_turn": "_rider_last_target_turn",
            },
        ),
        *_append(
            "state.turn_effects",
            {
                "kind": "'attack_rider_spell'",
                "spell": "spell",
                "source_id": "spell_cast_id",
                "cast_id": "spell_cast_id",
                "source_caster_id": "_caster_state_id",
                "caster": "_caster_state_id",
                "target": "_target_state_id",
                "spell_level": "slot_level",
                "ongoing_duration": "_rider_duration > 0",
                "suppressed": False,
                "created_tick": "state.lasting_tick",
                "expires_tick": "state.lasting_tick + 2 * _rider_duration",
            },
        ),
        _call(
            "link_srd_attack_rider_target",
            {"target_state": "{_target_state_id}", "source_id": "{spell_cast_id}"},
        ),
        _when(
            "caster_state.attack_rider_owned_ids == null",
            [_set("caster_state.attack_rider_owned_ids", [])],
        ),
        *_append("caster_state.attack_rider_owned_ids", "spell_cast_id"),
        *_append("state.attack_rider_source_ids", "spell_cast_id"),
        _set("caster_state.has_attack_rider_owned_sources", True),
        _set("state.has_attack_rider_spells", True),
        _set("_rider_source_id", "spell_cast_id"),
    ]
    return _tool(
        "Retain the exact paid rider source and original upcast lifetime.", parameters, effects
    )


def _mark_cast_tool():
    """Normal marks require actual creature sight and range before source installation."""
    parameters = [
        _entity("caster_state"),
        _entity("target_state"),
        _parameter("spell", "enum", enum_values=list(MARKS)),
        _parameter("slot_level", "integer", minimum=1, maximum=9),
        _parameter("spell_cast_id"),
        _parameter("save_dc", "integer"),
        _parameter("concentration_save_modifier", "integer"),
        deepcopy(CAST_CHOICES[0]),
    ]
    return _tool_scene(
        "Place an actual paid curse or mark on a seen creature within90 feet.",
        parameters
        + [
            _parameter("target_distance", "number", minimum=0),
            _parameter("caster_can_see_target", "boolean"),
        ],
        [
            _require(
                (
                    "target_state.creature_kind == 'character' or "
                    "target_state.creature_kind == 'monster'"
                ),
                "Mark an actual creature.",
            ),
            {
                "condition": (
                    "state.spatial_scene != null and state.spatial_scene.map != null and "
                    "state.spatial_scene.map.grid != null"
                ),
                "on_true": [
                    _call(
                        "resolve_srd_scene_relation",
                        {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                        (
                            "_scene_same_plane",
                            "_scene_distance",
                            "_scene_can_see",
                            "_scene_line_of_sight",
                        ),
                    ),
                    _require(
                        (
                            "_scene_same_plane == true and _scene_distance <= 90 and "
                            "_scene_can_see == true and _scene_line_of_sight == true"
                        ),
                        (
                            "The new creature must actually be visible and within90 feet "
                            "on this plane."
                        ),
                    ),
                ],
                "on_false": [
                    _require(
                        "target_distance <= 90 and caster_can_see_target == true",
                        "The new creature must actually be visible and within90 feet.",
                    ),
                ],
            },
            _call(
                "install_srd_attack_rider_source",
                {
                    p["name"]: "{_" + p["name"] + "_id}"
                    if p.get("resolve") == "entity"
                    else "{" + p["name"] + "}"
                    for p in parameters
                },
                ("_rider_source_id",),
            ),
        ],
    )


def _mark_roll_tool():
    """The attack owner calls this on the accepted hit, before any Smite changes Concentration."""
    return _tool(
        "Roll earned Hex or Hunter's Mark components without a second damage event.",
        [
            _entity("attacker_state"),
            _entity("target_state"),
            _optional("critical_hit", "boolean", False),
        ],
        [
            _set("_rider_components", []),
            _set("_rider_damage_rolls", []),
            _set("_rider_owned_ids", []),
            _when(
                (
                    "attacker_state.has_attack_rider_owned_sources == true and "
                    "attacker_state.attack_rider_owned_ids != null"
                ),
                [_set("_rider_owned_ids", "attacker_state.attack_rider_owned_ids")],
            ),
            _loop(
                "_rider_owned_ids",
                "_rider_id",
                [
                    _call(
                        "get_srd_attack_rider_source", {"source_id": "{_rider_id}"}, SOURCE_EXPORTS
                    ),
                    _when(
                        (
                            "_rider_live == true and _rider_source.caster == _attacker_state_id "
                            "and _rider_source.target == _target_state_id and "
                            "(_rider_source.spell == 'hex' or _rider_source.spell == "
                            "'hunters_mark')"
                        ),
                        [
                            {
                                "dice": {
                                    "expression": "1d6",
                                    "critical": "critical_hit == true",
                                },
                                "store_as": "_rider_mark_roll",
                            },
                            _set("_rider_mark_type", "'necrotic'"),
                            _when(
                                "_rider_source.spell == 'hunters_mark'",
                                [_set("_rider_mark_type", "'force'")],
                            ),
                            _push(
                                "_rider_components",
                                {
                                    "source_id": "_rider_id",
                                    "spell": "_rider_source.spell",
                                    "damage_type": "_rider_mark_type",
                                    "amount": "_rider_mark_roll.total",
                                    "roll": "_rider_mark_roll",
                                },
                            ),
                            _push("_rider_damage_rolls", "_rider_mark_roll"),
                        ],
                    ),
                ],
            ),
        ],
    )


def _d20_tool():
    """Read source benefits without treating an ability-check curse as a save penalty."""
    return _tool(
        "Read exact Shining/Hex/Mark D20 benefits for the actual roll and subject.",
        [
            _entity("actor_state"),
            _parameter("kind"),
            _parameter("ability"),
            _optional("skill", default=""),
            _optional("subject_state_id", default=""),
        ],
        [
            _set("_rider_check_disadvantage", False),
            _set("_rider_finding_advantage", False),
            _set("_rider_attack_advantage", False),
            _set("_rider_ids", []),
            _when(
                "kind == 'ability_check' and actor_state.attack_rider_source_ids != null",
                [
                    _loop(
                        "actor_state.attack_rider_source_ids",
                        "_rider_id",
                        [
                            _call(
                                "get_srd_attack_rider_source",
                                {"source_id": "{_rider_id}"},
                                SOURCE_EXPORTS,
                            ),
                            _when(
                                (
                                    "_rider_live == true and _rider_source.spell == 'hex' and "
                                    "_rider_source.target == _actor_state_id and "
                                    "_rider_source.ability "
                                    "== ability"
                                ),
                                [_set("_rider_check_disadvantage", True)],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                (
                    "kind == 'ability_check' and ability == 'wisdom' and (skill == "
                    "'perception' or skill == 'survival') and subject_state_id != '' and "
                    "actor_state.attack_rider_owned_ids != null"
                ),
                [
                    _loop(
                        "actor_state.attack_rider_owned_ids",
                        "_rider_id",
                        [
                            _call(
                                "get_srd_attack_rider_source",
                                {"source_id": "{_rider_id}"},
                                SOURCE_EXPORTS,
                            ),
                            _when(
                                (
                                    "_rider_live == true and _rider_source.spell == "
                                    "'hunters_mark' and "
                                    "_rider_source.caster == _actor_state_id and "
                                    "_rider_source.target == "
                                    "subject_state_id"
                                ),
                                [_set("_rider_finding_advantage", True)],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "kind == 'attack_roll' and subject_state_id != ''",
                [
                    _call(
                        "check_srd_shining_attack_target",
                        {"target_state": "{subject_state_id}"},
                        ("_rider_attack_advantage",),
                    ),
                ],
            ),
        ],
    )


def _shining_target_tool():
    return _tool(
        "Shining's real active target grants attack Advantage without a sight prerequisite.",
        [_entity("target_state")],
        [
            _set("_rider_attack_advantage", False),
            _when(
                "target_state.attack_rider_source_ids != null",
                [
                    _loop(
                        "target_state.attack_rider_source_ids",
                        "_rider_id",
                        [
                            _call(
                                "get_srd_attack_rider_source",
                                {"source_id": "{_rider_id}"},
                                SOURCE_EXPORTS,
                            ),
                            _when(
                                (
                                    "_rider_live == true and _rider_source.spell == "
                                    "'shining_smite' and "
                                    "_rider_source.target == _target_state_id"
                                ),
                                [_set("_rider_attack_advantage", True)],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _smite_roll_tool():
    """One paid Smite rolls once into its original accepted-hit event, without HP mutation."""
    return _tool(
        "Roll one already-paid Smite component and save its real critical dice on the hit.",
        [_entity("caster_state"), _parameter("pending_action_id")],
        [
            _set("_smite_event", "state.pending_srd_actions[pending_action_id]"),
            _require(
                "_smite_event.stage == 'attack_hit_riders' and "
                "_smite_event.actor_state_id == _caster_state_id and "
                "_smite_event.smite_cast_id == caster_state.last_spell_source_cast_id and "
                "_smite_event.smite_spell == caster_state.last_spell_source_spell and "
                "(_smite_event.smite_spell == 'divine_smite' or "
                "_smite_event.smite_spell == 'searing_smite' or "
                "_smite_event.smite_spell == 'shining_smite') and "
                "_smite_event.smite_component == null",
                "Roll only this actual paid Smite's unused original hit component.",
            ),
            _set("_smite_count", "_smite_event.smite_slot_level"),
            _set("_smite_die", "'d6'"),
            _set("_smite_type", "'radiant'"),
            _when("_smite_event.smite_spell == 'searing_smite'", [_set("_smite_type", "'fire'")]),
            _when(
                "_smite_event.smite_spell == 'divine_smite'",
                [
                    _set("_smite_die", "'d8'"),
                    _set("_smite_count", "_smite_count + 1"),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_smite_event.target_state_id}"},
                        ("_info_scene_entity",),
                    ),
                    _when(
                        (
                            "_info_scene_entity.metadata.creature_type == 'fiend' or "
                            "_info_scene_entity.metadata.creature_type == 'undead'"
                        ),
                        [_set("_smite_count", "_smite_count + 1")],
                    ),
                ],
            ),
            {
                "dice": {
                    "expression": "{_smite_count}{_smite_die}",
                    "critical": "_smite_event.attack_outcome == 'critical_hit'",
                },
                "store_as": "_smite_damage_roll",
            },
            _set(
                "_smite_component",
                {
                    "source_id": "_smite_event.smite_cast_id",
                    "spell": "_smite_event.smite_spell",
                    "damage_type": "_smite_type",
                    "amount": "_smite_damage_roll.total",
                    "roll": "_smite_damage_roll",
                },
            ),
            _set(
                "state.pending_srd_actions.{pending_action_id}.smite_component", "_smite_component"
            ),
        ],
    )


def _smite_cast_tool():
    parameters = [
        _entity("caster_state"),
        _parameter("pending_action_id"),
        _parameter("spell", "enum", enum_values=list(AFTER_HIT_SPELLS)),
        _parameter("slot_level", "integer", minimum=1, maximum=9),
        _parameter("spell_cast_id"),
        _parameter("save_dc", "integer"),
        _parameter("concentration_save_modifier", "integer"),
    ]
    return _tool(
        "Attach the successful paid after-hit Bonus Action spell to its authenticated hit.",
        parameters,
        [
            _when("state.smite_cast_records == null", [_set("state.smite_cast_records", {})]),
            _require(
                (
                    "spell_cast_id != '' and caster_state.last_spell_source_cast_id == "
                    "spell_cast_id and caster_state.last_spell_source_spell == spell"
                ),
                "Only the actual successful paid Bonus Action supplies this Smite.",
            ),
            _require(
                "spell_cast_id not in state.smite_cast_records",
                "This paid Smite source already attached its original hit.",
            ),
            _require(
                (
                    "(spell == 'shining_smite' and slot_level >= 2) or (spell != "
                    "'shining_smite' and slot_level >= 1)"
                ),
                "Preserve the original paid slot level.",
            ),
            _call(
                "prepare_srd_smite_hit",
                {
                    "caster_state": "{_caster_state_id}",
                    "pending_action_id": "{pending_action_id}",
                    "spell": "{spell}",
                },
                ("_smite_target_id", "_smite_critical"),
            ),
            _when(
                "spell != 'divine_smite'",
                [
                    _call(
                        "install_srd_attack_rider_source",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_smite_target_id}",
                            "spell": "{spell}",
                            "slot_level": "{slot_level}",
                            "spell_cast_id": "{spell_cast_id}",
                            "save_dc": "{save_dc}",
                            "concentration_save_modifier": "{concentration_save_modifier}",
                        },
                        ("_rider_source_id",),
                    ),
                ],
            ),
            _set("state.pending_srd_actions.{pending_action_id}.smite_cast_id", "spell_cast_id"),
            _set("state.pending_srd_actions.{pending_action_id}.smite_spell", "spell"),
            _set("state.pending_srd_actions.{pending_action_id}.smite_slot_level", "slot_level"),
            _set(
                "state.smite_cast_records.{spell_cast_id}",
                {
                    "caster": "_caster_state_id",
                    "pending_action_id": "pending_action_id",
                    "spell": "spell",
                },
            ),
            {
                "condition": "spell == 'ensnaring_strike'",
                "on_true": [
                    _set("_smite_damage_roll", None),
                    _set("_smite_component", None),
                    _call(
                        "resolve_srd_ensnaring_strike_hit",
                        {"target_state": "{_smite_target_id}", "source_id": "{spell_cast_id}"},
                        ("_ensnaring_save", "_ensnaring_save_outcome"),
                    ),
                ],
                "on_false": [
                    _call(
                        "roll_srd_smite_hit_component",
                        {
                            "caster_state": "{_caster_state_id}",
                            "pending_action_id": "{pending_action_id}",
                        },
                        ("_smite_damage_roll", "_smite_component"),
                    ),
                ],
            },
            _when(
                "spell == 'shining_smite'",
                [
                    _call(
                        "install_srd_shining_smite_light",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_smite_target_id}",
                            "source_id": "{spell_cast_id}",
                        },
                    )
                ],
            ),
            _set("_rider_source_id", "spell_cast_id"),
        ],
    )


def _shining_light_tool():
    return _tool_scene(
        "Attach Shining's actual bright5 light and Invisible suppression to its hit creature.",
        [_entity("caster_state"), _entity("target_state"), _parameter("source_id")],
        [
            _require(
                (
                    "target_state.creature_kind == 'character' or "
                    "target_state.creature_kind == 'monster'"
                ),
                "Shining Smite's target must be an actual creature.",
            ),
            _call("get_srd_attack_rider_source", {"source_id": "{source_id}"}, SOURCE_EXPORTS),
            _require(
                (
                    "_rider_live == true and _rider_source.spell == 'shining_smite' and "
                    "_rider_source.target == _target_state_id and _rider_source.caster "
                    "== _caster_state_id"
                ),
                "Use Shining's actual active concentrated source.",
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_target_state_id}"},
                ("_info_position", "_info_scene_entity"),
            ),
            *_append(
                "state.turn_effects",
                {
                    "kind": "'illumination_spell'",
                    "spell": "'shining_smite'",
                    "source": "_target_state_id",
                    "target": "_caster_state_id",
                    "caster": "_caster_state_id",
                    "source_id": "source_id",
                    "cast_id": "source_id",
                    "source_caster_id": "_caster_state_id",
                    "rider_source_id": "source_id",
                    "instance": "source_id",
                    "spell_level": "_rider_source.slot_level",
                    "origin": "_info_position",
                    "plane": "_info_scene_entity.metadata.plane",
                    "mode": "'outline'",
                    "bright_radius": 5,
                    "dim_radius": 0,
                    "dark_radius": 0,
                    "suppress_invisible": True,
                    "concentration": False,
                    "ongoing_duration": True,
                    "expires_tick": "_rider_source.expires_tick",
                    "suppressed": False,
                },
            ),
            _set("state.has_illumination_spells", True),
            _call("sync_srd_illumination_invisible", {"actor_state": "{_target_state_id}"}),
        ],
    )


def _unlink_tool():
    return _tool(
        "Remove only the exact rider's target index.",
        [_entity("target_state"), _parameter("source_id")],
        [
            _set("_rider_target_keep", []),
            _when(
                "target_state.attack_rider_source_ids != null",
                [
                    _loop(
                        "target_state.attack_rider_source_ids",
                        "_rider_target_id",
                        [
                            _when(
                                "_rider_target_id != source_id",
                                [_push("_rider_target_keep", "_rider_target_id")],
                            ),
                        ],
                    ),
                ],
            ),
            _set("target_state.attack_rider_source_ids", "_rider_target_keep"),
            _set("target_state.has_attack_rider_sources", "len(_rider_target_keep) > 0"),
        ],
    )


def _retire_tool():
    """Retirement cannot call Concentration termination, preserving the shared static DAG."""
    return _tool(
        "Retire one exact source and its light, preserving all independent sources.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_rider_retire", None),
            _when(
                "state.attack_rider_sources != null and source_id in state.attack_rider_sources",
                [
                    _set("_rider_retire", "state.attack_rider_sources[source_id]"),
                ],
            ),
            _when(
                "_rider_retire != null and _rider_retire.installed == true",
                [
                    _require(
                        "_rider_retire.caster == _caster_state_id",
                        "Retire only this source's actual caster.",
                    ),
                    _set(_source("installed"), False),
                    _when(
                        "_rider_retire.spell == 'ensnaring_strike'",
                        [_call("retire_srd_ensnaring_condition", {"source_id": "{source_id}"})],
                    ),
                    _set("_rider_effect_keep", []),
                    _loop(
                        "state.turn_effects",
                        "_rider_retire_effect",
                        [
                            {
                                "condition": (
                                    "(_rider_retire_effect.kind == 'attack_rider_spell' and "
                                    "_rider_retire_effect.source_id == source_id) or "
                                    "(_rider_retire_effect.kind == 'illumination_spell' and "
                                    "_rider_retire_effect.rider_source_id == source_id)"
                                ),
                                "on_true": [],
                                "on_false": [_push("_rider_effect_keep", "_rider_retire_effect")],
                            }
                        ],
                    ),
                    _set("state.turn_effects", "_rider_effect_keep"),
                    _call(
                        "unlink_srd_attack_rider_target",
                        {"target_state": "{_rider_retire.target}", "source_id": "{source_id}"},
                    ),
                    _set("_rider_owned_keep", []),
                    _loop(
                        "caster_state.attack_rider_owned_ids",
                        "_rider_keep_id",
                        [
                            _when(
                                "_rider_keep_id != source_id",
                                [_push("_rider_owned_keep", "_rider_keep_id")],
                            ),
                        ],
                    ),
                    _set("caster_state.attack_rider_owned_ids", "_rider_owned_keep"),
                    _set(
                        "caster_state.has_attack_rider_owned_sources", "len(_rider_owned_keep) > 0"
                    ),
                    _set("_rider_active_keep", []),
                    _loop(
                        "state.attack_rider_source_ids",
                        "_rider_keep_id",
                        [
                            _when(
                                "_rider_keep_id != source_id",
                                [_push("_rider_active_keep", "_rider_keep_id")],
                            ),
                        ],
                    ),
                    _set("state.attack_rider_source_ids", "_rider_active_keep"),
                    _set("state.has_attack_rider_spells", "len(_rider_active_keep) > 0"),
                    _when(
                        "_rider_retire.spell == 'shining_smite'",
                        [
                            _call(
                                "sync_srd_illumination_invisible",
                                {"actor_state": "{_rider_retire.target}"},
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _end_tool():
    return _tool(
        "End this source and clear only its actual surviving Concentration instance.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_rider_end_target", None),
            _when(
                "state.attack_rider_sources != null and source_id in state.attack_rider_sources",
                [
                    _require(
                        "state.attack_rider_sources[source_id].caster == _caster_state_id",
                        "End the actual source caster's rider.",
                    ),
                    _set("_rider_end_target", "state.attack_rider_sources[source_id].target"),
                    _call(
                        "retire_srd_attack_rider_source",
                        {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                    ),
                    _when(
                        (
                            "caster_state.concentration_cast_id == source_id and "
                            "caster_state.concentration_effect_family == 'attack_rider_spell'"
                        ),
                        [
                            _call(
                                "end_srd_concentration_node",
                                {
                                    "source_state": "{_caster_state_id}",
                                    "target_state": "{_rider_end_target}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _refresh_tool():
    return _tool(
        "Refresh only retained source IDs against current time, removal and Concentration.",
        [],
        [
            _set("_rider_refresh_ids", []),
            _when(
                "state.attack_rider_source_ids != null",
                [_set("_rider_refresh_ids", "state.attack_rider_source_ids")],
            ),
            _loop(
                "_rider_refresh_ids",
                "_rider_refresh_id",
                [
                    _call(
                        "get_srd_attack_rider_source",
                        {"source_id": "{_rider_refresh_id}"},
                        SOURCE_EXPORTS + ("_rider_current",),
                    ),
                    _when(
                        "_rider_source != null and _rider_current != true",
                        [
                            _call(
                                "finish_srd_attack_rider_source",
                                {
                                    "caster_state": "{_rider_source.caster}",
                                    "source_id": "{_rider_refresh_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _zero_stamp_tool():
    return _tool(
        "Record the actual marked target's zero-HP event before later recovery.",
        [_entity("caster_state"), _entity("target_state"), _parameter("source_id")],
        [
            _require(
                "target_state.hp == 0",
                "A mark transfer requires actual zero HP, not an asserted defeat.",
            ),
            _call("get_srd_attack_rider_source", {"source_id": "{source_id}"}, SOURCE_EXPORTS),
            _when(
                (
                    "_rider_live == true and _rider_source.caster == _caster_state_id "
                    "and _rider_source.target == _target_state_id and "
                    "(_rider_source.spell == 'hex' or _rider_source.spell == "
                    "'hunters_mark') and _rider_source.dropped_to_zero != true"
                ),
                [
                    _set(_source("dropped_to_zero"), True),
                    _set(_source("dropped_tick"), "state.lasting_tick"),
                    _when(
                        "caster_state.turns_started != null",
                        [_set(_source("dropped_caster_turn"), "caster_state.turns_started")],
                    ),
                ],
            ),
        ],
    )


def _zero_tool():
    """The final damage owner must observe zero before Clone and after a successful Fortitude."""
    return _tool(
        "Observe the real zero-HP target for earned mark transfer.",
        [_entity("target_state")],
        [
            _when(
                "target_state.hp == 0 and target_state.attack_rider_source_ids != null",
                [
                    _set("_rider_zero_ids", "target_state.attack_rider_source_ids"),
                    _loop(
                        "_rider_zero_ids",
                        "_rider_zero_id",
                        [
                            _call(
                                "get_srd_attack_rider_source",
                                {"source_id": "{_rider_zero_id}"},
                                SOURCE_EXPORTS,
                            ),
                            _when(
                                (
                                    "_rider_live == true and (_rider_source.spell == 'hex' or "
                                    "_rider_source.spell == 'hunters_mark')"
                                ),
                                [
                                    _call(
                                        "record_srd_attack_mark_zero",
                                        {
                                            "caster_state": "{_rider_source.caster}",
                                            "target_state": "{_target_state_id}",
                                            "source_id": "{_rider_zero_id}",
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


def _transfer_tool():
    tool = _tool_scene(
        "Move a genuine earned curse or quarry mark with one later Bonus Action.",
        [_entity("caster_state"), _entity("target_state"), _parameter("source_id")],
        [
            _call("get_srd_attack_rider_source", {"source_id": "{source_id}"}, SOURCE_EXPORTS),
            _require(
                (
                    "_rider_live == true and _rider_source.caster == _caster_state_id "
                    "and (_rider_source.spell == 'hex' or _rider_source.spell == "
                    "'hunters_mark') and _rider_source.dropped_to_zero == true"
                ),
                (
                    "Transfer only this actual caster's still-active mark after its "
                    "recorded target reached zero HP."
                ),
            ),
            _require(
                (
                    "_rider_source.spell != 'hex' or (_rider_source.dropped_caster_turn "
                    ">= 0 and caster_state.turns_started > "
                    "_rider_source.dropped_caster_turn)"
                ),
                (
                    "Hex's transfer must be on an actual later caster turn; an "
                    "unrecorded turn cannot establish that clause."
                ),
            ),
            _require(
                (
                    "target_state.creature_kind == 'character' or "
                    "target_state.creature_kind == 'monster'"
                ),
                "Select an actual creature.",
            ),
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ("_scene_same_plane", "_scene_distance", "_scene_can_see", "_scene_line_of_sight"),
            ),
            _require(
                (
                    "_scene_same_plane == true and _scene_distance <= 90 and "
                    "_scene_can_see == true and _scene_line_of_sight == true"
                ),
                "Transfer only to a genuinely seen creature within90 feet.",
            ),
            _call(
                "spend_spell_casting_budget",
                {
                    "caster_state": "{_caster_state_id}",
                    "casting_time": "bonus_action",
                    "slot_resource": "no_slot",
                },
            ),
            _set("_rider_previous_target", "_rider_source.target"),
            _call(
                "unlink_srd_attack_rider_target",
                {"target_state": "{_rider_previous_target}", "source_id": "{source_id}"},
            ),
            _set(_source("target"), "_target_state_id"),
            _set(_source("dropped_to_zero"), False),
            _set(_source("dropped_caster_turn"), -1),
            _set(_source("dropped_tick"), -1),
            _call(
                "link_srd_attack_rider_target",
                {"target_state": "{_target_state_id}", "source_id": "{source_id}"},
            ),
            _set("caster_state.concentration_target_state_id", "_target_state_id"),
            _set("caster_state.concentration_target_state_ids", ["_target_state_id"]),
            _set("_rider_transfer_effects", []),
            _loop(
                "state.turn_effects",
                "_rider_transfer_effect",
                [
                    _set("_rider_transferred_effect", "_rider_transfer_effect"),
                    _when(
                        (
                            "_rider_transfer_effect.kind == 'attack_rider_spell' and "
                            "_rider_transfer_effect.source_id == source_id"
                        ),
                        [
                            _set(
                                "_rider_transferred_effect",
                                {
                                    "kind": "'attack_rider_spell'",
                                    "spell": "_rider_source.spell",
                                    "source_id": "source_id",
                                    "cast_id": "source_id",
                                    "source_caster_id": "_caster_state_id",
                                    "caster": "_caster_state_id",
                                    "target": "_target_state_id",
                                    "spell_level": "_rider_source.slot_level",
                                    "ongoing_duration": True,
                                    "suppressed": False,
                                    "created_tick": "_rider_source.started_tick",
                                    "expires_tick": "_rider_source.expires_tick",
                                },
                            ),
                        ],
                    ),
                    _push("_rider_transfer_effects", "_rider_transferred_effect"),
                ],
            ),
            _set("state.turn_effects", "_rider_transfer_effects"),
            _set("_rider_transferred_spell", "_rider_source.spell"),
        ],
    )
    return _public(
        tool,
        "spell",
        "Move spell mark",
        [("Spell", "_rider_transferred_spell"), ("Target", "_target_state_id")],
    )


def _remove_hex_tool():
    return _tool(
        "Remove only actual Hex curses attached to this target.",
        [_entity("target_state")],
        [
            _call(
                "collect_srd_hex_curse_sources",
                {"target_state": "{_target_state_id}"},
                ("_rider_cure_ids",),
            ),
            _loop(
                "_rider_cure_ids",
                "_rider_cure_id",
                [
                    _call(
                        "get_srd_attack_rider_source",
                        {"source_id": "{_rider_cure_id}"},
                        SOURCE_EXPORTS,
                    ),
                    _when(
                        (
                            "_rider_source != null and _rider_source.installed == true and "
                            "_rider_source.spell == 'hex' and _rider_source.target == "
                            "_target_state_id"
                        ),
                        [
                            _call(
                                "finish_srd_attack_rider_source",
                                {
                                    "caster_state": "{_rider_source.caster}",
                                    "source_id": "{_rider_cure_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _hex_curse_sources_tool():
    """Recovery includes current suppressed Hexes, without manufacturing a condition source."""
    return _tool(
        "Collect only this target's retained current Hex curse source IDs.",
        [_entity("target_state")],
        [
            _set("_rider_cure_ids", []),
            _set("_rider_cure_candidates", []),
            _when(
                "target_state.attack_rider_source_ids != null",
                [_set("_rider_cure_candidates", "target_state.attack_rider_source_ids")],
            ),
            _loop(
                "_rider_cure_candidates",
                "_rider_cure_id",
                [
                    _call(
                        "get_srd_attack_rider_source",
                        {"source_id": "{_rider_cure_id}"},
                        SOURCE_EXPORTS + ("_rider_current",),
                    ),
                    _when(
                        "_rider_current == true and _rider_source.spell == 'hex' and "
                        "_rider_source.target == _target_state_id",
                        [_push("_rider_cure_ids", "_rider_cure_id")],
                    ),
                ],
            ),
        ],
    )


def _remove_one_hex_tool():
    return _tool(
        "End the exact selected current Hex curse, preserving every independent source.",
        [_entity("target_state"), _parameter("source_id")],
        [
            _call(
                "collect_srd_hex_curse_sources",
                {"target_state": "{_target_state_id}"},
                ("_rider_cure_ids",),
            ),
            _require(
                "source_id in _rider_cure_ids",
                "Choose an actual current Hex curse on this target.",
            ),
            _call("get_srd_attack_rider_source", {"source_id": "{source_id}"}, SOURCE_EXPORTS),
            _call(
                "finish_srd_attack_rider_source",
                {"caster_state": "{_rider_source.caster}", "source_id": "{source_id}"},
            ),
        ],
    )


def _searing_turn_tool():
    """Its later fire is a new noncritical game roll, followed by the actual saved-DC save."""
    return _tool(
        "Deal Searing's actual start-turn fire before its Constitution save, once per real turn.",
        [_entity("target_state"), _parameter("source_id")],
        [
            _call("get_srd_attack_rider_source", {"source_id": "{source_id}"}, SOURCE_EXPORTS),
            _require(
                (
                    "_rider_live == true and _rider_source.spell == 'searing_smite' and "
                    "_rider_source.target == _target_state_id"
                ),
                "Use the exact live Searing source on this actual target.",
            ),
            _require(
                (
                    "combat.turn_active == true and combat.active_actor == "
                    "_target_state_id and target_state.turns_started != null and "
                    "target_state.turns_started > _rider_source.last_target_turn"
                ),
                (
                    "Searing damage requires this target's actual new start turn, not a "
                    "replayed receipt."
                ),
            ),
            _set("_searing_source", "_rider_source"),
            _set(_source("last_target_turn"), "target_state.turns_started"),
            _set("_searing_before", "target_state.hp + target_state.temp_hp"),
            {
                "dice": {"expression": "{_searing_source.slot_level}d6"},
                "store_as": "_searing_damage_roll",
            },
            _set("_searing_adjusted_raw", "_searing_damage_roll.total"),
            _call(
                "refresh_srd_timed_condition_target",
                {
                    "target_state": "{_searing_source.caster}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
            _call(
                "modify_srd_actor_raw_damage_roll",
                {
                    "actor_state": "{_searing_source.caster}",
                    "amount": "{_searing_adjusted_raw}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_ray_raw_damage",),
            ),
            _set("_searing_adjusted_raw", "_ray_raw_damage"),
            _set("_searing_amount", "max(0, _searing_adjusted_raw)"),
            _set("_searing_unallocated_penalty", "max(0, -_searing_adjusted_raw)"),
            _call(
                "apply_srd_qualifying_damage",
                {
                    "target": "{_target_state_id}",
                    "amount": "{_searing_amount}",
                    "damage_type": "fire",
                    "adjustment": 0,
                    "source_state_id": "{_searing_source.caster}",
                    "game_damage_roll": True,
                    "unallocated_roll_penalty": "{_searing_unallocated_penalty}",
                    "observe_base_damage": True,
                },
                ("_source_base_effective_damage",),
            ),
            _set(
                "_searing_damage_taken",
                "max(0, _searing_before - target_state.hp - target_state.temp_hp)",
            ),
            _call(
                "d20_test",
                {
                    "actor_state": "{_target_state_id}",
                    "kind": "saving_throw",
                    "ability": "constitution",
                    "ability_modifier": "{target_state.saving_throw_modifiers.constitution}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "circumstantial_modifier": 0,
                    "target_number": "{_searing_source.save_dc}",
                    "advantage": False,
                    "disadvantage": False,
                    "forego_save": False,
                },
                ("_d20_test", "_outcome"),
            ),
            _set("_searing_save", "_d20_test"),
            _set("_searing_save_outcome", "_outcome"),
            _when(
                "_searing_save_outcome == 'success'",
                [
                    _call(
                        "finish_srd_attack_rider_source",
                        {"caster_state": "{_searing_source.caster}", "source_id": "{source_id}"},
                    ),
                ],
            ),
        ],
    )


def _start_tool():
    return _tool(
        "Process exact Searing and Ensnaring sources at this creature's start-turn boundary.",
        [_entity("target_state")],
        [
            _require(
                "combat.turn_active == true and combat.active_actor == _target_state_id "
                "and target_state.turns_started != null",
                "Use this target's actual committed start turn.",
            ),
            _set("_rider_start_ids", []),
            _set("_rider_turn_rows", []),
            _set("_rider_turn_damage", 0),
            _when(
                "target_state.attack_rider_source_ids != null",
                [_set("_rider_start_ids", "target_state.attack_rider_source_ids")],
            ),
            _loop(
                "_rider_start_ids",
                "_rider_start_id",
                [
                    _call(
                        "get_srd_attack_rider_source",
                        {"source_id": "{_rider_start_id}"},
                        SOURCE_EXPORTS + ("_rider_current",),
                    ),
                    _when(
                        (
                            "_rider_current == true and (_rider_source.spell == 'searing_smite' or "
                            "_rider_source.spell == 'ensnaring_strike') and "
                            "_rider_source.target == _target_state_id and "
                            "target_state.turns_started > _rider_source.last_target_turn"
                        ),
                        [
                            {
                                "condition": "_rider_live == true",
                                "on_true": [
                                    {
                                        "condition": "_rider_source.spell == 'ensnaring_strike'",
                                        "on_true": [
                                            _call(
                                                "resolve_srd_ensnaring_strike_turn",
                                                {
                                                    "target_state": "{_target_state_id}",
                                                    "source_id": "{_rider_start_id}",
                                                },
                                                (
                                                    "_ensnaring_damage_roll",
                                                    "_ensnaring_damage_taken",
                                                ),
                                            ),
                                            _set(
                                                "_rider_turn_damage",
                                                "_rider_turn_damage + _ensnaring_damage_taken",
                                            ),
                                            _set(
                                                "_ensnaring_turn_row",
                                                {
                                                    "kind": "'Ensnaring Strike'",
                                                    "target": "_target_state_id",
                                                    "value": "_ensnaring_damage_taken",
                                                    "damage_roll": None,
                                                    "detail": (
                                                        "'Start-turn Piercing; no repeat save'"
                                                    ),
                                                },
                                            ),
                                            _when(
                                                "_ensnaring_damage_roll != null",
                                                [
                                                    _set(
                                                        "_ensnaring_roll_patch",
                                                        {"damage_roll": "_ensnaring_damage_roll"},
                                                    ),
                                                    _set(
                                                        "_ensnaring_turn_row",
                                                        "merge(_ensnaring_turn_row, "
                                                        "_ensnaring_roll_patch)",
                                                    ),
                                                ],
                                            ),
                                            _push("_rider_turn_rows", "_ensnaring_turn_row"),
                                        ],
                                        "on_false": [
                                            _call(
                                                "resolve_srd_searing_smite_turn",
                                                {
                                                    "target_state": "{_target_state_id}",
                                                    "source_id": "{_rider_start_id}",
                                                },
                                                (
                                                    "_searing_damage_roll",
                                                    "_searing_damage_taken",
                                                    "_searing_save",
                                                    "_searing_save_outcome",
                                                ),
                                            ),
                                            _set(
                                                "_rider_turn_damage",
                                                "_rider_turn_damage + _searing_damage_taken",
                                            ),
                                            _push(
                                                "_rider_turn_rows",
                                                {
                                                    "kind": "'Searing Smite'",
                                                    "target": "_target_state_id",
                                                    "value": "_searing_damage_taken",
                                                    "detail": "_searing_save_outcome",
                                                    "damage_roll": "_searing_damage_roll",
                                                    "save_roll": "_searing_save",
                                                },
                                            ),
                                        ],
                                    }
                                ],
                                "on_false": [
                                    _set(
                                        _source("last_target_turn", "_rider_start_id"),
                                        "target_state.turns_started",
                                    )
                                ],
                            },
                        ],
                    ),
                ],
            ),
        ],
    )


def _bind_source_consequences(tools, changed):
    """Join start-turn and recovery at their existing source-owning boundaries only."""
    begin = deepcopy(tools["begin_combat_turn"])
    if "start_srd_damage_rider_turn" not in str(begin["effects"]):
        positions = [
            index
            for index, effect in enumerate(begin["effects"])
            if effect.get("set", {}).get("field") == "combat.turn_active"
            and effect["set"]["value"] is True
        ]
        if len(positions) != 1:
            raise ValueError("Searing requires the actual committed start-turn boundary.")
        begin["effects"].insert(
            positions[0] + 1,
            _when(
                "actor.has_attack_rider_sources == true",
                [
                    _call(
                        "start_srd_damage_rider_turn",
                        {"target_state": "{_actor_id}"},
                        ("_rider_turn_rows", "_rider_turn_damage"),
                    ),
                    _loop(
                        "_rider_turn_rows",
                        "_rider_turn_row",
                        [_push("_turn_effect_rows", "_rider_turn_row")],
                    ),
                    _set("_turn_effect_count", "len(_turn_effect_rows)"),
                    _set(
                        "_turn_effect_damage_applied",
                        "_turn_effect_damage_applied + _rider_turn_damage",
                    ),
                ],
            ),
        )
        changed["begin_combat_turn"] = begin
    for name in ("clear_srd_recovery_curses", "remove_srd_recovery_curses"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        if "remove_srd_hex_curses" not in str(tool["effects"]):
            tool["effects"].append(
                _when(
                    "target_state.has_attack_rider_sources == true",
                    [_call("remove_srd_hex_curses", {"target_state": "{_target_state_id}"})],
                )
            )
            changed[name] = tool
    name = "remove_srd_one_recovery_curse"
    if name in tools:
        tool = deepcopy(tools[name])
        if "collect_srd_hex_curse_sources" not in str(tool["effects"]):
            positions = [
                index
                for index, effect in enumerate(tool["effects"])
                if effect.get("set", {}).get("field") == "_selected_curse"
                and effect["set"]["value"] == "source_id"
            ]
            if len(positions) != 1:
                raise ValueError("Hex requires Greater Restoration's actual one-curse selector.")
            tool["effects"][positions[0] : positions[0]] = [
                _call(
                    "collect_srd_hex_curse_sources",
                    {"target_state": "{_target_state_id}"},
                    ("_rider_cure_ids",),
                ),
                _loop("_rider_cure_ids", "_rider_cure_id", [_push("_curse_ids", "_rider_cure_id")]),
            ]
            replacements = 0
            for effects in list(_lists(tool["effects"])):
                for index, effect in enumerate(effects):
                    if (
                        effect.get("call") == "retire_srd_timed_condition_source"
                        and effect.get("params", {}).get("source_id") == "{_selected_curse}"
                    ):
                        effects[index] = {
                            "condition": "_selected_curse in _rider_cure_ids",
                            "on_true": [
                                _call(
                                    "remove_srd_one_hex_curse",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "source_id": "{_selected_curse}",
                                    },
                                )
                            ],
                            "on_false": [effect],
                        }
                        replacements += 1
            if replacements != 1:
                raise ValueError("Hex requires one exact selected curse retirement callback.")
            changed[name] = tool


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _bind_collections(tool):
    """ForEach accepts a named array; snapshot each actual field into a private local first."""
    serial = 0

    def bind(effects):
        nonlocal serial
        result = []
        for effect in effects:
            for key in ("on_true", "on_false"):
                if key in effect:
                    effect[key] = bind(effect[key])
            if "for_each" in effect:
                loop = effect["for_each"]
                loop["do"] = bind(loop["do"])
                if "." in loop["collection"]:
                    serial += 1
                    local = f"_rider_collection_{serial}"
                    result.append(_set(local, loop["collection"]))
                    loop["collection"] = local
            result.append(effect)
        return result

    tool["effects"] = bind(tool["effects"])
    return tool


def bind_runtime_consumers(text):
    """Join only paid casting and source lifetime; Root owns actual hit/D20/damage joins."""
    text = extend_runtime(text)
    tools = _load_runtime(text)["tools"]
    changed = {}
    projected = bind_condition_projection(tools["refresh_srd_timed_condition_target"])
    if projected != tools["refresh_srd_timed_condition_target"]:
        changed["refresh_srd_timed_condition_target"] = projected
    _bind_source_consequences(tools, changed)
    for name in ("cast_spell", "cast_spell_outside_combat"):
        cast = deepcopy(tools[name])
        calls = [e.get("call") for sequence in _lists(cast["effects"]) for e in sequence]
        if "cast_srd_attack_mark" in calls or "cast_spell" in calls:
            continue
        for parameter in CAST_CHOICES:
            if not any(p["name"] == parameter["name"] for p in cast["parameters"]):
                cast["parameters"].append(deepcopy(parameter))
        positions = [
            i
            for i, e in enumerate(cast["effects"])
            if e.get("set", {}).get("field") == "_card_slots_after"
        ]
        if len(positions) != 1:
            raise ValueError("Riders require the final paid family emission before cast feedback.")
        cast["effects"].insert(
            positions[0],
            _when(
                (
                    "_spell_cast_failed != true and _pending_spell_operation_lost != "
                    "true and _spell_definition.family == 'attack_rider_spell'"
                ),
                [
                    _when(
                        "_spell_source_cast_id == null or _spell_source_cast_id == ''",
                        [
                            _call(
                                "begin_srd_spell_source_instance",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "spell": "{spell}",
                                    "slot_level": "{_slot_level}",
                                },
                                ("_spell_source_cast_id",),
                            ),
                        ],
                    ),
                    {
                        "condition": "spell == 'hex' or spell == 'hunters_mark'",
                        "on_true": [
                            _call(
                                "cast_srd_attack_mark",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "target_state": "{_scalar_target_id}",
                                    "spell": "{spell}",
                                    "slot_level": "{_slot_level}",
                                    "spell_cast_id": "{_spell_source_cast_id}",
                                    "target_distance": "{target_distance}",
                                    "caster_can_see_target": "{_caster_can_see_target}",
                                    "save_dc": "{_casting_profile.save_dc}",
                                    ("concentration_save_modifier"): (
                                        "{_casting_profile.concentration_save_modifier}"
                                    ),
                                    "rider_ability": "{rider_ability}",
                                },
                                ("_rider_source_id",),
                            ),
                        ],
                        "on_false": [
                            _call(
                                "cast_srd_hit_smite",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "pending_action_id": "{rider_pending_action_id}",
                                    "spell": "{spell}",
                                    "slot_level": "{_slot_level}",
                                    "spell_cast_id": "{_spell_source_cast_id}",
                                    "save_dc": "{_casting_profile.save_dc}",
                                    ("concentration_save_modifier"): (
                                        "{_casting_profile.concentration_save_modifier}"
                                    ),
                                },
                                ("_rider_source_id", "_smite_damage_roll", "_smite_component"),
                            ),
                        ],
                    },
                ],
            ),
        )
        changed[name] = cast
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_attack_riders" not in str(clock["effects"]):
        positions = [
            i
            for i, e in enumerate(clock["effects"])
            if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
        ]
        if len(positions) != 1:
            raise ValueError("Rider refresh requires the actual clock assignment.")
        clock["effects"].insert(
            positions[0] + 1,
            _when(
                "lasting_state.has_attack_rider_spells == true",
                [_call("refresh_srd_attack_riders", {})],
            ),
        )
        changed["advance_srd_lasting_clock"] = clock
    end = deepcopy(tools["end_srd_concentration_node"])
    if "retire_srd_attack_rider_source" not in str(end["effects"]):
        end["effects"].insert(
            0,
            _when(
                (
                    "source_state.concentration_effect_family == 'attack_rider_spell' "
                    "and source_state.concentration_cast_id != null"
                ),
                [
                    _call(
                        "retire_srd_attack_rider_source",
                        {
                            "caster_state": "{_source_state_id}",
                            "source_id": "{source_state.concentration_cast_id}",
                        },
                    )
                ],
            ),
        )
        changed["end_srd_concentration_node"] = end
    for name in ("process_srd_turn_effects", "refresh_srd_removed_spell_target"):
        tool = deepcopy(tools[name])
        if "refresh_srd_attack_riders" not in str(tool["effects"]):
            tool["effects"].append(
                _when(
                    "lasting_state.has_attack_rider_spells == true",
                    [_call("refresh_srd_attack_riders", {})],
                )
            )
            changed[name] = tool
    # Shining uses its actual rider source, independently of the illumination family.
    light = deepcopy(tools["check_srd_illumination_record"])
    if "get_srd_attack_rider_source" not in str(light["effects"]):
        light["effects"].append(
            _when(
                "record.spell == 'shining_smite'",
                [
                    _call(
                        "get_srd_attack_rider_source",
                        {"source_id": "{record.rider_source_id}"},
                        SOURCE_EXPORTS,
                    ),
                    _set(
                        "_illumination_active",
                        (
                            "_illumination_active == true and _rider_live == true and "
                            "_rider_source.target == record.source and _rider_source.caster == "
                            "_caster_state_id"
                        ),
                    ),
                ],
            )
        )
        changed["check_srd_illumination_record"] = light
    return _replace_tools(text, changed) if changed else text


def extend_runtime(text):
    tools = _load_runtime(text)["tools"]
    changed = {
        **ensnaring_tools(),
        "prepare_srd_smite_hit": _hit_tool(),
        "get_srd_attack_rider_source": _lookup_tool(),
        "check_srd_attack_rider_record": _record_tool(),
        "link_srd_attack_rider_target": _link_tool(),
        "install_srd_attack_rider_source": _install_tool(),
        "cast_srd_attack_mark": _mark_cast_tool(),
        "roll_srd_attack_mark_components": _mark_roll_tool(),
        "check_srd_attack_rider_d20": _d20_tool(),
        "check_srd_shining_attack_target": _shining_target_tool(),
        "roll_srd_smite_hit_component": _smite_roll_tool(),
        "cast_srd_hit_smite": _smite_cast_tool(),
        "install_srd_shining_smite_light": _shining_light_tool(),
        "unlink_srd_attack_rider_target": _unlink_tool(),
        "retire_srd_attack_rider_source": _retire_tool(),
        "finish_srd_attack_rider_source": _end_tool(),
        "refresh_srd_attack_riders": _refresh_tool(),
        "record_srd_attack_mark_zero": _zero_stamp_tool(),
        "observe_srd_attack_mark_zero": _zero_tool(),
        "transfer_srd_attack_mark": _transfer_tool(),
        "remove_srd_hex_curses": _remove_hex_tool(),
        "collect_srd_hex_curse_sources": _hex_curse_sources_tool(),
        "remove_srd_one_hex_curse": _remove_one_hex_tool(),
        "resolve_srd_searing_smite_turn": _searing_turn_tool(),
        "start_srd_damage_rider_turn": _start_tool(),
    }
    changed = {
        name: tool
        for name, raw in changed.items()
        if tools.get(name) != (tool := _bind_collections(raw))
    }
    return _replace_tools(text, changed) if changed else text
