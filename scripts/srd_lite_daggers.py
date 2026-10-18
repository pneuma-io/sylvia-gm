"""legacy damage triggers on the selected seven-foot 3D Sphere adapter."""

from copy import deepcopy

from .srd_control_behaviors import _each, _entity, _lasting, _param, _push
from .srd_lite_profiles import CLOUD, lists
from .srd_spell_environment import _area_entry_mode, _call, _sphere_damage_mode, _sphere_mode
from .srd_spell_environment import _environment_tool as _tool
from .srd_timed_actor_defenses import _require, _set, _when


def event_tool():
    return _tool(
        "Settle one authenticated creation, movement or turn-start Cloud damage event.",
        [
            _entity("target_state"),
            _param("area_id"),
            _param("event_kind"),
            _param("event_sequence", "integer"),
            _lasting(),
            _param(
                "witness_state_ids", "array", required=False, default=None, items={"type": "string"}
            ),
        ],
        [
            _set("_lite_daggers_summary_rows", []),
            _set("_lite_daggers_detail_rows", []),
            _set("_lite_daggers_applied", 0),
            _set("_lite_daggers_source", "lasting_state.persistent_spell_areas[area_id]"),
            _require(
                "_lite_daggers_source.spell == 'cloud_of_daggers' and "
                "_lite_daggers_source.mode == 'lite_daggers'",
                "Cloud damage needs its genuine saved source.",
            ),
            _set("_lite_daggers_current", False),
            _call(
                "qualify_srd_environment_sphere_source",
                {
                    "caster_state": "{_lite_daggers_source.caster}",
                    "area_id": "{area_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_sphere_source_current",),
            ),
            _set("_lite_daggers_current", "_sphere_source_current == true"),
            _when(
                "event_kind == 'creation' and "
                "_lite_daggers_source.appearance_in_progress == true and "
                "_target_state_id in _lite_daggers_source.appearance_targets and "
                "event_sequence == _lite_daggers_source.appearance_sequence",
                [_set("_lite_daggers_current", True)],
            ),
            _call(
                "format_srd_information_projection",
                {"message": "{event_kind}:{event_sequence}:{_target_state_id}"},
                {"_lite_daggers_event_id": "_info_projection_message"},
            ),
            _set("_lite_daggers_seen", []),
            _when(
                "_lite_daggers_source.lite_event_receipts != null",
                [_set("_lite_daggers_seen", "_lite_daggers_source.lite_event_receipts")],
            ),
            _when(
                "_lite_daggers_current == true and target_state.is_alive == true and "
                "_lite_daggers_event_id not in _lite_daggers_seen",
                [
                    _push("_lite_daggers_seen", "_lite_daggers_event_id"),
                    _set(
                        "lasting_state.persistent_spell_areas.{area_id}.lite_event_receipts",
                        "_lite_daggers_seen",
                    ),
                    _set(
                        "_lite_daggers_dice", "4 + 2 * max(0, _lite_daggers_source.spell_level - 2)"
                    ),
                    _call(
                        "format_srd_information_projection",
                        {"message": "{_lite_daggers_dice}d4"},
                        {"_lite_daggers_expression": "_info_projection_message"},
                    ),
                    {
                        "dice": {"expression": "{_lite_daggers_expression}"},
                        "store_as": "_lite_daggers_roll",
                    },
                    _set("_lite_daggers_hp_before", "target_state.hp"),
                    _set("_lite_daggers_temp_before", "target_state.temp_hp"),
                    _call(
                        "apply_srd_qualifying_damage",
                        {
                            "target": "{_target_state_id}",
                            "amount": "{_lite_daggers_roll.total}",
                            "adjustment": 0,
                            "damage_type": "slashing",
                            "source_state_id": "{_lite_daggers_source.caster}",
                            "witness_state_ids": "{witness_state_ids}",
                            "game_damage_roll": True,
                        },
                        ("_effective_damage",),
                    ),
                    _call(
                        "record_srd_spell_target_result",
                        {
                            "target": "{_target_state_id}",
                            "brief": "Cloud of Daggers: {_effective_damage} slashing",
                            "detail": (
                                "{_lite_daggers_expression}: {_lite_daggers_roll.results} = "
                                "{_lite_daggers_roll.total}; {_effective_damage} slashing applied, "
                                "no save; {event_kind} event. HP {_lite_daggers_hp_before} to "
                                "{target_state.hp}; temporary HP {_lite_daggers_temp_before} "
                                "to {target_state.temp_hp}."
                            ),
                        },
                        ("_spell_target_summary", "_spell_target_detail"),
                    ),
                    _push("_lite_daggers_summary_rows", "_spell_target_summary"),
                    _push("_lite_daggers_detail_rows", "_spell_target_detail"),
                    _set("_lite_daggers_applied", "_effective_damage"),
                ],
            ),
        ],
    )


def _extend_exact_expression(tool, expression, replacement):
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("condition", "value") and isinstance(child, str):
                    value[key] = child.replace(expression, replacement)
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(tool["effects"])


def _collect_damage_rows(tool, child_name):
    """Carry committed Cloud results across one existing nested-call boundary."""
    fields = ("summary_rows", "detail_rows", "applied")
    tool["effects"][0:0] = [
        _set("_lite_daggers_" + field, 0 if field == "applied" else []) for field in fields
    ]
    count = 0
    for effects in list(lists(tool["effects"])):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get("call") != child_name:
                continue
            if "condition" in effect or effect.get("export_context") == "*":
                raise ValueError("Cloud result propagation needs an unconditional named export")
            exports = effect.setdefault("export_context", {})
            exports.update(
                {"_lite_daggers_child_" + field: "_lite_daggers_" + field for field in fields}
            )
            effects[index + 1 : index + 1] = [
                _set(
                    "_lite_daggers_" + field,
                    "_lite_daggers_" + field + " + _lite_daggers_child_" + field,
                )
                for field in fields
            ]
            count += 1
    if not count:
        raise ValueError("Expected the actual Cloud result consumer: " + child_name)


def _bind_damage_cards(tools):
    """Reuse current cast, movement and turn cards without changing damage or saved state."""
    event = "resolve_srd_lite_daggers_event"
    for parent, child in (
        ("resolve_srd_lite_daggers_scene", event),
        ("cast_spell_environment", "resolve_srd_lite_daggers_scene"),
        ("resolve_srd_environment_hazard_event", event),
        ("resolve_srd_environment_hazard_start_turn", event),
        ("move_srd_scene_creature_celestial_segment", "resolve_srd_environment_hazard_event"),
        ("move_srd_scene_creature_node", "move_srd_scene_creature_celestial_segment"),
        ("move_srd_scene_creature", "move_srd_scene_creature_node"),
        ("begin_combat_turn", "resolve_srd_environment_hazard_start_turn"),
    ):
        _collect_damage_rows(tools[parent], child)
    tools["cast_spell_environment"]["effects"].extend(
        [
            _set("_spell_summary_rows", "_spell_summary_rows + _lite_daggers_summary_rows"),
            _set("_spell_detail_rows", "_spell_detail_rows + _lite_daggers_detail_rows"),
        ]
    )
    # The retained tail builds its compact summary before environment resolution.
    # Add only the first three committed Cloud target rows after that actual call;
    # Details still contains every target's recorded roll and resulting vitals.
    tail = tools["finish_srd_retained_cast_tail"]
    cast_calls = 0
    for effects in list(lists(tail["effects"])):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get("call") != "cast_spell_environment":
                continue
            effect["export_context"]["_lite_daggers_summary_rows"] = "_lite_daggers_summary_rows"
            effects[index + 1 : index + 1] = [
                _when(
                    "(" + effect["condition"] + ") and "
                    "_retained_cast_tail_choices.spell == 'cloud_of_daggers'",
                    [
                        _set("_lite_daggers_shown", 0),
                        _each(
                            "_lite_daggers_summary_rows",
                            "_lite_daggers_summary_row",
                            [
                                _when(
                                    "_lite_daggers_shown < 3",
                                    [
                                        _push("_spell_card_summary", "_lite_daggers_summary_row"),
                                        _set("_lite_daggers_shown", "_lite_daggers_shown + 1"),
                                    ],
                                )
                            ],
                        ),
                    ],
                )
            ]
            cast_calls += 1
    if cast_calls != 1:
        raise ValueError("Expected one retained environment cast completion owner")
    movement = tools["move_srd_scene_creature"]
    turn = tools["begin_combat_turn"]
    for tool in (movement, turn):
        tool["effects"].append(_set("_lite_daggers_event_count", "len(_lite_daggers_summary_rows)"))
        tool["metadata"]["result_card"]["summary"].extend(
            [
                {
                    "label": "Cloud of Daggers events",
                    "value": {"path": "context._lite_daggers_event_count"},
                    "omit_zero": True,
                },
                {
                    "label": "Cloud of Daggers slashing damage",
                    "value": {"path": "context._lite_daggers_applied"},
                    "omit_zero": True,
                },
            ]
        )
    if "details" in movement["metadata"]["result_card"]:
        raise ValueError("Preserve the existing movement details before adding Cloud rows")
    movement["metadata"]["result_card"].update(
        details_label="Cloud of Daggers calculations",
        details={"path": "context._lite_daggers_detail_rows"},
    )
    turn["effects"].extend(
        [
            _when("_turn_effect_rows == null", [_set("_turn_effect_rows", [])]),
            _set("_turn_effect_rows", "_turn_effect_rows + _lite_daggers_detail_rows"),
        ]
    )


def bind_daggers(runtime):
    tools = runtime["tools"]
    definitions = runtime["definitions"]
    # Keep neutral fields used by the accepted environment source installer.
    profile = deepcopy(definitions["spells"]["fog_cloud"])
    profile.update(CLOUD)
    definitions["spells"]["cloud_of_daggers"] = profile
    definitions["spell_casting"]["cloud_of_daggers"] = {
        "name": "Cloud of Daggers",
        "level": 2,
        "school": "conjuration",
        "classes": ["bard", "sorcerer", "warlock", "wizard"],
        "casting_time": "Action",
        "ritual": False,
        "range": "60 feet",
        "components": ["verbal", "somatic"],
        "material": "",
        "material_has_cost": False,
        "material_consumed": False,
        "focus_substitution": False,
        "duration": "Concentration, up to 10 turns",
        "material_parts": [],
    }
    tools["resolve_srd_lite_daggers_event"] = event_tool()
    for name, path, expression in (
        ("qualify_srd_environment_sphere_source", "_sphere_source", _sphere_mode("_sphere_source")),
        (
            "resolve_srd_environment_sphere_body_segment",
            "_sphere_geometry_source",
            _area_entry_mode("_sphere_geometry_source"),
        ),
    ):
        _extend_exact_expression(
            tools[name], expression, "(" + expression + " or " + path + ".mode == 'lite_daggers')"
        )
    initial = deepcopy(tools["resolve_srd_environment_sphere_scene"])
    expression = _sphere_damage_mode("_sphere_scene_source")
    _extend_exact_expression(initial, expression, "_sphere_scene_source.mode == 'lite_daggers'")
    for effects in lists(initial["effects"]):
        for effect in effects:
            if effect.get("call") == "resolve_srd_environment_sphere_event":
                effect["call"] = "resolve_srd_lite_daggers_event"
                effect["params"].update(
                    event_kind="creation", event_sequence="{_sphere_scene_source.sequence}"
                )
    tools["resolve_srd_lite_daggers_scene"] = initial
    cast = tools["cast_spell_environment"]
    position = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("call") == "refresh_srd_environment_mode_indices"
    )
    cast["effects"][position:position] = [
        _when(
            "_environment_profile.environment_mode == 'lite_daggers'",
            [
                _require(
                    " and ".join(
                        f"_environment_cast_origin[{i}] >= "
                        f"lasting_state.spatial_scene.map.bounds.min[{i}] and "
                        f"_environment_cast_origin[{i}] <= "
                        f"lasting_state.spatial_scene.map.bounds.max[{i}]"
                        for i in range(3)
                    ),
                    "The sphere origin must lie inside the admitted three-dimensional map.",
                ),
                _set(
                    (
                        "lasting_state.persistent_spell_areas.{_environment_cast_id}.lite_e"
                        "vent_receipts"
                    ),
                    [],
                ),
                _call(
                    "resolve_srd_lite_daggers_scene",
                    {"area_id": "{_environment_cast_id}", "lasting_state": "{_lasting_state_id}"},
                ),
            ],
        )
    ]
    entry = tools["resolve_srd_environment_hazard_entry"]
    sphere_branch = next(
        e
        for effects in lists(entry["effects"])
        for e in effects
        if e.get("condition", "").startswith("((")
        and "_environment_area.mode == 'insects'" in e["condition"]
    )
    cloud_branch = deepcopy(sphere_branch)
    cloud_branch["condition"] = "_environment_area.mode == 'lite_daggers'"
    # Every actual entry can occur again on the same turn. Keep the accepted
    # physical segment query and intersection event; remove its turn throttle.
    throttle = next(
        e
        for e in cloud_branch["on_true"]
        if e.get("condition") == "_actor_state_id not in _hazard_done_ids"
    )
    cloud_branch["on_true"] = throttle["on_true"]
    for effects in lists(entry["effects"]):
        if sphere_branch in effects:
            effects.insert(effects.index(sphere_branch) + 1, cloud_branch)
            break
    leaf = tools["move_srd_scene_creature_environment_segment"]
    mutation_count = 0
    for effects in lists(leaf["effects"]):
        for index in range(len(effects) - 1, -1, -1):
            if "spatial_mutation" in effects[index]:
                mutation_count += 1
                effects[index + 1 : index + 1] = [
                    _when(
                        "lasting_state.lite_movement_serial == null",
                        [_set("lasting_state.lite_movement_serial", 0)],
                    ),
                    _set(
                        "lasting_state.lite_movement_serial",
                        "lasting_state.lite_movement_serial + 1",
                    ),
                ]
    if mutation_count != 1:
        raise ValueError("Expected one genuine physical movement mutation owner")
    # Freeze the event number at the actual consumer call. A duplicate consumer
    # receives the same number; a later physical movement gets a fresh number.
    dispatch = tools["resolve_srd_environment_hazard_event"]
    dispatch["parameters"].append(
        _param("lite_event_sequence", "integer", required=False, default=0, model_hidden=True)
    )
    for tool in tools.values():
        for effects in lists(tool.get("effects", [])):
            for effect in effects:
                if effect.get("call") == "resolve_srd_environment_hazard_event":
                    effect.setdefault("params", {})["lite_event_sequence"] = (
                        "{lasting_state.lite_movement_serial}"
                    )
    # Keep the ordinary dispatch for every unrelated environment.
    original = deepcopy(dispatch["effects"])
    dispatch["effects"] = [
        {
            "condition": "lasting_state.persistent_spell_areas[area_id].mode == 'lite_daggers'",
            "on_true": [
                _require(
                    "lite_event_sequence > 0",
                    "Cloud entry requires an actual committed movement event.",
                ),
                _call(
                    "resolve_srd_lite_daggers_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{area_id}",
                        "event_kind": "movement",
                        "event_sequence": "{lite_event_sequence}",
                        "lasting_state": "{_lasting_state_id}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                ),
            ],
            "on_false": original,
        }
    ]
    starting = tools["resolve_srd_environment_hazard_start_turn"]
    loop = next(e["for_each"] for e in starting["effects"] if "for_each" in e)
    body = loop["do"][-1]["on_true"]
    body.append(
        _when(
            "_environment_area.mode == 'lite_daggers'",
            [
                _call(
                    "qualify_srd_environment_sphere_target",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{_environment_id}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                    ("_sphere_target_inside",),
                ),
                _when(
                    "_sphere_target_inside == true",
                    [
                        _call(
                            "resolve_srd_lite_daggers_event",
                            {
                                "target_state": "{_target_state_id}",
                                "area_id": "{_environment_id}",
                                "event_kind": "start",
                                "event_sequence": "{combat.turn_sequence}",
                                "lasting_state": "{_lasting_state_id}",
                                "witness_state_ids": "{witness_state_ids}",
                            },
                        )
                    ],
                ),
            ],
        )
    )
    _bind_damage_cards(tools)
    return runtime
