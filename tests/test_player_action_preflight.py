"""Shared deterministic preflights cannot roll, relocate, publish or suspend for a choice."""
from copy import deepcopy

import pytest

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge, PackageRuntimeError, _player_preflight
from sylvia_gm.application.player_actions import parse_player_controls
from sylvia_gm.runtime import RulesetRuntime


def compiled(effect):
    return RulesetRuntime(ruleset_dict={'tools': {
        'qualify': {'parameters': [], 'effects': [{'call': 'child', 'export_context': '*'}]},
        'child': {'parameters': [], 'effects': [effect]},
    }})


@pytest.mark.parametrize('effect', [
    {'dice': '1d20', 'store_as': '_roll'},
    {'shuffle': {'collection': '_rows'}},
    {'random_table': {'table': 'arbitrary'}},
    {'spatial_mutation': {'scene': 'state.spatial_scene'}},
    {'request_choice': {}}, {'publish': {'text': 'not permitted'}},
    {'set_participant': {'participant': 'person', 'field': 'value', 'value': 3}},
])
def test_forbidden_effect_is_rejected_before_execution_even_in_nested_call(effect, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('A preflight must never call the dice implementation')
    monkeypatch.setattr('sylvia_gm.runtime._effect_executor.dice_roll', forbidden)
    values = {'synthetic.state': {'value': 4}}
    before, rng = deepcopy(values), dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge('synthetic', values)
    with pytest.raises(PackageRuntimeError):
        _player_preflight(compiled(effect), parse_player_controls({'preflight': ['qualify']}), {}, bridge)
    assert values == before and not bridge.mutations()
    assert dice._rng.getstate() == rng


def test_deterministic_projection_stays_in_disposable_memory():
    values = {'synthetic.state': {'value': 4}}
    before = deepcopy(values)
    bridge = AuthorityMemoryStateBridge('synthetic', values)
    _player_preflight(compiled({'set': {'field': 'state.value', 'value': 7}}),
                      parse_player_controls({'preflight': ['qualify']}), {}, bridge)
    assert bridge.get_entity('state')['value'] == 7
    assert values == before


@pytest.mark.parametrize('preflight', ['tool', [None], ['bad name'], ['a'] * 5])
def test_invalid_preflight_declaration_fails_closed(preflight):
    assert not parse_player_controls({'preflight': preflight}).available


def test_player_scene_reads_and_queries_share_visibility_even_after_disposable_writes():
    from tests.test_tactical_grid_ui import _scene
    scene = _scene()
    values = {'synthetic.state': {'nested': {'scene': scene}}}
    before = deepcopy(values)
    bridge = AuthorityMemoryStateBridge('synthetic', values,
        {'person': {'data': {'scene': scene}}}, player_spatial_view=True)
    path = 'nested.scene'
    read = bridge.read_field('state', path)
    assert read == bridge.get_entity_raw('state')['nested']['scene']
    assert [row['id'] for row in read['entities']] == ['operator']
    assert [row['id'] for row in read['geometry']] == ['door-a']
    assert [row['id'] for row in read['zones']] == ['objective-zone']
    assert read == bridge._spatial_engine('state.' + path).snapshot.as_payload()
    assert bridge.get_participant('person')['data']['scene'] == read
    bridge.write_field('state', 'replacement', scene)
    assert bridge.read_field('state', 'replacement') == read
    assert bridge._spatial_engine('state.replacement').snapshot.as_payload() == read
    assert values == before
    full = AuthorityMemoryStateBridge('synthetic', values)
    assert len(full.read_field('state', path)['entities']) == 2
