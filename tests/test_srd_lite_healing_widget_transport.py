"""Native final-cast transport preserves actual healing dice, modifier and amount."""
from copy import deepcopy
import os, zipfile
from pathlib import Path

import pytest
import yaml

from scripts.srd_lite_visual_rolls import _capture_healing_totals, _lists
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._types import ExecutionContext
from sylvia_gm.application.result_cards import project_result_card


@pytest.fixture(scope='module')
def native():
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        data = yaml.load(archive.read('packages/sylvia.srd-lite.bg3/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    root = data['tools']['cast_spell']
    capture = next(e for rows in _lists(root['effects']) for e in rows
        if e.get('set', {}).get('field') == '_retained_cast_tail_context')
    tail = data['tools']['finish_srd_retained_cast_tail']
    restore = next(e for e in tail['effects']
        if e.get('for_each', {}).get('collection') == '_retained_cast_tail_context')
    pool = next(e for rows in _lists(tail['effects']) for e in rows
        if e.get('list_push', {}).get('list') == '_spell_card_summary' and
        e['list_push']['value'].get('value') == '_spell_healing.results')
    return data, capture, restore, pool


def _project(native, *, repaired, modifier=4, amount=32):
    data, original_capture, restore, pool = deepcopy(native)
    if repaired:
        _capture_healing_totals(data)
    capture = next(e for rows in _lists(data['tools']['cast_spell']['effects']) for e in rows
        if e.get('set', {}).get('field') == '_retained_cast_tail_context')
    wanted = {'_spell_healing', '_spell_healing_modifier', '_spell_healing_amount'}
    rows = [row for row in capture['set']['value'] if row['key'][1:-1] in wanted]
    parent = ExecutionContext(vars={'_spell_healing':{'sides':8, 'count':5,
        'results':[7,7,1,5,8], 'total':28, 'modifier':0},
        '_spell_healing_modifier':modifier, '_spell_healing_amount':amount})
    execute_effects([{'set':{'field':'_retained_cast_tail_context', 'value':rows}}], parent)
    frame = ExecutionContext(params={'_retained_cast_tail_context':parent.vars['_retained_cast_tail_context']},
        vars={'_spell_card_summary':[], '_spell_detail_rows':[], '_spell_featured_equations':[],
              '_spell_display_name':'Mass Cure Wounds'})
    execute_effects([restore, pool], frame)
    card = project_result_card(data['tools']['cast_spell_outside_combat']['metadata']['result_card'],
        parameters={}, public_context=frame.vars)
    return card, parent.vars, frame.vars


def test_uncaptured_native_healing_totals_reproduce_missing_card(native):
    card, _, frame = _project(native, repaired=False)
    assert frame['_spell_card_summary'][0]['equation']['modifier'] == '_spell_healing_modifier'
    assert frame['_spell_card_summary'][0]['equation']['total'] == '_spell_healing_amount'
    assert card is None


@pytest.mark.parametrize(('modifier', 'amount'), ((4,32), (0,28), (-2,26)))
def test_original_d8_faces_and_native_totals_survive_final_cast_transport(native, modifier, amount):
    card, parent, frame = _project(native, repaired=True, modifier=modifier, amount=amount)
    assert card is not None
    equation = card['summary'][0]['equation']
    assert equation == {'die_sides':8, 'rolls':[7,7,1,5,8], 'roll':28,
                        'modifier':modifier, 'total':amount}
    assert frame['_spell_healing'] == parent['_spell_healing']


def test_healing_capture_preserves_all_original_runtime_owners(native):
    data = deepcopy(native[0])
    before = deepcopy(data)
    assert _capture_healing_totals(data) == ('_spell_healing_modifier', '_spell_healing_amount')
    capture = next(e for rows in _lists(data['tools']['cast_spell']['effects']) for e in rows
        if e.get('set', {}).get('field') == '_retained_cast_tail_context')
    del capture['set']['value'][-2:]
    assert data == before
