"""Apply authoritative Otherworldly Slam cover without altering its native dice."""
from copy import deepcopy
from .srd_timed_actor_defenses import _call, _require, _set, _when
from .srd_lite_damage_roll_receipts import _lists
from .srd_lite_steed_slam import CARD_OWNERS

GEOMETRY = 'qualify_srd_created_actor_attack_geometry'
COVER = '_created_attack_cover_bonus'
ARMOR = '_steed_slam_target_ac'
STEED = "actor.created_spell_id == 'find_steed' and actor.form_attack_in_progress != true"


def _capture(field, source=None):
    """Use the native nullable context representation at each existing phase boundary."""
    value = field if source is None else source + '.' + field
    return {'key': repr(field), 'missing': value + ' == null', 'value': value}


def bind_steed_slam_cover(runtime):
    """Preserve geometry, damage and reactions; derive partial cover and carry it with the roll."""
    tools = runtime['tools']
    geometry = deepcopy(tools[GEOMETRY])
    guard = [effect for effect in geometry['effects'] if
        effect.get('require', {}).get('error') ==
        "The Steed's partial Cover attack consumer is not yet supported."]
    if len(guard) != 1:
        raise ValueError('Keep the exact native Steed geometry and Total Cover owner')
    collectors = [effect for rows in _lists(geometry['effects']) for effect in rows
        if effect.get('call') == 'collect_srd_spell_area_cover_targets']
    if len(collectors) != 1:
        raise ValueError('Keep the single native source-eye Cover collection')
    # The accepted point consumer keeps scene IDs scalar, and excludes only the actual source body.
    collectors[0]['call'] = 'collect_srd_sunburst_cover_targets'
    index = geometry['effects'].index(guard[0])
    geometry['effects'][index:index + 1] = [
        _set(COVER, 0),
        _when('_target_state_id in _srd_area_half_cover', [_set(COVER, 2)]),
        _when('_target_state_id in _srd_area_three_quarters', [_set(COVER, 5)])]
    tools[GEOMETRY] = geometry
    changed = {GEOMETRY}
    for name in ('resolve_adjudicated_creature_attack', 'begin_srd_creature_attack'):
        tool = tools[name]
        sites = 0
        for rows in _lists(tool['effects']):
            for effect in rows:
                if effect.get('call') == GEOMETRY:
                    effect['export_context'][COVER] = COVER
                    sites += 1
        if sites != 7:
            raise ValueError('Keep all seven native Steed geometry paths: ' + name)
        tool['effects'].insert(0, _set(COVER, 0))
        changed.add(name)
    targeting = tools['continue_srd_creature_attack_targeting']
    targeting['effects'][0:0] = [_set(COVER, 0), _when(STEED, [
        _call(GEOMETRY, {'actor_state': '{_actor_id}', 'target_state': '{_target_id}'},
            {'_created_attack_distance': '_created_attack_distance', COVER: COVER}),
        _require('_created_attack_distance <= 5',
                 'Otherworldly Slam requires an actual target within its 5-foot reach.')])]
    changed.add('continue_srd_creature_attack_targeting')
    completion = tools['complete_srd_creature_attack']
    completion['effects'].insert(0, _set(COVER, 0))
    # Capture on targeting and restore on completion; retain the original D20 through reactions.
    sites = 0
    for rows in _lists(targeting['effects']):
        for effect in rows:
            value = effect.get('set', {})
            if value.get('field') == '_retained_nullable_capture_rows' and any(
                    row['key'] == "'_attack_roll'" for row in value['value']):
                value['value'].append(_capture(COVER))
                sites += 1
    if sites != 1:
        raise ValueError('Keep the single native rolled-attack pending context capture')
    sites = 0
    for rows in _lists(completion['effects']):
        for effect in rows:
            value = effect.get('set', {})
            if value.get('field') == '_retained_nullable_capture_rows' and any(
                    row['key'] == "'_attack_roll'" for row in value['value']):
                value['value'].append(_capture(COVER, '_pending_action.context'))
                sites += 1
            if value.get('field') == '_hit_suffix_context':
                value['value'] += [_capture(COVER), _capture(ARMOR)]
    if sites != 1:
        raise ValueError('Keep the single native pending attack context restoration')
    for name in ('resolve_adjudicated_creature_attack',
                 'continue_srd_creature_attack_targeting', 'complete_srd_creature_attack'):
        tool = tools[name]
        sites = 0
        for rows in list(_lists(tool['effects'])):
            for index in range(len(rows) - 1, -1, -1):
                effect = rows[index]
                if effect.get('call') != 'resolve_srd_effective_armor_class':
                    continue
                rows[index:index] = [_set('_hand_attack_origin', '_actor_id'),
                    _set('_hand_existing_cover', 0),
                    _when(STEED, [_set('_hand_existing_cover', COVER)])]
                rows[index + 4:index + 4] = [_set(ARMOR, None), _when(STEED, [
                    _set('_effective_target_armor_class', '_effective_target_armor_class + ' + COVER),
                    _set(ARMOR, '_effective_target_armor_class')])]
                sites += 1
        if sites != 1:
            raise ValueError('Keep the single native effective Armor Class call: ' + name)
        changed.add(name)
    for name in CARD_OWNERS:
        tools[name]['metadata']['result_card']['summary'] += [
            {'label': 'Cover · AC bonus', 'optional': True, 'omit_zero': True,
             'value': {'path': 'context.' + COVER}},
            {'label': 'Target AC', 'optional': True, 'value': {'path': 'context.' + ARMOR}}]
        changed.add(name)
    providers = {'complete_srd_creature_attack', 'finish_srd_creature_attack_hit_packet',
                 'resume_srd_creature_attack_hit'}
    while True:
        inherited = set()
        for name, tool in tools.items():
            for rows in _lists(tool['effects']):
                for effect in rows:
                    if effect.get('call') not in providers:
                        continue
                    exports = effect.get('export_context')
                    if exports == '*':
                        inherited.add(name)
                    elif isinstance(exports, dict) and any(
                            exports.get(field) == field for field in ('_attack_roll', '_damage_roll')):
                        exports[COVER] = COVER
                        exports[ARMOR] = ARMOR
                        inherited.add(name)
        new = inherited - providers
        changed.update(inherited)
        if not new:
            break
        providers.update(new)
    tools['complete_srd_pending_action']['effects'].append(_when(
        ARMOR + ' != null and _pending_card_summary != null', [
            _when(COVER + ' > 0', [{'list_push': {'list': '_pending_card_summary',
                'value': {'label': "'Cover · AC bonus'", 'value': COVER}}}]),
            {'list_push': {'list': '_pending_card_summary',
                'value': {'label': "'Target AC'", 'value': ARMOR}}}]))
    changed.add('complete_srd_pending_action')
    return sorted(changed)
