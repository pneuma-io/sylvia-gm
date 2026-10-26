"""SRD preparation admits creatures through MCP and preserves identities on repeat calls."""

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from tests import test_srd_ability_checks as checks

PACKAGE_ID = checks.PACKAGE_ID
case = checks.case


def _prepare(owner, *, action_id=None, expected_revision=None, **changes):
    before = owner.snapshot("checks")
    identity = action_id or f"prepare-{before.revision}"
    return StatelessMcpMechanics(owner).execute_action(ActionCommand(
        campaign_id="checks", action_id=identity, idempotency_key=identity,
        owner_id="checks-owner", writer=WriterRole.DM,
        expected_revision=before.revision if expected_revision is None else expected_revision,
        turn_id="checks-turn", package_id=PACKAGE_ID, tool_name="prepare_srd_creature",
        parameters={
            "creature_id": "dera", "name": "Dera Flint",
            "source": "The Sparring Debt, prepared Dera statistics.",
            "armor_class": 13, "hit_points": 10, "speed": 30,
            "initiative_modifier": 1, "size_rank": 2,
            **changes,
        },
    ))


def test_preparation_keeps_declared_stats_and_rejects_duplicate_and_player(case):
    receipt = _prepare(case, strength_save=2, dexterity_save=1,
                       damage_resistances=["cold"],
                       skill_checks=[{"skill": "athletics", "modifier": 2}])
    assert receipt.status is ActionStatus.COMMITTED, receipt.result
    assert receipt.result.get("activity_label") == "Prepare creature"
    assert receipt.result.get("result_card") is None
    before = case.snapshot("checks")
    entities = before.state[f"{PACKAGE_ID}.entities"]
    assert entities["dera"]["hp"] == entities["dera"]["max_hp"] == 10
    assert entities["dera"]["saving_throw_modifiers"] == {"strength": 2, "dexterity": 1}
    assert entities["dera"]["skill_check_modifiers"] == {"athletics": 2}
    assert entities["dera"]["reaction_available"] is True
    assert entities["dera"]["damage_resistances"] == ["cold"]
    assert entities["dera"]["name"] == "Dera Flint"
    assert entities["dera"]["size_rank"] == 2
    assert entities["dera"]["source"] == "The Sparring Debt, prepared Dera statistics."
    for identity in ("dera", "hero"):
        with pytest.raises(CommandRejected, match="already exists"):
            _prepare(case, creature_id=identity, hit_points=999)
        assert case.snapshot("checks").state == before.state
        assert case.snapshot("checks").participants == before.participants


@pytest.mark.parametrize("change", [
    {"hit_points": 0}, {"speed": -1}, {"armor_class": 0},
    {"source": "x" * 1001}, {"name": ""}, {"damage_resistances": ["invented"]},
    {"size_rank": -1}, {"size_rank": 6},
    {"skill_checks": [{"skill": "athletics", "modifier": 31}]},
    {"skill_checks": [{"skill": "athletics", "modifier": 2},
                      {"skill": "athletics", "modifier": 3}]},
])
def test_invalid_preparation_does_not_create_any_entity(case, change):
    before = case.snapshot("checks")
    with pytest.raises(CommandRejected):
        _prepare(case, **change)
    assert case.snapshot("checks").state == before.state


def test_exact_preparation_retry_returns_original_receipt_without_new_mutation(case):
    revision = case.snapshot("checks").revision
    receipt = _prepare(case, action_id="prepare-retry", expected_revision=revision)
    saved = case.snapshot("checks")
    retried = _prepare(case, action_id="prepare-retry", expected_revision=revision)
    assert retried == receipt
    assert case.snapshot("checks") == saved


@pytest.mark.parametrize("facts", [{}, {"proficiency_bonus": 3, "challenge_rating_eighths": 16}])
def test_preparation_preserves_known_numeric_facts_and_leaves_unknown_facts_null(case, facts):
    receipt = _prepare(
        case, spells=[{"spell": "fire_bolt", "uses_per_day": 0}],
        spellcasting_ability="intelligence", spellcasting_modifier=2,
        spell_attack_bonus=5, spell_save_dc=13, constitution_save=1, **facts,
    )
    assert receipt.status is ActionStatus.COMMITTED, receipt.result
    creature = case.snapshot("checks").state[f"{PACKAGE_ID}.entities"]["dera"]
    for field in ("proficiency_bonus", "challenge_rating_eighths"):
        assert creature[field] == facts.get(field)
    assert creature["spellcasting_profile"]["proficiency_bonus"] == facts.get("proficiency_bonus")


def test_preparation_retains_multiattack_and_rejects_invalid_component_lists(case):
    routine = [{"attack": "Claw", "count": 2}, {"attack": "Bite", "count": 1}]
    assert _prepare(case, multiattack=routine).status is ActionStatus.COMMITTED
    assert case.snapshot("checks").state[f"{PACKAGE_ID}.entities"]["dera"]["multiattack"] == routine
    before = case.snapshot("checks")
    for bad in ([{"attack": "Claw", "count": 0}], [{"attack": "x" * 121, "count": 1}],
                [{"attack": "Claw", "count": 1}, {"attack": "Claw", "count": 2}],
                [{"attack": n, "count": 10} for n in ["Bite", "Claw", "Tail"]]):
        with pytest.raises(CommandRejected):
            _prepare(case, creature_id="other", multiattack=bad)
        assert case.snapshot("checks").state == before.state


@pytest.mark.parametrize("case", [True], indirect=True)
def test_preparation_cannot_add_or_reset_entities_during_combat(case):
    before = case.snapshot("checks")
    with pytest.raises(CommandRejected):
        _prepare(case)
    assert case.snapshot("checks").state == before.state
    assert case.snapshot("checks").participants == before.participants


@pytest.mark.parametrize("case", [True], indirect=True)
def test_agreed_end_closes_active_turn_preserves_actors_and_allows_next_opponent(case):
    """The declared ending and its exact retry cannot restore or duplicate saved state."""
    before = case.snapshot("checks")
    command = ActionCommand(
        campaign_id="checks", action_id="end-bout", idempotency_key="end-bout",
        owner_id="checks-owner", writer=WriterRole.DM, expected_revision=before.revision,
        turn_id="checks-turn", package_id=PACKAGE_ID, tool_name="end_combat_encounter",
        parameters={"end_reason": "fighting_stopped"},
    )
    receipt = StatelessMcpMechanics(case).execute_action(command)
    assert receipt.status is ActionStatus.COMMITTED
    saved = case.snapshot("checks")
    expected_entities = {
        actor_id: {
            **actor,
            "dodging": False, "spell_slot_cast_this_turn": False,
            "light_extra_attack_available": False, "light_extra_attack_used": False,
            "light_attack_source_weapons": [], "light_attack_source_weapon": None,
            "bonus_action_available": True, "reaction_available": True,
            "fighting_actor_ids": [],
            "flurry_attacks_remaining": 0, "jump_distance_multiplier": 1,
        }
        for actor_id, actor in before.state[f"{PACKAGE_ID}.entities"].items()
    }
    assert saved.state[f"{PACKAGE_ID}.entities"] == expected_entities
    combat = saved.state[f"{PACKAGE_ID}.combat"]
    assert combat["encounter_active"] is False and combat["turn_active"] is False
    assert combat["action_available"] is False and combat["phase"] == "inactive"
    # Rediscovery correctly removes End once fighting stops; authoritative exact retry
    # still returns its original receipt without requiring the operation to be offered again.
    with pytest.raises(CommandRejected, match="unavailable"):
        StatelessMcpMechanics(case).execute_action(command)
    assert case.execute_action(command) == receipt
    assert case.snapshot("checks") == saved
    assert _prepare(case).status is ActionStatus.COMMITTED
