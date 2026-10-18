"""Focused scene-free Steed callback composition with the previously admitted native owners."""

import re
from copy import deepcopy
from random import Random

import pytest
from scripts.srd_lite_combat_scene_reentry import ADMIT, PREPARE_STEED, bind_combat_scene_reentry
from scripts.srd_lite_steed_exploration_abilities import (
    CHARGE,
    FEY,
    GLARE,
    IDS,
    MOUNT_FIELD,
    PREPARE,
    RECONCILE,
    RECORDS,
    RIDER_FIELD,
    TOUCH,
    bind_steed_exploration_abilities,
)
from tests.test_srd_lite_combat_scene_reentry import admission_source as _admission_source
from tests.test_srd_lite_mass_cure_geometry import _calls
from tests.test_srd_lite_steed_healing_touch import _case, _snapshot

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import _json_safe
from sylvia_gm.application.result_card_admission import validate_result_card_declaration
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.application.schema_admission import validate_json_schema
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._schema_validator import validate_parameter_declarations

native_owner_source = _admission_source


@pytest.fixture(scope="module")
def bound(native_owner_source):
    """Reuse the root's admitted owner load; do not build or recast any paid spell."""
    data, record = deepcopy(native_owner_source)
    original = deepcopy(data)
    changed = bind_steed_exploration_abilities(data, record)
    assert data["definitions"] == original["definitions"]
    assert not validate_parameter_declarations(data)
    for name, owner in original["tools"].items():
        if name not in (TOUCH, GLARE, FEY):
            assert data["tools"][name] == owner, name
    for name in (TOUCH, GLARE, FEY):
        validate_result_card_declaration(data["tools"][name]["metadata"]["result_card"])
        declaration = next(row for row in record["tools"] if row["name"] == name)
        validate_json_schema(declaration["input_schema"], label=name)
    native_fey = original["tools"][FEY]["effects"]
    start = next(
        i for i, row in enumerate(native_fey) if row.get("call") == "srd_information_scene_entity"
    )
    assert data["tools"][FEY]["effects"][-1]["on_true"][1:] == native_fey[start:]
    closure, pending = {}, [TOUCH, GLARE, FEY, RECONCILE, PREPARE, "process_srd_turn_effects"]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    parsed = RulesetRuntime(
        ruleset_dict={
            **{key: value for key, value in data.items() if key not in {"tools", "procedures"}},
            "tools": closure,
        }
    )
    return parsed, data, changed


@pytest.fixture(scope="module")
def admission(bound):
    data = deepcopy(bound[1])
    bind_combat_scene_reentry(data)
    effects = data["tools"][ADMIT]["effects"]
    prepare = next(i for i, row in enumerate(effects) if row.get("call") == PREPARE_STEED)
    saved = next(
        i for i, row in enumerate(effects) if row.get("set", {}).get("field") == "_reentry_saved"
    )
    assert prepare < saved
    closure, pending = {}, [ADMIT, FEY]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    parsed = RulesetRuntime(
        ruleset_dict={
            **{key: value for key, value in data.items() if key not in {"tools", "procedures"}},
            "tools": closure,
        }
    )
    return parsed, data, bound[2]


def _register_source(bridge):
    bridge.write_field("state", "created_actor_ids", ["steed"])
    bridge.write_field(
        "state",
        "created_actor_records",
        {
            "steed": {
                "individual": "steed",
                "spell": "find_steed",
                "caster": "caster",
                "active": True,
                "disappearance": "",
                "spell_level": 2,
                "source_page": 131,
            }
        },
    )


def _qualified_retirement(bridge):
    """Consume the already proved native retirement result, including its real source record."""
    bridge.write_field("steed", "created_active", False)
    bridge.write_field("steed", "is_alive", False)
    bridge.write_field("steed", "hp", 0)
    bridge.write_field("steed", RIDER_FIELD, None)
    bridge.write_field("caster", MOUNT_FIELD, None)
    bridge.write_field("caster", "created_steed_id", None)
    bridge.write_field("state", "created_actor_records.steed.active", False)
    bridge.write_field("state", "created_actor_records.steed.disappearance", "dismissed")


def _exploration_case(bound, kind="celestial", rider=False, combat=False, **changes):
    steed_changes = {
        "creature_type": kind,
        "size_rank": 4,
        RIDER_FIELD: "caster" if rider else "",
        **changes.pop("steed_changes", {}),
    }
    caster_changes = {
        "size_rank": 3,
        MOUNT_FIELD: "steed" if rider else "",
        "spellcasting_profile": {"ability": "charisma", "attack_bonus": 9, "save_dc": 17},
        **changes.pop("caster_changes", {}),
    }
    target_changes = {"saving_throw_modifiers": {"wisdom": 2}, **changes.pop("target_changes", {})}
    bridge, state, act = _case(
        bound,
        combat=combat,
        steed_changes=steed_changes,
        caster_changes=caster_changes,
        target_changes=target_changes,
        **changes,
    )
    if not combat:
        bridge.write_field("state", "spatial_scene", None)
    return bridge, state, act


def _card(bound, name, result):
    return project_result_card(
        bound[1]["tools"][name]["metadata"]["result_card"],
        parameters={},
        public_context=_json_safe(result.context),
    )


def _step(act, **changes):
    return act(
        FEY,
        **{
            "steed_state": "steed",
            "established_destination": "The empty roadside clearing",
            "established_distance": 60,
            "established_plane": "material",
            "established_same_plane": True,
            "established_space_unoccupied": True,
            "established_environment_source_ids": [],
            **changes,
        },
    )


def test_scene_free_healing_keeps_native_dice_hp_charge_and_equation(bound, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    bridge, _, act = _exploration_case(bound)
    result = act(
        TOUCH,
        steed_state="steed",
        target_state="target",
        established_target_distance=5,
        established_same_plane=True,
    )
    assert result.success, result.error
    assert result.context["_steed_touch_roll"].results == (7, 7)
    assert bridge.get_entity("target")["hp"] == 26
    assert bridge.get_entity("steed")[CHARGE] is False
    assert bridge.get_entity("steed")["bonus_action_available"] is True
    assert bridge.get_entity("state")["spatial_scene"] is None
    card = _card(bound, TOUCH, result)
    assert next(row["equation"] for row in card["summary"] if "equation" in row) == {
        "die_sides": 8,
        "rolls": [7, 7],
        "roll": 14,
        "modifier": 2,
        "total": 16,
    }
    assert (
        next(
            row["value"] for row in card["summary"] if row["label"] == "Established separation (ft)"
        )
        == 5
    )


def test_scene_free_glare_keeps_current_dc_native_source_and_truthful_duration(bound, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(2))
    bridge, _, act = _exploration_case(bound, "fiend")
    result = act(
        GLARE,
        steed_state="steed",
        target_state="target",
        established_target_distance=60,
        established_same_plane=True,
        established_target_visible=True,
    )
    assert result.success, result.error
    assert result.context["_steed_save_dc"] == 17
    assert result.context["_steed_glare_outcome"] == "failure"
    assert "frightened" in bridge.get_entity("target")["conditions"]
    assert bridge.get_entity("steed")[CHARGE] is False
    assert bridge.get_entity("steed")["bonus_action_available"] is True
    state = bridge.get_entity("state")
    source = state["timed_condition_sources"][state["steed_glare_source_ids"][0]]
    assert source["source_is_magical"] is False
    assert source["control_profile"] == "steed_fell_glare"
    assert source["native_body_source"] == "steed"
    assert source["native_body_source_sequence"] == source["sequence"]
    assert state["spatial_scene"] is None
    card = _card(bound, GLARE, result)
    assert next(row["equation"] for row in card["summary"] if "equation" in row) == {
        "die_sides": 20,
        "roll": 2,
        "modifier": 2,
        "total": 4,
    }
    assert "1 round outside combat" in card["details"][0]["value"]
    assert "summoner's next turn" not in card["details"][0]["value"]


def test_scene_free_fey_step_retains_real_rider_and_fiction_without_coordinates(bound):
    bridge, _, act = _exploration_case(bound, "fey", rider=True)
    bridge.write_field(
        "state",
        "spell_barrier_sources",
        {
            "distant-source": {"installed": True, "expires_tick": 100},
        },
    )
    bridge.write_field("state", "has_spell_barriers", True)
    before_rng = dice._rng.getstate()
    result = _step(act, destination=[9000, 9000, 9000])
    assert result.success, result.error
    state = bridge.get_entity("state")
    assert state["spatial_scene"] is None
    assert bridge.get_entity("steed")[CHARGE] is False
    assert bridge.get_entity("steed")["bonus_action_available"] is True
    assert bridge.get_entity("steed")[RIDER_FIELD] == "caster"
    assert bridge.get_entity("caster")[MOUNT_FIELD] == "steed"
    assert state[IDS] == ["steed"]
    assert state[RECORDS]["steed"] == {
        "steed": "steed",
        "caster": "caster",
        "rider": "caster",
        "steed_placement": "The empty roadside clearing",
        "rider_placement": "The empty roadside clearing",
        "plane": "material",
        "mounted": True,
        "pending": True,
        "tick": 0,
    }
    assert dice._rng.getstate() == before_rng
    card = _card(bound, FEY, result)
    assert next(row["value"] for row in card["summary"] if row["label"] == "Destination") == (
        "The empty roadside clearing"
    )
    assert next(row["value"] for row in card["details"] if row["label"] == "Rider") == (
        "The empty roadside clearing"
    )
    assert all("equation" not in row for row in card["summary"])


@pytest.mark.parametrize(
    "pending,steed_arrived,rider_arrived,success",
    [
        (False, False, False, True),
        (True, False, False, False),
        (True, True, True, True),
        (True, False, True, False),
        (True, True, False, False),
    ],
)
def test_current_native_arrival_supersedes_only_each_exact_pending_actor_plane(
    bound, pending, steed_arrived, rider_arrived, success
):
    bridge, _, act = _exploration_case(
        bound,
        "fey",
        rider=True,
        steed_changes={"travel_location": {"plane": "ethereal"}} if steed_arrived else {},
        caster_changes={"find_path_current_plane": "ethereal"} if rider_arrived else {},
    )
    bridge.write_field("state", IDS, ["steed", "other-retained-id"])
    bridge.write_field(
        "state",
        RECORDS,
        {
            "steed": {
                "steed": "steed",
                "caster": "caster",
                "rider": "caster",
                "steed_placement": "The former clearing",
                "rider_placement": "The former clearing",
                "plane": "material",
                "mounted": True,
                "pending": pending,
                "tick": 0,
            },
            "other-retained-id": {"pending": False, "plane": "material"},
        },
    )
    before, before_rng = _snapshot(bridge), dice._rng.getstate()
    result = _step(act, established_plane="ethereal")
    assert result.success is success, result.error
    assert dice._rng.getstate() == before_rng
    if success:
        state = bridge.get_entity("state")
        assert state[RECORDS]["steed"]["plane"] == "ethereal"
        assert state[RECORDS]["steed"]["pending"] is True
        assert state[IDS] == ["steed", "other-retained-id"]
        assert state[RECORDS]["other-retained-id"] == {"pending": False, "plane": "material"}
        assert bridge.get_entity("steed")[CHARGE] is False
    else:
        assert "this exact actor's genuine native arrival" in result.error
        assert _snapshot(bridge) == before


@pytest.mark.parametrize(
    "expires,error",
    [
        (100, "cannot yet resolve this relevant environmental"),
        (0, "exact current active source ID"),
    ],
)
def test_declared_relevant_source_rejects_before_charge_rng_or_pending_record(
    bound, expires, error
):
    bridge, _, act = _exploration_case(bound, "fey")
    bridge.write_field(
        "state",
        "spell_barrier_sources",
        {
            "actual-source": {"installed": True, "expires_tick": expires},
        },
    )
    before, before_rng = _snapshot(bridge), dice._rng.getstate()
    result = _step(act, established_environment_source_ids=["actual-source"])
    assert not result.success and error in result.error
    assert _snapshot(bridge) == before and dice._rng.getstate() == before_rng


@pytest.mark.parametrize(
    "name,kind,facts,error",
    [
        (TOUCH, "celestial", {}, "current same-plane separation"),
        (
            TOUCH,
            "celestial",
            {"established_target_distance": 5.01, "established_same_plane": True},
            "within 5 feet",
        ),
        (
            GLARE,
            "fiend",
            {"established_target_distance": 60, "established_same_plane": True},
            "currently see",
        ),
        (
            FEY,
            "fey",
            {
                "established_destination": "The clearing",
                "established_distance": 60,
                "established_plane": "material",
                "established_same_plane": True,
                "established_space_unoccupied": True,
            },
            "Explicitly list",
        ),
    ],
)
def test_missing_or_impossible_current_facts_reject_before_rng_charge_and_state(
    bound, name, kind, facts, error
):
    bridge, _, act = _exploration_case(bound, kind)
    before, before_rng = _snapshot(bridge), dice._rng.getstate()
    params = {"steed_state": "steed", **facts}
    if name != FEY:
        params["target_state"] = "target"
    result = act(name, **params)
    assert not result.success and error in result.error
    assert _snapshot(bridge) == before
    assert dice._rng.getstate() == before_rng


def test_combat_fiction_facts_cannot_override_actual_range(bound):
    bridge, _, act = _exploration_case(bound, combat=True, position=(80, 0, 0))
    before, before_rng = _snapshot(bridge), dice._rng.getstate()
    result = act(
        TOUCH,
        steed_state="steed",
        target_state="target",
        established_target_distance=0,
        established_same_plane=True,
    )
    assert not result.success and "actually within 5 feet" in result.error
    assert _snapshot(bridge) == before and dice._rng.getstate() == before_rng


def test_pending_fey_pair_admission_preserves_mechanic_metadata_and_advances_revision(bound):
    bridge, _, act = _case(
        bound,
        steed_changes={"creature_type": "fey", "size_rank": 4, RIDER_FIELD: "caster"},
        caster_changes={"size_rank": 3, MOUNT_FIELD: "steed"},
    )
    saved_scene = deepcopy(bridge.get_entity("state")["spatial_scene"])
    steed_body = saved_scene["entities"][1]
    steed_body["metadata"].update(
        source_ref="find_steed",
        source_caster="caster",
        creature_type="fey",
        retained_mechanic="Keep this exact value",
    )
    bridge.write_field("state", "spatial_scene", None)
    result = _step(act)
    assert result.success, result.error
    bridge.write_field("state", "spatial_scene", saved_scene)
    assert act("srd_information_scene_entity", subject="steed").success
    incoming = deepcopy(saved_scene)
    incoming["entities"][0]["position"] = [40, 20, steed_body["height"]]
    incoming["entities"][1]["position"] = [40, 20, 0]
    incoming["entities"][1]["metadata"]["retained_mechanic"] = "Forged incoming reset"
    result = act(RECONCILE, lasting_state="state", scenes=[incoming])
    assert result.success, result.error
    state = bridge.get_entity("state")
    assert state[RECORDS]["steed"]["pending"] is False
    assert state["spatial_scene"]["map"]["revision"] == saved_scene["map"]["revision"] + 1
    assert state["spatial_scene"]["entities"][0]["position"] == [40, 20, steed_body["height"]]
    assert state["spatial_scene"]["entities"][1]["position"] == [40, 20, 0]
    assert (
        state["spatial_scene"]["entities"][1]["metadata"]["retained_mechanic"]
        == "Keep this exact value"
    )
    assert state["spatial_scene"]["entities"][2] == saved_scene["entities"][2]
    assert bridge.get_entity("steed")[CHARGE] is False


@pytest.mark.parametrize("rider", [False, True])
def test_retired_pending_steed_does_not_require_or_resurrect_its_body(bound, rider):
    """Admission consumes the already qualified retirement state; it does not recast or retire."""
    bridge, _, act = _case(
        bound,
        steed_changes={
            "creature_type": "fey",
            "size_rank": 4,
            RIDER_FIELD: "caster" if rider else "",
        },
        caster_changes={"size_rank": 3, MOUNT_FIELD: "steed" if rider else ""},
    )
    _register_source(bridge)
    saved_scene = deepcopy(bridge.get_entity("state")["spatial_scene"])
    bridge.write_field("state", "spatial_scene", None)
    assert _step(act).success
    _qualified_retirement(bridge)
    incoming = deepcopy(saved_scene)
    incoming["entities"] = [row for row in incoming["entities"] if row["id"] != "steed"]
    incoming["entities"][0]["position"] = [40, 20, 0]
    current = deepcopy(incoming)
    current["entities"][0]["position"] = [-10, 0, 0]
    bridge.write_field("state", "spatial_scene", current)
    result = act(RECONCILE, lasting_state="state", scenes=[incoming] if rider else [])
    assert result.success, result.error
    state = bridge.get_entity("state")
    assert state[RECORDS]["steed"]["pending"] is False
    assert all(row["id"] != "steed" for row in state["spatial_scene"]["entities"])
    assert bridge.get_entity("steed")["created_active"] is False
    assert bridge.get_entity("steed")[CHARGE] is False
    assert bridge.get_entity("caster")[MOUNT_FIELD] is None
    assert state["spatial_scene"]["entities"][0]["position"] == (
        [40, 20, 0] if rider else [-10, 0, 0]
    )


def test_retired_pending_steed_clears_exact_cached_proxy_collision_without_placement(bound):
    bridge, _, act = _case(
        bound,
        steed_changes={"creature_type": "fey", "size_rank": 4, RIDER_FIELD: ""},
        caster_changes={"size_rank": 3, MOUNT_FIELD: ""},
    )
    _register_source(bridge)
    cached = deepcopy(bridge.get_entity("state")["spatial_scene"])
    old_body = cached["entities"][1]
    old_body["metadata"].update(
        source_ref="find_steed",
        source_caster="caster",
        creature_type="fey",
        retained_mechanic="The saved source's exact metadata",
    )
    bridge.write_field("state", "spatial_scene", None)
    assert _step(act).success
    _qualified_retirement(bridge)
    bridge.write_field("state", "spatial_scene", cached)
    actors = {name: deepcopy(bridge.get_entity(name)) for name in ("caster", "steed", "target")}
    before_rng = dice._rng.getstate()
    query = {
        "subject": "target",
        "destination": [0, 0, 0],
        "plane": "material",
        "excluded": [],
    }
    before = act("resolve_srd_travel_placement", **query)
    assert before.success, before.error
    assert before.context["_travel_placement"]["distance"] > 0
    result = act(RECONCILE, lasting_state="state", scenes=[])
    assert result.success, result.error
    state = bridge.get_entity("state")
    assert state[RECORDS]["steed"]["pending"] is False
    assert state["spatial_scene"]["map"]["revision"] == cached["map"]["revision"] + 1
    assert state["spatial_scene"]["entities"][1] == {**old_body, "active": False}
    assert state["spatial_scene"]["entities"][0] == cached["entities"][0]
    assert state["spatial_scene"]["entities"][2] == cached["entities"][2]
    after = act("resolve_srd_travel_placement", **query)
    assert after.success, after.error
    assert after.context["_travel_placement"]["distance"] == 0
    assert {name: bridge.get_entity(name) for name in actors} == actors
    assert dice._rng.getstate() == before_rng


def test_cross_map_admission_omits_retired_proxy_and_admits_only_released_rider(admission):
    bridge, _, act = _case(
        admission,
        steed_changes={"creature_type": "fey", "size_rank": 4, RIDER_FIELD: "caster"},
        caster_changes={"size_rank": 3, MOUNT_FIELD: "steed"},
    )
    _register_source(bridge)
    cached = deepcopy(bridge.get_entity("state")["spatial_scene"])
    cached["entities"][1]["metadata"].update(
        source_ref="find_steed",
        source_caster="caster",
        creature_type="fey",
        retained_mechanic="Preserve the genuine retired source",
    )
    bridge.write_field("state", "spatial_scene", None)
    assert _step(act).success
    # The qualified native dismount/retirement leaves this actual released rider pending.
    bridge.write_field("state", RECORDS + ".steed.mounted", False)
    bridge.write_field("state", RECORDS + ".steed.rider_placement", "The empty ground beside it")
    _qualified_retirement(bridge)
    bridge.write_field("state", "spatial_scene", cached)
    incoming = deepcopy(cached)
    incoming["map"]["id"] = "new-arena-without-retired-steed"
    incoming["entities"] = [row for row in incoming["entities"] if row["id"] != "steed"]
    for row in incoming["entities"]:
        row["map_id"] = incoming["map"]["id"]
    incoming["entities"][0]["position"] = [40, 20, 0]
    incoming["entities"][1]["position"] = [80, 0, 0]
    actors = {name: deepcopy(bridge.get_entity(name)) for name in ("caster", "steed", "target")}
    source = deepcopy(bridge.get_entity("state")["created_actor_records"])
    before_rng = dice._rng.getstate()
    result = act(ADMIT, scenes=[incoming], actors=["caster", "target"], lasting_state="state")
    assert result.success, result.error
    state = bridge.get_entity("state")
    assert state["spatial_scene"]["map"]["id"] == incoming["map"]["id"]
    assert state["spatial_scene"]["map"]["revision"] == cached["map"]["revision"] + 3
    assert all(row["id"] != "steed" for row in state["spatial_scene"]["entities"])
    assert state["spatial_scene"]["entities"][0]["position"] == [40, 20, 0]
    assert state["spatial_scene"]["entities"][1]["position"] == [80, 0, 0]
    assert state["spatial_scene"]["entities"][1]["metadata"] == cached["entities"][2]["metadata"]
    assert state[RECORDS]["steed"]["rider"] == "caster"
    assert state[RECORDS]["steed"]["pending"] is False
    assert state["created_actor_records"] == source
    assert {name: bridge.get_entity(name) for name in actors} == actors
    assert dice._rng.getstate() == before_rng


@pytest.mark.parametrize(
    "tamper,tool,error",
    [
        ("individual", PREPARE, "exact inactive Find Steed source"),
        ("active", RECONCILE, "exact inactive Find Steed source"),
        ("body_caster", PREPARE, "exact Steed's genuine cached body"),
    ],
)
def test_retired_proxy_rejects_native_source_or_body_identity_tampering(bound, tamper, tool, error):
    bridge, _, act = _case(
        bound,
        steed_changes={"creature_type": "fey", "size_rank": 4, RIDER_FIELD: ""},
        caster_changes={"size_rank": 3, MOUNT_FIELD: ""},
    )
    _register_source(bridge)
    cached = deepcopy(bridge.get_entity("state")["spatial_scene"])
    cached["entities"][1]["metadata"].update(
        source_ref="find_steed",
        source_caster="caster",
        creature_type="fey",
    )
    bridge.write_field("state", "spatial_scene", None)
    assert _step(act).success
    _qualified_retirement(bridge)
    if tamper == "body_caster":
        cached["entities"][1]["metadata"]["source_caster"] = "target"
    else:
        bridge.write_field(
            "state",
            "created_actor_records.steed." + tamper,
            True if tamper == "active" else "other-steed",
        )
    bridge.write_field("state", "spatial_scene", cached)
    before, before_rng = _snapshot(bridge), dice._rng.getstate()
    result = act(tool, lasting_state="state", **({"scenes": []} if tool == RECONCILE else {}))
    assert not result.success and error in result.error
    assert _snapshot(bridge) == before
    assert dice._rng.getstate() == before_rng


def test_new_effects_obey_runtime_expression_literal_and_foreach_limits(bound):
    def check(value):
        if isinstance(value, str):
            assert len(value) <= 500, value
            assert all(len(literal) <= 200 for literal in re.findall(r"'([^']*)'", value)), value
        elif isinstance(value, list):
            for child in value:
                check(child)
        elif isinstance(value, dict):
            if "for_each" in value:
                assert re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value["for_each"]["collection"])
            for child in value.values():
                check(child)

    for name in bound[2]:
        check(bound[1]["tools"][name]["effects"])
