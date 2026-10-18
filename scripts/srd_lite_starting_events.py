"""Seed ordered starting levels and consume them through the existing SRD level-up tool."""
from copy import deepcopy

PREPARATION = {
    "queue_field": "initial_advancement_queue",
    "intent": "level_up_srd_character",
    "actor_parameter": "character",
    "prompt": "Resolve the queued starting levels before beginning the adventure. Ask for unresolved choices and obtain normal confirmation for each level. Original character cards remain unchanged.",
}

def _set(field, value):
    return {"set": {"field": field, "value": value}}

def bind_starting_events(runtime, record):
    """Add only campaign-local preparation; preserve ordinary awards and level-up choices."""
    context_present = "'state' in campaign_state and 'advancement' in campaign_state.state"
    creation = next(item for item in record["participant_artifacts"] if item["kind"] == "player_character")
    program = creation["initial_state_validation"]
    assert "srd_starting_level_events" not in program["definitions"]
    program["definitions"]["srd_starting_level_events"] = list(range(2, 21))
    program["effects"].insert(0, {
        "set": {"field": "_starting_level_queue", "value": []},
    })
    program["effects"].insert(1, {
        "condition": context_present,
        "on_true": [
            {"require": {
                "condition": "participant.level <= campaign_state.state.advancement.starting_level",
                "error": "This character is above the Plan's starting level. Choose a base-level character; the original card will not be reduced.",
            }},
            _set("_starting_levels", "definitions.srd_starting_level_events"),
            {"for_each": {"as": "_starting_level", "collection": "_starting_levels", "do": [
                {"condition": "participant.level < _starting_level and _starting_level <= campaign_state.state.advancement.starting_level", "on_true": [
                    {"list_push": {"list": "_starting_level_queue", "value": {
                        "from_level": "_starting_level - 1", "target_level": "_starting_level",
                        "intent": "level_up_srd_character",
                    }}},
                ]},
            ]}},
        ],
    })
    program["description"] += " At campaign start, queue each required level in order from the base card to the Plan's starting level; resolve normal choices and confirmation without changing the card."
    assert len(program["description"]) <= 2000
    properties = creation["entity_state_schema"]["properties"]
    for field in ("advancement_target", "initial_advancement_target"):
        assert field not in properties
        properties[field] = {"type": "integer", "minimum": 1, "maximum": 20}
    properties["initial_advancement_queue"] = {
        "type": "array", "minItems": 0, "maxItems": 19, "uniqueItems": True,
        "items": {"type": "object", "additionalProperties": False,
            "required": ["from_level", "target_level", "intent"], "properties": {
                "from_level": {"type": "integer", "minimum": 1, "maximum": 19},
                "target_level": {"type": "integer", "minimum": 2, "maximum": 20},
                "intent": {"type": "string", "enum": ["level_up_srd_character"]},
            }},
    }
    for field, expression in [
        ("advancement_target", "campaign_state.state.advancement.starting_level"),
        ("initial_advancement_target", "campaign_state.state.advancement.starting_level"),
        ("initial_advancement_queue", "rules._starting_level_queue"),
    ]:
        creation["initial_state_calculations"].append({
            "condition": context_present, "set": {"field": field, "value": expression},
        })
    creation.pop("schema_sha256", None)
    record["compatibility"]["requires"] = sorted(set(record["compatibility"]["requires"]) | {"campaign_starting_events.v1"})
    runtime["model_decision_boundary"]["preparation"] = deepcopy(PREPARATION)
    tool = runtime["tools"]["level_up_srd_character"]
    eligibility = [item for item in tool["pre_conditions"] if "policy.method" in item["condition"]]
    assert len(eligibility) == 1
    eligibility[0].update(
        condition="policy != null and ((policy.method == 'milestone' or policy.method == 'xp') or (character_state.initial_advancement_target != null and character_state.initial_advancement_target == policy.starting_level and character_state.initial_advancement_queue != null and len(character_state.initial_advancement_queue) > 0 and character.data.level < character_state.initial_advancement_target))",
        error="This campaign permits only its queued starting levels or actually earned advancement.",
    )
    effects = tool["effects"]
    index = next(i for i, effect in enumerate(effects) if effect.get("set", {}).get("field") == "_new_level")
    effects.insert(index + 1, {
        "condition": "character_state.initial_advancement_queue != null and len(character_state.initial_advancement_queue) > 0",
        "on_true": [{"require": {
            "condition": "character_state.initial_advancement_queue[0].from_level == _prior_character.level and character_state.initial_advancement_queue[0].target_level == _new_level and character_state.initial_advancement_queue[0].intent == 'level_up_srd_character'",
            "error": "Resolve the next queued starting level in order; levels cannot be skipped or repeated.",
        }}],
    })
    apply = next(effect for effect in effects if effect.get("condition") == "_decision == 'apply'")
    apply["on_true"].append({
        "condition": "character_state.initial_advancement_queue != null and len(character_state.initial_advancement_queue) > 0",
        "on_true": [
            _set("_starting_levels_remaining", []),
            _set("_starting_events", "character_state.initial_advancement_queue"),
            {"for_each": {"as": "_starting_event", "collection": "_starting_events", "do": [
                {"condition": "_starting_event.target_level != _new_level", "on_true": [
                    {"list_push": {"list": "_starting_levels_remaining", "value": "_starting_event"}},
                ]},
            ]}},
            _set("{_character_state_id}.initial_advancement_queue", "_starting_levels_remaining"),
        ],
    })
    assert len(apply["on_false"]) == 1 and "log" in apply["on_false"][0]
    apply["on_false"][0]["log"]["message"] = "Level-up cancelled; pending starting levels or earned advancement remain available."
    tool["description"] = tool["description"].replace("Apply one earned SRD level.", "Apply one earned or queued starting SRD level.")
    return creation
