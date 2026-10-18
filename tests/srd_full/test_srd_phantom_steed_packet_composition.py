"""Phantom damage is captured identically by the actual Dream and Prismatic packet owners."""

from hashlib import sha256

from scripts.srd_full.srd_dream_spell import bind_runtime_consumers as bind_dream
from scripts.srd_full.srd_phantom_steed import bind_damage_consumers, bind_runtime_consumers
from scripts.srd_full.srd_prismatic_damage_packets import bind_runtime as bind_prism
from scripts.srd_full.srd_qualified_packet_implementations import COHORTS, bind_runtime, restore_runtime
from scripts.srd_full.srd_received_damage_lifecycle import bind_runtime as bind_received
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_warding_bond_damage import bind_runtime_consumers as bind_bond

from tests import test_srd_qualified_packet_implementations as packets


def test_early_steed_damage_preserves_final_packet_clones_and_exact_restoration():
    raw = packets.PARENT.read_bytes()
    assert sha256(raw).hexdigest() == packets.PARENT_SHA
    text = bind_prism(
        bind_bond(bind_dream(bind_received(packets.PARENT.read_text(encoding="utf-8"))))
    )
    text = bind_damage_consumers(text)
    assert bind_damage_consumers(text) == text
    text = bind_prism(bind_dream(text))
    before = _load_runtime(text)
    names = {name for cohort in COHORTS for name in cohort if name is not None}
    before_packets = {name: before["tools"][name] for name in names if name in before["tools"]}
    text = bind_runtime_consumers(text)
    after = _load_runtime(text)
    assert {name: after["tools"][name] for name in before_packets} == before_packets
    factored = bind_runtime(text)
    assert bind_runtime(factored) == factored
    assert _load_runtime(restore_runtime(factored)) == after
    graph = _load_runtime(factored)
    for core, *_ in COHORTS:
        effects = str(graph["tools"][core]["effects"])
        assert "observe_srd_phantom_steed_damage" in effects
        assert "drain_srd_dream_wakes" not in effects
