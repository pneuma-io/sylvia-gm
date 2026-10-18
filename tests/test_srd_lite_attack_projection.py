"""The actual Lite attack adapter must not write intent scalars to saved combat."""
import pytest

from scripts.srd_lite_attacks import bind_attacks
from sylvia_gm.runtime import _effect_executor
from sylvia_gm.runtime._types import ExecutionContext, ToolResult


@pytest.mark.parametrize('spell,owner,alive', [
    ('guiding_bolt', 'cast_spell_attack_damage', True),
    ('inflict_wounds', 'cast_spell_attack_damage', True),
    ('scorching_ray', 'resolve_spell_multi_attack_target', True),
    ('scorching_ray', 'resolve_spell_multi_attack_target', False),
])
def test_actual_scene_projection_remains_ephemeral_and_preserves_unrelated_combat(spell, owner, alive):
    runtime = {
        'definitions': {'spells': {'guiding_bolt': {'turn_effect': {}}, 'scorching_ray': {}},
                        'spell_casting': dict.fromkeys(('guiding_bolt', 'scorching_ray', 'inflict_wounds'))},
        'tools': {name: {'effects': []} for name in (
            'cast_spell', 'begin_srd_spell_action', 'cast_spell_attack_damage',
            'resolve_spell_multi_attack_target', 'install_srd_turn_effect')},
    }
    runtime['definitions']['spell_casting'] = {name: {} for name in runtime['definitions']['spell_casting']}
    bind_attacks(runtime)
    actual = {'_lite_attack_distance': 4 if spell == 'inflict_wounds' else 12.5,
              '_lite_attack_caster_sees': False, '_lite_attack_target_sees': True,
              '_lite_attack_threatened': True, '_lite_attack_same_plane': True,
              '_lite_attack_line_of_sight': True}
    calls = []

    def nested(name, parameters, context):
        calls.append((name, dict(parameters)))
        assert name == 'resolve_srd_lite_attack_facts'
        return ToolResult(name, context=actual)

    combat = {'round': 4, 'active_actor': 'hero'}
    ctx = ExecutionContext(
        params={'spell': spell, 'target_distance': 0, 'caster_can_see_target': True,
                'target_can_see_caster': False, 'enemy_within_5_feet': False,
                'close_enemy_can_see_caster': False, 'close_enemy_incapacitated': True},
        vars={'_caster_state_id': 'hero', '_target_id': 'guard'},
        entities={'target': {'is_alive': alive}, 'caster_state': {'conditions': []}}, combat_state=combat,
    )
    _effect_executor.execute_effects(runtime['tools'][owner]['effects'], ctx, nested)
    assert calls == ([('resolve_srd_lite_attack_facts', {'caster_state': 'hero', 'target_state': 'guard', 'lasting_state': 'state'})] if alive else [])
    assert combat == {'round': 4, 'active_actor': 'hero'}
    if alive:
        assert all(ctx.vars[key] == value for key, value in actual.items())
    if owner == 'resolve_spell_multi_attack_target':
        assert '_spell_target_armor_class' in ctx.vars
        assert ctx.vars['_spell_target_armor_class'] is None


def test_attack_projection_preserves_quoted_saved_parameter_keys():
    from scripts.srd_lite_attacks import _expression_names
    row = {'key': "'target_distance'", 'missing': 'target_distance == null', 'value': 'target_distance'}
    assert _expression_names(row, {'target_distance': '_actual_distance'}) == {
        'key': "'target_distance'", 'missing': '_actual_distance == null', 'value': '_actual_distance',
    }
