"""Assemble the portable EXE recipe from explicit immutable release inputs."""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from collections.abc import Mapping
from pathlib import Path

from sylvia_gm.domain.package_library import validate_portable_path
from sylvia_gm.release_artifact import ReleaseArtifactError

DESKTOP = "Sylvia GM.exe"
OWNER = "sylvia-gm-owner.exe"
MARKER = "sylvia-portable.json"
ENTRY_MANIFEST = "ENTRY-HASHES.json"
LEGAL_FILES = {"LICENSE", "COPYRIGHT", "LICENSING-STATUS.md", "SYLVIA-ADDITIONAL-PERMISSIONS.md"}
REQUIRED_PAYLOADS = {
    DESKTOP,
    OWNER,
    MARKER,
    "README-PORTABLE.txt",
    "SRD-Lite.sylvia-gm-package",
    "Severed-Lands.sylvia-gm-package",
    *LEGAL_FILES,
    *("licenses/" + name for name in LEGAL_FILES),
    "licenses/SRD-5.2.1.md",
    "licenses/Severed-Lands.md",
    "licenses/dependency-notices.txt",
}


def _validate_payload_path(name: str, *, metadata: bool = False) -> None:
    allowed = REQUIRED_PAYLOADS | (
        {ENTRY_MANIFEST, "BUILD-IDENTITY.json", "SOURCE-INPUTS.json"} if metadata else set()
    )
    if name not in allowed and not name.startswith("licenses/"):
        raise ReleaseArtifactError("Unexpected portable payload; mutable data is never packaged")
    try:
        validate_portable_path(name)
    except ValueError as error:
        raise ReleaseArtifactError("Unsafe portable payload path") from error


PORTABLE_README = b"""Sylvia GM portable for Windows 11

Extract the complete ZIP into a writable local folder, then open Sylvia GM.exe.
No Python installation, CMD launcher or installer is required. Microsoft Edge
WebView2 must be available. This build is unsigned; verify its release hashes.

Keep the EXE, owner sidecar, portable marker and supplied notices together.
The marker selects adjacent isolated-data for campaigns, settings, WebView data
and temporary files. The ZIP contains no mutable user data or credentials.

Close Sylvia before moving or replacing it. Move the complete folder, including
isolated-data, to keep your campaigns. For an update, preserve isolated-data and
replace only the supplied application files. Keep live data on a local disk.

Installed and portable copies select different datasets and may coexist.
Opening the same dataset twice is refused by its existing owner lock; close the
other copy first. An ordinary installed relaunch focuses its installed window.

Protected provider credentials are tied to the Windows user through DPAPI.
On another Windows account, re-enter credentials; moving the folder does not
make protected credentials portable. Never copy credentials into diagnostics.

The included package archives are also available through Settings > Packages.
ChatGPT account connections require a separately installed supported Codex runtime.
Source and assembly checks do not establish clean-Windows or live-provider acceptance.
"""


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _source_file(root: Path, relative: str) -> bytes:
    path = root / relative
    if path.resolve(strict=True) != path.absolute() or not path.resolve().is_relative_to(root):
        raise ReleaseArtifactError("Portable input is an alias or outside source")
    if not path.is_file():
        raise ReleaseArtifactError("Portable input is not a regular file")
    return path.read_bytes()


def portable_payloads(root: Path, *, desktop: Path, owner: Path) -> dict[str, bytes]:
    """Use explicit binaries, declared notices and current bundled closures; never walk user data."""
    from sylvia_gm.application.bundled_packages import bundled_rift_archive, bundled_srd_archive

    root = root.resolve(strict=True)
    for path in (desktop, owner):
        if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
            raise ReleaseArtifactError("Portable executable must be a real immutable build output")
        if not path.read_bytes().startswith(b"MZ"):
            raise ReleaseArtifactError("Portable executable is not a Windows PE input")
    files = {
        DESKTOP: desktop.read_bytes(),
        OWNER: owner.read_bytes(),
        MARKER: b'{"version":1}\n',
        "README-PORTABLE.txt": PORTABLE_README,
        "SRD-Lite.sylvia-gm-package": bundled_srd_archive(),
        "Severed-Lands.sylvia-gm-package": bundled_rift_archive(),
    }
    resources = json.loads(_source_file(root, "src-tauri/tauri.conf.json"))["bundle"]["resources"]
    for source, destination in resources.items():
        supplied = root / "src-tauri" / source
        source_path = supplied.resolve(strict=True)
        if source_path != Path(os.path.abspath(supplied)):
            raise ReleaseArtifactError("Portable notice input must not be an alias")
        if not source_path.is_relative_to(root) or not destination.startswith("licenses/"):
            raise ReleaseArtifactError("Portable resources must be declared contained notices")
        relative = source_path.relative_to(root).as_posix()
        if (
            Path(destination).is_absolute()
            or ".." in Path(destination).parts
            or destination in files
        ):
            raise ReleaseArtifactError("Portable notice destinations are invalid or duplicate")
        files[destination] = _source_file(root, relative)
    for name in ("LICENSE", "COPYRIGHT", "LICENSING-STATUS.md", "SYLVIA-ADDITIONAL-PERMISSIONS.md"):
        files[name] = _source_file(root, name)
        if files.get("licenses/" + name) != files[name]:
            raise ReleaseArtifactError("Portable software notices are incomplete")
    for name in (
        "licenses/SRD-5.2.1.md",
        "licenses/Severed-Lands.md",
        "licenses/dependency-notices.txt",
    ):
        if not files.get(name):
            raise ReleaseArtifactError(
                "Portable required content/dependency attribution is missing"
            )
    return files


def assemble_portable_zip(
    output: Path,
    *,
    payloads: Mapping[str, bytes],
    source_files: Mapping[str, str],
    provenance: Mapping[str, str],
    toolchain: Mapping[str, str],
) -> dict[str, object]:
    files = dict(payloads)
    if (
        not REQUIRED_PAYLOADS <= files.keys()
        or files[MARKER] != b'{"version":1}\n'
        or not source_files
    ):
        raise ReleaseArtifactError("Portable payload or source inventory is incomplete")
    for name in files:
        _validate_payload_path(name)
    files["SOURCE-INPUTS.json"] = _json(dict(source_files))
    identity = {
        "format": 1,
        "product": "Sylvia GM",
        "entry_point": DESKTOP,
        "portable_marker": MARKER,
        "data_directory": "isolated-data",
        "mutable_data_packaged": False,
        "source": dict(provenance),
        "source_manifest_sha256": _digest(files["SOURCE-INPUTS.json"]),
        "source_inputs": len(source_files),
        "toolchain": dict(toolchain),
        "shipping_profile": "Cargo default optimized release",
        "desktop_sha256": _digest(files[DESKTOP]),
        "owner_sha256": _digest(files[OWNER]),
    }
    files["BUILD-IDENTITY.json"] = _json(identity)
    files[ENTRY_MANIFEST] = _json(
        {name: {"sha256": _digest(raw), "bytes": len(raw)} for name, raw in sorted(files.items())}
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, raw in sorted(files.items()):
            member = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            archive.writestr(member, raw)
    if verify_portable_zip(output) != identity:
        raise ReleaseArtifactError("Portable archive identity changed during assembly")
    return identity


def verify_portable_zip(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names) or archive.testzip() is not None:
            raise ReleaseArtifactError("Portable archive inventory/CRC is invalid")
        for name in names:
            _validate_payload_path(name, metadata=True)
        if not REQUIRED_PAYLOADS <= set(names):
            raise ReleaseArtifactError("Portable archive required payload is incomplete")
        entries = json.loads(archive.read(ENTRY_MANIFEST))
        if set(names) != set(entries) | {ENTRY_MANIFEST}:
            raise ReleaseArtifactError("Portable archive inventory does not match its manifest")
        for name, record in entries.items():
            raw = archive.read(name)
            if record != {"sha256": _digest(raw), "bytes": len(raw)}:
                raise ReleaseArtifactError("Portable payload does not match its hash manifest")
        identity = json.loads(archive.read("BUILD-IDENTITY.json"))
        for key, name in (("desktop_sha256", DESKTOP), ("owner_sha256", OWNER)):
            if identity[key] != _digest(archive.read(name)):
                raise ReleaseArtifactError("Portable executable identity is stale")
        if identity["source_manifest_sha256"] != _digest(archive.read("SOURCE-INPUTS.json")):
            raise ReleaseArtifactError("Portable source identity is stale")
        marker = json.loads(archive.read(MARKER))
        if marker != {"version": 1} or type(marker.get("version")) is not int:
            raise ReleaseArtifactError("Portable storage marker is invalid")
        return identity
