"""Keep resulting advancement Speed separate from the retained species base Speed."""

SPEED_DESCRIPTION = (
    "Resulting walking Speed after the new class features and worn equipment. "
    "The engine derives it from the retained base species Speed; do not change that base."
)


def bind_advancement_speed(runtime, record):
    """Validate the resulting proposal and commit the same derived Speed exactly once."""
    tool = runtime["tools"]["level_up_srd_character"]
    effects = tool["effects"]
    equipment = next(
        e["set"]["value"] for e in effects if e.get("set", {}).get("field") == "_equipment_updates"
    )
    assert equipment.pop("speed") == "_build.speed"
    armor_index = next(
        i
        for i, e in enumerate(effects)
        if e.get("require", {}).get("condition") == "_build.armor_class == _equipment_ac"
    )
    effects.insert(
        armor_index + 1,
        {
            "require": {
                "condition": "_build.speed == _equipment_speed",
                "error": "Proposed walking Speed must match the new class features "
                "and worn equipment. "
                "Keep the character's base species Speed unchanged.",
            }
        },
    )
    choice = next(e["request_choice"] for e in effects if "request_choice" in e)
    choice["prompt"] = choice["prompt"].replace(
        "{choices_summary}",
        "Computed maximum HP: {_proposed_max_hp}; Armor Class: {_equipment_ac}; "
        "Walking Speed: {_equipment_speed}. {choices_summary}",
    )
    choice_index = next(i for i, e in enumerate(effects) if "request_choice" in e)
    effects.insert(
        choice_index,
        {"set": {"field": "_proposed_max_hp", "value": "character_state.max_hp + _hp_gain"}},
    )
    apply = next(e["on_true"] for e in effects if e.get("condition") == "_decision == 'apply'")
    speed_write = [e for e in apply if e.get("set_participant", {}).get("field") == "speed"]
    assert len(speed_write) == 1 and speed_write[0]["set_participant"]["value"] == "_build.speed"
    apply.remove(speed_write[0])
    build = next(p for p in tool["parameters"] if p["name"] == "build")
    build["description"] = "Complete resulting build. " + SPEED_DESCRIPTION
    tool["description"] += (
        " Supply resulting AC and walking Speed; the engine validates both "
        "against new class features and worn equipment while retaining base species Speed."
    )
    summary = tool["metadata"]["result_card"]["summary"]
    summary.append(
        {
            "label": "Walking Speed",
            "value": {"path": "context.character_state.speed"},
            "optional": True,
        }
    )
    declaration = next(t for t in record["tools"] if t["name"] == "level_up_srd_character")
    declaration["input_schema"]["properties"]["build"]["description"] = build["description"]
