"""The draft helper preserves explicit endings instead of inventing them from a premise."""

import json

import pytest

from sylvia_gm.agent._builder_session import CampaignPlanToolAuthority, _campaign_plan_tools
from sylvia_gm.domain.campaign_plan import PackageBinding


def draft_tool(workspace, *, name="create_campaign_plan_draft", required=False):
    authority = CampaignPlanToolAuthority(
        ruleset=PackageBinding("example.rules", "1.0.0", "1" * 64),
        setting=PackageBinding("example.world", "1.0.0", "2" * 64),
        closure_sha256="3" * 64,
        setting_paths=frozenset({"harbor.md"}),
        read_setting=lambda path: "# Harbor",
        state_schemas=({
            "key": "state", "required": True, "references": [],
            "schema": {"type": "object", "properties": {
                "policy": {"type": "string", "minLength": 1},
            }, "required": ["policy"]},
        },) if required else (),
    )
    return next(
        t for t in _campaign_plan_tools(workspace, authority)
        if t.name == name
    )


def concept():
    return {
        "title": "Harbor", "premise": "A disputed cargo.", "tone": "Hopeful",
        "player_character_mode": "compatible", "starting_location_path": "harbor.md",
        "custom_companion_minimum": 0, "custom_companion_maximum": 0,
        "opening_situation": "The keeper requests help.",
        "primary_objective": "Find a foothold in the harbor.",
    }


def test_draft_preserves_authored_endings_in_structured_and_readable_copies(tmp_path):
    conditions = [
        "The cargo is delivered, handed over or abandoned with its loss recorded.",
        "The keeper has responded to that outcome; no second job is required.",
    ]
    result = draft_tool(tmp_path).handler({**concept(), "completion_conditions": conditions}, None)
    assert result["status"] == "draft_ready"
    plan = json.loads((tmp_path / result["plan_path"]).read_text(encoding="utf-8"))
    assert plan["objectives"][0]["completion_conditions"] == conditions
    assert plan["completion_conditions"] == conditions
    readable = (tmp_path / result["readable_path"]).read_text(encoding="utf-8")
    assert all(condition in readable for condition in conditions)


@pytest.mark.parametrize("conditions", [None, [], ["  "], [1], "The work is done."])
def test_draft_requires_nonempty_authored_endings(tmp_path, conditions):
    params = concept()
    if conditions is not None:
        params["completion_conditions"] = conditions
    result = draft_tool(tmp_path).handler(params, None)
    assert result["success"] is False
    assert not (tmp_path / "campaign-plan-draft").exists()


def test_incomplete_required_policy_can_be_drafted_but_not_published(tmp_path):
    result = draft_tool(tmp_path, required=True).handler({
        **concept(), "completion_conditions": ["The cargo dispute is resolved."],
    }, None)
    assert result["status"] == "draft_ready"
    publish = draft_tool(tmp_path, name="publish_campaign_plan", required=True)
    rejected = publish.handler({}, None)
    assert rejected["success"] is False
    assert rejected["validation_errors"][0]["path"] == "$.initial_state.mechanics"
    assert not (tmp_path / "campaign-plan.json").exists()
    path = tmp_path / result["plan_path"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["initial_state"]["mechanics"] = [{
        "namespace": "example.rules", "key": "state", "value": {"policy": "Fixed level."},
    }]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert publish.handler({}, None)["status"] == "ready"
    assert (tmp_path / "campaign-plan.json").exists()
