"""Meaningful exchange consumes actual saved Symbol Discord, with native casting costs."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_triggers import _tools

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_detection_masks as masks
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_triggers as owner
from tests import test_srd_symbol_exploration as clock
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_detection_masks import _later_information_cast
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act

COMMUNICATION_TOOLS = (
    "check_srd_symbol_afflictions",
    "qualify_srd_symbol_communication",
    "cast_srd_spell_message",
    "communicate_srd_spell",
    "communicate_srd_telepathic_bond_node",
    "reply_srd_spell_message_node",
    "validate_srd_telepathic_bond_member",
)


@lru_cache
def _runtime():
    runtime = deepcopy(clock._runtime()[0])
    changed = _tools(runtime)
    runtime["tools"].update({n: changed[n] for n in COMMUNICATION_TOOLS if n in changed})
    return runtime, yaml.safe_dump(runtime, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, payload = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=payload,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _actual_owner(monkeypatch):
    monkeypatch.setattr(owner, "_runtime", _runtime)
    monkeypatch.setattr(owner, "_mechanic", _mechanic)
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards,
        "_spell_definitions",
        lambda: _runtime()[0]["definitions"]["spells"],
    )


def _armed():
    state, data, _, identity = owner._cast("symbol", symbol_effect="discord")
    for actor in ("hero", "guard", "other"):
        state[f"{SRD}.entities"][actor]["languages"] = ["common"]
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["wisdom"] = 30
    return state, data, identity


def _activate(state, identity, affected):
    state[f"{SRD}.entities"][affected]["saving_throw_modifiers"]["wisdom"] = -30
    owner._event(state, identity, event="open")
    assert (
        _run("check_srd_symbol_afflictions", state, target_state=affected).result["context"][
            "_symbol_discord"
        ]
        is True
    )


@pytest.mark.parametrize("affected", ["hero", "guard"])
def test_real_discord_blocks_conversation_after_reopen_and_exact_expiry_restores_it(affected):
    state, _, identity = _armed()
    _activate(state, identity, affected)
    state = json.loads(json.dumps(state))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Discord"):
        _run("communicate_srd_spell", state, speaker_state="hero", listener_state="guard")
    assert state == before
    for actor in ("hero", "guard"):
        owner._entry(state, actor)["position"] = [100, 0, 0]
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert (
        _run("communicate_srd_spell", state, speaker_state="hero", listener_state="guard").result[
            "status"
        ]
        == "committed"
    )


@pytest.mark.parametrize("spell", ["message", "sending"])
@pytest.mark.parametrize("affected", ["hero", "guard"])
def test_real_paid_message_cast_keeps_cost_but_cannot_deliver_meaningful_words(
    spell, affected, monkeypatch
):
    state, data, identity = _armed()
    _activate(state, identity, affected)
    before = state[f"{SRD}.entities"]["hero"]["resources"].get("spell_slot_3")
    completed = []
    original_run = masks._run

    def capture(name, *args, **kwargs):
        result = original_run(name, *args, **kwargs)
        if name == "cast_spell":
            completed.append(result)
        return result

    monkeypatch.setattr(masks, "_run", capture)
    _later_information_cast(
        state, data, spell, message_words=["Open", "the", "gate"], divination_subject_met=True
    )
    assert state[f"{SRD}.entities"]["guard"].get("received_spell_message") is None
    assert not [r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == spell]
    assert completed and "Discord prevents meaningful communication" in str(
        completed[0].result["result_card"]
    )
    if spell == "sending":
        assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == before - 1
    assert state[f"{SRD}.entities"][affected]["symbol_affliction_managed"] is True


def test_sending_reply_discord_preserves_independent_noncommunication_sender_block_choice():
    state, data, identity = _armed()
    _later_information_cast(
        state, data, "sending", message_words=["Open", "the", "gate"], divination_subject_met=True
    )
    message = state[f"{SRD}.entities"]["guard"]["received_spell_message"]
    _activate(state, identity, "guard")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Discord"):
        _run(
            "reply_srd_spell_message",
            state,
            recipient_state="guard",
            sender_state="hero",
            message_id=message["id"],
            reply_words=["Ready"],
        )
    assert state == before
    result = _run(
        "reply_srd_spell_message",
        state,
        recipient_state="guard",
        sender_state="hero",
        message_id=message["id"],
        reply_words=[],
        block_sending=True,
    ).result["context"]
    assert result["_sender_blocked"] is True and result["_reply_received"] is False
    assert state[f"{SRD}.entities"]["guard"]["sending_blocks"]["hero"] == (
        state[f"{SRD}.state"]["lasting_tick"] + 9600
    )


def test_actual_bond_delivery_is_blocked_without_retiring_the_paid_bond():
    state, data, identity = _armed()
    _later_information_cast(state, data, "telepathic_bond", targets=["hero", "guard"])
    record = next(
        r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == "telepathic_bond"
    )
    _activate(state, identity, "guard")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Discord"):
        _run(
            "communicate_srd_telepathic_bond",
            state,
            caster_state="hero",
            sender_state="hero",
            recipient_state="guard",
            communication="The gate opens.",
        )
    assert state == before and record in state[f"{SRD}.state"]["turn_effects"]


def test_discord_prevents_initial_bond_member_eligibility_but_keeps_independent_language():
    state, data, identity = _armed()
    _activate(state, identity, "guard")
    _later_information_cast(state, data, "telepathic_bond", targets=["hero", "guard"])
    record = next(
        r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == "telepathic_bond"
    )
    assert record["members"] == ["hero"]
    assert state[f"{SRD}.entities"]["guard"]["languages"] == ["common"]


def test_communication_owner_emission_is_idempotent():
    runtime = deepcopy(_runtime()[0])
    repeated = _tools(runtime)
    for name in COMMUNICATION_TOOLS:
        if name in repeated:
            assert repeated[name] == runtime["tools"][name]


def test_actual_discord_source_cannot_apply_to_a_different_actor_through_a_stale_index():
    state, _, identity = _armed()
    _activate(state, identity, "guard")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["symbol_affliction_managed"] = True
    hero["timed_condition_source_ids"] = deepcopy(
        state[f"{SRD}.entities"]["guard"]["timed_condition_source_ids"]
    )
    result = _run(
        "qualify_srd_symbol_communication", state, speaker_state="hero", listener_state="other"
    ).result["context"]
    assert result["_symbol_exchange_blocked"] is False


def test_communication_fragment_admits_actual_parent_archive(monkeypatch):
    clock.test_clock_delta_is_admitted_with_actual_completed_parent_archive(monkeypatch)


def test_actual_public_paid_discord_source_reopen_exchange_rejection_and_saved_retry(
    tmp_path, monkeypatch
):
    import sylvia_gm.application.bundled_packages as bundled

    def case(spell):
        state, data, params = owner._case(spell, symbol_effect="discord")
        for actor in ("hero", "guard", "other"):
            state[f"{SRD}.entities"][actor]["languages"] = ["common"]
            state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["wisdom"] = (
                -30 if actor == "guard" else 30
            )
        return state, data, params

    monkeypatch.setattr(
        bundled, "_bundled_srd_root", lambda: clock.PARENT / "rulesets/dnd-srd-5.2.1"
    )
    monkeypatch.setattr(public_casting, "bundled_srd_archive", owner._test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", case)
    owner._test_archive.cache_clear()
    database = tmp_path / "discord.db"
    with AuthorityOwner.open(database, "casting-owner") as authority:
        _, _, params = public_casting._create_spell_campaign(authority, "symbol")
        begun, _ = _act(authority, "begin_srd_spell_cast", **_begin(params))
        assert begun.status is ActionStatus.COMMITTED
        continued, _ = _act(
            authority,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED
        cast, _ = _act(authority, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        activated, command = _act(
            authority,
            "resolve_srd_spell_trigger",
            trigger_id=cast.result["context"]["_trigger_id"],
            event_actor="guard",
            event_kind="open",
            fictional_match=True,
        )
        assert activated.status is ActionStatus.COMMITTED, activated.result
        saved = authority.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as authority:
        assert authority.snapshot("casting") == saved
        with pytest.raises(MechanicsRejected, match="Discord"):
            _act(authority, "communicate_srd_spell", speaker_state="guard", listener_state="other")
        assert authority.snapshot("casting") == saved

        class NoReroll(Random):
            def randint(self, _a, _b):
                pytest.fail("A saved Discord activation retry rolled again")

        monkeypatch.setattr(dice, "_rng", NoReroll(99))
        assert authority.execute_action(command) == activated
        assert authority.snapshot("casting") == saved
    owner._test_archive.cache_clear()
