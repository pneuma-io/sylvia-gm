"""Authored stationary DEEP AIM, separate from ordinary AIM and paid firing."""

ENTRY = "specialty.sniper.entry.deep_aim"
STATE = "sniper_preparation"


def eligible():
    return f"attacker.class_id == 'SHARPSHOOTER' and '{ENTRY}' in attacker.specialties"


def capture_ordinary():
    """Remember a completed ordinary solution only for this selected Entry; grant no bonus."""
    return [
        {
            "set": {"field": f"{{_attacker_id}}.{STATE}.valid", "value": False},
            "condition": f"attacker.{STATE} != null",
        },
        {
            "condition": (
                eligible() + " and attacker.weapon != null and attacker.muzzle != null "
                "and attacker.center != null and target.center != null"
            ),
            "on_true": [
                {
                    "set": {
                        "field": f"{{_attacker_id}}.{STATE}",
                        "value": {
                            "valid": True,
                            "deep": False,
                            "target": "_target_id",
                            "weapon": "attacker.weapon.id",
                            "aim_ap": "ap",
                            "point": "aim_point",
                            "origin": "attacker.center",
                            "muzzle": "attacker.muzzle",
                            "target_center": "target.center",
                            "map_id": "state.spatial_scene.map.id",
                            "map_revision": "state.spatial_scene.map.revision",
                        },
                    }
                },
            ],
            "on_false": [],
        },
    ]


def binding_calculations():
    """Use the same bounded scalar checks in execution and read-only controls.

    The first playable qualifies an unchanged stationary scene, not a material
    movement tolerance, reacquisition rule, or reaction/turn lifetime. A changed
    map revision cannot silently revive an old paid solution after returning.
    """
    marker = f"attacker.{STATE}"
    calculations = [
        {
            "set": {
                "field": "_sniper_identity",
                "value": (
                    f"{marker} != null and {marker}.valid == true and {eligible()} "
                    f"and {marker}.target == _target_id and attacker.aim.target == _target_id "
                    f"and attacker.weapon != null and {marker}.weapon == attacker.weapon.id "
                    f"and attacker.aim.ap == {marker}.aim_ap and attacker.aim.ap > 0"
                ),
            }
        }
    ]
    for key, current in (
        ("origin", "attacker.center"),
        ("muzzle", "attacker.muzzle"),
        ("target_center", "target.center"),
        ("point", "aim_point"),
    ):
        expression = " and ".join(f"{marker}.{key}[{i}] == {current}[{i}]" for i in range(3))
        if key == "point":
            expression += " and " + " and ".join(
                f"{marker}.point[{i}] == attacker.aim.point[{i}]" for i in range(3)
            )
        calculations.append(
            {"set": {"field": "_sniper_" + key, "value": f"{marker} != null and {expression}"}}
        )
    calculations.extend(
        [
            {
                "set": {
                    "field": "_sniper_map",
                    "value": (
                        f"{marker} != null and {marker}.map_id == state.spatial_scene.map.id "
                        f"and {marker}.map_revision == state.spatial_scene.map.revision"
                    ),
                }
            },
            {
                "set": {
                    "field": "_sniper_bound",
                    "value": (
                        "_sniper_identity and _sniper_origin and _sniper_muzzle and "
                        "_sniper_target_center and _sniper_point and _sniper_map"
                    ),
                }
            },
        ]
    )
    return calculations


def checks():
    return [
        {"condition": condition, "reason": reason}
        for condition, reason in [
            (
                "attacker.active == true and attacker.downed == false",
                "This actor cannot prepare a shot.",
            ),
            (eligible(), "DEEP AIM requires SHARPSHOOTER and the selected SNIPER Entry."),
            ("attacker.ap >= 1", "DEEP AIM requires one additional AP."),
            (
                "attacker.weapon != null and attacker.weapon.location == 'READY' "
                "and attacker.weapon.controlled_by == null",
                "The actual firearm must be ready and controlled.",
            ),
            ("target.active == true", "The selected target is not active."),
            (
                "_sniper_bound == true",
                "Complete ordinary AIM on this target and point from the current "
                "firing position. Changed scene/solution bindings require a fresh preparation.",
            ),
            (
                f"attacker.{STATE}.deep == false",
                "This solution is already in DEEP AIM; no cumulative stage.",
            ),
            ("_deep_exposure.exposure > 0", "This target has no valid direct firing solution."),
        ]
    ]


def specification(attacker, target, point, param, exposure_query):
    """Pay only the additional stage after its own eligibility and real exposure checks."""
    requirements = checks()
    ready = " and ".join(f"({row['condition']})" for row in requirements)
    return {
        "capability": "trigger-discipline.firearms",
        "description": (
            "After completed ordinary AIM, pay one additional AP for SNIPER DEEP AIM against "
            "the same target, point, firearm and stationary firing solution. The next ordinary "
            "prepared attack receives -2 difficulty once. This does not perform or prepay firing, "
            "replace AIM2, stack stages or grant higher SNIPER benefits. This bounded stage "
            "requires an unchanged scene binding; movement tolerances, reacquisition, turn and "
            "reaction lifetimes remain unqualified."
        ),
        "parameters": [
            attacker,
            target,
            point("aim_point"),
            param("map_id", "string", from_state="state.spatial_scene.map.id"),
            param("incoming_suppression", "object", from_state="state.prepared.suppression"),
        ],
        "effects": [
            *binding_calculations(),
            exposure_query,
            *(
                {"require": {"condition": row["condition"], "error": row["reason"]}}
                for row in requirements
            ),
            {"call": "td_preparation_exposure",
             "params": {"actor": "{_attacker_id}", "event": "DEEP_AIM"}},
            *binding_calculations(),
            exposure_query,
            {
                "condition": ready,
                "on_true": [
                    {"decrement": {"field": "{_attacker_id}.ap", "amount": 1}},
                    {"set": {"field": f"{{_attacker_id}}.{STATE}.deep", "value": True}},
                    {"set": {"field": "_deep_stage_status", "value": "'PREPARED'"}},
                    {"set": {"field": "_deep_stage_spent", "value": 1}},
                ],
                "on_false": [
                    {"set": {"field": "_deep_stage_status", "value": "'INTERRUPTED'"}},
                ],
            },
        ],
        "costs_action": True,
    }


def attack_offset(exposure):
    """Add one authored -2 to this attack's existing solution, not one per projectile."""
    return [
        *binding_calculations(),
        {"set": {"field": "_deep_aim_offset", "value": 0}},
        {
            "set": {"field": "_deep_aim_offset", "value": -2},
            "condition": (
                f"_sniper_bound == true and attacker.{STATE}.deep == true "
                f"and {exposure}.exposure > 0"
            ),
        },
    ]


def consume():
    """A committed ordinary attack consumes the held solution, including a miss or retarget."""
    return [
        {
            "condition": f"attacker.{STATE} != null",
            "on_true": [
                {"set": {"field": f"{{_attacker_id}}.{STATE}.valid", "value": False}},
                {"set": {"field": f"{{_attacker_id}}.{STATE}.deep", "value": False}},
            ],
            "on_false": [],
        },
        {"set": {"field": "state.last_attack.deep_aim_offset", "value": "_deep_aim_offset"}},
    ]


def state_schema():
    """Optional paid/ordinary state: no default preparation or change to older character data."""
    from .network_state_schema import _array, _object

    vector = _array({"type": "number"}, 3, 3)
    text = {"type": "string", "minLength": 1}
    return _object(
        {
            "valid": {"type": "boolean"},
            "deep": {"type": "boolean"},
            "target": text,
            "weapon": text,
            "aim_ap": {"type": "integer", "minimum": 1, "maximum": 2},
            "point": vector,
            "origin": vector,
            "muzzle": vector,
            "target_center": vector,
            "map_id": text,
            "map_revision": {"type": "integer", "minimum": 0},
        }
    )


def state_schemas(existing):
    entities = next(row for row in existing if row["key"] == "entities")
    entities["schema"]["additionalProperties"]["properties"][STATE] = state_schema()
    return existing
