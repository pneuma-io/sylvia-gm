"""Source-owned Luck and Resourceful use the actual D20 choice and Dice owners."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

if __package__:
    from .srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_divination_spells import _normalize
    from .srd_reincarnate_native_traits import SOURCE, _validate_source
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_divination_spells import _normalize
    from srd_reincarnate_native_traits import SOURCE, _validate_source
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools

ROOT = Path(__file__).resolve().parents[2]


def _source():
    _validate_source()
    for filename, page, expected in (
        ("playing_the_game", 8, "351f4ab4eeda5e8d06ce8cefc08baf68d506fa468b47eceb3801c57f08a6a08e"),
        ("rules_glossary", 183, "20c6ad564d9d71c81cc289c376cafa79b86de4a4c0080becc35ec449380988bc"),
    ):
        corpus = json.loads(
            (ROOT / f"rulesets/dnd-srd-5.2.1/content/corpus/{filename}.json").read_text()
        )
        row = next(row for row in corpus["page_records"] if row["page"] == page)
        if (
            row["text_sha256"] != expected
            or hashlib.sha256(row["text"].encode()).hexdigest() != expected
        ):
            raise ValueError("Retain the pinned actual reroll and Inspiration rules.")


def _reroll_tool():
    """The private producer accepts only the actual owning D20's fresh rolled object."""
    options = [
        _set(
            "_native_input_roll",
            {
                "count": "roll_count",
                "sides": "roll_sides",
                "results": "roll_results",
                "modifier": "roll_modifier",
                "total": "roll_total",
            },
        ),
        _set("_native_d20_original", "_native_input_roll"),
        _set("_native_d20_reroll", None),
        _set("_native_reroll_options", []),
        _push("_native_reroll_options", {"id": "'keep'", "label": "'Keep these rolls'"}),
        _set("_native_luck", False),
        _when(
            "actor_state.reincarnate_native_traits != null and actor_state.species == 'halfling'",
            [
                _call("qualify_srd_reincarnate_native_body", {"target_state": "{_actor_state_id}"}),
                _set("_native_luck", True),
            ],
        ),
        _require(
            "_native_input_roll.sides == 20 and len(_native_input_roll.results) >= 1 and "
            "len(_native_input_roll.results) <= 2",
            "Use only the actual owning D20 Test's one or two native dice.",
        ),
        _set("_native_inspiration", "actor_state.heroic_inspiration == true"),
        _when(
            "_native_inspiration == true",
            [
                _require(
                    "actor_state.heroic_inspiration_grant.owner == _actor_state_id and "
                    "actor_state.heroic_inspiration_grant.kind == 'human_resourceful' and "
                    "actor_state.heroic_inspiration_grant.source_sha256 == '" + SOURCE + "'",
                    "Retain this actual Resourceful Heroic Inspiration grant.",
                ),
            ],
        ),
    ]
    for index, ordinal in ((0, "first"), (1, "second")):
        options.append(
            _when(
                f"len(_native_input_roll.results) > {index}",
                [
                    _when(
                        f"_native_luck == true and _native_input_roll.results[{index}] == 1",
                        [
                            _push(
                                "_native_reroll_options",
                                {
                                    "id": repr("luck_" + ordinal),
                                    "label": repr("Luck: reroll the " + ordinal + " d20"),
                                },
                            )
                        ],
                    ),
                    _when(
                        "_native_inspiration == true",
                        [
                            _push(
                                "_native_reroll_options",
                                {
                                    "id": repr("heroic_" + ordinal),
                                    "label": repr(
                                        "Heroic Inspiration: reroll the " + ordinal + " d20"
                                    ),
                                },
                            )
                        ],
                    ),
                ],
            )
        )
    options.extend(
        [
            _set("_d20_test", "_native_input_roll"),
            _when(
                "len(_native_reroll_options) > 1",
                [
                    {
                        "request_choice": {
                            "choice_id": "srd-native-d20-reroll",
                            "prompt": "You rolled {_native_d20_original.results}. "
                            "Keep them or reroll one die?",
                            "options": "_native_reroll_options",
                        },
                        "store_as": "_native_reroll_choice",
                    },
                    _when(
                        "_native_reroll_choice != 'keep'",
                        [
                            _set("_native_reroll_index", 0),
                            _when(
                                "_native_reroll_choice == 'luck_second' or "
                                "_native_reroll_choice == 'heroic_second'",
                                [_set("_native_reroll_index", 1)],
                            ),
                            {"dice": {"expression": "d20"}, "store_as": "_native_d20_reroll"},
                            _set("_native_reroll_results", []),
                            _set("_native_reroll_position", 0),
                            _each(
                                "roll_results",
                                "_native_face",
                                [
                                    _when(
                                        "_native_reroll_position == _native_reroll_index",
                                        [
                                            _push(
                                                "_native_reroll_results",
                                                "_native_d20_reroll.total",
                                            )
                                        ],
                                        [_push("_native_reroll_results", "_native_face")],
                                    ),
                                    _set("_native_reroll_position", "_native_reroll_position + 1"),
                                ],
                            ),
                            _set("_native_reroll_total", "max(_native_reroll_results)"),
                            _when(
                                "use_lower == true",
                                [
                                    _set("_native_reroll_total", "min(_native_reroll_results)"),
                                ],
                            ),
                            _set(
                                "_d20_test",
                                {
                                    "count": "_native_input_roll.count",
                                    "sides": "_native_input_roll.sides",
                                    "results": "_native_reroll_results",
                                    "modifier": "_native_input_roll.modifier",
                                    "total": "_native_reroll_total",
                                },
                            ),
                            _when(
                                "_native_reroll_choice == 'heroic_first' or "
                                "_native_reroll_choice == 'heroic_second'",
                                [_set("actor_state.heroic_inspiration", False)],
                            ),
                        ],
                    ),
                ],
            ),
        ]
    )
    return _tool(
        "Offer one immediate source-owned reroll of the actual D20 Test; use its new face.",
        [
            _entity("actor_state"),
            _param("roll_results", "array", items={"type": "integer"}),
            *[
                _param("roll_" + field, "integer")
                for field in ("count", "sides", "modifier", "total")
            ],
            _param("use_lower", "boolean"),
        ],
        options,
    )


def _grant_tools():
    return {
        "grant_srd_resourceful_inspiration": _tool(
            "Grant actual Resourceful after a completed Long Rest; keep at most one instance.",
            [_entity("target_state")],
            [
                _call(
                    "qualify_srd_reincarnate_native_body", {"target_state": "{_target_state_id}"}
                ),
                _require(
                    "target_state.species == 'human'",
                    "Resourceful belongs to the actual Human body.",
                ),
                _when(
                    "target_state.heroic_inspiration != true",
                    [
                        _call(
                            "install_srd_resourceful_inspiration",
                            {
                                "recipient_state": "{_target_state_id}",
                                "recipient_character": "{_target_state_id}",
                                "source_state": "{_target_state_id}",
                            },
                        ),
                    ],
                ),
            ],
        ),
        "install_srd_resourceful_inspiration": _tool(
            "Install one Resourceful instance on its actual player character recipient.",
            [
                _entity("recipient_state"),
                _entity("source_state"),
                _param("recipient_character", resolve="participant"),
            ],
            [
                _require(
                    "recipient_character.kind == 'player_character' and "
                    "recipient_character.validation_status == 'validated' and "
                    "recipient_character.package_id == 'wotc.srd.5.2.1' and "
                    "_recipient_character_id == _recipient_state_id and "
                    "recipient_state.heroic_inspiration != true",
                    "Give the instance only to an actual player character who lacks it.",
                ),
                _set("recipient_state.heroic_inspiration", True),
                _set(
                    "recipient_state.heroic_inspiration_grant",
                    {
                        "owner": "_recipient_state_id",
                        "source_actor": "_source_state_id",
                        "kind": "'human_resourceful'",
                        "source_sha256": repr(SOURCE),
                    },
                ),
            ],
        ),
    }


def extend_runtime(text):
    """Bind immediately after actual native traits; non-D20 Inspiration producers stay explicit."""
    _source()
    tools = _load_runtime(text)["tools"]
    if "resolve_srd_reincarnate_d20_reroll" in tools:
        return text
    if "qualify_srd_reincarnate_native_body" not in tools:
        raise ValueError("Install the actual source-qualified new-body trait owner first.")
    changed = _grant_tools()
    changed["resolve_srd_reincarnate_d20_reroll"] = _reroll_tool()
    d20 = deepcopy(tools["d20_test"])
    pending = [d20["effects"]]
    matches = 0
    while pending:
        sequence = pending.pop()
        for index, effect in enumerate(tuple(sequence)):
            if effect.get("store_as") == "_d20_test" and "dice" in effect:
                if effect["dice"]["expression"] != "d20":
                    raise ValueError("Keep the genuine native D20 producer.")
                matches += 1
                sequence.insert(
                    index + 1,
                    _when(
                        "actor_state.heroic_inspiration == true or "
                        "(actor_state.reincarnate_native_traits != null and "
                        "actor_state.species == 'halfling')",
                        [
                            _set("_native_roll_faces", []),
                            _push("_native_roll_faces", "_d20_test.results[0]"),
                            _when(
                                "len(_d20_test.results) == 2",
                                [
                                    _push("_native_roll_faces", "_d20_test.results[1]"),
                                ],
                            ),
                            _call(
                                "resolve_srd_reincarnate_d20_reroll",
                                {
                                    "actor_state": "{_actor_state_id}",
                                    **{
                                        "roll_" + field: "{_d20_test." + field + "}"
                                        for field in (
                                            "count",
                                            "sides",
                                            "modifier",
                                            "total",
                                        )
                                    },
                                    "roll_results": "{_native_roll_faces}",
                                    "use_lower": "{_effective_disadvantage == true "
                                    "and _effective_advantage == false}",
                                },
                                ("_d20_test", "_native_d20_original", "_native_d20_reroll"),
                            ),
                        ],
                    ),
                )
            pending.extend(effect[key] for key in ("on_true", "on_false") if key in effect)
            if "for_each" in effect:
                pending.append(effect["for_each"]["do"])
    if matches != 1:
        raise ValueError(
            "Bind to exactly one actual native D20 producer before its result calculation."
        )
    changed["d20_test"] = d20
    rest = deepcopy(tools["complete_srd_rest"])
    rest["effects"].append(
        _when(
            "target_state.rest_kind == 'long' and target_state.reincarnate_native_traits != null "
            "and target_state.species == 'human'",
            [
                _call(
                    "grant_srd_resourceful_inspiration",
                    {
                        "target_state": "{_target_state_id}",
                    },
                )
            ],
        )
    )
    changed["complete_srd_rest"] = rest
    _normalize(changed)
    return _replace_tools(text, changed)
