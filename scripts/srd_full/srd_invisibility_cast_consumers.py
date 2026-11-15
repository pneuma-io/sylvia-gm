"""Ordinary Invisibility ends at the actual accepted start of a caster's spell."""

from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _tool, _when
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _tool, _when
    from srd_timed_senses import _replace_tools


def _walk(effects):
    for effect in effects:
        yield effect
        for branch in ("on_true", "on_false"):
            yield from _walk(effect.get(branch, []))
        yield from _walk(effect.get("for_each", {}).get("do", []))


def _ending():
    return _call(
        "end_srd_invisibility_spell_source",
        {"caster_state": "{_caster_state_id}", "lasting_state": "{_lasting_state_id}"},
    )


def _tools(runtime):
    """Preserve source costs and Slow decisions, and skip saved-window completions."""
    tools = runtime["tools"]
    changed = {}
    helper = _tool(
        "End this actual caster's ordinary Invisibility after accepted casting begins.",
        [_entity("caster_state")],
        [
            _when(
                "caster_state.illumination_has_invisibility == true",
                [
                    _call(
                        "end_srd_invisibility_for_actor",
                        {"actor_state": "{_caster_state_id}", "event": "spell"},
                    )
                ],
            )
        ],
    )
    if tools.get("end_srd_invisibility_spell_source") != helper:
        changed["end_srd_invisibility_spell_source"] = helper
    for name in ("cast_spell", "cast_spell_outside_combat"):
        cast = deepcopy(tools[name])
        if any(
            e.get("call") == "end_srd_invisibility_spell_source" for e in _walk(cast["effects"])
        ):
            repaired = False
            for e in _walk(cast["effects"]):
                if "_spell_cast_failed != true and " in e.get("condition", "") and any(
                    n.get("call") == "end_srd_invisibility_spell_source"
                    for n in _walk(e.get("on_true", []))
                ):
                    e["condition"] = e["condition"].replace("_spell_cast_failed != true and ", "")
                    repaired = True
            if repaired:
                changed[name] = cast
            continue
        source = next(
            (
                i
                for i, e in enumerate(cast["effects"])
                if any(n.get("call") == "begin_srd_spell_source_instance" for n in _walk([e]))
            ),
            None,
        )
        if source is None:
            raise ValueError("Invisibility casting needs the actual paid source dispatch boundary.")
        cast["effects"].insert(
            source,
            _when(
                "caster_state.illumination_has_invisibility == true and "
                "_pending_spell_operation_lost != true and "
                "caster_state.pending_srd_completion_id == null",
                [_ending()],
            ),
        )
        changed[name] = cast
    name = "begin_srd_spell_cast_internal"
    begin = deepcopy(tools[name])
    accepted = next(
        e
        for e in begin["effects"]
        if e.get("condition") == "_slow_cast_failed != true"
        and any(
            n.get("set", {}).get("field") == "caster_state.extended_casting"
            for n in _walk(e.get("on_true", []))
        )
    )
    legacy = [
        e for e in accepted["on_true"] if e.get("call") == "end_srd_invisibility_spell_source"
    ]
    if legacy:
        for e in legacy:
            accepted["on_true"].remove(e)
    if legacy or not any(
        e.get("call") == "end_srd_invisibility_spell_source" for e in _walk(begin["effects"])
    ):
        begin["effects"].insert(
            begin["effects"].index(accepted),
            _when("caster_state.illumination_has_invisibility == true", [_ending()]),
        )
        changed[name] = begin
    return changed


def bind_runtime_consumers(text):
    """Bind after actual casting/source owners; Reaction owns its accepted-begin callback."""
    return _replace_tools(text, _tools(_load_runtime(text)))
