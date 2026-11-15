"""Actual exploding attack dice and selected prismatic rays share SRD damage owners."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import (
        _call,
        _emit,
        _entity,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_progressive_saves import progressive_save_tool
    from .srd_sorcerous_burst_objects import (
        burst_extra_dice_limit_effects,
        burst_extra_dice_limit_parameter,
        compose_object_burst,
    )
    from .srd_spell_damage_consumers import _effect_lists
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_actor_spell_modifiers import (
        _call,
        _emit,
        _entity,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_progressive_saves import progressive_save_tool
    from srd_sorcerous_burst_objects import (
        burst_extra_dice_limit_effects,
        burst_extra_dice_limit_parameter,
        compose_object_burst,
    )
    from srd_spell_damage_consumers import _effect_lists
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


PUBLIC_TOOLS = {"resolve_srd_prismatic_violet_destination"}
RAYS = ("red", "orange", "yellow", "green", "blue", "indigo", "violet")
TYPES = ("fire", "acid", "lightning", "poison", "cold")


def _push(field, value):
    return {"list_push": {"list": field, "value": value}}


def _each(collection, item, effects):
    return {"for_each": {"collection": collection, "as": item, "do": effects}}


def _source(field):
    return "state.timed_condition_sources.{source_id}." + field


def _sorcerous_burst(tools, changed):
    """Explode actual raw attack dice before modifiers and retain the ability-based cap."""
    option = _parameter(
        "sorcerous_burst_extra_dice",
        "boolean",
        required=False,
        default=True,
    )
    options = [option, burst_extra_dice_limit_parameter()]
    attack = deepcopy(tools["cast_spell_attack_damage"])
    for parameter in options:
        if parameter["name"] not in {p["name"] for p in attack["parameters"]}:
            attack["parameters"].append(deepcopy(parameter))
    install_limit = "_burst_extra_dice_limit" not in str(attack["effects"])
    for effects in _effect_lists(attack["effects"]):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get("store_as") == "_spell_damage" and "dice" in effect:
                effect["dice"].update(
                    explode="spell == 'sorcerous_burst' and sorcerous_burst_extra_dice == true",
                    explode_on=8,
                    max_explosions="_burst_extra_dice_limit",
                )
                if install_limit:
                    effects[index:index] = burst_extra_dice_limit_effects()
    changed["cast_spell_attack_damage"] = attack
    for name in ("cast_spell", "cast_spell_outside_combat"):
        cast = deepcopy(tools[name])
        for parameter in options:
            if parameter["name"] not in {p["name"] for p in cast["parameters"]}:
                cast["parameters"].append(deepcopy(parameter))
        for effects in _effect_lists(cast["effects"]):
            for effect in effects:
                if effect.get("call") == "cast_spell_attack_damage":
                    for parameter in options:
                        effect["params"][parameter["name"]] = "{" + parameter["name"] + "}"
        changed[name] = cast


def _prismatic_roll_tools():
    """Reroll actual 8s through a local work queue under the unchanged effect budget."""
    return {
        "roll_srd_prismatic_non_special_ray": _tool(
            "Roll one actual Prismatic ray, retaining every rerolled 8.",
            [
                {
                    "name": "history",
                    "type": "array",
                    "items": {"type": "integer"},
                    "required": False,
                    "default": [],
                }
            ],
            [
                _set("_prismatic_roll_history", "history"),
                _set("_prismatic_reroll_queue", [1]),
                _each(
                    "_prismatic_reroll_queue",
                    "_prismatic_reroll",
                    [
                        {"dice": {"expression": "1d8"}, "store_as": "_prismatic_ray_roll"},
                        _push("_prismatic_roll_history", "_prismatic_ray_roll.total"),
                        _set("_prismatic_ray", "_prismatic_ray_roll.total"),
                        _when("_prismatic_ray == 8", [_push("_prismatic_reroll_queue", 1)]),
                    ],
                ),
            ],
        ),
        "roll_srd_prismatic_rays": _tool(
            "Select the actual one or two rays for one target without caller outcomes.",
            [
                _parameter(
                    "ray_labels",
                    "array",
                    items={"type": "string"},
                    min_items=8,
                    max_items=8,
                )
            ],
            [
                {"dice": {"expression": "1d8"}, "store_as": "_prismatic_first_roll"},
                _set(
                    "_prismatic_ray_selection",
                    {
                        "rays": ["_prismatic_first_roll.total"],
                        "history": ["_prismatic_first_roll.total"],
                        "labels": ["ray_labels[_prismatic_first_roll.total - 1]"],
                    },
                ),
                _when(
                    "_prismatic_first_roll.total == 8",
                    [
                        _set("_prismatic_rays", []),
                        _set("_prismatic_history", ["_prismatic_first_roll.total"]),
                        _set("_prismatic_twice", [1, 2]),
                        _each(
                            "_prismatic_twice",
                            "_prismatic_special_index",
                            [
                                _call(
                                    "roll_srd_prismatic_non_special_ray",
                                    {
                                        "history": "{_prismatic_history}",
                                    },
                                    ("_prismatic_ray", "_prismatic_roll_history"),
                                ),
                                _set("_prismatic_history", "_prismatic_roll_history"),
                                _push("_prismatic_rays", "_prismatic_ray"),
                            ],
                        ),
                        _set(
                            "_prismatic_ray_selection",
                            {
                                "rays": "_prismatic_rays",
                                "history": "_prismatic_history",
                                "labels": [
                                    "ray_labels[_prismatic_rays[0] - 1]",
                                    "ray_labels[_prismatic_rays[1] - 1]",
                                ],
                            },
                        ),
                    ],
                ),
            ],
        ),
    }


def _prismatic_source_tools():
    """Install printed instantaneous consequences in the existing timed source pool."""
    return {
        "install_srd_prismatic_consequence": _tool(
            "Install an Indigo or Violet consequence with its actual save and caster turn.",
            [
                _entity("target_state"),
                _entity("caster_state"),
                _parameter("ray", "integer"),
                _parameter("save_dc", "integer"),
            ],
            [
                _require(
                    "ray == 6 or ray == 7",
                    "A Prismatic consequence requires an actual Indigo or Violet ray.",
                ),
                _set("_prismatic_condition", "'restrained'"),
                _when("ray == 7", [_set("_prismatic_condition", "'blinded'")]),
                _call(
                    "install_srd_timed_condition_source",
                    {
                        "target_state": "{_target_state_id}",
                        "caster_state": "{_caster_state_id}",
                        "lasting_state": "state",
                        "spell": "prismatic_spray",
                        "condition": "{_prismatic_condition}",
                        "save_dc": "{save_dc}",
                        "duration_rounds": 0,
                        "repeat_save_timing": "none",
                        "save_ability": "constitution",
                        "concentration_sequence": 0,
                    },
                    {"_prismatic_source_id": "_timed_condition_source_id"},
                ),
                _call(
                    "initialize_srd_prismatic_consequence",
                    {
                        "target_state": "{_target_state_id}",
                        "caster_state": "{_caster_state_id}",
                        "source_id": "{_prismatic_source_id}",
                        "ray": "{ray}",
                    },
                ),
            ],
        ),
        "initialize_srd_prismatic_consequence": _tool(
            "Bind cumulative saves and the next caster turn to an installed source.",
            [
                _entity("target_state"),
                _entity("caster_state"),
                _parameter("source_id"),
                _parameter("ray", "integer"),
            ],
            [
                _set(_source("prismatic_ray"), "ray"),
                _set(_source("progressive_successes"), 0),
                _set(_source("progressive_failures"), 0),
                _set(_source("prismatic_last_target_turn"), -1),
                _set(_source("prismatic_caster_turn"), 0),
                _when(
                    "caster_state.turns_started != null",
                    [
                        _set(_source("prismatic_caster_turn"), "caster_state.turns_started"),
                    ],
                ),
                _set(_source("prismatic_travel_pending"), False),
                _set(_source("dispel_ongoing"), False),
                _set(_source("prismatic_indigo_finished"), False),
                _call(
                    "record_srd_result_text",
                    {
                        "text": "prismatic-violet:{source_id}:{state.lasting_serial}",
                    },
                    {"_prismatic_event_id": "_recorded_result_text"},
                ),
                _set(_source("prismatic_event_id"), "_prismatic_event_id"),
                _set("target_state.has_prismatic_sources", True),
                _set("caster_state.has_prismatic_violet_targets", True),
                _when(
                    "caster_state.prismatic_violet_source_ids == null",
                    [
                        _set("caster_state.prismatic_violet_source_ids", []),
                    ],
                ),
                _when(
                    "ray == 7",
                    [
                        _when(
                            "source_id not in caster_state.prismatic_violet_source_ids",
                            [
                                _set(
                                    "_prismatic_violet_ids",
                                    "caster_state.prismatic_violet_source_ids",
                                ),
                                _push("_prismatic_violet_ids", "source_id"),
                                _set(
                                    "caster_state.prismatic_violet_source_ids",
                                    "_prismatic_violet_ids",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }


def _prismatic_packet_update(**fields):
    """Replace the ephemeral packet as one value; local Set fields do not traverse maps."""
    keys = (
        "component_1",
        "component_2",
        "types",
        "labels",
        "hp_before",
        "divisor",
        "remainder",
        "has_damage",
        "blocked",
        "advantage",
        "evasion",
        "divisors",
        "cover_bonus",
    )
    return _set("_prism", {key: fields.get(key, f"_prism.{key}") for key in keys})


def _prismatic_target_tool(tools):
    """Halve one target packet across source-selected typed rays before its Curse component."""
    original = tools["resolve_spell_area_save_damage_target"]
    split = next(
        i for i, e in enumerate(original["effects"]) if e.get("call") == "resolve_srd_save_damage"
    )
    effects = deepcopy(original["effects"][:split])
    advantage = next(
        effect["set"]["value"]
        for effect in effects
        if effect.get("set", {}).get("field") == "_spell_save_advantage"
    )
    effects = [
        effect
        for effect in effects
        if effect.get("set", {}).get("field")
        not in {
            "_spell_save_modifier",
            "_area_hp_before",
            "_spell_save_disadvantage",
            "_spell_save_advantage",
        }
    ]
    save = next(effect for effect in effects if effect.get("call") == "d20_test")
    save["params"]["ability_modifier"] = "{target.saving_throw_modifiers.dexterity}"
    save["params"]["disadvantage"] = False
    save["params"]["advantage"] = "{_prism.advantage}"
    save["params"]["circumstantial_modifier"] = "{_prism.cover_bonus}"
    cover = next(
        effect for effect in effects if "half_cover_targets" in effect.get("condition", "")
    )
    cover.pop("on_false", None)
    for branches in (cover["on_true"][0]["on_true"], cover["on_true"][0]["on_false"]):
        branches[0] = _prismatic_packet_update(cover_bonus=branches[0]["set"]["value"])
    evasion = deepcopy(tools["resolve_srd_save_damage"]["effects"])
    eligibility = next(
        effect
        for effect in evasion
        if effect.get("on_true", [{}])[0].get("call") == "derive_srd_evasion"
    )
    eligibility["condition"] = (
        eligibility["condition"]
        .replace("target_state.", "target.")
        .replace("save_ability", "'dexterity'")
        .replace("success_damage", "'half'")
    )
    eligibility["on_true"][0]["params"]["actor"] = "{_target_id}"
    eligibility["on_true"][0]["export_context"] = {"_prism_evasion_available": "_evasion_available"}
    eligibility["condition"] = "_prism.has_damage == true and " + eligibility["condition"]
    eligibility["on_true"].append(_prismatic_packet_update(evasion="_prism_evasion_available"))
    eligibility["on_true"].append(
        _when(
            "_prism_evasion_available == true",
            [
                _prismatic_packet_update(divisors={"success": 2, "failure": 2}),
            ],
        )
    )
    effects.insert(
        0,
        _set(
            "_prism",
            {
                "component_1": {"present": False, "amount": 0, "damage_type": "fire"},
                "component_2": {"present": False, "amount": 0, "damage_type": "fire"},
                "types": ["'" + t + "'" for t in TYPES],
                "labels": ["'" + t + "'" for t in RAYS],
                "hp_before": "target.hp + target.temp_hp",
                "divisor": 1,
                "remainder": 0,
                "has_damage": "min(rays) <= 5",
                "blocked": False,
                "advantage": advantage,
                "evasion": False,
                "divisors": {"success": 2, "failure": 1},
                "cover_bonus": 0,
            },
        ),
    )
    effects += [
        eligibility,
    ]
    special = []
    for index in range(2):
        ray = f"rays[{index}]"
        color = [
            _when(
                f"{ray} <= 5 and _prism.blocked == false",
                [
                    _when(
                        "_prism.component_1.present == false",
                        [
                            _prismatic_packet_update(
                                component_1={
                                    "present": True,
                                    "amount": "(damage_total + _prism.remainder) // _prism.divisor",
                                    "damage_type": f"_prism.types[{ray} - 1]",
                                },
                            )
                        ],
                        [
                            _prismatic_packet_update(
                                component_2={
                                    "present": True,
                                    "amount": "(damage_total + _prism.remainder) // _prism.divisor",
                                    "damage_type": f"_prism.types[{ray} - 1]",
                                },
                            )
                        ],
                    ),
                    _prismatic_packet_update(
                        remainder="(damage_total + _prism.remainder) % _prism.divisor"
                    ),
                ],
            ),
        ]
        special += color
    packet = {
        "target": "{_target_id}",
        "source_state_id": "{source_state_id}",
        "witness_state_ids": "{witness_state_ids}",
        "game_damage_roll": True,
        "damage_roll_divisor": "{_prism.divisor}",
        "damage_roll_base_remainder": "{_prism.remainder}",
    }
    special += [
        _when(
            "_prism.component_1.present == true",
            [
                _when(
                    (
                        "_prism.component_2.present == true and _prism.component_1.damage_type != "
                        "_prism.component_2.damage_type"
                    ),
                    [
                        _call(
                            "apply_srd_qualifying_composite_damage",
                            packet
                            | {
                                "component_1_amount": "{_prism.component_1.amount}",
                                "component_1_damage_type": "{_prism.component_1.damage_type}",
                                "component_2_amount": "{_prism.component_2.amount}",
                                "component_2_damage_type": "{_prism.component_2.damage_type}",
                            },
                        ),
                    ],
                    [
                        _when(
                            "_prism.component_2.present == true",
                            [
                                _prismatic_packet_update(
                                    component_1={
                                        "present": True,
                                        "amount": (
                                            "_prism.component_1.amount + _prism.component_2.amount"
                                        ),
                                        "damage_type": "_prism.component_1.damage_type",
                                    }
                                )
                            ],
                        ),
                        _call(
                            "apply_srd_qualifying_damage",
                            packet
                            | {
                                "amount": "{_prism.component_1.amount}",
                                "adjustment": 0,
                                "damage_type": "{_prism.component_1.damage_type}",
                            },
                        ),
                    ],
                ),
            ],
        ),
    ]
    ordinary_damage = [
        _prismatic_packet_update(
            component_1={
                "present": True,
                "amount": "damage_total // _prism.divisors[_spell_save_outcome]",
                "damage_type": "_prism.types[rays[0] - 1]",
            },
            remainder="damage_total % _prism.divisors[_spell_save_outcome]",
            divisor="_prism.divisors[_spell_save_outcome]",
        ),
        _call(
            "apply_srd_qualifying_damage",
            packet
            | {
                "amount": "{_prism.component_1.amount}",
                "adjustment": 0,
                "damage_type": "{_prism.component_1.damage_type}",
            },
        )
        | {"condition": "not (_spell_save_outcome == 'success' and _prism.evasion == true)"},
    ]

    def consequence(ray):
        return _call(
            "install_srd_prismatic_consequence",
            {
                "target_state": "{_target_id}",
                "caster_state": "{source_state_id}",
                "ray": ray,
                "save_dc": "{spell_save_dc}",
            },
        )

    effects += [
        _when(
            "len(rays) == 2",
            [
                _when(
                    "_prism.has_damage == true and not (_spell_save_outcome == 'success' "
                    "and _prism.evasion == true)",
                    [_prismatic_packet_update(divisor="_prism.divisors[_spell_save_outcome]")]
                    + special,
                ),
                _when(
                    "_spell_save_outcome == 'failure' and max(rays) >= 6",
                    [
                        _when("6 in rays", [consequence(6)]),
                        _when("7 in rays", [consequence(7)]),
                    ],
                ),
            ],
            [
                _when(
                    "rays[0] <= 5",
                    ordinary_damage,
                    [
                        _when("_spell_save_outcome == 'failure'", [consequence("{rays[0]}")]),
                    ],
                ),
            ],
        ),
        _set("_spell_damage_amount", "max(0, _prism.hp_before - target.hp - target.temp_hp)"),
        _call(
            "record_srd_prismatic_target_result",
            {
                "target": "{_target_id}",
                "prior_rows": "{prior_rows}",
                "brief": (
                    "{_spell_save_outcome}; rays {ray_labels}; damage {_spell_damage_amount}"
                ),
                "detail": (
                    "Dexterity save {_spell_save_outcome}; rays {ray_labels}; ray "
                    "rolls {ray_history}; damage {_spell_damage_amount}."
                ),
            },
            ("_spell_detail_rows",),
        ),
    ]
    tool = deepcopy(original)
    tool["parameters"] += [
        _entity("caster_state"),
        _parameter("rays", "array", items={"type": "integer"}, min_items=1, max_items=2),
        _parameter("ray_history", "array", items={"type": "integer"}, min_items=1),
        _parameter("ray_labels", "array", items={"type": "string"}, min_items=1, max_items=2),
        _parameter(
            "prior_rows",
            "array",
            items={"type": "object"},
            required=False,
            default=[],
            min_items=0,
            max_items=64,
            model_hidden=True,
        ),
    ]
    tool["description"] = "Resolve one target's actual Prismatic rays and shared Dexterity save."
    tool["effects"] = effects
    return tool


def _prismatic_result_tool(tools):
    """Accumulate the existing native card's actual detail rows without unused summary rows."""
    tool = deepcopy(tools["record_srd_spell_area_target_result"])
    tool["parameters"].append(
        _parameter(
            "prior_rows",
            "array",
            items={"type": "object"},
            min_items=0,
            max_items=64,
            model_hidden=True,
        )
    )
    detail = next(
        effect["set"]["value"]
        for effect in tool["effects"]
        if effect.get("set", {}).get("field") == "_spell_detail_rows"
    )
    if not isinstance(detail, list) or len(detail) != 1:
        raise SystemExit("Prismatic Spray needs the actual native area card row contract.")
    tool["effects"] = [
        _set("_spell_detail_rows", "prior_rows"),
        _push("_spell_detail_rows", detail[0]),
    ]
    return tool


def _prismatic_geometry_parameters():
    """Expose the creator's actual origin and direction choices for this Self Cone."""
    return [
        _parameter(
            "prismatic_apex",
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            min_items=0,
            max_items=3,
        ),
        _parameter(
            "prismatic_direction",
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            min_items=0,
            max_items=3,
        ),
        _parameter("prismatic_include_origin", "boolean", required=False, default=False),
    ]


def _prismatic_area_tool(tools):
    """Derive actual Cone creatures and physical Cover before their rays and saves."""
    tool = deepcopy(tools["cast_spell_instantaneous_area_save_damage"])
    if (
        len(tool["effects"]) == 1
        and tool["effects"][0].get("condition") == "spell == 'prismatic_spray'"
    ):
        tool["effects"] = tool["effects"][0]["on_false"]
    split = next(
        i for i, e in enumerate(tool["effects"]) if e.get("condition") == "_spell_damage_dice > 0"
    )
    start = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_spell_casting_ability"
    )
    tool["parameters"] = [
        p
        for p in tool["parameters"]
        if p["name"] not in {"targets", "half_cover_targets", "three_quarters_cover_targets"}
    ]
    tool["parameters"] += _prismatic_geometry_parameters()
    tool["effects"] = (
        [
            _call(
                "collect_srd_cone_creature_targets",
                {
                    "source_state": "{_caster_state_id}",
                    "length_feet": 60,
                    "apex": "{prismatic_apex}",
                    "direction": "{prismatic_direction}",
                    "include_origin": "{prismatic_include_origin}",
                },
                ("_srd_cone_targets", "_srd_cone_origin", "_srd_cone_axis", "_srd_cone_source"),
            ),
            _set("_prismatic_source_frames", ["_srd_cone_source"]),
            _call(
                "collect_srd_spell_area_cover_targets",
                {
                    "source_state": "{_caster_state_id}",
                    "targets": "{_srd_cone_targets}",
                    "origin": "{_srd_cone_origin}",
                    "axis": "{_srd_cone_axis}",
                    "source_frames": "{_prismatic_source_frames}",
                },
                ("_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters"),
            ),
            _require("len(_srd_area_targets) <= 64", "Resolve at most 64 affected creatures."),
            _when(
                "'charmed' in caster_state.conditions",
                [
                    _call(
                        "validate_srd_spell_charmer_targets",
                        {
                            "caster_state": "{_caster_state_id}",
                            "targets": "{_srd_area_targets}",
                        },
                    ),
                ],
            ),
            _set("_spell_area_target_count", "len(_srd_area_targets)"),
            _set("_spell_area_last_save", None),
            _set("_spell_area_last_save_outcome", "'none'"),
            _set("_spell_area_last_save_advantage", False),
            _set("_spell_area_last_save_disadvantage", False),
            _set("_spell_area_last_damage_amount", 0),
        ]
        + tool["effects"][start:split]
        + [
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _set("_prismatic_selections", []),
            _set("_prismatic_ray_names", ["'" + label + "'" for label in (*RAYS, "special")]),
            _set("_prismatic_has_damage", False),
            _each(
                "_srd_area_targets",
                "_prismatic_selection_target",
                [
                    _call(
                        "roll_srd_prismatic_rays",
                        {
                            "ray_labels": "{_prismatic_ray_names}",
                        },
                        ("_prismatic_ray_selection",),
                    ),
                    _push(
                        "_prismatic_selections",
                        {
                            "target": "_prismatic_selection_target",
                            "rays": "_prismatic_ray_selection.rays",
                            "history": "_prismatic_ray_selection.history",
                            "labels": "_prismatic_ray_selection.labels",
                        },
                    ),
                    _set(
                        "_prismatic_has_damage",
                        "_prismatic_has_damage == true or min(_prismatic_ray_selection.rays) <= 5",
                    ),
                ],
            ),
            _when(
                "_prismatic_has_damage == true",
                [
                    {"dice": {"expression": "12d6"}, "store_as": "_spell_damage"},
                    _set("_spell_damage_total", "_spell_damage.total"),
                    _when(
                        "caster_state.has_actor_spell_modifiers == true",
                        [
                            _call(
                                "modify_srd_actor_damage_roll",
                                {
                                    "actor_state": "{_caster_state_id}",
                                    "lasting_state": "state",
                                    "amount": "{_spell_damage_total}",
                                },
                                {"_spell_damage_total": "_actor_modified_damage"},
                            ),
                        ],
                    ),
                ],
            ),
            _each(
                "_prismatic_selections",
                "_prismatic_selection",
                [
                    _call(
                        "resolve_srd_prismatic_target",
                        {
                            "spell": "prismatic_spray",
                            "target": "{_prismatic_selection.target}",
                            "save_ability": "dexterity",
                            "spell_save_dc": "{casting_save_dc}",
                            "damage_total": "{_spell_damage_total}",
                            "rays": "{_prismatic_selection.rays}",
                            "ray_history": "{_prismatic_selection.history}",
                            "ray_labels": "{_prismatic_selection.labels}",
                            "prior_rows": "{_spell_detail_rows}",
                            "damage_type": "fire",
                            "success_damage": "half",
                            "construct_save_disadvantage": False,
                            "half_cover_targets": "{_srd_area_half_cover}",
                            "three_quarters_cover_targets": "{_srd_area_three_quarters}",
                            "source_state_id": "{_caster_state_id}",
                            "witness_state_ids": "{witness_state_ids}",
                            "caster_state": "{_caster_state_id}",
                        },
                        {
                            "_spell_detail_rows": "_spell_detail_rows",
                            "_spell_area_last_save": "_spell_save",
                            "_spell_area_last_save_outcome": "_spell_save_outcome",
                            "_spell_area_last_save_advantage": "_spell_save_advantage",
                            "_spell_area_last_save_disadvantage": "_spell_save_disadvantage",
                            "_spell_area_last_damage_amount": "_spell_damage_amount",
                        },
                    ),
                ],
            ),
        ]
    )
    return tool


def _prismatic_turn_tools():
    """Keep actual repeat saves and a delayed planar destination in durable source records."""
    retire = [
        _call(
            "retire_srd_timed_condition_source",
            {"lasting_state": "state", "source_id": "{source_id}"},
        ),
        _call(
            "refresh_srd_timed_condition_target",
            {"target_state": "{_target_state_id}", "lasting_state": "state"},
        ),
    ]
    return {
        "resolve_srd_progressive_save": progressive_save_tool(),
        "resolve_srd_prismatic_indigo_source": _tool(
            "Resolve one actual cumulative Indigo save at this target turn's end.",
            [_entity("target_state"), _parameter("source_id")],
            [
                _call(
                    "resolve_srd_progressive_save",
                    {"target_state": "{_target_state_id}", "source_id": "{source_id}"},
                    (
                        "_progressive_save_roll",
                        "_progressive_save_outcome",
                        "_progressive_save_total",
                        "_progressive_successes",
                        "_progressive_failures",
                    ),
                ),
                _set(_source("prismatic_last_target_turn"), "target_state.turns_started"),
                _set("_prismatic_end_text", "'Indigo continues.'"),
                _when(
                    "_progressive_successes >= 3",
                    [
                        *retire,
                        _set("_prismatic_end_text", "'Three successes end Indigo.'"),
                    ],
                ),
                _when(
                    "_progressive_failures >= 3",
                    [
                        _set(_source("condition"), "'petrified'"),
                        _set(_source("prismatic_indigo_finished"), True),
                        _call(
                            "refresh_srd_timed_condition_target",
                            {"target_state": "{_target_state_id}", "lasting_state": "state"},
                        ),
                        _when(
                            (
                                "target_state.concentration_spell_id != null and 'petrified' "
                                "in target_state.conditions"
                            ),
                            [
                                _call(
                                    "end_srd_concentration_node",
                                    {
                                        "source_state": "{_target_state_id}",
                                        "target_state": (
                                            "{target_state.concentration_target_state_id}"
                                        ),
                                    },
                                ),
                            ],
                        ),
                        _set(
                            "_prismatic_end_text",
                            "'Three failures cause Petrified until restoration.'",
                        ),
                    ],
                ),
                _call(
                    "record_srd_end_turn_save",
                    {
                        "label": "Prismatic Spray: Indigo",
                        "value": (
                            "Constitution {_progressive_save_outcome} "
                            "({_progressive_save_total}); successes "
                            "{_progressive_successes}/3, failures {_progressive_failures}/3. "
                            "{_prismatic_end_text}"
                        ),
                    },
                    {"_prismatic_save_row": "_recorded_save_row"},
                ),
            ],
        ),
        "resolve_srd_prismatic_indigo_end_turn": _tool(
            "Read exact active Indigo sources and retain one save per actual target turn.",
            [_entity("target_state")],
            [
                _set("_prismatic_end_rows", []),
                _set("_prismatic_end_rolls", []),
                _call(
                    "refresh_srd_timed_condition_target",
                    {"target_state": "{_target_state_id}", "lasting_state": "state"},
                ),
                _set("_prismatic_ids", "target_state.timed_condition_source_ids"),
                _each(
                    "_prismatic_ids",
                    "_prismatic_id",
                    [
                        _set("_prismatic_record", "state.timed_condition_sources[_prismatic_id]"),
                        _when(
                            (
                                "_prismatic_record.installed == true and "
                                "_prismatic_record.spell_id == 'prismatic_spray' and "
                                "_prismatic_record.prismatic_ray == 6 and "
                                "_prismatic_record.prismatic_indigo_finished != true and "
                                "_prismatic_record.prismatic_last_target_turn != "
                                "target_state.turns_started"
                            ),
                            [
                                _call(
                                    "resolve_srd_prismatic_indigo_source",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "source_id": "{_prismatic_id}",
                                    },
                                    ("_prismatic_save_row", "_progressive_save_roll"),
                                ),
                                _push("_prismatic_end_rows", "_prismatic_save_row"),
                                _push("_prismatic_end_rolls", "_progressive_save_roll"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "resolve_srd_prismatic_violet_source": _tool(
            (
                "Resolve Violet's saved Wisdom save once, end Blinded and retain a failed "
                "planar event."
            ),
            [_entity("target_state"), _parameter("source_id")],
            [
                _set("_prismatic_record", "state.timed_condition_sources[source_id]"),
                _call(
                    "d20_test",
                    {
                        "actor_state": "{_target_state_id}",
                        "ability": "wisdom",
                        "kind": "saving_throw",
                        "ability_modifier": "{target_state.saving_throw_modifiers.wisdom}",
                        "proficiency_bonus": 0,
                        "proficiency_factor": "none",
                        "circumstantial_modifier": 0,
                        "target_number": "{_prismatic_record.save_dc}",
                        "advantage": False,
                        "disadvantage": False,
                        "forego_save": False,
                    },
                    {
                        "_prismatic_violet_save": "_d20_test",
                        "_prismatic_violet_outcome": "_outcome",
                    },
                ),
                _set("_prismatic_violet_text", "'Blinded ends.'"),
                _when(
                    "_prismatic_violet_outcome == 'failure'",
                    [
                        _when(
                            "state.prismatic_violet_pending == null",
                            [_set("state.prismatic_violet_pending", {})],
                        ),
                        _set("_prismatic_event_id", "_prismatic_record.prismatic_event_id"),
                        _set(
                            "state.prismatic_violet_pending.{_prismatic_event_id}",
                            {
                                "pending": True,
                                "target_state_id": "_target_state_id",
                                "source_id": "source_id",
                                "caster_state_id": "_prismatic_record.caster_state_id",
                                "save_roll": "_prismatic_violet_save",
                                "save_outcome": "_prismatic_violet_outcome",
                            },
                        ),
                        _set(
                            "_prismatic_violet_text",
                            "'Blinded ends; choose an admitted destination on another plane.'",
                        ),
                    ],
                ),
                *retire,
                _call(
                    "record_srd_end_turn_save",
                    {
                        "label": "Prismatic Spray: Violet",
                        "value": (
                            "Wisdom {_prismatic_violet_outcome}. {_prismatic_violet_text} "
                            "Event {_prismatic_record.prismatic_event_id}."
                        ),
                    },
                    {"_prismatic_violet_row": "_recorded_save_row"},
                ),
            ],
        ),
        "resolve_srd_prismatic_violet_start_turn": _tool(
            "Resolve Violet at the start of its actual caster's next turn.",
            [_entity("caster_state")],
            [
                _set("_prismatic_violet_rows", []),
                _set("_prismatic_violet_ids", "caster_state.prismatic_violet_source_ids"),
                _each(
                    "_prismatic_violet_ids",
                    "_prismatic_id",
                    [
                        _set("_prismatic_record", "state.timed_condition_sources[_prismatic_id]"),
                        _when(
                            (
                                "_prismatic_record != null and _prismatic_record.installed == "
                                "true and _prismatic_record.spell_id == 'prismatic_spray' and "
                                "_prismatic_record.prismatic_ray == 7 and "
                                "_prismatic_record.caster_state_id == _caster_state_id and "
                                "caster_state.turns_started > "
                                "_prismatic_record.prismatic_caster_turn"
                            ),
                            [
                                _call(
                                    "resolve_srd_prismatic_violet_source",
                                    {
                                        "target_state": "{_prismatic_record.target_state_id}",
                                        "source_id": "{_prismatic_id}",
                                    },
                                    ("_prismatic_violet_row",),
                                ),
                                _push("_prismatic_violet_rows", "_prismatic_violet_row"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "resolve_srd_prismatic_violet_destination": _tool(
            (
                "Select an admitted GM-chosen other-plane destination for an actual failed "
                "Violet save."
            ),
            [_parameter("event_id"), _parameter("destination_anchor")],
            [
                _set("_prismatic_pending", "state.prismatic_violet_pending[event_id]"),
                _require(
                    (
                        "_prismatic_pending != null and _prismatic_pending.pending == true and "
                        "_prismatic_pending.save_outcome == 'failure'"
                    ),
                    "Choose a destination only for an actual pending failed Violet save.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{destination_anchor}"},
                    {"_prismatic_anchor": "_info_scene_entity"},
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_prismatic_pending.target_state_id}"},
                    {"_prismatic_mover": "_info_scene_entity"},
                ),
                _require(
                    (
                        "_prismatic_anchor.metadata.plane != null and "
                        "_prismatic_anchor.metadata.plane != '' and "
                        "_prismatic_mover.metadata.plane != null and "
                        "_prismatic_anchor.metadata.plane != _prismatic_mover.metadata.plane"
                    ),
                    "Violet requires an admitted anchor on an actual different plane.",
                ),
                _call(
                    "resolve_srd_travel_placement",
                    {
                        "subject": "{_prismatic_pending.target_state_id}",
                        "destination": "{_prismatic_anchor.position}",
                        "plane": "{_prismatic_anchor.metadata.plane}",
                        "excluded": [],
                    },
                    ("_travel_placement",),
                ),
                _call(
                    "move_srd_travel_subject",
                    {
                        "subject": "{_prismatic_pending.target_state_id}",
                        "destination": "{_travel_placement.position}",
                        "plane": "{_prismatic_anchor.metadata.plane}",
                    },
                ),
                _set("state.prismatic_violet_pending.{event_id}.pending", False),
                _set(
                    "state.prismatic_violet_pending.{event_id}.destination",
                    "_travel_placement.position",
                ),
                _set(
                    "state.prismatic_violet_pending.{event_id}.plane",
                    "_prismatic_anchor.metadata.plane",
                ),
                _call(
                    "record_srd_result_text",
                    {
                        "text": (
                            "Violet teleports {_prismatic_pending.target_state_id} to "
                            "{_prismatic_anchor.metadata.plane}."
                        )
                    },
                    {"_prismatic_travel_result": "_recorded_result_text"},
                ),
            ],
        ),
    }


def _turn_hooks(tools, changed):
    """Attach scheduled color saves to actual turn boundaries, preserving their result rows."""
    end = deepcopy(tools["resolve_srd_timed_condition_end_turn"])
    if "resolve_srd_prismatic_indigo_end_turn" not in str(end["effects"]):
        end["effects"].append(
            _when(
                "target_state.has_prismatic_sources == true",
                [
                    _call(
                        "resolve_srd_prismatic_indigo_end_turn",
                        {"target_state": "{_target_state_id}"},
                        ("_prismatic_end_rows", "_prismatic_end_rolls"),
                    ),
                    _each(
                        "_prismatic_end_rows",
                        "_prismatic_row",
                        [_push("_timed_condition_save_details", "_prismatic_row")],
                    ),
                    _each(
                        "_prismatic_end_rolls",
                        "_prismatic_roll",
                        [_push("_timed_condition_end_turn_rolls", "_prismatic_roll")],
                    ),
                    _when(
                        "len(_prismatic_end_rows) > 0",
                        [_set("_timed_condition_card_title", "'End-turn saving throws'")],
                    ),
                ],
            )
        )
        changed["resolve_srd_timed_condition_end_turn"] = end
    begin = deepcopy(tools["begin_combat_turn"])
    if "resolve_srd_prismatic_violet_start_turn" not in str(begin["effects"]):
        index = next(
            i for i, e in enumerate(begin["effects"]) if e.get("call") == "process_srd_turn_effects"
        )
        begin["effects"].insert(
            index + 1,
            _when(
                "actor.has_prismatic_violet_targets == true",
                [
                    _call(
                        "resolve_srd_prismatic_violet_start_turn",
                        {"caster_state": "{_actor_id}"},
                        ("_prismatic_violet_rows",),
                    ),
                    _each(
                        "_prismatic_violet_rows",
                        "_prismatic_row",
                        [_push("_turn_effect_rows", "_prismatic_row")],
                    ),
                ],
            ),
        )
        changed["begin_combat_turn"] = begin


def _condition_projection(tools, changed):
    """Preserve native conditions while adding source-owned Restrained and Petrified."""
    installer = deepcopy(tools["install_srd_timed_condition_source"])
    for p in installer["parameters"]:
        if p["name"] == "condition":
            p["enum_values"] = list(dict.fromkeys([*p["enum_values"], "restrained", "petrified"]))
    refresh = deepcopy(tools["refresh_srd_timed_condition_target"])
    for condition in ("restrained", "petrified"):
        if f"_timed_{condition}" in str(refresh["effects"]):
            continue
        installer["effects"].insert(
            0,
            _when(
                f"target_state.timed_condition_unowned_{condition} == null",
                [
                    _set(
                        f"target_state.timed_condition_unowned_{condition}",
                        f"'{condition}' in target_state.conditions",
                    ),
                ],
            ),
        )
        refresh["effects"][0:0] = [
            _when(
                f"target_state.timed_condition_unowned_{condition} == null",
                [
                    _set(
                        f"target_state.timed_condition_unowned_{condition}",
                        f"'{condition}' in target_state.conditions",
                    ),
                ],
            ),
            _set(f"_timed_{condition}", False),
        ]
        for effects in _effect_lists(refresh["effects"]):
            for effect in effects:
                if effect.get("condition", "").startswith(
                    "_timed_condition_source != null and _timed_condition_source.installed "
                    "== true and _timed_condition_source.target_state_id"
                ):
                    effect["on_true"].append(
                        _when(
                            f"_timed_condition_source.condition == '{condition}' "
                            "and _timed_condition_source.suppressed != true",
                            [
                                _call(
                                    "check_srd_target_condition_immunity",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "condition": condition,
                                        "source_state_id": (
                                            "{_timed_condition_source.caster_state_id}"
                                        ),
                                        "source_is_magical": True,
                                    },
                                    ("_source_condition_immune",),
                                ),
                                _when(
                                    "_source_condition_immune != true",
                                    [
                                        _set(f"_timed_{condition}", True),
                                    ],
                                ),
                            ],
                        )
                    )
        refresh["effects"] += [
            {"remove_condition": {"target": "target_state", "condition": condition}},
            _when(
                f"target_state.timed_condition_unowned_{condition} == true "
                f"or _timed_{condition} == true",
                [
                    {"apply_condition": {"target": "target_state", "condition": condition}},
                ],
            ),
            _set(f"target_state.timed_condition_{condition}", f"_timed_{condition}"),
        ]
    for effects in _effect_lists(refresh["effects"]):
        for effect in effects:
            clause = effect.get("condition", "")
            condition = next(
                (
                    c
                    for c in ("blinded", "restrained", "petrified")
                    if clause.startswith(f"_timed_condition_source.condition == '{c}'")
                ),
                None,
            )
            if condition is not None and "prismatic_spray" not in str(effect):
                prior = effect["on_true"]
                effect["on_true"] = [
                    _when(
                        "_timed_condition_source.spell_id == 'prismatic_spray'",
                        [
                            _call(
                                "check_srd_target_condition_immunity",
                                {
                                    "target_state": "{_target_state_id}",
                                    "condition": condition,
                                    "source_state_id": "{_timed_condition_source.caster_state_id}",
                                    "source_is_magical": True,
                                },
                                ("_source_condition_immune",),
                            ),
                            _when(
                                (
                                    "_source_condition_immune != true and "
                                    "_timed_condition_source.suppressed != true"
                                ),
                                prior,
                            ),
                        ],
                        prior,
                    )
                ]
    changed["install_srd_timed_condition_source"] = installer
    changed["refresh_srd_timed_condition_target"] = refresh
    retire = deepcopy(tools["remove_srd_timed_condition_effect"])
    for p in retire["parameters"]:
        if p["name"] == "condition":
            p["enum_values"] = list(dict.fromkeys([*p["enum_values"], "restrained", "petrified"]))
    if "prismatic_violet_blindness_ended" not in str(retire["effects"]):
        for effects in list(_effect_lists(retire["effects"])):
            for index, effect in enumerate(effects):
                if (
                    effect.get("call") == "retire_srd_timed_condition_source"
                    and effect.get("params", {}).get("source_id")
                    == "{_timed_condition_source_to_remove}"
                ):
                    effects[index] = _when(
                        "condition == 'blinded' and _timed_condition_removal_source.spell_id "
                        "== 'prismatic_spray' and "
                        "_timed_condition_removal_source.prismatic_ray == 7",
                        [
                            _set(
                                "lasting_state.timed_condition_sources.{_timed_condition_source_to_remove}.condition",
                                "'none'",
                            ),
                            _set(
                                "lasting_state.timed_condition_sources.{_timed_condition_source_to_remove}.prismatic_violet_blindness_ended",
                                True,
                            ),
                        ],
                        [effect],
                    )
    changed["remove_srd_timed_condition_effect"] = retire
    remove = deepcopy(tools["remove_srd_condition"])
    if "condition == 'restrained' or condition == 'petrified'" not in str(remove["effects"]):
        remove["effects"].append(
            _when(
                "condition == 'restrained' or condition == 'petrified'",
                [
                    _set("target.timed_condition_unowned_{condition}", False),
                    _call(
                        "remove_srd_timed_condition_effect",
                        {
                            "target_state": "{target}",
                            "lasting_state": "state",
                            "condition": "{condition}",
                        },
                    ),
                ],
            )
        )
    changed["remove_srd_condition"] = remove


def _prismatic_public_consumer(tools, changed):
    """Route the real public cast directly to scene selection without caller target lists."""
    allowed = {p["name"] for p in changed["cast_srd_prismatic_spray"]["parameters"]}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(changed.get(name, tools[name]))
        for parameter in _prismatic_geometry_parameters():
            if not any(p["name"] == parameter["name"] for p in tool["parameters"]):
                tool["parameters"].append(parameter)
        if "cast_srd_prismatic_spray" not in str(tool["effects"]):
            for effects in list(_effect_lists(tool["effects"])):
                for index, effect in enumerate(effects):
                    if effect.get("call") == "cast_spell_instantaneous_area_save_damage":
                        call = deepcopy(effect)
                        call["call"] = "cast_srd_prismatic_spray"
                        call["params"] = {n: v for n, v in call["params"].items() if n in allowed}
                        call["params"].update(
                            {
                                p["name"]: "{" + p["name"] + "}"
                                for p in _prismatic_geometry_parameters()
                            }
                        )
                        effects[index] = _when("spell == 'prismatic_spray'", [call], [effect])
        changed[name] = tool


def extend_runtime(text):
    """Emit after family owners and before final damage composition and reaction clones."""
    tools = _load_runtime(text)["tools"]
    changed = _prismatic_roll_tools() | _prismatic_source_tools() | _prismatic_turn_tools()
    _sorcerous_burst(tools, changed)
    _condition_projection(tools, changed)
    _turn_hooks(tools, changed)
    changed["resolve_srd_prismatic_target"] = _prismatic_target_tool(tools)
    changed["record_srd_prismatic_target_result"] = _prismatic_result_tool(tools)
    changed["cast_srd_prismatic_spray"] = _prismatic_area_tool(tools)
    _prismatic_public_consumer(tools, changed)
    compose_object_burst(tools | changed, changed)
    area = deepcopy(tools["cast_spell_instantaneous_area_save_damage"])
    if "cast_srd_prismatic_spray" not in str(area["effects"]):
        params = {p["name"]: "{" + p["name"] + "}" for p in area["parameters"]}
        allowed = {p["name"] for p in changed["cast_srd_prismatic_spray"]["parameters"]}
        params = {n: v for n, v in params.items() if n in allowed}
        params["caster_state"] = "{_caster_state_id}"
        area["effects"] = [
            _when(
                "spell == 'prismatic_spray'",
                [
                    _call(
                        "cast_srd_prismatic_spray",
                        params,
                        (
                            "_spell_summary_rows",
                            "_spell_detail_rows",
                            "_spell_casting_ability",
                            "_spell_casting_modifier",
                            "_spell_proficiency_bonus",
                            "_spell_save_dc",
                            "_spell_damage_dice",
                            "_spell_damage",
                            "_spell_damage_total",
                            "_spell_area_target_count",
                            "_spell_area_last_save",
                            "_spell_area_last_save_outcome",
                            "_spell_area_last_save_advantage",
                            "_spell_area_last_save_disadvantage",
                            "_spell_area_last_damage_amount",
                        ),
                    ),
                ],
                area["effects"],
            )
        ]
    allowed = {p["name"] for p in changed["cast_srd_prismatic_spray"]["parameters"]}
    for effects in _effect_lists(area["effects"]):
        for effect in effects:
            if effect.get("call") == "cast_srd_prismatic_spray":
                effect["params"] = {n: v for n, v in effect["params"].items() if n in allowed}
    changed["cast_spell_instantaneous_area_save_damage"] = area
    # The scene owner keeps its canonical spatial path rather than an entity binding.
    object_attack = changed.pop("resolve_srd_sorcerous_burst_object_attack")
    return _replace_tools(
        _emit(text, changed), {"resolve_srd_sorcerous_burst_object_attack": object_attack}
    )
