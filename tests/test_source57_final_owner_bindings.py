"""Focused assertions against the same complete admitted final owners; future lease only."""

from copy import deepcopy

import pytest
from scripts import srd_contingency_child_casting as child
from scripts.srd_control_behaviors import _walk
from sylvia_gm.spatial import admit_spatial_scene

from tests import test_srd_contingency_child_casting as native
from tests import test_srd_contingency_removal as removal

SOURCE_BINDINGS = {
    "spell_cast_id": "{_spell_source_cast_id}",
    "spell_cast_owner": "{_target_state_id}",
    "actual_spell_level": "{_contingency_child.slot_level}",
}
TAIL_FAMILIES = (
    "timed_senses",
    "information_spell",
    "illumination_spell",
    "illusion_image",
    "illusion_appearance",
    "detection_mask_spell",
)


def _assert_required_source_mapping(original, mapped):
    """Require every actual native source formal to survive with its retained binding."""
    for field, expected in SOURCE_BINDINGS.items():
        if field in original["params"]:
            assert field in mapped["params"], f"missing required native source field: {field}"
            assert mapped["params"][field] == expected, f"wrong retained source field: {field}"


def test_every_final_family_dispatch_preserves_exports_and_native_source_binding():
    tools, _ = native.actual.__wrapped__()
    for family, owner in child.DISPATCHES.items():
        call = child._actual_dispatch(tools, family)
        mapped = child._dispatch(call)
        assert call["call"] == owner == mapped["call"]
        assert mapped.get("export_context") == call.get("export_context")
        _assert_required_source_mapping(call, mapped)


def test_final_tail_lookup_rejects_unreachable_or_duplicate_dispatch():
    tools, _ = native.actual.__wrapped__()
    changed = deepcopy(tools)
    changed["cast_spell"]["effects"] = [
        n
        for n in changed["cast_spell"]["effects"]
        if not any(part.get("call") == "finish_srd_retained_cast_tail" for part in _walk(n))
    ]
    with pytest.raises(ValueError, match="actual final paid-cast tail"):
        child._actual_dispatch(changed, "illumination_spell")
    changed = deepcopy(tools)
    call = child._actual_dispatch(changed, "illumination_spell")
    changed["finish_srd_retained_cast_tail"]["effects"].append(deepcopy(call))
    with pytest.raises(ValueError, match="exactly one actual illumination_spell"):
        child._actual_dispatch(changed, "illumination_spell")


def test_unknown_captured_choice_is_rejected_without_changing_native_call():
    tools, _ = native.actual.__wrapped__()
    call = deepcopy(child._actual_dispatch(tools, "illumination_spell"))
    call["params"]["illumination_object"] = "{_retained_cast_tail_choices.unapproved_choice}"
    before = deepcopy(call)
    with pytest.raises(ValueError, match="no genuine retained binding"):
        child._dispatch(call)
    assert call == before


def test_actual_inventory_setup_retains_complete_admitted_scene_before_drop(monkeypatch):
    removal._owners.__wrapped__(monkeypatch)
    expected = admit_spatial_scene(
        removal.force.objects._state()[f"{removal.SRD}.state"]["spatial_scene"]
    )
    state, _, _, sid = removal._cast(monkeypatch)
    scene = admit_spatial_scene(state[f"{removal.SRD}.state"]["spatial_scene"])
    assert scene.snapshot_id == expected.snapshot_id
    assert scene.map_id == expected.map_id
    assert scene.as_payload() == expected.as_payload()
    bodies = {row["id"]: row for row in scene.as_payload()["entities"]}
    assert set(bodies) == {"hero", "guard", "door", "other"}
    assert {name: row["position"] for name, row in bodies.items()} == {
        "hero": [0, 0, 0],
        "guard": [3, 2, 0],
        "door": [3, 0, 0],
        "other": [3, -2, 0],
    }
    assert bodies["door"]["metadata"] == {
        "plane": "material",
        "lighting": "bright",
        "fiction": "Synthetic wooden door",
    }
    assert bodies["other"]["metadata"]["participant_ref"] == "other"
    hero = state[f"{removal.SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_6"] == 2
    assert hero["resources"]["spell_slot_2"] == 2
    assert hero["temp_hp"] == 0
    assert state[f"{removal.SRD}.state"][removal.POOL][sid]["status"] == "armed"


@pytest.mark.parametrize("field", tuple(SOURCE_BINDINGS))
def test_required_mapping_predicate_rejects_dropped_native_field(field):
    tools, _ = native.actual.__wrapped__()
    seen = 0
    for family in child.DISPATCHES:
        call = child._actual_dispatch(tools, family)
        if field not in call["params"]:
            continue
        mapped = child._dispatch(call)
        _assert_required_source_mapping(call, mapped)
        del mapped["params"][field]
        with pytest.raises(AssertionError, match="missing required native source field"):
            _assert_required_source_mapping(call, mapped)
        seen += 1
    assert seen > 0


@pytest.mark.parametrize("family", TAIL_FAMILIES)
def test_affected_tail_family_rejects_missing_unreachable_duplicate_and_overlap(family):
    tools, _ = native.actual.__wrapped__()
    tail_name = "finish_srd_retained_cast_tail"
    changed = dict(tools)
    del changed[tail_name]
    with pytest.raises(ValueError, match="Unsupported final-layout fixture.*missing"):
        child._actual_dispatch(changed, family)
    changed = dict(tools)
    changed["cast_spell"] = deepcopy(tools["cast_spell"])
    changed["cast_spell"]["effects"] = [
        n
        for n in changed["cast_spell"]["effects"]
        if not any(part.get("call") == tail_name for part in _walk(n))
    ]
    with pytest.raises(ValueError, match="actual final paid-cast tail"):
        child._actual_dispatch(changed, family)
    call = child._actual_dispatch(tools, family)
    changed = dict(tools)
    changed[tail_name] = deepcopy(tools[tail_name])
    changed[tail_name]["effects"].append(deepcopy(call))
    with pytest.raises(ValueError, match="exactly one actual"):
        child._actual_dispatch(changed, family)
    changed = dict(tools)
    changed["cast_spell"] = deepcopy(tools["cast_spell"])
    changed["cast_spell"]["effects"].append(deepcopy(call))
    with pytest.raises(ValueError, match="actual final paid-cast tail"):
        child._actual_dispatch(changed, family)
