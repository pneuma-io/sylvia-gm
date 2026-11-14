"""The existing scenery constructor admits actual objects and static physical faces."""

from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_scene_relations import _array, _tool_scene
from scripts.srd_full.srd_spell_object_content import _new_scene_object
from scripts.srd_full.srd_timed_actor_defenses import _parameter, _set

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import MechanicContext, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.spatial import SpatialEngine
from tests.srd_full.test_srd_action_budget import ROOT, SRD, _run, _runtime_definition
from tests.srd_full.test_srd_spell_travel import _case


@lru_cache
def _construction(kind):
    """Compile the actual shared emitter through the real package mechanic bridge."""
    original = _runtime_definition((ROOT / "runtime/ruleset.yaml").read_bytes())
    options = {} if kind == "default" else {"scene_kind": kind}
    tool = _tool_scene(
        "Construct physical scenery from explicit admitted creation intent.",
        [_parameter("identity"), _array("origin"), _array("extent")],
        [
            _set("_world_origin", "origin"),
            *_new_scene_object(
                {"plane": "'material'", "material": "'stone'", "ground": True},
                identity="identity",
                extent="extent",
                movement="'blocking'",
                visibility="'opaque'",
                **options,
            ),
        ],
    )
    runtime = {
        key: deepcopy(original[key]) for key in ("sylvia_gm_runtime", "features", "abilities")
    }
    runtime.update(definitions={}, tools={"construct": tool})
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="a" * 64,
        runtime_bytes=yaml.safe_dump(runtime, sort_keys=False).encode(),
        declaration=ToolDeclaration(
            name="construct",
            capability="dnd.srd.5.2.1.action-economy",
            input_schema=owned_payload(_tool_schema(tool, tool_name="construct")),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


def _construct(state, kind="geometry", **changes):
    result = _construction(kind)(
        MechanicContext(
            campaign_id="construction",
            turn_id="turn",
            action_id="construct",
            state=owned_payload(state),
            parameters=owned_payload(
                {"identity": "face", "origin": [1, 2, 3], "extent": [2, 4, 6], **changes}
            ),
            participants=owned_payload({}),
        )
    )
    for mutation in result.mutations:
        state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)
    return result


@pytest.mark.parametrize("kind", ["default", "entity"])
def test_existing_entity_construction_preserves_actual_body_metadata_and_scene_revision(kind):
    state, _, _ = _case("misty_step")
    before = deepcopy(state[f"{SRD}.state"]["spatial_scene"])
    _construct(state, kind)
    after = state[f"{SRD}.state"]["spatial_scene"]
    actual = next(entity for entity in after["entities"] if entity["id"] == "face")
    assert actual["position"] == [1, 2, 3]
    assert actual["footprint"] == {"kind": "box", "half_extents": [1, 2]}
    assert actual["height"] == 6 and actual["movement"] == "blocking"
    assert actual["visibility"] == "opaque" and actual["active"] is True
    assert actual["metadata"] == {
        "plane": "material",
        "material": "stone",
        "ground": True,
        "is_object": True,
    }
    assert after["geometry"] == before["geometry"] and after["zones"] == before["zones"]
    assert len(after["entities"]) == len(before["entities"]) + 1
    assert after["map"]["revision"] == before["map"]["revision"] + 1


def test_created_geometry_is_actual_static_solid_opaque_and_qualifying_support():
    state, _, _ = _case("misty_step")
    before = deepcopy(state[f"{SRD}.state"]["spatial_scene"])
    _construct(state)
    after = state[f"{SRD}.state"]["spatial_scene"]
    assert after["entities"] == before["entities"] and after["zones"] == before["zones"]
    assert after["geometry"] == [
        {
            "id": "face",
            "map_id": "information",
            "volume": {
                "kind": "box",
                "min": [0, 0, 3],
                "max": [2, 4, 9],
            },
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            "metadata": {
                "plane": "material",
                "material": "stone",
                "ground": True,
                "is_object": True,
            },
        }
    ]
    assert after["map"]["revision"] == before["map"]["revision"] + 1
    hit = (
        SpatialEngine(after)
        .query(
            {
                "kind": "ray",
                "map_id": "information",
                "origin": [1, 2, 12],
                "destination": [1, 2, 0],
                "include": ["geometry"],
                "max_intersections": 8,
            }
        )
        .as_payload()["intersections"][0]
    )
    assert hit["id"] == "face" and hit["point"] == [1, 2, 9] and hit["normal"] == [0, 0, 1]
    _run("qualify_srd_environment_ground", state, point=[1, 2, 9], plane="material")


@pytest.mark.parametrize("identity", ["hero", "wall", "ambient_material"])
def test_geometry_construction_rejects_existing_scene_identity_without_mutation(identity):
    state, _, _ = _case("misty_step")
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(1)]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="existing scene identity"):
        _construct(state, identity=identity)
    assert state == before


@pytest.mark.parametrize(
    "origin,extent",
    [
        ([30000, 0, 0], [2, 4, 6]),
        ([0, 0, 29999], [2, 4, 6]),
        ([0, 0, 0], [0, 4, 6]),
        ([0, 0, 0], [2, -4, 6]),
    ],
)
def test_geometry_construction_rejects_unadmitted_bounds_or_extent_without_mutation(origin, extent):
    state, _, _ = _case("misty_step")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _construct(state, origin=origin, extent=extent)
    assert state == before
