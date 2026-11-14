"""Complete original SRD class materialization followed by genuine public actions.

Filler choices are from the approved 65 where possible. Source-legal book choices
outside that list remain explicit full-SRD controls, never launch admission claims.
"""
import io, json, os, zipfile, hashlib, shutil
from pathlib import Path
from copy import deepcopy
from functools import lru_cache
import pytest
from sylvia_gm.application.initial_character_state import materialize_initial_character_state, verify_initial_character_state
from sylvia_gm.application.schema_admission import validate_parameters
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import ActionStatus, Participant, ParticipantValidationStatus, StateValue, WriterRole, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected
from sylvia_gm.spatial import admit_spatial_scene
SRD = "wotc.srd.5.2.1"
ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = Path(os.environ.get("SRD_REVISED_CANDIDATE", str(ROOT / "tests/fixtures/srd_full/baseline.sylvia-gm-package")))
ROSTER = Path(os.environ.get("SRD_REVISED_ROSTER", str(ROOT / "tests/fixtures/srd_full/revised-launch-65.json")))

@lru_cache
def package_inputs():
    with zipfile.ZipFile(ARCHIVE) as z:
        record = next(r for r in json.loads(z.read('manifest.json'))['packages'] if r['id']==SRD)
        inventory = json.loads(z.read('packages/'+SRD+'/content/spell-status-inventory.json'))
    artifact = next(a for a in record['participant_artifacts'] if a['kind']=='player_character')
    headers = {r['id']:r['casting_requirements'] for r in inventory['spells']}
    return record,artifact,headers

def complete_wizard(spell, level=5, extra_spells=()):
    record,artifact,headers = package_inputs()
    tables=artifact['initial_state_validation']['definitions']
    policy=tables['spell_grants']['wizard']; choices=tables['spell_casting']
    maximum=policy['max_level'][level-1]
    launch=set(json.loads(ROSTER.read_text())['spell_ids'])
    eligible=[s for s,h in choices.items() if 'wizard' in h['classes'] and h['level']<=maximum]
    eligible.sort(key=lambda s:(s not in launch, choices[s]['level'],s))
    cantrips=[s for s in eligible if choices[s]['level']==0]
    levelled=[s for s in eligible if choices[s]['level']>0]
    required=list(dict.fromkeys([spell,*extra_spells]))
    for s in required:
        if s not in eligible: raise ValueError('Not a legal Wizard choice: '+s)
    def select(pool,count,wanted):
        chosen=[s for s in wanted if s in pool]
        chosen += [s for s in pool if s not in chosen][:count-len(chosen)]
        if len(chosen)!=count: raise ValueError('Insufficient complete class choices')
        return chosen
    prepared=select(cantrips,policy['complete_cantrips'][level-1],required)+select(levelled,policy['complete_spells'][level-1],required)
    book_count=6+2*(level-1)+(2 if level>=3 else 0)
    book=select(levelled,book_count,[s for s in prepared if choices[s]['level']>0])
    data={'name':'SRD Utility Wizard','level':level,'species':'human','background':'criminal',
          'classes':[{'id':'wizard','level':level}],
          'abilities':{'strength':8,'dexterity':15,'constitution':14,'intelligence':17,'wisdom':12,'charisma':10},
          'speed':30,'size_rank':1,'saving_throw_proficiencies':['intelligence','wisdom'],
          'origin_feats':['alert','skilled'],'skill_proficiencies':['arcana','history','sleight_of_hand','stealth','perception','investigation','insight','medicine'],
          'tool_proficiencies':['thieves_tools'],'weapon_proficiencies':['simple_weapons'],
          'spellcasting_grants':[{'source':'wizard','ability':'intelligence','spells':prepared}],
          'wizard_spellbook':book,'subclasses':({'wizard':'evoker'} if level>=3 else {})}
    # DM authored resources use the exact ordinary Wizard class table. The owning
    # materializer does not derive slots; that source gap is reported separately.
    slots_by_level={1:[2],2:[3],3:[4,2],4:[4,3],5:[4,3,2],6:[4,3,3],7:[4,3,3,1],8:[4,3,3,2],9:[4,3,3,3,1],10:[4,3,3,3,2],11:[4,3,3,3,2,1],12:[4,3,3,3,2,1],13:[4,3,3,3,2,1,1],14:[4,3,3,3,2,1,1],15:[4,3,3,3,2,1,1,1],16:[4,3,3,3,2,1,1,1],17:[4,3,3,3,2,1,1,1,1],18:[4,3,3,3,3,1,1,1,1],19:[4,3,3,3,3,2,1,1,1],20:[4,3,3,3,3,2,2,1,1]}
    resources={'hit_die':level,**{f'spell_slot_{i}':n for i,n in enumerate(slots_by_level[level],1)}}
    state={'max_hp':6+2+(level-1)*6,'hit_die_size':6,'inventory':['staff','spear','dagger'],
           'available_bonus_actions':[],'conditions':[],'damage_resistances':[],
           'damage_immunities':[],'damage_vulnerabilities':[],'prepared_spells':prepared,
           'resources':resources,'resource_maxima':deepcopy(resources),'worn_armor':None,'worn_shield':None}
    validate_parameters(data,artifact['schema'])
    kw={k:artifact[k] for k in ('entity_state_schema','initial_state_calculations','initial_state_validation')}
    completed=materialize_initial_character_state(data,state,**kw)
    assert verify_initial_character_state(data,completed,**kw)==completed
    evidence={'class':'wizard','level':level,'complete_grant_count':len(prepared),'book_count':len(book),
              'outside_launch_prepared':sorted(set(prepared)-launch),'outside_launch_book':sorted(set(book)-launch),
              'materializer':'materialize_initial_character_state','verified_saved_state':True,
              'slot_counts':'DM authored from SRD class table, not derived by materializer'}
    return data,thaw_json_value(completed),evidence

class NativeUtilityCase:
    def __init__(self,path,spell,*,level=5,extra_spells=(),positions=None,geometry=None,scene_objects=None,lighting='bright',entity_changes=None,scene_entity_changes=None,participant_changes=None,character_factory=None,installed_package=None,additional_characters=()):
        self.path=Path(path);self.manager=None
        self.record,artifact,headers=package_inputs();header=headers[spell]
        data,hero,self.acquisition=(character_factory or complete_wizard)(spell,level,extra_spells)
        if participant_changes: raise ValueError('Complete legal class helper does not permit sparse profile overrides')
        hero.update(bonus_action_available=True,reaction_available=True,spell_slot_cast_this_turn=False,ammunition={})
        entities={'hero':hero}
        for identity in ('guard','other','extra'):
            entities[identity]={'name':identity,'creature_kind':'monster','creature_type':'humanoid','hp':1000,'max_hp':1000,'is_alive':True,'conditions':[],
                'exhaustion_level':0,'temp_hp':0,'speed':30,'armor_class':13,'melee_reach':5,'initiative_modifier':0,
                'available_reactions':['opportunity_attack'],'reaction_available':True,'saving_throw_modifiers':dict.fromkeys(data['abilities'],0),
                'attitudes':{'hero':'friendly'},'stable':False,'death_successes':0,'death_failures':0,'stable_recovery_hours_remaining':0,
                'damage_immunities':[],'damage_resistances':[],'damage_vulnerabilities':[],'resources':{},'resource_maxima':{}}
        materials=[{'id':f'material_{i}','inventory_item':f'{spell}_material_{i}','kind':'specific','components':[p['id']],
                    'quantity':p['quantity']*3,'value_cp':p['minimum_value_cp']} for i,p in enumerate(header['material_parts'],1)]
        hero['spell_materials']=materials;hero['inventory'] += [m['inventory_item'] for m in materials]
        additional_profiles=[]
        for identity,additional_spell,additional_level,additional_factory in additional_characters:
            if identity in entities:raise ValueError('An additional complete character needs a unique actual identity')
            extra_data,extra_state,extra_evidence=additional_factory(additional_spell,additional_level,())
            extra_state=deepcopy(extra_state)
            extra_state.update(bonus_action_available=True,reaction_available=True,spell_slot_cast_this_turn=False,ammunition={})
            extra_header=headers[additional_spell]
            extra_materials=[{'id':f'{identity}_material_{i}','inventory_item':f'{additional_spell}_material_{i}','kind':'specific','components':[p['id']],
                'quantity':p['quantity']*3,'value_cp':p['minimum_value_cp']} for i,p in enumerate(extra_header['material_parts'],1)]
            extra_state['spell_materials']=extra_materials
            extra_state['inventory'] += [m['inventory_item'] for m in extra_materials]
            entities[identity]=extra_state
            additional_profiles.append((identity,extra_data))
            self.acquisition.setdefault('additional_characters',{})[identity]=extra_evidence
        for identity,changes in (entity_changes or {}).items():entities[identity].update(deepcopy(changes))
        positions=positions or {'hero':[0,0,0],'guard':[5,0,0],'other':[-5,0,0],'extra':[20,10,0]}
        for identity,_ in additional_profiles:
            if identity not in positions:raise ValueError('Admit the additional character actual scene position')
        scene={'format':1,'map':{'id':'utility','revision':0,'bounds':{'min':[-200,-200,-200],'max':[200,200,200]},'grid':{'cell_size':5,'unit':'ft','origin':[-200,-200]},'layers':[{'id':'world','min_elevation':-200,'max_elevation':200}]},
            'entities':[{'id':identity,'map_id':'utility','position':point,'footprint':{'kind':'box','half_extents':[.4,.4]},'height':6,'facing':[1,0],'active':True,'movement':'blocking','visibility':'opaque','metadata':{'participant_ref':identity,'plane':'material','lighting':lighting,'creature_type':'humanoid'}} for identity,point in positions.items()],
            'geometry':deepcopy(geometry or []),'zones':[{'id':'ambient','map_id':'utility','shape':{'kind':'box','min':[-200,-200,-200],'max':[200,200,200]},'metadata':{'active':True,'plane':'material','lighting':lighting}}],'terrain':[]}
        for row in scene['entities']:row.update(deepcopy((scene_entity_changes or {}).get(row['id'],{})))
        scene['entities'].extend(deepcopy(scene_objects or []));admit_spatial_scene(scene)
        state={'combat':{'encounter_active':False,'turn_active':False,'active_actor':None,'phase':'exploration','action_available':False},'entities':entities,'state':{'lasting_tick':0,'turn_effects':[],'spatial_scene':scene}}
        source=next(g['source'] for g in data['spellcasting_grants'] if spell in g['spells'])
        self.parameters={'spell':spell,'caster':'hero','caster_state':'hero','target':'guard','spell_grant':source,'slot_resource':f"spell_slot_{header['level']}" if header['level'] else 'cantrip',
            'target_distance':5,'caster_can_see_target':True,'caster_can_hear_target':True,'caster_can_touch_target':True,'target_willing':True,'cover':'none','targets_max_distance':5,
            'caster_can_see_all_targets':True,'all_targets_have_clear_path':True,'all_targets_willing':True,'caster_can_touch_all_targets':True,
            'origin_distance':0,'caster_can_see_origin':True,'origin_has_clear_path':True,'all_attack_targets_have_clear_path':True,
            'verbal_component_available':True,'somatic_component_available':True,'material_component_available':True,'material_item_ids':[m['id'] for m in materials]}
        if installed_package is not None:
            template, installed, digest = installed_package
            assert not self.path.exists()
            assert hashlib.sha256(template.read_bytes()).hexdigest() == digest
            shutil.copyfile(template, self.path)
            assert hashlib.sha256(self.path.read_bytes()).hexdigest() == digest
            self.acquisition['normal_install_template_sha256'] = digest
            self.open()
        else:
            self.open()
            installed=self.owner.install_package_archive(io.BytesIO(ARCHIVE.read_bytes()),install_id='utility')
        exact=next(e for e in installed.package_lock.entries if e.package_id==SRD)
        self.owner.create_campaign(CampaignCreate(campaign_id='utility',display_name='Revised SRD utility native proof',writer=WriterRole.DM,package_lock=installed.package_lock,turn_id='turn',
            participants=(Participant(participant_id='hero',kind='player_character',package_id=SRD,package_version=exact.version,package_sha256=exact.sha256,schema_id=artifact['schema_id'],schema_sha256=artifact['schema_sha256'],validation_status=ParticipantValidationStatus.VALIDATED,data=data),)+tuple(
                Participant(participant_id=identity,kind='player_character',package_id=SRD,package_version=exact.version,package_sha256=exact.sha256,
                    schema_id=artifact['schema_id'],schema_sha256=artifact['schema_sha256'],validation_status=ParticipantValidationStatus.VALIDATED,data=extra_data)
                for identity,extra_data in additional_profiles),
            initial_state=tuple(StateValue(SRD,k,v) for k,v in state.items())))

    def open(self):
        self.manager = AuthorityOwner.open(self.path, "utility-owner")
        self.owner = self.manager.__enter__()

    def close(self):
        if self.manager:
            self.manager.__exit__(None, None, None)
            self.manager = None

    def reopen(self):
        before = self.snapshot()
        self.close()
        self.open()
        assert self.snapshot().state == before.state

    def snapshot(self):
        return self.owner.snapshot("utility")

    def entities(self):
        return self.snapshot().state[SRD + ".entities"]

    def lasting(self):
        return self.snapshot().state[SRD + ".state"]

    def command(self, tool, parameters, identity=None):
        revision = self.snapshot().revision
        identity = identity or f"{tool}-{revision}"
        return ActionCommand(
            campaign_id="utility",
            action_id=identity,
            idempotency_key=identity,
            owner_id="utility-owner",
            writer=WriterRole.DM,
            expected_revision=revision,
            turn_id="turn",
            package_id=SRD,
            tool_name=tool,
            parameters=parameters,
        )

    def execute(self, command):
        if command.tool_name in {
            "cast_spell",
            "cast_spell_outside_combat",
            "begin_srd_spell_action",
            "prepare_srd_spells",
            "prepare_srd_creature",
            "use_srd_sunbeam",
        }:
            return StatelessMcpMechanics(self.owner).execute_action(command)
        return StatelessMcpMechanics(self.owner).execute_action(command)

    def act(self, tool, **parameters):
        command = self.command(tool, parameters)
        receipt = self.execute(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        return receipt, command

    def cast(self, **choices):
        if "origin" in choices:
            choices["environment_origin"] = choices.pop("origin")
        return self.act("cast_spell_outside_combat", **(self.parameters | choices))

    def reject(self, tool, parameters, match=None):
        before = self.snapshot()
        command = self.command(tool, parameters)
        try:
            receipt = self.execute(command)
        except (MechanicsRejected, CommandRejected) as error:
            if match is not None:
                import re
                assert re.search(match, str(error)), str(error)
            after = self.snapshot()
            assert after.revision == before.revision
            assert after.actions == before.actions
            assert after.events == before.events
        else:
            # The actual Authority owner records failures after effects begin as
            # a durable FAILED receipt. Its safe outcome contains no mutations.
            assert receipt.status is ActionStatus.FAILED, receipt.result
            assert thaw_json_value(receipt.result) == {
                'status':'failed','code':'tool_execution_failed'}
            after = self.snapshot()
            assert after.revision == before.revision + 1
            assert receipt.resulting_revision == after.revision
            previous = {a.action_id:a for a in before.actions}
            added = [a for a in after.actions if a.action_id not in previous]
            assert len(added) == 1 and added[0].action_id == command.action_id
            assert added[0].status is ActionStatus.FAILED
            assert added[0].result == receipt.result
            assert {a.action_id:a for a in after.actions if a.action_id in previous} == previous
            assert len(after.events) > len(before.events)
            assert after.events[:len(before.events)] == before.events
            assert not any(m.action_id == command.action_id for m in after.mutations)
            # The audit entry must not replay an invalid spend or effect.
            retried = self.execute(command)
            assert retried.status is ActionStatus.FAILED and retried.result == receipt.result
            assert self.snapshot() == after
        assert after.state == before.state
        assert after.participants == before.participants
        assert after.mutations == before.mutations
        assert after.consequences == before.consequences
        assert after.turns == before.turns
        assert after.continuity == before.continuity

    def start(self, second_actor="guard"):
        self.act(
            "start_combat_encounter_entities",
            first_actor="hero",
            second_actor=second_actor,
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        self.act("begin_combat_turn")
        # Follow the real unmodified Initiative roll. If the NPC wins, finish
        # its unused turn through the public turn owners before testing Hero.
        if self.snapshot().state[SRD + ".combat"]["active_actor"] != "hero":
            self.next_turn()
        assert self.snapshot().state[SRD + ".combat"]["active_actor"] == "hero"

    def next_turn(self):
        self.act("end_combat_turn")
        self.act("advance_combat_encounter_turn")
        self.act("begin_combat_turn")

    def attack(self, target="guard"):
        return self.act(
            "resolve_combat_attack",
            actor="hero",
            target=target,
            attack_sequence="primary",
            use_graze_on_miss=False,
            weapon="spear",
            attack_mode="melee",
            attack_ability="strength",
            wielding_two_handed=False,
            mounted=False,
            circumstantial_modifier=0,
            advantage=False,
            disadvantage=False,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
            attack_distance=5,
        )

@pytest.fixture
def native_utility_case(native_utility_installed_package):
    from tests.lite_fixture_archive import archive_closed_case
    opened=[]
    def make(tmp_path,name,spell,**kwargs):
        case=NativeUtilityCase(tmp_path/(name+'.db'),spell,installed_package=native_utility_installed_package,**kwargs);opened.append(case);return case
    yield make
    for case in opened:archive_closed_case(case)

@pytest.fixture(scope='session')
def native_utility_installed_package(tmp_path_factory):
    """Reuse one closed, normally admitted package library; create each campaign afresh."""
    from tests.lite_fixture_archive import archive_closed_database
    template = tmp_path_factory.getbasetemp() / 'normally-installed-package.db'
    with AuthorityOwner.open(template, 'utility-owner') as owner:
        installed = owner.install_package_archive(io.BytesIO(ARCHIVE.read_bytes()), install_id='utility')
    digest = hashlib.sha256(template.read_bytes()).hexdigest()
    template.with_suffix('.identity.json').write_text(json.dumps({
        'candidate':str(ARCHIVE), 'candidate_sha256':hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
        'closed_template_sha256':digest, 'normal_admission':True,
        'campaigns_created_individually':True, 'operation_limit':10000, 'node_limit':500000,
        'engine_or_rng_mocking':False,
    }, indent=2), encoding='utf-8')
    yield template, installed, digest
    assert hashlib.sha256(template.read_bytes()).hexdigest() == digest
    archive_closed_database(template, template.parent)
