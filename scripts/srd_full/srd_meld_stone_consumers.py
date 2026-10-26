"""Final movement, sight, hearing and physical-stone changes consume actual Meld sources."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _param
    from .srd_magic_jar_consumers import _walk
    from .srd_timed_actor_defenses import _call, _load_runtime, _require, _set, _when
    from .srd_timed_senses import _loop, _replace_tools
else:
    from srd_control_behaviors import _param
    from srd_magic_jar_consumers import _walk
    from srd_timed_actor_defenses import _call, _load_runtime, _require, _set, _when
    from srd_timed_senses import _loop, _replace_tools


HEARING_GUARD = (
    "kind == 'ability_check' and ability == 'wisdom' and skill == 'perception' and "
    "requires_hearing == true and actor_state != null and "
    "actor_state.meld_stone_source_id != null"
)
SIGHT_GUARD = "observer_state.meld_stone_source_id != null"
SELF_FAMILIES = (
    "spell_healing",
    "self_temporary_hit_points",
    "timed_actor_defense",
    "timed_actor_modifier",
    "timed_senses",
    "instantaneous_restoration",
    "single_target_concentration_damage_resistance",
    "single_target_timed_armor_class_floor",
    "single_target_timed_death_prevention",
    "single_target_concentration_armor_class_bonus",
)
REFRESH_GUARD = "lasting_state.has_meld_stone_sources == true"


def _lasting(tool):
    if not any(p["name"] == "lasting_state" for p in tool["parameters"]):
        tool["parameters"].append(
            _param(
                "lasting_state",
                required=False,
                default="state",
                resolve="entity",
                model_hidden=True,
            )
        )


def _travel_guards():
    """Keep both actual body and occupied-stone restrictions before position changes."""
    return [
        _require(
            "_travel_mover.metadata.meld_stone_source_id == null",
            "This actual body must first leave its merged stone.",
        ),
        _when(
            REFRESH_GUARD,
            [
                _set("_meld_movement_ids", "lasting_state.meld_stone_source_ids"),
                _loop(
                    "_meld_movement_ids",
                    "_meld_movement_id",
                    [
                        _set(
                            "_meld_movement_record",
                            "lasting_state.meld_stone_sources[_meld_movement_id]",
                        ),
                        _require(
                            "_meld_movement_record.installed != true or "
                            "_meld_movement_record.stone != _travel_mover.id",
                            "Moving occupied melded stone needs its physical containment consumer.",
                        ),
                    ],
                ),
            ],
        ),
    ]


def _travel_owner(tools):
    """Recognize only the original Travel body or its exact named relocation split."""
    wrapper_name, node_name = "move_srd_travel_subject", "move_srd_travel_subject_node"
    wrapper = tools[wrapper_name]
    calls = []
    _walk(
        wrapper["effects"],
        lambda effect: calls.append(effect) if effect.get("call") == node_name else None,
    )
    if not calls:
        # Regeneration can retain the prior node while emitting a fresh ordinary owner.
        return wrapper_name
    if len(calls) != 1 or wrapper["effects"][0] is not calls[0] or node_name not in tools:
        raise ValueError("Meld needs the exact first owning relocation-node Call")
    call, node = calls[0], tools[node_name]
    expected = {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
        + "}"
        for p in node["parameters"]
    }
    exports = call.get("export_context")
    if (
        set(call) != {"call", "params", "export_context"}
        or call["params"] != expected
        or not isinstance(exports, dict)
        or exports.get("_travel_mover") != "_travel_mover"
        or exports.get("_travel_scene_move") != "_travel_scene_move"
        or any(key != value for key, value in exports.items())
        or any("spatial_mutation" in e for e in wrapper["effects"])
    ):
        raise ValueError("Meld must preserve the actual relocation forwarding ABI")
    return node_name


def _bind_travel(tools):
    """Bind one real position owner, retaining every unowned effect and wrapper callback."""
    name = _travel_owner(tools)
    mover = deepcopy(tools[name])
    guards = _travel_guards()
    mover["effects"] = [e for e in mover["effects"] if e not in guards]
    found = [
        i
        for i, e in enumerate(mover["effects"])
        if e.get("set", {}).get("field") == "_travel_mover"
    ]
    mutations = [i for i, e in enumerate(mover["effects"]) if "spatial_mutation" in e]
    if (
        len(found) != 1
        or len(mutations) != 1
        or found[0] >= mutations[0]
        or mover["effects"][found[0]]["set"]["value"] != "_info_scene_entity"
        or mover["effects"][mutations[0]]["spatial_mutation"].get("target")
        != {"kind": "entity", "id": "{_travel_mover.id}"}
    ):
        raise ValueError("Meld needs the actual admitted mover before its sole position mutation")
    _lasting(mover)
    mover["effects"][found[0] + 1 : found[0] + 1] = guards
    return {name: mover}


def bind_travel_consumers(text):
    """Capture Meld restrictions before the Travel split and source-specific movement clones."""
    tools = _load_runtime(text)["tools"]
    if "refresh_srd_meld_stone_sources" not in tools:
        raise ValueError("Meld's exact source family must precede its travel consumers")
    return _replace_tools(text, _bind_travel(tools))


def bind_consumers(text):
    """Compose final real owners before reaction and source-specific attack clones are taken."""
    tools = _load_runtime(text)["tools"]
    if "refresh_srd_meld_stone_sources" not in tools:
        raise ValueError("Meld's exact source family must precede its consumers")
    changed = {}
    d20 = deepcopy(tools["d20_test"])
    seeing = (
        "actor_state != null and actor_state.meld_stone_source_id != null and "
        "kind == 'ability_check' and requires_sight == true"
    )

    def strip_old_meld_checks(nodes):
        nodes[:] = [e for e in nodes if e.get("condition") not in (HEARING_GUARD, seeing)]
        for node in nodes:
            for branch in ("on_true", "on_false"):
                if branch in node:
                    strip_old_meld_checks(node[branch])

    strip_old_meld_checks(d20["effects"])
    sight_check_found = []

    def bind_meld_checks(node):
        if node.get("condition") == "kind == 'ability_check' or kind == 'attack_roll'":
            sight_check_found.append(True)
            node["on_true"].extend(
                [
                    _when(HEARING_GUARD, [_set("_condition_disadvantage", True)]),
                    _when(seeing, [_set("_automatic_failure_reason", "'melded_outside_sight'")]),
                ]
            )

    _walk(d20["effects"], bind_meld_checks)
    if len(sight_check_found) != 1:
        raise ValueError("Meld needs the actual D20 ability-check condition owner")
    changed["d20_test"] = d20
    sight_owner = (
        "resolve_srd_sight_node" if "resolve_srd_sight_node" in tools else "resolve_srd_sight"
    )
    sight = deepcopy(tools[sight_owner])
    sight_found = []

    def no_outside_sight(node):
        if node.get("set", {}).get("field") == "_sight_can_see":
            sight_found.append(True)
            value = node["set"]["value"]
            clause = "observer_state.meld_stone_source_id == null"
            if clause not in value:
                node["set"]["value"] = f"({value}) and {clause}"
            subject_clause = "subject_state == null or subject_state.meld_stone_source_id == null"
            if subject_clause not in node["set"]["value"]:
                node["set"]["value"] += f" and ({subject_clause})"

    _walk(sight["effects"], no_outside_sight)
    if not sight_found:
        raise ValueError("Meld needs the actual sight-result owner before final wrappers")
    changed[sight_owner] = sight

    cast = deepcopy(tools["cast_spell"])
    guard = {
        "condition": "caster_state.meld_stone_source_id == null or "
        "(_target_id == _caster_state_id and (targets == null or len(targets) == 0))",
        "error": "A merged caster can cast spells only on the actual caster.",
    }
    if guard not in cast["pre_conditions"]:
        cast["pre_conditions"].append(guard)
    marker = "caster_state.meld_stone_source_id != null"
    cast["effects"] = [e for e in cast["effects"] if e.get("condition") != marker]
    at = (
        next(
            i
            for i, e in enumerate(cast["effects"])
            if e.get("table_lookup", {}).get("table") == "definitions.spells"
        )
        + 1
    )
    cast["effects"].insert(
        at,
        _when(
            marker,
            [
                _set("_meld_self_cast_families", [repr(family) for family in SELF_FAMILIES]),
                _require(
                    "_spell_definition.family in _meld_self_cast_families",
                    "This merged self-cast family lacks its exact body/target consumer.",
                ),
            ],
        ),
    )
    changed["cast_spell"] = cast

    for name in (
        "spend_combat_movement",
        "move_srd_scene_creature_node",
        "move_srd_scene_creature_environment_segment",
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        names = {p["name"] for p in tool["parameters"]}
        actor = "actor_state" if "actor_state" in names else "actor"
        guard = {
            "condition": f"{actor}.meld_stone_source_id == null",
            "error": "Leave the actual merged stone before ordinary movement.",
        }
        if guard not in tool.setdefault("pre_conditions", []):
            tool["pre_conditions"].append(guard)
        changed[name] = tool
    if "derive_srd_scene_movement_budget" in tools:
        tool = deepcopy(tools["derive_srd_scene_movement_budget"])
        guard = _require(
            "actor_state.meld_stone_source_id == null",
            "Leave the actual merged stone before ordinary movement.",
        )
        if guard not in tool["effects"]:
            tool["effects"].insert(0, guard)
        changed["derive_srd_scene_movement_budget"] = tool
    changed.update(_bind_travel(tools))

    for name in (
        "resolve_srd_spell_object_attack",
        "resolve_srd_sorcerous_burst_object_attack",
        "cast_spell_object_content_spell",
        "advance_srd_lasting_time",
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        _lasting(tool)
        hook = _when(REFRESH_GUARD, [_call("refresh_srd_meld_stone_sources", {})])
        tool["effects"] = [e for e in tool["effects"] if e != hook]
        tool["effects"].append(hook)
        changed[name] = tool
    return _replace_tools(text, changed)
