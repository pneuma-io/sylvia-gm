from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import pypdf
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "sources" / "dnd-srd-5.2.1"
PACKAGE_ROOT = ROOT / "rulesets" / "dnd-srd-5.2.1"
CONTENT_ROOT = PACKAGE_ROOT / "content"
CORPUS_ROOT = CONTENT_ROOT / "corpus"
SOURCE_ID = "wotc.srd.5.2.1.pdf"
SOURCE_SHA256 = "8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87"
PYPDF_VERSION = "6.15.0"
EXTRACTION_RECEIPT = {
    "tool": "pypdf",
    "version": PYPDF_VERSION,
    "method": "PdfReader.pages[].extract_text()",
    "text_encoding": "utf-8",
}
SECTIONS = (
    ("legal_information", "Legal Information", 1, 1),
    ("contents", "Contents", 2, 4),
    ("playing_the_game", "Playing the Game", 5, 18),
    ("character_creation", "Character Creation", 19, 27),
    ("classes", "Classes", 28, 82),
    ("character_origins", "Character Origins", 83, 86),
    ("feats", "Feats", 87, 88),
    ("equipment", "Equipment", 89, 103),
    ("spells", "Spells", 104, 175),
    ("rules_glossary", "Rules Glossary", 176, 191),
    ("gameplay_toolbox", "Gameplay Toolbox", 192, 203),
    ("magic_items", "Magic Items", 204, 253),
    ("monsters", "Monsters", 254, 343),
    ("animals", "Animals", 344, 364),
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_canonical(path: Path, value: Any) -> bytes:
    payload = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return payload


def _corpus_declaration(artifacts: list[str]) -> dict[str, Any]:
    return {
        "id": "official-srd-5.2.1-corpus",
        "kind": "reference_corpus",
        "family": "official-srd-corpus",
        "disposition": "included_nonmechanical",
        "classifications": ["official_licensed_content"],
        "sources": [
            {
                "authority_id": SOURCE_ID,
                "locator": {
                    "kind": "pdf_page_range",
                    "pages": [1, 364],
                    "content_sha256": SOURCE_SHA256,
                },
                "source_unit_id": "srd.5.2.1.complete-corpus",
            }
        ],
        "links": {
            "artifacts": sorted(["ATTRIBUTION.md", "content/corpus-index.json", *artifacts]),
            "capabilities": ["dnd.srd.5.2.1.corpus"],
            "mechanics": [],
            "procedures": [],
            "prompt_fragments": [],
            "schemas": [],
            "state_namespaces": [],
            "tools": [],
            "ui_surfaces": [],
        },
        "evidence": {"tests": []},
    }


def build_corpus(
    *,
    source: Path,
    package_root: Path,
    source_catalog_path: Path,
) -> None:
    if pypdf.__version__ != PYPDF_VERSION:
        raise SystemExit(
            "Refusing non-reproducible extraction with pypdf "
            f"{pypdf.__version__}; expected {PYPDF_VERSION}"
        )
    source_bytes = source.read_bytes()
    actual_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if actual_sha256 != SOURCE_SHA256:
        raise SystemExit(f"Refusing to build from unexpected source bytes: {actual_sha256}")

    pdf = PdfReader(source)
    if len(pdf.pages) != 364:
        raise SystemExit(f"Refusing unexpected page count: {len(pdf.pages)}")

    section_index: list[dict[str, Any]] = []
    corpus_artifacts: list[str] = []
    for section_id, title, first_page, last_page in SECTIONS:
        artifact = f"content/corpus/{section_id}.json"
        records = []
        for page_number in range(first_page, last_page + 1):
            text = pdf.pages[page_number - 1].extract_text() or ""
            records.append(
                {
                    "page": page_number,
                    "text": text,
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
        section_payload = {
            "format_id": "sylvia-gm-srd-corpus-section",
            "format_version": 1,
            "source_id": SOURCE_ID,
            "source_sha256": SOURCE_SHA256,
            "extraction": EXTRACTION_RECEIPT,
            "section": {
                "id": section_id,
                "title": title,
                "pages": [first_page, last_page],
            },
            "page_records": records,
        }
        payload_bytes = _write_canonical(package_root / artifact, section_payload)
        corpus_artifacts.append(artifact)
        section_index.append(
            {
                "id": section_id,
                "title": title,
                "pages": [first_page, last_page],
                "artifact": artifact,
                "artifact_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            }
        )

    index = {
        "format_id": "sylvia-gm-srd-corpus-index",
        "format_version": 1,
        "source_id": SOURCE_ID,
        "source_sha256": SOURCE_SHA256,
        "extraction": EXTRACTION_RECEIPT,
        "page_count": 364,
        "sections": section_index,
    }
    content_root = package_root / "content"
    _write_canonical(content_root / "corpus-index.json", index)

    coverage_records: list[dict[str, Any]] = []

    def walk_outline(items: list[object], parent: tuple[str, ...] = ()) -> None:
        previous_title: str | None = None
        for item in items:
            if isinstance(item, list):
                walk_outline(item, (*parent, previous_title) if previous_title else parent)
                continue
            title = str(getattr(item, "title", item))
            previous_title = title
            try:
                page = pdf.get_destination_page_number(item) + 1
            except Exception:
                continue
            outline_path = (*parent, title)
            normalized_title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            path_sha256 = hashlib.sha256("\n".join(outline_path).encode("utf-8")).hexdigest()
            coverage_records.append(
                {
                    "source_unit_id": (f"srd.5.2.1.outline.{normalized_title}.{path_sha256[:12]}"),
                    "outline_path": list(outline_path),
                    "page": page,
                    "disposition": "included_reference",
                    "mechanics_status": "mechanics_pending",
                }
            )

    walk_outline(pdf.outline)
    if len(coverage_records) != 519:
        raise SystemExit(f"Refusing unexpected official outline size: {len(coverage_records)}")
    coverage_ledger = {
        "format_id": "sylvia-gm-source-coverage-ledger",
        "format_version": 1,
        "source_id": SOURCE_ID,
        "source_sha256": SOURCE_SHA256,
        "claim_status": "CORPUS_COMPLETE_MECHANICS_IN_PROGRESS",
        "records": coverage_records,
    }
    _write_canonical(content_root / "coverage-ledger.json", coverage_ledger)

    catalog = json.loads(source_catalog_path.read_text(encoding="utf-8"))
    catalog["claim_status"] = "CORPUS_COMPLETE_MECHANICS_IN_PROGRESS"
    catalog["completion_rule"] = (
        "The complete official corpus is admitted as immutable package data. "
        "Alpha-04 remains incomplete until selected executable mechanics are "
        "complete and independently reviewed."
    )
    section_by_id = {item["id"]: item for item in section_index}
    for section in catalog["sections"]:
        mapped = bool(section.get("mapped_source_units"))
        section["mapping_status"] = (
            "corpus_admitted_mechanics_partial" if mapped else "corpus_admitted"
        )
        section["corpus_artifact"] = section_by_id[section["id"]]["artifact"]
        section["corpus_artifact_sha256"] = section_by_id[section["id"]]["artifact_sha256"]
    catalog["extraction"] = EXTRACTION_RECEIPT
    catalog_bytes = _write_canonical(source_catalog_path, catalog)

    declarations_path = content_root / "reference-content-declarations.json"
    declarations = json.loads(declarations_path.read_text(encoding="utf-8"))
    inclusions = [
        inclusion
        for inclusion in declarations["inclusions"]
        if inclusion["id"] != "official-srd-5.2.1-corpus"
    ]
    inclusions.append(_corpus_declaration(corpus_artifacts))
    declarations["inclusions"] = sorted(inclusions, key=lambda item: item["id"])
    declarations["scope"] = {
        **declarations["scope"],
        "claim_status": "COMPLETE",
        "scope_id": "srd-5.2.1-reference",
        "scope_kind": "complete_ruleset",
        "source_boundary_refs": [
            {
                "artifact_id": "srd-5.2.1-source-catalog",
                "sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            }
        ],
    }
    _write_canonical(declarations_path, declarations)

    print(
        f"Built SRD 5.2.1 corpus: {len(section_index)} sections, "
        f"{len(pdf.pages)} pages, source {SOURCE_SHA256}, "
        f"pypdf {PYPDF_VERSION}"
    )


def main() -> None:
    build_corpus(
        source=SOURCE_ROOT / "SRD_CC_v5.2.1.pdf",
        package_root=PACKAGE_ROOT,
        source_catalog_path=SOURCE_ROOT / "source-catalog.json",
    )


if __name__ == "__main__":
    main()
