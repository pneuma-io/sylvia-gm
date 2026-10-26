"""Complete original SRD spell-source acquisition; selected65 shortages stay explicit.

The actual original artifact validates every saved source, then the actual initial
materializer and verification run. No sparse grant or post-creation injection.
"""
from copy import deepcopy
import json
import re
import zipfile

from sylvia_gm.application.initial_character_state import materialize_initial_character_state, verify_initial_character_state
from sylvia_gm.application.schema_admission import validate_parameters
from sylvia_gm.domain.authority import thaw_json_value
from tests.srd_full.srd_revised_native_fixture import package_inputs, complete_wizard, ARCHIVE, ROSTER

def reference_slot_row(class_id, level):
    """Read only factual cells; this is fixture setup, not an engine pool owner."""
    pages = {'bard':31,'druid':41,'warlock':71}
    if not 1 <= level <= 20:
        raise ValueError('Use an original supported class level from 1 through 20.')
    with zipfile.ZipFile(ARCHIVE) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        reference_id = next(r['id'] for r in manifest['packages'] if r['id']=='wotc.srd.5.2.1.reference')
        corpus = json.loads(archive.read('packages/'+reference_id+'/content/corpus/classes.json'))
    text = next(r['text'] for r in corpus['page_records'] if r['page']==pages[class_id]).replace('\u2014','-')
    text = text[text.index('Features\n'):]
    matches = list(re.finditer(r'(?m)^(\d{1,2}) \+[2-6] ', text))
    assert [int(m[1]) for m in matches] == list(range(1,21))
    index = level-1
    row = text[matches[index].end():matches[index+1].start() if index<19 else len(text)].strip()
    tail = re.search(r'(?:\d+|-)(?:\s+(?:\d+|-))+$', row)
    assert tail is not None
    numbers = [0 if token=='-' else int(token) for token in tail[0].split()]
    if class_id=='warlock':
        return {'pact_slot_count':numbers[-2],'pact_slot_rank':numbers[-1]}
    return {'ordinary_slot_counts':numbers[-9:]}


def complete_foresight_class(class_id, spell='foresight', level=17, extra_spells=()):
    if class_id == 'wizard':
        return complete_wizard(spell, level, extra_spells)
    if class_id not in ('bard', 'druid', 'warlock'):
        raise ValueError('Foresight source is not supported by this original class.')
    record, artifact, headers = package_inputs()
    definitions = artifact['initial_state_validation']['definitions']
    policies, choices = definitions['spell_grants'], definitions['spell_casting']
    launch = set(json.loads(ROSTER.read_text())['spell_ids'])
    policy = policies[class_id]
    subclasses = {'bard': 'college_of_lore', 'druid': 'circle_of_land', 'warlock': 'fiend_patron'}
    ability = policy['ability']
    grants = []
    required = list(dict.fromkeys([spell, *extra_spells]))

    def selectable(source):
        p = policies[source]
        allowed = ['bard','cleric','druid','wizard'] if source == 'bard' and level >= 10 else p['lists']
        pool = [identity for identity, h in choices.items() if
            any(name in h['classes'] for name in (p['lists'] if h['level']==0 else allowed))
            and p['min_spell_level'] <= h['level'] <= p['max_level'][level-1]]
        return sorted(pool, key=lambda s:(s not in launch, choices[s]['level'], s))

    def select(pool, count, wanted=()):
        chosen = [s for s in wanted if s in pool]
        if len(chosen) > count:
            raise ValueError('Requested spells exceed the actual source quota.')
        chosen += [s for s in pool if s not in chosen][:count-len(chosen)]
        if len(chosen) != count:
            raise ValueError('Insufficient complete original spell choices.')
        return chosen

    def add(source):
        p = policies[source]
        if p['minimum'] > level:
            return
        fixed = sorted(s for s, minimum in p['fixed'].items() if minimum <= level)
        if fixed:
            selected = fixed
        elif source == 'magical_discoveries':
            # The original special source permits two spells in total, not2+2.
            selected = select([s for s in selectable(source) if choices[s]['level'] > 0], 2)
        else:
            pool = selectable(source)
            selected = select([s for s in pool if choices[s]['level']==0], p['complete_cantrips'][level-1], required)
            selected += select([s for s in pool if choices[s]['level']>0], p['complete_spells'][level-1], required)
        grants.append({'source': source, 'ability': p['ability'], 'spells': selected})

    for source in policy['automatic_sources']:
        p = policies[source]
        if p['subclass'] and p['subclass'] != subclasses[class_id]:
            continue
        add(source)
    if class_id == 'druid':
        add('land_arid')
    union = list(dict.fromkeys(s for grant in grants for s in grant['spells']))
    if spell not in union or any(s not in union for s in extra_spells):
        raise ValueError('Requested spell is not earned by this actual class level.')
    data = {'name': 'Complete SRD Foresight '+class_id, 'level': level, 'species': 'human',
        'background': 'criminal', 'classes': [{'id': class_id, 'level': level}],
        'abilities': {'strength':8,'dexterity':15,'constitution':14,'intelligence':12,'wisdom':12,'charisma':12},
        'speed':30,'size_rank':1,'origin_feats':['alert','skilled'],
        'saving_throw_proficiencies': {'bard':['dexterity','charisma'],'druid':['intelligence','wisdom'],'warlock':['wisdom','charisma']}[class_id],
        'skill_proficiencies': {'bard':['arcana','history','persuasion','sleight_of_hand','stealth','perception','investigation','insight','medicine'],
            'druid':['nature','perception','history','sleight_of_hand','stealth','arcana','investigation','medicine'],
            'warlock':['arcana','intimidation','history','sleight_of_hand','stealth','investigation','insight','medicine']}[class_id],
        'tool_proficiencies': ['thieves_tools'], 'weapon_proficiencies':['simple_weapons'],
        'spellcasting_grants':grants,'subclasses':{class_id:subclasses[class_id]}}
    data['abilities'][ability] = 17
    if class_id == 'druid':
        data['druid_order'] = 'warden'
        data['tool_proficiencies'].append('herbalism_kit')
    if class_id == 'bard':
        data['tool_proficiencies'] += ['musical_instrument_flute','musical_instrument_horn','musical_instrument_lute']
    # DM-authored factual budgets, not an implemented progression owner.
    reference = reference_slot_row(class_id, level)
    if class_id == 'warlock':
        pact = reference
        resources = {'hit_die':level, f"spell_slot_{pact['pact_slot_rank']}":pact['pact_slot_count']}
    else:
        slots = reference['ordinary_slot_counts']
        resources = {'hit_die':level, **{f'spell_slot_{i}':n for i,n in enumerate(slots,1) if n}}
    state = {'max_hp':8+2+(level-1)*7, 'hit_die_size':8,
        'inventory':['staff','spear','dagger'], 'available_bonus_actions':[],
        'conditions':[], 'damage_resistances':[], 'damage_immunities':[], 'damage_vulnerabilities':[],
        'prepared_spells':union, 'resources':resources,'resource_maxima':deepcopy(resources),
        'worn_armor':None,'worn_shield':None}
    validate_parameters(data, artifact['schema'])
    kwargs = {k:artifact[k] for k in ('entity_state_schema','initial_state_calculations','initial_state_validation')}
    completed = materialize_initial_character_state(data,state,**kwargs)
    assert verify_initial_character_state(data,completed,**kwargs) == completed
    evidence = {'class':class_id,'level':level,'complete_source_count':len(grants),
        'source_counts': {g['source']:len(g['spells']) for g in grants},
        'foresight_source':'mystic_arcanum_9' if class_id=='warlock' else class_id,
        'outside_launch_prepared':sorted(set(union)-launch),
        'materializer':'materialize_initial_character_state','verified_saved_state':True,
        'slot_counts':'DM authored from actual SRD table; not derived or Pact-separated by materializer',
        'scope':'original_full_srd_only_not_selected65_acquisition'}
    return data, thaw_json_value(completed), evidence


def bard_factory(spell, level, extra_spells):
    return complete_foresight_class('bard', spell, level, extra_spells)


def druid_factory(spell, level, extra_spells):
    return complete_foresight_class('druid', spell, level, extra_spells)


def warlock_factory(spell, level, extra_spells):
    return complete_foresight_class('warlock', spell, level, extra_spells)
