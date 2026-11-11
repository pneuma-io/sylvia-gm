"""Aggregate SRD 5.2.1 provenance and corpus reproducibility checks."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

from pypdf import PdfReader

SOURCE_ROOT = Path(__file__).parents[1] / "sources" / "srd-lite"
PACKAGE_ROOT = Path(__file__).parents[1] / "rulesets" / "srd-lite"
CONTENT_ROOT = PACKAGE_ROOT / "content"
CORPUS_ROOT = CONTENT_ROOT / "corpus"
EXPECTED_SHA256 = "8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87"
EXPECTED_SECTIONS = (
    ("legal_information", 1, 1),
    ("contents", 2, 4),
    ("playing_the_game", 5, 18),
    ("character_creation", 19, 27),
    ("classes", 28, 82),
    ("character_origins", 83, 86),
    ("feats", 87, 88),
    ("equipment", 89, 103),
    ("spells", 104, 175),
    ("rules_glossary", 176, 191),
    ("gameplay_toolbox", 192, 203),
    ("magic_items", 204, 253),
    ("monsters", 254, 343),
    ("animals", 344, 364),
)


def _corpus_generator() -> ModuleType:
    """Load the owning corpus generator without making scripts a runtime package."""

    script = Path(__file__).parents[1] / "scripts" / "build_srd_5_2_1_corpus.py"
    spec = importlib.util.spec_from_file_location("sylvia_gm_srd_corpus_generator", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module








def test_package_identity_is_current_and_contains_no_legacy_srd_path() -> None:
    """Package identity names only SRD 5.2.1 while allowing existing generic runtime content."""

    record = json.loads((PACKAGE_ROOT / "package-record.json").read_text(encoding="utf-8"))
    assert record["id"] == "sylvia.srd-lite.bg3"
    assert record["provenance"]["source_sha256"] == EXPECTED_SHA256
    assert record["content_declarations"] == "content/content-declarations.json"
    assert "dnd-5e-srd" not in json.dumps(record, ensure_ascii=False)
