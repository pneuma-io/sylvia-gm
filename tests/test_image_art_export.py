"""Selected image bytes and character associations survive ordinary campaign export/recovery."""

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.domain.authority import PackageLock, WriterRole
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_image_art import Images


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
            purpose="full_body",
            brief="Archivist",
            appearance="Silver hair",
        )
        result = service._futures[row["operation_id"]].result(timeout=15)
        assert result["status"] == "succeeded"
        saved = service.view("art-session")["selections"]
        raw = owner._store.images.image(saved[0]["sha256"])
        owner.export_campaign(operation_id="export-art", campaign_id="art-campaign", target=archive)
        service.close()
    with AuthorityOwner.open(tmp_path / "recipient.db", "recipient") as recipient:
        recipient.recover_campaign(recovery_id="recover-art", source=archive)
        service = ImageArt(recipient, adapters=(Images(),), settings=lambda: {"mode": "review"})
        assert service.view("art-session")["selections"] == saved
        assert recipient._store.images.image(saved[0]["sha256"]) == raw
        service.close()
