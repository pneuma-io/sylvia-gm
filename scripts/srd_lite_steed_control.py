"""Derive controlled Find Steed actions and initiative from the actual riding caster."""
from copy import deepcopy
from .srd_timed_actor_defenses import _call, _entity, _require, _set, _tool, _when
from .srd_lite_steed_fey_step import RIDER_FIELD, MOUNT_FIELD
from .srd_lite_mass_cure_geometry import _effect_lists

READ = 'read_srd_otherworldly_steed_control'
OWNER = 'read_srd_otherworldly_steed_riding_caster'
ELIGIBILITY = 'refresh_srd_otherworldly_initiative_eligibility'
ELIGIBLE_FIELD = 'otherworldly_steed_initiative_eligible'
CONTROLLED = '_otherworldly_steed_controlled'
INCAPACITATED = (
    "'incapacitated' in caster_state.conditions or 'paralyzed' in caster_state.conditions or "
    "'petrified' in caster_state.conditions or 'stunned' in caster_state.conditions or "
    "'unconscious' in caster_state.conditions"
)


def _riding_caster():
    """An ally riding does not imply the spell's special caster-controlled turn rule."""
    return _tool('Read the actual living, capable caster riding their own active Otherworldly Steed.',
        [_entity('steed_state'), _entity('caster_state')], [_set(CONTROLLED, False), _when(
        "steed_state.created_spell_id == 'find_steed' and steed_state.created_active == true and "
        'steed_state.is_alive == true and steed_state.hp > 0 and '
        'steed_state.created_caster_state_id == _caster_state_id and '
        'caster_state.created_steed_id == _steed_state_id and '
        'steed_state.'+RIDER_FIELD+' == _caster_state_id and '
        'caster_state.'+MOUNT_FIELD+' == _steed_state_id', [_when(
        'caster_state.is_alive == true and caster_state.hp > 0 and '
        'caster_state.conditions != null and not ('+INCAPACITATED+')', [_set(CONTROLLED, True)])])])


def _read():
    """Read control without spending resources or changing the creature's living state."""
    return _tool('Derive whether this actual Otherworldly Steed is controlled by its riding caster.',
        [_entity('actor_state')], [_set(CONTROLLED, False),
        _when("actor_state.created_spell_id == 'find_steed' and actor_state.created_active == true and "
            'actor_state.created_caster_state_id != null', [
            _call(OWNER, {'steed_state': '{_actor_state_id}',
                         'caster_state': '{actor_state.created_caster_state_id}'}, (CONTROLLED,))])])


def _eligibility():
    """Keep defeated-creature skipping while deriving a separate, package-owned control flag."""
    return _tool('Project actual living initiative eligibility, excluding a caster-controlled mounted Steed.',
        [_entity('actor_state')], [
        _call(READ, {'actor_state': '{_actor_state_id}'}, (CONTROLLED,)),
        _set('actor_state.'+ELIGIBLE_FIELD, False),
        _when('actor_state.is_alive != false and '+CONTROLLED+' != true',
              [_set('actor_state.'+ELIGIBLE_FIELD, True)])])


def _initiative_cards(tools):
    """Show native rolled dice while identifying the Steed's inherited caster initiative."""
    organizer = tools['organize_srd_summons_in_initiative']
    steed_rows = []
    for rows in _effect_lists(organizer['effects']):
        for effect in rows:
            value = effect.get('list_push', {}).get('value')
            if isinstance(value, dict) and value.get('entity_id') == '_initiative_linked_steed':
                steed_rows.append(value)
    if len(steed_rows) != 1 or 'initiative_source_label' in steed_rows[0]:
        raise ValueError('Keep one original linked Steed initiative entry')
    steed_rows[0]['initiative_source_label'] = '_initiative_entry.label'
    owner = tools['start_group_combat_encounter']
    card = owner['metadata']['result_card']
    if 'equations' in card:
        raise ValueError('Group Initiative roll widgets must be composed exactly once')
    projections = [(index, effect['for_each']) for index, effect in enumerate(owner['effects'])
                   if effect.get('for_each', {}).get('collection') == '_initiative_order'
                   and effect['for_each'].get('as') == '_entry']
    if len(projections) != 1:
        raise ValueError('Keep the original group Initiative card assembly')
    index, projection = projections[0]
    summary = [effect for effect in projection['do']
               if effect.get('list_push', {}).get('list') == '_initiative_summary']
    details = [effect for effect in projection['do']
               if effect.get('list_push', {}).get('list') == '_initiative_details']
    if len(summary) != 1 or len(details) != 4 or len(projection['do']) != 5:
        raise ValueError('Keep the native Initiative result and complete raw calculation rows')
    equation = {'die_sides': 20, 'roll': '_entry.roll.total',
                'modifier': '_entry.modifier', 'total': '_entry.initiative_result'}
    details[-1]['list_push']['value']['equation'] = deepcopy(equation)
    projection['do'] = [*summary, {
        'condition': '_entry.initiative_source_label != null',
        'on_true': [{'list_push': {'list': '_initiative_details', 'value': row}} for row in (
            {'label': "'Creature'", 'value': '_entry.label'},
            {'label': "'Shares Initiative with'", 'value': '_entry.initiative_source_label'},
            {'label': "'Total'", 'value': '_entry.initiative_result'})],
        'on_false': [*details, _when('len(_steed_initiative_equations) < 4', [
            {'list_push': {'list': '_steed_initiative_equations', 'value': {
                'label': '_entry.label', 'value': "'Initiative'", 'equation': equation}}}])] }]
    owner['effects'].insert(index, _set('_steed_initiative_equations', []))
    card['equations'] = {'path': 'context._steed_initiative_equations'}


def bind_steed_control(runtime):
    """Bind actual shared Action payment and the sole native group-initiative transition."""
    tools = runtime['tools']
    if any(name in tools for name in (READ, OWNER, ELIGIBILITY)):
        raise ValueError('Otherworldly Steed control must be composed exactly once')
    tools.update({READ: _read(), OWNER: _riding_caster(), ELIGIBILITY: _eligibility()})
    spend = tools['spend_srd_action']
    spend['effects'][0:0] = [
        _call(READ, {'actor_state': '{combat.active_actor}'}, (CONTROLLED,)),
        _require(CONTROLLED+" != true or (magic_action != true and "
            "(control_action_kind == 'dash' or control_action_kind == 'disengage' or "
            "control_action_kind == 'dodge'))",
            'A caster-controlled mounted Otherworldly Steed can take only Dash, Disengage or Dodge Actions.')]
    dodge_calls = [effect for effect in tools['take_dodge_action']['effects']
                   if effect.get('call') == 'spend_srd_action']
    if len(dodge_calls) != 1:
        raise ValueError('Keep the single native Dodge Action payment')
    dodge_calls[0]['params']['control_action_kind'] = 'dodge'
    advance = tools['advance_group_combat_encounter_turn']
    indices = [index for index, effect in enumerate(advance['effects'])
               if 'advance_initiative' in effect]
    if len(indices) != 1:
        raise ValueError('Keep the sole native group initiative transition')
    index = indices[0]
    owner = advance['effects'][index]
    if owner['advance_initiative'] != {'skip_field': 'is_alive', 'skip_equals': False}:
        raise ValueError('Preserve the original defeated-creature skip rule')
    refresh = {'for_each': {'collection': '_steed_control_order', 'as': '_steed_control_order_row', 'do': [
        _call(ELIGIBILITY, {'actor_state': '{_steed_control_order_row.entity_id}'})]}}
    advance['effects'][index:index] = [_set('_steed_control_order', 'combat.order'), refresh]
    owner['advance_initiative']['skip_field'] = ELIGIBLE_FIELD
    _initiative_cards(tools)
    # Clock synchronization and the rest of the existing advance remain in their original order.
    return [READ, OWNER, ELIGIBILITY, 'spend_srd_action', 'take_dodge_action',
            'advance_group_combat_encounter_turn', 'start_group_combat_encounter',
            'organize_srd_summons_in_initiative']
