"""Dream proofs use actual long casting, rest/source sleep, final damage and source retirement."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_dream_spell import PROFILES, PUBLIC_TOOLS, bind_runtime_consumers, extend_runtime
from scripts.srd_full.srd_extended_casting import extend_runtime as extend_casting
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm import dice
from sylvia_gm.application.bundled_packages import _srd_drafts as _base_drafts
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_scene_relations import _state


@lru_cache
def _runtime():
    source = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    text = extend_runtime(source.read_text(encoding="utf-8"))
    text = refresh_exploration_runtime(text)
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(PROFILES)
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "acid_arrow" in parameter.get("enum_values", []):
                parameter["enum_values"] = sorted(runtime["definitions"]["spells"])
    text = yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False)
    text = extend_casting(text, spell_ids=sorted(runtime["definitions"]["spells"]))
    text = bind_runtime_consumers(text)
    return _load_runtime(text), text.encode()


@lru_cache
def _mechanic(name):
    runtime, content = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=content,
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
def _authored(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    monkeypatch.setattr(dice, "_rng", Random(2))


def _case():
    state, data, parameters = cards._spell_case("dream")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
    scene["entities"][1]["metadata"]["known_by"] = ["hero"]
    scene["entities"][1]["metadata"]["plane"] = "material"
    state[f"{SRD}.state"] = {"lasting_tick": 0, "turn_effects": [], "spatial_scene": scene}
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0)
    return state, data, parameters


def _cast(state, **choices):
    _run(
        "cast_srd_dream_spell",
        state,
        caster_state="hero",
        target_state="guard",
        save_dc=19,
        slot_level=5,
        **choices,
    )
    return next(reversed(state[f"{SRD}.state"]["dream_spell_sources"]))


def test_actual_awake_target_waits_then_rest_sleep_accepts_dialogue_and_wake_recalls():
    state, _, _ = _case()
    identity = _cast(state)
    assert "incapacitated" in state[f"{SRD}.entities"]["hero"]["conditions"]
    assert state[f"{SRD}.entities"]["hero"]["dream_trance_speed_zero"] is True
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "converse_srd_dream", state, messenger_state="hero", dream_id=identity, message="Hello"
        )
    _run("begin_srd_rest", state, target_state="guard", kind="long", activity="sleep")
    _run(
        "converse_srd_dream",
        state,
        messenger_state="hero",
        dream_id=identity,
        message="Hello",
        environment="A blue sea",
    )
    _run("set_srd_rest_activity", state, target_state="guard", activity="light")
    record = state[f"{SRD}.state"]["dream_spell_sources"][identity]
    assert record["woke"] is True and record["active"] is False
    recall = state[f"{SRD}.entities"]["guard"]["dream_recall"][identity]
    assert recall["perfect_recall"] is True and recall["messages"] == ["Hello"]
    assert "incapacitated" not in state[f"{SRD}.entities"]["hero"]["conditions"]


def test_failed_nightmare_saves_damage_for_actual_wake_and_denies_rest_benefits():
    state, _, _ = _case()
    target = state[f"{SRD}.entities"]["guard"]
    target["saving_throw_modifiers"]["wisdom"] = -30
    _run("begin_srd_rest", state, target_state="guard", kind="long", activity="sleep")
    identity = _cast(state, dream_terrifying=True, dream_message_words=["Fear", "the", "moon"])
    record = state[f"{SRD}.state"]["dream_spell_sources"][identity]
    target = state[f"{SRD}.entities"]["guard"]
    assert record["terror_save_used"] is True and record["save_success"] is False
    assert target["hp"] == 1000 and record["wake_damage_pending"] is True
    target["hp"] = 900
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 900
    _run("complete_srd_rest", state, target_state="guard")
    target = state[f"{SRD}.entities"]["guard"]
    record = state[f"{SRD}.state"]["dream_spell_sources"][identity]
    assert 882 <= target["hp"] < 900 and target["rest_phase"] == "complete"
    assert record["wake_damage_settled"] is True
    assert record["wake_effective_damage"] == 900 - target["hp"]
    assert target.get("last_long_rest_tick") is None
    before = deepcopy(state)
    _run("settle_srd_dream_wake", state, target_state="guard")
    assert state == before


def test_genuine_one_minute_paid_cast_installs_after_clock_and_cost_once():
    from tests.srd_full.test_srd_extended_casting import _begin

    state, data, parameters = _case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 3
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    hero = state[f"{SRD}.entities"]["hero"]
    record = next(iter(state[f"{SRD}.state"]["dream_spell_sources"].values()))
    assert hero["resources"]["spell_slot_5"] == 2
    assert state[f"{SRD}.state"]["lasting_tick"] == 20
    assert record["started_tick"] == 20 and record["expires_tick"] == 9620
    assert record["spell_level"] == 5 and record["messenger"] == "hero"
    assert hero["dream_trance_speed_zero"] is True and "incapacitated" in hero["conditions"]


@pytest.mark.parametrize("save_modifier", [-30, 30])
def test_actual_save_variation_and_psychic_resistance_at_wake(save_modifier):
    state, _, _ = _case()
    target = state[f"{SRD}.entities"]["guard"]
    target["saving_throw_modifiers"]["wisdom"] = save_modifier
    target["damage_resistances"] = ["psychic"]
    _run("begin_srd_rest", state, target_state="guard", kind="long", activity="sleep")
    identity = _cast(state, dream_terrifying=True, dream_message_words=["Beware"])
    _run("set_srd_rest_activity", state, target_state="guard", activity="light")
    after_actual_wake = deepcopy(state)
    _run("settle_srd_dream_wake", state, target_state="guard")
    assert state == after_actual_wake
    record = state[f"{SRD}.state"]["dream_spell_sources"][identity]
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    if save_modifier > 0:
        assert record["save_success"] is True and hp == 1000
        assert record.get("wake_damage_roll") is None
    else:
        assert record["save_success"] is False
        assert record["wake_hp_temp_loss"] == 1000 - hp == record["wake_damage_roll"]["total"] // 2


@pytest.mark.parametrize("case", ["unknown", "other_plane", "eleven_words", "concealed_words"])
def test_actual_target_qualification_and_ten_word_limit_reject_atomically(case):
    state, _, _ = _case()
    subject = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]
    choices = {}
    if case == "unknown":
        subject["metadata"]["known_by"] = []
    elif case == "other_plane":
        subject["metadata"]["plane"] = "ethereal"
    elif case == "eleven_words":
        choices = {"dream_terrifying": True, "dream_message_words": ["a"] * 11}
    else:
        choices = {"dream_terrifying": True, "dream_message_words": ["two words"]}
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
        _cast(state, **choices)
    assert state == before


def test_source_expiry_releases_trance_but_preserves_later_wake_consequence():
    state, _, _ = _case()
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = -30
    _run("begin_srd_rest", state, target_state="guard", kind="long", activity="sleep")
    identity = _cast(state, dream_terrifying=True, dream_message_words=["Beware"])
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    record = state[f"{SRD}.state"]["dream_spell_sources"][identity]
    assert record["active"] is False and record["wake_damage_pending"] is True
    assert "incapacitated" not in state[f"{SRD}.entities"]["hero"]["conditions"]
    assert state[f"{SRD}.entities"]["hero"]["dream_trance_speed_zero"] is False
    _run("set_srd_rest_activity", state, target_state="guard", activity="light")
    _run("settle_srd_dream_wake", state, target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 1000


def test_final_owner_cloning_and_family_dispatch_repeat_without_duplicate_callbacks():
    source = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    once = bind_runtime_consumers(extend_runtime(source.read_text(encoding="utf-8")))
    twice = bind_runtime_consumers(extend_runtime(once))
    assert _load_runtime(once) == _load_runtime(twice)


@pytest.mark.parametrize("qualification", ["willing", "unwilling", "out_of_reach"])
def test_actual_other_messenger_requires_willing_touch(qualification):
    state, _, _ = _case()
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [3, 0, 0]
    if qualification == "out_of_reach":
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [10, 0, 0]
    before = deepcopy(state)
    options = {"dream_messenger": "guard", "dream_messenger_willing": qualification != "unwilling"}
    if qualification != "willing":
        with pytest.raises(RulesetPreconditionRejected):
            _cast(state, **options)
        assert state == before
        return
    identity = _cast(state, **options)
    assert "incapacitated" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "incapacitated" not in state[f"{SRD}.entities"]["hero"]["conditions"]
    _run("end_srd_dream_trance", state, messenger_state="guard", dream_id=identity)
    assert state[f"{SRD}.entities"]["guard"]["dream_trance_speed_zero"] is False
    assert state[f"{SRD}.state"]["has_dream_spells"] is False


def test_emerging_retires_only_dream_and_movement_cannot_bypass_its_speed_zero():
    state, _, _ = _case()
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["incapacitated"]
    identity = _cast(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Speed 0"):
        _run("derive_srd_scene_movement_budget", state, actor_state="hero", mode="walk")
    assert state == before
    _run("end_srd_dream_trance", state, messenger_state="hero", dream_id=identity)
    hero = state[f"{SRD}.entities"]["hero"]
    assert "incapacitated" in hero["conditions"]
    assert hero["dream_trance_speed_zero"] is False
    assert hero["speed"] == before[f"{SRD}.entities"]["hero"]["speed"]


def test_zero_hp_is_not_a_wake_and_real_final_damage_drains_nightmare_once():
    state, _, _ = _case()
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = -30
    _run("begin_srd_rest", state, target_state="guard", kind="long", activity="sleep")
    identity = _cast(state, dream_terrifying=True, dream_message_words=["Beware"])
    before = deepcopy(state)
    state[f"{SRD}.entities"]["guard"]["hp"] = 0
    _run("observe_srd_dream_sleep", state, target_state="guard")
    assert state[f"{SRD}.state"]["dream_spell_sources"][identity]["woke"] is False
    state = before
    _run(
        "apply_srd_qualifying_damage",
        state,
        target="guard",
        amount=1,
        adjustment=0,
        damage_type="fire",
        source_state_id="hero",
    )
    record = state[f"{SRD}.state"]["dream_spell_sources"][identity]
    assert record["woke"] is True and record["wake_damage_settled"] is True
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 999 - record["wake_hp_temp_loss"]
    assert state[f"{SRD}.state"]["dream_wake_target_ids"] == []
    assert state[f"{SRD}.state"]["has_dream_spells"] is False
    before = deepcopy(state)
    _run("drain_srd_dream_wakes", state)
    assert state == before


def _linked_drafts():
    """Keep the admitted public artifact links intact for the representative archive proof."""
    from sylvia_gm.application import bundled_packages
    from sylvia_gm.application.package_materialization import NormalizedPackageDraft

    primary, reference = _base_drafts()
    payload = dict(primary.payloads)
    declarations = json.loads(payload["content/content-declarations.json"])
    root = bundled_packages._bundled_srd_root()
    for inclusion in declarations["inclusions"]:
        for path in inclusion.get("links", {}).get("artifacts", []):
            if path not in payload:
                payload[path] = (root / path).read_bytes().replace(b"\r\n", b"\n")
    return NormalizedPackageDraft(primary.record, payload, 4), reference


@pytest.mark.parametrize("nightmare", [False, True])
def test_strict_paid_public_cast_reopen_retry_dialogue_and_later_perfect_recall(
    tmp_path,
    monkeypatch,
    nightmare,
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application import bundled_packages
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests import test_srd_spell_appearance as appearance
    from tests.srd_full.test_srd_extended_casting import _begin
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(bundled_packages, "_srd_drafts", _linked_drafts)
    monkeypatch.setattr(appearance, "_runtime", _runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", PUBLIC_TOOLS)
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)
    monkeypatch.setattr(public, "_spell_case", lambda spell: _case())
    database = tmp_path / "dream.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "dream",
            target_overrides={
                "hp": 1000,
                "max_hp": 1000,
                "saving_throw_modifiers": dict.fromkeys(
                    ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"],
                    -100,
                ),
            },
        )
        if nightmare:
            parameters.update(dream_terrifying=True, dream_message_words=["Beware"])
        names = {tool.name for tool in StatelessMcpMechanics(owner).list_tools("casting")}
        assert names >= PUBLIC_TOOLS
        begun, begin_command = _act(owner, "begin_srd_spell_cast", **_begin(parameters))
        assert begun.status is ActionStatus.COMMITTED
        channel = owner.snapshot("casting")
        assert channel.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 3
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(begin_command) == begun
        assert owner.snapshot("casting").state == channel.state
        continued, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED
        paid, cast_command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert paid.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        identity = next(iter(saved.state[f"{SRD}.state"]["dream_spell_sources"]))
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(cast_command) == paid
        assert owner.snapshot("casting").state == saved.state
        resting, _ = _act(
            owner, "begin_srd_rest", target_state="guard", kind="long", activity="sleep"
        )
        assert resting.status is ActionStatus.COMMITTED
        if nightmare:
            record = owner.snapshot("casting").state[f"{SRD}.state"]["dream_spell_sources"][
                identity
            ]
            assert record["save_success"] is False and record["wake_damage_pending"] is True
            spoken, spoken_command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        else:
            spoken, spoken_command = _act(
                owner,
                "converse_srd_dream",
                messenger_state="hero",
                dream_id=identity,
                message="Follow the river",
                environment="A clear flowing river",
            )
        assert spoken.status is ActionStatus.COMMITTED
        dreamed = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(spoken_command) == spoken
        assert owner.snapshot("casting").state == dreamed.state
        woke, _ = _act(owner, "set_srd_rest_activity", target_state="guard", activity="light")
        assert woke.status is ActionStatus.COMMITTED
        recall = owner.snapshot("casting").state[f"{SRD}.entities"]["guard"]["dream_recall"][
            identity
        ]
        assert recall["perfect_recall"] is True
        if nightmare:
            final = owner.snapshot("casting").state
            record = final[f"{SRD}.state"]["dream_spell_sources"][identity]
            assert record["wake_damage_settled"] is True
            assert 0 < record["wake_hp_temp_loss"] <= 18
            assert final[f"{SRD}.entities"]["guard"]["hp"] == 1000 - record["wake_hp_temp_loss"]
        else:
            assert recall["messages"] == ["Follow the river"]
