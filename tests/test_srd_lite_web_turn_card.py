"""Prove current native Web receipts render faithfully without changing state or rolling again."""
from copy import deepcopy
import json
import os
from pathlib import Path
import zipfile

import pytest
import yaml

from scripts.srd_lite_web_turn_card import _feedback_effects
from scripts.srd_lite_visual_rolls import _feature_recorded_equations
from scripts.srd_spell_environment import _environment_tool, _set
from scripts.srd_timed_actor_defenses import _entity
from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget

NAMESPACE = 'sylvia.srd-lite.bg3'


@pytest.fixture(scope='module')
def native_state():
    database = Path(os.environ['SRD_LITE_WEB_BURN_DATABASE'])
    with AuthorityOwner.open(database, 'web-turn-display-read-only') as owner:
        snapshot = owner.snapshot('burn')
    state = thaw_json_value(snapshot.state)
    actor, source = state[NAMESPACE + '.entities']['guard'], state[NAMESPACE + '.state']['persistent_spell_areas']['hero']
    # The older native receipts have no display totals. These explicit fixture
    # additions exercise projection; a fresh native run proves their new capture.
    actor['last_web_fire']['damage_roll_total'] = actor['last_web_fire']['raw_damage']['total']
    actor['last_web_save'].update(modifier=0, total=actor['last_web_save']['roll']['total'], save_dc=source['save_dc'])
    return state


@pytest.fixture(scope='module')
def projection_runtime():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        data = yaml.load(archive.read('packages/' + NAMESPACE + '/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    owner = _environment_tool('Read the actual current turn receipts.', [_entity('actor')],
        [_set('_turn_effect_rows', []), _set('_turn_effect_count', 0),
         _set('_turn_effect_damage_applied', 0), *_feedback_effects()])
    owner['metadata']['result_card'] = {'version': 1, 'title': 'Turn started', 'summary': [],
        'details': {'path': 'context._turn_effect_rows'}}
    _feature_recorded_equations(owner, 'web_feedback')
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = {'web_feedback': owner,
        'format_srd_information_projection': data['tools']['format_srd_information_projection']}
    return RulesetRuntime(ruleset_dict=subset)


def project(runtime, state):
    before, rng = json.dumps(state, sort_keys=True), dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    def invoke(name, parameters, parent=None):
        return execute_tool(name, parameters, runtime.tools, bridge, runtime.ruleset_config,
            runtime.definitions, combat_state=state[NAMESPACE + '.combat'], tool_executor=invoke,
            budget=parent.budget if parent is not None else ExecutionBudget())
    result = invoke('web_feedback', {'actor': 'guard'})
    assert result.success, result.error
    assert json.dumps(state, sort_keys=True) == before and dice._rng.getstate() == rng
    assert not bridge.mutations() and not result.state_changes
    assert result.context['_spell_featured_equations'] == result.context['_turn_effect_rows']
    assert result.context['_turn_effect_count'] == len(result.context['_turn_effect_rows'])
    expected_damage = state[NAMESPACE + '.entities']['guard']['last_web_fire']['applied_damage']
    if not any(row['equation']['die_sides'] == 4 for row in result.context['_turn_effect_rows']):
        expected_damage = 0
    assert result.context['_turn_effect_damage_applied'] == expected_damage
    card = project_result_card(runtime.tools['web_feedback'].metadata['result_card'],
        parameters={'actor': 'guard'}, public_context=result.context)
    assert card is not None and len(card['equations']) == len(result.context['_turn_effect_rows'])
    return result.context['_turn_effect_rows']


def test_native_faces_outcome_dc_and_applied_damage_are_visible(projection_runtime, native_state):
    rows = project(projection_runtime, native_state)
    assert len(rows) == 2 and all(row['label'] == 'guard' for row in rows)
    assert rows[0]['value'] == 'Burning Web · 5 fire damage applied'
    assert rows[0]['equation'] == {'die_sides': 4, 'roll': 5, 'rolls': [1, 4], 'modifier': 0, 'total': 5}
    assert rows[1]['value'] == 'Web · Dexterity save · DC 18 · failure'
    assert rows[1]['equation'] == {'die_sides': 20, 'roll': 11, 'modifier': 0, 'total': 11}


def test_adjusted_damage_and_immunity_do_not_replace_original_faces(projection_runtime, native_state):
    state = deepcopy(native_state)
    state[NAMESPACE + '.entities']['guard']['last_web_fire'].update(damage_roll_total=7, applied_damage=0)
    rows = project(projection_runtime, state)
    assert rows[0]['value'] == 'Burning Web · 0 fire damage applied'
    assert rows[0]['equation'] == {'die_sides': 4, 'roll': 5, 'rolls': [1, 4], 'modifier': 2, 'total': 7}


def test_native_adjusted_save_total_is_used(projection_runtime, native_state):
    state = deepcopy(native_state)
    state[NAMESPACE + '.entities']['guard']['last_web_save'].update(modifier=7, total=18, outcome='success')
    row = project(projection_runtime, state)[1]
    assert row['value'] == 'Web · Dexterity save · DC 18 · success'
    assert row['equation'] == {'die_sides': 20, 'roll': 11, 'modifier': 7, 'total': 18}


@pytest.mark.parametrize('change,expected', [('old_burn', 1), ('old_save', 1), ('entry_save', 1),
    ('exploration_save', 1), ('other_actor', 0), ('closed_turn', 0), ('older_receipts', 0)])
def test_stale_or_non_start_receipts_do_not_create_widgets(projection_runtime, native_state, change, expected):
    state = deepcopy(native_state)
    actor, combat = state[NAMESPACE + '.entities']['guard'], state[NAMESPACE + '.combat']
    if change == 'old_burn':
        actor['last_web_fire']['turn_sequence'] -= 1
    elif change == 'old_save':
        actor['last_web_save']['turn'] -= 1
    elif change == 'entry_save':
        actor['last_web_save']['timing'] = 'entry'
    elif change == 'exploration_save':
        actor['last_web_save']['clock'] = 'exploration'
    elif change == 'other_actor':
        combat['active_actor'] = 'hero'
    elif change == 'closed_turn':
        combat['turn_active'] = False
    else:
        actor['last_web_fire'].pop('damage_roll_total')
        actor['last_web_save'].pop('modifier')
    assert len(project(projection_runtime, state)) == expected
