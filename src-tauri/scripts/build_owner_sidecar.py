"""Build the standalone Windows authority sidecar for Tauri bundling.



The generated executable is a disposable build artifact.  Tauri's ``externalBin``

configuration consumes the target-triple-suffixed file and bundles it beside the

Sylvia GM executable as ``sylvia-gm-owner.exe``.

"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from sylvia_gm.application.bundled_campaign_plans import bundled_plan_files
from sylvia_gm.build_workspace import BuildWorkspace
from sylvia_gm.domain.package_library import validate_portable_path


def _rust_host_triple() -> str:

    completed = subprocess.run(
        ["rustc", "-vV"],
        check=True,
        capture_output=True,
        text=True,
    )

    for line in completed.stdout.splitlines():
        if line.startswith("host: "):
            return line.removeprefix("host: ").strip()

    raise RuntimeError("rustc did not report its host target triple")


_DATA_ROOTS = {
    "rulesets/srd-lite": "rulesets/srd-lite",
    "rulesets/riichi-mahjong": "rulesets/riichi-mahjong",
    "campaign-settings/severed-lands": "campaign-settings/severed-lands",
    "src/sylvia_gm/agent/skills": "sylvia_gm/agent/skills",
}

_DICTATION_VERSIONS = {
    "faster-whisper": "1.2.1",
    "ctranslate2": "4.8.3",
    "sounddevice": "0.5.6",
    "onnxruntime": "1.31.0",
    "av": "18.0.0",
}

_DICTATION_NOTICES = (
    "README.md",
    "HERMES-MIT.txt",
    "FASTER-WHISPER-MIT.txt",
    "WHISPER-MIT.txt",
    "CTRANSLATE2-MIT.txt",
    "FFMPEG-LGPLv3.txt",
    "GPLv3.txt",
    "PORTAUDIO-MIT.txt",
)


def dictation_build_arguments(repository_root: Path) -> list[str]:
    """Ship the pinned CPU runtime, VAD model, audio DLLs and original notices together."""
    for package, expected in _DICTATION_VERSIONS.items():
        if version(package) != expected:
            raise RuntimeError(f"Install the locked dictation extra before building ({package})")
    arguments = []
    hook = _contained_data_file(
        repository_root, repository_root / "src-tauri/scripts/hooks/hook-sounddevice.py"
    )
    arguments.extend(["--additional-hooks-dir", str(hook.parent)])
    # Local model loading and our fixed urllib download need no Xet client or global cache.
    arguments.extend(["--exclude-module", "hf_xet"])
    for package in (
        "faster_whisper",
        "ctranslate2",
        "sounddevice",
        "onnxruntime",
        "av",
    ):
        arguments.extend(["--collect-all", package])
    for package in ("faster-whisper", "sounddevice"):
        arguments.extend(["--recursive-copy-metadata", package])
    for name in _DICTATION_NOTICES:
        notice = _contained_data_file(
            repository_root, repository_root / "licenses/dictation" / name
        )
        arguments.extend(["--add-data", f"{notice}{os.pathsep}licenses/dictation"])
    return arguments


def _contained_data_file(root: Path, path: Path) -> Path:

    resolved = path.resolve()

    if (
        resolved != path.absolute()
        or not resolved.is_relative_to(root.resolve())
        or not path.is_file()
    ):
        raise ValueError(
            "Sidecar data file is missing or outside its declared root; aliases are not allowed"
        )

    return path


def sidecar_data_arguments(repository_root: Path, staging_root: Path) -> list[str]:
    """Stage only declared data files; never traverse private or untracked siblings."""

    inventory = _contained_data_file(
        repository_root, repository_root / "src-tauri/sidecar-data-files.json"
    )

    document = json.loads(inventory.read_text(encoding="utf-8"))

    if (
        not isinstance(document, dict)
        or set(document) != {"format", "files"}
        or type(document["format"]) is not int
        or document["format"] != 1
        or not isinstance(document["files"], list)
        or not document["files"]
    ):
        raise ValueError("Sidecar data inventory is invalid")

    selected = []

    seen = set()

    covered = set()

    for name in document["files"]:
        if not isinstance(name, str):
            raise ValueError("Sidecar data inventory path is invalid")

        validate_portable_path(name)

        folded = name.casefold()

        if folded in seen:
            raise ValueError("Sidecar data inventory path is duplicated")

        seen.add(folded)

        source_root = next(
            (prefix for prefix in _DATA_ROOTS if name.startswith(prefix + "/")), None
        )

        if source_root is None:
            raise ValueError("Sidecar data inventory path has no declared data root")

        declared_root = repository_root / source_root

        if not declared_root.resolve().is_relative_to(repository_root.resolve()):
            raise ValueError("Sidecar data root is outside the repository")

        source = _contained_data_file(declared_root, repository_root / name)

        destination = Path(_DATA_ROOTS[source_root]) / source.relative_to(declared_root)

        selected.append((source, declared_root, destination))

        covered.add(source_root)

    if covered != set(_DATA_ROOTS):
        raise ValueError("Sidecar data inventory must cover every declared data root")

    owned_root = (repository_root / ".owner-build").resolve()

    if not owned_root.is_relative_to(
        repository_root.resolve()
    ) or not staging_root.resolve().is_relative_to(owned_root):
        raise ValueError("Sidecar data staging is outside its owned build root")

    # A fresh directory prevents stale/unlisted files from entering the directory handoff.

    staging_root.mkdir()

    for source, declared_root, destination in selected:
        target = staging_root / destination

        target.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(_contained_data_file(declared_root, source), target)

    arguments = []

    for destination in _DATA_ROOTS.values():
        staged = staging_root / destination

        arguments.extend(["--add-data", f"{staged}{os.pathsep}{destination}"])

    return arguments


def campaign_plan_data_arguments(repository_root: Path) -> list[str]:
    """Include the selected public files, never unrelated drafts or working directories."""

    root = repository_root / "campaign-plans"

    arguments = []

    for path in bundled_plan_files(root):
        destination = Path("campaign-plans") / path.relative_to(root).parent

        arguments.extend(["--add-data", f"{path}{os.pathsep}{destination.as_posix()}"])

    return arguments


def main() -> int:

    tauri_root = Path(__file__).resolve().parents[1]

    repository_root = tauri_root.parent

    entry = tauri_root / "scripts" / "sylvia_gm_owner_entry.py"

    target_triple = os.environ.get("TAURI_ENV_TARGET_TRIPLE") or _rust_host_triple()

    suffix = ".exe" if target_triple.endswith("windows-msvc") else ""

    # Each build owns a fresh directory under its source checkout. Never clean a

    # global D:/Temp build root that may belong to another live task.

    owned_parent = repository_root / ".owner-build"

    owned_parent.mkdir(parents=True, exist_ok=True)

    invocation = BuildWorkspace.create(owned_parent)

    work_root = invocation.path

    if not work_root.is_relative_to(owned_parent.resolve()):
        raise RuntimeError("Sidecar build directory escaped its owning checkout")

    build_dir, dist_dir, spec_dir = (work_root / name for name in ("build", "dist", "spec"))

    for directory in (build_dir, dist_dir, spec_dir):
        directory.mkdir()

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noupx",
        "--name",
        "sylvia-gm-owner",
        "--paths",
        str(repository_root / "src"),
        "--add-data",
        f"{repository_root / 'config' / 'config.yaml'}{os.pathsep}config",
        *sidecar_data_arguments(repository_root, work_root / "data"),
        *campaign_plan_data_arguments(repository_root),
        *dictation_build_arguments(repository_root),
        "--add-data",
        f"{repository_root / 'src' / 'sylvia_gm' / 'campaign' / 'the-rift-awakening.json'}"
        f"{os.pathsep}sylvia_gm/campaign",
        "--workpath",
        str(build_dir),
        "--distpath",
        str(dist_dir),
        "--specpath",
        str(spec_dir),
        str(entry),
    ]

    build_environment = os.environ.copy()

    build_environment.pop("PYTHONHOME", None)

    build_environment.pop("PYTHONPATH", None)
    build_environment["HF_HOME"] = str(work_root / "hf-cache")
    build_environment["HF_XET_CACHE"] = str(work_root / "hf-cache/xet")
    build_environment["HF_HUB_OFFLINE"] = "1"
    build_environment["HF_HUB_DISABLE_TELEMETRY"] = "1"
    build_environment["HF_HUB_DISABLE_XET"] = "1"

    subprocess.run(
        command,
        cwd=repository_root,
        env=build_environment,
        check=True,
    )

    built = dist_dir / f"sylvia-gm-owner{suffix}"

    if not built.is_file() or built.stat().st_size == 0:
        raise RuntimeError(f"PyInstaller did not create {built}")

    binaries = tauri_root / "binaries"

    binaries.mkdir(parents=True, exist_ok=True)

    destination = binaries / f"sylvia-gm-owner-{target_triple}{suffix}"

    shutil.copy2(built, destination)

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()

    print(f"Built {destination} ({destination.stat().st_size} bytes, sha256={digest})")

    receipt = invocation.finish(destination, keep=os.environ.get("SYLVIA_KEEP_OWNER_BUILD") == "1")

    print(f"Invocation build work: {receipt['cleanup']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
