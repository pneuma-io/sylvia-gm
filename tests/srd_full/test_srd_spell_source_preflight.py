"""Invalid source excerpts fail before composing the growing spell runtime."""

from pathlib import Path

import pytest
import yaml
from scripts import build_srd_5_2_1_spells as builder


def test_invalid_cross_page_source_fails_before_runtime_composition(monkeypatch):
    package = Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1/content"
    declaration = yaml.safe_load((package / "spell-mechanics.yaml").read_text())
    source = declaration["spells"]["heat_metal"]["source"]
    source["page"] = source.pop("pages")[0]

    def unexpected_composition(*args):
        pytest.fail("Source errors must be rejected before the expensive runtime composition")

    monkeypatch.setattr(builder, "_compile_runtime", unexpected_composition)
    with pytest.raises(SystemExit, match="Spell source boundaries are absent: heat_metal"):
        builder._build_spell_outputs(
            corpus_bytes=(package / "corpus/spells.json").read_bytes(),
            playing_the_game_bytes=(package / "corpus/playing_the_game.json").read_bytes(),
            rules_glossary_bytes=(package / "corpus/rules_glossary.json").read_bytes(),
            declaration_text=yaml.safe_dump(declaration, sort_keys=False),
            runtime_source="",
            record_bytes=b"",
            declarations_bytes=b"",
            identity_bytes=b"",
        )
