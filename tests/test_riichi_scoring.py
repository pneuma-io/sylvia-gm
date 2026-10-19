"""Scoring vectors execute the package's real private mechanics through an admitted fixture tool."""

import io
from functools import lru_cache

import pytest
import yaml
from scripts.build_riichi_ruleset import NS, call, family, hand_defaults, source

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain.authority import StateValue, WriterRole, thaw_json_value


def tiles(text):
    result, digits, used = [], [], set()
    for char in text:
        if char.isdigit():
            digits.append(int(char))
            continue
        suit = "mpsz".index(char)
        for digit in digits:
            kind = suit * 9 + ((5 if digit == 0 else digit) - 1)
            candidates = [kind * 4 + n for n in range(4)]
            if digit == 0:
                candidates = [kind * 4]
            elif kind in (4, 13, 22):
                candidates = candidates[1:]
            value = next(item for item in candidates if item not in used)
            used.add(value)
            result.append(value)
        digits = []
    return result


@lru_cache(maxsize=1)
def archive():
    declaration = source()
    parameters = declaration["mechanic_families"]["score_hand"]["parameters"]
    declaration["mechanic_families"]["score_probe"] = family(
        parameters,
        [
            call(
                "_score_hand",
                {
                    "actor": "{_actor_id}",
                    "winning_tile": "{winning_tile}",
                    "is_tsumo": "{is_tsumo}",
                    "win_source": "{win_source}",
                },
                ["_score"],
            ),
            {"publish": {"fields": {"score": "_score"}}},
        ],
    )
    declaration["public_actions"].append(
        {
            **declaration["public_actions"][1],
            "name": "score_probe",
            "family": "score_probe",
            "description": "Synthetic scoring-fixture wrapper; not a product tool.",
        }
    )
    compiled = compile_ruleset_source(yaml.safe_dump(declaration).encode())
    with materialize_package_archive((compiled.draft,)) as artifact:
        return artifact.archive.read()


def score(
    tmp_path, hand, winning, *, tsumo=False, riichi=False, seat=1, meld=None, indicator=31, sets=()
):
    physical = tiles(hand)
    winning_kind = tiles(winning)[0] // 4
    winning_tile = next(tile for tile in reversed(physical) if tile // 4 == winning_kind)
    if not tsumo:
        physical.remove(winning_tile)
    game = {**hand_defaults(), "hand": physical, "riichi": riichi, "draw_count": 2}
    if meld:
        exposed = tiles(meld)
        game["melds"] = [
            {
                "form": "sequence",
                "base": min(exposed) // 4,
                "opened": True,
                "quad": False,
                "tiles": exposed,
                "source_seat": 0,
            }
        ]
    for notation, opened, quad in sets:
        values = tiles(notation)
        game["melds"].append(
            {
                "form": "triplet",
                "base": values[0] // 4,
                "opened": opened,
                "quad": quad,
                "tiles": values,
                "source_seat": 0,
            }
        )
    with AuthorityOwner.open(tmp_path / "score.sqlite3", "score-test") as owner:
        candidate = owner.install_package_archive(io.BytesIO(archive()), install_id="score")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="score",
                display_name="Scoring fixture",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn",
                initial_state=(
                    StateValue(
                        NS,
                        "entities",
                        {"seat": {"name": "Seat", "seat": seat, "score": 25000, "game": game}},
                    ),
                    StateValue(
                        NS,
                        "state",
                        {
                            "actor": "seat",
                            "dealer": 0,
                            "round_wind": 0,
                            "live": [1],
                            "dead": [124] * 4 + [indicator * 4] + [124] * 9,
                            "kan_count": 0,
                            "kan_pending_dora": 0,
                            "any_call": True,
                        },
                    ),
                ),
            )
        )
        receipt = owner.execute_action(
            ActionCommand(
                campaign_id="score",
                action_id="score",
                idempotency_key="score",
                turn_id="turn",
                owner_id="score-test",
                writer=WriterRole.DM,
                expected_revision=0,
                package_id=NS,
                tool_name="score_probe",
                parameters={
                    "actor": "seat",
                    "winning_tile": winning_tile,
                    "is_tsumo": tsumo,
                    "win_source": "live_draw" if tsumo else "discard",
                },
            )
        )
        assert receipt.status.value == "committed", receipt.result
        return thaw_json_value(receipt.result)["context"]["score"]


@pytest.mark.parametrize("tsumo,han,fu,payment", [(False, 2, 30, 2000), (True, 2, 20, 1500)])
def test_closed_pinfu_scoring(tmp_path, tsumo, han, fu, payment):
    result = score(tmp_path, "123456m23455p678s", "6m", tsumo=tsumo, riichi=not tsumo)
    assert result["valid"] and (result["han"], result["fu"], result["total"]) == (han, fu, payment)


def test_bonus_only_open_hand_cannot_win_and_open_tanyao_can(tmp_path):
    invalid = score(tmp_path / "no-yaku", "406m23455p678s", "6m", meld="123m")
    assert not invalid["valid"] and invalid["red"] == 1
    valid = score(tmp_path / "tanyao", "456m34555p678s", "6m", meld="234m")
    assert valid["valid"] and "All simples" in valid["yaku"]


def test_double_wind_pair_is_four_fu(tmp_path):
    result = score(tmp_path, "123456m789p234s11z", "4s", riichi=True, seat=0)
    assert result["fu_components"]["pair"] == 4
    assert result["fu"] == 40


def test_seven_pairs_is_fixed_twenty_five_fu(tmp_path):
    result = score(tmp_path, "1122m3344p5566s77z", "7z")
    assert result["valid"] and (result["han"], result["fu"], result["total"]) == (2, 25, 1600)


@pytest.mark.parametrize(
    "hand,winning,tsumo,units",
    [
        ("119m19p19s1234567z", "1m", False, 2),
        ("119m19p19s1234567z", "1z", False, 1),
        ("111m55m222p333s444z", "5m", False, 2),
        ("111m55m222p333s444z", "1m", True, 1),
        ("11123455678999m", "5m", False, 2),
        ("11123455678999m", "1m", False, 1),
    ],
)
def test_special_yakuman_waits(tmp_path, hand, winning, tsumo, units):
    result = score(tmp_path, hand, winning, tsumo=tsumo)
    assert result["valid"] and result["yakuman_units"] == units


def test_four_han_thirty_fu_does_not_round_up_to_mangan(tmp_path):
    result = score(tmp_path, "123456m23455p678s", "6m", riichi=True, indicator=12)
    assert (result["han"], result["fu"], result["total"]) == (4, 30, 7700)


@pytest.mark.parametrize(
    "prefix,opened,quad,set_fu,fu",
    [
        ("111m", False, False, 16, 50),
        ("111m", True, False, 12, 40),
        ("1111m", False, True, 40, 80),
        ("1111m", True, True, 24, 50),
        ("555m", False, False, 12, 50),
        ("555m", True, False, 10, 40),
        ("0555m", False, True, 24, 60),
        ("0555m", True, True, 16, 40),
    ],
)
def test_set_fu_distinguishes_concealment_terminals_and_kans(
    tmp_path,
    prefix,
    opened,
    quad,
    set_fu,
    fu,
):
    hand = "234p567s55566z"
    sets = [(prefix, opened, quad)] if opened or quad else []
    if not sets:
        hand = prefix + hand
    result = score(tmp_path, hand, "4p", sets=sets)
    assert result["valid"] and result["fu_components"]["sets"] == set_fu
    assert result["fu"] == fu


@pytest.mark.parametrize(
    "hand,winning",
    [
        ("123m234p456s77766z", "5s"),
        ("123m234p789s77766z", "7s"),
        ("123m234p123s77766z", "3s"),
        ("123m234p456s77766z", "6z"),
    ],
)
def test_closed_inside_edge_and_single_waits_add_two_fu(tmp_path, hand, winning):
    result = score(tmp_path, hand, winning)
    assert result["valid"] and result["fu_components"]["wait"] == 2


def test_counted_yakuman_and_highest_scoring_ambiguous_reading(tmp_path):
    result = score(tmp_path, "11223344556677m", "7m", tsumo=True, riichi=True, indicator=0)
    # Pair 1m with doubled 234m/567m sequences also gives pinfu: 14 han beats chiitoitsu.
    assert result["valid"] and result["basic"] == 8000 and result["han"] == 14
    assert result["fu"] == 20
    assert result["yakuman_units"] == 0 and "Ryanpeikou" in result["yaku"]


def test_big_four_winds_is_double_and_stacks_with_all_honors(tmp_path):
    result = score(
        tmp_path,
        "55z",
        "5z",
        sets=[(wind, True, False) for wind in ("111z", "222z", "333z", "444z")],
    )
    assert result["valid"] and result["yakuman_units"] == 3 and result["big_winds"]


def test_actual_yakuman_wins_a_payment_tie_with_an_alternate_counted_reading(tmp_path):
    result = score(tmp_path, "11122233344405m", "3m", tsumo=True, riichi=True, indicator=0)
    assert result["valid"] and result["basic"] == 8000
    assert result["yakuman_units"] == 1 and result["han"] == 0
    assert "Four concealed sets" in result["yaku"]
