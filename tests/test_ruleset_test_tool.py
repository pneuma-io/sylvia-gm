"""Builder runs the current editable draft without changing source or live state."""

import asyncio
from copy import deepcopy

import pytest
import yaml

from sylvia_gm.agent._builder_session import _ruleset_candidate_tool
from sylvia_gm.agent._mode import AgentMode
from sylvia_gm.agent._ruleset_test_provider import RulesetTestProvider
from sylvia_gm.agent._ruleset_test_tool import ruleset_test_tool
from sylvia_gm.agent._runner import AgentRunner
from sylvia_gm.domain._content_schema_ruleset import EXAMPLE as SOURCE
from sylvia_gm.domain._content_schema_ruleset_tests import EXAMPLE


def test_builder_runs_current_draft_and_invalidates_success_after_edits(tmp_path):
    draft = tmp_path / "ruleset-draft"
    draft.mkdir()
    path = draft / "ruleset-source.yaml"
    source = deepcopy(SOURCE)
    source["tests"] = deepcopy(EXAMPLE)
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    before = path.read_bytes()
    tool = ruleset_test_tool(tmp_path, cancelled=lambda: False)
    passed = tool.handler({}, None)
    assert passed["success"] is True, passed
    assert passed["summary"] == {"passed": 1, "total": 1, "failed": 0}
    assert path.read_bytes() == before
    source["tests"]["scenarios"][0]["steps"][0]["expect"][0]["equals"] = "rejected"
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    revised = path.read_bytes()
    failed = tool.handler({}, None)
    assert failed["success"] is False
    assert failed["status"] == "failed"
    assert failed["closure_sha256"] != passed["closure_sha256"]
    assert path.read_bytes() == revised
    assert list(tmp_path.iterdir()) == [draft]


@pytest.mark.asyncio
async def test_other_session_work_can_cancel_a_running_sandbox(tmp_path):
    draft = tmp_path / "ruleset-draft"
    draft.mkdir()
    source = deepcopy(SOURCE)
    source["tests"] = deepcopy(EXAMPLE)
    (draft / "ruleset-source.yaml").write_text(yaml.safe_dump(source), encoding="utf-8")
    stop = asyncio.Event()
    runner = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM,
        provider=RulesetTestProvider([], decision_owner=lambda: "none"),
        history_path=None,
        project_root=tmp_path,
        builder_application_tools=(ruleset_test_tool(tmp_path, cancelled=stop.is_set),),
    )
    timer = asyncio.get_running_loop().call_later(0.1, stop.set)
    try:
        result = await runner._execute_tool("test_ruleset_draft", {})
    finally:
        timer.cancel()
    assert result["status"] == "cancelled", result
    assert result["success"] is False


def test_publication_rechecks_exact_candidate_and_preserves_failed_revision(tmp_path):
    draft = tmp_path / "ruleset-draft"
    draft.mkdir()
    path = draft / "ruleset-source.yaml"
    source = deepcopy(SOURCE)
    published = []

    def publish(archive, **identity):
        published.append((archive.read(), identity))
        return {"status": "published"}

    tool = _ruleset_candidate_tool(
        workspace=tmp_path, session_id="test", publisher=publish,
    )
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    missing = tool.handler({}, None)
    assert missing["tests"]["status"] == "missing_tests"
    assert not published
    source["tests"] = deepcopy(EXAMPLE)
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    passed = tool.handler({}, None)
    assert passed["success"] is True, passed
    assert len(published) == 1
    assert published[0][1]["expected_closure_sha256"] == passed["tests"]["closure_sha256"]
    source["tests"]["scenarios"][0]["steps"][0]["expect"][1]["equals"] = 1
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    before = path.read_bytes()
    failed = tool.handler({}, None)
    assert failed["success"] is False
    assert failed["tests"]["status"] == "failed"
    assert len(published) == 1
    assert path.read_bytes() == before
