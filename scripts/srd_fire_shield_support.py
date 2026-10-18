"""Scoped guards and actual Fire Shield receipt forwarding for original SRD.

Reuse the retained cast, resistance, dice, damage, source-clock and lighting
owners. Repair earned single-class Pact recovery through the original rest
consumer. No repertoire, resource table, damage expression or UI changes.
"""

FIELDS = {'_fire_shield_damage_roll': None, '_fire_shield_damage_applied': 0,
          '_fire_shield_damage_type': None}
FORWARDERS = ('finish_srd_character_attack_hit_packet',
              'finish_srd_creature_attack_hit_packet',
              'complete_srd_character_attack', 'complete_srd_creature_attack',
              'complete_srd_pending_action')


def _nodes(value):
    if isinstance(value, dict):
        yield value
        for child in tuple(value.values()):
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in tuple(value):
            yield from _nodes(child)


def _bind_pact_rest_support(tools):
    """Qualify an optional actual Character File, never a caller's slot maxima.

    The original entity schema uses shared spell_slot_N keys. A single-class
    Warlock has an unambiguous Pact pool. Multiclass separate-pool ownership is
    not represented here, so this repair does not restore its ordinary slots.
    """
    changed = set()
    matching = (
        "target_character == null or (target_character.validation_status == 'validated' "
        "and target_character.participant_id == _target_state_id "
        "and target_character.kind == 'player_character' "
        "and target_character.package_id == 'sylvia.srd-lite.bg3')"
    )
    for name in ('complete_srd_rest', 'recover_srd_rest_resources'):
        tool = tools[name]
        if tool.setdefault('metadata', {}).get('srd_single_class_pact_rest_v1'):
            continue
        # Reuse the real optional participant declaration; omitted callers keep
        # the exact original recovery policy, including anonymous NPC rests.
        tool['parameters'].append('param_target_character_115ee703de5226a5')
        tool.setdefault('pre_conditions', []).append({
            'condition': matching,
            'error': "Rest recovery needs this creature's own validated Character File."})
        if name == 'complete_srd_rest':
            calls = [node for node in _nodes(tool['effects'])
                     if node.get('call') == 'recover_srd_rest_resources']
            if len(calls) != 1:
                raise ValueError('The original completed-rest recovery owner changed.')
            calls[0]['params']['target_character'] = '{_target_character_id}'
        else:
            gates = [node for node in _nodes(tool['effects'])
                     if node.get('condition') == '_dream_rest_denied != true']
            if len(gates) != 1:
                raise ValueError('The actual rest-benefit gate changed.')
            owner = gates[0]['on_true']
            qualify = (
                "kind == 'short' and target_character != null "
                "and len(target_character.data.classes) == 1 "
                "and target_character.data.classes[0].id == 'warlock' "
                "and target_character.data.classes[0].level >= 1 "
                "and target_character.data.classes[0].level <= 20"
            )
            level = 'target_character.data.classes[0].level'
            actual = [{'set': {'field': '_rest_pact_resource', 'value': "'spell_slot_1'"}}]
            for rank, threshold in ((2, 3), (3, 5), (4, 7), (5, 9)):
                actual.append({'set': {'field': '_rest_pact_resource',
                                      'value': repr('spell_slot_' + str(rank))},
                               'condition': level + ' >= ' + str(threshold)})
            actual.append({'set': {'field': '_rest_pact_maximum', 'value': 1}})
            for maximum, threshold in ((2, 2), (3, 11), (4, 17)):
                actual.append({'set': {'field': '_rest_pact_maximum', 'value': maximum},
                               'condition': level + ' >= ' + str(threshold)})
            actual.append({'require': {
                'condition': 'target_state.resources != null and target_state.resource_maxima != null '
                    'and _rest_pact_resource in target_state.resources '
                    'and _rest_pact_resource in target_state.resource_maxima '
                    'and target_state.resource_maxima[_rest_pact_resource] == _rest_pact_maximum '
                    'and target_state.resources[_rest_pact_resource] >= 0 '
                    'and target_state.resources[_rest_pact_resource] <= _rest_pact_maximum',
                'error': 'Pact recovery requires the actual earned class-table slot pool.'}})
            owner[:0] = [{'set': {'field': '_rest_pact_resource', 'value': "''"}},
                         {'condition': qualify, 'on_true': actual}]
            rows = [node for node in _nodes(owner)
                    if node.get('set', {}).get('field') == '_restore_amount'
                    and isinstance(node.get('condition'), str)
                    and node['condition'].startswith("kind == 'long' or ")]
            if len(rows) != 1:
                raise ValueError('The original full/short rest resource policy changed.')
            rows[0]['condition'] += (
                " or (kind == 'short' and _resource == _rest_pact_resource)"
            )
        tool['metadata']['srd_single_class_pact_rest_v1'] = True
        changed.add(name)
    return changed


def bind_srd_fire_shield_support(runtime):
    """Return changed tool names; caller alone writes/builds its private candidate."""
    tools = runtime['tools']
    changed = set()
    self_condition = (
        "spell != 'fire_shield' or (_target_id == _caster_state_id and "
        "(targets == null or len(targets) == 0 or "
        "(len(targets) == 1 and targets[0] == _caster_state_id)))"
    )
    mode_condition = "spell != 'fire_shield' or actor_spell_choice == 'warm' or actor_spell_choice == 'chill'"
    for name in ('cast_spell', 'cast_spell_outside_combat', 'begin_srd_spell_action'):
        rows = tools[name].setdefault('pre_conditions', [])
        for condition, error in (
            (self_condition, 'Fire Shield affects its actual caster only.'),
            (mode_condition, 'Choose a warm or chill Fire Shield.'),
        ):
            if not any(row.get('condition') == condition for row in rows):
                rows.append({'condition': condition, 'error': error})
                changed.add(name)
    resolver = tools['resolve_srd_fire_shield_hit']
    if not resolver.setdefault('metadata', {}).get('srd_fire_shield_receipts_v1'):
        # Roll/applied already initialize in the original producer.  Type must
        # also exist on genuine non-qualifying/miss paths before it can export.
        resolver['effects'].insert(0, {'set': {'field': '_fire_shield_damage_type', 'value': None}})
        resolver['metadata']['srd_fire_shield_receipts_v1'] = True
        changed.add('resolve_srd_fire_shield_hit')
    for name in FORWARDERS:
        tool = tools[name]
        if tool.setdefault('metadata', {}).get('srd_fire_shield_receipts_v1'):
            continue
        # Every callback has fresh private variables; initialize each actual
        # producer/forwarder, including ordinary attacks with no shield.
        tool['effects'][:0] = [{'set': {'field': field, 'value': value}}
                              for field, value in FIELDS.items()]
        for node in tuple(_nodes(tool['effects'])):
            if node.get('call') in ('resolve_srd_fire_shield_hit', *FORWARDERS):
                exports = node.setdefault('export_context', {})
                if exports == '*':
                    continue  # Original wildcard already forwards actual fields.
                if not isinstance(exports, dict):
                    raise TypeError('Unsupported actual callback export shape: ' + name)
                exports.update({field: field for field in FIELDS})
        tool['metadata']['srd_fire_shield_receipts_v1'] = True
        changed.add(name)
    changed.update(_bind_pact_rest_support(tools))
    return sorted(changed)
