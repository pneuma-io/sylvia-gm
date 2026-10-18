"""Keep retained death counters within their admitted state-schema bounds."""


def bind_death_counter_bounds(runtime):
    changed = []

    def walk(value, tool):
        if isinstance(value, dict):
            increment = value.get("increment")
            if isinstance(increment, dict) and str(increment.get("field", "")).endswith(
                (".death_failures", ".death_successes")
            ):
                field, amount = increment["field"], increment.get("amount", 1)
                if not isinstance(amount, (int, float, str)) or isinstance(amount, bool):
                    raise ValueError("Unexpected retained death-counter amount")
                del value["increment"]
                value["set"] = {"field": field, "value": f"min(3, {field} + ({amount}))"}
                changed.append({"tool": tool, "field": field, "amount": amount})
            for child in value.values():
                walk(child, tool)
        elif isinstance(value, list):
            for child in value:
                walk(child, tool)

    for name, tool in runtime["tools"].items():
        walk(tool, name)
    if not any(row["tool"] == "death_saving_throw" for row in changed):
        raise ValueError("Retained Death Saving Throw increment boundary changed")
    return changed
