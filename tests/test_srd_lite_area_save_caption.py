"""Name recorded area-spell saves without rerolls, HP changes or receipt rewriting."""
from copy import deepcopy
import json
import os
import zipfile

import pytest
import yaml

from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_vertical_cones import run, state_for
from scripts.srd_lite_visual_rolls import _lists


@pytest.fixture(scope='module')
def data():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        value = yaml.load(archive.read('packages/sylvia.srd-lite.bg3/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    if os.environ.get('SRD_LITE_PATCH_AREA_CAPTION') == '1':
        from scripts.srd_lite_visual_rolls import bind_area_save_source_caption
        bind_area_save_source_caption(value)
    return value


def test_every_selected_spell_has_its_authored_display_name(data):
    names = data['definitions']['srd_lite_spell_display_names']
    selected = data['definitions']['srd_lite_catalogue']
    assert len(selected) == 96
    assert all(isinstance(names[spell], str) and names[spell].strip() for spell in selected)


@pytest.mark.parametrize(('spell', 'damage_type'), [
    ('guardian_of_faith', 'radiant'), ('cone_of_cold', 'cold'), ('fire_storm', 'fire')])
def test_saved_native_d20_keeps_its_values_under_named_caption(data, spell, damage_type):
    evidence = json.loads(open(os.environ['SRD_LITE_NATIVE_GUARDIAN_RECEIPT'], encoding='utf-8').read())
    assert evidence['actual_saved_d20_and_damage_card_checked'] and evidence['slot_not_repaid']
    saved = evidence['save']
    area = data['tools']['resolve_spell_area_save_damage_target']
    name = next(deepcopy(effect) for effect in area['effects']
                if effect.get('set', {}).get('field') == '_visual_damage_spell_name')
    branches = [deepcopy(effect) for rows in _lists(area['effects']) for effect in rows
                if effect.get('condition') == '_card_d20_roll != null'
                and any(child.get('set', {}).get('field') == '_visual_save_row'
                        for child in effect.get('on_true', []))]
    assert len(branches) == 2 and branches[0] == branches[1]
    fields = {'_card_d20_roll': {'total': saved['roll']},
        '_card_d20_modifier': saved['modifier'], '_card_d20_total': saved['total'],
        '_spell_save_outcome': "'failure'", '_visual_save_row': None}
    pool = data['parameter_declarations']
    parameters = [deepcopy(pool[row]) if isinstance(row, str) else deepcopy(row) for row in area['parameters']]
    parameters = [row for row in parameters if row['name'] in
                  {'spell', 'target', 'save_ability', 'spell_save_dc', 'damage_type'}]
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = {'area_caption_fixture': {
        'description': 'Read-only projection of an already committed saving throw.',
        'parameters': parameters,
        'effects': [{'set': {'field': field, 'value': value}} for field, value in fields.items()] + [name, branches[0]]},
        'format_srd_information_projection': data['tools']['format_srd_information_projection']}
    parsed = RulesetRuntime(ruleset_dict=subset)
    result = run(parsed, state_for([0, 0, 0]), 'area_caption_fixture', {
        'spell': spell, 'target': 'guard', 'save_ability': 'dexterity',
        'spell_save_dc': 18, 'damage_type': damage_type})
    assert result.success, result.error
    row = result.context['_visual_save_row']
    assert row['equation'] == saved
    assert row['label'] == 'guard'
    assert row['value'] == (data['definitions']['srd_lite_spell_display_names'][spell] + ' · ' +
                           damage_type + ' damage · dexterity saving throw · DC 18 · failure')
