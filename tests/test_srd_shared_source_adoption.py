"""Authentic duration casts retain exact identity through their owning removal callbacks."""

import json
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_spell_removals import extend_runtime as removal_runtime
from scripts.srd_spell_source_instances import extend_runtime as source_runtime

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.test_srd_extended_casting import _begin
from tests.test_srd_illusion_removals import _prepare
from tests.test_srd_npc_spell_casting import _act
from tests.test_srd_spell_wave3_information import _case as _information_case
from tests.test_srd_spell_wave3_information import _entity

SRD = cards.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    root = os.environ.get("FC14_SOURCE_RUNTIME_ROOT")
    if root:
        monkeypatch.setattr(budget, "ROOT", Path(root))
    budget._mechanic.cache_clear()
    monkeypatch.setattr(
        cards,
        "_spell_definitions",
        lambda: budget._runtime_definition((budget.ROOT / "runtime/ruleset.yaml").read_bytes())[
            "definitions"
        ]["spells"],
    )
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    yield
    budget._mechanic.cache_clear()


def _scene_row(state, subject):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == subject
    )


def _case(spell="blink", slot=None):
    state, data, params = _information_case("mind_blank")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [5, 0, 0]
    scene["entities"] += [
        _entity("paper", [5, 0, 0], is_object=True, writable_surface=True),
        _entity("seen-hill", [30, 0, 0], is_object=True, seen_by=["hero"]),
        _entity("metal", [3, 0, 0], is_object=True),
        _entity("sapphire", [2, 0, 0], is_object=True),
        _entity("item", [4, 0, 0], is_object=True),
    ]
    for row in scene["entities"]:
        if row["id"] in {"hero", "guard"}:
            row["metadata"].update(basic_limb_arrangement="two arms and two legs", size_rank=2)
    scene["zones"] += [
        {
            "id": "adoption-light-" + plane,
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": plane, "lighting": "bright"},
        }
        for plane in ("material", "ethereal")
    ]
    for actor in ("hero", "guard"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, size_rank=2)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["intelligence"] = -100
    material_ids = _prepare(state, data, spell)
    if spell in {"goodberry", "antilife_shell", "heat_metal"}:
        saved = state[f"{SRD}.entities"]["hero"]
        budget._run(
            "prepare_srd_creature",
            state,
            creature_id="mage",
            name="Source caster",
            source="Synthetic creature: prepared source spells, DC 15, CON +6.",
            armor_class=13,
            hit_points=1000,
            speed=30,
            initiative_modifier=0,
            size_rank=2,
            constitution_save=6,
            spellcasting_ability="wisdom",
            spellcasting_modifier=3,
            spell_attack_bonus=7,
            spell_save_dc=15,
            inventory=saved["inventory"],
            spell_materials=saved["spell_materials"],
            spells=[
                {"spell": spell, "uses_per_day": 3},
                {"spell": "dispel_magic", "uses_per_day": 3},
            ],
        )
        params.pop("caster", None)
        params["caster_state"] = "mage"
        scene = state[f"{SRD}.state"]["spatial_scene"]
        scene["entities"][0]["id"] = "mage"
        scene["entities"][0]["metadata"]["participant_ref"] = "mage"
    params.update(
        spell=spell,
        target=params["caster_state"],
        target_distance=5,
        slot_resource=f"spell_slot_{slot or cards._casting_headers()[spell]['level']}",
        material_item_ids=material_ids,
    )
    specs = [
        {
            "subject": actor,
            "description": "A shorter traveler",
            "height_offset_inches": -12,
            "limb_arrangement": "two arms and two legs",
            "willing": True,
        }
        for actor in ("hero", "guard")
    ]
    params.update(
        appearance_specs=specs[:1] if spell == "disguise_self" else specs,
        appearance_position=[0, 0, 0],
        appearance_cube_min=[0, 0, 0],
        appearance_description="A rocky gully",
        appearance_terrain_kind="rocky gully",
        appearance_tactile_kind="rough rock",
        script_surface="paper",
        script_original="The door opens at dawn",
        script_readers=["guard"],
        illusion_position=[20, 0, 0],
        illusion_size=[10, 10, 10],
        illusion_description="A dangerous burning bridge",
        illusion_sound="Flames",
        phantasm_dangerous=True,
        phantasm_damage_appearance="fire",
        project_image_location="seen-hill",
        barrier_subject="",
        target_willing=True,
    )
    if spell == "phantasmal_force":
        params["target"] = "guard"
    if spell == "resilient_sphere":
        params["target"] = "guard"
        params["barrier_subject"] = "guard"
    if spell == "illusory_script":
        params.update(caster_can_touch_target=True, target_distance=0)
    if spell == "goodberry":
        budget._run(
            "record_srd_hand_sized_form",
            state,
            caster_state=params["caster_state"],
            form_id="berries",
            fiction="Ten berries fitting this hand",
            kind="trinket",
            extent=[0.4, 0.4, 0.3],
        )
        params.update(supply_output_id="berries", supply_hand_form_id="berries")
    if spell in {"heat_metal", "instant_summons"}:
        for object_id, metal, kind, item in (
            ("metal", True, "armor", "metal-armor"),
            ("sapphire", False, "sapphire", material_ids[0]),
            ("item", False, "object", ""),
        ):
            budget._run(
                "record_srd_object_utility_facts",
                state,
                object_id=object_id,
                weight_pounds=0.2 if kind == "sapphire" else 7,
                manufactured=True,
                metal=metal,
                object_kind=kind,
                inventory_item=item,
            )
        if spell == "heat_metal":
            state[f"{SRD}.entities"]["guard"].setdefault("inventory", []).append("metal-armor")
            budget._run(
                "record_srd_object_holder",
                state,
                actor_state="guard",
                object_id="metal",
                held_as="worn",
            )
            params["heated_object_id"] = "metal"
        else:
            budget._run(
                "record_srd_object_holder",
                state,
                actor_state="hero",
                object_id="sapphire",
                held_as="carried",
            )
            budget._run(
                "record_srd_spell_material_object",
                state,
                caster_state="hero",
                object_id="sapphire",
                material_item_id=material_ids[0],
            )
            params.update(utility_object_id="item", utility_sapphire_id="sapphire")
    return state, data, params


def _cast(state, data, params):
    hero = state[f"{SRD}.entities"][params["caster_state"]]
    hero["spell_slot_cast_this_turn"] = False
    rounds = {
        "hallucinatory_terrain": 100,
        "illusory_script": 10,
        "tiny_hut": 10,
        "instant_summons": 10,
    }.get(params["spell"])
    if rounds:
        budget._run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
        budget._run(
            "continue_srd_spell_cast",
            state,
            caster_state=params["caster_state"],
            elapsed_rounds=rounds,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    actual_params = params.copy()
    if params["caster_state"] == "mage":
        actual_params.pop("slot_resource", None)
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **actual_params)


def _records(state, spell):
    lasting = state[f"{SRD}.state"]
    if spell == "blink":
        return [state[f"{SRD}.entities"]["hero"]["travel_blink"]]
    if spell == "goodberry":
        return [_scene_row(state, "berries")["metadata"]["srd_goodberry_batch"]]
    for pool in ("appearance_spell_sources", "illusion_spell_sources", "spell_barrier_sources"):
        if pool in lasting:
            found = [
                r
                for r in lasting[pool].values()
                if r.get("spell", r.get("spell_id")) == spell and r.get("installed")
            ]
            if found:
                return found
    return [r for r in lasting.get("turn_effects", []) if r.get("spell") == spell]


SPELLS = [
    "blink",
    "disguise_self",
    "seeming",
    "hallucinatory_terrain",
    "illusory_script",
    "mirror_image",
    "phantasmal_force",
    "project_image",
    "goodberry",
    "antilife_shell",
    "resilient_sphere",
    "tiny_hut",
    "heat_metal",
    "instant_summons",
]


@pytest.mark.parametrize("spell", SPELLS)
def test_real_paid_cast_stamps_exact_source_and_full_cast_dismissal(spell):
    state, data, params = _case(spell)
    actor = params["caster_state"]
    hero = state[f"{SRD}.entities"][actor]
    npc = actor == "mage"
    before = (
        hero["spell_uses_remaining"][spell] if npc else hero["resources"][params["slot_resource"]]
    )
    result = _cast(state, data, params)
    sources = _records(state, spell)
    assert sources and {s["cast_id"] for s in sources} == {"srd_cast:1"}
    assert {s["source_caster_id"] for s in sources} == {actor}
    assert {s["spell_level"] if "spell_level" in s else s["slot_level"] for s in sources} == {
        cards._casting_headers()[spell]["level"]
    }
    hero = state[f"{SRD}.entities"][actor]
    assert (
        hero["spell_uses_remaining"][spell] if npc else hero["resources"][params["slot_resource"]]
    ) == before - 1
    assert "srd_cast:1" in str(result.result["result_card"])
    if spell == "instant_summons":
        snapshot = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected):
            budget._run("dismiss_srd_spell", state, caster_state=actor, cast_id="srd_cast:1")
        assert state == snapshot and sources[0]["ongoing_duration"] is False
    else:
        resources = deepcopy(state[f"{SRD}.entities"][actor]["resources"])
        budget._run("dismiss_srd_spell", state, caster_state=actor, cast_id="srd_cast:1")
        assert state[f"{SRD}.entities"][actor]["resources"] == resources
        if spell == "goodberry":
            assert not _scene_row(state, "berries")["active"]
        elif spell == "blink":
            assert state[f"{SRD}.entities"][actor]["travel_blink"] is None
        elif spell in {"heat_metal"}:
            assert not _records(state, spell)
        else:
            assert not _records(state, spell)


def _dispel(state, data, params, **choices):
    material_ids = _prepare(state, data, "dispel_magic")
    return _cast(
        state,
        data,
        params
        | {
            "spell": "dispel_magic",
            "target": "hero",
            "slot_resource": "spell_slot_9",
            "material_item_ids": material_ids,
        }
        | choices,
    )


@pytest.mark.parametrize("spell", ["mirror_image", "phantasmal_force", "project_image"])
def test_images_dispel_actual_subject_and_proxy_instead_of_caster_bookkeeping(spell):
    state, data, params = _case(spell)
    _cast(state, data, params)
    source = _records(state, spell)[0]
    if spell == "project_image":
        _dispel(state, data, params)
        assert _records(state, spell)
        _dispel(state, data, params, recovery_choice="illusion", recovery_source_id=source["id"])
    else:
        _dispel(state, data, params, target=source["target"])
    assert not _records(state, spell)


@pytest.mark.parametrize("spell", ["disguise_self", "seeming"])
def test_creature_dispel_retires_only_the_selected_appearance_subject(spell):
    state, data, params = _case(spell)
    _cast(state, data, params)
    _dispel(state, data, params, target="hero")
    remaining = _records(state, spell)
    assert [source["subject"] for source in remaining] == (["guard"] if spell == "seeming" else [])


@pytest.mark.parametrize("spell", ["antilife_shell", "resilient_sphere", "tiny_hut"])
def test_selected_barrier_dispel_uses_real_saved_boundary_and_exact_retirement(spell):
    state, data, params = _case(spell)
    _cast(state, data, params)
    source_id = next(
        key
        for key, source in state[f"{SRD}.state"]["spell_barrier_sources"].items()
        if source["installed"]
    )
    budget._run("read_srd_barrier_body", state, source_id=source_id)
    source = state[f"{SRD}.state"]["spell_barrier_sources"][source_id]
    subject = source["subject_id"]
    point = _scene_row(state, subject)["position"]
    result = _dispel(
        state,
        data,
        params,
        recovery_choice="barrier",
        recovery_source_id=source_id,
        recovery_position=point,
    )
    assert not _records(state, spell)
    assert "_dispel_roll" not in str(result.result["result_card"])


@pytest.mark.parametrize("seed,ended", [(0, False), (5, True)])
def test_selected_source_uses_actual_lower_slot_check(monkeypatch, seed, ended):
    state, data, params = _case("resilient_sphere", slot=9)
    _cast(state, data, params)
    source_id = next(iter(state[f"{SRD}.state"]["spell_barrier_sources"]))
    monkeypatch.setattr(dice, "_rng", Random(seed))
    result = _dispel(
        state,
        data,
        params,
        slot_resource="spell_slot_3",
        recovery_choice="barrier",
        recovery_source_id=source_id,
        recovery_position=_scene_row(state, "guard")["position"],
    )
    assert bool(_records(state, "resilient_sphere")) is not ended
    assert "_dispel_roll" not in str(result.result["result_card"])


def test_sphere_omitted_subject_uses_actual_creature_and_creature_dispel():
    state, data, params = _case("resilient_sphere")
    params["barrier_subject"] = ""
    _cast(state, data, params)
    assert _records(state, "resilient_sphere")[0]["target_state_id"] == "guard"
    _dispel(state, data, params, target="guard")
    assert not _records(state, "resilient_sphere")


@pytest.mark.parametrize(
    "spell,choice,item",
    [
        ("goodberry", "supply", "berries"),
        ("heat_metal", "heat_metal", "metal"),
        ("instant_summons", "object_utility", "sapphire"),
    ],
)
def test_object_dispel_uses_actual_source_object_and_preserves_other_objects(spell, choice, item):
    state, data, params = _case(spell)
    _cast(state, data, params)
    source = _records(state, spell)[0]
    if spell != "goodberry":
        _dispel(state, data, params)
        assert _records(state, spell), "Bookkeeping target is not the object effect subject"
    _dispel(
        state,
        data,
        params,
        recovery_choice=choice,
        recovery_item=item,
        recovery_source_id=source["source_id"],
    )
    if spell == "goodberry":
        assert not _scene_row(state, item)["active"]
    else:
        assert not _records(state, spell)
    assert _scene_row(state, "item")["active"]


def test_script_dispel_erases_only_its_current_owned_writing_and_dismissal_preserves_original():
    state, data, params = _case("illusory_script")
    _cast(state, data, params)
    source = _records(state, "illusory_script")[0]
    _dispel(state, data, params, recovery_choice="appearance", recovery_source_id=source["id"])
    assert state[f"{SRD}.state"]["script_written_text"]["paper"] == ""
    state, data, params = _case("illusory_script")
    _cast(state, data, params)
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:1")
    assert state[f"{SRD}.state"]["script_written_text"]["paper"] == params["script_original"]


def test_seeming_independent_same_tick_cast_and_exact_full_cast_dismissal():
    state, data, params = _case("seeming")
    _cast(state, data, params)
    _cast(state, data, params)
    sources = _records(state, "seeming")
    assert len(sources) == 4 and {s["cast_id"] for s in sources} == {"srd_cast:1", "srd_cast:2"}
    assert len({s["started_tick"] for s in sources}) == 1
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:1")
    assert {s["cast_id"] for s in _records(state, "seeming")} == {"srd_cast:2"}


def test_legacy_timed_condition_has_literal_absent_cast_owner():
    state, _, _ = _case()
    budget._run(
        "install_srd_timed_condition_source",
        state,
        target_state="guard",
        lasting_state="state",
        caster_state="hero",
        spell="hold_person",
        condition="paralyzed",
        save_dc=15,
        duration_rounds=10,
        repeat_save_timing="end_of_target_turn",
    )
    source = next(iter(state[f"{SRD}.state"]["timed_condition_sources"].values()))
    assert source["source_caster_id"] is None and source["cast_id"] == ""


def test_adoption_emission_is_semantically_stable_on_rebuild():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text(encoding="utf-8")
    first = removal_runtime(source_runtime(text))
    second = removal_runtime(source_runtime(first))
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(second, Loader=yaml.CSafeLoader)


def test_saved_state_source_removal_preserves_an_independent_source():
    state, data, params = _case("seeming")
    _cast(state, data, params)
    _cast(state, data, params)
    reopened = json.loads(json.dumps(state))
    budget._run("dismiss_srd_spell", reopened, caster_state="hero", cast_id="srd_cast:1")
    assert len(_records(reopened, "seeming")) == 2
    assert {s["cast_id"] for s in _records(reopened, "seeming")} == {"srd_cast:2"}


def test_actual_blink_phase_dismissal_returns_before_source_removal(monkeypatch):
    state, data, params = _case("blink")
    _cast(state, data, params)
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = 20
    state[f"{SRD}.entities"]["guard"]["initiative_modifier"] = 0
    budget._run(
        "start_combat_encounter_entities",
        state,
        first_actor="hero",
        second_actor="guard",
        first_surprised=False,
        second_surprised=False,
        tie_winner="first",
    )
    budget._run("begin_combat_turn", state)
    monkeypatch.setattr(dice, "_rng", Random(0))
    budget._run("end_combat_turn", state)
    assert _scene_row(state, "hero")["metadata"]["plane"] == "ethereal"
    assert state[f"{SRD}.entities"]["hero"]["travel_blink"]["phase"] is True
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:1")
    assert _scene_row(state, "hero")["metadata"]["plane"] == "material"
    assert state[f"{SRD}.entities"]["hero"]["travel_blink"] is None


def test_goodberry_removal_preserves_consumed_healing_and_nourishment():
    state, data, params = _case("goodberry")
    _cast(state, data, params)
    source = _records(state, "goodberry")[0]
    state[f"{SRD}.entities"]["mage"]["hp"] = 999
    budget._run("eat_srd_goodberry", state, actor_state="mage", object_id="berries")
    nourishment = deepcopy(state[f"{SRD}.entities"]["mage"]["srd_goodberry_nourishment"])
    _dispel(
        state,
        data,
        params,
        recovery_choice="supply",
        recovery_item="berries",
        recovery_source_id=source["source_id"],
    )
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["mage"]["srd_goodberry_nourishment"] == nourishment
    assert not _scene_row(state, "berries")["active"]


@pytest.mark.parametrize("seed,ended", [(0, False), (5, True)])
def test_real_lower_slot_dispel_preserves_or_retires_selected_actual_level(
    monkeypatch, seed, ended
):
    state, data, params = _case("mirror_image", slot=9)
    _cast(state, data, params)
    source = _records(state, "mirror_image")[0]
    assert source["spell_level"] == 9
    monkeypatch.setattr(dice, "_rng", Random(seed))
    _dispel(state, data, params, slot_resource="spell_slot_3")
    assert bool(_records(state, "mirror_image")) is not ended
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2


def test_terrain_dispel_uses_saved_plane_even_after_caster_enters_another_plane():
    state, data, params = _case("hallucinatory_terrain")
    _cast(state, data, params)
    source = _records(state, "hallucinatory_terrain")[0]
    ethereal = params | {
        "spell": "etherealness",
        "slot_resource": "spell_slot_7",
        "material_item_ids": _prepare(state, data, "etherealness"),
    }
    _cast(state, data, ethereal)
    assert _scene_row(state, "hero")["metadata"]["plane"] == "ethereal"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="another plane"):
        _dispel(state, data, params, recovery_choice="appearance", recovery_source_id=source["id"])
    # Preparing a different spell is an explicit fixture step; rejected casting
    # still leaves the actual source, slot and scene position unchanged.
    assert _records(state, "hallucinatory_terrain") == _records(before, "hallucinatory_terrain")
    assert _scene_row(state, "hero") == _scene_row(before, "hero")
    assert (
        state[f"{SRD}.entities"]["hero"]["resources"]
        == before[f"{SRD}.entities"]["hero"]["resources"]
    )


def _composed_archive():
    """Declare the actual composed runtime over an explicitly selected immutable public package."""
    primary, reference = bundled_packages._srd_drafts()
    content = (budget.ROOT / "runtime/ruleset.yaml").read_bytes()
    runtime = yaml.load(content, Loader=yaml.CSafeLoader)
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        declaration["input_schema"] = _tool_schema(
            runtime["tools"][declaration["name"]], tool_name=declaration["name"]
        )
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": content}
    return bundled_packages._archive(
        (NormalizedPackageDraft(record, payloads, primary.format_version), reference)
    )


def test_public_paid_cast_reopen_exact_dismissal_and_retry(tmp_path, monkeypatch):
    package_parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    if os.environ.get("FC14_SOURCE_RUNTIME_ROOT"):
        assert package_parent, (
            "Select the immutable public package parent for composed archive proof"
        )
        monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(package_parent))
        monkeypatch.setattr(public_casting, "bundled_srd_archive", _composed_archive)
    monkeypatch.setattr(public_casting, "_spell_case", _case)
    database = tmp_path / "source-adoption.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "blink")
        cast, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        source = saved.state[f"{SRD}.entities"]["hero"]["travel_blink"]
        assert source["source_caster_id"] == "hero" and source["cast_id"] == "srd_cast:1"
        assert source["slot_level"] == 3
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        assert owner.execute_action(cast_command) == cast
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        dismissed, command = _act(
            owner, "dismiss_srd_spell", caster_state="hero", cast_id=source["cast_id"]
        )
        assert dismissed.status is ActionStatus.COMMITTED, dismissed.result
        settled = owner.snapshot("casting")
        assert settled.state[f"{SRD}.entities"]["hero"]["travel_blink"] is None
        assert settled.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        assert owner.execute_action(command) == dismissed
        assert owner.execute_action(cast_command) == cast
        assert owner.snapshot("casting") == settled
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == settled
