"""Compatible examples are immutable authoring sources, not selected player characters."""

import hashlib
import json

from sylvia_gm.agent._builder_reference_copy_tool import builder_reference_copy_tool
from sylvia_gm.agent._builder_session import _participant_draft_check_tool, _provider_history
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import (
    capture_launch_references,
    reader_for_references,
    with_character_examples,
)
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog
from tests.test_builder_references import open_character_interview
from tests.test_bundled_starter_characters import authority, selection


def test_real_lite_examples_are_valid_owned_snapshots_not_prompt_prefill(tmp_path):
    database = tmp_path / "examples.db"
    with AuthorityOwner.open(database, "examples") as owner:
        selected = selection(owner, tmp_path)
        _, declaration = authority(owner, selected)
        parent = open_character_interview(
            owner, selected.candidate.candidate_id, selected.sources.setting.package_id,
            selected.plan.artifact_id,
        )
        before = owner.session_context(parent.builder_session_id)
        references = capture_launch_references(
            owner, tmp_path, before, _campaign_creation_catalog(tmp_path, owner),
        )
        captured = with_character_examples(owner, tmp_path, references, purpose="player_character")
        examples = captured["character_examples"]
        assert len(examples) == 12
        assert "character_examples" not in references
        assert with_character_examples(owner, tmp_path, references, purpose="companion")[
            "character_examples"
        ] == []
        child = owner.start_interview_builder_chat(
            operation_id="example-child", campaign_id=parent.campaign_id,
            parent_session_id=parent.builder_session_id, expected_construction_revision=4,
            purpose="player_character", visible_answer="Create my character",
            child_session_id="example-builder", references=captured,
        )
        context = owner.session_context(child.child_session_id)
        history = json.dumps(_provider_history(context))
        reader = reader_for_references(owner, captured)
        checker = _participant_draft_check_tool(
            "player_character", (declaration,), selected.sources.setting_character_schema,
        )
        for example in examples:
            assert example["path"] in history
            assert example["draft"]["authored_profile"] not in history
            draft = json.loads(reader.read(example["path"]))
            assert (
                hashlib.sha256(canonical_json_bytes(draft)).hexdigest() == example["content_sha256"]
            )
            fields = {**draft, "entity_state": json.dumps(draft["entity_state"]),
                      "setting_data": json.dumps(draft["setting_data"])}
            checked = checker.handler(fields, None)
            assert checked["success"] and checked["published"] is False
        assert owner.session_context(parent.builder_session_id) == before
        assert not (tmp_path / "participants").exists()
        original = canonical_json_bytes(captured)
        workspace = tmp_path / "custom-workspace"
        workspace.mkdir()
        example = next(
            e for e in examples if e["draft"]["participant"]["classes"][0]["id"] == "monk"
        )
        copied = builder_reference_copy_tool(reader, workspace).handler({
            "source_path": example["path"], "destination_path": "custom.json",
        }, None)
        assert copied["success"]
        (workspace / "custom.json").write_text('{"edited":true}')
        assert canonical_json_bytes(captured) == original
    with AuthorityOwner.open(database, "examples-reopen") as owner:
        from sylvia_gm.application.builder_references import reference_reader

        reader = reference_reader(owner, owner.session_context("example-builder"))
        assert json.loads(reader.read(example["path"])) == example["draft"]


def test_unselected_ruleset_has_no_implicit_example_sources(tmp_path):
    with AuthorityOwner.open(tmp_path / "empty.db", "empty") as owner:
        references = {"candidate_id": None, "packages": [], "plan": None}
        assert (
            with_character_examples(owner, tmp_path, references, purpose="player_character")
            == references
        )


def test_other_ruleset_does_not_receive_srd_examples(tmp_path):
    from tests.test_builder_library import install_character_packages

    with AuthorityOwner.open(tmp_path / "signal.db", "signal") as owner:
        candidate, setting_id = install_character_packages(owner)
        references = {"candidate_id": candidate.candidate_id, "plan": None, "packages": [
            {"id": entry.package_id, "version": entry.version, "content_sha256": entry.sha256,
             "prefix": "setting" if entry.package_id == setting_id else "ruleset"}
            for entry in candidate.package_lock.entries
        ]}
        captured = with_character_examples(owner, tmp_path, references, purpose="player_character")
        assert captured["character_examples"] == []
        assert not any(path.startswith("character-examples/")
                       for path in reader_for_references(owner, captured).paths)
