"""Select authored damage from the same four range bands as firearm difficulty."""


def selection(weapon, distance, output):
    """An absent profile keeps fixed damage; an explicit null band remains unresolved."""
    return [
        {"set": {"field": output, "value": f"{weapon}.damage"}},
        {
            "condition": f"{weapon}.range_damage != null",
            "on_true": [
                {"set": {"field": output, "value": f"{weapon}.range_damage[3]"}},
                *[{
                    "set": {"field": output, "value": f"{weapon}.range_damage[{band}]"},
                    "condition": f"{distance} <= {weapon}.range_breakpoints_m[{band}]",
                } for band in (2, 1, 0)],
            ],
            "on_false": [],
        },
    ]


def from_ray(weapon, ray, target_id, prefix):
    """Use actual target intersection distance, matching ordinary firearm range resolution."""
    return [
        {"set": {"field": prefix + "_distance", "value": 1000000}},
        {"set": {"field": prefix + "_intersections", "value": ray + ".intersections"}},
        {"for_each": {
            "collection": prefix + "_intersections", "as": prefix + "_intersection",
            "do": [{
                "set": {"field": prefix + "_distance", "value": prefix + "_intersection.distance"},
                "condition": prefix + "_intersection.id == " + target_id,
            }],
        }},
        *selection(weapon, prefix + "_distance", prefix + "_damage"),
    ]


def guard_attack(effects, ray_query):
    """Reject unresolved profile bands before the ordinary attack can spend or roll."""
    return [
        {"set": {"field": "_profile_damage", "value": "attacker.weapon.damage"}},
        {
            "condition": "attacker.weapon.range_damage != null",
            "on_true": [
                ray_query,
                *from_ray("attacker.weapon", "_profile_ray", "_target_id", "_profile"),
            ],
            "on_false": [],
        },
        {
            "condition": "attacker.weapon.range_damage == null or _profile_damage != null",
            "on_true": effects,
            "on_false": [
                {"set": {"field": "state.last_attack", "value": {}}},
                {"set": {"field": "state.last_attack.outcome",
                         "value": "'UNRESOLVED_RANGE_DAMAGE'"}},
                {"set": {"field": "state.last_attack.range_m", "value": "_profile_distance"}},
            ],
        },
    ]
