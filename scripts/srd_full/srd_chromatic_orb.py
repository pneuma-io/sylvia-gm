"""Chromatic Orb retains its actual matching dice and leaps in the original pending action."""

from copy import deepcopy

if __package__:
    from .srd_reaction_spells import _allocate, _capture_names, _copy_value, _save_parameters
    from .srd_scene_relations import _tool_scene
    from .srd_spell_light_damage_riders import _effect_lists
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
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_reaction_spells import _allocate, _capture_names, _copy_value, _save_parameters
    from srd_scene_relations import _tool_scene
    from srd_spell_light_damage_riders import _effect_lists
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
    from srd_timed_senses import _loop, _push, _replace_tools


PUBLIC_TOOLS = {"choose_srd_chromatic_orb_leap"}
LEAF = "cast_spell_attack_damage"
ATTACK = "resolve_srd_chromatic_orb_leap"
FINISH = "complete_srd_spell_attack_hit"
COMPLETE = "complete_srd_chromatic_orb_attack"
RECORD = "retain_srd_chromatic_orb_choice"
CHOICE = "chromatic_orb_choice"
CAST_ID = "orb_cast_id"


def _source_effects():
    """Only the committed paid scalar call creates an original casting receipt."""
    effects = [
        _set("_orb_source", None),
        _set("_orb_pending_action_id", "''"),
        _set("_orb_same_d8", False),
        _when(
            "spell == 'chromatic_orb'",
            [
                _require(
                    "orb_cast_id != '' and caster_state.last_spell_source_cast_id == orb_cast_id "
                    "and caster_state.last_spell_source_spell == 'chromatic_orb'",
                    "Chromatic Orb needs the identity of its actual paid casting.",
                ),
                _set(
                    "_orb_source",
                    {
                        "cast_id": "orb_cast_id",
                        "caster": "_caster_state_id",
                        "slot_level": "slot_level",
                        "damage_type": "selected_damage_type",
                        "hops": 0,
                        "visited": ["_target_id"],
                        "last_target": "_target_id",
                        "created_tick": None,
                        "turn_sequence": None,
                        "parameters": {},
                    },
                ),
                _when(
                    "caster_state.pending_srd_completion_id != null",
                    [_set("_orb_pending_action_id", "caster_state.pending_srd_completion_id")],
                ),
            ],
        ),
    ]
    return effects


def _match_effects():
    """Match genuine raw d8 faces before the nested receiving packet changes local results."""
    return _when(
        "spell == 'chromatic_orb'",
        [
            _set("_orb_same_d8", False),
            _set("_orb_d8_seen", []),
            _set("_orb_d8_faces", "_spell_damage.results"),
            _loop(
                "_orb_d8_faces",
                "_orb_face",
                [
                    _when("_orb_face in _orb_d8_seen", [_set("_orb_same_d8", True)]),
                    _push("_orb_d8_seen", "_orb_face"),
                ],
            ),
        ],
    )


def _sink_call(parameters):
    saved = [
        p
        for p in parameters
        if p["name"] not in {"caster_state", "target", "lasting_state", CAST_ID}
    ]
    path = "lasting_state.pending_srd_actions.{_orb_pending_action_id}.orb_source."
    effect = _when(
        "spell == 'chromatic_orb' and _orb_source != null and _orb_same_d8 == true "
        "and _orb_source.hops < _orb_source.slot_level",
        [
            _when(
                "_orb_pending_action_id == ''",
                [
                    *_allocate("chromatic_orb", "_caster_state_id", "_target_id"),
                    _set("_orb_pending_action_id", "_pending_action_id"),
                ],
            ),
            _set(
                "lasting_state.pending_srd_actions.{_orb_pending_action_id}.orb_source",
                "_orb_source",
            ),
            _when(
                "_orb_source.hops == 0",
                [
                    *_copy_value(path + "created_tick", "lasting_state.lasting_tick"),
                    *_copy_value(path + "turn_sequence", "combat.turn_sequence"),
                    *[
                        effect
                        for p in saved
                        for effect in _copy_value(path + "parameters." + p["name"], p["name"])
                    ],
                ],
            ),
            _call(
                RECORD,
                {
                    "caster_state": "{_caster_state_id}",
                    "target": "{_target_id}",
                    "pending_action_id": "{_orb_pending_action_id}",
                },
                ["_pending_action_id"],
            ),
            _call(
                "record_srd_spell_target_result",
                {
                    "target": "{_target_id}",
                    "brief": "Matching d8 results: a leap is available",
                    "detail": "Choose a different target within 30 feet, or decline the leap. "
                    "Pending action {_pending_action_id}.",
                },
                ["_spell_target_summary", "_spell_target_detail"],
            ),
            _push("_spell_summary_rows", "_spell_target_summary"),
            _push("_spell_detail_rows", "_spell_target_detail"),
        ],
    )
    return _tool("Retain the actual Orb choice.", [], [effect])["effects"][0]


def _record_tool():
    """Retain only a real matching attack, without calling another attack from damage."""
    return _tool(
        "Retain Chromatic Orb's earned next-target choice in its original action.",
        [
            _entity("caster_state"),
            _entity("target"),
            _parameter("pending_action_id"),
            _parameter("lasting_state", resolve="entity", required=False, default="state"),
        ],
        [
            _set("_orb_source", "lasting_state.pending_srd_actions[pending_action_id].orb_source"),
            _require(
                "_orb_source.caster == _caster_state_id and _orb_source.cast_id != '' and "
                "_orb_source.slot_level >= 1 and _orb_source.slot_level <= 9",
                "Retain only the original authenticated Chromatic Orb casting.",
            ),
            _set("_pending_action_id", "pending_action_id"),
            _when(
                "_orb_source.hops < _orb_source.slot_level",
                [
                    _set(
                        "lasting_state.pending_srd_actions.{_pending_action_id}.orb_source.last_target",
                        "_target_id",
                    ),
                    _set(
                        "lasting_state.pending_srd_actions.{_pending_action_id}.kind",
                        "'spell_attack'",
                    ),
                    _set(
                        "lasting_state.pending_srd_actions.{_pending_action_id}.owner_kind",
                        "'chromatic_orb'",
                    ),
                    _set(
                        "lasting_state.pending_srd_actions.{_pending_action_id}.stage", repr(CHOICE)
                    ),
                    _set("lasting_state.pending_srd_actions.{_pending_action_id}.status", "'open'"),
                    _set(
                        "lasting_state.pending_srd_actions.{_pending_action_id}.spell",
                        "'chromatic_orb'",
                    ),
                    _set(
                        "lasting_state.pending_srd_actions.{_pending_action_id}.target_state_id",
                        "_target_id",
                    ),
                ],
            ),
        ],
    )


def bind_paid_source(text):
    """Bind the original paid call and raw-dice boundary before Reaction copies them."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(tools[name])
        for effects in _effect_lists(tool["effects"]):
            for index in range(len(effects) - 1, -1, -1):
                effect = effects[index]
                if effect.get("call") != LEAF or CAST_ID in effect.get("params", {}):
                    continue
                effects.insert(
                    index,
                    _when(
                        "spell == 'chromatic_orb' and _spell_source_cast_id == ''",
                        [
                            _call(
                                "begin_srd_spell_source_instance",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "spell": "chromatic_orb",
                                    "slot_level": "{_slot_level}",
                                },
                                ["_spell_source_cast_id"],
                            )
                        ],
                    ),
                )
                effect["params"][CAST_ID] = "{_spell_source_cast_id}"
                effect.setdefault("export_context", {})["_orb_pending_action_id"] = (
                    "_orb_pending_action_id"
                )
        changed[name] = tool
    leaf = deepcopy(tools[LEAF])
    if CAST_ID not in {p["name"] for p in leaf["parameters"]}:
        leaf["parameters"].append(
            _parameter(CAST_ID, required=False, default="", model_hidden=True)
        )
        leaf["effects"][:0] = _source_effects()
        found = 0
        for effects in _effect_lists(leaf["effects"]):
            for index in range(len(effects) - 1, -1, -1):
                if "dice" in effects[index] and effects[index].get("store_as") == "_spell_damage":
                    effects.insert(index + 1, _match_effects())
                    found += 1
        if found != 1:
            raise ValueError("Chromatic Orb needs the scalar's actual original damage Dice.")
    # Reaction's existing capture discovers these source variables from this genuine suffix.
    leaf["effects"] = _clear_sink(leaf["effects"]) + [_sink_call(leaf["parameters"])]
    changed[LEAF] = leaf
    return _replace_tools(text, {k: v for k, v in changed.items() if tools.get(k) != v})


def _clear_sink(effects):
    """Remove only this owner's previous sink when refreshing the actual final scalar copies."""
    result = deepcopy(effects)
    for children in _effect_lists(result):
        children[:] = [
            effect
            for effect in children
            if not effect.get("condition", "").startswith(
                "spell == 'chromatic_orb' and _orb_source != null"
            )
        ]
    return result


def _leap_tools(leaf, finish):
    """Reuse the actual one-roll prefix and saved hit suffix in the existing pending frame."""
    split = (
        next(
            i
            for i, e in enumerate(leaf["effects"])
            if e.get("set", {}).get("field") == "_spell_critical_hit"
        )
        + 1
    )
    prefix = deepcopy(leaf["effects"][:split])
    prefix = [
        e
        for e in prefix
        if not (
            e.get("set", {}).get("field")
            in {"_orb_source", "_orb_pending_action_id", "_orb_same_d8"}
            or e.get("condition") == "spell == 'chromatic_orb'"
            and "_orb_source" in str(e)
        )
    ]
    suffix_start = (
        next(
            i
            for i, e in enumerate(finish["effects"])
            if e.get("set", {}).get("field") == "_spell_critical_hit"
        )
        + 1
    )
    suffix = _clear_sink(deepcopy(finish["effects"][suffix_start:]))
    suffix = [
        e
        for e in suffix
        if e.get("set", {}).get("field")
        != ("lasting_state.pending_srd_actions.{pending_action_id}.stage")
    ]
    names = sorted(
        set(_capture_names(prefix, suffix, {}))
        | {
            "_orb_source",
            "_orb_same_d8",
            "_orb_pending_action_id",
            "_spell_attack",
            "_spell_condition_critical_hit",
        }
    )
    saved = deepcopy(_save_parameters(leaf["parameters"]))
    for effects in _effect_lists(saved):
        for effect in effects:
            field = effect.get("set", {}).get("field", "")
            if ".parameters." in field:
                effect["set"]["field"] = field.replace(".parameters.", ".spell_attack_parameters.")
    attack = deepcopy(leaf)
    for parameter in attack["parameters"]:
        if parameter["name"] == "target_distance":
            parameter["type"] = "number"
    attack["parameters"] += [_parameter("pending_action_id")]
    attack["pre_conditions"] = [
        condition
        for condition in attack["pre_conditions"]
        if not any(
            token in condition["condition"]
            for token in (
                "prepared_spells",
                "resources[slot_resource]",
                "target_distance <=",
            )
        )
    ]
    attack["effects"] = [
        _set("_orb_source", "lasting_state.pending_srd_actions[pending_action_id].orb_source"),
        _set("_orb_pending_action_id", "pending_action_id"),
        _set("_orb_same_d8", False),
        _set("_pending_action_id", "pending_action_id"),
        _require(
            "lasting_state.pending_srd_actions[pending_action_id].owner_kind == 'chromatic_orb' "
            "and _orb_source.caster == _caster_state_id and _orb_source.cast_id == orb_cast_id",
            "Resolve only the actual leap selected in this original casting.",
        ),
        *prefix,
        *[
            _set(
                "lasting_state.pending_srd_actions.{pending_action_id}.stage",
                "'spell_attack_reaction'",
            ),
            _set(
                "lasting_state.pending_srd_actions.{pending_action_id}.target_state_id",
                "_target_id",
            ),
            _set(
                "lasting_state.pending_srd_actions.{pending_action_id}.attack_outcome",
                "'miss'",
            ),
            _when(
                "_spell_attack_outcome == 'success'",
                [
                    _set(
                        "lasting_state.pending_srd_actions.{pending_action_id}.attack_outcome",
                        "'hit'",
                    ),
                    _when(
                        "_spell_critical_hit == true",
                        [
                            _set(
                                "lasting_state.pending_srd_actions.{pending_action_id}.attack_outcome",
                                "'critical_hit'",
                            )
                        ],
                    ),
                ],
            ),
            _set(
                "lasting_state.pending_srd_actions.{pending_action_id}.spell_attack_parameters",
                {},
            ),
            _set("lasting_state.pending_srd_actions.{pending_action_id}.spell_attack_context", {}),
            *saved,
            *[
                e
                for name in names
                for e in _copy_value(
                    "lasting_state.pending_srd_actions.{pending_action_id}.spell_attack_context."
                    + name,
                    name,
                )
            ],
            _set("_spell_hit_window_deferred", "_spell_attack_outcome == 'success'"),
        ],
    ]
    attack["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    return {
        ATTACK: attack,
        COMPLETE: _complete_tool(finish),
        "choose_srd_chromatic_orb_leap": _choice_tool(attack),
    }


def _complete_tool(hit):
    """Finish the actual saved leap hit, or close its explicit decline, through native cleanup."""
    return _tool(
        "Complete this original Chromatic Orb leap after its actual defensive Reaction.",
        [
            _parameter("pending_action_id"),
            _parameter("lasting_state", resolve="entity", required=False, default="state"),
        ],
        [
            _set("_orb_event", "lasting_state.pending_srd_actions[pending_action_id]"),
            _require("_orb_event.owner_kind == 'chromatic_orb'", "This is not an Orb leap action."),
            _set("_spell_card_summary", []),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _set("_spell_attack", None),
            _set("_spell_attack_outcome", "''"),
            _set("_spell_attack_kind", "''"),
            _when(
                "_orb_event.stage == 'spell_attack_reaction'",
                [
                    _call(
                        FINISH,
                        {
                            **{
                                p["name"]: "{_orb_event.spell_attack_parameters." + p["name"] + "}"
                                for p in hit["parameters"]
                                if p["name"] not in {"lasting_state", "pending_action_id"}
                            },
                            "pending_action_id": "{pending_action_id}",
                        },
                        [
                            "_spell_summary_rows",
                            "_spell_detail_rows",
                            "_spell_attack",
                            "_spell_attack_outcome",
                            "_spell_attack_kind",
                        ],
                    ),
                    _set("_spell_card_summary", "_spell_summary_rows"),
                ],
            ),
            _when(
                "_orb_event.stage == 'chromatic_orb_declined'",
                [
                    _set("_spell_summary_rows", [{"label": "'Leap'", "value": "'Declined'"}]),
                    _set("_spell_card_summary", "_spell_summary_rows"),
                    _set(
                        "lasting_state.pending_srd_actions.{pending_action_id}.stage",
                        "'spell_attack_resolved'",
                    ),
                ],
            ),
        ],
    )


def _choice_tool(attack):
    """Select a new target from honest GM facts or admitted scene geometry; null declines."""
    facts = [
        _parameter("jump_distance", "number", required=False, default=None, minimum=0),
        _parameter("target_distance", "number", required=False, default=None, minimum=0),
        *[
            _parameter(name, "boolean", required=False, default=value)
            for name, value in (
                ("caster_can_see_target", True),
                ("target_can_see_caster", True),
                ("enemy_within_5_feet", False),
                ("close_enemy_can_see_caster", True),
                ("close_enemy_incapacitated", False),
            )
        ],
        _parameter(
            "cover",
            "enum",
            required=False,
            default="none",
            enum_values=["none", "half", "three_quarters", "total"],
        ),
    ]
    params = {
        p["name"]: "{_orb_source.parameters." + p["name"] + "}"
        for p in attack["parameters"]
        if p["name"]
        not in {
            "lasting_state",
            "caster_state",
            "target",
            "orb_source",
            "pending_action_id",
            CAST_ID,
        }
    }
    params.update({p["name"]: "{" + p["name"] + "}" for p in facts if p["name"] != "jump_distance"})
    params.update(
        {
            "caster_state": "{_caster_state_id}",
            "target": "{_target_id}",
            "advantage": False,
            "disadvantage": False,
            "pending_action_id": "{pending_action_id}",
            CAST_ID: "{_orb_source.cast_id}",
        }
    )
    tool = _tool_scene(
        "Choose or decline Chromatic Orb's earned leap in its original paid casting.",
        [
            _entity("caster_state"),
            _parameter("pending_action_id"),
            {**_entity("target"), "required": False, "default": None},
            _parameter("lasting_state", resolve="entity", required=False, default="state"),
            *facts,
        ],
        [
            _require(
                "lasting_state.pending_srd_action_stack != null and "
                "len(lasting_state.pending_srd_action_stack) > 0 and "
                "lasting_state.pending_srd_action_stack[-1] == pending_action_id",
                "Choose only the top original pending Orb action.",
            ),
            _set("_orb_event", "lasting_state.pending_srd_actions[pending_action_id]"),
            _require(
                "_orb_event.owner_kind == 'chromatic_orb' and "
                "_orb_event.stage == 'chromatic_orb_choice' and _orb_event.status == 'open' "
                "and _orb_event.actor_state_id == _caster_state_id",
                "This casting has no earned Orb leap choice.",
            ),
            _set("_orb_source", "_orb_event.orb_source"),
            _require(
                "_orb_source.caster == _caster_state_id and _orb_source.cast_id != ''",
                "The choice must belong to this actual original caster.",
            ),
            _set(
                "_orb_choice_current",
                "_orb_source.created_tick == lasting_state.lasting_tick and "
                "(_orb_source.turn_sequence == null or "
                "_orb_source.turn_sequence == combat.turn_sequence)",
            ),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _when(
                "target == null or _orb_choice_current != true",
                [
                    _set(
                        "lasting_state.pending_srd_actions.{pending_action_id}.stage",
                        "'chromatic_orb_declined'",
                    ),
                    _call(
                        "complete_srd_pending_action",
                        {"pending_action_id": "{pending_action_id}"},
                        ["_spell_summary_rows", "_spell_detail_rows"],
                    ),
                ],
            ),
            _when(
                "target != null and _orb_choice_current == true",
                [
                    _require(
                        "_orb_source.hops < _orb_source.slot_level and "
                        "not (_target_id in _orb_source.visited)",
                        "This casting cannot leap again or target the same creature twice.",
                    ),
                    _when(
                        "lasting_state.spatial_scene != null",
                        [
                            _call(
                                "resolve_srd_scene_geometry_relation",
                                {
                                    "actor_state": "{_orb_source.last_target}",
                                    "target_state": "{_target_id}",
                                },
                                ["_scene_distance", "_scene_same_plane"],
                            ),
                            _require(
                                "_scene_same_plane == true", "The Orb cannot leap across planes."
                            ),
                            _set("jump_distance", "_scene_distance"),
                            _call(
                                "resolve_srd_scene_geometry_relation",
                                {
                                    "actor_state": "{_caster_state_id}",
                                    "target_state": "{_target_id}",
                                },
                                ["_scene_distance", "_scene_exposure"],
                            ),
                            _set("target_distance", "_scene_distance"),
                            _set("cover", "'none'"),
                            _when("_scene_exposure.exposure <= 0.5", [_set("cover", "'half'")]),
                            _when(
                                "_scene_exposure.exposure <= 0.25",
                                [_set("cover", "'three_quarters'")],
                            ),
                            _when(
                                "_scene_exposure.visible_samples == 0", [_set("cover", "'total'")]
                            ),
                            _call(
                                "resolve_srd_illumination_target_sight",
                                {
                                    "observer_state": "{_caster_state_id}",
                                    "target_state": "{_target_id}",
                                },
                                ["_illumination_target_can_see"],
                            ),
                            _set("caster_can_see_target", "_illumination_target_can_see"),
                            _call(
                                "resolve_srd_illumination_target_sight",
                                {
                                    "observer_state": "{_target_id}",
                                    "target_state": "{_caster_state_id}",
                                },
                                ["_illumination_target_can_see"],
                            ),
                            _set("target_can_see_caster", "_illumination_target_can_see"),
                            _call(
                                "resolve_srd_produce_flame_nearby_enemies",
                                {"caster_state": "{_caster_state_id}"},
                                ["_produce_nearby_disadvantage"],
                            ),
                            _set("enemy_within_5_feet", "_produce_nearby_disadvantage"),
                            _set("close_enemy_can_see_caster", True),
                            _set("close_enemy_incapacitated", False),
                        ],
                    ),
                    _require(
                        "jump_distance != null and jump_distance <= 30 and target_distance != null",
                        "Record the actual hop distance (at most 30 feet) and caster distance.",
                    ),
                    _set("_orb_visited", "_orb_source.visited"),
                    _push("_orb_visited", "_target_id"),
                    _set(
                        "lasting_state.pending_srd_actions.{pending_action_id}.orb_source.visited",
                        "_orb_visited",
                    ),
                    _set(
                        "lasting_state.pending_srd_actions.{pending_action_id}.orb_source.hops",
                        "_orb_source.hops + 1",
                    ),
                    _set(
                        "_orb_source",
                        "lasting_state.pending_srd_actions[pending_action_id].orb_source",
                    ),
                    _call(
                        ATTACK,
                        params,
                        [
                            "_spell_summary_rows",
                            "_spell_detail_rows",
                            "_spell_attack",
                            "_spell_attack_outcome",
                            "_spell_hit_window_deferred",
                            "_card_d20_total",
                        ],
                    ),
                    _when(
                        "_spell_hit_window_deferred == true",
                        [
                            _call(
                                "record_srd_spell_target_result",
                                {
                                    "target": "{_target_id}",
                                    "brief": "Attack total {_card_d20_total}; awaiting defense",
                                    "detail": "Resolve the defensive Reaction window before damage "
                                    "or another leap choice.",
                                },
                                ["_spell_target_summary", "_spell_target_detail"],
                            ),
                            _push("_spell_summary_rows", "_spell_target_summary"),
                            _push("_spell_detail_rows", "_spell_target_detail"),
                        ],
                    ),
                    _when(
                        "_spell_hit_window_deferred != true and "
                        "lasting_state.pending_srd_actions[pending_action_id].stage "
                        "!= 'chromatic_orb_choice'",
                        [
                            _call(
                                "complete_srd_pending_action",
                                {"pending_action_id": "{pending_action_id}"},
                                ["_spell_summary_rows", "_spell_detail_rows"],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )
    tool["metadata"] = {"category": "combat", "visible_to_player": True, "costs_action": False}
    tool["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "compact",
        "title": "Chromatic Orb",
        "summary": {"path": "context._spell_summary_rows"},
        "details_label": "Targets and calculations",
        "details": {"path": "context._spell_detail_rows"},
    }
    return tool


def _pending_routes(tools):
    """Bind only the new owned kind and choice stage at existing saved-hit/cleanup boundaries."""
    finish = deepcopy(tools[FINISH])
    for effects in _effect_lists(finish["effects"]):
        for effect in effects:
            requirement = effect.get("require", {})
            clause = requirement.get("condition", "")
            marker = "_spell_hit_event.owner_kind == 'spell_cast'"
            if marker in clause and "'chromatic_orb'" not in clause:
                requirement["condition"] = clause.replace(
                    marker, "(" + marker + " or _spell_hit_event.owner_kind == 'chromatic_orb')"
                )
    complete = deepcopy(tools["complete_srd_pending_action"])
    # The paid spell already resolved; closing its remaining choice cannot resolve an old hit.
    for effects in _effect_lists(complete["effects"]):
        for effect in effects:
            requirement = effect.get("require", {})
            clause = requirement.get("condition", "")
            if "_pending_action.turn_actor == null or" in clause and "chromatic_orb" not in clause:
                requirement["condition"] = (
                    "(" + clause + ") or (_pending_action.owner_kind == 'chromatic_orb' "
                    "and _pending_action.stage == 'chromatic_orb_declined')"
                )
    if COMPLETE not in str(complete["effects"]):
        found = 0
        for effects in _effect_lists(complete["effects"]):
            for index in range(len(effects) - 1, -1, -1):
                effect = effects[index]
                if not any(
                    e.get("call") == "complete_srd_spell_attack_consequences"
                    for e in effect.get("on_true", [])
                ):
                    continue
                effect["condition"] += " and _pending_action.owner_kind != 'chromatic_orb'"
                effects.insert(
                    index + 1,
                    _when(
                        "_pending_action.kind == 'spell_attack' and "
                        "_pending_action.status == 'open' "
                        "and _pending_action.owner_kind == 'chromatic_orb' and "
                        "_pending_action.stage != 'chromatic_orb_choice'",
                        [
                            _call(
                                COMPLETE,
                                {"pending_action_id": "{pending_action_id}"},
                                [
                                    "_spell_card_summary",
                                    "_spell_summary_rows",
                                    "_spell_detail_rows",
                                    "_spell_attack",
                                    "_spell_attack_outcome",
                                    "_spell_attack_kind",
                                ],
                            )
                        ],
                    ),
                )
                found += 1
        if found != 1:
            raise ValueError(
                "Chromatic Orb needs the one actual shared spell-hit completion route."
            )
    for effects in _effect_lists(complete["effects"]):
        for effect in effects:
            setter = effect.get("set", {})
            if setter.get("field") == "_keep_spell_hit_open" and CHOICE not in setter["value"]:
                setter["value"] = (
                    "("
                    + setter["value"]
                    + ") or ("
                    + (
                        "lasting_state.pending_srd_actions[pending_action_id].owner_kind == "
                        "'chromatic_orb' and "
                        "lasting_state.pending_srd_actions[pending_action_id].stage "
                        "== 'chromatic_orb_choice')"
                    )
                )
    return {FINISH: finish, "complete_srd_pending_action": complete}


def bind_runtime_consumers(text):
    """Capture final actual spell-hit owners after Reaction, Mirror and source consumers."""
    tools = _load_runtime(text)["tools"]
    leaf = deepcopy(tools[LEAF])
    finish = deepcopy(tools["complete_srd_spell_attack_hit"])
    if CAST_ID not in {p["name"] for p in leaf["parameters"]}:
        raise ValueError("Emit the paid Chromatic Orb source before its final hit consumers.")
    leaf["effects"] = _clear_sink(leaf["effects"]) + [_sink_call(leaf["parameters"])]
    # A deferred hit has not rolled damage, so its actual suffix owns the later match.
    leaf["effects"][-1]["condition"] += " and _spell_hit_window_deferred != true"
    finish["effects"] = _clear_sink(finish["effects"]) + [_sink_call(finish["parameters"])]
    changed = {LEAF: leaf, "complete_srd_spell_attack_hit": finish, RECORD: _record_tool()}
    changed.update(_pending_routes({**tools, **changed}))
    # The remainder is emitted from the actual settled prefix/suffix, never a second caster.
    changed.update(_leap_tools(leaf, changed[FINISH]))
    return _replace_tools(text, {k: v for k, v in changed.items() if tools.get(k) != v})
