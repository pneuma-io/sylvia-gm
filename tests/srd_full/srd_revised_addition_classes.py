"""Complete original SRD acquisition fixtures for the ten delegated additions.

All earned sources and complete quotas use the immutable participant artifact.
Class levels and resource maxima are declared fixture facts, not progression
produced by the engine. Candidate subset shortages remain visible in evidence.
Integration target: tests/srd_revised_addition_classes.py.
"""
from copy import deepcopy
from functools import lru_cache
import json
import re
import zipfile

from sylvia_gm.application.initial_character_state import materialize_initial_character_state, verify_initial_character_state
from sylvia_gm.application.schema_admission import validate_parameters
from sylvia_gm.domain.authority import thaw_json_value
from tests.srd_full.srd_revised_native_fixture import ARCHIVE, ROSTER, complete_wizard, package_inputs


PAGES = {'bard':31, 'cleric':36, 'druid':41, 'paladin':53, 'ranger':58,
    'sorcerer':65, 'warlock':71, 'wizard':77}
SUBCLASSES = {'bard':'college_of_lore', 'cleric':'life_domain', 'druid':'circle_of_land',
    'paladin':'oath_of_devotion', 'ranger':'hunter', 'sorcerer':'draconic_sorcery',
    'warlock':'fiend_patron', 'wizard':'evoker'}
SAVES = {'bard':['dexterity','charisma'], 'cleric':['wisdom','charisma'],
    'druid':['intelligence','wisdom'], 'paladin':['wisdom','charisma'],
    'ranger':['strength','dexterity'], 'sorcerer':['constitution','charisma'],
    'warlock':['wisdom','charisma']}
CLASS_SKILLS = {'bard':['arcana','history','persuasion'], 'cleric':['insight','religion'],
    'druid':['nature','perception'], 'paladin':['athletics','persuasion'],
    'ranger':['animal_handling','nature','survival'], 'sorcerer':['arcana','persuasion'],
    'warlock':['arcana','intimidation']}


@lru_cache(maxsize=1)
def source_inputs():
    """Reuse actual installed-input contracts without mutating their dictionaries."""
    record, artifact, headers = package_inputs()
    definitions = artifact['initial_state_validation']['definitions']
    return record, artifact, headers, definitions['spell_grants'], definitions['spell_casting']


@lru_cache(maxsize=1)
def reference_pages():
    with zipfile.ZipFile(ARCHIVE) as archive:
        corpus = json.loads(archive.read('packages/wotc.srd.5.2.1.reference/content/corpus/classes.json'))
    return {row['page']:row['text'].replace('\u2014','-') for row in corpus['page_records']}


def reference_slot_row(class_id, level):
    """Read numeric original class-table cells for explicitly authored budgets."""
    if class_id not in PAGES or not 1 <= level <= 20:
        raise ValueError('Use an original class and class level1 through20.')
    text = reference_pages()[PAGES[class_id]]
    text = text[text.index('Features\n'):]
    matches = list(re.finditer(r'(?m)^(\d{1,2}) \+[2-6] ', text))
    if [int(m[1]) for m in matches] != list(range(1,21)):
        raise ValueError('Incomplete actual SRD class table: '+class_id)
    index = level-1
    row = text[matches[index].end():matches[index+1].start() if index<19 else len(text)].strip()
    tail = re.search(r'(?:\d+|-)(?:\s+(?:\d+|-))+$', row)
    if tail is None:
        raise ValueError('Missing numeric original SRD class row.')
    numbers = [0 if token=='-' else int(token) for token in tail[0].split()]
    if class_id == 'warlock':
        return {'pact_slot_count':numbers[-2], 'pact_slot_rank':numbers[-1]}
    width = 5 if class_id in ('paladin','ranger') else 9
    return {'ordinary_slot_counts':numbers[-width:]}


def complete_addition_class(class_id, spell, level=9, extra_spells=(), *, land='temperate'):
    """Materialize all actual earned grants; never inject a sparse spell source."""
    if class_id == 'wizard':
        return complete_wizard(spell, level, extra_spells)
    if class_id not in SAVES or not 1 <= level <= 20:
        raise ValueError('Unsupported original class or class level.')
    _, artifact, _, policies, choices = source_inputs()
    launch = set(json.loads(ROSTER.read_text(encoding='utf-8'))['spell_ids'])
    wanted = list(dict.fromkeys([spell, *extra_spells]))
    if any(identity not in choices for identity in wanted):
        raise ValueError('Requested spell is absent from the actual original catalogue.')
    subclass = SUBCLASSES[class_id] if level >= 3 else ''
    sources = [source for source in policies[class_id]['automatic_sources']
        if policies[source]['minimum'] <= level
        and (not policies[source]['subclass'] or policies[source]['subclass'] == subclass)]
    if class_id == 'druid' and level >= 3:
        source = 'land_'+land
        if source not in ('land_arid','land_polar','land_temperate','land_tropical'):
            raise ValueError('Use an actual Circle of Land terrain source.')
        sources.append(source)
    fixed_earned = {identity for source in sources for identity, minimum in policies[source]['fixed'].items()
        if minimum <= level}

    def pool(source):
        p = policies[source]
        allowed = ['bard','cleric','druid','wizard'] if source == 'bard' and level >= 10 else p['lists']
        return sorted((identity for identity, h in choices.items() if
            any(name in h['classes'] for name in (p['lists'] if h['level'] == 0 else allowed))
            and p['min_spell_level'] <= h['level'] <= p['max_level'][level-1]),
            key=lambda identity:(identity not in launch, choices[identity]['level'], identity))

    def select(eligible, count, requested):
        selected = [identity for identity in requested if identity in eligible]
        if len(selected) > count:
            raise ValueError('Requested spells exceed an actual complete source quota.')
        selected += [identity for identity in eligible if identity not in selected][:count-len(selected)]
        if len(selected) != count:
            raise ValueError('Insufficient choices for an actual complete original source.')
        return selected

    grants = []
    covered = set(fixed_earned)
    for source in sources:
        p = policies[source]
        if p['fixed']:
            selected = sorted(identity for identity, minimum in p['fixed'].items() if minimum <= level)
        else:
            eligible = pool(source)
            requested = [identity for identity in wanted if identity not in covered]
            if source == 'magical_discoveries':
                # The real validator requires two total choices, not two of each rank group.
                selected = select(eligible, 2, requested)
            else:
                selected = select([identity for identity in eligible if choices[identity]['level']==0],
                    p['complete_cantrips'][level-1], requested)
                selected += select([identity for identity in eligible if choices[identity]['level']>0],
                    p['complete_spells'][level-1], requested)
        grants.append({'source':source, 'ability':p['ability'], 'spells':selected})
        covered.update(selected)
    if any(identity not in covered for identity in wanted):
        raise ValueError('Requested spell is not earned by this class and level.')
    union = list(dict.fromkeys(identity for grant in grants for identity in grant['spells']))
    skills = list(CLASS_SKILLS[class_id]) + ['sleight_of_hand','stealth']
    # Human's skill plus the three Skilled choices use distinct legal skill names.
    fillers = ['history','perception','investigation','medicine','insight','religion','arcana','athletics']
    skills += [identity for identity in fillers if identity not in skills][:4]
    data = {'name':'Complete SRD '+class_id+' acquisition', 'level':level, 'species':'human',
        'background':'criminal', 'classes':[{'id':class_id,'level':level}],
        'abilities':{'strength':8,'dexterity':15,'constitution':14,'intelligence':12,'wisdom':12,'charisma':12},
        'speed':30, 'size_rank':1, 'origin_feats':['alert','skilled'],
        'saving_throw_proficiencies':SAVES[class_id], 'skill_proficiencies':skills,
        'tool_proficiencies':['thieves_tools'], 'weapon_proficiencies':['simple_weapons'],
        'spellcasting_grants':grants, 'subclasses':({class_id:subclass} if subclass else {})}
    data['abilities'][policies[class_id]['ability']] = 17
    if class_id == 'cleric':
        data['cleric_order'] = 'protector'
    if class_id == 'druid':
        data['druid_order'] = 'warden'
        data['tool_proficiencies'].append('herbalism_kit')
    if class_id == 'bard':
        data['tool_proficiencies'] += ['musical_instrument_flute','musical_instrument_horn','musical_instrument_lute']
    if class_id in ('paladin','ranger') and level >= 2:
        data['fighting_styles'] = [{'source':class_id,'style':'defense'}]
    table = reference_slot_row(class_id, level)
    resources = {'hit_die':level}
    if class_id == 'warlock':
        resources['spell_slot_'+str(table['pact_slot_rank'])] = table['pact_slot_count']
    else:
        resources.update({'spell_slot_'+str(rank):count
            for rank, count in enumerate(table['ordinary_slot_counts'],1) if count})
    die = 10 if class_id in ('paladin','ranger') else 6 if class_id == 'sorcerer' else 8
    state = {'max_hp':die+2+(level-1)*(die//2+3), 'hit_die_size':die,
        'inventory':['staff','spear','dagger'], 'available_bonus_actions':[],
        'conditions':[], 'damage_resistances':[], 'damage_immunities':[], 'damage_vulnerabilities':[],
        'prepared_spells':union, 'resources':resources, 'resource_maxima':deepcopy(resources),
        'worn_armor':None, 'worn_shield':None}
    validate_parameters(data, artifact['schema'])
    kwargs = {key:artifact[key] for key in ('entity_state_schema','initial_state_calculations','initial_state_validation')}
    completed = materialize_initial_character_state(data, state, **kwargs)
    if verify_initial_character_state(data, completed, **kwargs) != completed:
        raise AssertionError('Actual saved materializer verification did not retain completed state.')
    evidence = {'class':class_id, 'level':level, 'complete_source_count':len(grants),
        'source_counts':{grant['source']:len(grant['spells']) for grant in grants},
        'requested_spell_sources':{identity:[grant['source'] for grant in grants if identity in grant['spells']]
            for identity in wanted}, 'outside_launch_prepared':sorted(set(union)-launch),
        'materializer':'materialize_initial_character_state', 'verified_saved_state':True,
        'resource_table_row':table,
        'slot_counts':'DM authored original class-table facts; not derived or Pact-separated by materializer',
        'scope':'original full SRD complete acquisition; no selected65 or inferred112 admission claim'}
    return data, thaw_json_value(completed), evidence


def druid_factory(spell, level, extra_spells):
    return complete_addition_class('druid', spell, level, extra_spells)


def cleric_factory(spell, level, extra_spells):
    return complete_addition_class('cleric', spell, level, extra_spells)


def sorcerer_factory(spell, level, extra_spells):
    return complete_addition_class('sorcerer', spell, level, extra_spells)


def warlock_factory(spell, level, extra_spells):
    return complete_addition_class('warlock', spell, level, extra_spells)


def bard_factory(spell, level, extra_spells):
    return complete_addition_class('bard', spell, level, extra_spells)
