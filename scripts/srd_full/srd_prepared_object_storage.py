"""Prepared objects share one durability profile in Narrative and Scene play."""

import re
from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _when
    from .srd_spell_object_damage import _packet
    from .srd_spell_object_targets import _persist_profile, _validate_profile
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
    )
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import _when
    from srd_spell_object_damage import _packet
    from srd_spell_object_targets import _persist_profile, _validate_profile
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
    )
    from srd_timed_senses import _replace_tools


QUALIFY = "qualify_srd_entity_prepared_object"
STORE = "persist_srd_entity_object_damage"
PACKET = "apply_srd_entity_object_damage"
TELEPORT_PACKET = "apply_srd_entity_teleport_object_damage"
EXPORTS = (
    "_object_profile",
    "_object_hp_before",
    "_object_hp_after",
    "_object_destroyed",
    "_object_damage_applied",
    "_object_reverted",
)


def _private_values(value):
    """Captured input names become genuine private locals in the storage adapter."""
    if isinstance(value, str):
        for field in ("damage_type", "object_id"):
            value = re.sub(r"(?<![\w.])" + field + r"(?!\w)", "_storage_" + field, value)
        return value
    if isinstance(value, list):
        return [_private_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _private_values(item) for key, item in value.items()}
    return value


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _qualifier():
    """Validate canonical object statistics and a truthful optional same-ID projection."""
    validation = _validate_profile()
    validation[0] = _set("_object_profile", "object_state.srd_spell_object")
    return _tool(
        "Validate the admitted object entity's existing Breaking Objects profile.",
        [_entity("object_state")],
        [
            _require(
                "object_state.is_object == true and object_state.creature_kind == 'object' "
                "and object_state.creature_type == 'object'",
                "Use an actual admitted object identity.",
            ),
            _set("_object_scene", None),
            _when(
                "object_state.object_scene_id != null and object_state.object_scene_id != ''",
                [
                    _require(
                        "object_state.object_scene_id == _object_state_id",
                        "The optional object projection must use the same actual identity.",
                    ),
                    _call(
                        "qualify_srd_area_prepared_object",
                        {"object_id": "{_object_state_id}"},
                        ["_object_scene", "_object_profile"],
                    ),
                    _require(
                        "_object_scene.metadata.participant_ref == null and "
                        "_object_scene.metadata.is_object == true and "
                        "_object_scene.metadata.object_entity_ref == _object_state_id and "
                        "object_state.srd_spell_object != null and "
                        "len(object_state.srd_spell_object) == 9",
                        "The Scene projection must reference this exact canonical object profile.",
                    ),
                    _set(
                        "_storage_profile_fields",
                        [
                            repr(field)
                            for field in (
                                "kind",
                                "source",
                                "material",
                                "size",
                                "durability",
                                "armor_class",
                                "max_hp",
                                "hp",
                                "destroyed",
                            )
                        ],
                    ),
                    {
                        "for_each": {
                            "collection": "_storage_profile_fields",
                            "as": "_storage_profile_field",
                            "do": [
                                _require(
                                    "object_state.srd_spell_object[_storage_profile_field] == "
                                    "_object_profile[_storage_profile_field]",
                                    "Canonical and Scene object profile fields must agree.",
                                )
                            ],
                        }
                    },
                ],
                validation,
            ),
            _require(
                "object_state.hp == _object_profile.hp and "
                "object_state.max_hp == _object_profile.max_hp and "
                "object_state.armor_class == _object_profile.armor_class",
                "The object entity and its canonical durability profile must agree.",
            ),
        ],
    )


def _durability(tools):
    """Capture the final one-profile write rather than introducing another HP calculation."""
    packet = _packet(tools)
    found = [
        deepcopy(body[i:])
        for body in _lists(packet["effects"])
        for i, effect in enumerate(body)
        if effect.get("set") == {"field": "_object_hp_before", "value": "_object_profile.hp"}
    ]
    if len(found) != 1:
        raise ValueError("Canonical storage needs the one final object durability suffix.")
    suffix = found[0]
    loss = next(
        i for i, e in enumerate(suffix) if e.get("condition") == "_object_damage_applied > 0"
    )
    if [e.get("set", {}).get("field") for e in suffix[loss - 2 : loss]] != [
        "_object_hp_after",
        "_object_destroyed",
    ]:
        raise ValueError("Retain the existing object loss and destruction calculation.")
    persist = suffix[loss]["on_true"]
    profile = next(
        i for i, e in enumerate(persist) if e.get("set", {}).get("field") == "_object_profile"
    )
    mutation = next(i for i, e in enumerate(persist) if "spatial_mutation" in e)
    scene_write = deepcopy(persist[profile + 1 : mutation + 1])
    callbacks = deepcopy(persist[mutation + 1 :])
    persist[profile + 1 :] = [
        _set("object_state.srd_spell_object", "_object_profile"),
        _set("object_state.hp", "_object_profile.hp"),
        _set("object_state.is_alive", "_object_profile.hp > 0"),
        _when("_object_scene != null", scene_write),
        _when(
            "_object_destroyed == true and object_state.true_polymorph_object_source_id != null",
            [
                _call(
                    "retire_srd_true_polymorph_object_source",
                    {
                        "target_state": "{_object_state_id}",
                        "source_id": "{object_state.true_polymorph_object_source_id}",
                        "reason": "destroyed",
                    },
                ),
                _set("_object_reverted", "object_state.is_object != true"),
            ],
        ),
        _when("_object_scene != null and _object_reverted != true", callbacks),
    ]
    return _tool(
        "Write one final object durability loss, restoring an original body "
        "before destruction callbacks.",
        [_entity("object_state"), _parameter("amount", "integer", minimum=0)],
        [
            _call(
                QUALIFY,
                {"object_state": "{_object_state_id}"},
                ["_object_scene", "_object_profile"],
            ),
            _set("_storage_object_id", "_object_state_id"),
            _set("_object_hp_before", "_object_profile.hp"),
            _set("_object_damage_applied", "min(_object_hp_before, amount)"),
            _set("_object_reverted", False),
            *_private_values(suffix[loss - 2 : loss + 1]),
        ],
    )


def _entity_packet(tools, *, teleport=False):
    """Retain current typed mitigation; source callers alone admit their eligible targets."""
    packet = _packet(tools, teleport=teleport)
    body = next(
        b
        for b in _lists(packet["effects"])
        if any(
            e.get("set") == {"field": "_object_hp_before", "value": "_object_profile.hp"} for e in b
        )
    )
    suffix = deepcopy(body)
    start = next(
        i
        for i, e in enumerate(suffix)
        if e.get("set") == {"field": "_object_hp_before", "value": "_object_profile.hp"}
    )
    suffix = suffix[start:]
    loss = next(
        i for i, e in enumerate(suffix) if e.get("condition") == "_object_damage_applied > 0"
    )
    callbacks = deepcopy(suffix[loss + 1 :])
    suffix[loss - 2 :] = [
        _call(
            STORE,
            {"object_state": "{_object_state_id}", "amount": "{_object_damage_applied}"},
            EXPORTS,
        ),
        _when("_object_scene != null and _object_reverted != true", callbacks),
    ]
    guards = []
    if teleport:
        # These are the genuine paid-stage/member/cached-packet guards from Teleport's policy.
        guards = deepcopy(body[:start])
        call = next(
            i for i, e in enumerate(guards) if e.get("call") == "qualify_srd_area_prepared_object"
        )
        paid = next(
            i
            for i, e in enumerate(guards)
            if "caster_state.teleport_resolution.paid" in e.get("require", {}).get("condition", "")
        )
        guards = guards[paid:call]
    parameters = deepcopy(packet["parameters"])
    parameters = [p for p in parameters if p["name"] != "object_id"]
    parameters.append(_entity("object_state"))
    return _tool(
        "Receive actual source-qualified damage on a canonical prepared object "
        "without requiring coordinates.",
        parameters,
        [
            _set("_storage_object_id", "_object_state_id"),
            *_private_values(guards),
            _call(
                QUALIFY,
                {"object_state": "{_object_state_id}"},
                ["_object_scene", "_object_profile"],
            ),
            _set("_area_object_eligible", True),
            *_private_values(suffix),
        ],
    )


def _scene_receivers(tools):
    """A canonical Scene mirror delegates its write; the old nonmirror branch stays exact."""
    changed = {}
    for name in (
        "resolve_srd_spell_object_attack",
        "apply_srd_area_object_damage",
        "apply_srd_teleport_object_damage",
    ):
        owner = deepcopy(tools[name])
        if STORE in str(owner["effects"]):
            continue
        for body in _lists(owner["effects"]):
            for i, e in enumerate(body):
                if e.get("condition") != "_object_damage_applied > 0":
                    continue
                original = deepcopy(e["on_true"])
                e["on_true"] = [
                    _when(
                        "_object_scene.metadata.object_entity_ref != null and "
                        "_object_scene.metadata.object_entity_ref != ''",
                        [
                            _call(
                                STORE,
                                {
                                    "object_state": "{_object_scene.metadata.object_entity_ref}",
                                    "amount": "{_object_damage_applied}",
                                },
                                EXPORTS,
                            )
                        ],
                        original,
                    ),
                ]
                # Final Ice/Jar callbacks stay source-owned, and never destroy a restored creature.
                tail = deepcopy(body[i + 1 :])
                if tail:
                    body[i + 1 :] = [_when("_object_reverted != true", tail)]
                break
        owner["effects"].insert(0, _set("_object_reverted", False))
        changed[name] = owner
    return changed


def _admission(tools):
    """Stamp only the truthful same-ID mirror after existing factual object admission."""
    name = "prepare_srd_true_polymorph_object"
    owner = deepcopy(tools[name])
    if "object_entity_ref" in str(owner["effects"]):
        return {}
    write = _persist_profile()
    # Actual object facts already copied the existing Scene profile and created the entity.
    write.insert(0, _set("_object_profile", "_tp_object_profile"))
    write.insert(0, _set("_object_scene", "_info_scene_entity"))
    metadata = next(e for e in write if e.get("set", {}).get("field") == "_object_metadata_changes")
    metadata["set"]["value"].update(
        object_entity_ref="_tp_admitted_object_id",
        magical="magical",
        worn_by=None,
        carried_by=None,
    )
    merge = write.index(metadata) + 1
    for field in ("worn_by", "carried_by"):
        write[merge:merge] = [
            _when(
                field + " != ''",
                [
                    _set("_storage_holder_fact", {field: field}),
                    _set(
                        "_object_metadata_changes",
                        "merge(_object_metadata_changes, _storage_holder_fact)",
                    ),
                ],
            )
        ]
        merge += 1
    guards = [
        _require(
            "_info_scene_entity.metadata.magical == null or "
            "_info_scene_entity.metadata.magical == magical",
            "Canonical object facts must preserve the Scene object's known magic status.",
        )
    ]
    for field in ("worn_by", "carried_by"):
        guards.append(
            _require(
                f"_info_scene_entity.metadata.{field} == null or "
                f"_info_scene_entity.metadata.{field} == '' or "
                f"_info_scene_entity.metadata.{field} == {field}",
                "Canonical object facts must preserve the Scene object's actual holder.",
            )
        )
    owner["effects"].append(_when("scene_object == true", guards + write))
    normalized = _tool("", owner["parameters"], owner["effects"])
    owner["effects"] = normalized["effects"]
    return {name: owner}


def bind_runtime_consumers(text):
    """Emit after final prepared-object and True Polymorph owners, before callers capture them."""
    tools = _load_runtime(text)["tools"]
    if "retire_srd_true_polymorph_object_source" not in tools:
        raise ValueError(
            "Canonical object storage needs the actual transformed-source retirement owner."
        )
    # Always derive from the unchanged factories; repeat binding must not copy this adapter.
    base = deepcopy(tools)
    for name in (
        "resolve_srd_spell_object_attack",
        "apply_srd_area_object_damage",
        "apply_srd_teleport_object_damage",
    ):
        if STORE in str(base[name]["effects"]):
            for body in _lists(base[name]["effects"]):
                for e in body:
                    if e.get("condition") == "_object_damage_applied > 0":
                        branch = e["on_true"][0]
                        e["on_true"] = deepcopy(branch["on_false"])
            base[name]["effects"] = [
                e
                for e in base[name]["effects"]
                if e.get("set", {}).get("field") != "_object_reverted"
            ]
            for body in _lists(base[name]["effects"]):
                for i, e in enumerate(body):
                    if e.get("condition") == "_object_reverted != true":
                        body[i : i + 1] = e["on_true"]
    changed = {
        QUALIFY: _qualifier(),
        STORE: _durability(base),
        PACKET: _entity_packet(base),
        TELEPORT_PACKET: _entity_packet(base, teleport=True),
        **_scene_receivers(tools),
        **_admission(tools),
    }
    # Keep optional Scene effects on the canonical package path without prepending a snapshot.
    for owner in changed.values():
        for body in _lists(owner["effects"]):
            for effect in body:
                for kind in ("spatial_query", "spatial_mutation"):
                    if kind in effect:
                        effect[kind]["scene"] = "state.spatial_scene"
    return _replace_tools(text, {k: v for k, v in changed.items() if tools.get(k) != v})
