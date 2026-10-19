"""Check TD character format composition with Sylvia, without qualifying specialty mechanics."""

import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sylvia_gm.application.initial_character_state import (
    materialize_initial_character_state,
    verify_initial_character_state,
)
from sylvia_gm.application.schema_admission import validate_parameters
from sylvia_gm.domain.package_library import participant_schema_sha256
from sylvia_gm.runtime._condition_evaluator import evaluate
from sylvia_gm.runtime._expression_parser import parse_expression
from sylvia_gm.runtime._types import ExecutionContext

from balance_suite.parameters import default_manifest
from sylvia_ruleset import builder
from sylvia_ruleset.character_build import eligibility_catalog


def _authored_state(declaration):
    """Explicit synthetic resources exercise the format; they are not a starting profile."""
    fixture = builder._entity(
        "hex", side="allies", class_id="HACKER", position=[2, 3], weapon="pistol",
        parameter_values=default_manifest().seed_set().values, networking=6,
    )
    authored = deepcopy(fixture)
    for field in ("position_cell", "center", "muzzle", "control"):
        authored.pop(field)
    for field in ("target", "point"):
        authored["aim"].pop(field)
    for field in ("cyclic_rate_rpm", "controlled_by"):
        authored["weapon"].pop(field)
    for calculation in declaration["initial_state_calculations"]:
        authored.pop(calculation["set"]["field"], None)
    return authored


def test_declared_operative_state_copies_choices_without_scene_or_duplicate_lists():
    participant = builder._conformance_character()["artifact"]
    declaration = builder._character_declaration(
        builder._character_schema(participant["specialties"])
    )
    authored = _authored_state(declaration)
    reserve = deepcopy(authored["weapon"])
    reserve.update(id="spare-firearm", location="STOWED")
    authored["reserve_weapons"] = [reserve]
    before = deepcopy(authored)
    validate_parameters(participant, declaration["schema"])
    complete = materialize_initial_character_state(
        participant, authored,
        entity_state_schema=declaration["entity_state_schema"],
        initial_state_calculations=declaration["initial_state_calculations"],
    )
    assert authored == before
    for field in ("skills", "aptitudes", "specialties"):
        assert complete[field] == participant[field]
        assert complete[field] is not participant[field]
    assert complete["downed"] is False
    assert complete["reserve_weapons"] == [reserve]
    assert not {"position_cell", "center", "muzzle"} & set(complete)
    assert verify_initial_character_state(
        participant, complete,
        entity_state_schema=declaration["entity_state_schema"],
        initial_state_calculations=declaration["initial_state_calculations"],
    ) == complete
    context = ExecutionContext(params={"attacker": complete})
    assert evaluate(parse_expression("attacker.aim.target == null"), context) is True
    assert evaluate(parse_expression("attacker.weapon.controlled_by == null"), context) is True


def test_generated_creation_identity_matches_sylvia_complete_contract_hash():
    declaration = builder._character_declaration(builder._character_schema([]))
    completed = builder._complete_package_record({"participant_artifacts": [declaration]}, {})
    assert completed["participant_artifacts"][0]["schema_sha256"] == participant_schema_sha256(
        declaration["schema"], entity_state_schema=declaration["entity_state_schema"],
        initial_state_calculations=declaration["initial_state_calculations"],
    )


@pytest.mark.parametrize("unarmed", [False, True])
def test_new_character_save_reopen_and_prerequisite_rejection_use_normal_owner_path(tmp_path, unarmed):
    from sylvia_gm.application.reusable_participants import (
        ParticipantValidationError,
        load_reusable_participant,
        save_reusable_participant,
    )
    from sylvia_gm.domain.authority import PackageLockEntry, PackageRole
    from sylvia_gm.domain.campaign_plan import PackageBinding
    from sylvia_gm.domain.errors import CommandRejected
    from sylvia_gm.domain.package_library import ParticipantArtifactDeclaration

    catalog = eligibility_catalog(Path(__file__).resolve().parents[1])
    raw = builder._character_declaration(builder._character_schema(
        [node['id'] for node in catalog['nodes']], catalog,
    ))
    declaration = ParticipantArtifactDeclaration(
        kind=raw['kind'], schema_id=raw['schema_id'], schema=raw['schema'],
        schema_sha256=participant_schema_sha256(
            raw['schema'], entity_state_schema=raw['entity_state_schema'],
            initial_state_calculations=raw['initial_state_calculations'],
        ), entity_state_schema=raw['entity_state_schema'],
        initial_state_calculations=tuple(raw['initial_state_calculations']),
    )
    package = PackageLockEntry(
        package_id=builder.PACKAGE_ID, version='0.1.0', sha256='a' * 64,
        role=PackageRole.PRIMARY, load_order=0, dependencies=(), capabilities=(),
    )
    setting = PackageBinding('test.original-character', '0.1.0', 'b' * 64)
    participant = builder._conformance_character()['artifact']
    participant.update(id='maren_test', name='Maren', level=2,
                       specialties=['specialty.networker.entry.map_the_links',
                                    'specialty.networker.tier_1.credential_sweep'])
    authored = _authored_state(raw)
    authored.update(hp=7, res=3, ap=2)
    reserve = {**deepcopy(authored["weapon"]), "id": "reserve-rifle", "location": "STOWED"}
    authored["reserve_weapons"] = [reserve]
    if unarmed:
        authored["weapon"] = None
    arguments = dict(
        role='player_character', summary='A synthetic dock engineer.',
        authored_profile='# Maren\n\nA synthetic dock engineer.',
        participant=participant, entity_state=authored, package=package,
        declaration=declaration, campaign_setting=setting,
    )
    saved = save_reusable_participant(tmp_path, operation_id='maren', **arguments)
    loaded = load_reusable_participant(
        tmp_path, saved.artifact_id, package=package, declaration=declaration,
        campaign_setting=setting,
    )
    assert loaded.participant == participant
    assert [loaded.entity_state[key] for key in ('hp', 'res', 'ap')] == [7, 3, 2]
    assert loaded.entity_state["weapon"] == authored["weapon"]
    assert loaded.entity_state["reserve_weapons"] == [reserve]
    with pytest.raises(CommandRejected) as invalid_reserve:
        save_reusable_participant(tmp_path, operation_id='invalid-reserve', **{
            **arguments, 'entity_state': {**authored, 'reserve_weapons': [None]},
        })
    assert invalid_reserve.value.diagnostics[0].path == "reserve_weapons[0]"
    with pytest.raises(ParticipantValidationError) as failure:
        save_reusable_participant(tmp_path, operation_id='illegal-choice', **{
            **arguments, 'participant': {**participant, 'specialties': [
                'specialty.networker.tier_2.foothold',
            ]},
        })
    assert all(issue.path.startswith('specialties') for issue in failure.value.issues)
    assert load_reusable_participant(
        tmp_path, saved.artifact_id, package=package, declaration=declaration,
        campaign_setting=setting,
    ).content_sha256 == saved.content_sha256
