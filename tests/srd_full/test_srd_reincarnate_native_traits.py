"""New-body trait boundaries reuse actual Reincarnate, D20, damage and rest owners."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.srd_reincarnate_native_traits import extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def native_traits_runtime():
    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    text = extend_runtime((ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text())
    assert extend_runtime(text) == text
    assert not validate_ruleset(text)
    validate_content_shape("package_runtime", _load_runtime(text), file="reincarnate-native-traits")
    return _load_runtime(text), text.encode()


@pytest.fixture
def native_traits_owner(monkeypatch, native_traits_runtime):
    import tests.srd_full.test_srd_action_budget as budget

    runtime, encoded = native_traits_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="d" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    budget._tool_schema(runtime["tools"][name], tool_name=name)
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


def _new_body(species, *, old_species="goliath", complete=True):
    """Complete genuine recorded remains through the actual saved species-table choice."""
    state, data, _ = _spell_case("reincarnate")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, active_actor=None)
    state[f"{SRD}.state"]["lasting_tick"] = 0
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(
        is_alive=False,
        hp=0,
        conditions=["dead"],
        creature_type="humanoid",
        species=old_species,
        creature_kind="character",
        damage_resistances=["cold"],
        exhaustion_level=0,
    )
    data.update(
        species=old_species,
        skill_proficiencies=["arcana"],
        background="criminal",
        origin_feats=["alert"],
        size_rank=2,
        saving_throw_proficiencies=["intelligence", "wisdom"],
    )
    _run(
        "record_srd_deceased_body",
        state,
        target_state="hero",
        death_tick=0,
        old_age=False,
        soul_willing=True,
        body_available=True,
        vital_parts_present=True,
        living_creature_type="humanoid",
        name="Hero",
    )
    _run("begin_srd_reincarnation", state, target_state="hero", caster_state="guard")
    grants = deepcopy(data["spellcasting_grants"])
    choices = {
        "species_override": species,
        "skill_proficiencies": data["skill_proficiencies"],
        "origin_feats": data["origin_feats"],
        "spellcasting_grants": grants,
    }
    if species == "gnome":
        grants.append(
            {"source": "gnome_rock", "ability": "wisdom", "spells": ["mending", "prestidigitation"]}
        )
    if species == "elf":
        grants.append(
            {
                "source": "elf_wood",
                "ability": "wisdom",
                "spells": ["druidcraft", "longstrider", "pass_without_trace"],
            }
        )
        choices.update(
            lineage="wood_elf",
            species_skill="perception",
            skill_proficiencies=["arcana", "perception"],
        )
    if not complete:
        return state, data, choices
    result = _run(
        "complete_srd_reincarnation",
        state,
        participant_data=data,
        target_state="hero",
        target_character="hero",
        hit_points=1,
        **choices,
    )
    data = dict(
        next(row.data for row in result.participant_mutations if row.participant_id == "hero")
    )
    return state, data, choices


def _save(state, data, ability, **choices):
    return _run(
        "d20_test",
        state,
        participant_data=data,
        actor="hero",
        actor_state="hero",
        ability=ability,
        kind="saving_throw",
        proficiency_factor="normal" if ability in data["saving_throw_proficiencies"] else "none",
        circumstantial_modifier=0,
        target_number=10,
        **choices,
    ).result["context"]


def test_actual_dwarf_new_body_keeps_other_defense_and_uses_poison_receiving_save(
    native_traits_owner,
):
    state, data, _ = _new_body("dwarf")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["damage_resistances"] == ["cold", "poison"]
    assert hero["reincarnate_native_traits"]["base_resistances"] == ["cold"]
    assert hero["reincarnate_native_traits"]["hp_bonus"] == data["level"]
    assert hero["size_rank"] == data["size_rank"] == 2
    result = _save(state, data, "constitution", save_avoids_condition="poisoned")
    assert result["_effective_advantage"] is True and len(result["_d20_test"]["results"]) == 2
    result = _run(
        "check_srd_target_condition_immunity", state, target_state="hero", condition="poisoned"
    ).result["context"]
    assert result["_source_condition_save_advantage"] is True
    before = state[f"{SRD}.entities"]["hero"]["hp"]
    result = _run(
        "apply_srd_damage", state, target="hero", amount=7, adjustment=0, damage_type="poison"
    ).result["context"]
    assert result["_effective_damage"] == 3
    assert state[f"{SRD}.entities"]["hero"]["hp"] == max(0, before - 3)


def test_actual_gnome_mental_advantage_cancels_native_disadvantage(native_traits_owner):
    state, data, _ = _new_body("gnome")
    assert data["species"] == "gnome" and data["size_rank"] == 1
    result = _save(state, data, "wisdom")
    assert result["_effective_advantage"] is True and len(result["_d20_test"]["results"]) == 2
    result = _save(state, data, "wisdom", disadvantage=True)
    assert result["_effective_advantage"] is result["_effective_disadvantage"] is True
    assert len(result["_d20_test"]["results"]) == 1
    assert _save(state, data, "constitution")["_effective_advantage"] is False
    assert data["spellcasting_grants"][0]["source"] == "druid"
    assert data["spellcasting_grants"][1]["source"] == "gnome_rock"


def test_actual_elf_trance_remains_conscious_and_finishes_at_four_real_hours(native_traits_owner):
    state, data, _ = _new_body("elf")
    assert data["skill_proficiencies"] == ["arcana", "perception"]
    assert state[f"{SRD}.entities"]["hero"]["speed"] == 35
    assert _save(state, data, "wisdom", save_avoids_condition="charmed")["_effective_advantage"]
    _run("begin_srd_rest", state, target_state="hero", kind="long", activity="trance")
    assert "unconscious" not in state[f"{SRD}.entities"]["hero"]["conditions"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=2399)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="four real hours"):
        _run("complete_srd_rest", state, target_state="hero")
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("complete_srd_rest", state, target_state="hero")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["rest_trance_ticks"] == 4800 and hero["rest_sleep_ticks"] == 0
    assert hero["rest_phase"] == "complete" and hero["hp"] == hero["max_hp"]
    assert "unconscious" not in hero["conditions"]


def test_unknown_legacy_species_contribution_rejects_before_new_body_mutation(native_traits_owner):
    state, data, choices = _new_body("dwarf", old_species="human", complete=False)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="former species"):
        _run(
            "complete_srd_reincarnation",
            state,
            participant_data=data,
            target_state="hero",
            target_character="hero",
            hit_points=1,
            **choices,
        )
    assert state == before
