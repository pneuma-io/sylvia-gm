"""SRD Fighting Style choices retain the class feature or feat slot that grants them."""

STYLES = ("archery", "defense", "great_weapon_fighting", "two_weapon_fighting")
CLASSES = ("barbarian", "bard", "cleric", "druid", "fighter", "monk", "paladin",
           "ranger", "rogue", "sorcerer", "warlock", "wizard")


def grant_policies():
    """Class features and individual feat slots each grant at most one style choice."""
    policies = {name: {"class_id": name, "level": level, "subclass": "", "feat": False}
                for name, level in (("fighter", 1), ("paladin", 2), ("ranger", 2))}
    policies["champion"] = {"class_id": "fighter", "level": 7,
                            "subclass": "champion", "feat": False}
    for class_id in CLASSES:
        levels = [4, 8, 12, 16, 19]
        levels += [6, 14] if class_id == "fighter" else [10] if class_id == "rogue" else []
        for level in levels:
            policies[f"{class_id}_feat_{level}"] = {
                "class_id": class_id, "level": level, "subclass": "", "feat": True,
            }
    return policies


def choice_schema():
    """Keep grant provenance with the selected feat rather than a free list of bonuses."""
    return {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "source": {"type": "string", "enum": list(grant_policies()),
                       "description": "Class feature or exact class-level feat slot spent."},
            "style": {"type": "string", "enum": list(STYLES)},
        }, "required": ["source", "style"],
    }}


def validation_effects(profile, *, prefix="_style"):
    """Validate declared grants and derive the names consumed by combat and equipment."""
    def set_value(field, value):
        return {"set": {"field": field, "value": value}}

    def require(condition, error):
        return {"require": {"condition": condition, "error": error}}

    effects = [
        set_value("_style_records", []),
        set_value("_style_sources", []), set_value("_style_selected", []),
        set_value("_style_feature", False),
        set_value("_style_classes", f"{profile}.classes"),
        {"for_each": {"collection": "_style_classes", "as": "_style_class", "do": [
            {"condition": "_style_class.id == 'fighter' or "
             "((_style_class.id == 'paladin' or _style_class.id == 'ranger') "
             "and _style_class.level >= 2)", "on_true": [set_value("_style_feature", True)]},
        ]}},
        {"condition": f"{profile}.fighting_styles != null", "on_true": [
            set_value("_style_records", f"{profile}.fighting_styles"),
        ]},
        {"for_each": {"collection": "_style_records", "as": "_style_choice", "do": [
            require("_style_feature == true",
                    "A Fighting Style feat requires the Fighting Style class feature."),
            require("_style_choice.style in definitions.srd_fighting_styles",
                    "Select a Fighting Style from the SRD 5.2.1 feats."),
            require("_style_choice.source in definitions.srd_fighting_style_grants",
                    "A Fighting Style needs its actual class feature or feat slot."),
            require("_style_choice.source not in _style_sources "
                    "and _style_choice.style not in _style_selected",
                    "Each Fighting Style and its granting feature may be selected only once."),
            set_value("_style_policy",
                      "definitions.srd_fighting_style_grants[_style_choice.source]"),
            set_value("_style_class_level", 0),
            {"for_each": {"collection": "_style_classes", "as": "_style_owner", "do": [
                {"condition": "_style_owner.id == _style_policy.class_id", "on_true": [
                    set_value("_style_class_level", "_style_owner.level"),
                ]},
            ]}},
            require("_style_class_level >= _style_policy.level",
                    "The Fighting Style grant has not been earned at this class level."),
            require(f"_style_policy.subclass == '' or ({profile}.subclasses != null "
                    f"and {profile}.subclasses[_style_policy.class_id] == _style_policy.subclass)",
                    "The additional Fighting Style requires the Champion subclass."),
            {"list_push": {"list": "_style_sources", "value": "_style_choice.source"}},
            {"list_push": {"list": "_style_selected", "value": "_style_choice.style"}},
        ]}},
    ]

    effects += [
        set_value("_style_spell_grants", []),
        {"condition": f"{profile}.spellcasting_grants != null", "on_true": [
            set_value("_style_spell_grants", f"{profile}.spellcasting_grants"),
        ]},
        {"for_each": {"collection": "_style_spell_grants", "as": "_style_spell_grant", "do": [
            require("_style_spell_grant.source != 'blessed_warrior' "
                    "or 'paladin' not in _style_sources",
                    "Choose either Paladin's Fighting Style or Blessed Warrior, not both."),
            require("_style_spell_grant.source != 'druidic_warrior' "
                    "or 'ranger' not in _style_sources",
                    "Choose either Ranger's Fighting Style or Druidic Warrior, not both."),
        ]}},
    ]

    # Only local variable names use the prefix; definition and profile field names do not.
    if prefix == "_style":
        return effects
    import re

    def locals_only(value):
        if isinstance(value, dict):
            return {key: locals_only(item) for key, item in value.items()}
        if isinstance(value, list):
            return [locals_only(item) for item in value]
        if isinstance(value, str):
            return re.sub(r"(?<![a-zA-Z0-9])_style", prefix, value)
        return value

    return locals_only(effects)


def creation_effects():
    """A newly admitted character must select every earned class Fighting Style feature."""
    return [
        {"set": {"field": "_style_alternatives", "value": []}},
        {"for_each": {"collection": "_style_spell_grants", "as": "_style_alternative", "do": [
            {"list_push": {"list": "_style_alternatives", "value": "_style_alternative.source"}},
        ]}},
        {"for_each": {"collection": "_style_classes", "as": "_style_required_class", "do": [
            {"require": {
                "condition": "_style_required_class.id != 'fighter' or 'fighter' in _style_sources",
                "error": "Select the Fighting Style granted by the first Fighter level."}},
            {"require": {
                "condition": "_style_required_class.id != 'fighter' "
                "or _style_required_class.level < 7 or participant.subclasses == null "
                "or participant.subclasses.fighter != 'champion' or 'champion' in _style_sources",
                "error": "Select Champion's additional Fighting Style at Fighter level 7."}},
            *[{"require": {
                "condition": f"_style_required_class.id != '{class_id}' "
                f"or _style_required_class.level < 2 or '{class_id}' in _style_sources "
                f"or '{alternative}' in _style_alternatives",
                "error": f"Select {class_id.title()}'s Fighting Style or its cantrip alternative "
                "at class level 2.",
            }} for class_id, alternative in (("paladin", "blessed_warrior"),
                                             ("ranger", "druidic_warrior"))],
        ]}},
    ]


def level_up_effects():
    """Preserve old grants, permit Fighter's own replacement, and charge new feat slots."""
    return [*validation_effects("_prior_character", prefix="_old_style"),
        {"for_each": {"collection": "_old_style_records", "as": "_old_style_choice", "do": [
            {"require": {"condition": "_old_style_choice.source in _style_sources",
                         "error": "Preserve each earned Fighting Style grant."}},
            {"set": {"field": "_replacement_style", "value": None}},
            {"for_each": {"collection": "_style_records", "as": "_candidate_style", "do": [
                {"condition": "_candidate_style.source == _old_style_choice.source", "on_true": [
                    {"set": {"field": "_replacement_style", "value": "_candidate_style.style"}},
                ]},
            ]}},
            {"require": {
                "condition": "(_old_style_choice.source == 'fighter' and class_id == 'fighter') "
                "or _replacement_style == _old_style_choice.style",
                "error": "Only Fighter's original Fighting Style may change on a Fighter level."}},
        ]}},
        {"for_each": {"collection": "_style_records", "as": "_new_style_choice", "do": [
            {"condition": "_new_style_choice.source not in _old_style_sources", "on_true": [
                {"set": {"field": "_new_style_policy", "value":
                         "definitions.srd_fighting_style_grants[_new_style_choice.source]"}},
                {"require": {"condition": "_new_style_policy.class_id == class_id "
                             "and _new_style_policy.level == _class_level",
                             "error": "Acquire a Fighting Style at its granting class level."}},
                {"require": {"condition": "_new_style_policy.feat == false or _ability_total == 0",
                             "error": "A Fighting Style feat spends this level's feat choice; "
                             "it cannot also grant an Ability Score Improvement."}},
            ]},
        ]}},
        {"require": {
            "condition": "class_id != 'fighter' or _class_level != 1 "
            "or 'fighter' in _style_sources",
            "error": "Select the Fighting Style granted by the first Fighter level."}},
        {"require": {
            "condition": "class_id != 'fighter' or _class_level != 7 "
            "or _next_subclasses.fighter != 'champion' or 'champion' in _style_sources",
            "error": "Select Champion's additional Fighting Style at Fighter level 7."}},
        {"set": {"field": "_style_alternatives", "value": []}},
        {"for_each": {"collection": "_style_spell_grants", "as": "_style_alternative", "do": [
            {"list_push": {"list": "_style_alternatives", "value": "_style_alternative.source"}},
        ]}},
        *[{"require": {
            "condition": f"class_id != '{class_id}' or _class_level != 2 "
            f"or '{class_id}' in _style_sources or '{alternative}' in _style_alternatives",
            "error": f"Select {class_id.title()}'s Fighting Style or its cantrip alternative "
            "at class level 2.",
        }} for class_id, alternative in (("paladin", "blessed_warrior"),
                                         ("ranger", "druidic_warrior"))],
    ]



def tool():
    """Expose the same private calculation to attack consumers."""
    return {"description": "Validate recorded Fighting Style grants and derive their benefits.",
            "parameters": [{"name": "actor", "type": "string", "resolve": "participant",
                            "required": True}], "effects": validation_effects("actor.data")}
