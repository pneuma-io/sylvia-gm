"""The TD map's wall editor reaches author_wall through ordinary declared panel actions."""

import json
from copy import deepcopy

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, RulesetPreconditionRejected
from sylvia_gm.spatial import SpatialEngine
from sylvia_gm.spatial._wall_authoring import WALL_SELECTION_MESSAGE
from tests.test_spatial_engine import _path, _ray
from tests.test_td_grid_edge_owner import NS, candidate  # noqa: F401
from tests.test_td_wall_authoring import segment, setup_owner, state

CAMPAIGN = "identity"
MAP = "operations.map"


def panel(owner):
    surface = next(
        item for item in owner.campaign_panels(CAMPAIGN)["surfaces"] if item["id"] == MAP
    )
    return surface, {action["id"]: action for action in surface["actions"]}


def walls(surface):
    return {item["id"]: item.get("edge_wall") for item in surface["data"]["geometry"]}


def act(owner, action, key, **selection):
    surface, _ = panel(owner)
    return owner.execute_panel_action(
        campaign_id=CAMPAIGN, surface_id=MAP, panel_action_id=action, operation_id=key,
        expected_revision=owner.snapshot(CAMPAIGN).revision,
        selection={"map_revision": surface["data"]["map_revision"], **selection},
    )


def border(x, y0, y1):
    """A vertical run of cell borders at grid column x."""
    return [[[x, y], [x, y + 1]] for y in range(y0, y1)]


def created(owner, before):
    known = {item["id"] for item in before}
    return [item for item in state(owner)["spatial_scene"]["geometry"] if item["id"] not in known]


def test_draw_extend_separate_connect_refuse_and_reopen(candidate, tmp_path):  # noqa: F811
    database = setup_owner(tmp_path, candidate)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        surface, actions = panel(owner)
        # The renderer sees the authored barrier's public face, never its private metadata.
        assert actions["wall-draw"]["edge_segment"] == {
            "height": 3, "display": {"label": "Full wall", "kind": "wall", "height_label": "Full"},
        }
        assert "edge_segment" not in actions["wall-connect"]
        names = ("wall-draw", "wall-extend", "wall-connect")
        assert all(actions[name]["available"] for name in names)
        before = state(owner)["spatial_scene"]
        receipt = act(owner, "wall-draw", "draw", edges=border(20, 20, 22))
        assert receipt.status.value == "committed"
        first = created(owner, before["geometry"])
        assert [item["volume"] for item in first] == [
            {"kind": "grid_edge", "min": [20, y, 0], "max": [20, y + 1, 3]} for y in (20, 21)
        ]
        assert all(
            item["movement"] == "blocking" and item["visibility"] == "opaque"
            and item["occupies_cells"] is False
            and item["metadata"]["player"]["label"] == "Full wall"
            for item in first
        )
        surface, _ = panel(owner)
        assert len({walls(surface)[item["id"]] for item in first}) == 1
        # The new edge blocks crossing between the adjoining cells and stops a straight shot.
        scene = state(owner)["spatial_scene"]
        query = {**_path([19, 20], [20, 20], allow_diagonal=False), "map_id": scene["map"]["id"]}
        assert SpatialEngine(scene).query(query).as_payload()["cost"] > 1
        shot = {**_ray([19.5, 20.5, 1], [20.5, 20.5, 1]), "map_id": scene["map"]["id"],
                "include": ["geometry"]}
        hits = SpatialEngine(scene).query(shot).as_payload()["intersections"]
        assert [hit["id"] for hit in hits] == [first[0]["id"]]

        anchor = first[-1]["id"]
        before = state(owner)["spatial_scene"]["geometry"]
        act(owner, "wall-extend", "extend", edge_anchor=[anchor], edges=[[[20, 22], [21, 22]]])
        extension = created(owner, before)
        assert extension[0]["edge_layer_id"] == first[0]["edge_layer_id"]
        # A touching border drawn as New wall stays a separate wall.
        before = state(owner)["spatial_scene"]["geometry"]
        act(owner, "wall-draw", "separate", edges=[[[21, 22], [22, 22]]])
        separate = created(owner, before)
        surface, _ = panel(owner)
        assert walls(surface)[separate[0]["id"]] != walls(surface)[anchor]

        chosen = [item["id"] for item in first + extension + separate]
        revision = owner.snapshot(CAMPAIGN).revision
        act(owner, "wall-connect", "connect", edge_walls=chosen)
        surface, _ = panel(owner)
        assert len({walls(surface)[identity] for identity in chosen}) == 1
        assert owner.snapshot(CAMPAIGN).revision == revision + 1

        # Extending from a border that does not touch the chosen wall is refused unchanged.
        unchanged = owner.snapshot(CAMPAIGN)
        with pytest.raises(RulesetPreconditionRejected) as refusal:
            act(owner, "wall-extend", "detached", edge_anchor=[anchor], edges=border(30, 5, 6))
        assert WALL_SELECTION_MESSAGE in str(refusal.value)
        assert owner.snapshot(CAMPAIGN) == unchanged
        # A stale map revision never reaches the mechanic.
        with pytest.raises(CommandRejected):
            owner.execute_panel_action(
                campaign_id=CAMPAIGN, surface_id=MAP, panel_action_id="wall-draw",
                operation_id="stale", expected_revision=unchanged.revision,
                selection={"map_revision": surface["data"]["map_revision"] - 1,
                           "edges": border(25, 1, 2)},
            )
        final = owner.snapshot(CAMPAIGN)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        assert owner.snapshot(CAMPAIGN) == final
        surface, _ = panel(owner)
        assert len({walls(surface)[identity] for identity in chosen}) == 1


@pytest.mark.parametrize(
    "selection",
    [
        {"edges": [[[20, 20], [21, 21]]]},
        {"edges": [[[20, 20], [20, 21]], [[20, 21], [20, 20]]]},
        {"edges": [[[20, 20], [20, 99]]]},
        {"edges": []},
        {"edges": border(20, 20, 21), "geometry_ids": ["typed-id"]},
    ],
)
def test_malformed_border_selection_is_refused_before_the_mechanic(
    candidate, tmp_path, selection,  # noqa: F811
):
    database = setup_owner(tmp_path, candidate)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        before = owner.snapshot(CAMPAIGN)
        with pytest.raises(CommandRejected):
            act(owner, "wall-draw", "bad", **selection)
        assert owner.snapshot(CAMPAIGN) == before


def test_hidden_segments_stay_unselectable_and_unlinked(candidate, tmp_path):  # noqa: F811
    pieces = [segment(20 + index, 21 + index) for index in range(3)]
    pieces[1] = segment(21, 22, visible=False)
    for index, item in enumerate(pieces):
        item.update(id=f"piece-{index}", edge_layer_id="wall-shared")
    database = setup_owner(tmp_path, candidate, pieces)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        surface, _ = panel(owner)
        # The two visible ends are not presented as one wall through the hidden middle.
        assert walls(surface) == {"piece-0": "piece-0", "piece-2": "piece-2"}
        published = json.dumps(thaw_json_value(owner.campaign_panels(CAMPAIGN)))
        for secret in ("piece-1", "wall-shared", "edge_layer_id", "private-material"):
            assert secret not in published
        before = owner.snapshot(CAMPAIGN)
        for key, selection in (
            ("hidden-anchor", {"edge_anchor": ["piece-1"], "edges": border(2, 30, 31)}),
            ("hidden-connect", {"edge_walls": ["piece-0", "piece-1", "piece-2"]}),
        ):
            action = "wall-extend" if "edge_anchor" in selection else "wall-connect"
            with pytest.raises(CommandRejected):
                act(owner, action, key, **selection)
        # Joining only the visible pieces cannot claim the hidden one; the owner refuses.
        with pytest.raises(RulesetPreconditionRejected) as refusal:
            act(owner, "wall-connect", "visible-connect", edge_walls=["piece-0", "piece-2"])
        assert "piece-1" not in str(refusal.value)
        assert owner.snapshot(CAMPAIGN) == before
        saved = deepcopy(state(owner)["spatial_scene"]["geometry"])
        assert [item["edge_layer_id"] for item in saved] == ["wall-shared"] * 3
