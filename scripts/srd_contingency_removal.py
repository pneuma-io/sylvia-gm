"""Remove one authentic armed Contingency without retiring its released child spell."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _call, _emit, _when
    from .srd_contingency_spell import POOL, SPELL
    from .srd_control_behaviors import (
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
    )
    from .srd_spell_removal_adoption import _actual_path, _source_check
    from .srd_timed_actor_defenses import _load_runtime
else:
    from srd_actor_spell_modifiers import _call, _emit, _when
    from srd_contingency_spell import POOL, SPELL
    from srd_control_behaviors import (
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
    )
    from srd_spell_removal_adoption import _actual_path, _source_check
    from srd_timed_actor_defenses import _load_runtime


def _source_match():
    return (
        "_contingency_record != null and _contingency_record.spell == 'contingency' and "
        "_contingency_record.installed == true and _contingency_record.status == 'armed' and "
        "_contingency_record.cast_id == source_id and source_id != '' and "
        "_contingency_record.ongoing_duration == true"
    )


def _tools():
    qualify = _tool(
        "Read this body's exact armed Contingency, including expiry and actual carried material.",
        [_entity("target_state"), _param("source_id"), _lasting()],
        [
            _call(
                "check_srd_contingency_source",
                {"target_state": "{_target_state_id}"},
            ),
            _set("_contingency_record", None),
            _when(
                f"lasting_state.{POOL} != null",
                [
                    _set("_contingency_record", f"lasting_state.{POOL}[source_id]"),
                ],
            ),
            _require(_source_match(), "Select this body's actual armed Contingency source."),
            _require(
                "_contingency_record.source_caster_id == _target_state_id and "
                "_contingency_record.caster == _target_state_id and "
                "target_state.contingency_source_id == source_id",
                "The selected source must belong to this actual self caster.",
            ),
        ],
    )
    collector = _tool(
        "Collect only this caster's actual finite armed Contingency casting.",
        [_entity("caster_state"), _param("cast_id"), _lasting()],
        [
            _set("_contingency_dismiss_ids", []),
            _when(
                "caster_state.has_contingency == true and "
                "caster_state.contingency_source_id == cast_id",
                [
                    _call(
                        "qualify_srd_contingency_removal_source",
                        {"target_state": "{_caster_state_id}", "source_id": "{cast_id}"},
                        ("_contingency_record",),
                    ),
                    _push("_contingency_dismiss_ids", "cast_id"),
                ],
            ),
        ],
    )
    retire = _tool(
        "Dismiss exactly this caster's armed parent; released children retain their own lifetime.",
        [_entity("caster_state"), _param("cast_id"), _param("source_id"), _lasting()],
        [
            _require("source_id == cast_id", "Select the actual owned cast reference."),
            _call(
                "qualify_srd_contingency_removal_source",
                {"target_state": "{_caster_state_id}", "source_id": "{source_id}"},
            ),
            _call(
                "end_srd_contingency_source", {"source_id": "{source_id}", "reason": "dismissed"}
            ),
        ],
    )
    dispel = _tool(
        "Dispel the armed source at its self caster's actual current body and spell level.",
        [
            _entity("caster_state"),
            _param("source_id"),
            _lasting(),
            _param("caster", resolve="participant", required=False),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
        ],
        [
            _set("_contingency_record", None),
            _when(
                f"lasting_state.{POOL} != null",
                [_set("_contingency_record", f"lasting_state.{POOL}[source_id]")],
            ),
            _require(
                "_contingency_record != null", "Select an actual retained Contingency source."
            ),
            _call(
                "qualify_srd_contingency_removal_source",
                {"target_state": "{_contingency_record.caster}", "source_id": "{source_id}"},
                ("_contingency_record",),
            ),
            _require(
                "_contingency_record.dispel_ongoing == true",
                "Select an actual ongoing Contingency spell effect.",
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_contingency_record.caster}"},
                ("_info_scene_entity", "_info_position"),
            ),
            _set("_contingency_dispel_point", "_info_position"),
            _set("_contingency_dispel_plane", "_info_scene_entity.metadata.plane"),
            *_actual_path("_contingency_dispel_point", "_contingency_dispel_plane"),
            *_source_check("_contingency_record"),
            _when(
                "_dispel_succeeded == true",
                [
                    _call(
                        "end_srd_contingency_source",
                        {"source_id": "{source_id}", "reason": "dispel"},
                    )
                ],
            ),
        ],
    )
    return {
        "qualify_srd_contingency_removal_source": qualify,
        "collect_srd_contingency_owned_cast": collector,
        "retire_srd_contingency_owned_cast": retire,
        "dispel_srd_contingency_owned_source": dispel,
    }


def _bind_dismissal(tool):
    tool["effects"] = [e for e in tool["effects"] if "_contingency_dismiss_ids" not in str(e)]
    index = next(i for i, e in enumerate(tool["effects"]) if "require" in e)
    tool["effects"][index:index] = [
        _call(
            "collect_srd_contingency_owned_cast",
            {"caster_state": "{_caster_state_id}", "cast_id": "{cast_id}"},
            ("_contingency_dismiss_ids",),
        ),
        _set("_dismiss_count", "_dismiss_count + len(_contingency_dismiss_ids)"),
    ]
    tool["effects"].append(
        _each(
            "_contingency_dismiss_ids",
            "_contingency_dismiss_id",
            [
                _call(
                    "retire_srd_contingency_owned_cast",
                    {
                        "caster_state": "{_caster_state_id}",
                        "cast_id": "{cast_id}",
                        "source_id": "{_contingency_dismiss_id}",
                    },
                )
            ],
        )
    )


def _bind_recovery(tool):
    fields = {"_recovery_source_intents", "_recovery_source_owners"}
    found = set()
    for effect in tool["effects"]:
        if effect.get("set", {}).get("field") in fields:
            found.add(effect["set"]["field"])
            if repr(SPELL) not in effect["set"]["value"]:
                effect["set"]["value"].append(repr(SPELL))
    if found != fields:
        raise ValueError("Contingency requires the genuine selected-source Recovery owner.")
    marker = "spell == 'dispel_magic' and recovery_choice == 'contingency'"
    tool["effects"] = [e for e in tool["effects"] if e.get("condition") != marker]
    tool["effects"].append(
        _when(
            marker,
            [
                _call(
                    "dispel_srd_contingency_owned_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "caster": "{_caster_id}",
                        "source_id": "{recovery_source_id}",
                        "slot_level": "{slot_level}",
                        "casting_modifier": "{casting_modifier}",
                        "casting_ability": "{casting_ability}",
                    },
                    ("_adoption_dispel_check",),
                ),
                _set("_dispel_checks", ["_adoption_dispel_check"]),
                _set("_contingency_dispel_detail", "'Automatic at this slot level'"),
                _when(
                    "_adoption_dispel_check.roll != null",
                    [
                        _call(
                            "format_srd_information_projection",
                            {
                                "message": "d20 {_adoption_dispel_check.roll.results}; selected "
                                "{_adoption_dispel_check.roll.total}; modifier "
                                "{_adoption_dispel_check.roll.modifier}; "
                                "DC {_adoption_dispel_check.dc}; effect ended: "
                                "{_adoption_dispel_check.ended}",
                            },
                            ("_info_projection_message",),
                        ),
                        _set("_contingency_dispel_detail", "_info_projection_message"),
                    ],
                ),
                _set(
                    "_spell_summary_rows",
                    [
                        {
                            "label": "'Selected Contingency ended'",
                            "value": "_adoption_dispel_check.ended",
                        }
                    ],
                ),
                _set(
                    "_spell_detail_rows",
                    [
                        {"label": "'Source'", "value": "recovery_source_id"},
                        {"label": "'Actual Dispel check'", "value": "_contingency_dispel_detail"},
                    ],
                ),
            ],
        )
    )


def _bind_target_dispel(tool):
    marker = "target_state.has_contingency == true"
    tool["effects"] = [
        e
        for e in tool["effects"]
        if not (e.get("condition") == marker and "dispel_srd_contingency_owned_source" in str(e))
    ]
    tool["effects"].append(
        _when(
            marker,
            [
                _call(
                    "dispel_srd_contingency_owned_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "caster": "{_caster_id}",
                        "source_id": "{target_state.contingency_source_id}",
                        "slot_level": "{slot_level}",
                        "casting_modifier": "{casting_modifier}",
                        "casting_ability": "{casting_ability}",
                    },
                    ("_adoption_dispel_check",),
                ),
                _push("_dispel_checks", "_adoption_dispel_check"),
            ],
        )
    )


def _bind_inventory_loss(tool):
    if any(e.get("call") == "check_srd_contingency_source" for e in _walk(tool["effects"])):
        return
    tool["effects"].append(
        _when(
            "target_state.has_contingency == true",
            [
                _call("check_srd_contingency_source", {"target_state": "{_target_state_id}"}),
            ],
        )
    )


def _bind_public_source_intent(tool):
    """Preserve the existing source-only cast route instead of inventing a creature target."""
    for effect in _walk(tool["effects"]):
        if effect.get("set", {}).get("field") == "_recovery_source_intents":
            choices = effect["set"]["value"]
            if repr(SPELL) not in choices:
                choices.append(repr(SPELL))


def bind_runtime(text):
    """Join the settled Source/removal and inventory owners before their final saved captures."""
    tools = _load_runtime(text)["tools"]
    if "end_srd_contingency_source" not in tools:
        raise ValueError("Emit the genuine Contingency owner before its removal adapter.")
    changed = _tools()
    for name, binder in (
        ("cast_spell", _bind_public_source_intent),
        ("dismiss_srd_spell", _bind_dismissal),
        ("cast_srd_recovery", _bind_recovery),
        ("dispel_srd_target_sources", _bind_target_dispel),
        ("drop_srd_control_held_items", _bind_inventory_loss),
    ):
        changed[name] = deepcopy(tools[name])
        binder(changed[name])
    return _emit(text, changed)
