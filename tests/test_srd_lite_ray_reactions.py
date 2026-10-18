"""UNEXECUTED draft: actual admitted ray windows, costs, dice and saved campaigns.

Main must first compose an archive containing the ray cursor and admitted Shield
adapter. No test manufactures a pending event or substitutes an attack result.
"""

from copy import deepcopy
from dataclasses import replace
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole, owned_payload, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected
from sylvia_gm.runtime import _effect_executor
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import Case, VARIANT_ID


CURSOR = "lite_scorching_ray_cursor"
PUBLIC = {"begin_srd_spell_action", "complete_srd_pending_action", "cast_srd_reaction_spell"}


def _public(case):
    original = case.execute

    def execute(command):
        if command.tool_name in PUBLIC:
            return StatelessMcpMechanics(case.owner).execute_action(command)
        return original(command)

    case.execute = execute
    return case


class RayShieldCase(Case):
    """Admit a second Wizard with actual Shield source and hostile relationship facts."""

    def __init__(self, path):
        self.campaign_id = "lite"
        super().__init__(path, "scorching_ray", positions={
            "hero": [0, 0, 0], "guard": [-10, -10, 0],
            "other": [-10, 10, 0], "extra": [20, 10, 0],
        })
        self.parameters["targets_max_distance"] = 30
        original = self.snapshot()
        state = thaw_json_value(original.state)
        caster = original.participants[0]
        data = thaw_json_value(caster.data)
        data.update(name="Ray defender", wizard_spellbook=["shield"],
                    spellcasting_grants=[{"source": "wizard", "ability": "intelligence",
                                         "spells": ["shield"]}])
        defender = replace(caster, participant_id="defender", data=owned_payload(data))
        entities = state[VARIANT_ID + ".entities"]
        entities["hero"].setdefault("attitudes", {})["defender"] = "hostile"
        entities["defender"] = deepcopy(entities["hero"])
        entities["defender"].update(name="Ray defender", prepared_spells=["shield"],
                                    armor_class=16, hp=1000, max_hp=1000,
                                    reaction_available=True, attitudes={"hero": "hostile"})
        scene = state[VARIANT_ID + ".state"]["spatial_scene"]
        hero_proxy = next(row for row in scene["entities"] if row["id"] == "hero")
        hero_proxy["metadata"].setdefault("enemy_of", []).append("defender")
        proxy = deepcopy(hero_proxy)
        proxy.update(id="defender", position=[30, 0, 0])
        proxy["metadata"].update(participant_ref="defender", enemy_of=["hero"])
        scene["entities"].append(proxy)
        self.owner.create_campaign(CampaignCreate(
            campaign_id="lite-ray-shield", display_name="Actual saved ray Shield proof",
            writer=WriterRole.DM, package_lock=original.package_lock, turn_id="turn",
            participants=(caster, defender),
            initial_state=tuple(StateValue(VARIANT_ID, key.removeprefix(VARIANT_ID + "."), value)
                                for key, value in state.items()),
        ))
        self.campaign_id = "lite-ray-shield"

    def snapshot(self):
        return self.owner.snapshot(self.campaign_id)

    def command(self, tool, parameters, identity=None):
        return replace(super().command(tool, parameters, identity), campaign_id=self.campaign_id)


@pytest.fixture
def ray_case(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    opened = []

    def make(*, shield=False, spell="scorching_ray", **changes):
        path = tmp_path / f"ray-{len(opened)}.db"
        case = RayShieldCase(path) if shield else Case(path, spell, **changes)
        opened.append(case)
        return _public(case)

    yield make
    for case in opened:
        archive_closed_case(case)


def _record_rolls(monkeypatch):
    rolls = []
    original = _effect_executor.dice_roll

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, "dice_roll", recorded)
    return rolls


def _parameters(case, targets):
    parameters = {key: value for key, value in case.parameters.items()
                  if key not in {"target", "target_distance", "caster_can_see_target"}}
    return parameters | {"attack_targets": targets}


def _retry_reopen(case, receipt, command, rolls):
    saved = case.snapshot()
    before = len(rolls)
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert case.snapshot().participants == saved.participants
    assert len(rolls) == before


def _finish_windows(case, event_id, rolls):
    last = None
    # Three base rays can expose at most three genuine hit windows in this fixture.
    for _ in range(4):
        if event_id not in case.lasting().get("pending_srd_action_stack", []):
            break
        event = thaw_json_value(case.lasting()["pending_srd_actions"][event_id])
        assert event["kind"] == "spell_attack" and event["stage"] == "spell_attack_reaction"
        assert event[CURSOR]["awaiting_hit"] is True
        last, command = case.act("complete_srd_pending_action", pending_action_id=event_id)
        _retry_reopen(case, last, command, rolls)
    assert event_id not in case.lasting().get("pending_srd_action_stack", [])
    assert last is not None
    return last


def test_actual_ray_shield_changes_saved_hit_then_resumes_order_without_repayment(ray_case, monkeypatch):
    case = ray_case(shield=True)
    case.start()
    monkeypatch.setattr(dice, "_rng", Random(8))  # Genuine first D20 is 8, adjusted by the real Wizard.
    rolls = _record_rolls(monkeypatch)
    targets = ["defender", "other", "defender"]
    begun, begin_command = case.act("begin_srd_spell_action", **(_parameters(case, targets) |
        {"half_cover_targets": ["defender"]}))
    event_id = begun.result["context"]["_pending_action_id"]
    assert rolls == []
    _retry_reopen(case, begun, begin_command, rolls)
    retained, retained_command = case.act("complete_srd_pending_action", pending_action_id=event_id)
    event = thaw_json_value(case.lasting()["pending_srd_actions"][event_id])
    assert event["actor_state_id"] == "hero" and event["target_state_id"] == "defender"
    assert event["owner_kind"] == "spell_cast" and event["kind"] == "spell_attack"
    assert event["stage"] == "spell_attack_reaction" and event["status"] == "open"
    assert event[CURSOR]["targets"] == targets and event[CURSOR]["index"] == 0
    assert event[CURSOR]["context"]["_spell_multi_attacks"] == []
    actual = event["spell_attack_context"]
    assert event["spell_attack_parameters"]["cover"] == "half"
    assert actual["_spell_target_armor_class"] == 18
    assert actual["_card_d20_roll"]["results"] == list(rolls[0].results)
    assert actual["_spell_critical_hit"] is False
    assert 16 <= actual["_card_d20_total"] < 21 and event["lite_shield_eligible"] is True
    assert [roll.sides for roll in rolls] == [20]  # No mirror, damage or second ray has run.
    assert case.entities()["defender"]["hp"] == case.entities()["other"]["hp"] == 1000
    assert case.entities()["hero"]["resources"]["spell_slot_2"] == 2
    assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is False
    _retry_reopen(case, retained, retained_command, rolls)

    shield, shield_command = case.act("cast_srd_reaction_spell", spell="shield",
        caster="defender", caster_state="defender", slot_resource="spell_slot_1",
        reaction_trigger_id=event_id, verbal_component_available=True, somatic_component_available=True)
    _retry_reopen(case, shield, shield_command, rolls)
    assert case.entities()["defender"]["resources"]["spell_slot_1"] == 2
    assert case.entities()["defender"]["reaction_available"] is False
    assert case.lasting()["pending_srd_action_stack"][-1] == event_id
    before = len(rolls)
    resumed, resume_command = case.act("complete_srd_pending_action", pending_action_id=event_id)
    assert rolls[before].sides == 20  # Next ray; saved first D20 was reused and dealt no damage.
    assert case.entities()["defender"]["hp"] == 1000
    saved = thaw_json_value(case.lasting()["pending_srd_actions"][event_id])
    assert saved[CURSOR]["index"] == 1 and saved["target_state_id"] == "other"
    assert saved[CURSOR]["context"]["_spell_multi_attack_outcomes"] == ["failure"]
    assert saved[CURSOR]["context"]["_spell_multi_attacks"][0] == actual["_spell_attack"]
    assert saved[CURSOR]["context"]["_spell_multi_damage_amounts"] == [0]
    _retry_reopen(case, resumed, resume_command, rolls)
    final = _finish_windows(case, event_id, rolls)
    assert sum(roll.sides == 20 for roll in rolls) == 3
    assert final.result["context"]["_spell_multi_attack_targets"] == targets
    assert case.entities()["hero"]["resources"]["spell_slot_2"] == 2
    assert case.entities()["defender"]["resources"]["spell_slot_1"] == 2
    before = case.snapshot()
    with pytest.raises((CommandRejected, MechanicsRejected)):
        case.execute(case.command("complete_srd_pending_action", {"pending_action_id": event_id}))
    assert case.snapshot().state == before.state and case.snapshot().revision == before.revision


def test_actual_critical_ray_and_dead_later_target_skip_preserve_saved_rolls(ray_case, monkeypatch):
    case = ray_case(entity_changes={"guard": {"hp": 1}})
    case.start()
    monkeypatch.setattr(dice, "_rng", Random(5))  # Genuine first D20 is natural 20.
    rolls = _record_rolls(monkeypatch)
    targets = ["guard", "guard", "other"]
    begun, _ = case.act("begin_srd_spell_action", **_parameters(case, targets))
    event_id = begun.result["context"]["_pending_action_id"]
    held, held_command = case.act("complete_srd_pending_action", pending_action_id=event_id)
    event = thaw_json_value(case.lasting()["pending_srd_actions"][event_id])
    assert event["attack_outcome"] == "critical_hit"
    assert event["spell_attack_context"]["_spell_critical_hit"] is True
    assert event["spell_attack_context"]["_spell_condition_critical_hit"] is False
    assert event["spell_attack_context"]["_card_d20_roll"]["results"] == [20]
    assert event["lite_shield_eligible"] is False and [roll.sides for roll in rolls] == [20]
    _retry_reopen(case, held, held_command, rolls)
    final = _finish_windows(case, event_id, rolls)
    assert case.entities()["guard"]["is_alive"] is False
    assert sum(roll.sides == 20 for roll in rolls) == 2
    first_damage = next(roll for roll in rolls if roll.sides == 6)
    assert len(first_damage.results) == 4
    assert final.result["context"]["_spell_multi_attack_targets"] == targets
    assert final.result["context"]["_spell_multi_attack_outcomes"][1] == "target_already_defeated"
    assert final.result["context"]["_spell_multi_attacks"][1] is None
    assert final.result["context"]["_spell_multi_target_numbers"][1] is None
    assert case.entities()["hero"]["resources"]["spell_slot_2"] == 2


def test_direct_non_staged_rays_keep_existing_immediate_resolution(ray_case, monkeypatch):
    case = ray_case()
    case.start()
    monkeypatch.setattr(dice, "_rng", Random(8))
    rolls = _record_rolls(monkeypatch)
    receipt, command = case.act("cast_spell", **_parameters(case, ["guard", "other", "extra"]))
    assert sum(roll.sides == 20 for roll in rolls) == 3
    outcomes = receipt.result["context"]["_spell_multi_attack_outcomes"]
    assert sum(roll.sides == 6 for roll in rolls) == sum(value == "success" for value in outcomes)
    assert "success" in outcomes
    assert not case.lasting().get("pending_srd_action_stack")
    assert case.entities()["hero"]["resources"]["spell_slot_2"] == 2
    _retry_reopen(case, receipt, command, rolls)


def test_existing_scalar_hit_completion_remains_owned_by_scalar_suffix(ray_case, monkeypatch):
    case = ray_case(spell="guiding_bolt")
    case.start()
    monkeypatch.setattr(dice, "_rng", Random(8))
    rolls = _record_rolls(monkeypatch)
    begun, _ = case.act("begin_srd_spell_action", **case.parameters)
    event_id = begun.result["context"]["_pending_action_id"]
    held, command = case.act("complete_srd_pending_action", pending_action_id=event_id)
    event = thaw_json_value(case.lasting()["pending_srd_actions"][event_id])
    assert event["kind"] == "spell_attack" and event["stage"] == "spell_attack_reaction"
    assert CURSOR not in event and event["target_state_id"] == "guard"
    assert [roll.sides for roll in rolls] == [20]
    _retry_reopen(case, held, command, rolls)
    finished, command = case.act("complete_srd_pending_action", pending_action_id=event_id)
    assert event_id not in case.lasting().get("pending_srd_action_stack", [])
    assert [roll.sides for roll in rolls] == [20, 6]
    assert len(rolls[-1].results) == 4 and case.entities()["guard"]["hp"] < 1000
    assert case.entities()["hero"]["resources"]["spell_slot_1"] == 2
    _retry_reopen(case, finished, command, rolls)


def test_hostile_wizard_natural_twenty_is_a_real_window_but_shield_cannot_convert_it(ray_case, monkeypatch):
    case = ray_case(shield=True)
    case.start()
    monkeypatch.setattr(dice, "_rng", Random(5))
    rolls = _record_rolls(monkeypatch)
    begun, _ = case.act("begin_srd_spell_action", **_parameters(case, ["defender", "other", "extra"]))
    event_id = begun.result["context"]["_pending_action_id"]
    held, command = case.act("complete_srd_pending_action", pending_action_id=event_id)
    event = thaw_json_value(case.lasting()["pending_srd_actions"][event_id])
    assert event["target_state_id"] == "defender" and event["attack_outcome"] == "critical_hit"
    assert event["spell_attack_context"]["_card_d20_roll"]["results"] == [20]
    assert event["spell_attack_context"]["_spell_condition_critical_hit"] is False
    assert event["lite_shield_eligible"] is False
    _retry_reopen(case, held, command, rolls)
    before = case.snapshot()
    with pytest.raises((CommandRejected, MechanicsRejected)):
        case.act("cast_srd_reaction_spell", spell="shield", caster="defender", caster_state="defender",
            slot_resource="spell_slot_1", reaction_trigger_id=event_id,
            verbal_component_available=True, somatic_component_available=True)
    assert case.snapshot().state == before.state and case.snapshot().revision == before.revision
    assert case.entities()["defender"]["resources"]["spell_slot_1"] == 3
    assert case.entities()["defender"]["reaction_available"] is True
    _finish_windows(case, event_id, rolls)
    assert sum(roll.sides == 20 for roll in rolls) == 3
    assert case.entities()["defender"]["hp"] < 1000
