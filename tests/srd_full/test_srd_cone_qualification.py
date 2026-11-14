"""Cone membership uses actual admitted boxes rather than caller target receipts."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.srd_cone_qualification import extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application import package_runtime
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_spell_wave3_information import SRD, _entity, _scene

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cone_runtime():
    text = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    text = extend_runtime(text)
    assert extend_runtime(text) == text
    return text.encode()


@pytest.fixture(autouse=True)
def compiled_cones(monkeypatch, cone_runtime):
    runtime = _load_runtime(cone_runtime)

    @lru_cache
    def mechanic(name):
        schema = budget._tool_schema(runtime["tools"][name], tool_name=name)
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="c" * 64,
            runtime_bytes=cone_runtime,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.action-economy",
                input_schema=owned_payload(schema),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    original = package_runtime.execute_tool

    def execute(*args, **kwargs):
        result = original(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
            print(
                {
                    k: v
                    for k, v in result.context.items()
                    if k
                    in {
                        "_cone_direction_norm",
                        "_cone_axis",
                        "_cone_surface_distance",
                        "_cone_box",
                        "_cone_t_max",
                        "_cone_axial_min",
                    }
                }
            )
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", execute)


def _case(point, facing=(1, 0), *, half=(0.4, 0.4), height=6):
    source = _entity("dragon", [0, 0, 0], participant_ref="hero", creature_type="dragon")
    source.update(footprint={"kind": "box", "half_extents": [5, 5]}, height=10, facing=list(facing))
    target = _entity("guard", list(point), participant_ref="guard", creature_type="humanoid")
    target.update(footprint={"kind": "box", "half_extents": list(half)}, height=height)
    state = budget._state()
    state[f"{SRD}.entities"]["hero"]["creature_type"] = "dragon"
    state[f"{SRD}.entities"]["guard"] = dict(
        state[f"{SRD}.entities"]["hero"],
        creature_type="humanoid",
    )
    state[f"{SRD}.state"] = {"spatial_scene": _scene(source, target)}
    return state


@pytest.mark.parametrize(
    "point,expected",
    [
        ([15, 0, 0], True),
        ([15, 6, 0], False),
        ([15, 5.6, 0], True),
        ([36, 0, 0], False),
        ([35.3, 0, 0], True),
        ([4, 0, 0], False),
        ([15, 0, 15], False),
        ([15, 0, 9.5], True),
    ],
)
def test_full_box_edges_height_caps_and_source_size(point, expected):
    state = _case(point)
    result = budget._run(
        "qualify_srd_cone_admitted_box", state, source_index=0, target_index=1, length_feet=30
    ).result
    assert result["context"]["_srd_cone_origin"] == [5, 0, 5]
    assert result["context"]["_srd_cone_intersects"] is expected


@pytest.mark.parametrize(
    "facing,point",
    [
        ((-1, 0), [-15, 0, 0]),
        ((0, 1), [0, 15, 0]),
        ((0, -1), [0, -15, 0]),
        ((1, 1), [15, 15, 0]),
        ((-1, 1), [-15, 15, 0]),
        ((2, 1), [25, 12.5, 0]),
    ],
)
def test_actual_horizontal_facing_has_exact_volume_membership(facing, point):
    result = budget._run(
        "qualify_srd_cone_admitted_box",
        _case(point, facing),
        source_index=0,
        target_index=1,
        length_feet=30,
    ).result
    assert result["context"]["_srd_cone_intersects"] is True


def test_oblique_far_cap_crosses_box_without_an_inside_corner():
    state = _case([15, 15, 0], (1, 1), half=(10, 10))
    result = budget._run(
        "qualify_srd_cone_admitted_box",
        state,
        source_index=0,
        target_index=1,
        length_feet=10,
        width_feet=1,
    ).result
    assert result["context"]["_srd_cone_intersects"] is True


def test_selected_apex_must_stay_in_actual_source_volume():
    state = _case([15, 0, 0])
    result = budget._run(
        "qualify_srd_cone_admitted_box",
        state,
        source_index=0,
        target_index=1,
        length_feet=30,
        apex=[0, 0, 0],
    ).result
    assert result["context"]["_srd_cone_origin"] == [0, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="source volume"):
        budget._run(
            "qualify_srd_cone_admitted_box",
            state,
            source_index=0,
            target_index=1,
            length_feet=30,
            apex=[6, 0, 0],
        )
    assert state == before


def test_complete_scene_selection_excludes_force_objects_and_other_planes():
    state = _case([15, 0, 0])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] += [
        _entity("force", [15, 0, 0], participant_ref="force", creature_type="spell_force"),
        _entity("object", [15, 0, 0], participant_ref="object", is_object=True),
        _entity("far", [15, 20, 0], participant_ref="far", creature_type="humanoid"),
        _entity("ethereal", [15, 0, 0], participant_ref="ethereal", plane="ethereal"),
    ]
    for subject in ("far", "ethereal"):
        state[f"{SRD}.entities"][subject] = deepcopy(state[f"{SRD}.entities"]["guard"])
    result = budget._run(
        "collect_srd_cone_creature_targets", state, source_state="hero", length_feet=30
    ).result
    assert result["context"]["_srd_cone_targets"] == ["guard"]


def test_64_actual_box_creatures_use_unchanged_shared_budget():
    state = _case([15, 0, 0])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] = scene["entities"][:1] + [
        _entity(f"target{i}", [15, 0, 0], participant_ref=f"actor{i}", creature_type="humanoid")
        for i in range(64)
    ]
    for i in range(64):
        state[f"{SRD}.entities"][f"actor{i}"] = deepcopy(state[f"{SRD}.entities"]["guard"])
    result = budget._run(
        "collect_srd_cone_creature_targets", state, source_state="hero", length_feet=30
    ).result
    assert result["context"]["_srd_cone_targets"] == [f"actor{i}" for i in range(64)]
    assert result["context"]["_srd_cone_axis"] == [1, 0]
    assert result["context"]["_srd_cone_source"] == scene["entities"][0]


def test_curved_geometry_fails_clearly():
    state = _case([15, 0, 0])
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["footprint"] = {
        "kind": "circle",
        "radius": 1,
    }
    with pytest.raises(RulesetPreconditionRejected, match="box footprints"):
        budget._run(
            "qualify_srd_cone_admitted_box", state, source_index=0, target_index=1, length_feet=30
        )


def test_untyped_actual_actor_rejects_selection_atomically():
    state = _case([15, 0, 0])
    del state[f"{SRD}.entities"]["guard"]["creature_type"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="typed mechanics actor"):
        budget._run(
            "collect_srd_cone_creature_targets", state, source_state="hero", length_feet=30
        )
    assert state == before


def test_creator_can_choose_direction_and_include_the_actual_origin():
    state = _case([0, 15, 0])
    result = budget._run(
        "qualify_srd_cone_admitted_box",
        state,
        source_index=0,
        target_index=1,
        length_feet=30,
        direction=[0, 1],
    ).result
    assert result["context"]["_srd_cone_origin"] == [0, 5, 5]
    assert result["context"]["_srd_cone_intersects"] is True
    for included in (False, True):
        result = budget._run(
            "qualify_srd_cone_admitted_box",
            state,
            source_index=0,
            target_index=0,
            length_feet=30,
            include_origin=included,
        ).result
        assert result["context"]["_srd_cone_intersects"] is included
