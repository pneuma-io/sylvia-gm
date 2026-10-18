"""Normally admit a preserved 117-spell Lite archive with native movement/reaction integration."""

# This QA CLI pins imports to its isolated checkout.
# ruff: noqa: E402
import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root / "src"), str(root)]

import yaml
from scripts.srd_lite_opportunity_movement import (
    bind_opportunity_movement,
    link_opportunity_movement_content,
)
from scripts.srd_parameter_declarations import compact_runtime
from scripts.srd_runtime_defaults import compact_runtime_defaults
from scripts.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm.application._yaml_bounds import validate_yaml_graph
from sylvia_gm.application.package_library import validate_admitted_package_runtimes
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.infrastructure.package_archive import admit_package_archive
from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations
from sylvia_gm.runtime._schema_validator import validate_ruleset_data

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--input", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--prepared-runtime", type=Path)
args = parser.parse_args()
assert (
    hashlib.sha256(args.input.read_bytes()).hexdigest()
    == "ba790a2b177225ee844e507fc694359caab9bb2f55c00f07085f720502fd78f2"
)
args.output.mkdir(exist_ok=False)
drafts = []
with zipfile.ZipFile(args.input) as package:
    records = json.loads(package.read("manifest.json"))["packages"]
    for record in records:
        item = {
            key: deepcopy(value)
            for key, value in record.items()
            if key not in {"files", "content_sha256"}
        }
        payloads = {
            row["path"]: package.read("packages/" + record["id"] + "/" + row["path"])
            for row in record["files"]
        }
        drafts.append(NormalizedPackageDraft(item, payloads, 4))
primary = next(row for row in drafts if row.record["id"] == "sylvia.srd-lite.bg3")
runtime = _load_runtime(primary.payloads["runtime/ruleset.yaml"].decode("utf-8"))
original = deepcopy(runtime)
spells = deepcopy(runtime["definitions"]["spells"])
headers = deepcopy(runtime["definitions"]["spell_casting"])
assert len(spells) == 117, len(spells)
changed = bind_opportunity_movement(runtime, primary.record)
assert runtime["definitions"]["spells"] == spells
assert runtime["definitions"]["spell_casting"] == headers
assert runtime["definitions"] == original["definitions"]
assert (
    runtime["tools"]["trigger_opportunity_attack"]
    == original["tools"]["trigger_opportunity_attack"]
)
for name, tool in original["tools"].items():
    if name not in changed:
        assert runtime["tools"][name] == tool, "Unrelated native tool changed: " + name
assert not validate_ruleset_data(runtime), validate_ruleset_data(runtime)
runtime.pop("parameter_declarations", None)
default_compacted_sections = []
if args.prepared_runtime is not None:
    raw_runtime = args.prepared_runtime.read_bytes()
    prepared = _load_runtime(raw_runtime.decode("utf-8"))
    prepared.pop("parameter_declarations", None)
    text = raw_runtime.decode("utf-8").replace("\r\n", "\n")
    for name in changed:
        if prepared["tools"].get(name) == runtime["tools"][name]:
            continue
        pattern = rf"(?m)^  {re.escape(name)}:\n.*?(?=^  [a-zA-Z_][a-zA-Z0-9_]*:|^\S|\Z)"
        section = yaml.dump(
            {"tools": {name: runtime["tools"][name]}},
            Dumper=_RuntimeDumper,
            sort_keys=False,
            allow_unicode=True,
            width=100,
        )
        compacted_section = compact_runtime_defaults(section)
        if compacted_section != section:
            default_compacted_sections.append(name)
        section = compacted_section
        assert section.startswith("tools:\n")
        text, replacements = re.subn(
            pattern,
            lambda match, section=section: section[len("tools:\n") :],
            text,
            count=1,
            flags=re.DOTALL,
        )
        if replacements == 0:
            assert name not in prepared["tools"], (
                "Prepared runtime lacks unique tool section: " + name
            )
            boundary = re.search(r"(?m)^\S", text[text.index("tools:\n") + len("tools:\n") :])
            assert boundary is not None, "Prepared runtime lacks its following section boundary"
            position = text.index("tools:\n") + len("tools:\n") + boundary.start()
            text = text[:position] + section[len("tools:\n") :] + text[position:]
        else:
            assert replacements == 1, "Prepared runtime repeats tool section: " + name
    raw_runtime = text.encode("utf-8")
    prepared = _load_runtime(text)
    prepared.pop("parameter_declarations", None)
    assert prepared == runtime, "Prepared runtime differs from this exact source generator output"
else:
    rendered = yaml.dump(
        runtime, Dumper=_RuntimeDumper, sort_keys=False, allow_unicode=True, width=100
    )
    rendered = compact_runtime(compact_runtime_defaults(rendered))
    raw_runtime = rendered.encode("utf-8")
primary.payloads["runtime/ruleset.yaml"] = raw_runtime
declarations = json.loads(primary.payloads[primary.record["content_declarations"]])
link_opportunity_movement_content(declarations)
primary.payloads[primary.record["content_declarations"]] = canonical_json_bytes(declarations)
(args.output / "ruleset.yaml").write_bytes(raw_runtime)
(args.output / "package-record.json").write_text(
    json.dumps(primary.record, indent=2), encoding="utf-8"
)
graph = yaml.compose(raw_runtime, Loader=yaml.CSafeLoader)
validate_yaml_graph(
    graph,
    maximum_depth=64,
    maximum_nodes=500000,
    maximum_object_members=25000,
    count_mapping_keys=False,
)
decoded = yaml.load(raw_runtime, Loader=yaml.CSafeLoader)


def nodes(value):
    if isinstance(value, dict):
        return 1 + sum(nodes(child) for child in value.values())
    if isinstance(value, list):
        return 1 + sum(nodes(child) for child in value)
    return 1


counts = {
    "raw_nodes": nodes(decoded),
    "expanded_nodes": nodes(resolve_parameter_declarations(decoded)),
}
assert counts["expanded_nodes"] <= 500000, counts
print("Materializing owned Lite0.3.4 movement/reaction candidate", flush=True)
with materialize_package_archive(drafts) as materialized:
    data = materialized.archive.read()
    closure = materialized.closure_sha256
candidate = args.output / "SRD-Lite-117-Panels-0.3.4.sylvia-gm-package"
candidate.write_bytes(data)
print("Validating ordinary admission and runtime binding", flush=True)
with admit_package_archive(io.BytesIO(data)) as admitted:
    validate_admitted_package_runtimes(admitted)
result = {
    "input": str(args.input),
    "candidate": str(candidate),
    "package_sha256": hashlib.sha256(data).hexdigest(),
    "closure_sha256": closure,
    "runtime_sha256": hashlib.sha256(raw_runtime).hexdigest(),
    "version": "0.3.4",
    "spells": len(spells),
    "spell_definitions_and_headers_unchanged": True,
    "all_unrelated_tools_unchanged": True,
    "changed_tools": changed,
    "changed_payloads": ["runtime/ruleset.yaml", primary.record["content_declarations"]],
    "counts": counts,
    "normal_admission": True,
    "prepared_runtime_equal_to_generator": args.prepared_runtime is not None,
    "default_compacted_tool_sections": sorted(default_compacted_sections),
    "gameplay_test_status": "not_run",
}
(args.output / "candidate-results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
