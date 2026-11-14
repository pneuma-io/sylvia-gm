"""Join authentic bead removal and outer action settlement without recursive damage."""

from copy import deepcopy

if __package__:
    from .build_srd_5_2_1_mechanics import TOOL_CAPABILITIES
    from .srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _lasting,
        _param,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from .srd_delayed_blast_fireball import DRAIN, QUEUE, REFRESH, SPELL
    from .srd_spell_exile_composition import _reachable
    from .srd_spell_removal_adoption import _actual_path, _source_check
    from .srd_spell_trigger_composition import _effect_lists
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_actor_defenses import _tool as normalized_tool
    from .srd_timed_senses import _replace_tools
else:
    from build_srd_5_2_1_mechanics import TOOL_CAPABILITIES
    from srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _lasting,
        _param,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from srd_delayed_blast_fireball import DRAIN, QUEUE, REFRESH, SPELL
    from srd_spell_exile_composition import _reachable
    from srd_spell_removal_adoption import _actual_path, _source_check
    from srd_spell_trigger_composition import _effect_lists
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_actor_defenses import _tool as normalized_tool
    from srd_timed_senses import _replace_tools


def _selected_bead_dispel(tools):
    """Select the actual saved bead point, never its bookkeeping caster target."""
    position = [
        _set("_bead_origin", "_bead_source.origin"),
        _set("_bead_plane", "_bead_source.plane"),
    ]
    if "resolve_srd_delayed_blast_position" in tools:
        position = [
            _call(
                "resolve_srd_delayed_blast_position",
                {"record": "{_bead_source}"},
                ("_bead_origin", "_bead_plane"),
            )
        ]
    return _tool(
        "Dispel the selected authentic bead at its actual saved point and plane.",
        [
            _entity("caster_state"),
            _param("caster", resolve="participant", required=False),
            _lasting(),
            _param("source_id"),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
        ],
        [
            _set("_bead_source", None),
            _set("_bead_sources", []),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_bead_sources", "lasting_state.turn_effects")],
            ),
            _each(
                "_bead_sources",
                "_bead_candidate",
                [
                    _when(
                        f"_bead_candidate.kind == '{SPELL}' and "
                        "_bead_candidate.source_id == source_id",
                        [
                            _require("_bead_source == null", "The bead source is ambiguous."),
                            _set("_bead_source", "_bead_candidate"),
                        ],
                    ),
                ],
            ),
            _require(
                f"_bead_source != null and _bead_source.spell == '{SPELL}' and "
                "_bead_source.active == true and _bead_source.cast_id == source_id and "
                "_bead_source.source_caster_id == _bead_source.caster and "
                "_bead_source.spell_level == _bead_source.slot_level and "
                "_bead_source.slot_level >= 7 and _bead_source.slot_level <= 9 and "
                "_bead_source.ongoing_duration == true and "
                "lasting_state.lasting_tick < _bead_source.expires_tick",
                "Select the actual active paid bead source.",
            ),
            *position,
            *_actual_path("_bead_origin", "_bead_plane"),
            *_source_check("_bead_source"),
            _when(
                "_dispel_succeeded == true",
                [
                    _call(
                        "finish_srd_delayed_blast_removal",
                        {
                            "caster_state": "{_bead_source.caster}",
                            "source_id": "{source_id}",
                        },
                    ),
                ],
            ),
        ],
    )


def extend_removal_tools(tools, changed):
    """Queue a matched dismissal before deletion; selected Dispel uses the real bead."""
    if QUEUE not in tools:
        return
    changed[f"dispel_srd_{SPELL}_owned_source"] = _selected_bead_dispel(tools)
    matches = [
        node
        for node in _walk(changed["dismiss_srd_spell"]["effects"])
        if "_dismiss_effect.cast_id == cast_id" in node.get("condition", "")
    ]
    if len(matches) != 1:
        raise ValueError("Bead dismissal needs the unique actual turn-effect selection.")
    branch = matches[0]["on_true"]
    if QUEUE not in str(branch):
        branch.insert(
            0,
            _when(
                f"_dismiss_effect.kind == '{SPELL}' and _dismiss_effect.active == true",
                [
                    _call(
                        QUEUE,
                        {
                            "caster_state": "{_dismiss_effect.caster}",
                            "source_id": "{_dismiss_effect.source_id}",
                        },
                    ),
                ],
            ),
        )


def bind_runtime(text, public_tools):
    """Drain after genuine outer completion, preserving owners that already drain in place."""
    tools = _load_runtime(text)["tools"]
    if DRAIN not in tools:
        return text
    graph = {
        name: {
            node["call"]
            for effects in _effect_lists(tool["effects"])
            for node in effects
            if "call" in node
        }
        for name, tool in tools.items()
    }
    settlements = {
        DRAIN,
        "settle_srd_travel_exile_returns",
        "settle_srd_levitate_returns",
        "handoff_srd_telekinesis_releases",
        "handoff_srd_mirage_support_losses",
    } & tools.keys()
    within_settlement = _reachable(graph, settlements)
    candidates = (
        set(public_tools)
        | set(TOOL_CAPABILITIES)
        | {"cast_spell", "cast_spell_outside_combat", "advance_srd_lasting_clock"}
    ) & tools.keys() - within_settlement
    changed = {}
    for name in sorted(candidates):
        # The voluntary spell reads its resolved event immediately after its own drain.
        if DRAIN in graph[name]:
            continue
        descendants = _reachable(graph, {name})
        if not descendants & {QUEUE, REFRESH, "end_srd_concentration_node"}:
            continue
        owner = deepcopy(tools[name])
        completion = normalized_tool(
            owner["description"],
            owner["parameters"],
            [_when("state.has_delayed_blast_detonations == true", [_call(DRAIN, {})])],
        )
        owner["effects"].extend(completion["effects"])
        for parameter in owner["parameters"]:
            if parameter["name"] == "lasting_state":
                parameter["model_hidden"] = True
        changed[name] = owner
    return _replace_tools(text, changed) if changed else text
