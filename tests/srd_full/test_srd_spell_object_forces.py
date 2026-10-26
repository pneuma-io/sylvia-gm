"""Source force controls preserve actual object identity, costs, geometry and retries."""

from copy import deepcopy
from functools import lru_cache
from unittest.mock import patch

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.build_srd_5_2_1_spells import refresh_exploration_runtime
from scripts.srd_full.srd_spell_object_forces import PUBLIC_TOOLS, bind_runtime_consumers, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, MechanicContext, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_spell_object_content as objects
from tests import test_srd_spell_object_utilities as utilities
from tests import test_srd_spell_result_cards as spell_cards
from tests import test_srd_spell_supplies as supplies
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage

SRD = objects.SRD
_UTILITY_RUNTIME_BYTES = utilities._runtime_bytes


@lru_cache
def _runtime_bytes():
    return refresh_exploration_runtime(
        bind_runtime_consumers(extend_runtime(_UTILITY_RUNTIME_BYTES().decode()))
    ).encode()


@lru_cache
def _mechanic(name):
    runtime = _load_runtime(_runtime_bytes().decode())
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.1.0",
        package_sha256="d" * 64,
        runtime_bytes=_runtime_bytes(),
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


def _run(name, state, **parameters):
    result = _mechanic(name)(
        MechanicContext(
            campaign_id="object-forces",
            turn_id="turn",
            action_id=name,
            state=owned_payload(state),
            parameters=owned_payload(parameters),
            participants=owned_payload({}),
        )
    )
    for mutation in result.mutations:
        state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)
    return result.result["context"]


def _form(state, caster="hero"):
    _run(
        "record_srd_hand_sized_form",
        state,
        caster_state=caster,
        form_id="hand-form",
        fiction="Synthetic translucent spectral hand depiction",
        kind="image",
        extent=[0.5, 0.5, 0.5],
    )


def _mass(state, weight=7, kind="object"):
    _run(
        "record_srd_object_utility_facts",
        state,
        object_id="door",
        weight_pounds=weight,
        manufactured=True,
        metal=False,
        object_kind=kind,
        inventory_item="",
    )


def _cast(state, output="hand", operation="move"):
    _run(
        "cast_srd_mage_hand",
        state,
        caster_state="hero",
        spell="mage_hand",
        slot_level=0,
        output_id=output,
        form_id="hand-form",
        point=[3, 0, 0],
        operation=operation,
        object_id="door",
    )
    return next(
        r
        for r in state[f"{SRD}.state"]["turn_effects"]
        if r["kind"] == "object_utility_spell" and r["spell"] == "mage_hand"
    )


def _reject(name, state, *, match=None, **parameters):
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError), match=match):
        _run(name, state, **parameters)
    assert state == before


def test_actual_hand_cast_holds_weight_qualified_scenery_and_recast_releases_exact_source():
    state = utilities._state()
    _form(state)
    _mass(state)
    first = _cast(state, operation="hold")
    assert first["source_id"] == "hero:mage_hand:1" and first["spell_level"] == 0
    assert first["cargo_id"] == "door"
    assert (
        supplies._row(state, "door")["metadata"]["srd_spell_carrier"]["source_id"]
        == first["source_id"]
    )
    assert "hand" not in state[f"{SRD}.entities"]
    second = _cast(state, "second-hand")
    assert second["source_id"] == "hero:mage_hand:2"
    assert first not in state[f"{SRD}.state"]["turn_effects"]
    assert supplies._row(state, "hand")["active"] is False
    assert supplies._row(state, "door")["metadata"]["srd_spell_carrier"] is None


def test_hand_weight_and_actual_contact_rejection_preserve_all_state():
    state = utilities._state()
    _form(state)
    _mass(state, 11)
    before = deepcopy(state)
    _reject(
        "cast_srd_mage_hand",
        state,
        match="at most ten pounds",
        caster_state="hero",
        spell="mage_hand",
        slot_level=0,
        output_id="hand",
        form_id="hand-form",
        point=[3, 0, 0],
        operation="hold",
        object_id="door",
    )
    assert state == before
    source = _cast(state)
    _reject(
        "operate_srd_mage_hand",
        state,
        match="does not touch this object",
        caster_state="hero",
        source_id=source["source_id"],
        operation="hold",
        object_id="gem",
    )


def test_actual_hand_source_deadline_and_caster_relocation_vanish_without_creature_state():
    state = utilities._state()
    _form(state)
    source = _cast(state)
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert source not in state[f"{SRD}.state"]["turn_effects"]
    assert supplies._row(state, "hand")["active"] is False
    assert state[f"{SRD}.entities"]["hero"]["mage_hand_source_id"] is None
    second = _cast(state, "second-hand")
    _run(
        "move_srd_travel_subject",
        state,
        subject="hero",
        destination=[40, 0, 0],
        plane="material",
        teleport=True,
    )
    assert second not in state[f"{SRD}.state"]["turn_effects"]
    assert supplies._row(state, "second-hand")["active"] is False
    assert "conditions" not in supplies._row(state, "second-hand")["metadata"]


def test_touching_hand_opens_actual_unlocked_access_and_pours_actual_vial_contents():
    state = utilities._state()
    _form(state)
    _run(
        "record_srd_object_access",
        state,
        object_id="door",
        object_kind="container",
        closed=True,
        locks=[],
        stuck=False,
        barred=False,
    )
    _run(
        "record_srd_object_container",
        state,
        object_id="door",
        capacity_gallons=0.1,
        water_gallons=0.1,
        is_open=False,
    )
    _mass(state, 0.2, "vial")
    source = _cast(state, operation="open")
    assert supplies._row(state, "door")["metadata"]["srd_object_container"]["is_open"] is True
    _run(
        "operate_srd_mage_hand",
        state,
        caster_state="hero",
        source_id=source["source_id"],
        object_id="door",
        operation="pour",
    )
    metadata = supplies._row(state, "door")["metadata"]
    assert metadata["srd_object_container"]["water_gallons"] == 0
    assert metadata["srd_poured_contents"]["water_gallons"] == 0.1
    assert metadata["srd_poured_contents"]["position"] == [3, 0, 0]


def test_force_emission_and_relocation_bind_preserve_repeat_composition():
    text = utilities._runtime_bytes().decode()
    once = bind_runtime_consumers(extend_runtime(text))
    twice = bind_runtime_consumers(extend_runtime(once))
    assert _load_runtime(twice) == _load_runtime(once)


@lru_cache
def _test_archive():
    runtime_bytes = _runtime_bytes()
    with (
        patch.object(utilities, "_runtime_bytes", lambda: runtime_bytes),
        patch.object(
            utilities,
            "PUBLIC_TOOLS",
            utilities.PUBLIC_TOOLS | PUBLIC_TOOLS,
        ),
    ):
        return utilities._test_archive.__wrapped__()


def _campaign(owner, monkeypatch):
    definitions = spell_cards._spell_definitions() | supplies.PROFILES | utilities.PROFILES
    with (
        patch.object(objects, "_state", utilities._state),
        patch.object(objects, "_test_archive", _test_archive),
        patch.object(
            spell_cards,
            "_spell_definitions",
            lambda: definitions,
        ),
    ):
        return objects._create_object_campaign(owner, monkeypatch)


def test_paid_public_hand_control_carries_actual_object_magic_action_and_reopen_retry(
    tmp_path, monkeypatch
):
    database = tmp_path / "hand.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch)
        prepared, _ = _prepare_mage(owner, [{"spell": "mage_hand", "uses_per_day": 0}])
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        form, _ = _act(
            owner,
            "record_srd_hand_sized_form",
            caster_state="mage",
            form_id="hand-form",
            fiction="Synthetic spectral hand",
            kind="image",
            extent=[0.5, 0.5, 0.5],
        )
        assert form.status is ActionStatus.COMMITTED, form.result
        fact, _ = _act(
            owner,
            "record_srd_object_utility_facts",
            object_id="door",
            weight_pounds=7,
            manufactured=True,
            metal=False,
            object_kind="object",
            inventory_item="",
        )
        assert fact.status is ActionStatus.COMMITTED, fact.result
        started, _ = _act(
            owner,
            "start_combat_encounter_entities",
            first_actor="mage",
            second_actor="guard",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        assert started.status is ActionStatus.COMMITTED, started.result
        begun, _ = _act(owner, "begin_combat_turn")
        assert begun.status is ActionStatus.COMMITTED, begun.result
        cast, cast_command = _act(
            owner,
            "cast_spell",
            caster_state="mage",
            spell="mage_hand",
            utility_output_id="hand",
            utility_form_id="hand-form",
            utility_point=[3, 0, 0],
            utility_operation="hold",
            utility_object_id="door",
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        source = next(
            r for r in saved.state[f"{SRD}.state"]["turn_effects"] if r["spell"] == "mage_hand"
        )
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert (
            supplies._row(saved.state, "door")["metadata"]["srd_spell_carrier"]["source_id"]
            == source["source_id"]
        )
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.execute_action(cast_command) == cast
        assert reopened.snapshot("casting") == saved
        for _ in range(2):
            ended, _ = _act(reopened, "end_combat_turn")
            assert ended.status is ActionStatus.COMMITTED, ended.result
            advanced, _ = _act(reopened, "advance_combat_encounter_turn")
            assert advanced.status is ActionStatus.COMMITTED, advanced.result
            begun, _ = _act(reopened, "begin_combat_turn")
            assert begun.status is ActionStatus.COMMITTED, begun.result
        with pytest.raises(MechanicsRejected):
            _act(
                reopened,
                "control_srd_mage_hand",
                caster_state="mage",
                source_id=source["source_id"],
                destination=[8, 0, 0],
                operation="attack",
                object_id="door",
            )
        control, command = _act(
            reopened,
            "control_srd_mage_hand",
            caster_state="mage",
            source_id=source["source_id"],
            destination=[8, 0, 0],
            operation="drop",
            object_id="door",
        )
        assert control.status is ActionStatus.COMMITTED, control.result
        final = reopened.snapshot("casting")
        assert final.state[f"{SRD}.combat"]["action_available"] is False
        assert supplies._row(final.state, "hand")["position"] == [8, 0, 0]
        assert supplies._row(final.state, "door")["position"] == [8, 0, 0]
        assert supplies._row(final.state, "door")["metadata"]["srd_spell_carrier"] is None
        assert reopened.execute_action(command) == control
        assert reopened.snapshot("casting") == final


def test_public_ordinary_caster_movement_vanishes_saved_hand_without_a_second_position(
    tmp_path, monkeypatch
):
    database = tmp_path / "hand-movement.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch)
        prepared, _ = _prepare_mage(owner, [{"spell": "mage_hand", "uses_per_day": 0}])
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        form, _ = _act(
            owner,
            "record_srd_hand_sized_form",
            caster_state="mage",
            form_id="hand-form",
            fiction="Synthetic spectral hand",
            kind="image",
            extent=[0.5, 0.5, 0.5],
        )
        assert form.status is ActionStatus.COMMITTED, form.result
        cast, cast_command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell="mage_hand",
            utility_output_id="hand",
            utility_form_id="hand-form",
            utility_point=[-3, 0, 0],
            utility_operation="move",
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        source = next(
            r for r in saved.state[f"{SRD}.state"]["turn_effects"] if r.get("force_id") == "hand"
        )
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.execute_action(cast_command) == cast
        assert reopened.snapshot("casting") == saved
        advanced, _ = _act(reopened, "advance_srd_lasting_time", elapsed_rounds=1)
        assert advanced.status is ActionStatus.COMMITTED, advanced.result
        moved, move_command = _act(
            reopened,
            "move_srd_scene_creature",
            actor_state="mage",
            destination=[30, 1, 0],
        )
        assert moved.status is ActionStatus.COMMITTED, moved.result
        final = reopened.snapshot("casting")
        assert supplies._row(final.state, "hand")["active"] is False
        assert supplies._row(final.state, "hand")["position"] == [-3, 0, 0]
        assert "hand" not in final.state[f"{SRD}.entities"]
        assert source not in final.state[f"{SRD}.state"]["turn_effects"]
        assert final.state[f"{SRD}.entities"]["mage"]["mage_hand_source_id"] is None
        assert reopened.execute_action(move_command) == moved
        assert reopened.snapshot("casting") == final
