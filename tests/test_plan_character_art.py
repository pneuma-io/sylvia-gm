"""Prose-only Plan characters keep exact identities and usable art through ordinary play."""

import hashlib
import io
import json
from pathlib import Path

import pytest
from scripts.inventory_plan_artwork import plan_artwork_subjects

from sylvia_gm.application import bundled_character_art as library
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.campaign_library_selection import (
    plan_with_interview_intent,
    resolve_campaign_plan_selection,
)
from sylvia_gm.application.plan_bundle import export_plan_bundle, import_plan_bundle
from sylvia_gm.application.reusable_plans import save_reusable_rift_plan
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_image_hooks import png
from tests.test_inherited_character_art import character

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def subjects():
    return plan_artwork_subjects()


def prepared(subject):
    entry = character(subject["id"], {})
    entry["name"] = subject["name"]
    entry["sources"] = [source.copy() for source in subject["sources"]]
    return entry


def test_reviewed_plan_cast_resolves_all_72_source_identities(tmp_path, monkeypatch, subjects):
    assert len(subjects["reviewed_plans"]) == 18
    assert len(subjects["subjects"]) == 72
    assert len({item["id"] for item in subjects["subjects"]}) == 72
    assert not any(
        source["plan_id"] == "riichi-quick-test"
        for subject in subjects["subjects"]
        for source in subject["sources"]
    )
    rusks = [item for item in subjects["subjects"] if item["name"] == "Rusk"]
    assert len(rusks) == 2 and rusks[0]["id"] != rusks[1]["id"]
    engine = next(item for item in subjects["subjects"] if item["name"] == "The Vigilant Engine")
    assert engine["kind"] == "construct"
    (tmp_path / "character-art.json").write_text(
        json.dumps(
            {
                "format": 1,
                "characters": [prepared(item) for item in subjects["subjects"]],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(library, "_art_root", lambda: tmp_path)
    plans = {
        plan_id: json.loads((ROOT / "campaign-plans" / plan_id / "plan.json").read_bytes())
        for plan_id in subjects["reviewed_plans"]
    }
    for subject in subjects["subjects"]:
        assert subject["source_passage"]
        for source in subject["sources"]:
            result = library.bundled_plan_visuals(
                plans[source["plan_id"]],
                source["path"],
                subject_name=source["subject_name"],
                campaign_setting_ids=frozenset({source["campaign_setting_id"]}),
            )
            assert result["appearance"] == subject["id"]


def test_plan_binding_refuses_different_editions_subjects_or_source_namespaces(
    tmp_path, monkeypatch, subjects
):
    subject = next(item for item in subjects["subjects"] if item["name"] == "Nessa Rill")
    entry = prepared(subject)
    catalogue = {"format": 1, "characters": [entry]}
    target = tmp_path / "character-art.json"
    target.write_text(json.dumps(catalogue), encoding="utf-8")
    monkeypatch.setattr(library, "_art_root", lambda: tmp_path)
    source = subject["sources"][0]
    plan = json.loads((ROOT / "campaign-plans/last-cart-through/plan.json").read_bytes())
    raw = canonical_json_bytes(plan)

    def lookup(**overrides):
        return library.bundled_plan_visuals(
            overrides.get("plan", plan),
            overrides.get("path", source["path"]),
            subject_name=overrides.get("name", " NESSA "),
            campaign_setting_ids=overrides.get("settings", frozenset({"pneuma.severed-lands"})),
        )

    assert lookup()["appearance"] == subject["id"]
    assert lookup(plan={**plan, "id": "a-different-plan"}) is None
    copy = {**plan, "id": "a-copy", "artwork_source_plan_id": plan["id"]}
    assert lookup(plan=copy)["appearance"] == subject["id"]
    assert lookup(plan={**copy, "artwork_source_plan_id": "another-plan"}) is None
    assert lookup(plan={**copy, "artwork_source_plan_id": 123}) is None
    assert lookup(path="campaign_plan#/structure/0/situations/2") is None
    assert lookup(path="content/npcs/nessa.md") is None
    changed = json.loads(raw)
    changed["structure"][0]["situations"][1] += " The authored character passage changed."
    changed["artwork_source_plan_id"] = plan["id"]
    assert lookup(plan=changed) is None
    preferences = json.loads(raw)
    preferences["boundaries"].append("Player Boundary: no graphic injury")
    preferences["gm_guidance"]["instructions"] += "\nPlayer Preferences: quiet dialogue"
    assert lookup(plan=preferences)["appearance"] == subject["id"]
    assert lookup(path="campaign_plan#/structure/-1/situations/1") is None
    assert lookup(path="campaign_plan#/initial_state/facts/1/fact") is None
    assert lookup(name="Orlo") is None
    assert lookup(settings=frozenset()) is None
    assert lookup(settings=frozenset({"another.world"})) is None
    assert (
        library.bundled_source_visuals(
            source["plan_id"],
            source["path"],
            raw,
            subject_name="Nessa Rill",
            campaign_setting_ids=frozenset({"pneuma.severed-lands"}),
        )
        is None
    )
    # A declaration cannot pretend to be both a package source and a Plan source.
    for reference in entry["sources"]:
        reference["package_id"] = reference["plan_id"]
    target.write_text(json.dumps(catalogue), encoding="utf-8")
    assert lookup() is None


def test_actual_lite_plan_cast_reuses_art_and_exports_before_opening_artwork(
    tmp_path, monkeypatch, subjects
):
    plan_root = ROOT / "campaign-plans/last-cart-through"
    plan = json.loads((plan_root / "plan.json").read_bytes())
    art_root = tmp_path / "artwork"
    art_root.mkdir()
    monkeypatch.setattr(library, "_art_root", lambda: art_root)
    names = ("Nessa Rill", "Orlo")
    entries = [
        prepared(next(item for item in subjects["subjects"] if item["name"] == name))
        for name in names
    ]
    for index, entry in enumerate(entries):
        for step, purpose in enumerate(("full_body", "reference_sheet", "portrait")):
            image = png(3, 3, 100 + index * 3 + step)
            path = f"assets/characters/{image.sha256}.png"
            target = art_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(image.data)
            entry["visuals"][purpose] = {"path": path, "sha256": image.sha256}
    (art_root / "character-art.json").write_text(
        json.dumps(
            {
                "format": 1,
                "characters": entries,
            }
        ),
        encoding="utf-8",
    )
    archive = tmp_path / "campaign.sylvia-gm-campaign"
    with AuthorityOwner.open(tmp_path / "source.db", "owner") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO((plan_root / "setting.sylvia-gm-package").read_bytes()), install_id="lite"
        )
        selection = resolve_campaign_plan_selection(
            owner,
            tmp_path / "content",
            candidate_id=candidate.candidate_id,
            setting_id="pneuma.severed-lands",
            plan_id="last-cart-through",
        )
        plan = plan_with_interview_intent(
            selection,
            {
                "boundaries": "No graphic injury",
                "preferences": "Include quiet dialogue",
            },
        ).payload
        original = plan
        with pytest.raises(ValueError, match="artwork source"):
            selection.sources.validate(
                {**plan, "artwork_source_plan_id": 123},
                selection.plan.plan.readable_markdown,
            )
        copies = tmp_path / "plan-copies"
        markdown = selection.plan.plan.readable_markdown
        for index in range(2):
            saved = save_reusable_rift_plan(
                copies,
                operation_id=f"copy-{index}",
                title=f"Cart copy {index}",
                source_payload=plan,
                source_markdown=markdown,
                package_snapshot=selection.plan.package_snapshot,
            )
            assert saved.plan.payload["artwork_source_plan_id"] == "last-cart-through"
            assert saved.plan.payload["id"] != plan["id"]
            plan, markdown = saved.plan.payload, saved.plan.readable_markdown
        assert "artwork_source_plan_id" not in original
        transferred = io.BytesIO()
        export_plan_bundle(
            copies,
            saved.artifact_id,
            expected_content_sha256=saved.content_sha256,
            target=transferred,
        )
        transferred.seek(0)

        def install_snapshot(raw, install_id):
            owner.install_package_archive(io.BytesIO(raw), install_id=install_id)

        received = import_plan_bundle(
            tmp_path / "received-plans",
            transferred,
            install_snapshot=install_snapshot,
        )
        plan = received.plan.payload
        assert plan["artwork_source_plan_id"] == "last-cart-through"
        owner.create_campaign(
            CampaignCreate(
                campaign_id="cart",
                display_name="The cart",
                writer=WriterRole.BUILDER,
                package_lock=candidate.package_lock,
                turn_id="turn",
            )
        )
        owner.create_agent_session(
            campaign_id="cart",
            role=WriterRole.BUILDER,
            provider_id="test",
            model_id="test",
            session_id="builder",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        owner.record_builder_evidence(
            session_id="builder",
            evidence_id="authored-plan",
            source_kind="user_provided",
            content_sha256=hashlib.sha256(canonical_json_bytes(plan)).hexdigest(),
            source_locator=None,
            safe_excerpt=plan["premise"],
            claims=("cart-rescue",),
        )
        content = {"campaign_plan": plan}
        admitted = owner.admit_campaign_plan(
            plan_id="accepted-cart",
            campaign_id="cart",
            builder_session_id="builder",
            expected_revision=0,
            expected_package_lock_id=owner.current_package_lock("cart").lock_id,
            content=content,
            content_sha256=hashlib.sha256(canonical_json_bytes(content)).hexdigest(),
            provenance_evidence_ids=("authored-plan",),
            validation_outcome="valid",
            acceptance_status="accepted",
        )
        owner.complete_turn("cart", "turn")
        owner.handoff_builder_to_dm(
            handoff_id="play",
            campaign_id="cart",
            builder_session_id="builder",
            dm_session_id="gm",
            dm_provider_id="test",
            dm_model_id="test",
            expected_writer=WriterRole.BUILDER,
            expected_revision=1,
            expected_package_lock_id=owner.current_package_lock("cart").lock_id,
            expected_package_candidate_id=candidate.candidate_id,
            plan_id=admitted.plan_id,
            expected_plan_revision=admitted.plan_revision,
            expected_plan_content_sha256=admitted.content_sha256,
            completed_turn_id="turn",
            model_guidance_override=True,
        )
        owner.open_turn("cart", "play-turn")
        owner.append_agent_message(
            session_id="gm",
            message_id="meeting",
            kind=SessionMessageKind.USER,
            content={"text": "The player meets Nessa and Orlo beside the broken wagon."},
        )
        owner.update_campaign_file(
            session_id="gm",
            update_id="record",
            basis_message_ids=("meeting",),
            updates=tuple(
                {
                    "operation": "record_entity",
                    "entity_id": identity,
                    "entity_kind": "person",
                    "name": name,
                    "attributes": {},
                    "visibility": visibility,
                    "known_by": ["player"] if visibility == "player_known" else [],
                    "source_ref": source,
                }
                for identity, name, visibility, source in (
                    ("nessa", "Nessa", "player_known", entries[0]["sources"][0]["path"]),
                    ("orlo", "Orlo", "player_known", entries[1]["sources"][0]["path"]),
                    ("secret", "Nessa Rill", "gm_private", entries[0]["sources"][0]["path"]),
                    ("unknown", "Nessa", "player_known", "campaign_plan#/unknown"),
                )
            ),
        )
        original_plan_reader = owner._store.accepted_campaign_plan
        reads = []

        def read_plan(campaign):
            reads.append(campaign)
            return original_plan_reader(campaign)

        def no_state(*args, **kwargs):
            raise AssertionError("Plan portrait lookup must not load mechanics state")

        with monkeypatch.context() as metadata_only:
            metadata_only.setattr(owner._store, "accepted_campaign_plan", read_plan)
            metadata_only.setattr(owner._store, "snapshot", no_state)
            visuals = owner.character_visuals("cart")
            assert set(visuals) == {"nessa", "orlo"}
            assert visuals["nessa"]["appearance"] == entries[0]["id"]
            assert reads == ["cart"]
        assert owner._store.images.list_selections("cart") == []
        owner.image_art_action({"action": "save_settings", "mode": "off"})
        owner.export_campaign(operation_id="export", campaign_id="cart", target=archive)
        art = owner.image_art_service()
        assert len(art.view("gm", include_targets=False)["selections"]) == 6
        expected = art.character_assets("gm", "nessa")
        assert art._futures == {} and art.settings()["mode"] == "off"
    monkeypatch.setattr(library, "_art_root", lambda: tmp_path / "library-not-installed")
    with AuthorityOwner.open(tmp_path / "recipient.db", "recipient") as owner:
        owner.recover_campaign(recovery_id="imported", source=archive)
        owner.image_art_action({"action": "save_settings", "mode": "off"})
        art = owner.image_art_service()
        assert len(art.view("gm", include_targets=False)["selections"]) == 6
        assert art.character_assets("gm", "nessa") == expected
        assert art._futures == {} and art.settings()["mode"] == "off"
