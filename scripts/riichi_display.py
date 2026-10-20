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
    families["discard_history"] = family(
        [PRIVATE_ACTOR],
        [set_("_source_discards", "actor.game.discards")],
    )
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
                        "face": "definitions.tiles[state.winning_tile].face",
                        "flags": [],
                        "detail": literal("Winning discard"),
                        "emphasis": literal("accent"),
                    },
                ),
                set_("_complete_hand", {"pieces": "joined(_hand.pieces, one(_winning_piece))"}),
                set_("_hand", "merge(_hand, _complete_hand)"),
            ],
        ),
        set_("_pieces", []),
        set_("_riichi_marker", False),
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
                        "face": "definitions.tiles[_discard.tile].face",
                        "flags": [],
                    },
                ),
                branch(
                    "definitions.tiles[_discard.tile].red",
                    [set_("_piece", "merge(_piece, definitions.red_presentation)")],
                ),
                branch(
                    "_discard.riichi",
                    [
                        set_("_riichi_marker", True),
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
                            {
                                "detail": literal("Called into a meld"),
                                "emphasis": literal("muted"),
                                "flags": [literal("claimed")],
                            },
                        ),
                        set_("_piece", "merge(_piece, _flag)"),
                    ],
                ),
                branch(
                    "_riichi_marker and not _discard.claimed",
                    [
                        set_("_flag", {"flags": [literal("sideways")]}),
                        set_("_piece", "merge(_piece, _flag)"),
                        set_("_riichi_marker", False),
                    ],
                ),
                branch(
                    "state.last_discard != null and "
                    "_discard.tile == state.last_discard.tile and not _discard.claimed",
                    [
                        set_(
                            "_flag",
                            {
                                "emphasis": literal("accent"),
                                "flags": "joined(_piece.flags, one('latest'))",
                            },
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
                "role": literal("pond"),
                "visibility": literal("public"),
                "pieces": "_pieces",
            },
        ),
        set_("_regular", []),
        set_("_drawn_pieces", []),
        set_("_rack_pieces", "_hand.pieces"),
        each(
            "_rack_pieces",
            "_rack_piece",
            [
                branch(
                    "'drawn' in _rack_piece.flags",
                    [push("_drawn_pieces", "_rack_piece")],
                    [push("_regular", "_rack_piece")],
                )
            ],
        ),
        set_("_complete_hand", {"pieces": "joined(_regular, _drawn_pieces)"}),
        set_("_hand", "merge(_hand, _complete_hand)"),
        set_("_groups", ["_hand", "_discard_group"]),
        set_("_melds", "actor.game.melds"),
        set_("_index", 0),
        each(
            "_melds",
            "_meld",
            [
                set_("_pieces", []),
                set_("_tiles", "ordered(_meld.tiles)"),
                set_("_called", -1),
                set_("_added", -1),
                set_("_offset", "(_meld.source_seat - actor.seat + 4) % 4"),
                branch(
                    "_meld.opened",
                    [
                        call(
                            "_discard_history",
                            {"actor": "{state.seats[_meld.source_seat]}"},
                            ["_source_discards"],
                        ),
                        each(
                            "_source_discards",
                            "_source_discard",
                            [
                                branch(
                                    "_source_discard.claimed and "
                                    "_source_discard.tile in _meld.tiles",
                                    [set_("_called", "_source_discard.tile")],
                                )
                            ],
                        ),
                        branch(
                            "_meld.quad and _called != _meld.tiles[3]",
                            [set_("_added", "_meld.tiles[3]")],
                        ),
                    ],
                ),
                set_("_normal", []),
                each(
                    "_tiles",
                    "_normal_tile",
                    [
                        branch(
                            "_normal_tile != _called and _normal_tile != _added",
                            [push("_normal", "_normal_tile")],
                        )
                    ],
                ),
                set_("_ordered", "_tiles"),
                branch(
                    "not _meld.opened and definitions.tiles[_tiles[0]].red",
                    [
                        set_("_ordered", ["_tiles[1]", "_tiles[0]", "_tiles[2]", "_tiles[3]"]),
                    ],
                ),
                branch(
                    "_called >= 0",
                    [
                        set_("_slot", 0),
                        branch("_offset == 1", [set_("_slot", "len(_normal)")]),
                        branch("_offset == 2", [set_("_slot", 1)]),
                        set_(
                            "_ordered",
                            "joined(joined(portion(_normal, 0, _slot), one(_called)), "
                            "portion(_normal, _slot, len(_normal)))",
                        ),
                    ],
                ),
                each(
                    "_ordered",
                    "_tile",
                    [
                        set_(
                            "_piece",
                            {
                                "id": "'meld:' + text(_tile)",
                                "label": "definitions.tiles[_tile].label",
                                "face": "definitions.tiles[_tile].face",
                                "flags": [],
                            },
                        ),
                        branch(
                            "_tile == _called",
                            [
                                set_("_flag", {"flags": [literal("sideways")]}),
                                set_("_piece", "merge(_piece, _flag)"),
                            ],
                        ),
                        branch(
                            "not _meld.opened and (_tile == _ordered[0] or _tile == _ordered[3])",
                            [
                                set_("_flag", {"flags": [literal("back")]}),
                                set_("_piece", "merge(_piece, _flag)"),
                            ],
                        ),
                        branch(
                            "definitions.tiles[_tile].red",
                            [set_("_piece", "merge(_piece, definitions.red_presentation)")],
                        ),
                        push("_pieces", "_piece"),
                        branch(
                            "_tile == _called and _added >= 0",
                            [
                                push(
                                    "_pieces",
                                    {
                                        "id": "'meld:' + text(_added)",
                                        "label": "definitions.tiles[_added].label",
                                        "face": "definitions.tiles[_added].face",
                                        "flags": [literal("stacked")],
                                    },
                                )
                            ],
                        ),
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
                        "role": literal("meld"),
                        "source": "definitions.call_sources[_offset]",
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
                "badge": "definitions.wind_glyphs[(actor.seat - state.dealer + 4) % 4]",
                "score": "text(actor.score)",
                "active": "_actor_id == state.actor and not state.finished",
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
                        "face": "definitions.tiles[state.dead[4 + _index * 2]].face",
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
                        "role": literal("indicators"),
                        "visibility": literal("public"),
                        "pieces": "_dora",
                    },
                    {
                        "id": literal("choices"),
                        "label": literal("Actions"),
                        "role": literal("actions"),
                        "visibility": literal("public"),
                        "pieces": "_options",
                    },
                    {
                        "id": literal("result"),
                        "label": literal("Table"),
                        "role": literal("message"),
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
            set_(
                "state.public_observation",
                {
                    "table": "actor.game.public_table",
                    "result": "state.result",
                    "match_result": "state.match_result",
                },
            ),
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
