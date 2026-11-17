"""Selected image bytes and character associations survive ordinary campaign export/recovery."""

import json

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.domain.authority import PackageLock, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_image_art import Images
from tests.test_image_hooks import png


def test_art_export_recovers_exact_bytes_and_reference_associations(tmp_path):
    archive = tmp_path / "with-art.zip"
    with AuthorityOwner.open(tmp_path / "source.db", "source") as owner:
        owner.create_campaign(
            CampaignCreate(
                campaign_id="art-campaign",
                display_name="Art campaign",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                turn_id="turn-art-campaign",
            )
        )
        owner.create_agent_session(
            campaign_id="art-campaign",
            role=WriterRole.DM,
            provider_id="test",
            model_id="test",
            session_id="art-session",
        )
        owner.append_agent_message(
            session_id="art-session",
            message_id="art-basis",
            kind=SessionMessageKind.USER,
            content={"text": "The archivist Keeper is known to the player."},
        )
        owner.update_campaign_file(
            session_id="art-session",
            update_id="known-keeper",
            basis_message_ids=("art-basis",),
            updates=(
                {
                    "operation": "record_entity",
                    "entity_id": "keeper",
                    "entity_kind": "person",
                    "name": "Keeper",
                    "attributes": {},
                    "visibility": "player_known",
                    "known_by": ["player"],
                },
            ),
        )
        owner.complete_turn("art-campaign", "turn-art-campaign")
        service = ImageArt(owner, adapters=(Images(),), settings=lambda: {"mode": "auto"})
        assert {target["id"] for target in service.targets("art-session")} == {"keeper"}
        row = service.request(
            "art-session",
            operation_id="full-body",
            target_id="keeper",
            purpose="character",
            brief="Archivist",
            appearance="Silver hair",
        )
        for _ in range(3):
            result = service._futures[row["operation_id"]].result(timeout=15)
            assert result["status"] == "succeeded"
            row = service.view("art-session")["requests"][0]
        saved = service.view("art-session")["selections"]
        assert {item["purpose"] for item in saved} == {"full_body", "reference_sheet", "portrait"}
        raw = owner._store.images.image(saved[0]["sha256"])
        owner.export_campaign(operation_id="export-art", campaign_id="art-campaign", target=archive)
        service.close()
        # Relabelling a provider request cannot turn it into a sessionless local-copy receipt.
        with owner._store.images._transaction() as connection:
            connection.execute(
                "UPDATE image_requests SET session_id=?, request_json=? WHERE campaign_id=?",
                ("art-campaign", json.dumps({"source": "campaign_asset", "metadata": {}}),
                 "art-campaign"),
            )
        with pytest.raises(CommandRejected, match="portable_export_invalid_authority_closure"):
            owner._store.export_campaign("art-campaign", tmp_path / "invalid-sessionless.zip")
    with AuthorityOwner.open(tmp_path / "recipient.db", "recipient") as recipient:
        recipient.recover_campaign(recovery_id="recover-art", source=archive)
        service = ImageArt(recipient, adapters=(Images(),), settings=lambda: {"mode": "review"})
        assert service.view("art-session")["selections"] == saved
        assert recipient._store.images.image(saved[0]["sha256"]) == raw
        service.close()


def test_local_character_art_transfers_without_creating_an_agent_session(tmp_path):
    archive = tmp_path / "local-art.zip"
    image = png(64, 64, 91)
    path = f"assets/characters/{image.sha256}.png"
    with AuthorityOwner.open(tmp_path / "local.db", "local-copy") as owner:
        owner.create_campaign(CampaignCreate(
            campaign_id="local-art", display_name="Local art", writer=WriterRole.DM,
            package_lock=PackageLock(entries=()), turn_id="local-turn",
        ))
        owner._store.images.attach_character_visuals(
            scope_id="local-art", campaign_id="local-art", session_id="local-art",
            target_id="keeper", visuals={
                "appearance": "Silver hair and a blue coat", "status": "partial",
                "missing": ["full_body", "reference_sheet"],
                "portrait": {"path": path, "sha256": image.sha256},
                "frame": {"x": 0.4, "y": 0.3, "zoom": 1.2},
            }, assets={path: image.data},
        )
        owner.complete_turn("local-art", "local-turn")
        selected = owner._store.images.list_selections("local-art")
        owner._store.images.bind_publication(
            [row["operation_id"] for row in selected], "saved-keeper", "a" * 64
        )
        owner.export_campaign(operation_id="local-export", campaign_id="local-art", target=archive)
        assert owner._store.campaign_session_contexts("local-art") == ()
        selected = owner._store.images.list_selections("local-art")
    with AuthorityOwner.open(tmp_path / "local-recipient.db", "local-recipient") as owner:
        owner.recover_campaign(recovery_id="local-recovery", source=archive)
        assert owner._store.campaign_session_contexts("local-art") == ()
        assert owner._store.images.list_selections("local-art") == selected
        assert owner._store.images.image(image.sha256) == image.data
