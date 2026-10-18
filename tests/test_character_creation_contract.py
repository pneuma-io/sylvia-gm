"""Creation declarations survive real package admission and reusable character storage."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
import yaml

from sylvia_gm.agent._builder_session import (
    _participant_artifact_tool,
    _participant_draft_check_tool,
)
from sylvia_gm.agent._content_definition_tool import content_definition_tool
from sylvia_gm.agent._ruleset_draft_check_tool import ruleset_draft_check_tool
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import bundled_rift_participant_declaration
from sylvia_gm.application.initial_character_state import (
    materialize_initial_character_state,
    verify_initial_character_state,
)
from sylvia_gm.application.reusable_participants import (
    load_reusable_participant,
    save_reusable_participant,
)
from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.content_definitions import get_content_definition
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes, participant_schema_sha256


def _draft(workspace: Path, expression: str = "participant.rank * 2") -> None:
    source = get_content_definition("ruleset_source")["example"]
    source["participant_artifacts"] = [
        {
            "kind": "player_character",
            "schema_id": "example.coin-rules.character",
            "schema": {
                "$id": "example.coin-rules.character",
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "rank": {
                        "type": "integer",
                        "description": "Final rank after creation choices.",
                    },
                },
                "required": ["name", "rank"],
                "additionalProperties": False,
            },
            "entity_state_schema": {
                "type": "object",
                "properties": {"charge": {"type": "integer"}},
                "required": ["charge"],
                "additionalProperties": False,
            },
            "initial_state_calculations": [{"set": {"field": "charge", "value": expression}}],
        }
    ]
    path = workspace / "ruleset-draft" / "ruleset-source.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")


def test_creation_contract_and_calculated_character_survive_restart(tmp_path: Path) -> None:
    _draft(tmp_path)
    database = tmp_path / "owner.sqlite3"
    with materialize_ruleset_draft(tmp_path) as materialized:
        archive = materialized.archive.read()
    with AuthorityOwner.open(database, "character-contract-install") as owner:
        candidate = owner.install_package_archive(BytesIO(archive), install_id="coin-rules")
        (declaration,) = owner.package_participant_declarations(candidate.candidate_id)
    with AuthorityOwner.open(database, "character-contract-reopened") as owner:
        (restored,) = owner.package_participant_declarations(candidate.candidate_id)
        assert restored == declaration
    definition = content_definition_tool((restored,)).handler({"name": "character_creation"}, None)[
        "definition"
    ]["declarations"][0]
    assert definition["creation_available"] is True
    assert definition["participant"]["properties"]["rank"]["description"] == (
        "Final rank after creation choices."
    )
    assert definition["authored_entity_state"]["properties"] == {}
    assert definition["initial_state_calculations"][0]["set"]["field"] == "charge"
    assert restored.schema_sha256 == participant_schema_sha256(
        restored.schema,
        entity_state_schema=restored.entity_state_schema,
        initial_state_calculations=restored.initial_state_calculations,
    )
    assert restored.schema_sha256 != participant_schema_sha256(restored.schema)
    package = candidate.package_lock.entries[0]
    setting = PackageBinding("example.coin-setting", "1.0.0", "2" * 64)
    checked = _participant_draft_check_tool("companion", (restored,)).handler(
        {
            "summary": "Signal keeper.",
            "authored_profile": "# Vela\n\nA signal keeper.",
            "participant": {"name": "Vela", "rank": 3},
            "entity_state": "{}",
        },
        None,
    )
    assert checked == {
        "success": True,
        "status": "valid_draft",
        "published": False,
        "state_changed": False,
        "complete_entity_state": {"charge": 6},
        "setting_data": {},
    }
    assert not (tmp_path / "participants").exists()
    saved = save_reusable_participant(
        tmp_path,
        operation_id="coin-character",
        role="companion",
        summary="Signal keeper.",
        authored_profile="# Vela\n\nA signal keeper who records promises.",
        participant={"name": "Vela", "rank": 3},
        entity_state={},
        package=package,
        declaration=restored,
        campaign_setting=setting,
    )
    assert saved.entity_state == {"charge": 6}
    assert (
        load_reusable_participant(
            tmp_path,
            saved.artifact_id,
            package=package,
            declaration=restored,
            campaign_setting=setting,
        )
        == saved
    )

    def publish(**params: object) -> object:
        return save_reusable_participant(
            tmp_path,
            operation_id="bad-character",
            package=package,
            declaration=restored,
            campaign_setting=setting,
            **params,
        )

    result = _participant_artifact_tool(role="companion", publisher=publish).handler(
        {
            "summary": "Signal keeper.",
            "authored_profile": "# Vela\n\nA signal keeper.",
            "participant": {"name": "Vela", "rank": 3},
            "entity_state": json.dumps({"charge": 99}),
        },
        None,
    )
    assert result["code"] == "character_initial_state_calculated_field_authored"
    assert result["validation_errors"][0]["path"] == "entity_state.charge"
    assert result["state_changed"] is False
    canonical_json_bytes(result)  # The result can be retained in ordinary Builder history.


def test_bad_creation_formula_matches_draft_and_install_rejection(tmp_path: Path) -> None:
    _draft(tmp_path, "combat.round")
    checked = ruleset_draft_check_tool(tmp_path).handler({}, None)
    assert checked["code"] == "character_initial_state_expression_invalid"
    with materialize_ruleset_draft(tmp_path) as materialized:
        archive = materialized.archive.read()
    with AuthorityOwner.open(tmp_path / "owner.sqlite3", "bad-contract") as owner:
        with pytest.raises(CommandRejected) as rejected:
            owner.install_package_archive(BytesIO(archive), install_id="bad-coin-rules")
        assert checked["validation_errors"] == [
            item.as_payload() for item in rejected.value.diagnostics
        ]
        assert owner.package_candidates() == ()


def test_bundled_srd_starting_character_matches_its_declared_calculations() -> None:
    declaration = bundled_rift_participant_declaration()
    character = json.loads(
        (Path(__file__).parents[1] / "src/sylvia_gm/campaign/the-rift-awakening.json").read_text(
            encoding="utf-8"
        )
    )["player_archetypes"][0]
    complete = verify_initial_character_state(
        character["participant"],
        character["entity_state"],
        entity_state_schema=declaration.entity_state_schema,
        initial_state_calculations=declaration.initial_state_calculations,
        initial_state_validation=declaration.initial_state_validation,
    )
    authored = json.loads(canonical_json_bytes(complete))
    for calculation in declaration.initial_state_calculations:
        parts = calculation["set"]["field"].split(".")
        parent = authored
        for part in parts[:-1]:
            parent = parent[part]
        parent.pop(parts[-1], None)
    rebuilt = materialize_initial_character_state(
        character["participant"],
        authored,
        entity_state_schema=declaration.entity_state_schema,
        initial_state_calculations=declaration.initial_state_calculations,
        initial_state_validation=declaration.initial_state_validation,
    )
    assert rebuilt == complete
    assert rebuilt["saving_throw_modifiers"]["strength"] == 5
    assert rebuilt["saving_throw_modifiers"]["dexterity"] == 2
    assert rebuilt["hp"] == rebuilt["max_hp"] == 12
    assert rebuilt["hit_die_size"] == 10
    assert rebuilt["fighter_level"] == 1
    assert rebuilt["resources"]["hit_die"] == 1
    assert rebuilt["resources"]["second_wind"] == rebuilt["resource_maxima"]["second_wind"] == 2


@pytest.mark.parametrize("problem", ["missing_maxima", "nested_resource"])
def test_srd_draft_check_rejects_the_observed_resource_shape_errors(problem: str) -> None:
    from tests.test_reusable_participants import _participant, _starting_state

    declaration = bundled_rift_participant_declaration()
    state = _starting_state()
    if problem == "missing_maxima":
        state.pop("resource_maxima")
        expected_path = "resource_maxima.hit_die"
        expected_code = "character_initial_state_schema_invalid"
    else:
        state["resources"]["second_wind"] = {"remaining": 2, "maximum": 2}
        expected_path = "entity_state.resources.second_wind"
        expected_code = "character_initial_state_calculated_field_authored"
    result = _participant_draft_check_tool("player_character", (declaration,)).handler(
        {
            "summary": "A synthetic guard.",
            "authored_profile": "A synthetic guard from the harbor.",
            "participant": _participant("Mira"),
            "entity_state": json.dumps(state),
        },
        None,
    )
    assert result["code"] == expected_code
    assert result["state_changed"] is False
    assert expected_path in {item["path"] for item in result["validation_errors"]}


@pytest.mark.parametrize(
    "problem", ["valid", "missing_grants", "foreign_list", "counts", "prepared"],
)
def test_srd_draft_check_rejects_incomplete_or_illegal_casting_choices(problem):
    from tests.test_reusable_participants import _participant, _starting_state

    declaration = bundled_rift_participant_declaration()
    participant = _participant("New Wizard")
    participant.pop("fighting_styles")
    spells = ["fire_bolt", "light", "mage_hand", "magic_missile", "shield", "sleep",
              "burning_hands"]
    participant.update(classes=[{"id": "wizard", "level": 1}],
                       spellcasting_grants=[{"source": "wizard", "ability": "intelligence",
                                            "spells": spells}],
                       wizard_spellbook=spells[3:] + ["mage_armor", "chromatic_orb"])
    state = _starting_state()
    state["prepared_spells"] = list(spells)
    if problem == "missing_grants":
        participant.pop("spellcasting_grants")
    elif problem == "foreign_list":
        spells[-1] = "cure_wounds"
    elif problem == "counts":
        spells.pop()
    elif problem == "prepared":
        state["prepared_spells"] = []
    result = _participant_draft_check_tool("player_character", (declaration,)).handler(
        {"summary": "A synthetic Wizard.", "authored_profile": "A student of magic.",
         "participant": participant, "entity_state": json.dumps(state)}, None,
    )
    assert result["success"] is (problem == "valid"), result
    if problem != "valid":
        assert result["code"] == "character_initial_state_rule_rejected"
    assert result["state_changed"] is False


@pytest.mark.parametrize("fighter_level,uses", [(1, 0), (2, 1), (16, 1), (17, 2)])
def test_fighter_creation_derives_action_surge_from_class_level(fighter_level, uses):
    from tests.test_reusable_participants import _participant, _starting_state

    declaration = bundled_rift_participant_declaration()
    participant = _participant("New Fighter")
    participant.update(level=fighter_level, classes=[{"id": "fighter", "level": fighter_level}])
    if fighter_level >= 3:
        participant["subclasses"] = {"fighter": "champion"}
    if fighter_level >= 7:
        participant["fighting_styles"].append({
            "source": "champion", "style": "great_weapon_fighting",
        })
    result = _participant_draft_check_tool("player_character", (declaration,)).handler(
        {
            "summary": "A newly built Fighter.", "authored_profile": "A guard.",
            "participant": participant, "entity_state": json.dumps(_starting_state()),
        }, None,
    )
    assert result["success"] is True, result
    complete = result["complete_entity_state"]
    assert complete["resources"].get("action_surge", 0) == uses
    assert complete["resource_maxima"].get("action_surge", 0) == uses


@pytest.mark.parametrize("choice", ["missing", "complete", "wrong_school"])
def test_starting_evoker_requires_its_two_savant_book_choices(choice):
    from tests.test_reusable_participants import _participant, _starting_state
    from tests.test_srd_spell_advancement import WIZARD_BOOK, WIZARD_SPELLS

    participant = _participant("New Evoker")
    participant.pop("fighting_styles")
    spells = WIZARD_SPELLS + ["mage_armor", "darkvision"]
    book = WIZARD_BOOK + ["longstrider", "false_life", "darkvision", "blur"]
    if choice == "complete":
        book += ["scorching_ray", "shatter"]
    elif choice == "wrong_school":
        book = ["mage_armor", "shield", "sleep", "longstrider", "ray_of_sickness", "false_life",
                "darkvision", "blur", "hold_person", "web", "alter_self", "enhance_ability"]
        spells = WIZARD_SPELLS[:3] + book[:6]
    participant.update(level=3, classes=[{"id": "wizard", "level": 3}],
                       subclasses={"wizard": "evoker"}, wizard_spellbook=book,
                       spellcasting_grants=[{"source": "wizard", "ability": "intelligence",
                                            "spells": spells}])
    state = _starting_state()
    state["prepared_spells"] = spells
    result = _participant_draft_check_tool(
        "player_character", (bundled_rift_participant_declaration(),),
    ).handler({"summary": "An Evoker.", "authored_profile": "An Evoker from the harbor.",
               "participant": participant, "entity_state": json.dumps(state)}, None)
    assert result["success"] is (choice == "complete"), result
    assert result["state_changed"] is False
