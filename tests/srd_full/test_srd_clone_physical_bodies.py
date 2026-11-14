"""A real Clone transfers into its saved physical body while keeping one current individual."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_body_soul_spells as clone

budget = clone.budget
SRD = clone.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from clone._compiled_owner.__wrapped__(monkeypatch)


def _hero_scene(state):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "hero"
    )


def test_actual_cast_profile_drives_new_body_geometry_and_preserves_current_individual():
    state, data, params = clone._case()
    original = deepcopy(_hero_scene(state))
    clone._cast(state, data, params)
    profile = deepcopy(clone._body(state)["physical_profile"])
    assert profile["footprint"] == original["footprint"] and profile["height"] == original["height"]
    assert profile["size_rank"] == 2
    assert profile["appearance"] is None and profile["species"] is None
    clone._mature(state)
    # The admitted current scene is the physical-fact authority; this is a later body, not a
    # supplied transfer result. The real death owner must use the prior saved casting body.
    _hero_scene(state).update(footprint={"kind": "box", "half_extents": [4, 4]}, height=9)
    hero = state[f"{SRD}.entities"]["hero"]
    inventory = deepcopy(hero["inventory"])
    resources = deepcopy(hero["resources"])
    identity_ids = set(state[f"{SRD}.entities"])
    clone._kill(state, data)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["is_alive"] is True
    assert _hero_scene(state)["footprint"] == profile["footprint"]
    assert _hero_scene(state)["height"] == profile["height"]
    assert _hero_scene(state)["metadata"]["body_physical_profile"] == profile
    assert hero["current_body_physical_profile"] == profile
    assert hero["resources"] == resources and hero["max_hp"] == 40
    assert hero["inventory"] == [] and hero["prior_body_sources"]["hero"]["inventory"] == inventory
    assert set(state[f"{SRD}.entities"]) == identity_ids
    # Actual later movement consumes the restored scene body; it does not read the dead body's
    # changed dimensions or allocate another autonomous creature.
    budget._run(
        "move_srd_scene_creature",
        state,
        actor_state="hero",
        destination=[8, 0, 0],
        mode="walk",
        difficult_terrain=False,
    )
    assert _hero_scene(state)["position"] == [8, 0, 0]
    assert _hero_scene(state)["footprint"] == profile["footprint"]


def test_legacy_body_without_casting_profile_does_not_guess_or_cancel_actual_death():
    state, data, params = clone._case()
    clone._cast(state, data, params)
    clone._mature(state)
    source_id = clone._body(state)["source_id"]
    # Reproduce the actual saved legacy format at its owning lookup boundary.
    clone._body(state).pop("physical_profile")
    clone._kill(state, data)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["is_alive"] is False and hero["death_tick"] is not None
    assert hero["clone_transfer_pending"] is True and clone._body(state)["transferred"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="no recorded casting-body profile"):
        budget._run("select_srd_clone_body", state, target_state="hero", source_id=source_id)
    assert state == before


def test_real_paid_clone_public_death_reopen_and_retry(tmp_path, monkeypatch):
    clone.test_public_clone_paid_cast_reopen_mature_actual_death_and_retries(tmp_path, monkeypatch)
