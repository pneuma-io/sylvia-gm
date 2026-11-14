"""Power Words retain source precedence and a trusted, durable target choice."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts import srd_power_words as words
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run

SOURCE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-power-words/dnd-srd-5.2.1")
)


@lru_cache
def _runtime():
    text = words.bind_runtime_consumers((SOURCE / "runtime/ruleset.yaml").read_text())
    assert words.bind_runtime_consumers(text) == text
    return yaml.load(text, Loader=yaml.CSafeLoader), text.encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


class Rolls:
    def __init__(self, d20=1):
        self.d20 = d20
        self.saves = 0

    def randint(self, _a, sides):
        if sides == 20:
            self.saves += 1
            return self.d20
        return 1


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())
    original = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = original(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _sources(state):
    return state[f"{SRD}.state"]["timed_condition_sources"]


def test_paid_stun_overlap_saves_once_at_real_end_turn_then_restores_other_cast(monkeypatch):
    state, data, params = cards._spell_case("power_word_stun")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=150, max_hp=200, conditions=[], initiative_modifier=0)
    state[f"{SRD}.entities"]["other"]["initiative_modifier"] = 0
    target["saving_throw_modifiers"]["constitution"] = 0
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
    _run(
        "prepare_srd_creature",
        state,
        creature_id="mage",
        name="Mage",
        source="Synthetic native caster: Wisdom +3, spell attack +7, DC 20, CON save +6.",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=6,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=20,
        spells=[{"spell": "power_word_stun", "uses_per_day": 2, "casting_level": 8}],
    )
    npc = {k: v for k, v in params.items() if k not in {"caster", "slot_resource"}}
    npc["caster_state"] = "mage"
    _run("cast_spell_outside_combat", state, participant_data=data, **npc)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("cast_spell_outside_combat", state, participant_data=data, **npc)
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["power_word_stun"] == 0
    sources = sorted(
        ((i, s) for i, s in _sources(state).items() if s["installed"]),
        key=lambda row: row[1]["sequence"],
    )
    assert [s["save_dc"] for _, s in sources] == [18, 20, 20]
    _run(
        "install_srd_turn_effect",
        state,
        caster_state="hero",
        target_state="guard",
        lasting_state="state",
        spell="stunning_strike",
        effect_label="Stunning Strike",
        kind="stunned",
        expiry_owner="caster",
        expiry_phase="start",
        magnitude=0,
        dice_count=0,
        damage_die="d4",
        damage_type="force",
    )
    state = json.loads(json.dumps(state))
    _run(
        "start_combat_encounter_entities",
        state,
        first_actor="guard",
        second_actor="other",
        first_surprised=False,
        second_surprised=False,
        tie_winner="first",
    )
    _run("begin_combat_turn", state)
    rolls = Rolls(20)
    monkeypatch.setattr(dice, "_rng", rolls)
    ended = _run("end_combat_turn", state)
    assert rolls.saves == 1
    assert "DC 20" in str(ended.result["result_card"])
    assert _sources(state)[sources[-1][0]]["installed"] is False
    assert all(_sources(state)[i]["installed"] for i, _ in sources[:-1])
    assert "stunned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    before = deepcopy(state)
    _run("resolve_srd_timed_condition_end_turn", state, target_state="guard", lasting_state="state")
    assert rolls.saves == 1
    assert state == before
    # The next real end turn selects the other DC20 casting, never another save
    # in the original event; the independent caster-start Stun remains installed.
    _run("begin_combat_turn", state)
    _run("end_combat_turn", state)
    assert _sources(state)[sources[-2][0]]["installed"] is False
    assert _sources(state)[sources[0][0]]["installed"] is True
    assert "stunned" in state[f"{SRD}.entities"]["guard"]["conditions"]


def _archive():
    primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    runtime, encoded = _runtime()
    for declaration in record["tools"]:
        if any(
            p["name"] == "use_prone_reaction_to_stand"
            for p in runtime["tools"][declaration["name"]].get("parameters", [])
        ):
            declaration["input_schema"] = _tool_schema(
                runtime["tools"][declaration["name"]], tool_name=declaration["name"]
            )
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    return bundled_packages._archive(
        (NormalizedPackageDraft(record, payloads, primary.format_version), reference)
    )


@pytest.mark.parametrize("selection", ["stand", "remain-prone"])
def test_public_heal_target_choice_reopens_commits_real_costs_and_retries(
    tmp_path, monkeypatch, selection
):
    from sylvia_gm.application.authority import AuthorityOwner, ResolveMechanicsChoiceCommand
    from sylvia_gm.domain.authority import ActionStatus
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: SOURCE)
    monkeypatch.setattr(public, "ROOT", SOURCE.parents[1])
    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    database = tmp_path / "heal.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner,
            "power_word_heal",
            target_overrides={"conditions": ["prone", "stunned", "poisoned", "blinded"]},
        )
        initial = owner.snapshot("casting")
        waiting, action = _act(owner, "cast_spell_outside_combat", **params)
        assert waiting.status == "awaiting_choice"
        assert waiting.choice.choice_id == "power-word-heal-stand"
        assert "guard" in waiting.choice.prompt
        assert owner.snapshot("casting").state == initial.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(action) == waiting
        choice = ResolveMechanicsChoiceCommand(
            campaign_id="casting",
            action_id=action.action_id,
            resolution_id="target-reaction",
            owner_id="casting-owner",
            expected_revision=initial.revision,
            choice_id=waiting.choice.choice_id,
            option_id=selection,
        )
        healed = owner.resolve_mechanics_choice(choice)
        assert healed.status is ActionStatus.COMMITTED, healed.result
        saved = owner.snapshot("casting")
        guard = saved.state[f"{SRD}.entities"]["guard"]
        assert guard["hp"] == guard["max_hp"] == 100
        assert guard["temp_hp"] == 0
        assert guard["conditions"] == (
            ["blinded"] if selection == "stand" else ["prone", "blinded"]
        )
        assert guard["reaction_available"] is (selection != "stand")
        if selection == "stand":
            assert guard["last_reaction"] == "power_word_heal_stand"
            assert guard["reaction_spent_tick"] is not None
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(action) == healed
        assert owner.resolve_mechanics_choice(choice) == healed
        assert owner.snapshot("casting") == saved
