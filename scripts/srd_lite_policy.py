"""Fail closed at the variant's ordinary selection and casting owners."""

from .srd_control_behaviors import _each
from .srd_lite_catalogue import VARIANT_ID
from .srd_timed_actor_defenses import _require, _set, _when

CAST_ROOTS = {"cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"}
CORE_SPELL_INTENTS = CAST_ROOTS | {
    "begin_srd_spell_cast",
    "continue_srd_spell_cast",
    "complete_srd_pending_action",
    "begin_srd_character_attack",
    "begin_srd_creature_attack",
    "move_srd_scene_creature",
    "advance_srd_lasting_time",
    "end_concentration",
    "dismiss_srd_spell",
    "resolve_srd_scene_relation",
    "resolve_srd_sight",
    "read_srd_creature_attitude",
    "read_srd_passive_perception",
    "escape_srd_nonmagical_restraint",
    "begin_srd_scene_fall",
    "prepare_srd_spell_object",
    "query_srd_thunderwave_sound",
    "collect_srd_irresistible_dance",
}


def bind_exposed_operations(runtime, record):
    """Imported old sources cannot turn a pending recast into a supported operation."""
    denied = []
    for intent in record["tools"]:
        name = intent["name"]
        unrelated_fact = name.startswith(("record_srd_", "read_srd_carried_"))
        native_ability = "native_" in name
        if intent.get("capability") == "dnd.srd.5.2.1.spells" and not (
            name in CORE_SPELL_INTENTS or unrelated_fact or native_ability
        ):
            runtime["tools"][name].setdefault("effects", []).insert(
                0,
                _require(
                    "false",
                    "This spell operation is pending or excluded in the SRD Lite foundation.",
                ),
            )
            runtime["tools"][name].setdefault("metadata", {})["visible_to_player"] = False
            denied.append(name)
    return denied


def bind_boundary(runtime, catalogue, implemented):
    """Keep the complete catalogue separate from the enabled mechanical definitions."""
    identities = {row["identity"] for row in catalogue["profiles"]}
    if not set(implemented) <= identities:
        raise ValueError("An enabled profile is outside the reviewed normal catalogue")
    aliases = {alias: row["identity"] for row in catalogue["profiles"] for alias in row["aliases"]}
    definitions = runtime["definitions"]
    definitions["srd_lite_catalogue"] = sorted(identities)
    definitions["srd_lite_implemented"] = sorted(implemented)
    definitions["srd_lite_aliases"] = aliases
    for section in ("spells", "spell_casting"):
        definitions[section] = {
            key: value for key, value in definitions[section].items() if key in implemented
        }
    guarded = []
    for name, tool in runtime["tools"].items():
        parameters = tool.get("parameters", [])
        parameter_names = {p["name"] for p in parameters if isinstance(p, dict)}
        # Shared spell acquisition, NPC/item casts and imported choices eventually
        # consume these same definitions. Guard public scalar spell owners before
        # their original effects can spend or mutate anything.
        public = tool.get("metadata", {}).get("category") != "internal"
        if public and "spells" in parameter_names:
            requested = next(p for p in parameters if p.get("name") == "spells")
            record_items = requested.get("items", {}).get("type") == "object"
            reference = "_lite_acquired_spell.spell" if record_items else "_lite_acquired_spell"
            tool.setdefault("effects", [])[:0] = [
                _when(
                    "spells != null",
                    [
                        _each(
                            "spells",
                            "_lite_acquired_spell",
                            [
                                _require(
                                    reference + " in definitions.srd_lite_catalogue",
                                    (
                                        "This spell is excluded from the SRD Lite normal-pl"
                                        "ayer catalogue."
                                    ),
                                ),
                                _require(
                                    reference + " in definitions.srd_lite_implemented",
                                    "This SRD Lite profile is pending; it cannot be acquired yet.",
                                ),
                            ],
                        )
                    ],
                )
            ]
        if "spell" in parameter_names and (public or name in CAST_ROOTS):
            tool.setdefault("pre_conditions", [])[:0] = [
                {
                    ("condition"): (
                        "spell in definitions.srd_lite_catalogue or spell in definitions.sr"
                        "d_lite_aliases"
                    ),
                    "error": "This spell is excluded from the SRD Lite normal-player catalogue.",
                },
                {
                    ("condition"): (
                        "spell in definitions.srd_lite_implemented or (spell in definitions"
                        ".srd_lite_aliases and definitions.srd_lite_aliases[spell] in defin"
                        "itions.srd_lite_implemented)"
                    ),
                    "error": "This SRD Lite profile is pending; it cannot be selected or cast yet.",
                },
            ]
            for precondition in tool["pre_conditions"]:
                for prepared in ("caster_state.prepared_spells", "caster.data.wizard_spellbook"):
                    token = "spell in " + prepared
                    if token in precondition["condition"]:
                        precondition["condition"] = precondition["condition"].replace(
                            token,
                            "("
                            + token
                            + (
                                " or (spell in definitions.srd_lite_aliases and definitions"
                                ".srd_lite_aliases[spell] in "
                            )
                            + prepared
                            + "))",
                        )
            # Admit names for the existing explicit alias/pending policy. The
            # required lookup below still fails closed after this policy.
            for parameter in parameters:
                if parameter.get("name") == "spell":
                    parameter["type"] = "string"
                    parameter.pop("enum_values", None)
            tool.setdefault("effects", [])[:0] = [
                _when(
                    "spell in definitions.srd_lite_aliases",
                    [_set("spell", "definitions.srd_lite_aliases[spell]")],
                ),
                _require(
                    "spell in definitions.srd_lite_catalogue",
                    "This spell is excluded from the SRD Lite normal-player catalogue.",
                ),
                _require(
                    "spell in definitions.srd_lite_implemented",
                    "This SRD Lite profile is pending; it cannot be selected or cast yet.",
                ),
            ]
            guarded.append(name)

        def bind_lookup(value, bound_parameters=parameters):
            if isinstance(value, dict):
                lookup = value.get("table_lookup")
                if (
                    isinstance(lookup, dict)
                    and lookup.get("exact_selector")
                    and lookup.get("table") in ("definitions.spells", "definitions.spell_casting")
                ):
                    selector = next(p for p in bound_parameters if p.get("name") == lookup["key"])
                    if selector["type"] == "enum":
                        selector["enum_values"] = sorted(implemented)
                    else:
                        # Dynamic selection is guarded above; required remains
                        # true and the independently validated profile table is
                        # restricted to the three executable identities.
                        lookup["exact_selector"] = False
                for child in value.values():
                    bind_lookup(child)
            elif isinstance(value, list):
                for child in value:
                    bind_lookup(child)

        bind_lookup(tool.get("effects", []))
    if not set(guarded) >= CAST_ROOTS:
        raise ValueError("The ordinary public cast roots were not all guarded")
    return guarded


def rebind_owned_identity(value, *, path=(), changes=None):
    """Rebind known structured ownership fields; preserve reference dependencies."""
    changes = [] if changes is None else changes
    original = "sylvia.srd-lite.bg3"
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (key,)
            if key in {"state_namespace", "namespace", "package_id"} and child == original:
                value[key] = VARIANT_ID
                changes.append(".".join(map(str, child_path)))
            elif key == "condition" and isinstance(child, str):
                # Accepted PC/NPC admission guards contain this exact package
                # identity. This changes no core evaluator or arbitrary text.
                token = ".package_id == '" + original + "'"
                if token in child:
                    value[key] = child.replace(token, ".package_id == '" + VARIANT_ID + "'")
                    changes.append(".".join(map(str, child_path)))
            else:
                rebind_owned_identity(child, path=child_path, changes=changes)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rebind_owned_identity(child, path=path + (index,), changes=changes)
    return changes
