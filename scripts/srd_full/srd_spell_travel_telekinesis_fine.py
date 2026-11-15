"""Telekinesis fine control uses the actual ObjectContent access body and its saved locks."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _tool_scene
    from .srd_spell_travel_levitate import _body
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _public, _replace_tools
else:
    from srd_scene_relations import _tool_scene
    from srd_spell_travel_levitate import _body
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _public, _replace_tools


def _source_access_node(tools):
    """Extract the same access implementation, replacing only ordinary physical reach setup."""
    node = deepcopy(tools["operate_srd_locked_object_node"])
    index = next(
        i
        for i, effect in enumerate(node["effects"])
        if effect.get("call") == "resolve_srd_object_content_geometry"
    )
    reach = node["effects"][index - 3 : index]
    if len(reach) != 3 or any("_world_reach" not in str(effect) for effect in reach):
        raise ValueError("Object access needs its actual three-effect ordinary reach boundary.")
    node["parameters"].extend(
        [
            _parameter("source_range_feet", "integer", minimum=0, maximum=60),
            _parameter("source_requires_sight", "boolean"),
        ]
    )
    node["description"] = "Apply the existing canonical object access through a qualified source."
    geometry = node["effects"][index]
    geometry["params"]["range_feet"] = "{source_range_feet}"
    geometry["params"]["requires_sight"] = "{source_requires_sight}"
    del node["effects"][index - 3 : index]
    return node


def _magic_qualification(tools):
    """Use actual final magic-zone ownership, or reject active unsupported sources."""
    effects = [
        _require(
            "state.has_spell_magic_zones != true",
            "Telekinesis needs the actual magic-zone owner while a magic zone is active.",
        ),
    ]
    if "check_srd_magic_zone_effect" in tools:
        effects = [
            effect
            for target in ("{_caster_state_id}", "{object_id}")
            for effect in (
                _call(
                    "check_srd_magic_zone_effect",
                    {
                        "source_subject": "{_caster_state_id}",
                        "target_subject": target,
                        "effect_kind": "spell",
                        "actual_spell_level": "{slot_level}",
                    },
                    ["_magic_zone_effect_allowed"],
                ),
                _require(
                    "_magic_zone_effect_allowed == true",
                    "The actual magic zone suppresses this Telekinesis object operation.",
                ),
            )
        ]
    return _tool_scene(
        "Qualify the actual Telekinesis ability and object against saved magic-zone bodies.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("slot_level", "integer", minimum=5, maximum=9),
        ],
        effects,
    )


def fine_tools(tools, later):
    """Qualify an actual unheld object and consume the same later Magic Action as target motion."""
    if "operate_srd_locked_object_node" not in tools:
        return {}
    if __package__:
        from .srd_spell_travel import _scene_subject
    else:
        from srd_spell_travel import _scene_subject

    qualify = _tool_scene(
        "Qualify the actual visible Huge-or-smaller unheld object for fine control.",
        [_entity("caster_state"), _parameter("object_id")],
        [
            *_scene_subject("{_caster_state_id}", "_tk_fine_caster"),
            *_body("object_id"),
            _require(
                "_info_scene_entity.metadata.is_object == true and "
                "_info_scene_entity.metadata.plane == _tk_fine_caster.metadata.plane",
                "Fine control needs an actual object on the caster's plane.",
            ),
            _require(
                "_info_scene_entity.metadata.size_rank != null and "
                "_info_scene_entity.metadata.size_rank >= 0 and "
                "_info_scene_entity.metadata.size_rank <= 4",
                "Telekinesis affects an actual Huge-or-smaller object.",
            ),
            _require(
                "(_info_scene_entity.metadata.carried_by == null or "
                "_info_scene_entity.metadata.carried_by == '') and "
                "(_info_scene_entity.metadata.worn_by == null or "
                "_info_scene_entity.metadata.worn_by == '')",
                "Held or worn fine-control objects need their actual holder-save consumer.",
            ),
            _require(
                "_info_scene_entity.metadata.telekinesis_source_id == null or "
                "_info_scene_entity.metadata.telekinesis_source_id == "
                "caster_state.telekinesis_source_id",
                "Reconcile independent Telekinesis grips before fine control.",
            ),
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "range_feet": 60,
                    "requires_sight": True,
                },
                ["_object_scene"],
            ),
            _set("_tk_fine_object", "_object_scene"),
        ],
    )
    magic = _magic_qualification(tools)
    effects = later["effects"]
    validation = next(
        i
        for i, effect in enumerate(effects)
        if effect.get("call") == "validate_srd_telekinesis_cast"
    )
    attempt = next(
        i
        for i, effect in enumerate(effects)
        if effect.get("call") == "resolve_srd_telekinesis_attempt"
    )
    operate = _public(
        _tool_scene(
            "Open or close an actual object with the exact active Telekinesis Magic Action.",
            [
                _entity("caster_state"),
                _parameter("source_id"),
                _parameter("object_id"),
                _parameter("operation", "enum", enum_values=["open", "close"]),
                deepcopy(next(p for p in later["parameters"] if p["name"] == "lasting_state")),
            ],
            [
                *deepcopy(effects[:validation]),
                _call(
                    "qualify_srd_telekinesis_object_access",
                    {"caster_state": "{_caster_state_id}", "object_id": "{object_id}"},
                ),
                _call(
                    "check_srd_telekinesis_object_magic",
                    {
                        "caster_state": "{_caster_state_id}",
                        "object_id": "{object_id}",
                        "slot_level": "{_tk_source.slot_level}",
                    },
                ),
                *deepcopy(effects[validation + 1 : attempt]),
                _when(
                    "_tk_source.subject != null",
                    [_call("release_srd_telekinesis_grip", {"source_id": "{source_id}"})],
                ),
                _call(
                    "operate_srd_object_access_source_node",
                    {
                        "actor_state": "{_caster_state_id}",
                        "object_id": "{object_id}",
                        "operation": "{operation}",
                        "password": "",
                        "source_range_feet": 60,
                        "source_requires_sight": True,
                        "lasting_state": "state",
                    },
                    ["_world_access"],
                ),
                _set("_tk_fine_operation", "operation"),
                _call("prepare_srd_telekinesis_releases", {}),
            ],
        ),
        "exploration",
        "Telekinesis fine control",
        [("Operation", "_tk_fine_operation"), ("Closed", "_world_access.closed")],
    )
    return {
        "operate_srd_object_access_source_node": _source_access_node(tools),
        "qualify_srd_telekinesis_object_access": qualify,
        "check_srd_telekinesis_object_magic": magic,
        "operate_srd_telekinesis_object": operate,
    }


def emit_late_access(text):
    """Re-extract only the access leaf after final canonical ward/lock consumer binding."""
    tools = _load_runtime(text)["tools"]
    name = "operate_srd_object_access_source_node"
    if name not in tools:
        return text
    if "operate_srd_locked_object_node" not in tools:
        raise ValueError("Source-qualified access requires the final canonical object access node.")
    node = _source_access_node(tools)
    changed = {name: node}
    magic_name = "check_srd_telekinesis_object_magic"
    if magic_name in tools:
        changed[magic_name] = _magic_qualification(tools)
    if all(tools[key] == value for key, value in changed.items()):
        return text
    return _replace_tools(text, changed)
