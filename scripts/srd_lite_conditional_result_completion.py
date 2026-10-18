"""Proposed package-owned cards for two conditional Lite outcomes.

This evidence module has no I/O or gameplay entry point. It mutates only the
supplied draft. Existing game effects, parameters and controls remain intact.
"""

from copy import deepcopy

PREFIX = "_fc16_"
HELD = "record_srd_control_held_items"
BODY_HELD = "record_srd_magic_jar_body_held_items"
CONTINGENCY = "record_srd_contingency_world_fact"
CHANGED_TOOLS = (HELD, BODY_HELD, CONTINGENCY)
HELD_EXPORTS = (
    "_fc16_held_body_id",
    "_fc16_held_shillelagh_ended",
    "_fc16_held_scaled_changed",
)


def _set(field, value, condition=None):
    node = {"set": {"field": field, "value": value}}
    if condition is not None:
        node["condition"] = condition
    return node


def _row(label, path, *, optional=False, labels=None):
    value = {"path": path}
    if labels is not None:
        value["labels"] = labels
    result = {"label": label, "value": value}
    if optional:
        result["optional"] = True
    return result


def _held_before():
    return [
        _set(
            "_fc16_held_before",
            {
                "shillelagh": "target_state.spell_shillelagh == true",
                "scaled_items": "target_state.enlarge_scaled_items",
            },
        ),
    ]


def _held_after():
    return [
        _set("_fc16_held_body_id", "_target_state_id"),
        _set("_fc16_held_shillelagh_ended", None),
        _set("_fc16_held_scaled_changed", None),
        _set(
            "_fc16_held_shillelagh_ended",
            True,
            "_fc16_held_before.shillelagh == true and target_state.spell_shillelagh != true",
        ),
        _set(
            "_fc16_held_scaled_changed",
            True,
            "_fc16_held_before.scaled_items != null and "
            "_fc16_held_before.scaled_items != target_state.enlarge_scaled_items",
        ),
    ]


def _nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def bind_conditional_result_completion(runtime, record):
    """Bind two existing operations without new persistent effects or controls.

    ``record`` is deliberately unchanged: the caller owns the candidate version
    and archive identity. A second application is rejected rather than doubling
    the number of display operations.
    """
    if record.get("id", "sylvia.srd-lite.bg3") != "sylvia.srd-lite.bg3":
        raise ValueError("Conditional result completion requires the Lite package")
    tools = runtime["tools"]
    for name in CHANGED_TOOLS:
        if any(
            str(node.get("set", {}).get("field", "")).startswith(PREFIX)
            for node in _nodes(tools[name]["effects"])
        ):
            raise ValueError("Conditional result completion is already bound")

    held = tools[HELD]
    branches = [
        node
        for node in held["effects"]
        if node.get("condition") == "target_state.magic_jar_role == 'possessed'"
    ]
    if len(branches) != 1 or "on_false" not in branches[0]:
        raise ValueError("Held-item possession boundary changed")
    branch = branches[0]
    body_calls = [node for node in branch["on_true"] if node.get("call") == BODY_HELD]
    if len(body_calls) != 1:
        raise ValueError("Held-item actual-body call changed")
    existing = body_calls[0].get("export_context", {})
    if not isinstance(existing, dict):
        raise ValueError("Held-item actual-body exports require an explicit mapping")
    if set(existing) & set(HELD_EXPORTS):
        raise ValueError("Held-item display export already exists")

    # Each capture surrounds the existing body-specific writes and refreshes.
    # The possession source's own projected flags are never reported as the body.
    branch["on_false"] = _held_before() + branch["on_false"] + _held_after()
    body = tools[BODY_HELD]
    body["effects"] = _held_before() + body["effects"] + _held_after()
    body_calls[0]["export_context"] = {**existing, **{key: key for key in HELD_EXPORTS}}

    held_metadata = held.setdefault("metadata", {})
    held_metadata["result_card"] = {
        "version": 1,
        "footprint": "inline",
        "title": "Held items recorded",
        "summary": [
            _row("Actual body ID", "context._fc16_held_body_id"),
            _row("Held items", "parameters.held_items"),
            _row(
                "Shillelagh",
                "context._fc16_held_shillelagh_ended",
                optional=True,
                labels={"true": "Ended when the actual weapon was released"},
            ),
        ],
        "details": [
            _row("Held attacks", "parameters.held_attack_names"),
            _row(
                "Resized carried-item effects",
                "context._fc16_held_scaled_changed",
                optional=True,
                labels={"true": "Updated after item release"},
            ),
        ],
    }
    held_metadata.pop("activity_label", None)

    # The matching source already owns its historical release result. Never use
    # the last iteration from the release queue, which may contain other sources.
    source = "lasting_state.contingency_sources[_contingency_source_id]"
    eligible = (
        "_contingency_released == true and _contingency_source_id != null and "
        "lasting_state.contingency_sources != null and " + source + " != null"
    )
    contingency = tools[CONTINGENCY]
    contingency["effects"] += [
        _set("_fc16_contingency_status", None),
        _set("_fc16_contingency_summary", None),
        _set("_fc16_contingency_details", []),
        _set("_fc16_contingency_status", source + ".status", eligible),
        _set("_fc16_contingency_summary", source + ".release_summary", eligible),
        _set(
            "_fc16_contingency_details",
            source + ".release_detail_rows",
            eligible + " and " + source + ".release_detail_rows != null",
        ),
    ]
    contingency_metadata = contingency.setdefault("metadata", {})
    contingency_metadata["result_card"] = {
        "version": 1,
        "footprint": "inline",
        "title": "Contingency occurrence",
        "summary": [
            _row("Occurrence", "parameters.fact_tag"),
            _row(
                "Trigger",
                "context._contingency_released",
                labels={
                    "true": "Contingency triggered",
                    "false": "No release for this occurrence",
                },
            ),
            _row("Saved outcome", "context._fc16_contingency_summary", optional=True),
            _row("Recorded source status", "context._fc16_contingency_status", optional=True),
            _row("Recorded source ID", "context._contingency_source_id", optional=True),
        ],
        "details": {"path": "context._fc16_contingency_details"},
    }
    contingency_metadata.pop("activity_label", None)


def strip_display_completion(value):
    """Return a copy with only this binder's captures and helper exports removed.

    Metadata is retained so callers can separately check the explicitly allowed
    result-card/activity-label changes. The stripper never removes game writes.
    """
    if isinstance(value, list):
        return [
            strip_display_completion(node)
            for node in value
            if not (
                isinstance(node, dict)
                and str(node.get("set", {}).get("field", "")).startswith(PREFIX)
            )
        ]
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key == "export_context" and isinstance(child, dict):
                added = {
                    name
                    for name, target in child.items()
                    if name.startswith(PREFIX) or str(target).startswith(PREFIX)
                }
                if added:
                    kept = {name: target for name, target in child.items() if name not in added}
                    if kept:
                        result[key] = deepcopy(kept)
                else:
                    result[key] = deepcopy(child)
            else:
                result[key] = strip_display_completion(child)
        return result
    return deepcopy(value)


def assert_native_shape_preserved(before, after, before_record, after_record):
    """Require identical native declarations after removing display additions."""
    cleaned = strip_display_completion(after)
    expected = deepcopy(before)
    for name in CHANGED_TOOLS:
        for tool in (cleaned["tools"][name], expected["tools"][name]):
            tool.get("metadata", {}).pop("result_card", None)
            tool.get("metadata", {}).pop("activity_label", None)
    if cleaned != expected or before_record != after_record:
        raise AssertionError("Conditional result binding changed native shape")
