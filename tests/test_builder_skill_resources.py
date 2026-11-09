"""Packaged Builder resources stay usable through the advertised retrieval tools."""

from pathlib import Path

from sylvia_gm.agent import _skills
from sylvia_gm.agent._builder_session import _builder_skill_loader_tool
from sylvia_gm.agent._content_definition_tool import content_definition_tool
from sylvia_gm.domain._content_schema_runtime import PACKAGE_RUNTIME_SCHEMA


def test_ruleset_skill_points_to_the_loadable_lazy_executable_definition():
    skill = _builder_skill_loader_tool().handler({"name": "BUILD RULESET"}, None)
    assert skill["success"] is True
    text = skill["instructions"]
    assert "name: package_runtime" in text
    assert "definition.procedural_definition.executable" in text
    assert "section: /procedural_definition/executable" in text
    assert "`ruleset_source` for the compact authoring container" in text
    assert "check_ruleset_draft" in text
    tool = content_definition_tool()
    catalog = tool.handler({}, None)
    assert len(catalog["definitions"]) == 24
    assert "definition" not in catalog
    full = tool.handler({"name": "package_runtime"}, None)
    focused = tool.handler(
        {"name": "package_runtime", "section": "/procedural_definition/executable"}, None
    )
    executable = full["definition"]["procedural_definition"]["executable"]
    assert focused["success"] is True
    assert focused["value"] == executable
    assert executable["effects"]["body_fields"]["call"] == ["call", "name", "params"]
    assert "len" in executable["expressions"]["functions"]
    assert (
        "runtime loader, trigger links, static call graph and manifest tool-schema agreement"
        in (full["definition"]["procedural_definition"]["validation"])
    )
    assert "New package admission closes effect fields" in executable["validation_scope"][0]
    assert (
        "procedural_definition" not in tool.handler({"name": "ruleset_source"}, None)["definition"]
    )
    compact = tool.handler({"name": "ruleset_source"}, None)["definition"]
    procedures = compact["schema"]["properties"]["procedures"]
    assert procedures == PACKAGE_RUNTIME_SCHEMA["properties"]["procedures"]
    assert procedures is not PACKAGE_RUNTIME_SCHEMA["properties"]["procedures"]
    assert any("copied unchanged" in note for note in compact["requirements"])


def test_skill_loader_rejects_missing_packaged_workflow(monkeypatch, tmp_path):
    monkeypatch.setattr(_skills, "__file__", str(tmp_path / "_skills.py"))
    assert _builder_skill_loader_tool().handler({"name": "BUILD CAMPAIGN PLAN"}, None) == {
        "success": False,
        "status": "rejected",
        "code": "skill_reference_unavailable",
    }


def test_skill_loader_rejects_a_reference_that_disappears_after_catalog_read(monkeypatch):
    real_read = Path.read_text

    def missing_reference(path, *args, **kwargs):
        if path.name == "worked-examples.md":
            raise FileNotFoundError(path)
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", missing_reference)
    result = _builder_skill_loader_tool().handler(
        {"name": "BUILD CAMPAIGN PLAN", "reference": "references/worked-examples.md"}, None
    )
    assert result == {
        "success": False,
        "status": "rejected",
        "code": "skill_reference_unavailable",
    }
