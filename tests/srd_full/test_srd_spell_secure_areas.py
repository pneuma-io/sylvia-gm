"""Exact secure volumes govern actual movement, attacks, information and later source clocks."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_secure_areas import PROFILES, bind_runtime_consumers, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_environment import _ground
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

ROOT = Path(__file__).resolve().parents[2]


def test_secure_area_schema_does_not_require_magic_zones_to_have_been_emitted():
    original = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text(encoding="utf-8")
    runtime = _load_runtime(original)
    variants = runtime["definition_row_schemas"]["definitions.spells"]["variants"]
    # Secure choices must exist before extended casting captures their parameters.
    if "spell_magic_zone" in variants:
        import re

        original = re.sub(
            r"(?ms)^      spell_magic_zone:\n.*?(?=^      [a-z]|^  [a-z])",
            "",
            original,
            count=1,
        )
    extended = extend_runtime(original)
    assert "spell_secure_area" in _load_runtime(extended)["definition_row_schemas"][
        "definitions.spells"
    ]["variants"]
    assert extend_runtime(extended) == extended


@lru_cache
def _runtime():
    original = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text(encoding="utf-8")
    first = bind_runtime_consumers(extend_runtime(original))
    assert bind_runtime_consumers(extend_runtime(first)) == first
    runtime = _load_runtime(first)
    runtime["definitions"]["spells"].update(deepcopy(PROFILES))
    changed = {}
    for name, tool in runtime["tools"].items():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | PROFILES.keys())
                changed[name] = tool
    # Reuse the admitted package and add only these exact new family definitions.
    first = _replace_tools(first, changed)
    start_marker = "    # BEGIN GENERATED SPELL DEFINITIONS\n"
    end_marker = "    # END GENERATED SPELL DEFINITIONS"
    start, end = first.index(start_marker) + len(start_marker), first.index(end_marker)
    rows = "".join(
        f"    {name}: {json.dumps(value)}\n"
        for name, value in sorted(runtime["definitions"]["spells"].items())
    )
    first = first[:start] + rows + first[end:]
    return first.encode(), _load_runtime(first)


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="a" * 64,
        runtime_bytes=raw,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spell-casting",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _actual_owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)


def _case():
    state, data, parameters = _spell_case("chromatic_orb")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.state"]["lasting_tick"] = 0
    _admit_scene(state)
    _ground(state)
    state[f"{SRD}.entities"]["hero"]["creature_type"] = "humanoid"
    state[f"{SRD}.entities"]["guard"].update(creature_type="fiend", hp=200, max_hp=200)
    state[f"{SRD}.entities"]["guard"]["flying_speed"] = 30
    return state, data, parameters


def _body(state, actor):
    return next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == actor)


def _cast(state, spell, **choices):
    parameters = {
        "caster_state": "hero",
        "spell": spell,
        "slot_level": PROFILES[spell]["base_level"],
        "save_dc": 18,
        "origin": [0, 0, 0],
        "caster_can_see_origin": True,
        "caster_can_touch_origin": True,
    }
    if spell != "private_sanctum":
        parameters["chosen_types"] = ["fiend"]
    parameters.update(choices)
    result = _run("cast_srd_spell_secure_area", state, **parameters)
    source_id = result.result["context"]["_new_secure_source_id"]
    return source_id, state[f"{SRD}.state"]["spell_secure_area_sources"][source_id]


def _reject(state, tool, **parameters):
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(tool, state, **parameters)
    assert state == before


def test_circle_real_movement_reverse_height_plane_and_expiry():
    state, _, _ = _case()
    source_id, source = _cast(state, "magic_circle", slot_level=4)
    assert source["expires_tick"] == 2400
    _reject(state, "move_srd_scene_creature", actor_state="guard", destination=[5, 0, 0])
    assert _body(state, "guard")["position"] == [20, 0, 0]
    _body(state, "guard")["position"] = [20, 0, 21]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[5, 0, 21], mode="fly")
    _run("end_srd_secure_area_source", state, source_id=source_id)
    _body(state, "guard")["position"] = [0, 0, 0]
    reversed_id, _ = _cast(state, "magic_circle", reverse=True)
    _reject(state, "move_srd_scene_creature", actor_state="guard", destination=[20, 0, 0])
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert state[f"{SRD}.state"]["spell_secure_area_sources"][reversed_id]["installed"] is False
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[20, 0, 0])


def test_circle_segment_retains_numeric_closest_point():
    state, _, _ = _case()
    source_id, _ = _cast(state, "magic_circle")
    result = _run(
        "qualify_srd_secure_area_segment",
        state,
        source_id=source_id,
        start=[20, 0, 21],
        end=[5, 0, 21],
        plane="material",
    )
    assert result.result["context"]["_secure_area_segment_intersects"] is False


def test_circle_actual_target_aware_attack_and_causing_creature_immunity():
    state, _, _ = _case()
    _cast(state, "magic_circle")
    result = _run(
        "resolve_srd_attack_conditions",
        state,
        actor_state="guard",
        target_state="hero",
        attack_distance=20,
        actor_can_see_target=True,
        target_can_see_actor=True,
        fear_source_in_sight=False,
    )
    assert result.result["context"]["_condition_disadvantage"] is True
    result = _run(
        "check_srd_target_condition_immunity",
        state,
        target_state="hero",
        condition="charmed",
        source_state_id="guard",
    )
    assert result.result["context"]["_source_condition_immune"] is True
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "humanoid"
    result = _run(
        "check_srd_target_condition_immunity",
        state,
        target_state="hero",
        condition="charmed",
        source_state_id="guard",
    )
    assert result.result["context"]["_source_condition_immune"] is False


def test_circle_magic_query_requires_actual_save_but_does_not_move_or_spend():
    state, _, _ = _case()
    source_id, source = _cast(state, "magic_circle")
    before = deepcopy(state)
    result = _run(
        "check_srd_secure_area_movement",
        state,
        subject="guard",
        destination=[5, 0, 0],
        plane="material",
        teleport=True,
    )
    context = result.result["context"]
    facts = {
        k: context.get(k)
        for k in (
            "_secure_ids",
            "_secure_area_effect_active",
            "_secure_area_lifetime_active",
            "_secure_origin_inside",
            "_secure_destination_inside",
            "_secure_creature_type",
            "_secure_circle_crossing",
        )
    }
    facts["source"] = source
    facts["flag"] = state[f"{SRD}.state"].get("has_spell_secure_areas")
    assert context["_secure_area_magic_save_source_ids"] == [source_id], facts
    assert result.result["context"]["_secure_area_movement_allowed"] is True
    assert state == before
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["charisma"] = -100
    result = _run(
        "resolve_srd_magic_circle_transit", state, actor_state="guard", source_id=source_id
    )
    assert result.result["context"]["_secure_circle_transit_allowed"] is False
    save = result.result["context"]["_secure_circle_save"]
    assert len(save["roll"]["results"]) == 1
    assert 1 <= save["roll"]["results"][0] <= 20
    assert save["outcome"] == "failure"
    assert _body(state, "guard")["position"] == [20, 0, 0]


def test_sanctum_actual_vision_edge_inside_and_divination_school():
    state, _, _ = _case()
    _body(state, "hero")["position"] = [0, 0, 1]
    _body(state, "guard")["position"] = [20, 0, 1]
    _cast(state, "private_sanctum", side=10, security=["vision", "sound", "divination_targets"])
    result = _run(
        "resolve_srd_illumination_target_sight", state, observer_state="guard", target_state="hero"
    )
    assert result.result["context"]["_illumination_target_can_see"] is False
    state[f"{SRD}.entities"]["guard"]["blindsight_feet"] = 30
    result = _run(
        "resolve_srd_illumination_target_sight", state, observer_state="guard", target_state="hero"
    )
    assert result.result["context"]["_illumination_target_can_see"] is True
    state[f"{SRD}.entities"]["guard"]["blindsight_feet"] = 0
    _body(state, "guard")["position"] = [5, 0, 1]
    result = _run(
        "resolve_srd_illumination_target_sight", state, observer_state="guard", target_state="hero"
    )
    assert result.result["context"]["_illumination_target_can_see"] is True
    query = {"start": [-10, 5, 1], "end": [20, 5, 1], "plane": "material"}
    assert (
        _run("check_srd_secure_area_information", state, **query, query_kind="sound").result[
            "context"
        ]["_secure_area_information_allowed"]
        is False
    )
    for spell, expected in [("detect_thoughts", False), ("chromatic_orb", True)]:
        result = _run(
            "check_srd_secure_area_information",
            state,
            **query,
            query_kind="divination_target",
            subject="hero",
            spell=spell,
        )
        assert result.result["context"]["_secure_area_information_allowed"] is expected


def test_forbiddance_overlap_remains_while_suppressed_and_keeps_source_lifetime():
    state, _, _ = _case()
    source_id, source = _cast(state, "forbiddance", floor_width=20, floor_length=20)
    source["suppressed"] = True
    _reject(
        state,
        "cast_srd_spell_secure_area",
        caster_state="hero",
        spell="forbiddance",
        slot_level=6,
        save_dc=18,
        origin=[0, 0, 0],
        chosen_types=["fiend"],
        floor_width=10,
        floor_length=10,
        caster_can_touch_origin=True,
    )
    assert source["installed"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert state[f"{SRD}.state"]["spell_secure_area_sources"][source_id]["installed"] is False
    assert source_id not in state[f"{SRD}.state"]["spell_secure_area_source_ids"]


def test_forbiddance_password_admission_survives_turn_end_and_actual_exit_retires_it():
    state, _, _ = _case()
    _body(state, "guard")["position"] = [-10, 5, 0]
    source_id, _ = _cast(state, "forbiddance", floor_width=20, floor_length=20, password="friend")
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _run(
        "move_srd_creature_speaking_password",
        state,
        actor_state="guard",
        destination=[5, 5, 0],
        password="friend",
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    assert state[f"{SRD}.entities"]["guard"]["pending_secure_area_speech"] is None
    result = _run("resolve_srd_forbiddance_event", state, target_state="guard", event="end")
    assert not result.result["context"]["_secure_forbiddance_packets"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[-5, 5, 0])
    assert not state[f"{SRD}.state"]["spell_secure_area_sources"][source_id]["password_admissions"][
        "guard"
    ]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[5, 5, 0])
    after_entry = state[f"{SRD}.entities"]["guard"]["hp"]
    assert after_entry < hp
    result = _run("resolve_srd_forbiddance_event", state, target_state="guard", event="end")
    assert len(result.result["context"]["_secure_forbiddance_packets"]) == 1
    assert state[f"{SRD}.entities"]["guard"]["hp"] < after_entry
    after = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("resolve_srd_forbiddance_event", state, target_state="guard", event="end")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == after
    assert state[f"{SRD}.state"]["spell_secure_area_sources"][source_id]["installed"] is True


def test_sanctum_and_forbiddance_transport_queries_use_actual_planned_plane():
    state, _, _ = _case()
    _cast(state, "private_sanctum", side=10, security=["teleport", "planar"])
    for destination, plane in [([30, 0, 0], "material"), ([0, 0, 0], "ethereal")]:
        result = _run(
            "check_srd_secure_area_movement",
            state,
            subject="hero",
            destination=destination,
            plane=plane,
            teleport=True,
        )
        assert result.result["context"]["_secure_area_movement_allowed"] is False
    _body(state, "hero")["position"] = [-20, 0, 0]
    result = _run(
        "check_srd_secure_area_movement",
        state,
        subject="hero",
        destination=[-30, 0, 0],
        plane="material",
        teleport=True,
    )
    assert result.result["context"]["_secure_area_movement_allowed"] is True


def test_forbiddance_actual_combat_end_uses_active_actor_and_saved_turn_receipt():
    state, _, _ = _case()
    _body(state, "guard")["position"] = [5, 5, 0]
    source_id, _ = _cast(state, "forbiddance", floor_width=20, floor_length=20)
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=True, turn_active=False, current_actor="guard")
    _run("begin_combat_turn", state, actor="guard")
    before = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("end_combat_turn", state, actor="guard")
    assert state[f"{SRD}.entities"]["guard"]["hp"] < before
    source = state[f"{SRD}.state"]["spell_secure_area_sources"][source_id]
    assert source["end_damage_turns"]["guard"]["clock"] == "combat"
    assert source["end_damage_turns"]["guard"]["turn"] == state[f"{SRD}.combat"]["turn_sequence"]
    _reject(state, "end_combat_turn", actor="guard")


def test_actual_gaseous_body_cannot_speak_forbiddance_password():
    state, _, _ = _case()
    _body(state, "guard")["position"] = [-5, 5, 0]
    source_id, _ = _cast(state, "forbiddance", floor_width=20, floor_length=20, password="friend")
    source_case, data, parameters = _spell_case("gaseous_form")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"] = ["gaseous_form"]
    hero["spell_materials"] = deepcopy(source_case[f"{SRD}.entities"]["hero"]["spell_materials"])
    hero["inventory"] += [item["inventory_item"] for item in hero["spell_materials"]]
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **parameters,
    )
    assert state[f"{SRD}.entities"]["guard"]["form_can_speak"] is False
    _reject(
        state,
        "move_srd_creature_speaking_password",
        actor_state="guard",
        destination=[0, 5, 0],
        mode="fly",
        password="friend",
    )
    before = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[0, 5, 0], mode="fly")
    assert state[f"{SRD}.entities"]["guard"]["hp"] < before
    assert not state[f"{SRD}.state"]["spell_secure_area_sources"][source_id]["password_admissions"][
        "guard"
    ]


def test_forbiddance_consecutive_actual_sources_derive_only_final_material_obligation():
    state, _, _ = _case()
    choices = {
        "caster_state": "hero",
        "spell": "forbiddance",
        "slot_level": 6,
        "save_dc": 18,
        "origin": [0, 0, 0],
        "chosen_types": ["fiend"],
        "floor_width": 20,
        "floor_length": 20,
        "caster_can_touch_origin": True,
    }
    previous_id = None
    for day in range(30):
        if day:
            _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
            assert not state[f"{SRD}.state"]["spell_secure_area_sources"][previous_id]["installed"]
        prepared = _run("prepare_srd_secure_area_cast", state, **choices).result["context"]
        assert prepared["_secure_daily_count"] == day + 1
        assert prepared["_secure_materials_consumed"] is (day == 29)
        source_id, source = _cast(state, "forbiddance", floor_width=20, floor_length=20)
        assert source["daily_count"] == day + 1
        assert source["permanent"] is (day == 29)
        previous_id = source_id
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert state[f"{SRD}.state"]["spell_secure_area_sources"][source_id]["installed"]
    _run("end_srd_secure_area_source", state, source_id=source_id, reason="dispelled")
    assert not state[f"{SRD}.state"]["spell_secure_area_sources"][source_id]["installed"]


def test_daily_history_requires_exact_source_and_skipped_day_restarts():
    state, _, _ = _case()
    _, source = _cast(state, "private_sanctum", side=10, security=["vision"])
    _, same_day = _cast(state, "private_sanctum", side=10, security=["sound"])
    assert source["daily_count"] == same_day["daily_count"] == 1
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    _, next_day = _cast(state, "private_sanctum", side=10, security=["vision"])
    assert next_day["daily_count"] == 2
    _run("advance_srd_lasting_time", state, elapsed_rounds=28800)
    _, skipped = _cast(state, "private_sanctum", side=10, security=["vision"])
    assert skipped["daily_count"] == 1
    state[f"{SRD}.state"]["secure_area_daily_history"][0]["days"] = 364
    _reject(
        state,
        "cast_srd_spell_secure_area",
        caster_state="hero",
        spell="private_sanctum",
        slot_level=4,
        save_dc=18,
        origin=[0, 0, 0],
        side=10,
        security=["vision"],
    )


def test_public_paid_circle_extended_cast_reopens_and_retry_preserves_costs(monkeypatch, tmp_path):
    from dataclasses import replace

    import tests.srd_full.test_srd_public_spell_casting as public
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from sylvia_gm.domain.errors import MechanicsRejected
    from tests.srd_full.test_srd_extended_casting import _begin

    state, data, parameters = _spell_case("magic_circle")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    _ground(state)
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "fiend"
    parameters.update(secure_area_origin=[0, 0, 0], secure_area_types=["fiend"])
    materials = deepcopy(state[f"{SRD}.entities"]["hero"]["spell_materials"])
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, parameters))
    database = tmp_path / "circle.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "magic_circle")
        mechanics = StatelessMcpMechanics(owner)
        command = ActionCommand(
            campaign_id="casting",
            action_id="begin-circle",
            idempotency_key="begin-circle",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert mechanics.execute_action(command) == receipt
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3
        assert saved.state[f"{SRD}.entities"]["hero"]["spell_materials"] == materials
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        continuation = replace(
            command,
            action_id="continue-circle",
            idempotency_key="continue-circle",
            expected_revision=saved.revision,
            tool_name="continue_srd_spell_cast",
            parameters={
                "caster_state": "hero",
                "elapsed_rounds": 10,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        receipt = mechanics.execute_action(continuation)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert mechanics.execute_action(continuation) == receipt
        ready = owner.snapshot("casting")
        finish = replace(
            command,
            action_id="finish-circle",
            idempotency_key="finish-circle",
            expected_revision=ready.revision,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = mechanics.execute_action(finish)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert mechanics.execute_action(finish) == receipt
        finished = owner.snapshot("casting")
        assert owner.snapshot("casting").state == finished.state
        ((source_id, source),) = finished.state[f"{SRD}.state"]["spell_secure_area_sources"].items()
        assert source["spell_id"] == "magic_circle" and source["installed"]
        assert source["started_tick"] == 20 and source["expires_tick"] == 1220
        hero = finished.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["spell_slot_3"] == 2
        assert all(
            after["quantity"] < before["quantity"]
            for before, after in zip(materials, hero["spell_materials"], strict=True)
        )
        assert hero.get("extended_casting") is None
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == finished.state
        mechanics = StatelessMcpMechanics(owner)
        move = replace(
            command,
            action_id="enter-circle",
            idempotency_key="enter-circle",
            expected_revision=finished.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [5, 0, 0]},
        )
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(move)
        assert owner.snapshot("casting").state == finished.state
        time = replace(
            move,
            action_id="expire-circle",
            idempotency_key="expire-circle",
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 600},
        )
        assert mechanics.execute_action(time).status is ActionStatus.COMMITTED
        assert not owner.snapshot("casting").state[f"{SRD}.state"]["spell_secure_area_sources"][
            source_id
        ]["installed"]
        move = replace(move, expected_revision=owner.snapshot("casting").revision)
        receipt = mechanics.execute_action(move)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert mechanics.execute_action(move) == receipt
        after = owner.snapshot("casting")
        assert _body(after.state, "guard")["position"] == [5, 0, 0]
