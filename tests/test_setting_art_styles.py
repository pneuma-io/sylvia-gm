"""Setting defaults use real source selection and survive the ordinary image lifecycle."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from sylvia_gm.application import bundled_art_styles
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.builder_references import launch_references
from sylvia_gm.application.bundled_character_art import (
    artwork_payload_paths,
    bundled_character_image,
)
from sylvia_gm.application.bundled_packages import bundled_rift_authority
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.map_art_reference import map_art_reference
from sylvia_gm.application.tactical_grid import project_tactical_grid
from sylvia_gm.domain.art_styles import catalogue_art_styles, parse_art_style
from sylvia_gm.domain.authority import PackageRole, WriterRole
from tests.test_builder_references import launch_character, open_character_interview
from tests.test_campaign_library_selection import seed_campaign_library
from tests.test_image_art import Images
from tests.test_tactical_grid_ui import _scene

ROOT = Path(__file__).resolve().parents[1]


def _run(service, session_id, row):
    service.generate(session_id, row["operation_id"])
    result = service._futures[row["operation_id"]].result(timeout=20)
    assert result["status"] == "succeeded"
    return service.adopt(session_id, row["operation_id"], expected_sha256=None)


def test_lite_builder_gm_reopen_and_map_use_the_same_setting_style(tmp_path, monkeypatch):
    database = tmp_path / "styles.db"
    owner = AuthorityOwner.open(database, "style-owner")
    provider = Images()
    bindings = bundled_rift_authority()
    draft = open_character_interview(
        owner, bindings.ruleset.package_id, bindings.setting.package_id, "rift-awakening"
    )
    context = launch_character(owner, tmp_path, draft)
    session_id = context.session.session_id
    candidate = owner.package_candidate(launch_references(context)["candidate_id"])
    primary = next(x for x in candidate.package_lock.entries if x.role is PackageRole.PRIMARY)
    assert primary.package_id == "sylvia.srd-lite.bg3"
    owner.create_campaign(CampaignCreate(
        campaign_id="style-campaign", display_name="Style test", writer=WriterRole.DM,
        package_lock=candidate.package_lock, turn_id="turn",
    ))
    owner.create_agent_session(
        campaign_id="style-campaign", role=WriterRole.DM, provider_id="test",
        model_id="test", session_id="gm-style",
    )
    service = ImageArt(owner, adapters=(provider,), settings=lambda: {"mode": "review"})
    style = service.default_style(session_id)
    assert style["id"] == "severed-lands-anime-heroic-k-v2"
    guide = (ROOT / "artwork/styles/severed-lands-k.md").read_text(encoding="utf-8")
    assert style["brief"] == guide.split("## Rendering brief\n\n", 1)[1].split(
        "\n## Reference workflow", 1,
    )[0].strip()
    assert service.default_style("gm-style") == style
    assert service.view(session_id)["style_default"] == style
    assert provider.calls == []

    # A request admitted under v1 must finish after the library moves to the new guide.
    catalogue = json.loads((ROOT / "artwork/character-art.json").read_bytes())
    old_style = catalogue["retained_styles"][0]
    art_root = tmp_path / "artwork"
    for declared in catalogue_art_styles(catalogue, include_retained=True):
        path = art_root / declared.reference_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / "artwork" / declared.reference_path).read_bytes())
    catalogue_path = art_root / "character-art.json"
    catalogue_path.write_text(json.dumps({
        **catalogue, "styles": [old_style], "retained_styles": [],
    }), encoding="utf-8")
    monkeypatch.setattr(bundled_art_styles, "_art_root", lambda: art_root)
    assert service.default_style(session_id) == old_style
    row = service.request(
        session_id, operation_id="new-character", purpose="character", name="Synthetic scout",
        appearance="Adult scout, green eyes, plain red coat, no armor",
        brief="Use the player's chosen watercolour finish",
    )
    assert provider.calls == []
    adopted = _run(service, session_id, row)
    row = adopted["next_request"]
    assert json.loads(service.store.get(row["operation_id"])["request_json"])["style"] == old_style
    catalogue_path.write_text(json.dumps(catalogue), encoding="utf-8")
    assert service.default_style(session_id) == style
    service.close()
    owner.close()

    owner = AuthorityOwner.open(database, "reopened-style-owner")
    service = ImageArt(owner, adapters=(provider,), settings=lambda: {"mode": "review"})
    try:
        # A later default lookup must not change the style captured by the existing flow.
        monkeypatch.setattr(service, "default_style", lambda session: None)
        for stage in ("reference_sheet", "portrait"):
            assert row["purpose"] == stage
            saved = json.loads(service.store.get(row["operation_id"])["request_json"])
            assert saved["style"] == old_style
            adopted = _run(service, session_id, row)
            if stage == "reference_sheet":
                row = adopted["next_request"]
        assert [len(call.reference_images) for call in provider.calls] == [1, 2, 3]
        assert all(call.reference_images[-1].sha256 == old_style["reference"]["sha256"]
                   for call in provider.calls)
        assert old_style["brief"] in provider.calls[0].prompt
        assert all(
            "Adult scout, green eyes, plain red coat" in call.prompt for call in provider.calls
        )
        assert all("ONLY, not this subject's identity" in call.prompt for call in provider.calls)
        assert "watercolour finish" in provider.calls[0].prompt
        assert "precedence over this default" in provider.calls[0].prompt
        monkeypatch.undo()

        scene = _scene()
        projection = project_tactical_grid({"scene": scene})
        reference, _ = map_art_reference(projection)
        row = service.request(
            session_id, operation_id="map", purpose="map_underlay",
            target_id=projection["map"]["id"], brief="A daytime ruined courtyard",
            map_scene=scene,
        )
        _run(service, session_id, row)
        request = provider.calls[-1]
        assert [image.sha256 for image in request.reference_images] == [
            reference.sha256, style["reference"]["sha256"],
        ]
        assert "do not force a night scene" in request.prompt
        assert "daytime ruined courtyard" in request.prompt
        assert style["brief"] in request.prompt
    finally:
        service.close()
        owner.close()


def test_independent_setting_and_unbound_builder_do_not_inherit_severed_style(tmp_path):
    with AuthorityOwner.open(tmp_path / "independent.db", "owner") as owner:
        candidate, setting_id, _, _ = seed_campaign_library(owner, tmp_path)
        assert setting_id != "pneuma.severed-lands"
        owner.create_campaign(CampaignCreate(
            campaign_id="independent", display_name="Independent", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="turn",
        ))
        owner.create_agent_session(
            campaign_id="independent", role=WriterRole.DM, provider_id="test",
            model_id="test", session_id="independent-gm",
        )
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="unbound")
        provider = Images()
        service = ImageArt(owner, adapters=(provider,), settings=lambda: {"mode": "review"})
        try:
            assert service.default_style("independent-gm") is None
            assert service.default_style("unbound") is None
            row = service.request(
                "unbound", operation_id="universal", target_id="new-person", purpose="full_body",
                appearance="A pilot in a red suit", brief="Science fiction",
            )
            _run(service, "unbound", row)
            assert not provider.calls[0].reference_images
            assert "dark heroic" not in provider.calls[0].prompt.lower()
        finally:
            service.close()


def test_style_reference_and_resource_inventory_refuse_tampering(tmp_path, monkeypatch):
    raw = (ROOT / "artwork/character-art.json").read_bytes()
    catalogue = json.loads(raw)
    style = catalogue_art_styles(catalogue)[0]
    image = bundled_art_styles.style_reference(style.as_payload())
    resources = json.loads((ROOT / "src-tauri/tauri.conf.json").read_bytes())["bundle"]["resources"]
    assert "artwork/" + style.reference_path in artwork_payload_paths(raw)
    assert resources["../artwork/" + style.reference_path] == "artwork/" + style.reference_path
    assert image.sha256 == style.reference_sha256
    previous = catalogue_art_styles(catalogue, include_retained=True)[1]
    assert bundled_art_styles.style_reference(previous.as_payload()).sha256 == (
        previous.reference_sha256
    )
    assert "artwork/" + previous.reference_path in artwork_payload_paths(raw)
    assert previous not in catalogue_art_styles(catalogue)
    assert resources["../artwork/" + previous.reference_path] == (
        "artwork/" + previous.reference_path
    )
    with pytest.raises(ValueError, match="conflicting"):
        catalogue_art_styles({"styles": [style.as_payload()], "retained_styles": [
            style.as_payload(),
        ]})
    retained = catalogue["retained_assets"][0]
    assert bundled_character_image(retained["path"], retained["sha256"]) is not None
    assert "artwork/" + retained["path"] in artwork_payload_paths(raw)
    bad_catalogue = {**catalogue, "retained_assets": [{**retained, "path": "../private.png"}]}
    with pytest.raises(ValueError, match="Retained artwork"):
        artwork_payload_paths(json.dumps(bad_catalogue).encode())
    invalid = style.as_payload()
    invalid["reference"]["path"] = "../../private.png"
    with pytest.raises(ValueError, match="reference"):
        parse_art_style(invalid)
    duplicate = deepcopy(style.as_payload())
    duplicate["id"] += "-another"
    with pytest.raises(ValueError, match="conflicting"):
        catalogue_art_styles({"styles": [style.as_payload(), duplicate]})
    assert catalogue_art_styles({"format": 1, "characters": []}) == ()

    (tmp_path / "character-art.json").write_text(json.dumps({
        "format": 1, "characters": [], "styles": [style.as_payload()],
    }), encoding="utf-8")
    path = tmp_path / style.reference_path
    path.parent.mkdir(parents=True)
    path.write_bytes(b"changed")
    monkeypatch.setattr(bundled_art_styles, "_art_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="saved identity"):
        bundled_art_styles.style_reference(style.as_payload())
