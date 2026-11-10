from __future__ import annotations

from typing import Any


def _flatten(value: Any, prefix: str = "") -> dict[str, float]:
    result: dict[str, float] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"events", "enumeration", "outcomes", "sources"}:
                continue
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(_flatten(child, f"{prefix}[{index}]"))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        result[prefix] = float(value)
    return result


def compare_results(accepted: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    left = _flatten(accepted)
    right = _flatten(current)
    shared = sorted(set(left) & set(right))
    changed = [
        {
            "metric": metric,
            "accepted": left[metric],
            "current": right[metric],
            "absolute_delta": right[metric] - left[metric],
            "relative_delta": None if left[metric] == 0 else (right[metric] - left[metric]) / abs(left[metric]),
        }
        for metric in shared
        if left[metric] != right[metric]
    ]
    return {
        "accepted_parameter_hash": accepted.get("provenance", {}).get("parameter_set_hash"),
        "current_parameter_hash": current.get("provenance", {}).get("parameter_set_hash"),
        "shared_numeric_metrics": len(shared),
        "changed_metrics": changed,
        "missing_from_current": sorted(set(left) - set(right)),
        "new_in_current": sorted(set(right) - set(left)),
    }
