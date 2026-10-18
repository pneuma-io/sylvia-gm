"""Hallow's real permanent source governs actual ward entry and later selected extras."""

import json
from copy import deepcopy
from functools import lru_cache

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_extended_casting import extend_runtime as extend_casting
from scripts.srd_full.srd_reaction_spells import (
    finalize_pending_spell_guards,
    normalize_pending_spell_guards,
)
from scripts.srd_full.srd_spell_hallow import (
    PROFILES,
    bind_runtime_consumers,
    extend_runtime,
    validate_profiles,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_secure_area_materials as materials
from tests import test_srd_spell_result_cards as result_cards
from tests import test_srd_spell_secure_areas as secure
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case


@lru_cache
def _runtime():
    original = materials._runtime()[0].decode()
    first = extend_runtime(normalize_pending_spell_guards(original))
    runtime = _load_runtime(first)
    runtime["definitions"]["spells"].update(deepcopy(PROFILES))
    changes = {}
    for name, tool in runtime["tools"].items():
        for p in tool["parameters"]:
            if p["name"] == "spell" and "enum_values" in p:
                p["enum_values"] = sorted(set(p["enum_values"]) | PROFILES.keys())
                changes[name] = tool
    first = _replace_tools(first, changes)
    begin = "    # BEGIN GENERATED SPELL DEFINITIONS\n"
    end = "    # END GENERATED SPELL DEFINITIONS"
    start, finish = first.index(begin) + len(begin), first.index(end)
    rows = "".join(
        f"    {name}: {json.dumps(value)}\n"
        for name, value in sorted(runtime["definitions"]["spells"].items())
    )
    first = first[:start] + rows + first[finish:]
    first = extend_casting(first, spell_ids=sorted(runtime["definitions"]["spells"]))
    first = finalize_pending_spell_guards(refresh_exploration_runtime(first))
    first = bind_runtime_consumers(first)
    assert bind_runtime_consumers(first) == first
    validate_profiles(PROFILES)
    return first.encode(), _load_runtime(first)


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="b" * 64,
        runtime_bytes=raw,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        result_cards, "_spell_definitions", lambda: _runtime()[1]["definitions"]["spells"]
    )


def _case():
    state, data, parameters = _spell_case("hallow")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    secure._admit_scene(state)
    state[f"{SRD}.entities"]["hero"]["creature_type"] = "humanoid"
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "fiend"
    return state, data, parameters


def _cast(state, **choices):
    result = _run(
        "cast_srd_hallow",
        state,
        caster_state="hero",
        slot_level=5,
        origin=[0, 0, 0],
        radius=10,
        caster_can_touch_origin=True,
        **choices,
    )
    sid = result.result["context"]["_new_hallow_source_id"]
    return sid, result.result["context"]


def test_public_paid_day_cast_reopens_and_retry_preserves_one_permanent_ward(monkeypatch, tmp_path):
    from dataclasses import replace

    import tests.srd_full.test_srd_public_spell_casting as public
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
    from sylvia_gm.application.package_materialization import NormalizedPackageDraft
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests.srd_full.test_srd_extended_casting import _begin

    state, data, parameters = _case()
    parameters.update(
        hallow_origin=[0, 0, 0],
        hallow_radius=10,
        hallow_extra="courage",
        caster_can_touch_target=True,
        selected_damage_type="radiant",
    )
    supplies = deepcopy(state[f"{SRD}.entities"]["hero"]["spell_materials"])
    raw, runtime = _runtime()
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    record["tools"] = [d for d in record["tools"] if d["name"] in runtime["tools"]]
    for declaration in record["tools"]:
        declaration["input_schema"] = _tool_schema(
            runtime["tools"][declaration["name"]], tool_name=declaration["name"]
        )
    payload = dict(primary.payloads)
    payload["runtime/ruleset.yaml"] = raw
    content_path = record["content_declarations"]
    content = json.loads(payload[content_path])
    declared = {d["name"] for d in record["tools"]}
    for inclusion in content["inclusions"]:
        inclusion["links"]["tools"] = [n for n in inclusion["links"]["tools"] if n in declared]
    payload[content_path] = canonical_json_bytes(content)
    archive = _archive((NormalizedPackageDraft(record, payload, 4), reference))
    monkeypatch.setattr(public, "bundled_srd_archive", lambda: archive)
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, parameters))
    database = tmp_path / "hallow.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "hallow")
        mechanics = StatelessMcpMechanics(owner)
        begin = ActionCommand(
            campaign_id="casting",
            action_id="begin-hallow",
            idempotency_key="begin-hallow",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )
        receipt = mechanics.execute_action(begin)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert mechanics.execute_action(begin) == receipt
        started = owner.snapshot("casting")
        hero = started.state[f"{SRD}.entities"]["hero"]
        assert hero["spell_materials"] == supplies and hero["resources"]["spell_slot_5"] == 3
        assert not started.state[f"{SRD}.state"].get("has_hallow_sources")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == started.state
        mechanics = StatelessMcpMechanics(owner)
        continuing = replace(
            begin,
            action_id="continue-hallow",
            idempotency_key="continue-hallow",
            expected_revision=started.revision,
            tool_name="continue_srd_spell_cast",
            parameters={
                "caster_state": "hero",
                "elapsed_rounds": 14400,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        receipt = mechanics.execute_action(continuing)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert mechanics.execute_action(continuing) == receipt
        ready = owner.snapshot("casting")
        assert ready.state[f"{SRD}.entities"]["hero"]["spell_materials"] == supplies
        finish = replace(
            begin,
            action_id="finish-hallow",
            idempotency_key="finish-hallow",
            expected_revision=ready.revision,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = mechanics.execute_action(finish)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert mechanics.execute_action(finish) == receipt
        finished = owner.snapshot("casting")
        hero = finished.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["spell_slot_5"] == 2
        assert all(
            a["quantity"] == b["quantity"] - 1
            for a, b in zip(hero["spell_materials"], supplies, strict=True)
        )
        sources = finished.state[f"{SRD}.state"]["spell_hallow_sources"]
        assert len(sources) == 1
        source = next(iter(sources.values()))
        assert source["installed"] and source["permanent"] and source["spell_level"] == 5
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == finished.state
        receipt = StatelessMcpMechanics(owner).execute_action(finish)
        assert receipt.status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state == finished.state


def test_real_ward_entry_body_plane_and_selected_exclusions():
    state, _, _ = _case()
    sid, _ = _cast(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("move_srd_scene_creature", state, actor_state="guard", destination=[8, 0, 0])
    assert state == before
    # Real body edges, not only center points, enter the permanent Sphere.
    result = _run(
        "check_srd_hallow_movement",
        state,
        subject="guard",
        destination=[10.2, 0, 0],
        plane="material",
    ).result["context"]
    assert result["_hallow_movement_allowed"] is False
    assert result["_hallow_movement_blocking_ids"] == [sid]
    assert (
        _run(
            "check_srd_hallow_movement",
            state,
            subject="guard",
            destination=[8, 0, 0],
            plane="ethereal",
        ).result["context"]["_hallow_movement_allowed"]
        is True
    )
    assert (
        _run(
            "check_srd_hallow_movement",
            state,
            subject="guard",
            destination=[8, 0, 0],
            plane="material",
            voluntary=False,
        ).result["context"]["_hallow_movement_allowed"]
        is True
    )
    _run("end_srd_hallow_source", state, source_id=sid)
    _cast(state, ward_types=["undead"])
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[8, 0, 0])
    assert secure._body(state, "guard")["position"] == [8, 0, 0]


def test_real_permanent_lifetime_overlap_no_effect_and_exact_removal():
    state, _, _ = _case()
    sid, _ = _cast(state)
    failed, context = _cast(state)
    assert failed is None and context["_hallow_cast_failed"] is True
    assert len(state[f"{SRD}.state"]["spell_hallow_source_ids"]) == 1
    state[f"{SRD}.entities"]["hero"].update(is_alive=False, hp=0)
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    result = _run(
        "check_srd_hallow_movement", state, subject="guard", destination=[8, 0, 0], plane="material"
    ).result["context"]
    assert result["_hallow_movement_allowed"] is False
    _run("end_srd_hallow_source", state, source_id=sid)
    assert (
        _run(
            "check_srd_hallow_movement",
            state,
            subject="guard",
            destination=[8, 0, 0],
            plane="material",
        ).result["context"]["_hallow_movement_allowed"]
        is True
    )


@pytest.mark.parametrize("extra,expected", [("resistance", 6), ("vulnerability", 21)])
def test_actual_composite_typed_packet_temp_hp_and_same_defense_once(extra, expected):
    state, _, _ = _case()
    secure._body(state, "guard")["position"] = [5, 0, 0]
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=100, max_hp=100, temp_hp=3)
    if extra == "resistance":
        target["damage_resistances"] = ["fire"]
    _cast(state, ward_types=[], extra=extra, effect_types=["fiend"], damage_type="fire")
    _run(
        "apply_srd_qualifying_composite_damage",
        state,
        target="guard",
        component_1_amount=9,
        component_1_damage_type="fire",
        component_2_amount=6,
        component_2_damage_type="cold",
        source_state_id="hero",
    )
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] == 100 - expected - (1 if extra == "resistance" else 0)
    assert target["temp_hp"] == 0


def test_actual_courage_immunity_type_and_source_qualified_ward_check():
    state, _, _ = _case()
    secure._body(state, "guard")["position"] = [5, 0, 0]
    _cast(state, effect_types=["fiend"])
    result = _run(
        "check_srd_target_condition_immunity",
        state,
        target_state="guard",
        condition="frightened",
        source_state_id="other",
    ).result["context"]
    assert result["_source_condition_immune"] is True
    result = _run(
        "check_srd_hallow_condition",
        state,
        target_state="hero",
        condition="charmed",
        source_state_id="guard",
    ).result["context"]
    assert result["_hallow_condition_suppressed"] is True
    result = _run(
        "check_srd_hallow_condition",
        state,
        target_state="hero",
        condition="charmed",
        source_state_id="other",
    ).result["context"]
    assert result["_hallow_condition_suppressed"] is False


def test_actual_language_and_silence_at_final_perception_projection():
    state, _, _ = _case()
    secure._body(state, "guard")["position"] = [5, 0, 0]
    state[f"{SRD}.entities"]["hero"]["languages"] = ["common"]
    state[f"{SRD}.entities"]["guard"]["languages"] = ["infernal"]
    sid, _ = _cast(state, ward_types=[], extra="tongues", effect_types=["humanoid"])
    result = _run(
        "resolve_srd_spell_language",
        state,
        listener_state="guard",
        speaker_state="hero",
        channel="speech",
        perceived=True,
        language="common",
    ).result["context"]
    assert result["_language_understands"] is True and result["_language_can_reply"] is True
    _run("end_srd_hallow_source", state, source_id=sid)
    _cast(state, ward_types=[], extra="silence")
    result = _run(
        "resolve_srd_spell_language",
        state,
        listener_state="hero",
        speaker_state="hero",
        channel="speech",
        perceived=True,
        language="common",
    ).result["context"]
    assert result["_language_understands"] is False and result["_language_perceived"] is False


@pytest.mark.parametrize("extra,light", [("daylight", "bright"), ("darkness", "magical_darkness")])
def test_pure_light_source_level_suppression_and_retirement(extra, light):
    state, _, _ = _case()
    sid, _ = _cast(state, extra=extra)
    result = _run("check_srd_hallow_light", state, point=[0, 0, 0], plane="material").result[
        "context"
    ]
    assert (
        result["_hallow_light"],
        result["_hallow_light_spell_level"],
        result["_hallow_light_source_id"],
    ) == (light, 5, sid)
    state[f"{SRD}.state"]["spell_hallow_sources"][sid]["suppressed"] = True
    assert (
        _run("check_srd_hallow_light", state, point=[0, 0, 0], plane="material").result["context"][
            "_hallow_light"
        ]
        is None
    )
    state[f"{SRD}.state"]["spell_hallow_sources"][sid]["suppressed"] = False
    _run("end_srd_hallow_source", state, source_id=sid)
    assert (
        _run("check_srd_hallow_light", state, point=[0, 0, 0], plane="material").result["context"][
            "_hallow_light"
        ]
        is None
    )
