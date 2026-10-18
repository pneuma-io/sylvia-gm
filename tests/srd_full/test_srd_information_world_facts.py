"""Existing paid Information and Nature owners acquire physical facts without a map."""

# ruff: noqa: E402

import ast
import hashlib
import importlib.util
import sys
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import scripts
import yaml

import tests

BASELINE = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-information-world-facts/input-1"))
if not BASELINE.is_dir():
    pytest.skip(
        "The frozen native Information proof dependency is unavailable.", allow_module_level=True
    )
ROOT = Path(__file__).parents[1]
DEPENDENCIES = {
    "scripts/srd_information_spells.py": (
        "f65708d4248742ecd37aeceabf27db388f36ed341fc0da95f2e9716ec9117cc1"
    ),
    "scripts/srd_information_casting.py": (
        "4791bb81f04830094326ae61ccba19eca86d09129fe0bd014a0fe788f2f40103"
    ),
    "scripts/srd_divination_spells.py": (
        "7f4910148e38fbc5be3294a26339d11c0e5c816fa39a7c7e8e733969d8747237"
    ),
    "scripts/srd_divination_continuation.py": (
        "d2edd9e01d3463b5d98be5d234cb2eb42f66c730192e9bb36e217a085c053540"
    ),
    "scripts/srd_timed_actor_defenses.py": (
        "57815b0db5037ea6f0783c1ccebaac14094f42f8f50ea5c3696b3b580585cd13"
    ),
    "scripts/srd_timed_senses.py": (
        "37329ea79182f1eeceb57611e99d08450deb7a87fa0764de725ff4eb70570595"
    ),
    "tests/test_srd_information_self_casting.py": (
        "69359996227e471e39182c45ce7ec357dcd24ee3f626856fd246bb1b18acce79"
    ),
    "tests/test_srd_spell_wave3_information.py": (
        "1b89913c4fe0d07babdd416ede92090e21b46fb6c14098be0e790c198d90fef3"
    ),
    "tests/test_srd_spell_wave4_divination.py": (
        "011eab24eaf48c81abf7db713c87fa609330fc072300185c1c8be73d390f5add"
    ),
    "tests/test_srd_extended_casting.py": (
        "5bb7b7058ddcf45508254f5d2d451c9bad0efdac3355143c6afb14f6e49a85f7"
    ),
    "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml": (
        "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
    ),
}
for dependency, digest in DEPENDENCIES.items():
    assert hashlib.sha256((BASELINE / dependency).read_bytes()).hexdigest() == digest, dependency
scripts.__path__ = [str(BASELINE / "scripts"), *scripts.__path__]
tests.__path__ = [str(BASELINE / "tests"), *tests.__path__]
spec = importlib.util.spec_from_file_location(
    "scripts.srd_full.srd_information_world_facts", ROOT / "scripts/srd_information_world_facts.py"
)
owned = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = owned
spec.loader.exec_module(owned)

from scripts import srd_divination_continuation as continuation
from scripts import srd_divination_spells as divination
from scripts import srd_information_spells as information_owner
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected
from tests import test_srd_information_self_casting as base
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_wave3_information as information
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act


def _nature_current_owner():
    """Use the exact existing Nature body and its authentic native-CR continuation hunk."""
    changed = divination._nature_tools() | continuation._nature_tools()
    path = BASELINE / "scripts/srd_divination_continuation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "deepen_divination_tools"
    )
    start = next(
        i
        for i, n in enumerate(function.body)
        if isinstance(n, ast.FunctionDef) and n.name == "qualify"
    )
    fragment = ast.Module(body=deepcopy(function.body[start:]), type_ignores=[])
    env = vars(continuation).copy() | {"changed": changed}
    exec(compile(ast.fix_missing_locations(fragment), str(path), "exec"), env)
    return changed


@lru_cache
def _source_runtime():
    runtime, _ = base_runtime()
    runtime = deepcopy(runtime)
    changed = (
        information_owner._casting_tools() | divination._casting_tools() | _nature_current_owner()
    )
    divination._normalize(changed)
    runtime["tools"].update(changed)
    original = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    return _load_runtime(original), original


@lru_cache
def _runtime():
    _, original = _source_runtime()
    encoded = owned.bind_runtime_consumers(original)
    return _load_runtime(encoded), encoded.encode()


base_runtime = base._runtime


@pytest.fixture
def scope(monkeypatch):
    monkeypatch.setattr(base, "_runtime", _runtime)
    base._mechanic.cache_clear()
    base._authored.__wrapped__(monkeypatch)
    yield
    base._mechanic.cache_clear()


def _candidate(identity="guard", **extra):
    return {
        "id": identity,
        "subject_state_id": identity,
        "plane": "material",
        "distance_feet": 20,
        "direction": "east",
        "lighting": "bright",
        "obstruction": "clear",
        "crossings": [],
        **extra,
    }


def _facts(spell="detect_evil_and_good", candidates=None, coverage=30):
    return {
        "caster_state": "hero",
        "spell": spell,
        "current_place": "gate",
        "current_plane": "material",
        "coverage_feet": coverage,
        "candidates": candidates if candidates is not None else [_candidate()],
    }


def test_paid_information_source_map_free_query_reopen_retry_and_native_filters(
    scope, tmp_path, monkeypatch
):
    from sylvia_gm.application import bundled_packages
    from tests import test_srd_spell_appearance as appearance
    from tests.srd_full.test_srd_dream_spell import _linked_drafts

    monkeypatch.setattr(bundled_packages, "_srd_drafts", _linked_drafts)
    monkeypatch.setattr(appearance, "_runtime", _runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", owned.PUBLIC_TOOLS)
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)
    actual_case = information._case

    def case(spell):
        state, data, params = actual_case(spell)
        state[f"{SRD}.state"].pop("spatial_scene")
        state[f"{SRD}.entities"]["guard"]["creature_type"] = "fiend"
        params.pop("target")
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", case)
    database = tmp_path / "information-world.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "detect_evil_and_good")
        paid, paid_command = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        state = owner.snapshot("casting").state
        assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "detect_evil_and_good"
        record, record_command = _act(owner, "record_srd_information_world_facts", **_facts())
        assert record.status is ActionStatus.COMMITTED
        query, query_command = _act(
            owner,
            "query_srd_information_spell",
            caster_state="hero",
            spell="detect_evil_and_good",
            current_place="gate",
            current_plane="material",
        )
        assert information._summary(query)["Present"] is True
        assert "fiend" in str(information._summary(query)["Information"])
        saved = owner.snapshot("casting")
        assert StatelessMcpMechanics(owner).execute_action(paid_command) == paid
        assert StatelessMcpMechanics(owner).execute_action(record_command) == record
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        with pytest.raises(MechanicsRejected, match="actual current named place"):
            _act(
                reopened,
                "query_srd_information_spell",
                caster_state="hero",
                spell="detect_evil_and_good",
                current_place="elsewhere",
                current_plane="material",
            )
        assert reopened.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(reopened).execute_action(query_command) == query
        assert reopened.snapshot("casting").state == saved.state
        _act(
            reopened,
            "record_srd_information_world_facts",
            **_facts(
                candidates=[_candidate(crossings=[{"material": "stone", "thickness_feet": 1}])]
            ),
        )
        blocked, _ = _act(
            reopened,
            "query_srd_information_spell",
            caster_state="hero",
            spell="detect_evil_and_good",
            current_place="gate",
            current_plane="material",
        )
        assert information._summary(blocked)["Present"] is False
        _act(
            reopened,
            "record_srd_information_world_facts",
            **_facts(candidates=[_candidate(plane="astral")]),
        )
        other_plane, _ = _act(
            reopened,
            "query_srd_information_spell",
            caster_state="hero",
            spell="detect_evil_and_good",
            current_place="gate",
            current_plane="material",
        )
        assert information._summary(other_plane)["Present"] is False
        ended, _ = _act(
            reopened,
            "dismiss_srd_information_spell",
            caster_state="hero",
            spell="detect_evil_and_good",
        )
        assert ended.status is ActionStatus.COMMITTED
        assert (
            reopened.snapshot("casting").state[f"{SRD}.entities"]["hero"]["concentration_spell_id"]
            is None
        )
        after_end = reopened.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="not active"):
            _act(
                reopened,
                "query_srd_information_spell",
                caster_state="hero",
                spell="detect_evil_and_good",
                current_place="gate",
                current_plane="material",
            )
        assert reopened.snapshot("casting").state == after_end.state


def test_nature_owner_selects_actual_native_cr_and_three_physical_categories(scope):
    from tests import test_srd_spell_wave4_divination as native
    from tests.srd_full.test_srd_extended_casting import _begin

    state, data, params = native._case("commune_with_nature")
    state[f"{SRD}.state"].pop("spatial_scene")
    state[f"{SRD}.entities"]["guard"].update(
        creature_type="fiend", challenge_rating_eighths=80, is_alive=True
    )
    candidates = [
        _candidate(),
        _candidate("well", subject_state_id="", body_of_water=True, distance_feet=250),
        _candidate("village", subject_state_id="", settlement=True, distance_feet=301),
    ]
    _run(
        "record_srd_information_world_facts",
        state,
        **_facts("commune_with_nature", candidates, 15840),
    )
    params.update(
        divination_nature_facts=["powerful_creature", "water", "settlements"],
        divination_nature_setting="natural_underground",
        divination_subject="",
    )
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
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
    _run("cast_spell", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
    answer = state[f"{SRD}.entities"]["hero"]["last_nature_divination"]["facts"]
    assert len(answer) == 3
    assert "guard" in answer[0] and "well" in answer[1]
    assert "none" in answer[2] and "village" not in str(answer)
    assert state[f"{SRD}.entities"]["hero"]["last_nature_divination"]["radius"] == 300
    recall = _run("query_srd_nature_divination", state, caster_state="hero").result["context"]
    assert recall["_nature_known_facts"] == answer
    runtime, encoded = _runtime()
    assert _load_runtime(owned.bind_runtime_consumers(encoded.decode())) == runtime
    restored = _load_runtime(owned.restore_runtime(encoded.decode()))
    original, _ = _source_runtime()
    for name, alias in owned.ALIASES.items():
        assert restored["tools"][name] == runtime["tools"][alias] == original["tools"][name]
    for name, tool in original["tools"].items():
        assert restored["tools"][name] == tool, name
    candidate_schema = owned._fact_parameter()["items"]["properties"]
    assert not {
        "eligible",
        "found",
        "challenge_rating",
        "creature_type",
        "spell_school",
        "suppressed",
    }.intersection(candidate_schema)
