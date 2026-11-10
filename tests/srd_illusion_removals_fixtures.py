"""Real image sources retain cast provenance through exact dismissal and Dispel."""

import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_spell_removals import extend_runtime

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests.test_srd_npc_spell_casting import _act
from tests.test_srd_spell_result_cards import SRD, _casting_headers
from tests.test_srd_spell_wave3_information import _case as _information_case


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
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


def _prepare(state, data, spell):
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"] = [spell]
    hero["spell_slot_cast_this_turn"] = False
    data["spellcasting_grants"][0]["spells"] = [spell]
    data["wizard_spellbook"] = [spell] if _casting_headers()[spell]["level"] else []
    materials = [
        {
            "id": f"{spell}_material_{i}",
            "inventory_item": f"{spell}_material_{i}",
            "kind": "specific",
            "components": [part["id"]],
            "quantity": part["quantity"] * 3,
            "value_cp": part["minimum_value_cp"],
        }
        for i, part in enumerate(_casting_headers()[spell]["material_parts"])
    ]
    hero["spell_materials"] = materials
    hero["inventory"] += [
        m["inventory_item"] for m in materials if m["inventory_item"] not in hero["inventory"]
    ]
    return [m["id"] for m in materials]


def _fresh_case(spell="minor_illusion", *, slot=None):
    state, data, params = _information_case("mind_blank")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "illusion-removal-ambient",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    )
    level = _casting_headers()[spell]["level"]
    params.update(
        spell=spell,
        slot_resource=f"spell_slot_{slot or level}" if level else "cantrip",
        target="hero",
        illusion_position=[10, 0, 0],
        illusion_size=[5, 5, 5],
        illusion_description="A stone screen",
        illusion_subject_kind="object",
        illusion_trigger="a visible creature",
        illusion_performance_seconds=60,
        material_item_ids=_prepare(state, data, spell),
    )
    return state, data, params


def _cast(state, data, params):
    state[f"{SRD}.entities"]["hero"]["spell_slot_cast_this_turn"] = False
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **params)


def _case(spell="minor_illusion", *, slot=None):
    state, data, params = _fresh_case(spell, slot=slot)
    result = _cast(state, data, params)
    source = next(reversed(state[f"{SRD}.state"]["illusion_spell_sources"].values()))
    return state, data, params, source, result


def _proxy(state, source):
    return next(
        e
        for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if e["id"] == source["proxy_id"]
    )


def _source(state, source):
    return state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]


def _dispel(state, data, params, source, *, slot=3, target="hero", select_proxy=True):
    materials = _prepare(state, data, "dispel_magic")
    choices = (
        {"recovery_choice": "illusion", "recovery_source_id": source["id"]} if select_proxy else {}
    )
    return _cast(
        state,
        data,
        params
        | {
            "spell": "dispel_magic",
            "target": target,
            "slot_resource": f"spell_slot_{slot}",
            "material_item_ids": materials,
        }
        | choices,
    )


@pytest.mark.parametrize(
    "spell,slot,ongoing",
    [
        ("minor_illusion", 0, True),
        ("silent_image", 1, True),
        ("major_image", 3, True),
        ("major_image", 4, False),
        ("mislead", 5, True),
        ("programmed_illusion", 6, False),
    ],
)
def test_actual_named_installer_stamps_slot_owner_and_finite_cast_identity(spell, slot, ongoing):
    state, _, _, source, result = _case(spell, slot=slot)
    assert source["spell_level"] == slot and source["source_caster_id"] == "hero"
    assert source["dispel_ongoing"] is True and source["ongoing_duration"] is ongoing
    assert bool(source["cast_id"]) is (spell != "programmed_illusion")
    assert result.result["status"] == "committed" and _proxy(state, source)["active"]


@pytest.mark.parametrize("spell", ["minor_illusion", "silent_image", "major_image", "mislead"])
def test_finite_whole_cast_dismissal_uses_actual_proxy_owner_without_action_cost(spell):
    state, _, _, source, _ = _case(spell)
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=source["cast_id"])
    assert not _source(state, source)["installed"] and not _proxy(state, source)["active"]
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources
    assert state[f"{SRD}.entities"]["hero"].get("concentration_spell_id") is None


@pytest.mark.parametrize("spell,slot", [("major_image", 4), ("programmed_illusion", 6)])
def test_permanent_source_rejects_free_dismissal_but_actual_dispel_ends_it(spell, slot):
    state, data, params, source, _ = _case(spell, slot=slot)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run(
            "dismiss_srd_spell",
            state,
            caster_state="hero",
            cast_id=source["cast_id"] or "no-duration-cast",
        )
    assert state == before
    _dispel(state, data, params, source, slot=slot)
    assert not _source(state, source)["installed"] and not _proxy(state, source)["active"]


@pytest.mark.parametrize("seed,ended", [(1, False), (5, True)])
def test_actual_lower_slot_dispel_rolls_saved_upcast_level(monkeypatch, seed, ended):
    state, data, params, source, _ = _case("major_image", slot=6)
    monkeypatch.setattr(dice, "_rng", Random(seed))
    result = _dispel(state, data, params, source)
    assert _source(state, source)["installed"] is (not ended)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    assert result.result["context"]["_spell_source_cast_id"] == ""


def test_cantrip_zero_is_known_and_legacy_unknown_source_level_rejects_atomically():
    state, data, params, source, _ = _case()
    _dispel(state, data, params, source)
    assert not _source(state, source)["installed"]
    state, data, params, source, _ = _case()
    source["spell_level"] = -1
    _prepare(state, data, "dispel_magic")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="level"):
        _dispel(state, data, params, source)
    assert state == before


def test_creature_dispel_does_not_end_an_independent_image_by_bookkeeping_caster():
    state, data, params, source, _ = _case("major_image", slot=4)
    _dispel(state, data, params, source, select_proxy=False)
    assert _source(state, source)["installed"] and _proxy(state, source)["active"]


def test_mislead_creature_dispel_retires_double_preserves_native_invisible():
    state, data, params = _fresh_case("mislead")
    state[f"{SRD}.entities"]["hero"]["conditions"].append("invisible")
    _cast(state, data, params)
    source = next(iter(state[f"{SRD}.state"]["illusion_spell_sources"].values()))
    _dispel(state, data, params, source, slot=5, select_proxy=False)
    assert not _source(state, source)["installed"] and not _proxy(state, source)["active"]
    assert "invisible" in state[f"{SRD}.entities"]["hero"]["conditions"]


def test_old_illusion_dismissal_uses_exact_cast_and_rejects_legacy_identity():
    state, _, _, source, _ = _case()
    budget._run("dismiss_srd_illusion_spell", state, caster_state="hero", illusion_id=source["id"])
    assert not _source(state, source)["installed"]
    state, _, _, source, _ = _case()
    source["cast_id"] = ""
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="legacy"):
        budget._run(
            "dismiss_srd_illusion_spell", state, caster_state="hero", illusion_id=source["id"]
        )
    assert state == before


def test_illusion_removal_emitter_is_stable_on_repeat_generation():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = extend_runtime(text)
    second = extend_runtime(first)
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(second, Loader=yaml.CSafeLoader)


def test_same_tick_casts_remain_independent_when_only_finite_cast_is_dismissed():
    state, data, params, first, _ = _case("major_image", slot=4)
    _cast(state, data, params | {"illusion_position": [0, 10, 0]})
    second = next(reversed(state[f"{SRD}.state"]["illusion_spell_sources"].values()))
    assert first["started_tick"] == second["started_tick"]
    assert first["cast_id"] != second["cast_id"]
    materials = _prepare(state, data, "minor_illusion")
    _cast(
        state,
        data,
        params
        | {"spell": "minor_illusion", "slot_resource": "cantrip", "material_item_ids": materials},
    )
    finite = next(reversed(state[f"{SRD}.state"]["illusion_spell_sources"].values()))
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=finite["cast_id"])
    assert not _source(state, finite)["installed"]
    assert all(
        _source(state, s)["installed"] and _proxy(state, s)["active"] for s in (first, second)
    )


@pytest.mark.parametrize("geometry", ["other_plane", "out_of_range", "solid_path"])
def test_selected_dispel_uses_actual_proxy_geometry_and_rejects_atomically(geometry):
    state, data, params, source, _ = _case("major_image", slot=4)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if geometry == "other_plane":
        _proxy(state, source)["metadata"]["plane"] = "ethereal"
    elif geometry == "out_of_range":
        _proxy(state, source)["position"] = [150, 0, 0]
    else:
        scene["geometry"] = [
            {
                "id": "dispel_wall",
                "map_id": "information",
                "active": True,
                "volume": {"kind": "box", "min": [2, -10, -10], "max": [3, 10, 20]},
                "movement": "blocking",
                "visibility": "clear",
                "metadata": {"plane": "material"},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            }
        ]
    _prepare(state, data, "dispel_magic")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _dispel(state, data, params | {"target_distance": 0}, source, slot=4)
    assert state == before


def test_public_cast_reopen_actual_selected_dispel_and_retries_preserve_exact_source(
    tmp_path, monkeypatch
):
    def public_case(spell):
        state, data, params = _fresh_case(spell)
        state[f"{SRD}.entities"]["hero"]["prepared_spells"] += ["dispel_magic"]
        data["spellcasting_grants"][0]["spells"] += ["dispel_magic"]
        data["wizard_spellbook"] = ["dispel_magic"]
        return state, data, params

    monkeypatch.setattr(public_casting, "_spell_case", public_case)
    database = tmp_path / "casting.sqlite"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "minor_illusion")
        receipt, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        source = next(iter(saved.state[f"{SRD}.state"]["illusion_spell_sources"].values()))
        assert source["spell_level"] == 0 and source["cast_id"] == "srd_cast:1"
        assert owner.execute_action(cast_command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        dispel = params | {
            "spell": "dispel_magic",
            "slot_resource": "spell_slot_3",
            "material_item_ids": [],
            "recovery_choice": "illusion",
            "recovery_source_id": source["id"],
        }
        receipt, dispel_command = _act(owner, "cast_spell_outside_combat", **dispel)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        settled = owner.snapshot("casting")
        actual = settled.state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]
        assert not actual["installed"] and actual["cast_id"] == source["cast_id"]
        before_slots = saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"]
        assert (
            settled.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"]
            == before_slots - 1
        )
        assert owner.execute_action(dispel_command) == receipt
        assert owner.snapshot("casting").state == settled.state
        assert owner.execute_action(cast_command).status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state == settled.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == settled.state
