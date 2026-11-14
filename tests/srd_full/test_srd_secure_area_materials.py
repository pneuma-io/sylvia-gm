"""The real Forbiddance daily history controls the existing quantity/commit material owner."""

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_extended_casting import extend_runtime as extend_casting
from scripts.srd_full.srd_reaction_spells import (
    _cast_window,
    _tool,
    finalize_pending_spell_guards,
    normalize_pending_spell_guards,
)
from scripts.srd_full.srd_secure_area_materials import bind_runtime, extend_runtime
from scripts.srd_full.srd_spell_magic_zones import bind_effect_consumers as bind_magic_effects
from scripts.srd_full.srd_spell_magic_zones import normalize_runtime as normalize_magic_effects
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as result_cards
from tests import test_srd_spell_secure_areas as secure
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case


@lru_cache
def _runtime():
    # The immutable public input retains the older admitted runtime; compose the
    # genuine owning family dependency before Secure3's schema and source leaves.
    runtime_path = Path(
        os.environ.get(
            "FC14_MATERIAL_RUNTIME",
            str(
                Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml",
            ),
        )
    )
    original = runtime_path.read_text()
    assert "normalize_srd_barrier_scene_subject:" in original
    first = extend_runtime(
        secure.extend_runtime(normalize_pending_spell_guards(normalize_magic_effects(original)))
    )
    runtime = _load_runtime(first)
    runtime["definitions"]["spells"].update(deepcopy(secure.PROFILES))
    changed = {}
    for name, tool in runtime["tools"].items():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(
                    set(parameter["enum_values"]) | secure.PROFILES.keys()
                )
                changed[name] = tool
    first = _replace_tools(first, changed)
    begin, end = (
        "    # BEGIN GENERATED SPELL DEFINITIONS\n",
        "    # END GENERATED SPELL DEFINITIONS",
    )
    start, finish = first.index(begin) + len(begin), first.index(end)
    rows = "".join(
        f"    {name}: {json.dumps(value)}\n"
        for name, value in sorted(runtime["definitions"]["spells"].items())
    )
    first = first[:start] + rows + first[finish:]
    first = extend_casting(first, spell_ids=sorted(runtime["definitions"]["spells"]))
    first = refresh_exploration_runtime(first)
    first = bind_runtime(first)
    # Regenerate only the genuine material-validating casting prefix. Other
    # admitted reaction owners belong to the immutable runtime dependency.
    windows = _cast_window(_load_runtime(first)["tools"]["cast_spell"])
    for tool in windows.values():
        tool["effects"] = _tool("", tool["parameters"], tool["effects"])["effects"]
    first = finalize_pending_spell_guards(_replace_tools(first, windows))
    first = bind_magic_effects(secure.bind_runtime_consumers(first))
    repeated = bind_runtime(extend_runtime(first))
    if repeated != first:
        import difflib

        difference = list(difflib.unified_diff(first.splitlines(), repeated.splitlines()))
        raise AssertionError("\n".join(difference[:60]))
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
    monkeypatch.setattr(
        result_cards, "_spell_definitions", lambda: _runtime()[1]["definitions"]["spells"]
    )


def _materials(state, spell, *, derived=False, validate=False, waived=False):
    return _run(
        "resolve_srd_spell_materials",
        state,
        caster_state="hero",
        spell=spell,
        item_ids=[m["id"] for m in state[f"{SRD}.entities"]["hero"]["spell_materials"]],
        materials_available=True,
        components_waived=waived,
        permitted_focus_kinds=[],
        target_count=1,
        validate_only=validate,
        secure_area_final_consumption=derived,
    )


def _forbiddance():
    state, data, parameters = _spell_case("forbiddance")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    secure._admit_scene(state)
    secure._ground(state)
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "fiend"
    return state, data, parameters


def test_actual_thirty_day_history_consumes_only_final_supply_and_validation_retains_it():
    state, _, _ = _forbiddance()
    hero = state[f"{SRD}.entities"]["hero"]
    before = deepcopy(hero["spell_materials"])
    before_inventory = deepcopy(hero["inventory"])
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
    for day in range(30):
        if day:
            _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
        derived = _run("prepare_srd_secure_area_cast", state, **choices).result["context"]
        assert derived["_secure_daily_count"] == day + 1
        consume = derived["_secure_materials_consumed"]
        assert consume is (day == 29)
        _materials(state, "forbiddance", derived=consume, validate=True)
        hero = state[f"{SRD}.entities"]["hero"]
        assert hero["spell_materials"] == before
        assert hero["inventory"] == before_inventory
        receipt = _materials(state, "forbiddance", derived=consume)
        hero = state[f"{SRD}.entities"]["hero"]
        if day < 29:
            assert hero["spell_materials"] == before
            assert "Material used" in str(receipt.result["context"]["_material_rows"])
        else:
            assert all(
                a["quantity"] == b["quantity"] - 1
                for a, b in zip(hero["spell_materials"], before, strict=True)
            )
            assert "Material consumed" in str(receipt.result["context"]["_material_rows"])
        source_id, source = secure._cast(state, "forbiddance", floor_width=20, floor_length=20)
        assert source["permanent"] is consume
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert state[f"{SRD}.state"]["spell_secure_area_sources"][source_id]["installed"] is True


@pytest.mark.parametrize("spell,consumed", [("magic_circle", True), ("mage_armor", False)])
def test_other_printed_materials_keep_existing_consumption_even_with_private_flag(spell, consumed):
    state, _, _ = _spell_case(spell)
    hero = state[f"{SRD}.entities"]["hero"]
    before = deepcopy(hero["spell_materials"])
    _materials(state, spell, derived=True, validate=True)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["spell_materials"] == before
    _materials(state, spell, derived=True)
    hero = state[f"{SRD}.entities"]["hero"]
    assert all(
        a["quantity"] == b["quantity"] - int(consumed)
        for a, b in zip(hero["spell_materials"], before, strict=True)
    )


def test_existing_component_waiver_retains_supply_on_final_forbiddance():
    state, _, _ = _forbiddance()
    hero = state[f"{SRD}.entities"]["hero"]
    before = deepcopy(hero["spell_materials"])
    _materials(state, "forbiddance", derived=True, waived=True)
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"] == before
    _, runtime = _runtime()
    for name in ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_cast"):
        assert (
            "secure_area_final_consumption"
            not in _tool_schema(
                runtime["tools"][name],
                tool_name=name,
            )["properties"]
        )
    private = _tool_schema(runtime["tools"]["resolve_srd_spell_materials"])["properties"]
    assert private["secure_area_final_consumption"]["x-sylvia-gm-model-hidden"] is True


def test_public_thirtieth_paid_completion_reopens_and_retry_cannot_spend_again(
    monkeypatch, tmp_path
):
    from dataclasses import replace

    import tests.srd_full.test_srd_public_spell_casting as public
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
    from sylvia_gm.application.package_materialization import NormalizedPackageDraft
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests.srd_full.test_srd_extended_casting import _begin

    state, data, parameters = _forbiddance()
    parameters.update(
        secure_area_origin=[0, 0, 0],
        secure_area_types=["fiend"],
        secure_area_floor_width=20,
        secure_area_floor_length=20,
        selected_damage_type="radiant",
    )
    for day in range(29):
        if day:
            _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
        secure._cast(state, "forbiddance", floor_width=20, floor_length=20)
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    materials = deepcopy(state[f"{SRD}.entities"]["hero"]["spell_materials"])
    raw, runtime = _runtime()
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    record["tools"] = [d for d in record["tools"] if d["name"] in runtime["tools"]]
    for declaration in record["tools"]:
        if declaration["name"] in runtime["tools"]:
            declaration["input_schema"] = _tool_schema(
                runtime["tools"][declaration["name"]],
                tool_name=declaration["name"],
            )
    payload = dict(primary.payloads)
    payload["runtime/ruleset.yaml"] = raw
    # This selected-runtime archive retains only its real declared public tools,
    # including their exact inclusion links; later foreign tools are excluded.
    content_path = record["content_declarations"]
    content = json.loads(payload[content_path])
    declared_tools = {d["name"] for d in record["tools"]}
    for inclusion in content["inclusions"]:
        inclusion["links"]["tools"] = [
            n for n in inclusion["links"]["tools"] if n in declared_tools
        ]
    payload[content_path] = canonical_json_bytes(content)
    archive = _archive((NormalizedPackageDraft(record, payload, 4), reference))
    # This is strict Format-4 admission of the selected immutable-runtime owner
    # composition, not a canonical/full-pipeline completeness claim.
    monkeypatch.setattr(public, "bundled_srd_archive", lambda: archive)
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, parameters))
    database = tmp_path / "forbiddance30.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "forbiddance")
        mechanics = StatelessMcpMechanics(owner)
        command = ActionCommand(
            campaign_id="casting",
            action_id="begin-forbid30",
            idempotency_key="begin-forbid30",
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
        started = owner.snapshot("casting")
        hero = started.state[f"{SRD}.entities"]["hero"]
        assert hero["spell_materials"] == materials and hero["resources"]["spell_slot_6"] == 3
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == started.state
        mechanics = StatelessMcpMechanics(owner)
        continuing = replace(
            command,
            action_id="continue-forbid30",
            idempotency_key="continue-forbid30",
            expected_revision=started.revision,
            tool_name="continue_srd_spell_cast",
            parameters={
                "caster_state": "hero",
                "elapsed_rounds": 100,
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
        assert ready.state[f"{SRD}.entities"]["hero"]["spell_materials"] == materials
        finish = replace(
            command,
            action_id="finish-forbid30",
            idempotency_key="finish-forbid30",
            expected_revision=ready.revision,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = mechanics.execute_action(finish)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert mechanics.execute_action(finish) == receipt
        finished = owner.snapshot("casting")
        hero = finished.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["spell_slot_6"] == 2
        assert all(
            a["quantity"] == b["quantity"] - 1
            for a, b in zip(hero["spell_materials"], materials, strict=True)
        )
        installed = [
            s
            for s in finished.state[f"{SRD}.state"]["spell_secure_area_sources"].values()
            if s["installed"]
        ]
        assert (
            len(installed) == 1 and installed[0]["daily_count"] == 30 and installed[0]["permanent"]
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == finished.state
        receipt = StatelessMcpMechanics(owner).execute_action(finish)
        assert receipt.status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state == finished.state
