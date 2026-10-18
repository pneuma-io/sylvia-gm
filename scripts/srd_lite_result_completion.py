"""Ruleset-owned presentation of stable recovery and Freedom of Movement escape."""


def bind_result_completion(runtime, record):
    def row(label, path, **kw):
        return {"label": label, "value": {"path": path}, **kw}

    runtime["tools"]["advance_stable_recovery"]["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "inline",
        "title": "Stable recovery",
        "summary": [
            row("Target", "context.target.name", optional=True),
            {
                "label": "Outcome",
                "value": {
                    "path": "context._recovery_complete",
                    "labels": {"true": "Regained 1 HP", "false": "Recovery time advanced"},
                },
                "emphasis": True,
            },
            row("HP", "context._hp_after"),
            row("Hours remaining", "context._stable_recovery_hours_remaining_after"),
        ],
        "details": [
            row("Hours advanced", "parameters.elapsed_hours"),
            row("Conditions", "context._conditions_after"),
        ],
    }
    runtime["tools"]["escape_srd_nonmagical_restraint"]["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "inline",
        "title": "Restraint escaped",
        "summary": [
            row("Actor", "context.actor_state.name", optional=True),
            row("Removed condition", "parameters.restraint"),
            {"label": "Movement spent", "value": 5},
        ],
        "details": [row("Conditions", "context.actor_state.conditions")],
    }
    for name in ("advance_stable_recovery", "escape_srd_nonmagical_restraint"):
        runtime["tools"][name]["metadata"].pop("activity_label", None)

    def inline(title, summary, details=()):
        return {
            "version": 1,
            "footprint": "inline",
            "title": title,
            "summary": list(summary),
            "details": list(details),
        }

    cards = {
        "prepare_srd_creature": inline(
            "Creature prepared",
            [
                row("Creature", "parameters.name"),
                row("Creature ID", "parameters.creature_id"),
                row("Hit Points", "parameters.hit_points"),
            ],
            [row("Armor Class", "parameters.armor_class"), row("Source", "parameters.source")],
        ),
        "command_srd_summoned_creature": inline(
            "Summon command recorded",
            [
                row("Command", "context.summon_state.summon_command"),
                row("Summon ID", "parameters.summon_state"),
            ],
        ),
        "record_srd_creature_relationship": inline(
            "Relationship recorded",
            [
                row("Recorded attitude", "parameters.attitude"),
                row("Ally recorded", "parameters.is_ally"),
            ],
            [
                row("Creature ID", "parameters.actor_state"),
                row("Toward creature ID", "parameters.toward_state"),
            ],
        ),
        "read_srd_creature_attitude": inline(
            "Effective creature attitude",
            [row("Attitude", "context._creature_attitude")],
            [
                row("Creature ID", "parameters.actor_state"),
                row("Toward creature ID", "parameters.toward_state"),
            ],
        ),
        "record_srd_severed_body_part": inline(
            "Missing body part recorded",
            [
                row("Body part", "parameters.part_name"),
                row("Creature ID", "parameters.target_state"),
            ],
            [row("Part ID", "parameters.part_id")],
        ),
        "read_srd_carried_item_size": inline(
            "Carried item size",
            [
                row("Item", "parameters.item"),
                row("Size category adjustment", "context._carried_item_size_adjustment"),
            ],
            [row("Creature ID", "parameters.actor_state")],
        ),
    }
    for name, declaration in cards.items():
        runtime["tools"][name]["metadata"]["result_card"] = declaration
        runtime["tools"][name]["metadata"].pop("activity_label", None)
    from .srd_lite_conditional_result_completion import bind_conditional_result_completion

    bind_conditional_result_completion(runtime, record)
    from .srd_lite_turn_result_completion import bind_turn_result_completion

    bind_turn_result_completion(runtime)
    record["version"] = "0.3.6"
