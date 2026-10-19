"""Author the Riichi package as ordinary bounded Sylvia Ruleset declarations."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml

NS = "pneuma.riichi-mahjong"
ROOT = Path(__file__).resolve().parents[1] / "rulesets" / "riichi-mahjong"


def literal(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, str) else value


def set_(field, value):
    return {"set": {"field": field, "value": value}}


def require(condition, error):
    return {"require": {"condition": condition, "error": error}}


def branch(condition, yes, no=()):
    return {"condition": {"condition": condition, "on_true": list(yes), "on_false": list(no)}}


def each(collection, variable, effects):
    return {"for_each": {"collection": collection, "as": variable, "do": effects}}


def dora_indexes(destination):
    """Reveal a declared concealed kan before robbery while delayed indicators stay pending."""
    return [
        set_("_visible_indicators", "1 + state.kan_count - state.kan_pending_dora"),
        branch(
            "state.phase == 'kan_response' and state.pending_kan.op == 'ankan'",
            [set_("_visible_indicators", "_visible_indicators + 1")],
        ),
        set_(destination, "indices(_visible_indicators)"),
    ]


def call(name, params=None, exports=None):
    resolved, prelude = {}, []
    for key, value in (params or {}).items():
        if (
            isinstance(value, str)
            and value.startswith("{")
            and value.endswith("}")
            and not re.fullmatch(r"\{[A-Za-z_][\w.]*\}", value)
        ):
            variable = "_argument_" + key
            prelude.append(set_(variable, value[1:-1]))
            value = "{" + variable + "}"
        resolved[key] = value
    result = {"call": name, "params": resolved}
    if exports:
        result["export_context"] = {key: key for key in exports}
    return branch("true", [*prelude, result]) if prelude else result


def push(destination, value):
    return {"list_push": {"list": destination, "value": value}}


def parameter(name, kind="string", **kwargs):
    return {"name": name, "type": kind, "source": "choice", **kwargs}


ACTOR = {
    "name": "actor",
    "type": "entity_id",
    "source": "state",
    "from_state": "state.actor",
    "resolve": "entity",
}
PRIVATE_ACTOR = parameter("actor", "entity_id", resolve="entity")


def family(parameters, effects, pre=(), metadata=None):
    return {
        "parameters": parameters,
        "effects": effects,
        "pre_conditions": [{"condition": condition, "error": error} for condition, error in pre],
        "narrative": {},
        **({"metadata": metadata} if metadata else {}),
    }


def hand_defaults():
    return {
        "hand": [],
        "melds": [],
        "discards": [],
        "riichi": False,
        "double_riichi": False,
        "ippatsu": False,
        "temporary_furiten": False,
        "riichi_furiten": False,
        "draw_count": 0,
        "last_draw": None,
        "rinshan": False,
        "forbidden_discards": [],
        "choices": [],
        "decision_actions": [],
        "riichi_waits": [],
        "pao_dragon": None,
        "pao_wind": None,
        "revealed": False,
    }


def source():
    tiles = [
        {
            "kind": index // 4,
            "red": index in (16, 52, 88),
            "label": (
                str(index // 4 % 9 + 1) + "mps"[index // 36]
                if index < 108
                else ["East", "South", "West", "North", "White", "Green", "Red"][index // 4 - 27]
            ),
        }
        for index in range(136)
    ]
    for tile in tiles:
        if tile["red"]:
            tile["label"] += " (red)"
    patterns = [[kind, kind] for kind in range(34)] + [[kind] * 3 for kind in range(34)]
    patterns += [
        [suit * 9 + rank + offset for offset in range(3)] for suit in range(3) for rank in range(7)
    ]
    families = {}

    families["reset_hand"] = family([PRIVATE_ACTOR], [set_("actor.game", hand_defaults())])
    families["draw"] = family(
        [PRIVATE_ACTOR],
        [
            require("len(state.live) > 0", "The live wall is empty."),
            set_("_tile", "state.live[0]"),
            set_("_hand", "joined(actor.game.hand, one(_tile))"),
            set_("actor.game.hand", "_hand"),
            set_("actor.game.last_draw", "_tile"),
            set_("actor.game.draw_count", "actor.game.draw_count + 1"),
            set_("actor.game.temporary_furiten", False),
            set_("actor.game.rinshan", False),
            set_("state.live", "portion(state.live, 1, len(state.live))"),
            set_("state.phase", literal("discard")),
        ],
    )
    families["new_hand"] = family(
        [],
        [
            {"shuffle": {"collection": "indices(136)"}, "store_as": "_wall"},
            set_("state.dead", "portion(_wall, 122, 136)"),
            set_("state.live", "portion(_wall, 52, 122)"),
            set_("state.kan_count", 0),
            set_("state.kan_pending_dora", 0),
            set_("state.any_call", False),
            set_("state.last_discard", None),
            set_("state.responses", []),
            set_("state.pending_calls", []),
            set_("_seat_ids", "state.seats"),
            set_("_index", 0),
            each(
                "_seat_ids",
                "_seat",
                [
                    call("_reset_hand", {"actor": "{_seat}"}),
                    call(
                        "_deal_seat", {"actor": "{_seat}", "tiles": "{_wall}", "offset": "{_index}"}
                    ),
                    set_("_index", "_index + 1"),
                ],
            ),
            set_("state.actor", "state.seats[state.dealer]"),
            call("_draw", {"actor": "{state.actor}"}),
        ],
    )
    families["deal_seat"] = family(
        [
            PRIVATE_ACTOR,
            parameter("tiles", "array", items={"type": "integer"}),
            parameter("offset", "integer"),
        ],
        [set_("actor.game.hand", "portion(tiles, offset * 13, (offset + 1) * 13)")],
    )

    create_seats = []
    for seat, suffix, name in (
        (0, ".seat", None),
        (1, ".right", "Right opponent"),
        (2, ".across", "Across opponent"),
        (3, ".left", "Left opponent"),
    ):
        identity = "_player_id + " + literal(suffix)
        create_seats.append(
            {
                "create_entity": {
                    "id": identity,
                    "value": {
                        "name": "player.data.name" if seat == 0 else literal(name),
                        "seat": seat,
                        "participant_id": "_player_id" if seat == 0 else None,
                        "control": literal("player" if seat == 0 else "gm"),
                        "persona": literal(
                            "Play a thoughtful, friendly club match. Respect legal "
                            "choices and base tactics on your own hand and public play."
                        ),
                        "score": 25000,
                        "game": hand_defaults(),
                    },
                }
            }
        )
    families["start_game"] = family(
        [
            parameter("player", "entity_id", resolve="participant"),
            parameter("match_format", "enum", enum_values=["east_only", "east_south"]),
        ],
        [
            require(
                "len(_player_id) <= 100",
                "The selected player's identity is too long for table seats.",
            ),
            branch(
                "state.seats == null",
                create_seats,
                [
                    require(
                        "state.player == _player_id",
                        "Use this campaign's existing player for another match.",
                    ),
                ],
            ),
            set_("state.player", "_player_id"),
            set_(
                "state.seats",
                [
                    "_player_id + '.seat'",
                    "_player_id + '.right'",
                    "_player_id + '.across'",
                    "_player_id + '.left'",
                ],
            ),
            set_("_seat_ids", "state.seats"),
            each("_seat_ids", "_seat", [call("_reset_score", {"actor": "{_seat}"})]),
            set_("state.match_format", "match_format"),
            set_("state.round_wind", 0),
            set_("state.dealer", 0),
            set_("state.honba", 0),
            set_("state.sticks", 0),
            branch("state.sequence == null", [set_("state.sequence", 0)]),
            set_("state.finished", False),
            set_("state.match_result", None),
            set_("state.message", literal("Match started.")),
            call("_new_hand"),
            call("_refresh"),
            {"publish": {"fields": {"message": "state.message", "match_format": "match_format"}}},
        ],
        pre=[
            ("state.phase == null or state.phase == 'finished'", "Finish the current match first.")
        ],
        metadata={"model_behavior": "resolves_player_intent"},
    )
    families["reset_score"] = family([PRIVATE_ACTOR], [set_("actor.score", 25000)])

    families["refresh"] = family(
        [],
        [
            set_("state.sequence", "state.sequence + 1"),
            call("_options", {"actor": "{state.actor}"}, ["_choices"]),
            require("len(_choices) <= 256", "The current action set exceeds its bound."),
            # Receipts must not disclose concealed hand order or private option count.
            {"shuffle": {"collection": "indices(256)"}, "store_as": "_opaque_indices"},
            set_("_numbered", []),
            set_("_choice_ids", []),
            set_("_index", 0),
            each(
                "_choices",
                "_choice",
                [
                    set_(
                        "_id",
                        "'choice:' + text(state.sequence) + ':' + text(_opaque_indices[_index])",
                    ),
                    set_("_row", {"id": "_id"}),
                    set_("_row", "merge(_choice, _row)"),
                    push("_numbered", "_row"),
                    push("_choice_ids", "_id"),
                    set_("_index", "_index + 1"),
                ],
            ),
            set_("state.choices", "_numbered"),
            set_("state.choice_ids", "_choice_ids"),
            call("_actor_choices", {"actor": "{state.actor}", "choices": "{_numbered}"}),
            set_("_areas", []),
            set_("_statuses", []),
            set_("_seat_ids", "state.seats"),
            each(
                "_seat_ids",
                "_seat",
                [
                    call("_seat_table", {"actor": "{_seat}"}, ["_area", "_status"]),
                    push("_areas", "_area"),
                    push("_statuses", "_status"),
                ],
            ),
            call("_center_table", exports=["_center"]),
            push("_areas", "_center"),
            push(
                "_statuses",
                {
                    "label": literal("Round"),
                    "value": (
                        "definitions.winds[min(state.round_wind, 2)] + ' ' + text(state.dealer + 1)"
                    ),
                },
            ),
            push("_statuses", {"label": literal("Honba"), "value": "text(state.honba)"}),
            push("_statuses", {"label": literal("Riichi sticks"), "value": "text(state.sticks)"}),
            push("_statuses", {"label": literal("Live wall"), "value": "text(len(state.live))"}),
            set_(
                "state.table",
                {
                    "format": 1,
                    "title": literal("Riichi Mahjong"),
                    "status": "_statuses",
                    "areas": "_areas",
                },
            ),
        ],
    )
    families["actor_choices"] = family(
        [
            PRIVATE_ACTOR,
            parameter("choices", "array", items={"type": "object"}),
        ],
        [
            set_("actor.game.choices", "choices"),
            set_("_decisions", []),
            each("choices", "_choice", [push("_decisions", {
                "id": "_choice.id", "label": "_choice.label", "operation": literal("take_action"),
                "arguments": {"choice": "_choice.id"},
            })]),
            set_("actor.game.decision_actions", "_decisions"),
        ],
    )

    families["seat_table"] = family(
        [PRIVATE_ACTOR],
        [
            set_("_pieces", []),
            set_("_tiles", "ordered(actor.game.hand)"),
            each(
                "_tiles",
                "_tile",
                [
                    set_("_face", "definitions.tiles[_tile]"),
                    set_("_piece", {"id": "'tile:' + text(_tile)", "label": "_face.label"}),
                    branch(
                        "_face.red", [set_("_piece", "merge(_piece, definitions.red_presentation)")]
                    ),
                    branch(
                        "actor.game.last_draw == _tile",
                        [
                            set_(
                                "_drawn",
                                {"detail": literal("Drawn tile"), "emphasis": literal("accent")},
                            ),
                            set_("_piece", "merge(_piece, _drawn)"),
                        ],
                    ),
                    branch(
                        "_actor_id == state.actor and actor.control == 'player'",
                        [
                            set_("_choices", "state.choices"),
                            each(
                                "_choices",
                                "_choice",
                                [
                                    branch(
                                        "_choice.op == 'discard' and _choice.tiles[0] == _tile",
                                        [
                                            set_(
                                                "_action",
                                                {"id": "_choice.id", "action_id": literal("play")},
                                            ),
                                            set_("_piece", "merge(_piece, _action)"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    push("_pieces", "_piece"),
                ],
            ),
            set_(
                "_hand",
                {
                    "id": literal("hand"),
                    "label": literal("Hand"),
                    "visibility": literal("owner"),
                    "owner": "_actor_id",
                    "show_count": True,
                    "pieces": "_pieces",
                },
            ),
            branch(
                "actor.control == 'player'",
                [
                    set_("_hand_owner", {"owner": "actor.participant_id"}),
                    set_("_hand", "merge(_hand, _hand_owner)"),
                ],
            ),
            set_("_pieces", []),
            set_("_discards", "actor.game.discards"),
            each(
                "_discards",
                "_discard",
                [
                    set_("_face", "definitions.tiles[_discard.tile]"),
                    push(
                        "_pieces",
                        {"id": "'discard:' + text(_discard.tile)", "label": "_face.label"},
                    ),
                ],
            ),
            set_(
                "_discard_group",
                {
                    "id": literal("discards"),
                    "label": literal("Discards"),
                    "visibility": literal("public"),
                    "pieces": "_pieces",
                },
            ),
            set_(
                "_area",
                {
                    "id": "_actor_id",
                    "label": "actor.name",
                    "position": "definitions.positions[actor.seat]",
                    "groups": ["_hand", "_discard_group"],
                },
            ),
            set_("_status", {"label": "actor.name", "value": "text(actor.score)"}),
        ],
    )
    families["center_table"] = family(
        [],
        [
            set_("_dora", []),
            set_("_face", "definitions.tiles[state.dead[4]]"),
            push("_dora", {"id": literal("dora0"), "label": "_face.label"}),
            set_("_options", []),
            call("_choice_table", {"actor": "{state.actor}"}, ["_options", "_turn"]),
            set_(
                "_center",
                {
                    "id": literal("shared"),
                    "label": "_turn",
                    "position": literal("center"),
                    "groups": [
                        {
                            "id": literal("dora"),
                            "label": literal("Dora indicators"),
                            "visibility": literal("public"),
                            "pieces": "_dora",
                        },
                        {
                            "id": literal("choices"),
                            "label": literal("Actions"),
                            "visibility": literal("public"),
                            "pieces": "_options",
                        },
                    ],
                },
            ),
        ],
    )
    families["choice_table"] = family(
        [PRIVATE_ACTOR],
        [
            set_("_turn", "actor.name + ' to act'"),
            branch("state.phase == 'hand_end'", [set_("_turn", literal("Hand complete"))]),
            branch("state.phase == 'finished'", [set_("_turn", literal("Match complete"))]),
            set_("_options", []),
            branch(
                "actor.control == 'player'",
                [
                    set_("_choices", "state.choices"),
                    each(
                        "_choices",
                        "_choice",
                        [
                            branch(
                                "_choice.op != 'discard'",
                                [
                                    push(
                                        "_options",
                                        {
                                            "id": "_choice.id",
                                            "label": "_choice.label",
                                            "action_id": literal("play"),
                                        },
                                    ),
                                ],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )

    actions = []
    for name, description in (
        ("start_game", "Start an East-only or East–South four-player match with red fives."),
        (
            "take_action",
            ("Choose a current action ID using the acting seat's permitted information."),
        ),
    ):
        actions.append(
            {
                "name": name,
                "family": name,
                "description": description,
                "configure": {},
                "capability": NS + ".play",
                "state_predicates": [],
                "turn_statuses": ["open"],
                "writers": ["dm"],
                "reference_availability": "included",
                "gameplay_resolution": "engine_family",
                "deterministic_part": None,
                "gm_part": None,
            }
        )
    player_schema = {
        "$id": NS + ".player.v1",
        "type": "object",
        "required": ["name", "control"],
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 32},
            "control": {"type": "string", "enum": ["player"]},
        },
        "additionalProperties": False,
    }
    result = {
        "format": 1,
        "id": NS,
        "version": "0.1.0",
        "label": "Riichi Mahjong",
        "publisher": "Pneuma IO",
        "mechanic_families": families,
        "public_actions": actions,
        "private_mechanics": [
            {
                "name": "_" + name,
                "family": name,
                "description": "Package-owned " + name.replace("_", " ") + ".",
                "configure": {},
            }
            for name in families
            if name not in {"start_game", "take_action"}
        ],
        "definitions": {
            "tiles": tiles,
            "positions": ["bottom", "right", "top", "left"],
            "winds": ["East", "South", "West"],
            "seat_winds": ["East", "South", "West", "North"],
            "patterns": patterns,
            "red_presentation": {"detail": "Red five", "emphasis": "accent"},
        },
        "participant_artifacts": [
            {
                "kind": "player",
                "schema_id": player_schema["$id"],
                "schema": player_schema,
                "entity_state_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "initial_state_calculations": [],
            }
        ],
        "state_schemas": [
            {"key": key, "schema": {"type": "object"}, "references": [], "visibility": "engine"}
            for key in ("state", "entities")
        ],
        "model_decision_boundary": {
            "active_actor": {"namespace": NS, "key": "state.actor"},
            "actor_entities": {"namespace": NS, "key": "entities"},
            "participant_control": {
                "field": "control",
                "player_values": ["player"],
                "actor_participant_field": "participant_id",
            },
            "default_owner": "gm",
            "isolated_participant": {
                "instructions": (
                    "Play Riichi Mahjong for only this seat. Choose one offered legal action. "
                    "Tile IDs 0..135 have kind ID // 4: kinds 0..8 are 1m..9m, 9..17 are "
                    "1p..9p, 18..26 are 1s..9s; 27..33 are East, South, West, North, White, "
                    "Green, Red. Physical IDs 16, 52 and 88 are red fives. Your hand and "
                    "public_table are the only available tile information. Do not guess wall "
                    "order or another hand. The engine scores, validates and resolves all moves."
                ),
                "observation_path": ["game"], "persona_path": ["persona"],
                "actions_path": ["game", "decision_actions"],
                "public_observation": {"namespace": NS, "key": "state.public_observation"},
            },
        },
        "ui_surfaces": [
            {
                "id": "riichi-table",
                "title": "Riichi Mahjong",
                "capability": NS + ".play",
                "renderer": "piece_table",
                "default_size": 560,
                "minimum_size": 300,
                "source": {"kind": "state", "namespace": NS, "key": "state.table"},
                "actions": [
                    {
                        "id": "play",
                        "label": "Play",
                        "intent": "take_action",
                        "params": {},
                        "bindings": {"choice": "piece"},
                    }
                ],
            }
        ],
        "operating_guide": (
            "Run Riichi Mahjong through start_game and take_action. Use the "
            "named player and three isolated NPC decision contexts. The host dispatches NPC "
            "choices from that seat's permitted observations. Keep your continuous campaign "
            "context and narrate only their committed public results. Resolve the player's "
            "currently declared action through the offered IDs, then stop at the next player "
            "decision. Never inspect the wall, NPC hands or NPC thinking. The "
            "table, legal actions, scores and saved match state belong to the "
            "engine. Use ordinary gameplay/OOC transitions and preserve the "
            "table during conversation. There is no adventuring rules layer. "
            "Tile labels m, p and s mean characters, circles and bamboo; red "
            "fives are marked explicitly."
        ),
    }
    from scripts.riichi_scoring import add_scoring

    add_scoring(families, result["definitions"])
    from scripts.riichi_play import add_gameplay

    add_gameplay(families, result["definitions"])
    from scripts.riichi_display import add_display

    add_display(families)
    result["private_mechanics"] = [
        {
            "name": "_" + name,
            "family": name,
            "description": "Package-owned " + name.replace("_", " ") + ".",
            "configure": {},
        }
        for name in families
        if name not in {"start_game", "take_action"}
    ]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, help="Write an admitted importable package archive."
    )
    options = parser.parse_args()
    ROOT.mkdir(exist_ok=True)
    content = yaml.safe_dump(source(), sort_keys=False, allow_unicode=True)
    (ROOT / "ruleset-source.yaml").write_text(content, encoding="utf-8")
    if options.archive is not None:
        from sylvia_gm.application.package_library import validate_admitted_package_runtimes
        from sylvia_gm.application.package_materialization import materialize_package_archive
        from sylvia_gm.builder._ruleset_source import compile_ruleset_source
        from sylvia_gm.builder._setting_materializer import _world_draft
        from sylvia_gm.infrastructure.package_archive import admit_package_archive

        compiled = compile_ruleset_source(content.encode())
        world = _world_draft(
            package_id="pneuma.riichi-club",
            version="0.1.0",
            title="The Lantern Table",
            payloads={"world.md": (ROOT / "setting" / "world.md").read_bytes()},
            ruleset=compiled.draft,
        )
        with materialize_package_archive((compiled.draft, world)) as artifact:
            raw = artifact.archive.read()
        import io

        with admit_package_archive(io.BytesIO(raw)) as admitted:
            validate_admitted_package_runtimes(admitted)
        options.archive.parent.mkdir(parents=True, exist_ok=True)
        options.archive.write_bytes(raw)
        print(f"Admitted Riichi Mahjong: {options.archive} ({len(raw)} bytes)")


if __name__ == "__main__":
    main()
