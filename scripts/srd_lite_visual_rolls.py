"""Bind existing spell dice to the shared renderer, without changing a mechanic."""
from .srd_lite_damage_roll_receipts import bind_original_damage_roll_receipts, saved_roll_name

def _lists(rows):
    yield rows
    for effect in rows:
        for branch in ('on_true', 'on_false'):
            yield from _lists(effect.get(branch, []))
        if 'for_each' in effect:
            yield from _lists(effect['for_each']['do'])


def _caption(message, field):
    return {'call': 'format_srd_information_projection', 'params': {'message': message},
            'export_context': {field: '_info_projection_message'}}


def _save_equation():
    return {'die_sides': 20, 'roll': '_card_d20_roll.total',
            'modifier': '_card_d20_modifier', 'total': '_card_d20_total'}


def bind_area_save_source_caption(runtime):
    """Name the actual area spell and damage type beside its recorded saving throw."""
    owner = runtime['tools']['resolve_spell_area_save_damage_target']
    owner['effects'].insert(0, {'set': {'field': '_visual_damage_spell_name',
        'value': 'definitions.srd_lite_spell_display_names[spell]'}})
    count = 0
    for rows in _lists(owner['effects']):
        for effect in rows:
            if effect.get('call') != 'format_srd_information_projection' or effect.get(
                    'export_context') != {'_visual_save_caption': '_info_projection_message'}:
                continue
            message = effect['params']['message']
            assert message == '{save_ability} saving throw · DC {spell_save_dc} · {_spell_save_outcome}'
            effect['params']['message'] = '{_visual_damage_spell_name} · {damage_type} damage · ' + message
            count += 1
    assert count == 2


def _capture_healing_totals(runtime):
    """Carry native healing scalars with their existing dice into final card assembly."""
    captures = [effect['set']['value'] for rows in _lists(runtime['tools']['cast_spell']['effects'])
                for effect in rows if effect.get('set', {}).get('field') == '_retained_cast_tail_context']
    if len(captures) != 1 or not isinstance(captures[0], list):
        raise ValueError('Keep the sole native final cast context capture for healing cards')
    fields = ('_spell_healing_modifier', '_spell_healing_amount')
    for field in fields:
        key = "'" + field + "'"
        if any(row.get('key') == key for row in captures[0]):
            raise ValueError('Healing card scalar is already captured: ' + field)
        captures[0].append({'key':key, 'missing':field + ' == null', 'value':field})
    return fields


def _preserve_slow_null_roll(runtime):
    """Keep a settled no-roll Slow decision as null when a staged cast resumes."""
    owner = runtime['tools']['resolve_srd_slow_cast_failure']
    if sum(e.get('set', {}).get('field') == '_slow_cast_failure_roll' and
           e['set'].get('value') is None for rows in _lists(owner['effects']) for e in rows) != 1:
        raise ValueError('Keep the native typed null Slow roll initializer')
    source = '_slow_cast_event.slow_cast.roll'
    matches = [(rows, index) for rows in _lists(owner['effects']) for index, e in enumerate(rows)
               if e.get('set') == {'field': '_slow_cast_failure_roll', 'value': source}]
    if len(matches) != 1:
        raise ValueError('Keep the sole native saved Slow roll restore')
    rows, index = matches[0]
    rows[index] = {'condition': source + ' != null', 'on_true': [rows[index]]}
    return rows[index]


def _feature_recorded_equations(owner, name):
    """Feature existing recipient equations at each public result-card boundary."""
    card = owner.get('metadata', {}).get('result_card')
    if not card:
        return False
    details = card.get('details')
    if not isinstance(details, dict) or set(details) != {'path'} or not details['path'].startswith('context.'):
        raise ValueError('Use the existing public recipient rows: ' + name)
    source = details['path'].removeprefix('context.')
    owner['effects'].extend([
        {'set': {'field': '_spell_featured_equations', 'value': []}},
        {'for_each': {'collection': source, 'as': '_visual_equation_row', 'do': [
            {'condition': '_visual_equation_row.equation != null and len(_spell_featured_equations) < 4',
             'on_true': [{'list_push': {'list': '_spell_featured_equations',
                                      'value': '_visual_equation_row'}}]},
        ]}},
    ])
    card['equations'] = {'path': 'context._spell_featured_equations'}
    return True


def _bind_dragon_breath_damage_pool(runtime):
    """Show the recorded raw breath dice while keeping source penalties separate."""
    card = runtime['tools']['use_srd_dragon_breath']['metadata']['result_card']
    rows = {row['label']: row for row in card['summary']}
    expected = {'Source', 'Chosen type', 'Original save DC', 'Damage dice',
                'Raw amount', 'After source penalty'}
    if set(rows) != expected:
        raise ValueError('Keep the native retained breath card fields')
    card['subtitle'] = rows['Chosen type']['value']
    damage = {**rows['Raw amount'], 'label': 'Raw damage', 'equation': {
        'die_sides': 6,
        'rolls': {'path': 'context._breath_damage_dice'},
        'roll': {'path': 'context._breath_raw_damage'},
        'modifier': 0,
        'total': {'path': 'context._breath_raw_damage'},
    }}
    card['summary'] = [rows['Original save DC'], damage,
                       {**rows['After source penalty'], 'label': 'Damage before defenses'}]


def _bind_target_roll_widgets(runtime):
    """Feature individual attack and damage-save rolls beside their native receipts."""
    sites = 0
    for name, expected in (('cast_spell_attack_damage', 3),
                           ('resolve_spell_multi_attack_target', 1),
                           ('cast_spell_single_target_save_damage', 1)):
        count = 0
        for rows in list(_lists(runtime['tools'][name]['effects'])):
            for index in range(len(rows) - 1, -1, -1):
                recorder = rows[index]
                if recorder.get('call') != 'record_srd_spell_target_result':
                    continue
                brief = recorder['params']['brief']
                if brief == 'Matching d8 results: a leap is available':
                    continue
                if brief not in ('Attack total {_card_d20_total}; awaiting defense',
                                 '{_card_attack_label}; damage {_card_damage}',
                                 'Save {_spell_save_outcome}; damage {_card_damage}'):
                    raise ValueError('The individual native roll receipt changed: ' + name)
                push = {'list_push': {'list': '_spell_detail_rows', 'value': '_spell_target_detail'}}
                if rows[index + 2:index + 3] != [push]:
                    raise ValueError('Keep the individual full calculation before its widget: ' + name)
                caption = 'Attack roll · AC {_spell_target_armor_class} · {_card_attack_label}'
                if 'awaiting defense' in brief:
                    caption = 'Attack roll · awaiting defense'
                elif name == 'cast_spell_single_target_save_damage':
                    caption = '{save_ability} saving throw · DC {_spell_save_dc} · {_spell_save_outcome}'
                rows[index + 3:index + 3] = [
                    {'condition': '_card_d20_roll != null', 'on_true': [
                        _caption(caption, '_visual_target_roll_caption'),
                        {'list_push': {'list': '_spell_detail_rows', 'value': {
                            'label': 'target.name', 'value': '_visual_target_roll_caption',
                            'equation': _save_equation()}}},
                    ]},
                ]
                count += 1
        if count != expected:
            raise ValueError('Keep each actual individual roll branch: ' + name)
        sites += count
    return sites


def _bind_control_save_widgets(runtime):
    """Feature recorded condition/control saves and composite-damage recipient saves."""
    sites = 0
    for name, caption in (
        ('cast_spell_multi_target_timed_repeating_save_condition',
         '{save_ability} saving throw · DC {_timed_condition_save_dc} · '
         '{_timed_condition_initial_save_outcome} · {_card_condition_effect}'),
        ('cast_spell_multi_target_timed_control',
         '{save_ability} saving throw · DC {_timed_condition_save_dc} · '
         '{_timed_condition_initial_save_outcome} · {_card_condition_effect}'),
        ('resolve_spell_area_save_condition_target',
         '{save_ability} saving throw · DC {spell_save_dc} · {_spell_save_outcome} · {_area_effect_text}'),
        ('resolve_spell_area_composite_damage_target',
         '{save_ability} saving throw · DC {spell_save_dc} · {_spell_save_outcome}'),
    ):
        count = 0
        for rows in list(_lists(runtime['tools'][name]['effects'])):
            for index in range(len(rows) - 1, -1, -1):
                if rows[index].get('call') != 'record_srd_spell_target_result':
                    continue
                push = {'list_push': {'list': '_spell_detail_rows', 'value': '_spell_target_detail'}}
                if rows[index + 2:index + 3] != [push]:
                    raise ValueError('Keep the native control save receipt before its widget: ' + name)
                rows[index + 3:index + 3] = [
                    {'condition': '_card_d20_roll != null', 'on_true': [
                        _caption(caption, '_visual_control_save_caption'),
                        {'list_push': {'list': '_spell_detail_rows', 'value': {
                            'label': '_spell_target_detail.label', 'value': '_visual_control_save_caption',
                            'equation': _save_equation()}}},
                    ]},
                ]
                count += 1
        if count != 1:
            raise ValueError('Keep the sole native control save receipt: ' + name)
        sites += count
    return sites


def _bind_beam_damage_widgets(runtime):
    """Keep each beam's own dice visible with its native applied-damage receipt."""
    owner = runtime['tools']['resolve_spell_multi_attack_target']
    sites = 0
    original = saved_roll_name('_spell_damage')
    for rows in list(_lists(owner['effects'])):
        for index in range(len(rows) - 1, -1, -1):
            recorder = rows[index]
            if recorder.get('call') != 'record_srd_spell_target_result':
                continue
            if recorder['params']['brief'] != '{_card_attack_label}; damage {_card_damage}':
                raise ValueError('Keep the actual completed beam receipt')
            if rows[index + 3].get('condition') != '_card_d20_roll != null':
                raise ValueError('Keep the beam attack widget before its damage dice')
            rows[index + 4:index + 4] = [
                {'condition': original + '.results != null and len(' + original + '.results) > 0',
                 'on_true': [
                    _caption('{damage_type} damage dice · applied {_card_damage} '
                             '(HP {_card_hp_lost}; temporary HP {_card_temp_lost})',
                             '_visual_beam_damage_caption'),
                    {'list_push': {'list': '_spell_detail_rows', 'value': {
                        'label': '_spell_target_detail.label', 'value': '_visual_beam_damage_caption',
                        'equation': {'die_sides': original + '.sides', 'rolls': original + '.results',
                            'roll': original + '.total',
                            'modifier': '_spell_damage.modifier + _spell_damage.total - ' + original + '.total',
                            'total': '_spell_damage.total + _spell_damage.modifier'}}}},
                ]},
            ]
            sites += 1
    if sites != 1:
        raise ValueError('Keep the sole completed per-beam damage receipt')
    return sites


def _bind_order_save_widgets(runtime):
    """Forward the actual Command/order saving throw into the existing recipient card rows."""
    tools=runtime['tools'];field='_visual_order_save_row'
    save=tools['resolve_srd_control_behavior_save']
    save['effects'].insert(0,{'set':{'field':field,'value':None}})
    matches=[(rows,index) for rows in _lists(save['effects']) for index,effect in enumerate(rows)
             if effect.get('set')=={'field':'_control_save_detail','value':'_recorded_save_row.value'}]
    if len(matches)!=1:
        raise ValueError('Keep the actual rolled control save receipt')
    rows,index=matches[0]
    rows[index+1:index+1]=[
        _caption('Wisdom saving throw · DC {save_dc} · {_control_save_outcome}','_visual_order_save_caption'),
        {'set':{'field':field,'value':{'label':'_control_save_label','value':'_visual_order_save_caption',
            'equation':{'die_sides':20,'roll':'_d20_test.total',
                'modifier':'_combined_modifier','total':'_adjusted_total'}}}}]
    owner=tools['cast_srd_control_orders'];count=0
    for rows in list(_lists(owner['effects'])):
        for index in range(len(rows)-1,-1,-1):
            effect=rows[index]
            if effect.get('call')=='resolve_srd_control_behavior_save':
                effect['export_context'][field]=field
            if effect.get('list_push')=={'list':'_spell_detail_rows',
                    'value':{'label':'_control_save_label','value':'_control_save_detail'}}:
                rows[index+1:index+1]=[{'condition':field+' != null','on_true':[
                    {'list_push':{'list':'_spell_detail_rows','value':field}}]}]
                count+=1
    if count!=1:
        raise ValueError('Keep the sole native order recipient calculation')
    return count


def _bind_sleet_save_widgets(runtime):
    """Carry Sleet's original save through the established hazard recipient rows."""
    tools=runtime['tools'];field='_visual_sleet_save_row'
    save=tools['apply_srd_sleet_storm_save']
    rolls=[effect for rows in _lists(save['effects']) for effect in rows
           if effect.get('call')=='d20_test']
    if len(rolls)!=1:
        raise ValueError('Keep the sole native Sleet saving throw')
    for scalar in ('_combined_modifier','_adjusted_total'):
        rolls[0]['export_context'][scalar]=scalar
    row={'label':'target_state.name','value':'_visual_sleet_save_caption'}
    save['effects'].extend([
        _caption('Sleet Storm · Dexterity save · DC {_sleet_source.save_dc} · {_outcome}',
                 '_visual_sleet_save_caption'),
        {'set':{'field':field,'value':row}},
        {'condition':'_d20_test != null','on_true':[
            {'set':{'field':field,'value':{**row,'equation':{'die_sides':20,
                'roll':'_d20_test.total','modifier':'_combined_modifier','total':'_adjusted_total'}}}}]}])
    event=tools['resolve_srd_sleet_storm_event']
    event['effects'].insert(0,{'set':{'field':field,'value':None}})
    calls=[effect for rows in _lists(event['effects']) for effect in rows
           if effect.get('call')=='apply_srd_sleet_storm_save']
    if len(calls)!=1:
        raise ValueError('Keep the existing once-per-event Sleet save')
    calls[0].setdefault('export_context',{})[field]=field
    sites=0
    for name in ('resolve_srd_environment_hazard_event','resolve_srd_environment_hazard_start_turn'):
        owner=tools[name]
        if not any(effect.get('set')=={'field':'_lite_daggers_detail_rows','value':[]}
                   for effect in owner['effects']):
            raise ValueError('Keep the established hazard recipient-row initializer: '+name)
        matches=[(rows,index) for rows in _lists(owner['effects']) for index,effect in enumerate(rows)
                 if effect.get('call')=='resolve_srd_sleet_storm_event']
        if len(matches)!=1:
            raise ValueError('Keep the actual Sleet event dispatch: '+name)
        rows,index=matches[0]
        rows[index].setdefault('export_context',{})[field]=field
        rows[index+1:index+1]=[{'condition':field+' != null','on_true':[
            {'list_push':{'list':'_lite_daggers_detail_rows','value':field}}]}]
        sites+=1
    return sites


def _bind_repeat_save_widgets(runtime):
    """Display native repeating condition saves while retaining their full text receipts."""
    owner = runtime['tools']['resolve_srd_timed_condition_end_turn']
    recorded = 0
    for rows in list(_lists(owner['effects'])):
        for index in range(len(rows) - 1, -1, -1):
            if rows[index].get('call') != 'record_srd_end_turn_save':
                continue
            rows[index + 1:index + 1] = [
                {'condition': '_timed_condition_end_turn_roll != null', 'on_true': [
                    {'set': {'field': '_visual_repeat_spell_name',
                             'value': '_timed_condition_end_turn_source.spell_id'}},
                    {'condition': '_visual_repeat_spell_name in definitions.srd_lite_spell_display_names',
                     'on_true': [{'set': {'field': '_visual_repeat_spell_name',
                         'value': 'definitions.srd_lite_spell_display_names[_visual_repeat_spell_name]'}}]},
                    _caption('{target_state.name} · {_visual_repeat_spell_name}', '_visual_repeat_label'),
                    _caption('{_timed_condition_end_turn_source.save_ability} saving throw · '
                             '{_timed_condition_end_turn_source.condition} · '
                             'DC {_timed_condition_end_turn_source.save_dc} · '
                             '{_timed_condition_end_turn_outcome} · {_timed_condition_source_status}',
                             '_visual_repeat_caption'),
                    {'list_push': {'list': '_timed_condition_save_details', 'value': {
                        'label': '_visual_repeat_label', 'value': '_visual_repeat_caption',
                        'equation': {'die_sides': 20, 'roll': '_timed_condition_end_turn_roll.total',
                            'modifier': '_timed_condition_save_modifier',
                            'total': '_timed_condition_save_total'}}}},
                ]},
            ]
            recorded += 1
    if recorded != 1:
        raise ValueError('Keep the sole native repeating condition save receipt')
    return recorded


def bind_recorded_spell_roll_widgets(runtime):
    """Keep receipts intact and add saved, labelled equations at their existing owners."""
    tools = runtime['tools']
    repeat_receipts = _bind_repeat_save_widgets(runtime)
    target_receipts = _bind_target_roll_widgets(runtime)
    control_receipts = _bind_control_save_widgets(runtime)
    order_receipts = _bind_order_save_widgets(runtime)
    sleet_receipts = _bind_sleet_save_widgets(runtime)
    damage_receipts = bind_original_damage_roll_receipts(runtime)
    healing_fields = _capture_healing_totals(runtime)
    _preserve_slow_null_roll(runtime)
    beam_damage_receipts = _bind_beam_damage_widgets(runtime)
    for name, recorder in (
        ('resolve_spell_area_save_damage_target', 'record_srd_spell_area_target_result'),
        ('resolve_srd_dragon_breath_target', 'record_srd_dragon_breath_target_result'),
    ):
        owner = tools[name]
        owner['effects'].insert(0, {'set': {'field': '_visual_save_row', 'value': None}})
        recorded = 0
        for rows in list(_lists(owner['effects'])):
            for index in range(len(rows)-1, -1, -1):
                if rows[index].get('call') != recorder:
                    continue
                rows[index+1:index+1] = [{'condition': '_card_d20_roll != null', 'on_true': [
                    _caption('{save_ability} saving throw · DC {spell_save_dc} · '
                             '{_spell_save_outcome}', '_visual_save_caption'),
                    {'set': {'field': '_visual_save_row', 'value': {
                        'label': 'target.name', 'value': '_visual_save_caption',
                        'equation': _save_equation()}}},
                ]}]
                recorded += 1
        if recorded != 2:
            raise ValueError('Keep the rolled and automatic save receipt branches: ' + name)
        concentration_index = next((index for index, effect in enumerate(owner['effects'])
            if effect.get('condition') == '_area_target_concentration_save_roll != null'), None)
        if concentration_index is None:
            raise ValueError('Keep the real concentration ordering: ' + name)
        owner['effects'].insert(concentration_index, {
            'condition': '_visual_save_row != null', 'on_true': [
                {'list_push': {'list': '_spell_detail_rows', 'value': '_visual_save_row'}},
            ],
        })
        concentration = 0
        for rows in list(_lists(owner['effects'])):
            for index in range(len(rows)-1, -1, -1):
                row = rows[index].get('list_push', {})
                if row.get('value') != {'label': '_area_concentration_label',
                                        'value': '_area_concentration_text'}:
                    continue
                rows[index+1:index+1] = [
                    _caption('Concentration · DC {_area_target_concentration_save_dc} · '
                             '{_area_target_concentration_save_outcome}', '_visual_concentration_caption'),
                    {'list_push': {'list': '_spell_detail_rows', 'value': {
                        'label': '_area_concentration_label',
                        'value': '_visual_concentration_caption',
                        'equation': {'die_sides': 20,
                            'roll': '_area_target_concentration_save_roll.total',
                            'modifier': '_area_target_concentration_save_modifier',
                            'total': '_area_concentration_total'}}}},
                ]
                concentration += 1
        if concentration != 1:
            raise ValueError('Keep the real concentration receipt owner: ' + name)

    pool_rows = 0
    for rows in _lists(tools['finish_srd_retained_cast_tail']['effects']):
        for effect in rows:
            row = effect.get('list_push', {})
            value = row.get('value')
            if row.get('list') != '_spell_card_summary' or not isinstance(value, dict):
                continue
            source = value.get('value')
            if not isinstance(source, str) or not source.endswith('.results') or not source.startswith('_spell_'):
                continue
            roll = source.removesuffix('.results')
            original = saved_roll_name(roll)
            value['equation'] = {'die_sides': original+'.sides', 'rolls': original+'.results',
                                 'roll': original+'.total',
                                 'modifier': roll+'.modifier + '+roll+'.total - '+original+'.total',
                                 'total': roll+'.total + '+roll+'.modifier'}
            if roll == '_spell_healing':
                # The healing owner adds its admitted casting modifier after rolling.
                value['equation']['die_sides'] = roll+'.sides'
                value['equation']['rolls'] = source
                value['equation']['roll'] = roll+'.total'
                value['equation']['modifier'] = '_spell_healing_modifier'
                value['equation']['total'] = '_spell_healing_amount'
                original = roll
            check = original+'.results != null and len('+original+'.results) > 0'
            if 'condition' in effect:
                check = '(' + effect['condition'] + ') and ' + check
            effect['condition'] = check
            pool_rows += 1
    if pool_rows != 5:
        raise ValueError('Keep all five existing damage/component/healing pool owners')

    previews = []
    for name in ('cast_spell', 'cast_spell_outside_combat', 'use_srd_dragon_breath',
                 'move_srd_scene_creature', 'begin_combat_turn', 'end_combat_turn',
                 'complete_srd_pending_action'):
        owner = tools[name]
        card = owner.get('metadata', {}).get('result_card')
        if not _feature_recorded_equations(owner, name):
            continue
        if name == 'end_combat_turn':
            card['details_label'] = 'Saving throws and effects'
            conditions = [row for row in card.get('summary', [])
                          if row.get('value') == {'path': 'context._end_turn_conditions'}]
            if len(conditions) != 1:
                raise ValueError('Keep the native post-turn conditions summary')
            owner['effects'].extend([
                {'set': {'field': '_end_turn_display_conditions', 'value': "'None'"}},
                {'condition': '_end_turn_conditions != null and len(_end_turn_conditions) > 0',
                 'on_true': [{'set': {'field': '_end_turn_display_conditions',
                                     'value': '_end_turn_conditions'}}]},
            ])
            conditions[0]['value'] = {'path': 'context._end_turn_display_conditions'}
        previews.append(name)
    _bind_dragon_breath_damage_pool(runtime)
    bind_area_save_source_caption(runtime)
    return {'save_receipt_owners': 2, 'repeat_save_receipt_owners': repeat_receipts,
            'individual_roll_receipt_sites': target_receipts,
            'control_save_receipt_sites': control_receipts,
            'order_save_receipt_sites': order_receipts,
            'sleet_save_receipt_sites': sleet_receipts,
            'beam_damage_receipt_sites': beam_damage_receipts,
            **damage_receipts,
            'healing_cast_context_fields': len(healing_fields),
            'pool_rows': pool_rows, 'preview_owners': previews}
