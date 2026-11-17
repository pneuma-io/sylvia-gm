"""Unfinished Builder drafts retain their exact package authority during transfer."""

import io
import json
import sqlite3
import zipfile

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import ActivationCommand
from tests.test_character_creation_contract import _draft
from tests.test_setting_draft_check import _source_candidate


def _activate(owner, candidate, campaign_id):
    owner.create_campaign(CampaignCreate(
        campaign_id=campaign_id, display_name="Existing campaign", writer=WriterRole.DM,
        package_lock=candidate.package_lock, turn_id=campaign_id + "-turn",
    ))
    owner.complete_turn(campaign_id, campaign_id + "-turn")
    owner.activate_package(ActivationCommand(
        campaign_id=campaign_id, candidate_id=candidate.candidate_id,
        activation_id="activate-" + campaign_id,
        expected_revision=owner.snapshot(campaign_id).revision,
        expected_lock_id=owner.current_package_lock(campaign_id).lock_id,
        migration_id="existing-" + campaign_id,
    ), migrate=lambda state: state)


@pytest.mark.parametrize(
    ("begun", "shared_library", "activate_shared", "activate_source"),
    [(True, False, False, False), (False, True, False, False),
     (True, True, True, False), (True, True, False, True)],
)
def test_unfinished_draft_with_candidate_identity_recovers_and_reopens(
    tmp_path, begun, shared_library, activate_shared, activate_source
):
    archive = tmp_path / "draft.zip"
    with AuthorityOwner.open(tmp_path / "source.db", "source") as owner:
        candidate = _source_candidate(owner, tmp_path / "workspace")
        if activate_source:
            _activate(owner, candidate, "source-existing")
            candidate = next(
                item for item in owner.package_candidates()
                if item.candidate_id == candidate.candidate_id
            )
        confirmed = owner.confirm_new_campaign(
            operation_id="confirm", display_name="Draft artwork transfer",
            package_lock=candidate.package_lock, package_candidate_id=candidate.candidate_id,
            turn_id="construction", builder_session_id="builder", builder_provider_id="test",
            builder_model_id="test",
        )
        if begun:
            owner.begin_campaign_construction(
                operation_id="begin", campaign_id=confirmed.campaign_id, lane="customize",
                expected_revision=0, canonical_input={},
            )
        with materialize_ruleset_draft(tmp_path / "workspace") as materialized:
            owner.install_package_archive(
                io.BytesIO(materialized.archive.read()), install_id="unused-package"
            )
        owner.export_campaign(
            operation_id="export", campaign_id=confirmed.campaign_id, target=archive,
        )
    with zipfile.ZipFile(archive) as stream:
        records = [json.loads(line) for line in stream.read("authority.jsonl").splitlines()]
    assert {
        record["row"]["candidate_id"] for record in records
        if record["table"] == "package_candidates"
    } == {candidate.candidate_id}
    recipient = tmp_path / "recipient.db"
    with AuthorityOwner.open(recipient, "recipient") as owner:
        if shared_library:
            shared = _source_candidate(owner, tmp_path / "recipient-workspace")
            assert shared.candidate_id == candidate.candidate_id
            assert shared.package_lock == candidate.package_lock
        if activate_shared:
            _activate(owner, candidate, "existing")
        before = owner.package_candidates()
        owner.recover_campaign(recovery_id="recover", source=archive)
        if shared_library:
            assert owner.package_candidates() == before
        assert len(owner.package_candidates()) == 1
        assert owner.package_candidates()[0].closure_sha256 == candidate.closure_sha256
        if not begun:
            owner.begin_campaign_construction(
                operation_id="resume", campaign_id=confirmed.campaign_id, lane="customize",
                expected_revision=0, canonical_input={},
            )
        basis = owner._store.campaign_construction_basis(confirmed.campaign_id)
        assert basis.canonical_input == {}
        assert basis.package_candidate_id == candidate.candidate_id
    with AuthorityOwner.open(recipient, "reopened") as owner:
        assert owner._store.campaign_construction_basis(confirmed.campaign_id) == basis


def test_candidate_reuse_still_rejects_different_content_without_partial_import(tmp_path):
    archive = tmp_path / "draft.zip"
    with AuthorityOwner.open(tmp_path / "source.db", "source") as owner:
        candidate = _source_candidate(owner, tmp_path / "source-workspace")
        confirmed = owner.confirm_new_campaign(
            operation_id="confirm", display_name="Draft", package_lock=candidate.package_lock,
            package_candidate_id=candidate.candidate_id, turn_id="construction",
        )
        owner.export_campaign(
            operation_id="export", campaign_id=confirmed.campaign_id, target=archive
        )
    recipient = tmp_path / "recipient.db"
    with AuthorityOwner.open(recipient, "recipient") as owner:
        _draft(tmp_path / "recipient-workspace", expression="participant.rank * 3")
        with materialize_ruleset_draft(tmp_path / "recipient-workspace") as materialized:
            different = owner.install_package_archive(
                io.BytesIO(materialized.archive.read()), install_id=candidate.install_id,
            )
        assert different.candidate_id == candidate.candidate_id
        assert different.closure_sha256 != candidate.closure_sha256
        before = owner.package_candidates()
        with pytest.raises(CommandRejected) as rejected:
            owner.recover_campaign(recovery_id="recover", source=archive)
        assert rejected.value.code == "portable_recovery_collision"
        assert owner.package_candidates() == before
    with sqlite3.connect(recipient) as connection:
        assert connection.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0] == 0


def test_activated_campaign_recovers_into_same_validated_library_without_changing_it(tmp_path):
    archive = tmp_path / "running.zip"
    with AuthorityOwner.open(tmp_path / "source.db", "source") as owner:
        candidate = _source_candidate(owner, tmp_path / "source-workspace")
        _activate(owner, candidate, "running")
        expected = owner.snapshot("running")
        owner.export_campaign(operation_id="export", campaign_id="running", target=archive)
    recipient = tmp_path / "recipient.db"
    with AuthorityOwner.open(recipient, "recipient") as owner:
        shared = _source_candidate(owner, tmp_path / "recipient-workspace")
        owner.recover_campaign(recovery_id="recover", source=archive)
        assert owner.snapshot("running") == expected
        assert owner.package_candidates() == (shared,)
    with AuthorityOwner.open(recipient, "reopened") as owner:
        assert owner.snapshot("running") == expected
