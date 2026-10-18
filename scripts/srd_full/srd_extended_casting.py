"""Package-owned Rituals and uninterrupted casting measured in Magic actions or elapsed rounds."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _RuntimeDumper,
        _set,
        _tool,
        _when,
    )
else:
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _RuntimeDumper,
        _set,
        _tool,
        _when,
    )


CASTING_ROUNDS = {
    "1_minute": 10,
    "10_minutes": 100,
    "1_hour": 600,
    "8_hours": 4800,
    "12_hours": 7200,
    "24_hours": 14400,
}
PUBLIC_TOOLS = {"begin_srd_spell_cast", "continue_srd_spell_cast"}
INCAPACITATED = (
    "caster_state.is_alive == true and not ('incapacitated' in caster_state.conditions or "
    "'paralyzed' in caster_state.conditions or 'petrified' in caster_state.conditions or "
    "'stunned' in caster_state.conditions or 'unconscious' in caster_state.conditions)"
)


def _public(tool):
    tool["metadata"] = {"category": "spells", "costs_action": True}
    return tool


def _declare_continuation_progress(tool):
    """Attach recorded progress at the existing lookup and progress mutation."""
    effects = tool["effects"]
    lookup = next(
        i for i, effect in enumerate(effects) if effect.get("store_as") == "_casting_requirements"
    )
    label = _set("_continued_spell_label", "_casting_requirements.name")
    if effects[lookup + 1] != label:
        effects.insert(lookup + 1, label)
    increment = next(
        i
        for i, effect in enumerate(effects)
        if effect.get("set")
        == {
            "field": "caster_state.extended_casting.progress_rounds",
            "value": "caster_state.extended_casting.progress_rounds + elapsed_rounds",
        }
    )
    captures = [
        _set("_continued_cast_stage", "'progressing'"),
        _set("_continued_cast_rounds", "caster_state.extended_casting.progress_rounds"),
        _set("_continued_cast_required_rounds", "caster_state.extended_casting.required_rounds"),
        _when(
            "caster_state.extended_casting.progress_rounds >= "
            "caster_state.extended_casting.required_rounds",
            [_set("_continued_cast_stage", "'ready_to_finish'")],
        ),
    ]
    if effects[increment + 1 : increment + 1 + len(captures)] != captures:
        effects[increment + 1 : increment + 1] = captures


def _declare_continuation_outer(tool):
    """Declare the outer receipt and export only its four recorded scalar facts."""

    def delegates(value):
        if isinstance(value, dict):
            if value.get("call") == "continue_srd_spell_cast_internal":
                yield value
            for child in value.values():
                yield from delegates(child)
        elif isinstance(value, list):
            for child in value:
                yield from delegates(child)

    calls = list(delegates(tool["effects"]))
    assert len(calls) == 1, "Expected the existing private continuation delegate"
    calls[0]["export_context"] = {
        field: field
        for field in (
            "_continued_spell_label",
            "_continued_cast_stage",
            "_continued_cast_rounds",
            "_continued_cast_required_rounds",
        )
    }
    tool["metadata"]["result_card"] = {
        "version": 1,
        "title": {"path": "context._continued_spell_label"},
        "footprint": "compact",
        "summary": [
            {
                "label": "Casting",
                "value": {
                    "path": "context._continued_cast_stage",
                    "labels": {
                        "progressing": "Progressing",
                        "ready_to_finish": "Ready to finish",
                        "interrupted": "Interrupted",
                    },
                },
                "emphasis": True,
            },
            {
                "label": "Rounds completed",
                "value": {"path": "context._continued_cast_rounds"},
            },
            {
                "label": "Rounds required",
                "value": {"path": "context._continued_cast_required_rounds"},
            },
        ],
        "details": [],
    }


def _bind_private_casting_clock(tool):
    """Retain settlement order while fixing the two public wrappers to package state."""
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]

    def bind(value):
        if isinstance(value, str):
            if value == "{_lasting_state_id}":
                return "state"
            return re.sub(r"\blasting_state\.", "state.", value)
        if isinstance(value, list):
            return [bind(child) for child in value]
        if isinstance(value, dict):
            return {key: bind(child) for key, child in value.items()}
        return value

    tool["effects"] = bind(tool["effects"])
    if "pre_conditions" in tool:
        tool["pre_conditions"] = bind(tool["pre_conditions"])


def bind_private_casting_clocks(text):
    """Final composition hook: later settlement owners must not expose a selectable clock."""
    tools = _load_runtime(text)["tools"]
    for name in sorted(PUBLIC_TOOLS):
        tool = deepcopy(tools[name])
        _bind_private_casting_clock(tool)
        text = _render_tool(text, name, tool)
    return text


def _casting_rounds():
    effects = [_set("_required_casting_rounds", 0)]
    for timing, rounds in CASTING_ROUNDS.items():
        effects.append(
            _when(
                f"_spell_definition.casting_time == '{timing}'",
                [_set("_required_casting_rounds", rounds)],
            )
        )
    effects.append(
        _when(
            "casting_mode == 'ritual'",
            [_set("_required_casting_rounds", "_required_casting_rounds + 100")],
        )
    )
    return effects


def _render_tool(text, name, tool):
    """Keep one public cast alias and the generated selector while replacing a tool fragment."""
    rendered = yaml.dump({name: tool}, Dumper=_RuntimeDumper, sort_keys=False, width=105)
    rendered = "".join("  " + line if line.strip() else line for line in rendered.splitlines(True))
    if name == "cast_spell":
        start = rendered.index("    - name: spell\n")
        end = rendered.index("    - name:", start + 1)
        parameter = rendered[start:end]
        enum_start = parameter.index("      enum_values:\n") + len("      enum_values:\n")
        enum_end = parameter.index("      required:", enum_start)
        values = next(p for p in tool["parameters"] if p["name"] == "spell")["enum_values"]
        parameter = (
            parameter[:enum_start]
            + "        # BEGIN GENERATED SPELL SELECTOR\n"
            + "".join(f"          - {value}\n" for value in values)
            + "        # END GENERATED SPELL SELECTOR\n"
            + parameter[enum_end:]
        )
        rendered = rendered[:start] + parameter + rendered[end:]
        rendered = rendered.replace("  cast_spell:\n", "  cast_spell: &srd_cast_spell\n", 1)
    pattern = (
        rf"(?ms)^  {re.escape(name)}:[^\n]*\n.*?"
        r"(?=^  [A-Za-z_][^\n]*:\s*|^procedures:|^definition_row_schemas:)"
    )
    if re.search(pattern, text):
        return re.sub(pattern, lambda _: rendered, text, count=1)
    return text.replace("procedures: {}\n", rendered + "procedures: {}\n", 1)


def _effect_lists(effects):
    """Find the validated casting budget inside its pending-action continuation."""
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _effect_lists(effect[key])


def _capture_started_gesture(cast):
    """Retain the checked attempt before concentration shutdown clears its channel."""
    if not any(e.get("set", {}).get("field") == "_extended_slow_cast" for e in cast["effects"]):
        cast["effects"].insert(0, _set("_extended_slow_cast", None))
    for effects in _effect_lists(cast["effects"]):
        if not any(
            e.get("require", {}).get("error")
            == "Start and continue this longer casting before resolving its spell."
            for e in effects
        ):
            continue
        if not any(e.get("set", {}).get("field") == "_extended_slow_cast" for e in effects):
            index = next(
                i for i, e in enumerate(effects) if e.get("call") == "end_srd_concentration_node"
            )
            effects.insert(
                index, _set("_extended_slow_cast", "caster_state.extended_casting.slow_cast")
            )


def _started_gesture(begin, cast, material):
    """Check Slow at the actual start and settle a failed attempt through existing costs."""
    effects = begin["effects"]
    insertion = next(
        i
        for i, e in enumerate(effects)
        if e.get("condition") == "caster_state.concentration_spell_id != null"
    )
    effects[insertion:insertion] = [
        _set(
            "_started_somatic",
            "'somatic' in _casting_requirements.components and "
            "'somatic' not in _component_exemptions",
        ),
        _call(
            "resolve_srd_slow_cast_failure",
            {
                "caster_state": "{_caster_state_id}",
                "spell": "{spell}",
                "has_somatic_component": "{_started_somatic}",
            },
            ["_slow_cast_failed", "_slow_cast_failure_roll"],
        ),
        _set("_started_actual_components", []),
        _set("_started_components", "_casting_requirements.components"),
        {
            "for_each": {
                "collection": "_started_components",
                "as": "_started_component",
                "do": [
                    _when(
                        "_started_component not in _component_exemptions",
                        [
                            {
                                "list_push": {
                                    "list": "_started_actual_components",
                                    "value": "_started_component",
                                }
                            },
                        ],
                    ),
                ],
            }
        },
        _set("_started_limited_use", {"kind": "'none'", "pool": None, "maximum": 0}),
        _when(
            "caster == null and _npc_spell.uses_per_day > 0",
            [
                _set("_started_limited_use", {"kind": "'npc'", "pool": None, "maximum": 0}),
            ],
        ),
        _when(
            "_feature_cast == true",
            [
                _set(
                    "_started_limited_use",
                    {
                        "kind": "'feature'",
                        "pool": "_grant_free_pool",
                        "maximum": "_grant_free_limit",
                    },
                ),
            ],
        ),
    ]
    channel = next(
        i
        for i, e in enumerate(effects)
        if e.get("set", {}).get("field") == "caster_state.extended_casting"
    )
    effects[channel]["set"]["value"]["slow_cast"] = {
        "failed": "_slow_cast_failed",
        "roll": "_slow_cast_failure_roll",
        "spell": "spell",
        "caster_state_id": "_caster_state_id",
    }
    effects[channel]["set"]["value"]["components"] = "_started_actual_components"
    effects[channel]["set"]["value"]["limited_use"] = "_started_limited_use"
    started = effects[channel:]
    pay_materials = deepcopy(material)
    pay_materials["params"]["validate_only"] = False
    costs = [
        deepcopy(e)
        for e in cast["effects"]
        if (
            "caster_state.resources.{_cast_resource}" in str(e.get("on_true", []))
            and any("decrement" in child for child in e.get("on_true", []))
            or e.get("condition") == "_feature_cast == true"
            and any(
                child.get("set", {}).get("field")
                == "caster_state.spell_grant_uses.{_grant_free_pool}"
                for child in e.get("on_true", [])
            )
            or e.get("condition") == "caster == null and _npc_spell.uses_per_day > 0"
        )
    ]
    assert len(costs) == 3, "Failed longer casting must use the three existing spell-cost owners."
    effects[channel:] = [
        _when("_slow_cast_failed != true", started),
        _when(
            "_slow_cast_failed == true",
            [
                _require(
                    "_cast_resource == 'cantrip' or _cast_resource == 'no_slot' or "
                    "combat.encounter_active != true or "
                    "caster_state.spell_slot_cast_this_turn != true",
                    "Only one spell slot may be spent on a turn.",
                ),
                _when(
                    "combat.encounter_active == true and _cast_resource != 'cantrip' and "
                    "_cast_resource != 'no_slot'",
                    [_set("caster_state.spell_slot_cast_this_turn", True)],
                ),
                pay_materials,
                *costs,
                _set("caster_state.spell_preparation_open", False),
                _call(
                    "interrupt_srd_rest", {"target_state": "{_caster_state_id}", "reason": "spell"}
                ),
            ],
        ),
        _set(
            "_spell_card_summary",
            [
                {"label": "'Caster'", "value": "caster_state.name"},
                {"label": "'Casting rounds required'", "value": "_required_casting_rounds"},
                {"label": "'Casting'", "value": "'Longer casting started'"},
            ],
        ),
        _when(
            "_slow_cast_failed == true",
            [
                _set(
                    "_spell_card_summary",
                    [
                        {"label": "'Caster'", "value": "caster_state.name"},
                        {
                            "label": "'Casting'",
                            "value": "'Slow: Somatic gestures failed; casting costs spent.'",
                        },
                        {
                            "label": "'Slow gesture roll'",
                            "value": "_slow_cast_failure_roll.results",
                        },
                    ],
                ),
                _set(
                    "_spell_detail_rows",
                    [
                        {
                            "label": "'Casting'",
                            "value": "'No spell effect or casting channel was created.'",
                        },
                    ],
                ),
            ],
        ),
    ]


def extend_runtime(text, *, spell_ids=None):
    """Build casting channels from the same validated public cast prefix and existing lifecycle."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    changed = {}
    cast = deepcopy(tools["cast_spell"])
    if spell_ids is not None:
        next(p for p in cast["parameters"] if p["name"] == "spell")["enum_values"] = spell_ids
    if not any(p["name"] == "casting_mode" for p in cast["parameters"]):
        cast["parameters"] += [
            {
                "name": "casting_mode",
                "type": "enum",
                "enum_values": ["ordinary", "ritual"],
                "required": False,
                "default": "ordinary",
            },
            {"name": "spellbook_readable", "type": "boolean", "required": False, "default": False},
        ]
        resource = next(p for p in cast["parameters"] if p["name"] == "slot_resource")
        resource["enum_values"].append("ritual")
        prepared = next(
            p
            for p in cast["pre_conditions"]
            if p["condition"] == "spell in caster_state.prepared_spells"
        )
        prepared["condition"] += (
            " or (casting_mode == 'ritual' and caster != null and "
            "caster.data.wizard_spellbook != null and spell in caster.data.wizard_spellbook)"
        )
        cast["pre_conditions"] += [
            {
                "condition": "caster == null or (casting_mode == 'ritual') == "
                "(slot_resource == 'ritual')",
                "error": "A character Ritual uses the Ritual resource and no spell slot.",
            },
        ]
        pc = next(e for e in cast["effects"] if e.get("condition") == "caster != null")
        validate = next(e for e in pc["on_true"] if e.get("call") == "validate_srd_spell_grants")
        validate["params"]["ritual"] = "{_ritual_cast}"
        validate["export_context"]["_ritual_book_required"] = "_ritual_book_required"
        pc["on_true"].insert(
            pc["on_true"].index(validate) + 1,
            _require(
                "_ritual_book_required != true or spellbook_readable == true",
                "Ritual Adept requires reading the selected Wizard spell from its spellbook.",
            ),
        )
        feature = next(
            e for e in pc["on_true"] if e.get("condition") == "_cast_resource == 'feature'"
        )
        feature["on_false"][0]["require"]["condition"] += " or _ritual_cast == true"
        feature_index = pc["on_true"].index(feature)
        pc["on_true"].insert(
            feature_index,
            _when(
                "_ritual_cast == true",
                [_set("_cast_resource", "'no_slot'")],
            ),
        )
        requirements_index = next(
            i for i, e in enumerate(cast["effects"]) if e.get("store_as") == "_casting_requirements"
        )
        cast["effects"][requirements_index + 1 : requirements_index + 1] = [
            _set("_ritual_cast", "casting_mode == 'ritual'"),
            _require(
                "_ritual_cast != true or _casting_requirements.ritual == true",
                "The source spell has no Ritual casting alternative.",
            ),
        ]
        budget_index = next(
            i
            for i, e in enumerate(cast["effects"])
            if e.get("call") == "spend_spell_casting_budget"
        )
        budget = cast["effects"][budget_index]
        completion = [
            _require(
                "caster_state.concentration_effect_family == 'extended_casting' "
                "and caster_state.extended_casting.spell == spell "
                "and caster_state.extended_casting.mode == casting_mode "
                "and (caster == null or "
                "caster_state.extended_casting.slot_resource == slot_resource) "
                "and caster_state.extended_casting.grant == spell_grant "
                "and caster_state.extended_casting.progress_rounds >= "
                "caster_state.extended_casting.required_rounds",
                "Start and continue this longer casting before resolving its spell.",
            ),
            _when(
                "spell == 'contingency'",
                [
                    _require(
                        "caster_state.extended_casting.contingency_choices.contingent_spell "
                        "== contingent_spell",
                        "Keep the original Contingency choices.",
                    ),
                    _require(
                        "caster_state.extended_casting.contingency_choices."
                        "contingent_slot_resource "
                        "== contingent_slot_resource",
                        "Keep the original Contingency choices.",
                    ),
                    _require(
                        "caster_state.extended_casting.contingency_choices.contingent_spell_grant "
                        "== contingent_spell_grant",
                        "Keep the original Contingency choices.",
                    ),
                    _require(
                        "caster_state.extended_casting.contingency_choices."
                        "contingent_material_item_ids "
                        "== contingent_material_item_ids",
                        "Keep the original Contingency choices.",
                    ),
                    _require(
                        "caster_state.extended_casting.contingency_choices.contingent_hp_threshold "
                        "== contingent_hp_threshold",
                        "Keep the original Contingency choices.",
                    ),
                ],
            ),
            _require(INCAPACITATED, "Incapacitation interrupts the longer casting."),
            _require(
                "combat.encounter_active != true or (combat.turn_active == true "
                "and combat.active_actor == _caster_state_id and "
                "caster_state.extended_casting.last_turn == caster_state.turns_started)",
                "Finish on the same turn as the final casting Magic action.",
            ),
            _require(
                "_cast_resource == 'cantrip' or _cast_resource == 'no_slot' or "
                "combat.encounter_active != true or "
                "caster_state.spell_slot_cast_this_turn != true",
                "Only one spell slot may be spent on a turn.",
            ),
            _when(
                "combat.encounter_active == true and _cast_resource != 'cantrip' "
                "and _cast_resource != 'no_slot'",
                [_set("caster_state.spell_slot_cast_this_turn", True)],
            ),
            _call(
                "end_srd_concentration_node",
                {
                    "source_state": "{_caster_state_id}",
                    "target_state": "{_caster_state_id}",
                },
            ),
        ]
        cast["effects"][budget_index] = {
            "condition": "casting_mode == 'ritual' or "
            "(_spell_definition.casting_time != 'action' and "
            "_spell_definition.casting_time != 'bonus_action' and "
            "_spell_definition.casting_time != 'reaction')",
            "on_true": completion,
            "on_false": [
                _when(
                    "caster_state.concentration_effect_family == 'extended_casting'",
                    [
                        _call(
                            "end_srd_concentration_node",
                            {
                                "source_state": "{_caster_state_id}",
                                "target_state": "{_caster_state_id}",
                            },
                        ),
                    ],
                ),
                budget,
            ],
        }
        level_index = next(
            i
            for i, e in enumerate(cast["effects"])
            if e.get("condition") == "_feature_cast == true"
        )
        cast["effects"].insert(
            level_index + 1,
            _when(
                "_ritual_cast == true",
                [_set("_slot_level", "_spell_definition.base_level")],
            ),
        )
        cast["effects"].append(
            _when(
                "_ritual_cast == true",
                [
                    {
                        "list_push": {
                            "list": "_spell_card_summary",
                            "value": {
                                "label": "'Casting'",
                                "value": "'Ritual: no spell slot spent'",
                            },
                        }
                    },
                ],
            )
        )
    changed["cast_spell"] = cast
    _capture_started_gesture(cast)
    prepared = next(
        p
        for p in cast["pre_conditions"]
        if p["condition"].startswith("spell in caster_state.prepared_spells")
    )
    if "caster.data.wizard_spellbook != null" not in prepared["condition"]:
        prepared["condition"] = prepared["condition"].replace(
            "spell in caster.data.wizard_spellbook",
            "caster.data.wizard_spellbook != null and spell in caster.data.wizard_spellbook",
        )

    grants = deepcopy(tools["validate_srd_spell_grants"])
    if not any(p["name"] == "ritual" for p in grants["parameters"]):
        grants["parameters"].append(
            {"name": "ritual", "type": "boolean", "required": False, "default": False},
        )
        grants["effects"].insert(2, _set("_ritual_book_required", False))
        loop = next(
            e["for_each"]
            for e in grants["effects"]
            if e.get("for_each", {}).get("collection") == "_grant_records"
        )
        match = next(
            e for e in loop["do"] if e.get("condition", "").startswith("spell in _grant_spells")
        )
        match_index = loop["do"].index(match)
        loop["do"][match_index:match_index] = [
            _set("_ritual_book_match", False),
            _when(
                "ritual == true and _grant.source == 'wizard' and "
                "spell in _grant_profile.wizard_spellbook and "
                "(selected_source == '' or selected_source == 'wizard')",
                [
                    {
                        "table_lookup": {
                            "table": "definitions.spell_casting",
                            "key": "spell",
                            "required": True,
                        },
                        "store_as": "_ritual_header",
                    },
                    _require(
                        "_ritual_header.ritual == true and 'wizard' in "
                        "_ritual_header.classes and _ritual_header.level > 0 and "
                        "_ritual_header.level <= _grant_policy.max_level[_grant_level - 1]",
                        "Ritual Adept requires an eligible Wizard Ritual in the spellbook.",
                    ),
                    _set("_ritual_book_match", True),
                    _set("_ritual_book_required", True),
                ],
            ),
        ]
        match["condition"] = "(" + match["condition"] + ") or _ritual_book_match == true"
    changed["validate_srd_spell_grants"] = grants

    materials = deepcopy(tools["resolve_srd_spell_materials"])
    if not any(p["name"] == "validate_only" for p in materials["parameters"]):
        materials["parameters"].append(
            {"name": "validate_only", "type": "boolean", "required": False, "default": False},
        )
        outer = next(e for e in materials["effects"] if "on_true" in e)
        parts = next(
            e["for_each"]
            for e in outer["on_true"]
            if e.get("for_each", {}).get("collection") == "_material_parts"
        )
        spend = next(e for e in parts["do"] if e.get("condition") == "_part.consumed")
        spend["condition"] += " and validate_only != true"
    changed["resolve_srd_spell_materials"] = materials

    # Reuse the checked identity, spell grant, component and slot prefix without casting its family.
    material_index = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "resolve_srd_spell_materials"
    )
    prefix = deepcopy(cast["effects"][:material_index])
    prefix = [e for e in prefix if e.get("condition") != "targets != null"]
    prefix.insert(0, _set("_ritual_book_required", False))
    next(e for e in prefix if e.get("set", {}).get("field") == "_material_target_count")["set"][
        "value"
    ] = "material_target_count"
    validate_material = deepcopy(cast["effects"][material_index])
    validate_material["params"]["validate_only"] = True
    core = {
        "spell",
        "caster",
        "caster_state",
        "slot_resource",
        "spell_grant",
        "material_component_available",
        "material_item_ids",
        "verbal_component_available",
        "somatic_component_available",
        "casting_mode",
        "spellbook_readable",
        "contingent_spell",
        "contingent_slot_resource",
        "contingent_spell_grant",
        "contingent_material_item_ids",
        "contingent_hp_threshold",
    }
    parameters = [deepcopy(p) for p in cast["parameters"] if p["name"] in core]
    parameters += [
        {
            "name": "material_target_count",
            "type": "integer",
            "minimum": 1,
            "maximum": 128,
            "required": False,
            "default": 1,
        }
    ]
    begin = _tool(
        "Begin a source-defined longer casting or Ritual. Continue its Magic action each combat "
        "turn, or account for uninterrupted exploration rounds; then finish using cast_spell "
        "with current target facts. A failed Slow gesture spends the casting costs immediately.",
        parameters,
        prefix
        + _casting_rounds()
        + [
            _require("_required_casting_rounds >= 10", "This spell uses its ordinary cast action."),
            _require(INCAPACITATED, "An Incapacitated creature cannot begin casting."),
            _require(
                "caster_state.befuddled != true",
                "Befuddlement prevents this creature from beginning a spell.",
            ),
            _require(
                "_casting_profile.concentration_save_modifier != null",
                "Longer casting needs the caster's actual Constitution save modifier.",
            ),
            validate_material,
            _when(
                "combat.encounter_active == true and combat.order != null and "
                "len(combat.order) > 0 and combat.round != null and combat.turn_index != null",
                [
                    _call("sync_srd_group_lasting_clock", {"lasting_state": "state"}),
                ],
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when(
                "caster_state.concentration_spell_id != null",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{caster_state.concentration_target_state_id}",
                        },
                    ),
                ],
            ),
            _set("_initial_casting_progress", 0),
            _set("_initial_casting_turn", 0),
            _set("_saved_casting_resource", "'no_slot'"),
            _when("caster != null", [_set("_saved_casting_resource", "slot_resource")]),
            _when(
                "combat.encounter_active == true",
                [
                    _call(
                        "spend_spell_casting_budget",
                        {
                            "caster_state": "{_caster_state_id}",
                            "casting_time": "action",
                            "slot_resource": "no_slot",
                        },
                    ),
                    _set("_initial_casting_progress", 1),
                    _set("_initial_casting_turn", "caster_state.turns_started"),
                ],
            ),
            _set(
                "caster_state.extended_casting",
                {
                    "spell": "spell",
                    "mode": "casting_mode",
                    "slot_resource": "_saved_casting_resource",
                    "grant": "spell_grant",
                    "required_rounds": "_required_casting_rounds",
                    "book_required": "_ritual_book_required",
                    "materials": "material_item_ids",
                    "material_target_count": "material_target_count",
                    "material_waived": "_material_waived",
                    "material_focus_kinds": "_material_focus_kinds",
                    "progress_rounds": "_initial_casting_progress",
                    "last_turn": "_initial_casting_turn",
                },
            ),
            _when(
                "spell == 'contingency'",
                [
                    _set(
                        "caster_state.extended_casting.contingency_choices",
                        {
                            "contingent_spell": "contingent_spell",
                            "contingent_slot_resource": "contingent_slot_resource",
                            "contingent_spell_grant": "contingent_spell_grant",
                            "contingent_material_item_ids": "contingent_material_item_ids",
                            "contingent_hp_threshold": "contingent_hp_threshold",
                        },
                    ),
                ],
            ),
            _set("caster_state.concentration_spell_id", "spell"),
            _set("caster_state.concentration_effect_family", "'extended_casting'"),
            _set("caster_state.concentration_target_state_id", "_caster_state_id"),
            _set("caster_state.concentration_target_state_ids", []),
            _set(
                "caster_state.concentration_save_modifier",
                "_casting_profile.concentration_save_modifier",
            ),
            _set("caster_state.concentration_started_tick", "state.lasting_tick"),
            _set(
                "caster_state.concentration_expires_tick",
                "state.lasting_tick + _required_casting_rounds * 2 + 1",
            ),
            _when(
                "state.extended_casting_sources == null",
                [
                    _set("state.extended_casting_sources", []),
                ],
            ),
            _set("_channel_sources", "state.extended_casting_sources"),
            _when(
                "_caster_state_id not in _channel_sources",
                [
                    _require(
                        "len(_channel_sources) < 128",
                        "At most 128 creatures may have unfinished longer castings.",
                    ),
                    {"list_push": {"list": "_channel_sources", "value": "_caster_state_id"}},
                ],
            ),
            _set("state.extended_casting_sources", "_channel_sources"),
            _call("interrupt_srd_rest", {"target_state": "{_caster_state_id}", "reason": "spell"}),
        ],
        deepcopy(cast["pre_conditions"]),
    )
    _started_gesture(begin, cast, validate_material)
    changed["begin_srd_spell_cast"] = _public(begin)

    advance = [
        deepcopy(e)
        for e in tools["advance_srd_lasting_time"]["effects"]
        if e.get("call") != "interrupt_srd_extended_castings"
    ]
    continue_effects = [
        _require(INCAPACITATED, "Incapacitation interrupts the longer casting."),
        _require(
            "caster_state.befuddled != true",
            "Befuddlement prevents this creature from continuing a spell.",
        ),
        {
            "table_lookup": {
                "table": "definitions.spell_casting",
                "key": "caster_state.extended_casting.spell",
                "required": True,
            },
            "store_as": "_casting_requirements",
        },
        _set("_component_exemptions", []),
        _when(
            "caster_state.spellcasting_profile.component_exemptions != null",
            [
                _set(
                    "_component_exemptions",
                    "caster_state.spellcasting_profile.component_exemptions",
                ),
            ],
        ),
        _require(
            "'verbal' not in _casting_requirements.components or "
            "'verbal' in _component_exemptions or verbal_component_available == true",
            "Continue with the spell's available Verbal component.",
        ),
        _require(
            "'somatic' not in _casting_requirements.components or "
            "'somatic' in _component_exemptions or somatic_component_available == true",
            "Continue with the spell's available Somatic component.",
        ),
        _require(
            "caster_state.extended_casting.book_required != true or spellbook_readable == true",
            "Continue reading the Wizard spellbook for Ritual Adept.",
        ),
        _call(
            "resolve_srd_spell_materials",
            {
                "caster_state": "{_caster_state_id}",
                "spell": "{caster_state.extended_casting.spell}",
                "item_ids": "{caster_state.extended_casting.materials}",
                "target_count": "{caster_state.extended_casting.material_target_count}",
                "components_waived": "{caster_state.extended_casting.material_waived}",
                "permitted_focus_kinds": "{caster_state.extended_casting.material_focus_kinds}",
                "materials_available": "{material_component_available}",
                "validate_only": True,
            },
        ),
        _require(
            "caster_state.extended_casting.progress_rounds < "
            "caster_state.extended_casting.required_rounds",
            "The casting is ready; finish it using cast_spell.",
        ),
        {
            "condition": "combat.encounter_active == true",
            "on_true": [
                _require("elapsed_rounds == 1", "Use one Magic action per casting turn in combat."),
                _require(
                    "caster_state.extended_casting.last_turn < caster_state.turns_started",
                    "An extra action cannot accelerate a longer casting on the same turn.",
                ),
                _call(
                    "spend_spell_casting_budget",
                    {
                        "caster_state": "{_caster_state_id}",
                        "casting_time": "action",
                        "slot_resource": "no_slot",
                    },
                ),
                _set("caster_state.extended_casting.last_turn", "caster_state.turns_started"),
            ],
            "on_false": [
                _require("uninterrupted == true", "Confirm an uninterrupted casting interval."),
                _require(
                    "caster_state.extended_casting.progress_rounds + elapsed_rounds <= "
                    "caster_state.extended_casting.required_rounds",
                    "Advance only the remaining casting rounds before resolving the spell.",
                ),
                _call("interrupt_srd_extended_castings", {"except_caster": "{_caster_state_id}"}),
                *advance,
            ],
        },
        _set(
            "caster_state.extended_casting.progress_rounds",
            "caster_state.extended_casting.progress_rounds + elapsed_rounds",
        ),
    ]
    changed["continue_srd_spell_cast"] = _public(
        _tool(
            "Spend the next casting Magic action, or advance uninterrupted exploration rounds. "
            "Casting fails if concentration breaks or a required combat turn is missed.",
            [
                _entity("caster_state"),
                {
                    "name": "elapsed_rounds",
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 14400,
                    "required": False,
                    "default": 1,
                },
                {"name": "uninterrupted", "type": "boolean", "required": False, "default": False},
            ],
            # Component facts apply throughout the selected uninterrupted interval.
            continue_effects,
            [
                {
                    "condition": "caster_state.concentration_effect_family == 'extended_casting' "
                    "and caster_state.extended_casting != null",
                    "error": "There is no uninterrupted longer casting to continue.",
                }
            ],
        )
    )
    changed["continue_srd_spell_cast"]["parameters"] += [
        {"name": name, "type": "boolean", "required": False, "default": False}
        for name in (
            "verbal_component_available",
            "somatic_component_available",
            "material_component_available",
            "spellbook_readable",
        )
    ]
    _declare_continuation_progress(changed["continue_srd_spell_cast"])

    changed["interrupt_srd_extended_casting"] = _tool(
        "End a longer casting if it belongs to this actor; its slot and materials remain unspent.",
        [_entity("caster_state")],
        [
            _when(
                "caster_state.concentration_effect_family == 'extended_casting'",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{_caster_state_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    changed["interrupt_srd_extended_castings"] = _tool(
        "Interrupt unaccounted exploration casting intervals and prune the source registry.",
        [{"name": "except_caster", "type": "string", "required": False, "default": ""}],
        [
            _set("_casting_sources", []),
            _set("_retained_casting_sources", []),
            _when(
                "state.extended_casting_sources != null",
                [
                    _set("_casting_sources", "state.extended_casting_sources"),
                ],
            ),
            {
                "for_each": {
                    "collection": "_casting_sources",
                    "as": "_channel_source",
                    "do": [
                        {
                            "condition": "_channel_source == except_caster",
                            "on_true": [
                                {
                                    "list_push": {
                                        "list": "_retained_casting_sources",
                                        "value": "_channel_source",
                                    }
                                },
                            ],
                            "on_false": [
                                _call(
                                    "interrupt_srd_extended_casting",
                                    {"caster_state": "{_channel_source}"},
                                ),
                            ],
                        },
                    ],
                }
            },
            _set("state.extended_casting_sources", "_retained_casting_sources"),
        ],
    )
    changed["remove_srd_extended_casting_source"] = _tool(
        "Remove one ended casting channel from the bounded clock registry.",
        [{"name": "source_id", "type": "string", "required": True}],
        [
            _set("_casting_sources", []),
            _set("_retained_casting_sources", []),
            _when(
                "state.extended_casting_sources != null",
                [
                    _set("_casting_sources", "state.extended_casting_sources"),
                ],
            ),
            {
                "for_each": {
                    "collection": "_casting_sources",
                    "as": "_channel_source",
                    "do": [
                        _when(
                            "_channel_source != source_id",
                            [
                                {
                                    "list_push": {
                                        "list": "_retained_casting_sources",
                                        "value": "_channel_source",
                                    }
                                },
                            ],
                        ),
                    ],
                }
            },
            _set("state.extended_casting_sources", "_retained_casting_sources"),
        ],
    )
    ending = deepcopy(tools["end_srd_concentration_node"])
    if "source_state.extended_casting" not in str(ending["effects"]):
        ending["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'extended_casting'",
                [_set("source_state.extended_casting", None)],
            ),
        )
    changed["end_srd_concentration_node"] = ending
    channel_end = next(
        e
        for e in ending["effects"]
        if e.get("condition") == "source_state.concentration_effect_family == 'extended_casting'"
    )
    if "remove_srd_extended_casting_source" not in str(channel_end):
        channel_end["on_true"].append(
            _call(
                "remove_srd_extended_casting_source",
                {"source_id": "{_source_state_id}"},
            )
        )
    end_turn = deepcopy(tools["end_combat_turn"])
    if "interrupt_srd_extended_casting" not in str(end_turn["effects"]):
        end_turn["effects"].insert(
            0,
            _when(
                "actor.concentration_effect_family == 'extended_casting' and "
                "(actor.extended_casting.last_turn != actor.turns_started or "
                "actor.extended_casting.progress_rounds >= actor.extended_casting.required_rounds)",
                [_call("interrupt_srd_extended_casting", {"caster_state": "{_actor_id}"})],
            ),
        )
    changed["end_combat_turn"] = end_turn
    free_time = deepcopy(tools["advance_srd_lasting_time"])
    if "interrupt_srd_extended_castings" not in str(free_time["effects"]):
        free_time["effects"].insert(0, _call("interrupt_srd_extended_castings", {}))
    changed["advance_srd_lasting_time"] = free_time
    for name in PUBLIC_TOOLS:
        implementation = changed[name]
        internal_name = name + "_internal"
        changed[internal_name] = deepcopy(implementation)
        changed[internal_name]["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        public_parameters = [
            p for p in implementation["parameters"] if p["name"] != "lasting_state"
        ]
        params = {
            p["name"]: "{" + ("_" + p["name"] + "_id" if p.get("resolve") else p["name"]) + "}"
            for p in public_parameters
        }
        params["lasting_state"] = "state"
        changed[name] = {
            "description": implementation["description"],
            "parameters": public_parameters,
            "effects": [_call(internal_name, params)],
            "metadata": implementation["metadata"],
        }
        if name == "begin_srd_spell_cast":
            changed[name]["effects"][0]["export_context"] = {
                key: key
                for key in (
                    "_casting_requirements",
                    "_spell_card_summary",
                    "_spell_detail_rows",
                    "_slow_cast_failed",
                    "_slow_cast_failure_roll",
                )
            }
            changed[name]["metadata"]["result_card"] = deepcopy(cast["metadata"]["result_card"])
        elif name == "continue_srd_spell_cast":
            _declare_continuation_outer(changed[name])
    for name, tool in changed.items():
        text = _render_tool(text, name, tool)
    return text
