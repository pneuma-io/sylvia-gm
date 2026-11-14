"""Resolve occupied-body equipment at the final incoming Armor Class boundary."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _call, _param, _require, _set, _when
    from .srd_magic_jar_consumers import _forward_params, _walk
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import _call, _param, _require, _set, _when
    from srd_magic_jar_consumers import _forward_params, _walk
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


ARMOR_OWNER = "resolve_srd_magic_jar_incoming_armor_class"
DEFENSE_OWNER = "resolve_srd_magic_jar_armor_defenses"
VALIDATION_OWNER = "validate_srd_magic_jar_armor_defense"
ARMOR_GUARD = (
    "target_state.magic_jar_role == 'possessed' or target_state.magic_jar_role == 'host_soul'"
)


def _body_equipment(value):
    """Only actual armor/shield access changes subject; spell-recipient identity stays intact."""
    if isinstance(value, str):
        return value.replace("target_state.worn_armor", "jar_armor_host.worn_armor").replace(
            "target_state.worn_shield", "jar_armor_host.worn_shield"
        )
    if isinstance(value, list):
        return [_body_equipment(item) for item in value]
    if isinstance(value, dict):
        return {key: _body_equipment(item) for key, item in value.items()}
    return value


def _body_parameters(tool):
    tool["parameters"].extend(
        _param(name, resolve="entity", model_hidden=True)
        for name in ("jar_armor_caster", "jar_armor_host")
    )


def _body_arguments():
    return {
        "jar_armor_caster": "{_jar_armor_caster_id}",
        "jar_armor_host": "{_jar_armor_host_id}",
    }


def bind_incoming_defense_consumers(text):
    """Compose final AC/source owners after Jar equipment, before attack/reaction extraction."""
    tools = _load_runtime(text)["tools"]
    names = (
        "resolve_srd_effective_armor_class",
        "resolve_srd_timed_actor_defenses",
        "validate_srd_actor_defense_target",
        "resolve_srd_magic_jar_body_equipment",
        "resolve_srd_magic_jar_target",
        "read_srd_magic_jar_source",
    )
    for name in names:
        if name not in tools:
            raise ValueError(f"Magic Jar incoming armor requires {name}")
    original = deepcopy(tools[names[0]])
    original["effects"] = [
        effect
        for node in original["effects"]
        for effect in (node["on_false"] if node.get("condition") == ARMOR_GUARD else [node])
    ]
    armor = deepcopy(original)
    _body_parameters(armor)
    defenses = deepcopy(tools[names[1]])
    _body_parameters(defenses)
    defenses["effects"] = _body_equipment(defenses["effects"])
    validation = deepcopy(tools[names[2]])
    _body_parameters(validation)
    validation["effects"] = _body_equipment(validation["effects"])
    validation["pre_conditions"] = _body_equipment(validation.get("pre_conditions", []))

    def bind_defense(node):
        if node.get("call") == "resolve_srd_timed_actor_defenses":
            node["call"] = DEFENSE_OWNER
            node["params"].update(_body_arguments())

    def bind_validation(node):
        if node.get("call") == "validate_srd_actor_defense_target":
            node["call"] = VALIDATION_OWNER
            node["params"].update(_body_arguments())

    _walk(armor["effects"], bind_defense)
    _walk(defenses["effects"], bind_validation)

    def physical_validation(node):
        if node.get("condition") == "_defense_target_profile.base_ac > 0":
            body = node["on_true"]
            at = next(
                i
                for i, n in enumerate(body)
                if n.get("set", {}).get("field") == "_defense_target_ac"
            )
            body[at:at] = [
                _set("_defense_target_dexterity", "jar_armor_caster.ability_scores.dexterity"),
                _call(
                    "resolve_srd_equipment",
                    {
                        "actor": "{_jar_armor_caster_id}",
                        "actor_state": "{_jar_armor_caster_id}",
                    },
                    ("_equipment_training",),
                ),
                _set("_defense_target_shield_trained", "'shield' in _equipment_training"),
            ]

    _walk(validation["effects"], physical_validation)
    wrapper = deepcopy(original)
    wrapper["effects"] = [
        _when(
            ARMOR_GUARD,
            [
                _call(
                    "resolve_srd_magic_jar_target",
                    {"target_state": "{_target_state_id}"},
                    ("_magic_jar_target_id",),
                ),
                _call(
                    "read_srd_magic_jar_source",
                    {"caster_state": "{_magic_jar_target_id}"},
                    ("_jar_source",),
                ),
                _require(
                    "_jar_source.mode == 'possessed'",
                    "Incoming armor requires the exact occupied Magic Jar body.",
                ),
                _call(
                    "resolve_srd_equipment",
                    {"actor": "{_magic_jar_target_id}", "actor_state": "{_magic_jar_target_id}"},
                    ("_equipment_ac",),
                ),
                _call(
                    ARMOR_OWNER,
                    _forward_params(original["parameters"])
                    | {
                        "base_armor_class": "{_equipment_ac}",
                        "jar_armor_caster": "{_magic_jar_target_id}",
                        "jar_armor_host": "{_jar_source.host}",
                    },
                    ("_effective_armor_class",),
                ),
            ],
            original["effects"],
        )
    ]
    return _replace_tools(
        text,
        {
            names[0]: wrapper,
            ARMOR_OWNER: armor,
            DEFENSE_OWNER: defenses,
            VALIDATION_OWNER: validation,
        },
    )
