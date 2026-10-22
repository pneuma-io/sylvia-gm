"""Ward sight queries use genuine sources, strict rays and exact observer exemptions."""

import json
from copy import deepcopy
from functools import lru_cache

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_guards_wards_obscuration import EXPORTS, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_guards_wards as wards
from tests import test_srd_guards_wards_web as web
from tests import test_srd_spell_object_content as objects

SRD = objects.SRD


@lru_cache
def _runtime():
    text = extend_runtime(web._runtime()[0].decode())
    assert extend_runtime(text) == text
    runtime = _load_runtime(text)
    assert not (errors := validate_ruleset_data(runtime)), errors
    return text.encode(), runtime


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="e" * 64,
        runtime_bytes=raw,
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
    monkeypatch.setattr(objects, "_mechanic", _mechanic)


def _case(**choices):
    state = objects._state()
    objects._access(state)
    sid = wards._cast_ward(
        state,
        save_dc=40,
        stairs=[{"min": [2, 20, 0], "max": [12, 28, 15]}],
        **choices,
    )
    return state, sid


def _ray(state, start, end, **choices):
    result = objects._run(
        "resolve_srd_guards_wards_obscuration",
        state,
        start=start,
        end=end,
        plane=choices.get("plane", "material"),
        observer_state=choices.get("observer", "guard"),
    )
    return tuple(result[name] for name in EXPORTS)


def test_actual_fog_crossing_and_stair_web_are_distinct_ray_results():
    state, _ = _case()
    assert _ray(state, [-1, 4, 4], [11, 4, 4]) == (True, False)
    assert _ray(state, [1, 24, 4], [13, 24, 4]) == (False, True)
    assert _ray(state, [5, 4, 4], [5, 24, 4]) == (True, True)
    assert _ray(state, [1, 24, 4], [13, 24, 4], plane="ethereal") == (False, False)


def test_strict_face_tangency_endpoint_and_real_point_membership():
    state, _ = _case()
    assert _ray(state, [-1, 0, 4], [11, 0, 4]) == (False, False)
    assert _ray(state, [-1, 4, 4], [0, 4, 4]) == (False, False)
    assert _ray(state, [5, 4, 4], [5, 4, 4]) == (True, False)
    assert _ray(state, [5, 24, 4], [5, 24, 4]) == (False, True)
    assert _ray(state, [0, 4, 4], [0, 4, 4]) == (False, False)


def test_actual_designation_and_spoken_password_exempt_only_their_observer():
    state, sid = _case(designated_creatures=["other"])
    assert _ray(state, [5, 4, 4], [5, 24, 4], observer="other") == (False, False)
    assert _ray(state, [5, 4, 4], [5, 24, 4]) == (True, True)
    objects._run(
        "speak_srd_guards_wards_password",
        state,
        actor_state="guard",
        source_id=sid,
        password="silver threshold",
    )
    assert _ray(state, [5, 4, 4], [5, 24, 4]) == (False, False)
    assert _ray(state, [5, 4, 4], [5, 24, 4], observer="hero") == (True, True)


def test_exact_group_removal_preserves_other_obscuration_and_saved_replay_is_pure():
    state, sid = _case()
    objects._run("end_srd_guards_wards_effect", state, source_id=sid, group="corridors")
    assert _ray(state, [5, 4, 4], [5, 24, 4]) == (False, True)
    reopened = json.loads(json.dumps(state))
    before = deepcopy(reopened)
    assert _ray(reopened, [5, 4, 4], [5, 24, 4]) == (False, True)
    assert _ray(reopened, [5, 4, 4], [5, 24, 4]) == (False, True)
    assert reopened == before
    objects._run("end_srd_guards_wards_effect", reopened, source_id=sid, group="stairs")
    assert _ray(reopened, [5, 4, 4], [5, 24, 4]) == (False, False)


def test_source_suppression_restoration_and_actual_expiry_qualify_both_groups():
    state, sid = _case()
    state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["suppressed"] = True
    assert _ray(state, [5, 4, 4], [5, 24, 4]) == (False, False)
    state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["suppressed"] = False
    assert _ray(state, [5, 4, 4], [5, 24, 4]) == (True, True)
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert _ray(state, [5, 4, 4], [5, 24, 4]) == (False, False)


def test_no_ward_sources_returns_fresh_false_values_without_state_changes():
    state = objects._state()
    before = deepcopy(state)
    assert _ray(state, [5, 4, 4], [5, 24, 4]) == (False, False)
    assert state == before
