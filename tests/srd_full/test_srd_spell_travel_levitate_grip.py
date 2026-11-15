"""Actual Levitate surface contact preserves shared climbing, body clearance and entry timing."""

from copy import deepcopy
from dataclasses import replace

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_travel import SRD, _case, _point, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel_levitate import _install, _levitate_case


def _grip_case(spell='misty_step', *, rise=20, install=True):
    state, data = _levitate_case(spell)
    wall = deepcopy(state[f'{SRD}.state']['spatial_scene']['geometry'][0])
    wall.update(id='grip-wall', volume={'kind': 'box', 'min': [1, -50, 0],
                                      'max': [2, 50, 60]})
    wall['metadata']['ground'] = False
    state[f'{SRD}.state']['spatial_scene']['geometry'].append(wall)
    if install:
        _install(state, 'hero', rise=rise)
    return state, data


def _source_id(state):
    return state[f'{SRD}.state']['levitate_sources'][-1]['id']


def _contact(state, **changes):
    params = {'actor_state': 'hero', 'source_id': _source_id(state),
              'surface_id': 'grip-wall', 'face': 'west', 'contact_point': [1, 0, 22],
              'contact_within_reach': True}
    params.update(changes)
    return _run('record_srd_levitate_surface_grip', state, **params)


def _move(state, destination):
    return _run('move_srd_levitate_grip', state, actor_state='hero',
                source_id=_source_id(state), destination=destination)


def test_levitate_real_surface_contact_moves_with_shared_climbing_cost_and_retains_contact():
    state, _ = _grip_case()
    _contact(state)
    _move(state, [0, 5, 20])
    assert _point(state) == [0, 5, 20]
    source = state[f'{SRD}.state']['levitate_sources'][-1]
    assert source['grip']['contact'] == [1, 5, 22]
    assert source['grip']['origin'] == [0, 5, 20]
    assert source['grip_movement'] is None and source['movement_destination'] is None
    assert state[f'{SRD}.entities']['hero']['scene_movement_spent'] == 10
    _move(state, [0, 5, 25])
    assert _point(state) == [0, 5, 25]
    assert state[f'{SRD}.state']['levitate_sources'][-1]['grip']['contact'] == [1, 5, 27]
    assert state[f'{SRD}.entities']['hero']['scene_movement_spent'] == 20


@pytest.mark.parametrize('climb_speed,expected', [(0, 10), (20, 5)])
def test_levitate_grip_uses_real_climb_speed_or_shared_extra_climbing_cost(climb_speed, expected):
    state, _ = _grip_case()
    state[f'{SRD}.entities']['hero']['climb_speed'] = climb_speed
    _contact(state)
    _move(state, [0, 5, 20])
    assert _point(state) == [0, 5, 20]
    assert state[f'{SRD}.entities']['hero']['scene_movement_spent'] == expected


def test_levitate_grip_difficult_surface_route_cost_uses_actual_saved_zone():
    state, _ = _grip_case()
    scene = state[f'{SRD}.state']['spatial_scene']
    difficult = deepcopy(scene['zones'][0])
    difficult.update(id='difficult-surface',
                     shape={'kind': 'box', 'min': [-1, -1, 0], 'max': [1, 10, 60]})
    difficult['metadata']['difficult'] = True
    difficult['metadata']['plane'] = 'material'
    scene['zones'].append(difficult)
    _contact(state)
    _move(state, [0, 5, 20])
    assert state[f'{SRD}.entities']['hero']['scene_movement_spent'] == 15


def test_levitate_grip_actual_body_sweep_rejects_later_obstacle_without_cost_or_authorization():
    state, _ = _grip_case()
    _contact(state)
    scene = state[f'{SRD}.state']['spatial_scene']
    obstacle = deepcopy(scene['geometry'][0])
    obstacle.update(id='grip-obstacle',
                    volume={'kind': 'box', 'min': [-1, 2, 19], 'max': [1, 3, 26]})
    scene['geometry'].append(obstacle)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match='barrier'):
        _move(state, [0, 5, 20])
    assert state == before


def test_levitate_grip_actual_grease_entry_commits_boundary_before_failed_save(monkeypatch):
    state, data = _grip_case('grease', rise=0)
    state[f'{SRD}.entities']['hero'].setdefault('saving_throw_modifiers', {})['dexterity'] = (
        data['abilities']['dexterity'] - 10) // 2
    _contact(state, contact_point=[1, 0, 2])
    _, _, params = _case('grease')
    params['environment_origin'] = [0, 15, 0]
    actual = _TravelDice([(20, 1)])
    monkeypatch.setattr(dice, '_rng', actual)
    monkeypatch.setattr(dice, 'set_seed', lambda _seed: None)
    _run('cast_spell_outside_combat', state, participant_data=data, **params)
    _move(state, [0, 15, 0])
    assert _point(state) == [0, 10, 0]
    assert 'prone' in state[f'{SRD}.entities']['hero']['conditions']
    assert state[f'{SRD}.entities']['hero']['scene_movement_spent'] == 20
    source = state[f'{SRD}.state']['levitate_sources'][-1]
    assert source['grip']['contact'] == [1, 10, 2]
    assert source['movement_destination'] is None and source['grip_movement'] is None
    assert actual.consumed == [(20, 1)]


def test_levitate_grip_actual_cloud_body_cannot_manipulate_fixed_surface():
    state, _ = _grip_case()
    _run('cast_spell_transformation', state, caster_state='guard', lasting_state='state',
         spell='gaseous_form', actual_spell_level=3, save_dc=13, casting_modifier=3,
         casting_ability='wisdom', targets=['hero'], target_state='hero',
         concentration_save_modifier=0,
         target_distance=5, caster_can_touch_target=True, caster_can_touch_all_targets=True,
         all_targets_willing=True, target_willing=True)
    assert state[f'{SRD}.entities']['hero']['form_cloud'] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match='cloud'):
        _contact(state)
    assert state == before


def test_public_levitate_grip_paid_cast_actual_contact_climbing_reopens_and_retries(
    tmp_path, monkeypatch
):
    import tests.srd_full.test_srd_public_spell_casting as public

    def actual_case(_spell):
        state, data = _grip_case('levitate', install=False)
        _, _, params = _case('levitate')
        params.pop('targets', None)
        params.update(levitate_subject='hero', levitate_rise=20, all_targets_willing=True)
        return state, data, params

    monkeypatch.setattr(public, '_spell_case', actual_case)
    database = tmp_path / 'levitate-grip.db'
    with AuthorityOwner.open(database, 'casting-owner') as owner:
        _, _, params = public._create_spell_campaign(owner, 'levitate')
        cast = ActionCommand(
            campaign_id='casting', action_id='levitate', idempotency_key='levitate',
            owner_id='casting-owner', writer=WriterRole.DM, expected_revision=0,
            turn_id='casting-turn', package_id=SRD, tool_name='cast_spell_outside_combat',
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        mechanics.execute_action(cast)
        current = owner.snapshot('casting')
        source_id = _source_id(current.state)
        contact = replace(cast, action_id='contact', idempotency_key='contact',
                          expected_revision=current.revision,
                          tool_name='record_srd_levitate_surface_grip', parameters={
                              'actor_state': 'hero', 'source_id': source_id,
                              'surface_id': 'grip-wall', 'face': 'west',
                              'contact_point': [1, 0, 22], 'contact_within_reach': True,
                          })
        receipt = mechanics.execute_action(contact)
        assert mechanics.execute_action(contact) == receipt
        current = owner.snapshot('casting')
        movement = replace(contact, action_id='grip-move', idempotency_key='grip-move',
                           expected_revision=current.revision,
                           tool_name='move_srd_levitate_grip', parameters={
                               'actor_state': 'hero', 'source_id': source_id,
                               'destination': [0, 5, 20],
                           })
        receipt = mechanics.execute_action(movement)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(movement) == receipt
        saved = owner.snapshot('casting')
        assert _point(saved.state) == [0, 5, 20]
        assert saved.state[f'{SRD}.entities']['hero']['scene_movement_spent'] == 10
        assert saved.state[f'{SRD}.entities']['hero']['resources']['spell_slot_2'] == 2
    with AuthorityOwner.open(database, 'casting-owner') as owner:
        assert owner.snapshot('casting').state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        before = owner.snapshot('casting')
        with pytest.raises(MechanicsRejected, match='rectangle'):
            mechanics.execute_action(replace(
                movement, action_id='detach', idempotency_key='detach',
                expected_revision=before.revision,
                parameters={**movement.parameters, 'destination': [-5, 5, 20]},
            ))
        assert owner.snapshot('casting').state == before.state
        upward = replace(movement, action_id='upward', idempotency_key='upward',
                         expected_revision=before.revision,
                         parameters={**movement.parameters, 'destination': [0, 5, 25]})
        receipt = mechanics.execute_action(upward)
        assert mechanics.execute_action(upward) == receipt
        current = owner.snapshot('casting')
        assert _point(current.state) == [0, 5, 25]
        assert current.state[f'{SRD}.entities']['hero']['scene_movement_spent'] == 20
        ending = replace(cast, action_id='end', idempotency_key='end',
                         expected_revision=current.revision, tool_name='end_concentration',
                         parameters={'caster_state': 'hero'})
        receipt = mechanics.execute_action(ending)
        assert mechanics.execute_action(ending) == receipt
        after = owner.snapshot('casting')
        assert _point(after.state) == [0, 5, 0]
        assert after.state[f'{SRD}.entities']['hero']['scene_movement_spent'] == 20
        assert after.state[f'{SRD}.entities']['hero']['resources']['spell_slot_2'] == 2


@pytest.mark.parametrize('defect', ['fiction', 'surface', 'plane', 'passable', 'face', 'off-face'])
def test_levitate_grip_qualification_rejects_actual_invalid_contact_without_mutation(defect):
    state, _ = _grip_case()
    scene = state[f'{SRD}.state']['spatial_scene']
    wall = next(row for row in scene['geometry'] if row['id'] == 'grip-wall')
    params = {}
    if defect == 'fiction':
        params['contact_within_reach'] = False
    elif defect == 'surface':
        wall['active'] = False
    elif defect == 'plane':
        wall['metadata']['plane'] = 'ethereal'
    elif defect == 'passable':
        wall['movement'] = 'passable'
    elif defect == 'face':
        params['face'] = 'east'
    else:
        params['contact_point'] = [1, 51, 22]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _contact(state, **params)
    assert state == before


@pytest.mark.parametrize('defect', ['diagonal', 'detach', 'edge', 'body', 'surface', 'ordinary'])
def test_levitate_grip_later_invalid_motion_preserves_actual_position_and_budget(defect):
    state, _ = _grip_case()
    _contact(state)
    scene = state[f'{SRD}.state']['spatial_scene']
    destination = [0, 5, 20]
    if defect == 'diagonal':
        destination = [0, 5, 25]
    elif defect == 'detach':
        destination = [-5, 0, 20]
    elif defect == 'edge':
        destination = [0, 51, 20]
    elif defect == 'body':
        next(row for row in scene['entities'] if row['id'] == 'hero')['height'] += 1
    elif defect == 'surface':
        next(row for row in scene['geometry'] if row['id'] == 'grip-wall')['volume']['max'][1] = 60
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        if defect == 'ordinary':
            _run('move_srd_scene_creature', state, actor_state='hero',
                 destination=destination, mode='climb')
        else:
            _move(state, destination)
    assert state == before
