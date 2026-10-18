"""Hardcoded tool definitions for the Builder agent.

The Builder gets ordinary project-file tools:
- read_file: Read file contents with line numbering
- write_file: Write content atomically, creating parent directories
- edit_file: Replace one unique literal in an existing file
- search_files: Find files by glob or search content by regex
- delete_file: Delete a file within the project directory

Public web search is a separate per-turn capability.  It is never part of the
default Builder toolset because closed or restricted sources require the user
to explicitly direct online research.
"""

from __future__ import annotations

from typing import Any

# Tool definitions in OpenAI function-calling JSON schema format.
# These are constant — unlike the DM which derives tools from rulesets.

BUILDER_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a workspace file and its version. Use that version as expected_version "
                "when replacing or deleting it. A partial read still identifies the whole file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the file. Relative paths are resolved against the "
                            "project root."
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-indexed, default: 1).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read (default: 500, max: 2000).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file within the Sylvia GM project directory. "
                "Creates parent directories as needed. To replace an existing file, first "
                "read it and pass the returned version as expected_version. A stale save is "
                "rejected: reread and reconcile compatible edits; ask the user about conflicting "
                "intent. Omit expected_version only when creating a new file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to write to. Relative paths are resolved against the "
                            "project root."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Content to write to the file. Required — must not be null or empty."
                        ),
                    },
                    "expected_version": {
                        "type": "string",
                        "description": "Version from read_file or successful write_file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Make one small, exact correction to an existing workspace file without "
                "rewriting unrelated content. Read the file first, then provide its version and "
                "one non-empty old_text literal that occurs exactly once. The edit is rejected "
                "if the file changed or the literal has zero or multiple matches. Make edits "
                "to the same file one at a time, using each successful edit's returned version "
                "for the next edit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the existing file within the project root.",
                    },
                    "old_text": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Exact non-empty text to replace; it must occur once.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Exact replacement text. It may be empty.",
                    },
                    "expected_version": {
                        "type": "string",
                        "description": "Version from read_file or a successful file mutation.",
                    },
                },
                "required": ["path", "old_text", "new_text", "expected_version"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Search for files by glob pattern, or search file contents by regex, "
                "within the Sylvia GM project directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Glob pattern for file search (e.g., '*.yaml') or regex for "
                            "content search."
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Subdirectory to search, or a file for content search, "
                            "relative to project root. "
                            "Default: project root."
                        ),
                    },
                    "target": {
                        "type": "string",
                        "enum": ["files", "content"],
                        "description": (
                            "'files' to find files by name, 'content' to grep file contents."
                        ),
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": (
                "Delete a file within the Sylvia GM project directory. Read it first and pass "
                "its version; deletion is rejected if another chat has changed it. "
                "This operation cannot be undone. Directories cannot be deleted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the file to delete. Relative paths are resolved "
                            "against the project root."
                        ),
                    },
                    "expected_version": {
                        "type": "string",
                        "description": "Version from read_file or a successful write_file.",
                    },
                },
                "required": ["path", "expected_version"],
            },
        },
    },
]


PUBLIC_WEB_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the public web for current sources. Returns bounded titles, URLs, "
            "and descriptions for Builder research."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Specific web search query.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum result count from 1 to 10.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

# ── Interview widget tool (separate definition for toolset gating) ──
INTERVIEW_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "ask_question",
            "description": (
                "Ask the user a structured question in this Builder conversation. "
                "Use this INSTEAD of outputting [WIDGET:...] text. "
                "The UI will render an interactive widget card. "
                "Returns immediately with 'awaiting_answer' status. "
                "The player's answers arrive as an ordinary saved user reply."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["select", "text", "confirm", "cascade", "complete"],
                        "description": (
                            "Widget type: 'select' (single-choice dropdown), 'text' "
                            "(free-text input), 'confirm' (multi-select checkboxes), "
                            "'cascade' (option list with action button for cascading "
                            "choices like ruleset/setting/plan), 'complete' (campaign "
                            "ready summary)."
                        ),
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "The question to display. Required for select/text/confirm. "
                            "Not used for 'complete' type."
                        ),
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of options. Required for select/confirm types.",
                    },
                    "id": {
                        "type": "string",
                        "description": (
                            "Unique identifier for this question (e.g., 'genre', "
                            "'tone'). Used to match answers."
                        ),
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "Campaign summary displayed on the completion card. "
                            "Required for 'complete' type."
                        ),
                    },
                    "campaign_id": {
                        "type": "string",
                        "description": (
                            "The campaign ID for the completion flow. Required for 'complete' type."
                        ),
                    },
                },
                "required": ["type", "id"],
            },
        },
    },
]


# Dynamically injected only into an authoritative BUILD RULESET session. Keeping
# it separate prevents campaign-setting/plan Builders and legacy runners from
# acquiring package-publication authority through the ordinary static tool set.
RULESET_CANDIDATE_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "publish_ruleset_candidate",
        "description": (
            "Validate ruleset-draft/package.json and its dedicated files subtree, "
            "publish a non-active Ruleset package candidate for human review, and "
            "return the workspace-relative installable artifact path."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}


CAMPAIGN_SOURCE_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "inspect_campaign_sources",
        "description": (
            "List or read the selected Ruleset and Campaign Setting source material before "
            "drafting a Campaign Plan. Paths are package-relative and never machine paths. "
            "Use the returned setting/... paths directly with create_campaign_plan_draft."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "read"]},
                "path": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
}


CREATE_CAMPAIGN_PLAN_DRAFT_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "create_campaign_plan_draft",
        "description": (
            "Create a valid structured Campaign Plan draft from a concise campaign concept and "
            "a starting-party policy plus exact Setting paths returned by "
            "inspect_campaign_sources. Both setting/... and package-relative path forms are "
            "accepted. Supply observable completion conditions separately from the objective; "
            "finish the scaffold's remaining preparation before publishing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "premise": {"type": "string"},
                "tone": {"type": "string"},
                "player_character_mode": {
                    "type": "string",
                    "enum": ["compatible", "fixed"],
                },
                "player_character_path": {
                    "type": "string",
                    "description": (
                        "Required only for fixed mode: the exact character source path."
                    ),
                },
                "required_companion_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exact Setting paths for companions the campaign requires.",
                },
                "custom_companion_minimum": {"type": "integer", "minimum": 0},
                "custom_companion_maximum": {"type": "integer", "minimum": 0},
                "starting_location_path": {
                    "type": "string",
                    "description": (
                        "A location source path returned by inspect_campaign_sources, such as "
                        "setting/locations/cairnfields.md."
                    ),
                },
                "opening_situation": {"type": "string"},
                "opening_direction": {
                    "type": "string",
                    "description": (
                        "Optional creative direction for only the campaign's opening exchange."
                    ),
                },
                "primary_objective": {"type": "string"},
                "completion_conditions": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                    "description": (
                        "Required discoveries/events and the exact closing event of this "
                        "draft's main objective. All entries must hold; state any alternative "
                        "within the condition it satisfies. Define concrete failure endings "
                        "to the same problem. Accepting work or completing a side job cannot "
                        "substitute. These are authored conditions, not proof of story quality."
                    ),
                },
            },
            "required": [
                "title",
                "premise",
                "tone",
                "player_character_mode",
                "custom_companion_minimum",
                "custom_companion_maximum",
                "starting_location_path",
                "opening_situation",
                "primary_objective",
                "completion_conditions",
            ],
            "additionalProperties": False,
        },
    },
}


CAMPAIGN_PLAN_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "publish_campaign_plan",
        "description": (
            "Validate campaign-plan-draft/plan.json plus plan.md against the exact admitted "
            "Ruleset and Campaign Setting, then publish a selectable Campaign Plan artifact."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}


DISCOVER_CAMPAIGN_CONTENT_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "discover_campaign_content",
        "description": (
            "Search the Rulesets, Campaign Settings, Campaign Plans, and saved Player Characters "
            "and Companions, plus general character profiles requiring adaptation, available "
            "in this Sylvia GM installation. Use this before proposing content for a new campaign."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional name, theme, or artifact identity to search for.",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}


PREPARE_CAMPAIGN_SETUP_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "prepare_campaign_setup",
        "description": (
            "Prepare the app's deterministic New Campaign setup for a Campaign Plan returned by "
            "discover_campaign_content. This does not create, activate, or play the campaign."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_plan_id": {
                    "type": "string",
                    "description": "Exact Campaign Plan identity returned by discovery.",
                }
            },
            "required": ["campaign_plan_id"],
            "additionalProperties": False,
        },
    },
}
