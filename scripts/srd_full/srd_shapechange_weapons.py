"""Route resized Shapechange gear through the existing character weapon attack."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _call, _entity, _require, _set, _tool, _when
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_actor_spell_modifiers import _call, _entity, _require, _set, _tool, _when
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


_ORDINARY_FORM = "actor_state.form_statistics_replaced != true and actor_state.form_cloud != true"
_GEAR_FORM = (
    "actor_state.form_cloud != true and (actor_state.form_statistics_replaced != true or "
    "(actor_state.form_current_source_id != null and "
    "lasting_state.transformation_sources != null and "
    "actor_state.form_current_source_id in lasting_state.transformation_sources and "
    "lasting_state.transformation_sources[actor_state.form_current_source_id].spell_id "
    "== 'shapechange' and "
    "lasting_state.transformation_sources[actor_state.form_current_source_id].equipment_policy "
    "== 'resize'))"
)
_QUALIFIER = "qualify_srd_shapechange_weapon"
_OWNERS = ("resolve_combat_attack", "resolve_combat_attack_followup")


def _qualification_tool():
    """Refresh the current body and qualify only its authentic resized held weapon."""
    return _tool(
        "Use Shapechange's exact current source, retained proficiency and resized held gear.",
        [
            {"name": "actor", "type": "string", "resolve": "participant", "required": True},
            _entity("actor_state"),
            {"name": "weapon", "type": "string", "required": True},
            {
                "name": "lasting_state",
                "type": "string",
                "resolve": "entity",
                "required": False,
                "default": "state",
                "model_hidden": True,
            },
        ],
        [
            _require(
                "_actor_id == _actor_state_id", "The weapon user must be this actual character."
            ),
            _when(
                "actor_state.has_transformations == true",
                [
                    _call(
                        "project_srd_transformation",
                        {
                            "target_state": "{_actor_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    )
                ],
            ),
            _set("_shapechange_weapon_allowed", False),
            _set("_shapechange_weapon_source", None),
            _set("_shapechange_weapon_abilities", "actor.data.abilities"),
            _when(
                "actor_state.form_statistics_replaced == true",
                [
                    _require(
                        _GEAR_FORM, "This replaced form cannot use the character's weapon gear."
                    ),
                    _set(
                        "_shapechange_source",
                        "lasting_state.transformation_sources[actor_state.form_current_source_id]",
                    ),
                    {
                        **_call(
                            "check_srd_transformation_source",
                            {
                                "target_state": "{_actor_state_id}",
                                "caster_state": "{_shapechange_source.caster_state_id}",
                                "lasting_state": "{_lasting_state_id}",
                                "source_id": "{actor_state.form_current_source_id}",
                            },
                        ),
                        "export_context": {"_shapechange_source_active": "_form_source_active"},
                    },
                    _require(
                        "_shapechange_source_active == true and "
                        "_shapechange_source.target_state_id == _actor_state_id and "
                        "actor_state.form_retain_proficiencies == true and "
                        "actor_state.form_equipment_melded != true and "
                        "actor_state.form_cloud != true and "
                        "actor_state.form_ability_scores != null",
                        "A current active Shapechange body with resized gear is required.",
                    ),
                    _require(
                        "actor_state.inventory != null and weapon in actor_state.inventory and "
                        "actor_state.held_items != null and weapon in actor_state.held_items",
                        "The resized weapon must actually be carried and held by this character.",
                    ),
                    _set("_shapechange_weapon_allowed", True),
                    _set("_shapechange_weapon_abilities", "actor_state.form_ability_scores"),
                    _set(
                        "_shapechange_weapon_source",
                        {
                            "source_id": "actor_state.form_current_source_id",
                            "serial": "_shapechange_source.serial",
                            "form_id": "_shapechange_source.form_id",
                            "spell_id": "'shapechange'",
                            "page": 161,
                            "source_text_sha256": (
                                "'80598ac362d61daa66a02e1ee63a03f98a2434ec82373b2075dc7f760a6f0463'"
                            ),
                            "equipment_policy": "_shapechange_source.equipment_policy",
                            "weapon": "weapon",
                        },
                    ),
                ],
            ),
            _require("actor_state.form_cloud != true", "Cloud forms cannot use weapon gear."),
        ],
    )


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _bind_weapon(tool):
    """Preserve the attack lifecycle, changing only source eligibility and retained statistics."""
    gates = [
        p for p in tool["pre_conditions"] if p.get("condition") in (_ORDINARY_FORM, _GEAR_FORM)
    ]
    if len(gates) != 1:
        raise ValueError("Shapechange weapon binding needs the existing replaced-form gate.")
    gates[0]["condition"] = _GEAR_FORM
    effects = tool["effects"]
    if any(e.get("call") == _QUALIFIER for e in effects):
        return
    effects.insert(
        0,
        {
            **_call(
                _QUALIFIER,
                {
                    "actor": "{_actor_id}",
                    "actor_state": "{_actor_state_id}",
                    "weapon": "{weapon}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
            "export_context": {
                "_shapechange_weapon_allowed": "_shapechange_weapon_allowed",
                "_shapechange_weapon_abilities": "_shapechange_weapon_abilities",
                "_shapechange_weapon_source": "_shapechange_weapon_source",
            },
        },
    )
    effects.insert(
        1,
        _require(
            "_shapechange_weapon_allowed != true or (use_stunning_strike != true and "
            "use_graze_on_miss != true and attack_sequence != 'light_nick')",
            "Shapechange retains proficiencies, not Stunning Strike or Weapon Mastery features.",
        ),
    )
    ability_count = 0
    for node in _walk(effects):
        if node.get("call") == "spend_srd_attack":
            node["params"]["actor_state"] = "{_actor_state_id}"
        assignment = node.get("set", {})
        if assignment.get("field") == "_ability_modifier" and assignment.get("value") == (
            "(actor.data.abilities[attack_ability] - 10) // 2"
        ):
            assignment["value"] = "(_shapechange_weapon_abilities[attack_ability] - 10) // 2"
            ability_count += 1
        condition = node.get("condition")
        if isinstance(condition, str) and "'heavy' in weapon_info.properties" in condition:
            node["condition"] = condition.replace(
                "actor.data.abilities.", "_shapechange_weapon_abilities."
            )
    if ability_count != 1:
        raise ValueError("Shapechange needs the native weapon ability/damage assignment.")
    for index in range(len(effects) - 1, -1, -1):
        effect = effects[index]
        if effect.get("call") == "derive_srd_martial_arts":
            effects.insert(
                index + 1,
                _when(
                    "_shapechange_weapon_allowed == true", [_set("_martial_arts_available", False)]
                ),
            )
        elif effect.get("call") == "validate_srd_fighting_styles":
            effects.insert(
                index + 1,
                _when("_shapechange_weapon_allowed == true", [_set("_style_selected", [])]),
            )
        elif effect.get("set", {}).get("field") == "_weapon_mastery":
            effects.insert(
                index + 1,
                _when("_shapechange_weapon_allowed == true", [_set("_weapon_mastery", None)]),
            )


def bind_runtime_consumers(text):
    """Bind after body/weapon emission and before final retained attack/reaction capture."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    changed = {_QUALIFIER: _qualification_tool()}
    for name in _OWNERS:
        if name in tools:
            tool = deepcopy(tools[name])
            _bind_weapon(tool)
            changed[name] = tool
    spend = deepcopy(tools["spend_srd_attack"])
    if not any(p["name"] == "actor_state" for p in spend["parameters"]):
        spend["parameters"].append(
            {
                **_entity("actor_state"),
                "required": False,
                "default": None,
                "model_hidden": True,
            }
        )
    for node in _walk(spend["effects"]):
        if node.get("condition") == "actor.data.classes != null":
            node["condition"] = (
                "actor.data.classes != null and (actor_state == null or "
                "actor_state.form_statistics_replaced != true)"
            )
    changed["spend_srd_attack"] = spend
    return _replace_tools(text, changed)
