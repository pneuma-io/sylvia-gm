"""Flame Blade retains one paid source and its actual anatomical hand assignment."""

from copy import deepcopy

if __package__:
    from .srd_retained_spell_attacks import (
        FLAME_BLADE_PROFILE,
        _install_tool,
        _lists,
        _prepare_tool,
    )
    from .srd_scene_hand_occupancy import EXPORTS as HAND_EXPORTS
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
    from .srd_timed_senses import _loop, _optional, _public, _replace_tools
else:
    from srd_retained_spell_attacks import FLAME_BLADE_PROFILE, _install_tool, _lists, _prepare_tool
    from srd_scene_hand_occupancy import EXPORTS as HAND_EXPORTS
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
    from srd_timed_senses import _loop, _optional, _public, _replace_tools


PROFILE = FLAME_BLADE_PROFILE
PUBLIC_TOOLS = {"use_srd_flame_blade", "evoke_srd_flame_blade", "release_srd_flame_blade"}
SOURCE_EXPORTS = (
    "_flame_source",
    "_flame_source_id",
    "_flame_casting_ability",
    "_flame_attack_bonus",
    "_flame_casting_modifier",
    "_flame_dice_count",
    "_flame_max_range",
)
HAND_SOURCE_EXPORTS = ("_flame_hand_occupied", "_flame_hand_index", "_flame_hand_source_id")
ATTACK_EXPORTS = (
    "_flame_attack",
    "_flame_attack_kind",
    "_flame_attack_total",
    "_flame_armor_class",
    "_flame_damage",
    "_flame_damage_rolled",
    "_flame_damage_applied",
    "_flame_hp_after",
    "_flame_temp_after",
    "_flame_attack_dice",
    "_flame_damage_dice",
    "_flame_target",
    "_flame_source_id",
    "_flame_caster_hp_after",
    "_mirror_redirected",
    "_mirror_dice",
    "_mirror_source_id",
    "_mirror_remaining",
)


def _rename(value):
    if isinstance(value, str):
        return (
            value.replace("_retained_", "_flame_")
            .replace("has_flame_spell_attacks", "has_retained_spell_attacks")
            .replace("srd_flame_spell_attack", "srd_retained_spell_attack")
            .replace("Vampiric Touch", "Flame Blade")
        )
    if isinstance(value, list):
        return [_rename(child) for child in value]
    if isinstance(value, dict):
        return {key: _rename(child) for key, child in value.items()}
    return value


def _source_tool():
    """Reuse the retained source's current-stat owner with Flame Blade's original slot."""
    tool = _rename(_prepare_tool())
    for sequence in _lists(tool["effects"]):
        for effect in sequence:
            if "condition" in effect:
                effect["condition"] = effect["condition"].replace(
                    "'vampiric_touch'", "'flame_blade'"
                )
            if effect.get("set", {}).get("field") == "_flame_dice_count":
                effect["set"]["value"] = "_flame_source.slot_level + 1"
            if effect.get("call") == "check_srd_retained_spell_attack_record":
                effect["export_context"] = {"_flame_active": "_retained_active"}
    return tool


def _installer():
    """The existing paid Bonus Action installs, then manifests, without a second payment."""
    tool = _rename(_install_tool())
    next(p for p in tool["parameters"] if p["name"] == "slot_level")["minimum"] = 2
    tool["parameters"].append(_parameter("hand_index", "integer", minimum=0, maximum=7))
    for sequence in _lists(tool["effects"]):
        for effect in sequence:
            if "require" in effect:
                effect["require"]["condition"] = effect["require"]["condition"].replace(
                    "'vampiric_touch'", "'flame_blade'"
                )
            if "set" in effect:
                value = effect["set"].get("value")
                if isinstance(value, str):
                    effect["set"]["value"] = value.replace("vampiric_touch", "flame_blade").replace(
                        "state.lasting_tick + 20", "state.lasting_tick + 200"
                    )
                # The generic retained owner keeps the exact source actor field.
                effect["set"]["field"] = effect["set"]["field"].replace(
                    "caster_state.flame_attack_source_id", "caster_state.retained_attack_source_id"
                )
            if "list_push" in effect and isinstance(effect["list_push"].get("value"), dict):
                row = effect["list_push"]["value"]
                if row.get("kind") == "retained_spell_attack":
                    row.update(
                        spell="flame_blade",
                        expires_tick="lasting_state.lasting_tick + 200",
                        blade_manifested=False,
                        hand_index=-1,
                    )
    tool["effects"].append(
        _call(
            "manifest_srd_flame_blade",
            {"caster_state": "{_caster_state_id}", "hand_index": "{hand_index}"},
            ["_flame_source_id"],
        )
    )
    return tool


def _find_source():
    """Read only the caster's genuine installed Flame Blade instance, including suppression."""
    return [
        _set("_flame_source", None),
        _set("_flame_records", []),
        _when("state.turn_effects != null", [_set("_flame_records", "state.turn_effects")]),
        _loop(
            "_flame_records",
            "_flame_candidate",
            [
                _when(
                    "_flame_candidate.kind == 'retained_spell_attack' and "
                    "_flame_candidate.spell == 'flame_blade' and "
                    "_flame_candidate.caster == _caster_state_id and "
                    "_flame_candidate.source_id == caster_state.retained_attack_source_id",
                    [
                        _call(
                            "check_srd_retained_spell_attack_record",
                            {
                                "caster_state": "{_caster_state_id}",
                                "record": "{_flame_candidate}",
                            },
                            ["_retained_live", "_retained_active"],
                        ),
                        _when(
                            "_retained_live == true", [_set("_flame_source", "_flame_candidate")]
                        ),
                    ],
                )
            ],
        ),
    ]


def _hand_source():
    """A manifested source occupies only its recorded hand in the unchanged actual body."""
    effects = _find_source() + [
        _set("_flame_hand_occupied", False),
        _set("_flame_hand_index", -1),
        _set("_flame_hand_source_id", "''"),
        _when(
            "_flame_source != null and _retained_active == true and "
            "_flame_source.blade_manifested == true",
            [
                _require(
                    "_flame_source.hand_body.height == native_record.height and "
                    "_flame_source.hand_body.footprint.kind == native_record.footprint.kind and "
                    "_flame_source.hand_body.form_source_id == native_record.form_source_id",
                    "Flame Blade's body changed; release and evoke it in the current hand.",
                ),
                {
                    "condition": "native_record.footprint.kind == 'box'",
                    "on_true": [
                        _require(
                            " and ".join(
                                f"_flame_source.hand_body.footprint.half_extents[{i}] == "
                                f"native_record.footprint.half_extents[{i}]"
                                for i in range(2)
                            ),
                            "Flame Blade's Box body changed; release and evoke it again.",
                        )
                    ],
                    "on_false": [
                        _require(
                            "native_record.footprint.kind == 'circle' and "
                            "_flame_source.hand_body.footprint.radius == "
                            "native_record.footprint.radius",
                            "Flame Blade's circular body changed; release and evoke it again.",
                        )
                    ],
                },
                _require(
                    "_flame_source.hand_index >= 0 and "
                    "_flame_source.hand_index < native_record.capacity and "
                    "_flame_source.hand_index not in native_indices",
                    "Flame Blade's hand must remain in this anatomy and free of actual items.",
                ),
                _set("_flame_hand_occupied", True),
                _set("_flame_hand_index", "_flame_source.hand_index"),
                _set("_flame_hand_source_id", "_flame_source.source_id"),
            ],
        ),
    ]
    return _tool(
        "Qualify the actual active Flame Blade hand without changing equipment facts.",
        [
            _entity("caster_state"),
            _parameter("native_record", "object", from_state="state.spatial_scene"),
            _parameter("native_indices", "array", items={"type": "integer"}, max_items=8),
        ],
        effects,
    )


def _replace_record(patch):
    return [
        _set("_flame_record_patch", patch),
        _set("_flame_updated", "merge(_flame_source, _flame_record_patch)"),
        _set("_flame_kept", []),
        _loop(
            "_flame_records",
            "_flame_candidate",
            [
                {
                    "condition": "_flame_candidate.kind == 'retained_spell_attack' and "
                    "_flame_candidate.source_id == _flame_source.source_id",
                    "on_true": [{"list_push": {"list": "_flame_kept", "value": "_flame_updated"}}],
                    "on_false": [
                        {"list_push": {"list": "_flame_kept", "value": "_flame_candidate"}}
                    ],
                }
            ],
        ),
        _set("state.turn_effects", "_flame_kept"),
        _set("_flame_source_id", "_flame_source.source_id"),
    ]


def _manifest_tool():
    """Choose an unused anatomical hand without creating an inventory item."""
    return _tool(
        "Manifest the retained source in the chosen actual free hand.",
        [_entity("caster_state"), _parameter("hand_index", "integer", minimum=0, maximum=7)],
        _find_source()
        + [
            _require(
                "_flame_source != null and _retained_active == true",
                "No active saved Flame Blade source remains.",
            ),
            _require("_flame_source.blade_manifested != true", "Release the actual blade first."),
            _call(
                "qualify_srd_scene_hand_native_occupancy",
                {"actor_state": "{_caster_state_id}"},
                HAND_EXPORTS,
            ),
            _require(
                "hand_index < _scene_hand_capacity and hand_index not in _scene_hands_used",
                "Choose an actual free hand for Flame Blade.",
            ),
            _set(
                "_flame_hand_body",
                {
                    "footprint": "_scene_hand_record.footprint",
                    "height": "_scene_hand_record.height",
                    "form_source_id": None,
                },
            ),
            _when(
                "_scene_hand_record.form_source_id != null",
                [
                    _set(
                        "_flame_form_patch", {"form_source_id": "_scene_hand_record.form_source_id"}
                    ),
                    _set("_flame_hand_body", "merge(_flame_hand_body, _flame_form_patch)"),
                ],
            ),
            *_replace_record(
                {
                    "blade_manifested": True,
                    "hand_index": "hand_index",
                    "hand_body": "_flame_hand_body",
                }
            ),
        ],
    )


def _evoke_tool():
    return _public(
        _tool(
            "Evoke the still-living Flame Blade again with a real Bonus Action.",
            [_entity("caster_state"), _parameter("hand_index", "integer", minimum=0, maximum=7)],
            [
                _call(
                    "manifest_srd_flame_blade",
                    {
                        "caster_state": "{_caster_state_id}",
                        "hand_index": "{hand_index}",
                    },
                    ["_flame_source_id"],
                ),
                _call(
                    "spend_spell_casting_budget",
                    {
                        "caster_state": "{_caster_state_id}",
                        "casting_time": "bonus_action",
                        "slot_resource": "no_slot",
                    },
                ),
            ],
        ),
        "combat",
        "Flame Blade",
        [("Manifested hand", "hand_index"), ("Source", "_flame_source_id")],
    )


def _release_tool():
    tool = _public(
        _tool(
            "Release the actual fiery blade; the retained Concentration source can evoke it again.",
            [_entity("caster_state")],
            _find_source()
            + [
                _require("_flame_source != null", "No living saved Flame Blade source remains."),
                *_replace_record({"blade_manifested": False, "hand_index": -1}),
            ],
        ),
        "combat",
        "Release Flame Blade",
        [("Source", "_flame_source_id")],
    )
    tool["metadata"]["costs_action"] = False
    return tool


def _attack_tool(tools):
    """Add the actual casting modifier before the settled creature attack's typed mitigation."""
    tool = _rename(deepcopy(tools["resolve_srd_vampiric_touch_attack"]))
    tool["parameters"].append(_parameter("_flame_casting_modifier", "integer"))
    removed = {
        "_flame_necrotic_damage",
        "_flame_healing_due",
        "_flame_healing_applied",
        "_flame_caster_hp_before",
    }
    for sequence in _lists(tool["effects"]):
        sequence[:] = [
            e
            for e in sequence
            if e.get("set", {}).get("field") not in removed
            and "_flame_healing_due" not in e.get("condition", "")
        ]
        for effect in sequence:
            if (
                effect.get("set", {}).get("field") == "_flame_damage_rolled"
                and isinstance(effect["set"].get("value"), str)
                and "_flame_damage.total" in effect["set"]["value"]
            ):
                effect["set"]["value"] = "max(0, _flame_damage.total + _flame_casting_modifier)"
            if effect.get("call") == "apply_srd_qualifying_damage":
                effect["params"]["damage_type"] = "fire"
                effect["params"].pop("observe_damage_type", None)
                effect["params"].pop("observe_base_damage", None)
                for key in ("_source_observed_type_damage", "_source_base_effective_damage"):
                    effect["export_context"].pop(key, None)
    return tool


def _use_tool():
    effects = [
        _when("caster == null", [_set("_caster_id", None)]),
        _call(
            "prepare_srd_flame_blade_source",
            {
                "caster_state": "{_caster_state_id}",
                "caster": "{_caster_id}",
            },
            SOURCE_EXPORTS,
        ),
        _call(
            "qualify_srd_scene_hand_occupancy", {"actor_state": "{_caster_state_id}"}, HAND_EXPORTS
        ),
        _require(
            "_flame_source.blade_manifested == true and "
            "_flame_source.hand_index in _scene_hands_used",
            "Evoke the actual blade in its free anatomical hand before attacking.",
        ),
        _when(
            "combat.encounter_active != true",
            [
                _require(
                    "caster_state.flame_blade_used_tick == null or "
                    "state.lasting_tick >= caster_state.flame_blade_used_tick + 2",
                    "Advance the real exploration clock before another Magic action.",
                ),
            ],
        ),
        _call(
            "spend_spell_casting_budget",
            {
                "caster_state": "{_caster_state_id}",
                "casting_time": "action",
                "slot_resource": "no_slot",
            },
        ),
        _call(
            "resolve_srd_flame_blade_attack",
            {
                "caster_state": "{_caster_state_id}",
                "target": "{_target_id}",
                **{
                    ("source_id" if n == "_flame_source_id" else n): "{" + n + "}"
                    for n in SOURCE_EXPORTS
                    if n != "_flame_source"
                },
            },
            ATTACK_EXPORTS,
        ),
        _set("caster_state.flame_blade_used_tick", "state.lasting_tick"),
    ]
    return _public(
        _tool(
            "Take Flame Blade's genuine later Magic action melee spell attack.",
            [
                _entity("caster_state"),
                _optional("caster", resolve="participant"),
                _entity("target"),
            ],
            effects,
        ),
        "combat",
        "Flame Blade",
        [
            ("Target", "_flame_target"),
            ("Attack", "_flame_attack_kind"),
            ("Attack dice", "_flame_attack_dice"),
            ("Attack total", "_flame_attack_total"),
            ("Target AC", "_flame_armor_class"),
            ("Fire damage dice", "_flame_damage_dice"),
            ("Raw Fire amount", "_flame_damage_rolled"),
            ("All damage applied", "_flame_damage_applied"),
            ("Target HP after", "_flame_hp_after"),
            ("Target Temp HP after", "_flame_temp_after"),
            ("Mirror Image diverted", "_mirror_redirected"),
            ("Source", "_flame_source_id"),
        ],
    )


def _cast_tool():
    return _tool(
        "Manifest Flame Blade from the actual paid Bonus Action and original source facts.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer", minimum=2, maximum=9),
            _parameter(
                "casting_ability", "enum", enum_values=["intelligence", "wisdom", "charisma"]
            ),
            _parameter("concentration_save_modifier", "integer"),
            _optional("spell_cast_id", default="", model_hidden=True),
            _parameter("hand_index", "integer", minimum=0, maximum=7),
        ],
        [
            _call(
                "install_srd_flame_blade",
                {
                    "caster_state": "{_caster_state_id}",
                    "slot_level": "{slot_level}",
                    "casting_ability": "{casting_ability}",
                    "concentration_save_modifier": "{concentration_save_modifier}",
                    "spell_cast_id": "{spell_cast_id}",
                    "hand_index": "{hand_index}",
                },
                ["_flame_source_id"],
            ),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _call(
                "record_srd_spell_target_result",
                {
                    "target": "{_caster_state_id}",
                    "brief": "Flame Blade in hand {hand_index}",
                    "detail": "Flame Blade in hand {hand_index}; source {_flame_source_id}.",
                },
                ["_spell_target_summary", "_spell_target_detail"],
            ),
            {"list_push": {"list": "_spell_summary_rows", "value": "_spell_target_summary"}},
            {"list_push": {"list": "_spell_detail_rows", "value": "_spell_target_detail"}},
        ],
    )


def extend_runtime(text):
    tools = _load_runtime(text)["tools"]
    changed = {
        "prepare_srd_flame_blade_source": _source_tool(),
        "install_srd_flame_blade": _installer(),
        "manifest_srd_flame_blade": _manifest_tool(),
        "check_srd_flame_blade_hand_occupancy": _hand_source(),
        "evoke_srd_flame_blade": _evoke_tool(),
        "release_srd_flame_blade": _release_tool(),
        "resolve_srd_flame_blade_attack": _attack_tool(tools),
        "use_srd_flame_blade": _use_tool(),
        "cast_srd_flame_blade": _cast_tool(),
    }
    return _replace_tools(text, {n: t for n, t in changed.items() if tools.get(n) != t})


def bind_runtime_consumers(text):
    """Compose factual hand occupancy and paid dispatch through the existing retained owner."""
    text = extend_runtime(text)
    tools = _load_runtime(text)["tools"]
    changed = {}
    native_name = "qualify_srd_scene_hand_native_occupancy"
    if native_name not in tools:
        changed[native_name] = deepcopy(tools["qualify_srd_scene_hand_occupancy"])
    native = tools.get(native_name, changed.get(native_name))
    hand = deepcopy(native)
    if not any(p["name"] == "lasting_state" for p in hand["parameters"]):
        hand["parameters"].append(_optional("lasting_state", default="state", resolve="entity"))
    hand["effects"] = [
        _call(
            native_name,
            {
                "actor_state": "{_actor_state_id}",
                **(
                    {"lasting_state": "{_lasting_state_id}"}
                    if any(p["name"] == "lasting_state" for p in native["parameters"])
                    else {}
                ),
                "hand_capacity": "{hand_capacity}",
                "holdings": "{holdings}",
            },
            HAND_EXPORTS,
        ),
        _when(
            "lasting_state.has_retained_spell_attacks == true",
            [
                _call(
                    "check_srd_flame_blade_hand_occupancy",
                    {
                        "caster_state": "{_actor_state_id}",
                        "native_record": "{_scene_hand_record}",
                        "native_indices": "{_scene_hands_used}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                    HAND_SOURCE_EXPORTS,
                ),
                _when(
                    "_flame_hand_occupied == true",
                    [
                        {"list_push": {"list": "_scene_hands_used", "value": "_flame_hand_index"}},
                        _set(
                            "_scene_free_hand_count",
                            "_scene_hand_capacity - len(_scene_hands_used)",
                        ),
                    ],
                ),
            ],
        ),
    ]
    if tools["qualify_srd_scene_hand_occupancy"] != hand:
        changed["qualify_srd_scene_hand_occupancy"] = hand
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(tools[name])
        if any(e.get("call") == "cast_spell" for ss in _lists(tool["effects"]) for e in ss):
            continue
        if "cast_srd_flame_blade" in str(tool["effects"]):
            continue
        tool["parameters"].append(
            _optional(
                "flame_blade_hand",
                "integer",
                default=-1,
                minimum=-1,
                maximum=7,
            )
        )
        branches = [
            e
            for ss in _lists(tool["effects"])
            for e in ss
            if e.get("condition", "").endswith(
                "_spell_definition.family == 'retained_spell_attack'"
            )
            and "cast_srd_vampiric_touch" in str(e.get("on_true"))
        ]
        if len(branches) != 1:
            raise ValueError(
                "Flame Blade needs the final successful retained-family paid dispatch."
            )
        branch = branches[0]
        branch["on_true"] = [
            {
                "condition": "spell == 'flame_blade'",
                "on_true": [
                    _require(
                        "flame_blade_hand >= 0",
                        "Choose the recorded free anatomical hand for Flame Blade.",
                    ),
                    _call(
                        "cast_srd_flame_blade",
                        {
                            "caster_state": "{_caster_state_id}",
                            "slot_level": "{_slot_level}",
                            "spell_cast_id": "{_spell_source_cast_id}",
                            "hand_index": "{flame_blade_hand}",
                            "casting_ability": "{_casting_profile.ability}",
                            "concentration_save_modifier": (
                                "{_casting_profile.concentration_save_modifier}"
                            ),
                        },
                        ["_flame_source_id", "_spell_summary_rows", "_spell_detail_rows"],
                    ),
                ],
                "on_false": branch["on_true"],
            }
        ]
        changed[name] = tool
    return _replace_tools(text, changed) if changed else text
