"""Check settled class/tier eligibility independently of starting and progression tuning."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sylvia_ruleset.character_build import build_issues, eligibility_catalog


def test_canonical_catalog_and_one_legal_networker_build():
    catalog = eligibility_catalog(Path(__file__).resolve().parents[1])
    chosen = [
        'specialty.networker.entry.map_the_links',
        'specialty.networker.tier_1.pivot',
        'specialty.networker.tier_1.quiet_enumeration',
        'specialty.networker.tier_2.foothold',
        'specialty.networker.tier_2.segment_map',
    ]
    build = {'class_id': 'HACKER', 'level': 5, 'specialties': chosen}
    assert build_issues(build, catalog) == []
    assert any('belongs to HACKER' in issue for issue in build_issues({**build, 'class_id': 'TECH'}, catalog))
    assert any('at most 4' in issue for issue in build_issues({**build, 'level': 4}, catalog))
    assert any('two TIER 1' in issue for issue in build_issues({**build, 'specialties': chosen[:2] + chosen[3:]}, catalog))
    assert any('ENTRY' in issue for issue in build_issues({**build, 'specialties': chosen[1:]}, catalog))


def test_keystone_needs_two_actual_tier_four_choices_not_duplicate_credit():
    catalog = eligibility_catalog(Path(__file__).resolve().parents[1])
    nodes = [node for node in catalog['nodes'] if node['specialty'] == 'NETWORKER']
    chosen = [node['id'] for node in nodes if node['tier'] in (0, 1, 2, 3)]
    tier_four = next(node['id'] for node in nodes if node['tier'] == 4)
    keystone = next(node['id'] for node in nodes if node['tier'] == 5)
    issues = build_issues({'class_id': 'HACKER', 'level': 20,
                          'specialties': chosen + [tier_four, tier_four, keystone]}, catalog)
    assert any('more than once' in issue for issue in issues)
    assert any('two TIER 4' in issue for issue in issues)


def test_ordinary_participant_schema_enforces_settled_build_rules():
    from copy import deepcopy
    import pytest
    from sylvia_gm.application.schema_admission import validate_json_schema, validate_parameters
    from sylvia_gm.domain.errors import MechanicsRejected
    from sylvia_ruleset import builder

    catalog = eligibility_catalog(Path(__file__).resolve().parents[1])
    schema = builder._character_schema([node['id'] for node in catalog['nodes']], catalog)
    validate_json_schema(schema)
    participant = builder._conformance_character()['artifact']
    participant.update(level=5, specialties=[
        'specialty.networker.entry.map_the_links',
        'specialty.networker.tier_1.pivot',
        'specialty.networker.tier_1.quiet_enumeration',
        'specialty.networker.tier_2.foothold',
        'specialty.networker.tier_2.segment_map',
    ])
    before = deepcopy(participant)
    validate_parameters(participant, schema)
    assert participant == before
    for override in (
        {'class_id': 'TECH'}, {'level': 4},
        {'specialties': participant['specialties'][1:]},
        {'specialties': participant['specialties'][:2] + participant['specialties'][3:]},
        {'specialties': participant['specialties'] + participant['specialties'][:1]},
    ):
        with pytest.raises(MechanicsRejected) as failure:
            validate_parameters({**participant, **override}, schema)
        assert all(path.startswith('specialties') for path in failure.value.field_paths)
    assert 'max_ap' not in schema['properties']
