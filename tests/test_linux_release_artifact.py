"""The Linux release layout (one AppImage) and the script that builds it.

Synthetic bytes only: nothing here runs Cargo, Tauri or an AppImage. The Windows layout keeps
its own tests; these cases also pin that the two layouts never verify as each other.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from scripts import build_linux_prerelease as linux_builder
from scripts.build_windows_prerelease import LOCK_CHECK_COMMANDS

from sylvia_gm.release_artifact import (
    BUNDLED_NOTICES_KEY,
    DEPENDENCY_NOTICES_PATH,
    DEPENDENCY_NOTICES_SOURCE,
    LINUX_BUNDLED_NOTICES_PATH,
    LINUX_RELEASE_FORMAT_ID,
    LINUX_UNSIGNED_DISCLOSURE,
    PRERELEASE_VERSION,
    RELEASE_CHANNEL,
    ReleaseArtifact,
    ReleaseArtifactError,
    assemble_qualified_release_layout,
    assemble_release_layout,
    linux_release_platform,
    verify_release_layout,
    windows_release_platform,
)

ROOT = Path(__file__).parents[1]
NOTICES = b"SYNTHETIC TEST FIXTURE ONLY - no shipping licence text.\n"
NOTICES_SHA256 = hashlib.sha256(NOTICES).hexdigest()
BUNDLED = b"SYNTHETIC TEST FIXTURE ONLY - notices for bundled system libraries.\n"
PROVENANCE = {
    "source_commit": "a" * 40,
    "source_tree": "b" * 40,
    "source_timestamp_utc": "2026-11-16T00:00:00Z",
}
WINDOWS_TOOLCHAIN = {
    "cargo": "synthetic-cargo",
    "mcp": "synthetic-mcp",
    "pyinstaller": "synthetic-pyinstaller",
    "python": "synthetic-python",
    "rustc": "synthetic-rustc",
    "tauri_cli": "synthetic-tauri",
    "uv": "synthetic-uv",
}
LINUX_TOOLCHAIN = {
    **WINDOWS_TOOLCHAIN,
    "appimage_tools_sha256": "c" * 64,
    "appimage_tools_source": "downloaded by the bundler",
    "build_system": "Ubuntu 22.04.5 LTS",
    "glibc": "2.35",
}
LINUX_QUALIFICATION = {"ubuntu_22_04": "pending", "ubuntu_24_04": "pending"}


def _notices(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic-dependency-notices.txt"
    path.write_bytes(NOTICES)
    return path


def _bundled(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic-bundled-libraries.txt"
    path.write_bytes(BUNDLED)
    return path


def _appimage(tmp_path: Path, name: str | None = None) -> Path:
    path = tmp_path / (name or f"Sylvia GM_{PRERELEASE_VERSION}_amd64.AppImage")
    path.write_bytes(b"synthetic AppImage - never execute")
    return path


def _linux_release(tmp_path: Path, **changes: object) -> tuple[Path, dict[str, object]]:
    output = tmp_path / "linux-release"
    arguments: dict[str, object] = {
        "output": output,
        "artifacts": (ReleaseArtifact(_appimage(tmp_path), "appimage"),),
        "provenance": PROVENANCE,
        "toolchain": LINUX_TOOLCHAIN,
        "qualification": LINUX_QUALIFICATION,
        "license_path": ROOT / "LICENSE",
        "dependency_notices_path": _notices(tmp_path),
        "dependency_notices_sha256": NOTICES_SHA256,
        "platform": linux_release_platform(),
        "bundled_notices_path": _bundled(tmp_path),
    }
    arguments.update(changes)
    return output, assemble_release_layout(**arguments)  # type: ignore[arg-type]


def test_linux_layout_is_one_appimage_with_its_own_identity_and_disclosure(tmp_path):
    output, manifest = _linux_release(tmp_path)
    assert verify_release_layout(output, linux_release_platform()) == manifest
    assert manifest["format_id"] == LINUX_RELEASE_FORMAT_ID
    assert manifest["product"] == {
        "architecture": "x86_64",
        "channel": RELEASE_CHANNEL,
        "name": "Sylvia GM",
        "platform": "linux",
        "version": PRERELEASE_VERSION,
    }
    assert manifest["qualification"] == LINUX_QUALIFICATION
    assert manifest["toolchain"] == LINUX_TOOLCHAIN
    assert manifest["signing"] == {"disclosure": LINUX_UNSIGNED_DISCLOSURE, "status": "unsigned"}
    assert "Windows" not in LINUX_UNSIGNED_DISCLOSURE
    assert (output / "UNSIGNED.txt").read_text(encoding="utf-8") == LINUX_UNSIGNED_DISCLOSURE + "\n"
    assert [(row["kind"], row["name"]) for row in manifest["artifacts"]] == [
        ("appimage", f"Sylvia GM_{PRERELEASE_VERSION}_amd64.AppImage")
    ]
    assert sorted(path.name for path in output.iterdir()) == sorted(
        [
            "LICENSE",
            "SHA256SUMS.txt",
            f"Sylvia GM_{PRERELEASE_VERSION}_amd64.AppImage",
            "UNSIGNED.txt",
            "licenses",
            "release-manifest.json",
        ]
    )
    assert (output / DEPENDENCY_NOTICES_PATH).read_bytes() == NOTICES
    # The notices for the libraries inside the AppImage ship beside it, declared and hashed.
    assert (output / LINUX_BUNDLED_NOTICES_PATH).read_bytes() == BUNDLED
    assert manifest[BUNDLED_NOTICES_KEY] == {
        "path": LINUX_BUNDLED_NOTICES_PATH,
        "sha256": hashlib.sha256(BUNDLED).hexdigest(),
        "size_bytes": len(BUNDLED),
    }
    sums = (output / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert f"{hashlib.sha256(BUNDLED).hexdigest()}  {LINUX_BUNDLED_NOTICES_PATH}\n" in sums


@pytest.mark.runs_on("linux", reason="File permission bits are a POSIX property")
def test_the_appimage_in_a_linux_layout_is_executable_and_other_files_are_not(tmp_path):
    source = _appimage(tmp_path)
    source.chmod(0o600)
    output, manifest = _linux_release(tmp_path, artifacts=(ReleaseArtifact(source, "appimage"),))
    appimage = output / manifest["artifacts"][0]["name"]
    assert appimage.stat().st_mode & 0o777 == 0o755
    assert (output / "LICENSE").stat().st_mode & 0o111 == 0
    assert (output / LINUX_BUNDLED_NOTICES_PATH).stat().st_mode & 0o111 == 0
    qualified = tmp_path / "qualified"
    assemble_qualified_release_layout(
        source=output,
        output=qualified,
        qualification={"ubuntu_22_04": "passed", "ubuntu_24_04": "passed"},
        platform=linux_release_platform(),
    )
    assert (qualified / appimage.name).stat().st_mode & 0o777 == 0o755


def test_only_the_linux_layout_marks_an_artifact_executable():
    assert linux_release_platform().executable_kinds == frozenset({"appimage"})
    assert windows_release_platform().executable_kinds == frozenset()


def test_bundled_library_notices_are_required_on_linux_and_refused_on_windows(tmp_path):
    with pytest.raises(ReleaseArtifactError, match="Bundled library notices are required"):
        _linux_release(tmp_path, bundled_notices_path=None)
    assert not (tmp_path / "linux-release").exists()
    installer = tmp_path / "synthetic-setup.exe"
    installer.write_bytes(b"synthetic installer - never execute")
    with pytest.raises(ReleaseArtifactError, match="not accepted otherwise"):
        assemble_release_layout(
            output=tmp_path / "windows-release",
            artifacts=(ReleaseArtifact(installer, "nsis-installer"),),
            provenance=PROVENANCE,
            toolchain=WINDOWS_TOOLCHAIN,
            qualification={"windows_10": "pending", "windows_11": "pending"},
            license_path=ROOT / "LICENSE",
            dependency_notices_path=_notices(tmp_path),
            dependency_notices_sha256=NOTICES_SHA256,
            bundled_notices_path=_bundled(tmp_path),
        )
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"  \n")
    with pytest.raises(ReleaseArtifactError, match="empty or unreadable"):
        _linux_release(tmp_path, bundled_notices_path=empty)


def test_linux_layout_detects_changed_missing_or_undeclared_bundled_notices(tmp_path):
    output, _ = _linux_release(tmp_path)
    notices = output / LINUX_BUNDLED_NOTICES_PATH
    notices.write_bytes(BUNDLED + b"changed\n")
    with pytest.raises(ReleaseArtifactError, match="pin"):
        verify_release_layout(output, linux_release_platform())
    notices.unlink()
    with pytest.raises(ReleaseArtifactError, match="cannot be read"):
        verify_release_layout(output, linux_release_platform())
    notices.write_bytes(BUNDLED)
    assert verify_release_layout(output, linux_release_platform())
    manifest_path = output / "release-manifest.json"
    original = manifest_path.read_bytes()
    record = b'"bundled_library_notices":'
    assert original.count(record) == 1
    start = original.index(record)
    end = original.index(b"}", start) + 2
    manifest_path.write_bytes(original[:start] + original[end:])
    with pytest.raises(ReleaseArtifactError, match="fields are incomplete or unknown"):
        verify_release_layout(output, linux_release_platform())


def test_a_linux_layout_never_verifies_as_windows_and_the_reverse(tmp_path):
    linux, _ = _linux_release(tmp_path)
    refused = "format is unsupported|fields are incomplete or unknown"
    for platform in (None, windows_release_platform()):
        with pytest.raises(ReleaseArtifactError, match=refused):
            verify_release_layout(linux, platform) if platform else verify_release_layout(linux)
    installer = tmp_path / "synthetic-setup.exe"
    installer.write_bytes(b"synthetic installer - never execute")
    windows = tmp_path / "windows-release"
    assemble_release_layout(
        output=windows,
        artifacts=(ReleaseArtifact(installer, "nsis-installer"),),
        provenance=PROVENANCE,
        toolchain=WINDOWS_TOOLCHAIN,
        qualification={"windows_10": "pending", "windows_11": "pending"},
        license_path=ROOT / "LICENSE",
        dependency_notices_path=_notices(tmp_path),
        dependency_notices_sha256=NOTICES_SHA256,
    )
    assert verify_release_layout(windows)["product"]["platform"] == "windows"
    assert BUNDLED_NOTICES_KEY not in verify_release_layout(windows)
    with pytest.raises(ReleaseArtifactError, match=refused):
        verify_release_layout(windows, linux_release_platform())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"toolchain": WINDOWS_TOOLCHAIN}, "toolchain keys"),
        (
            {"qualification": {"windows_10": "pending", "windows_11": "pending"}},
            "qualification keys",
        ),
        (
            {"qualification": {"ubuntu_22_04": "maybe", "ubuntu_24_04": "pending"}},
            "state is invalid",
        ),
    ],
)
def test_linux_layout_refuses_windows_records_and_unknown_states(tmp_path, changes, message):
    with pytest.raises(ReleaseArtifactError, match=message):
        _linux_release(tmp_path, **changes)
    assert not (tmp_path / "linux-release").exists()


def test_linux_layout_requires_exactly_one_appimage_and_no_windows_artifact(tmp_path):
    installer = tmp_path / "synthetic-setup.exe"
    installer.write_bytes(b"synthetic installer - never execute")
    with pytest.raises(ReleaseArtifactError, match="kind is unsupported"):
        _linux_release(tmp_path, artifacts=(ReleaseArtifact(installer, "nsis-installer"),))
    wrong_suffix = tmp_path / "Sylvia GM.zip"
    wrong_suffix.write_bytes(b"synthetic")
    with pytest.raises(ReleaseArtifactError, match="extension does not match"):
        _linux_release(tmp_path, artifacts=(ReleaseArtifact(wrong_suffix, "appimage"),))
    with pytest.raises(ReleaseArtifactError, match="kinds collide"):
        _linux_release(
            tmp_path,
            artifacts=(
                ReleaseArtifact(_appimage(tmp_path, "first.AppImage"), "appimage"),
                ReleaseArtifact(_appimage(tmp_path, "second.AppImage"), "appimage"),
            ),
        )
    with pytest.raises(ReleaseArtifactError, match="no installer artifacts"):
        _linux_release(tmp_path, artifacts=())


def test_linux_layout_detects_a_changed_appimage_and_an_added_file(tmp_path):
    output, manifest = _linux_release(tmp_path)
    appimage = output / manifest["artifacts"][0]["name"]
    original = appimage.read_bytes()
    appimage.write_bytes(original[:-1] + b"X")
    with pytest.raises(ReleaseArtifactError, match="hash does not match"):
        verify_release_layout(output, linux_release_platform())
    appimage.write_bytes(original)
    (output / "extra.txt").write_text("undeclared", encoding="utf-8")
    with pytest.raises(ReleaseArtifactError, match="undeclared files"):
        verify_release_layout(output, linux_release_platform())


def test_qualified_linux_layout_keeps_the_same_bytes_with_new_states(tmp_path):
    source, manifest = _linux_release(tmp_path)
    qualified = assemble_qualified_release_layout(
        source=source,
        output=tmp_path / "qualified",
        qualification={"ubuntu_22_04": "passed", "ubuntu_24_04": "not_available"},
        platform=linux_release_platform(),
    )
    assert qualified["artifacts"] == manifest["artifacts"]
    assert qualified["qualification"] == {"ubuntu_22_04": "passed", "ubuntu_24_04": "not_available"}
    assert verify_release_layout(tmp_path / "qualified", linux_release_platform()) == qualified
    assert qualified[BUNDLED_NOTICES_KEY] == manifest[BUNDLED_NOTICES_KEY]
    assert (tmp_path / "qualified" / LINUX_BUNDLED_NOTICES_PATH).read_bytes() == BUNDLED
    with pytest.raises(ReleaseArtifactError, match="format is unsupported|fields are incomplete"):
        assemble_qualified_release_layout(
            source=source,
            output=tmp_path / "as-windows",
            qualification={"windows_10": "passed", "windows_11": "passed"},
        )


def test_build_machine_facts_are_read_strictly(tmp_path, monkeypatch):
    described = tmp_path / "os-release"
    described.write_text('NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 22.04.5 LTS"\n', encoding="utf-8")
    assert linux_builder._build_system(described) == "Ubuntu 22.04.5 LTS"
    described.write_text('NAME="Ubuntu"\nPRETTY_NAME=""\n', encoding="utf-8")
    with pytest.raises(ReleaseArtifactError, match="does not describe"):
        linux_builder._build_system(described)
    with pytest.raises(ReleaseArtifactError, match="does not describe"):
        linux_builder._build_system(tmp_path / "absent")

    def reports(value):
        monkeypatch.setattr(linux_builder.os, "confstr", lambda _name: value, raising=False)

    for reported, expected in (
        ("glibc 2.35", (2, 35)),
        ("glibc 2.39", (2, 39)),
        ("glibc 2.4", (2, 4)),
    ):
        reports(reported)
        assert linux_builder._glibc_version() == expected
    for reported in ("musl 1.2.4", "glibc", "glibc two.three", ""):
        reports(reported)
        with pytest.raises(ReleaseArtifactError, match="not glibc"):
            linux_builder._glibc_version()

    def unknown(_name):
        raise ValueError("unrecognized configuration name")

    monkeypatch.setattr(linux_builder.os, "confstr", unknown, raising=False)
    with pytest.raises(ReleaseArtifactError, match="not glibc"):
        linux_builder._glibc_version()


def test_bundling_tool_hash_covers_names_and_bytes_and_needs_the_tools(tmp_path):
    cache = tmp_path / "tauri"
    with pytest.raises(ReleaseArtifactError, match="were not kept"):
        linux_builder._appimage_tools_digest(cache)
    cache.mkdir()
    with pytest.raises(ReleaseArtifactError, match="were not kept"):
        linux_builder._appimage_tools_digest(cache)
    (cache / "AppRun-x86_64").write_bytes(b"synthetic runner")
    (cache / "linuxdeploy-x86_64.AppImage").write_bytes(b"synthetic bundler")
    first = linux_builder._appimage_tools_digest(cache)
    assert len(first) == 64 and first == linux_builder._appimage_tools_digest(cache)
    (cache / "linuxdeploy-x86_64.AppImage").write_bytes(b"synthetic bundler, changed")
    changed = linux_builder._appimage_tools_digest(cache)
    assert changed != first
    (cache / "linuxdeploy-x86_64.AppImage").rename(cache / "renamed.AppImage")
    assert linux_builder._appimage_tools_digest(cache) not in {first, changed}


def test_exactly_one_appimage_is_taken_from_the_build(tmp_path):
    bundle = tmp_path / "release/bundle/appimage"
    with pytest.raises(ReleaseArtifactError, match="exactly one AppImage"):
        linux_builder.discover_appimage(tmp_path)
    bundle.mkdir(parents=True)
    (bundle / "Sylvia GM.AppDir").mkdir()
    first = bundle / "first.AppImage"
    first.write_bytes(b"synthetic")
    assert linux_builder.discover_appimage(tmp_path) == ReleaseArtifact(first, "appimage")
    (bundle / "second.AppImage").write_bytes(b"synthetic")
    with pytest.raises(ReleaseArtifactError, match="exactly one AppImage"):
        linux_builder.discover_appimage(tmp_path)


def _builder_fixture(tmp_path, monkeypatch, *, glibc=(2, 35), system="linux"):
    root = tmp_path / "synthetic-source"
    (root / "src-tauri").mkdir(parents=True)
    shutil.copyfile(ROOT / "src-tauri/tauri.conf.json", root / "src-tauri/tauri.conf.json")
    shutil.copyfile(ROOT / "LICENSE", root / "LICENSE")
    (root / "licenses").mkdir()
    for name in linux_builder.RUST_STDLIB_NOTICE_SHA256:
        shutil.copyfile(ROOT / name, root / name)
    notice = root / DEPENDENCY_NOTICES_SOURCE
    notice.write_bytes(NOTICES)
    commands = []
    # The build machine's GStreamer plug-ins: the sound set plus ones that must stay out.
    plugins = tmp_path / "system-gstreamer-1.0"
    plugins.mkdir()
    for name in (*linux_builder.MEDIA_PLUGINS, "jack", "aasink", "cacasink"):
        (plugins / f"libgst{name}.so").write_bytes(b"synthetic plug-in - never load")
    monkeypatch.setattr(linux_builder, "_system_plugin_folder", lambda: plugins)
    monkeypatch.delenv("GSTREAMER_INCLUDE_BAD_PLUGINS", raising=False)

    def run(command, *, cwd, capture=False, env=None):
        commands.append(tuple(command))
        if tuple(command) == linux_builder.TAURI_BUILD_COMMAND:
            assert cwd == root / "src-tauri"
            assert env["UV_PYTHON"] == linux_builder.sys.executable
            assert env["APPIMAGE_EXTRACT_AND_RUN"] == "1"
            flags = env["CARGO_ENCODED_RUSTFLAGS"].split("\x1f")
            assert f"--remap-path-prefix={root}=/sylvia/source" in flags
            assert f"--remap-path-prefix={Path.home()}=/build-home" in flags
            assert not any("PDBALTPATH" in flag for flag in flags)
            target = Path(env["CARGO_TARGET_DIR"])
            cache = Path(env["XDG_CACHE_HOME"])
            assert target.is_relative_to(root / ".owner-build")
            assert cache.is_relative_to(root / ".owner-build") and cache != target
            assert Path(env["UV_CACHE_DIR"]) != cache
            # The bundler copies the media framework from this folder only: the sound set.
            staged = Path(env["GSTREAMER_PLUGINS_DIR"])
            assert staged.is_relative_to(root / ".owner-build")
            assert sorted(path.name for path in staged.iterdir()) == sorted(
                f"libgst{name}.so" for name in linux_builder.MEDIA_PLUGINS)
            assert "GSTREAMER_INCLUDE_BAD_PLUGINS" not in env
            bundle = target / "release/bundle/appimage"
            bundle.mkdir(parents=True)
            built = bundle / "Sylvia GM_synthetic_amd64.AppImage"
            built.write_bytes(b"synthetic - never execute")
            library = bundle / "Sylvia GM.AppDir/usr/lib/libsynthetic.so.1"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"synthetic library - never load")
            # Like the real bundler, download a tool only when its cache does not have it.
            (cache / "tauri").mkdir(parents=True, exist_ok=True)
            tool = cache / "tauri/linuxdeploy-x86_64.AppImage"
            if not tool.exists():
                tool.write_bytes(b"synthetic bundler")
        return ""

    written = []

    def write_notices(appdir, output, *, own_folder, build_system):
        # The real writer asks the build machine's package database; here it is replaced.
        assert appdir.name == "Sylvia GM.AppDir" and (appdir / "usr/lib").is_dir()
        assert own_folder == "Sylvia GM" and build_system == "Ubuntu 22.04.5 LTS"
        assert output.parent.is_relative_to(root / ".owner-build")
        output.write_bytes(BUNDLED)
        written.append(output)
        return ["synthetic-package"]

    monkeypatch.setattr(linux_builder.bundled, "write_notices", write_notices)
    monkeypatch.setattr(linux_builder.sys, "platform", system)
    monkeypatch.setattr(linux_builder, "_glibc_version", lambda: glibc)
    monkeypatch.setattr(linux_builder, "_build_system", lambda: "Ubuntu 22.04.5 LTS")
    monkeypatch.setattr(linux_builder, "validate_version_sources", lambda _root: PRERELEASE_VERSION)
    monkeypatch.setattr(linux_builder, "check_source", lambda *_: {"passed": True})
    monkeypatch.setattr(linux_builder.shared, "_run", run)
    monkeypatch.setattr(linux_builder.shared, "_require_clean_tree", lambda _root: None)
    monkeypatch.setattr(linux_builder.shared, "_source_identity", lambda _root: PROVENANCE)
    monkeypatch.setattr(linux_builder.shared, "_toolchain", lambda _root: WINDOWS_TOOLCHAIN)
    monkeypatch.setattr(
        linux_builder.shared, "_source_input_manifest", lambda _root: {"LICENSE": "a" * 64}
    )
    return root, notice, commands


def _build(root, output, tmp_path, **changes):
    arguments = {
        "root": root,
        "output": output,
        "ubuntu_22_04": "pending",
        "ubuntu_24_04": "not_available",
        "dependency_notices_sha256": NOTICES_SHA256,
        "forbidden_name_file": tmp_path / "external-names.txt",
    }
    arguments.update(changes)
    return linux_builder.build_linux_prerelease(**arguments)


def test_linux_builder_runs_the_locked_steps_and_records_the_build_machine(tmp_path, monkeypatch):
    root, _notice, commands = _builder_fixture(tmp_path, monkeypatch)
    output = tmp_path / "built"
    manifest = _build(root, output, tmp_path)
    assert commands == [*LOCK_CHECK_COMMANDS, linux_builder.TAURI_BUILD_COMMAND]
    assert linux_builder.TAURI_BUILD_COMMAND[-2:] == ("--bundles", "appimage")
    assert "--no-sign" in linux_builder.TAURI_BUILD_COMMAND
    assert verify_release_layout(output, linux_release_platform()) == manifest
    assert [row["kind"] for row in manifest["artifacts"]] == ["appimage"]
    assert manifest["qualification"] == {"ubuntu_22_04": "pending", "ubuntu_24_04": "not_available"}
    tools = manifest["toolchain"]
    assert tools["build_system"] == "Ubuntu 22.04.5 LTS" and tools["glibc"] == "2.35"
    assert len(tools["appimage_tools_sha256"]) == 64
    assert tools["appimage_tools_source"] == "downloaded by the bundler"
    assert {key: tools[key] for key in WINDOWS_TOOLCHAIN} == WINDOWS_TOOLCHAIN
    assert (output / DEPENDENCY_NOTICES_PATH).read_bytes() == NOTICES
    assert (output / LINUX_BUNDLED_NOTICES_PATH).read_bytes() == BUNDLED
    assert manifest[BUNDLED_NOTICES_KEY]["sha256"] == hashlib.sha256(BUNDLED).hexdigest()


def test_linux_builder_stops_when_a_bundled_library_cannot_be_accounted_for(tmp_path, monkeypatch):
    root, _notice, _commands = _builder_fixture(tmp_path, monkeypatch)

    def unaccounted(*_arguments, **_options):
        raise linux_builder.bundled.BundledNoticesError(
            "No installed package ships these bundled libraries: usr/lib/libmystery.so.3"
        )

    monkeypatch.setattr(linux_builder.bundled, "write_notices", unaccounted)
    with pytest.raises(ReleaseArtifactError, match="libmystery"):
        _build(root, tmp_path / "unaccounted", tmp_path)
    assert not (tmp_path / "unaccounted").exists()


def test_only_the_sound_plugins_are_staged_and_a_missing_one_stops_the_build(tmp_path):
    source = tmp_path / "system"
    source.mkdir()
    for name in ("app", "autodetect", "pulseaudio", "jack"):
        (source / f"libgst{name}.so").write_bytes(b"synthetic plug-in - never load")
    with pytest.raises(ReleaseArtifactError,
                       match=r"libgstaudioconvert\.so.*gstreamer1\.0-plugins-base"):
        linux_builder.stage_media_plugins(source, tmp_path / "refused")
    assert not (tmp_path / "refused").exists()
    for name in linux_builder.MEDIA_PLUGINS:
        (source / f"libgst{name}.so").write_bytes(f"synthetic {name}".encode())
    staged = linux_builder.stage_media_plugins(source, tmp_path / "staged")
    assert sorted(path.name for path in (tmp_path / "staged").iterdir()) == sorted(staged)
    assert "libgstjack.so" not in staged and len(staged) == len(linux_builder.MEDIA_PLUGINS)
    # Sound output and what WebKit's audio pipeline is built from; no video or text outputs.
    assert {"app", "autodetect", "pulseaudio", "audioconvert", "audioresample"} <= set(
        linux_builder.MEDIA_PLUGINS)
    assert not {"jack", "aasink", "cacasink"} & set(linux_builder.MEDIA_PLUGINS)


def test_the_unpacked_bundle_is_found_only_when_there_is_exactly_one(tmp_path):
    bundle = tmp_path / "release/bundle/appimage"
    with pytest.raises(ReleaseArtifactError, match="exactly one unpacked"):
        linux_builder.discover_appdir(tmp_path)
    (bundle / "Sylvia GM.AppDir").mkdir(parents=True)
    (bundle / "not-a-folder.AppDir").write_bytes(b"synthetic")
    assert linux_builder.discover_appdir(tmp_path) == bundle / "Sylvia GM.AppDir"
    (bundle / "Second.AppDir").mkdir()
    with pytest.raises(ReleaseArtifactError, match="exactly one unpacked"):
        linux_builder.discover_appdir(tmp_path)


def test_linux_builder_refuses_a_newer_machine_unless_told_it_is_a_trial(tmp_path, monkeypatch):
    root, _notice, commands = _builder_fixture(tmp_path, monkeypatch, glibc=(2, 39))
    with pytest.raises(ReleaseArtifactError, match=r"Ubuntu 22\.04 \(glibc 2\.35\).*glibc 2\.39"):
        _build(root, tmp_path / "refused", tmp_path)
    assert commands == [] and not (tmp_path / "refused").exists()
    manifest = _build(root, tmp_path / "trial", tmp_path, allow_newer_glibc=True)
    assert manifest["toolchain"]["glibc"] == "2.39"


def test_linux_builder_stops_before_any_command_on_bad_inputs(tmp_path, monkeypatch):
    root, notice, commands = _builder_fixture(tmp_path, monkeypatch, system="win32")
    with pytest.raises(ReleaseArtifactError, match="run on Linux"):
        _build(root, tmp_path / "wrong-system", tmp_path)
    monkeypatch.setattr(linux_builder.sys, "platform", "linux")
    with pytest.raises(ReleaseArtifactError, match="literal-name list"):
        _build(root, tmp_path / "no-names", tmp_path, forbidden_name_file=None)
    monkeypatch.setattr(linux_builder, "check_source", lambda *_: {"passed": False})
    with pytest.raises(ReleaseArtifactError, match="identity check failed"):
        _build(root, tmp_path / "named", tmp_path)
    monkeypatch.setattr(linux_builder, "check_source", lambda *_: {"passed": True})
    (tmp_path / "exists").mkdir()
    with pytest.raises(ReleaseArtifactError, match="already exists"):
        _build(root, tmp_path / "exists", tmp_path)
    notice.write_bytes(b"changed synthetic input")
    with pytest.raises(ReleaseArtifactError, match="pin"):
        _build(root, tmp_path / "changed", tmp_path)
    assert commands == []


def test_linux_builder_notices_an_input_changed_during_the_build(tmp_path, monkeypatch):
    root, notice, _commands = _builder_fixture(tmp_path, monkeypatch)
    original = linux_builder.shared._run

    def run(command, **options):
        result = original(command, **options)
        if tuple(command) == linux_builder.TAURI_BUILD_COMMAND:
            notice.write_bytes(b"changed synthetic build-time document")
        return result

    monkeypatch.setattr(linux_builder.shared, "_run", run)
    with pytest.raises(ReleaseArtifactError, match="pin"):
        _build(root, tmp_path / "changed-during", tmp_path)
    assert not (tmp_path / "changed-during").exists()


def _kept_tools(tmp_path, files):
    kept = tmp_path / "kept-appimage-tools"
    kept.mkdir(parents=True)
    for name, content in files.items():
        (kept / name).write_bytes(content)
    return kept, linux_builder._appimage_tools_digest(kept)


def test_a_kept_copy_of_the_bundling_tools_is_used_and_recorded(tmp_path, monkeypatch):
    # If the tools' "continuous" release is down or has changed, the build can use the exact
    # copy an earlier build ran, as long as it matches the hash that build recorded.
    root, _notice, commands = _builder_fixture(tmp_path, monkeypatch)
    kept, digest = _kept_tools(tmp_path, {"linuxdeploy-x86_64.AppImage": b"synthetic bundler"})
    manifest = _build(
        root, tmp_path / "built", tmp_path, appimage_tools=kept, appimage_tools_sha256=digest
    )
    assert manifest["toolchain"]["appimage_tools_sha256"] == digest
    assert manifest["toolchain"]["appimage_tools_source"] == "kept copy"
    assert linux_builder.TAURI_BUILD_COMMAND in commands


def test_a_kept_copy_that_no_longer_matches_stops_the_build_before_bundling(tmp_path, monkeypatch):
    root, _notice, commands = _builder_fixture(tmp_path, monkeypatch)
    kept, _digest = _kept_tools(tmp_path, {"linuxdeploy-x86_64.AppImage": b"changed bundler"})
    _unused, recorded = _kept_tools(
        tmp_path / "earlier", {"linuxdeploy-x86_64.AppImage": b"synthetic bundler"}
    )
    with pytest.raises(ReleaseArtifactError, match="do not match their recorded SHA-256"):
        _build(
            root, tmp_path / "built", tmp_path, appimage_tools=kept, appimage_tools_sha256=recorded
        )
    assert linux_builder.TAURI_BUILD_COMMAND not in commands
    assert not (tmp_path / "built").exists()


def test_a_bundler_that_adds_to_the_kept_tools_is_refused(tmp_path, monkeypatch):
    # The kept copy lacks a tool the bundler then downloads: the build no longer ran only it.
    root, _notice, _commands = _builder_fixture(tmp_path, monkeypatch)
    kept, digest = _kept_tools(tmp_path, {"linuxdeploy-plugin-appimage.AppImage": b"plug-in"})
    with pytest.raises(ReleaseArtifactError, match="replaced or added to the kept"):
        _build(
            root, tmp_path / "built", tmp_path, appimage_tools=kept, appimage_tools_sha256=digest
        )
    assert not (tmp_path / "built").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"appimage_tools": Path("kept")},
        {"appimage_tools_sha256": "0" * 64},
        {"appimage_tools": Path("kept"), "appimage_tools_sha256": "not a hash"},
    ],
)
def test_kept_bundling_tools_need_their_folder_and_hash_together(tmp_path, monkeypatch, changes):
    root, _notice, commands = _builder_fixture(tmp_path, monkeypatch)
    with pytest.raises(ReleaseArtifactError, match="need both their folder and their SHA-256"):
        _build(root, tmp_path / "built", tmp_path, **changes)
    assert commands == []


def test_linux_builder_command_line_defaults():
    with pytest.raises(SystemExit):
        linux_builder._parser(ROOT).parse_args([])
    arguments = linux_builder._parser(ROOT).parse_args(
        ["--forbidden-name-file", "names.txt", "--dependency-notices-sha256", "0" * 64]
    )
    assert arguments.output.name == f"sylvia-gm-{PRERELEASE_VERSION}-linux-x86_64"
    assert (arguments.ubuntu_22_04, arguments.ubuntu_24_04) == ("pending", "pending")
    assert arguments.allow_newer_glibc is False
    assert (arguments.appimage_tools, arguments.appimage_tools_sha256) == (None, None)
    kept = linux_builder._parser(ROOT).parse_args(
        ["--forbidden-name-file", "names.txt", "--dependency-notices-sha256", "0" * 64,
         "--appimage-tools", "kept", "--appimage-tools-sha256", "1" * 64]
    )
    assert (kept.appimage_tools, kept.appimage_tools_sha256) == (Path("kept"), "1" * 64)
