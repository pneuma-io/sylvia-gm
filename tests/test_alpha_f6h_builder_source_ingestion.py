"""Bounded local document/PDF ingestion for ordinary Builder sessions."""

from __future__ import annotations

import tomllib
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.infrastructure import builder_source_ingestion as source_ingestion
from sylvia_gm.infrastructure.builder_source_ingestion import (
    MAX_SOURCE_FILES,
    PURGE_PENDING_DIRECTORY,
    SOURCE_DIRECTORY,
    finalize_builder_source_purge,
    ingest_builder_sources,
    reconcile_builder_source_purges,
    stage_builder_sources_for_purge,
)


def _pdf_with_text(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 120 Td ({text}) Tj ET".encode("ascii")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        ),
    )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(value)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode(
            "ascii"
        )
    )
    return bytes(output)


def test_builder_ingests_text_and_text_layer_pdf_without_exposing_host_paths(
    tmp_path: Path,
) -> None:
    external = tmp_path / "selected-private-files"
    external.mkdir()
    text_source = external / "licensed-notes-private-name.md"
    text_source.write_text("Synthetic supplied rules notes.\n", encoding="utf-8")
    pdf_source = external / "closed-source-private-name.pdf"
    pdf_source.write_bytes(_pdf_with_text("Synthetic PDF rule source"))
    workspace = tmp_path / "campaigns" / "campaign-source-ingestion"

    first = ingest_builder_sources(workspace, (text_source, pdf_source))
    second = ingest_builder_sources(workspace, (text_source, pdf_source))

    assert first.sources == second.sources
    assert first.created is True
    assert second.created is False
    assert len(first.sources) == 2
    projection_text = repr([source.safe_projection() for source in first.sources])
    assert str(external) not in projection_text
    assert text_source.name not in projection_text
    assert pdf_source.name not in projection_text
    assert {source.media_type for source in first.sources} == {
        "text/plain; charset=utf-8",
        "application/pdf",
    }
    pdf = next(source for source in first.sources if source.media_type == "application/pdf")
    assert pdf.page_count == 1
    assert "Synthetic PDF rule source" in (workspace / pdf.workspace_path).read_text(
        encoding="utf-8"
    )
    assert all(
        source.workspace_path.startswith(f"{SOURCE_DIRECTORY}/batch-") for source in first.sources
    )
    assert sorted(path.name for path in (workspace / SOURCE_DIRECTORY).rglob("*.txt")) == sorted(
        Path(source.workspace_path).name for source in first.sources
    )


@pytest.mark.parametrize(
    ("name", "payload", "error_fragment"),
    (
        ("unsupported.docx", b"not a supported document", "type is not supported"),
        ("invalid.md", b"\xff\xfe", "not valid UTF-8"),
        ("image-only.pdf", _pdf_with_text(""), "no extractable text layer"),
    ),
)
def test_builder_source_ingestion_rejects_unsupported_or_unusable_sources(
    tmp_path: Path,
    name: str,
    payload: bytes,
    error_fragment: str,
) -> None:
    source = tmp_path / name
    source.write_bytes(payload)
    workspace = tmp_path / "workspace"

    with pytest.raises(CommandRejected, match=error_fragment):
        ingest_builder_sources(workspace, (source,))

    assert not (workspace / SOURCE_DIRECTORY).exists()


def test_builder_source_batch_is_bounded_and_rejects_duplicate_content(tmp_path: Path) -> None:
    sources = []
    for index in range(MAX_SOURCE_FILES + 1):
        source = tmp_path / f"source-{index}.txt"
        source.write_text(str(index), encoding="utf-8")
        sources.append(source)
    with pytest.raises(CommandRejected, match="one and eight"):
        ingest_builder_sources(tmp_path / "workspace", tuple(sources))

    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("same synthetic bytes", encoding="utf-8")
    second.write_text("same synthetic bytes", encoding="utf-8")
    with pytest.raises(CommandRejected, match="duplicate content"):
        ingest_builder_sources(tmp_path / "workspace", (first, second))


@pytest.mark.parametrize("bound", ("bytes", "characters"))
def test_builder_source_ingestion_enforces_batch_resource_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bound: str,
) -> None:
    source = tmp_path / "bounded.txt"
    source.write_text("sixsix", encoding="utf-8")
    if bound == "bytes":
        monkeypatch.setattr(source_ingestion, "MAX_SOURCE_BYTES", 5)
        message = "import byte limit"
    else:
        monkeypatch.setattr(source_ingestion, "MAX_EXTRACTED_CHARS", 5)
        message = "extracted-text limit"

    with pytest.raises(CommandRejected, match=message):
        ingest_builder_sources(tmp_path / "workspace", (source,))

    assert not (tmp_path / "workspace" / SOURCE_DIRECTORY).exists()


def test_builder_source_ingestion_rejects_encrypted_pdf(tmp_path: Path) -> None:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(_pdf_with_text("encrypted source"))))
    writer.encrypt("synthetic-password")
    encoded = BytesIO()
    writer.write(encoded)
    source = tmp_path / "encrypted.pdf"
    source.write_bytes(encoded.getvalue())

    with pytest.raises(CommandRejected, match="Encrypted PDF"):
        ingest_builder_sources(tmp_path / "workspace", (source,))


def test_pdf_decompression_limit_is_a_bounded_source_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pypdf import filters

    writer = PdfWriter(clone_from=PdfReader(BytesIO(_pdf_with_text("Synthetic text" * 20))))
    writer.pages[0].compress_content_streams()
    encoded = BytesIO()
    writer.write(encoded)
    source = tmp_path / "bounded.pdf"
    source.write_bytes(encoded.getvalue())
    monkeypatch.setattr(filters, "ZLIB_MAX_OUTPUT_LENGTH", 64)

    with pytest.raises(CommandRejected, match="PDF source exceeds the supported parsing limits"):
        ingest_builder_sources(tmp_path / "workspace", (source,))
    assert not (tmp_path / "workspace" / SOURCE_DIRECTORY).exists()


def test_permanent_purge_removes_only_builder_source_subtree(tmp_path: Path) -> None:
    workspace = tmp_path / "campaign"
    source = tmp_path / "source.txt"
    source.write_text("synthetic", encoding="utf-8")
    ingest_builder_sources(workspace, (source,))
    retained = workspace / "campaign-plan.md"
    retained.write_text("retained sibling", encoding="utf-8")

    stage = stage_builder_sources_for_purge(
        workspace,
        campaign_id="campaign",
        operation_id="purge-campaign",
    )
    assert not (workspace / SOURCE_DIRECTORY).exists()
    assert (workspace / PURGE_PENDING_DIRECTORY).is_dir()
    finalize_builder_source_purge(stage)

    assert not (workspace / SOURCE_DIRECTORY).exists()
    assert retained.read_text(encoding="utf-8") == "retained sibling"


def test_builder_source_batch_publish_failure_leaves_no_partial_private_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first private source", encoding="utf-8")
    second.write_text("second private source", encoding="utf-8")
    workspace = tmp_path / "workspace"

    def reject_publish(_source: object, _destination: object) -> None:
        raise OSError("synthetic atomic publish failure")

    monkeypatch.setattr(source_ingestion.os, "replace", reject_publish)
    with pytest.raises(CommandRejected, match="workspace is unavailable"):
        ingest_builder_sources(workspace, (first, second), batch_id="failed-batch")

    source_root = workspace / SOURCE_DIRECTORY
    assert not source_root.exists() or not tuple(source_root.rglob("*.txt"))


def test_builder_source_purge_quarantine_recovers_or_finishes_after_restart(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "campaigns" / "campaign-reconcile"
    source = tmp_path / "source.txt"
    source.write_text("private source", encoding="utf-8")
    ingest_builder_sources(workspace, (source,))

    stage_builder_sources_for_purge(
        workspace,
        campaign_id="campaign-reconcile",
        operation_id="purge-reconcile-before-commit",
    )
    reconcile_builder_source_purges(
        tmp_path,
        {"campaign-reconcile"},
        lambda _operation_id, _campaign_id: False,
    )
    assert (workspace / SOURCE_DIRECTORY).is_dir()
    assert not (workspace / PURGE_PENDING_DIRECTORY).exists()

    stage_builder_sources_for_purge(
        workspace,
        campaign_id="campaign-reconcile",
        operation_id="purge-reconcile-committed",
    )
    reconcile_builder_source_purges(
        tmp_path,
        {"campaign-reconcile"},
        lambda operation_id, _campaign_id: operation_id == "purge-reconcile-committed",
    )
    assert not (workspace / SOURCE_DIRECTORY).exists()
    assert not (workspace / PURGE_PENDING_DIRECTORY).exists()


def test_builder_source_purge_journal_publication_is_atomic_and_temp_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "campaigns" / "campaign-journal-crash"
    source = tmp_path / "source.txt"
    source.write_text("journal crash source", encoding="utf-8")
    ingested = ingest_builder_sources(workspace, (source,))
    published = workspace / ingested.sources[0].workspace_path
    original_replace = source_ingestion.os.replace

    def reject_journal_publish(source_path: object, destination_path: object) -> None:
        if Path(str(destination_path)).name.startswith("purge-"):
            raise OSError("synthetic journal publication crash")
        original_replace(source_path, destination_path)

    monkeypatch.setattr(source_ingestion.os, "replace", reject_journal_publish)
    with pytest.raises(CommandRejected, match="could not be quarantined"):
        stage_builder_sources_for_purge(
            workspace,
            campaign_id="campaign-journal-crash",
            operation_id="purge-journal-crash",
        )
    assert published.read_text(encoding="utf-8") == "journal crash source"

    monkeypatch.setattr(source_ingestion.os, "replace", original_replace)
    pending_root = workspace / PURGE_PENDING_DIRECTORY
    pending_root.mkdir(exist_ok=True)
    abandoned = pending_root / ".purge-abandoned.tmp"
    abandoned.mkdir()
    (abandoned / "purge.json").write_text("{", encoding="utf-8")
    reconcile_builder_source_purges(
        tmp_path,
        {"campaign-journal-crash"},
        lambda _operation_id, _campaign_id: False,
    )
    assert not abandoned.exists()
    assert published.read_text(encoding="utf-8") == "journal crash source"


def test_pdf_parser_is_a_pinned_production_sidecar_dependency() -> None:
    repository = Path(__file__).resolve().parents[1]
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))

    assert "pypdf==6.15.0" in project["project"]["dependencies"]
    assert "pypdf==6.15.0" not in project["project"]["optional-dependencies"]["dev"]
