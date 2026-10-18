"""Native PES-44 boundaries, independent of external card conversion."""

import hashlib
import json
from copy import deepcopy

import pytest

from sylvia_gm.agent._character_profile_tools import character_profile_tools
from sylvia_gm.application.character_profiles import (
    load_character_profile,
    save_character_profile,
    validate_character_profile,
)
from sylvia_gm.domain._content_schema_character_profile_v2 import EXAMPLE
from sylvia_gm.domain.package_library import canonical_json_bytes


def native_tools(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tools = {
        item.name: item
        for item in character_profile_tools(
            workspace,
            save=lambda data: save_character_profile(
                tmp_path,
                operation_id="native-contract",
                source=data,
            ).catalog_payload(),
            read=lambda identity, digest: load_character_profile(tmp_path, identity, digest),
        )
    }
    return workspace, tools


@pytest.mark.parametrize("digest", ["bad", "a" * 63, "g" * 64, "a" * 65])
def test_asset_digest_reference_requires_an_exact_sha256(digest):
    profile = deepcopy(EXAMPLE)
    profile["assets"][0]["uri"] = "asset:sha256:" + digest
    with pytest.raises(ValueError):
        validate_character_profile(canonical_json_bytes(profile))


@pytest.mark.parametrize(
    "case,path",
    [
        ("duplicate", "$.lore.entries[1].id"),
        ("credentials", "$.assets[0].uri"),
        ("malformed", "$"),
        ("limit", "$"),
        ("nonfinite", "$"),
    ],
)
def test_native_draft_errors_tell_builder_what_to_repair(tmp_path, case, path):
    workspace, tools = native_tools(tmp_path)
    profile = deepcopy(EXAMPLE)
    if case == "duplicate":
        profile["lore"]["entries"].append(deepcopy(profile["lore"]["entries"][0]))
    if case == "credentials":
        profile["assets"][0]["uri"] = "https://user:private@invalid.example/image"
    raw = canonical_json_bytes(profile)
    if case == "malformed":
        raw = b'{"private":'
    if case == "limit":
        raw = b" " * (256 * 1024 + 1)
    if case == "nonfinite":
        raw = b'{"private":1e400}'
    (workspace / "source.json").write_bytes(raw)
    result = tools["check_character_profile_draft"].handler({"path": "source.json"}, None)
    assert not result["success"]
    assert any(item["path"] == path and item["message"] for item in result["validation_errors"]), (
        result
    )
    assert "private" not in json.dumps(result)
    assert (workspace / "source.json").read_bytes() == raw
    assert not (tmp_path / "character-profiles").exists()


def test_native_rich_section_edit_copy_and_reopen_preserve_source(tmp_path):
    workspace, tools = native_tools(tmp_path)
    profile = deepcopy(EXAMPLE)
    profile["identity"]["aliases"] = ["  Lantern\n", "Vela"]
    profile["biography"]["description"] = "  Exact text.\n\n"
    profile["portrayal"]["context"] = "Imagined context, not an event."
    profile["lore"]["entries"][0]["foreign_configuration"] = {
        "status": "preserved_only",
        "format": "synthetic",
        "settings": {"script": "fetch('https://invalid.example')", "regex": True},
    }
    profile["assets"][0]["uri"] = "asset:sha256:" + "a" * 64
    raw = canonical_json_bytes(profile)
    (workspace / "source.json").write_bytes(raw)
    check = tools["check_character_profile_draft"].handler({"path": "source.json"}, None)
    assert check["success"] and not check["published"]
    saved = tools["publish_character_profile"].handler(
        {
            "path": "source.json",
            "expected_version": check["version"],
        },
        None,
    )
    assert saved["success"] and not saved["campaign_ready"]
    before = (tmp_path / "character-profiles" / saved["id"] / "profile.json").read_bytes()
    base = {"artifact_id": saved["id"], "expected_content_sha256": saved["content_sha256"]}
    for section in (
        "identity",
        "biography",
        "portrayal",
        "greetings",
        "dialogue_examples",
        "lore",
        "metadata",
        "assets",
        "foreign_configuration",
    ):
        result = tools["read_character_profile_section"].handler({**base, "section": section}, None)
        assert result["success"] and result["present"]
        assert result["content"] == profile[section]
        assert result["played_history"] is False and result["foreign_behavior"] == "preserved_only"
    entry = tools["read_character_profile_section"].handler(
        {
            **base,
            "section": "lore",
            "entry_id": "archive",
        },
        None,
    )
    assert entry["content"] == profile["lore"]["entries"][0]
    assert entry["content"]["enabled"] is False and entry["content"]["keys"] == []
    absent = tools["read_character_profile_section"].handler(
        {**base, "section": "import_source"}, None
    )
    assert absent["success"] and not absent["present"]
    opened = tools["open_character_profile"].handler(
        {**base, "destination_path": "edit.json"}, None
    )
    assert opened["success"]
    edited = json.loads((workspace / "edit.json").read_bytes())
    assert edited == profile
    edited["biography"]["voice"] = "New voice."
    edited_bytes = canonical_json_bytes(edited)
    (workspace / "edit.json").write_bytes(edited_bytes)
    stale = tools["publish_character_profile"].handler(
        {
            "path": "edit.json",
            "expected_version": opened["version"],
        },
        None,
    )
    assert stale["code"] == "stale_file"
    copy = tools["publish_character_profile"].handler(
        {
            "path": "edit.json",
            "expected_version": hashlib.sha256(edited_bytes).hexdigest(),
        },
        None,
    )
    assert copy["success"] and copy["id"] != saved["id"]
    assert load_character_profile(tmp_path, copy["id"]).profile == edited
    assert load_character_profile(tmp_path, saved["id"]).profile == profile
    assert (tmp_path / "character-profiles" / saved["id"] / "profile.json").read_bytes() == before


def test_large_native_lore_is_retrieved_by_id_without_truncation(tmp_path):
    workspace, tools = native_tools(tmp_path)
    profile = deepcopy(EXAMPLE)
    profile["lore"]["entries"] = [{"id": str(i), "content": str(i) * 40000} for i in range(2)]
    saved = save_character_profile(
        tmp_path, operation_id="large", source=canonical_json_bytes(profile)
    )
    base = {"artifact_id": saved.artifact_id, "expected_content_sha256": saved.content_sha256}
    whole = tools["read_character_profile_section"].handler({**base, "section": "lore"}, None)
    assert not whole["success"] and "64 KiB" in whole["correction"]
    for i in range(2):
        one = tools["read_character_profile_section"].handler(
            {**base, "section": "lore", "entry_id": str(i)}, None
        )
        assert one["success"] and one["content"] == profile["lore"]["entries"][i]
    opened = tools["open_character_profile"].handler(
        {**base, "destination_path": "whole.json"}, None
    )
    assert opened["success"] and json.loads((workspace / "whole.json").read_bytes()) == profile
