"""Admitted Longstrider and Enhance Leap casts retain their real costs and effects."""

from copy import deepcopy

from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import Case, VARIANT_ID


def test_free_exploration_longstrider_level_three_keeps_targets_cost_card_and_retry(tmp_path):
    """A free exploration cast retains its selected level and all three recipients."""
    case = Case(
        tmp_path / "longstrider-three-targets.db",
        "longstrider",
        positions={
            "hero": [0, 0, 0],
            "guard": [5, 0, 0],
            "other": [-5, 0, 0],
            "extra": [0, 5, 0],
        },
    )
    try:
        before = case.snapshot()
        entities_before = case.entities()
        resources_before = deepcopy(entities_before["hero"]["resources"])
        combat_before = before.state[VARIANT_ID + ".combat"]
        assert combat_before["encounter_active"] is False
        assert combat_before["turn_active"] is False
        assert {entities_before[target]["speed"] for target in ("guard", "other", "extra")} == {30}

        receipt, command = case.cast(
            casting_mode="ordinary",
            slot_resource="spell_slot_3",
            targets=["guard", "other", "extra"],
        )
        saved = case.snapshot()
        entities_after = case.entities()
        assert {entities_after[target]["speed"] for target in ("guard", "other", "extra")} == {40}
        assert entities_after["hero"]["speed"] == entities_before["hero"]["speed"]
        assert entities_after["hero"]["resources"] == resources_before
        assert saved.state[VARIANT_ID + ".state"]["lasting_tick"] == before.state[VARIANT_ID + ".state"]["lasting_tick"]
        assert saved.package_lock == before.package_lock

        card = receipt.result["result_card"]
        assert card["title"] == "Longstrider"
        summary = {row["label"]: str(row["value"]) for row in card["summary"]}
        assert summary["Spell slots spent"] == "0"
        assert any("level" in label.lower() and value == "3" for label, value in summary.items())
        for target in ("guard", "other", "extra"):
            details = [str(row["value"]) for row in card["details"] if row["label"] == target]
            assert details, f"The saved card is missing the actual recipient {target}."
            assert any("10" in detail and "long rest" in detail.lower() for detail in details)

        assert case.execute(command) == receipt
        assert case.snapshot() == saved
        case.reopen()
        assert case.snapshot() == saved
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
        assert {case.entities()[target]["speed"] for target in ("guard", "other", "extra")} == {40}
    finally:
        archive_closed_case(case)


def test_enhance_leap_triples_ordinary_jump_reach_and_spends_actual_distance(tmp_path):
    """A standing jump reaches three times its ordinary limit and pays each foot."""
    case = Case(tmp_path / "enhance-leap-jump.db", "jump")
    try:
        before = case.snapshot()
        resources_before = deepcopy(case.entities()["hero"]["resources"])
        assert before.participants[0].data["abilities"]["strength"] == 16

        cast_receipt, cast_command = case.cast(
            target="hero",
            casting_mode="ordinary",
            slot_resource="spell_slot_1",
        )
        cast_saved = case.snapshot()
        assert case.entities()["hero"]["resources"] == resources_before
        cast_card = cast_receipt.result["result_card"]
        assert cast_card["title"] == "Enhance Leap"
        summary = {row["label"]: str(row["value"]) for row in cast_card["summary"]}
        assert summary["Spell slots spent"] == "0"
        details = "\n".join(str(row["value"]) for row in cast_card["details"]).lower()
        assert any(word in details for word in (
            "tripled", "triple", "3x", "x3", "3×", "×3", "three times", "factor 3",
        ))
        assert "10 turns" in details
        assert case.execute(cast_command) == cast_receipt
        assert case.snapshot() == cast_saved
        case.reopen()
        assert case.execute(cast_command) == cast_receipt
        assert case.snapshot() == cast_saved

        case.start()
        combat_before = case.snapshot().state[VARIANT_ID + ".combat"]
        assert combat_before["movement_remaining"] == 30
        movement = {
            "movement_kind": "long_jump",
            "running_start": False,
            "difficult_terrain": False,
        }
        # Strength 16 permits 8 feet standing; tripling permits exactly 24.
        case.reject("spend_combat_movement", movement | {"distance": 25}, match="jump")
        receipt, command = case.act("spend_combat_movement", **movement, distance=24)
        saved = case.snapshot()
        combat = saved.state[VARIANT_ID + ".combat"]
        assert combat["movement_spent"] == 24
        assert combat["movement_remaining"] == 6
        assert combat["action_available"] is True
        assert case.entities()["hero"]["resources"] == resources_before

        card = receipt.result["result_card"]
        assert card["title"] == "Movement"
        summary = {row["label"]: str(row["value"]) for row in card["summary"]}
        assert summary["Moved (feet)"] == "24"
        assert summary["Movement spent (feet)"] == "24"
        assert summary["Movement remaining (feet)"] == "6"
        assert any(row["label"] == "Jump limit (feet)" and float(row["value"]) == 24
                   for row in card["details"])
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
        case.reopen()
        assert case.snapshot() == saved
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
    finally:
        archive_closed_case(case)


def test_short_rest_wizard_replacement_ends_longstrider_despite_other_prepared_grant(tmp_path):
    """Unpreparing the casting source ends its buff even when another grant keeps the spell."""
    grants = [
        {"source": "wizard", "ability": "intelligence", "spells": ["longstrider"]},
        {"source": "magic_initiate_wizard", "ability": "intelligence", "spells": ["longstrider"]},
    ]
    case = Case(
        tmp_path / "longstrider-source-preparation.db",
        "longstrider",
        participant_changes={
            "spellcasting_grants": grants,
            "wizard_spellbook": ["longstrider", "false_life"],
        },
    )
    try:
        before = case.snapshot()
        resources_before = deepcopy(case.entities()["hero"]["resources"])
        case.cast(
            targets=["guard"],
            casting_mode="ordinary",
            slot_resource="spell_slot_1",
            spell_grant="wizard",
        )
        assert case.entities()["guard"]["speed"] == 40
        records = [
            effect for effect in case.lasting()["turn_effects"]
            if effect["spell"] == "longstrider" and effect["target"] == "guard"
        ]
        assert len(records) == 1
        record = records[0]
        assert record["caster"] == "hero"
        assert record["preparation_source"] == "wizard"
        assert record["expires_tick"] is None
        assert isinstance(record["cast_id"], str) and record["cast_id"].strip()
        assert record["source_caster_id"] == "hero"
        case.act("begin_srd_rest", target_state="hero", kind="short", activity="light")
        case.act("advance_srd_lasting_time", elapsed_rounds=600)
        assert case.entities()["guard"]["speed"] == 40
        case.act("complete_srd_rest", target_state="hero")
        assert case.entities()["guard"]["speed"] == 40
        assert case.snapshot().participants[0].data["spellcasting_grants"] == grants
        assert case.entities()["hero"]["resources"] == resources_before

        receipt, command = case.act(
            "prepare_srd_spells",
            caster="hero",
            caster_state="hero",
            source="wizard",
            spells=["false_life"],
            spellbook_available=True,
        )
        saved = case.snapshot()
        updated_grants = deepcopy(grants)
        updated_grants[0]["spells"] = ["false_life"]
        assert saved.participants[0].data["spellcasting_grants"] == updated_grants
        assert set(case.entities()["hero"]["prepared_spells"]) == {"false_life", "longstrider"}
        assert case.entities()["guard"]["speed"] == 30
        assert case.entities()["hero"]["resources"] == resources_before
        assert saved.package_lock == before.package_lock
        card = receipt.result["result_card"]
        assert card["title"] == "Spells prepared"
        summary = {row["label"]: str(row["value"]) for row in card["summary"]}
        assert summary["Source"] == "Wizard"
        assert summary["Spells replaced"] == "1"
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
        case.reopen()
        assert case.snapshot() == saved
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
    finally:
        archive_closed_case(case)


def test_paid_longstrider_rejects_invalid_begin_and_completes_once_after_reopen(tmp_path):
    """A validated paid cast reserves one action and spends one slot only when completed."""
    case = Case(tmp_path / "longstrider-paid-pending.db", "longstrider")
    try:
        case.start()
        before = case.snapshot()
        resources_before = deepcopy(case.entities()["hero"]["resources"])
        parameters = case.parameters | {
            "casting_mode": "ordinary",
            "slot_resource": "spell_slot_3",
            "targets": ["guard", "other", "hero"],
        }
        case.reject(
            "begin_srd_spell_action",
            parameters | {"casting_mode": "ritual", "slot_resource": "ritual", "targets": ["guard"]},
        )
        case.reject(
            "begin_srd_spell_action",
            parameters | {"slot_resource": "spell_slot_1", "targets": ["guard", "other"]},
        )
        # Extra is actually more than 20 feet away; caller touch/range flags cannot admit it.
        case.reject(
            "begin_srd_spell_action",
            parameters | {
                "targets": ["guard", "other", "extra"],
                "targets_max_distance": 0,
                "caster_can_touch_all_targets": True,
            },
        )
        assert case.snapshot() == before

        begin_receipt, begin_command = case.act("begin_srd_spell_action", **parameters)
        begun = case.snapshot()
        assert begun.state[VARIANT_ID + ".combat"]["action_available"] is False
        assert case.entities()["hero"]["bonus_action_available"] is True
        assert case.entities()["hero"]["resources"] == resources_before
        assert {case.entities()[target]["speed"] for target in ("guard", "other", "hero")} == {30}
        pending = case.lasting()["pending_srd_actions"]
        assert len(pending) == 1
        pending_id = next(iter(pending))
        assert pending[pending_id]["kind"] == "spell_cast"
        assert case.lasting()["pending_srd_action_stack"] == [pending_id]
        assert case.execute(begin_command) == begin_receipt
        assert case.snapshot() == begun
        case.reopen()
        assert case.snapshot() == begun
        assert case.execute(begin_command) == begin_receipt
        assert case.snapshot() == begun

        receipt, command = case.act("complete_srd_pending_action", pending_action_id=pending_id)
        saved = case.snapshot()
        resources_after = dict(resources_before)
        resources_after["spell_slot_3"] -= 1
        assert case.entities()["hero"]["resources"] == resources_after
        assert {case.entities()[target]["speed"] for target in ("guard", "other", "hero")} == {40}
        assert saved.state[VARIANT_ID + ".combat"]["action_available"] is False
        assert case.entities()["hero"]["bonus_action_available"] is True
        assert case.lasting()["pending_srd_actions"] == {}
        assert case.lasting()["pending_srd_action_stack"] == []
        assert saved.package_lock == before.package_lock
        card = receipt.result["result_card"]
        assert card["title"] == "Longstrider"
        summary = {row["label"]: str(row["value"]) for row in card["summary"]}
        assert summary["Spell slots spent"] == "1"
        assert any("level" in label.lower() and value == "3" for label, value in summary.items())
        for target in ("guard", "other", "hero"):
            name = case.entities()[target]["name"]
            details = [str(row["value"]) for row in card["details"] if row["label"] == name]
            assert any("10" in detail and "long rest" in detail.lower() for detail in details)
        assert case.execute(begin_command) == begin_receipt
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
        case.reopen()
        assert case.snapshot() == saved
        assert case.execute(begin_command) == begin_receipt
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
    finally:
        archive_closed_case(case)


def test_self_longstrider_expires_only_when_an_earned_long_rest_completes(tmp_path):
    """Eight elapsed hours preserve the buff until the actual resting owner completes."""
    case = Case(
        tmp_path / "longstrider-self-long-rest.db",
        "longstrider",
        entity_changes={
            "hero": {
                "resources": {"spell_slot_1": 2},
                "resource_maxima": {"spell_slot_1": 4},
            },
        },
    )
    try:
        before = case.snapshot()
        case.cast(target="hero", targets=["hero"], casting_mode="ordinary", slot_resource="spell_slot_1")
        assert case.entities()["hero"]["speed"] == 40
        assert case.entities()["hero"]["resources"] == {"spell_slot_1": 2}
        case.reject("complete_srd_rest", {"target_state": "hero"})
        case.act("begin_srd_rest", target_state="hero", kind="long", activity="sleep")
        case.reject("complete_srd_rest", {"target_state": "hero"})
        case.act("advance_srd_lasting_time", elapsed_rounds=4800)
        elapsed = case.snapshot()
        assert case.entities()["hero"]["speed"] == 40
        assert case.entities()["hero"]["resources"] == {"spell_slot_1": 2}
        assert case.entities()["hero"]["rest_phase"] == "active"
        assert case.lasting()["lasting_tick"] == before.state[VARIANT_ID + ".state"]["lasting_tick"] + 9600

        receipt, command = case.act("complete_srd_rest", target_state="hero")
        saved = case.snapshot()
        hero = case.entities()["hero"]
        assert hero["speed"] == 30
        assert hero["rest_kind"] == "long"
        assert hero["rest_phase"] == "complete"
        assert hero["last_long_rest_tick"] == elapsed.state[VARIANT_ID + ".state"]["lasting_tick"]
        assert hero["resources"] == {"spell_slot_1": 4}
        assert hero["resource_maxima"] == {"spell_slot_1": 4}
        assert not any(effect["spell"] == "longstrider" and effect["target"] == "hero"
                       for effect in case.lasting()["turn_effects"])
        assert saved.package_lock == before.package_lock
        card = receipt.result["result_card"]
        assert card["title"] == "Rest completed"
        summary = {row["label"]: str(row["value"]) for row in card["summary"]}
        assert summary["Rest"] == "long"
        assert float(summary["Minutes rested"]) == 480
        assert any(row["label"] == "spell_slot_1" and float(row["value"]) == 2
                   for row in card["details"])
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
        case.reopen()
        assert case.snapshot() == saved
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
    finally:
        archive_closed_case(case)


def test_actual_fall_death_retires_only_the_dead_longstrider_recipient(tmp_path):
    """A real lethal fall ends one recipient's buff while preserving the other source."""
    case = Case(
        tmp_path / "longstrider-recipient-death.db",
        "longstrider",
        positions={
            "hero": [0, 0, 20],
            "guard": [5, 0, 20],
            "other": [-5, 0, 20],
            "extra": [20, 10, 0],
        },
        entity_changes={
            "guard": {
                "creature_kind": "monster",
                "hp": 2,
                "max_hp": 2,
                "temp_hp": 0,
                "is_alive": True,
            },
        },
    )
    try:
        before = case.snapshot()
        resources_before = dict(case.entities()["hero"]["resources"])
        case.cast(
            targets=["guard", "other"],
            casting_mode="ordinary",
            slot_resource="spell_slot_2",
            spell_grant="wizard",
        )
        assert {case.entities()[target]["speed"] for target in ("guard", "other")} == {40}
        assert case.entities()["hero"]["resources"] == resources_before
        records_before = {
            effect["target"]: effect for effect in case.lasting()["turn_effects"]
            if effect["spell"] == "longstrider"
        }
        assert set(records_before) == {"guard", "other"}
        other_cast_id = records_before["other"]["cast_id"]
        assert isinstance(other_cast_id, str) and other_cast_id.strip()
        assert records_before["other"]["source_caster_id"] == "hero"
        clock_before = case.lasting()["lasting_tick"]

        receipt, command = case.act(
            "resolve_srd_fall",
            target_state="guard",
            distance=20,
            landing="ground",
            reaction="none",
        )
        saved = case.snapshot()
        guard = case.entities()["guard"]
        assert guard["hp"] == 0
        assert guard["is_alive"] is False
        assert guard["death_tick"] == clock_before == case.lasting()["lasting_tick"]
        assert guard["speed"] == 30
        assert case.entities()["other"]["speed"] == 40
        assert case.entities()["other"]["hp"] == 1000
        records_after = [
            effect for effect in case.lasting()["turn_effects"]
            if effect["spell"] == "longstrider"
        ]
        assert len(records_after) == 1
        assert records_after[0]["target"] == "other"
        assert records_after[0]["cast_id"] == other_cast_id
        assert records_after[0]["source_caster_id"] == "hero"
        assert case.entities()["hero"]["resources"] == resources_before
        assert saved.package_lock == before.package_lock
        card = receipt.result["result_card"]
        assert card["title"] == "Falling"
        summary = {row["label"]: str(row["value"]) for row in card["summary"]}
        assert summary["Creature"] == "guard"
        assert float(summary["Fall (feet)"]) == 20
        assert float(summary["Damage taken"]) >= 2
        assert float(summary["HP lost"]) == 2
        assert float(summary["HP after"]) == 0
        details = {row["label"]: row["value"] for row in card["details"]}
        assert float(details["D6 count"]) == 2
        assert float(details["Rolled damage"]) >= 2
        assert details["Reaction"] == "None"
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
        case.reopen()
        assert case.snapshot() == saved
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
    finally:
        archive_closed_case(case)


def test_exact_longstrider_dismissal_preserves_self_and_guards_unresolved_recipient_rest(tmp_path):
    """Exact dismissal clears its recipient while unsupported separate rest remains atomic."""
    case = Case(tmp_path / "longstrider-dismissal-recipient-rest.db", "longstrider")
    try:
        before = case.snapshot()
        resources_before = dict(case.entities()["hero"]["resources"])
        cast_tick = case.lasting()["lasting_tick"]
        case.cast(target="guard", targets=["guard"], casting_mode="ordinary", slot_resource="spell_slot_1")
        case.cast(target="hero", targets=["hero"], casting_mode="ordinary", slot_resource="spell_slot_1")
        assert case.lasting()["lasting_tick"] == cast_tick
        records = {
            effect["target"]: effect for effect in case.lasting()["turn_effects"]
            if effect["spell"] == "longstrider"
        }
        assert set(records) == {"guard", "hero"}
        guard_cast_id = records["guard"]["cast_id"]
        hero_cast_id = records["hero"]["cast_id"]
        assert isinstance(guard_cast_id, str) and guard_cast_id.strip()
        assert isinstance(hero_cast_id, str) and hero_cast_id.strip()
        assert guard_cast_id != hero_cast_id
        assert records["guard"]["source_caster_id"] == records["hero"]["source_caster_id"] == "hero"
        assert {case.entities()[target]["speed"] for target in ("guard", "hero")} == {40}

        case.act("begin_srd_rest", target_state="guard", kind="long", activity="sleep")
        case.act("advance_srd_lasting_time", elapsed_rounds=4800)
        blocked = case.snapshot()
        assert case.entities()["guard"]["rest_phase"] == "active"
        assert "unconscious" not in case.entities()["hero"]["conditions"]
        case.reject(
            "complete_srd_rest",
            {"target_state": "guard"},
            match="(?i)(cross.actor|independent|separate|unsupported|pending)",
        )
        assert case.snapshot() == blocked
        assert {case.entities()[target]["speed"] for target in ("guard", "hero")} == {40}
        assert case.entities()["hero"]["resources"] == resources_before

        case.reject("dismiss_srd_spell", {"caster_state": "hero", "cast_id": ""})
        case.reject(
            "dismiss_srd_spell",
            {"caster_state": "hero", "cast_id": guard_cast_id + "-unknown"},
        )
        case.reject("dismiss_srd_spell", {"caster_state": "other", "cast_id": guard_cast_id})
        assert case.snapshot() == blocked

        receipt, command = case.act("dismiss_srd_spell", caster_state="hero", cast_id=guard_cast_id)
        assert case.entities()["guard"]["speed"] == 30
        assert case.entities()["hero"]["speed"] == 40
        remaining = [effect for effect in case.lasting()["turn_effects"] if effect["spell"] == "longstrider"]
        assert len(remaining) == 1
        assert remaining[0]["target"] == "hero" and remaining[0]["cast_id"] == hero_cast_id
        assert case.entities()["hero"]["resources"] == resources_before
        card = receipt.result["result_card"]
        assert card["title"] == "Spell dismissed"
        summary = {row["label"]: str(row["value"]) for row in card["summary"]}
        assert summary["Cast"] == guard_cast_id
        assert float(summary["Effects ended"]) == 1

        rest_receipt, _ = case.act("complete_srd_rest", target_state="guard")
        rested = case.snapshot()
        assert case.entities()["guard"]["rest_phase"] == "complete"
        assert case.entities()["guard"]["last_long_rest_tick"] == case.lasting()["lasting_tick"]
        assert case.entities()["guard"]["speed"] == 30
        assert case.entities()["hero"]["speed"] == 40
        assert case.entities()["hero"]["resources"] == resources_before
        rest_card = rest_receipt.result["result_card"]
        assert rest_card["title"] == "Rest completed"
        rest_summary = {row["label"]: str(row["value"]) for row in rest_card["summary"]}
        assert rest_summary["Rest"] == "long"
        assert float(rest_summary["Minutes rested"]) == 480
        assert rested.package_lock == before.package_lock
        assert case.execute(command) == receipt
        assert case.snapshot() == rested
        case.reopen()
        assert case.snapshot() == rested
        assert case.execute(command) == receipt
        assert case.snapshot() == rested

        case.cast(target="guard", targets=["guard"], casting_mode="ordinary", slot_resource="spell_slot_1")
        fresh = case.snapshot()
        fresh_records = {
            effect["target"]: effect for effect in case.lasting()["turn_effects"]
            if effect["spell"] == "longstrider"
        }
        assert set(fresh_records) == {"guard", "hero"}
        assert fresh_records["guard"]["cast_id"] != guard_cast_id
        assert fresh_records["hero"]["cast_id"] == hero_cast_id
        assert {case.entities()[target]["speed"] for target in ("guard", "hero")} == {40}
        assert case.entities()["hero"]["resources"] == resources_before
        assert case.execute(command) == receipt
        assert case.snapshot() == fresh
        case.reopen()
        assert case.snapshot() == fresh
        assert case.execute(command) == receipt
        assert case.snapshot() == fresh
    finally:
        archive_closed_case(case)


def test_free_longstrider_shape_uses_saved_capacity_without_spending_exhausted_slots(tmp_path):
    """Free casting retains exhausted unlocked capacity and rejects absent capacity or recipients."""
    resources = {"spell_slot_1": 0, "spell_slot_3": 0, "spell_slot_4": 0}
    maxima = {"spell_slot_1": 4, "spell_slot_3": 3, "spell_slot_4": 0}
    case = Case(
        tmp_path / "longstrider-free-capacity.db",
        "longstrider",
        positions={
            "hero": [0, 0, 0],
            "guard": [5, 0, 0],
            "other": [-5, 0, 0],
            "extra": [0, 5, 0],
        },
        entity_changes={
            "hero": {"resources": resources, "resource_maxima": maxima},
            "extra": {"creature_kind": "object", "is_alive": True},
        },
    )
    try:
        before = case.snapshot()
        parameters = case.parameters | {"casting_mode": "ordinary"}
        case.reject(
            "cast_spell_outside_combat",
            parameters | {"slot_resource": "spell_slot_4", "targets": ["guard", "other", "hero"]},
            match="(?i)(capacity|unlocked)",
        )
        case.reject(
            "cast_spell_outside_combat",
            parameters | {"slot_resource": "spell_slot_3", "targets": ["guard", "guard", "hero"]},
        )
        case.reject(
            "cast_spell_outside_combat",
            parameters | {"slot_resource": "spell_slot_1", "target": "extra", "targets": ["extra"]},
            match="(?i)creature",
        )
        assert case.snapshot() == before

        receipt, command = case.cast(
            casting_mode="ordinary",
            slot_resource="spell_slot_3",
            targets=["guard", "other", "hero"],
        )
        saved = case.snapshot()
        assert {case.entities()[target]["speed"] for target in ("guard", "other", "hero")} == {40}
        assert case.entities()["extra"]["speed"] == 30
        assert case.entities()["hero"]["resources"] == resources
        assert case.entities()["hero"]["resource_maxima"] == maxima
        assert case.lasting()["lasting_tick"] == before.state[VARIANT_ID + ".state"]["lasting_tick"]
        assert saved.package_lock == before.package_lock
        card = receipt.result["result_card"]
        assert card["title"] == "Longstrider"
        summary = {row["label"]: str(row["value"]) for row in card["summary"]}
        assert summary["Spell slots spent"] == "0"
        assert any("level" in label.lower() and value == "3" for label, value in summary.items())
        for target in ("guard", "other", "hero"):
            name = case.entities()[target]["name"]
            details = [str(row["value"]) for row in card["details"] if row["label"] == name]
            assert any("10" in detail and "long rest" in detail.lower() for detail in details)
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
        case.reopen()
        assert case.snapshot() == saved
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
    finally:
        archive_closed_case(case)


def test_enhance_leap_alias_actual_touch_nonstacking_and_finite_expiry_survive_replay(tmp_path):
    """Actual eligibility and ten-round source expiry govern one nonstacking Leap condition."""
    resources = {"spell_slot_1": 0, "spell_slot_3": 0}
    maxima = {"spell_slot_1": 4, "spell_slot_3": 3}
    case = Case(
        tmp_path / "enhance-leap-eligibility-expiry.db",
        "jump",
        entity_changes={"hero": {"resources": resources, "resource_maxima": maxima}},
    )

    def leap_records():
        return [effect for effect in case.lasting()["turn_effects"] if effect["spell"] == "jump"]

    try:
        before = case.snapshot()
        initial_tick = case.lasting()["lasting_tick"]
        assert before.participants[0].data["abilities"]["strength"] == 16
        alias_parameters = case.parameters | {
            "spell": "enhance_leap",
            "casting_mode": "ritual",
            "slot_resource": "spell_slot_3",
            "target": "hero",
            "targets": ["hero"],
            "target_distance": 99,
            "targets_max_distance": 99,
            "caster_can_touch_target": False,
            "caster_can_touch_all_targets": False,
            "material_component_available": False,
            "verbal_component_available": True,
            "somatic_component_available": True,
            "spellbook_readable": True,
        }
        case.reject(
            "cast_spell_outside_combat",
            alias_parameters | {"targets": ["hero", "guard"]},
        )
        case.reject(
            "cast_spell_outside_combat",
            alias_parameters | {
                "target": "extra",
                "targets": ["extra"],
                "target_distance": 0,
                "targets_max_distance": 0,
                "caster_can_touch_target": True,
                "caster_can_touch_all_targets": True,
            },
        )
        assert case.snapshot() == before

        receipt, command = case.act("cast_spell_outside_combat", **alias_parameters)
        alias_saved = case.snapshot()
        records = leap_records()
        assert len(records) == 1
        assert records[0]["target"] == "hero"
        assert records[0]["mobility"] == "jump_multiplier" and records[0]["magnitude"] == 3
        assert records[0]["expires_tick"] == initial_tick + 20
        assert case.entities()["hero"]["resources"] == resources
        assert case.lasting()["lasting_tick"] == initial_tick
        card = receipt.result["result_card"]
        assert card["title"] == "Enhance Leap"
        summary = {row["label"]: str(row["value"]) for row in card["summary"]}
        assert summary["Spell slots spent"] == "0"
        assert any("level" in label.lower() and value == "3" for label, value in summary.items())
        detail = "\n".join(str(row["value"]) for row in card["details"]).lower()
        assert any(word in detail for word in (
            "tripled", "triple", "3x", "x3", "3×", "×3", "three times", "factor 3",
        ))
        assert "10 turns" in detail
        assert case.execute(command) == receipt
        assert case.snapshot() == alias_saved

        case.cast(target="hero", targets=["hero"], casting_mode="ordinary", slot_resource="spell_slot_1")
        records = leap_records()
        assert len(records) == 1
        assert records[0]["magnitude"] == 3
        assert records[0]["expires_tick"] == initial_tick + 20
        assert case.lasting()["lasting_tick"] == initial_tick
        first_cast_id = records[0]["cast_id"]
        assert isinstance(first_cast_id, str) and first_cast_id.strip()

        advance_receipt, advance_command = case.act("advance_srd_lasting_time", elapsed_rounds=9)
        assert case.lasting()["lasting_tick"] == initial_tick + 18
        assert len(leap_records()) == 1 and leap_records()[0]["cast_id"] == first_cast_id
        case.cast(target="hero", targets=["hero"], casting_mode="ordinary", slot_resource="spell_slot_1")
        records = leap_records()
        assert len(records) == 1 and records[0]["magnitude"] == 3
        refreshed_cast_id = records[0]["cast_id"]
        assert refreshed_cast_id != first_cast_id
        assert records[0]["expires_tick"] == initial_tick + 38
        case.act("advance_srd_lasting_time", elapsed_rounds=9)
        active = case.snapshot()
        assert case.lasting()["lasting_tick"] == initial_tick + 36
        assert len(leap_records()) == 1 and leap_records()[0]["cast_id"] == refreshed_cast_id
        assert case.execute(advance_command) == advance_receipt
        assert case.snapshot() == active

        expiry_receipt, expiry_command = case.act("advance_srd_lasting_time", elapsed_rounds=1)
        assert case.lasting()["lasting_tick"] == initial_tick + 38
        assert leap_records() == []
        case.cast(target="hero", targets=["hero"], casting_mode="ordinary", slot_resource="spell_slot_1")
        fresh = case.snapshot()
        records = leap_records()
        assert len(records) == 1 and records[0]["magnitude"] == 3
        assert records[0]["cast_id"] != refreshed_cast_id
        assert records[0]["expires_tick"] == initial_tick + 58
        assert case.execute(expiry_command) == expiry_receipt
        assert case.snapshot() == fresh
        case.reopen()
        assert case.snapshot() == fresh
        assert case.execute(expiry_command) == expiry_receipt
        assert case.snapshot() == fresh
        case.act("advance_srd_lasting_time", elapsed_rounds=10)
        assert case.lasting()["lasting_tick"] == initial_tick + 58
        assert leap_records() == []

        case.start()
        movement = {"movement_kind": "long_jump", "running_start": False, "difficult_terrain": False}
        case.reject("spend_combat_movement", movement | {"distance": 9}, match="jump")
        movement_receipt, movement_command = case.act("spend_combat_movement", **movement, distance=8)
        saved = case.snapshot()
        combat = saved.state[VARIANT_ID + ".combat"]
        assert combat["movement_spent"] == 8
        assert combat["movement_remaining"] == 22
        assert combat["action_available"] is True
        assert case.entities()["hero"]["resources"] == resources
        assert case.entities()["hero"]["resource_maxima"] == maxima
        assert saved.package_lock == before.package_lock
        movement_card = movement_receipt.result["result_card"]
        assert movement_card["title"] == "Movement"
        movement_summary = {row["label"]: str(row["value"]) for row in movement_card["summary"]}
        assert float(movement_summary["Movement spent (feet)"]) == 8
        assert float(movement_summary["Movement remaining (feet)"]) == 22
        assert any(row["label"] == "Jump limit (feet)" and float(row["value"]) == 8
                   for row in movement_card["details"])
        assert case.execute(movement_command) == movement_receipt
        assert case.snapshot() == saved
        case.reopen()
        assert case.snapshot() == saved
        assert case.execute(movement_command) == movement_receipt
        assert case.snapshot() == saved
    finally:
        archive_closed_case(case)
