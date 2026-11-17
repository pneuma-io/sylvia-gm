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
from tests.srd_lite_combat_target_facts_fixtures import state
from tests.srd_lite_exploration_objects_fixtures import (
    check_expression_limits,
    invoke,
    require_success,
)
from tests.srd_lite_mass_cure_geometry_fixtures import NAMESPACE, _calls

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
