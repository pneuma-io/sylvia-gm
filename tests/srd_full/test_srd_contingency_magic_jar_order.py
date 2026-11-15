"""Contingency joins the genuine receiving body before Magic Jar copies and wraps it."""

import hashlib
import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.srd_contingency_completion import DAMAGE_OWNERS, OBSERVER, bind_damage
from scripts.srd_full.srd_control_behaviors import _walk
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from tests import test_srd_contingency_completion as completion

ROOT = Path(__file__).resolve().parents[2]
CAPTURE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-contingency-magic-jar-order/input-1")
)


def _parent(name, digest):
    path = CAPTURE / "parents/scripts" / f"{name}.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    spec = importlib.util.spec_from_file_location(f"scripts._contingency_order_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _observer(sequence):
    calls = [node for node in _walk(sequence) if node.get("call") == OBSERVER]
    assert len(calls) == 1
    assert calls[0]["params"] == {"target_state": "{target}", "hp_before": "{_hp_before}"}
    group = next(effect for effect in sequence if OBSERVER in str(effect))
    before = list(_walk(sequence[: sequence.index(group)]))
    assert any(node.get("set", {}).get("field") == "_hp_before" for node in before)
    assert any(
        node.get("apply_damage") == {"target": "target", "amount": "_hp_damage"} for node in before
    )
    return group


def test_pre_jar_contingency_is_retained_in_wrapped_and_occupied_body_with_receiving_lifecycle():
    raw = (CAPTURE / "excluded-root43-prefix.yaml").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "7119e686568681da02851a6e2e06981e5723d9d0a3cb94ab33d75d614e36724a"
    )
    runtime = _load_runtime(raw.decode())
    paid = _load_runtime(completion.PROOF.read_text())
    # Only actual immutable paid Contingency source/release leaves are overlaid. Existing native
    # packet bodies remain the real normal-prefix owners rather than fabricated fixture groups.
    runtime["tools"].update(
        {
            name: deepcopy(tool)
            for name, tool in paid["tools"].items()
            if "contingenc" in name or name == "release_srd_contingent_spell"
        }
    )
    text = completion._text(runtime)
    jar = _parent(
        "srd_magic_jar_consumers",
        "354a9161c7574fc0e45186340c7286111066b91f5f26e9fecaa3146ae0ba319a",
    )
    lifecycle = _parent(
        "srd_received_damage_lifecycle",
        "c8c197094483547eb1cfbe9890e43eb3360e7e97709468712512172acf66714c",
    )
    # The old order really fails on the current owner rather than a synthetic wrapped dictionary.
    with pytest.raises(ValueError, match="one completed receiving group: apply_srd_damage"):
        bind_damage(jar.bind_consumers(text))
    marked = bind_damage(text)
    before_jar = _load_runtime(marked)
    joined = lifecycle.bind_runtime(jar.bind_consumers(marked))
    tools = _load_runtime(joined)["tools"]
    for name in DAMAGE_OWNERS:
        sequence = tools[name]["effects"]
        if name == "apply_srd_damage":
            assert len(sequence) == 1
            wrapper = sequence[0]
            assert "target.magic_jar_role == 'host_soul'" in wrapper["condition"]
            assert wrapper["on_false"] == before_jar["tools"][name]["effects"]
            sequence = wrapper["on_false"]
        _observer(sequence)
    body = tools["apply_srd_magic_jar_body_damage"]["effects"]
    assert _observer(body) == _observer(tools["apply_srd_damage"]["effects"][0]["on_false"])
    occupied = tools["apply_srd_magic_jar_occupied_damage"]["effects"]
    body_calls = [
        node for node in _walk(occupied) if node.get("call") == "apply_srd_magic_jar_body_damage"
    ]
    assert len(body_calls) == 1
    assert body_calls[0]["params"]["target"] == "{target}"
    redirects = [
        node
        for node in _walk(tools["apply_srd_damage"]["effects"][0]["on_true"])
        if node.get("call") == "apply_srd_magic_jar_occupied_damage"
    ]
    assert len(redirects) == 1
    assert redirects[0]["params"]["target"] == "{_magic_jar_target_id}"
    # The copied body observes once; the authenticated occupied wrapper delegates without
    # installing a second observer or a child-release drain inside receiving damage.
    assert not any(node.get("call") == OBSERVER for node in _walk(occupied))
    assert "drain_srd_contingency_releases" not in str(occupied)
    for name in ("apply_srd_composite_damage", "apply_srd_qualifying_composite_damage"):
        assert any(
            node.get("call") == "interrupt_srd_rest" for node in _walk(tools[name]["effects"])
        )
    assert lifecycle.bind_runtime(joined) == joined
