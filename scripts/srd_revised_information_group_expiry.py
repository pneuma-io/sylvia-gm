"""Expire only the current saved Arcane Eye after real group-clock updates."""
from copy import deepcopy
from .srd_control_behaviors import _lasting
from .srd_timed_actor_defenses import _call, _set, _tool, _when
from .srd_timed_senses import _loop
from .srd_revised_information_expiry import HELPER, bind_revised_srd_information_expiry

GROUP_HELPER = "refresh_revised_srd_arcane_eye_group_expiry"
CLOCK_VALUE = ("combat.lasting_epoch_tick + 2 * (combat.round - combat.lasting_epoch_round) "
    "+ 2 * combat.turn_index * (len(combat.order) ** -1)")


def group_expiry_owner():
    return _tool("End only a current saved Arcane Eye at its group-clock deadline.", [_lasting()], [
        _set("_group_eye_records", []),
        _when("lasting_state.has_information_spells == true and lasting_state.turn_effects != null", [
            _set("_group_eye_records", "lasting_state.turn_effects"),
        ]),
        _loop("_group_eye_records", "_group_eye_record", [
            _when("_group_eye_record.kind == 'information_spell' and "
                "_group_eye_record.spell == 'arcane_eye' and "
                "_group_eye_record.concentration == true and "
                "lasting_state.lasting_tick >= _group_eye_record.expires_tick", [
                _call("read_srd_information_instance", {"record": "{_group_eye_record}"}, ("_info_instance_id",)),
                # Re-join the exact live saved instance and its current header in
                # the sealed owner. Its canonical end callback performs original
                # source/proxy removal. A stale or replaced header cannot remove
                # the newer proxy by an unconditional duplicate cleanup call.
                _call(HELPER, {"caster_state": "{_group_eye_record.caster}",
                    "instance_id": "{_info_instance_id}", "started_tick": "{_group_eye_record.started_tick}",
                    "expires_tick": "{_group_eye_record.expires_tick}", "lasting_state": "{_lasting_state_id}"}),
            ]),
        ]),
    ])


def bind_revised_srd_information_group_expiry(runtime):
    """Append one scoped callback, preserving the full real group-clock prefix."""
    bind_revised_srd_information_expiry(runtime)
    tools = runtime["tools"]
    assert "sync_srd_group_lasting_clock" in tools, "Retain the real group clock."
    clock = deepcopy(tools["sync_srd_group_lasting_clock"])
    assert any(p["name"] == "lasting_state" and p.get("resolve") == "entity" for p in clock["parameters"])
    writes = [e for e in clock["effects"] if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
        and e["set"]["value"] == CLOCK_VALUE]
    assert len(writes) == 1, "Review the actual fractional Initiative clock seam."
    expected = group_expiry_owner()
    if GROUP_HELPER in tools:
        assert tools[GROUP_HELPER] == expected, "An incompatible Eye group expiry owner already exists."
    else:
        tools[GROUP_HELPER] = expected
    hook = _call(GROUP_HELPER, {"lasting_state": "{_lasting_state_id}"})
    existing = [e for e in clock["effects"] if e.get("call") == GROUP_HELPER]
    if existing:
        assert existing == [hook] and clock["effects"][-1] == hook
    else:
        clock["effects"].append(hook)
    tools["sync_srd_group_lasting_clock"] = clock
    return (GROUP_HELPER, "sync_srd_group_lasting_clock")
