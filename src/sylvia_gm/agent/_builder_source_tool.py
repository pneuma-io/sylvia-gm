"""Advertise explicit Ruleset selection for a plain Builder chat."""

from sylvia_gm.application.builder_source_selection import BuilderSourceSelection
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName


def builder_source_tool(selection: BuilderSourceSelection) -> ToolSpec:
    """Bind discovered content without granting access to arbitrary library paths."""
    def select(params, _context):
        ruleset_id = params.get("ruleset_id")
        if set(params) != {"ruleset_id"} or not isinstance(ruleset_id, str) or not ruleset_id:
            return {"success": False, "code": "builder_source_parameters_invalid"}
        try:
            return thaw_json_value(selection.select(ruleset_id))
        except CommandRejected as error:
            return {
                "success": False, "code": "builder_source_selection_rejected", "error": str(error),
            }

    return ToolSpec.from_openai_schema(
        {"type": "function", "function": {
            "name": "select_builder_ruleset",
            "description": (
                "Select the Ruleset requested by the user before building a Campaign Setting. "
                "Use its selection_id from discover_campaign_content. This fixes an exact source "
                "snapshot for this chat; it cannot be replaced later. After selection, use "
                "inspect_builder_references, copy_builder_reference and Setting checking/saving "
                "tools in this same turn. Loading a skill does not select a Ruleset."
            ),
            "parameters": {
                "type": "object", "properties": {"ruleset_id": {"type": "string"}},
                "required": ["ruleset_id"], "additionalProperties": False,
            },
        }},
        handler=select, toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
    )


def builder_setting_source_tool(selection: BuilderSourceSelection) -> ToolSpec:
    """Select one exact Setting for Plan work without retargeting contextual Builder chats."""
    def select(params, _context):
        candidate_id = params.get("candidate_id")
        setting_id = params.get("setting_id")
        if (set(params) != {"candidate_id", "setting_id"}
            or not isinstance(setting_id, str) or not setting_id.strip()
            or (candidate_id is not None
                and (not isinstance(candidate_id, str) or not candidate_id.strip()))):
            return {"success": False, "code": "builder_source_parameters_invalid"}
        try:
            return thaw_json_value(selection.select_setting(**params))
        except CommandRejected as error:
            return {"success": False, "code": "builder_source_selection_rejected",
                    "error": str(error)}

    return ToolSpec.from_openai_schema(
        {"type": "function", "function": {
            "name": "select_builder_setting",
            "description": (
                "Select the user's exact saved Campaign Setting before Plan work. Use its "
                "id as setting_id, and candidate_id from its publication receipt or the Ruleset's "
                "discovered compatible_settings entry. "
                "For bundled content whose entry has candidate_id null, pass null; Sylvia "
                "installs and retains that exact bundled snapshot on selection. "
                "This enables Plan source inspection, drafting, checking and saving in this turn. "
                "An existing Ruleset binding must match; an existing Setting cannot be replaced."
            ),
            "parameters": {"type": "object", "properties": {
                "candidate_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "setting_id": {"type": "string"},
            }, "required": ["candidate_id", "setting_id"], "additionalProperties": False},
        }}, handler=select, toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
    )
