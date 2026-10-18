"""Restore selected spell observations through their original source and scene owners."""

from copy import deepcopy

RETAINED_OBSERVATIONS = {
    "observe_srd_information_proxy": frozenset({"arcane_eye"}),
    "read_srd_transformed_appearance": frozenset({"alter_self", "polymorph"}),
    "disperse_srd_fog": frozenset({"fog_cloud", "stinking_cloud"}),
    "inspect_srd_minor_world_effects": frozenset({"thaumaturgy"}),
}
INTERNAL_OBSERVATIONS = {
    "observe_srd_information_proxy_node": "observe_srd_information_proxy",
    "inspect_srd_minor_world_effects_node": "inspect_srd_minor_world_effects",
}


def _original(runtime, name):
    """Fail on missing or previously guarded owners rather than constructing fake effects."""
    tool = deepcopy(runtime["tools"].get(name))
    if not isinstance(tool, dict) or not tool.get("effects"):
        raise ValueError("Missing original spell observation: " + name)
    if any(row.get("require", {}).get("condition") == "false" for row in tool["effects"]):
        raise ValueError("Bind observations before Lite pending guards: " + name)
    return tool


def _require(condition, error):
    return {"require": {"condition": condition, "error": error}}


def _after_binding(tool, name, effect):
    """Check the actual record immediately after its original top-level binding."""
    matches = [
        i for i, row in enumerate(tool["effects"]) if row.get("set", {}).get("field") == name
    ]
    if len(matches) != 1:
        raise ValueError("Review the changed observation source binding: " + name)
    tool["effects"].insert(matches[0] + 1, effect)


def bind_retained_observations(runtime, selected):
    """Keep native observations, expiry, senses and wind geometry; constrain saved sources."""
    selected = set(selected)
    restored = set()
    for name, owners in RETAINED_OBSERVATIONS.items():
        if not owners & selected:
            continue
        tool = _original(runtime, name)
        if name == "observe_srd_information_proxy":
            node = _original(runtime, "observe_srd_information_proxy_node")
            _after_binding(
                node,
                "_observed_proxy",
                _require(
                    "_observed_proxy.metadata.information_proxy == 'arcane_eye' and "
                    "'arcane_eye' in definitions.srd_lite_implemented",
                    "This sensor observation requires the selected Arcane Eye source.",
                ),
            )
            runtime["tools"]["observe_srd_information_proxy_node"] = node
            card = tool["metadata"]["result_card"]
            card["title"] = "Arcane Eye observation"
            for row in card["summary"]:
                row["value"]["labels"] = {"true": "Yes", "false": "No"}
        elif name == "read_srd_transformed_appearance":
            tool["effects"].insert(
                0,
                {
                    "condition": "target_state.transformation_source_ids != null",
                    "on_true": [
                        {
                            "for_each": {
                                "collection": "target_state.transformation_source_ids",
                                "as": "_lite_appearance_source_id",
                                "do": [
                                    {
                                        "set": {
                                            "field": "_lite_appearance_source",
                                            "value": "lasting_state.transformation_sources["
                                            "_lite_appearance_source_id]",
                                        }
                                    },
                                    _require(
                                        "(_lite_appearance_source.spell_id == 'alter_self' or "
                                        "_lite_appearance_source.spell_id == 'polymorph') and "
                                        "_lite_appearance_source.spell_id in "
                                        "definitions.srd_lite_implemented",
                                        "Appearance inspection supports selected Alter Self "
                                        "and Polymorph sources.",
                                    ),
                                ],
                            }
                        }
                    ],
                },
            )
            tool.setdefault("metadata", {})["result_card"] = {
                "version": 1,
                "footprint": "compact",
                "title": "Transformed appearance",
                "summary": [
                    {"label": "Appearance", "value": {"path": "context._transformed_appearance"}},
                    {
                        "label": "Observed original identity",
                        "value": {"path": "context._transformed_true_identity"},
                        "optional": True,
                    },
                ],
            }
        elif name == "disperse_srd_fog":
            _after_binding(
                tool,
                "_environment_area",
                _require(
                    "(_environment_area.spell == 'fog_cloud' or "
                    "_environment_area.spell == 'stinking_cloud') and "
                    "_environment_area.spell in definitions.srd_lite_implemented",
                    "Dispersal requires a selected Fog Cloud or Stinking Cloud source.",
                ),
            )
            card = tool["metadata"]["result_card"]
            card["summary"][0]["value"]["path"] = "parameters.area_id"
            card["summary"].append(
                {"label": "Wind zone", "value": {"path": "parameters.wind_zone_id"}}
            )
        elif name == "inspect_srd_minor_world_effects":
            node = _original(runtime, "inspect_srd_minor_world_effects_node")
            loop = next(row["for_each"] for row in node["effects"] if "for_each" in row)
            if loop.get("as") != "_minor_record" or len(loop.get("do", [])) != 1:
                raise ValueError("Review the changed minor world inspection owner")
            entry = loop["do"][0]
            if entry.get("list_push", {}).get("list") != "_minor_effects":
                raise ValueError("Review the changed minor world inspection result")
            entry["condition"] = (
                "("
                + entry["condition"]
                + ") and "
                + (
                    "_minor_record.spell == 'thaumaturgy' and "
                    "_minor_record.spell in definitions.srd_lite_implemented"
                )
            )
            runtime["tools"]["inspect_srd_minor_world_effects_node"] = node
            if tool["effects"][0].get("call") != "inspect_srd_minor_world_effects_node":
                raise ValueError("Review the changed minor world result boundary")
            tool["effects"][0]["export_context"] = {"_minor_effects": "_minor_effects"}
        runtime["tools"][name] = tool
        restored.add(name)
    return restored
