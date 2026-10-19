"""Hidden collection state is authoritative without appearing on the player wire."""

import json
from dataclasses import replace

import pytest
import yaml

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain.authority import StateValue, WriterRole, thaw_json_value
from sylvia_gm.runtime import _collections
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._exceptions import EffectExecutionError
from sylvia_gm.runtime._types import ExecutionBudget, ExecutionContext


def test_collection_operations_preserve_inputs_and_reject_unbounded_work():
    original = ["a", "b", "a"]
    assert _collections.without(original, ["a"]) == ["b", "a"]
    assert _collections.replaced(original, 1, "c") == ["a", "c", "a"]
    assert _collections.joined(original, []) == original
    assert original == ["a", "b", "a"]
    assert _collections.occurrences(original, "a") == 2
    assert _collections.portion(original, 0, 2) == ["a", "b"]
    for operation in (
        lambda: _collections.indices(1025),
        lambda: _collections.indices(True),
        lambda: _collections.replaced(original, -1, "x"),
        lambda: _collections.without(original, ["absent"]),
        lambda: _collections.total([True]),
    ):
        with pytest.raises(ValueError):
            operation()


def test_multiset_partition_is_exact_and_deduplicates_repeated_group_slots():
    patterns = [[0, 0], [1, 1], [0, 1, 2], [0, 0, 0]]
    assert _collections.partitions([0, 0, 0, 0, 1, 2], patterns, [3, 3]) == [[2, 3]]
    assert _collections.partitions([0, 0, 1, 1], patterns, [2, 2]) == [[0, 1]]
    assert _collections.partitions([0, 0, 1], patterns, [2, 2]) == []


def test_hidden_shuffle_cannot_be_replayed_across_a_pending_choice():
    context = ExecutionContext(budget=ExecutionBudget())
    execute_effects([{"shuffle": {"collection": ["a", "b"]}, "store_as": "_bag"}], context)
    with pytest.raises(EffectExecutionError, match="Commit a hidden shuffle"):
        execute_effects([{"request_choice": {}}], context)


def test_private_collection_receipt_snapshot_retry_and_reopen(tmp_path, monkeypatch):
    calls = []

    class Random:
        def shuffle(self, values):
            calls.append(True)
            values.reverse()

    monkeypatch.setattr("sylvia_gm.runtime._effect_executor.secrets.SystemRandom", Random)
    source = {
        "format": 1,
        "id": "example.private-collection",
        "version": "1.0.0",
        "label": "Private collection",
        "publisher": "Example",
        "mechanic_families": {
            "deal": {
                "parameters": [],
                "pre_conditions": [],
                "narrative": {},
                "effects": [
                    {"shuffle": {"collection": ["hidden-a", "hidden-b"]}, "store_as": "_bag"},
                    {"set": {"field": "state.bag", "value": "_bag"}},
                    {"publish": {"fields": {"message": "'Pieces dealt.'"}}},
                ],
            }
        },
        "public_actions": [
            {
                "name": "deal",
                "family": "deal",
                "description": "Deal private pieces.",
                "configure": {},
                "capability": "example.private-collection.deal",
                "state_predicates": [],
                "turn_statuses": ["open"],
                "writers": ["dm"],
                "reference_availability": "included",
                "gameplay_resolution": "engine_family",
                "deterministic_part": None,
                "gm_part": None,
            }
        ],
        "private_mechanics": [],
        "definitions": {},
        "participant_artifacts": [],
        "operating_guide": None,
        "state_schemas": [
            {"key": "state", "schema": {"type": "object"}, "references": [], "visibility": "engine"}
        ],
    }
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    database = tmp_path / "collection.sqlite3"
    with AuthorityOwner.open(database, "test") as owner:
        with materialize_package_archive((compiled.draft,)) as artifact:
            candidate = owner.install_package_archive(artifact.archive, install_id="example")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="collection",
                display_name="Collection",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn",
                initial_state=(StateValue("example.private-collection", "state", {}),),
            )
        )
        command = ActionCommand(
            campaign_id="collection",
            action_id="deal-once",
            turn_id="turn",
            expected_revision=0,
            package_id="example.private-collection",
            tool_name="deal",
            parameters={},
            idempotency_key="deal-once",
            owner_id="test",
            writer=WriterRole.DM,
        )
        receipt = owner.execute_action(command)
        assert owner.execute_action(command) == receipt
        assert len(calls) == 1
        assert owner.snapshot("collection").state["example.private-collection.state"]["bag"] == [
            "hidden-b",
            "hidden-a",
        ]
        assert "hidden-" not in json.dumps(thaw_json_value(receipt.result))
        safe = owner.desktop_snapshot("collection")
        assert "example.private-collection.state" not in safe.state
        assert safe.mutations == ()
        assert "hidden-" not in json.dumps(
            [thaw_json_value(event.safe_payload) for event in safe.events]
        )
    with AuthorityOwner.open(database, "reopen") as owner:
        assert owner.desktop_snapshot("collection") == safe
        assert owner.execute_action(replace(command, owner_id="reopen")) == receipt
        assert len(calls) == 1
