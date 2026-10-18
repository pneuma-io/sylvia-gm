"""Select disposable, bounded working memory from authoritative campaign records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import zip_longest
from typing import cast

from sylvia_gm.application.knowledge_search import KnowledgeReference, rank_references
from sylvia_gm.domain.package_library import canonical_json_bytes

_MAX_MEMORY_RECORDS = 128
_MAX_MEMORY_BYTES = 48 * 1024


def select_campaign_memory(
    facts: Sequence[Mapping[str, object]],
    *,
    queries: Sequence[str],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Share prompt space among recent facts and relevant/current entities, without clipping facts.

    Recency and relevance each get a turn so neither unrelated recent events nor many older
    name matches can starve the other. Each cue is ranked independently so scene detail cannot
    veto a player's topic or OOC correction. Full records stay behind bounded on-demand reads.
    """
    records = [dict(item) for item in facts]
    references = [
        KnowledgeReference(
            result={
                "source": "campaign_state",
                "path": str(item.get("record_id", index)),
                "label": str(item.get("name", item.get("record_id", index))),
                "kind": "entity" if _is_entity(item) else "fact",
                "index": index,
            },
            identity=str(item.get("record_id", "")),
            body=canonical_json_bytes(item).decode("utf-8"),
        )
        for index, item in enumerate(records)
    ]
    rankings = [
        [cast(int, item["index"]) for item in rank_references(query[:2000], references)]
        for query in queries if query.strip()
    ]
    relevant = list(dict.fromkeys(
        index for group in zip_longest(*rankings) for index in group if index is not None
    ))
    recent = sorted(
        range(len(records)),
        key=lambda index: (_revision(records[index]), index),
        reverse=True,
    )
    lanes = (
        [i for i in recent if not _is_entity(records[i])],
        [i for i in relevant if _is_entity(records[i])],
        [i for i in relevant if not _is_entity(records[i])],
        [i for i in recent if _is_entity(records[i])],
    )
    selected: list[dict[str, object]] = []
    seen: set[int] = set()
    used_bytes = 2
    for group in zip_longest(*lanes):
        for index in group:
            if index is None or index in seen:
                continue
            seen.add(index)
            record = records[index]
            size = len(canonical_json_bytes(record)) + bool(selected)
            if len(selected) < _MAX_MEMORY_RECORDS and used_bytes + size <= _MAX_MEMORY_BYTES:
                selected.append(record)
                used_bytes += size
    return selected, {
        "total_records": len(records),
        "selected_records": len(selected),
        "omitted_records": len(records) - len(selected),
        "maximum_records": _MAX_MEMORY_RECORDS,
        "maximum_bytes": _MAX_MEMORY_BYTES,
        "meaning": (
            "Partial selection of recent facts and relevant/current entities, not all memory. "
            "admitted_revision orders when records were saved, not in-world time. Old plans and "
            "relative dates may have later outcomes. Search and read exact records before denying "
            "an earlier event or repeating a planned task. Omission or lookup failure is not "
            "evidence that an event never happened. NPC knowledge remains limited by known_by."
        ),
    }


def _is_entity(record: Mapping[str, object]) -> bool:
    """Recognize the authoritative record kind and older in-memory projections."""
    return record.get("record_kind") == "entity" or "entity_kind" in record


def _revision(record: Mapping[str, object]) -> int:
    """Use admitted chronology when available; never derive it from an authored identifier."""
    value = record.get("admitted_revision")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
