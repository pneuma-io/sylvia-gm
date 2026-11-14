"""Source-owned life/holy auras and worn-ring bonds use the actual shared scene."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import _each, _push
    from .srd_spell_barriers import _effect_lists, _scene, _tool
    from .srd_timed_actor_defenses import (
        _call as _base_call,
    )
    from .srd_timed_actor_defenses import (
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
    )
    from .srd_timed_actor_defenses import (
        _when as _base_when,
    )
    from .srd_timed_senses import _optional, _public, _replace_tools
else:
    from srd_control_behaviors import _each, _push
    from srd_spell_barriers import _effect_lists, _scene, _tool
    from srd_timed_actor_defenses import (
        _call as _base_call,
    )
    from srd_timed_actor_defenses import (
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
    )
    from srd_timed_actor_defenses import (
        _when as _base_when,
    )
    from srd_timed_senses import _optional, _public, _replace_tools


PROFILES = {
    "aura_of_life": {
        "family": "actor_spell_ward",
        "base_level": 4,
        "casting_time": "action",
        "range_kind": "self",
        "range_feet": 0,
        "duration_rounds": 100,
        "concentration": True,
        "ward_mode": "life",
        "radius_feet": 30,
    },
    "holy_aura": {
        "family": "actor_spell_ward",
        "base_level": 8,
        "casting_time": "action",
        "range_kind": "self",
        "range_feet": 0,
        "duration_rounds": 10,
        "concentration": True,
        "ward_mode": "holy",
        "radius_feet": 30,
    },
    "warding_bond": {
        "family": "actor_spell_ward",
        "base_level": 2,
        "casting_time": "action",
        "range_kind": "touch",
        "range_feet": 0,
        "duration_rounds": 600,
        "concentration": False,
        "ward_mode": "bond",
        "radius_feet": 60,
    },
}
PUBLIC_TOOLS = {
    "wear_srd_warding_bond_ring",
    "remove_srd_warding_bond_ring",
    "choose_srd_holy_aura_creatures",
}
WARD_EXPORTS = (
    "_ward_life_effective",
    "_ward_holy_effective",
    "_ward_bond_effective",
    "_warding_bond_source_id",
    "_warding_bond_caster_state_id",
    "_ward_holy_source_ids",
)


def _call(name, params, exports=()):
    """Retain actual owning exports while allowing the caller's distinct result name."""
    call = _base_call(name, params, exports)
    if isinstance(exports, dict):
        call["export_context"] = exports
    return call


def _when(condition, on_true, on_false=None):
    """Keep optional alternatives explicit in the owning runtime branch."""
    effect = _base_when(condition, on_true)
    if on_false is not None:
        effect["on_false"] = on_false
    return effect


def validate_profiles(definitions):
    """Only the three reviewed source profiles may enter this owner."""
    for spell, definition in definitions.items():
        if definition.get("family") == "actor_spell_ward" and definition != PROFILES.get(spell):
            raise SystemExit(f"Spell ward profile differs from its source: {spell}")


def _schema():
    properties = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in PROFILES["holy_aura"].items()
        if k != "family"
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _wards(effects):
    return [
        _when(
            "state.has_spell_wards == true",
            [
                _set("_ward_ids", "state.spell_ward_source_ids"),
                _each(
                    "_ward_ids",
                    "_ward_id",
                    [
                        _set("_ward_record", "state.spell_ward_sources[_ward_id]"),
                        _when("_ward_record.installed == true", effects),
                    ],
                ),
            ],
        )
    ]


def _ring_tools():
    """Wear a unique real stock item; quantities and price remain material-owner facts."""
    read = _tool(
        "Validate this actor's actual one-copy platinum ring and retained Material stock.",
        [_entity("actor_state"), _parameter("material_id")],
        [
            _require(
                "actor_state.inventory != null and actor_state.spell_materials != null",
                "A platinum ring needs the actor's actual inventory and material stock.",
            ),
            _set("_ring_material", None),
            _set("_ring_stock", "actor_state.spell_materials"),
            _each(
                "_ring_stock",
                "_ring_lot",
                [
                    _when(
                        "_ring_lot.id == material_id",
                        [
                            _require(
                                "_ring_material == null", "The material stock ID is duplicated."
                            ),
                            _set("_ring_material", "_ring_lot"),
                        ],
                    ),
                ],
            ),
            _require(
                "_ring_material != null and _ring_material.kind == 'specific' and "
                "'warding_bond.1' in _ring_material.components and "
                "_ring_material.quantity == 1 and _ring_material.value_cp >= 5000",
                "Wear one source-qualified platinum ring worth at least 50 GP.",
            ),
            _set("_ring_count", 0),
            _set("_ring_inventory", "actor_state.inventory"),
            _each(
                "_ring_inventory",
                "_ring_item",
                [
                    _when(
                        "_ring_item == _ring_material.inventory_item",
                        [
                            _set("_ring_count", "_ring_count + 1"),
                        ],
                    ),
                ],
            ),
            _require("_ring_count == 1", "The ring must identify one actual carried item."),
        ],
    )
    worn = _tool(
        "Check the actual retained worn ring; melded or released items grant no ring benefit.",
        [_entity("actor_state"), _parameter("material_id"), _parameter("inventory_item")],
        [
            _set("_ward_ring_worn", False),
            _when(
                "actor_state.worn_warding_bond_ring != null and "
                "actor_state.worn_warding_bond_ring.material_id == material_id and "
                "actor_state.worn_warding_bond_ring.inventory_item == inventory_item and "
                "actor_state.inventory != null and inventory_item in actor_state.inventory "
                "and actor_state.form_equipment_mode != 'melded' "
                "and actor_state.form_equipment_melded != true "
                "and actor_state.form_no_objects != true",
                [
                    _call(
                        "read_srd_warding_bond_ring",
                        {
                            "actor_state": "{_actor_state_id}",
                            "material_id": "{material_id}",
                        },
                        ("_ring_material",),
                    ),
                    _set("_ward_ring_worn", "_ring_material.inventory_item == inventory_item"),
                ],
            ),
        ],
    )
    wear = _public(
        _tool(
            "Record actually wearing one carried source-qualified platinum ring.",
            [_entity("actor_state"), _parameter("material_id")],
            [
                _require(
                    "actor_state.form_equipment_mode != 'melded' and "
                    "actor_state.form_equipment_melded != true and "
                    "actor_state.form_no_objects != true",
                    "A melded ring cannot be worn or used.",
                ),
                _call(
                    "read_srd_warding_bond_ring",
                    {
                        "actor_state": "{_actor_state_id}",
                        "material_id": "{material_id}",
                    },
                    ("_ring_material",),
                ),
                _set(
                    "actor_state.worn_warding_bond_ring",
                    {
                        "material_id": "material_id",
                        "inventory_item": "_ring_material.inventory_item",
                    },
                ),
                _set("_worn_ring_name", "_ring_material.inventory_item"),
            ],
        ),
        "spell",
        "Worn platinum ring",
        [("Ring", "_worn_ring_name")],
    )
    remove = _public(
        _tool(
            "Remove the actual worn platinum ring; retained bonds no longer grant its benefits.",
            [_entity("actor_state")],
            [
                _require(
                    "actor_state.form_equipment_melded != true and "
                    "actor_state.form_no_objects != true",
                    "This body's inaccessible or unusable equipment cannot be removed.",
                ),
                _require(
                    "actor_state.worn_warding_bond_ring != null", "There is no worn platinum ring."
                ),
                _set("actor_state.worn_warding_bond_ring", None),
                _set("_worn_ring_removed", True),
            ],
        ),
        "spell",
        "Platinum ring removed",
        [("Removed", "_worn_ring_removed")],
    )
    return {
        "read_srd_warding_bond_ring": read,
        "check_srd_warding_bond_ring_worn": worn,
        "wear_srd_warding_bond_ring": wear,
        "remove_srd_warding_bond_ring": remove,
    }


def _source_tools():
    """Retire exactly one cast; suppression and missing wear do not change elapsed time."""
    retire = _tool(
        "Retire one actual ward source without recursively ending concentration.",
        [_parameter("source_id"), _optional("reason", default="removed")],
        [
            _set("_ending_ward", "state.spell_ward_sources[source_id]"),
            _require("_ending_ward != null", "Select an actual saved ward source."),
            _when(
                "_ending_ward.installed == true",
                [
                    _set("state.spell_ward_sources.{source_id}.installed", False),
                    _set("state.spell_ward_sources.{source_id}.ended_reason", "reason"),
                    _set("state.spell_ward_sources.{source_id}.ended_tick", "state.lasting_tick"),
                ],
            ),
        ],
    )
    finish = _tool(
        "End only this ward's exact caster concentration.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _when(
                "caster_state.concentration_effect_family == 'actor_spell_ward' and "
                "caster_state.ward_source_id == source_id",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{caster_state.concentration_target_state_id}",
                        },
                    ),
                ],
            )
        ],
    )
    end = _tool(
        "End one selected ward and only its own concentration.",
        [_parameter("source_id"), _optional("reason", default="removed")],
        [
            _set("_removed_ward", "state.spell_ward_sources[source_id]"),
            _require("_removed_ward != null", "Select an actual saved ward source."),
            _when(
                "_removed_ward.installed == true",
                [
                    _call(
                        "retire_srd_spell_ward_record",
                        {"source_id": "{source_id}", "reason": "{reason}"},
                    ),
                    _when(
                        "_removed_ward.concentration == true",
                        [
                            _call(
                                "finish_srd_spell_ward_concentration",
                                {
                                    "caster_state": "{_removed_ward.caster_state_id}",
                                    "source_id": "{source_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    check = _tool(
        "Check real expiry, concentration or the Bond's caster HP and spatial separation.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_checked_ward", "state.spell_ward_sources[source_id]"),
            _set(
                "_ward_source_active",
                "_checked_ward.installed == true and "
                "state.lasting_tick < _checked_ward.expires_tick",
            ),
            _when(
                "_checked_ward.concentration == true",
                [
                    _set(
                        "_ward_source_active",
                        "_ward_source_active == true and "
                        "caster_state.is_alive == true and caster_state.hp > 0 and "
                        "caster_state.concentration_effect_family == 'actor_spell_ward' and "
                        "caster_state.ward_source_id == source_id",
                    ),
                ],
            ),
            _when(
                "_ward_source_active == true and _checked_ward.mode == 'bond'",
                [
                    *_scene("_caster_state_id", "_bond_caster_scene"),
                    *_scene("_checked_ward.target_state_id", "_bond_target_scene"),
                    _call(
                        "measure_srd_information_distance",
                        {
                            "start": "{_bond_caster_scene.position}",
                            "end": "{_bond_target_scene.position}",
                        },
                        ("_scene_distance",),
                    ),
                    _set(
                        "_ward_source_active",
                        "caster_state.hp > 0 and "
                        "_bond_caster_scene.metadata.plane == "
                        "_bond_target_scene.metadata.plane and "
                        "_scene_distance <= 60",
                    ),
                ],
            ),
            _when(
                "_ward_source_active != true and _checked_ward.installed == true",
                [
                    _call(
                        "end_srd_spell_ward_source",
                        {"source_id": "{source_id}", "reason": "lifetime"},
                    ),
                ],
            ),
        ],
    )
    refresh = _tool(
        "Refresh actual ward sources without touching any unrelated spell effects.",
        [],
        [
            _set("_has_live_wards", False),
            *_wards(
                [
                    _call(
                        "check_srd_spell_ward_source",
                        {
                            "caster_state": "{_ward_record.caster_state_id}",
                            "source_id": "{_ward_id}",
                        },
                        ("_ward_source_active",),
                    ),
                    _when("_ward_source_active == true", [_set("_has_live_wards", True)]),
                ]
            ),
            _set("state.has_spell_wards", "_has_live_wards"),
        ],
    )
    return {
        "retire_srd_spell_ward_record": retire,
        "finish_srd_spell_ward_concentration": finish,
        "end_srd_spell_ward_source": end,
        "check_srd_spell_ward_source": check,
        "refresh_srd_spell_wards": refresh,
    }


def _query_tools():
    """Derive benefits at the actual recipient boundary, never persist a stale AC or resistance."""
    caster_ref = "{_ward_record.caster_state_id}"
    position_ref = "{_ward_target_scene.position}"
    relation = _tool(
        "Qualify a caster's real current ally list or the saved Holy Aura selection.",
        [_entity("caster_state"), _entity("target_state"), _parameter("source_id")],
        [
            _set("_candidate_ward", "state.spell_ward_sources[source_id]"),
            _set("_ward_recipient_eligible", False),
            _when(
                "_candidate_ward.mode == 'life'",
                [
                    _set("_ward_recipient_eligible", "_caster_state_id == _target_state_id"),
                    _when(
                        "caster_state.ally_state_ids != null and "
                        "_target_state_id in caster_state.ally_state_ids",
                        [
                            _set("_ward_recipient_eligible", True),
                        ],
                    ),
                ],
            ),
            _when(
                "_candidate_ward.mode == 'holy'",
                [
                    _set(
                        "_ward_recipient_eligible",
                        "_target_state_id in _candidate_ward.chosen_state_ids",
                    ),
                ],
            ),
            _when(
                "_candidate_ward.mode == 'bond' and "
                "_candidate_ward.target_state_id == _target_state_id",
                [
                    _call(
                        "check_srd_warding_bond_ring_worn",
                        {
                            "actor_state": "{_caster_state_id}",
                            "material_id": "{_candidate_ward.caster_ring.material_id}",
                            "inventory_item": "{_candidate_ward.caster_ring.inventory_item}",
                        },
                        {"_bond_caster_wearing": "_ward_ring_worn"},
                    ),
                    _call(
                        "check_srd_warding_bond_ring_worn",
                        {
                            "actor_state": "{_target_state_id}",
                            "material_id": "{_candidate_ward.target_ring.material_id}",
                            "inventory_item": "{_candidate_ward.target_ring.inventory_item}",
                        },
                        {"_bond_target_wearing": "_ward_ring_worn"},
                    ),
                    _set(
                        "_ward_recipient_eligible",
                        "_bond_caster_wearing == true and _bond_target_wearing == true",
                    ),
                ],
            ),
        ],
    )
    query = _tool(
        "Read effective real aura/Bond benefits at this saved actor's current body and plane.",
        [_entity("target_state")],
        [
            _set("_ward_life_effective", False),
            _set("_ward_holy_effective", False),
            _set("_ward_bond_effective", False),
            _set("_warding_bond_source_id", None),
            _set("_warding_bond_caster_state_id", None),
            _set("_ward_holy_source_ids", []),
            _when(
                "state.has_spell_wards == true",
                [
                    _call("refresh_srd_spell_wards", {}),
                    *_scene("_target_state_id", "_ward_target_scene"),
                    *_wards(
                        [
                            _when(
                                "_ward_record.suppressed != true",
                                [
                                    _call(
                                        "qualify_srd_spell_ward_recipient",
                                        {
                                            "caster_state": "{_ward_record.caster_state_id}",
                                            "target_state": "{_target_state_id}",
                                            "source_id": "{_ward_id}",
                                        },
                                        ("_ward_recipient_eligible",),
                                    ),
                                    _set("_ward_in_area", False),
                                    _when(
                                        "_ward_recipient_eligible == true and "
                                        "_ward_record.mode == 'bond'",
                                        [
                                            _set("_ward_in_area", True),
                                        ],
                                    ),
                                    _when(
                                        "_ward_recipient_eligible == true and "
                                        "_ward_record.mode != 'bond'",
                                        [
                                            *_scene(
                                                "_ward_record.caster_state_id", "_ward_caster_scene"
                                            ),
                                            _when(
                                                "_ward_caster_scene.metadata.plane == "
                                                "_ward_target_scene.metadata.plane",
                                                [
                                                    _call(
                                                        "resolve_srd_environment_body_segment",
                                                        {
                                                            "moving_subject": "{_target_state_id}",
                                                            "fixed_subject": caster_ref,
                                                            "start": position_ref,
                                                            "end": "{_ward_target_scene.position}",
                                                            "radius": 30,
                                                        },
                                                        {"_ward_in_area": "_body_hit"},
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_ward_in_area == true",
                                        [
                                            _when(
                                                "_ward_record.mode == 'life'",
                                                [
                                                    _set("_ward_life_effective", True),
                                                ],
                                            ),
                                            _when(
                                                "_ward_record.mode == 'holy'",
                                                [
                                                    _set("_ward_holy_effective", True),
                                                    _push("_ward_holy_source_ids", "_ward_id"),
                                                ],
                                            ),
                                            _when(
                                                "_ward_record.mode == 'bond'",
                                                [
                                                    _set("_ward_bond_effective", True),
                                                    _set("_warding_bond_source_id", "_ward_id"),
                                                    _set(
                                                        "_warding_bond_caster_state_id",
                                                        "_ward_record.caster_state_id",
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ]
                    ),
                ],
            ),
        ],
    )
    bond = _tool(
        "Read the exact active recipient's damage reflection destination, with no numeric verdict.",
        [_entity("target_state")],
        [
            _call(
                "resolve_srd_spell_ward_benefits",
                {"target_state": "{_target_state_id}"},
                WARD_EXPORTS,
            )
        ],
    )
    return {
        "qualify_srd_spell_ward_recipient": relation,
        "resolve_srd_spell_ward_benefits": query,
        "check_srd_warding_bond_damage": bond,
    }


def _bond_material_tool():
    """The printed pair is split across two real wearers, rather than two caster stock units."""
    return _tool(
        "Qualify both actually worn, uniquely identified 50+ GP platinum rings.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _parameter("item_ids", "array", items={"type": "string"}),
            _parameter("materials_available", "boolean"),
        ],
        [
            _require(
                "_caster_state_id != _target_state_id", "Warding Bond touches another creature."
            ),
            _require(
                "materials_available == true", "The actual required pair of rings must be worn."
            ),
            _require(
                "caster_state.worn_warding_bond_ring != null and "
                "target_state.worn_warding_bond_ring != null",
                "Both connected creatures must actually wear their platinum rings.",
            ),
            _require(
                "caster_state.worn_warding_bond_ring.material_id in item_ids",
                "Select the caster's actual worn ring in the Material inputs.",
            ),
            _require(
                "caster_state.worn_warding_bond_ring.inventory_item != "
                "target_state.worn_warding_bond_ring.inventory_item",
                "The two rings need distinct actual inventory identities.",
            ),
            *[
                _call(
                    "check_srd_warding_bond_ring_worn",
                    {
                        "actor_state": "{"
                        + ("_caster_state_id" if who == "caster" else "_target_state_id")
                        + "}",
                        "material_id": "{" + who + "_state.worn_warding_bond_ring.material_id}",
                        "inventory_item": "{"
                        + who
                        + "_state.worn_warding_bond_ring.inventory_item}",
                    },
                    {"_bond_" + who + "_wearing": "_ward_ring_worn"},
                )
                for who in ("caster", "target")
            ],
            _require(
                "_bond_caster_wearing == true and _bond_target_wearing == true",
                "Both required rings must remain actually worn and carried.",
            ),
            _set(
                "_material_rows",
                [
                    {
                        "label": "'Material worn (retained)'",
                        "value": "caster_state.worn_warding_bond_ring.inventory_item",
                    },
                    {
                        "label": "'Material worn (retained)'",
                        "value": "target_state.worn_warding_bond_ring.inventory_item",
                    },
                ],
            ),
        ],
    )


def _holy_choices():
    """Select real creatures while preserving the aura's fixed source, clock and DC."""
    return [
        _set("_ward_chosen", []),
        _each(
            "chosen_state_ids",
            "_ward_chosen_id",
            [
                *_scene("_ward_chosen_id", "_ward_chosen_scene"),
                _require(
                    "_ward_chosen_scene.metadata.participant_ref != null",
                    "Holy Aura selects actual creatures, not objects.",
                ),
                _require(
                    "_ward_chosen_scene.metadata.participant_ref not in _ward_chosen",
                    "Choose each actual Holy Aura creature once.",
                ),
                _push("_ward_chosen", "_ward_chosen_scene.metadata.participant_ref"),
            ],
        ),
    ]


def _choose_tool():
    return _public(
        _tool(
            "Choose the actual creatures protected by your continuing Holy Aura.",
            [
                _entity("caster_state"),
                _parameter("source_id"),
                _parameter("chosen_state_ids", "array", items={"type": "string"}, max_items=64),
            ],
            [
                _set("_choice_source", "state.spell_ward_sources[source_id]"),
                _require(
                    "_choice_source != null and _choice_source.installed == true and "
                    "_choice_source.mode == 'holy' and "
                    "_choice_source.caster_state_id == _caster_state_id",
                    "Select your actual continuing Holy Aura source.",
                ),
                _call(
                    "check_srd_spell_ward_source",
                    {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                    ("_ward_source_active",),
                ),
                _require("_ward_source_active == true", "This Holy Aura has ended."),
                *_holy_choices(),
                _set("state.spell_ward_sources.{source_id}.chosen_state_ids", "_ward_chosen"),
                _set("_holy_chosen_count", "len(_ward_chosen)"),
            ],
        ),
        "spell",
        "Holy Aura creatures chosen",
        [("Chosen creatures", "_holy_chosen_count")],
    )


def _cast_tool():
    """Install the reviewed ward after actual cast costs, spatial identity and Material rules."""
    return _tool(
        "Install one source-owned aura or actual worn-ring connection.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("concentration_save_modifier", "integer"),
            _optional("save_dc", "integer", 0),
            _optional("target_state", default=None, resolve="entity"),
            _optional("target_willing", "boolean", False),
            _optional("caster_can_touch_target", "boolean", False),
            _optional("chosen_state_ids", "array", [], items={"type": "string"}, max_items=64),
            _optional("spell_cast_id", default="", model_hidden=True),
        ],
        [
            {
                "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
                "store_as": "_ward_profile",
            },
            _require(
                "_ward_profile.family == 'actor_spell_ward' and "
                "slot_level >= _ward_profile.base_level and slot_level <= 9",
                "Use the source-defined ward and actual casting slot.",
            ),
            *_scene("_caster_state_id", "_ward_origin"),
            _require(
                "_ward_origin.footprint.kind == 'box'",
                "These auras currently require the caster's actual admitted box body.",
            ),
            _set("_ward_target_id", None),
            _set("_ward_caster_ring", None),
            _set("_ward_target_ring", None),
            _set("_ward_chosen", []),
            _when(
                "_ward_profile.ward_mode == 'holy'",
                [
                    _require(
                        "save_dc >= 1 and save_dc <= 40", "Use the caster's actual Holy Aura DC."
                    ),
                    *_holy_choices(),
                ],
            ),
            _when(
                "_ward_profile.ward_mode == 'bond'",
                [
                    _require(
                        "target_state != null and _target_state_id != _caster_state_id and "
                        "target_willing == true and caster_can_touch_target == true",
                        "Touch another actual willing creature for Warding Bond.",
                    ),
                    *_scene("_target_state_id", "_ward_bond_target"),
                    _require(
                        "_ward_bond_target.metadata.plane == _ward_origin.metadata.plane",
                        "The actual touched creature must be on the caster's plane.",
                    ),
                    _call(
                        "measure_srd_information_distance",
                        {
                            "start": "{_ward_origin.position}",
                            "end": "{_ward_bond_target.position}",
                        },
                        ("_scene_distance",),
                    ),
                    _require(
                        "_scene_distance <= 5", "The actual Warding Bond target is outside touch."
                    ),
                    _require(
                        "caster_state.worn_warding_bond_ring != null and "
                        "target_state.worn_warding_bond_ring != null",
                        "Both connected creatures must actually wear platinum rings.",
                    ),
                    _require(
                        "caster_state.worn_warding_bond_ring.inventory_item != "
                        "target_state.worn_warding_bond_ring.inventory_item",
                        "The two rings need distinct actual inventory identities.",
                    ),
                    _call(
                        "check_srd_warding_bond_ring_worn",
                        {
                            "actor_state": "{_caster_state_id}",
                            "material_id": "{caster_state.worn_warding_bond_ring.material_id}",
                            "inventory_item": (
                                "{caster_state.worn_warding_bond_ring.inventory_item}"
                            ),
                        },
                        {"_bond_caster_wearing": "_ward_ring_worn"},
                    ),
                    _call(
                        "check_srd_warding_bond_ring_worn",
                        {
                            "actor_state": "{_target_state_id}",
                            "material_id": "{target_state.worn_warding_bond_ring.material_id}",
                            "inventory_item": (
                                "{target_state.worn_warding_bond_ring.inventory_item}"
                            ),
                        },
                        {"_bond_target_wearing": "_ward_ring_worn"},
                    ),
                    _require(
                        "_bond_caster_wearing == true and _bond_target_wearing == true",
                        "The actual two worn rings are required for the connection.",
                    ),
                    _set("_ward_target_id", "_target_state_id"),
                    _set("_ward_caster_ring", "caster_state.worn_warding_bond_ring"),
                    _set("_ward_target_ring", "target_state.worn_warding_bond_ring"),
                ],
            ),
            _when("state.has_spell_wards == true", [_call("refresh_srd_spell_wards", {})]),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
            _when(
                "state.spell_ward_sources == null",
                [
                    _set("state.spell_ward_sources", {}),
                    _set("state.spell_ward_source_ids", []),
                ],
            ),
            _when(
                "_ward_profile.ward_mode == 'bond'",
                _wards(
                    [
                        _when(
                            "_ward_record.mode == 'bond' and "
                            "(_ward_record.caster_state_id == _ward_target_id or "
                            "_ward_record.target_state_id == _ward_target_id)",
                            [
                                _call(
                                    "end_srd_spell_ward_source",
                                    {"source_id": "{_ward_id}", "reason": "recast"},
                                ),
                            ],
                        ),
                    ]
                ),
            ),
            _when(
                "_ward_profile.concentration == true and "
                "caster_state.concentration_spell_id != null",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{caster_state.concentration_target_state_id}",
                        },
                    ),
                ],
            ),
            {"increment": {"field": "state.lasting_serial", "amount": 1}},
            _call(
                "format_srd_information_projection",
                {
                    "message": "{_caster_state_id}:{spell}:{state.lasting_serial}",
                },
                ("_info_projection_message",),
            ),
            _set("_new_ward_id", "_info_projection_message"),
            _set(
                "state.spell_ward_sources.{_new_ward_id}",
                {
                    "installed": True,
                    "spell_id": "spell",
                    "spell_level": "slot_level",
                    "mode": "_ward_profile.ward_mode",
                    "caster_state_id": "_caster_state_id",
                    "target_state_id": "_ward_target_id",
                    "chosen_state_ids": "_ward_chosen",
                    "started_tick": "state.lasting_tick",
                    "serial": "state.lasting_serial",
                    "expires_tick": "state.lasting_tick + _ward_profile.duration_rounds * 2",
                    "concentration": "_ward_profile.concentration",
                    "save_dc": "save_dc",
                    "caster_ring": "_ward_caster_ring",
                    "target_ring": "_ward_target_ring",
                    "cast_id": "spell_cast_id",
                    "source_caster_id": "_caster_state_id",
                    "ongoing_duration": "spell_cast_id != ''",
                    "dispel_ongoing": True,
                },
            ),
            _set("_retained_ward_ids", []),
            _set("_previous_ward_ids", "state.spell_ward_source_ids"),
            _each(
                "_previous_ward_ids",
                "_previous_ward_id",
                [
                    _when(
                        "state.spell_ward_sources[_previous_ward_id].installed == true",
                        [
                            _push("_retained_ward_ids", "_previous_ward_id"),
                        ],
                    ),
                ],
            ),
            _require("len(_retained_ward_ids) < 128", "At most 128 actual live ward casts."),
            _push("_retained_ward_ids", "_new_ward_id"),
            _set("state.spell_ward_source_ids", "_retained_ward_ids"),
            _set("state.has_spell_wards", True),
            _when(
                "_ward_profile.concentration == true",
                [
                    _set("caster_state.concentration_spell_id", "spell"),
                    _set("caster_state.concentration_effect_family", "'actor_spell_ward'"),
                    _set("caster_state.concentration_target_state_id", "_caster_state_id"),
                    _set("caster_state.concentration_target_state_ids", []),
                    _set("caster_state.concentration_save_modifier", "concentration_save_modifier"),
                    _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                    _set(
                        "caster_state.concentration_expires_tick",
                        "state.lasting_tick + _ward_profile.duration_rounds * 2",
                    ),
                    _set("caster_state.ward_source_id", "_new_ward_id"),
                ],
            ),
            _set("_spell_summary_rows", [{"label": "'Ward'", "value": "spell"}]),
            _set("_spell_detail_rows", [{"label": "'Source'", "value": "_new_ward_id"}]),
        ],
    )


def _consequence_tools():
    """Use real turn/healing and condition owners for the printed later consequences."""
    holy_caster = "{_holy_hit_source.caster_state_id}"
    holy_level = "{_holy_hit_source.spell_level}"
    con_save = "{attacker_state.saving_throw_modifiers.constitution}"
    start = _tool(
        "Restore one actual HP when a living zero-HP ally starts its turn in Aura of Life.",
        [_entity("target_state")],
        [
            _set("_aura_life_healed", 0),
            _when(
                "target_state.is_alive == true and target_state.hp == 0",
                [
                    _call(
                        "resolve_srd_spell_ward_benefits",
                        {"target_state": "{_target_state_id}"},
                        WARD_EXPORTS,
                    ),
                    _when(
                        "_ward_life_effective == true",
                        [
                            _call(
                                "restore_hit_points",
                                {"target": "{_target_state_id}", "amount": 1},
                            ),
                            _set("_aura_life_healed", "target_state.hp"),
                        ],
                    ),
                ],
            ),
        ],
    )
    hit = _tool(
        "Resolve the actual Fiend/Undead melee hit against its retained protected recipient.",
        [
            _entity("attacker_state"),
            _parameter("attack_kind"),
            _parameter("attack_outcome"),
            _parameter(
                "protected_source_ids",
                "array",
                items={"type": "string"},
                max_items=128,
                model_hidden=True,
            ),
        ],
        [
            _set("_holy_aura_save", None),
            _set("_holy_aura_blinded", False),
            _set("_holy_hit_source", None),
            _when(
                "attack_kind == 'melee' and (attack_outcome == 'hit' or "
                "attack_outcome == 'critical_hit') and len(protected_source_ids) > 0",
                [
                    _require(
                        "attacker_state.creature_type != null",
                        "Holy Aura's hit consequence needs the attacker's actual creature type.",
                    ),
                    _when(
                        "attacker_state.creature_type == 'fiend' or "
                        "attacker_state.creature_type == 'undead'",
                        [
                            _each(
                                "protected_source_ids",
                                "_holy_source_id",
                                [
                                    _set(
                                        "_holy_candidate",
                                        "state.spell_ward_sources[_holy_source_id]",
                                    ),
                                    _require(
                                        "_holy_candidate != null and "
                                        "_holy_candidate.mode == 'holy'",
                                        "Use the actual protected hit's retained Holy Aura source.",
                                    ),
                                    _when(
                                        "_holy_hit_source == null or "
                                        "_holy_candidate.save_dc > _holy_hit_source.save_dc or "
                                        "(_holy_candidate.save_dc == _holy_hit_source.save_dc and "
                                        "_holy_candidate.serial > _holy_hit_source.serial)",
                                        [
                                            _set("_holy_hit_source", "_holy_candidate"),
                                        ],
                                    ),
                                ],
                            ),
                            _call(
                                "check_srd_target_condition_immunity",
                                {
                                    "target_state": "{_attacker_state_id}",
                                    "condition": "blinded",
                                    "source_state_id": "{_holy_hit_source.caster_state_id}",
                                },
                                ("_source_condition_immune",),
                            ),
                            _when(
                                "_source_condition_immune != true",
                                [
                                    _require(
                                        "attacker_state.saving_throw_modifiers."
                                        "constitution != null",
                                        "Use the attacker's actual Constitution save.",
                                    ),
                                    _call(
                                        "d20_test",
                                        {
                                            "actor_state": "{_attacker_state_id}",
                                            "kind": "saving_throw",
                                            "ability": "constitution",
                                            "ability_modifier": con_save,
                                            "proficiency_bonus": 0,
                                            "proficiency_factor": "none",
                                            "circumstantial_modifier": 0,
                                            "target_number": "{_holy_hit_source.save_dc}",
                                            "advantage": False,
                                            "disadvantage": False,
                                        },
                                        ("_d20_test", "_adjusted_total", "_outcome"),
                                    ),
                                    _set(
                                        "_holy_aura_save",
                                        {
                                            "roll": "_d20_test",
                                            "total": "_adjusted_total",
                                            "dc": "_holy_hit_source.save_dc",
                                            "outcome": "_outcome",
                                        },
                                    ),
                                    _when(
                                        "_outcome == 'failure'",
                                        [
                                            _call(
                                                "install_srd_turn_effect",
                                                {
                                                    "caster_state": holy_caster,
                                                    "target_state": "{_attacker_state_id}",
                                                    "lasting_state": "state",
                                                    "spell": "holy_aura",
                                                    "effect_label": "Holy Aura: Blinded",
                                                    "kind": "blinded",
                                                    "expiry_owner": "target",
                                                    "expiry_phase": "end",
                                                    "magnitude": 0,
                                                    "dice_count": 0,
                                                    "damage_die": "d4",
                                                    "damage_type": "radiant",
                                                    "spell_cast_id": "{_holy_hit_source.cast_id}",
                                                    "spell_cast_owner": holy_caster,
                                                    "actual_spell_level": holy_level,
                                                },
                                            ),
                                            _set(
                                                "_holy_aura_blinded",
                                                "'blinded' in attacker_state.conditions",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {"start_srd_aura_of_life_turn": start, "resolve_srd_holy_aura_hit": hit}


def _compose_materials(tools, changed):
    """Keep the generic material call anchor and change only the two-wearer source rule."""
    material = deepcopy(tools["resolve_srd_spell_materials"])
    if not any(p["name"] == "bond_target" for p in material["parameters"]):
        material["parameters"].append(_optional("bond_target", default=None, model_hidden=True))
    if "resolve_srd_warding_bond_materials" not in str(material["effects"]):
        material["effects"] = [
            _when(
                "spell == 'warding_bond'",
                [
                    _call(
                        "resolve_srd_warding_bond_materials",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{bond_target}",
                            "item_ids": "{item_ids}",
                            "materials_available": "{materials_available}",
                        },
                        ("_material_rows",),
                    ),
                ],
                material["effects"],
            ),
        ]
    changed["resolve_srd_spell_materials"] = material
    for effect in changed["cast_spell"]["effects"]:
        if effect.get("call") == "resolve_srd_spell_materials":
            effect["params"]["bond_target"] = "{_target_id}"


def _compose_consumers(tools, changed):
    """Extend active observer groups so ordinary saves/damage retain their inactive cost."""
    test = deepcopy(tools["d20_test"])
    group = next(
        e
        for e in test["effects"]
        if "actor_state.has_control_affliction_sources == true" in e.get("condition", "")
    )
    if "resolve_srd_spell_ward_benefits" in str(group):
        group["on_true"] = group["on_true"][0]["on_true"]
    group["condition"] = "actor_state != null and (actor_state.has_control_affliction_sources "
    group["condition"] += (
        "== true or (kind == 'saving_throw' and lasting_state.has_spell_wards == true))"
    )
    group["on_true"] = [
        _when("actor_state.has_control_affliction_sources == true", group["on_true"]),
        _when(
            "kind == 'saving_throw' and lasting_state.has_spell_wards == true",
            [
                _call(
                    "resolve_srd_spell_ward_benefits",
                    {"target_state": "{_actor_state_id}"},
                    WARD_EXPORTS,
                ),
                _when("_ward_bond_effective == true", [_set("_modifier", "_modifier + 1")]),
                _when("_ward_holy_effective == true", [_set("_effective_advantage", True)]),
            ],
        ),
    ]
    changed["d20_test"] = test
    ac = deepcopy(tools["resolve_srd_effective_armor_class"])
    ac["effects"] = [e for e in ac["effects"] if "resolve_srd_spell_ward_benefits" not in str(e)]
    ac["effects"].append(
        _when(
            "lasting_state.has_spell_wards == true",
            [
                _call(
                    "resolve_srd_spell_ward_benefits",
                    {"target_state": "{_target_state_id}"},
                    WARD_EXPORTS,
                ),
                _when(
                    "_ward_bond_effective == true",
                    [
                        _set("_effective_armor_class", "_effective_armor_class + 1"),
                    ],
                ),
            ],
        )
    )
    changed["resolve_srd_effective_armor_class"] = ac
    attack = deepcopy(tools["resolve_srd_attack_conditions"])
    attack["effects"] = [
        e for e in attack["effects"] if "resolve_srd_spell_ward_benefits" not in str(e)
    ]
    attack["effects"].append(
        _when(
            "lasting_state.has_spell_wards == true",
            [
                _call(
                    "resolve_srd_spell_ward_benefits",
                    {"target_state": "{_target_state_id}"},
                    WARD_EXPORTS,
                ),
                _when(
                    "_ward_holy_effective == true and _actor_state_id != _target_state_id",
                    [_set("_condition_disadvantage", True)],
                ),
            ],
        )
    )
    changed["resolve_srd_attack_conditions"] = attack
    for name in ("apply_srd_damage", "apply_srd_composite_damage"):
        owner = deepcopy(tools[name])
        if "resolve_srd_spell_ward_benefits" in str(owner):
            changed[name] = owner
            continue
        for active in owner["effects"]:
            if "resolve_srd_concentration_damage_resistance" not in str(active):
                continue
            call = active["on_true"][0]
            kind = call["params"]["damage_type"][1:-1]
            field = next(
                k for k, v in call["export_context"].items() if v == "_lasting_damage_resistance"
            )
            original = active["condition"]
            active["condition"] = f"({original}) or lasting_state.has_spell_wards == true"
            active["on_true"] = [
                _when(original, active["on_true"]),
                _when(
                    "lasting_state.has_spell_wards == true",
                    [
                        _call(
                            "resolve_srd_spell_ward_benefits",
                            {"target_state": "{target}"},
                            WARD_EXPORTS,
                        ),
                        _when(
                            "_ward_bond_effective == true or (_ward_life_effective == true "
                            f"and {kind} == 'necrotic')",
                            [_set(field, True)],
                        ),
                    ],
                ),
            ]
        # The existing completed-packet observer also records the Bond's irrevocable HP ending.
        after = next(e for e in owner["effects"] if "end_srd_invisibility_damage_source" in str(e))
        original = after["condition"]
        after["condition"] = (
            f"({original}) or (lasting_state.has_spell_wards == true and target.hp == 0)"
        )
        after["on_true"] = [
            _when(original, after["on_true"]),
            _when(
                "lasting_state.has_spell_wards == true and target.hp == 0",
                [_call("refresh_srd_spell_wards", {})],
            ),
        ]
        changed[name] = owner
    process = deepcopy(tools["process_srd_turn_effects"])
    process["effects"] = [
        e for e in process["effects"] if "start_srd_aura_of_life_turn" not in str(e)
    ]
    process["effects"].insert(
        0,
        _when(
            "lasting_state.has_spell_wards == true and "
            "boundary_phase == 'start' and boundary_actor != ''",
            [
                _call(
                    "start_srd_aura_of_life_turn",
                    {"target_state": "{boundary_actor}"},
                    ("_aura_life_healed",),
                ),
            ],
        ),
    )
    changed["process_srd_turn_effects"] = process
    harm = deepcopy(tools["cast_spell_single_target_save_damage"])
    for effects in _effect_lists(harm["effects"]):
        for i, effect in enumerate(list(effects)):
            if "reduce_hit_point_maximum_on_failed_save == true" in effect.get("condition", ""):
                if "_aura_maximum_hp_protected" in str(effect):
                    continue
                before = [
                    _set("_aura_maximum_hp_protected", False),
                    _when(
                        "lasting_state.has_spell_wards == true",
                        [
                            _call(
                                "resolve_srd_spell_ward_benefits",
                                {
                                    "target_state": "{_target_id}",
                                },
                                {"_aura_maximum_hp_protected": "_ward_life_effective"},
                            ),
                        ],
                    ),
                ]
                effect["on_true"] = before + [
                    _when("_aura_maximum_hp_protected != true", effect["on_true"]),
                ]
                effects[i] = effect
    changed["cast_spell_single_target_save_damage"] = harm


def _compose_hit_consumers(tools, changed):
    """Retain protection before the real hit, including a hit that ends its caster's life."""
    owners = {
        "resolve_combat_attack": (
            "_actor_state_id",
            "_target_id",
            "attack_mode",
            "_attack_outcome",
        ),
        "resolve_combat_attack_followup": (
            "_actor_state_id",
            "_target_id",
            "attack_mode",
            "_attack_outcome",
        ),
        "resolve_opportunity_attack": (
            "_actor_state_id",
            "_target_state_id",
            "'melee'",
            "_attack_outcome",
        ),
        "resolve_adjudicated_creature_attack": (
            "_actor_id",
            "_target_id",
            "attack_mode",
            "_attack_outcome",
        ),
        "resolve_srd_unarmed_effect": (
            "_actor_state_id",
            "_target_state_id",
            "'melee'",
            "_unarmed_attack_kind",
        ),
        "cast_spell_attack_damage": (
            "_caster_state_id",
            "_target_id",
            "attack_kind",
            "_spell_attack_kind",
        ),
    }
    for name, (actor, target, kind, outcome) in owners.items():
        tool = deepcopy(changed.get(name, tools[name]))
        tool["effects"] = [e for e in tool["effects"] if "_hit_holy_source_ids" not in str(e)]
        tool["effects"][:0] = [
            _set("_hit_holy_source_ids", []),
            _when(
                "lasting_state.has_spell_wards == true",
                [
                    _call(
                        "resolve_srd_spell_ward_benefits",
                        {"target_state": "{" + target + "}"},
                        {"_hit_holy_source_ids": "_ward_holy_source_ids"},
                    )
                ],
            ),
        ]
        tool["effects"].append(
            _when(
                "len(_hit_holy_source_ids) > 0",
                [
                    _call(
                        "resolve_srd_holy_aura_hit",
                        {
                            "attacker_state": "{" + actor + "}",
                            "attack_kind": "{" + kind + "}",
                            "attack_outcome": "{" + outcome + "}",
                            "protected_source_ids": "{_hit_holy_source_ids}",
                        },
                        ("_holy_aura_save", "_holy_aura_blinded"),
                    )
                ],
            )
        )
        changed[name] = tool


def extend_runtime(text):
    """Compose the owning leaves and direct consumers before final reaction/damage adapters."""
    tools = _load_runtime(text)["tools"]
    changed = {
        **_ring_tools(),
        **_source_tools(),
        **_query_tools(),
        **_consequence_tools(),
        "cast_srd_spell_ward": _cast_tool(),
        "resolve_srd_warding_bond_materials": _bond_material_tool(),
        "choose_srd_holy_aura_creatures": _choose_tool(),
    }
    cast = deepcopy(tools["cast_spell"])
    if not any(p["name"] == "ward_chosen" for p in cast["parameters"]):
        cast["parameters"].append(
            _optional("ward_chosen", "array", [], items={"type": "string"}, max_items=64)
        )
    for effects in _effect_lists(cast["effects"]):
        effects[:] = [
            e
            for e in effects
            if "_spell_definition.family == 'actor_spell_ward'" not in e.get("condition", "")
        ]
    index = max(
        i
        for i, e in enumerate(cast["effects"])
        if "_spell_definition.family ==" in e.get("condition", "")
    )
    cast["effects"].insert(
        index + 1,
        _when(
            "_spell_definition.family == 'actor_spell_ward'",
            [
                _call(
                    "cast_srd_spell_ward",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "slot_level": "{_slot_level}",
                        "concentration_save_modifier": (
                            "{_casting_profile.concentration_save_modifier}"
                        ),
                        "save_dc": "{_casting_profile.save_dc}",
                        "target_state": "{_target_id}",
                        "target_willing": "{target_willing}",
                        "caster_can_touch_target": "{_caster_can_touch_target}",
                        "chosen_state_ids": "{ward_chosen}",
                        "spell_cast_id": "{_spell_source_cast_id}",
                    },
                    ("_spell_summary_rows", "_spell_detail_rows"),
                ),
            ],
        ),
    )
    changed["cast_spell"] = cast
    _compose_materials(tools, changed)
    _compose_consumers(tools, changed)
    _compose_hit_consumers(tools, changed)
    for name in ("move_srd_travel_subject", "move_srd_scene_creature_environment_segment"):
        if name in tools:
            move = deepcopy(tools[name])
            move["effects"] = [
                e for e in move["effects"] if "refresh_srd_spell_wards" not in str(e)
            ]
            move["effects"].append(
                _when(
                    "lasting_state.has_spell_wards == true", [_call("refresh_srd_spell_wards", {})]
                )
            )
            changed[name] = move
    end = deepcopy(tools["end_srd_concentration_node"])
    end["effects"] = [e for e in end["effects"] if "retire_srd_spell_ward_record" not in str(e)]
    end["effects"].insert(
        0,
        _when(
            "source_state.concentration_effect_family == 'actor_spell_ward' "
            "and source_state.ward_source_id != null",
            [
                _call(
                    "retire_srd_spell_ward_record",
                    {
                        "source_id": "{source_state.ward_source_id}",
                        "reason": "concentration",
                    },
                ),
            ],
        ),
    )
    changed["end_srd_concentration_node"] = end
    text = _replace_tools(text, changed)
    text = re.sub(
        r"\n\n(?=  (?:" + "|".join(re.escape(n) for n in changed) + r"):\n|procedures:)",
        "\n",
        text,
    )
    # Other D20 cards retain this authored alias when its owning tool is regenerated.
    text = re.sub(
        r"(?ms)(^  d20_test:\n.*?      result_card):\n",
        r"\1: &d20_result_card\n",
        text,
        count=1,
    )
    schema = yaml.safe_dump({"actor_spell_ward": _schema()}, sort_keys=False)
    schema = "".join("      " + line for line in schema.splitlines(True))
    if "      actor_spell_ward:" in text:
        text = re.sub(
            r"(?ms)^      actor_spell_ward:\n.*?(?=^      [a-z])",
            lambda _: schema + "\n",
            text,
            count=1,
        )
    else:
        text = text.replace("      spell_barrier:", schema + "\n      spell_barrier:", 1)
    return text
