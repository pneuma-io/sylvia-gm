"""Card lifecycle and collision regressions exercise the real disk-backed importer."""

import json
import os
import socket
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sylvia_gm.agent._character_profile_tools import character_profile_tools
from sylvia_gm.application import character_card_import as importer
from sylvia_gm.application.character_profiles import load_character_profile, save_character_profile
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_rich_character_profiles import card, png


def source_file(root, index=0, version=1):
    value = card(version)
    data = value if version == 1 else value["data"]
    data["name"] = f"Card {index}"
    source = root / f"card-{index}.json"
    raw = json.dumps(value).encode()
    source.write_bytes(raw)
    return source, raw


def test_more_than_64_successful_imports_keep_originals_profiles_and_retries(tmp_path):
    saved_cards = []
    for index in range(80):
        source, raw = source_file(tmp_path, index)
        staged = importer.stage_card_preview(tmp_path, source)
        digest = staged["source_sha256"]
        saved = importer.confirm_card_preview(tmp_path, digest)
        assert importer.confirm_card_preview(Path(str(tmp_path)), digest) == saved
        saved_cards.append((digest, raw, saved))
    assert not list((tmp_path / "character-card-previews").iterdir())
    assert len(list((tmp_path / "character-profiles").iterdir())) == 80
    for digest, raw, saved in saved_cards:
        assert importer.load_import_original(tmp_path, digest) == raw
        profile = load_character_profile(tmp_path, saved["profile"]["id"])
        assert profile.profile == importer.preview_character_card(raw).profile
        assert saved["profile"]["campaign_ready"] is False


def test_64_real_pending_previews_still_block_new_sources(tmp_path):
    staged = []
    for index in range(64):
        source, raw = source_file(tmp_path, index)
        staged.append((source, raw, importer.stage_card_preview(tmp_path, source)))
    overflow, _ = source_file(tmp_path, 64)
    with pytest.raises(ValueError, match="capacity"):
        importer.stage_card_preview(tmp_path, overflow)
    assert importer.stage_card_preview(tmp_path, staged[0][0]) == staged[0][2]
    assert len(list((tmp_path / "character-card-previews").iterdir())) == 64
    assert not (tmp_path / "character-profiles").exists()
    importer.confirm_card_preview(tmp_path, staged[0][2]["source_sha256"])
    importer.stage_card_preview(tmp_path, overflow)
    assert len(list((tmp_path / "character-card-previews").iterdir())) == 64


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("container", ["json", "png"])
def test_collision_preserves_data_and_root_fields_with_truthful_report(
    tmp_path, version, container
):
    value = card(version)
    data = value if version == 1 else value["data"]
    data["outer_extensions"] = {"inner": "must preserve", "nested": {"outer_unknown": None}}
    data["outer_extensions_root"] = {"occupied": [False, ""]}
    if version > 1:
        value["outer_unknown"] = {"outer": "value", "nested": {"outer_extensions": [1, None]}}
        value["outer_extensions"] = {"root": "must also preserve"}
        data["extensions"]["outer_extensions"] = {"deep": "inert"}
    raw = (
        json.dumps(value).encode()
        if container == "json"
        else png(value, "ccv3" if version == 3 else "chara")
    )
    source = tmp_path / ("card." + container)
    source.write_bytes(raw)
    preview = importer.preview_character_card(raw)
    foreign = preview.profile["foreign_configuration"]["settings"]
    assert foreign["outer_extensions"] == data["outer_extensions"]
    assert foreign["outer_extensions_root"] == data["outer_extensions_root"]
    if version > 1:
        assert foreign["outer_extensions_root_1"] == {
            "outer_unknown": value["outer_unknown"],
            "outer_extensions": value["outer_extensions"],
        }
        assert foreign["extensions"] == data["extensions"]
        report = "\n".join(preview.report()["preserved_only"])
        assert "data.outer_extensions" in report
        assert "root.outer_unknown" in report
        assert "outer_extensions_root_1" in "\n".join(preview.report()["transformed"])
    staged = importer.stage_card_preview(tmp_path, source)
    source.write_text("{}")
    saved = importer.confirm_card_preview(tmp_path, staged["source_sha256"])
    assert saved["profile"]["conversion_preview"] == preview.report()
    assert load_character_profile(tmp_path, saved["profile"]["id"]).profile == preview.profile
    assert importer.load_import_original(tmp_path, preview.source_sha256) == raw
    assert importer.confirm_card_preview(tmp_path, preview.source_sha256) == saved


def test_old_completed_previews_retire_only_after_verified_save(tmp_path):
    pending = tmp_path / "character-card-previews"
    pending.mkdir()
    records = []
    for index in range(64):
        _, raw = source_file(tmp_path, index)
        digest = importer.preview_character_card(raw).source_sha256
        saved = importer.save_character_card_import(
            tmp_path, operation_id="desktop-card:" + digest, source=raw
        )
        (pending / digest).write_bytes(raw)  # On-disk state left by the frozen importer.
        records.append((digest, raw, saved))
    source, _ = source_file(tmp_path, 64)
    importer.stage_card_preview(tmp_path, source)
    assert len(list(pending.iterdir())) == 1
    for digest, raw, saved in records:
        assert importer.load_import_original(tmp_path, digest) == raw
        assert importer.confirm_card_preview(tmp_path, digest)["profile"] == saved


def test_failed_profile_save_keeps_pending_and_original_for_retry(tmp_path, monkeypatch):
    source, raw = source_file(tmp_path)
    digest = importer.stage_card_preview(tmp_path, source)["source_sha256"]
    save = importer.save_character_profile

    def fail(*args, **kwargs):
        raise OSError("Synthetic disk failure")

    monkeypatch.setattr(importer, "save_character_profile", fail)
    with pytest.raises(OSError, match="Synthetic disk"):
        importer.confirm_card_preview(tmp_path, digest)
    assert (tmp_path / "character-card-previews" / digest).read_bytes() == raw
    assert importer.load_import_original(tmp_path, digest) == raw
    # An immutable original without its successful desktop profile is still pending.
    another, _ = source_file(tmp_path, 1)
    importer.stage_card_preview(tmp_path, another)
    assert (tmp_path / "character-card-previews" / digest).exists()
    monkeypatch.setattr(importer, "save_character_profile", save)
    saved = importer.confirm_card_preview(tmp_path, digest)
    assert importer.confirm_card_preview(tmp_path, digest) == saved


def test_retirement_failure_can_retry_without_duplicate_profiles(tmp_path, monkeypatch):
    source, raw = source_file(tmp_path)
    digest = importer.stage_card_preview(tmp_path, source)["source_sha256"]
    pending = tmp_path / "character-card-previews" / digest
    unlink = Path.unlink

    def fail(path, *args, **kwargs):
        if path == pending:
            raise PermissionError("Synthetic retirement failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail)
    with pytest.raises(PermissionError, match="Synthetic retirement"):
        importer.confirm_card_preview(tmp_path, digest)
    assert pending.read_bytes() == raw
    assert importer.load_import_original(tmp_path, digest) == raw
    assert len(list((tmp_path / "character-profiles").iterdir())) == 1
    monkeypatch.setattr(Path, "unlink", unlink)
    saved = importer.confirm_card_preview(tmp_path, digest)
    assert importer.confirm_card_preview(tmp_path, digest) == saved
    assert len(list((tmp_path / "character-profiles").iterdir())) == 1


@pytest.mark.parametrize("which", ["original", "original-record", "profile", "profile-record"])
def test_retry_revalidates_saved_bytes_and_never_rewrites_tamper(tmp_path, which):
    source, _ = source_file(tmp_path)
    digest = importer.stage_card_preview(tmp_path, source)["source_sha256"]
    saved = importer.confirm_card_preview(tmp_path, digest)
    original = tmp_path / "character-card-imports" / digest
    profile = tmp_path / "character-profiles" / saved["profile"]["id"]
    target = {
        "original": original / "source.bin",
        "original-record": original / "record.json",
        "profile": profile / "profile.json",
        "profile-record": profile / "record.json",
    }[which]
    target.write_bytes(b"{}")
    with pytest.raises(ValueError):
        importer.confirm_card_preview(Path(str(tmp_path)), digest)
    assert target.read_bytes() == b"{}"


def test_pending_digest_remains_bound_to_captured_bytes(tmp_path):
    source, raw = source_file(tmp_path, 0, version=2)
    digest = importer.stage_card_preview(tmp_path, source)["source_sha256"]
    _, other = source_file(tmp_path, 1, version=2)
    source.write_bytes(other)
    pending = tmp_path / "character-card-previews" / digest
    pending.write_bytes(other)
    with pytest.raises(ValueError, match="changed"):
        importer.confirm_card_preview(tmp_path, digest)
    assert pending.read_bytes() == other
    assert not (tmp_path / "character-profiles").exists()
    assert not (tmp_path / "character-card-imports").exists()
    pending.write_bytes(raw)
    saved = importer.confirm_card_preview(tmp_path, digest)
    assert importer.load_import_original(tmp_path, digest) == raw
    assert load_character_profile(tmp_path, saved["profile"]["id"]).profile["name"] == "Card 0"


@pytest.mark.parametrize("digest", ["x", "../escape", "A" * 64, "a" * 64])
def test_missing_or_invalid_confirmation_never_creates_data(tmp_path, digest):
    with pytest.raises(ValueError, match="preview"):
        importer.confirm_card_preview(tmp_path, digest)
    assert not list(tmp_path.iterdir())


def test_original_saved_by_builder_cannot_bypass_desktop_preview(tmp_path):
    _, raw = source_file(tmp_path)
    saved = importer.save_character_card_import(tmp_path, operation_id="builder", source=raw)
    digest = saved["import_source_sha256"]
    before = sorted(path.name for path in (tmp_path / "character-profiles").iterdir())
    with pytest.raises(ValueError, match="preview unavailable"):
        importer.confirm_card_preview(tmp_path, digest)
    assert sorted(path.name for path in (tmp_path / "character-profiles").iterdir()) == before


def test_unknown_and_unsafe_pending_entries_are_not_retired(tmp_path, monkeypatch):
    source, raw = source_file(tmp_path)
    digest = importer.stage_card_preview(tmp_path, source)["source_sha256"]
    importer.save_character_card_import(tmp_path, operation_id="desktop-card:" + digest, source=raw)
    pending = tmp_path / "character-card-previews" / digest
    unknown = pending.parent / "user-note.txt"
    unknown.write_bytes(b"Never remove unrelated data")
    check = importer.is_link_or_reparse
    monkeypatch.setattr(importer, "is_link_or_reparse", lambda p: p == pending or check(p))
    another, _ = source_file(tmp_path, 1)
    importer.stage_card_preview(tmp_path, another)
    assert pending.read_bytes() == raw
    assert unknown.read_bytes() == b"Never remove unrelated data"
    with pytest.raises(ValueError, match="Unsafe preview entry"):
        importer.confirm_card_preview(tmp_path, digest)


@pytest.mark.parametrize("operation", ["stage", "confirm"])
def test_preview_root_reparse_guard_blocks_traversal(tmp_path, monkeypatch, operation):
    source, _ = source_file(tmp_path)
    staged = importer.stage_card_preview(tmp_path, source)
    root = tmp_path / "character-card-previews"
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    check = importer.is_link_or_reparse
    monkeypatch.setattr(importer, "is_link_or_reparse", lambda p: p == root or check(p))
    with pytest.raises(ValueError, match="Unsafe preview storage"):
        if operation == "stage":
            importer.stage_card_preview(tmp_path, source)
        else:
            importer.confirm_card_preview(tmp_path, staged["source_sha256"])
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert not (tmp_path / "character-profiles").exists()


def test_concurrent_stage_calls_keep_actual_pending_bound(tmp_path):
    sources = [source_file(tmp_path, index)[0] for index in range(70)]

    def stage(source):
        try:
            return importer.stage_card_preview(tmp_path, source)["status"]
        except ValueError as error:
            assert "capacity" in str(error)
            return "capacity"

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(stage, sources))
    assert results.count("preview") == 64
    assert results.count("capacity") == 6
    assert len(list((tmp_path / "character-card-previews").iterdir())) == 64


def test_inert_collisions_do_not_execute_fetch_or_change_environment(tmp_path, monkeypatch):
    marker = tmp_path / "executed.txt"
    instruction = f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')"
    value = card(3)
    value["data"]["outer_extensions"] = {"script": instruction}
    value["data"]["extensions"] = {"url": "https://invalid.example/config", "tool": instruction}
    value["outer_unknown"] = {"script": "<script>fetch('https://invalid.example')</script>"}
    raw = json.dumps(value).encode()
    source = tmp_path / "inert.json"
    source.write_bytes(raw)

    def unexpected(*args, **kwargs):
        pytest.fail("Importer attempted script/process execution or a remote fetch")

    monkeypatch.setattr(os, "system", unexpected)
    monkeypatch.setattr(subprocess, "Popen", unexpected)
    monkeypatch.setattr(socket.socket, "connect", unexpected)
    monkeypatch.setattr(urllib.request, "urlopen", unexpected)
    environment = dict(os.environ)
    preview = importer.stage_card_preview(tmp_path, source)
    saved = importer.confirm_card_preview(tmp_path, preview["source_sha256"])
    reopened = load_character_profile(tmp_path, saved["profile"]["id"]).profile
    settings = reopened["foreign_configuration"]["settings"]
    assert settings["outer_extensions"]["script"] == instruction
    assert settings["outer_extensions_root"]["outer_unknown"] == value["outer_unknown"]
    assert reopened["assets"][0]["uri"] == "https://invalid.example/icon.png"
    assert reopened["foreign_configuration"]["status"] == "preserved_only"
    assert importer.load_import_original(tmp_path, preview["source_sha256"]) == raw
    assert not marker.exists()
    assert dict(os.environ) == environment


def test_builder_collision_preview_and_import_still_require_exact_version(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    value = card(2)
    value["data"]["outer_extensions"] = {"inner": "must preserve"}
    value["outer_unknown"] = {"outer": "value"}
    source = workspace / "card.json"
    raw = canonical_json_bytes(value)
    source.write_bytes(raw)
    tools = {
        tool.name: tool
        for tool in character_profile_tools(
            workspace,
            save=lambda _: pytest.fail("Unexpected native publish"),
            read=lambda ident, sha: load_character_profile(tmp_path, ident, sha),
            import_source=lambda data: importer.save_character_card_import(
                tmp_path, operation_id="builder", source=data
            ),
        )
    }

    def call(name, **params):
        return tools[name].handler(params, None)

    preview = call("preview_character_card_import", path="card.json")
    source.write_bytes(canonical_json_bytes(card(2)))
    stale = call("import_character_card", path="card.json", expected_version=preview["version"])
    assert stale["code"] == "stale_file"
    assert not (tmp_path / "character-profiles").exists()
    source.write_bytes(raw)
    saved = call("import_character_card", path="card.json", expected_version=preview["version"])
    assert saved["success"] and saved["campaign_ready"] is False
    settings = load_character_profile(tmp_path, saved["id"]).profile["foreign_configuration"][
        "settings"
    ]
    assert settings["outer_extensions"] == value["data"]["outer_extensions"]
    assert settings["outer_extensions_root"]["outer_unknown"] == value["outer_unknown"]


def test_existing_profile_library_limit_is_preserved(tmp_path):
    from sylvia_gm.domain._content_schema_character_profile import EXAMPLE

    existing = []
    for index in range(256):
        profile = save_character_profile(
            tmp_path, operation_id=f"existing-{index}", source=canonical_json_bytes(EXAMPLE)
        )
        existing.append((profile.artifact_id, profile.content_sha256))
    source, raw = source_file(tmp_path)
    digest = importer.stage_card_preview(tmp_path, source)["source_sha256"]
    with pytest.raises(ValueError, match="256-entry limit"):
        importer.confirm_card_preview(tmp_path, digest)
    assert (tmp_path / "character-card-previews" / digest).read_bytes() == raw
    assert len(list((tmp_path / "character-profiles").iterdir())) == 256
    for ident, sha in existing:
        assert load_character_profile(tmp_path, ident, sha).profile == EXAMPLE


def test_missing_saved_profile_cannot_recreate_a_retired_confirmation(tmp_path):
    source, raw = source_file(tmp_path)
    digest = importer.stage_card_preview(tmp_path, source)["source_sha256"]
    saved = importer.confirm_card_preview(tmp_path, digest)
    profile_path = tmp_path / "character-profiles" / saved["profile"]["id"]
    moved = tmp_path / "synthetic-removed-profile"
    profile_path.rename(moved)
    with pytest.raises(ValueError, match="preview unavailable"):
        importer.confirm_card_preview(tmp_path, digest)
    assert not profile_path.exists()
    assert (moved / "profile.json").is_file()
    assert importer.load_import_original(tmp_path, digest) == raw


@pytest.mark.parametrize("tamper", ["pending", "original", "profile"])
def test_legacy_tamper_is_left_intact_and_keeps_pending_slot(tmp_path, tamper):
    source, raw = source_file(tmp_path)
    digest = importer.stage_card_preview(tmp_path, source)["source_sha256"]
    saved = importer.save_character_card_import(
        tmp_path, operation_id="desktop-card:" + digest, source=raw
    )
    pending = tmp_path / "character-card-previews" / digest
    target = {
        "pending": pending,
        "original": tmp_path / "character-card-imports" / digest / "source.bin",
        "profile": tmp_path / "character-profiles" / saved["id"] / "profile.json",
    }[tamper]
    target.write_bytes(b"{}")
    another, _ = source_file(tmp_path, 1)
    importer.stage_card_preview(tmp_path, another)
    assert pending.exists()
    assert target.read_bytes() == b"{}"
    assert len(list(pending.parent.iterdir())) == 2
