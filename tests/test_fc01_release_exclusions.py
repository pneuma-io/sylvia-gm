"""FC01 packaging regressions use synthetic files; no actual build or private input."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

FROZEN = Path(os.environ.get("FC01_SOURCE_ROOT", Path(__file__).resolve().parents[1])).resolve()
EVIDENCE = None
SYNTHETIC_ROOT = None
sys.path.insert(0, str(FROZEN / "src"))
bundled_plan_files = importlib.import_module(
    "sylvia_gm.application.bundled_campaign_plans"
).bundled_plan_files


@pytest.fixture(autouse=True)
def isolated_evidence(tmp_path, monkeypatch):
    requested = os.environ.get("FC01_TEST_EVIDENCE")
    evidence = Path(requested) if requested else tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    monkeypatch.setitem(globals(), "EVIDENCE", evidence)
    monkeypatch.setitem(globals(), "SYNTHETIC_ROOT", tmp_path.resolve())


def party(*, fixed=None, companions=()):
    player = (
        {"mode": "compatible"}
        if fixed is None
        else {"mode": "fixed", "character": {"id": "fixed", "path": fixed}}
    )
    return {
        "player_character": player,
        "required_companions": [
            {"id": f"companion-{i}", "path": path} for i, path in enumerate(companions)
        ],
        "custom_companions": {"minimum": 0, "maximum": 2},
    }


def put(root, relative, value=b"SYNTHETIC ONLY"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else value.encode("utf-8"))
    return path


def make_plan(root, slug="demo", *, policy=None, snapshot=True, contribution=False):
    put(root, f"{slug}/plan.json", json.dumps({"starting_party": policy or party()}))
    put(root, f"{slug}/plan.md", "# Synthetic plan\n")
    if snapshot:
        put(root, f"{slug}/setting.sylvia-gm-package")
    if contribution:
        put(root, f"{slug}/interview-contribution.json", "{}")


def catalogue(root, ids=("demo",)):
    put(
        root,
        "catalogue.json",
        json.dumps(
            [
                {"id": slug, "title": "Synthetic", "pitch": "Synthetic", "format": "Test"}
                for slug in ids
            ]
        ),
    )


def selected(root):
    return [p.relative_to(root).as_posix() for p in bundled_plan_files(root)]


@pytest.mark.parametrize(
    "snapshot,contribution", [(False, False), (True, False), (False, True), (True, True)]
)
def test_selected_plan_optional_inputs_and_unselected_siblings(tmp_path, snapshot, contribution):
    catalogue(tmp_path)
    make_plan(tmp_path, snapshot=snapshot, contribution=contribution)
    put(tmp_path, "demo/unselected.json")
    put(tmp_path, "demo/setting-files/characters/unreferenced.json")
    expected = ["catalogue.json", "demo/plan.json", "demo/plan.md"]
    if snapshot:
        expected.append("demo/setting.sylvia-gm-package")
    if contribution:
        expected.append("demo/interview-contribution.json")
    assert selected(tmp_path) == expected


def test_referenced_fixed_and_companion_character_files_only(tmp_path):
    catalogue(tmp_path)
    make_plan(
        tmp_path,
        policy=party(fixed="characters/fixed.json", companions=["characters/companion.json"]),
    )
    for name in ["fixed", "companion", "unreferenced"]:
        put(tmp_path, f"demo/setting-files/characters/{name}.json")
    assert selected(tmp_path) == [
        "catalogue.json",
        "demo/plan.json",
        "demo/plan.md",
        "demo/setting.sylvia-gm-package",
        "demo/setting-files/characters/companion.json",
        "demo/setting-files/characters/fixed.json",
    ]


def test_missing_loose_character_has_no_inventory_completeness_guarantee(tmp_path):
    catalogue(tmp_path)
    make_plan(tmp_path, policy=party(fixed="characters/missing.json"), snapshot=False)
    # This records observed selector behavior, not successful character/content admission.
    assert selected(tmp_path) == ["catalogue.json", "demo/plan.json", "demo/plan.md"]


@pytest.mark.parametrize(
    "snapshot,contribution", [(False, False), (True, False), (False, True), (True, True)]
)
def test_legacy_rift_fallback_optional_inputs(tmp_path, snapshot, contribution):
    catalogue(tmp_path)
    make_plan(tmp_path)
    make_plan(tmp_path, "the-rift-awakening", snapshot=snapshot, contribution=contribution)
    put(tmp_path, "the-rift-awakening/unselected-guide.md")
    expected = [
        "catalogue.json",
        "demo/plan.json",
        "demo/plan.md",
        "demo/setting.sylvia-gm-package",
        "the-rift-awakening/plan.json",
        "the-rift-awakening/plan.md",
    ]
    if snapshot:
        expected.append("the-rift-awakening/setting.sylvia-gm-package")
    if contribution:
        expected.append("the-rift-awakening/interview-contribution.json")
    assert selected(tmp_path) == expected


def test_selected_legacy_is_not_duplicated(tmp_path):
    catalogue(tmp_path, ("the-rift-awakening",))
    make_plan(tmp_path, "the-rift-awakening", contribution=True)
    assert selected(tmp_path) == [
        "catalogue.json",
        "the-rift-awakening/plan.json",
        "the-rift-awakening/plan.md",
        "the-rift-awakening/setting.sylvia-gm-package",
        "the-rift-awakening/interview-contribution.json",
    ]


def test_legacy_fallback_does_not_collect_loose_character_references(tmp_path):
    catalogue(tmp_path)
    make_plan(tmp_path)
    make_plan(
        tmp_path,
        "the-rift-awakening",
        snapshot=False,
        policy=party(fixed="characters/legacy-fixed.json"),
    )
    put(tmp_path, "the-rift-awakening/setting-files/characters/legacy-fixed.json")
    assert selected(tmp_path) == [
        "catalogue.json",
        "demo/plan.json",
        "demo/plan.md",
        "demo/setting.sylvia-gm-package",
        "the-rift-awakening/plan.json",
        "the-rift-awakening/plan.md",
    ]


@pytest.mark.parametrize(
    "slug", ["../outside", "/absolute", "a/b", r"a\b", "UPPER", "with space", "a--b", ""]
)
def test_malformed_catalogue_id_is_rejected_before_plan_read(tmp_path, monkeypatch, slug):
    catalogue(tmp_path, (slug,))
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(ValueError, match="identity"):
        bundled_plan_files(tmp_path)
    assert reads == [tmp_path / "catalogue.json"]


@pytest.mark.parametrize(
    "reference",
    [
        "../outside.json",
        "/absolute.json",
        "C:/outside.json",
        r"characters\bad.json",
        "characters/../bad.json",
        "NUL.json",
    ],
)
def test_malformed_character_path_rejected(tmp_path, reference):
    catalogue(tmp_path)
    make_plan(tmp_path, policy=party(fixed=reference))
    with pytest.raises(ValueError):
        bundled_plan_files(tmp_path)


@pytest.mark.parametrize("missing", ["plan.json", "plan.md", "legacy-plan.md"])
def test_required_selected_and_legacy_inputs_fail_when_missing(tmp_path, missing):
    catalogue(tmp_path)
    make_plan(tmp_path)
    if missing.startswith("legacy-"):
        make_plan(tmp_path, "the-rift-awakening")
        (tmp_path / "the-rift-awakening/plan.md").unlink()
    else:
        (tmp_path / "demo" / missing).unlink()
    expected_error = FileNotFoundError if missing == "plan.json" else ValueError
    expected_message = "plan.json" if missing == "plan.json" else "file is missing or outside"
    with pytest.raises(expected_error, match=expected_message):
        bundled_plan_files(tmp_path)


def directory_link(link, target):
    assert link.parent.resolve().is_relative_to(SYNTHETIC_ROOT)
    assert target.resolve().is_relative_to(SYNTHETIC_ROOT)
    try:
        link.symlink_to(target, target_is_directory=True)
        return "directory symlink"
    except OSError as exc:
        if os.name != "nt" or getattr(exc, "winerror", None) not in {5, 1314}:
            raise
        # Junction creation requires no privilege/security change. Both endpoints are synthetic.
        result = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = {
                "symlink_winerror": getattr(exc, "winerror", None),
                "junction_returncode": result.returncode,
                "junction_stdout": result.stdout.replace(str(SYNTHETIC_ROOT), "<fixture>"),
                "junction_stderr": result.stderr.replace(str(SYNTHETIC_ROOT), "<fixture>"),
            }
            (EVIDENCE / ("link-precondition-" + link.parent.name + ".json")).write_text(
                json.dumps(detail, indent=2), encoding="utf-8"
            )
            pytest.skip("Synthetic link unavailable: " + json.dumps(detail))
        return "directory junction (symlink privilege unavailable)"


def test_outside_root_link_is_rejected_and_read_order_recorded(tmp_path, monkeypatch):
    root = tmp_path / "selected"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    catalogue(root)
    make_plan(outside)
    kind = directory_link(root / "demo", outside / "demo")
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        reads.append(path.resolve())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(ValueError, match="outside the selected collection"):
        bundled_plan_files(root)
    assert outside / "demo/plan.json" not in reads
    (EVIDENCE / "outside-root-link.json").write_text(
        json.dumps(
            {
                "fixture": "fully synthetic sibling directories",
                "link_kind": kind,
                "final_inventory_rejected": True,
                "outside_plan_json_read_before_rejection": False,
                "real_private_contents_read": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_outside_root_character_directory_link_is_rejected_without_content_read(
    tmp_path, monkeypatch
):
    root = tmp_path / "selected"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    catalogue(root)
    make_plan(root, policy=party(fixed="characters/fixed.json"))
    put(outside, "characters/fixed.json")
    (root / "demo/setting-files").mkdir()
    kind = directory_link(root / "demo/setting-files/characters", outside / "characters")
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        reads.append(path.resolve())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(ValueError, match="outside the selected collection"):
        bundled_plan_files(root)
    assert outside / "characters/fixed.json" not in reads
    (EVIDENCE / "outside-root-character-link.json").write_text(
        json.dumps(
            {
                "link_kind": kind,
                "final_inventory_rejected": True,
                "outside_character_content_read": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_actual_sidecar_arguments_cover_all_synthetic_directory_sentinels(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "fc01_sidecar", FROZEN / "src-tauri/scripts/build_owner_sidecar.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__file__ = str(tmp_path / "src-tauri/scripts/build_owner_sidecar.py")
    put(
        tmp_path,
        "src-tauri/scripts/sylvia_gm_owner_entry.py",
        "# synthetic entry, never executed\n",
    )
    catalogue(tmp_path / "campaign-plans")
    make_plan(tmp_path / "campaign-plans")
    roots = [
        "rulesets/srd-lite",
        "campaign-settings/severed-lands",
        "src/sylvia_gm/agent/skills",
    ]
    legitimate = [
        "rulesets/srd-lite/content/fixture.json",
        "rulesets/srd-lite/content/roster-crosswalk.csv",
        "campaign-settings/severed-lands/campaigns/story.md",
        "src/sylvia_gm/agent/skills/build_fixture/SKILL.md",
        "src/sylvia_gm/agent/skills/build_fixture/references/example.json",
    ]
    for name in legitimate:
        put(tmp_path, name)
    put(
        tmp_path,
        "src-tauri/sidecar-data-files.json",
        json.dumps({"format": 1, "files": legitimate}),
    )
    sentinels = []
    for folder in roots:
        for suffix in [
            ".env.synthetic",
            "nested/private-trace.jsonl",
            "nested/user-save.sqlite",
            ".scratch/private-note.txt",
        ]:
            name = folder + "/" + suffix
            put(tmp_path, name)
            sentinels.append(name)
    put(tmp_path, "config/config.yaml")
    put(tmp_path, "config/unselected-private.yaml")
    put(tmp_path, "src/sylvia_gm/campaign/the-rift-awakening.json", "{}")
    put(tmp_path, "private/not-in-any-source.jsonl")
    calls = []

    def fake_run(command, *, cwd, env, check):
        assert command[:3] == [sys.executable, "-m", "PyInstaller"]
        assert cwd == tmp_path and check is True
        assert "PYTHONHOME" not in env and "PYTHONPATH" not in env
        calls.append(command)
        dist = Path(command[command.index("--distpath") + 1])
        put(dist, "sylvia-gm-owner.exe", b"SYNTHETIC EXECUTABLE PLACEHOLDER")
        return subprocess.CompletedProcess(command, 0)

    with (
        patch.dict(os.environ, {
            "TAURI_ENV_TARGET_TRIPLE": "x86_64-pc-windows-msvc",
            "SYLVIA_KEEP_OWNER_BUILD": "1",  # This test inspects staged payloads after return.
        }),
        patch.object(module.subprocess, "run", fake_run),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        assert module.main() == 0
    assert len(calls) == 1
    command = calls[0]
    data = [command[i + 1] for i, arg in enumerate(command[:-1]) if arg == "--add-data"]
    selectors = []
    included = set()
    for entry in data:
        source, destination = entry.split(os.pathsep, 1)
        path = Path(source)
        assert path.resolve().is_relative_to(tmp_path.resolve())
        selectors.append(
            {
                "source": path.relative_to(tmp_path).as_posix(),
                "destination": destination,
                "kind": "directory" if path.is_dir() else "file",
            }
        )
        paths = path.rglob("*") if path.is_dir() else [path]
        for payload in paths:
            if not payload.is_file():
                continue
            logical = (
                Path(destination) / payload.relative_to(path)
                if path.is_dir()
                else Path(destination) / payload.name
            )
            included.add(logical.as_posix())
    forbidden_destinations = {name.removeprefix("src/") for name in sentinels}
    observed = sorted(forbidden_destinations & included)
    assert observed == []
    legitimate_destinations = {name.removeprefix("src/") for name in legitimate}
    assert legitimate_destinations.issubset(included)
    assert "config/unselected-private.yaml" not in included
    assert "private/not-in-any-source.jsonl" not in included
    staged_directories = [x["source"] for x in selectors if x["kind"] == "directory"]
    assert len(staged_directories) == 3
    assert all(path.startswith(".owner-build/") for path in staged_directories)
    assert all(path not in roots for path in staged_directories)
    (EVIDENCE / "sidecar-directory-handoff.json").write_text(
        json.dumps(
            {
                "actual_command_constructor": "src-tauri/scripts/build_owner_sidecar.py:main",
                "subprocess_intercepted": True,
                "pyinstaller_ran": False,
                "fixture_contents": "dummy synthetic sentinels only",
                "add_data_selectors": selectors,
                "whole_directory_sentinels_covered": observed,
                "covered_count": len(observed),
                "release_exclusion_criterion": "passed at declared-source handoff",
                "limit": (
                    "Staged data coverage verified; PyInstaller traversal/filtering and final "
                    "executable bytes were not run or certified."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_outside_root_catalogue_is_rejected_before_read(tmp_path, monkeypatch):
    root = tmp_path / "selected"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    catalogue(outside)
    kind = directory_link(root / "catalogue-dir", outside)
    assert kind in {"directory symlink", "directory junction (symlink privilege unavailable)"}
    catalogue_path = root / "catalogue-dir/catalogue.json"
    # A linked catalogue directory lets this check run without file-symlink privileges.
    module = sys.modules[bundled_plan_files.__module__]
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        reads.append(path.resolve())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    # The selected root is deliberately the link's parent, not the external target.
    with pytest.raises(ValueError, match="outside the selected collection"):
        module._contained_plan_path(root, catalogue_path)
    assert not reads


def test_catalogue_read_checks_containment_first(tmp_path, monkeypatch):
    root = tmp_path / "selected"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    catalogue(outside)
    module = sys.modules[bundled_plan_files.__module__]
    original_resolve = Path.resolve
    original_read = Path.read_text
    reads = []

    def resolve(path, *args, **kwargs):
        if path == root / "catalogue.json":
            return outside / "catalogue.json"
        return original_resolve(path, *args, **kwargs)

    def read(path, *args, **kwargs):
        reads.append(path)
        if path == root / "catalogue.json":
            return original_read(outside / "catalogue.json", *args, **kwargs)
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(ValueError, match="outside the selected collection"):
        module.read_plan_catalogue(root)
    assert not reads


def load_sidecar():
    spec = importlib.util.spec_from_file_location(
        "fc01_sidecar_inventory", FROZEN / "src-tauri/scripts/build_owner_sidecar.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inventory_fixture(root):
    names = [
        "rulesets/srd-lite/content/fixture.json",
        "campaign-settings/severed-lands/campaigns/story.md",
        "src/sylvia_gm/agent/skills/build_fixture/SKILL.md",
    ]
    for name in names:
        put(root, name)
    put(root, "src-tauri/sidecar-data-files.json", json.dumps({"format": 1, "files": names}))
    run = root / ".owner-build/run"
    run.mkdir(parents=True)
    return names, run / "data"


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {"format": True, "files": ["a"]},
        {"format": 2, "files": ["a"]},
        {"format": 1, "files": []},
        {"format": 1, "files": "a"},
        {"format": 1, "files": ["a"], "unexpected": True},
    ],
)
def test_invalid_data_inventory_is_rejected_before_staging(tmp_path, bad):
    _, staging = inventory_fixture(tmp_path)
    put(tmp_path, "src-tauri/sidecar-data-files.json", json.dumps(bad))
    with pytest.raises(ValueError, match="inventory is invalid"):
        load_sidecar().sidecar_data_arguments(tmp_path, staging)
    assert not staging.exists()


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.json",
        "/outside.json",
        "C:/outside.json",
        r"rulesets\outside.json",
        "rulesets/srd-lite/../outside.json",
        "private/hidden.json",
    ],
)
def test_invalid_data_inventory_path_is_rejected_before_staging(tmp_path, bad_path):
    names, staging = inventory_fixture(tmp_path)
    put(
        tmp_path,
        "src-tauri/sidecar-data-files.json",
        json.dumps({"format": 1, "files": [*names, bad_path]}),
    )
    with pytest.raises(ValueError):
        load_sidecar().sidecar_data_arguments(tmp_path, staging)
    assert not staging.exists()


def test_duplicate_data_inventory_path_rejected(tmp_path):
    names, staging = inventory_fixture(tmp_path)
    put(
        tmp_path,
        "src-tauri/sidecar-data-files.json",
        json.dumps({"format": 1, "files": [*names, names[0].upper()]}),
    )
    with pytest.raises(ValueError, match="duplicated"):
        load_sidecar().sidecar_data_arguments(tmp_path, staging)
    assert not staging.exists()


def test_missing_declared_data_file_rejected_before_staging(tmp_path):
    names, staging = inventory_fixture(tmp_path)
    (tmp_path / names[-1]).unlink()
    with pytest.raises(ValueError, match="file is missing or outside"):
        load_sidecar().sidecar_data_arguments(tmp_path, staging)
    assert not staging.exists()


def test_missing_data_root_is_rejected_before_staging(tmp_path):
    names, staging = inventory_fixture(tmp_path)
    put(
        tmp_path,
        "src-tauri/sidecar-data-files.json",
        json.dumps({"format": 1, "files": names[:-1]}),
    )
    with pytest.raises(ValueError, match="every declared data root"):
        load_sidecar().sidecar_data_arguments(tmp_path, staging)
    assert not staging.exists()


def test_unlisted_private_siblings_are_neither_walked_nor_staged(tmp_path, monkeypatch):
    names, staging = inventory_fixture(tmp_path)
    private = "rulesets/srd-lite/.scratch/.env.synthetic"
    put(tmp_path, private)
    original_iterdir = Path.iterdir

    def iterdir(path):
        if path == tmp_path / "rulesets/srd-lite":
            raise AssertionError("Raw source directory must not be enumerated")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    data = load_sidecar().sidecar_data_arguments(tmp_path, staging)
    assert len(data) == 6
    expected = {name.removeprefix("src/") for name in names}
    actual = {p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()}
    assert actual == expected
    assert not (staging / private).exists()


def test_existing_data_staging_is_rejected(tmp_path):
    _, staging = inventory_fixture(tmp_path)
    put(staging, "stale-private.jsonl")
    with pytest.raises(FileExistsError):
        load_sidecar().sidecar_data_arguments(tmp_path, staging)
    assert {p.name for p in staging.iterdir()} == {"stale-private.jsonl"}


def test_staging_outside_owned_build_root_is_rejected(tmp_path):
    inventory_fixture(tmp_path)
    staging = tmp_path / "unowned-data"
    with pytest.raises(ValueError, match="staging is outside"):
        load_sidecar().sidecar_data_arguments(tmp_path, staging)
    assert not staging.exists()


def test_linked_data_inventory_rejected_before_read(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    put(outside, "sidecar-data-files.json", "{}")
    directory_link(root / "src-tauri", outside)
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        reads.append(path.resolve())
        return original(path, *args, **kwargs)

    module = load_sidecar()
    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(ValueError, match="file is missing or outside"):
        module.sidecar_data_arguments(root, root / ".owner-build/run/data")
    assert not reads


def test_linked_data_file_rejected_before_copy(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    names, staging = inventory_fixture(root)
    outside = tmp_path / "outside"
    put(outside, "content/fixture.json")
    content = root / "rulesets/srd-lite/content"
    (content / "fixture.json").unlink()
    content.rmdir()
    directory_link(content, outside / "content")
    module = load_sidecar()
    copied = []
    monkeypatch.setattr(module.shutil, "copy2", lambda *args, **kwargs: copied.append(args))
    with pytest.raises(ValueError, match="file is missing or outside"):
        module.sidecar_data_arguments(root, staging)
    assert not staging.exists()
    assert not copied


def test_linked_owned_build_root_outside_repo_is_rejected(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    names, _ = inventory_fixture(root)
    run = root / ".owner-build/run"
    run.rmdir()
    (root / ".owner-build").rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    directory_link(root / ".owner-build", outside)
    with pytest.raises(ValueError, match="staging is outside"):
        load_sidecar().sidecar_data_arguments(root, root / ".owner-build/data")
    assert not (outside / "data").exists()


def test_unlisted_inside_root_alias_is_rejected_before_copy(tmp_path, monkeypatch):
    names, staging = inventory_fixture(tmp_path)
    root = tmp_path / "rulesets/srd-lite"
    target = root / ".scratch/alias"
    put(target, "fixture.json")
    content = root / "content"
    (content / "fixture.json").unlink()
    content.rmdir()
    directory_link(content, target)
    module = load_sidecar()
    copied = []
    monkeypatch.setattr(module.shutil, "copy2", lambda *args, **kwargs: copied.append(args))
    with pytest.raises(ValueError, match="aliases are not allowed"):
        module.sidecar_data_arguments(tmp_path, staging)
    assert not copied
    assert not staging.exists()
