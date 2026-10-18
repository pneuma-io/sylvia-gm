"""Add branch-owned display captures/cards to the immutable TD 0.1.6 package."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

import yaml

sys.dont_write_bytecode = True
NS = "pneuma-io.trigger-discipline"
INPUT_SHA = "d803d4c3fa9ed4ea893882b45b9a6d5e75c3fb3d11e6341f67223751be94b8aa"
EXISTING = {
    "change_stance",
    "fire_single",
    "fire_burst",
    "fire_full_auto",
    "move_actor",
    "resolve_trigger_crossing",
    "treat_casualty",
}
ACTIVITY = {
    "inspect_trigger_crossing": "Inspect trigger geometry",
    "resolve_blast_snapshot": "Inspect blast geometry",
}
# Every listed scalar is copied only after this invocation writes the native field.
SPECS = {
    "aim": ("last_attack", "outcome", []),
    "autodrone_fire": (
        "last_drone_attack",
        "status",
        [
            "mode",
            "roll",
            "total",
            "outcome",
            "target",
            "cover_id",
            "cover_damage",
            "cover_hp_before",
            "cover_hp_after",
            "material_outcome",
            "target_reached",
            "geometry_revision",
        ],
    ),
    "cycle_weapon": ("last_reload", "status", ["actor", "operation"]),
    "drop_weapon": ("last_equipment", "status", ["actor", "ap_spent", "ground_slot", "weapon"]),
    "end_allies_turn": (None, None, []),
    "end_hostiles_turn": (None, None, []),
    "establish_suppression": ("last_trigger", "status", []),
    "fpv_fragmentation_strike": (
        "last_drone_attack",
        "status",
        [
            "mode",
            "roll",
            "placement_success",
            "target",
            "target_reached",
            "geometry_revision",
            "material_outcome",
            "cover_id",
            "cover_damage",
            "cover_hp_before",
            "cover_hp_after",
        ],
    ),
    "handle_weapon": (
        "last_equipment",
        "status",
        ["actor", "weapon", "previous_location", "location", "ap_spent"],
    ),
    "intrude_network": ("last_network", "status", ["action", "node", "success"]),
    "network_connection": ("last_network", "status", ["action", "node", "success"]),
    "operate_remote_function": (
        "last_network",
        "status",
        ["action", "node", "remote_function", "success", "geometry_revision"],
    ),
    "pickup_weapon": (
        "last_equipment",
        "status",
        ["actor", "ap_spent", "ground_object", "location", "weapon"],
    ),
    "pivot_network": ("last_network", "status", ["action", "node", "success"]),
    "prepare_overwatch": ("last_trigger", "status", []),
    "preserve_network_route": ("last_network", "status", ["action", "node", "success"]),
    "recover_between_encounters": ("last_recovery", "status", ["actor", "restored_res"]),
    "recover_recoil": ("last_attack", "outcome", []),
    "reload_weapon": ("last_reload", "status", ["actor", "operation", "disposition"]),
    "resolve_check": ("last_check", None, ["actor", "roll", "total", "difficulty", "success"]),
    "resolve_cqc": (
        "last_cqc",
        "status",
        ["success", "maneuver", "actor", "target", "roll", "total", "defense"],
    ),
    "resolve_cqc_displacement": (
        "last_cqc",
        "status",
        ["success", "maneuver", "actor", "target", "roll", "total", "defense", "geometry_revision"],
    ),
    "respond_network_defense": (
        "last_defense",
        "status",
        ["success", "actor", "defender", "response"],
    ),
    "switch_weapon": ("last_equipment", "status", ["actor", "ap_spent", "weapon", "stored_weapon"]),
    "transfer_reserve_weapon": (
        "last_equipment",
        "status",
        ["actor", "ap_spent", "recipient", "weapon"],
    ),
}
TITLES = {
    "aim": "Aim",
    "autodrone_fire": "Autonomous drone attack",
    "cycle_weapon": "Cycle firearm",
    "drop_weapon": "Drop firearm",
    "end_allies_turn": "Allied team turn ended",
    "end_hostiles_turn": "Hostile team turn ended",
    "establish_suppression": "Suppression preparation",
    "fpv_fragmentation_strike": "FPV fragmentation strike",
    "handle_weapon": "Firearm handling",
    "intrude_network": "Network check",
    "network_connection": "Network connection",
    "operate_remote_function": "Remote function",
    "pickup_weapon": "Pick up firearm",
    "pivot_network": "Network pivot",
    "prepare_overwatch": "Overwatch preparation",
    "preserve_network_route": "Network route",
    "recover_between_encounters": "RES recovery",
    "recover_recoil": "Recoil recovery",
    "reload_weapon": "Reload firearm",
    "resolve_check": "Check",
    "resolve_cqc": "Close combat",
    "resolve_cqc_displacement": "Close combat displacement",
    "respond_network_defense": "Network defense response",
    "switch_weapon": "Switch firearm",
    "transfer_reserve_weapon": "Transfer reserve firearm",
}
LABELS = {
    "status": "Result",
    "roll": "D20",
    "total": "Total",
    "success": "Succeeded",
    "difficulty": "Difficulty",
    "defense": "Defense",
    "outcome": "Attack outcome",
    "ap_spent": "AP spent",
    "restored_res": "RES restored",
    "target_reached": "Target reached",
    "placement_success": "Placement succeeded",
    "hp_damage": "HP damage applied",
    "res_damage": "RES damage applied",
    "detonation_x": "Detonation X",
    "detonation_y": "Detonation Y",
    "detonation_z": "Detonation Z",
    "aim_ap": "Aim AP",
    "aim_target": "Aim target",
    "zone_id": "Prepared zone",
    "capacity": "Suppression capacity",
    "shots": "Reserved shots",
    "active": "Prepared fire active",
    "recovered": "Recoil recovered",
}


def display(field, value, condition=None):
    row = {"set": {"field": "_card_" + field, "value": value}}
    if condition is not None:
        row["condition"] = condition
    return row


def strip_display(effects):
    result = []
    for node in effects:
        if str(node.get("set", {}).get("field", "")).startswith("_card_"):
            continue
        row = deepcopy(node)
        for branch in ("on_true", "on_false"):
            if branch in row:
                row[branch] = strip_display(row[branch])
        if "for_each" in row:
            row["for_each"]["do"] = strip_display(row["for_each"].get("do", []))
        result.append(row)
    return result


def add_completion(runtime):
    original = deepcopy(runtime)
    assert len(SPECS) == 25
    public = {n for n in runtime["tools"] if not n.startswith("td_")}
    assert public == set(SPECS) | set(ACTIVITY) | EXISTING
    assert {
        n for n in public if runtime["tools"][n].get("metadata", {}).get("result_card")
    } == EXISTING
    changes = {}
    for name, (last, status_key, scalar_keys) in SPECS.items():
        tool = runtime["tools"][name]
        assert "_card_" not in json.dumps(tool["effects"]), name
        actor = next(
            (
                p["name"]
                for p in tool["parameters"]
                if p.get("resolve") == "entity" and p["name"] in ("actor", "attacker")
            ),
            None,
        )
        fields = {"status", "roll", "total", "modifier", "difficulty"} | set(scalar_keys)
        extras = {
            "aim": ["aim_target", "aim_ap"],
            "prepare_overwatch": ["zone_id", "shots", "active"],
            "establish_suppression": ["zone_id", "capacity", "active"],
            "recover_recoil": ["recovered"],
            "autodrone_fire": ["hp_damage", "res_damage"],
            "fpv_fragmentation_strike": [
                "hp_damage",
                "res_damage",
                "total",
                "difficulty",
                "detonation_x",
                "detonation_y",
                "detonation_z",
            ],
            "end_allies_turn": ["next_side", "decision_owner"],
            "end_hostiles_turn": ["next_side", "decision_owner"],
        }.get(name, [])
        fields |= set(extras)
        if actor:
            fields |= {"ap_before", "ap_delta"}
        if name in ("autodrone_fire", "establish_suppression"):
            fields |= {"ammo_before", "ammo_spent", "ammo_remaining"}
        if name == "fpv_fragmentation_strike":
            fields.add("payload_consumed")
        prefix = [display(key, None) for key in sorted(fields)]
        prefix.append(display("status", "'No result recorded'"))
        if actor:
            prefix.append(display("ap_before", actor + ".ap"))
        if name in ("autodrone_fire", "establish_suppression"):
            prefix.append(
                display("ammo_before", actor + ".weapon.magazine.rounds", actor + ".weapon != null")
            )

        def capture_list(
            effects,
            *,
            name=name,
            last=last,
            status_key=status_key,
            scalar_keys=tuple(scalar_keys),
            extras=tuple(extras),
        ):
            result = []
            for source_node in effects:
                node = deepcopy(source_node)
                for branch in ("on_true", "on_false"):
                    if branch in node:
                        node[branch] = capture_list(node[branch])
                if "for_each" in node:
                    node["for_each"]["do"] = capture_list(node["for_each"].get("do", []))
                result.append(node)
                write = node.get("set", {})
                path = str(write.get("field", ""))
                condition = node.get("condition")
                if path == "state.last_rejection":
                    result.append(display("status", write["value"], condition))
                captures = {}
                if last and path == "state." + last and isinstance(write.get("value"), dict):
                    captures = dict(write["value"])
                elif last and path.startswith("state." + last + "."):
                    captures = {path[len("state." + last + ".") :]: write["value"]}
                for key, expression in captures.items():
                    if key == status_key:
                        result.append(display("status", expression, condition))
                    if key in scalar_keys:
                        result.append(display(key, expression, condition))
                    if name == "fpv_fragmentation_strike" and key == "actual_detonation":
                        for index, axis in enumerate("xyz"):
                            result.append(
                                display("detonation_" + axis, expression + f"[{index}]", condition)
                            )
                # A status is authored only inside the branch that actually performs this action.
                if name == "aim" and path == "{_attacker_id}.aim.ap":
                    result += [
                        display("status", "'AIMED'", condition),
                        display("aim_ap", "ap", condition),
                        display("aim_target", "_target_id", condition),
                    ]
                if name in ("prepare_overwatch", "establish_suppression"):
                    prepared = "overwatch" if name == "prepare_overwatch" else "suppression"
                    if path == "state.prepared." + prepared + ".active":
                        result.append(
                            display(
                                "status",
                                repr("PREPARED" if prepared == "overwatch" else "ESTABLISHED"),
                                condition,
                            )
                        )
                    for key in extras:
                        if path == "state.prepared." + prepared + "." + key:
                            result.append(display(key, write["value"], condition))
                if (
                    name == "recover_recoil"
                    and node.get("decrement", {}).get("field")
                    == "{_actor_id}.weapon.current_recoil"
                ):
                    result += [
                        display("status", "'RECOVERED'", condition),
                        display("recovered", "_recovered", condition),
                    ]
                if name == "autodrone_fire" and path == "state.last_drone_attack.mode":
                    result.append(display("status", "'RESOLVED'", condition))
                if name in ("autodrone_fire", "establish_suppression") and (
                    node.get("decrement", {}).get("field") == "{_actor_id}.weapon.magazine.rounds"
                    or path == "{_actor_id}.weapon.magazine.rounds"
                ):
                    result += [
                        display(
                            "ammo_spent",
                            "_card_ammo_before - actor.weapon.magazine.rounds",
                            condition,
                        ),
                        display("ammo_remaining", "actor.weapon.magazine.rounds", condition),
                    ]
                if name == "fpv_fragmentation_strike" and path == "{_drone_id}.payload":
                    result.append(display("payload_consumed", True, condition))
                # Capture damage at actual native HP/RES debits, never from potential damage.
                if name in ("autodrone_fire", "fpv_fragmentation_strike"):
                    lead = "_drone" if name == "autodrone_fire" else "_fragment"
                    target_field = node.get("decrement", {}).get("field")
                    if target_field == "{_target_id}.hp":
                        result.append(
                            display(
                                "hp_damage",
                                lead + "_hp_bypass + " + lead + "_hp_overflow",
                                condition,
                            )
                        )
                    if target_field == "{_target_id}.res":
                        result.append(display("res_damage", lead + "_res_damage", condition))
                if (
                    name in ("intrude_network", "pivot_network")
                    and node.get("store_as") == "_intrusion"
                ):
                    result += [
                        display("roll", "_intrusion.total", condition),
                        display(
                            "total",
                            "_intrusion.total + actor.skills.networking + actor.aptitudes.reason",
                            condition,
                        ),
                        display("difficulty", "_rule.difficulty", condition),
                    ]
                if name == "fpv_fragmentation_strike" and path == "_fpv_total":
                    result.append(display("total", "_fpv_total", condition))
                if name == "fpv_fragmentation_strike" and path == "_fpv_difficulty":
                    result.append(display("difficulty", "_fpv_difficulty", condition))
            return result

        effects = capture_list(tool["effects"])
        if name.startswith("end_"):
            side, decision = (
                ("hostiles", "gm") if name == "end_allies_turn" else ("allies", "player")
            )
            effects += [
                display("status", "'TURN_ENDED'"),
                display("next_side", repr(side)),
                display("decision_owner", repr(decision)),
            ]
        if name == "resolve_check":
            effects.append(display("status", "'PASSED'", "_card_success == true"))
            effects.append(display("status", "'FAILED'", "_card_success == false"))
        if actor:
            effects.append(
                display(
                    "ap_delta",
                    "_card_ap_before - " + actor + ".ap",
                    "_card_ap_before != null and " + actor + ".ap != null",
                )
            )
        effects.append(
            display(
                "modifier", "_card_total - _card_roll", "_card_total != null and _card_roll != null"
            )
        )
        tool["effects"] = prefix + effects

        def row(key, label=None, path=None):
            return {
                "label": label or LABELS.get(key, key.replace("_", " ").capitalize()),
                "optional": True,
                "value": {"path": path or "context._card_" + key},
            }

        summary = [row("status")]
        if actor:
            summary += [
                row("actor", "Character", "parameters." + actor),
                row("ap_delta", "AP spent"),
                row("ap", "AP remaining", "context." + actor + ".ap"),
            ]
        if any(p["name"] == "target" for p in tool["parameters"]):
            summary.append(row("target", "Target", "parameters.target"))
        summary_keys = [
            key for key in ("outcome", "success", "total", "difficulty", "defense") if key in fields
        ]
        summary += [row(key) for key in summary_keys]
        detail_keys = sorted(
            (set(scalar_keys) | set(extras)) - {"actor", "target", "status"} - set(summary_keys)
        )
        details = [row(key) for key in detail_keys]
        if name in ("autodrone_fire", "establish_suppression"):
            details += [
                row("ammo_spent", "Magazine rounds spent"),
                row("ammo_remaining", "Magazine rounds after"),
                row("chamber", "Chamber after", "context.actor.weapon.chamber"),
            ]
        if name == "fpv_fragmentation_strike":
            details.append(row("payload_consumed", "Payload consumed"))
        if actor and name in {
            "cycle_weapon",
            "reload_weapon",
            "handle_weapon",
            "switch_weapon",
            "pickup_weapon",
            "drop_weapon",
            "recover_recoil",
            "transfer_reserve_weapon",
        }:
            details += [
                row("chamber", "Chamber after", "context." + actor + ".weapon.chamber"),
                row(
                    "magazine",
                    "Magazine rounds after",
                    "context." + actor + ".weapon.magazine.rounds",
                ),
                row(
                    "location", "Current firearm location", "context." + actor + ".weapon.location"
                ),
                row("recoil", "Recoil after", "context." + actor + ".weapon.current_recoil"),
            ]
        if name in (
            "autodrone_fire",
            "fpv_fragmentation_strike",
            "resolve_cqc",
            "resolve_cqc_displacement",
        ):
            details += [
                row("hp", "Target HP after", "context.target.hp"),
                row("res", "Target RES after", "context.target.res"),
                row("downed", "Target downed", "context.target.downed"),
            ]
        if name == "recover_between_encounters":
            details += [
                row("res", "RES after", "context.actor.res"),
                row("hp", "HP after", "context.actor.hp"),
            ]
        if name == "fpv_fragmentation_strike":
            details.append(row("drone_active", "Drone active after", "context.drone.active"))
        declaration = {
            "version": 1,
            "title": TITLES[name],
            "summary": summary,
            "details": details,
            "details_label": "Details",
            "footprint": "compact"
            if name
            in {
                "resolve_check",
                "autodrone_fire",
                "fpv_fragmentation_strike",
                "resolve_cqc",
                "resolve_cqc_displacement",
                "intrude_network",
                "pivot_network",
            }
            else "inline",
        }
        declaration["equation"] = {
            "die_sides": 20,
            "optional": True,
            "roll": {"path": "context._card_roll"},
            "modifier": {"path": "context._card_modifier"},
            "total": {"path": "context._card_total"},
        }
        tool.setdefault("metadata", {})["result_card"] = declaration
        assert strip_display(tool["effects"]) == original["tools"][name]["effects"], name
        changes[name] = {
            "native_result": last,
            "status_field": status_key,
            "copied_scalar_fields": scalar_keys,
            "display_fields": sorted(fields),
        }
    for name, label in ACTIVITY.items():
        runtime["tools"][name].setdefault("metadata", {})["activity_label"] = label
    for name, before in original["tools"].items():
        after = runtime["tools"][name]
        assert {k: v for k, v in before.items() if k not in ("metadata", "effects")} == {
            k: v for k, v in after.items() if k not in ("metadata", "effects")
        }
        assert (strip_display(after["effects"]) if name in SPECS else after["effects"]) == before[
            "effects"
        ], name
        old_metadata, new_metadata = (
            deepcopy(before.get("metadata", {})),
            deepcopy(after.get("metadata", {})),
        )
        for key in ("result_card", "activity_label"):
            if name in SPECS or name in ACTIVITY:
                old_metadata.pop(key, None)
                new_metadata.pop(key, None)
        assert old_metadata == new_metadata, name
        if name in EXISTING:
            assert before == after, name
    assert {k: v for k, v in runtime.items() if k != "tools"} == {
        k: v for k, v in original.items() if k != "tools"
    }
    return changes


def build(source_root, source, output):
    assert hashlib.sha256(source.read_bytes()).hexdigest() == INPUT_SHA
    sys.path[:0] = [str(source_root / "src"), str(source_root)]
    from sylvia_gm.application.package_library import validate_admitted_package_runtimes
    from sylvia_gm.application.package_materialization import (
        NormalizedPackageDraft,
        materialize_package_archive,
    )
    from sylvia_gm.infrastructure.package_archive import admit_package_archive

    output.mkdir(parents=True, exist_ok=False)
    drafts = []
    with zipfile.ZipFile(source) as z:
        for original_record in json.loads(z.read("manifest.json"))["packages"]:
            record = {
                k: v for k, v in original_record.items() if k not in ("files", "content_sha256")
            }
            payloads = {
                f["path"]: z.read("packages/" + record["id"] + "/" + f["path"])
                for f in original_record["files"]
            }
            if record["id"] == NS:
                runtime = yaml.safe_load(payloads["runtime/ruleset.yaml"])
                changes = add_completion(runtime)
                payloads["runtime/ruleset.yaml"] = yaml.safe_dump(
                    runtime, sort_keys=False, allow_unicode=True
                ).encode()
                record["version"] = "0.1.7"
            drafts.append(NormalizedPackageDraft(record, payloads, 4))
    with materialize_package_archive(drafts) as package:
        raw = package.archive.read()
    archive = output / "Trigger-Discipline-Panels-0.1.7.sylvia-gm-package"
    archive.write_bytes(raw)
    with admit_package_archive(io.BytesIO(raw)) as admitted:
        validate_admitted_package_runtimes(admitted)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        primary = next(v for v in json.loads(z.read("manifest.json"))["packages"] if v["id"] == NS)
        with zipfile.ZipFile(source) as original:
            assert set(z.namelist()) == set(original.namelist())
            changed_payloads = [
                name
                for name in z.namelist()
                if name != "manifest.json" and z.read(name) != original.read(name)
            ]
            assert changed_payloads == ["packages/" + NS + "/runtime/ruleset.yaml"], (
                changed_payloads
            )
    proof = {
        "input_sha256": INPUT_SHA,
        "archive_sha256": hashlib.sha256(raw).hexdigest(),
        "primary_sha256": primary["content_sha256"],
        "version": "0.1.7",
        "normal_admission": True,
        "display_capture_stripped_native_tree_exact": True,
        "existing_7_tools_and_cards_exact": True,
        "parameters_controls_definitions_exact": True,
        "changed_payload_members": changed_payloads,
        "footprints": {
            name: runtime["tools"][name]["metadata"]["result_card"]["footprint"] for name in SPECS
        },
        "activity_only": ACTIVITY,
        "new_cards": changes,
    }
    (output / "proof.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"archive": str(archive), **{k: v for k, v in proof.items() if k != "new_cards"}}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source_root, args.input, args.output)
