"""Source-owned outer layers receive actual permitted object-attack damage, without object HP."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _call, _emit
    from .srd_prismatic_wall import POOL, _read_source, _source_current
    from .srd_sorcerous_burst_objects import DAMAGE_TYPES
    from .srd_spell_barriers import _effect_lists, _scene, _tool
    from .srd_spell_magic_zones import _when
    from .srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set
else:
    from srd_actor_spell_modifiers import _call, _emit
    from srd_prismatic_wall import POOL, _read_source, _source_current
    from srd_sorcerous_burst_objects import DAMAGE_TYPES
    from srd_spell_barriers import _effect_lists, _scene, _tool
    from srd_spell_magic_zones import _when
    from srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set


def _relation():
    """Target the nearest saved surface; partial-cover precision remains unsupported."""
    return _tool(
        "Qualify a current outer Wall layer, source plane and unobstructed nearest surface.",
        [_entity("caster_state"), _parameter("source_id"), _parameter("max_range_feet", "integer")],
        [
            *_read_source(),
            _require(_source_current("_wall_record"), "Select an actual current Wall layer."),
            *_scene("_caster_state_id", "_wall_attack_caster"),
            _require(
                "_wall_attack_caster.metadata.plane == _wall_record.plane",
                "This attack needs the actual source and caster on the same plane.",
            ),
            _set(
                "_wall_attack_point",
                [
                    f"min(max(_wall_attack_caster.position[{a}], _wall_record.volume.min[{a}]), "
                    f"_wall_record.volume.max[{a}])"
                    for a in range(3)
                ],
            ),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_wall_attack_caster.position}",
                    "end": "{_wall_attack_point}",
                },
                {"_object_distance": "_scene_distance"},
            ),
            _require(
                "_object_distance <= max_range_feet", "The actual Wall is beyond spell range."
            ),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_wall_attack_caster.position}",
                    "end": "{_wall_attack_point}",
                    "plane": "{_wall_record.plane}",
                    "rule": "solid",
                },
                ("_information_blocked",),
            ),
            _require("_information_blocked != true", "Total Cover blocks this actual Wall attack."),
            _call(
                "resolve_srd_illumination_point_sight",
                {
                    "observer_state": "{_caster_state_id}",
                    "point": "{_wall_attack_point}",
                    "perception_plane": "{_wall_record.plane}",
                },
                {"_object_can_see": "_illumination_point_can_see"},
            ),
            _set("_object_effective_armor_class", "_wall_record.armor_class"),
            _set("_object_faerie", False),
        ],
    )


def _attack(actual_tool):
    """Preserve actual Sorcerous dice and caster modifiers before the layer-specific receipt."""
    tool = deepcopy(actual_tool)
    tool["description"] = (
        "Resolve one source-qualified Sorcerous Burst against a current Wall's printed AC10; "
        "retain actual typed damage on its matching outer layer without fabricating object HP."
    )
    for effects in _effect_lists(tool["effects"]):
        for e in effects:
            if e.get("call") == "resolve_srd_spell_object_relation":
                e["call"] = "resolve_srd_prismatic_wall_attack_relation"
                e["params"] = {
                    "caster_state": "{_caster_state_id}",
                    "source_id": "{object_id}",
                    "max_range_feet": "{max_range_feet}",
                }
                e["export_context"] = {
                    key: key
                    for key in (
                        "_object_distance",
                        "_object_can_see",
                        "_object_effective_armor_class",
                        "_object_faerie",
                        "_wall_record",
                    )
                }
    # The base snapshots and trailing mutations belong to ordinary objects.
    # Raw dice/critical/explosions and the actual caster modifier preceding this
    # suffix remain the genuine owning producer, not a supplied numeric result.
    tail = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_object_hp_after"
    )
    damage_observers = [
        deepcopy(effect)
        for effect in tool["effects"][tail:]
        if "end_srd_invisibility_damage_source" in str(effect)
        and "end_srd_mislead_damage_source" in str(effect)
        and "spatial_mutation" not in str(effect)
        and "_object_hp_after" not in str(effect)
    ]
    tool["effects"] = tool["effects"][:tail]
    tool["effects"][0:0] = [
        _set("_wall_sorcerous_types", DAMAGE_TYPES),
        _require(
            "damage_type in _wall_sorcerous_types",
            "Sorcerous Burst must use one of its printed damage types.",
        ),
    ]
    for effects in _effect_lists(tool["effects"]):
        effects[:] = [
            e
            for e in effects
            if e.get("set", {}).get("field")
            not in {
                "_object_hp_before",
                "_object_intrinsic_immunities",
            }
            and e.get("condition") != "damage_type not in _object_intrinsic_immunities"
        ]
    tool["effects"] += [
        _set("_object_hp_before", None),
        _set("_object_hp_after", None),
        _set("_object_destroyed", False),
        _set("_wall_attack_layer_ended", False),
        _set("_wall_attack_threshold", 0),
        _set("_wall_attack_matching_type", "''"),
        _when(
            "_wall_record.outer_layer == 1",
            [
                _set("_wall_attack_threshold", 25),
                _set("_wall_attack_matching_type", "'cold'"),
            ],
        ),
        _when(
            "_wall_record.outer_layer == 5",
            [
                _set("_wall_attack_threshold", 25),
                _set("_wall_attack_matching_type", "'fire'"),
            ],
        ),
        _set("_wall_attack_damage_before", 0),
        _when(
            "_wall_record.layer_received_damage != null",
            [
                _when(
                    "damage_type in _wall_record.layer_received_damage",
                    [
                        _set(
                            "_wall_attack_damage_before",
                            "_wall_record.layer_received_damage[damage_type]",
                        ),
                    ],
                ),
            ],
        ),
        _set("_wall_attack_damage_after", "_wall_attack_damage_before"),
        _when(
            "(_object_attack_kind == 'hit' or _object_attack_kind == 'critical_hit') and "
            "damage_type == _wall_attack_matching_type and _wall_attack_threshold > 0",
            [
                _set("_object_damage_applied", "_object_damage_adjusted"),
                _set(
                    "_wall_attack_damage_after",
                    "_wall_attack_damage_before + _object_damage_applied",
                ),
                _when(
                    "_wall_record.layer_received_damage == null",
                    [
                        _set(f"state.{POOL}.{{object_id}}.layer_received_damage", {}),
                    ],
                ),
                _set(
                    f"state.{POOL}.{{object_id}}.layer_received_damage.{{damage_type}}",
                    "_wall_attack_damage_after",
                ),
                _when(
                    "_wall_attack_damage_after >= _wall_attack_threshold",
                    [
                        _call(
                            "retire_srd_prismatic_wall_layer",
                            {
                                "source_id": "{object_id}",
                                "expected_layer": "{_wall_record.outer_layer}",
                                "reason": "actual Sorcerous Burst typed layer damage",
                            },
                        ),
                        _set("_wall_attack_layer_ended", True),
                        _set("_object_destroyed", True),
                    ],
                ),
            ],
        ),
    ]
    tool["effects"] += damage_observers
    return tool


def _casting(actual_tool):
    """Keep the paid source-derived attack inputs and render actual layer evidence instead of HP."""
    tool = deepcopy(actual_tool)
    for effects in _effect_lists(tool["effects"]):
        for effect in effects:
            if effect.get("call") == "resolve_srd_sorcerous_burst_object_attack":
                effect["call"] = "resolve_srd_prismatic_wall_sorcerous_attack"
                effect["export_context"].update(
                    {
                        name: name
                        for name in (
                            "_wall_record",
                            "_wall_attack_threshold",
                            "_wall_attack_damage_before",
                            "_wall_attack_damage_after",
                            "_wall_attack_layer_ended",
                        )
                    }
                )
            if effect.get("call") == "record_srd_result_text" and (
                effect.get("export_context", {}).get("_burst_object_detail")
                == "_recorded_result_text"
            ):
                effect["params"]["text"] = (
                    "Wall {object_id}; actual outer layer {_wall_record.outer_layer}. "
                    "Attack {_object_attack_kind}. D20 {_object_attack_roll.results}; "
                    "total {_object_attack_total} against AC {_object_effective_armor_class}. "
                    "{selected_damage_type} rolled {_object_damage_rolled}; "
                    "matching layer damage {_object_damage_applied}. {_burst_object_raw_text} "
                    "Retained matching damage {_wall_attack_damage_before} -> "
                    "{_wall_attack_damage_after}; threshold {_wall_attack_threshold}; "
                    "layer ended {_wall_attack_layer_ended}."
                )
    return tool


def _bind_casting(actual_tool):
    """Select the chosen source ID; unrelated ordinary objects retain their owner."""
    tool = deepcopy(actual_tool)
    marker = f"lasting_state.{POOL} != null and object_id in lasting_state.{POOL}"
    if len(tool["effects"]) == 1 and tool["effects"][0].get("condition") == marker:
        tool["effects"] = tool["effects"][0]["on_false"]
    original = deepcopy(tool)
    tool["effects"] = [
        _when(
            marker.replace("lasting_state.", "state."),
            _casting(original)["effects"],
            original["effects"],
        )
    ]
    return tool


def bind_runtime(text):
    """Bind after Sorcerous object emission and Source modifiers, before final Reaction clones."""
    tools = _load_runtime(text)["tools"]
    name = "resolve_srd_sorcerous_burst_object_attack"
    return _emit(
        text,
        {
            "resolve_srd_prismatic_wall_attack_relation": _relation(),
            "resolve_srd_prismatic_wall_sorcerous_attack": _attack(tools[name]),
            "cast_srd_sorcerous_burst_object": _bind_casting(
                tools["cast_srd_sorcerous_burst_object"]
            ),
        },
    )
