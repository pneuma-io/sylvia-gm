"""Arcane Hand owns a Large, nonoccupying object and its paid force commands."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _snapshot, _spatial, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _replace_tools
else:
    from srd_scene_relations import _array, _snapshot, _spatial, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _replace_tools

PROFILE = {
    "family": "arcane_hand",
    "casting_time": "action",
    "base_level": 5,
    "range_kind": "distance",
    "range_feet": 120,
    "duration_rounds": 10,
    "concentration": True,
    "move_feet": 60,
    "base_dice": 5,
    "damage_die": "d8",
    "dice_per_slot_level": 2,
}
PUBLIC_TOOLS = {"command_srd_arcane_hand"}
SOURCE = (
    "wotc.srd.5.2.1:spells:p110:03b22c8bed6c642b84d33dea11fde8bbaaf3a42816ee7804e61f959ed945dec5"
)
OPTIONS = ["none", "clenched_fist", "forceful_hand", "grasping_hand", "interposing_hand"]
CAST_CHOICES = [
    _optional("hand_id", default=""),
    _array("hand_position", default=[0, 0, 0]),
    _optional("hand_height", "number", None, minimum=0.01, maximum=1000),
    _optional("hand_option", "enum", "none", enum_values=OPTIONS),
    _optional("hand_target", default=None, resolve="entity"),
    _parameter(
        "hand_destination",
        "array",
        required=False,
        default=[],
        items={"type": "number"},
        min_items=0,
        max_items=3,
    ),
    _optional("hand_push_feet", "number", 0, minimum=0, maximum=100),
    _optional("hand_crush", "boolean", False),
]
POSITION = ("_info_scene_entity", "_info_position")
RESULTS = (
    "_spell_summary_rows",
    "_spell_detail_rows",
    "_spell_attack_outcome",
    "_spell_damage_amount",
)


def schema():
    properties = {
        key: {
            "type": "boolean"
            if type(value) is bool
            else "integer"
            if type(value) is int
            else "string",
            "enum": [value],
        }
        for key, value in PROFILE.items()
        if key != "family"
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    for spell, profile in definitions.items():
        if profile.get("family") == "arcane_hand" and (
            spell != "arcane_hand" or profile != PROFILE
        ):
            raise SystemExit("Arcane Hand differs from its printed fixed profile.")


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _branch(condition, on_true, on_false):
    return {**_when(condition, on_true), "on_false": on_false}


def _supported():
    return _require(
        "(hand_option == 'none' or hand_option == 'interposing_hand') == (force_target == null)",
        "Choose a creature only for a striking, pushing or grasping hand.",
    )


def _creator():
    """Reuse the genuine Large placement owner, then retain the printed object's own profile."""
    return _tool_scene(
        "Create the exact source's visible, unoccupied Large object without making it a creature.",
        [
            _entity("caster_state"),
            _parameter("force_id"),
            _parameter("spell"),
            _array("position"),
            _parameter("hand_height", "number"),
        ],
        [
            _require(
                "spell == 'arcane_hand' and caster_state.max_hp > 0",
                "The hand uses this real caster's HP maximum.",
            ),
            _call(
                "resolve_srd_illumination_point_sight",
                {"observer_state": "{_caster_state_id}", "point": "{position}"},
                ("_illumination_point_can_see", "_illumination_point_lighting"),
            ),
            _require(
                "_illumination_point_can_see == true",
                "The actual hand creation space must be visible.",
            ),
            _call(
                "create_srd_source_actor_scene",
                {
                    "caster_state": "{_caster_state_id}",
                    "summon_id": "{force_id}",
                    "spell": "arcane_hand",
                    "range_feet": 120,
                    "half_extent": 5,
                    "creature_type": "spell_force",
                    "created_actor_position": "{position}",
                    "created_actor_height": "{hand_height}",
                    "created_actor_lighting": "{_illumination_point_lighting}",
                },
            ),
            _call("srd_information_scene_entity", {"subject": "{force_id}"}, POSITION),
            _set(
                "_hand_profile",
                {
                    "kind": "'arcane_hand'",
                    "source": repr(SOURCE),
                    "source_id": "force_id",
                    "caster": "_caster_state_id",
                    "armor_class": 20,
                    "max_hp": "caster_state.max_hp",
                    "hp": "caster_state.max_hp",
                    "size_rank": 3,
                },
            ),
            _set(
                "_hand_metadata_patch",
                {
                    "participant_ref": None,
                    "creature_type": None,
                    "is_object": True,
                    "spell_force": True,
                    "arcane_hand": "_hand_profile",
                },
            ),
            _set("_hand_metadata", "merge(_info_scene_entity.metadata, _hand_metadata_patch)"),
            *_snapshot(),
            {
                "spatial_mutation": {
                    "scene": "state.spatial_scene",
                    "map_id": "{state.spatial_scene.map.id}",
                    "expected_revision": "{_info_snapshot.geometry_revision}",
                    "target": {"kind": "entity", "id": "{force_id}"},
                    "patch": {
                        "movement": "passable",
                        "visibility": "clear",
                        "metadata": "{_hand_metadata}",
                    },
                },
                "store_as": "_hand_created",
            },
        ],
    )


def _collect():
    return _tool_scene(
        "Authenticate this live hand's exact force lifetime and current object body.",
        [_entity("caster_state"), _parameter("force_id")],
        [
            _call(
                "collect_srd_spell_force",
                {"caster_state": "{_caster_state_id}", "force_id": "{force_id}"},
                ("_force_record", "_force_profile"),
            ),
            _require(
                "_force_record.spell == 'arcane_hand' and _force_record.cast_id != "
                "null and _force_record.suppressed != true",
                "Choose this caster's active, unsuppressed Arcane Hand.",
            ),
            _call("srd_information_scene_entity", {"subject": "{force_id}"}, POSITION),
            _require(
                "_info_scene_entity.id == force_id and "
                "_info_scene_entity.metadata.participant_ref == null and "
                "_info_scene_entity.metadata.arcane_hand.source_id == force_id and "
                "_info_scene_entity.metadata.arcane_hand.caster == "
                "_caster_state_id and _info_scene_entity.metadata.arcane_hand.hp > "
                "0",
                "The hand must retain its actual living source-owned object body.",
            ),
        ],
    )


def _movement():
    """Translate the real Box body through supported geometry using the existing move owner."""
    return _tool_scene(
        "Move a saved nonoccupying hand up to sixty feet along an actually clear solid path.",
        [_entity("caster_state"), _parameter("force_id"), _array("destination")],
        [
            _call(
                "collect_srd_arcane_hand",
                {"caster_state": "{_caster_state_id}", "force_id": "{force_id}"},
                ("_force_record", "_force_profile", "_info_scene_entity", "_info_position"),
            ),
            _set("_hand_plane", "_info_scene_entity.metadata.plane"),
            _call(
                "measure_srd_information_distance",
                {"start": "{_info_position}", "end": "{destination}"},
                ("_scene_distance",),
            ),
            _require(
                "_scene_distance <= 60", "The hand can move at most sixty feet with this command."
            ),
            _set("_hand_material_plane", "_hand_plane == 'material'"),
            _spatial(
                "sweep",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "moving_entity_id": "{force_id}",
                    "destination": "{destination}",
                    "include_entities": False,
                    "metadata_filter": {
                        "key": "plane",
                        "value": "{_hand_plane}",
                        "missing_matches": "{_hand_material_plane}",
                    },
                },
                "_hand_sweep",
            ),
            _require(
                "_hand_sweep.supported == true and _hand_sweep.clear == true",
                "The supported hand body cannot move through a solid obstruction.",
            ),
            _call(
                "move_srd_travel_subject",
                {
                    "subject": "{force_id}",
                    "destination": "{destination}",
                    "plane": "{_hand_plane}",
                    "teleport": False,
                },
            ),
        ],
    )


def _attack(tools):
    """Retain the actual force attack/card/typed packet; replace only its source and body reach."""
    attack = deepcopy(tools["attack_srd_spell_force"])
    attack["description"] = (
        "Resolve a genuine Clenched Fist melee spell attack from this hand's actual Box body."
    )
    effects = attack["effects"]
    next(e for e in effects if e.get("call") == "collect_srd_spell_force")["call"] = (
        "collect_srd_arcane_hand"
    )
    # The inherited point distance is narrowed to actual Box-body distance, preserving later cover.
    hand_index = next(
        i
        for i, e in enumerate(effects)
        if e.get("set", {}).get("field") == "_force_attack_position"
    )
    effects.insert(hand_index + 1, _set("_hand_attack_body", "_info_scene_entity"))
    index = next(
        i for i, e in enumerate(effects) if e.get("call") == "measure_srd_information_distance"
    )
    nearest = [
        _require(
            "force_target.creature_kind == 'character' or force_target.creature_kind == 'monster'",
            "This bounded Clenched Fist path targets a real creature; object "
            "strikes remain unsupported.",
        ),
        _require(
            "_info_scene_entity.footprint.kind == 'box' and "
            "_hand_attack_body.footprint.kind == 'box'",
            "The current Clenched Fist range qualifier supports actual Box bodies.",
        ),
        _set("_hand_target_body", "_info_scene_entity"),
    ]
    for axis in range(2):
        nearest.extend(
            [
                _set(
                    f"_hand_near_{axis}",
                    f"max(_hand_attack_body.position[{axis}] - "
                    f"_hand_attack_body.footprint.half_extents[{axis}], "
                    f"min(_hand_target_body.position[{axis}], "
                    f"_hand_attack_body.position[{axis}] + "
                    f"_hand_attack_body.footprint.half_extents[{axis}]))",
                ),
                _set(
                    f"_hand_target_near_{axis}",
                    f"max(_hand_target_body.position[{axis}] - "
                    f"_hand_target_body.footprint.half_extents[{axis}], "
                    f"min(_hand_near_{axis}, _hand_target_body.position[{axis}] + "
                    f"_hand_target_body.footprint.half_extents[{axis}]))",
                ),
            ]
        )
    nearest.extend(
        [
            _set(
                "_hand_near_z",
                "max(_hand_attack_body.position[2], "
                "min(_hand_target_body.position[2], _hand_attack_body.position[2] "
                "+ _hand_attack_body.height))",
            ),
            _set(
                "_hand_target_near_z",
                "max(_hand_target_body.position[2], min(_hand_near_z, "
                "_hand_target_body.position[2] + _hand_target_body.height))",
            ),
            _set("_force_attack_position", ["_hand_near_0", "_hand_near_1", "_hand_near_z"]),
            _set(
                "_force_attack_target",
                ["_hand_target_near_0", "_hand_target_near_1", "_hand_target_near_z"],
            ),
        ]
    )
    effects[index:index] = nearest
    for sequence in _lists(effects):
        for effect in sequence:
            if effect.get("call") == "cast_spell_attack_damage":
                effect["params"].update(
                    flat_damage=0,
                    spell_cast_id="{_force_record.cast_id}",
                    actual_spell_level="{_force_record.slot_level}",
                    spell_cast_owner="{_caster_state_id}",
                )
    return attack


def _reach():
    """Use the actual nearest points of the hand and creature's admitted Box bodies."""
    effects = [
        _call(
            "collect_srd_arcane_hand",
            {"caster_state": "{_caster_state_id}", "force_id": "{force_id}"},
            ("_force_record", "_info_scene_entity"),
        ),
        _set("_hand_body", "_info_scene_entity"),
        _call("srd_information_scene_entity", {"subject": "{_force_target_id}"}, POSITION),
        _set("_hand_target_body", "_info_scene_entity"),
        _require(
            "force_target.is_alive == true and (force_target.creature_kind == 'character' or "
            "force_target.creature_kind == 'monster') and force_target.size_rank <= 4",
            "The hand pushes or grapples an actual Huge or smaller creature.",
        ),
        _require(
            "_hand_body.footprint.kind == 'box' and _hand_target_body.footprint.kind == 'box' "
            "and _hand_body.metadata.plane == _hand_target_body.metadata.plane",
            "This hand reach needs actual same-plane Box bodies.",
        ),
    ]
    for axis in range(3):
        hand_min = f"_hand_body.position[{axis}]" + (
            f" - _hand_body.footprint.half_extents[{axis}]" if axis < 2 else ""
        )
        hand_max = f"_hand_body.position[{axis}] + " + (
            f"_hand_body.footprint.half_extents[{axis}]" if axis < 2 else "_hand_body.height"
        )
        target_min = f"_hand_target_body.position[{axis}]" + (
            f" - _hand_target_body.footprint.half_extents[{axis}]" if axis < 2 else ""
        )
        target_max = f"_hand_target_body.position[{axis}] + " + (
            f"_hand_target_body.footprint.half_extents[{axis}]"
            if axis < 2
            else "_hand_target_body.height"
        )
        effects += [
            _set(
                f"_hand_near_{axis}",
                f"max({hand_min}, min(_hand_target_body.position[{axis}], {hand_max}))",
            ),
            _set(
                f"_hand_target_near_{axis}",
                f"max({target_min}, min(_hand_near_{axis}, {target_max}))",
            ),
        ]
    effects += [
        _set("_hand_reach_start", [f"_hand_near_{i}" for i in range(3)]),
        _set("_hand_reach_end", [f"_hand_target_near_{i}" for i in range(3)]),
        _call(
            "measure_srd_information_distance",
            {"start": "{_hand_reach_start}", "end": "{_hand_reach_end}"},
            ("_scene_distance",),
        ),
        _require(
            "require_reach != true or _scene_distance <= 5",
            "The creature must be within five feet of the actual hand.",
        ),
    ]
    return _tool_scene(
        "Qualify the selected hand's real creature reach.",
        [
            _entity("caster_state"),
            _parameter("force_id"),
            _entity("force_target"),
            _optional("require_reach", "boolean", True),
        ],
        effects,
    )


def _grapple_tools():
    release = _tool_scene(
        "Release one hand's grip, preserving independent hand and ordinary grapples.",
        [_parameter("force_id"), _entity("force_target")],
        [
            _set("state.spell_force_records.{force_id}.grapple_target", None),
            _call("project_srd_arcane_hand_grapples", {"force_target": "{_force_target_id}"}),
        ],
    )
    project = _tool_scene(
        "Project actual retained hand grips without erasing an independent Grappled source.",
        [_entity("force_target")],
        [
            _when(
                "force_target.arcane_hand_grapple_ids != null and "
                "'grappled' in force_target.conditions and force_target.grappled_by != null and "
                "force_target.grappled_by not in force_target.arcane_hand_grapple_ids",
                [
                    _set("force_target.arcane_hand_unowned_grappled", True),
                    _set("force_target.arcane_hand_prior_grappler", "force_target.grappled_by"),
                    _set(
                        "force_target.arcane_hand_prior_escape_dc", "force_target.grapple_escape_dc"
                    ),
                ],
            ),
            _set("_hand_grips", []),
            _set("_hand_grip_dc", 0),
            _set("_hand_grip_id", None),
            _set("_hand_ids", "state.spell_force_ids"),
            _loop(
                "_hand_ids",
                "_hand_id",
                [
                    _set("_hand_grip", "state.spell_force_records[_hand_id]"),
                    _when(
                        "_hand_grip.spell == 'arcane_hand' and _hand_grip.active == true and "
                        "_hand_grip.grapple_target == _force_target_id and state.lasting_tick <"
                        " _hand_grip.expires_tick and _hand_grip.suppressed != true",
                        [
                            {"list_push": {"list": "_hand_grips", "value": "_hand_id"}},
                            _when(
                                "_hand_grip.save_dc >= _hand_grip_dc",
                                [
                                    _set("_hand_grip_dc", "_hand_grip.save_dc"),
                                    _set("_hand_grip_id", "_hand_id"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("force_target.arcane_hand_grapple_ids", "_hand_grips"),
            _branch(
                "len(_hand_grips) > 0",
                [
                    {"apply_condition": {"target": "force_target", "condition": "grappled"}},
                    _set("force_target.grappled_by", "_hand_grip_id"),
                    _set("force_target.grapple_escape_dc", "_hand_grip_dc"),
                ],
                [
                    _set("force_target.arcane_hand_grapple_ids", None),
                    _when(
                        "force_target.arcane_hand_unowned_grappled != true",
                        [{"remove_condition": {"target": "force_target", "condition": "grappled"}}],
                    ),
                    _set("force_target.grappled_by", "force_target.arcane_hand_prior_grappler"),
                    _set(
                        "force_target.grapple_escape_dc", "force_target.arcane_hand_prior_escape_dc"
                    ),
                ],
            ),
        ],
    )
    return {"release_srd_arcane_hand_grapple": release, "project_srd_arcane_hand_grapples": project}


def _options(tools):
    """Express the three printed options using saved source statistics and real receivers."""
    target = "{_force_target_id}"
    hand = {"caster_state": "{_caster_state_id}", "force_id": "{force_id}"}
    save = [
        _set("_hand_save_modifier", "force_target.saving_throw_modifiers[_hand_save_ability]"),
        _require(
            "_hand_save_modifier != null", "The target needs its actual saving throw modifier."
        ),
        _call(
            "d20_test",
            {
                "actor_state": target,
                "ability": "{_hand_save_ability}",
                "kind": "saving_throw",
                "ability_modifier": "{_hand_save_modifier}",
                "proficiency_bonus": 0,
                "proficiency_factor": "none",
                "circumstantial_modifier": 0,
                "target_number": "{_force_record.save_dc}",
                "advantage": False,
                "disadvantage": False,
                "forego_save": False,
                "save_avoids_condition": "{_hand_save_condition}",
            },
            ("_outcome", "_d20_test", "_adjusted_total"),
        ),
        _set(
            "state.spell_force_records.{force_id}.last_option_save",
            {
                "target": "_force_target_id",
                "ability": "_hand_save_ability",
                "dc": "_force_record.save_dc",
                "roll": "_d20_test",
                "total": "_adjusted_total",
                "outcome": "_outcome",
            },
        ),
    ]
    push = [
        _require(
            "hand_push_feet <= max(0, 5 + _force_record.casting_modifier * 5)",
            "The push exceeds five feet plus five times the saved casting modifier.",
        ),
        _call(
            "measure_srd_information_distance",
            {"start": "{_hand_body.position}", "end": "{_hand_target_body.position}"},
            ("_scene_distance", "_scene_direction"),
        ),
        _require(
            "hand_push_feet == 0 or _scene_distance > 0",
            "A positive push needs an actual direction away from the hand.",
        ),
        _set("_hand_push_direction", "_scene_direction"),
        _set(
            "_hand_push_target",
            [
                f"_hand_target_body.position[{i}] + _hand_push_direction[{i}] * hand_push_feet"
                for i in range(3)
            ],
        ),
        _set(
            "_hand_push_follow",
            [
                f"_hand_body.position[{i}] + _hand_push_direction[{i}] * hand_push_feet"
                for i in range(3)
            ],
        ),
        _call(
            "move_srd_travel_subject",
            {
                "subject": target,
                "destination": "{_hand_push_target}",
                "plane": "{_hand_body.metadata.plane}",
                "teleport": False,
            },
        ),
        _call("srd_information_scene_entity", {"subject": target}, POSITION),
        _require(
            "_info_position[0] == _hand_push_target[0] and "
            "_info_position[1] == _hand_push_target[1] and "
            "_info_position[2] == _hand_push_target[2]",
            "The chosen push endpoint must be physically clear.",
        ),
        _call("move_srd_arcane_hand", {**hand, "destination": "{_hand_push_follow}"}),
    ]
    grip = [
        _call(
            "check_srd_target_condition_immunity",
            {
                "target_state": target,
                "condition": "grappled",
                "source_state_id": "{_caster_state_id}",
                "source_is_magical": True,
            },
            ("_source_condition_immune",),
        ),
        _when(
            "_source_condition_immune != true",
            [
                _when(
                    "force_target.arcane_hand_grapple_ids == null",
                    [
                        _set(
                            "force_target.arcane_hand_unowned_grappled",
                            "'grappled' in force_target.conditions",
                        ),
                        _set("force_target.arcane_hand_prior_grappler", None),
                        _set("force_target.arcane_hand_prior_escape_dc", None),
                        _when(
                            "'grappled' in force_target.conditions",
                            [
                                _set(
                                    "force_target.arcane_hand_prior_grappler",
                                    "force_target.grappled_by",
                                ),
                                _set(
                                    "force_target.arcane_hand_prior_escape_dc",
                                    "force_target.grapple_escape_dc",
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    "_force_record.grapple_target != null and _force_record.grapple_target != "
                    "_force_target_id",
                    [
                        _call(
                            "release_srd_arcane_hand_grapple",
                            {
                                "force_id": "{force_id}",
                                "force_target": "{_force_record.grapple_target}",
                            },
                        )
                    ],
                ),
                _set("state.spell_force_records.{force_id}.grapple_target", "_force_target_id"),
                _call("project_srd_arcane_hand_grapples", {"force_target": target}),
            ],
        ),
    ]
    damage_params = {
        "target": target,
        "amount": "{_spell_damage_amount}",
        "damage_type": "bludgeoning",
        "adjustment": 0,
        "source_state_id": "{_caster_state_id}",
        "source_damage_kind": "spell",
        "spell_cast_id": "{_force_record.cast_id}",
        "spell_cast_owner": "{_caster_state_id}",
        "actual_spell_level": "{_force_record.slot_level}",
    }
    damage_names = {p["name"] for p in tools["apply_srd_qualifying_damage"]["parameters"]}
    damage_params = {k: v for k, v in damage_params.items() if k in damage_names}
    crush = [
        _require(
            "_force_record.grapple_target == _force_target_id and 'grappled' in "
            "force_target.conditions and force_id in force_target.arcane_hand_grapple_ids",
            "Crush only this hand's actual retained Grappled creature.",
        ),
        _set("_hand_crush_dice", "4 + (_force_record.slot_level - 5) * 2"),
        {"dice": {"expression": "{_hand_crush_dice}d6"}, "store_as": "_hand_crush_roll"},
        _set(
            "_spell_damage_amount",
            "max(0, _hand_crush_roll.total + _force_record.casting_modifier)",
        ),
        _call(
            "apply_srd_qualifying_damage",
            damage_params,
            ("_effective_damage", "_is_alive_after"),
        ),
    ]
    effects = [
        _call("collect_srd_arcane_hand", hand, ("_force_record",)),
        _set("_spell_damage_amount", 0),
        _set("_spell_attack_outcome", None),
        _set("_spell_summary_rows", []),
        _set("_spell_detail_rows", []),
        _require(
            "hand_crush != true or hand_option == 'grasping_hand'",
            "Crushing is the saved Grasping Hand option.",
        ),
        _set("state.spell_force_records.{force_id}.hand_option", "hand_option"),
        _set("caster_state.arcane_hand_interposing", "hand_option == 'interposing_hand'"),
        _set("caster_state.arcane_hand_id", "force_id"),
        _set("state.has_arcane_hands", True),
        _when(
            "hand_option == 'clenched_fist'",
            [_call("attack_srd_arcane_hand", {**hand, "force_target": target}, RESULTS)],
        ),
        _when(
            "hand_option == 'forceful_hand' or hand_option == 'grasping_hand'",
            [
                _call(
                    "qualify_srd_arcane_hand_reach",
                    {**hand, "force_target": target},
                    ("_force_record", "_hand_body", "_hand_target_body"),
                ),
                _branch(
                    "hand_crush == true",
                    crush,
                    [
                        _set("_hand_save_ability", "'strength'"),
                        _set("_hand_save_condition", "'none'"),
                        _when(
                            "hand_option == 'grasping_hand'",
                            [
                                _set("_hand_save_ability", "'dexterity'"),
                                _set("_hand_save_condition", "'grappled'"),
                            ],
                        ),
                        *save,
                        _when(
                            "_outcome == 'failure'",
                            [_branch("hand_option == 'forceful_hand'", push, grip)],
                        ),
                    ],
                ),
            ],
        ),
    ]
    return _tool_scene(
        "Resolve the chosen printed hand option from its exact saved casting source.",
        [
            _entity("caster_state"),
            _parameter("force_id"),
            _optional("force_target", default=None, resolve="entity"),
            deepcopy(CAST_CHOICES[3]),
            *deepcopy(CAST_CHOICES[-2:]),
        ],
        effects,
    )


def _interposing_tools():
    """Reuse the admitted hand body and existing interval/terrain operations, without a planner."""
    volume = {
        "kind": "'box'",
        "min": ["_info_position[0] - 5", "_info_position[1] - 5", "_info_position[2]"],
        "max": [
            "_info_position[0] + 5",
            "_info_position[1] + 5",
            "_info_position[2] + _info_scene_entity.height",
        ],
    }
    live = (
        "_hand_interposer.spell == 'arcane_hand' and _hand_interposer.active == true and "
        "_hand_interposer.hand_option == 'interposing_hand' and _hand_interposer.suppressed != true"
        " and state.lasting_tick < _hand_interposer.expires_tick"
    )
    cover = _tool_scene(
        "Qualify printed Half Cover when an actual effect originates in or passes through this "
        "caster's interposing hand.",
        [_entity("target_state"), _parameter("origin_subject")],
        [
            _set("_hand_cover_bonus", 0),
            _when(
                "state.has_spell_forces == true and len(origin_subject) > 0",
                [
                    _call(
                        "srd_information_scene_entity", {"subject": "{origin_subject}"}, POSITION
                    ),
                    _set("_hand_cover_origin", "_info_position"),
                    _set("_hand_cover_plane", "_info_scene_entity.metadata.plane"),
                    _call(
                        "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION
                    ),
                    _set("_hand_cover_target", "_info_position"),
                    _set("_hand_interposer_ids", "state.spell_force_ids"),
                    _loop(
                        "_hand_interposer_ids",
                        "_hand_interposer_id",
                        [
                            _set(
                                "_hand_interposer", "state.spell_force_records[_hand_interposer_id]"
                            ),
                            _when(
                                live + " and _hand_interposer.caster == _target_state_id",
                                [
                                    _call(
                                        "srd_information_scene_entity",
                                        {"subject": "{_hand_interposer_id}"},
                                        POSITION,
                                    ),
                                    _set("_hand_interposing_volume", volume),
                                    _when(
                                        "_info_scene_entity.metadata.plane == _hand_cover_plane",
                                        [
                                            _call(
                                                "srd_information_segment_interval",
                                                {
                                                    "start": "{_hand_cover_origin}",
                                                    "end": "{_hand_cover_target}",
                                                    "volume": "{_hand_interposing_volume}",
                                                },
                                                ("_segment_hit",),
                                            ),
                                            _when(
                                                "_segment_hit == true",
                                                [_set("_hand_cover_bonus", 2)],
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
    terrain = _tool_scene(
        "Derive enemy-only difficult intervals from current interposing hand spaces.",
        [_parameter("actor_subject")],
        [
            _set("_hand_interposing_zones", []),
            _when(
                "state.has_spell_forces == true and len(actor_subject) > 0",
                [
                    _call("srd_information_scene_entity", {"subject": "{actor_subject}"}, POSITION),
                    _set("_hand_enemy_of", []),
                    _when(
                        "_info_scene_entity.metadata.enemy_of != null",
                        [_set("_hand_enemy_of", "_info_scene_entity.metadata.enemy_of")],
                    ),
                    _set("_hand_interposer_ids", "state.spell_force_ids"),
                    _loop(
                        "_hand_interposer_ids",
                        "_hand_interposer_id",
                        [
                            _set(
                                "_hand_interposer", "state.spell_force_records[_hand_interposer_id]"
                            ),
                            _when(
                                live + " and _hand_interposer.caster in _hand_enemy_of",
                                [
                                    _call(
                                        "srd_information_scene_entity",
                                        {"subject": "{_hand_interposer_id}"},
                                        POSITION,
                                    ),
                                    _set(
                                        "_hand_interposing_zone",
                                        {
                                            "shape": volume,
                                            "metadata": {
                                                "plane": "_info_scene_entity.metadata.plane",
                                                "difficult": True,
                                            },
                                        },
                                    ),
                                    {
                                        "list_push": {
                                            "list": "_hand_interposing_zones",
                                            "value": "_hand_interposing_zone",
                                        }
                                    },
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {"qualify_srd_arcane_hand_cover": cover, "collect_srd_arcane_hand_terrain": terrain}


def _cast(tools):
    """Reuse the source installer and immediate force result, without a second action or slot."""
    cast = deepcopy(tools["cast_spell_created_spell_force"])
    if not any(p["name"] == "spell_cast_id" for p in cast["parameters"]):
        cast["parameters"].append(_parameter("spell_cast_id"))
    cast["parameters"] += [*deepcopy(CAST_CHOICES[2:4]), *deepcopy(CAST_CHOICES[5:])]
    cast["effects"][:0] = [
        _set("_spell_attack_outcome", None),
        _set("_spell_damage_amount", 0),
        _require(
            "spell == 'arcane_hand' and spell_cast_id != '' and "
            "caster_state.last_spell_source_cast_id == spell_cast_id and "
            "caster_state.last_spell_source_spell == spell",
            "Install only this committed paid Arcane Hand source.",
        ),
        _supported(),
        _require(
            "combat.encounter_active != true or caster_state.turns_started != null",
            "Combat casting needs the caster's actual recorded turn.",
        ),
        _require(
            "hand_height != null and (len(hand_destination) == 0 or len(hand_destination) == 3)",
            "Admit the created object's actual height and an optional "
            "three-coordinate movement intent.",
        ),
        _require(
            "hand_crush != true",
            "The initial Grasping option attempts a grapple; crushing uses a separate Bonus "
            "Action.",
        ),
    ]
    for sequence in _lists(cast["effects"]):
        for effect in sequence:
            setting = effect.get("set", {})
            if setting.get("field") == "_force_record":
                setting["value"].update(
                    cast_id="spell_cast_id",
                    source_caster_id="_caster_state_id",
                    spell_level="slot_level",
                    suppressed=False,
                    ongoing_duration=True,
                )
            if effect.get("call") == "create_srd_spell_force_scene":
                effect["call"] = "create_srd_arcane_hand_scene"
                effect["params"]["hand_height"] = "{hand_height}"
            if effect.get("call") == "attack_srd_spell_force":
                effect["call"] = "apply_srd_arcane_hand_option"
                effect["params"].update(
                    hand_option="{hand_option}", hand_push_feet="{hand_push_feet}", hand_crush=False
                )
                effect["export_context"] = {name: name for name in RESULTS}
            if effect.get("condition") == "_force_profile.base_dice > 0 and force_target != null":
                effect["condition"] = "hand_option != 'none'"
    creation = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "create_srd_arcane_hand_scene"
    )
    cast["effects"].insert(
        creation + 1, _set("caster_state.concentration_cast_id", "spell_cast_id")
    )
    cast["effects"].insert(
        creation + 2,
        _when(
            "len(hand_destination) == 3",
            [
                _call(
                    "move_srd_arcane_hand",
                    {
                        "caster_state": "{_caster_state_id}",
                        "force_id": "{force_id}",
                        "destination": "{hand_destination}",
                    },
                )
            ],
        ),
    )
    return cast


def _command(tools):
    parent = tools["command_srd_spell_force"]
    delegate = next(
        (e for e in parent["effects"] if e.get("call") == "command_srd_spell_force_node"),
        None,
    )
    command = deepcopy(tools[delegate["call"]] if delegate else parent)
    command["parameters"] += [deepcopy(CAST_CHOICES[3]), *deepcopy(CAST_CHOICES[-2:])]
    command["effects"].insert(0, _supported())
    for sequence in _lists(command["effects"]):
        for effect in sequence:
            if effect.get("call") == "collect_srd_spell_force":
                effect["call"] = "collect_srd_arcane_hand"
            if effect.get("call") == "attack_srd_spell_force":
                effect["call"] = "apply_srd_arcane_hand_option"
                effect["params"].update(
                    hand_option="{hand_option}",
                    hand_push_feet="{hand_push_feet}",
                    hand_crush="{hand_crush}",
                )
            if effect.get("condition") == "_force_profile.base_dice > 0 and force_target != null":
                effect["condition"] = "hand_option != 'none'"
            if (
                effect.get("require", {}).get("condition")
                == "_force_profile.family == 'created_spell_force'"
            ):
                effect["require"]["condition"] = "_force_profile.family == 'arcane_hand'"
            requirement = effect.get("require", {})
            if requirement.get("condition", "").startswith(
                "combat.encounter_active != true or _force_record.spell"
            ):
                requirement["condition"] = (
                    "hand_crush == true or (" + requirement["condition"] + ")"
                )
            if (
                effect.get("call") == "srd_information_scene_entity"
                and effect["params"].get("subject") == "{force_id}"
            ):
                sequence.insert(
                    sequence.index(effect) + 1,
                    _require(
                        "hand_crush != true or (destination[0] == _info_position[0] and "
                        "destination[1] == _info_position[1] and destination[2] == "
                        "_info_position[2])",
                        "The separate crush Bonus Action does not move the hand.",
                    ),
                )
            if effect.get("call") == "update_srd_spell_force_scene":
                effect["call"] = "move_srd_arcane_hand"
                effect["params"] = {
                    "caster_state": "{_caster_state_id}",
                    "force_id": "{force_id}",
                    "destination": "{destination}",
                }
                effect["condition"] = "hand_crush != true"
    collected = next(
        i
        for i, effect in enumerate(command["effects"])
        if effect.get("call") == "collect_srd_arcane_hand"
    )
    command["effects"].insert(
        collected + 1,
        _require(
            "hand_crush == true or (combat.encounter_active == true and "
            "caster_state.turns_started != null and "
            "caster_state.turns_started > _force_record.created_turn) or "
            "(combat.encounter_active != true and lasting_state.lasting_tick > "
            "_force_record.started_tick)",
            "Command the hand only on a later turn.",
        ),
    )
    command["description"] = (
        "Use a real later-turn Bonus Action for this saved hand's movement and supported option."
    )
    if delegate is None:
        return {"command_srd_arcane_hand": command}
    wrapper = deepcopy(parent)
    wrapper["parameters"] += [deepcopy(CAST_CHOICES[3]), *deepcopy(CAST_CHOICES[-2:])]
    call = next(e for e in wrapper["effects"] if e.get("call") == delegate["call"])
    call["call"] = "command_srd_arcane_hand_node"
    call["params"]["hand_option"] = "{hand_option}"
    call["params"].update(hand_push_feet="{hand_push_feet}", hand_crush="{hand_crush}")
    return {
        "command_srd_arcane_hand": wrapper,
        "command_srd_arcane_hand_node": command,
    }


def extend_runtime(text):
    tools = _load_runtime(text)["tools"]
    changed = {
        "create_srd_arcane_hand_scene": _creator(),
        "collect_srd_arcane_hand": _collect(),
        "move_srd_arcane_hand": _movement(),
        "attack_srd_arcane_hand": _attack(tools),
        "cast_srd_arcane_hand": _cast(tools),
        "qualify_srd_arcane_hand_reach": _reach(),
        "apply_srd_arcane_hand_option": _options(tools),
    }
    changed.update(_grapple_tools())
    changed.update(_interposing_tools())
    changed.update(_command(tools))
    changed = {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    text = _replace_tools(text, changed) if changed else text
    return _bind_option_consumers(text)


def _bind_option_consumers(text):
    """Join the options to existing escape, retirement, cover and terrain owners once."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    remove = deepcopy(tools["remove_srd_spell_force"])
    if "release_srd_arcane_hand_grapple" not in str(remove["effects"]):
        remove["effects"][:0] = [
            _set("_hand_ending", "state.spell_force_records[force_id]"),
            _when(
                "_hand_ending.spell == 'arcane_hand'",
                [
                    _when(
                        "_hand_ending.grapple_target != null",
                        [
                            _call(
                                "release_srd_arcane_hand_grapple",
                                {
                                    "force_id": "{force_id}",
                                    "force_target": "{_hand_ending.grapple_target}",
                                },
                            )
                        ],
                    ),
                    _call(
                        "clear_srd_arcane_hand_mode",
                        {"caster_state": "{_hand_ending.caster}", "force_id": "{force_id}"},
                    ),
                ],
            ),
        ]
        changed["remove_srd_spell_force"] = remove
    changed["clear_srd_arcane_hand_mode"] = _tool_scene(
        "Clear only the retired hand's current caster mode.",
        [_entity("caster_state"), _parameter("force_id")],
        [
            _when(
                "caster_state.arcane_hand_id == force_id",
                [
                    _set("caster_state.arcane_hand_interposing", False),
                    _set("caster_state.arcane_hand_id", None),
                ],
            )
        ],
    )
    movement = deepcopy(tools["move_srd_arcane_hand"])
    movement["effects"] += [
        _when(
            "_force_record.grapple_target != null",
            [
                _call(
                    "qualify_srd_arcane_hand_reach",
                    {
                        "caster_state": "{_caster_state_id}",
                        "force_id": "{force_id}",
                        "force_target": "{_force_record.grapple_target}",
                        "require_reach": False,
                    },
                    ("_scene_distance",),
                ),
                _when(
                    "_scene_distance > 5",
                    [
                        _call(
                            "release_srd_arcane_hand_grapple",
                            {
                                "force_id": "{force_id}",
                                "force_target": "{_force_record.grapple_target}",
                            },
                        )
                    ],
                ),
            ],
        )
    ]
    changed["move_srd_arcane_hand"] = movement
    escape = deepcopy(tools["escape_srd_grapple_action"])
    if "release_srd_arcane_hand_grapple" not in str(escape["effects"]):
        escape["parameters"].append(_optional("hand_force_id", default=""))
        escape["effects"][:0] = [
            _set("_hand_escape_id", "hand_force_id"),
            _set("_hand_escape_current", False),
            _when(
                "actor_state.arcane_hand_grapple_ids != null and "
                "len(actor_state.arcane_hand_grapple_ids) > 0",
                [
                    _when(
                        "hand_force_id == ''", [_set("_hand_escape_id", "actor_state.grappled_by")]
                    ),
                    _require(
                        "_hand_escape_id in actor_state.arcane_hand_grapple_ids",
                        "Select an actual retained hand grip to escape.",
                    ),
                    _set("_hand_escape_record", "state.spell_force_records[_hand_escape_id]"),
                    _set("actor_state.grapple_escape_dc", "_hand_escape_record.save_dc"),
                    _set("_hand_escape_current", True),
                ],
            ),
        ]
        for e in escape["effects"]:
            if (
                ("remove_condition" in e and e["remove_condition"].get("condition") == "grappled")
                or e.get("set", {}).get("field")
                in ("actor_state.grappled_by", "actor_state.grapple_escape_dc")
            ) and e.get("condition") == "_escape_success == true":
                e["condition"] += " and _hand_escape_current != true"
        escape["effects"].append(
            _when(
                "_escape_success == true and _hand_escape_current == true",
                [
                    _call(
                        "release_srd_arcane_hand_grapple",
                        {"force_id": "{_hand_escape_id}", "force_target": "{_actor_state_id}"},
                    )
                ],
            )
        )
        changed["escape_srd_grapple_action"] = escape
    armor = deepcopy(tools["resolve_srd_effective_armor_class"])
    if "qualify_srd_arcane_hand_cover" not in str(armor["effects"]):
        armor["parameters"] += [
            _optional("hand_cover_source", default=""),
            _optional("hand_existing_cover_bonus", "integer", 0),
        ]
        armor["effects"].append(
            _when(
                "target_state.arcane_hand_interposing == true and len(hand_cover_source) > 0",
                [
                    _call(
                        "qualify_srd_arcane_hand_cover",
                        {
                            "target_state": "{_target_state_id}",
                            "origin_subject": "{hand_cover_source}",
                        },
                        ("_hand_cover_bonus",),
                    ),
                    _set(
                        "_effective_armor_class",
                        "_effective_armor_class + max(0, _hand_cover_bonus - "
                        "hand_existing_cover_bonus)",
                    ),
                ],
            )
        )
        changed["resolve_srd_effective_armor_class"] = armor
    for name, original in tools.items():
        if name.startswith("qualify_srd_arcane_hand"):
            continue
        t = deepcopy(changed.get(name, original))
        params = {p["name"] for p in t["parameters"]}
        source = (
            "_caster_state_id"
            if "caster_state" in params
            else "_actor_state_id"
            if "actor_state" in params
            else "_actor_id"
            if "actor" in params
            else None
        )
        modified = False
        for sequence in _lists(t["effects"]):
            for i, e in list(enumerate(sequence))[::-1]:
                if (
                    e.get("call") != "resolve_srd_effective_armor_class"
                    or not source
                    or "hand_cover_source" in e["params"]
                ):
                    continue
                additions = [_set("_hand_attack_origin", source), _set("_hand_existing_cover", 0)]
                if "force_source_id" in params:
                    additions.append(
                        _when(
                            "force_source_id != ''",
                            [_set("_hand_attack_origin", "force_source_id")],
                        )
                    )
                if "cover" in params:
                    additions += [
                        _when("cover == 'half'", [_set("_hand_existing_cover", 2)]),
                        _when("cover == 'three_quarters'", [_set("_hand_existing_cover", 5)]),
                    ]
                sequence[i:i] = additions
                e["params"].update(
                    hand_cover_source="{_hand_attack_origin}",
                    hand_existing_cover_bonus="{_hand_existing_cover}",
                )
                modified = True
        if name in (
            "resolve_srd_timed_condition_initial_save",
            "cast_spell_single_target_save_damage",
        ) and "qualify_srd_arcane_hand_cover" not in str(t["effects"]):
            source = "source_state_id" if "source_state_id" in params else "_caster_state_id"
            for sequence in _lists(t["effects"]):
                for i, e in list(enumerate(sequence))[::-1]:
                    if e.get("call") == "d20_test" and e["params"].get("kind") == "saving_throw":
                        target = e["params"]["actor_state"]
                        sequence[i:i] = [
                            _set("_hand_save_cover", 0),
                            _when(
                                "save_ability == 'dexterity' and len(" + source + ") > 0",
                                [
                                    _call(
                                        "qualify_srd_arcane_hand_cover",
                                        {
                                            "target_state": target,
                                            "origin_subject": "{" + source + "}",
                                        },
                                        ("_hand_cover_bonus",),
                                    ),
                                    _set("_hand_save_cover", "_hand_cover_bonus"),
                                ],
                            ),
                        ]
                        e["params"]["circumstantial_modifier"] = "{_hand_save_cover}"
                        modified = True
        if modified:
            changed[name] = t
    cost = deepcopy(tools["srd_information_movement_cost"])
    if "collect_srd_arcane_hand_terrain" not in str(cost["effects"]):
        index = next(
            i
            for i, e in enumerate(cost["effects"])
            if e.get("set", {}).get("field") == "_route_zones"
        )
        cost["effects"][index + 1 : index + 1] = [
            _when(
                "lasting_state.has_arcane_hands == true and lasting_state.has_spell_forces == true",
                [
                    _call(
                        "collect_srd_arcane_hand_terrain",
                        {"actor_subject": "{actor_subject}"},
                        ("_hand_interposing_zones",),
                    ),
                    _loop(
                        "_hand_interposing_zones",
                        "_hand_terrain_zone",
                        [{"list_push": {"list": "_route_zones", "value": "_hand_terrain_zone"}}],
                    ),
                ],
            )
        ]
        changed["srd_information_movement_cost"] = cost
    return _replace_tools(text, {n: t for n, t in changed.items() if t != tools.get(n)})


def bind_runtime_consumers(text):
    """Join after paid casting and force owners, before reaction capture and factoring."""
    text = extend_runtime(text)
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        cast = deepcopy(tools[name])
        if "cast_srd_arcane_hand" in str(cast["effects"]) or any(
            e.get("call") == "cast_spell" for e in cast["effects"]
        ):
            continue
        previous = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in previous]
        positions = [
            i
            for i, e in enumerate(cast["effects"])
            if e.get("set", {}).get("field") == "_card_slots_after"
        ]
        if len(positions) != 1:
            raise ValueError(
                "Arcane Hand needs the real paid family dispatch before final cast feedback."
            )
        params = {
            "caster_state": "{_caster_state_id}",
            "spell": "arcane_hand",
            "slot_level": "{_slot_level}",
            "spell_cast_id": "{_spell_source_cast_id}",
            "casting_ability": "{_casting_profile.ability}",
            "casting_modifier": "{_casting_profile.modifier}",
            "casting_attack_bonus": "{_casting_profile.attack_bonus}",
            "casting_save_dc": "{_casting_profile.save_dc}",
            "casting_scaling_level": "{_casting_profile.scaling_level}",
            "concentration_modifier": "{_casting_profile.concentration_save_modifier}",
            "force_id": "{hand_id}",
            "force_position": "{hand_position}",
            "force_target": "{_hand_target_id}",
            "force_on_ground": False,
            "hand_height": "{hand_height}",
            "hand_option": "{hand_option}",
            "hand_destination": "{hand_destination}",
            "hand_push_feet": "{hand_push_feet}",
            "hand_crush": "{hand_crush}",
        }
        cast["effects"].insert(
            positions[0],
            _when(
                "_spell_cast_failed != true and _pending_spell_operation_lost != "
                "true and _spell_definition.family == 'arcane_hand'",
                [
                    _when(
                        "_spell_source_cast_id == null or _spell_source_cast_id == ''",
                        [
                            _call(
                                "begin_srd_spell_source_instance",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "spell": "arcane_hand",
                                    "slot_level": "{_slot_level}",
                                },
                                ("_spell_source_cast_id",),
                            )
                        ],
                    ),
                    _call("cast_srd_arcane_hand", params, RESULTS),
                ],
            ),
        )
        changed[name] = cast
    for name in ("cast_spell_attack_damage",):
        leaf = deepcopy(tools[name])
        spell = next(p for p in leaf["parameters"] if p["name"] == "spell")
        if "enum_values" in spell:
            spell["enum_values"] = sorted(set(spell["enum_values"]) | {"arcane_hand"})
        if leaf != tools[name]:
            changed[name] = leaf
    return _replace_tools(text, changed) if changed else text
