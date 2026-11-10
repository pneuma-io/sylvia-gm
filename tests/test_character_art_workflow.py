"""Normal character artwork creation advances real saved image requests without format juggling."""

from threading import Event

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.image_hooks import ImageUpdate
from tests.test_image_art import Images, generate
from tests.test_image_art import art as art
from tests.test_image_hooks import png


def create(service, **parameters):
    return service.request(
        "builder-art",
        operation_id="keeper-art",
        purpose="character",
        name="Keeper",
        appearance="Silver hair, brown eyes, blue coat",
        brief="Painterly science fiction",
        **parameters,
    )


def test_review_walks_all_stages_with_separate_generation_and_adoption(art):
    _, service, provider, _ = art
    row = create(service)
    assert row["status"] == "proposed"
    assert row["character_flow"] == {
        "id": row["character_flow"]["id"],
        "step": 1,
        "total": 3,
        "name": "Keeper",
    }
    assert provider.calls == []
    for index, purpose in enumerate(("full_body", "reference_sheet", "portrait")):
        assert row["purpose"] == purpose and row["status"] == "proposed"
        result = generate(service, row["operation_id"])
        assert result["status"] == "succeeded"
        assert len(provider.calls) == index + 1
        assert len(service.view("builder-art")["selections"]) == index
        adopted = service.adopt("builder-art", row["operation_id"], expected_sha256=None)
        if index < 2:
            row = adopted["next_request"]
            assert len(provider.calls) == index + 1
    view = service.view("builder-art")
    assert view["needed_art"] == []
    assert view["targets"][0]["name"] == "Keeper"
    assert view["character_visuals"][view["targets"][0]["id"]]["portrait"]
    assert [len(call.reference_images) for call in provider.calls] == [0, 1, 2]
    assert create(service)["status"] == "succeeded"
    assert len(provider.calls) == 3


def test_auto_finishes_then_reuses_current_art_without_more_generation(art):
    _, service, provider, preferences = art
    preferences["mode"] = "auto"
    row = create(service)
    for _ in range(3):
        service._futures[row["operation_id"]].result(timeout=15)
        row = service.view("builder-art")["requests"][0]
    view = service.view("builder-art")
    assert view["needed_art"] == []
    assert len(view["selections"]) == 3
    target_id = view["targets"][0]["id"]
    assert (
        service.request(
            "builder-art",
            operation_id="another-deliberate-action",
            target_id=target_id,
            purpose="character",
            name="Keeper",
            brief="Reuse the established art",
        )["status"]
        == "already_available"
    )
    assert len(provider.calls) == 3


def test_mode_change_and_cancel_stop_the_automatic_sequence(art):
    _, service, provider, preferences = art
    preferences["mode"] = "auto"
    provider.release = Event()
    row = create(service)
    assert provider.started.wait(10)
    preferences["mode"] = "review"
    provider.release.set()
    service._futures[row["operation_id"]].result(timeout=15)
    assert service.view("builder-art")["selections"] == []
    adopted = service.adopt("builder-art", row["operation_id"], expected_sha256=None)
    next_request = adopted["next_request"]
    assert next_request["status"] == "proposed"
    service.cancel("builder-art", next_request["operation_id"])
    assert len(provider.calls) == 1
    assert create(service)["status"] == "cancelled"


def test_stage_failure_preserves_existing_card_portrait_and_does_not_retry(tmp_path):
    class LimitedImages(Images):
        def submit(self, request):
            if self.calls:
                self.calls.append(request)
                return ImageUpdate(
                    request.session_id, request.operation_id, "failed", error="rate_limited"
                )
            return super().submit(request)

    with AuthorityOwner.open(tmp_path / "art.db", "owner") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder-art")
        provider = LimitedImages()
        service = ImageArt(owner, adapters=(provider,), settings=lambda: {"mode": "auto"})
        service.restore_card_portrait(
            "builder-art",
            "keeper",
            name="Keeper",
            appearance="Blue coat",
            image=png().data,
        )
        original = service.view("builder-art")["selections"][0]
        row = create(service, target_id="keeper")
        service._futures[row["operation_id"]].result(timeout=15)
        failed = service.view("builder-art")["requests"][0]
        service._futures[failed["operation_id"]].result(timeout=15)
        assert create(service, target_id="keeper")["error"] == "rate_limited"
        assert len(provider.calls) == 2
        assert (
            next(
                row
                for row in service.view("builder-art")["selections"]
                if row["purpose"] == "portrait"
            )
            == original
        )
        service.close()


def test_reopen_never_runs_the_next_review_stage_without_generate(art, tmp_path):
    owner, service, provider, _ = art
    row = create(service)
    generate(service, row["operation_id"])
    service.adopt("builder-art", row["operation_id"], expected_sha256=None)
    service.close()
    owner.close()
    with AuthorityOwner.open(tmp_path / "art.db", "reopened") as owner:
        reopened = ImageArt(owner, adapters=(provider,), settings=lambda: {"mode": "auto"})
        assert reopened.view("builder-art")["requests"][0]["status"] == "proposed"
        assert len(provider.calls) == 1
        reopened.close()
