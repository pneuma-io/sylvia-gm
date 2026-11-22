"""Generate reference vectors for `select_campaign_memory` with Python's real ranker.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_campaign_memory_vectors.py

Writes `campaign_memory_vectors.json`: for fact lists and queries, the selected records and the
selection summary. The Rust side runs `sylvia_agent::campaign_memory::select_with` with
`sylvia_owner::preflight::rank_references` (lane 3's ranker). Reference tooling only; retired
with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent._campaign_memory import select_campaign_memory  # noqa: E402

OUTPUT = Path(__file__).with_name("campaign_memory_vectors.json")


def facts() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    names = ["Captain Orin", "Mara", "the Waystation", "Verdant Margin", "Riftwolf", "Old Bell"]
    for index, name in enumerate(names):
        records.append({"record_id": f"entity-{index}", "record_kind": "entity", "name": name,
                        "entity_kind": "npc" if index % 2 else "place",
                        "summary": f"{name} matters to the storm road.",
                        "admitted_revision": index + 1})
    texts = ["Orin promised to meet Mara at the Waystation at dawn.",
             "The Riftwolf was last seen in the Verdant Margin.",
             "A storm closed the northern road.",
             "Mara owes the ferryman two silver coins.",
             "The Old Bell rings when the Rift opens.",
             "Nobody trusts the toll keeper."]
    for index, text in enumerate(texts):
        records.append({"record_id": f"fact-{index}", "text": text, "visibility":
                        "player_known" if index % 2 == 0 else "gm_private",
                        "known_by": ["player", "mara"] if index % 3 == 0 else [],
                        "admitted_revision": 10 + index})
    records.append({"text": "A fact without an id or revision."})
    records.append({"record_id": "fact-bool-revision", "text": "Revision is a bool.",
                    "admitted_revision": True})
    return records


def big_facts() -> list[dict[str, object]]:
    return [{"record_id": f"bulk-{index:03d}", "text": "x" * 900 + f" item {index}",
             "admitted_revision": index} for index in range(140)]


def main() -> None:
    query_sets = [
        ["", "", "", ""],
        ["Where is Orin?", "", "waystation", "A storm on the road"],
        ["riftwolf", "OOC: what about the bell?", "", ""],
        ["   ", "Mara", "verdant-margin", ""],
        ["toll keeper ferryman coins", "", "", ""],
        ["ünïcode Ørin", "", "", ""],
    ]
    cases = []
    for name, records, queries_used in (("small", facts(), query_sets), ("empty", [], query_sets[:2]),
                                        ("bulk", big_facts(), query_sets[:2])):
        for queries in queries_used:
            selected, summary = select_campaign_memory(records, queries=queries)
            cases.append({"name": name, "facts": records, "queries": queries,
                          "selected": selected, "summary": summary})
    text = json.dumps({"cases": cases}, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(len(cases), "cases,", len(text), "bytes")


if __name__ == "__main__":
    main()
