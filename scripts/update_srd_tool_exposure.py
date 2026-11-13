"""Annotate SRD Lite tools with when the GM model is shown them, and version the package.

Two presentation-only annotations are stamped into ``package-record.json``. Neither changes
execution, admission or schema validation; see ``sylvia_gm.agent._tool_exposure``.

* ``x-sylvia-gm-spell-scope`` on a casting tool's ``spell`` input: the spells that tool can run.
  The model is shown only castable spells inside that scope; a tool with none left is not listed.
* ``x-sylvia-gm-exposure`` on a follow-up tool: conditions, any one of which makes it relevant.

Each entry below cites the refusal or rule it relies on. A tool is only conditioned when it
cannot succeed, or has no effect, while every condition is false. Tools not listed are always
shown. ``tests/test_gm_tool_exposure.py`` checks this table against the package and runtime.

Usage: python -m scripts.update_srd_tool_exposure --version 0.3.24
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

from sylvia_gm.application.bundled_packages import _srd_source_drafts
from sylvia_gm.application.package_materialization import materialize_package_archive

ROOT = Path(__file__).resolve().parents[1]
LITE = ROOT / "rulesets/srd-lite"

# Spells SRD Lite cannot cast. The casting tools refuse any spell outside
# ``definitions.srd_lite_implemented`` ("This SRD Lite profile is pending"), so an effect of
# these spells can never exist in a Lite campaign. Their follow-up tools stay listed only if a
# saved creature somehow lists the spell, which over-shows rather than hides.
NOT_IN_LITE = "Spell is outside SRD Lite; every casting tool refuses it, so no effect can exist."

SPELL_SCOPES: dict[str, dict[str, object]] = {
    "begin_srd_spell_action": {
        "casting_times": ["action", "bonus_action", "reaction"],
        "reason": (
            "Top-level require: casting_mode == 'ordinary' and casting time is action, "
            "bonus_action or reaction ('This window begins an Action, Bonus Action or "
            "Reaction spell.')."
        ),
    },
    "begin_srd_after_hit_smite": {
        "spells": ["divine_smite", "searing_smite", "shining_smite", "ensnaring_strike"],
        "reason": (
            "Top-level require: spell is divine_smite, searing_smite, shining_smite or "
            "ensnaring_strike ('Select a supported paid after-hit rider for this final weapon "
            "hit.')."
        ),
    },
    "cast_srd_reaction_spell": {
        "casting_times": ["reaction"],
        "reason": (
            "The tool casts 'a prepared Reaction spell against an actual saved trigger'. Only "
            "Reaction spells have a casting time that a trigger can start. The runtime does not "
            "refuse an Action spell here today; that gap is recorded in the PR, and this scope "
            "keeps the model to the rule."
        ),
    },
}


def _spells(*ids: str) -> dict[str, object]:
    return {"spells": list(ids)}


def _field(path: str, op: str) -> dict[str, object]:
    return {"entity_field": path, "op": op}


STEED = (
    "Requires an Otherworldly Steed (steed_state.created_caster_state_id), made only by "
    "Find Steed."
)
NATIVE_UNDEAD = (
    "Requires _animated_slam_record.spell == 'create_undead'; Create Undead is outside SRD Lite."
)

EXPOSURE: dict[str, dict[str, object]] = {
    # Otherworldly Steed (Find Steed).
    **{
        name: {"any_of": [_spells("find_steed")], "reason": STEED}
        for name in (
            "begin_srd_controlled_steed_phase",
            "begin_srd_otherworldly_forced_movement",
            "dismount_srd_otherworldly_steed",
            "establish_srd_lite_steed_healing_proximity",
            "mount_srd_otherworldly_steed",
            "pause_srd_controlled_steed_phase",
            "record_srd_lite_steed_disappearance_facts",
            "resolve_srd_otherworldly_mount_balance",
            "take_srd_otherworldly_steed_action",
            "use_srd_steed_fell_glare",
            "use_srd_steed_fey_step",
            "use_srd_steed_healing_touch",
        )
    },
    # Creatures raised by Create Undead.
    **{
        name: {"any_of": [_spells("create_undead")], "reason": NATIVE_UNDEAD}
        for name in (
            "admit_srd_native_wight_rise_language",
            "check_srd_native_wight_ability",
            "record_srd_native_wight_body_destruction",
            "use_srd_native_mummy_glare",
            "use_srd_native_wight_life_drain",
        )
    },
    # Class features.
    "use_srd_second_wind": {
        "any_of": [_field("fighter_level", "gte1")],
        "reason": (
            "Pre-condition: actor_state.fighter_level >= 1 ('no authoritative Fighter level'). "
            "Every character saves a second_wind resource, so the level identifies a Fighter; "
            "an exhausted Fighter still gets the engine's 'no remaining use' refusal."
        ),
    },
    "use_monk_wholeness_of_body": {
        "any_of": [_field("resources.wholeness_of_body", "not_null")],
        "reason": (
            "Pre-condition: actor_state.resources.wholeness_of_body >= 1. Shown while the "
            "resource exists, so an exhausted Monk gets the engine's refusal."
        ),
    },
    # Active effects of SRD Lite spells.
    "attack_srd_altered_natural_weapon": {
        "any_of": [_spells("alter_self"), _field("form_current_source_id", "not_null")],
        "reason": "Requires the active Alter Self growth (actor_state.form_current_source_id).",
    },
    "attack_srd_transformed_form": {
        "any_of": [_spells("polymorph"), _field("form_current_source_id", "not_null")],
        "reason": "Requires actor_state.form_statistics_replaced and form_current_source_id.",
    },
    "read_srd_transformed_appearance": {
        "any_of": [
            _spells("polymorph", "alter_self"),
            _field("form_current_source_id", "not_null"),
        ],
        "reason": "Reads an actual effective form, which only a transformation source creates.",
    },
    "collect_srd_irresistible_dance": {
        "any_of": [_spells("irresistible_dance"), _field("control_dance_active", "true")],
        "reason": (
            "Requires actor_state.control_dance_active; Irresistible Dance is outside SRD Lite."
        ),
    },
    "command_srd_summoned_creature": {
        "any_of": [_field("summon_active", "true")],
        "reason": "Requires summon_state.summon_active == true.",
    },
    "escape_srd_nonmagical_restraint": {
        "any_of": [_spells("freedom_of_movement"), _field("spell_freedom_of_movement", "true")],
        "reason": "Requires actor_state.spell_freedom_of_movement == true.",
    },
    "escape_srd_web": {
        "any_of": [_spells("web")],
        "reason": "Requires an installed source with spell_id == 'web'.",
    },
    "use_srd_dragon_breath": {
        "any_of": [_spells("dragons_breath")],
        "reason": "'No active paid Dragon's Breath source is available.'",
    },
    "violate_srd_geas": {
        "any_of": [_spells("geas")],
        "reason": "Requires an installed order with control_profile == 'geas'.",
    },
    "speak_srd_truth_zone": {
        "any_of": [_spells("zone_of_truth")],
        "reason": "Speaks inside an actual truth zone, created only by Zone of Truth.",
    },
    "read_srd_truth_zone_outcomes": {
        "any_of": [_spells("zone_of_truth")],
        "reason": "Requires a known source with profile == 'truth_zone'.",
    },
    "transfer_srd_attack_mark": {
        "any_of": [_spells("hex", "hunters_mark")],
        "reason": "Requires a live rider source whose spell is hex or hunters_mark.",
    },
    "traverse_srd_tree_stride": {
        "any_of": [_spells("tree_stride")],
        "reason": "Requires actor_state.concentration_spell_id == 'tree_stride'.",
    },
    "disperse_srd_fog": {
        "any_of": [_spells("fog_cloud", "stinking_cloud")],
        "reason": "'Dispersal requires a selected Fog Cloud or Stinking Cloud source.'",
    },
    "query_srd_thunderwave_sound": {
        "any_of": [_spells("thunderwave")],
        "reason": "'There is no recorded Thunderwave boom in this scene.'",
    },
    "observe_srd_information_proxy": {
        "any_of": [_spells("arcane_eye")],
        "reason": "'This sensor observation requires the selected Arcane Eye source.'",
    },
    "operate_srd_information_proxy": {
        "any_of": [_spells("arcane_eye", "clairvoyance")],
        "reason": (
            "Moves Arcane Eye or switches Clairvoyance's sense; Clairvoyance is outside SRD Lite."
        ),
    },
    "resolve_srd_fear_turn": {
        "any_of": [_spells("fear"), _field("control_behavior_source_ids", "not_null")],
        "reason": "Requires actor_state.control_behavior_source_ids (an active Fear source).",
    },
    "shake_srd_sleeping_creature": {
        "any_of": [_spells("sleep")],
        "reason": "Shakes away selected Sleep sources.",
    },
    "choose_srd_chromatic_orb_leap": {
        "any_of": [_spells("chromatic_orb")],
        "reason": "Chooses Chromatic Orb's earned leap in its original paid casting.",
    },
    "control_srd_mage_hand": {
        "any_of": [_spells("mage_hand")],
        "reason": "Moves the saved spectral hand that Mage Hand creates.",
    },
    "dismiss_srd_etherealness": {
        "any_of": [_spells("etherealness")],
        "reason": "Dismisses this caster's ongoing Etherealness.",
    },
    "dismiss_srd_illumination_spell": {
        "any_of": [_spells("aura_of_life", "daylight", "fire_shield", "light", "wall_of_fire")],
        "reason": "The dismissal code knows only these illumination spells.",
    },
    "ask_srd_divination_question": {
        "any_of": [_spells("commune", "contact_other_plane")],
        "reason": (
            "Asks within a divination's source-owned question count (Commune, Contact Other "
            "Plane)."
        ),
    },
    "inspect_srd_minor_world_effects": {
        "any_of": [_spells("thaumaturgy")],
        "reason": "Reads active minor world sources, which Thaumaturgy creates.",
    },
    "resolve_srd_control_order": {
        "any_of": [_spells("command", "suggestion", "geas", "charm_monster")],
        "reason": "Performs a saved control order (Command and other control spells).",
    },
    "resolve_srd_control_activity": {
        "any_of": [_spells("command", "suggestion", "geas", "charm_monster")],
        "reason": "Spends a saved control activity (Suggestion and other control spells).",
    },
    "begin_srd_scene_fall": {
        "any_of": [_spells("feather_fall")],
        "reason": "Opens a Feather Fall Reaction window. " + NOT_IN_LITE,
    },
    # Fact records read only by spells outside SRD Lite.
    "record_srd_magic_jar_container": {"any_of": [_spells("magic_jar")], "reason": NOT_IN_LITE},
    "record_srd_secret_chest_components": {
        "any_of": [_spells("secret_chest")],
        "reason": NOT_IN_LITE,
    },
    "record_srd_body_vessel": {"any_of": [_spells("clone")], "reason": NOT_IN_LITE},
    "record_srd_imprisonment_statuette": {
        "any_of": [_spells("imprisonment")],
        "reason": NOT_IN_LITE,
    },
    "record_srd_teleport_destination": {"any_of": [_spells("teleport")], "reason": NOT_IN_LITE},
    "record_srd_teleport_similar_area": {"any_of": [_spells("teleport")], "reason": NOT_IN_LITE},
    "record_srd_find_path_location": {"any_of": [_spells("find_the_path")], "reason": NOT_IN_LITE},
    "record_srd_find_path_routes": {"any_of": [_spells("find_the_path")], "reason": NOT_IN_LITE},
    "record_srd_demiplane_room_knowledge": {
        "any_of": [_spells("demiplane")],
        "reason": NOT_IN_LITE,
    },
    "record_srd_banishment_plane": {"any_of": [_spells("banishment")], "reason": NOT_IN_LITE},
    "record_srd_contingency_world_fact": {
        "any_of": [_spells("contingency")],
        "reason": NOT_IN_LITE,
    },
    "record_srd_reverse_gravity_anchoring": {
        "any_of": [_spells("reverse_gravity")],
        "reason": NOT_IN_LITE,
    },
    "record_srd_levitate_object_looseness": {
        "any_of": [_spells("levitate")],
        "reason": NOT_IN_LITE,
    },
    "record_srd_levitate_surface_grip": {"any_of": [_spells("levitate")], "reason": NOT_IN_LITE},
    "record_srd_telekinesis_object_size": {
        "any_of": [_spells("telekinesis")],
        "reason": NOT_IN_LITE,
    },
}


def _scope_spells(
    entry: dict[str, object], definitions: dict[str, object], declared: list[str]
) -> list[str]:
    """Keep every declared choice except implemented spells this tool always refuses.

    Spells outside SRD Lite stay, so a creature that lists one still receives the engine's
    explicit "excluded" refusal instead of a schema error.
    """
    implemented = set(definitions["srd_lite_implemented"])
    spells = definitions["spells"]
    if "spells" in entry:
        accepted = set(entry["spells"])  # type: ignore[arg-type]
    else:
        times = set(entry["casting_times"])  # type: ignore[arg-type]
        accepted = {
            spell
            for spell in implemented
            if isinstance(spells.get(spell), dict) and spells[spell].get("casting_time") in times
        }
    return sorted(
        spell for spell in declared if spell not in implemented or spell in accepted
    )


def annotate(record: dict, definitions: dict) -> dict[str, int]:
    """Stamp both annotations into *record* in place; return a count of each."""

    tools = {tool["name"]: tool for tool in record["tools"]}
    missing = sorted((set(SPELL_SCOPES) | set(EXPOSURE)) - set(tools))
    if missing:
        raise ValueError(f"Exposure table names unknown tools: {missing}")
    for tool in record["tools"]:
        schema = tool["input_schema"]
        schema.pop("x-sylvia-gm-exposure", None)
        spell = schema["properties"].get("spell")
        if isinstance(spell, dict):
            spell.pop("x-sylvia-gm-spell-scope", None)
    for name, entry in SPELL_SCOPES.items():
        spell = tools[name]["input_schema"]["properties"]["spell"]
        if spell.get("x-sylvia-gm-castable-spells") is not True:
            raise ValueError(f"{name} has no castable spell choice to scope")
        aliases = spell.get("x-sylvia-gm-value-aliases", {})
        declared = [value for value in spell["enum"] if value not in aliases]
        spell["x-sylvia-gm-spell-scope"] = _scope_spells(entry, definitions, declared)
    for name, entry in EXPOSURE.items():
        tools[name]["input_schema"]["x-sylvia-gm-exposure"] = {"any_of": entry["any_of"]}
    return {"spell_scopes": len(SPELL_SCOPES), "conditioned_tools": len(EXPOSURE)}


def update(version: str) -> None:
    import yaml

    record_path = LITE / "package-record.json"
    record = json.loads(record_path.read_bytes())
    runtime = yaml.load((LITE / "runtime/ruleset.yaml").read_text(encoding="utf-8"),
                        Loader=yaml.CSafeLoader)
    counts = annotate(record, runtime["definitions"])
    record["version"] = version
    record_path.write_bytes((json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    identity_path = LITE / "package-identity.json"
    identity = json.loads(identity_path.read_bytes())
    identity["primary"]["version"] = version
    identity_path.write_bytes(
        (json.dumps(identity, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    with materialize_package_archive(_srd_source_drafts()) as artifact:
        with zipfile.ZipFile(artifact.archive) as archive:
            manifests = json.loads(archive.read("manifest.json"))["packages"]
        primary = next(p for p in manifests if p["id"] == record["id"])
        record_path.write_bytes(
            (json.dumps(primary, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        print(json.dumps({
            "version": version,
            "content_sha256": primary["content_sha256"],
            "closure_sha256": artifact.closure_sha256,
            **counts,
        }), flush=True)
    catalogue = ROOT / "scripts/srd_lite_catalogue.py"
    text = catalogue.read_text(encoding="utf-8")
    text = re.sub(r'^VARIANT_VERSION = "[^"]+"', f'VARIANT_VERSION = "{version}"', text, flags=re.M)
    catalogue.write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    update(parser.parse_args().version)
