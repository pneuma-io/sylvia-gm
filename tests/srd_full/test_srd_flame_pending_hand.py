"""A real accepted Flame Blade cast forwards its chosen factual free hand once."""

from copy import deepcopy

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_flame_blade as flame
from tests import test_srd_mirage_fall_composition as actual
from tests import test_srd_public_spell_casting as public
from tests.srd_full.test_srd_action_budget import _run

actual_owners = actual.actual_owners


def test_paid_pending_flame_blade_saves_free_hand_and_completion_retries_once(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(flame, "_runtime", lambda: (actual.RUNTIME, actual.ENCODED))
    state, data, parameters = flame._case()
    state[f"{actual.SRD}.entities"]["hero"]["inventory"].append("Torch")
    _run("record_srd_control_held_items", state, target_state="hero", held_items=["Torch"])
    _run(
        "record_srd_scene_hand_occupancy",
        state,
        actor_state="hero",
        hand_capacity=2,
        holdings=[{"inventory_item": "Torch", "hand_indices": [0]}],
    )
    parameters["flame_blade_hand"] = 1
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, parameters))
    database = tmp_path / "flame-hand.db"
    rolls = []
    ordinary = _effect_executor.dice_roll

    def observe(sides, count=1, modifier=0):
        result = ordinary(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, "dice_roll", observe)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(
            owner, "flame_blade", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        before = owner.snapshot("casting")
        accepted, begin_command = actual._commit(owner, "begin_srd_spell_action", **parameters)
        saved = owner.snapshot("casting")
        lasting = saved.state[f"{actual.SRD}.state"]
        pending = lasting["pending_srd_action_stack"][-1]
        event = lasting["pending_srd_actions"][pending]
        assert event["kind"] == "spell_cast" and event["status"] == "open"
        assert event["spell"] == "flame_blade"
        assert event["parameters"]["flame_blade_hand"] == 1
        assert event["slow_cast"]["roll"] is None and event["slow_cast"]["failed"] is False
        assert actual._actor(saved)["resources"]["spell_slot_2"] == 3
        assert not any(r.get("spell") == "flame_blade" for r in lasting["turn_effects"])
        assert not rolls
        assert owner.execute_action(begin_command) == accepted
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        completed, completion_command = actual._commit(
            owner, "complete_srd_pending_action", pending_action_id=pending
        )
        finished = owner.snapshot("casting")
        sources = [
            r
            for r in finished.state[f"{actual.SRD}.state"]["turn_effects"]
            if r.get("spell") == "flame_blade"
        ]
        assert len(sources) == 1
        source = sources[0]
        assert source["active"] is True and source["hand_index"] == 1
        assert source["cast_id"] and source["source_caster_id"] == "hero"
        assert source["slot_level"] == 2 and source["spell_level"] == 2
        assert source["expires_tick"] == before.state[f"{actual.SRD}.state"]["lasting_tick"] + 200
        assert actual._actor(finished)["resources"]["spell_slot_2"] == 2
        assert actual._actor(finished)["concentration_spell_id"] == "flame_blade"
        assert actual._actor(finished)["concentration_cast_id"] == source["cast_id"]
        assert actual._actor(finished)["concentration_effect_family"] == "retained_spell_attack"
        assert actual._actor(finished)["hp"] == actual._actor(before)["hp"]
        assert (
            finished.state[f"{actual.SRD}.entities"]["guard"]["hp"]
            == before.state[f"{actual.SRD}.entities"]["guard"]["hp"]
        )
        assert not rolls
        assert completed.result["result_card"]["title"] == "Flame Blade"
        assert finished.state[f"{actual.SRD}.state"]["pending_srd_action_stack"] == []
        assert owner.execute_action(completion_command) == completed
        assert owner.snapshot("casting") == finished
        retained = deepcopy(source)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == finished
        assert owner.execute_action(begin_command) == accepted
        assert owner.execute_action(completion_command) == completed
        assert owner.snapshot("casting") == finished
        assert flame._source(owner.snapshot("casting").state) == retained
        assert not rolls
