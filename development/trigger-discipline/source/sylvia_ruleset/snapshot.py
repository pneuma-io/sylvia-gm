"""Own QUICK SOLUTION's AIM price and per-TEAM TURN performed-action history."""

ENTRY = "specialty.snapshot.entry.quick_solution"
STATE = "quick_solution_turn"
PRICE = "_quick_paid_ap"


def price_calculations():
    """Keep nominal AIM investment separate from the integer AP actually paid."""
    marker = f"attacker.{STATE}"
    return [
        {
            "set": {
                "field": "_quick_first",
                "value": (
                    f"attacker.class_id == 'SHARPSHOOTER' and '{ENTRY}' in attacker.specialties "
                    f"and ({marker} == null or (_target_id not in {marker}.aimed_targets "
                    f"and _target_id not in {marker}.attacked_targets))"
                ),
            }
        },
        {"set": {"field": "_quick_first_count", "value": "occurrences(one(_quick_first), true)"}},
        {
            "set": {
                "field": PRICE,
                "value": (
                    "max(1, ap - _quick_first_count * "
                    "definitions.parameters.specialty.snapshot.quick_solution.ap_discount)"
                ),
            }
        },
    ]


def performed(kind, actor="attacker", actor_id="_attacker_id", target_id="_target_id"):
    """Record only a performed AIM/attack, including a miss, once per target.

    History is kept for SHARPSHOOTER actors even before choosing this Entry, so
    choosing it cannot erase an already tracked action in the same TEAM TURN.
    Older state can omit the marker; reads/previews never create one.
    """
    assert kind in {"aimed_targets", "attacked_targets"}
    marker = f"{actor}.{STATE}"
    body = [
        {"set": {"field": "_quick_aimed", "value": []}},
        {"set": {"field": "_quick_attacked", "value": []}},
        {
            "set": {"field": "_quick_aimed", "value": f"{marker}.aimed_targets"},
            "condition": f"{marker} != null",
        },
        {
            "set": {"field": "_quick_attacked", "value": f"{marker}.attacked_targets"},
            "condition": f"{marker} != null",
        },
    ]
    selected = "_quick_aimed" if kind == "aimed_targets" else "_quick_attacked"
    body.extend(
        [
            {
                "list_push": {"list": selected, "value": target_id},
                "condition": f"{target_id} not in {selected}",
            },
            {
                "set": {
                    "field": f"{{{actor_id}}}.{STATE}",
                    "value": {
                        "aimed_targets": "_quick_aimed",
                        "attacked_targets": "_quick_attacked",
                    },
                }
            },
        ]
    )
    return [{"condition": f"{actor}.class_id == 'SHARPSHOOTER'", "on_true": body, "on_false": []}]


def reset():
    """Clear recorded history only when the actual TEAM TURN changes sides."""
    return [
        {
            "set": {
                "field": f"{{_actor_id}}.{STATE}",
                "value": {
                    "aimed_targets": [],
                    "attacked_targets": [],
                },
            },
            "condition": f"actor.{STATE} != null",
        }
    ]


def state_schema():
    from .network_state_schema import _array, _object

    targets = _array({"type": "string", "minLength": 1}, maximum=4096)
    targets["uniqueItems"] = True
    return _object({"aimed_targets": targets, "attacked_targets": targets})


def state_schemas(existing):
    entities = next(row for row in existing if row["key"] == "entities")
    entities["schema"]["additionalProperties"]["properties"][STATE] = state_schema()
    return existing
