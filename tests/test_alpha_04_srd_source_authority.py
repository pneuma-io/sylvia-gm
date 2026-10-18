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


def test_official_source_identity_and_complete_section_boundary() -> None:
    """The committed source is the exact licensed 364-page SRD selected for Alpha."""

    metadata = json.loads((SOURCE_ROOT / "source.json").read_text(encoding="utf-8"))
    pdf_path = SOURCE_ROOT / metadata["file"]
    pdf_bytes = pdf_path.read_bytes()
    catalog_bytes = (SOURCE_ROOT / "source-catalog.json").read_bytes()
    catalog = json.loads(catalog_bytes)
    declarations = json.loads(
        (CONTENT_ROOT / "content-declarations.json").read_text(encoding="utf-8")
    )

    assert len(pdf_bytes) == metadata["byte_length"] == 6_031_375
    assert hashlib.sha256(pdf_bytes).hexdigest() == metadata["sha256"] == EXPECTED_SHA256
    assert len(PdfReader(pdf_path).pages) == metadata["pdf_pages"] == 364
    assert metadata["license"] == "CC-BY-4.0"
    assert catalog["source_sha256"] == EXPECTED_SHA256
    assert tuple(
        (section["id"], section["pages"][0], section["pages"][1])
        for section in catalog["sections"]
    ) == EXPECTED_SECTIONS
    assert [
        page
        for section in catalog["sections"]
        for page in range(section["pages"][0], section["pages"][1] + 1)
    ] == list(range(1, 365))
    assert declarations["scope"]["source_boundary_refs"] == [
        {
            "artifact_id": "srd-5.2.1-source-catalog",
            "sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        }
    ]


def test_every_official_page_matches_the_committed_corpus() -> None:
    """One aggregate invariant proves lossless page admission without per-spell cases."""

    index = json.loads((CONTENT_ROOT / "corpus-index.json").read_text(encoding="utf-8"))
    pdf = PdfReader(SOURCE_ROOT / "SRD_CC_v5.2.1.pdf")
    admitted_pages: list[int] = []
    for section in index["sections"]:
        payload_bytes = (PACKAGE_ROOT / section["artifact"]).read_bytes()
        assert hashlib.sha256(payload_bytes).hexdigest() == section["artifact_sha256"]
        payload = json.loads(payload_bytes)
        for page in payload["page_records"]:
            page_number = page["page"]
            extracted = pdf.pages[page_number - 1].extract_text() or ""
            assert page["text"] == extracted
            assert page["text_sha256"] == hashlib.sha256(
                extracted.encode("utf-8")
            ).hexdigest()
            admitted_pages.append(page_number)
    assert admitted_pages == list(range(1, 365))


def test_corpus_regenerates_byte_for_byte_in_isolated_output(tmp_path: Path) -> None:
    """The one corpus generator reproduces all derived source artifacts exactly."""

    generated_package = tmp_path / "ruleset"
    generated_content = generated_package / "content"
    generated_content.mkdir(parents=True)
    shutil.copy2(
        CONTENT_ROOT / "content-declarations.json",
        generated_content / "content-declarations.json",
    )
    generated_catalog = tmp_path / "source-catalog.json"
    shutil.copy2(SOURCE_ROOT / "source-catalog.json", generated_catalog)

    _corpus_generator().build_corpus(
        source=SOURCE_ROOT / "SRD_CC_v5.2.1.pdf",
        package_root=generated_package,
        source_catalog_path=generated_catalog,
    )

    index = json.loads((CONTENT_ROOT / "corpus-index.json").read_text(encoding="utf-8"))
    generated_paths = [
        "content/corpus-index.json",
        "content/coverage-ledger.json",
        "content/content-declarations.json",
        *(section["artifact"] for section in index["sections"]),
    ]
    for relative in generated_paths:
        assert (generated_package / relative).read_bytes() == (PACKAGE_ROOT / relative).read_bytes()
    assert generated_catalog.read_bytes() == (SOURCE_ROOT / "source-catalog.json").read_bytes()


def test_package_identity_is_current_and_contains_no_legacy_srd_path() -> None:
    """Package identity names only SRD 5.2.1 while allowing existing generic runtime content."""

    record = json.loads((PACKAGE_ROOT / "package-record.json").read_text(encoding="utf-8"))
    assert record["id"] == "sylvia.srd-lite.bg3"
    assert record["provenance"]["source_sha256"] == EXPECTED_SHA256
    assert record["content_declarations"] == "content/content-declarations.json"
    assert "dnd-5e-srd" not in json.dumps(record, ensure_ascii=False)
