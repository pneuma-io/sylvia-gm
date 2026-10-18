"""The parent ward owns actual locks, group lifetimes and genuine area exemptions."""

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_extended_casting import extend_runtime as extend_casting
from scripts.srd_full.srd_guards_wards import PROFILES, extend_runtime
from scripts.srd_full.srd_reaction_spells import (
    finalize_pending_spell_guards,
    normalize_pending_spell_guards,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_spell_object_content as objects
from tests import test_srd_spell_result_cards as cards

SRD = objects.SRD


@lru_cache
def _runtime():
    base = Path(os.environ.get("FC14_MATERIAL_RUNTIME", objects.ROOT / "runtime/ruleset.yaml"))
    text = extend_runtime(base.read_text())
    assert extend_runtime(text) == text
    runtime = _load_runtime(text)
    assert not (errors := validate_ruleset_data(runtime)), errors
    return text.encode(), runtime


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="c" * 64,
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


@lru_cache
def _paid_runtime():
    base = Path(os.environ.get("FC14_MATERIAL_RUNTIME", objects.ROOT / "runtime/ruleset.yaml"))
    text = extend_runtime(normalize_pending_spell_guards(base.read_text()))
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(deepcopy(PROFILES))
    changes = {}
    for name, tool in runtime["tools"].items():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | PROFILES.keys())
                changes[name] = tool
    text = _replace_tools(text, changes)
    begin = "    # BEGIN GENERATED SPELL DEFINITIONS\n"
    end = "    # END GENERATED SPELL DEFINITIONS"
    start, finish = text.index(begin) + len(begin), text.index(end)
    rows = "".join(
        f"    {name}: {json.dumps(value)}\n"
        for name, value in sorted(runtime["definitions"]["spells"].items())
    )
    text = text[:start] + rows + text[finish:]
    text = extend_casting(text, spell_ids=sorted(runtime["definitions"]["spells"]))
    text = finalize_pending_spell_guards(refresh_exploration_runtime(text))
    return text.encode(), _load_runtime(text)


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(objects, "_mechanic", _mechanic)


def _case():
    state = objects._state()
    objects._access(state)
    return state, _cast_ward(state)


def _cast_ward(state, **choices):
    corridors = [{"min": [x, 0, 0], "max": [x + 10, 10, 20]} for x in [0, 12, 24, 36]]
    parameters = {
        "caster_state": "hero",
        "slot_level": 6,
        "origin": [0, 0, 0],
        "corridors": corridors,
        "lamp_positions": [[x + 5, 5, 5] for x in [0, 12, 24, 36]],
        "password": "silver threshold",
        "caster_can_touch_origin": True,
    }
    parameters.update(choices)
    context = objects._run("cast_srd_guards_wards", state, **parameters)
    return context["_new_guards_wards_source_id"]


def test_actual_parent_locks_block_access_and_exact_group_end_preserves_other_lock():
    state, sid = _case()
    lock = objects._object(state)["metadata"]["srd_arcane_locks"][-1]
    assert lock["ward_source_id"] == sid and lock["spell_level"] == 6
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        objects._run(
            "operate_srd_locked_object",
            state,
            actor_state="guard",
            object_id="door",
            operation="open",
        )
    assert state == before
    # A genuine independent Arcane Lock remains after the ward's door group ends.
    objects._cast(state, "arcane_lock")
    locks = deepcopy(objects._object(state)["metadata"]["srd_arcane_locks"])
    objects._run("end_srd_guards_wards_effect", state, source_id=sid, group="doors")
    assert objects._object(state)["metadata"]["srd_arcane_locks"] == locks[1:]
    source = state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]
    assert source["installed"] and not source["groups"]["doors"]
    for group in ["corridors", "stairs", "other"]:
        objects._run("end_srd_guards_wards_effect", state, source_id=sid, group=group)
    assert not state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["installed"]


def test_real_clock_expiry_releases_actual_doors_without_caster_concentration():
    state, sid = _case()
    state[f"{SRD}.entities"]["hero"].update(is_alive=False, hp=0)
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=14399)
    assert state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["installed"]
    assert objects._object(state)["metadata"]["srd_arcane_locks"]
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert not state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["installed"]
    assert objects._object(state)["metadata"]["srd_arcane_locks"] == []
    objects._run(
        "operate_srd_locked_object", state, actor_state="guard", object_id="door", operation="open"
    )
    assert not objects._object(state)["metadata"]["srd_object_access"]["closed"]


def test_actual_password_speaker_exemption_lasts_for_ward_and_is_plane_bound():
    state, sid = _case()
    before = objects._run(
        "check_srd_guards_wards_fog",
        state,
        observer_state="guard",
        point=[8, 4, 4],
        plane="material",
    )
    assert before["_guards_wards_fog"] is True
    objects._run(
        "speak_srd_guards_wards_password",
        state,
        actor_state="guard",
        source_id=sid,
        password="silver threshold",
    )
    assert state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["password_speakers"] == [
        "guard"
    ]
    result = objects._run(
        "check_srd_guards_wards_fog",
        state,
        observer_state="guard",
        point=[8, 4, 4],
        plane="material",
    )
    assert result["_guards_wards_fog"] is False
    objects._run(
        "operate_srd_locked_object", state, actor_state="guard", object_id="door", operation="open"
    )
    assert objects._object(state)["metadata"]["srd_object_access"]["closed"] is False
    result = objects._run(
        "check_srd_guards_wards_fog",
        state,
        observer_state="other",
        point=[8, 4, 4],
        plane="ethereal",
    )
    assert result["_guards_wards_fog"] is False


def test_actual_lamps_follow_other_group_source_level_and_retirement():
    state, sid = _case()
    result = objects._run("check_srd_guards_wards_light", state, point=[5, 5, 5], plane="material")
    assert result["_guards_wards_dim_light"] is True and result["_guards_wards_light_level"] == 6
    objects._run("end_srd_guards_wards_effect", state, source_id=sid, group="other")
    result = objects._run("check_srd_guards_wards_light", state, point=[5, 5, 5], plane="material")
    assert result["_guards_wards_dim_light"] is False
    assert state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["installed"]


@pytest.mark.skipif(
    os.environ.get("FC14_WARD_FULL_YEAR") != "1",
    reason="The actual365-day sequence is an opt-in owning checkpoint.",
)
def test_actual_daily_casts_reach_365_and_permanence_ends_only_after_all_groups():
    state, sid = _case()
    # An additional real same-day cast cannot manufacture a second day.
    sid = _cast_ward(state)
    lasting = state[f"{SRD}.state"]
    assert lasting["spell_guards_wards_sources"][sid]["daily_count"] == 1
    for day in range(1, 365):
        objects._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
        sid = _cast_ward(state)
        lasting = state[f"{SRD}.state"]
        assert lasting["spell_guards_wards_sources"][sid]["daily_count"] == day + 1
    assert lasting["spell_guards_wards_sources"][sid]["permanent"] is True
    state[f"{SRD}.entities"]["hero"].update(is_alive=False, hp=0)
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["installed"] is True
    assert objects._object(state)["metadata"]["srd_arcane_locks"]
    for group in ["corridors", "doors", "stairs"]:
        objects._run("end_srd_guards_wards_effect", state, source_id=sid, group=group)
    assert state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["installed"] is True
    objects._run("end_srd_guards_wards_effect", state, source_id=sid, group="other")
    assert state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["installed"] is False


def test_real_daily_counter_and_saved_history_permanence_threshold():
    state, sid = _case()
    sid = _cast_ward(state)
    assert state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["daily_count"] == 1
    for day in [2, 3]:
        objects._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
        sid = _cast_ward(state)
        assert state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["daily_count"] == day
    # A synthetic saved-history boundary fixture avoids repeating the proved year-long sequence.
    lasting = state[f"{SRD}.state"]
    lasting["spell_guards_wards_sources"][sid]["daily_count"] = 364
    lasting["guards_wards_daily_history"][0]["days"] = 364
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    sid = _cast_ward(state)
    source = state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]
    assert source["daily_count"] == 365 and source["permanent"] is True
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["installed"] is True


def test_skipped_daily_cast_resets_real_source_history_and_rejects_fabricated_history():
    state, sid = _case()
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=28800)
    sid = _cast_ward(state)
    assert state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["daily_count"] == 1
    state[f"{SRD}.state"]["guards_wards_daily_history"][0]["days"] = 364
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual saved ward source"):
        _cast_ward(state)
    assert state == before


def test_public_paid_hour_cast_reopens_and_retry_retains_real_ward_and_reusable_silver_rod(
    monkeypatch,
    tmp_path,
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
    from sylvia_gm.application.package_materialization import NormalizedPackageDraft
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_extended_casting import _begin

    raw, runtime = _paid_runtime()
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    state, data, parameters = cards._spell_case("guards_and_wards")
    scene_state = objects._state()
    objects._access(scene_state)
    state[f"{SRD}.state"] = deepcopy(scene_state[f"{SRD}.state"])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    parameters.update(
        guards_wards_origin=[0, 0, 0],
        guards_wards_corridors=[
            {"min": [x, 0, 0], "max": [x + 10, 10, 20]} for x in [0, 12, 24, 36]
        ],
        guards_wards_lamps=[[x + 5, 5, 5] for x in [0, 12, 24, 36]],
        guards_wards_password="silver threshold",
        caster_can_touch_target=True,
    )
    supplies = deepcopy(state[f"{SRD}.entities"]["hero"]["spell_materials"])
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
    database = tmp_path / "guards-wards.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "guards_and_wards")
        mechanics = StatelessMcpMechanics(owner)
        begin = ActionCommand(
            campaign_id="casting",
            action_id="begin-ward",
            idempotency_key="begin-ward",
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
        assert hero["spell_materials"] == supplies and hero["resources"]["spell_slot_6"] == 3
        assert not started.state[f"{SRD}.state"].get("has_guards_wards_sources")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == started.state
        mechanics = StatelessMcpMechanics(owner)
        continuing = replace(
            begin,
            action_id="continue-ward",
            idempotency_key="continue-ward",
            expected_revision=started.revision,
            tool_name="continue_srd_spell_cast",
            parameters={
                "caster_state": "hero",
                "elapsed_rounds": 600,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        receipt = mechanics.execute_action(continuing)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        ready = owner.snapshot("casting")
        assert ready.state[f"{SRD}.entities"]["hero"]["spell_materials"] == supplies
        finish = replace(
            begin,
            action_id="finish-ward",
            idempotency_key="finish-ward",
            expected_revision=ready.revision,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = mechanics.execute_action(finish)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert mechanics.execute_action(finish) == receipt
        finished = owner.snapshot("casting")
        hero = finished.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["spell_slot_6"] == 2 and hero["spell_materials"] == supplies
        sources = finished.state[f"{SRD}.state"]["spell_guards_wards_sources"]
        assert len(sources) == 1
        source = next(iter(sources.values()))
        assert source["installed"] and not source["permanent"] and source["spell_level"] == 6
        assert source["expires_tick"] - source["started_tick"] == 28800
        lock = objects._object(finished.state)["metadata"]["srd_arcane_locks"][0]
        assert lock["spell_level"] == 6 and lock["ward_source_id"] == next(iter(sources))
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == finished.state
        receipt = StatelessMcpMechanics(owner).execute_action(finish)
        assert receipt.status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state == finished.state
