"""Preserve original SRD Fly/Alter Self; refresh Fly's real paid saved source.

Compose with or without the deferred ER binder. No new public tool, profile, generic
caster clone, movement rule, landing restriction or roster decision is added.
"""
from copy import deepcopy

from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _call, _set, _when
from .srd_control_behaviors import _param, _entity, _lasting, _tool


FLY_SOURCE_GUARD = (
    "caster_state.concentration_spell_id == 'fly' and "
    "caster_state.concentration_effect_family == 'multi_target_timed_actor_mobility' and "
    "caster_state.concentration_cast_id == cast_id and "
    "caster_state.concentration_target_state_id != null and "
    "caster_state.concentration_target_state_ids != null"
)
FLY_ROW_GUARD = (
    "_effect.kind == 'actor_mobility' and _effect.spell == 'fly' and "
    "_effect.mobility == 'flight' and _effect.concentration == true and "
    "_effect.cast_id != null and _effect.cast_id != '' and "
    "_effect.source_caster_id != null and _effect.source_caster_id != '' and "
    "_effect.caster == _effect.source_caster_id"
)


def bind_revised_fly_alterself(runtime, record=None):
    """Append only Fly validity checks to the existing exact ER lifecycle seam."""
    fly = runtime["definitions"]["spells"]["fly"]
    assert fly["duration_rounds"] == 100 and fly["concentration"] is True
    assert fly["casting_time"] == "action" and fly["range_kind"] == "touch"
    assert fly["mobility"] == "flight" and fly["flying_speed"] == 60
    assert fly["base_targets"] == 1 and fly["targets_per_slot_level"] == 1
    tools = runtime["tools"]
    fly_refresh = _call("refresh_srd_concentration_source", {
        "source_state": "{_caster_state_id}",
        "target_state": "{caster_state.concentration_target_state_id}",
        "lasting_state": "{_lasting_state_id}",
    })
    fly_refresh["export_context"] = {"_revised_er_source_active": "_source_concentration_active"}
    # The original ER self branch remains exact. Fly instead refreshes the
    # actual saved primary recipient. The existing refresh owner validates
    # membership, including the final empty list after an actual Dispel.
    branch = _when(FLY_SOURCE_GUARD, [fly_refresh])
    if "refresh_srd_revised_expeditious_row" in tools:
        helper_name = "refresh_srd_revised_expeditious_row"
        refresh = tools[helper_name]
        assert len(refresh["effects"]) == 2, "Extend the actual ER helper once."
        assert "caster_state.concentration_spell_id == 'expeditious_retreat'" in refresh["effects"][1]["condition"]
        refresh["effects"].append(branch)
    else:
        helper_name = "refresh_srd_revised_fly_row"
        assert helper_name not in tools, "Bind this scoped extension only once."
        tools[helper_name] = _tool(
            "Refresh the actual paid Fly concentration source before retaining its saved mobility row.",
            [_entity("caster_state"), _lasting(), _param("cast_id")],
            [_set("_revised_er_source_active", False), branch],
        )
    hook = _when(FLY_ROW_GUARD, [
        _call(helper_name, {
            "caster_state": "{_effect.source_caster_id}",
            "lasting_state": "{_lasting_state_id}",
            "cast_id": "{_effect.cast_id}",
        }, ("_revised_er_source_active",)),
        _set("_effect_due", "_effect_due or _revised_er_source_active != true"),
    ])
    hooks = 0
    for sequence in list(lists(tools["process_srd_turn_effects"]["effects"])):
        for index, effect in enumerate(list(sequence)):
            if effect.get("list_push") == {"list": "_kept_turn_effects", "value": "_effect"} and effect.get("condition") == "_effect_due == false":
                sequence[index:index] = [deepcopy(hook)]
                hooks += 1
    assert hooks == 1, "Refresh before the actual saved row retention owner."
    # Alter Self retains its original transformation acquisition, action,
    # options, stats, respiration, natural-weapon and restoration consumers.
    return {"profiles": ["fly", "alter_self"], "new_tools": int(helper_name == "refresh_srd_revised_fly_row"), "native_verified": False}
