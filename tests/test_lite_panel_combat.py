"""Actual admitted Lite controls retain native mechanics and atomic intent admission."""
from copy import deepcopy
import json
import pytest
from tests.lite_imported_fixture import ImportedCase
from tests.lite_fixture_archive import archive_closed_case
from sylvia_gm.domain.errors import CommandRejected, RulesetPreconditionRejected, StaleRevision
from sylvia_gm.domain.authority import thaw_json_value
from pathlib import Path
from functools import lru_cache
import os
from sylvia_gm.application.bundled_packages import bundled_srd_archive

NS="sylvia.srd-lite.bg3"
MAP="srd-coordinate-map"

@lru_cache(maxsize=1)
def _native_lite_archive():
    """Native control journeys use the current package, preserving the legacy foundation fixture."""
    return bundled_srd_archive()

def case(path,spell="fire_bolt",target=(5,0,0),*,melee_weapon="spear"):
    value=ImportedCase(path,spell,archive_bytes=_native_lite_archive(),participant_changes={"party_role":"player_character"},positions={"hero":[0,0,0],"guard":list(target)},
        scene_metadata_changes={identity:{"player":{"label":identity.title(),"kind":"unit"}} for identity in ("hero","guard")},
        entity_changes={"hero":{"held_items":[melee_weapon],"inventory":[melee_weapon,"shortbow"],
                                "ammunition":{"arrow":20}}})
    value.act("prepare_srd_weapon_set",actor_state="hero",loadout=[{"set_name":"melee","main_hand":melee_weapon,"off_hand":""}])
    value.act("prepare_srd_weapon_set",actor_state="hero",loadout=[{"set_name":"ranged","main_hand":"shortbow","off_hand":""}])
    value.act("start_combat_encounter_entities",first_actor="hero",second_actor="guard",
              first_surprised=False,second_surprised=False,tie_winner="first")
    value.act("begin_combat_turn")
    return value

def command(value,action,operation="manual"):
    projection=value.owner.campaign_panels("lite")
    surface=next(s for s in projection["surfaces"] if s["id"]==MAP)
    declared=next(a for a in surface["actions"] if a["id"]==action)
    evidence=Path(os.environ["SYLVIA_QA_FIXTURE_ROOT"])/"panel-records"
    evidence.mkdir(exist_ok=True)
    (evidence/(action+"-"+operation+".json")).write_text(json.dumps(thaw_json_value(projection),indent=2))
    selection={"map_revision":surface["data"]["map_revision"]}
    for source in (declared.get("bindings") or {}).values():
        selection[source]="hero" if source=="actor" else "guard"
    return dict(campaign_id="lite",surface_id=MAP,panel_action_id=action,
                operation_id=operation,expected_revision=projection["revision"],
                selection=selection if declared.get("bindings") else None)

def readiness(value,request):
    before=value.snapshot()
    response=value.owner.player_action_availability(campaign_id='lite',expected_revision=before.revision,
        requests=[{'source':{'kind':'panel','surface_id':request['surface_id'],'action_id':request['panel_action_id']},
                   'selection':request['selection']}])
    assert value.snapshot()==before, 'Readiness must not execute effects or roll dice'
    return response['results'][0]

def execute(value,request):
    before=value.snapshot()
    assert value.owner.panel_action_receipt(**request) is None
    ready=readiness(value,request)
    assert ready['available'] is True, ready
    if request['panel_action_id'].startswith(('attack-','cast-')):
        assert ready['map_preview']['ray']['origin']==[0,0,0]
        scene=next(surface['data'] for surface in value.owner.campaign_panels('lite')['surfaces']
                   if surface['id']==MAP)
        assert ready['map_preview']['ray']['destination']==next(row['position'] for row in scene['entities'] if row['id']=='guard')
    preview=value.owner.preview_panel_action(**request)
    assert value.snapshot()==before,"Preview must be read-only"
    assert preview["confirmation_required"]
    return value.owner.execute_panel_action(**request,confirmation=preview["confirmation"])

@pytest.mark.parametrize("spell,action",[("fire_bolt","cast-fire_bolt"),("magic_missile","cast-magic_missile")])
def test_actual_spell_control_spends_once_and_reopens(tmp_path,spell,action):
    value=case(tmp_path/(spell+".sqlite3"),spell)
    try:
        before=deepcopy(value.entities()["hero"]["resources"])
        request=command(value,action)
        unavailable=command(value,'cast-ray_of_frost','unprepared')
        assert readiness(value,unavailable)['available'] is False
        receipt=execute(value,request)
        assert receipt.status.value=="committed"
        after=value.snapshot()
        assert readiness(value,request)['available'] is False, 'Spent combat action disables another cast'
        assert not after.state[NS+".combat"]["action_available"]
        assert after.state[NS+".combat"]["turn_active"] is True
        assert value.owner.panel_action_receipt(**request)==receipt
        assert value.owner.execute_panel_action(**request)==receipt
        assert value.snapshot()==after
        if spell=="magic_missile": assert value.entities()["hero"]["resources"]["spell_slot_1"]==before["spell_slot_1"]-1
        value.reopen();assert value.snapshot()==after
    finally: archive_closed_case(value)

def test_actual_melee_ranged_dash_and_turn_are_distinct(tmp_path):
    value=case(tmp_path/"combat.sqlite3")
    try:
        request=command(value,"attack-melee-strength")
        receipt=execute(value,request)
        assert receipt.status.value=="committed"
        assert value.snapshot().state[NS+".combat"]["turn_active"] is True
        end=execute(value,command(value,"end-turn","end"))
        assert end.status.value=="committed"
        assert value.snapshot().state[NS+".combat"]["turn_active"] is False
        saved=value.snapshot();value.reopen();assert value.snapshot()==saved
        # Explicit native enemy lifecycle, then another actual controlled round.
        value.act("advance_combat_encounter_turn");value.act("begin_combat_turn"); assert value.snapshot().state[NS+".combat"]["active_actor"]=="guard"
        value.act("end_combat_turn");value.act("advance_combat_encounter_turn");value.act("begin_combat_turn")
        value.act("activate_srd_weapon_set",actor_state="hero",set_name="ranged")
        arrows=value.entities()["hero"]["ammunition"]["arrow"]
        ranged=execute(value,command(value,"attack-ranged-dexterity","ranged"))
        assert ranged.status.value=="committed", repr(ranged.result)
        assert value.entities()["hero"]["ammunition"]["arrow"]==arrows-1
        execute(value,command(value,"end-turn","end-ranged"))
        value.act("advance_combat_encounter_turn");value.act("begin_combat_turn");value.act("end_combat_turn");value.act("advance_combat_encounter_turn");value.act("begin_combat_turn")
        movement=value.snapshot().state[NS+".combat"]["movement_remaining"]
        dash=execute(value,command(value,"dash","dash"))
        assert dash.status.value=="committed"
        combat=value.snapshot().state[NS+".combat"]
        assert combat["movement_remaining"]>movement and combat["dashes_taken"]==1
        assert combat["action_available"] is False and combat["turn_active"] is True
        saved=value.snapshot();value.reopen();assert value.snapshot()==saved

    finally: archive_closed_case(value)

def test_stale_unauthorized_and_out_of_range_controls_mutate_nothing(tmp_path):
    value=case(tmp_path/"rejected.sqlite3",target=(50,0,0))
    try:
        request=command(value,"attack-melee-strength")
        before=value.snapshot()
        for change in ({"expected_revision":before.revision-1},{"selection":{**request["selection"],"actor":"guard"}},
                       {"selection":{**request["selection"],"map_revision":999}}):
            with pytest.raises(StaleRevision if "expected_revision" in change else CommandRejected): value.owner.preview_panel_action(**{**request,**change})
            assert value.snapshot()==before
        with pytest.raises(RulesetPreconditionRejected, match="range|reach|distance"):
            execute(value,request)
        assert value.entities()["hero"]["resources"]==before.state[NS+".entities"]["hero"]["resources"]
        assert value.snapshot().state==before.state
    finally: archive_closed_case(value)
