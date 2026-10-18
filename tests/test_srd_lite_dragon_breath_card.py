"""Project the genuine retained breath pool without rerolling or hiding source penalties."""
from copy import deepcopy
import json
import os
import zipfile

import pytest
import yaml

from sylvia_gm import dice
from sylvia_gm.application.result_cards import project_result_card


@pytest.fixture(scope='module')
def declaration():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        runtime = yaml.load(archive.read('packages/sylvia.srd-lite.bg3/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    if os.environ.get('SRD_LITE_PATCH_BREATH_CARD') == '1':
        from scripts.srd_lite_visual_rolls import _bind_dragon_breath_damage_pool
        _bind_dragon_breath_damage_pool(runtime)
    return runtime['tools']['use_srd_dragon_breath']['metadata']['result_card']


@pytest.mark.parametrize(('faces', 'adjusted', 'has_target'), [
    ([5, 4, 5], 14, True), ([5, 4, 5], 9, True), ([5, 4, 5], 19, True),
    ([1, 2, 3, 4, 5, 6, 1, 2, 3], 27, True), ([5, 4, 5], 14, False),
])
def test_native_pool_and_defender_equation_stay_separate(declaration, faces, adjusted, has_target):
    evidence = json.loads(open(os.environ['SRD_LITE_NATIVE_BREATH_RECEIPT'], encoding='utf-8').read())
    assert evidence['breath_action_committed'] and evidence['full_audit_enabled_before_actions']
    target_rows = evidence['card']['details'] if has_target else []
    equations = evidence['card']['equations'] if has_target else []
    context = {'_breath_source_id': evidence['paid_sources'][0]['source_id'],
        '_breath_damage_type': 'fire', '_breath_save_dc': 18,
        '_breath_damage_dice': faces, '_breath_raw_damage': sum(faces),
        '_breath_damage_total': adjusted, '_breath_detail_rows': target_rows,
        '_spell_featured_equations': equations}
    before, rng = deepcopy(context), dice._rng.getstate()
    projected = project_result_card(declaration, parameters={}, public_context=context)
    assert projected is not None
    assert projected['subtitle'] == 'fire'
    damage = next(row for row in projected['summary'] if row['label'] == 'Raw damage')
    assert damage['equation'] == {'die_sides': 6, 'rolls': faces, 'roll': sum(faces),
                                  'modifier': 0, 'total': sum(faces)}
    assert next(row for row in projected['summary'] if row['label'] == 'Damage before defenses')['value'] == adjusted
    assert projected['equations'] == equations
    assert context == before and dice._rng.getstate() == rng
