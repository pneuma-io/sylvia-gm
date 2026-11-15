"""Fresh normal emission supplies the authentic Mummy mixed-routine prerequisite."""

import hashlib
import os
from pathlib import Path

from scripts import srd_native_mummy_fist as fist
from scripts import srd_native_mummy_routine as routine
from scripts.srd_full.srd_native_mummy_actions import _glare_tools
from scripts.srd_full.srd_parameter_declarations import restore_runtime
from scripts.srd_full.srd_spell_summoning import (
    _created_undead_additional_profiles,
    _created_undead_hooks,
    _walk,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

RUNTIME = Path(
    os.environ.get(
        "SYLVIA_NATIVE_MUMMY_EMISSION_RUNTIME",
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/sylvia-native-mummy-emission-runtime/wight-public-parent-runtime.yaml"),
    )
)


def test_fresh_created_undead_emission_supplies_mummy_constructor_and_routine(monkeypatch):
    raw = RUNTIME.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "00a7de081d9880701c9bd84f339b10b53ff7eb99a945fc5e39f19b5e325099d5"
    )
    tools = _load_runtime(restore_runtime(raw.decode()))["tools"]
    for name in (
        "prepare_srd_source_mummy",
        "finish_srd_mummy_source_actor",
        "spend_srd_native_mummy_action_component",
    ):
        tools.pop(name, None)
    emitted = _created_undead_hooks(tools)
    assert {"prepare_srd_source_mummy", "finish_srd_mummy_source_actor"} <= emitted.keys()
    prepare = emitted["prepare_srd_source_mummy"]
    finish = emitted["finish_srd_mummy_source_actor"]
    assert any(e.get("call") == "finish_srd_mummy_source_actor" for e in _walk(prepare["effects"]))
    source = _created_undead_additional_profiles()["mummy"]
    assert any(
        e.get("set", {}).get("field") == "actor_state.created_source_block_sha256"
        and e["set"]["value"] == repr(source["source_stat_block_sha256"])
        for e in _walk(finish["effects"])
    )
    tools.update(emitted)
    tools.update(_glare_tools(tools))

    # Compose the affected existing owners directly, without a package build or replay.
    def retain(_text, changed):
        tools.update(changed)
        return _text

    for owner in (fist, routine):
        monkeypatch.setattr(owner, "_load_runtime", lambda _text: {"tools": tools})
        monkeypatch.setattr(owner, "_replace_tools", retain)
    fist.bind_native_mummy_fist_attack_consumers("actual-current-fragment")
    routine.extend_runtime("actual-current-fragment")
    assert "spend_srd_native_mummy_action_component" in tools
    assert tools["finish_srd_mummy_source_actor"]["effects"][-1] == {
        "set": {
            "field": "actor_state.multiattack",
            "value": [
                {"attack": "'Rotting Fist'", "count": 2},
                {"attack": "'Dreadful Glare'", "count": 1},
            ],
        }
    }
