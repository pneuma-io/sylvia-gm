"""Retain explicitly prepared NPC training through actual transformed statistics."""

from copy import deepcopy

if __package__:
    from . import srd_actor_spell_modifiers as actor
else:
    import srd_actor_spell_modifiers as actor

_set, _when, _require = actor._set, actor._when, actor._require
SKILL_ABILITIES = {
    "athletics": "strength",
    "acrobatics": "dexterity",
    "sleight_of_hand": "dexterity",
    "stealth": "dexterity",
    "arcana": "intelligence",
    "history": "intelligence",
    "investigation": "intelligence",
    "nature": "intelligence",
    "religion": "intelligence",
    "animal_handling": "wisdom",
    "insight": "wisdom",
    "medicine": "wisdom",
    "perception": "wisdom",
    "survival": "wisdom",
    "deception": "charisma",
    "intimidation": "charisma",
    "performance": "charisma",
    "persuasion": "charisma",
}
ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")


def capture():
    """Keep original source facts once; an omitted list remains unknown."""
    return [
        _set("target_state.form_base_npc_proficiencies", None),
        _when(
            "target_character == null and target_state.proficiency_bonus != null and "
            "target_state.skill_proficiencies != null and "
            "target_state.saving_throw_proficiencies != null",
            [
                _set(
                    "target_state.form_base_npc_proficiencies",
                    {
                        "proficiency_bonus": "target_state.proficiency_bonus",
                        "skills": "target_state.skill_proficiencies",
                        "saves": "target_state.saving_throw_proficiencies",
                    },
                )
            ],
        ),
    ]


def projected_training():
    return [
        _set("_form_retained_training", None),
        _when(
            "target_state.form_base_pc_proficiencies != null",
            [
                _set("_form_retained_training", "target_state.form_base_pc_proficiencies"),
            ],
        ),
        _when(
            "_form_retained_training == null and target_state.form_base_npc_proficiencies != null",
            [
                _set("_form_retained_training", "target_state.form_base_npc_proficiencies"),
            ],
        ),
    ]


def projected_skills():
    """Compute saved NPC check totals from real body abilities and printed/retained training."""
    effects = [_set("_form_skill_modifiers", {})]
    for skill, ability in SKILL_ABILITIES.items():
        effects.extend(
            [
                _set(
                    "_form_skill_modifier",
                    f"(target_state.form_ability_scores.{ability} - 10) // 2",
                ),
                _when(
                    f"'{skill}' in _projected_beast.skills",
                    [
                        _set(
                            "_form_skill_modifier",
                            f"_form_skill_modifier + _projected_beast.skills.{skill} - "
                            f"((_projected_beast.ability_scores.{ability} - 10) // 2)",
                        ),
                    ],
                ),
                _when(
                    "_winning_form.spell_id == 'shapechange' and "
                    "_form_retained_training != null and "
                    f"'{skill}' in _form_retained_training.skills",
                    [
                        _set(
                            "_form_skill_modifier",
                            "max(_form_skill_modifier, "
                            f"((target_state.form_ability_scores.{ability} - 10) // 2) + "
                            "_form_retained_training.proficiency_bonus)",
                        ),
                    ],
                ),
                _set("_form_skill_patch", {skill: "_form_skill_modifier"}),
                _set("_form_skill_modifiers", "merge(_form_skill_modifiers, _form_skill_patch)"),
            ]
        )
    effects.append(_set("target_state.skill_check_modifiers", "_form_skill_modifiers"))
    return effects


def qualification():
    """Unknown or enhanced NPC training cannot silently become ordinary proficiency."""
    known = [
        _require(
            "target_state.proficiency_bonus != null and "
            "target_state.skill_proficiencies != null and "
            "target_state.saving_throw_proficiencies != null",
            "NPC Shapechange needs actual source Proficiency Bonus and "
            "explicit skill/save proficiency lists.",
        ),
        _require(
            "target_state.ability_scores != null",
            "Retained NPC training needs actual source ability scores.",
        ),
        _set("_form_source_abilities", "target_state.ability_scores"),
        _set("_form_source_skills", "target_state.skill_check_modifiers"),
        _set("_form_source_saves", "target_state.saving_throw_modifiers"),
        _when(
            "target_state.form_base_statistics != null",
            [
                _set("_form_source_abilities", "target_state.form_base_abilities"),
                _set(
                    "_form_source_skills", "target_state.form_base_statistics.skill_check_modifiers"
                ),
                _set(
                    "_form_source_saves", "target_state.form_base_statistics.saving_throw_modifiers"
                ),
            ],
        ),
    ]
    for skill, ability in SKILL_ABILITIES.items():
        known.append(
            _when(
                f"'{skill}' in target_state.skill_proficiencies and "
                f"_form_source_skills.{skill} != null",
                [
                    _require(
                        f"_form_source_skills.{skill} == "
                        f"((_form_source_abilities.{ability} - 10) // 2) + "
                        "target_state.proficiency_bonus",
                        "Enhanced NPC skill proficiency needs an owning source-qualified consumer.",
                    )
                ],
            )
        )
    for ability in ABILITIES:
        known.append(
            _when(
                f"'{ability}' in target_state.saving_throw_proficiencies and "
                f"_form_source_saves.{ability} != null",
                [
                    _require(
                        f"_form_source_saves.{ability} == "
                        f"((_form_source_abilities.{ability} - 10) // 2) + "
                        "target_state.proficiency_bonus",
                        "Enhanced NPC save proficiency needs an owning source-qualified consumer.",
                    )
                ],
            )
        )
    return _when("spell == 'shapechange' and target_character == null", known)


def compose_preparation(tools, changed):
    """Extend the existing preparation owner without inferring source training or PB."""
    tool = deepcopy(changed.get("prepare_srd_creature", tools["prepare_srd_creature"]))
    for name, names in (
        ("skill_proficiencies", tuple(SKILL_ABILITIES)),
        ("saving_throw_proficiencies", ABILITIES),
    ):
        if not any(p["name"] == name for p in tool["parameters"]):
            tool["parameters"].append(
                {
                    "name": name,
                    "type": "array",
                    "required": False,
                    "default": None,
                    "items": {"type": "string", "enum": list(names)},
                    "unique_items": True,
                    "max_items": len(names),
                    "description": "Copy the explicit complete proficiency list from the source. "
                    "Omission leaves training unknown; an empty list explicitly records none.",
                }
            )
        creation = next(
            e["create_entity"]["value"] for e in tool["effects"] if "create_entity" in e
        )
        creation[name] = None
        retention = _when(name + " != null", [_set("creature_id." + name, name)])
        if retention not in tool["effects"]:
            tool["effects"].append(retention)
    requirement = _require(
        "(skill_proficiencies == null and saving_throw_proficiencies == null) or "
        "proficiency_bonus != null",
        "Explicit NPC proficiency facts require the source's actual Proficiency Bonus.",
    )
    if requirement not in tool["effects"]:
        tool["effects"].insert(0, requirement)
    changed["prepare_srd_creature"] = tool
