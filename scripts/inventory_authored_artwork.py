"""Prepare authored Severed Lands cast sources, keeping retained editions separate for review."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {"npcs/lace-brothers.md": ("Thread", "Hook", "Needle")}
GUIDE = "content/monsters/rift-awakening-opposition.md"


def _is_cast_path(path: str) -> bool:
    return path.endswith(".md") and path.startswith(("npcs/", "content/monsters/"))


def _source_documents() -> Iterator[tuple[str, str, str, bytes]]:
    """Read current authored files and verify retained source members against their manifests."""
    world = ROOT / "campaign-settings/severed-lands"
    package_id = str(json.loads((world / "package-record.json").read_bytes())["id"])
    for folder in ("npcs", "content/monsters"):
        for path in sorted((world / folder).glob("*.md")):
            yield (
                package_id,
                path.relative_to(world).as_posix(),
                (path.relative_to(ROOT).as_posix()),
                path.read_bytes(),
            )
    for archive_path in sorted((ROOT / "campaign-plans").glob("*/setting.sylvia-gm-package")):
        with zipfile.ZipFile(archive_path) as archive:
            records = json.loads(archive.read("manifest.json"))["packages"]
            record = next((item for item in records if item["id"] == package_id), None)
            if record is None:
                continue
            for item in record["files"]:
                path = item["path"]
                if not _is_cast_path(path):
                    continue
                member = f"packages/{package_id}/{path}"
                raw = archive.read(member)
                if hashlib.sha256(raw).hexdigest() != item["sha256"] or len(raw) != item["bytes"]:
                    raise ValueError(f"Retained cast source checksum differs: {member}")
                yield (
                    package_id,
                    path,
                    (archive_path.relative_to(ROOT).as_posix() + "!" + member),
                    raw,
                )


def authored_artwork_subjects() -> dict[str, Any]:
    """Expand declared groups and retain all exact source editions without inventing appearances."""
    sources: dict[tuple[str, str, str], dict[str, Any]] = {}
    for package_id, path, origin, raw in _source_documents():
        normalized = raw.replace(b"\r\n", b"\n")
        digest = hashlib.sha256(normalized).hexdigest()
        key = (package_id, path, digest)
        if key not in sources:
            sources[key] = {
                "package_id": package_id,
                "path": path,
                "sha256": digest,
                "document": normalized.decode("utf-8"),
                "origins": [],
            }
        sources[key]["origins"].append(origin)
    subjects: dict[str, dict[str, Any]] = {}
    excluded = []
    for (package_id, path, digest), source in sorted(sources.items()):
        text = source["document"]
        if path == GUIDE:
            excluded.append(
                {
                    "path": path,
                    "sha256": digest,
                    "origins": source["origins"],
                    "reason": "Encounter guidance, not a separate creature profile",
                }
            )
            continue
        if text.startswith("---\n"):
            frontmatter = yaml.safe_load(text[4:].split("\n---\n", 1)[0])
        elif path == "npcs/v.md" and text.startswith("# V — Returner founder\n"):
            frontmatter = {"name": "V"}
        else:
            raise ValueError(f"Review cast source without named frontmatter: {path}")
        if not isinstance(frontmatter, dict) or not isinstance(frontmatter.get("name"), str):
            raise ValueError(f"Review unnamed cast source: {path}")
        kind = "npc" if path.startswith("npcs/") else "monster"
        for name in GROUPS.get(path, (frontmatter["name"],)):
            if path in GROUPS and name not in text:
                raise ValueError(f"Review changed group subject: {path}/{name}")
            slug = Path(path).stem + ("-" + name.lower() if path in GROUPS else "")
            identity = f"severed-{kind}-{slug}"
            subject = subjects.setdefault(
                identity,
                {
                    "id": identity,
                    "kind": kind,
                    "name": name,
                    "editions": [],
                },
            )
            binding = {
                "package_id": package_id,
                "path": path,
                "sha256": digest,
                "campaign_setting_id": package_id,
                **({"subject_name": name} if path in GROUPS else {}),
            }
            subject["editions"].append(
                {
                    "source": binding,
                    "document": text,
                    "origins": source["origins"],
                    "authored_metadata": frontmatter,
                }
            )
    return {
        "format_id": "sylvia-gm-authored-artwork-subjects",
        "format_version": 1,
        "subjects": [subjects[identity] for identity in sorted(subjects)],
        "excluded_sources": excluded,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the source inventory JSON here")
    args = parser.parse_args()
    inventory = authored_artwork_subjects()
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
                    "editions": sum(len(item["editions"]) for item in inventory["subjects"]),
                    "excluded_guides": len(inventory["excluded_sources"]),
                }
            )
        )


if __name__ == "__main__":
    main()
