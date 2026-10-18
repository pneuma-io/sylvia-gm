"""Expose recorded form attacks, Cloud events and selected control repeat saves."""

from copy import deepcopy

FORM_OWNERS = ("attack_srd_transformed_form", "attack_srd_altered_natural_weapon")
FORM_ROLL = "_visual_original_form_damage_roll"


def _lists(rows):
    """Visit the existing effect containers without changing their execution order."""
    yield rows
    for row in rows:
        for branch in ("on_true", "on_false"):
            yield from _lists(row.get(branch, []))
        if "for_each" in row:
            yield from _lists(row["for_each"]["do"])


def _bind_form_widgets(tools):
    """Snapshot genuine form damage faces before native total adjustments."""
    provider = tools["resolve_adjudicated_creature_attack"]
    if any(row.get("set", {}).get("field") == FORM_ROLL for row in provider["effects"]):
        raise ValueError("Form damage feedback must be composed exactly once")
    sites = [
        (rows, index)
        for rows in list(_lists(provider["effects"]))
        for index, effect in enumerate(rows)
        if "dice" in effect and effect.get("store_as") == "_damage_roll"
    ]
    if len(sites) != 1:
        raise ValueError("Keep the single native direct creature damage dice owner")
    rows, index = sites[0]
    rows[index + 1 : index + 1] = [
        {
            "condition": "(actor.form_attack_in_progress == true or "
            "actor.altered_natural_attack_in_progress == true) and _damage_roll != null",
            "on_true": [
                {
                    "set": {
                        "field": FORM_ROLL,
                        "value": {
                            "count": "_damage_roll.count",
                            "sides": "_damage_roll.sides",
                            "results": "_damage_roll.results",
                            "total": "_damage_roll.total",
                            "modifier": "_damage_roll.modifier",
                        },
                    }
                }
            ],
        }
    ]
    provider["effects"].insert(0, {"set": {"field": FORM_ROLL, "value": None}})
    for name in FORM_OWNERS:
        owner = tools[name]
        calls = [
            effect
            for rows in _lists(owner["effects"])
            for effect in rows
            if effect.get("call") == "resolve_adjudicated_creature_attack"
        ]
        if (
            len(calls) != 1
            or calls[0].get("export_context", {}).get("_damage_roll") != "_damage_roll"
        ):
            raise ValueError("Keep the actual recorded form damage export: " + name)
        calls[0]["export_context"][FORM_ROLL] = FORM_ROLL
        card = owner["metadata"]["result_card"]
        if card["equation"].get("roll") != {"path": "context._attack_roll.total"}:
            raise ValueError("Keep the existing recorded form attack equation: " + name)
        card["equation"]["rolls"] = {"path": "context._attack_roll.results"}
        card["summary"].append(
            {
                "label": "Damage dice before modifiers and defenses",
                "optional": True,
                "value": {"path": "context." + FORM_ROLL + ".results"},
                "equation": {
                    "die_sides": {"path": "context." + FORM_ROLL + ".sides"},
                    "rolls": {"path": "context." + FORM_ROLL + ".results"},
                    "roll": {"path": "context." + FORM_ROLL + ".total"},
                    "modifier": 0,
                    "total": {"path": "context." + FORM_ROLL + ".total"},
                },
            }
        )
        if name == "attack_srd_transformed_form":
            if (
                calls[0]["export_context"].get("_form_extra_damage_roll")
                != "_form_extra_damage_roll"
            ):
                raise ValueError("Keep the native named form extra-damage roll export")
            card["summary"].append(
                {
                    "label": "Additional damage dice before defenses",
                    "optional": True,
                    "value": {"path": "context._form_extra_damage_roll.results"},
                    "equation": {
                        "die_sides": {"path": "context._form_extra_damage_roll.sides"},
                        "rolls": {"path": "context._form_extra_damage_roll.results"},
                        "roll": {"path": "context._form_extra_damage_roll.total"},
                        "modifier": 0,
                        "total": {"path": "context._form_extra_damage_roll.total"},
                    },
                }
            )
    return len(FORM_OWNERS)


def _bind_cloud_widget(tools):
    """Append the genuine Cloud roll to its already propagated recipient details."""
    owner = tools["resolve_srd_lite_daggers_event"]
    sites = [
        (rows, index)
        for rows in list(_lists(owner["effects"]))
        for index, effect in enumerate(rows)
        if effect.get("list_push")
        == {"list": "_lite_daggers_detail_rows", "value": "_spell_target_detail"}
    ]
    if len(sites) != 1:
        raise ValueError("Keep the sole completed Cloud recipient receipt")
    rows, index = sites[0]
    rows[index + 1 : index + 1] = [
        {
            "list_push": {
                "list": "_lite_daggers_detail_rows",
                "value": {
                    "label": "target_state.name",
                    "value": "'Cloud of Daggers raw damage dice before defenses'",
                    "equation": {
                        "die_sides": "_lite_daggers_roll.sides",
                        "rolls": "_lite_daggers_roll.results",
                        "roll": "_lite_daggers_roll.total",
                        "modifier": 0,
                        "total": "_lite_daggers_roll.total",
                    },
                },
            },
        }
    ]
    return 1


def _bind_control_repeat_widgets(tools):
    """Forward the existing Fear/Sleep save equation through the native end-turn rows."""
    owner = tools["end_srd_control_behavior_turn"]
    sites = [
        (rows, index)
        for rows in list(_lists(owner["effects"]))
        for index, effect in enumerate(rows)
        if effect.get("call") == "resolve_srd_control_behavior_save"
    ]
    if len(sites) != 1:
        raise ValueError("Keep the sole native control repeat save call")
    rows, index = sites[0]
    exports = rows[index].get("export_context")
    if not isinstance(exports, dict) or exports.get("_control_save_roll") != "_control_save_roll":
        raise ValueError("Keep the actual saved control result exports")
    exports["_visual_order_save_row"] = "_visual_order_save_row"
    pushes = [
        (container, position)
        for container in list(_lists(owner["effects"]))
        for position, effect in enumerate(container)
        if effect.get("list_push")
        == {
            "list": "_control_end_rows",
            "value": {"label": "_control_source.spell_id", "value": "_control_save_detail"},
        }
    ]
    if len(pushes) != 1:
        raise ValueError("Keep the sole native control repeat-save text receipt")
    rows, index = pushes[0]
    rows[index + 1 : index + 1] = [
        {
            "condition": "(_control_source.spell_id == 'fear' or "
            "_control_source.spell_id == 'sleep') "
            "and _visual_order_save_row != null",
            "on_true": [
                {"list_push": {"list": "_control_end_rows", "value": "_visual_order_save_row"}}
            ],
        }
    ]
    return 1


def bind_followup_roll_widgets(runtime):
    """Add only recorded feedback after the existing shared spell-widget binder."""
    tools = runtime["tools"]
    names = (
        "resolve_adjudicated_creature_attack",
        *FORM_OWNERS,
        "resolve_srd_lite_daggers_event",
        "end_srd_control_behavior_turn",
    )
    changed = {name: deepcopy(tools[name]) for name in names}
    result = {
        "form_card_owners": _bind_form_widgets(changed),
        "cloud_recipient_receipts": _bind_cloud_widget(changed),
        "control_repeat_receipts": _bind_control_repeat_widgets(changed),
    }
    for name in names:
        tools[name] = changed[name]
    return result
