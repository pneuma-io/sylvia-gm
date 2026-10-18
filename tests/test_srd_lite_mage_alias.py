"""UNRUN public casting regression; imported mechanics fixture, not acquisition proof."""
import pytest

from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import Case
from tests.test_srd_lite_defenses import (
    _mage_params, _mage_records, _public_routes, _reject, _replay_reopen,
)


@pytest.fixture
def alias_case(tmp_path):
    opened = []

    def make(**changes):
        entities = changes.pop('entity_changes', {})
        entities['hero'] = {'worn_armor': None, 'worn_shield': None,
                            'armor_class': 12, **entities.get('hero', {})}
        case = Case(tmp_path / f'alias-{len(opened)}.db', 'mage_armor',
                    entity_changes=entities, **changes)
        opened.append(case)
        return _public_routes(case)

    yield make
    for case in opened:
        archive_closed_case(case)


@pytest.mark.parametrize('tool', ['cast_spell', 'cast_spell_outside_combat', 'begin_srd_spell_action'])
@pytest.mark.parametrize('spelling', ['mage_armor', 'mage_armour'])
def test_mage_advertised_alias_has_canonical_saved_source_and_once_only_cost(alias_case, tool, spelling):
    case = alias_case()
    if tool != 'cast_spell_outside_combat':
        case.start()
    before = case.entities()['hero']['resources']['spell_slot_1']
    receipt, command = case.act(tool, **_mage_params(case, spell=spelling, slot_resource='spell_slot_1'))
    _replay_reopen(case, receipt, command)
    if tool == 'begin_srd_spell_action':
        pending_id = receipt.result['context']['_pending_action_id']
        pending = case.lasting()['pending_srd_actions'][pending_id]
        assert pending['spell'] == 'mage_armor'
        receipt, command = case.act('complete_srd_pending_action', pending_action_id=pending_id)
        _replay_reopen(case, receipt, command)
        assert pending_id not in case.lasting().get('pending_srd_actions', {})
    record, = _mage_records(case)
    assert record['spell'] == 'mage_armor' and record['source_caster_id'] == 'hero'
    assert record['lite_prepared_source'] == 'wizard' and record['cast_id']
    assert record['magnitude'] == 15 and record['lifetime_kind'] == 'until_long_rest'
    assert record['expires_tick'] is None
    assert case.entities()['hero']['resources']['spell_slot_1'] == before - 1
    _replay_reopen(case, receipt, command)


def test_mage_alias_keeps_actual_armor_target_rejection_atomic(alias_case):
    case = alias_case(entity_changes={'hero': {'worn_armor': 'leather'}})
    _reject(case, 'cast_spell_outside_combat',
            _mage_params(case, spell='mage_armour', slot_resource='spell_slot_1'), 'unarmored')
    assert not _mage_records(case)
