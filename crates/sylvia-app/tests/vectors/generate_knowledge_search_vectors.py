"""Reference vectors for sylvia_app::knowledge_search: Python's ranking and paging.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_knowledge_search_vectors.py

`knowledge_terms` for words with stop words, possessives, both apostrophes, digits, underscores,
combining marks and Unicode case; `rank_references` for a reference set shaped as the campaign
knowledge catalog builds it (campaign state entities, participants, plan, Setting and Ruleset
documents, several sections of one file, an overview) under queries that reach each rule: a name
match, a phrase pair, body-only admission above and below the 0.6 threshold, the overview boost,
an exact current record, and stop words alone; and `bounded_search_page` at several offsets,
limits and byte bounds, including each refusal. It writes `knowledge_search_vectors.json`.
Reference tooling only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from sylvia_gm.application.knowledge_search import (  # noqa: E402
    KnowledgeReference,
    bounded_search_page,
    knowledge_terms,
    rank_references,
)

OUTPUT = Path(__file__).with_name("knowledge_search_vectors.json")

WORDS = [
    "", "The captain's log", "Mara’s blade", "it's", "o'neill's", "rock'n'roll", "snake_case words",
    "Straße STRASSE", "ǅemal", "naïve café", "Ⅻ roman ½", "where is the harbour now?",
    "a an the", "Ünder-the-Bridge", "x'", "'quoted'", "tab\tand\nnewline", "日本語のテキスト", "١٢٣ arabic digits",
]


def ref(source: str, path: str, label: str, body: str, *, identity: str | None = None,
        kind: str | None = None) -> KnowledgeReference:
    result: dict[str, object] = {"source": source, "path": path, "label": label}
    if kind is not None:
        result["kind"] = kind
    return KnowledgeReference(result=result, identity=identity or path, body=body)


REFERENCES = [
    ref("campaign_state", "entity:mara", "Mara", "Mara is a smuggler who knows the harbour tunnels.",
        kind="entity"),
    ref("campaign_state", "entity:harbour-master", "Harbour Master", "The harbour master taxes every ship.",
        kind="entity"),
    ref("participant", "participant:bo", "Bo", "Bo is a fisher; Bo's boat is old."),
    ref("campaign_plan", "plan#act-1", "Act One", "The heist at the harbour begins at night."),
    ref("campaign_plan", "plan#act-2", "Act Two", "The chase through the tunnels."),
    ref("campaign_setting", "setting/harbour.md#overview", "Overview", "The harbour city of Vell."),
    ref("campaign_setting", "setting/harbour.md#history", "History", "Vell was founded by smugglers."),
    ref("campaign_setting", "setting/harbour.md#personality", "Personality", "Proud and suspicious people."),
    ref("campaign_setting", "setting/tunnels.md", "Tunnels", "Old smuggler tunnels run under the docks."),
    ref("ruleset", "rules/stealth.md#hiding", "Hiding", "Hiding needs cover; smugglers hide well."),
    ref("ruleset", "rules/combat.md", "Combat", "Roll initiative. Attacks hit on a total of ten."),
]

QUERIES = [
    "Mara", "mara's tunnels", "harbour master", "the harbour", "harbour tunnels smuggler",
    "tunnels", "personality", "harbour overview", "roll initiative", "the", "", "Vell history",
    "smugglers hide", "boat", "MARA", "unknown words only", "harbour master taxes ships",
]

PAGES = [
    # (offset, limit, maximum_bytes)
    (0, 3, 10_000), (2, 2, 10_000), (0, 10, 10_000), (4, 3, 10_000), (0, 3, 400), (0, 3, 60),
    (0, 3, 120), (5, 1, 10_000), (6, 1, 10_000), (-1, 1, 10_000), (0, 0, 10_000), (1, -1, 10_000),
]


def attempt(read):  # noqa: ANN001, ANN201
    try:
        return {"ok": read()}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}


def main() -> None:
    references = [{"result": dict(item.result), "identity": item.identity, "body": item.body} for item in REFERENCES]
    ranked = {query: rank_references(query, REFERENCES) for query in QUERIES}
    matches = ranked["harbour tunnels smuggler"] or ranked["tunnels"]
    record = {
        "python": sys.version.split()[0],
        "terms": [{"value": value, "terms": list(knowledge_terms(value))} for value in WORDS],
        "references": references,
        "rankings": [{"query": query, "results": result} for query, result in ranked.items()],
        "empty_candidates": rank_references("harbour", []),
        "pages": [{"offset": offset, "limit": limit, "maximum_bytes": maximum,
                   "outcome": attempt(lambda: bounded_search_page({"query": "harbour", "total": len(matches)},
                                                                  matches, offset=offset, limit=limit,
                                                                  maximum_bytes=maximum))}
                  for offset, limit, maximum in PAGES],
        "page_matches": matches,
    }
    OUTPUT.write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
