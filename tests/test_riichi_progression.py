"""Controlled physical walls exercise the admitted package through ordinary actions."""

import io
from contextlib import contextmanager
from functools import lru_cache

import pytest
import yaml
from scripts.build_riichi_ruleset import NS, call, family, hand_defaults, source

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain.authority import StateValue, WriterRole
from tests.test_riichi_mahjong import Table, physical_tiles
from tests.test_riichi_scoring import tiles


@lru_cache(maxsize=4)
def scenario_archive(ron_honba=None, nagashi_closed=None):
    declaration = source()
    if ron_honba is not None:
        declaration["definitions"]["profile"]["ron_honba"] = ron_honba
    if nagashi_closed is not None:
        declaration["definitions"]["profile"]["nagashi_closed_required"] = nagashi_closed
    declaration["mechanic_families"]["fixture_refresh"] = family(
        [],
        [
            call("_refresh"),
            {"publish": {"fields": {"message": "'Controlled fixture prepared.'"}}},
        ],
    )
    declaration["public_actions"].append(
        {
            **declaration["public_actions"][1],
            "name": "fixture_refresh",
            "family": "fixture_refresh",
            "description": "Test-only projection wrapper, absent from the shipped package.",
        }
    )
    declaration["mechanic_families"]["fixture_exhaustive"] = family(
        [],
        [
            call("_exhaustive"),
            call("_refresh"),
            {"publish": {"fields": {"result": "state.result"}}},
        ],
    )
    declaration["public_actions"].append(
        {
            **declaration["public_actions"][1],
            "name": "fixture_exhaustive",
            "family": "fixture_exhaustive",
            "description": "Test-only end-wall wrapper, absent from the shipped package.",
        }
    )
    compiled = compile_ruleset_source(yaml.safe_dump(declaration).encode())
    with materialize_package_archive((compiled.draft,)) as artifact:
        return artifact.archive.read()


@contextmanager
def scenario(
    tmp_path,
    hands,
    *,
    overrides=None,
    games=None,
    discards=None,
    scores=None,
    exposed=None,
    ron_honba=None,
    nagashi_closed=None,
    exhausted=False,
    dead_wall=None,
):
    pool = list(range(136))

    def allocate(notation):
        result = []
        for example in tiles(notation):
            kind = example // 4
            tile = next(tile for tile in pool if tile // 4 == kind)
            pool.remove(tile)
            result.append(tile)
        return result

    physical_hands = [allocate(hand) if hand else None for hand in hands]
    reserved_dead = allocate(dead_wall) if dead_wall is not None else None
    discards = [allocate(text) for text in (discards or ["", "", "", ""])]
    melds = {}
    for seat, declarations in (exposed or {}).items():
        melds[seat] = []
        for notation, opened, quad in declarations:
            values = allocate(notation)
            melds[seat].append(
                {
                    "form": "triplet",
                    "base": values[0] // 4,
                    "opened": opened,
                    "quad": quad,
                    "tiles": values,
                    "source_seat": (seat + 1) % 4,
                }
            )
    for seat, hand in enumerate(physical_hands):
        if hand is None:
            length = 14 if seat == 0 else 13
            physical_hands[seat], pool = pool[:length], pool[length:]
    dead, live = (reserved_dead, pool) if reserved_dead is not None else (pool[:14], pool[14:])
    assert len(dead) == 14
    if exhausted:
        for index, tile in enumerate(live):
            discards[(0, 2, 3)[index % 3]].append(tile)
        live = []
    seats = ["you.seat", "you.right", "you.across", "you.left"]
    entities = {
        identity: {
            "name": "You" if seat == 0 else f"Opponent {seat}",
            "seat": seat,
            "participant_id": "you" if seat == 0 else None,
            "control": "player" if seat == 0 else "gm",
            "score": (scores or [25000] * 4)[seat],
            "game": {
                **hand_defaults(),
                "hand": physical_hands[seat],
                "draw_count": 2,
                "last_draw": physical_hands[seat][-1] if seat == 0 else None,
                "discards": [
                    {"tile": tile, "claimed": False, "riichi": False} for tile in discards[seat]
                ],
                "melds": melds.get(seat, []),
                **(games or {}).get(seat, {}),
            },
        }
        for seat, identity in enumerate(seats)
    }
    state = {
        "player": "you",
        "seats": seats,
        "phase": "discard",
        "actor": seats[0],
        "dealer": 0,
        "round_wind": 0,
        "honba": 0,
        "sticks": 0,
        "sequence": 0,
        "finished": False,
        "match_format": "east_only",
        "live": live,
        "dead": dead,
        "kan_count": 0,
        "kan_pending_dora": 0,
        "any_call": True,
        "pending_riichi": None,
        "pending_kan": None,
        "ron_seats": [],
        "kan_owners": [],
        "abort_after_discard": False,
        "result": None,
        "message": "Fixture",
        **(overrides or {}),
    }
    with AuthorityOwner.open(tmp_path / "table.sqlite3", "riichi-test") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(scenario_archive(ron_honba, nagashi_closed)), install_id="riichi"
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="riichi",
                display_name="Controlled Riichi",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn",
                initial_state=(
                    StateValue(NS, "state", state),
                    StateValue(NS, "entities", entities),
                ),
            )
        )
        table = Table(owner)
        table.act("fixture_refresh", {})
        yield table


def choose(table, op, kind=None):
    option = next(
        row
        for row in table.state()["choices"]
        if row["op"] == op and (kind is None or row["tiles"][0] // 4 == kind)
    )
    return table.act("take_action", {"choice": option["id"]})


def passes(table, count=3):
    for _ in range(count):
        if table.state()["phase"] not in {"response", "kan_response"}:
            break
        choose(table, "pass")


@pytest.mark.parametrize("ron_honba", ["each_winner", "nearest_winner"])
def test_three_ron_pays_every_winner_and_nearest_takes_sticks(tmp_path, ron_honba):
    hands = ["666m19m19p19s12345z", "12345m23455p678s", "34578m111p222s11z", "45789m333p789s22z"]
    # The discarder holds three unused physical copies of 6m; only one is discarded.
    with scenario(
        tmp_path,
        hands,
        overrides={"honba": 2, "sticks": 3},
        games={seat: {"riichi": True} for seat in (1, 2, 3)},
        scores=[25000, 24000, 24000, 24000],
        ron_honba=ron_honba,
    ) as table:
        choose(table, "discard", 5)
        for _ in range(3):
            choose(table, "ron")
        result = table.state()["result"]
        assert result["kind"] == "win" and len(result["winners"]) == 3
        assert [winner["seat"] for winner in result["winners"]] == [1, 2, 3]
        assert result["winners"][0]["gain"] == sum(result["winners"][0]["payments"]) + 3000
        for index, row in enumerate(result["winners"]):
            bonus = 600 if index == 0 or ron_honba == "each_winner" else 0
            assert row["payments"][0] == row["score"]["total"] + bonus
        assert sum(seat["score"] for seat in table.entities().values()) == 100000
        assert sorted(physical_tiles(table)) == list(range(136))


@pytest.mark.parametrize("discarder", [0, 2])
def test_ranked_multi_ron_awards_honba_and_deposits_in_discarder_turn_order(tmp_path, discarder):
    hands = ["666m19m19p19s12345z", "12345m23455p678s", "34578m111p222s11z", "45789m333p789s22z"]
    seats = ["you.seat", "you.right", "you.across", "you.left"]
    winners = [(discarder + offset) % 4 for offset in (1, 2, 3)]
    with scenario(
        tmp_path,
        [hands[(seat - discarder) % 4] for seat in range(4)],
        overrides={"actor": seats[discarder], "honba": 2, "sticks": 3},
        games={seat: {"riichi": True} for seat in winners},
        scores=[25000 if seat == discarder else 24000 for seat in range(4)],
    ) as table:
        choose(table, "discard", 5)
        for _ in winners:
            choose(table, "ron")
        result = table.state()["result"]
        assert [winner["seat"] for winner in result["winners"]] == winners
        assert [
            row["payments"][discarder] - row["score"]["total"] for row in result["winners"]
        ] == [600, 0, 0]
        assert [row["gain"] - sum(row["payments"]) for row in result["winners"]] == [3000, 0, 0]
        assert result["dealer_stays"] is (0 in winners)
        assert sum(seat["score"] for seat in table.entities().values()) == 100000
        assert sorted(physical_tiles(table)) == list(range(136))


def test_pon_beats_chi_and_called_tile_cannot_be_swapped(tmp_path):
    with scenario(
        tmp_path, ["3m123456789p1122s", "12456m789s11122z", "33m456789s12344z", None]
    ) as table:
        choose(table, "discard", 2)
        choose(table, "chi")
        choose(table, "pon")
        passes(table)
        assert table.state()["actor"] == "you.across" and table.state()["phase"] == "discard"
        game = table.entities()["you.across"]["game"]
        assert len(game["melds"]) == 1 and game["melds"][0]["form"] == "triplet"
        assert 2 in game["forbidden_discards"]
        assert sorted(physical_tiles(table)) == list(range(136))


def test_concealed_kan_uses_dead_wall_and_reveals_indicator(tmp_path):
    with scenario(tmp_path, ["1111m234p567s1234z", None, None, None]) as table:
        wall = len(table.state()["live"])
        choose(table, "ankan")
        passes(table)
        assert table.state()["phase"] == "discard" and table.state()["actor"] == "you.seat"
        assert table.state()["kan_count"] == 1 and table.state()["kan_pending_dora"] == 0
        assert len(table.state()["live"]) == wall - 1
        assert table.entities()["you.seat"]["game"]["rinshan"]
        assert sorted(physical_tiles(table)) == list(range(136))


def test_furiten_checks_all_waits_not_only_the_discarded_winning_kind(tmp_path):
    with scenario(
        tmp_path,
        ["6m123456789p1122z", "12345m23455p678s", None, None],
        discards=["", "3m", "", ""],
        games={1: {"riichi": True}},
    ) as table:
        choose(table, "discard", 5)
        assert table.entities()["you.right"]["game"]["riichi_furiten"]
        assert 1 not in table.state()["ron_seats"]


@pytest.mark.parametrize("match_format,wind", [("east_only", 0), ("east_south", 1)])
def test_final_dealer_leading_at_target_ends_match_and_ties_follow_start_order(
    tmp_path, match_format, wind
):
    with scenario(
        tmp_path,
        [None] * 4,
        overrides={
            "phase": "hand_end",
            "match_format": match_format,
            "dealer": 3,
            "round_wind": wind,
            "result": {"kind": "win", "dealer_stays": True},
        },
        scores=[20000, 20000, 20000, 40000],
    ) as table:
        choose(table, "continue")
        assert table.state()["phase"] == "finished" and table.state()["finished"]


@pytest.mark.parametrize("match_format,wind", [("east_only", 1), ("east_south", 2)])
def test_extension_target_does_not_override_a_dealer_among_multiple_ron_winners(
    tmp_path,
    match_format,
    wind,
):
    hands = ["666m19m19p19s12345z", "12345m23455p678s", "34578m111p222s11z", "45789m333p789s22z"]
    with scenario(
        tmp_path,
        [hands[2], hands[3], hands[0], hands[1]],
        overrides={"actor": "you.across", "round_wind": wind, "match_format": match_format},
        games={seat: {"riichi": True} for seat in (0, 1, 3)},
        scores=[10000, 18000, 42100, 29900],
    ) as table:
        choose(table, "discard", 5)
        for _ in range(3):
            choose(table, "ron")
        assert table.state()["result"]["dealer_stays"]
        scores = [seat["score"] for seat in table.entities().values()]
        assert table.entities()["you.left"]["score"] > 30000
        assert table.entities()["you.seat"]["score"] < max(scores)
        choose(table, "continue")
        assert not table.state()["finished"] and table.state()["phase"] == "discard"
        assert table.state()["round_wind"] == wind and table.state()["dealer"] == 0
        assert table.state()["honba"] == 1
        assert sorted(physical_tiles(table)) == list(range(136))


def test_exhaustive_draw_and_continue_preserve_match_state(tmp_path):
    with scenario(tmp_path, [None] * 4, discards=["2m", "2p", "2s", "3p"]) as table:
        # Follow the full live wall through ordinary discard/pass commands, without wins or calls.
        start_tiles = sorted(physical_tiles(table))
        for _ in range(320):
            phase = table.state()["phase"]
            if phase == "hand_end":
                break
            choose(table, "pass" if phase in ("response", "kan_response") else "discard")
        assert table.state()["phase"] == "hand_end"
        assert table.state()["result"]["kind"] == "draw"
        assert sorted(physical_tiles(table)) == start_tiles == list(range(136))
        honba = table.state()["honba"]
        choose(table, "continue")
        assert table.state()["phase"] == "discard" and table.state()["honba"] == honba + 1
        assert sorted(physical_tiles(table)) == list(range(136))


def test_riichi_can_keep_or_discard_the_red_five(tmp_path):
    with scenario(tmp_path, ["123455m23455p678s", None, None, None]) as table:
        options = [
            row
            for row in table.state()["choices"]
            if row["op"] == "riichi" and row["tiles"][0] // 4 == 4
        ]
        assert len(options) == 2
        assert {row["tiles"][0] for row in options} == {16, 17}
        table.act("take_action", {"choice": options[0]["id"]})
        passes(table)
        game = table.entities()["you.seat"]["game"]
        assert game["riichi"] and table.entities()["you.seat"]["score"] == 24000
        assert table.state()["sticks"] == 1 and game["riichi_waits"]


def test_ron_on_riichi_declaration_does_not_take_the_deposit(tmp_path):
    with scenario(
        tmp_path, ["123455m23455p678s", "12346m23455p678s", None, None], games={1: {"riichi": True}}
    ) as table:
        choose(table, "riichi", 4)
        choose(table, "ron")
        passes(table)
        winner = table.state()["result"]["winners"][0]
        assert table.entities()["you.seat"]["score"] == 25000 - winner["payments"][0]
        assert not table.entities()["you.seat"]["game"]["riichi"]
        assert table.state()["sticks"] == 0


@pytest.mark.parametrize("orphans", [True, False])
def test_only_kokushi_can_rob_a_concealed_kan(tmp_path, orphans):
    waiting = "99m19p19s1234567z" if orphans else "23m456789p678s55z"
    with scenario(
        tmp_path, ["1111m234p567s1234z", waiting, None, None], games={1: {"riichi": not orphans}}
    ) as table:
        choose(table, "ankan")
        if orphans:
            assert any(row["op"] == "ron" for row in table.state()["choices"])
            center = next(
                area for area in table.state()["table"]["areas"] if area["id"] == "shared"
            )
            assert len(center["groups"][0]["pieces"]) == 2
            choose(table, "ron")
            passes(table)
            assert table.state()["result"]["kind"] == "win"
            assert table.state()["result"]["winners"][0]["score"]["shape"] == "orphans"
            assert table.state()["kan_count"] == 0
        else:
            passes(table)
            assert table.state()["phase"] == "discard" and table.state()["kan_count"] == 1
        assert sorted(physical_tiles(table)) == list(range(136))


@pytest.mark.parametrize("stacked,payer,liable", [(False, 16000, 16000), (True, 48000, 16000)])
@pytest.mark.parametrize("honba", [0, 2])
def test_pao_splits_only_the_liable_yakuman_component(tmp_path, stacked, payer, liable, honba):
    # A terminal meld avoids all-honors; East adds the non-liable stacked yakuman.
    fourth = "111z" if stacked else "111m"
    hand = "2z" if stacked else "1z"
    discard_hand = "2z123456m234p5678s" if stacked else "1z123456m234p5678s"
    with scenario(
        tmp_path,
        [discard_hand, hand, None, None],
        games={1: {"pao_dragon": 2}},
        overrides={"honba": honba},
        exposed={1: [(text, True, False) for text in ("555z", "666z", "777z", fourth)]},
    ) as table:
        choose(table, "discard", 28 if stacked else 27)
        choose(table, "ron")
        passes(table)
        winner = table.state()["result"]["winners"][0]
        assert winner["payments"][0] == payer
        assert winner["payments"][2] == liable + honba * 300
        assert winner["payments"][3] == 0
        assert sum(seat["score"] for seat in table.entities().values()) == 100000
        assert sorted(physical_tiles(table)) == list(range(136))


@pytest.mark.parametrize("prior_concealed_kan", [False, True])
def test_pao_trigger_counts_open_dragon_groups_in_the_selected_profile(
    tmp_path, prior_concealed_kan
):
    # Preserve this profile's open-group interpretation; exact Mahjong Soul treatment
    # of a prior concealed kan remains unconfirmed by the available ranked rules.
    first = ("5555z", False, True) if prior_concealed_kan else ("555z", True, False)
    with scenario(
        tmp_path,
        ["7z123456m234p5678s", "77z123m44p", None, None],
        exposed={1: [first, ("666z", True, False)]},
        overrides={"kan_count": int(prior_concealed_kan)},
    ) as table:
        choose(table, "discard", 33)
        choose(table, "pon", 33)
        passes(table)
        assert table.entities()["you.right"]["game"]["pao_dragon"] == (
            None if prior_concealed_kan else 0
        )
        assert sorted(physical_tiles(table)) == list(range(136))


@pytest.mark.parametrize(
    "hand,waits,allowed",
    [
        ("1111m234p567s2255z", [28, 31], True),
        ("11112345678999m", list(range(9)), False),
        ("1111222333m45p55s", [11, 14], True),
    ],
)
def test_riichi_kan_requires_the_drawn_fourth_tile_and_unchanged_wait_types(
    tmp_path,
    hand,
    waits,
    allowed,
):
    with scenario(
        tmp_path,
        [hand, None, None, None],
        games={0: {"riichi": True, "riichi_waits": waits, "last_draw": 3}},
    ) as table:
        assert any(row["op"] == "ankan" for row in table.state()["choices"]) is allowed
        discards = [row["tiles"][0] for row in table.state()["choices"] if row["op"] == "discard"]
        assert discards == [3]
        if allowed:
            choose(table, "ankan")
            passes(table)
            assert table.state()["kan_count"] == 1
            assert sorted(physical_tiles(table)) == list(range(136))


def test_riichi_kan_filters_impossible_waits_using_all_owned_tiles(tmp_path):
    # Riichi 3333555666888m waits only on 4m: all four copies of 3m are already owned.
    # Drawing 6m permits a concealed kan of 6m without changing that wait.
    with scenario(
        tmp_path,
        ["3333555666888m6m", None, None, None],
        games={0: {"riichi": True, "riichi_waits": [3]}},
    ) as table:
        game = table.entities()["you.seat"]["game"]
        assert game["last_draw"] // 4 == 5
        kans = [row for row in table.state()["choices"] if row["op"] == "ankan"]
        assert [row["base"] for row in kans] == [5]
        assert [row["tiles"][0] for row in table.state()["choices"] if row["op"] == "discard"] == [
            game["last_draw"]
        ]
        choose(table, "ankan", 5)
        passes(table)
        game = table.entities()["you.seat"]["game"]
        assert table.state()["kan_count"] == 1
        assert game["melds"][0]["quad"] and game["melds"][0]["base"] == 5
        assert game["riichi_waits"] == [3]
        assert sorted(physical_tiles(table)) == list(range(136))


@pytest.mark.parametrize("op", ["minkan", "kakan"])
def test_open_and_added_kan_replace_a_tile_and_delay_indicator_until_discard(tmp_path, op):
    hands = ["3m123456789p1122s", "333m456p789s1122z", None, None]
    exposed = {}
    if op == "kakan":
        hands = ["3m456p789s1122z", None, None, None]
        exposed = {0: [("333m", True, False)]}
    with scenario(tmp_path, hands, exposed=exposed) as table:
        if op == "minkan":
            choose(table, "discard", 2)
        choose(table, op)
        passes(table)
        assert table.state()["kan_count"] == 1 and table.state()["kan_pending_dora"] == 1
        actor = table.entities()[table.state()["actor"]]
        assert actor["game"]["rinshan"] and actor["game"]["melds"][0]["quad"]
        assert sorted(physical_tiles(table)) == list(range(136))
        choose(table, "discard")
        assert table.state()["kan_pending_dora"] == 0
        center = next(area for area in table.state()["table"]["areas"] if area["id"] == "shared")
        assert len(center["groups"][0]["pieces"]) == 2


def test_pending_indicator_reveals_before_a_second_added_kan_is_robbed(tmp_path):
    with scenario(
        tmp_path,
        ["5m789p112s7z", "12346m23455p678s", None, None],
        exposed={0: [("777z", True, False), ("555m", True, False)]},
        dead_wall="1111z5z6z4m6z8s2z9s3z1p4z",
    ) as table:
        choose(table, "kakan", 33)
        passes(table)
        assert table.state()["kan_count"] == 1 and table.state()["kan_pending_dora"] == 1
        choose(table, "kakan", 4)
        choose(table, "ron")
        passes(table)
        winner = table.state()["result"]["winners"][0]
        assert "Robbing a kan" in winner["score"]["yaku"]
        assert winner["score"]["dora"] == 1
        assert table.state()["kan_count"] == 1 and table.state()["kan_pending_dora"] == 0
        center = next(area for area in table.state()["table"]["areas"] if area["id"] == "shared")
        assert len(center["groups"][0]["pieces"]) == 2
        assert sorted(physical_tiles(table)) == list(range(136))


def test_robbed_added_kan_does_not_commit_and_supplies_chankan_yaku(tmp_path):
    with scenario(
        tmp_path,
        ["3m789p234s1122z", "12456m23455p678s", None, None],
        exposed={0: [("333m", True, False)]},
    ) as table:
        choose(table, "kakan")
        choose(table, "ron")
        passes(table)
        assert table.state()["kan_count"] == 0
        assert not table.entities()["you.seat"]["game"]["melds"][0]["quad"]
        assert "Robbing a kan" in table.state()["result"]["winners"][0]["score"]["yaku"]
        assert sorted(physical_tiles(table)) == list(range(136))


def test_nine_terminals_abort_is_optional_and_preserves_counters(tmp_path):
    with scenario(
        tmp_path,
        ["19m19p19s1234567z5m", None, None, None],
        games={0: {"draw_count": 1}},
        overrides={"any_call": False, "honba": 2, "sticks": 3},
    ) as table:
        assert any(row["op"] == "discard" for row in table.state()["choices"])
        choose(table, "abort_nine")
        assert table.state()["result"]["reason"] == "Nine terminals"
        choose(table, "continue")
        assert table.state()["honba"] == 3 and table.state()["sticks"] == 3
        assert table.state()["dealer"] == 0


def test_four_first_winds_abort_after_the_response_window(tmp_path):
    with scenario(
        tmp_path,
        ["1z123456m234p5678s", None, None, None],
        discards=["", "1z", "1z", "1z"],
        overrides={"any_call": False},
    ) as table:
        choose(table, "discard", 27)
        passes(table)
        assert table.state()["result"]["reason"] == "Four winds"
        assert table.state()["result"]["dealer_stays"]


def test_four_riichi_abort_keeps_every_deposit(tmp_path):
    waiting = "19m19p19s1234567z"
    with scenario(
        tmp_path,
        ["123455m23455p678s", waiting, waiting, waiting],
        games={seat: {"riichi": True} for seat in (1, 2, 3)},
        scores=[25000, 24000, 24000, 24000],
        overrides={"sticks": 3},
    ) as table:
        choose(table, "riichi", 4)
        passes(table)
        assert table.state()["result"]["reason"] == "Four riichi"
        assert table.state()["sticks"] == 4
        assert sum(seat["score"] for seat in table.entities().values()) + 4000 == 100000


def test_fourth_kan_by_another_player_aborts_after_its_discard(tmp_path):
    with scenario(
        tmp_path,
        ["456p678s5z", "4444m234p567s1z", None, None],
        exposed={0: [("1111m", False, True), ("2222m", False, True)], 1: [("3333m", False, True)]},
        games={1: {"last_draw": 15}},
        overrides={"actor": "you.right", "kan_count": 3, "kan_owners": [0, 0, 1]},
    ) as table:
        choose(table, "ankan")
        passes(table)
        assert table.state()["kan_count"] == 4 and table.state()["abort_after_discard"]
        assert not any(row["op"] in {"ankan", "kakan"} for row in table.state()["choices"])
        choose(table, "discard")
        passes(table)
        assert table.state()["result"]["reason"] == "Four kans"
        assert sorted(physical_tiles(table)) == list(range(136))


@pytest.mark.parametrize("closed_required", [True, False, None])
def test_nagashi_profile_payment_and_draw_progression_are_explicit(tmp_path, closed_required):
    with scenario(
        tmp_path,
        ["12468m135p247s35z", "234p567s55m66p", None, None],
        exposed={1: [("222m", True, False)]},
        discards=["2p", "19m19p19s1234567z", "2s", "3p"],
        scores=[24000, 24000, 24000, 25000],
        overrides={"phase": "hand_end", "honba": 2, "sticks": 3},
        exhausted=True,
        nagashi_closed=closed_required,
    ) as table:
        assert sorted(physical_tiles(table)) == list(range(136))
        table.act("fixture_exhaustive", {})
        result = table.state()["result"]
        assert result["kind"] == "draw" and not result["dealer_stays"]
        if closed_required:
            assert result["nagashi"] == []
        else:
            assert result["nagashi"] == [1]
            assert list(result["deltas"]) == [-4000, 8000, -2000, -2000]
        assert table.state()["sticks"] == 3

        assert sum(seat["score"] for seat in table.entities().values()) + 3000 == 100000
        choose(table, "continue")
        assert table.state()["dealer"] == 1 and table.state()["honba"] == 3
        assert table.state()["sticks"] == 3


def test_chi_forbids_the_called_kind_and_the_opposite_outside_swap(tmp_path):
    with scenario(tmp_path, ["4m123456789p1122s", "45677m123p456s11z", None, None]) as table:
        choose(table, "discard", 3)
        choose(table, "chi")
        passes(table)
        assert table.state()["actor"] == "you.right"
        game = table.entities()["you.right"]["game"]
        assert set(game["forbidden_discards"]) == {3, 6}
        assert all(
            row["tiles"][0] // 4 not in {3, 6}
            for row in table.state()["choices"]
            if row["op"] == "discard"
        )
        assert sorted(physical_tiles(table)) == list(range(136))


def test_call_with_no_legal_post_call_discard_is_not_offered(tmp_path):
    with scenario(
        tmp_path,
        ["4m123456789p1122s", "4567m", None, None],
        exposed={1: [(text, True, False) for text in ("555p", "666p", "777s")]},
    ) as table:
        choose(table, "discard", 3)
        assert table.state()["actor"] == "you.right"
        options = [row["op"] for row in table.state()["choices"]]
        assert "ron" in options and "chi" not in options
