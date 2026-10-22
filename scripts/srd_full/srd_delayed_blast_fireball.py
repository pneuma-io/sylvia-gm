"""Delayed Blast Fireball queues authentic ended sources before outer-boundary detonation."""

from copy import deepcopy

if __package__:
    from . import srd_sunbeam as beam
    from . import srd_sunburst_geometry as sphere
    from .srd_scene_relations import _snapshot, _tool_scene
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
    from .srd_timed_senses import _loop, _public, _push, _replace_tools
else:
    import srd_sunbeam as beam
    import srd_sunburst_geometry as sphere
    from srd_scene_relations import _snapshot, _tool_scene
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
    from srd_timed_senses import _loop, _public, _push, _replace_tools


SPELL = "delayed_blast_fireball"
FAMILY = "delayed_blast_fireball"
PROFILE = {
    "family": FAMILY,
    "casting_time": "action",
    "base_level": 7,
    "range_kind": "distance",
    "range_feet": 150,
    "concentration": True,
    "duration_rounds": 10,
    "base_dice": 12,
    "damage_die": "d6",
    "damage_type": "fire",
    "save_ability": "dexterity",
    "success_damage": "half",
}
PUBLIC_TOOLS = {"detonate_srd_delayed_blast_fireball"}
QUEUE = "queue_srd_delayed_blast_fireball"
DRAIN = "resolve_srd_delayed_blast_detonations"
REFRESH = "refresh_srd_delayed_blast_fireball"
ROW_FIELDS = (
    "kind",
    "spell",
    "source_id",
    "caster",
    "target",
    "active",
    "suppressed",
    "created_tick",
    "expires_tick",
    "slot_level",
    "casting_ability",
    "cast_id",
    "source_caster_id",
    "spell_level",
    "ongoing_duration",
    "save_dc",
    "origin",
    "plane",
    "growth_dice",
    "last_caster_end_turn",
)


def schema():
    return {
        "type": "object",
        "properties": {
            name: {
                "type": "boolean"
                if isinstance(value, bool)
                else "integer"
                if isinstance(value, int)
                else "string",
                "enum": [value],
            }
            for name, value in PROFILE.items()
            if name != "family"
        },
        "required": [name for name in PROFILE if name != "family"],
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    for spell, definition in definitions.items():
        if definition.get("family") == FAMILY and (spell != SPELL or definition != PROFILE):
            raise ValueError("Delayed Blast Fireball differs from the pinned fixed source.")


def _rename(value):
    if isinstance(value, str):
        return (
            value.replace("_sunbeam_", "_dbf_")
            .replace("sunbeam", SPELL)
            .replace("has_dbf_sources", "has_delayed_blast_fireball_sources")
            .replace("Sunbeam", "Delayed Blast Fireball")
        )
    if isinstance(value, list):
        return [_rename(child) for child in value]
    if isinstance(value, dict):
        return {_rename(key): _rename(child) for key, child in value.items()}
    return value


def _intent():
    return _parameter(
        "delayed_blast_origin",
        "array",
        required=False,
        default=[],
        items={"type": "number"},
        min_items=0,
        max_items=3,
    )


def _messages(value):
    if isinstance(value, str):
        return value.replace("Sunburst", "Delayed Blast Fireball")
    if isinstance(value, list):
        return [_messages(child) for child in value]
    if isinstance(value, dict):
        return {key: _messages(child) for key, child in value.items()}
    return value


def _installer():
    tool = _rename(beam._installer())
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "mote_offset"]
    next(p for p in tool["parameters"] if p["name"] == "slot_level")["minimum"] = 7
    tool["parameters"] += [
        _parameter("origin", "array", items={"type": "number"}, min_items=3, max_items=3),
        _parameter("plane"),
    ]
    tool["effects"][:0] = [
        _set("_dbf_end_turn", -1),
        _when(
            "caster_state.turns_started != null",
            [
                _set("_dbf_end_turn", "caster_state.turns_started - 1"),
            ],
        ),
    ]
    for effects in beam._lists(tool["effects"]):
        for effect in effects:
            row = effect.get("list_push", {}).get("value")
            if isinstance(row, dict) and row.get("kind") == FAMILY:
                row.pop("mote_offset")
                row.update(
                    origin="origin",
                    plane="plane",
                    growth_dice=0,
                    last_caster_end_turn="_dbf_end_turn",
                )
                assert set(row) == set(ROW_FIELDS)
    return tool


def _point_validator():
    return _messages(
        sphere._replace_names(
            sphere._sphere_tool(),
            {
                "sunburst_origin": "delayed_blast_origin",
                "sunburst_plane": "_dbf_chosen_plane",
                "_sunburst_origin": "_dbf_origin",
                "_sunburst_plane": "_dbf_plane",
                "_sunburst_source": "_dbf_source_frame",
            },
        )
    ) | {"description": "Qualify the actual initial150-foot bead point and scene."}


def _collector():
    """Collect actual Sphere/Box recipients from the accepted bead, without recasting range."""
    original = sphere._sphere_tool()
    index = next(
        i
        for i, effect in enumerate(original["effects"])
        if effect.get("set", {}).get("field") == "_sunburst_sphere_targets"
    )
    effects = sphere._replace_names(
        original["effects"][index:],
        {
            "sunburst_origin": "origin",
            "_sunburst_origin": "_dbf_origin",
            "_sunburst_plane": "_dbf_plane",
            "_sunburst_sphere_targets": "_dbf_targets",
        },
    )
    for sequence in beam._lists(effects):
        for effect in sequence:
            if "<= 60 ** 2" in effect.get("condition", ""):
                effect["condition"] = effect["condition"].replace("<= 60 ** 2", "<= 20 ** 2")
    return _tool_scene(
        "Collect every supported actual creature Box intersecting the ended bead's20-foot Sphere.",
        [
            _parameter("origin", "array", items={"type": "number"}, min_items=3, max_items=3),
            _parameter("plane"),
        ],
        [
            *_snapshot(),
            _set("_dbf_origin", "origin"),
            _set("_dbf_plane", "plane"),
            *_messages(effects),
        ],
    )


def _queue_tool():
    """Only a still-admitted owned row may produce the once-only ended-source receipt."""
    return _tool(
        "Queue one actual ended Delayed Blast Fireball; this leaf never resolves damage.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_dbf_ending_record", None),
            _set("_dbf_ending_rows", []),
            _when(
                "state.turn_effects != null",
                [
                    _set("_dbf_ending_rows", "state.turn_effects"),
                ],
            ),
            _loop(
                "_dbf_ending_rows",
                "_dbf_row",
                [
                    _when(
                        "_dbf_row.kind == 'delayed_blast_fireball' and "
                        "_dbf_row.spell == 'delayed_blast_fireball' and "
                        "_dbf_row.source_id == source_id and _dbf_row.caster == _caster_state_id",
                        [
                            _require(
                                "_dbf_ending_record == null", "The bead has ambiguous source rows."
                            ),
                            _require(
                                "_dbf_row.cast_id == source_id and _dbf_row.active == true and "
                                "_dbf_row.source_caster_id == _caster_state_id and "
                                "_dbf_row.slot_level >= 7 and _dbf_row.slot_level <= 9",
                                "Only this authenticated original paid bead may detonate.",
                            ),
                            _set("_dbf_ending_record", "_dbf_row"),
                        ],
                    ),
                ],
            ),
            _when(
                "_dbf_ending_record != null",
                [
                    _set("_dbf_old_event", None),
                    _when(
                        "state.delayed_blast_detonations != null",
                        [
                            _when(
                                "state.delayed_blast_detonations[source_id] != null",
                                [
                                    _set(
                                        "_dbf_old_event",
                                        "state.delayed_blast_detonations[source_id]",
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_dbf_old_event == null",
                        [
                            _set(
                                "state.delayed_blast_detonations.{source_id}",
                                {
                                    "source_id": "source_id",
                                    "record": "_dbf_ending_record",
                                    "status": "queued",
                                    "ended_tick": "state.lasting_tick",
                                },
                            ),
                            _set("_dbf_fifo", []),
                            _when(
                                "state.delayed_blast_detonation_ids != null",
                                [
                                    _set("_dbf_fifo", "state.delayed_blast_detonation_ids"),
                                ],
                            ),
                            _require(
                                "len(_dbf_fifo) < 128",
                                "Resolve the existing bead detonation queue.",
                            ),
                            _push("_dbf_fifo", "source_id"),
                            _set("state.delayed_blast_detonation_ids", "_dbf_fifo"),
                            _set("state.has_delayed_blast_detonations", True),
                        ],
                    ),
                    _call("retire_srd_delayed_blast_fireball_source", {"source_id": "{source_id}"}),
                ],
            ),
        ],
    )


def _refresh_tool():
    """An actual lost lifetime queues before removing it; a new concentration is never ended."""
    return _tool(
        "Refresh actual bead lifetimes, queuing their printed ending exactly once.",
        [],
        [
            _set("_dbf_refresh_rows", []),
            _set("_dbf_any_live", False),
            _when("state.turn_effects != null", [_set("_dbf_refresh_rows", "state.turn_effects")]),
            _loop(
                "_dbf_refresh_rows",
                "_dbf_row",
                [
                    _when(
                        "_dbf_row.kind == 'delayed_blast_fireball'",
                        [
                            _call(
                                "check_srd_delayed_blast_fireball_record",
                                {
                                    "caster_state": "{_dbf_row.caster}",
                                    "record": "{_dbf_row}",
                                },
                                ["_dbf_live"],
                            ),
                            _when(
                                "_dbf_live != true",
                                [
                                    _call(
                                        QUEUE,
                                        {
                                            "caster_state": "{_dbf_row.caster}",
                                            "source_id": "{_dbf_row.source_id}",
                                        },
                                    ),
                                    _call(
                                        "settle_srd_delayed_blast_concentration",
                                        {
                                            "caster_state": "{_dbf_row.caster}",
                                            "source_id": "{_dbf_row.source_id}",
                                        },
                                    ),
                                ],
                            ),
                            _when("_dbf_live == true", [_set("_dbf_any_live", True)]),
                        ],
                    ),
                ],
            ),
            _set("state.has_delayed_blast_fireball_sources", "_dbf_any_live"),
        ],
    )


def _settle_tool():
    return _tool(
        "End only the expired bead's exact current concentration, never its successor.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _when(
                "caster_state.concentration_spell_id == 'delayed_blast_fireball' and "
                "caster_state.concentration_cast_id == source_id",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{_caster_state_id}",
                        },
                    ),
                ],
            ),
        ],
    )


def _retire_tool():
    """Retirement recomputes its row index without calling a lifecycle that can end sources."""
    return _tool(
        "Remove only this ended bead and derive the remaining actual source index.",
        [_parameter("source_id")],
        [
            _set("_dbf_retire_rows", []),
            _set("_dbf_retire_kept", []),
            _set("_dbf_retire_any", False),
            _when("state.turn_effects != null", [_set("_dbf_retire_rows", "state.turn_effects")]),
            _loop(
                "_dbf_retire_rows",
                "_dbf_retire_row",
                [
                    _when(
                        "_dbf_retire_row.kind != 'delayed_blast_fireball' or "
                        "_dbf_retire_row.source_id != source_id",
                        [
                            _push("_dbf_retire_kept", "_dbf_retire_row"),
                            _when(
                                "_dbf_retire_row.kind == 'delayed_blast_fireball'",
                                [
                                    _call(
                                        "check_srd_delayed_blast_fireball_record",
                                        {
                                            "caster_state": "{_dbf_retire_row.caster}",
                                            "record": "{_dbf_retire_row}",
                                        },
                                        ["_dbf_live"],
                                    ),
                                    _when("_dbf_live == true", [_set("_dbf_retire_any", True)]),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("state.turn_effects", "_dbf_retire_kept"),
            _set("state.has_delayed_blast_fireball_sources", "_dbf_retire_any"),
        ],
    )


def _finish_removal():
    return _tool(
        "Finish only this genuine ended bead; the authentic row must be queued before removal.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _call(QUEUE, {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"}),
            _call(
                "settle_srd_delayed_blast_concentration",
                {
                    "caster_state": "{_caster_state_id}",
                    "source_id": "{source_id}",
                },
            ),
        ],
    )


def _growth_tool():
    """Only the real creating caster's once-per-turn end earns another die while still live."""
    return _tool(
        "Earn the bead's printed die at its actual caster's genuine turn end.",
        [_entity("caster_state"), _parameter("boundary_turn", "integer", minimum=0)],
        [
            _set("_dbf_growth_rows", []),
            _set("_dbf_growth_kept", []),
            _when("state.turn_effects != null", [_set("_dbf_growth_rows", "state.turn_effects")]),
            _loop(
                "_dbf_growth_rows",
                "_dbf_row",
                [
                    _set("_dbf_growth_next", "_dbf_row"),
                    _when(
                        "_dbf_row.kind == 'delayed_blast_fireball' and "
                        "_dbf_row.caster == _caster_state_id",
                        [
                            _require(
                                "caster_state.turns_started != null and "
                                "caster_state.turns_started == boundary_turn",
                                "Use the creating caster's actual saved turn identity.",
                            ),
                            _call(
                                "check_srd_delayed_blast_fireball_record",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "record": "{_dbf_row}",
                                },
                                ["_dbf_live"],
                            ),
                            _when(
                                "_dbf_live == true and "
                                "boundary_turn > _dbf_row.last_caster_end_turn",
                                [
                                    _set(
                                        "_dbf_growth_changes",
                                        {
                                            "growth_dice": "_dbf_row.growth_dice + 1",
                                            "last_caster_end_turn": "boundary_turn",
                                        },
                                    ),
                                    _set(
                                        "_dbf_growth_next", "merge(_dbf_row, _dbf_growth_changes)"
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _push("_dbf_growth_kept", "_dbf_growth_next"),
                ],
            ),
            _set("state.turn_effects", "_dbf_growth_kept"),
        ],
    )


def _cast_tool():
    install = _installer()
    parameters = [
        deepcopy(p) for p in install["parameters"] if p["name"] not in {"origin", "plane"}
    ]
    parameters.append(_intent())
    return _tool(
        "Install this genuine paid bead without another action, slot or source allocation.",
        parameters,
        [
            _call(
                "qualify_srd_delayed_blast_point",
                {
                    "caster_state": "{_caster_state_id}",
                    "delayed_blast_origin": "{delayed_blast_origin}",
                },
                ["_dbf_origin", "_dbf_plane"],
            ),
            _call(
                "install_srd_delayed_blast_fireball",
                {
                    p["name"]: "{_caster_state_id}"
                    if p["name"] == "caster_state"
                    else "{_dbf_origin}"
                    if p["name"] == "origin"
                    else "{_dbf_plane}"
                    if p["name"] == "plane"
                    else "{" + p["name"] + "}"
                    for p in install["parameters"]
                    if p["name"] != "lasting_state"
                },
                ["_dbf_source_id"],
            ),
            _call(
                "record_srd_result_text",
                {
                    "text": "Bead retained; no initial damage. "
                    "Each actual caster turn end earns1d6; ending detonates its20-foot Sphere."
                },
                ["_recorded_result_text"],
            ),
            _set("_spell_detail_rows", [{"label": "'Effect'", "value": "_recorded_result_text"}]),
        ],
    )


def _detonation_tool():
    """The saved receipt is the accepted event, even after caster death or source expiry."""
    return _tool(
        "Resolve one authentic queued bead through existing save/typed/one-HP owners.",
        [_parameter("source_id")],
        [
            _set("_dbf_event", "state.delayed_blast_detonations[source_id]"),
            _require(
                "_dbf_event.status == 'queued' and _dbf_event.source_id == source_id and "
                "_dbf_event.record.source_id == source_id and "
                "_dbf_event.record.cast_id == source_id and "
                "_dbf_event.record.kind == 'delayed_blast_fireball'",
                "Only the actual queued original bead may resolve.",
            ),
            _set("_dbf_record", "_dbf_event.record"),
            _call(
                "collect_srd_delayed_blast_targets",
                {
                    "origin": "{_dbf_record.origin}",
                    "plane": "{_dbf_record.plane}",
                },
                ["_dbf_targets"],
            ),
            _call(
                "collect_srd_delayed_blast_cover_targets",
                {
                    "source_state": "{_dbf_record.caster}",
                    "origin": "{_dbf_record.origin}",
                    "area_plane": "{_dbf_record.plane}",
                    "targets": "{_dbf_targets}",
                },
                ["_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters"],
            ),
            _set("_dbf_dice_count", "12 + _dbf_record.slot_level - 7 + _dbf_record.growth_dice"),
            {"dice": {"expression": "{_dbf_dice_count}d6"}, "store_as": "_dbf_damage_roll"},
            _set("_dbf_raw_damage", "_dbf_damage_roll.total"),
            _call(
                "modify_srd_actor_damage_roll",
                {
                    "actor_state": "{_dbf_record.caster}",
                    "amount": "{_dbf_raw_damage}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ["_actor_modified_damage"],
            ),
            _set("_dbf_damage_total", "_actor_modified_damage"),
            _set("_dbf_summary", []),
            _set("_dbf_details", []),
            _loop(
                "_srd_area_targets",
                "_dbf_target",
                [
                    _call(
                        "resolve_spell_area_save_damage_target",
                        {
                            "spell": SPELL,
                            "target": "{_dbf_target}",
                            "save_ability": "dexterity",
                            "spell_save_dc": "{_dbf_record.save_dc}",
                            "damage_total": "{_dbf_damage_total}",
                            "damage_type": "fire",
                            "success_damage": "half",
                            "construct_save_disadvantage": False,
                            "half_cover_targets": "{_srd_area_half_cover}",
                            "three_quarters_cover_targets": "{_srd_area_three_quarters}",
                            "source_state_id": "{_dbf_record.caster}",
                            "spell_cast_id": "{source_id}",
                            "actual_spell_level": "{_dbf_record.slot_level}",
                            "spell_cast_owner": "{_dbf_record.caster}",
                        },
                        ["_spell_summary_rows", "_spell_detail_rows"],
                    ),
                    _loop("_spell_summary_rows", "_dbf_row", [_push("_dbf_summary", "_dbf_row")]),
                    _loop("_spell_detail_rows", "_dbf_row", [_push("_dbf_details", "_dbf_row")]),
                ],
            ),
            _set("state.delayed_blast_detonations.{source_id}.status", "'resolved'"),
            _set("state.delayed_blast_detonations.{source_id}.raw_roll", "_dbf_damage_roll"),
            _set("state.delayed_blast_detonations.{source_id}.damage_total", "_dbf_damage_total"),
        ],
    )


def _drain_tool():
    """A bounded FIFO also picks up genuine new endings caused by earlier detonations."""
    return _tool(
        "Resolve actual queued bead endings only at an outer action boundary.",
        [],
        [
            _set("_dbf_work", []),
            _when(
                "state.delayed_blast_detonation_ids != null",
                [
                    _set("_dbf_work", "state.delayed_blast_detonation_ids"),
                ],
            ),
            _set("_dbf_drained", []),
            _set("_dbf_drain_details", []),
            _loop(
                "_dbf_work",
                "_dbf_queued_id",
                [
                    _require("len(_dbf_work) <= 128", "Resolve at most 128 genuine bead endings."),
                    _when(
                        "state.delayed_blast_detonations[_dbf_queued_id].status == 'queued'",
                        [
                            {
                                "call": "resolve_srd_delayed_blast_fireball",
                                "params": {
                                    "source_id": "{_dbf_queued_id}",
                                },
                                "export_context": "*",
                            },
                            _loop(
                                "_dbf_details",
                                "_dbf_row",
                                [_push("_dbf_drain_details", "_dbf_row")],
                            ),
                        ],
                    ),
                    _push("_dbf_drained", "_dbf_queued_id"),
                    _loop(
                        "state.delayed_blast_detonation_ids",
                        "_dbf_new_id",
                        [
                            _when(
                                "_dbf_new_id not in _dbf_work", [_push("_dbf_work", "_dbf_new_id")]
                            ),
                        ],
                    ),
                ],
            ),
            _set("state.delayed_blast_detonation_ids", []),
            _set("state.has_delayed_blast_detonations", False),
        ],
    )


def _voluntary_tool():
    tool = _public(
        _tool(
            "End this actual retained bead's concentration and resolve its ending.",
            [_entity("caster_state")],
            [
                _require(
                    "caster_state.concentration_spell_id == 'delayed_blast_fireball' and "
                    "caster_state.delayed_blast_fireball_source_id != null",
                    "This actor has no current paid delayed bead.",
                ),
                _set("_dbf_chosen_source_id", "caster_state.delayed_blast_fireball_source_id"),
                _call(
                    "end_srd_concentration_node",
                    {
                        "source_state": "{_caster_state_id}",
                        "target_state": "{_caster_state_id}",
                    },
                ),
                {"call": DRAIN, "params": {}, "export_context": "*"},
                _require(
                    "state.delayed_blast_detonations[_dbf_chosen_source_id].status == 'resolved'",
                    "The actual ended bead did not resolve.",
                ),
                _set(
                    "_dbf_damage_roll",
                    "state.delayed_blast_detonations[_dbf_chosen_source_id].raw_roll",
                ),
                _set("_dbf_raw_damage", "_dbf_damage_roll.total"),
                _set(
                    "_dbf_damage_total",
                    "state.delayed_blast_detonations[_dbf_chosen_source_id].damage_total",
                ),
            ],
        ),
        "combat",
        "Delayed Blast Fireball",
        [
            ("Source", "_dbf_chosen_source_id"),
            ("Fire dice", "_dbf_damage_roll.results"),
            ("Raw damage", "_dbf_raw_damage"),
            ("After source penalty", "_dbf_damage_total"),
        ],
    )
    tool["metadata"]["result_card"]["details"] = {"path": "context._dbf_drain_details"}
    return tool


def extend_runtime(text):
    tools = _load_runtime(text)["tools"]
    point = _point_validator()
    # Its plane is source-qualified, not a public plane verdict.
    plane = next(p for p in point["parameters"] if p["name"] == "_dbf_chosen_plane")
    plane["model_hidden"] = True
    cover = sphere._point_cover(tools)
    names = {
        "collect_srd_sunburst_cover_targets": "collect_srd_delayed_blast_cover_targets",
        "qualify_srd_sunburst_target_cover": "qualify_srd_delayed_blast_target_cover",
    }
    cover = {
        names[name]: _messages(sphere._replace_names(tool, names)) for name, tool in cover.items()
    }
    collection = cover["collect_srd_delayed_blast_cover_targets"]
    collection["parameters"].append(_parameter("area_plane"))
    for effects in beam._lists(collection["effects"]):
        for effect in effects:
            if effect.get("set", {}).get("field") == "_breath_source_plane":
                effect["set"]["value"] = "area_plane"
    changed = {
        "qualify_srd_delayed_blast_point": point,
        "collect_srd_delayed_blast_targets": _collector(),
        "install_srd_delayed_blast_fireball": _installer(),
        "check_srd_delayed_blast_fireball_record": _rename(beam.retained._record_tool()),
        "retire_srd_delayed_blast_fireball_source": _retire_tool(),
        "cast_srd_delayed_blast_fireball": _cast_tool(),
        QUEUE: _queue_tool(),
        REFRESH: _refresh_tool(),
        "settle_srd_delayed_blast_concentration": _settle_tool(),
        "finish_srd_delayed_blast_removal": _finish_removal(),
        "grow_srd_delayed_blast_fireball": _growth_tool(),
        "resolve_srd_delayed_blast_fireball": _detonation_tool(),
        DRAIN: _drain_tool(),
        "detonate_srd_delayed_blast_fireball": _voluntary_tool(),
        **cover,
    }
    # Dragon's record/retirement leaves first become Sunbeam, then this exact source family.
    changed["check_srd_delayed_blast_fireball_record"] = _rename(
        beam._rename(beam.retained._record_tool())
    )
    return _replace_tools(
        text, {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    )


def extend_cast_parameters(text):
    tools, changed = _load_runtime(text)["tools"], {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(tools[name])
        if "delayed_blast_origin" not in {p["name"] for p in tool["parameters"]}:
            tool["parameters"].append(_intent())
            changed[name] = tool
    return _replace_tools(text, changed) if changed else text


def bind_runtime_consumers(text):
    """Root captures queue-only endings before packet clones and drains only safe outer owners."""
    text = extend_runtime(extend_cast_parameters(text))
    tools, changed = _load_runtime(text)["tools"], {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(tools[name])
        calls = {e.get("call") for s in beam._lists(tool["effects"]) for e in s}
        if "cast_srd_delayed_blast_fireball" not in calls and "cast_spell" not in calls:
            index = next(
                i
                for i, e in enumerate(tool["effects"])
                if e.get("set", {}).get("field") == "_card_slots_after"
            )
            tool["effects"].insert(
                index,
                _when(
                    "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
                    "_spell_definition.family == 'delayed_blast_fireball'",
                    [
                        {
                            "call": "cast_srd_delayed_blast_fireball",
                            "export_context": "*",
                            "params": {
                                "caster_state": "{_caster_state_id}",
                                "slot_level": "{_slot_level}",
                                "save_dc": "{_casting_profile.save_dc}",
                                "casting_ability": "{_casting_profile.ability}",
                                "concentration_save_modifier": (
                                    "{_casting_profile.concentration_save_modifier}"
                                ),
                                "spell_cast_id": "{_spell_source_cast_id}",
                                "delayed_blast_origin": "{delayed_blast_origin}",
                            },
                        },
                    ],
                ),
            )
            changed[name] = tool
    tool = deepcopy(tools["end_srd_concentration_node"])
    if QUEUE not in str(tool["effects"]):
        tool["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'delayed_blast_fireball' and "
                "source_state.delayed_blast_fireball_source_id != null",
                [
                    _call(
                        QUEUE,
                        {
                            "caster_state": "{_source_state_id}",
                            "source_id": "{source_state.delayed_blast_fireball_source_id}",
                        },
                    ),
                ],
            ),
        )
        changed["end_srd_concentration_node"] = tool
    for name in ("advance_srd_lasting_clock", "process_srd_turn_effects"):
        tool = deepcopy(tools[name])
        if REFRESH in str(tool["effects"]):
            continue
        if name == "advance_srd_lasting_clock":
            index = (
                next(
                    i
                    for i, e in enumerate(tool["effects"])
                    if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
                )
                + 1
            )
            tool["effects"].insert(
                index,
                _when(
                    "lasting_state.has_delayed_blast_fireball_sources == true",
                    [
                        _call(REFRESH, {"lasting_state": "{_lasting_state_id}"}),
                    ],
                ),
            )
        else:
            tool["effects"][:0] = [
                _when(
                    "lasting_state.has_delayed_blast_fireball_sources == true",
                    [_call(REFRESH, {"lasting_state": "{_lasting_state_id}"})],
                ),
                _when(
                    "lasting_state.has_delayed_blast_fireball_sources == true",
                    [
                        _when(
                            "boundary_phase == 'end' and combat.encounter_active == true",
                            [
                                _call(
                                    "grow_srd_delayed_blast_fireball",
                                    {
                                        "caster_state": "{boundary_actor}",
                                        "boundary_turn": "{boundary_turn}",
                                        "lasting_state": "{_lasting_state_id}",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ]
            tool["effects"].append(
                _when(
                    "lasting_state.has_delayed_blast_fireball_sources == true",
                    [
                        _call(REFRESH, {"lasting_state": "{_lasting_state_id}"}),
                    ],
                )
            )
        changed[name] = tool
    return _replace_tools(text, changed) if changed else text
