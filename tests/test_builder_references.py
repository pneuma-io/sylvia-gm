"""Saved Builder references retain their source independently of the launching Interview."""

import json
from pathlib import Path

import pytest

from sylvia_gm.agent._builder_reference_copy_tool import builder_reference_copy_tool
from sylvia_gm.agent._builder_session import _builder_reference_tool, _provider_history
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import (
    BuilderReferences,
    capture_content_references,
    capture_launch_references,
    reference_reader,
)
from sylvia_gm.application.bundled_campaign_plans import bundled_plan
from sylvia_gm.application.bundled_packages import (
    bundled_rift_authority,
)
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog


def open_character_interview(owner, ruleset_id, setting_id, plan_id):
    draft = owner.create_guided_campaign_draft(
        operation_id="draft-references",
        display_name="Source context",
        builder_session_id="source-interview",
        builder_provider_id="test",
        builder_model_id="test",
    )
    for revision, (step, value) in enumerate(
        (
            ("ruleset", {"candidate_id": ruleset_id}),
            ("setting", {"setting_id": setting_id}),
            ("plan", {"plan_id": plan_id}),
            ("name", {"display_name": "Source context"}),
        )
    ):
        owner.advance_guided_campaign_draft(
            operation_id=f"choose-{step}",
            campaign_id=draft.campaign_id,
            builder_session_id=draft.builder_session_id,
            expected_construction_revision=revision,
            current_step=step,
            visible_answer=step,
            answer=value,
        )
    return draft


def launch_character(owner, storage_root, draft):
    references = capture_launch_references(
        owner,
        storage_root,
        owner.session_context(draft.builder_session_id),
        _campaign_creation_catalog(storage_root, owner),
    )
    launched = owner.start_interview_builder_chat(
        operation_id="launch-reference-character",
        campaign_id=draft.campaign_id,
        parent_session_id=draft.builder_session_id,
        expected_construction_revision=4,
        purpose="player_character",
        visible_answer="New Character",
        child_session_id="source-builder",
        references=references,
    )
    return owner.session_context(launched.child_session_id)


def test_explicit_builder_choices_capture_the_same_snapshot_without_an_interview(tmp_path):
    bindings = bundled_rift_authority()
    plan = bundled_plan("rift-awakening").plan
    with AuthorityOwner.open(tmp_path / "explicit-source.db", "explicit-source-owner") as owner:
        catalog = _campaign_creation_catalog(tmp_path, owner)
        explicit = capture_content_references(
            owner, tmp_path, catalog,
            ruleset_id=bindings.ruleset.package_id,
            setting_id=bindings.setting.package_id,
            plan_id=plan.payload["id"],
        )
        draft = open_character_interview(
            owner, bindings.ruleset.package_id, bindings.setting.package_id, plan.payload["id"]
        )
        interview = capture_launch_references(
            owner, tmp_path, owner.session_context(draft.builder_session_id), catalog,
        )
        assert explicit == interview
        assert explicit["candidate_id"] is not None
        assert explicit["plan"]["content_sha256"] == plan.content_sha256
        assert any(package["prefix"] == "ruleset" for package in explicit["packages"])


@pytest.mark.parametrize(
    ("choices", "message"),
    [
        ({"ruleset_id": "missing-ruleset"}, "Ruleset is unavailable"),
        ({"setting_id": "missing-setting"}, "Setting snapshot is unavailable"),
        ({"plan_id": "missing-plan"}, "Plan reference is unavailable"),
    ],
)
def test_explicit_builder_choices_reject_unavailable_sources(tmp_path, choices, message):
    with (
        AuthorityOwner.open(tmp_path / "unavailable-source.db", "unavailable-owner") as owner,
        pytest.raises(CommandRejected, match=message),
    ):
        capture_content_references(
            owner, tmp_path, _campaign_creation_catalog(tmp_path, owner),
            **{"ruleset_id": bundled_rift_authority().ruleset.package_id, **choices},
        )


def test_party_step_can_open_an_independent_plan_completion_with_saved_sources(tmp_path):
    from sylvia_gm.agent._builder_session import _builder_prompt

    database = tmp_path / "complete-required.db"
    bindings = bundled_rift_authority()
    plan = bundled_plan("rift-awakening").plan
    with AuthorityOwner.open(database, "complete-required-owner") as owner:
        draft = open_character_interview(
            owner, bindings.ruleset.package_id, bindings.setting.package_id, plan.payload["id"]
        )
        parent = owner.session_context(draft.builder_session_id)
        references = capture_launch_references(
            owner, tmp_path, parent, _campaign_creation_catalog(tmp_path, owner)
        )
        with pytest.raises(CommandRejected, match="selected Campaign Plan"):
            owner.start_interview_builder_chat(
                operation_id="missing-plan-completion",
                campaign_id=draft.campaign_id,
                parent_session_id=draft.builder_session_id,
                expected_construction_revision=4,
                purpose="plan",
                visible_answer="Complete required characters in Plan",
                child_session_id="must-not-open",
                references={**references, "plan": None},
            )
        launch = owner.start_interview_builder_chat(
            operation_id="complete-required",
            campaign_id=draft.campaign_id,
            parent_session_id=draft.builder_session_id,
            expected_construction_revision=4,
            purpose="plan",
            visible_answer="Complete required characters in Plan",
            child_session_id="required-character-builder",
            references=references,
        )
        assert launch.status == "independent"
        assert owner.session_context(draft.builder_session_id) == parent
        child = owner.session_context(launch.child_session_id)
        assert child.session.campaign_id is None
        assert child.messages[0].content["interview_step"] == "participants"
        assert "Complete the required Character Files" in _builder_prompt(child)
        assert json.loads(reference_reader(owner, child).read("campaign-plan/plan.json")) == (
            json.loads(canonical_json_bytes(plan.payload))
        )
    with AuthorityOwner.open(database, "complete-required-reopened") as owner:
        child = owner.session_context("required-character-builder")
        assert (
            reference_reader(owner, child).read("campaign-plan/plan.md") == plan.readable_markdown
        )
        workspace = tmp_path / "completion-workspace"
        copy = builder_reference_copy_tool(reference_reader(owner, child), workspace)
        for extension in ("json", "md"):
            assert (
                copy.handler(
                    {
                        "source_path": f"campaign-plan/plan.{extension}",
                        "destination_path": f"campaign-plan-draft/plan.{extension}",
                    },
                    None,
                )["success"]
                is True
            )
        assert json.loads((workspace / "campaign-plan-draft/plan.json").read_text()) == (
            json.loads(canonical_json_bytes(plan.payload))
        )
        assert (workspace / "campaign-plan-draft/plan.md").read_text() == plan.readable_markdown
        assert owner.session_context(draft.builder_session_id) == parent


def test_bundled_sources_remain_exact_after_reselection_and_restart(tmp_path: Path) -> None:
    database = tmp_path / "references.db"
    bindings = bundled_rift_authority()
    original_plan = bundled_plan("rift-awakening").plan
    with AuthorityOwner.open(database, "owner-source-context") as owner:
        draft = open_character_interview(
            owner,
            bindings.ruleset.package_id,
            bindings.setting.package_id,
            original_plan.payload["id"],
        )
        context = launch_character(owner, tmp_path, draft)
        refreshed = _campaign_creation_catalog(tmp_path, owner)
        assert next(item for item in refreshed if item.get("kind") == "ruleset")[
            "selection_id"
        ] == (bindings.ruleset.package_id)
        reader = reference_reader(owner, context)
        assert reader is not None
        assert "campaign-plan/plan.md" in reader.paths
        assert any(path.startswith("ruleset/") for path in reader.paths)
        assert any(path.startswith("setting/") for path in reader.paths)
        assert any(path.startswith("dependencies/") for path in reader.paths)
        assert reader.read("campaign-plan/plan.md") == original_plan.readable_markdown
        assert (
            json.loads(reader.read("campaign-plan/plan.json"))["id"] == original_plan.payload["id"]
        )
        assert original_plan.readable_markdown not in json.dumps(_provider_history(context))
        setting_path = next(
            path for path in reader.paths if path.startswith("setting/") and path.endswith(".md")
        )
        original_setting_text = reader.read(setting_path)
        owner.advance_guided_campaign_draft(
            operation_id="choose-other-ruleset",
            campaign_id=draft.campaign_id,
            builder_session_id=draft.builder_session_id,
            expected_construction_revision=4,
            current_step="ruleset",
            visible_answer="No Ruleset",
            answer={"candidate_id": None},
        )
        assert owner.session_context("source-builder") == context
    with AuthorityOwner.open(database, "owner-reference-reopen") as owner:
        reader = reference_reader(owner, owner.session_context("source-builder"))
        assert reader.read(setting_path) == original_setting_text
        assert reader.read("campaign-plan/plan.md") == original_plan.readable_markdown
        tool = _builder_reference_tool(reader)
        first = tool.handler({"action": "read", "path": setting_path, "limit": 12}, None)
        second = tool.handler(
            {"action": "read", "path": setting_path, "offset": 12, "limit": 12}, None
        )
        assert first["content"] + second["content"] == original_setting_text[:24]
        assert first["next_offset"] == 12
        assert tool.handler({"action": "read", "path": "../../unrelated"}, None)["success"] is False
        found = tool.handler(
            {
                "action": "find",
                "path": "ruleset/runtime/ruleset.yaml",
                "query": "target.resources[resource] >= amount",
                "limit": 1,
            },
            None,
        )
        assert found["success"] is True
        match = found["matches"][0]
        excerpt = tool.handler(
            {
                "action": "read",
                "path": found["path"],
                "offset": match["excerpt_offset"],
                "limit": len(match["excerpt"]),
            },
            None,
        )
        assert excerpt["content"] == match["excerpt"]
        assert "target.resources[resource] >= amount" in excerpt["content"]


def test_reference_find_is_literal_paged_bounded_and_scoped() -> None:
    path = "ruleset/runtime/example.yaml"
    query = "β[charge]"
    content = "Résumé\n" + (query + "\n") * 53
    reads = []

    def read(selected):
        reads.append(selected)
        assert selected == path
        return content

    tool = _builder_reference_tool(BuilderReferences((path,), read))
    first = tool.handler(
        {
            "action": "find",
            "path": path,
            "query": query,
            "limit": 1000,
        },
        None,
    )
    assert first["success"] is True
    assert len(first["matches"]) == 50
    assert first["matches"][0]["offset"] == len("Résumé\n")
    assert all(len(match["excerpt"]) <= 320 + len(query) for match in first["matches"])
    second = tool.handler(
        {
            "action": "find",
            "path": path,
            "query": query,
            "offset": first["next_offset"],
        },
        None,
    )
    assert len(second["matches"]) == 3
    assert second["next_offset"] is None
    offsets = [match["offset"] for page in (first, second) for match in page["matches"]]
    assert len(set(offsets)) == 53
    assert all(content[offset : offset + len(query)] == query for offset in offsets)
    absent = tool.handler({"action": "find", "path": path, "query": "missing"}, None)
    assert absent["matches"] == []
    assert absent["next_offset"] is None
    read_count = len(reads)
    for params in (
        {"path": "../../unrelated", "query": query},
        {"path": path, "query": ""},
        {"path": path, "query": "x" * 121},
        {"path": path, "query": query, "offset": -1},
    ):
        assert tool.handler({"action": "find", **params}, None)["success"] is False
    assert len(reads) == read_count


def test_non_dnd_builder_reads_its_installed_ruleset_and_setting(tmp_path: Path) -> None:
    fixtures = Path(__file__).parent / "fixtures"
    ruleset_root = fixtures / "rulesets" / "signal-and-static"
    setting_root = fixtures / "campaign-settings" / "signal-and-static"
    ruleset_record = json.loads((ruleset_root / "package-record.json").read_text())
    setting_record = json.loads((setting_root / "package-record.json").read_text())
    ruleset_record["dependencies"] = [
        {
            "namespace": "campaign_setting",
            "kind": "campaign_setting",
            "id": setting_record["id"],
            "version": setting_record["version"],
        }
    ]
    drafts = tuple(
        NormalizedPackageDraft(
            record=record,
            payloads={
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file() and path.name != "package-record.json"
            },
        )
        for root, record in ((ruleset_root, ruleset_record), (setting_root, setting_record))
    )
    with AuthorityOwner.open(tmp_path / "neutral.db", "owner-neutral-references") as owner:
        with materialize_package_archive(drafts) as archive:
            candidate = owner.install_package_archive(archive.archive, install_id="install-neutral")
        draft = open_character_interview(owner, candidate.candidate_id, setting_record["id"], None)
        context = launch_character(owner, tmp_path, draft)
        reader = reference_reader(owner, context)
        assert reader is not None
        assert (
            reader.read("ruleset/runtime/ruleset.yaml")
            == (ruleset_root / "runtime/ruleset.yaml").read_text()
        )
        setting_path = next(
            path for path in reader.paths if path.startswith("setting/") and path.endswith(".md")
        )
        assert (
            reader.read(setting_path)
            == (setting_root / setting_path.removeprefix("setting/")).read_text()
        )
        tool = _builder_reference_tool(reader)
        pages = [{"path": path, "limit": 4000} for path in (
            "ruleset/runtime/ruleset.yaml", setting_path,
        )]
        batch = tool.handler({"action": "read_many", "pages": pages}, None)
        assert batch["success"] is True
        assert batch["pages"] == [
            tool.handler({"action": "read", **page}, None) for page in pages
        ]
        assert "campaign-plan/plan.md" not in reader.paths
        assert not any("severed" in path or "wotc" in path for path in reader.paths)
