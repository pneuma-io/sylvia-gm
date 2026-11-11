"""Bind the selected GM casting-input and refusal repairs to the Lite package."""

from copy import deepcopy

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
    implemented = sorted(set(definitions["srd_lite_implemented"]))
    aliases = definitions.get("srd_lite_aliases", {})
    single = []
    multiple = []
    # These operations derive recipients before the generic damage family runs.
    recipient_owners = {
        "sunburst": "cast_srd_sunburst",
        "fire_storm": "qualify_srd_lite_fire_storm_cubes",
    }
    for spell, profile in definitions["spells"].items():
        family = profile["family"]
        names = ("cast_" + family, "cast_spell_" + family)
        owner = (
            tools[recipient_owners[spell]]
            if spell in recipient_owners
            else next((tools[name] for name in names if name in tools), None)
        )
        required = {p["name"] for p in _parameters(runtime, owner or {}) if p.get("required", True)}
        if "target" in required:
            single.append(spell)
        if "targets" in required:
            multiple.append(spell)
    definitions["srd_lite_single_target_spells"] = sorted(single)
    definitions["srd_lite_multiple_target_spells"] = sorted(multiple)

    for name in CAST_ROOTS:
        tool = tools[name]
        parameters = deepcopy(_parameters(runtime, tool))
        by_name = {p["name"]: p for p in parameters}
        by_name["spell"].update(type="enum", enum_values=implemented + sorted(aliases))
        # Inline spell choices while reusing the other parameter declarations.
        tool["parameters"] = [
            by_name["spell"] if p["name"] == "spell" else old
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
        if "target" in by_name:
            alternatives = ["target != null"]
            if "targets" in by_name:
                alternatives.append("(targets != null and len(targets) > 0)")
            if "object_target_id" in by_name:
                alternatives.append("object_target_id != ''")
            missing(
                "spell not in definitions.srd_lite_single_target_spells or "
                + " or ".join(alternatives),
                "target",
                "Select the actual recipient. Self-touch is automatic; touching another "
                "creature requires caster_can_touch_target=true.",
            )
        if "targets" in by_name:
            missing(
                "spell not in definitions.srd_lite_multiple_target_spells or "
                "(targets != null and len(targets) > 0)",
                "targets",
                "Select the actual recipients for this spell.",
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

    # Bracket indexing rejects missing keys before the authored refusal can be returned.
    tools["restore_srd_resource"]["pre_conditions"][1]["condition"] = (
        "target.resources != null and resource in target.resources and "
        "target.resource_maxima != null and resource in target.resource_maxima"
    )
    source = tools["check_srd_contingency_source"]["effects"]
    if not any(e.get("set", {}).get("field") == "_contingency_record" for e in source):
        source.insert(0, {"set": {"field": "_contingency_record", "value": None}})

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
