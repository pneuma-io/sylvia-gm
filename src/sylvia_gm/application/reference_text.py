"""Bound model excerpts independently of the admitted UTF-8 source size."""

from collections import Counter
from collections.abc import Iterator
from heapq import nlargest
from typing import TypedDict

from sylvia_gm.application.knowledge_search import knowledge_terms
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes

REFERENCE_REPLY_BYTE_LIMIT = 64 * 1024
REFERENCE_CONTENT_BYTE_LIMIT = 48 * 1024
REFERENCE_EXCERPT_CHARS = 16_000


class TextExcerpt(TypedDict):
    content: str
    selection: dict[str, str | int]
    truncated: bool
    next_offset: int | None


def text_excerpt(
    text: str, offset: int = 0, *, maximum_bytes: int = REFERENCE_REPLY_BYTE_LIMIT
) -> TextExcerpt:
    """Return one UTF-8-safe excerpt and its exact character continuation."""
    if (
        not isinstance(maximum_bytes, int)
        or isinstance(maximum_bytes, bool)
        or not 1 <= maximum_bytes <= REFERENCE_REPLY_BYTE_LIMIT
    ):
        raise CommandRejected(
            "Reference reply limit must be from 1 to 65536 bytes",
            code="campaign_reference_limit_invalid",
        )
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or offset > len(text)
        or (text and offset == len(text))
    ):
        raise CommandRejected(
            "Reference text offset is unavailable", code="campaign_reference_offset_invalid"
        )
    part = text[offset : offset + REFERENCE_EXCERPT_CHARS]
    budget = min(maximum_bytes, REFERENCE_CONTENT_BYTE_LIMIT)
    content = part.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    # JSON escaping can make valid text larger on the wire (for example control characters).
    if len(canonical_json_bytes(content)) - 2 > budget:
        low, high = 0, len(content)
        while low < high:
            middle = (low + high + 1) // 2
            if len(canonical_json_bytes(content[:middle])) - 2 <= budget:
                low = middle
            else:
                high = middle - 1
        content = content[:low]
    if part and not content:
        raise CommandRejected(
            "Reference reply limit cannot hold the next UTF-8 character",
            code="campaign_reference_limit_invalid",
        )
    following = offset + len(content)
    return {
        "content": content,
        "selection": {
            "kind": "text_excerpt",
            "offset": offset,
            "characters": len(content),
            "total_characters": len(text),
        },
        "truncated": following < len(text),
        "next_offset": following if following < len(text) else None,
    }


def matching_text_excerpts(text: str, query: str, limit: int) -> Iterator[tuple[int, str]]:
    """Keep only the best bounded excerpts per file without retaining a whole token index."""
    terms = set(knowledge_terms(query))

    def candidates() -> Iterator[tuple[int, int, int, int, str]]:
        # Overlap covers terms at an excerpt boundary; returned offsets remain exact.
        for offset in range(0, len(text), REFERENCE_EXCERPT_CHARS - 512):
            part = text[offset : offset + REFERENCE_EXCERPT_CHARS]
            counts = Counter(knowledge_terms(part))
            present = terms & counts.keys()
            if present:
                yield len(present), sum(counts[t] for t in present), -offset, offset, part

    for _coverage, _frequency, _order, offset, content in nlargest(limit, candidates()):
        yield offset, content
