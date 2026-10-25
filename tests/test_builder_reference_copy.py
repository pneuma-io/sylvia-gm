"""Complete reference copies share the guarded workspace mutation boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sylvia_gm.agent._builder_reference_copy_tool import builder_reference_copy_tool
from sylvia_gm.application.builder_references import BuilderReferences
from sylvia_gm.builder._workspace_files import content_version
from sylvia_gm.tools._builder_handlers import tool_edit_file


def test_full_reference_copy_preserves_large_source_and_rejects_stale_replacement(tmp_path: Path):
    source = json.dumps({"story": "A saved scene. " * 4000, "required_companion": "medic"})
    references = BuilderReferences(("campaign-plan/plan.json",), lambda _path: source)
    tool = builder_reference_copy_tool(references, tmp_path)
    args = {
        "source_path": "campaign-plan/plan.json",
        "destination_path": "campaign-plan-draft/plan.json",
    }
    copied = tool.handler(args, None)
    target = tmp_path / args["destination_path"]
    assert copied["success"] is True
    assert target.read_bytes() == source.encode("utf-8")
    assert copied["bytes_written"] > 32000
    assert "content" not in copied

    assert tool.handler(args, None)["error_code"] == "stale_file"
    target.write_text("A newer local edit", encoding="utf-8")
    assert (
        tool.handler({**args, "expected_version": copied["version"]}, None)["error_code"]
        == "stale_file"
    )
    assert target.read_text() == "A newer local edit"
    observed = content_version(target.read_bytes())
    assert tool.handler({**args, "expected_version": observed}, None)["success"] is True
    assert target.read_bytes() == source.encode("utf-8")
    assert references.read(args["source_path"]) == source


@pytest.mark.parametrize("destination", ["../outside.json", ".env", ".builder-sources/source.json"])
def test_reference_copy_enforces_existing_workspace_guards(tmp_path: Path, destination: str):
    reads = []
    references = BuilderReferences(
        ("setting/source.json",), lambda path: reads.append(path) or "{}"
    )
    result = builder_reference_copy_tool(references, tmp_path).handler(
        {
            "source_path": "setting/source.json",
            "destination_path": destination,
        },
        None,
    )
    assert result == {"success": False, "code": "reference_copy_path_denied"}
    assert reads == []
    assert list(tmp_path.iterdir()) == []


def test_reference_copy_cannot_read_an_unlisted_source(tmp_path: Path):
    reads = []
    references = BuilderReferences((), lambda path: reads.append(path) or "{}")
    result = builder_reference_copy_tool(references, tmp_path).handler(
        {
            "source_path": "unrelated/private.json",
            "destination_path": "copy.json",
        },
        None,
    )
    assert result["success"] is False
    assert reads == []
    assert not (tmp_path / "copy.json").exists()


def test_known_complete_source_can_be_edited_with_copy_version_without_reread(tmp_path: Path):
    source = '{"name":"Fictional monk","level":1}\n'
    references = BuilderReferences(("character-profile/profile.json",), lambda _path: source)
    tool = builder_reference_copy_tool(references, tmp_path)
    args = {"source_path": "character-profile/profile.json", "destination_path": "character.json"}
    copied = tool.handler(args, None)
    target = tmp_path / "character.json"
    edit = tool_edit_file({
        "_resolved_path": target, "old_text": "Fictional monk", "new_text": "Test monk",
        "expected_version": copied["version"],
    }, None)
    assert edit["success"] is True
    assert target.read_text() == '{"name":"Test monk","level":1}\n'
    before = target.read_bytes()
    stale = tool_edit_file({
        "_resolved_path": target, "old_text": "Test monk", "new_text": "Stale overwrite",
        "expected_version": copied["version"],
    }, None)
    assert stale["error_code"] == "stale_file"
    assert tool.handler(args, None)["error_code"] == "stale_file"
    assert target.read_bytes() == before
    assert references.read(args["source_path"]) == source
