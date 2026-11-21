"""Generate reference vectors for sylvia_app::srd_lite_character_compatibility.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_srd_lite_compat_vectors.py

`retained_equipment_character_contract` admits one exact SRD Lite 0.3.0 player-character
declaration (`TARGET_SCHEMA`). No 0.3.0 record survives in the repository, so the vectors stand
the bundled SRD player declaration in for it: Python's `TARGET_SCHEMA` is set to that
declaration's actual hash, and the Rust test calls the same check with the same target. Every
early return is exercised by changing one input at a time; the accepted case returns the retained
Lite 0.2 contract, whose fingerprint (`SOURCE_SCHEMA`) both sides check.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application import srd_lite_character_compatibility as compat  # noqa: E402
from sylvia_gm.application.bundled_packages import bundled_rift_participant_declaration  # noqa: E402
from sylvia_gm.domain.authority import PackageLockEntry, PackageRole  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes, participant_schema_sha256  # noqa: E402

OUTPUT = Path(__file__).with_name("srd_lite_compat_vectors.json")


def digest(value) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def project(declaration):
    if declaration is None:
        return None
    return {"kind": declaration.kind, "schema_id": declaration.schema_id, "schema_sha256": declaration.schema_sha256,
            "schema": digest(declaration.schema), "entity_state_schema": digest(declaration.entity_state_schema),
            "initial_state_calculations": digest(list(declaration.initial_state_calculations)),
            "initial_state_validation": digest(declaration.initial_state_validation)}


def main() -> None:
    declaration = bundled_rift_participant_declaration()
    target = participant_schema_sha256(
        declaration.schema, entity_state_schema=declaration.entity_state_schema,
        initial_state_calculations=declaration.initial_state_calculations,
        initial_state_validation=declaration.initial_state_validation)
    compat.TARGET_SCHEMA = target
    declaration = replace(declaration, schema_sha256=target)
    base = {"package": {"id": "sylvia.srd-lite.bg3", "version": "0.3.0", "role": "primary"},
            "declaration": {"kind": declaration.kind, "schema_id": declaration.schema_id, "schema_sha256": "target",
                            "schema": "bundled"},
            "source": {"id": declaration.schema_id, "content_sha256": compat.SOURCE_SCHEMA}}
    changes = [
        {}, {"package": {"id": "pneuma.other"}}, {"package": {"version": "0.3.1"}}, {"package": {"role": "dependency"}},
        {"declaration": {"kind": "companion"}}, {"declaration": {"schema_id": "sylvia.srd-lite.bg3.other"}},
        {"declaration": {"schema_sha256": "0" * 64}}, {"declaration": {"schema": "changed"}},
        {"source": {"id": "x"}}, {"source": {"content_sha256": "1" * 64}}, {"source": {"id": None}},
        {"source": {"missing": True}},
    ]
    cases = []
    for change in changes:
        inputs = {key: {**value, **change.get(key, {})} for key, value in base.items()}
        package = PackageLockEntry(inputs["package"]["id"], inputs["package"]["version"], "a" * 64,
                                   PackageRole(inputs["package"]["role"]), 0, (), ())
        schema = dict(declaration.schema)
        if inputs["declaration"]["schema"] == "changed":
            schema["title"] = "Changed"
        sha = target if inputs["declaration"]["schema_sha256"] == "target" else inputs["declaration"]["schema_sha256"]
        candidate = replace(declaration, kind=inputs["declaration"]["kind"], schema_id=inputs["declaration"]["schema_id"],
                            schema_sha256=sha, schema=schema)
        source = {} if inputs["source"].get("missing") else {"id": inputs["source"]["id"],
                                                              "content_sha256": inputs["source"]["content_sha256"]}
        try:
            result = ["ok", project(compat.retained_equipment_character_contract(package, candidate, source))]
        except Exception as error:  # every class is behaviour
            result = [type(error).__name__, str(error)]
        cases.append({"change": change, "outcome": result})
    vectors = {"python": sys.version.split()[0], "target": target, "base": base, "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(len(cases), "cases;", sum(1 for c in cases if c["outcome"][1] is not None), "accepted")


if __name__ == "__main__":
    main()
