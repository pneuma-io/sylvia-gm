"""Original Lite Blade Ward over the real source, damage and EndTurn owners.

Original adaptation over the sealed Retreat engine. Native evidence belongs to
the external exact-byte proof and readable handoff, rather than this module.

Bind after the old profile adapters, before Lite boundary/choice restrictions.
Root must also add blade_ward to SELECTED and rebuild ordinary grant/artifact
validation. This module adds no public operation or damage writer.

Selected behavior: level-zero self Action, V/S, no concentration, type-wide
Bludgeoning/Piercing/Slashing resistance. Immunity wins; resistance halves once;
an exact active Ward suppresses only physical vulnerability multiplication.
Reapplication Ignore retains the first active source and deadline.

Combat expiry follows the existing True Strike model: own next EndTurn,
including the casting turn in the two turns. Exploration fallback is +4 ticks.
Combat entry checks the real exploration deadline before qualifying that source.
Exploration entry follows the stated engine convention; no installed legacy parity
is implied by the transition.
"""

from copy import deepcopy
import re


PHYSICAL = ["'bludgeoning'", "'piercing'", "'slashing'"]
FAMILY = "lite_blade_ward"
PROFILE = {
    "family": FAMILY,
    "base_level": 0,
    "casting_time": "action",
    "range_kind": "self",
    "range_feet": 0,
    "duration_rounds": 2,
    "concentration": False,
}
HEADER = {
    "name": "Blade Ward",
    "level": 0,
    "school": "abjuration",
    "classes": ["bard", "cleric", "sorcerer", "warlock", "wizard"],
    "casting_time": "Action",
    "range": "Self",
    "duration": "2 turns, ending at the caster's next EndTurn",
    "components": ["verbal", "somatic"],
    "material": "",
    "material_parts": [],
    "material_consumed": False,
    "material_has_cost": False,
    "focus_substitution": False,
    "ritual": False,
}


def _set(field, value):
    return {"set": {"field": field, "value": value}}


def _when(condition, yes, no=None):
    node = {"condition": condition, "on_true": yes}
    if no is not None:
        node["on_false"] = no
    return node


def _call(name, params, exports=()):
    node = {"call": name, "params": params}
    if exports:
        node["export_context"] = (
            exports if isinstance(exports, dict) else {name: name for name in exports}
        )
    return node


def _require(condition, message):
    return {"require": {"condition": condition, "error": message}}


def _push(collection, value):
    return {"list_push": {"list": collection, "value": value}}


def _each(collection, name, effects):
    return {"for_each": {"collection": collection, "as": name, "do": effects}}


def _entity(name):
    return {"name": name, "type": "string", "resolve": "entity", "required": True}


def _lasting():
    return {
        "name": "lasting_state", "type": "string", "resolve": "entity",
        "required": False, "default": "state", "model_hidden": True,
    }


def _tool(description, params, effects):
    return {
        "description": description, "parameters": params, "pre_conditions": [],
        "effects": effects,
        "metadata": {"category": "internal", "visible_to_player": False,
                     "costs_action": False},
    }


def _nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def _qualify(row, prefix):
    """Short grammar-valid facts, each below the unchanged 500-character cap."""
    return [
        _set(prefix + "_identity", f"{row}.kind == 'lite_blade_ward' and "
             f"{row}.spell == 'blade_ward' and {row}.cast_id != null and {row}.cast_id != ''"),
        _set(prefix + "_owner", f"{row}.caster == {row}.target and "
             f"{row}.source_caster_id == {row}.caster and {row}.owner == {row}.target "
             f"and {row}.phase == 'end'"),
        _set(prefix + "_span", f"{row}.ongoing_duration == true and "
             f"{row}.started_tick != null and {row}.expires_tick != null and {row}.turn != null"),
        _set(prefix + "_valid", prefix + "_identity and " + prefix + "_owner and " + prefix + "_span"),
    ]


def _entry_record(row):
    # Context field writes are flat, not dotted mapping mutations. Reconstruct
    # only this adapter's own record, preserving every authored source field.
    keys = ("kind", "spell", "label", "caster", "target", "source_caster_id", "cast_id",
            "owner", "phase", "turn", "started_tick", "expires_tick", "ongoing_duration", "spell_level")
    return {**{key: row + "." + key for key in keys}, "combat_qualified": True}


def _resolver():
    return _tool(
        "Read an active exact self Ward without mutating native resistance or HP.",
        [_entity("target_state"), _lasting()],
        [
            _set("_bw_active", False), _set("_bw_active_cast_id", "''"),
            _set("_bw_records", []),
            _when("lasting_state.turn_effects != null", [
                _set("_bw_records", "lasting_state.turn_effects"),
            ]),
            _each("_bw_records", "_bw_row", [
                *_qualify("_bw_row", "_bw_read"),
                _set("_bw_clock_live", "_bw_read_valid and lasting_state.lasting_tick != null and "
                     "_bw_row.expires_tick != null and "
                     "lasting_state.lasting_tick + 0.000000001 < _bw_row.expires_tick"),
                _set("_bw_combat_live", "_bw_read_valid and combat.encounter_active == true and "
                     "_bw_row.combat_qualified == true and "
                     "(target_state.turns_started == null or target_state.turns_started <= _bw_row.turn)"),
                _when(
                    "_bw_read_valid and _bw_row.target == _target_state_id and "
                    "(_bw_combat_live or ((combat.encounter_active != true or "
                    "_bw_row.combat_qualified != true) and _bw_clock_live))",
                    [_set("_bw_active", True),
                     _set("_bw_active_cast_id", "_bw_row.cast_id")],
                ),
            ]),
        ],
    )


def _installer():
    return _tool(
        "Install one Ward; a new cast cannot refresh a live Ignore condition.",
        [_entity("caster_state"), _lasting(),
         {"name": "spell_cast_id", "type": "string", "required": True}],
        [
            _require("spell_cast_id != ''", "Ward requires its actual committed cast reference."),
            _when("lasting_state.lasting_tick == null", [_set("lasting_state.lasting_tick", 0)]),
            _call("resolve_srd_lite_blade_ward", {
                "target_state": "{_caster_state_id}",
                "lasting_state": "{_lasting_state_id}",
            }, ("_bw_active", "_bw_active_cast_id")),
            _set("_bw_application", "'Existing Ward retained; its deadline was not refreshed.'"),
            _when("_bw_active != true", [
                _set("_bw_turn", 0),
                _when("caster_state.turns_started != null", [
                    _set("_bw_turn", "caster_state.turns_started"),
                ]),
                _set("_bw_kept", []), _set("_bw_old", []),
                _when("lasting_state.turn_effects != null", [
                    _set("_bw_old", "lasting_state.turn_effects"),
                ]),
                _each("_bw_old", "_bw_old_row", [
                    _when("not (_bw_old_row.kind == 'lite_blade_ward' and "
                          "_bw_old_row.target == _caster_state_id)", [
                        _push("_bw_kept", "_bw_old_row"),
                    ]),
                ]),
                _require("len(_bw_kept) < 128", "Resolve existing effects before adding Ward."),
                _push("_bw_kept", {
                    "kind": "'lite_blade_ward'", "spell": "'blade_ward'", "label": "'Blade Ward'",
                    "caster": "_caster_state_id", "target": "_caster_state_id",
                    "source_caster_id": "_caster_state_id", "cast_id": "spell_cast_id",
                    "owner": "_caster_state_id", "phase": "'end'", "turn": "_bw_turn + 1",
                    "started_tick": "lasting_state.lasting_tick",
                    "expires_tick": "lasting_state.lasting_tick + 4",
                    "ongoing_duration": True, "spell_level": 0,
                    "combat_qualified": "combat.encounter_active == true",
                }),
                _set("lasting_state.turn_effects", "_bw_kept"),
                _set("_bw_active_cast_id", "spell_cast_id"),
                _set("_bw_application", "'Resistance to Bludgeoning, Piercing and Slashing for 2 turns.'"),
            ]),
            _set("_spell_summary_rows", [
                {"label": "'Ward'", "value": "_bw_application"},
                {"label": "'Active Ward cast'", "value": "_bw_active_cast_id"},
            ]),
            _set("_spell_detail_rows", [
                {"label": "'Lifetime'", "value": "'Current and next own EndTurn; exploration 2 rounds.'"},
                {"label": "'Mitigation'", "value": "'Physical immunity wins; one resistance half overrides physical vulnerability.'"},
            ]),
        ],
    )


def _combat_entry():
    return _tool(
        "Qualify live Ward sources against real time before an encounter starts.",
        [_lasting()],
        [
            _require("combat.encounter_active != true", "Ward entry requires the actual inactive boundary."),
            _set("_bw_entry_old", []), _set("_bw_entry_kept", []),
            _when("lasting_state.turn_effects != null", [
                _set("_bw_entry_old", "lasting_state.turn_effects"),
            ]),
            _each("_bw_entry_old", "_bw_entry_row", [
                *_qualify("_bw_entry_row", "_bw_entry"),
                _when("_bw_entry_valid", [
                    _when("lasting_state.lasting_tick != null and "
                          "lasting_state.lasting_tick + 0.000000001 < _bw_entry_row.expires_tick", [
                        _push("_bw_entry_kept", _entry_record("_bw_entry_row")),
                    ]),
                ], [_push("_bw_entry_kept", "_bw_entry_row")]),
            ]),
            _set("lasting_state.turn_effects", "_bw_entry_kept"),
        ],
    )


def _bind_combat_entry(tools):
    for name in ("start_combat_encounter", "start_combat_encounter_entities", "start_group_combat_encounter"):
        sequence = tools[name]["effects"]
        positions = [index for index, node in enumerate(sequence)
                     if node.get("set", {}).get("field") == "combat.encounter_active"
                     and node["set"].get("value") is True]
        if len(positions) != 1:
            raise ValueError(f"Rebind {name}'s actual inactive-to-active boundary")
        sequence.insert(positions[0], _call("qualify_srd_lite_blade_ward_combat_entry", {
            "lasting_state": "state",
        }))


def _bind_casts(tools):
    seen = set()
    for name in ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"):
        tool = tools[name]
        if id(tool) in seen:
            continue
        seen.add(id(tool))
        tool["effects"].insert(0, _when("spell == 'blade_ward'", [
            _require("target == null or _target_id == _caster_state_id", "Blade Ward is self-only."),
            _require("targets == null or len(targets) == 0 or "
                     "(len(targets) == 1 and targets[0] == _caster_state_id)",
                     "Blade Ward cannot select another creature."),
        ]))
        # Begin saves its choices and Action; only actual cast completion dispatches.
        if name == "begin_srd_spell_action":
            continue
        has_span = False
        for node in _nodes(tool["effects"]):
            setter = node.get("set", {})
            if setter.get("field") == "_spell_time_span_ids":
                setter["value"].append("'blade_ward'")
                has_span = True
        if not has_span:
            # A pure wrapper calls the actual cast owner instead.
            continue
        insertion = next(
            i for i, effect in enumerate(tool["effects"])
            if "_spell_definition.family" in str(effect)
            and "_spell_source_cast_id" in str(effect)
        )
        tool["effects"].insert(insertion, _when(
            "_spell_cast_failed != true and _pending_spell_operation_lost != true "
            "and _spell_definition.family == 'lite_blade_ward'",
            [_require("_slot_level == 0", "Ward is a cantrip without an upcast shape."),
             _call("cast_srd_lite_blade_ward", {
                 "caster_state": "{_caster_state_id}", "lasting_state": "{_lasting_state_id}",
                 "spell_cast_id": "{_spell_source_cast_id}",
             }, ("_spell_summary_rows", "_spell_detail_rows"))],
        ))


def _half_field(node):
    candidates = [node.get("set", {})]
    candidates += [child.get("set", {}) for child in node.get("on_true", [])]
    for setter in candidates:
        field = setter.get("field", "")
        if field and setter.get("value") == field + " // 2":
            return field
    return None


def _bind_damage(tools):
    expected = {
        # The named composite template has no admitted live caller in this
        # sealed pipeline. Its public saved clones already exist before binding.
        "apply_srd_damage": 1,
        "apply_srd_saved_hit_qualifying_damage": 1,
        "apply_srd_saved_hit_qualifying_composite_damage": 1,
    }
    for name, count in expected.items():
        tool = tools[name]
        resistance_count = vulnerability_count = 0
        for sequence in _effect_lists(tool["effects"]):
            for index, node in reversed(list(enumerate(sequence))):
                condition = node.get("condition", "")
                match = re.search(r"([A-Za-z_]\w*) in target\.damage_resistances", condition)
                field = _half_field(node)
                if match and field:
                    dtype = match.group(1)
                    sequence[index:index] = [
                        _set("_bw_native_resists", condition),
                        _set("_bw_packet_resists", f"{field} > 0 and _bw_damage_active == true "
                             f"and {dtype} in _bw_physical_types"),
                    ]
                    node["condition"] = "_bw_native_resists or _bw_packet_resists"
                    resistance_count += 1
                match = re.search(r"([A-Za-z_]\w*) in target\.damage_vulnerabilities", condition)
                if match:
                    dtype = match.group(1)
                    sequence[index:index] = [
                        _set("_bw_native_vulnerable", condition),
                        _set("_bw_packet_physical", f"_bw_damage_active == true and {dtype} in _bw_physical_types"),
                    ]
                    node["condition"] = "_bw_native_vulnerable and not _bw_packet_physical"
                    vulnerability_count += 1
        if (resistance_count, vulnerability_count) != (count, count):
            raise ValueError(f"Rebind {name}'s actual reducers: {resistance_count}/{vulnerability_count}")
        tool["effects"][0:0] = [
            _set("_bw_physical_types", PHYSICAL),
            _call("resolve_srd_lite_blade_ward", {
                # Saved packet generation rewrites resolved target objects to
                # their stable ID before callbacks; join that same owner seam.
                "target_state": "{_target_id}" if name.startswith("apply_srd_saved_hit_") else "{target}",
                "lasting_state": "{_lasting_state_id}",
            }, {"_bw_damage_active": "_bw_active"}),
        ]


def _bind_saved_packet_card(tools):
    """Show actual mixed types at the existing pending NPC completion card."""
    owner = tools['complete_srd_pending_action']
    matched = 0
    for branch in list(_nodes(owner['effects'])):
        if not branch.get('condition', '').startswith(
                "_pending_action.owner_kind == 'creature_attack'"):
            continue
        # Freeze the original lists before adding an unchanged singleton branch.
        for effects in list(_effect_lists(branch.get('on_true', []))):
            for index, node in enumerate(list(effects)):
                text = node.get('params', {}).get('text', '')
                if (node.get('call') != 'record_srd_result_text' or not text.startswith(
                        'Damage dice {_damage_roll.results}; incoming {_damage_incoming} {_weapon_damage_type};')):
                    continue
                actual = deepcopy(node)
                actual['params']['text'] = (
                    'Base damage dice {_damage_roll.results}; total incoming {_damage_incoming}; '
                    '{_bw_type_damage_text} Temporary HP absorbed {_damage_absorbed}; '
                    'HP lost {_damage_hp_lost}.')
                effects[index] = _when(
                    '_hit_type_receipts != null and len(_hit_type_receipts) > 1', [
                        _call('format_srd_saved_hit_type_receipts',
                              {'receipts': '{_hit_type_receipts}'},
                              {'_bw_type_damage_text': '_recorded_result_text'}),
                        actual,
                    ], [deepcopy(node)])
                matched += 1
    if matched != 2:
        raise ValueError('Bind exactly the normal and resumed saved NPC damage cards')


def _bind_end_turn(tools):
    owner = tools["process_srd_turn_effects"]
    capture = _call("format_srd_information_projection", {
        "message": "{boundary_actor}",
    }, {"_bw_boundary_actor": "_info_projection_message"})
    capture["condition"] = "boundary_actor != null and boundary_actor != ''"
    # Free time has no boundary actor. Empty string is an absent required
    # message to the existing formatter, so use a scalar sentinel and skip it.
    owner["effects"][0:0] = [_set("_bw_boundary_actor", "'__time_only__'"), capture]
    matches = 0
    # Place after the generic elapsed-clock override and before kept-row decision.
    for node in _nodes(owner["effects"]):
        loop = node.get("for_each", {})
        if loop.get("as") != "_effect":
            continue
        for sequence in _effect_lists(loop.get("do", [])):
            for index, effect in enumerate(list(sequence)):
                if (effect.get("set", {}).get("field") == "_effect_due"
                        and effect.get("set", {}).get("value") is True
                        and "_effect.expires_tick" in effect.get("condition", "")):
                    sequence[index + 1:index + 1] = [
                        *_qualify("_effect", "_bw_effect"),
                        _when("_bw_effect_valid", [
                            _set("_bw_own_end", "_effect.owner == _bw_boundary_actor and "
                                 "boundary_phase == 'end' and boundary_turn >= _effect.turn"),
                            _set("_bw_clock_due", "lasting_state.lasting_tick != null and "
                                 "lasting_state.lasting_tick + 0.000000001 >= _effect.expires_tick"),
                            _set("_effect_due", "_bw_own_end or ((combat.encounter_active != true or "
                                 "_effect.combat_qualified != true) and _bw_clock_due)"),
                        ]),
                    ]
                    matches += 1
    if matches != 1:
        raise ValueError("Expected the actual single generic turn-effect deadline seam")


def _effect_lists(sequence):
    yield sequence
    for node in sequence:
        for branch in ("on_true", "on_false"):
            if isinstance(node.get(branch), list):
                yield from _effect_lists(node[branch])
        if "for_each" in node:
            yield from _effect_lists(node["for_each"]["do"])


def _bind_exact_dismissal(tools):
    """Keep the original exact public owner, including its active/caster checks."""
    owner = tools["dismiss_srd_spell"]
    owner["effects"].insert(0, _call("resolve_srd_lite_blade_ward", {
        "target_state": "{_caster_state_id}", "lasting_state": "{_lasting_state_id}",
    }, {"_bw_dismiss_active": "_bw_active", "_bw_dismiss_cast_id": "_bw_active_cast_id"}))
    matches = 0
    token = ("_dismiss_effect.expires_tick != null and "
             "lasting_state.lasting_tick < _dismiss_effect.expires_tick")
    # Current Mage Armour / Longstrider producer factors finite expiry into
    # one per-row local. Never append Ward to its near-cap expression.
    for sequence in list(_effect_lists(owner["effects"])):
        for index, node in reversed(list(enumerate(sequence))):
            condition = node.get("condition", "")
            lifetime_ref = "_lite_dismiss_lifetime == true"
            if lifetime_ref in condition:
                if "_dismiss_effect.cast_id == cast_id" not in condition:
                    raise ValueError("Ward dismissal requires the exact outer cast guard")
                setters = [i for i, prior in enumerate(sequence[:index])
                           if prior.get("set", {}).get("field") == "_lite_dismiss_lifetime"
                           and token in str(prior["set"].get("value", ""))]
                if len(setters) != 1:
                    raise ValueError("Expected one preceding per-row dismissal lifetime setter")
                sequence[index:index] = [
                    *_qualify("_dismiss_effect", "_bw_dismiss_row"),
                    _set("_bw_dismiss_row_live", "_lite_dismiss_lifetime == true or "
                         "(_bw_dismiss_row_valid and _bw_dismiss_active == true "
                         "and _bw_dismiss_cast_id == cast_id)"),
                ]
                node["condition"] = condition.replace(lifetime_ref, "_bw_dismiss_row_live == true")
                matches += 1
            elif token in condition:
                # Preserve compatibility with the immutable reviewed support base.
                sequence[index:index] = [
                    *_qualify("_dismiss_effect", "_bw_dismiss_row"),
                    _set("_bw_dismiss_row_live", f"({token}) or (_bw_dismiss_row_valid and "
                         "_bw_dismiss_active == true and _bw_dismiss_cast_id == cast_id)"),
                ]
                node["condition"] = condition.replace(token, "_bw_dismiss_row_live")
                matches += 1
    if matches != 1:
        raise ValueError("Expected the actual exact-cast generic dismissal row gate")


def bind_blade_ward_support(runtime):
    """Mutate only root's private Lite runtime; no file I/O, admission or execution."""
    tools = runtime["tools"]
    if "resolve_srd_lite_blade_ward" in tools:
        raise ValueError("Ward draft must bind once to the original sealed source")
    runtime["definitions"]["spells"]["blade_ward"] = deepcopy(PROFILE)
    runtime["definitions"]["spell_casting"]["blade_ward"] = deepcopy(HEADER)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"][FAMILY] = {
        "type": "object", "additionalProperties": False,
        "required": [key for key in PROFILE if key != "family"],
        "properties": {
            key: {"type": "boolean" if isinstance(value, bool)
                  else "integer" if isinstance(value, int) else "string", "enum": [value]}
            for key, value in PROFILE.items() if key != "family"
        },
    }
    tools["resolve_srd_lite_blade_ward"] = _resolver()
    tools["cast_srd_lite_blade_ward"] = _installer()
    tools["qualify_srd_lite_blade_ward_combat_entry"] = _combat_entry()
    _bind_casts(tools)
    _bind_damage(tools)
    _bind_saved_packet_card(tools)
    _bind_end_turn(tools)
    _bind_combat_entry(tools)


def bind_blade_ward_dismissal(runtime):
    """Join exact dismissal after the sealed mobility and Retreat adapters bind."""
    _bind_exact_dismissal(runtime['tools'])
