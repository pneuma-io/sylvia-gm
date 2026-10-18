"""Forward genuine retained Insect Plague recipient rolls to existing cards."""

from copy import deepcopy

from .srd_control_behaviors import _each, _push
from .srd_timed_actor_defenses import _call, _set, _when

PREFIX = "_lite_insect_"
FIELDS = ("summary_rows", "detail_rows", "applied")
EDGES = (
    ("resolve_srd_environment_sphere_scene", "resolve_srd_environment_sphere_event"),
    ("cast_spell_environment", "resolve_srd_environment_sphere_scene"),
    ("resolve_srd_environment_hazard_event", "resolve_srd_environment_sphere_event"),
    ("resolve_srd_environment_hazard_end_turn", "resolve_srd_environment_sphere_event"),
    (
        "move_srd_scene_creature_celestial_segment",
        "resolve_srd_environment_hazard_event",
    ),
    ("move_srd_scene_creature_node", "move_srd_scene_creature_celestial_segment"),
    ("move_srd_scene_creature", "move_srd_scene_creature_node"),
    ("end_combat_turn", "resolve_srd_environment_hazard_end_turn"),
)


def _lists(rows):
    yield rows
    for row in rows:
        for branch in ("on_true", "on_false"):
            yield from _lists(row.get(branch, []))
        if "for_each" in row:
            yield from _lists(row["for_each"]["do"])


def _initial_fields(prefix=PREFIX):
    return [_set(prefix + field, 0 if field == "applied" else []) for field in FIELDS]


def _receipt_rows():
    """Read recorded rolls and committed vitals; saves and damage are never rerolled."""
    return [
        _when(
            "_sphere_event_source.spell == 'insect_plague'",
            [
                _call(
                    "record_srd_spell_target_result",
                    {
                        "target": "{_target_state_id}",
                        "brief": "Insect Plague: {_effective_damage} "
                        "{_sphere_profile.damage_type}; {_outcome}",
                        "detail": "{_sphere_profile.save_ability} save {_d20_test.results} "
                        "with modifier "
                        "{_lite_insect_save_modifier}, DC {_sphere_event_source.save_dc}: "
                        "{_outcome}. Raw damage {_sphere_damage_roll.results} = "
                        "{_sphere_damage_roll.total}; {_effective_damage} "
                        "{_sphere_profile.damage_type} applied. HP {_lite_insect_hp_before} "
                        "to {target_state.hp}; temporary HP {_lite_insect_temp_before} "
                        "to {target_state.temp_hp}.",
                    },
                    ("_spell_target_summary", "_spell_target_detail"),
                ),
                _push(PREFIX + "summary_rows", "_spell_target_summary"),
                _push(PREFIX + "detail_rows", "_spell_target_detail"),
                _call(
                    "format_srd_information_projection",
                    {
                        "message": "Insect Plague · {_sphere_profile.save_ability} save · "
                        "DC {_sphere_event_source.save_dc} · {_outcome}"
                    },
                    ("_info_projection_message",),
                ),
                _push(
                    PREFIX + "detail_rows",
                    {
                        "label": "_spell_target_detail.label",
                        "value": "_info_projection_message",
                        "equation": {
                            "die_sides": 20,
                            "roll": "_d20_test.total",
                            "rolls": "_d20_test.results",
                            "modifier": "_lite_insect_save_modifier",
                            "total": "_lite_insect_save_total",
                        },
                    },
                ),
                _call(
                    "format_srd_information_projection",
                    {
                        "message": "Insect Plague · raw {_sphere_profile.damage_type} "
                        "damage dice · "
                        "applied {_effective_damage} after save and defenses"
                    },
                    ("_info_projection_message",),
                ),
                _push(
                    PREFIX + "detail_rows",
                    {
                        "label": "_spell_target_detail.label",
                        "value": "_info_projection_message",
                        "equation": {
                            "die_sides": "_sphere_damage_roll.sides",
                            "rolls": "_sphere_damage_roll.results",
                            "roll": "_sphere_damage_roll.total",
                            "modifier": 0,
                            "total": "_sphere_damage_roll.total",
                        },
                    },
                ),
                _set(PREFIX + "applied", "_effective_damage"),
            ],
        )
    ]


def _bind_event(owner):
    receipts, saves, damage = 0, 0, 0
    owner["effects"][:0] = _initial_fields()
    for rows in list(_lists(owner["effects"])):
        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            if row.get("call") == "d20_test":
                exports = row.get("export_context")
                if not isinstance(exports, dict) or exports.get("_d20_test") != "_d20_test":
                    raise ValueError("Keep the native sphere saving throw export")
                exports.update(
                    {
                        "_lite_insect_save_modifier": "_combined_modifier",
                        "_lite_insect_save_total": "_adjusted_total",
                    }
                )
                saves += 1
            if row.get("call") == "apply_srd_qualifying_damage":
                rows[index:index] = [
                    _set("_lite_insect_hp_before", "target_state.hp"),
                    _set("_lite_insect_temp_before", "target_state.temp_hp"),
                ]
                damage += 1
            if row.get("set", {}).get("field") == "target_state.last_environment_sphere_save":
                rows[index + 1 : index + 1] = _receipt_rows()
                receipts += 1
    if (receipts, saves, damage) != (1, 1, 1):
        raise ValueError("Keep the sole native sphere save/damage/receipt sequence")


def _collect(owner, child):
    """Reset each child capsule before a conditional call to prevent stale recipient rows."""
    owner["effects"][:0] = _initial_fields()
    matches = 0
    for rows in list(_lists(owner["effects"])):
        for index in range(len(rows) - 1, -1, -1):
            call = rows[index]
            if call.get("call") != child:
                continue
            exports = call.setdefault("export_context", {})
            if not isinstance(exports, dict):
                raise ValueError("Keep named recipient exports: " + child)
            exports.update({PREFIX + "child_" + field: PREFIX + field for field in FIELDS})
            rows[index:index] = _initial_fields(PREFIX + "child_")
            rows[index + len(FIELDS) + 1 : index + len(FIELDS) + 1] = [
                _set(PREFIX + field, PREFIX + field + " + " + PREFIX + "child_" + field)
                for field in FIELDS
            ]
            matches += 1
    if not matches:
        raise ValueError("Keep the native sphere recipient consumer: " + child)


def bind_insect_feedback(runtime):
    """Compose before the existing featured recorded-equation binder."""
    tools = runtime["tools"]
    names = {name for edge in EDGES for name in edge} | {"finish_srd_retained_cast_tail"}
    changed = {name: deepcopy(tools[name]) for name in names}
    owner = changed["resolve_srd_environment_sphere_event"]
    if any(row.get("set", {}).get("field", "").startswith(PREFIX) for row in owner["effects"]):
        raise ValueError("Insect feedback must be composed exactly once")
    _bind_event(owner)
    for parent, child in EDGES:
        _collect(changed[parent], child)
    changed["cast_spell_environment"]["effects"] += [
        _set("_spell_summary_rows", "_spell_summary_rows + " + PREFIX + "summary_rows"),
        _set("_spell_detail_rows", "_spell_detail_rows + " + PREFIX + "detail_rows"),
    ]
    tail = changed["finish_srd_retained_cast_tail"]
    sites = 0
    for rows in list(_lists(tail["effects"])):
        for index in range(len(rows) - 1, -1, -1):
            call = rows[index]
            if call.get("call") != "cast_spell_environment":
                continue
            exports = call.get("export_context")
            if not isinstance(exports, dict) or "condition" not in call:
                raise ValueError("Keep the actual conditional retained environment dispatch")
            exports[PREFIX + "summary_rows"] = PREFIX + "summary_rows"
            rows[index + 1 : index + 1] = [
                _when(
                    "("
                    + call["condition"]
                    + ") and _retained_cast_tail_choices.spell == 'insect_plague'",
                    [
                        _set(PREFIX + "shown", 0),
                        _each(
                            PREFIX + "summary_rows",
                            PREFIX + "row",
                            [
                                _when(
                                    PREFIX + "shown < 3",
                                    [
                                        _push("_spell_card_summary", PREFIX + "row"),
                                        _set(PREFIX + "shown", PREFIX + "shown + 1"),
                                    ],
                                )
                            ],
                        ),
                    ],
                )
            ]
            sites += 1
    if sites != 1:
        raise ValueError("Keep the sole retained environment completion card")
    move = changed["move_srd_scene_creature"]
    card = move["metadata"]["result_card"]
    if card.get("details") != {"path": "context._guardian_movement_card_rows"}:
        raise ValueError("Preserve the shared movement recipient details")
    movement_sites = [
        (rows, index)
        for rows in _lists(move["effects"])
        for index, row in enumerate(rows)
        if row.get("set", {}).get("field") == "_guardian_movement_card_rows"
    ]
    if len(movement_sites) != 1:
        raise ValueError("Keep the sole genuine Guardian movement row projection")
    rows, index = movement_sites[0]
    if rows[index] != _set(
        "_guardian_movement_card_rows", "_lite_daggers_detail_rows + _guardian_movement_rows"
    ):
        raise ValueError("Keep the genuine shared Guardian and Daggers movement rows")
    card["summary"].append(
        {
            "label": "Insect Plague damage events",
            "omit_zero": True,
            "value": {"path": "context." + PREFIX + "event_count"},
        }
    )
    # The existing Guardian card reads its combined rows, so join Insect receipts
    # before that genuine projection without replacing its card path or summary.
    rows[index:index] = [
        _set(PREFIX + "event_count", "len(" + PREFIX + "summary_rows)"),
        _set(
            "_lite_daggers_detail_rows",
            "_lite_daggers_detail_rows + " + PREFIX + "detail_rows",
        ),
    ]
    # This binder precedes the Steed turn wrapper. Append the actual end-turn
    # receipts before its native card/equation projection, so both rider and
    # controlled Steed turns carry the same committed recipient feedback.
    end = changed["end_combat_turn"]
    end_sites = 0
    for rows in list(_lists(end["effects"])):
        for index in range(len(rows) - 1, -1, -1):
            if rows[index].get("call") == "resolve_srd_environment_hazard_end_turn":
                collected = [
                    _set(
                        PREFIX + field,
                        PREFIX + field + " + " + PREFIX + "child_" + field,
                    )
                    for field in FIELDS
                ]
                at = index + 1 + len(collected)
                if rows[index + 1 : at] != collected:
                    raise ValueError(
                        "Collect the native end-turn receipt before projecting its rows"
                    )
                rows[at:at] = [
                    _when("_end_turn_rows == null", [_set("_end_turn_rows", [])]),
                    _set("_end_turn_rows", "_end_turn_rows + " + PREFIX + "detail_rows"),
                ]
                end_sites += 1
    if end_sites != 1:
        raise ValueError("Keep the sole actual end-turn environment consumer")
    tools.update(changed)
    return sorted(changed)
