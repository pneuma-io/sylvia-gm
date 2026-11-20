"""Issue 191: a creature summoned outside combat can join the combat that starts later.

Find Steed and Summon Dragon cast in exploration save an "exploration birth" record. When combat
starts with that creature placed on the map, the admission reconciles the record against the
creature's real body. That reconciliation must accept the paid creature, not refuse it.
"""

from copy import deepcopy

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from tests.srd_fixture_scene import fixture_scene
from tests.test_srd_public_spell_casting import SRD, _create_spell_campaign

SUMMONS = {
    "summon_dragon": {
        "summon_id": "dragon_one",
        "selected_damage_type": "fire",
        "created_actor_space_visible": True,
        "caster_can_see_summon_space": True,
        "summon_space_unoccupied": True,
        "summon_distance": 30,
    },
    "find_steed": {
        "summon_id": "steed_one",
        "created_individual_id": "steed_one",
        "created_creature_type": "celestial",
        "created_actor_space_visible": True,
    },
}


def _act(owner, tool_name, **parameters):
    before = owner.snapshot("casting")
    command = ActionCommand(
        campaign_id="casting", action_id=f"entry-{before.revision}-{tool_name}",
        idempotency_key=f"entry-{before.revision}-{tool_name}", owner_id="casting-owner",
        writer=WriterRole.DM, expected_revision=before.revision, turn_id="casting-turn",
        package_id=SRD, tool_name=tool_name, parameters=parameters,
    )
    return StatelessMcpMechanics(owner).execute_action(command)


def _summon_outside_combat(owner, spell):
    _, _, parameters = _create_spell_campaign(owner, spell)
    parameters.pop("target", None)
    parameters.update(
        SUMMONS[spell],
        created_actor_placement="An established clear open space thirty feet from the caster.",
        created_actor_distance=30, created_actor_space_unoccupied=True,
        created_actor_clear_path=True,
    )
    receipt = _act(owner, "cast_spell_outside_combat", **parameters)
    assert receipt.status is ActionStatus.COMMITTED, receipt.result
    return SUMMONS[spell]["summon_id"]


def _scene_with_summon(state, summon_id, spell):
    entities = state[f"{SRD}.entities"]
    scene = fixture_scene(
        {key: value for key, value in entities.items() if key in {"hero", "guard"}},
        positions={"hero": [0, 0, 0], "guard": [60, 0, 0]},
    )
    body = deepcopy(scene["entities"][0])
    body.update(id=summon_id, position=[30, 0, 0], height=10)
    body["footprint"] = {"kind": "box", "half_extents": [5, 5]}
    body["metadata"] = body["metadata"] | {
        "participant_ref": summon_id,
        "creature_type": entities[summon_id]["creature_type"],
        "source_ref": spell,
        "source_caster": "hero",
    }
    scene["entities"].append(body)
    return scene


@pytest.mark.parametrize("spell", sorted(SUMMONS))
def test_exploration_summon_joins_the_combat_that_starts_later(tmp_path, spell):
    with AuthorityOwner.open(tmp_path / "entry.db", "casting-owner") as owner:
        summon_id = _summon_outside_combat(owner, spell)
        saved = owner.snapshot("casting").state
        record = saved[f"{SRD}.state"]["exploration_created_actor_records"][summon_id]
        assert record["body_admitted"] is False
        started = _act(
            owner, "start_group_combat_encounter",
            combatants=["hero", "guard", summon_id],
            combat_scenes=[_scene_with_summon(saved, summon_id, spell)],
        )
        assert started.status is ActionStatus.COMMITTED, started.result
        after = owner.snapshot("casting").state
        record = after[f"{SRD}.state"]["exploration_created_actor_records"][summon_id]
        assert record["body_admitted"] is True
        bodies = {body["id"]: body for body in after[f"{SRD}.state"]["spatial_scene"]["entities"]}
        assert bodies[summon_id]["active"] is True
