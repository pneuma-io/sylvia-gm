"""Original Lite status joins over the sealed real cast, turn and attack owners.

Bind after the profile and Ward adapters, before the Lite boundary. This module
does not generate, execute, expose a new public operation or write damage. Full
SRD source files remain unchanged. Native evidence belongs to the root's exact
admitted archive and public tests, not to this construction code.

Frost uses the original maximum speed-reduction projection and bounded clock
fallback when its actual expiry owner is dead. Mockery keeps the original next-
attack convention. Grasp retains its separate original Opportunity Attack owner.
"""

from copy import deepcopy

from .srd_control_behaviors import _call, _each, _entity, _lasting, _push, _require, _set, _tool, _when
from .srd_lite_profiles import lists


STATUS_SPELLS = ("ray_of_frost", "vicious_mockery")
SELECTED = "spell == 'ray_of_frost' or spell == 'vicious_mockery'"
INSTALLER = "install_srd_lite_offensive_status"


def _nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def _qualify(row, prefix):
    """Factored predicates stay within the original expression grammar/cap."""
    return [
        _set(prefix + "_id", f"{row}.lite_offensive_status == true and "
             f"{row}.cast_id != null and {row}.cast_id != '' and "
             f"{row}.caster != null and {row}.source_caster_id == {row}.caster"),
        _set(prefix + "_span", f"{row}.ongoing_duration == true and {row}.spell_level == 0 and "
             f"{row}.started_tick != null and {row}.expires_tick != null and "
             f"{row}.expires_tick == {row}.started_tick + 2 and {row}.turn != null"),
        _set(prefix + "_frost", f"{row}.spell == 'ray_of_frost' and {row}.kind == 'speed_reduction' "
             f"and {row}.owner == {row}.caster and {row}.phase == 'start' and {row}.magnitude == 10"),
        _set(prefix + "_mockery", f"{row}.spell == 'vicious_mockery' and "
             f"{row}.kind == 'next_attack_disadvantage' and {row}.owner == {row}.target "
             f"and {row}.phase == 'end' and {row}.magnitude == 0"),
        _set(prefix + "_valid", f"{prefix}_id and {prefix}_span and "
             f"({prefix}_frost or {prefix}_mockery)"),
    ]


def _target_live(row, prefix):
    """Mockery owns the target counter; Frost uses actual turn processing."""
    return [
        _set(prefix + "_clock", f"{prefix}_valid and lasting_state.lasting_tick != null and "
             f"lasting_state.lasting_tick + 0.000000001 < {row}.expires_tick"),
        _set(prefix + "_counter", "target_state.turns_started == null"),
        _when("target_state.turns_started != null", [
            _set(prefix + "_counter", f"{prefix}_mockery and target_state.turns_started <= {row}.turn"),
        ]),
        _set(prefix + "_combat", f"{prefix}_valid and combat.encounter_active == true and "
             f"{row}.combat_qualified == true and {prefix}_counter"),
        _set(prefix + "_live", f"{prefix}_valid and {row}.target == _target_state_id and "
             f"({prefix}_combat or ((combat.encounter_active != true or "
             f"{row}.combat_qualified != true) and {prefix}_clock))"),
    ]


def _mockery_consumer():
    return _tool(
        "Consume only qualified Lite Mockery immediately before a real direct NPC attack roll.",
        [_entity("target_state"), _lasting()],
        [
            _set("_lo_mockery_penalty", False), _set("_lo_mockery_rows", []),
            _set("_lo_mockery_kept", []),
            _when("lasting_state.turn_effects != null", [
                _set("_lo_mockery_rows", "lasting_state.turn_effects"),
            ]),
            _each("_lo_mockery_rows", "_lo_mockery_row", [
                *_qualify("_lo_mockery_row", "_lo_mockery"),
                *_target_live("_lo_mockery_row", "_lo_mockery"),
                _when("_lo_mockery_mockery and _lo_mockery_live", [
                    _set("_lo_mockery_penalty", True),
                ], [_push("_lo_mockery_kept", "_lo_mockery_row")]),
            ]),
            # An unrelated NPC attack must not replace an absent native list
            # with an empty list merely because this qualified reader ran.
            _when("_lo_mockery_penalty", [
                _set("lasting_state.turn_effects", "_lo_mockery_kept"),
            ]),
        ],
    )


def _installer(original):
    """Private clone preserves the real constructor/projection and original ID bindings."""
    tool = deepcopy(original)
    tool["description"] = "Install qualified Lite offensive statuses through the original next-turn constructor."
    selected = "(" + SELECTED + ")"
    tool["effects"][0:0] = [
        _set("_lo_install_selected", selected),
        _when("_lo_install_selected", [
            _require("spell_cast_id != null and spell_cast_id != '' and "
                     "_spell_cast_owner_id == _caster_state_id and actual_spell_level == 0",
                     "This status requires its actual committed cantrip source and caster identity."),
            _set("_lo_install_frost", "spell == 'ray_of_frost' and kind == 'speed_reduction' and "
                 "expiry_owner == 'caster' and expiry_phase == 'start' and magnitude == 10"),
            _set("_lo_install_mockery", "spell == 'vicious_mockery' and kind == 'next_attack_disadvantage' and "
                 "expiry_owner == 'target' and expiry_phase == 'end' and magnitude == 0"),
            _require("dice_count == 0 and (_lo_install_frost or _lo_install_mockery)",
                     "The selected status must match its admitted kind and next-turn owner."),
        ]),
    ]
    count = 0
    keys = None
    for effects in list(lists(tool["effects"])):
        for index, node in enumerate(list(effects)):
            push = node.get("list_push", {})
            if push.get("list") != "_turn_effects" or not isinstance(push.get("value"), dict):
                continue
            original_row = deepcopy(push["value"])
            row = {**original_row, "lite_offensive_status": True,
                   "started_tick": "lasting_state.lasting_tick",
                   "combat_qualified": "combat.encounter_active == true"}
            effects[index] = _when("_lo_install_selected", [_push("_turn_effects", row)], [deepcopy(node)])
            keys = tuple(row)
            count += 1
    if count != 1:
        raise ValueError("Rebind the exact original next-turn record constructor")
    return tool, keys


def _entry_tool(keys):
    entry_row = {key: "_lo_entry_row." + key for key in keys}
    entry_row["combat_qualified"] = True
    return _tool(
        "Prune expired exploration statuses before the actual encounter boundary and qualify live sources.",
        [_lasting()],
        [
            _require("combat.encounter_active != true", "Status entry requires the actual inactive boundary."),
            _set("_lo_entry_rows", []), _set("_lo_entry_kept", []), _set("_lo_entry_targets", []),
            _when("lasting_state.turn_effects != null", [
                _set("_lo_entry_rows", "lasting_state.turn_effects"),
            ]),
            _each("_lo_entry_rows", "_lo_entry_row", [
                *_qualify("_lo_entry_row", "_lo_entry"),
                _when("_lo_entry_valid", [
                    _when("_lo_entry_row.target not in _lo_entry_targets", [
                        _push("_lo_entry_targets", "_lo_entry_row.target"),
                    ]),
                    _when("lasting_state.lasting_tick != null and lasting_state.lasting_tick + "
                          "0.000000001 < _lo_entry_row.expires_tick", [
                        _push("_lo_entry_kept", entry_row),
                    ]),
                ], [_push("_lo_entry_kept", "_lo_entry_row")]),
            ]),
            _set("lasting_state.turn_effects", "_lo_entry_kept"),
            _each("_lo_entry_targets", "_lo_entry_target", [
                _call("project_srd_turn_modifiers", {
                    "target_state": "{_lo_entry_target}", "lasting_state": "{_lasting_state_id}",
                }),
            ]),
        ],
    )


def _bind_source_allocation(tools):
    matches = 0
    visited = set()
    for name in ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"):
        tool = tools[name]
        if id(tool) in visited:
            continue
        visited.add(id(tool))
        for node in _nodes(tool["effects"]):
            setter = node.get("set", {})
            if setter.get("field") == "_spell_time_span_ids":
                for spell in STATUS_SPELLS:
                    literal = "'" + spell + "'"
                    if literal not in setter["value"]:
                        setter["value"].append(literal)
                matches += 1
    if not matches:
        raise ValueError("Bind source IDs at the original committed cast allocator")


def _bind_install_callbacks(tools):
    matches = 0
    visited = set()
    for tool in list(tools.values()):
        if id(tool) in visited:
            continue
        visited.add(id(tool))
        for effects in list(lists(tool.get("effects", []))):
            for index, node in enumerate(list(effects)):
                if (node.get("call") != "install_srd_turn_effect" or
                        node.get("params", {}).get("spell") != "{spell}"):
                    continue
                selected = deepcopy(node)
                selected["call"] = INSTALLER
                effects[index] = _when(SELECTED, [selected], [deepcopy(node)])
                matches += 1
    if matches < 3:
        raise ValueError("Join the actual ordinary hit/save and resumed spell-hit callbacks")


def _bind_expiry(tools):
    owner = tools["process_srd_turn_effects"]
    capture = _call("format_srd_information_projection", {"message": "{boundary_actor}"},
                    ("_info_projection_message",))
    capture["export_context"] = {"_lo_boundary_actor": "_info_projection_message"}
    capture["condition"] = "boundary_actor != null and boundary_actor != ''"
    owner["effects"][0:0] = [_set("_lo_boundary_actor", "'__time_only__'"), capture]
    matches = 0
    for node in list(_nodes(owner["effects"])):
        loop = node.get("for_each", {})
        if loop.get("as") != "_effect":
            continue
        for effects in list(lists(loop.get("do", []))):
            for index, effect in enumerate(list(effects)):
                if (effect.get("set", {}).get("field") != "_effect_due" or
                        effect["set"].get("value") is not True or
                        "_effect.expires_tick" not in effect.get("condition", "")):
                    continue
                effects[index + 1:index + 1] = [
                    _set("_lo_native_due", "_effect_due"),
                    *_qualify("_effect", "_lo_due"),
                    _when("_lo_due_valid", [
                        _call("resolve_srd_lite_offensive_expiry_owner", {
                            "owner_state": "{_effect.owner}",
                        }, ("_lo_expiry_owner_alive",)),
                        _set("_lo_own_due", "_effect.owner == _lo_boundary_actor and "
                             "_effect.phase == boundary_phase and boundary_turn >= _effect.turn"),
                        _set("_lo_clock_due", "lasting_state.lasting_tick != null and "
                             "lasting_state.lasting_tick + 0.000000001 >= _effect.expires_tick"),
                        _set("_effect_due", "_lo_own_due or ((combat.encounter_active != true or "
                             "_effect.combat_qualified != true) and _lo_clock_due) or "
                             "(_lo_expiry_owner_alive != true and _lo_native_due)"),
                    ]),
                ]
                matches += 1
    if matches != 1:
        raise ValueError("Bind the original turn processor after its single generic clock fallback")


def _bind_entry(tools):
    for name in ("start_combat_encounter", "start_combat_encounter_entities", "start_group_combat_encounter"):
        effects = tools[name]["effects"]
        indices = [i for i, node in enumerate(effects)
                   if node.get("set", {}).get("field") == "combat.encounter_active"
                   and node["set"].get("value") is True]
        if len(indices) != 1:
            raise ValueError("Bind " + name + " at its actual inactive-to-active boundary")
        effects.insert(indices[0], _call("qualify_srd_lite_offensive_combat_entry", {"lasting_state": "state"}))


def _bind_direct_npc_rolls(tools):
    for name in ("resolve_adjudicated_creature_attack", "continue_srd_creature_attack_targeting"):
        count = 0
        for effects in list(lists(tools[name]["effects"])):
            for index, node in enumerate(list(effects)):
                if node.get("store_as") != "_attack_roll" or node.get("dice", {}).get("expression") != "d20":
                    continue
                if "_condition_disadvantage" not in str(node["dice"].get("disadvantage")):
                    raise ValueError("Retain the original NPC Disadvantage expression")
                effects[index:index] = [
                    _call("consume_srd_lite_mockery_npc_attack", {
                        "target_state": "{_actor_id}", "lasting_state": "{_lasting_state_id}",
                    }, ("_lo_mockery_penalty",)),
                    _set("_condition_disadvantage", "_condition_disadvantage == true or _lo_mockery_penalty == true"),
                    _set("_effective_disadvantage", "_effective_disadvantage == true or _lo_mockery_penalty == true"),
                ]
                count += 1
        if count != 1:
            raise ValueError("Bind " + name + " immediately before its original single direct attack roll")


def bind_offensive_statuses(runtime):
    """Change only root's private Lite runtime; no filesystem or execution side effects."""
    tools = runtime["tools"]
    if INSTALLER in tools:
        raise ValueError("Offensive statuses must bind once to the original sealed pipeline")
    installer, keys = _installer(tools["install_srd_turn_effect"])
    _bind_source_allocation(tools)
    _bind_install_callbacks(tools)
    tools[INSTALLER] = installer
    tools["consume_srd_lite_mockery_npc_attack"] = _mockery_consumer()
    tools["resolve_srd_lite_offensive_expiry_owner"] = _tool(
        "Read only the saved expiry owner's life flag; preserve native finite fallback.",
        [_entity("owner_state")],
        [_set("_lo_expiry_owner_alive", "owner_state.is_alive == true")],
    )
    tools["qualify_srd_lite_offensive_combat_entry"] = _entry_tool(keys)
    _bind_expiry(tools)
    _bind_entry(tools)
    _bind_direct_npc_rolls(tools)
