"""True Strike pays once and reuses the authentic weapon roll and one typed HP packet."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_invisibility_cast_consumers import _tools as invisibility_tools
from scripts.srd_full.srd_mirror_image_consumers import _weapon
from scripts.srd_full.srd_scene_hand_occupancy import extend_runtime as extend_hands
from scripts.srd_full.srd_scene_sight_origins import bind_runtime_consumers as bind_sight_origins
from scripts.srd_full.srd_spell_invisibility_timing import bind_runtime_consumers as bind_invisible_timing
from scripts.srd_full.srd_true_strike import PROFILE, WEAPON_NODE, bind_runtime_consumers, schema

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_attack_budget import _attack
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import SOURCE, _mage_spec, _source_runtime
from tests.srd_full.test_srd_spell_illumination import _case as _source_case
from tests.srd_full.test_srd_spell_wave3_information import _entity, _wall


@lru_cache
def _dependency_runtime():
    parent = deepcopy(_source_runtime()[0])
    parent["tools"]["resolve_combat_attack"] = _weapon(
        parent["tools"]["resolve_combat_attack"], "actor_state", "target"
    )
    parent["tools"]["end_srd_invisibility_spell_source"] = invisibility_tools(parent)[
        "end_srd_invisibility_spell_source"
    ]
    text = yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False)
    text = bind_invisible_timing(bind_sight_origins(extend_hands(text)))
    return yaml.load(text, Loader=yaml.CSafeLoader), text.encode()


@lru_cache
def _runtime():
    parent = deepcopy(_dependency_runtime()[0])
    parent["definitions"]["spells"]["true_strike"] = deepcopy(PROFILE)
    parent["definition_row_schemas"]["definitions.spells"]["variants"]["true_strike"] = schema()
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast_internal",
        "begin_srd_spell_action",
    ):
        next(p for p in parent["tools"][name]["parameters"] if p["name"] == "spell")[
            "enum_values"
        ].append("true_strike")
    text = yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False)
    text = bind_runtime_consumers(text)
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    assert runtime["tools"]["d20_test"] == parent["tools"]["d20_test"]
    assert (
        runtime["tools"]["resolve_combat_attack"]["parameters"]
        == (parent["tools"]["resolve_combat_attack"]["parameters"])
    )
    return runtime, text.encode()


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


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    actual = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


class Rolls(Random):
    def __init__(self, attack=10, damage=3):
        super().__init__(0)
        self.attack, self.damage = attack, damage
        self.sides = []

    def randint(self, _low, high):
        self.sides.append(high)
        return self.attack if high == 20 else min(high, self.damage)


def _case(level=5, choice="weapon", combat=True):
    state, data, params = _source_case("true_strike")
    data.update(
        level=level,
        classes=[{"id": "wizard", "level": level}],
        weapon_proficiencies=["quarterstaff"],
    )
    data["abilities"].update(strength=8, dexterity=10, intelligence=18)
    actor = state[f"{SRD}.entities"]["hero"]
    actor.update(creature_type="humanoid", held_items=["quarterstaff"])
    actor["inventory"].append("quarterstaff")
    actor["spell_materials"] = [
        {
            "id": "material_weapon",
            "inventory_item": "quarterstaff",
            "kind": "specific",
            "components": ["true_strike.1"],
            "quantity": 1,
            "value_cp": 1,
        }
    ]
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        row["metadata"]["lighting"] = "bright"
        if row["id"] not in {"hero", "guard"}:
            row["position"] = [100, 100, 0]
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0, armor_class=12)
    _run(
        "record_srd_scene_hand_occupancy",
        state,
        actor_state="hero",
        hand_capacity=2,
        holdings=[{"inventory_item": "quarterstaff", "hand_indices": [0]}],
    )
    state[f"{SRD}.combat"].update(
        encounter_active=combat,
        turn_active=combat,
        phase="turn_active" if combat else "exploration",
        active_actor="hero" if combat else None,
        current_actor="hero" if combat else None,
        action_available=combat,
        normal_action_spent=False,
        attacks_remaining=0,
    )
    params.update(
        material_item_ids=["material_weapon"],
        true_strike_weapon="quarterstaff",
        true_strike_damage_choice=choice,
    )
    return state, data, params


def _cast(state, data, params, *, exploration=False):
    return _run(
        "cast_spell_outside_combat" if exploration else "cast_spell",
        state,
        participant_data=data,
        **params,
    ).result


@pytest.mark.parametrize(
    "level,choice,dice_count", [(1, "weapon", 0), (5, "weapon", 1), (17, "radiant", 3)]
)
def test_paid_cast_uses_actual_ability_proficiency_one_roll_and_one_action(
    monkeypatch, level, choice, dice_count
):
    state, data, params = _case(level, choice)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _cast(state, data, params)
    context = result["context"]
    assert context["_ability_modifier"] == 4
    assert context["_weapon_proficiency_modifier"] == 2 + (level - 1) // 4
    assert context["_attack_outcome"] == "hit"
    assert rolls.sides == [20, 6] + [6] * dice_count
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - (7 + dice_count * 3)
    combat = state[f"{SRD}.combat"]
    assert combat["action_available"] is False and combat["attacks_remaining"] == 0
    assert state[f"{SRD}.entities"]["hero"]["light_attack_source_weapons"] == []
    assert result["result_card"]["title"] == "True Strike"
    assert state[f"{SRD}.state"]["spell_source_serial"] == 1
    assert state[f"{SRD}.state"]["turn_effects"] == []


@pytest.mark.parametrize("problem", ["proficiency", "material", "holding", "range"])
def test_actual_eligibility_rejection_is_atomic_before_roll(monkeypatch, problem):
    state, data, params = _case()
    if problem == "proficiency":
        data["weapon_proficiencies"] = []
    elif problem == "material":
        state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["inventory_item"] = "spear"
    elif problem == "holding":
        state[f"{SRD}.entities"]["hero"]["held_items"] = []
    else:
        next(
            row
            for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if row["id"] == "guard"
        )["position"] = [10, 0, 0]
    before = deepcopy(state)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    with pytest.raises(RulesetPreconditionRejected):
        _cast(state, data, params)
    assert state == before and rolls.sides == []


def test_default_weapon_path_keeps_native_attack_budget_and_card(monkeypatch):
    state, data, _ = _case()
    rolls = Rolls(attack=1)
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _attack(state, data, weapon="quarterstaff").result
    assert result["context"]["_ability_modifier"] == -1
    assert rolls.sides == [20]
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert result["result_card"]["title"] == "Attack"
    public = _tool_schema(_runtime()[0]["tools"]["resolve_combat_attack"])
    assert "_true_strike_context" not in public["properties"]
    assert _runtime()[0]["tools"][WEAPON_NODE]["metadata"]["category"] == "internal"


@pytest.mark.parametrize(
    "choice,types,expected",
    [
        ("radiant", ["radiant"], 5),
        ("weapon", ["bludgeoning"], 6),
    ],
)
def test_matching_type_is_mitigated_once_with_one_hp_packet(monkeypatch, choice, types, expected):
    state, data, params = _case(choice=choice)
    state[f"{SRD}.entities"]["guard"].update(damage_resistances=types, temp_hp=2)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _cast(state, data, params)
    context = result["context"]
    assert context["_damage_applied"] == expected
    assert context["_damage_absorbed"] == 2
    assert context["_damage_hp_lost"] == expected - 2
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - (expected - 2)
    assert rolls.sides == [20, 6, 6]


@pytest.mark.parametrize("attack,expected", [(1, "miss"), (20, "critical_hit")])
def test_native_one_twenty_and_critical_double_only_real_damage_dice(monkeypatch, attack, expected):
    state, data, params = _case()
    rolls = Rolls(attack=attack)
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _cast(state, data, params)
    assert result["context"]["_attack_outcome"] == expected
    assert rolls.sides == ([20] if attack == 1 else [20, 6, 6, 6, 6])
    assert state[f"{SRD}.entities"]["guard"]["hp"] == (1000 if attack == 1 else 984)


def test_repeated_emission_and_paid_cast_stay_within_unchanged_budget(monkeypatch):
    from sylvia_gm.runtime._types import ExecutionBudget

    runtime, encoded = _runtime()
    assert yaml.load(bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader) == runtime
    state, data, params = _case()
    monkeypatch.setattr(dice, "_rng", Rolls())
    budgets = []
    actual = ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        actual(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    _cast(state, data, params)
    assert all(b.max_operations == 10000 for b in budgets)
    print("Actual paid True Strike operations:", max(b.operations for b in budgets))

    def nodes(value):
        if isinstance(value, dict):
            return 1 + sum(nodes(k) + nodes(v) for k, v in value.items())
        if isinstance(value, list):
            return 1 + sum(map(nodes, value))
        return 1

    print("True Strike expanded-node delta:", nodes(runtime) - nodes(_dependency_runtime()[0]))


@lru_cache
def _archive():
    runtime, encoded = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    names = {
        "cast_spell",
        "cast_spell_outside_combat",
        "resolve_combat_attack",
        "begin_srd_spell_action",
        "record_srd_scene_hand_occupancy",
    }
    for name in names:
        record["tools"] = [row for row in record["tools"] if row["name"] != name]
        record["tools"].append(
            {
                "name": name,
                "capability": "dnd.srd.5.2.1.spells",
                "input_schema": _tool_schema(runtime["tools"][name]),
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
            }
        )
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    content = json.loads(payloads["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "true-strike-paid-weapon-attack",
            "kind": "ruleset_runtime",
            "family": "spells",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "tools": sorted(names),
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["paid-true-strike-real-weapon-one-roll-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_actual_public_paid_attack_reopens_and_retry_does_not_repeat_payment_roll_or_hp(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case(combat=False))
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    database = tmp_path / "true-strike.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner,
            "true_strike",
            target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0},
        )
        receipt, command = _act(owner, "cast_spell_outside_combat", **params)
        saved = owner.snapshot("casting")
        assert receipt.status is ActionStatus.COMMITTED
        assert rolls.sides == [20, 6, 6]
        assert saved.state[f"{SRD}.state"]["spell_source_serial"] == 1
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 990
        faces = deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == saved and rolls.sides == faces


@pytest.mark.parametrize(
    "weapon,expected,sides",
    [
        ("quarterstaff", 2, [20, 6, 8, 6]),
        ("blowgun", 0, [20, 6, 8]),
    ],
)
def test_actual_ray_penalty_is_allocated_once_across_weapon_and_radiant(
    monkeypatch, weapon, expected, sides
):
    from tests.srd_full.test_srd_spell_damage_consumers import _enfeeble

    state, data, params = _case(combat=False)
    if weapon == "blowgun":
        actor = state[f"{SRD}.entities"]["hero"]
        actor["inventory"].append(weapon)
        actor["held_items"] = [weapon]
        actor["spell_materials"][0]["inventory_item"] = weapon
        definition = _runtime()[0]["definitions"]["weapons"][weapon]
        actor["ammunition"][definition["ammunition_type"]] = 2
        data["weapon_proficiencies"] = [weapon]
        params.update(true_strike_weapon=weapon, true_strike_attack_mode="ranged")
        next(
            row
            for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if row["id"] == "guard"
        )["position"] = [10, 0, 0]
        _run(
            "record_srd_scene_hand_occupancy",
            state,
            actor_state="hero",
            hand_capacity=2,
            holdings=[{"inventory_item": weapon, "hand_indices": [0]}],
        )
    monkeypatch.setattr(dice, "_rng", Rolls())
    _enfeeble(state, data)
    assert state[f"{SRD}.entities"]["hero"]["spell_ray_of_enfeeblement"] is True

    class RayRolls(Rolls):
        def randint(self, low, high):
            result = super().randint(low, high)
            return 8 if high == 8 else result

    rolls = RayRolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _cast(state, data, params, exploration=True)
    assert result["context"]["_actual_weapon_attack_ability"] == "intelligence"
    assert rolls.sides == sides
    assert result["context"]["_damage_applied"] == expected
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - expected
    if weapon == "blowgun":
        assert state[f"{SRD}.entities"]["hero"]["ammunition"][definition["ammunition_type"]] == 1


def test_actual_mirror_diverts_before_all_true_strike_damage(monkeypatch):
    state, data, params = _case(combat=False)
    _run("prepare_srd_creature", state, **_mage_spec())
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(row for row in scene["entities"] if row["id"] == "guard")["position"] = [100, 0, 0]
    scene["entities"].append(
        _entity(
            "mage",
            [5, 0, 0],
            participant_ref="mage",
            creature_type="humanoid",
            plane="material",
            lighting="bright",
        )
    )
    params["target"] = "mage"
    _run(
        "cast_spell_outside_combat",
        state,
        caster_state="mage",
        spell="mirror_image",
        target="mage",
        verbal_component_available=True,
        somatic_component_available=True,
        target_distance=0,
    )
    rolls = Rolls(attack=10, damage=6)
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _cast(state, data, params, exploration=True)
    assert result["context"]["_mirror_redirected"] is True
    assert rolls.sides == [20, 6, 6, 6]
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert result["context"]["_damage_applied"] == 0


def test_actual_recorded_eye_qualifies_cover_from_current_body_and_keeps_anchor_range(monkeypatch):
    from sylvia_gm.runtime import _effect_executor

    state, data, params = _case()
    wall = _wall(1)
    wall["volume"] = {"kind": "box", "min": [2, -10, -1], "max": [3, 10, 1]}
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [wall]
    for observer in ("hero", "guard"):
        _run("record_srd_scene_sight_origin", state, observer_state=observer, offset=[0, 0, 5])
    origins = []
    samples = []
    actual = _effect_executor._execute_effect

    def observe(effect, context, tool_executor=None):
        if effect.get("store_as") == "_true_strike_exposure":
            origins.append(deepcopy(context.vars["_sight_origin"]))
            assert effect["spatial_query"]["query"]["observer"]["point"] == "{_sight_origin}"
        actual(effect, context, tool_executor)
        if effect.get("store_as") == "_true_strike_exposure":
            samples.append(context.vars["_true_strike_exposure"]["visible_samples"])

    monkeypatch.setattr(_effect_executor, "_execute_effect", observe)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _cast(state, data, params)
    context = result["context"]
    assert origins == [[0, 0, 5]]
    assert samples == [9]
    assert context["_true_strike_cover_bonus"] == 0
    assert context["_true_strike_distance"] == 5
    assert context["_attack_outcome"] == "hit" and rolls.sides == [20, 6, 6]


@pytest.mark.parametrize(
    "spell,native,remains",
    [
        ("invisibility", False, False),
        ("greater_invisibility", False, True),
        ("invisibility", True, True),
    ],
)
def test_real_invisible_source_grants_attack_advantage_and_ends_after_a_missed_roll(
    monkeypatch, spell, native, remains
):
    from sylvia_gm.runtime import _effect_executor
    from tests.srd_full.test_srd_spell_result_cards import _casting_headers

    state, data, params = _case(combat=False)
    spec = _mage_spec()
    assert spell in {row["spell"] for row in spec["spells"]}
    _run("prepare_srd_creature", state, **spec)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [-5, 0, 0], participant_ref="mage", creature_type="humanoid")
    )
    mage = state[f"{SRD}.entities"]["mage"]
    mage["spell_materials"] = [
        {
            "id": f"invisible_material_{i}",
            "inventory_item": f"invisible_supply_{i}",
            "kind": "specific",
            "components": [part["id"]],
            "quantity": part["quantity"],
            "value_cp": part["minimum_value_cp"],
        }
        for i, part in enumerate(_casting_headers()[spell]["material_parts"])
    ]
    mage["inventory"] = [part["inventory_item"] for part in mage["spell_materials"]]
    hero = state[f"{SRD}.entities"]["hero"]
    if native:
        hero["conditions"].append("invisible")
    _run(
        "cast_spell_outside_combat",
        state,
        caster_state="mage",
        spell=spell,
        target="hero",
        targets=["hero"],
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        material_item_ids=[part["id"] for part in mage["spell_materials"]],
    )
    seen_at_roll = []
    ending_events = []
    actual = _effect_executor._execute_effect

    def observe(effect, context, tool_executor=None):
        if effect.get("dice", {}).get("expression") == "1d20":
            seen_at_roll.append("invisible" in context.vars["actor_state"]["conditions"])
        if effect.get("call") == "end_srd_invisibility_for_actor":
            ending_events.append(effect["params"]["event"])
        actual(effect, context, tool_executor)

    monkeypatch.setattr(_effect_executor, "_execute_effect", observe)
    rolls = Rolls(attack=1)
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _cast(state, data, params, exploration=True)
    assert seen_at_roll == [True] and rolls.sides == [20, 20]
    assert ending_events.count("attack_roll") == 1
    assert result["context"]["_attack_outcome"] == "miss"
    assert ("invisible" in state[f"{SRD}.entities"]["hero"]["conditions"]) is remains
