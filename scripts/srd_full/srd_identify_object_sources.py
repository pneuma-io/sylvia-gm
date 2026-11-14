"""Identify reads object-owned light and weapon sources instead of their bookkeeping holders."""

if __package__:
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _set, _tool, _when
    from .srd_timed_senses import _loop, _push
else:
    from srd_timed_actor_defenses import _call, _entity, _parameter, _set, _tool, _when
    from srd_timed_senses import _loop, _push


def _weapon_tool():
    """Require a unique inventory weapon and its retained source."""
    return _tool(
        "Read exact non-concentration weapon enchantments for a uniquely owned inventory object.",
        [_entity("actor_state"), _parameter("weapon")],
        [
            _call(
                "validate_srd_inventory_spell_weapon",
                {"actor_state": "{_actor_state_id}", "weapon": "{weapon}"},
            ),
            _set("_identify_weapon_spells", []),
            _set("_identify_weapon_records", []),
            _when(
                "state.turn_effects != null",
                [_set("_identify_weapon_records", "state.turn_effects")],
            ),
            _loop(
                "_identify_weapon_records",
                "_identify_weapon_record",
                [
                    _when(
                        "_identify_weapon_record.kind == 'actor_spell_modifier' and "
                        "_identify_weapon_record.target == _actor_state_id and "
                        "_identify_weapon_record.weapon == weapon and "
                        "_identify_weapon_record.concentration != true and "
                        "state.lasting_tick + 0.000000001 < "
                        "_identify_weapon_record.expires_tick and "
                        "(_identify_weapon_record.spell == 'magic_weapon' or "
                        "_identify_weapon_record.spell == 'shillelagh')",
                        [
                            _set(
                                "_identify_weapon_active",
                                "_identify_weapon_record.spell == 'magic_weapon'",
                            ),
                            _when(
                                "_identify_weapon_record.spell == 'shillelagh' and "
                                "actor_state.held_items != null and "
                                "weapon in actor_state.held_items",
                                [_set("_identify_weapon_active", True)],
                            ),
                            _when(
                                "_identify_weapon_active == true and "
                                "_identify_weapon_record.spell not in _identify_weapon_spells",
                                [_push("_identify_weapon_spells", "_identify_weapon_record.spell")],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _subject_tool():
    """Match an illumination source to its physical subject, including creature scene aliases."""
    return _tool(
        "Read exact object-anchored source effects; empty point anchors never identify an object.",
        [_parameter("subject", "object", from_state="state.spatial_scene")],
        [
            _set("_identify_object_spells", []),
            _set("_identify_object_records", []),
            _when(
                "state.turn_effects != null",
                [_set("_identify_object_records", "state.turn_effects")],
            ),
            _loop(
                "_identify_object_records",
                "_identify_object_record",
                [
                    _when(
                        "_identify_object_record.kind == 'illumination_spell' and "
                        "_identify_object_record.source != '' and "
                        "(_identify_object_record.source == subject.id or "
                        "(subject.metadata.participant_ref != null and "
                        "_identify_object_record.source == subject.metadata.participant_ref))",
                        [
                            _call(
                                "check_srd_illumination_record",
                                {
                                    "record": "{_identify_object_record}",
                                    "caster_state": "{_identify_object_record.caster}",
                                },
                                ["_illumination_active"],
                            ),
                            _when(
                                "_illumination_active == true and "
                                "_identify_object_record.spell not in _identify_object_spells",
                                [_push("_identify_object_spells", "_identify_object_record.spell")],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "subject.metadata.is_object == true and "
                "subject.metadata.inventory_owner_ref != null and "
                "subject.metadata.inventory_weapon_id != null",
                [
                    _call(
                        "identify_srd_inventory_object_spells",
                        {
                            "actor_state": "{subject.metadata.inventory_owner_ref}",
                            "weapon": "{subject.metadata.inventory_weapon_id}",
                        },
                        ["_identify_weapon_spells"],
                    ),
                    _loop(
                        "_identify_weapon_spells",
                        "_identify_weapon_spell",
                        [
                            _when(
                                "_identify_weapon_spell not in _identify_object_spells",
                                [_push("_identify_object_spells", "_identify_weapon_spell")],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _transformation_tool():
    """Read the actual subject's transformation index through its owning lifetime check."""
    return _tool(
        "Identify exact active transformation spells on the touched creature.",
        [_entity("target_state")],
        [
            _set("_identify_form_spells", []),
            _when(
                "target_state.has_transformations == true and "
                "target_state.transformation_source_ids != null",
                [
                    _set("_identify_form_ids", "target_state.transformation_source_ids"),
                    _loop(
                        "_identify_form_ids",
                        "_identify_form_id",
                        [
                            _set(
                                "_identify_form_source",
                                "state.transformation_sources[_identify_form_id]",
                            ),
                            _when(
                                "_identify_form_source.installed == true",
                                [
                                    _call(
                                        "check_srd_transformation_source",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "caster_state": (
                                                "{_identify_form_source.caster_state_id}"
                                            ),
                                            "source_id": "{_identify_form_id}",
                                            "lasting_state": "state",
                                        },
                                        ["_form_source_active"],
                                    ),
                                    _when(
                                        "_form_source_active == true and "
                                        "_identify_form_source.spell_id not in "
                                        "_identify_form_spells",
                                        [
                                            _push(
                                                "_identify_form_spells",
                                                "_identify_form_source.spell_id",
                                            )
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def deepen_identify_object_sources(changed):
    """Qualify real subjects before saving Identify information."""
    changed["identify_srd_inventory_object_spells"] = _weapon_tool()
    changed["identify_srd_anchored_object_spells"] = _subject_tool()
    changed["identify_srd_creature_transformation_spells"] = _transformation_tool()
    effects = changed["cast_srd_identify"]["effects"]

    def visit(items):
        for effect in items:
            condition = effect.get("condition", "")
            if "_identify_source.spell != null" in condition:
                effect["condition"] = (
                    "_identify_source.kind != 'illumination_spell' and "
                    "not (_identify_source.kind == 'actor_spell_modifier' and "
                    "_identify_source.weapon != null and _identify_source.weapon != '') and ("
                    + condition
                    + ")"
                )
            for key in ("on_true", "on_false"):
                if key in effect:
                    visit(effect[key])
            if "for_each" in effect:
                visit(effect["for_each"]["do"])
        for effect in items:
            if effect.get("condition") == "_identify_protected != true":
                effect["on_true"] += [
                    _call(
                        "identify_srd_anchored_object_spells",
                        {"subject": "{_identify_subject}"},
                        ["_identify_object_spells"],
                    ),
                    _loop(
                        "_identify_object_spells",
                        "_identify_object_spell",
                        [
                            _when(
                                "_identify_object_spell not in _identify_information",
                                [_push("_identify_information", "_identify_object_spell")],
                            ),
                        ],
                    ),
                    _when(
                        "_identify_subject.metadata.participant_ref != null",
                        [
                            _call(
                                "identify_srd_creature_transformation_spells",
                                {"target_state": "{_identify_subject.metadata.participant_ref}"},
                                ["_identify_form_spells"],
                            ),
                            _loop(
                                "_identify_form_spells",
                                "_identify_form_spell",
                                [
                                    _when(
                                        "_identify_form_spell not in _identify_information",
                                        [_push("_identify_information", "_identify_form_spell")],
                                    )
                                ],
                            ),
                        ],
                    ),
                ]
                break

    visit(effects)
