"""DSL helpers and canonical equipment-tool export.

The authored Lite runtime is the source of truth. The retired bootstrap bodies
are deliberately absent so rebuilding cannot restore profile-only equipment."""
from copy import deepcopy

PUBLIC_TOOLS = {'prepare_srd_weapon_set', 'activate_srd_weapon_set', 'change_srd_character_shield'}

def set_value(field,value):return {'set':{'field':field,'value':value}}
def require(condition,error):return {'require':{'condition':condition,'error':error}}
def when(condition,effects,otherwise=None):
    value={'condition':condition,'on_true':effects}
    if otherwise is not None:value['on_false']=otherwise
    return value
def call(name,parameters=None,exports=None):
    value={'call':name}
    if parameters is not None:value['params']=parameters
    if exports is not None:value['export_context']=exports
    return value
def each(collection,name,effects):return {'for_each':{'collection':collection,'as':name,'do':effects}}
def actor_parameter():return {'name':'actor_state','type':'string','resolve':'entity'}
def tool(description,parameters,effects,*,public=False,title=None):
    metadata={'category':'equipment' if public else 'internal','visible_to_player':public}
    if public:
        metadata['player_controls']={'actor_parameter':'actor_state','confirmation':'none','response':'on_request'}
        metadata['result_card']={'version':1,'footprint':'compact','title':title or 'Equipment',
            'summary':[{'label':'Creature','value':{'path':'context.actor_state.name'}},
                       {'label':'Active set','value':{'path':'context.actor_state.weapon_sets.active_set'}}]}
    return {'description':description,'parameters':parameters,'effects':effects,'metadata':metadata}


def build_tools():
    """Export the current admitted definitions without maintaining a second engine."""
    from pathlib import Path
    import yaml
    from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations
    path=Path(__file__).resolve().parents[1]/'rulesets/srd-lite/runtime/ruleset.yaml'
    runtime=resolve_parameter_declarations(yaml.load(path.read_bytes(),Loader=yaml.CSafeLoader))
    names=PUBLIC_TOOLS | {
        'initialize_srd_weapon_sets','validate_srd_weapon_set','apply_srd_active_weapon_set',
        'derive_srd_equipped_weapon','reconcile_srd_saved_weapon_inventory',
        'release_srd_thrown_prepared_weapon','read_srd_prepared_copy_refs',
        'ensure_srd_weapon_copies','select_srd_weapon_copy','forget_srd_weapon_copy',
    }
    return {name:deepcopy(runtime['tools'][name]) for name in sorted(names)}
