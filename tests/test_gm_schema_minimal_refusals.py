"""Schema-minimal calls cross real MCP/owner authority on separate starter saves."""

import json
import re
import shutil

import pytest
from mcp.client import Client

from sylvia_gm.agent._provider_session import (
    _derive_mechanics_parameters,
    _normalize_state_references,
)
from sylvia_gm.agent._session_loop import _mechanics_rejection_feedback
from sylvia_gm.agent._stateless_mcp import (
    StatelessMcpMechanics,
    _meta,
    build_stateless_mcp_server,
)
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_gm_memory_casting_hotfix import lite_save as lite_save


def minimal_value(name, schema, actor="hero", ordinal=0):
    """Satisfy structural schema requirements, leaving game eligibility to the owner."""
    for keyword in ("oneOf", "anyOf"):
        if keyword in schema:
            return minimal_value(name, schema[keyword][0], actor, ordinal)
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][min(ordinal, len(schema["enum"]) - 1)]
    kind = schema.get("type")
    if kind == "object":
        return {
            key: minimal_value(key, schema.get("properties", {}).get(key, {}), actor)
            for key in schema.get("required", [])
        }
    if kind == "array":
        return [
            minimal_value(name, schema.get("items", {}), actor, index)
            for index in range(schema.get("minItems", 0))
        ]
    if kind == "boolean":
        return True
    if kind in {"integer", "number"}:
        return max(schema.get("minimum", 1), schema.get("exclusiveMinimum", 0) + 1)
    if (
        schema.get("x-sylvia-gm-reference") in {"entity", "participant"}
        or name.endswith(("_state", "_states", "_actor_ids"))
        or name in {"caster", "actor", "target", "character", "combatants"}
    ):
        return actor if ordinal == 0 else f"unavailable-entity-{ordinal}"
    return "audit" + (str(ordinal) if ordinal else "")


@pytest.mark.asyncio
async def test_every_exposed_tool_has_explanatory_schema_minimal_feedback(lite_save, tmp_path):
    database = tmp_path / "isolated-campaigns.sqlite3"
    shutil.copyfile(lite_save, database)
    with AuthorityOwner.open(database, "hotfix-owner") as owner:
        owner.set_admin_full_audit(True)
        seed = owner.snapshot("casting")
        tools = await StatelessMcpMechanics(owner)._list_tools(
            "casting",
            purpose="schema_minimal_sweep",
            discovery_id="sweep",
            session_id="gm",
        )
        checked = []
        for index, tool in enumerate(tools):
            campaign, session = f"sweep-{index}", f"gm-{index}"
            owner.create_campaign(
                CampaignCreate(
                    campaign_id=campaign,
                    display_name="Schema-minimal synthetic call",
                    writer=WriterRole.DM,
                    package_lock=seed.package_lock,
                    turn_id="casting-turn",
                    participants=seed.participants,
                    initial_state=tuple(
                        StateValue(*key.rsplit(".", 1), value) for key, value in seed.state.items()
                    ),
                )
            )
            owner.create_agent_session(
                campaign_id=campaign,
                session_id=session,
                role=WriterRole.DM,
                provider_id="fixture",
                model_id="fixture",
            )
            owner.append_agent_message(
                session_id=session,
                message_id="sweep-basis",
                kind=SessionMessageKind.USER,
                content={"text": "Inspect the current synthetic scene."},
            )
            before = owner.snapshot(campaign)
            assert before.state == seed.state and before.participants == seed.participants
            schema = tool.input_schema
            arguments = {
                name: minimal_value(name, schema["properties"][name])
                for name in schema.get("required", [])
            }
            try:
                filled = _normalize_state_references(arguments, schema, before.state)
                filled = _derive_mechanics_parameters(
                    filled,
                    tool.parameter_derivations,
                    before.participants,
                )
            except MechanicsRejected as error:
                payload = _mechanics_rejection_feedback(error)
            else:
                meta = _meta(
                    {
                        "campaign_id": campaign,
                        "session_id": session,
                        "tool_name": tool.name,
                        "basis_message_id": "sweep-basis",
                        "update_id": f"sweep-{index}",
                        "turn_id": "casting-turn",
                        "action_id": f"sweep-{index}",
                        "idempotency_key": f"sweep-{index}",
                        "owner_id": "hotfix-owner",
                        "writer": "dm",
                        "expected_revision": before.revision,
                        "package_id": tool.package_id,
                        "package_version": tool.package_version,
                        "package_sha256": tool.package_sha256,
                        "initiating_session_id": session,
                    }
                )
                async with Client(
                    build_stateless_mcp_server(owner), raise_exceptions=True
                ) as client:
                    await client.list_tools(
                        meta=_meta({"campaign_id": campaign, "session_id": session}),
                        cache_mode="bypass",
                    )
                    result = await client.call_tool(tool.name, filled, meta=meta)
                payload = result.structured_content
            assert isinstance(payload, dict), (tool.name, payload)
            rendered = json.dumps(payload)
            assert not re.search(
                r"Missing required parameter|Parameter validation failed|Traceback", rendered
            ), (
                tool.name,
                payload,
            )
            body = payload.get("result", payload)
            if body.get("code") or payload.get("status") in {"rejected", "failed"}:
                assert isinstance(body.get("reason"), str) and body["reason"].strip(), (
                    tool.name,
                    payload,
                )
                after = owner.snapshot(campaign)
                assert after.state == before.state and after.participants == before.participants
                if body.get("code") == "tool_execution_failed":
                    # Failed actions record their safe failure/audit event. A refusal
                    # never admits an action or changes the campaign revision.
                    assert after.revision == before.revision + 1
                    defects = [
                        owner.admin_full_audit_record(i)["trace"]
                        for i in range(owner.admin_full_audit_status()["trace_count"])
                    ]
                    assert any(
                        row.get("kind") == "ruleset_execution_defect"
                        and row.get("tool_name") == tool.name
                        and (row.get("error") or row.get("cause"))
                        for row in defects
                    ), tool.name
                else:
                    assert after.revision == before.revision
            checked.append(tool.name)
        assert len(checked) == len(tools) and len(checked) == len(set(checked))
        print(f"Schema-minimal MCP/owner calls: {len(checked)}; all feedback checks passed.")
