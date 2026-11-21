"""Apply the SRD Lite spell repairs and regenerate the versioned package claims.

Only runtime entries the repairs change are re-rendered; every other authored YAML byte stays.
Run from the repository root: python -m scripts.update_srd_lite_spell_repairs --version X.Y.Z
"""

import argparse
import json
import re
import zipfile
from pathlib import Path

import yaml

from sylvia_gm.application.bundled_packages import _srd_source_drafts
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations

from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_lite_spell_repairs import apply_repairs
from .srd_lite_window_repairs import NEW_PUBLIC_TOOLS
from .update_srd_gm_feedback import _replace_entry, _without_descriptions

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "rulesets/srd-lite"
SECTIONS = ("definition_row_schemas", "definitions", "tools", "parameter_declarations")


def _write_json(path, value):
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _set_catalogue_version(version):
    catalogue = ROOT / "scripts/srd_lite_catalogue.py"
    text = catalogue.read_bytes().decode("utf-8")
    text = re.sub(r'^VARIANT_VERSION = "[^"]+"', f'VARIANT_VERSION = "{version}"', text, flags=re.M)
    catalogue.write_bytes(text.encode("utf-8"))


def update(version):
    path = PACKAGE / "runtime/ruleset.yaml"
    original = path.read_bytes().decode("utf-8")
    runtime = yaml.load(original, Loader=yaml.CSafeLoader)
    before = {
        section: {name: json.dumps(row, sort_keys=True) for name, row in runtime[section].items()}
        for section in SECTIONS
    }
    expanded_before = resolve_parameter_declarations(runtime)
    record_path = PACKAGE / "package-record.json"
    record = json.loads(record_path.read_bytes())
    inputs_before = {
        tool["name"]: _tool_schema(expanded_before["tools"][tool["name"]], tool["input_schema"])
        for tool in record["tools"]
    }
    apply_repairs(runtime)
    result = original
    changed = []
    for section in SECTIONS:
        for name, value in runtime[section].items():
            if json.dumps(value, sort_keys=True) != before[section].get(name):
                result = _replace_entry(result, section, name, value)
                changed.append(f"{section}.{name}")
    if yaml.load(result, Loader=yaml.CSafeLoader) != runtime:
        raise ValueError("Targeted runtime rendering changed unrelated data")
    path.write_bytes(result.encode("utf-8"))

    expanded = resolve_parameter_declarations(runtime)
    for tool in record["tools"]:
        incoming = _tool_schema(expanded["tools"][tool["name"]], tool["input_schema"])
        # Refresh a model-facing schema only when its executable claims changed.
        if _without_descriptions(inputs_before[tool["name"]]) != _without_descriptions(incoming):
            tool["input_schema"] = incoming
    _add_public_tools(record, expanded)
    record["version"] = version
    _write_json(record_path, record)
    identity_path = PACKAGE / "package-identity.json"
    identity = json.loads(identity_path.read_bytes())
    identity["primary"]["version"] = version
    _write_json(identity_path, identity)
    _set_catalogue_version(version)
    write_catalogue_for(version)
    with materialize_package_archive(_srd_source_drafts()) as artifact:
        with zipfile.ZipFile(artifact.archive) as archive:
            manifests = json.loads(archive.read("manifest.json"))["packages"]
        primary = next(p for p in manifests if p["id"] == record["id"])
        _write_json(record_path, primary)
        print(
            json.dumps(
                {
                    "version": version,
                    "content_sha256": primary["content_sha256"],
                    "closure_sha256": artifact.closure_sha256,
                    "changed": changed,
                }
            ),
            flush=True,
        )


def _add_public_tools(record, expanded):
    """Declare each public operation a repair added, beside the operation it follows."""
    declarations_path = PACKAGE / "content/content-declarations.json"
    declarations = json.loads(declarations_path.read_bytes())
    for name, spec in NEW_PUBLIC_TOOLS.items():
        if name not in expanded["tools"]:
            raise ValueError("A declared public operation is missing from the runtime: " + name)
        names = [tool["name"] for tool in record["tools"]]
        if name not in names:
            record["tools"].insert(names.index(spec["after"]) + 1, {
                "capability": spec["capability"],
                "input_schema": _tool_schema(expanded["tools"][name]),
                "name": name,
                "state_predicates": spec["state_predicates"],
                "turn_statuses": spec["turn_statuses"],
                "writers": spec["writers"],
            })
        (inclusion,) = [
            row for row in declarations["inclusions"]
            if spec["after"] in row.get("links", {}).get("tools", ())
        ]
        if name not in inclusion["links"]["tools"]:
            inclusion["links"]["tools"] = sorted([*inclusion["links"]["tools"], name])
    declarations_path.write_bytes(json.dumps(declarations, separators=(",", ":")).encode("utf-8"))


def write_catalogue_for(version):
    """The catalogue module read its version at import; stamp the requested one."""
    from . import srd_lite_catalogue

    srd_lite_catalogue.VARIANT_VERSION = version
    srd_lite_catalogue.write_catalogue()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    update(parser.parse_args().version)
