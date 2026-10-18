"""Let a controlled Otherworldly Steed share its caster's turn using native budgets."""
from copy import deepcopy
from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
from .srd_simulacrum_turns import TURN_FIELDS
from .srd_lite_steed_control import READ, CONTROLLED
from .srd_lite_steed_mount import MOUNT
from .srd_lite_steed_healing_touch import OWNER

BEGIN = 'begin_srd_controlled_steed_phase'
PAUSE = 'pause_srd_controlled_steed_phase'
ACT = 'take_srd_otherworldly_steed_action'
SAVE = 'save_srd_otherworldly_turn_budget'
RESTORE = 'restore_srd_otherworldly_turn_budget'
AUTO = 'initialize_srd_riding_steed_turn'
FINISH = 'finish_srd_riding_steed_turn'
CONTINUE = 'resume_srd_independent_steed_budget'
NATIVE_BEGIN = 'begin_srd_otherworldly_native_turn_body'
NATIVE_END = 'end_srd_otherworldly_native_turn_body'
RIDER = 'combat.otherworldly_steed_phase_rider'
STEED = 'combat.otherworldly_steed_phase_steed'
BUDGET = 'otherworldly_turn_budget'
TOKEN = 'otherworldly_rider_turn'
ROUND = 'otherworldly_rider_round'
ENDED = 'otherworldly_turn_ended'
CONTINUATION = 'otherworldly_independent_continuation'
START_ROWS = '_otherworldly_start_rows'
END_ROWS = '_otherworldly_end_rows'
FIELDS = (*TURN_FIELDS, 'action_taken', 'last_action_source')
ACTION_OWNERS = {'dash': 'dash_combat_action', 'disengage': 'take_disengage_action',
                 'dodge': 'take_dodge_action'}


def _budget_tools():
    """Save only acting-creature fields; initiative, clocks and pending actions stay live."""
    saved = [_set('actor_state.'+BUDGET, {})]
    restored = [_require('actor_state.'+BUDGET+' != null', 'Resume the actual saved creature budget.')]
    for field in FIELDS:
        saved.extend([_set('actor_state.'+BUDGET+'.'+field, None),
            _when('combat.'+field+' != null',
                  [_set('actor_state.'+BUDGET+'.'+field, 'combat.'+field)])])
        restored.extend([_set('combat.'+field, None),
            _when('actor_state.'+BUDGET+'.'+field+' != null',
                  [_set('combat.'+field, 'actor_state.'+BUDGET+'.'+field)])])
    restored.extend([
        _call('refresh_srd_timed_condition_target', {'target_state': '{_actor_state_id}', 'lasting_state': 'state'}),
        _set('combat.active_actor_at_zero_hp', 'actor_state.hp == 0'),
        _set('combat.active_actor_stable', 'actor_state.stable == true'),
        _set('combat.active_actor_spell_slow', 'actor_state.spell_slow == true'),
        _set('combat.active_actor_form_wind_only_actions', 'actor_state.form_wind_only_actions == true'),
        _set('combat.active_actor_form_restricted',
            'actor_state.form_statistics_replaced == true and actor_state.form_can_cast != true'),
        _set('combat.active_actor_can_act', False),
        _when('actor_state.is_alive == true and actor_state.hp > 0 and '
            "not ('incapacitated' in actor_state.conditions or 'paralyzed' in actor_state.conditions or "
            "'petrified' in actor_state.conditions or 'stunned' in actor_state.conditions or "
            "'unconscious' in actor_state.conditions)", [_set('combat.active_actor_can_act', True)]),
        _when('combat.active_actor_can_act != true', [_set('combat.action_available', False)]),
        _call('refresh_srd_movement_budget', {'actor_state': '{_actor_state_id}'})])
    return {
        SAVE: _tool('Save this actual creature\'s current turn budget.', [_entity('actor_state')], saved),
        RESTORE: _tool('Resume this actual creature\'s spent turn budget without refreshing it.',
                      [_entity('actor_state')], restored),
    }


def _phase_card(tool, title):
    """Expose actual remaining budgets without adding a dice widget for a non-roll transition."""
    tool['metadata'] = {'category': 'combat', 'costs_action': False, 'result_card': {
        'version': 1, 'footprint': 'compact', 'title': title,
        'summary': [
            {'label': 'Rider', 'value': {'path': 'context.rider_state.name'}},
            {'label': 'Steed', 'value': {'path': 'context.steed_state.name'}},
            {'label': 'Steed movement (ft)', 'value': {'path': 'context._steed_phase_movement'}},
            {'label': 'Steed Action', 'value': {'path': 'context._steed_phase_action',
                'labels': {'true': 'Available', 'false': 'Spent'}}},
            {'label': 'Steed Bonus Action', 'value': {'path': 'context.steed_state.bonus_action_available',
                'labels': {'true': 'Available', 'false': 'Spent'}}}],
        'details': {'path': 'context.'+START_ROWS}, 'details_label': 'Turn effects'}}
    return tool


def _begin():
    """Enter or resume the exact controlled Steed on the actual riding caster's turn."""
    native = _call(NATIVE_BEGIN, {'actor': '{_steed_state_id}'})
    native['export_context'] = {START_ROWS: '_turn_effect_rows'}
    start = [
        _set('steed_state.'+TOKEN, 'rider_state.turns_started'),
        _set('steed_state.'+ROUND, 'combat.round'),
        _set('steed_state.'+ENDED, False),
        _set('steed_state.'+CONTINUATION, False),
        _set('combat.current_actor', '_steed_state_id'),
        _set('combat.turn_active', False), native]
    tool = _tool('Let the controlled Otherworldly Steed act on its actual riding caster\'s turn. '
        'Use Dash, Disengage or Dodge for its Action; its own Bonus Action stays separate. '
        'Pause this phase to resume the rider, then re-enter without refreshing spent budgets.',
        [_entity('rider_state'), _entity('steed_state')], [
        _call('require_srd_pending_window_closed', {}),
        _call(READ, {'actor_state': '{_steed_state_id}'}, (CONTROLLED,)),
        _require(CONTROLLED+' == true and steed_state.created_caster_state_id == _rider_state_id',
            'Only the actual capable caster riding their own Steed controls this phase.'),
        _require('combat.encounter_active == true and combat.turn_active == true and '
            'combat.active_actor == _rider_state_id and '+RIDER+' == null and '+STEED+' == null',
            'Enter the Steed phase from its actual riding caster\'s active combat turn.'),
        _require('rider_state.turns_started != null and combat.round != null',
            'Use the actual initialized rider turn and encounter round.'),
        _require('state.time_stop_source == null or state.time_stop_source.active != true',
            'A separate Steed cannot act while Time Stop freezes other creatures.'),
        _require('steed_state.'+TOKEN+' != rider_state.turns_started or steed_state.'+ROUND+' != combat.round or '
            'steed_state.'+ENDED+' != true', 'This Steed already ended its actual turn.'),
        _set(START_ROWS, []),
        _call(SAVE, {'actor_state': '{_rider_state_id}'}),
        _set(RIDER, '_rider_state_id'), _set(STEED, '_steed_state_id'),
        {'condition': 'steed_state.'+TOKEN+' != rider_state.turns_started or steed_state.'+ROUND+' != combat.round',
         'on_true': start, 'on_false': [_call(RESTORE, {'actor_state': '{_steed_state_id}'})]},
        _set('_steed_phase_movement', 'combat.movement_remaining'),
        _set('_steed_phase_action', 'combat.action_available'),
        _when('steed_state.'+ENDED+' == true', [
            _set('_steed_phase_movement', 'steed_state.'+BUDGET+'.movement_remaining'),
            _set('_steed_phase_action', False),
            {'list_push': {'list': START_ROWS, 'value': {
                'label': "'Steed turn ended'", 'value': 'steed_state.name'}}}])])
    return _phase_card(tool, 'Steed acting')


def _pause():
    """Pause without resolving end-of-turn conditions or replenishing either creature."""
    return _phase_card(_tool('Pause the controlled Steed and resume its rider\'s existing turn budget. '
        'The Steed can resume later on this rider turn with the movement and actions it has left.',
        [_entity('rider_state'), _entity('steed_state')], [
        _call('require_srd_pending_window_closed', {}),
        _require(RIDER+' == _rider_state_id and '+STEED+' == _steed_state_id and '
            'combat.active_actor == _steed_state_id and rider_state.'+BUDGET+' != null',
            'Pause the actual active Steed phase and its saved rider budget.'),
        _set(START_ROWS, []),
        _set('_steed_phase_movement', 'combat.movement_remaining'),
        _set('_steed_phase_action', 'combat.action_available'),
        _call(SAVE, {'actor_state': '{_steed_state_id}'}),
        _call(RESTORE, {'actor_state': '{_rider_state_id}'}),
        _set(RIDER, None), _set(STEED, None)]), 'Rider acting')


def _initialize():
    """Begin the Steed once at the rider boundary or when the rider actually mounts mid-turn."""
    enter = _call(BEGIN, {'rider_state': '{_rider_state_id}', 'steed_state': '{_steed_state_id}'}, (START_ROWS,))
    return _tool('Initialize the actual riding caster\'s controlled Steed on their shared turn.',
        [_entity('rider_state'), _entity('steed_state')], [
        _set(START_ROWS, []),
        _call(READ, {'actor_state': '{_steed_state_id}'}, (CONTROLLED,)),
        _when(CONTROLLED+' == true and combat.encounter_active == true and combat.turn_active == true and '
            'combat.active_actor == _rider_state_id and '+STEED+' == null', [enter,
            _when('('+STEED+' == _steed_state_id) and '
                '(state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0)',
                [_call(PAUSE, {'rider_state': '{_rider_state_id}', 'steed_state': '{_steed_state_id}'})])])])


def _finish():
    """End the actual Steed once; an incapacitated caster hands off the existing Steed budget."""
    ending = _call(NATIVE_END, {'actor': '{_steed_state_id}'})
    ending['export_context'] = {END_ROWS: '_end_turn_rows'}
    return _tool('Close the controlled Steed at its actual rider turn end without advancing initiative or time.',
        [_entity('rider_state'), _entity('steed_state')], [
        _set(END_ROWS, []),
        _when('steed_state.'+TOKEN+' == rider_state.turns_started and steed_state.'+ROUND+' == combat.round and '
            'steed_state.'+BUDGET+' != null and steed_state.'+ENDED+' != true', [
            _call('require_srd_pending_window_closed', {}),
            _call(READ, {'actor_state': '{_steed_state_id}'}, (CONTROLLED,)),
            {'condition': CONTROLLED+' == true', 'on_true': [
                _call(SAVE, {'actor_state': '{_rider_state_id}'}),
                _call(RESTORE, {'actor_state': '{_steed_state_id}'}), ending,
                _set('steed_state.'+ENDED, True),
                _call(SAVE, {'actor_state': '{_steed_state_id}'}),
                _call(RESTORE, {'actor_state': '{_rider_state_id}'}),
                {'list_push': {'list': END_ROWS, 'value': {
                    'label': "'Steed turn ended'", 'value': 'steed_state.name'}}}],
             'on_false': [_set('steed_state.'+CONTINUATION, True)]}])])


def _continue():
    """Keep already-spent Steed resources when it becomes independent on this initiative count."""
    return _tool('Resume the actual Steed budget handed off from an incapacitated riding caster.',
        [_entity('steed_state'), _entity('caster_state')], [
        _require("steed_state.created_spell_id == 'find_steed' and steed_state.created_active == true and "
            'steed_state.created_caster_state_id == _caster_state_id and '
            'caster_state.created_steed_id == _steed_state_id', 'Keep the actual Steed and source caster.'),
        _require('steed_state.'+CONTINUATION+' == true and steed_state.'+TOKEN+' == caster_state.turns_started and '
            'steed_state.'+ROUND+' == combat.round and steed_state.'+ENDED+' != true',
            'Resume only the existing Steed turn on this actual caster initiative count.'),
        _call(RESTORE, {'actor_state': '{_steed_state_id}'}),
        _set('steed_state.'+CONTINUATION, False)])


def _bind_continuation_feedback(tools):
    """Show the actual resumed budget; optional rows preserve all ordinary turn projections."""
    owner=tools['begin_combat_turn']
    branch=owner['effects'][0]
    if branch.get('condition')!='actor.'+CONTINUATION+' == true':
        raise ValueError('Keep the actual independent Steed continuation branch')
    branch['on_true'].extend([
        _set('_lite_daggers_event_count',0),
        _set('_lite_daggers_applied',0),
        _set('_steed_resumed_name','actor.name'),
        _set('_steed_resumed_movement','combat.movement_remaining'),
        _set('_steed_resumed_action',"'Spent'"),
        _when('combat.action_available == true',[_set('_steed_resumed_action',"'Available'")])])
    summary=owner['metadata']['result_card']['summary']
    summary.extend([
        {'label':'Steed turn resumed','value':{'path':'context._steed_resumed_name'},'optional':True},
        {'label':'Movement remaining (ft)','value':{'path':'context._steed_resumed_movement'},'optional':True},
        {'label':'Action','value':{'path':'context._steed_resumed_action'},'optional':True}])


def _native_body(tool):
    """Move the genuine owner into an internal body while keeping its exact guards and parameters."""
    native = deepcopy(tool)
    actor = next(row for row in native['parameters'] if row['name'] == 'actor')
    actor.pop('from_state', None)
    actor['required'] = True
    native['metadata'] = {'category': 'internal', 'visible_to_player': False, 'costs_action': False}
    return native


def _action_tools(tools):
    """Reuse genuine Action effects for a generated NPC, replacing only player identity admission."""
    native = {}
    for kind, owner in ACTION_OWNERS.items():
        tool = deepcopy(tools[owner])
        actor = next(row for row in tool['parameters'] if row['name'] == 'actor')
        if actor.get('resolve') != 'participant':
            raise ValueError('Keep the reviewed native player Action identity: '+owner)
        actor['resolve'] = 'entity'
        guard = [row for row in tool['pre_conditions']
                 if row['condition'] == "actor.validation_status == 'validated'"]
        if len(guard) != 1:
            raise ValueError('Keep the single native player validation gate: '+owner)
        tool['pre_conditions'].remove(guard[0])
        for row in tool['pre_conditions']:
            row['condition'] = row['condition'].replace('actor.participant_id', '_actor_id')
        def replace_identity(value):
            if isinstance(value, str):
                return value.replace('actor.participant_id', '_actor_id')
            if isinstance(value, list):
                return [replace_identity(item) for item in value]
            if isinstance(value, dict):
                return {key: replace_identity(item) for key, item in value.items()}
            return value
        tool['effects'] = replace_identity(tool['effects'])
        tool['metadata'] = {'category': 'internal', 'visible_to_player': False, 'costs_action': True}
        native['take_srd_otherworldly_native_'+kind] = tool
    action = _tool('Take Dash, Disengage or Dodge with the actual Otherworldly Steed\'s native Action budget. '
        'Enter its controlled phase first while the capable caster rides; an independent Steed uses its own active turn.',
        [_entity('steed_state'), _parameter('action', 'enum', enum_values=list(ACTION_OWNERS))], [
        _call('require_srd_pending_window_closed', {}),
        _call(OWNER, {'steed_state': '{_steed_state_id}', 'caster_state': '{steed_state.created_caster_state_id}'}),
        _call(READ, {'actor_state': '{_steed_state_id}'}, (CONTROLLED,)),
        _require('combat.turn_active == true and combat.active_actor == _steed_state_id and '
            'combat.current_actor == _steed_state_id', 'Only the actual active Steed can take this Action.'),
        _require(CONTROLLED+' != true or ('+STEED+' == _steed_state_id and '+RIDER+
            ' == steed_state.created_caster_state_id)', 'Use the actual controlled Steed phase on its riding caster turn.'),
        *[_when("action == '"+kind+"'", [_call('take_srd_otherworldly_native_'+kind,
            {'actor': '{_steed_state_id}', 'actor_state': '{_steed_state_id}'})]) for kind in ACTION_OWNERS],
        _set('_steed_action_kind', 'action'), _set('_steed_phase_movement', 'combat.movement_remaining'),
        _set('_steed_phase_action', 'combat.action_available')])
    action['metadata'] = {'category': 'combat', 'costs_action': True, 'result_card': {
        'version': 1, 'footprint': 'compact', 'title': 'Steed Action', 'summary': [
            {'label': 'Steed', 'value': {'path': 'context.steed_state.name'}},
            {'label': 'Action', 'value': {'path': 'context._steed_action_kind',
                'labels': {'dash': 'Dash', 'disengage': 'Disengage', 'dodge': 'Dodge'}}},
            {'label': 'Movement available (ft)', 'value': {'path': 'context._steed_phase_movement'}},
            {'label': 'Action remaining', 'value': {'path': 'context._steed_phase_action',
                'labels': {'true': 'Available', 'false': 'Spent'}}}]}}
    return {**native, ACT: action}


def _bind_controlled_order_ending(tools):
    """A compelled Steed ends its own turn and returns the saved rider budget."""
    order = tools['start_srd_control_order_turn']
    ending = next(effect for effect in order['effects']
                  if effect.get('condition') == 'target_state.control_order_ends_turn == true')
    index = next(index for index, effect in enumerate(ending['on_true'])
                 if effect.get('call') == 'end_combat_turn')
    original = ending['on_true'][index]
    ending['on_true'][index] = {
        'condition': STEED+' == _target_state_id and '+RIDER+' != null',
        'on_true': [
            _call(NATIVE_END, {'actor': '{_target_state_id}'}),
            _set('target_state.'+ENDED, True),
            _call(SAVE, {'actor_state': '{_target_state_id}'}),
            _call(RESTORE, {'actor_state': '{'+RIDER+'}'}),
            _set(RIDER, None), _set(STEED, None)],
        'on_false': [original]}


def _forward(native):
    """Forward the original typed parameters and actual private receipts to the public wrapper."""
    params = {row['name']: '{_'+row['name']+'_id}' if row.get('resolve') == 'entity'
              else '{'+row['name']+'}' for row in native['parameters']}
    return {'call': native['name'], 'params': params, 'export_context': '*'}


def bind_steed_turns(runtime, record):
    """Compose after existing turn consumers and widgets, before private-owner reachability pruning."""
    tools = runtime['tools']
    names = (BEGIN, PAUSE, ACT, SAVE, RESTORE, AUTO, FINISH, CONTINUE, NATIVE_BEGIN, NATIVE_END)
    if any(name in tools for name in names):
        raise ValueError('Controlled Steed turns must be composed exactly once')
    tools.update({**_budget_tools(), BEGIN: _begin(), PAUSE: _pause(), AUTO: _initialize(),
                  FINISH: _finish(), CONTINUE: _continue(), **_action_tools(tools)})
    ordinary_begin, ordinary_end = tools['begin_combat_turn'], tools['end_combat_turn']
    begin, end = _native_body(ordinary_begin), _native_body(ordinary_end)
    equation_index = next(index for index, effect in enumerate(begin['effects'])
                         if effect.get('set', {}).get('field') == '_spell_featured_equations')
    equation_projection = deepcopy(begin['effects'][equation_index:])
    for effect in begin['effects']:
        if effect.get('set', {}).get('field') == 'combat.turn_sequence':
            effect['condition'] = STEED+' == null'
        elif effect.get('condition') == 'combat.turn_sequence == null':
            effect['condition'] += ' and '+STEED+' == null'
        elif 'actor.summon_active == true and (actor.summon_command' in effect.get('condition', ''):
            effect['condition'] += " and actor.created_spell_id != 'find_steed'"
    end['parameters'].append(_parameter('controlled_steed_end_rows', 'array', required=False, default=[],
        items={'type': 'object'}, model_hidden=True, max_items=256))
    card_index = next(index for index, effect in enumerate(end['effects'])
                      if effect.get('set', {}).get('field') == '_end_turn_card_title')
    end['effects'].insert(card_index, {'for_each': {'collection': 'controlled_steed_end_rows',
        'as': '_steed_end_row', 'do': [{'list_push': {'list': '_end_turn_rows', 'value': '_steed_end_row'}}]}})
    tools.update({NATIVE_BEGIN: begin, NATIVE_END: end})
    _bind_controlled_order_ending(tools)
    native_begin = {'name': NATIVE_BEGIN, 'parameters': begin['parameters']}
    native_end = {'name': NATIVE_END, 'parameters': end['parameters']}
    end_call = _forward(native_end)
    end_call['params']['controlled_steed_end_rows'] = '{'+END_ROWS+'}'
    continuation = [
        _call(CONTINUE, {'steed_state': '{_actor_id}', 'caster_state': '{actor.created_caster_state_id}'}),
        _set('_turn_effect_rows', []), _set('_turn_effect_count', 0),
        _set('_turn_effect_damage_applied', 0), _set('_spell_featured_equations', [])]
    ordinary_begin['effects'] = [
        {'condition': 'actor.'+CONTINUATION+' == true', 'on_true': continuation,
         'on_false': [_forward(native_begin)]},
        _set(START_ROWS, []),
        _when('actor.created_steed_id != null and '+STEED+' == null', [
            _call(AUTO, {'rider_state': '{_actor_id}', 'steed_state': '{actor.created_steed_id}'}, (START_ROWS,))]),
        {'for_each': {'collection': START_ROWS, 'as': '_steed_start_row', 'do': [
            {'list_push': {'list': '_turn_effect_rows', 'value': '_steed_start_row'}}]}},
        *equation_projection]
    ordinary_end['effects'] = [
        _require(STEED+' == null', 'Pause the actual Steed phase before ending the riding caster\'s turn.'),
        _set(END_ROWS, []),
        _when('actor.created_steed_id != null', [
            _call(FINISH, {'rider_state': '{_actor_id}', 'steed_state': '{actor.created_steed_id}'}, (END_ROWS,))]),
        end_call,
        _when("actor.created_spell_id == 'find_steed' and actor."+BUDGET+' != null', [
            _set('actor.'+ENDED, True), _call(SAVE, {'actor_state': '{_actor_id}'})])]
    # A mid-turn mount initializes the same genuine Steed budget after its paid relocation.
    mount = tools[MOUNT]
    mount['effects'].append(_call(AUTO, {'rider_state': '{_rider_state_id}',
                                      'steed_state': '{_steed_state_id}'}, (START_ROWS,)))
    mount['metadata']['result_card']['details'] = {'path': 'context.'+START_ROWS}
    _bind_continuation_feedback(tools)
    for name in (BEGIN, PAUSE, ACT):
        record['tools'].append({'name': name, 'capability': 'dnd.srd.5.2.1.spells', 'writers': ['dm'],
            'turn_statuses': ['open'], 'state_predicates': [], 'input_schema': _tool_schema(tools[name], tool_name=name)})
    return (BEGIN, PAUSE, ACT)
