"""Recorded spell equations survive projection without frontend arithmetic or state access."""
from copy import deepcopy
import json

import pytest

from sylvia_gm.application.result_card_admission import validate_result_card_declaration
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.domain.content_validation import ContentShapeError


def declaration():
    return {'version': 1, 'title': 'Acid Splash', 'footprint': 'compact',
            'summary': {'path': 'context.damage'},
            'equations': {'path': 'context.checks'},
            'details': {'path': 'context.receipts'}}


def context():
    return {'damage': [{'label': 'Damage dice', 'value': [3, 1, 5, 5],
                        'equation': {'die_sides': 6, 'rolls': [3, 1, 5, 5],
                                     'roll': 14, 'modifier': 0, 'total': 14}}],
            'checks': [{'label': 'Lite caster', 'value': 'Dexterity saving throw · DC 18 · failure',
                        'equation': {'die_sides': 20, 'roll': 2, 'modifier': 2, 'total': 4}},
                       {'label': 'Lite caster concentration',
                        'value': 'Concentration · DC 10 · failure',
                        'equation': {'die_sides': 20, 'roll': 5, 'modifier': 2, 'total': 7}}],
            'receipts': [{'label': 'Damage', 'value': 'HP 104 to 90'}]}


def test_named_spell_checks_are_detached_recorded_equations():
    validate_result_card_declaration(declaration())
    values = context()
    card = project_result_card(declaration(), parameters={}, public_context=values)
    assert card['summary'][0]['equation']['rolls'] == [3, 1, 5, 5]
    assert [row['equation']['total'] for row in card['equations']] == [4, 7]
    saved = json.loads(json.dumps(card))
    values['checks'][0]['equation']['total'] = 999
    values['damage'][0]['equation']['rolls'][0] = 6
    assert card == saved


def test_equation_projection_does_not_recompute_totals_or_outcomes():
    values = context()
    values['checks'][0]['equation']['total'] = 99
    values['damage'][0]['equation']['total'] = 88
    card = project_result_card(declaration(), parameters={}, public_context=values)
    assert card['equations'][0]['equation']['total'] == 99
    assert card['summary'][0]['equation']['total'] == 88
    assert card['equations'][0]['value'].endswith('failure')


@pytest.mark.parametrize('bad', [[], [7], [True], [1.5], [0], [1]*65, None])
def test_invalid_pool_faces_fall_back_without_inventing_a_roll(bad):
    values = context()
    values['damage'][0]['equation']['rolls'] = bad
    assert project_result_card(declaration(), parameters={}, public_context=values) is None


def test_recorded_equations_cannot_reinterpret_references_even_when_optional():
    values = context()
    for field in ('die_sides', 'roll', 'modifier', 'total', 'rolls'):
        invalid = deepcopy(values)
        invalid['checks'][0]['equation'][field] = {'path': 'context.private'}
        invalid['checks'][0]['equation']['optional'] = True
        assert project_result_card(declaration(), parameters={}, public_context=invalid) is None


def test_authored_equation_paths_use_the_existing_public_boundary():
    valid = {'version': 1, 'title': 'Recorded check', 'equations': [{
        'label': 'Recipient', 'value': 'Recorded outcome', 'equation': {
            'die_sides': 20, 'roll': {'path': 'context.roll'}, 'modifier': 0, 'total': 99,
        }}]}
    validate_result_card_declaration(valid)
    assert project_result_card(valid, parameters={}, public_context={'roll': 4})['equations'][0]['equation']['total'] == 99
    for path in ('state.secret', 'context..roll'):
        invalid = deepcopy(valid)
        invalid['equations'][0]['equation']['roll'] = {'path': path}
        with pytest.raises(ContentShapeError):
            validate_result_card_declaration(invalid)


def test_featured_equations_are_bounded_and_require_actual_numeric_records():
    values = context()
    values['checks'] = [deepcopy(values['checks'][0]) for _ in range(16)]
    assert project_result_card(declaration(), parameters={}, public_context=values)
    values['checks'].append(deepcopy(values['checks'][0]))
    assert project_result_card(declaration(), parameters={}, public_context=values) is None
    values['checks'] = [{'label': 'No roll', 'value': 'Automatic success'}]
    assert project_result_card(declaration(), parameters={}, public_context=values) is None
    values['checks'] = []
    assert project_result_card(declaration(), parameters={}, public_context=values)['equations'] == []
