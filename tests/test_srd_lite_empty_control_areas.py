"""Selected empty-area concentration through unchanged paid and lifecycle owners."""

import os
import zipfile
from copy import deepcopy
from random import Random

import pytest
from scripts.srd_lite_control_area_geometry import (
    PAID,
    PREFIX_LENGTH,
    ROOTS,
    bind_control_combat_areas,
)
from scripts.srd_lite_empty_control_areas import (
    END,
    OLD_EMPTY_GUARD,
    QUERY_PREFIX,
    REFRESH,
    START,
    bind_paid_empty_control_areas,
)
from scripts.srd_lite_flame_strike_geometry import bind_flame_strike_combat_cylinder
from scripts.srd_lite_mass_cure_geometry import _effect_lists
from scripts.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime, _effect_executor
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_srd_lite_control_area_geometry import state
from tests.test_srd_lite_instant_combat_areas import preflight
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls


@pytest.fixture(scope="module")
def empty_controls():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    bind_flame_strike_combat_cylinder(data)
    bind_control_combat_areas(data)
    profiles = deepcopy(data["definitions"]["spells"])
    headers = deepcopy(data["definitions"]["spell_casting"])
    unchanged = {
        name: deepcopy(data["tools"][name])
        for name in (
            "d20_test",
            "resolve_srd_concentration_after_damage",
            "end_concentration",
            "complete_srd_pending_action",
        )
    }
    original_roots = {name: deepcopy(data["tools"][name]) for name in ROOTS}
    changed = bind_paid_empty_control_areas(data)
    assert changed == [
        *ROOTS,
        PAID,
        START,
        REFRESH,
        END,
        QUERY_PREFIX + REFRESH,
        QUERY_PREFIX + END,
    ]
    assert data["definitions"]["spells"] == profiles
    assert data["definitions"]["spell_casting"] == headers
    assert {name: data["tools"][name] for name in unchanged} == unchanged
    # The entire public casting/action/payment flow differs only by removal of
    # the previous explicit empty rejection after actual geometry admission.
    for root, original in original_roots.items():
        for rows in list(_effect_lists(original["effects"])):
            rows[:] = [row for row in rows if row != OLD_EMPTY_GUARD]
        assert data["tools"][root] == original
    pending = [
        PAID,
        START,
        REFRESH,
        END,
        QUERY_PREFIX + REFRESH,
        QUERY_PREFIX + END,
        "refresh_srd_condition_casters",
        "resolve_srd_concentration_after_damage",
        "end_concentration",
    ]
    for root in ROOTS:
        preview = deepcopy(data["tools"][root])
        preview["effects"] = preview["effects"][:PREFIX_LENGTH]
        preview["pre_conditions"] = []
        data["tools"]["preflight_" + root] = preview
        pending.append("preflight_" + root)
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in {"tools", "procedures"}}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


def owning_state(*, combat=True):
    value = state((20, 0, 0), hero=(-30, 0, 0), combat=combat)
    lasting = value[NAMESPACE + ".state"]
    lasting.update(condition_caster_ids=[], timed_condition_sources={})
    for actor in value[NAMESPACE + ".entities"].values():
        actor.update(
            creature_kind="monster",
            temp_hp=0,
            exhaustion_level=0,
            speed=30,
            saving_throw_modifiers={"wisdom": 0, "constitution": 0},
            condition_immunities=[],
            damage_immunities=[],
            damage_resistances=[],
            damage_vulnerabilities=[],
            held_items=[],
            held_attack_names=[],
            inventory=[],
        )
    return value


def run(parsed, value, tool, parameters, *, rolls=False):
    """Apply only successful real bridge mutations and retain rejection/RNG invariants."""
    before = deepcopy(value)
    rng = dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, value)
    parameters = dict(parameters)
    if any(row.name == "lasting_state" for row in parsed.tools[tool].parameters):
        parameters.setdefault("lasting_state", "state")

    def nested(name, supplied, parent):
        return execute_tool(
            name,
            supplied,
            parsed.tools,
            bridge,
            parsed.ruleset_config,
            parsed.definitions,
            combat_state=parent.combat_state,
            tool_executor=nested,
            budget=parent.budget,
        )

    result = execute_tool(
        tool,
        parameters,
        parsed.tools,
        bridge,
        parsed.ruleset_config,
        parsed.definitions,
        combat_state=value[NAMESPACE + ".combat"],
        tool_executor=nested,
        budget=ExecutionBudget(),
    )
    assert value == before
    if result.success:
        for mutation in bridge.mutations():
            value[mutation.namespace + "." + mutation.key] = deepcopy(mutation.value)
    if not rolls:
        assert dice._rng.getstate() == rng
    return result


def cast(
    parsed,
    value,
    spell="sleep",
    *,
    caster="hero",
    targets=(),
    modifier=0,
    cast_id="empty-1",
    forego=(),
):
    return run(
        parsed,
        value,
        PAID,
        {
            "caster_state": caster,
            "spell": spell,
            "slot_level": 3 if spell == "fear" else 1,
            "save_dc": 15,
            "concentration_save_modifier": modifier,
            "targets": list(targets),
            "targets_max_distance": 0,
            "caster_can_see_all_targets": True,
            "all_targets_have_clear_path": True,
            "forego_save_targets": list(forego),
            "spell_cast_id": cast_id,
            "spell_cast_owner": caster,
            "actual_spell_level": 3 if spell == "fear" else 1,
        },
    )


def assert_ended(value, caster="hero"):
    actor = value[NAMESPACE + ".entities"][caster]
    assert actor["concentration_spell_id"] is None
    assert actor["concentration_effect_family"] is None
    assert actor["concentration_target_state_id"] is None
    assert actor["concentration_target_state_ids"] is None
    assert caster not in value[NAMESPACE + ".state"]["condition_caster_ids"]


@pytest.mark.parametrize("spell", ("fear", "sleep"))
def test_paid_empty_area_owns_concentration_without_recipient_rolls_or_writes(
    empty_controls, spell
):
    value = owning_state()
    guard = deepcopy(value[NAMESPACE + ".entities"]["guard"])
    result = cast(empty_controls, value, spell)
    assert result.success, result.error
    actor = value[NAMESPACE + ".entities"]["hero"]
    assert actor["concentration_spell_id"] == spell
    assert actor["concentration_cast_id"] == "empty-1"
    assert actor["concentration_target_state_id"] == "hero"
    assert actor["concentration_target_state_ids"] == []
    assert actor["concentration_expires_tick"] == 20
    assert value[NAMESPACE + ".state"]["condition_caster_ids"] == ["hero"]
    assert value[NAMESPACE + ".state"]["timed_condition_sources"] == {}
    assert value[NAMESPACE + ".entities"]["guard"] == guard
    assert result.context["_control_save_rolls"] == []
    assert result.context["_control_save_outcomes"] == []
    assert result.context["_spell_summary_rows"] == [
        {"label": "Affected creatures", "value": 0},
        {"label": "Concentration", "value": "Active"},
    ]


@pytest.mark.parametrize("root", ROOTS)
@pytest.mark.parametrize("spell", ("fear", "sleep"))
def test_public_and_pending_preflight_keep_empty_actual_recipients(empty_controls, root, spell):
    value = state((-10, 0, 0)) if spell == "fear" else owning_state()
    result = preflight(
        empty_controls,
        value,
        "preflight_" + root,
        {
            "caster_state": "hero",
            "spell": spell,
            "slot_resource": "spell_slot_3" if spell == "fear" else "spell_slot_1",
            "environment_origin": [0, 0, 3],
        },
    )
    assert result.success, result.error
    assert result.context["_control_geometry_targets"] == []
    assert result.context["_control_geometry_target"] is None
    assert result.context["_control_geometry__target_id"] is None


@pytest.mark.parametrize("tool", (REFRESH, QUERY_PREFIX + REFRESH))
def test_real_owner_anchor_refreshes_after_combat_without_ending_empty_source(empty_controls, tool):
    value = owning_state()
    assert cast(empty_controls, value).success
    value[NAMESPACE + ".combat"]["encounter_active"] = False
    result = run(empty_controls, value, tool, {"source_state": "hero", "target_state": "hero"})
    assert result.success, result.error
    assert result.context["_source_concentration_active"] is True
    assert value[NAMESPACE + ".entities"]["hero"]["concentration_target_state_ids"] == []


@pytest.mark.parametrize("invalid", ("expiry", "incapacity"))
def test_existing_condition_caster_index_expires_or_invalidates_empty_source(
    empty_controls, invalid
):
    value = owning_state()
    assert cast(empty_controls, value).success
    if invalid == "expiry":
        value[NAMESPACE + ".state"]["lasting_tick"] = 20
    else:
        value[NAMESPACE + ".entities"]["hero"]["conditions"] = ["incapacitated"]
    result = run(empty_controls, value, "refresh_srd_condition_casters", {})
    assert result.success, result.error
    assert_ended(value)


def test_empty_replacement_and_explicit_end_preserve_other_casters(empty_controls):
    value = owning_state()
    value[NAMESPACE + ".entities"]["ally"] = deepcopy(value[NAMESPACE + ".entities"]["guard"])
    assert cast(empty_controls, value, "fear", cast_id="old-fear").success
    first_sequence = value[NAMESPACE + ".entities"]["hero"]["concentration_sequence"]
    result = cast(empty_controls, value, caster="ally", cast_id="ally-sleep")
    assert result.success, result.error
    assert cast(empty_controls, value, "sleep", cast_id="new-sleep").success
    actor = value[NAMESPACE + ".entities"]["hero"]
    assert actor["concentration_spell_id"] == "sleep"
    assert actor["concentration_cast_id"] == "new-sleep"
    assert actor["concentration_sequence"] > first_sequence
    assert value[NAMESPACE + ".state"]["condition_caster_ids"].count("hero") == 1
    ally = deepcopy(value[NAMESPACE + ".entities"]["ally"])
    result = run(empty_controls, value, "end_concentration", {"caster_state": "hero"})
    assert result.success, result.error
    assert_ended(value)
    assert value[NAMESPACE + ".entities"]["ally"] == ally
    assert value[NAMESPACE + ".state"]["condition_caster_ids"] == ["ally"]


def test_replacing_nonempty_control_retires_its_actual_recipient_source(empty_controls):
    value = owning_state()
    result = cast(
        empty_controls, value, "fear", targets=["guard"], forego=["guard"], cast_id="old-fear"
    )
    assert result.success, result.error
    assert value[NAMESPACE + ".entities"]["hero"]["concentration_target_state_id"] == "guard"
    assert "frightened" in value[NAMESPACE + ".entities"]["guard"]["conditions"]
    result = cast(empty_controls, value, "sleep", cast_id="new-empty")
    assert result.success, result.error
    assert "frightened" not in value[NAMESPACE + ".entities"]["guard"]["conditions"]
    assert value[NAMESPACE + ".entities"]["hero"]["concentration_target_state_ids"] == []


@pytest.mark.parametrize(("modifier", "outcome"), ((20, "success"), (-20, "failure")))
def test_damage_uses_one_existing_concentration_save_and_ends_only_on_failure(
    empty_controls, monkeypatch, modifier, outcome
):
    monkeypatch.setattr(dice, "_rng", Random(7))
    value = owning_state()
    assert cast(empty_controls, value, modifier=modifier).success
    expressions = []
    original = _effect_executor.dice_roll

    def recorded(expression, *args, **kwargs):
        expressions.append(expression)
        return original(expression, *args, **kwargs)

    monkeypatch.setattr(_effect_executor, "dice_roll", recorded)
    result = run(
        empty_controls,
        value,
        "resolve_srd_concentration_after_damage",
        {"caster_state": "hero", "damage_taken": 10},
        rolls=True,
    )
    assert result.success, result.error
    assert len(expressions) == 1
    assert result.context["_concentration_save_dc"] == 10
    assert result.context["_concentration_save_outcome"] == outcome
    if outcome == "failure":
        assert_ended(value)
    else:
        assert value[NAMESPACE + ".entities"]["hero"]["concentration_target_state_ids"] == []


@pytest.mark.parametrize(
    ("spell", "combat"), (("sleep", False), ("fear", False), ("confusion", True))
)
def test_other_spells_and_exploration_keep_the_nonempty_contract(empty_controls, spell, combat):
    value = owning_state(combat=combat)
    before = deepcopy(value)
    result = cast(empty_controls, value, spell)
    assert not result.success
    assert value == before
