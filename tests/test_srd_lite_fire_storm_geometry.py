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
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _scene_entity, _wall, _calls


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


def test_public_transport_declarations_add_only_optional_cube_intent(bound):
    _, data, _ = bound
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        record = next(row for row in json.loads(archive.read('manifest.json'))['packages']
            if row['id'] == NAMESPACE)
    before = deepcopy(record)
    exposed = expose_fire_storm_cube_intent(data, record)
    assert {'cast_spell', 'cast_spell_outside_combat', 'begin_srd_spell_action',
            'begin_srd_after_hit_smite', 'cast_srd_reaction_spell'} <= set(exposed)
    for original, after in zip(before['tools'], record['tools']):
        assert original['name'] == after['name']
        if after['name'] in exposed:
            assert PARAMETER not in original['input_schema']['properties']
            assert PARAMETER not in after['input_schema'].get('required', [])
            assert after['input_schema']['properties'].pop(PARAMETER)['type'] == 'array'
        assert after == original


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
        tool['parameters'] = [deepcopy(pool[p]) if isinstance(p, str) else p for p in tool['parameters']]
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


def test_ten_cubes_collect_unique_creatures_and_only_eligible_fuel(bound):
    frames = [_scene_entity('recipient', [10, 5, 0]),
              _scene_entity('far', [110, 5, 0]),
              _object('fuel', [10, 4, 0]), _object('stone', [30, 3, 0], flammable=False),
              _object('worn', [40, 3, 0], worn_by='recipient', fuel=False),
              _object('carried', [50, 3, 0], carried_by='recipient', fuel=False)]
    frames[0]['metadata']['cover_from'] = {'hero': 'half'}
    result = _run(bound, cubes=[cube((i * 10, 0, 0)) for i in range(10)], frames=frames, ignite=True)
    assert result.success, result.error
    assert result.context['_fire_storm_cube_count'] == 10
    assert result.context['_fire_storm_targets'] == ['recipient']
    assert result.context['_fire_storm_objects'] == ['fuel']
    assert result.context['_fire_storm_half_cover'] == ['recipient']


def test_separate_face_connected_pairs_and_staggered_faces_are_supported(bound):
    result = _run(bound, cubes=[cube(), cube((10, 5, 0)), cube((50, 0, 0)), cube((60, 0, 0))],
                  frames=[_scene_entity('recipient', [55, 5, 0])])
    assert result.success, result.error
    assert result.context['_fire_storm_targets'] == ['recipient']


@pytest.mark.parametrize('cubes', [[], [cube()], [cube((i * 10, 0, 0)) for i in range(11)],
    [cube(), cube((11, 0, 0))], [cube(), cube((10, 10, 0))],
    [cube(origin=(5, 5, 5)), cube((10, 0, 0))],
    [cube((-201, 0, 0)), cube((-191, 0, 0))]])
def test_invalid_cube_shapes_contact_origins_or_bounds_reject_without_payment(bound, cubes):
    result = _run(bound, cubes=cubes)
    assert not result.success
    assert 'Expression exceeds' not in result.error


@pytest.mark.parametrize('field', ('min', 'origin'))
@pytest.mark.parametrize('coordinates', ([0, 0], [0, 0, 0, 0]))
def test_wrong_point_coordinates_reject_before_native_payment(bound, field, coordinates):
    cubes = [cube(), cube((10, 0, 0))]
    cubes[0][field] = coordinates
    result = _run(bound, cubes=cubes)
    assert not result.success and 'exactly three coordinates' in result.error


def test_actual_origin_range_uses_caster_body_and_not_reported_distance(bound):
    valid = _run(bound, hero=(-140.4, 0, 0))
    assert valid.success, valid.error
    assert valid.context['_fire_storm_distance'] == 150
    invalid = _run(bound, hero=(-140.41, 0, 0))
    assert not invalid.success and 'exceeds 150 feet' in invalid.error


def test_other_plane_and_vertical_bodies_do_not_become_recipients(bound):
    result = _run(bound, frames=[_scene_entity('recipient', [5, 5, 0], plane='ethereal'),
                                _scene_entity('above', [5, 5, 11])])
    assert result.success, result.error
    assert result.context['_fire_storm_targets'] == []


def test_missing_fuel_facts_reject_before_native_ignition_or_dice(bound):
    result = _run(bound, frames=[_object('fuel', [5, 5, 0], fuel=False)])
    assert not result.success and 'ordinary fuel facts' in result.error


def test_solid_cover_excludes_fuel_and_creature_but_not_clear_side(bound):
    wall = _wall()
    wall['volume'] = {'kind': 'box', 'min': [4, -2, -10], 'max': [5, 12, 12]}
    # Both origins and caster are on the near side. The second cube is above,
    # maintaining face contact while its origin remains on the same near side.
    recipient = _scene_entity('recipient', [7, 5, 0])
    recipient['metadata']['cover_from'] = {'hero': 'total'}
    result = _run(bound, cubes=[cube(), cube((0, 0, -10), origin=(0, 0, 0))],
        geometry=[wall], frames=[recipient,
                                _object('blocked-fuel', [7, 4, 0]),
                                _scene_entity('clear', [2, 5, 0])])
    assert result.success, result.error
    assert result.context['_fire_storm_targets'] == ['clear']
    assert result.context['_fire_storm_objects'] == []


def test_unadmitted_physical_cover_rejects_clearly_without_guessing_category(bound):
    wall = _wall()
    wall['volume'] = {'kind': 'box', 'min': [4, -2, -10], 'max': [5, 12, 12]}
    result = _run(bound, cubes=[cube(), cube((0, 0, -10), origin=(0, 0, 0))],
                  geometry=[wall], frames=[_scene_entity('recipient', [7, 5, 0])])
    assert not result.success
    assert 'admitted categorical Cover' in result.error


def test_excluded_origin_only_contact_is_not_a_recipient(bound):
    cubes = [cube(), cube((10, 0, 0))]
    recipient = _scene_entity('recipient', [-0.4, -0.4, -6])
    absent = _run(bound, cubes=cubes, frames=[recipient])
    assert absent.success, absent.error
    assert absent.context['_fire_storm_targets'] == []
    cubes[0]['include_origin'] = True
    included = _run(bound, cubes=cubes, frames=[recipient])
    assert included.success, included.error
    assert included.context['_fire_storm_targets'] == ['recipient']


def test_cast_and_action_preflight_forward_actual_scene_arrays(bound):
    _, data, _ = bound
    for name in ('cast_spell', 'begin_srd_spell_action'):
        owner = data['tools'][name]
        preflights = [e for e in owner['effects'] if any(row.get('call') == QUALIFY for row in e.get('on_true', []))]
        assert len(preflights) == 1
        index = owner['effects'].index(preflights[0])
        initializers = [i for i, e in enumerate(owner['effects'])
            if e.get('set', {}).get('field', '').startswith('_fire_dispatch_retained_ice_dispatch_')]
        assert len(initializers) == 8
        assert max(initializers) < index
        frame = ExecutionContext(params={'spell': 'fire_storm', 'caster_state': {},
            'caster_can_see_origin': False, PARAMETER: [cube(), cube((10, 0, 0))]},
            vars={'_caster_state_id': 'hero'}, definitions=data['definitions'])
        from sylvia_gm.runtime._types import ToolResult
        def selected(name, params, parent):
            assert name == QUALIFY
            assert params[PARAMETER] == [cube(), cube((10, 0, 0))]
            return ToolResult(name, context=dict(zip(EXPORTS, [['recipient'], [], [], ['fuel'], 12.5, 2])))
        execute_effects([preflights[0]], frame, selected)
        assert frame.vars['_fire_dispatch_retained_ice_dispatch_targets'] == ['recipient']
        assert frame.vars['_fire_dispatch_retained_ice_dispatch_origin_distance'] == 13
        assert frame.vars['_fire_dispatch_area_object_targets'] == ['fuel']
        assert frame.params['caster_can_see_origin'] is False
        assert '_fire_dispatch_caster_can_see_origin' not in frame.vars


def test_actual_scene_arrays_survive_original_dispatch_defaults(bound):
    _, data, _ = bound
    from sylvia_gm.runtime._types import ToolResult
    for name in ('cast_spell', 'begin_srd_spell_action'):
        effects = data['tools'][name]['effects']
        start = next(i for i, e in enumerate(effects)
            if e.get('set', {}).get('field') == '_fire_dispatch_retained_ice_dispatch_targets')
        stop = next(i for i, e in enumerate(effects)
            if any(row.get('call') == QUALIFY for row in e.get('on_true', [])))
        frame = ExecutionContext(params={'spell': 'fire_storm', 'targets': ['forged'],
            'half_cover_targets': ['forged'], 'three_quarters_cover_targets': [],
            'origin_distance': 999, PARAMETER: [cube(), cube((10, 0, 0))]},
            vars={'_caster_state_id': 'hero'}, definitions=data['definitions'])
        def selected(tool, parameters, parent):
            assert tool == QUALIFY
            return ToolResult(tool, context=dict(zip(EXPORTS,
                [['actual'], [], ['actual'], [], 12.5, 2])))
        execute_effects(effects[start:stop + 1], frame, selected)
        assert frame.vars['_fire_dispatch_retained_ice_dispatch_targets'] == ['actual']
        assert frame.vars['_fire_dispatch_retained_ice_dispatch_half_cover_targets'] == []
        assert frame.vars['_fire_dispatch_retained_ice_dispatch_three_quarters_cover_targets'] == ['actual']
        assert frame.vars['_fire_dispatch_retained_ice_dispatch_origin_distance'] == 13


def test_cube_intent_reaches_pending_capture_and_completion(bound):
    _, data, changed = bound
    assert {'cast_spell', 'cast_spell_outside_combat', 'begin_srd_spell_action',
            'complete_srd_pending_action'} <= set(changed['transport_owners'])
    captured = [row for effect in data['tools']['begin_srd_spell_action']['effects']
        for rows in _effect_lists([effect]) for e in rows
        for row in (e['set']['value'] if isinstance(e.get('set', {}).get('value'), list) else [])
        if isinstance(row, dict) and row.get('key') == "'fire_storm_cubes'"]
    assert len(captured) == 1
    assert captured[0]['value'] == PARAMETER and captured[0]['missing'] == PARAMETER + ' == null'
    completed = [e for rows in _effect_lists(data['tools']['complete_srd_pending_action']['effects'])
                 for e in rows if e.get('call') == 'cast_spell']
    assert len(completed) == 1
    assert completed[0]['params'][PARAMETER] == '{_pending_action.parameters.fire_storm_cubes}'


@pytest.mark.parametrize(('spell', 'values', 'expected'), (
    ('fire_storm', [], None), ('fire_storm', ['recipient'], ['recipient']),
    ('fire_storm', None, None), ('fireball', [], [])))
def test_staged_fire_empty_derived_lists_keep_original_public_contract(bound, spell, values, expected):
    _, data, _ = bound
    rows = next(rows for rows in _effect_lists(data['tools']['complete_srd_pending_action']['effects'])
        if any(e.get('call') == 'cast_spell' for e in rows))
    call_index = next(i for i,e in enumerate(rows) if e.get('call') == 'cast_spell')
    normalizers = rows[call_index - 6:call_index]
    names = ('half_cover_targets', 'three_quarters_cover_targets')
    assert rows[call_index]['params']['targets'] == '{_pending_action.parameters.targets}'
    assert all(normalizers[i * 3].get('set', {}).get('field') == '_fire_pending_public_' + name
        for i,name in enumerate(names))
    frame = ExecutionContext(vars={'_pending_action':{'spell':spell,
        'parameters':{name:deepcopy(values) for name in names}}})
    execute_effects(normalizers, frame)
    for name in names:
        assert frame.vars['_fire_pending_public_' + name] == expected
        parameter = next(p for p in data['tools']['cast_spell']['parameters'] if p['name'] == name)
        assert parameter['min_items'] == 1 and not parameter['required'] and parameter.get('default') is None


def test_actual_area_receipt_survives_native_final_cast_context_transport(bound):
    _, data, _ = bound
    captures = [e for rows in _effect_lists(data['tools']['cast_spell']['effects']) for e in rows
                if e.get('set', {}).get('field') == '_retained_cast_tail_context']
    assert len(captures) == 1
    originals = {'_fire_storm_targets': ['recipient'], '_fire_storm_half_cover': [],
                 '_fire_storm_three_quarters': [], '_fire_storm_objects': ['fuel'],
                 '_fire_storm_distance': 12.5, '_fire_storm_cube_count': 2}
    added = [row for row in captures[0]['set']['value'] if row['key'][1:-1] in EXPORTS]
    assert len(added) == len(EXPORTS)
    root_frame = ExecutionContext(vars=deepcopy(originals))
    execute_effects([{'set': {'field': '_retained_cast_tail_context', 'value': added}}], root_frame)
    tail_frame = ExecutionContext(params={
        '_retained_cast_tail_context': root_frame.vars['_retained_cast_tail_context']})
    restore = next(e for e in data['tools']['finish_srd_retained_cast_tail']['effects']
                   if e.get('for_each', {}).get('collection') == '_retained_cast_tail_context')
    execute_effects([restore], tail_frame)
    assert all(tail_frame.vars[field] == value for field, value in originals.items())
    tail_frame.vars.update(_ms='fire_storm', _spell_detail_rows=[], _spell_card_summary=[])
    from sylvia_gm.runtime._types import ToolResult
    def formatter(name, params, parent):
        assert name == 'format_srd_information_projection'
        child = ExecutionContext(params=params)
        execute_effects(data['tools'][name]['effects'], child)
        return ToolResult(name, context=child.vars)
    execute_effects([data['tools']['cast_spell']['effects'][-1]], tail_frame, formatter)
    receipt = tail_frame.vars['_spell_detail_rows'][0]
    assert receipt['label'] == 'Fire Storm area'
    assert receipt['value'] == '2 admitted cubes; creatures: recipient; fuel objects: fuel.'
    assert tail_frame.vars['_spell_card_summary'] == [{'label': 'Cubes', 'value': 2}]
