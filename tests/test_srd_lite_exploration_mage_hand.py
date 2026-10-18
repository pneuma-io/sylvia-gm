"""Exercise the real finite hand, object and clock owners without exploration map setup."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_exploration_mage_hand import (
    CAST,
    CHOICES,
    CONTROL,
    FACT_NODES,
    FACTS,
    MARKER,
    OPERATE,
    QUALIFY,
    RECONCILE,
    SOURCE,
    WRITE,
    _write_tool,
    bind_exploration_mage_hand,
)
from scripts.srd_lite_exploration_objects import (
    ADMIT,
    CAPTURE,
    LOAD,
    POOL,
    bind_exploration_objects,
)
from scripts.srd_lite_exploration_objects import (
    RECONCILE as RECONCILE_OBJECTS,
)
from scripts.srd_lite_profiles import lists
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.test_srd_lite_combat_target_facts import state
from tests.test_srd_lite_exploration_objects import (
    check_expression_limits,
    invoke,
    require_success,
)
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls

from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._schema_validator import validate_ruleset_data


def test_private_object_writer_uses_admitted_bounded_payload():
    owner = _write_tool()
    errors = validate_ruleset_data({"meta": {"id": "mage-hand-payload"}, "tools": {WRITE: owner}})
    assert not errors, errors
    payload = next(row for row in owner["parameters"] if row["name"] == "updates")
    assert payload["type"] == "array" and payload["items"] == {"type": "object"}
    assert payload["min_items"] == payload["max_items"] == 1


@pytest.fixture(scope="module")
def hands():
    with zipfile.ZipFile(
        os.environ.get("SRD_LITE_MAGE_HAND_BASE") or os.environ["SRD_LITE_CANDIDATE"]
    ) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    definitions = deepcopy(data["definitions"])
    if LOAD not in data["tools"]:
        bind_exploration_objects(data, {"tools": []})
    assert SOURCE not in data["tools"], "Select an unbound Mage Hand base package."
    native = {
        name: deepcopy(data["tools"][name])
        for name in (
            CAST,
            CONTROL,
            OPERATE,
            *FACTS,
            "refresh_srd_mage_hand",
            "release_srd_mage_hand_object",
            "deactivate_srd_mage_hand_scene",
            "qualify_srd_mage_hand_source",
        )
    }
    native_nodes = {name: deepcopy(data["tools"][name]) for name in FACT_NODES}
    changed = bind_exploration_mage_hand(data)
    assert data["definitions"] == definitions
    for name, original in native_nodes.items():
        assert data["tools"][name] == original
        exploration_name = FACT_NODES[name]
        assert exploration_name in changed
        assert data["tools"][exploration_name]["parameters"] == original["parameters"]
    for name in FACTS:
        native_node = name + "_node"
        actual = data["tools"][name]["effects"][0]["on_true"]
        assert FACT_NODES[native_node] in set(_calls(actual))
        assert native_node not in set(_calls(actual))
    for name, original in native.items():
        branch = data["tools"][name]["effects"][0]
        assert branch["condition"] == "combat.encounter_active != true"
        if name == CONTROL:
            assert branch["on_false"][0]["require"]["condition"] == "destination != null"
            expected = branch["on_false"][1:]
        else:
            expected = branch["on_false"]
        assert expected == original["effects"]
        check_expression_limits(branch["on_true"])
    for name in changed:
        if name not in native and name not in {
            "cast_spell",
            "cast_spell_node",
            "cast_spell_outside_combat",
            "begin_srd_spell_action",
            "complete_srd_pending_action",
            "finish_srd_retained_cast_tail",
            "cast_spell_object_utility_spell",
        }:
            check_expression_limits(data["tools"][name]["effects"])
        for group in lists(data["tools"][name]["effects"]):
            for row in group:
                field = row.get("set", {}).get("field")
                assert field not in {"object_id", "source_id"}, (name, field)
    for name in ("cast_spell", "begin_srd_spell_action"):
        found = False
        for group in lists(data["tools"][name]["effects"]):
            for index, row in enumerate(group):
                if (
                    row.get("set", {}).get("field") == "_casting_profile"
                    or row.get("store_as") == "_casting_profile"
                ):
                    assert QUALIFY in set(_calls(group[index + 1]))
                    found = True
        assert found, name
    delegate = [
        row
        for group in lists(data["tools"]["cast_spell_outside_combat"]["effects"])
        for row in group
        if row.get("call") == "cast_spell"
    ]
    assert len(delegate) == 1
    for choice, _, _, _ in CHOICES:
        assert delegate[0]["params"][choice] == "{" + choice + "}"
    with pytest.raises(ValueError, match="unbound"):
        bind_exploration_mage_hand(data)
    pending = [
        CAST,
        CONTROL,
        QUALIFY,
        RECONCILE,
        ADMIT,
        *FACTS,
        CAPTURE,
        RECONCILE_OBJECTS,
        "record_srd_object_container",
        "record_srd_object_break",
        "record_srd_hand_sized_form",
        "refresh_srd_object_forces",
        "retire_srd_mage_hand",
    ]
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset), data["tools"][CONTROL]["metadata"]["result_card"]


def fiction():
    value = state(combat=False)
    del value[NAMESPACE + ".state"]["spatial_scene"]
    value[NAMESPACE + ".combat"].update(encounter_active=False, pending_action=None)
    value[NAMESPACE + ".entities"]["hero"].update(
        creature_kind="humanoid",
        turns_started=0,
        last_spell_source_cast_id="hand-cast",
        last_spell_source_spell="mage_hand",
        concentrating_on="existing-concentration",
    )
    return value


def depiction(parsed, value):
    require_success(
        parsed,
        value,
        "record_srd_hand_sized_form",
        {
            "caster_state": "hero",
            "form_id": "spectral-hand",
            "fiction": "A pale spectral hand",
            "kind": "image",
            "extent": [1, 0.5, 0.5],
        },
    )


def object_facts(parsed, value, identity="vial", *, weight=1, holder="", kind="vial"):
    require_success(
        parsed,
        value,
        ADMIT,
        {
            "object_id": identity,
            "fiction": "An actual small object",
            "extent": [0.5, 0.5, 1],
            "size_rank": 0,
            "carried_by": holder,
            "movement": "passable",
            "visibility": "clear",
        },
    )
    require_success(
        parsed,
        value,
        FACTS[0],
        {
            "object_id": identity,
            "weight_pounds": weight,
            "manufactured": True,
            "metal": False,
            "object_kind": kind,
        },
    )


def facts(**changes):
    return {
        "mage_hand_placement": "Beside the table",
        "mage_hand_caster_distance": 10,
        "mage_hand_move_distance": 0,
        "mage_hand_clear_path": True,
        "mage_hand_object_contact": True,
        **changes,
    }


def qualify(parsed, value, **changes):
    return invoke(
        parsed,
        value,
        QUALIFY,
        {
            "caster_state": "hero",
            "output_id": "hand",
            "form_id": "spectral-hand",
            "operation": "move",
            "object_id": "",
            **facts(),
            **changes,
        },
    )


def cast(parsed, value, **changes):
    return invoke(
        parsed,
        value,
        CAST,
        {
            "caster_state": "hero",
            "spell": "mage_hand",
            "slot_level": 0,
            "output_id": "hand",
            "form_id": "spectral-hand",
            "point": [999, 999, 999],
            "operation": "move",
            "object_id": "",
            "spell_cast_id": "hand-cast",
            "spell_cast_owner": "hero",
            "actual_spell_level": 0,
            **facts(),
            **changes,
        },
    )


def control(parsed, value, operation="move", **changes):
    return invoke(
        parsed,
        value,
        CONTROL,
        {
            "caster_state": "hero",
            "source_id": value[NAMESPACE + ".entities"]["hero"]["mage_hand_source_id"],
            "operation": operation,
            "object_id": "",
            **facts(),
            **changes,
        },
    )


def body(value, identity):
    return value[NAMESPACE + ".state"][POOL][identity]["body"]


def source(value):
    return next(
        row
        for row in value[NAMESPACE + ".state"]["turn_effects"]
        if row.get("spell") == "mage_hand"
    )


def setup(hands, *, cargo=False):
    parsed = hands[0]
    value = fiction()
    depiction(parsed, value)
    if cargo:
        object_facts(parsed, value)
    result = cast(
        parsed, value, operation="hold" if cargo else "move", object_id="vial" if cargo else ""
    )
    assert result.success, result.error
    return value


def test_scene_free_cast_keeps_native_source_identity_duration_and_feedback(hands):
    parsed = hands[0]
    value = fiction()
    depiction(parsed, value)
    before = deepcopy(value)
    admitted = qualify(parsed, value)
    assert admitted.success, admitted.error
    assert value == before
    result = cast(parsed, value)
    assert result.success, result.error
    record = source(value)
    assert record["cast_id"] == "hand-cast" and record["source_caster_id"] == "hero"
    assert record["ongoing_duration"] is True and record["spell_level"] == 0
    assert record["force_id"] == "hand" and record["expires_tick"] - record["started_tick"] == 20
    assert body(value, "hand")["metadata"][MARKER]["fiction"] == "Beside the table"
    assert body(value, "hand")["height"] == 0.5
    assert body(value, "hand")["metadata"]["plane"] is None
    assert body(value, "hand")["metadata"]["lighting"] is None
    assert "spatial_scene" not in value[NAMESPACE + ".state"]
    assert value[NAMESPACE + ".entities"]["hero"]["concentrating_on"] == "existing-concentration"
    assert result.context["_utility_details"][-1]["value"] == "Beside the table"
    assert "" not in value[NAMESPACE + ".combat"]


@pytest.mark.parametrize(
    "change",
    [
        {"mage_hand_placement": ""},
        {"mage_hand_caster_distance": 30.001},
        {"mage_hand_clear_path": False},
        {"form_id": "unknown"},
        {"operation": "drop"},
    ],
)
def test_cast_qualification_rejects_invalid_fiction_before_any_mutation(hands, change):
    parsed = hands[0]
    value = fiction()
    depiction(parsed, value)
    before = deepcopy(value)
    rejected = qualify(parsed, value, **change)
    assert not rejected.success
    assert value == before


@pytest.mark.parametrize("weight,holder", [(10.001, ""), (1, "guard")])
def test_original_hold_weight_and_occupied_owner_limits_survive(hands, weight, holder):
    parsed = hands[0]
    value = fiction()
    depiction(parsed, value)
    object_facts(parsed, value, weight=weight, holder=holder)
    rejected = qualify(parsed, value, operation="hold", object_id="vial")
    assert not rejected.success and "ten pounds" in rejected.error
    assert not [
        row
        for row in value[NAMESPACE + ".state"]["turn_effects"]
        if row.get("spell") == "mage_hand"
    ]


def test_later_control_moves_native_cargo_without_combat_or_coordinates(hands):
    parsed, card = hands
    value = setup(hands, cargo=True)
    first = deepcopy(source(value))
    object_facts(parsed, value, "other", kind="object")
    rejected = control(parsed, value, "hold", object_id="other")
    assert not rejected.success and "one actual unheld object" in rejected.error
    moved = control(parsed, value, mage_hand_placement="At the shelf", mage_hand_move_distance=30)
    assert moved.success, moved.error
    assert value[NAMESPACE + ".state"]["lasting_tick"] == 2
    assert source(value)["expires_tick"] == first["expires_tick"]
    assert source(value)["cargo_id"] == "vial"
    for identity in ("hand", "vial"):
        assert body(value, identity)["metadata"][MARKER]["fiction"] == "At the shelf"
    assert body(value, "vial")["metadata"]["srd_spell_carrier"]["cast_id"] == "hand-cast"
    assert "spatial_scene" not in value[NAMESPACE + ".state"]
    assert "" not in value[NAMESPACE + ".combat"]
    projected = project_result_card(
        card,
        parameters={"operation": "move", "source_id": first["source_id"]},
        public_context=moved.context,
    )
    assert projected is not None
    assert projected["summary"][0]["value"] is False
    assert any(row.get("value") == "At the shelf" for row in projected["details"])


@pytest.mark.parametrize(
    "change",
    [
        {"mage_hand_move_distance": 30.001},
        {"mage_hand_clear_path": False},
        {"mage_hand_move_distance": -1},
        {"mage_hand_placement": ""},
    ],
)
def test_invalid_control_preserves_source_cargo_and_clock(hands, change):
    value = setup(hands, cargo=True)
    before = deepcopy(value)
    result = control(hands[0], value, **change)
    assert not result.success and value == before


def test_open_close_and_pour_keep_native_contents_and_property_changes(hands):
    parsed = hands[0]
    value = setup(hands)
    object_facts(parsed, value)
    require_success(
        parsed,
        value,
        FACTS[1],
        {
            "object_id": "vial",
            "object_kind": "container",
            "closed": True,
            "locks": [],
            "stuck": False,
            "barred": False,
        },
    )
    require_success(
        parsed,
        value,
        "record_srd_object_container",
        {
            "object_id": "vial",
            "capacity_gallons": 0.5,
            "water_gallons": 0.25,
            "is_open": False,
            "food_capacity_pounds": 0,
            "food_pounds": 0,
        },
    )
    opened = control(parsed, value, "open", object_id="vial")
    assert opened.success, opened.error
    assert body(value, "vial")["movement"] == "passable"
    assert body(value, "vial")["visibility"] == "clear"
    poured = control(parsed, value, "pour", object_id="vial")
    assert poured.success, poured.error
    metadata = body(value, "vial")["metadata"]
    assert metadata["srd_object_container"]["water_gallons"] == 0
    assert metadata["srd_poured_contents"]["water_gallons"] == 0.25
    assert metadata["srd_poured_contents"]["position"] is None
    assert metadata["srd_poured_contents"]["fictional_placement"] == "Beside the table"
    closed = control(parsed, value, "close", object_id="vial")
    assert closed.success, closed.error
    assert body(value, "vial")["movement"] == "blocking"
    assert body(value, "vial")["visibility"] == "opaque"
    assert not body(value, "vial")["metadata"]["srd_object_container"]["is_open"]


def test_native_range_disappearance_and_deadline_release_exact_cargo(hands):
    value = setup(hands, cargo=True)
    vanished = control(hands[0], value, mage_hand_caster_distance=30.001)
    assert vanished.success, vanished.error
    assert vanished.context["_force_vanished"] is True
    assert not body(value, "hand")["active"]
    assert body(value, "vial")["metadata"]["srd_spell_carrier"] is None
    assert not [
        row
        for row in value[NAMESPACE + ".state"]["turn_effects"]
        if row.get("spell") == "mage_hand"
    ]
    value = setup(hands, cargo=True)
    value[NAMESPACE + ".state"]["lasting_tick"] = source(value)["expires_tick"] - 2
    expired = control(hands[0], value)
    assert expired.success, expired.error
    assert expired.context["_force_vanished"] is True
    assert not body(value, "hand")["active"]
    assert body(value, "vial")["metadata"]["srd_spell_carrier"] is None


def scene_placement(value, *, hand=(12, 0, 0), cargo=(12, 0, 0)):
    scene = deepcopy(state(combat=False)[NAMESPACE + ".state"]["spatial_scene"])
    for identity, position in (("hand", hand), ("vial", cargo)):
        if identity in value[NAMESPACE + ".state"][POOL]:
            row = deepcopy(body(value, identity))
            row.update(map_id=scene["map"]["id"], position=list(position), facing=[1, 0])
            row["metadata"].update(plane="material", lighting="bright")
            scene["entities"].append(row)
    return scene


def reenter(parsed, value, incoming):
    require_success(parsed, value, RECONCILE, {"scenes": [incoming]})
    require_success(parsed, value, RECONCILE_OBJECTS, {})


def test_same_map_pending_exact_hand_cargo_reconcile_and_rebase_breaks(hands):
    parsed = hands[0]
    value = setup(hands, cargo=True)
    require_success(
        parsed,
        value,
        "record_srd_object_break",
        {
            "object_id": "vial",
            "break_id": "crack",
            "break_min": [-0.2, 0, 0],
            "break_max": [0.2, 0, 0.5],
        },
    )
    old = scene_placement(value, hand=(100, 0, 0), cargo=(100, 0, 0))
    value[NAMESPACE + ".state"]["spatial_scene"] = old
    require_success(parsed, value, CAPTURE, {})
    moved = control(
        parsed, value, mage_hand_placement="At the near shelf", mage_hand_move_distance=20
    )
    assert moved.success, moved.error
    incoming = scene_placement(value)
    before_revision = value[NAMESPACE + ".state"]["spatial_scene"]["map"]["revision"]
    require_success(parsed, value, RECONCILE, {"scenes": [incoming]})
    assert value[NAMESPACE + ".state"]["spatial_scene"]["map"]["revision"] == before_revision + 1
    require_success(parsed, value, RECONCILE_OBJECTS, {})
    assert value[NAMESPACE + ".state"]["spatial_scene"]["map"]["revision"] == before_revision + 2
    actual = {row["id"]: row for row in value[NAMESPACE + ".state"]["spatial_scene"]["entities"]}
    assert actual["hand"]["position"] == [12, 0, 0]
    assert actual["vial"]["position"] == [12, 0, 0]
    assert actual["vial"]["metadata"]["srd_object_breaks"][0]["min"][0] == pytest.approx(11.8)
    assert not body(value, "hand")["metadata"][MARKER]["pending"]
    assert "" not in value[NAMESPACE + ".combat"]


@pytest.mark.parametrize("kind", ["range", "plane", "dimensions", "source", "cargo_contact"])
def test_incoming_pending_force_uses_real_positions_and_identity(hands, kind):
    parsed = hands[0]
    value = setup(hands, cargo=True)
    value[NAMESPACE + ".state"]["spatial_scene"] = scene_placement(
        value, hand=(100, 0, 0), cargo=(100, 0, 0)
    )
    incoming = scene_placement(value)
    rows = {row["id"]: row for row in incoming["entities"]}
    if kind == "range":
        rows["hand"]["position"][0] = 31
    elif kind == "plane":
        rows["hand"]["metadata"]["plane"] = "ethereal"
    elif kind == "dimensions":
        rows["hand"]["height"] += 1
    elif kind == "source":
        rows["hand"]["metadata"]["srd_object_force"]["source_id"] = "forged"
    else:
        rows["vial"]["position"][0] = 20
    before = deepcopy(value)
    rejected = invoke(parsed, value, RECONCILE, {"scenes": [incoming]})
    assert not rejected.success and value == before


def test_open_and_retirement_frame_changes_survive_old_scene_capture(hands):
    parsed = hands[0]
    value = setup(hands)
    object_facts(parsed, value, "door", kind="object")
    require_success(
        parsed,
        value,
        FACTS[1],
        {
            "object_id": "door",
            "object_kind": "door",
            "closed": True,
            "locks": [],
            "stuck": False,
            "barred": False,
        },
    )
    old = scene_placement(value)
    door = deepcopy(body(value, "door"))
    door.update(
        map_id=old["map"]["id"],
        position=[20, 0, 0],
        facing=[1, 0],
        movement="blocking",
        visibility="opaque",
    )
    door["metadata"].update(plane="material", lighting="bright")
    door["metadata"]["srd_spell_object"] = {
        "hp": 7,
        "max_hp": 10,
        "magical": True,
        "destroyed": False,
    }
    old["entities"].append(door)
    value[NAMESPACE + ".state"]["spatial_scene"] = old
    require_success(parsed, value, CAPTURE, {})
    opened = control(parsed, value, "open", object_id="door")
    assert opened.success, opened.error
    require_success(parsed, value, CAPTURE, {})
    assert body(value, "door")["movement"] == "passable"
    assert body(value, "door")["visibility"] == "clear"
    assert body(value, "door")["metadata"]["srd_spell_object"]["hp"] == 7
    value[NAMESPACE + ".state"]["lasting_tick"] = source(value)["expires_tick"]
    require_success(parsed, value, "refresh_srd_object_forces", {})
    require_success(parsed, value, CAPTURE, {})
    assert not body(value, "hand")["active"]
    require_success(parsed, value, RECONCILE_OBJECTS, {})
    actual = {row["id"]: row for row in value[NAMESPACE + ".state"]["spatial_scene"]["entities"]}
    assert not actual["hand"]["active"]
    assert actual["door"]["movement"] == "passable"
    assert actual["door"]["metadata"]["srd_spell_object"]["hp"] == 7


def test_dropped_cargo_can_be_omitted_or_explicitly_reconciled_after_retirement(hands):
    parsed = hands[0]
    value = setup(hands, cargo=True)
    dropped = control(parsed, value, "drop", object_id="vial", mage_hand_placement="On the shelf")
    assert dropped.success, dropped.error
    require_success(
        parsed,
        value,
        "retire_srd_mage_hand",
        {
            "caster_state": "hero",
            "source_id": source(value)["source_id"],
        },
    )
    old = deepcopy(body(value, "vial"))
    unrelated = deepcopy(state(combat=False)[NAMESPACE + ".state"]["spatial_scene"])
    value[NAMESPACE + ".state"]["spatial_scene"] = unrelated
    reenter(parsed, value, unrelated)
    assert body(value, "vial") == old
    incoming = scene_placement(value, cargo=(15, 0, 0))
    incoming["entities"] = [row for row in incoming["entities"] if row["id"] != "hand"]
    value[NAMESPACE + ".state"]["spatial_scene"] = deepcopy(incoming)
    reenter(parsed, value, incoming)
    assert body(value, "vial")["position"] == [15, 0, 0]
    assert body(value, "vial")["metadata"]["srd_spell_carrier"] is None
    assert not body(value, "vial")["metadata"][MARKER]["pending"]
