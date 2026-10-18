"""A portable Plan brings its saved world and rules into an empty library."""

import io
import json
import zipfile
from pathlib import Path

import pytest

from sylvia_gm.agent._builder_session import _campaign_plan_example
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_campaign_plans import bundled_plan
from sylvia_gm.application.campaign_library_selection import resolve_campaign_library_selection
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.plan_bundle import export_plan_bundle, import_plan_bundle
from sylvia_gm.application.plan_snapshot import retained_plan_snapshot
from sylvia_gm.application.reusable_plans import (
    load_reusable_campaign_plan,
    save_reusable_campaign_plan,
)
from sylvia_gm.builder._setting_materializer import materialize_setting_draft
from sylvia_gm.campaign import rift
from sylvia_gm.domain.errors import CommandRejected
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness, _ScriptedProvider
from tests.test_builder_library import install_character_packages
from tests.test_builder_references import launch_character, open_character_interview


def portable_plan(root):
    root.mkdir()
    world_files = root / "workspace" / "setting-draft" / "files"
    world_files.mkdir(parents=True)
    (world_files / "world.md").write_text(
        "# Relay Island\nA silent radio tower.", encoding="utf-8", newline="\n"
    )
    with AuthorityOwner.open(root / "source.db", "plan-export-source") as owner:
        original, _ = install_character_packages(owner)
        with materialize_setting_draft(
            owner,
            root / "workspace",
            candidate_id=original.candidate_id,
            package_id="local.relay-island",
            version="1.0.0",
            title="Relay Island",
        ) as world:
            source = owner.install_package_archive(world.archive, install_id="island")
        authority = campaign_plan_sources(owner, source.package_lock)
        payload = json.loads(_campaign_plan_example(authority))
        payload.update(
            id="relay-adventure", title="The Silent Relay", premise="Find the lost signal."
        )
        plan = save_reusable_campaign_plan(
            root,
            operation_id="relay-adventure",
            source_payload=payload,
            source_markdown="# The Silent Relay\n\nFind the lost signal.",
            validate_plan=authority.validate,
            package_snapshot=retained_plan_snapshot(owner, payload),
        )
        output = io.BytesIO()
        export_plan_bundle(
            root,
            plan.artifact_id,
            expected_content_sha256=plan.content_sha256,
            target=output,
        )
        return plan, source, output.getvalue()


def test_import_into_empty_library_retains_sources_and_supports_later_builder(tmp_path):
    plan, source, archive = portable_plan(tmp_path / "source")
    chosen_file = tmp_path / "adventure.sylvia-gm-plan"
    chosen_file.write_bytes(archive)
    destination = tmp_path / "destination"
    destination.mkdir()
    database = destination / "campaigns.db"
    harness = _OwnerHarness(database, None)
    try:
        imported = harness.request(
            "import-plan",
            "install_package_file",
            {
                "install_id": "import-plan",
                "source_path": str(chosen_file),
            },
        )
        repeated = harness.request(
            "import-again",
            "install_package_file",
            {
                "install_id": "import-again",
                "source_path": str(chosen_file),
            },
        )
        assert imported == repeated
        assert imported["id"] == plan.artifact_id
        catalog = harness.request("plan-picker", "campaign_creation_catalog")
        assert next(item for item in catalog if item.get("id") == plan.artifact_id) == imported
        exported = harness.request(
            "export-plan",
            "export_campaign_plan_file",
            {
                "export_id": "portable-relay",
                "plan_id": imported["id"],
                "expected_content_sha256": imported["content_sha256"],
            },
        )
        assert Path(exported["artifact_path"]).read_bytes() == archive
    finally:
        harness.close()
    with AuthorityOwner.open(database, "reopen-imported-plan") as owner:
        candidates = owner.package_candidates()
        imported_candidates = [
            item for item in candidates if item.closure_sha256 == source.closure_sha256
        ]
        assert len(imported_candidates) == 1
        candidate = imported_candidates[0]
        # A fresh desktop also installs its bundled SRD; it supplies none of this Plan's sources.
        assert all(
            item == candidate or item.package_lock.entries[0].package_id == "sylvia.srd-lite.bg3"
            for item in candidates
        )
        assert candidate.package_lock == source.package_lock
        saved = load_reusable_campaign_plan(destination, imported["id"])
        assert saved.package_snapshot == plan.package_snapshot
        choice = resolve_campaign_library_selection(
            owner,
            destination,
            candidate_id=candidate.candidate_id,
            setting_id="local.relay-island",
            plan_id=saved.artifact_id,
        )
        assert choice.sources.read_setting("world.md") == "# Relay Island\nA silent radio tower."
        interview = open_character_interview(
            owner,
            candidate.candidate_id,
            "local.relay-island",
            saved.artifact_id,
        )
        builder = launch_character(owner, destination, interview)
        from sylvia_gm.application.builder_references import reference_reader

        references = reference_reader(owner, builder)
        assert references.read("campaign-plan/plan.md") == plan.plan.readable_markdown
        assert references.read("setting/world.md") == choice.sources.read_setting("world.md")


def test_bundled_plan_export_keeps_party_setup_and_starts_from_its_saved_copy(
    tmp_path, monkeypatch,
):
    source_root = tmp_path / "source"
    source_root.mkdir()
    harness = _OwnerHarness(source_root / "campaigns.db", None)
    try:
        catalog = harness.request("catalog", "campaign_creation_catalog")
        original = next(item for item in catalog if item.get("id") == "a-debt-in-iron")
        params = {
            "export_id": "portable-rift", "plan_id": original["id"],
            "expected_content_sha256": original["content_sha256"],
        }
        harness.send("stale-export", {
            "method": "export_campaign_plan_file",
            "params": {**params, "expected_content_sha256": "0" * 64},
        })
        assert harness.read()["ok"] is False
        exported = harness.request("export", "export_campaign_plan_file", params)
    finally:
        harness.close()

    # The imported Plan must use its contained party template even if the app's default changes.
    def unavailable_default():
        raise AssertionError("Rift setup unexpectedly used the application's current party data")

    monkeypatch.setattr(rift, "_template", unavailable_default)
    destination = tmp_path / "destination"
    destination.mkdir()
    harness = _OwnerHarness(destination / "campaigns.db", _ScriptedProvider(()))
    try:
        imported = harness.request("import", "install_package_file", {
            "install_id": "portable-rift", "source_path": exported["artifact_path"],
        })
        retained = load_reusable_campaign_plan(destination, imported["id"])
        assert retained.plan.payload["starting_party"] == (
            bundled_plan("a-debt-in-iron").plan.payload["starting_party"]
        )
        sources = {
            "ruleset_selection": "sylvia.srd-lite.bg3", "setting_id": "pneuma.severed-lands",
            "plan_id": imported["id"],
        }
        options = harness.request("party", "campaign_party_options", sources)
        assert [item["name"] for item in options["player_archetypes"]] == ["Tavin Bell"]
        archetype_id = options["player_archetypes"][0]["id"]
        draft = harness.request("new", "create_guided_campaign_draft", {
            "operation_id": "new-rift", "display_name": "Imported Rift",
            "provider_id": "test.transport", "model_id": "test.transport/model0",
        })
        answers = (
            ("ruleset", {"candidate_id": sources["ruleset_selection"]}),
            ("setting", {"setting_id": sources["setting_id"]}),
            ("plan", {"plan_id": sources["plan_id"]}),
            ("name", {"display_name": "Imported Rift"}),
            ("participants", {"archetype_id": archetype_id, "companion_ids": []}),
            ("boundaries_preferences", {"boundaries": "", "preferences": ""}),
            ("specific_requests", {"no_additional_requests": True, "requests": ""}),
        )
        for revision, (step, answer) in enumerate(answers):
            harness.request(f"answer-{step}", "advance_guided_campaign_draft", {
                "operation_id": f"answer-{step}", "campaign_id": draft["campaign_id"],
                "builder_session_id": draft["builder_session_id"],
                "expected_construction_revision": revision, "current_step": step,
                "visible_answer": step, "answer": answer,
            })
        harness.request("start", "start_guided_campaign", {
            "candidate_id": sources["ruleset_selection"],
            "setting_id": sources["setting_id"], "plan_id": sources["plan_id"],
            "operation_id": "start-imported", "campaign_id": draft["campaign_id"],
            "builder_session_id": draft["builder_session_id"], "display_name": "Imported Rift",
            "player_character_id": archetype_id, "companion_ids": [], "premise": "",
            "construction_intent": {
                "boundaries": "", "preferences": "", "specific_requests": "",
                "contributions": {},
            },
        })
        finalized = harness.request("finalize", "finalize_campaign_construction", {
            "campaign_id": draft["campaign_id"],
            "dm_provider_id": "test.transport", "dm_model_id": "test.transport/model0",
            "model_guidance_override": True,
        })
        assert finalized["handoff"]["dm_session_id"]
        assert finalized["dm_turn_id"]
    finally:
        harness.close()
    with AuthorityOwner.open(destination / "campaigns.db", "reopened-rift") as owner:
        snapshot = owner.snapshot(draft["campaign_id"])
        assert len(snapshot.participants) == 1
        assert snapshot.participants[0].data["name"] == "Tavin Bell"
        saved = load_reusable_campaign_plan(destination, imported["id"])
        assert saved.plan.payload["bindings"]["ruleset"] == original["ruleset"]
        assert saved.plan.payload["bindings"]["campaign_setting"] == original["campaign_setting"]
        assert owner.current_package_lock(draft["campaign_id"]).package_lock.entries[0].sha256 == (
            saved.plan.payload["bindings"]["ruleset"]["content_sha256"]
        )


@pytest.mark.parametrize("damage", ["missing_snapshot", "outside_path", "wrong_rules"])
def test_invalid_plan_bundle_publishes_neither_plan_nor_packages(tmp_path, damage):
    _plan, _source, archive = portable_plan(tmp_path / "source")
    with zipfile.ZipFile(io.BytesIO(archive)) as original:
        files = {name: original.read(name) for name in original.namelist()}
    if damage == "missing_snapshot":
        files.pop("setting.sylvia-gm-package")
    elif damage == "outside_path":
        files["../outside.txt"] = b"unexpected"
    else:
        payload = json.loads(files["plan.json"])
        payload["bindings"]["ruleset"]["content_sha256"] = "0" * 64
        files["plan.json"] = json.dumps(payload).encode()
    forged = io.BytesIO()
    with zipfile.ZipFile(forged, "w") as modified:
        for name, value in files.items():
            modified.writestr(name, value)
    forged.seek(0)
    installed = []
    destination = tmp_path / "destination"
    message = "package bindings do not match" if damage == "wrong_rules" else "members are invalid"
    with pytest.raises((ValueError, CommandRejected), match=message):
        import_plan_bundle(
            destination,
            forged,
            install_snapshot=lambda data, expected: installed.append(expected),
        )
    assert installed == []
    assert not (destination / "campaign-plans").exists()
    assert not (tmp_path / "outside.txt").exists()
