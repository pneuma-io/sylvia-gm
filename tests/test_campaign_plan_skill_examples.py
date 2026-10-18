"""Worked preparation remains retrievable, source-bound and usable by the real Plan importer."""

import hashlib
import io
import json

import pytest
from scripts.build_severed_lands_campaigns import EXAMPLES, PLANS, render_plan, sources_for

from sylvia_gm.agent._builder_session import _builder_skill_loader_tool
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.campaign_library_selection import resolve_campaign_library_selection
from sylvia_gm.application.plan_bundle import export_plan_bundle, import_plan_bundle
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.application.reusable_plans import save_reusable_campaign_plan


def test_builder_retrieves_a_complete_valid_example_from_its_advertised_references():
    tool = _builder_skill_loader_tool()
    main = tool.handler({"name": "BUILD CAMPAIGN PLAN"}, None)
    assert main["success"] is True
    for slug, prefix in EXAMPLES.items():
        texts = {}
        for suffix in ("json", "md"):
            reference = f"references/{prefix}-plan.{suffix}"
            assert reference in main["references"]
            loaded = tool.handler({"name": main["name"], "reference": reference}, None)
            assert loaded["success"] is True
            assert loaded["reference"] == reference
            texts[suffix] = loaded["instructions"]
            assert loaded["sha256"] == hashlib.sha256(texts[suffix].encode("utf-8")).hexdigest()
            assert texts[suffix] == (PLANS / slug / f"plan.{suffix}").read_text(encoding="utf-8")
        plan = json.loads(texts["json"])
        validate_plan_snapshot(sources_for(PLANS / slug).snapshot, plan, texts["md"])
        assert texts["md"] == render_plan(plan)


@pytest.mark.parametrize(
    "reference",
    [
        "../build_ruleset/SKILL.md",
        "references/../../_skills.py",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "references/unknown.md",
        "SKILL.md",
        "",
    ],
)
def test_builder_reference_lookup_rejects_paths_outside_the_advertised_catalog(reference):
    result = _builder_skill_loader_tool().handler(
        {"name": "BUILD CAMPAIGN PLAN", "reference": reference},
        None,
    )
    assert result == {
        "success": False,
        "status": "rejected",
        "code": "skill_reference_unavailable",
    }


def test_reference_belongs_to_the_selected_skill():
    result = _builder_skill_loader_tool().handler(
        {
            "name": "BUILD RULESET",
            "reference": "references/combat-plan.json",
        },
        None,
    )
    assert result["success"] is False
    assert result["code"] == "skill_reference_unavailable"


@pytest.mark.parametrize(
    "slug,name",
    [
        ("a-debt-in-iron", "Tavin Bell"),
    ],
)
def test_fixed_protagonist_is_ready_after_import_into_an_empty_library(tmp_path, slug, name):
    root = PLANS / slug
    payload = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    markdown = (root / "plan.md").read_text(encoding="utf-8")
    sources = sources_for(root)
    source_library = tmp_path / "source"
    saved = save_reusable_campaign_plan(
        source_library,
        operation_id=slug,
        source_payload=payload,
        source_markdown=markdown,
        validate_plan=lambda value, text: validate_plan_snapshot(sources.snapshot, value, text),
        package_snapshot=sources.snapshot,
    )
    bundle = io.BytesIO()
    export_plan_bundle(
        source_library,
        saved.artifact_id,
        expected_content_sha256=saved.content_sha256,
        target=bundle,
    )
    destination = tmp_path / "destination"
    destination.mkdir()
    with AuthorityOwner.open(destination / "campaigns.db", "fixed-plan-import") as owner:

        def install(data, closure):
            candidate = owner.install_package_archive(io.BytesIO(data), install_id=slug)
            assert candidate.closure_sha256 == closure

        imported = import_plan_bundle(
            destination, io.BytesIO(bundle.getvalue()), install_snapshot=install
        )
        candidate = next(
            c
            for c in owner.package_candidates()
            if c.closure_sha256 == payload["bindings"]["package_closure_sha256"]
        )
        choice = resolve_campaign_library_selection(
            owner,
            destination,
            candidate_id=candidate.candidate_id,
            setting_id="pneuma.severed-lands",
            plan_id=imported.artifact_id,
        )
        options = choice.party_options()
        assert options["starting_party"]["player_character"]["mode"] == "fixed"
        (protagonist,) = options["player_archetypes"]
        assert protagonist["name"] == name
        assert protagonist["campaign_ready"] is True
        assert (
            protagonist["source_ref"] == payload["starting_party"]["player_character"]["character"]
        )
        assert imported.content_sha256 == saved.content_sha256
