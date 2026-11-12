"""New design text preserves authored details and exact artwork-source bindings."""

import hashlib

import pytest
from scripts.inventory_authored_artwork import authored_artwork_subjects
from scripts.prepare_authored_design_inputs import authored_design_inputs, split_design_document


def test_actual_roster_retains_every_binding_and_separates_editable_defaults():
    original = {subject["id"]: subject for subject in authored_artwork_subjects()["subjects"]}
    prepared = {subject["id"]: subject for subject in authored_design_inputs()["subjects"]}
    assert prepared.keys() == original.keys() and len(prepared) == 218
    assert sum(len(subject["design_variants"]) for subject in prepared.values()) == 219
    omitted = 0
    editions = 0
    for identity, subject in prepared.items():
        expected = {
            edition["source"]["sha256"]: edition for edition in original[identity]["editions"]
        }
        seen = set()
        selected = []
        for variant in subject["design_variants"]:
            text = variant["authored_document"]
            assert hashlib.sha256(text.encode()).hexdigest() == variant["design_text_sha256"]
            if variant["design_text_sha256"] == subject["current_design_text_sha256"]:
                selected.append(variant)
            for edition in variant["source_editions"]:
                source = expected[edition["source"]["sha256"]]
                assert edition["source"] == source["source"]
                assert edition["origins"] == source["origins"]
                authored, appendix = split_design_document(source["document"])
                assert authored == text
                assert edition["omitted_editable_appendix_sha256"] == (
                    hashlib.sha256(appendix.encode()).hexdigest() if appendix is not None else None
                )
                omitted += appendix is not None
                editions += 1
                seen.add(edition["source"]["sha256"])
        assert seen == expected.keys() and len(selected) == 1
        assert any(edition["current"] for edition in selected[0]["source_editions"])
    assert editions == 499 and omitted == 280
    marla = prepared["severed-npc-marla-cook"]["design_variants"][0]["authored_document"]
    assert "Marla treats feeding people as a form of care" in marla
    assert "## Personality" in marla
    assert "auburn" not in marla and "terracotta" not in marla
    assert "assets/characters/" not in marla
    assert len(prepared["severed-npc-v"]["design_variants"]) == 2
    siblings = [
        prepared["severed-npc-lace-brothers-" + name] for name in ("thread", "hook", "needle")
    ]
    assert {subject["name"] for subject in siblings} == {"Thread", "Hook", "Needle"}
    assert all(
        "subject_name" in subject["design_variants"][0]["source_editions"][0]["source"]
        for subject in siblings
    )


def test_authored_appearance_and_subsequent_lore_are_never_silently_removed():
    authored = "# Scout\n\n## Appearance and artwork\n\nTheir authored eye patch is essential.\n"
    assert split_design_document(authored) == (authored, None)
    ambiguous = (
        "# Scout\n\n## Appearance and artwork\n\n"
        "This prepared appearance is an editable visual default; "
        "the character's authored identity, "
        "mechanics and lore above remain authoritative.\n\n## Later authored lore\nKeep this.\n"
    )
    with pytest.raises(ValueError, match="ambiguous"):
        split_design_document(ambiguous)
    with pytest.raises(ValueError, match="unrecognized"):
        split_design_document("# Scout\n\n## Appearance and artwork\nAn editable visual default.\n")
