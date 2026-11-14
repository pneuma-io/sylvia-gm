"""Mummy native sight, saving throws and exact caster-turn condition lifetimes."""

from copy import deepcopy

if __package__:
    from .srd_native_undead_curses import MUMMY_BLOCK
    from .srd_spell_summoning import (
        _animated_attack_tools,
        _choose,
        _created_actor_attack_geometry_tool,
        _created_ghoul_claw_tools,
        _created_undead_additional_profiles,
        _zombie_source_profile,
    )
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _push, _replace_tools
else:
    from srd_native_undead_curses import MUMMY_BLOCK
    from srd_spell_summoning import (
        _animated_attack_tools,
        _choose,
        _created_actor_attack_geometry_tool,
        _created_ghoul_claw_tools,
        _created_undead_additional_profiles,
        _zombie_source_profile,
    )
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _push, _replace_tools


def _native_body_tool(require_command=False):
    """Retain existing created-body qualification without requiring a command for a receiver."""
    mummy = _created_undead_additional_profiles()["mummy"]
    assert mummy["source_stat_block_sha256"] == MUMMY_BLOCK
    assert "Wisdom Saving Throw: DC 11" in mummy["source_stat_block"]
    zombie_hash = _zombie_source_profile()["source_stat_block_sha256"]
    original = _animated_attack_tools()["qualify_srd_animated_zombie_attack"]

    def bind(value):
        if isinstance(value, str):
            return (
                value.replace(zombie_hash, MUMMY_BLOCK)
                .replace("animate_dead", "create_undead")
                .replace("zombie", "mummy")
                .replace("Zombie", "Mummy")
            )
        if isinstance(value, list):
            return [bind(child) for child in value]
        if isinstance(value, dict):
            return {key: bind(child) for key, child in value.items()}
        return value

    result = bind(original)
    if not require_command:
        result["effects"] = [
            effect
            for effect in result["effects"]
            if not (
                effect.get("call") == "refresh_srd_animated_undead_control"
                or effect.get("condition") == "_animated_control_active == true"
            )
        ]
    return result


def _glare_tools(tools):
    """Resolve printed Glare from actual scene sight, never an asserted visible-target flag."""
    body = _native_body_tool()
    can_act = next(p["condition"] for p in tools["resolve_adjudicated_creature_attack"][
        "pre_conditions"] if "actor.conditions" in p["condition"] and
        "incapacitated" in p["condition"]).replace("actor.", "actor_state.")
    geometry = _created_actor_attack_geometry_tool()
    cutoff = next(
        i
        for i, effect in enumerate(geometry["effects"])
        if effect.get("set", {}).get("field") == "_created_attack_targets"
    )
    geometry["effects"] = geometry["effects"][:cutoff] + [
        _require("_created_attack_distance <= 60", "Dreadful Glare requires actual range60 feet."),
        _call(
            "resolve_srd_scene_relation",
            {
                "actor_state": "{_actor_state_id}",
                "target_state": "{_target_state_id}",
            },
            ("_scene_can_see",),
        ),
        _require("_scene_can_see == true", "The actual native Mummy must see this creature."),
    ]
    projection = deepcopy(_created_ghoul_claw_tools()["reproject_srd_ghoul_claw_paralysis"])

    def fear(value):
        if isinstance(value, str):
            return (
                value.replace("ghoul", "mummy_glare")
                .replace("paralysis", "fear")
                .replace("paralyzed", "frightened")
                .replace("Paralyzed", "Frightened")
            )
        if isinstance(value, list):
            return [fear(child) for child in value]
        if isinstance(value, dict):
            return {key: fear(child) for key, child in value.items()}
        return value

    projection = fear(projection)
    resolve = _tool(
        "Resolve the genuine Mummy Glare receiving branch with one real DC11 Wisdom save.",
        [
            _entity("actor_state"),
            _entity("target_state"),
            _optional("lasting_state", resolve="entity", default="state"),
        ],
        [
            _call("qualify_srd_native_mummy_body", {"actor_state": "{_actor_state_id}"}),
            _require(
                "target_state.creature_type != null and target_state.is_object != true and "
                "target_state.is_alive == true",
                "Select an actual living creature.",
            ),
            _require(
                "actor_state.turns_started != null and actor_state.turns_started > 0",
                "Retain this Mummy's genuine turn counter for next-turn expiry.",
            ),
            _call(
                "qualify_srd_native_mummy_glare_geometry",
                {
                    "actor_state": "{_actor_state_id}",
                    "target_state": "{_target_state_id}",
                },
            ),
            _set("_mummy_glare_outcome", "'immune'"),
            _set("_mummy_glare_save_total", None),
            _set("_mummy_glare_save_roll", None),
            _set("_mummy_glare_source_id", None),
            _set("_mummy_glare_immunity_until", 0),
            _when(
                "target_state.mummy_glare_immunities != null and "
                "_actor_state_id in target_state.mummy_glare_immunities",
                [
                    _set(
                        "_mummy_glare_immunity_until",
                        "target_state.mummy_glare_immunities[_actor_state_id]",
                    ),
                ],
            ),
            _when(
                "lasting_state.lasting_tick >= _mummy_glare_immunity_until",
                [
                    _call(
                        "check_srd_target_condition_immunity",
                        {
                            "target_state": "{_target_state_id}",
                            "condition": "frightened",
                            "source_state_id": "{_actor_state_id}",
                        },
                        ("_source_condition_immune",),
                    ),
                    _when(
                        "_source_condition_immune != true",
                        [
                            _set("_mummy_glare_character", None),
                            _when(
                                "target_state.creature_kind == 'character'",
                                [
                                    _set("_mummy_glare_character", "_target_state_id"),
                                ],
                            ),
                            _call(
                                "resolve_srd_divination_save",
                                {
                                    "target_state": "{_target_state_id}",
                                    "actor": "{_mummy_glare_character}",
                                    "ability": "wisdom",
                                    "dc": 11,
                                },
                                ("_divination_save_success", "_d20_test", "_adjusted_total"),
                            ),
                            _set("_mummy_glare_save_roll", "_d20_test"),
                            _set("_mummy_glare_save_total", "_adjusted_total"),
                            _choose(
                                "_divination_save_success == true",
                                [
                                    _set("_mummy_glare_outcome", "'success'"),
                                    _when(
                                        "target_state.mummy_glare_immunities == null",
                                        [
                                            _set("target_state.mummy_glare_immunities", {}),
                                        ],
                                    ),
                                    _set(
                                        "target_state.mummy_glare_immunities.{_actor_state_id}",
                                        "lasting_state.lasting_tick + 28800",
                                    ),
                                ],
                                [
                                    _set("_mummy_glare_outcome", "'failure'"),
                                    _require(
                                        "actor_state.mummy_glare_source_ids == null or "
                                        "len(actor_state.mummy_glare_source_ids) < 128",
                                "Retire existing Glare sources before "
                                "exhausting this index.",
                                    ),
                                    _call(
                                        "install_srd_timed_condition_source",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "caster_state": "{_actor_state_id}",
                                            "lasting_state": "{_lasting_state_id}",
                                            "spell": "create_undead",
                                            "condition": "frightened",
                                            "duration_rounds": 0,
                                            "save_dc": 11,
                                            "repeat_save_timing": "none",
                                            "save_ability": "wisdom",
                                        },
                                        ("_timed_condition_source_id",),
                                    ),
                                    _set("_mummy_glare_source_id", "_timed_condition_source_id"),
                                    _set(
                                        "_mummy_glare_stamp",
                                        {
                                            "native_profile": "'mummy_glare'",
                                            "native_attack": "'Dreadful Glare'",
                                            "native_body_source": "_actor_state_id",
                                            "native_body_block_sha256": repr(MUMMY_BLOCK),
                                "native_body_source_sequence": (
                                    "lasting_state.timed_condition_sources["
                                    "_timed_condition_source_id].sequence"),
                                "native_caster_end_turn": (
                                    "actor_state.turns_started + 1"),
                                            "dispel_ongoing": False,
                                            "source_is_magical": False,
                                            "effect_kind": "'physical'",
                                            "cast_id": "''",
                                            "ongoing_duration": False,
                                        },
                                    ),
                                    _set(
                                        "lasting_state.timed_condition_sources.{_timed_condition_source_id}",
                                        "merge(lasting_state.timed_condition_sources["
                                        "_timed_condition_source_id], _mummy_glare_stamp)",
                                    ),
                                    _set("_mummy_glare_index", []),
                                    _when(
                                        "actor_state.mummy_glare_source_ids != null",
                                        [
                                            _set(
                                                "_mummy_glare_index",
                                                "actor_state.mummy_glare_source_ids",
                                            ),
                                        ],
                                    ),
                                    _push("_mummy_glare_index", "_timed_condition_source_id"),
                                    _set(
                                        "actor_state.mummy_glare_source_ids", "_mummy_glare_index"
                                    ),
                                    _set("actor_state.has_native_mummy_glare_sources", True),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    expire = _tool(
        "Retire only this Mummy's authenticated Glare at its actual next completed turn.",
        [_entity("actor_state"), _optional("lasting_state", resolve="entity", default="state")],
        [
            _set("_mummy_glare_retained", []),
            _set("_mummy_glare_due_ids", "actor_state.mummy_glare_source_ids"),
            _loop(
                "_mummy_glare_due_ids",
                "_mummy_glare_due_id",
                [
                    _set(
                        "_mummy_glare_due",
                        "lasting_state.timed_condition_sources[_mummy_glare_due_id]",
                    ),
                    _when(
                        "_mummy_glare_due.installed == true and "
                        "_mummy_glare_due.native_body_source == _actor_state_id and "
                        "_mummy_glare_due.native_attack == 'Dreadful Glare'",
                        [
                            _when(
                                "_mummy_glare_due.native_body_block_sha256 == '"
                                + MUMMY_BLOCK
                                + "' and _mummy_glare_due.native_body_source_sequence == "
                                "_mummy_glare_due.sequence",
                                [
                                    _choose(
                                    "actor_state.turns_started >= "
                                    "_mummy_glare_due.native_caster_end_turn",
                                        [
                                            _call(
                                                "retire_srd_timed_condition_source",
                                                {
                                                    "source_id": "{_mummy_glare_due_id}",
                                                    "lasting_state": "{_lasting_state_id}",
                                                },
                                            ),
                                            _call(
                                                "reproject_srd_native_mummy_glare_fear",
                                                {
                                                "target_state": (
                                                    "{_mummy_glare_due.target_state_id}"),
                                                    "lasting_state": "{_lasting_state_id}",
                                                },
                                            ),
                                        ],
                                        [_push("_mummy_glare_retained", "_mummy_glare_due_id")],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("actor_state.mummy_glare_source_ids", "_mummy_glare_retained"),
            _set("actor_state.has_native_mummy_glare_sources", "len(_mummy_glare_retained) > 0"),
        ],
    )
    action = _tool(
        "Use the printed standalone Glare action on this Mummy's actual commanded turn.",
        [_entity("actor_state"), _entity("target_state")],
        [
            _require(
                "combat.turn_active == true and combat.active_actor == _actor_state_id",
                "Use the actual native Mummy's active turn.",
            ),
            _require(can_act, "The native Mummy cannot use Glare while incapacitated."),
            _call("qualify_srd_native_mummy_glare_action", {"actor_state": "{_actor_state_id}"}),
            _call("spend_srd_action", {"magic_action": False}),
            _call(
                "resolve_srd_native_mummy_glare",
                {
                    "actor_state": "{_actor_state_id}",
                    "target_state": "{_target_state_id}",
                },
                (
                    "_mummy_glare_outcome",
                    "_mummy_glare_save_total",
                    "_mummy_glare_save_roll",
                    "_mummy_glare_source_id",
                ),
            ),
        ],
    )
    return {
        "qualify_srd_native_mummy_body": body,
        "qualify_srd_native_mummy_glare_action": _native_body_tool(require_command=True),
        "qualify_srd_native_mummy_glare_geometry": geometry,
        "resolve_srd_native_mummy_glare": resolve,
        "use_srd_native_mummy_glare": action,
        "reproject_srd_native_mummy_glare_fear": projection,
        "expire_srd_native_mummy_glare_sources": expire,
    }


def extend_runtime(text):
    """Emit source-qualified Glare receivers without opening an incomplete public body."""
    tools = _load_runtime(text)["tools"]
    changed = {name: tool for name, tool in _glare_tools(tools).items() if tools.get(name) != tool}
    return _replace_tools(text, changed) if changed else text


def bind_mummy_glare_turn_consumers(text):
    """Observe genuine caster turn completion after the final shared turn lifecycle."""
    tools = _load_runtime(text)["tools"]
    end = deepcopy(tools["end_combat_turn"])
    if "expire_srd_native_mummy_glare_sources" in str(end["effects"]):
        return text
    end["effects"].append(
        _when(
            "actor.has_native_mummy_glare_sources == true",
            [
                _call("expire_srd_native_mummy_glare_sources", {"actor_state": "{_actor_id}"}),
            ],
        )
    )
    return _replace_tools(text, {"end_combat_turn": end})
