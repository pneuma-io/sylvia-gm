"""Native Fey Step geometry, saved rider, teleport, budgets and truthful receipt checks."""
from copy import deepcopy
import json, os
from pathlib import Path
import pytest

from scripts.srd_lite_steed_fey_step import (
    ACTION, PREFLIGHT, RIDER_FIELD, MOUNT_FIELD, bind_steed_fey_step, expose_steed_fey_step)
from scripts.srd_lite_steed_healing_touch import CHARGE
from sylvia_gm import dice
from sylvia_gm.application.package_runtime import _json_safe
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.application.result_card_admission import validate_result_card_declaration
from sylvia_gm.runtime import RulesetRuntime
from tests.srd_lite_steed_healing_touch_fixtures import bound as touch_bound, _case, _snapshot
from tests.srd_lite_mass_cure_geometry_fixtures import _calls, _scene_entity, _wall


@pytest.fixture(scope='module')
def bound(touch_bound):
    data = deepcopy(touch_bound[1])
    original = {name: deepcopy(data['tools'][name]) for name in (
        'move_srd_travel_subject', 'move_srd_travel_subject_node',
        'resolve_srd_travel_placement', 'consume_srd_bonus_action', 'complete_srd_rest')}
    profiles = deepcopy(data['definitions']['spells'])
    changed = bind_steed_fey_step(data)
    assert data['definitions']['spells'] == profiles
    assert all(data['tools'][name] == tool for name, tool in original.items())
    node = original['move_srd_travel_subject_node']
    owning_index = next(i for i, row in enumerate(node['effects']) if 'spatial_mutation' in row)
    assert data['tools'][PREFLIGHT]['effects'][2:] == node['effects'][:owning_index]
    closure, pending = {}, [ACTION]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    parsed = RulesetRuntime(ruleset_dict={**{key: value for key, value in data.items()
        if key not in {'tools', 'procedures'}}, 'tools': closure})
    validate_result_card_declaration(data['tools'][ACTION]['metadata']['result_card'])
    return parsed, data, changed


def _fey_case(bound, *, rider=False, combat=False, geometry=None, steed_changes=None,
              caster_changes=None, rider_changes=None, rider_position=None, rider_plane='material'):
    bridge, state, act = _case(bound, combat=combat,
        steed_changes={'creature_type': 'fey', 'size_rank': 4, RIDER_FIELD: 'caster' if rider else '',
                       **(steed_changes or {})},
        caster_changes={'size_rank': 3, MOUNT_FIELD: 'steed' if rider else '',
                        **(caster_changes or {}), **(rider_changes or {})})
    scene = deepcopy(bridge.get_entity('state')['spatial_scene'])
    scene['geometry'] = geometry or []
    if rider:
        scene['entities'][0] = _scene_entity('caster', rider_position or [0, 0, 6], plane=rider_plane)
    bridge.write_field('state', 'spatial_scene', scene)
    return bridge, state, act


def _step(act, destination=None):
    return act(ACTION, steed_state='steed', destination=destination or [60, 0, 0])


def _frame(bridge, identity):
    return next(row for row in bridge.get_entity('state')['spatial_scene']['entities']
                if row['metadata']['participant_ref'] == identity)






def check_native_saved_rider_moves_with_saddle_offset_and_receipt(bound):
    bridge, _, act = _fey_case(bound, rider=True, combat=True,
                               caster_changes={'conditions': ['incapacitated']})
    before = dice._rng.getstate()
    result = _step(act, [30, 40, 0])
    assert result.success, result.error
    assert _frame(bridge, 'steed')['position'] == [30, 40, 0]
    assert _frame(bridge, 'caster')['position'] == [30, 40, 6]
    assert result.context['_fey_distance'] == 50
    assert bridge.get_entity('steed')[RIDER_FIELD] == 'caster'
    assert bridge.get_entity('caster')[MOUNT_FIELD] == 'steed'
    assert dice._rng.getstate() == before
    card = project_result_card(bound[1]['tools'][ACTION]['metadata']['result_card'],
        parameters={}, public_context=_json_safe(result.context))
    assert any(row['label'] == 'Rider' and row['value'] == 'Caster' for row in card['summary'])
    assert any(row['label'] == 'Rider' and row['value'] == [30, 40, 6] for row in card['details'])
    if os.environ.get('SRD_LITE_STEED_FEY_CARD'):
        Path(os.environ['SRD_LITE_STEED_FEY_CARD']).write_text(json.dumps(card, indent=2), encoding='utf-8')
