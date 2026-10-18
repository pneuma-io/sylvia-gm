"""One real Ray reduction belongs to Celestial's one simultaneous damage roll."""

from copy import deepcopy
from pathlib import Path

from scripts.srd_full.srd_celestial_shared_rolls import LIGHT_TOOLS, bind_shared_damage_rolls
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from tests import test_srd_celestial_initial_sight as initial
from tests import test_srd_celestial_lights as light
from tests import test_srd_conjured_presences as presence
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_spell_result_cards import _spell_case
from tests.srd_full.test_srd_spell_travel import SRD

actual_fall_scope = initial.actual_fall_scope
conjured_scope = initial.conjured_scope
celestial_scope = initial.celestial_scope


def test_real_paid_ray_shared_celestial_roll_mixed_saves_reopens_and_retries(
    celestial_scope, actual_fall_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        fall, "RUNTIME_PATH", Path(".scratch/celestial-shared-roll-composition/runtime.yaml")
    )

    def case(_spell):
        state, data, params = presence._case(celestial_searing_targets=["guard", "other"])
        body = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["entities"][1])
        body.update(id="other", position=[10, 0, 0])
        body["metadata"]["participant_ref"] = "other"
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(body)
        state[f"{SRD}.entities"]["hero"]["hp"] = 100
        state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = {
            "constitution": (data["abilities"]["constitution"] - 10) // 2
        }
        state[f"{SRD}.entities"]["other"]["temp_hp"] = 0
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", case)
    game = light._rolls(
        monkeypatch,
        [(20, 1), (20, 20), (20, 10), (20, 1)]
        + [(12, 2)] * 6
        + [(8, 5)]
        + [(12, 3)] * 4
        + [(20, 10), (20, 10)],
    )
    database = tmp_path / "celestial-shared-ray.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "conjure_celestial", target_overrides={"hp": 500, "max_hp": 500}
        )
        prepared, _ = _prepare_mage(owner, [{"spell": "ray_of_enfeeblement", "uses_per_day": 2}])
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        _, _, ray = _spell_case("ray_of_enfeeblement")
        ray.pop("caster")
        ray.pop("slot_resource")
        ray.update(caster_state="mage", target="hero", targets=["hero"])
        paid_ray, _ = _act(owner, "cast_spell_outside_combat", **ray)
        assert paid_ray.status is ActionStatus.COMMITTED, paid_ray.result
        ray_state = owner.snapshot("casting").state
        assert ray_state[f"{SRD}.entities"]["hero"]["spell_ray_of_enfeeblement"] is True
        assert (
            ray_state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["ray_of_enfeeblement"] == 1
        )
        mechanics = StatelessMcpMechanics(owner)
        for name, intent, action in [
            ("start_group_combat_encounter", {"combatants": ["hero", "guard", "other"]}, "start"),
            ("begin_combat_turn", {}, "begin-hero"),
        ]:
            setup = dwelling._command(intent, owner.snapshot("casting").revision, name, action)
            assert mechanics.execute_action(setup).status is ActionStatus.COMMITTED
        command = dwelling._command(
            params, owner.snapshot("casting").revision, "cast_spell", "shared-celestial"
        )
        result = mechanics.execute_action(command)
        assert result.status is ActionStatus.COMMITTED, result.result
        context = result.result["context"]
        assert game.consumed[4:11] == [(12, 2)] * 6 + [(8, 5)]
        packets = [row for row in context["_presence_results"] if row["light"] == "searing"]
        assert [(row["target"], row["save"], row["damage"]) for row in packets] == [
            ("guard", "success", 3),
            ("other", "failure", 7),
        ]
        saved = owner.snapshot("casting")
        assert presence._source(saved.state)["cast_id"] != ""
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 497
        assert saved.state[f"{SRD}.entities"]["other"]["hp"] == 993
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        assert sum(sides == 8 for sides, _ in game.consumed) == 1
        assert not game.rolls
        assert mechanics.execute_action(command) == result and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert StatelessMcpMechanics(owner).execute_action(command) == result
        assert owner.snapshot("casting") == saved

    parent = Path(".scratch/celestial-initial-sight-frozen-v1/excluded/runtime.yaml").read_text()
    before = _load_runtime(parent)
    joined = _load_runtime(bind_shared_damage_rolls(parent))
    for name, tool in before["tools"].items():
        if name not in LIGHT_TOOLS:
            assert joined["tools"][name] == tool
    assert _load_runtime(bind_shared_damage_rolls(bind_shared_damage_rolls(parent))) == joined
