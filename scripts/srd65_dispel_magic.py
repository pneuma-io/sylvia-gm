"""Retained SRD Dispel checks with exact illumination and ordinary target geometry."""
from .srd_control_behaviors import (
    _call, _each, _entity, _lasting, _param, _push, _require, _set, _tool, _when,
)

CHOICES = ('illumination_object', 'illumination_effect')
SOURCE = {'source_id': 'wotc.srd.5.2.1.pdf', 'page': 124,
          'sha256': '8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87'}


def _path(point, plane):
    return [
        _require(f'{plane} == _srd65_dispel_plane', 'The actual Dispel target must share the caster plane.'),
        _call('measure_srd_information_distance',
              {'start': '{_srd65_dispel_origin}', 'end': '{' + point + '}'}, ('_scene_distance',)),
        _require('_scene_distance <= 120', 'The actual Dispel target is outside 120 feet.'),
        _call('srd_information_barriers',
              {'start': '{_srd65_dispel_origin}', 'end': '{' + point + '}',
               'rule': 'solid', 'plane': '{_srd65_dispel_plane}'}, ('_information_blocked',)),
        _require('_information_blocked != true', 'A solid barrier blocks the actual Dispel path.'),
    ]


def _caster_origin():
    return [
        _call('srd_information_scene_entity', {'subject': '{_caster_state_id}'},
              ('_info_scene_entity', '_info_position')),
        _set('_srd65_dispel_origin', '_info_position'),
        _set('_srd65_dispel_plane', '_info_scene_entity.metadata.plane'),
        _require('_srd65_dispel_plane != null', 'Dispel needs the actual caster plane.'),
    ]


def authored_tools():
    recipient = _tool('Qualify actual ordinary Dispel range, plane and solid path without requiring sight.',
        [_entity('caster_state'), _entity('target_state')], [
            *_caster_origin(),
            _call('srd_information_scene_entity', {'subject': '{_target_state_id}'},
                  ('_info_scene_entity', '_info_position')),
            *_path('_info_position', '_info_scene_entity.metadata.plane'),
        ])
    selected = _tool('Join one actual illumination object or single-anchor effect, then use retained source checks/removal.',
        [_entity('caster_state'), _lasting(),
         _param('caster', resolve='participant', required=False, default=None),
         _param('choice', 'enum', enum_values=list(CHOICES)), _param('source_id'),
         _param('slot_level', 'integer'), _param('casting_modifier', 'integer'),
         _param('casting_ability'), _param('validate_only', 'boolean', required=False, default=False)], [
            *_caster_origin(),
            _set('_srd65_selected_rows', []),
            _set('_srd65_light_rows', []),
            _when('lasting_state.turn_effects != null',
                  [_set('_srd65_light_rows', 'lasting_state.turn_effects')]),
            _set('_srd65_selected_point', None), _set('_srd65_selected_plane', None),
            _when("choice == 'illumination_object'", [
                _call('srd_information_scene_entity', {'subject': '{source_id}'},
                      ('_info_scene_entity', '_info_position')),
                _require('_info_scene_entity.metadata.is_object == true', 'Choose an actual admitted Dispel object.'),
                _set('_srd65_selected_point', '_info_position'),
                _set('_srd65_selected_plane', '_info_scene_entity.metadata.plane'),
                _set('_srd65_holder', None),
                _when("_info_scene_entity.metadata.carried_by != null and _info_scene_entity.metadata.carried_by != ''",
                      [_set('_srd65_holder', '_info_scene_entity.metadata.carried_by')]),
                _when("_srd65_holder == null and _info_scene_entity.metadata.worn_by != null and _info_scene_entity.metadata.worn_by != ''",
                      [_set('_srd65_holder', '_info_scene_entity.metadata.worn_by')]),
                _when('_srd65_holder != null', [
                    _call('srd_information_scene_entity', {'subject': '{_srd65_holder}'},
                          ('_info_scene_entity', '_info_position')),
                    _set('_srd65_selected_point', '_info_position'),
                    _set('_srd65_selected_plane', '_info_scene_entity.metadata.plane'),
                ]),
            ]),
            _each('_srd65_light_rows', '_srd65_lamp', [
                _when("_srd65_lamp.kind == 'illumination_spell' and (_srd65_lamp.mode == 'object' or _srd65_lamp.mode == 'point_or_object' or _srd65_lamp.mode == 'self')", [
                    _set('_srd65_match', False),
                    _when("choice == 'illumination_object' and _srd65_lamp.source == source_id", [_set('_srd65_match', True)]),
                    _when("choice == 'illumination_effect'", [
                        _call('format_srd_information_projection', {'message': 'illumination:{_srd65_lamp.instance}'}, ('_info_projection_message',)),
                        _set('_srd65_match', '_info_projection_message == source_id'),
                    ]),
                    _when('_srd65_match == true', [
                        _call('check_srd_illumination_record',
                              {'record': '{_srd65_lamp}', 'caster_state': '{_srd65_lamp.caster}',
                               'lasting_state': '{_lasting_state_id}'},
                              ('_illumination_active', '_illumination_origin', '_illumination_plane')),
                        _when('_illumination_active == true', [
                            _push('_srd65_selected_rows', '_srd65_lamp'),
                            _when("choice == 'illumination_effect'", [
                                _set('_srd65_selected_point', '_illumination_origin'),
                                _set('_srd65_selected_plane', '_illumination_plane'),
                            ]),
                        ]),
                    ]),
                ]),
            ]),
            _require("choice != 'illumination_effect' or len(_srd65_selected_rows) == 1",
                     'Select one actual active single-anchor illumination effect instance.'),
            *_path('_srd65_selected_point', '_srd65_selected_plane'),
            _set('_srd65_dispel_checks', []),
            _each('_srd65_selected_rows', '_srd65_selected_lamp', [
                _set('_srd65_source_level', -1),
                _when('_srd65_selected_lamp.spell_level != null', [_set('_srd65_source_level', '_srd65_selected_lamp.spell_level')]),
                _when('_srd65_source_level < 0 and _srd65_selected_lamp.slot_level != null',
                      [_set('_srd65_source_level', '_srd65_selected_lamp.slot_level')]),
                _require('_srd65_source_level >= 0 and _srd65_source_level <= 9',
                         'Dispel needs the actual saved illumination casting level.'),
                _when('validate_only != true', [
                    _call('resolve_srd_dispel_source_check',
                          {'caster_state': '{_caster_state_id}', 'caster': '{_caster_id}',
                           'source_level': '{_srd65_source_level}', 'slot_level': '{slot_level}',
                           'casting_modifier': '{casting_modifier}', 'casting_ability': '{casting_ability}'},
                          ('_dispel_succeeded', '_dispel_roll')),
                    _push('_srd65_dispel_checks',
                          {'spell': '_srd65_selected_lamp.spell', 'instance': '_srd65_selected_lamp.instance',
                           'source': '_srd65_selected_lamp.source', 'caster': '_srd65_selected_lamp.caster',
                           'source_level': '_srd65_source_level', 'dc': '10 + _srd65_source_level',
                           'roll': '_dispel_roll', 'ended': '_dispel_succeeded'}),
                    _when('_dispel_succeeded == true', [
                        _call('remove_srd_illumination_instance',
                              {'caster_state': '{_srd65_selected_lamp.caster}',
                               'instance': '{_srd65_selected_lamp.instance}',
                               'lasting_state': '{_lasting_state_id}'}),
                    ]),
                ]),
            ]),
        ])
    mobility = _tool('Rebase only the exact successfully dispelled mobility source to surviving saved recipients, then use retained concentration refresh.',
        [_entity('caster_state'), _lasting(), _param('spell'), _param('cast_id')], [
            _when("cast_id != '' and caster_state.concentration_spell_id == spell and "
                  "caster_state.concentration_cast_id == cast_id and "
                  "caster_state.concentration_effect_family == 'multi_target_timed_actor_mobility' and "
                  "caster_state.concentration_target_state_id != null", [
                _set('_srd65_mobility_rows', []), _set('_srd65_mobility_recipients', []),
                _when('lasting_state.turn_effects != null', [
                    _set('_srd65_mobility_rows', 'lasting_state.turn_effects'),
                ]),
                _each('_srd65_mobility_rows', '_srd65_survivor', [
                    _when("_srd65_survivor.kind == 'actor_mobility' and "
                          "_srd65_survivor.caster == _caster_state_id and "
                          "_srd65_survivor.source_caster_id == _caster_state_id and "
                          "_srd65_survivor.spell == spell and "
                          "_srd65_survivor.cast_id == cast_id and "
                          "_srd65_survivor.concentration == true and "
                          "_srd65_survivor.expires_tick != null and "
                          "lasting_state.lasting_tick + 0.000000001 < _srd65_survivor.expires_tick and "
                          "_srd65_survivor.target not in _srd65_mobility_recipients", [
                        _push('_srd65_mobility_recipients', '_srd65_survivor.target'),
                    ]),
                ]),
                _set('caster_state.concentration_target_state_ids', '_srd65_mobility_recipients'),
                _when('len(_srd65_mobility_recipients) > 0', [
                    _set('caster_state.concentration_target_state_id', '_srd65_mobility_recipients[0]'),
                ]),
                # An empty source retains its old actual primary solely for
                # the unchanged refresh/termination owner to resolve it.
                _call('refresh_srd_concentration_source', {
                    'source_state': '{_caster_state_id}',
                    'target_state': '{caster_state.concentration_target_state_id}',
                    'lasting_state': '{_lasting_state_id}',
                }),
            ]),
        ])
    return {'qualify_srd65_dispel_recipient': recipient,
            'dispel_srd65_illumination_selection': selected,
            'refresh_srd65_dispelled_mobility_recipients': mobility}


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values(): yield from _walk(child)
    elif isinstance(value, list):
        for child in value: yield from _walk(child)


def bind_srd65_dispel_magic(runtime):
    """Compose after retained source owners and parameter expansion; no roster changes."""
    profile = runtime['definitions']['spells']['dispel_magic']
    assert (profile['family'], profile['base_level'], profile['casting_time'],
            profile['range_kind'], profile['range_feet']) == ('recovery', 3, 'action', 'distance', 120)
    tools = runtime['tools']
    if 'dispel_srd65_illumination_selection' in tools: return
    tools.update(authored_tools())
    choice_test = "recovery_choice == 'illumination_object' or recovery_choice == 'illumination_effect'"
    for name in ('cast_spell', 'cast_spell_outside_combat', 'begin_srd_spell_action', 'cast_srd_recovery'):
        for node in _walk(tools[name]):
            setting = node.get('set', {})
            if setting.get('field') in {'_recovery_source_intents', '_recovery_source_owners'}:
                for choice in CHOICES:
                    if repr(choice) not in setting['value']: setting['value'].append(repr(choice))
    for name in ('cast_spell', 'cast_spell_outside_combat', 'begin_srd_spell_action'):
        tools[name]['effects'].insert(0, _when("spell == 'dispel_magic'", [
            _when("recovery_choice == 'none' or recovery_choice == ''", [
                _require('target != null', 'Select an actual Dispel creature recipient.'),
                _require('targets == null or len(targets) == 0 or (len(targets) == 1 and targets[0] == _target_id)',
                         'Dispel selects exactly one actual creature recipient.'),
                _call('qualify_srd65_dispel_recipient',
                      {'caster_state': '{_caster_state_id}', 'target_state': '{_target_id}'}),
            ]),
            _when(choice_test, [
                _call('dispel_srd65_illumination_selection',
                      {'caster_state': '{_caster_state_id}', 'choice': '{recovery_choice}',
                       'source_id': '{recovery_source_id}', 'validate_only': True,
                       'slot_level': 0, 'casting_modifier': 0, 'casting_ability': 'intelligence'}),
            ]),
        ]))
    matches = [node for node in _walk(tools['dispel_srd_target_sources'])
               if node.get('condition', '').startswith('_dispel_effect.target == _target_state_id and')]
    assert len(matches) == 1, 'Use the actual ordinary turn-effect removal boundary.'
    dispel = tools['dispel_srd_target_sources']
    dispel['effects'].insert(0, _set('_srd65_removed_mobility', []))
    matches[0]['on_true'].append(_when(
        "_dispel_remove == true and _dispel_effect.kind == 'actor_mobility' and "
        "_dispel_effect.concentration == true and _dispel_effect.cast_id != null and "
        "_dispel_effect.cast_id != '' and _dispel_effect.caster == _dispel_effect.source_caster_id",
        [_push('_srd65_removed_mobility', '_dispel_effect')]))
    matches[0]['on_true'] = [_when(
        "not (_dispel_effect.kind == 'illumination_spell' and "
        "(_dispel_effect.mode == 'object' or _dispel_effect.mode == 'point_or_object' or _dispel_effect.mode == 'dancing'))",
        matches[0]['on_true'])]
    writes = [index for index, node in enumerate(dispel['effects'])
              if node.get('set') == {'field': 'lasting_state.turn_effects', 'value': '_dispel_kept'}]
    assert len(writes) == 1, 'Rebase only after the actual successful saved-row filter.'
    dispel['effects'].insert(writes[0] + 1, _each('_srd65_removed_mobility', '_srd65_removed_mobility_row', [
        _call('refresh_srd65_dispelled_mobility_recipients', {
            'caster_state': '{_srd65_removed_mobility_row.caster}',
            'spell': '{_srd65_removed_mobility_row.spell}',
            'cast_id': '{_srd65_removed_mobility_row.cast_id}',
            'lasting_state': '{_lasting_state_id}',
        }),
    ]))
    recovery = tools['cast_srd_recovery']
    recovery['effects'].append(_when("spell == 'dispel_magic' and (" + choice_test + ')', [
        _call('dispel_srd65_illumination_selection',
              {'caster_state': '{_caster_state_id}', 'caster': '{_caster_id}',
               'choice': '{recovery_choice}', 'source_id': '{recovery_source_id}',
               'slot_level': '{slot_level}', 'casting_modifier': '{casting_modifier}',
               'casting_ability': '{casting_ability}'}, ('_srd65_dispel_checks',)),
        _set('_dispel_checks', '_srd65_dispel_checks'),
        _set('_spell_summary_rows', [{'label': "'Illumination target'", 'value': 'recovery_source_id'}]),
        _set('_spell_detail_rows', []),
    ]))
    recovery['effects'].append(_when("spell == 'dispel_magic' and _dispel_checks != null", [
        _push('_spell_detail_rows', {'label': "'Dispel source decisions'", 'value': '_dispel_checks'}),
    ]))
