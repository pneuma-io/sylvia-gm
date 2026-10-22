"""Ruleset browsing defers party construction; complete discovery stays complete."""

import json
from types import SimpleNamespace

import pytest

from sylvia_gm.application import bundled_starter_characters
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.reusable_participants import load_reusable_participant
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.interfaces import owner_process as owner
from tests.test_campaign_library_selection import seed_campaign_library


def test_setup_catalog_defers_only_participants_and_default_discovery_is_complete(
    monkeypatch, tmp_path
):
    rules = {"id": "example.rules", "version": "1", "content_sha256": "a" * 64}
    setting = {"id": "example.setting", "version": "1", "content_sha256": "b" * 64}
    rows = (
        {
            "kind": "ruleset",
            **rules,
            "title": "Rules",
            "selection_id": rules["id"],
            "compatible_settings": (),
        },
        {"kind": "campaign_setting", **setting, "title": "Setting"},
    )
    monkeypatch.setattr(owner, "bundled_campaign_content_catalog", lambda: rows)
    monkeypatch.setattr(
        owner,
        "bundled_rift_authority",
        lambda: SimpleNamespace(
            ruleset=SimpleNamespace(as_payload=lambda: rules),
            setting=SimpleNamespace(as_payload=lambda: setting),
        ),
    )
    monkeypatch.setattr(owner, "character_profile_catalog", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(owner, "reusable_campaign_plan_catalog", lambda *_args, **_kwargs: ())
    calls = []

    def participants(*_args, **_kwargs):
        calls.append(_kwargs["include_starters"])
        return ({"kind": "player_character", "id": "a-real-validated-character"},)

    monkeypatch.setattr(owner, "_reusable_participant_catalog", participants)
    monkeypatch.setattr(owner, "participant_catalog_payload", lambda _owner, value: value)
    authority = SimpleNamespace(package_candidates=lambda: ())
    metadata = owner._campaign_creation_catalog(tmp_path, authority, include_participants=False)
    assert calls == [False]
    complete = owner._campaign_creation_catalog(tmp_path, authority)
    assert calls == [False, True]
    assert complete == (*metadata, {"kind": "player_character", "id": "a-real-validated-character"})


@pytest.mark.parametrize("damage", ["malformed_json", "invalid_state"])
def test_metadata_only_setup_keeps_actual_saved_character_repair_warnings(
    monkeypatch, tmp_path, damage
):
    with AuthorityOwner.open(tmp_path / "saved.sqlite3", "saved-character-warning") as authority:
        candidate, _setting_id, _plan, characters = seed_campaign_library(authority, tmp_path)
        primary = next(
            row for row in candidate.package_lock.entries if row.role is PackageRole.PRIMARY
        )
        declaration = authority.package_participant_declarations(candidate.candidate_id)[0]
        sources = owner.campaign_plan_sources(
            authority, candidate.package_lock, setting_id=_setting_id
        )
        assert sources is not None
        monkeypatch.setattr(owner, "bundled_campaign_content_catalog", lambda: ())
        monkeypatch.setattr(owner, "bundled_rift_authority", lambda: SimpleNamespace(
            ruleset=sources.ruleset, setting=sources.setting,
        ))
        monkeypatch.setattr(owner, "_rift_participant_authority", lambda: (primary, declaration))
        starter_calls = []

        def starters(*_args, **_kwargs):
            starter_calls.append(True)
            return ()

        monkeypatch.setattr(bundled_starter_characters, "bundled_starter_participants", starters)
        invalid, healthy = characters
        initial_metadata = owner._campaign_creation_catalog(
            tmp_path, authority, include_participants=False
        )
        assert not starter_calls
        valid_catalog = owner._campaign_creation_catalog(tmp_path, authority)
        assert initial_metadata == tuple(
            item for item in valid_catalog
            if item.get("id") not in {invalid.artifact_id, healthy.artifact_id}
        )
        starter_calls.clear()
        path = tmp_path / "participants" / invalid.artifact_id / "payload.json"
        if damage == "malformed_json":
            path.write_bytes(b'{"participant":')
        else:
            payload = json.loads(path.read_text())
            payload["entity_state"]["stress"] = "PRIVATE SAVED VALUE"
            path.write_text(json.dumps(payload))
        damaged_bytes = path.read_bytes()
        with pytest.raises((ValueError, CommandRejected)):
            load_reusable_participant(tmp_path, invalid.artifact_id, package=primary,
                                      declaration=declaration, campaign_setting=sources.setting)

        metadata = owner._campaign_creation_catalog(tmp_path, authority, include_participants=False)
        warnings = [item for item in metadata if item.get("kind") == "unavailable_content"
                    and item.get("id") == invalid.artifact_id]
        assert len(warnings) == 1, "The first Ruleset screen must retain character repair warnings"
        assert not starter_calls, "Warning discovery must not construct the built-in roster"
        assert not any(item.get("id") == healthy.artifact_id for item in metadata)
        warning = warnings[0]
        assert warning["content_kind"] == "participant"
        assert warning["title"] == "Saved character unavailable"
        assert "Repair a copy in Builder" in warning["summary"]
        assert "PRIVATE SAVED VALUE" not in str(warning)
        if damage == "invalid_state":
            assert warning["validation_errors"]
            assert warning["validation_errors"][0]["path"] == "stress"

        complete = owner._campaign_creation_catalog(tmp_path, authority)
        assert [item for item in complete if item.get("kind") == "unavailable_content"
                and item.get("id") == invalid.artifact_id] == warnings
        assert len([item for item in complete if item.get("id") == healthy.artifact_id]) == 1
        healthy_catalog = tuple(
            item for item in complete if item.get("kind") != "unavailable_content"
        )
        assert healthy_catalog == tuple(
            item for item in valid_catalog if item.get("id") != invalid.artifact_id
        )
        assert path.read_bytes() == damaged_bytes


@pytest.mark.parametrize("library_state", ["absent", "empty"])
def test_empty_metadata_discovery_never_loads_the_roster_or_its_authority(
    monkeypatch, tmp_path, library_state
):
    if library_state == "empty":
        (tmp_path / "participants").mkdir()

    def unexpected(*_args, **_kwargs):
        pytest.fail("An empty saved library must not discover or construct the built-in roster")

    monkeypatch.setattr(owner, "_rift_reusable_participants", unexpected)
    assert owner._reusable_participant_catalog(
        tmp_path, SimpleNamespace(), include_starters=False
    ) == ()


@pytest.mark.parametrize("entry_kind", ["file", "directory"])
def test_nonempty_saved_library_still_reaches_validation(monkeypatch, tmp_path, entry_kind):
    root = tmp_path / "participants"
    root.mkdir()
    entry = root / "malformed-entry"
    if entry_kind == "file":
        entry.write_bytes(b"malformed character data")
    else:
        entry.mkdir()

    class ValidationReachedError(Exception):
        pass

    def validate(*_args, **kwargs):
        assert kwargs["include_starters"] is False
        raise ValidationReachedError

    monkeypatch.setattr(owner, "_rift_reusable_participants", validate)
    with pytest.raises(ValidationReachedError):
        owner._reusable_participant_catalog(tmp_path, SimpleNamespace(), include_starters=False)
    assert entry.exists()


def test_complete_discovery_keeps_starters_with_an_empty_saved_library(monkeypatch, tmp_path):
    (tmp_path / "participants").mkdir()
    calls = []

    def discover(*_args, **kwargs):
        calls.append(kwargs["include_starters"])
        return ()

    monkeypatch.setattr(owner, "_rift_reusable_participants", discover)
    assert owner._reusable_participant_catalog(
        tmp_path, SimpleNamespace(package_candidates=lambda: ())
    ) == ()
    assert calls == [True]


def test_a_file_at_the_saved_library_root_is_not_treated_as_an_empty_roster(tmp_path):
    path = tmp_path / "participants"
    path.write_bytes(b"not a participant directory")
    with pytest.raises(NotADirectoryError):
        owner._reusable_participant_catalog(tmp_path, SimpleNamespace(), include_starters=False)
    assert path.read_bytes() == b"not a participant directory"
