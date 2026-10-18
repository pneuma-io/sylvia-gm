"""Source-granted free casting spends its own saved pool through the ordinary cast."""

from copy import deepcopy

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests.test_srd_action_budget import _run
from tests.test_srd_npc_spell_casting import _act
from tests.test_srd_public_spell_casting import _create_spell_campaign
from tests.test_srd_spell_grants import _validate
from tests.test_srd_spell_result_cards import SRD, _spell_case


def test_feat_free_cast_is_atomic_saved_replay_safe_and_recovers_only_after_long_rest(tmp_path):
    database = tmp_path / "feat-casting.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner, "cure_wounds",
            participant_overrides={
                "level": 1, "classes": [{"id": "fighter", "level": 1}],
                "spellcasting_grants": [{"source": "magic_initiate_cleric", "ability": "wisdom",
                                         "spells": ["cure_wounds"]}],
            },
            entity_overrides={"resources": {}, "resource_maxima": {}},
        )
        parameters["slot_resource"] = "feature"
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            _act(owner, "cast_spell_outside_combat", **(parameters | {
                "target_distance": 60, "caster_can_touch_target": False,
            }))
        assert owner.snapshot("casting").state == before.state
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"] == {}
        assert hero["spell_grant_uses"]["magic_initiate_cleric"] == 1
        summary = {row["label"]: row["value"] for row in receipt.result["result_card"]["summary"]}
        assert summary["Feature uses spent"] == 1 and summary["Feature uses remaining"] == 0
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        with pytest.raises(MechanicsRejected, match="feature use"):
            _act(owner, "cast_spell_outside_combat", **parameters)
        assert owner.snapshot("casting").state == saved.state
        for kind, hours in [("short", 1), ("long", 8)]:
            _act(owner, "begin_srd_rest", target_state="hero", kind=kind,
                 activity="sleep" if kind == "long" else "light")
            _act(owner, "advance_srd_lasting_time", elapsed_rounds=hours * 600)
            rest, _ = _act(owner, "complete_srd_rest", target_state="hero")
            assert rest.status is ActionStatus.COMMITTED
            if kind == "short":
                before = owner.snapshot("casting")
                with pytest.raises(MechanicsRejected, match="feature use"):
                    _act(owner, "cast_spell_outside_combat", **parameters)
                assert owner.snapshot("casting").state == before.state
        result, _ = _act(owner, "cast_spell_outside_combat", **parameters)
        assert result.status is ActionStatus.COMMITTED
        hero = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        assert hero["spell_grant_uses"]["magic_initiate_cleric"] == 1
        assert hero["resources"] == {}


@pytest.mark.parametrize("feature_first", [True, False])
def test_free_bonus_spell_and_slotted_action_spell_share_one_slot_spend(feature_first):
    state, data, feature = _spell_case("healing_word")
    data.update(level=3, classes=[{"id": "wizard", "level": 3}],
                origin_feats=["magic_initiate"], wizard_spellbook=["magic_missile"])
    data["abilities"].update(intelligence=18, wisdom=18)
    data["spellcasting_grants"] = [
        {"source": "magic_initiate_cleric", "ability": "wisdom", "spells": ["healing_word"]},
        {"source": "wizard", "ability": "intelligence", "spells": ["magic_missile"]},
    ]
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(prepared_spells=["healing_word", "magic_missile"], resources={"spell_slot_1": 1})
    state[f"{SRD}.entities"]["guard"].update(hp=100, max_hp=200)
    feature["slot_resource"] = "feature"
    _, _, slotted = _spell_case("magic_missile")
    for parameters in ([feature, slotted] if feature_first else [slotted, feature]):
        result = _run("cast_spell", state, participant_data=data, **parameters)
        assert result.result["status"] == "committed"
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_1"] == 0
    assert hero["spell_slot_cast_this_turn"] is True
    assert hero["bonus_action_available"] is False
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert hero["spell_grant_uses"]["magic_initiate_cleric"] == 1


def test_an_ordinary_class_grant_does_not_create_free_casts():
    state, data, parameters = _spell_case("magic_missile")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="free cast"):
        _run("cast_spell", state, participant_data=data,
             **(parameters | {"slot_resource": "feature"}))
    assert state == before


def test_feature_exhaustion_does_not_block_an_allowed_slot_cast():
    state, data, parameters = _spell_case("magic_missile")
    data["origin_feats"] = ["magic_initiate"]
    data["spellcasting_grants"] = [{
        "source": "magic_initiate_wizard", "ability": "intelligence", "spells": ["magic_missile"],
    }]
    hero = state[f"{SRD}.entities"]["hero"]
    hero["spell_grant_uses"] = {"magic_initiate_wizard": 1}
    slots = hero["resources"]["spell_slot_1"]
    _run("cast_spell", state, participant_data=data, **parameters)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["spell_grant_uses"] == {"magic_initiate_wizard": 1}
    assert hero["resources"]["spell_slot_1"] == slots - 1


def test_grant_pools_share_land_uses_separate_lineage_spells_and_scale_with_character_level():
    _, data, _ = _spell_case("fireball")
    data.update(level=6, classes=[{"id": "druid", "level": 6}],
                subclasses={"druid": "circle_of_land"})
    data["spellcasting_grants"] = [{
        "source": "land_arid", "ability": "wisdom", "spells": ["burning_hands", "fireball"],
    }]
    for spell in ["burning_hands", "fireball"]:
        result = _validate(data, spell)
        assert (result["_grant_free_pool"], result["_grant_free_limit"]) == ("natural_recovery", 1)
    data["spellcasting_grants"][0].update(source="land_polar", spells=["ice_storm"])
    data.update(level=7, classes=[{"id": "druid", "level": 7}])
    assert _validate(data, "ice_storm")["_grant_free_pool"] == "natural_recovery"
    data.update(species="tiefling", spellcasting_grants=[{
        "source": "tiefling_abyssal", "ability": "wisdom",
        "spells": ["ray_of_sickness", "hold_person"],
    }])
    for spell in ["ray_of_sickness", "hold_person"]:
        result = _validate(data, spell)
        assert result["_grant_free_pool"] == spell
        assert result["_grant_free_limit"] == 1
    data.update(species="gnome", level=9,
                classes=[{"id": "fighter", "level": 8}, {"id": "wizard", "level": 1}],
                spellcasting_grants=[{"source": "gnome_forest", "ability": "wisdom",
                                      "spells": ["speak_with_animals"]}])
    assert _validate(data, "speak_with_animals")["_grant_free_limit"] == 4


def test_mystic_arcanum_requires_its_exact_spell_level_and_uses_its_own_charge():
    state, data, parameters = _spell_case("power_word_kill")
    data.update(level=17, classes=[{"id": "warlock", "level": 17}],
                spellcasting_grants=[{"source": "mystic_arcanum_9", "ability": "charisma",
                                      "spells": ["power_word_kill"]}])
    data["abilities"]["charisma"] = 18
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="feature cast"):
        _run("cast_spell", state, participant_data=data, **parameters)
    assert state == before
    _run("cast_spell", state, participant_data=data, **(parameters | {"slot_resource": "feature"}))
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"] == before[f"{SRD}.entities"]["hero"]["resources"]
    assert hero["spell_grant_uses"] == {"mystic_arcanum_9": 1}
    assert state[f"{SRD}.entities"]["guard"]["is_alive"] is False
    assert "dead" in state[f"{SRD}.entities"]["guard"]["conditions"]
    data["spellcasting_grants"][0]["spells"] = ["magic_missile"]
    with pytest.raises(RulesetPreconditionRejected, match="actual class"):
        _validate(data, "magic_missile")


def test_feat_free_cast_preserves_other_spell_pool_and_requires_actual_materials():
    state, data, parameters = _spell_case("false_life")
    data.update(species="human", level=5, classes=[{"id": "fighter", "level": 5}], origin_feats=["magic_initiate"],
                spellcasting_grants=[{"source": "magic_initiate_wizard", "ability": "intelligence",
                                      "spells": ["false_life"]}])
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(resources={}, spell_grant_uses={"ray_of_enfeeblement": 1})
    # A free feat cast still needs the nonconsumed alcohol component.
    before_stock = hero["spell_materials"][0]["quantity"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Material"):
        _run("cast_spell", state, participant_data=data,
             **(parameters | {"slot_resource": "feature", "material_component_available": False}))
    assert state == before
    _run("cast_spell", state, participant_data=data, **(parameters | {"slot_resource": "feature"}))
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"] == {}
    assert hero["spell_grant_uses"] == {"magic_initiate_wizard": 1, "ray_of_enfeeblement": 1}
    assert hero["spell_materials"][0]["quantity"] == before_stock
