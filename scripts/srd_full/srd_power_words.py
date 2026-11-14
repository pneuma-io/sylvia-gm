"""Power Words reuse timed-condition saves and the trusted target's Reaction choice."""

import re
from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _set, _tool, _when
    from .srd_timed_senses import _loop, _replace_tools
else:
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _set, _tool, _when
    from srd_timed_senses import _loop, _replace_tools


def _stun_precedence():
    """Apply p106's strongest/newest rule to p154's saved Stunned consequences.

    Saved DC measures potency; the real installation sequence breaks ties. A save
    ends that selected source. Other casts become eligible on the next end turn,
    rather than generating another save in the same event (bounded interpretation).
    """
    return _tool(
        "Select one active Power Word Stun consequence for this end-turn save.",
        [_entity("target_state"), _entity("lasting_state")],
        [
            _set("_power_word_stun_source_id", None),
            _set("_power_word_stun_dc", -1),
            _set("_power_word_stun_sequence", -1),
            _set("_power_word_stun_ids", []),
            _when(
                "target_state.timed_condition_source_ids != null",
                [_set("_power_word_stun_ids", "target_state.timed_condition_source_ids")],
            ),
            _loop(
                "_power_word_stun_ids",
                "_power_word_stun_id",
                [
                    _set(
                        "_power_word_stun_source",
                        "lasting_state.timed_condition_sources[_power_word_stun_id]",
                    ),
                    _when(
                        "_power_word_stun_source.installed == true and "
                        "_power_word_stun_source.target_state_id == _target_state_id and "
                        "_power_word_stun_source.spell_id == 'power_word_stun' and "
                        "_power_word_stun_source.condition == 'stunned' and "
                        "_power_word_stun_source.repeat_save_timing == 'end_of_target_turn' and "
                        "_power_word_stun_source.suppressed != true",
                        [
                            _when(
                                "_power_word_stun_source.save_dc > _power_word_stun_dc or "
                                "(_power_word_stun_source.save_dc == _power_word_stun_dc and "
                                "_power_word_stun_source.sequence > _power_word_stun_sequence)",
                                [
                                    _set("_power_word_stun_source_id", "_power_word_stun_id"),
                                    _set("_power_word_stun_dc", "_power_word_stun_source.save_dc"),
                                    _set(
                                        "_power_word_stun_sequence",
                                        "_power_word_stun_source.sequence",
                                    ),
                                ],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )


def _effect_lists(effects):
    """Visit only inline effect lists, preserving named lifecycle owners."""
    yield effects
    for effect in effects:
        for branch in ("on_true", "on_false"):
            if branch in effect:
                yield from _effect_lists(effect[branch])
        if "for_each" in effect:
            yield from _effect_lists(effect["for_each"]["do"])


def bind_runtime_consumers(text):
    """Bind after timed owners/restoration, before final Reaction continuation copies."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    helper = _stun_precedence()
    if tools.get("select_srd_power_word_stun_source") != helper:
        changed["select_srd_power_word_stun_source"] = helper
    ending = deepcopy(tools["resolve_srd_timed_condition_end_turn"])
    if "select_srd_power_word_stun_source" not in str(ending["effects"]):
        loops = [
            (effects, i)
            for effects in _effect_lists(ending["effects"])
            for i, effect in enumerate(effects)
            if effect.get("for_each", {}).get("as") == "_timed_condition_end_turn_source_id"
        ]
        if len(loops) != 1:
            raise ValueError("Power Word Stun requires the actual indexed end-turn save loop.")
        effects, index = loops[0]
        effects.insert(
            index,
            _call(
                "select_srd_power_word_stun_source",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
                ("_power_word_stun_source_id",),
            ),
        )
        guards = [
            effect
            for effects in _effect_lists(ending["effects"])
            for effect in effects
            if "_timed_condition_end_turn_source.repeat_save_timing == 'end_of_target_turn'"
            in effect.get("condition", "")
        ]
        if len(guards) != 1:
            raise ValueError("Power Word Stun requires the actual repeating-save source guard.")
        guards[0]["condition"] += (
            " and (_timed_condition_end_turn_source.spell_id != 'power_word_stun' or "
            "_timed_condition_end_turn_source_id == _power_word_stun_source_id)"
        )
        changed["resolve_srd_timed_condition_end_turn"] = ending

    restoration = deepcopy(tools["cast_spell_instantaneous_restoration"])
    if "power-word-heal-stand" not in str(restoration["effects"]):
        stands = [
            effect
            for effects in _effect_lists(restoration["effects"])
            for effect in effects
            if "use_prone_reaction_to_stand == true" in effect.get("condition", "")
        ]
        if len(stands) != 1:
            raise ValueError("Power Word Heal requires its actual post-restoration stand branch.")
        stand = stands[0]
        stand["condition"] = (
            "offers_prone_reaction_stand == true and target.reaction_available == true "
            "and 'prone' in target.conditions"
        )
        for guard in tools["consume_srd_reaction"].get("pre_conditions", []):
            stand["condition"] += (
                " and (" + re.sub(r"\bactor_state\b", "target", guard["condition"]) + ")"
            )
        stand["on_true"] = [
            {
                "request_choice": {
                    "choice_id": "power-word-heal-stand",
                    "prompt": "Power Word Heal: {_target_id}, use your Reaction to stand up?",
                    "options": [
                        {"id": "stand", "label": "Use my Reaction to stand up"},
                        {"id": "remain-prone", "label": "Remain Prone and keep my Reaction"},
                    ],
                },
                "store_as": "_power_word_heal_stand_choice",
            },
            _when(
                "_power_word_heal_stand_choice == 'stand'",
                [
                    _call(
                        "consume_srd_reaction",
                        {
                            "lasting_state": "state",
                            "actor_state": "{_target_id}",
                            "reaction": "power_word_heal_stand",
                        },
                    ),
                    _call("remove_srd_condition", {"target": "{_target_id}", "condition": "prone"}),
                    _set("_spell_prone_reaction_used", True),
                ],
            ),
        ]
        changed["cast_spell_instantaneous_restoration"] = restoration
    # Retain the old optional input for saved call compatibility; only the trusted
    # target choice controls standing. Hide it from newly generated tool schemas.
    for name, original in tools.items():
        legacy = next(
            (
                p
                for p in original.get("parameters", [])
                if p["name"] == "use_prone_reaction_to_stand"
            ),
            None,
        )
        if legacy is not None and legacy.get("model_hidden") is not True:
            tool = deepcopy(changed.get(name, original))
            parameter = next(p for p in tool["parameters"] if p["name"] == legacy["name"])
            parameter["model_hidden"] = True
            changed[name] = tool
    return _replace_tools(text, changed) if changed else text
