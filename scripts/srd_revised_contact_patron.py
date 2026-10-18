"""Narrow SRD 5.2.1 Contact Patron repair; apply to decoded original source.

The saved flag is produced only after validate_srd_spell_grants and the actual
feature-use check. It follows the existing retained-tail source context. No new
public tool or caller-provided autosave flag is exposed.
"""
from copy import deepcopy
import hashlib
import json
from sylvia_gm.domain.package_library import participant_schema_sha256

FREE_USES = [0] * 8 + [1] * 12
SELECTOR = {
    'name': 'divination_contact_target', 'type': 'enum',
    'enum_values': ['other_entity', 'own_patron'],
    'required': False, 'default': 'other_entity',
}
SELECTOR_SCHEMA = {
    'type': 'string', 'enum': ['other_entity', 'own_patron'],
}
FREE_PARAMETER = {
    'name': 'contact_patron_free_cast', 'type': 'boolean',
    'required': False, 'default': False,
}
ENTRY = {'cast_spell', 'cast_spell_outside_combat',
         'begin_srd_spell_cast', 'begin_srd_spell_cast_internal'}
GUARD = {
    'require': {
        'condition': "spell != 'contact_other_plane' or slot_resource != 'feature' or spell_grant != 'contact_patron' or divination_contact_target == 'own_patron'",
        'error': 'Contact Patron grants a free cast only when contacting your own patron.',
    }
}


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _patch_policy(policies):
    p = policies['contact_patron']
    assert p['class_id'] == 'warlock' and p['minimum'] == 9
    assert p['fixed'] == {'contact_other_plane': 9}
    assert p['free_pool'] == 'contact_patron' and p['slots_allowed'] is True
    assert p['free_uses'] in ([0] * 20, FREE_USES), p['free_uses']
    p['free_uses'] = FREE_USES.copy()


def repair_contact_patron_artifact(record):
    """Update the actual earned-source policy used by creation and validation.

    JSON participant/entity schemas remain identical. The real identity includes
    calculations and validation policy, so recompute it through the actual owner.
    The package writer must reseal the modified mechanical manifest as usual.
    """
    artifact = next(a for a in record['participant_artifacts']
                    if a['kind'] == 'player_character')
    _patch_policy(artifact['initial_state_validation']['definitions']['spell_grants'])
    artifact['schema_sha256']=participant_schema_sha256(artifact['schema'],
        entity_state_schema=artifact.get('entity_state_schema'),
        initial_state_calculations=artifact.get('initial_state_calculations',()),
        initial_state_validation=artifact.get('initial_state_validation'))
    return artifact


def repair_contact_patron(runtime, record):
    """Mutate only owned casting seams, the exact policy row and input schemas.

    Input must already have resolved parameter declarations. Retain the original
    source's casting, concentration, costs, question and damage consumers.
    """
    _patch_policy(runtime['definitions']['spell_grants'])
    repair_contact_patron_artifact(record)
    tools = runtime['tools']
    family = tools['cast_spell_divination_spell']
    assert all(isinstance(p, dict) for p in family['parameters']), 'Decode declarations first.'
    if any(p['name'] == FREE_PARAMETER['name'] for p in family['parameters']):
        return {'already_applied': True}

    owned = ENTRY | {'cast_spell_divination_spell', 'finish_srd_retained_cast_tail',
                     'complete_srd_spell_attack_consequences'}
    for name in owned:
        tools[name] = deepcopy(tools[name])
    for name in ENTRY:
        assert not any(p['name'] == SELECTOR['name'] for p in tools[name]['parameters'])
        tools[name]['parameters'].append(deepcopy(SELECTOR))
        for node in _walk(tools[name]['effects']):
            if node.get('call') in ENTRY:
                node.setdefault('params', {})['divination_contact_target'] = '{divination_contact_target}'
    for name in ('cast_spell', 'begin_srd_spell_cast_internal'):
        tools[name]['effects'].insert(0, deepcopy(GUARD))

    # The authoritative cast derives this flag inside the successfully validated
    # feature branch, before saved tail production. The beginning owner never
    # spends a feature or invents a benefit merely by accepting a request.
    initialized = derived = 0
    def bind_flag(nodes):
        nonlocal initialized, derived
        for node in list(nodes):
            if not isinstance(node, dict):
                continue
            s = node.get('set', {})
            if s.get('field') == '_feature_cast':
                at = nodes.index(node) + 1
                if s['value'] is False:
                    nodes.insert(at, {'set': {'field': '_contact_patron_free_cast', 'value': False}})
                    initialized += 1
                elif s['value'] is True:
                    nodes.insert(at, {'set': {'field': '_contact_patron_free_cast',
                        'value': "spell == 'contact_other_plane' and spell_grant == 'contact_patron' and divination_contact_target == 'own_patron'"}})
                    derived += 1
            for child in node.values():
                if isinstance(child, list):
                    bind_flag(child)
                elif isinstance(child, dict):
                    for sub in child.values():
                        if isinstance(sub, list):
                            bind_flag(sub)
    bind_flag(tools['cast_spell']['effects'])
    assert initialized == 1 and derived == 1, (initialized, derived)

    context_rows = choice_rows = 0
    for name in ('cast_spell', 'complete_srd_spell_attack_consequences'):
        for node in _walk(tools[name]['effects']):
            setting = node.get('set', {})
            value = setting.get('value')
            if setting.get('field') in ('_retained_cast_tail_context', '_retained_spell_attack_context') and isinstance(value, list):
                if any(isinstance(r, dict) and r.get('key') == "'_feature_cast'" for r in value):
                    value.append({'key': "'_contact_patron_free_cast'",
                        'missing': '_contact_patron_free_cast == null',
                        'value': '_contact_patron_free_cast'})
                    context_rows += 1
            # Both retained spell-attack and ordinary tail source snapshots use
            # the same real missing/value protocol. Preserve the flag through
            # any differently named producer rather than reconstructing it.
            elif isinstance(value, list) and any(isinstance(r, dict) and r.get('key') == "'_feature_cast'" for r in value):
                value.append({'key': "'_contact_patron_free_cast'",
                    'missing': '_contact_patron_free_cast == null',
                    'value': '_contact_patron_free_cast'})
                context_rows += 1
            if setting.get('field') == '_retained_cast_tail_choices' and isinstance(value, list):
                assert len(value) == 1 and isinstance(value[0], dict)
                assert 'divination_subject' in value[0] and 'spell' in value[0]
                value[0]['contact_patron_free_cast'] = '_contact_patron_free_cast == true'
                choice_rows += 1
    assert context_rows >= 2 and choice_rows == 2, (context_rows, choice_rows)

    family = tools['cast_spell_divination_spell']
    family['parameters'].append(deepcopy(FREE_PARAMETER))
    saves = calls = 0
    for node in _walk(family['effects']):
        if node.get('condition') == "spell == 'contact_other_plane'":
            branch = node['on_true']
            actual = next(n for n in branch if n.get('call') == 'resolve_srd_divination_save')
            assert actual['params']['ability'] == 'intelligence' and actual['params']['dc'] == 15
            actual['condition'] = 'contact_patron_free_cast != true'
            branch.insert(0, {'set': {'field': '_divination_save_success', 'value': 'contact_patron_free_cast == true'}})
            saves += 1
    for node in _walk(tools['finish_srd_retained_cast_tail']['effects']):
        if node.get('call') == 'cast_spell_divination_spell':
            assert node['params']['divination_subject'] == '{_retained_cast_tail_choices.divination_subject}'
            node['params']['contact_patron_free_cast'] = '{_retained_cast_tail_choices.contact_patron_free_cast}'
            calls += 1
    assert saves == 1 and calls == 1, (saves, calls)
    public = []
    for declaration in record['tools']:
        if declaration['name'] in ENTRY:
            assert set(declaration) == {'name', 'capability', 'input_schema', 'state_predicates', 'turn_statuses', 'writers'}
            declaration['input_schema']['properties']['divination_contact_target'] = deepcopy(SELECTOR_SCHEMA)
            public.append(declaration['name'])
    assert {'cast_spell', 'cast_spell_outside_combat', 'begin_srd_spell_cast'} <= set(public)
    return {'tools': sorted(owned), 'public_schemas': sorted(public),
        'source_context_rows': context_rows, 'tail_choice_rows': choice_rows,
        'source_specific_free_uses': FREE_USES,
        'participant_schema_sha256_resealed': next(a['schema_sha256'] for a in record['participant_artifacts'] if a['kind']=='player_character'),
        'native_acceptance': False, 'normal_expanded_node_delta': 'pending root normal build'}


def policy_identity(record):
    a = next(a for a in record['participant_artifacts'] if a['kind']=='player_character')
    p = a['initial_state_validation']['definitions']['spell_grants']['contact_patron']
    return hashlib.sha256(json.dumps(p, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
