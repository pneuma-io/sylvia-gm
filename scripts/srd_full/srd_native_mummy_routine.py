"""Printed Mummy Multiattack retains two real Fists and one actual Dreadful Glare."""

from copy import deepcopy

if __package__:
    from .srd_native_undead_curses import MUMMY_BLOCK
    from .srd_spell_summoning import _choose, _created_undead_additional_profiles, _walk
    from .srd_timed_actor_defenses import _call, _load_runtime, _require, _set, _when
    from .srd_timed_senses import _replace_tools
else:
    from srd_native_undead_curses import MUMMY_BLOCK
    from srd_spell_summoning import _choose, _created_undead_additional_profiles, _walk
    from srd_timed_actor_defenses import _call, _load_runtime, _require, _set, _when
    from srd_timed_senses import _replace_tools


def _effect_groups(effects):
    """Find the current finite named-component spending body without copying a stale owner."""
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            yield from _effect_groups(effect.get(key, []))
        yield from _effect_groups(effect.get("for_each", {}).get("do", []))


def _routine_budget(original):
    """Reuse the actual action, finite counts and control suffix for this printed mixed routine."""
    groups = [
        group
        for group in _effect_groups(original["effects"])
        if any(
            e.get("condition") == "use_multiattack == true"
            and any(
                child.get("set", {}).get("field") == "_routine_entries"
                for child in e.get("on_true", [])
            )
            for e in group
        )
    ]
    if len(groups) != 1:
        raise ValueError("Preserve the actual one finite creature Multiattack spending body.")
    effects = deepcopy(groups[0])
    starts = [
        e
        for e in effects
        if e.get("condition") == "combat.attacks_remaining == null or combat.attacks_remaining == 0"
    ]
    if len(starts) != 1 or [e.get("call") for e in effects if e.get("call")] != [
        "refresh_srd_attack_availability",
        "finish_srd_control_attack",
    ]:
        raise ValueError("Keep the real action and final control/attack-availability suffix.")
    if starts[0]["on_true"][0] != _call(
        "spend_srd_action", {"magic_action": False, "control_action_kind": "attack"}
    ):
        raise ValueError("Mummy components must spend the same one real ordinary action.")
    tool = deepcopy(original)
    tool["description"] = "Spend one genuine Fist or Glare component of this printed native Mummy."
    tool["effects"] = [
        _call("qualify_srd_native_mummy_fist_action", {"actor_state": "{_actor_id}"}),
        _require(
            "attack_name == 'Rotting Fist' or attack_name == 'Dreadful Glare'",
            "Use only the printed Mummy's actual Fist or Glare action component.",
        ),
        _require(
            "len(actor.multiattack) == 2 and actor.multiattack[0].attack == 'Rotting Fist' and "
            "actor.multiattack[0].count == 2 and actor.multiattack[1].attack == 'Dreadful Glare' "
            "and actor.multiattack[1].count == 1",
            "Retain exactly the printed two Fists and one Dreadful Glare.",
        ),
        _when(
            "combat.attacks_remaining == null or combat.attacks_remaining == 0",
            [
                _set("combat.native_mummy_multiattack_active", "use_multiattack"),
                _set("combat.native_mummy_multiattack_actor", "_actor_id"),
            ],
        ),
        _require(
            "combat.native_mummy_multiattack_actor == _actor_id and "
            "combat.native_mummy_multiattack_active == use_multiattack",
            "Finish this same real native Mummy action and its remaining components.",
        ),
        *effects,
    ]
    return tool


def extend_runtime(text):
    """Bind real mixed components while retaining the current NPC Fist roll/packet owner."""
    row = _created_undead_additional_profiles()["mummy"]
    assert row["source_stat_block_sha256"] == MUMMY_BLOCK
    assert "Multiattack. The mummy makes two Rotting Fist at-" in row["source_stat_block"]
    assert "tacks and uses Dreadful Glare." in row["source_stat_block"]
    tools = _load_runtime(text)["tools"]
    name = "spend_srd_native_mummy_action_component"
    if name in tools:
        if tools[name] != _routine_budget(tools["spend_srd_creature_attack"]):
            raise ValueError("Preserve the captured actual Mummy finite component owner.")
        return text
    budget = _routine_budget(tools["spend_srd_creature_attack"])
    spend = deepcopy(tools["spend_srd_creature_attack"])
    spend["effects"] = [
        {
            "condition": "actor.created_spell_id == 'create_undead' and "
            "actor.created_form_id == 'mummy' and actor.form_attack_in_progress != true",
            "on_true": [
                _call(
                    name,
                    {
                        "actor": "{_actor_id}",
                        "attack_name": "{attack_name}",
                        "use_multiattack": "{use_multiattack}",
                    },
                )
            ],
            "on_false": spend["effects"],
        }
    ]
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    guards = [
        guard
        for guard in attack["pre_conditions"]
        if guard["error"]
        == "Use the actual printed single native Fist; its full Multiattack remains closed."
    ]
    if len(guards) != 1 or not guards[0]["condition"].endswith("and use_multiattack != true)"):
        raise ValueError("Open only the actual source-owned single Fist's Multiattack fence.")
    guards[0]["condition"] = (
        guards[0]["condition"].removesuffix("and use_multiattack != true)") + ")"
    )
    guards[0]["error"] = "Use the genuine printed Fist and its finite native action component."
    finish = deepcopy(tools["finish_srd_mummy_source_actor"])
    finish["effects"].append(
        _set(
            "actor_state.multiattack",
            [
                {"attack": "'Rotting Fist'", "count": 2},
                {"attack": "'Dreadful Glare'", "count": 1},
            ],
        )
    )
    glare = deepcopy(tools["use_srd_native_mummy_glare"])
    glare["parameters"].append(
        {"name": "use_multiattack", "type": "boolean", "required": False, "default": False}
    )
    spends = [e for e in _walk(glare["effects"]) if e.get("call") == "spend_srd_action"]
    if len(spends) != 1 or spends[0]["params"] != {"magic_action": False}:
        raise ValueError("Keep the actual standalone Glare's one source action-spending boundary.")
    standalone = deepcopy(spends[0])
    spends[0].clear()
    spends[0].update(
        _choose(
            "use_multiattack == true",
            [
                _call(
                    "spend_srd_creature_attack",
                    {
                        "actor": "{_actor_state_id}",
                        "attack_name": "Dreadful Glare",
                        "use_multiattack": True,
                    },
                )
            ],
            [standalone],
        )
    )
    return _replace_tools(
        text,
        {
            name: budget,
            "spend_srd_creature_attack": spend,
            "resolve_adjudicated_creature_attack": attack,
            "finish_srd_mummy_source_actor": finish,
            "use_srd_native_mummy_glare": glare,
        },
    )
