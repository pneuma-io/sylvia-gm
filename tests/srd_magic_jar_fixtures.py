"""Magic Jar proves one soul, distinct actual bodies, source costs and physical consequences."""

import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_magic_jar import PUBLIC_TOOLS, extend_runtime
from scripts.srd_magic_jar_consumers import bind_consumers

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, MechanicContext, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import srd_planar_binding_fixtures as binding
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.test_srd_extended_casting import _begin
from tests.srd_illusion_removals_fixtures import _prepare
from tests.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_shared_source_adoption_fixtures import _case as _source_case
from tests.test_srd_spell_wave3_information import _entity

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
