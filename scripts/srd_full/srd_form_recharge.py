"""Printed form attack Recharge uses belong to their actual saved spell source."""

from copy import deepcopy

if __package__:
    from . import srd_actor_spell_modifiers as actor
else:
    import srd_actor_spell_modifiers as actor

_set, _when, _call, _require = actor._set, actor._when, actor._call, actor._require


def _loop(collection, item, effects):
    return {"for_each": {"collection": collection, "as": item, "do": effects}}


def _lookup():
    return {
        "table_lookup": {
            "table": "definitions.beast_forms",
            "key": "_recharge_source.form_id",
            "required": True,
        },
        "store_as": "_recharge_form",
    }


def _source():
    return [
        _set("_recharge_source_id", "target_state.form_current_source_id"),
        _set("_recharge_source", "lasting_state.transformation_sources[_recharge_source_id]"),
        _require("_recharge_source.installed == true", "The actual form source has ended."),
        _lookup(),
        _set("_recharge_form_id", "_recharge_source.form_id"),
        _when(
            "_recharge_source.recharge_uses == null",
            [_set("lasting_state.transformation_sources.{_recharge_source_id}.recharge_uses", {})],
        ),
        _set("_recharge_uses", {}),
        _when(
            "_recharge_form_id in lasting_state.transformation_sources"
            "[_recharge_source_id].recharge_uses",
            [
                _set(
                    "_recharge_uses",
                    "lasting_state.transformation_sources"
                    "[_recharge_source_id].recharge_uses[_recharge_form_id]",
                )
            ],
        ),
    ]


def tools():
    parameters = [actor._entity("target_state"), actor._entity("lasting_state")]
    commit = _set(
        "lasting_state.transformation_sources.{_recharge_source_id}.recharge_uses.{_recharge_form_id}",
        "_recharge_uses",
    )
    consume = actor._tool(
        "Spend one actual printed form Recharge attack without supplying its availability.",
        parameters + [actor._parameter("attack_name")],
        _source()
        + [
            commit,
            _require(
                "attack_name in _recharge_form.attacks and "
                "_recharge_form.attacks[attack_name].recharge_min != null",
                "This form has no such printed Recharge attack.",
            ),
            _set("_recharge_key", "attack_name"),
            _require(
                "_recharge_key not in _recharge_uses or "
                "_recharge_uses[_recharge_key].available == true",
                "This printed attack is spent until its actual Recharge roll or completed rest.",
            ),
            _set("_recharge_turn", 0),
            _when(
                "target_state.turns_started != null",
                [_set("_recharge_turn", "target_state.turns_started")],
            ),
            _set(
                "lasting_state.transformation_sources.{_recharge_source_id}."
                "recharge_uses.{_recharge_form_id}.{_recharge_key}",
                {"available": False, "last_turn": "_recharge_turn", "roll": None},
            ),
        ],
    )
    start = actor._tool(
        "At the actual turn start, roll the saved body's spent printed Recharge actions once.",
        parameters,
        _source()
        + [
            commit,
            _set("_recharge_rows", []),
            _set("_recharge_attacks", "_recharge_form.recharge_attack_names"),
            _loop(
                "_recharge_attacks",
                "_recharge_attack_name",
                [
                    _set("_recharge_key", "_recharge_attack_name"),
                    _when(
                        "_recharge_key in _recharge_uses and "
                        "_recharge_uses[_recharge_key].available != true and "
                        "_recharge_uses[_recharge_key].last_turn < target_state.turns_started",
                        [
                            {"dice": {"expression": "d6"}, "store_as": "_recharge_roll"},
                            _set(
                                "lasting_state.transformation_sources.{_recharge_source_id}."
                                "recharge_uses.{_recharge_form_id}.{_recharge_key}",
                                {
                                    "available": "_recharge_roll.total >= "
                                    "_recharge_form.attacks[_recharge_attack_name].recharge_min",
                                    "last_turn": "target_state.turns_started",
                                    "roll": "_recharge_roll",
                                },
                            ),
                            {
                                "list_push": {
                                    "list": "_recharge_rows",
                                    "value": {
                                        "label": "_recharge_attack_name",
                                        "value": "_recharge_roll.total",
                                    },
                                }
                            },
                        ],
                    ),
                ],
            ),
        ],
    )
    rest = actor._tool(
        "A completed Short or Long Rest renews printed uses of the actual active form source.",
        parameters,
        _source()
        + [_set("lasting_state.transformation_sources.{_recharge_source_id}.recharge_uses", {})],
    )
    return {
        "consume_srd_form_recharge_attack": consume,
        "start_srd_form_recharge_turn": start,
        "restore_srd_form_recharge_rest": rest,
    }


def compose(runtime_tools, changed):
    """Attach to genuine start-turn and completed-rest owners with cheap inactive guards."""
    changed.update(tools())
    for name, target, helper, anchor in (
        ("begin_combat_turn", "actor", "start_srd_form_recharge_turn", "actor.turns_started"),
        ("recover_srd_rest_resources", "target_state", "restore_srd_form_recharge_rest", None),
    ):
        tool = changed.setdefault(name, deepcopy(runtime_tools[name]))
        tool["effects"] = [effect for effect in tool["effects"] if helper not in str(effect)]
        effects = [
            _call(
                helper,
                {
                    "target_state": "{_actor_id}" if target == "actor" else "{_target_state_id}",
                    "lasting_state": "state",
                },
                ["_recharge_rows"] if anchor else None,
            ),
        ]
        if anchor:
            effects.append(
                _loop(
                    "_recharge_rows",
                    "_recharge_row",
                    [
                        {
                            "list_push": {"list": "_turn_effect_rows", "value": "_recharge_row"},
                        }
                    ],
                )
            )
            # Existing turn-effect processing initializes rows before this owning callback.
            index = next(
                i + 1
                for i, e in enumerate(tool["effects"])
                if e.get("call") == "process_srd_turn_effects"
            )
            tool["effects"].insert(index, _when(f"{target}.form_has_recharge == true", effects))
        else:
            tool["effects"].append(_when(f"{target}.form_has_recharge == true", effects))
