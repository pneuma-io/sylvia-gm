"""Exact retained snapshots preserve nulls, source choices and real pending actions."""

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_retained_nullable_captures import OWNERS, ROW, ROWS, bind_runtime, restore_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.runtime import _effect_executor
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests import test_srd_spell_special_damage as special
from tests.srd_full.test_srd_received_damage_lifecycle import PARENT, PARENT_SHA, _fragment, _nodes


@lru_cache
def _graphs():
    raw = PARENT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PARENT_SHA
    text = raw.decode()
    original = _load_runtime(text)
    composed = bind_runtime(text)
    assert bind_runtime(composed) == composed
    batched = _load_runtime(composed)
    assert _load_runtime(restore_runtime(composed)) == original
    assert bind_runtime(restore_runtime(composed)) == composed
    assert not validate_ruleset_data(batched)
    return original, batched


def test_every_public_schema_and_all_unowned_effects_remain_exact():
    original, batched = _graphs()
    assert _nodes(original) == 530817
    assert _nodes(batched) == 521108
    for name, before in original["tools"].items():
        after = batched["tools"][name]
        if name not in OWNERS:
            assert before == after
        else:
            assert {k: v for k, v in before.items() if k != "effects"} == {
                k: v for k, v in after.items() if k != "effects"
            }
            assert budget._tool_schema(before, tool_name=name) == budget._tool_schema(
                after, tool_name=name
            )


@pytest.mark.parametrize("spell", ["fire_bolt", "levitate"])
def test_native_begin_retains_null_typed_choices_materials_source_and_budget(monkeypatch, spell):
    original, batched = _graphs()
    outcomes = []
    for graph in (original, batched):
        fragment = _fragment(graph, ["begin_srd_spell_action"])
        encoded = yaml.safe_dump(fragment, sort_keys=False).encode()
        special.compiled_special.__wrapped__(monkeypatch, (encoded, graph["definitions"]["spells"]))
        state, data, params = cards._spell_case(spell)
        state[f"{cards.SRD}.state"]["spatial_scene"] = special._scene(
            special._entity("hero", [0, 0, 0], participant_ref="hero", creature_type="humanoid"),
            special._entity("guard", [5, 0, 0], participant_ref="guard", creature_type="humanoid"),
            special._entity("other", [30, 0, 0], participant_ref="other", creature_type="humanoid"),
        )
        if spell == "levitate":
            params.update(levitate_subject="guard", levitate_rise=20)
            state[f"{cards.SRD}.state"]["spatial_scene"]["geometry"] = [
                {
                    "id": "ground",
                    "map_id": "information",
                    "active": True,
                    "volume": {"kind": "box", "min": [-500, -500, -1], "max": [500, 500, 0]},
                    "movement": "blocking",
                    "occupies_cells": True,
                    "visibility": "opaque",
                    "metadata": {"plane": "material", "ground": True},
                    "destroyed": {
                        "active": False,
                        "movement": "passable",
                        "visibility": "clear",
                    },
                }
            ]
        names = {p["name"] for p in graph["tools"]["begin_srd_spell_action"]["parameters"]}
        params = {k: v for k, v in params.items() if k in names}
        counters = []
        actual = _effect_executor._execute_effect

        def measured(effect, context, executor=None, counters=counters, actual=actual):
            if not counters and context.budget is not None:
                counters.append(context.budget)
            return actual(effect, context, executor)

        with monkeypatch.context() as scoped:
            scoped.setattr(_effect_executor, "_execute_effect", measured)
            result = budget._run(
                "begin_srd_spell_action", state, participant_data=data, **params
            ).result
        assert counters[0].max_operations == 10000
        pending_id = result["context"]["_pending_action_id"]
        pending = state[f"{cards.SRD}.state"]["pending_srd_actions"][pending_id]
        saved = pending["parameters"]
        assert saved["spell"] == spell and saved["caster_state"] == "hero"
        assert saved["material_item_ids"] == params["material_item_ids"]
        assert saved["attack_targets"] is None
        assert saved["sunburst_plane"] == ""
        assert saved["verbal_component_available"] is True
        assert saved["target_distance"] == 5
        assert type(saved["target_distance"]) is int
        context = {k: v for k, v in result["context"].items() if k not in {ROWS, ROW}}
        outcomes.append(
            (deepcopy(state), context, result.get("result_card"), counters[0].operations)
        )
    assert outcomes[0][:3] == outcomes[1][:3]
    assert outcomes[1][3] <= outcomes[0][3] + 2
    print("native nullable begin", spell, outcomes[0][3], outcomes[1][3])


def test_native_retained_spell_hit_json_reopen_and_retry_keeps_roll_cost_and_packet(monkeypatch):
    original, batched = _graphs()
    outcomes = []
    for graph in (original, batched):
        fragment = _fragment(graph, ["begin_srd_spell_action", "complete_srd_pending_action"])
        encoded = yaml.safe_dump(fragment, sort_keys=False).encode()
        special.compiled_special.__wrapped__(monkeypatch, (encoded, graph["definitions"]["spells"]))
        state, data, params = cards._spell_case("fire_bolt")
        state[f"{cards.SRD}.state"]["spatial_scene"] = special._scene(
            special._entity("hero", [0, 0, 0], participant_ref="hero", creature_type="humanoid"),
            special._entity("guard", [5, 0, 0], participant_ref="guard", creature_type="humanoid"),
            special._entity("other", [30, 0, 0], participant_ref="other", creature_type="humanoid"),
        )
        state[f"{cards.SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=100)
        names = {p["name"] for p in graph["tools"]["begin_srd_spell_action"]["parameters"]}
        params = {k: v for k, v in params.items() if k in names}
        monkeypatch.setattr(dice, "_rng", Random(5))
        monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
        begun = budget._run("begin_srd_spell_action", state, participant_data=data, **params).result
        event_id = begun["context"]["_pending_action_id"]
        state = json.loads(json.dumps(state))
        budget._run(
            "complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id
        )
        saved = state[f"{cards.SRD}.state"]["pending_srd_actions"][event_id]
        assert saved["stage"] == "spell_attack_reaction"
        roll = deepcopy(saved["spell_attack_context"]["_spell_attack"])
        assert state[f"{cards.SRD}.entities"]["guard"]["temp_hp"] == 100
        state = json.loads(json.dumps(state))
        finished = budget._run(
            "complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id
        ).result
        assert finished["context"]["_spell_attack"] == roll
        assert state[f"{cards.SRD}.entities"]["guard"]["temp_hp"] < 100
        before_retry = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected):
            budget._run(
                "complete_srd_pending_action",
                state,
                participant_data=data,
                pending_action_id=event_id,
            )
        assert state == before_retry
        context = {k: v for k, v in finished["context"].items() if k not in {ROWS, ROW}}
        outcomes.append((state, context, finished.get("result_card")))
    assert outcomes[0] == outcomes[1]
