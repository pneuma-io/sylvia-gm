"""Prepare exact artwork bindings for the reviewed prose-only cast of shipped Campaign Plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from sylvia_gm.application.bundled_campaign_plans import read_plan_catalogue
from sylvia_gm.application.bundled_character_art import plan_artwork_source
from sylvia_gm.domain.package_library import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]


def plan_artwork_subjects() -> dict[str, Any]:
    """Check the reviewed roster against current Plan prose and emit production-ready bindings."""
    collection = ROOT / "campaign-plans"
    roster = json.loads((ROOT / "artwork/plan-cast.json").read_bytes())
    expected = {row["id"] for row in read_plan_catalogue(collection)}
    if (collection / "the-rift-awakening/plan.json").is_file():
        expected.add("the-rift-awakening")
    plans = {row["id"]: row["subjects"] for row in roster["plans"]}
    if roster.get("format") != 1 or set(plans) != expected or len(plans) != len(roster["plans"]):
        raise ValueError("The artwork roster must review every shipped and retained Plan once")
    subjects = []
    for plan_id, rows in sorted(plans.items()):
        path = collection / plan_id / "plan.json"
        plan = json.loads(path.read_bytes())
        if plan["id"] != plan_id:
            raise ValueError("Artwork Plan source identity differs from its collection entry")
        digest = hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
        setting = plan["bindings"]["campaign_setting"]
        identities: set[str] = set()
        for row in rows:
            if row["id"] in identities:
                raise ValueError("A Plan artwork subject is declared more than once")
            identities.add(row["id"])
            source_ref = "campaign_plan#" + row["source"]
            source = plan_artwork_source(plan, source_ref)
            if source is None:
                raise ValueError(f"Plan artwork source is not prose: {plan_id}/{row['id']}")
            text, source_hash = source
            names = list(dict.fromkeys([row["name"], *row["aliases"]]))
            if not all(isinstance(name, str) and name.strip() for name in names) or not any(
                re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE)
                for name in names
            ):
                raise ValueError(f"Plan subject needs source review: {plan_id}/{row['id']}")
            subjects.append(
                {
                    "id": f"severed-plan-{plan_id}-{row['id']}",
                    "name": row["name"],
                    "kind": row.get("kind", "person"),
                    "plan_sha256": digest,
                    "source_passage": text,
                    "sources": [
                        {
                            "plan_id": plan_id,
                            "path": source_ref,
                            "sha256": source_hash,
                            "subject_name": name,
                            "campaign_setting_id": setting["id"],
                        }
                        for name in names
                    ],
                }
            )
    return {
        "format_id": "sylvia-gm-plan-artwork-subjects",
        "format_version": 1,
        "reviewed_plans": sorted(plans),
        "subjects": subjects,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the artwork subject inventory here")
    args = parser.parse_args()
    inventory = plan_artwork_subjects()
    encoded = json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8", newline="\n")
        print(
            json.dumps(
                {
                    "plans": len(inventory["reviewed_plans"]),
                    "subjects": len(inventory["subjects"]),
                }
            )
        )


if __name__ == "__main__":
    main()
