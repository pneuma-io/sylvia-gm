"""Build and assemble the exact local unsigned Sylvia GM Windows prerelease."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from sylvia_gm.build_workspace import BuildWorkspace
from sylvia_gm.covered_source_delivery import (
    COVERED_SOURCE_DELIVERY_SHA256,
    covered_source_required,
)
from sylvia_gm.portable_artifact import assemble_portable_zip, portable_payloads
from sylvia_gm.release_artifact import (
    DEPENDENCY_NOTICES_PATH,
    DEPENDENCY_NOTICES_SOURCE,
    PRERELEASE_VERSION,
    RUST_STDLIB_NOTICE_SHA256,
    WINDOWS_INSTALLER_VERSION,
    ReleaseArtifact,
    ReleaseArtifactError,
    assemble_release_layout,
    read_dependency_notices,
    read_notice_companions,
    validate_version_sources,
    verify_release_layout,
)

if __package__:
    from .check_release_identity import check_source
else:
    from check_release_identity import check_source

TAURI_WINDOWS_VERSION_OVERRIDE = json.dumps(
    {"version": WINDOWS_INSTALLER_VERSION},
    separators=(",", ":"),
)
TAURI_BUILD_COMMAND = (
    "cargo",
    "tauri",
    "build",
    "--ci",
    "--no-sign",
    "--bundles",
    "nsis",
    "--config",
    TAURI_WINDOWS_VERSION_OVERRIDE,
)
LOCK_CHECK_COMMANDS = (
    ("uv", "lock", "--check"),
    (
        "cargo",
        "metadata",
        "--locked",
        "--format-version",
        "1",
        "--no-deps",
        "--manifest-path",
        "src-tauri/Cargo.toml",
    ),
)
_QUALIFICATION_STATES = ("pending", "passed", "failed", "not_available")


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> str:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            check=True,
            capture_output=capture,
            text=True,
            env=env,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseArtifactError(f"Release command failed: {command[0]}") from error
    return completed.stdout.strip() if capture else ""


def _require_clean_tree(root: Path) -> None:
    status = _run(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=root,
        capture=True,
    )
    if status:
        raise ReleaseArtifactError(
            "Windows prerelease builds require an exact clean committed worktree"
        )


def _source_identity(root: Path) -> dict[str, str]:
    commit = _run(("git", "rev-parse", "HEAD"), cwd=root, capture=True)
    tree = _run(("git", "rev-parse", "HEAD^{tree}"), cwd=root, capture=True)
    timestamp = _run(
        ("git", "show", "-s", "--format=%cI", "HEAD"),
        cwd=root,
        capture=True,
    )
    try:
        utc_timestamp = (
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            .astimezone(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except ValueError as error:
        raise ReleaseArtifactError("Git returned an invalid source timestamp") from error
    return {
        "source_commit": commit,
        "source_tree": tree,
        "source_timestamp_utc": utc_timestamp,
    }


def _source_input_manifest(root: Path) -> dict[str, str]:
    paths = _run(("git", "ls-files", "-z"), cwd=root, capture=True).split("\0")
    inputs = {}
    for relative in filter(None, paths):
        path = root / relative
        if path.resolve(strict=True) != path.absolute() or not path.resolve().is_relative_to(root):
            raise ReleaseArtifactError("Release source input is an alias or outside the checkout")
        inputs[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not inputs:
        raise ReleaseArtifactError("Release source input manifest is empty")
    return inputs


def _toolchain(root: Path) -> dict[str, str]:
    return {
        "cargo": _run(("cargo", "--version"), cwd=root, capture=True),
        "mcp": importlib.metadata.version("mcp"),
        "pyinstaller": importlib.metadata.version("pyinstaller"),
        "python": (
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        "rustc": _run(("rustc", "--version"), cwd=root, capture=True),
        "tauri_cli": _run(("cargo", "tauri", "--version"), cwd=root, capture=True),
        "uv": _run(("uv", "--version"), cwd=root, capture=True),
    }


def _nsis_bundle_directory(root: Path) -> Path:
    root = root.resolve(strict=True)
    base = (root / "src-tauri" / "target" / "release" / "bundle").resolve()
    if not base.is_relative_to(root):
        raise ReleaseArtifactError("Bundle directory escapes the repository")
    return base / "nsis"


def discover_installer_artifacts(
    root: Path, *, target: Path | None = None
) -> tuple[ReleaseArtifact, ...]:
    directory = _nsis_bundle_directory(root) if target is None else target / "release/bundle/nsis"
    matches = sorted(directory.glob("*.exe"))
    if len(matches) != 1:
        raise ReleaseArtifactError("Tauri must produce exactly one NSIS installer")
    return (ReleaseArtifact(source=matches[0], kind="nsis-installer"),)


def build_windows_prerelease(
    *,
    root: Path,
    output: Path,
    windows_10: str,
    windows_11: str,
    dependency_notices_sha256: str,
    forbidden_name_file: Path | None = None,
) -> dict[str, object]:
    root = root.resolve(strict=True)
    output = output.resolve()
    if not isinstance(dependency_notices_sha256, str):
        raise ReleaseArtifactError("Dependency notice SHA-256 pin is invalid")
    notice_path = root / DEPENDENCY_NOTICES_SOURCE
    notice_bytes = read_dependency_notices(notice_path, expected_sha256=dependency_notices_sha256)
    resources = json.loads((root / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))[
        "bundle"
    ]["resources"]
    if (
        resources.get(f"../{DEPENDENCY_NOTICES_SOURCE}") != DEPENDENCY_NOTICES_PATH
        or list(resources.values()).count(DEPENDENCY_NOTICES_PATH) != 1
    ):
        raise ReleaseArtifactError("Tauri dependency notice resource mapping is invalid")
    for name in RUST_STDLIB_NOTICE_SHA256:
        if resources.get("../" + name) != name or list(resources.values()).count(name) != 1:
            raise ReleaseArtifactError("Tauri notice companion resource mapping is invalid")
    if covered_source_required(notice_bytes):
        for name in COVERED_SOURCE_DELIVERY_SHA256:
            if resources.get("../" + name) != name or list(resources.values()).count(name) != 1:
                raise ReleaseArtifactError("Tauri covered-source resource mapping is invalid")
    companions = read_notice_companions(
        root / "licenses", include_covered_source=covered_source_required(notice_bytes)
    )
    validate_version_sources(root)
    _require_clean_tree(root)
    if output.exists():
        raise ReleaseArtifactError("Release output already exists")
    if forbidden_name_file is None:
        raise ReleaseArtifactError("Release builds require the external literal-name list")
    identity_check = check_source(root, forbidden_name_file)
    if not identity_check["passed"]:
        raise ReleaseArtifactError("Exact release identity check failed")
    source_files = _source_input_manifest(root)
    for command in LOCK_CHECK_COMMANDS:
        _run(command, cwd=root, capture=True)
    provenance = _source_identity(root)
    toolchain = _toolchain(root)
    invocation = BuildWorkspace.create(root / ".owner-build")
    target = invocation.path / "target"
    environment = dict(os.environ, CARGO_TARGET_DIR=str(target))
    # Build the sidecar with the interpreter recorded by this build driver.
    environment["UV_PYTHON"] = sys.executable
    # Rust panic locations must not embed the builder's personal source/registry paths.
    rust_flags = environment.get("CARGO_ENCODED_RUSTFLAGS", "")
    if not rust_flags and environment.get("RUSTFLAGS", "").strip():
        raise ReleaseArtifactError("Supply existing Rust flags through CARGO_ENCODED_RUSTFLAGS")
    remaps = ((Path.home(), "/build-home"), (root, "/sylvia/source"))
    environment["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(
        [flag for flag in rust_flags.split("\x1f") if flag]
        + [f"--remap-path-prefix={path}={replacement}" for path, replacement in remaps]
        + [f"--remap-path-prefix={path.as_posix()}={replacement}" for path, replacement in remaps]
        + ["-C", "link-arg=/PDBALTPATH:%_PDB%"]
    )
    print(f"Isolated release build work (retained on failure): {invocation.path}", flush=True)
    _run(TAURI_BUILD_COMMAND, cwd=root / "src-tauri", env=environment)
    final_notice_bytes = read_dependency_notices(
        notice_path,
        expected_sha256=dependency_notices_sha256,
    )
    if final_notice_bytes != notice_bytes:
        raise ReleaseArtifactError("Dependency notice input changed during the build")
    if (
        read_notice_companions(
            root / "licenses", include_covered_source=covered_source_required(notice_bytes)
        )
        != companions
    ):
        raise ReleaseArtifactError("Notice companion input changed during the build")
    if _source_input_manifest(root) != source_files:
        raise ReleaseArtifactError("Release source inputs changed during the build")
    portable = invocation.path / f"sylvia-gm-{PRERELEASE_VERSION}-windows-x86_64-portable.zip"
    assemble_portable_zip(
        portable,
        payloads=portable_payloads(
            root,
            desktop=target / "release/sylvia-gm.exe",
            owner=root / "src-tauri/binaries/sylvia-gm-owner-x86_64-pc-windows-msvc.exe",
        ),
        source_files=source_files,
        provenance=provenance,
        toolchain=toolchain,
    )
    artifacts = (
        *discover_installer_artifacts(root, target=target),
        ReleaseArtifact(portable, "portable-exe-zip"),
    )
    manifest = assemble_release_layout(
        output=output,
        artifacts=artifacts,
        provenance=provenance,
        toolchain=toolchain,
        qualification={
            "windows_10": windows_10,
            "windows_11": windows_11,
        },
        license_path=root / "LICENSE",
        dependency_notices_path=notice_path,
        dependency_notices_sha256=dependency_notices_sha256,
        dependency_notice_companions_directory=root / "licenses",
    )
    if verify_release_layout(output) != manifest:
        raise ReleaseArtifactError("Assembled release did not verify exactly")
    receipt = invocation.finish(
        output / "release-manifest.json", keep=os.environ.get("SYLVIA_KEEP_OWNER_BUILD") == "1"
    )
    print(f"Invocation release work: {receipt['cleanup']}", flush=True)
    return manifest


def _parser(root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--forbidden-name-file",
        type=Path,
        required=True,
        help="External plain-text literal-name list used by the exact release gate",
    )
    parser.add_argument(
        "--dependency-notices-sha256",
        required=True,
        help="SHA-256 of the reconciled repository-root dependency-notices.txt input",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "dist" / f"sylvia-gm-{PRERELEASE_VERSION}-windows-x86_64",
    )
    parser.add_argument(
        "--windows-10",
        choices=_QUALIFICATION_STATES,
        default="pending",
    )
    parser.add_argument(
        "--windows-11",
        choices=_QUALIFICATION_STATES,
        default="pending",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    arguments = _parser(root).parse_args(argv)
    manifest = build_windows_prerelease(
        root=root,
        output=arguments.output,
        windows_10=arguments.windows_10,
        windows_11=arguments.windows_11,
        dependency_notices_sha256=arguments.dependency_notices_sha256,
        forbidden_name_file=arguments.forbidden_name_file,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
