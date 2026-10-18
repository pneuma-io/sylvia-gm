"""Disintegrate destroys its actual still-zero body before soul or vessel remapping."""

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
        _when,
    )
    from .srd_timed_actor_defenses import _load_runtime
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
        _when,
    )
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


TAG = "disintegrate_spell_packet"
BODY_CALL = "disintegrate_srd_physical_body"
PC_DEATH = (
    "target.hp == 0 and target.creature_kind == 'character' and _remaining_damage >= target.max_hp"
)
PC_DISINTEGRATE = (
    "target.hp == 0 and target.creature_kind == 'character' and "
    "(_remaining_damage >= target.max_hp or "
    "(disintegrate_spell_packet == true and _hp_before > 0 and _hp_damage > 0))"
)
ZERO_GUARD = "disintegrate_spell_packet == true and _hp_before > 0 and _hp_damage > 0"
SCALAR_FACADES = {
    "apply_srd_qualifying_damage": "execute_srd_qualified_scalar_packet",
    "apply_srd_prismatic_damage": "execute_srd_qualified_scalar_packet",
    "apply_srd_dream_wake_damage": "execute_srd_qualified_scalar_packet",
}


def _lists(effects):
    yield effects
    for effect in effects:
        for branch in ("on_true", "on_false"):
            if branch in effect:
                yield from _lists(effect[branch])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _parameter(tool):
    if not any(p["name"] == TAG for p in tool["parameters"]):
        tool["parameters"].append(
            _param(TAG, "boolean", required=False, default=False, model_hidden=True)
        )


def _forward(parameters):
    return {
        p["name"]: "{_" + p["name"] + "_id}"
        if p.get("resolve") in {"entity", "participant"}
        else "{" + p["name"] + "}"
        for p in parameters
    }


def _facade(tool, core, name):
    """Recognize the existing exact delegate without expanding its physical packet."""
    if not any(e.get("call") == core for e in tool["effects"]):
        return False
    expected = {
        "call": core,
        "params": _forward(tool["parameters"])
        | {"render_damage_snapshot": name != "apply_srd_prismatic_damage"},
        "export_context": "*",
    }
    drain = {
        "condition": "lasting_state.has_dream_spells == true",
        "on_true": [{"call": "drain_srd_dream_wakes", "params": {}}],
    }
    if tool["effects"] not in ([expected], [expected, drain]):
        raise ValueError("Disintegrate requires the unchanged qualified scalar facade.")
    return True


def _body_record_tool():
    # Only actual material-classified carried scene objects have a deterministic item ending.
    gear = [
        _set("_dust_scene_rows", "lasting_state.spatial_scene.entities"),
        _each(
            "_dust_scene_rows",
            "_dust_item",
            [
                _when(
                    "_dust_item.active == true and _dust_item.metadata.is_object == true and "
                    "(_dust_item.metadata.carried_by == _target_state_id or "
                    "_dust_item.metadata.worn_by == _target_state_id)",
                    [
                        _when(
                            "_dust_item.metadata.srd_object_material.magical == false",
                            [
                                _set("_dust_item_name", "_dust_item.id"),
                                _when(
                                    "_dust_item.metadata.inventory_item != null",
                                    [
                                        _set(
                                            "_dust_item_name", "_dust_item.metadata.inventory_item"
                                        ),
                                    ],
                                ),
                                _push("_dust_removed_items", "_dust_item_name"),
                                {
                                    "spatial_query": {
                                        "scene": "state.spatial_scene",
                                        "operation": "snapshot",
                                    },
                                    "store_as": "_dust_snapshot",
                                },
                                {
                                    "spatial_mutation": {
                                        "scene": "state.spatial_scene",
                                        "map_id": "{lasting_state.spatial_scene.map.id}",
                                        "expected_revision": "{_dust_snapshot.geometry_revision}",
                                        "target": {"kind": "entity", "id": "{_dust_item.id}"},
                                        "patch": {"active": False},
                                    },
                                    "store_as": "_dust_item_write",
                                },
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]
    effects = [
        _set("target_state.body_disintegrated", True),
        _set("target_state.body_available", False),
        _set("target_state.vital_parts_present", False),
        _set("target_state.disintegrated_by_caster", "source_state_id"),
        _set("target_state.disintegrated_body_source_id", "_target_state_id"),
        _when(
            "target_state.current_body_source_id != null",
            [
                _set(
                    "target_state.disintegrated_body_source_id",
                    "target_state.current_body_source_id",
                ),
            ],
        ),
        _set("_dust_removed_items", []),
        _when("lasting_state.spatial_scene != null", gear),
    ]
    for field in ("inventory", "held_items", "held_attack_names"):
        effects.append(
            _when(
                "target_state." + field + " != null",
                [
                    _set("_dust_kept_items", []),
                    _set("_dust_inventory_source", "target_state." + field),
                    _each(
                        "_dust_inventory_source",
                        "_dust_inventory_item",
                        [
                            _when(
                                "_dust_inventory_item not in _dust_removed_items",
                                [
                                    _push("_dust_kept_items", "_dust_inventory_item"),
                                ],
                            ),
                        ],
                    ),
                    _set("target_state." + field, "_dust_kept_items"),
                ],
            )
        )
    for field in ("worn_armor", "worn_shield"):
        effects.append(
            _when(
                "target_state."
                + field
                + " != null and target_state."
                + field
                + ".inventory_item in _dust_removed_items",
                [_set("target_state." + field, None)],
            )
        )
    effects.extend(
        [
            _when(
                "target_state.spell_materials != null",
                [
                    _set("_dust_kept_materials", []),
                    _set("_dust_material_source", "target_state.spell_materials"),
                    _each(
                        "_dust_material_source",
                        "_dust_material",
                        [
                            _when(
                                "_dust_material.inventory_item not in _dust_removed_items",
                                [
                                    _push("_dust_kept_materials", "_dust_material"),
                                ],
                            ),
                        ],
                    ),
                    _set("target_state.spell_materials", "_dust_kept_materials"),
                ],
            ),
            _set("target_state.disintegrated_items", "_dust_removed_items"),
        ]
    )
    return _tool(
        "Save the exact dusted body cause and destroy only its admitted nonmagical carried gear.",
        [_entity("target_state"), _param("source_state_id"), _lasting()],
        effects,
    )


def _body_tool():
    return _tool(
        "Resolve form endings, then mark the actual still-zero body before soul remapping.",
        [_entity("target_state"), _param("source_state_id"), _lasting()],
        [
            _when(
                "target_state.has_transformations == true",
                [
                    _call(
                        "project_srd_transformation",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                ],
            ),
            _when(
                "target_state.hp == 0",
                [
                    _set("_dust_body_id", "_target_state_id"),
                    _when(
                        "target_state.magic_jar_role == 'possessed'",
                        [
                            _call(
                                "read_srd_magic_jar_source",
                                {"caster_state": "{_target_state_id}"},
                                ("_jar_source",),
                            ),
                            _set("_dust_body_id", "_jar_source.host"),
                        ],
                    ),
                    _call(
                        "record_srd_disintegrated_body",
                        {
                            "target_state": "{_dust_body_id}",
                            "source_state_id": "{source_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                ],
            ),
        ],
    )


def _producer_tool(qualified):
    """Use Jar's real alias/settlement only for this spell's authenticated creature packet."""
    value = deepcopy(qualified)
    _parameter(value)
    for parameter in value["parameters"]:
        if parameter["name"] == "target":
            parameter["resolve"] = "entity"
    params = _forward(value["parameters"])
    params["target"] = "{_disintegrate_actual_target_id}"
    value["description"] = (
        "Apply Disintegrate's actual failed-save packet to the physical occupied body."
    )
    value["pre_conditions"] = []
    value.pop("narrative", None)
    value.get("metadata", {}).pop("result_card", None)
    value["effects"] = [
        _require(
            "disintegrate_spell_packet == true",
            "Only the failed Disintegrate save issues this packet.",
        ),
        _set("_disintegrate_actual_target_id", "_target_id"),
        _set("_disintegrate_occupied_body", False),
        _when(
            "target.magic_jar_role == 'host_soul' or target.magic_jar_role == 'possessed'",
            [
                _call(
                    "resolve_srd_magic_jar_target",
                    {"target_state": "{_target_id}"},
                    ("_magic_jar_target_id",),
                ),
                _set("_disintegrate_actual_target_id", "_magic_jar_target_id"),
                _call(
                    "read_srd_magic_jar_source",
                    {"caster_state": "{_disintegrate_actual_target_id}"},
                    ("_jar_source",),
                ),
                _set("_disintegrate_host_id", "_jar_source.host"),
                _set("_disintegrate_occupied_body", True),
            ],
        ),
        {"call": "apply_srd_qualifying_damage", "params": params, "export_context": "*"},
        _when(
            "_disintegrate_occupied_body == true",
            [
                _call(
                    "sync_srd_magic_jar_host_body",
                    {
                        "caster_state": "{_disintegrate_actual_target_id}",
                        "host_state": "{_disintegrate_host_id}",
                    },
                ),
                _call(
                    "settle_srd_disintegrate_jar_death",
                    {"caster_state": "{_disintegrate_actual_target_id}"},
                ),
            ],
        ),
    ]
    return value


def _jar_settlement_tool():
    return _tool(
        "Settle only the existing actual occupied-host death receipt after Disintegrate reception.",
        [_entity("caster_state")],
        [
            _when(
                "caster_state.magic_jar_host_death_pending == true",
                [
                    _set("caster_state.magic_jar_host_death_pending", False),
                    _call(
                        "settle_srd_magic_jar_host_death", {"caster_state": "{_caster_state_id}"}
                    ),
                ],
            )
        ],
    )


def _bind_packet(tool):
    value = deepcopy(tool)
    _parameter(value)
    for effects in _lists(value["effects"]):
        effects[:] = [e for e in effects if e.get("condition") != ZERO_GUARD]
    admitted = {PC_DEATH: PC_DISINTEGRATE, PC_DISINTEGRATE: PC_DISINTEGRATE}
    for before, after in list(admitted.items()):
        admitted[f"({before}) and target.astral_projection_role != 'form'"] = (
            f"({after}) and target.astral_projection_role != 'form'"
        )
    pc = [
        e for effects in _lists(value["effects"]) for e in effects if e.get("condition") in admitted
    ]
    if len(pc) != 1:
        raise ValueError("Disintegrate requires the native single PC massive-damage death branch.")
    pc[0]["condition"] = admitted[pc[0]["condition"]]
    deaths = 0
    for effects in _lists(value["effects"]):
        for index, effect in enumerate(list(effects)):
            if effect.get("apply_condition") != {"target": "target", "condition": "dead"}:
                continue
            if not any(e.get("call") == "record_srd_actual_death" for e in effects):
                raise ValueError(
                    "Disintegrate requires actual death recording before any body remap."
                )
            effects.insert(
                index + 1,
                _when(
                    ZERO_GUARD,
                    [
                        _call(
                            BODY_CALL,
                            {"target_state": "{target}", "source_state_id": "{source_state_id}"},
                        ),
                    ],
                ),
            )
            deaths += 1
    if deaths < 2:
        raise ValueError("Disintegrate cannot locate native post-prevention zero/death outcomes.")
    return value


def bind_runtime_consumers(text):
    """Bind final scalar bodies after prevention/forms/Jar/typed receivers and before Rx clones."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "record_srd_disintegrated_body": _body_record_tool(),
        BODY_CALL: _body_tool(),
        "settle_srd_disintegrate_jar_death": _jar_settlement_tool(),
    }
    packet_names = {
        "apply_srd_damage",
        "apply_srd_magic_jar_body_damage",
        *SCALAR_FACADES,
        "execute_srd_qualified_scalar_packet",
    }
    for name in sorted(packet_names & tools.keys()):
        original = tools[name]
        core = SCALAR_FACADES.get(name)
        if core and _facade(original, core, name):
            if core not in tools:
                raise ValueError("Disintegrate requires the actual delegated scalar receiver.")
            value = deepcopy(original)
            _parameter(value)
            value["effects"][0]["params"][TAG] = "{" + TAG + "}"
        else:
            value = _bind_packet(original)
        for effects in _lists(value["effects"]):
            for effect in effects:
                if effect.get("call") == "apply_srd_magic_jar_occupied_damage":
                    effect["params"][TAG] = "{" + TAG + "}"
        changed[name] = value
    if "apply_srd_magic_jar_occupied_damage" in tools:
        occupied = deepcopy(tools["apply_srd_magic_jar_occupied_damage"])
        _parameter(occupied)
        calls = [
            e
            for effects in _lists(occupied["effects"])
            for e in effects
            if e.get("call") == "apply_srd_magic_jar_body_damage"
        ]
        if len(calls) != 1:
            raise ValueError("Disintegrate requires the one actual occupied-body packet.")
        calls[0]["params"][TAG] = "{" + TAG + "}"
        changed["apply_srd_magic_jar_occupied_damage"] = occupied
    family = deepcopy(tools["cast_spell_single_target_save_damage"])
    creature_guard = {
        "condition": "spell != 'disintegrate' or target.is_object != true",
        "error": "Disintegrate's creature route requires a creature; ordinary object targets "
        "remain unsupported.",
    }
    if creature_guard not in family.setdefault("pre_conditions", []):
        family["pre_conditions"].append(creature_guard)
    for effects in _lists(family["effects"]):
        for index, effect in enumerate(list(effects)):
            if effect.get("condition") == "_disintegrate_spell_packet == true" and effect.get(
                "on_false"
            ):
                effects[index : index + 1] = effect["on_false"]
    calls = [
        e
        for effects in _lists(family["effects"])
        for e in effects
        if e.get("call") in {"apply_srd_damage", "apply_srd_qualifying_damage"}
    ]
    if len(calls) != 1:
        raise ValueError("Disintegrate must retain its one actual failed-save scalar packet.")
    for effects in list(_lists(family["effects"])):
        effects[:] = [
            e for e in effects if e.get("set", {}).get("field") != "_disintegrate_spell_packet"
        ]
        if calls[0] in effects:
            index = effects.index(calls[0])
            issued = deepcopy(calls[0])
            issued["call"] = "apply_srd_disintegrate_creature_damage"
            issued["params"][TAG] = True
            effects[index] = _when("_disintegrate_spell_packet == true", [issued], [calls[0]])
            effects.insert(
                index,
                _set(
                    "_disintegrate_spell_packet",
                    "spell == 'disintegrate' and _spell_save_outcome == 'failure'",
                ),
            )
    changed["cast_spell_single_target_save_damage"] = family
    changed["apply_srd_disintegrate_creature_damage"] = _producer_tool(
        changed["apply_srd_qualifying_damage"]
    )
    for name in ("resolve_srd_revival", "begin_srd_reincarnation"):
        if name not in tools:
            continue
        recovery = deepcopy(tools[name])
        condition = "target_state.body_disintegrated != true"
        if name == "resolve_srd_revival":
            condition += " or spell == 'true_resurrection'"
        guard = {
            "condition": condition,
            "error": "A disintegrated body can be revived only by True Resurrection or Wish.",
        }
        if guard not in recovery.setdefault("pre_conditions", []):
            recovery["pre_conditions"].append(guard)
        if name == "resolve_srd_revival":
            for effects in _lists(recovery["effects"]):
                for index, effect in enumerate(list(effects)):
                    if effect.get("set") == {
                        "field": "target_state.is_alive",
                        "value": True,
                    } and not any(
                        e.get("set", {}).get("field") == "target_state.body_disintegrated"
                        for e in effects
                    ):
                        effects.insert(index + 1, _set("target_state.body_disintegrated", False))
        changed[name] = recovery
    if "settle_srd_clone_death" in tools:
        clone = deepcopy(tools["settle_srd_clone_death"])
        for effects in _lists(clone["effects"]):
            for index, effect in enumerate(list(effects)):
                if effect.get("set", {}).get("field") == "_clone_prior_body":
                    body = effect["set"]["value"]
                    if isinstance(body, dict):
                        body["body_disintegrated"] = "target_state.body_disintegrated == true"
                if effect.get("set") == {
                    "field": "target_state.body_available",
                    "value": True,
                } and not any(
                    e.get("set", {}).get("field") == "target_state.body_disintegrated"
                    for e in effects
                ):
                    effects.insert(index + 1, _set("target_state.body_disintegrated", False))
        changed["settle_srd_clone_death"] = clone
    owned = {
        "record_srd_disintegrated_body",
        BODY_CALL,
        "settle_srd_disintegrate_jar_death",
        "apply_srd_disintegrate_creature_damage",
    }
    for name in owned:
        for parameter in changed[name]["parameters"]:
            if parameter["name"] == "lasting_state":
                parameter["model_hidden"] = True
    return _replace_tools(text, changed)
