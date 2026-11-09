"""Tile presentation follows real discard/call provenance without changing the game rules."""

import hashlib
import json

import pytest
from scripts.build_riichi_ruleset import source

from sylvia_gm.application.piece_table import project_piece_table
from tests.test_riichi_progression import choose, passes, scenario


def area(table, seat=0):
    return table.state()["table"]["areas"][seat]


def test_gameplay_and_scoring_families_are_unchanged_from_previous_version():
    new = source()
    display = {"refresh", "seat_table", "center_table", "choice_table", "discard_history"}
    gameplay = {key: value for key, value in new["mechanic_families"].items() if key not in display}

    def digest(value):
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()

    # Canonical digests from the accepted 91159825 package, excluding its presentation families.
    assert digest(gameplay) == "c160ab9ece6c6ae7e11c1f73468b2383dbf3613e34517d745e7e379076a7d260"
    assert (
        digest(new["public_actions"])
        == "8bd6ce8e6e6f13ae76fce648af81d64e88b3c144cf22083897adb87f34c11cd1"
    )


def test_chi_uses_actual_called_tile_source_and_removes_it_from_pond(tmp_path):
    with scenario(tmp_path, ["123m456p789s12344z", "23m456p789s12344z", None, None]) as table:
        choose(table, "discard", 0)
        choose(table, "chi")
        passes(table)
        seats = table.state()["table"]["areas"]
        meld = next(group for group in seats[1]["groups"] if group.get("role") == "meld")
        pond = next(group for group in seats[0]["groups"] if group.get("role") == "pond")
        assert meld["source"] == "left"
        assert meld["pieces"][0]["flags"] == ("sideways",)
        assert meld["pieces"][0]["face"]["kind"] == 0
        assert "claimed" in pond["pieces"][0]["flags"]
        assert sorted(piece["face"]["kind"] for piece in meld["pieces"]) == [0, 1, 2]


def test_riichi_marker_and_drawn_tile_survive_owner_projection(tmp_path):
    with scenario(tmp_path, ["123m123p123s11122z", None, None, None]) as table:
        hand = area(table)["groups"][0]["pieces"]
        assert hand[-1]["flags"] == ("drawn",)
        assert sum("drawn" in piece["flags"] for piece in hand) == 1
        choose(table, "riichi")
        pond = area(table)["groups"][1]["pieces"]
        assert "sideways" in pond[0]["flags"]
        projection = project_piece_table(
            table.state()["table"], viewer_ids={"you"}, action_ids={"play"}
        )
        for seat in projection["areas"][1:4]:
            assert seat["groups"][0]["concealed"] is True
            assert "pieces" not in seat["groups"][0]


@pytest.mark.parametrize("hand", ["1111m234p567s1234z", "0555m234p567s1234z"])
def test_concealed_kan_has_outer_backs_and_public_middle_faces(tmp_path, hand):
    with scenario(tmp_path, [hand, None, None, None]) as table:
        choose(table, "ankan")
        passes(table)
        meld = next(group for group in area(table)["groups"] if group.get("role") == "meld")
        assert meld["source"] == "self"
        assert ["back" in piece["flags"] for piece in meld["pieces"]] == [True, False, False, True]
        assert len(meld["pieces"]) == 4
        if hand.startswith("0555"):
            assert meld["pieces"][1]["face"]["red"] is True


@pytest.mark.parametrize("operation", ["minkan", "kakan"])
def test_open_and_added_kan_use_the_actual_claimed_tile_and_stack(tmp_path, operation):
    with scenario(tmp_path, ["3m123456789p1122s", "333m456p789s1122z", None, None]) as table:
        choose(table, "discard", 2)
        choose(table, "minkan" if operation == "minkan" else "pon")
        passes(table)
        if operation == "kakan":
            # Keep the fourth copy and return through ordinary turns before upgrading this pon.
            choose(table, "discard")
            passes(table)
            for _ in range(3):
                choose(table, "discard")
                passes(table)
            assert table.state()["actor"] == "you.right"
            choose(table, "kakan")
            passes(table)
        meld = next(group for group in area(table, 1)["groups"] if group.get("role") == "meld")
        pond = area(table, 0)["groups"][1]["pieces"]
        called = next(piece for piece in pond if "claimed" in piece["flags"])
        sideways = next(piece for piece in meld["pieces"] if "sideways" in piece["flags"])
        assert called["face"] == sideways["face"]
        assert called["id"].split(":")[-1] == sideways["id"].split(":")[-1]
        assert meld["source"] == "left"
        assert len(meld["pieces"]) == 4
        assert sum("stacked" in piece["flags"] for piece in meld["pieces"]) == (
            operation == "kakan"
        )
