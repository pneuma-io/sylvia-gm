"""Package-selected occupied space projects separately from physical clearance and private actors."""
from copy import deepcopy

import pytest

from sylvia_gm.application.tactical_grid import project_coordinate_map
from sylvia_gm.domain.authority import thaw_json_value
from scripts.srd_lite_creature_space import CONVENTION, WIDTHS
from tests.test_tactical_grid_ui import _scene


def projection(rank):
    scene = _scene()
    scene['entities'][0]['metadata']['participant_ref'] = 'person'
    before = deepcopy(scene)
    result = thaw_json_value(project_coordinate_map({'scene': scene, 'occupied_space': {
        'declaration': {'convention': CONVENTION, 'widths': WIDTHS, 'category_field': 'size_rank'},
        'actors': {'person': {'size_rank': rank, 'private_note': 'never project this'}},
    }}, actor_reference='participant_ref'))
    assert scene == before
    return result['entities'][0]


@pytest.mark.parametrize('rank,width', list(enumerate(WIDTHS)))
def test_diameter_uses_actual_category_with_unchanged_position_and_body(rank, width):
    entity = projection(rank)
    assert entity['occupied_space'] == {'status': 'declared', 'kind': 'circle',
        'diameter': width, 'convention': CONVENTION}
    assert entity['footprint'] == {'kind': 'box', 'half_extents': [.4, .4]}
    assert entity['position'] == [1.5, 2.5, 0]
    assert 'private_note' not in str(entity)


@pytest.mark.parametrize('rank', [None, -1, 6, True, 2.0, 'Medium'])
def test_missing_or_unsupported_size_is_an_explicit_unknown(rank):
    entity = projection(rank)
    assert entity['occupied_space']['status'] == 'unavailable'
    assert 'diameter' not in entity['occupied_space']
