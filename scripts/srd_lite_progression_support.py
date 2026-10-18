"""Expose only the original stability recovery owner required by retained Spare."""
from copy import deepcopy


def expose_original_stable_recovery(record):
    """Leave the SRD runtime body and all parameter declarations unchanged."""
    rows = {row["name"]: row for row in record["tools"]}
    assert "advance_stable_recovery" not in rows
    recovery = deepcopy(rows["resolve_srd_fall"])
    clock = rows["advance_srd_lasting_time"]
    recovery.update(
        name="advance_stable_recovery",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "target": {"type": "string", "x-sylvia-gm-reference": "entity"},
                "elapsed_hours": {"type": "integer"},
                "witness_state_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["target", "elapsed_hours"],
        },
        state_predicates=deepcopy(clock["state_predicates"]),
    )
    assert recovery["capability"] == "dnd.srd.5.2.1.damage-healing-core"
    assert recovery["writers"] == ["dm"] and recovery["turn_statuses"] == ["open"]
    record["tools"].append(recovery)
    return record
