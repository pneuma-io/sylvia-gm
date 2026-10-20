"""Declare Riichi hand analysis and scoring in the ordinary bounded Ruleset language."""

from scripts.build_riichi_ruleset import (
    PRIVATE_ACTOR,
    branch,
    call,
    dora_indexes,
    each,
    family,
    literal,
    parameter,
    push,
    set_,
)


def add_scoring(families, definitions):
    better_score = (
        "(_candidate.valid and not _score.valid) or "
        "(_candidate.valid == _score.valid and (_candidate.total > _score.total or "
        "(_candidate.total == _score.total and (_candidate.yakuman_units > "
        "_score.yakuman_units or (_candidate.yakuman_units == _score.yakuman_units "
        "and _candidate.han > _score.han)))))"
    )
    definitions.update(
        {
            "terminals": [0, 8, 9, 17, 18, 26, *range(27, 34)],
            "green_kinds": [19, 20, 21, 23, 25, 32],
            "sequence_starts": [suit * 9 + rank for suit in range(3) for rank in range(7)],
            "group_sizes": [[2] + [3] * (4 - melds) for melds in range(5)],
            "dora_successors": [suit * 9 + (rank + 1) % 9 for suit in range(3) for rank in range(9)]
            + [28, 29, 30, 27, 32, 33, 31],
        }
    )
    families["waits"] = family(
        [PRIVATE_ACTOR, parameter("tiles", "array", items={"type": "integer"})],
        [
            set_("_kinds", []),
            each("tiles", "_tile", [push("_kinds", "definitions.tiles[_tile].kind")]),
            set_(
                "_waits",
                (
                    "completions(_kinds, definitions.patterns, "
                    "definitions.group_sizes[len(actor.game.melds)], indices(34))"
                ),
            ),
            set_("_pairs", []),
            set_("_singles", []),
            set_("_terminals", []),
            set_("_types", "indices(34)"),
            set_("_only_terminals", True),
            each(
                "_types",
                "_kind",
                [
                    set_("_count", "occurrences(_kinds, _kind)"),
                    branch("_count == 2", [push("_pairs", "_kind")]),
                    branch("_count == 1", [push("_singles", "_kind")]),
                    branch(
                        "_count > 0",
                        [
                            branch(
                                "_kind in definitions.terminals",
                                [push("_terminals", "_kind")],
                                [set_("_only_terminals", False)],
                            ),
                        ],
                    ),
                ],
            ),
            branch(
                "len(actor.game.melds) == 0",
                [
                    branch(
                        "len(_pairs) == 6 and len(_singles) == 1", [push("_waits", "_singles[0]")]
                    ),
                    branch(
                        "_only_terminals and len(_terminals) == 13",
                        [set_("_waits", "definitions.terminals")],
                    ),
                    branch(
                        "_only_terminals and len(_terminals) == 12 and len(_pairs) == 1",
                        [
                            set_("_missing", "without(definitions.terminals, _terminals)"),
                            push("_waits", "_missing[0]"),
                        ],
                    ),
                ],
            ),
            set_("_all", "_kinds"),
            set_("_melds", "actor.game.melds"),
            each(
                "_melds",
                "_meld",
                [
                    set_("_meld_tiles", "_meld.tiles"),
                    each("_meld_tiles", "_tile", [push("_all", "definitions.tiles[_tile].kind")]),
                ],
            ),
            set_("_filtered", []),
            each(
                "_waits",
                "_kind",
                [
                    branch(
                        "occurrences(_all, _kind) < 4 and _kind not in _filtered",
                        [push("_filtered", "_kind")],
                    )
                ],
            ),
            set_("_waits", "_filtered"),
        ],
    )

    params = [
        PRIVATE_ACTOR,
        parameter("winning_tile", "integer"),
        parameter("is_tsumo", "boolean"),
        parameter("win_source"),
    ]
    families["score_hand"] = family(
        params,
        [
            set_(
                "_score",
                {
                    "valid": False,
                    "total": 0,
                    "han": 0,
                    "fu": 0,
                    "basic": 0,
                    "yakuman_units": 0,
                    "yaku": [],
                    "dora": 0,
                    "red": 0,
                    "ura": 0,
                },
            ),
            set_("_closed_tiles", "actor.game.hand"),
            branch(
                "not is_tsumo", [set_("_closed_tiles", "joined(_closed_tiles, one(winning_tile))")]
            ),
            set_("_kinds", []),
            each("_closed_tiles", "_tile", [push("_kinds", "definitions.tiles[_tile].kind")]),
            set_("_winning_kind", "definitions.tiles[winning_tile].kind"),
            set_(
                "_patterns",
                (
                    "partitions(_kinds, definitions.patterns, "
                    "definitions.group_sizes[len(actor.game.melds)])"
                ),
            ),
            each(
                "_patterns",
                "_partition",
                [
                    set_("_groups", []),
                    each(
                        "_partition",
                        "_pattern",
                        [
                            branch(
                                "_pattern < 34",
                                [
                                    push(
                                        "_groups",
                                        {
                                            "form": literal("pair"),
                                            "base": "_pattern",
                                            "opened": False,
                                            "quad": False,
                                        },
                                    ),
                                ],
                                [
                                    branch(
                                        "_pattern < 68",
                                        [
                                            push(
                                                "_groups",
                                                {
                                                    "form": literal("triplet"),
                                                    "base": "_pattern - 34",
                                                    "opened": False,
                                                    "quad": False,
                                                },
                                            ),
                                        ],
                                        [
                                            push(
                                                "_groups",
                                                {
                                                    "form": literal("sequence"),
                                                    "base": (
                                                        "definitions.sequence_starts[_pattern - 68]"
                                                    ),
                                                    "opened": False,
                                                    "quad": False,
                                                },
                                            ),
                                        ],
                                    )
                                ],
                            ),
                        ],
                    ),
                    set_("_closed_count", "len(_groups)"),
                    set_("_melds", "actor.game.melds"),
                    each(
                        "_melds",
                        "_meld",
                        [
                            push(
                                "_groups",
                                {
                                    "form": "_meld.form",
                                    "base": "_meld.base",
                                    "opened": "_meld.opened",
                                    "quad": "_meld.quad",
                                },
                            )
                        ],
                    ),
                    set_("_placements", "indices(_closed_count)"),
                    each(
                        "_placements",
                        "_placement",
                        [
                            set_("_group", "_groups[_placement]"),
                            branch(
                                (
                                    (
                                        "(_group.form == 'sequence' and _winning_kind >= "
                                        "_group.base and "
                                    )
                                    + (
                                        "_winning_kind <= _group.base + 2) or (_group.form != "
                                        "'sequence' "
                                    )
                                    + "and _winning_kind == _group.base)"
                                ),
                                [
                                    call(
                                        "_score_shape",
                                        {
                                            "actor": "{_actor_id}",
                                            "winning_tile": "{winning_tile}",
                                            "is_tsumo": "{is_tsumo}",
                                            "win_source": "{win_source}",
                                            "groups": "{_groups}",
                                            "placement": "{_placement}",
                                            "shape": "standard",
                                        },
                                        ["_candidate"],
                                    ),
                                    branch(
                                        (
                                            better_score
                                        ),
                                        [set_("_score", "_candidate")],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            branch(
                "len(actor.game.melds) == 0 and len(_closed_tiles) == 14",
                [
                    set_("_pair_count", 0),
                    set_("_distinct_terminals", 0),
                    set_("_only_terminals", True),
                    set_("_types", "indices(34)"),
                    each(
                        "_types",
                        "_kind",
                        [
                            set_("_count", "occurrences(_kinds, _kind)"),
                            branch("_count == 2", [set_("_pair_count", "_pair_count + 1")]),
                            branch(
                                "_count > 0",
                                [
                                    branch(
                                        "_kind in definitions.terminals",
                                        [set_("_distinct_terminals", "_distinct_terminals + 1")],
                                        [set_("_only_terminals", False)],
                                    )
                                ],
                            ),
                        ],
                    ),
                    set_("_shape", None),
                    branch("_pair_count == 7", [set_("_shape", literal("seven_pairs"))]),
                    branch(
                        "_only_terminals and _distinct_terminals == 13",
                        [set_("_shape", literal("orphans"))],
                    ),
                    branch(
                        "_shape != null",
                        [
                            call(
                                "_score_shape",
                                {
                                    "actor": "{_actor_id}",
                                    "winning_tile": "{winning_tile}",
                                    "is_tsumo": "{is_tsumo}",
                                    "win_source": "{win_source}",
                                    "groups": [],
                                    "placement": -1,
                                    "shape": "{_shape}",
                                },
                                ["_candidate"],
                            ),
                            branch(
                                (
                                    better_score
                                ),
                                [set_("_score", "_candidate")],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    effects = [
        set_("_closed", True),
        set_("_tiles", "actor.game.hand"),
        branch("not is_tsumo", [set_("_tiles", "joined(_tiles, one(winning_tile))")]),
        set_("_melds", "actor.game.melds"),
        each(
            "_melds",
            "_meld",
            [
                set_("_tiles", "joined(_tiles, _meld.tiles)"),
                branch("_meld.opened", [set_("_closed", False)]),
            ],
        ),
        set_("_kinds", []),
        set_("_suits", []),
        set_("_red", 0),
        set_("_all_simple", True),
        set_("_all_honors", True),
        set_("_all_terminals", True),
        set_("_all_outside", True),
        set_("_has_honors", False),
        set_("_all_green", True),
        each(
            "_tiles",
            "_tile",
            [
                set_("_face", "definitions.tiles[_tile]"),
                push("_kinds", "_face.kind"),
                branch("_face.red", [set_("_red", "_red + 1")]),
                branch(
                    "_face.kind >= 27",
                    [
                        set_("_all_simple", False),
                        set_("_all_terminals", False),
                        set_("_has_honors", True),
                    ],
                    [
                        set_("_all_honors", False),
                        set_("_suit", "_face.kind // 9"),
                        branch("_suit not in _suits", [push("_suits", "_suit")]),
                        branch(
                            "_face.kind % 9 == 0 or _face.kind % 9 == 8",
                            [set_("_all_simple", False)],
                            [set_("_all_terminals", False), set_("_all_outside", False)],
                        ),
                    ],
                ),
                branch("_face.kind not in definitions.green_kinds", [set_("_all_green", False)]),
            ],
        ),
        set_("_seat_wind", "27 + (actor.seat - state.dealer + 4) % 4"),
        set_("_round_wind", "27 + state.round_wind"),
        set_("_winning_kind", "definitions.tiles[winning_tile].kind"),
        set_("_sequences", []),
        set_("_triplets", []),
        set_("_concealed_sets", 0),
        set_("_quads", 0),
        set_("_dragons", 0),
        set_("_winds", 0),
        set_("_yakuhai", 0),
        set_("_pair", -1),
        set_("_meld_fu", 0),
        set_("_pair_fu", 0),
        set_("_wait_fu", 0),
        set_("_outside_groups", True),
        set_("_index", 0),
        each(
            "groups",
            "_group",
            [
                set_(
                    "_outside", "_group.base >= 27 or _group.base % 9 == 0 or _group.base % 9 == 8"
                ),
                branch(
                    "_group.form == 'pair'",
                    [
                        set_("_pair", "_group.base"),
                        branch("_pair >= 31", [set_("_pair_fu", "_pair_fu + 2")]),
                        branch("_pair == _seat_wind", [set_("_pair_fu", "_pair_fu + 2")]),
                        branch("_pair == _round_wind", [set_("_pair_fu", "_pair_fu + 2")]),
                        branch("_index == placement", [set_("_wait_fu", 2)]),
                    ],
                    [
                        branch(
                            "_group.form == 'sequence'",
                            [
                                push("_sequences", "_group.base"),
                                set_("_outside", "_group.base % 9 == 0 or _group.base % 9 == 6"),
                                branch(
                                    (
                                        (
                                            "_index == placement and (_winning_kind == "
                                            "_group.base + 1 or "
                                        )
                                        + (
                                            "(_group.base % 9 == 0 and _winning_kind == "
                                            "_group.base + 2) or "
                                        )
                                        + "(_group.base % 9 == 6 and _winning_kind == _group.base))"
                                    ),
                                    [set_("_wait_fu", 2)],
                                ),
                            ],
                            [
                                push("_triplets", "_group.base"),
                                set_(
                                    "_open",
                                    "_group.opened or (not is_tsumo and _index == placement)",
                                ),
                                branch(
                                    "not _open", [set_("_concealed_sets", "_concealed_sets + 1")]
                                ),
                                set_("_fu", 2),
                                branch("not _open", [set_("_fu", "_fu * 2")]),
                                branch(
                                    "_group.quad",
                                    [set_("_fu", "_fu * 4"), set_("_quads", "_quads + 1")],
                                ),
                                branch("_outside", [set_("_fu", "_fu * 2")]),
                                set_("_meld_fu", "_meld_fu + _fu"),
                                branch(
                                    "_group.base >= 31",
                                    [
                                        set_("_dragons", "_dragons + 1"),
                                        set_("_yakuhai", "_yakuhai + 1"),
                                    ],
                                ),
                                branch(
                                    "_group.base >= 27 and _group.base <= 30",
                                    [set_("_winds", "_winds + 1")],
                                ),
                                branch(
                                    "_group.base == _seat_wind", [set_("_yakuhai", "_yakuhai + 1")]
                                ),
                                branch(
                                    "_group.base == _round_wind", [set_("_yakuhai", "_yakuhai + 1")]
                                ),
                            ],
                        )
                    ],
                ),
                branch("not _outside", [set_("_outside_groups", False)]),
                set_("_index", "_index + 1"),
            ],
        ),
        set_("_sequence_pairs", 0),
        set_("_starts", "indices(27)"),
        each(
            "_starts",
            "_start",
            [set_("_sequence_pairs", "_sequence_pairs + occurrences(_sequences, _start) // 2")],
        ),
        set_("_sanshoku_sequences", False),
        set_("_sanshoku_triplets", False),
        set_("_straight", False),
        set_("_ranks", "indices(9)"),
        each(
            "_ranks",
            "_rank",
            [
                branch(
                    (
                        "_rank in _sequences and _rank + 9 in _sequences and _rank + 18 "
                        "in _sequences"
                    ),
                    [set_("_sanshoku_sequences", True)],
                ),
                branch(
                    ("_rank in _triplets and _rank + 9 in _triplets and _rank + 18 in _triplets"),
                    [set_("_sanshoku_triplets", True)],
                ),
            ],
        ),
        set_("_bases", [0, 9, 18]),
        each(
            "_bases",
            "_base",
            [
                branch(
                    ("_base in _sequences and _base + 3 in _sequences and _base + 6 in _sequences"),
                    [set_("_straight", True)],
                )
            ],
        ),
        set_(
            "_pinfu",
            (
                "shape == 'standard' and _closed and len(_sequences) == 4 and "
                "_pair_fu == 0 and _wait_fu == 0"
            ),
        ),
        set_("_nine_gates", False),
        set_("_pure_nine_gates", False),
        branch(
            "_closed and len(actor.game.melds) == 0 and len(_suits) == 1 and not _has_honors",
            [
                set_("_base", "_suits[0] * 9"),
                set_("_nine_gates", True),
                set_("_pure_nine_gates", True),
                each(
                    "_ranks",
                    "_rank",
                    [
                        set_("_required", 1),
                        branch("_rank == 0 or _rank == 8", [set_("_required", 3)]),
                        set_("_count", "occurrences(_kinds, _base + _rank)"),
                        branch("_count < _required", [set_("_nine_gates", False)]),
                        branch("_base + _rank == _winning_kind", [set_("_count", "_count - 1")]),
                        branch("_count != _required", [set_("_pure_nine_gates", False)]),
                    ],
                ),
            ],
        ),
        set_("_han", 0),
        set_("_yakuman", 0),
        set_("_yaku", []),
        set_("_yakuman_yaku", []),
        set_(
            "_heaven",
            (
                "is_tsumo and actor.game.draw_count == 1 and "
                "len(actor.game.discards) == 0 and not state.any_call and "
                "actor.seat == state.dealer"
            ),
        ),
    ]
    yaku = [
        ("actor.game.double_riichi", 2, "Double riichi"),
        ("actor.game.riichi and not actor.game.double_riichi", 1, "Riichi"),
        ("actor.game.riichi and actor.game.ippatsu", 1, "Ippatsu"),
        ("is_tsumo and _closed", 1, "Closed self-draw"),
        ("_all_simple", 1, "All simples"),
        ("_pinfu", 1, "Pinfu"),
        ("_closed and _sequence_pairs == 1", 1, "Iipeikou"),
        ("_closed and _sequence_pairs == 2", 3, "Ryanpeikou"),
        ("_yakuhai > 0", "_yakuhai", "Value sets"),
        ("win_source == 'rinshan'", 1, "Rinshan"),
        ("win_source == 'added_kan'", 1, "Robbing a kan"),
        ("win_source == 'live_draw' and len(state.live) == 0", 1, "Last-tile self-draw"),
        ("win_source == 'discard' and len(state.live) == 0", 1, "Last-discard win"),
        ("shape == 'seven_pairs'", 2, "Seven pairs"),
        ("len(_triplets) == 4", 2, "All triplets"),
        ("_concealed_sets == 3", 2, "Three concealed sets"),
        ("_quads == 3", 2, "Three kans"),
        ("_sanshoku_triplets", 2, "Three-color triplets"),
        ("_dragons == 2 and _pair >= 31", 2, "Little three dragons"),
        ("_all_outside and len(_sequences) == 0", 2, "All terminals and honors"),
        ("_sanshoku_sequences and _closed", 2, "Three-color sequences"),
        ("_sanshoku_sequences and not _closed", 1, "Three-color sequences"),
        ("_straight and _closed", 2, "Pure straight"),
        ("_straight and not _closed", 1, "Pure straight"),
        (
            "_outside_groups and len(_sequences) > 0 and _has_honors and _closed",
            2,
            "Mixed outside hand",
        ),
        (
            ("_outside_groups and len(_sequences) > 0 and _has_honors and not _closed"),
            1,
            "Mixed outside hand",
        ),
        (
            ("_outside_groups and len(_sequences) > 0 and not _has_honors and _closed"),
            3,
            "Pure outside hand",
        ),
        (
            ("_outside_groups and len(_sequences) > 0 and not _has_honors and not _closed"),
            2,
            "Pure outside hand",
        ),
        ("len(_suits) == 1 and _has_honors and _closed", 3, "Half flush"),
        ("len(_suits) == 1 and _has_honors and not _closed", 2, "Half flush"),
        ("len(_suits) == 1 and not _has_honors and _closed", 6, "Full flush"),
        ("len(_suits) == 1 and not _has_honors and not _closed", 5, "Full flush"),
    ]
    for condition, han, label in yaku:
        effects.append(
            branch(condition, [set_("_han", "_han + " + str(han)), push("_yaku", literal(label))])
        )
    yakuman = [
        ("_dragons == 3", 1, "Big three dragons"),
        ("_winds == 4", 2, "Big four winds"),
        ("_winds == 3 and _pair >= 27 and _pair <= 30", 1, "Little four winds"),
        ("_all_honors", 1, "All honors"),
        ("_all_terminals", 1, "All terminals"),
        ("_all_green", 1, "All green"),
        ("_quads == 4", 1, "Four kans"),
        (
            "_concealed_sets == 4 and (_wait_fu == 2 or _heaven)",
            2,
            "Four concealed sets, single wait",
        ),
        ("_concealed_sets == 4 and _wait_fu != 2 and not _heaven", 1, "Four concealed sets"),
        ("_nine_gates and (_pure_nine_gates or _heaven)", 2, "Pure nine gates"),
        ("_nine_gates and not _pure_nine_gates and not _heaven", 1, "Nine gates"),
        (
            ("shape == 'orphans' and (occurrences(_kinds, _winning_kind) == 2 or _heaven)"),
            2,
            "Thirteen orphans, thirteen-sided wait",
        ),
        (
            ("shape == 'orphans' and occurrences(_kinds, _winning_kind) != 2 and not _heaven"),
            1,
            "Thirteen orphans",
        ),
        ("_heaven", 1, "Blessing of heaven"),
        (
            (
                "is_tsumo and actor.game.draw_count == 1 and "
                "len(actor.game.discards) == 0 and not state.any_call and "
                "actor.seat != state.dealer"
            ),
            1,
            "Blessing of earth",
        ),
    ]
    for condition, units, label in yakuman:
        effects.append(
            branch(
                condition,
                [
                    set_("_yakuman", "_yakuman + " + str(units)),
                    push("_yakuman_yaku", literal(label)),
                ],
            )
        )
    effects += [
        set_("_fu", 20),
        branch(
            "shape == 'seven_pairs'",
            [set_("_fu", 25)],
            [
                branch("_closed and not is_tsumo", [set_("_fu", "_fu + 10")]),
                branch("is_tsumo and not _pinfu", [set_("_fu", "_fu + 2")]),
                set_("_fu", "_fu + _meld_fu + _pair_fu + _wait_fu"),
                branch("not _closed and _fu == 20", [set_("_fu", 30)]),
                set_("_fu", "((_fu + 9) // 10) * 10"),
            ],
        ),
        set_("_valid", "_han > 0 or _yakuman > 0"),
        set_("_dora", 0),
        set_("_ura", 0),
        *dora_indexes("_indicators"),
        each(
            "_indicators",
            "_index",
            [
                set_("_indicator_kind", "definitions.tiles[state.dead[4 + _index * 2]].kind"),
                set_(
                    "_dora",
                    ("_dora + occurrences(_kinds, definitions.dora_successors[_indicator_kind])"),
                ),
                branch(
                    "actor.game.riichi",
                    [
                        set_(
                            "_indicator_kind", "definitions.tiles[state.dead[5 + _index * 2]].kind"
                        ),
                        set_(
                            "_ura",
                            (
                                "_ura + occurrences(_kinds, definitions.dora_successors[_indicator"
                                "_kind])"
                            ),
                        ),
                    ],
                ),
            ],
        ),
        set_("_han", "_han + _dora + _red + _ura"),
        set_("_basic", 0),
        branch(
            "_yakuman > 0",
            [
                set_("_basic", "8000 * _yakuman"),
                set_("_yaku", "_yakuman_yaku"),
                set_("_han", 0),
                set_("_fu", 0),
            ],
            [
                branch(
                    "_han >= 13",
                    [set_("_basic", 8000)],
                    [
                        branch(
                            "_han >= 11",
                            [set_("_basic", 6000)],
                            [
                                branch(
                                    "_han >= 8",
                                    [set_("_basic", 4000)],
                                    [
                                        branch(
                                            "_han >= 6",
                                            [set_("_basic", 3000)],
                                            [
                                                branch(
                                                    "_han >= 5",
                                                    [set_("_basic", 2000)],
                                                    [
                                                        set_(
                                                            "_basic",
                                                            "min(2000, _fu * 2 ** (_han + 2))",
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
        ),
        set_("_multiplier", 4),
        branch("actor.seat == state.dealer", [set_("_multiplier", 6)]),
        set_("_payment", "((_basic * _multiplier + 99) // 100) * 100"),
        branch(
            "is_tsumo",
            [
                branch(
                    "actor.seat == state.dealer",
                    [set_("_payment", "((_basic * 2 + 99) // 100) * 300")],
                    [
                        set_(
                            "_payment",
                            "((_basic * 2 + 99) // 100) * 100 + ((_basic + 99) // 100) * 200",
                        )
                    ],
                ),
            ],
        ),
        set_(
            "_candidate",
            {
                "valid": "_valid",
                "total": "_payment",
                "han": "_han",
                "fu": "_fu",
                "basic": "_basic",
                "yakuman_units": "_yakuman",
                "yaku": "_yaku",
                "dora": "_dora",
                "red": "_red",
                "ura": "_ura",
                "big_dragons": "_dragons == 3",
                "big_winds": "_winds == 4",
                "fu_components": {"sets": "_meld_fu", "pair": "_pair_fu", "wait": "_wait_fu"},
                "shape": "shape",
                "concealed_sets": "_concealed_sets",
            },
        ),
    ]
    families["score_shape"] = family(
        [
            *params,
            parameter("groups", "array", items={"type": "object"}),
            parameter("placement", "integer"),
            parameter("shape"),
        ],
        effects,
    )
