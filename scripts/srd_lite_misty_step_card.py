"""Show the actual arrival and mount status in the existing paid Misty Step widget."""
from .srd_timed_actor_defenses import _call, _entity, _set, _when
from .srd_scene_relations import _tool_scene

OWNER='format_srd_misty_step_cast_receipt'
ROWS='_misty_cast_receipt_rows'


def _formatter():
    """Read the completed caster body without changing mechanics or rolling dice."""
    return _tool_scene('Describe the actual completed Misty Step arrival.',[_entity('caster_state')],[
        _call('srd_information_scene_entity',{'subject':'{_caster_state_id}'},('_info_position',)),
        _set('_misty_mount_status',"'Unmounted'"),
        _when("caster_state.otherworldly_steed_mount != null and caster_state.otherworldly_steed_mount != ''",
            [_set('_misty_mount_status',"'Mounted'")]),
        _set(ROWS,[{'label':"'Position (ft)'",'value':'_info_position'},
            {'label':"'Mount status'",'value':'_misty_mount_status'}]),
        _when("caster_state.scene_fall.status == 'pending'",[
            {'list_push':{'list':ROWS,'value':{'label':"'Landing'",'value':"'Fall awaiting resolution'"}}}])])


def bind_misty_step_card(runtime):
    """Append actual arrival feedback after the existing paid cast and outer settlements."""
    tools=runtime['tools']
    if OWNER in tools:raise ValueError('Misty Step feedback must be composed exactly once')
    tools[OWNER]=_formatter()
    tools['finish_srd_retained_cast_tail']['effects'].append(
        _when("_retained_cast_tail_choices.spell == 'misty_step' and _spell_cast_failed != true and "
            '_pending_spell_operation_lost != true',[
            _call(OWNER,{'caster_state':'{_caster_state_id}'},(ROWS,)),
            {'for_each':{'collection':ROWS,'as':'_misty_cast_receipt_row','do':[
                {'list_push':{'list':'_spell_card_summary','value':'_misty_cast_receipt_row'}}]}}]))
