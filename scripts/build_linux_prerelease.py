"""Build and assemble the exact local unsigned Sylvia GM Linux prerelease (one AppImage)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from sylvia_gm.build_workspace import BuildWorkspace
from sylvia_gm.covered_source_delivery import (
    COVERED_SOURCE_DELIVERY_SHA256,
    covered_source_required,
)
from sylvia_gm.release_artifact import (
    DEPENDENCY_NOTICES_PATH,
    DEPENDENCY_NOTICES_SOURCE,
    PRERELEASE_VERSION,
    RUST_STDLIB_NOTICE_SHA256,
    ReleaseArtifact,
    ReleaseArtifactError,
    assemble_release_layout,
    linux_release_platform,
    read_dependency_notices,
    read_notice_companions,
    validate_version_sources,
    verify_release_layout,
)

if __package__:
    from . import build_linux_bundled_notices as bundled
    from . import build_windows_prerelease as shared
    from .check_release_identity import check_source
else:
    import build_linux_bundled_notices as bundled
    import build_windows_prerelease as shared
    from check_release_identity import check_source

TAURI_BUILD_COMMAND = (
    "cargo",
    "tauri",
    "build",
    "--ci",
    "--no-sign",
    "--bundles",
    "appimage",
)
# An AppImage carries the build machine's libraries and needs a C library at least as new as
# theirs. Building on Ubuntu 22.04 keeps the result usable on distributions of that age.
BASELINE_GLIBC = (2, 35)
BASELINE_SYSTEM = "Ubuntu 22.04"
_QUALIFICATION_STATES = ("pending", "passed", "failed", "not_available")


def _glibc_version() -> tuple[int, int]:
    """The C library this machine runs, which is what a bundle's libraries were built against."""

    try:
        # Not platform.libc_ver(): that reports what the interpreter was linked for.
        name, _, version = os.confstr("CS_GNU_LIBC_VERSION").partition(" ")  # type: ignore[union-attr]
    except (AttributeError, OSError, ValueError) as error:
        raise ReleaseArtifactError("The build machine's C library is not glibc") from error
    parts = version.split(".")
    if name != "glibc" or len(parts) < 2 or not all(part.isdigit() for part in parts[:2]):
        raise ReleaseArtifactError("The build machine's C library is not glibc")
    return int(parts[0]), int(parts[1])


def _build_system(os_release: Path = Path("/etc/os-release")) -> str:
    try:
        lines = os_release.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ReleaseArtifactError("The build machine does not describe its system") from error
    for line in lines:
        if line.startswith("PRETTY_NAME="):
            name = line.partition("=")[2].strip().strip('"').strip()
            if name:
                return name
    raise ReleaseArtifactError("The build machine does not describe its system")


def _appimage_tools_digest(cache: Path) -> str:
    """One hash over the bundling tools Tauri downloaded and ran, by name and content."""

    try:
        files = sorted(path for path in cache.iterdir() if path.is_file() and not path.is_symlink())
    except OSError as error:
        raise ReleaseArtifactError("The AppImage bundling tools were not kept") from error
    if not files:
        raise ReleaseArtifactError("The AppImage bundling tools were not kept")
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def discover_appdir(target: Path) -> Path:
    """The unpacked bundle Tauri leaves beside the AppImage it packed from it."""

    matches = sorted(
        path for path in (target / "release/bundle/appimage").glob("*.AppDir") if path.is_dir()
    )
    if len(matches) != 1:
        raise ReleaseArtifactError("Tauri must leave exactly one unpacked AppImage folder")
    return matches[0]


def discover_appimage(target: Path) -> ReleaseArtifact:
    matches = sorted((target / "release/bundle/appimage").glob("*.AppImage"))
    if len(matches) != 1:
        raise ReleaseArtifactError("Tauri must produce exactly one AppImage")
    return ReleaseArtifact(source=matches[0], kind="appimage")


def build_linux_prerelease(
    *,
    root: Path,
    output: Path,
    ubuntu_22_04: str,
    ubuntu_24_04: str,
    dependency_notices_sha256: str,
    forbidden_name_file: Path | None = None,
    allow_newer_glibc: bool = False,
) -> dict[str, object]:
    if not sys.platform.startswith("linux"):
        raise ReleaseArtifactError("Linux prerelease builds run on Linux")
    glibc = _glibc_version()
    if glibc > BASELINE_GLIBC and not allow_newer_glibc:
        raise ReleaseArtifactError(
            f"Build Linux releases on {BASELINE_SYSTEM} (glibc "
            f"{BASELINE_GLIBC[0]}.{BASELINE_GLIBC[1]}) so they run on older distributions; "
            f"this machine has glibc {glibc[0]}.{glibc[1]}"
        )
    root = root.resolve(strict=True)
    output = output.resolve()
    if not isinstance(dependency_notices_sha256, str):
        raise ReleaseArtifactError("Dependency notice SHA-256 pin is invalid")
    notice_path = root / DEPENDENCY_NOTICES_SOURCE
    notice_bytes = read_dependency_notices(notice_path, expected_sha256=dependency_notices_sha256)
    tauri_config = json.loads((root / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    resources = tauri_config["bundle"]["resources"]
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
    shared._require_clean_tree(root)
    if output.exists():
        raise ReleaseArtifactError("Release output already exists")
    if forbidden_name_file is None:
        raise ReleaseArtifactError("Release builds require the external literal-name list")
    identity_check = check_source(root, forbidden_name_file)
    if not identity_check["passed"]:
        raise ReleaseArtifactError("Exact release identity check failed")
    source_files = shared._source_input_manifest(root)
    for command in shared.LOCK_CHECK_COMMANDS:
        shared._run(command, cwd=root, capture=True)
    provenance = shared._source_identity(root)
    toolchain = shared._toolchain(root)
    invocation = BuildWorkspace.create(root / ".owner-build")
    target = invocation.path / "target"
    # The bundling tools are downloaded into this build's own folder, so the hash recorded
    # below describes exactly what this build ran and nothing left over from another.
    tools_cache = invocation.path / "cache"
    environment = dict(
        os.environ,
        CARGO_TARGET_DIR=str(target),
        XDG_CACHE_HOME=str(tools_cache),
        UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR")
        or str(Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "uv"),
    )
    # Build the sidecar with the interpreter recorded by this build driver.
    environment["UV_PYTHON"] = sys.executable
    # The bundling tools are AppImages themselves; run them without needing FUSE.
    environment["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    # Rust panic locations must not embed the builder's personal source/registry paths.
    rust_flags = environment.get("CARGO_ENCODED_RUSTFLAGS", "")
    if not rust_flags and environment.get("RUSTFLAGS", "").strip():
        raise ReleaseArtifactError("Supply existing Rust flags through CARGO_ENCODED_RUSTFLAGS")
    remaps = ((Path.home(), "/build-home"), (root, "/sylvia/source"))
    environment["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(
        [flag for flag in rust_flags.split("\x1f") if flag]
        + [f"--remap-path-prefix={path}={replacement}" for path, replacement in remaps]
    )
    print(f"Isolated release build work (retained on failure): {invocation.path}", flush=True)
    shared._run(TAURI_BUILD_COMMAND, cwd=root / "src-tauri", env=environment)
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
    if shared._source_input_manifest(root) != source_files:
        raise ReleaseArtifactError("Release source inputs changed during the build")
    # The notices for the system libraries this build copied into the AppImage, written from
    # the unpacked bundle and this machine's packages so they describe exactly this build.
    build_system = _build_system()
    bundled_notices = invocation.path / bundled.NOTICES_NAME
    try:
        packages = bundled.write_notices(
            discover_appdir(target),
            bundled_notices,
            own_folder=str(tauri_config["productName"]),
            build_system=build_system,
        )
    except bundled.BundledNoticesError as error:
        raise ReleaseArtifactError(str(error)) from error
    print(f"Bundled library notices: {len(packages)} packages", flush=True)
    release_platform = linux_release_platform()
    manifest = assemble_release_layout(
        output=output,
        artifacts=(discover_appimage(target),),
        provenance=provenance,
        toolchain={
            **toolchain,
            "appimage_tools_sha256": _appimage_tools_digest(tools_cache / "tauri"),
            "build_system": build_system,
            "glibc": f"{glibc[0]}.{glibc[1]}",
        },
        qualification={
            "ubuntu_22_04": ubuntu_22_04,
            "ubuntu_24_04": ubuntu_24_04,
        },
        license_path=root / "LICENSE",
        dependency_notices_path=notice_path,
        dependency_notices_sha256=dependency_notices_sha256,
        dependency_notice_companions_directory=root / "licenses",
        platform=release_platform,
        bundled_notices_path=bundled_notices,
    )
    if verify_release_layout(output, release_platform) != manifest:
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
        default=root / "dist" / f"sylvia-gm-{PRERELEASE_VERSION}-linux-x86_64",
    )
    parser.add_argument("--ubuntu-22-04", choices=_QUALIFICATION_STATES, default="pending")
    parser.add_argument("--ubuntu-24-04", choices=_QUALIFICATION_STATES, default="pending")
    parser.add_argument(
        "--allow-newer-glibc",
        action="store_true",
        help=(
            "Trial builds only: build on a machine newer than the baseline. The result "
            "will not start on older distributions."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    arguments = _parser(root).parse_args(argv)
    manifest = build_linux_prerelease(
        root=root,
        output=arguments.output,
        ubuntu_22_04=arguments.ubuntu_22_04,
        ubuntu_24_04=arguments.ubuntu_24_04,
        dependency_notices_sha256=arguments.dependency_notices_sha256,
        forbidden_name_file=arguments.forbidden_name_file,
        allow_newer_glibc=arguments.allow_newer_glibc,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
