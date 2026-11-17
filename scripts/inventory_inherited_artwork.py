"""Prepare source-bound Severed Lands artwork subjects without generating or changing content."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from sylvia_gm.application.bundled_packages import _rift_drafts

ROOT = Path(__file__).resolve().parents[1]
PATHS = ("runtime/ruleset.yaml", "content/non-beast-form-index.json")


def _source_files() -> Iterator[tuple[str, str, str, bytes]]:
    """Read current and retained admitted-source files, checking each retained file's inventory."""
    ruleset, _, setting = _rift_drafts()
    ruleset_id, setting_id = str(ruleset.record["id"]), str(setting.record["id"])
    for path in PATHS:
        yield ruleset_id, path, "current:" + path, ruleset.payloads[path]
    for archive_path in sorted((ROOT / "campaign-plans").glob("*/setting.sylvia-gm-package")):
        with zipfile.ZipFile(archive_path) as archive:
            records = json.loads(archive.read("manifest.json"))["packages"]
            if not any(record["id"] == setting_id for record in records):
                continue
            primary = next((record for record in records if record["id"] == ruleset_id), None)
            if primary is None:
                raise ValueError(
                    f"Retained Setting has no current Lite source: {archive_path.name}"
                )
            files = {item["path"]: item for item in primary["files"]}
            for path in PATHS:
                if path not in files:
                    continue  # Older sources only bind subjects they actually declare.
                member = f"packages/{ruleset_id}/{path}"
                raw = archive.read(member)
                if files[path]["sha256"] != hashlib.sha256(raw).hexdigest() or files[path][
                    "bytes"
                ] != len(raw):
                    raise ValueError(f"Retained artwork source checksum differs: {member}")
                origin = archive_path.relative_to(ROOT).as_posix() + "!" + member
                yield ruleset_id, path, origin, raw


def inherited_artwork_subjects() -> dict[str, Any]:
    """Group stable source identities across editions, preserving their exact bindings."""
    _, _, setting = _rift_drafts()
    setting_id = str(setting.record["id"])
    subjects: dict[str, dict[str, Any]] = {}
    source_files: dict[tuple[str, str, str], dict[str, Any]] = {}
    for package_id, path, origin, raw in _source_files():
        digest = hashlib.sha256(raw).hexdigest()
        key = (package_id, path, digest)
        if key in source_files:
            source_files[key]["origins"].append(origin)
            continue
        source_files[key] = {
            "package_id": package_id,
            "path": path,
            "sha256": digest,
            "byte_count": len(raw),
            "origins": [origin],
        }
        if path.endswith(".yaml"):
            table = yaml.safe_load(raw)["definitions"]["beast_forms"]
            prefix, kind = "/definitions/beast_forms/", "beast"
        else:
            table = json.loads(raw)["forms"]
            prefix, kind = "/forms/", "non_beast"
        for identity, value in sorted(table.items()):
            if not isinstance(identity, str) or not isinstance(value, dict):
                raise ValueError("Inherited artwork requires named form records")
            name = value.get("name") or identity.replace("_", " ").title()
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Inherited artwork source name is invalid")
            subject = subjects.setdefault(
                identity,
                {
                    "id": "severed-inherited-" + identity,
                    "source_id": identity,
                    "name": name,
                    "kind": kind,
                    "physical_facts": {
                        key: value[key]
                        for key in (
                            "creature_type",
                            "size_rank",
                            "appearance",
                            "description",
                        )
                        if key in value
                    },
                    "sources": [],
                },
            )
            if subject["name"] != name:
                raise ValueError(
                    f"Review changed inherited identity before sharing artwork: {identity}"
                )
            token = identity.replace("~", "~0").replace("/", "~1")
            subject["sources"].append(
                {
                    "package_id": package_id,
                    "path": path + "#" + prefix + token,
                    "sha256": digest,
                    "campaign_setting_id": setting_id,
                }
            )
    return {
        "format_id": "sylvia-gm-inherited-artwork-subjects",
        "format_version": 1,
        "campaign_setting_id": setting_id,
        "subjects": [subjects[identity] for identity in sorted(subjects)],
        "source_files": [source_files[key] for key in sorted(source_files)],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the source inventory JSON to this path")
    args = parser.parse_args()
    inventory = inherited_artwork_subjects()
    encoded = json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8", newline="\n")
        print(
            json.dumps(
                {
                    "subjects": len(inventory["subjects"]),
                    "source_files": len(inventory["source_files"]),
                }
            )
        )


if __name__ == "__main__":
    main()
