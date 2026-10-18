"""Steed statistics use the actual current summoner and original paid casting arithmetic."""
from copy import deepcopy
import pytest
from scripts.srd_lite_steed_casting_statistics import CURRENT, ABILITY, bind_steed_casting_statistics
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_srd_lite_steed_healing_touch import bound as touch_bound, _case
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls


@pytest.fixture(scope='module')
def bound(touch_bound):
    data = deepcopy(touch_bound[1])
    creation_before = deepcopy(data['tools']['cast_spell_independent_created_creature'])
    finish_before = deepcopy(data['tools']['finish_srd_steed_source_actor'])
    profiles_before = deepcopy(data['definitions']['spells'])
    changed = bind_steed_casting_statistics(data)
    assert data['definitions']['spells'] == profiles_before
    assert data['tools']['cast_spell_independent_created_creature'].get('pre_conditions', []) == creation_before.get('pre_conditions', [])
    assert data['tools']['finish_srd_steed_source_actor']['effects'][:-1] == finish_before['effects']
    closure, pending = {}, [CURRENT, 'finish_srd_steed_source_actor']
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    parsed = RulesetRuntime(ruleset_dict={**{key: value for key, value in data.items()
        if key not in {'tools', 'procedures'}}, 'tools': closure})
    return parsed, data, changed


def _statistics_case(bound, *, level=5, score=18, ability='charisma', recovery=None,
                     npc=False, participant_changes=None, steed_changes=None, caster_changes=None):
    _, state, _ = _case(bound, steed_changes={ABILITY: ability, 'created_save_dc': 12,
        'created_attack_bonus': 4, **(steed_changes or {})}, caster_changes={
        'creature_kind': 'monster' if npc else 'character', 'ability_scores': {'charisma': 18},
        'spellcasting_profile': {'ability': 'wisdom', 'attack_bonus': 9, 'save_dc': 17},
        **({'has_recovery_ability_reductions': True, 'ability_scores': {'charisma': recovery}}
           if recovery is not None else {}), **(caster_changes or {})})
    artifact = {'participant_id': 'caster', 'validation_status': 'validated',
        'package_id': NAMESPACE, 'kind': 'player_character',
        'data': {'level': level, 'abilities': {ability or 'charisma': score}}, **(participant_changes or {})}
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state, {} if npc else {'caster': artifact})
    parsed = bound[0]
    def act(name, **parameters):
        def nested(tool, args, parent):
            return execute_tool(tool, args, parsed.tools, bridge, parsed.ruleset_config,
                parsed.definitions, combat_state=bridge.combat_state,
                tool_executor=nested, budget=parent.budget)
        return execute_tool(name, parameters, parsed.tools, bridge, parsed.ruleset_config,
            parsed.definitions, combat_state=bridge.combat_state,
            tool_executor=nested, budget=ExecutionBudget())
    return bridge, act


@pytest.mark.parametrize('level,score,expected_bonus,expected_dc',
                         [(5, 18, 7, 15), (9, 20, 9, 17), (17, 20, 11, 19)])
def test_current_character_changes_do_not_reuse_creation_dc(bound, level, score, expected_bonus, expected_dc):
    bridge, act = _statistics_case(bound, level=level, score=score)
    result = act(CURRENT, steed_state='steed', caster_state='caster')
    assert result.success, result.error
    assert result.context['_steed_attack_bonus'] == expected_bonus
    assert result.context['_steed_save_dc'] == expected_dc
    assert bridge.get_entity('steed')['created_save_dc'] == 12


def test_native_recovery_ability_reduction_is_preserved(bound):
    bridge, act = _statistics_case(bound, recovery=10)
    result = act(CURRENT, steed_state='steed', caster_state='caster')
    assert result.success, result.error
    assert result.context['_steed_attack_bonus'] == 3 and result.context['_steed_save_dc'] == 11


def test_npc_uses_its_live_saved_profile_without_a_character_artifact(bound):
    bridge, act = _statistics_case(bound, npc=True)
    result = act(CURRENT, steed_state='steed', caster_state='caster')
    assert result.success, result.error
    assert result.context['_steed_attack_bonus'] == 9 and result.context['_steed_save_dc'] == 17


@pytest.mark.parametrize('changes,error', [
    ({'participant_changes': {'participant_id': 'other'}}, 'actual validated'),
    ({'participant_changes': {'validation_status': 'quarantined'}}, 'actual validated'),
    ({'ability': None}, 'original casting ability'),
    ({'caster_changes': {'is_alive': False}}, 'living caster')])
def test_wrong_or_missing_authority_fails_without_changing_stored_statistics(bound, changes, error):
    bridge, act = _statistics_case(bound, **changes)
    before = bridge.get_entity('steed'), bridge.get_entity('caster')
    result = act(CURRENT, steed_state='steed', caster_state='caster')
    assert not result.success and error in result.error
    assert before == (bridge.get_entity('steed'), bridge.get_entity('caster'))


def test_creation_finish_captures_the_paid_owner_ability(bound):
    bridge, act = _statistics_case(bound, ability=None)
    result = act('finish_srd_steed_source_actor', caster_state='caster', actor_state='steed',
        slot_level=2, attack_bonus=7, save_dc=15, casting_ability='charisma')
    assert result.success, result.error
    assert bridge.get_entity('steed')[ABILITY] == 'charisma'
    current = act(CURRENT, steed_state='steed', caster_state='caster')
    assert current.success, current.error
