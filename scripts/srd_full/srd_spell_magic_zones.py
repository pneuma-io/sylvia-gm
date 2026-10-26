"""Exact saved magic-zone sources qualify real casts, actions and spell effects."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import _each, _push
    from .srd_scene_relations import _array
    from .srd_spell_barriers import _effect_lists, _scene, _tool
    from .srd_spell_barriers import _geometry_tools as _barrier_geometry_tools
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _optional, _replace_tools
else:
    from srd_control_behaviors import _each, _push
    from srd_scene_relations import _array
    from srd_spell_barriers import _effect_lists, _scene, _tool
    from srd_spell_barriers import _geometry_tools as _barrier_geometry_tools
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _optional, _replace_tools


PROFILES = {
    "antimagic_field": {
        "family": "spell_magic_zone",
        "base_level": 8,
        "casting_time": "action",
        "range_kind": "self",
        "range_feet": 0,
        "duration_rounds": 600,
        "concentration": True,
        "zone_mode": "antimagic",
        "radius_feet": 10,
    },
    "forcecage": {
        "family": "spell_magic_zone",
        "base_level": 7,
        "casting_time": "action",
        "range_kind": "distance",
        "range_feet": 100,
        "duration_rounds": 600,
        "concentration": True,
        "zone_mode": "forcecage",
        "radius_feet": 0,
    },
    "globe_of_invulnerability": {
        "family": "spell_magic_zone",
        "base_level": 6,
        "casting_time": "action",
        "range_kind": "self",
        "range_feet": 0,
        "duration_rounds": 10,
        "concentration": True,
        "zone_mode": "globe",
        "radius_feet": 10,
    },
}
PUBLIC_TOOLS = set()
MEMBERSHIP_EXPORTS = ("_magic_zone_inside", "_magic_zone_overlap")
EFFECT_EXPORTS = ("_magic_zone_effect_allowed", "_magic_zone_blocking_source_ids")


def _when(condition, effects, otherwise=None):
    node = {"condition": condition, "on_true": effects}
    if otherwise is not None:
        node["on_false"] = otherwise
    return node


def validate_profiles(definitions):
    """Admit exactly the pinned source profiles, never caller-supplied spell rules."""
    for spell, definition in definitions.items():
        if definition.get("family") == "spell_magic_zone" and definition != PROFILES.get(spell):
            raise SystemExit(f"Magic-zone profile differs from its printed source: {spell}")


def _schema():
    properties = {
        key: {
            "type": "boolean"
            if type(value) is bool
            else "integer"
            if type(value) is int
            else "string"
        }
        for key, value in PROFILES["antimagic_field"].items()
        if key != "family"
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _sources(effects):
    return [
        _when(
            "state.has_spell_magic_zones == true",
            [
                _set("_magic_zone_ids", "state.spell_magic_zone_source_ids"),
                _each(
                    "_magic_zone_ids",
                    "_magic_zone_id",
                    [
                        _set(
                            "_magic_zone_record", "state.spell_magic_zone_sources[_magic_zone_id]"
                        ),
                        _when("_magic_zone_record.installed == true", effects),
                    ],
                ),
            ],
        )
    ]


def _geometry_tools():
    """Reuse the admitted box-body Emanation owner; radius zero is an exact source Cube."""

    def rename(value):
        if isinstance(value, str):
            return value.replace("barrier", "magic_zone").replace("'antilife'", "'antimagic'")
        if isinstance(value, list):
            return [rename(child) for child in value]
        if isinstance(value, dict):
            return {rename(key): rename(child) for key, child in value.items()}
        return value

    return rename(_barrier_geometry_tools())


def _source_tools():
    retire = _tool(
        "Retire one exact magic-zone source without recursively ending concentration.",
        [_parameter("source_id"), _optional("reason", default="removed")],
        [
            _set("_retiring_zone", "state.spell_magic_zone_sources[source_id]"),
            _require("_retiring_zone != null", "Select the actual saved magic-zone source."),
            _when(
                "_retiring_zone.installed == true",
                [
                    _set("state.spell_magic_zone_sources.{source_id}.installed", False),
                    _set("state.spell_magic_zone_sources.{source_id}.ended_reason", "reason"),
                    _set(
                        "state.spell_magic_zone_sources.{source_id}.ended_tick",
                        "state.lasting_tick",
                    ),
                ],
            ),
        ],
    )
    end = _tool(
        "End only the selected magic-zone record and its matching concentration.",
        [_parameter("source_id"), _optional("reason", default="removed")],
        [
            _set("_ending_zone", "state.spell_magic_zone_sources[source_id]"),
            _require("_ending_zone != null", "Select the actual saved magic-zone source."),
            _when(
                "_ending_zone.installed == true",
                [
                    _call(
                        "retire_srd_spell_magic_zone_record",
                        {"source_id": "{source_id}", "reason": "{reason}"},
                    ),
                    _call(
                        "finish_srd_spell_magic_zone_concentration",
                        {
                            "caster_state": "{_ending_zone.caster_state_id}",
                            "source_id": "{source_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    finish = _tool(
        "Finish concentration only when it owns this exact removed source.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _when(
                "caster_state.concentration_effect_family == 'spell_magic_zone' and "
                "caster_state.magic_zone_source_id == source_id",
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
        ],
    )
    check = _tool(
        "Resolve the actual clock and caster concentration of one saved magic zone.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_checked_zone", "state.spell_magic_zone_sources[source_id]"),
            _set(
                "_magic_zone_source_active",
                "_checked_zone != null and _checked_zone.installed == true and "
                "state.lasting_tick + 0.000000001 < _checked_zone.expires_tick",
            ),
            _when(
                "_magic_zone_source_active == true",
                [
                    _set(
                        "_magic_zone_source_active",
                        "caster_state.is_alive == true and caster_state.hp > 0 and "
                        "caster_state.concentration_effect_family == 'spell_magic_zone' and "
                        "caster_state.magic_zone_source_id == source_id",
                    ),
                ],
            ),
            _when(
                "_magic_zone_source_active != true and _checked_zone != null and "
                "_checked_zone.installed == true",
                [
                    _call(
                        "end_srd_spell_magic_zone_source",
                        {"source_id": "{source_id}", "reason": "lifetime"},
                    ),
                ],
            ),
        ],
    )
    refresh = _tool(
        "Refresh source clocks without ending, extending or suppressing unrelated effects.",
        [],
        [
            _set("_has_live_magic_zones", False),
            *_sources(
                [
                    _call(
                        "check_srd_spell_magic_zone_source",
                        {
                            "caster_state": "{_magic_zone_record.caster_state_id}",
                            "source_id": "{_magic_zone_id}",
                        },
                        ("_magic_zone_source_active",),
                    ),
                    _when(
                        "_magic_zone_source_active == true", [_set("_has_live_magic_zones", True)]
                    ),
                ]
            ),
            _set("state.has_spell_magic_zones", "_has_live_magic_zones"),
        ],
    )
    return {
        "retire_srd_spell_magic_zone_record": retire,
        "end_srd_spell_magic_zone_source": end,
        "finish_srd_spell_magic_zone_concentration": finish,
        "check_srd_spell_magic_zone_source": check,
        "refresh_srd_spell_magic_zones": refresh,
    }


def _query_tools():
    cast = _tool(
        "Qualify actual spellcasting and Magic actions at the actor's admitted body.",
        [_entity("actor_state")],
        [
            _set("_magic_zone_can_cast", True),
            _set("_magic_zone_blocking_source_ids", []),
            _when(
                "state.has_spell_magic_zones == true",
                [
                    _call("refresh_srd_spell_magic_zones", {}),
                    *_sources(
                        [
                            _when(
                                "_magic_zone_record.mode == 'antimagic'",
                                [
                                    _call(
                                        "resolve_srd_magic_zone_membership",
                                        {
                                            "subject": "{_actor_state_id}",
                                            "source_id": "{_magic_zone_id}",
                                        },
                                        MEMBERSHIP_EXPORTS,
                                    ),
                                    _when(
                                        "_magic_zone_overlap == true",
                                        [
                                            _set("_magic_zone_can_cast", False),
                                            _push(
                                                "_magic_zone_blocking_source_ids", "_magic_zone_id"
                                            ),
                                        ],
                                    ),
                                ],
                            )
                        ]
                    ),
                ],
            ),
        ],
    )
    effect = _tool(
        "Qualify an actual spell or other magical effect against saved source volumes.",
        [
            _parameter("source_subject"),
            _parameter("target_subject"),
            _parameter("effect_kind"),
            _optional("actual_spell_level", "integer", -1, model_hidden=True),
        ],
        [
            _set("_magic_zone_effect_allowed", True),
            _set("_magic_zone_blocking_source_ids", []),
            _require(
                "effect_kind == 'spell' or effect_kind == 'other_magic' or effect_kind "
                "== 'physical'",
                "Use the actual owning spell, magical effect or physical classification.",
            ),
            _when(
                "state.has_spell_magic_zones == true and effect_kind != 'physical'",
                [
                    _call("refresh_srd_spell_magic_zones", {}),
                    *_sources(
                        [
                            _when(
                                "_magic_zone_record.suppressed != true",
                                [
                                    _call(
                                        "resolve_srd_magic_zone_membership",
                                        {
                                            "subject": "{source_subject}",
                                            "source_id": "{_magic_zone_id}",
                                        },
                                        MEMBERSHIP_EXPORTS,
                                    ),
                                    _set("_magic_origin_inside", "_magic_zone_overlap"),
                                    _call(
                                        "resolve_srd_magic_zone_membership",
                                        {
                                            "subject": "{target_subject}",
                                            "source_id": "{_magic_zone_id}",
                                        },
                                        MEMBERSHIP_EXPORTS,
                                    ),
                                    _set("_magic_target_inside", "_magic_zone_overlap"),
                                    _when(
                                        "_magic_zone_record.mode == 'antimagic' and "
                                        "_magic_target_inside == true",
                                        [
                                            _set("_magic_zone_effect_allowed", False),
                                            _push(
                                                "_magic_zone_blocking_source_ids", "_magic_zone_id"
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_magic_zone_record.mode == 'globe' and effect_kind "
                                        "== 'spell' and "
                                        "_magic_origin_inside != true and "
                                        "_magic_target_inside == true",
                                        [
                                            _require(
                                                "actual_spell_level >= 0 and "
                                                "actual_spell_level <= 9",
                                                "Globe requires the actual committed spell "
                                                "slot level.",
                                            ),
                                            _when(
                                                "actual_spell_level <= "
                                                "_magic_zone_record.spell_level - 1",
                                                [
                                                    _set("_magic_zone_effect_allowed", False),
                                                    _push(
                                                        "_magic_zone_blocking_source_ids",
                                                        "_magic_zone_id",
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_magic_zone_record.mode == 'forcecage' and "
                                        "_magic_zone_record.prison_shape == 'box' and "
                                        "_magic_origin_inside != _magic_target_inside and "
                                        "effect_kind == 'spell'",
                                        [
                                            _set("_magic_zone_effect_allowed", False),
                                            _push(
                                                "_magic_zone_blocking_source_ids", "_magic_zone_id"
                                            ),
                                        ],
                                    ),
                                ],
                            )
                        ]
                    ),
                ],
            ),
        ],
    )
    return {"check_srd_magic_zone_cast": cast, "check_srd_magic_zone_effect": effect}


def _cast_tool():
    return _tool(
        "Install the actual source volume, slot, clock and caster concentration.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("concentration_save_modifier", "integer"),
            _parameter("save_dc", "integer"),
            _array("origin", default=[0, 0, 0]),
            _optional("prison_shape", default="cage"),
            _optional("prison_side", "number", 20),
            _optional("spell_cast_id", default="", model_hidden=True),
        ],
        [
            {
                "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
                "store_as": "_zone_profile",
            },
            _require(
                "_zone_profile != null and _zone_profile.family == 'spell_magic_zone' and "
                "slot_level >= _zone_profile.base_level and slot_level <= 9",
                "Use the reviewed magic-zone spell and actual slot.",
            ),
            *_scene("_caster_state_id", "_zone_caster_body"),
            _require(
                "_zone_caster_body.footprint.kind == 'box'",
                "Magic-zone Emanations require an admitted box caster body.",
            ),
            _set("_zone_body", "_zone_caster_body"),
            _when(
                "_zone_profile.zone_mode == 'forcecage'",
                [
                    _require(
                        "prison_shape == 'cage' or prison_shape == 'box'",
                        "Choose the printed cage or solid-box form.",
                    ),
                    _require(
                        "prison_side > 0 and ((prison_shape == 'cage' and prison_side <= 20) or "
                        "(prison_shape == 'box' and prison_side <= 10))",
                        "The actual prison side exceeds the chosen source form.",
                    ),
                    _call(
                        "measure_srd_information_distance",
                        {"start": "{_zone_caster_body.position}", "end": "{origin}"},
                        ("_scene_distance",),
                    ),
                    _require(
                        "_scene_distance <= 100",
                        "Choose a prison area within the actual 100-foot cast range.",
                    ),
                    *[
                        _require(
                            f"origin[{i}] - prison_side * 0.5 >= "
                            f"state.spatial_scene.map.bounds.min[{i}] and "
                            f"origin[{i}] + prison_side * 0.5 <= "
                            f"state.spatial_scene.map.bounds.max[{i}]",
                            "The prison must fit within the admitted map.",
                        )
                        for i in range(3)
                    ],
                    _set(
                        "_zone_body",
                        {
                            "position": ["origin[0]", "origin[1]", "origin[2] - prison_side * 0.5"],
                            "footprint": {
                                "kind": "'box'",
                                "half_extents": ["prison_side * 0.5", "prison_side * 0.5"],
                            },
                            "height": "prison_side",
                            "metadata": {"plane": "_zone_caster_body.metadata.plane"},
                        },
                    ),
                ],
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
            _when(
                "state.spell_magic_zone_sources == null",
                [
                    _set("state.spell_magic_zone_sources", {}),
                    _set("state.spell_magic_zone_source_ids", []),
                ],
            ),
            _when(
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
                {"message": "{_caster_state_id}:{spell}:{state.lasting_serial}"},
                ("_info_projection_message",),
            ),
            _set("_new_magic_zone_id", "_info_projection_message"),
            _set(
                "state.spell_magic_zone_sources.{_new_magic_zone_id}",
                {
                    "installed": True,
                    "spell_id": "spell",
                    "spell_level": "slot_level",
                    "mode": "_zone_profile.zone_mode",
                    "caster_state_id": "_caster_state_id",
                    "source_caster_id": "_caster_state_id",
                    "cast_id": "spell_cast_id",
                    "ongoing_duration": "spell_cast_id != ''",
                    "started_tick": "state.lasting_tick",
                    "serial": "state.lasting_serial",
                    "expires_tick": "state.lasting_tick + _zone_profile.duration_rounds * 2",
                    "concentration": True,
                    "body": "_zone_body",
                    "plane": "_zone_caster_body.metadata.plane",
                    "radius": "_zone_profile.radius_feet",
                    "prison_shape": "prison_shape",
                    "prison_side": "prison_side",
                    "save_dc": "save_dc",
                    "dispel_ongoing": "_zone_profile.zone_mode == 'globe'",
                    "trapped_state_ids": [],
                },
            ),
            _set("_retained_zone_ids", []),
            _set("_previous_zone_ids", "state.spell_magic_zone_source_ids"),
            _each(
                "_previous_zone_ids",
                "_previous_zone_id",
                [
                    _when(
                        "state.spell_magic_zone_sources[_previous_zone_id].installed == true",
                        [_push("_retained_zone_ids", "_previous_zone_id")],
                    ),
                ],
            ),
            _require(
                "len(_retained_zone_ids) < 128",
                "At most 128 actual active magic-zone sources are supported.",
            ),
            _push("_retained_zone_ids", "_new_magic_zone_id"),
            _set("state.spell_magic_zone_source_ids", "_retained_zone_ids"),
            _set("state.has_spell_magic_zones", True),
            _set("caster_state.concentration_spell_id", "spell"),
            _set("caster_state.concentration_effect_family", "'spell_magic_zone'"),
            _set("caster_state.concentration_target_state_id", "_caster_state_id"),
            _set("caster_state.concentration_target_state_ids", []),
            _set("caster_state.concentration_save_modifier", "concentration_save_modifier"),
            _set("caster_state.concentration_started_tick", "state.lasting_tick"),
            _set(
                "caster_state.concentration_expires_tick",
                "state.lasting_tick + _zone_profile.duration_rounds * 2",
            ),
            _set("caster_state.magic_zone_source_id", "_new_magic_zone_id"),
            _when(
                "_zone_profile.zone_mode == 'forcecage'",
                [
                    _call("capture_srd_forcecage_subjects", {"source_id": "{_new_magic_zone_id}"}),
                ],
            ),
            _set("_spell_summary_rows", [{"label": "Spell", "value": "_zone_profile.zone_mode"}]),
            _set("_spell_detail_rows", [{"label": "Source", "value": "_new_magic_zone_id"}]),
        ],
    )


def _force_tools():
    """Fully contained creatures have real containment; unresolved partial pushes fail clearly."""
    moving_subject = "{_zone_movement_subject}"
    capture = _tool(
        "Bind completely contained actual creatures to this saved source prison.",
        [_parameter("source_id")],
        [
            _set("_force_scene_entities", "state.spatial_scene.entities"),
            _set("_force_trapped", []),
            _each(
                "_force_scene_entities",
                "_force_candidate",
                [
                    _when(
                        "_force_candidate.active == true and "
                        "_force_candidate.metadata.participant_ref != null",
                        [
                            _call(
                                "resolve_srd_magic_zone_membership",
                                {
                                    "subject": "{_force_candidate.id}",
                                    "source_id": "{source_id}",
                                },
                                MEMBERSHIP_EXPORTS,
                            ),
                            _require(
                                "_magic_zone_overlap != true or _magic_zone_inside == true",
                                "Forcecage partial-body pushing is unsupported; this prison "
                                "intersects a creature that must be pushed.",
                            ),
                            _when(
                                "_magic_zone_inside == true",
                                [
                                    _push(
                                        "_force_trapped",
                                        "_force_candidate.metadata.participant_ref",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("state.spell_magic_zone_sources.{source_id}.trapped_state_ids", "_force_trapped"),
        ],
    )
    movement = _tool(
        "Qualify real ordinary body movement and identify paid magical escape boundaries.",
        [
            _parameter("subject"),
            _array("destination"),
            _parameter("plane"),
            _optional("teleport", "boolean", False, model_hidden=True),
            _optional("planar", "boolean", False, model_hidden=True),
        ],
        [
            _set("_magic_zone_movement_allowed", True),
            _set("_magic_zone_escape_required", False),
            _set("_magic_zone_blocking_source_ids", []),
            _when(
                "state.has_spell_magic_zones == true",
                [
                    _call("refresh_srd_spell_magic_zones", {}),
                    _call(
                        "normalize_srd_barrier_scene_subject",
                        {"subject": "{subject}"},
                        ("_barrier_raw_scene_subject",),
                    ),
                    _set("_zone_movement_subject", "_barrier_raw_scene_subject"),
                    *_scene("_zone_movement_subject", "_zone_mover"),
                    *_sources(
                        [
                            _when(
                                "_magic_zone_record.suppressed != true",
                                [
                                    _call(
                                        "resolve_srd_magic_zone_membership",
                                        {
                                            "subject": "{_zone_movement_subject}",
                                            "source_id": "{_magic_zone_id}",
                                        },
                                        MEMBERSHIP_EXPORTS,
                                    ),
                                    _set("_zone_movement_origin_inside", "_magic_zone_inside"),
                                    _set("_zone_movement_origin_overlap", "_magic_zone_overlap"),
                                    _call(
                                        "resolve_srd_magic_zone_membership",
                                        {
                                            "subject": "{_zone_movement_subject}",
                                            "source_id": "{_magic_zone_id}",
                                            "position": "{destination}",
                                        },
                                        MEMBERSHIP_EXPORTS,
                                    ),
                                    _set(
                                        "_zone_movement_destination_inside",
                                        "_magic_zone_inside == true and "
                                        "_magic_zone_record.plane == plane",
                                    ),
                                    _set(
                                        "_zone_movement_destination_overlap",
                                        "_magic_zone_overlap == true and "
                                        "_magic_zone_record.plane == plane",
                                    ),
                                    _when(
                                        "_magic_zone_record.mode == 'antimagic' and (teleport "
                                        "== true or planar == true) and "
                                        "(_zone_movement_origin_overlap == true or "
                                        "_zone_movement_destination_overlap == true)",
                                        [
                                            _set("_magic_zone_movement_allowed", False),
                                            _push(
                                                "_magic_zone_blocking_source_ids", "_magic_zone_id"
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_magic_zone_record.mode == 'forcecage'",
                                        [
                                            _when(
                                                "_zone_movement_origin_inside == true and "
                                                "_zone_movement_destination_inside != true",
                                                [
                                                    _set("_magic_zone_movement_allowed", False),
                                                    _push(
                                                        "_magic_zone_blocking_source_ids",
                                                        "_magic_zone_id",
                                                    ),
                                                    _when(
                                                        "teleport == true or planar == true",
                                                        [_set("_magic_zone_escape_required", True)],
                                                    ),
                                                ],
                                            ),
                                            _when(
                                                "teleport != true and planar != true and "
                                                "plane == _magic_zone_record.plane",
                                                [
                                                    _call(
                                                        "resolve_srd_magic_zone_body_segment",
                                                        {
                                                            "moving_subject": moving_subject,
                                                            "source_id": "{_magic_zone_id}",
                                                            "start": "{_zone_mover.position}",
                                                            "end": "{destination}",
                                                        },
                                                        ("_body_hit",),
                                                    ),
                                                    _when(
                                                        "_body_hit == true and "
                                                        "_magic_zone_record.prison_shape == 'cage' "
                                                        "and _zone_mover.metadata."
                                                        "participant_ref == null",
                                                        [
                                                            _require(
                                                                "false",
                                                                "Cage bars require an actual "
                                                                "creature or a source-qualified "
                                                                "object-passage owner.",
                                                            )
                                                        ],
                                                    ),
                                                    _when(
                                                        "_body_hit == true and "
                                                        "_zone_movement_origin_inside != "
                                                        "_zone_movement_destination_inside",
                                                        [
                                                            _set(
                                                                "_magic_zone_movement_allowed",
                                                                False,
                                                            ),
                                                            _push(
                                                                "_magic_zone_blocking_source_ids",
                                                                "_magic_zone_id",
                                                            ),
                                                        ],
                                                    ),
                                                    _when(
                                                        "_body_hit == true and "
                                                        "_zone_movement_origin_inside != true "
                                                        "and "
                                                        "_zone_movement_destination_inside != "
                                                        "true",
                                                        [
                                                            _set(
                                                                "_magic_zone_movement_allowed",
                                                                False,
                                                            ),
                                                            _push(
                                                                "_magic_zone_blocking_source_ids",
                                                                "_magic_zone_id",
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            )
                        ]
                    ),
                ],
            ),
        ],
    )
    return {"capture_srd_forcecage_subjects": capture, "check_srd_magic_zone_movement": movement}


def _unwrap_effect_consumers(effects):
    """Remove only this final binder's exact three-effect envelope before regeneration."""
    restored = []
    index = 0
    while index < len(effects):
        group = effects[index : index + 3]
        if (
            len(group) == 3
            and group[0].get("set", {}).get("field") == "_magic_zone_effect_allowed"
            and group[1].get("condition") == "lasting_state.has_spell_magic_zones == true"
            and group[2].get("condition") == "_magic_zone_effect_allowed == true"
        ):
            restored.extend(_unwrap_effect_consumers(group[2]["on_true"]))
            index += 3
            continue
        effect = deepcopy(effects[index])
        for branch in ("on_true", "on_false"):
            if branch in effect:
                effect[branch] = _unwrap_effect_consumers(effect[branch])
        restored.append(effect)
        index += 1
    return restored


EFFECT_CONSUMERS = (
    "cast_spell_single_target_save_damage",
    "cast_spell_attack_damage",
    "complete_srd_spell_attack_hit",
)


def normalize_runtime(text):
    """Restore the exact scalar owners before reaction generators locate their flat anchors."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in EFFECT_CONSUMERS:
        if name in tools:
            tool = deepcopy(tools[name])
            effects = _unwrap_effect_consumers(tool["effects"])
            if effects != tool["effects"]:
                tool["effects"] = effects
                changed[name] = tool
    return _replace_tools(text, changed) if changed else text


def _compose_scalar_effects(tools, changed):
    """Committed scalar casts keep their real costs but produce no protected-target effect."""
    for name in EFFECT_CONSUMERS:
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        original = _unwrap_effect_consumers(tool["effects"])
        saved_hit = name == "complete_srd_spell_attack_hit"
        prefix = []
        if saved_hit:
            split = next(
                i
                for i, effect in enumerate(original)
                if effect.get("set", {}).get("field") == "_spell_target_armor_class"
            )
            prefix, original = original[:split], original[split:]
        tool["effects"] = [
            *prefix,
            _set("_magic_zone_effect_allowed", True),
            _when(
                "lasting_state.has_spell_magic_zones == true",
                [
                    _call(
                        "check_srd_magic_zone_effect",
                        {
                            "source_subject": "{_caster_state_id}",
                            "target_subject": "{_target_id}",
                            "effect_kind": "spell",
                            "actual_spell_level": "{slot_level}",
                        },
                        EFFECT_EXPORTS,
                    ),
                ],
            ),
            _when(
                "_magic_zone_effect_allowed == true",
                original,
                [
                    _set("_spell_damage", 0),
                    _set("_card_damage", 0),
                    _set("_spell_damage_amount", 0),
                    _set("_spell_damage_dice", None),
                    _set("_spell_casting_ability", None),
                    _set("_spell_attack", None),
                    _set("_spell_attack_advantage", False),
                    _set("_spell_attack_disadvantage", False),
                    _set("_spell_attack_kind", None),
                    _set("_spell_attack_mode", None),
                    _set("_spell_effective_damage_type", None),
                    _set("_spell_target_armor_class", None),
                    _set("_spell_hit_window_deferred", False),
                    _set("_spell_save", None),
                    _set("_spell_save_advantage", False),
                    _set("_spell_save_foregone", False),
                    _set("_spell_save_modifier", None),
                    _set("_spell_save_dc", None),
                    _set("_spell_attack_outcome", "'no_effect'"),
                    _set("_spell_save_outcome", "'no_effect'"),
                    _set(
                        "_spell_summary_rows",
                        [
                            {"label": "Spell", "value": "spell"},
                            {
                                "label": "Effect",
                                "value": "'Excluded by the actual magic-zone source.'",
                            },
                        ],
                    ),
                    _set(
                        "_spell_detail_rows",
                        [{"label": "Blocking sources", "value": "_magic_zone_blocking_source_ids"}],
                    ),
                ],
            ),
        ]
        if saved_hit:
            excluded = tool["effects"][-1]["on_false"]
            excluded[:] = [
                effect
                for effect in excluded
                if effect.get("set", {}).get("field") != "_spell_attack"
            ]
            excluded.extend(
                [
                    _set("_spell_attack_kind", "'no_effect'"),
                    _set(
                        "state.pending_srd_actions.{pending_action_id}.stage",
                        "'spell_attack_resolved'",
                    ),
                ]
            )
        changed[name] = tool


def bind_effect_consumers(text):
    """Bind after final reactions so atomic casts and saved spell-hit completion share the gate."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    _compose_scalar_effects(tools, changed)
    return _replace_tools(text, changed)


def extend_runtime(text):
    """Compose source leaves before final reaction, removal and damage consumers."""
    tools = _load_runtime(text)["tools"]
    changed = {
        **_geometry_tools(),
        **_source_tools(),
        **_query_tools(),
        **_force_tools(),
        "cast_srd_spell_magic_zone": _cast_tool(),
    }
    cast = deepcopy(tools["cast_spell"])
    for parameter in (
        _array("magic_zone_origin", default=[0, 0, 0]),
        _optional("magic_zone_shape", default="cage"),
        _optional("magic_zone_side", "number", 20),
    ):
        if not any(p["name"] == parameter["name"] for p in cast["parameters"]):
            cast["parameters"].append(parameter)
    for effects in _effect_lists(cast["effects"]):
        effects[:] = [
            e
            for e in effects
            if "_spell_definition.family == 'spell_magic_zone'" not in e.get("condition", "")
        ]
    index = max(
        i
        for i, e in enumerate(cast["effects"])
        if "_spell_definition.family ==" in e.get("condition", "")
    )
    cast["effects"].insert(
        index + 1,
        _when(
            "_spell_definition.family == 'spell_magic_zone'",
            [
                _call(
                    "cast_srd_spell_magic_zone",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "slot_level": "{_slot_level}",
                        "save_dc": "{_casting_profile.save_dc}",
                        "concentration_save_modifier": (
                            "{_casting_profile.concentration_save_modifier}"
                        ),
                        "origin": "{magic_zone_origin}",
                        "prison_shape": "{magic_zone_shape}",
                        "prison_side": "{magic_zone_side}",
                        "spell_cast_id": "{_spell_source_cast_id}",
                    },
                    ("_spell_summary_rows", "_spell_detail_rows"),
                ),
            ],
        ),
    )
    changed["cast_spell"] = cast
    end = deepcopy(tools["end_srd_concentration_node"])
    end["effects"] = [
        e for e in end["effects"] if "retire_srd_spell_magic_zone_record" not in str(e)
    ]
    end["effects"].insert(
        0,
        _when(
            "source_state.concentration_effect_family == 'spell_magic_zone' and "
            "source_state.magic_zone_source_id != null",
            [
                _call(
                    "retire_srd_spell_magic_zone_record",
                    {"source_id": "{source_state.magic_zone_source_id}", "reason": "concentration"},
                ),
            ],
        ),
    )
    changed["end_srd_concentration_node"] = end
    action = deepcopy(tools["spend_srd_action"])
    if not any(p["name"] == "lasting_state" for p in action["parameters"]):
        action["parameters"].append(
            _optional("lasting_state", default="state", resolve="entity", model_hidden=True)
        )
    action["effects"] = [e for e in action["effects"] if "check_srd_magic_zone_cast" not in str(e)]
    action["effects"].insert(
        0,
        _when(
            "magic_action == true and lasting_state.has_spell_magic_zones == true",
            [
                _call(
                    "check_srd_magic_zone_cast",
                    {"actor_state": "{combat.active_actor}"},
                    ("_magic_zone_can_cast",),
                ),
                _require(
                    "_magic_zone_can_cast == true",
                    "Antimagic Field prevents the Magic action at this actual body.",
                ),
            ],
        ),
    )
    changed["spend_srd_action"] = action
    text = _replace_tools(text, changed)
    schema = yaml.safe_dump({"spell_magic_zone": _schema()}, sort_keys=False)
    schema = "".join("      " + line for line in schema.splitlines(True))
    if "      spell_magic_zone:" in text:
        text = re.sub(
            r"(?ms)^      spell_magic_zone:\n.*?(?=^      [a-z])",
            lambda _: schema + "\n",
            text,
            count=1,
        )
    else:
        text = text.replace("      spell_barrier:", schema + "\n      spell_barrier:", 1)
    return text


def bind_runtime_consumers(text):
    """Ordinary committed movement cannot bypass the actual prison; paid escape stays separate."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("move_srd_travel_subject", "move_srd_scene_creature_environment_segment"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        tool["effects"] = [
            e for e in tool["effects"] if "check_srd_magic_zone_movement" not in str(e)
        ]
        is_segment = name == "move_srd_scene_creature_environment_segment"
        subject = "{_actor_state_id}" if is_segment else "{subject}"
        plane_effects = (
            [*_scene("_actor_state_id", "_zone_segment_actor")]
            if is_segment
            else [
                _call(
                    "normalize_srd_barrier_scene_subject",
                    {"subject": "{subject}"},
                    ("_barrier_raw_scene_subject",),
                ),
                *_scene("_barrier_raw_scene_subject", "_zone_segment_actor"),
            ]
        )
        plane = "{_zone_segment_actor.metadata.plane}" if is_segment else "{plane}"
        condition = "lasting_state.has_spell_magic_zones == true"
        if not is_segment:
            condition += " and teleport != true"
        guard = [
            _call(
                "check_srd_magic_zone_movement",
                {"subject": subject, "destination": "{destination}", "plane": plane},
                ("_magic_zone_movement_allowed",),
            ),
            _require(
                "_magic_zone_movement_allowed == true",
                "The actual source Forcecage blocks ordinary passage.",
            ),
        ]
        if not is_segment:
            guard = [_when("plane == _zone_segment_actor.metadata.plane", guard)]
        tool["effects"].insert(0, _when(condition, [*plane_effects, *guard]))
        changed[name] = tool
    return _replace_tools(text, changed)
