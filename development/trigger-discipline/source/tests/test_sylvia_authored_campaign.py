"""One source-level vertical from authored TD libraries into persisted campaign play."""

import io
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sylvia_gm.agent._builder_session import _campaign_plan_example
from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
)
from sylvia_gm.application.campaign_library_selection import (
    materialize_library_party,
    resolve_campaign_library_selection,
)
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.reusable_participants import save_reusable_participant
from sylvia_gm.application.reusable_plans import save_reusable_campaign_plan
from sylvia_gm.builder._setting_materializer import materialize_setting_draft
from sylvia_gm.domain.authority import PackageRole, WriterRole

from balance_suite.parameters import default_manifest
from sylvia_ruleset import builder


def _token(identity, map_id, cell, side, label):
    return {
        "id": identity,
        "map_id": map_id,
        "position": [cell[0] + 0.5, cell[1] + 0.5, 0],
        "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
        "height": 1.8,
        "facing": [1 if side == "allies" else -1, 0],
        "active": True,
        "movement": "blocking",
        "visibility": "opaque",
        "metadata": {"player": {"label": label, "kind": "unit", "side": side}},
    }


def _fresh_package(parameters):
    runtime = builder._runtime(parameters.values, {})
    runtime["tools"].pop("exercise_specialty_counterfactual")
    specialties = [*builder._conformance_character()["artifact"]["specialties"],
                   "specialty.field_medic.entry.combat_medicine"]
    rules = builder._manifest("1" * 64, specialties, ["not-executed"])
    rules["tools"] = [
        tool for tool in rules["tools"]
        if tool["name"] in runtime["tools"]
    ]
    rules["ui_surfaces"] = [
        surface for surface in rules["ui_surfaces"] if surface["id"] != "operations.network"
    ]
    rules["ui_surfaces"][0]["actions"] = [
        action for action in rules["ui_surfaces"][0]["actions"]
        if action["id"] in {"move", "aim", "fire", "burst", "full-auto"}
    ]
    rule_files = {"runtime/ruleset.yaml": builder._canonical_json(runtime)}
    guide_path = runtime["definitions"]["authoring_defaults"]["campaign_authoring_reference"]
    rule_files[guide_path] = (Path(builder.__file__).parent / guide_path).read_bytes()
    rule_files["content/content-declarations.json"] = builder._canonical_json(
        builder._content_declarations(
            "1" * 64, parameters.hash, sorted(rule_files), rules, []
        )
    )
    rule_record = builder._complete_package_record(rules, rule_files)

    return builder._archive_bytes([(rule_record, rule_files)])


def _actor(identity, name, cell, parameters, *, weapon="service_rifle", side="allies"):
    # TD's helper supplies explicitly synthetic equipment and current parameter values only.
    actor = builder._entity(
        identity,
        side=side,
        class_id="RIFLEMAN",
        position=cell,
        weapon=weapon,
        parameter_values=parameters.values,
    )
    actor["name"] = name
    return actor


def _participant(actor):
    return {
        key: deepcopy(actor[key])
        for key in (
            "id", "name", "side", "class_id", "level", "skills", "aptitudes", "specialties"
        )
    }


def _execute(owner, campaign_id, tool_name, parameters):
    basis = owner.snapshot(campaign_id)
    tool = next(item for item in owner.catalog(campaign_id) if item.name == tool_name)
    return owner.execute_action(
        ActionCommand(
            campaign_id=campaign_id,
            action_id=f"action-{basis.revision}-{tool_name}",
            idempotency_key=f"key-{basis.revision}-{tool_name}",
            owner_id="campaign-owner",
            writer=WriterRole.DM,
            expected_revision=basis.revision,
            turn_id="authored-turn",
            package_id=tool.package_id,
            tool_name=tool.name,
            parameters=parameters,
        )
    )


def test_fresh_setting_plan_and_reusable_party_reach_persisted_td_play(tmp_path):
    parameters = default_manifest().seed_set()
    database = tmp_path / "campaign.db"
    archive = _fresh_package(parameters)
    setting_id = "local.td-authored-harbor"
    map_id = "authored-harbor-grid"
    hostile_id = "harbor-sentry"
    hostile = _actor(hostile_id, "Harbor Sentry", [8, 2], parameters, side="hostiles")
    # A durable synthetic target keeps this composition check independent of its attack roll.
    hostile.update(hp=100, max_ap=3)
    scene = {
        "format": 1,
        "map": {
            "id": map_id,
            "revision": 1,
            "bounds": {"min": [0, 0, 0], "max": [12, 8, 3]},
            "grid": {"cell_size": 1, "unit": "m", "origin": [0, 0]},
            "layers": [{"id": "floor", "min_elevation": 0, "max_elevation": 3}],
        },
        "geometry": [],
        "entities": [_token(hostile_id, map_id, [8, 2], "hostiles", "Harbor Sentry")],
        "zones": [],
        "terrain": [],
    }

    with AuthorityOwner.open(database, "campaign-owner") as owner:
        rules = owner.install_package_archive(io.BytesIO(archive), install_id="fresh-rules")
        location = tmp_path / "setting-draft" / "files" / "locations" / "harbor.md"
        location.parent.mkdir(parents=True)
        location.write_text("# Authored Harbor\n\nA contested harbor approach.\n", encoding="utf-8")
        with materialize_setting_draft(
            owner, tmp_path, candidate_id=rules.candidate_id,
            package_id=setting_id, version="1.0.0", title="Authored Harbor",
        ) as setting:
            candidate = owner.install_package_archive(setting.archive, install_id="fresh-authored")
        sources = campaign_plan_sources(owner, candidate.package_lock, setting_id=setting_id)
        payload = json.loads(_campaign_plan_example(sources))
        payload.update(
            id="authored-harbor-operation",
            title="Authored Harbor Operation",
            premise="Secure the harbor approach.",
            starting_location={"id": "harbor", "path": "locations/harbor.md"},
        )
        payload["starting_party"]["custom_companions"] = {"minimum": 1, "maximum": 1}
        world = {
            "decision_owner": "player",
            "active_side": "allies",
            "eligible_actor_ids": [],
            "turn_roster": [hostile_id],
            "focused_actor": None,
            "spatial_scene": scene,
            "selection": {}, "preview": {}, "network": {}, "network_access": {},
            "objectives": [], "mission": {"status": "ACTIVE"},
            "prepared": {"overwatch": {"active": False}, "suppression": {"active": False}},
            "physical": {"security_door_open": False},
            "ground_objects": {}, "ground_object_sequence": 0,
            "last_check": {}, "last_move": {}, "last_attack": {}, "last_trigger": {},
            "last_cqc": {}, "last_reload": {}, "last_medical": {}, "last_blast": {},
            "last_drone_attack": {}, "last_specialty_test": {}, "last_network": {},
        }
        payload["initial_state"].update(
            facts=["The harbor approach is contested."],
            mechanics=[
                {"namespace": builder.PACKAGE_ID, "key": "state", "value": world},
                {"namespace": builder.PACKAGE_ID, "key": "entities", "value": {hostile_id: hostile}},
            ],
        )
        slots = ([1, 2], [1, 3])
        payload["initial_state"]["party_placements"] = [
            {
                "entity_state": {
                    "id": {"$participant": "id"},
                    "name": {"$participant": "name"},
                    "position_cell": cell,
                    "center": [cell[0] + 0.5, cell[1] + 0.5, 0.9],
                    "muzzle": [cell[0] + 0.91, cell[1] + 0.5, 1.4],
                },
                "append": [
                    {"namespace": builder.PACKAGE_ID, "key": "state.spatial_scene.entities",
                     "value": _token({"$participant": "id"}, map_id, cell, "allies", {"$participant": "name"})},
                    {"namespace": builder.PACKAGE_ID, "key": "state.turn_roster",
                     "value": {"$participant": "id"}},
                    {"namespace": builder.PACKAGE_ID, "key": "state.eligible_actor_ids",
                     "value": {"$participant": "id"}},
                ],
            }
            for cell in slots
        ]
        plan = save_reusable_campaign_plan(
            tmp_path,
            operation_id="fresh-plan",
            source_payload=payload,
            source_markdown="# Authored Harbor Operation\n\nSecure the harbor approach.",
            validate_plan=sources.validate,
        )
        source_plan = deepcopy(plan.plan.payload)
        primary = next(e for e in candidate.package_lock.entries if e.role is PackageRole.PRIMARY)
        declaration = owner.package_participant_declarations(candidate.candidate_id)[0]
        created = []
        for role, identity, name, cell in (
            ("player_character", "fresh-rifleman", "Keel", slots[0]),
            ("companion", "fresh-companion", "Mast", slots[1]),
        ):
            actor = _actor(identity, name, cell, parameters)
            actor.update(ap=6 if role == "player_character" else 5,
                         max_ap=6 if role == "player_character" else 5)
            if role == "companion":
                actor.update(class_id="CORPSMAN", medical_kit_capable=True,
                             specialties=["specialty.field_medic.entry.combat_medicine"])
            else:
                actor.update(res=8, max_res=10)
            authored = deepcopy(actor)
            for field in ("position_cell", "center", "muzzle", "control"):
                authored.pop(field)
            for field in ("target", "point"):
                authored["aim"].pop(field)
            authored["weapon"].pop("controlled_by")
            for calculation in declaration.initial_state_calculations:
                authored.pop(calculation["set"]["field"], None)
            created.append(save_reusable_participant(
                tmp_path, operation_id=identity, role=role,
                summary=f"{name}, an authored harbor operator.",
                authored_profile=f"# {name}\n\nPrepared for the harbor operation.",
                participant=_participant(actor), entity_state=authored,
                package=primary, declaration=declaration, campaign_setting=sources.setting,
            ))

        selection = resolve_campaign_library_selection(
            owner, tmp_path, candidate_id=candidate.candidate_id,
            setting_id=setting_id, plan_id=plan.artifact_id,
        )
        participants, initial_state, _ = materialize_library_party(
            owner, selection, operation_id="start-authored",
            player_character_id=created[0].artifact_id,
            companion_ids=(created[1].artifact_id,),
        )
        player_id, companion_id = [item.participant_id for item in participants]
        owner.create_campaign(CampaignCreate(
            campaign_id="authored-harbor", display_name="Authored Harbor",
            writer=WriterRole.DM, package_lock=selection.candidate.package_lock,
            turn_id="authored-turn", participants=participants, initial_state=initial_state,
        ))
        started = owner.snapshot("authored-harbor")
        state = started.state[f"{builder.PACKAGE_ID}.state"]
        assert state["turn_roster"] == [hostile_id, player_id, companion_id]
        assert state["eligible_actor_ids"] == [player_id, companion_id]
        assert {item["id"] for item in state["spatial_scene"]["entities"]} == {
            hostile_id, player_id, companion_id
        }
        assert plan.plan.payload == source_plan
        inventory = json.loads(owner.read_campaign_reference(
            "authored-harbor", source="campaign_state", path="inventory",
        )["content"])
        assert inventory["mode"] == "ruleset" and inventory["holdings"] is None
        assert inventory["ruleset_inventory"]["state"] == {
            "namespace": builder.PACKAGE_ID, "key": "entities",
        }
        assert "switch_weapon" in inventory["update_tools"]

        _execute(owner, "authored-harbor", "move_actor", {"actor": player_id, "destination": [2, 2]})
        treatment = {
            "actor": companion_id, "target": player_id, "action": "BATTLEFIELD_CARE",
            "issue_id": "harbor-forearm-injury-1", "ap_cost": 1, "difficulty": 14,
            "check_mode": "CERTAIN", "function_recovery": "PLAUSIBLE",
        }
        _execute(owner, "authored-harbor", "treat_casualty", treatment)
        treated = owner.snapshot("authored-harbor").state[f"{builder.PACKAGE_ID}.entities"]
        assert treated[player_id]["res"] == 9
        assert treated[companion_id]["ap"] == 4
        burst = _execute(owner, "authored-harbor", "fire_burst", {
            "attacker": player_id, "target": hostile_id,
            "aim_point": [8.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT",
        })
        assert len(burst.result["shot_effects"]["shots"]) == 3
        assert all(shot["map_id"] == map_id for shot in burst.result["shot_effects"]["shots"])
        _execute(owner, "authored-harbor", "end_allies_turn", {})

    with AuthorityOwner.open(database, "campaign-owner") as owner:
        reopened = owner.snapshot("authored-harbor")
        state = reopened.state[f"{builder.PACKAGE_ID}.state"]
        entities = reopened.state[f"{builder.PACKAGE_ID}.entities"]
        assert entities[player_id]["position_cell"] == [2, 2]
        assert entities[player_id]["res"] == 9
        assert list(entities[player_id]["battlefield_treatments"]) == ["harbor-forearm-injury-1"]
        assert state["active_side"] == "hostiles"
        assert state["decision_owner"] == "gm"
        assert state["eligible_actor_ids"] == [hostile_id]
        assert entities[hostile_id]["ap"] == 3
        assert entities[player_id]["ap"] == entities[companion_id]["ap"] == 0
        _execute(owner, "authored-harbor", "end_hostiles_turn", {})
        refreshed = owner.snapshot("authored-harbor")
        assert refreshed.state[f"{builder.PACKAGE_ID}.state"]["eligible_actor_ids"] == [
            player_id, companion_id,
        ]
        actors = refreshed.state[f"{builder.PACKAGE_ID}.entities"]
        assert actors[player_id]["ap"] == 6
        assert actors[companion_id]["ap"] == 5
        _execute(owner, "authored-harbor", "treat_casualty", treatment)
        after_repeat = owner.snapshot("authored-harbor").state[f"{builder.PACKAGE_ID}.entities"]
        assert after_repeat[player_id]["res"] == 9
        squad = next(p for p in owner.campaign_panels("authored-harbor")["surfaces"]
                     if p["id"] == "operations.party")
        assert squad["data"]["entities"][player_id]["res"] == 9
        weapon = deepcopy(dict(actors[player_id]["weapon"]))
        _execute(owner, "authored-harbor", "handle_weapon", {
            "actor": player_id, "weapon_id": weapon["id"], "location": "STOWED", "ap_cost": 1,
        })
        weapon["location"] = "STOWED"
        projection = owner.campaign_panels("authored-harbor")
        assert not any(surface["renderer"] == "generic_inventory" for surface in projection["surfaces"])
        loadout = next(p for p in projection["surfaces"] if p["id"] == "operations.loadout")
        assert loadout["data"]["entities"][player_id]["weapon"] == weapon
        assert resolve_campaign_library_selection(
            owner, tmp_path, candidate_id=candidate.candidate_id,
            setting_id=setting_id, plan_id=plan.artifact_id,
        ).plan.plan.payload == source_plan

    with AuthorityOwner.open(database, "campaign-owner") as owner:
        assert owner.campaign_panels("authored-harbor") == projection
        current = owner.snapshot("authored-harbor").state[f"{builder.PACKAGE_ID}.entities"]
        assert current[player_id]["weapon"] == weapon
        assert current[player_id]["ap"] == 5
        _execute(owner, "authored-harbor", "drop_weapon", {
            "actor": player_id, "weapon_id": weapon["id"], "ap_cost": 0,
        })
        dropped = owner.campaign_panels("authored-harbor")
        loadout = next(p for p in dropped["surfaces"] if p["id"] == "operations.loadout")
        assert loadout["data"]["entities"][player_id]["weapon"] is None

    with AuthorityOwner.open(database, "campaign-owner") as owner:
        current = owner.snapshot("authored-harbor").state[f"{builder.PACKAGE_ID}.entities"]
        assert current[player_id]["weapon"] is None
        _execute(owner, "authored-harbor", "pickup_weapon", {
            "actor": player_id, "ground_id": "weapon_1", "weapon_id": weapon["id"],
            "location": "READY", "ap_cost": 1,
        })
        weapon["location"] = "READY"
        loadout = next(p for p in owner.campaign_panels("authored-harbor")["surfaces"]
                       if p["id"] == "operations.loadout")
        assert loadout["data"]["entities"][player_id]["weapon"] == weapon
        final = owner.snapshot("authored-harbor").state
        assert final[f"{builder.PACKAGE_ID}.state"]["ground_objects"]["weapon_1"] is None
        assert final[f"{builder.PACKAGE_ID}.entities"][player_id]["ap"] == 4
