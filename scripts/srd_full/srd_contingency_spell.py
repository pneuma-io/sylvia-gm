"""Contingency pays its supported chosen spell at setup and releases saved effects once."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_actor_spell_modifiers import _call, _emit, _when
    from .srd_control_behaviors import _each, _entity, _lasting, _param, _require, _set, _tool
    from .srd_timed_actor_defenses import _load_runtime
else:
    from srd_actor_spell_modifiers import _call, _emit, _when
    from srd_control_behaviors import _each, _entity, _lasting, _param, _require, _set, _tool
    from srd_timed_actor_defenses import _load_runtime

SPELL = "contingency"
FAMILY = "contingency_spell"
SOURCE_SHA256 = "b77c82e2d75a8cc5a356ebcf85840ebcfbb47e4051d13d003129c5e0cb85f46e"
POOL = "contingency_sources"
PROFILE = {
    "family": FAMILY,
    "base_level": 6,
    "casting_time": "10_minutes",
    "range_kind": "self",
    "range_feet": 0,
    "duration_rounds": 144000,
    "concentration": False,
}
CHILDREN = ("false_life", "water_breathing")
FALSE_LIFE_ROLL = "_spell_temporary_hit_point_roll"
CHOICES = (
    _param("contingent_spell", "enum", enum_values=["", *CHILDREN], required=False, default=""),
    _param(
        "contingent_slot_resource",
        "enum",
        enum_values=[f"spell_slot_{n}" for n in range(1, 6)],
        required=False,
        default="spell_slot_1",
    ),
    _param("contingent_spell_grant", required=False, default=""),
    _param(
        "contingent_material_item_ids",
        "array",
        items={"type": "string"},
        max_items=8,
        unique_items=True,
        required=False,
        default=[],
    ),
    _param(
        "contingent_hp_threshold", "integer", minimum=0, maximum=100000, required=False, default=0
    ),
)


def validate_profiles(definitions):
    """Retain the pinned ten-day, nonconcentration Self source header."""
    if SPELL in definitions and definitions[SPELL] != PROFILE:
        raise ValueError("Contingency must retain its pinned source profile.")


def _prepare_child(cast):
    """Borrow actual eligibility/component/slot checks without invoking a family or spending."""
    boundary = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("set", {}).get("field") == "_material_target_count"
    )
    prefix = deepcopy(cast["effects"][:boundary])
    prefix = [
        e
        for e in prefix
        if "prepare_srd_contingency_cast" not in str(e)
        and "install_srd_contingency_source" not in str(e)
    ]
    material = deepcopy(
        next(e for e in cast["effects"] if e.get("call") == "resolve_srd_spell_materials")
    )
    material["params"].update(validate_only=True, target_count=1)
    material["params"].pop("bond_target", None)
    core = {
        "spell",
        "caster",
        "caster_state",
        "slot_resource",
        "spell_grant",
        "material_component_available",
        "material_item_ids",
        "verbal_component_available",
        "somatic_component_available",
        "casting_mode",
        "spellbook_readable",
        "lasting_state",
    }
    params = [deepcopy(p) for p in cast["parameters"] if p["name"] in core]
    resource = next(p for p in params if p["name"] == "slot_resource")
    resource["enum_values"] = [f"spell_slot_{n}" for n in range(1, 6)]
    return _tool(
        "Validate the chosen Action spell using the original paid-casting preparation owner.",
        params,
        [
            _require(
                "caster != null",
                "This Contingency subset needs a Character caster and two actual spell slots.",
            ),
            _set("_ritual_book_required", False),
        ]
        + prefix
        + [
            _require(
                "spell == 'false_life' or spell == 'water_breathing'",
                "This chosen spell has no supported deferred release owner.",
            ),
            _require(
                "_spell_definition.base_level <= 5 and _spell_definition.concentration != true",
                "The chosen spell must be at most level five and supported without Concentration.",
            ),
            _require(
                "_spell_definition.casting_time == 'action' and _ritual_cast != true",
                "The chosen spell must have an Action casting time.",
            ),
            _set("_contingent_slot_level", 0),
            *[
                _when(f"slot_resource == 'spell_slot_{n}'", [_set("_contingent_slot_level", n)])
                for n in range(1, 6)
            ],
            material,
            _set(
                "_contingent_prepared",
                {
                    "caster": "_caster_state_id",
                    "spell": "spell",
                    "slot_resource": "_cast_resource",
                    "slot_level": "_contingent_slot_level",
                    "casting_profile": "_casting_profile",
                    "material_item_ids": "material_item_ids",
                },
            ),
        ],
    )


def _prepare_parent():
    """The ten-minute begin and completion both validate the actual child and carried statuette."""
    params = [
        _entity("caster_state"),
        _param("caster", resolve="participant", required=False, default=None),
        _lasting(),
        *deepcopy(CHOICES),
        _param(
            "material_item_ids", "array", items={"type": "string"}, max_items=8, unique_items=True
        ),
        _param("material_component_available", "boolean", required=False, default=False),
        _param("verbal_component_available", "boolean", required=False, default=False),
        _param("somatic_component_available", "boolean", required=False, default=False),
    ]
    return _tool(
        "Prepare supported Contingency choices, without spending either spell's slot or Action.",
        params,
        [
            _call(
                "prepare_srd_contingent_child",
                {
                    "caster": "{_caster_id}",
                    "caster_state": "{_caster_state_id}",
                    "spell": "{contingent_spell}",
                    "slot_resource": "{contingent_slot_resource}",
                    "spell_grant": "{contingent_spell_grant}",
                    "material_item_ids": "{contingent_material_item_ids}",
                    "material_component_available": "{material_component_available}",
                    "verbal_component_available": "{verbal_component_available}",
                    "somatic_component_available": "{somatic_component_available}",
                    "casting_mode": "ordinary",
                    "spellbook_readable": False,
                },
                ("_contingent_prepared",),
            ),
            _set("_contingent_statuette_item", None),
            _require(
                "caster_state.spell_materials != null and caster_state.inventory != null",
                "Record the actual carried Contingency material first.",
            ),
            _set("_contingent_supplies", "caster_state.spell_materials"),
            _each(
                "_contingent_supplies",
                "_supply",
                [
                    _when(
                        "_supply.id in material_item_ids and "
                        "'contingency.1' in _supply.components and "
                        "_supply.value_cp >= 150000 and _supply.quantity >= 1 and "
                        "_supply.inventory_item in caster_state.inventory",
                        [
                            _require(
                                "_contingent_statuette_item == null",
                                "Select one actual Contingency statuette.",
                            ),
                            _set("_contingent_statuette_item", "_supply.inventory_item"),
                        ],
                    ),
                ],
            ),
            _require(
                "_contingent_statuette_item != null",
                "Contingency needs its actual carried 1,500 GP self-statuette.",
            ),
            _set("caster_state.contingency_prepared", "_contingent_prepared"),
        ],
    )


def _source_tools():
    prefix = f"lasting_state.{POOL}.{{source_id}}"
    clear = _tool(
        "Clear only the target marker for this exact retired Contingency.",
        [_entity("target_state"), _param("source_id")],
        [
            _when(
                "target_state.contingency_source_id == source_id",
                [
                    _set("target_state.has_contingency", False),
                ],
            )
        ],
    )
    end = _tool(
        "Retire this exact Contingency without ending the already released chosen effect.",
        [_param("source_id"), _param("reason", required=False, default="ended"), _lasting()],
        [
            _set("_contingency_end_record", None),
            _when(
                f"lasting_state.{POOL} != null",
                [
                    _set("_contingency_end_record", f"lasting_state.{POOL}[source_id]"),
                ],
            ),
            _when(
                "_contingency_end_record != null and _contingency_end_record.installed == true",
                [
                    _set(prefix + ".installed", False),
                    _set(prefix + ".status", "'ended'"),
                    _set(prefix + ".end_reason", "reason"),
                    _call(
                        "clear_srd_contingency_target",
                        {
                            "target_state": "{_contingency_end_record.caster}",
                            "source_id": "{source_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    check = _tool(
        "Qualify the actual saved source, clock and carried material before a trigger.",
        [_entity("target_state"), _lasting()],
        [
            _set("_contingency_current", False),
            _set("_contingency_source_id", None),
            _when(
                f"target_state.contingency_source_id != null and lasting_state.{POOL} != null",
                [
                    _set("_contingency_source_id", "target_state.contingency_source_id"),
                    _set("_contingency_record", f"lasting_state.{POOL}[_contingency_source_id]"),
                    _when(
                        "_contingency_record != null and _contingency_record.installed == true and "
                        "_contingency_record.caster == _target_state_id",
                        [
                            _set("_contingency_now", 0),
                            _when(
                                "lasting_state.lasting_tick != null",
                                [_set("_contingency_now", "lasting_state.lasting_tick")],
                            ),
                            _set(
                                "_contingency_lifetime_current",
                                "_contingency_now < _contingency_record.expires_tick and "
                                "target_state.inventory != null and "
                                "_contingency_record.statuette_item in target_state.inventory",
                            ),
                            _when(
                                "_contingency_lifetime_current != true",
                                [
                                    _call(
                                        "end_srd_contingency_source",
                                        {
                                            "source_id": "{_contingency_source_id}",
                                            "reason": "expired or statuette no longer carried",
                                        },
                                    )
                                ],
                            ),
                            _set(
                                "_contingency_current",
                                "_contingency_lifetime_current == true "
                                "and _contingency_record.suppressed != true",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    install = _tool(
        "Commit the chosen spell's actual setup slot, then retain its deferred effect.",
        [
            _entity("caster_state"),
            _lasting(),
            _param("statuette_item", model_hidden=True),
            _param("hp_threshold", "integer", minimum=0, maximum=100000),
            _param("spell_cast_id", required=False, default="", model_hidden=True),
            _param(
                "spell_cast_owner",
                resolve="entity",
                required=False,
                default=None,
                model_hidden=True,
            ),
            _param(
                "actual_spell_level",
                "integer",
                minimum=-1,
                maximum=9,
                required=False,
                default=-1,
                model_hidden=True,
            ),
        ],
        [
            _set("_contingent_setup", "caster_state.contingency_prepared"),
            _require(
                "spell_cast_id != '' and spell_cast_id == caster_state.last_spell_source_cast_id "
                "and caster_state.last_spell_source_spell == 'contingency' and "
                "_spell_cast_owner_id == _caster_state_id",
                "Contingency setup needs the actual committed parent cast identity.",
            ),
            _require(
                "_contingent_setup != null and _contingent_setup.caster == _caster_state_id and "
                "(_contingent_setup.spell == 'false_life' or "
                "_contingent_setup.spell == 'water_breathing') and "
                "_contingent_setup.slot_level >= 1 and _contingent_setup.slot_level <= 5",
                "Use the validated chosen-spell setup.",
            ),
            _set("_contingent_slot_remaining", 0),
            *[
                _when(
                    f"_contingent_setup.slot_resource == 'spell_slot_{level}'",
                    [
                        _set(
                            "_contingent_slot_remaining",
                            f"caster_state.resources.spell_slot_{level}",
                        ),
                    ],
                )
                for level in range(1, 6)
            ],
            _require(
                "statuette_item in caster_state.inventory and _contingent_slot_remaining >= 1",
                "The actual carried item and chosen spell slot must remain available at setup.",
            ),
            _set("_cast_resource", "_contingent_setup.slot_resource"),
            # This is replaced with the actual cast owner's decrement branch at composition.
            {"decrement": {"field": "caster_state.resources.{_cast_resource}", "amount": 1}},
            _when(
                "caster_state.contingency_source_id != null",
                [
                    _call(
                        "end_srd_contingency_source",
                        {"source_id": "{caster_state.contingency_source_id}", "reason": "recast"},
                    )
                ],
            ),
            _set("_contingency_now", 0),
            _when(
                "lasting_state.lasting_tick != null",
                [_set("_contingency_now", "lasting_state.lasting_tick")],
            ),
            _set("_contingency_source_id", "spell_cast_id"),
            _set(
                f"lasting_state.{POOL}.{{_contingency_source_id}}",
                {
                    "spell": "'contingency'",
                    "cast_id": "spell_cast_id",
                    "source_caster_id": "_caster_state_id",
                    "caster": "_caster_state_id",
                    "spell_level": "actual_spell_level",
                    "installed": True,
                    "ongoing_duration": True,
                    "dispel_ongoing": True,
                    "suppressed": False,
                    "status": "'armed'",
                    "started_tick": "_contingency_now",
                    "expires_tick": "_contingency_now + 288000",
                    "statuette_item": "statuette_item",
                    "trigger": "'hp_damage_threshold'",
                    "hp_threshold": "hp_threshold",
                    "child": "_contingent_setup",
                    "source_sha256": repr(SOURCE_SHA256),
                },
            ),
            _set("caster_state.contingency_source_id", "_contingency_source_id"),
            _set("caster_state.has_contingency", True),
            _set("lasting_state.has_contingency", True),
            _when(
                "lasting_state.contingency_caster_ids == null",
                [_set("lasting_state.contingency_caster_ids", [])],
            ),
            _when(
                "_caster_state_id not in lasting_state.contingency_caster_ids",
                [
                    _set("_contingency_casters", "lasting_state.contingency_caster_ids"),
                    _require(
                        "len(_contingency_casters) < 128",
                        "At most 128 casters may retain Contingency history.",
                    ),
                    {
                        "list_push": {
                            "list": "_contingency_casters",
                            "value": "_caster_state_id",
                        }
                    },
                    _set("lasting_state.contingency_caster_ids", "_contingency_casters"),
                ],
            ),
            _set(
                "_spell_effect_summary",
                "'Contingency armed; both spell slots spent and chosen effect deferred.'",
            ),
            _set("_spell_effect_details", []),
            _set(
                "_spell_summary_rows",
                [
                    {
                        "label": "'Contingency'",
                        "value": "'Armed; both spell slots paid and chosen effect deferred.'",
                    }
                ],
            ),
            _set("_spell_detail_rows", []),
            _set("_contingent_cast_resource", "_contingent_setup.slot_resource"),
            _set("caster_state.contingency_prepared", None),
        ],
    )
    return {
        "clear_srd_contingency_target": clear,
        "end_srd_contingency_source": end,
        "check_srd_contingency_source": check,
        "install_srd_contingency_source": install,
    }


def _release_tools(tools):
    """Retain the original effect owner's dice/choice/lifetime; setup already paid eligibility."""
    false = deepcopy(tools["cast_spell_self_temporary_hit_points"])
    false["pre_conditions"] = [
        p
        for p in false.get("pre_conditions", [])
        if "prepared_spells" not in str(p) and "slot_resource" not in str(p)
    ]
    release = _tool(
        "Consume the first genuine HP-reducing trigger before releasing the chosen spell once.",
        [
            _entity("target_state"),
            _param("hp_before", "integer", minimum=0, model_hidden=True),
            _lasting(),
        ],
        [
            _set("_contingency_released", False),
            _call(
                "check_srd_contingency_source",
                {"target_state": "{_target_state_id}"},
                ("_contingency_current", "_contingency_source_id", "_contingency_record"),
            ),
            _when(
                "_contingency_current == true and hp_before > target_state.hp and "
                "target_state.hp <= _contingency_record.hp_threshold",
                [
                    _set("_contingency_child", "_contingency_record.child"),
                    _set(f"lasting_state.{POOL}.{{_contingency_source_id}}.installed", False),
                    _set(f"lasting_state.{POOL}.{{_contingency_source_id}}.status", "'released'"),
                    _set(
                        f"lasting_state.{POOL}.{{_contingency_source_id}}.trigger_hp_before",
                        "hp_before",
                    ),
                    _set(
                        f"lasting_state.{POOL}.{{_contingency_source_id}}.trigger_hp_after",
                        "target_state.hp",
                    ),
                    _set("_contingency_released", True),
                    _set("_spell_effect_summary", "'The chosen spell has no living self target.'"),
                    _set("_contingent_summary_rows", []),
                    _set("_contingent_detail_rows", []),
                    _set("_contingent_release_roll", None),
                    _when(
                        "target_state.is_alive == true",
                        [
                            _when(
                                "_contingency_child.spell == 'false_life'",
                                [
                                    _call(
                                        "release_srd_contingent_false_life",
                                        {
                                            "spell": "false_life",
                                            "family": "self_temporary_hit_points",
                                            "caster_state": "{_target_state_id}",
                                            "slot_resource": "no_slot",
                                            "slot_level": "{_contingency_child.slot_level}",
                                            "base_level": 1,
                                            "base_dice": 2,
                                            "temporary_hit_point_die": "d4",
                                            "flat_bonus": 4,
                                            "temporary_hit_points_per_slot_level": 5,
                                            "range_kind": "self",
                                            "range_feet": 0,
                                        },
                                        {
                                            "_contingent_summary_rows": "_spell_summary_rows",
                                            "_contingent_detail_rows": "_spell_detail_rows",
                                            "_contingent_release_roll": FALSE_LIFE_ROLL,
                                        },
                                    ),
                                    _set(
                                        "_spell_effect_summary", "'Contingent False Life released.'"
                                    ),
                                ],
                            ),
                            _when(
                                "_contingency_child.spell == 'water_breathing'",
                                [
                                    _call(
                                        "begin_srd_spell_source_instance",
                                        {
                                            "caster_state": "{_target_state_id}",
                                            "spell": "water_breathing",
                                            "slot_level": "{_contingency_child.slot_level}",
                                        },
                                        ("_spell_source_cast_id",),
                                    ),
                                    _set("_contingency_targets", ["_target_state_id"]),
                                    _call(
                                        "cast_spell_timed_senses",
                                        {
                                            "spell": "water_breathing",
                                            "caster_state": "{_target_state_id}",
                                            "target_state": "{_target_state_id}",
                                            "targets": "{_contingency_targets}",
                                            "target_willing": True,
                                            "all_targets_willing": True,
                                            "target_distance": 0,
                                            "targets_max_distance": 0,
                                            "caster_can_touch_target": True,
                                            "caster_can_touch_all_targets": True,
                                            "actual_spell_level": "{_contingency_child.slot_level}",
                                            "spell_cast_id": "{_spell_source_cast_id}",
                                            "spell_cast_owner": "{_target_state_id}",
                                        },
                                        {
                                            "_contingent_summary_rows": "_spell_summary_rows",
                                            "_contingent_detail_rows": "_spell_detail_rows",
                                        },
                                    ),
                                    _set(
                                        "_spell_effect_summary",
                                        "'Contingent Water Breathing released.'",
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set(
                        f"lasting_state.{POOL}.{{_contingency_source_id}}.release_summary",
                        "_spell_effect_summary",
                    ),
                ],
            ),
        ],
    )
    # Water Breathing settles real overdue respiration, which can itself cause Damage.
    # A completed-packet observer therefore queues; only the outer action drains releases.
    event = release["effects"][-1]
    child_effects = [
        effect
        for effect in event["on_true"]
        if effect.get("condition") == "target_state.is_alive == true"
        or effect.get("set", {}).get("field")
        in {
            "_spell_effect_summary",
            "_contingent_summary_rows",
            "_contingent_detail_rows",
            "_contingent_release_roll",
        }
        or effect.get("set", {}).get("field", "").endswith(".release_summary")
    ]
    event["on_true"] = [
        effect
        for effect in event["on_true"]
        if effect not in child_effects
        and effect.get("set", {}).get("field") != "_contingency_child"
    ]
    next(
        effect
        for effect in event["on_true"]
        if effect.get("set", {}).get("field", "").endswith(".status")
    )["set"]["value"] = "'queued'"
    event["on_true"] += [
        _set("target_state.has_contingency", False),
        _when(
            "lasting_state.contingency_release_queue == null",
            [
                _set("lasting_state.contingency_release_queue", []),
            ],
        ),
        _set("_contingency_pending_ids", "lasting_state.contingency_release_queue"),
        {"list_push": {"list": "_contingency_pending_ids", "value": "_contingency_source_id"}},
        _set("lasting_state.contingency_release_queue", "_contingency_pending_ids"),
    ]
    release["description"] = "Consume a genuine completed HP-reduction trigger and queue its child."
    child = _tool(
        "Release one genuinely consumed saved trigger without another slot or Action.",
        [_entity("target_state"), _param("source_id"), _lasting()],
        [
            _set("_contingency_record", f"lasting_state.{POOL}[source_id]"),
            _when(
                "_contingency_record != null and _contingency_record.installed != true and "
                "_contingency_record.status == 'queued' and "
                "_contingency_record.caster == _target_state_id",
                [
                    _set("_contingency_source_id", "source_id"),
                    _set("_contingency_child", "_contingency_record.child"),
                    _set(f"lasting_state.{POOL}.{{_contingency_source_id}}.status", "'released'"),
                    *child_effects,
                    _set(
                        f"lasting_state.{POOL}.{{_contingency_source_id}}.release_detail_rows",
                        "_contingent_detail_rows",
                    ),
                    _set(
                        f"lasting_state.{POOL}.{{_contingency_source_id}}.release_roll",
                        "_contingent_release_roll",
                    ),
                ],
            ),
        ],
    )
    drain = _tool(
        "Drain saved Contingency releases at the genuine outer completed action.",
        [_lasting()],
        [
            _set("_contingency_queue", []),
            _when(
                "lasting_state.contingency_release_queue != null",
                [
                    _set("_contingency_queue", "lasting_state.contingency_release_queue"),
                ],
            ),
            _set("lasting_state.contingency_release_queue", []),
            _each(
                "_contingency_queue",
                "_queued_id",
                [
                    _set("_queued_source", f"lasting_state.{POOL}[_queued_id]"),
                    _when(
                        "_queued_source != null and _queued_source.status == 'queued'",
                        [
                            _call(
                                "release_srd_contingent_spell",
                                {
                                    "source_id": "{_queued_id}",
                                    "target_state": "{_queued_source.caster}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "release_srd_contingent_false_life": false,
        "observe_srd_contingency_damage": release,
        "release_srd_contingent_spell": child,
        "drain_srd_contingency_releases": drain,
    }


def extend_runtime(text):
    """Emit before extended-casting and common Source capture; final damage joins stay explicit."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    cast = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CHOICES if p["name"] not in names]
    changed = {
        "prepare_srd_contingent_child": _prepare_child(cast),
        "prepare_srd_contingency_cast": _prepare_parent(),
        **_source_tools(),
        **_release_tools(tools),
    }
    spends = [
        e
        for e in cast["effects"]
        if e.get("condition")
        == "caster != null and _cast_resource != 'cantrip' and _cast_resource != 'no_slot'"
        and any(
            child.get("decrement", {}).get("field") == "caster_state.resources.{_cast_resource}"
            for child in e.get("on_true", [])
        )
    ]
    if len(spends) != 1:
        raise ValueError("Contingency needs the unique actual spell-slot spending branch.")
    actual_spend = spends[0]
    install = changed["install_srd_contingency_source"]
    spend_index = next(i for i, e in enumerate(install["effects"]) if "decrement" in e)
    install["effects"][spend_index : spend_index + 1] = deepcopy(actual_spend["on_true"])
    cast["effects"] = [
        e
        for e in cast["effects"]
        if "prepare_srd_contingency_cast" not in str(e)
        and "install_srd_contingency_source" not in str(e)
    ]
    material_index = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "resolve_srd_spell_materials"
    )
    cast["effects"].insert(
        material_index,
        _when(
            "spell == 'contingency'",
            [
                _call(
                    "prepare_srd_contingency_cast",
                    {
                        "caster": "{_caster_id}",
                        "caster_state": "{_caster_state_id}",
                        **{p["name"]: "{" + p["name"] + "}" for p in CHOICES},
                        "material_item_ids": "{material_item_ids}",
                        "material_component_available": "{material_component_available}",
                        "verbal_component_available": "{verbal_component_available}",
                        "somatic_component_available": "{somatic_component_available}",
                    },
                    ("_contingent_prepared", "_contingent_statuette_item"),
                )
            ],
        ),
    )
    spend_index = cast["effects"].index(actual_spend)
    cast["effects"].insert(
        spend_index,
        _when(
            "_spell_definition.family == 'contingency_spell' and "
            "_spell_cast_failed != true and _pending_spell_operation_lost != true",
            [
                _call(
                    "install_srd_contingency_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "statuette_item": "{_contingent_statuette_item}",
                        "hp_threshold": "{contingent_hp_threshold}",
                        "spell_cast_id": "{_spell_source_cast_id}",
                        "spell_cast_owner": "{_caster_state_id}",
                        "actual_spell_level": "{_slot_level}",
                    },
                    (
                        "_spell_effect_summary",
                        "_spell_effect_details",
                        "_spell_summary_rows",
                        "_spell_detail_rows",
                        "_contingency_source_id",
                        "_contingent_cast_resource",
                    ),
                )
            ],
        ),
    )
    changed["cast_spell"] = cast
    clock = deepcopy(tools["advance_srd_lasting_time"])
    clock["effects"] = [e for e in clock["effects"] if "check_srd_contingency_source" not in str(e)]
    clock["effects"].append(
        _when(
            "state.has_contingency == true",
            [
                _set("_contingency_actors", "state.contingency_caster_ids"),
                _each(
                    "_contingency_actors",
                    "_actor",
                    [_call("check_srd_contingency_source", {"target_state": "{_actor}"})],
                ),
            ],
        )
    )
    # Source-specific damage, inventory-loss and common retirement binders are Root joins.
    changed["advance_srd_lasting_time"] = clock
    text = _emit(text, changed)
    row = {
        "type": "object",
        "properties": {
            k: {
                "type": "boolean"
                if isinstance(v, bool)
                else "integer"
                if isinstance(v, int)
                else "string",
                "enum": [v],
            }
            for k, v in PROFILE.items()
            if k != "family"
        },
        "required": [k for k in PROFILE if k != "family"],
        "additionalProperties": False,
    }
    variants = _load_runtime(text)["definition_row_schemas"]["definitions.spells"]["variants"]
    if FAMILY in variants:
        if variants[FAMILY] != row:
            raise ValueError("Contingency schema differs from the pinned source row.")
        return text
    fragment = yaml.safe_dump({FAMILY: row}, sort_keys=False)
    fragment = "".join(
        "      " + line if line.strip() else line for line in fragment.splitlines(True)
    )
    anchor = re.search(rf"(?m)^      {re.escape(next(iter(variants)))}:", text)
    if not anchor:
        raise ValueError("Contingency needs the actual spell definition schema anchor.")
    return text[: anchor.start()] + fragment + text[anchor.start() :]
