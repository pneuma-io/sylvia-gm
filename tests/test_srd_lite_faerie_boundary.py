"""Native Faerie sphere boundaries, actual placement rejection and combat payment."""
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_zones import retain
from tests.lite_imported_fixture import ImportedCase
from tests.test_srd_lite_foundation import VARIANT_ID


def test_faerie_sphere_closed_body_not_box_and_actual_placement_cost(tmp_path):
    wall = {
        'id': 'wall', 'map_id': 'lite',
        'volume': {'kind': 'box', 'min': [-11, -10, 80], 'max': [-9, 10, 100]},
        'active': True, 'movement': 'blocking', 'visibility': 'opaque',
        'metadata': {'material': 'stone'},
        'destroyed': {'active': False, 'movement': 'passable', 'visibility': 'clear'},
    }
    case = ImportedCase(tmp_path / 'faerie-boundary.db', 'faerie_fire',
        participant_changes={
            'classes': [{'id': 'druid', 'level': 17}], 'wizard_spellbook': [],
            'spellcasting_grants': [{'source': 'druid', 'ability': 'wisdom', 'spells': ['faerie_fire']}],
            'abilities': {'strength': 16, 'dexterity': 14, 'constitution': 14,
                'intelligence': 18, 'wisdom': 18, 'charisma': 10}},
        entity_changes={'hero': {'saving_throw_modifiers': {'dexterity': 100}},
            'guard': {'saving_throw_modifiers': {'dexterity': -100}},
            'other': {'saving_throw_modifiers': {'dexterity': -100}},
            'extra': {'saving_throw_modifiers': {'dexterity': -100}}},
        positions={'hero': [-50, 30, 90], 'guard': [-9.6, 30, 90],
            'other': [-9.59, 30, 90], 'extra': [-11, 49, 90]},
        scene_geometry=[wall])
    try:
        case.start()
        initial = case.snapshot()
        resources = dict(case.entities()['hero']['resources'])
        case.reject('cast_spell', case.parameters | {
            'illumination_position': [0, 0, 93], 'origin_distance': 0,
            'origin_has_clear_path': True}, match='solid barrier')
        case.reject('cast_spell', case.parameters | {
            'illumination_position': [-30, 30, 101], 'origin_distance': 0,
            'origin_has_clear_path': True}, match='admitted map')
        assert case.snapshot() == initial
        receipt, command = case.act('cast_spell', **(case.parameters | {
            'illumination_position': [-30, 30, 93], 'slot_resource': 'spell_slot_2',
            'somatic_component_available': False, 'material_component_available': False}))
        marked = case.lasting()['turn_effects']
        marked_ids = {row['source'] for row in marked if row.get('kind') == 'illumination_spell'
            and row.get('spell') == 'faerie_fire'}
        assert 'guard' in marked_ids
        assert 'other' not in marked_ids
        assert 'extra' not in marked_ids
        assert case.entities()['hero']['resources'] == resources | {
            'spell_slot_2': resources['spell_slot_2'] - 1}
        assert case.snapshot().state[VARIANT_ID + '.combat']['action_available'] is False
        assert case.entities()['hero']['concentration_spell_id'] == 'faerie_fire'
        assert receipt.result['result_card']['title'] == 'Faerie Fire'
        retain(case, receipt, command)
    finally:
        archive_closed_case(case)
