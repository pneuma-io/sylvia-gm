"""Show the actual unresolved mounted save on the action that opened it."""
from copy import deepcopy
from .srd_timed_actor_defenses import _call,_set,_tool,_when
from .srd_lite_steed_balance import KIND

READ='read_srd_otherworldly_pending_save_feedback'
STATUS='_mount_balance_pending_status'
RIDER='_mount_balance_pending_rider_name'
STEED='_mount_balance_pending_steed_name'
OWNERS=('resolve_srd_fall','begin_combat_turn','move_srd_scene_creature',
    'complete_srd_pending_action','cast_spell','cast_spell_outside_combat')


def _reader():
    """Read only the saved innermost choice and the existing creature-name owner."""
    return _tool('Describe the actual unresolved mounted Dexterity save without changing game state.',[],[
        _set(STATUS,None),_set(RIDER,None),_set(STEED,None),
        _when('state.pending_srd_action_stack != null and len(state.pending_srd_action_stack) > 0',[
            _set('_mount_balance_feedback_event',
                'state.pending_srd_actions[state.pending_srd_action_stack[-1]]'),
            _when("_mount_balance_feedback_event.kind == '"+KIND+"' and "
                "_mount_balance_feedback_event.status == 'open' and _mount_balance_feedback_event.stage == 'balance'",[
                _set(STATUS,"'DC 10 Dexterity save pending'"),
                _call('read_srd_summon_initiative_label',
                    {'summon_state':'{_mount_balance_feedback_event.parameters.rider}'},('_initiative_summon_name',)),
                _set(RIDER,'_initiative_summon_name'),
                _call('read_srd_summon_initiative_label',
                    {'summon_state':'{_mount_balance_feedback_event.parameters.steed}'},('_initiative_summon_name',)),
                _set(STEED,'_initiative_summon_name')])])])


def bind_mount_pending_cards(runtime):
    """Append a read-only indication to the original static or computed card summary."""
    tools=runtime['tools']
    if READ in tools:
        raise ValueError('Mounted save feedback must be composed exactly once')
    tools[READ]=_reader()
    rows=[{'label':label,'value':{'path':'context.'+variable},'optional':True}
        for label,variable in (('Mounted save',STATUS),('Mounted rider',RIDER),('Mount',STEED))]
    for name in OWNERS:
        owner=tools[name]
        summary=owner['metadata']['result_card']['summary']
        owner['effects'].append(_call(READ,{},(STATUS,RIDER,STEED)))
        if isinstance(summary,list):
            summary.extend(deepcopy(rows))
        elif isinstance(summary,dict) and set(summary)=={'path'}:
            path=summary['path']
            if not path.startswith('context._') or '.' in path[len('context.'):]:
                raise ValueError('Keep the original local computed card summary: '+name)
            variable=path[len('context.'):]
            owner['effects'].append(_when(STATUS+' != null',[
                {'list_push':{'list':variable,'value':{'label':"'"+row['label']+"'",
                    'value':row['value']['path'][len('context.'):]}}} for row in rows]))
        else:
            raise ValueError('Keep the original supported card summary: '+name)
    return [READ,*OWNERS]
