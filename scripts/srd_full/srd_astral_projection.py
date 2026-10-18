"""Project one unmodified logical actor while retaining its distinct original physical body."""

from copy import deepcopy

if __package__:
    from .srd_divination_spells import _action
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from .srd_spell_object_content import _new_scene_object
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
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
else:
    from srd_divination_spells import _action
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from srd_spell_object_content import _new_scene_object
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
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools

PROFILE = {
    "family": "astral_projection",
    "base_level": 9,
    "casting_time": "1_hour",
    "range_kind": "distance",
    "range_feet": 10,
    "concentration": False,
    "until_dispelled": True,
}
SOURCE = (
    "wotc.srd.5.2.1:spells:p111:eecccf60877d85440c472b8d05bd5a0ed9b0278d94f4b1c494b474a2e28d3d4d"
)
PUBLIC_TOOLS = {"read_srd_astral_projection", "dismiss_srd_astral_projection"}
BODY_STATE_FIELDS = (
    "hp",
    "max_hp",
    "temp_hp",
    "is_alive",
    "stable",
    "death_successes",
    "death_failures",
    "exhaustion_level",
)
POSSESSION_FIELDS = (
    "inventory",
    "spell_materials",
    "held_items",
    "held_attack_names",
    "dropped_items",
    "control_dropped_attack_names",
    "control_held_items_dropped",
    "worn_armor",
    "worn_shield",
    "armor_class",
    "armor_speed_penalty",
    "armor_untrained",
    "armor_stealth_disadvantage",
    "speed",
    "climb_speed",
    "swim_speed",
    "equipment_profile",
)
CAST_CHOICES = [
    _optional("astral_anchor", default=""),
    _optional(
        "astral_companions", "array", [], items={"type": "string"}, unique_items=True, max_items=8
    ),
]
RESULTS = ("_spell_summary_rows", "_spell_detail_rows")


def schema():
    properties = {
        k: {
            "type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string",
            "enum": [v],
        }
        for k, v in PROFILE.items()
        if k != "family"
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    for spell, row in definitions.items():
        if row.get("family") == "astral_projection" and (
            spell != "astral_projection" or row != PROFILE
        ):
            raise ValueError("Astral Projection must retain its printed fixed profile.")


def _patch(identity, patch):
    return [
        *_snapshot(),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "entity", "id": identity},
                "patch": patch,
            },
            "store_as": "_astral_scene_changed",
        },
    ]


def _possession_requirements():
    """Qualify represented ordinary possessions before either payment or replication."""
    return [
        *[
            _when(
                "caster_state." + slot + " != null",
                [
                    _require(
                        "caster_state.inventory != null and caster_state."
                        + slot
                        + ".inventory_item in caster_state.inventory and caster_state."
                        + slot
                        + ".magic_bonus == 0",
                        "Replicate only actual carried ordinary equipment; magical properties "
                        "remain unsupported.",
                    ),
                ],
            )
            for slot in ("worn_armor", "worn_shield")
        ],
        _when(
            "caster_state.held_items != null",
            [
                _set("_astral_held", "caster_state.held_items"),
                _loop(
                    "_astral_held",
                    "_astral_held_item",
                    [
                        _require(
                            "caster_state.inventory != null and "
                            "_astral_held_item in caster_state.inventory",
                            "Held replicas must name actual carried inventory items.",
                        ),
                    ],
                ),
            ],
        ),
        _set("_astral_objects", "state.spatial_scene.entities"),
        _loop(
            "_astral_objects",
            "_astral_object",
            [
                _require(
                    "_astral_object.active == false or "
                    "(_astral_object.metadata.carried_by != _caster_state_id and "
                    "_astral_object.metadata.worn_by != _caster_state_id)",
                    "Replicating separately admitted carried/worn object bodies remains "
                    "unsupported; they cannot follow the logical Astral form.",
                ),
            ],
        ),
    ]


def _plain_actor():
    return [
        _require(
            "len(caster_state.conditions) == 0 and caster_state.hp > 0 and "
            "caster_state.is_alive == true and caster_state.exhaustion_level == 0",
            "This bounded projection needs a living body without independent conditions.",
        ),
        _require(
            "caster_state.astral_projection_role == null and "
            "caster_state.magic_jar_role == null and caster_state.has_transformations != true "
            "and caster_state.travel_exile == null and "
            "caster_state.concentration_spell_id == null and "
            "caster_state.current_body_source_id == null",
            "End actual body replacement and ongoing concentration before this projection.",
        ),
        _require(
            "caster_state.created_spell_id == null and caster_state.summon_caster_state_id == null",
            "Projection of an existing source-created actor remains unsupported.",
        ),
        _require(
            "(caster_state.timed_condition_source_ids == null or "
            "len(caster_state.timed_condition_source_ids) == 0) and "
            "caster_state.has_timed_actor_defenses != true and "
            "caster_state.has_actor_spell_modifiers != true and "
            "caster_state.has_actor_weapon_modifiers != true and "
            "caster_state.actor_size_projected != true and "
            "caster_state.illumination_has_invisibility != true",
            "Replicating existing source-owned effects remains unsupported.",
        ),
        _when(
            "state.turn_effects != null",
            [
                _set("_astral_prior_effects", "state.turn_effects"),
                _loop(
                    "_astral_prior_effects",
                    "_astral_prior_effect",
                    [
                        _require(
                            "_astral_prior_effect.installed == false or "
                            "(_astral_prior_effect.expires_tick != null and "
                            "_astral_prior_effect.expires_tick <= state.lasting_tick) or "
                            "(_astral_prior_effect.target != _caster_state_id and "
                            "_astral_prior_effect.source != _caster_state_id and "
                            "_astral_prior_effect.caster != _caster_state_id)",
                            "Replicating this actor's actual retained spell effects "
                            "is unsupported.",
                        ),
                    ],
                ),
            ],
        ),
        *_possession_requirements(),
        _require(
            "caster_state.condition_immunities == null or "
            "'unconscious' not in caster_state.condition_immunities",
            "This bounded path requires a body that can receive Unconscious.",
        ),
    ]


def _single_projection_template():
    """A nested read refreshes scene aliases before the canonical constructor reads them."""
    body_fields = {
        "name": "caster_state.name",
        "creature_kind": "caster_state.creature_kind",
        "creature_type": "caster_state.creature_type",
        "logical_actor_id": "_caster_state_id",
        "hp": "caster_state.hp",
        "max_hp": "caster_state.max_hp",
        "temp_hp": "caster_state.temp_hp",
        "armor_class": "caster_state.armor_class",
        "speed": "caster_state.speed",
        "is_alive": True,
        "conditions": ["unconscious"],
        "exhaustion_level": 0,
        "stable": "caster_state.stable",
        "death_successes": "caster_state.death_successes",
        "death_failures": "caster_state.death_failures",
        "reaction_available": False,
        "resources": {},
        "resource_maxima": {},
        "damage_immunities": "caster_state.damage_immunities",
        "damage_resistances": "caster_state.damage_resistances",
        "damage_vulnerabilities": "caster_state.damage_vulnerabilities",
        "astral_projection_role": "'body'",
        "astral_projection_source_id": "spell_cast_id",
        "suspended_animation": True,
        "needs_food": False,
        "needs_air": False,
        "aging_suspended": True,
        "nonautonomous_body": True,
        **{field: "caster_state." + field for field in POSSESSION_FIELDS},
    }
    project = [
        *_plain_actor(),
        _require(
            "_astral_origin.id != _caster_state_id and "
            "_astral_origin.metadata.participant_ref == _caster_state_id and "
            "_astral_origin.footprint.kind == 'box'",
            "Use an actual Box body proxy distinct from the logical actor identity; "
            "direct-ID body aliases remain unsupported.",
        ),
        _require("astral_anchor != ''", "Select an actual admitted Astral Plane arrival anchor."),
        _call("srd_information_scene_entity", {"subject": "{astral_anchor}"}, POSITION_EXPORTS),
        _set("_astral_anchor", "_info_scene_entity"),
        _require(
            "_astral_anchor.active != false and _astral_anchor.metadata.plane == 'astral'",
            "The real arrival anchor must be on the Astral Plane.",
        ),
        _call(
            "record_srd_result_text",
            {"text": "{spell_cast_id}:original_body"},
            ("_recorded_result_text",),
        ),
        _set("_astral_body_id", "_recorded_result_text"),
        _call(
            "record_srd_result_text",
            {"text": "{spell_cast_id}:astral_form"},
            ("_recorded_result_text",),
        ),
        _set("_astral_form_scene_id", "_recorded_result_text"),
        {"create_entity": {"id": "_astral_body_id", "value": body_fields}},
        _set(
            "_astral_body_patch",
            {
                "participant_ref": "_astral_body_id",
                "astral_projection_source_id": "spell_cast_id",
                "astral_projection_role": "'body'",
            },
        ),
        _set("_astral_body_metadata", "merge(_astral_origin.metadata, _astral_body_patch)"),
        *_patch("{_astral_origin.id}", {"metadata": "{_astral_body_metadata}"}),
        _call("srd_information_scene_entity", {"subject": "{_astral_origin.id}"}),
        _set("_world_origin", "_astral_origin.position"),
        _set(
            "_astral_extent",
            [
                "_astral_origin.footprint.half_extents[0] * 2",
                "_astral_origin.footprint.half_extents[1] * 2",
                "_astral_origin.height",
            ],
        ),
        _set(
            "_astral_form_patch",
            {
                "participant_ref": "_caster_state_id",
                "astral_projection_source_id": "spell_cast_id",
                "astral_projection_role": "'form'",
            },
        ),
        _set("_astral_form_metadata", "merge(_astral_origin.metadata, _astral_form_patch)"),
        *_new_scene_object(
            "_astral_form_metadata",
            identity="_astral_form_scene_id",
            extent="_astral_extent",
            movement="_astral_origin.movement",
            visibility="_astral_origin.visibility",
        ),
        *_patch(
            "{_astral_form_scene_id}",
            {
                "metadata": "{_astral_form_metadata}",
                "facing": "{_astral_origin.facing}",
            },
        ),
        _call(
            "resolve_srd_travel_placement",
            {
                "subject": "{_astral_form_scene_id}",
                "destination": "{_astral_anchor.position}",
                "plane": "astral",
                "excluded": [],
            },
            ("_travel_placement",),
        ),
        _require(
            "_travel_placement.supported == true and _travel_placement.found == true",
            "The actual Astral arrival needs supported unoccupied body placement.",
        ),
        _call(
            "move_srd_travel_subject",
            {
                "subject": "{_astral_form_scene_id}",
                "destination": "{_travel_placement.position}",
                "plane": "astral",
            },
        ),
        _when(
            "state.astral_projection_sources == null",
            [
                _set("state.astral_projection_sources", {}),
                _set("state.astral_projection_source_ids", []),
            ],
        ),
        _set("_astral_ids", "state.astral_projection_source_ids"),
        _push("_astral_ids", "spell_cast_id"),
        _set("state.astral_projection_source_ids", "_astral_ids"),
        _set(
            "state.astral_projection_sources.{spell_cast_id}",
            {
                "installed": True,
                "caster_state_id": "_caster_state_id",
                "spell_id": "'astral_projection'",
                "spell_level": 9,
                "cast_id": "spell_cast_id",
                "source_caster_id": "_caster_state_id",
                "started_tick": "state.lasting_tick",
                "expires_tick": None,
                "dispel_ongoing": True,
                "ongoing_duration": False,
                "body_id": "_astral_body_id",
                "body_scene_id": "_astral_origin.id",
                "body_metadata": "_astral_origin.metadata",
                "form_scene_id": "_astral_form_scene_id",
                "silver_cord_cut": False,
            },
        ),
        _set("caster_state.astral_projection_role", "'form'"),
        _set("caster_state.astral_projection_source_id", "spell_cast_id"),
        _set("state.has_astral_projections", True),
    ]
    return _tool_scene(
        "Install only this actual paid hour's bounded self-projection.",
        [
            _entity("caster_state"),
            _parameter("spell_cast_id"),
            *deepcopy(CAST_CHOICES),
        ],
        [
            _require(
                "caster_state.last_spell_source_cast_id == spell_cast_id and "
                "caster_state.last_spell_source_spell == 'astral_projection' "
                "and spell_cast_id != ''",
                "Projection requires the genuine committed spell source and paid material.",
            ),
            _require("len(astral_companions) == 0", "Companion projection remains unsupported."),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_astral_origin", "_info_scene_entity"),
            _require("_astral_origin.metadata.plane != null", "Record this body's actual plane."),
            _set("_astral_ended_on_cast", "_astral_origin.metadata.plane == 'astral'"),
            _when("_astral_ended_on_cast != true", project),
            _set(
                "_spell_summary_rows",
                [
                    {
                        "label": "'Astral Projection'",
                        "value": "'Projection ended on the Astral Plane'",
                    }
                ],
            ),
            _when(
                "_astral_ended_on_cast != true",
                [
                    _set(
                        "_spell_summary_rows",
                        [{"label": "'Astral form'", "value": "_astral_form_scene_id"}],
                    ),
                ],
            ),
            _set("_spell_detail_rows", []),
        ],
    )


def _read():
    return _tool_scene(
        "Read this actual source's distinct body and Astral form.",
        [
            _entity("caster_state"),
        ],
        [
            _require(
                "caster_state.astral_projection_role == 'form'",
                "This logical actor has no active Astral projection.",
            ),
            _set("_astral_source_id", "caster_state.astral_projection_source_id"),
            _set("_astral_source", "state.astral_projection_sources[_astral_source_id]"),
            _require(
                "_astral_source.installed == true and "
                "_astral_source.caster_state_id == _caster_state_id",
                "Read only this actual caster's installed projection.",
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_astral_source.body_scene_id}"},
                POSITION_EXPORTS,
            ),
            _set("_astral_body_scene", "_info_scene_entity"),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_astral_source.form_scene_id}"},
                POSITION_EXPORTS,
            ),
            _set("_astral_form_scene", "_info_scene_entity"),
            _require(
                "_astral_body_scene.active != false and "
                "_astral_body_scene.metadata.participant_ref == _astral_source.body_id and "
                "_astral_form_scene.active != false and "
                "_astral_form_scene.metadata.participant_ref == _caster_state_id and "
                "_astral_form_scene.metadata.plane == 'astral'",
                "The exact original body and actual Astral form must still be admitted.",
            ),
        ],
    )


def _return_body():
    return _tool_scene(
        "Return this living bounded projection to its exact original body.",
        [
            _entity("caster_state"),
        ],
        [
            _call(
                "read_srd_astral_projection_source",
                {"caster_state": "{_caster_state_id}"},
                ("_astral_source_id", "_astral_source", "_astral_body_scene", "_astral_form_scene"),
            ),
            _require(
                "caster_state.hp > 0 and caster_state.is_alive == true and "
                "len(caster_state.conditions) == 0",
                "Dual-body damage, death and effect restoration remain unsupported.",
            ),
            _call(
                "retire_srd_astral_original_body",
                {
                    "body_state": "{_astral_source.body_id}",
                    "caster_state": "{_caster_state_id}",
                    "source_id": "{_astral_source_id}",
                },
            ),
            *_patch("{_astral_source.form_scene_id}", {"active": False}),
            _set(
                "_astral_retired_patch",
                {
                    "participant_ref": "_caster_state_id",
                    "astral_projection_role": None,
                    "astral_projection_source_id": None,
                },
            ),
            _set(
                "_astral_restored_meta", "merge(_astral_body_scene.metadata, _astral_retired_patch)"
            ),
            *_patch("{_astral_source.body_scene_id}", {"metadata": "{_astral_restored_meta}"}),
            _set("caster_state.astral_projection_role", None),
            _set("state.astral_projection_sources.{_astral_source_id}.installed", False),
        ],
    )


def extend_runtime(text):
    tools = _load_runtime(text)["tools"]
    changed = {
        "cast_srd_astral_projection": _cast(),
        "project_srd_astral_member": _member(),
        "qualify_srd_astral_companion": _companion(),
        "prepare_srd_astral_targets": _targets(),
        "observe_srd_astral_receiving": _observe(),
        "snapshot_srd_astral_pc_statistics": _pc_statistics(),
        "prepare_srd_astral_departure": _departure(),
        "settle_srd_astral_returns": _settle(),
        "read_srd_astral_projection_source": _read(),
        "read_srd_astral_projection": _public(
            _read(),
            "exploration",
            "Astral Projection",
            [
                ("Original body", "_astral_source.body_id"),
                ("Body plane", "_astral_body_scene.metadata.plane"),
                ("Form plane", "_astral_form_scene.metadata.plane"),
            ],
        ),
        "return_srd_astral_projection": _return(),
        "retire_srd_astral_original_body": _tool(
            "Retire exactly this nonautonomous body alias.",
            [
                _entity("body_state"),
                _entity("caster_state"),
                _parameter("source_id"),
            ],
            [
                _require(
                    "body_state.astral_projection_role == 'body' and "
                    "body_state.astral_projection_source_id == source_id and "
                    "body_state.logical_actor_id == _caster_state_id",
                    "Retire only this exact source's original body.",
                ),
                *[
                    _set("caster_state." + field, "body_state." + field)
                    for field in BODY_STATE_FIELDS + POSSESSION_FIELDS
                ],
                _set("_astral_body_conditions", []),
                _set("_astral_conditions", "body_state.conditions"),
                _loop(
                    "_astral_conditions",
                    "_astral_condition",
                    [
                        _when(
                            "_astral_condition != 'unconscious' or body_state.hp <= 0",
                            [_push("_astral_body_conditions", "_astral_condition")],
                        ),
                    ],
                ),
                _set("caster_state.conditions", "_astral_body_conditions"),
                _set("body_state.astral_projection_role", "'retired_body'"),
                _set("body_state.is_alive", False),
                _set("body_state.suspended_animation", False),
            ],
        ),
    }
    dismiss = _return()
    dismiss["effects"][:0] = _action()
    changed["dismiss_srd_astral_projection"] = _public(
        dismiss,
        "spells",
        "Dismiss Astral Projection",
        [("Original body", "_astral_source.body_id")],
    )
    changed = {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    return _replace_tools(text, changed) if changed else text


def _bind_paid_owners(text):
    """Join expanded paid owners; unsupported two-body consequences reject before mutation."""
    text = extend_runtime(text)
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        cast = deepcopy(tools[name])
        if any(e.get("call") == "cast_spell" for e in cast["effects"]):
            continue
        if "cast_srd_astral_projection" not in str(cast["effects"]):
            present = {p["name"] for p in cast["parameters"]}
            cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in present]
            index = next(
                i
                for i, e in enumerate(cast["effects"])
                if e.get("set", {}).get("field") == "_card_slots_after"
            )
            cast["effects"].insert(
                index,
                _when(
                    "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
                    "_spell_definition.family == 'astral_projection'",
                    [
                        _require(
                            "_scalar_target_supplied != true or "
                            "_scalar_target_id == _caster_state_id",
                            "This bounded Astral Projection only projects its caster.",
                        ),
                        _require(
                            "targets == null or len(targets) == 0",
                            "Companion projection remains unsupported.",
                        ),
                        _when(
                            "_spell_source_cast_id == null or _spell_source_cast_id == ''",
                            [
                                _call(
                                    "begin_srd_spell_source_instance",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "spell": "astral_projection",
                                        "slot_level": "{_slot_level}",
                                    },
                                    ("_spell_source_cast_id",),
                                ),
                            ],
                        ),
                        _call(
                            "cast_srd_astral_projection",
                            {
                                "caster_state": "{_caster_state_id}",
                                "spell_cast_id": "{_spell_source_cast_id}",
                                "astral_anchor": "{astral_anchor}",
                                "astral_companions": "{astral_companions}",
                            },
                            RESULTS,
                        ),
                    ],
                ),
            )
            changed[name] = cast
    blocked = "No receiving or further casting is supported for this bounded two-form projection."
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        "begin_srd_spell_action",
        "begin_srd_extended_spell_action",
    ):
        if name not in tools:
            continue
        tool = deepcopy(changed.get(name, tools[name]))
        fields = {p["name"] for p in tool["parameters"]}
        conditions = []
        if "caster_state" in fields:
            tool["pre_conditions"] = [
                clause
                for clause in tool["pre_conditions"]
                if not (
                    clause.get("error") == blocked
                    and clause["condition"].startswith("caster_state.")
                    and clause["condition"] != "caster_state.astral_projection_role != 'body'"
                )
            ]
            conditions += ["caster_state.astral_projection_role != 'body'"]
        if "target" in fields:
            conditions += [
                "target == null or (target.astral_projection_role != 'form' and "
                "target.astral_projection_role != 'body')"
            ]
        for condition in conditions:
            clause = {"condition": condition, "error": blocked}
            if clause not in tool["pre_conditions"]:
                tool["pre_conditions"].append(clause)
                changed[name] = tool
    for name in (
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "apply_srd_qualifying_damage",
        "apply_srd_qualifying_composite_damage",
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        clause = {
            "condition": "target.astral_projection_role != 'form' and "
            "target.astral_projection_role != 'body'",
            "error": blocked,
        }
        if clause not in tool["pre_conditions"]:
            tool["pre_conditions"].append(clause)
            changed[name] = tool
    for name in (
        "apply_srd_condition",
        "remove_srd_condition",
        "install_srd_timed_condition_source",
        "install_srd_timed_actor_defense",
        "install_srd_timed_senses",
        "install_srd_timed_actor_mobility",
        "install_srd_timed_hit_point_bonus",
        "install_srd_timed_armor_class_floor",
        "install_srd_timed_speed_bonus",
        "install_srd_timed_death_prevention",
        "cast_spell_healing",
        "restore_shared_spell_healing_target",
        "restore_srd_healing_pool_target",
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        targets = [
            p["name"]
            for p in tool["parameters"]
            if p.get("resolve") == "entity" and p["name"] in {"target", "target_state"}
        ]
        for target in targets:
            clause = {
                "condition": f"{target} == null or "
                f"({target}.astral_projection_role != 'form' and "
                f"{target}.astral_projection_role != 'body')",
                "error": blocked,
            }
            if clause not in tool["pre_conditions"]:
                tool["pre_conditions"].append(clause)
                changed[name] = tool
    mover_name = (
        "move_srd_travel_subject_node"
        if "move_srd_travel_subject_node" in tools
        else "move_srd_travel_subject"
    )
    mover = deepcopy(tools[mover_name])
    if "Astral departure" not in str(mover["effects"]):
        index = next(
            i
            for i, e in enumerate(mover["effects"])
            if e.get("set", {}).get("field") == "_travel_mover"
        )
        mover["effects"].insert(
            index + 1,
            _require(
                "_travel_mover.metadata.astral_projection_role != 'form' or plane == 'astral'",
                "Astral departure with original-body transport remains unsupported.",
            ),
        )
        changed[mover_name] = mover
    return _replace_tools(text, changed) if changed else text


def _rewrite(value, replacements):
    """Bind the existing projection implementation to another resolved target."""
    import re

    if isinstance(value, str):
        return re.sub(
            r"\b(" + "|".join(replacements) + r")\b",
            lambda match: replacements[match.group()],
            value,
        )
    if isinstance(value, list):
        return [_rewrite(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite(item, replacements) for key, item in value.items()}
    return value


def _member():
    """Use the same body/form constructor for one actually paid selected target."""
    original = _single_projection_template()
    project = next(
        e["on_true"]
        for e in original["effects"]
        if e.get("condition") == "_astral_ended_on_cast != true"
    )
    project = _rewrite(
        project,
        {
            "caster_state": "actor_state",
            "_caster_state_id": "_actor_state_id",
            "spell_cast_id": "projection_source_id",
        },
    )
    body = next(e["create_entity"]["value"] for e in project if "create_entity" in e)
    for field in (
        "ability_scores",
        "ability_modifiers",
        "saving_throws",
        "saving_throw_modifiers",
        "condition_immunities",
        "proficiency_bonus",
        "native_actor_profile",
    ):
        body[field] = "actor_state." + field
    body["saving_throw_modifiers"] = "_astral_saved_saves"
    body["ability_scores"] = "_astral_saved_abilities"
    body["astral_original_pc_id"] = "_astral_original_pc_id"
    create_index = next(i for i, effect in enumerate(project) if "create_entity" in effect)
    project[create_index:create_index] = [
        _set("_astral_saved_saves", "actor_state.saving_throw_modifiers"),
        _set("_astral_saved_abilities", "actor_state.ability_scores"),
        _set("_astral_original_pc_id", None),
        _when(
            "actor_state.creature_kind == 'character'",
            [
                _call(
                    "snapshot_srd_astral_pc_statistics",
                    {
                        "actor_state": "{_actor_state_id}",
                        "actor_character": "{_actor_state_id}",
                    },
                    ("_astral_saved_saves", "_astral_saved_abilities", "_astral_original_pc_id"),
                ),
            ],
        ),
    ]
    source = next(
        e["set"]["value"]
        for e in project
        if e.get("set", {}).get("field")
        == "lasting_state.astral_projection_sources.{projection_source_id}"
    )
    source.update(cast_id="spell_cast_id", source_caster_id="_caster_state_id", return_queued=False)
    return _tool_scene(
        "Project one member of this genuine paid source, without another cost.",
        [
            _entity("caster_state"),
            _entity("actor_state"),
            _parameter("spell_cast_id"),
            _parameter("projection_source_id"),
            _parameter("astral_anchor"),
        ],
        [
            _require(
                "caster_state.last_spell_source_cast_id == spell_cast_id and "
                "caster_state.last_spell_source_spell == 'astral_projection' and "
                "_actor_state_id in caster_state.astral_cast_targets",
                "Project only a target selected by this actual committed cast.",
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_actor_state_id}"}, POSITION_EXPORTS
            ),
            _set("_astral_origin", "_info_scene_entity"),
            *project,
        ],
    )


def _companion():
    return _tool(
        "Qualify an actual willing companion in the printed ten-foot range.",
        [
            _entity("caster_state"),
            _entity("actor_state"),
        ],
        [
            *_rewrite(
                _plain_actor(),
                {"caster_state": "actor_state", "_caster_state_id": "_actor_state_id"},
            ),
            _call(
                "resolve_srd_scene_relation",
                {
                    "actor_state": "{_caster_state_id}",
                    "target_state": "{_actor_state_id}",
                },
                ("_scene_distance", "_scene_same_plane"),
            ),
            _require(
                "_scene_same_plane == true and _scene_distance <= 10",
                "Astral companions must be within ten feet on the caster's plane.",
            ),
        ],
    )


def _pc_statistics():
    """Snapshot receive-only PC abilities/saves from the genuine validated original file."""
    effects = [
        _require(
            "actor_character.validation_status == 'validated' and "
            "actor_character.package_id == 'wotc.srd.5.2.1' and "
            "actor_character.kind == 'player_character' and "
            "_actor_character_id == _actor_state_id and "
            "actor_state.creature_kind == 'character' and "
            "actor_character.data.level >= 1 and actor_character.data.level <= 20",
            "The physical snapshot requires this actor's actual validated Character File.",
        ),
        _require(
            "actor_state.has_recovery_ability_reductions != true and "
            "actor_state.has_actor_spell_modifiers != true",
            "Replicating active ability/save alterations remains unsupported.",
        ),
        _set("_astral_saved_abilities", "actor_character.data.abilities"),
        _set("_astral_saved_saves", {}),
        _set("_astral_original_pc_id", "_actor_character_id"),
    ]
    for ability in ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"):
        effects.extend(
            [
                _set(
                    "_astral_save_modifier", f"(actor_character.data.abilities.{ability} - 10) // 2"
                ),
                _when(
                    f"'{ability}' in actor_character.data.saving_throw_proficiencies",
                    [
                        _set(
                            "_astral_save_modifier",
                            "_astral_save_modifier + 2 + (actor_character.data.level - 1) // 4",
                        ),
                    ],
                ),
                _when(
                    f"actor_state.saving_throw_modifiers.{ability} != null",
                    [
                        _set(
                            "_astral_save_modifier", f"actor_state.saving_throw_modifiers.{ability}"
                        ),
                    ],
                ),
                _set("_astral_save_patch", {ability: "_astral_save_modifier"}),
                _set("_astral_saved_saves", "merge(_astral_saved_saves, _astral_save_patch)"),
            ]
        )
    return _tool(
        "Retain an authenticated physical PC snapshot for native receiving only.",
        [
            _entity("actor_state"),
            _parameter("actor_character", resolve="participant"),
        ],
        effects,
    )


def _targets():
    return _tool(
        "Derive target identities and material quantities before payment.",
        [
            _entity("caster_state"),
            deepcopy(CAST_CHOICES[1]),
            _optional("companions_willing", "boolean", False),
        ],
        [
            *_possession_requirements(),
            _require(
                "len(astral_companions) == 0 or companions_willing == true",
                "Astral Projection requires willing companions.",
            ),
            _require(
                "_caster_state_id not in astral_companions",
                "The caster is already included among Astral targets.",
            ),
            _set("_astral_targets", ["_caster_state_id"]),
            _loop(
                "astral_companions",
                "_astral_companion",
                [
                    _call(
                        "qualify_srd_astral_companion",
                        {
                            "caster_state": "{_caster_state_id}",
                            "actor_state": "{_astral_companion}",
                        },
                    ),
                    _push("_astral_targets", "_astral_companion"),
                ],
            ),
            _set("_astral_material_target_count", "len(_astral_targets)"),
        ],
    )


def _cast():
    return _tool_scene(
        "Project the caster and actual paid companions through the same owner.",
        [
            _entity("caster_state"),
            _parameter("spell_cast_id"),
            *deepcopy(CAST_CHOICES),
            _optional("companions_willing", "boolean", False),
        ],
        [
            _require(
                "caster_state.last_spell_source_cast_id == spell_cast_id and "
                "caster_state.last_spell_source_spell == 'astral_projection' "
                "and spell_cast_id != ''",
                "Projection requires this genuine paid source identity.",
            ),
            _call(
                "prepare_srd_astral_targets",
                {
                    "caster_state": "{_caster_state_id}",
                    "astral_companions": "{astral_companions}",
                    "companions_willing": "{companions_willing}",
                },
                ("_astral_targets",),
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _require("_info_scene_entity.metadata.plane != null", "Record the actual body plane."),
            _set("_astral_ended_on_cast", "_info_scene_entity.metadata.plane == 'astral'"),
            _when(
                "_astral_ended_on_cast != true",
                [
                    _set("caster_state.astral_projection_cast_id", "spell_cast_id"),
                    _set("caster_state.astral_cast_targets", "_astral_targets"),
                    _loop(
                        "_astral_targets",
                        "_astral_projected",
                        [
                            _call(
                                "record_srd_result_text",
                                {
                                    "text": "{spell_cast_id}:{_astral_projected}",
                                },
                                ("_recorded_result_text",),
                            ),
                            _set("_astral_member_id", "_recorded_result_text"),
                            _when(
                                "_astral_projected == _caster_state_id",
                                [
                                    _set("_astral_member_id", "spell_cast_id"),
                                ],
                            ),
                            _call(
                                "project_srd_astral_member",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "actor_state": "{_astral_projected}",
                                    "spell_cast_id": "{spell_cast_id}",
                                    "projection_source_id": "{_astral_member_id}",
                                    "astral_anchor": "{astral_anchor}",
                                },
                            ),
                        ],
                    ),
                    _set("caster_state.astral_cast_targets", None),
                ],
            ),
            _set(
                "_spell_summary_rows",
                [{"label": "'Astral targets'", "value": "len(_astral_targets)"}],
            ),
            _set("_spell_detail_rows", []),
        ],
    )


def _return():
    tool = _return_body()
    tool["effects"] = [e for e in tool["effects"] if "Dual-body damage" not in str(e)]
    tool["effects"].extend(
        [
            _set("caster_state.astral_projection_source_id", None),
            _set("state.astral_projection_sources.{_astral_source_id}.return_queued", False),
        ]
    )
    return tool


def _observe():
    return _tool(
        "Queue this actual body's or form's zero-HP return after native receiving.",
        [
            _entity("target"),
        ],
        [
            _when(
                "target.astral_projection_role == 'body' or "
                "target.astral_projection_role == 'form'",
                [
                    _set("_astral_hit_source_id", "target.astral_projection_source_id"),
                    _set(
                        "_astral_hit_source",
                        "state.astral_projection_sources[_astral_hit_source_id]",
                    ),
                    _require(
                        "_astral_hit_source.installed == true and "
                        "(_target_id == _astral_hit_source.body_id or "
                        "_target_id == _astral_hit_source.caster_state_id)",
                        "Receive only through the actual source's body or form identity.",
                    ),
                    _when(
                        "target.astral_projection_role == 'body' and target.hp > 0 and "
                        "'unconscious' not in target.conditions",
                        [
                            _set("_astral_suspended_conditions", "target.conditions"),
                            _push("_astral_suspended_conditions", "'unconscious'"),
                            _set("target.conditions", "_astral_suspended_conditions"),
                        ],
                    ),
                    _when(
                        "target.hp <= 0",
                        [
                            _set(
                                "state.astral_projection_sources.{_astral_hit_source_id}.return_queued",
                                True,
                            ),
                            _set("state.has_astral_pending_returns", True),
                        ],
                    ),
                ],
            ),
        ],
    )


def _settle():
    return _tool(
        "Return only genuine zero-HP members after their native action completes.",
        [],
        [
            _set("_astral_return_ids", "state.astral_projection_source_ids"),
            _loop(
                "_astral_return_ids",
                "_astral_return_id",
                [
                    _set(
                        "_astral_return_source",
                        "state.astral_projection_sources[_astral_return_id]",
                    ),
                    _when(
                        "_astral_return_source.installed == true and "
                        "_astral_return_source.return_queued == true",
                        [
                            _call(
                                "return_srd_astral_projection",
                                {
                                    "caster_state": "{_astral_return_source.caster_state_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            _set("state.has_astral_pending_returns", False),
        ],
    )


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def bind_runtime_consumers(text):
    """Join target-count payment and native receiving, preserving all existing suffixes."""
    text = _bind_paid_owners(text)
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name, original in tools.items():
        tool = deepcopy(original)
        for effects in _lists(tool["effects"]):
            effects[:] = [
                e
                for e in effects
                if e.get("require", {}).get("error")
                not in {
                    "This bounded Astral Projection only projects its caster.",
                    "Companion projection remains unsupported.",
                }
            ]
            for index, effect in list(enumerate(effects)):
                if effect.get("call") == "begin_srd_spell_cast_internal":
                    fields = {p["name"] for p in tool["parameters"]}
                    tool["parameters"] += [
                        deepcopy(p) for p in CAST_CHOICES if p["name"] not in fields
                    ]
                    if "all_targets_willing" not in fields:
                        tool["parameters"].append(
                            _optional("all_targets_willing", "boolean", False)
                        )
                    effect["params"].update(
                        astral_anchor="{astral_anchor}",
                        astral_companions="{astral_companions}",
                        all_targets_willing="{all_targets_willing}",
                    )
                if effect.get("set", {}).get("field") == "caster_state.extended_casting":
                    record = effect["set"]["value"]
                    if isinstance(record, dict):
                        record["material_target_count"] = "_material_target_count"
                        record["astral_companions"] = "astral_companions"
                        record["astral_companions_willing"] = "all_targets_willing"
                if effect.get("call") == "cast_srd_astral_projection":
                    effect["params"]["companions_willing"] = "{all_targets_willing}"
                if effect.get("call") == "resolve_srd_spell_materials":
                    if "_astral_material_target_count" in str(effects):
                        continue
                    fields = {p["name"] for p in tool["parameters"]}
                    tool["parameters"] += [
                        deepcopy(p) for p in CAST_CHOICES if p["name"] not in fields
                    ]
                    if "all_targets_willing" not in fields:
                        tool["parameters"].append(
                            _optional("all_targets_willing", "boolean", False)
                        )
                    continuing = name == "continue_srd_spell_cast"
                    choice = (
                        "caster_state.extended_casting.astral_companions"
                        if continuing
                        else "astral_companions"
                    )
                    willing = (
                        "caster_state.extended_casting.astral_companions_willing"
                        if continuing
                        else "all_targets_willing"
                    )
                    actual_spell = "caster_state.extended_casting.spell" if continuing else "spell"
                    effects.insert(
                        index,
                        _when(
                            actual_spell + " == 'astral_projection'",
                            [
                                _call(
                                    "prepare_srd_astral_targets",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "astral_companions": "{" + choice + "}",
                                        "companions_willing": "{" + willing + "}",
                                    },
                                    ("_astral_material_target_count",),
                                ),
                                _set("_material_target_count", "_astral_material_target_count"),
                            ],
                        ),
                    )
        # Existing native HP and condition owners retain their own immunity/lifecycle logic.
        receiving = {
            "apply_srd_damage",
            "apply_srd_composite_damage",
            "apply_srd_qualifying_damage",
            "apply_srd_qualifying_composite_damage",
            "cast_spell",
            "cast_spell_outside_combat",
            "begin_srd_spell_action",
            "begin_srd_extended_spell_action",
            "begin_srd_spell_cast",
            "begin_srd_spell_cast_internal",
            "apply_srd_condition",
            "remove_srd_condition",
            "cast_spell_healing",
            "restore_shared_spell_healing_target",
            "restore_srd_healing_pool_target",
        }
        if name in receiving:
            tool["pre_conditions"] = [
                p
                for p in tool["pre_conditions"]
                if not (
                    p.get("error", "").startswith("No receiving")
                    and not p["condition"].startswith("caster_state.")
                )
            ]
            for clause in tool["pre_conditions"]:
                if clause.get("error", "").startswith("No receiving") and clause[
                    "condition"
                ].startswith("caster_state."):
                    clause["condition"] = "caster_state.astral_projection_role != 'body'"
        if name in {
            "apply_srd_damage",
            "apply_srd_composite_damage",
            "apply_srd_qualifying_damage",
            "apply_srd_qualifying_composite_damage",
            "execute_srd_qualified_scalar_packet",
            "execute_srd_qualified_composite_packet",
        }:
            for effects in _lists(tool["effects"]):
                for effect in effects:
                    condition = effect.get("condition", "")
                    if (
                        isinstance(condition, str)
                        and "target.creature_kind" in condition
                        and ("target.hp == 0" in condition or "_hp_before == 0" in condition)
                        and "target.astral_projection_role" not in condition
                        and "'field': 'target.is_alive'" in str(effect)
                    ):
                        # Zero HP ends the form; the body's ordinary death owner remains intact.
                        effect["condition"] = (
                            "(" + condition + ") and target.astral_projection_role != 'form'"
                        )
            if not any(e.get("call") == "observe_srd_astral_receiving" for e in tool["effects"]):
                tool["effects"].append(
                    _call("observe_srd_astral_receiving", {"target": "{target}"})
                )
        if name in {
            "restore_shared_spell_healing_target",
            "restore_srd_healing_pool_target",
        } and not any(e.get("call") == "observe_srd_astral_receiving" for e in tool["effects"]):
            actual = next(
                p["name"]
                for p in tool["parameters"]
                if p.get("resolve") == "entity" and p["name"] in {"target", "target_state"}
            )
            tool["effects"].append(
                _call("observe_srd_astral_receiving", {"target": "{" + actual + "}"})
            )
        if name == "dismiss_srd_astral_projection":
            tool = _tool_scene(
                "The real caster dismisses every member of this actual cast.",
                [_entity("caster_state"), _optional("source_id", default="")],
                [
                    _set("_astral_dismiss_cast_id", "source_id"),
                    _when(
                        "source_id == ''",
                        [
                            _set(
                                "_astral_dismiss_cast_id", "caster_state.astral_projection_cast_id"
                            ),
                        ],
                    ),
                    _set("_astral_dismiss_ids", "state.astral_projection_source_ids"),
                    _set("_astral_can_dismiss", False),
                    _loop(
                        "_astral_dismiss_ids",
                        "_astral_selected_id",
                        [
                            _set(
                                "_astral_selected",
                                "state.astral_projection_sources[_astral_selected_id]",
                            ),
                            _when(
                                "_astral_selected.installed == true and "
                                "_astral_selected.source_caster_id == _caster_state_id and "
                                "_astral_selected.cast_id == _astral_dismiss_cast_id",
                                [
                                    _set("_astral_can_dismiss", True),
                                ],
                            ),
                        ],
                    ),
                    _require(
                        "_astral_can_dismiss == true",
                        "Select this caster's actual active projection.",
                    ),
                    *_action(),
                    _loop(
                        "_astral_dismiss_ids",
                        "_astral_dismiss_id",
                        [
                            _set(
                                "_astral_dismiss_source",
                                "state.astral_projection_sources[_astral_dismiss_id]",
                            ),
                            _when(
                                "_astral_dismiss_source.installed == true and "
                                "_astral_dismiss_source.source_caster_id == _caster_state_id and "
                                "_astral_dismiss_source.cast_id == _astral_dismiss_cast_id",
                                [
                                    _call(
                                        "return_srd_astral_projection",
                                        {
                                            "caster_state": (
                                                "{_astral_dismiss_source.caster_state_id}"
                                            ),
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )
            tool = _public(tool, "spells", "Dismiss Astral Projection", [])
        if tool != original:
            changed[name] = tool
    return _replace_tools(text, changed) if changed else text


def bind_completed_returns(text, public_tools):
    """Attach queued returns to completed native actions, outside every settlement descendant."""
    if __package__:
        from .srd_spell_exile_composition import _reachable
    else:
        from srd_spell_exile_composition import _reachable
    tools = _load_runtime(text)["tools"]
    graph = {
        name: {e["call"] for effects in _lists(tool["effects"]) for e in effects if "call" in e}
        for name, tool in tools.items()
    }
    within = _reachable(graph, {"settle_srd_astral_returns"})
    changed = {}
    for name in set(public_tools) & tools.keys() - within:
        if not _reachable(graph, {name}) & {"observe_srd_astral_receiving"}:
            continue
        tool = deepcopy(tools[name])
        if "settle_srd_astral_returns" in str(tool["effects"]):
            continue
        suffix = _tool(
            tool["description"],
            tool["parameters"],
            [
                _when(
                    "state.has_astral_pending_returns == true",
                    [_call("settle_srd_astral_returns", {})],
                ),
            ],
        )
        tool["effects"].extend(suffix["effects"])
        changed[name] = tool
    return _replace_tools(text, changed) if changed else text


def _departure():
    """Reunite the genuine physical body through the already-qualified Travel mutation."""
    return _tool_scene(
        "Prepare this exact Source's living original body for its form's actual departure.",
        [
            _entity("caster_state"),
            _parameter("destination", "array", items={"type": "number"}, min_items=3, max_items=3),
            _parameter("plane"),
        ],
        [
            _call(
                "read_srd_astral_projection_source",
                {"caster_state": "{_caster_state_id}"},
                ("_astral_source_id", "_astral_source", "_astral_body_scene", "_astral_form_scene"),
            ),
            _require(
                "plane != 'astral' and plane != ''", "An actual departure needs another plane."
            ),
            _require(
                "_astral_body_scene.footprint.kind == 'box' and "
                "_astral_form_scene.footprint.kind == 'box' and "
                "_astral_body_scene.height == _astral_form_scene.height and "
                "_astral_body_scene.footprint.half_extents[0] == "
                "_astral_form_scene.footprint.half_extents[0] and "
                "_astral_body_scene.footprint.half_extents[1] == "
                "_astral_form_scene.footprint.half_extents[1]",
                "Changed physical/form geometry needs its own destination "
                "permission qualification.",
            ),
            _call(
                "resolve_srd_travel_placement",
                {
                    "subject": "{_astral_source.body_scene_id}",
                    "destination": "{destination}",
                    "plane": "{plane}",
                    "excluded": [],
                },
                ("_travel_placement",),
            ),
            _require(
                "_travel_placement.supported == true and _travel_placement.found == true and "
                "_travel_placement.distance == 0",
                "The genuine original body must fit the actual qualified departure destination.",
            ),
            _call(
                "retire_srd_astral_original_body",
                {
                    "body_state": "{_astral_source.body_id}",
                    "caster_state": "{_caster_state_id}",
                    "source_id": "{_astral_source_id}",
                },
            ),
            _require(
                "caster_state.hp > 0 and caster_state.is_alive == true",
                "A zero-HP physical body must finish its genuine return before departure.",
            ),
            *_patch("{_astral_source.form_scene_id}", {"active": False}),
            _set(
                "_astral_departure_clear",
                {
                    "participant_ref": "_caster_state_id",
                    "astral_projection_role": None,
                    "astral_projection_source_id": None,
                },
            ),
            _set(
                "_astral_departure_metadata",
                "merge(_astral_body_scene.metadata, _astral_departure_clear)",
            ),
            _set("_astral_departure_row_patch", {"metadata": "_astral_departure_metadata"}),
            _set(
                "_astral_departure_mover", "merge(_astral_body_scene, _astral_departure_row_patch)"
            ),
            _set("caster_state.astral_projection_role", None),
            _set("caster_state.astral_projection_source_id", None),
            _set("state.astral_projection_sources.{_astral_source_id}.installed", False),
            _set("state.astral_projection_sources.{_astral_source_id}.return_queued", False),
        ],
    )


def bind_departure_consumers(text):
    """Bind after final Travel guards/sole node; retain its one mutation and observer ABI."""
    tools = _load_runtime(text)["tools"]
    name = (
        "move_srd_travel_subject_node"
        if "move_srd_travel_subject_node" in tools
        else "move_srd_travel_subject"
    )
    mover = deepcopy(tools[name])
    marker = "prepare_srd_astral_departure"
    for effects in _lists(mover["effects"]):
        effects[:] = [
            effect
            for effect in effects
            if effect.get("require", {}).get("error")
            != "Astral departure with original-body transport remains unsupported."
        ]
    mover["effects"] = [effect for effect in mover["effects"] if marker not in str(effect)]
    mutations = [i for i, e in enumerate(mover["effects"]) if "spatial_mutation" in e]
    if len(mutations) != 1:
        raise ValueError("Astral departure needs one genuine final Travel mutation.")
    index = mutations[0]
    mutation = mover["effects"][index]["spatial_mutation"]
    if mutation["target"] != {"kind": "entity", "id": "{_travel_mover.id}"} or mutation[
        "patch"
    ] != {"position": "{destination}", "metadata": "{_travel_metadata}"}:
        raise ValueError("Astral departure must retain the actual Travel mutation ABI.")
    mover["effects"].insert(
        index,
        _when(
            "_travel_mover.metadata.astral_projection_role == 'form' and plane != 'astral'",
            [
                _call(
                    marker,
                    {
                        "caster_state": "{_travel_mover.metadata.participant_ref}",
                        "destination": "{destination}",
                        "plane": "{plane}",
                    },
                    ("_astral_departure_mover", "_astral_departure_metadata"),
                ),
                _set("_travel_mover", "_astral_departure_mover"),
                _set("_info_scene_entity", "_travel_mover"),
                _set("_info_position", "_travel_mover.position"),
                _set("_astral_departure_patch", {"previous_position": "_travel_mover.position"}),
                _set("_travel_patch", "merge(_travel_patch, _astral_departure_patch)"),
                _set("_travel_metadata", "merge(_astral_departure_metadata, _travel_patch)"),
                _set("_travel_guard_target", "_travel_mover.metadata.participant_ref"),
                *[
                    _when(variable + " != null", [_set(variable, value)])
                    for variable, value in (
                        ("_barrier_raw_scene_subject", "_travel_mover.id"),
                        ("_zone_segment_actor", "_travel_mover"),
                        ("_symbol_entry_previous_position", "_travel_mover.position"),
                        ("_symbol_entry_previous_plane", "_travel_mover.metadata.plane"),
                    )
                ],
            ],
        ),
    )
    changed = {name: mover, marker: _departure()}
    changed = {key: value for key, value in changed.items() if tools.get(key) != value}
    return _replace_tools(text, changed) if changed else text
