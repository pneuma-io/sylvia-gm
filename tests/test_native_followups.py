"""Changed follow-ups execute against the exact admitted candidate and saved receipts."""
import json
from dataclasses import replace
from random import Random
from pathlib import Path
import pytest
from sylvia_gm import dice
from sylvia_gm.domain.authority import thaw_json_value
from tests.test_srd_lite_foundation import Case
from tests import test_srd_lite_foundation as support
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from scripts.srd_lite_catalogue import VARIANT_ID
from sylvia_gm.spatial import admit_spatial_scene
from sylvia_gm.domain.errors import MechanicsRejected

@pytest.fixture
def make_case(tmp_path, monkeypatch):
    cases = []
    monkeypatch.setattr(dice, '_rng', Random(0))
    monkeypatch.setattr(dice, 'set_seed', lambda _seed: None)
    execute_tool = package_runtime.execute_tool
    def observed_execution(*args, **kwargs):
        result = execute_tool(*args, **kwargs)
        if not result.success:
            print('NATIVE_RUNTIME_FAILURE', result.tool_name, result.failure_kind, result.error, flush=True)
        return result
    monkeypatch.setattr(package_runtime, 'execute_tool', observed_execution)
    def make(spell, **kwargs):
        scene_metadata=kwargs.pop('scene_metadata',{})
        source=kwargs.get('casting_source') or 'wizard'
        _, data=support._combat_case([{'id':source,'level':17}])
        ability='wisdom' if source in {'cleric','druid','ranger'} else ('charisma' if source in {'bard','sorcerer','warlock'} else 'intelligence')
        data['abilities'][ability]=18
        data.update(kwargs.get('participant_changes') or {})
        proficient=data.get('saving_throw_proficiencies',['intelligence','wisdom'])
        saving_modifiers={key:(value-10)//2+(6 if key in proficient else 0) for key,value in data['abilities'].items()}
        changes=dict(kwargs.get('entity_changes') or {})
        changes['hero']={'saving_throw_modifiers':saving_modifiers,
            'stable':False,'death_successes':0,'death_failures':0,
            'stable_recovery_hours_remaining':0,'dodging':False,'speed':30}|changes.get('hero',{})
        kwargs['entity_changes']=changes
        original_create=AuthorityOwner.create_campaign
        def create_with_initial_scene(owner, command):
            frames=[]
            for frame in command.initial_state:
                if frame.namespace==VARIANT_ID and frame.key=='state' and scene_metadata:
                    value=thaw_json_value(frame.value)
                    for row in value['spatial_scene']['entities']:
                        row['metadata'].update(scene_metadata.get(row['id'],{}))
                    admit_spatial_scene(value['spatial_scene'])
                    frame=replace(frame,value=value)
                frames.append(frame)
            return original_create(owner,replace(command,initial_state=tuple(frames)))
        with monkeypatch.context() as initial_setup:
            initial_setup.setattr(AuthorityOwner,'create_campaign',create_with_initial_scene)
            case = Case(tmp_path / (spell + '.sqlite3'), spell, **kwargs)
        case.owner.set_admin_full_audit(True)
        original_execute=case.execute
        def observed_action(command):
            try:
                return original_execute(command)
            except MechanicsRejected as error:
                print('NATIVE_ADMISSION_FAILURE', command.tool_name,
                    error.code, error.field_paths, error.validation_issues, flush=True)
                raise
        case.execute=observed_action
        cases.append(case)
        return case
    yield make
    for case in cases:
        case.close()

def save_card(tmp_path, name, receipt):
    card = thaw_json_value(receipt.result['result_card'])
    (tmp_path / (name + '-card.json')).write_text(json.dumps(card, indent=2))
    return card

def test_darkvision_saved_source_dismissal(make_case, tmp_path):
    case = make_case('darkvision')
    case.cast(target='hero', targets=['hero'], target_distance=0)
    assert any(row['kind'] == 'timed_senses' and row['spell'] == 'darkvision' for row in case.lasting()['turn_effects'])
    receipt, _ = case.act('dismiss_srd_senses_spell', caster_state='hero', spell='darkvision')
    save_card(tmp_path, 'darkvision-dismiss', receipt)
    assert not any(row['kind'] == 'timed_senses' and row['spell'] == 'darkvision' for row in case.lasting()['turn_effects'])

def test_sleep_shake_ends_actual_source_and_spends_action(make_case, tmp_path):
    case = make_case('sleep', entity_changes={'guard': {'saving_throw_modifiers': {'wisdom': -30}}})
    case.cast(targets=['guard'], environment_origin=[5,0,0])
    assert 'incapacitated' in case.entities()['guard']['conditions']
    case.start()
    receipt, _ = case.act('shake_srd_sleeping_creature', actor_state='hero', target_state='guard', target_distance=5)
    save_card(tmp_path, 'sleep-shake', receipt)
    assert 'incapacitated' not in case.entities()['guard']['conditions']
    assert case.snapshot().state[case.record['id'] + '.combat']['action_available'] is False

def test_animals_actual_beast_communication_and_dismissal(make_case, tmp_path):
    case = make_case('speak_with_animals', casting_source='druid', entity_changes={'guard': {'creature_type': 'beast', 'languages': []}})
    case.cast(target='hero', targets=['hero'], target_distance=0)
    receipt, _ = case.act('communicate_srd_spell', listener_state='hero', speaker_state='guard')
    card = save_card(tmp_path, 'animals-communicate', receipt)
    assert any(row['label'] == 'Understands' and row['value'] == 'Yes' for row in card['summary'])
    case.act('dismiss_srd_information_spell', caster_state='hero', spell='speak_with_animals')
    case.reject('communicate_srd_spell', {'listener_state': 'hero', 'speaker_state': 'guard'})

def test_alter_self_change_uses_actual_saved_source(make_case, tmp_path):
    case = make_case('alter_self')
    case.cast(target='hero', targets=['hero'], target_distance=0, transformation_option='aquatic')
    assert case.entities()['hero']['alter_self_option'] == 'aquatic'
    case.start()
    receipt, _ = case.act('change_srd_transformation', actor_state='hero', option='claws')
    save_card(tmp_path, 'alter-self-change', receipt)
    assert case.entities()['hero']['alter_self_option'] == 'claws'
    assert case.snapshot().state[case.record['id'] + '.combat']['action_available'] is False
