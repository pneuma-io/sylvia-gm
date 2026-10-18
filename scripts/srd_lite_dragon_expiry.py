"""Reconcile the exact naturally expired Dragon source after refresh pruning."""
from copy import deepcopy
import hashlib
import json

OWNER = 'refresh_srd_dragon_breath'
HELPER = 'expire_srd_dragon_breath_concentration'
ORIGINAL_REFRESH_SHA256 = 'b2ddf4bfa174e8f998e6be8bbcb053714a9ec1d0a500e3d01a03d5c3db173285'
PROJECTION_FIELDS = (
    'concentration_spell_id', 'concentration_effect_family',
    'concentration_target_state_id', 'concentration_target_state_ids',
    'concentration_started_tick', 'concentration_expires_tick',
    'concentration_save_modifier',
)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def _when(condition, effects):
    return {'condition': condition, 'on_true': effects}


def projection_cleanup(runtime):
    """Reuse only the established null setters for Dragon-installed projections.

    Calling the complete end owner here would add a static call cycle through
    retire_srd_dragon_breath_source. Other families' removal is already separate.
    Historical cast/source IDs are intentionally retained, as in the end owner.
    """
    cleanup = []
    for name in PROJECTION_FIELDS:
        wanted = {'set': {'field': 'source_state.' + name, 'value': None}}
        matches = [effect for effect in runtime['tools']['end_srd_concentration_node']['effects']
                   if effect.get('set', {}).get('field') == wanted['set']['field']]
        assert matches == [wanted], 'Established concentration projection cleanup drift.'
        cleanup.append(deepcopy(matches[0]))
    return cleanup


def expiry_helper(cleanup):
    """Resolve the actual caster entity, then match its entire source identity."""
    return {
        'description': 'Reconcile concentration for this exact naturally expired Dragon source.',
        'parameters': [
            {'name': 'source_state', 'type': 'string', 'resolve': 'entity', 'required': True},
            {'name': 'source_id', 'type': 'string', 'required': True},
            {'name': 'created_tick', 'type': 'number', 'required': True},
            {'name': 'target_state', 'type': 'string', 'resolve': 'entity', 'required': True},
            {'name': 'expires_tick', 'type': 'number', 'required': True},
            {'name': 'lasting_state', 'type': 'string', 'resolve': 'entity', 'required': True},
        ],
        'pre_conditions': [],
        'effects': [_when(
            "source_id != null and created_tick != null and _target_state_id != null and "
            "expires_tick != null and lasting_state.lasting_tick != null and "
            "lasting_state.lasting_tick >= expires_tick",
            [_when(
                "source_state.concentration_spell_id == 'dragons_breath' and "
                "source_state.concentration_effect_family == 'dragon_breath'",
                [_when(
                    "source_state.concentration_cast_id == source_id and "
                    "source_state.dragon_breath_source_id == source_id and "
                    "source_state.concentration_started_tick == created_tick and "
                    "source_state.concentration_expires_tick == expires_tick and "
                    "source_state.concentration_target_state_id == _target_state_id",
                    deepcopy(cleanup),
                )],
            )],
        )],
        'metadata': {'category': 'internal', 'visible_to_player': False, 'costs_action': False},
    }


def post_pruning_effect():
    # This is appended AFTER both retained writes. The loop still reads the old
    # local snapshot, while retire -> refresh sees only the already-pruned list.
    return {'for_each': {
        'collection': '_breath_records', 'as': '_expired_breath_record',
        'do': [_when(
            "_expired_breath_record.kind == 'dragon_breath' and "
            "_expired_breath_record.spell == 'dragons_breath' and "
            "_expired_breath_record.active == true and "
            "_expired_breath_record.expires_tick != null and "
            "lasting_state.lasting_tick != null and "
            "lasting_state.lasting_tick >= _expired_breath_record.expires_tick",
            [{'call': HELPER, 'params': {
                'source_state': '{_expired_breath_record.caster}',
                'source_id': '{_expired_breath_record.source_id}',
                'created_tick': '{_expired_breath_record.created_tick}',
                'target_state': '{_expired_breath_record.target}',
                'expires_tick': '{_expired_breath_record.expires_tick}',
                'lasting_state': '{_lasting_state_id}',
            }}],
        )],
    }}


def bind_dragon_natural_expiry_cleanup(runtime):
    """Append one guarded reconciliation pass; preserve the first seven effects."""
    tools = runtime['tools']
    owner = deepcopy(tools[OWNER])
    appended = post_pruning_effect()
    restored = deepcopy(owner)
    if len(owner['effects']) == 8 and owner['effects'][-1] == appended:
        restored['effects'].pop()
    else:
        assert len(owner['effects']) == 7, 'Partial or different Dragon expiry repair.'
        owner['effects'].append(appended)
    assert _digest(restored) == ORIGINAL_REFRESH_SHA256, 'Retained Dragon refresh drift.'
    assert restored['effects'][-2:] == [
        {'set': {'field': 'lasting_state.turn_effects', 'value': '_breath_kept'}},
        {'set': {'field': 'lasting_state.has_dragon_breath', 'value': '_breath_any'}},
    ], 'Cleanup must follow the actual pruning writes.'
    helper = expiry_helper(projection_cleanup(runtime))
    assert HELPER not in tools or tools[HELPER] == helper, 'Existing helper identity conflict.'
    tools[OWNER] = owner
    tools[HELPER] = helper
    return {'owner': OWNER, 'internal_helper': HELPER,
        'original_owner_sha256': ORIGINAL_REFRESH_SHA256,
        'owner_sha256': _digest(owner), 'helper_sha256': _digest(helper),
        'public_operations_added': [], 'native_verified': False}
