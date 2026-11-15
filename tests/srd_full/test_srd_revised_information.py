"""Original SRD information spells through complete acquisition and public owners.

No private effect calls, source injection, forced dice, sparse grants or retail
parity assumptions. Root chooses and normally admits the candidate.
"""
import json
from dataclasses import replace
import pytest
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import IdempotencyConflict
from tests.srd_full.srd_revised_native_fixture import native_utility_case, SRD
from tests.srd_full.srd_revised_addition_classes import bard_factory, druid_factory


def _records(case, spell):
    return [r for r in case.lasting().get("turn_effects", [])
        if r.get("kind") == "information_spell" and r.get("spell") == spell]


def _proxy(case):
    identity = _records(case, "arcane_eye")[0]["proxy_id"]
    return next(e for e in case.lasting()["spatial_scene"]["entities"] if e["id"] == identity)


def _summary(receipt):
    return {r["label"]: r["value"] for r in receipt.result["result_card"]["summary"]}


def _query(case):
    receipt, command = case.act("query_srd_information_spell", caster_state="hero", spell="arcane_eye")
    return _summary(receipt), receipt, command


def _retry(case, receipt, command):
    saved = case.snapshot()
    assert case.execute(command) == receipt and case.snapshot() == saved
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot() == saved


def _write(case, name, **observations):
    (case.path.parent / name).write_text(json.dumps({"acquisition": case.acquisition,
        "package_id": case.record["id"], **thaw_json_value(observations)}, indent=2), encoding="utf-8")


def _wall(identity, low=-10, high=10):
    return {"id": identity, "map_id": "utility",
        "volume": {"kind": "box", "min": [10, low, -10], "max": [11, high, 10]},
        "active": True, "movement": "blocking", "occupies_cells": True,
        "visibility": "opaque", "metadata": {"material": "stone"},
        "destroyed": {"active": False, "movement": "passable", "visibility": "clear"}}


def _animals(native_utility_case, tmp_path, name, *, level=1, **kwargs):
    scene_changes = {"guard": {"metadata": {"participant_ref": "guard", "plane": "material",
        "lighting": kwargs.get("lighting", "bright"), "creature_type": "beast"}},
        **kwargs.pop("scene_entity_changes", {})}
    return native_utility_case(tmp_path, name, spell="speak_with_animals", level=level,
        character_factory=druid_factory,
        entity_changes={"guard": {"creature_type": "beast", "languages": []},
            "other": {"languages": ["infernal"]}, **kwargs.pop("entity_changes", {})},
        scene_entity_changes=scene_changes, **kwargs)


def _talk(case, **choices):
    return case.act("communicate_srd_spell", listener_state="hero", speaker_state="guard", **choices)


def _ritual_parameters(case):
    names = {"spell", "caster", "caster_state", "casting_mode", "slot_resource",
        "verbal_component_available", "somatic_component_available", "material_component_available",
        "material_item_ids", "spell_grant", "spellbook_readable"}
    return {k: v for k, v in (case.parameters | {"casting_mode": "ritual", "slot_resource": "ritual"}).items()
        if k in names}


def test_arcane_eye_pending_paid_creation_proxy_and_retry(tmp_path, native_utility_case):
    case = native_utility_case(tmp_path, "arcane-eye-paid", spell="arcane_eye", level=7)
    assert case.acquisition["verified_saved_state"] is True
    parameters = case.parameters | {"information_position": [5, 0, 0], "information_lighting": "bright"}
    slots = dict(case.entities()["hero"]["resources"])
    materials = thaw_json_value(case.entities()["hero"]["spell_materials"])
    case.start()
    case.act("spend_combat_movement", distance=10, difficult_terrain=False)
    begun, command = case.act("begin_srd_spell_action", **parameters)
    assert case.snapshot().state[SRD + ".combat"]["action_available"] is False
    assert case.snapshot().state[SRD + ".combat"]["movement_remaining"] == 20
    assert case.entities()["hero"]["resources"] == slots and not _records(case, "arcane_eye")
    _retry(case, begun, command)
    pending = next(iter(case.lasting()["pending_srd_actions"]))
    cast, command = case.act("complete_srd_pending_action", pending_action_id=pending)
    source = _records(case, "arcane_eye")[0]
    assert source["target"] == "hero" and source["caster"] == "hero" and source["concentration"] is True
    assert source["expires_tick"] - source["started_tick"] == 1200
    assert case.entities()["hero"]["concentration_spell_id"] == "arcane_eye"
    assert case.entities()["hero"]["resources"] == slots | {"spell_slot_4": slots["spell_slot_4"] - 1}
    assert case.entities()["hero"]["spell_materials"] == materials
    eye = _proxy(case)
    assert eye["position"] == [5, 0, 0] and eye["active"] is True and eye["movement"] == "passable"
    assert eye["metadata"]["invisible"] is True and eye["metadata"]["invulnerable"] is True
    assert eye["metadata"]["plane"] == "material" and eye["metadata"]["source_caster"] == "hero"
    _retry(case, cast, command)
    query, receipt, command = _query(case)
    assert "guard: seeing" in query["Information"]
    _retry(case, receipt, command)
    saved = case.snapshot()
    with pytest.raises(IdempotencyConflict):
        case.execute(replace(command, parameters=command.parameters | {"spell": "detect_magic"}))
    assert case.snapshot() == saved
    _write(case, "arcane-eye-paid-evidence.json", source=source, eye=eye, query=query,
        casting_card=cast.result["result_card"], slots_before=slots)


def test_arcane_eye_invalid_components_slot_geometry_and_ritual_atomic(tmp_path, native_utility_case):
    case = native_utility_case(tmp_path, "arcane-eye-invalid", spell="arcane_eye", level=7)
    params = case.parameters | {"information_position": [5, 0, 0], "information_lighting": "bright"}
    for tool in ("cast_spell_outside_combat", "begin_srd_spell_action"):
        for changes in ({"verbal_component_available": False}, {"somatic_component_available": False},
            {"material_component_available": False}, {"material_item_ids": []},
            {"slot_resource": "spell_slot_3"}, {"slot_resource": "cantrip"},
            {"slot_resource": "ritual", "casting_mode": "ritual"},
            {"information_position": [30.01, 0, 0], "origin_distance": 0},
            {"information_position": [5, 0]}, {"information_sense": "hearing"}):
            case.reject(tool, params | changes)
    assert not _records(case, "arcane_eye")


def test_arcane_eye_actual_all_direction_sight_darkvision_plane_and_invisible_controls(tmp_path, native_utility_case):
    case = native_utility_case(tmp_path, "arcane-eye-sight", spell="arcane_eye", level=7,
        lighting="darkness", positions={"hero": [0, 0, 0], "guard": [-25, 0, 0],
            "other": [-25.01, 0, 0], "extra": [5, 20, 0]},
        entity_changes={"hero": {"conditions": ["blinded"]}, "extra": {"conditions": ["invisible"]}})
    case.cast(information_position=[5, 0, 0], information_lighting="darkness")
    query, _, _ = _query(case)
    assert "guard: seeing" in query["Information"]
    assert "other: seeing" not in query["Information"] and "extra: seeing" not in query["Information"]
    # The eye's own sight is independent of Hero's blinded eyes and facing.
    assert case.entities()["hero"]["conditions"] == ["blinded"]
    plane = native_utility_case(tmp_path, "arcane-eye-plane", spell="arcane_eye", level=7,
        scene_entity_changes={"guard": {"metadata": {"participant_ref": "guard", "plane": "ethereal",
            "lighting": "bright", "creature_type": "humanoid"}}})
    plane.cast(information_position=[5, 0, 0], information_lighting="bright")
    assert "guard: seeing" not in _query(plane)[0]["Information"]
    magical = native_utility_case(tmp_path, "arcane-eye-magical-darkness", spell="arcane_eye", level=7,
        lighting="magical_darkness")
    magical.cast(information_position=[5, 0, 0], information_lighting="magical_darkness")
    assert "guard: seeing" not in _query(magical)[0]["Information"]
    _write(case, "arcane-eye-sight-evidence.json", query=query,
        actual_positions=case.lasting()["spatial_scene"]["entities"])


@pytest.mark.parametrize("gap,permitted", [(1 / 12, True), (0.999 / 12, False)])
def test_arcane_eye_actual_one_inch_clearance_and_bonus_action_budget(tmp_path, native_utility_case, gap, permitted):
    case = native_utility_case(tmp_path, "arcane-eye-gap-" + str(permitted), spell="arcane_eye", level=7,
        geometry=[_wall("lower", high=-gap / 2), _wall("upper", low=gap / 2)])
    case.cast(information_position=[5, 0, 0], information_lighting="bright")
    case.start()
    case.reject("operate_srd_information_proxy", {"caster_state": "hero", "spell": "arcane_eye",
        "destination": [36, 0, 0]})
    assert case.entities()["hero"]["bonus_action_available"] is True
    parameters = {"caster_state": "hero", "spell": "arcane_eye", "destination": [15, 0, 0]}
    if permitted:
        receipt, command = case.act("operate_srd_information_proxy", **parameters)
        assert _proxy(case)["position"] == [15, 0, 0]
        assert case.entities()["hero"]["bonus_action_available"] is False
        assert case.snapshot().state[SRD + ".combat"]["action_available"] is True
        assert case.snapshot().state[SRD + ".combat"]["movement_remaining"] == 30
        _retry(case, receipt, command)
        case.reject("operate_srd_information_proxy", parameters | {"destination": [16, 0, 0]})
    else:
        case.reject("operate_srd_information_proxy", parameters)
        assert _proxy(case)["position"] == [5, 0, 0]
        assert case.entities()["hero"]["bonus_action_available"] is True


def test_arcane_eye_actual_proxy_observation_and_damage_target_admission(tmp_path, native_utility_case):
    case = native_utility_case(tmp_path, "arcane-eye-observer", spell="arcane_eye", level=7,
        extra_spells=("see_invisibility",), entity_changes={"hero": {"spell_materials": [
            {"id": "focus", "inventory_item": "staff", "kind": "arcane_focus", "components": [],
                "quantity": 1, "value_cp": 0}]}}, scene_objects=[{
            "id": "crate", "map_id": "utility", "position": [10, 5, 0],
            "footprint": {"kind": "box", "half_extents": [0.4, 0.4]}, "height": 1,
            "facing": [1, 0], "active": True, "movement": "blocking", "visibility": "opaque",
            "metadata": {"plane": "material", "lighting": "bright", "is_object": True}}])
    case.cast(information_position=[5, 0, 0], information_lighting="bright", material_item_ids=["focus"])
    eye = _proxy(case)
    ordinary, _ = case.act("observe_srd_information_proxy", observer_state="guard", proxy_id=eye["id"])
    assert _summary(ordinary)["Visible"] is False
    case.cast(spell="see_invisibility", slot_resource="spell_slot_2", target="hero", targets=["hero"],
        material_item_ids=["focus"])
    capable, _ = case.act("observe_srd_information_proxy", observer_state="hero", proxy_id=eye["id"])
    assert _summary(capable)["Visible"] is True
    assert _summary(capable)["Luminous fist-size orb"] is False
    assert case.entities()["hero"]["concentration_spell_id"] == "arcane_eye"
    case.reject("prepare_srd_spell_object", {"object_id": eye["id"], "material": "stone",
        "size": "tiny", "durability": "fragile"}, match="Arcane Eye.*invulnerable")
    ordinary_object, ordinary_command = case.act("prepare_srd_spell_object", object_id="crate",
        material="wood", size="tiny", durability="fragile")
    crate = next(e for e in case.lasting()["spatial_scene"]["entities"] if e["id"] == "crate")
    assert crate["metadata"]["srd_spell_object"]["hp"] > 0
    assert _proxy(case)["metadata"].get("srd_spell_object") is None
    _retry(case, ordinary_object, ordinary_command)
    case.start()
    # Real source-qualified object classification and ordinary creature-target
    # attack admission both reject the eye, without a caller immunity boolean.
    case.reject("resolve_combat_attack", {"actor": "hero", "target": eye["id"],
        "weapon": "spear", "attack_mode": "melee", "attack_ability": "strength",
        "attack_distance": 5, "attack_sequence": "primary", "circumstantial_modifier": 0,
        "use_graze_on_miss": False, "wielding_two_handed": False, "mounted": False,
        "advantage": False, "disadvantage": False, "actor_can_see_target": True,
        "target_can_see_actor": True, "target_is_charmer": False, "fear_source_in_sight": False})
    assert _proxy(case)["active"] is True and _proxy(case)["position"] == eye["position"]
    assert case.snapshot().state[SRD + ".combat"]["action_available"] is True
    _write(case, "arcane-eye-observer-evidence.json", ordinary=_summary(ordinary), capable=_summary(capable), eye=eye,
        actual_crate_profile=crate["metadata"]["srd_spell_object"],
        limitation="Current saved source/object and creature target owners; no future damage tool claim.")


def test_arcane_eye_real_zero_hp_incapacitation_retires_source_and_proxy(tmp_path, native_utility_case):
    # A GM-admitted injury follows complete character materialization; the
    # actual public spell deals at least six force damage with unmodified dice.
    case = native_utility_case(tmp_path, "arcane-eye-incapacitation", spell="arcane_eye", level=7,
        extra_spells=("magic_missile",), entity_changes={"hero": {"hp": 6}})
    case.cast(information_position=[5, 0, 0], information_lighting="bright")
    identity = _proxy(case)["id"]
    slots = case.entities()["hero"]["resources"]["spell_slot_1"]
    hurt, command = case.cast(spell="magic_missile", slot_resource="spell_slot_1",
        target="hero", targets=["hero"], target_distance=0, targets_max_distance=0, material_item_ids=[])
    assert case.entities()["hero"]["hp"] == 0 and case.entities()["hero"]["is_alive"] is True
    assert "unconscious" in case.entities()["hero"]["conditions"]
    assert case.entities()["hero"]["resources"]["spell_slot_1"] == slots - 1
    assert not _records(case, "arcane_eye") and case.entities()["hero"].get("concentration_spell_id") is None
    assert next(e for e in case.lasting()["spatial_scene"]["entities"] if e["id"] == identity)["active"] is False
    case.reject("query_srd_information_spell", {"caster_state": "hero", "spell": "arcane_eye"})
    _retry(case, hurt, command)
    _write(case, "arcane-eye-incapacitation-evidence.json", health=case.entities()["hero"]["hp"],
        actual_damage_card=hurt.result["result_card"], alive=True, source_retired=True)


def test_arcane_eye_source_expiry_upcast_dismissal_and_replacement(tmp_path, native_utility_case):
    case = native_utility_case(tmp_path, "arcane-eye-expiry", spell="arcane_eye", level=9,
        extra_spells=("detect_magic",))
    cast, command = case.cast(slot_resource="spell_slot_5", information_position=[5, 0, 0], information_lighting="bright")
    source = dict(_records(case, "arcane_eye")[0]); identity = source["proxy_id"]
    assert source["expires_tick"] - source["started_tick"] == 1200
    remaining = source["expires_tick"] - case.lasting()["lasting_tick"]
    assert remaining > 2 and remaining % 2 == 0
    case.act("advance_srd_lasting_time", elapsed_rounds=int(remaining / 2) - 1)
    assert "guard: seeing" in _query(case)[0]["Information"]
    case.reopen()
    expired, expiry_command = case.act("advance_srd_lasting_time", elapsed_rounds=1)
    assert not _records(case, "arcane_eye")
    assert next(e for e in case.lasting()["spatial_scene"]["entities"] if e["id"] == identity)["active"] is False
    assert case.entities()["hero"].get("concentration_spell_id") is None
    case.reject("query_srd_information_spell", {"caster_state": "hero", "spell": "arcane_eye"})
    _retry(case, expired, expiry_command)
    _retry(case, cast, command)
    case.cast(information_position=[5, 0, 0], information_lighting="bright")
    dismissed, dismiss_command = case.act("dismiss_srd_information_spell", caster_state="hero", spell="arcane_eye")
    assert not _records(case, "arcane_eye") and case.entities()["hero"].get("concentration_spell_id") is None
    _retry(case, dismissed, dismiss_command)
    case.cast(information_position=[5, 0, 0], information_lighting="bright")
    case.cast(spell="detect_magic", slot_resource="spell_slot_1", material_item_ids=[])
    assert not _records(case, "arcane_eye") and case.entities()["hero"]["concentration_spell_id"] == "detect_magic"
    assert next(e for e in case.lasting()["spatial_scene"]["entities"] if e["id"] == identity)["active"] is False
    _write(case, "arcane-eye-expiry-evidence.json", source=source, final_concentration="detect_magic")


def test_animals_complete_druidic_acquisition_pending_paid_understanding_and_retry(tmp_path, native_utility_case):
    case = _animals(native_utility_case, tmp_path, "animals-paid")
    assert case.acquisition["verified_saved_state"] is True
    assert case.acquisition["requested_spell_sources"]["speak_with_animals"] == ["druidic"]
    assert case.acquisition["source_counts"] == {"druid": 6, "druidic": 1}
    case.reject("communicate_srd_spell", {"listener_state": "hero", "speaker_state": "guard"})
    slots = dict(case.entities()["hero"]["resources"])
    case.start()
    begun, command = case.act("begin_srd_spell_action", **(case.parameters | {"material_component_available": False}))
    assert case.snapshot().state[SRD + ".combat"]["action_available"] is False
    assert case.entities()["hero"]["resources"] == slots and not _records(case, "speak_with_animals")
    _retry(case, begun, command)
    pending = next(iter(case.lasting()["pending_srd_actions"]))
    cast, command = case.act("complete_srd_pending_action", pending_action_id=pending)
    source = _records(case, "speak_with_animals")[0]
    assert source["caster"] == "hero" and source["target"] == "hero" and source["concentration"] is False
    assert source["expires_tick"] - source["started_tick"] == 200 and source["owner"] == "__time_only__"
    assert case.entities()["hero"]["resources"] == slots | {"spell_slot_1": slots["spell_slot_1"] - 1}
    assert case.entities()["hero"].get("concentration_spell_id") is None
    _retry(case, cast, command)
    talk, command = _talk(case)
    summary = _summary(talk)
    assert summary["Understands"] is True and summary["Can reply"] is True
    assert summary["All Influence skills"] is True
    _retry(case, talk, command)
    saved = case.snapshot()
    with pytest.raises(IdempotencyConflict):
        case.execute(replace(command, parameters=command.parameters | {"speaker_state": "other"}))
    assert case.snapshot() == saved
    _write(case, "animals-paid-evidence.json", source=source, conversation=summary,
        slot_before=slots, no_material_required=True)


def test_animals_invalid_components_source_and_real_speech_boundaries_atomic(tmp_path, native_utility_case):
    case = _animals(native_utility_case, tmp_path, "animals-invalid")
    for tool in ("cast_spell_outside_combat", "begin_srd_spell_action"):
        for changes in ({"verbal_component_available": False}, {"somatic_component_available": False},
            {"slot_resource": "cantrip"}, {"spell_grant": "wizard"}):
            case.reject(tool, case.parameters | changes)
    case.cast(material_component_available=False, material_item_ids=[])
    for changes in ({"channel": "signs"}, {"perceived": False}, {"speaker_state": "other"}):
        case.reject("communicate_srd_spell", {"listener_state": "hero", "speaker_state": "guard"} | changes)
    case.reject("communicate_srd_spell", {"listener_state": "hero", "speaker_state": "guard",
        "purpose": "past_day", "hours_ago": 24.01})
    for purpose in ("nearby_places", "nearby_monsters", "past_day"):
        receipt, _ = _talk(case, purpose=purpose, hours_ago=24)
        assert _summary(receipt)["Information available"] is True
        # Eligibility is the actual original consumer's output. The source has
        # no saved Beast event-answer producer; do not invent any narrated fact.
    deaf = _animals(native_utility_case, tmp_path, "animals-deaf",
        entity_changes={"hero": {"conditions": ["deafened"]}})
    deaf.cast()
    deaf.reject("communicate_srd_spell", {"listener_state": "hero", "speaker_state": "guard"})


def test_animals_real_materialized_influence_action_and_shared_budget(tmp_path, native_utility_case):
    case = _animals(native_utility_case, tmp_path, "animals-influence")
    case.cast()
    case.start()
    receipt, command = _talk(case, purpose="influence", actor="hero", influence_skill="performance", target_number=10)
    result = _summary(receipt)
    assert result["All Influence skills"] is True
    assert result["Influence outcome"] in {"success", "failure"}
    assert isinstance(result["Influence total"], (int, float))
    assert case.snapshot().state[SRD + ".combat"]["action_available"] is False
    assert case.entities()["hero"]["bonus_action_available"] is True
    assert case.snapshot().state[SRD + ".combat"]["movement_remaining"] == 30
    _retry(case, receipt, command)
    case.reject("communicate_srd_spell", {"listener_state": "hero", "speaker_state": "guard",
        "purpose": "influence", "actor": "hero", "influence_skill": "persuasion"})
    _write(case, "animals-influence-evidence.json", real_derived_character_result=result,
        limitation="No dice outcome or NPC modifier is forced; ordinary Influence eligibility is consumed.")


def test_animals_ritual_actual_interval_components_no_slot_and_retry(tmp_path, native_utility_case):
    case = _animals(native_utility_case, tmp_path, "animals-ritual")
    slots = dict(case.entities()["hero"]["resources"])
    begin = _ritual_parameters(case)
    begun, command = case.act("begin_srd_spell_cast", **begin)
    assert not _records(case, "speak_with_animals") and case.entities()["hero"]["resources"] == slots
    _retry(case, begun, command)
    params = case.parameters | {"casting_mode": "ritual", "slot_resource": "ritual"}
    case.reject("cast_spell_outside_combat", params)
    continued, command = case.act("continue_srd_spell_cast", caster_state="hero", elapsed_rounds=100,
        uninterrupted=True, verbal_component_available=True, somatic_component_available=True,
        material_component_available=True)
    assert case.lasting()["lasting_tick"] >= 200
    _retry(case, continued, command)
    cast, command = case.act("cast_spell_outside_combat", **params)
    assert case.entities()["hero"]["resources"] == slots
    assert _records(case, "speak_with_animals")[0]["expires_tick"] - _records(case, "speak_with_animals")[0]["started_tick"] == 200
    assert _summary(_talk(case)[0])["Can reply"] is True
    _retry(case, cast, command)
    _write(case, "animals-ritual-evidence.json", source=_records(case, "speak_with_animals")[0],
        actual_tick=case.lasting()["lasting_tick"], slots_unchanged=slots)


def test_animals_upcast_exact_time_expiry_dismissal_and_reopen(tmp_path, native_utility_case):
    case = _animals(native_utility_case, tmp_path, "animals-expiry", level=3)
    slots = dict(case.entities()["hero"]["resources"])
    cast, command = case.cast(slot_resource="spell_slot_2")
    source = dict(_records(case, "speak_with_animals")[0])
    assert source["expires_tick"] - source["started_tick"] == 200
    assert case.entities()["hero"]["resources"] == slots | {"spell_slot_2": slots["spell_slot_2"] - 1}
    remaining = source["expires_tick"] - case.lasting()["lasting_tick"]
    assert remaining > 2 and remaining % 2 == 0
    case.act("advance_srd_lasting_time", elapsed_rounds=int(remaining / 2) - 1)
    assert _summary(_talk(case)[0])["Understands"] is True
    case.reopen()
    expired, expiry_command = case.act("advance_srd_lasting_time", elapsed_rounds=1)
    assert not _records(case, "speak_with_animals")
    case.reject("communicate_srd_spell", {"listener_state": "hero", "speaker_state": "guard"})
    _retry(case, expired, expiry_command)
    _retry(case, cast, command)
    case.cast()
    dismissed, command = case.act("dismiss_srd_information_spell", caster_state="hero", spell="speak_with_animals")
    assert not _records(case, "speak_with_animals")
    case.reject("communicate_srd_spell", {"listener_state": "hero", "speaker_state": "guard"})
    _retry(case, dismissed, command)
    _write(case, "animals-expiry-evidence.json", source=source, final_tick=case.lasting()["lasting_tick"])


def test_animals_nonconcentration_source_survives_actual_caster_incapacitation(tmp_path, native_utility_case):
    # The original level-10 Bard pool legally includes Wizard spells; acquire
    # every ordinary/subclass quota through the complete factory, then injure.
    case = native_utility_case(tmp_path, "animals-incapacitation", spell="speak_with_animals", level=10,
        character_factory=bard_factory, extra_spells=("magic_missile",), entity_changes={"hero": {"hp": 6}})
    assert case.acquisition["verified_saved_state"] is True
    case.cast()
    source = dict(_records(case, "speak_with_animals")[0])
    hurt, command = case.cast(spell="magic_missile", spell_grant="bard", slot_resource="spell_slot_1",
        target="hero", targets=["hero"], target_distance=0, targets_max_distance=0, material_item_ids=[])
    assert case.entities()["hero"]["hp"] == 0 and "unconscious" in case.entities()["hero"]["conditions"]
    assert case.entities()["hero"]["is_alive"] is True
    assert len(_records(case, "speak_with_animals")) == 1
    assert _records(case, "speak_with_animals")[0]["expires_tick"] == source["expires_tick"]
    assert case.entities()["hero"].get("concentration_spell_id") is None
    _retry(case, hurt, command)
    _write(case, "animals-incapacitation-evidence.json", source=source,
        actual_damage_card=hurt.result["result_card"],
        limitation="Source persistence makes no claim that an unconscious creature perceives speech or that it died.")
