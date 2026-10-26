"""Source replicas use native possessions while original physical stock stays independent."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from scripts.srd_full.srd_astral_projection import POSSESSION_FIELDS, bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_astral_projection as original
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act


@pytest.fixture(autouse=True)
def owners(monkeypatch):
    parent = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-astral-possessions/retained-native-parent.yaml")
    ).read_bytes()
    assert sha256(parent).hexdigest() == (
        "fe066d1251fb874d6cb62f0fcb8eb925d4db40d56df0a3c8a8831158ce2624bc"
    )
    parsed = _load_runtime(parent.decode())
    monkeypatch.setattr(original, "source_runtime", lambda: (deepcopy(parsed), parent))
    original._runtime.cache_clear()
    original._mechanic.cache_clear()
    original._archive.cache_clear()
    monkeypatch.setattr(budget, "_mechanic", original._mechanic)
    monkeypatch.setattr(
        cards, "_spell_definitions", lambda: original._runtime()[0]["definitions"]["spells"]
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    execute = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


def _case():
    state, data, params = original._case()
    actor = state[f"{SRD}.entities"]["hero"]
    actor["inventory"] += ["shield", "dagger", "diamond_dust"]
    actor["spell_materials"].append(
        {
            "id": "diamonds",
            "inventory_item": "diamond_dust",
            "kind": "specific",
            "quantity": 1,
            "value_cp": 10000,
            "components": ["stoneskin.1"],
        }
    )
    actor["prepared_spells"].append("stoneskin")
    data["spellcasting_grants"][0]["spells"].append("stoneskin")
    data["wizard_spellbook"].append("stoneskin")
    guard = next(
        r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "guard"
    )
    guard["position"] = [105, 100, 0]
    guard["metadata"]["plane"] = "astral"
    return state, data, params


def test_public_paid_replica_material_held_and_worn_use_return_reopen_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(public, "bundled_srd_archive", original._archive)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _case())
    database = tmp_path / "astral-items.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "astral_projection")
        equip, _ = _act(
            owner,
            "change_srd_equipment",
            actor="hero",
            actor_state="hero",
            slot="shield",
            item=[{"inventory_item": "shield", "armor_type": "shield", "magic_bonus": 0}],
        )
        assert equip.status is ActionStatus.COMMITTED, equip.result
        held, _ = _act(
            owner,
            "record_srd_control_held_items",
            target_state="hero",
            held_items=["shield"],
            held_attack_names=[],
        )
        assert held.status is ActionStatus.COMMITTED, held.result
        begin, begin_input = _act(owner, "begin_srd_spell_cast", **original._begin_values(params))
        assert begin.status is ActionStatus.COMMITTED, begin.result
        progress, progress_input = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert progress.status is ActionStatus.COMMITTED, progress.result
        cast, cast_input = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        state = owner.snapshot("casting").state
        actor = state[f"{SRD}.entities"]["hero"]
        source = state[f"{SRD}.state"]["astral_projection_sources"][
            actor["astral_projection_source_id"]
        ]
        body_id = source["body_id"]
        body = state[f"{SRD}.entities"][body_id]
        physical = {field: deepcopy(body.get(field)) for field in POSSESSION_FIELDS}
        assert physical["held_items"] == ["shield"] and physical["worn_shield"]
        assert actor["inventory"] == body["inventory"]
        assert actor["spell_materials"] == body["spell_materials"]
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        doff, doff_input = _act(
            owner,
            "change_srd_equipment",
            actor="hero",
            actor_state="hero",
            slot="shield",
            item=[],
        )
        assert doff.status is ActionStatus.COMMITTED, doff.result
        ungrip, ungrip_input = _act(
            owner,
            "record_srd_control_held_items",
            target_state="hero",
            held_items=[],
            held_attack_names=[],
        )
        assert ungrip.status is ActionStatus.COMMITTED, ungrip.result
        stoneskin = params | {
            "spell": "stoneskin",
            "target": "guard",
            "slot_resource": "spell_slot_4",
            "material_item_ids": ["diamonds"],
        }
        use, use_input = _act(owner, "cast_spell_outside_combat", **stoneskin)
        assert use.status is ActionStatus.COMMITTED, use.result
        state = owner.snapshot("casting").state
        actor = state[f"{SRD}.entities"]["hero"]
        body = state[f"{SRD}.entities"][body_id]
        assert actor["held_items"] == [] and actor["worn_shield"] is None
        assert "diamond_dust" not in actor["inventory"]
        assert actor["spell_materials"][-1]["quantity"] == 0
        assert {field: body.get(field) for field in POSSESSION_FIELDS} == physical
        assert actor["resources"]["spell_slot_4"] == 2
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        dismiss, dismiss_input = _act(owner, "dismiss_srd_astral_projection", caster_state="hero")
        assert dismiss.status is ActionStatus.COMMITTED, dismiss.result
        final = owner.snapshot("casting")
        actor = final.state[f"{SRD}.entities"]["hero"]
        assert {field: actor.get(field) for field in POSSESSION_FIELDS} == physical
        assert actor["resources"]["spell_slot_4"] == 2
        assert actor["resources"]["spell_slot_9"] == 2
        for command, receipt in (
            (begin_input, begin),
            (progress_input, progress),
            (cast_input, cast),
            (doff_input, doff),
            (ungrip_input, ungrip),
            (use_input, use),
            (dismiss_input, dismiss),
        ):
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == final


def test_magical_possessions_reject_before_cost_and_repeat_is_exact():
    text = original._runtime()[1].decode()
    assert _load_runtime(bind_runtime_consumers(text)) == _load_runtime(text)
    state, data, params = _case()
    actor = state[f"{SRD}.entities"]["hero"]
    actor["worn_shield"] = {"inventory_item": "shield", "armor_type": "shield", "magic_bonus": 1}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="magical properties"):
        _run("begin_srd_spell_cast", state, participant_data=data, **original._begin_values(params))
    assert state == before
