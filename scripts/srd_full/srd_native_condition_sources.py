"""Authenticate retained physical attack conditions independently of their creating spell."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _call, _emit
    from .srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _set, _tool, _when
else:
    from srd_actor_spell_modifiers import _call, _emit
    from srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _set, _tool, _when

GHOUL_BLOCK_SHA256 = "ca93598a7266b964cec2a4c96725334f2e20800bfa8ccc9693c82aa0657870e1"
GHAST_BLOCK_SHA256 = "9ada001041b41a1e129de34eeabbecc5672dabdd2d455d08e105a66c6f2cfe86"
FREEDOM_FENCE = "Join Ghoul Claw's authenticated native Freedom classification first."


def _tools():
    """Require both the allocated condition witness and the original admitted body record."""
    authentication = _tool(
        "Authenticate printed Ghoul/Ghast conditions from retained source provenance.",
        [_parameter("source_id"), _entity("lasting_state")],
        [
            _set("_native_condition_source", False),
            _set("_native_condition_record", "lasting_state.timed_condition_sources[source_id]"),
            _when(
                "_native_condition_record != null and "
                "_native_condition_record.installed == true and "
                "_native_condition_record.native_body_source != null and "
                "_native_condition_record.sequence != null and "
                "_native_condition_record.native_body_source_sequence == "
                "_native_condition_record.sequence",
                [
                    _set(
                        "_native_condition_body",
                        "lasting_state.created_actor_records["
                        "_native_condition_record.native_body_source]",
                    ),
                    _when(
                        "_native_condition_body != null and "
                        "_native_condition_record.spell_id == 'create_undead' and "
                        "_native_condition_record.caster_state_id == "
                        "_native_condition_record.native_body_source",
                        [
                            _set(
                                "_native_condition_body_valid",
                                "_native_condition_body.spell == 'create_undead' and "
                                "_native_condition_body.individual == "
                                "_native_condition_record.native_body_source and "
                                "_native_condition_body.body_source != null and "
                                "_native_condition_body.body_source_id != null and "
                                "len(_native_condition_body.body_source) > 0 and "
                                "len(_native_condition_body.body_source_id) > 0",
                            ),
                            _when(
                                "_native_condition_record.condition == 'paralyzed' and "
                                "_native_condition_record.native_attack == 'Claw' and "
                                "_native_condition_record.native_body_block_sha256 == '"
                                + GHOUL_BLOCK_SHA256
                                + "' and _native_condition_body.source_block_sha256 == '"
                                + GHOUL_BLOCK_SHA256
                                + "'",
                                [_set("_native_condition_source", "_native_condition_body_valid")],
                            ),
                            _when(
                                "_native_condition_record.native_body_block_sha256 == '"
                                + GHAST_BLOCK_SHA256
                                + "' and _native_condition_body.source_block_sha256 == '"
                                + GHAST_BLOCK_SHA256
                                + "'",
                                [
                                    _set(
                                        "_native_condition_source",
                                        "_native_condition_body_valid and "
                                        "((_native_condition_record.condition == 'paralyzed' and "
                                        "_native_condition_record.native_attack == 'Claw') or "
                                        "(_native_condition_record.condition == 'poisoned' and "
                                        "_native_condition_record.native_attack == 'Stench'))",
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    projection = _tool(
        "Preserve only authenticated physical Claw paralysis through active Freedom of Movement.",
        [_entity("target_state"), _entity("lasting_state")],
        [
            _set("_freedom_native_paralyzed", False),
            _set("_freedom_native_index", []),
            _when(
                "target_state.ghoul_claw_source_ids != null",
                [_set("_freedom_native_index", "target_state.ghoul_claw_source_ids")],
            ),
            _when(
                "target_state.ghast_claw_source_ids != null",
                [
                    _set("_freedom_native_ghast_index", "target_state.ghast_claw_source_ids"),
                    {
                        "for_each": {
                            "collection": "_freedom_native_ghast_index",
                            "as": "_freedom_native_ghast_id",
                            "do": [
                                {
                                    "list_push": {
                                        "list": "_freedom_native_index",
                                        "value": "_freedom_native_ghast_id",
                                    }
                                }
                            ],
                        }
                    },
                ],
            ),
            {
                "for_each": {
                    "collection": "_freedom_native_index",
                    "as": "_freedom_native_id",
                    "do": [
                        _call(
                            "check_srd_native_condition_source",
                            {
                                "source_id": "{_freedom_native_id}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                            ("_native_condition_source",),
                        ),
                        _set(
                            "_freedom_native_record",
                            "lasting_state.timed_condition_sources[_freedom_native_id]",
                        ),
                        _when(
                            "_native_condition_source == true and "
                            "_freedom_native_record.condition == 'paralyzed' and "
                            "_freedom_native_record.target_state_id == _target_state_id",
                            [
                                _call(
                                    "check_srd_target_condition_immunity",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "condition": "paralyzed",
                                        "source_state_id": (
                                            "{_freedom_native_record.caster_state_id}"
                                        ),
                                        "source_is_magical": False,
                                    },
                                    ("_source_condition_immune",),
                                ),
                                _set(
                                    "lasting_state.timed_condition_sources.{_freedom_native_id}.suppressed",
                                    "_source_condition_immune == true or "
                                    "_freedom_native_record.body_immunity_suppressed == true",
                                ),
                                _when(
                                    "_source_condition_immune != true and "
                                    "_freedom_native_record.body_immunity_suppressed != true",
                                    [_set("_freedom_native_paralyzed", True)],
                                ),
                            ],
                        ),
                    ],
                }
            },
            _when(
                "_freedom_native_paralyzed == true",
                [
                    {"apply_condition": {"target": "target_state", "condition": "paralyzed"}},
                    _set("target_state.timed_condition_paralyzed", True),
                ],
            ),
        ],
    )
    return {
        "check_srd_native_condition_source": authentication,
        "resolve_srd_freedom_native_conditions": projection,
    }


def bind_runtime(text):
    """Join only authentic Claw installers and the final shared suppression projection."""
    tools = _load_runtime(text)["tools"]
    changed = _tools()
    suppression = deepcopy(tools["resolve_srd_timed_actor_condition_suppression"])
    suppression["effects"] = [
        effect
        for effect in suppression["effects"]
        if "resolve_srd_freedom_native_conditions" not in str(effect)
    ]
    suppression["effects"].append(
        _when(
            "(target_state.has_ghoul_claw_sources == true or "
            "target_state.has_ghast_claw_sources == true) and "
            "target_state.spell_freedom_of_movement == true",
            [
                _call(
                    "resolve_srd_freedom_native_conditions",
                    {
                        "target_state": "{_target_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                )
            ],
        )
    )
    changed["resolve_srd_timed_actor_condition_suppression"] = suppression
    for form in ("ghoul", "ghast"):
        target_name = f"qualify_srd_{form}_claw_target"
        if target_name in tools:
            target = deepcopy(tools[target_name])
            for effect in target["effects"]:
                if effect.get("condition") == "target_state.has_actor_spell_modifiers == true":
                    effect["on_true"] = [
                        child
                        for child in effect["on_true"]
                        if child.get("require", {}).get("error")
                        != FREEDOM_FENCE.replace("Ghoul", form.title())
                    ]
            changed[target_name] = target
        claw_name = f"resolve_srd_created_{form}_claw"
        if claw_name in tools:
            claw = deepcopy(tools[claw_name])

            def after_stamp(effects, form=form):
                for index, effect in enumerate(list(effects)):
                    if (
                        effect.get("set", {}).get("field")
                        == f"target_state.has_{form}_claw_sources"
                    ):
                        if index + 1 < len(effects) and effects[index + 1].get("call") == (
                            "refresh_srd_timed_condition_target"
                        ):
                            continue
                        effects.insert(
                            index + 1,
                            _call(
                                "refresh_srd_timed_condition_target",
                                {
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            ),
                        )
                    for branch in ("on_true", "on_false"):
                        if branch in effect:
                            after_stamp(effect[branch])

            after_stamp(claw["effects"])
            changed[claw_name] = claw
    return _emit(text, changed)
