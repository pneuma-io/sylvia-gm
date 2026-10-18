"""Source-owned Thorn walls reuse genuine environment bodies, saves and movement costs."""

from copy import deepcopy

if __package__:
    from . import srd_spell_environment as environment
    from .srd_actor_spell_modifiers import _emit
    from .srd_timed_actor_defenses import _load_runtime
else:
    import srd_spell_environment as environment
    from srd_actor_spell_modifiers import _emit
    from srd_timed_actor_defenses import _load_runtime

SPELL = "wall_of_thorns"
SOURCE_SHA256 = "6ed3739a43979bc4bbb6f0dbe479e81b9e8c9d703d93aea89b5067f4b660dcdc"
PROFILE = {
    **environment.PROFILES["wall_of_fire"],
    "base_level": 6,
    "duration_rounds": 100,
    "environment_mode": "thorn",
    "height_feet": 10,
    "damage_dice": 7,
    "damage_type": "piercing",
}


def validate_profiles(definitions):
    """Admit this source's exact row, leaving existing environment rows to their own validator."""
    if SPELL in definitions and definitions[SPELL] != PROFILE:
        raise ValueError("Wall of Thorns must retain its exact pinned source profile.")


def _rename(value):
    if isinstance(value, str):
        return (
            value.replace("wall_of_fire", "wall_of_thorns")
            .replace("has_wall_of_thorns", "has_thorn_wall")
            .replace("Wall of Fire", "Wall of Thorns")
            .replace("'wall_fire'", "'thorn'")
            .replace("_fire_", "_thorn_")
        )
    if isinstance(value, list):
        return [_rename(item) for item in value]
    if isinstance(value, dict):
        return {_rename(key): _rename(item) for key, item in value.items()}
    return value


def _common_later_receipts(value):
    """Entry and ending turn share one later receipt; appearance has its own actual receipt."""
    if isinstance(value, str):
        for prefix in ("entry", "end"):
            for suffix in ("clock", "turn", "targets"):
                value = value.replace(f".{prefix}_{suffix}", f".later_{suffix}")
        return value
    if isinstance(value, list):
        return [_common_later_receipts(item) for item in value]
    if isinstance(value, dict):
        return {key: _common_later_receipts(item) for key, item in value.items()}
    return value


def _tools(existing):
    """Borrow genuine appearance rolls and source-local saves from the owning wall tools."""
    names = (
        "qualify_srd_wall_of_fire_source",
        "qualify_srd_wall_of_fire_target",
        "qualify_srd_wall_of_fire_effect",
        "resolve_srd_wall_of_fire_body_segment",
        "resolve_srd_wall_of_fire_event",
        "resolve_srd_wall_of_fire_scene",
        "resolve_srd_wall_of_fire_opacity",
    )
    tools = {_rename(name): _rename(deepcopy(existing[name])) for name in names}
    source = tools["qualify_srd_wall_of_thorns_source"]
    source["effects"].append(
        environment._set(
            "_sphere_source_current",
            "_sphere_source_current == true and _sphere_source.spell == 'wall_of_thorns' and "
            f"_sphere_source.source_sha256 == '{SOURCE_SHA256}'",
        )
    )
    target = tools["qualify_srd_wall_of_thorns_target"]
    target["effects"] = [
        effect
        for effect in target["effects"]
        if not effect.get("condition", "").startswith("timing == 'end'")
    ]
    after_source = next(
        i
        for i, effect in enumerate(target["effects"])
        if effect.get("set", {}).get("field") == "_sphere_target_source"
    )
    target["effects"].insert(
        after_source + 1,
        environment._set(
            "_thorn_exposure_origin",
            [
                f"(_sphere_target_source.volume.min[{axis}] + "
                f"_sphere_target_source.volume.max[{axis}]) * 0.5"
                for axis in range(3)
            ],
        ),
    )
    for effects in environment._effect_lists(target["effects"]):
        for effect in effects:
            if effect.get("spatial_query", {}).get("operation") == "exposure":
                effect["spatial_query"]["query"]["observer"]["point"] = "{_thorn_exposure_origin}"
    event = tools["resolve_srd_wall_of_thorns_event"]
    event["effects"] = _common_later_receipts(event["effects"])
    event["effects"].insert(0, environment._set("_thorn_damage_type", "'slashing'"))
    event["effects"].insert(
        1,
        environment._when(
            "timing == 'initial'", [environment._set("_thorn_damage_type", "'piercing'")]
        ),
    )
    for effects in list(environment._effect_lists(event["effects"])):
        for effect in list(effects):
            if effect.get("condition") == "timing == 'initial'" and any(
                e.get("call") == "d20_test" for e in effect.get("on_true", [])
            ):
                index = effects.index(effect)
                effects[index : index + 1] = effect["on_true"]
            if effect.get("condition") == "timing == 'initial' and _outcome == 'success'":
                effect["condition"] = "_outcome == 'success'"
            if (
                effect.get("set", {}).get("field") == "_sphere_turn"
                and effect["set"]["value"] == "lasting_state.lasting_tick"
            ):
                effect["set"]["value"] = "lasting_state.lasting_tick // 2"
            if effect.get("call") == "apply_srd_qualifying_damage":
                effect["params"]["damage_type"] = "{_thorn_damage_type}"
            if effect.get("condition") == "timing == 'initial'" and any(
                e.get("set", {}).get("field") == "target_state.last_wall_of_thorns_save.roll"
                for e in effect.get("on_true", [])
            ):
                index = effects.index(effect)
                effects[index : index + 1] = effect["on_true"]
    route = _rename(
        deepcopy(environment._blade_barrier_tools()["resolve_srd_blade_barrier_intervals"])
    )

    # The actual straight path interval remains derived from the same saved source Box.
    def route_names(value):
        if isinstance(value, str):
            return (
                value.replace("blade_barrier", "wall_of_thorns")
                .replace("_blade_", "_thorn_")
                .replace("'blade'", "'thorn'")
            )
        if isinstance(value, list):
            return [route_names(item) for item in value]
        if isinstance(value, dict):
            return {route_names(key): route_names(item) for key, item in value.items()}
        return value

    route = route_names(route)
    for effects in environment._effect_lists(route["effects"]):
        for effect in effects:
            if effect.get("call") == "srd_information_segment_interval":
                effect["call"] = "resolve_srd_wall_of_thorns_body_segment"
                effect["params"] = {
                    "moving_subject": "{actor_subject}",
                    "area_id": "{_environment_id}",
                    "start": "{start}",
                    "end": "{end}",
                }
                effect["export_context"] = {
                    "_segment_hit": "_body_hit",
                    "_segment_entry": "_body_entry",
                    "_segment_exit": "_body_exit",
                }
    tools["resolve_srd_wall_of_thorns_intervals"] = route
    return tools


def _bind_sources(changed):
    """Use the actual supported wall constructor before Concentration and source installation."""
    cast = changed["cast_spell_environment"]
    if "resolve_srd_wall_of_thorns_scene" not in str(cast["effects"]):
        cast["parameters"] += [
            environment._optional("thorn_wall_length", "number", 60, minimum=0.01, maximum=60),
            environment._optional(
                "thorn_wall_shape", "string", "straight", enum_values=["straight", "ring"]
            ),
        ]
        additions = []
        for index, effect in enumerate(cast["effects"]):
            if effect.get("condition") != "_environment_profile.environment_mode == 'wall_fire'":
                continue
            if "_fire_cast_volume" not in str(
                effect
            ) and "resolve_srd_wall_of_fire_scene" not in str(effect):
                continue
            copied = _rename(deepcopy(effect))
            for effects in environment._effect_lists(copied["on_true"]):
                for entry in effects:
                    field = entry.get("set", {}).get("field", "")
                    value = entry.get("set", {}).get("value")
                    if isinstance(value, str):
                        entry["set"]["value"] = value.replace(
                            "fire_wall_length", "thorn_wall_length"
                        ).replace("fire_wall_side", "'none'")
                    if (
                        field == "_thorn_cast_volume"
                        and isinstance(value, dict)
                        and value.get("kind") == "'box'"
                    ):
                        volume = entry["set"]["value"]
                        volume["max"][2] = "_environment_cast_origin[2] + 10"
                        for axis in (0, 1):
                            for side, sign in (("min", " - "), ("max", " + ")):
                                volume[side][axis] = (
                                    volume[side][axis]
                                    .replace("fire_wall_length", "thorn_wall_length")
                                    .replace(sign + "0.5", sign + "2.5")
                                )
                    if isinstance(entry.get("require", {}).get("condition"), str):
                        entry["require"]["condition"] = entry["require"]["condition"].replace(
                            "fire_wall_length", "thorn_wall_length"
                        )
            if "_thorn_support_volume" in str(copied):
                copied["on_true"].insert(
                    0,
                    environment._require(
                        "thorn_wall_shape == 'straight'",
                        "Wall of Thorns needs its supported straight Box; "
                        "the printed ring is not implemented.",
                    ),
                )
            if "resolve_srd_wall_of_thorns_scene" in str(copied):
                copied["on_true"].insert(
                    0,
                    environment._set(
                        "state.persistent_spell_areas.{_environment_cast_id}.source_sha256",
                        repr(SOURCE_SHA256),
                    ),
                )
                copied["on_true"].insert(
                    1, environment._call("refresh_srd_environment_mode_indices", {})
                )
            copied = _common_later_receipts(copied)
            additions.append((index + 1, copied))
        if len(additions) != 3:
            raise ValueError(
                "Thorns needs the genuine three wall geometry/install/appearance boundaries."
            )
        for index, effect in reversed(additions):
            cast["effects"].insert(index, effect)
    index_tool = changed["refresh_srd_environment_mode_indices"]
    if "has_thorn_wall" not in str(index_tool["effects"]):
        index_tool["effects"].insert(0, environment._set("state.has_thorn_wall", False))
        loop = next(effect["for_each"] for effect in index_tool["effects"] if "for_each" in effect)
        loop["do"][-1]["on_true"].append(
            environment._when(
                "_environment_area.mode == 'thorn'",
                [environment._set("state.has_thorn_wall", True)],
            )
        )


def _bind_consumers(changed):
    """Join actual committed entry, ending turn, opacity and literal route cost owners."""
    entry = changed["resolve_srd_environment_hazard_entry"]
    if "resolve_srd_wall_of_thorns_body_segment" not in str(entry["effects"]):
        loop = next(e["for_each"] for e in entry["effects"] if "for_each" in e)
        body = loop["do"][-1]["on_true"]
        fire = next(
            e for e in body if e.get("condition") == "_environment_area.mode == 'wall_fire'"
        )
        candidate = _common_later_receipts(_rename(deepcopy(fire["on_true"])))
        for effects in environment._effect_lists(candidate):
            for effect in effects:
                condition = effect.get("condition", "")
                if ".later_turn == _hazard_turn" in condition:
                    effect["condition"] = condition.replace("_hazard_turn", "_thorn_hazard_turn")
                if effect.get("call") == "resolve_srd_wall_of_thorns_body_segment":
                    index = effects.index(effect) + 1
                    effects[index:index] = [
                        environment._when(
                            "_body_hit == true and _body_exit > _body_entry",
                            [
                                environment._call(
                                    "measure_srd_information_distance",
                                    {"start": "{_hazard_start}", "end": "{destination}"},
                                    ("_scene_distance",),
                                ),
                                environment._when(
                                    "_scene_distance > 0",
                                    [
                                        environment._set(
                                            "_body_entry",
                                            "_body_entry + min("
                                            "(_body_exit - _body_entry) * 0.5, "
                                            "0.000001 * _scene_distance ** -1)",
                                        )
                                    ],
                                ),
                            ],
                        )
                    ]
        body.append(
            environment._when(
                "_environment_area.mode == 'thorn'",
                [
                    environment._set("_thorn_hazard_turn", "_hazard_turn"),
                    environment._when(
                        "_hazard_clock == 'exploration'",
                        [environment._set("_thorn_hazard_turn", "_hazard_turn // 2")],
                    ),
                    *candidate,
                ],
            )
        )
    dispatch = changed["resolve_srd_environment_hazard_event"]
    if "resolve_srd_wall_of_thorns_event" not in str(dispatch["effects"]):
        dispatch["effects"][-1:] = [
            environment._when(
                "_hazard_dispatch_source.mode == 'thorn'",
                [
                    environment._call(
                        "resolve_srd_wall_of_thorns_event",
                        {
                            "target_state": "{_target_state_id}",
                            "area_id": "{area_id}",
                            "witness_state_ids": "{witness_state_ids}",
                            "timing": "entry",
                        },
                    )
                ],
                dispatch["effects"][-1:],
            )
        ]
    ending = changed["resolve_srd_environment_hazard_end_turn"]
    if "resolve_srd_wall_of_thorns_event" not in str(ending["effects"]):
        loop = next(e["for_each"] for e in ending["effects"] if "for_each" in e)
        loop["do"][-1]["on_true"].append(
            environment._when(
                "_environment_area.mode == 'thorn' and combat.turn_active == true and "
                "combat.active_actor == _target_state_id",
                [
                    environment._call(
                        "resolve_srd_wall_of_thorns_event",
                        {
                            "target_state": "{_target_state_id}",
                            "area_id": "{_environment_id}",
                            "witness_state_ids": "{witness_state_ids}",
                            "timing": "end",
                        },
                    )
                ],
            )
        )
    for name in ("resolve_srd_environment_point", "resolve_srd_environment_segment"):
        tool = changed[name]
        if "resolve_srd_wall_of_thorns_opacity" in str(tool["effects"]):
            continue
        params = {"start": "{start}", "end": "{end}", "plane": "{plane}"}
        if name.endswith("point"):
            params.update(start="{point}", end="{point}")
        tool["effects"].append(
            environment._when(
                "state.has_thorn_wall == true",
                [
                    environment._call(
                        "resolve_srd_wall_of_thorns_opacity", params, ("_environment_thorn_opaque",)
                    ),
                    environment._when(
                        "_environment_thorn_opaque == true",
                        [
                            environment._set(
                                "_environment_fog"
                                if name.endswith("point")
                                else "_environment_sight_obscured",
                                True,
                            )
                        ],
                    ),
                ],
            )
        )
    dispel = changed["dispel_srd_environment_source"]
    for effects in environment._effect_lists(dispel["effects"]):
        for effect in effects:
            requirement = effect.get("require", {})
            condition = requirement.get("condition", "")
            if "_environment_dispel_zone_found == true" in condition and "'thorn'" not in condition:
                requirement["condition"] += " or _environment_dispel_source.mode == 'thorn'"
    cost = changed["srd_information_movement_cost"]
    if "resolve_srd_wall_of_thorns_intervals" in str(cost["effects"]):
        return
    sort_index = next(i for i, e in enumerate(cost["effects"]) if "sort" in e)
    active = "state.has_thorn_wall == true and len(actor_subject) > 0"
    cost["effects"][sort_index:sort_index] = [
        environment._when(
            active,
            [
                environment._call(
                    "resolve_srd_wall_of_thorns_intervals",
                    {"actor_subject": "{actor_subject}", "start": "{start}", "end": "{end}"},
                    ("_thorn_route_intervals",),
                ),
                environment._each(
                    "_thorn_route_intervals",
                    "_thorn_route_interval",
                    [
                        environment._push("_route_cuts", {"t": "_thorn_route_interval.entry"}),
                        environment._push("_route_cuts", {"t": "_thorn_route_interval.exit"}),
                    ],
                ),
            ],
        )
    ]
    for effects in list(environment._effect_lists(cost["effects"])):
        index = next(
            (
                i
                for i, e in enumerate(effects)
                if e.get("set", {}).get("field") == "_route_cost"
                and e["set"]["value"] == "_route_cost + _route_piece_cost"
            ),
            None,
        )
        if index is not None:
            effects[index:index] = [
                environment._when(
                    active,
                    [
                        environment._set("_thorn_piece_inside", False),
                        environment._each(
                            "_thorn_route_intervals",
                            "_thorn_route_interval",
                            [
                                environment._when(
                                    "_route_mid > _thorn_route_interval.entry and "
                                    "_route_mid < _thorn_route_interval.exit",
                                    [environment._set("_thorn_piece_inside", True)],
                                ),
                            ],
                        ),
                        environment._when(
                            "_thorn_piece_inside == true",
                            [
                                environment._set(
                                    "_route_piece_cost",
                                    "_route_piece_cost + "
                                    "3 * (_route_cut.t - _route_previous) * _route_distance",
                                )
                            ],
                        ),
                    ],
                )
            ]


def extend_runtime(text):
    """Emit after the genuine Environment owner, before Source/Reaction captures its installer."""
    existing = _load_runtime(text)["tools"]
    changed = _tools(existing)
    names = (
        "cast_spell_environment",
        "refresh_srd_environment_mode_indices",
        "cast_spell",
        "resolve_srd_environment_hazard_entry",
        "resolve_srd_environment_hazard_event",
        "resolve_srd_environment_hazard_end_turn",
        "resolve_srd_environment_point",
        "resolve_srd_environment_segment",
        "dispel_srd_environment_source",
        "srd_information_movement_cost",
    )
    changed.update({name: deepcopy(existing[name]) for name in names})
    _bind_sources(changed)
    _bind_consumers(changed)
    public = changed["cast_spell"]
    names = {parameter["name"] for parameter in public["parameters"]}
    for suffix, kind, default, facts in (
        ("length", "number", 60, {"minimum": 0.01, "maximum": 60}),
        ("shape", "string", "straight", {"enum_values": ["straight", "ring"]}),
    ):
        name = "environment_thorn_wall_" + suffix
        if name not in names:
            public["parameters"].append(environment._optional(name, kind, default, **facts))
    for effects in environment._effect_lists(public["effects"]):
        for effect in effects:
            if effect.get("call") == "cast_spell_environment":
                for suffix in ("length", "shape"):
                    effect["params"]["thorn_wall_" + suffix] = (
                        "{environment_thorn_wall_" + suffix + "}"
                    )
    return _emit(text, changed)
