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
from tests.test_srd_lite_combat_target_facts import state
from tests.test_srd_lite_exploration_objects import (
    cast,
    check_expression_limits,
    invoke,
    require_success,
)
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls

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


@pytest.mark.parametrize("stale", [False, True])
def test_current_recipient_facts_work_without_or_against_stale_map(dispel, stale):
    value = fiction(stale=stale)
    result = require_success(
        dispel,
        value,
        RECIPIENT,
        {
            "caster_state": "hero",
            "target_state": "guard",
            "target_distance": 120,
            "cover": "three_quarters",
        },
    )
    assert "_srd65_dispel_origin" not in result.context
    assert "_scene_distance" not in result.context


@pytest.mark.parametrize("stale", [False, True])
def test_ordinary_current_recipient_reaches_native_source_check_and_removal(dispel, stale):
    value = fiction(stale=stale)
    # The saved timed-senses record is the native Darkvision producer's finite
    # record, including the actual source-level annotation used by Dispel.
    actual = {
        "spell": "darkvision",
        "label": "darkvision",
        "caster": "hero",
        "target": "guard",
        "kind": "timed_senses",
        "owner": "__time_only__",
        "phase": "time",
        "turn": 1000000000,
        "started_tick": 0,
        "expires_tick": 9600,
        "concentration": False,
        "magnitude": 0,
        "dice_count": 0,
        "damage_die": "",
        "damage_type": "",
        "spell_level": 2,
    }
    value[NAMESPACE + ".state"]["turn_effects"] = [actual]
    require_success(
        dispel,
        value,
        RECIPIENT,
        {
            "caster_state": "hero",
            "target_state": "guard",
            "target_distance": 20,
            "cover": "none",
        },
    )
    result = require_success(
        dispel,
        value,
        RECOVERY,
        {
            "caster_state": "hero",
            "target_state": "guard",
            "spell": "dispel_magic",
            "slot_level": 3,
            "casting_modifier": 3,
            "casting_ability": "wisdom",
            "save_dc": 13,
            "concentration_save_modifier": 0,
            "can_touch": False,
            "target_distance": 20,
        },
    )
    assert actual not in value[NAMESPACE + ".state"]["turn_effects"]
    assert result.context["_dispel_checks"] == [
        {"spell": "darkvision", "roll": None, "ended": True}
    ]


@pytest.mark.parametrize(
    "facts",
    [
        {"target_distance": 120.01},
        {"target_distance": -1},
        {"target_distance": None},
        {"cover": "total"},
        {"cover": None},
    ],
)
def test_invalid_ordinary_facts_fail_in_actual_prefix_before_payment_or_rng(dispel, facts):
    value = fiction(stale=True)
    before = deepcopy(value)
    result = invoke(
        dispel,
        value,
        "preflight_exploration_dispel",
        {
            "caster_state": "hero",
            "spell": "dispel_magic",
            "target": "guard",
            "target_distance": 20,
            "cover": "none",
            **facts,
        },
    )
    assert not result.success
    assert value == before
    assert "_dispel_roll" not in result.context
    assert "_slot_level" not in result.context


@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("choice", ["illumination_object", "illumination_effect"])
def test_scene_free_light_uses_exact_native_clock_source_removal_and_card_tail(
    dispel,
    stale,
    choice,
):
    value = fiction(stale=stale)
    actual = light(dispel, value)
    source_id = (
        "cup" if choice == "illumination_object" else "illumination:" + str(actual["instance"])
    )
    before = deepcopy(value[NAMESPACE + ".state"]["turn_effects"])
    result = require_success(dispel, value, SELECTED, selection(choice=choice, source_id=source_id))
    assert value[NAMESPACE + ".state"]["turn_effects"] == before
    assert result.context["_srd65_selected_rows"] == [actual]
    assert "_srd65_dispel_origin" not in result.context
    assert "_scene_distance" not in result.context
    result = require_success(
        dispel,
        value,
        RECOVERY,
        {
            "caster_state": "hero",
            "spell": "dispel_magic",
            "slot_level": 3,
            "casting_modifier": 3,
            "casting_ability": "wisdom",
            "save_dc": 13,
            "concentration_save_modifier": 0,
            "can_touch": False,
            "recovery_choice": choice,
            "recovery_source_id": source_id,
            "origin_distance": 20,
            "origin_has_clear_path": True,
        },
    )
    assert actual not in value[NAMESPACE + ".state"]["turn_effects"]
    decision = result.context["_dispel_checks"][0]
    assert decision["source"] == "cup" and decision["instance"] == actual["instance"]
    assert decision["source_level"] == 0 and decision["roll"] is None and decision["ended"] is True
    assert result.context["_spell_summary_rows"] == [
        {"label": "Illumination target", "value": source_id}
    ]
    assert result.context["_spell_detail_rows"][-1]["value"] == [decision]


@pytest.mark.parametrize(
    "changes",
    [
        {"origin_distance": 121},
        {"origin_distance": None},
        {"origin_has_clear_path": False},
        {"source_id": "missing"},
    ],
)
def test_invalid_light_facts_precede_actual_source_rng_and_payment(dispel, changes):
    value = fiction(stale=True)
    light(dispel, value)
    before = deepcopy(value)
    result = invoke(
        dispel,
        value,
        "preflight_exploration_dispel",
        {
            "caster_state": "hero",
            "spell": "dispel_magic",
            "recovery_choice": "illumination_object",
            "recovery_source_id": changes.get("source_id", "cup"),
            "origin_distance": changes.get("origin_distance", 20),
            "origin_has_clear_path": changes.get("origin_has_clear_path", True),
        },
    )
    assert not result.success
    assert value == before
    assert "_dispel_roll" not in result.context
    assert "_slot_level" not in result.context


def test_elapsed_light_does_not_become_an_active_source(dispel):
    value = fiction()
    actual = light(dispel, value)
    value[NAMESPACE + ".state"]["lasting_tick"] = actual["expires_tick"]
    before = deepcopy(value)
    result = invoke(dispel, value, SELECTED, selection())
    assert not result.success
    assert value == before
    assert "_dispel_roll" not in result.context


def test_combat_still_uses_actual_coordinate_plane_and_range(dispel):
    parameters = {
        "caster_state": "hero",
        "target_state": "guard",
        "target_distance": 1,
        "cover": "none",
    }
    too_far = invoke(dispel, state((121, 0, 0)), RECIPIENT, parameters)
    assert not too_far.success
    wrong_plane = invoke(dispel, state((20, 0, 0), plane="other-plane"), RECIPIENT, parameters)
    assert not wrong_plane.success
    legal = require_success(
        dispel,
        state((120, 0, 0)),
        RECIPIENT,
        {**parameters, "target_distance": 999, "cover": "total"},
    )
    assert legal.context["_scene_distance"] == 120


def test_higher_level_source_keeps_actual_d20_receipt(dispel):
    value = fiction()
    value[NAMESPACE + ".state"]["turn_effects"] = [
        {
            "spell": "darkvision",
            "label": "darkvision",
            "caster": "hero",
            "target": "guard",
            "kind": "timed_senses",
            "owner": "__time_only__",
            "phase": "time",
            "turn": 1000000000,
            "started_tick": 0,
            "expires_tick": 9600,
            "concentration": False,
            "magnitude": 0,
            "dice_count": 0,
            "damage_die": "",
            "damage_type": "",
            "spell_level": 4,
        }
    ]
    result = invoke(
        dispel,
        value,
        RECOVERY,
        {
            "caster_state": "hero",
            "target_state": "guard",
            "spell": "dispel_magic",
            "slot_level": 3,
            "casting_modifier": 3,
            "casting_ability": "wisdom",
            "save_dc": 13,
            "concentration_save_modifier": 0,
            "can_touch": False,
            "target_distance": 20,
        },
        allow_rng=True,
    )
    assert result.success, result.error
    decision = result.context["_dispel_checks"][0]
    assert decision["roll"].sides == 20 and decision["roll"].count == 1
    assert len(decision["roll"].results) == 1
    assert decision["roll"].total == sum(decision["roll"].results)
    assert decision["roll"].modifier == 3
    assert decision["ended"] is (decision["roll"].total + decision["roll"].modifier >= 14)
    assert len(value[NAMESPACE + ".state"]["turn_effects"]) == int(not decision["ended"])
