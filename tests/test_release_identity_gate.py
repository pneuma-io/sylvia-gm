"""Literal release checks require external names and inspect archive payloads too."""

import io
import zipfile

import pytest
from scripts.check_release_identity import load_names, scan_paths


def test_missing_or_embedded_name_list_cannot_qualify_source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    embedded = root / "names.txt"
    embedded.write_text("retired-fixture\n")
    with pytest.raises(ValueError, match="outside"):
        load_names(embedded, root)
    external = tmp_path / "names.txt"
    external.write_text("")
    with pytest.raises(ValueError, match="empty"):
        load_names(external, root)
    with pytest.raises(ValueError, match="explicit"):
        scan_paths(root, [], ())


def test_exact_case_insensitive_names_in_source_and_archives_are_rejected(tmp_path):
    (tmp_path / "README.md").write_text("Clean source")
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("content.json", '{"name":"RETIRED-FIXTURE"}')
    (tmp_path / "example.zip").write_bytes(raw.getvalue())
    result = scan_paths(tmp_path, ["README.md", "example.zip"], ("retired-fixture",))
    assert not result["passed"]
    assert result["violations"] == ["content: example.zip", "content: example.zip!/content.json"]
    assert result["archives_scanned"] == 1
    (tmp_path / "README.md").write_text("RETIRED-FIXTURE")
    assert (
        "content: README.md"
        in scan_paths(tmp_path, ["README.md"], ("retired-fixture",))["violations"]
    )


def test_clean_source_passes_without_literal_exemptions(tmp_path):
    (tmp_path / "README.md").write_text("Sylvia GM")
    assert scan_paths(tmp_path, ["README.md"], ("retired-fixture",))["passed"]


@pytest.mark.parametrize("with_bom", [False, True])
@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("text", ["RETIRED-FIXTURE", "ReTiReD-FiXtUrE"])
def test_utf16le_names_in_raw_files_and_archive_members_are_rejected(
    tmp_path, with_bom, archived, text
):
    raw = (b"\xff\xfe" if with_bom else b"") + ("prefix " + text + " suffix").encode("utf-16le")
    name = "content.txt"
    if archived:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name, raw)
        name = "content.zip"
        raw = buffer.getvalue()
    (tmp_path / name).write_bytes(raw)
    result = scan_paths(tmp_path, [name], ("retired-fixture",))
    assert not result["passed"]
    expected = "content: content.zip!/content.txt" if archived else "content: content.txt"
    assert expected in result["violations"]


@pytest.mark.parametrize("with_bom", [False, True])
@pytest.mark.parametrize("archived", [False, True])
def test_clean_utf16le_raw_and_archived_text_passes(tmp_path, with_bom, archived):
    raw = (b"\xff\xfe" if with_bom else b"") + "Sylvia GM content".encode("utf-16le")
    name = "content.txt"
    if archived:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name, raw)
        name = "content.zip"
        raw = buffer.getvalue()
    (tmp_path / name).write_bytes(raw)
    result = scan_paths(tmp_path, [name], ("retired-fixture",))
    assert result["passed"] and result["violations"] == []
