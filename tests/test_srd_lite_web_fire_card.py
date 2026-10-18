"""Count real paid ignition state and exclude stale or unrelated burning cells without side effects."""
from copy import deepcopy
import json
import os
from pathlib import Path
import zipfile

import pytest
import yaml

from scripts.srd_lite_web_fire_card import _feedback_effects
from scripts.srd_spell_environment import _environment_tool, _set
from scripts.srd_timed_actor_defenses import _entity
from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE


@pytest.fixture(scope='module')
def paid_state():
    database = Path(os.environ['SRD_LITE_WEB_RING_DATABASE'])
    assert database.is_file()
    with AuthorityOwner.open(database, 'web-feedback-read-only') as owner:
        snapshot = owner.snapshot('lite')
    return thaw_json_value(snapshot.state)


@pytest.fixture(scope='module')
def feedback_runtime():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        data = yaml.load(archive.read('packages/' + NAMESPACE + '/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = {'feedback': _environment_tool('Read the committed ignition feedback.',
        [_entity('caster_state')], [_set('_spell_card_summary', []), _set('_spell_detail_rows', []), *_feedback_effects()])}
    return RulesetRuntime(ruleset_dict=subset)


def run(parsed, state, caster):
    before, rng = json.dumps(state, sort_keys=True), dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    result = execute_tool('feedback', {'caster_state': caster}, parsed.tools, bridge,
        parsed.ruleset_config, parsed.definitions, budget=ExecutionBudget())
    assert result.success, result.error
    assert json.dumps(state, sort_keys=True) == before and dice._rng.getstate() == rng
    assert not bridge.mutations() and not result.state_changes
    return result.context


@pytest.mark.parametrize('caster', ['hero', 'other'])
def test_real_paid_sources_show_the_twelve_actual_burning_cells(feedback_runtime, paid_state, caster):
    context = run(feedback_runtime, paid_state, caster)
    assert context['_web_feedback_count'] == 12
    assert context['_spell_card_summary'] == [{'label': 'Web cells ignited', 'value': 12}]
    assert context['_spell_detail_rows'] == [{'label': 'Web burning',
        'value': 'Each exposed 5-foot cube burns away after 1 round.'}]


@pytest.mark.parametrize('change', ['expired', 'destroyed', 'older_source', 'different_fire'])
def test_unrelated_or_ended_burns_are_excluded(feedback_runtime, paid_state, change):
    state = deepcopy(paid_state)
    web = state[NAMESPACE + '.state']['persistent_spell_areas']['hero']
    cells = [cell for cell in web['web_cells'].values() if cell['status'] == 'burning']
    cell = cells[0]
    if change == 'expired':
        cell['burn_end_tick'] = state[NAMESPACE + '.state']['lasting_tick']
    elif change == 'destroyed':
        cell['status'] = 'destroyed'
    elif change == 'older_source':
        cell['source_sequence'] -= 1
    else:
        cell['fire_area_id'] = 'unrelated-fire'
    context = run(feedback_runtime, state, 'other')
    assert context['_web_feedback_count'] == 11


def test_zero_ignition_adds_no_false_burning_row(feedback_runtime, paid_state):
    state = deepcopy(paid_state)
    for cell in state[NAMESPACE + '.state']['persistent_spell_areas']['hero']['web_cells'].values():
        cell['status'] = 'intact'
    context = run(feedback_runtime, state, 'other')
    assert context['_web_feedback_count'] == 0
    assert context['_spell_card_summary'] == context['_spell_detail_rows'] == []
