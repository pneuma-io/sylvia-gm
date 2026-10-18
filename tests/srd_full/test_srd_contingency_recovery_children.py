"""Retained recovery reuses native effects and spends its consumed diamond only at setup."""

import ast
import hashlib
import importlib
import os
import sys
import types
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full import srd_contingency_child_casting as child
from scripts.srd_full.srd_control_behaviors import _walk
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._types import ExecutionContext
from tests.srd_full import test_srd_contingency_child_casting as native
from tests.srd_full import test_srd_contingency_child_public as prior
from tests.srd_full import test_srd_contingency_combined_public as combined
from tests.srd_full import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_npc_spell_casting import _act

PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_RECOVERY_RUNTIME",
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/fc14-contingency-recovery-runtime/selected-runtime.yaml"),
    )
)
PROOF_SHA = "33f3d9ffa1ecf4f8ec5f0b6be5519c6460674f39cf535f8a9a8af0e4fbe5d001"


def _native_curses():
    """Read the actual current native owner; its public source validation remains intact."""
    package = types.ModuleType("fc14_native_recovery_proof")
    package.__path__ = [str(native.CURRENT / "scripts")]
    sys.modules.setdefault(package.__name__, package)
    return importlib.import_module(package.__name__ + ".srd_native_undead_curses")


def _actual_native_recovery_suffix(text):
    """Use the unchanged native recovery suffix without rebuilding unrelated death owners."""
    owner = _native_curses()
    source = Path(owner.__file__)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "04843839e0fc717dc1ffd2a651dafe2802d3106d1218b5fdca607c54f296de9b"
    )
    owner._validate_sources()
    function = next(
        n
        for n in ast.parse(source.read_text()).body
        if isinstance(n, ast.FunctionDef) and n.name == "extend_runtime"
    )
    start = next(
        i
        for i, n in enumerate(function.body)
        if isinstance(n, ast.For)
        and isinstance(n.iter, ast.Tuple)
        and ast.literal_eval(n.iter)
        == ("remove_srd_recovery_curses", "remove_srd_one_recovery_curse")
    )
    assert isinstance(function.body[-1], ast.Return)
    statements = function.body[start:-1]
    namespace = {
        "tools": owner._load_runtime(text)["tools"],
        "deepcopy": deepcopy,
        "_set": owner._set,
        "_call": owner._call,
        "changed": {
            "read_srd_native_mummy_rot": owner._native_source_tools()["read_srd_native_mummy_rot"]
        },
    }
    exec(compile(ast.Module(body=statements, type_ignores=[]), str(source), "exec"), namespace)
    return owner._replace_tools(text, namespace["changed"])


def test_actual_recovery_dispatch_preserves_native_effects_and_self_source_bindings():
    tools, definitions = native.actual.__wrapped__()
    assert {"remove_curse", "greater_restoration", "dispel_evil_and_good"} <= set(
        child._selected(definitions)
    )
    recovery = tools["cast_srd_recovery"]
    retained = child._retained_family(recovery)
    assert retained["effects"] == recovery["effects"]
    assert child._retained_family(retained) == retained
    original = next(
        n
        for n in _walk(tools["cast_spell"]["effects"])
        if n.get("call") == "cast_srd_recovery" and "target_state" in n["params"]
    )
    call = child._dispatch(original)
    assert call["export_context"] == original["export_context"]
    assert call["params"]["target_state"] == "{_target_state_id}"
    assert call["params"]["recovery_choice"] == "{_contingency_child.effect_choice}"
    assert call["params"]["recovery_item"] == "{_contingency_child.recovery_item}"
    assert call["params"]["recovery_source_id"] == "{_contingency_child.recovery_source_id}"
    assert call["params"]["actual_spell_level"] == "{_contingency_child.slot_level}"


def test_stale_recovery_selection_has_no_effect_before_native_strict_selection():
    source = {
        "installed": True,
        "control_profile": "curse",
        "sequence": 7,
        "target_state_id": "hero",
    }
    child_record = {
        "effect_choice": "curse",
        "recovery_source_id": "curse_1",
        "recovery_source_sequence": 7,
        "recovery_item": "",
    }
    for current, allowed in (
        (source, True),
        (dict(source, sequence=8), False),
        (dict(source, installed=False), False),
    ):
        context = ExecutionContext(
            params={
                "target_state": {"timed_condition_source_ids": ["curse_1"]},
                "lasting_state": {"timed_condition_sources": {"curse_1": current}},
            },
            vars={
                "_target_state_id": "hero",
                "_contingency_child": child_record,
                "_contingent_effect_eligible": True,
            },
        )
        execute_effects(child._recovery_selection_eligibility(), context)
        assert context.vars["_contingent_effect_eligible"] is allowed
    assert not any("require" in n for n in _walk(child._recovery_selection_eligibility()))


def test_long_setup_preserves_selected_source_sequence_and_recovery_choices():
    tools, definitions = native.actual.__wrapped__()
    original = tools["prepare_srd_contingent_child"]
    preparation = deepcopy(original)
    child._prepare(preparation, child._selected(definitions))
    witness = next(
        n for n in preparation["effects"] if n.get("condition") == child.RECOVERY_SOURCE + " != ''"
    )
    for sequence, allowed in ((7, True), (8, False)):
        context = ExecutionContext(
            params={
                "contingent_recovery_source_id": "curse_1",
                "contingent_effect_choice": "curse",
                "caster_state": {
                    "concentration_effect_family": "extended_casting",
                    "extended_casting": {"spell": "contingency"},
                    "contingency_prepared": {
                        "recovery_source_id": "curse_1",
                        "recovery_source_sequence": 7,
                    },
                },
                "lasting_state": {
                    "timed_condition_sources": {
                        "curse_1": {
                            "installed": True,
                            "sequence": sequence,
                            "target_state_id": "hero",
                            "control_profile": "curse",
                        }
                    }
                },
            },
            vars={"_caster_state_id": "hero"},
        )
        if allowed:
            execute_effects([witness], context)
            assert context.vars["_contingent_recovery_source_sequence"] == 7
        else:
            with pytest.raises(Exception, match="Keep the actual recovery source"):
                execute_effects([witness], context)
    saved = next(
        n["set"]["value"]
        for n in preparation["effects"]
        if n.get("set", {}).get("field") == child.PREPARED
    )
    assert saved["recovery_source_sequence"] == "_contingent_recovery_source_sequence"
    assert saved["recovery_item"] == child.RECOVERY_ITEM
    once = deepcopy(preparation)
    child._prepare(preparation, child._selected(definitions))
    assert preparation == once


def test_late_actual_native_owner_is_in_retained_recovery_and_source_selection():
    parent = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-contingency-recovery-children/selected-runtime.yaml")
    )
    assert hashlib.sha256(parent.read_bytes()).hexdigest() == (
        "0a951053d87b0f2110ae3497e707a377ffffa60051fc598ec69bb5698459582d"
    )
    text = _actual_native_recovery_suffix(parent.read_text())
    tools = _load_runtime(text)["tools"]
    original = deepcopy(tools["cast_srd_recovery"])
    emitted = child.bind_release(text)
    final = _load_runtime(emitted)["tools"]
    retained = final["release_srd_contingent_recovery"]
    assert retained["effects"] == original["effects"]
    assert any(
        n.get("set", {}).get("field") == "target_state.native_undead_max_hp_reduction_receipts"
        for n in _walk(retained["effects"])
    )
    assert final["cast_srd_recovery"] == original
    for name in (child.RELEASE, "prepare_srd_contingent_child"):
        assert any(
            n.get("call") == "read_srd_native_mummy_rot" for n in _walk(final[name]["effects"])
        )
    assert not any(
        "native undead maximum-HP restoration needs" in str(n)
        for n in _walk(final[child.RELEASE]["effects"])
    )
    assert child.bind_release(emitted) == emitted


@pytest.fixture(scope="module")
def actual_archive():
    assert hashlib.sha256(PROOF.read_bytes()).hexdigest() == PROOF_SHA
    original = combined.PROOF
    combined.PROOF = PROOF
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    try:
        yield combined._selected_archive()
    finally:
        combined.PROOF = original
        combined._runtime.cache_clear()
        combined._selected_archive.cache_clear()


def test_paid_restoration_defers_native_exhaustion_removal_and_pays_diamond_once(
    tmp_path, monkeypatch, actual_archive
):
    state, data, parameters, lots, level = prior._case(monkeypatch, "greater_restoration")
    data["classes"] = [{"id": "wizard", "level": 11}, {"id": "cleric", "level": 9}]
    data["level"] = 20
    data["wizard_spellbook"].remove("greater_restoration")
    data["spellcasting_grants"][0]["spells"].remove("greater_restoration")
    data["spellcasting_grants"].append(
        {"source": "cleric", "ability": "wisdom", "spells": ["greater_restoration"]}
    )
    hero = state[f"{combined.SRD}.entities"]["hero"]
    hero["resources"][f"spell_slot_{level}"] = 1
    hero["exhaustion_level"] = 2
    parameters["contingent_effect_choice"] = "exhaustion"
    monkeypatch.setattr(public_casting, "_spell_case", lambda _: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", lambda: actual_archive)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    database = tmp_path / "restoration.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "contingency")
        parent_receipt, parent_command = combined._cast(owner, parameters, 100)
        armed = owner.snapshot("casting")
        actor = armed.state[f"{combined.SRD}.entities"]["hero"]
        source_id = actor["contingency_source_id"]
        assert actor["exhaustion_level"] == 2
        assert actor["resources"][f"spell_slot_{level}"] == 0
        quantities = {r["id"]: r["quantity"] for r in actor["spell_materials"]}
        assert all(quantities[lot] == 2 for lot in lots)
        before_resources = deepcopy(actor["resources"])
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == armed
        monkeypatch.setattr(dice, "_rng", Random(0))
        missile = {
            key: value for key, value in parameters.items() if not key.startswith("contingent_")
        } | {
            "spell": "magic_missile",
            "slot_resource": "spell_slot_2",
            "target": "hero",
            "material_item_ids": [],
        }
        hit, hit_command = _act(owner, "cast_spell_outside_combat", **missile)
        assert hit.status is ActionStatus.COMMITTED, hit.result
        released = owner.snapshot("casting")
        actor = released.state[f"{combined.SRD}.entities"]["hero"]
        parent = released.state[f"{combined.SRD}.state"]["contingency_sources"][source_id]
        assert parent["child_effect_applied"] is True and parent["status"] == "released"
        assert parent["trigger_hp_before"] > parent["trigger_hp_after"]
        assert actor["exhaustion_level"] == 1
        assert actor["concentration_spell_id"] is None
        assert {r["id"]: r["quantity"] for r in actor["spell_materials"]} == quantities
        expected = dict(before_resources)
        expected["spell_slot_2"] -= 1
        assert dict(actor["resources"]) == expected
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == released
        monkeypatch.setattr(dice, "roll", lambda *_: pytest.fail("A saved retry must not reroll."))
        for command, receipt in ((parent_command, parent_receipt), (hit_command, hit)):
            assert owner.execute_action(command) == receipt
            assert owner.snapshot("casting") == released
