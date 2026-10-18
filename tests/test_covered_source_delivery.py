"""Recipient-source files follow the actual release boundary; no installer is run."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest
from scripts import build_windows_prerelease as builder

from sylvia_gm.covered_source_delivery import (
    COVERED_SOURCE_DELIVERY_SHA256,
    CoveredSourceError,
    _validate_archive,
    read_covered_source_companions,
)
from sylvia_gm.release_artifact import (
    RUST_STDLIB_NOTICE_SHA256,
    ReleaseArtifact,
    ReleaseArtifactError,
    assemble_qualified_release_layout,
    assemble_release_layout,
    verify_release_layout,
)

ROOT = Path(__file__).resolve().parents[1]


def _assemble(tmp_path: Path, *, source: Path = ROOT, companions: bool = True) -> Path:
    """Use real notice/source bytes with a labelled inert installer fixture."""
    installer = tmp_path / "DO-NOT-RUN-source-fixture.exe"
    installer.write_bytes(b"SYNTHETIC INSTALLER FIXTURE: never execute")
    notice = source / "dependency-notices.txt"
    output = tmp_path / "source-release"
    assemble_release_layout(
        output=output,
        artifacts=[ReleaseArtifact(installer, "nsis-installer")],
        provenance={
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "source_timestamp_utc": "2026-10-16T07:00:00Z",
        },
        toolchain=dict.fromkeys(
            ["cargo", "mcp", "pyinstaller", "python", "rustc", "tauri_cli", "uv"],
            "synthetic provenance fixture",
        ),
        qualification={"windows_10": "pending", "windows_11": "pending"},
        license_path=ROOT / "LICENSE",
        dependency_notices_path=notice,
        dependency_notices_sha256=hashlib.sha256(notice.read_bytes()).hexdigest(),
        dependency_notice_companions_directory=source / "licenses" if companions else None,
    )
    return output


def test_real_source_archive_is_readable_self_contained_and_qualified() -> None:
    values = read_covered_source_companions(ROOT / "licenses")
    assert set(values) == set(COVERED_SOURCE_DELIVERY_SHA256)
    external = json.loads(values["licenses/COVERED-SOURCE-MAP.json"])
    assert external["historical_compiled_correspondence"] is False
    assert external["certifi_historical_generation_inputs_proven"] is False
    with zipfile.ZipFile(io.BytesIO(values["licenses/COVERED-SOURCE.zip"])) as archive:
        assert len(archive.namelist()) == 27
        for name in archive.namelist():
            if name.endswith(".crate"):
                with tarfile.open(fileobj=io.BytesIO(archive.read(name)), mode="r:gz") as source:
                    assert any(m.name.endswith("/build.rs") for m in source)
            else:
                assert archive.read(name).decode("utf8").strip()
        assert len(archive.read("certifi/cacert.pem")) == 240216
        assert "Mozilla Public License" in archive.read("notices/MPL-2.0.txt").decode()
        generated = [n for n in archive.namelist() if n.startswith("selectors/generated/")]
        assert len(generated) == 2
        assert archive.read(generated[0]) == archive.read(generated[1])
        assert (
            len(
                [
                    n
                    for n in archive.namelist()
                    if n.startswith("selectors/current-selected-source/")
                ]
            )
            == 16
        )


def test_current_resources_and_assembler_preserve_exact_source_bytes(tmp_path: Path) -> None:
    resources = json.loads((ROOT / "src-tauri/tauri.conf.json").read_bytes())["bundle"]["resources"]
    assert len(resources) == 16
    for name in COVERED_SOURCE_DELIVERY_SHA256:
        assert resources["../" + name] == name
        assert list(resources.values()).count(name) == 1
    release = _assemble(tmp_path)
    manifest = verify_release_layout(release)
    assert manifest["format_version"] == 3
    assert len(manifest["dependency_notices"]["companions"]) == 7
    qualified = tmp_path / "qualified-source-release"
    result = assemble_qualified_release_layout(
        source=release,
        output=qualified,
        qualification={"windows_10": "not_available", "windows_11": "pending"},
    )
    assert verify_release_layout(qualified) == result
    for name in COVERED_SOURCE_DELIVERY_SHA256:
        assert (
            (release / name).read_bytes()
            == (qualified / name).read_bytes()
            == (ROOT / name).read_bytes()
        )


@pytest.mark.parametrize("name", list(COVERED_SOURCE_DELIVERY_SHA256))
@pytest.mark.parametrize("mutation", ["missing", "altered"])
def test_assembled_release_rejects_each_missing_or_changed_source(
    tmp_path: Path,
    name: str,
    mutation: str,
) -> None:
    release = _assemble(tmp_path)
    target = release / name
    if mutation == "missing":
        target.unlink()
    else:
        target.write_bytes(target.read_bytes() + b"changed")
    with pytest.raises(ReleaseArtifactError):
        verify_release_layout(release)
    with pytest.raises(ReleaseArtifactError):
        assemble_qualified_release_layout(
            source=release,
            output=tmp_path / "rejected",
            qualification={"windows_10": "pending", "windows_11": "pending"},
        )
    assert not (tmp_path / "rejected").exists()


def test_source_claim_cannot_omit_the_companion_directory(tmp_path: Path) -> None:
    with pytest.raises(ReleaseArtifactError, match="companion directory"):
        _assemble(tmp_path, companions=False)
    assert not (tmp_path / "source-release").exists()


@pytest.mark.parametrize(
    "missing",
    [
        "certifi/LICENSE",
        "notices/MPL-2.0.txt",
        "selectors/selectors-0.36.1.crate",
        "certifi/cacert.pem",
    ],
)
def test_source_package_rejects_missing_source_or_original_notice(missing: str) -> None:
    values = read_covered_source_companions(ROOT / "licenses")
    source_map = json.loads(values["licenses/COVERED-SOURCE-MAP.json"])
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(values["licenses/COVERED-SOURCE.zip"])) as original,
        zipfile.ZipFile(output, "w") as changed,
    ):
        for name in original.namelist():
            if name != missing:
                changed.writestr(name, original.read(name))
    blob = output.getvalue()
    source_map["archive"].update(size_bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest())
    with pytest.raises((CoveredSourceError, KeyError)):
        _validate_archive(blob, source_map, values["licenses/COVERED-SOURCE-README.md"])


@pytest.mark.parametrize(
    "mutation", ["missing-source", "missing-notice", "missing-mapping", "duplicate-mapping"]
)
def test_builder_rejects_source_gaps_before_any_build_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    (tmp_path / "src-tauri").mkdir()
    (tmp_path / "licenses").mkdir()
    resources = json.loads((ROOT / "src-tauri/tauri.conf.json").read_bytes())["bundle"]["resources"]
    for name in {**RUST_STDLIB_NOTICE_SHA256, **COVERED_SOURCE_DELIVERY_SHA256}:
        shutil.copyfile(ROOT / name, tmp_path / name)
    shutil.copyfile(ROOT / "dependency-notices.txt", tmp_path / "dependency-notices.txt")
    if mutation == "missing-source":
        (tmp_path / "licenses/COVERED-SOURCE.zip").unlink()
    elif mutation == "missing-notice":
        (tmp_path / "licenses/COVERED-SOURCE-README.md").unlink()
    elif mutation == "missing-mapping":
        del resources["../licenses/COVERED-SOURCE.zip"]
    else:
        resources["../duplicate"] = "licenses/COVERED-SOURCE.zip"
    (tmp_path / "src-tauri/tauri.conf.json").write_text(
        json.dumps({"bundle": {"resources": resources}})
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid source supplement reached build/source execution")

    monkeypatch.setattr(builder, "_run", forbidden)
    monkeypatch.setattr(builder, "validate_version_sources", forbidden)
    with pytest.raises(ReleaseArtifactError):
        builder.build_windows_prerelease(
            root=tmp_path,
            output=tmp_path / "rejected",
            windows_10="pending",
            windows_11="pending",
            dependency_notices_sha256=hashlib.sha256(
                (tmp_path / "dependency-notices.txt").read_bytes()
            ).hexdigest(),
        )
    assert not (tmp_path / "rejected").exists()
