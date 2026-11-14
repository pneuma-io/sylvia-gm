"""Magic Jar proves one soul, distinct actual bodies, source costs and physical consequences."""

import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_magic_jar import PUBLIC_TOOLS, extend_runtime
from scripts.srd_full.srd_magic_jar_consumers import bind_consumers

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, MechanicContext, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_planar_binding as binding
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_illusion_removals import _prepare
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_shared_source_adoption import _case as _source_case
from tests.srd_full.test_srd_spell_wave3_information import _entity

SRD = cards.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    root = os.environ.get("FC14_JAR_RUNTIME_ROOT")
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
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    yield
    budget._mechanic.cache_clear()


def _host_artifact(data):
    host = deepcopy(data)
    host.update(
        name="Actual host",
        abilities={
            "strength": 20,
            "dexterity": 8,
            "constitution": 18,
            "intelligence": 10,
            "wisdom": 10,
            "charisma": 3,
        },
        saving_throw_proficiencies=[],
    )
    return host


def _run(tool_name, state, data, *, host_overrides=None, **params):
    host = _host_artifact(data)
    host.update(host_overrides or {})
    result = budget._mechanic(tool_name)(
        MechanicContext(
            campaign_id="jar",
            turn_id="jar-turn",
            action_id=tool_name,
            state=owned_payload(state),
            parameters=owned_payload(params),
            participants=owned_payload(
                {
                    actor: {
                        "participant_id": actor,
                        "kind": "player_character",
                        "validation_status": "validated",
                        "package_id": SRD,
                        "data": artifact,
                    }
                    for actor, artifact in (("hero", data), ("guard", host))
                }
            ),
        )
    )
    if result.result["status"] == "committed":
        for mutation in result.mutations:
            state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)
    return result


def _case(*, bind=True):
    state, data, params = _source_case("magic_jar")
    params["magic_jar_container"] = "reliquary"
    entities = state[f"{SRD}.entities"]
    for name in ("hero", "guard"):
        entities[name].update(
            creature_kind="character",
            creature_type="humanoid",
            hp=40 if name == "hero" else 60,
            max_hp=40 if name == "hero" else 60,
            hit_die_size=6,
            hit_dice_pools=None,
        )
        # The validated fixture is a Wizard; its source class defines d6 and actual level HD.
        entities[name]["resources"]["hit_die"] = data["level"]
        entities[name].setdefault("resource_maxima", {})["hit_die"] = data["level"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"].append(
        {
            "id": "reliquary",
            "map_id": scene["map"]["id"],
            "position": [3, 0, 0],
            "footprint": {"kind": "box", "half_extents": [0.5, 0.5]},
            "height": 1,
            "facing": [1, 0],
            "active": True,
            "movement": "passable",
            "visibility": "clear",
            "metadata": {"is_object": True, "plane": "material"},
        }
    )
    if bind:
        _run(
            "record_srd_magic_jar_container",
            state,
            data,
            caster_state="hero",
            object_id="reliquary",
            material_item_id=params["material_item_ids"][0],
        )
    return state, data, params


def _cast(state, data, params):
    _run("begin_srd_spell_cast", state, data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        data,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    return _run("cast_spell_outside_combat", state, data, **params)


def _source(state):
    return next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] == "reliquary"
    )["metadata"]["srd_magic_jar"]


def _possess(state, data):
    return _run(
        "project_srd_magic_jar_soul",
        state,
        data,
        caster_state="hero",
        host_state="guard",
        host_character="guard",
    )


def test_emitter_repeat_preserves_actual_shared_owners():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = bind_consumers(extend_runtime(text))
    second = bind_consumers(extend_runtime(first))
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    assert yaml.load(first, Loader=loader) == yaml.load(second, Loader=loader)


def test_actual_minute_material_and_single_soul_source():
    state, data, params = _case()
    slot = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"]
    _cast(state, data, params)
    hero = state[f"{SRD}.entities"]["hero"]
    source = _source(state)
    assert hero["resources"]["spell_slot_6"] == slot - 1
    assert hero["spell_materials"][0]["quantity"] == 3
    assert source["cast_id"] == hero["last_spell_source_cast_id"]
    body = state[f"{SRD}.entities"][source["body_subject"]]
    assert body["logical_actor_id"] == "hero"
    assert body["magic_jar_role"] == "body"
    assert "unconscious" not in body["conditions"]
    current = _run("srd_information_scene_entity", state, data, subject="hero")
    assert current.result["context"]["_info_scene_entity"]["id"] == "reliquary"


def test_actual_possession_projects_listed_stats_and_later_d20_only():
    state, data, params = _case()
    _cast(state, data, params)
    original = deepcopy(state[f"{SRD}.entities"]["hero"])
    _possess(state, data)
    hero, host = (state[f"{SRD}.entities"][s] for s in ("hero", "guard"))
    assert hero["magic_jar_role"] == "possessed"
    assert hero["hp"] == 60
    assert hero["ability_scores"]["strength"] == 20
    assert hero["ability_scores"]["intelligence"] == data["abilities"]["intelligence"]
    assert hero["base_passive_perception"] == 10
    assert hero.get("condition_immunities") == original.get("condition_immunities")
    assert host["magic_jar_role"] == "host_soul" and "incapacitated" in host["conditions"]
    later = _run(
        "d20_test",
        state,
        data,
        actor="hero",
        actor_state="hero",
        ability="strength",
        kind="ability_check",
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
    )
    assert later.result["context"]["_combined_modifier"] == 5
    _run("derive_srd_control_character_facts", state, data, actor="hero", target_state="hero")
    assert state[f"{SRD}.entities"]["hero"]["base_passive_perception"] == 10
    scene = _run("srd_information_scene_entity", state, data, subject="hero")
    assert scene.result["context"]["_info_scene_entity"]["id"] == "guard"


def test_real_host_target_damage_returns_to_same_living_host():
    state, data, params = _case()
    _cast(state, data, params)
    _possess(state, data)
    _run(
        "apply_srd_damage",
        state,
        data,
        target="guard",
        amount=7,
        adjustment=0,
        damage_type="force",
        source_state_id="",
    )
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 53
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 53
    _run("return_srd_magic_jar_soul", state, data, caster_state="hero", destination="container")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 53
    assert "incapacitated" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("return_srd_magic_jar_soul", state, data, caster_state="hero", destination="body")
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 40
    assert state[f"{SRD}.entities"]["hero"]["magic_jar_role"] is None
    assert _source(state)["installed"] is False


def test_container_soul_cannot_cast_or_move():
    state, data, params = _case()
    _cast(state, data, params)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="cannot cast"):
        _run("begin_srd_spell_cast", state, data, **_begin(params))
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="cannot move"):
        _run("derive_srd_scene_movement_budget", state, data, actor_state="hero", mode="walk")
    assert state == before


def test_host_body_death_uses_actual_caster_save_and_preserves_one_soul(monkeypatch):
    state, data, params = _case()
    _cast(state, data, params)
    _possess(state, data)
    monkeypatch.setattr(dice, "_rng", Random(5))  # Actual d20 20 against the saved caster DC.
    _run(
        "apply_srd_damage",
        state,
        data,
        target="guard",
        amount=200,
        adjustment=0,
        damage_type="force",
        source_state_id="",
    )
    hero, host = (state[f"{SRD}.entities"][s] for s in ("hero", "guard"))
    assert host["is_alive"] is False and host["death_tick"] is not None
    assert hero["is_alive"] is True and hero.get("death_tick") is None
    assert hero["hp"] == 40 and hero["magic_jar_role"] == "container"
    assert _source(state)["mode"] == "container"


def test_host_body_death_failed_save_is_actual_caster_and_host_death(monkeypatch):
    state, data, params = _case()
    _cast(state, data, params)
    _possess(state, data)
    monkeypatch.setattr(dice, "_rng", Random(1))
    _run(
        "apply_srd_damage",
        state,
        data,
        target="guard",
        amount=200,
        adjustment=0,
        damage_type="force",
        source_state_id="",
    )
    assert state[f"{SRD}.entities"]["hero"]["is_alive"] is False
    assert state[f"{SRD}.entities"]["hero"]["death_tick"] is not None
    assert state[f"{SRD}.entities"]["guard"]["is_alive"] is False
    assert _source(state)["mode"] == "caster_dead"


def test_abandoned_physical_body_death_does_not_create_another_soul_death():
    state, data, params = _case()
    _cast(state, data, params)
    body_id = _source(state)["body_subject"]
    _run(
        "apply_srd_damage",
        state,
        data,
        target=body_id,
        amount=200,
        adjustment=0,
        damage_type="force",
        source_state_id="",
    )
    body = state[f"{SRD}.entities"][body_id]
    assert body["is_alive"] is False and body.get("death_tick") is None
    assert state[f"{SRD}.entities"]["hero"]["is_alive"] is True
    _run("end_srd_magic_jar_source", state, data, caster_state="hero", reason="dispel")
    assert state[f"{SRD}.entities"]["hero"]["is_alive"] is False
    assert state[f"{SRD}.entities"]["hero"]["death_tick"] is not None


def test_resisted_actual_host_has_exact_cast_24_hour_immunity():
    state, data, params = _case()
    _cast(state, data, params)
    host = deepcopy(data["abilities"])
    host["charisma"] = 20
    _run(
        "project_srd_magic_jar_soul",
        state,
        data,
        caster_state="hero",
        host_state="guard",
        host_character="guard",
        host_overrides={"abilities": host},
    )
    assert _source(state)["mode"] == "container"
    deadline = _source(state)["resisted_hosts"][0]["until_tick"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="past 24 hours"):
        _possess(state, data)
    assert state == before
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=14400)
    assert state[f"{SRD}.state"]["lasting_tick"] >= deadline
    _possess(state, data)
    assert _source(state)["mode"] == "possessed"


def test_actual_paid_dispel_uses_ongoing_source_level_and_return_owner():
    state, data, params = _case()
    _cast(state, data, params)
    _possess(state, data)
    source_id = _source(state)["source_id"]
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("dispeller", [0, 5, 0], participant_ref="dispeller", creature_type="humanoid")
    )
    _run(
        "prepare_srd_creature",
        state,
        data,
        creature_id="dispeller",
        name="Prepared dispeller",
        source="Synthetic spellcaster: Dispel Magic cast at level 6, DC 15, CON +6.",
        armor_class=13,
        hit_points=40,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=6,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[{"spell": "dispel_magic", "uses_per_day": 1, "casting_level": 6}],
    )
    choices = params | {
        "caster_state": "dispeller",
        "spell": "dispel_magic",
        "material_item_ids": [],
        "recovery_choice": "magic_jar",
        "recovery_surface": "reliquary",
        "recovery_source_id": source_id,
    }
    choices.pop("caster")
    choices.pop("slot_resource")
    outcome = _run("cast_spell_outside_combat", state, data, **choices)
    assert {"label": "Selected Jar ended", "value": True} in outcome.result["context"][
        "_spell_summary_rows"
    ]
    assert state[f"{SRD}.entities"]["dispeller"]["spell_uses_remaining"]["dispel_magic"] == 0
    assert _source(state)["installed"] is False
    assert state[f"{SRD}.entities"]["hero"]["magic_jar_role"] is None
    assert state[f"{SRD}.entities"]["guard"]["magic_jar_role"] is None


def test_named_protection_prevents_effect_after_actual_projection_action_cost():
    state, data, params = _case()
    _cast(state, data, params)
    material_ids = _prepare(state, deepcopy(data), "protection_from_evil_and_good")
    inventory = state[f"{SRD}.entities"]["hero"]["inventory"]
    materials = state[f"{SRD}.entities"]["hero"]["spell_materials"]
    _run(
        "prepare_srd_creature",
        state,
        data,
        creature_id="protector",
        name="Prepared protector",
        source="Synthetic spellcaster: Protection from Evil and Good, DC 15, CON +6.",
        armor_class=13,
        hit_points=40,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=6,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        inventory=inventory,
        spell_materials=materials,
        spells=[{"spell": "protection_from_evil_and_good", "uses_per_day": 1}],
    )
    protection = params | {
        "caster_state": "protector",
        "spell": "protection_from_evil_and_good",
        "target": "guard",
        "target_distance": 1,
        "caster_can_touch_target": True,
        "material_item_ids": material_ids,
    }
    protection.pop("caster")
    protection.pop("slot_resource")
    _run("cast_spell_outside_combat", state, data, **protection)
    tick = state[f"{SRD}.state"]["lasting_tick"]
    outcome = _possess(state, data)
    assert outcome.result["context"]["_jar_possessed"] is False
    assert outcome.result["context"]["_d20_test"] is None
    assert state[f"{SRD}.state"]["lasting_tick"] == tick + 2
    assert _source(state)["mode"] == "container"


def test_public_paid_cast_possession_named_damage_return_reopen_and_retry(tmp_path, monkeypatch):
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    assert parent
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
    monkeypatch.setattr(binding, "PUBLIC_TOOLS", binding.PUBLIC_TOOLS | PUBLIC_TOOLS)
    monkeypatch.setattr(public_casting, "bundled_srd_archive", binding._archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda *_args, **_kwargs: _case(bind=False))
    original_create = AuthorityOwner.create_campaign

    def add_actual_host(owner, command):
        hero = command.participants[0]
        host = replace(hero, participant_id="guard", data=_host_artifact(hero.data))
        return original_create(owner, replace(command, participants=(*command.participants, host)))

    monkeypatch.setattr(AuthorityOwner, "create_campaign", add_actual_host)
    database = tmp_path / "jar.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, "magic_jar", target_overrides={"hp": 60, "max_hp": 60, "temp_hp": 0}
        )
        receipt, _ = _act(
            owner,
            "record_srd_magic_jar_container",
            caster_state="hero",
            object_id="reliquary",
            material_item_id=params["material_item_ids"][0],
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(owner, "begin_srd_spell_cast", **_begin(params))
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, command = _act(owner, "cast_spell_outside_combat", **params)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert owner.execute_action(command) == receipt and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipt, command = _act(
            owner,
            "project_srd_magic_jar_soul",
            caster_state="hero",
            host_state="guard",
            host_character="guard",
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        possessed = owner.snapshot("casting")
        assert possessed.state[f"{SRD}.entities"]["hero"]["magic_jar_role"] == "possessed"
        assert owner.execute_action(command) == receipt and owner.snapshot("casting") == possessed
        receipt, _ = _prepare_mage(owner, [{"spell": "magic_missile", "uses_per_day": 1}])
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        missile = params | {
            "caster_state": "mage",
            "spell": "magic_missile",
            "target": "guard",
            "material_item_ids": [],
            "projectile_targets": ["guard"] * 3,
        }
        missile.pop("caster")
        missile.pop("slot_resource")
        receipt, command = _act(owner, "cast_spell_outside_combat", **missile)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        damaged = owner.snapshot("casting")
        assert damaged.state[f"{SRD}.entities"]["hero"]["hp"] < 60
        assert (
            damaged.state[f"{SRD}.entities"]["hero"]["hp"]
            == damaged.state[f"{SRD}.entities"]["guard"]["hp"]
        )
        assert owner.execute_action(command) == receipt and owner.snapshot("casting") == damaged
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == damaged
        for destination in ("container", "body"):
            receipt, command = _act(
                owner, "return_srd_magic_jar_soul", caster_state="hero", destination=destination
            )
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            saved = owner.snapshot("casting")
            assert owner.execute_action(command) == receipt and owner.snapshot("casting") == saved
        assert saved.state[f"{SRD}.entities"]["hero"]["hp"] == 40
        assert saved.state[f"{SRD}.entities"]["hero"]["magic_jar_role"] is None
        assert _source(saved.state)["installed"] is False
