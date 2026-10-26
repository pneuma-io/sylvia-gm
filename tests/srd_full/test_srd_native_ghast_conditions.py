"""Authenticate the Ghast's actual printed physical riders, including saved reuse."""

import importlib.util
import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_native_condition_sources import bind_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm import dice
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_native_condition_sources as native


@lru_cache
def _runtime():
    root = Path(__file__).resolve().parents[2]
    dependency = root / ".scratch/native-ghast-parent-v1/summoning.py"
    spec = importlib.util.spec_from_file_location("scripts._ghast_native_producer", dependency)
    summoning = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(summoning)
    summoning.__file__ = str(root / "scripts/srd_spell_summoning.py")
    text = Path(
        os.environ.get(
            "FC14_NATIVE_CONDITION_RUNTIME",
            root / ".scratch/secure-material-parent-v1/actual-runtime.yaml",
        )
    ).read_text()
    text = summoning.bind_animated_undead_turn_consumers(summoning.extend_runtime(text))
    text = bind_runtime(text)
    assert bind_runtime(text) == text
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"]["create_undead"] = summoning.CREATED_UNDEAD_PROFILE
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | {"create_undead"})
    pending = [
        "create_srd_created_ghast_body",
        "resolve_srd_created_ghast_claw",
        "resolve_srd_created_ghast_stench",
        "check_srd_native_condition_source",
        "refresh_srd_timed_condition_target",
        "install_srd_actor_spell_modifier",
        "install_srd_timed_condition_source",
        "retire_srd_timed_condition_source",
        "expire_srd_created_ghast_claw_sources",
        "expire_srd_created_ghast_stench_sources",
        "apply_srd_damage",
        "advance_srd_lasting_clock",
        "command_srd_animated_undead",
        "start_group_combat_encounter",
        "begin_combat_turn",
        "end_combat_turn",
        "advance_group_combat_encounter_turn",
    ]
    selected = {}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected[name] = runtime["tools"][name]
        stack = [selected[name]]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if "call" in item:
                    pending.append(item["call"])
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    closure = runtime | {"tools": selected}
    assert not (errors := validate_ruleset_data(closure)), errors
    return closure, yaml.dump(closure, Dumper=_RuntimeDumper, sort_keys=False).encode()


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    native._mechanic.cache_clear()
    monkeypatch.setattr(native, "_runtime", _runtime)
    native._owner.__wrapped__(monkeypatch)
    yield
    native._mechanic.cache_clear()


def _authenticate(state, data, source_id):
    return native._run(
        "check_srd_native_condition_source", state, data, source_id=source_id, lasting_state="state"
    )["_native_condition_source"]


def test_ghast_claw_affects_real_elf_through_freedom_and_saved_attacker_death(monkeypatch):
    state, data = native._native_body(form_id="ghast")
    data["species"] = "elf"
    native._freedom(state, data)
    context = native._claw(state, data, monkeypatch, form_id="ghast")
    sid = context["_ghast_claw_source_id"]
    assert context["_ghast_claw_save_outcome"] == "failure" and _authenticate(state, data, sid)
    native._run(
        "apply_srd_damage",
        state,
        data,
        target="ghoul_0",
        amount=100,
        adjustment=0,
        damage_type="radiant",
        critical_hit=False,
    )
    state = json.loads(json.dumps(state))
    native._run(
        "refresh_srd_timed_condition_target",
        state,
        data,
        target_state="hero",
        lasting_state="state",
    )
    hero = state[f"{native.SRD}.entities"]["hero"]
    source = state[f"{native.SRD}.state"]["timed_condition_sources"][sid]
    assert source["installed"] and not source["suppressed"] and _authenticate(state, data, sid)
    assert hero["spell_freedom_of_movement"] and "paralyzed" in hero["conditions"]
    native._run("end_combat_turn", state, data)
    for _ in range(3):
        native._run("advance_group_combat_encounter_turn", state, data)
        actor = state[f"{native.SRD}.combat"]["current_actor"]
        native._run("begin_combat_turn", state, data, actor=actor)
        native._run("end_combat_turn", state, data)
        if actor == "hero":
            break
    else:
        pytest.fail("The actual target never completed its next turn.")
    assert not state[f"{native.SRD}.state"]["timed_condition_sources"][sid]["installed"]
    assert "paralyzed" not in state[f"{native.SRD}.entities"]["hero"]["conditions"]


def test_ghast_native_witness_rejects_pool_reuse_and_wrong_body_attack_pair(monkeypatch):
    state, data = native._native_body(form_id="ghast")
    sid = native._claw(state, data, monkeypatch, form_id="ghast")["_ghast_claw_source_id"]
    assert _authenticate(state, data, sid)
    for change in ("hash", "attack", "sequence", "registry"):
        altered = deepcopy(state)
        source = altered[f"{native.SRD}.state"]["timed_condition_sources"][sid]
        if change == "hash":
            source["native_body_block_sha256"] = "0" * 64
        elif change == "attack":
            source["native_attack"] = "Stench"
        elif change == "sequence":
            source["native_body_source_sequence"] += 1
        else:
            altered[f"{native.SRD}.state"]["created_actor_records"]["ghoul_0"]["individual"] = (
                "other"
            )
        assert not _authenticate(altered, data, sid)
    native._run(
        "retire_srd_timed_condition_source", state, data, source_id=sid, lasting_state="state"
    )
    current = native._run(
        "install_srd_timed_condition_source",
        state,
        data,
        target_state="hero",
        caster_state="guard",
        lasting_state="state",
        spell="hold_person",
        condition="paralyzed",
        duration_rounds=10,
        save_dc=10,
        save_ability="wisdom",
        repeat_save_timing="none",
    )
    assert current["_timed_condition_source_id"] == sid and not _authenticate(state, data, sid)
    native._freedom(state, data)
    assert "paralyzed" not in state[f"{native.SRD}.entities"]["hero"]["conditions"]
    assert state[f"{native.SRD}.state"]["timed_condition_sources"][sid]["suppressed"]


def test_actual_stench_is_native_but_not_a_freedom_paralysis_projection(monkeypatch):
    state, data = native._native_body(form_id="ghast")
    native._freedom(state, data)

    class FailedSave:
        def randint(self, low, high):
            assert (low, high) == (1, 20)
            return 1

    monkeypatch.setattr(dice, "_rng", FailedSave())
    native._run("end_combat_turn", state, data)
    for _ in range(3):
        native._run("advance_group_combat_encounter_turn", state, data)
        actor = state[f"{native.SRD}.combat"]["current_actor"]
        native._run("begin_combat_turn", state, data, actor=actor)
        if actor == "hero":
            break
        native._run("end_combat_turn", state, data)
    hero = state[f"{native.SRD}.entities"]["hero"]
    (sid,) = hero["ghast_stench_source_ids"]
    assert _authenticate(state, data, sid) and "poisoned" in hero["conditions"]
    assert "paralyzed" not in hero["conditions"]
    state = json.loads(json.dumps(state))
    assert _authenticate(state, data, sid)
    native._run(
        "refresh_srd_timed_condition_target",
        state,
        data,
        target_state="hero",
        lasting_state="state",
    )
    assert "poisoned" in state[f"{native.SRD}.entities"]["hero"]["conditions"]
