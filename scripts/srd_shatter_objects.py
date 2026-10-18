"""GM-selected Shatter objects receive the native shared Thunder roll without a save."""

from copy import deepcopy

if __package__:
    from .srd_spell_damage_consumers import _effect_lists as _lists
    from .srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_spell_damage_consumers import _effect_lists as _lists
    from srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _push, _replace_tools

AREA = "cast_spell_instantaneous_area_save_damage"
OBJECTS = "shatter_object_targets"
RECEIVE = "apply_srd_area_object_damage"


def _intent():
    return _parameter(
        OBJECTS,
        "array",
        required=False,
        default=[],
        items={"type": "string"},
        max_items=64,
        unique_items=True,
        description="Actual objects the GM identifies as lying in Shatter's ten-foot Sphere.",
    )


def bind_runtime_consumers(text):
    """Join the genuine shared area roll to object HP before saved cast captures."""
    tools = _load_runtime(text)["tools"]
    if RECEIVE not in tools:
        raise ValueError("Shatter needs the genuine prepared-object area damage owner.")
    changed = {}
    area = deepcopy(tools[AREA])
    next(p for p in area["parameters"] if p["name"] == "targets")["min_items"] = 0
    if OBJECTS not in {p["name"] for p in area["parameters"]}:
        area["parameters"].append(_intent())
    if "_shatter_object_damage" not in str(area["effects"]):
        area["effects"].insert(
            0,
            _require(
                f"spell == 'shatter' or len({OBJECTS}) == 0",
                "Only Shatter uses this unattended-object recipient intent.",
            ),
        )
        area["effects"].append(
            _when(
                f"spell == 'shatter' and len({OBJECTS}) > 0",
                [
                    _when(
                        "len(targets) == 0",
                        [
                            _set("_spell_area_last_save", None),
                            _set("_spell_area_last_save_outcome", "''"),
                            _set("_spell_area_last_save_advantage", False),
                            _set("_spell_area_last_save_disadvantage", False),
                            _set("_spell_area_last_damage_amount", 0),
                        ],
                    ),
                    _set("_shatter_object_damage", "max(0, _spell_damage_total)"),
                    _loop(
                        OBJECTS,
                        "_shatter_object_id",
                        [
                            _call(
                                RECEIVE,
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "object_id": "{_shatter_object_id}",
                                    "component_1_amount": "{_shatter_object_damage}",
                                    "component_1_damage_type": "thunder",
                                },
                                ["_area_object_eligible", "_object_damage_applied"],
                            ),
                            _when(
                                "_area_object_eligible == true",
                                [
                                    _push(
                                        "_spell_detail_rows",
                                        {
                                            "label": "_shatter_object_id",
                                            "value": "_object_damage_applied",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        )
    changed[AREA] = area
    owners = {AREA}
    progress = True
    while progress:
        progress = False
        for name, tool in tools.items():
            if name not in owners and any(
                e.get("call") in owners for rows in _lists(tool.get("effects", [])) for e in rows
            ):
                owners.add(name)
                progress = True
    for name in owners - {AREA}:
        tool = deepcopy(tools[name])
        for parameter in tool["parameters"]:
            if parameter["name"] == "targets":
                parameter["min_items"] = 0
        if OBJECTS not in {p["name"] for p in tool["parameters"]}:
            tool["parameters"].append(_intent())
        for rows in _lists(tool["effects"]):
            for effect in rows:
                if effect.get("call") in owners:
                    effect.setdefault("params", {})[OBJECTS] = "{" + OBJECTS + "}"
        changed[name] = tool
    return _replace_tools(text, changed)
