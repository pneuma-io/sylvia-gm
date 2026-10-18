"""A Humanoid genuinely slain by Life Drain rises from its same corpse after 24 hours."""

import re
from copy import deepcopy

if __package__:
    from .srd_divination_spells import _normalize
    from .srd_native_receiving_boundary import native_receiving_boundary
    from .srd_native_wight_actions import WIGHT_BLOCK, _validate_source
    from .srd_spell_summoning import (
        _choose,
        _scene_position,
        _undead_fortitude_tools,
        _walk,
        _zombie_actor_tools,
        _zombie_source_profile,
    )
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
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
else:
    from srd_divination_spells import _normalize
    from srd_native_receiving_boundary import native_receiving_boundary
    from srd_native_wight_actions import WIGHT_BLOCK, _validate_source
    from srd_spell_summoning import (
        _choose,
        _scene_position,
        _undead_fortitude_tools,
        _walk,
        _zombie_actor_tools,
        _zombie_source_profile,
    )
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
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools

ORIGIN = "wight_life_drain"
PUBLIC_TOOLS = {
    "admit_srd_native_wight_rise_language",
    "record_srd_native_wight_body_destruction",
}


def _read_tools():
    """Use one physical-body registry with distinct native lineage and no spell origin."""
    zombie = _zombie_source_profile()
    pending = _tool(
        "Read the exact retained death and Life Drain witness for this pending native rise.",
        [_entity("corpse_state")],
        [
            _require(
                "corpse_state.native_wight_rise_id != null and "
                "corpse_state.native_wight_rise_id in state.native_wight_rise_records",
                "This corpse has no actual Life Drain rise witness.",
            ),
            _set("_native_rise_id", "corpse_state.native_wight_rise_id"),
            _set("_native_rise", "state.native_wight_rise_records[_native_rise_id]"),
            _require(
                "_native_rise.body == _corpse_state_id and "
                "_native_rise.body_source_id == _corpse_state_id and "
                "_native_rise.attack == 'Life Drain' and "
                "_native_rise.controller_block_sha256 == " + repr(WIGHT_BLOCK),
                "Retain the actual Life Drain, original body and controller provenance.",
            ),
        ],
    )
    body = _tool(
        "Qualify this installed native Zombie and its retained real Wight controller.",
        [_entity("actor_state"), _optional("allow_zero", "boolean", False)],
        [
            _require(
                "actor_state.created_origin_kind == 'wight_life_drain' and "
                "actor_state.created_spell_id == null and "
                "actor_state.created_caster_state_id == null and "
                "actor_state.created_form_id == 'zombie' and actor_state.created_active == true "
                "and actor_state.created_individual_id == _actor_state_id and "
                "actor_state.is_alive == true and (actor_state.hp > 0 or allow_zero == true)",
                "Select the actual present native Zombie, not a spell-created alias.",
            ),
            _require(
                "actor_state.creature_type == 'undead' and "
                "actor_state.form_statistics_replaced != true and "
                "actor_state.current_body_source_id == null and "
                "actor_state.magic_jar_role == null and "
                "actor_state.created_source_block_sha256 == "
                + repr(zombie["source_stat_block_sha256"]),
                "Retain the complete actual printed native Zombie body.",
            ),
            _set("_native_zombie_record", "state.created_actor_records[_actor_state_id]"),
            _require(
                "_native_zombie_record.active == true and "
                "_native_zombie_record.individual == _actor_state_id and "
                "_native_zombie_record.spell == null and _native_zombie_record.caster == null "
                "and _native_zombie_record.native_origin == 'wight_life_drain' and "
                "_native_zombie_record.controller == actor_state.native_wight_controller_id",
                "Keep this real body's registry and native control lineage distinct from casting.",
            ),
            _require(
                "_native_zombie_record.body_source == actor_state.created_body_source_state_id "
                "and _native_zombie_record.body_source_id == actor_state.created_body_source_id "
                "and _native_zombie_record.source_block_sha256 == "
                "actor_state.created_source_block_sha256",
                "Keep this real body's registry and native lineage distinct from casting.",
            ),
            _set(
                "_native_zombie_rise",
                "state.native_wight_rise_records[actor_state.native_wight_rise_id]",
            ),
            _require(
                "_native_zombie_rise.status == 'risen' and "
                "_native_zombie_rise.zombie == _actor_state_id and "
                "_native_zombie_rise.controller == actor_state.native_wight_controller_id and "
                "_native_zombie_rise.body == actor_state.created_body_source_state_id and "
                "_native_zombie_rise.controller_block_sha256 == " + repr(WIGHT_BLOCK),
                "The actual risen individual must retain its original Life Drain witness.",
            ),
            _set("_native_wight_zombie_actor_id", "_actor_state_id"),
        ],
    )
    count_one = _tool(
        "Count a genuinely living Zombie with this exact native Wight control lineage.",
        [_entity("actor_state"), _entity("controller_state")],
        [
            _set("_native_wight_live_count", 0),
            _when(
                "actor_state.is_alive == true and "
                "actor_state.native_wight_controller_id == _controller_state_id",
                [
                    _call(
                        "qualify_srd_native_wight_zombie_body", {"actor_state": "{_actor_state_id}"}
                    ),
                    _set("_native_wight_live_count", 1),
                ],
            ),
        ],
    )
    count = _tool(
        "Count actual living native Zombies under this Wight's own control.",
        [_entity("controller_state")],
        [
            _set("_native_wight_controlled_count", 0),
            _when(
                "state.native_wight_zombie_ids != null",
                [
                    _set("_native_wight_count_ids", "state.native_wight_zombie_ids"),
                    _loop(
                        "_native_wight_count_ids",
                        "_native_wight_count_id",
                        [
                            _call(
                                "count_srd_native_wight_living_zombie",
                                {
                                    "actor_state": "{_native_wight_count_id}",
                                    "controller_state": "{_controller_state_id}",
                                },
                                ("_native_wight_live_count",),
                            ),
                            _set(
                                "_native_wight_controlled_count",
                                "_native_wight_controlled_count + _native_wight_live_count",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "read_srd_native_wight_rise": pending,
        "qualify_srd_native_wight_zombie_body": body,
        "count_srd_native_wight_living_zombie": count_one,
        "count_srd_native_wight_controlled_zombies": count,
    }


def _record_tools():
    """Record only a newly stamped death caused inside the owning Life Drain receiver."""
    record = _tool(
        "Retain this actual Humanoid death's native Life Drain rise witness.",
        [_entity("controller_state"), _entity("corpse_state")],
        [
            _call("qualify_srd_native_wight_body", {"actor_state": "{_controller_state_id}"}),
            _require(
                "corpse_state.is_alive == false and corpse_state.death_tick != null and "
                "corpse_state.death_tick == state.lasting_tick and "
                "corpse_state.death_creature_type == 'humanoid' and "
                "corpse_state.death_was_undead != true and "
                "corpse_state.current_body_source_id == null and "
                "corpse_state.magic_jar_role == null",
                "Record only this packet's actual native Humanoid death.",
            ),
            _when(
                "state.native_wight_rise_ids == null",
                [
                    _set("state.native_wight_rise_ids", []),
                    _set("state.native_wight_rise_records", {}),
                    _set("state.native_wight_rise_sequence", 0),
                ],
            ),
            _require(
                "len(state.native_wight_rise_ids) < 128",
                "Resolve retained native rises before adding more witnesses.",
            ),
            _when(
                "corpse_state.native_wight_rise_id != null",
                [
                    _set(
                        "_prior_native_rise",
                        "state.native_wight_rise_records[corpse_state.native_wight_rise_id]",
                    ),
                    _require(
                        "_prior_native_rise.active != true",
                        "One actual death may retain only one pending native rise.",
                    ),
                ],
            ),
            _set("state.native_wight_rise_sequence", "state.native_wight_rise_sequence + 1"),
            _call(
                "record_srd_result_text",
                {
                    "text": "wight_rise_{state.native_wight_rise_sequence}",
                },
                ("_recorded_result_text",),
            ),
            _set("_native_rise_id", "_recorded_result_text"),
            _set("_native_rise_ids", "state.native_wight_rise_ids"),
            _push("_native_rise_ids", "_native_rise_id"),
            _set("state.native_wight_rise_ids", "_native_rise_ids"),
            _set(
                "state.native_wight_rise_records.{_native_rise_id}",
                {
                    "body": "_corpse_state_id",
                    "body_source_id": "_corpse_state_id",
                    "controller": "_controller_state_id",
                    "controller_block_sha256": repr(WIGHT_BLOCK),
                    "attack": "'Life Drain'",
                    "death_tick": "corpse_state.death_tick",
                    "deadline": "corpse_state.death_tick + 28800",
                    "active": True,
                    "status": "'pending'",
                    "sequence": "state.native_wight_rise_sequence",
                    "other_language": None,
                    "zombie": None,
                },
            ),
            _set("corpse_state.native_wight_rise_id", "_native_rise_id"),
            _set("state.has_native_wight_rises", True),
        ],
    )
    language = _public(
        _tool(
            "Admit the future Zombie's printed other understood language, not rise eligibility.",
            [_entity("corpse_state"), _parameter("other_language")],
            [
                _call(
                    "read_srd_native_wight_rise",
                    {"corpse_state": "{_corpse_state_id}"},
                    ("_native_rise", "_native_rise_id"),
                ),
                _require(
                    "_native_rise.active == true and _native_rise.status == 'pending' and "
                    "corpse_state.is_alive == false and "
                    "corpse_state.death_tick == _native_rise.death_tick and "
                    "state.lasting_tick < _native_rise.deadline",
                    "Admit language only for this actual pending original death.",
                ),
                _require(
                    "len(other_language) >= 1 and len(other_language) <= 80 and "
                    "other_language != 'common'",
                    "Admit the printed one other understood language in 1 to 80 characters.",
                ),
                _set(
                    "state.native_wight_rise_records.{_native_rise_id}.other_language",
                    "other_language",
                ),
            ],
        ),
        "summoning",
        "Native Zombie language",
        [("Source", "_native_rise_id")],
    )
    destruction = _public(
        _tool(
            "Record observed destruction of this exact original corpse, without asserting death.",
            [_entity("corpse_state")],
            [
                _call(
                    "read_srd_native_wight_rise",
                    {"corpse_state": "{_corpse_state_id}"},
                    ("_native_rise", "_native_rise_id"),
                ),
                _require(
                    "_native_rise.active == true and _native_rise.status == 'pending' and "
                    "corpse_state.is_alive == false and "
                    "corpse_state.death_tick == _native_rise.death_tick and "
                    "corpse_state.body_animated_actor_id == null and "
                    "corpse_state.current_body_source_id == null and "
                    "corpse_state.magic_jar_role == null",
                    "Record physical destruction only for this unconsumed original dead body.",
                ),
                _set("corpse_state.body_available", False),
                _set("corpse_state.vital_parts_present", False),
                _set("state.native_wight_rise_records.{_native_rise_id}.body_destroyed", True),
                _set("state.native_wight_rise_records.{_native_rise_id}.active", False),
                _set(
                    "state.native_wight_rise_records.{_native_rise_id}.status",
                    "'cancelled_body_destroyed'",
                ),
            ],
        ),
        "summoning",
        "Original corpse destruction",
        [("Source", "_native_rise_id")],
    )
    return {
        "record_srd_native_wight_rise": record,
        "admit_srd_native_wight_rise_language": language,
        "record_srd_native_wight_body_destruction": destruction,
    }


def _revival_tool():
    """Cancel the witnessed original death permanently when its individual truly revives."""
    return _tool(
        "Cancel only this restored individual's exact pending original Life Drain death.",
        [_entity("target_state")],
        [
            _require(
                "target_state.is_alive == true and target_state.death_tick == null",
                "Cancel a pending native rise only after genuine life restoration.",
            ),
            _set("_restored_rise_id", "target_state.native_wight_rise_id"),
            _set("_restored_rise", "state.native_wight_rise_records[_restored_rise_id]"),
            _require(
                "_restored_rise.body == _target_state_id and "
                "_restored_rise.body_source_id == _target_state_id and "
                "_restored_rise.status == 'pending' and _restored_rise.active == true and "
                "_restored_rise.attack == 'Life Drain' and "
                "_restored_rise.controller_block_sha256 == " + repr(WIGHT_BLOCK),
                "Retain the exact pending native original-body witness.",
            ),
            _set("state.native_wight_rise_records.{_restored_rise_id}.active", False),
            _set(
                "state.native_wight_rise_records.{_restored_rise_id}.status",
                "'cancelled_restored_to_life'",
            ),
        ],
    )


def _constructors(tools):
    """Reuse the exact printed Zombie preparation while retaining a native controller."""
    original = _zombie_actor_tools(tools)
    prepare = deepcopy(original["prepare_srd_source_zombie"])
    finish = deepcopy(original["finish_srd_zombie_source_actor"])
    prepare["parameters"].append(_parameter("rise_id"))
    calls = [
        e for e in _walk(prepare["effects"]) if e.get("call") == "finish_srd_zombie_source_actor"
    ]
    if len(calls) != 1:
        raise ValueError("Preserve the one real source-stat preparation/physical-body finisher.")
    calls[0]["call"] = "finish_srd_native_wight_zombie"
    calls[0]["params"]["rise_id"] = "{rise_id}"
    prepare["description"] = (
        "Prepare the printed native Zombie from its original actual Life Drain corpse."
    )
    for e in _walk(prepare["effects"]):
        req = e.get("require", {})
        if "Animate Dead requires a Small or Medium" in req.get("error", ""):
            req["error"] = (
                "Only Small or Medium native Humanoid corpse appearance is supported for this rise."
            )
    finish["parameters"].append(_parameter("rise_id"))
    finish["effects"] = [
        e
        for e in finish["effects"]
        if not any(
            key in str(e)
            for key in (
                "actor_state.animated_",
                "state.animated_actor_ids",
                "_zombie_animated_ids",
                "state.has_animated_undead",
            )
        )
    ]
    for e in _walk(finish["effects"]):
        s = e.get("set", {})
        if s.get("field") in {
            "actor_state.created_spell_id",
            "actor_state.created_caster_state_id",
        }:
            s["value"] = None
        if s.get("field") in {
            "state.created_actor_records.{_actor_state_id}",
            "lasting_state.created_actor_records.{_actor_state_id}",
        }:
            s["value"].update(
                spell=None,
                caster=None,
                native_origin=repr(ORIGIN),
                controller="_caster_state_id",
                native_source_id="rise_id",
            )
    finish["effects"][:0] = [
        _require(
            "rise_id in state.native_wight_rise_records and "
            "state.native_wight_rise_records[rise_id].active == true and "
            "state.native_wight_rise_records[rise_id].body == _corpse_state_id and "
            "state.native_wight_rise_records[rise_id].controller == _caster_state_id",
            "Native construction must retain its actual pending Life Drain witness.",
        ),
    ]
    finish["effects"] += [
        _set("actor_state.created_origin_kind", repr(ORIGIN)),
        _set("actor_state.summon_active", False),
        _set("actor_state.summon_caster_state_id", None),
        _set("actor_state.native_wight_controller_id", "_caster_state_id"),
        _set("actor_state.native_wight_rise_id", "rise_id"),
        _when("state.native_wight_zombie_ids == null", [_set("state.native_wight_zombie_ids", [])]),
        _set("_native_wight_zombie_ids", "state.native_wight_zombie_ids"),
        _require(
            "len(_native_wight_zombie_ids) < 128",
            "Resolve retained native bodies before adding more.",
        ),
        _push("_native_wight_zombie_ids", "_actor_state_id"),
        _set("state.native_wight_zombie_ids", "_native_wight_zombie_ids"),
        _set("state.native_wight_rise_records.{rise_id}.zombie", "_actor_state_id"),
        _set("state.native_wight_rise_records.{rise_id}.status", "'risen'"),
        _set("state.native_wight_rise_records.{rise_id}.active", False),
    ]
    return {"prepare_srd_native_wight_zombie": prepare, "finish_srd_native_wight_zombie": finish}


def _scene_constructor(tools):
    """Reuse exact source-sized placement at the current corpse, without caster range fiction."""
    creator = deepcopy(tools["create_srd_source_actor_scene"])
    bounds = [
        i
        for i, e in enumerate(creator["effects"])
        if "full printed footprint" in e.get("require", {}).get("error", "")
    ]
    if not bounds:
        raise ValueError("Preserve the current source-volume bounds/placement/occupancy owners.")
    tail = creator["effects"][bounds[0] :]
    for e in _walk(tail):
        if e.get("set", {}).get("field") == "_created_actor_scene":
            e["set"]["value"]["metadata"]["source_caster"] = None
            e["set"]["value"]["metadata"]["native_controller"] = "_controller_state_id"
    creator["parameters"] = [
        _entity("corpse_state"),
        _entity("controller_state"),
        _parameter("summon_id"),
        _optional("lasting_state", resolve="entity", default="state"),
    ]
    creator["effects"] = [
        *_scene_position("{_corpse_state_id}", "_native_rise_origin"),
        _set("_native_rise_scene", "_info_scene_entity"),
        _require(
            "_native_rise_scene.metadata.participant_ref == _corpse_state_id and "
            "_native_rise_scene.footprint.kind == 'box' and "
            "_native_rise_scene.metadata.creature_type == 'humanoid' and "
            "_native_rise_scene.metadata.plane != null and _native_rise_scene.height > 0",
            "Use this original corpse actual box body, plane and admitted appearance height.",
        ),
        _require(
            "(corpse_state.inventory == null or len(corpse_state.inventory) == 0) and "
            "(corpse_state.equipment == null or len(corpse_state.equipment) == 0) and "
            "corpse_state.form_statistics_replaced != true and "
            "corpse_state.current_body_source_id == null "
            "and corpse_state.magic_jar_role == null",
            "Changed physical bodies and carried/worn corpse gear remain unsupported.",
        ),
        _set("created_actor_position", "_native_rise_origin"),
        _set("created_actor_height", "_native_rise_scene.height"),
        _set("created_actor_lighting", "_native_rise_scene.metadata.lighting"),
        _set("_created_actor_plane", "_native_rise_scene.metadata.plane"),
        _set("half_extent", 2.5),
        _set("creature_type", "'undead'"),
        _set("spell", repr(ORIGIN)),
        _call(
            "update_srd_spell_force_scene",
            {
                "force_id": "{_native_rise_scene.id}",
                "position": "{_native_rise_origin}",
                "active": False,
            },
        ),
        *tail,
    ]
    local_names = {
        "created_actor_position",
        "created_actor_height",
        "created_actor_lighting",
        "half_extent",
        "creature_type",
        "spell",
    }
    pattern = re.compile(r"(?<![.\w])(" + "|".join(sorted(local_names)) + r")(?!\w)")

    def localize(value):
        if isinstance(value, str):
            return pattern.sub(lambda match: "_native_" + match.group(), value)
        if isinstance(value, list):
            return [localize(item) for item in value]
        if isinstance(value, dict):
            return {key: localize(item) for key, item in value.items()}
        return value

    creator["effects"] = localize(creator["effects"])
    return creator


def _deadline_tools():
    """Cancel restored originals and rise only from an available body at the exact boundary."""
    settle = _tool(
        "Resolve this real native rise deadline or actual original-body cancellation.",
        [_entity("corpse_state")],
        [
            _call(
                "read_srd_native_wight_rise",
                {"corpse_state": "{_corpse_state_id}"},
                ("_native_rise", "_native_rise_id"),
            ),
            _when(
                "_native_rise.active == true",
                [
                    _choose(
                        "corpse_state.is_alive == true or corpse_state.death_tick != "
                        "_native_rise.death_tick",
                        [
                            _set("state.native_wight_rise_records.{_native_rise_id}.active", False),
                            _set(
                                "state.native_wight_rise_records.{_native_rise_id}.status",
                                "'cancelled_original_body'",
                            ),
                        ],
                        [
                            _when(
                                "state.lasting_tick >= _native_rise.deadline",
                                [
                                    _require(
                                        "state.lasting_tick == _native_rise.deadline",
                                        "Advance to the actual native rise deadline; "
                                        "overdue chronology "
                                        "is unsupported.",
                                    ),
                                    _require(
                                        "corpse_state.body_available == true and "
                                        "corpse_state.body_animated_actor_id == null",
                                        "Join the actual available original body at its deadline; "
                                        "unavailability alone does not prove destruction.",
                                    ),
                                    _require(
                                        "_native_rise.other_language != null",
                                        "Admit this printed Zombie other understood "
                                        "language before its "
                                        "deadline.",
                                    ),
                                    _require(
                                        "combat.encounter_active != true and "
                                        "combat.turn_active != true",
                                        "Join actual independent Initiative before "
                                        "rising inside an "
                                        "existing encounter.",
                                    ),
                                    _call(
                                        "qualify_srd_native_wight_body",
                                        {"actor_state": "{_native_rise.controller}"},
                                    ),
                                    _call(
                                        "count_srd_native_wight_controlled_zombies",
                                        {"controller_state": "{_native_rise.controller}"},
                                        ("_native_wight_controlled_count",),
                                    ),
                                    _require(
                                        "_native_wight_controlled_count < 12",
                                        "This actual Wight already controls twelve "
                                        "living native Zombies; "
                                        "over-cap scheduling is unsupported.",
                                    ),
                                    _call(
                                        "record_srd_result_text",
                                        {"text": "wight_zombie_{_native_rise.sequence}"},
                                        ("_recorded_result_text",),
                                    ),
                                    _set("_native_rise_zombie", "_recorded_result_text"),
                                    _call(
                                        "create_srd_native_wight_zombie_scene",
                                        {
                                            "corpse_state": "{_corpse_state_id}",
                                            "controller_state": "{_native_rise.controller}",
                                            "summon_id": "{_native_rise_zombie}",
                                        },
                                    ),
                                    _call(
                                        "prepare_srd_native_wight_zombie",
                                        {
                                            "creature_id": "{_native_rise_zombie}",
                                            "corpse_state": "{_corpse_state_id}",
                                            "source_caster": "{_native_rise.controller}",
                                            "other_language": "{_native_rise.other_language}",
                                            "rise_id": "{_native_rise_id}",
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
    refresh = _tool(
        "Resolve actual pending native Life Drain bodies against the real clock.",
        [],
        [
            _set("_native_rise_pending", []),
            _set("_native_rise_all_ids", "state.native_wight_rise_ids"),
            _loop(
                "_native_rise_all_ids",
                "_native_rise_refresh_id",
                [
                    _set(
                        "_native_rise_refresh",
                        "state.native_wight_rise_records[_native_rise_refresh_id]",
                    ),
                    _when(
                        "_native_rise_refresh.active == true",
                        [
                            _call(
                                "settle_srd_native_wight_rise",
                                {"corpse_state": "{_native_rise_refresh.body}"},
                            ),
                            _when(
                                "state.native_wight_rise_records[_native_rise_refresh_i"
                                "d].active "
                                "== true",
                                [
                                    _push("_native_rise_pending", "_native_rise_refresh_id"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("state.has_native_wight_rises", "len(_native_rise_pending) > 0"),
        ],
    )
    return {"settle_srd_native_wight_rise": settle, "refresh_srd_native_wight_rises": refresh}


def _rise_packet(packet):
    """Keep the passed real Life Drain packet and witness its actual newly stamped death."""
    packet = deepcopy(packet)
    if "record_srd_native_wight_rise" in str(packet["effects"]):
        return packet
    boundary = native_receiving_boundary(packet["effects"])
    boundary.ordinary[boundary.completion_index : boundary.completion_index] = [
        _when(
            "_native_rise_was_alive == true and _native_rise_was_type == 'humanoid' and "
            "_native_rise_old_death == null and target.is_alive == false and "
            "target.death_tick != null and target.death_creature_type == 'humanoid'",
            [
                _call(
                    "record_srd_native_wight_rise",
                    {
                        "controller_state": "{source_state_id}",
                        "corpse_state": "{target}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                ),
            ],
        ),
    ]
    boundary.ordinary[boundary.hp_before_index + 1 : boundary.hp_before_index + 1] = [
        _set("_native_rise_was_alive", "target.is_alive"),
        _set("_native_rise_was_type", "target.creature_type"),
        _set("_native_rise_old_death", None),
        _when("target.death_tick != null", [_set("_native_rise_old_death", "target.death_tick")]),
    ]

    # A runtime identifier is formatted by the existing text owner, not an unsupported str call.
    def replace(effects):
        for i, e in enumerate(effects):
            setting = e.get("set", {})
            if (
                setting.get("field") == "_wight_maximum_receipt_id"
                and isinstance(setting.get("value"), str)
                and "str(" in setting["value"]
            ):
                if (
                    setting["value"]
                    != "'wight_life_drain_' + str(lasting_state.native_undead_maximum_sequence)"
                ):
                    raise ValueError("Do not rewrite unrelated source receipt expressions.")
                effects[i : i + 1] = [
                    _call(
                        "record_srd_result_text",
                        {"text": "wight_life_drain_{lasting_state.native_undead_maximum_sequence}"},
                        ("_recorded_result_text",),
                    ),
                    _set("_wight_maximum_receipt_id", "_recorded_result_text"),
                ]
            for key in ("on_true", "on_false"):
                replace(e.get(key, []))
            replace(e.get("for_each", {}).get("do", []))

    replace(packet["effects"])
    return packet


def _fortitude(tools):
    """Delegate native Fortitude to the same actual saved CON owner and typed lethal receipt."""
    fort = deepcopy(_undead_fortitude_tools()["resolve_srd_created_undead_fortitude"])
    fort["effects"][:0] = [
        _call(
            "qualify_srd_native_wight_zombie_body",
            {"actor_state": "{_target_state_id}", "allow_zero": True},
        ),
    ]
    for e in _walk(fort["effects"]):
        req = e.get("require", {})
        if req:
            req["condition"] = (
                req["condition"]
                .replace("created_spell_id == 'animate_dead'", "created_spell_id == null")
                .replace("_undead_record.spell == 'animate_dead'", "_undead_record.spell == null")
            )
    return fort


def _bind_lethal(tool, composite, qualified, *, native_fist=False):
    tool = deepcopy(tool)
    boundary = native_receiving_boundary(tool["effects"], native_fist=native_fist)
    lethal = [boundary.monster_zero]
    if "resolve_srd_native_wight_zombie_fortitude" in str(lethal[0]):
        return tool
    radiant = "damage_type == 'radiant' and _effective_damage > 0"
    prefix = []
    if composite:
        radiant = (
            "(component_1_damage_type == 'radiant' and "
            "_component_1_effective_damage > 0) or (component_2_damage_type "
            "== 'radiant' and _component_2_effective_damage > 0)"
        )
    elif qualified:
        receipts = [
            e
            for e in tool["effects"]
            if e.get("condition")
            and any(
                r.get("set", {}).get("field") == "_source_base_effective_damage"
                for r in _walk(e.get("on_true", []))
            )
        ]
        if len(receipts) != 1:
            raise ValueError("Retain the authentic qualified scalar base typed receipt.")
        prefix = [
            _set("_native_zombie_base_received", "_effective_damage"),
            _when(
                receipts[0]["condition"],
                [_set("_native_zombie_base_received", "_source_base_effective_damage")],
            ),
        ]
        radiant = "damage_type == 'radiant' and _native_zombie_base_received > 0"
    callback = _when(
        "_hp_before > 0 and _effective_damage > 0 and target.is_alive == true and "
        "target.created_origin_kind == 'wight_life_drain' and "
        "target.created_spell_id == null and target.form_statistics_replaced != true",
        [
            *prefix,
            _call(
                "resolve_srd_native_wight_zombie_fortitude",
                {
                    "target_state": "{target}",
                    "damage_taken": "{_effective_damage}",
                    "radiant_damage": "{" + radiant + "}",
                    "critical_hit": "{critical_hit}",
                },
            ),
        ],
    )
    suffix = [
        i for i, e in enumerate(lethal[0]["on_true"]) if e.get("condition") == "target.hp == 0"
    ]
    if len(suffix) != 1:
        raise ValueError("Preserve the actual existing still-zero death/Fortitude suffix.")
    lethal[0]["on_true"].insert(suffix[0], callback)
    return tool


def _bind_revival_clear(effects, cleared):
    """Cancel the old rise at genuine revival, before a same-tick later death can reuse it."""
    for index, effect in enumerate(effects):
        setting = effect.get("set", {})
        if setting.get("field") == "target_state.death_tick" and setting.get("value") is None:
            effects.insert(
                index + 1,
                _when(
                    "target_state.native_wight_rise_id != null and "
                    "lasting_state.native_wight_rise_records != null and "
                    "target_state.native_wight_rise_id in "
                    "lasting_state.native_wight_rise_records and "
                    "lasting_state.native_wight_rise_records"
                    "[target_state.native_wight_rise_id].active == true",
                    [
                        _call(
                            "cancel_srd_native_wight_rise_after_revival",
                            {
                                "target_state": "{_target_state_id}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                        )
                    ],
                ),
            )
            cleared.append(setting)
        for key in ("on_true", "on_false"):
            _bind_revival_clear(effect.get(key, []), cleared)
        _bind_revival_clear(effect.get("for_each", {}).get("do", []), cleared)


def bind_native_wight_rise_consumers(text):
    """Refresh the real Life Drain packet and native lethal owners before final reaction clones."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    packet = _rise_packet(tools["apply_srd_native_wight_life_drain_packet"])
    if packet != tools["apply_srd_native_wight_life_drain_packet"]:
        changed["apply_srd_native_wight_life_drain_packet"] = packet
    for name, composite, qualified in (
        ("apply_srd_damage", False, False),
        ("apply_srd_composite_damage", True, False),
        ("apply_srd_qualifying_damage", False, True),
        ("apply_srd_qualifying_composite_damage", True, True),
        ("apply_srd_native_mummy_fist_packet", True, True),
        ("apply_srd_native_wight_life_drain_packet", False, True),
    ):
        if name not in tools:
            continue
        selected = name
        core = (
            "execute_srd_qualified_composite_packet"
            if composite
            else "execute_srd_qualified_scalar_packet"
        )
        if qualified and tools[name]["effects"][0].get("call") == core:
            if __package__:
                from .srd_qualified_packet_implementations import _delegate
            else:
                from srd_qualified_packet_implementations import _delegate
            expected = _delegate(tools[name], core, True, [])["effects"][0]
            if tools[name]["effects"][0] != expected or core not in tools:
                raise ValueError("Bind only this actual qualified packet's exact named delegate.")
            selected = core
        tool = _bind_lethal(
            changed.get(selected, tools[selected]),
            composite,
            qualified,
            native_fist=name == "apply_srd_native_mummy_fist_packet",
        )
        if tool != tools[selected]:
            changed[selected] = tool
    for name in ("resolve_srd_revival", "complete_srd_reincarnation_internal"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        if "cancel_srd_native_wight_rise_after_revival" in str(tool["effects"]):
            continue
        cleared = []

        _bind_revival_clear(tool["effects"], cleared)
        if len(cleared) != 1:
            raise ValueError("Preserve this actual revival owner's one genuine death-time clear.")
        changed[name] = tool
    _normalize(changed)
    return _replace_tools(text, changed) if changed else text


def extend_runtime(text):
    """Compose only actual native rise, body/placement and ordinary NPC attack qualification."""
    _validate_source()
    source = _zombie_source_profile()
    assert (
        source["source_stat_block_sha256"]
        == "d0fb38eb23ca51a76be136e4e40fb0bb9fdf837fea094c4823d52ef930bebab4"
    )
    tools = _load_runtime(text)["tools"]
    changed = _read_tools() | _record_tools() | _constructors(tools) | _deadline_tools()
    changed["create_srd_native_wight_zombie_scene"] = _scene_constructor(tools)
    changed["resolve_srd_native_wight_zombie_fortitude"] = _fortitude(tools)
    changed["cancel_srd_native_wight_rise_after_revival"] = _revival_tool()
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_native_wight_rises" not in str(clock["effects"]):
        clock["effects"].append(
            _when(
                "lasting_state.has_native_wight_rises == true",
                [
                    _call(
                        "refresh_srd_native_wight_rises", {"lasting_state": "{_lasting_state_id}"}
                    ),
                ],
            )
        )
    changed["advance_srd_lasting_clock"] = clock
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    if "qualify_srd_native_wight_zombie_body" not in str(attack["effects"]):
        attack["effects"].insert(
            0,
            _when(
                "actor.created_origin_kind == 'wight_life_drain' and "
                "actor.form_attack_in_progress != true",
                [
                    _call("qualify_srd_native_wight_zombie_body", {"actor_state": "{_actor_id}"}),
                    _call(
                        "qualify_srd_native_wight_body",
                        {"actor_state": "{actor.native_wight_controller_id}"},
                    ),
                    _require(
                        "attack_name == 'Slam' and attack_mode == 'melee' and "
                        "damage_expression == 'd8' and damage_modifier == 1 and "
                        "damage_type == 'bludgeoning' and "
                        "attack_modifier == 3 and normal_range == 5 and long_range == 5 "
                        "and use_multiattack != true and attack_uses_weapon != true",
                        "Use only this native Zombie genuine printed Slam.",
                    ),
                    _call(
                        "qualify_srd_created_actor_attack_geometry",
                        {
                            "actor_state": "{_actor_id}",
                            "target_state": "{_target_id}",
                        },
                        ("_created_attack_distance",),
                    ),
                    _require(
                        "attack_distance == _created_attack_distance",
                        "Use this real native body attack distance.",
                    ),
                ],
            ),
        )
    changed["resolve_adjudicated_creature_attack"] = attack
    _normalize(changed)
    changed = {n: t for n, t in changed.items() if tools.get(n) != t}
    return _replace_tools(text, changed) if changed else text
