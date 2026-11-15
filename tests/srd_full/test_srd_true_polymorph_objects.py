"""True Polymorph uses actual paid costs and two different reversible receiving owners."""

from copy import deepcopy

import pytest
import yaml
from scripts.srd_full.srd_true_polymorph_objects import PUBLIC_TOOLS, extend_runtime

from tests import test_srd_information_self_casting as casting
from tests import test_srd_spell_wave6_transformations as forms
from tests.srd_full.test_srd_action_budget import SRD, _run


@pytest.fixture(autouse=True)
def _authored(monkeypatch):
    _, base = casting._runtime()
    text = extend_runtime(base.decode())
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    monkeypatch.setattr(casting, "_runtime", lambda: (runtime, text.encode()))
    casting._mechanic.cache_clear()
    casting._authored.__wrapped__(monkeypatch)
    yield
    casting._mechanic.cache_clear()


def _case(mode, **choices):
    state, data, params = forms._setup("true_polymorph", **choices)
    state[f"{SRD}.state"]["spatial_scene"] = None
    params.update(
        true_polymorph_mode=mode,
        target_willing=True,
        true_polymorph_description="A wooden statuette",
    )
    return state, data, params


def test_creature_object_receives_real_damage_then_restores_original_living_identity():
    state, data, params = _case("creature_to_object", true_polymorph_size="tiny")
    forms._entity(state)["inventory"] = ["lantern"]
    original = deepcopy(forms._entity(state))
    forms._cast(state, data, params)
    target = forms._entity(state)
    assert target["is_object"] is True and target["hp"] == 5 and target["speed"] == 0
    assert target["form_can_cast"] is False
    source_id = target["true_polymorph_object_source_id"]
    _run(
        "apply_srd_qualifying_composite_damage",
        state,
        target="guard",
        component_1_amount=20,
        component_1_damage_type="psychic",
        component_2_amount=1,
        component_2_damage_type="bludgeoning",
        source_state_id="hero",
    )
    target = forms._entity(state)
    assert target["hp"] == 4
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=6,
        adjustment=0,
        damage_type="bludgeoning",
        source_state_id="",
    )
    target = forms._entity(state)
    assert target["is_alive"] is True and target["hp"] == original["hp"]
    assert target["inventory"] == original["inventory"]
    assert target["true_polymorph_object_source_id"] is None
    assert state[f"{SRD}.state"]["true_polymorph_object_sources"][source_id]["active"] is False


def test_existing_object_becomes_native_creature_then_reverts_after_concentration():
    state, data, params = _case("object_to_creature", transformation_form="camel")
    _run(
        "prepare_srd_true_polymorph_object",
        state,
        object_id="statue",
        name="Wooden statue",
        source="Plan: actual unheld statue",
        material="wood",
        size="large",
        durability="resilient",
        magical=False,
    )
    params.update(true_polymorph_object="statue", target="statue")
    forms._cast(state, data, params)
    actor = forms._entity(state, "statue")
    assert actor["is_object"] is False and actor["hp"] == 17 and actor["speed"] == 50
    assert actor["temp_hp"] == 0 and actor["true_polymorph_controlled"] is True
    _run(
        "command_srd_true_polymorph_creature",
        state,
        actor_state="hero",
        target_state="statue",
        command="Carry my packs to camp",
    )
    actor = forms._entity(state, "statue")
    assert actor["true_polymorph_command"] == "Carry my packs to camp"
    _run("end_concentration", state, caster_state="hero")
    actor = forms._entity(state, "statue")
    assert actor["is_object"] is True and actor["hp"] == 27 and actor["name"] == "Wooden statue"
    assert actor["srd_spell_object"]["material"] == "wood"
    assert actor["creature_kind"] == "object" and actor["resources"] is None


def test_emitter_semantic_repeat_preserves_other_receiving_effects():
    runtime, data = casting._runtime()
    assert yaml.load(extend_runtime(data.decode()), Loader=yaml.CSafeLoader) == runtime


def test_scene_body_restores_original_footprint_at_current_committed_position():
    from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

    state, data, params = _case("creature_to_object", true_polymorph_size="tiny")
    _admit_scene(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    original = deepcopy(next(e for e in scene["entities"] if e["id"] == "guard"))
    params["true_polymorph_body"] = [
        {"footprint": {"kind": "box", "half_extents": [0.25, 0.25]}, "height": 1}
    ]
    forms._cast(state, data, params)
    body = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "guard")
    assert body["height"] == 1 and body["metadata"]["is_object"] is True
    _run("end_concentration", state, caster_state="hero")
    body = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "guard")
    assert body["position"] == original["position"] and body["height"] == original["height"]
    assert (
        body["footprint"] == original["footprint"]
        and body["metadata"]["participant_ref"] == "guard"
    )


@pytest.mark.parametrize("mode", ["object_to_creature", "creature_to_object"])
def test_strict_paid_object_modes_reopen_retry_and_actual_source_retirement(
    mode, tmp_path, monkeypatch
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application import bundled_packages
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from sylvia_gm.domain.errors import MechanicsRejected
    from tests import test_srd_public_spell_casting as public
    from tests import test_srd_spell_appearance as appearance
    from tests.srd_full.test_srd_dream_spell import _linked_drafts
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(bundled_packages, "_srd_drafts", _linked_drafts)
    monkeypatch.setattr(appearance, "_runtime", casting._runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", PUBLIC_TOOLS)
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)

    def actual_case(_spell):
        state, data, params = _case(mode, transformation_form="camel", true_polymorph_size="tiny")
        data["spellcasting_grants"][0]["spells"].append("dispel_magic")
        data["wizard_spellbook"].append("dispel_magic")
        state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("dispel_magic")
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", actual_case)
    database = tmp_path / (mode + ".db")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "true_polymorph", target_overrides={"hp": 19, "max_hp": 20}
        )
        assert {t.name for t in StatelessMcpMechanics(owner).list_tools("casting")} >= PUBLIC_TOOLS
        if mode == "object_to_creature":
            admitted, preparation = _act(
                owner,
                "prepare_srd_true_polymorph_object",
                object_id="statue",
                name="Wooden statue",
                source="Plan: unheld nonmagical large statue",
                material="wood",
                size="large",
                durability="resilient",
                magical=False,
            )
            assert admitted.status is ActionStatus.COMMITTED
            params.update(target="statue", true_polymorph_object="statue")
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            _act(owner, "cast_spell_outside_combat", **(params | {"target_distance": 31}))
        assert owner.snapshot("casting").state == before.state
        paid, command = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        target_id = params["target"]
        target = saved.state[f"{SRD}.entities"][target_id]
        source_id = target["true_polymorph_object_source_id"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2
        assert owner.snapshot("casting").state[f"{SRD}.state"]["spell_source_serial"] == 1
        assert target["is_object"] is (mode == "creature_to_object")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert StatelessMcpMechanics(owner).execute_action(command) == paid
        assert owner.snapshot("casting").state == saved.state
        if mode == "object_to_creature":
            controlled, control = _act(
                owner,
                "command_srd_true_polymorph_creature",
                actor_state="hero",
                target_state=target_id,
                command="Carry the pack",
            )
            assert controlled.status is ActionStatus.COMMITTED
            clock, clock_command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=601)
            assert clock.status is ActionStatus.COMMITTED
            later = owner.snapshot("casting")
            record = later.state[f"{SRD}.state"]["true_polymorph_object_sources"][source_id]
            assert record["permanent"] is True
            assert later.state[f"{SRD}.entities"][target_id]["true_polymorph_controlled"] is False
            assert StatelessMcpMechanics(owner).execute_action(clock_command) == clock
            ended, ending = _act(
                owner,
                "cast_spell_outside_combat",
                **(
                    params
                    | {
                        "spell": "dispel_magic",
                        "slot_resource": "spell_slot_9",
                        "material_item_ids": [],
                        "recovery_choice": "true_polymorph_object",
                        "recovery_source_id": source_id,
                    }
                ),
            )
        else:
            ended, ending = _act(owner, "end_concentration", caster_state="hero")
        assert ended.status is ActionStatus.COMMITTED
        settled = owner.snapshot("casting")
        assert (
            settled.state[f"{SRD}.state"]["true_polymorph_object_sources"][source_id]["active"]
            is False
        )
        restored = settled.state[f"{SRD}.entities"][target_id]
        assert restored["is_object"] is (True if mode == "object_to_creature" else None)
        assert restored["hp"] == (27 if mode == "object_to_creature" else 19)
        assert StatelessMcpMechanics(owner).execute_action(ending) == ended
        assert owner.snapshot("casting").state == settled.state


def _paid_animal_under_object():
    state, data, params = _case("creature_to_object", true_polymorph_size="tiny")
    _run(
        "prepare_srd_creature",
        state,
        creature_id="druid",
        name="NPC Druid",
        source="Synthetic stat block: Animal Shapes1/day, Wisdom+3, DC15",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=6,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[{"spell": "animal_shapes", "uses_per_day": 1}],
    )
    _, _, animal = forms._setup("animal_shapes")
    animal.pop("caster")
    animal.pop("slot_resource")
    animal.update(caster_state="druid", targets=["guard"], transformation_form="camel")
    forms._cast(state, data, animal)
    animal_id = forms._entity(state)["form_current_source_id"]
    assert forms._entity(state)["speed"] == 50
    forms._cast(state, data, params)
    assert forms._entity(state)["is_object"] is True
    assert forms._entity(state, "druid")["spell_uses_remaining"]["animal_shapes"] == 0
    assert forms._entity(state, "hero")["resources"]["spell_slot_9"] == 2
    return state, data, animal_id


def test_paid_object_return_projects_actual_changed_independent_animal_source():
    state, data, animal_id = _paid_animal_under_object()
    _run(
        "change_srd_transformation",
        state,
        participant_data=data,
        actor_state="druid",
        source_ids=[animal_id],
        form_id="giant_rat",
    )
    assert forms._sources(state)[animal_id]["form_id"] == "giant_rat"
    _run("end_concentration", state, caster_state="hero")
    target = forms._entity(state)
    assert target["form_current_source_id"] == animal_id
    assert target["form_appearance"] == "Giant Rat" and target["speed"] == 30
    assert target["is_object"] is not True and target["hp"] == 19
    assert forms._sources(state)[animal_id]["installed"] is True
    _run("end_srd_transformation_source", state, target_state="guard", source_id=animal_id)
    assert forms._entity(state)["form_current_source_id"] is None
    assert forms._entity(state)["speed"] == 30


def test_paid_permanent_object_return_does_not_resurrect_expired_animal_body_or_temp_hp():
    state, data, animal_id = _paid_animal_under_object()
    _run("advance_srd_lasting_time", state, elapsed_rounds=14401)
    object_id = forms._entity(state)["true_polymorph_object_source_id"]
    assert state[f"{SRD}.state"]["true_polymorph_object_sources"][object_id]["permanent"] is True
    assert forms._sources(state)[animal_id]["installed"] is False
    from tests.srd_full.test_srd_spell_damage_consumers import _grant

    dispel = _grant(state, data, "dispel_magic")
    dispel.update(
        slot_resource="spell_slot_9",
        recovery_choice="true_polymorph_object",
        recovery_source_id=object_id,
    )
    forms._cast(state, data, dispel)
    target = forms._entity(state)
    assert target["true_polymorph_object_source_id"] is None
    assert target["form_current_source_id"] is None and target["transformation_source_ids"] == []
    assert target["speed"] == 30 and target["armor_class"] == 1
    assert target["temp_hp"] == 0 and target["temp_hp_transformation_source_id"] is None
    assert target["hp"] == 19 and target["is_alive"] is True
