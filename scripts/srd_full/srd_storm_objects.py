"""Storm recipients reuse the final prepared-object durability owner without an attack."""

from copy import deepcopy

if __package__:
    from . import srd_storm_later_turns as later
    from . import srd_storm_of_vengeance as storm
    from .srd_retained_spell_attacks import _lists
    from .srd_scene_relations import _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    import srd_storm_later_turns as later
    import srd_storm_of_vengeance as storm
    from srd_retained_spell_attacks import _lists
    from srd_scene_relations import _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _push, _replace_tools


QUALIFY = "qualify_srd_storm_prepared_object"
PACKET = "apply_srd_storm_object_damage"
COLLECT = "collect_srd_storm_object_targets"
COVER = "collect_srd_storm_object_cover_targets"
TARGET_COVER = "qualify_srd_storm_object_target_cover"
PUBLIC_TOOLS = set()


def _qualification(tools):
    """Capture the actual ordinary/Ice validation, before attack sight or range work."""
    owner = tools["resolve_srd_spell_object_relation"]
    end = next(
        i
        for i, effect in enumerate(owner["effects"])
        if effect.get("set", {}).get("field") == "_object_invisible"
    )
    effects = deepcopy(owner["effects"][:end])
    if "_object_profile.hp > 0" not in str(effects):
        raise ValueError("Storm needs the settled positive-HP prepared-object validator.")
    effects.append(
        _require(
            "_object_scene.metadata.is_object == true",
            "Storm object damage requires an actual admitted object.",
        )
    )
    return _tool(
        "Validate this actual prepared object using its final source-owned rule profile.",
        [_parameter("object_id")],
        effects,
    )


def _packet(tools):
    """Keep every actual typed mitigation, persistence and destruction callback after one roll."""
    owner = tools["resolve_srd_spell_object_attack"]
    begin = next(
        i
        for i, effect in enumerate(owner["effects"])
        if effect.get("set", {}).get("field") == "_object_hp_before"
    )
    suffix = deepcopy(owner["effects"][begin:])
    branches = [
        effect
        for effect in suffix
        if effect.get("condition")
        == "_object_attack_kind == 'hit' or _object_attack_kind == 'critical_hit'"
    ]
    if len(branches) != 1:
        raise ValueError("Storm needs the one settled object hit/durability suffix.")
    hit = branches[0]
    produced = [
        i
        for i, effect in enumerate(hit["on_true"])
        if effect.get("set", {}).get("field") == "_object_damage_rolled"
    ]
    if len(produced) != 1:
        raise ValueError("Storm needs the object's one original raw damage producer.")
    earned = deepcopy(hit["on_true"][produced[0] + 1 :])
    if "modify_srd_actor_damage_roll" in str(earned):
        raise ValueError("Storm's shared roll must not pay a second object damage penalty.")
    where = suffix.index(hit)
    suffix[where : where + 1] = [_set("_object_damage_rolled", "amount"), *earned]
    if "_object_attack_kind" in str(suffix) or "_object_damage_roll.total" in str(suffix):
        raise ValueError("Storm's earned object suffix must not depend on a fictional attack.")
    return _tool_scene(
        "Apply this already-earned Storm amount through one authentic object HP transition.",
        [
            _parameter("object_id"),
            _parameter("amount", "integer", minimum=0),
            _parameter("damage_type", "enum", enum_values=["acid", "lightning"]),
        ],
        [
            _call(QUALIFY, {"object_id": "{object_id}"}, ("_object_scene", "_object_profile")),
            *suffix,
            _call(
                "record_srd_result_text",
                {
                    "text": "{_object_damage_applied} {damage_type} object damage. "
                    "Object HP {_object_hp_before} -> {_object_hp_after}; "
                    "destroyed {_object_destroyed}.",
                },
                ("_recorded_result_text",),
            ),
            _set(
                "_storm_object_summary",
                {"label": "object_id", "value": "_recorded_result_text"},
            ),
        ],
    )


def _collector():
    """Select actual unheld object Boxes under the original source's cloud disk."""
    return _tool_scene(
        "Collect supported prepared object sections actually under this saved Storm cloud.",
        [
            _parameter("record", "object", from_state="state.turn_effects"),
            _parameter(
                "selected_targets",
                "array",
                required=False,
                default=[],
                items={"type": "string"},
                max_items=6,
                unique_items=True,
            ),
        ],
        [
            _require(
                "record.kind == 'storm_of_vengeance' and record.radius == 300",
                "The actual Storm source owns the printed cloud radius.",
            ),
            _set("_storm_object_candidates", []),
            _set("_storm_object_rows", "state.spatial_scene.entities"),
            _loop(
                "_storm_object_rows",
                "_storm_object_body",
                [
                    _when(
                        "_storm_object_body.active == true and "
                        "_storm_object_body.metadata.is_object == true and "
                        "_storm_object_body.metadata.participant_ref == null and "
                        "_storm_object_body.metadata.srd_spell_object.destroyed != true and "
                        "(len(selected_targets) == 0 or "
                        "_storm_object_body.id in selected_targets) and "
                        "(_storm_object_body.metadata.plane == record.plane or "
                        "_storm_object_body.metadata.plane == null)",
                        [
                            _require(
                                "_storm_object_body.metadata.plane != null and "
                                "_storm_object_body.footprint.kind == 'box'",
                                "Storm object collection needs actual object Boxes and planes.",
                            ),
                            _require(
                                "(_storm_object_body.metadata.carried_by == null or "
                                "_storm_object_body.metadata.carried_by == '') and "
                                "(_storm_object_body.metadata.worn_by == null or "
                                "_storm_object_body.metadata.worn_by == '')",
                                "Storm held objects need source-qualified body placement first.",
                            ),
                            _set(
                                "_storm_object_near",
                                storm.sphere._nearest("_storm_object_body", "record.origin"),
                            ),
                            _when(
                                "_storm_object_body.position[2] < record.origin[2] and "
                                "(_storm_object_near[0] - record.origin[0]) ** 2 + "
                                "(_storm_object_near[1] - record.origin[1]) ** 2 <= 300 ** 2",
                                [
                                    _require(
                                        "len(_storm_object_candidates) < 64",
                                        "Storm exceeds64 supported object sections.",
                                    ),
                                    _call(
                                        QUALIFY,
                                        {"object_id": "{_storm_object_body.id}"},
                                    ),
                                    _push("_storm_object_candidates", "_storm_object_body.id"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _object_cover(tools):
    """Use the same final physical exposure/Cover owner with actual object scene identities."""
    collect = deepcopy(tools["collect_srd_storm_cover_targets"])
    target = deepcopy(tools["qualify_srd_storm_target_cover"])
    index = next(i for i, p in enumerate(collect["parameters"]) if p["name"] == "targets")
    collect["parameters"][index] = _parameter(
        "targets", "array", items={"type": "string"}, max_items=64, unique_items=True
    )
    collect = storm.sphere._replace_names(
        collect,
        {
            "_breath_candidate_scene.metadata.participant_ref": "_breath_candidate_scene.id",
            "qualify_srd_storm_target_cover": TARGET_COVER,
        },
    )
    for parameter in target["parameters"]:
        if parameter["name"] == "target_state":
            parameter.pop("resolve", None)
    affected = [
        effect
        for sequence in _lists(target["effects"])
        for effect in sequence
        if effect.get("set", {}).get("field") == "_breath_target_affected"
    ]
    if len(affected) != 1:
        raise ValueError("Storm object Cover needs the one native affected-target result.")
    affected[0]["set"]["value"] = "_breath_target_cover != 'total'"
    return {COVER: collect, TARGET_COVER: target}


def _bind_stage_two(tools):
    """Collect before claim/damage, then reuse the shared once-adjusted Acid roll."""
    tool = deepcopy(tools[later.START])
    if PACKET in str(tool["effects"]):
        return tool
    claims = []
    for sequence in _lists(tool["effects"]):
        for effect in list(sequence):
            if effect.get("call") == later.CLAIM:
                claims.append((sequence, effect))
    if len(claims) != 1:
        raise ValueError("Storm objects need the one authentic pre-damage stage claim.")
    sequence, claim = claims[0]
    sequence.insert(0, _set("_storm_later_objects", []))
    rolls = [
        (nested, effect)
        for nested in _lists(tool["effects"])
        for effect in nested
        if effect.get("store_as") == "_storm_later_damage_roll"
    ]
    if len(rolls) != 1:
        raise ValueError("Storm objects need the authentic shared stage roll.")
    active, roll = rolls[0]
    index = active.index(roll)
    active[index:index] = [
        _set("_storm_saved_creature_targets", "_srd_area_targets"),
        _when(
            "_storm_later_row.storm_ordinal == 2",
            [
                _call(COLLECT, {"record": "{_storm_later_row}"}, ("_storm_object_candidates",)),
                _call(
                    COVER,
                    {
                        "source_state": "{_caster_state_id}",
                        "origin": "{_storm_later_row.origin}",
                        "area_plane": "{_storm_later_row.plane}",
                        "targets": "{_storm_object_candidates}",
                    },
                    ("_srd_area_targets",),
                ),
                _set("_storm_later_objects", "_srd_area_targets"),
            ],
        ),
        _set("_srd_area_targets", "_storm_saved_creature_targets"),
    ]
    for nested in _lists(tool["effects"]):
        for effect in nested:
            if effect.get("set", {}).get("field") == "_storm_later_stage_result":
                value = effect["set"]["value"]
                if isinstance(value, dict):
                    value["object_targets"] = "_storm_later_objects"
    sequence.append(
        _loop(
            "_storm_later_objects",
            "_storm_later_object",
            [
                _call(
                    PACKET,
                    {
                        "object_id": "{_storm_later_object}",
                        "amount": "{_actor_modified_damage}",
                        "damage_type": "acid",
                    },
                    ("_storm_object_summary", "_object_damage_applied"),
                ),
                _push("_storm_later_summary", "_storm_object_summary"),
                _set(
                    "_storm_later_damage_applied",
                    "_storm_later_damage_applied + _object_damage_applied",
                ),
            ],
        )
    )
    return tool


def bind_runtime_consumers(text):
    """Capture final Ice/Jar/object suffixes after their owners; bind before reaction copies."""
    tools = _load_runtime(text)["tools"]
    generated = {
        QUALIFY: _qualification(tools),
        PACKET: _packet(tools),
        COLLECT: _collector(),
        **_object_cover(tools),
        later.START: _bind_stage_two(tools),
    }
    return _replace_tools(
        text, {name: tool for name, tool in generated.items() if tools.get(name) != tool}
    )
