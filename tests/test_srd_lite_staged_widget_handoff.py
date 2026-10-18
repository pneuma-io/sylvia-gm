"""Check native staged-card handoffs without repeating paid mechanics or save/load."""
from copy import deepcopy
import os, zipfile
from pathlib import Path
import pytest
import yaml
from scripts.srd_lite_visual_rolls import _preserve_slow_null_roll, _feature_recorded_equations, _lists
from sylvia_gm import dice
from sylvia_gm.runtime._types import ExecutionContext
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.application.result_cards import project_result_card


@pytest.fixture(scope='module')
def native():
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        return yaml.load(archive.read('packages/sylvia.srd-lite.bg3/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)


def test_native_absent_saved_slow_roll_reproduces_unresolved_text(native):
    leaf = native['tools']['resolve_srd_slow_cast_failure']
    restore = next(e for rows in _lists(leaf['effects']) for e in rows
        if e.get('set') == {'field':'_slow_cast_failure_roll','value':'_slow_cast_event.slow_cast.roll'})
    frame = ExecutionContext(vars={'_slow_cast_failure_roll':None,'_slow_cast_event':{'slow_cast':{'roll':None}}})
    execute_effects([restore],frame)
    assert frame.vars['_slow_cast_failure_roll'] == '_slow_cast_event.slow_cast.roll'


@pytest.mark.parametrize('roll',(None,{'sides':4,'count':1,'results':[1],'total':1,'modifier':0},
    {'sides':4,'count':1,'results':[4],'total':4,'modifier':0}))
def test_saved_slow_roll_preserves_null_or_original_dice_without_rng(native,roll):
    data = deepcopy(native)
    _preserve_slow_null_roll(data)
    failed = roll is not None and roll['total'] == 1
    event = {'kind':'spell_cast','actor_state_id':'hero','parameters':{'spell':'fire_storm'},
        'slow_cast':{'roll':deepcopy(roll),'failed':failed}}
    frame = ExecutionContext(params={'spell':'fire_storm','has_somatic_component':True,
        'pending_action_id':'cast','caster_state':{'spell_slow':True},
        'lasting_state':{'pending_srd_actions':{'cast':event}}},vars={'_caster_state_id':'hero'})
    before = dice._rng.getstate()
    execute_effects(data['tools']['resolve_srd_slow_cast_failure']['effects'],frame)
    assert dice._rng.getstate() == before
    assert frame.vars['_slow_cast_failure_roll'] == roll
    assert frame.vars['_slow_cast_failed'] is failed
    assert frame.vars['_slow_cast_cached'] is True
    assert frame.params['lasting_state']['pending_srd_actions']['cast'] == event


def test_completed_card_features_actual_save_without_changing_damage_or_receipts(native):
    owner = deepcopy(native['tools']['complete_srd_pending_action'])
    old_effects = deepcopy(owner['effects'])
    assert _feature_recorded_equations(owner,'complete_srd_pending_action')
    assert owner['effects'][:-2] == old_effects
    damage = {'label':'Damage dice','value':[1,5,9,8,7,5,8],
        'equation':{'die_sides':10,'rolls':[1,5,9,8,7,5,8],'roll':43,'modifier':0,'total':43}}
    save = {'label':'guard','value':'dexterity saving throw · DC 18 · failure',
        'equation':{'die_sides':20,'roll':12,'modifier':0,'total':12}}
    frame = ExecutionContext(vars={'_pending_card_title':'Fire Storm',
        '_pending_card_summary':[damage], '_pending_card_details':[save]})
    execute_effects(owner['effects'][-2:],frame)
    card = project_result_card(owner['metadata']['result_card'], parameters={},public_context=frame.vars)
    assert card is not None
    assert card['summary'][0]['equation'] == damage['equation']
    assert card['equations'][0]['equation'] == save['equation']
    assert frame.vars['_pending_card_details'] == [save]


def test_complete_equation_preview_retains_existing_four_row_bound(native):
    owner = deepcopy(native['tools']['complete_srd_pending_action'])
    _feature_recorded_equations(owner,'complete_srd_pending_action')
    rows = [{'label':str(i),'value':'Recorded save','equation':{'die_sides':20,'roll':i+1,'modifier':0,'total':i+1}}
        for i in range(6)]
    frame = ExecutionContext(vars={'_pending_card_details':deepcopy(rows)})
    execute_effects(owner['effects'][-2:],frame)
    assert frame.vars['_spell_featured_equations'] == rows[:4]
    assert frame.vars['_pending_card_details'] == rows
