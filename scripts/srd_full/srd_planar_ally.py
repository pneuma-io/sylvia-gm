"""Planar Ally relocates a real autonomous creature and retains its negotiated service."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import yaml

if __package__:
    from .srd_divination_spells import PENDING, _normalize
    from .srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
    from .srd_spell_summoning import _choose, _replace_created_cast_dispatch
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
else:
    from srd_divination_spells import PENDING, _normalize
    from srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
    from srd_spell_summoning import _choose, _replace_created_cast_dispatch
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools

PAGE_HASH = "23fb8caafdffe01e848971a81b3955360ef9c2e76c354485695fe4fd32af0e5a"
PROFILE = {
    "family": "planar_ally_service",
    "base_level": 6,
    "casting_time": "10_minutes",
    "range_kind": "distance",
    "range_feet": 60,
    "concentration": False,
}
PUBLIC_TOOLS = {
    "record_srd_planar_patron",
    "negotiate_srd_planar_ally",
    "complete_srd_planar_ally_task",
    "dodge_srd_planar_ally",
}
CAST_CHOICES = [
    _optional("planar_patron", default=""),
    _optional("planar_ally", default=""),
    _optional("requested_ally_name", default=""),
    _optional("planar_ally_position", "array", [], items={"type": "number"}, max_items=3),
]


def validate_profiles(definitions):
    """Bind the complete spell text and reject unreviewed fixed profile changes."""
    pages = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "rulesets/dnd-srd-5.2.1/content/corpus/spells.json"
        ).read_text()
    )
    page = next(p for p in pages["page_records"] if p["page"] == 152)
    if (
        page["text_sha256"] != PAGE_HASH
        or hashlib.sha256(page["text"].encode()).hexdigest() != PAGE_HASH
    ):
        raise ValueError("Planar Ally must retain its exact pinned source page.")
    for spell, profile in definitions.items():
        if profile.get("family") == PROFILE["family"] and (
            spell != "planar_ally" or profile != PROFILE
        ):
            raise ValueError("Planar Ally fixed source profile differs.")


def _scene(subject, local):
    return [
        _call("srd_information_scene_entity", {"subject": subject}, POSITION_EXPORTS),
        _set(local, "_info_scene_entity"),
    ]


def _body_guard():
    return [
        _require(
            "ally_state.creature_kind == 'monster' and ally_state.source != null and "
            "len(ally_state.source) > 0",
            "Select a genuine already-prepared NPC and retain its stat source.",
        ),
        _require(
            "ally_state.is_alive == true and ally_state.hp > 0 and "
            "(ally_state.creature_type == 'celestial' or ally_state.creature_type == 'elemental' "
            "or ally_state.creature_type == 'fiend')",
            "The sent creature must be a living Celestial, Elemental or Fiend.",
        ),
        _require(
            "ally_state.form_statistics_replaced != true and "
            "ally_state.current_body_source_id == null "
            "and ally_state.magic_jar_source_id == null",
            "Join replacement or transferred-body Planar Ally identity before using it.",
        ),
    ]


def _tools():
    body = _tool_scene(
        "Qualify the actual sent individual without altering its native statistics.",
        [_entity("ally_state")],
        _body_guard()
        + _scene("{_ally_state_id}", "_ally_body")
        + [
            _require(
                "_ally_body.metadata.participant_ref == _ally_state_id and "
                "_ally_body.metadata.plane != null and _ally_body.footprint.kind == 'box'",
                "Select one actual typed creature body, plane and supported box footprint.",
            ),
        ],
    )
    patron = _public(
        _tool(
            "Record a cosmic patron known to this caster and its actual loyal "
            "creatures (GM fiction).",
            [
                _entity("caster_state"),
                _parameter("patron_id"),
                _parameter("patron_name"),
                _parameter(
                    "loyal_actor_ids",
                    "array",
                    items={"type": "string"},
                    unique_items=True,
                    min_items=1,
                    max_items=64,
                ),
            ],
            [
                _require(PENDING, "Complete the pending action before recording patron facts."),
                _require(
                    "patron_id != '' and len(patron_id) <= 128 and '.' not in patron_id and "
                    "'{' not in patron_id and '}' not in patron_id and patron_name != ''",
                    "Name the actual known cosmic entity.",
                ),
                _loop(
                    "loyal_actor_ids",
                    "_patron_loyal_actor",
                    [
                        _call(
                            "qualify_srd_planar_ally_body", {"ally_state": "{_patron_loyal_actor}"}
                        ),
                    ],
                ),
                _when(
                    "caster_state.planar_known_patrons == null",
                    [_set("caster_state.planar_known_patrons", {})],
                ),
                _set(
                    "caster_state.planar_known_patrons.{patron_id}",
                    {
                        "name": "patron_name",
                        "loyal_actor_ids": "loyal_actor_ids",
                    },
                ),
                _set("_ally_patron_name", "patron_name"),
            ],
        ),
        "exploration",
        "Known cosmic patron",
        [("Patron", "_ally_patron_name")],
    )
    record = _tool(
        "Read the exact caster-owned service on the retained actual creature.",
        [_entity("caster_state"), _entity("ally_state")],
        [
            _require(
                "ally_state.planar_ally_service_id != null and state.planar_ally_records != null",
                "This actual creature has no Planar Ally service.",
            ),
            _set("_ally_service_id", "ally_state.planar_ally_service_id"),
            _require(
                "_ally_service_id in state.planar_ally_records",
                "The actual service source is missing.",
            ),
            _set("_ally_service", "state.planar_ally_records[_ally_service_id]"),
            _require(
                "_ally_service.spell == 'planar_ally' and _ally_service.ally == _ally_state_id "
                "and _ally_service.caster == _caster_state_id and "
                "_ally_service.source_page_sha256 == '" + PAGE_HASH + "' and "
                "_ally_service.active == true",
                "The exact individual, caster and active source must agree.",
            ),
        ],
    )
    relocate = _tool_scene(
        "Relocate the retained actual body and qualify its complete unoccupied space.",
        [_entity("ally_state"), _array("destination"), _parameter("plane")],
        [
            _call(
                "resolve_srd_travel_placement",
                {
                    "subject": "{_ally_state_id}",
                    "destination": "{destination}",
                    "plane": "{plane}",
                    "excluded": [],
                },
                ["_travel_placement"],
            ),
            _require(
                "_travel_placement.distance == 0",
                "The selected space must fit the actual creature's full body.",
            ),
            _call(
                "move_srd_travel_subject",
                {
                    "subject": "{_ally_state_id}",
                    "destination": "{destination}",
                    "plane": "{plane}",
                    "teleport": True,
                },
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_ally_state_id}"}, POSITION_EXPORTS
            ),
            _call("qualify_srd_created_actor_unoccupied", {"summon_id": "{_info_scene_entity.id}"}),
        ],
    )
    cast = _tool_scene(
        "Resolve Planar Ally's paid spell with one real independently acting creature.",
        [_entity("caster_state"), *deepcopy(CAST_CHOICES)],
        [
            _require(
                "planar_patron != '' and planar_ally != '' and len(planar_ally_position) == 3",
                "Choose a known patron, its actual sent creature and unoccupied appearance point.",
            ),
            _require(
                "caster_state.planar_known_patrons != null and "
                "planar_patron in caster_state.planar_known_patrons",
                "The cosmic entity must actually be known to this caster.",
            ),
            _set("_ally_patron", "caster_state.planar_known_patrons[planar_patron]"),
            _require(
                "planar_ally in _ally_patron.loyal_actor_ids",
                "The GM-selected real creature must be loyal to the selected patron.",
            ),
            _call("qualify_srd_planar_ally_body", {"ally_state": "{planar_ally}"}, ["_ally_body"]),
            _set("_ally_home", "_ally_body"),
            _scene("{_caster_state_id}", "_ally_cast_body")[0],
            _set("_ally_cast_body", "_info_scene_entity"),
            _require("_ally_cast_body.metadata.plane != null", "Use the caster's actual plane."),
            _call(
                "measure_srd_information_distance",
                {"start": "{_ally_cast_body.position}", "end": "{planar_ally_position}"},
                ["_scene_distance"],
            ),
            _require(
                "_scene_distance <= 60", "The actual appearance point must be within 60 feet."
            ),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_ally_cast_body.position}",
                    "end": "{planar_ally_position}",
                    "rule": "solid",
                },
                ["_information_blocked"],
            ),
            _require(
                "_information_blocked != true",
                "The appearance point requires an actual clear path.",
            ),
            _call(
                "install_srd_planar_ally_service",
                {
                    "caster_state": "{_caster_state_id}",
                    "ally_state": "{planar_ally}",
                    "patron_id": "{planar_patron}",
                    "requested_name": "{requested_ally_name}",
                    "destination": "{planar_ally_position}",
                    "plane": "{_ally_cast_body.metadata.plane}",
                },
                ["_ally_service_id", "_ally_actual_name"],
            ),
            _set(
                "_spell_summary_rows",
                [
                    {"label": "'Sent creature'", "value": "_ally_actual_name"},
                    {"label": "'Service'", "value": "'Autonomous; awaiting agreement'"},
                ],
            ),
            _set(
                "_spell_detail_rows",
                [
                    {"label": "'Planar Ally source'", "value": "_ally_service_id"},
                    {"label": "'Requested name'", "value": "requested_ally_name"},
                ],
            ),
        ],
    )
    install = _tool_scene(
        "Save the real creature's home and install only autonomous service provenance.",
        [
            _entity("caster_state"),
            _entity("ally_state"),
            _parameter("patron_id"),
            _optional("requested_name", default=""),
            _array("destination"),
            _parameter("plane"),
        ],
        [
            _call(
                "qualify_srd_planar_ally_body", {"ally_state": "{_ally_state_id}"}, ["_ally_body"]
            ),
            _require(
                "ally_state.planar_ally_service_id == null",
                "Finish the existing service before recalling this individual.",
            ),
            _set("_ally_home_position", "_ally_body.position"),
            _require(
                "_ally_body.metadata.home_plane != null and "
                "_ally_body.metadata.home_plane == _ally_body.metadata.plane",
                "This existing-actor subset requires its actual recorded home plane "
                "and a body presently at home.",
            ),
            _set("_ally_home_plane", "_ally_body.metadata.home_plane"),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when(
                "state.planar_ally_records == null",
                [
                    _set("state.planar_ally_records", {}),
                    _set("state.planar_ally_ids", []),
                    _set("state.planar_ally_sequence", 0),
                ],
            ),
            _set("state.planar_ally_sequence", "state.planar_ally_sequence + 1"),
            _call(
                "record_srd_result_text",
                {"text": "planar_ally_{state.planar_ally_sequence}"},
                ["_recorded_result_text"],
            ),
            _set("_ally_service_id", "_recorded_result_text"),
            _call(
                "relocate_srd_planar_ally",
                {
                    "ally_state": "{_ally_state_id}",
                    "destination": "{destination}",
                    "plane": "{plane}",
                },
            ),
            _set(
                "state.planar_ally_records.{_ally_service_id}",
                {
                    "spell": "'planar_ally'",
                    "source_page_sha256": "'" + PAGE_HASH + "'",
                    "caster": "_caster_state_id",
                    "ally": "_ally_state_id",
                    "patron": "patron_id",
                    "active": True,
                    "status": "'negotiating'",
                    "started_tick": "state.lasting_tick",
                    "home_position": "_ally_home_position",
                    "home_plane": "_ally_home_plane",
                    "requested_name": "requested_name",
                    "actual_name": "ally_state.name",
                    "deadline": None,
                    "task": None,
                    "agreement": None,
                    "report": None,
                },
            ),
            _set("ally_state.planar_ally_service_id", "_ally_service_id"),
            _set("_ally_actual_name", "ally_state.name"),
            _set("_ally_active_ids", "state.planar_ally_ids"),
            _push("_ally_active_ids", "_ally_service_id"),
            _set("state.planar_ally_ids", "_ally_active_ids"),
            _set("state.has_planar_allies", True),
        ],
    )
    communicate = _tool_scene(
        "Qualify actual perceived two-way language before the autonomous bargain.",
        [
            _entity("caster_state"),
            _entity("ally_state"),
            _parameter("language"),
            _parameter("channel", "enum", enum_values=["speech", "signs"]),
        ],
        [
            _require(
                "caster_state.is_alive == true and ally_state.is_alive == true and "
                "'unconscious' not in caster_state.conditions and 'unconscious' "
                "not in ally_state.conditions",
                "Both actual creatures must be able to communicate intentionally.",
            ),
            *_scene("{_caster_state_id}", "_ally_speaker"),
            *_scene("{_ally_state_id}", "_ally_listener"),
            _require(
                "_ally_speaker.metadata.plane != null and "
                "_ally_speaker.metadata.plane == _ally_listener.metadata.plane",
                "This ordinary conversation needs the same actual plane.",
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_ally_speaker.position}", "end": "{_ally_listener.position}"},
                ["_scene_distance"],
            ),
            _require(
                "_scene_distance <= 60",
                "Join remote communication before bargaining beyond this bounded "
                "ordinary conversation.",
            ),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_ally_speaker.position}",
                    "end": "{_ally_listener.position}",
                    "rule": "solid",
                },
                ["_information_blocked"],
            ),
            _require(
                "_information_blocked != true",
                "Join obstructed speech or signing before this bargain.",
            ),
            _choose(
                "channel == 'speech'",
                [
                    *[
                        _call(
                            "resolve_srd_environment_point",
                            {
                                "point": "{" + who + ".position}",
                                "plane": "{_ally_speaker.metadata.plane}",
                            },
                            ["_environment_silence"],
                        )
                        for who in ("_ally_speaker",)
                    ],
                    _require(
                        "_environment_silence != true",
                        "The actual speaker cannot speak in Silence.",
                    ),
                    _call(
                        "resolve_srd_environment_point",
                        {
                            "point": "{_ally_listener.position}",
                            "plane": "{_ally_speaker.metadata.plane}",
                        },
                        ["_environment_silence"],
                    ),
                    _require(
                        "_environment_silence != true",
                        "The actual reply cannot be spoken in Silence.",
                    ),
                    _call(
                        "resolve_srd_environment_segment",
                        {
                            "start": "{_ally_speaker.position}",
                            "end": "{_ally_listener.position}",
                            "plane": "{_ally_speaker.metadata.plane}",
                        },
                        ["_environment_sound_blocked"],
                    ),
                    _require(
                        "_environment_sound_blocked != true",
                        "Silence blocks the actual conversation's sound path.",
                    ),
                ],
                [
                    _call(
                        "resolve_srd_scene_relation",
                        {"actor_state": "{_caster_state_id}", "target_state": "{_ally_state_id}"},
                        ["_scene_can_see"],
                    ),
                    _require(
                        "_scene_can_see == true",
                        "The caster must actually see the signing creature.",
                    ),
                    _call(
                        "resolve_srd_scene_relation",
                        {"actor_state": "{_ally_state_id}", "target_state": "{_caster_state_id}"},
                        ["_scene_can_see"],
                    ),
                    _require(
                        "_scene_can_see == true",
                        "The sent creature must actually see the caster's signs.",
                    ),
                ],
            ),
            *[
                _call(
                    "resolve_srd_spell_language",
                    {
                        "speaker_state": "{" + speaker + "}",
                        "listener_state": "{" + listener + "}",
                        "channel": "{channel}",
                        "language": "{language}",
                        "perceived": True,
                    },
                    ["_language_understands"],
                )
                if step == 0
                else _require(
                    "_language_understands == true",
                    "Both actual creatures must understand the chosen language.",
                )
                for speaker, listener in (
                    ("_caster_state_id", "_ally_state_id"),
                    ("_ally_state_id", "_caster_state_id"),
                )
                for step in range(2)
            ],
        ],
    )
    return_home = _tool_scene(
        "Return this actual autonomous individual to its retained home plane.",
        [
            _entity("caster_state"),
            _entity("ally_state"),
            _parameter("reason"),
            _optional("report", default=""),
        ],
        [
            _call(
                "read_srd_planar_ally_service",
                {"caster_state": "{_caster_state_id}", "ally_state": "{_ally_state_id}"},
                ["_ally_service", "_ally_service_id"],
            ),
            _body_guard()[1],
            _call(
                "relocate_srd_planar_ally",
                {
                    "ally_state": "{_ally_state_id}",
                    "destination": "{_ally_service.home_position}",
                    "plane": "{_ally_service.home_plane}",
                },
            ),
            _set(
                "_ally_return_patch",
                {
                    "active": False,
                    "status": "'returned'",
                    "ended_tick": "state.lasting_tick",
                    "end_reason": "reason",
                    "report": "report",
                },
            ),
            _set(
                "state.planar_ally_records.{_ally_service_id}",
                "merge(_ally_service, _ally_return_patch)",
            ),
            _set("ally_state.planar_ally_service_id", None),
            _set("_ally_remaining_ids", []),
            _set("_ally_old_ids", "state.planar_ally_ids"),
            _loop(
                "_ally_old_ids",
                "_ally_old_id",
                [
                    _when(
                        "_ally_old_id != _ally_service_id",
                        [_push("_ally_remaining_ids", "_ally_old_id")],
                    )
                ],
            ),
            _set("state.planar_ally_ids", "_ally_remaining_ids"),
            _set("state.has_planar_allies", "len(_ally_remaining_ids) > 0"),
            _set("_ally_returned", True),
        ],
    )
    negotiate = _public(
        _tool(
            "Record the GM's autonomous agreement; calculate source fees "
            "without inventing payment or task success.",
            [
                _entity("caster_state"),
                _entity("ally_state"),
                _parameter("decision", "enum", enum_values=["agree", "no_agreement"]),
                _optional("language", default=""),
                _optional("channel", "enum", "speech", enum_values=["speech", "signs"]),
                _optional("task", default=""),
                _optional("unit", "enum", "minute", enum_values=["minute", "hour", "day"]),
                _optional("quantity", "integer", 1, minimum=1, maximum=10000),
                _optional("agreed_gp", "integer", -1, minimum=-1, maximum=100000000),
                _optional("adjustment_reason", default=""),
                _optional("agreement_statement", default=""),
                _optional("payment_timing", "enum", "after", enum_values=["after", "waived"]),
            ],
            [
                _require(PENDING, "Complete the pending action before bargaining."),
                _call(
                    "read_srd_planar_ally_service",
                    {"caster_state": "{_caster_state_id}", "ally_state": "{_ally_state_id}"},
                    ["_ally_service", "_ally_service_id"],
                ),
                _require(
                    "_ally_service.status == 'negotiating'",
                    "This exact service already has its agreed terms.",
                ),
                _set("_ally_suggested_gp", None),
                _set("_ally_agreed_gp", None),
                _set("_ally_service_deadline", None),
                _choose(
                    "decision == 'no_agreement'",
                    [
                        _call(
                            "return_srd_planar_ally_home",
                            {
                                "caster_state": "{_caster_state_id}",
                                "ally_state": "{_ally_state_id}",
                                "reason": "no_agreement",
                            },
                        ),
                        _set("_ally_outcome", "'No agreement; creature returned home'"),
                    ],
                    [
                        _call(
                            "qualify_srd_planar_ally_conversation",
                            {
                                "caster_state": "{_caster_state_id}",
                                "ally_state": "{_ally_state_id}",
                                "language": "{language}",
                                "channel": "{channel}",
                            },
                        ),
                        _require(
                            "task != '' and agreement_statement != ''",
                            "Retain the actual requested task and autonomous agreement as GM "
                            "fiction.",
                        ),
                        _require(
                            "unit != 'day' or quantity <= 10",
                            "The printed daily service quote covers up to ten days.",
                        ),
                        _set("_ally_rate", 100),
                        _set("_ally_unit_ticks", 20),
                        _when(
                            "unit == 'hour'",
                            [_set("_ally_rate", 1000), _set("_ally_unit_ticks", 1200)],
                        ),
                        _when(
                            "unit == 'day'",
                            [_set("_ally_rate", 10000), _set("_ally_unit_ticks", 28800)],
                        ),
                        _set("_ally_suggested_gp", "quantity * _ally_rate"),
                        _set("_ally_agreed_gp", "_ally_suggested_gp"),
                        _when("agreed_gp >= 0", [_set("_ally_agreed_gp", "agreed_gp")]),
                        _require(
                            "_ally_agreed_gp == _ally_suggested_gp or adjustment_reason != ''",
                            "Retain the GM's circumstance or ethos reason for adjusting the "
                            "printed payment.",
                        ),
                        _require(
                            "(payment_timing == 'waived' and _ally_agreed_gp == 0) or "
                            "(payment_timing == 'after' and _ally_agreed_gp > 0)",
                            "Use genuine waived service or an agreed payment obligation "
                            "afterward; prepaid transfers need their actual inventory owner.",
                        ),
                        _set(
                            "_ally_service_deadline",
                            "state.lasting_tick + quantity * _ally_unit_ticks",
                        ),
                        _set(
                            "_ally_agreement_patch",
                            {
                                "status": "'serving'",
                                "deadline": "_ally_service_deadline",
                                "task": "task",
                                "agreement": {
                                    "statement": "agreement_statement",
                                    "unit": "unit",
                                    "quantity": "quantity",
                                    "suggested_gp": "_ally_suggested_gp",
                                    "agreed_gp": "_ally_agreed_gp",
                                    "adjustment_reason": "adjustment_reason",
                                    "payment_timing": "payment_timing",
                                    "paid": False,
                                    "language": "language",
                                    "channel": "channel",
                                },
                            },
                        ),
                        _set(
                            "state.planar_ally_records.{_ally_service_id}",
                            "merge(_ally_service, _ally_agreement_patch)",
                        ),
                        _set(
                            "_ally_outcome",
                            "'Autonomous service agreed; no command or task outcome granted'",
                        ),
                    ],
                ),
            ],
        ),
        "exploration",
        "Planar Ally bargain",
        [
            ("Outcome", "_ally_outcome"),
            ("Suggested GP", "_ally_suggested_gp"),
            ("Agreed GP", "_ally_agreed_gp"),
            ("Deadline", "_ally_service_deadline"),
        ],
    )
    complete = _public(
        _tool(
            "Record genuine fictional task completion and return the "
            "independently acting creature home.",
            [
                _entity("caster_state"),
                _entity("ally_state"),
                _parameter("completion_record"),
                _optional("report", default=""),
            ],
            [
                _require(PENDING, "Complete the pending action before ending the service."),
                _call(
                    "read_srd_planar_ally_service",
                    {"caster_state": "{_caster_state_id}", "ally_state": "{_ally_state_id}"},
                    ["_ally_service", "_ally_service_id"],
                ),
                _require(
                    "_ally_service.status == 'serving' and completion_record != ''",
                    "Record the actual GM-resolved task completion; this tool does "
                    "not perform the task.",
                ),
                _set(
                    "state.planar_ally_records.{_ally_service_id}.completion_record",
                    "completion_record",
                ),
                _call(
                    "return_srd_planar_ally_home",
                    {
                        "caster_state": "{_caster_state_id}",
                        "ally_state": "{_ally_state_id}",
                        "reason": "task_completed",
                        "report": "{report}",
                    },
                    ["_ally_returned"],
                ),
            ],
        ),
        "exploration",
        "Planar Ally returned",
        [("Returned", "_ally_returned")],
    )
    refresh = _tool(
        "Return each real Ally when its exact agreed service deadline is reached.",
        [],
        [
            _set("_ally_clock_ids", "state.planar_ally_ids"),
            _loop(
                "_ally_clock_ids",
                "_ally_clock_id",
                [
                    _set("_ally_clock_service", "state.planar_ally_records[_ally_clock_id]"),
                    _when(
                        "_ally_clock_service.active == true and "
                        "_ally_clock_service.status == 'serving' and "
                        "state.lasting_tick >= _ally_clock_service.deadline",
                        [
                            _call(
                                "return_srd_planar_ally_home",
                                {
                                    "caster_state": "{_ally_clock_service.caster}",
                                    "ally_state": "{_ally_clock_service.ally}",
                                    "reason": "service_expired",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "qualify_srd_planar_ally_body": body,
        "record_srd_planar_patron": patron,
        "read_srd_planar_ally_service": record,
        "relocate_srd_planar_ally": relocate,
        "cast_spell_planar_ally_service": cast,
        "install_srd_planar_ally_service": install,
        "qualify_srd_planar_ally_conversation": communicate,
        "return_srd_planar_ally_home": return_home,
        "negotiate_srd_planar_ally": negotiate,
        "complete_srd_planar_ally_task": complete,
        "refresh_srd_planar_ally_deadlines": refresh,
    }


def _dodge_tool(tools):
    """Keep the real ordinary Dodge action for the autonomous NPC on its own turn."""
    dodge = deepcopy(tools["take_dodge_action"])
    validation = [
        p
        for p in dodge["pre_conditions"]
        if p["condition"] == "actor.validation_status == 'validated'"
    ]
    if len(validation) != 1:
        raise ValueError("Preserve the current ordinary Dodge qualification and benefit owners.")
    validation[0].update(
        condition="actor_state.creature_kind == 'monster' and "
        "actor_state.planar_ally_service_id != null and actor_state.is_alive == true",
        error="Only a real independently acting Planar Ally can use this NPC action.",
    )
    for requirement in dodge["pre_conditions"]:
        requirement["condition"] = requirement["condition"].replace(
            "actor.participant_id", "_actor_state_id"
        )
    dodge["parameters"] = [p for p in dodge["parameters"] if p["name"] != "actor"]
    if not any(p["name"] == "lasting_state" for p in dodge["parameters"]):
        dodge["parameters"].append(_optional("lasting_state", resolve="entity", default="state"))
    dodge["effects"][:0] = [
        _require(
            PENDING.replace("state.", "lasting_state."),
            "Complete the pending action before ordinary Dodge.",
        ),
        _require(
            "lasting_state.planar_ally_records != null and actor_state.planar_ally_service_id "
            "in lasting_state.planar_ally_records",
            "The exact autonomous service record is missing.",
        ),
        _set(
            "_ally_dodge_record",
            "lasting_state.planar_ally_records[actor_state.planar_ally_service_id]",
        ),
        _call(
            "read_srd_planar_ally_service",
            {"caster_state": "{_ally_dodge_record.caster}", "ally_state": "{_actor_state_id}"},
        ),
        _call("qualify_srd_planar_ally_body", {"ally_state": "{_actor_state_id}"}),
    ]
    dodge["description"] = (
        "Take the ordinary Dodge action as this actual autonomous creature on its own turn."
    )
    dodge["metadata"]["result_card"]["summary"][0]["value"] = {"path": "context.actor_state.name"}
    return dodge


def extend_runtime(text):
    """Bind paid casting and service return to the existing scene and clock owners."""
    validate_profiles({"planar_ally": PROFILE})
    tools = _load_runtime(text)["tools"]
    changed = _tools() | {"dodge_srd_planar_ally": _dodge_tool(tools)}
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        names = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in names]
        _replace_created_cast_dispatch(
            cast,
            PROFILE["family"],
            _when(
                "_spell_definition.family == 'planar_ally_service'",
                [
                    _call(
                        "cast_spell_planar_ally_service",
                        {
                            "caster_state": "{_caster_state_id}",
                            **{p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES},
                        },
                        ["_spell_summary_rows", "_spell_detail_rows"],
                    )
                ],
            ),
        )
        changed[name] = cast
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_planar_ally_deadlines" not in str(clock["effects"]):
        clock["effects"].append(
            _when(
                "lasting_state.has_planar_allies == true",
                [
                    _call(
                        "refresh_srd_planar_ally_deadlines",
                        {"lasting_state": "{_lasting_state_id}"},
                    )
                ],
            )
        )
    changed["advance_srd_lasting_clock"] = clock
    _normalize(changed)
    if "      planar_ally_service:" in text and all(tools.get(n) == t for n, t in changed.items()):
        return text
    text = _replace_tools(text, changed)
    if "      planar_ally_service:" not in text:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": [k for k in PROFILE if k != "family"],
            "properties": {
                k: {
                    "type": "boolean"
                    if type(v) is bool
                    else "integer"
                    if type(v) is int
                    else "string"
                }
                for k, v in PROFILE.items()
                if k != "family"
            },
        }
        fragment = yaml.dump({PROFILE["family"]: schema}, Dumper=_RuntimeDumper, sort_keys=False)
        marker = "      divination_spell:"
        if text.count(marker) != 1:
            raise ValueError("Preserve the genuine installed spell-profile union.")
        text = text.replace(
            marker, "".join("      " + line for line in fragment.splitlines(True)) + marker, 1
        )
    return text
