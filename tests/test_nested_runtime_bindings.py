"""Internal Ruleset arguments survive while omitted values remain authority-bound."""

import pytest
import yaml

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain.authority import StateValue, WriterRole, thaw_json_value


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ({}, {"was_null": False, "marker": "authoritative"}),
        ({"context": None}, {"was_null": True, "marker": "absent"}),
        ({"context": {"marker": "nested"}}, {"was_null": False, "marker": "nested"}),
    ],
)
def test_nested_arguments_and_public_state_binding(tmp_path, arguments, expected):
    fields = {"was_null": "_was_null", "marker": "_marker"}
    source = {
        "format": 1,
        "id": "example.nested-binding",
        "version": "1.0.0",
        "label": "Nested binding fixture",
        "publisher": "Example",
        "mechanic_families": {
            "helper": {
                "parameters": [
                    {
                        "name": "context",
                        "type": "object",
                        "required": False,
                        "source": "state",
                        "from_state": "state.context",
                    }
                ],
                "pre_conditions": [],
                "narrative": {},
                "effects": [
                    {"set": {"field": "_was_null", "value": "context == null"}},
                    {"set": {"field": "_marker", "value": "'absent'"}},
                    {
                        "condition": "context != null",
                        "on_true": [
                            {"set": {"field": "_marker", "value": "context.marker"}},
                        ],
                    },
                    {"publish": {"fields": fields}},
                ],
            },
            "wrapper": {
                "parameters": [],
                "pre_conditions": [],
                "narrative": {},
                "effects": [
                    {
                        "call": "helper",
                        "params": arguments,
                        "export_context": {"_was_null": "_was_null", "_marker": "_marker"},
                    },
                    {"publish": {"fields": fields}},
                ],
            },
        },
        "public_actions": [
            {
                "name": name,
                "family": name,
                "description": name,
                "configure": {},
                "capability": "example.nested-binding." + name,
                "state_predicates": [],
                "turn_statuses": ["open"],
                "writers": ["dm"],
                "reference_availability": "included",
                "gameplay_resolution": "engine_family",
                "deterministic_part": None,
                "gm_part": None,
            }
            for name in ("helper", "wrapper")
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
    with AuthorityOwner.open(tmp_path / "nested.sqlite3", "test") as owner:
        with materialize_package_archive((compiled.draft,)) as artifact:
            candidate = owner.install_package_archive(artifact.archive, install_id="nested")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="nested",
                display_name="Nested binding",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn",
                initial_state=(
                    StateValue(
                        "example.nested-binding", "state", {"context": {"marker": "authoritative"}}
                    ),
                ),
            )
        )
        before = owner.snapshot("nested").state
        for index, (name, result) in enumerate(
            (
                ("wrapper", expected),
                ("helper", {"was_null": False, "marker": "authoritative"}),
            )
        ):
            receipt = owner.execute_action(
                ActionCommand(
                    campaign_id="nested",
                    action_id=name,
                    turn_id="turn",
                    expected_revision=index,
                    package_id="example.nested-binding",
                    tool_name=name,
                    parameters={},
                    idempotency_key=name,
                    owner_id="test",
                    writer=WriterRole.DM,
                )
            )
            assert receipt.status.value == "committed"
            assert thaw_json_value(receipt.result)["context"] == result
        assert owner.snapshot("nested").state == before
