"""Prove Misty Step's exploration authority and unchanged native payment/staging seams."""

import json
import os
import zipfile
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from scripts.srd_lite_mass_cure_geometry import _effect_lists
from scripts.srd_lite_misty_step_exploration import (
    ARRIVAL,
    CARD,
    COMBAT_VALIDATE,
    EXPLORE_CAST,
    EXPLORE_VALIDATE,
    FACTS,
    LOCATION_SCHEMA,
    MOUNT_FIELD,
    PAID,
    PARAMETER,
    RIDER_FIELD,
    ROWS,
    VALIDATE,
    bind_misty_step_exploration,
)
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.test_srd_lite_instant_combat_areas import scene
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _wall

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.domain.package_library import participant_schema_sha256
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget


def _without_new_facts(value):
    """Reconstruct the old transport exactly, keeping all old cost/roll/staging expressions."""
    if isinstance(value, list):
        return [
            _without_new_facts(row)
            for row in value
            if not (
                isinstance(row, dict)
                and (
                    row.get("name") == FACTS
                    or row.get("key") == repr(FACTS)
                    or row.get("set", {})
                    .get("field", "")
                    .endswith(".parameters." + FACTS)
                )
            )
        ]
    if isinstance(value, dict):
        return {
            key: _without_new_facts(child)
            for key, child in value.items()
            if key != FACTS
        }
    return value


def _subset(data, starts):
    closure, pending = {}, list(starts)
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {
        key: value for key, value in data.items() if key not in {"tools", "procedures"}
    }
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


@pytest.fixture(scope="module")
def misty_record():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    return next(record for record in manifest["packages"] if record["id"] == NAMESPACE)


@pytest.fixture(scope="module")
def misty(misty_record):
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    original = {
        "definitions": {
            key: deepcopy(data["definitions"][key])
            for key in ("spells", "spell_casting")
        },
        "tools": {
            name: deepcopy(data["tools"][name])
            for name in (
                PAID,
                CARD,
                VALIDATE,
                "cast_spell",
                "begin_srd_spell_action",
                "cast_spell_outside_combat",
                "complete_srd_pending_action",
            )
        },
    }
    original_artifacts = deepcopy(misty_record["participant_artifacts"])
    bind_misty_step_exploration(data, misty_record)
    for before, after in zip(
        original_artifacts, misty_record["participant_artifacts"], strict=True
    ):
        if before["kind"] != "player_character":
            assert before == after
            continue
        assert after["schema"] == before["schema"]
        assert (
            after["entity_state_schema"]["required"]
            == before["entity_state_schema"]["required"]
        )
        assert (
            after["initial_state_calculations"] == before["initial_state_calculations"]
        )
        assert after.get("initial_state_validation") == before.get(
            "initial_state_validation"
        )
        expected = deepcopy(before["entity_state_schema"])
        expected["properties"][ARRIVAL] = LOCATION_SCHEMA
        assert after["entity_state_schema"] == expected
        assert after["schema_sha256"] == participant_schema_sha256(
            after["schema"],
            entity_state_schema=expected,
            initial_state_calculations=after["initial_state_calculations"],
            initial_state_validation=after.get("initial_state_validation"),
        )
        assert after["schema_sha256"] != before["schema_sha256"]
    assert data["definitions"]["spells"] == original["definitions"]["spells"]
    assert (
        data["definitions"]["spell_casting"] == original["definitions"]["spell_casting"]
    )
    assert data["tools"][COMBAT_VALIDATE] == original["tools"][VALIDATE]
    assert data["tools"][PAID]["effects"][0]["condition"] == (
        "spell == 'misty_step' and combat.encounter_active != true"
    )
    assert (
        _without_new_facts(data["tools"][PAID]["effects"][0]["on_false"])
        == (original["tools"][PAID]["effects"])
    )
    assert (
        data["tools"][CARD]["effects"][0]["on_true"]
        == original["tools"][CARD]["effects"]
    )
    # Existing public effect order and paid/staged action owners stay intact.
    # Only the additional choice, pending parameter and preflight argument differ.
    for name in (
        "cast_spell",
        "begin_srd_spell_action",
        "cast_spell_outside_combat",
        "complete_srd_pending_action",
    ):
        assert (
            _without_new_facts(data["tools"][name]["effects"])
            == original["tools"][name]["effects"]
        )
    for root in ("cast_spell", "begin_srd_spell_action"):
        sites = [
            row
            for rows in _effect_lists(data["tools"][root]["effects"])
            for row in rows
            if row.get("call") == VALIDATE
        ]
        assert len(sites) == 1 and sites[0]["params"][FACTS] == "{" + FACTS + "}"
    # Exercise the actual saved-choice preflight branch using its native parameters,
    # excluding unrelated PC preparation checks already proved by the public owner.
    for root in ("cast_spell", "begin_srd_spell_action"):
        owner = data["tools"][root]
        branch = next(
            row
            for rows in _effect_lists(owner["effects"])
            for row in rows
            if any(child.get("call") == VALIDATE for child in row.get("on_true", []))
        )
        preview = deepcopy(owner)
        preview["pre_conditions"] = []
        preview["parameters"] = [
            p
            for p in preview["parameters"]
            if p["name"]
            in {"caster_state", "lasting_state", "spell", "travel_destination", FACTS}
        ]
        preview["effects"] = [
            {"set": {"field": "_ms", "value": "spell"}},
            {"set": {"field": "_instant_cast_targets", "value": []}},
            deepcopy(branch),
        ]
        data["tools"]["preflight_" + root] = preview
    starts = (
        EXPLORE_VALIDATE,
        EXPLORE_CAST,
        VALIDATE,
        CARD,
        "preflight_cast_spell",
        "preflight_begin_srd_spell_action",
    )
    return data, _subset(data, starts)


def state(*, combat=False, cached_scene=False):
    value = scene([10, 0, 0], hero=(0, 0, 0))
    value[NAMESPACE + ".combat"]["encounter_active"] = combat
    value[NAMESPACE + ".entities"]["hero"]["resources"] = {
        "spell_slot_2": 2,
        "spell_slot_4": 2,
    }
    if not cached_scene:
        value[NAMESPACE + ".state"].pop("spatial_scene")
    return value


def facts(**changes):
    return [
        {
            "place": "West balcony",
            "current_plane": "material",
            "plane": "material",
            "horizontal_distance_feet": 30,
            "elevation_change_feet": 0,
            "visible": True,
            "unoccupied": True,
            **changes,
        }
    ]


def run(parsed, value, tool, parameters, *, mutable=False):
    """Run actual owners with authoritative combat context and apply only bridge mutations."""
    before, rng = json.dumps(value, sort_keys=True), dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, value)

    def nested(name, values, parent):
        return execute_tool(
            name,
            values,
            parsed.tools,
            bridge,
            parsed.ruleset_config,
            parsed.definitions,
            combat_state=parent.combat_state,
            tool_executor=nested,
            budget=parent.budget,
        )

    result = execute_tool(
        tool,
        parameters,
        parsed.tools,
        bridge,
        parsed.ruleset_config,
        parsed.definitions,
        combat_state=value[NAMESPACE + ".combat"],
        tool_executor=nested,
        budget=ExecutionBudget(),
    )
    after = deepcopy(value)
    if result.success:
        for mutation in bridge.mutations():
            after[mutation.namespace + "." + mutation.key] = deepcopy(mutation.value)
    assert json.dumps(value, sort_keys=True) == before
    assert dice._rng.getstate() == rng
    if not mutable:
        assert not bridge.mutations()
        assert after == value
    return result, after


@pytest.mark.parametrize("root", ("cast_spell", "begin_srd_spell_action"))
def test_exploration_preflight_needs_no_scene_or_explicit_coordinate(misty, root):
    _, parsed = misty
    value = state()
    result, _ = run(
        parsed,
        value,
        "preflight_" + root,
        {"spell": "misty_step", "caster_state": "hero", FACTS: facts()},
    )
    assert result.success, result.error


@pytest.mark.parametrize(
    "changes,match",
    (
        ({"visible": False}, "see"),
        ({"unoccupied": False}, "unoccupied"),
        ({"plane": "ethereal"}, "plane"),
        ({"horizontal_distance_feet": 30.01}, "range"),
    ),
)
def test_invalid_exploration_destination_rejects_before_payment(misty, changes, match):
    _, parsed = misty
    for root in ("cast_spell", "begin_srd_spell_action"):
        value = state()
        result, _ = run(
            parsed,
            value,
            "preflight_" + root,
            {"spell": "misty_step", "caster_state": "hero", FACTS: facts(**changes)},
        )
        assert not result.success and match in result.error, result.error
        assert value[NAMESPACE + ".entities"]["hero"]["resources"]["spell_slot_2"] == 2


@pytest.mark.parametrize("destination", ([], [{"place": "West balcony"}]))
def test_unestablished_destination_is_never_defaulted_to_success(misty, destination):
    _, parsed = misty
    result, _ = run(
        parsed, state(), EXPLORE_VALIDATE, {"caster_state": "hero", FACTS: destination}
    )
    assert not result.success, result.error


def test_blinded_caster_cannot_override_sight_with_a_visible_destination_fact(misty):
    _, parsed = misty
    value = state()
    value[NAMESPACE + ".entities"]["hero"]["conditions"] = ["blinded"]
    result, _ = run(
        parsed, value, EXPLORE_VALIDATE, {"caster_state": "hero", FACTS: facts()}
    )
    assert not result.success and "see" in result.error, result.error


@pytest.mark.parametrize(
    "horizontal,elevation,allowed",
    (
        (24, -18, True),
        (24.01, -18, False),
        (24, 18, True),
        (24.01, 18, False),
        (0, -30, True),
        (0, 30.01, False),
    ),
)
def test_exploration_preserves_original_30_foot_range_including_either_elevation(
    misty, horizontal, elevation, allowed
):
    _, parsed = misty
    result, _ = run(
        parsed,
        state(),
        EXPLORE_VALIDATE,
        {
            "caster_state": "hero",
            FACTS: facts(
                horizontal_distance_feet=horizontal, elevation_change_feet=elevation
            ),
        },
    )
    assert result.success is allowed, result.error


@pytest.mark.parametrize("saved", ("travel", "path", "scene"))
def test_claimed_origin_cannot_override_a_saved_plane(misty, saved):
    _, parsed = misty
    value = state(cached_scene=saved == "scene")
    hero = value[NAMESPACE + ".entities"]["hero"]
    if saved == "travel":
        hero[ARRIVAL] = {
            "place": "Courtyard",
            "plane": "material",
            "native_anchor": False,
            "via_spell": "misty_step",
        }
    elif saved == "path":
        hero["find_path_current_plane"] = "material"
    result, _ = run(
        parsed,
        value,
        EXPLORE_VALIDATE,
        {
            "caster_state": "hero",
            FACTS: facts(current_plane="ethereal", plane="ethereal"),
        },
    )
    assert not result.success and "plane" in result.error, result.error


def test_actual_saved_plane_outweighs_an_older_exploration_arrival(misty):
    _, parsed = misty
    value = state(cached_scene=True)
    value[NAMESPACE + ".entities"]["hero"][ARRIVAL] = {
        "place": "Old courtyard",
        "plane": "ethereal",
        "native_anchor": False,
        "via_spell": "misty_step",
    }
    result, _ = run(
        parsed, value, EXPLORE_VALIDATE, {"caster_state": "hero", FACTS: facts()}
    )
    assert result.success, result.error


def test_paid_exploration_commits_caster_arrival_and_receipt_without_moving_scene(
    misty, misty_record
):
    _, parsed = misty
    value = state(cached_scene=True)
    hero = value[NAMESPACE + ".entities"]["hero"]
    hero["find_path_current_place"], hero["find_path_current_plane"] = (
        "Courtyard",
        "material",
    )
    result, after = run(
        parsed,
        value,
        EXPLORE_CAST,
        {"caster_state": "hero", FACTS: facts()},
        mutable=True,
    )
    assert result.success, result.error
    actual = after[NAMESPACE + ".entities"]["hero"]
    assert actual[ARRIVAL] == {
        "place": "West balcony",
        "plane": "material",
        "native_anchor": False,
        "via_spell": "misty_step",
    }
    artifact = next(
        row
        for row in misty_record["participant_artifacts"]
        if row["kind"] == "player_character"
    )
    assert ARRIVAL in artifact["entity_state_schema"]["properties"]
    Draft202012Validator(
        artifact["entity_state_schema"]["properties"][ARRIVAL]
    ).validate(actual[ARRIVAL])
    assert actual["find_path_current_place"] == "West balcony"
    assert (
        actual["resources"] == hero["resources"]
    )  # Costs remain exclusively at the shared cast owner.
    assert after[NAMESPACE + ".state"] == value[NAMESPACE + ".state"]
    assert (
        after[NAMESPACE + ".entities"]["guard"]
        == value[NAMESPACE + ".entities"]["guard"]
    )
    card, _ = run(parsed, after, CARD, {"caster_state": "hero"})
    assert card.success, card.error
    assert card.context[ROWS] == [
        {"label": "Arrival", "value": "West balcony"},
        {"label": "Plane", "value": "material"},
        {"label": "Mount status", "value": "Unmounted"},
    ]
    assert all(row["label"] != "Position (ft)" for row in card.context[ROWS])


def test_paid_exploration_detaches_only_actual_reciprocal_mount(misty):
    _, parsed = misty
    value = state()
    actors = value[NAMESPACE + ".entities"]
    actors["hero"][MOUNT_FIELD] = "guard"
    actors["guard"][RIDER_FIELD] = "hero"
    actors["guard"].update(created_spell_id="find_steed", created_active=True)
    result, after = run(
        parsed,
        value,
        EXPLORE_CAST,
        {"caster_state": "hero", FACTS: facts()},
        mutable=True,
    )
    assert result.success, result.error
    assert after[NAMESPACE + ".entities"]["hero"][MOUNT_FIELD] is None
    assert after[NAMESPACE + ".entities"]["guard"][RIDER_FIELD] is None
    assert ARRIVAL not in after[NAMESPACE + ".entities"]["guard"]


@pytest.mark.parametrize("failure", ("occupied", "footprint", "wall", "range"))
def test_combat_ignores_favorable_fictional_destination_claims(misty, failure):
    _, parsed = misty
    value = state(combat=True, cached_scene=True)
    point = [30, 0, 0]
    if failure == "occupied":
        value[NAMESPACE + ".state"]["spatial_scene"]["entities"][1]["position"] = point
    elif failure == "footprint":
        point = [
            10.79,
            0,
            0,
        ]  # The destination point is clear; the caster's full body overlaps.
    elif failure == "wall":
        wall = _wall()
        wall["volume"] = {"kind": "box", "min": [14, -5, -1], "max": [16, 5, 10]}
        value[NAMESPACE + ".state"]["spatial_scene"]["geometry"] = [wall]
    else:
        point = [30.01, 0, 0]
    result, _ = run(
        parsed,
        value,
        VALIDATE,
        {
            "caster_state": "hero",
            "destination": point,
            FACTS: facts(horizontal_distance_feet=0),
        },
    )
    assert not result.success, result.error
    assert any(
        word in result.error for word in ("unoccupied", "space", "see", "range")
    ), result.error


def test_combat_uses_saved_elevation_and_ignores_invalid_exploration_facts(misty):
    _, parsed = misty
    value = state(combat=True, cached_scene=True)
    saved = value[NAMESPACE + ".state"]["spatial_scene"]
    saved["entities"][0]["position"] = [0, 0, 18]
    saved["zones"] = [
        {
            "id": "actual-bright-room",
            "map_id": saved["map"]["id"],
            "shape": {"kind": "box", "min": [-5, -5, -5], "max": [40, 5, 30]},
            "metadata": {"active": True, "plane": "material", "lighting": "bright"},
        }
    ]
    result, _ = run(
        parsed,
        value,
        VALIDATE,
        {
            "caster_state": "hero",
            "destination": [24, 0, 0],
            FACTS: facts(visible=False, unoccupied=False, plane="ethereal"),
        },
    )
    assert result.success, result.error
    # A fabricated downhill story cannot admit a farther combat endpoint.
    result, _ = run(
        parsed,
        value,
        VALIDATE,
        {
            "caster_state": "hero",
            "destination": [24.01, 0, 0],
            FACTS: facts(elevation_change_feet=-1000, horizontal_distance_feet=0),
        },
    )
    assert not result.success and "range" in result.error, result.error


def test_pending_and_retained_choice_transport_carries_the_exact_fact_record(misty):
    data, _ = misty
    tools = data["tools"]
    for root in ("cast_spell", "complete_srd_spell_attack_consequences"):
        choice_rows = [
            row["set"]["value"][0]
            for rows in _effect_lists(tools[root]["effects"])
            for row in rows
            if row.get("set", {}).get("field") == "_retained_cast_tail_choices"
            and isinstance(row["set"]["value"], list)
        ]
        assert choice_rows and all(row[FACTS] == FACTS for row in choice_rows)
    # The normal pending owner uses nullable named rows. The longer-casting
    # Ritual owner has no immediate-cast choice capsule.
    pending_captures = [
        item
        for rows in _effect_lists(tools["begin_srd_spell_action"]["effects"])
        for row in rows
        if row.get("set", {}).get("field") == "_retained_nullable_capture_rows"
        for item in row["set"]["value"]
        if item.get("key") == repr(FACTS)
    ]
    assert pending_captures == [
        {"key": repr(FACTS), "missing": FACTS + " == null", "value": FACTS}
    ]
    pending_writes = [
        row
        for owner in tools.values()
        for rows in _effect_lists(owner.get("effects", []))
        for row in rows
        if row.get("set", {}).get("field", "").endswith(".parameters." + FACTS)
    ]
    assert any(row["set"]["value"] == FACTS for row in pending_writes)
    completion_calls = [
        row
        for rows in _effect_lists(tools["complete_srd_pending_action"]["effects"])
        for row in rows
        if row.get("call") in ("cast_spell", "begin_srd_spell_cast")
    ]
    assert completion_calls
    assert all(
        row["params"][FACTS] == "{_pending_action.parameters." + FACTS + "}"
        for row in completion_calls
        if "travel_destination" in row.get("params", {})
    )
    tail_calls = [
        row
        for rows in _effect_lists(tools["finish_srd_retained_cast_tail"]["effects"])
        for row in rows
        if row.get("call") == PAID
    ]
    assert len(tail_calls) == 1
    assert (
        tail_calls[0]["params"][FACTS] == "{_retained_cast_tail_choices." + FACTS + "}"
    )
    assert COMBAT_VALIDATE in tools


def test_native_pending_capture_and_completion_deliver_typed_exploration_facts(misty):
    """Execute the changed capture/projection seams; shared payment stays with its proven owner."""
    data, _ = misty
    tools = data["tools"]
    begin = tools["begin_srd_spell_action"]
    effects = begin["effects"]
    captured_at = next(
        i
        for i, row in enumerate(effects)
        if row.get("set", {}).get("field") == "_retained_nullable_capture_rows"
        and any(item.get("key") == repr(FACTS) for item in row["set"]["value"])
    )
    capture, capture_loop = deepcopy(effects[captured_at : captured_at + 2])
    capture["set"]["value"] = [
        row
        for row in capture["set"]["value"]
        if row["key"] in {repr(FACTS), "'caster_state'", "'spell'"}
    ]
    assert capture_loop["for_each"]["collection"] == "_retained_nullable_capture_rows"
    initialize = next(
        row
        for row in effects
        if row.get("set", {}).get("field")
        == "lasting_state.pending_srd_actions.{_pending_action_id}"
    )
    selected = {"caster_state", "lasting_state", "spell", FACTS}
    stage = {
        **begin,
        "pre_conditions": [],
        "parameters": [p for p in begin["parameters"] if p["name"] in selected],
        "effects": [
            {"set": {"field": "_pending_action_id", "value": "'misty-exploration'"}},
            {"set": {"field": "_ms", "value": "spell"}},
            deepcopy(initialize),
            capture,
            capture_loop,
        ],
    }
    complete = tools["complete_srd_pending_action"]
    read_pending = next(
        row
        for row in complete["effects"]
        if row.get("set", {}).get("field") == "_pending_action"
    )
    completion_call = deepcopy(
        next(
            row
            for rows in _effect_lists(complete["effects"])
            for row in rows
            if row.get("call") == "cast_spell"
            and "travel_destination" in row.get("params", {})
        )
    )
    completion_call["params"] = {
        name: value
        for name, value in completion_call["params"].items()
        if name in selected
    }
    # This transport probe omits native card assembly, already covered by the
    # paid owner proofs; its completion does not request those omitted exports.
    completion_call.pop("export_context", None)
    # Use the actual immediate choice object, native unpack operation and paid
    # travel projection. Only unrelated spell choices/cost owners are omitted.
    cast = tools["cast_spell"]
    definition = deepcopy(
        next(
            row
            for rows in _effect_lists(cast["effects"])
            for row in rows
            if row.get("store_as") == "_spell_definition"
        )
    )
    failed = deepcopy(
        next(
            row
            for rows in _effect_lists(cast["effects"])
            for row in rows
            if row.get("set", {}).get("field") == "_spell_cast_failed"
            and row["set"]["value"] is False
        )
    )
    choices = deepcopy(
        next(
            row
            for rows in _effect_lists(cast["effects"])
            for row in rows
            if row.get("set", {}).get("field") == "_retained_cast_tail_choices"
        )
    )
    choices["set"]["value"][0] = {
        name: value
        for name, value in choices["set"]["value"][0].items()
        if name in {"spell", FACTS}
    }
    tail = tools["finish_srd_retained_cast_tail"]
    unpack = next(
        row
        for row in tail["effects"]
        if row.get("set", {}).get("field") == "_retained_cast_tail_choices"
    )
    travel_call = deepcopy(
        next(
            row
            for rows in _effect_lists(tail["effects"])
            for row in rows
            if row.get("call") == PAID
        )
    )
    travel_call["params"] = {
        name: value for name, value in travel_call["params"].items() if name in selected
    }
    fragments = {
        **data,
        "tools": {
            **tools,
            "stage_native_misty_facts": stage,
            "complete_srd_pending_action": {
                **complete,
                "pre_conditions": [],
                "effects": [deepcopy(read_pending), completion_call],
            },
            "cast_spell": {
                **cast,
                "pre_conditions": [],
                "parameters": [p for p in cast["parameters"] if p["name"] in selected],
                "effects": [
                    {"set": {"field": "_ms", "value": "spell"}},
                    definition,
                    failed,
                    choices,
                    deepcopy(unpack),
                    travel_call,
                ],
            },
            PAID: {
                **tools[PAID],
                "pre_conditions": [],
                "parameters": [
                    p for p in tools[PAID]["parameters"] if p["name"] in selected
                ],
                "effects": deepcopy(tools[PAID]["effects"][0]["on_true"]),
            },
        },
    }
    parsed = _subset(
        fragments, ("stage_native_misty_facts", "complete_srd_pending_action")
    )
    value = state()
    supplied = facts(
        place="Upper landing", horizontal_distance_feet=24, elevation_change_feet=18
    )
    staged, after = run(
        parsed,
        value,
        "stage_native_misty_facts",
        {"caster_state": "hero", "spell": "misty_step", FACTS: supplied},
        mutable=True,
    )
    assert staged.success, staged.error
    pending = after[NAMESPACE + ".state"]["pending_srd_actions"]["misty-exploration"]
    assert pending["parameters"]["spell"] == "misty_step"
    assert pending["parameters"][FACTS] == supplied
    assert pending["parameters"]["caster_state"] == "hero"
    completed, final = run(
        parsed,
        after,
        "complete_srd_pending_action",
        {"pending_action_id": "misty-exploration"},
        mutable=True,
    )
    assert completed.success, completed.error
    assert final[NAMESPACE + ".entities"]["hero"][ARRIVAL] == {
        "place": "Upper landing",
        "plane": "material",
        "native_anchor": False,
        "via_spell": "misty_step",
    }
    assert (
        final[NAMESPACE + ".entities"]["hero"]["resources"]
        == value[NAMESPACE + ".entities"]["hero"]["resources"]
    )


@pytest.mark.parametrize("field,limit", (("place", 300), ("current_plane", 100), ("plane", 100)))
def test_oversized_exploration_destination_rejects_before_payment(misty, field, limit):
    _, parsed = misty
    value = state()
    result, _ = run(parsed, value, EXPLORE_VALIDATE,
                    {"caster_state": "hero", FACTS: facts(**{field: "x" * (limit + 1)})})
    assert not result.success and "bounded" in result.error, result.error


def test_pooled_exploration_fact_descriptor_uses_supported_native_fields():
    from sylvia_gm.runtime._schema_validator import validate_parameter_declarations

    assert validate_parameter_declarations({"parameter_declarations": {"param_misty": PARAMETER}}) == []


def test_current_native_arrival_plane_supersedes_historical_arena(misty):
    _, parsed = misty
    value = state(cached_scene=True)
    value[NAMESPACE + ".entities"]["hero"][ARRIVAL] = {
        "place": "Ethereal balcony", "plane": "ethereal", "native_anchor": False, "via_spell": "misty_step"
    }
    result, _ = run(parsed, value, EXPLORE_VALIDATE,
                    {"caster_state": "hero", FACTS: facts(current_plane="ethereal", plane="ethereal")})
    assert result.success, result.error


def test_arrival_schema_accepts_only_the_exact_bounded_exploration_record(
    misty, misty_record
):
    from sylvia_gm.application.schema_admission import validate_json_schema

    data, _ = misty
    artifact = next(
        row
        for row in misty_record["participant_artifacts"]
        if row["kind"] == "player_character"
    )
    schema = artifact["entity_state_schema"]["properties"][ARRIVAL]
    assert schema == LOCATION_SCHEMA
    assert artifact["entity_state_schema"]["additionalProperties"] is False
    assert ARRIVAL not in artifact["entity_state_schema"]["required"]
    Draft202012Validator.check_schema(schema)
    validate_json_schema(schema, label="Misty Step exploration arrival")
    validator = Draft202012Validator(schema)
    valid = {
        "place": "West balcony",
        "plane": "material",
        "native_anchor": False,
        "via_spell": "misty_step",
    }
    assert validator.is_valid(valid)
    for invalid in (
        {key: value for key, value in valid.items() if key != "plane"},
        valid | {"position": [0, 0, 0]},
        valid | {"place": "x" * 301},
        valid | {"plane": "x" * 101},
        valid | {"native_anchor": True},
        valid | {"via_spell": "dimension_door"},
        None,
    ):
        assert not validator.is_valid(invalid)
    # The generated paid write closes over precisely this declared object; no
    # unrelated actor fields or initial-state calculation were introduced.
    writes = [
        row["set"]["value"]
        for rows in _effect_lists(data["tools"][EXPLORE_CAST]["effects"])
        for row in rows
        if row.get("set", {}).get("field") == "caster_state." + ARRIVAL
    ]
    assert len(writes) == 1 and set(writes[0]) == set(schema["required"])
    assert (
        writes[0]["native_anchor"] is False and writes[0]["via_spell"] == "'misty_step'"
    )
