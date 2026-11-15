"""Structural completion proof on actual admitted packets and frozen paid Contingency leaves."""

import hashlib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_contingency_completion import (
    DAMAGE_OWNERS,
    DRAIN,
    OBSERVER,
    SETTLEMENT_ROOTS,
    _graph,
    _reachable,
    bind_damage,
    bind_runtime,
)
from scripts.srd_full.srd_control_behaviors import _walk
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-contingency-completion/ruleset.yaml")
)
PROOF = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-contingency-completion/contingency-removal-proof-runtime.yaml")
)


class NoAliases(getattr(yaml, "CSafeDumper", yaml.SafeDumper)):
    def ignore_aliases(self, data):
        return True


@lru_cache
def _parents():
    native, proof = NATIVE.read_bytes(), PROOF.read_bytes()
    assert hashlib.sha256(native).hexdigest() == (
        "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    assert hashlib.sha256(proof).hexdigest() == (
        "2888e319ee414b136422c2759ea2081087f3cbc223e3dbf7142e2dfbeda1a5be"
    )
    runtime, paid = _load_runtime(native.decode()), _load_runtime(proof.decode())
    # Actual frozen source/child owners define the release graph; native packets stay native.
    runtime["tools"].update(
        {name: tool for name, tool in paid["tools"].items() if name not in runtime["tools"]}
    )
    return runtime


def _text(runtime):
    return yaml.dump(runtime, Dumper=NoAliases, sort_keys=False)


def test_actual_post_hp_observers_share_existing_group_and_repeat():
    before = _parents()
    text = bind_damage(_text(before))
    assert bind_damage(text) == text
    after = _load_runtime(text)
    for name in DAMAGE_OWNERS:
        original, joined = before["tools"][name], after["tools"][name]
        assert len(joined["effects"]) == len(original["effects"])
        calls = [node for node in _walk(joined["effects"]) if node.get("call") == OBSERVER]
        assert len(calls) == 1
        assert calls[0]["params"] == {"target_state": "{target}", "hp_before": "{_hp_before}"}
        group_index = next(
            i for i, effect in enumerate(joined["effects"]) if OBSERVER in str(effect)
        )
        assert any(
            node.get("apply_damage") == {"target": "target", "amount": "_hp_damage"}
            for node in _walk(joined["effects"][:group_index])
        )
        group = joined["effects"][group_index]
        assert group["on_true"][0]["on_true"] == original["effects"][group_index]["on_true"]
        assert "target.has_contingency == true" in group["condition"]
        assert len(group["condition"]) <= 500
    assert {
        name for name in after["tools"] if before["tools"][name] != after["tools"][name]
    } == set(DAMAGE_OWNERS)


def test_actual_release_closure_excludes_receivers_clock_and_child_costs():
    text = bind_damage(_text(_parents()))
    before = _load_runtime(text)
    graph = _graph(before["tools"])
    release_closure = _reachable(graph, {DRAIN})
    assert "cast_spell_timed_senses" in release_closure
    assert "resolve_srd_respiration_interval" in release_closure
    assert OBSERVER in release_closure
    # This proof supplies only real outer public roots; no synthetic packet or trigger verdict.
    public = {"cast_spell", "cast_spell_outside_combat", "character_attack", "creature_attack"}
    joined = bind_runtime(text, public)
    assert bind_runtime(joined, public) == joined
    after = _load_runtime(joined)
    changed = {name for name in after["tools"] if before["tools"][name] != after["tools"][name]}
    assert "cast_spell" in changed
    assert not changed & _reachable(graph, SETTLEMENT_ROOTS & graph.keys())
    final = _graph(after["tools"])
    assert not changed & _reachable(final, {DRAIN})
    for name in changed:
        assert after["tools"][name]["effects"][:-1] == before["tools"][name]["effects"]
        assert after["tools"][name]["effects"][-1]["condition"].endswith(
            "contingency_release_queue) > 0"
        )
    for name in release_closure & before["tools"].keys():
        assert after["tools"][name] == before["tools"][name]


def test_receiving_rejects_card_snapshot_without_real_hp_transition():
    runtime = deepcopy(_parents())
    owner = runtime["tools"]["apply_srd_damage"]
    owner["effects"] = [effect for effect in owner["effects"] if "apply_damage" not in effect]
    with pytest.raises(ValueError, match="actual completed HP transition"):
        bind_damage(_text(runtime))
