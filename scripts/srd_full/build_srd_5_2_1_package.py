from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_RECORD = ROOT / "rulesets" / "dnd-srd-5.2.1" / "package-record.json"
REFERENCE_RECORD = ROOT / "rulesets" / "dnd-srd-5.2.1" / "reference-package-record.json"
IDENTITY_SOURCE = ROOT / "rulesets" / "dnd-srd-5.2.1" / "package-identity.json"
BUILD_STAGES = (
    "build_srd_5_2_1_corpus.py",
    "build_srd_beast_forms.py",
    "build_srd_5_2_1_mechanics.py",
    "build_srd_5_2_1_spells.py",
    "build_srd_5_2_1_advancement.py",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _replace_bytes(path: Path, value: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _identity_records() -> tuple[dict[str, Any], dict[str, Any]]:
    """Read the sole authoring source for both installed SRD package identities."""

    source = json.loads(IDENTITY_SOURCE.read_text(encoding="utf-8"))
    if set(source) != {"format_id", "format_version", "primary", "reference"} or (
        source["format_id"] != "sylvia-gm-package-identity-source" or source["format_version"] != 1
    ):
        raise SystemExit("SRD package identity source is invalid")
    expected = {
        "id",
        "version",
        "role",
        "label",
        "publisher",
        "source_sha256",
        "compatibility",
        "minimum_model_capability_tier",
        "content_declarations",
        "content_kind",
        "dependencies",
    }
    primary = source["primary"]
    reference = source["reference"]
    if not isinstance(primary, dict) or not isinstance(reference, dict):
        raise SystemExit("SRD package identities must be objects")
    if set(primary) != expected or set(reference) != expected:
        raise SystemExit("SRD package identity fields are invalid")
    return primary, reference


def _apply_identity(record: dict[str, Any], identity: dict[str, Any]) -> None:
    """Apply only canonical identity and compatibility fields to generated content."""

    record.update(
        {
            "id": identity["id"],
            "version": identity["version"],
            "role": identity["role"],
            "content_kind": identity["content_kind"],
            "metadata": {"label": identity["label"]},
            "provenance": {
                "publisher": identity["publisher"],
                "source_sha256": identity["source_sha256"],
            },
            "compatibility": identity["compatibility"],
            "minimum_model_capability_tier": identity["minimum_model_capability_tier"],
            "content_declarations": identity["content_declarations"],
            "dependencies": identity["dependencies"],
        }
    )


def main() -> None:
    primary_identity, reference_identity = _identity_records()
    # Child stages derive content only. Give them the canonical primary identity
    # before generation, then finalize both records after every content projection.
    record = json.loads(PACKAGE_RECORD.read_text(encoding="utf-8"))
    _apply_identity(record, primary_identity)
    _replace_bytes(PACKAGE_RECORD, _canonical(record))

    script_root = Path(__file__).resolve().parent
    for stage in BUILD_STAGES:
        # Keep each owning generator in its own process. On Windows, executing
        # all three through repeated ``runpy`` calls can leave the large runtime
        # projection unable to reopen for replacement in the final spell stage.
        subprocess.run(
            [sys.executable, str(script_root / stage)],
            cwd=ROOT,
            check=True,
        )

    # Spell consumers can add parameters to shared tools after their owning stage.
    # Derive public declarations from the final runtime before archive admission.
    subprocess.run(
        [sys.executable, str(script_root / "build_srd_5_2_1_mechanics.py"),
         "--runtime-declarations-only"],
        cwd=ROOT,
        check=True,
    )

    record = json.loads(PACKAGE_RECORD.read_text(encoding="utf-8"))
    _apply_identity(record, primary_identity)
    _replace_bytes(PACKAGE_RECORD, _canonical(record))
    reference_record = json.loads(REFERENCE_RECORD.read_text(encoding="utf-8"))
    _apply_identity(reference_record, reference_identity)
    _replace_bytes(REFERENCE_RECORD, _canonical(reference_record))
    # Check the exact generated archive using normal installation validation. A
    # successful child generator does not prove its output is runnable.
    from sylvia_gm.application.bundled_packages import bundled_srd_archive
    from sylvia_gm.application.package_library import validate_admitted_package_runtimes
    from sylvia_gm.infrastructure.package_archive import admit_package_archive

    with admit_package_archive(io.BytesIO(bundled_srd_archive())) as admitted:
        validate_admitted_package_runtimes(admitted)
    print(
        "Built deterministic SRD 5.2.1 packages "
        f"{primary_identity['version']} + reference {reference_identity['version']}"
    )


if __name__ == "__main__":
    main()
