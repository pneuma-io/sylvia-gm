"""Search pages account for serialized metadata without losing exact references."""

import pytest

from sylvia_gm.application.knowledge_search import bounded_search_page
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes


def test_byte_limited_pages_preserve_all_reference_identities_in_order():
    matches = [
        {"path": f"exact/{index}", "provenance": {"heading": '"\\\n雪' * 2500}}
        for index in range(8)
    ]
    offset = 0
    recovered = []
    while offset is not None:
        page = bounded_search_page(
            {"query": "雪", "campaign_revision": 4},
            matches,
            offset=offset,
            limit=8,
            maximum_bytes=65536,
        )
        assert len(canonical_json_bytes(page)) <= 65536
        assert page["matches"]
        assert page["offset"] == len(recovered)
        assert page["truncated"] == (page["next_offset"] is not None)
        recovered.extend(page["matches"])
        offset = page["next_offset"]
    assert recovered == matches


@pytest.mark.parametrize("offset", [-1, True, "0", 2])
def test_invalid_continuation_is_rejected(offset):
    with pytest.raises(CommandRejected) as error:
        bounded_search_page({}, [{"path": "exact"}], offset=offset, limit=8, maximum_bytes=65536)
    assert error.value.code == "campaign_knowledge_offset_invalid"


@pytest.mark.parametrize(
    ("metadata", "matches", "code"),
    [
        ({"query": "x" * 65536}, [], "campaign_knowledge_metadata_too_large"),
        ({}, [{"path": "x" * 65536}], "campaign_knowledge_result_too_large"),
    ],
)
def test_unrepresentable_exact_metadata_is_explicit(metadata, matches, code):
    with pytest.raises(CommandRejected) as error:
        bounded_search_page(metadata, matches, offset=0, limit=8, maximum_bytes=65536)
    assert error.value.code == code


def test_empty_and_completed_pages_are_bounded_and_terminal():
    for matches, offset in [([], 0), ([{"path": "exact"}], 1)]:
        page = bounded_search_page({}, matches, offset=offset, limit=8, maximum_bytes=65536)
        assert page["matches"] == []
        assert page["next_offset"] is None
        assert page["truncated"] is False
