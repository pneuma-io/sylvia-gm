"""Compose independently rolled force darts through the retained damage owner."""

from .srd_control_behaviors import _each, _entity, _lasting, _param, _push
from .srd_timed_actor_defenses import _call, _require, _set, _tool, _when


def bind_missiles(runtime):
    """Enable the profile only when the caller also selects it for final-byte verification."""
    runtime['definitions']['spells']['magic_missile']['range_feet'] = 60
    runtime['definitions']['spell_casting']['magic_missile']['range'] = '60 feet'
    tools = runtime['tools']
    tools['validate_srd_lite_missile_target'] = _tool(
        'Qualify each allocated force-dart target using actual scene sight and range.',
        [_entity('caster_state'), _entity('target_state'), _lasting()],
        [
            _require("target_state.is_alive == true and (target_state.creature_kind == 'character' or target_state.creature_kind == 'monster')", 'Magic Missile needs a living creature when targets are selected.'),
            _call('resolve_srd_scene_relation_node', {'actor_state': '{_caster_state_id}', 'target_state': '{_target_state_id}', 'lasting_state': '{_lasting_state_id}'}, ('_scene_distance', '_scene_same_plane', '_scene_line_of_sight', '_scene_can_see')),
            _require('_scene_distance <= 60 and _scene_same_plane == true and _scene_line_of_sight == true and _scene_can_see == true', 'Every actual dart target needs sight and a clear path within sixty feet.'),
        ],
    )
    qualify = _call('validate_srd_lite_missile_target', {'caster_state': '{_caster_state_id}', 'target_state': '{_lite_missile_target}', 'lasting_state': '{_lasting_state_id}'})
    for name in ('cast_spell', 'begin_srd_spell_action'):
        tools[name]['effects'].insert(0, _when("spell == 'magic_missile'", [
            _when('projectile_targets != null', [_each('projectile_targets', '_lite_missile_target', [qualify])]),
            _when('projectile_targets == null', [_call('validate_srd_lite_missile_target', {'caster_state': '{_caster_state_id}', 'target_state': '{_target_id}', 'lasting_state': '{_lasting_state_id}'})]),
        ]))
    tools['apply_srd_lite_force_dart'] = _tool(
        'Apply one already rolled dart and record its actual damage; skip further damage after death.',
        [_entity('caster_state'), _entity('target'), _lasting(), _param('face', 'integer'), _param('index', 'integer')],
        [
            _set('_lite_dart_hp', 0), _set('_lite_dart_temp', 0),
            _set('_lite_dart_alive_before', 'target.is_alive'),
            _when('target.is_alive == true', [_call('apply_srd_magic_missile_damage', {
                'target': '{_target_id}', 'amount': '{face}', 'adjustment': 1, 'damage_type': 'force',
                'source_state_id': '{_caster_state_id}', 'lasting_state': '{_lasting_state_id}',
            }, ('_hp_lost', '_temp_hp_absorbed')),
                _set('_lite_dart_hp', '_hp_lost'), _set('_lite_dart_temp', '_temp_hp_absorbed'),
            ]),
            _call('record_srd_spell_target_result', {
                'target': '{_target_id}', 'brief': 'Dart {index}: HP {_lite_dart_hp}; temporary HP {_lite_dart_temp}',
                'detail': 'Dart {index}. d4: {face}; + 1 force before mitigation. HP lost {_lite_dart_hp}; temporary HP lost {_lite_dart_temp}. Target alive before dart: {_lite_dart_alive_before}.',
            }, ('_spell_target_summary', '_spell_target_detail')),
        ],
    )
    tool = tools['cast_spell_automatic_force_projectiles']
    tool['effects'] = [
        _set('_spell_summary_rows', []), _set('_spell_detail_rows', []),
        _set('_spell_projectiles', 'base_projectiles + (slot_level - base_level) * projectiles_per_slot_level'),
        {'dice': {'expression': '{_spell_projectiles}{damage_die}'}, 'store_as': '_spell_damage'},
        _set('_lite_missile_faces', '_spell_damage.results'),
        _set('_lite_missile_index', 0),
        _each('_lite_missile_faces', '_lite_missile_face', [
            _set('_lite_missile_target', '_target_id'),
            _when('projectile_targets != null', [_set('_lite_missile_target', 'projectile_targets[_lite_missile_index]')]),
            _set('_lite_missile_index', '_lite_missile_index + 1'),
            _call('apply_srd_lite_force_dart', {'caster_state': '{_caster_state_id}', 'target': '{_lite_missile_target}', 'lasting_state': '{_lasting_state_id}', 'face': '{_lite_missile_face}', 'index': '{_lite_missile_index}'}, ('_spell_target_summary', '_spell_target_detail')),
            _push('_spell_summary_rows', '_spell_target_summary'), _push('_spell_detail_rows', '_spell_target_detail'),
        ]),
        _set('_spell_flat_damage', '_spell_projectiles * flat_damage_per_projectile'),
    ]
