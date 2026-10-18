"""Draft: validate chosen SRD Chain targets with saved scene distances only.

Keep the immutable 10d8 save/damage/cardinality/component owners. This is not the
preserved legacy automatic-target/history adapter. Parent owns integration and tests.
"""


INTERNAL_TOOL = 'validate_srd_chain_selected_range'


def _require(condition, error):
    return {'require': {'condition': condition, 'error': error}}


def _relation(actor, target):
    return {'call': 'resolve_srd_scene_relation_node',
            'params': {'actor_state': actor, 'target_state': target,
                       'lasting_state': '{_lasting_state_id}'},
            'export_context': {'_scene_distance': '_scene_distance',
                               '_scene_same_plane': '_scene_same_plane'}}


def bind_srd_chain_selected_range(runtime):
    """Bind fresh SRD profiles before boundary; do not run the old Chain binder."""
    tools = runtime['tools']
    if 'collect_srd_lite_chain_targets' in tools:
        raise ValueError('Build SRD Chain reuse without the preserved legacy Chain adapter')
    definition = runtime['definitions']['spells']['chain_lightning']
    expected = {'range_feet': 150, 'secondary_range_feet': 30, 'base_level': 6,
                'base_targets': 4, 'targets_per_slot_level': 1, 'base_dice': 10,
                'damage_die': 'd8', 'dice_per_slot_level': 0}
    if any(definition.get(key) != value for key, value in expected.items()):
        raise ValueError('Retain the immutable SRD Chain profile for this binder')
    tools[INTERNAL_TOOL] = {
        'description': 'Validate chosen Chain Lightning creatures against actual primary and secondary scene ranges.',
        'parameters': [
            {'name': 'caster_state', 'type': 'string', 'resolve': 'entity', 'required': True},
            {'name': 'primary_state', 'type': 'string', 'resolve': 'entity', 'required': True},
            {'name': 'lasting_state', 'type': 'string', 'resolve': 'entity', 'required': False, 'default': 'state'},
            {'name': 'targets', 'type': 'entity_id_list', 'resolve': 'entity_list',
             'required': True, 'min_items': 1, 'max_items': 64, 'unique_items': True},
        ],
        'pre_conditions': [],
        'effects': [
            _require('_primary_state_id in targets', 'Include the actual primary in the chosen Chain target list.'),
            _relation('{_caster_state_id}', '{_primary_state_id}'),
            _require('_scene_same_plane == true and _scene_distance <= 150',
                     'The actual Chain primary must be on the same plane within 150 feet.'),
            {'set': {'field': '_srd_chain_primary_distance', 'value': '_scene_distance'}},
            {'for_each': {'collection': 'targets', 'as': '_srd_chain_selected', 'do': [
                {'condition': '_srd_chain_selected != _primary_state_id', 'on_true': [
                    _relation('{_primary_state_id}', '{_srd_chain_selected}'),
                    _require('_scene_same_plane == true and _scene_distance <= 30',
                             'Every actual Chain secondary must be on the primary plane within 30 feet of the primary.'),
                ]},
            ]}},
        ],
        'metadata': {'category': 'internal', 'visible_to_player': False, 'costs_action': False},
    }
    for name in ('cast_spell', 'begin_srd_spell_action'):
        effects = tools[name]['effects']
        insertion = next(i + 1 for i, effect in enumerate(effects)
                         if effect.get('table_lookup', {}).get('table') == 'definitions.spells')
        effects.insert(insertion, {'condition': "spell == 'chain_lightning'", 'on_true': [
            _require('target != null and targets != null and len(targets) >= 1',
                     'Choose one primary and an explicit Chain target list.'),
            {'call': INTERNAL_TOOL,
             'params': {'caster_state': '{_caster_state_id}', 'primary_state': '{_target_id}',
                        'lasting_state': '{_lasting_state_id}', 'targets': '{targets}'},
             'export_context': {'_srd_chain_primary_distance': '_srd_chain_primary_distance'}},
        ]})
    return runtime
