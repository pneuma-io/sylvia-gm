"""Declare the four-seat Mahjong state machine; core runtime stays game-neutral."""

from scripts.build_riichi_ruleset import (
    ACTOR,
    PRIVATE_ACTOR,
    branch,
    call,
    each,
    family,
    literal,
    parameter,
    push,
    require,
    set_,
)

SELECTED = {
    "name": "selected",
    "type": "object",
    "source": "state",
    "from_state": "state.action_selection",
}


def action(op, label, tiles=(), **fields):
    return {"op": literal(op), "label": literal(label), "tiles": list(tiles), **fields}


def add_gameplay(families, definitions):
    definitions["profile"] = {
        "id": "sylvia-four-player-aka-v1",
        "label": "Mahjong Soul-like four-player",
        "starting_score": 25000,
        "target": 30000,
        "multiple_ron": "all",
        "ron_honba": "nearest_winner",
        "riichi_pot": "nearest_winner",
        "nagashi": "draw_payments_no_honba_no_sticks",
        "nagashi_closed_required": False,
        "kuikae": "forbidden",
    }
    # A hand resets these fields before drawing. Persistent round counters live outside it.
    families["new_hand"]["effects"][1:1] = [
        set_("state.pending_riichi", None),
        set_("state.pending_kan", None),
        set_("state.ron_seats", []),
        set_("state.kan_owners", []),
        set_("state.abort_after_discard", False),
        set_("state.result", None),
        set_("state.winners", []),
    ]
    families["forced_responses"] = family(
        [],
        [
            set_("_keep_advancing", True),
            set_("_steps", [0, 1, 2]),
            each(
                "_steps",
                "_step",
                [
                    branch(
                        "_keep_advancing and (state.phase == 'response' "
                        "or state.phase == 'kan_response')",
                        [
                            call("_options", {"actor": "{state.actor}"}, ["_choices"]),
                            branch(
                                "len(_choices) == 1 and _choices[0].op == 'pass'",
                                [
                                    set_("state.action_selection", "_choices[0]"),
                                    call("_respond", {"actor": "{state.actor}"}),
                                ],
                                [set_("_keep_advancing", False)],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    families["refresh"]["effects"].insert(0, call("_forced_responses"))
    families["options"] = family(
        [PRIVATE_ACTOR],
        [
            set_("_choices", []),
            branch(
                "state.phase == 'discard'",
                [
                    call("_discard_options", {"actor": "{_actor_id}"}, ["_choices"]),
                ],
            ),
            branch(
                "state.phase == 'response' or state.phase == 'kan_response'",
                [
                    call("_response_options", {"actor": "{_actor_id}"}, ["_choices"]),
                ],
            ),
            branch("state.phase == 'hand_end'", [push("_choices", action("continue", "Continue"))]),
        ],
    )
    families["matching"] = family(
        [
            PRIVATE_ACTOR,
            parameter("kind", "integer"),
        ],
        [
            set_("_matching", []),
            set_("_hand", "ordered(actor.game.hand)"),
            each(
                "_hand",
                "_tile",
                [branch("definitions.tiles[_tile].kind == kind", [push("_matching", "_tile")])],
            ),
        ],
    )
    families["closed"] = family(
        [PRIVATE_ACTOR],
        [
            set_("_closed", True),
            set_("_melds", "actor.game.melds"),
            each("_melds", "_meld", [branch("_meld.opened", [set_("_closed", False)])]),
        ],
    )
    families["discard_options"] = family(
        [PRIVATE_ACTOR],
        [
            set_("_choices", []),
            set_("_hand", "ordered(actor.game.hand)"),
            call("_closed", {"actor": "{_actor_id}"}, ["_closed"]),
            set_("_last_riichi_kind", -1),
            set_("_cached_waits", []),
            each(
                "_hand",
                "_tile",
                [
                    branch(
                        (
                            "(not actor.game.riichi or actor.game.last_draw == _tile) and "
                            "definitions.tiles[_tile].kind not in "
                            "actor.game.forbidden_discards"
                        ),
                        [
                            push(
                                "_choices",
                                {
                                    "op": literal("discard"),
                                    "tiles": ["_tile"],
                                    "label": "'Discard ' + definitions.tiles[_tile].label",
                                },
                            ),
                            branch(
                                (
                                    "not actor.game.riichi and _closed and actor.score >= 1000 and "
                                    "len(state.live) >= 4"
                                ),
                                [
                                    branch(
                                        "definitions.tiles[_tile].kind != _last_riichi_kind",
                                        [
                                            call(
                                                "_waits",
                                                {
                                                    "actor": "{_actor_id}",
                                                    "tiles": (
                                                        "{without(actor.game.hand, one(_tile))}"
                                                    ),
                                                },
                                                ["_waits"],
                                            ),
                                            set_("_cached_waits", "_waits"),
                                            set_(
                                                "_last_riichi_kind", "definitions.tiles[_tile].kind"
                                            ),
                                        ],
                                    ),
                                    branch(
                                        "len(_cached_waits) > 0",
                                        [
                                            push(
                                                "_choices",
                                                {
                                                    "op": literal("riichi"),
                                                    "tiles": ["_tile"],
                                                    "waits": "_cached_waits",
                                                    "label": (
                                                        "'Riichi: discard ' + "
                                                        "definitions.tiles[_tile].labe"
                                                        "l"
                                                    ),
                                                },
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            branch(
                "actor.game.last_draw != null",
                [
                    set_("_source", literal("live_draw")),
                    branch("actor.game.rinshan", [set_("_source", literal("rinshan"))]),
                    call(
                        "_score_hand",
                        {
                            "actor": "{_actor_id}",
                            "winning_tile": "{actor.game.last_draw}",
                            "is_tsumo": True,
                            "win_source": "{_source}",
                        },
                        ["_score"],
                    ),
                    branch("_score.valid", [push("_choices", action("tsumo", "Tsumo"))]),
                    branch(
                        "len(state.live) > 0 and state.kan_count < 4",
                        [
                            set_("_types", "indices(34)"),
                            each(
                                "_types",
                                "_kind",
                                [
                                    call(
                                        "_matching",
                                        {"actor": "{_actor_id}", "kind": "{_kind}"},
                                        ["_matching"],
                                    ),
                                    branch(
                                        "len(_matching) == 4",
                                        [
                                            set_("_allowed", True),
                                            branch(
                                                "actor.game.riichi",
                                                [
                                                    call(
                                                        "_riichi_kan",
                                                        {
                                                            "actor": "{_actor_id}",
                                                            "kind": "{_kind}",
                                                            "tiles": "{_matching}",
                                                        },
                                                        ["_allowed"],
                                                    ),
                                                ],
                                            ),
                                            branch(
                                                "_allowed",
                                                [
                                                    push(
                                                        "_choices",
                                                        {
                                                            "op": literal("ankan"),
                                                            "tiles": "_matching",
                                                            "base": "_kind",
                                                            "label": (
                                                                "'Concealed kan: ' + "
                                                                "definitions.tiles[_match"
                                                                "ing[0]].label"
                                                            ),
                                                        },
                                                    )
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            branch(
                                "not actor.game.riichi",
                                [
                                    set_("_melds", "actor.game.melds"),
                                    set_("_index", 0),
                                    each(
                                        "_melds",
                                        "_meld",
                                        [
                                            branch(
                                                (
                                                    "_meld.form == 'triplet' and not "
                                                    "_meld.quad and _meld.opened"
                                                ),
                                                [
                                                    call(
                                                        "_matching",
                                                        {
                                                            "actor": "{_actor_id}",
                                                            "kind": "{_meld.base}",
                                                        },
                                                        ["_matching"],
                                                    ),
                                                    each(
                                                        "_matching",
                                                        "_tile",
                                                        [
                                                            push(
                                                                "_choices",
                                                                {
                                                                    "op": literal("kakan"),
                                                                    "tiles": ["_tile"],
                                                                    "base": "_meld.base",
                                                                    "meld_index": "_index",
                                                                    "label": (
                                                                        "'Added kan: ' + "
                                                                        "definitions.tiles[_tile]"
                                                                        ".label"
                                                                    ),
                                                                },
                                                            )
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            set_("_index", "_index + 1"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    branch(
                        (
                            "actor.game.draw_count == 1 and len(actor.game.discards) == 0 and "
                            "not state.any_call"
                        ),
                        [
                            set_("_terminals", []),
                            set_("_hand", "actor.game.hand"),
                            each(
                                "_hand",
                                "_tile",
                                [
                                    branch(
                                        (
                                            (
                                                "definitions.tiles[_tile].kind in "
                                                "definitions.terminals and "
                                            )
                                            + "definitions.tiles[_tile].kind not in _terminals"
                                        ),
                                        [push("_terminals", "definitions.tiles[_tile].kind")],
                                    )
                                ],
                            ),
                            branch(
                                "len(_terminals) >= 9",
                                [
                                    push(
                                        "_choices",
                                        action("abort_nine", "Nine terminals: abort hand"),
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    # Ranked riichi permits a drawn fourth tile when the waiting tile types stay unchanged.
    # Compare waits against a hypothetical meld without publishing or committing it.
    families["riichi_kan"] = family(
        [
            PRIVATE_ACTOR,
            parameter("kind", "integer"),
            parameter("tiles", "array", items={"type": "integer"}),
        ],
        [
            set_("_allowed", "definitions.tiles[actor.game.last_draw].kind == kind"),
            set_("_wait_kinds", "actor.game.riichi_waits"),
            set_("_remaining", "without(actor.game.hand, tiles)"),
            set_("_remaining_kinds", []),
            each(
                "_remaining", "_tile", [push("_remaining_kinds", "definitions.tiles[_tile].kind")]
            ),
            set_(
                "_after",
                (
                    "completions(_remaining_kinds, definitions.patterns, "
                    "definitions.group_sizes[len(actor.game.melds) + 1], indices(34))"
                ),
            ),
            set_("_owned_kinds", "_kinds"),
            set_("_melds", "actor.game.melds"),
            push("_owned_kinds", "definitions.tiles[actor.game.last_draw].kind"),
            each(
                "_melds",
                "_meld",
                [
                    set_("_meld_tiles", "_meld.tiles"),
                    each(
                        "_meld_tiles",
                        "_tile",
                        [push("_owned_kinds", "definitions.tiles[_tile].kind")],
                    ),
                ],
            ),
            set_("_filtered_after", []),
            each(
                "_after",
                "_wait_kind",
                [
                    branch(
                        "occurrences(_owned_kinds, _wait_kind) < 4",
                        [push("_filtered_after", "_wait_kind")],
                    ),
                ],
            ),
            branch(
                "len(_filtered_after) != len(actor.game.riichi_waits)",
                [set_("_allowed", False)],
            ),
            each(
                "_filtered_after",
                "_wait_kind",
                [
                    branch("_wait_kind not in actor.game.riichi_waits", [set_("_allowed", False)]),
                ],
            ),
        ],
    )
    families["response_options"] = family(
        [PRIVATE_ACTOR],
        [
            set_("_choices", [action("pass", "Pass")]),
            set_("_tile", "state.last_discard.tile"),
            set_("_kind", "definitions.tiles[_tile].kind"),
            call("_waits", {"actor": "{_actor_id}", "tiles": "{actor.game.hand}"}, ["_waits"]),
            set_("_furiten", "actor.game.temporary_furiten or actor.game.riichi_furiten"),
            set_("_discards", "actor.game.discards"),
            each(
                "_discards",
                "_discard",
                [
                    branch(
                        "definitions.tiles[_discard.tile].kind in _waits", [set_("_furiten", True)]
                    )
                ],
            ),
            branch(
                "_kind in _waits and not _furiten",
                [
                    set_("_source", literal("discard")),
                    branch(
                        "state.last_discard.rinshan == true",
                        [set_("_source", literal("rinshan_discard"))],
                    ),
                    branch(
                        "state.phase == 'kan_response'",
                        [
                            set_("_source", literal("closed_kan")),
                            branch(
                                "state.pending_kan.op == 'kakan'",
                                [set_("_source", literal("added_kan"))],
                            ),
                        ],
                    ),
                    call(
                        "_score_hand",
                        {
                            "actor": "{_actor_id}",
                            "winning_tile": "{_tile}",
                            "is_tsumo": False,
                            "win_source": "{_source}",
                        },
                        ["_score"],
                    ),
                    branch(
                        ("_score.valid and (_source != 'closed_kan' or _score.shape == 'orphans')"),
                        [push("_choices", action("ron", "Ron"))],
                    ),
                ],
            ),
            branch(
                (
                    "state.phase == 'response' and not actor.game.riichi and "
                    "len(state.live) > 0 and not state.abort_after_discard"
                ),
                [
                    call("_matching", {"actor": "{_actor_id}", "kind": "{_kind}"}, ["_matching"]),
                    set_("_copies", "_matching"),
                    set_("_indexes", "indices(len(_copies))"),
                    each(
                        "_indexes",
                        "_a",
                        [
                            each(
                                "_indexes",
                                "_b",
                                [
                                    branch(
                                        "_a < _b",
                                        [
                                            call(
                                                "_call_option",
                                                {
                                                    "actor": "{_actor_id}",
                                                    "op": "pon",
                                                    "tiles": (
                                                        "{joined(one(_copies[_a]), "
                                                        "one(_copies[_b]))}"
                                                    ),
                                                    "base": "{_kind}",
                                                },
                                                ["_option"],
                                            ),
                                            branch(
                                                "_option != null", [push("_choices", "_option")]
                                            ),
                                        ],
                                    )
                                ],
                            )
                        ],
                    ),
                    branch(
                        "len(_copies) == 3 and state.kan_count < 4",
                        [
                            push(
                                "_choices",
                                {
                                    "op": literal("minkan"),
                                    "tiles": "_copies",
                                    "base": "_kind",
                                    "label": literal("Open kan"),
                                },
                            ),
                        ],
                    ),
                    branch(
                        "actor.seat == (state.last_discard.seat + 1) % 4 and _kind < 27",
                        [
                            set_("_offsets", [0, 1, 2]),
                            each(
                                "_offsets",
                                "_offset",
                                [
                                    set_("_base", "_kind - _offset"),
                                    branch(
                                        (
                                            "_base >= 0 and _base // 9 == _kind // 9 and _base "
                                            "% 9 <= 6"
                                        ),
                                        [
                                            set_(
                                                "_needed",
                                                (
                                                    (
                                                        "without(joined(one(_base), "
                                                        "joined(one(_base + 1), one(_base + "
                                                    )
                                                    + "2))), one(_kind))"
                                                ),
                                            ),
                                            call(
                                                "_matching",
                                                {"actor": "{_actor_id}", "kind": "{_needed[0]}"},
                                                ["_matching"],
                                            ),
                                            set_("_first", "_matching"),
                                            call(
                                                "_matching",
                                                {"actor": "{_actor_id}", "kind": "{_needed[1]}"},
                                                ["_matching"],
                                            ),
                                            set_("_second", "_matching"),
                                            each(
                                                "_first",
                                                "_a",
                                                [
                                                    each(
                                                        "_second",
                                                        "_b",
                                                        [
                                                            call(
                                                                "_call_option",
                                                                {
                                                                    "actor": "{_actor_id}",
                                                                    "op": "chi",
                                                                    "tiles": (
                                                                        "{joined(one(_a), one(_b))}"
                                                                    ),
                                                                    "base": "{_base}",
                                                                },
                                                                ["_option"],
                                                            ),
                                                            branch(
                                                                "_option != null",
                                                                [push("_choices", "_option")],
                                                            ),
                                                        ],
                                                    )
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    # Kuikae bans the called kind and a sequence's opposite outside swap.
    families["call_option"] = family(
        [
            PRIVATE_ACTOR,
            parameter("op"),
            parameter("tiles", "array", items={"type": "integer"}),
            parameter("base", "integer"),
        ],
        [
            set_("_called", "definitions.tiles[state.last_discard.tile].kind"),
            set_("_forbidden", "one(_called)"),
            branch(
                "op == 'chi'",
                [
                    branch("_called == base and base % 9 < 6", [push("_forbidden", "base + 3")]),
                    branch(
                        "_called == base + 2 and base % 9 > 0", [push("_forbidden", "base - 1")]
                    ),
                ],
            ),
            set_("_legal_discard", False),
            set_("_remaining", "without(actor.game.hand, tiles)"),
            each(
                "_remaining",
                "_tile",
                [
                    branch(
                        "definitions.tiles[_tile].kind not in _forbidden",
                        [set_("_legal_discard", True)],
                    )
                ],
            ),
            set_("_option", None),
            branch(
                "_legal_discard",
                [
                    set_(
                        "_option",
                        {
                            "op": "op",
                            "tiles": "tiles",
                            "base": "base",
                            "forbidden": "_forbidden",
                            "label": (
                                "op + ': ' + definitions.tiles[tiles[0]].label + ' ' + "
                                "definitions.tiles[tiles[1]].label"
                            ),
                        },
                    )
                ],
            ),
        ],
    )

    families["take_action"] = family(
        [ACTOR, parameter("choice")],
        [
            set_("_selected", None),
            set_("_choices", "state.choices"),
            each(
                "_choices",
                "_choice",
                [branch("_choice.id == choice", [set_("_selected", "_choice")])],
            ),
            require("_selected != null", "This action is no longer legal."),
            set_("state.action_selection", "_selected"),
            branch(
                "_selected.op == 'discard' or _selected.op == 'riichi'",
                [
                    call("_discard", {"actor": "{_actor_id}"}),
                ],
            ),
            branch(
                (
                    "_selected.op == 'pass' or _selected.op == 'ron' or _selected.op "
                    "== 'chi' or _selected.op == 'pon' or _selected.op == 'minkan'"
                ),
                [
                    call("_respond", {"actor": "{_actor_id}"}),
                ],
            ),
            branch(
                "_selected.op == 'ankan' or _selected.op == 'kakan'",
                [
                    set_("state.kan_pending_dora", 0),
                    set_("_kan_seat", {"seat": "actor.seat"}),
                    set_("state.pending_kan", "merge(_selected, _kan_seat)"),
                    set_(
                        "state.last_discard",
                        {"seat": "actor.seat", "tile": "_selected.tiles[len(_selected.tiles) - 1]"},
                    ),
                    call("_begin_responses", {"seat": "{actor.seat}"}),
                    set_("state.phase", literal("kan_response")),
                    set_("state.message", "actor.name + ' declared a kan.'"),
                ],
            ),
            branch(
                "_selected.op == 'tsumo'",
                [
                    set_("state.winners", "one(actor.seat)"),
                    set_("state.win_tsumo", True),
                    set_("state.winning_tile", "actor.game.last_draw"),
                    set_("state.win_source", literal("live_draw")),
                    branch("actor.game.rinshan", [set_("state.win_source", literal("rinshan"))]),
                    call("_settle_wins"),
                ],
            ),
            branch("_selected.op == 'abort_nine'", [call("_abort", {"reason": "Nine terminals"})]),
            branch("_selected.op == 'continue'", [call("_advance_match")]),
            call("_refresh"),
            {
                "publish": {
                    "fields": {
                        "message": "state.message",
                        "result": "state.result",
                        "match_result": "state.match_result",
                    }
                }
            },
        ],
        pre=[("state.phase in definitions.play_phases", "The table is not waiting for a move.")],
        metadata={
            "model_behavior": "resolves_player_intent",
            "player_controls": {
                "confirmation": "none",
                "response": "automatic",
                "checks": [
                    {
                        "condition": "choice in state.choice_ids",
                        "reason": "This move is no longer available.",
                    }
                ],
            }
        },
    )
    definitions["play_phases"] = ["discard", "response", "kan_response", "hand_end"]
    families["begin_responses"] = family(
        [parameter("seat", "integer")],
        [
            set_("state.pending_calls", []),
            set_("state.ron_seats", []),
            set_("_responders", []),
            set_("_steps", [1, 2, 3]),
            each("_steps", "_step", [push("_responders", "state.seats[(seat + _step) % 4]")]),
            set_("state.responders", "_responders"),
            set_("state.actor", "_responders[0]"),
        ],
    )
    families["discard"] = family(
        [PRIVATE_ACTOR, SELECTED],
        [
            set_("_tile", "selected.tiles[0]"),
            set_("actor.game.hand", "without(actor.game.hand, one(_tile))"),
            set_("actor.game.ippatsu", False),
            branch(
                "selected.op == 'riichi'",
                [
                    set_("state.pending_riichi", "actor.seat"),
                    set_("actor.game.riichi_waits", "selected.waits"),
                    set_(
                        "actor.game.double_riichi",
                        "len(actor.game.discards) == 0 and not state.any_call",
                    ),
                ],
            ),
            set_("_discards", "actor.game.discards"),
            push(
                "_discards",
                {"tile": "_tile", "claimed": False, "riichi": "selected.op == 'riichi'"},
            ),
            set_("actor.game.discards", "_discards"),
            set_("actor.game.forbidden_discards", []),
            set_("actor.game.last_draw", None),
            set_(
                "state.last_discard",
                {"tile": "_tile", "seat": "actor.seat", "rinshan": "actor.game.rinshan"},
            ),
            set_("actor.game.rinshan", False),
            set_("state.kan_pending_dora", 0),
            set_("state.pending_kan", None),
            call("_begin_responses", {"seat": "{actor.seat}"}),
            set_("state.phase", literal("response")),
            set_(
                "state.message", "actor.name + ' discarded ' + definitions.tiles[_tile].label + '.'"
            ),
            branch(
                "selected.op == 'riichi'",
                [
                    set_(
                        "state.message",
                        "actor.name + ' declared riichi and discarded ' "
                        "+ definitions.tiles[_tile].label + '.'",
                    ),
                ],
            ),
        ],
    )
    families["respond"] = family(
        [PRIVATE_ACTOR, SELECTED],
        [
            branch(
                "selected.op == 'ron'",
                [set_("state.ron_seats", "joined(state.ron_seats, one(actor.seat))")],
                [
                    call(
                        "_waits", {"actor": "{_actor_id}", "tiles": "{actor.game.hand}"}, ["_waits"]
                    ),
                    branch(
                        "definitions.tiles[state.last_discard.tile].kind in _waits",
                        [
                            set_("_completes", True),
                            branch(
                                "state.phase == 'kan_response' and state.pending_kan.op == 'ankan'",
                                [
                                    call(
                                        "_score_hand",
                                        {
                                            "actor": "{_actor_id}",
                                            "winning_tile": "{state.last_discard.tile}",
                                            "is_tsumo": False,
                                            "win_source": "closed_kan",
                                        },
                                        ["_score"],
                                    ),
                                    set_("_completes", "_score.shape == 'orphans'"),
                                ],
                            ),
                            branch(
                                "_completes",
                                [
                                    set_("actor.game.temporary_furiten", True),
                                    branch(
                                        "actor.game.riichi",
                                        [set_("actor.game.riichi_furiten", True)],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    branch(
                        "selected.op != 'pass'",
                        [
                            set_("_row", {"seat": "actor.seat"}),
                            set_(
                                "state.pending_calls",
                                "joined(state.pending_calls, one(merge(selected, _row)))",
                            ),
                        ],
                    ),
                ],
            ),
            set_("state.responders", "portion(state.responders, 1, len(state.responders))"),
            branch(
                "len(state.responders) > 0",
                [set_("state.actor", "state.responders[0]")],
                [call("_resolve_responses")],
            ),
        ],
    )
    families["resolve_responses"] = family(
        [],
        [
            branch(
                "len(state.ron_seats) > 0",
                [
                    set_("state.winners", "state.ron_seats"),
                    set_("state.win_tsumo", False),
                    set_("state.winning_tile", "state.last_discard.tile"),
                    set_("state.win_source", literal("discard")),
                    branch(
                        "state.last_discard.rinshan == true",
                        [set_("state.win_source", literal("rinshan_discard"))],
                    ),
                    branch(
                        "state.phase == 'kan_response'",
                        [
                            set_("state.win_source", literal("closed_kan")),
                            branch(
                                "state.pending_kan.op == 'kakan'",
                                [set_("state.win_source", literal("added_kan"))],
                            ),
                        ],
                    ),
                    call("_settle_wins"),
                ],
                [
                    branch(
                        "state.phase == 'kan_response'",
                        [call("_commit_kan")],
                        [
                            branch(
                                "state.pending_riichi != null",
                                [
                                    call(
                                        "_accept_riichi",
                                        {"actor": "{state.seats[state.pending_riichi]}"},
                                    ),
                                    set_("state.pending_riichi", None),
                                ],
                            ),
                            set_("_chosen", None),
                            set_("_calls", "state.pending_calls"),
                            each(
                                "_calls",
                                "_call",
                                [
                                    branch(
                                        (
                                            "_chosen == null or (_chosen.op == 'chi' and "
                                            "_call.op != 'chi')"
                                        ),
                                        [set_("_chosen", "_call")],
                                    ),
                                ],
                            ),
                            branch(
                                "_chosen != null",
                                [
                                    set_("state.action_selection", "_chosen"),
                                    call("_commit_call", {"actor": "{state.seats[_chosen.seat]}"}),
                                ],
                                [
                                    call("_abort_checks", exports=["_abort_reason"]),
                                    branch(
                                        "_abort_reason != null",
                                        [call("_abort", {"reason": "{_abort_reason}"})],
                                        [
                                            branch(
                                                "len(state.live) == 0",
                                                [call("_exhaustive")],
                                                [
                                                    set_(
                                                        "state.actor",
                                                        (
                                                            "state.seats[(state.last_discard.se"
                                                            "at + 1) % 4]"
                                                        ),
                                                    ),
                                                    call("_draw", {"actor": "{state.actor}"}),
                                                    set_(
                                                        "state.message",
                                                        literal("Next seat drew a tile."),
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    families["accept_riichi"] = family(
        [PRIVATE_ACTOR],
        [
            set_("actor.score", "actor.score - 1000"),
            set_("state.sticks", "state.sticks + 1"),
            set_("actor.game.riichi", True),
            set_("actor.game.ippatsu", True),
        ],
    )
    families["cancel_ippatsu"] = family([PRIVATE_ACTOR], [set_("actor.game.ippatsu", False)])
    families["interrupt"] = family(
        [],
        [
            set_("state.any_call", True),
            set_("_seats", "state.seats"),
            each("_seats", "_seat", [call("_cancel_ippatsu", {"actor": "{_seat}"})]),
        ],
    )
    families["claim_discard"] = family(
        [PRIVATE_ACTOR],
        [
            set_("_discards", "actor.game.discards"),
            set_("_index", "len(_discards) - 1"),
            set_("_last", "merge(_discards[_index], definitions.claimed_flag)"),
            set_("actor.game.discards", "replaced(_discards, _index, _last)"),
        ],
    )
    definitions["claimed_flag"] = {"claimed": True}
    families["commit_call"] = family(
        [PRIVATE_ACTOR, SELECTED],
        [
            call("_interrupt"),
            call("_claim_discard", {"actor": "{state.seats[state.last_discard.seat]}"}),
            set_("actor.game.hand", "without(actor.game.hand, selected.tiles)"),
            set_(
                "_meld",
                {
                    "form": literal("triplet"),
                    "base": "selected.base",
                    "opened": True,
                    "quad": "selected.op == 'minkan'",
                    "tiles": "joined(selected.tiles, one(state.last_discard.tile))",
                    "source_seat": "state.last_discard.seat",
                },
            ),
            branch(
                "selected.op == 'chi'",
                [
                    set_("_form", {"form": literal("sequence")}),
                    set_("_meld", "merge(_meld, _form)"),
                ],
            ),
            set_("actor.game.melds", "joined(actor.game.melds, one(_meld))"),
            call(
                "_pao_check", {"actor": "{_actor_id}", "source_seat": "{state.last_discard.seat}"}
            ),
            set_("state.actor", "_actor_id"),
            set_("state.phase", literal("discard")),
            set_("actor.game.last_draw", None),
            branch(
                "selected.op == 'minkan'",
                [
                    call("_finish_kan", {"actor": "{_actor_id}", "concealed": False}),
                ],
                [set_("actor.game.forbidden_discards", "selected.forbidden")],
            ),
            set_("state.message", "actor.name + ' called ' + selected.op + '.'"),
        ],
    )
    families["pao_check"] = family(
        [PRIVATE_ACTOR, parameter("source_seat", "integer")],
        [
            set_("_dragons", 0),
            set_("_winds", 0),
            set_("_melds", "actor.game.melds"),
            each(
                "_melds",
                "_meld",
                [
                    branch(
                        "_meld.form == 'triplet' and _meld.opened",
                        [
                            branch("_meld.base >= 31", [set_("_dragons", "_dragons + 1")]),
                            branch(
                                "_meld.base >= 27 and _meld.base <= 30",
                                [set_("_winds", "_winds + 1")],
                            ),
                        ],
                    )
                ],
            ),
            branch(
                "_dragons == 3 and actor.game.pao_dragon == null",
                [set_("actor.game.pao_dragon", "source_seat")],
            ),
            branch(
                "_winds == 4 and actor.game.pao_wind == null",
                [set_("actor.game.pao_wind", "source_seat")],
            ),
        ],
    )
    families["commit_kan"] = family(
        [],
        [
            set_("state.action_selection", "state.pending_kan"),
            call("_complete_kan", {"actor": "{state.seats[state.pending_kan.seat]}"}),
            set_("state.pending_kan", None),
        ],
    )
    families["complete_kan"] = family(
        [PRIVATE_ACTOR, SELECTED],
        [
            call("_interrupt"),
            set_("actor.game.hand", "without(actor.game.hand, selected.tiles)"),
            set_("_melds", "actor.game.melds"),
            branch(
                "selected.op == 'ankan'",
                [
                    push(
                        "_melds",
                        {
                            "form": literal("triplet"),
                            "base": "selected.base",
                            "opened": False,
                            "quad": True,
                            "tiles": "selected.tiles",
                            "source_seat": "actor.seat",
                        },
                    ),
                ],
                [
                    set_("_old", "_melds[selected.meld_index]"),
                    set_("_update", {"quad": True, "tiles": "joined(_old.tiles, selected.tiles)"}),
                    set_("_melds", "replaced(_melds, selected.meld_index, merge(_old, _update))"),
                ],
            ),
            set_("actor.game.melds", "_melds"),
            set_("state.actor", "_actor_id"),
            call("_finish_kan", {"actor": "{_actor_id}", "concealed": "{selected.op == 'ankan'}"}),
            set_("state.message", "actor.name + ' completed a kan.'"),
        ],
    )
    families["finish_kan"] = family(
        [PRIVATE_ACTOR, parameter("concealed", "boolean")],
        [
            set_("_tile", "state.dead[state.kan_count]"),
            set_(
                "state.dead",
                "replaced(state.dead, state.kan_count, state.live[len(state.live) - 1])",
            ),
            set_("state.live", "portion(state.live, 0, len(state.live) - 1)"),
            set_("state.kan_count", "state.kan_count + 1"),
            set_("state.kan_pending_dora", 1),
            branch("concealed", [set_("state.kan_pending_dora", 0)]),
            set_("state.kan_owners", "joined(state.kan_owners, one(actor.seat))"),
            set_("_owners", "state.kan_owners"),
            set_("_other", False),
            each("_owners", "_owner", [branch("_owner != actor.seat", [set_("_other", True)])]),
            branch("state.kan_count == 4 and _other", [set_("state.abort_after_discard", True)]),
            set_("actor.game.hand", "joined(actor.game.hand, one(_tile))"),
            set_("actor.game.last_draw", "_tile"),
            set_("actor.game.draw_count", "actor.game.draw_count + 1"),
            set_("actor.game.rinshan", True),
            set_("actor.game.temporary_furiten", False),
            set_("state.phase", literal("discard")),
        ],
    )
    families["abort_flags"] = family(
        [PRIVATE_ACTOR],
        [
            set_("_riichi", "actor.game.riichi"),
            set_("_first_wind", -1),
            branch(
                "len(actor.game.discards) == 1",
                [
                    set_("_kind", "definitions.tiles[actor.game.discards[0].tile].kind"),
                    branch("_kind >= 27 and _kind <= 30", [set_("_first_wind", "_kind")]),
                ],
            ),
        ],
    )
    families["abort_checks"] = family(
        [],
        [
            set_("_abort_reason", None),
            set_("_all_riichi", True),
            set_("_same_wind", True),
            set_("_wind", -1),
            set_("_seats", "state.seats"),
            each(
                "_seats",
                "_seat",
                [
                    call("_abort_flags", {"actor": "{_seat}"}, ["_riichi", "_first_wind"]),
                    branch("not _riichi", [set_("_all_riichi", False)]),
                    branch(
                        "_first_wind == -1 or (_wind != -1 and _wind != _first_wind)",
                        [set_("_same_wind", False)],
                    ),
                    set_("_wind", "_first_wind"),
                ],
            ),
            branch(
                "_same_wind and not state.any_call", [set_("_abort_reason", literal("Four winds"))]
            ),
            branch("_all_riichi", [set_("_abort_reason", literal("Four riichi"))]),
            branch("state.abort_after_discard", [set_("_abort_reason", literal("Four kans"))]),
        ],
    )
    families["abort"] = family(
        [parameter("reason")],
        [
            set_(
                "state.result", {"kind": literal("abort"), "reason": "reason", "dealer_stays": True}
            ),
            call("_end_hand"),
            set_("state.message", "'Abortive draw: ' + reason + '.'"),
        ],
    )
    add_settlement(families)


def add_settlement(families):
    """Score movement and match progression are separate from move enumeration."""
    families["adjust_score"] = family(
        [PRIVATE_ACTOR, parameter("amount", "integer")],
        [set_("actor.score", "actor.score + amount")],
    )
    families["settle_wins"] = family(
        [],
        [
            set_("_winners", "state.winners"),
            set_("_results", []),
            set_("_dealer_stays", False),
            set_("_first", True),
            each(
                "_winners",
                "_winner",
                [
                    call(
                        "_pay_winner",
                        {"actor": "{state.seats[_winner]}", "award_sticks": "{_first}"},
                        ["_win_result"],
                    ),
                    push("_results", "_win_result"),
                    set_("_first", False),
                    branch("_winner == state.dealer", [set_("_dealer_stays", True)]),
                ],
            ),
            set_("state.sticks", 0),
            set_("state.pending_riichi", None),
            set_(
                "state.result",
                {"kind": literal("win"), "winners": "_results", "dealer_stays": "_dealer_stays"},
            ),
            call("_end_hand"),
            set_("state.message", literal("Hand won. Scores settled.")),
        ],
    )
    families["pay_winner"] = family(
        [PRIVATE_ACTOR, parameter("award_sticks", "boolean")],
        [
            call(
                "_score_hand",
                {
                    "actor": "{_actor_id}",
                    "winning_tile": "{state.winning_tile}",
                    "is_tsumo": "{state.win_tsumo}",
                    "win_source": "{state.win_source}",
                },
                ["_score"],
            ),
            require("_score.valid", "The winning hand has no legal yaku."),
            set_("_payments", [0, 0, 0, 0]),
            set_("_ron_honba", 0),
            branch(
                "award_sticks or definitions.profile.ron_honba == 'each_winner'",
                [
                    set_("_ron_honba", "state.honba * 300"),
                ],
            ),
            set_("_basic", "_score.basic"),
            set_("_pao", []),
            branch(
                "_score.big_dragons and actor.game.pao_dragon != null",
                [push("_pao", {"seat": "actor.game.pao_dragon", "basic": 8000})],
            ),
            branch(
                "_score.big_winds and actor.game.pao_wind != null",
                [push("_pao", {"seat": "actor.game.pao_wind", "basic": 16000})],
            ),
            each(
                "_pao",
                "_liability",
                [
                    set_("_component", "_liability.basic * 4"),
                    branch(
                        "actor.seat == state.dealer", [set_("_component", "_liability.basic * 6")]
                    ),
                    set_("_basic", "_basic - _liability.basic"),
                    branch(
                        "state.win_tsumo or state.last_discard.seat == _liability.seat",
                        [
                            set_(
                                "_payments",
                                (
                                    (
                                        "replaced(_payments, _liability.seat, "
                                        "_payments[_liability.seat] "
                                    )
                                    + "+ _component)"
                                ),
                            ),
                        ],
                        [
                            set_(
                                "_payments",
                                (
                                    (
                                        "replaced(_payments, _liability.seat, "
                                        "_payments[_liability.seat] "
                                    )
                                    + "+ _component // 2)"
                                ),
                            ),
                            set_("_source", "state.last_discard.seat"),
                            set_(
                                "_payments",
                                (
                                    "replaced(_payments, _source, _payments[_source] + "
                                    "_component // 2)"
                                ),
                            ),
                        ],
                    ),
                ],
            ),
            branch(
                "state.win_tsumo",
                [
                    set_("_payers", "indices(4)"),
                    each(
                        "_payers",
                        "_payer",
                        [
                            branch(
                                "_payer != actor.seat",
                                [
                                    set_("_factor", 1),
                                    branch(
                                        "actor.seat == state.dealer or _payer == state.dealer",
                                        [set_("_factor", 2)],
                                    ),
                                    set_("_payment", "((_basic * _factor + 99) // 100) * 100"),
                                    set_("_honba", "state.honba * 100"),
                                    branch(
                                        "len(_pao) > 0",
                                        [
                                            set_("_honba", 0),
                                            branch(
                                                "_payer == _pao[0].seat",
                                                [set_("_honba", "state.honba * 300")],
                                            ),
                                        ],
                                    ),
                                    set_(
                                        "_payments",
                                        (
                                            "replaced(_payments, _payer, _payments[_payer] + "
                                            "_payment + _honba)"
                                        ),
                                    ),
                                ],
                            )
                        ],
                    ),
                ],
                [
                    set_("_factor", 4),
                    branch("actor.seat == state.dealer", [set_("_factor", 6)]),
                    set_("_source", "state.last_discard.seat"),
                    set_(
                        "_payments",
                        (
                            "replaced(_payments, _source, _payments[_source] + ((_basic * "
                            "_factor + 99) // 100) * 100)"
                        ),
                    ),
                    set_("_honba_source", "_source"),
                    branch("len(_pao) > 0", [set_("_honba_source", "_pao[0].seat")]),
                    set_(
                        "_payments",
                        "replaced(_payments, _honba_source, "
                        "_payments[_honba_source] + _ron_honba)",
                    ),
                ],
            ),
            set_("_payers", "indices(4)"),
            each(
                "_payers",
                "_payer",
                [
                    branch(
                        "_payments[_payer] > 0",
                        [
                            call(
                                "_adjust_score",
                                {
                                    "actor": "{state.seats[_payer]}",
                                    "amount": "{0 - _payments[_payer]}",
                                },
                            ),
                        ],
                    )
                ],
            ),
            set_("_gain", "total(_payments)"),
            branch("award_sticks", [set_("_gain", "_gain + state.sticks * 1000")]),
            set_("actor.score", "actor.score + _gain"),
            set_("actor.game.revealed", True),
            set_("_winning_hand", "actor.game.hand"),
            branch(
                "not state.win_tsumo",
                [set_("_winning_hand", "joined(_winning_hand, one(state.winning_tile))")],
            ),
            set_("_labels", []),
            each("_winning_hand", "_tile", [push("_labels", "definitions.tiles[_tile].label")]),
            set_(
                "_win_result",
                {
                    "seat": "actor.seat",
                    "name": "actor.name",
                    "score": "_score",
                    "payments": "_payments",
                    "gain": "_gain",
                    "hand": "_labels",
                },
            ),
        ],
    )
    families["draw_status"] = family(
        [PRIVATE_ACTOR],
        [
            call("_waits", {"actor": "{_actor_id}", "tiles": "{actor.game.hand}"}, ["_waits"]),
            set_("_tenpai", "len(_waits) > 0"),
            set_("_nagashi", "len(actor.game.discards) > 0"),
            set_("_discards", "actor.game.discards"),
            each(
                "_discards",
                "_discard",
                [
                    branch(
                        (
                            "_discard.claimed or definitions.tiles[_discard.tile].kind not in "
                            "definitions.terminals"
                        ),
                        [set_("_nagashi", False)],
                    )
                ],
            ),
            set_("_melds", "actor.game.melds"),
            each(
                "_melds",
                "_meld",
                [
                    branch(
                        "_meld.opened and definitions.profile.nagashi_closed_required",
                        [set_("_nagashi", False)],
                    )
                ],
            ),
            set_("actor.game.revealed", "_tenpai"),
        ],
    )
    families["exhaustive"] = family(
        [],
        [
            set_("_tenpai_seats", []),
            set_("_nagashi_seats", []),
            set_("_seats", "indices(4)"),
            each(
                "_seats",
                "_seat",
                [
                    call(
                        "_draw_status", {"actor": "{state.seats[_seat]}"}, ["_tenpai", "_nagashi"]
                    ),
                    branch("_tenpai", [push("_tenpai_seats", "_seat")]),
                    branch("_nagashi", [push("_nagashi_seats", "_seat")]),
                ],
            ),
            set_("_deltas", [0, 0, 0, 0]),
            branch(
                "len(_nagashi_seats) > 0",
                [
                    each(
                        "_nagashi_seats",
                        "_winner",
                        [
                            each(
                                "_seats",
                                "_payer",
                                [
                                    branch(
                                        "_payer != _winner",
                                        [
                                            set_("_amount", 2000),
                                            branch(
                                                "_winner == state.dealer or _payer == state.dealer",
                                                [set_("_amount", 4000)],
                                            ),
                                            set_(
                                                "_deltas",
                                                (
                                                    "replaced(_deltas, _payer, _deltas[_payer] "
                                                    "- _amount)"
                                                ),
                                            ),
                                            set_(
                                                "_deltas",
                                                (
                                                    "replaced(_deltas, _winner, "
                                                    "_deltas[_winner] + _amount)"
                                                ),
                                            ),
                                        ],
                                    )
                                ],
                            )
                        ],
                    ),
                ],
                [
                    branch(
                        "len(_tenpai_seats) > 0 and len(_tenpai_seats) < 4",
                        [
                            each(
                                "_seats",
                                "_seat",
                                [
                                    set_("_amount", "0 - 3000 // (4 - len(_tenpai_seats))"),
                                    branch(
                                        "_seat in _tenpai_seats",
                                        [set_("_amount", "3000 // len(_tenpai_seats)")],
                                    ),
                                    set_("_deltas", "replaced(_deltas, _seat, _amount)"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            each(
                "_seats",
                "_seat",
                [
                    call(
                        "_adjust_score",
                        {"actor": "{state.seats[_seat]}", "amount": "{_deltas[_seat]}"},
                    )
                ],
            ),
            set_(
                "state.result",
                {
                    "kind": literal("draw"),
                    "tenpai": "_tenpai_seats",
                    "nagashi": "_nagashi_seats",
                    "deltas": "_deltas",
                    "dealer_stays": "state.dealer in _tenpai_seats",
                },
            ),
            call("_end_hand"),
            set_("state.message", literal("Exhaustive draw. Scores settled.")),
        ],
    )
    families["end_hand"] = family(
        [],
        [
            set_("state.phase", literal("hand_end")),
            set_("state.actor", "state.seats[0]"),
        ],
    )
    families["standing"] = family(
        [PRIVATE_ACTOR],
        [set_("_score", "actor.score"), set_("_name", "actor.name")],
    )
    families["rank_match"] = family(
        [],
        [
            set_("_scores", []),
            set_("_seats", "state.seats"),
            each(
                "_seats",
                "_seat",
                [
                    call("_standing", {"actor": "{_seat}"}, ["_score"]),
                    push("_scores", "_score"),
                ],
            ),
            set_("_picked", []),
            set_("_standings", []),
            set_("_ranks", "indices(4)"),
            each(
                "_ranks",
                "_rank",
                [
                    set_("_best", -1),
                    set_("_candidates", "indices(4)"),
                    each(
                        "_candidates",
                        "_candidate",
                        [
                            branch(
                                "_candidate not in _picked",
                                [
                                    branch(
                                        "_best == -1",
                                        [set_("_best", "_candidate")],
                                        [
                                            branch(
                                                "_scores[_candidate] > _scores[_best]",
                                                [set_("_best", "_candidate")],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    call("_standing", {"actor": "{state.seats[_best]}"}, ["_score", "_name"]),
                    push(
                        "_standings",
                        {"rank": "_rank + 1", "seat": "_best", "name": "_name", "points": "_score"},
                    ),
                    push("_picked", "_best"),
                ],
            ),
            set_(
                "state.match_result",
                {
                    "winner": "_standings[0].seat",
                    "standings": "_standings",
                    "match_format": "state.match_format",
                },
            ),
        ],
    )
    families["advance_match"] = family(
        [],
        [
            set_("_last_dealer", "state.dealer"),
            set_("_last_round", "state.round_wind"),
            set_("_last_honba", "state.honba"),
            set_("_scores", []),
            set_("_seats", "state.seats"),
            each(
                "_seats",
                "_seat",
                [call("_standing", {"actor": "{_seat}"}, ["_score"]), push("_scores", "_score")],
            ),
            set_("_top", 0),
            set_("_indexes", [1, 2, 3]),
            each(
                "_indexes",
                "_index",
                [branch("_scores[_index] > _scores[_top]", [set_("_top", "_index")])],
            ),
            set_("_nominal", 0),
            branch("state.match_format == 'east_south'", [set_("_nominal", 1)]),
            set_(
                "_dealer_ron_priority",
                "state.result.kind == 'win' and state.result.dealer_stays and "
                "state.result.winners != null and len(state.result.winners) > 1",
            ),
            set_("_finished", "min(_scores[0], _scores[1], _scores[2], _scores[3]) < 0"),
            branch(
                "state.round_wind > _nominal and _scores[_top] >= 30000 "
                "and not _dealer_ron_priority",
                [set_("_finished", True)],
            ),
            branch(
                (
                    "state.round_wind >= _nominal and state.dealer == 3 and _top == 3 "
                    "and _scores[3] >= 30000 and state.result.dealer_stays and "
                    "state.result.kind != 'abort'"
                ),
                [set_("_finished", True)],
            ),
            branch(
                "not state.result.dealer_stays",
                [
                    set_("state.dealer", "state.dealer + 1"),
                    branch(
                        "state.dealer == 4",
                        [set_("state.dealer", 0), set_("state.round_wind", "state.round_wind + 1")],
                    ),
                    branch(
                        "state.result.kind == 'win'",
                        [set_("state.honba", 0)],
                        [set_("state.honba", "state.honba + 1")],
                    ),
                ],
                [set_("state.honba", "state.honba + 1")],
            ),
            branch(
                "state.round_wind > _nominal and _scores[_top] >= 30000 "
                "and not _dealer_ron_priority",
                [set_("_finished", True)],
            ),
            branch("state.round_wind > _nominal + 1", [set_("_finished", True)]),
            branch(
                "_finished",
                [
                    set_("state.dealer", "_last_dealer"),
                    set_("state.round_wind", "_last_round"),
                    set_("state.honba", "_last_honba"),
                    call(
                        "_adjust_score",
                        {"actor": "{state.seats[_top]}", "amount": "{state.sticks * 1000}"},
                    ),
                    set_("state.sticks", 0),
                    set_("state.finished", True),
                    set_("state.phase", literal("finished")),
                    call("_rank_match"),
                    set_(
                        "state.message", literal("Match complete. Ties follow original seat order.")
                    ),
                ],
                [call("_new_hand"), set_("state.message", literal("Next hand dealt."))],
            ),
        ],
    )
