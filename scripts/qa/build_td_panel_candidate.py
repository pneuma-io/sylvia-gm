# This standalone QA CLI binds imports to its own isolated checkout before admission.
# ruff: noqa: E402
import hashlib
import io
import json
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

import yaml

sys.dont_write_bytecode = True
import argparse

r = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(r / "src"), str(r)]
parser = argparse.ArgumentParser(
    description="Build native TD panel metadata and authored low-edge fixtures."
)
parser.add_argument("--input", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
options = parser.parse_args()
source = options.input
out = options.output
out.mkdir(exist_ok=False)
from sylvia_gm.application.package_library import validate_admitted_package_runtimes
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.infrastructure.package_archive import admit_package_archive

with zipfile.ZipFile(source) as z:
    records = json.loads(z.read("manifest.json"))["packages"]
    drafts = []
    for row in records:
        record = {k: v for k, v in row.items() if k not in ["files", "content_sha256"]}
        payloads = {
            f["path"]: z.read("packages/" + row["id"] + "/" + f["path"]) for f in row["files"]
        }
        drafts.append((record, payloads))
record, payloads = next(row for row in drafts if row[0]["id"] == "pneuma-io.trigger-discipline")
runtime = yaml.safe_load(payloads["runtime/ruleset.yaml"])
original = deepcopy(runtime)


def nodes(effects):
    for node in effects:
        yield node
        for key in ["on_true", "on_false"]:
            yield from nodes(node.get(key, []))
        if "for_each" in node:
            yield from nodes(node["for_each"].get("do", []))


prices = {}
names = {a["intent"] for s in record["ui_surfaces"] for a in s["actions"]}
for name in sorted(names):
    tool = runtime["tools"][name]
    policy = tool.setdefault("metadata", {}).setdefault("player_controls", {})
    debits = [
        n["decrement"]["amount"]
        for n in nodes(tool["effects"])
        if "decrement" in n and str(n["decrement"]["field"]).endswith(".ap")
    ]
    if not debits:
        continue
    assert len(set(map(str, debits))) == 1, (name, debits)
    expression = str(debits[0])
    source_expression = expression
    if expression == "_fire_ap":
        assignments = [
            n["set"]["value"]
            for n in nodes(tool["effects"])
            if n.get("set", {}).get("field") == expression
        ]
        assert len(assignments) == 1
        expression = assignments[0]
    elif expression == "_rule.ap":
        # Select the same native rule object for INTRUDE and reentry branches.
        assignments = [
            n["set"]["value"]
            for n in nodes(tool["effects"])
            if n.get("set", {}).get("field") == "_rule" and isinstance(n["set"]["value"], str)
        ]
        assert len(assignments) == 2
        expression = f"{assignments[0]}.ap if method == 'INTRUDE' else {assignments[1]}.ap"
    if expression == "_function.ap":
        assignments = [
            n["set"]["value"]
            for n in nodes(tool["effects"])
            if n.get("set", {}).get("field") == "_function" and isinstance(n["set"]["value"], str)
        ]
        assert len(assignments) == 1
        expression = assignments[0] + ".ap"
    if name == "network_connection":
        expression = f"{expression} if action == 'CONNECT' else 0"
    existing = policy.setdefault("calculations", [])
    if expression == "_move_ap":
        assert any(c.get("set", {}).get("field") == "_move_ap" for c in existing)
        field = "_move_ap"
    elif name in ["intrude_network", "pivot_network"]:
        field = "_preview_ap_cost"
        expression = assignments[0] + ".ap"
        existing.append(
            {"condition": "method == 'INTRUDE'", "set": {"field": field, "value": expression}}
        )
        existing.append(
            {
                "condition": "method != 'INTRUDE'",
                "set": {"field": "_preview_reentry_ap_cost", "value": assignments[1] + ".ap"},
            }
        )
    elif name == "network_connection":
        field = "_preview_ap_cost"
        expression = source_expression
        existing.append(
            {"condition": "action == 'CONNECT'", "set": {"field": field, "value": expression}}
        )
        existing.append(
            {
                "condition": "action == 'DISCONNECT'",
                "set": {"field": "_preview_disconnect_ap_cost", "value": "0"},
            }
        )
    else:
        field = "_preview_ap_cost"
        existing.append({"set": {"field": field, "value": expression}})
    assert len(existing) <= 24, (name, len(existing))
    actor_parameter = policy.get("actor_parameter") or next(
        (param["name"] for param in tool["parameters"] if param.get("resolve") == "entity"), None
    )
    if actor_parameter:
        policy["actor_parameter"] = actor_parameter
    if not policy.get("checks") and actor_parameter:
        policy["checks"] = [
            {
                "condition": f"{actor_parameter}.side == active_side",
                "reason": "This character cannot act during the other side’s turn.",
            },
            {
                "condition": f"{actor_parameter}.ap >= {field}",
                "reason": "This character does not have enough action points.",
            },
        ]
    preview = policy.setdefault("preview", {"version": 1, "title": "Action preview", "summary": []})
    preview["summary"] = [
        row for row in preview["summary"] if row.get("label") not in ["AP cost", "Estimated AP"]
    ]
    preview["summary"].append(
        {
            "label": "AP cost",
            "optional": expression == "_move_ap"
            or name in ["intrude_network", "pivot_network", "network_connection"],
            "value": {"path": "context." + field},
        }
    )
    if name in ["intrude_network", "pivot_network"]:
        preview["summary"].append(
            {
                "label": "AP cost",
                "optional": True,
                "value": {"path": "context._preview_reentry_ap_cost"},
            }
        )
    if name == "network_connection":
        preview["summary"].append(
            {
                "label": "AP cost",
                "optional": True,
                "value": {"path": "context._preview_disconnect_ap_cost"},
            }
        )
    for row in preview["summary"]:
        if row.get("label") == "Actor":
            row["label"] = "Character"
    prices[name] = {
        "native_debit_expression": source_expression,
        "preview_expression": expression,
        "context_field": field,
    }
# No execution body, parameter, definition, configuration or native collision code is changed.
for name, tool in runtime["tools"].items():
    for key in set(tool) | set(original["tools"][name]):
        if key != "metadata":
            assert tool.get(key) == original["tools"][name].get(key), (name, key)
assert runtime["definitions"] == original["definitions"]
payloads["runtime/ruleset.yaml"] = yaml.safe_dump(
    runtime, sort_keys=False, allow_unicode=True
).encode()
record["version"] = "0.1.6"
# Authored package scenario, not a renderer-only cloned horizontal object.
scenario = json.loads(payloads["conformance/scenario.json"])
authored = deepcopy(scenario)
ns = "pneuma-io.trigger-discipline"
state = authored["initial_state"][ns + ".state"]
scene = state["spatial_scene"]
scene["map"]["id"] = "td-authored-low-edge-yard"
scene["map"]["revision"] = 1


def remap(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "map_id" and item == "td-conformance-floor":
                value[key] = scene["map"]["id"]
            else:
                remap(item)
    elif isinstance(value, list):
        for item in value:
            remap(item)


remap(state)
for category in ["geometry", "entities", "zones"]:
    for item in scene[category]:
        item["map_id"] = scene["map"]["id"]
counter = next(g for g in scene["geometry"] if g["id"] == "partial-cover")
counter["metadata"]["player"]["label"] = "Occupied solid counter"
counter["volume"] = {"kind": "box", "min": [14, 6, 0], "max": [14.5, 9, 1]}
presets = {
    v["id"]: v["height_m"]
    for v in runtime["definitions"]["authoring_defaults"]["cover_height_presets"]
}


def edge(identity, label, low, high, height):
    item = deepcopy(counter)
    item["id"] = identity
    item["occupies_cells"] = False
    item["volume"] = {"kind": "box", "min": [*low, 0], "max": [*high, height]}
    item["metadata"]["player"].update(
        label=label,
        kind="cover",
        height_label="Quarter" if height == presets["quarter"] else "Half",
    )
    return item


scene["geometry"] += [
    edge("quarter-edge-vertical", "Quarter edge cover", [6, 1], [6.08, 4], presets["quarter"]),
    edge("half-edge-horizontal", "Half edge cover", [3, 6], [8, 6.08], presets["half"]),
]
crate = deepcopy(counter)
crate["id"] = "occupied-crate"
crate["volume"] = {"kind": "box", "min": [10, 6, 0], "max": [11, 7, presets["half"]]}
crate["metadata"]["player"].update(label="Occupied crate", kind="cover", height_label="Half")
scene["geometry"].append(crate)
payloads["conformance/panel-edge-combat.json"] = json.dumps(authored, indent=2).encode()
# Link the new owned conformance artifact through ordinary content admission.
declarations = json.loads(payloads[record["content_declarations"]])
link = next(
    v["links"]
    for v in declarations["inclusions"]
    if "conformance/scenario.json" in v["links"].get("artifacts", [])
)
link["artifacts"] = sorted(set(link["artifacts"]) | {"conformance/panel-edge-combat.json"})
payloads[record["content_declarations"]] = json.dumps(
    declarations, sort_keys=True, separators=(",", ":")
).encode()
proof = {
    "original_archive_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "native_execution_bodies_and_definitions_exact": True,
    "costs": prices,
    "old_fixture_preserved": True,
    "authored_edge_scenario": "conformance/panel-edge-combat.json",
    "heights_from_native_presets": presets,
    "version": "0.1.6",
}
with materialize_package_archive([NormalizedPackageDraft(record, payloads, 4)]) as m:
    raw = m.archive.read()
    primary = m.manifests[0]
    proof["primary_sha256"] = (
        primary.content_sha256 if hasattr(primary, "content_sha256") else primary.identity[2]
    )
archive = out / "Trigger-Discipline-Panels-0.1.6.sylvia-gm-package"
archive.write_bytes(raw)
with admit_package_archive(io.BytesIO(raw)) as admitted:
    validate_admitted_package_runtimes(admitted)
(out / "proof.json").write_text(json.dumps(proof, indent=2))
(out / "authored-scenario.json").write_text(json.dumps(authored, indent=2))
print(
    json.dumps(
        {
            "normal_admission": True,
            "output": str(archive),
            "cost_tools": len(prices),
            "native_bodies_exact": True,
            "primary_sha256": proof["primary_sha256"],
        }
    )
)
