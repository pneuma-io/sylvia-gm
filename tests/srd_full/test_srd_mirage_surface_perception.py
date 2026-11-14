"""Actual Ethereal sight qualifies nearest terrain surfaces, preserving its fixed sixty feet."""

from copy import deepcopy

import pytest
from scripts.srd_full.srd_mirage_arcane import PUBLIC_TOOLS

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_mirage_arcane as mirage
from tests import test_srd_mirage_geometry as geometry
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_illusions import _grant_other_spell


@pytest.fixture(autouse=True)
def _actual_geometry_runtime(monkeypatch):
    monkeypatch.setattr(mirage, "_runtime", geometry._runtime)
    mirage._mechanic.cache_clear()
    monkeypatch.setattr(budget, "_mechanic", mirage._mechanic)
    monkeypatch.setattr(
        cards, "_spell_definitions", lambda: geometry._runtime()[0]["definitions"]["spells"]
    )


def _projected_case(distance):
    state, data, _ = geometry._case()
    hill = geometry._hill(state)
    hill["volume"]["max"][0] = 100
    observer = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "hero"
    )
    observer["position"] = [8 - distance, 0, 4]
    mirage._cast(
        state, mirage_side_feet=120, mirage_terrain_changes=[geometry._spec(maximum=[100, 5, 8])]
    )
    parameters = _grant_other_spell(state, data, "etherealness")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"]["spell"] == "etherealness"
    return state


@pytest.mark.parametrize("distance", [28, 60])
def test_paid_etherealness_sees_near_surface_when_actual_box_center_is_beyond_sixty(distance):
    state = _projected_case(distance)
    result = _run("perceive_srd_mirage_terrain", state, observer_state="hero", subject="hill")
    rows = mirage._summary(result)
    assert rows["Visible"] is True and rows["Terrain"] == "clear meadow"
    assert rows["Physical feel"] == "" and rows["Within tactile reach"] is False
    assert result.result["context"]["_illumination_point_distance"] == distance


def test_surface_beyond_sixty_retains_actual_ethereal_reach_and_atomic_rejection():
    state = _projected_case(61)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="sixty"):
        _run("perceive_srd_mirage_terrain", state, observer_state="hero", subject="hill")
    assert state == before


def test_blinded_actual_ethereal_observer_cannot_see_near_material_surface():
    state = _projected_case(28)
    _run("apply_srd_condition", state, target="hero", condition="blinded")
    rows = mirage._summary(
        _run("perceive_srd_mirage_terrain", state, observer_state="hero", subject="hill")
    )
    assert rows["Visible"] is False and rows["Terrain"] == ""
    assert rows["True form visible"] is False


def test_strict_paid_public_sources_reopen_retry_then_see_actual_nearest_surface(
    tmp_path, monkeypatch
):
    from sylvia_gm.application import bundled_packages
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_dream_spell as dream
    from tests import test_srd_public_spell_casting as public
    from tests import test_srd_spell_appearance as appearance
    from tests.srd_full.test_srd_extended_casting import _begin
    from tests.srd_full.test_srd_npc_spell_casting import _act

    ethereal_parameters = {}

    def case(_spell):
        state, data, parameters = geometry._case()
        geometry._hill(state)["volume"]["max"][0] = 100
        actor = next(
            row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "hero"
        )
        actor["position"] = [-20, 0, 4]
        parameters.update(
            mirage_side_feet=120, mirage_terrain_changes=[geometry._spec(maximum=[100, 5, 8])]
        )
        ethereal_parameters.update(_grant_other_spell(state, data, "etherealness"))
        return state, data, parameters

    monkeypatch.setattr(bundled_packages, "_srd_drafts", dream._linked_drafts)
    monkeypatch.setattr(appearance, "_runtime", geometry._runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", PUBLIC_TOOLS)
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)
    monkeypatch.setattr(public, "_spell_case", case)
    database = tmp_path / "mirage-surface.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "mirage_arcane")
        begun, _ = _act(owner, "begin_srd_spell_cast", **_begin(parameters))
        completed, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=100,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        terrain, _ = _act(owner, "cast_spell_outside_combat", **parameters)
        projected, projected_command = _act(
            owner, "cast_spell_outside_combat", **ethereal_parameters
        )
        assert (
            begun.status
            is completed.status
            is terrain.status
            is projected.status
            is ActionStatus.COMMITTED
        )
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 1
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(projected_command) == projected
        assert owner.snapshot("casting").state == saved.state
        seen, seen_command = _act(
            owner, "perceive_srd_mirage_terrain", observer_state="hero", subject="hill"
        )
        assert seen.status is ActionStatus.COMMITTED
        rows = mirage._summary(seen)
        assert rows["Visible"] is True and rows["Terrain"] == "clear meadow"
        assert rows["Within tactile reach"] is False
        after = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(seen_command) == seen
        assert owner.snapshot("casting").state == after.state
