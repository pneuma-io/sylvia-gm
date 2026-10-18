"""Named-place divination selects actual physical route facts without a global pathfinder."""

import json
import re
from copy import deepcopy
from pathlib import Path

if __package__:
    from .srd_courier_chronology import _emit
    from .srd_divination_spells import _normalize
    from .srd_illusion_appearance import HIDDEN_SOURCE
    from .srd_scene_relations import _snapshot, _tool_scene
    from .srd_spell_travel import _scene_subject
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
    from .srd_timed_senses import _loop, _optional, _public, _push
else:
    from srd_courier_chronology import _emit
    from srd_divination_spells import _normalize
    from srd_illusion_appearance import HIDDEN_SOURCE
    from srd_scene_relations import _snapshot, _tool_scene
    from srd_spell_travel import _scene_subject
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
    from srd_timed_senses import _loop, _optional, _public, _push


SOURCE = {
    "find_the_path": {
        "page": 131,
        "start": "Find the Path",
        "end": None,
        "continuation_start": "face a choice of paths along the way there, you know \n"
        "which path is the most direct.\n",
        "continuation_next_heading": "Find Traps",
    }
}
PROFILES = {
    "find_the_path": {
        "family": "find_path",
        "base_level": 6,
        "casting_time": "1_minute",
        "range_kind": "self",
        "range_feet": 0,
        "concentration": True,
        "duration_rounds": 14400,
    }
}
PUBLIC_TOOLS = {
    "record_srd_find_path_location",
    "record_srd_find_path_routes",
    "query_srd_find_path",
}
CAST_CHOICES = [_optional("path_location", default="")]
PENDING = "state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0"


def validate_profiles():
    """Read the complete two-column pinned source, including its page-start continuation."""
    root = Path(__file__).resolve().parents[1]
    corpus = json.loads((root / "rulesets/srd-lite/content/corpus/spells.json").read_text())
    page = next(r for r in corpus["page_records"] if r["page"] == 131)
    text = " ".join(page["text"].split())
    for clause in (
        "Level 6 Divination (Bard, Cleric, Druid)",
        "Casting Time: 1 minute",
        "Range: Self",
        "worth 100+ GP",
        "Duration: Concentration, up to 1 day",
        "most direct physical route",
        "must be familiar",
        "another plane of existence",
        "moving des",
        "unspecific",
        "how far it is",
        "what direction it lies",
        "which path is the most direct",
    ):
        if clause not in text:
            raise ValueError(f"Pinned Find the Path clause missing: {clause}")
    return page["text_sha256"]


def _knowledge_tools():
    admit = _public(
        _tool_scene(
            "Record fictional named-place knowledge against an actual admitted location anchor.",
            [
                _entity("actor_state"),
                _parameter("location_subject"),
                _parameter("location_name"),
                _parameter("familiar", "boolean"),
                _parameter("stationary", "boolean"),
                _parameter("specific", "boolean"),
            ],
            [
                _require(PENDING, "Complete the pending action before admitting place facts."),
                _require(
                    "combat.encounter_active != true", "Admit named-place facts outside combat."
                ),
                _require("len(location_name) > 0", "Name this actual location."),
                *_scene_subject("{location_subject}", "_path_anchor"),
                _require(
                    "_path_anchor.metadata.participant_ref == null",
                    "Use a location, not a creature.",
                ),
                _require(
                    "_path_anchor.metadata.plane != null", "The location needs its actual plane."
                ),
                _require(
                    "stationary != true or _path_anchor.metadata.moving != true",
                    "An actually moving anchor cannot be admitted as stationary.",
                ),
                _set("_path_locations", []),
                _when(
                    "actor_state.find_path_locations != null",
                    [_set("_path_locations", "actor_state.find_path_locations")],
                ),
                _set("_path_kept", []),
                _loop(
                    "_path_locations",
                    "_path_prior",
                    [
                        _when(
                            "_path_prior.name != location_name",
                            [_push("_path_kept", "_path_prior")],
                        )
                    ],
                ),
                _require("len(_path_kept) < 64", "Use the current bounded named-place knowledge."),
                _push(
                    "_path_kept",
                    {
                        "name": "location_name",
                        "anchor": "_path_anchor.id",
                        "position": "_path_anchor.position",
                        "plane": "_path_anchor.metadata.plane",
                        "familiar": "familiar",
                        "stationary": "stationary",
                        "specific": "specific",
                    },
                ),
                _set("actor_state.find_path_locations", "_path_kept"),
            ],
        ),
        "information",
        "Named place knowledge",
        [("Place", "location_name")],
    )
    qualify = _tool_scene(
        "Qualify the named place from retained fictional facts and actual current geometry.",
        [_entity("caster_state"), _parameter("location_name")],
        [
            _set("_path_place", None),
            _set("_path_places", []),
            _when(
                "caster_state.find_path_locations != null",
                [_set("_path_places", "caster_state.find_path_locations")],
            ),
            _loop(
                "_path_places",
                "_path_place_row",
                [
                    _when(
                        "_path_place_row.name == location_name",
                        [_set("_path_place", "_path_place_row")],
                    )
                ],
            ),
            _require("_path_place != null", "Admit this named location's actual knowledge first."),
            *_scene_subject("{_caster_state_id}", "_path_caster_body"),
            *_scene_subject("{_path_place.anchor}", "_path_destination"),
            _set(
                "_path_place_qualified",
                "_path_place.familiar == true and _path_place.specific == true and "
                "_path_place.stationary == true and _path_destination.metadata.moving != true and "
                "_path_destination.metadata.participant_ref == null and "
                "_path_destination.metadata.plane == _path_place.plane and "
                "_path_caster_body.metadata.plane == _path_place.plane and "
                "_path_destination.position[0] == _path_place.position[0] and "
                "_path_destination.position[1] == _path_place.position[1] and "
                "_path_destination.position[2] == _path_place.position[2]",
            ),
        ],
    )
    return {"record_srd_find_path_location": admit, "qualify_srd_find_path_location": qualify}


def _cast_tool():
    """A printed invalid destination fizzles after real paid completion without a new source."""
    return _tool_scene(
        "Resolve the paid minute cast against the actual named familiar stationary location.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer"),
            _parameter("concentration_modifier", "integer"),
            *deepcopy(CAST_CHOICES),
            *deepcopy(HIDDEN_SOURCE),
        ],
        [
            _call(
                "qualify_srd_find_path_location",
                {"caster_state": "{_caster_state_id}", "location_name": "{path_location}"},
                ["_path_place", "_path_place_qualified", "_path_destination"],
            ),
            _set("_spell_summary_rows", [{"label": "'Named place'", "value": "path_location"}]),
            _set("_spell_detail_rows", []),
            {
                "condition": "_path_place_qualified == true",
                "on_true": [
                    _set("_path_self_targets", ["_caster_state_id"]),
                    _call(
                        "start_srd_condition_concentration",
                        {
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "state",
                            "targets": "{_path_self_targets}",
                            "spell": "find_the_path",
                            "duration_rounds": 14400,
                            "save_modifier": "{concentration_modifier}",
                        },
                    ),
                    _set("caster_state.concentration_effect_family", "'divination_spell'"),
                    _call(
                        "format_srd_information_projection",
                        {
                            "message": "path:{_caster_state_id}:"
                            "{caster_state.concentration_sequence}"
                        },
                        ["_info_projection_message"],
                    ),
                    _set("caster_state.find_path_instance_id", "_info_projection_message"),
                    _set(
                        "_path_record",
                        {
                            "kind": "'divination_spell'",
                            "spell": "'find_the_path'",
                            "caster": "_caster_state_id",
                            "target": "_caster_state_id",
                            "instance_id": "_info_projection_message",
                            "proxy_id": "''",
                            "sequence": "caster_state.concentration_sequence",
                            "concentration": True,
                            "started_tick": "state.lasting_tick",
                            "expires_tick": "state.lasting_tick + 28800",
                            "location_name": "path_location",
                            "anchor": "_path_destination.id",
                            "position": "_path_destination.position",
                            "plane": "_path_destination.metadata.plane",
                            "cast_id": "spell_cast_id",
                            "source_caster_id": "_caster_state_id",
                            "spell_level": "slot_level",
                            "dispel_ongoing": True,
                            "ongoing_duration": True,
                        },
                    ),
                    _call("save_srd_divination_record", {"record": "{_path_record}"}),
                    _set("state.has_find_path_sources", True),
                    _push(
                        "_spell_detail_rows",
                        {"label": "'Source'", "value": "_path_record.instance_id"},
                    ),
                    _push(
                        "_spell_detail_rows",
                        {
                            "label": "'Location sense'",
                            "value": "'Actual distance and direction available; "
                            "global physical route pending.'",
                        },
                    ),
                ],
                "on_false": [
                    _push(
                        "_spell_detail_rows",
                        {
                            "label": "'Destination'",
                            "value": "'Spell fails: named place is unfamiliar, moving, "
                            "unspecific or on another plane.'",
                        },
                    ),
                ],
            },
        ],
    )


def _query_tool():
    """Distance/direction are exact; the existing bounded grid route is labelled separately."""
    return _public(
        _tool_scene(
            "Read this actual source's place sense and bounded scene route; "
            "global routing is pending.",
            [_entity("actor_state")],
            [
                _call(
                    "collect_srd_divination_record",
                    {
                        "caster_state": "{_actor_state_id}",
                        "spell": "find_the_path",
                        "instance_id": "{actor_state.find_path_instance_id}",
                    },
                    ["_divination_record", "_divination_active"],
                ),
                _require(
                    "_divination_active == true and _divination_record.sequence == "
                    "actor_state.concentration_sequence",
                    "No current exact Find the Path source.",
                ),
                *_scene_subject("{_actor_state_id}", "_path_query_actor"),
                *_scene_subject("{_divination_record.anchor}", "_path_query_anchor"),
                _set("_path_distance", None),
                _set("_path_direction", []),
                _set("_path_local_route", []),
                _set("_path_local_reason", "'other_plane'"),
                _set(
                    "_path_route_status", "'Global most-direct physical route remains unsupported.'"
                ),
                _when(
                    "_path_query_actor.metadata.plane == _divination_record.plane",
                    [
                        _require(
                            "_path_query_anchor.metadata.moving != true and "
                            "_path_query_anchor.metadata.plane == _divination_record.plane and "
                            "_path_query_anchor.position[0] == _divination_record.position[0] and "
                            "_path_query_anchor.position[1] == _divination_record.position[1] and "
                            "_path_query_anchor.position[2] == _divination_record.position[2]",
                            "The named stationary location no longer matches its actual anchor.",
                        ),
                        _call(
                            "measure_srd_information_distance",
                            {
                                "start": "{_path_query_actor.position}",
                                "end": "{_path_query_anchor.position}",
                            },
                            ["_scene_distance", "_scene_direction"],
                        ),
                        _set("_path_distance", "_scene_distance"),
                        _set("_path_direction", "_scene_direction"),
                        _call(
                            "plan_srd_animal_messenger_route",
                            {
                                "target_state": "{_actor_state_id}",
                                "goal": "{_path_query_anchor.position}",
                            },
                            ["_courier_route", "_courier_route_reason"],
                        ),
                        _set("_path_local_route", "_courier_route"),
                        _set("_path_local_reason", "_courier_route_reason"),
                    ],
                ),
            ],
        ),
        "information",
        "Find the Path",
        [
            ("Distance feet", "_path_distance"),
            ("Direction", "_path_direction"),
            ("Local scene route", "_path_local_route"),
            ("Local route result", "_path_local_reason"),
            ("Physical route", "_path_route_status"),
        ],
    )


def _clock_tools():
    qualify = _tool_scene(
        "Expire only the current exact source's concentration.",
        [_entity("caster_state"), _parameter("record", "object")],
        [
            _when(
                "record.instance_id == caster_state.find_path_instance_id and "
                "record.sequence == caster_state.concentration_sequence and "
                "caster_state.concentration_spell_id == 'find_the_path' and "
                "caster_state.concentration_effect_family == 'divination_spell' and "
                "state.lasting_tick >= record.expires_tick",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{_caster_state_id}",
                        },
                    ),
                    _set("caster_state.find_path_instance_id", None),
                ],
            )
        ],
    )
    refresh = _tool_scene(
        "Consume the actual day boundary before common divination expiry.",
        [],
        [
            _when(
                "state.has_find_path_sources == true",
                [
                    _set("_path_clock_rows", "state.turn_effects"),
                    _loop(
                        "_path_clock_rows",
                        "_path_clock_row",
                        [
                            _when(
                                "_path_clock_row.kind == 'divination_spell' and "
                                "_path_clock_row.spell == 'find_the_path'",
                                [
                                    _call(
                                        "settle_srd_find_path_expiry",
                                        {
                                            "caster_state": "{_path_clock_row.caster}",
                                            "record": "{_path_clock_row}",
                                        },
                                    ),
                                ],
                            )
                        ],
                    ),
                    _set("_path_live", False),
                    _set("_path_clock_after", "state.turn_effects"),
                    _loop(
                        "_path_clock_after",
                        "_path_clock_kept",
                        [
                            _when(
                                "_path_clock_kept.kind == 'divination_spell' and "
                                "_path_clock_kept.spell == 'find_the_path' and "
                                "state.lasting_tick < _path_clock_kept.expires_tick",
                                [_set("_path_live", True)],
                            )
                        ],
                    ),
                    _set("state.has_find_path_sources", "_path_live"),
                ],
            )
        ],
    )
    return {"settle_srd_find_path_expiry": qualify, "refresh_srd_find_path_sources": refresh}


def _cast_binding(tools, changed):
    """Replace only our existing dispatch, preserving final lost/Slow guards on repeat."""
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        if not any(p["name"] == "path_location" for p in cast["parameters"]):
            cast["parameters"] += deepcopy(CAST_CHOICES)
        branch = _when(
            "_spell_definition.family == 'find_path'",
            [
                _call(
                    "cast_srd_find_path",
                    {
                        "caster_state": "{_caster_state_id}",
                        "slot_level": "{_slot_level}",
                        "concentration_modifier": "{_casting_profile.concentration_save_modifier}",
                        "path_location": "{path_location}",
                    },
                    ["_spell_summary_rows", "_spell_detail_rows"],
                )
            ],
        )

        def replace(effects, branch=branch):
            found = False
            for i, e in enumerate(effects):
                if "_spell_definition.family == 'find_path'" in e.get("condition", ""):
                    fresh = deepcopy(branch)
                    fresh["condition"] = e["condition"]
                    effects[i] = fresh
                    found = True
                else:
                    for key in ("on_true", "on_false"):
                        if key in e:
                            found = replace(e[key]) or found
            return found

        def insert(effects, branch=branch):
            slots = [
                i
                for i, e in enumerate(effects)
                if "_spell_definition.family ==" in e.get("condition", "")
            ]
            if slots:
                effects.insert(slots[-1] + 1, deepcopy(branch))
                return True
            return any(insert(e[k]) for e in effects for k in ("on_true", "on_false") if k in e)

        if not replace(cast["effects"]) and not insert(cast["effects"]):
            raise ValueError("Find the Path needs the genuine paid spell dispatch.")
        changed[name] = cast


def _place_rows(actor, name="location_name"):
    """Read only this actor's admitted named-place facts."""
    return [
        _set("_path_places", []),
        _when(
            f"{actor}.find_path_locations != null",
            [_set("_path_places", f"{actor}.find_path_locations")],
        ),
        _set("_path_place", None),
        _loop(
            "_path_places",
            "_path_place_row",
            [_when(f"_path_place_row.name == {name}", [_set("_path_place", "_path_place_row")])],
        ),
        _require("_path_place != null", "Admit this named location's actual knowledge first."),
    ]


def _active_path(actor="actor_state"):
    """Authenticate the current paid caster-owned record before reading or updating facts."""
    return [
        _call(
            "collect_srd_divination_record",
            {
                "caster_state": "{_" + actor + "_id}",
                "spell": "find_the_path",
                "instance_id": "{" + actor + ".find_path_instance_id}",
            },
            ["_divination_record", "_divination_active"],
        ),
        _require(
            "_divination_active == true and _divination_record.sequence == "
            + actor
            + ".concentration_sequence",
            "No current exact Find the Path source.",
        ),
    ]


def _factual_tools(changed):
    """Keep native anchor authority; use admitted world facts when coordinates are absent."""
    admit = changed["record_srd_find_path_location"]
    native_admit = deepcopy(admit["effects"])
    admit["parameters"] += [
        _optional("location_plane", default=""),
        _optional("actor_plane", default=""),
    ]
    admit["effects"] = [
        {
            "condition": "state.spatial_scene != null",
            "on_true": native_admit,
            "on_false": [
                _require(PENDING, "Complete the pending action before admitting place facts."),
                _require(
                    "combat.encounter_active != true", "Admit named-place facts outside combat."
                ),
                _require(
                    "len(location_name) > 0 and len(location_subject) > 0 and "
                    "len(location_plane) > 0 and len(actor_plane) > 0",
                    "Name the actual place and its plane, and the actor's current plane.",
                ),
                _set("_path_locations", []),
                _when(
                    "actor_state.find_path_locations != null",
                    [_set("_path_locations", "actor_state.find_path_locations")],
                ),
                _set("_path_kept", []),
                _loop(
                    "_path_locations",
                    "_path_prior",
                    [
                        _when(
                            "_path_prior.name != location_name",
                            [_push("_path_kept", "_path_prior")],
                        )
                    ],
                ),
                _require("len(_path_kept) < 64", "Use the current bounded named-place knowledge."),
                _push(
                    "_path_kept",
                    {
                        "name": "location_name",
                        "anchor": "location_subject",
                        "position": None,
                        "plane": "location_plane",
                        "familiar": "familiar",
                        "stationary": "stationary",
                        "specific": "specific",
                        "native_anchor": False,
                    },
                ),
                _set("actor_state.find_path_locations", "_path_kept"),
                _set("actor_state.find_path_current_plane", "actor_plane"),
            ],
        }
    ]
    qualify = changed["qualify_srd_find_path_location"]
    native_qualify = deepcopy(qualify["effects"])
    qualify["effects"] = [
        {
            "condition": "state.spatial_scene != null",
            "on_true": native_qualify,
            "on_false": [
                *_place_rows("caster_state"),
                _set("_path_destination", None),
                _require(
                    "_path_place.native_anchor == false",
                    "Admit current non-coordinate place facts.",
                ),
                _set(
                    "_path_place_qualified",
                    "_path_place.familiar == true and _path_place.specific == true and "
                    "_path_place.stationary == true and "
                    "caster_state.find_path_current_plane == _path_place.plane",
                ),
            ],
        }
    ]
    cast = changed["cast_srd_find_path"]
    for effect in cast["effects"]:
        for i, child in enumerate(effect.get("on_true", [])):
            if child.get("set", {}).get("field") == "_path_record":
                record = child["set"]["value"]
                record.update(
                    anchor="_path_place.anchor",
                    position=None,
                    plane="_path_place.plane",
                    native_anchor=False,
                )
                effect["on_true"].insert(
                    i + 1,
                    _when(
                        "state.spatial_scene != null",
                        [
                            _set(
                                "_path_native_record",
                                {
                                    "position": "_path_place.position",
                                    "native_anchor": True,
                                },
                            ),
                            _set("_path_record", "merge(_path_record, _path_native_record)"),
                        ],
                    ),
                )
                break
    routes = _parameter(
        "routes",
        "array",
        min_items=1,
        max_items=64,
        items={
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "remaining_distance_feet": {"type": "number", "minimum": 0},
                "description": {"type": "string"},
            },
            "required": ["id", "remaining_distance_feet", "description"],
            "additionalProperties": False,
        },
    )
    recorder = _tool_scene(
        "Record actual physical route lengths at this paid source's current place; "
        "do not supply a best-route verdict.",
        [
            _entity("actor_state"),
            _parameter("source_id"),
            _parameter("destination_subject"),
            _parameter("current_place"),
            _parameter("current_plane"),
            _parameter("distance_feet", "number", minimum=0),
            _parameter("direction"),
            routes,
        ],
        [
            _require(PENDING, "Complete the pending action before admitting route facts."),
            *_active_path(),
            _require(
                "source_id == _divination_record.instance_id and "
                "destination_subject == _divination_record.anchor",
                "Route facts must belong to this actual source and destination.",
            ),
            _require(
                "current_plane == _divination_record.plane and len(current_place) > 0 and "
                "len(direction) > 0 and len(routes) > 0",
                "Admit this actual same-plane current place, direction and physical routes.",
            ),
            _set("_path_fact_body", None),
            _set("_path_fact_position", None),
            _when(
                "state.spatial_scene != null",
                [
                    *_snapshot(),
                    *_scene_subject("{_actor_state_id}", "_path_fact_actor"),
                    _require(
                        "_path_fact_actor.metadata.plane == current_plane",
                        "Route facts must match the actor's actual plane.",
                    ),
                    _set("_path_fact_body", "_path_fact_actor.id"),
                    _set("_path_fact_position", "_path_fact_actor.position"),
                ],
            ),
            _set("_path_route_ids", []),
            _loop(
                "routes",
                "_path_route_fact",
                [
                    _require(
                        "_path_route_fact.id not in _path_route_ids",
                        "Each physical route needs a distinct identity.",
                    ),
                    _push("_path_route_ids", "_path_route_fact.id"),
                ],
            ),
            _set(
                "_path_fact_value",
                {
                    "place": "current_place",
                    "plane": "current_plane",
                    "body_id": None,
                    "position": None,
                    "distance_feet": "distance_feet",
                    "direction": "direction",
                    "routes": "routes",
                    "valid": True,
                },
            ),
            _when(
                "state.spatial_scene != null",
                [
                    _set(
                        "_path_native_fact",
                        {
                            "body_id": "_path_fact_body",
                            "position": "_path_fact_position",
                        },
                    ),
                    _set("_path_fact_value", "merge(_path_fact_value, _path_native_fact)"),
                ],
            ),
            _set("_path_fact_patch", {"route_facts": "_path_fact_value"}),
            _set("_path_fact_record", "merge(_divination_record, _path_fact_patch)"),
            _call("save_srd_divination_record", {"record": "{_path_fact_record}"}),
            _set("actor_state.find_path_current_plane", "current_plane"),
            _set("actor_state.find_path_current_place", "current_place"),
            _set("_path_recorded_place", "current_place"),
        ],
    )
    changed["record_srd_find_path_routes"] = _public(
        recorder,
        "information",
        "Find the Path route facts",
        [("Current place", "_path_recorded_place")],
    )
    query = changed["query_srd_find_path"]
    native_query = deepcopy(query["effects"])
    query["parameters"] += [
        _optional("current_place", default=""),
        _optional("current_plane", default=""),
    ]
    query["effects"] = [
        *_active_path(),
        _set("_path_best_routes", []),
        _set("_path_best_route_ids", []),
        _set("_path_best_route_rows", []),
        _set("_path_shortest_distance", None),
        _set("_path_factual_direction", None),
        {
            "condition": "state.spatial_scene != null",
            "on_true": native_query,
            "on_false": [
                _require(
                    "_divination_record.native_anchor == false",
                    "Admit current non-coordinate destination facts.",
                ),
                *_place_rows("actor_state", "_divination_record.location_name"),
                _require(
                    "_path_place.anchor == _divination_record.anchor and "
                    "_path_place.plane == _divination_record.plane and "
                    "_path_place.stationary == true and _path_place.specific == true",
                    "The named stationary destination no longer matches its admitted place.",
                ),
                _set("_path_distance", None),
                _set("_path_direction", []),
                _set("_path_local_route", []),
                _set("_path_local_reason", "'not_coordinate_play'"),
                _set("_path_route_status", "'Admit actual routes at the current place.'"),
                _set("_path_query_plane", "actor_state.find_path_current_plane"),
                _when("current_plane != ''", [_set("_path_query_plane", "current_plane")]),
                _set("_path_query_place", "''"),
                _when(
                    "actor_state.find_path_current_place != null",
                    [
                        _set("_path_query_place", "actor_state.find_path_current_place"),
                    ],
                ),
                _when("current_place != ''", [_set("_path_query_place", "current_place")]),
                _when(
                    "_divination_record.route_facts != null and "
                    "(_path_query_plane != _divination_record.route_facts.plane or "
                    "_path_query_place != _divination_record.route_facts.place)",
                    [
                        _call("invalidate_srd_find_path_routes", {"subject": "{_actor_state_id}"}),
                        _set("_path_record_patch", {"route_facts": None}),
                        _set("_divination_record", "merge(_divination_record, _path_record_patch)"),
                    ],
                ),
                _set("actor_state.find_path_current_plane", "_path_query_plane"),
                _set("actor_state.find_path_current_place", "_path_query_place"),
            ],
        },
        _set("_path_facts_usable", False),
        _when(
            "_divination_record.route_facts != null",
            [
                _set("_path_facts", "_divination_record.route_facts"),
                _set(
                    "_path_facts_usable",
                    "_path_facts.valid == true and "
                    "(current_place == '' or current_place == _path_facts.place)",
                ),
                {
                    "condition": "state.spatial_scene != null",
                    "on_true": [
                        _set(
                            "_path_facts_usable",
                            "_path_facts_usable == true and "
                            "_path_query_actor.id == _path_facts.body_id and "
                            "_path_query_actor.position[0] == _path_facts.position[0] and "
                            "_path_query_actor.position[1] == _path_facts.position[1] and "
                            "_path_query_actor.position[2] == _path_facts.position[2] and "
                            "_path_query_actor.metadata.plane == _path_facts.plane and "
                            "_path_query_actor.metadata.plane == _divination_record.plane",
                        ),
                    ],
                    "on_false": [
                        _set(
                            "_path_facts_usable",
                            "_path_facts_usable == true and "
                            "_path_query_plane == _path_facts.plane and "
                            "_path_query_plane == _divination_record.plane",
                        ),
                    ],
                },
                _when(
                    "_path_facts_usable == true",
                    [
                        _when(
                            "state.spatial_scene == null",
                            [_set("_path_distance", "_path_facts.distance_feet")],
                        ),
                        _set("_path_factual_direction", "_path_facts.direction"),
                        _set("_path_route_candidates", "_path_facts.routes"),
                        _loop(
                            "_path_route_candidates",
                            "_path_route",
                            [
                                _when(
                                    "_path_shortest_distance == null or "
                                    "_path_route.remaining_distance_feet < _path_shortest_distance",
                                    [
                                        _set(
                                            "_path_shortest_distance",
                                            "_path_route.remaining_distance_feet",
                                        ),
                                        _set("_path_best_routes", []),
                                        _set("_path_best_route_ids", []),
                                        _set("_path_best_route_rows", []),
                                    ],
                                ),
                                _when(
                                    "_path_route.remaining_distance_feet == "
                                    "_path_shortest_distance",
                                    [
                                        _push("_path_best_routes", "_path_route"),
                                        _push("_path_best_route_ids", "_path_route.id"),
                                        _push(
                                            "_path_best_route_rows",
                                            {
                                                "label": "_path_route.id",
                                                "value": "_path_route.description",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        _set(
                            "_path_route_status",
                            "'Most direct of the admitted physical routes at this current place.'",
                        ),
                    ],
                ),
            ],
        ),
    ]
    query["effects"].append(_set("_path_local_route_steps", "len(_path_local_route)"))
    for row in query["metadata"]["result_card"]["summary"]:
        if row["label"] == "Local scene route":
            row["label"] = "Local scene route steps"
            row["value"]["path"] = "context._path_local_route_steps"
    query["metadata"]["result_card"]["summary"] += [
        {"label": "Known direction", "value": {"path": "context._path_factual_direction"}},
        {"label": "Most direct routes", "value": {"path": "context._path_best_route_ids"}},
    ]
    query["metadata"]["result_card"]["details"] = {"path": "context._path_best_route_rows"}
    changed["invalidate_srd_find_path_routes"] = _tool(
        "Invalidate only this actual moved caster's retained physical-route facts.",
        [_parameter("subject")],
        [
            _set("_path_motion_rows", "state.turn_effects"),
            _loop(
                "_path_motion_rows",
                "_path_motion_record",
                [
                    _when(
                        "_path_motion_record.kind == 'divination_spell' and "
                        "_path_motion_record.spell == 'find_the_path' and "
                        "_path_motion_record.route_facts != null and "
                        "(subject == _path_motion_record.caster or "
                        "subject == _path_motion_record.route_facts.body_id)",
                        [
                            _set("_path_motion_patch", {"route_facts": None}),
                            _set(
                                "_path_motion_changed",
                                "merge(_path_motion_record, _path_motion_patch)",
                            ),
                            _call(
                                "save_srd_divination_record", {"record": "{_path_motion_changed}"}
                            ),
                        ],
                    )
                ],
            ),
        ],
    )


def _route_movement_binding(tools, changed):
    """Invalidate at committed movement; replacement/end is qualified by source identity."""
    owners = {
        "move_srd_travel_subject": ("{subject}", None),
        "move_srd_scene_creature": ("{_actor_state_id}", None),
        "spend_combat_movement": ("{_actor_state_id}", "distance"),
        "spend_srd_movement": ("{_target_id}", "distance"),
    }
    for name, (subject, distance) in owners.items():
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        condition = "state.has_find_path_sources == true"
        if distance:
            names = {p["name"] for p in tool["parameters"]}
            if distance not in names:
                if "amount" not in names:
                    raise ValueError("Find Path needs the actual movement distance parameter.")
                distance = "amount"
            condition += " and " + distance + " > 0"
        hook = _when(condition, [_call("invalidate_srd_find_path_routes", {"subject": subject})])
        hook = _tool(tool["description"], tool["parameters"], [hook])["effects"][0]
        tool["effects"] = [e for e in tool["effects"] if e != hook]
        tool["effects"].append(hook)
        changed[name] = tool


def _bind_route_state(changed):
    """Bind added state reads like existing tools, retaining the canonical spatial scene path."""

    def canonical_scene(value):
        if isinstance(value, list):
            return [canonical_scene(item) for item in value]
        if isinstance(value, dict):
            return {
                key: "state.spatial_scene" if key == "scene" else canonical_scene(item)
                for key, item in value.items()
            }
        return value

    for name in (
        "record_srd_find_path_location",
        "qualify_srd_find_path_location",
        "cast_srd_find_path",
        "query_srd_find_path",
        "invalidate_srd_find_path_routes",
    ):
        tool = changed[name]
        bound = _tool(tool["description"], tool["parameters"], tool["effects"])
        tool["effects"] = canonical_scene(bound["effects"])


def extend_runtime(text):
    """Emit a narrow family on the existing divination registry and casting owners."""
    tools = _load_runtime(text)["tools"]
    changed = {
        **_knowledge_tools(),
        "cast_srd_find_path": _cast_tool(),
        "query_srd_find_path": _query_tool(),
        **_clock_tools(),
    }
    _factual_tools(changed)
    _route_movement_binding(tools, changed)
    _cast_binding(tools, changed)
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_find_path_sources" not in str(clock["effects"]):
        i = next(
            i
            for i, e in enumerate(clock["effects"])
            if e.get("call") == "refresh_srd_divination_spells"
        )
        clock["effects"].insert(i, _call("refresh_srd_find_path_sources", {}))
    changed["advance_srd_lasting_clock"] = clock
    _bind_route_state(changed)
    _normalize(changed)
    text = _emit(text, changed)
    schema = {
        "type": "object",
        "properties": {
            k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
            for k, v in PROFILES["find_the_path"].items()
            if k != "family"
        },
        "required": [k for k in PROFILES["find_the_path"] if k != "family"],
        "additionalProperties": False,
    }
    row = "      find_path: " + json.dumps(schema, separators=(",", ":"))
    pattern = r"(?m)^      find_path: .*"
    if re.search(pattern, text):
        text = re.sub(pattern, lambda _: row, text)
    else:
        variants = _load_runtime(text)["definition_row_schemas"]["definitions.spells"]["variants"]
        anchor = "      " + next(iter(variants)) + ":"
        assert text.count(anchor) == 1
        text = text.replace(anchor, row + "\n" + anchor, 1)
    return text


def bind_runtime_consumers(text):
    """Refresh the named movement hooks after genuine movement owners finish regeneration."""
    changed = {}
    _route_movement_binding(_load_runtime(text)["tools"], changed)
    _normalize(changed)
    return _emit(text, changed)
