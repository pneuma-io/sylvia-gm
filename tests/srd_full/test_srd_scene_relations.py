"""The shared scene relation derives range and sight from admitted scene entries."""

from copy import deepcopy

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_spatial_engine import _geometry
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case


def _state():
    state, _, _ = _spell_case("darkvision")
    entities = []
    for subject, position in (("hero", [0, 0, 0]), ("guard", [3, 4, 12])):
        entities.append(
            {
                "id": subject,
                "map_id": "relation",
                "position": position,
                "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
                "height": 6,
                "facing": [1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"participant_ref": subject, "plane": "material", "lighting": "bright"},
            }
        )
    state[f"{SRD}.state"] = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": {
            "format": 1,
            "map": {
                "id": "relation",
                "revision": 0,
                "bounds": {"min": [-100, -100, -100], "max": [100, 100, 100]},
                "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
                "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}],
            },
            "entities": entities,
            "geometry": [],
            "zones": [],
            "terrain": [],
        },
    }
    return state


def _relation(state):
    result = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    return {row["label"]: row["value"] for row in result.result["result_card"]["summary"]}


def test_scene_relation_calculates_three_dimensional_distance_and_direction():
    result = _relation(_state())
    assert result["Distance"] == 13
    assert result["Direction"] == pytest.approx([3 / 13, 4 / 13, 12 / 13])
    assert result["Can see"] is True
    assert result["Actor scene ID"] == "hero"


@pytest.mark.parametrize("lighting", ["dim", "darkness", "magical_darkness"])
def test_actual_light_is_consumed_by_source_qualified_sight(lighting):
    state = _state()
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["lighting"] = lighting
    assert _relation(state)["Can see"] is (lighting == "dim")
    state[f"{SRD}.entities"]["hero"]["truesight_feet"] = 120
    assert _relation(state)["Can see"] is True


def test_invisibility_blindness_and_native_blindsight_use_the_saved_actor():
    state = _state()
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["invisible"] = True
    assert _relation(state)["Can see"] is False
    state[f"{SRD}.entities"]["hero"]["truesight_feet"] = 120
    assert _relation(state)["Can see"] is True
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    assert _relation(state)["Can see"] is False
    state[f"{SRD}.entities"]["hero"]["blindsight_feet"] = 30
    assert _relation(state)["Can see"] is True


def test_ethereal_visibility_does_not_forge_same_plane():
    state = _state()
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["plane"] = "ethereal"
    assert _relation(state)["Can see"] is False
    state[f"{SRD}.entities"]["hero"]["truesight_feet"] = 120
    result = _relation(state)
    assert result["Can see"] is True
    assert result["Same plane"] is False


def test_scene_relation_rejects_missing_authoritative_light_without_mutation():
    state = _state()
    del state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["lighting"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="ambient lighting"):
        _relation(state)
    assert state == before


@pytest.mark.parametrize("obstruction", ["clear", "wall", "other_plane"])
def test_geometry_relation_uses_the_scene_without_light_or_d20(monkeypatch, obstruction):
    state = _state()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    del scene["entities"][1]["metadata"]["lighting"]
    if obstruction == "wall":
        wall = _geometry("wall", [1, -100, -100], [2, 100, 100])
        wall["map_id"] = "relation"
        scene["geometry"] = [wall]
    elif obstruction == "other_plane":
        scene["entities"][1]["metadata"]["plane"] = "ethereal"
    monkeypatch.setattr(dice, "roll", lambda *args, **kwargs: pytest.fail("Geometry rolled dice"))
    before = deepcopy(state)
    result = _run(
        "resolve_srd_scene_geometry_relation", state, actor_state="hero", target_state="guard"
    ).result["context"]
    assert result["_scene_distance"] == 13
    assert result["_scene_direction"] == pytest.approx([3 / 13, 4 / 13, 12 / 13])
    assert result["_scene_same_plane"] is (obstruction != "other_plane")
    assert result["_scene_line_of_sight"] is (obstruction == "clear")
    assert "_scene_can_see" not in result
    assert state == before
