"""Prepare authored text for new character designs without imposing editable artwork defaults."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.inventory_authored_artwork import authored_artwork_subjects

_HEADING = "\n## Appearance and artwork\n"
_EDITABLE_NOTICES = (
    "This prepared appearance is an editable visual default; the character's authored identity, "
    "mechanics and lore above remain authoritative.",
    "These separate prepared appearances are editable visual defaults. The authored sibling "
    "identities, personalities and lore above remain authoritative.",
    "This prepared appearance is an editable visual default; the creature's "
    "mechanics and lore above remain authoritative.",
)


def split_design_document(document: str) -> tuple[str, str | None]:
    """Separate only the explicitly marked producer appendix; preserve authored appearances."""
    prefix, heading, appendix = document.partition(_HEADING)
    marked = heading and any(notice in appendix for notice in _EDITABLE_NOTICES)
    if heading and "editable visual default" in appendix and not marked:
        raise ValueError("Review unrecognized editable artwork notice before preparing a design")
    if marked:
        if document.count(_HEADING) != 1 or "\n## " in appendix:
            raise ValueError("Review ambiguous artwork appendix before preparing a new design")
        if not prefix.strip():
            raise ValueError("An editable artwork appendix must follow the authored character")
        return prefix.rstrip("\n") + "\n", heading + appendix
    return document.rstrip("\n") + "\n", None


def authored_design_inputs() -> dict[str, Any]:
    """Group identical design text while retaining each complete source identity and origin."""
    inventory = authored_artwork_subjects()
    subjects = []
    for subject in inventory["subjects"]:
        variants: dict[str, dict[str, Any]] = {}
        current = set()
        for edition in subject["editions"]:
            document = edition["document"]
            source = edition["source"]
            if hashlib.sha256(document.encode()).hexdigest() != source["sha256"]:
                raise ValueError("Design input differs from its exact source binding")
            authored, appendix = split_design_document(document)
            digest = hashlib.sha256(authored.encode()).hexdigest()
            variant = variants.setdefault(
                digest,
                {
                    "design_text_sha256": digest,
                    "authored_document": authored,
                    "source_editions": [],
                },
            )
            current_origin = "campaign-settings/severed-lands/" + source["path"]
            is_current = current_origin in edition["origins"]
            if is_current:
                current.add(digest)
            variant["source_editions"].append(
                {
                    "source": dict(source),
                    "origins": list(edition["origins"]),
                    "current": is_current,
                    "omitted_editable_appendix_sha256": (
                        hashlib.sha256(appendix.encode()).hexdigest()
                        if appendix is not None
                        else None
                    ),
                }
            )
        if len(current) != 1:
            raise ValueError(f"Review missing or conflicting current design text: {subject['id']}")
        subjects.append(
            {
                "id": subject["id"],
                "kind": subject["kind"],
                "name": subject["name"],
                "current_design_text_sha256": next(iter(current)),
                "design_variants": [variants[key] for key in sorted(variants)],
            }
        )
    return {
        "format_id": "sylvia-gm-authored-design-inputs",
        "format_version": 1,
        "use": (
            "Initial design preparation only. Use the selected current authored text with separate "
            "rendering guidance. Preserve stated identities, appearances and unresolved mysteries. "
            "Editable prior artwork defaults are excluded, not replaced with new canonical facts. "
            "source.sha256 binds the full source; design_text_sha256 hashes design text only. "
            "After a new design is selected, use its canonical references for later image stages."
        ),
        "subjects": subjects,
        "excluded_sources": inventory["excluded_sources"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the design inputs JSON here")
    args = parser.parse_args()
    prepared = authored_design_inputs()
    encoded = json.dumps(prepared, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8", newline="\n")
        variants = [v for subject in prepared["subjects"] for v in subject["design_variants"]]
        editions = [e for variant in variants for e in variant["source_editions"]]
        print(
            json.dumps(
                {
                    "subjects": len(prepared["subjects"]),
                    "design_variants": len(variants),
                    "source_editions": len(editions),
                    "editable_appendices_excluded": sum(
                        edition["omitted_editable_appendix_sha256"] is not None
                        for edition in editions
                    ),
                }
            )
        )


if __name__ == "__main__":
    main()
