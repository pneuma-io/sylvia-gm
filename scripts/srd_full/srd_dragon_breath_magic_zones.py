"""Qualify the retained recipient and actual Cone targets without a lifecycle call cycle."""

from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _call, _load_runtime, _set, _when
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import _call, _load_runtime, _set, _when
    from srd_timed_senses import _replace_tools


PURE_SOURCE = "qualify_srd_dragon_magic_zone_source"
PURE_CAST = "qualify_srd_dragon_magic_action_zone"
PURE_EFFECT = "qualify_srd_dragon_magic_effect_zone"
TARGET = "resolve_srd_dragon_breath_magic_target"


def _lists(effects):
    yield effects
    for effect in effects:
        for branch in ("on_true", "on_false"):
            if branch in effect:
                yield from _lists(effect[branch])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _pure_source(original):
    """Keep the actual source predicate; lifecycle settlement remains at its existing owner."""
    tool = deepcopy(original)
    endings = [e for e in tool["effects"] if "end_srd_spell_magic_zone_source" in str(e)]
    if len(endings) != 1:
        raise ValueError("Dragon availability needs the exact Magic Zone source-ending boundary.")
    tool["effects"].remove(endings[0])
    tool["description"] = "Read the genuine Magic Zone clock and concentration without ending it."
    return tool


def _pure_query(original):
    """Reuse final membership and exclusion math, adding its current source qualification."""
    tool = deepcopy(original)
    refreshes = installed = 0
    for sequence in _lists(tool["effects"]):
        for effect in list(sequence):
            if effect.get("call") == "refresh_srd_spell_magic_zones":
                sequence.remove(effect)
                refreshes += 1
            if effect.get("condition") == "_magic_zone_record.installed == true":
                old = effect["on_true"]
                effect["on_true"] = [
                    _call(
                        PURE_SOURCE,
                        {
                            "caster_state": "{_magic_zone_record.caster_state_id}",
                            "source_id": "{_magic_zone_id}",
                        },
                        ["_magic_zone_source_active"],
                    ),
                    _when("_magic_zone_source_active == true", old),
                ]
                installed += 1
    if refreshes != 1 or installed != 1:
        raise ValueError("Dragon availability needs the actual refreshing Magic Zone query.")
    return tool


def _target(original):
    """Delegate unchanged save/packet behavior only after the actual spell-effect qualifier."""
    params = {p["name"]: "{" + p["name"] + "}" for p in original["parameters"]}
    for parameter in original["parameters"]:
        if parameter.get("resolve") == "entity":
            params[parameter["name"]] = "{_" + parameter["name"] + "_id}"
    call = _call(
        "resolve_spell_area_save_damage_target",
        params,
        ["_spell_summary_rows", "_spell_detail_rows"],
    )
    tool = deepcopy(original)
    tool["description"] = "Qualify Dragon's Breath's actual magical Cone target before its save."
    tool["effects"] = [
        {
            "condition": "lasting_state.has_spell_magic_zones == true",
            "on_true": [
                _call(
                    PURE_EFFECT,
                    {
                        "source_subject": "{source_state_id}",
                        "target_subject": "{_target_id}",
                        "effect_kind": "spell",
                        "actual_spell_level": "{actual_spell_level}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                    ["_magic_zone_effect_allowed", "_magic_zone_blocking_source_ids"],
                ),
                {
                    "condition": "_magic_zone_effect_allowed == true",
                    "on_true": [call],
                    "on_false": [
                        _set(
                            "_spell_summary_rows",
                            [{"label": "target.name", "value": "'Dragon’s Breath: no effect'"}],
                        ),
                        _set(
                            "_spell_detail_rows",
                            [
                                {
                                    "label": "target.name",
                                    "value": "'Excluded by the actual Magic Zone source.'",
                                },
                                {
                                    "label": "'Blocking sources'",
                                    "value": "_magic_zone_blocking_source_ids",
                                },
                            ],
                        ),
                    ],
                },
            ],
            "on_false": [deepcopy(call)],
        },
    ]
    # Original numeric/save readiness checks belong to the actual save owner. A suppressed
    # target never rolls a save, but the authenticated source identities remain formal.
    tool["pre_conditions"] = []
    tool["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    return tool


def bind_runtime_consumers(text):
    """Join after final Dragon and Magic Zone owners, before later packet or reaction capture."""
    text = text.replace("\r\n", "\n")
    tools = _load_runtime(text)["tools"]
    needed = {
        "check_srd_spell_magic_zone_source",
        "check_srd_magic_zone_cast",
        "check_srd_magic_zone_effect",
        "check_srd_dragon_breath_record",
        "use_srd_dragon_breath",
        "resolve_spell_area_save_damage_target",
    }
    if missing := needed - tools.keys():
        raise ValueError("Dragon availability needs genuine owners: " + ", ".join(sorted(missing)))
    changed = {
        PURE_SOURCE: _pure_source(tools["check_srd_spell_magic_zone_source"]),
        PURE_CAST: _pure_query(tools["check_srd_magic_zone_cast"]),
        PURE_EFFECT: _pure_query(tools["check_srd_magic_zone_effect"]),
        TARGET: _target(tools["resolve_spell_area_save_damage_target"]),
    }
    record = deepcopy(tools["check_srd_dragon_breath_record"])
    if PURE_CAST not in str(record["effects"]):
        record["effects"].append(
            _when(
                "_breath_active == true and lasting_state.has_spell_magic_zones == true",
                [
                    _call(
                        PURE_CAST,
                        {"actor_state": "{record.target}", "lasting_state": "{_lasting_state_id}"},
                        ["_magic_zone_can_cast", "_magic_zone_blocking_source_ids"],
                    ),
                    _set("_breath_active", "_magic_zone_can_cast == true"),
                ],
            )
        )
        changed["check_srd_dragon_breath_record"] = record
    use = deepcopy(tools["use_srd_dragon_breath"])
    calls = 0
    for sequence in _lists(use["effects"]):
        for effect in sequence:
            if effect.get("call") == "resolve_spell_area_save_damage_target":
                effect["call"] = TARGET
                calls += 1
    if calls > 1 or (not calls and TARGET not in str(use["effects"])):
        raise ValueError("Dragon availability needs one actual area target call.")
    if calls:
        changed["use_srd_dragon_breath"] = use
    changed = {n: t for n, t in changed.items() if tools.get(n) != t}
    return _replace_tools(text, changed) if changed else text
