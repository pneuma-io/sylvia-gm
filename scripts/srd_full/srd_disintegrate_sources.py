"""Disintegrate's explicit force-source exceptions retire their existing owning sources."""

from copy import deepcopy

if __package__:
    from .srd_freezing_sphere import _lists
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
    )
    from .srd_timed_senses import _optional, _replace_tools
else:
    from srd_freezing_sphere import _lists
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
    )
    from srd_timed_senses import _optional, _replace_tools


INTENTS = (
    _optional(
        "disintegrate_source_kind",
        "enum",
        "creature",
        enum_values=["creature", "wall_of_force", "resilient_sphere"],
    ),
    _optional("disintegrate_source_id", "string", ""),
)
LEAF = "cast_srd_disintegrate_force_source"


def _adapter():
    """Actual source IDs qualify the exceptions; the selected visible target is a GM sight fact."""
    return _tool(
        "Destroy an actual Force Wall or Resilient globe without damaging its occupant.",
        [
            _entity("caster_state"),
            _parameter("kind", "enum", enum_values=["wall_of_force", "resilient_sphere"]),
            _parameter("source_id"),
            _parameter("target_distance", "number", minimum=0),
            _parameter("caster_can_see_target", "boolean"),
        ],
        [
            _require(
                "source_id != '' and target_distance <= 60 and caster_can_see_target == true",
                "Select a visible actual force source within60feet.",
            ),
            _set("_disintegrate_source", None),
            {
                "condition": "kind == 'wall_of_force'",
                "on_true": [
                    _require(
                        "state.solid_spell_wall_sources != null and "
                        "source_id in state.solid_spell_wall_sources",
                        "Select an existing Wall of Force source.",
                    ),
                    _set("_disintegrate_source", "state.solid_spell_wall_sources[source_id]"),
                    _require(
                        "_disintegrate_source.spell_id == 'wall_of_force' and "
                        "_disintegrate_source.installed == true",
                        "The Force Wall is no longer live.",
                    ),
                    _call(
                        "qualify_srd_solid_spell_wall_source",
                        {
                            "caster_state": "{_disintegrate_source.caster_state_id}",
                            "source_id": "{source_id}",
                        },
                        ["_solid_wall_current"],
                    ),
                    _require("_solid_wall_current == true", "The selected Force Wall has expired."),
                    _call(
                        "end_srd_solid_spell_wall_source",
                        {"source_id": "{source_id}", "reason": "disintegrate"},
                    ),
                ],
                "on_false": [
                    _require(
                        "state.spell_barrier_sources != null and source_id in "
                        "state.spell_barrier_sources",
                        "Select an existing Resilient Sphere source.",
                    ),
                    _set("_disintegrate_source", "state.spell_barrier_sources[source_id]"),
                    _require(
                        "_disintegrate_source.spell_id == 'resilient_sphere' and "
                        "_disintegrate_source.installed == true",
                        "The selected source is not a live Resilient Sphere.",
                    ),
                    _call(
                        "check_srd_spell_barrier_source",
                        {
                            "caster_state": "{_disintegrate_source.caster_state_id}",
                            "source_id": "{source_id}",
                        },
                        ["_barrier_source_active"],
                    ),
                    _require("_barrier_source_active == true", "The selected Sphere has expired."),
                    _call(
                        "end_srd_spell_barrier_source",
                        {"source_id": "{source_id}", "reason": "disintegrate"},
                    ),
                ],
            },
            _set("_spell_summary_rows", [{"label": "'Force source'", "value": "source_id"}]),
            _set(
                "_spell_detail_rows",
                [
                    {
                        "label": "'Effect'",
                        "value": "'Disintegrated the selected force source; no creature damage.'",
                    }
                ],
            ),
        ],
    )


def bind_runtime_consumers(text):
    """Join only the actual paid single-target family branch before Reaction captures it."""
    tools = _load_runtime(text)["tools"]
    changed = {LEAF: _adapter()}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        owner = deepcopy(tools[name])
        names = {p["name"] for p in owner["parameters"]}
        owner["parameters"] += [deepcopy(p) for p in INTENTS if p["name"] not in names]
        marker = "spell == 'disintegrate' and disintegrate_source_kind != 'creature'"
        if LEAF not in str(owner["effects"]):
            found = 0
            for effects in list(_lists(owner["effects"])):
                for index, effect in enumerate(list(effects)):
                    if effect.get("call") == "cast_spell":
                        effect["params"].update({p["name"]: "{" + p["name"] + "}" for p in INTENTS})
                        found += 1
                        continue
                    if effect.get("call") != "cast_spell_single_target_save_damage":
                        continue
                    effects[index] = {
                        "condition": marker,
                        "on_true": [
                            _call(
                                LEAF,
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "kind": "{disintegrate_source_kind}",
                                    "source_id": "{disintegrate_source_id}",
                                    "target_distance": "{target_distance}",
                                    "caster_can_see_target": "{_caster_can_see_target}",
                                },
                                ["_nested_spell_summary_rows", "_nested_spell_detail_rows"],
                            ),
                        ],
                        "on_false": [effect],
                    }
                    effects[index]["on_true"][0]["export_context"] = {
                        "_nested_spell_summary_rows": "_spell_summary_rows",
                        "_nested_spell_detail_rows": "_spell_detail_rows",
                    }
                    found += 1
            if found != 1:
                raise ValueError("Disintegrate needs the genuine paid scalar family dispatch.")
        changed[name] = owner
    return _replace_tools(_replace_tools(text, changed), changed)
