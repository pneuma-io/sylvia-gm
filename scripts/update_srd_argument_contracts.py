"""Stamp per-spell argument contracts on the SRD Lite casting tools, and version the package.

The two casting tools accept over 500 inputs, and every spell uses a few dozen. This writes a
version-1 ``x-sylvia-gm-argument-contracts`` declaration (see
``sylvia_gm.agent._argument_contracts``) at the root of each tool's input schema in
``package-record.json``: the inputs nearly every spell uses stay in the model-facing envelope,
and each spell lists the rest. ``scripts.srd_lite_argument_contracts`` derives the lists from
the runtime, as a sound over-approximation. The declaration changes only what the model is
shown at once; the executable schema and every check are unchanged.

``derived_from_sha256`` names the runtime sections the lists were derived from;
``tests/test_argument_contracts.py`` fails when they change, so rerun this after any runtime
change (after ``scripts.update_srd_lite_spell_repairs``, before refreshing Plans).

Usage: python -m scripts.update_srd_argument_contracts --version 0.3.27
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LITE = ROOT / "rulesets/srd-lite"
CONTRACT_KEY = "x-sylvia-gm-argument-contracts"
#: An input most spells use stays in the envelope rather than in every spell's list.
COMMON_SHARE = 0.9
SOURCE_SECTIONS = ("tools", "parameter_declarations", "definitions")


def runtime_digest(runtime: dict) -> str:
    """Digest of the runtime sections the contracts are derived from."""
    payload = {section: runtime[section] for section in SOURCE_SECTIONS}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def contracts(inputs: dict[str, dict[str, list[str]]], digest: str) -> dict[str, dict]:
    """One declaration per casting tool from each spell's derived inputs."""
    result = {}
    for tool, by_spell in inputs.items():
        counts = Counter(name for names in by_spell.values() for name in names)
        threshold = COMMON_SHARE * len(by_spell)
        common = sorted(name for name, count in counts.items()
                        if count >= threshold and name != "spell")
        result[tool] = {
            "version": 1,
            "selector": "spell",
            "field": "spell_arguments",
            "common": common,
            "by_value": {spell: sorted(set(names) - set(common) - {"spell"})
                         for spell, names in sorted(by_spell.items())},
            "derived_from_sha256": digest,
        }
    return result


def annotate(record: dict, declarations: dict[str, dict]) -> None:
    tools = {tool["name"]: tool for tool in record["tools"]}
    for name, declaration in declarations.items():
        schema = tools[name]["input_schema"]
        missing = {*declaration["common"],
                   *(n for names in declaration["by_value"].values() for n in names)}
        missing -= set(schema["properties"])
        if missing:
            raise ValueError(f"{name} contract names unknown inputs: {sorted(missing)[:5]}")
        schema[CONTRACT_KEY] = declaration


def update(version: str) -> None:
    import yaml

    from sylvia_gm.application.bundled_packages import _srd_source_drafts
    from sylvia_gm.application.package_materialization import materialize_package_archive

    from .srd_lite_argument_contracts import spell_inputs
    from .update_srd_lite_spell_repairs import _set_catalogue_version, write_catalogue_for

    runtime = yaml.load((LITE / "runtime/ruleset.yaml").read_bytes().decode("utf-8"),
                        Loader=yaml.CSafeLoader)
    declarations = contracts(spell_inputs(runtime), runtime_digest(runtime))
    record_path = LITE / "package-record.json"
    record = json.loads(record_path.read_bytes())
    annotate(record, declarations)
    record["version"] = version
    record_path.write_bytes((json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    identity_path = LITE / "package-identity.json"
    identity = json.loads(identity_path.read_bytes())
    identity["primary"]["version"] = version
    identity_path.write_bytes(
        (json.dumps(identity, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    _set_catalogue_version(version)
    write_catalogue_for(version)
    with materialize_package_archive(_srd_source_drafts()) as artifact:
        with zipfile.ZipFile(artifact.archive) as archive:
            manifests = json.loads(archive.read("manifest.json"))["packages"]
        primary = next(p for p in manifests if p["id"] == record["id"])
        record_path.write_bytes(
            (json.dumps(primary, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        print(json.dumps({
            "version": version,
            "content_sha256": primary["content_sha256"],
            "closure_sha256": artifact.closure_sha256,
            "common": {tool: len(d["common"]) for tool, d in declarations.items()},
        }), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    update(parser.parse_args().version)
