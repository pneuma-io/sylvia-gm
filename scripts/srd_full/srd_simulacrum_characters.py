"""Bind a Simulacrum's detached Character File facts to its own native actor costs."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _when
    from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _require, _set
    from .srd_timed_senses import _optional, _replace_tools
else:
    from srd_control_behaviors import _when
    from srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _require, _set
    from srd_timed_senses import _optional, _replace_tools

SOURCE_SHA = "46caf9afebae4016c8ee304af967edfa90b4cbcfd5b89c0619e471eaf5b41ef2"
PUBLIC_TOOLS = {"check_srd_simulacrum_character_ability"}
ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
CHARACTER_CHOICE = _optional("target_character", resolve="participant", default=None)


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _substitute(value, substitutions):
    if isinstance(value, str):
        for old, new in substitutions:
            value = value.replace(old, new)
        return value
    if isinstance(value, dict):
        return {key: _substitute(child, substitutions) for key, child in value.items()}
    if isinstance(value, list):
        return [_substitute(child, substitutions) for child in value]
    return value


def _choice(tool):
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "target_character"]
    tool["parameters"].append(deepcopy(CHARACTER_CHOICE))


def _read_original():
    scores = [
        _require(
            f"target_character.data.abilities.{ability} >= 3 and "
            f"target_character.data.abilities.{ability} <= 30",
            "The validated original must retain each actual ability score.",
        )
        for ability in ABILITIES
    ]
    saves = [_set("_sim_pc_saves", {})]
    for ability in ABILITIES:
        saves += [
            _set("_sim_pc_save", f"(target_character.data.abilities.{ability} - 10) // 2"),
            _when(
                f"'{ability}' in target_character.data.saving_throw_proficiencies",
                [_set("_sim_pc_save", "_sim_pc_save + _sim_pc_proficiency")],
            ),
            _when(
                f"original_state.saving_throw_modifiers.{ability} != null",
                [_set("_sim_pc_save", f"original_state.saving_throw_modifiers.{ability}")],
            ),
            _set("_sim_pc_save_row", {ability: "_sim_pc_save"}),
            _set("_sim_pc_saves", "merge(_sim_pc_saves, _sim_pc_save_row)"),
        ]
    return _tool_scene(
        "Read a matching validated Character File and current unequipped original without "
        "creating a participant identity or changing the original's resources.",
        [_entity("original_state"), deepcopy(CHARACTER_CHOICE)],
        [
            _require(
                "target_character != null and target_character.validation_status == 'validated' "
                "and target_character.kind == 'player_character' and "
                "target_character.package_id == 'wotc.srd.5.2.1' and "
                "target_character.participant_id == _original_state_id and "
                "original_state.creature_kind != 'monster'",
                "PC copying requires the actual matching validated Character File.",
            ),
            _require(
                "original_state.form_statistics_replaced != true and "
                "original_state.current_body_source_id == null and "
                "original_state.magic_jar_role == null",
                "A replaced or transferred PC body needs its own copy semantics.",
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_original_state_id}"},
                POSITION_EXPORTS,
            ),
            _set(
                "_sim_eligible",
                "original_state.is_alive == true and "
                "_info_scene_entity.metadata.creature_type == 'humanoid'",
            ),
            _require(
                "_info_scene_entity.metadata.creature_type != null",
                "Record the original's actual creature type before copying it.",
            ),
            _require(
                "target_character.data.level >= 1 and target_character.data.level <= 20 and "
                "len(target_character.data.classes) == 1 and "
                "target_character.data.classes[0].level == target_character.data.level and "
                "target_character.data.saving_throw_proficiencies != null",
                "The current native body constructor needs one genuine class Hit Die pool "
                "and saved saving-throw training; multiclass pools remain explicit.",
            ),
            *scores,
            _set("_sim_pc_data", "target_character.data"),
            _set("_sim_pc_proficiency", "2 + ((target_character.data.level - 1) // 4)"),
            *saves,
            _set(
                "_sim_pc_hit_die",
                "definitions.srd_advancement_hit_dice[target_character.data.classes[0].id]",
            ),
            _require("_sim_pc_hit_die != null", "The PC's original Hit Dice are unrepresented."),
            _set(
                "_sim_pc_fields",
                {
                    "ability_scores": "target_character.data.abilities",
                    "constitution_modifier": (
                        "(target_character.data.abilities.constitution - 10) // 2"
                    ),
                    "proficiency_bonus": "_sim_pc_proficiency",
                    "saving_throw_modifiers": "_sim_pc_saves",
                    "saving_throw_proficiencies": (
                        "target_character.data.saving_throw_proficiencies"
                    ),
                    "skill_proficiencies": "target_character.data.skill_proficiencies",
                    "native_hit_dice": {
                        "count": "target_character.data.level",
                        "size": "_sim_pc_hit_die",
                    },
                    "hit_die_size": "_sim_pc_hit_die",
                    "melee_reach": 5,
                    "initiative_modifier": "(target_character.data.abilities.dexterity - 10) // 2",
                },
            ),
            _set("_sim_original_profile", "merge(original_state, _sim_pc_fields)"),
            _require(
                "original_state.max_hp >= 2 and len(original_state.inventory) == 0 and "
                "original_state.worn_armor == null and original_state.worn_shield == null",
                "The represented PC copy currently needs an unequipped original with "
                "positive half-maximum HP.",
            ),
            _require(
                "original_state.temp_hp == 0 and original_state.exhaustion_level == 0 and "
                "original_state.dodging != true and len(original_state.conditions) == 0 and "
                "(original_state.timed_condition_source_ids == null or "
                "len(original_state.timed_condition_source_ids) == 0) and "
                "original_state.has_transformations != true and "
                "original_state.has_timed_actor_defenses != true and "
                "original_state.has_actor_spell_modifiers != true and "
                "original_state.has_timed_senses != true",
                "Copying external effects, temporary HP or exhaustion needs their source owners.",
            ),
        ],
    )


def _read_copy():
    return _tool_scene(
        "Authenticate the genuine live Construct and its detached Character File data.",
        [_entity("caster_state")],
        [
            _call(
                "read_srd_simulacrum_source",
                {"target_state": "{_caster_state_id}"},
                ["_sim_record", "_sim_active"],
            ),
            _require(
                "_sim_active == true and caster_state.creature_type == 'construct' and "
                "caster_state.simulacrum_character_data != null and "
                "caster_state.simulacrum_character_original == _sim_record.original and "
                f"caster_state.simulacrum_character_source_sha256 == '{SOURCE_SHA}'",
                "Choose the actual live PC-original Simulacrum, not an aliased participant.",
            ),
            _set("_sim_pc_data", "caster_state.simulacrum_character_data"),
        ],
    )


def bind_sources(text):
    """Extend the existing native source constructor; NPC and lifetime owners stay intact."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "read_srd_simulacrum_original_character": _read_original(),
        "read_srd_simulacrum_character_capabilities": _read_copy(),
    }
    qualification = deepcopy(tools["qualify_srd_simulacrum_original"])
    _choice(qualification)
    old = qualification["effects"]
    if old and old[0].get("condition") == "target_character != null":
        old = deepcopy(old[0]["on_false"])
        while old and old[-1].get("set", {}).get("field") in {
            "_sim_original_profile",
            "_sim_pc_data",
        }:
            old.pop()
    qualification["effects"] = [
        _when(
            "target_character != null",
            [
                _call(
                    "read_srd_simulacrum_original_character",
                    {
                        "original_state": "{_original_state_id}",
                        "target_character": "{_target_character_id}",
                    },
                    ["_sim_eligible", "_sim_original_profile", "_sim_pc_data"],
                )
            ],
            deepcopy(old)
            + [
                _set("_sim_pc_data", None),
                _set("_sim_original_profile", "original_state"),
            ],
        )
    ]
    changed["qualify_srd_simulacrum_original"] = qualification

    for name in ("install_srd_simulacrum", "snapshot_srd_simulacrum_body"):
        tool = deepcopy(tools[name])
        _choice(tool)
        # Replace only the native statistical reads, never state writes or actor identities.
        if name == "install_srd_simulacrum":
            for effect in _walk(tool["effects"]):
                if effect.get("call") == "qualify_srd_simulacrum_original":
                    effect["params"]["target_character"] = "{_target_character_id}"
                    effect["export_context"]["_sim_original_profile"] = "_sim_original_profile"
                elif effect.get("call") == "prepare_srd_simulacrum_native_body":
                    effect["params"] = _substitute(
                        effect["params"], [("original_state.", "_sim_original_profile.")]
                    )
                elif effect.get("call") == "snapshot_srd_simulacrum_body":
                    effect["params"]["target_character"] = "{_target_character_id}"
        else:
            # The installer already re-read the current original after the genuine twelve hours.
            prefix = [
                _call(
                    "qualify_srd_simulacrum_original",
                    {
                        "original_state": "{_original_state_id}",
                        "target_character": "{_target_character_id}",
                    },
                    ["_sim_original_profile", "_sim_pc_data"],
                )
            ]
            for effect in _walk(tool["effects"]):
                assignment = effect.get("set", {})
                if isinstance(assignment.get("value"), str) and assignment["value"].startswith(
                    "original_state."
                ):
                    assignment["value"] = assignment["value"].replace(
                        "original_state.", "_sim_original_profile."
                    )
                if isinstance(effect.get("condition"), str) and effect["condition"].startswith(
                    "original_state."
                ):
                    effect["condition"] = effect["condition"].replace(
                        "original_state.", "_sim_original_profile."
                    )
            # These facts stay detached from the participant. Resources use the existing copy.
            footer = _when(
                "target_character != null",
                [
                    _set("duplicate_state.simulacrum_character_data", "_sim_pc_data"),
                    _set("duplicate_state.simulacrum_character_original", "_original_state_id"),
                    _set("duplicate_state.simulacrum_character_source_sha256", f"'{SOURCE_SHA}'"),
                    _set("duplicate_state.spellcasting_profile", None),
                    _set("duplicate_state.spell_definitions", None),
                    _set("duplicate_state.spell_uses_remaining", None),
                ],
            )
            if not any(e.get("condition") == "target_character != null" for e in tool["effects"]):
                tool["effects"] = prefix + tool["effects"] + [footer]
        changed[name] = tool

    for name in ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_cast"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        _choice(tool)
        for effect in _walk(tool["effects"]):
            if effect.get("call") in {"install_srd_simulacrum", "qualify_srd_simulacrum_original"}:
                effect["params"]["target_character"] = "{_target_character_id}"
        changed[name] = tool
    return _replace_tools(text, changed)


def _grant_tool(tools):
    tool = deepcopy(tools["validate_srd_spell_grants"])
    tool["description"] = (
        "Validate the actual Simulacrum's detached grants with the native policies."
    )
    tool["parameters"] = [_entity("caster_state")] + [
        p for p in tool["parameters"] if p["name"] not in {"caster", "proposal"}
    ]
    assert tool["effects"][0]["set"]["field"] == "_grant_profile"
    assert tool["effects"][1]["condition"] == "proposal != null and len(proposal) == 1"
    tool["effects"] = [
        _call(
            "read_srd_simulacrum_character_capabilities",
            {"caster_state": "{_caster_state_id}"},
            ["_sim_pc_data"],
        ),
        _set("_grant_profile", "_sim_pc_data"),
    ] + tool["effects"][2:]
    return tool


def _cast_tool(tool):
    """Keep the same cost/effect kernel and supply PC facts without a participant alias."""
    tool = deepcopy(tool)
    if any(e.get("set", {}).get("field") == "_sim_pc_cast" for e in tool["effects"]):
        return tool
    marker = "caster_state.simulacrum_character_data != null"
    for precondition in tool.get("pre_conditions", []):
        value = precondition.get("condition")
        if not isinstance(value, str):
            continue
        if (
            value.startswith("caster != null or (")
            or value == "caster != null or slot_resource == null"
        ):
            precondition["condition"] = f"({value}) or ({marker})"
        elif value == "caster == null or slot_resource != null":
            precondition["condition"] = (
                f"(caster == null and not ({marker})) or slot_resource != null"
            )
    replacements = [
        ("caster.data", "_casting_character_data"),
        ("caster != null", "_casting_pc_actor == true"),
        ("caster == null", "_casting_pc_actor != true"),
    ]
    tool["effects"] = _substitute(tool["effects"], replacements)
    for effect in list(_walk(tool["effects"])):
        if effect.get("call") == "validate_srd_spell_grants":
            original = deepcopy(effect)
            copied_grant = deepcopy(original)
            copied_grant["call"] = "validate_srd_simulacrum_character_grants"
            copied_grant["params"].pop("caster")
            copied_grant["params"]["caster_state"] = "{_caster_state_id}"
            effect.clear()
            effect.update(
                _when(
                    "_sim_pc_cast == true",
                    [copied_grant],
                    [original],
                )
            )
    prefix = [
        _set("_sim_pc_cast", False),
        _set("_casting_pc_actor", "caster != null"),
        _set("_casting_character_data", None),
        _when("caster != null", [_set("_casting_character_data", "caster.data")]),
        _when(
            f"caster == null and {marker}",
            [
                _call(
                    "read_srd_simulacrum_character_capabilities",
                    {"caster_state": "{_caster_state_id}"},
                    ["_sim_pc_data"],
                ),
                _set("_sim_pc_cast", True),
                _set("_casting_pc_actor", True),
                _set("_casting_character_data", "_sim_pc_data"),
                _require(
                    "caster_state.worn_armor == null and caster_state.worn_shield == null",
                    "PC-copy equipped casting needs the source-owned equipment adapter.",
                ),
                _require(
                    "casting_mode != 'ritual'", "The copy needs an actual spellbook for a Ritual."
                ),
            ],
        ),
    ]
    tool["effects"] = prefix + tool["effects"]
    return tool


def bind_casting_consumers(text):
    """Run before saved/reaction capture and again on the actual final casting roots."""
    tools = _load_runtime(text)["tools"]
    changed = {"validate_srd_simulacrum_character_grants": _grant_tool(tools)}
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
    ):
        # Only actual preparation kernels carry this branch, not thin wrappers.
        if name in tools and any(
            e.get("set", {}).get("field") == "_cast_resource" for e in tools[name]["effects"]
        ):
            changed[name] = _cast_tool(tools[name])
    return _replace_tools(text, changed)


def bind_check_consumer(text):
    """Use the native ability-check/D20 owner with the actual Construct's saved PC facts."""
    tools = _load_runtime(text)["tools"]
    check = deepcopy(tools["ability_check"])
    check["description"] = "Resolve a genuine PC-original Simulacrum's represented ability check."
    check["parameters"] = [p for p in check["parameters"] if p["name"] != "actor"]
    actor_state = next(p for p in check["parameters"] if p["name"] == "actor_state")
    actor_state.pop("model_derive", None)
    actor_state["required"] = True
    check = _substitute(check, [("actor.data", "_sim_pc_data")])
    for effect in _walk(check["effects"]):
        if effect.get("call") == "d20_test":
            effect["params"]["actor"] = None
            effect["params"]["ability_modifier"] = "{_sim_pc_check_ability_modifier}"
            effect["params"]["proficiency_bonus"] = "{_sim_pc_check_proficiency}"
    check["effects"] = [
        _call(
            "read_srd_simulacrum_character_capabilities",
            {"caster_state": "{_actor_state_id}"},
            ["_sim_pc_data"],
        ),
        _set("_sim_pc_check_ability_modifier", "(actor_state.ability_scores[ability] - 10) // 2"),
        _set("_sim_pc_check_proficiency", "actor_state.proficiency_bonus"),
    ] + check["effects"]
    return _replace_tools(text, {"check_srd_simulacrum_character_ability": check})
