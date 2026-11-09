"""Configured and retained watch reuse ordinary finite prepared-fire authority."""

from copy import deepcopy

from . import snapshot as quick_solution
from . import overwatch_lanes

COUNTER_PREP = "specialty.sentinel.tier_3.counter_prep"


def counter_prep_calculations(actor="actor", *, pure=False):
    """Keep eligibility within expression bounds and share it with pure previews."""
    effects = []
    for tier, names in ((1, ("broad_sector", "stable_nest", "fast_setup")),
                        (2, ("tracking_sector", "crossing_shot", "second_trigger"))):
        counts = [f"occurrences(one('specialty.sentinel.tier_{tier}.{name}' in "
                  f"{actor}.specialties), true)" for name in names]
        if not pure:
            effects.append({"set": {"field": f"_cp_tier{tier}", "value": 0}})
        effects.append({"set": {"field": f"_cp_tier{tier}", "value": " + ".join(counts)},
                        "condition": f"{actor}.specialties != null"})
    effects.append({"set": {"field": "_cp_eligible", "value": (
        f"{actor}.class_id == 'SHARPSHOOTER' and {actor}.level >= 6 and "
        f"{actor}.specialties != null and '{COUNTER_PREP}' in {actor}.specialties and "
        f"'specialty.sentinel.entry.prepared_sightline' in {actor}.specialties and "
        "_cp_tier1 >= 2 and _cp_tier2 >= 2"
    )}})
    return effects


def preparation_exposure(actor_id, event):
    """One declared preparation event, with no separate AIM/watch double dispatch."""
    return {
        "call": "td_preparation_exposure",
        "params": {"actor": "{" + actor_id + "}", "event": event},
        "export_context": {"_prep_interruption": "_prep_interruption"},
    }


def counter_prep_helper(param, attack):
    """Use the saved watch, actual zone/exposure and ordinary finite prepared shot."""
    effects = [
        {"set": {"field": "_cp_side", "value": "overwatcher.side"}},
        *counter_prep_calculations("overwatcher"),
        {"set": {"field": "_cp_watch", "value": (
            "prepared_watch.active == true and prepared_watch.counter_prep == true and "
            "prepared_watch.owner == _overwatcher_id and prepared_watch.shots > 0"
        )}},
        {"set": {"field": "_cp_actors", "value": (
            "actor.active == true and actor.downed == false and overwatcher.active == true "
            "and overwatcher.downed == false and actor.side != overwatcher.side"
        )}},
        {"set": {"field": "_cp_weapon", "value": (
            "overwatcher.weapon.chamber == 'LIVE' and overwatcher.weapon.location == 'READY' "
            "and overwatcher.weapon.controlled_by == null"
        )}},
        {"set": {"field": "_cp_stationary", "value": (
            "prepared_watch.stationary_origin == null or "
            "(prepared_watch.stationary_origin[0] == overwatcher.position_cell[0] and "
            "prepared_watch.stationary_origin[1] == overwatcher.position_cell[1])"
        )}},
        {"set": {"field": "_watch_stationary", "value": "_cp_stationary"}},
        *overwatch_lanes.validity('prepared_watch', invalidate='inspect_only == false'),
        {"set": {"field": "_cp_stationary", "value": "_watch_stationary"}},
        {"set": {"field": "_counter_prep_exposed", "value": False}},
        {"condition": (
            "_cp_watch == true and _cp_eligible == true and _cp_actors == true and "
            "_cp_weapon == true and _cp_stationary == true"
        ), "on_true": [
            {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"},
             "store_as": "_cp_snapshot"},
            {"spatial_query": {"scene": "state.spatial_scene", "operation": "crossed_zones",
                "snapshot": "{_cp_snapshot}", "query": {
                    "map_id": "{map_id}", "path": ["{actor.center}", "{actor.center}"],
                    "max_crossings": 4096,
                }}, "store_as": "_cp_area"},
            {"set": {"field": "_cp_crossings", "value": "_cp_area.crossings"}},
            {"set": {"field": "_cp_inside", "value": False}},
            {"for_each": {"collection": "_cp_crossings", "as": "_cp_crossing", "do": [
                {"set": {"field": "_cp_inside", "value": True},
                 "condition": "_cp_crossing.zone_id == prepared_watch.zone_id"},
            ]}},
            {"condition": "_cp_inside == true", "on_true": [
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "exposure",
                    "snapshot": "{_cp_snapshot}", "query": {
                        "map_id": "{map_id}", "observer": {"point": "{overwatcher.muzzle}"},
                        "target": {"entity_id": "{_actor_id}"},
                        "samples": {"horizontal": 3, "vertical": 3},
                        "include": ["geometry", "entities"],
                    }}, "store_as": "_cp_exposure"},
                {"set": {"field": "_counter_prep_exposed", "value": "_cp_exposure.exposure > 0"}},
            ]},
        ]},
        {"set": {"field": "_counter_prep_ready",
                 "value": "_counter_prep_exposed == true and inspect_only == false"}},
        {"set": {"field": "_cp_critical", "value": None}},
        *attack(prefix="_counter_prep", shooter="overwatcher", shooter_id="_overwatcher_id",
                target="actor", target_id="_actor_id", target_point="actor.center",
                record="state.last_counter_prep", condition="_counter_prep_ready == true",
                difficulty_modifier="0", critical_effect="_cp_critical", consume_round=True,
                choose_critical=True),
        {"decrement": {"field": "state.prepared.overwatch.shots", "amount": 1},
         "condition": "_counter_prep_ready == true"},
        {"set": {"field": "state.prepared.overwatch.active", "value": False},
         "condition": "_counter_prep_ready == true and prepared_watch.shots <= 1"},
        {"set": {"field": "state.last_counter_prep.event", "value": "event"},
         "condition": "_counter_prep_ready == true"},
    ]
    return {
        "description": "Inspect or resolve configured COUNTER-PREP using one saved OVERWATCH shot.",
        "parameters": [param("actor", "entity_id", resolve="entity"),
                       param("watch_bodies", "array", items="object", from_state="state.spatial_scene.entities"),
                       param("watch_zones", "array", items="object", from_state="state.spatial_scene.zones"),
                       param("overwatcher", "entity_id", resolve="entity"),
                       param("event", "string"), param("inspect_only", "boolean"),
                       param("prepared_watch", "object", from_state="state.prepared.overwatch"),
                       param("map_id", "string", from_state="state.spatial_scene.map.id")],
        "effects": effects, "metadata": {"costs_action": False},
    }


def preparation_helper(param):
    """Order current watch/suppression exposures, rechecking each before resolution."""
    def incoming(kind, inspect=False):
        watch = kind == "OVERWATCH"
        return {
            "call": "td_counter_prep_exposure" if watch else "td_suppression_exposure",
            "params": {"actor": "{_actor_id}",
                       "overwatcher" if watch else "suppressor":
                           "{_prep_watcher}" if watch else "{_prep_suppressor}",
                       "event": "{event}", "inspect_only": inspect},
            "export_context": ({
                "_prep_watch_ready": "_counter_prep_exposed",
                "_prep_watch_side": "_cp_side",
            } if watch else {
                "_prep_supp_ready": "_suppression_exposed", "_prep_supp_side": "_suppression_side",
            }) if inspect else {
                "_prep_fired": "_counter_prep_ready" if watch else "_incoming_suppression_ready",
            },
        }

    def resolve(kind):
        return [incoming(kind), {"set": {"field": "_prep_interruption", "value": repr(
            "COUNTER_PREP" if kind == "OVERWATCH" else "SUPPRESSION"
        )}, "condition": "_prep_fired == true and actor.downed == true"}]

    effects = [
        {"set": {"field": "_prep_interruption", "value": "'NONE'"}},
        {"set": {"field": "_prep_watcher", "value": "_actor_id"}},
        {"set": {"field": "_prep_suppressor", "value": "_actor_id"}},
        {"set": {"field": "_prep_watcher", "value": "prepared.overwatch.owner"},
         "condition": "prepared.overwatch.active == true and prepared.overwatch.owner != null"},
        {"set": {"field": "_prep_suppressor", "value": "prepared.suppression.owner"},
         "condition": "prepared.suppression.active == true and prepared.suppression.owner != null"},
        incoming("OVERWATCH", True), incoming("SUPPRESSION", True),
        {"set": {"field": "_prep_order", "value": "'OVERWATCH'"}},
        {"set": {"field": "_prep_order", "value": "'SUPPRESSION'"},
         "condition": "_prep_watch_ready == false and _prep_supp_ready == true"},
        {"set": {"field": "_prep_order", "value": "'SUPPRESSION'"},
         "condition": "_prep_watch_side == active_side and _prep_supp_side != active_side"},
        {"request_choice": {"choice_id": "preparation-trigger-order",
            "prompt": "Choose which same-side preparation reaction resolves first.",
            "options": [{"id": "OVERWATCH", "label": "Counter-prep first"},
                        {"id": "SUPPRESSION", "label": "Suppression first"}]},
         "store_as": "_prep_order", "condition": (
             "_prep_watch_ready == true and _prep_supp_ready == true and "
             "_prep_watch_side == _prep_supp_side"
         )},
        {"condition": "_prep_order == 'OVERWATCH'",
         "on_true": resolve("OVERWATCH") + resolve("SUPPRESSION"),
         "on_false": resolve("SUPPRESSION") + resolve("OVERWATCH")},
    ]
    return {
        "description": (
            "Resolve the single exposure event from an otherwise legal firing preparation."
        ),
        "parameters": [param("actor", "entity_id", resolve="entity"), param("event", "string"),
                       param("prepared", "object", from_state="state.prepared"),
                       param("active_side", "string", from_state="state.active_side")],
        "effects": effects, "metadata": {"costs_action": False},
    }


def _incoming_setup(tool, param, event, ready, commits):
    """Resolve one setup exposure after prerequisites, before any combined benefit."""
    tool["parameters"].append(
        param("incoming_suppression", "object", from_state="state.prepared.suppression"),
    )
    return [
        preparation_exposure("_actor_id", event),
        {"condition": ready, "on_true": commits, "on_false": _interrupted_setup()},
    ]


def _interrupted_setup():
    """Report incoming fire without claiming any watch, AIM or own AP commitment."""
    return [
        {"set": {"field": "_sentinel_status", "value": "'INTERRUPTED_BY_SUPPRESSION'"}},
        {"set": {"field": "state.last_trigger.status",
                 "value": "'INTERRUPTED_BY_SUPPRESSION'"}},
        {"set": {"field": "_sentinel_status", "value": "'INTERRUPTED_BY_COUNTER_PREP'"},
         "condition": "_prep_interruption == 'COUNTER_PREP'"},
        {"set": {"field": "state.last_trigger.status", "value": "'INTERRUPTED_BY_COUNTER_PREP'"},
         "condition": "_prep_interruption == 'COUNTER_PREP'"},
    ]


def movement_effects(actor_id="_actor_id", prepared="prepared"):
    """Leaving a combined stationary sightline ends it without reviving on return."""
    return overwatch_lanes.break_lane(actor_id, prepared) + [{"condition": (
        f"{prepared}.overwatch.owner == {actor_id} and "
        f"{prepared}.overwatch.stationary_origin != null"
    ), "on_true": [
        {"set": {"field": "_watch_stationary", "value": False}},
        {"set": {"field": "state.prepared.overwatch.active", "value": False}},
        {"set": {"field": "state.prepared.overwatch.shots", "value": 0}},
        {"set": {"field": f"{{{actor_id}}}.aim.ap", "value": 0}},
        {"set": {"field": f"{{{actor_id}}}.aim.target", "value": "null"}},
        {"set": {"field": f"{{{actor_id}}}.aim.point", "value": "null"}},
    ], "on_false": []}]


def specification(ordinary, param):
    """Prepare a retained watch without selecting the unresolved trigger-count parameter."""
    tool = deepcopy(ordinary)
    tool["description"] = (
        "Prepare SECOND TRIGGER overwatch with an explicitly adjudicated total shot allowance "
        "greater than the ordinary allowance. Requires the actual selected specialty. Pays "
        "ordinary setup AP; the ordinary resolver validates each trigger and consumes each "
        "shot's ammunition and recoil. This does not grant prepared AIM, extra sectors, "
        "stationary discounts or a default specialty quantity."
    )
    tool["parameters"].append(param("shot_allowance", "integer", minimum=2))
    tool["effects"].insert(0, {
        "set": {"field": "_sentinel_status", "value": "'PREPARATION_REJECTED'"},
    })
    prepare = tool["effects"][-1]
    prepare["condition"] += (
        " and 'specialty.sentinel.tier_2.second_trigger' in actor.specialties"
        " and actor.active == true and actor.downed == false"
        " and shot_allowance > definitions.parameters.overwatch.shot_allowance"
    )
    for effect in prepare["on_true"]:
        if effect.get("set", {}).get("field") == "state.prepared.overwatch.shots":
            effect["set"]["value"] = "shot_allowance"
    prepare["on_true"].append({"set": {"field": "_sentinel_status", "value": "'PREPARED'"}})
    prepare["on_true"].append({"set": {
        "field": "_sentinel_setup_ap_spent", "value": "definitions.parameters.overwatch.setup_ap",
    }})
    prepare["on_true"] = _incoming_setup(
        tool, param, "PREPARE_SENTINEL_OVERWATCH", prepare["condition"], prepare["on_true"],
    )
    tool["effects"].insert(0, {"set": {"field": "_sentinel_setup_ap_spent", "value": 0}})
    return tool


def sightline_specification(ordinary, aim, param):
    """Combine one ordinary AIM action and watch with a GM-supplied reduced total cost."""
    tool = deepcopy(ordinary)
    tool["description"] = (
        "Prepare SENTINEL's PREPARED SIGHTLINE at this actual stationary position. Supply "
        "the adjudicated combined ap_cost and ordinary aim_ap (1 or 2); combined cost must "
        "be less than separate OVERWATCH plus AIM. Requires the ENTRY, a current zone, "
        "a usable weapon and real exposure to the selected aim target. Stores the normal "
        "target-specific AIM solution and ordinary shot allowance. A selected SECOND TRIGGER "
        "can use an explicit greater shot_allowance in the same setup; omit it or use API "
        "sentinel 0 for ordinary allowance. Movement invalidates this stationary preparation. "
        "No discount or trigger-count default is selected."
    )
    tool["parameters"].extend(deepcopy(aim["parameters"][1:3]))
    aim_ap = deepcopy(aim["parameters"][3])
    aim_ap["name"] = "aim_ap"
    tool["parameters"].extend([
        aim_ap, param("ap_cost", "integer", minimum=0),
        param("shot_allowance", "integer", required=False, default=0, minimum=0),
    ])
    prepare = tool["effects"][-1]
    prepare["condition"] = prepare["condition"].replace(
        "actor.ap >= definitions.parameters.overwatch.setup_ap", "actor.ap >= ap_cost",
    ) + (
        " and actor.active == true and actor.downed == false and target.active == true"
        " and 'specialty.sentinel.entry.prepared_sightline' in actor.specialties"
        " and ap_cost < definitions.parameters.overwatch.setup_ap + aim_ap"
        " and _sightline_shots_allowed == true"
    )
    commits = prepare["on_true"]
    commits[0]["decrement"]["amount"] = "ap_cost"
    for effect in commits:
        if effect.get("set", {}).get("field") == "state.prepared.overwatch.shots":
            effect["set"]["value"] = "_sightline_shots"
    commits.extend([
        {"set": {"field": "{_actor_id}.aim.target", "value": "_target_id"}},
        {"set": {"field": "{_actor_id}.aim.point", "value": "aim_point"}},
        {"set": {"field": "{_actor_id}.aim.ap", "value": "aim_ap"}},
        *quick_solution.performed("aimed_targets", "actor", "_actor_id"),
        {"set": {"field": "state.prepared.overwatch.stationary_origin",
                 "value": "actor.position_cell"}},
        {"set": {"field": "state.focused_actor", "value": "_actor_id"}},
        {"set": {"field": "_sentinel_status", "value": "'PREPARED'"}},
        {"set": {"field": "_sentinel_setup_ap_spent", "value": "ap_cost"}},
    ])
    exposure_query = {
        "spatial_query": {"scene": "state.spatial_scene", "operation": "exposure",
            "query": {"map_id": "{map_id}", "observer": {"point": "{actor.muzzle}"},
                "target": {"entity_id": "{_target_id}"},
                "samples": {"horizontal": 3, "vertical": 3},
                "include": ["geometry", "entities"]}}, "store_as": "_sightline_exposure",
    }
    # This authored combined setup is one action/exposure, not separate calls to
    # AIM and ordinary watch. Requery its firing solution after incoming effects.
    prepare["on_true"] = [
        deepcopy(exposure_query),
        {"condition": "_sightline_exposure.exposure > 0", "on_true": _incoming_setup(
            tool, param, "PREPARE_SENTINEL_SIGHTLINE", prepare["condition"], [
                deepcopy(exposure_query),
                {"condition": "_sightline_exposure.exposure > 0", "on_true": commits,
                 "on_false": _interrupted_setup()},
            ],
        )},
    ]
    tool["effects"].insert(0, {
        "set": {"field": "_sentinel_status", "value": "'PREPARATION_REJECTED'"},
    })
    tool["effects"][1:1] = [
        {"set": {"field": "_sentinel_setup_ap_spent", "value": 0}},
        {"set": {"field": "_sightline_shots",
                 "value": "definitions.parameters.overwatch.shot_allowance"}},
        {"set": {"field": "_sightline_shots_allowed", "value": "shot_allowance == 0"}},
        {"condition": (
            "actor.specialties != null and "
            "shot_allowance > definitions.parameters.overwatch.shot_allowance and "
            "'specialty.sentinel.tier_2.second_trigger' in actor.specialties"
        ), "on_true": [
            {"set": {"field": "_sightline_shots", "value": "shot_allowance"}},
            {"set": {"field": "_sightline_shots_allowed", "value": True}},
        ], "on_false": []},
    ]
    return tool


def state_schemas(existing):
    """Type only the optional configuration; older ordinary watches stay valid."""
    from .network_state_schema import _object

    world = next(row for row in existing if row["key"] == "state")["schema"]
    watch = _object({"counter_prep": {"type": "boolean"}}, (), additional=True)
    world["properties"]["prepared"] = _object({"overwatch": watch}, (), additional=True)
    return existing
