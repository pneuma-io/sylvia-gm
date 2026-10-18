"""Use admitted Box and circular recipient bodies for Guardian of Faith's radius."""
from copy import deepcopy
from .srd_lite_visual_rolls import _lists


def bind_guardian_recipient_body(runtime):
    """Measure Box-to-Box or Box-to-cylinder distance without a square circle proxy."""
    tool = deepcopy(runtime['tools']['measure_srd_guardian_distance'])
    effects = tool['effects']
    guard = next(e['require'] for e in effects
                 if 'Guardian radius qualification' in e.get('require', {}).get('error', ''))
    assert guard['condition'] == ("_guardian_scene.footprint.kind == 'box' and "
                                  "_guardian_target_scene.footprint.kind == 'box'")
    guard.update(
        condition="_guardian_scene.footprint.kind == 'box' and "
                  "(_guardian_target_scene.footprint.kind == 'box' or "
                  "_guardian_target_scene.footprint.kind == 'circle')",
        error='Guardian range needs its actual Box source and an admitted creature footprint.')
    first = next(i for i, e in enumerate(effects)
                 if e.get('set', {}).get('field') == '_guardian_gap_x')
    calculations = {e['set']['field']: e['set']['value'] for e in effects[first:first + 4]}
    assert set(calculations) == {'_guardian_gap_x', '_guardian_gap_y', '_guardian_gap_z',
                                 '_guardian_distance_squared'}
    box_gaps = [calculations['_guardian_gap_' + axis] for axis in ('x', 'y')]
    circle_gaps = [value.replace(' - _guardian_target_scene.footprint.half_extents[' + str(i) + ']', '')
                   for i, value in enumerate(box_gaps)]
    vertical = '(' + calculations['_guardian_gap_z'] + ') ** 2'
    box = '(' + box_gaps[0] + ') ** 2 + (' + box_gaps[1] + ') ** 2 + ' + vertical
    circle = ('max(0, ((' + circle_gaps[0] + ') ** 2 + (' + circle_gaps[1] +
              ') ** 2) ** 0.5 - _guardian_target_scene.footprint.radius) ** 2 + ' + vertical)
    effects[first:first + 4] = [
        {'set': {'field': '_guardian_distance_squared', 'value': value},
         'condition': "_guardian_target_scene.footprint.kind == '" + kind + "'"}
        for kind, value in (('box', box), ('circle', circle))
    ]
    # The complete admitted frames already contain both positions. Keep only the
    # frame export instead of carrying the same coordinates in separate locals.
    effects[:] = [e for e in effects if e.get('set', {}).get('field')
                  not in {'_guardian_origin', '_guardian_target_position',
                          '_guardian_scene', '_guardian_target_scene'}]

    def use_frame(value):
        if isinstance(value, str):
            return value.replace('_guardian_target_position', '_target.position').replace(
                '_guardian_origin', '_source.position').replace(
                '_guardian_target_scene', '_target').replace('_guardian_scene', '_source')
        if isinstance(value, list):
            return [use_frame(row) for row in value]
        if isinstance(value, dict):
            return {key: use_frame(row) for key, row in value.items()}
        return value

    tool['effects'] = use_frame(effects)
    frame_names = iter(('_source', '_target'))
    for effect in tool['effects']:
        if effect.get('call') == 'srd_information_scene_entity':
            assert '_info_scene_entity' in effect['export_context']
            name = next(frame_names)
            effect['export_context'] = {name: '_info_scene_entity'}
            if name == '_target':
                effect['export_context']['_guardian_target_scene'] = '_info_scene_entity'
    assert all(len(effect['set']['value']) <= 500 for effect in tool['effects']
               if isinstance(effect.get('set', {}).get('value'), str))
    tool['description'] = 'Measure nearest distance from the Guardian Box to the admitted recipient body.'
    runtime['tools']['measure_srd_guardian_distance'] = tool


def bind_guardian_turn_feedback(runtime):
    """Carry each current Guardian save and fixed damage to the native turn card."""
    tools = runtime['tools']
    event = tools['start_srd_guardian_turn']
    event['effects'][:0] = [
        {'set': {'field': '_guardian_turn_rows', 'value': []}},
        {'set': {'field': '_guardian_turn_damage', 'value': 0}},
    ]
    calls = [(rows, index) for rows in list(_lists(event['effects']))
             for index, effect in enumerate(rows)
             if effect.get('call') == 'damage_srd_guardian_target']
    assert len(calls) == 1
    rows, index = calls[0]
    rows[index]['export_context'] = {
        '_guardian_target_rows': '_spell_detail_rows',
        '_guardian_target_damage': '_affliction_trigger_damage',
    }
    rows[index + 1:index + 1] = [
        {'condition': '_guardian_target_rows != null', 'on_true': [
            {'set': {'field': '_guardian_turn_rows',
                     'value': '_guardian_turn_rows + _guardian_target_rows'}}]},
        {'condition': '_guardian_target_damage != null', 'on_true': [
            {'set': {'field': '_guardian_turn_damage',
                     'value': '_guardian_turn_damage + _guardian_target_damage'}}]},
    ]
    begin = tools['begin_combat_turn']
    calls = [(rows, index) for rows in list(_lists(begin['effects']))
             for index, effect in enumerate(rows)
             if effect.get('call') == 'start_srd_guardian_turn']
    assert len(calls) == 1
    rows, index = calls[0]
    rows[index]['export_context'] = {
        '_guardian_turn_rows': '_guardian_turn_rows',
        '_guardian_turn_damage': '_guardian_turn_damage',
    }
    rows[index + 1:index + 1] = [
        {'set': {'field': '_turn_effect_rows',
                 'value': '_turn_effect_rows + _guardian_turn_rows'}},
        {'set': {'field': '_turn_effect_damage_applied',
                 'value': '_turn_effect_damage_applied + _guardian_turn_damage'}},
    ]
    # This display count is consumed by the final card. Compute it once after all
    # native turn hooks instead of keeping identical updates in each hook.
    updates = 0
    for rows in list(_lists(begin['effects'])):
        for index in range(len(rows) - 1, -1, -1):
            if rows[index].get('set') == {'field': '_turn_effect_count',
                                          'value': 'len(_turn_effect_rows)'}:
                rows.pop(index)
                updates += 1
    assert updates >= 1
    begin['effects'].append({'set': {'field': '_turn_effect_count',
                                    'value': 'len(_turn_effect_rows)'}})


def bind_guardian_movement_body(runtime):
    """Use continuous neutral body proximity for actual Guardian entry movement."""
    tool = runtime['tools']['resolve_srd_scope96_guardian_move']
    starts = [row for row in tool['parameters'] if (
        runtime.get('parameter_declarations', {}).get(row, {}) if isinstance(row, str) else row
        ).get('name') == 'start']
    assert len(starts) == 1
    tool['parameters'].remove(starts[0])
    callers = [effect for rows in _lists(runtime['tools']['move_srd_scene_creature_environment_segment']['effects'])
               for effect in rows if effect.get('call') == 'resolve_srd_scope96_guardian_move']
    assert len(callers) == 1 and callers[0]['params']['start'] == '{_move_origin}'
    callers[0]['params'].pop('start')
    calls = [(rows, index) for rows in list(_lists(tool['effects']))
             for index, effect in enumerate(rows)
             if effect.get('call') == 'resolve_srd_environment_body_segment']
    assert len(calls) == 1
    rows, index = calls[0]
    assert rows[index]['params']['radius'] == 10
    trigger = rows[index + 1]
    assert trigger['condition'].startswith('_body_hit == true and _body_entry > ')
    effects = trigger['on_true']
    measures = [i for i, effect in enumerate(effects)
                if effect.get('call') == 'measure_srd_guardian_distance']
    assert len(measures) == 1
    measure = effects.pop(measures[0])
    measure['export_context'].update({'_guardian_source_body': '_source',
                                      '_guardian_moving_body': '_target'})
    trigger['condition'] = trigger['condition'].replace('_body_hit', '_guardian_proximity.hit').replace(
        '_body_entry', '_guardian_proximity.entry')
    rows[index:index + 2] = [measure,
        {'condition': '_guardian_source_body.metadata.plane == _guardian_moving_body.metadata.plane',
         'on_true': [
            {'spatial_query': {'scene': 'state.spatial_scene', 'operation': 'body_proximity',
                'query': {'map_id': '{state.spatial_scene.map.id}',
                    'moving_entity_id': '{_guardian_moving_body.id}',
                    'fixed_entity_id': '{_guardian_source_body.id}',
                    'destination': '{end}', 'radius': 10}}, 'store_as': '_guardian_proximity'},
            trigger]}]
    tool['description'] = 'Resolve first Guardian entry through actual continuous body proximity and recorded turn limits.'
