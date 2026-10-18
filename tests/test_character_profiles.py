"""Unbound profile storage, guarded tools and legacy participant identity."""

import hashlib
import json
from pathlib import Path

import pytest

from sylvia_gm.agent._character_profile_tools import character_profile_tools
from sylvia_gm.application.character_profiles import (
    character_profile_catalog,
    load_character_profile,
    profile_from_snapshot,
    save_character_profile,
)
from sylvia_gm.application.reusable_participants import (
    load_reusable_participant,
    save_reusable_participant,
)
from sylvia_gm.domain._content_schema_character_profile import EXAMPLE
from sylvia_gm.domain.content_definitions import get_content_definition
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_character_content_definition import _authority


def source(**changes):
    return canonical_json_bytes({**EXAMPLE, **changes})


def test_profile_save_edit_retry_and_retained_snapshot(tmp_path):
    first = save_character_profile(tmp_path, operation_id="first", source=source())
    assert save_character_profile(tmp_path, operation_id="first", source=source()) == first
    second = save_character_profile(tmp_path, operation_id="first", source=source(name="Other"))
    assert second.artifact_id != first.artifact_id
    assert load_character_profile(tmp_path, first.artifact_id) == first
    assert profile_from_snapshot(first.snapshot()) == first
    assert "authored_profile" not in first.catalog_payload()
    assert first.catalog_payload()["campaign_ready"] is False
    assert len(character_profile_catalog(tmp_path, on_invalid=lambda *_: pytest.fail())) == 2
    with pytest.raises(CommandRejected, match="does not match"):
        load_character_profile(tmp_path, first.artifact_id, "0" * 64)
    bad = {**first.snapshot(), "profile": {**first.profile, "name": "Tampered"}}
    with pytest.raises(ValueError, match="digest"):
        profile_from_snapshot(bad)
    assert get_content_definition("character_profile")["example"] == EXAMPLE


@pytest.mark.parametrize(
    "data",
    [
        source(format_version=True),
        source(format_version=2),
        source(name=" padded"),
        source(name=""),
        source(summary="x" * 501),
        source(authored_profile="private\x00text"),
        source(participant={}),
        source(ruleset="none"),
        source(authored_profile="x" * (256 * 1024)),
        b'{"name":"private","name":"duplicate"}',
        b'{"name":NaN}',
        b"[" * 34 + b"0" + b"]" * 34,
    ],
    ids=[
        "boolean-version",
        "new-version",
        "padded-name",
        "empty-name",
        "long-summary",
        "nul",
        "participant",
        "ruleset",
        "oversized",
        "duplicate",
        "nonfinite",
        "deep",
    ],
)
def test_invalid_profile_does_not_publish_or_echo_content(tmp_path, data):
    with pytest.raises(ValueError) as error:
        save_character_profile(tmp_path, operation_id="invalid", source=data)
    assert "private" not in str(error.value)
    assert not (tmp_path / "character-profiles").exists()


def test_catalog_keeps_healthy_entries_and_rejects_tamper(tmp_path):
    first = save_character_profile(tmp_path, operation_id="first", source=source())
    broken = save_character_profile(tmp_path, operation_id="second", source=source(name="Other"))
    (tmp_path / "character-profiles" / broken.artifact_id / "profile.json").write_bytes(source())
    errors = []
    assert character_profile_catalog(tmp_path, on_invalid=lambda *args: errors.append(args)) == (
        first,
    )
    assert len(errors) == 1
    with pytest.raises(ValueError):
        load_character_profile(tmp_path, "../other")
    (tmp_path / "character-profiles" / first.artifact_id / "unexpected.txt").write_text("x")
    with pytest.raises(ValueError, match="unsafe"):
        load_character_profile(tmp_path, first.artifact_id)


def test_failed_save_never_exposes_partial_entry(tmp_path, monkeypatch):
    def fail_rename(*_args):
        raise OSError("Injected disk failure")

    monkeypatch.setattr(Path, "rename", fail_rename)
    with pytest.raises(OSError):
        save_character_profile(tmp_path, operation_id="fail", source=source())
    assert list((tmp_path / "character-profiles").iterdir()) == []


def test_tools_check_save_open_and_guard_versions(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tools = {
        item.name: item
        for item in character_profile_tools(
            workspace,
            save=lambda data: save_character_profile(
                tmp_path, operation_id="tool", source=data
            ).catalog_payload(),
            read=lambda identity, digest: load_character_profile(tmp_path, identity, digest),
        )
    }
    data = source(authored_profile="Full body " * 1500 + "END")
    (workspace / "source.json").write_bytes(data)
    check = tools["check_character_profile_draft"].handler({"path": "source.json"}, None)
    assert check["valid"] and not check["published"]
    assert not (tmp_path / "character-profiles").exists()
    publish = tools["publish_character_profile"]
    assert (
        publish.handler({"path": "source.json", "expected_version": "old"}, None)["code"]
        == "stale_file"
    )
    saved = publish.handler({"path": "source.json", "expected_version": check["version"]}, None)
    assert saved["success"] and not saved["campaign_ready"]
    opened = tools["open_character_profile"].handler(
        {
            "artifact_id": saved["id"],
            "expected_content_sha256": saved["content_sha256"],
            "destination_path": "copy.json",
        },
        None,
    )
    assert opened["success"]
    assert json.loads((workspace / "copy.json").read_text())["authored_profile"].endswith("END")
    assert (
        tools["open_character_profile"].handler(
            {
                "artifact_id": saved["id"],
                "expected_content_sha256": saved["content_sha256"],
                "destination_path": "copy.json",
            },
            None,
        )["code"]
        == "stale_file"
    )
    assert (
        tools["check_character_profile_draft"].handler({"path": "../outside"}, None)["code"]
        == "character_profile_path_denied"
    )


def test_v4_identity_survives_and_v5_provenance_is_hashed(tmp_path):
    package, declaration, setting = _authority()
    saved = save_reusable_participant(
        tmp_path,
        operation_id="old",
        role="companion",
        summary="Keeper",
        authored_profile="An archivist.",
        participant={"name": "Vela", "rank": 2},
        entity_state={"charge": 4},
        package=package,
        declaration=declaration,
        campaign_setting=setting,
    )
    folder = tmp_path / "participants" / saved.artifact_id
    record = json.loads((folder / "record.json").read_text())
    payload = json.loads((folder / "payload.json").read_text())
    identity = {
        key: record[key]
        for key in (
            "role",
            "summary",
            "package_id",
            "package_version",
            "package_sha256",
            "schema_id",
            "schema_sha256",
            "campaign_setting_id",
            "campaign_setting_version",
            "campaign_setting_sha256",
        )
    }
    identity.update(authored_profile="An archivist.", payload=payload)
    record.update(
        format_version=4, content_sha256=hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    )
    record.pop("source_profile")
    (folder / "record.json").write_bytes(canonical_json_bytes(record))
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    kwargs = {"package": package, "declaration": declaration, "campaign_setting": setting}
    old = load_reusable_participant(tmp_path, saved.artifact_id, **kwargs)
    assert old.content_sha256 == record["content_sha256"]
    assert old.authored_profile == "An archivist." and old.setting_data == {}
    assert before == {path.name: path.read_bytes() for path in folder.iterdir()}
    profile = save_character_profile(tmp_path, operation_id="base", source=source())
    adapted = save_reusable_participant(
        tmp_path,
        operation_id="new",
        role="companion",
        summary="Keeper",
        authored_profile="An archivist.",
        participant={"name": "Vela", "rank": 2},
        entity_state={"charge": 4},
        source_profile=profile.provenance(),
        **kwargs,
    )
    assert adapted.source_profile == profile.provenance()
    assert load_reusable_participant(tmp_path, adapted.artifact_id, **kwargs) == adapted
    new_path = tmp_path / "participants" / adapted.artifact_id / "record.json"
    corrupted = json.loads(new_path.read_text())
    corrupted["source_profile"]["name"] = "Other"
    new_path.write_bytes(canonical_json_bytes(corrupted))
    with pytest.raises(ValueError, match="hash"):
        load_reusable_participant(tmp_path, adapted.artifact_id, **kwargs)
