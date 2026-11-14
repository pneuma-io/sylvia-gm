"""Actual cast allocation joins finite scene forces to saved dismissal and Dispel."""

from copy import deepcopy
from functools import lru_cache
from unittest.mock import patch

import pytest
from scripts import srd_spell_source_instances as sources
from scripts.srd_full.build_srd_5_2_1_spells import refresh_exploration_runtime
from scripts.srd_full.srd_control_behaviors import _walk
from scripts.srd_full.srd_spell_object_utility_sources import bind_source_consumers
from scripts.srd_full.srd_spell_recovery import extend_runtime as extend_recovery
from scripts.srd_full.srd_spell_removals import extend_runtime as extend_removals
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests import test_srd_spell_floating_disk as disk
from tests import test_srd_spell_object_forces as hand
from tests import test_srd_spell_supplies as supplies
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage

SRD = disk.SRD
_DISK_RUNTIME_BYTES = disk._runtime_bytes


def _test_dismissal_join(text):
    """Check production composition instead of adding a second test-only dispatcher."""
    tool = _load_runtime(text)["tools"]["dismiss_srd_spell"]
    calls = [node.get("call") for node in _walk(tool["effects"])]
    assert calls.count("collect_srd_object_utility_dismiss_sources") == 1
    assert calls.count("retire_srd_object_utility_source") == 1
    return text


@lru_cache
def _runtime_bytes():
    text = extend_recovery(_DISK_RUNTIME_BYTES().decode())
    # Root owns these two allowlist additions and the shared dismissal join.
    # The fixture executes the real allocator and exact owners, never installs
    # fabricated pending casts, roll results, or state identities.
    with (
        patch.dict(
            sources.SPECIFIC_DURATION_OWNERS,
            {
                "mage_hand": "cast_srd_mage_hand",
                "floating_disk": "cast_srd_floating_disk",
            },
        ),
        patch.object(
            sources,
            "INSTALLERS",
            sources.INSTALLERS
            | {
                "cast_srd_mage_hand",
                "cast_srd_floating_disk",
            },
        ),
    ):
        text = sources.extend_runtime(text)
    return refresh_exploration_runtime(
        _test_dismissal_join(extend_removals(bind_source_consumers(text)))
    ).encode()


def _campaign(owner, monkeypatch):
    runtime_bytes = _runtime_bytes()
    with (
        patch.object(disk, "_runtime_bytes", lambda: runtime_bytes),
        patch.object(disk, "PUBLIC_TOOLS", disk.PUBLIC_TOOLS | {"dismiss_srd_spell"}),
        patch.object(disk, "_test_archive", disk._test_archive.__wrapped__),
    ):
        return disk._campaign(owner, monkeypatch)


def _prepare(owner, spells):
    prepared, _ = _prepare_mage(
        owner,
        spells,
        inventory=["mercury-drop"],
        spell_materials=[
            {
                "id": "mercury-lot",
                "inventory_item": "mercury-drop",
                "kind": "specific",
                "components": ["floating_disk.1"],
                "quantity": 1,
                "value_cp": 0,
            }
        ],
    )
    assert prepared.status is ActionStatus.COMMITTED, prepared.result


def _cast(owner, spell, **choices):
    result, command = _act(
        owner,
        "cast_spell_outside_combat",
        caster_state="mage",
        spell=spell,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        **choices,
    )
    assert result.status is ActionStatus.COMMITTED, result.result
    return result, command


def _source(owner, force):
    return next(
        record
        for record in owner.snapshot("casting").state[f"{SRD}.state"]["turn_effects"]
        if record.get("force_id") == force
    )


def test_paid_independent_disks_saved_exact_dismissal_and_dispel_keep_costs_and_cargo(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "finite-object-sources.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch)
        _prepare(
            owner,
            [
                {"spell": "floating_disk", "uses_per_day": 2},
                {"spell": "dispel_magic", "uses_per_day": 1},
            ],
        )
        fact, _ = _act(
            owner,
            "record_srd_object_utility_facts",
            object_id="door",
            weight_pounds=7,
            manufactured=True,
            metal=False,
            object_kind="object",
            inventory_item="cargo-token",
        )
        assert fact.status is ActionStatus.COMMITTED, fact.result
        cast1, command1 = _cast(
            owner,
            "floating_disk",
            utility_output_id="disk-one",
            utility_point=[0, 3, 3],
            material_item_ids=["mercury-lot"],
        )
        cast2, command2 = _cast(
            owner,
            "floating_disk",
            utility_output_id="disk-two",
            utility_point=[6, 3, 3],
            material_item_ids=["mercury-lot"],
        )
        one, two = _source(owner, "disk-one"), _source(owner, "disk-two")
        assert one["source_id"] == "mage:floating_disk:1"
        assert two["source_id"] == "mage:floating_disk:2"
        assert one["cast_id"] != two["cast_id"] and one["cast_id"].startswith("srd_cast:")
        assert one["source_caster_id"] == two["source_caster_id"] == "mage"
        assert one["ongoing_duration"] is two["ongoing_duration"] is True
        loaded, load_command = _act(
            owner,
            "load_srd_floating_disk",
            actor_state="mage",
            source_id=one["source_id"],
            object_id="door",
            position=[0, 3, 3 + 1 / 12],
        )
        assert loaded.status is ActionStatus.COMMITTED, loaded.result
        saved = owner.snapshot("casting")
        carrier = supplies._row(saved.state, "door")["metadata"]["srd_spell_carrier"]
        assert carrier["cast_id"] == one["cast_id"] and carrier["source_caster_id"] == "mage"
        assert (
            supplies._row(saved.state, "door")["metadata"]["srd_object_utility"]["inventory_item"]
            == "cargo-token"
        )
        for force, source in (("disk-one", one), ("disk-two", two)):
            stamp = supplies._row(saved.state, force)["metadata"]["srd_object_force"]
            assert stamp["cast_id"] == source["cast_id"]
            assert stamp["source_id"] == source["source_id"]
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        for command, result in ((command1, cast1), (command2, cast2), (load_command, loaded)):
            assert reopened.execute_action(command) == result
        assert reopened.snapshot("casting") == saved
        with pytest.raises(MechanicsRejected, match="belonging to this caster"):
            _act(reopened, "dismiss_srd_spell", caster_state="hero", cast_id=one["cast_id"])
        assert reopened.snapshot("casting").state == saved.state
        dismissed, dismiss_command = _act(
            reopened,
            "dismiss_srd_spell",
            caster_state="mage",
            cast_id=one["cast_id"],
        )
        assert dismissed.status is ActionStatus.COMMITTED, dismissed.result
        after = reopened.snapshot("casting")
        assert supplies._row(after.state, "disk-one")["active"] is False
        assert supplies._row(after.state, "disk-two")["active"] is True
        assert supplies._row(after.state, "door")["metadata"]["srd_spell_carrier"] is None
        assert list(supplies._row(after.state, "door")["position"]) == [0, 3, 0]
        assert after.state[f"{SRD}.combat"] == saved.state[f"{SRD}.combat"]
        assert after.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["floating_disk"] == 0
        assert reopened.execute_action(dismiss_command) == dismissed
        assert reopened.snapshot("casting") == after
        dispelled, dispel_command = _cast(
            reopened,
            "dispel_magic",
            recovery_choice="object_utility",
            recovery_item="disk-two",
            recovery_source_id=two["source_id"],
        )
        assert dispelled.result["result_card"]["title"] == "Dispel Magic"
        final = reopened.snapshot("casting")
        assert supplies._row(final.state, "disk-two")["active"] is False
        assert not any(
            r.get("force_id") in {"disk-one", "disk-two"}
            for r in final.state[f"{SRD}.state"]["turn_effects"]
        )
        assert final.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["dispel_magic"] == 0
        assert reopened.execute_action(dispel_command) == dispelled
        assert reopened.snapshot("casting") == final


def test_real_cantrip_identity_survives_saved_hand_cargo_and_paid_selected_dispel(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "hand-source.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch)
        _prepare(
            owner,
            [
                {"spell": "mage_hand", "uses_per_day": 0},
                {"spell": "dispel_magic", "uses_per_day": 1},
            ],
        )
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
            inventory_item="cargo-token",
        )
        assert fact.status is ActionStatus.COMMITTED, fact.result
        cast, command = _cast(
            owner,
            "mage_hand",
            utility_output_id="hand",
            utility_form_id="hand-form",
            utility_point=[3, 0, 0],
            utility_operation="hold",
            utility_object_id="door",
        )
        source = _source(owner, "hand")
        assert source["spell_level"] == 0 and source["cast_id"].startswith("srd_cast:")
        assert source["source_caster_id"] == "mage" and source["ongoing_duration"] is True
        saved = owner.snapshot("casting")
        assert (
            supplies._row(saved.state, "door")["metadata"]["srd_spell_carrier"]["cast_id"]
            == source["cast_id"]
        )
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.execute_action(command) == cast
        assert reopened.snapshot("casting") == saved
        dispelled, dispel_command = _cast(
            reopened,
            "dispel_magic",
            recovery_choice="object_utility",
            recovery_item="hand",
            recovery_source_id=source["source_id"],
        )
        assert dispelled.result["result_card"]["title"] == "Dispel Magic"
        final = reopened.snapshot("casting")
        assert supplies._row(final.state, "hand")["active"] is False
        assert supplies._row(final.state, "door")["metadata"]["srd_spell_carrier"] is None
        assert (
            supplies._row(final.state, "door")["metadata"]["srd_object_utility"]["inventory_item"]
            == "cargo-token"
        )
        assert final.state[f"{SRD}.entities"]["mage"]["mage_hand_source_id"] is None
        assert reopened.execute_action(dispel_command) == dispelled
        assert reopened.snapshot("casting") == final


def test_legacy_private_force_is_not_a_finite_cast_and_forged_identity_rolls_back(monkeypatch):
    runtime_bytes = _runtime_bytes()
    with patch.object(hand, "_runtime_bytes", lambda: runtime_bytes):
        hand._mechanic.cache_clear()
        state = disk._state()
        hand._form(state)
        hand._mass(state)
        hand._cast(state)
        source = next(
            r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("force_id") == "hand"
        )
        assert source["cast_id"] == "" and source["source_caster_id"] is None
        assert source["ongoing_duration"] is False
        context = hand._run(
            "collect_srd_object_utility_dismiss_sources",
            state,
            caster_state="hero",
            cast_id="",
        )
        assert context["_utility_dismiss_sources"] == []
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="actual allocated"):
            hand._run(
                "cast_srd_mage_hand",
                state,
                caster_state="hero",
                spell="mage_hand",
                slot_level=0,
                output_id="forged-hand",
                form_id="hand-form",
                point=[5, 0, 0],
                operation="move",
                spell_cast_id="srd_cast:invented",
                spell_cast_owner="hero",
                actual_spell_level=0,
            )
        assert state == before
        allocated = hand._run(
            "begin_srd_spell_source_instance",
            state,
            caster_state="hero",
            spell="mage_hand",
            slot_level=0,
        )["_spell_source_cast_id"]
        hand._run(
            "cast_srd_mage_hand",
            state,
            caster_state="hero",
            spell="mage_hand",
            slot_level=0,
            output_id="allocated-hand",
            form_id="hand-form",
            point=[5, 0, 0],
            operation="move",
            spell_cast_id=allocated,
            spell_cast_owner="hero",
            actual_spell_level=0,
        )
        collected = hand._run(
            "collect_srd_object_utility_dismiss_sources",
            state,
            caster_state="hero",
            cast_id=allocated,
        )["_utility_dismiss_sources"]
        assert len(collected) == 1 and collected[0]["force_id"] == "allocated-hand"
        assert collected[0]["spell_level"] == 0 and collected[0]["source_caster_id"] == "hero"
        assert state[f"{SRD}.state"]["lasting_tick"] == 0
        hand._mechanic.cache_clear()


def test_owner_source_binding_repeats_without_altering_cast_or_retirement_guards():
    text = _runtime_bytes().decode()
    once = bind_source_consumers(text)
    twice = bind_source_consumers(once)
    assert _load_runtime(once) == _load_runtime(twice)
    original, result = _load_runtime(text)["tools"], _load_runtime(twice)["tools"]
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "retire_srd_object_utility_source",
        "move_srd_travel_subject_node",
        "control_srd_mage_hand",
    ):
        assert original[name] == result[name]


def test_public_ordinary_recovery_still_rejects_missing_target_without_spending(
    tmp_path,
    monkeypatch,
):
    with AuthorityOwner.open(tmp_path / "ordinary-recovery.db", "casting-owner") as owner:
        _campaign(owner, monkeypatch)
        _prepare(owner, [{"spell": "dispel_magic", "uses_per_day": 1}])
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="ordinary Recovery recipient"):
            _act(
                owner,
                "cast_spell_outside_combat",
                caster_state="mage",
                spell="dispel_magic",
                recovery_choice="none",
                verbal_component_available=True,
                somatic_component_available=True,
                material_component_available=True,
            )
        assert owner.snapshot("casting").state == before.state
        assert before.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["dispel_magic"] == 1
