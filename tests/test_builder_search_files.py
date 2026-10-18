from pathlib import Path

from sylvia_gm.tools._builder_handlers import tool_search_files


def test_search_file_content_uses_the_selected_file(tmp_path: Path) -> None:
    repair = tmp_path / "repair-draft"
    repair.mkdir()
    plan = repair / "plan.json"
    plan.write_text('{"id":"intruder_alpha","revision":4}\n', encoding="utf-8")

    result = tool_search_files({
        "_project_root": tmp_path,
        "_resolved_path": plan,
        "pattern": "intruder_alpha|revision",
        "target": "content",
    }, None)

    assert result == {
        "success": True,
        "matches": [{
            "file": str(Path("repair-draft") / "plan.json"),
            "line": 1,
            "content": '{"id":"intruder_alpha","revision":4}',
            "content_complete": True,
        }],
        "count": 1,
    }


def test_search_rejects_a_missing_selected_path(tmp_path: Path) -> None:
    missing = tmp_path / "repair-draft" / "missing.json"

    result = tool_search_files({
        "_project_root": tmp_path,
        "_resolved_path": missing,
        "pattern": "revision",
        "target": "content",
    }, None)

    assert result["success"] is False
    assert "not found" in result["error"].lower()


def test_search_directory_content_keeps_relative_results(tmp_path: Path) -> None:
    repair = tmp_path / "repair-draft"
    nested = repair / "nested"
    nested.mkdir(parents=True)
    (nested / "plan.json").write_text("revision one\n", encoding="utf-8")

    result = tool_search_files({
        "_project_root": tmp_path,
        "_resolved_path": repair,
        "pattern": "revision",
        "target": "content",
    }, None)

    assert result["success"] is True
    assert result["count"] == 1
    assert result["matches"][0]["file"] == str(Path("nested") / "plan.json")


def test_long_search_line_is_an_explicit_excerpt_without_changing_source(tmp_path: Path) -> None:
    source = tmp_path / "long.txt"
    text = "target " + "🦊" * 300
    source.write_text(text, encoding="utf-8")
    result = tool_search_files({
        "_project_root": tmp_path, "_resolved_path": source,
        "pattern": "target", "target": "content",
    }, None)
    assert result["matches"][0]["content"] == text[:200]
    assert result["matches"][0]["content_complete"] is False
    assert source.read_text(encoding="utf-8") == text
