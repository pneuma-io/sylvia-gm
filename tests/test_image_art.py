"""Persistent art behavior through the real owner, with disposable scripted image providers."""

from threading import Event

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.image_hooks import ImageCapabilities, ImageUpdate
from sylvia_gm.application.map_art_reference import map_art_reference
from sylvia_gm.application.tactical_grid import project_tactical_grid
from sylvia_gm.domain.image_art import PortraitFrame
from tests.test_image_hooks import png
from tests.test_tactical_grid_ui import _scene


class Images:
    capabilities = ImageCapabilities(
        "test.images",
        frozenset({"create", "edit"}),
        frozenset({(1024, 1024), (1024, 1536), (1536, 1024)}),
        synthetic=True,
    )

    def __init__(self):
        self.calls = []
        self.started = Event()
        self.release = None

    def submit(self, request):
        self.calls.append(request)
        self.started.set()
        if self.release is not None:
            assert self.release.wait(10)
        return ImageUpdate(
            request.session_id,
            request.operation_id,
            "succeeded",
            image=png(request.width, request.height, 60 + len(self.calls) % 120),
        )


@pytest.fixture
def art(tmp_path):
    owner = AuthorityOwner.open(tmp_path / "art.db", "image-owner")
    owner.create_builder_chat(provider_id="fixture", model_id="fixture", session_id="builder-art")
    provider = Images()
    preferences = {"mode": "review", "connection": "test.images"}
    service = ImageArt(owner, adapters=(provider,), settings=lambda: dict(preferences))
    try:
        yield owner, service, provider, preferences
    finally:
        service.close()
        owner.close()


def request(service, key, purpose="full_body", **extra):
    return service.request(
        "builder-art",
        operation_id=key,
        target_id="relay-keeper",
        purpose=purpose,
        brief="Coastal science-fiction archivist",
        appearance="Short silver hair, brown eyes, blue coat",
        **extra,
    )


def generate(service, operation_id):
    service.generate("builder-art", operation_id)
    return service._futures[operation_id].result(timeout=15)


def test_review_generate_adopt_replay_and_reopen(art, tmp_path):
    owner, service, provider, _ = art
    proposed = request(service, "first")
    assert proposed["status"] == "proposed" and not provider.calls
    result = generate(service, proposed["operation_id"])
    assert result["status"] == "succeeded"
    assert request(service, "first") == result
    assert len(provider.calls) == 1
    saved = service.adopt("builder-art", result["operation_id"], expected_sha256=None)
    assert saved["metadata"]["appearance"].startswith("Short silver")
    assert service.adopt("builder-art", result["operation_id"], expected_sha256=None) == saved
    service.close()
    owner.close()
    reopened = AuthorityOwner.open(tmp_path / "art.db", "reopened-owner")
    try:
        restored = ImageArt(reopened, adapters=(provider,), settings=lambda: {"mode": "review"})
        assert restored.view("builder-art")["selections"] == [saved]
        assert restored.asset("builder-art", saved["sha256"])["data_url"].startswith(
            "data:image/png;base64,"
        )
        assert len(provider.calls) == 1
    finally:
        reopened.close()


def test_auto_saves_both_reference_formats_and_reuses_them_for_portrait(art):
    owner, service, provider, preferences = art
    preferences["mode"] = "auto"
    appearance = "Two-headed Rift hound, four legs, slate fur; both heads must remain visible"
    for purpose in ("full_body", "reference_sheet", "portrait"):
        row = service.request(
            "builder-art", operation_id=purpose, target_id="rift-hound", purpose=purpose,
            brief="A two-headed creature portrait with both identities visible",
            appearance=appearance,
        )
        assert service._futures[row["operation_id"]].result(timeout=15)["status"] == "succeeded"
    assert {item["purpose"] for item in service.view("builder-art")["selections"]} == {
        "full_body",
        "reference_sheet",
        "portrait",
    }
    assert len(provider.calls[1].reference_images) == 1
    assert len(provider.calls[2].reference_images) == 2
    assert appearance in provider.calls[2].prompt
    assert "Keep every authored head within the crop" in provider.calls[2].prompt
    portrait = next(
        row for row in service.view("builder-art")["selections"] if row["purpose"] == "portrait"
    )
    frame = PortraitFrame(0.42, 0.31, 1.5).as_payload()
    service.frame("builder-art", "rift-hound", portrait["sha256"], frame)
    assert (
        next(
            row for row in service.view("builder-art")["selections"] if row["purpose"] == "portrait"
        )["frame"]
        == frame
    )
    owner._store.reclaim_package_bytes()
    assert service.asset("builder-art", portrait["sha256"])["sha256"] == portrait["sha256"]


def test_cancel_wins_inflight_result_without_replacing_asset(art):
    _, service, provider, _ = art
    proposal = request(service, "cancel")
    provider.release = Event()
    service.generate("builder-art", proposal["operation_id"])
    assert provider.started.wait(10)
    assert service.cancel("builder-art", proposal["operation_id"])["status"] == "cancelled"
    provider.release.set()
    assert service._futures[proposal["operation_id"]].result(timeout=15)["status"] == "cancelled"
    assert service.view("builder-art")["selections"] == []
    assert len(provider.calls) == 1


def test_interrupted_admission_does_not_redispatch(art):
    owner, service, provider, _ = art
    proposed = request(service, "interrupted")
    owner._store.images.approve(proposed["operation_id"])
    restored = ImageArt(owner, adapters=(provider,), settings=lambda: {"mode": "auto"})
    row = restored.view("builder-art")["requests"][0]
    assert row["status"] == "interrupted"
    assert restored.generate("builder-art", row["operation_id"])["status"] == "interrupted"
    assert not provider.calls


def test_map_reference_is_geometry_bound_and_contains_no_actor_or_secret_data(art):
    _, service, provider, preferences = art
    scene = _scene()
    projection = project_tactical_grid({"scene": scene})
    reference, metadata = map_art_reference(projection)
    assert reference.dimensions == (768, 512)
    scene["entities"] = []
    assert map_art_reference(project_tactical_grid({"scene": scene}))[0] == reference
    assert "passage-behind-wall" not in str(metadata)
    preferences["mode"] = "auto"
    result = service.request(
        "builder-art",
        operation_id="map",
        target_id="training-floor",
        purpose="map_underlay",
        brief="Concrete training floor",
        map_scene=scene,
    )
    assert service._futures[result["operation_id"]].result(timeout=15)["status"] == "succeeded"
    assert provider.calls[0].reference_images == (reference,)
    assert (
        service.view("builder-art")["selections"][0]["metadata"]["bounds"] == scene["map"]["bounds"]
    )


def test_mode_off_and_stale_selection_are_enforced(art):
    _, service, _, preferences = art
    preferences["mode"] = "off"
    with pytest.raises(ValueError, match="Enable"):
        request(service, "off")
    preferences["mode"] = "review"
    first = request(service, "first")
    second = request(service, "second")
    generate(service, first["operation_id"])
    generate(service, second["operation_id"])
    saved = service.adopt("builder-art", first["operation_id"], expected_sha256=None)
    with pytest.raises(ValueError, match="selection changed"):
        service.adopt("builder-art", second["operation_id"], expected_sha256=None)
    assert service.view("builder-art")["selections"] == [saved]


def test_changed_canonical_reference_requires_a_new_sheet_before_another_portrait(art):
    _, service, provider, preferences = art
    preferences["mode"] = "auto"
    for purpose in ("full_body", "reference_sheet", "portrait"):
        row = request(service, purpose, purpose)
        service._futures[row["operation_id"]].result(timeout=15)
    replacement = request(service, "new-full-body")
    service._futures[replacement["operation_id"]].result(timeout=15)
    partial = service.view("builder-art")["character_visuals"]["relay-keeper"]
    assert partial["status"] == "partial"
    assert partial["missing"] == ["reference_sheet", "portrait"]
    portrait = request(service, "portrait-too-early", "portrait")
    result = service._futures[portrait["operation_id"]].result(timeout=15)
    assert result["error"] == "missing_character_references"
    assert len(provider.calls) == 4


def test_terrain_changes_the_reference_but_actor_movement_does_not():
    scene = _scene()
    reference, metadata = map_art_reference(project_tactical_grid({"scene": scene}))
    scene["terrain"] = []
    changed, changed_metadata = map_art_reference(project_tactical_grid({"scene": scene}))
    assert changed != reference
    assert changed_metadata["layout_sha256"] != metadata["layout_sha256"]


def test_prepared_reference_bundle_rebinds_to_a_new_builder_without_generation(art):
    owner, service, provider, preferences = art
    preferences["mode"] = "auto"
    for purpose in ("full_body", "reference_sheet", "portrait"):
        row = request(service, purpose, purpose)
        service._futures[row["operation_id"]].result(timeout=15)
    visuals = service.view("builder-art")["character_visuals"]["relay-keeper"]
    owner.create_builder_chat(provider_id="test", model_id="test", session_id="next-builder")
    service.restore_visuals("next-builder", "saved-keeper", visuals)
    restored = service.view("next-builder")["character_visuals"]["saved-keeper"]
    assert restored["full_body"]["sha256"] == visuals["full_body"]["sha256"]
    assert restored["reference_sheet"]["sha256"] == visuals["reference_sheet"]["sha256"]
    assert len(provider.calls) == 3
    row = service.request(
        "next-builder",
        operation_id="new-portrait",
        target_id="saved-keeper",
        purpose="portrait",
        brief="Keep the same identity",
    )
    assert service._futures[row["operation_id"]].result(timeout=15)["status"] == "succeeded"
    assert len(provider.calls[-1].reference_images) == 2
