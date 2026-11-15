"""Join ready source owners to exact cast dismissal and selected effect dispelling."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
else:
    from srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )


SELECTED_KINDS = (
    "delayed_blast_fireball",
    "appearance",
    "barrier",
    "supply",
    "object_utility",
    "heat_metal",
    "dream",
    "secure_area",
    "levitate",
    "passwall",
    "mirage",
    "hallow",
    "telekinesis",
    "guards_wards",
    "dwelling",
)


def _read_lifetime_source(kind):
    """Read one actual source from its established pool without inventing cast provenance."""
    pool, active = {
        "dream": ("dream_spell_sources", "active"),
        "secure_area": ("spell_secure_area_sources", "installed"),
        "levitate": ("levitate_sources", "active"),
        "passwall": ("passwall_sources", "active"),
        "mirage": ("mirage_sources", "installed"),
        "hallow": ("spell_hallow_sources", "installed"),
        "telekinesis": ("telekinesis_sources", "active"),
        "guards_wards": ("spell_guards_wards_sources", "installed"),
    }[kind]
    read = [_set("_lifetime_record", None)]
    if kind in {"dream", "secure_area", "mirage", "hallow", "guards_wards"}:
        read += [
            _require(
                f"lasting_state.{pool} != null and source_id in lasting_state.{pool}",
                "Select an actual retained " + kind + " source.",
            ),
            _set("_lifetime_record", f"lasting_state.{pool}[source_id]"),
        ]
    else:
        read += [
            _set("_lifetime_records", []),
            _when(
                f"lasting_state.{pool} != null",
                [
                    _set("_lifetime_records", f"lasting_state.{pool}"),
                ],
            ),
            _each(
                "_lifetime_records",
                "_lifetime_candidate",
                [
                    _when(
                        "_lifetime_candidate.id == source_id",
                        [
                            _require("_lifetime_record == null", "Source identity is ambiguous."),
                            _set("_lifetime_record", "_lifetime_candidate"),
                        ],
                    ),
                ],
            ),
            _require("_lifetime_record != null", "Select an actual retained " + kind + " source."),
        ]
        read += [
            _when(
                "_lifetime_record.spell_level == null",
                [
                    _set("_lifetime_level_patch", {"spell_level": "_lifetime_record.slot_level"}),
                    _set("_lifetime_record", "merge(_lifetime_record, _lifetime_level_patch)"),
                ],
            ),
        ]
    read += [
        _set(
            "_lifetime_active",
            f"_lifetime_record.{active} == true and "
            "(_lifetime_record.permanent == true or "
            "lasting_state.lasting_tick < _lifetime_record.expires_tick)",
        ),
    ]
    return _tool(
        "Read the exact retained " + kind + " source and its actual lifetime.",
        [_lasting(), _param("source_id")],
        read,
    )


def _retire_lifetime_source(kind, identity):
    if kind == "guards_wards":
        return [
            _call(
                "end_srd_guards_wards_effect",
                {
                    "source_id": identity,
                    "group": "all",
                    "reason": "dismissed",
                },
            )
        ]
    if kind == "dream":
        return [_call("retire_srd_dream_source", {"dream_id": identity, "reason": "removed"})]
    if kind == "secure_area":
        return [
            _call("end_srd_secure_area_source", {"source_id": identity, "reason": "removed"}),
            _call("refresh_srd_secure_area_sources", {}),
        ]
    if kind == "levitate":
        return [_call("end_srd_owned_levitate_source", {"source_id": identity})]
    if kind == "telekinesis":
        return [_call("end_srd_owned_telekinesis_source", {"source_id": identity})]
    if kind == "hallow":
        return [_call("end_srd_hallow_source", {"source_id": identity, "reason": "dispel"})]
    if kind == "mirage":
        return [
            _call("retire_srd_mirage_source", {"mirage_id": identity, "reason": "removed"}),
            _call("refresh_srd_mirage_sources", {}),
        ]
    return [_call("end_srd_passwall_source", {"source_id": identity})]


def _lifetime_tools(tools, changed):
    """Use each source's real subject and exact retirement, including queued Levitate return."""
    owners = {
        "dream": "retire_srd_dream_source",
        "secure_area": "end_srd_secure_area_source",
        "levitate": "mark_srd_levitate_return",
        "passwall": "end_srd_passwall_source",
        "mirage": "retire_srd_mirage_source",
        "hallow": "end_srd_hallow_source",
        "telekinesis": "end_srd_telekinesis_source",
    }
    for kind, owner in owners.items():
        if owner not in tools:
            continue
        changed["read_srd_lifetime_" + kind + "_source"] = _read_lifetime_source(kind)
        effects = [
            _call(
                "read_srd_lifetime_" + kind + "_source",
                {"source_id": "{source_id}"},
                ("_lifetime_record", "_lifetime_active"),
            ),
            _require(
                "_lifetime_active == true and _lifetime_record.dispel_ongoing != false",
                "Select an actual active ongoing " + kind + " source.",
            ),
        ]
        if kind == "dream":
            effects += [
                _require(
                    "target_state != null and (_lifetime_record.target == _target_state_id "
                    "or _lifetime_record.messenger == _target_state_id)",
                    "Select this Dream's actual target or messenger.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_target_state_id}"},
                    ("_info_scene_entity", "_info_position"),
                ),
                _set("_lifetime_dispel_point", "_info_position"),
                _set("_lifetime_dispel_plane", "_info_scene_entity.metadata.plane"),
            ]
        elif kind in {"levitate", "telekinesis"}:
            effects += [
                _set("_lifetime_raw_subject", "''"),
                _when(
                    "subject != ''",
                    [
                        _call(
                            "format_srd_information_projection",
                            {"message": "{subject}"},
                            ("_info_projection_message",),
                        ),
                        _set("_lifetime_raw_subject", "_info_projection_message"),
                    ],
                ),
            ]
            if kind == "telekinesis":
                effects += [
                    _when(
                        "_lifetime_raw_subject == ''",
                        [
                            _require(
                                "target_state != null",
                                "Select Telekinesis's actual caster or affected subject.",
                            ),
                            _set("_lifetime_raw_subject", "_target_state_id"),
                        ],
                    ),
                ]
            else:
                effects += [
                    _require(
                        "_lifetime_raw_subject == _lifetime_record.subject",
                        "Select the actual raised body.",
                    )
                ]
            effects += [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_lifetime_raw_subject}"},
                    ("_info_scene_entity", "_info_position"),
                ),
            ]
            if kind == "telekinesis":
                effects += [
                    _require(
                        "_lifetime_raw_subject == _lifetime_record.caster or "
                        "_info_scene_entity.id == _lifetime_record.subject",
                        "Select this Telekinesis's caster ability or actual held body.",
                    ),
                    _when(
                        "_lifetime_raw_subject != _lifetime_record.caster",
                        [
                            _require(
                                "_info_scene_entity.metadata.telekinesis_source_id == source_id "
                                "and _info_scene_entity.metadata.plane == _lifetime_record.plane",
                                "Reconcile the actual held body's source before Dispel.",
                            )
                        ],
                    ),
                    _set("_lifetime_dispel_plane", "_info_scene_entity.metadata.plane"),
                ]
            else:
                effects += [
                    _require(
                        "_info_scene_entity.metadata.levitate_source_id == source_id and "
                        "_info_scene_entity.metadata.plane == _lifetime_record.plane",
                        "Reconcile the actual raised body's source before Dispel.",
                    ),
                    _set("_lifetime_dispel_plane", "_lifetime_record.plane"),
                ]
            effects += [_set("_lifetime_dispel_point", "_info_position")]
        else:
            if kind in {"secure_area", "hallow"}:
                prefix = "hallow" if kind == "hallow" else "secure_area"
                effects += [
                    _call(
                        "qualify_srd_" + prefix + "_point",
                        {
                            "source_id": "{source_id}",
                            "point": "{point}",
                            "plane": "{_lifetime_record.plane}",
                        },
                        ("_" + prefix + "_point_inside",),
                    ),
                    _require(
                        "_" + prefix + "_point_inside == true",
                        "Choose a point in the actual saved " + kind + " area.",
                    ),
                ]
            elif kind == "mirage":
                effects += [
                    _require(
                        " and ".join(
                            f"point[{axis}] >= _lifetime_record.corner[{axis}] and "
                            f"point[{axis}] <= _lifetime_record.corner[{axis}] + "
                            "_lifetime_record.side"
                            for axis in range(2)
                        )
                        + " and point[2] == _lifetime_record.corner[2]",
                        "Choose a point on the actual saved Mirage Square.",
                    )
                ]
            else:
                effects += [
                    _require(
                        " and ".join(
                            f"point[{axis}] >= _lifetime_record.open_min[{axis}] and "
                            f"point[{axis}] <= _lifetime_record.open_max[{axis}]"
                            for axis in range(3)
                        ),
                        "Choose a point in the actual saved Passwall opening.",
                    )
                ]
            effects += [
                _set("_lifetime_dispel_point", "point"),
                _set("_lifetime_dispel_plane", "_lifetime_record.plane"),
            ]
        effects += [
            *_actual_path("_lifetime_dispel_point", "_lifetime_dispel_plane"),
            *_source_check(
                "_lifetime_record", "spell_id" if kind in {"secure_area", "hallow"} else "spell"
            ),
            _when("_dispel_succeeded == true", _retire_lifetime_source(kind, "{source_id}")),
        ]
        changed["dispel_srd_" + kind + "_owned_source"] = _tool(
            "Dispel the exact " + kind + " source using its actual subject, slot and ending.",
            [
                _entity("caster_state"),
                _entity("target_state") | {"required": False, "default": None},
                _lasting(),
                _param("source_id"),
                _param("subject", required=False, default=""),
                _param(
                    "point",
                    "array",
                    required=False,
                    default=[0, 0, 0],
                    items={"type": "number"},
                    min_items=3,
                    max_items=3,
                ),
                _param("caster", resolve="participant", required=False),
                _param("slot_level", "integer"),
                _param("casting_modifier", "integer"),
                _param("casting_ability"),
            ],
            effects,
        )
    for spell, owner in (
        ("levitate", "mark_srd_levitate_return"),
        ("telekinesis", "end_srd_telekinesis_source"),
    ):
        if owner not in tools:
            continue
        changed["end_srd_owned_" + spell + "_source"] = _tool(
            "Retire one exact " + spell + " concentration and queue its genuine release.",
            [_lasting(), _param("source_id")],
            [
                _call(
                    "read_srd_lifetime_" + spell + "_source",
                    {"source_id": "{source_id}"},
                    ("_lifetime_record",),
                ),
                _call(
                    "retire_srd_owned_" + spell + "_focus",
                    {
                        "source_state": "{_lifetime_record.caster}",
                        "source_id": "{source_id}",
                        "sequence": "{_lifetime_record.sequence}",
                    },
                ),
            ],
        )
        changed["retire_srd_owned_" + spell + "_focus"] = _tool(
            "End only the matching saved " + spell + " focus before deferred physical release.",
            [
                _entity("source_state"),
                _lasting(),
                _param("source_id"),
                _param("sequence", "integer"),
            ],
            [
                _when(
                    "source_state.concentration_spell_id == " + repr(spell) + " and "
                    "source_state.concentration_sequence == sequence and "
                    "source_state." + spell + "_source_id == source_id",
                    [
                        _call(
                            "end_srd_concentration_node",
                            {
                                "source_state": "{_source_state_id}",
                                "target_state": "{_source_state_id}",
                            },
                        ),
                    ],
                ),
                _call(
                    owner,
                    (
                        {"source_id": "{source_id}"}
                        if spell == "telekinesis"
                        else {
                            "source_id": "{source_id}",
                            "caster_state": "{_source_state_id}",
                            "sequence": "{sequence}",
                        }
                    ),
                ),
            ],
        )


def _finite_match(record):
    return (
        f"{record}.cast_id == cast_id and {record}.source_caster_id == _caster_state_id "
        f"and {record}.ongoing_duration == true and {record}.expires_tick != null "
        f"and lasting_state.lasting_tick < {record}.expires_tick"
    )


def _remember(kind, source_id, subject="''"):
    return [
        _push(
            "_dismiss_adoption_sources", {"kind": repr(kind), "id": source_id, "subject": subject}
        ),
        _set("_dismiss_count", "_dismiss_count + 1"),
    ]


def _join_lifetime_dismissal(tools, changed, selection, retirement):
    """Collect exact finite casts through each existing source index, retaining legacy ambiguity."""
    if "collect_srd_dwelling_cast_sources" in tools:
        selection += [
            _call(
                "collect_srd_dwelling_cast_sources",
                {"caster_state": "{_caster_state_id}", "cast_id": "{cast_id}"},
                ("_dwelling_cast_source_ids",),
            ),
            _each(
                "_dwelling_cast_source_ids",
                "_dwelling_dismiss_source_id",
                _remember("dwelling", "_dwelling_dismiss_source_id"),
            ),
        ]
        retirement.append(
            _when(
                "_dismiss_adoption.kind == 'dwelling'",
                [
                    _call(
                        "retire_srd_owned_dwelling_source",
                        {"source_id": "{_dismiss_adoption.id}"},
                    ),
                ],
            )
        )
    if "retire_srd_dream_source" in tools:
        changed["collect_srd_dismiss_dream_target"] = _tool(
            "Collect this actual Dream target's finite owned cast IDs through its retained index.",
            [_entity("target_state"), _entity("caster_state"), _lasting(), _param("cast_id")],
            [
                _set("_lifetime_dismiss_ids", []),
                _when(
                    "target_state.dream_target_source_ids != null",
                    [
                        _set("_lifetime_target_ids", "target_state.dream_target_source_ids"),
                        _each(
                            "_lifetime_target_ids",
                            "_lifetime_target_id",
                            [
                                _call(
                                    "read_srd_lifetime_dream_source",
                                    {
                                        "source_id": "{_lifetime_target_id}",
                                    },
                                    ("_lifetime_record", "_lifetime_active"),
                                ),
                                _when(
                                    "_lifetime_active == true and "
                                    + _finite_match("_lifetime_record"),
                                    [
                                        _push("_lifetime_dismiss_ids", "_lifetime_target_id"),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
        selection += [
            _when(
                "lasting_state.has_dream_spells == true",
                [
                    _set("_lifetime_dream_targets", "lasting_state.dream_spell_target_ids"),
                    _each(
                        "_lifetime_dream_targets",
                        "_lifetime_dream_target",
                        [
                            _call(
                                "collect_srd_dismiss_dream_target",
                                {
                                    "target_state": "{_lifetime_dream_target}",
                                    "caster_state": "{_caster_state_id}",
                                    "cast_id": "{cast_id}",
                                },
                                ("_lifetime_dismiss_ids",),
                            ),
                            _each(
                                "_lifetime_dismiss_ids",
                                "_lifetime_dismiss_id",
                                _remember("dream", "_lifetime_dismiss_id"),
                            ),
                        ],
                    ),
                ],
            )
        ]
        retirement += [
            _when(
                "_dismiss_adoption.kind == 'dream'",
                _retire_lifetime_source("dream", "{_dismiss_adoption.id}"),
            )
        ]
    for kind, pool, index, active, owner in (
        (
            "secure_area",
            "spell_secure_area_sources",
            "spell_secure_area_source_ids",
            "installed",
            "end_srd_secure_area_source",
        ),
        ("levitate", "levitate_sources", None, "active", "mark_srd_levitate_return"),
        ("passwall", "passwall_sources", None, "active", "end_srd_passwall_source"),
        ("mirage", "mirage_sources", "mirage_source_ids", "installed", "retire_srd_mirage_source"),
        ("telekinesis", "telekinesis_sources", None, "active", "end_srd_telekinesis_source"),
        (
            "guards_wards",
            "spell_guards_wards_sources",
            "spell_guards_wards_source_ids",
            "installed",
            "end_srd_guards_wards_effect",
        ),
    ):
        if owner not in tools:
            continue
        rows = "_lifetime_dismiss_" + kind + "_rows"
        item = "_lifetime_dismiss_" + kind + "_item"
        record = "_lifetime_dismiss_" + kind + "_record"
        read = [_set(record, f"lasting_state.{pool}[{item}]")] if index else [_set(record, item)]
        source_id = item if index else record + ".id"
        selection += [
            _when(
                f"lasting_state.{index or pool} != null",
                [
                    _set(rows, f"lasting_state.{index or pool}"),
                    _each(
                        rows,
                        item,
                        read
                        + [
                            _when(
                                f"{record}.{active} == true and {record}.permanent != true and "
                                + _finite_match(record),
                                _remember(kind, source_id),
                            ),
                        ],
                    ),
                ],
            )
        ]
        retirement += [
            _when(
                "_dismiss_adoption.kind == " + repr(kind),
                _retire_lifetime_source(kind, "{_dismiss_adoption.id}"),
            )
        ]


def _join_dismissal(tools, changed):
    """Collect from existing indexes, then invoke each source's authentic retirement owner."""
    dismiss = changed["dismiss_srd_spell"]
    for parameter in dismiss["parameters"]:
        if parameter["name"] == "cast_id":
            parameter.update(required=False, default="")
    if not any(p["condition"] == "cast_id != ''" for p in dismiss["pre_conditions"]):
        dismiss["pre_conditions"].insert(
            0,
            {
                "condition": "cast_id != ''",
                "error": "Select an actual nonblank cast reference; "
                "legacy effects cannot be grouped.",
            },
        )
    selection = [_set("_dismiss_adoption_sources", [])]
    retirement = []
    _join_lifetime_dismissal(tools, changed, selection, retirement)
    if "end_srd_blink_target" in tools:
        changed["collect_srd_dismiss_blink_target"] = _tool(
            "Read the actual recipient's finite Blink cast before source-owned return.",
            [_entity("target_state"), _entity("caster_state"), _lasting(), _param("cast_id")],
            [
                _set("_dismiss_blink_matches", False),
                _when(
                    "target_state.travel_blink != null",
                    [_set("_dismiss_blink_matches", _finite_match("target_state.travel_blink"))],
                ),
            ],
        )
        selection += [
            _when(
                "lasting_state.travel_blink_targets != null",
                [
                    _set("_dismiss_blink_targets", "lasting_state.travel_blink_targets"),
                    _each(
                        "_dismiss_blink_targets",
                        "_dismiss_blink_target",
                        [
                            _call(
                                "collect_srd_dismiss_blink_target",
                                {
                                    "target_state": "{_dismiss_blink_target}",
                                    "caster_state": "{_caster_state_id}",
                                    "cast_id": "{cast_id}",
                                },
                                ("_dismiss_blink_matches",),
                            ),
                            _when(
                                "_dismiss_blink_matches == true",
                                _remember("blink", "_dismiss_blink_target"),
                            ),
                        ],
                    ),
                ],
            )
        ]
        retirement += [
            _when(
                "_dismiss_adoption.kind == 'blink'",
                [_call("end_srd_blink_target", {"target_state": "{_dismiss_adoption.id}"})],
            )
        ]
    for kind, index, collect, export, active, retire in (
        (
            "appearance",
            "appearance_source_ids",
            "collect_srd_appearance_source",
            "_appearance_record",
            "_appearance_active",
            "retire_srd_appearance_source",
        ),
        (
            "barrier",
            "spell_barrier_source_ids",
            None,
            "_dismiss_barrier",
            "_barrier_source_active",
            "end_srd_spell_barrier_source",
        ),
    ):
        if retire not in tools:
            continue
        ids = "_dismiss_" + kind + "_ids"
        item = "_dismiss_" + kind + "_id"
        if collect:
            read = [_call(collect, {"appearance_id": "{" + item + "}"}, (export, active))]
            extra = export + ".installed == true"
        else:
            read = [
                _set(export, "lasting_state.spell_barrier_sources[" + item + "]"),
                _call(
                    "check_srd_spell_barrier_source",
                    {
                        "caster_state": "{" + export + ".caster_state_id}",
                        "source_id": "{" + item + "}",
                    },
                    (active,),
                ),
            ]
            extra = export + ".installed == true"
        selection.append(
            _when(
                "lasting_state." + index + " != null",
                [
                    _set(ids, "lasting_state." + index),
                    _each(
                        ids,
                        item,
                        read
                        + [
                            _when(
                                active + " == true and " + extra + " and " + _finite_match(export),
                                _remember(kind, item),
                            )
                        ],
                    ),
                ],
            )
        )
        retirement.append(
            _when(
                "_dismiss_adoption.kind == " + repr(kind),
                [
                    _call(
                        retire,
                        {
                            (
                                "appearance_id" if kind == "appearance" else "source_id"
                            ): "{_dismiss_adoption.id}",
                            "reason": "dismissed",
                        },
                    )
                ],
            )
        )
    if "retire_srd_supply_source" in tools:
        selection.append(
            _when(
                "lasting_state.spatial_scene != null",
                [
                    _set("_dismiss_supply_objects", "lasting_state.spatial_scene.entities"),
                    _each(
                        "_dismiss_supply_objects",
                        "_dismiss_supply_object",
                        [
                            _when(
                                "_dismiss_supply_object.active == true and "
                                "_dismiss_supply_object.metadata.srd_goodberry_batch != null",
                                [
                                    _set(
                                        "_dismiss_supply",
                                        "_dismiss_supply_object.metadata.srd_goodberry_batch",
                                    ),
                                    _when(
                                        "_dismiss_supply.remaining > 0 and "
                                        + _finite_match("_dismiss_supply"),
                                        _remember(
                                            "supply",
                                            "_dismiss_supply.source_id",
                                            "_dismiss_supply_object.id",
                                        ),
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        )
        retirement.append(
            _when(
                "_dismiss_adoption.kind == 'supply'",
                [
                    _call(
                        "retire_srd_supply_source",
                        {
                            "object_id": "{_dismiss_adoption.subject}",
                            "source_id": "{_dismiss_adoption.id}",
                        },
                    )
                ],
            )
        )
    if "end_srd_heat_metal_source" in tools:
        for node in _walk(dismiss["effects"]):
            if "_dismiss_effect.cast_id == cast_id" in node.get("condition", ""):
                node["condition"] += " and _dismiss_effect.kind != 'heat_metal_spell'"
        selection.append(
            _each(
                "_dismiss_effects",
                "_dismiss_heat",
                [
                    _when(
                        "_dismiss_heat.kind == 'heat_metal_spell' and "
                        + _finite_match("_dismiss_heat"),
                        [
                            _call(
                                "read_srd_heat_metal_source",
                                {
                                    "object_id": "{_dismiss_heat.object_id}",
                                    "source_id": "{_dismiss_heat.source_id}",
                                },
                                ("_heat_metal_source",),
                            ),
                            *_remember("heat_metal", "_dismiss_heat.source_id"),
                        ],
                    ),
                ],
            )
        )
        retirement.append(
            _when(
                "_dismiss_adoption.kind == 'heat_metal'",
                [
                    _call(
                        "end_srd_heat_metal_source",
                        {
                            "caster_state": "{_caster_state_id}",
                            "source_id": "{_dismiss_adoption.id}",
                        },
                    )
                ],
            )
        )
    requirement = next(i for i, e in enumerate(dismiss["effects"]) if "require" in e)
    dismiss["effects"][requirement:requirement] = selection
    # Retirement must precede the final generic concentration shutdown: Heat's
    # source reader verifies its own current concentration before removing it.
    final_concentration = next(
        i
        for i, e in enumerate(dismiss["effects"])
        if e.get("condition") == "_dismiss_concentration_owned == true"
    )
    dismiss["effects"].insert(
        final_concentration, _each("_dismiss_adoption_sources", "_dismiss_adoption", retirement)
    )
    dismiss["effects"][final_concentration + 1]["condition"] = (
        "_dismiss_concentration_owned == true and caster_state.concentration_spell_id != null "
        "and caster_state.concentration_cast_id == cast_id"
    )
    if "dismiss_srd_mirage_arcane" in tools:
        old = deepcopy(tools["dismiss_srd_mirage_arcane"])
        old["effects"] = [
            e
            for e in old["effects"]
            if "require" in e
            and "_mirage_record" not in str(e)
            and "authentic finite Mirage" not in str(e)
        ] + [
            _call(
                "read_srd_lifetime_mirage_source",
                {"source_id": "{mirage_id}"},
                ("_lifetime_record", "_lifetime_active"),
            ),
            _require(
                "_lifetime_active == true and _lifetime_record.cast_id != null and "
                "_lifetime_record.cast_id != '' and "
                "_lifetime_record.source_caster_id == _caster_state_id",
                "Select an authentic finite Mirage cast owned by this caster.",
            ),
            _call(
                "dismiss_srd_spell",
                {"caster_state": "{_caster_state_id}", "cast_id": "{_lifetime_record.cast_id}"},
            ),
            _set("_mirage_dismissed", True),
        ]
        changed["dismiss_srd_mirage_arcane"] = old


def _actual_path(point, plane):
    return [
        _call(
            "srd_information_scene_entity",
            {"subject": "{_caster_state_id}"},
            ("_info_scene_entity", "_info_position"),
        ),
        _set("_adoption_caster_position", "_info_position"),
        _require(
            "_info_scene_entity.metadata.plane == " + plane,
            "The selected spell effect is on another plane.",
        ),
        _call(
            "measure_srd_information_distance",
            {"start": "{_adoption_caster_position}", "end": "{" + point + "}"},
            ("_scene_distance",),
        ),
        _require("_scene_distance <= 120", "The selected spell effect is beyond Dispel range."),
        _call(
            "srd_information_barriers",
            {
                "start": "{_adoption_caster_position}",
                "end": "{" + point + "}",
                "plane": "{" + plane + "}",
                "rule": "solid",
            },
            ("_information_blocked",),
        ),
        _require("_information_blocked != true", "A solid barrier blocks the actual Dispel path."),
    ]


def _source_check(record, spell_field="spell"):
    return [
        _call(
            "resolve_srd_dispel_source_check",
            {
                "caster_state": "{_caster_state_id}",
                "caster": "{_caster_id}",
                "slot_level": "{slot_level}",
                "source_level": "{" + record + ".spell_level}",
                "casting_modifier": "{casting_modifier}",
                "casting_ability": "{casting_ability}",
            },
            ("_dispel_succeeded", "_dispel_roll"),
        ),
        _set(
            "_adoption_dispel_check",
            {
                "spell": record + "." + spell_field,
                "source_id": "source_id",
                "ended": "_dispel_succeeded",
                "roll": None,
                "dc": "10 + " + record + ".spell_level",
            },
        ),
        _when(
            "_dispel_roll != null",
            [
                _set("_adoption_roll_patch", {"roll": "_dispel_roll"}),
                _set(
                    "_adoption_dispel_check", "merge(_adoption_dispel_check, _adoption_roll_patch)"
                ),
            ],
        ),
    ]


def _object_source_tools(tools, changed):
    """Use the currently selected actual object and its existing source query."""
    for kind, read, export, retire, has_caster in (
        ("supply", "read_srd_supply_source", "_supply_source", "retire_srd_supply_source", False),
        (
            "object_utility",
            "read_srd_object_utility_source",
            "_object_utility_source",
            "retire_srd_object_utility_source",
            True,
        ),
        (
            "heat_metal",
            "read_srd_heat_metal_source",
            "_heat_metal_source",
            "end_srd_heat_metal_source",
            True,
        ),
    ):
        if read not in tools:
            continue
        params = {"source_id": "{source_id}"}
        params["caster_state" if has_caster else "object_id"] = (
            "{" + export + ".caster}" if has_caster else "{object_id}"
        )
        changed["dispel_srd_" + kind + "_owned_source"] = _tool(
            "Dispel the actual selected "
            + kind
            + " source using its source level and retirement owner.",
            [
                _entity("caster_state"),
                _lasting(),
                _param("object_id"),
                _param("source_id"),
                _param("caster", resolve="participant", required=False),
                _param("slot_level", "integer"),
                _param("casting_modifier", "integer"),
                _param("casting_ability"),
            ],
            [
                _call(
                    "resolve_srd_object_content_geometry",
                    {
                        "caster_state": "{_caster_state_id}",
                        "object_id": "{object_id}",
                        "range_feet": 120,
                        "requires_sight": False,
                    },
                ),
                _call(read, {"object_id": "{object_id}", "source_id": "{source_id}"}, (export,)),
                *_source_check(export),
                _when("_dispel_succeeded == true", [_call(retire, params)]),
            ],
        )


def _appearance_tools(tools, changed):
    if "retire_srd_appearance_source" not in tools:
        return
    changed["dispel_srd_appearance_owned_source"] = _tool(
        "Dispel a saved appearance at its actual subject or saved terrain Cube.",
        [
            _entity("caster_state"),
            _lasting(),
            _param("source_id"),
            _param("caster", resolve="participant", required=False),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
        ],
        [
            _call(
                "collect_srd_appearance_source",
                {"appearance_id": "{source_id}"},
                ("_appearance_record", "_appearance_active"),
            ),
            _require(
                "_appearance_active == true and _appearance_record.dispel_ongoing == true",
                "Select an actual active appearance source.",
            ),
            _when(
                "_appearance_record.spell == 'hallucinatory_terrain'",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_caster_state_id}"},
                        ("_info_position",),
                    ),
                    _set(
                        "_appearance_dispel_volume",
                        {
                            "kind": "'box'",
                            "min": "_appearance_record.cube_min",
                            "max": "_appearance_record.cube_max",
                        },
                    ),
                    _call(
                        "srd_information_volume_point",
                        {"origin": "{_info_position}", "volume": "{_appearance_dispel_volume}"},
                        ("_volume_point",),
                    ),
                    _set("_appearance_dispel_point", "_volume_point"),
                    _set("_appearance_dispel_plane", "_appearance_record.spec.plane"),
                ],
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_appearance_record.subject}"},
                        ("_info_scene_entity", "_info_position"),
                    ),
                    _set("_appearance_dispel_point", "_info_position"),
                    _set("_appearance_dispel_plane", "_info_scene_entity.metadata.plane"),
                ],
            ),
            *_actual_path("_appearance_dispel_point", "_appearance_dispel_plane"),
            *_source_check("_appearance_record"),
            _when(
                "_dispel_succeeded == true",
                [
                    _call(
                        "retire_srd_appearance_source",
                        {"appearance_id": "{source_id}", "reason": "dispel"},
                    ),
                    _call("refresh_srd_appearance_sources", {}),
                ],
            ),
        ],
    )


def _guards_wards_tools(tools, changed):
    """Keep parent immunity to Dispel separate from actual ward effect retirement."""
    if "end_srd_guards_wards_effect" not in tools:
        return
    changed["read_srd_lifetime_guards_wards_source"] = _read_lifetime_source("guards_wards")
    inside = " and ".join(
        f"point[{axis}] >= _lifetime_record.box.min[{axis}] and "
        f"point[{axis}] <= _lifetime_record.box.max[{axis}]"
        for axis in range(3)
    )
    stairs = [
        _require(
            "effect_group != 'stairs'", "The actual ward stair Web qualifier is not yet supported."
        )
    ]
    if "qualify_srd_guards_wards_web_source" in tools:
        stairs = [
            _when(
                "effect_group == 'stairs'",
                [
                    _require(
                        "_lifetime_record.web_section_ids != null and "
                        "len(_lifetime_record.web_section_ids) > 0",
                        "The actual ward stair Web qualifier needs a retained section.",
                    ),
                    _set("_guards_effect_found", False),
                    _set("_guards_owned_stairs", "_lifetime_record.web_section_ids"),
                    _each(
                        "_guards_owned_stairs",
                        "_guards_owned_stair_id",
                        [
                            _call(
                                "qualify_srd_guards_wards_web_source",
                                {"area_id": "{_guards_owned_stair_id}"},
                                ("_sphere_source_current",),
                            ),
                            _set(
                                "_guards_owned_stair",
                                "lasting_state.spell_guarded_web_sections[_guards_owned_stair_id]",
                            ),
                            _set(
                                "_guards_stair_contains_point",
                                " and ".join(
                                    f"point[{axis}] >= _guards_owned_stair.volume.min[{axis}] and "
                                    f"point[{axis}] <= _guards_owned_stair.volume.max[{axis}]"
                                    for axis in range(3)
                                ),
                            ),
                            _when(
                                "_sphere_source_current == true and "
                                "_guards_owned_stair.parent_source_id == source_id and "
                                "_guards_owned_stair.parent_serial == _lifetime_record.serial and "
                                "_guards_owned_stair.plane == _lifetime_record.plane and "
                                "_guards_owned_stair.spell_level == _lifetime_record.spell_level "
                                "and _guards_stair_contains_point == true",
                                [_set("_guards_effect_found", True)],
                            ),
                        ],
                    ),
                    _require(
                        "_guards_effect_found == true", "Select actual ward stair Web scenery."
                    ),
                ],
            )
        ]
    effects = [
        _call(
            "read_srd_lifetime_guards_wards_source",
            {"source_id": "{source_id}"},
            ("_lifetime_record", "_lifetime_active"),
        ),
        _require("_lifetime_active == true", "Select an actual active Guards and Wards source."),
        _set("_guards_effect_choices", ["''", "'corridors'", "'doors'", "'stairs'", "'other'"]),
        _require(
            "effect_group in _guards_effect_choices", "Select one actual ward effect or its parent."
        ),
        _set("_guards_dispel_point", "point"),
        _when(
            "effect_group != ''",
            [
                _require(
                    "_lifetime_record.groups[effect_group] == true",
                    "That ward effect has already ended.",
                ),
            ],
        ),
        _when(
            "effect_group == 'doors'",
            [
                _require("object_id != ''", "Select an actual door carrying this ward's effect."),
                _call(
                    "format_srd_information_projection",
                    {"message": "{object_id}"},
                    ("_info_projection_message",),
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_info_projection_message}"},
                    ("_info_scene_entity", "_info_position"),
                ),
                _require(
                    "_info_scene_entity.id in _lifetime_record.door_ids and "
                    "_info_scene_entity.metadata.plane == _lifetime_record.plane",
                    "Select this ward's actual door on its recorded plane.",
                ),
                _set("_guards_owned_locks", []),
                _when(
                    "_info_scene_entity.metadata.srd_arcane_locks != null",
                    [
                        _set("_guards_owned_locks", "_info_scene_entity.metadata.srd_arcane_locks"),
                    ],
                ),
                _set("_guards_door_effect_found", False),
                _each(
                    "_guards_owned_locks",
                    "_guards_owned_lock",
                    [
                        _when(
                            "_guards_owned_lock.ward_source_id == source_id and "
                            "_guards_owned_lock.ward_group == 'doors' and "
                            "_guards_owned_lock.spell == 'arcane_lock' and "
                            "_guards_owned_lock.spell_level == _lifetime_record.spell_level",
                            [_set("_guards_door_effect_found", True)],
                        ),
                    ],
                ),
                _require(
                    "_guards_door_effect_found == true",
                    "The actual selected door has no retained ward lock.",
                ),
                _set("_guards_dispel_point", "_info_position"),
            ],
            [
                _require(inside, "Choose an actual point inside this ward's recorded area."),
            ],
        ),
        _when(
            "effect_group == 'corridors'",
            [
                _set("_guards_effect_found", False),
                _set("_guards_owned_corridors", "_lifetime_record.corridors"),
                _each(
                    "_guards_owned_corridors",
                    "_guards_owned_corridor",
                    [
                        _when(
                            " and ".join(
                                f"point[{axis}] >= _guards_owned_corridor.min[{axis}] and "
                                f"point[{axis}] <= _guards_owned_corridor.max[{axis}]"
                                for axis in range(3)
                            ),
                            [_set("_guards_effect_found", True)],
                        ),
                    ],
                ),
                _require("_guards_effect_found == true", "Select actual ward corridor fog."),
            ],
        ),
        _when(
            "effect_group == 'other'",
            [
                _require(
                    "_lifetime_record.other == 'dancing_lights'",
                    "This owner supports the actual ward lights.",
                ),
                _set("_guards_effect_found", False),
                _set("_guards_owned_lamps", "_lifetime_record.lamps"),
                _each(
                    "_guards_owned_lamps",
                    "_guards_owned_lamp",
                    [
                        _call(
                            "measure_srd_information_distance",
                            {"start": "{point}", "end": "{_guards_owned_lamp}"},
                            ("_scene_distance",),
                        ),
                        _when("_scene_distance <= 10", [_set("_guards_effect_found", True)]),
                    ],
                ),
                _require(
                    "_guards_effect_found == true",
                    "Select an actual ward light's current contribution.",
                ),
            ],
        ),
        *stairs,
        *_actual_path("_guards_dispel_point", "_lifetime_record.plane"),
        _when(
            "effect_group == ''",
            [
                _set(
                    "_adoption_dispel_check",
                    {
                        "source": "source_id",
                        "spell": "'guards_and_wards'",
                        "ended": False,
                        "roll": None,
                        "no_effect": True,
                    },
                ),
            ],
            [
                *_source_check("_lifetime_record", "spell_id"),
                _when(
                    "_dispel_succeeded == true",
                    [
                        _call(
                            "end_srd_guards_wards_effect",
                            {
                                "source_id": "{source_id}",
                                "group": "{effect_group}",
                                "reason": "dispelled",
                            },
                        ),
                    ],
                ),
            ],
        ),
    ]
    changed["dispel_srd_guards_wards_owned_source"] = _tool(
        "Dispel one real ward effect; Dispel has no effect on the parent itself.",
        [
            _entity("caster_state"),
            _lasting(),
            _param("source_id"),
            _param("effect_group", required=False, default=""),
            _param("object_id", required=False, default=""),
            _param("point", "array", items={"type": "number"}, min_items=3, max_items=3),
            _param("caster", resolve="participant", required=False),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
        ],
        effects,
    )
    # An Arcane Lock child already received the actual object-source check.
    # Its callback retires only this parent Doors effect, preserving independent locks.
    if "retire_srd_object_spell_source" in tools:
        retire = deepcopy(tools["retire_srd_object_spell_source"])
        marker = "_object_spell_source.ward_source_id == null"
        original = next(
            (e["on_true"] for e in retire["effects"] if e.get("condition") == marker), None
        )
        if original is None:
            original = retire["effects"][1:]
        retire["effects"] = [
            retire["effects"][0],
            _when(
                marker,
                original,
                [
                    _require(
                        "lasting_state.spell_guards_wards_sources != null and "
                        "_object_spell_source.ward_source_id in "
                        "lasting_state.spell_guards_wards_sources",
                        "The actual ward child has no retained parent.",
                    ),
                    _set(
                        "_guards_child_parent",
                        "lasting_state.spell_guards_wards_sources[_object_spell_source.ward_source_id]",
                    ),
                    _require(
                        "_guards_child_parent.installed == true and "
                        "_guards_child_parent.groups.doors == true and "
                        "object_id in _guards_child_parent.door_ids and "
                        "_object_spell_source.ward_group == 'doors' and "
                        "_object_spell_source.spell_level == _guards_child_parent.spell_level",
                        "Select the actual retained ward Door effect.",
                    ),
                    _call(
                        "end_srd_guards_wards_effect",
                        {
                            "source_id": "{_object_spell_source.ward_source_id}",
                            "group": "doors",
                            "reason": "dispelled",
                        },
                    ),
                ],
            ),
        ]
        if not any(p["name"] == "lasting_state" for p in retire["parameters"]):
            retire["parameters"].append(_lasting())
        changed["retire_srd_object_spell_source"] = retire
    for name in ("cast_spell", "cast_srd_recovery"):
        tool = changed.setdefault(name, deepcopy(tools[name]))
        if not any(p["name"] == "recovery_effect" for p in tool["parameters"]):
            tool["parameters"].append(_param("recovery_effect", required=False, default=""))
    for node in _walk(changed["cast_spell"]["effects"]):
        if node.get("call") == "cast_srd_recovery":
            node["params"]["recovery_effect"] = "{recovery_effect}"


def _barrier_tools(tools, changed):
    if "end_srd_spell_barrier_source" not in tools:
        return
    changed["dispel_srd_barrier_owned_source"] = _tool(
        "Dispel an exact active barrier at an admitted point inside its current boundary.",
        [
            _entity("caster_state"),
            _lasting(),
            _param("source_id"),
            _param("point", "array", items={"type": "number"}, min_items=3, max_items=3),
            _param("caster", resolve="participant", required=False),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
        ],
        [
            _require(
                "lasting_state.spell_barrier_sources != null and source_id in "
                "lasting_state.spell_barrier_sources",
                "Select an actual barrier source.",
            ),
            _set("_adoption_barrier", "lasting_state.spell_barrier_sources[source_id]"),
            _call(
                "check_srd_spell_barrier_source",
                {"caster_state": "{_adoption_barrier.caster_state_id}", "source_id": "{source_id}"},
                ("_barrier_source_active",),
            ),
            _require(
                "_barrier_source_active == true and _adoption_barrier.dispel_ongoing == true",
                "Select an actual active ongoing barrier.",
            ),
            _call(
                "read_srd_barrier_body",
                {"source_id": "{source_id}"},
                ("_barrier_body", "_barrier_plane", "_barrier_radius"),
            ),
            _set(
                "_adoption_barrier_center",
                [
                    "_barrier_body.position[0]",
                    "_barrier_body.position[1]",
                    "_barrier_body.position[2] + _barrier_body.height * 0.5",
                ],
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{point}", "end": "{_adoption_barrier_center}"},
                ("_scene_distance",),
            ),
            _require(
                "_scene_distance <= _barrier_radius and (_adoption_barrier.mode != 'hut' "
                "or point[2] >= _barrier_body.position[2])",
                "The chosen point must lie inside the actual saved barrier.",
            ),
            *_actual_path("point", "_barrier_plane"),
            *_source_check("_adoption_barrier", "spell_id"),
            _when(
                "_dispel_succeeded == true",
                [
                    _call(
                        "end_srd_spell_barrier_source",
                        {"source_id": "{source_id}", "reason": "dispel"},
                    ),
                    _call("refresh_srd_spell_barriers", {}),
                ],
            ),
        ],
    )


def _dwelling_tools(tools, changed):
    """Dispel the actual paired entrance, retaining each source's raw queued return."""
    if "collect_srd_dwelling_dispel_sources" not in tools:
        return
    changed["dispel_srd_dwelling_owned_source"] = _tool(
        "Dispel one actual ongoing Mansion or Rope Trick through its physical entrance.",
        [
            _entity("caster_state"),
            _lasting(),
            _param("source_id"),
            _param("subject"),
            _param("caster", resolve="participant", required=False),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
        ],
        [
            _call(
                "collect_srd_dwelling_dispel_sources",
                {"subject": "{subject}"},
                ("_dwelling_dispel_source_ids", "_dwelling_dispel_body"),
            ),
            _require(
                "source_id in _dwelling_dispel_source_ids",
                "Select this actual active dwelling's paired entrance.",
            ),
            _call(
                "read_srd_lifetime_dwelling_source",
                {"source_id": "{source_id}"},
                ("_lifetime_record", "_lifetime_active"),
            ),
            _require(
                "_lifetime_active == true and _lifetime_record.dispel_ongoing != false",
                "Select an actual ongoing dwelling source.",
            ),
            _set(
                "_dwelling_dispel_point",
                [
                    "_dwelling_dispel_body.position[0]",
                    "_dwelling_dispel_body.position[1]",
                    "_dwelling_dispel_body.position[2] + _dwelling_dispel_body.height * 0.5",
                ],
            ),
            *_actual_path("_dwelling_dispel_point", "_dwelling_dispel_body.metadata.plane"),
            *_source_check("_lifetime_record"),
            _when(
                "_dispel_succeeded == true",
                [
                    _call("retire_srd_owned_dwelling_source", {"source_id": "{source_id}"}),
                ],
            ),
        ],
    )


def _bind_selected_recovery(changed):
    recovery = changed["cast_srd_recovery"]
    choices = [kind for kind in SELECTED_KINDS if "dispel_srd_" + kind + "_owned_source" in changed]
    marker = " and ".join("recovery_choice != " + repr(kind) for kind in choices)
    ordinary_marker = "_recovery_selected_source != true"
    ordinary = []
    source_conditions = {
        "spell == 'dispel_magic' and recovery_choice == " + repr(k) for k in SELECTED_KINDS
    }
    for effect in recovery["effects"]:
        if effect.get("condition") == ordinary_marker:
            ordinary = effect["on_true"][2:]
            break
    if not ordinary:
        ordinary = [
            effect
            for effect in recovery["effects"]
            if effect.get("condition") not in source_conditions
        ]
    # An omitted recipient is admitted only by an actual selected-source owner.
    # Ordinary targets must be present before the clock or any effect mutates.
    for parameter in recovery["parameters"]:
        if parameter["name"] in {"target_state", "target_distance"}:
            parameter.update(required=False, default=None)
    recovery["effects"] = ordinary

    def wrap(effects, guarded=False):
        for i, effect in enumerate(effects):
            protected = guarded or effect.get("condition") == marker
            if effect.get("call") == "dispel_srd_target_sources" and not protected:
                effects[i] = _when(marker, [effect])
            else:
                for branch in ("on_true", "on_false"):
                    if branch in effect:
                        wrap(effect[branch], protected)

    wrap(recovery["effects"])
    recovery["effects"] = [
        _set("_recovery_source_intents", [repr(k) for k in SELECTED_KINDS]),
        _set("_recovery_source_owners", [repr(k) for k in choices]),
        _require(
            "spell != 'dispel_magic' or recovery_choice not in _recovery_source_intents "
            "or recovery_choice in _recovery_source_owners",
            "The selected Dispel source has no admitted owning callback.",
        ),
        _set(
            "_recovery_selected_source",
            "spell == 'dispel_magic' and recovery_choice in _recovery_source_owners",
        ),
        _when(
            ordinary_marker,
            [
                _require("target_state != null", "Select an actual ordinary Recovery recipient."),
                _require(
                    "target_distance != null", "Qualify the ordinary Recovery recipient range."
                ),
                *recovery["effects"],
            ],
        ),
        _when(
            "_recovery_selected_source == true",
            [
                _require("recovery_source_id != ''", "Select the actual retained Dispel source."),
                _set("_spell_summary_rows", []),
                _set("_spell_detail_rows", []),
                _set("_revival_succeeded", False),
            ],
        ),
    ]
    for kind in choices:
        condition = "spell == 'dispel_magic' and recovery_choice == " + repr(kind)
        recovery["effects"] = [e for e in recovery["effects"] if e.get("condition") != condition]
        params = {
            "caster_state": "{_caster_state_id}",
            "caster": "{_caster_id}",
            "source_id": "{recovery_source_id}",
            "slot_level": "{slot_level}",
            "casting_modifier": "{casting_modifier}",
            "casting_ability": "{casting_ability}",
        }
        if kind in {"supply", "object_utility", "heat_metal"}:
            params["object_id"] = "{recovery_item}"
        if kind in {"barrier", "secure_area", "passwall", "mirage", "hallow", "guards_wards"}:
            params["point"] = "{recovery_position}"
        if kind == "guards_wards":
            params["effect_group"] = "{recovery_effect}"
            params["object_id"] = "{recovery_item}"
        if kind == "dream":
            params["target_state"] = "{_target_state_id}"
        if kind in {"levitate", "dwelling"}:
            params["subject"] = "{recovery_surface}"
        if kind == "telekinesis":
            params["subject"] = "{recovery_surface}"
            params["target_state"] = "{_target_state_id}"
        recovery["effects"].append(
            _when(
                condition,
                [
                    _call(
                        "dispel_srd_" + kind + "_owned_source", params, ("_adoption_dispel_check",)
                    ),
                    _set("_dispel_checks", ["_adoption_dispel_check"]),
                    _set("_adoption_dispel_detail", "'Automatic at this slot level'"),
                    _when(
                        "_adoption_dispel_check.no_effect == true",
                        [
                            _set(
                                "_adoption_dispel_detail",
                                "'Dispel Magic has no effect on Guards and Wards itself.'",
                            ),
                        ],
                    ),
                    _when(
                        "_adoption_dispel_check.roll != null",
                        [
                            _call(
                                "format_srd_information_projection",
                                {
                                    "message": "d20 {_adoption_dispel_check.roll.results}; "
                                    "selected {_adoption_dispel_check.roll.total}; "
                                    "modifier {_adoption_dispel_check.roll.modifier}; "
                                    "DC {_adoption_dispel_check.dc}; "
                                    "effect ended: {_adoption_dispel_check.ended}",
                                },
                                ("_info_projection_message",),
                            ),
                            _set("_adoption_dispel_detail", "_info_projection_message"),
                        ],
                    ),
                    _set(
                        "_spell_summary_rows",
                        [
                            {
                                "label": "'Selected source ended'",
                                "value": "_adoption_dispel_check.ended",
                            }
                        ],
                    ),
                    _set(
                        "_spell_detail_rows",
                        [
                            {"label": "'Source'", "value": "recovery_source_id"},
                            {
                                "label": "'Actual Dispel check'",
                                "value": "_adoption_dispel_detail",
                            },
                        ],
                    ),
                ],
            )
        )


def _blink_dispel(tools, changed):
    if "end_srd_blink_target" not in tools:
        return
    changed["dispel_srd_blink_owned_target"] = _tool(
        "Dispel one actual finite Blink grant, returning its phased recipient before removal.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("caster", resolve="participant", required=False),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
        ],
        [
            _set("_blink_dispel_checks", []),
            _when(
                "target_state.travel_blink != null and "
                "lasting_state.lasting_tick < target_state.travel_blink.expires_tick",
                [
                    _set("_blink_dispel_source", "target_state.travel_blink"),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        ("_info_scene_entity", "_info_position"),
                    ),
                    _set("_blink_dispel_point", "_info_position"),
                    _set("_blink_dispel_plane", "_info_scene_entity.metadata.plane"),
                    *_actual_path("_blink_dispel_point", "_blink_dispel_plane"),
                    _call(
                        "resolve_srd_dispel_source_check",
                        {
                            "caster_state": "{_caster_state_id}",
                            "caster": "{_caster_id}",
                            "slot_level": "{slot_level}",
                            "source_level": "{_blink_dispel_source.slot_level}",
                            "casting_modifier": "{casting_modifier}",
                            "casting_ability": "{casting_ability}",
                        },
                        ("_dispel_succeeded", "_dispel_roll"),
                    ),
                    _push(
                        "_blink_dispel_checks",
                        {"spell": "'blink'", "ended": "_dispel_succeeded", "roll": "_dispel_roll"},
                    ),
                    _when(
                        "_dispel_succeeded == true",
                        [
                            _call("end_srd_blink_target", {"target_state": "{_target_state_id}"}),
                        ],
                    ),
                ],
            ),
        ],
    )
    dispel = changed["dispel_srd_target_sources"]
    marker = "target_state.travel_blink != null"
    dispel["effects"] = [
        e
        for e in dispel["effects"]
        if not (e.get("condition") == marker and "dispel_srd_blink_owned_target" in str(e))
    ]
    dispel["effects"].append(
        _when(
            marker,
            [
                _call(
                    "dispel_srd_blink_owned_target",
                    {
                        "target_state": "{_target_state_id}",
                        "caster_state": "{_caster_state_id}",
                        "caster": "{_caster_id}",
                        "slot_level": "{slot_level}",
                        "casting_modifier": "{casting_modifier}",
                        "casting_ability": "{casting_ability}",
                    },
                    ("_blink_dispel_checks",),
                ),
                _each(
                    "_blink_dispel_checks",
                    "_blink_dispel_check",
                    [_push("_dispel_checks", "_blink_dispel_check")],
                ),
            ],
        )
    )


def _creature_joins(tools, changed):
    """Join only effects whose real subject is the selected creature, never caster bookkeeping."""
    dispel = changed["dispel_srd_target_sources"]
    common = {
        "caster_state": "{_caster_state_id}",
        "caster": "{_caster_id}",
        "slot_level": "{slot_level}",
        "casting_modifier": "{casting_modifier}",
        "casting_ability": "{casting_ability}",
    }
    if "retire_srd_appearance_source" in tools:
        marker = "lasting_state.has_appearance_spells == true"
        dispel["effects"] = [
            e
            for e in dispel["effects"]
            if not (e.get("condition") == marker and "_adoption_appearance_ids" in str(e))
        ]
        dispel["effects"].append(
            _when(
                marker,
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        ("_info_scene_entity",),
                    ),
                    _set("_adoption_appearance_subject", "_info_scene_entity.id"),
                    _set("_adoption_appearance_ids", "lasting_state.appearance_source_ids"),
                    _each(
                        "_adoption_appearance_ids",
                        "_adoption_appearance_id",
                        [
                            _call(
                                "collect_srd_appearance_source",
                                {"appearance_id": "{_adoption_appearance_id}"},
                                ("_appearance_record", "_appearance_active"),
                            ),
                            _when(
                                "_appearance_active == true and _appearance_record.subject == "
                                "_adoption_appearance_subject and "
                                "(_appearance_record.spell == 'disguise_self' "
                                "or _appearance_record.spell == 'seeming')",
                                [
                                    _call(
                                        "dispel_srd_appearance_owned_source",
                                        common | {"source_id": "{_adoption_appearance_id}"},
                                        ("_adoption_dispel_check",),
                                    ),
                                    _push("_dispel_checks", "_adoption_dispel_check"),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        )
        node_name = (
            "dismiss_srd_appearance_node"
            if "dismiss_srd_appearance_node" in tools
            else "dismiss_srd_appearance"
        )
        if node_name in tools:
            old = deepcopy(tools[node_name])
            old["effects"] = [e for e in old["effects"] if "require" in e] + [
                _call(
                    "collect_srd_appearance_source",
                    {"appearance_id": "{appearance_id}"},
                    ("_appearance_record", "_appearance_active"),
                ),
                _require(
                    "_appearance_active == true and _appearance_record.cast_id != null and "
                    "_appearance_record.cast_id != '' and "
                    "_appearance_record.source_caster_id == _caster_state_id",
                    "Select an authentic finite appearance cast owned by this caster.",
                ),
                _call(
                    "dismiss_srd_spell",
                    {
                        "caster_state": "{_caster_state_id}",
                        "cast_id": "{_appearance_record.cast_id}",
                    },
                ),
                _set("_appearance_dismissed", True),
            ]
            # The obsolete source-owner require refers to a local that is now
            # supplied by collect; retain only the ordinary pending/action fences.
            old["effects"] = [
                e
                for e in old["effects"]
                if not ("require" in e and "_appearance_record.caster" in str(e))
            ]
            changed[node_name] = old
    if "end_srd_spell_barrier_source" in tools:
        marker = "lasting_state.has_spell_barriers == true"
        dispel["effects"] = [
            e
            for e in dispel["effects"]
            if not (e.get("condition") == marker and "_adoption_barrier_ids" in str(e))
        ]
        dispel["effects"].append(
            _when(
                marker,
                [
                    _set("_adoption_barrier_ids", "lasting_state.spell_barrier_source_ids"),
                    _each(
                        "_adoption_barrier_ids",
                        "_adoption_barrier_id",
                        [
                            _set(
                                "_adoption_barrier_source",
                                "lasting_state.spell_barrier_sources[_adoption_barrier_id]",
                            ),
                            _when(
                                "_adoption_barrier_source.installed == true and "
                                "((_adoption_barrier_source.mode == 'resilient' and "
                                "_adoption_barrier_source.target_state_id == _target_state_id) or "
                                "(_adoption_barrier_source.mode == 'antilife' and "
                                "_adoption_barrier_source.caster_state_id == _target_state_id))",
                                [
                                    _call(
                                        "read_srd_barrier_body",
                                        {"source_id": "{_adoption_barrier_id}"},
                                        ("_barrier_body",),
                                    ),
                                    _set(
                                        "_adoption_barrier_point",
                                        [
                                            "_barrier_body.position[0]",
                                            "_barrier_body.position[1]",
                                            "_barrier_body.position[2] + "
                                            "_barrier_body.height * 0.5",
                                        ],
                                    ),
                                    _call(
                                        "dispel_srd_barrier_owned_source",
                                        common
                                        | {
                                            "source_id": "{_adoption_barrier_id}",
                                            "point": "{_adoption_barrier_point}",
                                        },
                                        ("_adoption_dispel_check",),
                                    ),
                                    _push("_dispel_checks", "_adoption_dispel_check"),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        )


def _selected_creature_images(changed):
    tool = changed.get("dispel_srd_illusion_source")
    if tool is None:
        return
    for node in _walk(tool["effects"]):
        if node.get("condition") != "select_proxy == true":
            continue
        original_proxy = node["on_true"]
        creature = deepcopy(node["on_false"])
        node["on_true"] = [
            _when(
                "_illusion_record.spell == 'mirror_image' or "
                "_illusion_record.spell == 'phantasmal_force'",
                creature,
                original_proxy,
            )
        ]
        break


def _lifetime_creature_joins(tools, changed):
    """Creature Dispel follows actual Dream recipients and raised bodies.

    A caster kept only as bookkeeping is excluded; Telekinesis grants its actual caster ability.
    """
    dispel = changed["dispel_srd_target_sources"]
    # Dream's linked condition is one aspect of this same source; its registry
    # retirement owns both trance consequences and retained nightmare aftermath.
    for node in _walk(dispel["effects"]):
        condition = node.get("condition", "")
        if "_recovery_source.dispel_ongoing != false" in condition and (
            "_recovery_source.dream_source_id" not in condition
        ):
            node["condition"] = condition + " and _recovery_source.dream_source_id == null"
    dismiss = changed["dismiss_srd_spell"]
    for node in _walk(dismiss["effects"]):
        condition = node.get("condition", "")
        if "_dismiss_source.installed == true" in condition and (
            "_dismiss_source.dream_source_id" not in condition
        ):
            node["condition"] = condition + " and _dismiss_source.dream_source_id == null"
    common = {
        "caster_state": "{_caster_state_id}",
        "caster": "{_caster_id}",
        "target_state": "{_target_state_id}",
        "slot_level": "{slot_level}",
        "casting_modifier": "{casting_modifier}",
        "casting_ability": "{casting_ability}",
    }
    if "retire_srd_dream_source" in tools:
        marker = "lasting_state.has_dream_spells == true"
        dispel["effects"] = [
            e
            for e in dispel["effects"]
            if not (e.get("condition") == marker and "_lifetime_dream_dispel_ids" in str(e))
        ]
        collect = [_set("_lifetime_dream_dispel_ids", [])]
        for index in ("dream_target_source_ids", "dream_messenger_source_ids"):
            collect += [
                _when(
                    "target_state." + index + " != null",
                    [
                        _set("_lifetime_dream_candidates", "target_state." + index),
                        _each(
                            "_lifetime_dream_candidates",
                            "_lifetime_dream_id",
                            [
                                _when(
                                    "_lifetime_dream_id not in _lifetime_dream_dispel_ids",
                                    [
                                        _push("_lifetime_dream_dispel_ids", "_lifetime_dream_id"),
                                    ],
                                ),
                            ],
                        ),
                    ],
                )
            ]
        collect += [
            _each(
                "_lifetime_dream_dispel_ids",
                "_lifetime_dream_id",
                [
                    _call(
                        "read_srd_lifetime_dream_source",
                        {"source_id": "{_lifetime_dream_id}"},
                        ("_lifetime_active",),
                    ),
                    _when(
                        "_lifetime_active == true",
                        [
                            _call(
                                "dispel_srd_dream_owned_source",
                                common
                                | {
                                    "source_id": "{_lifetime_dream_id}",
                                },
                                ("_adoption_dispel_check",),
                            ),
                            _push("_dispel_checks", "_adoption_dispel_check"),
                        ],
                    ),
                ],
            )
        ]
        dispel["effects"].append(_when(marker, collect))
    if "mark_srd_levitate_return" in tools:
        marker = "lasting_state.has_levitate_sources == true"
        dispel["effects"] = [
            e
            for e in dispel["effects"]
            if not (e.get("condition") == marker and "_lifetime_raised_sources" in str(e))
        ]
        dispel["effects"].append(
            _when(
                marker,
                [
                    _set("_lifetime_raised_sources", "lasting_state.levitate_sources"),
                    _each(
                        "_lifetime_raised_sources",
                        "_lifetime_raised_source",
                        [
                            _when(
                                "_lifetime_raised_source.active == true and "
                                "_lifetime_raised_source.creature_id == _target_state_id and "
                                "lasting_state.lasting_tick < _lifetime_raised_source.expires_tick",
                                [
                                    _call(
                                        "dispel_srd_levitate_owned_source",
                                        common
                                        | {
                                            "source_id": "{_lifetime_raised_source.id}",
                                            "subject": "{_lifetime_raised_source.subject}",
                                        },
                                        ("_adoption_dispel_check",),
                                    ),
                                    _push("_dispel_checks", "_adoption_dispel_check"),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        )
    if "end_srd_telekinesis_source" in tools:
        marker = "lasting_state.has_telekinesis_sources == true"
        dispel["effects"] = [
            e
            for e in dispel["effects"]
            if not (e.get("condition") == marker and "_lifetime_tk_sources" in str(e))
        ]
        dispel["effects"].append(
            _when(
                marker,
                [
                    _set("_lifetime_tk_sources", "lasting_state.telekinesis_sources"),
                    _each(
                        "_lifetime_tk_sources",
                        "_lifetime_tk_source",
                        [
                            _when(
                                "_lifetime_tk_source.active == true and "
                                "(_lifetime_tk_source.caster == _target_state_id or "
                                "_lifetime_tk_source.creature_id == _target_state_id) and "
                                "lasting_state.lasting_tick < _lifetime_tk_source.expires_tick",
                                [
                                    _call(
                                        "dispel_srd_telekinesis_owned_source",
                                        common | {"source_id": "{_lifetime_tk_source.id}"},
                                        ("_adoption_dispel_check",),
                                    ),
                                    _push("_dispel_checks", "_adoption_dispel_check"),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        )


def extend_tools(tools, changed):
    """Compose only families with admitted exact retirement callbacks; no extra registry."""
    _lifetime_tools(tools, changed)
    _join_dismissal(tools, changed)
    _object_source_tools(tools, changed)
    _appearance_tools(tools, changed)
    _barrier_tools(tools, changed)
    _guards_wards_tools(tools, changed)
    _dwelling_tools(tools, changed)
    if __package__:
        from .srd_delayed_blast_completion import extend_removal_tools
    else:
        from srd_delayed_blast_completion import extend_removal_tools
    extend_removal_tools(tools, changed)
    _bind_selected_recovery(changed)
    _blink_dispel(tools, changed)
    _creature_joins(tools, changed)
    _selected_creature_images(changed)
    _lifetime_creature_joins(tools, changed)
    # Object effects live on their actual objects even when caster IDs are kept
    # as bookkeeping targets in the shared turn-effect storage.
    marker = (
        "_dispel_effect.kind != 'heat_metal_spell' and "
        "_dispel_effect.kind != 'object_utility_spell' and "
        "_dispel_effect.kind != 'delayed_blast_fireball'"
    )

    def exclude_bookkeeping(effects, guarded=False):
        for i, node in enumerate(effects):
            protected = guarded or node.get("condition") == marker
            if "_dispel_effect.target == _target_state_id" in node.get("condition", ""):
                for kind in ("heat_metal_spell", "object_utility_spell", "delayed_blast_fireball"):
                    node["condition"] = node["condition"].replace(
                        " and _dispel_effect.kind != " + repr(kind), ""
                    )
                if not protected:
                    effects[i] = _when(marker, [node])
            else:
                for branch in ("on_true", "on_false"):
                    if branch in node:
                        exclude_bookkeeping(node[branch], protected)

    exclude_bookkeeping(changed["dispel_srd_target_sources"]["effects"])
