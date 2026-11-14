"""Plant Growth uses real paid modes, source Sphere cost, exclusions and saved crop yield."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_plant_growth import PROFILE, PUBLIC_TOOLS, YEAR_TICKS, extend_runtime
from scripts.srd_full.srd_plant_growth_consumers import bind_consumers

from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_magic_jar as jar
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene

SRD = jar.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    root = os.environ.get("FC14_PLANT_RUNTIME_ROOT")
    assert root, "Select the real source-composed Plant Growth owner closure"
    monkeypatch.setenv("FC14_JAR_RUNTIME_ROOT", root)
    yield from jar._compiled.__wrapped__(monkeypatch)


def _case(*, mode="overgrowth", exclusions=None):
    state, data, params = jar.cards._spell_case("plant_growth")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    crop = _entity(
        "crop", [-5, 3, 0], nonmagical_plant=True, crop_ready=True, crop_normal_food_pounds=5
    )
    crop.update(height=1, movement="passable", visibility="clear")
    crop["metadata"]["crop_harvest_extent"] = [1, 1, 0.5]
    state[f"{SRD}.state"]["spatial_scene"] = _scene(
        _entity("hero", [-5, 0, 0], participant_ref="hero"),
        crop,
        _entity("distant-plant", [2700, 0, 0], nonmagical_plant=True),
        _entity("magic-plant", [0, 5, 0], nonmagical_plant=False),
    )
    params.update(
        target="hero",
        plant_growth_mode=mode,
        plant_growth_point=[0, 0, 0],
        plant_growth_exclusions=exclusions or [],
    )
    return state, data, params


def _run(name, state, data, **params):
    return jar._run(name, state, data, **params)


def _crop(state):
    return next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "crop")


def _cost(state, data, start, end, *, difficult=False, plane="material", airborne=False):
    return _run(
        "srd_information_movement_cost",
        state,
        data,
        start=start,
        end=end,
        base_difficult=difficult,
        plane=plane,
        ignore_ground_environments=airborne,
    ).result["context"]["_route_cost"]


def test_actual_paid_overgrowth_changes_only_real_normal_plants_and_literal_sphere():
    state, data, params = _case()
    slots = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"]
    _run("cast_spell_outside_combat", state, data, **params)
    assert _crop(state)["metadata"]["plant_growth_overgrown"] is True
    scene = state[f"{SRD}.state"]["spatial_scene"]
    assert all(
        "plant_growth_overgrown" not in e["metadata"]
        for e in scene["entities"]
        if e["id"] in ("magic-plant", "distant-plant")
    )
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == slots - 1
    assert _cost(state, data, [-110, 0, 0], [-90, 0, 0]) == pytest.approx(50)
    assert _cost(state, data, [-5, 0, 0], [5, 0, 0], difficult=True) == pytest.approx(50)
    assert _cost(state, data, [70, 0, 40], [80, 0, 40], airborne=True) == pytest.approx(40)
    assert _cost(state, data, [-5, 0, 0], [5, 0, 0], plane="astral") == pytest.approx(10)
    # Bounding Box corners do not become an invented larger area.
    assert _cost(state, data, [80, 80, 0], [90, 80, 0]) == pytest.approx(10)
    zone = scene["zones"][0]
    assert zone["metadata"]["ongoing_duration"] is False
    assert zone["metadata"]["dispel_ongoing"] is False


def test_actual_exclusion_intervals_and_independent_overgrowth_do_not_multiply():
    box = {"kind": "box", "min": [-2, -2, -10], "max": [2, 2, 10]}
    state, data, params = _case(exclusions=[box])
    _run("cast_spell_outside_combat", state, data, **params)
    assert _cost(state, data, [-5, 0, 0], [5, 0, 0]) == pytest.approx(28)
    _run("cast_spell_outside_combat", state, data, **params)
    assert _cost(state, data, [-5, 0, 0], [5, 0, 0]) == pytest.approx(28)
    moving = _run(
        "move_srd_scene_creature",
        state,
        data,
        actor_state="hero",
        destination=[5, 0, 0],
        mode="walk",
        difficult_terrain=False,
    )
    assert moving.result["context"]["_scene_movement_cost"] == pytest.approx(28)
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == pytest.approx(28)


@pytest.mark.parametrize("invalid", ["range", "exclusion", "unsupported"])
def test_illegal_geometry_rejects_atomically_before_actual_cost(invalid):
    state, data, params = _case()
    if invalid == "range":
        params["plant_growth_point"] = [151, 0, 0]
    elif invalid == "exclusion":
        params["plant_growth_exclusions"] = [
            {"kind": "box", "min": [99, -2, -2], "max": [105, 2, 2]}
        ]
    else:
        params["plant_growth_exclusions"] = [
            {"kind": "cylinder", "center": [0, 0, 0], "radius": 1, "height": 1}
        ]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, data, **params)
    assert state == before


def _enrich(state, data, params):
    _run(
        "begin_srd_spell_cast",
        state,
        data,
        **(_begin(params) | {"plant_growth_mode": "enrichment"}),
    )
    _run(
        "continue_srd_spell_cast",
        state,
        data,
        caster_state="hero",
        elapsed_rounds=4800,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell_outside_combat", state, data, **params)


def test_enrichment_requires_actual_eight_hours_and_real_harvest_uses_saved_yield():
    state, data, params = _case(mode="enrichment")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="eight-hour|longer"):
        _run("cast_spell_outside_combat", state, data, **params)
    assert state == before
    _enrich(state, data, params)
    now = state[f"{SRD}.state"]["lasting_tick"]
    metadata = _crop(state)["metadata"]
    assert now == 9600 and metadata["plant_growth_enriched_until"] == now + YEAR_TICKS
    original_deadline = metadata["plant_growth_enriched_until"]
    _enrich(state, data, params)
    assert _crop(state)["metadata"]["plant_growth_enriched_until"] == original_deadline
    harvest = _run(
        "harvest_srd_enriched_crop",
        state,
        data,
        actor_state="hero",
        crop_id="crop",
        output_id="food",
    )
    assert harvest.result["context"]["_crop_food_pounds"] == 10
    scene = state[f"{SRD}.state"]["spatial_scene"]
    food = next(e for e in scene["entities"] if e["id"] == "food")
    assert food["metadata"]["food_pounds"] == 10
    assert food["metadata"]["nonmagical_food"] is True
    assert _crop(state)["metadata"]["crop_ready"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="ready"):
        _run(
            "harvest_srd_enriched_crop",
            state,
            data,
            actor_state="hero",
            crop_id="crop",
            output_id="food2",
        )
    assert state == before


def test_year_deadline_uses_real_clock_and_expired_crop_yield_is_ordinary():
    state, data, params = _case(mode="enrichment")
    _enrich(state, data, params)
    # Move only the real existing lasting clock via its owning action in bounded day intervals.
    for _ in range(52):
        _run("advance_srd_lasting_time", state, data, elapsed_rounds=100000)
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=56000)
    harvest = _run(
        "harvest_srd_enriched_crop",
        state,
        data,
        actor_state="hero",
        crop_id="crop",
        output_id="food",
    )
    assert harvest.result["context"]["_crop_food_pounds"] == 5


def test_family_final_consumers_repeat_without_duplicate_routes_or_casting_hooks():
    text = (jar.budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = bind_consumers(extend_runtime(text))
    second = bind_consumers(extend_runtime(first))
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(second, Loader=yaml.CSafeLoader)
    assert PROFILE["duration_rounds"] == 0


def test_speak_with_plants_removes_only_growth_cost_and_preserves_difficult_ground():
    state, data, params = _case()
    data["spellcasting_grants"][0]["spells"].append("speak_with_plants")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("speak_with_plants")
    _run("cast_spell_outside_combat", state, data, **params)
    _run("cast_spell_outside_combat", state, data, **(params | {"spell": "speak_with_plants"}))
    _run("command_srd_plants", state, data, caster_state="hero", command="ordinary_terrain")
    assert _cost(state, data, [-5, 0, 0], [5, 0, 0]) == pytest.approx(10)
    assert _cost(state, data, [-5, 0, 0], [5, 0, 0], difficult=True) == pytest.approx(20)
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=100)
    assert _cost(state, data, [-5, 0, 0], [5, 0, 0]) == pytest.approx(40)


def _public_fixture(monkeypatch):
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    assert parent
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
    monkeypatch.setattr(jar.binding, "PUBLIC_TOOLS", PUBLIC_TOOLS)
    monkeypatch.setattr(jar.public_casting, "bundled_srd_archive", jar.binding._archive)
    monkeypatch.setattr(
        jar.public_casting, "_spell_case", lambda *_a, **_k: _case(mode="enrichment")
    )
    drafts = bundled_packages._srd_drafts

    def selected_drafts():
        primary, reference = drafts()
        runtime = yaml.load(
            (jar.budget.ROOT / "runtime/ruleset.yaml").read_text(), Loader=yaml.CSafeLoader
        )
        record = deepcopy(primary.record)
        record["tools"] = [t for t in record["tools"] if t["name"] in runtime["tools"]]
        payloads = dict(primary.payloads)
        path = record["content_declarations"]
        declarations = json.loads(payloads[path])
        for inclusion in declarations["inclusions"]:
            links = inclusion.get("links", {})
            if "tools" in links:
                links["tools"] = [n for n in links["tools"] if n in runtime["tools"]]
        payloads[path] = canonical_json_bytes(declarations)
        return NormalizedPackageDraft(record, payloads, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", selected_drafts)


def test_public_real_eight_hour_cast_then_harvest_reopen_and_retry(tmp_path, monkeypatch):
    _public_fixture(monkeypatch)
    database = tmp_path / "plant-growth.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = jar.public_casting._create_spell_campaign(owner, "plant_growth")
        original = owner.snapshot("casting")
        began, begin_command = _act(
            owner, "begin_srd_spell_cast", **(_begin(params) | {"plant_growth_mode": "enrichment"})
        )
        assert began.status is ActionStatus.COMMITTED, began.result
        channel = owner.snapshot("casting")
        assert (
            channel.state[f"{SRD}.entities"]["hero"]["extended_casting"]["required_rounds"] == 4800
        )
        assert owner.execute_action(begin_command) == began and owner.snapshot("casting") == channel
    with AuthorityOwner.open(database, "casting-owner") as owner:
        continued, continuation = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=4800,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED, continued.result
        cast, command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["spell_slot_3"] == (
            original.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] - 1
        )
        assert _crop(saved.state)["metadata"]["plant_growth_enriched_from"] == 9600
        assert (
            owner.execute_action(continuation) == continued and owner.snapshot("casting") == saved
        )
        assert owner.execute_action(command) == cast and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        harvested, harvest_command = _act(
            owner, "harvest_srd_enriched_crop", actor_state="hero", crop_id="crop", output_id="food"
        )
        assert harvested.status is ActionStatus.COMMITTED, harvested.result
        final = owner.snapshot("casting")
        scene = final.state[f"{SRD}.state"]["spatial_scene"]
        assert (
            next(e for e in scene["entities"] if e["id"] == "food")["metadata"]["food_pounds"] == 10
        )
        assert (
            owner.execute_action(harvest_command) == harvested
            and owner.snapshot("casting") == final
        )
        assert owner.execute_action(command) == cast and owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final


def test_partial_plant_body_at_sphere_boundary_fails_clearly_without_cost():
    state, data, params = _case()
    _crop(state)["position"] = [100, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="boundary-spanning"):
        _run("cast_spell_outside_combat", state, data, **params)
    assert state == before


def test_actual_harvest_spends_one_available_action_and_consumes_ready_crop():
    state, data, _ = _case()
    combat = state[f"{SRD}.combat"]
    combat.update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        action_available=True,
        normal_action_spent=False,
    )
    _run(
        "harvest_srd_enriched_crop",
        state,
        data,
        actor_state="hero",
        crop_id="crop",
        output_id="food",
    )
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    assert _crop(state)["metadata"]["crop_ready"] is False
