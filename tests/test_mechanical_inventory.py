"""Ruleset mechanics own inventory without a second ledger or bookkeeping bypass."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
import yaml

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.domain.authority import StateValue, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_inventory import holding
from tests.test_panel_starting_layout import compile_layout, starting_layout

PACKAGE = "example.panel-rules"
MARKER = "x-sylvia-gm-inventory"


@pytest.fixture
def draft():
    """One small real mechanic and panel share the same committed supply count."""
    compiled = compile_layout(starting_layout())
    record = deepcopy(compiled.draft.record)
    record["tools"] = [{
        "name": "use_supply", "capability": f"{PACKAGE}.presentation",
        "input_schema": {
            "type": "object", "properties": {}, "required": [], "additionalProperties": False,
            MARKER: {"format": "mechanics", "state": {"namespace": PACKAGE, "key": "state"},
                     "surface_id": "inventory", "tools": ["use_supply"]},
        },
        "writers": ["dm"], "turn_statuses": ["open"], "state_predicates": [],
    }]
    next(p for p in record["ui_surfaces"] if p["id"] == "inventory")["source"]["key"] = "state"
    runtime = yaml.safe_load(compiled.draft.payloads["runtime/ruleset.yaml"])
    runtime["tools"]["use_supply"] = {
        "description": "Consume one real supply.",
        "parameters": [{"name": "supplies", "type": "integer", "from_state": "state.supplies"}],
        "pre_conditions": [{"condition": "supplies > 0", "error": "No supplies remain."}],
        "effects": [{"decrement": {"field": "state.supplies", "amount": 1}}],
        "narrative": {},
    }
    declarations = json.loads(compiled.draft.payloads["content/content-declarations.json"])
    declarations["inclusions"][0]["links"]["tools"] = ["use_supply"]
    return replace(compiled.draft, record=record, payloads={
        **compiled.draft.payloads, "runtime/ruleset.yaml": yaml.safe_dump(runtime).encode(),
        "content/content-declarations.json": canonical_json_bytes(declarations),
    })


@pytest.mark.parametrize("fault", [
    "foreign_state", "extra_state_field", "blank_state_key", "empty_tools", "many_tools",
    "string_tools", "invalid_tool", "duplicate_tools", "extra_field", "bookkeeping",
    "missing_tool", "missing_surface", "wrong_writer", "missing_declaring_tool", "ambiguous",
])
def test_invalid_inventory_declarations_fail_normal_package_admission(draft, fault):
    tool = draft.record["tools"][0]
    binding = tool["input_schema"][MARKER]
    if fault == "foreign_state":
        binding["state"]["namespace"] = "someone.else"
    elif fault == "extra_state_field":
        binding["state"]["path"] = "supplies"
    elif fault == "blank_state_key":
        binding["state"]["key"] = " "
    elif fault == "empty_tools":
        binding["tools"] = []
    elif fault == "many_tools":
        binding["tools"] = [f"tool-{i}" for i in range(33)]
    elif fault == "string_tools":
        binding["tools"] = "use_supply"
    elif fault == "invalid_tool":
        binding["tools"] = [{"name": "use_supply"}]
    elif fault == "duplicate_tools":
        binding["tools"] *= 2
    elif fault == "extra_field":
        binding["field"] = "supplies"
    elif fault == "bookkeeping":
        tool["input_schema"]["x-sylvia-gm-character-update"] = binding["state"]
    elif fault == "missing_tool":
        binding["tools"].append("not_declared")
    elif fault == "missing_surface":
        binding["surface_id"] = "not_declared"
    elif fault == "wrong_writer":
        tool["writers"] = ["builder"]
    else:
        other = deepcopy(tool)
        other["name"] = "other_tool"
        if fault == "missing_declaring_tool":
            other["input_schema"].pop(MARKER)
            binding["tools"] = ["other_tool"]
        draft.record["tools"].append(other)
    with pytest.raises(ValueError):
        materialize_package_archive((draft,))


def test_unavailable_mechanic_does_not_reactivate_fallback_inventory(tmp_path, draft):
    draft.record["tools"][0]["state_predicates"] = [
        {"namespace": PACKAGE, "key": "can_use_supply", "equals": True},
    ]
    with AuthorityOwner.open(tmp_path / "unavailable.db", "inventory-owner") as owner:
        with materialize_package_archive((draft,)) as artifact:
            candidate = owner.install_package_archive(artifact.archive, install_id="rules")
        owner.create_campaign(CampaignCreate(
            campaign_id="inventory", display_name="Unavailable mechanic", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="play",
            initial_state=(StateValue(PACKAGE, "can_use_supply", False),),
        ))
        assert owner.catalog("inventory") == ()
        reference = json.loads(owner.read_campaign_reference(
            "inventory", source="campaign_state", path="inventory",
        )["content"])
        assert reference["mode"] == "ruleset"
        assert not any(p["renderer"] == "generic_inventory"
                       for p in owner.campaign_panels("inventory")["surfaces"])


def test_shared_inventory_rejects_fallback_and_preserves_mechanics_after_reopen(tmp_path, draft):
    database = tmp_path / "inventory.db"
    with AuthorityOwner.open(database, "inventory-owner") as owner:
        with materialize_package_archive((draft,)) as artifact:
            candidate = owner.install_package_archive(artifact.archive, install_id="rules")
        owner.create_campaign(CampaignCreate(
            campaign_id="inventory", display_name="Mechanical inventory", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="play",
            initial_state=(StateValue(PACKAGE, "state", {"supplies": 3}),),
        ))
        session = owner.create_agent_session(
            campaign_id="inventory", role=WriterRole.DM,
            provider_id="scripted", model_id="scripted",
        )
        owner.append_agent_message(
            session_id=session.session_id, message_id="action", kind=SessionMessageKind.USER,
            content={"text": "Use a supply."},
        )
        mcp = StatelessMcpMechanics(owner)

        def reference():
            return json.loads(owner.read_campaign_reference(
                "inventory", source="campaign_state", path="inventory",
            )["content"])

        shared = reference()
        assert shared["mode"] == "ruleset" and shared["holdings"] is None
        assert shared["update_tools"] == ["use_supply"]
        assert shared["ruleset_inventory"]["state"] == {"namespace": PACKAGE, "key": "state"}
        options = owner.campaign_narrative_options(session.session_id)
        assert "update_inventory" not in options["operations"]
        assert "update_character" not in options["operations"]
        before = owner.snapshot("inventory")
        with pytest.raises(MechanicsRejected):
            mcp.commit_campaign_file(
                "inventory", session_id=session.session_id, basis_message_id="action",
                parameters={
                    "state_change": "changed", "state_change_summary": "An invalid ledger write.",
                    "facts": [{"fact_id": "uncommitted", "category": "continuity",
                               "text": "Must not survive rejection.", "visibility": "player_known",
                               "known_by": ["player"]}],
                    "inventory_updates": [{"expected_revision": before.revision,
                                           "holdings": [holding()]}],
                },
            )
        assert owner.snapshot("inventory") == before
        command = ActionCommand(
            campaign_id="inventory", action_id="consume", idempotency_key="consume",
            owner_id="inventory-owner", writer=WriterRole.DM, expected_revision=before.revision,
            turn_id="play", package_id=PACKAGE, tool_name="use_supply", parameters={},
        )
        receipt = mcp.execute_action(command)
        assert receipt.status.value == "committed"
        assert mcp.execute_action(command) == receipt
        after = owner.snapshot("inventory")
        assert after.state[f"{PACKAGE}.state"]["supplies"] == 2
        projection = owner.campaign_panels("inventory")
        assert not any(p["renderer"] == "generic_inventory" for p in projection["surfaces"])
        panel = next(p for p in projection["surfaces"] if p["id"] == "inventory")
        assert panel["data"]["supplies"] == 2
        assert reference()["campaign_revision"] == after.revision
    with AuthorityOwner.open(database, "inventory-reopen") as owner:
        assert owner.snapshot("inventory") == after
        assert owner.campaign_panels("inventory") == projection
        reopened = json.loads(owner.read_campaign_reference(
            "inventory", source="campaign_state", path="inventory",
        )["content"])
        assert reopened["update_tools"] == ["use_supply"]
