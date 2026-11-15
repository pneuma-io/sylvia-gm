"""Clone retains one logical individual and exact physical vessel/body provenance."""

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
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from .srd_spell_object_content import _metadata_patch
    from .srd_spell_object_targets import _lookup_object
    from .srd_timed_senses import _public as _emit_public
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
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from srd_spell_object_content import _metadata_patch
    from srd_spell_object_targets import _lookup_object
    from srd_timed_senses import _public as _emit_public
    from srd_timed_senses import _replace_tools


PROFILES = {
    "clone": {
        "family": "body_soul_spell",
        "base_level": 8,
        "casting_time": "1_hour",
        "range_kind": "touch",
        "range_feet": 0,
        "concentration": False,
        "maturation_rounds": 1728000,
    },
}
PUBLIC_TOOLS = {
    "record_srd_body_vessel",
    "record_srd_soul_disposition",
    "operate_srd_clone_vessel",
    "read_srd_clone_body",
    "select_srd_clone_body",
    "settle_srd_clone_transfer",
}
CAST_CHOICES = [
    _param("body_vessel_id", required=False, default=""),
    _param("clone_age", "enum", required=False, default="same", enum_values=["same", "younger"]),
]


def _public(tool, fields=None):
    """Present the actor/body identity and actual owned result on an ordinary public card."""
    first = tool["parameters"][0]
    subject = "_" + first["name"] + "_id" if first.get("resolve") == "entity" else first["name"]
    return _emit_public(
        tool, "exploration", "Body and soul", [("Subject", subject), *(fields or [])]
    )


def validate_profiles(definitions):
    """The physical source constants must stay equal to the pinned Clone record."""
    for spell, definition in definitions.items():
        if definition.get("family") == "body_soul_spell" and definition != PROFILES.get(spell):
            raise ValueError(f"{spell} differs from its pinned body/soul profile")


def _schema():
    return {
        "type": "object",
        "properties": {
            k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
            for k, v in PROFILES["clone"].items()
            if k != "family"
        },
        "required": sorted(set(PROFILES["clone"]) - {"family"}),
        "additionalProperties": False,
    }


def _clock():
    return _when("lasting_state.lasting_tick == null", [_set("lasting_state.lasting_tick", 0)])


def _vessel_lookup():
    return [
        *_lookup_object(),
        _require(
            "_object_scene.metadata.srd_body_vessel != null", "Admit this actual body vessel first."
        ),
        _set("_body_vessel", "_object_scene.metadata.srd_body_vessel"),
    ]


def _persist_clone_body():
    return [
        _set("_clone_metadata_change", {"srd_clone_body": "_clone_body"}),
        _set("_clone_metadata", "merge(_clone_vessel_scene.metadata, _clone_metadata_change)"),
        *_snapshot(),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "entity", "id": "{_clone_vessel_scene.id}"},
                "patch": {"metadata": "{_clone_metadata}"},
            },
            "store_as": "_clone_body_write",
        },
    ]


def _capture_clone_physical_body():
    """Capture the actual casting body, separately from later logical abilities and progression."""
    effects = [
        _set(
            "_clone_physical_body",
            {
                "footprint": "_info_scene_entity.footprint",
                "height": "_info_scene_entity.height",
                "size_rank": None,
                "size_category": None,
                "species": None,
                "species_lineage": None,
                "appearance": None,
            },
        )
    ]
    for field in ("size_rank", "size_category", "species", "species_lineage"):
        effects.append(
            _when(
                "target_state." + field + " != null",
                [
                    _set("_clone_physical_field", {field: "target_state." + field}),
                    _set(
                        "_clone_physical_body",
                        "merge(_clone_physical_body, _clone_physical_field)",
                    ),
                ],
            )
        )
    effects.append(
        _when(
            "_info_scene_entity.metadata.appearance != null",
            [
                _set(
                    "_clone_physical_field",
                    {"appearance": "_info_scene_entity.metadata.appearance"},
                ),
                _set(
                    "_clone_physical_body",
                    "merge(_clone_physical_body, _clone_physical_field)",
                ),
            ],
        )
    )
    return effects


def _restore_clone_physical_body():
    """Project saved physical size without rolling back the individual's current capabilities."""
    effects = [_set("target_state.current_body_physical_profile", "_clone_body.physical_profile")]
    for field in ("size_rank", "size_category"):
        effects.append(
            _when(
                "_clone_body.physical_profile." + field + " != null",
                [_set("target_state." + field, "_clone_body.physical_profile." + field)],
            )
        )
    return effects


def _tools():
    tools = {}
    tools["record_srd_body_vessel"] = _public(
        _tool_scene(
            "Bind actual prepared Clone vessel material to its scene object and physical interior.",
            [
                _entity("caster_state"),
                _param("object_id"),
                _param("material_item_id"),
                _param(
                    "interior_feet", "array", items={"type": "number"}, min_items=3, max_items=3
                ),
                _param("sealed", "boolean"),
            ],
            [
                _require(
                    "combat.encounter_active != true", "Admit physical vessel facts outside combat."
                ),
                *_lookup_object(),
                _require(
                    "_object_scene.metadata.srd_body_vessel == null",
                    "This vessel already has physical provenance.",
                ),
                _require(
                    "_object_scene.footprint.kind == 'box'",
                    "Use an admitted bounded physical vessel.",
                ),
                *[
                    _require(
                        f"interior_feet[{i}] > 0", "Vessel interior dimensions must be positive."
                    )
                    for i in range(3)
                ],
                *[
                    _require(
                        f"interior_feet[{i}] <= _object_scene.footprint.half_extents[{i}] * 2",
                        "Interior cannot exceed actual exterior bounds.",
                    )
                    for i in range(2)
                ],
                _require(
                    "interior_feet[2] <= _object_scene.height",
                    "Interior cannot exceed actual vessel height.",
                ),
                _set("_body_material", None),
                _set("_body_materials", []),
                _when(
                    "caster_state.spell_materials != null",
                    [_set("_body_materials", "caster_state.spell_materials")],
                ),
                _each(
                    "_body_materials",
                    "_body_lot",
                    [
                        _when(
                            "_body_lot.id == material_item_id",
                            [
                                _require(
                                    "_body_material == null", "Material identity is ambiguous."
                                ),
                                _set("_body_material", "_body_lot"),
                            ],
                        ),
                    ],
                ),
                _require(
                    "_body_material != null and _body_material.quantity >= 1 and "
                    "_body_material.value_cp >= 200000 and 'clone.2' in "
                    "_body_material.components and "
                    "_body_material.kind == 'specific' and _body_material.inventory_item in"
                    " caster_state.inventory",
                    "Use the actual possessed sealable Clone vessel worth at least 2,000 GP.",
                ),
                *_metadata_patch(
                    {
                        "srd_body_vessel": {
                            "owner": "_caster_state_id",
                            "material_id": "material_item_id",
                            "inventory_item": "_body_material.inventory_item",
                            "interior_feet": "interior_feet",
                            "sealed": "sealed",
                            "disturbed": False,
                        }
                    }
                ),
            ],
        )
    )
    tools["record_srd_soul_disposition"] = _public(
        _tool(
            (
                "Record the actual individual's free and willing soul facts without asserting "
                "death or revival."
            ),
            [_entity("target_state"), _param("free", "boolean"), _param("willing", "boolean")],
            [
                _require(
                    "target_state.created_disappearance != 'pocket'",
                    "A pocket disappearance is not a soul-death event.",
                ),
                _set("target_state.soul_free", "free"),
                _set("target_state.soul_willing", "willing"),
            ],
        )
    )
    tools["operate_srd_clone_vessel"] = _public(
        _tool_scene(
            (
                "Actually open or close one reachable Clone vessel; opening disturbs its "
                "retained body."
            ),
            [
                _entity("actor_state"),
                _param("object_id"),
                _param("operation", "enum", enum_values=["open", "close"]),
            ],
            [
                *_vessel_lookup(),
                _call(
                    "resolve_srd_object_content_geometry",
                    {
                        "caster_state": "{_actor_state_id}",
                        "object_id": "{object_id}",
                        "range_feet": 5,
                        "requires_sight": False,
                    },
                    ("_world_distance",),
                ),
                _when(
                    "combat.encounter_active == true",
                    [
                        _require(
                            "combat.active_actor == _actor_state_id",
                            "Use the actual acting creature's action.",
                        ),
                        _call(
                            "spend_srd_action",
                            {"magic_action": False, "control_action_kind": "utilize"},
                        ),
                    ],
                ),
                _set(
                    "_vessel_change",
                    {
                        "sealed": "operation == 'close'",
                        "disturbed": "operation == 'open' or _body_vessel.disturbed == true",
                    },
                ),
                _set("_body_vessel", "merge(_body_vessel, _vessel_change)"),
                *_metadata_patch({"srd_body_vessel": "_body_vessel"}),
                _call("refresh_srd_clone_bodies", {}),
            ],
        )
    )
    tools["refresh_srd_clone_bodies"] = _tool(
        "Refresh existing physical Clone bodies from actual elapsed time and disturbed vessels.",
        [_lasting()],
        [
            _clock(),
            _set("_clone_scene_records", "state.spatial_scene.entities"),
            _each(
                "_clone_scene_records",
                "_clone_scene_record",
                [
                    _when(
                        (
                            "_clone_scene_record.metadata.srd_clone_body != null and "
                            "_clone_scene_record.metadata.srd_clone_body.active == true"
                        ),
                        [
                            _call(
                                "refresh_srd_clone_body",
                                {
                                    "source_id": (
                                        "{_clone_scene_record.metadata.srd_clone_body.source_id}"
                                    )
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    tools["read_srd_clone_body"] = _public(
        _tool(
            "Read one actual retained Clone body's maturity and physical provenance.",
            [_param("source_id"), _lasting()],
            [_call("refresh_srd_clone_body", {"source_id": "{source_id}"}, ("_clone_body",))],
        ),
        [("Body", "_clone_body")],
    )
    tools["refresh_srd_clone_body"] = _tool_scene(
        "Resolve one exact Clone body; no caller supplies maturity or a transfer verdict.",
        [_param("source_id"), _lasting()],
        [
            _clock(),
            _set("_clone_body", None),
            _set("_clone_scene_rows", "state.spatial_scene.entities"),
            _each(
                "_clone_scene_rows",
                "_clone_scene_row",
                [
                    _when(
                        (
                            "_clone_scene_row.metadata.srd_clone_body != null and "
                            "_clone_scene_row.metadata.srd_clone_body.source_id == source_id"
                        ),
                        [
                            _require("_clone_body == null", "Clone body identity is ambiguous."),
                            _set("_clone_body", "_clone_scene_row.metadata.srd_clone_body"),
                            _set("_clone_vessel_scene", "_clone_scene_row"),
                        ],
                    ),
                ],
            ),
            _require("_clone_body != null", "No actual Clone body has this identity."),
            _when(
                "_clone_body.active == true",
                [
                    _set(
                        "_clone_change",
                        {"mature": "lasting_state.lasting_tick >= _clone_body.matures_tick"},
                    ),
                    _set("_clone_ended", {"active": False}),
                    _when(
                        (
                            "_clone_vessel_scene == null or _clone_vessel_scene.active != true "
                            "or _clone_vessel_scene.metadata.srd_body_vessel.disturbed == true "
                            "or _clone_vessel_scene.metadata.srd_body_vessel.sealed != true"
                        ),
                        [_set("_clone_change", "merge(_clone_change, _clone_ended)")],
                    ),
                    _when(
                        (
                            "_clone_vessel_scene.metadata.srd_object_breaks != null and "
                            "len(_clone_vessel_scene.metadata.srd_object_breaks) > 0"
                        ),
                        [_set("_clone_change", "merge(_clone_change, _clone_ended)")],
                    ),
                    _set("_clone_body", "merge(_clone_body, _clone_change)"),
                    *_persist_clone_body(),
                ],
            ),
        ],
    )
    tools["cast_srd_body_soul_spell"] = _tool_scene(
        "Create the actual duplicate after committed Clone casting and material costs.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _param("slot_level", "integer"),
            _param("object_id"),
            *CAST_CHOICES,
            _param("material_item_ids", "array", items={"type": "string"}),
            _lasting(),
        ],
        [
            _clock(),
            *_vessel_lookup(),
            _require(
                "target_state.is_alive == true",
                "Touch an actual creature; detached flesh is not admitted yet.",
            ),
            _require(
                "target_state.form_statistics_replaced != true and "
                "target_state.form_cloud != true and target_state.magic_jar_role == null",
                "Cloning a replacement form or a Magic Jar body needs its exact physical owner.",
            ),
            _require(
                (
                    "_body_vessel.owner == _caster_state_id and _body_vessel.material_id in "
                    "material_item_ids"
                ),
                "Use the same actual selected vessel material.",
            ),
            _require(
                "_body_vessel.sealed == true and _body_vessel.disturbed != true",
                "Clone needs its actual undisturbed sealed vessel.",
            ),
            _require(
                "_object_scene.metadata.srd_clone_body == null",
                "This vessel already contains its source-owned body.",
            ),
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{body_vessel_id}",
                    "range_feet": 5,
                    "requires_sight": False,
                },
                ("_world_distance",),
            ),
            _call(
                "resolve_srd_scene_geometry_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                (
                    "_scene_distance",
                    "_scene_same_plane",
                    "_scene_line_of_sight",
                    "_scene_target_entity_id",
                ),
            ),
            _require(
                (
                    "_scene_same_plane == true and _scene_distance <= 5 and "
                    "_scene_line_of_sight == true"
                ),
                "Actually touch the source creature through an unobstructed path.",
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _require(
                "_info_scene_entity.footprint.kind == 'box'",
                "Cloning currently needs the creature's admitted body bounds.",
            ),
            *[
                _require(
                    f"_body_vessel.interior_feet[{i}] >= "
                    f"_info_scene_entity.footprint.half_extents[{i}] * 2",
                    "The actual vessel must hold the creature being cloned.",
                )
                for i in range(2)
            ],
            _require(
                "_body_vessel.interior_feet[2] >= _info_scene_entity.height",
                "The actual vessel must hold the creature's full height.",
            ),
            *_capture_clone_physical_body(),
            _set("_clone_serial", 1),
            _when(
                "caster_state.clone_serial != null",
                [_set("_clone_serial", "caster_state.clone_serial + 1")],
            ),
            _set("caster_state.clone_serial", "_clone_serial"),
            _call(
                "format_srd_information_projection",
                {"message": "{_caster_state_id}:clone:{_clone_serial}"},
                ("_info_projection_message",),
            ),
            _set("_clone_id", "_info_projection_message"),
            _set(
                "_clone_body",
                {
                    "kind": "clone_body",
                    "spell": "'clone'",
                    "source_id": "_clone_id",
                    "caster": "_caster_state_id",
                    "target": "_target_state_id",
                    "owner": "_target_state_id",
                    "phase": "'never'",
                    "turn": 0,
                    "vessel": "body_vessel_id",
                    "started_tick": "lasting_state.lasting_tick",
                    "matures_tick": "lasting_state.lasting_tick + 3456000",
                    "age_choice": "clone_age",
                    "mature": False,
                    "active": True,
                    "transferred": False,
                    "dispel_ongoing": False,
                    "spell_level": "slot_level",
                    "physical_profile": "_clone_physical_body",
                },
            ),
            _set("lasting_state.has_clone_bodies", True),
            _set("target_state.has_clone_bodies", True),
            _when(
                "target_state.clone_body_vessels == null",
                [_set("target_state.clone_body_vessels", [])],
            ),
            _push("target_state.clone_body_vessels", "body_vessel_id"),
            *_lookup_object(),
            *_metadata_patch({"srd_clone_body": "_clone_body"}),
            _set("_spell_summary_rows", [{"label": "'Clone'", "value": "_clone_id"}]),
            _set("_spell_detail_rows", [{"label": "'Maturation rounds'", "value": 1728000}]),
        ],
    )
    tools.update(_transfer_tools())
    return tools


def _transfer_tools():
    select = _tool(
        "Select one actual mature body for this same individual's next eligible Clone transfer.",
        [_entity("target_state"), _param("source_id"), _lasting()],
        [
            _call("refresh_srd_clone_body", {"source_id": "{source_id}"}, ("_clone_body",)),
            _require(
                (
                    "_clone_body.target == _target_state_id and _clone_body.active == true and "
                    "_clone_body.mature == true"
                ),
                "Choose this individual's actual undisturbed mature Clone body.",
            ),
            _require(
                "_clone_body.physical_profile != null",
                "This legacy Clone has no recorded casting-body profile.",
            ),
            _require(
                "target_state.form_statistics_replaced != true and "
                "target_state.form_cloud != true and target_state.magic_jar_role == null",
                "Finish the exact replacement-form or Magic Jar body transition first.",
            ),
            _set("target_state.selected_clone_body_source_id", "source_id"),
        ],
    )
    settle = _tool_scene(
        (
            "Settle an actual post-maturation death into one exact available body, preserving "
            "its logical individual."
        ),
        [_entity("target_state"), _lasting()],
        [
            _set("_clone_transfer_done", False),
            _when(
                (
                    "target_state.has_clone_bodies == true and target_state.is_alive == false "
                    "and target_state.death_tick != null and target_state.soul_free == true and"
                    " target_state.soul_willing == true"
                ),
                [
                    _call("refresh_srd_clone_bodies", {}),
                    _set("_clone_candidates", []),
                    _set("_clone_scene_records", "state.spatial_scene.entities"),
                    _each(
                        "_clone_scene_records",
                        "_clone_scene_record",
                        [
                            _set("_clone_candidate", None),
                            _when(
                                "_clone_scene_record.metadata.srd_clone_body != null",
                                [
                                    _set(
                                        "_clone_candidate",
                                        "_clone_scene_record.metadata.srd_clone_body",
                                    )
                                ],
                            ),
                            _when(
                                (
                                    "_clone_candidate != null and _clone_candidate.target == "
                                    "_target_state_id and _clone_candidate.active == true and "
                                    "_clone_candidate.mature == true and "
                                    "target_state.death_tick >= _clone_candidate.matures_tick"
                                ),
                                [_push("_clone_candidates", "_clone_candidate")],
                            ),
                        ],
                    ),
                    _set("_clone_body", None),
                    _when(
                        "target_state.selected_clone_body_source_id != null",
                        [
                            _each(
                                "_clone_candidates",
                                "_clone_candidate",
                                [
                                    _when(
                                        (
                                            "_clone_candidate.source_id == "
                                            "target_state.selected_clone_body_source_id"
                                        ),
                                        [_set("_clone_body", "_clone_candidate")],
                                    ),
                                ],
                            ),
                        ],
                        [
                            _when(
                                "len(_clone_candidates) == 1",
                                [_set("_clone_body", "_clone_candidates[0]")],
                            )
                        ],
                    ),
                    _set(
                        "target_state.clone_transfer_pending",
                        "len(_clone_candidates) > 1 and _clone_body == null",
                    ),
                    _when(
                        "_clone_body != null and (_clone_body.physical_profile == null or "
                        "target_state.form_statistics_replaced == true or "
                        "target_state.form_cloud == true or target_state.magic_jar_role != null)",
                        [_set("target_state.clone_transfer_pending", True)],
                    ),
                    _when(
                        "_clone_body != null and _clone_body.physical_profile != null and "
                        "target_state.form_statistics_replaced != true and "
                        "target_state.form_cloud != true and target_state.magic_jar_role == null",
                        [
                            _when(
                                "target_state.concentration_active == true",
                                [
                                    _call(
                                        "end_srd_concentration_node",
                                        {
                                            "source_state": "{_target_state_id}",
                                            "target_state": "{_target_state_id}",
                                        },
                                    )
                                ],
                            ),
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_target_state_id}"},
                                POSITION_EXPORTS,
                            ),
                            _set("_clone_old_scene", "_info_scene_entity"),
                            _set("_clone_old_body_id", "_target_state_id"),
                            _when(
                                "target_state.current_body_source_id != null",
                                [_set("_clone_old_body_id", "target_state.current_body_source_id")],
                            ),
                            _set("_clone_old_animated_id", None),
                            _when(
                                "target_state.body_animated_actor_id != null",
                                [
                                    _set(
                                        "_clone_old_animated_id",
                                        "target_state.body_animated_actor_id",
                                    )
                                ],
                            ),
                            _set("_clone_old_inventory", []),
                            _when(
                                "target_state.inventory != null",
                                [_set("_clone_old_inventory", "target_state.inventory")],
                            ),
                            _set(
                                "_clone_prior_body",
                                {
                                    "kind": "'body_provenance'",
                                    "source_id": "_clone_old_body_id",
                                    "logical_actor_id": "_target_state_id",
                                    "owner": "_target_state_id",
                                    "target": "_target_state_id",
                                    "phase": "'never'",
                                    "turn": 0,
                                    "death_tick": "target_state.death_tick",
                                    "body_available": "target_state.body_available == true",
                                    "death_creature_type": "target_state.death_creature_type",
                                    "death_was_undead": "target_state.death_was_undead == true",
                                    "inventory": "_clone_old_inventory",
                                    "position": "_clone_old_scene.position",
                                    "plane": "_clone_old_scene.metadata.plane",
                                    "soul_elsewhere": True,
                                    "active": False,
                                    "body_animated_actor_id": None,
                                },
                            ),
                            _when(
                                "_clone_old_animated_id != null",
                                [
                                    _set(
                                        "_clone_animated_link",
                                        {"body_animated_actor_id": "_clone_old_animated_id"},
                                    ),
                                    _set(
                                        "_clone_prior_body",
                                        "merge(_clone_prior_body, _clone_animated_link)",
                                    ),
                                ],
                            ),
                            _when(
                                "target_state.prior_body_sources == null",
                                [_set("target_state.prior_body_sources", {})],
                            ),
                            _set(
                                "target_state.prior_body_sources.{_clone_old_body_id}",
                                "_clone_prior_body",
                            ),
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_clone_body.vessel}"},
                                POSITION_EXPORTS,
                            ),
                            _set("_clone_new_scene", "_info_scene_entity"),
                            _set("_clone_vessel_scene", "_info_scene_entity"),
                            _set(
                                "_clone_position_patch",
                                {
                                    "plane": "_clone_new_scene.metadata.plane",
                                    "current_body_source_id": "_clone_body.source_id",
                                    "previous_position": "_clone_old_scene.position",
                                    "body_physical_profile": "_clone_body.physical_profile",
                                },
                            ),
                            _set(
                                "_clone_actor_metadata",
                                "merge(_clone_old_scene.metadata, _clone_position_patch)",
                            ),
                            *_snapshot(),
                            {
                                "spatial_mutation": {
                                    "scene": "state.spatial_scene",
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "expected_revision": "{_info_snapshot.geometry_revision}",
                                    "target": {"kind": "entity", "id": "{_clone_old_scene.id}"},
                                    "patch": {
                                        "position": "{_clone_new_scene.position}",
                                        "metadata": "{_clone_actor_metadata}",
                                        "footprint": "{_clone_body.physical_profile.footprint}",
                                        "height": "{_clone_body.physical_profile.height}",
                                    },
                                },
                                "store_as": "_clone_relocation",
                            },
                            _set("target_state.current_body_source_id", "_clone_body.source_id"),
                            *_restore_clone_physical_body(),
                            _set("target_state.body_animated_actor_id", None),
                            _set("target_state.inventory", []),
                            _set("target_state.held_items", []),
                            _set("target_state.held_attack_names", []),
                            _set("target_state.worn_armor", None),
                            _set("target_state.worn_shield", None),
                            _set("target_state.attuned_items", []),
                            _set("target_state.body_available", True),
                            _set("target_state.vital_parts_present", True),
                            _set("target_state.is_alive", True),
                            _set("target_state.hp", 0),
                            {"remove_condition": {"target": "target_state", "condition": "dead"}},
                            _call(
                                "restore_srd_returned_hit_points",
                                {
                                    "target_state": "{_target_state_id}",
                                    "amount": "{target_state.max_hp}",
                                },
                            ),
                            _set("target_state.death_tick", None),
                            _set("target_state.death_old_age", False),
                            _set("target_state.clone_transfer_pending", False),
                            _set("target_state.body_age_choice", "_clone_body.age_choice"),
                            _set(
                                "_clone_update",
                                {
                                    "active": False,
                                    "transferred": True,
                                    "transfer_tick": "lasting_state.lasting_tick",
                                },
                            ),
                            _set("_clone_body", "merge(_clone_body, _clone_update)"),
                            *_persist_clone_body(),
                            _set("_clone_transfer_done", True),
                        ],
                    ),
                ],
            ),
        ],
    )
    lookup = _tool(
        (
            "Resolve prior physical body provenance by exact source, never by the individual's "
            "current replacement body."
        ),
        [_entity("target_state"), _param("body_source_id"), _lasting()],
        [
            _call(
                "format_srd_information_projection",
                {"message": "{body_source_id}"},
                ("_info_projection_message",),
            ),
            _set("_prior_source_id", "_info_projection_message"),
            _set("_prior_body", None),
            _when(
                (
                    "target_state.prior_body_sources != null and "
                    "target_state.prior_body_sources[_prior_source_id] != null"
                ),
                [_set("_prior_body", "target_state.prior_body_sources[_prior_source_id]")],
            ),
        ],
    )
    return {
        "select_srd_clone_body": _public(select),
        "settle_srd_clone_transfer": _public(
            deepcopy(settle), [("Transferred", "_clone_transfer_done")]
        ),
        "settle_srd_clone_death": settle,
        "read_srd_prior_body_source": lookup,
    }


def _strip(value, owned):
    if isinstance(value, list):
        value[:] = [
            n
            for n in value
            if not isinstance(n, dict)
            or not (
                n.get("call") in owned
                or n.get("condition")
                == "_spell_cast_failed != true and _spell_definition.family == 'body_soul_spell'"
                or (
                    n.get("condition", "").endswith(".has_clone_bodies == true")
                    and any(e.get("call") in owned for e in _walk(n))
                )
            )
        ]
        for n in value:
            _strip(n, owned)
    elif isinstance(value, dict):
        for n in value.values():
            _strip(n, owned)


def extend_runtime(text):
    """Compose physical Clone sources after actual extended casting and scene owners."""
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = runtime["tools"]
    changed = _tools()
    cast = deepcopy(tools["cast_spell"])
    _strip(cast["effects"], {"cast_srd_body_soul_spell"})
    cast["parameters"] = [
        p for p in cast["parameters"] if p["name"] not in {p["name"] for p in CAST_CHOICES}
    ] + deepcopy(CAST_CHOICES)
    resolution = cast["effects"]
    resolution.append(
        _when(
            "_spell_cast_failed != true and _spell_definition.family == 'body_soul_spell'",
            [
                _call(
                    "cast_srd_body_soul_spell",
                    {
                        "caster_state": "{_caster_state_id}",
                        "target_state": "{_target_id}",
                        "slot_level": "{_slot_level}",
                        "object_id": "{body_vessel_id}",
                        "material_item_ids": "{material_item_ids}",
                        **{p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES},
                    },
                    ("_spell_summary_rows", "_spell_detail_rows"),
                ),
            ],
        )
    )
    changed["cast_spell"] = cast
    advance = deepcopy(tools["advance_srd_lasting_time"])
    _strip(advance["effects"], {"refresh_srd_clone_bodies"})
    advance["effects"].append(
        _when("state.has_clone_bodies == true", [_call("refresh_srd_clone_bodies", {})])
    )
    changed["advance_srd_lasting_time"] = advance
    deceased = deepcopy(tools["record_srd_deceased_body"])
    body_guard = "body_available != true or target_state.body_animated_actor_id == null"
    if not any(p["condition"] == body_guard for p in deceased["pre_conditions"]):
        deceased["pre_conditions"].append(
            {
                "condition": body_guard,
                "error": (
                    "An actual animated corpse remains consumed; its physical body cannot be "
                    "restored by a fact declaration."
                ),
            }
        )
    changed["record_srd_deceased_body"] = deceased
    death_targets = {
        "apply_srd_damage": "target",
        "apply_srd_composite_damage": "target",
        "death_saving_throw": "_target_id",
        "gain_srd_exhaustion": "_target_id",
        "cast_spell_instantaneous_threshold_death_damage": "_target_id",
        "project_srd_timed_hit_point_bonus": "_target_state_id",
    }
    for name, subject in death_targets.items():
        if name not in tools:
            continue
        death = deepcopy(tools[name])
        _strip(death["effects"], {"settle_srd_clone_death"})
        if not any(p["name"] == "lasting_state" for p in death["parameters"]):
            death["parameters"].append(_lasting())

        def hook_actual_death(value, subject=subject):
            if isinstance(value, list):
                for item in list(value):
                    hook_actual_death(item)
                if any(
                    isinstance(n, dict) and n.get("apply_condition", {}).get("condition") == "dead"
                    for n in value
                ):
                    value.append(
                        _when(
                            "lasting_state.has_clone_bodies == true",
                            [
                                _call(
                                    "settle_srd_clone_death", {"target_state": "{" + subject + "}"}
                                )
                            ],
                        )
                    )
            elif isinstance(value, dict):
                for item in value.values():
                    hook_actual_death(item)

        hook_actual_death(death["effects"])
        changed[name] = death
    text = _replace_tools(text, changed)
    if "      body_soul_spell:" not in text:
        schema = yaml.safe_dump({"body_soul_spell": _schema()}, sort_keys=False)
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      timed_senses:",
            1,
        )
    return text
