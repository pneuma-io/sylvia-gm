"""Find Path includes its printed page-start continuation without relaxing source guards."""

import json
from copy import deepcopy

import pytest
from scripts import build_srd_5_2_1_spells as builder


def test_both_exact_reordered_continuations_and_changed_find_path_rejection():
    declaration = builder._spell_declaration(
        builder.SPELL_MECHANICS_PATH.read_text(encoding="utf-8")
    )
    corpora = {
        "spells": json.loads(builder.CORPUS_PATH.read_text(encoding="utf-8")),
        "rules_glossary": json.loads(
            builder.RULES_GLOSSARY_CORPUS_PATH.read_text(encoding="utf-8")
        ),
        "playing_the_game": json.loads(
            builder.PLAYING_THE_GAME_CORPUS_PATH.read_text(encoding="utf-8")
        ),
    }
    source = builder._spell_mechanics_source(corpora, declaration)
    for spell, page in (("antilife_shell", 109), ("find_the_path", 131)):
        row = source["spells"][spell]
        assert row["continuation"]["page"] == page
        assert row["derived_text"].endswith(row["continuation"]["derived_text"])
    assert "which path is the most direct." in source["spells"]["find_the_path"]["derived_text"]
    changed = deepcopy(declaration)
    changed["spells"]["find_the_path"]["source"]["continuation_next_heading"] = "Find Steed"
    with pytest.raises(SystemExit, match="Reordered spell continuation"):
        builder._spell_mechanics_source(corpora, changed)
