"""Renaming a saved character keeps its identity references and does not generate fresh art."""

import hashlib
import json

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.character_profiles import load_character_profile, save_character_profile
from sylvia_gm.application.character_transfer import load_character_source
from sylvia_gm.application.compatible_character_copy import authored_character_state
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.ready_campaigns import (
    customize_ready_campaign,
    ready_campaign_catalog,
    resolve_ready_campaign,
)
from sylvia_gm.config.image_settings import ImageSettings
from sylvia_gm.domain._content_schema_character_profile_v2 import EXAMPLE as RICH_PROFILE
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.provider._base import ProviderResponse
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_character_profiles import call, receipts, submit
from tests.test_builder_library import LibraryProvider, launch_library_builder
from tests.test_campaign_library_selection import seed_campaign_library
from tests.test_image_art import Images
from tests.test_image_hooks import png


def complete_art(art, session_id, target_id, name):
    assets = {}
    visuals = {"appearance": "Silver hair, blue coat", "frame": {"x": 0.4, "y": 0.3, "zoom": 1.6}}
    for index, purpose in enumerate(("full_body", "reference_sheet", "portrait")):
        image = png(32 + index, 48, 72)
        path = f"assets/characters/{image.sha256}.png"
        assets[path] = image.data
        visuals[purpose] = {"path": path, "sha256": image.sha256}
    art.restore_visuals(session_id, target_id, visuals, name=name, assets=assets)
    return visuals


@pytest.mark.parametrize("ruleset", ["signal", "lite"])
def test_owner_builder_rename_keeps_complete_art_without_a_fresh_proposal(tmp_path, ruleset):
    database = tmp_path / "rename.db"
    origin = None
    with AuthorityOwner.open(database, "seed") as owner:
        if ruleset == "signal":
            candidate, setting_id, _, characters = seed_campaign_library(owner, tmp_path)
            source = characters[1]
            origin = save_character_profile(
                tmp_path,
                operation_id="original-persona",
                source=canonical_json_bytes({**RICH_PROFILE, "name": source.name}),
            )
        else:
            row = next(
                item for item in ready_campaign_catalog() if item["id"] == "last-cart-through"
            )
            selection, _ = resolve_ready_campaign(owner, tmp_path, row["id"], row["content_sha256"])
            copied = customize_ready_campaign(
                owner,
                tmp_path,
                operation_id="editable-party",
                template_id=row["id"],
                expected_content_sha256=row["content_sha256"],
            )
            member = next(item for item in copied["characters"] if item["kind"] == "companion")
            source = load_character_source(
                owner,
                tmp_path,
                artifact_id=member["id"],
                expected_content_sha256=member["content_sha256"],
                kind="companion",
            )
            candidate, setting_id = selection.candidate, selection.sources.setting.package_id
        declaration = next(
            item
            for item in owner.exact_participant_declarations(
                source.package_id,
                source.package_version,
                source.package_sha256,
            )
            if item.schema_id == source.schema_id
        )
        authored_state = authored_character_state(source.entity_state, declaration)
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="source-art")
        art = ImageArt(owner)
        try:
            complete_art(art, "source-art", source.artifact_id, source.name)
            art.bind_publication(
                "source-art",
                source.artifact_id,
                source.content_sha256,
                name=source.name,
                source_target_id=source.artifact_id,
            )
            original_visuals = owner._store.images.published_visuals(
                source.artifact_id, source.content_sha256
            )
        finally:
            art.close()
    original = (tmp_path / "participants" / source.artifact_id / "payload.json").read_bytes()
    ImageSettings(tmp_path).save("review")
    participant = dict(thaw_json_value(source.participant))
    participant["name"] = "Aster"
    provider = LibraryProvider(
        responses=[
            call(
                "open_character_draft",
                {
                    "artifact_id": source.artifact_id,
                    "expected_content_sha256": source.content_sha256,
                    "destination_path": "renamed.json",
                },
            ),
            call(
                "publish_participant_artifact",
                {
                    "summary": "A renamed signal operator.",
                    "authored_profile": source.authored_profile.replace(source.name, "Aster"),
                    "participant": participant,
                    "entity_state": canonical_json_bytes(authored_state).decode(),
                    "setting_data": canonical_json_bytes(source.setting_data).decode(),
                    "schema_id": source.schema_id,
                    **(
                        {
                            "source_profile": {
                                "artifact_id": origin.artifact_id,
                                "content_sha256": origin.content_sha256,
                            }
                        }
                        if origin is not None
                        else {}
                    ),
                },
            ),
            ProviderResponse(content="Aster is saved with the same artwork."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        _, session_id = launch_library_builder(
            harness, candidate.candidate_id, setting_id, "companion"
        )
        result = submit(harness, session_id)
        assert result["interruption"] is None, result
        saved = receipts(provider)["publish_participant_artifact"]
        assert saved["success"] is True, saved
        assert saved["artwork"]["status"] == "already_available"
        view = harness.request("images", "image_art", {"session_id": session_id, "action": "view"})
        assert len(view["requests"]) == 3
        assert all(
            row["status"] == "succeeded" and "character_flow" not in row for row in view["requests"]
        )
        assert len(view["selections"]) == 3
        assert view["character_visuals"][source.artifact_id]["frame"] == original_visuals["frame"]
    finally:
        harness.close()
    assert (
        tmp_path / "participants" / source.artifact_id / "payload.json"
    ).read_bytes() == original
    with AuthorityOwner.open(database, "reopen") as owner:
        copied = owner._store.images.published_visuals(saved["id"], saved["content_sha256"])
        assert copied["frame"] == original_visuals["frame"]
        for purpose in ("full_body", "reference_sheet", "portrait"):
            assert copied[purpose]["sha256"] == original_visuals[purpose]["sha256"]


def test_explicit_source_never_links_an_unrelated_same_name_workflow(tmp_path):
    with AuthorityOwner.open(tmp_path / "identity.db", "owner") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        images = Images()
        preferences = {"mode": "review"}
        art = ImageArt(owner, adapters=(images,), settings=lambda: dict(preferences))
        try:
            own = complete_art(art, "builder", "source-vela", "Vela")
            other = art.request(
                "builder",
                operation_id="other",
                purpose="character",
                name="Aster",
                appearance="Red hair, yellow jacket",
                brief="Portraits for a different character",
            )
            preferences["mode"] = "auto"
            assert (
                art.ensure_character_art(
                    "builder",
                    name="Aster",
                    appearance="Silver hair, blue coat",
                    target_id="source-vela",
                )["status"]
                == "already_available"
            )
            art.bind_publication(
                "builder",
                "saved-aster",
                "a" * 64,
                name="Aster",
                source_target_id="source-vela",
            )
            linked = owner._store.images.published_visuals("saved-aster", "a" * 64)
            assert linked["portrait"]["sha256"] == own["portrait"]["sha256"]
            request = owner._store.images.get(other["operation_id"])
            assert not json.loads(request["request_json"]).get("publications")
            assert images.calls == [] and not art._futures
            assert (
                art.visuals_for_publication("builder", "Aster", source_target_id="missing") is None
            )
        finally:
            art.close()


def test_unbound_profile_rename_reuses_art_after_builder_restart(tmp_path):
    database = tmp_path / "profile.db"
    profile = save_character_profile(
        tmp_path,
        operation_id="profile",
        source=canonical_json_bytes({**RICH_PROFILE, "name": "Vela"}),
    )
    with AuthorityOwner.open(database, "seed") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="source-art")
        art = ImageArt(owner)
        try:
            complete_art(art, "source-art", profile.artifact_id, "Vela")
            art.bind_publication(
                "source-art",
                profile.artifact_id,
                profile.content_sha256,
                name="Vela",
                source_target_id=profile.artifact_id,
            )
        finally:
            art.close()
    ImageSettings(tmp_path).save("review")
    provider = LibraryProvider(
        responses=[
            call(
                "open_character_profile",
                {
                    "artifact_id": profile.artifact_id,
                    "expected_content_sha256": profile.content_sha256,
                    "destination_path": "profile.json",
                },
            ),
            ProviderResponse(content="The complete source is open."),
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
        assert receipts(provider)["open_character_profile"]["success"] is True
    finally:
        harness.close()
    data = dict(thaw_json_value(profile.profile))
    original = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    data["name"] = "Aster"
    updated = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    provider = LibraryProvider(
        responses=[
            call(
                "write_file",
                {
                    "path": "profile.json",
                    "content": updated,
                    "expected_version": hashlib.sha256(original.encode()).hexdigest(),
                },
            ),
            call(
                "publish_character_profile",
                {
                    "path": "profile.json",
                    "expected_version": hashlib.sha256(updated.encode()).hexdigest(),
                },
            ),
            ProviderResponse(content="Aster is saved with Vela's established artwork."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        assert submit(harness, session_id, "rename")["interruption"] is None
        saved = receipts(provider)["publish_character_profile"]
        assert saved["success"] is True, saved
        assert saved["artwork"]["status"] == "already_available"
        view = harness.request("art", "image_art", {"session_id": session_id, "action": "view"})
        assert len(view["requests"]) == 3
        assert all(
            row["status"] == "succeeded" and "character_flow" not in row for row in view["requests"]
        )
        assert len(view["selections"]) == 3
        copy = load_character_profile(tmp_path, saved["id"])
        assert copy.profile["visuals"]["frame"] == {"x": 0.4, "y": 0.3, "zoom": 1.6}
        assert copy.profile["name"] == "Aster"
        assert load_character_profile(tmp_path, profile.artifact_id).profile["name"] == "Vela"
    finally:
        harness.close()
