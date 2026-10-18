"""Equipment invariants at the actual package inventory boundary."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.initial_character_state import materialize_initial_character_state
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected, RulesetPreconditionRejected
from tests.srd_full.test_reusable_participants import _participant, _participant_authority, _starting_state
from tests.srd_full.test_srd_action_budget import SRD, _run, _state
from tests.srd_full.test_srd_advancement import award, build, command, create, resolve
from tests.srd_full.test_srd_attack_budget import _attack, _combat_case
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import _spell_case


@pytest.mark.parametrize("slot,item", [("worn_armor", "chain_mail"), ("worn_shield", "shield")])
def test_inventory_cannot_remove_a_worn_item(slot, item):
    state = _state()
    actor = state[f"{SRD}.entities"]["hero"]
    actor.update(inventory=[item, "rope"], armor_class=18)
    actor[slot] = {"inventory_item": item, "armor_type": item, "magic_bonus": 0}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Remove worn armor"):
        _run("set_srd_carried_inventory", state, actor_state="hero", inventory=["rope"])
    assert state == before
    result = _run("set_srd_carried_inventory", state, actor_state="hero", inventory=[item])
    assert result.result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"][slot] == before[f"{SRD}.entities"]["hero"][slot]


def test_unworn_inventory_changes_do_not_invent_equipment_or_change_defenses():
    state = _state()
    actor = state[f"{SRD}.entities"]["hero"]
    actor.update(inventory=["chain_mail"], armor_class=12)
    assert _run("set_srd_carried_inventory", state, actor_state="hero",
                inventory=["shield"]).result["status"] == "committed"
    actor = state[f"{SRD}.entities"]["hero"]
    assert actor["armor_class"] == 12
    assert "worn_armor" not in actor and "worn_shield" not in actor


def _equipment(armor=None, shield=None, *, classes=None, dexterity=16, strength=16, **choices):
    """Resolve the real package helper without a test-side armor formula."""
    classes = classes or [{"id": "fighter", "level": 1}]
    source = next((row["id"] for row in classes if row["id"] in ("fighter", "paladin", "ranger")),
                  "fighter")
    if "fighting_styles" in choices:
        choices["fighting_styles"] = [
            {"source": source, "style": style} if isinstance(style, str) else style
            for style in choices["fighting_styles"]
        ]
    state = _state()
    actor = state[f"{SRD}.entities"]["hero"]
    actor["inventory"] = []
    for slot, kind in (("worn_armor", armor), ("worn_shield", shield)):
        if kind:
            actor["inventory"].append(kind)
            actor[slot] = {"inventory_item": kind, "armor_type": kind, "magic_bonus": 0}
    before = deepcopy(state)
    result = _run("resolve_srd_equipment", state, actor="hero", actor_state="hero",
                  participant_data={
                      "classes": classes or [{"id": "fighter", "level": 1}],
                      "abilities": {"dexterity": dexterity, "strength": strength,
                                    "constitution": 14, "wisdom": 18, "charisma": 16}, **choices,
                  })
    assert result.result["status"] == "committed"
    assert state == before
    return result.result["context"]


@pytest.mark.parametrize("armor,dexterity,ac,stealth", [
    (None, 16, 13, False), ("leather_armor", 16, 14, False),
    ("breastplate", 18, 16, False), ("breastplate", 8, 13, False),
    ("plate_armor", 18, 18, True), ("padded_armor", 16, 14, True),
])
def test_armor_family_calculations_preserve_negative_dexterity(armor, dexterity, ac, stealth):
    result = _equipment(armor, dexterity=dexterity)
    assert result["_equipment_ac"] == ac
    assert result["_equipment_stealth_disadvantage"] is stealth
    assert result["_equipment_untrained_armor"] is False


def test_untrained_shield_has_no_bonus_and_does_not_prohibit_casting():
    wizard = [{"id": "wizard", "level": 1}]
    result = _equipment(shield="shield", classes=wizard)
    assert result["_equipment_ac"] == 13
    assert result["_equipment_untrained_armor"] is False
    result = _equipment("leather_armor", shield="shield", classes=wizard)
    assert result["_equipment_ac"] == 14
    assert result["_equipment_untrained_armor"] is True


def test_starting_and_multiclass_training_are_distinct():
    result = _equipment("plate_armor", "shield", strength=14)
    assert result["_equipment_ac"] == 20
    assert result["_equipment_speed_penalty"] == 10
    result = _equipment("plate_armor", "shield", classes=[
        {"id": "wizard", "level": 1}, {"id": "fighter", "level": 1},
    ])
    assert result["_equipment_ac"] == 20
    assert result["_equipment_untrained_armor"] is True


@pytest.mark.parametrize("class_id,order_key,order,armor", [
    ("cleric", "cleric_order", "protector", "plate_armor"),
    ("druid", "druid_order", "warden", "breastplate"),
])
def test_selected_class_order_grants_its_armor_training(class_id, order_key, order, armor):
    classes = [{"id": class_id, "level": 1}]
    assert _equipment(armor, classes=classes)["_equipment_untrained_armor"] is True
    assert _equipment(armor, classes=classes, **{order_key: order})[
        "_equipment_untrained_armor"] is False


@pytest.mark.parametrize("slot,item,allowed", [
    ("worn_armor", "leather_armor", False), ("worn_shield", "shield", True),
])
def test_public_cast_enforces_armor_training_without_treating_shields_as_armor(slot, item, allowed):
    state, data, parameters = _spell_case("fire_bolt")
    actor = state[f"{SRD}.entities"]["hero"]
    actor["inventory"].append(item)
    actor[slot] = {"inventory_item": item, "armor_type": item, "magic_bonus": 0}
    before = deepcopy(state)
    if allowed:
        result = _run("cast_spell", state, participant_data=data, **parameters)
        assert result.result["status"] == "committed"
    else:
        with pytest.raises(RulesetPreconditionRejected, match="armor training"):
            _run("cast_spell", state, participant_data=data, **parameters)
        assert state == before


@pytest.mark.parametrize("classes,shield,expected", [
    ([{"id": "barbarian", "level": 1}], None, 15),
    ([{"id": "barbarian", "level": 1}], "shield", 17),
    ([{"id": "monk", "level": 1}], None, 17),
    ([{"id": "monk", "level": 1}], "shield", 13),
    ([{"id": "monk", "level": 1}, {"id": "barbarian", "level": 1}], None, 17),
    ([{"id": "barbarian", "level": 1}, {"id": "monk", "level": 1}], None, 17),
])
def test_unarmored_defenses_do_not_stack_and_preserve_shield_rules(classes, shield, expected):
    assert _equipment(shield=shield, classes=classes)["_equipment_ac"] == expected


def test_defense_style_requires_armor_and_draconic_base_never_stacks_with_armor():
    assert _equipment("chain_mail", fighting_styles=["defense"])["_equipment_ac"] == 17
    assert _equipment(fighting_styles=["defense"])["_equipment_ac"] == 13
    choices = {"classes": [{"id": "sorcerer", "level": 3}],
               "subclasses": {"sorcerer": "draconic_sorcery"}}
    assert _equipment(**choices)["_equipment_ac"] == 16
    assert _equipment("leather_armor", **choices)["_equipment_ac"] == 14


def _change(state, data, slot, kind=None):
    items = [] if kind is None else [
        {"inventory_item": kind, "armor_type": kind, "magic_bonus": 0},
    ]
    return _run("change_srd_equipment", state, participant_data=data,
                actor="hero", actor_state="hero", slot=slot, item=items)


def test_don_and_doff_armor_update_defenses_clock_and_inventory_guard_together():
    state, data = _combat_case()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["hero"]["inventory"].append("plate_armor")
    data["abilities"]["strength"] = 14
    data["fighting_styles"] = [{"source": "fighter", "style": "defense"}]
    result = _change(state, data, "armor", "plate_armor")
    actor = state[f"{SRD}.entities"]["hero"]
    assert actor["armor_class"] == 19 and actor["speed"] == 20
    assert actor["armor_stealth_disadvantage"] is True
    assert result.result["result_card"]["title"] == "Equipment changed"
    tick = state[f"{SRD}.state"]["lasting_tick"]
    assert tick == 200
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Remove the currently worn"):
        _change(state, data, "armor", "plate_armor")
    assert state == before
    _change(state, data, "armor")
    actor = state[f"{SRD}.entities"]["hero"]
    assert actor["armor_class"] == 12 and actor["speed"] == 30
    assert actor["worn_armor"] is None and actor["armor_stealth_disadvantage"] is False
    assert state[f"{SRD}.state"]["lasting_tick"] == tick + 100
    assert _run("set_srd_carried_inventory", state, actor_state="hero",
                inventory=[]).result["status"] == "committed"


def test_shield_uses_one_combat_action_and_failed_second_change_is_atomic():
    state, data = _combat_case()
    state[f"{SRD}.entities"]["hero"]["inventory"].append("shield")
    assert _change(state, data, "shield", "shield").result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"]["armor_class"] == 14
    assert state[f"{SRD}.combat"]["action_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="no action"):
        _change(state, data, "shield")
    assert state == before


def test_untrained_armor_disadvantage_reaches_an_actual_weapon_attack():
    state, data = _combat_case([{"id": "wizard", "level": 1}])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["hero"]["inventory"].append("leather_armor")
    _change(state, data, "armor", "leather_armor")
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True)
    result = _attack(state, data)
    assert len(result.result["context"]["_attack_roll"]["results"]) == 2
    assert result.result["context"]["_effective_disadvantage"] is True


@pytest.mark.parametrize("class_id,ability,skill,expected", [
    ("wizard", "strength", "athletics", True),
    ("wizard", "wisdom", "perception", False),
    ("fighter", "dexterity", "stealth", True),
    ("fighter", "strength", "athletics", False),
    ("fighter", "strength", "stealth", False),
])
def test_saved_equipment_penalties_reach_appropriate_ability_checks(
    class_id, ability, skill, expected,
):
    state, data = _combat_case([{"id": class_id, "level": 1}])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["hero"]["inventory"].append("chain_mail")
    _change(state, data, "armor", "chain_mail")
    result = _run("ability_check", state, participant_data=data, actor="hero", actor_state="hero",
                  ability=ability, skill=skill, circumstantial_modifier=0, target_number=12)
    assert result.result["context"]["_effective_disadvantage"] is expected


@pytest.mark.parametrize("ability,expected", [("dexterity", True), ("wisdom", False)])
def test_equipment_penalty_reaches_the_shared_saving_throw(ability, expected):
    state, data = _combat_case([{"id": "wizard", "level": 1}])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["hero"]["inventory"].append("chain_mail")
    _change(state, data, "armor", "chain_mail")
    result = _run("d20_test", state, actor_state="hero", kind="saving_throw",
                  ability=ability, ability_modifier=0, proficiency_bonus=0,
                  proficiency_factor="none", circumstantial_modifier=0, target_number=12,
                  advantage=False, disadvantage=False, forego_save=False)
    context = result.result["context"]
    assert context["_effective_disadvantage"] is expected
    assert len(context["_d20_test"]["results"]) == (2 if expected else 1)


def test_public_equipment_action_reopens_with_defenses_and_casting_restrictions(tmp_path):
    database = tmp_path / "equipment.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, casting = _create_spell_campaign(
            owner, "fire_bolt", entity_overrides={"inventory": ["chain_mail"]},
        )
        names = {tool.name for tool in StatelessMcpMechanics(owner).list_tools("casting")}
        assert "change_srd_equipment" in names
        assert "resolve_srd_equipment" not in names
        receipt, command = _act(owner, "change_srd_equipment", actor="hero", actor_state="hero",
                                slot="armor", item=[{
                                    "inventory_item": "chain_mail", "armor_type": "chain_mail",
                                    "magic_bonus": 0,
                                }])
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        actor = saved.state[f"{SRD}.entities"]["hero"]
        assert actor["armor_class"] == 16 and actor["armor_untrained"] is True
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        with pytest.raises(MechanicsRejected, match="armor training"):
            _act(owner, "cast_spell_outside_combat", **casting)
        assert owner.snapshot("casting").state == saved.state
        removed, _ = _act(owner, "change_srd_equipment", actor="hero", actor_state="hero",
                          slot="armor", item=[])
        assert removed.status is ActionStatus.COMMITTED
        cast, _ = _act(owner, "cast_spell_outside_combat", **casting)
        assert cast.status is ActionStatus.COMMITTED


@pytest.mark.parametrize("worn,bonus,expected", [(True, 0, 17), (True, 1, 18), (False, 0, 12)])
def test_creation_calculates_defenses_from_selected_gear_without_authored_ac(worn, bonus, expected):
    _, declaration = _participant_authority()
    profile = {**_participant("Armored Fighter"),
               "fighting_styles": [{"source": "fighter", "style": "defense"}]}
    authored = _starting_state()
    if worn:
        authored["worn_armor"]["magic_bonus"] = bonus
    else:
        authored["worn_armor"] = None
    assert "armor_class" not in authored
    completed = materialize_initial_character_state(
        profile, authored, entity_state_schema=declaration.entity_state_schema,
        initial_state_calculations=declaration.initial_state_calculations,
        initial_state_validation=declaration.initial_state_validation,
    )
    assert completed["armor_class"] == expected
    assert completed["armor_untrained"] is False
    assert "chain_mail" in completed["inventory"]


@pytest.mark.parametrize("level,source", [(1, "fighter"), (7, "champion")])
def test_creation_requires_each_earned_fighter_style(level, source):
    _, declaration = _participant_authority()
    profile = _participant("Fighter")
    profile.update(level=level, classes=[{"id": "fighter", "level": level}])
    if level == 7:
        profile["subclasses"] = {"fighter": "champion"}
    else:
        profile.pop("fighting_styles")

    def complete():
        return materialize_initial_character_state(
            profile, _starting_state(), entity_state_schema=declaration.entity_state_schema,
            initial_state_calculations=declaration.initial_state_calculations,
            initial_state_validation=declaration.initial_state_validation,
        )

    with pytest.raises(CommandRejected, match="Fighting Style"):
        complete()
    profile.setdefault("fighting_styles", []).append({"source": source, "style": "defense"})
    assert complete()["armor_class"] == 17


def test_creation_rejects_worn_gear_not_in_carried_inventory():
    _, declaration = _participant_authority()
    authored = _starting_state()
    authored["inventory"].remove("chain_mail")
    with pytest.raises(CommandRejected, match="carried inventory"):
        materialize_initial_character_state(
            _participant("Fighter"), authored, entity_state_schema=declaration.entity_state_schema,
            initial_state_calculations=declaration.initial_state_calculations,
            initial_state_validation=declaration.initial_state_validation,
        )


def test_level_up_recalculates_worn_armor_from_new_strength_and_preserves_gear(tmp_path):
    database = tmp_path / "equipment-level.db"
    armor = {"inventory_item": "plate_armor", "armor_type": "plate_armor", "magic_bonus": 0}
    with AuthorityOwner.open(database, "test") as owner:
        create(owner, level=3, participant_overrides={
            "abilities": {"strength": 13, "dexterity": 10, "constitution": 10,
                          "intelligence": 10, "wisdom": 10, "charisma": 10},
            "fighting_styles": [{"source": "fighter", "style": "defense"}],
        }, entity_overrides={
            "inventory": ["plate_armor"], "worn_armor": armor, "worn_shield": None,
            "armor_class": 19, "speed": 20, "armor_speed_penalty": 10,
            "armor_untrained": False, "armor_stealth_disadvantage": True,
        })
        award(owner)
        proposal = build(owner)
        proposal["build"][0]["abilities"]["strength"] = 15
        proposal["build"][0]["armor_class"] = 20
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected, match="Proposed Armor Class"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "bad-ac"))
        assert owner.snapshot("advancement").state == before.state
        assert owner.snapshot("advancement").participants == before.participants
        proposal["build"][0]["armor_class"] = 19
        waiting = owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        assert owner.snapshot("advancement").state == before.state
        assert resolve(owner, waiting).status is ActionStatus.COMMITTED
        saved = owner.snapshot("advancement")
        actor = saved.state[f"{SRD}.entities"]["hero"]
        assert actor["worn_armor"] == armor
        assert actor["armor_class"] == 19 and actor["speed"] == 30
        assert actor["armor_speed_penalty"] == 0
        assert actor["hp"] == 5
        assert actor["resource_maxima"]["second_wind"] == 3
        # The new class grant increases capacity; the previously spent use remains spent.
        assert actor["resources"]["second_wind"] == 2
        assert saved.participants[0].data["fighting_styles"] == [
            {"source": "fighter", "style": "defense"},
        ]
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("advancement").state == saved.state


@pytest.mark.parametrize("class_id,level,armor,shield,speed", [
    ("barbarian", 4, None, None, 30), ("barbarian", 5, None, None, 40),
    ("barbarian", 5, "hide_armor", None, 40), ("barbarian", 5, "plate_armor", None, 30),
    ("monk", 2, None, None, 40), ("monk", 6, None, None, 45),
    ("monk", 18, None, None, 60), ("monk", 6, "leather_armor", None, 30),
    ("monk", 6, None, "shield", 30),
    ("ranger", 6, None, None, 40), ("ranger", 6, "plate_armor", None, 30),
])
def test_class_movement_obeys_armor_and_shield_conditions(class_id, level, armor, shield, speed):
    result = _equipment(armor, shield, classes=[{"id": class_id, "level": level}])
    assert result["_equipment_speed"] == speed
    expected_roving = speed if class_id == "ranger" else 0
    assert result["_equipment_climb_speed"] == result["_equipment_swim_speed"] == expected_roving


def test_shield_change_recalculates_monk_movement_after_real_dash_and_movement():
    state, data = _combat_case([{"id": "monk", "level": 2}, {"id": "fighter", "level": 2}])
    actor = state[f"{SRD}.entities"]["hero"]
    actor.update(speed=40, spell_speed_reduction=10, exhaustion_level=1)
    actor["inventory"].append("shield")
    actor["resources"]["action_surge"] = 1
    state[f"{SRD}.combat"].update(movement_remaining=25, movement_spent=0, dashes_taken=0)
    assert _run("spend_combat_movement", state, actor="hero", actor_state="hero",
                distance=10, difficult_terrain=False).result["status"] == "committed"
    assert _run("dash_combat_action", state, participant_data=data, actor="hero",
                actor_state="hero").result["status"] == "committed"
    assert state[f"{SRD}.combat"]["movement_remaining"] == 40
    _run("use_srd_action_surge", state, participant_data=data, actor="hero", actor_state="hero")
    _change(state, data, "shield", "shield")
    actor = state[f"{SRD}.entities"]["hero"]
    combat = state[f"{SRD}.combat"]
    assert actor["speed"] == 30 and actor["resources"]["action_surge"] == 0
    assert combat["dashes_taken"] == 1 and combat["movement_spent"] == 10
    assert combat["movement_remaining"] == 20
    assert combat["action_available"] is False


def test_dodge_cannot_use_unreduced_profile_speed_to_bypass_zero_effective_speed():
    state, data = _combat_case()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["hero"]["inventory"].append("plate_armor")
    data["abilities"]["strength"] = 14
    _change(state, data, "armor", "plate_armor")
    state[f"{SRD}.entities"]["hero"]["exhaustion_level"] = 4
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("take_dodge_action", state, participant_data=data, actor="hero", actor_state="hero")
    assert state == before


def test_npc_equipment_preserves_natural_defenses_and_spends_the_shared_action():
    state = _state()
    state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard")
    state[f"{SRD}.entities"]["guard"] = {
        "name": "Guard", "creature_kind": "monster", "hp": 20, "is_alive": True,
        "conditions": [], "speed": 30, "inventory": ["shield"],
        "equipment_profile": {
            "strength": 16, "dexterity": 12, "unarmored_ac": 15, "speed": 30,
            "climb_speed": 20, "swim_speed": 0, "armor_training": ["shield"],
        },
    }
    receipt = _run("change_srd_equipment", state, actor_state="guard", slot="shield",
                   item=[{"inventory_item": "shield", "armor_type": "shield", "magic_bonus": 0}])
    assert receipt.result["status"] == "committed"
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["armor_class"] == 17
    assert guard["climb_speed"] == 20
    assert state[f"{SRD}.combat"]["action_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("change_srd_equipment", state, actor_state="guard", slot="shield", item=[])
    assert state == before


@pytest.mark.parametrize("valid", [False, True])
def test_public_npc_equipment_admission_casting_and_reopen(tmp_path, valid):
    database = tmp_path / "npc-equipment.db"
    profile = {
        "strength": 10, "dexterity": 12, "unarmored_ac": 11, "speed": 30,
        "climb_speed": 0, "swim_speed": 0, "armor_training": [],
        "armor": [{"inventory_item": "borrowed_leather", "armor_type": "leather_armor",
                   "magic_bonus": 0}], "shield": [],
    }
    if not valid:
        profile["unarmored_ac"] = 16
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "fire_bolt")
        before = owner.snapshot("casting")
        if not valid:
            with pytest.raises(MechanicsRejected, match="AC and Speed"):
                _prepare_mage(owner, [{"spell": "fire_bolt", "uses_per_day": 0}],
                              inventory=["borrowed_leather"], equipment=[profile])
            assert owner.snapshot("casting").state == before.state
            return
        receipt, _ = _prepare_mage(
            owner, [{"spell": "fire_bolt", "uses_per_day": 0}],
            inventory=["borrowed_leather"], equipment=[profile],
        )
        assert receipt.status is ActionStatus.COMMITTED
        parameters.pop("caster")
        parameters.pop("slot_resource")
        parameters["caster_state"] = "mage"
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="untrained armor"):
            _act(owner, "cast_spell_outside_combat", **parameters)
        assert owner.snapshot("casting").state == before.state
        receipt, command = _act(owner, "change_srd_equipment", actor_state="mage",
                               slot="armor", item=[])
        assert receipt.status is ActionStatus.COMMITTED
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        saved = owner.snapshot("casting")
        mage = saved.state[f"{SRD}.entities"]["mage"]
        assert mage["armor_class"] == 11 and mage["armor_untrained"] is False
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        receipt, _ = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED


@pytest.mark.parametrize("style,classes", [
    ("dueling", [{"id": "fighter", "level": 1}]),
    ("protection", [{"id": "fighter", "level": 1}]),
    ("archery", [{"id": "wizard", "level": 1}]),
    ("two_weapon_fighting", [{"id": "paladin", "level": 1}]),
    ("great_weapon_fighting", [{"id": "ranger", "level": 1}]),
])
def test_equipment_rejects_non_srd_or_unearned_style_prerequisites(style, classes):
    with pytest.raises(RulesetPreconditionRejected, match="Fighting Style"):
        _equipment(classes=classes, fighting_styles=[style])


@pytest.mark.parametrize("class_id,level", [("fighter", 1), ("paladin", 2), ("ranger", 2)])
@pytest.mark.parametrize("style", ["archery", "defense", "great_weapon_fighting",
                                   "two_weapon_fighting"])
def test_srd_style_prerequisite_accepts_eligible_class_feature(class_id, level, style):
    result = _equipment(classes=[{"id": class_id, "level": level}], fighting_styles=[style])
    assert result["_equipment_style_eligible"] is True


@pytest.mark.parametrize("weapon,mode,expected", [("shortbow", "ranged", 2),
                                                 ("dagger", "thrown", 0)])
def test_archery_applies_to_ranged_weapons_not_thrown_melee_weapons(weapon, mode, expected):
    state, data = _combat_case()
    data["fighting_styles"] = [{"source": "fighter", "style": "archery"}]
    state[f"{SRD}.entities"]["hero"].update(inventory=[weapon], ammunition={"arrow": 2})
    result = _attack(state, data, weapon=weapon, attack_mode=mode, attack_ability="dexterity",
                     wielding_two_handed=weapon == "shortbow", attack_distance=15)
    context = result.result["context"]
    assert context["_attack_modifier"] == 5 + expected
    details = {row["label"]: row["value"] for row in result.result["result_card"]["details"]}
    assert details.get("Archery", 0) == expected


@pytest.mark.parametrize("style,strength,expected", [(True, 16, 3), (False, 16, 0),
                                                    (True, 8, -1)])
def test_two_weapon_style_retains_ability_damage_on_light_extra_attack(style, strength, expected):
    state, data = _combat_case()
    data["abilities"]["strength"] = strength
    data["fighting_styles"] = (
        [{"source": "fighter", "style": "two_weapon_fighting"}] if style else []
    )
    _attack(state, data, weapon="dagger")
    result = _attack(state, data, weapon="scimitar", attack_sequence="light_nick")
    assert result.result["context"]["_damage_modifier"] == expected


@pytest.mark.parametrize("weapon,two_handed,applies,seed", [
    ("greatsword", True, True, 6), ("greatsword", True, True, 37),
    ("spear", True, True, 6), ("spear", False, False, 6), ("shortbow", True, False, 6),
])
def test_great_weapon_style_raises_low_damage_dice_without_changing_raw_rolls(
    monkeypatch, weapon, two_handed, applies, seed,
):
    monkeypatch.setattr(dice, "_rng", Random(seed))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _combat_case()
    data["fighting_styles"] = [{"source": "fighter", "style": "great_weapon_fighting"}]
    state[f"{SRD}.entities"]["hero"].update(inventory=[weapon], ammunition={"arrow": 2})
    result = _attack(state, data, weapon=weapon, wielding_two_handed=two_handed,
                     attack_mode="ranged" if weapon == "shortbow" else "melee",
                     attack_ability="dexterity" if weapon == "shortbow" else "strength")
    context = result.result["context"]
    raw = context["_damage_roll"]["results"]
    assert min(raw) <= 2
    if seed == 37:
        assert context["_critical_hit"] is True and len(raw) == 4
    bonus = sum(max(0, 3 - value) for value in raw) if applies else 0
    assert context["_great_weapon_damage_bonus"] == bonus
    details = {row["label"]: row["value"] for row in result.result["result_card"]["details"]}
    assert details.get("Great Weapon Fighting", 0) == bonus
    assert context["_damage_applied"] == sum(raw) + bonus + context["_damage_modifier"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - context["_damage_applied"]



def test_two_handed_weapon_attack_rejects_wielded_shield_without_spending_action():
    state, data = _combat_case()
    state[f"{SRD}.entities"]["hero"]["worn_shield"] = {
        "inventory_item": "shield", "armor_type": "shield", "magic_bonus": 0,
    }
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="shield"):
        _attack(state, data, wielding_two_handed=True)
    assert state == before



def test_style_grants_retain_the_feature_that_awards_each_choice():
    result = _equipment("chain_mail", classes=[{"id": "fighter", "level": 7}],
                        subclasses={"fighter": "champion"}, fighting_styles=[
                            {"source": "fighter", "style": "defense"},
                            {"source": "champion", "style": "archery"},
                            {"source": "fighter_feat_4", "style": "great_weapon_fighting"},
                        ])
    assert result["_equipment_ac"] == 17
    assert result["_style_sources"] == ["fighter", "champion", "fighter_feat_4"]


@pytest.mark.parametrize("level,subclass,choices", [
    (7, "champion", [{"source": "fighter", "style": "defense"},
                      {"source": "fighter", "style": "archery"}]),
    (7, "champion", [{"source": "fighter", "style": "defense"},
                      {"source": "champion", "style": "defense"}]),
    (6, "champion", [{"source": "champion", "style": "archery"}]),
    (7, "champion", [{"source": "fighter_feat_8", "style": "archery"}]),
    (7, "champion", [{"source": "paladin", "style": "archery"}]),
    (7, "other", [{"source": "champion", "style": "archery"}]),
])
def test_style_grant_count_level_and_subclass_are_enforced(level, subclass, choices):
    with pytest.raises(RulesetPreconditionRejected, match="Fighting Style"):
        _equipment(classes=[{"id": "fighter", "level": level}],
                   subclasses={"fighter": subclass}, fighting_styles=choices)


@pytest.mark.parametrize("class_id,alternative", [("paladin", "blessed_warrior"),
                                                  ("ranger", "druidic_warrior")])
def test_style_cantrip_alternative_cannot_also_spend_its_class_style_grant(class_id, alternative):
    profile = {"classes": [{"id": class_id, "level": 4}],
               "spellcasting_grants": [{"source": alternative, "spells": []}]}
    with pytest.raises(RulesetPreconditionRejected, match="not both"):
        _equipment(**profile, fighting_styles=[{"source": class_id, "style": "defense"}])
    result = _equipment(**profile, fighting_styles=[
        {"source": f"{class_id}_feat_4", "style": "defense"},
    ])
    assert result["_style_selected"] == ["defense"]


def test_public_style_replacement_and_feat_cost_survive_confirmation_and_reopen(tmp_path):
    database = tmp_path / "style-level.db"
    prior = [{"source": "fighter", "style": "defense"}]
    selected = [{"source": "fighter", "style": "archery"},
                {"source": "fighter_feat_4", "style": "defense"}]
    with AuthorityOwner.open(database, "test") as owner:
        create(owner, level=3, multiclass_ready=True,
               participant_overrides={"fighting_styles": prior})
        award(owner)
        before = owner.snapshot("advancement")
        proposal = build(owner)
        proposal["build"][0]["fighting_styles"] = []
        with pytest.raises(CommandRejected, match="Preserve each earned Fighting Style"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "remove"))
        proposal["class_id"] = "barbarian"
        proposal["build"][0]["fighting_styles"] = selected[:1]
        with pytest.raises(CommandRejected, match="Only Fighter"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "wrong-class"))
        proposal["class_id"] = "fighter"
        proposal["build"][0]["fighting_styles"] = selected
        proposal["build"][0]["abilities"]["strength"] += 2
        with pytest.raises(CommandRejected, match="spends this level's feat choice"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "double-spend"))
        assert owner.snapshot("advancement").state == before.state
        assert owner.snapshot("advancement").participants == before.participants
        proposal["build"][0]["abilities"]["strength"] -= 2
        waiting = owner.execute_action(command(owner, "level_up_srd_character", proposal, "styles"))
        assert owner.snapshot("advancement").participants == before.participants
        assert resolve(owner, waiting).status is ActionStatus.COMMITTED
        saved = owner.snapshot("advancement")
        assert saved.participants[0].data["fighting_styles"] == selected
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("advancement").participants == saved.participants
        assert owner.snapshot("advancement").state == saved.state


def test_feat_granted_style_cannot_be_replaced_as_fighters_original_choice(tmp_path):
    with AuthorityOwner.open(tmp_path / "fixed-style.db", "test") as owner:
        create(owner, level=4, participant_overrides={"fighting_styles": [
            {"source": "fighter", "style": "archery"},
            {"source": "fighter_feat_4", "style": "defense"},
        ]})
        award(owner)
        before = owner.snapshot("advancement")
        proposal = build(owner)
        proposal["build"][0]["fighting_styles"] = [
            {"source": "fighter", "style": "archery"},
            {"source": "fighter_feat_4", "style": "great_weapon_fighting"},
        ]
        with pytest.raises(CommandRejected, match="Only Fighter"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "replace-feat"))
        assert owner.snapshot("advancement").participants == before.participants
        assert owner.snapshot("advancement").state == before.state



def test_champion_level_requires_its_new_style_before_confirmation(tmp_path):
    from sylvia_gm.domain.authority import AwaitingChoiceReceipt

    with AuthorityOwner.open(tmp_path / "champion.sqlite3", "test") as owner:
        create(owner, level=6, participant_overrides={"fighting_styles": [
            {"source": "fighter", "style": "archery"},
        ]})
        award(owner)
        proposal = build(owner)
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected, match="Champion.*Fighting Style"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "missing"))
        assert owner.snapshot("advancement").state == before.state
        proposal["build"][0]["fighting_styles"] = [
            {"source": "fighter", "style": "archery"},
            {"source": "champion", "style": "defense"},
        ]
        receipt = owner.execute_action(command(
            owner, "level_up_srd_character", proposal, "complete",
        ))
        assert isinstance(receipt, AwaitingChoiceReceipt)


def test_multiclass_fighter_requires_its_first_style_before_confirmation(tmp_path):
    from sylvia_gm.domain.authority import AwaitingChoiceReceipt

    with AuthorityOwner.open(tmp_path / "first-style.sqlite3", "test") as owner:
        create(owner, multiclass_ready=True, participant_overrides={
            "classes": [{"id": "barbarian", "level": 1}], "fighting_styles": [],
        })
        award(owner)
        proposal = build(owner)
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected, match="first Fighter level"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "missing"))
        assert owner.snapshot("advancement").state == before.state
        proposal["build"][0]["fighting_styles"] = [{"source": "fighter", "style": "archery"}]
        receipt = owner.execute_action(command(
            owner, "level_up_srd_character", proposal, "complete",
        ))
        assert isinstance(receipt, AwaitingChoiceReceipt)


def test_level_up_checks_new_cantrip_alternative_against_new_style_choice(tmp_path):
    with AuthorityOwner.open(tmp_path / "paladin-style.db", "test") as owner:
        old_spells = ["bless", "cure_wounds"]
        create(owner, participant_overrides={
            "classes": [{"id": "paladin", "level": 1}],
            "spellcasting_grants": [{"source": "paladin", "ability": "charisma",
                                     "spells": old_spells}],
        }, entity_overrides={"prepared_spells": old_spells,
                             "resources": {"hit_die": 0, "slot_1": 2},
                             "resource_maxima": {"hit_die": 1, "slot_1": 2}})
        award(owner)
        proposal = build(owner)
        proposal["class_id"] = "paladin"
        choice = proposal["build"][0]
        choice["fighting_styles"] = [{"source": "paladin", "style": "defense"}]
        choice["spellcasting"] = [
            {"source": "paladin", "ability": "charisma", "spells": old_spells + ["divine_favor"]},
            {"source": "paladins_smite", "ability": "charisma", "spells": ["divine_smite"]},
            {"source": "blessed_warrior", "ability": "charisma",
             "spells": ["guidance", "sacred_flame"]},
        ]
        choice["prepared_spells"] = [spell for grant in choice["spellcasting"]
                                     for spell in grant["spells"]]
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected, match="not both"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "both"))
        assert owner.snapshot("advancement").state == before.state
        assert owner.snapshot("advancement").participants == before.participants
        choice["fighting_styles"] = []
        alternative = choice["spellcasting"].pop()
        choice["prepared_spells"] = [spell for grant in choice["spellcasting"]
                                     for spell in grant["spells"]]
        with pytest.raises(CommandRejected, match="Fighting Style or its cantrip alternative"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "neither"))
        assert owner.snapshot("advancement").state == before.state
        choice["spellcasting"].append(alternative)
        choice["prepared_spells"].extend(alternative["spells"])
        waiting = owner.execute_action(command(
            owner, "level_up_srd_character", proposal, "alternative",
        ))
        assert resolve(owner, waiting).status is ActionStatus.COMMITTED
        assert owner.snapshot("advancement").participants[0].data["fighting_styles"] == []
