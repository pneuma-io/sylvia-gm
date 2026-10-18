"""Deterministic, integrity-verifiable Windows prerelease layouts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from sylvia_gm import __version__
from sylvia_gm.covered_source_delivery import (
    CoveredSourceError,
    covered_source_required,
    read_covered_source_companions,
)

PRERELEASE_VERSION = __version__
WINDOWS_INSTALLER_VERSION = "0.3.0-1"
RELEASE_FORMAT_ID = "sylvia-gm-windows-release-manifest"
RELEASE_FORMAT_VERSION = 3
DEPENDENCY_NOTICES_SOURCE = "dependency-notices.txt"
DEPENDENCY_NOTICES_PATH = "licenses/dependency-notices.txt"
RUST_STDLIB_NOTICE_SHA256 = {
    "licenses/RUST-STDLIB-COPYRIGHT": (
        "172020dbfd5b53a226dfde77616190a48dcff519b0bc0e6deb91a8450782c4af"
    ),
    "licenses/RUST-STDLIB-COPYRIGHT-library.html": (
        "0a65bb747c49c7bb816cbc7188319bd6e4e8d08091c1190b8a3c0971c47968ed"
    ),
    "licenses/RUST-STDLIB-LICENSE-APACHE": (
        "62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a"
    ),
    "licenses/RUST-STDLIB-LICENSE-MIT": (
        "b71bd43a069ca0641a9ecfe585ca7b3c53b5cc1608f8b68321168698e28b5ea1"
    ),
}
UNSIGNED_DISCLOSURE = (
    "This Sylvia GM Alpha prerelease is unsigned. Windows may show an unfamiliar-publisher "
    "warning. Verify the installer SHA-256 against release-manifest.json or SHA256SUMS.txt "
    "before running it."
)

_VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-alpha\.([1-9][0-9]*)$"
)
_GIT_ID_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_ARTIFACT_SUFFIX = {
    "msi-installer": ".msi",
    "nsis-installer": ".exe",
    "portable-exe-zip": ".zip",
}
_REQUIRED_ARTIFACT_KINDS = frozenset({"nsis-installer"})
_QUALIFICATION_STATES = frozenset({"pending", "passed", "failed", "not_available"})
_PROVENANCE_KEYS = frozenset({"source_commit", "source_tree", "source_timestamp_utc"})
_QUALIFICATION_KEYS = frozenset({"windows_10", "windows_11"})
_TOOLCHAIN_KEYS = frozenset({"cargo", "mcp", "pyinstaller", "python", "rustc", "tauri_cli", "uv"})
_MANIFEST_KEYS = frozenset(
    {
        "artifacts",
        "dependency_notices",
        "format_id",
        "format_version",
        "product",
        "provenance",
        "qualification",
        "signing",
        "toolchain",
    }
)
_ARTIFACT_KEYS = frozenset({"kind", "name", "sha256", "size_bytes"})
_NOTICE_KEYS = frozenset({"path", "sha256", "size_bytes"})
_PRODUCT = {
    "architecture": "x86_64",
    "channel": "alpha",
    "name": "Sylvia GM",
    "platform": "windows",
    "version": PRERELEASE_VERSION,
    "windows_installer_version": WINDOWS_INSTALLER_VERSION,
}
_SIGNING = {
    "disclosure": UNSIGNED_DISCLOSURE,
    "status": "unsigned",
}


class ReleaseArtifactError(ValueError):
    """Release inputs or assembled bytes are not safe and coherent."""


@dataclass(frozen=True, slots=True)
class ReleaseArtifact:
    """One locally built installer selected for the release layout."""

    source: Path
    kind: str


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode(
        "utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_prerelease_version(value: str) -> re.Match[str]:
    matched = _VERSION_RE.fullmatch(value)
    if matched is None:
        raise ReleaseArtifactError("Release version is not a semantic Alpha prerelease version")
    return matched


def _uv_normalized_version(value: str) -> str:
    matched = _require_prerelease_version(value)
    return f"{matched.group(1)}.{matched.group(2)}.{matched.group(3)}a{matched.group(4)}"


def _expected_windows_installer_version(value: str) -> str:
    matched = _require_prerelease_version(value)
    return f"{matched.group(1)}.{matched.group(2)}.{matched.group(3)}-{matched.group(4)}"


def _package_version(document: Mapping[str, object], name: str, source: str) -> str:
    packages = document.get("package")
    if not isinstance(packages, list):
        raise ReleaseArtifactError(f"{source} has no package list")
    matches = [
        item.get("version")
        for item in packages
        if isinstance(item, dict) and item.get("name") == name
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise ReleaseArtifactError(f"{source} has no unique {name} package version")
    return matches[0]


def validate_version_sources(root: Path) -> str:
    """Require one coherent prerelease identity across every release input."""

    root = root.resolve()
    try:
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        cargo = tomllib.loads((root / "src-tauri/Cargo.toml").read_text(encoding="utf-8"))
        tauri = json.loads((root / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
        cargo_lock = tomllib.loads((root / "src-tauri/Cargo.lock").read_text(encoding="utf-8"))
        uv_lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
        versions = {
            "python_package": cast(dict[str, Any], pyproject["project"])["version"],
            "python_runtime": PRERELEASE_VERSION,
            "rust_package": cast(dict[str, Any], cargo["package"])["version"],
            "tauri_bundle": cast(dict[str, Any], tauri)["version"],
            "cargo_lock": _package_version(cargo_lock, "sylvia-gm", "Cargo.lock"),
        }
    except (KeyError, OSError, TypeError, tomllib.TOMLDecodeError, json.JSONDecodeError) as error:
        raise ReleaseArtifactError("Release version sources cannot be read") from error
    if any(not isinstance(value, str) for value in versions.values()):
        raise ReleaseArtifactError("Release version source is not text")
    expected = cast(str, versions["python_runtime"])
    _require_prerelease_version(expected)
    if _expected_windows_installer_version(expected) != WINDOWS_INSTALLER_VERSION:
        raise ReleaseArtifactError("Windows installer version does not match the prerelease")
    if set(cast(dict[str, str], versions).values()) != {expected}:
        raise ReleaseArtifactError("Release version sources do not match exactly")
    uv_version = _package_version(uv_lock, "sylvia-gm", "uv.lock")
    if uv_version not in {expected, _uv_normalized_version(expected)}:
        raise ReleaseArtifactError("uv.lock package version does not match the release")
    return expected


def _safe_leaf(name: str) -> str:
    if (
        not name
        or len(name) > 180
        or Path(name).name != name
        or any(character in name for character in '<>:"/\\|?*')
        or any(ord(character) < 32 for character in name)
        or name.endswith((" ", "."))
        or name.split(".", maxsplit=1)[0].upper() in _WINDOWS_RESERVED
    ):
        raise ReleaseArtifactError("Release artifact name is not a portable Windows leaf")
    return name


def _validated_artifacts(artifacts: Sequence[ReleaseArtifact]) -> tuple[ReleaseArtifact, ...]:
    if not artifacts:
        raise ReleaseArtifactError("Release has no installer artifacts")
    names: set[str] = set()
    kinds: set[str] = set()
    validated: list[ReleaseArtifact] = []
    for artifact in artifacts:
        expected_suffix = _ARTIFACT_SUFFIX.get(artifact.kind)
        if expected_suffix is None:
            raise ReleaseArtifactError("Release artifact kind is unsupported")
        try:
            supplied_source = artifact.source
            source = supplied_source.resolve(strict=True)
        except OSError as error:
            raise ReleaseArtifactError("Release artifact source cannot be read") from error
        if supplied_source.is_symlink() or not source.is_file() or source.is_symlink():
            raise ReleaseArtifactError("Release artifact source is not a regular file")
        if source.stat().st_size <= 0:
            raise ReleaseArtifactError("Release artifact source is empty")
        name = _safe_leaf(source.name)
        if source.suffix.casefold() != expected_suffix:
            raise ReleaseArtifactError("Release artifact extension does not match its kind")
        folded = name.casefold()
        if folded in names:
            raise ReleaseArtifactError("Release artifact names collide")
        if artifact.kind in kinds:
            raise ReleaseArtifactError("Release artifact kinds collide")
        names.add(folded)
        kinds.add(artifact.kind)
        validated.append(ReleaseArtifact(source=source, kind=artifact.kind))
    if not _REQUIRED_ARTIFACT_KINDS.issubset(kinds):
        raise ReleaseArtifactError("Release must contain exactly one NSIS installer")
    return tuple(sorted(validated, key=lambda item: item.source.name.casefold()))


def _validated_mapping(
    value: Mapping[str, str], *, name: str, exact_keys: frozenset[str] | None = None
) -> dict[str, str]:
    projected = dict(value)
    if exact_keys is not None and frozenset(projected) != exact_keys:
        raise ReleaseArtifactError(f"Release {name} keys are incomplete or unknown")
    if not projected or any(
        not isinstance(key, str)
        or not key
        or not isinstance(item, str)
        or not item
        or item != item.strip()
        for key, item in projected.items()
    ):
        raise ReleaseArtifactError(f"Release {name} is invalid")
    return dict(sorted(projected.items()))


def _manifest_mapping(
    value: object, *, name: str, exact_keys: frozenset[str] | None = None
) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ReleaseArtifactError(f"Release {name} is invalid")
    return _validated_mapping(
        cast(Mapping[str, str], value),
        name=name,
        exact_keys=exact_keys,
    )


def _checksums_bytes(directory: Path, names: Sequence[str]) -> bytes:
    lines = [
        f"{_sha256_file(directory / name)}  {name}" for name in sorted(names, key=str.casefold)
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def read_dependency_notices(path: Path, *, expected_sha256: str | None = None) -> bytes:
    """Read exact supplied UTF-8 notice bytes; reject empty text and redirected paths."""

    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256)
    ):
        raise ReleaseArtifactError("Dependency notice SHA-256 pin is invalid")
    try:
        resolved = path.resolve(strict=True)
        if path.is_symlink() or resolved != path.absolute() or not resolved.is_file():
            raise ReleaseArtifactError("Dependency notice path is not a regular unredirected file")
        content = resolved.read_bytes()
        text = content.decode("utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise ReleaseArtifactError("Dependency notices cannot be read as UTF-8 text") from error
    if not text.strip() or any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        raise ReleaseArtifactError("Dependency notices are empty or unreadable text")
    if expected_sha256 is not None and hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ReleaseArtifactError("Dependency notices do not match the SHA-256 pin")
    return content


def read_notice_companions(
    directory: Path, *, include_covered_source: bool = False
) -> dict[str, bytes]:
    """Read pinned original notices and the source files claimed by the notice."""

    values = {
        name: read_dependency_notices(directory / Path(name).name, expected_sha256=digest)
        for name, digest in RUST_STDLIB_NOTICE_SHA256.items()
    }
    if include_covered_source:
        try:
            values.update(read_covered_source_companions(directory))
        except CoveredSourceError as error:
            raise ReleaseArtifactError(str(error)) from error
    return values


def _manifest_dependency_notices(directory: Path, value: object) -> bytes:
    """Require the single selected notice path and its existing size/hash conventions."""

    if not isinstance(value, dict) or frozenset(value) not in (
        _NOTICE_KEYS,
        _NOTICE_KEYS | {"companions"},
    ):
        raise ReleaseArtifactError("Release dependency notice record is invalid")
    if value.get("path") != DEPENDENCY_NOTICES_PATH:
        raise ReleaseArtifactError("Release dependency notice path is invalid")
    digest = value.get("sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ReleaseArtifactError("Release dependency notice hash is invalid")
    content = read_dependency_notices(directory / DEPENDENCY_NOTICES_PATH, expected_sha256=digest)
    size = value.get("size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size != len(content):
        raise ReleaseArtifactError("Release dependency notice size does not match the manifest")
    if "companions" in value:
        expected = read_notice_companions(
            directory / "licenses", include_covered_source=covered_source_required(content)
        )
        records = [
            {"path": name, "sha256": hashlib.sha256(blob).hexdigest(), "size_bytes": len(blob)}
            for name, blob in expected.items()
        ]
        if value["companions"] != records:
            raise ReleaseArtifactError(
                "Release notice companion records do not match the pinned originals"
            )
    elif covered_source_required(content):
        raise ReleaseArtifactError("Claimed covered-source supplement is missing from the release")
    return content


def assemble_release_layout(
    *,
    output: Path,
    artifacts: Sequence[ReleaseArtifact],
    provenance: Mapping[str, str],
    toolchain: Mapping[str, str],
    qualification: Mapping[str, str],
    license_path: Path,
    dependency_notices_path: Path,
    dependency_notices_sha256: str,
    dependency_notice_companions_directory: Path | None = None,
    version: str = PRERELEASE_VERSION,
) -> dict[str, object]:
    """Atomically assemble one canonical unsigned prerelease directory."""

    _require_prerelease_version(version)
    if version != PRERELEASE_VERSION:
        raise ReleaseArtifactError("Release prerelease version does not match the application")
    selected = _validated_artifacts(artifacts)
    source = _validated_mapping(
        provenance,
        name="provenance",
        exact_keys=_PROVENANCE_KEYS,
    )
    if not _GIT_ID_RE.fullmatch(source["source_commit"]) or not _GIT_ID_RE.fullmatch(
        source["source_tree"]
    ):
        raise ReleaseArtifactError("Release source identity is invalid")
    if not _UTC_RE.fullmatch(source["source_timestamp_utc"]):
        raise ReleaseArtifactError("Release source timestamp is invalid")
    tools = _validated_mapping(toolchain, name="toolchain", exact_keys=_TOOLCHAIN_KEYS)
    qualified = _validated_mapping(
        qualification,
        name="qualification",
        exact_keys=_QUALIFICATION_KEYS,
    )
    if any(status not in _QUALIFICATION_STATES for status in qualified.values()):
        raise ReleaseArtifactError("Release qualification state is invalid")
    try:
        source_license = license_path.resolve(strict=True)
    except OSError as error:
        raise ReleaseArtifactError("Release license cannot be read") from error
    if license_path.is_symlink() or not source_license.is_file() or source_license.is_symlink():
        raise ReleaseArtifactError("Release license is not a regular file")
    if not isinstance(dependency_notices_sha256, str) or not _SHA256_RE.fullmatch(
        dependency_notices_sha256
    ):
        raise ReleaseArtifactError("Dependency notice SHA-256 pin is invalid")
    notice_bytes = read_dependency_notices(
        dependency_notices_path,
        expected_sha256=dependency_notices_sha256,
    )

    companions = (
        read_notice_companions(
            dependency_notice_companions_directory,
            include_covered_source=covered_source_required(notice_bytes),
        )
        if dependency_notice_companions_directory is not None
        else {}
    )
    if covered_source_required(notice_bytes) and not companions:
        raise ReleaseArtifactError("Claimed covered-source supplement has no companion directory")
    output = output.resolve()
    _safe_leaf(output.name)
    if output.exists():
        raise ReleaseArtifactError("Release output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        records: list[dict[str, object]] = []
        for artifact in selected:
            destination = staging / artifact.source.name
            shutil.copyfile(artifact.source, destination)
            records.append(
                {
                    "kind": artifact.kind,
                    "name": destination.name,
                    "sha256": _sha256_file(destination),
                    "size_bytes": destination.stat().st_size,
                }
            )
        shutil.copyfile(source_license, staging / "LICENSE")
        notice_destination = staging / DEPENDENCY_NOTICES_PATH
        notice_destination.parent.mkdir()
        notice_destination.write_bytes(notice_bytes)
        for name, blob in companions.items():
            (staging / name).write_bytes(blob)
        (staging / "UNSIGNED.txt").write_text(UNSIGNED_DISCLOSURE + "\n", encoding="utf-8")
        manifest: dict[str, object] = {
            "artifacts": records,
            "dependency_notices": {
                "path": DEPENDENCY_NOTICES_PATH,
                "sha256": dependency_notices_sha256,
                "size_bytes": len(notice_bytes),
            },
            "format_id": RELEASE_FORMAT_ID,
            "format_version": RELEASE_FORMAT_VERSION,
            "product": {**_PRODUCT, "version": version},
            "provenance": source,
            "qualification": qualified,
            "signing": _SIGNING,
            "toolchain": tools,
        }
        if companions:
            cast(dict[str, object], manifest["dependency_notices"])["companions"] = [
                {"path": name, "sha256": hashlib.sha256(blob).hexdigest(), "size_bytes": len(blob)}
                for name, blob in companions.items()
            ]
        (staging / "release-manifest.json").write_bytes(_canonical_json_bytes(manifest))
        checksummed_names = [
            *(cast(str, record["name"]) for record in records),
            "LICENSE",
            DEPENDENCY_NOTICES_PATH,
            *companions,
            "UNSIGNED.txt",
            "release-manifest.json",
        ]
        (staging / "SHA256SUMS.txt").write_bytes(_checksums_bytes(staging, checksummed_names))
        os.replace(staging, output)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_release_layout(directory: Path) -> dict[str, object]:
    """Verify every declared byte and reject unlisted release-layout files."""

    try:
        resolved_directory = directory.resolve(strict=True)
    except OSError as error:
        raise ReleaseArtifactError("Release layout cannot be read") from error
    if (
        directory.is_symlink()
        or resolved_directory != directory.absolute()
        or not resolved_directory.is_dir()
    ):
        raise ReleaseArtifactError("Release layout is not a regular directory")
    directory = resolved_directory
    manifest_path = directory / "release-manifest.json"
    try:
        raw_manifest = manifest_path.read_bytes()
        manifest = json.loads(raw_manifest)
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseArtifactError("Release manifest cannot be read") from error
    if not isinstance(manifest, dict) or raw_manifest != _canonical_json_bytes(manifest):
        raise ReleaseArtifactError("Release manifest is not canonical")
    if frozenset(manifest) != _MANIFEST_KEYS:
        raise ReleaseArtifactError("Release manifest fields are incomplete or unknown")
    if manifest.get("format_id") != RELEASE_FORMAT_ID or manifest.get("format_version") not in (
        2,
        RELEASE_FORMAT_VERSION,
    ):
        raise ReleaseArtifactError("Release manifest format is unsupported")
    product = manifest.get("product")
    if product != _PRODUCT:
        raise ReleaseArtifactError("Release manifest version does not match the application")
    if manifest.get("signing") != _SIGNING:
        raise ReleaseArtifactError("Release signing disclosure is invalid")
    source = _manifest_mapping(
        manifest.get("provenance"),
        name="provenance",
        exact_keys=_PROVENANCE_KEYS,
    )
    if not _GIT_ID_RE.fullmatch(source["source_commit"]) or not _GIT_ID_RE.fullmatch(
        source["source_tree"]
    ):
        raise ReleaseArtifactError("Release source identity is invalid")
    if not _UTC_RE.fullmatch(source["source_timestamp_utc"]):
        raise ReleaseArtifactError("Release source timestamp is invalid")
    qualified = _manifest_mapping(
        manifest.get("qualification"),
        name="qualification",
        exact_keys=_QUALIFICATION_KEYS,
    )
    if any(status not in _QUALIFICATION_STATES for status in qualified.values()):
        raise ReleaseArtifactError("Release qualification state is invalid")
    _manifest_mapping(
        manifest.get("toolchain"),
        name="toolchain",
        exact_keys=_TOOLCHAIN_KEYS,
    )
    records = manifest.get("artifacts")
    if not isinstance(records, list) or not records:
        raise ReleaseArtifactError("Release manifest has no artifacts")
    _manifest_dependency_notices(directory, manifest.get("dependency_notices"))
    expected_names = {
        "LICENSE",
        "SHA256SUMS.txt",
        "UNSIGNED.txt",
        "release-manifest.json",
        DEPENDENCY_NOTICES_PATH,
    }
    notice_record = cast(dict[str, object], manifest["dependency_notices"])
    if "companions" in notice_record:
        if manifest["format_version"] != RELEASE_FORMAT_VERSION:
            raise ReleaseArtifactError("Notice companions require release format 3")
        expected_names.update(
            cast(str, row["path"])
            for row in cast(list[dict[str, object]], notice_record["companions"])
        )
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or frozenset(record) != _ARTIFACT_KEYS:
            raise ReleaseArtifactError("Release artifact record is invalid")
        record_name = record.get("name")
        if not isinstance(record_name, str):
            raise ReleaseArtifactError("Release artifact name is invalid")
        name = _safe_leaf(record_name)
        if name.casefold() in seen:
            raise ReleaseArtifactError("Release artifact names collide")
        seen.add(name.casefold())
        expected_names.add(name)
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise ReleaseArtifactError("Release artifact is missing")
        expected_size = record.get("size_bytes")
        if (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size <= 0
            or path.stat().st_size != expected_size
        ):
            raise ReleaseArtifactError("Release artifact size does not match the manifest")
        expected_sha256 = record.get("sha256")
        if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256):
            raise ReleaseArtifactError("Release artifact hash is invalid")
        if _sha256_file(path) != expected_sha256:
            raise ReleaseArtifactError("Release artifact hash does not match the manifest")
        kind = record.get("kind")
        if not isinstance(kind, str) or kind not in _ARTIFACT_SUFFIX:
            raise ReleaseArtifactError("Release artifact kind is invalid")
        if path.suffix.casefold() != _ARTIFACT_SUFFIX[kind]:
            raise ReleaseArtifactError("Release artifact kind is invalid")
    if not _REQUIRED_ARTIFACT_KINDS.issubset({cast(str, record["kind"]) for record in records}):
        raise ReleaseArtifactError("Release must contain exactly one NSIS installer")
    if [cast(str, record["name"]) for record in records] != sorted(
        (cast(str, record["name"]) for record in records), key=str.casefold
    ):
        raise ReleaseArtifactError("Release artifact records are not sorted")
    actual_names: set[str] = set()
    for path in directory.iterdir():
        if path.is_symlink() or path.resolve() != path.absolute():
            raise ReleaseArtifactError("Release layout contains redirected paths")
        if path.name == "licenses" and path.is_dir():
            for notice in path.iterdir():
                if (
                    notice.is_symlink()
                    or not notice.is_file()
                    or notice.resolve() != notice.absolute()
                ):
                    raise ReleaseArtifactError("Release layout contains undeclared files")
                actual_names.add(notice.relative_to(directory).as_posix())
        elif path.is_file():
            actual_names.add(path.name)
        else:
            raise ReleaseArtifactError("Release layout contains undeclared files")
    if actual_names != expected_names:
        raise ReleaseArtifactError("Release layout contains undeclared files")
    if (directory / "UNSIGNED.txt").read_text(encoding="utf-8") != UNSIGNED_DISCLOSURE + "\n":
        raise ReleaseArtifactError("Release unsigned disclosure does not match")
    checksum_names = sorted(expected_names - {"SHA256SUMS.txt"}, key=str.casefold)
    if (directory / "SHA256SUMS.txt").read_bytes() != _checksums_bytes(directory, checksum_names):
        raise ReleaseArtifactError("Release checksums do not match the layout")
    return cast(dict[str, object], manifest)


def assemble_qualified_release_layout(
    *,
    source: Path,
    output: Path,
    qualification: Mapping[str, str],
) -> dict[str, object]:
    """Copy verified installer bytes into a new layout with completed qualification state."""

    manifest = verify_release_layout(source)
    source = source.resolve(strict=True)
    records = cast(list[dict[str, object]], manifest["artifacts"])
    artifacts = tuple(
        ReleaseArtifact(
            source=source / cast(str, record["name"]),
            kind=cast(str, record["kind"]),
        )
        for record in records
    )
    product = cast(dict[str, object], manifest["product"])
    return assemble_release_layout(
        output=output,
        artifacts=artifacts,
        provenance=cast(Mapping[str, str], manifest["provenance"]),
        toolchain=cast(Mapping[str, str], manifest["toolchain"]),
        qualification=qualification,
        license_path=source / "LICENSE",
        dependency_notices_path=source / DEPENDENCY_NOTICES_PATH,
        dependency_notices_sha256=cast(dict[str, str], manifest["dependency_notices"])["sha256"],
        dependency_notice_companions_directory=(
            source / "licenses"
            if "companions" in cast(dict[str, object], manifest["dependency_notices"])
            else None
        ),
        version=cast(str, product["version"]),
    )
