"""Point and object illumination casts never require a fabricated creature target."""

import hashlib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_illumination import _casting_tool

from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_result_cards import _spell_case
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene

PARENT = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-illumination-targetless/input-1"))


@lru_cache
def _runtime():
    payload = (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == (
        "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
    )
    runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
    # Keep the completed parent's source/removal adapters, including its normalized
    # one-element object-source parameter. Apply only the two actual owning hunks.
    authored = _casting_tool()
    actual = runtime["tools"]["cast_spell_illumination_spell"]
    index = next(i for i, p in enumerate(actual["parameters"]) if p["name"] == "target_state")
    actual["parameters"][index] = deepcopy(
        next(p for p in authored["parameters"] if p["name"] == "target_state")
    )
    authored_invisible = next(
        e
        for e in authored["effects"]
        if e.get("condition") == "_light_profile.illumination_mode == 'invisible'"
    )
    actual_invisible = next(
        e
        for e in actual["effects"]
        if e.get("condition") == "_light_profile.illumination_mode == 'invisible'"
    )
    fallback = next(
        e for e in authored_invisible["on_true"] if e.get("condition") == "len(_invis_targets) == 0"
    )
    index = next(
        i
        for i, e in enumerate(actual_invisible["on_true"])
        if e.get("condition") == "len(_invis_targets) == 0"
    )
    actual_invisible["on_true"][index] = deepcopy(fallback)
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
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _actual_owner(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)


def _case(spell="darkness", *, object_id=""):
    state, data, params = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.state"] = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": _scene(
            _entity("hero", [0, 0, 0], participant_ref="hero"),
            _entity("guard", [5, 0, 0], participant_ref="guard"),
            _entity("other", [0, 5, 0], participant_ref="other"),
            _entity(
                "orb",
                [5, 0, 0],
                is_object=True,
                size_rank=2,
                carried_by="",
                worn_by="",
                opaque_cover=False,
            ),
        ),
    }
    params.pop("target", None)
    params.update(illumination_position=[40, 0, 0], illumination_object=object_id)
    return state, data, params


def _records(state):
    return [
        r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("kind") == "illumination_spell"
    ]


@pytest.mark.parametrize(
    "object_id,origin,source", [("", [40, 0, 0], ""), ("orb", [5, 0, 0], "orb")]
)
def test_paid_targetless_darkness_uses_actual_point_or_object_and_slot(object_id, origin, source):
    state, data, params = _case(object_id=object_id)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert result.result["status"] == "committed"
    record = _records(state)[0]
    assert record["source"] == source and record["origin"] == origin
    assert record["dark_radius"] == 15 and record["slot_level"] == 2
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    context = _run(
        "resolve_srd_information_light", state, point=origin, plane="material", ambient="bright"
    ).result["context"]
    assert context["_information_light"] == "magical_darkness"


@pytest.mark.parametrize("invalid", ["range", "holder", "plane"])
def test_targetless_darkness_rejects_actual_invalid_source_without_cost_or_source_leak(invalid):
    state, data, params = _case(object_id="orb" if invalid != "range" else "")
    orb = state[f"{SRD}.state"]["spatial_scene"]["entities"][-1]
    if invalid == "range":
        params["illumination_position"] = [60.01, 0, 0]
    elif invalid == "holder":
        orb["metadata"]["carried_by"] = "hero"
    else:
        orb["metadata"]["plane"] = "ethereal"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


@pytest.mark.parametrize("spell", ["invisibility", "greater_invisibility"])
def test_actual_scalar_invisible_creature_target_semantics_are_preserved(spell):
    state, data, params = _case(spell)
    params["target"] = "guard"
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert "invisible" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "invisible" not in state[f"{SRD}.entities"]["hero"]["conditions"]
    assert _records(state)[0]["source"] == "guard"


def test_invisibility_without_any_real_creature_target_fails_without_cost():
    state, data, params = _case("invisibility")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual creature"):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def test_upcast_invisibility_uses_actual_target_array_without_unrelated_scalar():
    state, data, params = _case("invisibility")
    params.update(slot_resource="spell_slot_3", targets=["guard", "other"])
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert {r["source"] for r in _records(state)} == {"guard", "other"}
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    for actor in ("guard", "other"):
        assert "invisible" in state[f"{SRD}.entities"][actor]["conditions"]


@lru_cache
def _test_archive():
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


def test_actual_archive_paid_targetless_darkness_reopen_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    monkeypatch.setattr(public_casting, "ROOT", PARENT)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    database = tmp_path / "targetless-darkness.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "darkness")
        assert "target" not in params
        cast, command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        records = _records(saved.state)
        assert records[0]["source"] == "" and records[0]["origin"] == [40, 0, 0]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(command) == cast
        assert reopened.snapshot("casting") == saved
