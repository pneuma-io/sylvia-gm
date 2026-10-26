"""Paid Create Undead selects the printed Mummy with its actual native action receivers."""

from copy import deepcopy

if __package__:
    from .srd_native_undead_curses import _validate_sources
    from .srd_spell_summoning import _choose, _walk
    from .srd_timed_actor_defenses import _call, _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_native_undead_curses import _validate_sources
    from srd_spell_summoning import _choose, _walk
    from srd_timed_actor_defenses import _call, _load_runtime
    from srd_timed_senses import _replace_tools

PUBLIC_TOOLS = {"use_srd_native_mummy_glare"}


def extend_runtime(text):
    """Open only the actual Mummy selector after its real mixed routine and receivers exist."""
    _validate_sources()
    tools = _load_runtime(text)["tools"]
    required = {
        "spend_srd_native_mummy_action_component",
        "apply_srd_native_mummy_fist_packet",
        "apply_srd_native_mummy_fist_damage",
        "resolve_srd_native_mummy_glare",
        "resolve_srd_native_mummy_maximum_zero",
        "require_srd_pending_window_closed",
    }
    if not required <= tools.keys():
        raise ValueError(
            "Keep actual Mummy routine, typed Fist, curse and death receivers installed."
        )
    cast = deepcopy(tools["cast_spell_created_undead_creature"])
    if "create_srd_created_mummy_body" in str(cast["effects"]):
        return text
    body = deepcopy(tools["create_srd_created_ghast_body"])
    prepares = [e for e in _walk(body["effects"]) if e.get("call") == "prepare_srd_source_ghast"]
    if len(prepares) != 1 or "other_languages" not in prepares[0]["params"]:
        raise ValueError("Preserve the actual eligible-corpse and source-language creator.")
    prepares[0]["call"] = "prepare_srd_source_mummy"
    body["description"] = "Create the actual printed Mummy at its consumed eligible corpse's space."
    guards = [
        e["require"]
        for e in _walk(cast["effects"])
        if e.get("require", {}).get("condition")
        == "created_undead_form == 'ghoul' or created_undead_form == 'ghast' "
        "or created_undead_form == 'wight'"
    ]
    if len(guards) != 1:
        raise ValueError("Open only the exact paid Undead selector with Wight already joined.")
    guards[0]["condition"] += " or created_undead_form == 'mummy'"
    guards[0]["error"] = "Select only the actual printed Ghouls, Ghasts, Wights or Mummies."
    branches = [
        e
        for e in _walk(cast["effects"])
        if e.get("condition") == "created_undead_form == 'wight'"
        and e.get("on_true", [{}])[0].get("call") == "create_srd_created_wight_body"
    ]
    if len(branches) != 1 or len(branches[0].get("on_false", [])) != 1:
        raise ValueError("Preserve the exact existing Wight and Ghast alternative dispatch.")
    original = branches[0]["on_false"][0]
    if original.get("call") != "create_srd_created_ghast_body":
        raise ValueError("Keep the actual existing Ghast creation path unchanged.")
    chosen = deepcopy(original)
    chosen["call"] = "create_srd_created_mummy_body"
    branches[0]["on_false"] = [_choose("created_undead_form == 'mummy'", [chosen], [original])]
    cast["description"] = (
        "Create printed Ghouls, Ghasts, Wights or Mummies at night, "
        "or renew their actual saved control."
    )
    glare = deepcopy(tools["use_srd_native_mummy_glare"])
    glare["effects"].insert(0, _call("require_srd_pending_window_closed", {}))
    glare["metadata"].update(
        category="combat",
        visible_to_player=True,
        costs_action=True,
        activity_label="Dreadful Glare",
    )
    glare["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "compact",
        "title": "Dreadful Glare",
        "subtitle": "Wisdom saving throw",
        "summary": [{"label": "Save", "value": {"path": "context._mummy_glare_outcome"}}],
        "details": [
            {
                "label": "Wisdom dice",
                "value": {"path": "context._mummy_glare_save_roll.results"},
                "optional": True,
            },
            {
                "label": "Wisdom total",
                "value": {"path": "context._mummy_glare_save_total"},
                "optional": True,
            },
            {"label": "DC", "value": 11},
            {"label": "Failure duration", "value": "Frightened until this Mummy's next turn ends."},
        ],
    }
    return _replace_tools(
        text,
        {
            "create_srd_created_mummy_body": body,
            "cast_spell_created_undead_creature": cast,
            "use_srd_native_mummy_glare": glare,
        },
    )
