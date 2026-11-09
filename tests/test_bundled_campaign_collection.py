"""Built-in Plans use the real Interview, retained packages and reusable-character paths."""

import json
import io
import zipfile
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import capture_content_references
from sylvia_gm.application.bundled_campaign_plans import (
    bundled_plan,
    bundled_plan_files,
    bundled_plans_root,
    read_plan_catalogue,
)
from sylvia_gm.application.bundled_packages import (
    bundled_campaign_content_catalog,
    bundled_rift_authority,
)
from sylvia_gm.application.campaign_library_selection import resolve_campaign_library_selection
from sylvia_gm.application.interview_content import resolve_interview_content
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness


def test_flagship_references_setting_secrets_and_rejects_inline_collisions():
    selected = bundled_plan("rift-awakening")
    payload = json.loads(canonical_json_bytes(selected.plan.payload))
    with zipfile.ZipFile(io.BytesIO(selected.package_snapshot)) as archive:
        knowledge = json.loads(archive.read("packages/pneuma.severed-lands/setting-knowledge.json"))
    facts = {fact["id"]: fact for fact in knowledge["facts"]}
    secret = payload["secrets"][0]
    assert secret["setting_fact_id"] == secret["id"]
    fact = facts[secret["id"]]
    payload["secrets"][0] = {
        "id": secret["id"], "fact": fact["fact"], "known_by": fact["known_by"],
        "reveal": secret["reveal"],
    }
    with pytest.raises(ValueError, match="use setting_fact_id"):
        validate_plan_snapshot(selected.package_snapshot, payload, "# Invalid duplicate secret")


def test_collection_is_public_complete_and_does_not_create_library_copies(tmp_path):
    public = read_plan_catalogue(bundled_plans_root())
    with AuthorityOwner.open(tmp_path / "campaigns.db", "collection") as owner:
        rows = _campaign_creation_catalog(tmp_path, owner)
        plans = [row for row in rows if row["kind"] == "campaign_plan"]
        assert len(plans) == len(public) == 17
        assert {row["id"] for row in plans} == {row["id"] for row in public}
        for row in plans:
            source = next(item for item in public if item["id"] == row["id"])
            assert row["summary"] == source["pitch"]
            assert row["browsing"]["format"] == source["format"]
            assert not row.get("reusable")
        assert not (tmp_path / "campaign-plans").exists()
        base_setting = next(row for row in rows if row["kind"] == "campaign_setting")
        flagship = next(row for row in plans if row["id"] == "rift-awakening")
        assert base_setting["content_sha256"] == bundled_rift_authority().setting.content_sha256
        assert flagship["campaign_setting"]["content_sha256"] != base_setting["content_sha256"]
        plans[0]["starting_party"].clear()
        assert bundled_campaign_content_catalog()[2]["starting_party"]
    with AuthorityOwner.open(tmp_path / "campaigns.db", "collection-reopened") as owner:
        assert len([row for row in _campaign_creation_catalog(tmp_path, owner)
                    if row["kind"] == "campaign_plan"]) == 17


@pytest.mark.parametrize("slug", [
    "rift-awakening", "last-cart-through", "the-cartographers-wake",
    "the-map-of-tomorrow", "a-debt-in-iron",
])
def test_interview_and_builder_use_each_builtins_exact_retained_content(tmp_path, slug):
    with AuthorityOwner.open(tmp_path / "campaigns.db", f"collection-{slug}") as owner:
        selected = bundled_plan(slug)
        rule_id = selected.plan.payload["bindings"]["ruleset"]["id"]
        content = resolve_interview_content(owner, tmp_path, {
            "ruleset": {"candidate_id": rule_id},
            "setting": {"setting_id": "pneuma.severed-lands"},
            "plan": {"plan_id": slug},
        })
        assert content.candidate.closure_sha256 == (
            selected.plan.payload["bindings"]["package_closure_sha256"]
        )
        selection = resolve_campaign_library_selection(
            owner, tmp_path, candidate_id=rule_id,
            setting_id="pneuma.severed-lands", plan_id=slug,
        )
        assert selection.plan.plan.payload["id"] == slug
        references = capture_content_references(
            owner, tmp_path, _campaign_creation_catalog(tmp_path, owner),
            ruleset_id=rule_id, setting_id="pneuma.severed-lands", plan_id=slug,
        )
        assert references["plan"]["payload"]["id"] == slug
        world = next(row for row in references["packages"] if row["prefix"] == "setting")
        assert world["content_sha256"] == (
            selected.plan.payload["bindings"]["campaign_setting"]["content_sha256"]
        )
        if slug == "a-debt-in-iron":
            (character,) = selection.party_options()["player_archetypes"]
            assert character["name"] == "Tavin Bell"
            assert character["campaign_ready"] is True
            assert selection.sources.setting.content_sha256 != bundled_plan(
                "last-cart-through"
            ).plan.payload["bindings"]["campaign_setting"]["content_sha256"]


def test_build_inventory_excludes_unselected_files_and_includes_fixed_character(tmp_path):
    root = bundled_plans_root()
    files = bundled_plan_files(root)
    assert root / "a-debt-in-iron/setting-files/characters/tavin-bell.json" in files
    assert root / "player-guide.html" not in files
    snapshot_path = root / "rift-awakening/setting.sylvia-gm-package"
    assert snapshot_path in files
    with zipfile.ZipFile(snapshot_path) as archive:
        assert "packages/pneuma.severed-lands/DM-SECRETS.md" in archive.namelist()
        assert "packages/pneuma.severed-lands/setting-knowledge.json" in archive.namelist()
    assert root / "the-rift-awakening/plan.json" in files
    assert root / "the-rift-awakening/setting.sylvia-gm-package" in files
    assert len([path for path in files if path.name == "plan.json"]) == 18
    invalid = [{"id": "../private", "title": "Invalid", "pitch": "Invalid", "format": "Test"}]
    (tmp_path / "catalogue.json").write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        bundled_plan_files(tmp_path)


def test_fixed_builtin_exports_without_setup_and_coexists_with_its_imported_copy(tmp_path):
    harness = _OwnerHarness(tmp_path / "campaigns.db", None)
    try:
        original = bundled_plan("a-debt-in-iron")
        exported = harness.request("export-fixed", "export_campaign_plan_file", {
            "export_id": "tavin-export", "plan_id": original.artifact_id,
            "expected_content_sha256": original.content_sha256,
        })
        assert Path(exported["artifact_path"]).is_file()
        imported = harness.request("import-fixed", "install_package_file", {
            "install_id": "tavin-import", "source_path": exported["artifact_path"],
        })
        assert imported["id"] != original.artifact_id
        catalog = harness.request("catalog-after-import", "campaign_creation_catalog")
        assert len([row for row in catalog if row["kind"] == "campaign_plan"]) == 18
        options = harness.request("fixed-party", "campaign_party_options", {
            "ruleset_selection": original.plan.payload["bindings"]["ruleset"]["id"], "setting_id": "pneuma.severed-lands",
            "plan_id": imported["id"],
        })
        assert options["player_archetypes"][0]["name"] == "Tavin Bell"
        assert options["player_archetypes"][0]["campaign_ready"] is True
    finally:
        harness.close()
