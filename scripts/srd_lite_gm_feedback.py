"""Bind the selected GM casting-input and refusal repairs to the Lite package."""

from copy import deepcopy

from .srd_lite_recipient_contract import condition_inputs, recipient_contracts

CAST_ROOTS = (
    "begin_srd_spell_action",
    "cast_srd_reaction_spell",
    "begin_srd_after_hit_smite",
    "begin_srd_spell_cast",
    "begin_srd_extended_spell_action",
    "cast_spell_outside_combat",
)


def retire_refusal_stubs(runtime, record):
    """Retire dead declarations in the new version; retain any internally called bodies."""
    tools = runtime["tools"]
    refused = {
        name
        for name, tool in tools.items()
        if tool.get("effects") and tool["effects"][0].get("require", {}).get("condition") == "false"
    }
    called = set()

    def inspect(value, *, controls=False):
        if isinstance(value, dict):
            if isinstance(value.get("call"), str):
                called.add(value["call"])
            for key, child in value.items():
                inspect(child, controls=controls or key == "player_controls")
        elif isinstance(value, list):
            for child in value:
                inspect(child, controls=controls)
        elif controls and isinstance(value, str) and value in refused:
            raise ValueError(f"A shipped player control references refused tool {value}")

    inspect(runtime)
    inspect(record.get("ui_surfaces", []), controls=True)
    record["tools"] = [tool for tool in record["tools"] if tool["name"] not in refused]
    removed = refused - called
    for name in removed:
        del tools[name]
    return refused, removed


def _parameters(runtime, tool):
    declarations = runtime.get("parameter_declarations", {})
    return [declarations[p] if isinstance(p, str) else p for p in tool.get("parameters", [])]


def bind_gm_feedback(runtime):
    """Keep rules facts in Lite while exposing every shared missing input before effects."""
    tools = runtime["tools"]
    definitions = runtime["definitions"]
    somatic_guidance = (
        "Defaults to true. Held weapons, shields and other items never prevent spell "
        "gestures, in or out of combat. Set false only when the scene prevents the "
        "caster from moving enough to gesture."
    )
    parameters = list(runtime.get("parameter_declarations", {}).values())
    parameters.extend(
        parameter
        for tool in tools.values()
        for parameter in tool.get("parameters", [])
        if isinstance(parameter, dict)
    )
    for parameter in parameters:
        if parameter.get("name") == "somatic_component_available":
            parameter.update(default=True, description=somatic_guidance)
        elif parameter.get("name") == "caster_can_touch_target":
            parameter["description"] = (
                "For a Touch spell on another creature without engine-derived combat range, "
                "supply true only when the caster "
                "can touch that target now; false means it is out of touch reach. "
                "Self-touch is automatic."
            )
        elif parameter.get("name") == "target_distance":
            parameter["description"] = (
                "Current target distance in feet when not supplied by saved combat geometry. "
                "Supply the established scene distance; self-targeting is distance zero."
            )
    implemented = sorted(set(definitions["srd_lite_implemented"]))
    aliases = definitions.get("srd_lite_aliases", {})
    definitions.pop("srd_lite_single_target_spells", None)
    definitions.pop("srd_lite_multiple_target_spells", None)
    definitions["srd_lite_recipient_contracts"] = recipient_contracts(runtime)

    for name in CAST_ROOTS:
        tool = tools[name]
        parameters = deepcopy(_parameters(runtime, tool))
        by_name = {p["name"]: p for p in parameters}
        by_name["spell"].update(type="enum", enum_values=implemented + sorted(aliases))
        # Preserve omission separately from an explicit inability to reach the target.
        # Internal range checks still fail closed for either value.
        if "caster_can_touch_target" in by_name:
            by_name["caster_can_touch_target"]["default"] = None
        # Inline spell choices while reusing the other parameter declarations.
        tool["parameters"] = [
            by_name[p["name"]] if p["name"] in {"spell", "caster_can_touch_target"} else old
            for old, p in zip(tool["parameters"], parameters, strict=True)
        ]
        checks = []

        def missing(condition, field, guidance="", _checks=checks):
            _checks.append(
                {"condition": condition, "error": f"Missing input '{field}'. {guidance}".strip()}
            )

        if "slot_resource" in by_name:
            missing(
                "(caster == null and caster_state.simulacrum_character_data == null) "
                "or slot_resource != null",
                "slot_resource",
                "Select the actual character spell-slot, cantrip or Ritual resource.",
            )
        for component in ("verbal", "somatic", "material"):
            field = component + "_component_available"
            if field not in by_name:
                continue
            exempt = (
                "(caster == null and caster_state.spellcasting_profile.component_exemptions "
                f"!= null and '{component}' in "
                "caster_state.spellcasting_profile.component_exemptions)"
            )
            scope = (
                "spell not in definitions.spell_casting or "
                f"'{component}' not in definitions.spell_casting[spell].components or {exempt}"
            )
            if component != "somatic":
                missing(
                    f"{scope} or {field} != null",
                    field,
                    "Supply the current scene fact or a saved stat-block exemption.",
                )
            checks.append(
                {
                    "condition": f"{scope} or {field} == null or {field} == true",
                    "error": f"Input '{field}' is unavailable for this spell.",
                }
            )
        for identity, contract in definitions["srd_lite_recipient_contracts"].items():
            field = contract["field"]
            if field not in by_name or not condition_inputs(contract["when"]) <= by_name.keys():
                continue
            alternatives = []
            for caller_input in contract["caller_inputs"]:
                if caller_input not in by_name:
                    continue
                condition = caller_input + " != null"
                if caller_input == "targets" and contract["nonempty"]:
                    condition += " and len(targets) > 0"
                if caller_input == "object_target_id":
                    condition += " and object_target_id != ''"
                alternatives.append("(" + condition + ")")
            if not alternatives:
                continue
            mode_exempt = (
                "combat.encounter_active != true"
                if contract["in_combat"]
                else "combat.encounter_active == true"
            )
            missing(
                f"spell not in definitions.srd_lite_recipient_contracts.{identity}.spells or "
                f"{mode_exempt} or not ({contract['when']}) or " + " or ".join(alternatives),
                field,
                "Select the actual recipients for this spell."
                if field == "targets"
                else "Select the actual recipient. Self-touch is automatic; touching another "
                "creature requires caster_can_touch_target=true.",
            )
        # The existing combat projector owns scalar geometry before staging or
        # payment. Its qualifier must run before asking a caller for those facts.
        geometry_exempt = (
            "combat.encounter_active == true and "
            "spell in definitions.srd_lite_scalar_geometry_spells"
        )
        if "targets" in by_name:
            geometry_exempt += " and (targets == null or len(targets) == 0)"
        if {"caster_state", "target", "caster_can_touch_target"} <= by_name.keys():
            touch_exempt = (
                "spell not in definitions.srd_lite_scalar_geometry_spells or "
                "definitions.spells[spell].range_kind != 'touch' or target == null or "
                f"_caster_state_id == _target_id or ({geometry_exempt})"
            )
            missing(
                f"{touch_exempt} or caster_can_touch_target != null",
                "caster_can_touch_target",
                "Supply true if the caster can touch this other creature now; "
                "otherwise select a reachable target. Self-touch is automatic.",
            )
            checks.append(
                {
                    "condition": f"{touch_exempt} or caster_can_touch_target != false",
                    "error": "Input 'caster_can_touch_target' is false: the selected target is "
                    "out of touch reach. Select a reachable target; do not claim contact.",
                }
            )
        if {"caster_state", "target", "target_distance"} <= by_name.keys():
            missing(
                "spell not in definitions.srd_lite_scalar_geometry_spells or "
                "definitions.spells[spell].range_kind != 'distance' or target == null or "
                f"_caster_state_id == _target_id or ({geometry_exempt}) or target_distance != null",
                "target_distance",
                "Supply the current distance to the target in feet from established scene facts.",
            )
        # Idempotence permits the same binder during ordinary package regeneration.
        old = [
            pc
            for pc in tool.get("pre_conditions", []) or []
            if not isinstance(pc, dict)
            or not pc.get("error", "").startswith(("Missing input '", "Input '"))
        ]
        tool["pre_conditions"] = checks + old

    for tool in tools.values():
        parameters = {p["name"] for p in _parameters(runtime, tool)}
        if {"caster_state", "target"} <= parameters:
            for check in tool.get("pre_conditions", []) or []:
                if isinstance(check, dict) and "caster_can_touch_target == true" in check.get(
                    "condition", ""
                ):
                    condition = check["condition"]
                    if "_caster_state_id == _target_id" not in condition:
                        check["condition"] = condition.replace(
                            "caster_can_touch_target == true",
                            "(caster_can_touch_target == true or _caster_state_id == _target_id)",
                        )
                if isinstance(check, dict) and check.get("error") == (
                    "The selected target does not satisfy the spell's range."
                ):
                    check["error"] = (
                        "The selected target is outside this spell's range. For Touch, "
                        "caster_can_touch_target must be true for another creature; "
                        "self-touch is automatic. For distance range, supply target_distance "
                        "in feet within range_feet."
                    )

    # Bracket indexing rejects missing keys before the authored refusal can be returned.
    tools["restore_srd_resource"]["pre_conditions"][1]["condition"] = (
        "target.resources != null and resource in target.resources and "
        "target.resource_maxima != null and resource in target.resource_maxima"
    )
    source = tools["check_srd_contingency_source"]["effects"]
    if not any(e.get("set", {}).get("field") == "_contingency_record" for e in source):
        source.insert(0, {"set": {"field": "_contingency_record", "value": None}})

    # These operations already pass the Steed's saved creator to their owner
    # helper. Reject a non-Steed before that missing reference reaches a callee.
    for name in (
        "mount_srd_otherworldly_steed",
        "dismount_srd_otherworldly_steed",
        "take_srd_otherworldly_steed_action",
        "begin_srd_otherworldly_forced_movement",
        "use_srd_steed_fell_glare",
        "use_srd_steed_fey_step",
        "use_srd_steed_healing_touch",
    ):
        tool = tools[name]
        check = {
            "condition": "steed_state.created_caster_state_id != null",
            "error": "Select an actual Otherworldly Steed with its saved caster reference "
            "(created_caster_state_id); an ordinary creature cannot use this operation.",
        }
        previous = tool.get("pre_conditions", []) or []
        if check not in previous:
            tool["pre_conditions"] = [check, *previous]

    for name in (
        "study_srd_appearance",
        "study_srd_illusion",
        "study_srd_phantasm",
        "study_srd_maze_escape",
        "change_srd_equipment",
    ):
        if name not in tools:
            continue
        tool = tools[name]
        parameters = deepcopy(_parameters(runtime, tool))
        required_sources = {
            p["model_derive"]["source"]
            for p in parameters
            if isinstance(p.get("model_derive"), dict) and p["model_derive"].get("kind") == "copy"
        }
        tool["parameters"] = [
            dict(p, model_required=True) if p["name"] in required_sources else old
            for old, p in zip(tool["parameters"], parameters, strict=True)
        ]
    return aliases
