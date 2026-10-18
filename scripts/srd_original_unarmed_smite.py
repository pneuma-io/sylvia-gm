"""Join a real original Unarmed Strike to the existing after-hit Smite owner.

The optional request pauses only an accepted damage attack after Mirror Image.
It saves original dice and callbacks, then uses the original pending completion
and typed receiving lifecycle. No attack, spell use, or dice are replayed.
"""
from copy import deepcopy
import re

INTENT = 'request_after_hit_smite'
MARKER = 'original_unarmed_divine_smite_v1'
RESUME = 'resume_srd_unarmed_hit_smite'
RECEIPTS = ('_hit_rider_receipts', '_hit_type_receipts', '_hit_raw_components')


def _set(field, value):
    return {'set': {'field': field, 'value': value}}


def _require(condition, error):
    return {'require': {'condition': condition, 'error': error}}


def _when(condition, yes, no=None):
    result = {'condition': condition, 'on_true': yes}
    if no is not None:
        result['on_false'] = no
    return result


def _nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def _forward(parameter):
    name = parameter['name']
    return '{_' + name + '_id}' if parameter.get('resolve') else '{' + name + '}'


def _rows(names):
    return [{'key': repr(name), 'missing': name + ' == null', 'value': name}
            for name in sorted(names)]


def _restore(collection):
    return {'for_each': {'collection': collection, 'as': '_unarmed_saved_row', 'do': [
        _when('_unarmed_saved_row.missing == true',
              [_set('{_unarmed_saved_row.key}', None)],
              [_set('{_unarmed_saved_row.key}', '_unarmed_saved_row.value')])
    ]}}


def bind_original_unarmed_smite(runtime, *, selected=('divine_smite',)):
    """Return exact changed owners; retain all unrequested original strike paths."""
    if 'divine_smite' not in selected:
        return []
    tools = runtime['tools']
    if tools['resolve_unarmed_strike'].get('metadata', {}).get(MARKER):
        if RESUME not in tools:
            raise ValueError('Incomplete installed original unarmed Smite bridge.')
        return []
    for name in ('capture_srd_final_hit_riders', 'apply_srd_saved_hit_qualifying_damage',
                 'prepare_srd_smite_hit', 'begin_srd_after_hit_smite'):
        if name not in tools:
            raise ValueError('Missing real unarmed Smite receiver: ' + name)
    public = deepcopy(tools['resolve_unarmed_strike'])
    core = deepcopy(tools['resolve_srd_unarmed_effect'])
    complete = deepcopy(tools['complete_srd_pending_action'])
    # This owner lives on the public wrapper, not in its original child context.
    # The delayed wrapper suffix must preserve its genuine chosen state identity.
    time_stop = next(p for p in public['parameters'] if p['name'] == 'time_stop_state')
    if not any(p['name'] == 'time_stop_state' for p in core['parameters']):
        core['parameters'].append(deepcopy(time_stop))
    original_parameters = deepcopy(core['parameters'])
    flag = {'name': INTENT, 'type': 'boolean', 'required': False, 'default': False}
    for owner in (public, core):
        if any(p['name'] == INTENT for p in owner['parameters']):
            raise ValueError('Unexpected pre-existing unarmed Smite intent.')
        owner['parameters'].append(deepcopy(flag))
    calls = [node for node in _nodes(public['effects'])
             if node.get('call') == 'resolve_srd_unarmed_effect']
    if len(calls) != 1 or not isinstance(calls[0].get('export_context'), dict):
        raise ValueError('Require the original explicit public unarmed callback.')
    invoke = calls[0]
    invoke['params'][INTENT] = '{' + INTENT + '}'
    invoke['params']['time_stop_state'] = '{_time_stop_state_id}'
    invoke['export_context'].update({name: name for name in
        ('_unarmed_smite_deferred', '_unarmed_pending_action_id')})
    if public['effects'][0].get('call') != 'spend_srd_attack' or public['effects'][2] is not invoke:
        raise ValueError('Unexpected original Unarmed Strike attack-spend boundary.')
    public_tail = deepcopy(public['effects'][3:])
    public['effects'] = [
        _when(INTENT + ' == true', [
            _require("effect == 'damage'", 'An after-hit Smite window requires a damaging Unarmed Strike.'),
            _require('combat.turn_active == true and combat.active_actor == _actor_state_id',
                     'Request this after-hit Bonus Action on the real attacker turn.'),
            _require('_actor_id == _actor_state_id',
                     'Use the actual participant and its own attack state for this Smite window.'),
            _require('lasting_state.pending_srd_action_stack == null or '
                     'len(lasting_state.pending_srd_action_stack) == 0',
                     'Complete the existing pending action before this Unarmed Strike.'),
        ]),
        *public['effects'][:3],
        _when('_unarmed_smite_deferred != true', public_tail),
    ]
    # Exact final damage producer: real d20 and Mirror already settled above it.
    branches = [e for e in core['effects'] if e.get('condition') == "effect == 'damage'" and 'on_true' in e]
    if len(branches) != 1:
        raise ValueError('Require one original damaging Unarmed Strike branch.')
    branch = branches[0]
    damage_indices = [i for i, e in enumerate(branch['on_true'])
                      if e.get('call') == 'apply_srd_qualifying_damage']
    if len(damage_indices) != 1 or not any(
        node.get('call') == 'resolve_srd_mirror_image_hit'
        for node in _nodes(branch['on_true'][:damage_indices[0]])):
        raise ValueError('Unarmed bridge requires the real final post-Mirror damage boundary.')
    split = damage_indices[0]
    damage_tail = deepcopy(branch['on_true'][split:])
    if len(damage_tail) < 4 or not all('set' in e for e in damage_tail[3:]):
        raise ValueError('Require the original pure unarmed damage presentation tail.')
    damage_presentation, damage_tail = damage_tail[3:], damage_tail[:3]
    branch_index = core['effects'].index(branch)
    if core['effects'][branch_index + 1].get('condition') != "effect != 'damage'":
        raise ValueError('Preserve the independent original Grapple and Shove branch.')
    common_tail = deepcopy(core['effects'][branch_index + 2:])
    # Restore all actual private reads/card outputs because child contexts are fresh.
    # Pure presentation/default assignments execute once before the decision;
    # the original effectful tail is factored into one shared leaf.
    callback_start = next((i for i, e in enumerate(common_tail) if e.get('call') or
                           any(n.get('call') for n in _nodes(e))), None)
    if callback_start is None:
        raise ValueError('The original unarmed effectful suffix is absent.')
    common_defaults, common_callbacks = common_tail[:callback_start], common_tail[callback_start:]
    suffix = damage_tail + common_callbacks + public_tail
    suffix_text = repr(suffix)
    helper_parameters = [p for p in original_parameters if p.get('resolve') or
                         re.search(r'(?<![A-Za-z0-9_])' + re.escape(p['name']) +
                                   r'(?![A-Za-z0-9_])', suffix_text)]
    saved_names = set(invoke['export_context']) - {
        '_unarmed_smite_deferred', '_unarmed_pending_action_id'}
    saved_names.update(re.findall(r'(?<![A-Za-z0-9])_[A-Za-z][A-Za-z0-9_]*', repr(suffix)))
    saved_names -= {'_actor_id', '_actor_state_id', '_target_state_id',
                    '_lasting_state_id', '_time_stop_state_id'}
    # Forward only genuine parameters, with explicit null capture rather than
    # interpolating absent nullable values into strings.
    parameter_rows = [{'key': repr(p['name']), 'missing': _forward(p).strip('{}') + ' == null',
                       'value': _forward(p).strip('{}')} for p in helper_parameters]
    event = 'lasting_state.pending_srd_actions.{_unarmed_pending_action_id}.'
    allocation = deepcopy(tools['begin_srd_character_attack']['effects'])
    starts = [i for i, e in enumerate(allocation)
              if e.get('set', {}).get('field') == 'lasting_state.pending_srd_actions'
              and e.get('condition') == 'lasting_state.pending_srd_actions == null']
    frames = [i for i, e in enumerate(allocation)
              if e.get('set', {}).get('field') == 'lasting_state.pending_srd_actions.{_pending_action_id}']
    if len(starts) != 1 or len(frames) != 1 or frames[0] <= starts[0]:
        raise ValueError('Require the original sequence-and-stack pending allocator.')
    allocate = allocation[starts[0]:frames[0]]
    # These are the unchanged original allocator expressions, only aliasing its ID.
    capture = [
        *allocate,
        _set('_unarmed_pending_action_id', '_pending_action_id'),
        _set(event[:-1], {'kind': "'weapon_attack'", 'owner_kind': "'unarmed_strike'",
                         'status': "'open'", 'stage': "'attack_hit_riders'",
                         'actor_state_id': '_actor_state_id', 'target_state_id': '_target_state_id',
                         'turn_actor': 'combat.active_actor', 'attack_roll': '_d20_test',
                         'attack_outcome': "'hit'", 'parameters': {}, 'context': {}}),
        _when('_unarmed_critical_hit == true', [_set(event + 'attack_outcome', "'critical_hit'")]),
        {'list_push': {'list': '_pending_stack', 'value': '_unarmed_pending_action_id'}},
        _set('lasting_state.pending_srd_action_stack', '_pending_stack'),
        _set('_unarmed_parameter_rows', parameter_rows),
        {'for_each': {'collection': '_unarmed_parameter_rows', 'as': '_unarmed_saved_row', 'do': [
            _when('_unarmed_saved_row.missing == true',
                  [_set(event + 'parameters.{_unarmed_saved_row.key}', None)],
                  [_set(event + 'parameters.{_unarmed_saved_row.key}', '_unarmed_saved_row.value')])
        ]}},
        {'call': 'capture_srd_final_hit_riders',
         'params': {'attacker_state': '{_actor_state_id}', 'target_state': '{_target_state_id}',
                    'pending_action_id': '{_unarmed_pending_action_id}',
                    'minor_elementals_damage_type': '{minor_elementals_damage_type}'}},
        _set(event + 'unarmed_suffix_rows', '_unarmed_suffix_rows'),
        _set('_unarmed_damage', None),
    ]
    branch['on_true'][split:] = [*damage_presentation,
        _set('_unarmed_smite_deferred',
             INTENT + ' == true and _unarmed_success == true and _mirror_redirected != true')]
    ordinary_invoke = {'call': RESUME,
        'params': {**{p['name']: _forward(p) for p in helper_parameters},
                   '_unarmed_suffix_rows': '{_unarmed_suffix_rows}'}, 'export_context': '*'}
    core['effects'][branch_index + 2:] = [*common_defaults,
        _set('_unarmed_suffix_rows', _rows(saved_names)),
        _when('_unarmed_smite_deferred == true', capture, [ordinary_invoke])]
    core['effects'][:0] = [_set('_unarmed_smite_deferred', False),
                          _set('_unarmed_pending_action_id', "''")]
    saved_damage = deepcopy(damage_tail[0])
    saved_damage['call'] = 'apply_srd_saved_hit_qualifying_damage'
    saved_damage['params']['_hit_rider_event_id'] = '{pending_action_id}'
    saved_damage['export_context'].update({name: name for name in RECEIPTS})
    helper = {
        'description': 'Complete the original accepted Unarmed Strike and its saved damage once.',
        'parameters': helper_parameters + [
            {'name': 'pending_action_id', 'required': False, 'default': ''},
            {'name': '_unarmed_suffix_rows', 'type': 'array', 'items': {'type': 'object'}}],
        'effects': [
            _when("pending_action_id != ''", [
                _set('_unarmed_saved_hit', 'lasting_state.pending_srd_actions[pending_action_id]'),
                _require("_unarmed_saved_hit.owner_kind == 'unarmed_strike' and "
                     "_unarmed_saved_hit.stage == 'attack_hit_riders' and "
                     "_unarmed_saved_hit.status == 'open' and "
                     '_unarmed_saved_hit.actor_state_id == _actor_state_id and '
                     '_unarmed_saved_hit.target_state_id == _target_state_id and '
                     '_unarmed_saved_hit.rider_marks_captured == true and '
                     'lasting_state.pending_srd_action_stack[-1] == pending_action_id',
                     'Resume only this authentic innermost final Unarmed Strike hit.'),
                _set('lasting_state.pending_srd_actions.{pending_action_id}.stage', "'attack_hit_settling'"),
            ]),
            _restore('_unarmed_suffix_rows'),
            *[_set(name, []) for name in RECEIPTS],
            _when("effect == 'damage'", [
                _when("pending_action_id != ''", [saved_damage], [damage_tail[0]]),
                *deepcopy(damage_tail[1:]),
            ]),
            *common_callbacks,
            _when("pending_action_id != ''", [
                *public_tail,
                _set('lasting_state.pending_srd_actions.{pending_action_id}.stage', "'attack_hit_resolved'"),
            ]),
        ],
        'metadata': {'category': 'internal', 'visible_to_player': False,
                     'costs_action': False, MARKER: True},
    }
    cleanup = [i for i, e in enumerate(complete['effects'])
               if e.get('set', {}).get('field') == '_pending_completion_kind']
    if len(cleanup) != 1:
        raise ValueError('Require the original common pending completion cleanup owner.')
    complete['effects'].insert(cleanup[0], _when(
        "_pending_action.owner_kind == 'unarmed_strike' and "
        "_pending_action.stage == 'attack_hit_riders' and _pending_action.status == 'open'", [
            {'call': RESUME,
             'params': {**{p['name']: '{_pending_action.parameters.' + p['name'] + '}'
                          for p in helper_parameters}, 'pending_action_id': '{pending_action_id}',
                        '_unarmed_suffix_rows': '{_pending_action.unarmed_suffix_rows}'},
             'export_context': {**{name: name for name in RECEIPTS},
                                **{name: name for name in invoke['export_context']
                                   if name not in ('_unarmed_smite_deferred', '_unarmed_pending_action_id')}}},
            _set('_pending_card_title', "'Unarmed Strike'"),
            _set('_pending_card_summary', [{'label': "'Outcome'", 'value': '_unarmed_result'},
                                         {'label': "'Damage'", 'value': '_unarmed_damage'}]),
            _set('_pending_card_details', [{'label': "'D20 results'", 'value': '_d20_test.results'},
                                          {'label': "'HP lost'", 'value': '_unarmed_hp_lost'}]),
        ]))
    # Existing card rider formatting must see the fresh resumed receipts first.
    effects = complete['effects']
    resumed = effects.pop(next(i for i, e in enumerate(effects)
                              if e.get('condition', '').startswith("_pending_action.owner_kind == 'unarmed_strike'")))
    before_cards = next(i for i, e in enumerate(effects)
                        if e.get('condition') == '_hit_rider_deferred != true and len(_hit_rider_receipts) > 0')
    effects.insert(before_cards, resumed)
    for owner in (public, core, complete):
        owner.setdefault('metadata', {})[MARKER] = True
    tools.update({'resolve_unarmed_strike': public, 'resolve_srd_unarmed_effect': core,
                  'complete_srd_pending_action': complete, RESUME: helper})
    return ['resolve_unarmed_strike', 'resolve_srd_unarmed_effect',
            'complete_srd_pending_action', RESUME]
