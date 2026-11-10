"""Artwork survives early character saves, actual library materialization and offline card reuse."""

from threading import Event

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.campaign_library_selection import (
    materialize_library_party,
    resolve_campaign_library_selection,
)
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.domain.image_art import validate_character_visuals
from tests.test_campaign_library_selection import seed_campaign_library
from tests.test_image_art import Images
from tests.test_image_hooks import png


def test_early_saved_library_character_receives_later_adopted_stages_in_campaign(tmp_path):
    database = tmp_path / "handoff.db"
    with AuthorityOwner.open(database, "owner") as owner:
        candidate, setting_id, plan, characters = seed_campaign_library(owner, tmp_path)
        source = characters[1]
        original = (tmp_path / "participants" / source.artifact_id / "payload.json").read_bytes()
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="art-builder")
        provider = Images()
        provider.release = Event()
        art = ImageArt(owner, adapters=(provider,), settings=lambda: {"mode": "review"})
        row = art.request(
            "art-builder",
            operation_id="mora",
            purpose="character",
            name=source.name,
            appearance="Brown eyes, silver hair, blue coat",
            brief="Painterly science fiction",
        )
        art.generate("art-builder", row["operation_id"])
        assert provider.started.wait(10)
        art.bind_publication(
            "art-builder", source.artifact_id, source.content_sha256, name=source.name
        )
        provider.release.set()
        assert (
            owner._store.images.published_visuals(source.artifact_id, source.content_sha256) is None
        )
        selection = resolve_campaign_library_selection(
            owner,
            tmp_path,
            candidate_id=candidate.candidate_id,
            setting_id=setting_id,
            plan_id=plan.artifact_id,
        )
        participants, state, selected_party = materialize_library_party(
            owner,
            selection,
            operation_id="party",
            player_character_id=characters[0].artifact_id,
            companion_ids=(source.artifact_id,),
        )
        assert "visuals" not in selected_party[1]["character_file"]
        confirmed = owner.confirm_new_campaign(
            operation_id="confirm",
            display_name="Artwork handoff",
            package_lock=candidate.package_lock,
            turn_id="construction",
            builder_session_id="campaign-builder",
            builder_provider_id="test",
            builder_model_id="test",
            participants=participants,
            initial_state=state,
        )
        owner.begin_campaign_construction(
            operation_id="begin",
            campaign_id=confirmed.campaign_id,
            lane="customize",
            expected_revision=0,
            canonical_input={"selected_party": selected_party},
        )
        actor_id = participants[1].participant_id
        for index in range(3):
            assert art._futures[row["operation_id"]].result(timeout=15)["status"] == "succeeded"
            if index == 0:
                assert (
                    owner._store.images.published_visuals(source.artifact_id, source.content_sha256)
                    is None
                )
            adopted = art.adopt("art-builder", row["operation_id"], expected_sha256=None)
            campaign_art = owner.character_visuals(confirmed.campaign_id)[actor_id]
            assert (
                len(
                    [
                        purpose
                        for purpose in ("full_body", "reference_sheet", "portrait")
                        if purpose in campaign_art
                    ]
                )
                == index + 1
            )
            view = art.view("campaign-builder", include_targets=False)
            assert (
                len([item for item in view["selections"] if item["target_id"] == actor_id])
                == index + 1
            )
            if index < 2:
                assert campaign_art["status"] == "partial"
                row = adopted["next_request"]
                art.generate("art-builder", row["operation_id"])
        assert "status" not in owner.character_visuals(confirmed.campaign_id)[actor_id]
        assert len(provider.calls) == 3
        assert owner._store.images.published_visuals(source.artifact_id, "0" * 64) is None
        portrait = next(
            item
            for item in view["selections"]
            if item["target_id"] == actor_id and item["purpose"] == "portrait"
        )
        art.frame("campaign-builder", actor_id, portrait["sha256"], {"x": 0.2, "y": 0.3, "zoom": 2})
        assert (
            owner._store.images.published_visuals(source.artifact_id, source.content_sha256)[
                "frame"
            ]["zoom"]
            == 1
        )
        assert (
            tmp_path / "participants" / source.artifact_id / "payload.json"
        ).read_bytes() == original
        expected = art.character_assets("campaign-builder", actor_id)
        archive = tmp_path / "campaign.zip"
        owner.export_campaign(
            operation_id="export", campaign_id=confirmed.campaign_id, target=archive
        )
        art.close()
    with AuthorityOwner.open(tmp_path / "recipient.db", "recipient") as owner:
        owner.recover_campaign(recovery_id="recover", source=archive)
        art = ImageArt(owner)
        assert art.character_assets("campaign-builder", actor_id) == expected
        assert (
            next(
                row
                for row in art.view("campaign-builder")["selections"]
                if row["target_id"] == actor_id and row["purpose"] == "portrait"
            )["frame"]["zoom"]
            == 2
        )
        art.close()


def test_card_portrait_and_crop_survive_offline_reuse_as_explicit_partial_art(tmp_path):
    with AuthorityOwner.open(tmp_path / "card.db", "owner") as owner:
        for session in ("card", "copy"):
            owner.create_builder_chat(provider_id="test", model_id="test", session_id=session)
        images = Images()
        art = ImageArt(owner, adapters=(images,), settings=lambda: {"mode": "off"})
        art.restore_card_portrait(
            "card", "vela", name="Vela", appearance="Blue coat", image=png(32, 48).data
        )
        portrait = art.view("card")["selections"][0]
        art.frame("card", "vela", portrait["sha256"], {"x": 0.4, "y": 0.3, "zoom": 1.8})
        bundle = art.visuals_for_publication("card", "Vela")
        assert bundle["status"] == "partial"
        assert validate_character_visuals(bundle) == bundle
        art.restore_visuals("copy", "saved-vela", bundle, name="Vela")
        restored = art.view("copy")["character_visuals"]["saved-vela"]
        assert restored["portrait"]["sha256"] == portrait["sha256"]
        assert restored["frame"] == bundle["frame"]
        assert restored["missing"] == ["full_body", "reference_sheet", "portrait"]
        assert images.calls == []
        art.close()


@pytest.mark.parametrize("missing", [[], ["portrait", "portrait"], [{}], ["map_underlay"]])
def test_partial_art_requires_a_valid_unfinished_stage_list(missing):
    with pytest.raises(ValueError):
        validate_character_visuals(
            {
                "appearance": "Blue coat",
                "status": "partial",
                "missing": missing,
                "portrait": {"sha256": "a" * 64, "operation_id": "portrait"},
            }
        )
