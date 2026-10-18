"""Representative native consumers of the newly composed worker repairs."""
from tests.test_native_followups import make_case, save_card


def test_dragon_actual_expiry_clears_its_saved_concentration(make_case, tmp_path):
    case = make_case('dragons_breath')
    case.cast(target='hero', targets=['hero'], target_distance=0, selected_damage_type='fire')
    assert case.entities()['hero']['concentration_spell_id'] == 'dragons_breath'
    before = dict(case.entities()['hero']['resources'])
    receipt, _ = case.act('advance_srd_lasting_time', elapsed_rounds=10)
    save_card(tmp_path, 'dragon-expiry', receipt)
    assert not any(r['kind'] == 'dragon_breath' for r in case.lasting()['turn_effects'])
    assert case.entities()['hero']['concentration_spell_id'] is None
    assert case.entities()['hero']['resources'] == before


def test_native_mass_healing_receivers_and_actual_saved_targets(make_case, tmp_path):
    case = make_case('mass_healing_word', casting_source='cleric',
        entity_changes={'guard': {'hp': 500}, 'other': {'hp': 600}})
    receipt, _ = case.cast(targets=['guard', 'other'], targets_max_distance=5)
    save_card(tmp_path, 'mass-healing', receipt)
    assert case.entities()['guard']['hp'] > 500
    assert case.entities()['other']['hp'] > 600
    assert case.entities()['extra']['hp'] == 1000


def test_environment_sight_composes_with_actual_ray_attack(make_case, tmp_path):
    spells=['fog_cloud','ray_of_frost']
    case = make_case('fog_cloud', participant_changes={
        'spellcasting_grants':[{'source':'wizard','ability':'intelligence','spells':spells}],
        'wizard_spellbook':['fog_cloud']}, entity_changes={'hero':{'prepared_spells':spells}})
    case.cast(environment_origin=[0,0,0])
    case.start()
    receipt, _ = case.act('cast_spell', **(case.parameters|{
        'spell':'ray_of_frost','slot_resource':'cantrip','target':'guard',
        'caster_can_see_target':True,'target_can_see_caster':True}))
    save_card(tmp_path, 'fog-ray', receipt)
    assert case.entities()['guard']['hp'] < 1000


def test_ice_storm_actual_cylinder_selects_inside_creatures(make_case, tmp_path):
    case = make_case('ice_storm', positions={
        'hero':[-50,0,0], 'guard':[5,0,0], 'other':[30,0,0], 'extra':[5,0,50]},geometry=[])
    receipt, _ = case.cast(environment_origin=[0,0,0],origin_distance=50,
        targets=['guard'])
    save_card(tmp_path, 'ice-cylinder', receipt)
    assert case.entities()['guard']['hp'] < 1000
    assert case.entities()['other']['hp'] == 1000
    assert case.entities()['extra']['hp'] == 1000


def test_guardian_entry_damage_does_not_repeat_on_exit_or_reentry(make_case, tmp_path):
    ground={'id':'floor','map_id':'lite','volume':{
        'kind':'box','min':[-100,-100,-1],'max':[100,100,0]},
        'active':True,'movement':'blocking','visibility':'opaque',
        'destroyed':{'active':False,'movement':'passable','visibility':'clear'},
        'metadata':{'ground':True,'plane':'material'}}
    case = make_case('guardian_of_faith',casting_source='cleric',
        positions={'hero':[10,-10,0],'guard':[0,0,0],'other':[-40,0,0],'extra':[-40,20,0]},
        entity_changes={'guard':{'attitudes':{'hero':'hostile'}}},
        scene_metadata={'guard':{'enemy_of':['hero']}},geometry=[ground])
    case.cast(force_id='faith_guardian',created_actor_position=[20,0,0],created_actor_height=10)
    case.start()
    case.next_turn()
    assert case.snapshot().state[case.record['id']+'.combat']['active_actor'] == 'guard'
    receipt, _ = case.act('move_srd_scene_creature',actor_state='guard',destination=[10,0,0],mode='walk')
    card=save_card(tmp_path, 'guardian-entry', receipt)
    damaged = case.entities()['guard']['hp']
    assert damaged < 1000
    assert any(row['label']=='Guardian radiant damage' and row['value']==1000-damaged
        for row in card['summary'])
    assert card['details_label']=='Spell effects and calculations'
    assert any('Saving throw against DC' in row['value'] and f'Damage {1000-damaged}' in row['value']
        for row in card['details'])
    case.act('move_srd_scene_creature',actor_state='guard',destination=[0,0,0],mode='walk')
    assert case.entities()['guard']['hp'] == damaged
    case.act('move_srd_scene_creature',actor_state='guard',destination=[10,0,0],mode='walk')
    assert case.entities()['guard']['hp'] == damaged


def test_sunburst_actual_sphere_has_valid_recipient_identity_arrays(make_case, tmp_path):
    case=make_case('sunburst')
    receipt, _ = case.cast(targets=['hero','guard','other','extra'],
        sunburst_origin=[0,0,0],sunburst_plane='material')
    save_card(tmp_path, 'sunburst-sphere', receipt)
    assert case.entities()['guard']['hp'] < 1000
    assert case.entities()['other']['hp'] < 1000
    assert case.entities()['extra']['hp'] < 1000


def test_area_damage_card_exposes_the_actual_concentration_roll(make_case, tmp_path):
    spells=['dragons_breath','acid_splash']
    case=make_case('dragons_breath',participant_changes={
        'spellcasting_grants':[{'source':'wizard','ability':'intelligence','spells':spells}],
        'wizard_spellbook':['dragons_breath']},entity_changes={'hero':{'prepared_spells':spells}})
    case.cast(target='hero',targets=['hero'],target_distance=0,selected_damage_type='fire')
    for attempt in range(4):
        receipt, _ = case.cast(spell='acid_splash',slot_resource='cantrip',target='hero',
            targets=['hero'],target_distance=0,targets_max_distance=0,origin_distance=0,
            all_targets_in_area=True,all_targets_have_clear_path=True,
            caster_can_see_origin=True,origin_has_clear_path=True,material_item_ids=[])
        context=receipt.result['context']
        roll=context.get('_spell_area_last_concentration_save_roll')
        if roll is not None:
            card=save_card(tmp_path,'actual-concentration',receipt)
            dc=context['_spell_area_last_concentration_save_dc']
            total=roll['total']+roll['modifier']
            outcome=context['_spell_area_last_concentration_save_outcome']
            assert outcome == ('success' if total>=dc else 'failure')
            rows=[r for r in card['details'] if 'concentration' in r['label']]
            assert len(rows)==1
            assert rows[0]['label']=='Lite caster concentration'
            assert card['title']=='Acid Splash'
            assert f'DC {dc}: {outcome}' in rows[0]['value']
            assert f'= {total}.' in rows[0]['value']
            break
        case.act('advance_srd_lasting_time',elapsed_rounds=1)
    else:
        raise AssertionError('No actual positive-damage concentration save was observed')
