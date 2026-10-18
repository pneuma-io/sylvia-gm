"""Expose the SRD scene and its existing committed movement owner in the map panel."""

from .srd_lite_catalogue import VARIANT_ID

MAP_PANEL_ID = 'srd-coordinate-map'


def bind_coordinate_map(record):
    """Bind raw 3D destination intent; the existing SRD tool owns qualification and costs."""
    assert not any(row['id'] == MAP_PANEL_ID for row in record['ui_surfaces'])
    assert any(row['name'] == 'move_srd_scene_creature' for row in record['tools'])
    if 'tactical_grid.v1' not in record['compatibility']['requires']:
        record['compatibility']['requires'].append('tactical_grid.v1')
    record['ui_surfaces'].append({
        'id': MAP_PANEL_ID,
        'capability': 'dnd.srd.5.2.1.movement-modes',
        'title': 'Combat map',
        'icon': '◇',
        'renderer': 'coordinate_map',
        'source': {'kind': 'object', 'fields': {
            'scene': {'kind': 'state', 'namespace': VARIANT_ID, 'key': 'state.spatial_scene'},
            'enabled': {'kind': 'state', 'namespace': VARIANT_ID, 'key': 'combat.encounter_active'},
        }},
        'group': None,
        'order': 5,
        'default_size': 520,
        'minimum_size': 240,
        'default_placement': 'docked',
        'allowed_placements': ['docked', 'floating', 'window'],
        'actions': [{
            'id': 'move-' + mode,
            'label': label,
            'intent': 'move_srd_scene_creature',
            'params': {'mode': mode},
            'bindings': {'actor_state': 'actor', 'destination': 'aim_point'},
            'actor_reference': 'participant_ref',
        } for mode, label in (('walk', 'Move'), ('fly', 'Fly'), ('climb', 'Climb'), ('swim', 'Swim'))],
    })
