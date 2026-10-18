"""Ward-only and shared damage regressions through genuine public producers."""
import json
from tests.test_srd_lite_foundation import Case,VARIANT_ID
from tests.lite_fixture_archive import archive_closed_case


def test_ward_alone_halves_real_fall_and_post_expiry_and_foreign_actor_do_not(tmp_path):
    case=Case(tmp_path/'ward-only-controls.db','blade_ward',entity_changes={
        'hero':{'hp':1000,'max_hp':1000,'damage_resistances':[],
                'damage_vulnerabilities':[],'damage_immunities':[]},
        'guard':{'damage_resistances':[],'damage_vulnerabilities':[],'damage_immunities':[]}})
    observed=[]
    try:
        resources=dict(case.entities()['hero']['resources'])
        cast,cast_command=case.cast(target='hero',targets=['hero'])
        for identity,label in [('hero','Ward alone'),('guard','foreign non-Ward')]:
            hp=case.entities()[identity]['hp']
            fall,command=case.act('resolve_srd_fall',target_state=identity,
                distance=30,landing='ground',reaction='none')
            context=fall.result['context']
            roll=context['_fall_roll']
            raw=roll['total']
            effective=context['_fall_effective_damage']
            assert len(roll['results'])==3 and raw==sum(roll['results'])
            assert effective==(raw//2 if identity=='hero' else raw)
            assert case.entities()[identity]['hp']==hp-effective
            saved=case.snapshot()
            assert case.execute(command)==fall and case.snapshot()==saved
            observed.append({'control':label,'raw':raw,'effective':effective,
                'actual_faces':list(roll['results']),'hp_before':hp,'hp_after':case.entities()[identity]['hp']})
        case.act('advance_srd_lasting_time',elapsed_rounds=2)
        assert not any(r.get('kind')=='lite_blade_ward' for r in case.lasting()['turn_effects'])
        hp=case.entities()['hero']['hp']
        fall,command=case.act('resolve_srd_fall',target_state='hero',distance=30,landing='ground',reaction='none')
        context=fall.result['context']
        raw=context['_fall_roll']['total']
        assert context['_fall_effective_damage']==raw
        assert case.entities()['hero']['hp']==hp-raw
        assert case.entities()['hero']['resources']==resources
        expired=case.snapshot()
        assert case.execute(cast_command)==cast and case.execute(command)==fall
        assert case.snapshot()==expired
        case.reopen()
        assert case.execute(cast_command)==cast and case.execute(command)==fall and case.snapshot()==expired
        observed.append({'control':'post expiry','raw':raw,'effective':raw,
            'actual_faces':list(context['_fall_roll']['results']),'hp_before':hp,'hp_after':case.entities()['hero']['hp']})
        (tmp_path/'ward-only-observations.json').write_text(json.dumps(observed,indent=2),encoding='utf-8')
    finally:
        archive_closed_case(case)
