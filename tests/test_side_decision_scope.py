"""Side-scoped Ruleset decision ownership and eligibility conformance."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import cast

import pytest
import yaml

from sylvia_gm.agent._provider_session import _gm_decision_control_message
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import (
    ActionStatus,
    JsonValue,
    ModelDecisionActor,
    ModelDecisionBoundary,
    ModelDecisionOwner,
    StateValue,
    WriterRole,
    owned_payload,
)
from sylvia_gm.domain.errors import StaleRevision
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record
from sylvia_gm.runtime._schema_validator import validate_ruleset

PACKAGE_ID = "sylvia-gm.conformance.side-scope"


def _runtime() -> bytes:
    document = yaml.safe_dump(
        {
            "sylvia_gm_runtime": {
                "format": 1,
                "state_namespace": PACKAGE_ID,
            },
            "model_decision_scope": {
                "decision_owner": {"namespace": PACKAGE_ID, "key": "state.decision_owner"},
                "active_side": {"namespace": PACKAGE_ID, "key": "state.active_side"},
                "eligible_actors": {
                    "namespace": PACKAGE_ID,
                    "key": "state.eligible_actor_ids",
                },
                "focused_actor": {"namespace": PACKAGE_ID, "key": "state.focused_actor"},
                "actor_entities": {"namespace": PACKAGE_ID, "key": "state.entities"},
            },
            "tools": {
                "act_alpha_first": {
                    "description": "Spend Alpha's first AP.",
                    "parameters": [],
                    "effects": [
                        {"decrement": {"field": "state.entities.alpha.ap", "amount": 1}},
                        {
                            "set": {
                                "field": "state.focused_actor",
                                "value": "'alpha'",
                            }
                        },
                    ],
                    "metadata": {"costs_action": True},
                },
                "act_bravo": {
                    "description": "Spend Bravo's AP.",
                    "parameters": [],
                    "effects": [
                        {"decrement": {"field": "state.entities.bravo.ap", "amount": 1}},
                        {
                            "set": {
                                "field": "state.eligible_actor_ids",
                                "value": "definitions.alpha_only",
                            }
                        },
                        {"set": {"field": "state.focused_actor", "value": "'alpha'"}},
                    ],
                    "metadata": {"costs_action": True},
                },
                "act_alpha_finish": {
                    "description": "Spend Alpha's final AP.",
                    "parameters": [],
                    "effects": [
                        {"decrement": {"field": "state.entities.alpha.ap", "amount": 1}},
                        {
                            "set": {
                                "field": "state.eligible_actor_ids",
                                "value": "definitions.no_actors",
                            }
                        },
                        {"set": {"field": "state.focused_actor", "value": "null"}},
                    ],
                    "metadata": {"costs_action": True},
                },
                "end_side": {
                    "description": "End the active package-owned side decision.",
                    "parameters": [],
                    "effects": [
                        {"set": {"field": "state.active_side", "value": "null"}},
                        {"set": {"field": "state.focused_actor", "value": "null"}},
                    ],
                    "metadata": {"costs_action": True},
                },
            },
            "procedures": {},
            "definitions": {"alpha_only": ["alpha"], "no_actors": []},
            "narrative": {},
        },
        sort_keys=False,
    )
    return str(document).encode()


def _tool(name: str, *predicates: tuple[str, object]) -> dict[str, object]:
    return {
        "name": name,
        "capability": "side.decide",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "state_predicates": [
            {"namespace": PACKAGE_ID, "key": f"state.{key}", "equals": value}
            for key, value in predicates
        ],
        "turn_statuses": ["open"],
        "writers": ["dm"],
    }


def _archive() -> bytes:
    runtime = _runtime()
    record: dict[str, object] = {
        "id": PACKAGE_ID,
        "version": "1.0.0",
        "role": "primary",
        "metadata": {"label": "Side scope"},
        "provenance": {"publisher": "tests", "source_sha256": "a" * 64},
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": ["side.decide"],
        "tools": [
            _tool("act_alpha_first", ("active_side", "hostiles"), ("entities.alpha.ap", 2)),
            _tool(
                "act_bravo",
                ("active_side", "hostiles"),
                ("entities.alpha.ap", 1),
                ("entities.bravo.ap", 1),
            ),
            _tool(
                "act_alpha_finish",
                ("active_side", "hostiles"),
                ("entities.alpha.ap", 1),
                ("entities.bravo.ap", 0),
            ),
            _tool("end_side", ("active_side", "hostiles"), ("eligible_actor_ids", [])),
        ],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [
            {
                "path": "runtime/ruleset.yaml",
                "bytes": len(runtime),
                "sha256": hashlib.sha256(runtime).hexdigest(),
            }
        ],
    }
    record["content_sha256"] = content_root_for_record(record)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical_json_bytes({"format": 1, "packages": [record]}))
        archive.writestr(f"packages/{PACKAGE_ID}/runtime/ruleset.yaml", runtime)
    return output.getvalue()


def _state(owner: str = "gm") -> dict[str, object]:
    return {
        "decision_owner": owner,
        "active_side": "hostiles",
        "eligible_actor_ids": ["alpha", "bravo"],
        "focused_actor": None,
        "entities": {
            "alpha": {"name": "Alpha", "ap": 2, "private_note": "model-safe"},
            "bravo": {"name": "Bravo", "ap": 1, "private_note": "model-safe"},
            "charlie": {
                "name": "Charlie",
                "ap": 2,
                "incapacitated": True,
                "private_note": "not eligible",
            },
        },
    }


def _command(
    *,
    owner_id: str,
    campaign_id: str,
    action_id: str,
    expected_revision: int,
    tool_name: str,
    parameters: dict[str, object],
) -> ActionCommand:
    return ActionCommand(
        campaign_id=campaign_id,
        action_id=action_id,
        idempotency_key=action_id,
        owner_id=owner_id,
        writer=WriterRole.DM,
        expected_revision=expected_revision,
        turn_id="turn-side-scope",
        package_id=PACKAGE_ID,
        tool_name=tool_name,
        parameters=owned_payload(parameters),
    )


def test_runtime_admits_new_scope_and_preserves_legacy_boundary_form() -> None:
    current = yaml.safe_load(_runtime())
    assert validate_ruleset(yaml.safe_dump(current, sort_keys=False)) == []

    current.pop("model_decision_scope")
    current["model_decision_boundary"] = {
        "active_actor": {"namespace": PACKAGE_ID, "key": "state.focused_actor"},
        "actor_entities": {"namespace": PACKAGE_ID, "key": "state.entities"},
        "participant_control": {"field": "control_owner", "player_values": ["player"]},
        "default_owner": "gm",
    }
    assert validate_ruleset(yaml.safe_dump(current, sort_keys=False)) == []


def test_gm_control_message_contains_complete_eligible_actor_set() -> None:
    boundary = ModelDecisionBoundary(
        owner=ModelDecisionOwner.GM,
        actor_id="alpha",
        actor_name="Alpha",
        actor_state=owned_payload({"ap": 2}),
        campaign_revision=4,
        active_side_id="hostiles",
        focused_actor_id="alpha",
        eligible_actors=(
            ModelDecisionActor("alpha", "Alpha", owned_payload({"ap": 2})),
            ModelDecisionActor("bravo", "Bravo", owned_payload({"ap": 1})),
        ),
    )

    message = _gm_decision_control_message(boundary)
    payload_text = cast(str, message["content"]).split("\n", maxsplit=2)[1]
    payload = json.loads(payload_text)
    assert payload["active_side_id"] == "hostiles"
    assert payload["focused_actor_id"] == "alpha"
    assert [actor["actor_id"] for actor in payload["eligible_actors"]] == ["alpha", "bravo"]
    assert [actor["actor_state"]["ap"] for actor in payload["eligible_actors"]] == [2, 1]


def test_installed_side_scope_interleaves_resources_refreshes_tools_and_reopens(
    tmp_path: Path,
) -> None:
    database = tmp_path / "side-scope.sqlite3"
    campaign_id = "campaign-side-scope"
    owner_id = "owner-side-scope"
    with AuthorityOwner.open(database, owner_id) as owner:
        candidate = owner.install_package_archive(io.BytesIO(_archive()), install_id="side-scope")
        owner.create_campaign(
            CampaignCreate(
                campaign_id=campaign_id,
                display_name="Side scope",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn-side-scope",
                initial_state=(StateValue(PACKAGE_ID, "state", cast(JsonValue, _state())),),
            )
        )

        scope = owner.model_decision_boundary(campaign_id)
        assert scope is not None
        assert scope.owner is ModelDecisionOwner.GM
        assert scope.active_side_id == "hostiles"
        assert scope.actor_id == "alpha"
        assert [actor.actor_id for actor in scope.eligible_actors] == ["alpha", "bravo"]
        assert [actor.actor_state["ap"] for actor in scope.eligible_actors] == [2, 1]
        assert all(actor.actor_id != "charlie" for actor in scope.eligible_actors)
        assert [tool.name for tool in owner.catalog(campaign_id)] == ["act_alpha_first"]

        for revision, tool_name, expected_ids, expected_ap, next_tools in (
            (0, "act_alpha_first", ["alpha", "bravo"], [1, 1], ["act_bravo"]),
            (1, "act_bravo", ["alpha"], [1], ["act_alpha_finish"]),
            (2, "act_alpha_finish", [], [], ["end_side"]),
        ):
            receipt = owner.execute_action(
                _command(
                    owner_id=owner_id,
                    campaign_id=campaign_id,
                    action_id=f"act-{revision}",
                    expected_revision=revision,
                    tool_name=tool_name,
                    parameters={},
                )
            )
            assert receipt.status is ActionStatus.COMMITTED
            scope = owner.model_decision_boundary(campaign_id)
            assert scope is not None
            assert [item.actor_id for item in scope.eligible_actors] == expected_ids
            assert [item.actor_state["ap"] for item in scope.eligible_actors] == expected_ap
            assert [tool.name for tool in owner.catalog(campaign_id)] == next_tools

        assert [tool.name for tool in owner.catalog(campaign_id)] == ["end_side"]
        with pytest.raises(StaleRevision):
            owner.execute_action(
                _command(
                    owner_id=owner_id,
                    campaign_id=campaign_id,
                    action_id="end-stale",
                    expected_revision=2,
                    tool_name="end_side",
                    parameters={},
                )
            )

    with AuthorityOwner.open(database, owner_id) as reopened:
        scope = reopened.model_decision_boundary(campaign_id)
        assert scope is not None
        assert scope.active_side_id == "hostiles"
        assert scope.eligible_actors == ()
        assert [tool.name for tool in reopened.catalog(campaign_id)] == ["end_side"]

        receipt = reopened.execute_action(
            _command(
                owner_id=owner_id,
                campaign_id=campaign_id,
                action_id="end-side",
                expected_revision=3,
                tool_name="end_side",
                parameters={},
            )
        )
        assert receipt.status is ActionStatus.COMMITTED
        assert reopened.model_decision_boundary(campaign_id) is None


def test_player_side_scope_is_projected_without_changing_package_resources(tmp_path: Path) -> None:
    database = tmp_path / "player-side-scope.sqlite3"
    with AuthorityOwner.open(database, "owner-player-side") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_archive()), install_id="player-side-scope"
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="campaign-player-side",
                display_name="Player side",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn-player-side",
                initial_state=(StateValue(PACKAGE_ID, "state", cast(JsonValue, _state("player"))),),
            )
        )
        scope = owner.model_decision_boundary("campaign-player-side")
        assert scope is not None
        assert scope.owner is ModelDecisionOwner.PLAYER
        assert [actor.actor_state["ap"] for actor in scope.eligible_actors] == [2, 1]
