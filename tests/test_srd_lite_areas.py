"""Actual native Lite combat Cone/Sphere geometry, saved intent, objects and ownership."""
from dataclasses import replace
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole, thaw_json_value
from sylvia_gm.runtime import _effect_executor
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import Case, VARIANT_ID
from tests.test_srd_lite_healing import healing_case


@pytest.fixture
def area_case(healing_case):
    """Use ordinary encounter entry before authoritative combat geometry checks."""
    def make(*args, **kwargs):
        case = healing_case(*args, **kwargs)
        case.start()
        return case
    return make


class SceneAreaCase(Case):
    """Admit an actual scene through CampaignCreate, then use ordinary object tools."""
    def __init__(self, path, spell, *, extra_scene=(), geometry=(), lasting_changes=None, **changes):
        self.campaign_id = "lite"
        super().__init__(path, spell, **changes)
        initial = self.snapshot()
        state = thaw_json_value(initial.state)
        state[VARIANT_ID + ".state"].update(lasting_changes or {})
        scene = state[VARIANT_ID + ".state"]["spatial_scene"]
        scene["entities"] += list(extra_scene)
        scene["geometry"] += list(geometry)
        self.owner.create_campaign(CampaignCreate(
            campaign_id="lite-area-scene", display_name="Actual Lite area objects",
            writer=WriterRole.DM, package_lock=initial.package_lock, turn_id="turn",
            participants=initial.participants,
            initial_state=tuple(StateValue(VARIANT_ID, key.removeprefix(VARIANT_ID + "."), value) for key, value in state.items()),
        ))
        self.campaign_id = "lite-area-scene"

    def snapshot(self):
        return self.owner.snapshot(self.campaign_id)

    def command(self, tool, parameters, identity=None):
        return replace(super().command(tool, parameters, identity), campaign_id=self.campaign_id)


@pytest.fixture
def area_scene(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    opened = []

    def make(spell, **changes):
        case = SceneAreaCase(tmp_path / f"{len(opened)}.db", spell, **changes)
        opened.append(case)
        return case

    yield make
    for case in opened:
        archive_closed_case(case)


def _object(identity, position, **facts):
    return {"id": identity, "map_id": "lite", "position": position,
            "footprint": {"kind": "box", "half_extents": [0.4, 0.4]}, "height": 1,
            "facing": [1, 0], "active": True, "movement": "passable", "visibility": "clear",
            "metadata": {"is_object": True, "plane": "material", "lighting": "bright", **facts}}


def _scene_objects(case):
    return {row["id"]: row for row in case.lasting()["spatial_scene"]["entities"] if row["metadata"].get("is_object")}


@pytest.mark.parametrize("spell,base,sides,dice", [("burning_hands", 1, 6, 3), ("fireball", 3, 6, 8)])
@pytest.mark.parametrize("extra_slot", [0, 2])
def test_native_area_roll_save_damage_scene_membership_budget_and_reopen(area_case, monkeypatch, spell, base, sides, dice, extra_slot):
    positions = {"hero": [-20, 0, 0] if spell == "fireball" else [0, 0, 0], "guard": [10, 3, 0], "other": [12, -3, 0], "extra": [90, 0, 0]}
    case = area_case(spell, positions=positions, entity_changes={
        "guard": {"saving_throw_modifiers": dict.fromkeys(("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"), -100)},
        "other": {"saving_throw_modifiers": dict.fromkeys(("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"), 100)},
    })
    original = _effect_executor.dice_roll
    damage = []

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        if sides != 20:
            damage.append(result)
        return result

    monkeypatch.setattr(_effect_executor, "dice_roll", recorded)
    slot = base + extra_slot
    before = case.snapshot()
    choices = {"instant_area_direction": [1, 0]} if spell == "burning_hands" else {"environment_origin": [10, 0, 3]}
    receipt, command = case.act("cast_spell", **(case.parameters | choices | {"slot_resource": f"spell_slot_{slot}", "targets": ["extra"], "targets_max_distance": 0, "origin_distance": 0}))
    assert len(damage) == 1 and damage[0].sides == sides and len(damage[0].results) == dice + extra_slot
    raw = damage[0].total
    assert case.entities()["guard"]["hp"] == 1000 - raw
    assert case.entities()["other"]["hp"] == 1000 - raw // 2
    assert case.entities()["extra"]["hp"] == 1000
    expected = dict(before.state["sylvia.srd-lite.bg3.entities"]["hero"]["resources"])
    expected[f"spell_slot_{slot}"] -= 1
    assert case.entities()["hero"]["resources"] == expected
    assert case.snapshot().state["sylvia.srd-lite.bg3.combat"]["action_available"] is False
    assert not case.entities()["hero"].get("concentration_spell_id")
    text = "\n".join(row["value"] for row in receipt.result["result_card"]["details"])
    assert "failure" in text.lower() and "success" in text.lower()
    saved = case.snapshot()
    assert case.execute(command) == receipt
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert len(damage) == 1


@pytest.mark.parametrize("offset,included", [(0.4, True), (0.41, False)])
def test_sphere_uses_closed_body_boundary_and_excludes_far_body(area_case, offset, included):
    case = area_case("fireball", positions={"hero": [-20, 0, 0], "guard": [40 + offset, 0, 0], "other": [-90, 0, 0], "extra": [90, 0, 0]})
    case.act("cast_spell", **(case.parameters | {"environment_origin": [20, 0, 3], "targets": ["other", "extra"]}))
    assert (case.entities()["guard"]["hp"] < 1000) is included
    assert case.entities()["other"]["hp"] == case.entities()["extra"]["hp"] == 1000


@pytest.mark.parametrize("spell", ["fireball"])
def test_forged_origin_range_rejects_atomically(area_case, spell):
    case = area_case(spell)
    case.reject("cast_spell", case.parameters | {"environment_origin": [150.5, 0, 3], "origin_distance": 0, "origin_has_clear_path": True, "targets": ["guard"]}, match="actual area point")


@pytest.mark.parametrize("offset,included", [(0.4, True), (0.41, False)])
def test_cone_closed_base_boundary_and_direction_are_authoritative(area_case, offset, included):
    case = area_case("burning_hands", positions={"hero": [0, 0, 0], "guard": [15.4 + offset, 0, 0], "other": [-50, 0, 0], "extra": [-5, 0, 0]})
    caster_hp = case.entities()["hero"]["hp"]
    case.act("cast_spell", **(case.parameters | {"instant_area_apex": [0.4, 0, 3], "instant_area_direction": [1, 0], "targets": ["other", "extra"]}))
    assert (case.entities()["guard"]["hp"] < 1000) is included
    assert case.entities()["other"]["hp"] == case.entities()["extra"]["hp"] == 1000
    assert case.entities()["hero"]["hp"] == caster_hp


def test_caller_cannot_place_a_cone_apex_outside_caster_body(area_case):
    case = area_case("burning_hands")
    case.reject("cast_spell", case.parameters | {"instant_area_apex": [50, 0, 3], "instant_area_direction": [1, 0], "targets": ["guard"]}, match="actual source volume")


@pytest.mark.parametrize("spell", ["burning_hands", "fireball"])
def test_staged_area_retains_actual_origin_through_saved_reopen_and_completion(area_case, spell):
    case = area_case(spell, positions={"hero": [-20, 0, 0] if spell == "fireball" else [0, 0, 0],
        "guard": [10, 3, 0] if spell == "fireball" else [3, 10, 0],
        "other": [80, -30, 0], "extra": [-80, -30, 0] if spell == "fireball" else [10, 0, 0]})
    resources = dict(case.entities()["hero"]["resources"])
    choices = {"instant_area_apex": [0, 0.4, 3], "instant_area_direction": [0, 1]} if spell == "burning_hands" else {"environment_origin": [10, 0, 3]}
    begun, begin_command = case.act("begin_srd_spell_action", **(case.parameters | choices | {"targets": ["extra"]}))
    pending_id = begun.result["context"]["_pending_action_id"]
    parameters = case.lasting()["pending_srd_actions"][pending_id]["parameters"]
    assert all(tuple(parameters[key]) == tuple(value) for key, value in choices.items())
    assert case.entities()["guard"]["hp"] == 1000
    assert case.entities()["hero"]["resources"] == resources
    assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is False
    pending = case.snapshot()
    case.reopen()
    assert case.execute(begin_command) == begun and case.snapshot().state == pending.state
    receipt, command = case.act("complete_srd_pending_action", pending_action_id=pending_id)
    assert case.entities()["guard"]["hp"] < 1000
    assert case.entities()["extra"]["hp"] == 1000
    assert case.entities()["hero"]["resources"][f"spell_slot_{1 if spell == 'burning_hands' else 3}"] == 2
    assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is False
    saved = case.snapshot()
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state


def test_invalid_staged_area_origin_rejects_before_action_reservation(area_case):
    case = area_case("fireball")
    case.reject("begin_srd_spell_action", case.parameters | {"environment_origin": [150.5, 0, 3],
        "origin_distance": 0, "origin_has_clear_path": True, "targets": ["guard"]}, match="actual area point")


@pytest.mark.parametrize("spell", ["burning_hands", "fireball"])
def test_native_area_ignites_unheld_objects_once_without_inventing_direct_object_damage(area_scene, monkeypatch, spell):
    objects = [_object("fuel", [10, 3, 0]), _object("carried", [10, 2, 0], carried_by="guard"), _object("outside", [90, 0, 0])]
    case = area_scene(spell, extra_scene=objects, positions={"hero": [-20, 0, 0] if spell == "fireball" else [0, 0, 0], "guard": [80, 0, 0], "other": [-80, 0, 0], "extra": [90, 30, 0]})
    for identity in ("fuel", "carried", "outside"):
        case.act("prepare_srd_spell_object", object_id=identity, material="wood", size="large", durability="resilient", magic_status="nonmagical")
        case.act("record_srd_ordinary_flame", object_id=identity, exposed=True, burning=False, flammability="flammable")
    case.start()
    before_objects = _scene_objects(case)
    before_resources = dict(case.entities()["hero"]["resources"])
    original = _effect_executor.dice_roll
    damage = []

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        assert sides == 6  # An object-only area needs no invented creature saving throws.
        damage.append(result)
        return result

    monkeypatch.setattr(_effect_executor, "dice_roll", recorded)
    choices = {"instant_area_direction": [1, 0]} if spell == "burning_hands" else {"environment_origin": [10, 3, 3]}
    receipt, command = case.act("cast_spell", **(case.parameters | choices | {"targets": ["guard"], "area_object_targets": ["carried", "outside"]}))
    assert len(damage) == 1 and damage[0].count == (3 if spell == "burning_hands" else 8)
    after_objects = _scene_objects(case)
    # Native spell text deals this packet to creatures and ignites objects.
    # The separate draft object-HP packet remains archived and inactive.
    assert after_objects["fuel"]["metadata"]["srd_spell_object"]["hp"] == before_objects["fuel"]["metadata"]["srd_spell_object"]["hp"]
    assert after_objects["fuel"]["metadata"]["srd_ordinary_flame"]["burning"] is True
    assert after_objects["carried"] == before_objects["carried"]
    assert after_objects["outside"] == before_objects["outside"]
    before_resources[f"spell_slot_{1 if spell == 'burning_hands' else 3}"] -= 1
    assert case.entities()["hero"]["resources"] == before_resources
    assert any(row["label"] == "fuel" and "ignited" in row["value"].lower() for row in receipt.result["result_card"]["details"])
    saved = case.snapshot()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert len(damage) == 1


def _saved_condition_source(target, caster, spell, **facts):
    return {"target_state_id": target, "caster_state_id": caster, "spell_id": spell,
        "installed": True, "condition": "charmed", "control_profile": "none",
        "suppressed": False, "sequence": 1, "concentration_sequence": 0,
        "started_tick": 0, "expires_tick": 7200, "save_ability": "wisdom", "save_dc": 15,
        "repeat_save_timing": "none", "spell_level": 1, "ongoing_duration": True,
        "cast_id": "saved-condition-1", "source_caster_id": caster, "dispel_ongoing": True,
        "caster_allies": [], **facts}


def _saved_condition_storage(source, target, **facts):
    return {"timed_condition_sources": {"tc01": source},
        "timed_condition_active_target_ids": [target],
        "timed_condition_free_source_ids": [f"tc{number:02}" for number in range(64, 1, -1)],
        "lasting_serial": 2, **facts}


def _selected_area_choices(spell):
    return {"instant_area_direction": [1, 0]} if spell == "burning_hands" else {"environment_origin": [10, 0, 3]}


@pytest.mark.parametrize("spell", ["burning_hands", "fireball"])
def test_actual_area_charmer_cannot_be_omitted_by_forged_target_list(area_scene, monkeypatch, spell):
    source = _saved_condition_source("hero", "guard", "charm_person", control_profile="friendly_charm")
    case = area_scene(spell, positions={"hero": [-20, 0, 0] if spell == "fireball" else [0, 0, 0],
        "guard": [10, 3, 0], "other": [-80, -30, 0], "extra": [80, -30, 0]},
        lasting_changes=_saved_condition_storage(source, "hero"),
        entity_changes={"hero": {"conditions": ["charmed"], "timed_condition_source_ids": ["tc01"],
            "timed_charm_caster_ids": ["guard"], "timed_condition_unowned_charmed": False}})
    case.start()
    def no_roll(*_args, **_kwargs):
        raise AssertionError("The actual Charmer must reject before any damage/save roll")
    monkeypatch.setattr(_effect_executor, "dice_roll", no_roll)
    parameters = {key: value for key, value in case.parameters.items() if key != "target"}
    case.reject("cast_spell", parameters | _selected_area_choices(spell) | {"targets": ["extra"]}, match="Charmer|charmer")


@pytest.mark.parametrize("spell", ["burning_hands", "fireball"])
def test_forged_unrelated_memory_recipient_remains_unharmed_and_owned(area_scene, spell):
    source = _saved_condition_source("extra", "other", "modify_memory", sequence=2,
        concentration_sequence=1, expires_tick=20, spell_level=5, cast_id="saved-memory-1",
        memory_event_id="event1", memory_mode="alter", memory_finished=False,
        memory_aborted=False, memory_applied=False, dialogue_profile="memory")
    case = area_scene(spell, positions={"hero": [-20, 0, 0] if spell == "fireball" else [0, 0, 0],
        "guard": [10, 3, 0], "other": [-80, -30, 0], "extra": [80, -30, 0]},
        lasting_changes=_saved_condition_storage(source, "extra", condition_caster_ids=["other"],
            active_memory_sources=1, has_memory_spells=True),
        entity_changes={
            "extra": {"timed_condition_source_ids": ["tc01"], "conditions": ["charmed", "incapacitated"],
                "timed_condition_unowned_charmed": False, "timed_condition_unowned_incapacitated": False,
                "timed_charm_caster_ids": ["other"], "memory_link_incapacitated": True,
                "memory_unaware": True, "has_memory_sources": True, "life_event_ids": ["event1"],
                "life_events": {"event1": {"id": "event1", "start_tick": 0, "end_tick": 0,
                    "true_description": "Saw the gate.", "perceived_description": "Saw the gate.",
                    "window_only": False, "modified": False}}},
            "other": {"concentration_spell_id": "modify_memory",
                "concentration_effect_family": "multi_target_timed_repeating_save_condition",
                "concentration_sequence": 1, "concentration_target_state_id": "extra",
                "concentration_target_state_ids": ["extra"], "concentration_started_tick": 0,
                "concentration_expires_tick": 20, "concentration_save_modifier": 0,
                "concentration_cast_id": "saved-memory-1"},
        })
    case.start()
    parameters = {key: value for key, value in case.parameters.items() if key != "target"}
    receipt, command = case.act("cast_spell", **(parameters | _selected_area_choices(spell) | {"targets": ["extra"]}))
    assert case.entities()["guard"]["hp"] < 1000 and case.entities()["extra"]["hp"] == 1000
    actual = case.lasting()["timed_condition_sources"]["tc01"]
    assert actual["installed"] is True and actual["dialogue_profile"] == "memory"
    assert actual["memory_aborted"] is actual["memory_finished"] is actual["memory_applied"] is False
    assert case.lasting()["active_memory_sources"] == 1
    assert case.entities()["other"]["concentration_spell_id"] == "modify_memory"
    assert case.entities()["hero"]["resources"][f"spell_slot_{1 if spell == 'burning_hands' else 3}"] == 2
    saved = case.snapshot()
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
