"""Hellish Rebuke responds to an authentic completed receiving packet through saved Reactions."""

from copy import deepcopy

if __package__:
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
    from .srd_timed_senses import _loop, _replace_tools
else:
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
    from srd_timed_senses import _loop, _replace_tools


PROFILE = {
    "family": "reaction_spell",
    "casting_time": "reaction",
    "base_level": 1,
    "range_kind": "distance",
    "range_feet": 60,
    "effect_kind": "hellish_rebuke",
    "effect_rounds": 0,
}
PUBLIC_TOOLS = {"begin_srd_hellish_rebuke"}
QUALIFY = "qualify_srd_hellish_rebuke_receiver"
OPEN = "open_srd_received_damage_reaction_window"
RESOLVE = "resolve_srd_hellish_rebuke"
SIGHT_EXPORTS = [
    "_illumination_target_can_see",
    "_illumination_target_same_plane",
    "_illumination_point_distance",
    "_illumination_point_origin",
]


def _push(field, value):
    return {"list_push": {"list": field, "value": value}}


def _lists(effects):
    yield effects
    for effect in effects:
        for branch in ("on_true", "on_false"):
            if branch in effect:
                yield from _lists(effect[branch])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _qualifier():
    """Current actor/scene/sight qualify the queued receipt; no final HP difference is a roll."""
    return _tool(
        "Qualify the actual living damage receiver and visible source creature for Hellish Rebuke.",
        [
            _entity("receiver_state"),
            _entity("source_state"),
            _parameter("event_id"),
            _parameter(
                "require_reaction", "boolean", required=False, default=True, model_hidden=True
            ),
        ],
        [
            _set("_hellish_eligible", False),
            _set("_illumination_target_can_see", False),
            _set("_illumination_target_same_plane", False),
            _set("_illumination_point_distance", None),
            _set("_illumination_point_origin", None),
            _set("_srd_area_targets", []),
            _set("_srd_area_half_cover", []),
            _set("_srd_area_three_quarters", []),
            _set("_hellish_receiver_scene", False),
            _set("_hellish_source_scene", False),
            _set("_hellish_rows", []),
            _set("_hellish_event", "state.pending_srd_actions[event_id]"),
            _when(
                "state.spatial_scene != null and state.spatial_scene.entities != null",
                [_set("_hellish_rows", "state.spatial_scene.entities")],
            ),
            _loop(
                "_hellish_rows",
                "_hellish_row",
                [
                    _when(
                        "_hellish_row.active == true and _hellish_row.metadata.is_object != true "
                        "and _hellish_row.metadata.creature_type != null",
                        [
                            _when(
                                "_hellish_row.id == _receiver_state_id or "
                                "_hellish_row.metadata.participant_ref == _receiver_state_id",
                                [_set("_hellish_receiver_scene", True)],
                            ),
                            _when(
                                "_hellish_row.id == _source_state_id or "
                                "_hellish_row.metadata.participant_ref == _source_state_id",
                                [_set("_hellish_source_scene", True)],
                            ),
                        ],
                    ),
                ],
            ),
            _set(
                "_hellish_receipt_valid",
                "_hellish_event.kind == 'received_damage' and "
                "_hellish_event.owner_kind == 'hellish_received_damage' and "
                "_hellish_event.received_damage.effective_damage > 0 and "
                "_hellish_event.actor_state_id == _source_state_id and "
                "_hellish_event.target_state_id == _receiver_state_id and "
                "_hellish_event.received_damage.created_tick == state.lasting_tick and "
                "(_hellish_event.turn_actor == null or (combat.turn_active == true and "
                "combat.active_actor == _hellish_event.turn_actor))",
            ),
            _set(
                "_hellish_reactable",
                "receiver_state.is_alive == true and receiver_state.hp > 0 and "
                "(require_reaction != true or receiver_state.reaction_available == true) and "
                "receiver_state.prepared_spells != null and "
                "'hellish_rebuke' in receiver_state.prepared_spells",
            ),
            _set(
                "_hellish_incapacitated",
                "'incapacitated' in receiver_state.conditions or "
                "'paralyzed' in receiver_state.conditions or "
                "'petrified' in receiver_state.conditions "
                "or 'stunned' in receiver_state.conditions or 'unconscious' in "
                "receiver_state.conditions",
            ),
            _when(
                "_hellish_receipt_valid == true and _hellish_reactable == true and "
                "_hellish_incapacitated != true and _hellish_receiver_scene == true and "
                "_hellish_source_scene == true",
                [
                    _call(
                        "resolve_srd_illumination_target_sight",
                        {
                            "observer_state": "{_receiver_state_id}",
                            "target_state": "{_source_state_id}",
                        },
                        SIGHT_EXPORTS,
                    ),
                    _set(
                        "_hellish_eligible",
                        "_illumination_target_can_see == true and "
                        "_illumination_target_same_plane == true and "
                        "_illumination_point_distance <= 60",
                    ),
                    _when(
                        "_hellish_eligible == true",
                        [
                            _set("_hellish_targets", ["_source_state_id"]),
                            _call(
                                "collect_srd_spell_area_cover_targets",
                                {
                                    "source_state": "{_receiver_state_id}",
                                    "targets": "{_hellish_targets}",
                                    "origin": "{_illumination_point_origin}",
                                },
                                [
                                    "_srd_area_targets",
                                    "_srd_area_half_cover",
                                    "_srd_area_three_quarters",
                                ],
                            ),
                            _set("_hellish_eligible", "_source_state_id in _srd_area_targets"),
                        ],
                    ),
                ],
            ),
        ],
    )


def _opener():
    """Only outer settled boundaries open FIFO events; packet producers never call this tool."""
    return _tool(
        "Open the first eligible completed receiving event after its actual outer action settles.",
        [],
        [
            _set("_opened_received_damage_id", None),
            _set("_received_queue_blocked", False),
            _set("_received_queue_kept", []),
            _set("_received_queue", "state.pending_srd_received_damage_ids"),
            _when(
                "_received_queue != null and len(_received_queue) > 0 and "
                "(state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0)",
                [
                    _loop(
                        "_received_queue",
                        "_received_event_id",
                        [
                            _require(
                                "_received_event_id in state.pending_srd_actions",
                                "The receiving queue needs its actual saved event.",
                            ),
                            _set(
                                "_received_event", "state.pending_srd_actions[_received_event_id]"
                            ),
                            _when(
                                "_received_event.status == 'queued'",
                                [
                                    _set("_received_ready", False),
                                    _when(
                                        "_opened_received_damage_id == null and "
                                        "_received_queue_blocked != true",
                                        [
                                            _set(
                                                "_received_ready",
                                                "_received_event.parent_action_id == null or "
                                                "_received_event.parent_action_id not in "
                                                "state.pending_srd_actions or "
                                                "state.pending_srd_actions["
                                                "_received_event.parent_action_id].status == "
                                                "'completed'",
                                            ),
                                            _when(
                                                "_received_ready != true",
                                                [
                                                    _set(
                                                        "_received_parent",
                                                        "state.pending_srd_actions["
                                                        "_received_event.parent_action_id]",
                                                    ),
                                                    _set(
                                                        "_received_ready",
                                                        "_received_parent.kind == "
                                                        "'freezing_sphere' "
                                                        "and _received_parent.owner_kind == "
                                                        "'freezing_sphere' and "
                                                        "_received_parent.status == 'resolved'",
                                                    ),
                                                ],
                                            ),
                                            _when(
                                                "_received_ready != true",
                                                [_set("_received_queue_blocked", True)],
                                            ),
                                            _when(
                                                "_received_ready == true",
                                                [
                                                    _call(
                                                        QUALIFY,
                                                        {
                                                            "receiver_state": (
                                                                "{_received_event.target_state_id}"
                                                            ),
                                                            "source_state": (
                                                                "{_received_event.actor_state_id}"
                                                            ),
                                                            "event_id": "{_received_event_id}",
                                                        },
                                                        ["_hellish_eligible"],
                                                    ),
                                                    {
                                                        "condition": "_hellish_eligible == true",
                                                        "on_true": [
                                                            _set(
                                                                "_opened_received_damage_id",
                                                                "_received_event_id",
                                                            ),
                                                            _set(
                                                                "state.pending_srd_actions.{_received_event_id}.status",
                                                                "'open'",
                                                            ),
                                                            _set(
                                                                "state.pending_srd_actions.{_received_event_id}.stage",
                                                                "'reaction'",
                                                            ),
                                                            _set(
                                                                "state.pending_srd_action_stack",
                                                                ["_received_event_id"],
                                                            ),
                                                        ],
                                                        "on_false": [
                                                            _set(
                                                                "state.pending_srd_actions.{_received_event_id}.status",
                                                                "'ineligible'",
                                                            ),
                                                        ],
                                                    },
                                                ],
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "state.pending_srd_actions["
                                        "_received_event_id].status == 'queued'",
                                        [_push("_received_queue_kept", "_received_event_id")],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set("state.pending_srd_received_damage_ids", "_received_queue_kept"),
                ],
            ),
        ],
    )


def _resolver():
    return _tool(
        "Resolve the genuine paid Hellish Rebuke with one Dexterity save and one Fire packet.",
        [
            _entity("caster_state"),
            _parameter("reaction_trigger_id"),
            _parameter("save_dc", "integer", minimum=1, maximum=40),
            _parameter("actual_spell_level", "integer", minimum=1, maximum=9),
            _parameter("spell_cast_id", required=False, default=""),
        ],
        [
            _set("_hellish_event", "state.pending_srd_actions[reaction_trigger_id]"),
            _require(
                "_hellish_event.hellish_rebuke_resolved != true and "
                "_hellish_event.target_state_id == _caster_state_id",
                "Hellish Rebuke resolves this receiver's completed damage only once.",
            ),
            _call(
                QUALIFY,
                {
                    "receiver_state": "{_caster_state_id}",
                    "source_state": "{_hellish_event.actor_state_id}",
                    "event_id": "{reaction_trigger_id}",
                    "require_reaction": False,
                },
                [
                    "_hellish_eligible",
                    *SIGHT_EXPORTS,
                    "_srd_area_targets",
                    "_srd_area_half_cover",
                    "_srd_area_three_quarters",
                ],
            ),
            # Payment has already consumed the Reaction. The pre-cost validator owns availability.
            _require(
                "_hellish_eligible == true",
                "Hellish Rebuke needs the actual visible damaging creature within 60 feet.",
            ),
            _set("_hellish_dice_count", "actual_spell_level + 1"),
            {
                "dice": {"expression": "{_hellish_dice_count}d10"},
                "store_as": "_hellish_damage_roll",
            },
            _set("_hellish_damage_total", "_hellish_damage_roll.total"),
            _when(
                "caster_state.has_actor_spell_modifiers == true",
                [
                    _call(
                        "modify_srd_actor_damage_roll",
                        {"actor_state": "{_caster_state_id}", "amount": "{_hellish_damage_total}"},
                        ["_actor_modified_damage"],
                    ),
                    _set("_hellish_damage_total", "_actor_modified_damage"),
                ],
            ),
            _call(
                "resolve_spell_area_save_damage_target",
                {
                    "spell": "hellish_rebuke",
                    "target": "{_hellish_event.actor_state_id}",
                    "save_ability": "dexterity",
                    "spell_save_dc": "{save_dc}",
                    "damage_total": "{_hellish_damage_total}",
                    "damage_type": "fire",
                    "success_damage": "half",
                    "construct_save_disadvantage": False,
                    "half_cover_targets": "{_srd_area_half_cover}",
                    "three_quarters_cover_targets": "{_srd_area_three_quarters}",
                    "source_state_id": "{_caster_state_id}",
                    "spell_cast_id": "{spell_cast_id}",
                    "actual_spell_level": "{actual_spell_level}",
                    "spell_cast_owner": "{_caster_state_id}",
                },
                ["_spell_summary_rows", "_spell_detail_rows"],
            ),
            _push(
                "_spell_detail_rows",
                {
                    "label": "'Fire damage dice'",
                    "value": "_hellish_damage_roll.results",
                },
            ),
            _set("state.pending_srd_actions.{reaction_trigger_id}.hellish_rebuke_resolved", True),
        ],
    )


def _begin():
    result = _tool(
        "Begin Hellish Rebuke against the actual saved receiving event; permit Counterspell.",
        [
            _entity("caster_state"),
            _parameter("caster", resolve="participant", required=False, default=None),
            _parameter("reaction_trigger_id"),
            _parameter("slot_resource", required=False, default=None),
            _parameter("verbal_component_available", "boolean"),
            _parameter("somatic_component_available", "boolean"),
        ],
        [
            _call(
                "validate_srd_reaction_spell_trigger",
                {
                    "caster_state": "{_caster_state_id}",
                    "spell": "hellish_rebuke",
                    "reaction_trigger_id": "{reaction_trigger_id}",
                },
                SIGHT_EXPORTS,
            ),
            _set("_hellish_event", "state.pending_srd_actions[reaction_trigger_id]"),
            _when("caster == null", [_set("_caster_id", None)]),
            _set("_hellish_slot", None),
            _when("slot_resource != null", [_set("_hellish_slot", "slot_resource")]),
            _when(
                "caster != null and slot_resource == null",
                [_set("_hellish_slot", "'spell_slot_1'")],
            ),
            _call(
                "begin_srd_spell_action",
                {
                    "caster_state": "{_caster_state_id}",
                    "caster": "{_caster_id}",
                    "spell": "hellish_rebuke",
                    "reaction_trigger_id": "{reaction_trigger_id}",
                    "target": "{_hellish_event.actor_state_id}",
                    "slot_resource": "{_hellish_slot}",
                    "target_distance": "{_illumination_point_distance}",
                    "caster_can_see_target": "{_illumination_target_can_see}",
                    "verbal_component_available": "{verbal_component_available}",
                    "somatic_component_available": "{somatic_component_available}",
                },
                ["_pending_action_id"],
            ),
            _set("_pending_card_title", "'Hellish Rebuke'"),
            _set(
                "_pending_card_summary",
                [
                    {"label": "'Saved cast'", "value": "_pending_action_id"},
                    {"label": "'Damage event'", "value": "reaction_trigger_id"},
                ],
            ),
            _set(
                "_pending_card_details",
                [
                    {
                        "label": "'Casting'",
                        "value": "'Reaction spent; resolve Counterspell before Fire.'",
                    },
                ],
            ),
        ],
    )
    result["parameters"] = [p for p in result["parameters"] if p["name"] != "lasting_state"]
    result["metadata"] = {
        "category": "combat",
        "costs_action": False,
        "result_card": {
            "version": 1,
            "footprint": "compact",
            "title": {"path": "context._pending_card_title"},
            "summary": {"path": "context._pending_card_summary"},
            "details_label": "Reaction window",
            "details": {"path": "context._pending_card_details"},
        },
    }
    return result


def extend_runtime(text):
    text = text.replace("\r\n", "\n")
    old = _load_runtime(text)["tools"]
    required = {
        "resolve_srd_illumination_target_sight",
        "collect_srd_spell_area_cover_targets",
        "resolve_spell_area_save_damage_target",
        "begin_srd_spell_action",
    }
    if missing := required - old.keys():
        raise ValueError("Hellish Rebuke needs actual owners: " + ", ".join(sorted(missing)))
    changed = {
        QUALIFY: _qualifier(),
        OPEN: _opener(),
        RESOLVE: _resolver(),
        "begin_srd_hellish_rebuke": _begin(),
    }
    return _replace_tools(text, {n: t for n, t in changed.items() if old.get(n) != t})


def bind_runtime_consumers(text):
    """Join after Reaction construction; receiving packets have no edge back to this binder."""
    text = extend_runtime(text)
    tools = _load_runtime(text)["tools"]
    changed = {}
    validate = deepcopy(tools["validate_srd_reaction_spell_trigger"])
    if QUALIFY not in str(validate["effects"]):
        guard = _tool(
            "",
            [],
            [
                _when(
                    "spell == 'hellish_rebuke'",
                    [
                        _require(
                            "_reaction_trigger.kind == 'received_damage' and "
                            "_reaction_trigger.owner_kind == 'hellish_received_damage' and "
                            "_reaction_trigger.target_state_id == _caster_state_id and "
                            "_reaction_trigger.hellish_rebuke_resolved != true",
                            "Hellish Rebuke needs this caster's actual completed receiving event.",
                        ),
                        _set("_hellish_needs_reaction", True),
                        _when(
                            "caster_state.pending_srd_completion_id != null and "
                            "caster_state.pending_srd_completion_id in "
                            "state.pending_srd_actions",
                            [
                                _set(
                                    "_hellish_completing",
                                    "state.pending_srd_actions["
                                    "caster_state.pending_srd_completion_id]",
                                ),
                                _when(
                                    "_hellish_completing.kind == 'spell_cast' and "
                                    "_hellish_completing.spell == 'hellish_rebuke' and "
                                    "_hellish_completing.actor_state_id == _caster_state_id and "
                                    "_hellish_completing.parameters.reaction_trigger_id == "
                                    "reaction_trigger_id",
                                    [_set("_hellish_needs_reaction", False)],
                                ),
                            ],
                        ),
                        _call(
                            QUALIFY,
                            {
                                "receiver_state": "{_caster_state_id}",
                                "source_state": "{_reaction_trigger.actor_state_id}",
                                "event_id": "{reaction_trigger_id}",
                                "require_reaction": "{_hellish_needs_reaction}",
                            },
                            ["_hellish_eligible", *SIGHT_EXPORTS],
                        ),
                        _require(
                            "_hellish_eligible == true",
                            "Hellish Rebuke needs a live Reaction and visible creature "
                            "within 60 feet.",
                        ),
                    ],
                )
            ],
        )["effects"][0]
        validate["effects"].append(guard)
        changed["validate_srd_reaction_spell_trigger"] = validate
    cast = deepcopy(tools["cast_spell_reaction"])
    existing = {p["name"] for p in cast["parameters"]}
    if "actual_spell_level" not in existing:
        cast["parameters"].append(
            _parameter(
                "actual_spell_level",
                "integer",
                required=False,
                default=1,
                minimum=1,
                maximum=9,
                model_hidden=True,
            )
        )
    if "spell_cast_id" not in existing:
        cast["parameters"].append(
            _parameter(
                "spell_cast_id",
                required=False,
                default="",
                model_hidden=True,
            )
        )
    if RESOLVE not in str(cast["effects"]):
        card_guarded = 0
        for sequence in _lists(cast["effects"]):
            for effect in sequence:
                if effect.get("condition", "").startswith("spell != 'feather_fall'"):
                    effect["condition"] += " and spell != 'hellish_rebuke'"
                    card_guarded += 1
        # The admitted earlier owner has the same card writer as three top-level effects.
        anchors = [
            i
            for i, e in enumerate(cast["effects"])
            if e.get("call") == "record_srd_spell_target_result"
            and e["params"].get("brief") == "{_reaction_outcome_text}"
        ]
        if anchors:
            if len(anchors) != 1 or card_guarded:
                raise ValueError("Hellish Rebuke needs one actual generic Reaction card boundary.")
            index = anchors[0]
            suffix = cast["effects"][index : index + 3]
            if len(suffix) != 3 or any("list_push" not in e for e in suffix[1:]):
                raise ValueError("Hellish Rebuke needs the actual target/card row collectors.")
            cast["effects"][index : index + 3] = [_when("spell != 'hellish_rebuke'", suffix)]
            card_guarded = 1
        if card_guarded != 1:
            raise ValueError("Hellish Rebuke needs its owning Reaction card boundary.")
        cast["effects"].append(
            _when(
                "spell == 'hellish_rebuke'",
                [
                    _call(
                        RESOLVE,
                        {
                            "caster_state": "{_caster_state_id}",
                            "reaction_trigger_id": "{reaction_trigger_id}",
                            "save_dc": "{save_dc}",
                            "actual_spell_level": "{actual_spell_level}",
                            "spell_cast_id": "{spell_cast_id}",
                        },
                        ["_spell_summary_rows", "_spell_detail_rows", "_hellish_damage_roll"],
                    ),
                ],
            )
        )
        changed["cast_spell_reaction"] = cast
    complete = deepcopy(tools["complete_srd_pending_action"])
    if OPEN not in str(complete["effects"]):
        found = 0
        for sequence in _lists(complete["effects"]):
            for effect in sequence:
                if any(
                    e.get("set", {}).get("field") == "lasting_state.pending_srd_actions"
                    and e["set"]["value"] == {}
                    for e in effect.get("on_true", [])
                ):
                    effect["condition"] += (
                        " and (lasting_state.pending_srd_received_damage_ids == null or "
                        "len(lasting_state.pending_srd_received_damage_ids) == 0)"
                    )
                    found += 1
        if found != 1:
            raise ValueError("Hellish needs the exact pending owner's empty-registry cleanup.")
        complete["effects"].append(
            _when(
                "_pending_action.kind == 'received_damage'",
                [
                    _set("_pending_card_title", "'Received damage'"),
                    _set(
                        "_pending_card_summary",
                        [
                            {"label": "'Damage source'", "value": "_pending_action.actor_state_id"},
                            {
                                "label": "'Completed damage'",
                                "value": "_pending_action.received_damage.effective_damage",
                            },
                        ],
                    ),
                    _set(
                        "_pending_card_details",
                        [
                            {
                                "label": "'Reaction opportunity'",
                                "value": "'Closed; original damage already applied.'",
                            },
                        ],
                    ),
                ],
            )
        )
        complete["effects"].append(
            _when(
                "lasting_state.pending_srd_received_damage_ids != null and "
                "len(lasting_state.pending_srd_received_damage_ids) > 0",
                [_call(OPEN, {}, ["_opened_received_damage_id"])],
            )
        )
        changed["complete_srd_pending_action"] = complete
    # Only settled public outer owners drain. Never append this edge to a damage packet.
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "resolve_combat_attack",
        "resolve_srd_creature_attack",
        "begin_combat_turn",
        "choose_srd_storm_lightning_targets",
        "resolve_srd_freezing_sphere_globe",
    ):
        if name not in tools or OPEN in str(tools[name]["effects"]):
            continue
        owner = deepcopy(tools[name])
        if name in {"cast_spell", "cast_spell_outside_combat"}:
            for sequence in _lists(owner["effects"]):
                for effect in sequence:
                    if effect.get("call") == "cast_spell_reaction":
                        effect["params"].update(
                            actual_spell_level="{_slot_level}",
                            spell_cast_id="{_spell_source_cast_id}",
                        )
        state_name = (
            "lasting_state"
            if any(p["name"] == "lasting_state" for p in owner["parameters"])
            else "state"
        )
        owner["effects"].append(
            _when(
                f"{state_name}.pending_srd_received_damage_ids != null and "
                f"len({state_name}.pending_srd_received_damage_ids) > 0",
                [_call(OPEN, {}, ["_opened_received_damage_id"])],
            )
        )
        changed[name] = owner
    return _replace_tools(text, changed) if changed else text
