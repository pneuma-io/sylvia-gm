"""Qualify the actual ward consumer against admitted round bodies and source state."""

import os
import zipfile
from copy import deepcopy

import pytest
import yaml
from scripts.srd_lite_ward_body import bind_ward_recipient_bodies
from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls
from tests.test_srd_lite_vertical_cones import run, state_for

TOOL = "resolve_srd_spell_ward_benefits"


@pytest.fixture(scope="module")
def wards():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = yaml.load(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml"), Loader=yaml.CSafeLoader
        )
    if any(
        name == "resolve_srd_environment_body_segment"
        for name in _calls(data["tools"][TOOL]["effects"])
    ):
        bind_ward_recipient_bodies(data)
    pool, closure, pending = data.get("parameter_declarations", {}), {}, [TOOL]
    while pending:
        name = pending.pop()
        if name not in closure:
            tool = deepcopy(data["tools"][name])
            tool["parameters"] = [
                deepcopy(pool[p]) if isinstance(p, str) else p for p in tool["parameters"]
            ]
            closure[name] = tool
            pending.extend(_calls(tool["effects"]))
    subset = {key: value for key, value in data.items() if key not in {"tools", "procedures"}}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


def _state(position, caster_kind="circle", recipient_kind="circle"):
    state = state_for(position)
    lasting = state[NAMESPACE + ".state"]
    for row, kind in zip(
        lasting["spatial_scene"]["entities"], (caster_kind, recipient_kind), strict=True
    ):
        row["footprint"] = (
            {"kind": "circle", "radius": 2}
            if kind == "circle"
            else {"kind": "box", "half_extents": [2, 2]}
        )
    lasting.update(
        has_spell_wards=True,
        spell_ward_source_ids=["life-source"],
        spell_ward_sources={
            "life-source": {
                "installed": True,
                "suppressed": False,
                "mode": "life",
                "expires_tick": 100,
                "concentration": True,
                "caster_state_id": "hero",
            }
        },
    )
    state[NAMESPACE + ".entities"]["hero"].update(
        ally_state_ids=["guard"],
        concentration_effect_family="actor_spell_ward",
        ward_source_id="life-source",
    )
    return state


@pytest.mark.parametrize(
    "caster_kind,recipient_kind",
    [("box", "box"), ("box", "circle"), ("circle", "box"), ("circle", "circle")],
)
@pytest.mark.parametrize("position,included", [([34, 0, 0], True), ([34.01, 0, 0], False)])
def test_current_body_boundary_includes_contact_without_center_or_square_proxy(
    wards, caster_kind, recipient_kind, position, included
):
    result = run(
        wards, _state(position, caster_kind, recipient_kind), TOOL, {"target_state": "guard"}
    )
    assert result.success, result.error
    assert result.context["_ward_life_effective"] is included
    assert not result.context["_ward_holy_effective"] and not result.context["_ward_bond_effective"]


@pytest.mark.parametrize(
    "caster_kind,recipient_kind,included",
    [
        ("box", "box", True),
        ("box", "circle", False),
        ("circle", "box", False),
        ("circle", "circle", False),
    ],
)
def test_diagonal_uses_actual_round_footprint(wards, caster_kind, recipient_kind, included):
    result = run(
        wards, _state([25, 25, 0], caster_kind, recipient_kind), TOOL, {"target_state": "guard"}
    )
    assert result.success, result.error
    assert result.context["_ward_life_effective"] is included


@pytest.mark.parametrize(
    "position,included", [([0, 0, 36], True), ([0, 0, 36.01], False), ([34, 0, 36], False)]
)
def test_current_height_combines_with_horizontal_body_gap(wards, position, included):
    result = run(wards, _state(position), TOOL, {"target_state": "guard"})
    assert result.success, result.error
    assert result.context["_ward_life_effective"] is included


@pytest.mark.parametrize("reason", ["not-ally", "other-plane", "suppressed"])
def test_existing_relation_plane_and_suppression_still_qualify_before_effects(wards, reason):
    state = _state([0, 0, 0])
    if reason == "not-ally":
        state[NAMESPACE + ".entities"]["hero"]["ally_state_ids"] = []
    elif reason == "other-plane":
        state[NAMESPACE + ".state"]["spatial_scene"]["entities"][1]["metadata"]["plane"] = (
            "ethereal"
        )
    else:
        state[NAMESPACE + ".state"]["spell_ward_sources"]["life-source"]["suppressed"] = True
    result = run(wards, state, TOOL, {"target_state": "guard"})
    assert result.success, result.error
    assert not result.context["_ward_life_effective"], {
        key: (result.context.get(key) or {}).get("metadata")
        for key in ("_ward_target_scene", "_ward_caster_scene")
    }


def test_following_query_uses_changed_authoritative_positions(wards):
    state = _state([34, 0, 0])
    result = run(wards, state, TOOL, {"target_state": "guard"})
    assert result.success and result.context["_ward_life_effective"]
    state[NAMESPACE + ".state"]["spatial_scene"]["entities"][0]["position"] = [-20, 0, 0]
    result = run(wards, state, TOOL, {"target_state": "guard"})
    assert result.success and not result.context["_ward_life_effective"]
