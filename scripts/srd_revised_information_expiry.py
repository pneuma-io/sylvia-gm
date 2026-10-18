"""End only the current Arcane Eye concentration at its saved source expiry."""
from copy import deepcopy
from .srd_control_behaviors import _entity,_lasting
from .srd_timed_actor_defenses import _call,_parameter,_set,_tool,_when
from .srd_timed_senses import _loop

HELPER='end_revised_srd_arcane_eye_expiry'


def expiry_owner():
    return _tool('End the matching current Arcane Eye concentration at its actual saved deadline.',[
        _entity('caster_state'),_parameter('instance_id'),
        _parameter('started_tick','number'),_parameter('expires_tick','number'),_lasting()], [
        _set('_eye_expiry_matches',False),
        _set('_eye_expiry_records',[]),
        _when('lasting_state.turn_effects != null',[
            _set('_eye_expiry_records','lasting_state.turn_effects'),
        ]),
        _loop('_eye_expiry_records','_eye_expiry_record',[
            _when("_eye_expiry_record.kind == 'information_spell' and "
                "_eye_expiry_record.spell == 'arcane_eye' and "
                '_eye_expiry_record.caster == _caster_state_id and '
                '_eye_expiry_record.target == _caster_state_id and '
                '_eye_expiry_record.concentration == true and '
                '_eye_expiry_record.started_tick == started_tick and '
                '_eye_expiry_record.expires_tick == expires_tick',[
                _call('read_srd_information_instance',{'record':'{_eye_expiry_record}'},('_info_instance_id',)),
                _when("instance_id != '' and _info_instance_id == instance_id",[
                    _set('_eye_expiry_matches',True),
                ]),
            ]),
        ]),
        _when('_eye_expiry_matches == true and lasting_state.lasting_tick >= expires_tick and '
            "caster_state.concentration_spell_id == 'arcane_eye' and "
            "caster_state.concentration_effect_family == 'information_spell' and "
            'caster_state.concentration_target_state_id == _caster_state_id and '
            'caster_state.concentration_started_tick == started_tick and '
            'caster_state.concentration_expires_tick == expires_tick',[
            _call('end_srd_concentration_node',{'source_state':'{_caster_state_id}',
                'target_state':'{_caster_state_id}','lasting_state':'{_lasting_state_id}'}),
        ]),
    ])


def expiry_hook():
    return _when("_refresh_info_record.spell == 'arcane_eye' and "
        '_refresh_info_record.concentration == true and '
        'lasting_state.lasting_tick >= _refresh_info_record.expires_tick',[
        _call(HELPER,{'caster_state':'{_refresh_info_record.caster}',
            'instance_id':'{_info_instance_id}','started_tick':'{_refresh_info_record.started_tick}',
            'expires_tick':'{_refresh_info_record.expires_tick}','lasting_state':'{_lasting_state_id}'}),
    ])


def bind_revised_srd_information_expiry(runtime):
    """Add one hook before the retained exact removal; do not replace callbacks."""
    tools=runtime['tools']
    for owner in ('refresh_srd_information_spells','remove_srd_information_effects',
        'read_srd_information_instance','end_srd_concentration_node'):
        assert owner in tools,'Retain the original information/concentration owner: '+owner
    expected=expiry_owner()
    if HELPER in tools:
        assert tools[HELPER]==expected,'An incompatible Arcane Eye expiry owner is already installed.'
    else:tools[HELPER]=expected
    refresh=deepcopy(tools['refresh_srd_information_spells'])
    count=0
    def bind(effects):
        nonlocal count
        result=[]
        for effect in effects:
            node=deepcopy(effect)
            for branch in ('on_true','on_false'):
                if branch in node:node[branch]=bind(node[branch])
            if 'for_each' in node:node['for_each']['do']=bind(node['for_each']['do'])
            if node.get('call')=='remove_srd_information_effects' and node.get('params',{}).get('instance_id')=='{_info_instance_id}':
                assert node['params']['caster_state']=='{_refresh_info_record.caster}'
                count+=1
                if not result or result[-1]!=expiry_hook():result.append(expiry_hook())
            result.append(node)
        return result
    refresh['effects']=bind(refresh['effects'])
    assert count==1,'Use the actual single expired-record removal seam.'
    tools['refresh_srd_information_spells']=refresh
    return (HELPER,'refresh_srd_information_spells')
