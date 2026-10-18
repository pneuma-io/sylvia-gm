"""Life Bond at its real package DSL/HP/scene boundaries with synthetic bodies."""
from copy import deepcopy
import json, os, zipfile
from pathlib import Path
from random import Random

import pytest
import yaml

from scripts.srd_lite_steed_life_bond import QUALIFY, APPLY, LEVEL, bind_steed_life_bond
from scripts.srd_lite_mass_cure_geometry import _effect_lists
from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _scene_entity, _calls


@pytest.fixture(scope='module')
def bound():
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        name = next(n for n in archive.namelist() if n.endswith('/runtime/ruleset.yaml') and NAMESPACE in n)
        data = yaml.load(archive.read(name), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[p]) if isinstance(p, str) else p for p in tool['parameters']]
    original_hp = deepcopy(data['tools']['restore_hit_points'])
    original_profiles = deepcopy(data['definitions']['spells'])
    sites = bind_steed_life_bond(data)
    assert data['tools']['restore_hit_points'] == original_hp
    assert data['definitions']['spells'] == original_profiles
    # Keep the original receiving-ID repair already demonstrated by the restorative checks.
    for name in ('restore_shared_spell_healing_target', 'restore_srd_healing_pool_target'):
        for rows in _effect_lists(data['tools'][name]['effects']):
            for effect in rows:
                if effect.get('call') == 'observe_srd_astral_receiving':
                    assert effect['params']['target'] == '{_target_id}'
    closure, pending = {}, [QUALIFY, APPLY, 'restore_shared_spell_healing_target',
                            'restore_srd_healing_pool_target', 'cast_spell_healing']
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = closure
    return RulesetRuntime(ruleset_dict=subset), data, sites


def _run(bound, *, position=(10, 0, 0), caster_hp=10, caster_max=50, steed_hp=5,
         steed_max=25, plane='material', caster_changes=None, steed_changes=None,
         frames=None, level=1, amount=12, pool=False, family=False):
    parsed, data, _ = bound
    hero = _scene_entity('hero', [0, 0, 0])
    steed = _scene_entity('steed', list(position))
    steed['footprint'] = {'kind': 'box', 'half_extents': [5, 5]}
    steed['metadata']['plane'] = plane
    scene = {'format': 1, 'map': {'id': 'life-bond-fixture', 'revision': 0,
        'bounds': {'min': [-200, -200, -200], 'max': [200, 200, 200]},
        'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-200, -200]},
        'layers': [{'id': 'world', 'min_elevation': -200, 'max_elevation': 200}]},
        'entities': deepcopy(frames if frames is not None else [hero, steed]),
        'geometry': [], 'terrain': [], 'zones': []}
    for frame in scene['entities']:
        frame['map_id'] = 'life-bond-fixture'
    defaults = {'kind': 'character', 'creature_type': 'humanoid', 'hp': 10,
        'max_hp': 50, 'temp_hp': 0, 'is_alive': True, 'conditions': [],
        'death_successes': 0, 'death_failures': 0, 'stable': False,
        'stable_recovery_hours_remaining': 0, 'resources': {'spell_slot_1': 2},
        'prepared_spells': ['cure_wounds']}
    entities = {
        'hero': {**deepcopy(defaults), 'name': 'Caster', 'hp': caster_hp, 'max_hp': caster_max,
                 'created_steed_id': 'steed', **(caster_changes or {})},
        'steed': {**deepcopy(defaults), 'name': 'Otherworldly Steed', 'hp': steed_hp,
                  'max_hp': steed_max, 'creature_type': 'celestial', 'created_spell_id': 'find_steed',
                  'created_caster_state_id': 'hero', 'created_active': True, **(steed_changes or {})}}
    state = {NAMESPACE + '.state': {'lasting_tick': 0, 'turn_effects': [], 'spatial_scene': scene},
             NAMESPACE + '.entities': entities, NAMESPACE + '.combat': {}}
    before = json.dumps(state, sort_keys=True)
    rng_before = dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    def nested(name, parameters, parent):
        return execute_tool(name, parameters, parsed.tools, bridge, parsed.ruleset_config,
                            parsed.definitions, tool_executor=nested, budget=parent.budget)
    name = 'restore_srd_healing_pool_target' if pool else 'restore_shared_spell_healing_target'
    params = {'target': 'hero', 'amount': amount, LEVEL: level}
    if pool:
        params.update(ends_blinded=False, ends_deafened=False, ends_poisoned=False)
    if family:
        name = 'cast_spell_healing'
        profile = data['definitions']['spells']['cure_wounds']
        params = {key: value for key, value in profile.items()
                  if key in {p['name'] for p in data['tools'][name]['parameters']}}
        params.update(spell='cure_wounds', caster_state='hero', target='hero',
            slot_level=1, slot_resource='spell_slot_1', casting_ability='wisdom',
            casting_modifier=4, casting_attack_bonus=8, casting_save_dc=16,
            casting_scaling_level=9, casting_concentration_save_modifier=2,
            target_distance=0, caster_can_see_target=True, caster_can_touch_target=True,
            cover='none')
    result = execute_tool(name, params, parsed.tools, bridge, parsed.ruleset_config,
                          parsed.definitions, tool_executor=nested, budget=ExecutionBudget())
    if not family:
        assert dice._rng.getstate() == rng_before, 'Life Bond must reuse the spell gain without new dice'
    assert bridge.get_entity('hero')['resources']['spell_slot_1'] == 2
    assert bridge.combat_state == {}
    assert state[NAMESPACE + '.state']['spatial_scene'] == scene
    if not result.success:
        assert json.dumps(state, sort_keys=True) == before, 'Qualification must precede HP changes'
    return result, bridge


@pytest.mark.parametrize('pool', [False, True])
def test_same_actual_hp_gain_and_receipt_in_each_recipient_owner(bound, pool):
    result, bridge = _run(bound, pool=pool)
    assert result.success, result.error
    assert bridge.get_entity('hero')['hp'] == 22
    assert bridge.get_entity('steed')['hp'] == 17
    rows = result.context['_spell_detail_rows']
    assert any('Life Bond' in row['value'] and 'caster regained 12 HP' in row['value']
               and 'steed restored 12 HP' in row['value'] for row in rows)


@pytest.mark.parametrize('caster_hp,steed_hp,expected_caster,expected_steed', [
    (49, 5, 50, 6), (10, 24, 22, 25), (50, 5, 50, 5)])
def test_caps_use_actual_gain_and_native_steed_maximum(bound, caster_hp, steed_hp,
                                                     expected_caster, expected_steed):
    result, bridge = _run(bound, caster_hp=caster_hp, steed_hp=steed_hp)
    assert result.success, result.error
    assert bridge.get_entity('hero')['hp'] == expected_caster
    assert bridge.get_entity('steed')['hp'] == expected_steed


@pytest.mark.parametrize('caster_kind,steed_kind', [('box', 'box'), ('box', 'circle'),
                                                 ('circle', 'box'), ('circle', 'circle')])
def test_exact_five_foot_body_boundary_for_each_admitted_footprint_pair(bound, caster_kind, steed_kind):
    frames = [_scene_entity('hero', [0, 0, 0]), _scene_entity('steed', [10.4, 0, 0])]
    for frame, kind, radius in zip(frames, (caster_kind, steed_kind), (0.4, 5)):
        frame['footprint'] = ({'kind': 'circle', 'radius': radius} if kind == 'circle'
                              else {'kind': 'box', 'half_extents': [radius, radius]})
    result, bridge = _run(bound, frames=frames)
    assert result.success, result.error
    assert bridge.get_entity('steed')['hp'] == 17


@pytest.mark.parametrize('changes,target', [
    ({'hit_point_recovery_blocked': True}, 'caster'),
    ({'hit_point_recovery_blocked': True}, 'steed')])
def test_original_healing_prohibitions_are_preserved(bound, changes, target):
    result, bridge = _run(bound, **{target + '_changes': changes})
    assert result.success, result.error
    assert bridge.get_entity('hero')['hp'] == (10 if target == 'caster' else 22)
    assert bridge.get_entity('steed')['hp'] == 5


@pytest.mark.parametrize('position,plane', [((12.5001, 0, 0), 'material'),
    ((10, 0, 0), 'ethereal'), ((0, 0, 11.0001), 'material')])
def test_outside_body_range_or_plane_does_not_share_healing(bound, position, plane):
    result, bridge = _run(bound, position=position, plane=plane)
    assert result.success, result.error
    assert bridge.get_entity('hero')['hp'] == 22
    assert bridge.get_entity('steed')['hp'] == 5


def test_level_zero_and_unlinked_healing_leave_steed_untouched(bound):
    for params in ({'level': 0}, {'caster_changes': {'created_steed_id': None}}):
        result, bridge = _run(bound, **params)
        assert result.success, result.error
        assert bridge.get_entity('hero')['hp'] == 22
        assert bridge.get_entity('steed')['hp'] == 5


@pytest.mark.parametrize('changes', [
    {'created_caster_state_id': 'someone_else'}, {'created_active': False},
    {'created_spell_id': 'animate_dead'}, {'is_alive': False}])
def test_stale_or_foreign_steed_rejects_before_healing(bound, changes):
    result, bridge = _run(bound, steed_changes=changes)
    assert not result.success and 'actual active Otherworldly Steed' in result.error
    assert bridge.get_entity('hero')['hp'] == 10


def test_missing_active_body_rejects_before_healing(bound):
    result, bridge = _run(bound, frames=[_scene_entity('hero', [0, 0, 0])])
    assert not result.success and 'no active admitted scene entity' in result.error
    assert bridge.get_entity('hero')['hp'] == 10


def test_foreign_scene_identity_rejects_before_healing(bound):
    frames = [_scene_entity('hero', [0, 0, 0]), _scene_entity('steed', [10, 0, 0])]
    frames[1]['metadata']['participant_ref'] = 'someone_else'
    result, bridge = _run(bound, frames=frames)
    assert not result.success and 'scene body bound to this actual creature' in result.error
    assert bridge.get_entity('hero')['hp'] == 10


def test_native_healing_callers_transport_only_actual_spell_levels(bound):
    _, data, sites = bound
    assert len(sites) == 9
    for owner, receiver in (
        ('cast_spell_instantaneous_multi_target_healing', 'restore_shared_spell_healing_target'),
        ('cast_spell_instantaneous_healing_pool', 'restore_srd_healing_pool_target')):
        calls = [e for rows in _effect_lists(data['tools'][owner]['effects']) for e in rows
                 if e.get('call') == receiver]
        assert len(calls) == 1 and calls[0]['params'][LEVEL] == '{slot_level}'
    for name in ('complete_srd_rest', 'use_srd_second_wind', 'death_saving_throw'):
        assert QUALIFY not in tuple(_calls(data['tools'][name]['effects']))
    assert APPLY not in tuple(_calls(data['tools']['restore_hit_points']['effects']))


def test_actual_cure_family_retains_one_native_roll_and_both_healing_receipts(bound, monkeypatch):
    from sylvia_gm.runtime import _effect_executor
    native_roll = _effect_executor.dice_roll
    rolls = []
    def recorded(*args, **kwargs):
        roll = native_roll(*args, **kwargs)
        rolls.append(roll)
        return roll
    monkeypatch.setattr(dice, '_rng', Random(0))
    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    result, bridge = _run(bound, family=True, caster_hp=49)
    assert result.success, result.error
    assert len(rolls) == 1 and rolls[0].sides == 8 and rolls[0].count == 2
    assert rolls[0].results == (7, 7) and rolls[0].total == 14
    assert result.context['_spell_healing_amount'] == 18
    assert bridge.get_entity('hero')['hp'] == 50
    assert bridge.get_entity('steed')['hp'] == 6
    rows = result.context['_spell_detail_rows']
    assert len(rows) == 2
    assert any('Life Bond: caster regained 1 HP' in row['value'] for row in rows)
    assert any('Healing dice' in row['value'] and '= 18 before the HP cap' in row['value'] for row in rows)


def test_actual_healing_card_projects_life_bond_and_original_dice_without_extra_roll(bound, monkeypatch):
    from scripts.srd_lite_concentration_totals import bind_concentration_roll_totals
    from scripts.srd_lite_concentration_cards import bind_area_concentration_cards
    from scripts.srd_lite_visual_rolls import bind_recorded_spell_roll_widgets
    from sylvia_gm.runtime._effect_executor import execute_effects
    from sylvia_gm.runtime._types import ExecutionContext
    from sylvia_gm.application.result_cards import project_result_card
    from sylvia_gm.application.result_card_admission import validate_result_card_declaration
    _, data, _ = bound
    monkeypatch.setattr(dice, '_rng', Random(0))
    result, bridge = _run(bound, family=True, caster_hp=49)
    assert result.success, result.error
    before_projection = dice._rng.getstate()
    bind_concentration_roll_totals(data)
    bind_area_concentration_cards(data)
    bind_recorded_spell_roll_widgets(data)
    root = data['tools']['cast_spell']
    pool = next(effect for rows in _effect_lists(data['tools']['finish_srd_retained_cast_tail']['effects'])
                for effect in rows if effect.get('list_push', {}).get('value', {}).get('value')
                == '_spell_healing.results')
    frame = ExecutionContext(params={}, vars={**result.context, '_spell_display_name': 'Cure Wounds',
        '_spell_card_summary': deepcopy(result.context['_spell_summary_rows'])})
    execute_effects([pool, *root['effects'][-2:]], frame)
    declaration = root['metadata']['result_card']
    validate_result_card_declaration(declaration)
    card = project_result_card(declaration, parameters={}, public_context=frame.vars)
    assert card is not None
    equation = next(row['equation'] for row in card['summary'] if 'equation' in row)
    assert equation == {'die_sides': 8, 'rolls': [7, 7], 'roll': 14, 'modifier': 4, 'total': 18}
    assert any('Life Bond' in row['value'] and 'HP restored 1' in row['value'] for row in card['summary'])
    assert any('caster regained 1 HP' in row['value'] for row in card['details'])
    assert dice._rng.getstate() == before_projection
    assert bridge.get_entity('hero')['hp'] == 50 and bridge.get_entity('steed')['hp'] == 6
    if os.environ.get('SRD_LITE_STEED_CARD'):
        Path(os.environ['SRD_LITE_STEED_CARD']).write_text(json.dumps(card, indent=2), encoding='utf-8')
