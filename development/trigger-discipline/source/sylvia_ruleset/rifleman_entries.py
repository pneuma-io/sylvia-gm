"""Owner-approved provisional GUNNER/OPERATOR entry rules; not final balance."""

GUNNER = "specialty.gunner.entry.sustained_fire_discipline"
OPERATOR = "specialty.operator.entry.weapon_manipulation"
CHAIN = "gunner_chain_weapon_id"
GUNNER_PARAMETER = "specialty.gunner.sustained_fire_discipline.recoil_reduction"
OPERATOR_PARAMETER = "specialty.operator.weapon_manipulation.ap_discount"
APPROVAL = "Sentinel_de771604e5ac81918159f49261ce26fd; TDH-09 r58"


def validate_configuration(values):
    """Do not let a later balance seed silently change the approved playable values."""
    for identifier in (GUNNER_PARAMETER, OPERATOR_PARAMETER):
        if values[identifier] != 1:
            raise ValueError(f"{identifier}: the approved provisional playable value is 1")


def has_entry(actor, entry):
    """Require the actual class and selected entry, including older actors without it."""
    return (
        f"{actor}.class_id == 'RIFLEMAN' and 'specialties' in {actor} "
        f"and '{entry}' in {actor}.specialties"
    )


def recoil_gain(actor, actor_id, ordinary, *, prefix):
    """Change only a committed action's new gain, then bind its physical firearm."""
    gain = f"{prefix}_recoil_gain"
    eligible = has_entry(actor, GUNNER)
    return [
        {"set": {"field": gain, "value": ordinary}},
        {
            "set": {
                "field": gain,
                "value": (f"max(0, {gain} - definitions.parameters.{GUNNER_PARAMETER})"),
            },
            "condition": (
                f"{eligible} and '{CHAIN}' in {actor} "
                f"and {actor}.{CHAIN} == {actor}.weapon.id "
                f"and {actor}.weapon.current_recoil > 0"
            ),
        },
        {"increment": {"field": f"{{{actor_id}}}.weapon.current_recoil", "amount": gain}},
        {
            "set": {"field": f"{{{actor_id}}}.{CHAIN}", "value": f"{actor}.weapon.id"},
            "condition": eligible,
        },
    ]


def end_chain(actor="actor", actor_id="_actor_id"):
    """Clear an existing execution marker only at a committed reset boundary."""
    return [
        {
            "set": {"field": f"{{{actor_id}}}.{CHAIN}", "value": None},
            "condition": f"'{CHAIN}' in {actor}",
        }
    ]


def manipulation_price(ordinary, *, actor="actor"):
    """Share one-assignment pricing with read-only controls; final GM totals bypass it.

    The existing bounded occurrence function converts eligibility to a 0/1 count
    without boolean arithmetic or repeated local assignments in preview metadata.
    """
    return [
        {
            "set": {
                "field": "_operator_manipulation_count",
                "value": f"occurrences(one({has_entry(actor, OPERATOR)}), true)",
            }
        },
        {
            "set": {
                "field": "_manipulation_ap",
                "value": (
                    f"{ordinary} + _operator_manipulation_count * "
                    f"(max(1, {ordinary} - definitions.parameters.{OPERATOR_PARAMETER}) "
                    f"- {ordinary})"
                ),
            },
            "condition": (f"{actor}.weapon != null and {ordinary} != null"),
        },
    ]
