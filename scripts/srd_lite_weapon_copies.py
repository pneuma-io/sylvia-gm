"""Stable local identities for carried base-weapon copies; armor remains separate."""
from scripts.srd_lite_weapon_sets import tool,actor_parameter,set_value as st,require as rq,when,call,each

def normalize_removed(prefix):
    return [
        each(prefix+'_removed',prefix+'_step',[
            when(prefix+'_min in '+prefix+'_removed',[st(prefix+'_min',prefix+'_min + 1')])]),
        st(prefix+'_kept_removed',[]),each(prefix+'_removed',prefix+'_removed_id',[
            when(prefix+'_removed_id >= '+prefix+'_min',[{'list_push':{'list':prefix+'_kept_removed','value':prefix+'_removed_id'}}])]),
        st(prefix+'_removed',prefix+'_kept_removed')]

def ensure_copies():
    effects=[rq('weapon in definitions.weapons','Select a supported base weapon.'),st('_copy_quantity',1),
        when('actor_state.inventory_quantities != null and weapon in actor_state.inventory_quantities',[st('_copy_quantity','actor_state.inventory_quantities[weapon]')]),
        when('desired_quantity != null',[st('_copy_quantity','desired_quantity')]),
        rq('_copy_quantity == 0 or weapon in actor_state.inventory','Positive carried copies need an actual inventory entry.'),
        st('_copy_min',1),st('_copy_max',0),st('_copy_removed',[]),
        when('actor_state.inventory_weapon_copies != null and weapon in actor_state.inventory_weapon_copies',[
            st('_copy_ledger','actor_state.inventory_weapon_copies[weapon]'),
            st('_copy_min','_copy_ledger.minimum'),st('_copy_max','_copy_ledger.maximum'),st('_copy_removed','_copy_ledger.removed')]),
        st('_copy_old_quantity','max(0, _copy_max - _copy_min + 1) - len(_copy_removed)'),
        rq('_copy_old_quantity >= 0','Stored copy ownership is inconsistent.'),
        when('_copy_quantity == 0',[st('_copy_min','_copy_max + 1'),st('_copy_removed',[])],[
            when('_copy_quantity < _copy_old_quantity',[
                rq('removed_copies != null and len(removed_copies) == _copy_old_quantity - _copy_quantity','Identify the actual copy serials removed from a partial holding.'),
                each('removed_copies','_copy_lost',[
                    rq('_copy_lost >= _copy_min and _copy_lost <= _copy_max and _copy_lost not in _copy_removed','A removed copy must be actually carried and listed once.'),
                    {'list_push':{'list':'_copy_removed','value':'_copy_lost'}}])]),
            when('_copy_quantity > _copy_old_quantity',[st('_copy_max','_copy_max + _copy_quantity - _copy_old_quantity')]),
            *normalize_removed('_copy')]),
        rq('_copy_max <= 1000000000','The carried-copy identity range is exhausted.'),
        st('_copy_ledger',{'minimum':'_copy_min','maximum':'_copy_max','removed':'_copy_removed'}),
        st('actor_state.inventory_weapon_copies.{weapon}','_copy_ledger')]
    return tool('Synchronize actual quantities with stable local copy serials. Lost serials are never silently reused.',[
        actor_parameter(),{'name':'weapon','type':'string'},
        {'name':'desired_quantity','type':'integer','required':False,'minimum':0,'maximum':10000},
        {'name':'removed_copies','type':'array','required':False,'items':{'type':'integer'},'unique_items':True}],effects)

def select_copy():
    effects=[call('ensure_srd_weapon_copies',{'actor_state':'{_actor_state_id}','weapon':'{weapon}'},{'_selected_copy_ledger':'_copy_ledger'}),
        st('_selected_copy','requested_copy'),
        when('_selected_copy == null',[
            st('_selected_copy','_selected_copy_ledger.minimum'),
            when('_selected_copy == excluded_copy',[st('_selected_copy','_selected_copy + 1')]),
            st('_selected_removed','_selected_copy_ledger.removed'),each('_selected_removed','_selected_step',[
                when('_selected_copy in _selected_removed or _selected_copy == excluded_copy',[st('_selected_copy','_selected_copy + 1')])])]),
        rq('_selected_copy >= _selected_copy_ledger.minimum and _selected_copy <= _selected_copy_ledger.maximum and _selected_copy not in _selected_copy_ledger.removed and _selected_copy != excluded_copy','Choose an actual distinct carried copy.')]
    return tool('Select one actual carried copy, preserving its identity across hand/set changes.',[
        actor_parameter(),{'name':'weapon','type':'string'},
        {'name':'requested_copy','type':'integer','required':False,'minimum':1},
        {'name':'excluded_copy','type':'integer','required':False,'minimum':1}],effects)

def forget_copy():
    effects=[call('select_srd_weapon_copy',{'actor_state':'{_actor_state_id}','weapon':'{weapon}','requested_copy':'{copy_serial}'},{'_lost_copy_ledger':'_selected_copy_ledger'}),
        st('_lost_min','_lost_copy_ledger.minimum'),st('_lost_max','_lost_copy_ledger.maximum'),st('_lost_removed','_lost_copy_ledger.removed'),
        {'list_push':{'list':'_lost_removed','value':'copy_serial'}},*normalize_removed('_lost'),
        st('_lost_ledger',{'minimum':'_lost_min','maximum':'_lost_max','removed':'_lost_removed'}),
        st('actor_state.inventory_weapon_copies.{weapon}','_lost_ledger')]
    return tool('Forget one actually released local copy without renumbering the remaining carried copies.',[
        actor_parameter(),{'name':'weapon','type':'string'},{'name':'copy_serial','type':'integer','minimum':1}],effects)

def build_copy_tools():
    return {'ensure_srd_weapon_copies':ensure_copies(),'select_srd_weapon_copy':select_copy(),'forget_srd_weapon_copy':forget_copy()}
