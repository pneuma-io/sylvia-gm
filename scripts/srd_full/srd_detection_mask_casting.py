"""Connect saved detection masks to the existing paid spell casting owner."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_spell_detection_masks import CHOICES, _schema
    from .srd_spell_trigger_composition import _effect_lists
    from .srd_timed_actor_defenses import _call, _load_runtime, _RuntimeDumper, _when
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_spell_detection_masks import CHOICES, _schema
    from srd_spell_trigger_composition import _effect_lists
    from srd_timed_actor_defenses import _call, _load_runtime, _RuntimeDumper, _when
    from srd_timed_senses import _loop, _push, _replace_tools


def extend_runtime(text):
    """Install masks after successful payment, using the actual retained casting identity."""
    tools = _load_runtime(text)["tools"]
    cast = deepcopy(tools["cast_spell"])
    prior = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CHOICES if p["name"] not in prior]
    marker = "_spell_cast_failed != true and _spell_definition.family == 'detection_mask_spell'"
    for effects in list(_effect_lists(cast["effects"])):
        effects[:] = [e for e in effects if e.get("condition") != marker]
    resolution = next(
        effects
        for effects in _effect_lists(cast["effects"])
        if any(
            "_spell_definition.family == 'information_spell'" in e.get("condition", "")
            for e in effects
        )
    )
    params = {p["name"]: "{" + p["name"] + "}" for p in CHOICES}
    params.update(
        caster_state="{_caster_state_id}",
        spell="{spell}",
        slot_level="{_slot_level}",
        save_dc="{_casting_profile.save_dc}",
        concentration_modifier="{_casting_profile.concentration_save_modifier}",
        spell_cast_id="{_spell_source_cast_id}",
        spell_cast_owner="{_caster_state_id}",
    )
    call = _call("apply_srd_detection_mask", params)
    call["export_context"] = {
        "_mask_summary": "_spell_summary_rows",
        "_mask_details": "_spell_detail_rows",
    }
    resolution.append(
        _when(
            marker,
            [
                # Until-dispelled Sequester also owns one real committed cast identity.
                _when(
                    "_spell_source_cast_id == null or _spell_source_cast_id == ''",
                    [
                        _call(
                            "begin_srd_spell_source_instance",
                            {
                                "caster_state": "{_caster_state_id}",
                                "spell": "{spell}",
                                "slot_level": "{_slot_level}",
                                "lasting_state": "state",
                            },
                            ["_spell_source_cast_id"],
                        ),
                    ],
                ),
                call,
                _loop(
                    "_mask_summary",
                    "_mask_card_row",
                    [_push("_spell_summary_rows", "_mask_card_row")],
                ),
                _loop(
                    "_mask_details",
                    "_mask_card_row",
                    [_push("_spell_detail_rows", "_mask_card_row")],
                ),
            ],
        )
    )
    text = _replace_tools(text, {"cast_spell": cast})
    rendered = yaml.dump(
        {"detection_mask_spell": _schema()}, Dumper=_RuntimeDumper, sort_keys=False
    )
    rendered = "".join("      " + line for line in rendered.splitlines(True))
    pattern = r"(?ms)^      detection_mask_spell:\n.*?(?=^      [A-Za-z_]|^\S|\Z)"
    if re.search(pattern, text):
        text = re.sub(pattern, lambda _: rendered, text, count=1)
    else:
        anchor = "      timed_senses:"
        if text.count(anchor) != 1:
            raise SystemExit("Detection masks need the existing spell definition variant boundary.")
        text = text.replace(anchor, rendered + "\n" + anchor, 1)
    return text
