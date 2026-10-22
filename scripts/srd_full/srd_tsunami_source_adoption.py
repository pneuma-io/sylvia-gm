"""Join Tsunami's actual cast identity and exact retirement to ready Source owners."""

from copy import deepcopy

if __package__:
    from .srd_courier_chronology import _emit
    from .srd_divination_spells import _normalize
    from .srd_scene_relations import _array, _tool_scene
    from .srd_spell_removal_adoption import _actual_path, _source_check
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _when,
    )
    from .srd_timed_senses import _optional
    from .srd_tsunami import _source
else:
    from srd_courier_chronology import _emit
    from srd_divination_spells import _normalize
    from srd_scene_relations import _array, _tool_scene
    from srd_spell_removal_adoption import _actual_path, _source_check
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _when,
    )
    from srd_timed_senses import _optional
    from srd_tsunami import _source


def _walk(effects):
    for effect in effects:
        yield effect
        for branch in ("on_true", "on_false"):
            yield from _walk(effect.get(branch, []))
        yield from _walk(effect.get("for_each", {}).get("effects", []))


def _producer(tools, changed):
    """Reuse the existing paid serial allocator and private forwarding, never a new ID."""
    if "begin_srd_spell_source_instance" not in tools:
        raise ValueError("Tsunami adoption requires the authentic common Source producer first.")
    for name, original in tools.items():
        span_nodes = [
            node
            for node in _walk(original.get("effects", []))
            if node.get("set", {}).get("field") == "_spell_time_span_ids"
        ]
        if not span_nodes:
            continue
        owner = deepcopy(original)
        for node in _walk(owner["effects"]):
            if node.get("set", {}).get("field") == "_spell_time_span_ids":
                values = node["set"]["value"]
                if "'tsunami'" not in values:
                    values.append("'tsunami'")
            if node.get("call") == "cast_srd_tsunami":
                node["params"].update(
                    spell_cast_id="{_spell_source_cast_id}",
                    spell_cast_owner="{_caster_state_id}",
                    actual_spell_level="{_slot_level}",
                )
        changed[name] = owner
    cast = deepcopy(tools["cast_srd_tsunami"])
    marker = "spell_cast_id != ''"
    cast["effects"] = [
        e
        for e in cast["effects"]
        if not (e.get("condition") == marker and "actual Tsunami cast owner" in str(e))
    ]
    cast["effects"].insert(
        0,
        _when(
            marker,
            [
                _require(
                    "spell_cast_owner != null and _spell_cast_owner_id == _caster_state_id and "
                    "caster_state.last_spell_source_cast_id == spell_cast_id and "
                    "caster_state.last_spell_source_spell == 'tsunami' and "
                    "actual_spell_level == slot_level",
                    "Use the actual Tsunami cast owner, identity and paid slot.",
                ),
            ],
        ),
    )
    for node in _walk(cast["effects"]):
        if node.get("set", {}).get("field") == "_tsunami_source":
            record = node["set"]["value"]
            if isinstance(record, dict) and record.get("spell") == "'tsunami'":
                record["ongoing_duration"] = "spell_cast_id != ''"
    changed["cast_srd_tsunami"] = cast


def _source_tools():
    end = _tool_scene(
        "End only the exact water focus and preserve its actual queued exit consequences.",
        [_parameter("source_id")],
        [
            *_source(),
            _call(
                "retire_srd_tsunami_focus",
                {
                    "caster_state": "{_tsunami_source.caster}",
                    "source_id": "{source_id}",
                    "sequence": "{_tsunami_source.sequence}",
                },
            ),
            _call("retire_srd_tsunami_source", {"source_id": "{source_id}"}),
        ],
    )
    focus = _tool_scene(
        "Clear only this exact actual concentration before source-owned retirement.",
        [_entity("caster_state"), _parameter("source_id"), _parameter("sequence", "integer")],
        [
            *_source(),
            _require(
                "_tsunami_source.caster == _caster_state_id and "
                "_tsunami_source.sequence == sequence",
                "The exact retained water source owns this focus.",
            ),
            _when(
                "caster_state.concentration_spell_id == 'tsunami' and "
                "caster_state.concentration_effect_family == 'tsunami' and "
                "caster_state.concentration_sequence == sequence",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{_caster_state_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    dispel = _tool_scene(
        "Dispel a selected actual point of the active water effect at its real level.",
        [
            _entity("caster_state"),
            _optional("caster", "string", None, resolve="participant"),
            _parameter("source_id"),
            _array("point"),
            _parameter("slot_level", "integer"),
            _parameter("casting_modifier", "integer"),
            _parameter("casting_ability"),
        ],
        [
            *_source(),
            _require(
                "_tsunami_source.active == true and "
                "state.lasting_tick < _tsunami_source.expires_tick",
                "Select an actual active ongoing water source.",
            ),
            _call(
                "refresh_srd_tsunami_source",
                {"caster_state": "{_tsunami_source.caster}", "source_id": "{source_id}"},
            ),
            *_source(),
            _require("_tsunami_source.active == true", "The exact actual water focus has ended."),
            *[
                _require(
                    f"point[{i}] >= _tsunami_source.volume.min[{i}] and "
                    f"point[{i}] <= _tsunami_source.volume.max[{i}]",
                    "Select a point actually within this magical water effect.",
                )
                for i in range(3)
            ],
            *_actual_path("point", "_tsunami_source.plane"),
            *_source_check("_tsunami_source"),
            _when(
                "_dispel_succeeded == true",
                [
                    _call("end_srd_owned_tsunami_source", {"source_id": "{source_id}"}),
                ],
            ),
        ],
    )
    return {
        "end_srd_owned_tsunami_source": end,
        "retire_srd_tsunami_focus": focus,
        "dispel_srd_tsunami_owned_source": dispel,
    }


def _selected_dispel(tools, changed):
    """Retain the paid Recovery path and add only one real selected-effect choice."""
    original = tools["cast_srd_recovery"]
    marker = "spell == 'dispel_magic' and recovery_choice == 'tsunami'"
    owner = deepcopy(original)
    owner["effects"] = [e for e in owner["effects"] if e.get("condition") != marker]
    for node in _walk(owner["effects"]):
        if (
            node.get("set", {}).get("field")
            in {
                "_recovery_source_intents",
                "_recovery_source_owners",
            }
            and "'tsunami'" not in node["set"]["value"]
        ):
            node["set"]["value"].append("'tsunami'")
    template = next(
        (
            e
            for e in owner["effects"]
            if e.get("condition") == "spell == 'dispel_magic' and recovery_choice == 'secure_area'"
        ),
        None,
    )
    if template is None:
        raise ValueError("Tsunami adoption needs the ready selected-effect Recovery owner.")
    branch = deepcopy(template)
    branch["condition"] = marker
    for node in _walk([branch]):
        if node.get("call") == "dispel_srd_secure_area_owned_source":
            node["call"] = "dispel_srd_tsunami_owned_source"
    owner["effects"].append(branch)
    changed["cast_srd_recovery"] = owner
    for name, original in tools.items():
        parameters = original.get("parameters", [])
        if any(p["name"] == "recovery_choice" and p["type"] == "enum" for p in parameters):
            expanded = deepcopy(changed.get(name, original))
            for parameter in expanded["parameters"]:
                if (
                    parameter["name"] == "recovery_choice"
                    and "tsunami" not in parameter["enum_values"]
                ):
                    parameter["enum_values"].append("tsunami")
            changed[name] = expanded


def bind_runtime(text):
    """Run after common Source/removal, before final extended casting and Reaction cloning."""
    tools = _load_runtime(text)["tools"]
    if "cast_srd_tsunami" not in tools:
        return text
    changed = _source_tools()
    _producer(tools, changed)
    _selected_dispel(tools, changed)
    _normalize(changed)
    return _emit(text, changed)
