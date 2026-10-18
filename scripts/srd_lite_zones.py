"""legacy zone profiles composed only into the isolated Lite variant."""

ZONE_PROFILES = {
    "silence": {"range_feet": 60, "radius_feet": 20, "duration_rounds": 100},
}


def bind_zones(runtime):
    """Reuse source-owned environment, distance and lifecycle consumers with legacy data."""
    for spell, changes in ZONE_PROFILES.items():
        runtime["definitions"]["spells"][spell].update(changes)
        header = runtime["definitions"]["spell_casting"][spell]
        header.update(
            components=["verbal", "somatic"], material="", material_parts=[],
            material_has_cost=False, material_consumed=False, focus_substitution=False,
            range="60 feet", duration="Concentration, up to 100 turns",
        )


def _value_schema(value):
    """Describe primitive and nested profile facts with the native schema grammar."""
    if isinstance(value, dict):
        properties = {key: _value_schema(child) for key, child in value.items()}
        return {"type":"object", "additionalProperties":False,
                "required":list(properties), "properties":properties}
    if isinstance(value, list):
        item_schema = _value_schema(value[0]) if value else {"type":"string"}
        # The complete ordered list remains exact. A homogeneous primitive list
        # may contain distinct original values; each item must admit those same
        # values rather than being incorrectly restricted to its first entry.
        if value and all(
            not isinstance(item, (dict, list))
            and _value_schema(item)["type"] == item_schema["type"]
            for item in value
        ):
            unique = []
            for item in value:
                if item not in unique:
                    unique.append(item)
            item_schema = {"type":item_schema["type"], "enum":unique}
        return {"type":"array", "enum":[value], "items":item_schema}
    kind = "null" if value is None else "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "number" if isinstance(value, float) else "string"
    return {"type":kind, "enum":[value]}


def profile_schema(runtime):
    """Use the supported discriminator grammar to retain exact per-spell contracts."""
    variants = {}
    for identity, row in runtime["definitions"]["spells"].items():
        row["lite_profile"] = identity
        properties = {}
        for key, value in row.items():
            if key == "lite_profile":
                continue
            properties[key] = _value_schema(value)
        variants[identity] = {"type":"object", "additionalProperties":False,
                              "required":list(properties), "properties":properties}
    runtime["definition_row_schemas"]["definitions.spells"] = {
        "discriminator":"lite_profile", "variants":variants}
