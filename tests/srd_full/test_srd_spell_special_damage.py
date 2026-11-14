"""Special dice and selected rays execute through real public spell and timed owners."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_actor_spell_modifiers import _emit, refresh_exploration_runtime
from scripts.srd_full.srd_cone_qualification import extend_runtime as extend_cone
from scripts.srd_full.srd_d20_normalization import normalize_runtime as normalize_d20
from scripts.srd_full.srd_spell_damage_consumers import extend_runtime as extend_damage
from scripts.srd_full.srd_spell_minor_world import bind_d20_runtime
from scripts.srd_full.srd_spell_minor_world import extend_runtime as extend_minor_world
from scripts.srd_full.srd_spell_special_damage import extend_runtime
from scripts.srd_full.srd_spell_summoning import _area_cover_tools
from scripts.srd_full.srd_spell_travel import _tools as travel_tools
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cases
from tests.srd_full.test_srd_spell_damage_consumers import _enfeeble, _grant, _record_rolls
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene, _wall

ROOT = Path(__file__).resolve().parents[2]
SRD = cases.SRD


@pytest.fixture(scope="module")
def special_runtime():
    text = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    text = _replace_tools(text, _area_cover_tools())
    text = _replace_tools(
        text,
        {
            n: t
            for n, t in travel_tools().items()
            if n
            in {
                "resolve_srd_travel_placement",
                "move_srd_travel_subject",
            }
        },
    )
    text = normalize_d20(
        bind_d20_runtime(extend_damage(extend_runtime(extend_cone(extend_minor_world(text)))))
    )
    assert normalize_d20(bind_d20_runtime(extend_damage(extend_runtime(text)))) == text
    text = refresh_exploration_runtime(text)
    runtime = _load_runtime(text)
    errors = validate_ruleset_data(runtime)
    assert not errors, errors
    profiles = yaml.safe_load(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics.yaml").read_text(),
    )["spells"]
    names = ("sorcerous_burst", "prismatic_spray")
    runtime["definitions"]["spells"].update({n: profiles[n]["definition"] for n in names})
    changed = {}
    for name, tool in runtime["tools"].items():
        if name not in {
            "cast_spell",
            "cast_spell_outside_combat",
            "begin_srd_spell_cast_internal",
            "begin_srd_spell_action",
        }:
            continue
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = list(dict.fromkeys([*parameter["enum_values"], *names]))
                changed[name] = tool
    text = _emit(text, changed)
    start = text.index("    # BEGIN GENERATED SPELL DEFINITIONS")
    end = text.index("    # END GENERATED SPELL DEFINITIONS", start)
    rows = yaml.safe_dump(runtime["definitions"]["spells"], sort_keys=False)
    text = (
        text[:start]
        + "    # BEGIN GENERATED SPELL DEFINITIONS\n"
        + "".join("    " + line for line in rows.splitlines(True))
        + text[end:]
    )
    return text.encode(), runtime["definitions"]["spells"]


@pytest.fixture(autouse=True)
def compiled_special(monkeypatch, special_runtime):
    encoded, definitions = special_runtime
    runtime = _load_runtime(encoded)

    @lru_cache
    def mechanic(name):
        schema = budget._tool_schema(runtime["tools"][name], tool_name=name)
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="a" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.action-economy",
                input_schema=owned_payload(schema),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    monkeypatch.setattr(cases, "_spell_definitions", lambda: definitions)
    spell_case = cases._spell_case

    def actual_scene_case(spell):
        state, data, params = spell_case(spell)
        if spell == "prismatic_spray":
            state[f"{SRD}.entities"]["hero"]["creature_type"] = "humanoid"
            state[f"{SRD}.state"]["spatial_scene"] = _scene(
                _entity("hero", [0, 0, 0], participant_ref="hero", creature_type="humanoid"),
                _entity("guard", [20, 0, 0], participant_ref="guard", creature_type="humanoid"),
                _entity("other", [-200, 0, 0], participant_ref="other", creature_type="humanoid"),
            )
        return state, data, params

    monkeypatch.setattr(cases, "_spell_case", actual_scene_case)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


class ActualDiceSequence:
    """Supply reproducible random faces to the actual dice owner, never tool outcomes."""

    def __init__(self, d8=(), d20=10, d6=()):
        self.d8 = iter(d8)
        self.d6 = iter(d6)
        self.d20 = d20

    def randint(self, low, high):
        assert low == 1
        if high == 8:
            return next(self.d8, 8)
        if high == 6:
            return next(self.d6, 1)
        return self.d20 if high == 20 else 1


@pytest.mark.parametrize("critical,extra", [(False, True), (True, True), (False, False)])
def test_public_sorcerous_burst_limits_all_added_dice_by_actual_casting_modifier(
    monkeypatch,
    critical,
    extra,
):
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d20=20 if critical else 10))
    state, data, parameters = cases._spell_case("sorcerous_burst")
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=0)
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"selected_damage_type": "psychic", "sorcerous_burst_extra_dice": extra}),
    ).result
    roll = result["context"]["_spell_damage"]
    initial = 8 if critical else 4
    assert roll["count"] == initial
    assert len(roll["results"]) == initial + (4 if extra else 0)
    assert roll["total"] == 8 * len(roll["results"])
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - roll["total"]


@pytest.mark.parametrize("success", [False, True])
def test_public_prismatic_special_retains_rerolls_and_one_shared_save(monkeypatch, success):
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[8, 8, 1, 2]))
    state, data, parameters = cases._spell_case("prismatic_spray")
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=0)
    guard["saving_throw_modifiers"]["dexterity"] = 100 if success else -100
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"targets": ["guard"]}),
    ).result
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - (12 if success else 24)
    text = str(result["result_card"]["details"])
    assert "red" in text and "orange" in text
    assert "ray rolls 8, 8, 1, 2" in text
    assert "success" in text if success else "failure" in text


def test_public_prismatic_uses_actual_cone_and_physical_cover(monkeypatch):
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[1], d20=13))
    state, data, parameters = cases._spell_case("prismatic_spray")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["metadata"]["cover_from"] = {"hero": "three_quarters"}
    scene["entities"][2]["position"] = [20, 5, 0]
    scene["entities"][2]["metadata"]["cover_from"] = {"hero": "total"}
    scene["geometry"] = [_wall(1)]
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=0)
    guard["saving_throw_modifiers"]["dexterity"] = 0
    other_before = deepcopy(state[f"{SRD}.entities"]["other"])
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"targets": ["other"], "half_cover_targets": ["other"]}),
    ).result
    assert result["context"]["_spell_area_target_count"] == 1
    assert result["context"]["_spell_area_last_save_outcome"] == "success"
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 994
    assert state[f"{SRD}.entities"]["other"] == other_before
    assert [r["label"] for r in result["result_card"]["details"]] == ["Guard"]


def test_prismatic_damaging_colors_share_one_actual_roll_and_source_penalty(monkeypatch):
    state, data, parameters = cases._spell_case("prismatic_spray")
    state[f"{SRD}.combat"]["encounter_active"] = False
    state[f"{SRD}.combat"]["turn_active"] = False
    _enfeeble(state, data)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][2]["position"] = [20, 5, 0]
    for identity in ("guard", "other"):
        target = state[f"{SRD}.entities"][identity]
        target.update(hp=1000, max_hp=1000, temp_hp=0)
        target["saving_throw_modifiers"]["dexterity"] = -100
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[1, 8, 3, 5, 8]))
    rolls = _record_rolls(monkeypatch)
    result = budget._run(
        "cast_spell_outside_combat", state, participant_data=data, **parameters
    ).result
    (damage_roll,) = [r for r in rolls if r.sides == 6]
    assert damage_roll.count == 12 and damage_roll.total == 12
    assert [r.total for r in rolls if r.sides == 8] == [1, 8, 3, 5, 8]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 996
    assert state[f"{SRD}.entities"]["other"]["hp"] == 992
    assert len(result["result_card"]["details"]) == 2


def test_prismatic_condition_only_colors_do_not_roll_unused_damage(monkeypatch):
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[6]))
    state, data, parameters = cases._spell_case("prismatic_spray")
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = -100
    rolls = _record_rolls(monkeypatch)
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert not [r for r in rolls if r.sides == 6]
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]


@pytest.mark.parametrize(
    "rays,extra,immunities,expected",
    [
        ([1], 1, [], 7),
        ([8, 1, 1], 1, [], 13),
        ([8, 1, 2], 3, ["fire"], 8),
        ([8, 1, 2], 3, ["fire", "acid"], 1),
    ],
)
def test_actual_prismatic_odd_save_packet_carries_remainder_through_rays_and_curse(
    monkeypatch, rays, extra, immunities, expected
):
    state, data, parameters = cases._spell_case("prismatic_spray")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    guard = state[f"{SRD}.entities"]["guard"]
    guard["saving_throw_modifiers"]["wisdom"] = -100
    curse = _grant(state, data, "bestow_curse")
    curse.update(curse_mode="damage", slot_resource="spell_slot_5", targets=["guard"])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [2, 0, 0]
    budget._run("cast_spell_outside_combat", state, participant_data=data, **curse)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [20, 0, 0]
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=0, damage_immunities=immunities)
    guard["saving_throw_modifiers"]["dexterity"] = 100
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[*rays, extra], d6=[2]))
    rolls = _record_rolls(monkeypatch)
    result = budget._run(
        "cast_spell_outside_combat", state, participant_data=data, **parameters
    ).result
    (base,) = [r for r in rolls if r.sides == 6]
    assert base.count == 12 and base.total == 13
    assert [r.total for r in rolls if r.sides == 8] == [*rays, extra]
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["last_curse_damage_roll"]["total"] == extra
    assert guard["hp"] == 1000 - expected
    assert result["context"]["_spell_area_last_damage_amount"] == expected


@pytest.mark.parametrize("success", [True, False])
def test_public_prismatic_complete64_scene_creatures_and_packets(monkeypatch, success):
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[1] * 64))
    state, data, parameters = cases._spell_case("prismatic_spray")
    entities = state[f"{SRD}.entities"]
    identities = [f"prism-target-{i}" for i in range(64)]
    for identity in identities:
        entities[identity] = deepcopy(entities["guard"])
        entities[identity].update(name=identity, hp=1000, max_hp=1000, temp_hp=0)
        entities[identity]["saving_throw_modifiers"]["dexterity"] = 100 if success else -100
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] = scene["entities"][:1] + [
        _entity(i, [20, 0, 0], participant_ref=i, creature_type="humanoid") for i in identities
    ]
    parameters.pop("targets", None)
    parameters.pop("target", None)
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **parameters,
    ).result
    assert result["context"]["_spell_area_target_count"] == 64
    assert len(result["result_card"]["details"]) == 64
    assert all(entities[i]["hp"] == 1000 for i in identities)
    expected = 994 if success else 988
    assert all(state[f"{SRD}.entities"][i]["hp"] == expected for i in identities)


@pytest.mark.parametrize(
    "monk_level,success,expected",
    [(7, True, 0), (7, False, 10), (6, False, 19)],
)
def test_prismatic_uses_earned_evasion_before_actual_typed_packet(
    monkeypatch,
    monk_level,
    success,
    expected,
):
    state, data, parameters = cases._spell_case("prismatic_spray")
    data.update(
        level=20,
        classes=[
            {"id": "wizard", "level": 20 - monk_level},
            {"id": "monk", "level": monk_level},
        ],
    )
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=1000, max_hp=1000, temp_hp=0, damage_resistances=["fire"])
    hero["saving_throw_modifiers"] = {"dexterity": 100 if success else -100}
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [-200, 0, 0]
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[8, 1, 2], d6=[2]))
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"prismatic_include_origin": True}),
    ).result
    assert result["context"]["_spell_area_target_count"] == 1
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 1000 - expected
    assert result["context"]["_spell_area_last_save_outcome"] == (
        "success" if success else "failure"
    )


@pytest.mark.parametrize("ray,condition", [(6, "restrained"), (7, "blinded")])
def test_public_prismatic_condition_ray_installs_actual_instantaneous_source(
    monkeypatch,
    ray,
    condition,
):
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[ray]))
    state, data, parameters = cases._spell_case("prismatic_spray")
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = -100
    budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"targets": ["guard"]}),
    )
    target = state[f"{SRD}.entities"]["guard"]
    assert condition in target["conditions"]
    (source_id,) = target["timed_condition_source_ids"]
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["prismatic_ray"] == ray
    assert source["caster_state_id"] == "hero" and source["concentration_sequence"] == 0
    assert source["expires_tick"] is None and source["dispel_ongoing"] is False
    assert source["progressive_successes"] == source["progressive_failures"] == 0


def _condition_cast(monkeypatch, ray):
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[ray]))
    state, data, parameters = cases._spell_case("prismatic_spray")
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = -100
    budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"targets": ["guard"]}),
    )
    return state


def _begin(state, actor):
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor=actor)
    return budget._run("begin_combat_turn", state, actor=actor)


@pytest.mark.parametrize("successes", [True, False])
def test_actual_indigo_turns_count_mixed_saves_and_restore_exact_petrification(
    monkeypatch, successes
):
    state = _condition_cast(monkeypatch, 6)
    target = state[f"{SRD}.entities"]["guard"]
    (source_id,) = target["timed_condition_source_ids"]
    for success in (
        [True, False, True, False, True] if successes else [False, True, False, True, False]
    ):
        target["saving_throw_modifiers"]["constitution"] = 100 if success else -100
        _begin(state, "guard")
        result = budget._run("end_combat_turn", state, actor="guard").result
        assert "Indigo" in str(result["result_card"])
        target = state[f"{SRD}.entities"]["guard"]
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert (source["progressive_successes"], source["progressive_failures"]) == (
        (3, 2) if successes else (2, 3)
    )
    assert "restrained" not in target["conditions"]
    if successes:
        assert source["installed"] is False
    else:
        assert "petrified" in target["conditions"] and source["installed"] is True
        budget._run(
            "install_srd_timed_condition_source",
            state,
            target_state="guard",
            caster_state="hero",
            lasting_state="state",
            spell="blindness_deafness",
            condition="deafened",
            save_dc=10,
            duration_rounds=100,
            repeat_save_timing="none",
            save_ability="constitution",
            concentration_sequence=0,
        )
        budget._run("remove_srd_condition", state, target="guard", condition="petrified")
        assert "petrified" not in state[f"{SRD}.entities"]["guard"]["conditions"]
        assert "deafened" in state[f"{SRD}.entities"]["guard"]["conditions"]
        assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is False


@pytest.mark.parametrize("success", [True, False])
def test_violet_actual_next_caster_turn_ends_blind_and_admits_failed_plane_choice(
    monkeypatch, success
):
    state = _condition_cast(monkeypatch, 7)
    target = state[f"{SRD}.entities"]["guard"]
    target["saving_throw_modifiers"]["wisdom"] = 100 if success else -100
    _begin(state, "guard")
    assert "blinded" in state[f"{SRD}.entities"]["guard"]["conditions"]
    budget._run("end_combat_turn", state, actor="guard")
    result = _begin(state, "hero").result
    assert "Violet" in str(result["result_card"])
    assert "blinded" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    if success:
        assert not state[f"{SRD}.state"].get("prismatic_violet_pending")
    else:
        (event_id,) = state[f"{SRD}.state"]["prismatic_violet_pending"]
        state[f"{SRD}.state"]["spatial_scene"] = _scene(
            _entity("guard", [5, 0, 0], participant_ref="guard"),
            _entity("violet_anchor", [50, 0, 0], plane="astral"),
        )
        budget._run(
            "resolve_srd_prismatic_violet_destination",
            state,
            event_id=event_id,
            destination_anchor="violet_anchor",
        )
        scene = state[f"{SRD}.state"]["spatial_scene"]
        mover = next(e for e in scene["entities"] if e["id"] == "guard")
        assert mover["metadata"]["plane"] == "astral" and mover["position"] == [49.2, 0, 0]
        assert state[f"{SRD}.state"]["prismatic_violet_pending"][event_id]["pending"] is False


@pytest.mark.parametrize("ray,condition", [(6, "restrained"), (7, "blinded")])
def test_condition_immunity_suppresses_projection_without_discarding_color_source(
    monkeypatch, ray, condition
):
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[ray]))
    state, data, parameters = cases._spell_case("prismatic_spray")
    target = state[f"{SRD}.entities"]["guard"]
    target["condition_immunities"] = [condition]
    target["saving_throw_modifiers"]["dexterity"] = -100
    budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"targets": ["guard"]}),
    )
    target = state[f"{SRD}.entities"]["guard"]
    assert condition not in target["conditions"]
    (source_id,) = target["timed_condition_source_ids"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is True


def test_actual_prismatic_rerolls_share_existing_budget_and_reject_atomically(monkeypatch):
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence())
    state, data, parameters = cases._spell_case("prismatic_spray")
    before = deepcopy(state)
    with pytest.raises(PackageRuntimeError):
        budget._run(
            "cast_spell_outside_combat",
            state,
            participant_data=data,
            **(parameters | {"targets": ["guard"]}),
        )
    assert state == before


def _public_recovery(state, spell, **choices):
    supplies, _, parameters = cases._spell_case(spell)
    caster = supplies[f"{SRD}.entities"]["hero"]
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, phase="exploration")
    budget._run(
        "prepare_srd_creature",
        state,
        creature_id="healer",
        name="Recovery caster",
        source="Selected synthetic recovery caster: Wisdom +3, spell attack +7, DC15.",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=2,
        size_rank=2,
        constitution_save=6,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[{"spell": spell, "uses_per_day": 1}],
        inventory=caster["inventory"],
        spell_materials=caster["spell_materials"],
    )
    parameters.pop("caster")
    parameters.pop("slot_resource")
    budget._run(
        "cast_spell_outside_combat", state, **(parameters | {"caster_state": "healer"} | choices)
    )


def test_actual_heal_ends_only_violet_blindness_before_its_saved_wisdom_save(monkeypatch):
    state = _condition_cast(monkeypatch, 7)
    (source_id,) = state[f"{SRD}.entities"]["guard"]["timed_condition_source_ids"]
    _public_recovery(state, "heal")
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["installed"] is True and source["condition"] == "none"
    assert "blinded" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = -100
    result = _begin(state, "hero").result
    assert "Violet" in str(result["result_card"])
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is False
    assert len(state[f"{SRD}.state"]["prismatic_violet_pending"]) == 1


def test_actual_greater_restoration_retires_indigo_petrification(monkeypatch):
    state = _condition_cast(monkeypatch, 6)
    for _ in range(3):
        state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = -100
        _begin(state, "guard")
        budget._run("end_combat_turn", state, actor="guard")
    assert "petrified" in state[f"{SRD}.entities"]["guard"]["conditions"]
    (source_id,) = state[f"{SRD}.entities"]["guard"]["timed_condition_source_ids"]
    _public_recovery(state, "greater_restoration", recovery_choice="petrified")
    assert "petrified" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is False
