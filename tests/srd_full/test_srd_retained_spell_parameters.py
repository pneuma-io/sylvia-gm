"""Private saved-hit declarations shrink while actual paid/reopened hit completion stays intact."""

import hashlib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.srd_retained_spell_parameters import OWNER, _unused_parameters, bind_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from tests import test_srd_spell_attack_reactions as hits
from tests import test_srd_spell_special_damage as special

BASE = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-retained-spell-parameters/input-1"))


def _calls(effects):
    for effect in effects:
        if effect.get("call") == OWNER:
            yield effect
        for key in ("on_true", "on_false"):
            yield from _calls(effect.get(key, []))
        if "for_each" in effect:
            yield from _calls(effect["for_each"]["do"])


@lru_cache
def _native(build):
    path, digest = {
        267: (
            BASE / "fc14-spells-20260930-wave19/build/"
            "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml",
            "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07",
        ),
        274: (
            BASE / "fc14-spells-20260930-wave8/damage-consumers/.scratch/"
            "fc14-node-parent-274/runtime.yaml",
            "9f9ab63e3a26ee866bdf2e24ff7693e2ae0a8d542aaba276deed611c7c69fd3f",
        ),
    }[build]
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest
    return data.decode()


def test_final_private_trim_preserves_every_effect_output_and_other_declaration():
    text = _native(274)
    before = _load_runtime(text)
    changed = bind_runtime(text)
    after = _load_runtime(changed)
    assert bind_runtime(changed) == changed
    removed = _unused_parameters(before["tools"][OWNER])
    assert len(removed) == 301
    assert [p["name"] for p in after["tools"][OWNER]["parameters"]] == [
        "spell", "caster_state", "lasting_state", "pending_action_id"
    ]
    for name, original in before["tools"].items():
        result = after["tools"][name]
        if name == OWNER:
            assert {key: value for key, value in original.items() if key != "parameters"} == {
                key: value for key, value in result.items() if key != "parameters"
            }
        else:
            restored = deepcopy(result)
            original_calls = list(_calls(original["effects"]))
            changed_calls = list(_calls(restored["effects"]))
            assert len(original_calls) == len(changed_calls)
            for old_call, new_call in zip(original_calls, changed_calls, strict=True):
                assert new_call["params"] == {
                    key: value for key, value in old_call["params"].items() if key not in removed
                }
                new_call["params"] = deepcopy(old_call["params"])
            assert restored == original
    assert {key: value for key, value in before.items() if key != "tools"} == {
        key: value for key, value in after.items() if key != "tools"
    }


@pytest.fixture
def actual_retained_owner(monkeypatch):
    text = bind_runtime(_native(267))
    runtime = _load_runtime(text)
    special.compiled_special.__wrapped__(
        monkeypatch, (text.encode(), runtime["definitions"]["spells"])
    )


@pytest.mark.parametrize("seed,critical", [(1, False), (5, True)])
def test_actual_paid_saved_spell_hit_retains_defense_cost_roll_and_completion(
    monkeypatch, actual_retained_owner, seed, critical
):
    hits.test_actual_spell_hit_opens_shield_window_without_early_damage_or_reroll(
        monkeypatch, seed, critical
    )


@pytest.mark.parametrize("shielded", [False, True])
def test_actual_saved_ray_rider_is_deferred_until_hit_defense_finishes(
    monkeypatch, actual_retained_owner, shielded
):
    hits.test_ray_of_frost_rider_waits_for_actual_spell_hit_completion(monkeypatch, shielded)


def test_private_trim_retains_late_consumer_root_alias_and_implicit_state_registration():
    tool = deepcopy(_load_runtime(_native(274))["tools"][OWNER])
    tool["effects"].append(
        {"call": "late_consumer", "params": {"actor": "{_caster_id}", "target": "{target}"}}
    )
    tool["parameters"].append(
        {"name": "registered_source", "type": "string", "from_state": "source.slot"}
    )
    unused = _unused_parameters(tool)
    assert not {"caster", "target", "registered_source"} & unused
