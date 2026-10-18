"""Resolve selected Fire Bolt objects through native casting and object owners."""

from copy import deepcopy

from .build_srd_5_2_1_spells import _tool_schema
from .srd_control_behaviors import _each, _lasting
from .srd_lite_attacks import _effect_names
from .srd_lite_exploration_objects import (
    ADMIT,
    LOAD,
    _load,
    _without_scene,
)
from .srd_lite_profiles import lists
from .srd_spell_object_targets import ATTACK_EXPORTS, RELATION_EXPORTS, _validate_profile
from .srd_timed_actor_defenses import (
    _call,
    _entity,
    _parameter,
    _require,
    _set,
    _tool,
    _when,
)

FACTS = "fire_bolt_object_facts"
QUALIFY = "qualify_srd_lite_fire_bolt_object"
ATTACK = "resolve_srd_lite_fire_bolt_object_attack"
CAST = "cast_srd_lite_fire_bolt_object"
IGNITE = "ignite_srd_lite_fire_bolt_object"
CLASSIFY = "validate_srd_lite_exploration_object_damage_target"
COMBAT = "combat.encounter_active == true"
SELECTED = "_fire_object_selected"
GATE = "_ms == 'fire_bolt' and object_target_id != ''"
PARAMETER = {
    "name": FACTS,
    "type": "array",
    "required": False,
    "default": [],
    "min_items": 0,
    "max_items": 1,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "place": {"type": "string", "minLength": 1},
            "current_plane": {"type": "string", "minLength": 1},
            "plane": {"type": "string", "minLength": 1},
            "distance_feet": {"type": "number", "minimum": 0},
            "can_see": {"type": "boolean"},
            "cover": {"type": "string", "enum": ["none", "half", "three_quarters", "total"]},
            "nearby_enemy_disadvantage": {"type": "boolean"},
        },
        "required": [
            "place",
            "current_plane",
            "plane",
            "distance_feet",
            "can_see",
            "cover",
            "nearby_enemy_disadvantage",
        ],
    },
    "description": (
        "Outside combat, establish one actual Fire Bolt object receiver's current place, "
        "caster/current and object planes, distance, sight, categorical Cover and whether "
        "a nonincapacitated enemy within five feet can see the caster. Admit the actual "
        "object and classify its native material/size/durability; record combustibility "
        "and ordinary fuel facts before casting. Combat ignores these fictional facts."
    ),
}


def _unheld(body):
    return (
        f"({body}.metadata.worn_by == null or {body}.metadata.worn_by == '') and "
        f"({body}.metadata.carried_by == null or {body}.metadata.carried_by == '')"
    )


def _fuel_preflight():
    """Validate ignition's necessary physical facts before any payment or attack dice."""
    return [
        _require(
            "_object_scene.metadata.flammable != null",
            "Record the object's actual combustibility before casting Fire Bolt.",
        ),
        _when(
            "_object_scene.metadata.flammable == true and " + _unheld("_object_scene"),
            [
                _require(
                    "_object_scene.metadata.srd_ordinary_flame.kind == 'ordinary_flame'",
                    "Record the unheld flammable object's native ordinary fuel facts first.",
                ),
            ],
        ),
    ]


def _qualification():
    """Native scene authority in combat; established receiver facts outside combat."""
    exports = (*RELATION_EXPORTS, "_fire_object_enemy", "_fire_object_place")
    exploration = [
        _require("len(" + FACTS + ") == 1", "Establish one current exploration object receiver."),
        _set("_fire_receiver", FACTS + "[0]"),
        _require(
            "_fire_receiver.place != '' and _fire_receiver.current_plane != '' and "
            "_fire_receiver.plane == _fire_receiver.current_plane and "
            "len(_fire_receiver.place) <= 300 and "
            "len(_fire_receiver.current_plane) <= 100 and len(_fire_receiver.plane) <= 100",
            "Fire Bolt needs a named actual receiver on the caster's current plane.",
        ),
        *[
            _when(
                "caster_state." + field + " != null",
                [
                    _require(
                        "caster_state." + field + " == _fire_receiver.current_plane",
                        "The declared current plane contradicts the caster's native arrival.",
                    ),
                ],
            )
            for field in ("travel_location.plane", "find_path_current_plane")
        ],
        _require(
            "_fire_receiver.distance_feet <= max_range_feet and _fire_receiver.cover != 'total'",
            "The actual exploration object is beyond range or behind Total Cover.",
        ),
        _load(),
        _call(CLASSIFY, {"object_id": "{object_id}", "lasting_state": "{_lasting_state_id}"}),
        _require(
            "_object_scene.metadata.object_entity_ref == null or "
            "_object_scene.metadata.object_entity_ref == ''",
            "This source-owned exploration object's native retirement needs its scene.",
        ),
        _require(
            "_object_scene.metadata.srd_magic_jar.installed != true",
            "An installed Magic Jar container needs its native physical source-ending scene.",
        ),
        _when(
            "lasting_state.has_meld_stone_sources == true",
            [
                _set("_fire_meld_ids", "lasting_state.meld_stone_source_ids"),
                _each(
                    "_fire_meld_ids",
                    "_fire_meld_id",
                    [
                        _set(
                            "_fire_meld_source", "lasting_state.meld_stone_sources[_fire_meld_id]"
                        ),
                        _require(
                            "_fire_meld_source.installed != true or "
                            "_fire_meld_source.stone != object_id",
                            "An occupied melded stone needs its native physical containment scene.",
                        ),
                    ],
                ),
            ],
        ),
        *_validate_profile(),
        _set("_object_distance", "_fire_receiver.distance_feet"),
        _set("_object_can_see", "_fire_receiver.can_see"),
        _set("_object_cover_bonus", 0),
        _when("_fire_receiver.cover == 'half'", [_set("_object_cover_bonus", 2)]),
        _when("_fire_receiver.cover == 'three_quarters'", [_set("_object_cover_bonus", 5)]),
        _set("_object_effective_armor_class", "_object_profile.armor_class + _object_cover_bonus"),
        _call(
            "check_srd_illumination_subject",
            {"subject": "{object_id}"},
            ("_illumination_faerie", "_illumination_invisible", "_illumination_suppressed"),
        ),
        _set("_object_faerie", "_illumination_faerie == true"),
        _when(
            "_illumination_invisible == true and _illumination_suppressed != true",
            [_set("_object_can_see", False)],
        ),
        _set("_fire_object_enemy", "_fire_receiver.nearby_enemy_disadvantage"),
        _set("_fire_object_place", "_fire_receiver.place"),
    ]
    return _tool(
        "Validate the selected Fire Bolt object without allowing reported combat geometry.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("max_range_feet", "number", minimum=0),
            _lasting(),
            deepcopy(PARAMETER),
        ],
        [
            {
                "condition": COMBAT,
                "on_true": [
                    _call(
                        "resolve_srd_spell_object_relation",
                        {
                            "caster_state": "{_caster_state_id}",
                            "object_id": "{object_id}",
                            "max_range_feet": "{max_range_feet}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        RELATION_EXPORTS,
                    ),
                    _call(
                        "resolve_srd_produce_flame_nearby_enemies",
                        {
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    )
                    | {"export_context": {"_fire_object_enemy": "_produce_nearby_disadvantage"}},
                    _set("_fire_object_place", "object_id"),
                ],
                "on_false": exploration,
            },
            *_fuel_preflight(),
        ],
    ), exports


def _outside_effects(effects):
    """Use qualified object persistence, deferring map consumers until actual scene entry."""
    renamed = _effect_names(effects, {"_object_metadata_changes": "_world_changes"})
    adapted = _without_scene(
        renamed, fact_nodes={"validate_revised_srd_arcane_eye_damage_target": CLASSIFY}
    )
    for group in lists(adapted):
        for row in group:
            if row.get("call") in {
                "observe_srd_retained_web_flames",
                "refresh_srd_spell_triggers",
                "end_srd_destroyed_magic_jar_container",
                "refresh_srd_meld_stone_sources",
            }:
                original = row.get("condition")
                row["condition"] = COMBAT + (" and (" + original + ")" if original else "")
    return adapted


def _shared_object_effects(effects):
    """Share native object operations; branch only on scene reads/writes and map observers."""
    renamed = _effect_names(effects, {"_object_metadata_changes": "_world_changes"})

    def selected(rows):
        output = []
        for row in rows:
            scene_guard = row.get("require", {}).get("condition") in {
                "lasting_state.spatial_scene != null",
                "lasting_state.spatial_scene.map.grid.unit == 'ft'",
            }
            scene_leaf = (
                scene_guard
                or "spatial_query" in row
                or "spatial_mutation" in row
                or row.get("call") == "srd_information_scene_entity"
            )
            if scene_leaf:
                output.append(
                    {"condition": COMBAT, "on_true": [row], "on_false": _without_scene([row])}
                )
                continue
            if row.get("call") in {
                "observe_srd_retained_web_flames",
                "refresh_srd_spell_triggers",
                "end_srd_destroyed_magic_jar_container",
                "refresh_srd_meld_stone_sources",
            }:
                output.append(_when(COMBAT, [row]))
                continue
            for branch in ("on_true", "on_false"):
                if branch in row:
                    row[branch] = selected(row[branch])
            if "for_each" in row:
                row["for_each"]["do"] = selected(row["for_each"]["do"])
            output.append(row)
        return output

    return selected(renamed)


def _attack_tool(native):
    """Retain final native object dice, mitigation, HP writes and retirement callbacks."""
    tool = deepcopy(native)
    tool["parameters"] += [_parameter("casting_ability"), deepcopy(PARAMETER)]
    relations = 0
    for group in list(lists(tool["effects"])):
        for index in range(len(group) - 1, -1, -1):
            row = group[index]
            if row.get("call") == "resolve_srd_spell_object_relation":
                row["call"] = QUALIFY
                row["params"][FACTS] = "{" + FACTS + "}"
                relations += 1
            if row.get("call") == "d20_test":
                row["params"]["ability"] = "{casting_ability}"
            if (
                row.get("set", {}).get("field") == "_object_damage_rolled"
                and row["set"]["value"] != 0
            ):
                group[index + 1 : index + 1] = [
                    _set("_object_damage_adjusted", "_object_damage_rolled"),
                    _when(
                        "caster_state.has_actor_spell_modifiers == true",
                        [
                            _call(
                                "modify_srd_actor_damage_roll",
                                {
                                    "actor_state": "{_caster_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                    "amount": "{_object_damage_rolled}",
                                },
                            )
                            | {
                                "export_context": {
                                    "_object_damage_adjusted": "_actor_modified_damage"
                                }
                            },
                        ],
                    ),
                ]
            if (
                row.get("set", {}).get("field") == "_object_damage_applied"
                and row["set"]["value"] != 0
            ):
                row["set"]["value"] = row["set"]["value"].replace(
                    "_object_damage_rolled", "_object_damage_adjusted"
                )
    if relations != 1:
        raise ValueError("Keep the final object's one native attack relation.")
    tool["effects"] = _shared_object_effects(tool["effects"])
    return tool


def _ignite_tool(native):
    tool = deepcopy(native)
    found = 0
    for group in lists(tool["effects"]):
        for row in group:
            if row.get("condition") == (
                "_object_scene.metadata.flammable == true and "
                "_object_scene.metadata.worn_by == null and "
                "_object_scene.metadata.carried_by == null"
            ):
                row["condition"] = "_object_scene.metadata.flammable == true and " + _unheld(
                    "_object_scene"
                )
                found += 1
    if found != 1:
        raise ValueError("Keep the existing native flammable/unheld ignition guard.")
    tool["effects"] = _shared_object_effects(tool["effects"])
    return tool


def _recorded_object_roll_rows():
    """Expose recorded attack/damage dice, keeping applied object damage separate."""
    return [
        {
            "list_push": {
                "list": "_spell_detail_rows",
                "value": {
                    "label": "'Object attack (D20)'",
                    "value": "_object_attack_total",
                    "equation": {
                        "die_sides": "_object_attack_roll.sides",
                        "rolls": "_object_attack_roll.results",
                        "roll": "_object_attack_roll.total",
                        "modifier": "_object_attack_total - _object_attack_roll.total",
                        "total": "_object_attack_total",
                    },
                },
            }
        },
        _when(
            "_object_damage_roll != null",
            [
                {
                    "list_push": {
                        "list": "_spell_detail_rows",
                        "value": {
                            "label": "'Raw fire dice before object defenses (D10)'",
                            "value": "_object_damage_roll.total",
                            "equation": {
                                "die_sides": "_object_damage_roll.sides",
                                "rolls": "_object_damage_roll.results",
                                "roll": "_object_damage_roll.total",
                                "modifier": 0,
                                "total": "_object_damage_roll.total",
                            },
                        },
                    }
                },
            ],
        ),
        {
            "list_push": {
                "list": "_spell_detail_rows",
                "value": {
                    "label": "'Applied object damage after defenses and HP limit'",
                    "value": "_object_damage_applied",
                },
            }
        },
    ]


def _cast_tool(runtime, relation_exports):
    """Consume the already paid native casting profile; never create a creature recipient."""
    profile = runtime["definitions"]["spells"]["fire_bolt"]
    native = runtime["tools"]["cast_spell_attack_damage"]["effects"]
    gates = [row for row in native if row.get("condition") == "_magic_zone_effect_allowed == true"]
    zone_checks = [row for row in native if row.get("call") == "check_srd_magic_zone_effect"]
    if len(gates) != 1 or len(zone_checks) != 1:
        raise ValueError("Keep the actual native spell effect magic-zone gate.")
    gate = gates[0]
    scalars = {
        name: "_fire_object_" + name
        for name in (
            "base_dice",
            "cantrip_upgrade_dice",
            "upgrade_level_1",
            "upgrade_level_2",
            "upgrade_level_3",
            "scaling_mode",
        )
    }
    scaling = _effect_names(
        [
            row
            for row in gate["on_true"]
            if row.get("set", {}).get("field") == "_spell_damage_dice"
            and row.get("condition") != "scaling_mode == 'slot_level'"
        ],
        scalars,
    )
    if len(scaling) != 4:
        raise ValueError("Keep Fire Bolt's four original total-level damage calculations.")
    profile_inputs = [
        _set(private, repr(profile[name]) if isinstance(profile[name], str) else profile[name])
        for name, private in scalars.items()
    ]
    tool = _tool(
        "Resolve only an already paid Fire Bolt against one actual prepared object.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("casting_ability"),
            _parameter("casting_attack_bonus", "integer"),
            _parameter("casting_scaling_level", "integer", minimum=1, maximum=20),
            _parameter("advantage", "boolean", required=False, default=False),
            _parameter("disadvantage", "boolean", required=False, default=False),
            _lasting(),
            deepcopy(PARAMETER),
        ],
        [
            *profile_inputs,
            *scaling,
            _call(
                QUALIFY,
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "max_range_feet": profile["range_feet"],
                    "lasting_state": "{_lasting_state_id}",
                    FACTS: "{" + FACTS + "}",
                },
                relation_exports,
            ),
            _set("_fire_attack_disadvantage", "disadvantage == true or _fire_object_enemy == true"),
            _call(
                ATTACK,
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "attack_modifier": "{casting_attack_bonus}",
                    "casting_ability": "{casting_ability}",
                    "damage_die": profile["damage_die"],
                    "dice_count": "{_spell_damage_dice}",
                    "damage_type": "fire",
                    "max_range_feet": profile["range_feet"],
                    "advantage": "{advantage}",
                    "disadvantage": "{_fire_attack_disadvantage}",
                    "lasting_state": "{_lasting_state_id}",
                    FACTS: "{" + FACTS + "}",
                },
                ATTACK_EXPORTS,
            ),
            _set("_area_object_ignited", False),
            _when(
                "_object_attack_kind == 'hit' or _object_attack_kind == 'critical_hit'",
                [
                    _call(
                        IGNITE,
                        {"object_id": "{object_id}", "lasting_state": "{_lasting_state_id}"},
                        ("_area_object_ignited",),
                    ),
                ],
            ),
            *[
                _set(destination, source)
                for destination, source in {
                    "_spell_attack": "_object_attack_roll",
                    "_spell_attack_kind": "_object_attack_kind",
                    "_spell_attack_advantage": "_object_advantage",
                    "_spell_attack_disadvantage": "_object_disadvantage",
                    "_spell_damage": "_object_damage_roll",
                    "_visual_original_spell_damage": "_object_damage_roll",
                    "_spell_damage_amount": "_object_damage_applied",
                    "_spell_target_armor_class": "_object_effective_armor_class",
                    "_spell_casting_ability": "casting_ability",
                }.items()
            ],
            _set("_spell_attack_mode", "'ranged'"),
            _set("_spell_effective_damage_type", "'fire'"),
            _set("_spell_attack_outcome", "'success'"),
            _when("_object_attack_kind == 'miss'", [_set("_spell_attack_outcome", "'failure'")]),
            _set("_fire_raw_dice", []),
            _when(
                "_object_damage_roll != null",
                [_set("_fire_raw_dice", "_object_damage_roll.results")],
            ),
            _call(
                "record_srd_result_text",
                {
                    "text": (
                        "Object {object_id}; {_object_attack_kind}. "
                        "D20 {_object_attack_roll.results}; "
                        "total {_object_attack_total} against AC {_object_effective_armor_class}. "
                        "Fire damage {_object_damage_applied}; raw dice {_fire_raw_dice}. "
                        "Object HP {_object_hp_before} -> {_object_hp_after}. "
                        "Ignited {_area_object_ignited}."
                    )
                },
            )
            | {"export_context": {"_fire_object_detail": "_recorded_result_text"}},
            _set(
                "_spell_summary_rows", [{"label": "object_id", "value": "_object_damage_applied"}]
            ),
            _set(
                "_spell_detail_rows",
                [
                    {"label": "object_id", "value": "_fire_object_detail"},
                    {"label": "'Object location'", "value": "_fire_object_place"},
                ],
            ),
            *_recorded_object_roll_rows(),
        ],
    )
    zone_check = deepcopy(zone_checks[0])
    zone_check["params"].update(target_subject="{object_id}", actual_spell_level=0)
    tool["effects"] = [
        *tool["effects"][: len(profile_inputs)],
        _set("_fire_object_spell", "'fire_bolt'"),
        _set("_spell_hit_window_deferred", False),
        _set("_visual_original_spell_damage", None),
        _set("_magic_zone_effect_allowed", True),
        zone_check,
        {
            "condition": gate["condition"],
            "on_true": tool["effects"][len(profile_inputs) :],
            "on_false": _effect_names(gate["on_false"], {"spell": "_fire_object_spell"}),
        },
    ]
    return tool


def _transport(tools):
    """Join the current normal-pending and retained-choice capsules, preserving native costs."""
    template = "object_target_id"
    for owner in tools.values():
        if any(p.get("name") == template for p in owner.get("parameters", [])):
            owner["parameters"].append(deepcopy(PARAMETER))
    for owner in tools.values():
        for group in lists(owner.get("effects", [])):
            expanded = []
            for row in group:
                setting = row.get("set", {})
                value = setting.get("value")
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and template in item:
                            item[FACTS] = FACTS
                    if setting.get("field") == "_retained_nullable_capture_rows" and any(
                        item.get("key") == repr(template) for item in value
                    ):
                        value.append(
                            {"key": repr(FACTS), "missing": FACTS + " == null", "value": FACTS}
                        )
                if isinstance(value, dict) and template in value:
                    value[FACTS] = FACTS
                params = row.get("params", {})
                target = tools.get(row.get("call"), {})
                if template in params and any(
                    p.get("name") == FACTS for p in target.get("parameters", [])
                ):
                    params[FACTS] = params[template].replace(template, FACTS)
                expanded.append(row)
                field = setting.get("field", "")
                if field.endswith(".parameters." + template):
                    copy = deepcopy(row)
                    copy["set"]["field"] = field.removesuffix(template) + FACTS
                    if isinstance(value, str):
                        copy["set"]["value"] = value.replace(template, FACTS)
                    expanded.append(copy)
            group[:] = expanded
    tail = tools["finish_srd_retained_cast_tail"]
    for value in tail.get("metadata", {}).values():
        if isinstance(value, list) and template in value and FACTS not in value:
            value.append(FACTS)


def bind_fire_bolt_objects(runtime, record):
    """Bind after exploration objects/cantrip facts, before selected-family/private pruning."""
    tools = runtime["tools"]
    if CAST in tools:
        raise ValueError("Fire Bolt objects must be bound exactly once.")
    required = {
        LOAD,
        ADMIT,
        "resolve_srd_spell_object_attack",
        "resolve_srd_spell_object_relation",
        "ignite_srd_area_object",
        "prepare_srd_spell_object_node",
        "record_srd_ordinary_flame_node",
        "validate_revised_srd_arcane_eye_damage_target",
    }
    if missing := required - tools.keys():
        raise ValueError(
            "Keep the final native object owners before Lite pruning: " + str(sorted(missing))
        )
    profile = runtime["definitions"]["spells"]["fire_bolt"]
    if any(
        profile.get(key) != value
        for key, value in {
            "family": "spell_attack_damage",
            "base_level": 0,
            "base_dice": 1,
            "cantrip_upgrade_dice": 1,
            "scaling_mode": "caster_level",
            "damage_die": "d10",
            "range_feet": 120,
            "upgrade_level_1": 5,
            "upgrade_level_2": 11,
            "upgrade_level_3": 17,
        }.items()
    ):
        raise ValueError("Preserve the approved original Fire Bolt profile.")
    qualifier, exports = _qualification()
    guard = deepcopy(tools["validate_revised_srd_arcane_eye_damage_target"])
    guard["effects"] = _without_scene(guard["effects"])
    tools[CLASSIFY] = guard
    tools[QUALIFY] = qualifier
    tools[ATTACK] = _attack_tool(tools["resolve_srd_spell_object_attack"])
    tools[IGNITE] = _ignite_tool(tools["ignite_srd_area_object"])
    tools[CAST] = _cast_tool(runtime, exports)
    admission = tools[ADMIT]
    old = "Admit a new fictional object for Light/Mending outside combat, without map coordinates. "
    if not admission["description"].startswith(old):
        raise ValueError("Keep the current bounded exploration object admission contract.")
    admission["description"] = admission["description"].replace(
        old,
        "Admit a new fictional object for Light/Mending or Fire Bolt outside combat, "
        "without map coordinates. ",
        1,
    )
    # The existing public fact roots keep their contracts and pending-window guards.
    for name in ("prepare_srd_spell_object_node", "record_srd_ordinary_flame_node"):
        native = tools[name]["effects"]
        tools[name]["effects"] = [
            {"condition": COMBAT, "on_true": native, "on_false": _outside_effects(native)}
        ]
    _transport(tools)
    for name in ("cast_spell", "begin_srd_spell_action"):
        owner = tools[name]
        origins = [
            row
            for group in lists(owner["effects"])
            for row in group
            if row.get("set", {}).get("field") == "_fire_dispatch_origin_has_clear_path"
            and row["set"]["value"] == "origin_has_clear_path"
        ]
        if len(origins) != 1:
            raise ValueError("Keep the actual checked origin fact initializer: " + name)
        for group in lists(owner["effects"]):
            for index, row in enumerate(group):
                if row is origins[0]:
                    # QUALIFY has already established native non-total Cover for this object.
                    group[index + 1 : index + 1] = [
                        _when(
                            SELECTED + " == true",
                            [
                                _set("_fire_dispatch_origin_has_clear_path", True),
                            ],
                        ),
                    ]
                    break
        branches = [
            row
            for group in lists(owner["effects"])
            for row in group
            if any(
                child.get("require", {}).get("error")
                == "This existing creature attack family does not resolve object "
                "or environmental effects."
                for child in row.get("on_true", [])
            )
        ]
        if len(branches) != 1:
            raise ValueError("Keep the existing creature-only cantrip preflight: " + name)
        branches[0]["condition"] = "(" + branches[0]["condition"] + ") and " + SELECTED + " != true"
        owner["effects"][:0] = [
            _set("_fire_object_spell", "spell"),
            _when(
                "spell in definitions.srd_lite_aliases",
                [
                    _set("_fire_object_spell", "definitions.srd_lite_aliases[spell]"),
                ],
            ),
            _set(SELECTED, "_fire_object_spell == 'fire_bolt' and object_target_id != ''"),
            _when(
                SELECTED + " == true",
                [
                    _require(
                        "target == null and (targets == null or len(targets) == 0) and "
                        "(attack_targets == null or len(attack_targets) == 0)",
                        "Choose one Fire Bolt creature or actual object, "
                        "without fabricated recipients.",
                    ),
                    _call(
                        QUALIFY,
                        {
                            "caster_state": "{_caster_state_id}",
                            "object_id": "{object_target_id}",
                            "max_range_feet": profile["range_feet"],
                            "lasting_state": "{_lasting_state_id}",
                            FACTS: "{" + FACTS + "}",
                        },
                        exports,
                    ),
                ],
            ),
        ]
    dispatches = 0
    for owner in tools.values():
        for group in lists(owner.get("effects", [])):
            for row in group:
                if row.get("condition") != "_ms == 'sorcerous_burst' and object_target_id != ''":
                    continue
                if any(
                    child.get("call") == "cast_srd_sorcerous_burst_object"
                    for child in row.get("on_true", [])
                ):
                    call = next(
                        child
                        for child in row["on_true"]
                        if child.get("call") == "cast_srd_sorcerous_burst_object"
                    )
                    old_params = call["params"]
                    call["call"] = CAST
                    call["params"] = {
                        key: old_params[key]
                        for key in (
                            "caster_state",
                            "object_id",
                            "casting_ability",
                            "casting_attack_bonus",
                            "casting_scaling_level",
                            "advantage",
                            "disadvantage",
                        )
                    }
                    call["params"].update(
                        lasting_state="{_lasting_state_id}", **{FACTS: "{" + FACTS + "}"}
                    )
                    row["condition"] = GATE
                    row["on_true"][0]["require"]["error"] = (
                        "Choose one Fire Bolt creature or object target."
                    )
                    dispatches += 1
                elif row.get("on_true") == [_set("_control_harm_scalar_target", None)]:
                    row["condition"] = GATE
    if dispatches != 1:
        raise ValueError("Keep the current paid object-dispatch site exactly once.")
    for declaration in record["tools"]:
        if declaration["name"] in tools and any(
            p.get("name") == FACTS for p in tools[declaration["name"]].get("parameters", [])
        ):
            declaration["input_schema"] = _tool_schema(tools[declaration["name"]])
    return [
        QUALIFY,
        ATTACK,
        CAST,
        IGNITE,
        CLASSIFY,
        "prepare_srd_spell_object_node",
        "record_srd_ordinary_flame_node",
    ]
