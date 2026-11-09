"""Rank ephemeral references from admitted campaign bytes; retain no parallel search store."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes

_STOP_WORDS = frozenset(
    [
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "by",
        "can",
        "could",
        "current",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "she",
        "should",
        "tell",
        "than",
        "that",
        "the",
        "their",
        "them",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "now",
    ]
)
_SOURCE_ORDER = {
    "campaign_state": 0,
    "participant": 1,
    "campaign_plan": 2,
    "campaign_setting": 3,
    "ruleset": 4,
}


def knowledge_terms(value: str) -> tuple[str, ...]:
    """Use whole words, treating punctuation and possessives consistently in all fields."""
    words = re.findall(r"[^\W_]+(?:['’][^\W_]+)?", value.casefold())
    return tuple(
        word.removesuffix("'s").removesuffix("’s") for word in words if word not in _STOP_WORDS
    )


@dataclass(frozen=True)
class KnowledgeReference:
    """Separate internal searchable text from the reference-only public result."""

    result: Mapping[str, object]
    identity: str
    body: str


def bounded_search_page(
    metadata: Mapping[str, object],
    matches: Sequence[dict[str, object]],
    *,
    offset: int,
    limit: int,
    maximum_bytes: int,
) -> dict[str, object]:
    """Page exact references without dropping a result that exceeds the remaining bytes."""
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= len(matches):
        raise CommandRejected(
            "Campaign knowledge parameter 'offset' is outside the current results",
            code="campaign_knowledge_offset_invalid",
        )
    selected: list[dict[str, object]] = []

    def payload() -> dict[str, object]:
        end = offset + len(selected)
        return {
            **metadata,
            "offset": offset,
            "limit": limit,
            "matches": list(selected),
            "truncated": end < len(matches),
            "next_offset": end if end < len(matches) else None,
        }

    if len(canonical_json_bytes(payload())) > maximum_bytes:
        raise CommandRejected(
            "Campaign knowledge search metadata exceeds the reply byte bound",
            code="campaign_knowledge_metadata_too_large",
        )
    for match in matches[offset : offset + limit]:
        selected.append(match)
        if len(canonical_json_bytes(payload())) <= maximum_bytes:
            continue
        selected.pop()
        if not selected:
            raise CommandRejected(
                "An exact campaign knowledge reference exceeds the reply byte bound",
                code="campaign_knowledge_result_too_large",
            )
        break
    return payload()


def rank_references(
    query: str,
    candidates: Sequence[KnowledgeReference],
) -> list[dict[str, object]]:
    """BM25 body relevance plus name/phrase weight, with conservative body-only admission.

    A title match can survive absent descriptive words. Without one, most of the query's IDF
    weight must match, avoiding unrelated results from incidental common words. Return
    distinct documents before additional sections so one long file cannot fill the shortlist.
    """
    query_words = knowledge_terms(query)
    terms = set(query_words)
    if not terms or not candidates:
        return []
    titles = [knowledge_terms(f"{item.identity} {item.result['label']}") for item in candidates]
    bodies = [Counter(knowledge_terms(item.body)) for item in candidates]
    frequencies = Counter(
        term
        for title, body in zip(titles, bodies, strict=True)
        for term in (set(title) | body.keys()) & terms
    )
    idf = {
        term: math.log(1 + (len(candidates) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
        for term in terms
    }
    average_length = max(1, sum(body.total() for body in bodies) / len(bodies))
    query_pairs = set(zip(query_words, query_words[1:], strict=False))
    query_weight = sum(idf.values())
    ranked = []
    for reference, title, body in zip(candidates, titles, bodies, strict=True):
        named = terms & set(title)
        matched = terms & body.keys()
        result = reference.result
        document_terms = set(knowledge_terms(str(result["path"]).split("#", 1)[0]))
        title_pairs = set(zip(title, title[1:], strict=False))
        # A generic heading such as "Personality" alone is weaker than a document name or phrase.
        named_subject = bool(terms & document_terms or query_pairs & title_pairs)
        if not named_subject and sum(idf[term] for term in matched) < 0.6 * query_weight:
            continue
        length_normalizer = 1.2 * (0.25 + 0.75 * body.total() / average_length)
        score = sum(
            idf[term] * body[term] * 2.2 / (body[term] + length_normalizer) for term in matched
        )
        score += 4 * sum(idf[term] for term in named)
        score += 2 * sum(idf[left] + idf[right] for left, right in query_pairs & title_pairs)
        if terms <= document_terms and str(result["label"]).casefold() == "overview":
            score += 4 * query_weight
        # Named current records take precedence; similarly named records remain distinct choices.
        exact_name = set(knowledge_terms(str(result["label"]))) == terms
        current = result["source"] == "campaign_state" and (
            (result.get("kind") == "entity" and bool(named)) or exact_name
        )
        precedence = 0 if current and exact_name else 1 if current else 2
        ranked.append(
            (precedence, -score, _SOURCE_ORDER[str(result["source"])], str(result["path"]), result)
        )
    ranked.sort(key=lambda item: item[:4])
    seen = set()
    distinct: list[dict[str, object]] = []
    additional_sections: list[dict[str, object]] = []
    for *_rank, result in ranked:
        document = (result["source"], str(result["path"]).split("#", 1)[0])
        destination = additional_sections if document in seen else distinct
        destination.append(dict(result))
        seen.add(document)
    return distinct + additional_sections
