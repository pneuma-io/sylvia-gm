"""Real owner previews reuse physical queries while hidden scene changes stay private."""

from copy import deepcopy
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from tests import test_player_actions as action_fixture
from tests.test_player_action_availability import _checks, _route_controls
from tests.test_player_actions import _create, _move, _request
from tests.test_tactical_grid_ui import _scene


def _move_controls():
    controls = _route_controls()
    controls["checks"] = controls["checks"][:1]
    controls["map_preview"] = {"path": "_path"}
    controls["preview"] = {
        "version": 1,
        "title": "Move estimate",
        "summary": [
            {"label": "AP", "value": {"path": "context._route_ap"}, "optional": True},
        ],
    }
    return controls


def test_hidden_obstacles_do_not_change_preview_but_still_change_committed_route(
    tmp_path: Path,
    monkeypatch,
):
    previews, committed_paths = [], []
    for hidden in (True, False):
        scene = _scene()
        if not hidden:
            scene["geometry"] = scene["geometry"][:1]
            scene["entities"] = scene["entities"][:1]
        monkeypatch.setattr(action_fixture, "_scene", lambda scene=scene: deepcopy(scene))
        with AuthorityOwner.open(tmp_path / f"known-{hidden}.sqlite3", "owner") as owner:
            _create(owner, controls=_move_controls(), path_binding=True)
            before = owner.snapshot("campaign")
            result = _checks(owner, destination=[7, 2])
            assert result["available"] is True
            preview = result["map_preview"]
            assert preview["path"]["points"][0] == [1.5, 2.5, 0]
            assert preview["path"]["points"][-1] == [7.5, 2.5, 0]
            assert "secret-wall" not in str(result) and "ambusher" not in str(result)
            assert "snapshot_id" not in str(result) and "metadata" not in str(result)
            assert owner.snapshot("campaign") == before
            previews.append(result)
            _move(
                owner,
                selection={
                    "actor": "operator",
                    "destination": [7, 2],
                    "map_revision": 7,
                },
            )
            committed_paths.append(owner.snapshot("campaign").actions[0].parameters["path"])
    assert previews[0] == previews[1]
    assert committed_paths[0] != committed_paths[1]
    assert committed_paths[1] == previews[1]["map_preview"]["path"]["cells"]


def test_unaffordable_or_blocked_moves_still_have_honest_visible_estimates(tmp_path: Path):
    controls = _move_controls()
    controls["checks"] = _route_controls()["checks"]
    with AuthorityOwner.open(tmp_path / "unavailable.sqlite3", "owner") as owner:
        _create(owner, controls=controls)
        costly = _checks(owner, destination=[4, 4])
        assert costly["available"] is False
        assert costly["map_preview"]["path"]["found"] is True
        assert costly["preview"]["summary"][0]["value"] > 1
        blocked = _checks(owner, destination=[3, 2])
        assert blocked["available"] is False
        assert blocked["map_preview"]["path"]["found"] is False
        assert blocked["map_preview"]["path"]["points"] == []
        assert blocked["map_preview"]["path"]["cost"] is None


def test_firing_preview_uses_authored_origin_height_and_real_partial_exposure(
    tmp_path: Path,
    monkeypatch,
):
    scene = _scene()
    scene["geometry"] = scene["geometry"][:1]
    scene["geometry"][0]["volume"] = {
        "kind": "box",
        "min": [5, 1, 0],
        "max": [5.2, 4, 1],
    }
    target = deepcopy(scene["entities"][0])
    target.update(
        id="target",
        position=[7.5, 2.5, 0],
        metadata={
            "player": {"label": "Target", "kind": "unit"},
        },
    )
    scene["entities"].append(target)
    monkeypatch.setattr(action_fixture, "_scene", lambda: deepcopy(scene))
    controls = {
        "queries": [
            {
                "store_as": "_ray",
                "spatial_query": {
                    "scene": "state.spatial_scene",
                    "operation": "ray",
                    "query": {
                        "map_id": "training-floor",
                        "origin": [1.91, 2.5, 1.4],
                        "destination": [7.5, 2.5, 0.9],
                        "include": ["geometry", "entities"],
                        "max_intersections": 64,
                    },
                },
            },
            {
                "store_as": "_exposure",
                "spatial_query": {
                    "scene": "state.spatial_scene",
                    "operation": "exposure",
                    "query": {
                        "map_id": "training-floor",
                        "observer": {"point": [1.91, 2.5, 1.4]},
                        "target": {"entity_id": "target"},
                        "samples": {"horizontal": 3, "vertical": 3},
                        "include": ["geometry", "entities"],
                    },
                },
            },
        ],
        "map_preview": {"ray": "_ray", "exposure": "_exposure"},
    }
    with AuthorityOwner.open(tmp_path / "firing.sqlite3", "owner") as owner:
        _create(owner, controls=controls)
        before = owner.snapshot("campaign")
        with monkeypatch.context() as patch:

            def forbidden(*args, **kwargs):
                pytest.fail("Preview attempted an effect or dice operation")

            patch.setattr("sylvia_gm.runtime._tool_executor.execute_effects", forbidden)
            patch.setattr("sylvia_gm.dice.set_seed", forbidden)
            result = _checks(owner)
            proposal = owner.preview_panel_action(**_request())
        preview = result["map_preview"]
        assert preview == proposal["map_preview"]
        assert 0 < preview["exposure"]["fraction"] < 1
        assert preview["exposure"]["cover_ids"] == ["door-a"]
        assert preview["ray"]["origin"] == [1.91, 2.5, 1.4]
        assert [item["id"] for item in preview["ray"]["contacts"]] == ["target"]
        assert "ambusher" not in str(preview) and "concealed-hostile" not in str(preview)
        assert owner.snapshot("campaign") == before
