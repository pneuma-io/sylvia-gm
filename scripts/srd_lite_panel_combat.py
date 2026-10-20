"""Declare direct combat intent through the existing Lite authoritative operations."""

from copy import deepcopy
import json

COMMON_ATTACK = {
    "attack_sequence": "primary", "use_graze_on_miss": False,
    "mounted": False, "circumstantial_modifier": 0,
    "advantage": False, "disadvantage": False,
    "actor_can_see_target": False, "target_can_see_actor": False,
    "target_is_charmer": False, "fear_source_in_sight": False, "attack_distance": 0,
}

def bind_combat_controls(runtime, record):
    """Controls submit ordinary intent; the existing engine derives geometry and spends resources."""
    surface = next(row for row in record["ui_surfaces"] if row["renderer"] == "coordinate_map")
    if not any(row['id'] == 'dnd-character' for row in record['ui_surfaces']):
        character = deepcopy(next(row for row in record['ui_surfaces'] if row['renderer'] == 'party'))
        character.update(id='dnd-character', title='Character', renderer='unit_list', order=15,
                         default_size=240, minimum_size=140)
        character['actions'] = []
        record['ui_surfaces'].append(character)
    actions = surface["actions"]
    def action(identity, label, intent, params, bindings=None):
        row={"id":identity,"label":label,"intent":intent,"params":params}
        if bindings:
            row["bindings"]=bindings
            if "actor" in bindings.values(): row["actor_reference"]="participant_ref"
            if "target" in bindings.values(): row["target_reference"]="participant_ref"
        actions.append(row)
    for mode, ability, label in (("melee","strength","Melee (Str)"),("melee","dexterity","Melee (Dex)"),
                                  ("ranged","dexterity","Ranged"),("thrown","strength","Throw (Str)"),
                                  ("thrown","dexterity","Throw (Dex)")):
        action("attack-"+mode+"-"+ability,label,"resolve_combat_attack",
               {**COMMON_ATTACK,"attack_mode":mode,"attack_ability":ability},
               {"actor":"actor","target":"target"})
    for spell,label,slot in (("fire_bolt","Fire Bolt","cantrip"),("ray_of_frost","Ray of Frost","cantrip"),
                              ("magic_missile","Magic Missile (1)","spell_slot_1")):
        action("cast-"+spell,label,"cast_spell",{
            "spell":spell,"slot_resource":slot,"verbal_component_available":True,
            "somatic_component_available":True,"material_component_available":True,
        },{"caster":"actor","caster_state":"actor","target":"target"})
    action("dash","Dash","dash_combat_action",{}, {"actor":"actor","actor_state":"actor"})
    action("end-turn","End combat turn","end_combat_turn",{})
    for row in actions:
        sources=set(row.get('bindings',{}).values())
        if 'actor' in sources and sources.intersection({'target','aim_point'}): row['spatial_preview']='segment'
    assert len(actions)<=16
    for name,actor in (("resolve_combat_attack","actor"),("cast_spell","caster_state"),
                       ("dash_combat_action","actor"),("end_combat_turn",None)):
        controls={"confirmation":"required","response":"on_request"}
        if actor: controls["actor_parameter"]=actor
        checks=[]
        declaration=next(row for row in record['tools'] if row['name']==name)
        for predicate in declaration.get('state_predicates',[]):
            value=predicate['equals']
            literal=json.dumps(value)
            operator='!=' if predicate.get('negated') else '=='
            condition=('len('+predicate['key']+') '+operator+' 0' if value == [] else predicate['key']+' '+operator+' '+literal)
            checks.append({'condition':condition,
                           'reason':'This action is unavailable in the current combat state.'})
        if name=='cast_spell':
            checks += [
                {'condition':"combat.turn_active == true and combat.active_actor == _caster_state_id and combat.action_available == true",
                 "reason":"Casting needs this creature's available combat action."},
                {'condition':"caster_state.prepared_spells != null and spell in caster_state.prepared_spells",
                 "reason":"This spell is not in the creature's prepared repertoire."},
                {'condition':"slot_resource == 'cantrip' or (caster_state.resources != null and caster_state.resources[slot_resource] != null and caster_state.resources[slot_resource] > 0 and caster_state.spell_slot_cast_this_turn != true)",
                 'reason':'The selected spell slot is unavailable this turn.'},
            ]
        if checks: controls['checks']=checks
        runtime["tools"][name].setdefault("metadata",{})["player_controls"]=controls
    record["version"]="0.3.4"


def bind_queue_response_controls(runtime, record):
    """Ending a character turn commits its intentions and requests one GM response."""
    controls = runtime["tools"]["end_combat_turn"]["metadata"]["player_controls"]
    assert controls["response"] in {"on_request", "automatic"}
    controls["response"] = "automatic"
    record["version"] = "0.3.5"


def bind_gm_encounter_boundary(runtime, record):
    """Keep ending an encounter with the GM while preserving its existing engine operation."""
    assert "end_combat_encounter" in runtime["tools"]
    for surface in record["ui_surfaces"]:
        surface["actions"] = [action for action in surface["actions"]
                              if action["intent"] != "end_combat_encounter"]
    record["version"] = "0.3.15"
