"""Build Siren Protocol with an explicitly supplied, exact Trigger Discipline snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sylvia_gm.application.package_library import validate_admitted_package_runtimes
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.domain.package_library import canonical_json_bytes, validate_portable_path
from sylvia_gm.infrastructure.package_archive import admit_package_archive

SETTING_ROOT = Path(__file__).resolve().parents[1] / "campaign-settings" / "siren-protocol"
SETTING_ID = "pneuma-io.siren-protocol"
RULESET_ID = "pneuma-io.trigger-discipline"


def setting_draft(ruleset: dict, root: Path = SETTING_ROOT) -> NormalizedPackageDraft:
    """Verify the selected source inventory and bind one current format-4 Ruleset."""
    if (ruleset.get("id"), ruleset.get("content_kind"), ruleset.get("role")) != (
        RULESET_ID,
        "ruleset",
        "primary",
    ) or ruleset.get("dependencies") != []:
        raise ValueError("Supply the current standalone Trigger Discipline Ruleset draft")
    manifest_bytes = (root / "source-manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    files = {}
    digest = hashlib.sha256()
    for item in manifest["files"]:
        path = validate_portable_path(item["path"])
        if path in files or not (root / path).resolve().is_relative_to(root.resolve()):
            raise ValueError("Setting source path is repeated or outside its root")
        value = (root / path).read_bytes()
        actual = hashlib.sha256(value).hexdigest()
        if actual != item["sha256"]:
            raise ValueError(
                f"Setting source changed: {path}; review and refresh its source manifest"
            )
        digest.update(path.encode() + b"\0" + bytes.fromhex(actual))
        files[path] = value
    if digest.hexdigest() != manifest["source_sha256"]:
        raise ValueError("Setting source identity is invalid")
    files["source-manifest.json"] = manifest_bytes
    record = json.loads((root / "package-record.json").read_text(encoding="utf-8"))
    record["provenance"]["source_sha256"] = digest.hexdigest()
    record["dependencies"] = [
        {
            "namespace": "ruleset_snapshot",
            "kind": "ruleset_snapshot",
            "id": ruleset["id"],
            "version": ruleset["version"],
        }
    ]
    declarations = json.loads(
        (root / "content/content-declarations.json").read_text(encoding="utf-8")
    )
    declarations["inclusions"][0]["links"]["artifacts"] = sorted(files)
    files["content/content-declarations.json"] = canonical_json_bytes(declarations)
    return NormalizedPackageDraft(record, files, 4)


def compose_siren_protocol_archive(
    ruleset: dict, files: dict[str, bytes], *, root: Path = SETTING_ROOT
):
    """Materialize the same exact closure that ordinary library import and Builder inspect."""
    return materialize_package_archive(
        (
            NormalizedPackageDraft(ruleset, files, 4),
            setting_draft(ruleset, root),
        )
    )


def main() -> None:
    """Write a validated local artifact, with no installation, publication or provider call."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ruleset-draft", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record = json.loads((args.ruleset_draft / "package.json").read_text(encoding="utf-8"))
    files_root = args.ruleset_draft / "files"
    files = {
        path.relative_to(files_root).as_posix(): path.read_bytes()
        for path in sorted(files_root.rglob("*"))
        if path.is_file()
    }
    with compose_siren_protocol_archive(record, files) as artifact:
        with admit_package_archive(artifact.archive) as admitted:
            validate_admitted_package_runtimes(admitted)
        artifact.archive.seek(0)
        value = artifact.archive.read()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # An explicit new destination keeps earlier accepted artifacts intact.
        with args.output.open("xb") as output:
            output.write(value)
        print(f"Siren Protocol: {artifact.closure_sha256}")


if __name__ == "__main__":
    main()
