"""Portable character constraints share bounded runtime effects without mutation authority."""

import json
from copy import deepcopy
from io import BytesIO

import pytest
import yaml

from sylvia_gm.agent._builder_session import _participant_draft_check_tool
from sylvia_gm.agent._content_definition_tool import content_definition_tool
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.character_rules import (
    prepare_character_rules,
    validate_character_rules,
    verify_character_rules,
)
from sylvia_gm.application.initial_character_state import (
    materialize_initial_character_state,
    verify_initial_character_state,
)
from sylvia_gm.application.reusable_participants import (
    load_reusable_participant,
    parse_setting_character,
    save_reusable_participant,
)
from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import participant_schema_sha256
from tests.test_character_creation_contract import _draft


def _program():
    return {
        "description": "Choose a rank no greater than the declared maximum.",
        "definitions": {"limits": {"rank": {"maximum": 3}}},
        "effects": [
            {"table_lookup": {"table": "definitions.limits", "key": "'rank'", "required": True},
             "store_as": "_limit"},
            {"require": {"condition": "participant.rank <= _limit.maximum",
                         "error": "Choose a rank at or below the maximum."}},
        ],
    }


def test_prepared_rules_recheck_state_and_isolate_aliased_tables():
    program = {
        "description": "Check every state using fresh local table data.",
        "definitions": {"lists": {"seed": [0]}},
        "effects": [
            {"table_lookup": {"table": "definitions.lists", "key": "'seed'", "required": True},
             "store_as": "_items"},
            {"list_push": {"list": "_items", "value": "state.charge"}},
            {"require": {"condition": "state.charge <= participant.rank",
                         "error": "Charge exceeds rank."}},
            {"set": {"field": "_charge", "value": "state.charge * 2"}},
        ],
    }
    before = deepcopy(program)
    check = prepare_character_rules({"rank": 3}, program)
    first = check({"charge": 1})
    assert first == {"_items": [0, 1], "_charge": 2}
    first["_items"].append(99)
    assert check({"charge": 2}) == {"_items": [0, 2], "_charge": 4}
    assert check({"charge": 1}) == {"_items": [0, 1], "_charge": 2}
    with pytest.raises(CommandRejected, match="Charge exceeds rank"):
        check({"charge": 4})
    assert program == before


def test_prepared_rules_reject_effectful_programs_before_execution():
    program = _program()
    program["effects"].append({"set": {"field": "state.charge", "value": "99"}})
    before = deepcopy(program)
    with pytest.raises(CommandRejected, match="private local variables"):
        prepare_character_rules({"rank": 3}, program)
    assert program == before


def test_creation_projects_read_only_rule_results_and_rechecks_saved_values():
    program = _program()
    program["effects"].append({"set": {"field": "_charge", "value": "participant.rank * 2"}})
    contract = {
        "entity_state_schema": {"type": "object", "properties": {
            "charge": {"type": "integer"},
        }, "required": ["charge"], "additionalProperties": False},
        "initial_state_calculations": [{"set": {"field": "charge", "value": "rules._charge"}}],
        "initial_state_validation": program,
    }
    participant, authored = {"rank": 3}, {}
    before = deepcopy((participant, authored, contract))
    complete = materialize_initial_character_state(participant, authored, **contract)
    assert complete == {"charge": 6}
    assert (participant, authored, contract) == before
    assert verify_initial_character_state(participant, complete, **contract) == complete
    with pytest.raises(CommandRejected) as rejected:
        verify_initial_character_state(participant, {"charge": 7}, **contract)
    assert rejected.value.code == "character_initial_state_calculated_value_mismatch"
    with pytest.raises(CommandRejected, match="at or below"):
        materialize_initial_character_state({"rank": 4}, authored, **contract)


def test_rule_result_projection_honors_false_conditions_before_running_the_program():
    contract = {
        "entity_state_schema": {"type": "object", "properties": {
            "charge": {"type": "integer"}, "optional": {"type": "integer"},
        }, "required": ["charge"], "additionalProperties": False},
        "initial_state_calculations": [
            {"condition": "false", "set": {"field": "optional", "value": "rules._missing"}},
            {"set": {"field": "charge", "value": "6"}},
        ],
        "initial_state_validation": {
            "description": "Validate after the ordinary charge calculation.", "definitions": {},
            "effects": [{"require": {
                "condition": "state.charge == 6", "error": "Charge required.",
            }}],
        },
    }
    assert materialize_initial_character_state({}, {}, **contract) == {"charge": 6}


def test_rules_survive_package_storage_and_reopen_and_share_builder_validation(tmp_path):
    _draft(tmp_path)
    path = tmp_path / "ruleset-draft/ruleset-source.yaml"
    source = yaml.safe_load(path.read_text())
    source["participant_artifacts"][0]["initial_state_validation"] = _program()
    path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    with materialize_ruleset_draft(tmp_path) as built:
        archive = built.archive.read()
    database = tmp_path / "owner.db"
    with AuthorityOwner.open(database, "test") as owner:
        candidate = owner.install_package_archive(BytesIO(archive), install_id="rules")
        (declaration,) = owner.package_participant_declarations(candidate.candidate_id)
    with AuthorityOwner.open(database, "test") as owner:
        (restored,) = owner.package_participant_declarations(candidate.candidate_id)
        assert restored == declaration
        assert restored.initial_state_validation == _program()
        assert restored.schema_sha256 != participant_schema_sha256(
            restored.schema, entity_state_schema=restored.entity_state_schema,
            initial_state_calculations=restored.initial_state_calculations,
        )
        exposed = content_definition_tool((restored,)).handler(
            {"name": "character_creation"}, None,
        )["definition"]["declarations"][0]
        assert exposed["validation_requirements"] == _program()["description"]
        assert "initial_state_validation" not in exposed
        checker = _participant_draft_check_tool("player_character", (restored,))
        for rank in (3, 4):
            request = {"summary": "A signal keeper.", "authored_profile": "A signal keeper.",
                       "participant": {"name": "Keeper", "rank": rank}, "entity_state": "{}"}
            before = deepcopy(request)
            result = checker.handler(request, None)
            assert request == before
            assert result["state_changed"] is False
            assert result["success"] is (rank == 3)
            if rank == 4:
                assert result["code"] == "character_initial_state_rule_rejected"
        package = candidate.package_lock.entries[0]
        setting = PackageBinding("example.coin-setting", "1.0.0", "2" * 64)
        saved = save_reusable_participant(
            tmp_path, operation_id="keeper", role="player_character", summary="A signal keeper.",
            authored_profile="A signal keeper.", participant={"name": "Keeper", "rank": 3},
            entity_state={}, package=package, declaration=restored, campaign_setting=setting,
        )
        assert load_reusable_participant(
            tmp_path, saved.artifact_id, package=package, declaration=restored,
            campaign_setting=setting,
        ) == saved
        with pytest.raises(CommandRejected) as rejected:
            save_reusable_participant(
                tmp_path, operation_id="bad-keeper", role="player_character", summary="Keeper.",
                authored_profile="Keeper.", participant={"name": "Keeper", "rank": 4},
                entity_state={}, package=package, declaration=restored, campaign_setting=setting,
            )
        assert rejected.value.code == "character_initial_state_rule_rejected"
        envelope = {
            "format_id": "sylvia-gm-character-file", "format_version": 2, "id": "keeper",
            "schema": {"id": restored.schema_id, "content_sha256": restored.schema_sha256},
            "summary": "Keeper.", "authored_profile": "Keeper.", "setting_data": {},
            "participant": {"name": "Keeper", "rank": 4}, "entity_state": {"charge": 8},
        }
        with pytest.raises(CommandRejected) as imported:
            parse_setting_character(json.dumps(envelope), character_id="keeper",
                                    role="player_character", package=package, declaration=restored,
                                    campaign_setting=setting)
        assert imported.value.code == "character_initial_state_rule_rejected"


@pytest.mark.parametrize("effect", [
    {"call": "write_something"},
    {"dice": {"expression": "1d20"}},
    {"set": {"field": "state.charge", "value": 99}},
    {"set_participant": {"participant": "hero", "field": "rank", "value": 99}},
    {"table_lookup": {"table": "state.private", "key": "'rank'"}, "store_as": "_rank"},
])
def test_nested_rules_cannot_obtain_mutation_randomness_or_external_access(effect):
    program = _program()
    program["effects"] = [{"condition": "true", "on_true": [effect]}]
    with pytest.raises(CommandRejected) as rejected:
        validate_character_rules(program)
    assert rejected.value.code == "character_initial_state_validation_invalid"


def test_rules_share_the_runtime_operation_budget_and_preserve_inputs():
    program = _program()
    program["effects"] = [
        {"set": {"field": "_items", "value": [0] * 142}},
        {"for_each": {"collection": "_items", "as": "_outer", "do": [
            {"for_each": {"collection": "_items", "as": "_inner", "do": [
                {"require": {"condition": "true", "error": "Unused."}},
            ]}},
        ]}},
    ]
    participant, state = {"rank": 3}, {"charge": 6}
    with pytest.raises(CommandRejected) as rejected:
        verify_character_rules(participant, state, program)
    assert rejected.value.code == "character_initial_state_validation_failed"
    assert json.dumps([participant, state]) == '[{"rank": 3}, {"charge": 6}]'
