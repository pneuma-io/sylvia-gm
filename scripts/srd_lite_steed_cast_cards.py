"""Show the actual created Steed and released rider in the existing paid-cast widget."""
from .srd_timed_actor_defenses import _call, _entity, _require, _set, _when
from .srd_scene_relations import _tool_scene

OWNER = 'format_srd_otherworldly_steed_cast_receipt'
ROWS = '_steed_cast_receipt_rows'


def _formatter():
    """Read the committed source actor and scene position without changing game state."""
    return _tool_scene('Describe the actual Otherworldly Steed created by this paid cast.',
        [_entity('steed_state'),_entity('caster_state')],[
        _require("steed_state.created_spell_id == 'find_steed' and steed_state.created_active == true and "
            'steed_state.created_caster_state_id == _caster_state_id and '
            'caster_state.created_steed_id == _steed_state_id',
            'Describe only this caster\'s actual current Steed.'),
        _call('srd_information_scene_entity',{'subject':'{_steed_state_id}'},('_info_position',)),
        _set('_steed_cast_receipt_name','_steed_state_id'),
        _when('steed_state.name != null',[_set('_steed_cast_receipt_name','steed_state.name')]),
        _set(ROWS,[
            {'label':"'Steed'",'value':'_steed_cast_receipt_name'},
            {'label':"'Creature type'",'value':'steed_state.creature_type'},
            {'label':"'Hit points'",'value':'steed_state.hp'},
            {'label':"'Steed position'",'value':'_info_position'}]),
        _when("caster_state.scene_fall.status == 'pending'",[
            {'list_push':{'list':ROWS,'value':{
                'label':"'Rider'",'value':"'Unmounted; fall awaiting resolution'"}}}])])


def bind_steed_cast_cards(runtime):
    """Append source feedback after existing creation and rider-fall settlement have completed."""
    tools = runtime['tools']
    if OWNER in tools:
        raise ValueError('Steed cast feedback must be composed exactly once')
    tools[OWNER] = _formatter()
    tools['finish_srd_retained_cast_tail']['effects'].append(
        _when("_retained_cast_tail_choices.spell == 'find_steed' and _spell_cast_failed != true and "
            '_pending_spell_operation_lost != true',[
            _call(OWNER,{'steed_state':'{caster_state.created_steed_id}',
                'caster_state':'{_caster_state_id}'},(ROWS,)),
            {'for_each':{'collection':ROWS,'as':'_steed_cast_receipt_row','do':[
                {'list_push':{'list':'_spell_card_summary','value':'_steed_cast_receipt_row'}}]}}]))
    return [OWNER,'finish_srd_retained_cast_tail']
