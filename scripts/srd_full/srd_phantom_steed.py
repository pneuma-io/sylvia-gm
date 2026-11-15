"""Phantom Steed's real horse body, chosen rider and source-owned fade."""

import json
import re
from copy import deepcopy
from pathlib import Path

if __package__:
    from .srd_courier_chronology import _emit
    from .srd_divination_spells import _normalize
    from .srd_illusion_appearance import HIDDEN_SOURCE
    from .srd_mirage_support import _ground_without_landing_requirement
    from .srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
    from .srd_spell_object_content import _new_scene_object
    from .srd_spell_summoning import _created_actor_placement_tools
    from .srd_spell_travel import _scene_subject
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _push
else:
    from srd_courier_chronology import _emit
    from srd_divination_spells import _normalize
    from srd_illusion_appearance import HIDDEN_SOURCE
    from srd_mirage_support import _ground_without_landing_requirement
    from srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
    from srd_spell_object_content import _new_scene_object
    from srd_spell_summoning import _created_actor_placement_tools
    from srd_spell_travel import _scene_subject
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _push


SOURCE = {"phantom_steed": (152, "Phantom Steed", "Planar Ally")}
PROFILES = {
    "phantom_steed": {
        "family": "phantom_steed",
        "base_level": 3,
        "casting_time": "1_minute",
        "range_kind": "distance",
        "range_feet": 30,
        "concentration": False,
        "duration_rounds": 600,
        "effect_rounds": 600,
        "size_rank": 3,
        "speed": 100,
        "travel_miles_per_hour": 13,
        "fade_rounds": 10,
        "equipment_separation_feet": 10,
    }
}
PUBLIC_TOOLS = {
    "mount_srd_phantom_steed",
    "dismount_srd_phantom_steed",
    "dismiss_srd_phantom_steed",
    "handle_srd_phantom_steed_equipment",
}
CAST_CHOICES = [
    _optional("phantom_steed_id", default=""),
    _optional(
        "phantom_steed_position",
        "array",
        [0, 0, 0],
        items={"type": "number"},
        min_items=3,
        max_items=3,
    ),
    _optional("phantom_steed_height", "number", 6, minimum=0.001),
    _optional("phantom_saddle_height", "number", 4, minimum=0.001),
    _optional("phantom_steed_appearance", default=""),
    _optional("phantom_rider", default=""),
    _optional(
        "phantom_steed_lighting",
        "enum",
        "bright",
        enum_values=["bright", "dim", "darkness", "magical_darkness"],
    ),
    _optional(
        "phantom_equipment_extents",
        "array",
        [[3, 2, 0.5], [0.5, 0.2, 0.2], [1, 1, 0.2]],
        items={"type": "array", "items": {"type": "number"}},
        min_items=3,
        max_items=3,
    ),
]
PENDING = "state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0"
ACTIVE = (
    "actor_state.is_alive == true and 'incapacitated' not in actor_state.conditions and "
    "'paralyzed' not in actor_state.conditions and 'petrified' not in actor_state.conditions "
    "and 'stunned' not in actor_state.conditions and 'unconscious' not in actor_state.conditions"
)


def _source(identity="source_id"):
    """Read one retained exact spell instance without inferring ownership from a name."""
    return [
        _require("state.phantom_steed_sources != null", "Choose an actual Phantom Steed source."),
        _set("_ps_source", None),
        _set("_ps_sources", "state.phantom_steed_sources"),
        _loop(
            "_ps_sources",
            "_ps_row",
            [
                _when(f"_ps_row.id == {identity}", [_set("_ps_source", "_ps_row")]),
            ],
        ),
        _require(
            "_ps_source != null and _ps_source.spell == 'phantom_steed'",
            "Choose the exact retained Phantom Steed source.",
        ),
    ]


def _save():
    """Replace only this exact retained source in its family pool."""
    return [
        _set("_ps_saved", []),
        _set("_ps_current", "state.phantom_steed_sources"),
        _loop(
            "_ps_current",
            "_ps_current_source",
            [
                {
                    "condition": "_ps_current_source.id == _ps_source.id",
                    "on_true": [_push("_ps_saved", "_ps_source")],
                    "on_false": [_push("_ps_saved", "_ps_current_source")],
                }
            ],
        ),
        _set("state.phantom_steed_sources", "_ps_saved"),
    ]


def _horse():
    """Validate the complete pinned spell and the admitted ordinary Riding Horse block."""
    root = Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1/content"
    corpus = json.loads((root / "corpus/spells.json").read_text(encoding="utf-8"))
    page = next(row["text"] for row in corpus["page_records"] if row["page"] == 152)
    text = " ".join(page.split("Phantom Steed\n", 1)[1].split("Planar Ally\n", 1)[0].split())
    clauses = [
        "Casting Time: 1 minute or Ritual",
        "Range: 30 feet",
        "Components: V, S",
        "Duration: 1 hour",
        "A Large, quasi-real, horselike creature appears on",
        "saddle, bit, and bridle",
        "more than 10 feet away",
        "you or a creature you choose",
        "Riding Horse",
        "Speed of 100 feet",
        "13 miles in an hour",
        "1 minute to dismount",
        "ends early if the steed takes any damage",
    ]
    if any(clause not in text for clause in clauses):
        raise ValueError("Phantom Steed differs from its pinned printed clauses.")
    horse = json.loads((root / "beast-form-index.json").read_text(encoding="utf-8"))["forms"][
        "riding_horse"
    ]
    if (
        horse["source_stat_block_sha256"]
        != "1f8586d531416c459da44f3567b39c78de4fa15fc46ad6d6f1bbb2d55e5f476e"
    ):
        raise ValueError("The admitted Riding Horse block differs from the pinned source.")
    if not horse["execution_supported"] or horse["size_rank"] != 3:
        raise ValueError("The source horse requires its actual admitted Large body.")
    return horse


def validate_profiles(definitions):
    _horse()
    for spell, definition in definitions.items():
        if definition.get("family") == "phantom_steed" and definition != PROFILES.get(spell):
            raise SystemExit("Phantom Steed source profile differs: " + spell)


def _preparation(tools):
    """Derive one genuine NPC from Riding Horse; public callers cannot choose its statistics."""
    horse = _horse()
    tool = deepcopy(tools["prepare_srd_creature"])
    parameters = tool["parameters"]
    tool["parameters"] = [_parameter("creature_id")]
    tool["pre_conditions"] = []
    tool["description"] = "Prepare only the printed Phantom Steed's actual Riding Horse statistics."
    tool["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    fixed = {
        "name": "'Phantom Steed'",
        "source": "'SRD5.2.1 Phantom Steed p152 / Riding Horse p360'",
        "armor_class": horse["armor_class"],
        "hit_points": horse["hit_points"],
        "speed": 100,
        "size_rank": horse["size_rank"],
        "creature_type": "'beast'",
        "initiative_modifier": horse["initiative_modifier"],
        "proficiency_bonus": 2,
        "challenge_rating_eighths": horse["challenge_rating_eighths"],
        **{a + "_score": n for a, n in horse["ability_scores"].items()},
        **{a + "_save": n for a, n in horse["saving_throw_modifiers"].items()},
    }
    prefix = [
        _set(p["name"], fixed.get(p["name"], deepcopy(p.get("default"))))
        for p in parameters
        if p["name"] != "creature_id"
    ]
    tool["effects"] = prefix + tool["effects"]
    removed = {p["name"] for p in parameters if p["name"] != "creature_id"}
    pattern = re.compile(
        r"(?<![.\w])(" + "|".join(sorted(removed, key=len, reverse=True)) + r")(?!\w)"
    )

    def local(value):
        if isinstance(value, str):
            return pattern.sub(lambda m: "_ps_prep_" + m.group(), value)
        if isinstance(value, list):
            return [local(item) for item in value]
        if isinstance(value, dict):
            return {k: local(v) for k, v in value.items()}
        return value

    tool["effects"] = local(tool["effects"])
    return tool


def _actor_finish():
    horse = _horse()
    return _tool_scene(
        "Retain authentic source provenance on the real steed actor.",
        [_entity("actor_state"), _parameter("source_id"), _entity("caster_state")],
        [
            _set("actor_state.phantom_steed_source_id", "source_id"),
            _set("actor_state.phantom_steed_caster", "_caster_state_id"),
            _set("actor_state.source_stat_block_sha256", repr(horse["source_stat_block_sha256"])),
            _set("actor_state.passive_perception", horse["passive_perception"]),
            _set("actor_state.languages", horse["languages"]),
            _set("actor_state.body_available", True),
            _set("actor_state.vital_parts_present", True),
            _set("actor_state.death_tick", None),
            *[_set("actor_state." + key, value) for key, value in horse["senses"].items()],
            *[
                _set("actor_state." + key, horse[key])
                for key in ("breathes_air", "breathes_water", "hold_breath_ticks")
            ],
            {
                "table_lookup": {
                    "table": "definitions.beast_forms",
                    "key": "'riding_horse'",
                    "required": True,
                },
                "store_as": "_ps_horse_form",
            },
            _set("actor_state.phantom_steed_attacks", "_ps_horse_form.attacks"),
        ],
    )


def _casting_tool():
    return _tool_scene(
        "Create the real source horse only after the shared paid long casting.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer"),
            *deepcopy(CAST_CHOICES),
            *deepcopy(HIDDEN_SOURCE),
        ],
        [
            _require(
                "phantom_steed_id != ''", "Choose the source horse's unique actual creature ID."
            ),
            _require("phantom_steed_appearance != ''", "Describe the source horse's appearance."),
            _require(
                "phantom_saddle_height <= phantom_steed_height",
                "The admitted saddle must lie on this actual horselike body.",
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when("state.phantom_steed_sources == null", [_set("state.phantom_steed_sources", [])]),
            _set("_ps_initial_sources", "state.phantom_steed_sources"),
            _loop(
                "_ps_initial_sources",
                "_ps_initial_source",
                [
                    _require(
                        "_ps_initial_source.steed != phantom_steed_id",
                        "The source horse ID already belongs to a retained spell.",
                    ),
                ],
            ),
            _call(
                "create_srd_phantom_steed_scene",
                {
                    "caster_state": "{_caster_state_id}",
                    "summon_id": "{phantom_steed_id}",
                    "spell": "phantom_steed",
                    "range_feet": 30,
                    "half_extent": 5,
                    "creature_type": "beast",
                    "requires_sight": False,
                    "created_actor_position": "{phantom_steed_position}",
                    "created_actor_height": "{phantom_steed_height}",
                    "created_actor_lighting": "{phantom_steed_lighting}",
                },
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{phantom_steed_id}"}, POSITION_EXPORTS
            ),
            *_ground_without_landing_requirement(),
            _require(
                "_fall_ground_height != null and abs(_info_position[2] - _fall_ground_height) "
                "<= 0.000001",
                "The source horse must appear on an actual admitted supporting ground.",
            ),
            _call("prepare_srd_phantom_steed_actor", {"creature_id": "{phantom_steed_id}"}),
            _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
            {"increment": {"field": "state.lasting_serial", "amount": 1}},
            _call(
                "format_srd_information_projection",
                {
                    "message": "{_caster_state_id}:phantom_steed:{state.lasting_serial}",
                },
                ["_info_projection_message"],
            ),
            _set("_ps_id", "_info_projection_message"),
            _set("_ps_chosen_rider", "phantom_rider"),
            _when("phantom_rider == ''", [_set("_ps_chosen_rider", "_caster_state_id")]),
            _call("qualify_srd_phantom_steed_chosen_rider", {"target_state": "{_ps_chosen_rider}"}),
            _set(
                "_ps_source",
                {
                    "id": "_ps_id",
                    "spell": "'phantom_steed'",
                    "caster": "_caster_state_id",
                    "steed": "phantom_steed_id",
                    "serial": "state.lasting_serial",
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 1200",
                    "status": "'active'",
                    "chosen_rider": "_ps_chosen_rider",
                    "rider": "''",
                    "appearance": "phantom_steed_appearance",
                    "saddle_height": "phantom_saddle_height",
                    "cast_id": "spell_cast_id",
                    "source_caster_id": "_caster_state_id",
                    "spell_level": "slot_level",
                    "dispel_ongoing": True,
                    "ongoing_duration": True,
                    "equipment_ids": [],
                    "ended_tick": None,
                    "fade_until": None,
                },
            ),
            _push("_ps_initial_sources", "_ps_source"),
            _set("state.phantom_steed_sources", "_ps_initial_sources"),
            _set("state.has_phantom_steeds", True),
            _call(
                "finish_srd_phantom_steed_actor",
                {
                    "actor_state": "{phantom_steed_id}",
                    "source_id": "{_ps_id}",
                    "caster_state": "{_caster_state_id}",
                },
            ),
            _call(
                "create_srd_phantom_steed_equipment",
                {"source_id": "{_ps_id}", "extents": "{phantom_equipment_extents}"},
            ),
            _set("_spell_summary_rows", [{"label": "'Steed'", "value": "phantom_steed_id"}]),
            _set(
                "_spell_detail_rows",
                [
                    {
                        "label": "'Source form'",
                        "value": "'Riding Horse, Speed100, travel13 miles/hour'",
                    },
                    {"label": "'Chosen rider'", "value": "_ps_chosen_rider"},
                ],
            ),
        ],
    )


def _lifetime_tools():
    fade = _tool_scene(
        "End only this exact source, retaining its printed one-minute fading body.",
        [_parameter("source_id"), _parameter("reason"), _parameter("ended_tick", "number")],
        [
            *_source(),
            _require(
                "ended_tick >= _ps_source.started_tick and ended_tick <= state.lasting_tick",
                "The real source ending cannot precede its cast or invent future time.",
            ),
            _when(
                "_ps_source.status == 'active'",
                [
                    _set(
                        "_ps_patch",
                        {
                            "status": "'fading'",
                            "ended_tick": "ended_tick",
                            "fade_until": "ended_tick + 20",
                            "end_reason": "reason",
                        },
                    ),
                    _set("_ps_source", "merge(_ps_source, _ps_patch)"),
                    *_save(),
                    _call("retire_srd_phantom_steed_equipment", {"source_id": "{source_id}"}),
                ],
            ),
        ],
    )
    damaged = _tool_scene(
        "Observe only a real positive completed packet on its actual source horse.",
        [_entity("target_state")],
        [
            _when(
                "target_state.phantom_steed_source_id != null",
                [
                    *_source("target_state.phantom_steed_source_id"),
                    _require(
                        "_ps_source.steed == _target_state_id and "
                        "target_state.phantom_steed_caster == _ps_source.caster",
                        "Only the authentic current source horse owns this damage ending.",
                    ),
                    _when(
                        "_ps_source.status == 'active'",
                        [
                            _call(
                                "retire_srd_phantom_steed_source",
                                {
                                    "source_id": "{_ps_source.id}",
                                    "reason": "damage",
                                    "ended_tick": "{state.lasting_tick}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    refresh = _tool_scene(
        "Project actual source expiry without allocating a nested fall event.",
        [],
        [
            _when(
                "state.has_phantom_steeds == true",
                [
                    _set("_ps_lifetime_rows", "state.phantom_steed_sources"),
                    _loop(
                        "_ps_lifetime_rows",
                        "_ps_lifetime",
                        [
                            _when(
                                "_ps_lifetime.status == 'active' and state.lasting_tick >= "
                                "_ps_lifetime.expires_tick",
                                [
                                    _call(
                                        "retire_srd_phantom_steed_source",
                                        {
                                            "source_id": "{_ps_lifetime.id}",
                                            "reason": "expiry",
                                            "ended_tick": "{_ps_lifetime.expires_tick}",
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
    return {
        "retire_srd_phantom_steed_source": fade,
        "observe_srd_phantom_steed_damage": damaged,
        "refresh_srd_phantom_steeds": refresh,
    }


def _scene_creation():
    tool = deepcopy(_created_actor_placement_tools()["create_srd_source_actor_scene"])
    tool["description"] = "Place the source horse on its actual same-plane unoccupied Large space."
    for effect in tool["effects"]:
        guard = effect.get("require", {})
        if "Only a printed source without sight-qualified placement" in guard.get("error", ""):
            guard["condition"] += " or spell == 'phantom_steed'"
    return tool


def _deactivate(identity):
    return [
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{state.spatial_scene.map.revision}",
                "target": {"kind": "entity", "id": identity},
                "patch": {"active": False, "movement": "passable", "visibility": "clear"},
            },
            "store_as": "_ps_retired_scene",
        }
    ]


def _equipment_tools():
    """Source-created tack is actual scenery; holder geometry decides its ten-foot lifetime."""
    create = _tool_scene(
        "Create the three actual source tack identities on this horse.",
        [
            _parameter("source_id"),
            _parameter("extents", "array", items={"type": "array", "items": {"type": "number"}}),
        ],
        [
            *_source(),
            *_scene_subject("{_ps_source.steed}", "_ps_equipped_horse"),
            _set("_world_origin", "_ps_equipped_horse.position"),
            _set("_ps_equipment_names", ["'saddle'", "'bit'", "'bridle'"]),
            _set("_ps_equipment_index", 0),
            _set("_ps_equipment_ids", []),
            _loop(
                "_ps_equipment_names",
                "_ps_equipment_name",
                [
                    _call(
                        "format_srd_information_projection",
                        {
                            "message": "{_ps_source.id}:{_ps_equipment_name}",
                        },
                        ["_info_projection_message"],
                    ),
                    _set("_ps_equipment_id", "_info_projection_message"),
                    _set("_ps_equipment_extent", "extents[_ps_equipment_index]"),
                    _require(
                        "len(_ps_equipment_extent) == 3",
                        "Each actual tack body needs exactly three dimensions.",
                    ),
                    *[
                        _require(
                            f"_ps_equipment_extent[{a}] > 0",
                            "Admit the tack's actual positive dimensions.",
                        )
                        for a in range(3)
                    ],
                    *_new_scene_object(
                        {
                            "source_spell": "'phantom_steed'",
                            "phantom_steed_source": "source_id",
                            "phantom_steed_equipment": "_ps_equipment_name",
                            "carried_by": "_ps_source.steed",
                            "plane": "_ps_equipped_horse.metadata.plane",
                            "lighting": "_ps_equipped_horse.metadata.lighting",
                            "fiction": "_ps_equipment_name",
                        },
                        identity="_ps_equipment_id",
                        extent="_ps_equipment_extent",
                    ),
                    _push("_ps_equipment_ids", "_ps_equipment_id"),
                    {"increment": {"field": "_ps_equipment_index", "amount": 1}},
                ],
            ),
            _set("_ps_equipment_patch", {"equipment_ids": "_ps_equipment_ids"}),
            _set("_ps_source", "merge(_ps_source, _ps_equipment_patch)"),
            *_save(),
        ],
    )
    retire = _tool_scene(
        "Retire only the tack created by this exact ended source.",
        [_parameter("source_id")],
        [
            *_source(),
            _set("_ps_tack_ids", "_ps_source.equipment_ids"),
            _loop(
                "_ps_tack_ids",
                "_ps_tack_id",
                [
                    *_scene_subject("{_ps_tack_id}", "_ps_tack"),
                    _require(
                        "_ps_tack.metadata.phantom_steed_source == source_id",
                        "The tack must retain its actual source identity.",
                    ),
                    _when("_ps_tack.active == true", _deactivate("{_ps_tack_id}")),
                ],
            ),
        ],
    )
    check = _tool_scene(
        "Use actual holder/source positions before delivering source tack.",
        [_parameter("source_id")],
        [
            *_source(),
            *_scene_subject("{_ps_source.steed}", "_ps_tack_horse"),
            _set("_ps_tack_horse_position", "_ps_tack_horse.position"),
            _set("_ps_tack_horse_plane", "_ps_tack_horse.metadata.plane"),
            _set("_ps_tack_ids", "_ps_source.equipment_ids"),
            _loop(
                "_ps_tack_ids",
                "_ps_tack_id",
                [
                    *_scene_subject("{_ps_tack_id}", "_ps_tack"),
                    _when(
                        "_ps_tack.active == true",
                        [
                            _set("_ps_tack_position", "_ps_tack.position"),
                            _set("_ps_tack_plane", "_ps_tack.metadata.plane"),
                            _when(
                                "_ps_tack.metadata.carried_by != null and "
                                "_ps_tack.metadata.carried_by != ''",
                                [
                                    *_scene_subject(
                                        "{_ps_tack.metadata.carried_by}", "_ps_tack_holder"
                                    ),
                                    _set("_ps_tack_position", "_ps_tack_holder.position"),
                                    _set("_ps_tack_plane", "_ps_tack_holder.metadata.plane"),
                                ],
                            ),
                            _call(
                                "measure_srd_information_distance",
                                {
                                    "start": "{_ps_tack_horse_position}",
                                    "end": "{_ps_tack_position}",
                                },
                                ["_scene_distance"],
                            ),
                            _when(
                                "_ps_source.status != 'active' or _ps_tack_plane != "
                                "_ps_tack_horse_plane or _scene_distance > 10",
                                _deactivate("{_ps_tack_id}"),
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    handle = _public(
        _tool_scene(
            "Take source tack within actual reach, or drop your own carried piece.",
            [
                _entity("actor_state"),
                _parameter("source_id"),
                _parameter("equipment_id"),
                _parameter("operation", "enum", enum_values=["take", "drop"]),
            ],
            [
                _require(PENDING, "Complete the pending action before handling source tack."),
                _require(ACTIVE, "The actual handler must be able to interact with the tack."),
                _call(
                    "check_srd_active_control_action",
                    {
                        "actor_state": "{_actor_state_id}",
                        "action_kind": "action",
                        "magic_action": False,
                    },
                ),
                _require(
                    "combat.encounter_active != true or (combat.turn_active == true and "
                    "combat.active_actor == _actor_state_id)",
                    "Handle the actual equipment on this actor's turn.",
                ),
                _call("refresh_srd_phantom_steed_equipment", {"source_id": "{source_id}"}),
                *_source(),
                _require(
                    "equipment_id in _ps_source.equipment_ids",
                    "Choose one actual piece from this exact source.",
                ),
                *_scene_subject("{equipment_id}", "_ps_handled_tack"),
                *_scene_subject("{_actor_state_id}", "_ps_handler"),
                _require(
                    "_ps_handled_tack.active == true and _ps_handled_tack.metadata."
                    "phantom_steed_source == source_id",
                    "The actual source piece must still exist.",
                ),
                _set("_ps_tack_new_holder", "''"),
                _when(
                    "operation == 'take'",
                    [
                        _set("_ps_tack_reach_body", "_ps_handled_tack"),
                        _when(
                            "_ps_handled_tack.metadata.carried_by != null and "
                            "_ps_handled_tack.metadata.carried_by != ''",
                            [
                                *_scene_subject(
                                    "{_ps_handled_tack.metadata.carried_by}", "_ps_tack_reach_body"
                                ),
                                _require(
                                    "_ps_handled_tack.metadata.carried_by == _ps_source.steed or "
                                    "_ps_handled_tack.metadata.carried_by == _actor_state_id",
                                    "Another creature's held tack needs its actual release.",
                                ),
                            ],
                        ),
                        _require(
                            "_ps_tack_reach_body.metadata.plane == _ps_handler.metadata.plane",
                            "The real handler and tack must share a plane.",
                        ),
                        *_body_distance("_ps_handler", "_ps_tack_reach_body", "_ps_tack_reach"),
                        _require(
                            "_ps_tack_reach <= 5", "The actual source tack must be within reach."
                        ),
                        _set("_ps_tack_new_holder", "_ps_handler.id"),
                    ],
                ),
                _when(
                    "operation == 'drop'",
                    [
                        _require(
                            "_ps_handled_tack.metadata.carried_by == _ps_handler.id",
                            "Only the actual holder can drop this source equipment.",
                        ),
                        _call(
                            "move_srd_travel_subject",
                            {
                                "subject": "{equipment_id}",
                                "destination": "{_ps_handler.position}",
                                "plane": "{_ps_handler.metadata.plane}",
                                "teleport": False,
                            },
                        ),
                    ],
                ),
                _set(
                    "_ps_tack_holder_patch",
                    {"carried_by": "_ps_tack_new_holder", "plane": "_ps_handler.metadata.plane"},
                ),
                _set(
                    "_ps_tack_metadata", "merge(_ps_handled_tack.metadata, _ps_tack_holder_patch)"
                ),
                {
                    "spatial_mutation": {
                        "scene": "state.spatial_scene",
                        "map_id": "{state.spatial_scene.map.id}",
                        "expected_revision": "{state.spatial_scene.map.revision}",
                        "target": {"kind": "entity", "id": "{equipment_id}"},
                        "patch": {"metadata": "{_ps_tack_metadata}"},
                    },
                    "store_as": "_ps_handled_scene",
                },
                _call("refresh_srd_phantom_steed_equipment", {"source_id": "{source_id}"}),
                _set("_ps_tack_operation", "operation"),
            ],
        ),
        "exploration",
        "Phantom Steed equipment",
        [("Operation", "_ps_tack_operation")],
    )
    all_tack = _tool_scene(
        "Retire only real separated tack after actual carrier or horse movement.",
        [],
        [
            _when(
                "state.has_phantom_steeds == true",
                [
                    _set("_ps_all_tack_sources", "state.phantom_steed_sources"),
                    _loop(
                        "_ps_all_tack_sources",
                        "_ps_all_tack_source",
                        [
                            _when(
                                "_ps_all_tack_source.status == 'active'",
                                [
                                    _call(
                                        "refresh_srd_phantom_steed_equipment",
                                        {"source_id": "{_ps_all_tack_source.id}"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "create_srd_phantom_steed_equipment": create,
        "retire_srd_phantom_steed_equipment": retire,
        "refresh_srd_phantom_steed_equipment": check,
        "handle_srd_phantom_steed_equipment": handle,
        "refresh_all_srd_phantom_steed_equipment": all_tack,
    }


def _mount_budget_tool():
    """Half Speed comes from the existing current Speed projection, never a supplied cost."""
    return _tool_scene(
        "Spend half this actual rider's current Speed to mount or dismount.",
        [_entity("actor_state")],
        [
            _require(PENDING, "Complete the actual pending window before mounting or dismounting."),
            _require(ACTIVE, "An Incapacitated rider cannot mount or dismount."),
            {
                "condition": "combat.encounter_active == true",
                "on_true": [
                    _require(
                        "combat.turn_active == true and combat.active_actor == _actor_state_id",
                        "Mount or dismount during this rider's real move.",
                    ),
                    _call(
                        "refresh_srd_movement_budget",
                        {"actor_state": "{_actor_state_id}"},
                        ["_movement_speed"],
                    ),
                    _set("_ps_mount_cost", "floor(_movement_speed * 0.5)"),
                    _call(
                        "spend_combat_movement",
                        {
                            "actor": "{_actor_state_id}",
                            "actor_state": "{_actor_state_id}",
                            "distance": "{_ps_mount_cost}",
                            "difficult_terrain": False,
                            "movement_kind": "walk",
                        },
                    ),
                ],
                "on_false": [
                    _call(
                        "derive_srd_scene_movement_budget",
                        {"actor_state": "{_actor_state_id}", "mode": "walk"},
                        ["_scene_exploration_remaining", "_movement_speed"],
                    ),
                    _set("_ps_mount_cost", "floor(_movement_speed * 0.5)"),
                    _require(
                        "_ps_mount_cost > 0 and _ps_mount_cost <= _scene_exploration_remaining",
                        "The actual rider needs enough movement to mount or dismount.",
                    ),
                    _set(
                        "actor_state.scene_movement_spent",
                        "actor_state.scene_movement_spent + _ps_mount_cost",
                    ),
                ],
            },
        ],
    )


def _body_distance(first, second, local):
    """Measure the closest actual supported box bodies, rather than their Large-body centers."""
    return [
        _require(
            f"{first}.footprint.kind == 'box' and {second}.footprint.kind == 'box'",
            "Mount geometry currently needs actual admitted box bodies.",
        ),
        _set(
            "_ps_distance_vector",
            [
                *[
                    f"max(0, abs({first}.position[{a}] - {second}.position[{a}]) - "
                    f"{first}.footprint.half_extents[{a}] - {second}.footprint.half_extents[{a}])"
                    for a in range(2)
                ],
                f"max(0, {first}.position[2] - {second}.position[2] - {second}.height, "
                f"{second}.position[2] - {first}.position[2] - {first}.height)",
            ],
        ),
        _call(
            "measure_srd_information_distance",
            {"start": [0, 0, 0], "end": "{_ps_distance_vector}"},
            ["_scene_distance"],
        ),
        _set(local, "_scene_distance"),
    ]


def _movement_receipt(subject, destination, operation):
    return [
        *_scene_subject(subject, "_ps_receipt_body"),
        _set(
            "state.phantom_steed_movement_receipt",
            {
                "kind": "'phantom_steed_rider'",
                "operation": repr(operation),
                "source_id": "_ps_source.id",
                "serial": "_ps_source.serial",
                "started_tick": "_ps_source.started_tick",
                "steed": "_ps_source.steed",
                "subject": subject.strip("{}"),
                "body_id": "_ps_receipt_body.id",
                "origin": "_ps_receipt_body.position",
                "footprint": "_ps_receipt_body.footprint",
                "height": "_ps_receipt_body.height",
                "destination": destination,
                "plane": "_ps_receipt_body.metadata.plane",
                "consumed": False,
            },
        ),
    ]


def _mount_tools():
    mount = _public(
        _tool_scene(
            "Mount only the real source horse as its chosen rider.",
            [_entity("actor_state"), _parameter("source_id")],
            [
                *_source(),
                _require(
                    "_ps_source.status == 'active' and state.lasting_tick < "
                    "_ps_source.expires_tick",
                    "The actual spell must still permit riding this steed.",
                ),
                _require(
                    "_ps_source.chosen_rider == _actor_state_id and _ps_source.rider == ''",
                    "Only the chosen rider can occupy this actual mount.",
                ),
                _require(
                    "actor_state.phantom_steed_mount == null and actor_state.size_rank < 3",
                    "The real Large horse needs an unmounted rider of a smaller size.",
                ),
                *_scene_subject("{_actor_state_id}", "_ps_rider_body"),
                *_scene_subject("{_ps_source.steed}", "_ps_mount_body"),
                _require(
                    "_ps_rider_body.metadata.plane == _ps_mount_body.metadata.plane",
                    "The actual horse and rider must share a plane.",
                ),
                *_body_distance("_ps_rider_body", "_ps_mount_body", "_ps_mount_distance"),
                _require("_ps_mount_distance <= 5", "The real mount must be within five feet."),
                _call(
                    "spend_srd_phantom_mount_movement",
                    {"actor_state": "{_actor_state_id}"},
                    ["_ps_mount_cost"],
                ),
                _set(
                    "_ps_mount_destination",
                    [
                        "_ps_mount_body.position[0]",
                        "_ps_mount_body.position[1]",
                        "_ps_mount_body.position[2] + _ps_source.saddle_height",
                    ],
                ),
                *_movement_receipt("{_actor_state_id}", "_ps_mount_destination", "mount"),
                _call(
                    "move_srd_phantom_steed_rider",
                    {
                        "subject": "{_actor_state_id}",
                        "destination": "{_ps_mount_destination}",
                        "plane": "{_ps_mount_body.metadata.plane}",
                    },
                ),
                _set("actor_state.phantom_steed_mount", "source_id"),
                _set(
                    "_ps_mount_patch", {"rider": "_actor_state_id", "mount_mode": "'independent'"}
                ),
                _set("_ps_source", "merge(_ps_source, _ps_mount_patch)"),
                *_save(),
                _set("state.phantom_steed_movement_receipt", None),
                _set("_ps_mounted", True),
            ],
        ),
        "movement",
        "Phantom Steed mounting",
        [("Mounted", "_ps_mounted"), ("Movement spent", "_ps_mount_cost")],
    )
    dismount = _public(
        _tool_scene(
            "Dismount the actual active or fading horse to a clear ground space.",
            [_entity("actor_state"), _parameter("source_id"), _array("destination")],
            [
                *_source(),
                _require(
                    "_ps_source.rider == _actor_state_id and actor_state.phantom_steed_mount == "
                    "source_id",
                    "Only the authentic currently mounted rider can dismount.",
                ),
                _require(
                    "_ps_source.status == 'active' or (_ps_source.status == 'fading' and "
                    "state.lasting_tick < _ps_source.fade_until)",
                    "The source's actual dismount grace must still remain.",
                ),
                *_scene_subject("{_actor_state_id}", "_ps_rider_body"),
                *_scene_subject("{_ps_source.steed}", "_ps_mount_body"),
                _set(
                    "_ps_intended_body",
                    {
                        "position": "destination",
                        "footprint": "_ps_rider_body.footprint",
                        "height": "_ps_rider_body.height",
                    },
                ),
                *_body_distance("_ps_intended_body", "_ps_mount_body", "_ps_dismount_distance"),
                _require(
                    "_ps_dismount_distance <= 5",
                    "Choose an actual dismount space within five feet of the horse.",
                ),
                _call(
                    "spend_srd_phantom_mount_movement",
                    {"actor_state": "{_actor_state_id}"},
                    ["_ps_mount_cost"],
                ),
                *_movement_receipt("{_actor_state_id}", "destination", "dismount"),
                _call(
                    "move_srd_phantom_steed_rider",
                    {
                        "subject": "{_actor_state_id}",
                        "destination": "{destination}",
                        "plane": "{_ps_rider_body.metadata.plane}",
                    },
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_actor_state_id}"},
                    POSITION_EXPORTS,
                ),
                *_ground_without_landing_requirement(),
                _require(
                    "_fall_ground_height != null and abs(_info_position[2] - "
                    "_fall_ground_height) <= 0.000001",
                    "This dismount needs an authentic supported ground endpoint.",
                ),
                _set("actor_state.phantom_steed_mount", None),
                _set("_ps_mount_patch", {"rider": "''"}),
                _set("_ps_source", "merge(_ps_source, _ps_mount_patch)"),
                *_save(),
                _set("state.phantom_steed_movement_receipt", None),
                _set("_ps_mounted", False),
            ],
        ),
        "movement",
        "Phantom Steed dismounting",
        [("Mounted", "_ps_mounted"), ("Movement spent", "_ps_mount_cost")],
    )
    return {
        "mount_srd_phantom_steed": mount,
        "dismount_srd_phantom_steed": dismount,
        "spend_srd_phantom_mount_movement": _mount_budget_tool(),
    }


def _rider_receipt_tool():
    return _tool_scene(
        "Qualify only this genuine current source rider relocation receipt.",
        [_parameter("subject"), _array("destination"), _parameter("plane")],
        [
            _set("_ps_move_receipt", "state.phantom_steed_movement_receipt"),
            _require(
                "_ps_move_receipt != null and _ps_move_receipt.kind == 'phantom_steed_rider'",
                "Only an actual source mount or dismount can relocate its rider.",
            ),
            _require(
                "_ps_move_receipt.consumed != true and _ps_move_receipt.subject == subject and "
                "_ps_move_receipt.plane == plane",
                "Choose this exact unused source rider receipt.",
            ),
            *_source("_ps_move_receipt.source_id"),
            _require(
                "_ps_source.serial == _ps_move_receipt.serial and _ps_source.started_tick == "
                "_ps_move_receipt.started_tick and _ps_source.steed == _ps_move_receipt.steed",
                "The authentic source horse must still own its rider relocation.",
            ),
            _require(
                "_ps_source.status == 'active' or (_ps_source.status == 'fading' and "
                "state.lasting_tick < _ps_source.fade_until)",
                "The actual source body must still exist.",
            ),
            _require(
                "(_ps_move_receipt.operation == 'mount' and _ps_source.rider == '' and "
                "_ps_source.chosen_rider == subject) or ((_ps_move_receipt.operation == "
                "'dismount' or _ps_move_receipt.operation == 'follow') and "
                "_ps_source.rider == subject)",
                "This exact source must own the actual riding operation.",
            ),
            *_scene_subject("{subject}", "_ps_receipt_current_body"),
            _require(
                "_ps_receipt_current_body.active == true and _ps_receipt_current_body.id == "
                "_ps_move_receipt.body_id",
                "Use the same actual current rider body.",
            ),
            *[
                _require(
                    f"_ps_receipt_current_body.position[{a}] == _ps_move_receipt.origin[{a}] "
                    f"and destination[{a}] == _ps_move_receipt.destination[{a}]",
                    "Preserve the unchanged source body and checked endpoint.",
                )
                for a in range(3)
            ],
            _require(
                "_ps_receipt_current_body.metadata.plane == plane and "
                "_ps_receipt_current_body.height == _ps_move_receipt.height",
                "Keep the actual source rider's plane and body dimensions.",
            ),
            _require(
                "_ps_receipt_current_body.footprint.kind == _ps_move_receipt.footprint.kind "
                "and _ps_receipt_current_body.footprint.kind == 'box' and "
                "_ps_receipt_current_body.footprint.half_extents[0] == "
                "_ps_move_receipt.footprint.half_extents[0] and "
                "_ps_receipt_current_body.footprint.half_extents[1] == "
                "_ps_move_receipt.footprint.half_extents[1]",
                "Preserve the same actual rider footprint.",
            ),
            _require(PENDING, "Complete the real saved window before the mount change."),
            _set("_ps_carrier_id", "_ps_source.steed"),
        ],
    )


def _rider_follow_tool():
    """An actual voluntary horse segment carries its own rider through the final receiver."""
    return _tool_scene(
        "Follow only this source horse's completed genuine budgeted scene movement.",
        [_entity("actor_state"), _array("previous_origin")],
        [
            _when(
                "actor_state.phantom_steed_source_id != null",
                [
                    *_source("actor_state.phantom_steed_source_id"),
                    _require(
                        "_ps_source.steed == _actor_state_id",
                        "Only this authentic source horse may carry its rider.",
                    ),
                    _when(
                        "_ps_source.rider != ''",
                        [
                            *_scene_subject("{_ps_source.steed}", "_ps_follow_horse"),
                            *_scene_subject("{_ps_source.rider}", "_ps_follow_rider"),
                            _require(
                                "_ps_follow_horse.active == true and "
                                "_ps_follow_rider.active == true "
                                "and _ps_follow_horse.metadata.plane == "
                                "_ps_follow_rider.metadata.plane",
                                "Preserve the real active source horse and rider bodies.",
                            ),
                            *[
                                _require(
                                    f"abs(_ps_follow_rider.position[{axis}] - "
                                    f"previous_origin[{axis}]) "
                                    "<= 0.000001",
                                    "Keep the authentic pre-move riding position.",
                                )
                                for axis in range(2)
                            ],
                            _require(
                                "abs(_ps_follow_rider.position[2] - previous_origin[2] - "
                                "_ps_source.saddle_height) <= 0.000001",
                                "Keep the real source saddle's vertical rider position.",
                            ),
                            _set(
                                "_ps_follow_destination",
                                [
                                    "_ps_follow_horse.position[0]",
                                    "_ps_follow_horse.position[1]",
                                    "_ps_follow_horse.position[2] + _ps_source.saddle_height",
                                ],
                            ),
                            *_movement_receipt(
                                "{_ps_source.rider}", "_ps_follow_destination", "follow"
                            ),
                            _call(
                                "move_srd_phantom_steed_rider",
                                {
                                    "subject": "{_ps_source.rider}",
                                    "destination": "{_ps_follow_destination}",
                                    "plane": "{_ps_follow_horse.metadata.plane}",
                                },
                            ),
                            _set("state.phantom_steed_movement_receipt", None),
                        ],
                    ),
                ],
            ),
        ],
    )


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _rider_clone(tools):
    """Retain the final sole physical receiver; exclude only its authenticated own mount."""
    changed = {}
    wrapper = deepcopy(tools["move_srd_travel_subject"])
    node_call = [
        v for v in _walk(wrapper["effects"]) if v.get("call") == "move_srd_travel_subject_node"
    ]
    owners = [("move_srd_phantom_steed_rider", wrapper)]
    if node_call:
        if len(node_call) != 1:
            raise ValueError("Keep one actual final rider relocation node.")
        node_call[0]["call"] = "move_srd_phantom_steed_rider_node"
        owners.append(
            ("move_srd_phantom_steed_rider_node", deepcopy(tools["move_srd_travel_subject_node"]))
        )
        for key in ("teleport", "levitate_source_id", "telekinesis_source_id", "sphere_source_id"):
            node_call[0]["params"].pop(key, None)
    for name, owner in owners:
        fixed = {
            "teleport": False,
            "levitate_source_id": "",
            "telekinesis_source_id": "",
            "sphere_source_id": "",
        }
        parameters = owner["parameters"]
        owner["parameters"] = [v for v in parameters if v["name"] not in fixed]
        owner["effects"][:0] = [
            _call(
                "qualify_srd_phantom_steed_rider_move",
                {"subject": "{subject}", "destination": "{destination}", "plane": "{plane}"},
                ["_ps_carrier_id"],
            ),
            *[_set(v["name"], fixed[v["name"]]) for v in parameters if v["name"] in fixed],
        ]
        for value in _walk(owner["effects"]):
            if value.get("set", {}).get("field") == "_travel_excluded":
                value["set"]["value"] = ["_ps_carrier_id"]
            query = value.get("spatial_query", {})
            if query.get("operation") in {"placement", "sweep"}:
                body = query.get("query", query)
                prior = body.get("exclude_entity_ids", [])
                if isinstance(prior, list) and "{_ps_carrier_id}" not in prior:
                    prior.append("{_ps_carrier_id}")
                    body["exclude_entity_ids"] = prior
        mutations = [v for v in _walk(owner["effects"]) if "spatial_mutation" in v]
        if mutations:
            if len(mutations) != 1 or mutations[0] not in owner["effects"]:
                raise ValueError("Keep the actual sole final rider position owner.")
            i = owner["effects"].index(mutations[0])
            owner["effects"].insert(i, _set("state.phantom_steed_movement_receipt.consumed", True))
        changed[name] = owner
    return changed


def _fade_tools():
    """At the actual outer action end, retire a faded body and offer its real rider's fall."""
    retire_actor = _tool_scene(
        "Retire only this exact faded source body, preserving other actors.",
        [_entity("actor_state"), _parameter("source_id")],
        [
            _require(
                "actor_state.phantom_steed_source_id == source_id",
                "Only the authentic source horse may fade away.",
            ),
            _set("actor_state.is_alive", False),
            _set("actor_state.body_available", False),
            _set("actor_state.vital_parts_present", False),
            _set("actor_state.phantom_steed_retired", True),
        ],
    )
    rider = _tool_scene(
        "Release only the actual source rider and qualify unsupported real space.",
        [_entity("actor_state"), _parameter("source_id")],
        [
            _require(
                "actor_state.phantom_steed_mount == source_id",
                "Only the real still-mounted source rider owns this fade consequence.",
            ),
            _set("actor_state.phantom_steed_mount", None),
            _call(
                "srd_information_scene_entity", {"subject": "{_actor_state_id}"}, POSITION_EXPORTS
            ),
            *_ground_without_landing_requirement(),
            _set("_ps_fade_falling", False),
            _when(
                "_fall_ground_height == null or _info_position[2] > _fall_ground_height + 0.000001",
                [
                    _require(
                        "actor_state.is_alive == true",
                        "The dead dropped rider needs its physical fall owner.",
                    ),
                    _call(
                        "qualify_srd_mirage_support_flight",
                        {"target_state": "{_actor_state_id}"},
                        ["_support_flight", "_support_scene_descent", "_support_wind_descent"],
                    ),
                    _when(
                        "_support_flight != true and _support_scene_descent != true and "
                        "_support_wind_descent != true",
                        [
                            _call(
                                "qualify_srd_scene_fall_target",
                                {"target_state": "{_actor_state_id}", "fall_cause": "support_lost"},
                                ["_falling_event"],
                            ),
                            _set("_ps_fade_falling", True),
                        ],
                    ),
                ],
            ),
        ],
    )
    settle = _tool_scene(
        "Settle actual earned one-minute fading at completed actions, never nested source removal.",
        [],
        [
            _when(
                "state.has_phantom_steeds == true and (" + PENDING + ")",
                [
                    _call("refresh_srd_phantom_steeds", {}),
                    _set("_ps_fade_rows", "state.phantom_steed_sources"),
                    _set("_ps_falling_riders", []),
                    _set("_ps_fade_receipts", []),
                    _loop(
                        "_ps_fade_rows",
                        "_ps_fade_row",
                        [
                            _when(
                                "_ps_fade_row.status == 'fading' and state.lasting_tick >= "
                                "_ps_fade_row.fade_until",
                                [
                                    *_source("_ps_fade_row.id"),
                                    *_scene_subject("{_ps_source.steed}", "_ps_faded_body"),
                                    _require(
                                        "_ps_faded_body.metadata.participant_ref == "
                                        "_ps_source.steed",
                                        "Preserve the exact source horse body before its actual "
                                        "retirement.",
                                    ),
                                    _set(
                                        "_ps_fade_proof",
                                        {
                                            "source": "_ps_source",
                                            "body": "_ps_faded_body",
                                            "tick": "state.lasting_tick",
                                        },
                                    ),
                                    *_deactivate("{_ps_faded_body.id}"),
                                    _call(
                                        "finish_srd_phantom_steed_fade",
                                        {
                                            "actor_state": "{_ps_source.steed}",
                                            "source_id": "{_ps_source.id}",
                                        },
                                    ),
                                    _when(
                                        "_ps_source.rider != ''",
                                        [
                                            _call(
                                                "release_srd_phantom_steed_faded_rider",
                                                {
                                                    "actor_state": "{_ps_source.rider}",
                                                    "source_id": "{_ps_source.id}",
                                                },
                                                ["_ps_fade_falling"],
                                            ),
                                            _when(
                                                "_ps_fade_falling == true",
                                                [
                                                    _push("_ps_falling_riders", "_ps_source.rider"),
                                                    _push("_ps_fade_receipts", "_ps_fade_proof"),
                                                ],
                                            ),
                                        ],
                                    ),
                                    _set(
                                        "_ps_fade_patch",
                                        {
                                            "status": "'retired'",
                                            "rider": "''",
                                            "dispel_ongoing": False,
                                            "ongoing_duration": False,
                                        },
                                    ),
                                    _set("_ps_source", "merge(_ps_source, _ps_fade_patch)"),
                                    *_save(),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "len(_ps_falling_riders) > 0",
                        [
                            _call(
                                "begin_srd_scene_fall",
                                {
                                    "target_states": "{_ps_falling_riders}",
                                    "fall_cause": "support_lost",
                                },
                                ["_pending_action_id"],
                            ),
                            _set(
                                "state.pending_srd_actions.{_pending_action_id}.context.phantom_steed_fade_receipts",
                                "_ps_fade_receipts",
                            ),
                        ],
                    ),
                    _set("_ps_remaining_live", False),
                    _set("_ps_final_rows", "state.phantom_steed_sources"),
                    _loop(
                        "_ps_final_rows",
                        "_ps_final_row",
                        [
                            _when(
                                "_ps_final_row.status == 'active' or _ps_final_row.status == "
                                "'fading'",
                                [_set("_ps_remaining_live", True)],
                            ),
                        ],
                    ),
                    _set("state.has_phantom_steeds", "_ps_remaining_live"),
                ],
            ),
        ],
    )
    dismiss = _public(
        _tool_scene(
            "Dismiss your real timed source without an action and preserve its fade.",
            [_entity("actor_state"), _parameter("source_id")],
            [
                _require(PENDING, "Complete the pending action before source dismissal."),
                _require(
                    "'incapacitated' not in actor_state.conditions",
                    "An Incapacitated caster cannot dismiss the source.",
                ),
                *_source(),
                _require(
                    "_ps_source.caster == _actor_state_id",
                    "Only the actual caster may dismiss this source.",
                ),
                _require("_ps_source.status == 'active'", "The actual source is already ending."),
                _call(
                    "retire_srd_phantom_steed_source",
                    {
                        "source_id": "{source_id}",
                        "reason": "dismiss",
                        "ended_tick": "{state.lasting_tick}",
                    },
                ),
                _set("_ps_dismissed", True),
            ],
        ),
        "spell",
        "Phantom Steed ending",
        [("Dismissed", "_ps_dismissed")],
    )
    return {
        "finish_srd_phantom_steed_fade": retire_actor,
        "release_srd_phantom_steed_faded_rider": rider,
        "settle_srd_phantom_steed_fades": settle,
        "dismiss_srd_phantom_steed": dismiss,
    }


def _cast_binding(tools, changed):
    """Replace this family's exact branch while preserving shared final failed-cast guards."""
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        parameters = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in parameters]
        branch = _when(
            "_spell_definition.family == 'phantom_steed'",
            [
                _call(
                    "cast_srd_phantom_steed",
                    {
                        "caster_state": "{_caster_state_id}",
                        "slot_level": "{_slot_level}",
                        **{p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES},
                    },
                    ["_spell_summary_rows", "_spell_detail_rows"],
                ),
            ],
        )

        def replace(effects, branch=branch):
            found = False
            for i, e in enumerate(effects):
                if "_spell_definition.family == 'phantom_steed'" in e.get("condition", ""):
                    new = deepcopy(branch)
                    new["condition"] = e["condition"]
                    effects[i] = new
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
            raise ValueError("The real paid spell dispatcher is absent.")
        changed[name] = cast


def extend_runtime(text):
    """Emit only this spell family; final receiver, damage and clock hooks bind later."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "prepare_srd_phantom_steed_actor": _preparation(tools),
        "qualify_srd_phantom_steed_chosen_rider": _tool_scene(
            "Choose an actual creature for this source's riding permission.",
            [_entity("target_state")],
            [
                _require(
                    "target_state.is_alive == true",
                    "The chosen rider must be an actual living creature.",
                ),
            ],
        ),
        "finish_srd_phantom_steed_actor": _actor_finish(),
        "create_srd_phantom_steed_scene": _scene_creation(),
        "cast_srd_phantom_steed": _casting_tool(),
        "qualify_srd_phantom_steed_rider_move": _rider_receipt_tool(),
        "follow_srd_phantom_steed_rider": _rider_follow_tool(),
        **_equipment_tools(),
        **_lifetime_tools(),
        **_mount_tools(),
        **_fade_tools(),
    }
    _cast_binding(tools, changed)
    _normalize(changed)
    text = _emit(text, changed)
    schema = {
        "type": "object",
        "properties": {
            key: {
                "type": "boolean"
                if type(value) is bool
                else "integer"
                if type(value) is int
                else "string"
            }
            for key, value in PROFILES["phantom_steed"].items()
            if key != "family"
        },
        "required": [key for key in PROFILES["phantom_steed"] if key != "family"],
        "additionalProperties": False,
    }
    row = "      phantom_steed: " + json.dumps(schema, separators=(",", ":"))
    pattern = r"(?m)^      phantom_steed: .*"
    if re.search(pattern, text):
        text = re.sub(pattern, lambda _: row, text)
    else:
        variants = _load_runtime(text)["definition_row_schemas"]["definitions.spells"]["variants"]
        anchor = "      " + next(iter(variants)) + ":"
        if text.count(anchor) != 1:
            raise ValueError("The source family needs one admitted schema anchor.")
        text = text.replace(anchor, row + "\n" + anchor, 1)
    return text


def bind_damage_consumers(text):
    """Observe actual receiving packets before Dream and Prismatic capture their final copies."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in (
        "apply_srd_damage",
        "apply_srd_qualifying_damage",
        "apply_srd_composite_damage",
        "apply_srd_qualifying_composite_damage",
    ):
        if name not in tools:
            continue
        owner = deepcopy(tools[name])
        if "observe_srd_phantom_steed_damage" in str(owner["effects"]):
            continue
        owner["effects"].append(
            _when(
                "lasting_state.has_phantom_steeds == true and _effective_damage > 0",
                [_call("observe_srd_phantom_steed_damage", {"target_state": "{target}"})],
            )
        )
        changed[name] = owner
    if not changed:
        return text
    _normalize(changed)
    return _emit(text, changed)


def bind_runtime_consumers(text):
    """Bind final physical and clock owners without changing captured receiving packets."""
    tools = _load_runtime(text)["tools"]
    # Capture our actual movement aftermath on the first pass as well as regeneration.
    travel = deepcopy(tools["move_srd_travel_subject"])
    if "refresh_all_srd_phantom_steed_equipment" not in str(travel["effects"]):
        travel["effects"].append(_call("refresh_all_srd_phantom_steed_equipment", {}))
    tools["move_srd_travel_subject"] = travel
    changed = _rider_clone(tools)
    changed["move_srd_travel_subject"] = travel
    from_names = (
        "advance_srd_lasting_time",
        "advance_srd_turn",
        "complete_srd_pending_action",
        "cast_spell",
        "cast_spell_outside_combat",
        "continue_srd_rest",
        "dismiss_srd_phantom_steed",
        "move_srd_scene_creature",
        "dismount_srd_phantom_steed",
        "mount_srd_phantom_steed",
    )
    for name in from_names:
        if name not in tools:
            continue
        owner = deepcopy(tools[name])
        if "settle_srd_phantom_steed_fades" not in str(owner["effects"]):
            owner["effects"].append(_call("settle_srd_phantom_steed_fades", {}))
        changed[name] = owner
    if "move_srd_scene_creature" in changed:
        owner = changed["move_srd_scene_creature"]
        if "Dismount the actual source horse" not in str(owner["effects"]):
            owner["effects"].insert(
                0,
                _require(
                    "actor_state.phantom_steed_mount == null",
                    "Dismount the actual source horse before spending the rider's own movement.",
                ),
            )
    for name in ("move_srd_scene_creature_environment_segment", "move_srd_scene_creature_node"):
        if name not in tools:
            continue
        owner = deepcopy(tools[name])
        mutations = [e for e in owner["effects"] if "spatial_mutation" in e]
        if not mutations:
            continue
        if len(mutations) != 1:
            raise ValueError("Keep one actual completed horse segment position mutation.")
        if "follow_srd_phantom_steed_rider" not in str(owner["effects"]):
            index = owner["effects"].index(mutations[0]) + 1
            owner["effects"].insert(
                index,
                _when(
                    "actor_state.phantom_steed_source_id != null",
                    [
                        _call(
                            "follow_srd_phantom_steed_rider",
                            {
                                "actor_state": "{_actor_state_id}",
                                "previous_origin": "{_move_scene_entity.position}",
                            },
                        ),
                    ],
                ),
            )
        changed[name] = owner
    for name in (
        "move_srd_scene_creature",
        "move_srd_travel_subject",
        "handle_srd_phantom_steed_equipment",
    ):
        if name not in tools:
            continue
        owner = changed.get(name, deepcopy(tools[name]))
        if "refresh_all_srd_phantom_steed_equipment" not in str(owner["effects"]):
            owner["effects"].append(_call("refresh_all_srd_phantom_steed_equipment", {}))
        changed[name] = owner
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_phantom_steeds" not in str(clock["effects"]):
        clock["effects"].append(_call("refresh_srd_phantom_steeds", {}))
    changed["advance_srd_lasting_clock"] = clock
    _normalize(changed)
    return _emit(text, changed)
