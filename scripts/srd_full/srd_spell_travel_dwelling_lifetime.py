"""Retained dwellings' existing list pool supplies exact lifetime and physical Dispel selectors."""

if __package__:
    from .srd_scene_relations import _tool_scene
    from .srd_spell_travel_dwelling import _lookup
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _push
else:
    from srd_scene_relations import _tool_scene
    from srd_spell_travel_dwelling import _lookup
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _push


def dwelling_lifetime_tools():
    """Admit only Mansion and Rope; Root retains shared allocation, checks and outer returns."""
    if __package__:
        from .srd_spell_travel import _scene_subject
    else:
        from srd_spell_travel import _scene_subject

    read = _tool_scene(
        "Read the real dwelling list-pool source and its finite lifetime without projection.",
        [_parameter("source_id")],
        [
            *_lookup(),
            _require(
                "(_xd_source.spell == 'magnificent_mansion' or _xd_source.spell == 'rope_trick')",
                "Select a retained dwelling source.",
            ),
            _set("_lifetime_record", "_xd_source"),
            _set(
                "_lifetime_active",
                "_xd_source.active == true and state.lasting_tick < _xd_source.expires_tick",
            ),
        ],
    )
    collect = _tool_scene(
        "Select only this actual caster's exact finite, allocated dwelling cast; "
        "legacy stays separate.",
        [_entity("caster_state"), _parameter("cast_id")],
        [
            _require("cast_id != ''", "Choose an authentic allocated cast identity."),
            _set("_dwelling_cast_source_ids", []),
            _set("_dwelling_cast_rows", []),
            _when(
                "state.extradimensional_sources != null",
                [_set("_dwelling_cast_rows", "state.extradimensional_sources")],
            ),
            _loop(
                "_dwelling_cast_rows",
                "_dwelling_cast_row",
                [
                    _when(
                        "(_dwelling_cast_row.spell == 'magnificent_mansion' or "
                        "_dwelling_cast_row.spell == 'rope_trick') and "
                        "_dwelling_cast_row.active == true and "
                        "_dwelling_cast_row.cast_id == cast_id and "
                        "_dwelling_cast_row.source_caster_id == _caster_state_id and "
                        "_dwelling_cast_row.caster == _caster_state_id and "
                        "_dwelling_cast_row.ongoing_duration == true and "
                        "_dwelling_cast_row.expires_tick != null and "
                        "state.lasting_tick < _dwelling_cast_row.expires_tick",
                        [
                            _push("_dwelling_cast_source_ids", "_dwelling_cast_row.id"),
                        ],
                    ),
                ],
            ),
        ],
    )
    dispel = _tool_scene(
        "Select the actual active dwelling spell from either source-owned physical door.",
        [_parameter("subject")],
        [
            *_scene_subject("{subject}", "_dwelling_dispel_body"),
            _set("_dwelling_dispel_source_ids", []),
            _set("_dwelling_dispel_rows", []),
            _when(
                "state.extradimensional_sources != null",
                [_set("_dwelling_dispel_rows", "state.extradimensional_sources")],
            ),
            _loop(
                "_dwelling_dispel_rows",
                "_dwelling_dispel_row",
                [
                    _when(
                        "(_dwelling_dispel_row.spell == 'magnificent_mansion' or "
                        "_dwelling_dispel_row.spell == 'rope_trick') and "
                        "_dwelling_dispel_row.active == true and "
                        "state.lasting_tick < _dwelling_dispel_row.expires_tick and "
                        "_dwelling_dispel_body.metadata.dwelling_source_id == "
                        "_dwelling_dispel_row.id and "
                        "(_dwelling_dispel_body.id == _dwelling_dispel_row.outer_id or "
                        "_dwelling_dispel_body.id == _dwelling_dispel_row.inner_id)",
                        [
                            _push("_dwelling_dispel_source_ids", "_dwelling_dispel_row.id"),
                        ],
                    ),
                ],
            ),
        ],
    )
    retire = _tool_scene(
        "Retire only the selected actual dwelling source through its raw queue-only end owner.",
        [_parameter("source_id")],
        [
            *_lookup(),
            _require(
                "(_xd_source.spell == 'magnificent_mansion' or _xd_source.spell == 'rope_trick')",
                "Select a retained dwelling source.",
            ),
            _call("end_srd_extradimensional_source", {"source_id": "{source_id}"}),
        ],
    )
    return {
        "read_srd_lifetime_dwelling_source": read,
        "collect_srd_dwelling_cast_sources": collect,
        "collect_srd_dwelling_dispel_sources": dispel,
        "retire_srd_owned_dwelling_source": retire,
    }


def emit_runtime(text):
    """Emit definitions after Mansion exists, before Root's existing common removal wiring."""
    if __package__:
        from .srd_spell_travel import _normalize
        from .srd_timed_senses import _replace_tools
    else:
        from srd_spell_travel import _normalize
        from srd_timed_senses import _replace_tools
    changed = dwelling_lifetime_tools()
    _normalize(changed)
    return _replace_tools(text, changed)
