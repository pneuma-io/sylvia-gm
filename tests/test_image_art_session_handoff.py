"""Saved campaign image work remains actionable when its original chat is stopped."""

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.domain.authority import PackageLock, WriterRole
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_image_art import Images


@pytest.mark.parametrize("stop_origin", [False, True], ids=["active-origin", "stopped-origin"])
@pytest.mark.parametrize("complete", [False, True], ids=["single-image", "complete-character"])
def test_next_campaign_session_can_generate_and_adopt_an_existing_request(
    tmp_path, stop_origin, complete
):
    with AuthorityOwner.open(tmp_path / "handoff.db", "art-session-handoff") as owner:
        owner.create_campaign(CampaignCreate(
            campaign_id="handoff", display_name="Image handoff", writer=WriterRole.DM,
            package_lock=PackageLock(entries=()), turn_id="initial-turn",
        ))
        owner.create_agent_session(
            campaign_id="handoff", role=WriterRole.DM, provider_id="test", model_id="test",
            session_id="original-chat",
        )
        owner.append_agent_message(
            session_id="original-chat", message_id="introduced", kind=SessionMessageKind.USER,
            content={"text": "The player meets Keeper, an archivist in a blue coat."},
        )
        owner.update_campaign_file(
            session_id="original-chat", update_id="known-keeper", basis_message_ids=("introduced",),
            updates=({"operation": "record_entity", "entity_id": "keeper", "entity_kind": "person",
                      "name": "Keeper", "attributes": {}, "visibility": "player_known",
                      "known_by": ["player"]},),
        )
        owner.complete_turn("handoff", "initial-turn")
        images = Images()
        art = ImageArt(owner, adapters=(images,), settings=lambda: {"mode": "review"})
        try:
            row = art.request(
                "original-chat", operation_id="keeper-body", target_id="keeper",
                purpose="character" if complete else "full_body", name="Keeper",
                appearance="Silver hair and a blue coat", brief="RPG portrait",
            )
            owner.create_builder_chat(
                provider_id="test", model_id="test", session_id="unrelated-chat"
            )
            with pytest.raises(ValueError, match="unavailable"):
                art.generate("unrelated-chat", row["operation_id"])
            assert images.calls == []
            if stop_origin:
                owner.stop_agent_session("original-chat")
            owner.create_agent_session(
                campaign_id="handoff", role=WriterRole.DM, provider_id="test", model_id="test",
                session_id="current-chat",
            )
            owner.create_agent_session(
                campaign_id="handoff", role=WriterRole.DM, provider_id="test", model_id="test",
                session_id="other-current-chat",
            )
            assert "keeper" in {target["id"] for target in art.targets("current-chat")}
            selected = {}
            steps = ("full_body", "reference_sheet", "portrait") if complete else ("full_body",)
            for index, purpose in enumerate(steps):
                art.generate("current-chat", row["operation_id"])
                result = art._futures[row["operation_id"]].result(timeout=15)
                assert result["status"] == "succeeded", result
                assert len(images.calls) == index + 1
                adopted = art.adopt("current-chat", row["operation_id"], expected_sha256=None)
                assert adopted["target_id"] == "keeper" and adopted["purpose"] == purpose
                selected[purpose] = {
                    key: value for key, value in adopted.items() if key != "next_request"
                }
                assert owner._store.images.get(row["operation_id"])["session_id"] == "original-chat"
                assert art.generate("current-chat", row["operation_id"])["status"] == "succeeded"
                replay = art.adopt(
                    "other-current-chat", row["operation_id"], expected_sha256=None
                )
                assert replay == adopted
                assert len(images.calls) == index + 1
                if index + 1 < len(steps):
                    row = adopted["next_request"]
            observed = art.view("current-chat")["selections"]
            assert {item["purpose"]: item for item in observed} == selected
            assert [len(request.reference_images) for request in images.calls] == list(
                range(len(steps))
            )
            archive = tmp_path / "handoff.zip"
            owner.export_campaign(
                operation_id="handoff-export", campaign_id="handoff", target=archive
            )
        finally:
            art.close()
    with AuthorityOwner.open(tmp_path / "recipient.db", "handoff-recipient") as recipient:
        recipient.recover_campaign(recovery_id="handoff-recovery", source=archive)
        art = ImageArt(recipient)
        try:
            observed = art.view("current-chat")["selections"]
            assert {item["purpose"]: item for item in observed} == selected
            assert recipient._store.images.get(row["operation_id"])["session_id"] == "original-chat"
        finally:
            art.close()
