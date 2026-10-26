"""Mirror Image diverts real hits after saved defenses, preserving actual attack budgets."""

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_mirror_image_consumers import MIRROR_EXPORTS, _spell, _tools, _weapon, _wrapper

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_attack_budget import _attack, _combat_case, _npc_attack
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_reaction_spells import _cast_parameters
from tests.srd_full.test_srd_reaction_windows import _parameters
from tests.srd_full.test_srd_spell_result_cards import _spell_case
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene
from tests.srd_full.test_srd_unarmed_creatures import _strike

PARENT = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-mirror-image-consumers/input-1"))


@lru_cache
def _runtime():
    payload = (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == (
        "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
    )
    runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
    runtime["tools"].update(_tools(runtime))
    # This admitted runtime already has Reaction clones; apply the identical owning suffix
    # transform to its real saved leaves that canonical pre-clone binding will regenerate.
    for name, attacker in [
        ("complete_srd_character_attack", "actor_state"),
        ("complete_srd_creature_attack", "actor"),
    ]:
        runtime["tools"][name] = _weapon(runtime["tools"][name], attacker, "target")
    runtime["tools"]["complete_srd_spell_attack_hit"] = _spell(
        runtime["tools"]["complete_srd_spell_attack_hit"]
    )
    # Root's Reaction owner appends these exact four exports when the scalar owner has
    # its actual diversion marker; this completed-parent test models that generated join.
    if "_mirror_redirected" in str(runtime["tools"]["cast_spell_attack_damage"]["effects"]):
        runtime["tools"]["complete_srd_spell_attack_consequences"], found = _wrapper(
            runtime["tools"]["complete_srd_spell_attack_consequences"],
            {"complete_srd_spell_attack_hit"},
        )
        assert found
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


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
            capability="dnd.srd.5.2.1.combat",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _actual_owner(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


def _armed():
    state, data = _combat_case()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.state"] = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": _scene(
            _entity("hero", [0, 0, 0], participant_ref="hero"),
            _entity("mage", [5, 0, 0], participant_ref="mage"),
            _entity("guard", [5, 5, 0], participant_ref="guard"),
        ),
    }
    supplies = _spell_case("fire_shield")[0][f"{SRD}.entities"]["hero"]["spell_materials"]
    _run(
        "prepare_srd_creature",
        state,
        creature_id="mage",
        name="Mirror Mage",
        source="Synthetic NPC: AC13, HP1000, Speed30, Str/Dex/Con save0, Int+3, attack+7, DC15.",
        armor_class=13,
        hit_points=1000,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=0,
        strength_save=0,
        dexterity_save=0,
        wisdom_save=0,
        inventory=[item["inventory_item"] for item in supplies],
        spell_materials=supplies,
        spellcasting_ability="intelligence",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[
            {"spell": "mirror_image", "uses_per_day": 2},
            {"spell": "shield", "uses_per_day": 2},
            {"spell": "fire_shield", "uses_per_day": 2},
        ],
    )
    state[f"{SRD}.entities"]["mage"]["dodging"] = False
    _run(
        "cast_spell_outside_combat",
        state,
        spell="mirror_image",
        caster_state="mage",
        verbal_component_available=True,
        somatic_component_available=True,
    )
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        normal_action_spent=False,
        attacks_remaining=0,
    )
    source = next(
        v
        for v in state[f"{SRD}.state"]["illusion_spell_sources"].values()
        if v["spell"] == "mirror_image" and v["installed"]
    )
    return state, data, source["id"]


def _source(state, source_id):
    return state[f"{SRD}.state"]["illusion_spell_sources"][source_id]


def _spell_actor(state, spell):
    """Give the attacker a real admitted class, grant, prepared spell and paid slot source."""
    spell_state, data, params = _spell_case(spell)
    state[f"{SRD}.entities"]["hero"] = spell_state[f"{SRD}.entities"]["hero"]
    if "target" in params:
        params["target"] = "mage"
    return data, params


class _Rolls(Random):
    def __init__(self, attack=20, mirror=6):
        super().__init__(0)
        self.attack = attack
        self.mirror = mirror
        self.sides = []

    def randint(self, _a, b):
        self.sides.append(b)
        return self.attack if b == 20 else self.mirror if b == 6 else 1


def test_actual_weapon_hit_diverts_before_damage_and_preserves_extra_attack_and_native_card(
    monkeypatch,
):
    state, data, source_id = _armed()
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _attack(state, data, target="mage").result
    assert result["context"]["_attack_outcome"] == "critical_hit"
    assert result["context"]["_mirror_redirected"] is True
    assert result["context"]["_damage_applied"] == 0
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert _source(state, source_id)["remaining_duplicates"] == 2
    assert rolls.sides == [20, 6, 6, 6]
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 1
    details = {r["label"]: r["value"] for r in result["result_card"]["details"]}
    assert details["Mirror Image diverted"] is True
    assert details["Duplicates remaining"] == 2
    assert "Damage dice" not in details


@pytest.mark.parametrize(
    "sense,reach,bypass",
    [
        ("blindsight_feet", 5, False),
        ("blindsight_feet", 10, True),
        ("truesight_feet", 5, False),
        ("truesight_feet", 10, True),
    ],
)
def test_real_three_dimensional_sense_reach_limits_mirror_bypass(monkeypatch, sense, reach, bypass):
    state, _, source_id = _armed()
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [0, 0, 10]
    state[f"{SRD}.entities"]["hero"][sense] = reach
    monkeypatch.setattr(dice, "_rng", _Rolls())
    result = _run(
        "resolve_srd_mirror_image_hit", state, target_state="mage", attacker_state="hero"
    ).result["context"]
    assert result["_mirror_redirected"] is not bypass
    assert len(result["_mirror_dice"]) == (0 if bypass else 3)
    assert _source(state, source_id)["remaining_duplicates"] == (3 if bypass else 2)


def test_actual_blinded_attacker_cannot_be_diverted_by_visual_duplicates(monkeypatch):
    state, _, source_id = _armed()
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _run(
        "resolve_srd_mirror_image_hit", state, target_state="mage", attacker_state="hero"
    ).result["context"]
    assert result["_mirror_redirected"] is False
    assert rolls.sides == []
    assert _source(state, source_id)["remaining_duplicates"] == 3


@pytest.mark.parametrize("attack,shield_misses", [(10, True), (20, False)])
def test_real_saved_shield_window_precedes_mirror_and_reopened_continuation_rolls_it_once(
    monkeypatch,
    attack,
    shield_misses,
):
    state, data, source_id = _armed()
    rolls = _Rolls(attack=attack)
    monkeypatch.setattr(dice, "_rng", rolls)
    begun = _run(
        "begin_srd_character_attack",
        state,
        participant_data=data,
        **(_parameters() | {"target": "mage"}),
    )
    pending = begun.result["context"]["_pending_action_id"]
    event = deepcopy(state[f"{SRD}.state"]["pending_srd_actions"][pending])
    assert rolls.sides == [20]
    assert _source(state, source_id)["remaining_duplicates"] == 3
    _run("cast_srd_reaction_spell", state, **_cast_parameters("shield", pending))
    state = json.loads(json.dumps(state))

    class SavedAttack(_Rolls):
        def randint(self, a, b):
            assert b != 20, "The saved attack roll must not repeat after defense"
            return super().randint(a, b)

    continuation_rolls = SavedAttack()
    monkeypatch.setattr(dice, "_rng", continuation_rolls)
    finished = _run(
        "complete_srd_pending_action", state, participant_data=data, pending_action_id=pending
    ).result["context"]
    assert finished["_attack_roll"] == event["attack_roll"]
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert _source(state, source_id)["remaining_duplicates"] == (3 if shield_misses else 2)
    assert continuation_rolls.sides == ([] if shield_misses else [6, 6, 6])


@pytest.mark.parametrize("effect", ["grapple", "shove_prone", "shove_push"])
def test_unarmed_save_effects_never_roll_or_destroy_visual_duplicates(monkeypatch, effect):
    state, data, source_id = _armed()
    data["size_rank"] = 2
    rolls = _Rolls(attack=1)
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _strike(state, data, effect, target_state="mage").result["context"]
    assert result["_unarmed_roll_kind"] == "Saving throw"
    assert rolls.sides == [20]
    assert _source(state, source_id)["remaining_duplicates"] == 3


def test_owned_hit_binder_retains_top_level_critical_anchors_and_is_idempotent():
    runtime, _ = _runtime()
    again = _tools(runtime)
    for name, tool in again.items():
        assert tool == runtime["tools"][name]
    assert any(
        e.get("set", {}).get("field") == "_critical_hit"
        for e in again["resolve_combat_attack"]["effects"]
    )
    assert any(
        e.get("set", {}).get("field") == "_spell_critical_hit"
        for e in again["cast_spell_attack_damage"]["effects"]
    )


@pytest.mark.parametrize("spell", ["fire_bolt", "ray_of_frost", "starry_wisp", "poison_spray"])
def test_actual_public_scalar_spell_hit_skips_damage_and_source_owned_hit_riders(
    monkeypatch, spell
):
    state, _, source_id = _armed()
    data, params = _spell_actor(state, spell)
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _run("cast_spell", state, participant_data=data, **params).result["context"]
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert _source(state, source_id)["remaining_duplicates"] == 2
    assert rolls.sides == [20, 6, 6, 6]
    assert result["_spell_attack_kind"] == "critical_hit"
    assert "Duplicate hit" in str(result["_spell_summary_rows"])
    assert not any(
        record.get("spell") in {"ray_of_frost", "starry_wisp"}
        for record in state[f"{SRD}.state"].get("turn_effects", [])
    )


def test_actual_multi_attack_cast_rechecks_each_remaining_duplicate_and_final_retirement(
    monkeypatch,
):
    state, _, source_id = _armed()
    data, params = _spell_actor(state, "scorching_ray")
    params.update(attack_targets=["mage"] * 4, slot_resource="spell_slot_3")
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _run("cast_spell", state, participant_data=data, **params).result["context"]
    assert _source(state, source_id)["installed"] is False
    assert _source(state, source_id)["remaining_duplicates"] == 0
    assert rolls.sides == [20, 6, 6, 6, 20, 6, 6, 20, 6, 20, 6, 6, 6, 6]
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 976
    assert str(result["_spell_summary_rows"]).count("Duplicate hit") == 3


def test_actual_retained_produce_flame_critical_diverts_before_dice_and_keeps_source(monkeypatch):
    state, _, source_id = _armed()
    data, params = _spell_actor(state, "produce_flame")
    _run("cast_spell", state, participant_data=data, **params)
    source = deepcopy(
        [r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == "produce_flame"]
    )
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _run(
        "hurl_srd_produce_flame",
        state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        target="mage",
    ).result
    assert result["context"]["_produce_attack_kind"] == "critical_hit"
    assert result["context"]["_mirror_redirected"] is True
    assert result["context"]["_produce_damage_applied"] == 0
    assert rolls.sides == [20, 6, 6, 6]
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert _source(state, source_id)["remaining_duplicates"] == 2
    assert [
        r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == "produce_flame"
    ] == source
    assert state[f"{SRD}.combat"]["action_available"] is False


@pytest.mark.parametrize("redirected", [True, False])
def test_actual_unarmed_damage_diversion_skips_stunning_save_and_retaliation(
    monkeypatch, redirected
):
    state, data, source_id = _armed()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    shield = _spell_case("fire_shield")[2]
    shield.pop("caster")
    shield.pop("slot_resource")
    shield.update(caster_state="mage", target="mage")
    _run("cast_spell_outside_combat", state, **shield)
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True, turn_sequence=1)
    data.update(classes=[{"id": "monk", "level": 5}], level=5, size_rank=2)
    data["abilities"]["wisdom"] = 16
    actor = state[f"{SRD}.entities"]["hero"]
    actor.update(resources={"focus_point": 5}, resource_maxima={"focus_point": 5})
    hp = actor["hp"]
    rolls = _Rolls(mirror=6 if redirected else 1)
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _strike(
        state,
        data,
        "damage",
        target_state="mage",
        use_stunning_strike=True,
        stunning_forego_save=True,
    ).result["context"]
    assert result["_mirror_redirected"] is redirected
    assert _source(state, source_id)["remaining_duplicates"] == (2 if redirected else 3)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == (5 if redirected else 4)
    assert ("stunned" in state[f"{SRD}.entities"]["mage"]["conditions"]) is not redirected
    assert (state[f"{SRD}.entities"]["hero"]["hp"] < hp) is not redirected
    if redirected:
        assert rolls.sides == [20, 6, 6, 6]
        assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000


def test_saved_public_spell_hit_after_shield_forwards_diversion_to_retained_riders(monkeypatch):
    state, _, source_id = _armed()
    data, params = _spell_actor(state, "ray_of_frost")
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    _run("begin_srd_spell_action", state, participant_data=data, **params)
    pending = state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=pending)
    assert _source(state, source_id)["remaining_duplicates"] == 3
    assert rolls.sides == [20]
    _run("cast_srd_reaction_spell", state, **_cast_parameters("shield", pending))
    state = json.loads(json.dumps(state))

    class SavedHit(_Rolls):
        def randint(self, a, b):
            assert b != 20, "The pending spell must retain its actual hit roll"
            return super().randint(a, b)

    continuation = SavedHit()
    monkeypatch.setattr(dice, "_rng", continuation)
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=pending)
    assert _source(state, source_id)["remaining_duplicates"] == 2
    assert continuation.sides == [6, 6, 6]
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert not any(
        r.get("spell") == "ray_of_frost" for r in state[f"{SRD}.state"].get("turn_effects", [])
    )
    assert not state[f"{SRD}.state"]["pending_srd_action_stack"]
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["shield"] == 1
    saved_call = next(
        e
        for e in _runtime()[0]["tools"]["complete_srd_spell_attack_consequences"]["effects"]
        if e.get("call") == "complete_srd_spell_attack_hit"
    )
    assert all(saved_call["export_context"][name] == name for name in MIRROR_EXPORTS)


def test_actual_graze_miss_damages_creature_without_testing_or_consuming_duplicates(monkeypatch):
    state, data, source_id = _armed()
    data["weapon_masteries"].append("greatsword")
    state[f"{SRD}.entities"]["hero"]["inventory"].append("greatsword")
    rolls = _Rolls(attack=1)
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _attack(
        state,
        data,
        target="mage",
        weapon="greatsword",
        wielding_two_handed=True,
        use_graze_on_miss=True,
    ).result["context"]
    assert result["_attack_outcome"] == "miss"
    assert result["_graze_damage"] == 3
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 997
    assert _source(state, source_id)["remaining_duplicates"] == 3
    assert rolls.sides == [20]


@pytest.mark.parametrize("spell", ["divine_favor", "bestow_curse"])
def test_paid_damage_rider_spell_does_not_roll_or_damage_a_diverted_weapon_hit(monkeypatch, spell):
    state, _, source_id = _armed()
    data, params = _spell_actor(state, spell)
    if spell == "bestow_curse":
        params.update(targets=["mage"], curse_mode="damage", forego_save_targets=["mage"])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True)
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _attack(state, data, target="mage").result["context"]
    assert result["_mirror_redirected"] is True
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert rolls.sides == [20, 6, 6, 6]
    assert _source(state, source_id)["remaining_duplicates"] == 2


@pytest.mark.parametrize("distance,bypass", [(120, True), (121, False)])
def test_paid_true_seeing_uses_current_timed_source_and_its_exact_reach(
    monkeypatch, distance, bypass
):
    state, _, source_id = _armed()
    data, params = _spell_actor(state, "true_seeing")
    params["target"] = "hero"
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [distance, 0, 0]
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _run(
        "resolve_srd_mirror_image_hit", state, target_state="mage", attacker_state="hero"
    ).result["context"]
    assert result["_mirror_redirected"] is not bypass
    assert _source(state, source_id)["remaining_duplicates"] == (3 if bypass else 2)
    assert rolls.sides == ([] if bypass else [6, 6, 6])


def test_actual_polymorph_bear_hit_diversion_blocks_its_source_owned_prone_rider(monkeypatch):
    state, _, source_id = _armed()
    data, params = _spell_actor(state, "polymorph")
    params.update(target="guard", transformation_form="brown_bear", forego_save_targets=["guard"])
    state[f"{SRD}.entities"]["guard"].update(
        challenge_rating_eighths=32,
        ability_scores=dict.fromkeys(data["abilities"], 12),
        size_rank=2,
        languages=["common"],
        passive_perception=11,
        saving_throw_modifiers=dict.fromkeys(data["abilities"], 1),
    )
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
    )
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _run(
        "attack_srd_transformed_form",
        state,
        actor_state="guard",
        target_state="mage",
        attack_name="Claw",
        attack_distance=5,
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
    ).result["context"]
    assert result["_mirror_redirected"] is True
    assert _source(state, source_id)["remaining_duplicates"] == 2
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert "prone" not in state[f"{SRD}.entities"]["mage"]["conditions"]
    assert rolls.sides == [20, 6, 6, 6]


@lru_cache
def _test_archive():
    """Admit this bounded hit binder over the exact completed parent, without a build."""
    with patch.object(
        bundled_packages, "_bundled_srd_root", lambda: PARENT / "rulesets/dnd-srd-5.2.1"
    ):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    runtime, payload = _runtime()
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(runtime["tools"][row["name"]], tool_name=row["name"])
    return bundled_packages._archive(
        (
            NormalizedPackageDraft(
                record, dict(primary.payloads) | {"runtime/ruleset.yaml": payload}, 4
            ),
            reference,
        )
    )


def test_actual_archive_paid_mirror_cast_and_public_hit_survive_reopen_and_retry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    monkeypatch.setattr(public_casting, "ROOT", PARENT)

    def public_case(spell):
        state, data, params = _spell_case(spell)
        state[f"{SRD}.state"] = {
            "lasting_tick": 0,
            "turn_effects": [],
            "spatial_scene": _scene(
                _entity("hero", [0, 0, 0], participant_ref="hero"),
                _entity("mage", [5, 0, 0], participant_ref="mage"),
                _entity("guard", [5, 5, 0], participant_ref="guard"),
            ),
        }
        return state, data, params

    monkeypatch.setattr(public_casting, "_spell_case", public_case)
    database = tmp_path / "mirror.db"
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "ray_of_frost")
        prepared, _ = _act(
            owner,
            "prepare_srd_creature",
            creature_id="mage",
            name="Mirror Mage",
            source="Synthetic NPC: AC13, HP1000, Speed30, Intelligence+3, attack+7, DC15.",
            armor_class=13,
            hit_points=1000,
            speed=30,
            initiative_modifier=0,
            size_rank=2,
            constitution_save=0,
            spellcasting_ability="intelligence",
            spellcasting_modifier=3,
            spell_attack_bonus=7,
            spell_save_dc=15,
            spells=[{"spell": "mirror_image", "uses_per_day": 2}],
        )
        assert prepared.status is ActionStatus.COMMITTED
        cast, cast_command = _act(
            owner,
            "cast_spell_outside_combat",
            spell="mirror_image",
            caster_state="mage",
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert cast.status is ActionStatus.COMMITTED
        params["target"] = "mage"
        attacked, attack_command = _act(owner, "cast_spell_outside_combat", **params)
        assert attacked.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["mage"]["hp"] == 1000
        source = next(iter(saved.state[f"{SRD}.state"]["illusion_spell_sources"].values()))
        assert source["remaining_duplicates"] == 2
        assert not any(
            r.get("spell") == "ray_of_frost" for r in saved.state[f"{SRD}.state"]["turn_effects"]
        )
        assert "Duplicate hit" in str(attacked.result["result_card"])
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved

        class NoDice(Random):
            def randint(self, _a, _b):
                pytest.fail("A durable Mirror cast or attack retry rolled again")

        monkeypatch.setattr(dice, "_rng", NoDice(1))
        assert reopened.execute_action(cast_command) == cast
        assert reopened.execute_action(attack_command) == attacked
        assert reopened.snapshot("casting") == saved


def test_diverted_ranged_weapon_consumes_real_ammunition_and_ends_saved_hidden_state(monkeypatch):
    state, data, source_id = _armed()
    actor = state[f"{SRD}.entities"]["hero"]
    actor["inventory"].append("longbow")
    actor["ammunition"] = {"arrow": 2}
    actor.update(hidden=True, hidden_dc=10)
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _attack(
        state,
        data,
        target="mage",
        weapon="longbow",
        attack_mode="ranged",
        attack_ability="dexterity",
        wielding_two_handed=True,
    ).result["context"]
    assert result["_mirror_redirected"] is True
    assert state[f"{SRD}.entities"]["hero"]["ammunition"]["arrow"] == 1
    assert state[f"{SRD}.entities"]["hero"]["hidden"] is False
    assert _source(state, source_id)["remaining_duplicates"] == 2
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert all(side in {20, 6} for side in rolls.sides)


def test_real_help_is_consumed_on_diverted_npc_attack_and_saved_duplicates_remain_source_exact(
    monkeypatch,
):
    state, data, source_id = _armed()
    _run(
        "help_srd_attack_action",
        state,
        participant_data=data,
        actor="hero",
        actor_state="hero",
        ally_state="guard",
        target_state="mage",
        target_distance=5,
    )
    state[f"{SRD}.combat"].update(
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        normal_action_spent=False,
        attacks_remaining=0,
    )
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _npc_attack(
        state,
        "Spear",
        target="mage",
        use_multiattack=False,
    ).result["context"]
    assert result["_mirror_redirected"] is True
    assert state[f"{SRD}.entities"]["guard"].get("help_source") is None
    assert rolls.sides == [20, 20, 6, 6, 6]
    assert _source(state, source_id)["remaining_duplicates"] == 2
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000


def test_actual_clock_expiry_restores_creature_hit_damage_without_duplicate_dice(monkeypatch):
    state, data, source_id = _armed()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True)
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _attack(state, data, target="mage").result["context"]
    assert result["_mirror_redirected"] is False
    assert result["_mirror_dice"] == []
    assert state[f"{SRD}.entities"]["mage"]["hp"] < 1000
    assert _source(state, source_id)["remaining_duplicates"] == 3
    assert rolls.sides == [20, 6, 6]
