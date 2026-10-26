"""Planar Binding retains its actual casting target and source-owned commands."""

from copy import deepcopy

import yaml

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
    from .srd_timed_senses import _replace_tools
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
    from srd_timed_senses import _replace_tools


PROFILES = {
    "planar_binding": {
        "family": "control_binding_spell",
        "base_level": 5,
        "casting_time": "1_hour",
        "range_kind": "distance",
        "range_feet": 60,
        "duration_rounds": 14400,
        "concentration": False,
        "save_ability": "charisma",
    },
}
DURATION_ROUNDS = {5: 14400, 6: 144000, 7: 432000, 8: 2592000, 9: 5270400}
PUBLIC_TOOLS = {"issue_srd_bound_order", "resolve_srd_binding_return"}
GEOMETRY_EXPORTS = (
    "_scene_same_plane",
    "_scene_distance",
    "_scene_target_position",
    "_scene_line_of_sight",
)
CAST_CHOICES = []


def validate_profiles(definitions):
    """Only the actual pinned source profile enters this owner."""
    for spell, definition in definitions.items():
        if definition.get("family") == "control_binding_spell" and definition != PROFILES.get(
            spell
        ):
            raise ValueError(f"{spell} differs from its pinned Planar Binding profile")


def _schema():
    return {
        "type": "object",
        "properties": {
            key: {
                "type": "boolean"
                if type(value) is bool
                else "integer"
                if type(value) is int
                else "string"
            }
            for key, value in PROFILES["planar_binding"].items()
            if key != "family"
        },
        "required": sorted(set(PROFILES["planar_binding"]) - {"family"}),
        "additionalProperties": False,
    }


def _source_field(field, source="source_id"):
    """Address one retained condition source without changing its pool ownership."""
    return f"lasting_state.timed_condition_sources.{{{source}}}.{field}"


def _geometry(caster="_caster_state_id", target="_target_state_id"):
    return _call(
        "resolve_srd_scene_geometry_relation",
        {"actor_state": "{" + caster + "}", "target_state": "{" + target + "}"},
        GEOMETRY_EXPORTS,
    )


def _channel_tools():
    check = _tool(
        "Qualify the actual retained Binding target, interrupting a channel that leaves range.",
        [_entity("caster_state"), _lasting()],
        [
            _set("_binding_channel_active", False),
            _when(
                "caster_state.extended_casting != null and "
                "caster_state.extended_casting.spell == 'planar_binding'",
                [
                    _require(
                        "caster_state.extended_casting.binding_target != null",
                        "Planar Binding must retain its actual target from the start of casting.",
                    ),
                    _call(
                        "check_srd_binding_channel_target",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{caster_state.extended_casting.binding_target}",
                        },
                        ("_binding_channel_in_range",),
                    ),
                    _when(
                        "_binding_channel_in_range == true",
                        [_set("_binding_channel_active", True)],
                        [
                            _call(
                                "interrupt_srd_extended_casting",
                                {"caster_state": "{_caster_state_id}"},
                            )
                        ],
                    ),
                ],
            ),
        ],
    )
    target = _tool(
        "Measure the actual two creature bodies independently of sight or a caller range verdict.",
        [_entity("caster_state"), _entity("target_state")],
        [
            _set("_binding_channel_in_range", False),
            _when(
                "caster_state.is_alive == true and target_state.is_alive == true",
                [
                    _geometry(),
                    _set(
                        "_binding_channel_in_range",
                        "_scene_same_plane == true and _scene_distance <= 60",
                    ),
                ],
            ),
        ],
    )
    refresh = _tool(
        "Recheck indexed actual Binding channels after committed movement or a casting interval.",
        [_lasting()],
        [
            _set("_binding_channels", []),
            _set("_binding_kept_channels", []),
            _when(
                "lasting_state.binding_channel_caster_ids != null",
                [_set("_binding_channels", "lasting_state.binding_channel_caster_ids")],
            ),
            _each(
                "_binding_channels",
                "_binding_channel_caster",
                [
                    _call(
                        "check_srd_binding_channel",
                        {"caster_state": "{_binding_channel_caster}"},
                        ("_binding_channel_active",),
                    ),
                    _when(
                        "_binding_channel_active == true",
                        [_push("_binding_kept_channels", "_binding_channel_caster")],
                    ),
                ],
            ),
            _set("lasting_state.binding_channel_caster_ids", "_binding_kept_channels"),
            _set("lasting_state.has_binding_channels", "len(_binding_kept_channels) > 0"),
        ],
    )
    return {
        "check_srd_binding_channel": check,
        "check_srd_binding_channel_target": target,
        "refresh_srd_binding_channels": refresh,
    }


def _cast_tool():
    save = _call(
        "d20_test",
        {
            "actor_state": "{_target_state_id}",
            "ability": "charisma",
            "kind": "saving_throw",
            "ability_modifier": "{target_state.saving_throw_modifiers.charisma}",
            "proficiency_bonus": 0,
            "proficiency_factor": "none",
            "circumstantial_modifier": 0,
            "target_number": "{save_dc}",
            "advantage": False,
            "disadvantage": False,
            "forego_save": False,
            "save_avoids_condition": "none",
        },
        ("_d20_test", "_outcome"),
    )
    return _tool(
        "Commit the actual Binding save, source lifetime and command "
        "after its full retained-target casting.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("slot_level", "integer", minimum=5, maximum=9),
            _param("save_dc", "integer"),
            *deepcopy(CAST_CHOICES),
            _param("spell_cast_id", required=False, default="", model_hidden=True),
            _param(
                "spell_cast_owner",
                resolve="entity",
                required=False,
                default=None,
                model_hidden=True,
            ),
            _param(
                "actual_spell_level",
                "integer",
                minimum=-1,
                maximum=9,
                required=False,
                default=-1,
                model_hidden=True,
            ),
        ],
        [
            _require(
                "caster_state.binding_completed_tick == lasting_state.lasting_tick",
                "Planar Binding needs its real completed casting channel.",
            ),
            _require(
                "caster_state.binding_completed_target == _target_state_id",
                "Finish Binding on the same actual creature retained at its start.",
            ),
            _geometry(),
            _require(
                "_scene_same_plane == true and _scene_distance <= 60",
                "The actual bound target must remain within 60 feet.",
            ),
            _set("_binding_outcome", "'success'"),
            _set("_binding_roll", None),
            _set("_binding_source_id", None),
            _set("_binding_eligible_types", ["'celestial'", "'elemental'", "'fey'", "'fiend'"]),
            _set(
                "_binding_ineligible", "target_state.creature_type not in _binding_eligible_types"
            ),
            _when(
                "_binding_ineligible != true",
                [
                    _call(
                        "check_srd_mind_blank_information",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "query_kind": "mind_control",
                        },
                        ("_mind_blank_information_blocked",),
                    ),
                    _when(
                        "_mind_blank_information_blocked != true",
                        [
                            _require(
                                "target_state.saving_throw_modifiers.charisma != null",
                                "Binding needs the target's actual Charisma saving throw modifier.",
                            ),
                            save,
                            _set("_binding_roll", "_d20_test"),
                            _set("_binding_outcome", "_outcome"),
                        ],
                    ),
                ],
            ),
            _when(
                "_binding_outcome == 'failure'",
                [
                    *[
                        _when(f"slot_level == {level}", [_set("_binding_rounds", rounds)])
                        for level, rounds in DURATION_ROUNDS.items()
                    ],
                    _call(
                        "install_srd_timed_condition_source",
                        {
                            "target_state": "{_target_state_id}",
                            "caster_state": "{_caster_state_id}",
                            "spell": "planar_binding",
                            "condition": "none",
                            "save_dc": "{save_dc}",
                            "duration_rounds": "{_binding_rounds}",
                            "repeat_save_timing": "none",
                            "save_ability": "charisma",
                            "control_profile": "planar_binding",
                            "spell_cast_id": "{spell_cast_id}",
                            "spell_cast_owner": "{_spell_cast_owner_id}",
                            "actual_spell_level": "{actual_spell_level}",
                        },
                        ("_timed_condition_source_id",),
                    ),
                    _set("_binding_source_id", "_timed_condition_source_id"),
                    _set(
                        _source_field("binding_origin", "_binding_source_id"),
                        "_scene_target_position",
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        ("_info_scene_entity",),
                    ),
                    _set(
                        _source_field("binding_plane", "_binding_source_id"),
                        "_info_scene_entity.metadata.plane",
                    ),
                    _set(
                        _source_field("dispel_ongoing", "_binding_source_id"),
                        True,
                    ),
                    _call(
                        "initialize_srd_control_order_source",
                        {
                            "target_state": "{_target_state_id}",
                            "source_id": "{_binding_source_id}",
                            "command": "none",
                            "activity_words": [],
                            "indefinite": 0,
                        },
                    ),
                    _set("target_state.binding_managed", True),
                    _call(
                        "refresh_srd_timed_condition_target", {"target_state": "{_target_state_id}"}
                    ),
                ],
            ),
            _set(
                "_spell_summary_rows",
                [{"label": "'Bound'", "value": "_binding_outcome == 'failure'"}],
            ),
            _set("_spell_detail_rows", []),
            _when(
                "_binding_roll != null",
                [
                    _push(
                        "_spell_detail_rows", {"label": "'Saving throw'", "value": "_binding_roll"}
                    )
                ],
            ),
            _set("caster_state.binding_completed_tick", None),
            _set("caster_state.binding_completed_target", None),
        ],
    )


def _widen_order(tool):
    """Share actual task/attack/movement consumers without granting Dominate's special Reactions."""

    def amend(nodes):
        for node in nodes[:]:
            if node.get("set", {}).get("field") == "_order":
                index = nodes.index(node) + 1
                if not (
                    index < len(nodes)
                    and nodes[index].get("set", {}).get("field") == "_order_direct_control"
                ):
                    nodes.insert(
                        index,
                        _set(
                            "_order_direct_control",
                            "_order.control_profile == 'dominate' or "
                            "_order.control_profile == 'planar_binding'",
                        ),
                    )
            for field in (node, node.get("require", {})):
                value = field.get("condition")
                if isinstance(value, str):
                    field["condition"] = value.replace(
                        "_order.control_profile == 'dominate'", "_order_direct_control == true"
                    ).replace(
                        "_order.control_profile != 'dominate'", "_order_direct_control != true"
                    )
            for key in ("on_true", "on_false", "effects"):
                if isinstance(node.get(key), list):
                    amend(node[key])
            if isinstance(node.get("for_each"), dict):
                amend(node["for_each"].get("do", []))

    amend(tool.get("effects", []))


def _order_tools(tools):
    issue = deepcopy(
        tools.get("issue_srd_dominated_order_internal", tools["issue_srd_dominated_order"])
    )
    issue["metadata"].update(category="combat", visible_to_player=True, costs_action=False)
    issue["description"] = "Deliver a concrete understood command to this actual bound creature."
    issue["parameters"] += [_param("language", required=False, default="")]
    first = next(
        i
        for i, node in enumerate(issue["effects"])
        if "Only this Dominate" in node.get("require", {}).get("error", "")
    )
    last = next(
        i
        for i, node in enumerate(issue["effects"])
        if node.get("set", {}).get("field", "").endswith(".order_kind")
    )
    issue["effects"][first:last] = [
        _require(
            "_order.control_profile == 'planar_binding' and "
            "_order.caster_state_id == _caster_state_id and "
            "_order.target_state_id == _target_state_id",
            "Only the actual Binding caster may command its retained source.",
        ),
        _require(
            "combat.encounter_active != true or (combat.turn_active == "
            "true and combat.active_actor == _caster_state_id)",
            "Give a bound command on the caster's turn.",
        ),
        _geometry(),
        _require(
            "_scene_same_plane == true and _scene_line_of_sight == true",
            "The ordinary delivered command requires an actual "
            "unobstructed same-plane interaction.",
        ),
        _call(
            "resolve_srd_spell_language",
            {
                "listener_state": "{_target_state_id}",
                "speaker_state": "{_caster_state_id}",
                "channel": "speech",
                "perceived": True,
                "language": "{language}",
            },
            ("_language_understands",),
        ),
        _require(
            "_language_understands == true and len(order_description) > 0",
            "Record an actual understood command; the spell grants no telepathic link.",
        ),
        _require(
            "order_kind != 'attack' or order_target_state != null",
            "An Attack order must name its actual target.",
        ),
        _set(_source_field("binding_reported"), False),
        _set(_source_field("binding_return_pending"), False),
        _set(_source_field("binding_waiting_at_origin"), False),
    ]
    complete = _tool(
        "After an actual bound task completes, retain its owed "
        "report or return as a source-owned order.",
        [_entity("target_state"), _lasting()],
        [
            _set("_binding_orders", []),
            _when(
                "target_state.control_order_source_ids != null",
                [_set("_binding_orders", "target_state.control_order_source_ids")],
            ),
            _each(
                "_binding_orders",
                "_binding_order_id",
                [
                    _set(
                        "_binding_order", "lasting_state.timed_condition_sources[_binding_order_id]"
                    ),
                    _when(
                        "_binding_order.control_profile == 'planar_binding' and "
                        "_binding_order.order_complete == true and "
                        "_binding_order.binding_reported != true and "
                        "_binding_order.binding_waiting_at_origin != true",
                        [
                            _set(
                                _source_field("binding_return_pending", "_binding_order_id"),
                                True,
                            ),
                            _set(
                                _source_field("order_kind", "_binding_order_id"),
                                "'move'",
                            ),
                            _set(
                                _source_field("order_complete", "_binding_order_id"),
                                False,
                            ),
                            _set(
                                _source_field("order_pending", "_binding_order_id"),
                                True,
                            ),
                        ],
                    ),
                ],
            ),
            _call("refresh_srd_control_order_projection", {"target_state": "{_target_state_id}"}),
        ],
    )
    returning = _tool(
        "Move the bound creature along a supplied actual route "
        "toward its caster or original binding place.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _lasting(),
            _param("source_id"),
            _param("destination", "array", items={"type": "number"}, min_items=3, max_items=3),
            _param(
                "mode",
                "enum",
                enum_values=["walk", "fly", "climb", "ceiling"],
                default="walk",
                required=False,
            ),
            _param("report_description", required=False, default=""),
        ],
        [
            _set("_binding_order", "lasting_state.timed_condition_sources[source_id]"),
            _require(
                "_binding_order.installed == true and "
                "_binding_order.suppressed != true and "
                "_binding_order.control_profile == 'planar_binding'",
                "Only an effective exact Binding source can direct this return.",
            ),
            _require(
                "_binding_order.target_state_id == _target_state_id and "
                "_binding_order.caster_state_id == _caster_state_id and "
                "_binding_order.binding_return_pending == true",
                "The actual bound creature must owe this source its report or return.",
            ),
            _call(
                "check_srd_selected_order_source",
                {"source_id": "{source_id}", "target_state": "{_target_state_id}"},
            ),
            _call(
                "resolve_srd_scene_geometry_relation",
                {"actor_state": "{_target_state_id}", "target_state": "{_caster_state_id}"},
                ("_scene_same_plane", "_scene_actor_position", "_scene_target_position"),
            ),
            _set("_binding_return_goal", "_binding_order.binding_origin"),
            _when(
                "_scene_same_plane == true",
                [_set("_binding_return_goal", "_scene_target_position")],
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_target_state_id}"},
                ("_info_scene_entity",),
            ),
            _require(
                "_scene_same_plane == true or "
                "_info_scene_entity.metadata.plane == "
                "_binding_order.binding_plane",
                "A different-plane report returns to the actual original "
                "binding plane; ordinary movement does not invent planar "
                "travel.",
            ),
            _set("target_state.control_order_moving", True),
            _when(
                "destination[0] != _scene_actor_position[0] or "
                "destination[1] != _scene_actor_position[1] or "
                "destination[2] != _scene_actor_position[2]",
                [
                    _call(
                        "move_srd_scene_creature",
                        {
                            "actor_state": "{_target_state_id}",
                            "destination": "{destination}",
                            "mode": "{mode}",
                            "difficult_terrain": False,
                        },
                        ("_scene_actor_position",),
                    )
                ],
            ),
            _set("target_state.control_order_moving", False),
            _set(
                "_binding_remaining",
                "((_scene_actor_position[0] - _binding_return_goal[0]) ** 2 "
                "+ (_scene_actor_position[1] - _binding_return_goal[1]) ** 2 "
                "+ (_scene_actor_position[2] - _binding_return_goal[2]) ** "
                "2) ** 0.5",
            ),
            _set(_source_field("order_pending"), False),
            _when(
                "_binding_remaining <= 5",
                [
                    _when(
                        "_scene_same_plane == true",
                        [
                            _require(
                                "len(report_description) > 0",
                                "Record the bound creature's actual completion report.",
                            ),
                            _set(
                                _source_field("binding_report"),
                                "report_description",
                            ),
                            _set(
                                _source_field("binding_reported"),
                                True,
                            ),
                        ],
                        [
                            _set(
                                _source_field("binding_waiting_at_origin"),
                                True,
                            )
                        ],
                    ),
                    _set(
                        _source_field("binding_return_pending"),
                        False,
                    ),
                    _set(_source_field("order_complete"), True),
                ],
            ),
            _call("refresh_srd_timed_condition_target", {"target_state": "{_target_state_id}"}),
        ],
        [
            {
                "condition": "combat.encounter_active != true or (combat.turn_active == "
                "true and combat.active_actor == _target_state_id)",
                "error": "Use only this bound creature's active turn movement.",
            }
        ],
        public=True,
    )
    return {
        "issue_srd_bound_order": issue,
        "finish_srd_bound_activity": complete,
        "resolve_srd_binding_return": returning,
    }


def _effect_lists(effects):
    yield effects
    for effect in effects:
        for branch in ("on_true", "on_false"):
            yield from _effect_lists(effect.get(branch, []))
        if "for_each" in effect:
            yield from _effect_lists(effect["for_each"]["do"])


def _casting_owners(tools):
    changed = {}
    begin = deepcopy(tools["begin_srd_spell_cast"])
    begin["effects"] = [
        node
        for node in begin["effects"]
        if node.get("condition")
        != "spell == 'planar_binding' and caster_state.extended_casting != null"
    ]
    if not any(p["name"] == "target" for p in begin["parameters"]):
        begin["parameters"].append(_param("target", resolve="entity", default=None, required=False))
    begin["effects"].append(
        _when(
            "spell == 'planar_binding' and caster_state.extended_casting != null",
            [
                _require(
                    "target != null",
                    "Retain Planar Binding's actual creature target from the casting start.",
                ),
                _call(
                    "check_srd_binding_channel_target",
                    {"caster_state": "{_caster_state_id}", "target_state": "{_target_id}"},
                    ("_binding_channel_in_range",),
                ),
                _require(
                    "_binding_channel_in_range == true",
                    "The actual Binding target must begin within 60 feet.",
                ),
                _set("caster_state.extended_casting.binding_target", "_target_id"),
                _set("_binding_channels", []),
                _when(
                    "lasting_state.binding_channel_caster_ids != null",
                    [_set("_binding_channels", "lasting_state.binding_channel_caster_ids")],
                ),
                _when(
                    "_caster_state_id not in _binding_channels",
                    [_push("_binding_channels", "_caster_state_id")],
                ),
                _set("lasting_state.binding_channel_caster_ids", "_binding_channels"),
                _set("lasting_state.has_binding_channels", True),
            ],
        )
    )
    if not any(p["name"] == "lasting_state" for p in begin["parameters"]):
        begin["parameters"].append(_lasting())
    changed["begin_srd_spell_cast"] = begin
    continuing = deepcopy(tools["continue_srd_spell_cast"])
    continuing["effects"] = [
        node for node in continuing["effects"] if node.get("call") != "check_srd_binding_channel"
    ]
    continuing["effects"].insert(
        0, _call("check_srd_binding_channel", {"caster_state": "{_caster_state_id}"})
    )
    changed["continue_srd_spell_cast"] = continuing
    cast = deepcopy(tools["cast_spell"])
    for nodes in _effect_lists(cast["effects"]):
        nodes[:] = [
            node
            for node in nodes
            if node.get("condition")
            != "_spell_cast_failed != true and _spell_definition.family == 'control_binding_spell'"
            and not (
                node.get("condition") == "spell == 'planar_binding'"
                and (
                    "binding_completed_tick" in str(node)
                    or "check_srd_binding_channel" in str(node)
                )
            )
        ]
    for choice in CAST_CHOICES:
        if not any(p["name"] == choice["name"] for p in cast["parameters"]):
            cast["parameters"].append(deepcopy(choice))
    # The shared completion owner retires the channel before family dispatch.
    # Capture only a genuinely completed retained-target receipt immediately before that retirement.
    for nodes in _effect_lists(cast["effects"]):
        if any("Start and continue" in node.get("require", {}).get("error", "") for node in nodes):
            nodes.insert(
                0,
                _when(
                    "spell == 'planar_binding'",
                    [
                        _call(
                            "check_srd_binding_channel",
                            {"caster_state": "{_caster_state_id}"},
                            ("_binding_channel_active",),
                        ),
                        _require(
                            "_binding_channel_active == true and target != null",
                            "Complete only the retained actual Binding target.",
                        ),
                        _require(
                            "caster_state.extended_casting.binding_target == _target_id",
                            "Finish Binding on the same retained target.",
                        ),
                    ],
                ),
            )
            end = next(
                i
                for i, node in enumerate(nodes)
                if node.get("call") == "end_srd_concentration_node"
            )
            nodes.insert(
                end,
                _when(
                    "spell == 'planar_binding'",
                    [
                        _set("caster_state.binding_completed_target", "_target_id"),
                        _set("caster_state.binding_completed_tick", "lasting_state.lasting_tick"),
                    ],
                ),
            )
    dispatch = next(
        i
        for i, node in enumerate(cast["effects"])
        if any(e.get("call") == "cast_spell_multi_target_timed_speed_bonus" for e in _walk(node))
    )
    cast["effects"].insert(
        dispatch,
        _when(
            "_spell_cast_failed != true and _spell_definition.family == 'control_binding_spell'",
            [
                _call(
                    "cast_srd_planar_binding",
                    {
                        "caster_state": "{_caster_state_id}",
                        "target_state": "{_target_id}",
                        "slot_level": "{_slot_level}",
                        "save_dc": "{_casting_profile.save_dc}",
                        "spell_cast_id": "{_spell_source_cast_id}",
                        "spell_cast_owner": "{_caster_state_id}",
                        "actual_spell_level": "{_slot_level}",
                    },
                    ("_spell_summary_rows", "_spell_detail_rows"),
                ),
            ],
        ),
    )
    changed["cast_spell"] = cast
    for name in (
        "move_srd_scene_creature_node",
        "move_srd_travel_subject",
    ):
        if name in tools:
            moving = deepcopy(tools[name])
            moving["effects"] = [
                node
                for node in moving["effects"]
                if node.get("condition") != "lasting_state.has_binding_channels == true"
            ]
            if not any(p["name"] == "lasting_state" for p in moving["parameters"]):
                moving["parameters"].append(_lasting())
            moving["effects"].append(
                _when(
                    "lasting_state.has_binding_channels == true",
                    [_call("refresh_srd_binding_channels", {})],
                )
            )
            changed[name] = moving
    return changed


def extend_runtime(text):
    "Bind the new family at package owners; the core and"
    "independent source lifetimes stay intact."
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = runtime["tools"]
    changed = _channel_tools()
    changed["cast_srd_planar_binding"] = _cast_tool()
    changed.update(_order_tools(tools))
    changed.update(_casting_owners(tools))
    installer = deepcopy(tools["install_srd_timed_condition_source"])
    control = next(p for p in installer["parameters"] if p["name"] == "control_profile")
    if "planar_binding" not in control["enum_values"]:
        control["enum_values"].append("planar_binding")
    changed["install_srd_timed_condition_source"] = installer
    selecting = deepcopy(tools["resolve_srd_control_behavior_sources"])
    for node in _walk(selecting["effects"]):
        if (
            node.get("set", {}).get("field") == "_control_spell_profiles"
            and "'planar_binding'" not in node["set"]["value"]
        ):
            node["set"]["value"].append("'planar_binding'")
    changed["resolve_srd_control_behavior_sources"] = selecting
    for name in (
        "start_srd_control_order_turn",
        "check_srd_control_order_action",
        "check_srd_control_order_attack",
        "finish_srd_control_order_attack",
        "resolve_srd_control_order",
        "resolve_srd_control_activity",
    ):
        if name in tools:
            owned_name = name + "_internal" if name + "_internal" in tools else name
            tool = deepcopy(tools[owned_name])
            tool["effects"] = [
                node
                for node in tool["effects"]
                if node.get("call") != "finish_srd_bound_activity"
                and "Use the Binding return owner" not in node.get("require", {}).get("error", "")
            ]
            _widen_order(tool)
            if name in ("finish_srd_control_order_attack", "resolve_srd_control_activity"):
                tool["effects"].append(
                    _call("finish_srd_bound_activity", {"target_state": "{_actor_state_id}"})
                )
            if name == "resolve_srd_control_order":
                tool["effects"].insert(
                    0,
                    _require(
                        "lasting_state.timed_condition_sources[source_id].binding_ret"
                        "urn_pending != true",
                        "Use the Binding return owner for the owed completion report.",
                    ),
                )
                tool["effects"].append(
                    _call("finish_srd_bound_activity", {"target_state": "{_actor_state_id}"})
                )
            changed[owned_name] = tool
    for name in ("resolve_srd_control_behavior_conditions", "refresh_srd_control_order_projection"):
        tool = deepcopy(tools[name])
        for node in _walk(tool["effects"]):
            condition = node.get("condition", "")
            if "control_profile == 'geas'" in condition and "planar_binding" not in condition:
                marker = (
                    "_control_index_source"
                    if "_control_index_source" in condition
                    else "_order_source"
                )
                node["condition"] = condition.replace(
                    marker + ".control_profile == 'geas'",
                    marker
                    + ".control_profile == 'geas' or "
                    + marker
                    + ".control_profile == 'planar_binding'",
                )
        changed[name] = tool
    project = changed["refresh_srd_control_order_projection"]
    project["effects"] = [
        node
        for node in project["effects"]
        if node.get("set", {}).get("field") != "target_state.binding_waiting_at_origin"
        and node.get("for_each", {}).get("as") != "_binding_selected_id"
    ]
    project["effects"].append(_set("target_state.binding_waiting_at_origin", False))
    project["effects"].append(
        _each(
            "_order_selected",
            "_binding_selected_id",
            [
                _set(
                    "_binding_selected",
                    "lasting_state.timed_condition_sources[_binding_selected_id]",
                ),
                _when(
                    "_binding_selected.control_profile == 'planar_binding' and "
                    "_binding_selected.binding_waiting_at_origin == true",
                    [_set("target_state.binding_waiting_at_origin", True)],
                ),
            ],
        )
    )
    spending = deepcopy(tools["spend_combat_movement"])
    spending["pre_conditions"] = [
        p
        for p in spending["pre_conditions"]
        if p.get("condition") != "actor_state.binding_waiting_at_origin != true"
    ]
    spending["pre_conditions"].append(
        {
            "condition": "actor_state.binding_waiting_at_origin != true",
            "error": "This exact Binding source requires the creature to remain "
            "at its binding place.",
        }
    )
    changed["spend_combat_movement"] = spending
    deriving = deepcopy(tools["derive_srd_scene_movement_budget"])
    deriving["effects"] = [
        node
        for node in deriving["effects"]
        if node.get("require", {}).get("condition")
        != "actor_state.binding_waiting_at_origin != true"
    ]
    deriving["effects"].insert(
        0,
        _require(
            "actor_state.binding_waiting_at_origin != true",
            "This Binding source requires the creature to remain at its original binding place.",
        ),
    )
    changed["derive_srd_scene_movement_budget"] = deriving
    suppression = deepcopy(tools["resolve_srd_timed_actor_condition_suppression"])
    for node in _walk(suppression["effects"]):
        condition = node.get("condition", "")
        if (
            "_actor_mind_control_suppressed" in condition
            and "'confusion'" in condition
            and "'planar_binding'" not in condition
        ):
            node["condition"] = condition.replace(
                "_actor_suppression_source.control_profile == 'confusion'",
                "_actor_suppression_source.control_profile == 'confusion' or "
                "_actor_suppression_source.control_profile == "
                "'planar_binding'",
            )
    changed["resolve_srd_timed_actor_condition_suppression"] = suppression
    composed = {**tools, **changed}
    for tool in changed.values():
        for node in _walk(tool.get("effects", [])):
            called = composed.get(node.get("call"), {})
            if any(
                p["name"] == "lasting_state" and p.get("required")
                for p in called.get("parameters", [])
            ):
                if not any(p["name"] == "lasting_state" for p in tool["parameters"]):
                    tool["parameters"].append(_lasting())
                node["params"].setdefault("lasting_state", "{_lasting_state_id}")
    text = _replace_tools(text, changed)
    if "      control_binding_spell:" not in text:
        schema = yaml.safe_dump({"control_binding_spell": _schema()}, sort_keys=False)
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      timed_senses:",
            1,
        )
    return text
