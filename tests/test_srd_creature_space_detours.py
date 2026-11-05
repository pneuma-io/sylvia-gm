"""An open, affordable destination beyond a hostile must remain reachable."""
from copy import deepcopy
from math import dist

import pytest

from sylvia_gm.spatial import SpatialEngine
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm import dice
from tests.test_srd_creature_space_owner import MAP, NS, make_case, request


def test_open_destination_beyond_hostile_has_a_legal_affordable_detour(tmp_path):
    value = make_case(tmp_path / 'detour.sqlite3')
    try:
        before = value.snapshot()
        scene = thaw_json_value(before.state[NS + '.state']['spatial_scene'])
        explicit = [[0, 0, 0], [2.5, -5, 0], [12.5, -5, 0], [15, 0, 0]]
        explicit_cost = sum(dist(a, b) for a, b in zip(explicit, explicit[1:]))
        assert explicit_cost < before.state[NS + '.combat']['movement_remaining'] == 30
        for start, end in zip(explicit, explicit[1:]):
            segment_scene = deepcopy(scene)
            next(body for body in segment_scene['entities'] if body['id'] == 'hero')['position'] = start
            overlap = SpatialEngine(segment_scene).query({
                'kind': 'circle_overlap', 'map_id': scene['map']['id'],
                'moving_entity_id': 'hero', 'fixed_entity_id': 'guard',
                'destination': end, 'moving_radius': 2.5, 'fixed_radius': 2.5,
            }).as_payload()
            assert not overlap['hit']
        proposed = request(value, [15, 0, 0])
        rng = dice._rng.getstate()
        availability = value.owner.player_action_availability(
            campaign_id='lite', expected_revision=before.revision,
            requests=[{'source': {'kind': 'panel', 'surface_id': MAP, 'action_id': 'move-walk'},
                       'selection': proposed['selection']}])
        assert availability['results'][0]['available'], availability
        preview = value.owner.preview_panel_action(**proposed)
        cost = {row['label']: row['value'] for row in preview['preview']['summary']}['Movement spent']
        assert 15 < cost <= explicit_cost
        for change in ({'confirmation': None}, {'confirmation': 'invalid'},
                       {'operation_id': 'changed-detour', 'confirmation': preview['confirmation']}):
            with pytest.raises(CommandRejected):
                value.owner.execute_panel_action(**{**proposed, **change})
        assert value.snapshot() == before
        assert dice._rng.getstate() == rng
        receipt = value.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        after = value.snapshot()
        assert after.state[NS + '.combat']['movement_spent'] == pytest.approx(cost)
        assert next(body for body in after.state[NS + '.state']['spatial_scene']['entities']
                    if body['id'] == 'hero')['position'] == [15, 0, 0]
        assert value.owner.execute_panel_action(**proposed) == receipt
        value.reopen()
        assert value.snapshot().state == after.state
    finally:
        value.close()


def test_unaffordable_destination_does_not_commit_a_partial_detour(tmp_path):
    value = make_case(tmp_path / 'unaffordable.sqlite3')
    try:
        value.act('move_srd_scene_creature', actor_state='hero', destination=[-15, 0, 0])
        before = value.snapshot()
        assert before.state[NS + '.combat']['movement_remaining'] == 15
        proposed = request(value, [15, 0, 0])
        with pytest.raises(CommandRejected):
            value.owner.preview_panel_action(**proposed)
        value.reject('move_srd_scene_creature', {'actor_state': 'hero', 'destination': [15, 0, 0]},
                     match='enough movement')
        assert value.snapshot() == before
    finally:
        value.close()
