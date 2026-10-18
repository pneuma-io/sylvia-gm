"""Read tool-call fragments from the supported provider stream representations."""

from collections.abc import Mapping


def _delta_value(delta: object, name: str, default: object = None) -> object:
    """Read fields from SDK objects or serialized provider events."""
    if isinstance(delta, Mapping):
        return delta.get(name, default)
    return getattr(delta, name, default)


def parse_tool_call_delta(delta: object) -> tuple[int, str, str, str]:
    """Return index, identity, name and argument fragments for either wire format."""
    raw_index = _delta_value(delta, "index", 0)
    index = raw_index if isinstance(raw_index, int) and raw_index >= 0 else 0
    raw_id = _delta_value(delta, "id", "")
    call_id = raw_id if isinstance(raw_id, str) else ""
    function = _delta_value(delta, "function")
    if function is not None:
        raw_name = _delta_value(function, "name", "")
        raw_arguments = _delta_value(function, "arguments", "")
    else:
        raw_name = _delta_value(delta, "name", "")
        raw_arguments = _delta_value(
            delta,
            "arguments",
            _delta_value(delta, "partial_json", ""),
        )
    name = raw_name if isinstance(raw_name, str) else ""
    arguments = raw_arguments if isinstance(raw_arguments, str) else ""
    return index, call_id, name, arguments
