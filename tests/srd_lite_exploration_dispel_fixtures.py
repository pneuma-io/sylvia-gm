"""Exercise native Dispel recipients and retained Light with current exploration facts."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_exploration_dispel import (
    COMBAT,
    ORIGIN_FACTS,
    RECIPIENT,
    RECOVERY,
    ROOTS,
    SELECTED,
    _actual_roll_receipts,
    bind_exploration_dispel,
)
from scripts.srd_lite_exploration_objects import ADMIT, LIGHT, LOAD
from scripts.srd_lite_profiles import lists
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.srd_lite_combat_target_facts_fixtures import state
from tests.srd_lite_exploration_objects_fixtures import (
    cast,
    check_expression_limits,
    invoke,
    require_success,
)
from tests.srd_lite_mass_cure_geometry_fixtures import NAMESPACE, _calls

from sylvia_gm.runtime import RulesetRuntime


@pytest.fixture(scope="module")
def dispel():
    with zipfile.ZipFile(
        os.environ.get("SRD_LITE_DISPEL_BASE") or os.environ["SRD_LITE_CANDIDATE"]
    ) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    assert LOAD in data["tools"], "Select the current qualified exploration-object base."
    definitions = deepcopy(data["definitions"])
    native = {name: deepcopy(data["tools"][name]) for name in (RECIPIENT, SELECTED, RECOVERY)}
    native_light_check = deepcopy(data["tools"]["check_srd_illumination_record"])
    changed = bind_exploration_dispel(data)
    assert definitions == data["definitions"]
    assert data["tools"]["check_srd_illumination_record"] == native_light_check
    recipient_branch = data["tools"][RECIPIENT]["effects"][0]
    assert recipient_branch["condition"] == COMBAT
    assert recipient_branch["on_true"] == native[RECIPIENT]["effects"]
    selected = data["tools"][SELECTED]
    # Every changed geometry seam retains the exact native combat effects.
    assert selected["effects"][0]["on_true"] == native[SELECTED]["effects"][:4]
    original_object = next(
        row
        for row in native[SELECTED]["effects"]
        if row.get("condition") == "choice == 'illumination_object'"
    )
    actual_object = next(
        row
        for row in selected["effects"]
        if row.get("condition") == "choice == 'illumination_object'"
    )
    assert actual_object["on_true"][0]["on_true"] == original_object["on_true"]
    original_record = next(
        row
        for group in lists(native[SELECTED]["effects"])
        for row in group
        if row.get("call") == "check_srd_illumination_record"
    )
    record_branch = next(
        row
        for group in lists(selected["effects"])
        for row in group
        if row.get("condition") == COMBAT and row.get("on_true") == [original_record]
    )
    assert record_branch["on_true"] == [original_record]
    original_path_start = next(
        i
        for i, row in enumerate(native[SELECTED]["effects"])
        if row.get("require", {}).get("condition") == "_srd65_selected_plane == _srd65_dispel_plane"
    )
    path = next(
        row
        for row in selected["effects"]
        if row.get("condition") == COMBAT
        and row.get("on_true")
        == native[SELECTED]["effects"][original_path_start : original_path_start + 5]
    )
    assert (
        path["on_true"]
        == (native[SELECTED]["effects"][original_path_start : original_path_start + 5])
    )
    # Native check/removal and final card rows remain one shared paid tail.
    paid_start = next(
        i
        for i, row in enumerate(native[SELECTED]["effects"])
        if row.get("set", {}).get("field") == "_srd65_dispel_checks"
    )
    actual_paid_start = next(
        i
        for i, row in enumerate(selected["effects"])
        if row.get("set", {}).get("field") == "_srd65_dispel_checks"
    )
    expected_paid = {"effects": deepcopy(native[SELECTED]["effects"][paid_start:])}
    _actual_roll_receipts(expected_paid)
    assert selected["effects"][actual_paid_start:] == expected_paid["effects"]
    expected_recovery = deepcopy(native[RECOVERY]["effects"])
    for group in lists(expected_recovery):
        for row in group:
            if row.get("call") == SELECTED:
                row["params"].update({fact: "{" + fact + "}" for fact in ORIGIN_FACTS})
    _actual_roll_receipts({"effects": expected_recovery})
    assert data["tools"][RECOVERY]["effects"] == expected_recovery
    for name in changed:
        if name not in ROOTS:
            check_expression_limits(data["tools"][name]["effects"])
    # Keep the exact active public prefix through its inherited prepayment joins.
    public = deepcopy(data["tools"]["cast_spell"])
    end = next(
        index
        for index, row in enumerate(public["effects"])
        if {RECIPIENT, SELECTED} <= set(_calls(row))
    )
    public["effects"] = public["effects"][: end + 1]
    public["pre_conditions"] = []
    data["tools"]["preflight_exploration_dispel"] = public
    for name in ROOTS:
        for group in lists(data["tools"][name]["effects"]):
            for row in group:
                if row.get("call") == RECIPIENT:
                    assert row["params"]["target_distance"] == "{target_distance}"
                    assert row["params"]["cover"] == "{cover}"
                if row.get("call") in (SELECTED, RECOVERY):
                    for fact in ORIGIN_FACTS:
                        assert row["params"][fact] == "{" + fact + "}"
    completion = data["tools"]["complete_srd_pending_action"]
    delegates = [
        row
        for group in lists(completion["effects"])
        for row in group
        if row.get("call") == "cast_spell"
    ]
    assert len(delegates) == 1
    for fact in ORIGIN_FACTS:
        assert delegates[0]["params"][fact] == "{_pending_action.parameters." + fact + "}"
    closure, pending = (
        {},
        [RECIPIENT, SELECTED, RECOVERY, LIGHT, ADMIT, "preflight_exploration_dispel"],
    )
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    with pytest.raises(ValueError, match="already bound"):
        bind_exploration_dispel(data)
    return RulesetRuntime(ruleset_dict=subset)


def fiction(*, stale=False):
    value = state((190, 0, 0), combat=False, plane="other-plane", lighting="darkness")
    value[NAMESPACE + ".combat"].update(encounter_active=False, pending_action=None)
    if not stale:
        del value[NAMESPACE + ".state"]["spatial_scene"]
    return value


def light(dispel, value):
    require_success(
        dispel,
        value,
        ADMIT,
        {
            "object_id": "cup",
            "fiction": "A wooden cup",
            "extent": [1, 1, 1],
            "size_rank": 0,
            "movement": "passable",
            "visibility": "clear",
        },
    )
    result = cast(dispel, value, "light")
    assert result.success, result.error
    return next(
        row for row in value[NAMESPACE + ".state"]["turn_effects"] if row.get("spell") == "light"
    )


def selection(**changes):
    return {
        "caster_state": "hero",
        "choice": "illumination_object",
        "source_id": "cup",
        "validate_only": True,
        "slot_level": 3,
        "casting_modifier": 3,
        "casting_ability": "wisdom",
        "origin_distance": 20,
        "origin_has_clear_path": True,
        **changes,
    }
