from __future__ import annotations

import pytest

from sylvia_gm.agent._content_definition_tool import content_definition_tool
from sylvia_gm.application.bundled_packages import (
    bundled_rift_authority,
    bundled_rift_participant_declaration,
)
from sylvia_gm.application.initial_character_state import materialize_initial_character_state
from sylvia_gm.application.reusable_participants import (
    load_reusable_participant,
    save_reusable_participant,
)
from sylvia_gm.application.schema_admission import validate_parameters
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected
from tests.test_reusable_participants import (
    _participant,
    _participant_authority,
    _starting_state,
)


def _without_second_wind():
    state = _starting_state()
    state["resources"].pop("second_wind", None)
    state["resource_maxima"].pop("second_wind", None)
    return state


def _complete(participant):
    declaration = bundled_rift_participant_declaration()
    participant = {**participant, "subclasses": {
        row["id"]: {"fighter": "champion", "rogue": "thief"}[row["id"]]
        for row in participant["classes"] if row["level"] >= 3
    }}
    if any(row["id"] == "fighter" and row["level"] >= 7 for row in participant["classes"]):
        participant["fighting_styles"] = [*participant["fighting_styles"], {
            "source": "champion", "style": "great_weapon_fighting",
        }]
    return materialize_initial_character_state(
        participant, _without_second_wind(),
        entity_state_schema=declaration.entity_state_schema,
        initial_state_calculations=declaration.initial_state_calculations,
        initial_state_validation=declaration.initial_state_validation,
    )


@pytest.mark.parametrize(("fighter_level", "expected"), [(1, 2), (4, 3), (10, 4), (20, 4)])
def test_srd_fighter_second_wind_uses_follow_canonical_bands(fighter_level, expected):
    participant = _participant("Custom Fighter")
    participant["level"] = fighter_level
    participant["classes"] = [{"id": "fighter", "level": fighter_level}]
    completed = _complete(participant)
    assert completed["fighter_level"] == fighter_level
    assert completed["resources"]["second_wind"] == expected
    assert completed["resource_maxima"]["second_wind"] == expected


def test_srd_fighter_grant_can_appear_later_in_multiclass_list():
    participant = _participant("Multiclass Fighter")
    participant["level"] = 10
    participant["classes"] = [{"id": "rogue", "level": 6}, {"id": "fighter", "level": 4}]
    completed = _complete(participant)
    assert completed["fighter_level"] == 4
    assert completed["resources"]["second_wind"] == 3
    assert completed["resource_maxima"]["second_wind"] == 3


def test_srd_nonfighter_receives_zero_second_wind():
    participant = _participant("Custom Rogue")
    participant.pop("fighting_styles")
    participant["classes"] = [{"id": "rogue", "level": 1}]
    completed = _complete(participant)
    assert completed["fighter_level"] == 0
    assert completed["resources"]["second_wind"] == 0
    assert completed["resource_maxima"]["second_wind"] == 0


def test_srd_fighter_cannot_override_ruleset_owned_second_wind():
    declaration = bundled_rift_participant_declaration()
    state = _without_second_wind()
    state["resources"]["second_wind"] = 1
    with pytest.raises(CommandRejected) as rejected:
        materialize_initial_character_state(
            _participant("Custom Fighter"), state,
            entity_state_schema=declaration.entity_state_schema,
            initial_state_calculations=declaration.initial_state_calculations,
        )
    assert rejected.value.code == "character_initial_state_calculated_field_authored"
    assert rejected.value.diagnostics[0].path == "entity_state.resources.second_wind"


@pytest.mark.parametrize("invalid", [
    {"level": 21}, {"classes": []},
    {"classes": [{"id": "fighter", "level": 1}] * 21},
    {"classes": [{"id": "fighter", "level": 21}]},
])
def test_srd_participant_schema_rejects_choices_outside_canonical_bounds(invalid):
    schema = bundled_rift_participant_declaration().schema
    participant = {**_participant("Custom Fighter"), **invalid}
    with pytest.raises(CommandRejected):
        validate_parameters(participant, schema)


def test_srd_origin_grants_reach_builder_and_survive_character_reopen(tmp_path):
    package, declaration = _participant_authority()
    skills = [
        "athletics", "intimidation", "perception", "insight", "survival",
        "acrobatics", "stealth", "persuasion",
    ]
    elara = {
        **_participant("Synthetic Elara"),
        "species": "human",
        "background": "soldier",
        "abilities": {
            "strength": 17, "dexterity": 13, "constitution": 15,
            "intelligence": 8, "wisdom": 12, "charisma": 10,
        },
        "origin_feats": ["savage_attacker", "skilled"],
        "skill_proficiencies": skills,
    }
    saved = save_reusable_participant(
        tmp_path,
        operation_id="synthetic-elara-origin-grants",
        role="player_character",
        summary="A synthetic Human Soldier Fighter.",
        authored_profile=(
            "# Synthetic Elara\n\nA Human Fighter with Resourceful, Skillful, Versatile, "
            "Savage Attacker and Skilled."
        ),
        participant=elara,
        entity_state=_starting_state(max_hp=12),
        package=package,
        declaration=declaration,
        campaign_setting=bundled_rift_authority().setting,
    )
    reopened = load_reusable_participant(
        tmp_path,
        saved.artifact_id,
        package=package,
        declaration=declaration,
        campaign_setting=bundled_rift_authority().setting,
    )
    assert reopened.participant["origin_feats"] == ["savage_attacker", "skilled"]
    assert reopened.participant["skill_proficiencies"] == skills

    definition = content_definition_tool((declaration,)).handler(
        {"name": "character_creation"}, None
    )["definition"]["declarations"][0]["participant"]
    assert definition["properties"]["origin_feats"]["items"]["enum"] == [
        "alert", "magic_initiate", "savage_attacker", "skilled",
    ]
    skill_guidance = definition["properties"]["skill_proficiencies"]["description"]
    assert "source by source" in skill_guidance
    assert "Skillful grants one skill" in skill_guidance
    assert "each Skilled selection grants three skill or tool" in skill_guidance


@pytest.mark.parametrize(("field", "value"), [
    ("skill_proficiencies", "perception"),
    ("tool_proficiencies", "smiths_tools"),
    ("weapon_proficiencies", "martial_weapons"),
    ("weapon_masteries", "longsword"),
    ("saving_throw_proficiencies", "strength"),
])
def test_srd_proficiency_sets_reject_duplicate_choices(field, value):
    participant = _participant("Synthetic duplicate grant")
    participant[field] = [value, value]
    with pytest.raises(MechanicsRejected) as rejected:
        validate_parameters(participant, bundled_rift_participant_declaration().schema)
    assert [(issue.path, issue.kind) for issue in rejected.value.validation_issues] == [
        (field, "duplicate_items"),
    ]


def test_srd_repeatable_origin_feat_selections_remain_admissible():
    participant = _participant("Synthetic repeatable feat")
    participant["origin_feats"] = ["skilled", "skilled"]
    validate_parameters(participant, bundled_rift_participant_declaration().schema)


@pytest.mark.parametrize("feats", [None, [], ["not_an_srd_origin_feat"]])
def test_srd_origin_feat_requires_a_supported_choice(feats):
    participant = _participant("Synthetic unknown feat")
    if feats is None:
        participant.pop("origin_feats")
    else:
        participant["origin_feats"] = feats
    with pytest.raises(CommandRejected):
        validate_parameters(participant, bundled_rift_participant_declaration().schema)


@pytest.mark.parametrize(("level", "expected"), [(1, 4), (5, 5), (20, 8)])
def test_srd_alert_origin_feat_adds_proficiency_to_starting_initiative(level, expected):
    participant = _participant("Synthetic Alert character")
    participant["level"] = level
    participant["classes"] = [{"id": "fighter", "level": level}]
    participant["origin_feats"] = ["alert"]
    completed = _complete(participant)
    assert completed["initiative_modifier"] == expected
