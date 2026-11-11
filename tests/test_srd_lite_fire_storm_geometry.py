"""Public synthetic component checks for Fire Storm's scene-derived source adapter."""
from copy import deepcopy
import json, os, zipfile
from pathlib import Path

import pytest
import yaml

from scripts.srd_lite_fire_storm_geometry import QUALIFY, FAMILY, PARAMETER, EXPORTS, bind_fire_storm_scene_geometry, expose_fire_storm_cube_intent, _transport, _intent
from scripts.srd_lite_mass_cure_geometry import _effect_lists
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._types import ExecutionContext
from sylvia_gm import dice
from tests.srd_lite_mass_cure_geometry_fixtures import NAMESPACE, _scene_entity, _wall, _calls


def cube(minimum=(0, 0, 0), *, origin=None, include_origin=False):
    return {'min': list(minimum), 'origin': list(minimum if origin is None else origin),
            'include_origin': include_origin}


def test_fixed_other_spell_reaction_transport_stays_unchanged():
    from scripts.srd_hellish_rebuke import _begin, PROFILE
    runtime = {'definitions': {'spells': {'hellish_rebuke': deepcopy(PROFILE)}},
        'tools': {'begin_srd_hellish_rebuke': _begin()}}
    before = deepcopy(runtime)
    assert _transport(runtime) == []
    assert runtime == before


def test_cube_intent_matches_normal_parameter_admission():
    from sylvia_gm.runtime._schema_validator import validate_parameter_declarations
    assert validate_parameter_declarations({'parameter_declarations': {'cube_intent': _intent()}}) == []




@pytest.mark.parametrize('spell', ('fire_storm', '{spell}'))
def test_fire_storm_or_generic_cast_still_requires_its_origin_transport(spell):
    runtime = {'definitions': {'spells': {'fire_storm': {}}}, 'tools': {
        'source_cast': {'parameters': [], 'effects': [
            {'call': 'cast_spell', 'params': {'spell': spell}}]}}}
    with pytest.raises(ValueError, match='lacks the retained origin intent'):
        _transport(runtime)


@pytest.fixture(scope='module')
def bound():
    package = Path(os.environ['SRD_LITE_CANDIDATE'])
    with zipfile.ZipFile(package) as archive:
        entries = [name for name in archive.namelist()
                   if name.endswith('/runtime/ruleset.yaml') and NAMESPACE in name]
        assert len(entries) == 1
        data = yaml.load(archive.read(entries[0]), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[p]) if isinstance(p, str) else p for p in tool.get("parameters", [])]
    original = {name: deepcopy(data['tools'][name]) for name in (FAMILY, 'ignite_srd_area_object')}
    profile = deepcopy(data['definitions']['spells']['fire_storm'])
    casting = deepcopy(data['definitions']['spell_casting']['fire_storm'])
    changed = bind_fire_storm_scene_geometry(data)
    assert data['definitions']['spells']['fire_storm'] == profile
    assert data['definitions']['spell_casting']['fire_storm'] == casting
    assert all(data['tools'][name] == tool for name, tool in original.items())
    assert changed['qualifier'] == QUALIFY
    # Parse the actual qualifier's dependency closure through normal runtime schemas.
    pending = [QUALIFY, 'ignite_srd_area_object']
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = closure
    return RulesetRuntime(ruleset_dict=subset), data, changed


def _object(identity, position, *, flammable=True, fuel=True, **metadata):
    result = _scene_entity(identity, position)
    result['metadata'].pop('participant_ref')
    result['metadata'].update(is_object=True, flammable=flammable, **metadata)
    if fuel:
        result['metadata']['srd_ordinary_flame'] = {'kind': 'ordinary_flame', 'burning': False}
    return result


def _run(bound, *, cubes=None, frames=None, geometry=None, hero=(0, -10, 0), ignite=False):
    parsed, _, _ = bound
    rows = [_scene_entity('hero', list(hero)), *(frames or [_scene_entity('recipient', [5, 5, 0])])]
    scene = {'format': 1, 'map': {'id': 'mass-cure-fixture', 'revision': 0,
        'bounds': {'min': [-200, -200, -200], 'max': [200, 200, 200]},
        'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-200, -200]},
        'layers': [{'id': 'world', 'min_elevation': -200, 'max_elevation': 200}]},
        'entities': rows, 'geometry': geometry or [], 'terrain': [], 'zones': []}
    entities = {row['metadata']['participant_ref']: {
        'name': row['id'], 'kind': 'character', 'creature_type': 'humanoid',
        'hp': 50, 'max_hp': 50, 'is_alive': True, 'conditions': [],
        'resources': {'spell_slot_7': 2}, 'srd_affinity': {}}
        for row in rows if 'participant_ref' in row['metadata']}
    state = {NAMESPACE + '.state': {'lasting_tick': 0, 'turn_effects': [], 'spatial_scene': scene},
             NAMESPACE + '.entities': entities, NAMESPACE + '.combat': {}}
    before = json.dumps(state, sort_keys=True)
    rng_before = dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    def nested(name, parameters, parent):
        return execute_tool(name, parameters, parsed.tools, bridge, parsed.ruleset_config,
                            parsed.definitions, tool_executor=nested, budget=parent.budget)
    result = execute_tool(QUALIFY, {'caster_state': 'hero', PARAMETER:
        [cube(), cube((10, 0, 0))] if cubes is None else cubes},
        parsed.tools, bridge, parsed.ruleset_config, parsed.definitions,
        tool_executor=nested, budget=ExecutionBudget())
    assert dice._rng.getstate() == rng_before
    assert json.dumps(state, sort_keys=True) == before
    assert bridge.get_entity('hero')['resources']['spell_slot_7'] == 2
    assert bridge.combat_state == {}
    if ignite:
        assert result.success, result.error
        for identity in result.context['_fire_storm_objects']:
            ignited = execute_tool('ignite_srd_area_object', {'object_id': identity},
                parsed.tools, bridge, parsed.ruleset_config, parsed.definitions,
                tool_executor=nested, budget=ExecutionBudget())
            assert ignited.success, ignited.error
            assert ignited.context['_area_object_ignited'] is True
        latest = bridge.get_entity('state')['spatial_scene']['entities']
        for row in latest:
            if row['metadata'].get('is_object') is True:
                assert row['metadata'].get('srd_ordinary_flame', {}).get('burning', False) is (
                    row['id'] in result.context['_fire_storm_objects'])
        assert bridge.get_entity('hero')['resources']['spell_slot_7'] == 2
        assert dice._rng.getstate() == rng_before
    return result
