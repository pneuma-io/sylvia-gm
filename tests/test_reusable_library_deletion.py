from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from sylvia_gm.application import reusable_library_deletion
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.reusable_participants import (
    compatible_reusable_participants,
    delete_reusable_participant,
    save_reusable_participant,
)
from sylvia_gm.application.reusable_plans import (
    delete_reusable_campaign_plan,
    reusable_campaign_plan_catalog,
    save_reusable_campaign_plan,
)
from sylvia_gm.domain.authority import PackageLockEntry, PackageRole, SafePayload
from sylvia_gm.domain.campaign_plan import PackageBinding, ValidatedCampaignPlan
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import ParticipantArtifactDeclaration, canonical_json_bytes
from sylvia_gm.interfaces.owner_process import _dispatch


def _participant_context():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }
    package = PackageLockEntry(
        package_id="example.rules",
        version="1.0.0",
        sha256="1" * 64,
        role=PackageRole.PRIMARY,
        load_order=0,
        dependencies=(),
        capabilities=(),
    )
    declaration = ParticipantArtifactDeclaration(
        kind="character",
        schema_id="example.character",
        schema_sha256=hashlib.sha256(canonical_json_bytes(schema)).hexdigest(),
        schema=schema,
    )
    setting = PackageBinding("example.setting", "1.0.0", "2" * 64)
    return package, declaration, setting


def _save_participant(root: Path, operation_id: str, name: str):
    package, declaration, setting = _participant_context()
    return save_reusable_participant(
        root,
        operation_id=operation_id,
        role="companion",
        summary=f"Synthetic profile for {name}.",
        authored_profile=f"# {name}\n\nSynthetic profile.",
        participant={"name": name},
        entity_state={},
        package=package,
        declaration=declaration,
        campaign_setting=setting,
    )


def _plan_validator(payload: object, markdown: str) -> ValidatedCampaignPlan:
    value = dict(payload) if isinstance(payload, dict) else {}
    return ValidatedCampaignPlan(
        payload=value,  # type: ignore[arg-type]
        content_sha256=hashlib.sha256(canonical_json_bytes(value)).hexdigest(),
        artifact_id=str(value["id"]),
        title=str(value["title"]),
        readable_markdown=markdown,
        referenced_setting_paths=(),
    )


def _save_plan(root: Path):
    payload: SafePayload = {
        "id": "synthetic-plan",
        "title": "Synthetic Plan",
        "premise": "Synthetic premise.",
        "bindings": {
            "ruleset": {"id": "example.rules", "version": "1", "content_sha256": "1" * 64},
            "campaign_setting": {
                "id": "example.setting",
                "version": "1",
                "content_sha256": "2" * 64,
            },
        },
        "starting_party": {},
    }
    return save_reusable_campaign_plan(
        root,
        operation_id="save-plan",
        source_payload=payload,
        source_markdown="# Synthetic Plan",
        validate_plan=_plan_validator,
    )


def test_dispatch_deletes_exact_entry_and_preserves_campaign_chat_and_sibling(tmp_path: Path):
    selected = _save_participant(tmp_path, "selected", "Selected")
    sibling = _save_participant(tmp_path, "sibling", "Sibling")
    with AuthorityOwner.open(tmp_path / "authority.sqlite3", "owner-delete-library") as owner:
        campaign = owner.create_guided_campaign_draft(
            operation_id="kept-campaign",
            display_name="Kept campaign",
            builder_session_id="kept-interview",
            builder_provider_id="test.provider",
            builder_model_id="test.model",
        )
        owner.create_builder_chat(
            provider_id="test.provider", model_id="test.model", session_id="kept-chat"
        )
        with pytest.raises(CommandRejected, match="changed; refresh"):
            _dispatch(
                None,
                "delete_reusable_library_entry",  # type: ignore[arg-type]
                {
                    "kind": selected.role,
                    "artifact_id": selected.artifact_id,
                    "expected_content_sha256": "f" * 64,
                    "confirmation": f"permanently delete {selected.artifact_id}",
                },
                application_owner=owner,
                owner_id="owner-delete-library",
                provider_resolver=None,
                model_choices=lambda: (),  # type: ignore[arg-type]
                session_loop=None,
                storage_root=tmp_path,  # type: ignore[arg-type]
            )
        result = _dispatch(
            None,
            "delete_reusable_library_entry",  # type: ignore[arg-type]
            {
                "kind": selected.role,
                "artifact_id": selected.artifact_id,
                "expected_content_sha256": selected.content_sha256,
                "confirmation": f"permanently delete {selected.artifact_id}",
            },
            application_owner=owner,
            owner_id="owner-delete-library",
            provider_resolver=None,
            model_choices=lambda: (),  # type: ignore[arg-type]
            session_loop=None,
            storage_root=tmp_path,  # type: ignore[arg-type]
        )
        assert result == {"artifact_id": selected.artifact_id, "deleted": True}
        assert owner.session_context("kept-chat").session.session_id == "kept-chat"
        assert owner.session_context("kept-interview").session.campaign_id == campaign.campaign_id
    assert not (tmp_path / "participants" / selected.artifact_id).exists()
    assert (tmp_path / "participants" / sibling.artifact_id).is_dir()


def test_cleanup_failure_keeps_record_for_an_exact_retry(tmp_path: Path, monkeypatch):
    saved = _save_participant(tmp_path, "retry", "Retry")
    target = tmp_path / "participants" / saved.artifact_id
    original_unlink = Path.unlink
    failed = False

    def fail_once(path: Path, *args, **kwargs):
        nonlocal failed
        if path.name == "profile.md" and not failed:
            failed = True
            raise PermissionError("synthetic lock")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_once)
    with pytest.raises(OSError, match="restored for retry"):
        delete_reusable_participant(
            tmp_path,
            saved.artifact_id,
            expected_content_sha256=saved.content_sha256,
            confirmation=f"permanently delete {saved.artifact_id}",
        )
    assert (target / "record.json").is_file()
    package, declaration, setting = _participant_context()
    assert compatible_reusable_participants(
        tmp_path, package=package, declaration=declaration, campaign_setting=setting
    ) == (saved,)
    assert (
        delete_reusable_participant(
            tmp_path,
            saved.artifact_id,
            expected_content_sha256=saved.content_sha256,
            confirmation=f"permanently delete {saved.artifact_id}",
        )
        is True
    )


def test_rejects_inexact_confirmation_and_linked_library_root(tmp_path: Path, monkeypatch):
    saved = _save_participant(tmp_path, "confirmation", "Confirmation")
    with pytest.raises(ValueError, match="confirmation is not exact"):
        delete_reusable_participant(
            tmp_path,
            saved.artifact_id,
            expected_content_sha256=saved.content_sha256,
            confirmation=saved.artifact_id,
        )
    linked_root = tmp_path / "linked-storage"
    linked_target = linked_root / "participants" / saved.artifact_id
    linked_target.mkdir(parents=True)
    (linked_target / "record.json").write_text("{}", encoding="utf-8")
    original_check = reusable_library_deletion.is_link_or_reparse
    monkeypatch.setattr(
        reusable_library_deletion,
        "is_link_or_reparse",
        lambda path: path == linked_root / "participants" or original_check(path),
    )
    with pytest.raises(ValueError, match="linked directories"):
        delete_reusable_participant(
            linked_root,
            saved.artifact_id,
            expected_content_sha256=saved.content_sha256,
            confirmation=f"permanently delete {saved.artifact_id}",
        )
    assert linked_target.is_dir()


def test_plan_cleanup_failure_restores_catalog_entry_for_retry(tmp_path: Path, monkeypatch):
    saved = _save_plan(tmp_path)
    original_unlink = Path.unlink
    failed = False

    def fail_once(path: Path, *args, **kwargs):
        nonlocal failed
        if path.name == "plan.md" and not failed:
            failed = True
            raise PermissionError("synthetic lock")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_once)
    with pytest.raises(OSError, match="restored for retry"):
        delete_reusable_campaign_plan(
            tmp_path,
            saved.artifact_id,
            expected_content_sha256=saved.content_sha256,
            confirmation=f"permanently delete {saved.artifact_id}",
        )
    assert reusable_campaign_plan_catalog(tmp_path, validate_plan=_plan_validator) == (saved,)
    assert (
        delete_reusable_campaign_plan(
            tmp_path,
            saved.artifact_id,
            expected_content_sha256=saved.content_sha256,
            confirmation=f"permanently delete {saved.artifact_id}",
        )
        is True
    )
