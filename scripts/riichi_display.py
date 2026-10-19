"""Present a complete readable table and the active opponent's permitted context."""

from scripts.build_riichi_ruleset import (
    PRIVATE_ACTOR,
    branch,
    call,
    dora_indexes,
    each,
    family,
    literal,
    push,
    set_,
)


def add_display(families):
    families["seat_table"]["effects"] += [
        branch(
            "actor.game.revealed",
            [
                set_("_visible", {"visibility": literal("public")}),
                set_("_hand", "merge(_hand, _visible)"),
            ],
        ),
        branch(
            "actor.game.revealed and state.result != null and state.result.kind == 'win' "
            "and actor.seat in state.winners and not state.win_tsumo",
            [
                set_(
                    "_winning_piece",
                    {
                        "id": literal("winning-discard"),
                        "label": "definitions.tiles[state.winning_tile].label",
                        "detail": literal("Winning discard"),
                        "emphasis": literal("accent"),
                    },
                ),
                set_("_complete_hand", {"pieces": "joined(_hand.pieces, one(_winning_piece))"}),
                set_("_hand", "merge(_hand, _complete_hand)"),
            ],
        ),
        set_("_pieces", []),
        set_("_discards", "actor.game.discards"),
        each(
            "_discards",
            "_discard",
            [
                set_(
                    "_piece",
                    {
                        "id": "'discard:' + text(_discard.tile)",
                        "label": "definitions.tiles[_discard.tile].label",
                    },
                ),
                branch(
                    "definitions.tiles[_discard.tile].red",
                    [set_("_piece", "merge(_piece, definitions.red_presentation)")],
                ),
                branch(
                    "_discard.riichi",
                    [
                        set_(
                            "_flag",
                            {
                                "detail": literal("Riichi declaration"),
                                "emphasis": literal("accent"),
                            },
                        ),
                        set_("_piece", "merge(_piece, _flag)"),
                    ],
                ),
                branch(
                    "_discard.claimed",
                    [
                        set_(
                            "_flag",
                            {"detail": literal("Called into a meld"), "emphasis": literal("muted")},
                        ),
                        set_("_piece", "merge(_piece, _flag)"),
                    ],
                ),
                branch(
                    "state.last_discard != null and "
                    "_discard.tile == state.last_discard.tile and not _discard.claimed",
                    [
                        set_(
                            "_flag",
                            {"detail": literal("Latest discard"), "emphasis": literal("accent")},
                        ),
                        set_("_piece", "merge(_piece, _flag)"),
                    ],
                ),
                push("_pieces", "_piece"),
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
        set_("_groups", ["_hand", "_discard_group"]),
        set_("_melds", "actor.game.melds"),
        set_("_index", 0),
        each(
            "_melds",
            "_meld",
            [
                set_("_pieces", []),
                set_("_tiles", "ordered(_meld.tiles)"),
                each(
                    "_tiles",
                    "_tile",
                    [
                        set_(
                            "_piece",
                            {
                                "id": "'meld:' + text(_tile)",
                                "label": "definitions.tiles[_tile].label",
                            },
                        ),
                        branch(
                            "definitions.tiles[_tile].red",
                            [set_("_piece", "merge(_piece, definitions.red_presentation)")],
                        ),
                        push("_pieces", "_piece"),
                    ],
                ),
                set_("_label", literal("Pon")),
                branch("_meld.form == 'sequence'", [set_("_label", literal("Chi"))]),
                branch(
                    "_meld.quad",
                    [
                        set_("_label", literal("Open kan")),
                        branch("not _meld.opened", [set_("_label", literal("Concealed kan"))]),
                    ],
                ),
                push(
                    "_groups",
                    {
                        "id": "'meld:' + text(_index)",
                        "label": "_label",
                        "visibility": literal("public"),
                        "pieces": "_pieces",
                    },
                ),
                set_("_index", "_index + 1"),
            ],
        ),
        set_(
            "_seat_label",
            ("actor.name + ' / ' + definitions.seat_winds[(actor.seat - state.dealer + 4) % 4]"),
        ),
        branch("actor.game.riichi", [set_("_seat_label", "_seat_label + ' / Riichi'")]),
        branch(
            "state.result != null and state.result.kind == 'draw'",
            [
                branch(
                    "actor.game.revealed",
                    [set_("_seat_label", "_seat_label + ' / Tenpai'")],
                    [set_("_seat_label", "_seat_label + ' / Noten'")],
                ),
            ],
        ),
        set_(
            "_area",
            {
                "id": "_actor_id",
                "label": "_seat_label",
                "position": "definitions.positions[actor.seat]",
                "groups": "_groups",
            },
        ),
    ]
    families["center_table"]["effects"] = [
        set_("_dora", []),
        *dora_indexes("_indexes"),
        each(
            "_indexes",
            "_index",
            [
                push(
                    "_dora",
                    {
                        "id": "'dora:' + text(_index)",
                        "label": "definitions.tiles[state.dead[4 + _index * 2]].label",
                    },
                )
            ],
        ),
        call("_choice_table", {"actor": "{state.actor}"}, ["_options", "_turn"]),
        set_("_info", [{"id": literal("message"), "label": "state.message"}]),
        branch(
            "state.finished and state.match_result != null",
            [
                set_("_standings", "state.match_result.standings"),
                each(
                    "_standings",
                    "_standing",
                    [
                        push(
                            "_info",
                            {
                                "id": "'place:' + text(_standing.seat)",
                                "label": (
                                    "text(_standing.rank) + '. ' + _standing.name + ': ' "
                                    "+ text(_standing.points)"
                                ),
                            },
                        ),
                    ],
                ),
            ],
        ),
        branch(
            "state.pending_kan != null and state.phase == 'kan_response'",
            [
                push(
                    "_info",
                    {
                        "id": literal("kan"),
                        "label": (
                            "'Kan declared: ' + definitions.tiles[state.last_discard.tile].label"
                        ),
                    },
                ),
            ],
        ),
        branch(
            "state.result != null and state.result.kind == 'win'",
            [
                set_("_wins", "state.result.winners"),
                each(
                    "_wins",
                    "_win",
                    [
                        set_(
                            "_score_summary",
                            (
                                "text(_win.score.han) + ' han / ' "
                                "+ text(_win.score.fu) + ' fu; ' "
                                "+ 'dora ' + text(_win.score.dora) "
                                "+ ', red ' + text(_win.score.red) "
                                "+ ', ura ' + text(_win.score.ura)"
                            ),
                        ),
                        branch(
                            "_win.score.yakuman_units > 0",
                            [
                                set_(
                                    "_score_summary", "text(_win.score.yakuman_units) + ' yakuman'"
                                ),
                            ],
                        ),
                        push(
                            "_info",
                            {
                                "id": "'winner:' + text(_win.seat)",
                                "label": "_win.name + ' +' + text(_win.gain)",
                                "detail": "_score_summary",
                                "emphasis": literal("accent"),
                            },
                        ),
                        set_("_yaku", "_win.score.yaku"),
                        set_("_index", 0),
                        each(
                            "_yaku",
                            "_label",
                            [
                                push(
                                    "_info",
                                    {
                                        "id": "'yaku:' + text(_win.seat) + ':' + text(_index)",
                                        "label": "_label",
                                    },
                                ),
                                set_("_index", "_index + 1"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
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
                    {
                        "id": literal("result"),
                        "label": literal("Table"),
                        "visibility": literal("public"),
                        "pieces": "_info",
                    },
                ],
            },
        ),
    ]
    # No wall order, dead-wall tiles, other concealed hands, or unrevealed ura enters this view.
    families["public_seat"] = family(
        [PRIVATE_ACTOR],
        [
            set_(
                "_public_seat",
                {
                    "name": "actor.name",
                    "seat": "actor.seat",
                    "score": "actor.score",
                    "riichi": "actor.game.riichi",
                    "discards": "actor.game.discards",
                    "melds": "actor.game.melds",
                    "concealed_count": "len(actor.game.hand)",
                },
            ),
        ],
    )
    families["actor_public_table"] = family(
        [
            PRIVATE_ACTOR,
            {"name": "records", "type": "array", "source": "choice", "items": {"type": "object"}},
        ],
        [
            set_("_indicators", []),
            *dora_indexes("_indexes"),
            each(
                "_indexes",
                "_index",
                [push("_indicators", "definitions.tiles[state.dead[4 + _index * 2]].kind")],
            ),
            set_(
                "actor.game.public_table",
                {
                    "seats": "records",
                    "dora_indicators": "_indicators",
                    "live_count": "len(state.live)",
                    "dealer": "state.dealer",
                    "round_wind": "state.round_wind",
                    "honba": "state.honba",
                    "sticks": "state.sticks",
                    "last_discard": "state.last_discard",
                    "phase": "state.phase",
                    "message": "state.message",
                },
            ),
            set_("state.public_observation", {
                "table": "actor.game.public_table", "result": "state.result",
                "match_result": "state.match_result",
            }),
        ],
    )
    families["refresh"]["effects"] += [
        set_("_records", []),
        set_("_seats", "state.seats"),
        each(
            "_seats",
            "_seat",
            [
                call("_public_seat", {"actor": "{_seat}"}, ["_public_seat"]),
                push("_records", "_public_seat"),
            ],
        ),
        call("_actor_public_table", {"actor": "{state.actor}", "records": "{_records}"}),
    ]
