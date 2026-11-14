"""Real Etherealness sources return through exact Dispel and whole-cast owners."""

import os
from copy import deepcopy
from math import dist
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_spell_removals import extend_runtime

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_progressive_afflictions import _add_spell
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave3_information import _case as _information_case


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    """An isolated owner may supply a frozen composed runtime before root integration."""
    runtime_root = os.environ.get("FC14_SOURCE_RUNTIME_ROOT")
    if runtime_root:
        monkeypatch.setattr(budget, "ROOT", Path(runtime_root))
        monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(runtime_root))
    budget._mechanic.cache_clear()
    bundled_packages.bundled_srd_archive.cache_clear()
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    yield
    budget._mechanic.cache_clear()
    bundled_packages.bundled_srd_archive.cache_clear()


def _fresh_case():
    # The validated Wizard fixture supplies actual abilities/slots; the named
    # spell and its rules still come from the composed package's pinned source.
    state, data, params = _information_case("mind_blank")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(prepared_spells=["etherealness", "dispel_magic"], hp=500, max_hp=500, size_rank=2)
    data["spellcasting_grants"][0]["spells"] = ["etherealness", "dispel_magic"]
    data["wizard_spellbook"] = ["etherealness", "dispel_magic"]
    state[f"{SRD}.entities"]["guard"].update(hp=500, max_hp=500, temp_hp=0)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [5, 0, 0]
    scene["zones"] += [
        {
            "id": f"ambient_{plane}",
            "map_id": "information",
            "shape": {"kind": "box", "min": [-1000, -1000, -100], "max": [1000, 1000, 100]},
            "metadata": {"plane": plane, "lighting": "bright"},
        }
        for plane in ("material", "ethereal")
    ]
    params.update(spell="etherealness", slot_resource="spell_slot_8", targets=["guard"])
    return state, data, params


def _case():
    state, data, params = _fresh_case()
    _cast(state, data, params)
    return state, data, params


def _cast(state, data, params):
    state[f"{SRD}.entities"]["hero"]["spell_slot_cast_this_turn"] = False
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **params)


def _scene_entity(state, actor):
    return next(
        entity
        for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if entity["id"] == actor
    )


def test_removal_emitter_keeps_exact_phase_owner_hooks_stable_on_rebuild():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text(encoding="utf-8")
    first = extend_runtime(text)
    second = extend_runtime(first)
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    assert yaml.load(first, Loader=loader) == yaml.load(second, Loader=loader)


def test_actual_upcast_source_identity_spans_every_phase_recipient():
    state, _, _ = _case()
    sources = [state[f"{SRD}.entities"][actor]["travel_ethereal"] for actor in ("hero", "guard")]
    assert {source["cast_id"] for source in sources} == {"srd_cast:1"}
    assert all(
        source["source_caster_id"] == "hero" and source["ongoing_duration"] for source in sources
    )
    assert all(source["slot_level"] == 8 for source in sources)


def test_whole_cast_dismissal_returns_actual_group_without_spending_action():
    state, _, _ = _case()
    hero = deepcopy(state[f"{SRD}.entities"]["hero"])
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:1")
    for actor in ("hero", "guard"):
        assert state[f"{SRD}.entities"][actor]["travel_ethereal"] is None
        assert _scene_entity(state, actor)["metadata"]["plane"] == "material"
    assert state[f"{SRD}.entities"]["hero"]["resources"] == hero["resources"]


@pytest.mark.parametrize("seed,ended", [(0, False), (5, True)])
def test_real_lower_slot_dispel_checks_actual_saved_level_and_keeps_other_recipient(
    monkeypatch,
    seed,
    ended,
):
    state, _, _ = _case()
    data, params = _add_spell(state, "dispel_magic")
    monkeypatch.setattr(dice, "_rng", Random(seed))
    _cast(state, data, params | {"slot_resource": "spell_slot_3"})
    assert (state[f"{SRD}.entities"]["guard"]["travel_ethereal"] is None) is ended
    assert _scene_entity(state, "guard")["metadata"]["plane"] == (
        "material" if ended else "ethereal"
    )
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"] is not None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2


def test_equal_slot_dispel_then_dismissal_returns_only_remaining_cast_recipient():
    state, _, _ = _case()
    data, params = _add_spell(state, "dispel_magic")
    _cast(state, data, params | {"slot_resource": "spell_slot_8"})
    assert state[f"{SRD}.entities"]["guard"]["travel_ethereal"] is None
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:1")
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"] is None
    assert state[f"{SRD}.state"]["travel_phase_targets"] == []


def test_old_phase_dismissal_requires_one_exact_cast_and_preserves_same_caster_sibling():
    state, original_data, original_params = _case()
    data, params = _add_spell(state, "dispel_magic")
    _cast(state, data, params | {"target": "hero", "slot_resource": "spell_slot_8"})
    state[f"{SRD}.entities"]["hero"]["prepared_spells"] = ["etherealness"]
    caster_only = {key: value for key, value in original_params.items() if key != "targets"}
    _cast(state, original_data, caster_only)
    assert state[f"{SRD}.entities"]["guard"]["travel_ethereal"]["cast_id"] == "srd_cast:1"
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"]["cast_id"] == "srd_cast:2"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="unambiguous"):
        budget._run("dismiss_srd_etherealness", state, caster_state="hero")
    assert state == before
    budget._run("dismiss_srd_etherealness", state, caster_state="hero", cast_id="srd_cast:1")
    assert state[f"{SRD}.entities"]["guard"]["travel_ethereal"] is None
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"]["cast_id"] == "srd_cast:2"


def test_exact_dismissal_uses_existing_return_placement_and_real_shunt_damage():
    state, _, _ = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"] = [
        {
            "id": "material_rock",
            "map_id": "information",
            "active": True,
            "volume": {"kind": "box", "min": [-2, -2, -2], "max": [2, 2, 2]},
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": {"plane": "material"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    ]
    start = _scene_entity(state, "hero")["position"]
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:1")
    entity = _scene_entity(state, "hero")
    displacement = dist(start, entity["position"])
    assert displacement > 0
    assert entity["metadata"]["plane"] == "material"
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 500 - int(displacement * 2)


def test_phase_dismissal_never_groups_an_unidentified_legacy_recipient_with_known_cast():
    state, _, _ = _case()
    source = state[f"{SRD}.entities"]["guard"]["travel_ethereal"]
    source.pop("cast_id")
    source.pop("source_caster_id")
    source["ongoing_duration"] = False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="unambiguous"):
        budget._run("dismiss_srd_etherealness", state, caster_state="hero")
    assert state == before
    budget._run("dismiss_srd_etherealness", state, caster_state="hero", cast_id="srd_cast:1")
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"] is None
    assert state[f"{SRD}.entities"]["guard"]["travel_ethereal"] == source


def test_strict_public_phase_cast_reopens_and_exact_dismissal_retries_without_reentry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _fresh_case())
    database = tmp_path / "etherealness.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, "etherealness", target_overrides={"hp": 500, "max_hp": 500}
        )
        receipt, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        identity = saved.state[f"{SRD}.entities"]["hero"]["travel_ethereal"]["cast_id"]
        assert identity == "srd_cast:1"
        assert owner.execute_action(cast_command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        receipt, command = _act(
            owner, "dismiss_srd_etherealness", caster_state="hero", cast_id=identity
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        settled = owner.snapshot("casting")
        for actor in ("hero", "guard"):
            assert settled.state[f"{SRD}.entities"][actor]["travel_ethereal"] is None
        assert (
            settled.state[f"{SRD}.entities"]["hero"]["resources"]
            == (saved.state[f"{SRD}.entities"]["hero"]["resources"])
        )
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == settled.state
        assert owner.execute_action(cast_command).status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state == settled.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == settled.state


@pytest.mark.parametrize("geometry", ["other_plane", "out_of_range", "solid_path"])
def test_dispel_rejects_illegal_actual_geometry_even_when_caller_distance_claims_near(geometry):
    state, _, _ = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if geometry == "other_plane":
        _scene_entity(state, "guard")["metadata"]["plane"] = "material"
    elif geometry == "out_of_range":
        _scene_entity(state, "guard")["position"] = [150, 0, 0]
    else:
        scene["geometry"] = [
            {
                "id": "ethereal_wall",
                "map_id": "information",
                "active": True,
                "volume": {"kind": "box", "min": [2, -10, -10], "max": [3, 10, 20]},
                "movement": "blocking",
                "visibility": "clear",
                "metadata": {"plane": "ethereal"},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            }
        ]
    data, params = _add_spell(state, "dispel_magic")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _cast(state, data, params | {"target_distance": 0, "slot_resource": "spell_slot_8"})
    assert state == before
