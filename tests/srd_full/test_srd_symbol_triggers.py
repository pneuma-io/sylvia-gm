"""Symbol derives its activation, save, light and later turn effects from actual saved sources."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_spell_triggers as owner
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _wall

PARENT = Path(__file__).resolve().parents[2]


@lru_cache
def _runtime():
    payload = (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
    assert runtime["definitions"]["spells"]["symbol"] == owner.PROFILES["symbol"]
    assert "resolve_srd_symbol_boundary" in runtime["tools"]
    return runtime, payload


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
        lambda: owner._runtime()[0]["definitions"]["spells"],
    )


def _symbol(effect="death", **choices):
    state, _, _, identity = owner._cast("symbol", symbol_effect=effect, **choices)
    owner._entry(state, "hero")["position"] = [200, 0, 0]
    state[f"{SRD}.combat"]["turn_sequence"] = 1
    for name, modifier in [("guard", 30), ("other", -30)]:
        state[f"{SRD}.entities"][name]["saving_throw_modifiers"].update(
            constitution=modifier, wisdom=modifier
        )
    return state, identity


def _boundary(state, actor="other", kind="end", **values):
    return _run(
        "resolve_srd_symbol_boundary", state, target_state=actor, boundary_kind=kind, **values
    ).result["context"]


def _advance_rounds(state, rounds):
    """Use real six-second clock boundaries while an active aura can apply another save."""
    for _ in range(rounds):
        _run("advance_srd_lasting_time", state, elapsed_rounds=1)


def test_symbol_paid_source_activation_death_roll_original_dc_and_real_light_expiry():
    state, identity = _symbol()
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_7"] == 2
    assert hero["spell_materials"][0]["quantity"] == 2
    assert owner._source(state)["expires_tick"] is None
    hero["is_alive"] = False
    owner._entry(state, "hero")["metadata"]["plane"] = "ethereal"
    state = json.loads(json.dumps(state))
    before_guard = state[f"{SRD}.entities"]["guard"]["hp"]
    before_other = state[f"{SRD}.entities"]["other"]["hp"]
    fired = owner._event(state, identity, event="open")
    assert fired["_trigger_fired"] is True
    lost_guard = before_guard - state[f"{SRD}.entities"]["guard"]["hp"]
    lost_other = before_other - state[f"{SRD}.entities"]["other"]["hp"]
    assert 10 <= lost_other <= 100 and lost_guard == lost_other // 2
    assert "DC 18" in str(fired["_trigger_details"])
    light = _run(
        "resolve_srd_information_light",
        state,
        point=[65, 0, 0],
        plane="material",
        ambient="darkness",
    ).result["context"]
    assert light["_information_light"] == "dim"
    assert (
        _run(
            "resolve_srd_information_light",
            state,
            point=[65.01, 0, 0],
            plane="material",
            ambient="darkness",
        ).result["context"]["_information_light"]
        == "darkness"
    )
    assert owner._event(state, identity, event="open")["_trigger_fired"] is False
    _advance_rounds(state, 100)
    assert (
        _run(
            "resolve_srd_information_light",
            state,
            point=[6, 0, 0],
            plane="material",
            ambient="darkness",
        ).result["context"]["_information_light"]
        == "darkness"
    )


def test_symbol_real_turn_once_guard_and_actual_entry_geometry():
    state, identity = _symbol()
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=True, turn_active=False, current_actor="other")
    _run("begin_combat_turn", state, actor="other")
    owner._event(state, identity, event="open")
    saved = deepcopy(state[f"{SRD}.entities"]["other"])
    assert _boundary(state)["_symbol_boundary_details"] == []
    assert state[f"{SRD}.entities"]["other"] == saved
    _run("end_combat_turn", state, actor="other")
    combat["current_actor"] = "other"
    _run("begin_combat_turn", state, actor="other")
    assert (
        _boundary(state, kind="entry", previous_position=[5, 6, 0], previous_plane="material")[
            "_symbol_boundary_details"
        ]
        == []
    )
    owner._entry(state, "other")["position"] = [5, 65, 0]
    assert _boundary(state)["_symbol_boundary_details"] == []
    owner._entry(state, "other")["position"] = [5, 5, 0]
    entered = _boundary(
        state, kind="entry", previous_position=[5, 65, 0], previous_plane="material"
    )
    assert "Saving throw" in str(entered["_symbol_boundary_details"])
    assert state[f"{SRD}.entities"]["other"]["hp"] < saved["hp"]
    assert _boundary(state)["_symbol_boundary_details"] == []
    with pytest.raises(RulesetPreconditionRejected, match="previous position"):
        _boundary(state, kind="entry")


@pytest.mark.parametrize(
    "effect,condition,duration",
    [
        ("pain", "incapacitated", 10),
        ("stunning", "stunned", 10),
        ("sleep", "unconscious", 100),
        ("fear", "frightened", 10),
    ],
)
def test_symbol_source_condition_saves_actual_lifetime_and_independent_source(
    effect, condition, duration
):
    state, identity = _symbol(effect)
    owner._event(state, identity, event="open")
    assert condition not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert condition in state[f"{SRD}.entities"]["other"]["conditions"]
    installed = next(
        row
        for row in state[f"{SRD}.state"]["timed_condition_sources"].values()
        if row.get("symbol_effect") == effect
    )
    assert installed["expires_tick"] - state[f"{SRD}.state"]["lasting_tick"] == duration * 2
    _run(
        "move_srd_travel_subject", state, subject="other", destination=[200, 5, 0], plane="material"
    )
    _advance_rounds(state, duration)
    assert condition not in state[f"{SRD}.entities"]["other"]["conditions"]


def test_symbol_discord_later_real_roll_disadvantage_and_communication_qualifier():
    state, identity = _symbol("discord")
    owner._event(state, identity, event="open")
    state = json.loads(json.dumps(state))
    active = _run("check_srd_symbol_afflictions", state, target_state="other").result["context"]
    assert active["_symbol_discord"] and active["_symbol_communication_blocked"]
    for kind, count in [("attack_roll", 2), ("ability_check", 2), ("saving_throw", 1)]:
        result = _run(
            "d20_test",
            state,
            actor_state="other",
            ability="wisdom",
            kind=kind,
            ability_modifier=0,
            proficiency_bonus=0,
            proficiency_factor="none",
            circumstantial_modifier=0,
            target_number=18,
        ).result["context"]
        assert len(result["_d20_test"]["results"]) == count
    _run(
        "move_srd_travel_subject", state, subject="other", destination=[200, 5, 0], plane="material"
    )
    _advance_rounds(state, 10)
    ended = _run("check_srd_symbol_afflictions", state, target_state="other").result["context"]
    assert not ended["_symbol_discord"] and not ended["_symbol_communication_blocked"]


def _fear_roll(state):
    return _run(
        "d20_test",
        state,
        actor_state="other",
        ability="wisdom",
        kind="ability_check",
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=18,
    ).result["context"]


@pytest.mark.parametrize("perception", ["seen", "wall", "blinded", "blindsight", "removed"])
def test_fear_later_roll_uses_actual_glyph_sight_and_missing_anchor(perception):
    state, identity = _symbol("fear")
    owner._event(state, identity, event="open")
    if perception == "wall":
        wall = _wall(1)
        wall["volume"] = {"kind": "box", "min": [4, 2, -5], "max": [6, 3, 10]}
        state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [wall]
    if perception in ("blinded", "blindsight"):
        state[f"{SRD}.entities"]["other"]["conditions"].append("blinded")
        if perception == "blindsight":
            state[f"{SRD}.entities"]["other"]["blindsight_feet"] = 10
    if perception == "removed":
        scene = state[f"{SRD}.state"]["spatial_scene"]
        scene["entities"] = [e for e in scene["entities"] if e["id"] != "orb"]
    saved = json.loads(json.dumps(state))
    result = _fear_roll(saved)
    assert len(result["_d20_test"]["results"]) == (2 if perception in ("seen", "blindsight") else 1)


@pytest.mark.parametrize(
    "projection,visible",
    [
        ("etherealness", True),
        ("blink", True),
        ("expired", False),
        ("unsourced", False),
        ("beyond60", False),
    ],
)
def test_fear_projected_perception_reuses_live_pure_travel_qualifier_without_moving(
    projection, visible
):
    state, identity = _symbol("fear")
    owner._event(state, identity, event="open")
    subject = state[f"{SRD}.entities"]["other"]
    entry = owner._entry(state, "other")
    entry["metadata"]["plane"] = "ethereal"
    current_tick = state[f"{SRD}.state"]["lasting_tick"]
    if projection == "blink":
        subject["travel_blink"] = {
            "spell": "blink",
            "phase": True,
            "return_plane": "material",
            "expires_tick": current_tick + 20,
        }
    elif projection != "unsourced":
        subject["travel_ethereal"] = {
            "spell": "etherealness",
            "return_plane": "material",
            "expires_tick": current_tick if projection == "expired" else current_tick + 20,
        }
    if projection == "beyond60":
        entry["position"] = [5, 61, 0]
    before = deepcopy(entry)
    result = _fear_roll(state)
    assert len(result["_d20_test"]["results"]) == (2 if visible else 1)
    assert owner._entry(state, "other") == before


@pytest.mark.parametrize("anchor_kind,active", [("object", False), ("surface", True)])
def test_symbol_printed_movement_retirement_is_bound_to_the_actual_anchor_kind(anchor_kind, active):
    state, identity = _symbol("pain", trigger_anchor_kind=anchor_kind)
    owner._entry(state, "orb")["position"] = [15.01, 0, 0]
    owner._entry(state, "guard")["position"] = [16, 0, 0]
    assert owner._event(state, identity, event="open")["_trigger_fired"] is active


@pytest.mark.parametrize("ending", ["damage", "shake"])
def test_symbol_sleep_ends_through_real_damage_or_paid_action_with_actual_contact(ending):
    state, identity = _symbol("sleep")
    owner._event(state, identity, event="open")
    target = state[f"{SRD}.entities"]["other"]
    assert "unconscious" in target["conditions"]
    if ending == "damage":
        _run("apply_srd_damage", state, target="other", amount=1, adjustment=0, damage_type="fire")
    else:
        state[f"{SRD}.combat"].update(
            turn_active=True, active_actor="guard", active_actor_can_act=True, action_available=True
        )
        with pytest.raises(RulesetPreconditionRejected, match="actual same-plane contact"):
            _run("shake_srd_symbol_sleeper", state, actor_state="guard", target_state="other")
        owner._entry(state, "guard")["position"] = [5, 4, 0]
        _run("shake_srd_symbol_sleeper", state, actor_state="guard", target_state="other")
        assert state[f"{SRD}.combat"]["action_available"] is False
    assert "unconscious" not in state[f"{SRD}.entities"]["other"]["conditions"]
    assert not any(
        s["installed"] and s.get("symbol_effect") == "sleep"
        for s in state[f"{SRD}.state"]["timed_condition_sources"].values()
    )


def test_actual_public_paid_symbol_activation_cards_saved_reopen_and_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        bundled_packages, "_bundled_srd_root", lambda: PARENT / "rulesets/dnd-srd-5.2.1"
    )
    owner._test_archive.cache_clear()

    def public_case(spell):
        state, data, params = owner._case(spell)
        for name, modifier in [("guard", 30), ("other", -30)]:
            state[f"{SRD}.entities"][name]["saving_throw_modifiers"]["constitution"] = modifier
        return state, data, params

    monkeypatch.setattr(owner.public_casting, "bundled_srd_archive", owner._test_archive)
    monkeypatch.setattr(owner.public_casting, "_spell_case", public_case)
    database = tmp_path / "symbol.db"
    with AuthorityOwner.open(database, "casting-owner") as authority:
        _, _, params = owner.public_casting._create_spell_campaign(authority, "symbol")
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
        identity = cast.result["context"]["_trigger_id"]
        saved_armed = authority.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as authority:
        assert authority.snapshot("casting") == saved_armed
        fired, command = _act(
            authority,
            "resolve_srd_spell_trigger",
            trigger_id=identity,
            event_actor="guard",
            event_kind="open",
            fictional_match=True,
        )
        assert fired.status is ActionStatus.COMMITTED, fired.result
        assert len(fired.result["context"]["_trigger_damage_roll"]["results"]) == 10
        assert "Saving throw" in str(fired.result["result_card"])
        assert "Damage" in str(fired.result["result_card"])
        saved_active = authority.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as authority:
        assert authority.snapshot("casting") == saved_active

        class NoReroll(Random):
            def randint(self, _a, _b):
                pytest.fail("A saved Symbol activation retry rolled again")

        monkeypatch.setattr(dice, "_rng", NoReroll(99))
        assert authority.execute_action(command) == fired
        assert authority.snapshot("casting") == saved_active
