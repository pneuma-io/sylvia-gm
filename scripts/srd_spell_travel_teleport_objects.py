"""Object Teleport uses the same paid journey and canonical object receiving owners."""

from copy import deepcopy

if __package__:
    from .srd_spell_object_content import GEOMETRY_EXPORTS
    from .srd_spell_object_damage import QUALIFY, TELEPORT_PACKET
    from .srd_timed_actor_defenses import _call, _entity, _require, _set, _when
    from .srd_timed_senses import _optional, _push
else:
    from srd_spell_object_content import GEOMETRY_EXPORTS
    from srd_spell_object_damage import QUALIFY, TELEPORT_PACKET
    from srd_timed_actor_defenses import _call, _entity, _require, _set, _when
    from srd_timed_senses import _optional, _push


OBJECT_CAST_CHOICES = [
    _optional("teleport_object_subject", default=""),
    _optional("teleport_holder_willing", "boolean", False),
]
RELEASE = "release_srd_teleport_carried_object"


def _release(tools):
    """Capture the existing exact physical release, with paid arrival and consent authority."""
    owner = deepcopy(tools["drop_srd_heated_object"])
    if "target_state.inventory" not in str(owner["effects"]) or (
        "carried_by" not in str(owner["effects"])
    ):
        raise ValueError("Teleport release needs the genuine physical object release owner.")
    owner["description"] = (
        "Release the exact carried Teleport object from its saved willing holder."
    )
    owner["parameters"].insert(0, _entity("caster_state"))
    owner["effects"][:0] = [
        _require(
            "caster_state.teleport_resolution != null and "
            "caster_state.teleport_resolution.paid == true and "
            "caster_state.teleport_resolution.status == 'pending' and "
            "caster_state.teleport_resolution.mode == 'object' and "
            "caster_state.teleport_resolution.stage == 'arrival'",
            "A carried release belongs to an actual paid object Teleport arrival.",
        ),
        _set(
            "_teleport_release_member",
            "caster_state.teleport_resolution.party["
            "caster_state.teleport_resolution.arrival_index]",
        ),
        _require(
            "_teleport_release_member.target == object_id and "
            "_teleport_release_member.holder == _target_state_id and "
            "_teleport_release_member.holder_willing == true",
            "Release only the saved object from its actual consenting holder.",
        ),
    ]
    return owner


def bind_teleport_object_tools(generated, tools):
    """Add one object party without changing the creature party or paid stage machine."""
    generated = deepcopy(generated)
    preflight = generated["validate_srd_teleport_cast"]
    preflight["parameters"].extend(deepcopy(OBJECT_CAST_CHOICES))
    effects = preflight["effects"]
    start = next(
        i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_teleport_targets"
    )
    end = next(
        i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_teleport_familiarity"
    )
    creature_party = deepcopy(effects[start:end])
    object_party = [
        _require(
            "targets == null or len(targets) == 0",
            "Object Teleport cannot also select creature companions.",
        ),
        _call(
            "resolve_srd_object_content_geometry",
            {
                "caster_state": "{_caster_state_id}",
                "object_id": "{teleport_object_subject}",
                "range_feet": 10,
                "requires_sight": True,
            },
            list(GEOMETRY_EXPORTS),
        ),
        _set("_teleport_object", "_object_scene"),
        _call(
            QUALIFY,
            {"object_id": "{teleport_object_subject}"},
            ["_object_scene", "_object_profile"],
        ),
        _require(
            "(_object_profile.size == 'tiny' or _object_profile.size == 'small' or "
            "_object_profile.size == 'medium' or _object_profile.size == 'large') and "
            "(_teleport_object.metadata.size_rank == null or "
            "(_teleport_object.metadata.size_rank >= 0 and "
            "_teleport_object.metadata.size_rank <= 3))",
            "Teleport requires the actual object's Large-or-smaller size fact.",
        ),
        _require(
            "_teleport_object.metadata.worn_by == null",
            "Worn or anatomical object separation needs an admitted physical release owner.",
        ),
        _require(
            "_teleport_object.metadata.carried_by == null or teleport_holder_willing == true",
            "The actual creature holding or carrying this object must be willing.",
        ),
        _set("_teleport_holder", "''"),
        _when(
            "_teleport_object.metadata.carried_by != null",
            [
                _set("_teleport_holder", "_teleport_object.metadata.carried_by"),
            ],
        ),
        _set("_teleport_party", []),
        _push(
            "_teleport_party",
            {
                "target": "_teleport_object.id",
                "origin": "_teleport_object.position",
                "offset": [0, 0, 0],
                "holder": "_teleport_holder",
                "holder_willing": "teleport_holder_willing",
            },
        ),
    ]
    effects[start:end] = [
        _set("_teleport_mode", "'creature'"),
        _when("teleport_object_subject == ''", creature_party),
        _when(
            "teleport_object_subject != ''",
            [
                _set("_teleport_mode", "'object'"),
                *object_party,
            ],
        ),
    ]
    for i, e in enumerate(effects):
        if e.get("for_each", {}).get("as") == "_teleport_party_member":
            effects[i] = _when("_teleport_mode != 'object'", [e])
        if e.get("set", {}).get("field") == "caster_state.teleport_preflight":
            e["set"]["value"]["mode"] = "_teleport_mode"
    start_tool = generated["start_srd_teleport_resolution"]
    for e in start_tool["effects"]:
        if e.get("set", {}).get("field") == "caster_state.teleport_resolution":
            e["set"]["value"]["mode"] = "_teleport_preflight.mode"
    step = generated["step_srd_teleport_resolution"]
    for e in step["effects"]:
        if e.get("condition") == "_teleport_stage == 'mishap_damage'":
            body = e["on_true"]
            i = next(
                i for i, x in enumerate(body) if x.get("call") == "apply_srd_qualifying_damage"
            )
            body[i : i + 1] = [
                _when("caster_state.teleport_resolution.mode != 'object'", [body[i]]),
                _when(
                    "caster_state.teleport_resolution.mode == 'object'",
                    [
                        _call(
                            TELEPORT_PACKET,
                            {
                                "caster_state": "{_caster_state_id}",
                                "object_id": "{_teleport_packet.target}",
                                "component_1_amount": "{_teleport_packet.modified_amount}",
                                "component_1_damage_type": "force",
                            },
                            ["_object_damage_applied"],
                        ),
                        _set("_effective_damage", "_object_damage_applied"),
                    ],
                ),
            ]
        if e.get("condition") == "_teleport_stage == 'arrival'":
            body = e["on_true"]
            i = next(i for i, x in enumerate(body) if x.get("call") == "move_srd_travel_subject")
            body[i:i] = [
                _when(
                    "caster_state.teleport_resolution.mode == 'object'",
                    [
                        _call(
                            QUALIFY,
                            {"object_id": "{_teleport_member.target}"},
                            ["_object_scene", "_object_profile"],
                        ),
                        _require(
                            "_object_scene.metadata.worn_by == null and "
                            "(_object_scene.metadata.carried_by == null or "
                            "_object_scene.metadata.carried_by == _teleport_member.holder)",
                            "The Teleport object cannot acquire a different holder in transit.",
                        ),
                        _when(
                            "_object_scene.metadata.carried_by != null",
                            [
                                _call(
                                    RELEASE,
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "target_state": "{_teleport_member.holder}",
                                        "object_id": "{_teleport_member.target}",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ]
    generated[RELEASE] = _release(tools)
    return generated
