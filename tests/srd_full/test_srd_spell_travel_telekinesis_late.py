"""Late fall emission preserves every final owner and admits the actual paid release journey."""

from copy import deepcopy

import pytest
import yaml
from scripts.srd_full.srd_spell_travel_telekinesis import emit_late_handoff, telekinesis_tools
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper
from scripts.srd_full.srd_timed_senses import _replace_tools

from tests import test_srd_spell_travel_telekinesis_fall as fall


def _delayed_allocator_text():
    runtime = deepcopy(_load_runtime(fall.RUNTIME_PATH.read_bytes()))
    allocator = runtime["tools"].pop("begin_srd_scene_fall")
    runtime["tools"].pop("handoff_srd_telekinesis_releases", None)
    text = yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False, width=105)
    return text, allocator


def test_missing_allocator_preserves_exact_early_text():
    text, _ = _delayed_allocator_text()
    assert "handoff_srd_telekinesis_releases" not in telekinesis_tools(_load_runtime(text)["tools"])
    assert emit_late_handoff(text) == text


def test_late_handoff_requires_its_existing_source_owners():
    text = "tools:\n  begin_srd_scene_fall: {}\nprocedures: {}\n"
    with pytest.raises(ValueError, match="requires existing owners"):
        emit_late_handoff(text)


def test_late_handoff_changes_only_one_definition_and_is_idempotent():
    text, allocator = _delayed_allocator_text()
    ready = _replace_tools(text, {"begin_srd_scene_fall": allocator})
    before = _load_runtime(ready)
    emitted = emit_late_handoff(ready)
    after = _load_runtime(emitted)
    leaf = after["tools"].pop("handoff_srd_telekinesis_releases")
    assert after == before
    assert leaf == telekinesis_tools(before["tools"])["handoff_srd_telekinesis_releases"]
    assert emit_late_handoff(emitted) == emitted


@pytest.fixture
def late_actual_fall_scope(monkeypatch, tmp_path):
    text, allocator = _delayed_allocator_text()
    assert emit_late_handoff(text) == text
    ready = _replace_tools(text, {"begin_srd_scene_fall": allocator})
    path = tmp_path / "late-fall-runtime.yaml"
    path.write_text(emit_late_handoff(ready), encoding="utf-8", newline="\n")
    monkeypatch.setattr(fall, "RUNTIME_PATH", path)
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def test_late_emitted_leaf_real_paid_release_reopens_and_lands(
    late_actual_fall_scope, tmp_path, monkeypatch
):
    fall.test_paid_telekinesis_release_real_fall_reopens_retries_and_lands(
        late_actual_fall_scope, tmp_path, monkeypatch, feather=False
    )
