"""Paid Create Undead selects the genuine Wight body and its source-owned later actions."""

from copy import deepcopy

if __package__:
    from .srd_native_wight_actions import _validate_source
    from .srd_spell_summoning import _choose, _walk
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_native_wight_actions import _validate_source
    from srd_spell_summoning import _choose, _walk
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools

PUBLIC_TOOLS = {"use_srd_native_wight_life_drain", "check_srd_native_wight_ability"}


def extend_runtime(text):
    """Open only Wight's authenticated constructor through the actual paid cast dispatcher."""
    _validate_source()
    tools = _load_runtime(text)["tools"]
    cast = deepcopy(tools["cast_spell_created_undead_creature"])
    changed = {}
    body_choices = next(p for p in cast["parameters"] if p["name"] == "created_undead_bodies")
    language_choice = body_choices["items"]["properties"]["other_languages"]
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        public = deepcopy(tools[name])
        choice = next(p for p in public["parameters"] if p["name"] == "created_undead_bodies")
        properties = choice["items"]["properties"]
        if properties.get("other_languages") != language_choice:
            if "other_languages" in properties:
                raise ValueError("Preserve the actual source-language choice schema.")
            properties["other_languages"] = deepcopy(language_choice)
            changed[name] = public
    if "create_srd_created_wight_body" in str(cast["effects"]):
        return _replace_tools(text, changed) if changed else text
    body = deepcopy(tools["create_srd_created_ghast_body"])
    prepares = [e for e in _walk(body["effects"]) if e.get("call") == "prepare_srd_source_ghast"]
    if len(prepares) != 1 or "other_languages" not in prepares[0]["params"]:
        raise ValueError("Preserve the actual corpse/source-language creation boundary.")
    prepares[0]["call"] = "prepare_srd_source_wight"
    body["description"] = "Create the actual source Wight at its consumed eligible corpse's space."
    guards = [
        e["require"]
        for e in _walk(cast["effects"])
        if e.get("require", {}).get("condition")
        == "created_undead_form == 'ghoul' or created_undead_form == 'ghast'"
    ]
    if len(guards) != 1:
        raise ValueError("Open only the exact existing closed Undead form selector.")
    guards[0]["condition"] += " or created_undead_form == 'wight'"
    guards[0]["error"] = "Join Mummy's full native automatic/routine receivers before creation."
    dispatch = [
        e
        for e in _walk(cast["effects"])
        if e.get("condition") == "created_undead_form == 'ghoul'"
        and e.get("on_true", [{}])[0].get("call") == "create_srd_created_ghoul_body"
    ]
    if len(dispatch) != 1 or len(dispatch[0]["on_false"]) != 1:
        raise ValueError("Preserve actual Ghoul and Ghast paid body dispatch.")
    original = dispatch[0]["on_false"][0]
    if original.get("call") != "create_srd_created_ghast_body":
        raise ValueError("Preserve the actual existing alternative body creation call.")
    chosen = deepcopy(original)
    chosen["call"] = "create_srd_created_wight_body"
    dispatch[0]["on_false"] = [_choose("created_undead_form == 'wight'", [chosen], [original])]
    cast["description"] = (
        "Create source Ghouls, Ghasts or Wights at night, or renew their actual saved control."
    )
    changed.update(create_srd_created_wight_body=body, cast_spell_created_undead_creature=cast)
    for name in PUBLIC_TOOLS:
        tool = deepcopy(tools[name])
        tool["metadata"].update(
            category="combat",
            visible_to_player=True,
            costs_action=name == "use_srd_native_wight_life_drain",
            activity_label="Life Drain" if name.startswith("use_") else "Wight ability check",
        )
        changed[name] = tool
    return _replace_tools(text, changed)
