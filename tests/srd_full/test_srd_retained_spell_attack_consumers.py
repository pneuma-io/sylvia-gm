"""Final retained-attack joins use existing paid casts, clock and exact removal owners."""

from copy import deepcopy
from functools import lru_cache
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_retained_spell_attacks import _lists, bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _call, _when

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_retained_spell_attacks as source
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity

BASE_RUNTIME = source._runtime


@lru_cache
def _runtime():
    runtime = deepcopy(BASE_RUNTIME()[0])
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = runtime["tools"][name]
        tool["effects"] = [e for e in tool["effects"] if "cast_srd_vampiric_touch" not in str(e)]
        index = next(
            i
            for i, e in enumerate(tool["effects"])
            if e.get("set", {}).get("field") == "_card_slots_after"
        )
        # Only this proposed common adoption allocation is absent from native267.
        # The production retained binder receives its output and never allocates.
        tool["effects"].insert(
            index,
            _when(
                "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
                "_spell_definition.family == 'retained_spell_attack'",
                [
                    _call(
                        "begin_srd_spell_source_instance",
                        {
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "spell": "{spell}",
                            "slot_level": "{_slot_level}",
                        },
                        ["_spell_source_cast_id"],
                    )
                ],
            ),
        )
    before = deepcopy(runtime)
    encoded = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    encoded = bind_runtime_consumers(encoded)
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    assert runtime["tools"]["d20_test"] == before["tools"]["d20_test"]

    def calls(tool):
        return [e.get("call") for ss in _lists(tool["effects"]) for e in ss]

    for name in ("cast_spell", "cast_spell_outside_combat"):
        assert calls(runtime["tools"][name]).count("begin_srd_spell_source_instance") == calls(
            before["tools"][name]
        ).count("begin_srd_spell_source_instance")
    return runtime, encoded.encode()


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
            input_schema=owned_payload(_tool_schema(runtime["tools"][name])),
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
    monkeypatch.setattr(dice, "_rng", source.Rolls())
    actual = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


def _records(state):
    return [
        r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("kind") == "retained_spell_attack"
    ]


def test_actual_paid_dispatch_uses_one_existing_source_identity_and_emits_idempotently():
    runtime, encoded = _runtime()
    assert bind_runtime_consumers(encoded.decode()) == encoded.decode()
    state, data, params = source._case()
    context = source._cast(state, data, params)
    record = _records(state)[0]
    assert context["_retained_source_id"] == record["cast_id"] == "srd_cast:1"
    assert state[f"{SRD}.state"]["spell_source_serial"] == 1
    assert state[f"{SRD}.entities"]["hero"]["concentration_cast_id"] == record["cast_id"]
    assert runtime["tools"]["cast_spell"]["metadata"]["result_card"]


@pytest.mark.parametrize("ending", ["clock", "concentration", "dismissal"])
def test_actual_clock_concentration_and_dismissal_retire_source_and_derive_index(ending):
    state, data, params = source._case()
    source._cast(state, data, params)
    record = _records(state)[0]
    if ending == "clock":
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    elif ending == "concentration":
        _run("end_concentration", state, caster_state="hero")
    else:
        _run("dismiss_srd_spell", state, caster_state="hero", cast_id=record["cast_id"])
    assert _records(state) == []
    assert state[f"{SRD}.state"]["has_retained_spell_attacks"] is False
    if ending != "clock":
        assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="No active saved"):
        source._later(state, data)
    assert state == before


@pytest.mark.parametrize("source_level", [3, 5])
def test_actual_paid_dispel_uses_true_source_level_and_exact_retirement(source_level):
    state, data, params = source._case(source_level)
    source._cast(state, data, params)
    spec = source._mage_spec()
    spec["spells"].append({"spell": "dispel_magic", "uses_per_day": 2})
    _run("prepare_srd_creature", state, **spec)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [10, 0, 0], participant_ref="mage", creature_type="humanoid")
    )
    result = _run(
        "cast_spell_outside_combat",
        state,
        spell="dispel_magic",
        caster_state="mage",
        target="hero",
        # Native267's existing Recovery declaration still requires this actual
        # ten-foot scene distance; the new retained action has no distance input.
        target_distance=10,
        verbal_component_available=True,
        somatic_component_available=True,
    )
    assert result.result["result_card"]
    if source_level == 3:
        assert _records(state) == []
        assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
        assert state[f"{SRD}.state"]["has_retained_spell_attacks"] is False
    else:
        # The real level-3 Dispel check is d20(10)+Int(3) versus DC15.
        assert _records(state)[0]["slot_level"] == 5
        assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "vampiric_touch"
        assert state[f"{SRD}.state"]["has_retained_spell_attacks"] is True


def test_exact_old_source_removal_preserves_a_real_newer_recast():
    state, data, params = source._case()
    old = source._cast(state, data, params)["_retained_source_id"]
    new = source._cast(state, data, params)["_retained_source_id"]
    assert old != new
    _run("finish_srd_retained_spell_attack_removal", state, caster_state="hero", source_id=old)
    assert _records(state)[0]["source_id"] == new
    assert state[f"{SRD}.entities"]["hero"]["concentration_cast_id"] == new
    assert state[f"{SRD}.state"]["has_retained_spell_attacks"] is True


def test_actual_public_bound_source_reopens_dismisses_and_retries_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "_runtime", _runtime)
    source._archive.cache_clear()
    monkeypatch.setattr(public_casting, "bundled_srd_archive", source._archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: source._case())
    database = tmp_path / "retained-bound.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, "vampiric_touch", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        paid, command = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        cast_id = _records(saved.state)[0]["cast_id"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(command) == paid
        ended, ending_command = _act(
            owner, "dismiss_srd_spell", caster_state="hero", cast_id=cast_id
        )
        assert ended.status is ActionStatus.COMMITTED
        final = owner.snapshot("casting")
        assert _records(final.state) == []
        with pytest.raises(MechanicsRejected, match="No active saved"):
            _act(
                owner, "use_srd_vampiric_touch", caster="hero", caster_state="hero", target="guard"
            )
        assert owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:

        class NoReroll(Random):
            def randint(self, _a, _b):
                pytest.fail("A paid or removed retained-source retry rolled again")

        monkeypatch.setattr(dice, "_rng", NoReroll())
        assert owner.execute_action(command) == paid
        assert owner.execute_action(ending_command) == ended
        assert owner.snapshot("casting") == final
    source._archive.cache_clear()
