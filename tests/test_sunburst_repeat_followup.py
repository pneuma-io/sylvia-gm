"""Sunburst's actual repeat-save and blindness cleanup through admitted authority actions."""
from tests.test_native_followups import make_case, save_card


def test_successful_sunburst_repeat_save_removes_its_actual_blindness_source(make_case, tmp_path):
    case = make_case('sunburst', positions={'hero': [-80, 0, 0], 'guard': [5, 0, 0],
        'other': [90, 0, 0], 'extra': [-95, 0, 0]}, geometry=[])
    cast, _ = case.cast(targets=['guard'], sunburst_origin=[0, 0, 0], sunburst_plane='material')
    save_card(tmp_path, 'sunburst-initial-save', cast)
    assert 'blinded' in case.entities()['guard']['conditions']
    source_ids = list(case.entities()['guard']['timed_condition_source_ids'])
    assert len(source_ids) == 1
    source_id = source_ids[0]
    source = case.lasting()['timed_condition_sources'][source_id]
    assert source['spell_id'] == 'sunburst' and source['installed'] is True
    resources = dict(case.entities()['hero']['resources'])
    hp = case.entities()['guard']['hp']
    case.start()
    end_card = None
    for turn in range(1, 9):
        case.next_turn()
        assert case.snapshot().state[case.record['id'] + '.combat']['active_actor'] == 'guard'
        receipt, _ = case.act('end_combat_turn')
        context = receipt.result['context']
        rolls = context['_timed_condition_end_turn_rolls']
        assert len(rolls) == 1, 'The actual saved Sunburst source earns one end-turn save'
        end_card = save_card(tmp_path, 'sunburst-repeat-' + str(turn), receipt)
        assert any('vs DC' in row['value'] and ('source remains' in row['value'] or 'source ended' in row['value'])
                   for row in end_card['details'])
        assert case.entities()['hero']['resources'] == resources
        assert case.entities()['guard']['hp'] == hp
        if 'blinded' not in case.entities()['guard']['conditions']:
            assert any(': success; source ended' in row['value'] for row in end_card['details'])
            assert source_id not in case.entities()['guard']['timed_condition_source_ids']
            assert case.lasting()['timed_condition_sources'][source_id]['installed'] is False
            break
        assert case.lasting()['timed_condition_sources'][source_id]['installed'] is True
        case.act('advance_combat_encounter_turn')
        case.act('begin_combat_turn')
    else:
        raise AssertionError('The seeded natural Sunburst follow-up did not produce the selected success case')
    assert end_card is not None
