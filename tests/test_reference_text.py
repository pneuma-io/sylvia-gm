"""UTF-8 excerpts keep exact offsets, bounded replies and a bounded search shortlist."""

import pytest

from sylvia_gm.application.reference_text import matching_text_excerpts, text_excerpt
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes


def test_unicode_continuations_reconstruct_the_source_without_split_characters():
    source = "é🙂Ж" * 12000
    offset, parts = 0, []
    while True:
        page = text_excerpt(source, offset, maximum_bytes=30000)
        assert len(page["content"].encode()) <= 30000
        assert page["content"] == source[offset : offset + len(page["content"])]
        parts.append(page["content"])
        if page["next_offset"] is None:
            assert page["truncated"] is False
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert "".join(parts) == source


@pytest.mark.parametrize(
    "offset,budget",
    [(-1, 65536), (True, 65536), (99, 65536), (0, 0), (0, True), (0, 65537), (0, 1)],
)
def test_invalid_offsets_and_reply_budgets_fail_explicitly(offset, budget):
    with pytest.raises(CommandRejected):
        text_excerpt("🙂", offset, maximum_bytes=budget)


def test_search_finds_later_and_boundary_terms_with_exact_bounded_excerpts():
    source = "padding " * 25000 + "Monk Focus Points " + "tail " * 10000
    pages = list(matching_text_excerpts(source, "Monk Focus Points", 2))
    assert pages and len(pages) <= 2
    assert any("Monk Focus Points" in part for _, part in pages)
    for offset, part in pages:
        assert offset > 65536
        assert source[offset : offset + len(part)] == part
        assert len(part.encode()) <= 65536
    assert list(matching_text_excerpts(source, "absent-unicorn", 8)) == []


def test_json_escaping_also_respects_the_wire_budget_and_exact_continuation():
    source = '\x01\n\\"' * 16000
    parts, offset = [], 0
    while True:
        page = text_excerpt(source, offset)
        assert len(canonical_json_bytes(page["content"])) - 2 <= 48 * 1024
        parts.append(page["content"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert "".join(parts) == source


def test_markdown_hierarchy_and_available_heading_metadata_are_bounded():
    from sylvia_gm.application.authority import _markdown_section_references, _markdown_sections

    value = "# Root\nIntro\n## One\nBody\n### Nested\nChild\n## Two\nLast"
    sections = _markdown_sections(value)
    assert "Child" in sections[1][2] and "Last" not in sections[1][2]
    assert sections[-1] == ("Two", "two", "## Two\nLast")
    crowded = "\n".join(f"## Heading {i}\nBody" for i in range(1000))
    references, total = _markdown_section_references(
        crowded, path="lore/source.md", document_private=False
    )
    assert total == 1000 and 0 < len(references) <= 100
    assert len(canonical_json_bytes(references)) <= 8 * 1024
