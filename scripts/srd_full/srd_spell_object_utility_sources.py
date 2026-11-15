"""Bind finite object forces to the existing committed-cast identity and cleanup."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


INSTALLERS = {"cast_srd_mage_hand": "mage_hand", "cast_srd_floating_disk": "floating_disk"}


def _identity(spell):
    """Legacy private installs stay unqualified; a supplied identity must be allocated."""
    return [
        _set(
            "_utility_common_stamp",
            {"cast_id": "''", "source_caster_id": None, "ongoing_duration": False},
        ),
        _when(
            "spell_cast_id != ''",
            [
                _require(
                    "spell_cast_owner != null and _spell_cast_owner_id == _caster_state_id "
                    "and caster_state.last_spell_source_cast_id == spell_cast_id "
                    "and caster_state.last_spell_source_spell == " + repr(spell) + " "
                    "and actual_spell_level == slot_level and actual_spell_level >= 0",
                    "The object force needs its actual allocated caster, spell and slot identity.",
                ),
                _set(
                    "_utility_common_stamp",
                    {
                        "cast_id": "spell_cast_id",
                        "source_caster_id": "_caster_state_id",
                        "ongoing_duration": True,
                    },
                ),
            ],
        ),
    ]


def _bind_stamps(effects, *, cargo=False):
    """Merge whole objects: context writes are shallow and scalar nulls are opaque."""
    result = []
    for effect in effects:
        if "set" in effect and any(
            marker in str(effect)
            for marker in (
                "_utility_common_stamp",
                "_utility_carrier_stamp",
                "_utility_bound_stamp",
                "_utility_bound_metadata",
            )
        ):
            continue
        if effect.get("condition") == "spell_cast_id != ''" and (
            "_utility_common_stamp" in str(effect)
        ):
            continue
        if "_utility_carrier_stamp" in str(effect) and (
            effect.get("condition")
            == (
                "_utility_record.cast_id != null and _utility_record.cast_id != '' "
                "and _utility_record.source_caster_id != null"
            )
        ):
            continue
        for branch in ("on_true", "on_false"):
            if branch in effect:
                effect[branch] = _bind_stamps(effect[branch], cargo=cargo)
        result.append(effect)
        assignment = effect.get("set", {})
        field, value = assignment.get("field"), assignment.get("value")
        if not cargo and field == "_utility_record" and isinstance(value, dict):
            for key in ("cast_id", "source_caster_id", "ongoing_duration"):
                value.pop(key, None)
            result.append(_set(field, "merge(_utility_record, _utility_common_stamp)"))
        marker = "srd_spell_carrier" if cargo else "srd_object_force"
        if isinstance(value, dict) and isinstance(value.get(marker), dict):
            if cargo:
                result += [
                    _set(
                        "_utility_carrier_stamp",
                        {
                            "cast_id": "''",
                            "source_caster_id": None,
                            "ongoing_duration": False,
                        },
                    ),
                    _when(
                        "_utility_record.cast_id != null and _utility_record.cast_id != '' "
                        "and _utility_record.source_caster_id != null",
                        [
                            _set(
                                "_utility_carrier_stamp",
                                {
                                    "cast_id": "_utility_record.cast_id",
                                    "source_caster_id": "_utility_record.source_caster_id",
                                    "ongoing_duration": "_utility_record.ongoing_duration",
                                },
                            )
                        ],
                    ),
                ]
            stamp = "_utility_carrier_stamp" if cargo else "_utility_common_stamp"
            result += [
                _set("_utility_bound_stamp", "merge(" + field + "." + marker + ", " + stamp + ")"),
                _set("_utility_bound_metadata", {marker: "_utility_bound_stamp"}),
                _set(field, "merge(" + field + ", _utility_bound_metadata)"),
            ]
    return result


def _collect():
    """Select genuine finite forces before the shared dispatcher invokes exact retirement."""
    return _tool(
        "Collect only this caster's actual live Hand or Disk sources for one allocated cast.",
        [_entity("caster_state"), _lasting(), _param("cast_id", required=False, default="")],
        [
            _set("_utility_dismiss_sources", []),
            _set("_utility_dismiss_effects", []),
            _set("_utility_dismiss_now", 0),
            _when(
                "lasting_state.lasting_tick != null",
                [_set("_utility_dismiss_now", "lasting_state.lasting_tick")],
            ),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_utility_dismiss_effects", "lasting_state.turn_effects")],
            ),
            _each(
                "_utility_dismiss_effects",
                "_utility_dismiss_candidate",
                [
                    _when(
                        "_utility_dismiss_candidate.kind == 'object_utility_spell' "
                        "and (_utility_dismiss_candidate.spell == 'mage_hand' or "
                        "_utility_dismiss_candidate.spell == 'floating_disk')",
                        [
                            _when(
                                "cast_id != '' and _utility_dismiss_candidate.cast_id == cast_id "
                                "and _utility_dismiss_candidate.source_caster_id "
                                "== _caster_state_id "
                                "and _utility_dismiss_candidate.caster == _caster_state_id "
                                "and _utility_dismiss_candidate.ongoing_duration == true "
                                "and _utility_dismiss_candidate.active == true "
                                "and _utility_dismiss_candidate.expires_tick != null "
                                "and _utility_dismiss_now < "
                                "_utility_dismiss_candidate.expires_tick",
                                [
                                    _call(
                                        "read_srd_object_utility_source",
                                        {
                                            "object_id": "{_utility_dismiss_candidate.force_id}",
                                            "source_id": "{_utility_dismiss_candidate.source_id}",
                                        },
                                        ("_object_utility_source",),
                                    ),
                                    _push("_utility_dismiss_sources", "_object_utility_source"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def bind_source_consumers(text):
    """Run after the shared producer and actual Hand/Disk family composition."""
    tools = _load_runtime(text)["tools"]
    changed = {"collect_srd_object_utility_dismiss_sources": _collect()}
    for name, spell in INSTALLERS.items():
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        for parameter in (
            _param("spell_cast_id", required=False, default="", model_hidden=True),
            _param(
                "spell_cast_owner",
                resolve="entity",
                required=False,
                default=None,
                model_hidden=True,
            ),
            _param(
                "actual_spell_level",
                "integer",
                required=False,
                default=-1,
                minimum=-1,
                maximum=9,
                model_hidden=True,
            ),
        ):
            if not any(p["name"] == parameter["name"] for p in tool["parameters"]):
                tool["parameters"].append(parameter)
        tool["effects"] = _bind_stamps(tool["effects"])
        tool["effects"][:0] = _identity(spell)
        changed[name] = tool
    for name in ("operate_srd_mage_hand", "load_srd_floating_disk"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        tool["effects"] = _bind_stamps(tool["effects"], cargo=True)
        changed[name] = tool
    return _replace_tools(text, changed)
