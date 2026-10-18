"""Area spells reuse the final prepared-object durability suffix without an attack."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _replace_tools
else:
    from srd_scene_relations import _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _replace_tools


QUALIFY = "qualify_srd_area_prepared_object"
PACKET = "apply_srd_area_object_damage"
TELEPORT_PACKET = "apply_srd_teleport_object_damage"
TYPES = [
    "acid",
    "bludgeoning",
    "cold",
    "fire",
    "force",
    "lightning",
    "necrotic",
    "piercing",
    "poison",
    "psychic",
    "radiant",
    "slashing",
    "thunder",
]


def _has_profile_assignment(effect):
    """Find the actual validation boundary even when a spell object wraps ordinary validation."""
    pending = [effect]
    while pending:
        node = pending.pop()
        if node.get("set", {}).get("field") == "_object_profile":
            return True
        for branch in ("on_true", "on_false", "do"):
            pending.extend(node.get(branch, []))
    return False


def _qualification(tools):
    """Retain actual source-aware profile validation before the attack's sight/range work."""
    effects = tools["resolve_srd_spell_object_relation"]["effects"]
    end = next(
        i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_object_invisible"
    )
    validation = deepcopy(effects[:end])
    if "_object_profile.hp > 0" not in str(validation):
        raise ValueError("Area object damage needs the final prepared-object validator.")
    return _tool(
        "Validate an actual prepared object using its existing source-owned statistics.",
        [_parameter("object_id")],
        validation,
    )


def _packet(tools, *, teleport=False):
    """Mitigate two real components, then retain the original one-HP write and callbacks."""
    owner = tools["resolve_srd_spell_object_attack"]
    start = next(
        i
        for i, e in enumerate(owner["effects"])
        if e.get("set", {}).get("field") == "_object_hp_before"
    )
    suffix = deepcopy(owner["effects"][start:])
    hit = [
        e
        for e in suffix
        if e.get("condition")
        == "_object_attack_kind == 'hit' or _object_attack_kind == 'critical_hit'"
    ]
    if len(hit) != 1:
        raise ValueError("Area damage needs the one final object durability suffix.")
    rolled = [
        i
        for i, e in enumerate(hit[0]["on_true"])
        if e.get("set", {}).get("field") == "_object_damage_rolled"
    ]
    if len(rolled) != 1:
        raise ValueError("Area damage needs the object's original raw damage boundary.")
    mitigation = deepcopy(hit[0]["on_true"][rolled[0] + 1 :])
    if "modify_srd_actor_damage_roll" in str(mitigation):
        raise ValueError("An already adjusted area roll cannot pay a second raw penalty.")
    components = []
    for number in (1, 2):
        components.extend(
            [
                _set("damage_type", f"component_{number}_damage_type"),
                _set("_object_damage_rolled", f"component_{number}_amount"),
                _set("_object_damage_applied", 0),
                *deepcopy(mitigation),
                _set(f"_area_object_component_{number}_applied", "_object_damage_applied"),
            ]
        )
    where = suffix.index(hit[0])
    suffix[where : where + 1] = [
        *components,
        _set(
            "_area_object_component_2_applied",
            "min(max(0, _object_hp_before - "
            "_area_object_component_1_applied), _area_object_component_2_applied)",
        ),
        _set("_object_damage_rolled", "component_1_amount + component_2_amount"),
        _set(
            "_object_damage_applied",
            "min(_object_hp_before, "
            "_area_object_component_1_applied + _area_object_component_2_applied)",
        ),
    ]
    if "_object_attack_kind" in str(suffix) or "_object_damage_roll.total" in str(suffix):
        raise ValueError("Area object damage must not depend on a fictional attack.")
    lookup = deepcopy(tools["resolve_srd_spell_object_relation"]["effects"])
    lookup = lookup[: next(i for i, e in enumerate(lookup) if _has_profile_assignment(e))]
    eligibility = [
        _when(
            "_object_scene.metadata.worn_by == null and _object_scene.metadata.carried_by == null",
            [
                _require(
                    "_object_scene.metadata.magical != null",
                    "Record whether the selected object is actually magical.",
                ),
                _when(
                    "_object_scene.metadata.magical == false",
                    [
                        _call(
                            QUALIFY,
                            {"object_id": "{object_id}"},
                            ["_object_scene", "_object_profile"],
                        ),
                        _set("_area_object_eligible", True),
                        *suffix,
                    ],
                ),
            ],
        ),
    ]
    if teleport:
        eligibility = [
            _require(
                "caster_state.teleport_resolution != null and "
                "caster_state.teleport_resolution.paid == true and "
                "caster_state.teleport_resolution.status == 'pending' and "
                "caster_state.teleport_resolution.mode == 'object' and "
                "caster_state.teleport_resolution.stage == 'mishap_damage' and "
                "caster_state.teleport_resolution.pending_mishap != null",
                "An object Mishap needs the actual paid pending Teleport packet.",
            ),
            _set(
                "_teleport_object_member",
                "caster_state.teleport_resolution.party["
                "caster_state.teleport_resolution.mishap_index]",
            ),
            _set("_teleport_object_packet", "caster_state.teleport_resolution.pending_mishap"),
            _require(
                "_teleport_object_member.target == object_id and "
                "_teleport_object_packet.target == object_id and "
                "_teleport_object_packet.modified_amount == component_1_amount and "
                "component_1_damage_type == 'force' and component_2_amount == 0",
                "Apply only this object's cached real Force Mishap packet.",
            ),
            _call(QUALIFY, {"object_id": "{object_id}"}, ["_object_scene", "_object_profile"]),
            _set("_area_object_eligible", True),
            *suffix,
        ]
    return _tool_scene(
        (
            "Apply the exact paid object Teleport Mishap using the existing durability owner."
            if teleport
            else ("Apply genuine area-roll components to an unattended "
                  "nonmagical prepared object once.")
        ),
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("component_1_amount", "integer", minimum=0),
            _parameter("component_1_damage_type", "enum", enum_values=TYPES),
            _parameter("component_2_amount", "integer", required=False, default=0, minimum=0),
            _parameter(
                "component_2_damage_type",
                "enum",
                required=False,
                default="force",
                enum_values=TYPES,
            ),
        ],
        [
            _set("_area_object_eligible", False),
            _set("_object_damage_applied", 0),
            _set("_object_hp_before", None),
            _set("_object_hp_after", None),
            _set("_object_destroyed", None),
            _set("_area_object_component_1_applied", 0),
            _set("_area_object_component_2_applied", 0),
            *lookup,
            *eligibility,
        ],
    )


def _preparation_facts(tools):
    """Add a factual magic classification to preparation without changing its statistic table."""
    changed = {}
    for name in ("prepare_srd_spell_object", "prepare_srd_spell_object_node"):
        tool = deepcopy(tools[name])
        if "magic_status" not in {p["name"] for p in tool["parameters"]}:
            tool["parameters"].append(
                _parameter(
                    "magic_status",
                    "enum",
                    required=False,
                    default="unrecorded",
                    enum_values=["unrecorded", "nonmagical", "magical"],
                )
            )
            for index, effect in enumerate(tool["effects"]):
                if effect.get("call") == "prepare_srd_spell_object_node":
                    effect["params"]["magic_status"] = "{magic_status}"
                if effect.get("set", {}).get("field") == "_object_metadata_changes":
                    tool["effects"].insert(
                        index + 1,
                        _when(
                            "magic_status != 'unrecorded'",
                            [
                                _set(
                                    "_area_object_magic_fact",
                                    {"magical": "magic_status == 'magical'"},
                                ),
                                _set(
                                    "_object_metadata_changes",
                                    "merge(_object_metadata_changes, _area_object_magic_fact)",
                                ),
                            ],
                        ),
                    )
                    break
        changed[name] = tool
    return changed


def bind_runtime_consumers(text):
    """Capture the current final object owner before consumers and saved continuations copy it."""
    tools = _load_runtime(text)["tools"]
    generated = {
        QUALIFY: _qualification(tools),
        PACKET: _packet(tools),
        TELEPORT_PACKET: _packet(tools, teleport=True),
        **_preparation_facts(tools),
    }
    return _replace_tools(text, {k: v for k, v in generated.items() if tools.get(k) != v})
