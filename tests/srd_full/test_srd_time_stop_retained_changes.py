"""Native retained healing and later Eyebite use the creating stop's real source."""

# ruff: noqa: E402, I001
import hashlib
import ast
import importlib.util
import sys
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

from tests import test_srd_time_stop_retained_damage as prior
from scripts import srd_spell_wards as wards
from scripts import srd_spell_source_instances as sources
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from sylvia_gm import dice
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "scripts.srd_full.srd_time_stop_retained_changes", ROOT / "scripts/srd_time_stop_retained_changes.py"
)
owned = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = owned
spec.loader.exec_module(owned)
base = prior.base
for name, digest in {
    "srd_spell_wards.py": "438b899f1b34b222bb199359bd4d4df8d3109301d2ec38d8e4a59c1fd81f8c93",
    "srd_control_afflictions.py": (
        "32ed3604dce2fadbe4c400952f59816e3d01d9da5f9d9d46a908e5c26ce4a454"
    ),
    "srd_spell_source_instances.py": (
        "310df649a31bc9de8bc3568f699b9f27b407a71bb2c753ec235a28e12c9d991a"
    ),
}.items():
    assert hashlib.sha256((prior.BASELINE / "scripts" / name).read_bytes()).hexdigest() == digest


@lru_cache
def _runtime():
    runtime = yaml.load(
        (prior.PARENT / "selected-runtime.yaml").read_text(), Loader=yaml.CSafeLoader
    )
    base.restore_tool_defaults(runtime)
    # This selected predecessor retains the native Ward leaves but predates its paid
    # family dispatch. Compose the exact bounded block from the hash-pinned owner.
    module = ast.parse((prior.BASELINE / "scripts/srd_spell_wards.py").read_text())
    function = next(
        n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "extend_runtime"
    )
    start = next(
        i
        for i, n in enumerate(function.body)
        if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "cast"
    )
    end = next(
        i
        for i, n in enumerate(function.body)
        if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "changed['cast_spell']"
    )
    namespace = {**vars(wards), "tools": runtime["tools"], "changed": {}}
    exec(
        compile(
            ast.Module(body=function.body[start : end + 1], type_ignores=[]),
            "<actual-ward-paid-dispatch>",
            "exec",
        ),
        namespace,
    )
    runtime["tools"].update(namespace["changed"])
    # Refresh the exact current finite Source admission; retain the original allocator,
    # forwarding and concentration identity hooks already present in this predecessor.
    admitted = sources.supported_time_spans(
        runtime["definitions"]["spell_casting"],
        runtime["definitions"]["spells"],
        tools=runtime["tools"],
    )
    tables = [
        e
        for e in runtime["tools"]["cast_spell"]["effects"]
        if e.get("set", {}).get("field") == "_spell_time_span_ids"
    ]
    assert len(tables) == 1 and "eyebite" in admitted
    tables[0]["set"]["value"] = [f"'{spell}'" for spell in admitted]
    text = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    text = prior.effects.bind_source_consumers(text)
    text = prior.effects.bind_received_effects(text)
    text = base.refresh_exploration_runtime(text)
    text = prior.owned.bind_runtime_consumers(text)
    before = _load_runtime(text)
    bound = owned.bind_runtime_consumers(text)
    runtime = _load_runtime(bound)
    assert _load_runtime(owned.bind_runtime_consumers(bound)) == runtime
    assert _load_runtime(owned.restore_runtime(bound)) == before
    runtime["definitions"]["spells"].update(base.stop.PROFILES)
    runtime["definitions"]["spells"].update(wards.PROFILES)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["actor_spell_ward"] = (
        wards._schema()
    )
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "acid_arrow" in parameter.get("enum_values", []):
                parameter["enum_values"] = sorted(runtime["definitions"]["spells"])
    selected = {
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_time_stop_turn",
        "end_srd_time_stop_turn",
        "start_srd_aura_of_life_turn",
        "retarget_srd_eyebite",
        "record_srd_creature_relationship",
        "apply_srd_damage",
        "finalize_srd_time_stop",
    }
    while True:
        added = {
            e["call"]
            for name in selected
            for nodes in prior.effects._lists(runtime["tools"][name]["effects"])
            for e in nodes
            if "call" in e
        } - selected
        if not added:
            break
        assert not added - runtime["tools"].keys()
        selected.update(added)
    runtime["tools"] = {n: t for n, t in runtime["tools"].items() if n in selected}
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(base._tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    from sylvia_gm.application import package_runtime

    actual = package_runtime.execute_tool

    def observe(name, *args, **kwargs):
        result = actual(name, *args, **kwargs)
        if not result.success:
            print(name, result.failure_kind, result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", observe)
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", base.Rolls())


@pytest.mark.parametrize("created_during_stop", [True, False])
def test_paid_life_source_native_receiving_owner_heals_then_marks_only_creating_stop(
    created_during_stop,
):
    state, data, stop_params = base._case()
    life = base._grant(state, data, "aura_of_life")
    prior._bard_sources(data, "aura_of_life")
    life.pop("target", None)
    guard = state[f"{SRD}.entities"]["guard"]
    guard["creature_kind"] = "character"
    _run(
        "record_srd_creature_relationship",
        state,
        actor_state="hero",
        toward_state="guard",
        attitude="friendly",
        is_ally=True,
    )
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=1000,
        adjustment=0,
        damage_type="bludgeoning",
        source_state_id="other",
    )
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 0 and guard["is_alive"] and "unconscious" in guard["conditions"]
    if not created_during_stop:
        _run("cast_spell_outside_combat", state, participant_data=data, **life)
    stop = base._start(state, data, stop_params)
    tick = state[f"{SRD}.state"]["lasting_tick"]
    if created_during_stop:
        _run("cast_spell", state, participant_data=data, **life)
    record = next(
        r
        for r in state[f"{SRD}.state"]["spell_ward_sources"].values()
        if r["spell_id"] == "aura_of_life" and r["installed"]
    )
    assert record["created_time_stop_id"] == (stop["cast_id"] if created_during_stop else None)
    assert state[f"{SRD}.state"]["time_stop_source"]["active"]
    # Another creature's public turn remains paused. This is the genuine receiving leaf,
    # not a forged turn or automatic scheduling claim.
    result = _run("start_srd_aura_of_life_turn", state, target_state="guard")
    guard = state[f"{SRD}.entities"]["guard"]
    stop = state[f"{SRD}.state"]["time_stop_source"]
    assert guard["hp"] == 1 and "unconscious" not in guard["conditions"]
    assert result.result["context"]["_aura_life_healed"] == 1
    assert stop["active"] is not created_during_stop
    assert stop["ending"] is created_during_stop
    if created_during_stop:
        assert stop["end_reason"] == "other_creature"
    assert state[f"{SRD}.state"]["lasting_tick"] == tick
    assert record["installed"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2


@pytest.mark.parametrize("created_during_stop", [True, False])
def test_paid_eyebite_success_then_later_native_condition_ends_only_its_creating_stop(
    created_during_stop,
):
    state, data, stop_params = base._case()
    eye = base._grant(
        state, data, "eyebite", targets=["other"], target_distance=60, eyebite_mode="asleep"
    )
    eye.pop("target", None)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(e for e in scene["entities"] if e["id"] == "other")["position"] = [50, 0, 0]
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = 100
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = -100
    if not created_during_stop:
        _run("cast_spell_outside_combat", state, participant_data=data, **eye)
    stop = base._start(state, data, stop_params)
    receipt, tick = stop["cast_id"], state[f"{SRD}.state"]["lasting_tick"]
    if created_during_stop:
        _run("cast_spell", state, participant_data=data, **eye)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["eyebite_time_stop_receipt"]["created_time_stop_id"] == (
        receipt if created_during_stop else None
    )
    assert hero["eyebite_time_stop_receipt"]["cast_id"] == hero["concentration_cast_id"]
    assert hero["eyebite_saved_success_ids"] == ["other"]
    assert stop["active"] and not stop["spell_action_active"]
    _run("end_srd_time_stop_turn", state, caster_state="hero")
    _run("begin_srd_time_stop_turn", state, caster_state="hero")
    result = _run(
        "retarget_srd_eyebite",
        state,
        participant_data=data,
        caster_state="hero",
        target_state="guard",
        eyebite_mode="asleep",
    )
    guard = state[f"{SRD}.entities"]["guard"]
    hero = state[f"{SRD}.entities"]["hero"]
    stop = state[f"{SRD}.state"]["time_stop_source"]
    assert "unconscious" in guard["conditions"] and "prone" in guard["conditions"]
    assert guard["hp"] == 1000
    assert stop["active"] is not created_during_stop
    assert not stop["ending"]
    if created_during_stop:
        assert stop["end_reason"] == "other_creature"
    assert state[f"{SRD}.state"]["lasting_tick"] == tick
    assert hero["resources"]["spell_slot_6"] == 2
    assert hero["last_eyebite_save_roll"]["count"] == 1
    if created_during_stop:
        assert result.result["context"]["_time_stop_change_conditions_after"] == guard["conditions"]
    else:
        assert "_time_stop_change_conditions_after" not in result.result["context"]


def test_named_conjured_healing_and_fear_preserve_native_calls_and_inverse():
    runtime = _load_runtime((prior.PARENT / "selected-runtime.yaml").read_text())
    initial = prior.presence._celestial()
    runtime["tools"].update(
        {
            "resolve_srd_celestial_initial_lights": initial,
            "resolve_srd_celestial_event_lights": prior.celestial._event_batch(initial),
            "install_srd_conjured_fey_fear": prior.presence._fey_fear(),
        }
    )
    text = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    bound = owned.bind_runtime_consumers(text)
    assert _load_runtime(owned.restore_runtime(bound)) == runtime
    assert _load_runtime(owned.bind_runtime_consumers(bound)) == _load_runtime(bound)
    candidate = _load_runtime(bound)
    for name in owned.CONJURED:
        native = deepcopy(candidate["tools"][name])
        owned._unmark(native)
        call, kind = owned.CONJURED[name]
        owned._remove_around(
            native, call, "{_presence.caster}", "_presence.created_time_stop_id", kind
        )
        assert native == runtime["tools"][name]
