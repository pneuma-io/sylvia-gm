"""Divination damage reports actual loss after the complete source-owned packet."""

if __package__:
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _set, _tool, _when
    from .srd_timed_senses import _push
else:
    from srd_timed_actor_defenses import _call, _entity, _parameter, _set, _tool, _when
    from srd_timed_senses import _push


def _hit_points_tool(prefix="mind_spike"):
    """Bind the current target afresh instead of reusing the cast's earlier entity snapshot."""
    return _tool(
        "Read current authoritative HP and Temp HP for the damage owner's feedback.",
        [_entity("target_state")],
        [
            _set(f"_{prefix}_hp_plus_temp", "target_state.hp"),
            _when(
                "target_state.temp_hp != null",
                [
                    _set(
                        f"_{prefix}_hp_plus_temp",
                        f"_{prefix}_hp_plus_temp + target_state.temp_hp",
                    )
                ],
            ),
        ],
    )


def _loss_tool(prefix="mind_spike"):
    """Keep the final packet's mitigation, Temp HP and shared riders in the observed result."""
    return _tool(
        "Read actual HP plus Temp HP loss after the complete divination damage packet.",
        [_entity("target_state"), _parameter("before_total", "number")],
        [
            _call(
                f"read_srd_{prefix}_hit_points",
                {"target_state": "{_target_state_id}"},
                [f"_{prefix}_hp_plus_temp"],
            ),
            _set(f"_{prefix}_damage_loss", f"max(0, before_total - _{prefix}_hp_plus_temp)"),
        ],
    )


def deepen_mind_spike_feedback(changed):
    """Place the named final reader after damage; final packet composition preserves that seam."""
    changed["read_srd_mind_spike_hit_points"] = _hit_points_tool()
    changed["read_srd_mind_spike_damage_loss"] = _loss_tool()
    effects = changed["cast_srd_mind_spike"]["effects"]
    index = next(i for i, effect in enumerate(effects) if effect.get("call") == "apply_srd_damage")
    effects[index:index] = [
        _call(
            "read_srd_mind_spike_hit_points",
            {"target_state": "{_target_state_id}"},
            ["_mind_spike_hp_plus_temp"],
        ),
        _set("_mind_spike_before_damage", "_mind_spike_hp_plus_temp"),
    ]
    effects.insert(
        index + 3,
        _call(
            "read_srd_mind_spike_damage_loss",
            {"target_state": "{_target_state_id}", "before_total": "{_mind_spike_before_damage}"},
            ["_mind_spike_damage_loss"],
        ),
    )
    for effect in effects:
        if effect.get("set", {}).get("field") == "_divination_answer":
            effect["set"]["value"] = "_mind_spike_damage_loss"
    deepen_contact_feedback(changed)


def deepen_contact_feedback(changed):
    """Bracket Contact's real failure packet without changing its roll or condition source."""
    changed["read_srd_contact_hit_points"] = _hit_points_tool("contact")
    changed["read_srd_contact_damage_loss"] = _loss_tool("contact")

    def deepen(effects):
        for effect in effects:
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    deepen(effect[branch])
        for index, effect in enumerate(effects):
            if (
                effect.get("call") == "apply_srd_damage"
                and effect.get("params", {}).get("amount") == "{_contact_damage.total}"
            ):
                effects[index:index] = [
                    _call(
                        "read_srd_contact_hit_points",
                        {"target_state": "{_caster_state_id}"},
                        ["_contact_hp_plus_temp"],
                    ),
                    _set("_contact_before_damage", "_contact_hp_plus_temp"),
                ]
                effects[index + 3 : index + 3] = [
                    _call(
                        "read_srd_contact_damage_loss",
                        {
                            "target_state": "{_caster_state_id}",
                            "before_total": "{_contact_before_damage}",
                        },
                        ["_contact_damage_loss"],
                    ),
                    _push(
                        "_spell_detail_rows", {"label": "'Damage'", "value": "_contact_damage_loss"}
                    ),
                ]
                return

    deepen(changed["cast_spell_divination_spell"]["effects"])
