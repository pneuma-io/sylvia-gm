"""Selected paid owners replayed unchanged against the admitted source and loss binder.

These are owning-boundary probes, not package admission or installed-app claims.
The root runner supplies the actual admitted archive through SRD_LITE_CANDIDATE.
"""

import json
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts.srd_lite_catalogue import VARIANT_ID
from scripts.srd_lite_exploration_steed_loss import FACTS, RECORDS, RETIRE
from tests.test_srd_attack_budget import SRD, _combat_case
from tests.test_srd_lite_exploration_steed_loss import (
    _bridge,
    _calls,
    _facts,
    _nodes,
    _run,
)
from tests.test_srd_lite_exploration_steed_loss import (
    bound as _bound,
)
from tests.test_srd_lite_exploration_steed_loss import (
    native_owner_source as _native_owner_source,
)

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.runtime import RulesetRuntime

bound = _bound
native_owner_source = _native_owner_source

PAID_ROOTS = (
    FACTS,
    "cast_spell_outside_combat",
    "complete_srd_pending_action",
)


@pytest.fixture(scope="module")
def paid_owners(bound, native_owner_source):
    """Retain the native paid tool bodies and every actual callee, including false arms."""
    data = bound[0]
    original = native_owner_source[0]
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "cast_srd_reaction_spell",
        "spend_spell_casting_budget",
        "consume_srd_reaction",
        "cast_spell_independent_created_creature",
        "apply_srd_damage",
        "complete_srd_pending_action",
        "install_srd_feather_fall",
        "install_srd_feather_fall_target",
        "advance_srd_lasting_time",
    ):
        assert data["tools"][name] == original["tools"][name], name
    closure, pending = {}, list(PAID_ROOTS)
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    selected = {
        **{
            key: deepcopy(value)
            for key, value in data.items()
            if key not in {"tools", "procedures"}
        },
        "tools": deepcopy(closure),
    }
    print(f"1082 paid native closure: {len(closure)} owners, {_nodes(selected)} nodes")
    return RulesetRuntime(ruleset_dict=selected), data


def _paid_bridge(data):
    """Use the existing actual class caster fixture and exact synthetic native Steed source."""
    native_state, pc = _combat_case([{"id": "wizard", "level": 5}, {"id": "paladin", "level": 5}])
    initial = _bridge(cached=False)
    hero = initial.get_entity("hero")
    hero.update(native_state[SRD + ".entities"]["hero"])
    hero.update(
        name="Actual paid caster",
        hp=40,
        max_hp=40,
        temp_hp=0,
        creature_type="humanoid",
        size_rank=2,
        reaction_available=True,
        spell_slot_cast_this_turn=False,
        prepared_spells=["magic_missile", "find_steed"],
        resources={f"spell_slot_{level}": 3 for level in range(1, 4)},
        spell_materials=[],
    )
    pc.update(
        name="Actual paid caster",
        species="human",
        background="sage",
        size_rank=2,
        saving_throw_proficiencies=["intelligence", "wisdom"],
        origin_feats=["magic_initiate"],
        spellcasting_grants=[
            {
                "source": "wizard",
                "ability": "intelligence",
                "spells": ["magic_missile"],
            },
            {"source": "paladin", "ability": "charisma", "spells": ["find_steed"]},
        ],
        wizard_spellbook=["magic_missile"],
    )
    pc["abilities"].update(intelligence=18, charisma=18)
    assert set(hero["prepared_spells"]) <= set(data["definitions"]["srd_lite_catalogue"])
    for spell in hero["prepared_spells"]:
        assert data["definitions"]["spell_casting"][spell]["material_parts"] == []
    steed = initial.get_entity("steed")
    steed.update(
        creature_kind="monster",
        armor_class=1,
        temp_hp=0,
        max_hp=25,
        exhaustion_level=0,
        damage_immunities=[],
        damage_resistances=[],
        damage_vulnerabilities=[],
        resource_maxima={},
    )
    entities = {name: initial.get_entity(name) for name in ("bag", "saddle", "held-rope")} | {
        "hero": hero,
        "steed": steed,
    }
    bridge = AuthorityMemoryStateBridge(
        VARIANT_ID,
        {
            VARIANT_ID + ".state": initial.get_entity("state"),
            VARIANT_ID + ".combat": deepcopy(initial.combat_state),
            VARIANT_ID + ".entities": entities,
        },
        {
            "hero": {
                "participant_id": "hero",
                "kind": "player_character",
                "validation_status": "validated",
                "package_id": VARIANT_ID,
                "data": pc,
            }
        },
    )
    return bridge


def _project_and_capture(data, owner, result, parameters, tmp_path, step):
    """Save only the actual public card for the root's existing frontend visual review."""
    declaration = data["tools"][owner].get("metadata", {}).get("result_card")
    assert declaration is not None, f"{owner} has no declared public card"
    card = project_result_card(declaration, parameters=parameters, public_context=result.context)
    assert card is not None, f"{owner} did not project its actual declared result card"
    directory = Path(os.environ.get("SRD_LITE_STEED_CARD_CAPTURE_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{step}-{owner}.json"
    destination.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    print(f"Actual synthetic {owner} card: {destination}")
    return card


def _caster(spell, **choices):
    return {
        "spell": spell,
        "caster": "hero",
        "caster_state": "hero",
        "verbal_component_available": True,
        "somatic_component_available": True,
        "material_component_available": True,
        **choices,
    }


def _fall_event(bridge):
    lasting = bridge.get_entity("state")
    event_id = lasting["pending_srd_action_stack"][-1]
    event = lasting["pending_srd_actions"][event_id]
    assert event["kind"] == "scene_fall" and event["status"] == "open"
    assert event["stage"] == "reaction"
    assert bridge.get_entity("hero")["scene_fall"]["event_id"] == event_id
    return event_id


def test_native_paid_magic_missile_death_completes_real_eighty_foot_fall(
    paid_owners, monkeypatch, tmp_path
):
    parsed, data = paid_owners
    bridge = _paid_bridge(data)
    monkeypatch.setattr(dice, "_rng", Random(1082))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    bridge.write_field("steed", "hp", 1)
    tick = bridge.get_entity("state")["lasting_tick"]
    result = _run(parsed, bridge, FACTS, _facts(established_rider_fall_distance=80))
    assert result.success, result.error
    damage_calls = []
    damage_parameters = _caster(
        "magic_missile",
        target="steed",
        slot_resource="spell_slot_1",
        target_distance=5,
        caster_can_see_target=True,
        cover="none",
    )
    damage = _run(
        parsed,
        bridge,
        "cast_spell_outside_combat",
        damage_parameters,
        damage_calls,
    )
    assert damage.success, damage.error
    assert "apply_srd_damage" in damage_calls and RETIRE in damage_calls
    assert bridge.get_entity("state")["lasting_tick"] == tick
    assert bridge.get_entity("state")["created_actor_records"]["steed"]["active"] is False
    assert bridge.get_entity("steed")["is_alive"] is False
    assert bridge.get_entity("hero")["resources"]["spell_slot_1"] == 2
    damage_card = _project_and_capture(
        data, "cast_spell_outside_combat", damage, damage_parameters, tmp_path, "death-cast"
    )
    assert damage_card["title"] == "Magic Missile"
    event_id = _fall_event(bridge)
    calls = []
    completion_parameters = {"pending_action_id": event_id}
    completed = _run(
        parsed,
        bridge,
        "complete_srd_pending_action",
        completion_parameters,
        calls,
    )
    assert completed.success, completed.error
    hero, lasting = bridge.get_entity("hero"), bridge.get_entity("state")
    assert calls.count("resolve_srd_fall") == 1
    assert calls.count("apply_srd_damage") == 1
    assert "consume_srd_reaction" not in calls and "install_srd_feather_fall" not in calls
    assert hero["resources"]["spell_slot_1"] == 2 and hero["reaction_available"] is True
    assert lasting["lasting_tick"] == tick
    assert lasting["pending_srd_action_stack"] == []
    assert hero["scene_fall"]["status"] == "landed"
    assert hero["scene_fall"]["remaining_distance"] == 0
    assert hero["scene_fall"]["ended_reason"] == "ordinary_fall"
    card = _project_and_capture(
        data,
        "complete_srd_pending_action",
        completed,
        completion_parameters,
        tmp_path,
        "death-eighty-foot-fall",
    )
    assert card["title"] == "Falling creatures"
    (equation,) = [row["equation"] for row in card["details"] if "equation" in row]
    assert equation["die_sides"] == 6 and len(equation["rolls"]) == 8
    assert equation["total"] == equation["roll"] == sum(equation["rolls"])
    assert hero["hp"] == max(0, 40 - equation["total"])
    assert "prone" in hero["conditions"]
    assert any(
        row.get("label") == "Current location"
        and _facts()["established_rider_landing"] in row["value"]
        and "remaining height 0 feet" in row["value"]
        for row in card["details"]
    )
    assert (
        bridge.get_entity("state")[RECORDS]["steed"]["rider_placement"]
        == _facts()["established_rider_landing"]
    )
    assert bridge.get_entity("state")["created_actor_records"]["steed"]["active"] is False
    assert bridge.get_entity("state").get("spatial_scene") is None


def test_native_action_find_steed_replacement_keeps_immediate_loss_facts_fresh(
    paid_owners, tmp_path
):
    parsed, data = paid_owners
    assert data["definitions"]["spells"]["find_steed"]["casting_time"] == "action"
    assert data["definitions"]["spell_casting"]["find_steed"]["casting_time"] == "Action"
    bridge = _paid_bridge(data)
    tick = bridge.get_entity("state")["lasting_tick"]
    result = _run(parsed, bridge, FACTS, _facts(established_rider_fall_distance=5))
    assert result.success, result.error
    calls = []
    replacement_parameters = _caster(
        "find_steed",
        slot_resource="spell_slot_2",
        created_individual_id="actual-successor-steed",
        return_created_individual=False,
        created_creature_type="celestial",
        created_actor_placement="beside the supported courtyard stones",
        created_actor_distance=5,
        created_actor_space_unoccupied=True,
        created_actor_clear_path=True,
    )
    result = _run(
        parsed,
        bridge,
        "cast_spell_outside_combat",
        replacement_parameters,
        calls,
    )
    assert result.success, result.error
    assert RETIRE in calls and "spend_spell_casting_budget" in calls
    assert not any(
        name in calls for name in ("advance_srd_lasting_time", "advance_srd_lasting_clock")
    )
    hero, lasting = bridge.get_entity("hero"), bridge.get_entity("state")
    assert lasting["lasting_tick"] == tick
    assert hero["resources"]["spell_slot_2"] == 2
    assert hero["created_steed_id"] == "actual-successor-steed"
    assert lasting["created_actor_records"]["steed"]["active"] is False
    assert lasting["created_actor_records"]["steed"]["disappearance"] == "replaced"
    new_source = lasting["created_actor_records"]["actual-successor-steed"]
    assert new_source["caster"] == "hero" and new_source["active"] is True
    assert new_source["spell"] == "find_steed" and new_source["spell_level"] == 2
    replacement_card = _project_and_capture(
        data,
        "cast_spell_outside_combat",
        result,
        replacement_parameters,
        tmp_path,
        "replacement-cast",
    )
    assert replacement_card["title"] == "Find Steed"
    event_id = _fall_event(bridge)
    completion_parameters = {"pending_action_id": event_id}
    completed = _run(parsed, bridge, "complete_srd_pending_action", completion_parameters)
    assert completed.success, completed.error
    assert bridge.get_entity("hero")["hp"] == 40
    assert bridge.get_entity("hero")["scene_fall"]["ended_reason"] == "ordinary_fall"
    assert bridge.get_entity("state").get("spatial_scene") is None
    card = _project_and_capture(
        data,
        "complete_srd_pending_action",
        completed,
        completion_parameters,
        tmp_path,
        "replacement-five-foot-fall",
    )
    assert card["title"] == "Falling creatures"
    assert not any("equation" in row for row in card["summary"] + card["details"])
    assert any(
        row.get("label") == "Current location"
        and _facts()["established_rider_landing"] in row["value"]
        and "remaining height 0 feet" in row["value"]
        for row in card["details"]
    )
