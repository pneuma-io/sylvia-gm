"""Exercise competing chats through the real guarded Builder file tools."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from sylvia_gm.agent._mode import AgentMode
from sylvia_gm.agent._runner import AgentRunner
from sylvia_gm.provider._base import FakeProvider


def builder(workspace: Path) -> AgentRunner:
    return AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM,
        provider=FakeProvider(responses=[]),
        history_path=None,
        project_root=workspace,
        ruleset_name="none",
    )


def test_older_chat_must_reconcile_before_replacing_or_deleting(tmp_path: Path) -> None:
    first, second = builder(tmp_path), builder(tmp_path)
    path = tmp_path / "shared.md"
    path.write_bytes(b"Original\r\nSecond line\r\n")
    observed = first._tool_read_file({"path": "shared.md", "limit": 1})
    assert observed["success"]
    assert observed["lines"] == 1
    assert observed["total_lines"] == 2
    assert (
        second._tool_write_file({"path": "shared.md", "content": "Unobserved"})["error_code"]
        == "stale_file"
    )
    other_observation = second._tool_read_file({"path": "shared.md"})
    saved = second._tool_write_file(
        {
            "path": "shared.md",
            "content": "Second writer's change\n",
            "expected_version": other_observation["version"],
        }
    )
    assert saved["success"]
    stale_request = {"path": "shared.md", "expected_version": observed["version"]}
    stale_save = first._tool_write_file({**stale_request, "content": "Obsolete replacement"})
    stale_delete = first._tool_delete_file(stale_request)
    assert stale_save["error_code"] == stale_delete["error_code"] == "stale_file"
    assert "read_file" in stale_save["error"]
    assert path.read_text() == "Second writer's change\n"

    # A new runner (as after a restart) can use the latest returned version without shared memory.
    resumed = builder(tmp_path)
    latest = resumed._tool_read_file({"path": "shared.md"})
    reconciled = resumed._tool_write_file(
        {
            "path": "shared.md",
            "content": "Second writer's change\nCompatible addition\n",
            "expected_version": latest["version"],
        }
    )
    assert reconciled["success"]
    assert resumed._tool_delete_file(
        {"path": "shared.md", "expected_version": reconciled["version"]}
    )["success"]
    assert not path.exists()
    assert (
        second._tool_write_file(
            {"path": "shared.md", "content": "Bring it back", "expected_version": saved["version"]}
        )["error_code"]
        == "stale_file"
    )
    assert not path.exists()


@pytest.mark.parametrize("initial", [None, "Existing content"])
def test_competing_saves_and_creations_have_one_winner(tmp_path: Path, initial: str | None) -> None:
    first, second = builder(tmp_path), builder(tmp_path)
    path = tmp_path / "new.md"
    if initial is not None:
        path.write_text(initial)
    observations = [chat._tool_read_file({"path": "new.md"}) for chat in (first, second)]
    ready = Barrier(2)

    def save(index: int) -> dict:
        ready.wait()
        return (first, second)[index]._tool_write_file(
            {
                "path": "new.md",
                "content": f"Writer {index}",
                "expected_version": observations[index]["version"],
            }
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, (0, 1)))
    assert sum(result["success"] for result in results) == 1
    winner = next(index for index, result in enumerate(results) if result["success"])
    assert path.read_text() == f"Writer {winner}"
    assert results[1 - winner]["error_code"] == "stale_file"
    assert list(tmp_path.glob(".sylvia-builder-write-*")) == []


def test_builder_surgically_edits_one_literal_without_changing_other_bytes(tmp_path: Path) -> None:
    runner = builder(tmp_path)
    path = tmp_path / "plan.json"
    original = b'{\r\n  "companion": {"path": "characters/sera.json"},\r\n  "kept": true\r\n}\r\n'
    path.write_bytes(original)
    observed = runner._tool_read_file({"path": "plan.json"})

    result = runner._tool_edit_file(
        {
            "path": "plan.json",
            "old_text": '"companion": {"path": "characters/sera.json"}',
            "new_text": '"companion": {"id": "sera", "path": "characters/sera.json"}',
            "expected_version": observed["version"],
        }
    )

    assert result["success"] is True
    assert result["path"] == "plan.json"
    assert result["version"] != observed["version"]
    assert path.read_bytes() == original.replace(
        b'"companion": {"path": "characters/sera.json"}',
        b'"companion": {"id": "sera", "path": "characters/sera.json"}',
    )


@pytest.mark.parametrize(
    ("content", "old_text"), [("same value\nsame value\n", "same value"), ("aaa", "aa")]
)
def test_builder_edit_rejects_stale_and_non_unique_targets(
    tmp_path: Path,
    content: str,
    old_text: str,
) -> None:
    first, second = builder(tmp_path), builder(tmp_path)
    path = tmp_path / "plan.json"
    path.write_text(content, encoding="utf-8")
    observed = first._tool_read_file({"path": "plan.json"})

    ambiguous = first._tool_edit_file(
        {
            "path": "plan.json",
            "old_text": old_text,
            "new_text": "corrected",
            "expected_version": observed["version"],
        }
    )
    assert ambiguous["error_code"] == "text_not_unique"
    assert path.read_text(encoding="utf-8") == content

    missing = first._tool_edit_file(
        {
            "path": "plan.json",
            "old_text": "absent value",
            "new_text": "corrected",
            "expected_version": observed["version"],
        }
    )
    assert missing["error_code"] == "text_not_found"
    assert path.read_text(encoding="utf-8") == content

    current = second._tool_read_file({"path": "plan.json"})
    second._tool_write_file(
        {"path": "plan.json", "content": "other change\n", "expected_version": current["version"]}
    )
    stale = first._tool_edit_file(
        {
            "path": "plan.json",
            "old_text": old_text,
            "new_text": "corrected",
            "expected_version": observed["version"],
        }
    )
    assert stale["error_code"] == "stale_file"
    assert path.read_text(encoding="utf-8") == "other change\n"
