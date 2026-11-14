"""Real outer action owners hand queued suspension releases to the saved fall owner."""

import hashlib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_spells import SPELL_SUPPORT_TOOLS
from scripts.srd_full.srd_reaction_spells import bind_pending_support_fences
from scripts.srd_full.srd_runtime_defaults import compact_runtime_defaults
from scripts.srd_full.srd_spell_exile_composition import bind_runtime
from scripts.srd_full.srd_spell_travel_telekinesis import emit_late_handoff
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests import test_srd_spell_travel_telekinesis_pending as pending

PARENT = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-telekinesis-completion/runtime.yaml")
)
PARENT_SHA = "b1377f6e33e7f2ff350486f0ded43008f121863f9f1d6933cf91445b2a83e81c"
MARKER = "state.has_telekinesis_pending_releases == true"


@lru_cache
def _completion_scope():
    raw = PARENT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PARENT_SHA
    text = raw.decode()
    tools = _load_runtime(text)["tools"]
    changed = {}
    # Remove only the two historical proof adapters. Actual retirement, clock,
    # paid-cast, pending and fall definitions remain their immutable source bytes.
    for name in ("end_concentration", "advance_srd_lasting_time"):
        tool = deepcopy(tools[name])
        tool["effects"] = [
            effect
            for effect in tool["effects"]
            if effect.get("condition") != MARKER
            and effect.get("call") != "handoff_srd_telekinesis_releases"
        ]
        changed[name] = tool
    text = _replace_tools(text, changed)
    text = emit_late_handoff(text)
    text = bind_pending_support_fences(text)
    text = bind_runtime(text, SPELL_SUPPORT_TOOLS)
    return compact_runtime_defaults(text)


def test_final_completion_binds_actual_release_owners_without_a_call_cycle():
    text = _completion_scope()
    tools = _load_runtime(text)["tools"]
    for name in (
        "end_concentration",
        "advance_srd_lasting_time",
        "use_srd_telekinesis",
    ):
        assert any(
            effect.get("condition") in {MARKER, MARKER.replace("state.", "lasting_state.")}
            and effect.get("on_true", [{}])[0].get("call") == "handoff_srd_telekinesis_releases"
            for effect in tools[name]["effects"]
        ), name
    assert _load_runtime(bind_runtime(text, SPELL_SUPPORT_TOOLS)) == _load_runtime(text)
    from scripts.srd_full.srd_spell_trigger_composition import _effect_lists

    graph = {
        name: {
            effect["call"]
            for branch in _effect_lists(tool["effects"])
            for effect in branch
            if "call" in effect
        }
        for name, tool in tools.items()
    }
    visited = set()

    def visit(name, ancestors):
        assert name not in ancestors, (name, ancestors)
        if name in visited:
            return
        assert name in graph, name
        for child in graph[name]:
            visit(child, ancestors | {name})
        visited.add(name)

    for name in graph:
        visit(name, set())


@pytest.fixture
def real_completion_scope(monkeypatch, tmp_path):
    path = tmp_path / "actual-completion.yaml"
    path.write_text(_completion_scope(), encoding="utf-8", newline="\n")
    monkeypatch.setattr(fall, "RUNTIME_PATH", path)
    monkeypatch.setattr(
        fall,
        "EXTRA_PUBLIC_TOOLS",
        frozenset({"use_srd_telekinesis", "record_srd_telekinesis_object_size"}),
    )
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def test_actual_public_concentration_end_reopens_retries_and_lands(
    real_completion_scope, tmp_path, monkeypatch
):
    fall.test_paid_telekinesis_release_real_fall_reopens_retries_and_lands(
        real_completion_scope, tmp_path, monkeypatch, feather=False
    )


def test_actual_public_clock_release_reopens_fences_and_lands(
    real_completion_scope, tmp_path, monkeypatch
):
    pending.test_real_paid_release_fences_later_tools_reopens_then_allows_after_landing(
        real_completion_scope, tmp_path, monkeypatch
    )
