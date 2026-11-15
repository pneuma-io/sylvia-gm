"""Completed roll consumers retain actual cards, source hooks and outcomes with less work."""

import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_d20_normalization import normalize_d20_tool, normalize_runtime
from scripts.srd_full.srd_spell_illumination import _compose_invisibility_roll
from scripts.srd_full.srd_timed_actor_defenses import _set, _when
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_illumination import _case, _cast, _records

ROOT = Path(__file__).resolve().parents[2]


def test_replacing_real_d20_owner_preserves_native_shared_result_cards():
    text = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    # Final emitters may expand aliases; restore the real consumer's identical shared card.
    if "*d20_result_card" not in text:
        actual = yaml.load(text, Loader=yaml.CSafeLoader)["tools"]
        assert (
            actual["study_srd_action"]["metadata"]["result_card"]
            == actual["d20_test"]["metadata"]["result_card"]
        )
        if "&d20_result_card" not in text:
            pattern = r"(?ms)^  d20_test:\n.*?(?=^  [A-Za-z_][^\n]*:\s*|^procedures:)"
            fragment = re.search(pattern, text).group()
            anchored = fragment.replace(
                "      result_card:\n", "      result_card: &d20_result_card\n", 1
            )
            assert anchored != fragment
            text = text.replace(fragment, anchored, 1)
        pattern = r"(?ms)^  study_srd_action:\n.*?(?=^  [A-Za-z_][^\n]*:\s*|^procedures:)"
        fragment = re.search(pattern, text).group()
        shared = re.sub(
            r"(?ms)^      result_card:.*?(?=^      [A-Za-z_]|^    [A-Za-z_]|\Z)",
            "      result_card: *d20_result_card\n",
            fragment,
            count=1,
        )
        assert shared != fragment
        text = text.replace(fragment, shared, 1)
    before = yaml.load(text, Loader=yaml.CSafeLoader)
    assert "*d20_result_card" in text
    changed = deepcopy(before["tools"]["d20_test"])
    changed["description"] += " Source consumers preserve the native result card."
    result = _replace_tools(text, {"d20_test": changed})
    after = yaml.load(result, Loader=yaml.CSafeLoader)
    for name, tool in before["tools"].items():
        assert after["tools"][name] == (changed if name == "d20_test" else tool)


PARAMS = {
    "actor_state": "guard",
    "kind": "saving_throw",
    "ability": "dexterity",
    "ability_modifier": 0,
    "proficiency_bonus": 3,
    "proficiency_factor": "none",
    "circumstantial_modifier": 0,
    "target_number": 10,
}
MATH_FIELDS = (
    "_d20_test",
    "_outcome",
    "_attack_outcome",
    "_adjusted_total",
    "_combined_modifier",
    "_effective_advantage",
    "_effective_disadvantage",
    "_save_foregone",
    "_automatic_failure_reason",
    "_proficiency_modifier",
    "_ability_modifier",
    "_guidance_bonus",
    "_lasting_d20_modifier",
    "_exhaustion_penalty",
)


def _legacy_tool(tool):
    """Retain current hooks and reconstruct the four prior base shapes only for comparison."""
    result = normalize_d20_tool(tool)
    effects = result["effects"]
    rolling = next(e for e in effects if e.get("condition", "").startswith("forego_save == true"))
    title = next(e for e in effects if e.get("condition") == "skill != null" and "on_false" in e)
    index = effects.index(title)
    effects[index : index + 1] = [
        _set("_card_title", "'Ability check'"),
        _when("kind == 'saving_throw'", [_set("_card_title", "'Saving throw'")]),
        _when("kind == 'attack_roll'", [_set("_card_title", "'Attack roll'")]),
        _when("ability != null", [_set("_card_title", "ability")]),
        _when("skill != null", [_set("_card_title", "skill")]),
        _when("tool != null and skill == null", [_set("_card_title", "tool")]),
    ]
    fields = {"_d20_test", "_adjusted_total", "_combined_modifier"}
    defaults = [e for e in rolling["on_true"] if e.get("set", {}).get("field") in fields]
    assert len(defaults) == 3
    rolling["on_true"] = [e for e in rolling["on_true"] if e not in defaults]
    effects[index:index] = defaults
    index = next(
        i
        for i, e in enumerate(effects)
        if e.get("set", {}).get("field") == "_d20_proficiency_scale"
    )
    effects[index : index + 2] = [
        _set("_proficiency_modifier", 0),
        _when(
            "proficiency_factor == 'half'",
            [_set("_proficiency_modifier", "_proficiency_bonus // 2")],
        ),
        _when(
            "proficiency_factor == 'normal'", [_set("_proficiency_modifier", "_proficiency_bonus")]
        ),
        _when(
            "proficiency_factor == 'double'",
            [_set("_proficiency_modifier", "_proficiency_bonus * 2")],
        ),
    ]
    native = next(
        e
        for e in effects
        if any(
            child.get("condition") == "kind == 'ability_check' or kind == 'attack_roll'"
            for child in e.get("on_true", [])
        )
    )
    body = native["on_true"]
    group = next(
        e for e in body if e.get("condition") == "kind == 'ability_check' or kind == 'attack_roll'"
    )
    index = body.index(group)
    body[index : index + 1] = group["on_true"]
    dodge = next(
        e
        for e in body
        if e.get("condition")
        == ("actor_state.dodging == true and kind == 'saving_throw' and ability == 'dexterity'")
    )
    index = body.index(dodge)
    body[index : index + 1] = dodge["on_true"]
    rolled = rolling["on_false"]
    attack = next(e for e in rolled if e.get("condition") == "kind == 'attack_roll'")
    critical = [
        e for e in attack["on_true"] if e.get("condition", "").startswith("_d20_test.total ==")
    ]
    assert len(critical) == 2
    attack["on_true"] = [e for e in attack["on_true"] if e not in critical]
    for e in critical:
        e["condition"] = "kind == 'attack_roll' and " + e["condition"]
    index = rolled.index(attack)
    rolled[index + 1 : index + 1] = critical
    return result


def _future_revival_math(tool):
    """Retain the actual signed recovery owner; older parents receive its accepted test overlay."""
    result = deepcopy(tool)
    effects = result["effects"]
    if "_adjusted_total + actor_state.revival_penalty" in str(effects):
        return result
    effects[:] = [
        e
        for e in effects
        if not (
            "actor_state.revival_penalty" in e.get("condition", "")
            and any(
                child.get("set", {}).get("field") == "_combined_modifier"
                for child in e.get("on_true", [])
            )
        )
    ]
    rolling = next(e for e in effects if e.get("condition", "").startswith("forego_save == true"))
    rolled = rolling["on_false"]
    if "_adjusted_total + actor_state.revival_penalty" not in str(rolled):
        rolled[:] = [
            e
            for e in rolled
            if not (
                "actor_state.revival_penalty" in e.get("condition", "")
                and any(
                    child.get("set", {}).get("field") == "_combined_modifier"
                    for child in e.get("on_true", [])
                )
            )
        ]
        index = next(
            i for i, e in enumerate(rolled) if e.get("set", {}).get("field") == "_combined_modifier"
        )
        rolled.insert(
            index,
            _when(
                "actor_state != null and actor_state.revival_penalty != null",
                [_set("_adjusted_total", "_adjusted_total + actor_state.revival_penalty")],
            ),
        )
    return result


@lru_cache
def _runtime(normalized=True):
    runtime = yaml.load(
        (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes(), Loader=yaml.CSafeLoader
    )
    legacy = _legacy_tool(
        _future_revival_math(_compose_invisibility_roll(runtime["tools"]["d20_test"]))
    )
    runtime["tools"]["d20_test"] = normalize_d20_tool(legacy) if normalized else legacy
    assert normalize_d20_tool(normalize_d20_tool(legacy)) == normalize_d20_tool(legacy)
    return runtime, yaml.safe_dump(runtime, sort_keys=False).encode()


@lru_cache
def _mechanic(name, normalized=True):
    runtime, payload = _runtime(normalized)
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
def _actual_runtime(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards, "_spell_definitions", lambda: runtime["definitions"]["spells"]
    )
    monkeypatch.setattr(dice, "set_seed", lambda _: None)


def _compare(monkeypatch, tool, state, params, data=None, seed=0):
    count = 0
    original = ExecutionBudget.consume_operation

    def measure(budget):
        nonlocal count
        count += 1
        original(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", measure)
    results = []
    for normalized in (False, True):
        monkeypatch.setattr(
            test_srd_action_budget, "_mechanic", lambda name, n=normalized: _mechanic(name, n)
        )
        saved = deepcopy(state)
        monkeypatch.setattr(dice, "_rng", Random(seed))
        count = 0
        result = _run(tool, saved, participant_data=data, **params)
        context = result.result["context"]
        results.append((result, saved, count, {key: context.get(key) for key in MATH_FIELDS}))
    assert results[0][0].result["result_card"] == results[1][0].result["result_card"]
    assert results[0][1] == results[1][1]
    assert results[0][3] == results[1][3]
    return results


@pytest.mark.parametrize(
    "condition,dodging", [([], False), (["restrained"], False), ([], True), (["paralyzed"], False)]
)
def test_actual_saves_and_automatic_failure_keep_cards_math_and_state_with_lower_cost(
    monkeypatch, condition, dodging
):
    state, _, _ = _case("light")
    state[f"{SRD}.entities"]["guard"].update(conditions=condition, dodging=dodging)
    results = _compare(monkeypatch, "d20_test", state, PARAMS)
    assert results[0][2] - results[1][2] >= 11
    print(
        f"Native save {condition!r}, Dodge={dodging}: {results[0][2]} -> {results[1][2]} operations"
    )
    if condition == ["paralyzed"]:
        context = results[1][0].result["context"]
        assert context["_d20_test"] is None
        assert context["_adjusted_total"] is None
        assert context["_combined_modifier"] is None
        assert context["_outcome"] == "failure"
    assert ExecutionBudget().max_operations == 10_000


@pytest.mark.parametrize(
    "conditions,changes,expected",
    [
        (["blinded"], {"kind": "attack_roll"}, "disadvantage"),
        (["poisoned"], {"kind": "ability_check"}, "disadvantage"),
        (["prone"], {"kind": "attack_roll"}, "disadvantage"),
        (["restrained"], {"kind": "attack_roll"}, "disadvantage"),
        (["invisible", "blinded"], {"kind": "attack_roll"}, "cancelled"),
        (["frightened"], {"kind": "ability_check", "fear_source_in_sight": True}, "disadvantage"),
        (["blinded"], {"kind": "ability_check", "requires_sight": True}, "blinded_sight"),
        (["deafened"], {"kind": "ability_check", "requires_hearing": True}, "deafened_hearing"),
    ],
)
def test_moved_native_attack_and_check_branches_keep_qualified_outcomes(
    monkeypatch, conditions, changes, expected
):
    state, _, _ = _case("light")
    state[f"{SRD}.entities"]["guard"]["conditions"] = conditions
    results = _compare(monkeypatch, "d20_test", state, PARAMS | changes)
    context = results[1][0].result["context"]
    if expected == "disadvantage":
        assert context["_effective_disadvantage"] is True
        assert len(context["_d20_test"]["results"]) == 2
    elif expected == "cancelled":
        assert context["_effective_advantage"] is True
        assert context["_effective_disadvantage"] is True
        assert len(context["_d20_test"]["results"]) == 1
    else:
        assert context["_automatic_failure_reason"] == expected
        assert context["_outcome"] == "failure"
        assert context["_d20_test"] is None


@pytest.mark.parametrize("factor", ["none", "half", "normal", "double"])
@pytest.mark.parametrize("bonus", [3, -3])
def test_all_admitted_proficiency_factors_keep_exact_integer_rounding(monkeypatch, factor, bonus):
    state, _, _ = _case("light")
    results = _compare(
        monkeypatch,
        "d20_test",
        state,
        PARAMS | {"proficiency_factor": factor, "proficiency_bonus": bonus},
    )
    expected = {"none": 0, "half": bonus // 2, "normal": bonus, "double": bonus * 2}[factor]
    assert results[1][0].result["context"]["_proficiency_modifier"] == expected


@pytest.mark.parametrize("seed,total,outcome", [(5, 20, "critical_hit"), (31, 1, "critical_miss")])
def test_actual_natural_attack_outcomes_retain_the_original_roll_and_card(
    monkeypatch, seed, total, outcome
):
    state, _, _ = _case("light")
    results = _compare(monkeypatch, "d20_test", state, PARAMS | {"kind": "attack_roll"}, seed=seed)
    context = results[1][0].result["context"]
    assert context["_d20_test"]["total"] == total
    assert context["_attack_outcome"] == outcome


@pytest.mark.parametrize(
    "spell,kind,changes",
    [
        ("bless", "saving_throw", {}),
        ("bane", "saving_throw", {}),
        ("guidance", "ability_check", {"skill": "athletics"}),
        ("invisibility", "attack_roll", {"target_number": 99}),
        ("greater_invisibility", "attack_roll", {"target_number": 99}),
    ],
)
def test_active_source_hooks_cross_actual_cast_then_mixed_roll_consumers(
    monkeypatch, spell, kind, changes
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    state, data, params = _case(spell, selected_skill="athletics")
    if spell in {"bless", "bane"}:
        params["targets"] = ["guard"]
        state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["charisma"] = -1
    _run("cast_spell", state, participant_data=data, **params)
    guard = state[f"{SRD}.entities"]["guard"]
    if spell in {"bless", "bane"}:
        assert guard["concentration_d20_modifier_source_ids"]
    if spell == "guidance":
        assert guard["guidance_head_caster_state_id"] == "hero"
    results = _compare(monkeypatch, "d20_test", state, PARAMS | {"kind": kind} | changes)
    context = results[1][0].result["context"]
    if spell == "bless":
        assert context["_lasting_d20_modifier"] > 0
    if spell == "bane":
        assert context["_lasting_d20_modifier"] < 0
    if spell == "guidance":
        assert context["_guidance_bonus"] > 0
    if spell == "invisibility":
        assert "invisible" not in results[1][1][f"{SRD}.entities"]["guard"]["conditions"]
    if spell == "greater_invisibility":
        assert "invisible" in results[1][1][f"{SRD}.entities"]["guard"]["conditions"]


def test_actual_outline_suppression_keeps_native_invisible_without_its_roll_benefit(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    state, _, _, _ = _cast("faerie_fire", illumination_object="", illumination_position=[1, 0, 0])
    state[f"{SRD}.entities"]["other"]["conditions"].append("invisible")
    results = _compare(
        monkeypatch, "d20_test", state, PARAMS | {"actor_state": "other", "kind": "attack_roll"}
    )
    assert len(results[1][0].result["context"]["_d20_test"]["results"]) == 1
    assert "invisible" in results[1][1][f"{SRD}.entities"]["other"]["conditions"]


def test_source_revival_penalty_reduces_actual_total_before_combined_modifier_and_outcome(
    monkeypatch,
):
    state, _, _ = _case("light")
    state[f"{SRD}.entities"]["guard"]["revival_penalty"] = -4
    results = _compare(monkeypatch, "d20_test", state, PARAMS)
    context = results[1][0].result["context"]
    assert context["_d20_test"]["total"] == 13
    assert context["_adjusted_total"] == 9
    assert context["_combined_modifier"] == -4
    assert context["_outcome"] == "failure"


def test_real_condition_save_and_character_check_wrappers_keep_native_cards_and_authority(
    monkeypatch,
):
    state, data, _ = _case("light")
    _compare(
        monkeypatch,
        "resolve_srd_condition_saving_throw",
        state,
        {
            "target": "guard",
            "ability": "dexterity",
            "condition": "poisoned",
            "source_save_dc": 10,
        },
    )
    _compare(
        monkeypatch,
        "ability_check",
        state,
        {
            "actor": "hero",
            "actor_state": "hero",
            "ability": "strength",
            "skill": "athletics",
            "circumstantial_modifier": 0,
            "target_number": 10,
        },
        data=data,
    )


def test_normal_runtime_emission_changes_only_the_completed_roll_and_is_idempotent():
    runtime, payload = _runtime(False)
    normalized = normalize_runtime(payload.decode())
    emitted = yaml.load(normalized, Loader=yaml.CSafeLoader)
    assert [
        name for name in emitted["tools"] if runtime["tools"][name] != emitted["tools"][name]
    ] == ["d20_test"]
    assert normalize_runtime(normalized) == normalized


@lru_cache
def _test_archive():
    runtime, payload = _runtime()
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    for tool in record["tools"]:
        if tool["name"] in runtime["tools"]:
            tool["input_schema"] = _tool_schema(
                runtime["tools"][tool["name"]], tool_name=tool["name"]
            )
    return _archive(
        (
            NormalizedPackageDraft(
                record,
                dict(primary.payloads) | {"runtime/ruleset.yaml": payload},
                primary.format_version,
            ),
            reference,
        )
    )


def test_actual_archived_public_roll_keeps_native_card_source_end_and_durable_retry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", _case)
    monkeypatch.setattr(dice, "_rng", Random(0))
    database = tmp_path / "normalized-roll.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "invisibility")
        cast, _ = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        assert _records(owner.snapshot("casting").state, "invisibility")
        receipt, command = _act(
            owner, "d20_test", **(PARAMS | {"kind": "attack_roll", "target_number": 99})
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        context = receipt.result["context"]
        assert len(context["_d20_test"]["results"]) == 2
        assert context["_attack_outcome"] == "normal"
        assert context["_outcome"] == "failure"
        assert receipt.result["result_card"]["title"] == "Dexterity"
        saved = owner.snapshot("casting")
        assert not _records(saved.state, "invisibility")
        assert "invisible" not in saved.state[f"{SRD}.entities"]["guard"]["conditions"]
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved

        class NoReroll(Random):
            def randint(self, _a, _b):
                pytest.fail("Durable normalized roll retry rolled again")

        monkeypatch.setattr(dice, "_rng", NoReroll(99))
        assert reopened.execute_action(command) == receipt
        assert reopened.snapshot("casting") == saved
