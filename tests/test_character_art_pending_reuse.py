"""A saved character copy shares unfinished artwork without retrying it in another chat."""

import hashlib
import json

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.character_profiles import load_character_profile, save_character_profile
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.image_hooks import ImageUpdate
from sylvia_gm.config.image_settings import ImageSettings
from sylvia_gm.domain._content_schema_character_profile_v2 import EXAMPLE as RICH_PROFILE
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.provider._base import ProviderResponse
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_character_profiles import call, receipts, submit
from tests.test_builder_library import LibraryProvider
from tests.test_image_art import Images


@pytest.mark.parametrize("state", ["proposed", "failed", "partial_failed"])
def test_copy_reuses_unfinished_source_and_receives_later_adopted_stages(tmp_path, state):
    database = tmp_path / "reuse.db"
    profile = save_character_profile(
        tmp_path,
        operation_id="source",
        source=canonical_json_bytes({**RICH_PROFILE, "name": "Vela"}),
    )
    request = {
        "purpose": "character",
        "name": "Vela",
        "appearance": "Blue coat",
        "brief": "Painterly",
    }

    class Failure(Images):
        def submit(self, request):
            if state == "partial_failed" and not self.calls:
                return super().submit(request)
            self.calls.append(request)
            return ImageUpdate(
                request.session_id, request.operation_id, "failed", error="rate_limited"
            )

    with AuthorityOwner.open(database, "seed") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="original")
        art = ImageArt(owner, adapters=(Failure(),), settings=lambda: {"mode": "review"})
        try:
            row = art.request("original", operation_id="source-art", **request)
            art.bind_publication(
                "original", profile.artifact_id, profile.content_sha256, name="Vela"
            )
            if state != "proposed":
                art.generate("original", row["operation_id"])
                result = art._futures[row["operation_id"]].result(timeout=15)
                if state == "partial_failed":
                    assert result["status"] == "succeeded"
                    row = art.adopt("original", row["operation_id"], expected_sha256=None)[
                        "next_request"
                    ]
                    art.generate("original", row["operation_id"])
                    result = art._futures[row["operation_id"]].result(timeout=15)
                assert result["status"] == "failed"
        finally:
            art.close()

    ImageSettings(tmp_path).save("review")
    data = dict(thaw_json_value(profile.profile))
    original = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    data["name"] = "Aster"
    updated = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    provider = LibraryProvider(
        responses=[
            call(
                "open_character_profile",
                {
                    "artifact_id": profile.artifact_id,
                    "expected_content_sha256": profile.content_sha256,
                    "destination_path": "copy.json",
                },
            ),
            call(
                "write_file",
                {
                    "path": "copy.json",
                    "content": updated,
                    "expected_version": hashlib.sha256(original.encode()).hexdigest(),
                },
            ),
            call(
                "publish_character_profile",
                {
                    "path": "copy.json",
                    "expected_version": hashlib.sha256(updated.encode()).hexdigest(),
                },
            ),
            ProviderResponse(content="Aster keeps the existing artwork workflow."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        chat = harness.request(
            "new",
            "create_builder_chat",
            {
                "operation_id": "new",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        session_id = chat["session"]["session_id"]
        assert submit(harness, session_id)["interruption"] is None
        saved = receipts(provider)["publish_character_profile"]
        assert saved["success"] is True, saved
        assert saved["artwork"]["source_session_id"] == "original"
        assert saved["artwork"]["linked"] is True
        assert saved["artwork"]["status"] == ("proposed" if state == "proposed" else "failed")
        view = harness.request("art", "image_art", {"session_id": session_id, "action": "view"})
        assert all(
            row["status"] == "succeeded" and "character_flow" not in row for row in view["requests"]
        )
    finally:
        harness.close()
    with AuthorityOwner.open(database, "continue") as owner:
        images = Images()
        preferences = {"mode": "auto"}
        art = ImageArt(owner, adapters=(images,), settings=lambda: dict(preferences))
        try:
            linked = art.ensure_character_art(
                session_id,
                name="Aster",
                appearance="Blue coat",
                target_id=profile.artifact_id,
                source_content_sha256=profile.content_sha256,
            )
            assert linked["linked"] is True
            assert linked["source_session_id"] == "original"
            assert images.calls == [] and not art._futures
            with pytest.raises(ValueError, match="unavailable"):
                art.generate(session_id, row["operation_id"])
            preferences["mode"] = "review"
            # A retry is deliberate, and runs only in the original authorized chat.
            if state != "proposed":
                row = art.request("original", operation_id="deliberate-retry", **request)
            for _ in range(2 if state == "partial_failed" else 3):
                art.generate("original", row["operation_id"])
                assert art._futures[row["operation_id"]].result(timeout=15)["status"] == "succeeded"
                adopted = art.adopt("original", row["operation_id"], expected_sha256=None)
                row = adopted.get("next_request")
            assert row is None
            expected = owner._store.images.published_visuals(
                profile.artifact_id, profile.content_sha256
            )
            copied = owner._store.images.published_visuals(saved["id"], saved["content_sha256"])
            assert copied is not None and "status" not in copied
            assert copied["frame"] == expected["frame"]
            for purpose in ("full_body", "reference_sheet", "portrait"):
                assert copied[purpose]["sha256"] == expected[purpose]["sha256"]
            art.restore_profile(session_id, load_character_profile(tmp_path, saved["id"]), tmp_path)
            assert len(art.character_assets(session_id, saved["id"])) == 3
        finally:
            art.close()


def test_deleted_source_chat_requires_an_explicit_new_request_without_auto_submission(tmp_path):
    profile = save_character_profile(
        tmp_path, operation_id="source", source=canonical_json_bytes(RICH_PROFILE)
    )
    with AuthorityOwner.open(tmp_path / "deleted.db", "owner") as owner:
        for session_id in ("original", "copy"):
            owner.create_builder_chat(provider_id="test", model_id="test", session_id=session_id)
        images = Images()
        preferences = {"mode": "review"}
        art = ImageArt(owner, adapters=(images,), settings=lambda: dict(preferences))
        try:
            art.request(
                "original",
                operation_id="original-art",
                purpose="character",
                name="Vela",
                appearance="Blue coat",
                brief="Painterly",
            )
            art.bind_publication(
                "original", profile.artifact_id, profile.content_sha256, name="Vela"
            )
            owner.delete_builder_chat("original", confirmation="permanently delete original")
            preferences["mode"] = "auto"
            result = art.ensure_character_art(
                "copy",
                name="Vela",
                appearance="Blue coat",
                target_id=profile.artifact_id,
                source_content_sha256=profile.content_sha256,
            )
            assert result["status"] == "unavailable" and result["source_unavailable"] is True
            assert "new artwork request" in result["message"]
            assert art.view("copy")["requests"] == []
            assert images.calls == [] and not art._futures
        finally:
            art.close()
