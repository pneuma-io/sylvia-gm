"""Show actual committed Web ignition on the existing Web and Wall of Fire cast cards."""
from .srd_spell_environment import _areas, _push, _set, _when
from .srd_retained_web_fire_v2 import _cells


def _feedback_effects():
    """Count current burning cells attributable to this actual cast without changing their lifecycle."""
    return [
        _set('_web_feedback_source', 'state.persistent_spell_areas[caster_state.environment_area_id]'),
        _set('_web_feedback_count', 0),
        *_areas([_when("_environment_area.mode == 'web' and _environment_area.web_cell_schema == 1 and "
            "(_web_feedback_source.mode == 'wall_fire' or _environment_id == caster_state.environment_area_id)",
            _cells('_environment_area', [_when("_web_cell.status == 'burning' and "
                "(_web_feedback_source.mode == 'web' or (_web_cell.fire_area_id == caster_state.environment_area_id "
                "and _web_cell.fire_sequence == _web_feedback_source.sequence))", [
                    _set('_web_feedback_count', '_web_feedback_count + 1')])]))]),
        _when('_web_feedback_count > 0', [
            _push('_spell_card_summary', {'label': "'Web cells ignited'", 'value': '_web_feedback_count'}),
            _push('_spell_detail_rows', {'label': "'Web burning'",
                'value': "'Each exposed 5-foot cube burns away after 1 round.'"})])]


def bind_web_fire_cast_feedback(runtime):
    """Append derived feedback at the existing final card owner after the paid casting effects."""
    tail = runtime['tools']['finish_srd_retained_cast_tail']
    if '_web_feedback_count' in str(tail['effects']):
        raise ValueError('Web ignition feedback must be composed exactly once.')
    tail['effects'].append(_when(
        "(_retained_cast_tail_choices.spell == 'web' or _retained_cast_tail_choices.spell == 'wall_of_fire') and "
        "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
        "state.persistent_spell_areas[caster_state.environment_area_id].active == true", _feedback_effects()))
    return ['finish_srd_retained_cast_tail']
